from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from .models import ConfigurationError, TemporaryWorkerError


PROMPT_TOKEN = "__LINGNIAN_PROMPT__"
NEGATIVE_PROMPT_TOKEN = "__LINGNIAN_NEGATIVE_PROMPT__"
SEED_TOKEN = "__LINGNIAN_SEED__"
OUTPUT_PREFIX_TOKEN = "__LINGNIAN_OUTPUT_PREFIX__"


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


def _scene_seed(scene: dict[str, Any], seed_offset: int = 0) -> int:
    """Create a stable but shot-specific seed instead of repeating one story seed."""

    material = json.dumps(
        {
            "story": scene.get("source_story_id"),
            "scene": scene.get("scene"),
            "subtitle": scene.get("subtitle"),
            "visual_direction": scene.get("visual_direction"),
            "attempt": seed_offset,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % (2**63 - 1)


def _connected_node_id(value: Any) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], (str, int)):
        return str(value[0])
    return None


def _adapt_common_workflow(workflow: dict[str, Any], replacements: dict[str, Any]) -> dict[str, Any]:
    """Inject Lingnian values into common ComfyUI API graphs without placeholders.

    Existing classroom workflows are frequently exported with fixed prompts.  A
    documentary worker must not silently reuse those fixed values for every shot.
    We follow the sampler's positive/negative conditioning edges and update the
    connected text encoders, then update common seed/output/dimension fields.
    """

    rendered = _replace_tokens(workflow, replacements)
    positive_ids: set[str] = set()
    negative_ids: set[str] = set()
    for node in rendered.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", "")).lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or "sampler" not in class_type:
            continue
        positive = _connected_node_id(inputs.get("positive"))
        negative = _connected_node_id(inputs.get("negative"))
        if positive:
            positive_ids.add(positive)
        if negative:
            negative_ids.add(negative)

    for node_id, node in rendered.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", "")).lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if str(node_id) in positive_ids and isinstance(inputs.get("text"), str):
            inputs["text"] = replacements[PROMPT_TOKEN]
        if str(node_id) in negative_ids and isinstance(inputs.get("text"), str):
            inputs["text"] = replacements[NEGATIVE_PROMPT_TOKEN]
        for seed_key in ("seed", "noise_seed"):
            if seed_key in inputs and not isinstance(inputs[seed_key], list):
                inputs[seed_key] = replacements[SEED_TOKEN]
        if "filename_prefix" in inputs:
            inputs["filename_prefix"] = replacements[OUTPUT_PREFIX_TOKEN]
        if "width" in inputs and not isinstance(inputs["width"], list):
            inputs["width"] = replacements["__LINGNIAN_WIDTH__"]
        if "height" in inputs and not isinstance(inputs["height"], list):
            inputs["height"] = replacements["__LINGNIAN_HEIGHT__"]
        if "video" in class_type or "latentvideo" in class_type:
            for frame_key in ("frames", "num_frames", "video_frames", "length"):
                if frame_key in inputs and not isinstance(inputs[frame_key], list):
                    inputs[frame_key] = replacements["__LINGNIAN_FRAMES__"]
    return rendered


def _workflow_binding_report(workflow: dict[str, Any]) -> dict[str, bool]:
    serialized = json.dumps(workflow, ensure_ascii=False)
    prompt_bound = PROMPT_TOKEN in serialized
    seed_bound = SEED_TOKEN in serialized
    output_bound = OUTPUT_PREFIX_TOKEN in serialized
    sampler_conditioning = False
    seed_field = False
    output_field = False
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", "")).lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "sampler" in class_type and _connected_node_id(inputs.get("positive")):
            sampler_conditioning = True
        seed_field = seed_field or any(key in inputs for key in ("seed", "noise_seed"))
        output_field = output_field or "filename_prefix" in inputs
    return {
        "prompt": prompt_bound or sampler_conditioning,
        "seed": seed_bound or seed_field,
        "output": output_bound or output_field,
    }


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
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError("纪实空镜工作流不是有效 JSON。") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise ConfigurationError("纪实空镜工作流必须是 ComfyUI API 格式。")
        bindings = _workflow_binding_report(workflow)
        missing = [label for key, label in (("prompt", "提示词"), ("seed", "随机种子"), ("output", "输出文件名")) if not bindings[key]]
        if missing:
            raise ConfigurationError(f"纪实空镜工作流无法接入{'、'.join(missing)}。")
        try:
            response = self._client.get("/system_stats")
            response.raise_for_status()
            object_info = self._client.get("/object_info")
            object_info.raise_for_status()
            available = object_info.json()
        except httpx.HTTPError as exc:
            raise TemporaryWorkerError("ComfyUI 尚未在本机 8188 端口就绪。") from exc
        classes = {str(node.get("class_type", "")) for node in workflow.values() if isinstance(node, dict)}
        if not classes or classes - set(available):
            raise ConfigurationError("纪实空镜工作流包含本机 ComfyUI 不支持的节点。")

    def _load_workflow(self, replacements: dict[str, Any]) -> dict[str, Any]:
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError("纪实空镜工作流不是有效 JSON。") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise ConfigurationError("纪实空镜工作流必须是 ComfyUI API 格式。")
        return _adapt_common_workflow(workflow, replacements)

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
        seed_offset: int = 0,
        on_wait: Callable[[], None] | None = None,
    ) -> Path:
        duration = int(scene["duration_seconds"])
        generated_duration = min(duration, self.max_generation_seconds)
        scene_text = str(scene.get("narration") or scene.get("subtitle") or "").strip()
        prompt = (
            f"中国家庭纪实电影风格，克制、真实、自然光。第{scene.get('scene', '')}镜头；"
            f"画面必须直接对应已确认原文「{scene_text}」。{scene['visual_direction']} "
            "不出现文字水印，不生成可识别真人正脸，不增加故事之外的人物或事件。"
        )
        image_name = self.upload_image(source_image) if source_image else ""
        workflow = self._load_workflow(
            {
                "__LINGNIAN_PROMPT__": prompt,
                "__LINGNIAN_NEGATIVE_PROMPT__": (
                    "字幕，水印，标志，畸形人物，正脸，身份错乱，现代物品，虚构事件，"
                    "无关城市航拍，通用城市天际线，重复画面，静止人物"
                ),
                "__LINGNIAN_WIDTH__": width,
                "__LINGNIAN_HEIGHT__": height,
                "__LINGNIAN_FPS__": fps,
                "__LINGNIAN_FRAMES__": max(fps, generated_duration * fps),
                "__LINGNIAN_DURATION_SECONDS__": generated_duration,
                "__LINGNIAN_IMAGE__": image_name,
                "__LINGNIAN_OUTPUT_PREFIX__": f"lingnian/{output_path.stem}",
                "__LINGNIAN_SEED__": _scene_seed(scene, seed_offset),
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
