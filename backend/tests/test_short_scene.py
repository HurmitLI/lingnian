from copy import deepcopy

import pytest

from app.services.memory.short_scene import propose_short_scenes, with_sentence_timing


def metadata():
    return {"interview_timeline": {
        "version": 1, "clock": "merged_audio_pcm", "asset_id": "audio", "sample_rate": 16000,
        "frames": 960000, "pcm_sha256": "a" * 64,
        "segments": [
            {"role": "question", "start_frame": 0, "end_frame": 160000},
            {"role": "answer", "turn_id": "turn", "start_frame": 160000, "end_frame": 960000,
             "asr_timing": {"status": "available", "clock": "segment_relative_ms", "text_basis": "raw_asr",
                            "granularity": "sentence", "items": [
                                {"text": "我站在车站等车。", "start_ms": 1000, "end_ms": 10000},
                                {"text": "这句话太长不能截断。", "start_ms": 11000, "end_ms": 26000},
                            ]}},
        ]}}


def test_real_offsets_no_question_no_truncation():
    data = metadata()
    before = deepcopy(data)
    result = propose_short_scenes(data, audio_asset_id="audio")
    assert result["status"] == "candidates_ready"
    assert result["generation_ready"] is False
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["start_frame"] == 176000
    assert candidate["end_frame"] == 320000
    assert candidate["duration_seconds"] == 9
    assert candidate["text"] == "我站在车站等车。"
    assert data == before


def test_four_second_complete_sentence_is_kept_without_padding_the_timing():
    data = metadata()
    data["interview_timeline"]["segments"][1]["asr_timing"]["items"] = [
        {"text": "发工资的时候，让我印象深刻。", "start_ms": 1400, "end_ms": 5420}
    ]
    result = propose_short_scenes(data, audio_asset_id="audio")
    assert result["status"] == "candidates_ready"
    assert result["candidates"][0]["duration_seconds"] == 4.02
    assert result["candidates"][0]["start_frame"] == 182400
    assert result["candidates"][0]["end_frame"] == 246720


@pytest.mark.parametrize("damage", ["wrong_asset", "nan", "overlap", "token", "incomplete", "truncated", "boolean"])
def test_unreliable_sources_are_not_executable(damage):
    data = metadata()
    timeline = data["interview_timeline"]
    timing = timeline["segments"][1]["asr_timing"]
    if damage == "wrong_asset":
        timeline["asset_id"] = "old"
    elif damage == "nan":
        timing["items"][0]["start_ms"] = float("nan")
    elif damage == "overlap":
        timing["items"][1]["start_ms"] = 9000
    elif damage == "token":
        timing["granularity"] = "token"
    elif damage == "incomplete":
        timing["items"][0]["text"] = "我站在车站等"
    elif damage == "truncated":
        timeline["frames"] += 1
    else:
        timing["items"][0]["start_ms"] = True
    result = propose_short_scenes(data, audio_asset_id="audio")
    assert result["candidates"] == []
    assert result["generation_ready"] is False


def test_missing_timing_never_guesses():
    assert propose_short_scenes({}, audio_asset_id="audio")["status"] == "needs_source_timing"


def test_exact_raw_tokens_can_recover_existing_punctuation_only():
    data = metadata()
    timing = data["interview_timeline"]["segments"][1]["asr_timing"]
    timing["granularity"] = "token"
    timing["items"] = [{"text": token, "start_ms": 1000 + index * 1000, "end_ms": 2000 + index * 1000}
                       for index, token in enumerate("我站在车站等车啊")]
    before = deepcopy(data)
    aligned = with_sentence_timing(data, {"turn": "我站在车站等车啊。"})
    assert data == before
    result = propose_short_scenes(aligned, audio_asset_id="audio")
    assert result["candidates"][0]["text"] == "我站在车站等车啊。"
    assert result["candidates"][0]["duration_seconds"] == 8
    for changed in ("我站在汽车站等车啊。", "我站在车站等车啊"):
        assert propose_short_scenes(with_sentence_timing(data, {"turn": changed}), audio_asset_id="audio")["candidates"] == []
    timing["items"][2]["end_ms"] = float("nan")
    assert propose_short_scenes(with_sentence_timing(data, {"turn": "我站在车站等车啊。"}), audio_asset_id="audio")["candidates"] == []


def test_preview_endpoint_requires_existing_session(client):
    response = client.get("/api/v1/memory-sessions/missing/short-scene-preview")
    assert response.status_code == 404


def test_preview_of_new_interview_does_not_create_generation(client, db):
    from sqlalchemy import select, func
    from app.models import GenerativeMediaRequest
    family = client.post("/api/v1/families", json={"display_name": "测试", "idempotency_key": "short"}).json()
    elder = client.post("/api/v1/elder-profiles", json={"family_id": family["id"], "display_name": "妈妈", "preferred_name": "妈妈"}).json()
    session = client.post("/api/v1/memory-sessions", json={"elder_id": elder["id"], "life_stage": "童年"}).json()
    response = client.get(f"/api/v1/memory-sessions/{session['id']}/short-scene-preview")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_source_timing"
    assert db.scalar(select(func.count()).select_from(GenerativeMediaRequest)) == 0
