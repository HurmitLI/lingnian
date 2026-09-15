"""Bounded local file inbox for the isolated film workflow.

No listener, shell, credential loading, installation, queue clearing or service
changes. Requests are sent by the existing authorized UU file transfer. This is
not a general command execution endpoint. Interrupted claims never auto-replay.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import httpx

from .film_executor import _save
from .models import PackageError


def read_request(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise PackageError("Invalid local request file")
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise PackageError("Invalid request JSON") from exc
    if not isinstance(request, dict) or set(request) - {"id", "operation", "project"}:
        raise PackageError("Only fixed film operations are supported")
    identifier = request.get("id")
    if not isinstance(identifier, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", identifier) is None or path.stem != identifier:
        raise PackageError("Request ID must match its safe filename")
    if request.get("operation") not in {"snapshot", "continue_v5", "film"}:
        raise PackageError("Unknown operation; never run arbitrary commands")
    if request["operation"] == "film":
        if not isinstance(request.get("project"), str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", request["project"]) is None:
            raise PackageError("Film project must be an inbox folder name")
    elif "project" in request:
        raise PackageError("Unexpected project argument")
    return request


def snapshot() -> dict:
    # Never save raw queue graphs, prompts, interview text, or environment.
    with httpx.Client(base_url="http://127.0.0.1:8188", timeout=15, trust_env=False) as client:
        queue_response = client.get("/queue")
        queue_response.raise_for_status()
        queue = queue_response.json()
        response = client.get("/object_info")
        response.raise_for_status()
        nodes = response.json()
    models = {}
    for node, field in (("UNETLoader", "unet_name"), ("CheckpointLoaderSimple", "ckpt_name"),
                        ("CLIPLoader", "clip_name"), ("VAELoader", "vae_name"), ("LoraLoader", "lora_name")):
        field_info = nodes.get(node, {}).get("input", {}).get("required", {}).get(field, [])
        values = field_info[0] if field_info and isinstance(field_info[0], list) else []
        models[node] = [name[:256] for name in values[:200] if isinstance(name, str)]
    return {"status": "completed", "operation": "snapshot", "queue_running": len(queue.get("queue_running", [])),
            "queue_pending": len(queue.get("queue_pending", [])), "models": models,
            "available_nodes": sorted(str(key)[:160] for key in nodes)[:1500],
            "ffmpeg_available": bool(shutil.which("ffmpeg")), "ffprobe_available": bool(shutil.which("ffprobe"))}


def command_for(request: dict, root: Path, remaining: float) -> list[str]:
    if request["operation"] == "continue_v5":
        script = root.parent / "lingnian-film-batch-0906/continue_v5_reviewed.py"
        if not script.is_file() or remaining < 3700:
            raise PackageError("Existing continuation package missing or too little safe execution time")
        return [sys.executable, "-u", str(script)]
    if request["operation"] != "film" or remaining < 1900:
        raise PackageError("No film operation or too little time remaining")
    inputs = (root / "inputs").resolve()
    project = inputs / request["project"]
    for name in ("plan.json", "reference.png", "narration.wav"):
        path = project / name
        if not path.is_file() or not path.resolve().is_relative_to(inputs):
            raise PackageError("Film inputs missing or escaping the scoped inbox")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise PackageError("Existing FFmpeg tools unavailable; nothing installed")
    return [sys.executable, "-u", "-m", "lingnian_worker.film_watch", "--plan", str(project / "plan.json"),
            "--reference", str(project / "reference.png"), "--narration", str(project / "narration.wav"),
            "--output", str(root / "film-jobs"), "--ffmpeg", ffmpeg, "--ffprobe", ffprobe,
            "--max-seconds", str(int(remaining - 1800))]


def serve(root: Path, *, seconds: int = 14400, interval: int = 5) -> None:
    if not 60 <= seconds <= 43200 or not 1 <= interval <= 60:
        raise PackageError("Local bridge duration or interval out of bounds")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("requests", "claims", "responses", "inputs"):
        (root / name).mkdir(exist_ok=True)
    lock = root / "bridge.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    deadline, active, output_log = time.monotonic() + seconds, None, None
    active_id = None
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        while time.monotonic() < deadline:
            if active is not None:
                code = active.poll()
                if code is not None:
                    output_log.close()
                    _save(root / "responses" / f"{active_id}.json", {"status": "process_exited", "exit_code": code,
                          "visual_accepted": False, "note": "Inspect film status and media; process exit is not film acceptance"})
                    active, output_log, active_id = None, None, None
            stopped = (root / "STOP").exists()
            _save(root / "bridge-status.json", {"status": "draining" if stopped and active else "running",
                  "active_id": active_id, "active_pid": active.pid if active else None,
                  "remaining_seconds": max(0, int(deadline - time.monotonic()))})
            if stopped and active is None:
                break
            if not stopped and active is None:
                for path in sorted((root / "requests").glob("*.json"))[:100]:
                    claim = root / "claims" / (path.stem + ".started")
                    if claim.exists() or (root / "responses" / path.name).exists():
                        continue
                    try:
                        request = read_request(path)
                        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.close(fd)
                        if request["operation"] == "snapshot":
                            _save(root / "responses" / path.name, snapshot())
                            continue
                        command = command_for(request, root, deadline - time.monotonic())
                        output_log = (root / "responses" / (path.stem + ".log")).open("x", encoding="utf-8")
                        active = subprocess.Popen(command, cwd=root, stdout=output_log, stderr=subprocess.STDOUT,
                                                  stdin=subprocess.DEVNULL, shell=False)
                        active_id = request["id"]
                        _save(root / "responses" / path.name, {"status": "running", "pid": active.pid,
                              "operation": request["operation"], "visual_accepted": False})
                        break
                    except Exception as exc:
                        if output_log is not None and active is None:
                            output_log.close()
                            output_log = None
                        # No exception text: could contain request bodies or credentials.
                        _save(root / "responses" / path.name, {"status": "needs_diagnosis", "error_type": type(exc).__name__,
                              "automatic_replay": False})
                        if active is not None:
                            break
            time.sleep(interval)
        _save(root / "bridge-status.json", {"status": "detached_active_job" if active else "stopped",
              "active_id": active_id, "active_pid": active.pid if active else None, "gpu_task_cancelled": False})
    finally:
        if output_log is not None:
            output_log.close()
        # If a child outlives this bounded loop, keep the bridge lock; no second
        # bridge may overlap it until its PID and journal have been reconciled.
        if active is None or active.poll() is not None:
            lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="限时本地文件任务入口，无监听端口、无任意命令")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=14400)
    args = parser.parse_args()
    serve(args.root.resolve(), seconds=args.seconds)


if __name__ == "__main__":
    main()
