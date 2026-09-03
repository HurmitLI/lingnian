from http import HTTPStatus
from types import SimpleNamespace

import dashscope.audio.asr

from app.services.asr import extract_dashscope_sentences
from app.services.asr.provider import DashScopeASRProvider
import app.services.asr.provider as provider_module


def test_extract_dashscope_sentences_combines_punctuated_results():
    value = [
        {"text": "小时候，家门口有一条河。"},
        {"text": "夏天我们总去河边。"},
    ]
    assert extract_dashscope_sentences(value) == "小时候，家门口有一条河。夏天我们总去河边。"


def test_extract_dashscope_sentences_accepts_single_sentence_and_ignores_noise():
    assert extract_dashscope_sentences({"text": " 我还记得。 "}) == "我还记得。"
    assert extract_dashscope_sentences({"begin_time": 0}) == ""


def test_dashscope_provider_uses_non_streaming_callback_and_local_file(monkeypatch, tmp_path):
    created_with = {}
    called_with = []

    class FakeResult:
        status_code = HTTPStatus.OK

        @staticmethod
        def get_sentence():
            return [{"text": "这是一次语音识别测试。"}]

    class FakeRecognition:
        def __init__(self, **kwargs):
            created_with.update(kwargs)

        @staticmethod
        def get_last_request_id():
            return "request-test"

        def call(self, audio_path):
            called_with.append(audio_path)
            return FakeResult()

    monkeypatch.setattr(
        provider_module,
        "get_settings",
        lambda: SimpleNamespace(llm_api_key="test-key"),
    )
    monkeypatch.setattr(dashscope.audio.asr, "Recognition", FakeRecognition)
    audio_path = tmp_path / "answer.wav"
    audio_path.write_bytes(b"test-audio")

    result = DashScopeASRProvider("paraformer-realtime-v2").transcribe(audio_path)

    assert created_with["callback"] is None
    assert created_with["format"] == "wav"
    assert called_with == [str(audio_path)]
    assert result.text == "这是一次语音识别测试。"
    assert result.metadata["request_id"] == "request-test"
