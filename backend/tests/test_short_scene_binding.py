"""Synthetic PCM fixtures: source adapter validation, not real ASR/GPU tests."""
import hashlib
import wave
from copy import deepcopy

import pytest

from app.services.asr.timing import pcm_evidence
from app.services.memory.short_scene import bind_short_scene_source, propose_short_scenes


@pytest.fixture
def source(tmp_path):
    audio = tmp_path / "normalized.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x01\x00" * 960000)
    metadata = {"interview_timeline": {"version": 1, "clock": "merged_audio_pcm", "asset_id": "source",
        **pcm_evidence(audio), "segments": [
            {"role": "question", "start_frame": 0, "end_frame": 160000},
            {"role": "answer", "turn_id": "turn", "start_frame": 160000, "end_frame": 960000,
             "asr_timing": {"status": "available", "granularity": "sentence", "clock": "segment_relative_ms", "text_basis": "raw_asr",
                            "items": [{"text": "我在站台等车。", "start_ms": 1000, "end_ms": 10000}]}}
        ]}}
    candidate = propose_short_scenes(metadata, audio_asset_id="source")["candidates"][0]
    return dict(metadata=metadata, audio_asset_id="source", candidate_id=candidate["id"],
                raw_answers={"turn": "我在站台等车。后来我到了无锡。"}, normalized_audio=audio, source_use_authorized=True)


def test_maps_samples_to_file_hash_without_losing_full_context(source):
    before = deepcopy(source["metadata"])
    result = bind_short_scene_source(**source)
    file_hash = hashlib.sha256(source["normalized_audio"].read_bytes()).hexdigest()
    assert result["recording"]["sha256"] == file_hash
    assert file_hash != result["source_binding"]["pcm_sha256"]
    assert result["timing"]["recording_sha256"] == file_hash
    assert result["timing"]["items"][0]["start_frame"] == 176000
    assert result["timing"]["items"][0]["end_frame"] == 320000
    assert result["selection"] == {"first_sentence": 0, "last_sentence": 0}
    assert "后来我到了无锡" in result["source_text"]
    assert result["generation_ready"] is False and "scene" not in result
    assert source["metadata"] == before


@pytest.mark.parametrize("damage", ["audio", "selection", "consent", "missing_context", "edited_text", "changed_timing"])
def test_source_adapter_fails_closed(source, damage):
    if damage == "audio":
        with wave.open(str(source["normalized_audio"]), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x02\x00" * 960000)
    elif damage == "selection":
        source["candidate_id"] = "wrong"
    elif damage == "consent":
        source["source_use_authorized"] = 1
    elif damage == "missing_context":
        source["raw_answers"] = {}
    elif damage == "edited_text":
        source["raw_answers"]["turn"] = "我乘坐蒸汽火车离开无锡。"
    else:
        source["metadata"]["interview_timeline"]["segments"][1]["asr_timing"]["items"][0]["start_ms"] = 1500
    with pytest.raises(ValueError, match="SHORT_SCENE_"):
        bind_short_scene_source(**source)
