from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image

from . import __version__
from .cloud import CloudClient, WorkerTask, decrypt_package, safe_extract
from .core import ComfyClient, Store


class ProductionWorker:
    def __init__(self, *, api_base: str, token: str, root: Path, comfy_root: Path, poll_seconds: int = 8, cloud: CloudClient | None = None):
        self.token = token
        self.root = root
        self.comfy_root = comfy_root
        root.mkdir(parents=True, exist_ok=True)
        self.cloud = cloud or CloudClient(api_base, token)
        self.comfy = ComfyClient("http://127.0.0.1:8188", 900)
        self.store = Store(root / "production.db")
        self.poll_seconds = poll_seconds
        self.capabilities = ["photo_restore"]
        self.status_path = root / "status.json"
        self.log_path = root / "worker.jsonl"
        self._status("idle", "空闲")

    def _log(self, event: str, *, task_id: str | None = None, detail: str | None = None) -> None:
        payload = {"time": time.time(), "event": event, "task_id": task_id}
        if detail:
            payload["detail"] = detail.replace(self.token, "***")[:500]
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _status(self, code: str, label: str, *, task_id: str | None = None, error: str | None = None) -> None:
        payload = {"status": code, "label": label, "task_id": task_id, "updated_at": time.time()}
        if error:
            payload["error"] = error[:300]
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.status_path)

    def heartbeat(self):
        stats = self.comfy.health()
        device = stats["devices"][0]
        summary = f"{device['name']} / {round(device['vram_total'] / 1073741824, 1)}GB / {socket.gethostname()}"
        beat = self.cloud.heartbeat(software_version=__version__, device_summary=summary, capabilities=self.capabilities)
        self.poll_seconds = max(3, min(60, beat.poll_interval_seconds))
        return beat

    def once(self) -> bool:
        self._status("claiming", "领取任务")
        task = self.cloud.claim()
        if task is None:
            self._status("idle", "空闲")
            return False
        if self.store.status(task.id) == "succeeded":
            self.cloud.fail(task, "WORKFLOW_FAILED", "节点发现重复的已完成任务，需要人工核对。", False)
            return False
        self._process(task)
        return True

    def _process(self, task: WorkerTask) -> None:
        work = Path(tempfile.mkdtemp(prefix=f"lingnian-worker-{task.id[:12]}-", dir=self.root))
        try:
            self._log("task_claimed", task_id=task.id)
            self.store.set(task.id, "downloading")
            self._status("downloading", "下载素材", task_id=task.id)
            encrypted = self.cloud.download_package(task, work / "package.lnpkg")
            package = decrypt_package(encrypted, work / "package.zip", worker_token=self.token, request_id=task.id)
            manifest = safe_extract(package, work / "package")
            if manifest["generation_type"] != task.generation_type:
                raise ValueError("任务类型与素材包不一致。")
            self.cloud.progress(task, 10, "素材已安全下载并校验")
            self.store.set(task.id, "generating")
            self._status("generating", "生成中", task_id=task.id)
            result = self._generate(task, manifest, work / "package")
            self._log("image_ready", task_id=task.id, detail=str(result))
            self.cloud.progress(task, 90, "生成完成，正在校验结果")
            self.store.set(task.id, "uploading")
            self._status("uploading", "上传中", task_id=task.id)
            self._upload_with_retry(task, result)
            self.store.set(task.id, "succeeded")
            self._status("succeeded", "成功", task_id=task.id)
            self._log("task_succeeded", task_id=task.id, detail=str(result))
        except httpx.HTTPStatusError as exc:
            self.store.set(task.id, "failed", f"HTTP {exc.response.status_code}")
            self._log("cloud_http_error", task_id=task.id, detail=f"status={exc.response.status_code} body={exc.response.text[:300]}")
            self._status("failed", "失败", task_id=task.id, error="云端拒绝了任务状态，节点将继续安全重试。")
            if exc.response.status_code not in {404, 409}:
                self._safe_fail(task, "RESULT_UPLOAD_FAILED", "结果上传暂时失败。", True)
        except Exception as exc:
            code = "MODEL_MISSING" if isinstance(exc, FileNotFoundError) else "WORKFLOW_FAILED"
            message = "本地缺少任务所需模型。" if code == "MODEL_MISSING" else "本地工作流没有完成。"
            self.store.set(task.id, "failed", type(exc).__name__)
            self._log("task_error", task_id=task.id, detail=f"{type(exc).__name__}: {exc}")
            self._status("failed", "失败", task_id=task.id, error=message)
            self._safe_fail(task, code, message, code != "MODEL_MISSING")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _safe_fail(self, task: WorkerTask, code: str, message: str, retryable: bool) -> None:
        try:
            self.cloud.fail(task, code, message, retryable)
        except Exception:
            pass

    def _upload_with_retry(self, task: WorkerTask, result: Path) -> None:
        for attempt in range(1, 6):
            try:
                self.cloud.upload_result(task, result, actual_cost_cents=0)
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 or attempt == 5:
                    raise
                self._log("result_upload_retry", task_id=task.id, detail=f"attempt={attempt} status={exc.response.status_code}")
                self.cloud.progress(task, 95, "结果上传暂时中断，正在自动重试")
                time.sleep(min(30, 2**attempt))

    def _generate(self, task: WorkerTask, manifest: dict, package_root: Path) -> Path:
        if task.generation_type != "photo_restore":
            raise FileNotFoundError("当前节点尚未安装已授权视频模型。")
        media = next((item for item in manifest["media"] if item.get("mime_type", "").startswith("image/")), None)
        if not media:
            raise ValueError("修复任务缺少照片。")
        source = package_root.joinpath(*media["path"].split("/"))
        with Image.open(source) as source_image:
            source_width, source_height = source_image.size
        if source_width <= 0 or source_height <= 0:
            raise ValueError("原图尺寸无效。")
        target_width = min(1024, source_width)
        target_height = max(1, round(target_width * source_height / source_width))
        output_dir = self.comfy_root / "output" / "lingnian"
        cached = sorted(output_dir.glob(f"production-{task.id}_*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
        for candidate in cached:
            with Image.open(candidate) as cached_image:
                if cached_image.size == (target_width, target_height):
                    return candidate
        input_name = f"lingnian-{task.id}{source.suffix.lower()}"
        comfy_input = self.comfy_root / "input" / input_name
        shutil.copyfile(source, comfy_input)
        workflow = json.loads((Path(__file__).parents[1] / "workflows" / "老照片修复.json").read_text(encoding="utf-8"))
        workflow["1"]["inputs"]["image"] = input_name
        workflow["4"]["inputs"]["width"] = target_width
        workflow["4"]["inputs"]["height"] = target_height
        workflow["4"]["inputs"]["crop"] = "disabled"
        workflow["5"]["inputs"]["filename_prefix"] = f"lingnian/production-{task.id}"
        try:
            history = self.comfy.run(workflow)
            images = history.get("outputs", {}).get("5", {}).get("images", [])
            if not images:
                raise RuntimeError("ComfyUI 没有返回输出文件。")
            item = images[0]
            result = self.comfy_root / "output" / item.get("subfolder", "") / item["filename"]
            if not result.is_file():
                raise RuntimeError("ComfyUI 输出文件不存在。")
            with Image.open(result) as output_image:
                if output_image.size != (target_width, target_height):
                    raise RuntimeError("修复结果尺寸不符合原图宽高比，已阻止上传。")
            return result
        finally:
            comfy_input.unlink(missing_ok=True)

    def run_forever(self) -> None:
        backoff = 3
        while True:
            try:
                self.heartbeat()
                self.once()
                backoff = 3
                time.sleep(self.poll_seconds)
            except (httpx.HTTPError, OSError) as exc:
                self._log("worker_offline", detail=f"{type(exc).__name__}: {exc}")
                self._status("offline", "云端暂时无法连接", error="节点会自动重试。")
                time.sleep(backoff)
                backoff = min(60, backoff * 2)
