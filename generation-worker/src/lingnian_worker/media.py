from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import AudioDurationMismatch, PackageError


def _run(command: list[str], *, description: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise PackageError(f"{description}失败。")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap_text(value: str, width: int) -> str:
    max_chars = max(10, width // 44)
    return "\n".join(textwrap.wrap(value.strip(), width=max_chars, break_long_words=True))


def _concat_entry(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def make_card(path: Path, *, text: str, width: int, height: int, source_card: bool = False) -> Path:
    background = (28, 24, 22) if source_card else (42, 31, 23)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    title_font = _font(max(28, min(width, height) // 16))
    small_font = _font(max(18, min(width, height) // 34))
    wrapped = _wrap_text(text, width)
    box = draw.multiline_textbbox((0, 0), wrapped, font=title_font, spacing=14, align="center")
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    draw.multiline_text(
        ((width - text_width) / 2, (height - text_height) / 2),
        wrapped,
        font=title_font,
        fill=(247, 239, 226),
        spacing=14,
        align="center",
    )
    draw.text((width * 0.08, height * 0.88), "聆年 · 家庭记忆", font=small_font, fill=(196, 164, 123))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def burn_subtitle(source: Path, target: Path, *, subtitle: str) -> Path:
    with Image.open(source) as image:
        canvas = image.convert("RGBA" if image.mode == "RGBA" else "RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(max(22, min(canvas.width, canvas.height) // 28))
    wrapped = _wrap_text(subtitle, canvas.width)
    box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10, align="center")
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    x = (canvas.width - text_width) / 2
    y = canvas.height - text_height - canvas.height * 0.09
    padding = max(16, canvas.width // 45)
    draw.rounded_rectangle(
        (x - padding, y - padding / 2, x + text_width + padding, y + text_height + padding / 2),
        radius=14,
        fill=(10, 8, 7, 165),
    )
    draw.multiline_text((x, y), wrapped, font=font, fill=(255, 252, 247, 255), spacing=10, align="center")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, format="PNG")
    return target


class MediaRenderer:
    def __init__(self, *, ffmpeg: str, ffprobe: str) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def probe(self, path: Path) -> dict:
        completed = subprocess.run(
            [self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise PackageError("成片信息读取失败。")
        try:
            value = json.loads(completed.stdout)
            video = next((item for item in value.get("streams", []) if item.get("width")), {})
            return {
                "duration": float((value.get("format") or {}).get("duration", 0)),
                "width": int(video.get("width", 0)),
                "height": int(video.get("height", 0)),
            }
        except (ValueError, TypeError) as exc:
            raise PackageError("成片信息格式不正确。") from exc

    def image_clip(
        self,
        image: Path,
        target: Path,
        *,
        subtitle: str,
        duration: int,
        width: int,
        height: int,
        fps: int,
        motion: str,
    ) -> Path:
        prepared = target.with_suffix(".frame.png")
        with Image.open(image) as source:
            rgb = source.convert("RGB")
            ratio = max(width / rgb.width, height / rgb.height)
            resized = rgb.resize((round(rgb.width * ratio), round(rgb.height * ratio)), Image.Resampling.LANCZOS)
            left = max(0, (resized.width - width) // 2)
            top = max(0, (resized.height - height) // 2)
            resized.crop((left, top, left + width, top + height)).save(prepared, format="PNG")
        if subtitle:
            burn_subtitle(prepared, prepared, subtitle=subtitle)
        frame_count = max(1, duration * fps)
        zoom = "1" if motion == "none" else "min(zoom+0.00035,1.035)"
        _run(
            [
                self.ffmpeg, "-y", "-loop", "1", "-i", str(prepared),
                "-vf", f"zoompan=z='{zoom}':d={frame_count}:s={width}x{height}:fps={fps},format=yuv420p",
                "-t", str(duration), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(target),
            ],
            description="静态镜头制作",
        )
        return target

    def video_clip(
        self,
        source: Path,
        target: Path,
        *,
        subtitle: str,
        duration: int,
        width: int,
        height: int,
        fps: int,
    ) -> Path:
        subtitle_image = target.with_suffix(".subtitle.png")
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(subtitle_image)
        if subtitle:
            burn_subtitle(subtitle_image, subtitle_image, subtitle=subtitle)
        filter_graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"fps={fps},trim=duration={duration},setpts=PTS-STARTPTS[base];"
            f"[1:v]format=rgba[overlay];[base][overlay]overlay=0:0:format=auto,format=yuv420p[v]"
        )
        _run(
            [
                self.ffmpeg, "-y", "-stream_loop", "-1", "-i", str(source), "-loop", "1", "-i", str(subtitle_image),
                "-filter_complex", filter_graph, "-map", "[v]", "-t", str(duration), "-an",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(target),
            ],
            description="动态镜头整理",
        )
        return target

    def assemble(self, clips: list[Path], target: Path, *, audio: Path | None, duration: int) -> Path:
        if not clips:
            raise PackageError("没有可合成的镜头。")
        concat_file = target.with_suffix(".concat.txt")
        concat_file.write_text(
            "".join(_concat_entry(path) for path in clips),
            encoding="utf-8",
        )
        silent = target.with_suffix(".silent.mp4")
        _run(
            [self.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent)],
            description="镜头拼接",
        )
        if audio:
            audio_duration = self.probe(audio)["duration"]
            if audio_duration > duration + 1.0:
                raise AudioDurationMismatch("原始录音长于目标影片，不能在句子中间强行截断；请选择更长时长。")
            _run(
                [
                    self.ffmpeg, "-y", "-i", str(silent), "-i", str(audio),
                    "-filter_complex", "[1:a]apad[a]", "-map", "0:v:0", "-map", "[a]",
                    "-t", str(duration), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target),
                ],
                description="原声合成",
            )
        else:
            _run(
                [
                    self.ffmpeg, "-y", "-i", str(silent), "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-t", str(duration), "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(target),
                ],
                description="静音轨合成",
            )
        return target
