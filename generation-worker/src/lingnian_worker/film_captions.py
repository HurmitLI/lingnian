"""Conservative alignment of approved Chinese text to actual token timestamps.

Only equal token sequences or explicit non-factual homophone/particle variants
are accepted. No duration-by-character guessing and no replacement of the story.
"""
import math
from pathlib import Path
import re
import wave

from .continuity import digest
from .models import PackageError


def align_chapter(text: str, recognition: dict, *, offset: float, duration: float) -> tuple[list, list]:
    if (not isinstance(text, str) or not isinstance(recognition, dict) or not isinstance(recognition.get("text"), str)
            or not all(type(n) in (int, float) and math.isfinite(n) for n in (offset, duration))
            or offset < 0 or duration <= 0):
        raise PackageError("字幕对齐输入或时间范围不正确。")
    tokens = list(re.finditer(r"[\u3400-\u9fff]|[A-Za-z0-9]+", text))
    heard, stamps = recognition.get("text", "").split(), recognition.get("timestamp")
    if not tokens or not isinstance(stamps, list) or len(heard) != len(tokens) or len(stamps) != len(tokens):
        raise PackageError("原文与真实时间戳数量不同，不能按字数猜时间。")
    changes, previous = [], 0
    for index, (token, word, pair) in enumerate(zip(tokens, heard, stamps)):
        original = token.group()
        if original != word:
            same_pronoun = original in "她他它" and word in "她他它" and len(word) == 1
            particle = original in "啊呀" and word in "啊呀" and len(word) == 1
            if not same_pronoun and not particle:
                raise PackageError("原文与识别存在实质差异，需核对后再对齐，不能改写原文。")
            changes.append({"token": index, "original": original, "recognized": word,
                            "reason": "pronoun_homophone" if same_pronoun else "particle_variant"})
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in pair)
                or not previous <= pair[0] < pair[1] <= duration * 1000):
            raise PackageError("识别时间戳倒序、重叠或超出真实音频。")
        previous = pair[1]
    cues, start = [], 0
    for index, token in enumerate(tokens):
        last = index == len(tokens) - 1
        end_char = tokens[index + 1].start() if not last else len(text)
        punctuation = re.search(r"[，。！？；,.!?;]", text[token.end():end_char])
        if not last and not punctuation and index - start < 17:
            continue
        if stamps[index][1] - stamps[start][0] < 300 and not last:
            continue
        quoted = text[tokens[start].start():end_char].strip()
        cue = {"text": quoted, "source_quote": quoted, "_start_char": tokens[start].start(), "start_seconds": offset + stamps[start][0] / 1000,
               "end_seconds": offset + stamps[index][1] / 1000}
        if cue["end_seconds"] - cue["start_seconds"] < 0.3 and cues:
            before = cues.pop()
            cue["start_seconds"] = before["start_seconds"]
            cue["_start_char"] = before["_start_char"]
            cue["text"] = cue["source_quote"] = text[before["_start_char"]:end_char].strip()
        if not 0.3 <= cue["end_seconds"] - cue["start_seconds"] <= 12 or len(cue["text"]) > 80:
            raise PackageError("字幕需进一步分句或复核，不能生成闪过/过长的字幕。")
        cues.append(cue)
        start = index + 1
    for cue in cues:
        cue.pop("_start_char")
    return cues, changes


def align_narration(manifest: dict, evidence: dict, *, narration: Path) -> dict:
    if not narration.is_file() or digest(narration) != manifest.get("audio_sha256"):
        raise PackageError("整段旁白与时间戳来源不一致。")
    with wave.open(str(narration)) as audio:
        duration = audio.getnframes() / audio.getframerate()
        if not 60 <= duration <= 75 or audio.getframerate() != manifest.get("sample_rate") or abs(duration - manifest["duration_seconds"]) > 0.0001:
            raise PackageError("旁白真实时长或采样率与来源记录不符。")
    cues, differences, previous = [], [], 0
    chapters = manifest.get("chapters")
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 12:
        raise PackageError("缺少分段旁白依据。")
    for chapter in chapters:
        record = evidence.get(chapter["id"])
        if (not isinstance(record, dict) or record.get("source_sha256") != chapter["sha256"]
                or record.get("narration_sha256") != manifest["audio_sha256"]
                or chapter["text"] not in manifest["source_text"]):
            raise PackageError("分段真实识别证据或原文来源不符。")
        start, end = chapter["start_seconds"], chapter["end_seconds"]
        if (not all(type(n) in (int, float) and math.isfinite(n) for n in (start, end))
                or abs(start - previous) > 0.0001 or not start < end <= duration):
            raise PackageError("旁白章节边界不连续或超出音频。")
        aligned, variants = align_chapter(chapter["text"], record["recognition"], offset=start, duration=end - start)
        cues.extend(aligned)
        differences.extend({"chapter": chapter["id"], **v} for v in variants)
        previous = end
    if abs(previous - duration) > 0.0001 or not 1 <= len(cues) <= 60 or cues[-1]["end_seconds"] < duration - 1:
        raise PackageError("字幕未完整覆盖旁白或字幕过多，需进一步复核。")
    return {"format": "lingnian-caption-alignment", "version": 1, "narration_sha256": manifest["audio_sha256"],
            "alignment_method": "actual_local_asr_token_timestamps", "source_text_preserved": True,
            "narration_cues": cues, "recognized_variants": differences, "listening_review": "pending",
            "final_visual_accepted": False}
