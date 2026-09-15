"""Explicitly synthetic text narration, feeding the existing recorded workflow."""
from __future__ import annotations

import asyncio
import io
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.api.routes import (require, get_db, get_secret_store, media_read,
                            protect_values, encrypt_asset_if_needed)
from app.core.config import get_settings
from app.core.errors import DomainError
from app.models import MemorySession, MediaAsset, WorkflowTask, ConsentEvent
from app.schemas.api import MediaAssetRead
from app.services.archive.assets import store_audio_upload
from app.services.database_snapshot import persist_configured_database_snapshot
from app.services.security.key_store import SecretStore

router = APIRouter(prefix="/api/v1")
VOICE = "zh-CN-XiaoxiaoNeural"


class NarrationCreate(BaseModel):
    text: str = Field(min_length=10, max_length=1500)
    external_speech_authorized: bool = False


async def synthesize_narration(text: str) -> bytes:
    import edge_tts
    chunks = []
    async for chunk in edge_tts.Communicate(text, VOICE, rate="+0%", pitch="+0Hz").stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    audio = b"".join(chunks)
    if not audio:
        raise ValueError("Speech service returned no audio")
    return audio


@router.post("/memory-sessions/{session_id}/text-narration", response_model=MediaAssetRead)
async def create_text_narration(session_id: str, payload: NarrationCreate,
                                db: Session = Depends(get_db),
                                store: SecretStore = Depends(get_secret_store)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次记录。")
    if not payload.external_speech_authorized:
        raise DomainError("SPEECH_CONSENT_REQUIRED", "请先同意把这段文字发送到语音服务生成 AI 配音。", 409)
    text = payload.text.strip()
    if len(text) < 10:
        raise DomainError("NARRATION_TOO_SHORT", "请填写至少十个字的故事。", 422)
    # One reserved call per session, persisted before dispatch. A network timeout
    # can retrieve the same result but must never silently synthesize it twice.
    binding = str(uuid5(NAMESPACE_URL, f"lingnian-narration:{session_id}:{VOICE}:{text}"))
    prior = db.scalar(select(WorkflowTask).where(WorkflowTask.session_id == session_id,
                                               WorkflowTask.task_type == "text_narration"))
    if prior:
        if prior.id != binding:
            raise DomainError("NARRATION_INPUT_CHANGED", "这次记录已有配音任务。修改文字请新建一段记录，保留已有声音。", 409)
        if prior.status == "succeeded" and prior.output_ref:
            return media_read(db, require(db, MediaAsset, prior.output_ref, "ASSET_NOT_FOUND", "配音记录不存在。"), store)
        raise DomainError("NARRATION_UNRESOLVED", "这次配音尚未确认完成，已保留原任务，不会重复发送。请刷新查看。", 409)
    if session.interview_mode != "single" or session.status not in {"PROMPT_READY", "RECORDING_PENDING"} or session.media_assets:
        raise DomainError("NARRATION_SESSION_NOT_EMPTY", "请新建一段记录后生成配音，不覆盖已有录音。", 409)
    task = WorkflowTask(id=binding, session_id=session_id, task_type="text_narration",
                        status="running", progress=5, attempt=1)
    db.add(task)
    db.add(ConsentEvent(action="synthetic_narration", actor_label="文字配音提交者",
                        object_type="memory_session", object_id=session_id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DomainError("NARRATION_ALREADY_RESERVED", "配音任务已保存，请刷新查看原任务。", 409) from exc
    persist_configured_database_snapshot()
    try:
        audio = await asyncio.wait_for(synthesize_narration(text), timeout=90)
        upload = UploadFile(io.BytesIO(audio), filename="AI合成配音-晓晓.mp3",
                            headers=Headers({"content-type": "audio/mpeg"}))
        stored = await store_audio_upload(upload, session_id, get_settings())
        asset = MediaAsset(session_id=session_id, kind="audio_original", status="ready",
                           is_original=False, **stored)
        db.add(asset)
        db.flush()
        family = session.elder.person.family
        protect_values(db, family, asset, {"original_filename": asset.original_filename}, store)
        encrypt_asset_if_needed(db, asset, family, store)
        task.output_ref, task.status, task.progress = asset.id, "succeeded", 100
        session.status = "AUDIO_UPLOADED"
        db.commit()
        persist_configured_database_snapshot()
        return media_read(db, asset, store)
    except Exception as exc:
        db.rollback()
        task = db.get(WorkflowTask, binding)
        if task and task.status != "succeeded":
            task.status = "needs_attention"
            task.error_code = "NARRATION_RESPONSE_UNRESOLVED"
            db.commit()
            persist_configured_database_snapshot()
        raise DomainError("NARRATION_UNRESOLVED", "配音暂未确认完成，已保存任务，不会用静音或重复请求代替。", 503) from exc
