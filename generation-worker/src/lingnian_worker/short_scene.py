"""Isolated single-shot preview: source-timed audio -> native 10s -> review.

No cloud queue or automatic visual judging. This deliberately bypasses the
legacy renderer's loop/slowdown path and does not extend the long-film contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable

from PIL import Image

from .comfyui import ComfyUiClient
from .continuity import build_wan_graph, checked_run, digest, normalize_segment
from .film_executor import read_review
from .media import MediaRenderer
from .models import PackageError, TemporaryWorkerError


INPUT_CHECKS = ("whole_recording_context", "complete_sentence", "one_scene_one_action",
                "source_matches_scene", "identity", "anatomy", "era_location", "opening_state")
OUTPUT_CHECKS = ("source_matches_scene", "identity", "eyes_face", "hands_limbs", "wardrobe_props",
                 "continuous_motion", "no_cuts_or_style_change", "audio_sync", "full_playback")
MIN_COMPLETE_EXCERPT_SECONDS = 4
MAX_COMPLETE_EXCERPT_SECONDS = 10


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _save(path: Path, value: dict) -> None:
    # Evidence/API graphs must not acquire timestamps that change their hashes
    # or become accidental ComfyUI nodes. Files can contain private context.
    fd, temporary = tempfile.mkstemp(prefix=".short-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 6000


def validate_plan(plan: dict, *, recording: Path, reference: Path) -> dict:
    _require(isinstance(plan, dict) and plan.get("format") == "lingnian-short-scene"
             and type(plan.get("version")) is int and plan["version"] == 1, "不支持的短片方案。")
    _require(type(plan.get("duration_seconds")) is int and plan["duration_seconds"] == 10,
             "此入口只生成一个原生10秒镜头。")
    _require(_text(plan.get("source_text")), "必须先保留整段回忆，不能只按关键词配画面。")
    source, anchor, scene = plan.get("recording"), plan.get("reference"), plan.get("scene")
    _require(isinstance(source, dict) and isinstance(anchor, dict) and isinstance(scene, dict), "缺少原声、参考或场景。")
    for value, file in ((source, recording), (anchor, reference)):
        _require(value.get("usage_authorized") is True and file.is_file()
                 and digest(file) == value.get("sha256"), "素材未授权或与方案摘要不符。")
    fixture = plan.get("test_fixture_only") is True
    _require(source.get("kind") == "authorized_recording"
             or (fixture and source.get("kind") in {"licensed_synthetic_voice", "synthetic_pcm_fixture"}),
             "产品入口只使用获授权的原声；既有合成声音只能用于明确标记的隔离测试。")
    claims = {"user_photo": "photo_reference_not_historical_footage",
              "generated_reference": "illustrative_not_verified_likeness"}
    _require(anchor.get("kind") in claims and anchor.get("identity_claim") == claims[anchor["kind"]],
             "照片来源或示意人物声明不正确。")
    try:
        with Image.open(reference) as picture:
            w, h = picture.size
            _require(picture.format == "PNG" and 640 <= w <= 4096 and 352 <= h <= 4096,
                     "需要经过检查的清晰PNG场景参考，不用小远景直接冒充高清人物。")
            _require(abs(w / h - 1280 / 704) < 0.08, "参考画面比例不符，先重新构图，禁止拉伸人物。")
            picture.verify()
        with wave.open(str(recording), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            _require(audio.getnchannels() == 1 and audio.getsampwidth() == 2 and rate == 16000
                     and 0 < frames <= rate * 7200, "原声须为可核对的16k单声道PCM录音。")
            count = 0
            while chunk := audio.readframes(32768):
                count += len(chunk)
            _require(count == frames * 2, "原声文件截断，不能按头部时长继续。")
    except (OSError, ValueError, EOFError, wave.Error) as exc:
        raise PackageError("素材无法读取。") from exc
    _require(type(source.get("frames")) is int and source["frames"] == frames,
             "原声时长与时间依据不匹配。")
    timing = plan.get("timing")
    _require(isinstance(timing, dict) and timing.get("recording_sha256") == source["sha256"]
             and timing.get("basis") == "actual_asr_sentence_timestamps", "不能按字数猜截取时间。")
    items = timing.get("items")
    _require(isinstance(items, list) and 0 < len(items) <= 1000, "缺少原声语句时间戳。")
    previous = 0
    for item in items:
        _require(isinstance(item, dict), "时间戳条目错误。")
        start, end = item.get("start_frame"), item.get("end_frame")
        _require(type(start) is int and type(end) is int and previous <= start < end <= frames
                 and _text(item.get("text")) and item["text"] in plan["source_text"]
                 and item.get("role") in {"answer", "question"}, "语句来源或真实时间戳非法。")
        previous = end
    selection = plan.get("selection")
    _require(isinstance(selection, dict), "尚未选择原声片段。")
    first, last = selection.get("first_sentence"), selection.get("last_sentence")
    _require(type(first) is int and type(last) is int and 0 <= first <= last < len(items), "截取语句范围非法。")
    chosen = items[first:last + 1]
    _require(all(i["role"] == "answer" and re.search(r"[。！？.!?][\"”’']?$", i["text"].strip()) for i in chosen),
             "只截完整回答，不包含AI问题、不截半句。")
    _require(all(b["start_frame"] - a["end_frame"] <= rate * 1.5 for a, b in zip(chosen, chosen[1:])),
             "片段中有长停顿，请换一段连续的讲述。")
    start, end = chosen[0]["start_frame"], chosen[-1]["end_frame"]
    _require(MIN_COMPLETE_EXCERPT_SECONDS <= (end - start) / rate <= MAX_COMPLETE_EXCERPT_SECONDS,
             "完整语句需为4至10秒；不截断、压速或拼接原声，短于成片的部分保留自然停顿。")
    for key in ("context_summary", "character", "wardrobe", "location", "era", "opening_state", "action", "render_bible", "render_action"):
        _require(_text(scene.get(key)), f"场景缺少{key}。")
    _require(scene.get("shot_count") == 1 and type(scene.get("shot_count")) is int
             and scene.get("subject_count") == 1 and type(scene.get("subject_count")) is int
             and scene.get("camera") == "locked" and scene.get("style") == "consistent_color_live_action",
             "先只支持一个主体、固定机位、稳定彩色的单镜头。")
    quotes = scene.get("source_quotes")
    chosen_text = "".join(i["text"] for i in chosen)
    _require(isinstance(quotes, list) and quotes and all(_text(q) and q in chosen_text for q in quotes),
             "场景必须对应选中的原声，不取其他段落充当依据。")
    _require(len((scene["render_bible"] + " " + scene["render_action"]).split()) <= 220, "场景描述过长。")
    _require(isinstance(scene.get("unknowns"), list) and all(_text(v) for v in scene["unknowns"]), "未知细节需单独列出，不伪造史实。")
    return {"start_frame": start, "end_frame": end, "sample_rate": rate,
            "text": chosen_text, "duration_seconds": (end - start) / rate}


def make_graph(plan: dict) -> dict:
    scene = plan["scene"]
    return build_wan_graph(
        {"render_bible": scene["render_bible"],
         "render_negative": "畸形眼睛，多手，多肢体，脸部融化，人物替换，黑白画面，蒙太奇，切镜，闪烁"},
        {"id": 1, "duration_seconds": 10, "render_action": scene["render_action"]},
        image_name="reference.png", prefix="lingnian-short-scene",
    )


def extract_recording(recording: Path, target: Path, excerpt: dict) -> None:
    with wave.open(str(recording), "rb") as source:
        source.setpos(excerpt["start_frame"])
        count = excerpt["end_frame"] - excerpt["start_frame"]
        pcm = source.readframes(count)
        _require(len(pcm) == count * 2, "原声截取不完整。")
        with wave.open(str(target), "wb") as output:
            output.setparams(source.getparams())
            output.writeframes(pcm)


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    return f"00:00:{milliseconds // 1000:02d},{milliseconds % 1000:03d}"


def execute(plan: dict, *, recording: Path, reference: Path, work_dir: Path,
            client: ComfyUiClient, renderer: MediaRenderer,
            on_generating: Callable[[], None] | None = None,
            authorize_input: Callable[[dict], bool] | None = None) -> dict:
    _require(urlparse(client.base_url).hostname in {"127.0.0.1", "localhost", "::1"}, "只连接家庭节点本机ComfyUI。")
    excerpt = validate_plan(plan, recording=recording, reference=reference)
    graph = make_graph(plan)
    binding = _hash({"plan": plan, "graph": graph, "server": client.base_url})
    folder = work_dir / binding[:24]
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "execution.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TemporaryWorkerError("短片已被领取，先核对旧进程，不重复生成。") from exc
    os.close(fd)
    try:
        def status(name: str, **extra) -> dict:
            value = {"status": name, "binding": binding, "job_dir": str(folder), "excerpt": excerpt,
                     "test_fixture_only": plan.get("test_fixture_only") is True,
                     "final_visual_accepted": name == "accepted", **extra}
            _save(folder / "status.json", value)
            return value

        input_binding = _hash({"job": binding, "stage": "context_and_reference"})
        review = read_review(folder / "input-review.json", input_binding, required_checks=INPUT_CHECKS)
        if authorize_input is not None:
            # The connector obtains this decision from the authenticated backend for
            # this exact encrypted package. Never write a fabricated local review.
            review = "accepted" if authorize_input(plan) is True else None
        if review != "accepted":
            return status("input_rejected" if review == "rejected" else "awaiting_input_review",
                          review_binding=input_binding, required_checks=list(INPUT_CHECKS))
        raw, candidate = folder / "raw.mp4", folder / "candidate.mp4"
        # The existing client's journal resumes prompt_id after interruption and
        # refuses unknown submissions. Never retry with a new untracked seed.
        cached = folder / "candidate-evidence.json"
        if cached.exists():
            evidence = json.loads(cached.read_text(encoding="utf-8"))
            _require(isinstance(evidence, dict) and evidence.get("binding") == binding
                     and candidate.is_file() and digest(candidate) == evidence.get("candidate_sha256")
                     and raw.is_file() and digest(raw) == evidence.get("raw_sha256"), "候选文件已变更，保留现场，不自动重跑。")
        else:
            actual_path = folder / "actual-api.json"
            if actual_path.exists():
                actual = json.loads(actual_path.read_text(encoding="utf-8"))
                normalized = json.loads(json.dumps(actual))
                _require(isinstance(normalized, dict), "实际请求记录损坏。")
                normalized["4"]["inputs"]["image"] = "reference.png"
                normalized["12"]["inputs"]["filename_prefix"] = "lingnian-short-scene"
                _require(normalized == graph, "实际请求已变更，不能套用旧任务。")
                graph = actual
            else:
                _require(not (folder / "job.json").exists(), "已有任务但缺失实际请求，先核对，不重投。")
                graph["4"]["inputs"]["image"] = client.upload_image(reference)
                graph["12"]["inputs"]["filename_prefix"] += "/" + binding[:24]
                _save(actual_path, graph)
            status("generating", gpu_journal=str(folder / "job.json"))
            if on_generating:
                on_generating()  # Validate cloud lease before submitting any new GPU prompt.
            callbacks = {"on_wait": on_generating} if on_generating else {}
            raw = client.run_workflow(graph, output_path=raw, journal_path=folder / "job.json", **callbacks)
            _require(raw.suffix.lower() == ".mp4", "只接受真实动态MP4输出。")
            meta = renderer.probe(raw)
            _require(meta.get("width") == 1280 and meta.get("height") == 704
                     and math.isfinite(meta["duration"]) and 10 <= meta["duration"] <= 10.25,
                     "原生视频尺寸或时长错误，禁止延展凑成10秒。")
            visual = folder / "visual.mp4"
            normalize_segment(renderer, raw, visual, 10)
            audio = folder / "excerpt.wav"
            extract_recording(recording, audio, excerpt)
            chosen = plan["timing"]["items"][plan["selection"]["first_sentence"]:plan["selection"]["last_sentence"] + 1]
            captions = folder / "captions.srt"
            captions.write_text("\n\n".join(
                f"{index}\n{_srt_time((cue['start_frame'] - excerpt['start_frame']) / 16000)} --> "
                f"{_srt_time((cue['end_frame'] - excerpt['start_frame']) / 16000)}\n{cue['text']}"
                for index, cue in enumerate(chosen, 1)) + "\n", encoding="utf-8")
            checked_run([renderer.ffmpeg, "-nostdin", "-y", "-i", str(visual), "-i", str(audio), "-i", str(captions),
                         "-map", "0:v:0", "-map", "1:a:0", "-map", "2:0", "-c:v", "copy", "-c:a", "aac",
                         "-af", "apad", "-c:s", "mov_text", "-disposition:s:0", "default", "-t", "10",
                         "-movflags", "+faststart", str(candidate)])
            result = renderer.probe(candidate)
            _require(result.get("has_audio") is True and abs(result["duration"] - 10) <= 0.1,
                     "候选缺少声音或时长不符。")
            evidence = {"binding": binding, "candidate_sha256": digest(candidate), "raw_sha256": digest(raw),
                        "excerpt_sha256": digest(audio), "audio_tail_silence_seconds": 10 - excerpt["duration_seconds"]}
            _save(cached, evidence)
        output_binding = _hash(evidence)
        decision = read_review(folder / "output-review.json", output_binding, required_checks=OUTPUT_CHECKS)
        accepted_status = "fixture_accepted" if plan.get("test_fixture_only") is True else "accepted"
        return status(accepted_status if decision == "accepted" else "rejected" if decision == "rejected" else "awaiting_full_playback_review",
                      candidate=str(candidate), review_binding=output_binding, required_checks=list(OUTPUT_CHECKS))
    except Exception:
        _save(folder / "status.json", {"status": "interrupted_or_failed", "binding": binding,
                                    "final_visual_accepted": False, "notice": "先核对job.json，不能将状态当作自动重试许可。"})
        raise
    finally:
        lock.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="隔离10秒单场景执行，不上传正式产品")
    for name in ("plan", "recording", "reference", "work-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    client = ComfyUiClient("http://127.0.0.1:8188", args.plan, timeout_seconds=3600)
    print(json.dumps(execute(plan, recording=args.recording, reference=args.reference, work_dir=args.work_dir,
                             client=client, renderer=MediaRenderer(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)), ensure_ascii=False))


if __name__ == "__main__":
    main()
