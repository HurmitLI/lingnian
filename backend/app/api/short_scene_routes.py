"""Purpose-bound text selection. This router cannot submit GPU jobs."""
from datetime import UTC, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes import protect_values, require, secure_value, transcript_read
from app.core.database import get_db
from app.core.config import get_settings
from app.core.errors import DomainError
from app.models import FamilyArchive, MediaAsset, MemorySession, ModelConsentEvent, ShortScenePlan
from app.models.entities import now_utc
from app.services.auth import current_auth
from app.services.database_snapshot import persist_configured_database_snapshot
from app.services.llm.provider import get_llm_provider
from app.services.memory.short_scene_selection import PURPOSE, prepare_selection_request, validate_selection_response
from app.services.security import SecretStore, get_secret_store
from app.services.security.secure_fields import is_encrypted_family

router = APIRouter(prefix="/api/v1", tags=["short-scene"])
ReferenceMode = Literal["illustrative", "user_photo"]


class SelectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_mode: ReferenceMode = "illustrative"
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    authorize_text_send: StrictBool


def prepare(db, session, store, mode):
    audio = db.scalar(select(MediaAsset).where(
        MediaAsset.session_id == session.id, MediaAsset.kind == "audio_original",
        MediaAsset.is_original.is_(True), MediaAsset.status == "ready",
    ).order_by(MediaAsset.created_at.desc()))
    metadata = transcript_read(db, session.transcript, store).asr_metadata if session.transcript else {}
    family = session.elder.person.family
    answers = {t.id: secure_value(db, family, t, "raw_answer_text", store) for t in session.interview_turns}
    questions = {t.id: secure_value(db, family, t, "question_text", store) for t in session.interview_turns}
    narrator = session.narrator
    context = {
        "subject_label": secure_value(db, family, session.elder, "preferred_name", store),
        "narrator_label": secure_value(db, family, narrator, "display_name", store) if narrator else None,
        "narrator_is_subject": narrator.id == session.elder.person_id if narrator else None,
    }
    try:
        return prepare_selection_request(metadata, raw_answers=answers,
                                         audio_asset_id=audio.id if audio else "", reference_mode=mode,
                                         raw_questions=questions, interview_context=context)
    except ValueError as exc:
        raise DomainError("SHORT_SCENE_SOURCE_INVALID", "原声和文字依据不完整，请先检查采访记录。", 409) from exc


def view(db, item, store):
    family = db.get(FamilyArchive, item.family_id)
    status = item.status
    # A crashed process must not look active forever or silently send again.
    if status == "dispatching" and item.deadline_at.replace(tzinfo=UTC) < now_utc():
        status = "outcome_unknown"
    return {"id": item.id, "session_id": item.session_id, "input_sha256": item.input_sha256,
            "reference_mode": item.reference_mode, "status": status,
            "result": secure_value(db, family, item, "result_payload", store),
            "error_code": item.error_code, "created_at": item.created_at,
            "generation_ready": False, "automatic_retry": False}


@router.get("/memory-sessions/{session_id}/short-scene-selection-input")
def selection_input(session_id: str, reference_mode: ReferenceMode = "illustrative",
                    db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次记录。")
    request = prepare(db, session, store, reference_mode)
    # Read-only preview of exactly which text will leave the archive.
    return {k: v for k, v in request.items() if k not in {"system_prompt", "user_content"}}


@router.get("/memory-sessions/{session_id}/short-scene-plans")
def list_plans(session_id: str, db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次记录。")
    return [view(db, p, store) for p in db.scalars(select(ShortScenePlan).where(
        ShortScenePlan.session_id == session_id).order_by(ShortScenePlan.created_at.desc()).limit(20))]


@router.post("/memory-sessions/{session_id}/short-scene-plans")
def select_scene(session_id: str, payload: SelectionCreate, db: Session = Depends(get_db),
                 store: SecretStore = Depends(get_secret_store)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次记录。")
    family = session.elder.person.family
    if not payload.authorize_text_send:
        raise DomainError("SHORT_SCENE_CONSENT_REQUIRED", "需要同意本次发送采访文字以选择场景。", 409)
    existing = db.scalar(select(ShortScenePlan).where(
        ShortScenePlan.session_id == session.id, ShortScenePlan.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.input_sha256 != payload.input_sha256 or existing.reference_mode != payload.reference_mode:
            raise DomainError("SHORT_SCENE_KEY_CONFLICT", "这次请求已绑定其他内容，请重新查看选景依据。", 409)
        return view(db, existing, store)
    request = prepare(db, session, store, payload.reference_mode)
    if request.get("input_sha256") != payload.input_sha256:
        raise DomainError("SHORT_SCENE_INPUT_CHANGED", "采访内容或选景依据已变化，请重新查看后授权。", 409)
    existing = db.scalar(select(ShortScenePlan).where(
        ShortScenePlan.session_id == session.id, ShortScenePlan.input_sha256 == payload.input_sha256))
    if existing:
        return view(db, existing, store)
    if family.data_classification != "test" and not is_encrypted_family(family):
        raise DomainError("SHORT_SCENE_ENCRYPTION_REQUIRED", "真实家庭档案需要先启用加密。", 409)
    try:
        provider = get_llm_provider()
    except Exception as exc:
        raise DomainError("SHORT_SCENE_PROVIDER_UNAVAILABLE", "选景模型暂未配置，未发送任何内容。", 503) from exc
    if provider.provider_name != "qwen" or not callable(getattr(provider, "select_short_scene", None)):
        raise DomainError("SHORT_SCENE_PROVIDER_UNAVAILABLE", "当前为测试模型，未调用真实选景服务。", 503)
    context = current_auth.get()
    consent = ModelConsentEvent(
        id=str(uuid4()), family_id=family.id, session_id=session.id,
        actor_label=f"user:{context.user_id}" if context else "本机用户",
        purpose=PURPOSE, decision="granted", one_time=True, input_sha256=payload.input_sha256,
        data_classification=family.data_classification, used_at=now_utc(),
    )
    item = ShortScenePlan(
        id=str(uuid4()), family_id=family.id, session_id=session.id, consent_id=consent.id,
        idempotency_key=payload.idempotency_key, input_sha256=payload.input_sha256,
        reference_mode=payload.reference_mode, provider=provider.provider_name, model=provider.model_name,
        request_payload=request, result_payload={}, status="dispatching", deadline_at=now_utc() + timedelta(minutes=2),
    )
    try:
        db.add(consent)
        db.flush()
        db.add(item)
        db.flush()
        protect_values(db, family, consent, {"actor_label": consent.actor_label}, store)
        protect_values(db, family, item, {"request_payload": request, "result_payload": {}}, store)
        db.commit()  # Unique input and consumed consent committed BEFORE the single external call.
    except IntegrityError:
        db.rollback()
        winner = db.scalar(select(ShortScenePlan).where(ShortScenePlan.session_id == session.id,
                              ShortScenePlan.input_sha256 == payload.input_sha256))
        if winner:
            return view(db, winner, store)
        raise DomainError("SHORT_SCENE_KEY_CONFLICT", "请求已提交，请刷新查看已有任务。", 409)
    if get_settings().formal_auth_required:
        try:
            if persist_configured_database_snapshot(get_settings()) is None:
                raise RuntimeError("SNAPSHOT_NOT_CONFIGURED")
        except Exception as exc:
            item.status, item.error_code = "not_dispatched", "SHORT_SCENE_DURABILITY_FAILED"
            db.commit()
            raise DomainError("SHORT_SCENE_DURABILITY_FAILED", "选景请求尚未安全保存，未发送文字。", 503) from exc
    try:
        response = provider.select_short_scene(request["system_prompt"], request["user_content"])
        result = validate_selection_response(request, response, input_sha256=payload.input_sha256)
    except ValueError:
        item.status, item.error_code = "invalid_proposal", "SHORT_SCENE_PROPOSAL_INVALID"
    except Exception:
        # No exception body is stored: SDK messages may contain text or credentials.
        item.status, item.error_code = "outcome_unknown", "SHORT_SCENE_PROVIDER_OUTCOME_UNKNOWN"
    else:
        item.status = result["status"]
        item.result_payload = result
        db.expire(family)  # Encryption may have been activated while the model was responding.
        protect_values(db, family, item, {"result_payload": result}, store)
    db.commit()
    return view(db, item, store)
