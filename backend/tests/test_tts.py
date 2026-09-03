from __future__ import annotations

import io
import wave

from app.services.tts.provider import add_wav_lead_in


def wav_bytes(*, frames: int = 2400, sample_rate: int = 24_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x01\x00" * frames)
    return buffer.getvalue()


def test_add_wav_lead_in_preserves_audio_after_quiet_start():
    original = wav_bytes()
    prepared = add_wav_lead_in(original, duration_ms=180)

    with wave.open(io.BytesIO(prepared), "rb") as audio:
        assert audio.getframerate() == 24_000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        frames = audio.readframes(audio.getnframes())

    silence_size = round(24_000 * 0.18) * 2
    assert frames[:silence_size] == b"\x00" * silence_size
    assert frames[silence_size:] == b"\x01\x00" * 2400


def test_add_wav_lead_in_leaves_invalid_input_unchanged():
    assert add_wav_lead_in(b"not-wave") == b"not-wave"
