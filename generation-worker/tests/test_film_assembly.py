import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import imageio_ffmpeg
import pytest

from test_film_contract import film
from test_film_executor import runtime, review
from lingnian_worker import film_assembly as module
from lingnian_worker.film_assembly import assemble_film, burn_timed_captions, require_audible
from lingnian_worker.models import PackageError


@pytest.fixture
def ready(runtime, monkeypatch):
    executor, args = runtime
    for _ in range(16):
        review(executor.execute(**args))
    result = executor.execute(**args)
    renderer = executor.renderer
    renderer.final_bad = False
    renderer.short_clip = False
    renderer.assemble_calls = 0

    def probe(path):
        if path == args["narration"]:
            return {"has_audio": True, "audio_duration": 63.5, "duration": 63.5}
        return {"has_audio": path.name == "film.mp4" and not renderer.final_bad,
                "audio_duration": 64 if path.name == "film.mp4" else None,
                "duration": 64 if path.name == "film.mp4" else (1 if renderer.short_clip else 4), "width": 1280, "height": 720}

    def assemble(clips, target, *, audio, duration):
        renderer.assemble_calls += 1
        assert audio == args["narration"] and duration == 64
        target.write_bytes(b"".join(path.read_bytes() for path in clips))
        return target

    def burn(renderer, source, target, **kwargs):
        target.write_bytes(source.read_bytes())
        return target

    renderer.ffmpeg = "ffmpeg"
    renderer.probe, renderer.assemble = probe, assemble
    monkeypatch.setattr(module, "burn_timed_captions", burn)
    monkeypatch.setattr(module, "require_audible", lambda *args: None)
    monkeypatch.setattr(module, "_run", lambda *args, **kwargs: None)
    return dict(**args, job_dir=Path(result["job_dir"]), renderer=renderer)


def test_assembles_once_without_claiming_final_visual_pass(ready):
    target = assemble_film(**ready)
    result = json.loads((target.parent / "assembly-report.json").read_text())
    assert result["status"] == "awaiting_final_review"
    assert result["final_visual_accepted"] is False
    assert result["captions"] == "burned_in"
    assert result["looping"] is False
    assert assemble_film(**ready) == target
    assert ready["renderer"].assemble_calls == 1


@pytest.mark.parametrize("defect", ["review", "clip", "short", "silent_final", "existing", "audio"])
def test_cannot_assemble_or_replace_invalid_material(ready, defect):
    job = ready["job_dir"]
    if defect == "review": (job / "shot-03/attempt-1/review.json").unlink()
    elif defect == "clip": (job / "shot-03/attempt-1/clip.mp4").write_bytes(b"changed")
    elif defect == "short": ready["renderer"].short_clip = True
    elif defect == "silent_final": ready["renderer"].final_bad = True
    elif defect == "existing": (job / "film.mp4").write_bytes(b"user-existing-file")
    else: ready["narration"].write_bytes(b"other-audio")
    with pytest.raises(PackageError):
        assemble_film(**ready)
    assert not (job / "assembly-report.json").exists()
    if defect == "existing":
        assert (job / "film.mp4").read_bytes() == b"user-existing-file"


@pytest.mark.parametrize("peak,success", [("-inf", False), ("-10.1", True), ("invalid", False)])
def test_silent_track_is_not_audible(monkeypatch, tmp_path, peak, success):
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=f"RMS level dB: {peak}"))
    if success:
        require_audible(SimpleNamespace(ffmpeg="ffmpeg"), tmp_path / "voice.wav")
    else:
        with pytest.raises(PackageError):
            require_audible(SimpleNamespace(ffmpeg="ffmpeg"), tmp_path / "voice.wav")


def test_caption_command_never_loops_video_or_changes_playback_speed(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(module, "_run", lambda command, **kwargs: commands.append(command))
    burn_timed_captions(SimpleNamespace(ffmpeg="ffmpeg"), tmp_path / "source.mp4", tmp_path / "out.mp4",
                        cues=[{"text": "一个回忆", "start_seconds": 3, "end_seconds": 5}], offset=4, seconds=4)
    command = commands[0]
    assert command[:4] == ["ffmpeg", "-y", "-i", str(tmp_path / "source.mp4")]
    assert "-stream_loop" not in command
    graph = command[command.index("-filter_complex") + 1]
    assert "gte(t,0.000)*lt(t,1.000)" in graph
    assert "setpts" not in graph and "tpad" not in graph


def test_real_ffmpeg_burns_visible_chinese_captions_and_decodes(tmp_path):
    """One-second flat-color fixture: codec test only, NOT story/video QA."""
    from PIL import Image, ImageStat
    from lingnian_worker.media import _font
    font = _font(25, require_cjk=True)
    assert bytes(font.getmask("年")) != bytes(font.getmask("记")), "Chinese glyphs must not be identical missing-glyph boxes"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source, target = tmp_path / "source.mp4", tmp_path / "captioned.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=24:d=1",
                    "-c:v", "libx264", str(source)], check=True, capture_output=True)
    burn_timed_captions(SimpleNamespace(ffmpeg=ffmpeg), source, target,
                        cues=[{"text": "一九八二年的回忆", "start_seconds": 0.5, "end_seconds": 1}], offset=0, seconds=1)
    subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(target), "-f", "null", "-"], check=True, capture_output=True)
    values = []
    for second in (0.25, 0.75):
        image = tmp_path / f"frame-{second}.png"
        subprocess.run([ffmpeg, "-v", "error", "-ss", str(second), "-i", str(target), "-frames:v", "1", str(image)], check=True, capture_output=True)
        with Image.open(image) as frame:
            values.append(ImageStat.Stat(frame.convert("L").crop((350, 570, 930, 700))).stddev[0])
    assert values[0] < 2 and values[1] > 10


def test_real_audio_detector_distinguishes_tone_from_silence(tmp_path):
    """Synthetic tone validates audio presence only, not narration quality."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for name, source in (("tone", "sine=frequency=440:duration=0.25"), ("silence", "anullsrc=r=48000:cl=mono")):
        audio = tmp_path / f"{name}.wav"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", source, "-t", "0.25", str(audio)], check=True, capture_output=True)
        if name == "tone": require_audible(SimpleNamespace(ffmpeg=ffmpeg), audio)
        else:
            with pytest.raises(PackageError):
                require_audible(SimpleNamespace(ffmpeg=ffmpeg), audio)
