"""Persistent, single-lane connector for reviewed short-reference and video jobs.

The service performs read-only queue checks before every new claim. Recovery of
an already claimed task stays inside the dedicated connector, which never
creates a second GPU submission when the prior outcome is unknown.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import WorkerConfig
from .models import ConfigurationError, TemporaryWorkerError
from .short_reference_service import ReferenceApi, ReferenceConfig, run_once as run_reference_once
from .short_scene_service import ShortSceneApi, run_once as run_short_once


def gpu_queue_state(comfyui_url: str) -> str:
    parsed = urlparse(comfyui_url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ConfigurationError("自动领取器只检查节点本机 ComfyUI。")
    try:
        response = httpx.get(comfyui_url.rstrip("/") + "/queue", timeout=10)
        response.raise_for_status()
        value = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TemporaryWorkerError("ComfyUI 暂未就绪，尚未领取任务。") from exc
    if not isinstance(value, dict) or any(not isinstance(value.get(key), list) for key in ("queue_running", "queue_pending")):
        raise TemporaryWorkerError("不能确认显卡队列，尚未领取任务。")
    return "busy" if value["queue_running"] or value["queue_pending"] else "idle"


def _active(work_dir: Path, folder: str) -> bool:
    return (work_dir / folder / "active.json").is_file()


def run_cycle(config, reference_api, short_api, *, queue_probe=gpu_queue_state,
              reference_runner=None, short_runner=None) -> dict:
    # Recovery must run before a fresh queue check; it only inspects the exact
    # persisted task unless the original connector proves that no task exists.
    if _active(config.work_dir, "short-reference"):
        kwargs = {"runner": reference_runner} if reference_runner else {}
        result = run_reference_once(config, reference_api, **kwargs)
        return {"lane": "reference", **result}
    if _active(config.work_dir, "native-short-scene"):
        kwargs = {"runner": short_runner} if short_runner else {}
        result = run_short_once(config, short_api, **kwargs)
        return {"lane": "video", **result}
    if queue_probe(config.comfyui_url) != "idle":
        return {"lane": "none", "status": "gpu_busy", "gpu_submitted": False}
    kwargs = {"runner": reference_runner} if reference_runner else {}
    result = run_reference_once(config, reference_api, **kwargs)
    if result["status"] != "idle":
        return {"lane": "reference", **result}
    if queue_probe(config.comfyui_url) != "idle":
        return {"lane": "none", "status": "gpu_busy", "gpu_submitted": False}
    kwargs = {"runner": short_runner} if short_runner else {}
    result = run_short_once(config, short_api, **kwargs)
    return {"lane": "video", **result}


def _compatible(video: WorkerConfig, reference: ReferenceConfig) -> None:
    if any((video.backend_url != reference.backend_url, video.node_token != reference.node_token,
            video.work_dir != reference.work_dir, video.comfyui_url != reference.comfyui_url)):
        raise ConfigurationError("参考图和短片连接器配置不一致，不能自动领取。")


def main() -> None:
    parser = argparse.ArgumentParser(description="单通道自动领取参考图与10秒短片任务")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    video = WorkerConfig.from_env(once=args.once)
    reference = ReferenceConfig.from_env()
    _compatible(video, reference)
    reference_api = ReferenceApi(video.backend_url, video.node_token)
    short_api = ShortSceneApi(video.backend_url, video.node_token)
    try:
        while True:
            try:
                result = run_cycle(video, reference_api, short_api)
            except TemporaryWorkerError as exc:
                # A pure pre-claim outage has no active task and is safe to wait
                # through. Any persisted active task requires operator review.
                if _active(video.work_dir, "short-reference") or _active(video.work_dir, "native-short-scene"):
                    raise
                result = {"lane": "none", "status": "node_unavailable", "gpu_submitted": False,
                          "message": str(exc)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                break
            time.sleep(video.poll_seconds)
    finally:
        reference_api.close()
        short_api.close()


if __name__ == "__main__":
    main()
