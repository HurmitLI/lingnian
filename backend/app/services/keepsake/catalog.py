from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models import ElderProfile, MediaAsset, Story


def manifest_sha256(items: list[dict]) -> str:
    canonical = json.dumps(items, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_keepsake_manifest(
    db: Session,
    profile: ElderProfile,
    story_ids: list[str],
) -> list[dict]:
    settings = get_settings()
    if len(story_ids) != len(set(story_ids)):
        raise DomainError("DUPLICATE_STORY", "同一篇故事不能重复选择。", 409)
    if not 1 <= len(story_ids) <= settings.keepsake_max_stories:
        raise DomainError(
            "KEEPSAKE_STORY_LIMIT",
            f"每份念想需要选择 1～{settings.keepsake_max_stories} 篇故事。",
            400,
        )

    items: list[dict] = []
    for position, story_id in enumerate(story_ids):
        story = db.get(Story, story_id)
        if not story or story.elder_id != profile.id:
            raise DomainError("STORY_NOT_CONFIRMED", "所选故事不属于当前讲述者。", 409)
        session = story.source_draft.session
        if session.status != "ARCHIVED":
            raise DomainError("STORY_NOT_CONFIRMED", "只有已人工确认归档的故事才能制作念想。", 409)
        audio = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.session_id == session.id,
                MediaAsset.kind == "audio_original",
                MediaAsset.is_original.is_(True),
                MediaAsset.status == "ready",
            )
            .order_by(MediaAsset.created_at.desc())
        )
        if not audio:
            raise DomainError("SOURCE_AUDIO_MISSING", "所选故事没有可用的原始录音。", 409)
        image = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.session_id == session.id,
                MediaAsset.kind.in_(["photo_original", "old_object_original"]),
                MediaAsset.status == "ready",
            )
            .order_by(MediaAsset.created_at.desc())
        )
        items.append(
            {
                "position": position,
                "story_id": story.id,
                "audio_asset_id": audio.id,
                "image_asset_id": image.id if image else None,
            }
        )
    return items
