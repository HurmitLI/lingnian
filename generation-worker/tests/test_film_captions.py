import pytest

from lingnian_worker.film_captions import align_chapter, align_narration
from lingnian_worker.models import PackageError


def test_real_timestamps_not_character_weight_guessing():
    cues, changes = align_chapter("她来。你好！", {"text": "他 来 你 好", "timestamp": [[100, 250], [400, 700], [1800, 2200], [2300, 2600]]},
                                  offset=10, duration=3)
    assert [c["text"] for c in cues] == ["她来。", "你好！"]
    assert cues[0]["start_seconds"] == 10.1
    assert cues[1]["start_seconds"] == 11.8
    assert changes[0]["reason"] == "pronoun_homophone"


@pytest.mark.parametrize("defect", ["missing", "name", "date", "overlap", "outside", "nan", "reversed"])
def test_substantive_changes_and_invalid_times_do_not_make_fake_alignment(defect):
    source = "无锡。"
    result = {"text": "无 锡", "timestamp": [[100, 400], [400, 700]]}
    if defect == "missing": result["timestamp"].pop()
    elif defect == "name": result["text"] = "无 夕"
    elif defect == "date": source, result["text"] = "八二。", "八 三"
    elif defect == "overlap": result["timestamp"][1][0] = 300
    elif defect == "outside": result["timestamp"][1][1] = 2000
    elif defect == "nan": result["timestamp"][0][0] = float("nan")
    else: result["timestamp"][0] = [400, 100]
    with pytest.raises(PackageError):
        align_chapter(source, result, offset=0, duration=1)


def test_short_tail_clause_merges_without_invented_words():
    cues, _ = align_chapter("你好，啊。", {"text": "你 好 呀", "timestamp": [[0, 300], [300, 500], [550, 650]]}, offset=0, duration=1)
    assert len(cues) == 1 and cues[0]["source_quote"] == "你好，啊。"


def test_unpunctuated_paragraph_splits_without_timing_estimates():
    text = "人" * 30
    cues, _ = align_chapter(text, {"text": " ".join(text), "timestamp": [[i * 200, (i + 1) * 200] for i in range(30)]}, offset=0, duration=6)
    assert len(cues) == 2
    assert "".join(c["text"] for c in cues) == text
    assert cues[1]["start_seconds"] == 3.6


@pytest.fixture
def full_alignment(tmp_path):
    import hashlib
    import wave
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as writer:
        writer.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x01\x00" * 480000)
    sha = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest = {"audio_sha256": sha, "sample_rate": 8000, "duration_seconds": 60, "source_text": "她来。你好。",
                "chapters": [{"id": "one", "sha256": "a" * 64, "text": "她来。", "start_seconds": 0, "end_seconds": 30},
                             {"id": "two", "sha256": "b" * 64, "text": "你好。", "start_seconds": 30, "end_seconds": 60}]}
    evidence = {key: {"narration_sha256": sha, "source_sha256": letter * 64,
                     "recognition": {"text": text, "timestamp": [[29000, 29500], [29500, 29900]]}}
                for key, letter, text in (("one", "a", "他 来"), ("two", "b", "你 好"))}
    return manifest, evidence, audio


def test_full_alignment_checks_real_audio_binding_and_remains_unreviewed(full_alignment):
    manifest, evidence, audio = full_alignment
    result = align_narration(manifest, evidence, narration=audio)
    assert result["listening_review"] == "pending"
    assert result["final_visual_accepted"] is False
    assert result["narration_cues"][-1]["end_seconds"] == 59.9


@pytest.mark.parametrize("change", ["audio", "evidence", "chapter"])
def test_full_alignment_rejects_changed_evidence_or_audio(full_alignment, change):
    manifest, evidence, audio = full_alignment
    if change == "audio": audio.write_bytes(b"changed")
    elif change == "evidence": evidence["two"]["source_sha256"] = "0" * 64
    else: manifest["chapters"][1]["start_seconds"] = 29
    with pytest.raises(PackageError):
        align_narration(manifest, evidence, narration=audio)
