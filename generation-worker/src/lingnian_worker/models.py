from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkerTask:
    id: str
    generation_type: str
    lease_token: str
    package_url: str
    max_cost_cents: int
    attempt_count: int
    production_spec: dict[str, Any]
    resume_checkpoint: dict[str, Any]

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "WorkerTask":
        return cls(
            id=str(value["id"]),
            generation_type=str(value["generation_type"]),
            lease_token=str(value["lease_token"]),
            package_url=str(value["package_url"]),
            max_cost_cents=int(value.get("max_cost_cents", 0)),
            attempt_count=int(value.get("attempt_count", 1)),
            production_spec=dict(value.get("production_spec") or {}),
            resume_checkpoint=dict(value.get("resume_checkpoint") or {}),
        )


@dataclass(frozen=True)
class AuthorizedPackage:
    root: Path
    manifest: dict[str, Any]
    plan: dict[str, Any]
    audio_path: Path | None
    image_path: Path | None


@dataclass(frozen=True)
class RenderReport:
    result_path: Path
    rendered_scene_count: int
    duration_seconds: float
    width: int
    height: int


class WorkerError(RuntimeError):
    code = "WORKFLOW_FAILED"
    retryable = False


class ConfigurationError(WorkerError):
    code = "MODEL_MISSING"


class PackageError(WorkerError):
    code = "PACKAGE_INVALID"


class AudioDurationMismatch(PackageError):
    code = "AUDIO_DURATION_MISMATCH"


class TemporaryWorkerError(WorkerError):
    code = "COMFYUI_TEMPORARY_FAILURE"
    retryable = True


class TaskCancelled(WorkerError):
    code = "TASK_CANCELLED"
