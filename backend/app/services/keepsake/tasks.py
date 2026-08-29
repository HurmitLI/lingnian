from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Keepsake, MediaAsset, Story
from app.services.archive.assets import calculate_sha256, resolve_controlled_path, verify_asset_integrity
from app.services.keepsake.renderer import render_keepsake_video
from app.services.security import (
    decrypt_media_file,
    encrypt_media_file,
    get_secret_store,
    is_encrypted_family,
    media_context,
    require_family_master_key,
    reveal_field,
)
from app.services.security.key_store import SecretStore


def recover_interrupted_keepsakes() -> int:
    with SessionLocal() as db:
        result = db.execute(
            update(Keepsake)
            .where(Keepsake.status.in_(["queued", "rendering"]))
            .values(status="failed_retryable", progress=0, error_code="PROCESS_INTERRUPTED")
        )
        db.commit()
        return result.rowcount or 0


def _mark_failed(keepsake_id: str, code: str) -> None:
    with SessionLocal() as db:
        keepsake = db.get(Keepsake, keepsake_id)
        if not keepsake or keepsake.status == "ready":
            return
        keepsake.status = "failed_retryable" if keepsake.attempt < 3 else "failed_final"
        keepsake.progress = 0
        keepsake.error_code = code
        db.commit()


def _secure_value(db, family, obj, field_name: str, key: bytes | None):
    return reveal_field(
        db,
        family=family,
        object_type=obj.__tablename__,
        object_id=obj.id,
        field_name=field_name,
        stored_value=getattr(obj, field_name),
        master_key=key,
    )


def _readable_asset(
    *,
    asset: MediaAsset,
    family_id: str,
    master_key: bytes | None,
    work_dir: Path,
) -> Path:
    settings = get_settings()
    stored = resolve_controlled_path(settings.resolved_asset_root, asset.relative_path)
    if not verify_asset_integrity(
        stored, expected_size=asset.size_bytes, expected_sha256=asset.sha256
    ):
        raise RuntimeError("SOURCE_ASSET_CORRUPT")
    if asset.encryption_version == 0:
        return stored
    if asset.encryption_version != 1 or master_key is None:
        raise RuntimeError("SOURCE_ASSET_DECRYPTION_FAILED")
    readable = work_dir / f"source-{asset.id}"
    result = decrypt_media_file(
        stored,
        readable,
        master_key,
        associated_data=media_context(family_id, asset.id),
    )
    if (
        result.plaintext_size != asset.plaintext_size_bytes
        or result.plaintext_sha256 != asset.plaintext_sha256
    ):
        readable.unlink(missing_ok=True)
        raise RuntimeError("SOURCE_ASSET_CORRUPT")
    return readable


def process_keepsake(
    keepsake_id: str,
    secret_store: SecretStore | None = None,
) -> None:
    settings = get_settings()
    work_dir = settings.resolved_asset_root / "runtime" / "keepsakes" / keepsake_id
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, mode=0o700)
    target_path: Path | None = None
    try:
        with SessionLocal() as db:
            keepsake = db.get(Keepsake, keepsake_id)
            if not keepsake or keepsake.status not in {"queued", "failed_retryable"}:
                return
            family = keepsake.elder.person.family
            store = secret_store or get_secret_store()
            key = require_family_master_key(family, store) if is_encrypted_family(family) else None
            title = _secure_value(db, family, keepsake, "title", key)
            keepsake.status = "rendering"
            keepsake.progress = 10
            keepsake.error_code = None
            db.commit()

            clips: list[dict] = []
            for item in sorted(keepsake.story_manifest, key=lambda value: value["position"]):
                story = db.get(Story, item["story_id"])
                audio = db.get(MediaAsset, item["audio_asset_id"])
                image = db.get(MediaAsset, item["image_asset_id"]) if item.get("image_asset_id") else None
                if not story or story.elder_id != keepsake.elder_id or not audio:
                    raise RuntimeError("KEEPSAKE_MANIFEST_INVALID")
                if story.source_draft.session.status != "ARCHIVED":
                    raise RuntimeError("STORY_NOT_CONFIRMED")
                if audio.session_id != story.source_draft.session_id or audio.kind != "audio_original":
                    raise RuntimeError("KEEPSAKE_MANIFEST_INVALID")
                if image and (
                    image.session_id != story.source_draft.session_id
                    or image.kind not in {"photo_original", "old_object_original"}
                ):
                    raise RuntimeError("KEEPSAKE_MANIFEST_INVALID")
                clips.append(
                    {
                        "story_title": _secure_value(db, family, story, "title", key),
                        "story_excerpt": _secure_value(db, family, story, "body", key)[:360],
                        "life_stage": story.source_draft.session.life_stage,
                        "audio_path": _readable_asset(
                            asset=audio,
                            family_id=family.id,
                            master_key=key,
                            work_dir=work_dir,
                        ),
                        "image_path": _readable_asset(
                            asset=image,
                            family_id=family.id,
                            master_key=key,
                            work_dir=work_dir,
                        )
                        if image
                        else None,
                    }
                )

            if not clips:
                raise RuntimeError("NO_ELIGIBLE_STORIES")

            def update_progress(progress: int) -> None:
                keepsake.progress = progress
                db.commit()

            plaintext_output = work_dir / "keepsake.mp4"
            duration_ms = render_keepsake_video(
                work_dir=work_dir,
                output_path=plaintext_output,
                keepsake_title=title,
                clips=clips,
                width=keepsake.width,
                height=keepsake.height,
                max_source_seconds=settings.keepsake_max_source_seconds,
                timeout_seconds=settings.keepsake_ffmpeg_timeout_seconds,
                on_progress=update_progress,
            )
            relative_path = (
                f"assets/keepsakes/{keepsake.elder_id}/{keepsake.id}.nnmedia"
                if is_encrypted_family(family)
                else f"assets/keepsakes/{keepsake.elder_id}/{keepsake.id}.mp4"
            )
            target_path = resolve_controlled_path(settings.resolved_asset_root, relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.unlink(missing_ok=True)
            if is_encrypted_family(family):
                result = encrypt_media_file(
                    plaintext_output,
                    target_path,
                    key,
                    associated_data=media_context(family.id, keepsake.id),
                )
                keepsake.encryption_version = 1
                keepsake.plaintext_size_bytes = result.plaintext_size
                keepsake.plaintext_sha256 = result.plaintext_sha256
                keepsake.size_bytes = result.ciphertext_size
                keepsake.sha256 = result.ciphertext_sha256
            else:
                shutil.move(plaintext_output, target_path)
                target_path.chmod(0o600)
                keepsake.encryption_version = 0
                keepsake.plaintext_size_bytes = target_path.stat().st_size
                keepsake.plaintext_sha256 = calculate_sha256(target_path)
                keepsake.size_bytes = keepsake.plaintext_size_bytes
                keepsake.sha256 = keepsake.plaintext_sha256
            keepsake.relative_path = relative_path
            keepsake.duration_ms = duration_ms
            keepsake.status = "ready"
            keepsake.progress = 100
            keepsake.error_code = None
            db.commit()
    except Exception as exc:
        message = str(exc)
        known_codes = {
            "NO_ELIGIBLE_STORIES",
            "STORY_NOT_CONFIRMED",
            "SOURCE_ASSET_CORRUPT",
            "SOURCE_ASSET_DECRYPTION_FAILED",
            "KEEPSAKE_MANIFEST_INVALID",
            "KEEPSAKE_DURATION_LIMIT",
            "KEEPSAKE_CJK_FONT_MISSING",
            "KEEPSAKE_RENDER_FAILED",
        }
        code = message if message in known_codes else "KEEPSAKE_RENDER_FAILED"
        _mark_failed(keepsake_id, code)
        if target_path:
            target_path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
