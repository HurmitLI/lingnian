from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes
    provider: str
    model: str
    voice: str


class TTSProvider(Protocol):
    def synthesize(self, text: str) -> SpeechResult: ...


def add_wav_lead_in(audio: bytes, duration_ms: int = 180) -> bytes:
    """Give browsers and speakers a short quiet lead-in before the first syllable."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getcomptype() != "NONE":
                return audio
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
        silence_frames = round(sample_rate * max(0, duration_ms) / 1000)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(sample_rate)
            output.writeframes(b"\x00" * silence_frames * channels * sample_width)
            output.writeframes(frames)
        return buffer.getvalue()
    except (EOFError, OSError, ValueError, wave.Error):
        return audio


class MockTTSProvider:
    def synthesize(self, text: str) -> SpeechResult:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24_000)
            output.writeframes(b"\x00\x00" * 4_800)
        return SpeechResult(
            audio=buffer.getvalue(),
            provider="mock",
            model="mock-tts-v1",
            voice="mock",
        )


class DashScopeTTSProvider:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("未配置千问 API Key，不能使用自然语音。")
        self.api_key = settings.llm_api_key
        self.model = settings.tts_model
        self.voice = settings.tts_voice
        self.rate = settings.tts_rate
        self.pitch = settings.tts_pitch
        self.volume = settings.tts_volume

    def synthesize(self, text: str) -> SpeechResult:
        from dashscope.audio.http_tts.http_speech_synthesizer import (
            HttpSpeechSynthesizer,
        )

        result = HttpSpeechSynthesizer.call(
            model=self.model,
            text=text.strip(),
            voice=self.voice,
            audio_format="wav",
            sample_rate=24_000,
            volume=self.volume,
            rate=self.rate,
            pitch=self.pitch,
            language_hints=["zh"],
            seed=20260903,
            stream=False,
            api_key=self.api_key,
        )
        audio_url = getattr(result, "audio_url", None)
        if not audio_url:
            raise RuntimeError("语音服务没有返回可用音频。")
        response = httpx.get(audio_url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        audio = response.content
        if not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"):
            raise RuntimeError("语音服务返回了无法识别的音频。")
        return SpeechResult(
            audio=add_wav_lead_in(audio),
            provider="dashscope",
            model=self.model,
            voice=self.voice,
        )


@lru_cache
def get_tts_provider() -> TTSProvider:
    settings = get_settings()
    if settings.app_env == "test" or settings.tts_provider == "mock":
        return MockTTSProvider()
    if settings.tts_provider == "dashscope" or (
        settings.tts_provider == "auto"
        and settings.llm_provider == "qwen"
        and settings.llm_api_key
    ):
        return DashScopeTTSProvider()
    return MockTTSProvider()
