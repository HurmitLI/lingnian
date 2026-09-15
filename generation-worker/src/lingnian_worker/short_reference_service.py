"""Opt-in reference-only connector. No video, installs, inferred review or unknown retries."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from uuid import UUID, uuid4

from .api import WorkerApi
from .comfyui import ComfyUiClient
from .models import PackageError, TemporaryWorkerError
from .package import decrypt_package
from .short_scene import _save, _require
from .short_scene_reference import prepare_reference
from .short_scene_reference_bundle import receive_reference_bundle, TOTAL_CAP

PREFIX = "/api/v1/generation-worker/short-reference/tasks"
HEX = r"[0-9a-f]{64}"


@dataclass(frozen=True)
class ReferenceConfig:
    backend_url: str
    node_token: str
    work_dir: Path
    comfyui_url: str
    comfy_timeout_seconds: int = 600
    poll_seconds: int = 8

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        import keyring
        from .config import KEYRING_SERVICE, KEYRING_USERNAME, _positive_int
        load_dotenv()
        backend = (os.getenv("LINGNIAN_BACKEND_URL") or os.getenv("LINGNIAN_API_BASE") or "").strip().rstrip("/")
        parsed = urlparse(backend)
        _require(parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment, "参考服务需要已配置的HTTPS后端地址。")
        token = os.getenv("LINGNIAN_NODE_TOKEN", "").strip() or keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or ""
        _require(len(token) >= 32, "没有找到已有节点密钥，不自动创建授权。")
        comfy = os.getenv("LINGNIAN_COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/")
        local = urlparse(comfy)
        _require(local.scheme in {"http", "https"} and local.hostname in {"127.0.0.1", "localhost", "::1"}
                 and not local.username and not local.password, "参考生成只调用节点本机ComfyUI。")
        return cls(backend, token, Path(os.getenv("LINGNIAN_WORK_DIR", ".worker-data")).expanduser().resolve(), comfy,
                   _positive_int("LINGNIAN_COMFY_TIMEOUT_SECONDS", 600), _positive_int("LINGNIAN_POLL_SECONDS", 8))


def valid_id(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


class ReferenceApi(WorkerApi):
    def claim_reference(self):
        task = self._json("POST", PREFIX + "/claim", json={"protocol": "short-reference-v1", **({"request_key": self.claim_request_key} if getattr(self, "claim_request_key", None) else {})})
        if task is None:
            return None
        _require(isinstance(task, dict) and valid_id(task.get("id"))
            and task.get("protocol") == "short-reference-v1" and task.get("purpose") == "short_scene_reference_generation"
            and all(isinstance(task.get(k), str) and re.fullmatch(HEX, task[k]) for k in ("package_sha256", "brief_sha256"))
            and isinstance(task.get("lease_token"), str) and 32 <= len(task["lease_token"]) <= 128
            and task.get("package_url") == PREFIX + f"/{task['id']}/package"
            and task.get("automatic_retry") is False and task.get("visual_accepted") is False,
            "参考领取协议不符，未发送后续素材请求。")
        return task

    def reference_progress(self, task, stage, percent):
        return self._json("PATCH", PREFIX + f"/{task['id']}/progress",
            headers={"X-Lingnian-Lease": task["lease_token"]}, json={"stage": stage, "percent": percent})

    def recover_reference(self, job_id):
        _require(valid_id(job_id), "参考任务编号不正确。")
        return self._json("GET", PREFIX + f"/{job_id}/result")

    def receive(self, task, *, token, root):
        encrypted = bytearray()
        with self._client.stream("GET", task["package_url"], headers={"X-Lingnian-Lease": task["lease_token"]}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                encrypted.extend(chunk)
                _require(len(encrypted) <= TOTAL_CAP + 128, "参考包超过传输限制。")
        plain = decrypt_package(bytes(encrypted), token=token, request_id=task["id"])
        _require(hashlib.sha256(plain).hexdigest() == task["package_sha256"], "参考包不是当前任务指定内容。")
        path = root / "incoming.zip"
        owns = False
        try:
            with path.open("xb") as output:
                owns = True; path.chmod(0o600); output.write(plain)
            return receive_reference_bundle(path, expected_sha256=task["package_sha256"],
                expected_brief_sha256=task["brief_sha256"], output_dir=root / "inputs")
        finally:
            if owns:
                path.unlink(missing_ok=True)

    def reference_result(self, task, image, report):
        _require(image.is_file() and not image.is_symlink() and 0 < image.stat().st_size <= 8 * 1024**2, "参考结果大小不符。")
        data = image.read_bytes()
        _require(hashlib.sha256(data).hexdigest() == report["image_sha256"], "参考图在回传前变化。")
        return self._json("POST", PREFIX + f"/{task['id']}/result",
            headers={"X-Lingnian-Lease": task["lease_token"]}, data={"evidence": json.dumps(report)},
            files={"result": ("reference.png", data, "image/png")})


def completed(value, job_id, brief_sha, report=None):
    return (isinstance(value, dict) and value.get("id") == job_id and value.get("brief_sha256") == brief_sha
        and value.get("status") == "awaiting_reference_review" and value.get("visual_accepted") is False
        and value.get("result_verified") == "bytes_and_binding_only" and valid_id(value.get("result_asset_id"))
        and isinstance(value.get("result_report"), dict) and value["result_report"].get("brief_sha256") == brief_sha
        and (report is None or value["result_report"] == report))


def run_once(config, api, *, runner=prepare_reference):
    workspace = config.work_dir / "short-reference"
    _require(not any(p.is_symlink() for p in (workspace, *workspace.parents)), "参考工作目录不能包含链接。")
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = workspace / "connector.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
    except FileExistsError as exc:
        raise TemporaryWorkerError("参考连接器已有执行锁；核对原进程，不重复领取。") from exc
    active_path = workspace / "active.json"
    task = None
    root = None
    owns_root = False
    request_key = str(uuid4())
    try:
        if active_path.exists() or active_path.is_symlink():
            _require(not active_path.is_symlink(), "参考恢复记录不能是链接。")
            active = json.loads(active_path.read_text(encoding="utf-8"))
            if not valid_id(active.get("job_id")):
                if not valid_id(active.get("request_key")) or not isinstance(api, ReferenceApi):
                    return {"status": "claim_outcome_unknown", "automatic_retry": False}
                request_key = active["request_key"]
            else:
                recovered = api.recover_reference(active["job_id"])
                if completed(recovered, active["job_id"], active["brief_sha256"], active.get("result_report")):
                    state = {"job_id": active["job_id"], "status": "awaiting_reference_review", "visual_accepted": False}
                    prior_root = workspace / active["job_id"]
                    _require(prior_root.is_dir() and not prior_root.is_symlink(), "原任务目录缺失，先保留恢复记录。")
                    _save(prior_root / "delivery.json", {**active, **state})
                    active_path.unlink()
                    return state
                return {"job_id": active["job_id"], "status": "interrupted", "automatic_retry": False}
        # A lost claim response may replay only this persisted request key.
        _save(active_path, {"status": "claiming", "request_key": request_key, "automatic_retry": False})
        if isinstance(api, ReferenceApi): api.claim_request_key = request_key
        task = api.claim_reference()
        if task is None:
            active_path.unlink()
            return {"status": "idle", "gpu_submitted": False}
        root = workspace / task["id"]
        active = {"request_key": request_key, "job_id": task["id"], "brief_sha256": task["brief_sha256"], "package_sha256": task["package_sha256"],
                  "status": "receiving", "automatic_retry": False}
        _save(active_path, active)
        _require(not root.exists() and not root.is_symlink(), "原参考任务已有目录，不能覆盖或重新执行。")
        root.mkdir(mode=0o700); owns_root = True
        _save(root / "delivery.json", active)
        received = api.receive(task, token=config.node_token, root=root)
        brief_path = Path(received["brief_path"])
        envelope = json.loads(brief_path.read_text(encoding="utf-8"))
        api.reference_progress(task, "preparing", 5)
        last = [float("-inf")]
        def heartbeat():
            if time.monotonic() - last[0] >= 20:
                api.reference_progress(task, "generating", 20)
                last[0] = time.monotonic()
        comfy = ComfyUiClient(config.comfyui_url, brief_path, timeout_seconds=config.comfy_timeout_seconds)
        try:
            receipt = runner(envelope, photo=Path(received["photo_path"]) if received["photo_path"] else None,
                work_dir=root / "render", client=comfy, authorized_brief_sha256=task["brief_sha256"], on_generating=heartbeat)
        finally:
            comfy.close()
        heartbeat()
        image = Path(receipt["reference_path"])
        _require(not any(p.is_symlink() for p in (image, *image.parents)) and (root / "render").resolve() in image.resolve().parents,
                 "参考结果不在本任务生成目录。")
        journal_path, graph_path = image.parent / "job.json", image.parent / "actual-api.json"
        _require(all(p.is_file() and not p.is_symlink() and p.stat().st_size <= 2 * 1024**2 for p in (journal_path, graph_path)),
                 "参考日志或工作流不是本任务的普通证据文件。")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        _require(receipt.get("brief_sha256") == task["brief_sha256"] and receipt.get("visual_accepted") is False
            and receipt.get("generation_ready") is False and journal.get("stage") == "downloaded"
            and valid_id(journal.get("prompt_id")) and journal.get("output_sha256") == receipt.get("sha256"),
            "参考产物缺少当前任务的生成日志，不能上传。")
        report = {"brief_sha256": task["brief_sha256"], "image_sha256": receipt["sha256"], "reference_binding": receipt["binding"],
            "graph_sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest(), "prompt_id": journal["prompt_id"]}
        active.update(status="uploading", result_report=report)
        _save(active_path, active); _save(root / "delivery.json", active)
        try:
            result = api.reference_result(task, image, report)
        except Exception:
            # Read-only recovery only. Never generate again or change upload identity.
            result = api.recover_reference(task["id"])
        _require(completed(result, task["id"], task["brief_sha256"], report), "参考回传结果尚未确认，保留原图和原任务。")
        state = {"job_id": task["id"], "status": "awaiting_reference_review", "visual_accepted": False}
        _save(root / "delivery.json", {**active, **state}); active_path.unlink()
        return state
    except Exception:
        # Leave non-secret active evidence intact. No lease/node tokens go to disk.
        if owns_root and root is not None and root.is_dir() and not root.is_symlink():
            _save(root / "interrupted.json", {"status": "interrupted", "automatic_retry": False})
        raise
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="独立参考图连接器；不启动视频或安装模型")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = ReferenceConfig.from_env()
    api = ReferenceApi(config.backend_url, config.node_token)
    try:
        while True:
            state = run_once(config, api)
            print(json.dumps(state, ensure_ascii=False), flush=True)
            if state["status"] not in {"idle", "awaiting_reference_review"}:
                raise SystemExit(1)
            if args.once:
                break
            time.sleep(config.poll_seconds)
    except Exception:
        print(json.dumps({"status": "interrupted", "automatic_retry": False}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    finally:
        api.close()


if __name__ == "__main__":
    main()
