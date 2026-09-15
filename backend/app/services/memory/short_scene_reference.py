"""Prepare a source-bound reference brief, not a GPU grant or a visual review."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from .short_scene_selection import validate_selection_response


def prepare_reference_brief(request: dict, response: dict, *, input_sha256: str,
                            photo: Path | None = None, photo_use_authorized: bool = False) -> dict:
    result = validate_selection_response(request, response, input_sha256=input_sha256)
    if result["status"] != "awaiting_scene_context_review":
        raise ValueError("SHORT_REFERENCE_NO_SELECTED_SCENE")
    mode = request["payload"]["reference_mode"]
    source = None
    if mode == "user_photo":
        if photo_use_authorized is not True or photo is None or photo.is_symlink() or not photo.is_file():
            raise ValueError("SHORT_REFERENCE_PHOTO_AUTHORIZATION_REQUIRED")
        if not 0 < photo.stat().st_size <= 32 * 1024**2:
            raise ValueError("SHORT_REFERENCE_PHOTO_TOO_LARGE")
        with Image.open(photo) as picture:
            if picture.format not in {"PNG", "JPEG"} or not all(128 <= d <= 4096 for d in picture.size):
                raise ValueError("SHORT_REFERENCE_PHOTO_INVALID")
            picture.verify()
        source = {"sha256": hashlib.sha256(photo.read_bytes()).hexdigest(), "usage_authorized": True,
                  "identity_claim": "photo_reference_not_historical_footage"}
    elif photo is not None:
        raise ValueError("SHORT_REFERENCE_UNEXPECTED_PHOTO")
    # JSON clients serialize 9.0 as 9. Canonicalize this only numeric float field
    # before hashing so a normal browser/Windows JSON round trip does not break the brief.
    candidate = dict(result["candidate"])
    duration = float(candidate["duration_seconds"])
    candidate["duration_seconds"] = int(duration) if duration.is_integer() else duration
    body = {"format": "lingnian-short-reference", "version": 1, "renderer_version": 2,
            "selection_input_sha256": input_sha256,
            "reference_mode": mode, "source_photo": source,
            "candidate": candidate, "scene": result["selection"]["scene"],
            "answers": request["payload"]["answers"], "interview_context": request["payload"]["interview_context"],
            "identity_claim": "photo_reference_not_historical_footage" if source else "illustrative_not_verified_likeness",
            "target": {"width": 1280, "height": 704, "images": 1},
            "generation_authorized": False, "visual_accepted": False}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return {"brief": body, "brief_sha256": hashlib.sha256(encoded).hexdigest(),
            "status": "awaiting_reference_generation_consent", "generation_ready": False}
