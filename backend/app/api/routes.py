from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import (
    ArchiveSecurity,
    ConsentEvent,
    ElderProfile,
    FamilyArchive,
    MediaAsset,
    MediaLink,
    MemoryBook,
    MemoryFact,
    MemorySession,
    ModelConsentEvent,
    Person,
    PersonRelationship,
    QuestionPrompt,
    Reminder,
    Story,
    StoryDraft,
    TimelineEvent,
    Transcript,
    TopicPreference,
    WorkflowTask,
)
from app.models.entities import now_utc
from app.schemas.api import (
    ConfirmDraftRequest,
    ElderProfileCreate,
    ElderProfileRead,
    FamilySecurityRead,
    FamilyCreate,
    FamilyRead,
    HealthRead,
    ElderMemoryContext,
    MediaAssetRead,
    MediaLinkRead,
    MemorySessionCreate,
    MemorySessionRead,
    ModelConsentCreate,
    ModelConsentRead,
    OrganizationTaskCreate,
    MemoryFactRead,
    MemoryBookCreate,
    MemoryBookRead,
    PersonCreate,
    PersonRead,
    PersonRelationshipCreate,
    PersonRelationshipRead,
    QuestionPromptRead,
    ReminderCreate,
    ReminderRead,
    RecoveryPackageCreate,
    SecurityActivationRequest,
    SecurityInitializeRequest,
    SessionDetail,
    StoryDraftRead,
    StoryRead,
    TaskRead,
    TaskRetryRequest,
    StageCoverage,
    TimelineEventRead,
    TimelineItem,
    TranscriptRead,
    TranscriptUpdate,
    TopicPreferenceRead,
    TopicPreferenceUpsert,
)
from app.services.archive.assets import (
    calculate_sha256,
    resolve_controlled_path,
    store_audio_upload,
    store_image_upload,
    verify_asset_integrity,
)
from app.services.memory import (
    ensure_question_bank,
    markdown_sha256,
    render_memory_book,
    render_memory_book_pdf,
    select_question,
    SelectedQuestion,
)
from app.services.privacy import (
    STORY_ORGANIZATION_PURPOSE,
    corrected_text_sha256,
    model_consent_error,
    requires_explicit_model_consent,
)
from app.services.security import (
    RecoveryPackageError,
    activate_archive_encryption,
    build_recovery_package,
    get_family_key_manager,
    get_secret_store,
    is_encrypted_family,
    protect_field,
    recover_master_key,
    require_family_master_key,
    reveal_field,
    TEXT_PLACEHOLDER,
    decrypt_media_file,
    encrypt_media_file,
    media_context,
)
from app.services.security.key_store import SecretStore, SecretStoreError
from app.services.workflow.state import transition
from app.services.workflow.tasks import process_organization, process_transcription


router = APIRouter(prefix="/api/v1")


def require(db: Session, model, object_id: str, code: str, message: str):
    item = db.get(model, object_id)
    if not item:
        raise DomainError(code, message, 404)
    return item


def family_master_key(family: FamilyArchive, store: SecretStore) -> bytes | None:
    if not is_encrypted_family(family):
        return None
    try:
        return require_family_master_key(family, store)
    except (SecretStoreError, ValueError) as exc:
        raise DomainError(
            "MASTER_KEY_MISSING",
            "无法解锁家庭档案，请使用离线恢复包恢复主密钥。",
            409,
        ) from exc


def secure_value(
    db: Session,
    family: FamilyArchive,
    obj,
    field_name: str,
    store: SecretStore,
    *,
    master_key: bytes | None = None,
):
    key = master_key if master_key is not None else family_master_key(family, store)
    try:
        return reveal_field(
            db,
            family=family,
            object_type=obj.__tablename__,
            object_id=obj.id,
            field_name=field_name,
            stored_value=getattr(obj, field_name),
            master_key=key,
        )
    except ValueError as exc:
        raise DomainError(
            "ARCHIVE_DECRYPTION_FAILED",
            "家庭档案无法解密，内容可能已损坏，请从备份恢复。",
            409,
        ) from exc


def elder_read(
    db: Session, profile: ElderProfile, store: SecretStore
) -> ElderProfileRead:
    family = profile.person.family
    key = family_master_key(family, store)
    return ElderProfileRead(
        id=profile.id,
        person_id=profile.person_id,
        family_id=profile.person.family_id,
        data_classification=profile.person.family.data_classification,
        display_name=secure_value(db, family, profile.person, "display_name", store, master_key=key),
        preferred_name=secure_value(db, family, profile, "preferred_name", store, master_key=key),
        birth_year=secure_value(db, family, profile, "birth_year", store, master_key=key),
        birth_era=secure_value(db, family, profile, "birth_era", store, master_key=key),
        native_place=secure_value(db, family, profile, "native_place", store, master_key=key),
        occupation_summary=secure_value(db, family, profile, "occupation_summary", store, master_key=key),
        created_at=profile.created_at,
    )


def media_read(db: Session, asset: MediaAsset, store: SecretStore) -> MediaAssetRead:
    family = asset.session.elder.person.family
    return MediaAssetRead(
        id=asset.id,
        kind=asset.kind,
        original_filename=secure_value(db, family, asset, "original_filename", store),
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        status=asset.status,
        is_original=asset.is_original,
        content_url=f"/api/v1/media-assets/{asset.id}/content",
    )


def person_read(db: Session, person: Person, store: SecretStore) -> PersonRead:
    return PersonRead(
        id=person.id,
        family_id=person.family_id,
        role=person.role,
        display_name=secure_value(
            db, person.family, person, "display_name", store
        ),
        created_at=person.created_at,
    )


def relationship_read(
    db: Session, relationship: PersonRelationship, store: SecretStore
) -> PersonRelationshipRead:
    family = relationship.family
    key = family_master_key(family, store)
    return PersonRelationshipRead(
        id=relationship.id,
        family_id=relationship.family_id,
        from_person_id=relationship.from_person_id,
        to_person_id=relationship.to_person_id,
        relationship_type=relationship.relationship_type,
        custom_label=secure_value(
            db, family, relationship, "custom_label", store, master_key=key
        ),
        confirmed_by=secure_value(
            db, family, relationship, "confirmed_by", store, master_key=key
        ),
        created_at=relationship.created_at,
    )


def media_link_read(
    db: Session, link: MediaLink, store: SecretStore
) -> MediaLinkRead:
    family = link.media_asset.session.elder.person.family
    key = family_master_key(family, store)
    return MediaLinkRead(
        id=link.id,
        media_asset_id=link.media_asset_id,
        elder_id=link.elder_id,
        trigger_kind=link.trigger_kind,
        user_annotation=secure_value(
            db, family, link, "user_annotation", store, master_key=key
        ),
        width=link.width,
        height=link.height,
        model_inference=secure_value(
            db, family, link, "model_inference", store, master_key=key
        ),
        created_at=link.created_at,
    )


def memory_session_read(
    db: Session, session: MemorySession, store: SecretStore
) -> MemorySessionRead:
    family = session.elder.person.family
    return MemorySessionRead(
        id=session.id,
        elder_id=session.elder_id,
        life_stage=session.life_stage,
        prompt_id=session.prompt_id,
        question_text=secure_value(db, family, session, "question_text", store),
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def transcript_read(
    db: Session, transcript: Transcript, store: SecretStore
) -> TranscriptRead:
    family = transcript.session.elder.person.family
    key = family_master_key(family, store)
    return TranscriptRead(
        id=transcript.id,
        session_id=transcript.session_id,
        raw_text=secure_value(db, family, transcript, "raw_text", store, master_key=key),
        corrected_text=secure_value(
            db, family, transcript, "corrected_text", store, master_key=key
        ),
        version=transcript.version,
        asr_provider=transcript.asr_provider,
        asr_model=transcript.asr_model,
        asr_metadata=secure_value(
            db, family, transcript, "asr_metadata", store, master_key=key
        ),
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
    )


def story_draft_read(
    db: Session, draft: StoryDraft, store: SecretStore
) -> StoryDraftRead:
    family = draft.session.elder.person.family
    key = family_master_key(family, store)
    return StoryDraftRead(
        id=draft.id,
        session_id=draft.session_id,
        transcript_version=draft.transcript_version,
        title=secure_value(db, family, draft, "title", store, master_key=key),
        body=secure_value(db, family, draft, "body", store, master_key=key),
        timeline_mentions=secure_value(
            db, family, draft, "timeline_mentions", store, master_key=key
        ),
        people_mentions=secure_value(
            db, family, draft, "people_mentions", store, master_key=key
        ),
        uncertainties=secure_value(
            db, family, draft, "uncertainties", store, master_key=key
        ),
        added_facts=secure_value(
            db, family, draft, "added_facts", store, master_key=key
        ),
        source_coverage=draft.source_coverage,
        provider=draft.provider,
        model=draft.model,
        status=draft.status,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def model_consent_read(
    db: Session, consent: ModelConsentEvent, store: SecretStore
) -> ModelConsentRead:
    family = consent.family if hasattr(consent, "family") else db.get(FamilyArchive, consent.family_id)
    return ModelConsentRead(
        id=consent.id,
        family_id=consent.family_id,
        session_id=consent.session_id,
        actor_label=secure_value(db, family, consent, "actor_label", store),
        purpose=consent.purpose,
        data_classification=consent.data_classification,
        decision=consent.decision,
        one_time=consent.one_time,
        used_at=consent.used_at,
        revoked_at=consent.revoked_at,
        created_at=consent.created_at,
    )


def topic_preference_read(
    db: Session, preference: TopicPreference, store: SecretStore
) -> TopicPreferenceRead:
    family = preference.elder.person.family
    key = family_master_key(family, store)
    return TopicPreferenceRead(
        id=preference.id,
        elder_id=preference.elder_id,
        topic_key=preference.topic_key,
        preference=preference.preference,
        note=secure_value(db, family, preference, "note", store, master_key=key),
        updated_by=secure_value(
            db, family, preference, "updated_by", store, master_key=key
        ),
        updated_at=preference.updated_at,
    )


def memory_fact_read(
    db: Session, fact: MemoryFact, store: SecretStore
) -> MemoryFactRead:
    family = fact.elder.person.family
    key = family_master_key(family, store)
    return MemoryFactRead(
        id=fact.id,
        elder_id=fact.elder_id,
        story_id=fact.story_id,
        fact_type=fact.fact_type,
        subject_label=secure_value(
            db, family, fact, "subject_label", store, master_key=key
        ),
        value_text=secure_value(
            db, family, fact, "value_text", store, master_key=key
        ),
        content_sha256=fact.content_sha256,
        confidence=fact.confidence,
        status=fact.status,
        created_at=fact.created_at,
    )


def story_read(db: Session, story: Story, store: SecretStore) -> StoryRead:
    family = story.elder.person.family
    key = family_master_key(family, store)
    return StoryRead(
        id=story.id,
        elder_id=story.elder_id,
        source_draft_id=story.source_draft_id,
        title=secure_value(db, family, story, "title", store, master_key=key),
        body=secure_value(db, family, story, "body", store, master_key=key),
        confirmed_by=secure_value(
            db, family, story, "confirmed_by", store, master_key=key
        ),
        confirmed_at=story.confirmed_at,
    )


def timeline_event_read(
    db: Session, event: TimelineEvent, family: FamilyArchive, store: SecretStore
) -> TimelineEventRead:
    key = family_master_key(family, store)
    return TimelineEventRead(
        id=event.id,
        story_id=event.story_id,
        time_expression=secure_value(
            db, family, event, "time_expression", store, master_key=key
        ),
        normalized_time=secure_value(
            db, family, event, "normalized_time", store, master_key=key
        ),
        confidence=event.confidence,
    )


def memory_book_read(
    db: Session, book: MemoryBook, store: SecretStore
) -> MemoryBookRead:
    family = book.elder.person.family
    key = family_master_key(family, store)
    return MemoryBookRead(
        id=book.id,
        elder_id=book.elder_id,
        version=book.version,
        title=secure_value(db, family, book, "title", store, master_key=key),
        content_sha256=book.content_sha256,
        story_manifest=secure_value(
            db, family, book, "story_manifest", store, master_key=key
        ),
        created_by=secure_value(
            db, family, book, "created_by", store, master_key=key
        ),
        status=book.status,
        pdf_status=book.pdf_status,
        pdf_sha256=book.pdf_sha256,
        created_at=book.created_at,
    )


def decrypted_book_sources(
    db: Session,
    profile: ElderProfile,
    stories: list[Story],
    store: SecretStore,
):
    family = profile.person.family
    key = family_master_key(family, store)
    profile_view = SimpleNamespace(
        preferred_name=secure_value(
            db, family, profile, "preferred_name", store, master_key=key
        )
    )
    story_views = []
    for story in stories:
        session_view = SimpleNamespace(life_stage=story.source_draft.session.life_stage)
        draft_view = SimpleNamespace(session=session_view)
        story_views.append(
            SimpleNamespace(
                id=story.id,
                source_draft_id=story.source_draft_id,
                source_draft=draft_view,
                title=secure_value(
                    db, family, story, "title", store, master_key=key
                ),
                body=secure_value(db, family, story, "body", store, master_key=key),
                confirmed_by=secure_value(
                    db, family, story, "confirmed_by", store, master_key=key
                ),
                confirmed_at=story.confirmed_at,
            )
        )
    return profile_view, story_views


def protect_values(
    db: Session,
    family: FamilyArchive,
    obj,
    values: dict[str, object],
    store: SecretStore,
) -> None:
    if not is_encrypted_family(family):
        return
    key = family_master_key(family, store)
    if key is None:
        raise DomainError("MASTER_KEY_MISSING", "无法解锁家庭档案。", 409)
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
            setattr(obj, field_name, [] if isinstance(current, list) else {} if isinstance(current, dict) else None if isinstance(current, int) else TEXT_PLACEHOLDER)


def encrypt_asset_if_needed(
    db: Session,
    asset: MediaAsset,
    family: FamilyArchive,
    store: SecretStore,
) -> None:
    if not is_encrypted_family(family):
        return
    key = family_master_key(family, store)
    if key is None:
        raise DomainError("MASTER_KEY_MISSING", "无法解锁家庭档案。", 409)
    path = resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path)
    temp = path.with_name(f".{path.name}.encrypting-{asset.id}")
    try:
        result = encrypt_media_file(
            path,
            temp,
            key,
            associated_data=media_context(family.id, asset.id),
        )
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise
    asset.plaintext_size_bytes = result.plaintext_size
    asset.plaintext_sha256 = result.plaintext_sha256
    asset.size_bytes = result.ciphertext_size
    asset.sha256 = result.ciphertext_sha256
    asset.encryption_version = 1


def reminder_read(reminder: Reminder) -> ReminderRead:
    def as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    display_status = reminder.status
    if (
        reminder.status == "scheduled"
        and reminder.remind_at <= datetime.now(UTC).replace(tzinfo=None)
    ):
        display_status = "due"
    return ReminderRead(
        id=reminder.id,
        elder_id=reminder.elder_id,
        topic_key=reminder.topic_key,
        remind_at=as_utc(reminder.remind_at),
        status=display_status,
        idempotency_key=reminder.idempotency_key,
        last_shown_at=as_utc(reminder.last_shown_at),
        show_count=reminder.show_count,
        created_at=as_utc(reminder.created_at),
        updated_at=as_utc(reminder.updated_at),
    )


@router.get("/health", response_model=HealthRead)
def health() -> HealthRead:
    settings = get_settings()
    return HealthRead(
        environment=settings.app_env,
        asr_provider=settings.asr_provider,
        llm_provider=settings.llm_provider,
    )


@router.post("/families", response_model=FamilyRead, status_code=201)
def create_family(payload: FamilyCreate, db: Session = Depends(get_db)) -> FamilyArchive:
    if payload.data_classification != "test":
        raise DomainError(
            "REAL_DATA_MODE_LOCKED",
            "真实资料模式要在完成本机加密与恢复演练后单独开启；当前只能创建虚构测试档案。",
            409,
        )
    if payload.idempotency_key:
        existing = db.scalar(
            select(FamilyArchive).where(
                FamilyArchive.idempotency_key == payload.idempotency_key
            )
        )
        if existing:
            return existing
    family = FamilyArchive(
        display_name=payload.display_name.strip(),
        idempotency_key=payload.idempotency_key,
        data_classification=payload.data_classification,
    )
    db.add(family)
    db.commit()
    db.refresh(family)
    return family


def family_security_read(
    family: FamilyArchive,
    metadata: ArchiveSecurity | None,
    *,
    key_initialized: bool | None = None,
) -> FamilySecurityRead:
    return FamilySecurityRead(
        family_id=family.id,
        key_version=metadata.key_version if metadata else None,
        encryption_status=metadata.encryption_status if metadata else "not_initialized",
        key_initialized=(metadata is not None) if key_initialized is None else key_initialized,
        recovery_package_created_at=metadata.recovery_package_created_at if metadata else None,
        recovery_verified_at=metadata.recovery_verified_at if metadata else None,
        activated_at=metadata.activated_at if metadata else None,
    )


@router.get("/families/{family_id}/security", response_model=FamilySecurityRead)
def get_family_security(
    family_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FamilySecurityRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    metadata = db.scalar(
        select(ArchiveSecurity).where(ArchiveSecurity.family_id == family.id)
    )
    key_present = False
    if metadata:
        try:
            key_present = (
                get_family_key_manager(
                    family.id, secret_store, key_version=metadata.key_version
                ).get_existing()
                is not None
            )
        except SecretStoreError:
            key_present = False
    return family_security_read(family, metadata, key_initialized=key_present)


@router.post(
    "/families/{family_id}/security/initialize",
    response_model=FamilySecurityRead,
    status_code=201,
)
def initialize_family_security(
    family_id: str,
    payload: SecurityInitializeRequest,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FamilySecurityRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    metadata = db.scalar(
        select(ArchiveSecurity).where(ArchiveSecurity.family_id == family.id)
    )
    key_version = metadata.key_version if metadata else 1
    try:
        get_family_key_manager(
            family.id, secret_store, key_version=key_version
        ).get_or_create()
    except SecretStoreError as exc:
        raise DomainError(
            "KEYCHAIN_UNAVAILABLE", "暂时无法使用 Mac 钥匙串，请解锁后重试。", 503
        ) from exc

    if metadata is None:
        metadata = ArchiveSecurity(
            family_id=family.id,
            key_version=key_version,
            encryption_status="key_ready",
        )
        db.add(metadata)
        db.add(
            ConsentEvent(
                action="initialize_archive_security",
                actor_label=payload.actor_label.strip(),
                object_type="family_archive",
                object_id=family.id,
            )
        )
        db.commit()
        db.refresh(metadata)
    return family_security_read(family, metadata, key_initialized=True)


@router.post("/families/{family_id}/security/recovery-package")
def create_recovery_package(
    family_id: str,
    payload: RecoveryPackageCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> Response:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    metadata = db.scalar(
        select(ArchiveSecurity).where(ArchiveSecurity.family_id == family.id)
    )
    if metadata is None:
        raise DomainError("SECURITY_NOT_INITIALIZED", "请先初始化家庭档案安全设置。", 409)
    try:
        master_key = get_family_key_manager(
            family.id, secret_store, key_version=metadata.key_version
        ).get_existing()
    except SecretStoreError as exc:
        raise DomainError(
            "KEYCHAIN_UNAVAILABLE", "暂时无法从 Mac 钥匙串读取家庭档案密钥。", 503
        ) from exc
    if master_key is None:
        raise DomainError(
            "MASTER_KEY_MISSING", "Mac 钥匙串中没有找到家庭档案密钥，请使用恢复包恢复。", 409
        )

    package_json = build_recovery_package(
        master_key,
        payload.recovery_passphrase.get_secret_value(),
        scope=f"family:{family.id}",
    )
    metadata.recovery_package_created_at = now_utc()
    db.add(
        ConsentEvent(
            action="export_recovery_package",
            actor_label=payload.actor_label.strip(),
            object_type="family_archive",
            object_id=family.id,
        )
    )
    db.commit()
    filename = f"niannian-recovery-{family.id}.json"
    return Response(
        content=package_json,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/families/{family_id}/security/verify-recovery",
    response_model=FamilySecurityRead,
)
async def verify_recovery_package(
    family_id: str,
    package: UploadFile = File(...),
    recovery_passphrase: str = Form(..., min_length=12, max_length=200),
    actor_label: str = Form(..., min_length=1, max_length=80),
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FamilySecurityRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    metadata = db.scalar(
        select(ArchiveSecurity).where(ArchiveSecurity.family_id == family.id)
    )
    if metadata is None:
        raise DomainError("SECURITY_NOT_INITIALIZED", "请先初始化家庭档案安全设置。", 409)
    try:
        package_bytes = await package.read(512 * 1024 + 1)
    finally:
        await package.close()
    if len(package_bytes) > 512 * 1024:
        raise DomainError("RECOVERY_PACKAGE_TOO_LARGE", "恢复包文件过大。", 413)
    try:
        recovered_key = recover_master_key(
            package_bytes.decode("utf-8"),
            recovery_passphrase,
            expected_scope=f"family:{family.id}",
        )
        get_family_key_manager(
            family.id, secret_store, key_version=metadata.key_version
        ).import_recovered(recovered_key, overwrite=False)
    except UnicodeDecodeError as exc:
        raise DomainError("RECOVERY_PACKAGE_INVALID", "恢复包不是有效的 UTF-8 JSON 文件。", 422) from exc
    except RecoveryPackageError as exc:
        raise DomainError("RECOVERY_VERIFICATION_FAILED", str(exc), 409) from exc
    except SecretStoreError as exc:
        raise DomainError("KEYCHAIN_CONFLICT", str(exc), 409) from exc
    metadata.recovery_verified_at = now_utc()
    db.add(
        ConsentEvent(
            action="verify_recovery_package",
            actor_label=actor_label.strip(),
            object_type="family_archive",
            object_id=family.id,
        )
    )
    db.commit()
    db.refresh(metadata)
    return family_security_read(family, metadata, key_initialized=True)


@router.post(
    "/families/{family_id}/security/activate",
    response_model=FamilySecurityRead,
)
def activate_family_security(
    family_id: str,
    payload: SecurityActivationRequest,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FamilySecurityRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    metadata = db.scalar(
        select(ArchiveSecurity).where(ArchiveSecurity.family_id == family.id)
    )
    if metadata is None:
        raise DomainError("SECURITY_NOT_INITIALIZED", "请先初始化家庭档案安全设置。", 409)
    try:
        master_key = get_family_key_manager(
            family.id, secret_store, key_version=metadata.key_version
        ).get_existing()
        if master_key is None:
            raise DomainError(
                "MASTER_KEY_MISSING", "Mac 钥匙串中没有找到家庭档案密钥。", 409
            )
        activate_archive_encryption(
            db,
            family=family,
            metadata=metadata,
            master_key=master_key,
            target_classification=payload.data_classification,
            settings=get_settings(),
        )
    except DomainError:
        raise
    except SecretStoreError as exc:
        raise DomainError("KEYCHAIN_UNAVAILABLE", "暂时无法从 Mac 钥匙串读取密钥。", 503) from exc
    except ValueError as exc:
        messages = {
            "RECOVERY_PACKAGE_REQUIRED": "请先下载离线恢复包。",
            "RECOVERY_VERIFICATION_REQUIRED": "请先上传恢复包并完成恢复验证。",
            "ASSET_INTEGRITY_FAILED": "媒体已损坏，请先从备份恢复。",
        }
        raise DomainError(
            "ARCHIVE_ENCRYPTION_FAILED",
            messages.get(str(exc), "家庭档案加密失败，已停止启用真实资料。"),
            409,
        ) from exc
    db.refresh(family)
    db.refresh(metadata)
    activation_event = ConsentEvent(
        action="activate_encrypted_archive",
        actor_label=payload.actor_label.strip(),
        object_type="family_archive",
        object_id=family.id,
    )
    db.add(activation_event)
    db.flush()
    protect_values(
        db,
        family,
        activation_event,
        {"actor_label": payload.actor_label.strip()},
        secret_store,
    )
    db.commit()
    return family_security_read(family, metadata, key_initialized=True)


@router.post("/elder-profiles", response_model=ElderProfileRead, status_code=201)
def create_elder_profile(
    payload: ElderProfileCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ElderProfileRead:
    family = require(db, FamilyArchive, payload.family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    person = Person(
        family_id=payload.family_id,
        role="elder",
        display_name=payload.display_name.strip(),
    )
    profile = ElderProfile(
        person=person,
        preferred_name=payload.preferred_name.strip(),
        birth_year=payload.birth_year,
        birth_era=payload.birth_era,
        native_place=payload.native_place,
        occupation_summary=payload.occupation_summary,
    )
    db.add(profile)
    db.flush()
    protect_values(
        db,
        family,
        person,
        {"display_name": payload.display_name.strip()},
        secret_store,
    )
    protect_values(
        db,
        family,
        profile,
        {
            "preferred_name": payload.preferred_name.strip(),
            "birth_year": payload.birth_year,
            "birth_era": payload.birth_era,
            "native_place": payload.native_place,
            "occupation_summary": payload.occupation_summary,
        },
        secret_store,
    )
    db.commit()
    db.refresh(profile)
    return elder_read(db, profile, secret_store)


@router.post(
    "/families/{family_id}/people", response_model=PersonRead, status_code=201
)
def create_family_person(
    family_id: str,
    payload: PersonCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> PersonRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    person = Person(
        family_id=family_id,
        role=payload.role.strip(),
        display_name=payload.display_name.strip(),
    )
    db.add(person)
    db.flush()
    protect_values(
        db, family, person, {"display_name": payload.display_name.strip()}, secret_store
    )
    db.commit()
    db.refresh(person)
    return person_read(db, person, secret_store)


@router.get("/families/{family_id}/people", response_model=list[PersonRead])
def list_family_people(
    family_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[PersonRead]:
    require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    people = list(
        db.scalars(
            select(Person)
            .where(Person.family_id == family_id)
            .order_by(Person.created_at)
        ).all()
    )
    return [person_read(db, item, secret_store) for item in people]


@router.post(
    "/families/{family_id}/relationships",
    response_model=PersonRelationshipRead,
    status_code=201,
)
def create_person_relationship(
    family_id: str,
    payload: PersonRelationshipCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> PersonRelationshipRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    from_person = require(
        db, Person, payload.from_person_id, "PERSON_NOT_FOUND", "没有找到关系起点人物。"
    )
    to_person = require(
        db, Person, payload.to_person_id, "PERSON_NOT_FOUND", "没有找到关系终点人物。"
    )
    if from_person.id == to_person.id:
        raise DomainError("RELATIONSHIP_SELF_REFERENCE", "不能把同一个人关联为自己的亲属。", 409)
    if from_person.family_id != family_id or to_person.family_id != family_id:
        raise DomainError(
            "RELATIONSHIP_FAMILY_MISMATCH", "关系中的两个人必须属于同一个家庭档案。", 409
        )
    if payload.relationship_type == "custom" and not payload.custom_label:
        raise DomainError("CUSTOM_RELATIONSHIP_LABEL_REQUIRED", "自定义关系需要填写称谓。", 422)
    existing = db.scalar(
        select(PersonRelationship).where(
            PersonRelationship.from_person_id == from_person.id,
            PersonRelationship.to_person_id == to_person.id,
            PersonRelationship.relationship_type == payload.relationship_type,
        )
    )
    if existing:
        return relationship_read(db, existing, secret_store)
    relationship = PersonRelationship(
        family_id=family_id,
        from_person_id=from_person.id,
        to_person_id=to_person.id,
        relationship_type=payload.relationship_type,
        custom_label=payload.custom_label.strip() if payload.custom_label else None,
        confirmed_by=payload.confirmed_by.strip(),
    )
    db.add(relationship)
    db.flush()
    protect_values(
        db,
        family,
        relationship,
        {
            "custom_label": payload.custom_label.strip() if payload.custom_label else None,
            "confirmed_by": payload.confirmed_by.strip(),
        },
        secret_store,
    )
    db.commit()
    db.refresh(relationship)
    return relationship_read(db, relationship, secret_store)


@router.get(
    "/families/{family_id}/relationships",
    response_model=list[PersonRelationshipRead],
)
def list_person_relationships(
    family_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[PersonRelationshipRead]:
    require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    relationships = list(
        db.scalars(
            select(PersonRelationship)
            .where(PersonRelationship.family_id == family_id)
            .order_by(PersonRelationship.created_at)
        ).all()
    )
    return [relationship_read(db, item, secret_store) for item in relationships]


@router.get("/elder-profiles", response_model=list[ElderProfileRead])
def list_elder_profiles(
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[ElderProfileRead]:
    profiles = db.scalars(select(ElderProfile).order_by(ElderProfile.created_at.desc())).all()
    return [elder_read(db, profile, secret_store) for profile in profiles]


@router.get("/elder-profiles/{profile_id}", response_model=ElderProfileRead)
def get_elder_profile(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ElderProfileRead:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。"
    )
    return elder_read(db, profile, secret_store)


@router.put(
    "/elder-profiles/{profile_id}/topic-preferences/{topic_key}",
    response_model=TopicPreferenceRead,
)
def upsert_topic_preference(
    profile_id: str,
    topic_key: str,
    payload: TopicPreferenceUpsert,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> TopicPreferenceRead:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    normalized_topic = topic_key.strip()
    if payload.topic_key.strip() != normalized_topic:
        raise DomainError("TOPIC_KEY_MISMATCH", "路径和内容中的话题不一致。", 422)
    preference = db.scalar(
        select(TopicPreference).where(
            TopicPreference.elder_id == profile_id,
            TopicPreference.topic_key == normalized_topic,
        )
    )
    values = {
        "preference": payload.preference,
        "note": payload.note.strip() if payload.note else None,
        "updated_by": payload.updated_by.strip(),
    }
    if preference:
        for key, value in values.items():
            setattr(preference, key, value)
    else:
        preference = TopicPreference(
            elder_id=profile_id,
            topic_key=normalized_topic,
            **values,
        )
        db.add(preference)
    db.flush()
    protect_values(
        db,
        profile.person.family,
        preference,
        {"note": values["note"], "updated_by": values["updated_by"]},
        secret_store,
    )
    if payload.preference == "avoid":
        reminders = db.scalars(
            select(Reminder).where(
                Reminder.elder_id == profile_id,
                Reminder.topic_key == normalized_topic,
                Reminder.status.in_(["scheduled", "due"]),
            )
        ).all()
        for reminder in reminders:
            reminder.status = "paused_by_preference"
    db.commit()
    db.refresh(preference)
    return topic_preference_read(db, preference, secret_store)


@router.get(
    "/elder-profiles/{profile_id}/topic-preferences",
    response_model=list[TopicPreferenceRead],
)
def list_topic_preferences(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[TopicPreferenceRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    preferences = list(
        db.scalars(
            select(TopicPreference)
            .where(TopicPreference.elder_id == profile_id)
            .order_by(TopicPreference.topic_key)
        ).all()
    )
    return [topic_preference_read(db, item, secret_store) for item in preferences]


@router.get("/question-prompts", response_model=list[QuestionPromptRead])
def list_question_prompts(db: Session = Depends(get_db)) -> list[QuestionPrompt]:
    ensure_question_bank(db)
    db.commit()
    return list(
        db.scalars(
            select(QuestionPrompt)
            .where(QuestionPrompt.enabled.is_(True))
            .order_by(QuestionPrompt.life_stage, QuestionPrompt.prompt_key)
        ).all()
    )


@router.get(
    "/elder-profiles/{profile_id}/memory-context",
    response_model=ElderMemoryContext,
)
def get_elder_memory_context(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ElderMemoryContext:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。"
    )
    sessions = db.scalars(
        select(MemorySession).where(MemorySession.elder_id == profile.id)
    ).all()
    session_counts: dict[str, int] = {}
    for session in sessions:
        session_counts[session.life_stage] = session_counts.get(session.life_stage, 0) + 1
    confirmed_counts: dict[str, int] = {}
    for story in profile.stories:
        stage = story.source_draft.session.life_stage
        confirmed_counts[stage] = confirmed_counts.get(stage, 0) + 1
    stages = sorted(set(session_counts) | set(confirmed_counts))
    return ElderMemoryContext(
        coverage=[
            StageCoverage(
                life_stage=stage,
                session_count=session_counts.get(stage, 0),
                confirmed_story_count=confirmed_counts.get(stage, 0),
            )
            for stage in stages
        ],
        preferences=[
            topic_preference_read(db, item, secret_store)
            for item in profile.topic_preferences
        ],
        confirmed_facts=[
            memory_fact_read(db, item, secret_store)
            for item in profile.memory_facts
            if item.status == "active"
        ],
    )


@router.post(
    "/elder-profiles/{profile_id}/reminders",
    response_model=ReminderRead,
    status_code=201,
)
def create_reminder(
    profile_id: str,
    payload: ReminderCreate,
    db: Session = Depends(get_db),
) -> ReminderRead:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    existing = db.scalar(
        select(Reminder).where(Reminder.idempotency_key == payload.idempotency_key)
    )
    if existing:
        if existing.elder_id != profile_id:
            raise DomainError(
                "REMINDER_IDEMPOTENCY_CONFLICT", "这个提醒请求标识已经被其他档案使用。", 409
            )
        return reminder_read(existing)
    preference = db.scalar(
        select(TopicPreference).where(
            TopicPreference.elder_id == profile_id,
            TopicPreference.topic_key == payload.topic_key.strip(),
        )
    )
    if preference and preference.preference == "avoid":
        raise DomainError(
            "REMINDER_BLOCKED_BY_PREFERENCE",
            "讲述者已经选择不要再问这个话题，不能创建提醒。",
            409,
        )
    remind_at = payload.remind_at.astimezone(UTC).replace(tzinfo=None)
    reminder = Reminder(
        elder_id=profile_id,
        topic_key=payload.topic_key.strip(),
        remind_at=remind_at,
        status="scheduled",
        idempotency_key=payload.idempotency_key,
        show_count=0,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder_read(reminder)


@router.get(
    "/elder-profiles/{profile_id}/reminders", response_model=list[ReminderRead]
)
def list_reminders(
    profile_id: str, db: Session = Depends(get_db)
) -> list[ReminderRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    reminders = db.scalars(
        select(Reminder)
        .where(Reminder.elder_id == profile_id)
        .order_by(Reminder.remind_at.desc())
    ).all()
    return [reminder_read(item) for item in reminders]


@router.get(
    "/elder-profiles/{profile_id}/reminders/due",
    response_model=list[ReminderRead],
)
def list_due_reminders(
    profile_id: str, db: Session = Depends(get_db)
) -> list[ReminderRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    current_utc = datetime.now(UTC).replace(tzinfo=None)
    reminders = db.scalars(
        select(Reminder)
        .where(
            Reminder.elder_id == profile_id,
            Reminder.status == "scheduled",
            Reminder.remind_at <= current_utc,
        )
        .order_by(Reminder.remind_at)
    ).all()
    return [reminder_read(item) for item in reminders]


@router.post("/reminders/{reminder_id}/shown", response_model=ReminderRead)
def mark_reminder_shown(
    reminder_id: str, db: Session = Depends(get_db)
) -> ReminderRead:
    reminder = require(
        db, Reminder, reminder_id, "REMINDER_NOT_FOUND", "没有找到这个本机提醒。"
    )
    if reminder.status == "scheduled":
        reminder.status = "shown_once"
        reminder.show_count += 1
        reminder.last_shown_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
        db.refresh(reminder)
    return reminder_read(reminder)


@router.post("/reminders/{reminder_id}/dismiss", response_model=ReminderRead)
def dismiss_reminder(
    reminder_id: str, db: Session = Depends(get_db)
) -> ReminderRead:
    reminder = require(
        db, Reminder, reminder_id, "REMINDER_NOT_FOUND", "没有找到这个本机提醒。"
    )
    reminder.status = "dismissed"
    db.commit()
    db.refresh(reminder)
    return reminder_read(reminder)


@router.post(
    "/elder-profiles/{profile_id}/memory-books",
    response_model=MemoryBookRead,
    status_code=201,
)
def create_memory_book(
    profile_id: str,
    payload: MemoryBookCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MemoryBookRead:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。"
    )
    stories = sorted(profile.stories, key=lambda item: item.confirmed_at)
    if not stories:
        raise DomainError(
            "NO_CONFIRMED_STORIES", "至少需要一篇人工确认的故事才能生成回忆录。", 409
        )
    version = (
        db.scalar(
            select(func.max(MemoryBook.version)).where(MemoryBook.elder_id == profile.id)
        )
        or 0
    ) + 1
    profile_view, story_views = decrypted_book_sources(
        db, profile, stories, secret_store
    )
    title = payload.title.strip() if payload.title else f"{profile_view.preferred_name}的家庭回忆录"
    markdown_content, manifest = render_memory_book(
        profile_view, story_views, title=title
    )
    book = MemoryBook(
        elder_id=profile.id,
        version=version,
        title=title,
        markdown_content=markdown_content,
        content_sha256=markdown_sha256(markdown_content),
        story_manifest=manifest,
        created_by=payload.created_by.strip(),
        status="ready",
    )
    db.add(book)
    db.flush()
    protect_values(
        db,
        profile.person.family,
        book,
        {
            "title": title,
            "markdown_content": markdown_content,
            "story_manifest": manifest,
            "created_by": payload.created_by.strip(),
        },
        secret_store,
    )
    db.commit()
    db.refresh(book)
    return memory_book_read(db, book, secret_store)


@router.get(
    "/elder-profiles/{profile_id}/memory-books", response_model=list[MemoryBookRead]
)
def list_memory_books(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[MemoryBookRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    books = list(
        db.scalars(
            select(MemoryBook)
            .where(MemoryBook.elder_id == profile_id)
            .order_by(MemoryBook.version.desc())
        ).all()
    )
    return [memory_book_read(db, item, secret_store) for item in books]


@router.get("/memory-books/{book_id}/markdown")
def download_memory_book_markdown(
    book_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> Response:
    book = require(
        db, MemoryBook, book_id, "MEMORY_BOOK_NOT_FOUND", "没有找到这个回忆录版本。"
    )
    family = book.elder.person.family
    markdown_content = secure_value(
        db, family, book, "markdown_content", secret_store
    )
    if markdown_sha256(markdown_content) != book.content_sha256:
        raise DomainError(
            "MEMORY_BOOK_INTEGRITY_FAILED", "回忆录文件完整性校验失败，已阻止下载。", 409
        )
    return Response(
        content=markdown_content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="niannian-memory-book-v{book.version}.md"'
        },
    )


@router.get("/memory-books/{book_id}/pdf")
def download_memory_book_pdf(
    book_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FileResponse:
    book = require(
        db, MemoryBook, book_id, "MEMORY_BOOK_NOT_FOUND", "没有找到这个回忆录版本。"
    )
    settings = get_settings()
    family = book.elder.person.family
    key = family_master_key(family, secret_store)
    title = secure_value(db, family, book, "title", secret_store, master_key=key)
    manifest = secure_value(
        db, family, book, "story_manifest", secret_store, master_key=key
    )
    relative_path = f"exports/memory-books/{book.elder_id}/{book.id}.pdf"
    output_path = resolve_controlled_path(settings.resolved_asset_root, relative_path)
    needs_generation = (
        book.pdf_status != "ready"
        or book.pdf_relative_path != relative_path
        or not output_path.is_file()
    )
    if needs_generation:
        stories_by_id = {story.id: story for story in book.elder.stories}
        stories = [
            stories_by_id[item["story_id"]]
            for item in manifest
            if item.get("story_id") in stories_by_id
        ]
        profile_view, story_views = decrypted_book_sources(
            db, book.elder, stories, secret_store
        )
        temporary_path = output_path.with_suffix(".tmp.pdf")
        try:
            pdf_sha256 = render_memory_book_pdf(
                profile_view,
                story_views,
                title=title,
                output_path=temporary_path,
            )
            if is_encrypted_family(family):
                encrypted_temporary = output_path.with_suffix(".tmp.encrypted")
                result = encrypt_media_file(
                    temporary_path,
                    encrypted_temporary,
                    key,
                    associated_data=media_context(
                        family.id, f"memory-book-{book.id}"
                    ),
                )
                os.replace(encrypted_temporary, output_path)
                temporary_path.unlink(missing_ok=True)
                book.pdf_encryption_version = 1
                book.pdf_ciphertext_size = result.ciphertext_size
                book.pdf_ciphertext_sha256 = result.ciphertext_sha256
            else:
                temporary_path.replace(output_path)
        except Exception as exc:
            temporary_path.unlink(missing_ok=True)
            output_path.with_suffix(".tmp.encrypted").unlink(missing_ok=True)
            book.pdf_status = "failed"
            db.commit()
            raise DomainError(
                "MEMORY_BOOK_PDF_FAILED",
                "打印版 PDF 暂时生成失败，Markdown 版本仍可正常下载。",
                503,
            ) from exc
        book.pdf_status = "ready"
        book.pdf_relative_path = relative_path
        book.pdf_sha256 = pdf_sha256
        db.commit()
        db.refresh(book)
    expected_stored_sha = (
        book.pdf_ciphertext_sha256
        if book.pdf_encryption_version == 1
        else book.pdf_sha256
    )
    if not expected_stored_sha or calculate_sha256(output_path) != expected_stored_sha:
        book.pdf_status = "corrupt"
        db.commit()
        raise DomainError(
            "MEMORY_BOOK_PDF_INTEGRITY_FAILED",
            "打印版 PDF 完整性校验失败，已阻止下载。",
            409,
        )
    if book.pdf_encryption_version == 0:
        return FileResponse(
            output_path,
            media_type="application/pdf",
            filename=f"niannian-memory-book-v{book.version}.pdf",
        )
    runtime_dir = settings.resolved_asset_root / "runtime" / "decrypted"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"memory-book-{book.id}-", suffix=".pdf", dir=runtime_dir
    )
    os.close(descriptor)
    decrypted_path = Path(temporary_name)
    decrypted_path.unlink(missing_ok=True)
    try:
        result = decrypt_media_file(
            output_path,
            decrypted_path,
            key,
            associated_data=media_context(family.id, f"memory-book-{book.id}"),
        )
    except Exception as exc:
        decrypted_path.unlink(missing_ok=True)
        raise DomainError(
            "MEMORY_BOOK_PDF_DECRYPTION_FAILED",
            "打印版 PDF 无法解密，请从备份恢复。",
            409,
        ) from exc
    if result.plaintext_sha256 != book.pdf_sha256:
        decrypted_path.unlink(missing_ok=True)
        raise DomainError(
            "MEMORY_BOOK_PDF_INTEGRITY_FAILED",
            "打印版 PDF 解密后校验失败，已阻止下载。",
            409,
        )
    return FileResponse(
        decrypted_path,
        media_type="application/pdf",
        filename=f"niannian-memory-book-v{book.version}.pdf",
        background=BackgroundTask(decrypted_path.unlink, missing_ok=True),
    )


@router.post("/memory-sessions", response_model=MemorySessionRead, status_code=201)
def create_memory_session(
    payload: MemorySessionCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MemorySessionRead:
    elder = require(
        db, ElderProfile, payload.elder_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。"
    )
    try:
        if payload.trigger_kind in {"photo", "old_object"}:
            preference = db.scalar(
                select(TopicPreference).where(
                    TopicPreference.elder_id == elder.id,
                    TopicPreference.topic_key == payload.life_stage.strip(),
                )
            )
            if preference and preference.preference == "avoid":
                raise ValueError("TOPIC_BLOCKED_BY_PREFERENCE")
            if (
                preference
                and preference.preference == "ask_first"
                and not payload.topic_confirmed
            ):
                raise ValueError("TOPIC_CONFIRMATION_REQUIRED")
            fixed_text = (
                "这张照片让您想起什么？"
                if payload.trigger_kind == "photo"
                else "这件老物件让您想起什么？"
            )
            question = SelectedQuestion(
                prompt_id=f"{payload.trigger_kind}-fixed-v1",
                question_text=fixed_text,
            )
        else:
            question = select_question(
                db,
                elder_id=elder.id,
                life_stage=payload.life_stage.strip(),
                topic_confirmed=payload.topic_confirmed,
            )
    except ValueError as exc:
        if str(exc) == "TOPIC_BLOCKED_BY_PREFERENCE":
            raise DomainError(
                "TOPIC_BLOCKED_BY_PREFERENCE",
                "这位讲述者已经选择不要再问这个话题。",
                409,
            ) from exc
        if str(exc) == "TOPIC_CONFIRMATION_REQUIRED":
            raise DomainError(
                "TOPIC_CONFIRMATION_REQUIRED",
                "请先询问讲述者是否愿意聊这个话题。",
                409,
            ) from exc
        raise
    except Exception as exc:
        raise DomainError("QUESTION_GENERATION_FAILED", "暂时没能生成回忆问题，请重试。", 503) from exc
    session = MemorySession(
        elder_id=elder.id,
        life_stage=payload.life_stage.strip(),
        prompt_id=question.prompt_id,
        question_text=question.question_text,
        status="PROMPT_READY",
    )
    db.add(session)
    db.flush()
    family = elder.person.family
    protect_values(
        db,
        family,
        session,
        {"question_text": question.question_text},
        secret_store,
    )
    db.commit()
    db.refresh(session)
    return memory_session_read(db, session, secret_store)


@router.get("/memory-sessions/{session_id}", response_model=SessionDetail)
def get_memory_session(
    session_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> SessionDetail:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    assets = [media_read(db, asset, secret_store) for asset in session.media_assets]
    tasks = sorted(session.tasks, key=lambda item: item.created_at, reverse=True)
    return SessionDetail(
        session=memory_session_read(db, session, secret_store),
        media_assets=assets,
        transcript=transcript_read(db, session.transcript, secret_store)
        if session.transcript
        else None,
        story_draft=story_draft_read(db, session.story_draft, secret_store)
        if session.story_draft
        else None,
        tasks=[TaskRead.model_validate(task) for task in tasks],
    )


@router.post("/memory-sessions/{session_id}/skip", response_model=MemorySessionRead)
def skip_memory_session(
    session_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MemorySessionRead:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status == "SKIPPED":
        return session
    if session.status == "ARCHIVED":
        raise DomainError("ARCHIVED_SESSION_LOCKED", "已经归档的故事不能在这里跳过。", 409)

    settings = get_settings()
    for asset in list(session.media_assets):
        path = resolve_controlled_path(settings.resolved_asset_root, asset.relative_path)
        path.unlink(missing_ok=True)
        db.delete(asset)
    if session.transcript:
        db.delete(session.transcript)
    if session.story_draft:
        db.delete(session.story_draft)
    for task in list(session.tasks):
        db.delete(task)
    session.status = "SKIPPED"
    skip_event = ConsentEvent(
        action="skip",
        actor_label="family_tester",
        object_type="memory_session",
        object_id=session.id,
    )
    db.add(skip_event)
    db.flush()
    protect_values(
        db,
        session.elder.person.family,
        skip_event,
        {"actor_label": "family_tester"},
        secret_store,
    )
    db.commit()
    db.refresh(session)
    return memory_session_read(db, session, secret_store)


@router.post(
    "/memory-sessions/{session_id}/audio", response_model=MediaAssetRead, status_code=201
)
async def upload_audio(
    session_id: str,
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MediaAssetRead:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status not in {"PROMPT_READY", "RECORDING_PENDING", "AUDIO_UPLOADED"}:
        raise DomainError("SESSION_NOT_READY_FOR_AUDIO", "当前步骤不能上传音频。", 409)

    stored = await store_audio_upload(audio, session.id, get_settings())
    asset = MediaAsset(
        session_id=session.id,
        kind="audio_original",
        status="ready",
        is_original=True,
        **stored,
    )
    db.add(asset)
    db.flush()
    family = session.elder.person.family
    protect_values(
        db,
        family,
        asset,
        {"original_filename": asset.original_filename},
        secret_store,
    )
    encrypt_asset_if_needed(db, asset, family, secret_store)
    if session.status != "AUDIO_UPLOADED":
        session.status = transition(session.status, "AUDIO_UPLOADED")
    db.commit()
    db.refresh(asset)
    return media_read(db, asset, secret_store)


@router.post(
    "/memory-sessions/{session_id}/trigger-image",
    response_model=MediaLinkRead,
    status_code=201,
)
async def upload_trigger_image(
    session_id: str,
    image: UploadFile = File(...),
    trigger_kind: str = Form(...),
    user_annotation: str | None = Form(default=None),
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MediaLinkRead:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if trigger_kind not in {"photo", "old_object"}:
        raise DomainError("TRIGGER_KIND_INVALID", "图片触发类型无效。", 422)
    if session.prompt_id != f"{trigger_kind}-fixed-v1":
        raise DomainError(
            "TRIGGER_SESSION_MISMATCH", "这次回忆不是对应的图片触发会话。", 409
        )
    stored = await store_image_upload(image, session.id, get_settings())
    width = stored.pop("width")
    height = stored.pop("height")
    asset = MediaAsset(
        session_id=session.id,
        kind=f"{trigger_kind}_original",
        status="ready",
        is_original=True,
        **stored,
    )
    db.add(asset)
    db.flush()
    family = session.elder.person.family
    protect_values(
        db,
        family,
        asset,
        {"original_filename": asset.original_filename},
        secret_store,
    )
    encrypt_asset_if_needed(db, asset, family, secret_store)
    link = MediaLink(
        media_asset_id=asset.id,
        elder_id=session.elder_id,
        trigger_kind=trigger_kind,
        user_annotation=user_annotation.strip()[:1000] if user_annotation else None,
        width=width,
        height=height,
        model_inference=None,
    )
    db.add(link)
    db.flush()
    protect_values(
        db,
        family,
        link,
        {
            "user_annotation": user_annotation.strip()[:1000] if user_annotation else None,
            "model_inference": None,
        },
        secret_store,
    )
    db.commit()
    db.refresh(link)
    return media_link_read(db, link, secret_store)


@router.delete("/media-assets/{asset_id}", status_code=204)
def delete_trigger_image(asset_id: str, db: Session = Depends(get_db)) -> Response:
    asset = require(
        db, MediaAsset, asset_id, "MEDIA_NOT_FOUND", "没有找到这份媒体资料。"
    )
    if asset.kind not in {"photo_original", "old_object_original"}:
        raise DomainError("MEDIA_DELETE_NOT_ALLOWED", "这里只能删除照片或老物件图片。", 409)
    if asset.session.status == "ARCHIVED":
        raise DomainError("ARCHIVED_MEDIA_LOCKED", "已归档故事的图片不能在这里删除。", 409)
    path = resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path)
    path.unlink(missing_ok=True)
    db.delete(asset)
    db.commit()
    return Response(status_code=204)


def create_task(
    db: Session,
    session: MemorySession,
    task_type: str,
    target_status: str,
    *,
    model_consent_event_id: str | None = None,
) -> WorkflowTask:
    max_attempt = db.scalar(
        select(func.max(WorkflowTask.attempt)).where(
            WorkflowTask.session_id == session.id,
            WorkflowTask.task_type == task_type,
        )
    )
    task = WorkflowTask(
        session_id=session.id,
        task_type=task_type,
        attempt=(max_attempt or 0) + 1,
        status="queued",
        progress=0,
        model_consent_event_id=model_consent_event_id,
    )
    session.status = target_status
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def reserve_model_consent(
    db: Session,
    session: MemorySession,
    consent_event_id: str | None,
    store: SecretStore,
) -> str | None:
    family = session.elder.person.family
    if not requires_explicit_model_consent(
        get_settings().llm_provider, family.data_classification
    ):
        return None
    consent = db.get(ModelConsentEvent, consent_event_id) if consent_event_id else None
    corrected_text = (
        secure_value(
            db, family, session.transcript, "corrected_text", store
        )
        if session.transcript
        else ""
    )
    error_code = model_consent_error(
        consent,
        family_id=family.id,
        session_id=session.id,
        data_classification=family.data_classification,
        corrected_text=corrected_text,
        require_consumed=False,
    )
    if error_code:
        raise DomainError(
            error_code,
            "真实校对稿不会默认发送给千问。请先明确授权本次发送，再开始整理。",
            409,
        )
    consent.used_at = now_utc()
    return consent.id


@router.post(
    "/memory-sessions/{session_id}/transcription-tasks",
    response_model=TaskRead,
    status_code=202,
)
def submit_transcription(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> WorkflowTask:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status != "AUDIO_UPLOADED":
        raise DomainError("SESSION_NOT_READY_FOR_TRANSCRIPTION", "请先上传音频。", 409)
    task = create_task(db, session, "transcription", "AUDIO_UPLOADED")
    background_tasks.add_task(process_transcription, task.id, secret_store)
    return task


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: Session = Depends(get_db)) -> WorkflowTask:
    return require(db, WorkflowTask, task_id, "TASK_NOT_FOUND", "没有找到这个处理任务。")


@router.post("/tasks/{task_id}/retry", response_model=TaskRead, status_code=202)
def retry_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: TaskRetryRequest | None = None,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> WorkflowTask:
    old_task = require(db, WorkflowTask, task_id, "TASK_NOT_FOUND", "没有找到这个处理任务。")
    if old_task.status not in {"failed_retryable", "failed_final"}:
        raise DomainError("TASK_NOT_RETRYABLE", "这个任务当前不需要重试。", 409)
    if old_task.attempt >= 3:
        raise DomainError("TASK_RETRY_LIMIT", "这个任务已经达到重试次数上限。", 409)
    session = require(
        db, MemorySession, old_task.session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    target = "AUDIO_UPLOADED" if old_task.task_type == "transcription" else "TRANSCRIPT_REVIEW"
    consent_event_id = None
    if old_task.task_type == "organization":
        consent_event_id = reserve_model_consent(
            db, session, payload.consent_event_id if payload else None, secret_store
        )
    new_task = create_task(
        db,
        session,
        old_task.task_type,
        target,
        model_consent_event_id=consent_event_id,
    )
    worker = process_transcription if old_task.task_type == "transcription" else process_organization
    background_tasks.add_task(worker, new_task.id, secret_store)
    return new_task


@router.patch("/transcripts/{transcript_id}", response_model=TranscriptRead)
def update_transcript(
    transcript_id: str,
    payload: TranscriptUpdate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> TranscriptRead:
    transcript = require(
        db, Transcript, transcript_id, "TRANSCRIPT_NOT_FOUND", "没有找到这份转写。"
    )
    session = transcript.session
    if session.status not in {"TRANSCRIPT_REVIEW", "DRAFT_REVIEW"}:
        raise DomainError("TRANSCRIPT_LOCKED", "当前步骤不能修改转写。", 409)
    unused_consents = db.scalars(
        select(ModelConsentEvent).where(
            ModelConsentEvent.session_id == session.id,
            ModelConsentEvent.used_at.is_(None),
            ModelConsentEvent.revoked_at.is_(None),
        )
    ).all()
    for consent in unused_consents:
        consent.revoked_at = now_utc()
    corrected_text = payload.corrected_text.strip()
    transcript.corrected_text = corrected_text
    protect_values(
        db,
        session.elder.person.family,
        transcript,
        {"corrected_text": corrected_text},
        secret_store,
    )
    transcript.version += 1
    if session.status == "DRAFT_REVIEW":
        session.status = transition(session.status, "TRANSCRIPT_REVIEW")
        if session.story_draft:
            session.story_draft.status = "outdated"
    db.commit()
    db.refresh(transcript)
    return transcript_read(db, transcript, secret_store)


@router.post(
    "/memory-sessions/{session_id}/model-consents",
    response_model=ModelConsentRead,
    status_code=201,
)
def create_model_consent(
    session_id: str,
    payload: ModelConsentCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ModelConsentRead:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status != "TRANSCRIPT_REVIEW" or not session.transcript:
        raise DomainError(
            "SESSION_NOT_READY_FOR_MODEL_CONSENT", "请先完成并保存人工校对稿。", 409
        )
    family = session.elder.person.family
    if not requires_explicit_model_consent(
        get_settings().llm_provider, family.data_classification
    ):
        raise DomainError(
            "MODEL_CONSENT_NOT_REQUIRED",
            "当前是虚构测试资料或本地模型流程，不需要创建真实资料外发授权。",
            409,
        )
    consent = ModelConsentEvent(
        family_id=family.id,
        session_id=session.id,
        actor_label=payload.actor_label.strip(),
        purpose=STORY_ORGANIZATION_PURPOSE,
        data_classification=family.data_classification,
        decision="granted",
        one_time=True,
        input_sha256=corrected_text_sha256(
            secure_value(db, family, session.transcript, "corrected_text", secret_store)
        ),
    )
    db.add(consent)
    db.flush()
    protect_values(
        db,
        family,
        consent,
        {"actor_label": payload.actor_label.strip()},
        secret_store,
    )
    db.commit()
    db.refresh(consent)
    return model_consent_read(db, consent, secret_store)


@router.post(
    "/memory-sessions/{session_id}/organization-tasks",
    response_model=TaskRead,
    status_code=202,
)
def submit_organization(
    session_id: str,
    background_tasks: BackgroundTasks,
    payload: OrganizationTaskCreate | None = None,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> WorkflowTask:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status != "TRANSCRIPT_REVIEW" or not session.transcript:
        raise DomainError("SESSION_NOT_READY_FOR_ORGANIZATION", "请先完成转写校对。", 409)
    consent_event_id = reserve_model_consent(
        db, session, payload.consent_event_id if payload else None, secret_store
    )
    task = create_task(
        db,
        session,
        "organization",
        "TRANSCRIPT_REVIEW",
        model_consent_event_id=consent_event_id,
    )
    background_tasks.add_task(process_organization, task.id, secret_store)
    return task


@router.get("/story-drafts/{draft_id}", response_model=StoryDraftRead)
def get_story_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> StoryDraftRead:
    draft = require(db, StoryDraft, draft_id, "DRAFT_NOT_FOUND", "没有找到这份故事草稿。")
    return story_draft_read(db, draft, secret_store)


@router.post("/story-drafts/{draft_id}/reject", response_model=StoryDraftRead)
def reject_story_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> StoryDraftRead:
    draft = require(db, StoryDraft, draft_id, "DRAFT_NOT_FOUND", "没有找到这份故事草稿。")
    session = draft.session
    if session.status != "DRAFT_REVIEW":
        raise DomainError("DRAFT_NOT_REVIEWABLE", "这份草稿当前不能退回。", 409)
    draft.status = "rejected"
    session.status = transition(session.status, "TRANSCRIPT_REVIEW")
    db.commit()
    db.refresh(draft)
    return story_draft_read(db, draft, secret_store)


@router.post("/story-drafts/{draft_id}/confirm", response_model=StoryRead)
def confirm_story_draft(
    draft_id: str,
    payload: ConfirmDraftRequest,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> StoryRead:
    draft = require(db, StoryDraft, draft_id, "DRAFT_NOT_FOUND", "没有找到这份故事草稿。")
    existing = db.scalar(select(Story).where(Story.source_draft_id == draft.id))
    if existing:
        return story_read(db, existing, secret_store)
    session = draft.session
    if session.status != "DRAFT_REVIEW" or draft.status != "pending_review":
        raise DomainError("DRAFT_NOT_REVIEWABLE", "这份草稿当前不能确认归档。", 409)
    if draft.transcript_version != session.transcript.version:
        raise DomainError("DRAFT_OUTDATED", "转写已经修改，请重新整理后再归档。", 409)

    family = session.elder.person.family
    key = family_master_key(family, secret_store)
    draft_title = secure_value(db, family, draft, "title", secret_store, master_key=key)
    draft_body = secure_value(db, family, draft, "body", secret_store, master_key=key)
    timeline_mentions = secure_value(
        db, family, draft, "timeline_mentions", secret_store, master_key=key
    )
    preferred_name = secure_value(
        db, family, session.elder, "preferred_name", secret_store, master_key=key
    )
    session.status = transition(session.status, "CONFIRMED")
    story = Story(
        elder_id=session.elder_id,
        source_draft_id=draft.id,
        title=draft_title,
        body=draft_body,
        confirmed_by=payload.confirmed_by.strip(),
    )
    db.add(story)
    db.flush()
    protect_values(
        db,
        family,
        story,
        {
            "title": draft_title,
            "body": draft_body,
            "confirmed_by": payload.confirmed_by.strip(),
        },
        secret_store,
    )
    fact = MemoryFact(
        elder_id=session.elder_id,
        story_id=story.id,
        fact_type="confirmed_story",
        subject_label=preferred_name,
        value_text=draft_body,
        content_sha256=hashlib.sha256(draft_body.encode("utf-8")).hexdigest(),
        confidence="confirmed",
        status="active",
    )
    db.add(fact)
    db.flush()
    protect_values(
        db,
        family,
        fact,
        {"subject_label": preferred_name, "value_text": draft_body},
        secret_store,
    )
    for mention in timeline_mentions:
        event = TimelineEvent(
            story_id=story.id,
            time_expression=mention.get("expression"),
            normalized_time=mention.get("normalized"),
            confidence=mention.get("confidence", "uncertain"),
        )
        db.add(event)
        db.flush()
        protect_values(
            db,
            family,
            event,
            {
                "time_expression": mention.get("expression"),
                "normalized_time": mention.get("normalized"),
            },
            secret_store,
        )
    draft.status = "confirmed"
    confirmation_event = ConsentEvent(
        action="confirm_archive",
        actor_label=payload.confirmed_by.strip(),
        object_type="story_draft",
        object_id=draft.id,
    )
    db.add(confirmation_event)
    db.flush()
    protect_values(
        db,
        family,
        confirmation_event,
        {"actor_label": payload.confirmed_by.strip()},
        secret_store,
    )
    session.status = transition(session.status, "ARCHIVED")
    db.commit()
    db.refresh(story)
    return story_read(db, story, secret_store)


@router.get("/elder-profiles/{profile_id}/timeline", response_model=list[TimelineItem])
def get_timeline(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[TimelineItem]:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。")
    stories = db.scalars(
        select(Story).where(Story.elder_id == profile_id).order_by(Story.confirmed_at.desc())
    ).all()
    items: list[TimelineItem] = []
    for story in stories:
        original = db.scalar(
            select(MediaAsset)
            .join(MemorySession, MediaAsset.session_id == MemorySession.id)
            .where(
                MemorySession.id == story.source_draft.session_id,
                MediaAsset.kind == "audio_original",
                MediaAsset.is_original.is_(True),
            )
            .order_by(MediaAsset.created_at.desc())
        )
        items.append(
            TimelineItem(
                story=story_read(db, story, secret_store),
                events=[
                    timeline_event_read(
                        db, event, profile.person.family, secret_store
                    )
                    for event in story.timeline_events
                ],
                audio_url=f"/api/v1/media-assets/{original.id}/content" if original else None,
            )
        )
    return items


@router.get("/media-assets/{asset_id}/content")
def get_media_content(
    asset_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FileResponse:
    asset = require(db, MediaAsset, asset_id, "ASSET_NOT_FOUND", "没有找到这个音频文件。")
    path = resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path)
    if not path.is_file():
        raise DomainError("ASSET_FILE_MISSING", "音频文件已经损坏或丢失。", 404)
    if not verify_asset_integrity(
        path,
        expected_size=asset.size_bytes,
        expected_sha256=asset.sha256,
    ):
        asset.status = "corrupt"
        asset.integrity_checked_at = now_utc()
        db.commit()
        raise DomainError(
            "ASSET_INTEGRITY_FAILED",
            "音频文件校验失败，可能已经损坏，请从备份恢复。",
            409,
        )
    asset.integrity_checked_at = now_utc()
    db.commit()
    family = asset.session.elder.person.family
    filename = secure_value(db, family, asset, "original_filename", secret_store)
    if asset.encryption_version == 0:
        return FileResponse(path, media_type=asset.mime_type, filename=filename)
    if asset.encryption_version != 1:
        raise DomainError("ASSET_ENCRYPTION_UNSUPPORTED", "不支持这份媒体的加密版本。", 409)
    key = family_master_key(family, secret_store)
    if key is None:
        raise DomainError("MASTER_KEY_MISSING", "无法解锁家庭档案。", 409)
    runtime_dir = get_settings().resolved_asset_root / "runtime" / "decrypted"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{asset.id}-", suffix=Path(filename).suffix, dir=runtime_dir
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink(missing_ok=True)
    try:
        result = decrypt_media_file(
            path,
            temporary_path,
            key,
            associated_data=media_context(family.id, asset.id),
        )
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        asset.status = "corrupt"
        db.commit()
        raise DomainError(
            "ASSET_DECRYPTION_FAILED",
            "媒体无法解密，密钥错误或文件已损坏，请从备份恢复。",
            409,
        ) from exc
    if (
        result.plaintext_size != asset.plaintext_size_bytes
        or result.plaintext_sha256 != asset.plaintext_sha256
    ):
        temporary_path.unlink(missing_ok=True)
        asset.status = "corrupt"
        db.commit()
        raise DomainError(
            "ASSET_PLAINTEXT_INTEGRITY_FAILED",
            "媒体解密后校验失败，请从备份恢复。",
            409,
        )
    return FileResponse(
        temporary_path,
        media_type=asset.mime_type,
        filename=filename,
        background=BackgroundTask(temporary_path.unlink, missing_ok=True),
    )
