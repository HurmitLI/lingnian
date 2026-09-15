"""Opt-in native-10s cloud connector; never launches the legacy renderer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from .api import WorkerApi
from .comfyui import ComfyUiClient
from .media import MediaRenderer
from .models import PackageError, TemporaryWorkerError
from .package import decrypt_package
from .short_scene import execute, _save
from .short_scene_bundle import receive_bundle, TOTAL_CAP


PREFIX = "/api/v1/generation-worker/short-scene/tasks"


class ShortSceneApi(WorkerApi):
    def claim_short(self):
        task = self._json("POST", PREFIX + "/claim", json={"protocol": "native-short-scene-v1", **({"request_key": self.claim_request_key} if getattr(self, "claim_request_key", None) else {})})
        if task is None:
            return None
        if (not isinstance(task, dict) or task.get("protocol") != "native-short-scene-v1"
                or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", str(task.get("id", "")))
                or not re.fullmatch(r"[0-9a-f]{64}", str(task.get("package_sha256", "")))
                or not isinstance(task.get("lease_token"), str) or not 32 <= len(task["lease_token"]) <= 128
                or task.get("package_url") != PREFIX + "/" + task["id"] + "/package"):
            raise PackageError("短片领取响应不符合专用协议，未发送素材请求。")
        return task

    def short_progress(self, task, stage, percent):
        return self._json("PATCH", PREFIX + f"/{task['id']}/progress",
            headers={"X-Lingnian-Lease": task["lease_token"]}, json={"stage": stage, "percent": percent})

    def authorize_input(self, task, plan):
        value = self._json("GET", PREFIX + f"/{task['id']}/input-authorization",
            headers={"X-Lingnian-Lease": task["lease_token"]})
        return (isinstance(value, dict) and value.get("decision") == "accepted"
            and value.get("job_id") == task["id"] and value.get("package_sha256") == task["package_sha256"]
            and value.get("audio_sha256") == plan.get("recording", {}).get("sha256")
            and value.get("image_sha256") == plan.get("reference", {}).get("sha256")
            and value.get("selection_input_sha256") == plan.get("provenance", {}).get("selection_input_sha256")
            and value.get("full_playback_accepted") is False)

    def recover_short(self, task):
        return self._json("GET", PREFIX + f"/{task.get('id', task.get('job_id'))}/result")

    def receive(self, task, *, token, root):
        encrypted = bytearray()
        with self._client.stream("GET", task["package_url"], headers={"X-Lingnian-Lease": task["lease_token"]}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                encrypted.extend(chunk)
                if len(encrypted) > TOTAL_CAP + 128:
                    raise PackageError("加密制作包超过传输限制。")
        plain = decrypt_package(bytes(encrypted), token=token, request_id=task["id"])
        if hashlib.sha256(plain).hexdigest() != task["package_sha256"]:
            raise PackageError("制作包与领取时的摘要不一致。")
        path = root / "incoming.zip"
        try:
            with path.open("xb") as output:
                output.write(plain)
            path.chmod(0o600)
            return receive_bundle(path, expected_sha256=task["package_sha256"], output_dir=root / "inputs")
        finally:
            path.unlink(missing_ok=True)

    def short_result(self, task, candidate):
        if not candidate.is_file() or not 0 < candidate.stat().st_size <= 15 * 1024**2:
            raise PackageError("短片候选超出回传限制。")
        data = candidate.read_bytes()
        return self._json("POST", PREFIX + f"/{task['id']}/result",
            headers={"X-Lingnian-Lease": task["lease_token"], "X-Content-Sha256": hashlib.sha256(data).hexdigest()},
            files={"result": ("short-scene.mp4", data, "video/mp4")})


def run_once(config, api, *, runner=execute):
    workspace = config.work_dir / "native-short-scene"
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = workspace / "connector.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TemporaryWorkerError("短片连接器已有进程或中断锁；核对原任务，不并行领取。") from exc
    os.close(fd)
    task = None
    active_path = workspace / "active.json"
    request_key = str(uuid4())
    try:
        if active_path.exists():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            if active.get("job_id"):
                recovered = api.recover_short(active)
                if (recovered.get("id") == active["job_id"] and recovered.get("package_sha256") == active.get("package_sha256")
                        and recovered.get("status") == "awaiting_full_playback_review"
                        and active.get("result_sha256") and recovered.get("result_sha256") == active["result_sha256"]):
                    active_path.unlink()
                    return {"job_id":active["job_id"],"status":"awaiting_full_playback_review","visual_accepted":False}
                return {"job_id":active["job_id"],"status":"interrupted","automatic_retry":False}
            if not re.fullmatch(r"[a-zA-Z0-9-]{8,80}", str(active.get("request_key", ""))) or not isinstance(api, ShortSceneApi):
                return {"status":"claim_outcome_unknown","automatic_retry":False}
            request_key = active["request_key"]
        _save(active_path, {"status":"claiming","request_key":request_key,"automatic_retry":False})
        if isinstance(api, ShortSceneApi): api.claim_request_key = request_key
        task = api.claim_short()
        if task is None:
            active_path.unlink()
            return {"status": "idle", "gpu_submitted": False}
        active = {"job_id":task["id"],"package_sha256":task["package_sha256"],"request_key":request_key,"status":"receiving"}
        _save(active_path, active)
        root = workspace / task["id"]
        root.mkdir(exist_ok=True, mode=0o700)
        # Persist only non-secret identity; lease tokens remain in process memory.
        _save(root / "delivery.json", {"job_id": task["id"], "package_sha256": task["package_sha256"], "status": "receiving"})
        received = api.receive(task, token=config.node_token, root=root)
        plan_path = Path(received["paths"]["plan"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("test_fixture_only") is True or plan.get("recording", {}).get("kind") != "authorized_recording":
            raise PackageError("测试素材不能通过正式连接器提交GPU。")
        api.short_progress(task, "preparing", 5)
        last = [float("-inf")]
        def heartbeat():
            if time.monotonic() - last[0] >= 20:
                api.short_progress(task, "generating", 20)
                last[0] = time.monotonic()
        review_options = {"authorize_input": lambda current: api.authorize_input(task, current)} if isinstance(api, ShortSceneApi) else {}
        result = runner(plan, recording=Path(received["paths"]["recording"]), reference=Path(received["paths"]["reference"]),
            work_dir=root / "render", client=ComfyUiClient(config.comfyui_url, plan_path, timeout_seconds=config.comfy_timeout_seconds),
            renderer=MediaRenderer(ffmpeg=config.ffmpeg, ffprobe=config.ffprobe), on_generating=heartbeat, **review_options)
        if result["status"] == "awaiting_input_review":
            api.short_progress(task, "awaiting_input_review", 5)
        elif result["status"] in {"awaiting_full_playback_review", "accepted", "rejected"} and result.get("candidate"):
            # Previously cached candidates also need a valid generating lease before delivery.
            heartbeat()
            candidate = Path(result["candidate"]).resolve()
            if root.resolve() not in candidate.parents:
                raise PackageError("候选文件不在本任务目录。")
            if result["status"] == "rejected":
                api.short_progress(task, "failed", 99)
            else:
                active.update(status="uploading", result_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest())
                _save(active_path, active)
                try:
                    delivered = api.short_result(task, candidate)
                except Exception:
                    recovered = api.recover_short(task)
                    if (recovered.get("id") != task["id"] or recovered.get("package_sha256") != task["package_sha256"]
                            or recovered.get("status") != "awaiting_full_playback_review"
                            or recovered.get("result_sha256") != active["result_sha256"]):
                        raise TemporaryWorkerError("视频回传结果未确认，保留原候选，不重复生成。")
                else:
                    if isinstance(api, ShortSceneApi) and (not isinstance(delivered, dict) or delivered.get("id") != task["id"]
                            or delivered.get("status") != "awaiting_full_playback_review"):
                        raise TemporaryWorkerError("视频回传响应不符合原任务，保留证据。")
        else:
            api.short_progress(task, "failed", 5)
        state = {"job_id": task["id"], "status": result["status"], "visual_accepted": False}
        _save(root / "delivery.json", state)
        active_path.unlink()
        return state
    except Exception:
        if task:
            _save(workspace / task["id"] / "delivery.json", {"job_id": task["id"], "status": "interrupted", "automatic_retry": False})
        # Do not claim success, erase GPU journals, or automatically resubmit an uncertain task.
        raise
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="独立10秒任务连接器；不替换旧节点；无审核不提交GPU")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from .config import WorkerConfig
    config = WorkerConfig.from_env(once=args.once)
    api = ShortSceneApi(config.backend_url, config.node_token)
    try:
        while True:
            result = run_once(config, api)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once or result["status"] != "idle":
                # Pending review or uncertainty requires a concrete next action, not a fake busy loop.
                break
            time.sleep(config.poll_seconds)
    finally:
        api.close()


if __name__ == "__main__":
    main()
