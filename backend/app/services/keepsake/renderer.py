from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from app.services.asr.audio import get_ffmpeg_binary, normalize_audio


FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    font_path = next((path for path in FONT_CANDIDATES if path.is_file()), None)
    if font_path is None:
        raise RuntimeError("KEEPSAKE_CJK_FONT_MISSING")
    return ImageFont.truetype(str(font_path), size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for character in text.replace("\n", " "):
        candidate = current + character
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = character
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and sum(len(line) for line in lines) < len(text.replace("\n", " ")):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def render_story_card(
    output_path: Path,
    *,
    width: int,
    height: int,
    keepsake_title: str,
    story_title: str,
    story_excerpt: str,
    life_stage: str,
    image_path: Path | None,
) -> None:
    if image_path:
        with Image.open(image_path) as source:
            canvas = ImageOps.fit(source.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
        canvas = ImageEnhance.Brightness(canvas).enhance(0.55)
    else:
        canvas = Image.new("RGB", (width, height), "#eee5d4")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle((0, 0, width, height), fill=(30, 25, 20, 55 if image_path else 0))
    draw.rectangle((0, height - 94, width, height), fill=(38, 32, 27, 215))
    title_font = _font(max(34, width // 22))
    body_font = _font(max(22, width // 42))
    small_font = _font(max(17, width // 58))
    margin = max(54, width // 15)
    text_color = "#fffaf0" if image_path else "#29271f"
    muted_color = "#f3dec0" if image_path else "#74312b"
    draw.text((margin, 56), keepsake_title, font=small_font, fill=muted_color)
    draw.text((margin, 105), life_stage, font=small_font, fill=muted_color)
    story_lines = _wrap(draw, story_title, title_font, width - margin * 2, 2)
    y = 155
    for line in story_lines:
        draw.text((margin, y), line, font=title_font, fill=text_color)
        y += int(title_font.size * 1.35)
    y += 24
    for line in _wrap(draw, story_excerpt, body_font, width - margin * 2, 4):
        draw.text((margin, y), line, font=body_font, fill=text_color)
        y += int(body_font.size * 1.55)
    draw.text(
        (margin, height - 65),
        "聆年 · 家庭记忆整理 · 原始录音 · 非实时影像",
        font=small_font,
        fill="#fffaf0",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    output_path.chmod(0o600)


def _run(command: list[str], *, timeout: int) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError("KEEPSAKE_RENDER_FAILED")


def render_keepsake_video(
    *,
    work_dir: Path,
    output_path: Path,
    keepsake_title: str,
    clips: list[dict],
    width: int,
    height: int,
    max_source_seconds: int,
    timeout_seconds: int,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    ffmpeg = get_ffmpeg_binary()
    work_dir.mkdir(parents=True, exist_ok=True)
    rendered_clips: list[Path] = []
    total_seconds = 0.0
    for index, clip in enumerate(clips):
        normalized = work_dir / f"audio-{index:02d}.wav"
        normalize_audio(clip["audio_path"], normalized)
        with wave.open(str(normalized), "rb") as audio:
            duration = audio.getnframes() / max(audio.getframerate(), 1)
        total_seconds += duration
        if total_seconds > max_source_seconds:
            raise RuntimeError("KEEPSAKE_DURATION_LIMIT")
        card = work_dir / f"card-{index:02d}.png"
        render_story_card(
            card,
            width=width,
            height=height,
            keepsake_title=keepsake_title,
            story_title=clip["story_title"],
            story_excerpt=clip["story_excerpt"],
            life_stage=clip["life_stage"],
            image_path=clip.get("image_path"),
        )
        rendered = work_dir / f"clip-{index:02d}.mp4"
        _run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-loop",
                "1",
                "-framerate",
                "25",
                "-i",
                str(card),
                "-i",
                str(normalized),
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "stillimage",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-shortest",
                str(rendered),
            ],
            timeout=timeout_seconds,
        )
        rendered_clips.append(rendered)
        if on_progress:
            on_progress(25 + round(55 * (index + 1) / len(clips)))

    concat_file = work_dir / "clips.txt"
    concat_file.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in rendered_clips),
        encoding="utf-8",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-metadata",
            f"title={keepsake_title}",
            "-metadata",
            "comment=家庭记忆整理 · 原始录音 · 非实时影像",
            str(output_path),
        ],
        timeout=timeout_seconds,
    )
    output_path.chmod(0o600)
    if on_progress:
        on_progress(90)
    return round(total_seconds * 1000)
