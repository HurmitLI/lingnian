"""Local resumable long-film execution. Reviews are evidence, not UI approvals.

The orchestrating assistant can inspect media and write hash-bound review files.
Absent or rejected reviews never become accepted just because generation worked.
No cloud jobs, credentials, installation or publication are performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, UnidentifiedImageError

from .comfyui import ComfyUiClient
from .continuity import build_wan_graph, contact_sheet, digest, normalize_segment, tail_frame
from .film_contract import film_contract_digest, render_plan_for_shot, scene_for_shot
from .media import MediaRenderer
from .models import ConfigurationError, PackageError, TemporaryWorkerError, WorkerError


REVIEW_CHECKS = ("identity", "wardrobe_props", "story_action", "camera_space", "motion", "seam")
SCENE_CHECKS = ("identity", "wardrobe_props", "story_context", "era_location", "composition")


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def execution_binding(plan: dict, base_url: str) -> str:
    plan_hash = film_contract_digest(plan)
    if plan["version"] == 2:
        graph_version = _hash({"graphs": [build_wan_graph(render_plan_for_shot(plan, s["id"]), s, image_name="version", prefix="version")
                                         for s in plan["segments"]]})
    else:
        graph_version = _hash(build_wan_graph(plan, plan["segments"][0], image_name="version", prefix="version"))
    return _hash({"plan": plan_hash, "graph_version": graph_version, "server": base_url})


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError) as exc:
        raise PackageError("任务或复核记录损坏；保留现场，不自动覆盖重跑。") from exc


def _save(path: Path, value: dict) -> None:
    ComfyUiClient._save_journal(path, value)


def read_review(path: Path, binding: str, *, required_checks=REVIEW_CHECKS) -> str:
    if not path.exists():
        return "pending"
    value = _read(path)
    if value.get("binding") != binding:
        raise PackageError("复核记录对应其他镜头或旧文件，不能套用。")
    if value.get("reviewer") not in {"assistant_visual", "human_visual"} or not isinstance(value.get("notes"), str) or not value["notes"].strip():
        raise PackageError("复核缺少实际检查者或检查说明。")
    decision = value.get("decision")
    checks = value.get("checks")
    if decision == "accepted" and isinstance(checks, dict) and all(checks.get(k) is True for k in required_checks):
        return "accepted"
    if decision == "rejected":
        return "rejected"
    raise PackageError("没有完整的视觉复核依据，不自动通过。")


def scene_reference_status(scene: dict, job_dir: Path, binding: str) -> tuple[Path, dict | None]:
    folder = job_dir / "scene-references" / scene["id"]
    image = folder / "reference.png"
    review_binding = _hash({"job": binding, "scene": scene})
    status = {"binding": binding, "job_dir": str(job_dir), "scene_id": scene["id"],
              "review_binding": review_binding, "shot": scene["start_shot"], "attempt": 0,
              "required_opening_state": scene["opening_state"]}
    if not image.is_file():
        return image, {**status, "status": "awaiting_scene_reference"}
    if digest(image) != scene["anchor_sha256"]:
        raise PackageError("场景参考图摘要不符，不用其他图片顶替。")
    try:
        with Image.open(image) as picture:
            if picture.format != "PNG" or not all(64 <= n <= 4096 for n in picture.size):
                raise PackageError("场景参考应为 64–4096 像素的 PNG 图片。")
            picture.verify()
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError) as exc:
        raise PackageError("场景参考图无法验证。") from exc
    decision = read_review(folder / "review.json", review_binding, required_checks=SCENE_CHECKS)
    if decision == "accepted":
        # An attractive/identity-consistent image can still show the wrong
        # opening state (e.g. open bag when the shot requires it closed).
        # This records an actual review, not an automated image-state detector.
        evidence = _read(folder / "review.json").get("opening_state_evidence")
        if (not isinstance(evidence, dict) or evidence.get("expected") != scene["opening_state"]
                or not isinstance(evidence.get("observed"), str) or not evidence["observed"].strip()
                or type(evidence.get("matches")) is not bool):
            return image, {**status, "status": "awaiting_scene_reference_review",
                           "reason": "缺少针对当前开场状态的实际目视记录；保留旧记录，不能自动补写通过。"}
        if evidence["matches"] is False:
            return image, {**status, "status": "scene_reference_rejected",
                           "reason": "参考图实际状态与镜头开场要求不一致，不能靠后续提示词覆盖。"}
        return image, None
    return image, {**status, "status": "awaiting_scene_reference_review" if decision == "pending" else "scene_reference_rejected"}


def stage_scene_reference(plan: dict, *, scene_id: str, image: Path, work_dir: Path,
                          base_url: str = "http://127.0.0.1:8188") -> dict:
    """Import an already generated/authorized scene image, never create approval.

    No upload or GPU call; do not overwrite a different existing candidate or
    silently grant source-photo authorization by receiving a file.
    """
    binding = execution_binding(plan, base_url)
    if plan["version"] != 2:
        raise PackageError("只有第二版整片合同支持场景参考导入。")
    scene = next((s for s in plan["scenes"] if s["id"] == scene_id), None)
    if scene is None or not image.is_file() or digest(image) != scene["anchor_sha256"]:
        raise PackageError("场景编号或图片摘要与合同不匹配。")
    folder = work_dir / binding[:24]
    target = folder / "scene-references" / scene_id / "reference.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _, gate = scene_reference_status(scene, folder, binding)
        return gate or {"status": "scene_reference_ready", "binding": binding, "scene_id": scene_id, "job_dir": str(folder)}
    # Validate in a temporary scoped directory before publishing into the job.
    import tempfile
    staging = Path(tempfile.mkdtemp(prefix=".reference-", dir=folder))
    candidate = staging / "scene-references" / scene_id / "reference.png"
    candidate.parent.mkdir(parents=True)
    shutil.copyfile(image, candidate)
    scene_reference_status(scene, staging, binding)
    try:
        os.link(candidate, target)
    except FileExistsError as exc:
        raise PackageError("场景参考已由另一进程写入；保留临时文件，不覆盖。") from exc
    _, gate = scene_reference_status(scene, folder, binding)
    return gate or {"status": "scene_reference_ready", "binding": binding, "scene_id": scene_id, "job_dir": str(folder)}


class ContinuousFilmExecutor:
    """Generate up to the next review gate; resume idempotently after review.

    A maximum of three candidates per shot is enforced. This bounds generation,
    not review quality. Real visual inspection is still required for each gate.
    """

    def __init__(self, *, renderer: MediaRenderer, comfyui: ComfyUiClient, work_dir: Path):
        self.renderer, self.comfyui, self.work_dir = renderer, comfyui, work_dir

    def execute(self, plan: dict, *, reference: Path, narration: Path) -> dict:
        film_contract_digest(plan)
        if urlparse(self.comfyui.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ConfigurationError("隔离长片执行器仅连接本机 ComfyUI。")
        for path, expected in ((reference, plan["reference"]["sha256"]), (narration, plan["narration"]["sha256"])):
            if not path.is_file() or digest(path) != expected:
                raise PackageError("真实参考图或旁白与方案摘要不符。")
        audio_meta = self.renderer.probe(narration)
        audio_seconds = audio_meta.get("audio_duration")
        if (not audio_meta.get("has_audio") or type(audio_seconds) not in (int, float)
                or not math.isfinite(audio_seconds)
                or abs(audio_seconds - plan["narration"]["duration_seconds"]) > 0.1):
            raise PackageError("旁白缺少真实音轨或实际时长不符。")
        binding = execution_binding(plan, self.comfyui.base_url)
        job_dir = self.work_dir / binding[:24]
        job_dir.mkdir(parents=True, exist_ok=True)
        lock = job_dir / "execution.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise TemporaryWorkerError("整片已有执行锁；核对进程后处理，不重复提交。") from exc
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(str(os.getpid()))
            return self._run(plan, reference, job_dir, binding)
        except WorkerError as exc:
            _save(job_dir / "status.json", {"status": "failed", "binding": binding, "error_code": exc.code})
            raise
        finally:
            lock.unlink(missing_ok=True)

    def _run(self, plan: dict, reference: Path, job_dir: Path, binding: str) -> dict:
        checkpoint = job_dir / "checkpoint.json"
        state = _read(checkpoint) if checkpoint.exists() else {"version": 1, "binding": binding, "shots": []}
        if state.get("binding") != binding or not isinstance(state.get("shots"), list):
            raise PackageError("整片断点不匹配，不能复用。")
        if len(state["shots"]) > len(plan["segments"]):
            raise PackageError("整片断点超出镜头数。")
        source = reference
        clips = []
        for number, segment in enumerate(plan["segments"], 1):
            scene = scene_for_shot(plan, number)
            if scene and number == scene["start_shot"]:
                source, gate = scene_reference_status(scene, job_dir, binding)
                if gate:
                    _save(job_dir / "status.json", gate)
                    return gate
            if len(state["shots"]) < number:
                state["shots"].append({"id": number, "attempts": []})
                _save(checkpoint, state)
            shot = state["shots"][number - 1]
            if not isinstance(shot, dict) or shot.get("id") != number or not isinstance(shot.get("attempts"), list) or len(shot["attempts"]) > 3:
                raise PackageError("镜头重试记录损坏。")
            accepted = None
            for attempt in range(1, 4):
                folder = job_dir / f"shot-{number:02d}" / f"attempt-{attempt}"
                folder.mkdir(parents=True, exist_ok=True)
                # Content-addressed upload names avoid overwriting another job's anchor.
                anchor = folder / f"anchor-{digest(source)}{source.suffix.lower()}"
                if anchor.exists() and digest(anchor) != digest(source):
                    raise PackageError("已保存的参考图损坏，不能继续。")
                if not anchor.exists():
                    shutil.copyfile(source, anchor)
                raw, clip, tail = (folder / name for name in ("raw.mp4", "clip.mp4", "tail.png"))
                entry = shot["attempts"][attempt - 1] if len(shot["attempts"]) >= attempt else None
                if entry is not None:
                    self._verify_entry(entry, anchor, raw, clip, tail)
                else:
                    image_name = self.comfyui.upload_image(anchor)
                    graph = build_wan_graph(render_plan_for_shot(plan, number), segment, image_name=image_name,
                                            prefix=f"lingnian-film/{binding[:16]}-{number}-{attempt}")
                    graph["9"]["inputs"]["seed"] += 10000 * (attempt - 1)
                    _save(job_dir / "status.json", {"status": "generating", "shot": number, "attempt": attempt, "binding": binding})
                    raw_result = self.comfyui.run_workflow(graph, output_path=raw, journal_path=folder / "comfy-task.json")
                    if raw_result != raw:
                        raise PackageError("长片镜头没有返回预期的 MP4。")
                    normalize_segment(self.renderer, raw, clip, segment["duration_seconds"])
                    tail_frame(self.renderer, raw, tail, segment["duration_seconds"])
                    contact_sheet(self.renderer, raw, folder / "review.png", segment["duration_seconds"])
                    entry = {"input_sha256": digest(anchor), "raw_sha256": digest(raw), "clip_sha256": digest(clip), "tail_sha256": digest(tail)}
                    entry["review_binding"] = _hash({"job": binding, "shot": number, "attempt": attempt, **entry})
                    shot["attempts"].append(entry)
                    _save(checkpoint, state)
                # Required keys are recomputed, so modifying checkpoint alone cannot
                # attach an old review to a different candidate.
                expected_review = _hash({"job": binding, "shot": number, "attempt": attempt,
                                        **{k: entry[k] for k in ("input_sha256", "raw_sha256", "clip_sha256", "tail_sha256")}})
                if entry.get("review_binding") != expected_review:
                    raise PackageError("镜头复核绑定损坏。")
                decision = read_review(folder / "review.json", expected_review)
                if decision == "pending":
                    return self._status(job_dir, "awaiting_visual_review", binding, number, attempt, expected_review)
                if decision == "accepted":
                    accepted = (clip, tail)
                    break
            if accepted is None:
                return self._status(job_dir, "retry_limit_reached", binding, number, 3)
            clips.append(str(accepted[0].relative_to(job_dir)))
            source = accepted[1]
        result = self._status(job_dir, "ready_for_assembly", binding, len(clips), 0)
        result.update(clips=clips, duration_seconds=plan["duration_seconds"], final_visual_accepted=False,
                      narration_sha256=plan["narration"]["sha256"])
        _save(job_dir / "status.json", result)
        return result

    @staticmethod
    def _verify_entry(entry: dict, anchor: Path, raw: Path, clip: Path, tail: Path) -> None:
        if not isinstance(entry, dict):
            raise PackageError("镜头记录损坏。")
        for path, key in ((anchor, "input_sha256"), (raw, "raw_sha256"), (clip, "clip_sha256"), (tail, "tail_sha256")):
            if not path.is_file() or entry.get(key) != digest(path):
                raise PackageError("已生成镜头或接续尾帧摘要改变，不延长损坏链。")

    @staticmethod
    def _status(folder: Path, status: str, binding: str, shot: int, attempt: int, review_binding: str | None = None) -> dict:
        result = {"status": status, "binding": binding, "shot": shot, "attempt": attempt, "job_dir": str(folder)}
        if review_binding:
            result["review_binding"] = review_binding
        _save(folder / "status.json", result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="本机长片生成至下一视觉复核点，不发布云端")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--narration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    args = parser.parse_args()
    client = ComfyUiClient("http://127.0.0.1:8188", args.plan, timeout_seconds=3600)
    try:
        executor = ContinuousFilmExecutor(renderer=MediaRenderer(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe), comfyui=client, work_dir=args.output)
        print(json.dumps(executor.execute(_read(args.plan), reference=args.reference, narration=args.narration), ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
