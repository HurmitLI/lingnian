"""One local launch, bounded review waiting and automatic assembly.

This does not invent visual approvals, open a service, or interrupt other jobs.
The assistant inspecting each candidate supplies its hash-bound review.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time
from typing import Callable

from .comfyui import ComfyUiClient
from .film_assembly import assemble_film
from .film_executor import ContinuousFilmExecutor, _read, _save, execution_binding, read_review, scene_reference_status
from .media import MediaRenderer
from .models import ConfigurationError, PackageError, TemporaryWorkerError, WorkerError


def watch_film(executor: ContinuousFilmExecutor, plan: dict, *, reference: Path, narration: Path,
               max_seconds: float = 14400, poll_seconds: float = 10,
               clock: Callable = time.monotonic, sleep: Callable = time.sleep,
               assembler: Callable = assemble_film) -> dict:
    """Deadline/STOP are checked BETWEEN steps; an active Comfy job is not killed.

    Network/unknown submission errors stop with diagnostic status rather than
    blindly resubmit. A restart resumes journalled IDs; it never clears locks.
    """
    if (not math.isfinite(max_seconds) or not 1 <= max_seconds <= 43200
            or not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 60):
        raise ConfigurationError("等待时长应为 1–43200 秒，检查间隔不超过 60 秒。")
    binding = execution_binding(plan, executor.comfyui.base_url)
    folder = executor.work_dir / binding[:24]
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "watch.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TemporaryWorkerError("整片自动接续已启动；保留原锁，不启动第二个。") from exc
    deadline, pending = clock() + max_seconds, None

    def report(status: str, **fields) -> dict:
        result = {"status": status, "binding": binding, "job_dir": str(folder),
                  "final_visual_accepted": False, **fields}
        _save(folder / "watch-status.json", result)
        return result

    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        while True:
            if (executor.work_dir / "STOP").exists() or (folder / "STOP").exists():
                return report("stopped", reason="stop_file", active_gpu_task_cancelled=False)
            if clock() >= deadline:
                return report("paused", reason="watch_deadline", active_gpu_task_cancelled=False)
            if pending is not None:
                if pending["status"] in {"awaiting_scene_reference", "awaiting_scene_reference_review"}:
                    scene = next(s for s in plan["scenes"] if s["id"] == pending["scene_id"])
                    _, gate = scene_reference_status(scene, folder, binding)
                    waiting = gate is not None and gate["status"] != "scene_reference_rejected"
                    if gate and gate["status"] != pending["status"]:
                        pending = gate
                        report(gate["status"], scene_id=gate["scene_id"], review_binding=gate["review_binding"])
                else:
                    review_path = folder / f"shot-{pending['shot']:02d}" / f"attempt-{pending['attempt']}" / "review.json"
                    waiting = read_review(review_path, pending["review_binding"]) == "pending"
                if waiting:
                    sleep(min(poll_seconds, max(0, deadline - clock())))
                    continue
                pending = None
            report("running")
            result = executor.execute(plan, reference=reference, narration=narration)
            status = result.get("status")
            if status in {"awaiting_visual_review", "awaiting_scene_reference", "awaiting_scene_reference_review"}:
                pending = result
                report(status, shot=result["shot"], attempt=result["attempt"], review_binding=result["review_binding"],
                       **({"scene_id": result["scene_id"]} if "scene_id" in result else {}))
            elif status == "ready_for_assembly":
                # Respect STOP/deadline even if the previous generation overran.
                if (executor.work_dir / "STOP").exists() or (folder / "STOP").exists() or clock() >= deadline:
                    return report("paused", reason="assembly_not_started", active_gpu_task_cancelled=False)
                report("assembling")
                video = assembler(plan, job_dir=folder, reference=reference, narration=narration,
                                  renderer=executor.renderer, base_url=executor.comfyui.base_url)
                return report("awaiting_final_review", film=str(video))
            elif status in {"retry_limit_reached", "scene_reference_rejected"}:
                return report(status, shot=result["shot"], attempt=result["attempt"],
                              **({"scene_id": result["scene_id"]} if "scene_id" in result else {}))
            else:
                raise PackageError("执行器返回未知状态，保留断点，不自动重跑。")
    except WorkerError as exc:
        report("needs_diagnosis", error_code=exc.code, automatic_resubmission=False)
        raise
    except Exception:
        # Do not persist tracebacks or request bodies that could contain data.
        report("needs_diagnosis", error_code="UNEXPECTED_LOCAL_ERROR", automatic_resubmission=False)
        raise
    finally:
        lock.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="长片一次启动：等待助手复核、接续同一任务并合成；不自动发布")
    for name in ("plan", "reference", "narration", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--max-seconds", type=float, default=14400)
    args = parser.parse_args()
    client = ComfyUiClient("http://127.0.0.1:8188", args.plan, timeout_seconds=1800)
    try:
        executor = ContinuousFilmExecutor(renderer=MediaRenderer(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe),
                                          comfyui=client, work_dir=args.output)
        result = watch_film(executor, _read(args.plan), reference=args.reference, narration=args.narration,
                            max_seconds=args.max_seconds)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
