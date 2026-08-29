from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    MediaAsset,
    MemorySession,
    ModelConsentEvent,
    StoryDraft,
    Transcript,
    WorkflowTask,
)
from app.services.archive.assets import resolve_controlled_path
from app.services.asr import get_asr_provider
from app.services.asr.audio import normalize_audio
from app.services.llm import get_llm_provider
from app.services.privacy import model_consent_error, requires_explicit_model_consent
from app.services.workflow.fact_guard import detect_added_facts
from app.services.workflow.state import transition


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mark_failed(task_id: str, code: str) -> None:
    with SessionLocal() as db:
        task = db.get(WorkflowTask, task_id)
        if not task:
            return
        session = db.get(MemorySession, task.session_id)
        task.status = "failed_retryable" if task.attempt < 3 else "failed_final"
        task.error_code = code
        task.progress = 0
        if session and session.status not in {"SKIPPED", "ARCHIVED"}:
            session.status = (
                "TRANSCRIPT_REVIEW"
                if task.task_type == "organization" and session.transcript
                else "FAILED_RETRYABLE"
            )
        db.commit()


def process_transcription(task_id: str) -> None:
    settings = get_settings()
    try:
        with SessionLocal() as db:
            task = db.get(WorkflowTask, task_id)
            if not task or task.status not in {"queued", "failed_retryable"}:
                return
            session = db.get(MemorySession, task.session_id)
            if not session or session.status in {"SKIPPED", "ARCHIVED"}:
                return
            task.status = "running"
            task.progress = 10
            session.status = transition(session.status, "TRANSCRIBING")
            db.commit()

            original = db.scalar(
                select(MediaAsset)
                .where(
                    MediaAsset.session_id == session.id,
                    MediaAsset.kind == "audio_original",
                    MediaAsset.is_original.is_(True),
                    MediaAsset.status == "ready",
                )
                .order_by(MediaAsset.created_at.desc())
            )
            if not original:
                raise RuntimeError("NO_AUDIO")

            source_path = resolve_controlled_path(settings.resolved_asset_root, original.relative_path)
            derived_relative = f"assets/derived/{session.id}/{original.id}.wav"
            derived_path = resolve_controlled_path(settings.resolved_asset_root, derived_relative)
            normalize_audio(source_path, derived_path)
            derived_path.chmod(0o600)

            derived = db.scalar(
                select(MediaAsset).where(MediaAsset.relative_path == derived_relative)
            )
            if not derived:
                derived = MediaAsset(
                    session_id=session.id,
                    kind="audio_normalized",
                    relative_path=derived_relative,
                    original_filename=f"{Path(original.original_filename).stem}.wav",
                    mime_type="audio/wav",
                    size_bytes=derived_path.stat().st_size,
                    sha256=sha256_file(derived_path),
                    status="ready",
                    is_original=False,
                )
                db.add(derived)
            task.progress = 45
            db.commit()

            result = get_asr_provider().transcribe(derived_path)
            transcript = db.scalar(select(Transcript).where(Transcript.session_id == session.id))
            if transcript:
                transcript.raw_text = result.text
                transcript.corrected_text = result.text
                transcript.version += 1
                transcript.asr_provider = result.provider
                transcript.asr_model = result.model
                transcript.asr_metadata = result.metadata
            else:
                transcript = Transcript(
                    session_id=session.id,
                    raw_text=result.text,
                    corrected_text=result.text,
                    version=1,
                    asr_provider=result.provider,
                    asr_model=result.model,
                    asr_metadata=result.metadata,
                )
                db.add(transcript)
                db.flush()

            task.status = "succeeded"
            task.progress = 100
            task.output_ref = transcript.id
            session.status = transition(session.status, "TRANSCRIPT_REVIEW")
            db.commit()
    except Exception as exc:
        code = "ASR_PROCESSING_FAILED"
        message = str(exc)
        if message in {"NO_AUDIO"}:
            code = message
        elif "FFmpeg" in message or "音频格式转换" in message:
            code = "AUDIO_NORMALIZATION_FAILED"
        elif "FunASR" in message:
            code = "ASR_NOT_INSTALLED"
        _mark_failed(task_id, code)


def process_organization(task_id: str) -> None:
    try:
        with SessionLocal() as db:
            task = db.get(WorkflowTask, task_id)
            if not task or task.status not in {"queued", "failed_retryable"}:
                return
            session = db.get(MemorySession, task.session_id)
            if not session or session.status in {"SKIPPED", "ARCHIVED"}:
                return
            transcript = db.scalar(select(Transcript).where(Transcript.session_id == session.id))
            if not transcript or not transcript.corrected_text.strip():
                raise RuntimeError("NO_REVIEWED_TRANSCRIPT")

            task.status = "running"
            task.progress = 20
            session.status = transition(session.status, "ORGANIZING")
            db.commit()

            provider = get_llm_provider()
            family = session.elder.person.family
            if requires_explicit_model_consent(
                provider.provider_name, family.data_classification
            ):
                consent = (
                    db.get(ModelConsentEvent, task.model_consent_event_id)
                    if task.model_consent_event_id
                    else None
                )
                consent_error = model_consent_error(
                    consent,
                    family_id=family.id,
                    session_id=session.id,
                    data_classification=family.data_classification,
                    corrected_text=transcript.corrected_text,
                    require_consumed=True,
                )
                if consent_error:
                    raise RuntimeError(consent_error)
            output = provider.organize_story(transcript.corrected_text, session.question_text)
            added_facts = detect_added_facts(transcript.corrected_text, output)

            draft = db.scalar(select(StoryDraft).where(StoryDraft.session_id == session.id))
            payload = {
                "transcript_version": transcript.version,
                "title": output.title,
                "body": output.body,
                "timeline_mentions": [item.model_dump() for item in output.timeline_mentions],
                "people_mentions": output.people_mentions,
                "uncertainties": output.uncertainties,
                "added_facts": added_facts,
                "source_coverage": output.source_coverage,
                "provider": provider.provider_name,
                "model": provider.model_name,
                "status": "pending_review",
            }
            if draft:
                for key, value in payload.items():
                    setattr(draft, key, value)
            else:
                draft = StoryDraft(session_id=session.id, **payload)
                db.add(draft)
                db.flush()

            task.status = "succeeded"
            task.progress = 100
            task.output_ref = draft.id
            session.status = transition(session.status, "DRAFT_REVIEW")
            db.commit()
    except Exception as exc:
        code = "LLM_PROCESSING_FAILED"
        message = str(exc)
        if message == "NO_REVIEWED_TRANSCRIPT":
            code = message
        elif message.startswith("MODEL_CONSENT_"):
            code = message
        elif "LLM_API_KEY" in message:
            code = "LLM_KEY_MISSING"
        _mark_failed(task_id, code)
