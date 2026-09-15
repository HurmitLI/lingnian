"""Build a traceable 60–75 second narration from whole authorized PCM chapters.

No TTS call, time stretching, partial-sentence cut, silence padding or automatic
listening approval. Chapter times are sample-exact, not forced-aligned subtitles.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import wave

from .continuity import digest
from .film_executor import _read, _save
from .models import PackageError


def build_narration(chapters: list[dict], *, source_text: str, output: Path) -> dict:
    if not isinstance(source_text, str) or not source_text.strip() or not isinstance(chapters, list) or not 1 <= len(chapters) <= 12:
        raise PackageError("需要完整原文与 1–12 个已授权的完整语音段落。")
    chunks, entries, shape, total, ids, hashes = [], [], None, 0, set(), set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise PackageError("旁白段落格式错误。")
        identifier, text = chapter.get("id"), chapter.get("text")
        kind, authorized = chapter.get("kind"), chapter.get("usage_authorized")
        if (not isinstance(identifier, str) or not identifier.strip() or identifier in ids
                or not isinstance(text, str) or not text.strip() or text not in source_text
                or kind not in {"authorized_recording", "licensed_synthetic_voice"} or authorized is not True):
            raise PackageError("旁白缺少原文、唯一编号或已授权来源。")
        ids.add(identifier)
        path = chapter.get("path")
        if not isinstance(path, Path) or not path.is_file() or digest(path) != chapter.get("sha256"):
            raise PackageError("旁白文件与来源摘要不一致。")
        if chapter["sha256"] in hashes:
            raise PackageError("相同音频重复出现，不能重复段落凑时长。")
        hashes.add(chapter["sha256"])
        try:
            with wave.open(str(path), "rb") as audio:
                current = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
                frames = audio.getnframes()
                if (audio.getcomptype() != "NONE" or current[0] not in {1, 2} or current[1] != 2
                        or not 8000 <= current[2] <= 48000 or not 0 < frames <= current[2] * 75):
                    raise PackageError("先规范化为真实时长的 16 位 PCM WAV；不信任流式占位文件头。")
                payload = audio.readframes(frames)
                if len(payload) != frames * current[0] * current[1]:
                    raise PackageError("旁白实际音频不足文件声明长度，不能截断处理。")
        except (wave.Error, EOFError, OSError) as exc:
            raise PackageError("旁白 WAV 无法完整读取。") from exc
        if digest(path) != chapter["sha256"]:
            raise PackageError("读取时旁白文件发生变化，保留原件后重试。")
        if shape is None:
            shape = current
        if current != shape:
            raise PackageError("旁白采样格式不同；不能直接拼接。")
        if not any(payload):
            raise PackageError("旁白段落为数字静音。")
        entries.append({"id": identifier, "text": text, "sha256": chapter["sha256"],
                        "kind": kind, "usage_authorized": True, "start_sample": total,
                        "end_sample": total + frames, "start_seconds": total / shape[2],
                        "end_seconds": (total + frames) / shape[2]})
        total += frames
        if total / shape[2] > 75:
            raise PackageError("完整段落合计超过 75 秒；选择其他完整段落，不自动加速或截断。")
        chunks.append(payload)
    duration = total / shape[2]
    if duration < 60:
        raise PackageError("完整旁白不足 60 秒；不添加静音凑时长。")
    provenance = {"format": "lingnian-narration-bundle", "version": 1,
                  "source_text": source_text, "chapters": entries, "sample_rate": shape[2],
                  "channels": shape[0], "sample_width": shape[1], "duration_seconds": duration,
                  "speed_changed": False, "looping": False, "silence_padding": False,
                  "subtitle_alignment": "pending_sentence_alignment", "listening_review": "pending"}
    binding = hashlib.sha256(json.dumps(provenance, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    folder = output / binding[:24]
    if folder.exists():
        report = _read(folder / "manifest.json")
        expected = {key: value for key, value in report.items() if key not in {"binding", "audio_sha256", "updated_at"}}
        if expected != provenance or report.get("binding") != binding or report.get("audio_sha256") != digest(folder / "narration.wav"):
            raise PackageError("已有声音包摘要改变，保留原件，不覆盖重建。")
        return {**report, "directory": str(folder)}
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".narration-", dir=output))
    audio_path = staging / "narration.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(shape[0])
        audio.setsampwidth(shape[1])
        audio.setframerate(shape[2])
        for chunk in chunks:
            audio.writeframes(chunk)
    report = {**provenance, "binding": binding, "audio_sha256": digest(audio_path)}
    _save(staging / "manifest.json", report)
    # A complete nonempty directory is published together; collisions never
    # replace an existing result. Retain staging on failure for diagnosis.
    if folder.exists():
        raise PackageError("相同声音包同时完成，请保留现场后复核。")
    os.rename(staging, folder)
    return {**report, "directory": str(folder)}
