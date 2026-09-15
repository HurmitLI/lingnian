"""Family-scoped offline export; does not queue generation or grant review approval."""
from pathlib import Path
import shutil
import tempfile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.routes import family_master_key, protect_values, require, secure_value, transcript_read
from app.api.short_scene_routes import prepare
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import MediaAsset, MemorySession, ModelConsentEvent, ShortScenePlan
from app.models.entities import now_utc
from app.services.archive.assets import resolve_controlled_path, verify_asset_integrity
from app.services.auth import current_auth
from app.services.database_snapshot import persist_configured_database_snapshot
from app.services.memory.short_scene_bundle import LIMITS, create_short_scene_bundle
from app.services.security import SecretStore, decrypt_media_file, get_secret_store, media_context
from app.services.security.secure_fields import is_encrypted_family

router = APIRouter(prefix="/api/v1", tags=["short-scene"])


class ExportConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorize_material_export: StrictBool
    reference_rights_confirmed: StrictBool
    subject_consent: StrictBool
    no_impersonation: StrictBool


@router.post("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/production-package")
def export_short_scene_package(
    session_id: str, plan_id: str, consent: str = Form(...), reference: UploadFile = File(...),
    db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store),
):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次记录。")
    item = require(db, ShortScenePlan, plan_id, "SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。")
    if item.session_id != session.id:
        raise DomainError("SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。", 404)
    try:
        options = ExportConsent.model_validate_json(consent)
    except ValidationError as exc:
        raise DomainError("SHORT_SCENE_EXPORT_CONSENT_INVALID", "请提供完整、明确的本次素材导出授权。", 422) from exc
    if not all((options.authorize_material_export, options.reference_rights_confirmed,
                options.subject_consent, options.no_impersonation)):
        raise DomainError("SHORT_SCENE_EXPORT_CONSENT_REQUIRED", "请确认原声导出、参考图使用权及人物授权。", 409)
    if item.status != "awaiting_scene_context_review":
        raise DomainError("SHORT_SCENE_PLAN_NOT_READY", "这份方案尚不可用于制作包。", 409)
    family = session.elder.person.family
    if family.data_classification != "test" and not is_encrypted_family(family):
        raise DomainError("SHORT_SCENE_ENCRYPTION_REQUIRED", "真实家庭档案需要先启用加密。", 409)
    request = prepare(db, session, store, item.reference_mode)
    if not (request["input_sha256"] == item.input_sha256 == options.input_sha256):
        raise DomainError("SHORT_SCENE_INPUT_CHANGED", "采访内容已变化，请重新准备场景方案。", 409)
    result = secure_value(db, family, item, "result_payload", store)
    audio = db.scalar(select(MediaAsset).where(
        MediaAsset.session_id == session.id, MediaAsset.kind == "audio_original",
        MediaAsset.is_original.is_(True), MediaAsset.status == "ready",
    ).order_by(MediaAsset.created_at.desc()))
    if audio is None:
        raise DomainError("ORIGINAL_AUDIO_REQUIRED", "这次采访没有完整原声。", 409)
    # Only the server-owned merged WAV is accepted. Never accept client file paths or timings.
    if audio.size_bytes > LIMITS["recording.wav"] + 128:
        raise DomainError("SHORT_SCENE_ASSET_TOO_LARGE", "完整原声超出制作包大小限制。", 413)
    root = Path(tempfile.mkdtemp(prefix="lingnian-short-export-"))
    try:
        source = resolve_controlled_path(get_settings().resolved_asset_root, audio.relative_path)
        if not verify_asset_integrity(source, expected_size=audio.size_bytes, expected_sha256=audio.sha256):
            raise DomainError("ASSET_INTEGRITY_FAILED", "原声文件校验失败，未导出素材。", 409)
        recording = root / "recording.wav"
        if audio.encryption_version == 0:
            shutil.copyfile(source, recording)
        elif audio.encryption_version == 1:
            key = family_master_key(family, store)
            if key is None:
                raise DomainError("MASTER_KEY_MISSING", "无法解锁家庭原声。", 409)
            decoded = decrypt_media_file(source, recording, key, associated_data=media_context(family.id, audio.id))
            if decoded.plaintext_sha256 != audio.plaintext_sha256 or decoded.plaintext_size != audio.plaintext_size_bytes:
                raise DomainError("ASSET_INTEGRITY_FAILED", "原声解密校验失败。", 409)
        else:
            raise DomainError("ASSET_ENCRYPTION_UNSUPPORTED", "无法读取这份加密原声。", 409)
        recording.chmod(0o600)
        image = root / "reference.png"
        count = 0
        with image.open("xb") as output:
            while chunk := reference.file.read(1024 * 1024):
                count += len(chunk)
                if count > LIMITS["reference.png"]:
                    raise DomainError("SHORT_SCENE_REFERENCE_TOO_LARGE", "参考图不能超过32MiB。", 413)
                output.write(chunk)
        image.chmod(0o600)
        bundle = create_short_scene_bundle(
            metadata=transcript_read(db, session.transcript, store).asr_metadata,
            raw_answers={t.id: secure_value(db, family, t, "raw_answer_text", store) for t in session.interview_turns},
            raw_questions={t.id: secure_value(db, family, t, "question_text", store) for t in session.interview_turns},
            interview_context=request["payload"]["interview_context"], audio_asset_id=audio.id,
            input_sha256=options.input_sha256, model_response=result.get("selection", {}),
            normalized_audio=recording, reference=image,
            reference_kind="user_photo" if item.reference_mode == "user_photo" else "generated_reference",
            source_use_authorized=True, reference_use_authorized=True, destination=root / "short-scene.zip",
        )
        context = current_auth.get()
        event = ModelConsentEvent(
            family_id=family.id, session_id=session.id, purpose="short_scene_material_export",
            actor_label=f"user:{context.user_id}" if context else "本机用户", decision="granted",
            one_time=True, used_at=now_utc(), input_sha256=bundle["sha256"], data_classification=family.data_classification,
        )
        db.add(event)
        db.flush()
        protect_values(db, family, event, {"actor_label": event.actor_label}, store)
        db.commit()
        if get_settings().formal_auth_required:
            if persist_configured_database_snapshot(get_settings()) is None:
                raise DomainError("SHORT_SCENE_DURABILITY_FAILED", "导出授权尚未安全保存，未提供素材包。", 503)
        return FileResponse(
            bundle["path"], media_type="application/zip", filename="lingnian-short-scene.zip",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Lingnian-No-Snapshot": "1",
                     "X-Lingnian-Package-Sha256": bundle["sha256"], "X-Lingnian-Generation-Ready": "false"},
            background=BackgroundTask(shutil.rmtree, root, ignore_errors=True),
        )
    except DomainError:
        shutil.rmtree(root, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        # Do not expose paths, decrypted interview text, image metadata or key failures.
        raise DomainError("SHORT_SCENE_EXPORT_FAILED", "制作包准备失败，请检查参考PNG构图及原声依据；没有提交生成。", 409) from exc
