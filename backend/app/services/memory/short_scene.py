"""Read-only excerpt proposals. Timing is evidence, not story understanding.

Never allocate time by character count or include an interviewer's question.
These are candidates for semantic/listening review, not executable GPU jobs.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path

from app.services.asr.timing import pcm_evidence


MIN_COMPLETE_EXCERPT_SECONDS = 4
MAX_COMPLETE_EXCERPT_SECONDS = 10


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def with_sentence_timing(metadata: dict, raw_answers: dict[str, str]) -> dict:
    """Recover punctuation boundaries only when raw ASR tokens match exactly.

    Corrected story text is deliberately not an input. No fuzzy matching,
    paraphrase alignment, punctuation insertion or proportional timestamps.
    """
    updated = deepcopy(metadata)
    timeline = updated.get("interview_timeline")
    if not isinstance(timeline, dict) or not isinstance(timeline.get("segments"), list):
        return updated
    for segment in timeline["segments"]:
        if not isinstance(segment, dict) or segment.get("role") != "answer":
            continue
        timing = segment.get("asr_timing")
        text = raw_answers.get(segment.get("turn_id"))
        if (not isinstance(timing, dict) or timing.get("granularity") != "token"
                or not isinstance(text, str) or not text.strip()):
            continue
        tokens = list(re.finditer(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))
        items = timing.get("items")
        if (not isinstance(items, list) or not items or len(items) != len(tokens)
                or any(not isinstance(item, dict) or token.group() != item.get("text") for token, item in zip(tokens, items))):
            continue
        # Validate every token before collapsing to sentences; otherwise a bad
        # interior timestamp could be hidden by valid sentence endpoints.
        previous, valid = 0, True
        for item in items:
            a, b = item.get("start_ms"), item.get("end_ms")
            if not _number(a) or not _number(b) or not previous <= a < b:
                valid = False
                break
            previous = b
        if not valid:
            continue
        sentences, first = [], 0
        for index, token in enumerate(tokens):
            end_char = tokens[index + 1].start() if index + 1 < len(tokens) else len(text)
            punctuation = text[token.end():end_char]
            if re.search(r"[。！？.!?]", punctuation):
                sentences.append({"text": text[tokens[first].start():end_char].strip(),
                                  "start_ms": items[first]["start_ms"], "end_ms": items[index]["end_ms"]})
                first = index + 1
        if sentences and first == len(tokens):
            segment["asr_timing"] = {**timing, "granularity": "sentence", "items": sentences,
                                     "alignment_method": "exact_raw_asr_tokens_with_original_punctuation"}
    return updated


def propose_short_scenes(metadata: dict, *, audio_asset_id: str) -> dict:
    result = {"version": 1, "status": "needs_source_timing", "candidates": [],
              "generation_ready": False,
              "notice": "仅按真实语句时间筛选候选；仍需结合整段回忆核对语义、单一场景和原声，尚未生成视频。"}
    timeline = metadata.get("interview_timeline") if isinstance(metadata, dict) else None
    if not isinstance(timeline, dict):
        return result
    rate, frames = timeline.get("sample_rate"), timeline.get("frames")
    if (timeline.get("version") != 1 or timeline.get("clock") != "merged_audio_pcm"
            or timeline.get("asset_id") != audio_asset_id or rate != 16000
            or type(frames) is not int or not 0 < frames <= 16000 * 7200
            or not isinstance(timeline.get("pcm_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", timeline["pcm_sha256"]) is None):
        return result
    segments = timeline.get("segments")
    if not isinstance(segments, list) or not 0 < len(segments) <= 1000:
        return result
    previous = 0
    candidates = []
    for segment in segments:
        if not isinstance(segment, dict):
            return result
        start, end = segment.get("start_frame"), segment.get("end_frame")
        if (type(start) is not int or type(end) is not int
                or not previous == start < end <= frames
                or segment.get("role") not in {"answer", "question"}):
            return result
        previous = end
        timing = segment.get("asr_timing")
        if segment["role"] != "answer" or not isinstance(timing, dict):
            continue
        # Token-only timestamps have lost sentence punctuation. Do not invent
        # sentence boundaries or cut a complete sentence into a ten-second slot.
        if (timing.get("status") != "available" or timing.get("granularity") != "sentence"
                or timing.get("clock") != "segment_relative_ms"
                or timing.get("text_basis") != "raw_asr"):
            continue
        items = timing.get("items")
        if not isinstance(items, list) or not 0 < len(items) <= 1000:
            return result
        last_end = 0
        for item in items:
            if not isinstance(item, dict):
                return result
            a, b, text = item.get("start_ms"), item.get("end_ms"), item.get("text")
            if (not _number(a) or not _number(b) or not last_end <= a < b <= (end - start) * 1000 / rate
                    or not isinstance(text, str) or not 0 < len(text.strip()) <= 4000):
                return result
            last_end = b
        for index, first in enumerate(items):
            for stop in range(index, min(index + 4, len(items))):
                group = items[index:stop + 1]
                duration = (group[-1]["end_ms"] - first["start_ms"]) / 1000
                if duration > MAX_COMPLETE_EXCERPT_SECONDS:
                    break
                if duration < MIN_COMPLETE_EXCERPT_SECONDS or any(not re.search(r"[。！？.!?][\"”’']?$", x["text"].strip()) for x in group):
                    continue
                if any(b["start_ms"] - a["end_ms"] > 1500 for a, b in zip(group, group[1:])):
                    continue
                candidate = {
                    "audio_asset_id": audio_asset_id, "source_pcm_sha256": timeline["pcm_sha256"],
                    "sample_rate": rate, "source_frames": frames,
                    "start_frame": start + round(first["start_ms"] * rate / 1000),
                    "end_frame": start + round(group[-1]["end_ms"] * rate / 1000),
                    "text": "".join(x["text"] for x in group),
                    "turn_id": segment.get("turn_id"), "review_status": "needs_context_and_listening_review",
                    "duration_seconds": round(duration, 3),
                }
                candidate["id"] = hashlib.sha256(json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                candidates.append(candidate)
    if previous != frames:
        return result
    candidates.sort(key=lambda c: (abs(10 - c["duration_seconds"]), c["start_frame"]))
    return {**result, "status": "candidates_ready" if candidates else "no_complete_excerpt",
            "candidates": candidates[:5]}


def bind_short_scene_source(metadata: dict, *, raw_answers: dict[str, str], audio_asset_id: str,
                            candidate_id: str, normalized_audio: Path,
                            source_use_authorized: bool) -> dict:
    """Build the source half of a worker plan from SERVER-owned metadata.

    Callers must obtain authorization and decrypt/normalize the controlled asset
    first. This does not accept client-supplied timings or grant consent itself.
    It intentionally omits reference/scene, so it cannot start GPU generation.
    """
    if source_use_authorized is not True:
        raise ValueError("SHORT_SCENE_SOURCE_AUTHORIZATION_REQUIRED")
    aligned = with_sentence_timing(metadata, raw_answers)
    proposals = propose_short_scenes(aligned, audio_asset_id=audio_asset_id)
    selected = next((c for c in proposals["candidates"] if c["id"] == candidate_id), None)
    if selected is None:
        raise ValueError("SHORT_SCENE_SELECTION_STALE")
    actual = pcm_evidence(normalized_audio)
    if any(actual[key] != selected[field] for key, field in (
        ("pcm_sha256", "source_pcm_sha256"), ("frames", "source_frames"), ("sample_rate", "sample_rate"),
    )):
        raise ValueError("SHORT_SCENE_RECORDING_MISMATCH")
    items, context = [], []
    for segment in aligned["interview_timeline"]["segments"]:
        if segment["role"] != "answer":
            continue
        original = raw_answers.get(segment.get("turn_id"))
        if not isinstance(original, str) or not original.strip():
            raise ValueError("SHORT_SCENE_FULL_CONTEXT_MISSING")
        context.append(original)
        timing = segment.get("asr_timing", {})
        if (timing.get("status") != "available" or timing.get("granularity") != "sentence"
                or timing.get("clock") != "segment_relative_ms" or timing.get("text_basis") != "raw_asr"):
            continue
        for cue in timing["items"]:
            if cue["text"] not in original:
                raise ValueError("SHORT_SCENE_RAW_TEXT_MISMATCH")
            items.append({"role": "answer", "text": cue["text"],
                          "start_frame": segment["start_frame"] + round(cue["start_ms"] * 16),
                          "end_frame": segment["start_frame"] + round(cue["end_ms"] * 16)})
    first = next((i for i, cue in enumerate(items) if cue["start_frame"] == selected["start_frame"]), None)
    last = next((i for i, cue in enumerate(items) if cue["end_frame"] == selected["end_frame"]), None)
    if first is None or last is None or first > last or "".join(i["text"] for i in items[first:last + 1]) != selected["text"]:
        raise ValueError("SHORT_SCENE_SELECTION_MISMATCH")
    # PCM hash covers samples; worker hash covers WAV bytes, including headers.
    # They have different meanings and must NEVER be copied into each other.
    sha = hashlib.sha256()
    with normalized_audio.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    file_hash = sha.hexdigest()
    return {"format": "lingnian-short-scene", "version": 1, "duration_seconds": 10,
            "status": "awaiting_semantic_scene_and_reference", "generation_ready": False,
            "source_text": "\n".join(context),
            "recording": {"kind": "authorized_recording", "usage_authorized": True,
                          "sha256": file_hash, "frames": actual["frames"]},
            "timing": {"recording_sha256": file_hash, "basis": "actual_asr_sentence_timestamps", "items": items},
            "selection": {"first_sentence": first, "last_sentence": last},
            "source_binding": {"audio_asset_id": audio_asset_id, "candidate_id": candidate_id,
                               "pcm_sha256": actual["pcm_sha256"], "file_sha256": file_hash}}
