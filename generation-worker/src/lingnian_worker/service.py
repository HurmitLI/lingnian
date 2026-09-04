from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import __version__
from .api import WorkerApi
from .comfyui import ComfyUiClient
from .config import WorkerConfig
from .executor import DocumentaryExecutor
from .media import MediaRenderer
from .models import WorkerError
from .package import open_authorized_package


LOGGER = logging.getLogger("lingnian-worker")


def device_summary() -> str:
    gpu = "GPU 未识别"
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu = result.stdout.strip().splitlines()[0].replace(",", " /", 1) + "MB"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{platform.system()} {platform.release()} · {gpu} · {platform.node()}"[:240]


class WorkerService:
    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.api = WorkerApi(config.backend_url, config.node_token)
        self.comfyui = ComfyUiClient(
            config.comfyui_url,
            config.scene_workflow,
            timeout_seconds=config.comfy_timeout_seconds,
            max_generation_seconds=config.max_generation_seconds,
        )
        self.executor = DocumentaryExecutor(
            renderer=MediaRenderer(ffmpeg=config.ffmpeg, ffprobe=config.ffprobe),
            comfyui=self.comfyui,
            work_dir=config.work_dir,
        )

    def close(self) -> None:
        self.comfyui.close()
        self.api.close()

    def doctor(self) -> str:
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        summary = device_summary()
        if "GPU 未识别" in summary:
            raise WorkerError("没有识别到 NVIDIA 显卡，请检查驱动和 nvidia-smi。")
        self.comfyui.doctor()
        self.api.heartbeat(
            software_version=f"lingnian-worker/{__version__}",
            device_summary=summary,
            capabilities=self.config.capabilities,
        )
        return "云端、系统凭据、ComfyUI、工作流和媒体工具均已就绪。"

    def run_once(self) -> bool:
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        self.comfyui.doctor()
        self.api.heartbeat(
            software_version=f"lingnian-worker/{__version__}",
            device_summary=device_summary(),
            capabilities=self.config.capabilities,
        )
        task = self.api.claim()
        if task is None:
            return False
        LOGGER.info("已领取授权任务 %s（第 %s 次）", task.id, task.attempt_count)
        try:
            payload = self.api.download_package(task)
            with tempfile.TemporaryDirectory(prefix="lingnian-authorized-") as temporary:
                package = open_authorized_package(
                    payload,
                    token=self.config.node_token,
                    request_id=task.id,
                    destination=Path(temporary),
                )
                report = self.executor.execute(
                    task,
                    package,
                    lambda percent, stage, completed, total, checkpoint: self.api.progress(
                        task,
                        percent=percent,
                        stage=stage,
                        completed=completed,
                        total=total,
                        checkpoint_key=checkpoint,
                    ),
                )
                self.api.upload_result(task, report)
            shutil.rmtree(self.config.work_dir / "jobs" / task.id, ignore_errors=True)
            LOGGER.info("任务 %s 已回传，等待家庭人工验收", task.id)
            return True
        except WorkerError as exc:
            LOGGER.error("任务 %s 未完成：%s", task.id, exc)
            try:
                self.api.fail(task, code=exc.code, retryable=exc.retryable)
            except WorkerError:
                LOGGER.warning("任务租约可能已失效，保留本地镜头检查点等待重新领取")
            return True
        except Exception:
            LOGGER.exception("任务 %s 出现未分类错误（日志不包含家庭正文）", task.id)
            try:
                self.api.fail(task, code="WORKFLOW_FAILED", retryable=False)
            except WorkerError:
                pass
            return True

    def run_forever(self) -> None:
        LOGGER.info("聆年家用生成节点 v%s 已启动", __version__)
        while True:
            try:
                handled = self.run_once()
                time.sleep(1 if handled else self.config.poll_seconds)
            except KeyboardInterrupt:
                LOGGER.info("节点已安全停止")
                return
            except WorkerError as exc:
                LOGGER.warning("节点暂时离线：%s", exc)
                time.sleep(self.config.poll_seconds)
