import wave

import pytest

from app.services.asr.provider import FunASRProvider
from app.services.asr.timing import bound_answer_timing, pcm_evidence, source_timing


@pytest.fixture
def audio_path(tmp_path):
    path = tmp_path / "answer.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return path


def test_funasr_provider_retains_actual_tokens_without_new_model(audio_path):
    class Model:
        def generate(self, **kwargs):
            return [{"text": "妈 妈", "timestamp": [[10, 100], [150, 300]]}]
    provider = FunASRProvider.__new__(FunASRProvider)
    provider.model, provider.model_id = Model(), "test-only"
    result = provider.transcribe(audio_path)
    assert result.text == "妈妈"
    timing = result.metadata["timing"]
    assert timing["status"] == "available"
    assert timing["duration_ms"] == 1000
    assert timing["items"][1] == {"text": "妈", "start_ms": 150, "end_ms": 300}


@pytest.mark.parametrize("stamps", [None, [[0, 50]], [[0, 100], [80, 200]],
    [[-1, 50], [50, 100]], [[0, 100], [100, 1001]], [[0, 50], [50, float("nan")]],
    [[False, 50], [50, 100]], [[0, 50], [50]]])
def test_invalid_timing_is_unavailable_not_guessed(audio_path, stamps):
    timing = source_timing(audio_path, {"text": "妈 妈", "timestamp": stamps}, "funasr")
    assert timing["status"] == "unavailable"
    assert "items" not in timing


def test_dashscope_sentence_milliseconds_and_unknown_fields_not_retained(audio_path):
    timing = source_timing(audio_path, [{"text": "妈妈。", "begin_time": 20, "end_time": 800,
                                       "secret": "must-not-copy"}], "dashscope")
    assert timing["granularity"] == "sentence"
    assert timing["items"] == [{"text": "妈妈。", "start_ms": 20, "end_ms": 800}]
    assert "secret" not in str(timing)


def test_invalid_audio_does_not_break_transcription(tmp_path):
    path = tmp_path / "not.wav"
    path.write_bytes(b"invalid")
    assert source_timing(path, {}, "funasr")["reason"] == "normalized_audio_unavailable"


def test_merge_only_reuses_timing_for_identical_pcm(audio_path):
    timing = source_timing(audio_path, {"text": "妈", "timestamp": [[0, 200]]}, "funasr")
    evidence = pcm_evidence(audio_path)
    assert bound_answer_timing({"timing": timing}, evidence)["status"] == "available"
    evidence["pcm_sha256"] = "0" * 64
    assert bound_answer_timing({"timing": timing}, evidence)["status"] == "unavailable"
