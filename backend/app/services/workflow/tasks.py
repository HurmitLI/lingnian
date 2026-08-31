from __future__ import annotations

import hashlib
import os
import tempfile
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
from app.services.security import (
    TEXT_PLACEHOLDER,
    decrypt_media_file,
    get_secret_store,
    is_encrypted_family,
    media_context,
    protect_field,
    require_family_master_key,
    reveal_field,
)
from app.services.security.key_store import SecretStore
from app.services.workflow.fact_guard import detect_added_facts
from app.services.workflow.state import transition


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def _protect_values(db, family, obj, values: dict[str, object], key: bytes | None) -> None:
    if not is_encrypted_family(family):
        return
    if key is None:
        raise RuntimeError("MASTER_KEY_MISSING")
    for field_name, value in values.items():
        protect_field(
            db,
            family=family,
            object_type=obj.__tablename__,
            object_id=obj.id,
            field_name=field_name,
            value=value,
            master_key=key,
        )
        if value is not None:
            current = getattr(obj, field_name)
            setattr(
                obj,
                field_name,
                []
                if isinstance(current, list)
                else {}
                if isinstance(current, dict)
                else TEXT_PLACEHOLDER,
            )


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


def process_transcription(task_id: str, secret_store: SecretStore | None = None) -> None:
    settings = get_settings()
    temporary_paths: list[Path] = []
    try:
        with SessionLocal() as db:
            task = db.get(WorkflowTask, task_id)
            if not task or task.status not in {"queued", "failed_retryable"}:
                return
            session = db.get(MemorySession, task.session_id)
            if not session or session.status in {"SKIPPED", "ARCHIVED"}:
                return
            family = session.elder.person.family
            store = secret_store or get_secret_store()
            key = require_family_master_key(family, store) if is_encrypted_family(family) else None
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

            stored_source_path = resolve_controlled_path(
                settings.resolved_asset_root, original.relative_path
            )
            source_path = stored_source_path
            if original.encryption_version == 1:
                runtime_dir = settings.resolved_asset_root / "runtime" / "workflow"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(
                    prefix=f"{original.id}-", suffix=".source", dir=runtime_dir
                )
                os.close(descriptor)
                source_path = Path(name)
                source_path.unlink(missing_ok=True)
                temporary_paths.append(source_path)
                decrypted = decrypt_media_file(
                    stored_source_path,
                    source_path,
                    key,
                    associated_data=media_context(family.id, original.id),
                )
                if (
                    decrypted.plaintext_size != original.plaintext_size_bytes
                    or decrypted.plaintext_sha256 != original.plaintext_sha256
                ):
                    raise RuntimeError("ASSET_PLAINTEXT_INTEGRITY_FAILED")
            if is_encrypted_family(family):
                runtime_dir = settings.resolved_asset_root / "runtime" / "workflow"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(
                    prefix=f"{original.id}-", suffix=".wav", dir=runtime_dir
                )
                os.close(descriptor)
                derived_path = Path(name)
                derived_path.unlink(missing_ok=True)
                temporary_paths.append(derived_path)
                derived_relative = None
            else:
                derived_relative = f"assets/derived/{session.id}/{original.id}.wav"
                derived_path = resolve_controlled_path(
                    settings.resolved_asset_root, derived_relative
                )
            normalize_audio(source_path, derived_path)
            derived_path.chmod(0o600)

            if derived_relative is not None:
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
            _protect_values(
                db,
                family,
                transcript,
                {
                    "raw_text": result.text,
                    "corrected_text": result.text,
                    "asr_metadata": result.metadata,
                },
                key,
            )

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
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)


def process_organization(task_id: str, secret_store: SecretStore | None = None) -> None:
    try:
        with SessionLocal() as db:
            task = db.get(WorkflowTask, task_id)
            if not task or task.status not in {"queued", "failed_retryable"}:
                return
            session = db.get(MemorySession, task.session_id)
            if not session or session.status in {"SKIPPED", "ARCHIVED"}:
                return
            family = session.elder.person.family
            store = secret_store or get_secret_store()
            key = require_family_master_key(family, store) if is_encrypted_family(family) else None
            transcript = db.scalar(select(Transcript).where(Transcript.session_id == session.id))
            corrected_text = (
                _secure_value(db, family, transcript, "corrected_text", key)
                if transcript
                else ""
            )
            if not transcript or not corrected_text.strip():
                raise RuntimeError("NO_REVIEWED_TRANSCRIPT")
            question_text = _secure_value(db, family, session, "question_text", key)

            task.status = "running"
            task.progress = 20
            session.status = transition(session.status, "ORGANIZING")
            db.commit()

            provider = get_llm_provider()
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
                    corrected_text=corrected_text,
                    require_consumed=True,
                )
                if consent_error:
                    raise RuntimeError(consent_error)
            output = provider.organize_story(corrected_text, question_text)
            added_facts = detect_added_facts(corrected_text, output)

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
                for payload_key, value in payload.items():
                    setattr(draft, payload_key, value)
            else:
                draft = StoryDraft(session_id=session.id, **payload)
                db.add(draft)
                db.flush()
            _protect_values(
                db,
                family,
                draft,
                {
                    "title": output.title,
                    "body": output.body,
                    "timeline_mentions": payload["timeline_mentions"],
                    "people_mentions": output.people_mentions,
                    "uncertainties": output.uncertainties,
                    "added_facts": added_facts,
                },
                key,
            )

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
        elif message == "INSUFFICIENT_STORY_CONTENT":
            code = message
        elif message.startswith("MODEL_CONSENT_"):
            code = message
        elif "LLM_API_KEY" in message:
            code = "LLM_KEY_MISSING"
        _mark_failed(task_id, code)
