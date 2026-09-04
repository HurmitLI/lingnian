from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import keyring
from dotenv import load_dotenv

from .models import ConfigurationError


KEYRING_SERVICE = "lingnian-generation-worker"
KEYRING_USERNAME = "node-token"


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数。") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于零。")
    return value


def _resolve_executable(explicit: str | None, command: str, bundled: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ConfigurationError(f"没有找到 {path.name}。")
        return str(path)
    discovered = shutil.which(command)
    if discovered:
        return discovered
    if bundled:
        return bundled
    raise ConfigurationError(f"没有找到 {command}，请先安装并加入 PATH。")


@dataclass(frozen=True)
class WorkerConfig:
    backend_url: str
    node_token: str
    comfyui_url: str
    work_dir: Path
    scene_workflow: Path
    poll_seconds: int
    comfy_timeout_seconds: int
    max_generation_seconds: int
    capabilities: tuple[str, ...]
    ffmpeg: str
    ffprobe: str
    once: bool = False

    @classmethod
    def from_env(cls, *, once: bool = False) -> "WorkerConfig":
        load_dotenv()
        backend_url = (
            os.getenv("LINGNIAN_BACKEND_URL")
            or os.getenv("LINGNIAN_API_BASE")
            or ""
        ).strip().rstrip("/")
        if not backend_url.startswith("https://"):
            raise ConfigurationError("LINGNIAN_BACKEND_URL 必须使用 HTTPS。")
        token = os.getenv("LINGNIAN_NODE_TOKEN", "").strip()
        if not token:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or ""
        if len(token) < 32:
            raise ConfigurationError("没有找到有效的家用节点连接密钥。")
        capabilities = tuple(
            item.strip()
            for item in os.getenv("LINGNIAN_CAPABILITIES", "scene_video").split(",")
            if item.strip()
        )
        allowed = {"photo_restore", "portrait_video", "scene_video"}
        if not capabilities or set(capabilities) - allowed:
            raise ConfigurationError("LINGNIAN_CAPABILITIES 包含不支持的能力。")
        work_dir = Path(os.getenv("LINGNIAN_WORK_DIR", ".worker-data")).expanduser().resolve()
        workflow = Path(
            os.getenv("LINGNIAN_SCENE_WORKFLOW", "workflows/documentary-scene-api.json")
        ).expanduser().resolve()
        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg = _resolve_executable(os.getenv("LINGNIAN_FFMPEG"), "ffmpeg", bundled_ffmpeg)
        ffprobe_guess = str(Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"))
        ffprobe = _resolve_executable(
            os.getenv("LINGNIAN_FFPROBE"),
            "ffprobe",
            ffprobe_guess if Path(ffprobe_guess).is_file() else None,
        )
        return cls(
            backend_url=backend_url,
            node_token=token,
            comfyui_url=os.getenv("LINGNIAN_COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/"),
            work_dir=work_dir,
            scene_workflow=workflow,
            poll_seconds=_positive_int("LINGNIAN_POLL_SECONDS", 8),
            comfy_timeout_seconds=_positive_int("LINGNIAN_COMFY_TIMEOUT_SECONDS", 1800),
            max_generation_seconds=_positive_int("LINGNIAN_MAX_GENERATION_SECONDS", 5),
            capabilities=capabilities,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            once=once,
        )
