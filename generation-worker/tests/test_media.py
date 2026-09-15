from __future__ import annotations

import shutil
import json
from types import SimpleNamespace

import imageio_ffmpeg
import pytest
from PIL import Image

from lingnian_worker.api import MAX_RESULT_UPLOAD_BYTES
from lingnian_worker.media import MAX_RESULT_BYTES, MediaRenderer


def test_visual_fingerprint_ignores_container_and_distinguishes_scenes(tmp_path):
    renderer = MediaRenderer(ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(), ffprobe="ffprobe")
    fingerprints = []
    for name, color in (("warm", (120, 66, 32)), ("cool", (26, 74, 128))):
        source = tmp_path / f"{name}.png"
        Image.new("RGB", (320, 180), color).save(source)
        clip = renderer.image_clip(
            source,
            tmp_path / f"{name}.mp4",
            subtitle="",
            duration=3,
            width=320,
            height=180,
            fps=8,
            motion="slow_push",
        )
        fingerprints.append(renderer.visual_fingerprint(clip))
    assert fingerprints[0] != fingerprints[1]
    assert fingerprints[0] == renderer.visual_fingerprint(tmp_path / "warm.mp4")


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
    assert result.stat().st_size <= MAX_RESULT_BYTES
    assert metadata["width"] == 320
    assert metadata["height"] == 180
    assert 0.8 <= metadata["duration"] <= 1.2


def test_result_ceiling_leaves_one_mib_for_multipart_headroom():
    assert MAX_RESULT_BYTES == 15 * 1024 * 1024
    assert MAX_RESULT_UPLOAD_BYTES == MAX_RESULT_BYTES


@pytest.mark.parametrize("streams,expected", [
    ([{"codec_type": "audio", "duration": "3.5"}], 3.5),
    ([{"codec_type": "audio"}], 64.0),
    ([{"codec_type": "video", "width": 1280, "height": 720}, {"codec_type": "audio", "duration": "2"}], 2.0),
    ([{"codec_type": "video", "width": 1280, "height": 720}, {"codec_type": "audio"}], None),
])
def test_probe_does_not_confuse_container_length_with_short_audio(monkeypatch, tmp_path, streams, expected):
    monkeypatch.setattr("lingnian_worker.media.subprocess.run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({"format": {"duration": "64"}, "streams": streams})))
    result = MediaRenderer(ffmpeg="ffmpeg", ffprobe="ffprobe").probe(tmp_path / "input")
    assert result["has_audio"] is True
    assert result["audio_duration"] == expected
