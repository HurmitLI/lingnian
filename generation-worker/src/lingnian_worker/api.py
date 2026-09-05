from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from .models import PackageError, RenderReport, TemporaryWorkerError, WorkerTask


MAX_RESULT_UPLOAD_BYTES = 15 * 1024 * 1024


class WorkerApi:
    def __init__(self, backend_url: str, token: str, *, timeout: float = 90.0) -> None:
        self._base = backend_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise TemporaryWorkerError(
                f"Cloud worker API returned HTTP {exc.response.status_code}."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TemporaryWorkerError("云端节点接口暂时不可用。") from exc

    def heartbeat(self, *, software_version: str, device_summary: str, capabilities: tuple[str, ...]) -> int:
        value = self._json(
            "POST",
            "/api/v1/generation-worker/heartbeat",
            json={
                "software_version": software_version,
                "device_summary": device_summary,
                "capabilities": list(capabilities),
            },
        )
        return int(value.get("poll_interval_seconds", 8))

    def claim(self) -> WorkerTask | None:
        value = self._json("POST", "/api/v1/generation-worker/tasks/claim")
        return None if value is None else WorkerTask.from_api(value)

    def download_package(self, task: WorkerTask) -> bytes:
        try:
            response = self._client.get(
                task.package_url,
                headers={"X-Lingnian-Lease": task.lease_token},
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            raise TemporaryWorkerError("授权素材包暂时无法下载。") from exc

    def progress(
        self,
        task: WorkerTask,
        *,
        percent: int,
        stage: str,
        completed: int | None = None,
        total: int | None = None,
        checkpoint_key: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"progress_percent": min(99, max(0, percent)), "progress_stage": stage}
        if total is not None:
            payload.update(
                completed_scene_count=max(0, completed or 0),
                total_scene_count=total,
                checkpoint_key=checkpoint_key,
            )
        self._json(
            "PATCH",
            f"/api/v1/generation-worker/tasks/{task.id}/progress",
            headers={"X-Lingnian-Lease": task.lease_token},
            json=payload,
        )

    def upload_result(self, task: WorkerTask, report: RenderReport) -> None:
        if report.result_path.stat().st_size > MAX_RESULT_UPLOAD_BYTES:
            raise PackageError("Rendered video exceeds the safe upload size before upload.")
        payload = report.result_path.read_bytes()
        self._json(
            "POST",
            f"/api/v1/generation-worker/tasks/{task.id}/result",
            headers={
                "X-Lingnian-Lease": task.lease_token,
                "X-Content-Sha256": hashlib.sha256(payload).hexdigest(),
            },
            data={
                "actual_cost_cents": "0",
                "rendered_scene_count": str(report.rendered_scene_count),
                "duration_seconds": str(report.duration_seconds),
                "width": str(report.width),
                "height": str(report.height),
                "generated_context_scene_count": str(report.generated_context_scene_count),
                "generated_video_scene_count": str(report.generated_video_scene_count),
                "unique_generated_visual_count": str(report.unique_generated_visual_count),
                "duplicate_visual_check_passed": str(report.duplicate_visual_check_passed).lower(),
            },
            files={"result": (report.result_path.name, payload, "video/mp4")},
        )

    def fail(self, task: WorkerTask, *, code: str, retryable: bool) -> None:
        self._json(
            "POST",
            f"/api/v1/generation-worker/tasks/{task.id}/fail",
            headers={"X-Lingnian-Lease": task.lease_token},
            json={"error_code": code, "message": "worker detail redacted", "retryable": retryable},
        )
