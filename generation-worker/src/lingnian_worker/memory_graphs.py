"""Explicit Klein/Wan graphs for reference-led memory films.

Keep these separate from legacy workflow adaptation: the native model canvas,
conditioning links and frame count are part of the tested rendering contract.
"""
from __future__ import annotations

import json
import re
from importlib.resources import files

from .models import PackageError


def _template(name: str) -> dict:
    return json.loads(files("lingnian_worker").joinpath("workflows", name + ".json").read_text(encoding="utf-8"))


def _validate(prompt: str, seed: int, prefix: str) -> None:
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 6000:
        raise PackageError("镜头缺少有效的画面描述。")
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise PackageError("镜头种子不正确。")
    if not re.fullmatch(r"lingnian-memory/[a-zA-Z0-9_-]{1,80}", prefix):
        raise PackageError("镜头输出标识不正确。")


def reference_graph(*, prompt: str, seed: int, prefix: str, reference: str | None = None) -> dict:
    _validate(prompt, seed, prefix)
    graph = _template("memory-reference-v1")
    graph["4"]["inputs"]["text"] = prompt
    graph["7"]["inputs"]["noise_seed"] = seed
    graph["13"]["inputs"]["filename_prefix"] = prefix
    if reference:
        graph.update({
            "20": {"class_type": "LoadImage", "inputs": {"image": reference}},
            "21": {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": ["20", 0], "upscale_method": "nearest-exact", "megapixels": 1.0,
                "resolution_steps": 1}},
            "22": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["3", 0]}},
            "23": {"class_type": "ReferenceLatent", "inputs": {
                "conditioning": ["4", 0], "latent": ["22", 0]}},
            "24": {"class_type": "ReferenceLatent", "inputs": {
                "conditioning": ["5", 0], "latent": ["22", 0]}},
        })
        graph["8"]["inputs"].update(positive=["23", 0], negative=["24", 0])
    return graph


def motion_graph(*, prompt: str, seed: int, prefix: str, reference: str) -> dict:
    _validate(prompt, seed, prefix)
    if not reference:
        raise PackageError("动态镜头必须使用当前场景的参考图。")
    graph = _template("memory-motion-v1")
    graph["4"]["inputs"]["image"] = reference
    graph["5"]["inputs"]["text"] = prompt
    graph["9"]["inputs"]["seed"] = seed
    graph["12"]["inputs"]["filename_prefix"] = prefix
    return graph
