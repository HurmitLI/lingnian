from __future__ import annotations

import shutil

import imageio_ffmpeg
import pytest
from PIL import Image

from lingnian_worker.media import MediaRenderer


def test_renders_and_assembles_a_real_short_mp4(tmp_path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe is not installed")
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 180), (62, 45, 32)).save(source)
    renderer = MediaRenderer(ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(), ffprobe=ffprobe)
    clip = renderer.image_clip(
        source,
        tmp_path / "scene.mp4",
        subtitle="一次家庭回忆",
        duration=1,
        width=320,
        height=180,
        fps=8,
        motion="slow_push",
    )
    result = renderer.assemble([clip], tmp_path / "result.mp4", audio=None, duration=1)
    metadata = renderer.probe(result)
    assert result.stat().st_size > 1000
    assert metadata["width"] == 320
    assert metadata["height"] == 180
    assert 0.8 <= metadata["duration"] <= 1.2
