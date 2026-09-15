import hashlib
import wave

import pytest

from lingnian_worker.film_narration import build_narration
from lingnian_worker.models import PackageError


@pytest.fixture
def bundle(tmp_path):
    chapters = []
    for n in (1, 2, 3):
        path = tmp_path / f"chapter-{n}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes([n, 0]) * 240000)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        chapters.append({"id": str(n), "text": str(n), "path": path, "sha256": sha,
                         "kind": "licensed_synthetic_voice", "usage_authorized": True})
    # Third fixture is retained on disk for the overlength check below.
    chapters.pop()
    return dict(chapters=chapters, source_text="1,2", output=tmp_path / "out")


def test_sample_exact_whole_chapters_and_idempotent_bundle(bundle):
    report = build_narration(**bundle)
    assert report["duration_seconds"] == 60
    assert report["chapters"][1]["start_sample"] == 240000
    assert report["listening_review"] == "pending"
    assert report["subtitle_alignment"] == "pending_sentence_alignment"
    assert build_narration(**bundle)["audio_sha256"] == report["audio_sha256"]
    from pathlib import Path
    with wave.open(str(Path(report["directory"]) / "narration.wav")) as audio:
        assert audio.getnframes() == 480000
        assert audio.readframes(480000) == b"\x01\x00" * 240000 + b"\x02\x00" * 240000


@pytest.mark.parametrize("defect", ["no_consent", "bad_hash", "wrong_text", "duplicate_id", "duplicate_audio", "short", "long", "header"])
def test_invalid_source_cannot_build_film_audio(bundle, defect):
    chapter = bundle["chapters"][0]
    if defect == "no_consent": chapter["usage_authorized"] = False
    elif defect == "bad_hash": chapter["sha256"] = "0" * 64
    elif defect == "wrong_text": chapter["text"] = "invented"
    elif defect == "duplicate_id": bundle["chapters"][1]["id"] = chapter["id"]
    elif defect == "duplicate_audio": bundle["chapters"][1] = {**chapter, "id": "other"}
    elif defect == "short": bundle["chapters"].pop()
    elif defect == "long":
        path = chapter["path"].with_name("chapter-3.wav")
        bundle["chapters"].append({**chapter, "id": "3", "text": "1", "path": path,
                                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    else:
        data = bytearray(chapter["path"].read_bytes())
        data[40:44] = (0x7fffffff).to_bytes(4, "little")
        chapter["path"].write_bytes(data)
        chapter["sha256"] = hashlib.sha256(data).hexdigest()
    with pytest.raises(PackageError):
        build_narration(**bundle)


def test_modified_bundle_is_not_overwritten(bundle):
    from pathlib import Path
    report = build_narration(**bundle)
    path = Path(report["directory"]) / "narration.wav"
    path.write_bytes(b"changed")
    with pytest.raises(PackageError):
        build_narration(**bundle)
    assert path.read_bytes() == b"changed"
