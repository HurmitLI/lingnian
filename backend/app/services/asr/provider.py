from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
import os
from pathlib import Path
import re
from typing import Protocol

from app.core.config import get_settings


@dataclass
class ASRResult:
    text: str
    provider: str
    model: str
    metadata: dict


class ASRProvider(Protocol):
    def transcribe(self, audio_path: Path) -> ASRResult: ...


class MockASRProvider:
    def transcribe(self, audio_path: Path) -> ASRResult:
        return ASRResult(
            text="小时候，我常跟着家里人去河边。那时候的事情，我还记得一些。",
            provider="mock",
            model="mock-asr-v1",
            metadata={"notice": "这是开发用模拟转写，不代表真实 ASR 验收。"},
        )


class FunASRProvider:
    def __init__(self, model_id: str) -> None:
        settings = get_settings()
        settings.resolved_model_cache_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MODELSCOPE_CACHE", str(settings.resolved_model_cache_root))
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                "FunASR 尚未安装，请安装 backend 的 asr 可选依赖后再试。"
            ) from exc
        self.model_id = model_id
        self.model = AutoModel(model=model_id, disable_update=True)

    def transcribe(self, audio_path: Path) -> ASRResult:
        result = self.model.generate(input=str(audio_path))
        if not result:
            raise RuntimeError("本地 ASR 没有返回转写结果。")
        first = result[0] if isinstance(result, list) else result
        text = first.get("text", "") if isinstance(first, dict) else str(first)
        text = normalize_chinese_spacing(text.strip())
        if not text:
            raise RuntimeError("本地 ASR 返回了空文本。")
        return ASRResult(
            text=text,
            provider="funasr",
            model=self.model_id,
            metadata={"segments": len(result) if isinstance(result, list) else 1},
        )


def extract_dashscope_sentences(value: object) -> str:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return ""
    parts = []
    for sentence in value:
        if isinstance(sentence, dict) and sentence.get("text"):
            parts.append(str(sentence["text"]).strip())
    return normalize_chinese_spacing("".join(parts).strip())


class DashScopeASRProvider:
    def __init__(self, model_id: str) -> None:
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("未配置千问 API Key，不能使用云端语音识别。")
        self.api_key = settings.llm_api_key
        self.model_id = model_id

    def transcribe(self, audio_path: Path) -> ASRResult:
        import dashscope
        from dashscope.audio.asr import Recognition

        dashscope.api_key = self.api_key
        recognition = Recognition(
            model=self.model_id,
            callback=None,
            format="wav",
            sample_rate=16_000,
            semantic_punctuation_enabled=True,
            language_hints=["zh", "en"],
        )
        result = recognition.call(str(audio_path))
        if result.status_code != HTTPStatus.OK:
            raise RuntimeError("云端语音识别暂时没有成功，请稍后重试。")
        sentences = result.get_sentence()
        text = extract_dashscope_sentences(sentences)
        if not text:
            raise RuntimeError("云端语音识别没有返回可用文字。")
        request_id = (
            recognition.get_last_request_id()
            if hasattr(recognition, "get_last_request_id")
            else None
        )
        return ASRResult(
            text=text,
            provider="dashscope",
            model=self.model_id,
            metadata={
                "sentence_count": len(sentences) if isinstance(sentences, list) else 1,
                "request_id": request_id,
            },
        )


def normalize_chinese_spacing(text: str) -> str:
    """移除 ASR 偶尔插入的逐字中文空格，保留中英文之间的正常间隔。"""
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)


@lru_cache
def get_asr_provider() -> ASRProvider:
    settings = get_settings()
    if settings.asr_provider == "mock":
        return MockASRProvider()
    if settings.asr_provider == "funasr":
        return FunASRProvider(settings.asr_model_id)
    if settings.asr_provider == "dashscope":
        return DashScopeASRProvider(settings.asr_model_id)
    raise RuntimeError(f"不支持的 ASR_PROVIDER：{settings.asr_provider}")
