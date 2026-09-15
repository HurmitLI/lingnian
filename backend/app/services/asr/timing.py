"""Retain source ASR estimates, never invent timings for edited story text."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import wave


def pcm_evidence(path: Path) -> dict:
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as audio:
        rate, channels, width = audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
        frames = audio.getnframes()
        if (rate, channels, width) != (16000, 1, 2) or not 0 < frames <= rate * 7200:
            raise ValueError("unsupported_normalized_audio")
        size = 0
        while chunk := audio.readframes(32768):
            size += len(chunk)
            digest.update(chunk)
        if size != frames * channels * width:
            raise ValueError("truncated_normalized_audio")
    return {"pcm_sha256": digest.hexdigest(), "frames": frames, "sample_rate": rate,
            "duration_ms": frames * 1000 / rate}


def _items_valid(items: object, duration_ms: float) -> bool:
    if not isinstance(items, list) or not 0 < len(items) <= 30000:
        return False
    previous = 0.0
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return False
        if not 0 < len(item["text"]) <= 4000:
            return False
        start, end = item.get("start_ms"), item.get("end_ms")
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
               for t in (start, end)):
            return False
        if not previous <= start < end <= duration_ms:
            return False
        previous = end
    return True


def source_timing(path: Path, value: object, provider: str) -> dict:
    base = {"version": 1, "clock": "asr_input_pcm", "text_basis": "raw_asr",
            "review_status": "unreviewed", "status": "unavailable"}
    try:
        base.update(pcm_evidence(path))
    except (OSError, EOFError, wave.Error, ValueError):
        return {**base, "reason": "normalized_audio_unavailable"}
    items = []
    if provider == "funasr" and isinstance(value, dict):
        # Only a one-to-one raw-token correspondence is accepted. No allocation
        # by character count when a different model returns another convention.
        tokens = re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", str(value.get("text", "")))
        stamps = value.get("timestamp")
        if isinstance(stamps, list) and len(stamps) == len(tokens):
            for token, stamp in zip(tokens, stamps):
                if not isinstance(stamp, (list, tuple)) or len(stamp) != 2:
                    break
                items.append({"text": token, "start_ms": stamp[0], "end_ms": stamp[1]})
            if len(items) != len(tokens):
                items = []
        granularity = "token"
    elif provider == "dashscope":
        sentences = [value] if isinstance(value, dict) else value
        if isinstance(sentences, list):
            items = [{"text": s.get("text"), "start_ms": s.get("begin_time"),
                      "end_ms": s.get("end_time")} for s in sentences if isinstance(s, dict)]
            if len(items) != len(sentences):
                items = []
        granularity = "sentence"
    else:
        granularity = "unknown"
    if not _items_valid(items, base["duration_ms"]):
        return {**base, "reason": "missing_or_invalid_source_timestamps"}
    return {**base, "status": "available", "granularity": granularity, "items": items}


def bound_answer_timing(metadata: object, evidence: dict) -> dict:
    timing = metadata.get("timing") if isinstance(metadata, dict) else None
    if (isinstance(timing, dict) and timing.get("version") == 1
            and timing.get("status") == "available" and timing.get("clock") == "asr_input_pcm"
            and timing.get("text_basis") == "raw_asr"
            and timing.get("pcm_sha256") == evidence["pcm_sha256"]
            and timing.get("frames") == evidence["frames"]
            and timing.get("sample_rate") == evidence["sample_rate"]
            and timing.get("granularity") in {"token", "sentence"}
            and _items_valid(timing.get("items"), evidence["duration_ms"])):
        # Whitelist, not the entire vendor response or arbitrary metadata.
        return {"status": "available", "clock": "segment_relative_ms", "text_basis": "raw_asr",
                "review_status": "unreviewed", "granularity": timing["granularity"],
                "items": [{k: item[k] for k in ("text", "start_ms", "end_ms")}
                          for item in timing["items"]]}
    return {"status": "unavailable", "reason": "missing_invalid_or_mismatched_source_timing"}
