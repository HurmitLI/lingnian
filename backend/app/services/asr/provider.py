from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
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
    raise RuntimeError(f"不支持的 ASR_PROVIDER：{settings.asr_provider}")
