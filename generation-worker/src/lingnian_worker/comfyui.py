from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from .models import ConfigurationError, TemporaryWorkerError


def _replace_tokens(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_tokens(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_tokens(item, replacements) for item in value]
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        rendered = value
        for token, replacement in replacements.items():
            rendered = rendered.replace(token, str(replacement))
        return rendered
    return value


class ComfyUiClient:
    def __init__(
        self,
        base_url: str,
        workflow_path: Path,
        *,
        timeout_seconds: int,
        max_generation_seconds: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow_path = workflow_path
        self.timeout_seconds = timeout_seconds
        self.max_generation_seconds = max_generation_seconds
        self.client_id = f"lingnian-{uuid4()}"
        self._client = httpx.Client(base_url=self.base_url, timeout=60.0)

    def close(self) -> None:
        self._client.close()

    def doctor(self) -> None:
        if not self.workflow_path.is_file():
            raise ConfigurationError("缺少纪实空镜 ComfyUI API 工作流。")
        try:
            response = self._client.get("/system_stats")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TemporaryWorkerError("ComfyUI 尚未在本机 8188 端口就绪。") from exc

    def _load_workflow(self, replacements: dict[str, Any]) -> dict[str, Any]:
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError("纪实空镜工作流不是有效 JSON。") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise ConfigurationError("纪实空镜工作流必须是 ComfyUI API 格式。")
        return _replace_tokens(workflow, replacements)

    def upload_image(self, path: Path) -> str:
        try:
            with path.open("rb") as source:
                response = self._client.post(
                    "/upload/image",
                    data={"type": "input", "overwrite": "true"},
                    files={"image": (path.name, source, "application/octet-stream")},
                )
            response.raise_for_status()
            return str(response.json()["name"])
        except (OSError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise TemporaryWorkerError("ComfyUI 暂时无法接收授权图片。") from exc

    def generate_scene(
        self,
        *,
        scene: dict[str, Any],
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        source_image: Path | None,
        on_wait: Callable[[], None] | None = None,
    ) -> Path:
        duration = int(scene["duration_seconds"])
        generated_duration = min(duration, self.max_generation_seconds)
        prompt = (
            f"中国家庭纪实电影风格，克制、真实、自然光，{scene['visual_direction']} "
            "不出现文字水印，不生成可识别真人正脸，不增加故事之外的人物或事件。"
        )
        image_name = self.upload_image(source_image) if source_image else ""
        workflow = self._load_workflow(
            {
                "__LINGNIAN_PROMPT__": prompt,
                "__LINGNIAN_NEGATIVE_PROMPT__": "字幕，水印，标志，畸形人物，正脸，身份错乱，现代物品，虚构事件",
                "__LINGNIAN_WIDTH__": width,
                "__LINGNIAN_HEIGHT__": height,
                "__LINGNIAN_FPS__": fps,
                "__LINGNIAN_FRAMES__": max(fps, generated_duration * fps),
                "__LINGNIAN_DURATION_SECONDS__": generated_duration,
                "__LINGNIAN_IMAGE__": image_name,
                "__LINGNIAN_OUTPUT_PREFIX__": f"lingnian/{output_path.stem}",
                "__LINGNIAN_SEED__": int.from_bytes(
                    scene["source_story_id"].encode("utf-8")[:8], "little"
                ) % (2**63 - 1),
            }
        )
        try:
            submitted = self._client.post("/prompt", json={"prompt": workflow, "client_id": self.client_id})
            submitted.raise_for_status()
            prompt_id = str(submitted.json()["prompt_id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise TemporaryWorkerError("ComfyUI 未能启动这个镜头。") from exc

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if on_wait:
                on_wait()
            try:
                response = self._client.get(f"/history/{prompt_id}")
                response.raise_for_status()
                history = response.json().get(prompt_id)
            except (httpx.HTTPError, ValueError) as exc:
                raise TemporaryWorkerError("读取 ComfyUI 镜头进度失败。") from exc
            if history:
                status = history.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    raise TemporaryWorkerError("ComfyUI 镜头生成失败。")
                artifact = self._first_artifact(history.get("outputs") or {})
                if artifact:
                    return self._download_artifact(artifact, output_path)
            time.sleep(2)
        raise TemporaryWorkerError("ComfyUI 镜头生成超时。")

    @staticmethod
    def _first_artifact(outputs: dict[str, Any]) -> dict[str, str] | None:
        for node_output in outputs.values():
            for key in ("videos", "gifs", "images"):
                for item in node_output.get(key) or []:
                    filename = str(item.get("filename", ""))
                    if filename:
                        return {
                            "filename": filename,
                            "subfolder": str(item.get("subfolder", "")),
                            "type": str(item.get("type", "output")),
                        }
        return None

    def _download_artifact(self, artifact: dict[str, str], output_path: Path) -> Path:
        suffix = Path(artifact["filename"]).suffix.lower() or ".bin"
        target = output_path.with_suffix(suffix)
        try:
            response = self._client.get(f"/view?{urlencode(artifact)}")
            response.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
        except (OSError, httpx.HTTPError) as exc:
            raise TemporaryWorkerError("ComfyUI 镜头结果下载失败。") from exc
        return target
