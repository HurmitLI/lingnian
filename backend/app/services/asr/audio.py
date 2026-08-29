from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.core.config import get_settings


def get_ffmpeg_binary() -> str:
    settings = get_settings()
    if settings.ffmpeg_binary:
        return settings.ffmpeg_binary
    system_binary = shutil.which("ffmpeg")
    if system_binary:
        return system_binary
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("找不到可用的 FFmpeg 音频转换工具。") from exc


def normalize_audio(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        get_ffmpeg_binary(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError("音频格式转换失败，请确认文件可以正常播放。")

