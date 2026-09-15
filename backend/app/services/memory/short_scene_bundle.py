"""Build an offline worker bundle from current source evidence and a saved proposal.

This is a preparation artifact, NOT generation authorization or visual approval.
Callers own asset decryption and access checks; no client paths are accepted by an API.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from .short_scene import bind_short_scene_source
from .short_scene_selection import prepare_selection_request, validate_selection_response


FORMAT = "lingnian-short-scene-bundle"
LIMITS = {"plan.json": 256 * 1024, "recording.wav": 256 * 1024**2, "reference.png": 32 * 1024**2}


def file_evidence(path: Path) -> dict:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
            size += len(block)
    return {"sha256": sha.hexdigest(), "size_bytes": size}


def create_short_scene_bundle(*, metadata: dict, raw_answers: dict[str, str], raw_questions: dict[str, str],
                              interview_context: dict, audio_asset_id: str, input_sha256: str,
                              model_response: dict, normalized_audio: Path, reference: Path,
                              reference_kind: str, source_use_authorized: bool,
                              reference_use_authorized: bool, destination: Path,
                              test_fixture_only: bool = False) -> dict:
    if source_use_authorized is not True or reference_use_authorized is not True:
        raise ValueError("SHORT_SCENE_MATERIAL_AUTHORIZATION_REQUIRED")
    if reference_kind not in {"user_photo", "generated_reference"}:
        raise ValueError("SHORT_SCENE_REFERENCE_KIND_INVALID")
    if destination.exists() or destination.is_symlink():
        raise ValueError("SHORT_SCENE_BUNDLE_ALREADY_EXISTS")
    # Reject invalid/oversized files before reading or normalizing their content.
    for path, name in ((normalized_audio, "recording.wav"), (reference, "reference.png")):
        if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= LIMITS[name]:
            raise ValueError("SHORT_SCENE_ASSET_INVALID")
    with Image.open(reference) as picture:
        w, h = picture.size
        if picture.format != "PNG" or not (640 <= w <= 4096 and 352 <= h <= 4096) or abs(w / h - 1280 / 704) >= 0.08:
            raise ValueError("SHORT_SCENE_REFERENCE_COMPOSITION_REQUIRED")
        picture.verify()
    request = prepare_selection_request(
        metadata, raw_answers=raw_answers, raw_questions=raw_questions, interview_context=interview_context,
        audio_asset_id=audio_asset_id, reference_mode="user_photo" if reference_kind == "user_photo" else "illustrative",
    )
    result = validate_selection_response(request, model_response, input_sha256=input_sha256)
    if result["status"] != "awaiting_scene_context_review":
        raise ValueError("SHORT_SCENE_NO_SELECTED_SCENE")
    chosen = result["selection"]
    plan = bind_short_scene_source(
        metadata, raw_answers=raw_answers, audio_asset_id=audio_asset_id,
        candidate_id=chosen["candidate_id"], normalized_audio=normalized_audio, source_use_authorized=True,
    )
    if len(plan["source_text"]) > 6000:
        raise ValueError("SHORT_SCENE_WORKER_CONTEXT_TOO_LONG")
    scene = chosen["scene"]
    facts = {fact["field"]: fact for fact in scene["facts"]}
    reference_evidence = file_evidence(reference)
    plan.update({
        "status": "awaiting_input_review", "generation_ready": False, "test_fixture_only": test_fixture_only is True,
        "reference": {"kind": reference_kind, "usage_authorized": True, "sha256": reference_evidence["sha256"],
                      "identity_claim": "photo_reference_not_historical_footage" if reference_kind == "user_photo" else "illustrative_not_verified_likeness"},
        "scene": {**{key: scene[key] for key in ("context_summary", "opening_state", "action", "render_bible", "render_action", "source_quotes")},
                  **{key: facts[key]["value"] if facts[key]["status"] == "known" else "未知；参考画面仅作示意，不作为历史事实"
                     for key in ("character", "wardrobe", "location", "era")},
                  "unknowns": [f"{key}: 原文未说明" for key, fact in facts.items() if fact["status"] == "unknown"] + scene["illustrative_details"],
                  "shot_count": 1, "subject_count": 1, "camera": "locked", "style": "consistent_color_live_action"},
        "provenance": {"selection_input_sha256": input_sha256, "interview_context": request["payload"]["interview_context"],
                       "answers": request["payload"]["answers"], "facts": scene["facts"],
                       "notice": "只完成资料绑定；输入参考与完整成片仍需实际复核，无自动生成授权。"},
    })
    if test_fixture_only is True:
        plan["recording"]["kind"] = "synthetic_pcm_fixture"
    plan_bytes = json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(plan_bytes) > LIMITS["plan.json"]:
        raise ValueError("SHORT_SCENE_PLAN_TOO_LARGE")
    audio_evidence = file_evidence(normalized_audio)
    if audio_evidence["sha256"] != plan["recording"]["sha256"]:
        raise ValueError("SHORT_SCENE_RECORDING_CHANGED")
    manifest = {"format": FORMAT, "version": 1, "files": {
        "plan.json": {"sha256": hashlib.sha256(plan_bytes).hexdigest(), "size_bytes": len(plan_bytes)},
        "recording.wav": audio_evidence, "reference.png": reference_evidence,
    }}
    fd, temporary = tempfile.mkstemp(prefix=".short-scene-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    try:
        # ZIP_STORED avoids compression bombs and preserves exact original bytes.
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True).encode())
            bundle.writestr("plan.json", plan_bytes)
            for path, name in ((normalized_audio, "recording.wav"), (reference, "reference.png")):
                sha, count = hashlib.sha256(), 0
                with path.open("rb") as source, bundle.open(name, "w") as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        count += len(block)
                        if count > LIMITS[name]:
                            raise ValueError("SHORT_SCENE_ASSET_TOO_LARGE")
                        sha.update(block)
                        target.write(block)
                if {"sha256": sha.hexdigest(), "size_bytes": count} != manifest["files"][name]:
                    raise ValueError("SHORT_SCENE_ASSET_CHANGED_DURING_PACKAGING")
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and fails if another writer won.
        os.link(temporary, destination)
        return {"path": str(destination), **file_evidence(destination), "format": FORMAT, "version": 1,
                "status": "awaiting_input_review", "generation_ready": False, "manifest": manifest}
    finally:
        Path(temporary).unlink(missing_ok=True)
