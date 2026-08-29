from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from fastapi.responses import FileResponse, Response
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
    MemoryFact,
    MemorySession,
    ModelConsentEvent,
    Person,
    PersonRelationship,
    QuestionPrompt,
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
    MemorySessionCreate,
    MemorySessionRead,
    ModelConsentCreate,
    ModelConsentRead,
    OrganizationTaskCreate,
    MemoryFactRead,
    PersonCreate,
    PersonRead,
    PersonRelationshipCreate,
    PersonRelationshipRead,
    QuestionPromptRead,
    RecoveryPackageCreate,
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
    resolve_controlled_path,
    store_audio_upload,
    verify_asset_integrity,
)
from app.services.memory import ensure_question_bank, select_question
from app.services.privacy import (
    STORY_ORGANIZATION_PURPOSE,
    corrected_text_sha256,
    model_consent_error,
    requires_explicit_model_consent,
)
from app.services.security import build_recovery_package, get_family_key_manager, get_secret_store
from app.services.security.key_store import SecretStore, SecretStoreError
from app.services.workflow.state import transition
from app.services.workflow.tasks import process_organization, process_transcription


router = APIRouter(prefix="/api/v1")


def require(db: Session, model, object_id: str, code: str, message: str):
    item = db.get(model, object_id)
    if not item:
        raise DomainError(code, message, 404)
    return item


def elder_read(profile: ElderProfile) -> ElderProfileRead:
    return ElderProfileRead(
        id=profile.id,
        person_id=profile.person_id,
        family_id=profile.person.family_id,
        data_classification=profile.person.family.data_classification,
        display_name=profile.person.display_name,
        preferred_name=profile.preferred_name,
        birth_year=profile.birth_year,
        birth_era=profile.birth_era,
        native_place=profile.native_place,
        occupation_summary=profile.occupation_summary,
        created_at=profile.created_at,
    )


def media_read(asset: MediaAsset) -> MediaAssetRead:
    return MediaAssetRead(
        id=asset.id,
        kind=asset.kind,
        original_filename=asset.original_filename,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        status=asset.status,
        is_original=asset.is_original,
        content_url=f"/api/v1/media-assets/{asset.id}/content",
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
    family: FamilyArchive, metadata: ArchiveSecurity | None
) -> FamilySecurityRead:
    return FamilySecurityRead(
        family_id=family.id,
        key_version=metadata.key_version if metadata else None,
        encryption_status=metadata.encryption_status if metadata else "not_initialized",
        key_initialized=metadata is not None,
        recovery_package_created_at=metadata.recovery_package_created_at if metadata else None,
    )


@router.get("/families/{family_id}/security", response_model=FamilySecurityRead)
def get_family_security(
    family_id: str, db: Session = Depends(get_db)
) -> FamilySecurityRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    metadata = db.scalar(
        select(ArchiveSecurity).where(ArchiveSecurity.family_id == family.id)
    )
    return family_security_read(family, metadata)


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
    return family_security_read(family, metadata)


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


@router.post("/elder-profiles", response_model=ElderProfileRead, status_code=201)
def create_elder_profile(
    payload: ElderProfileCreate, db: Session = Depends(get_db)
) -> ElderProfileRead:
    require(db, FamilyArchive, payload.family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
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
    db.commit()
    db.refresh(profile)
    return elder_read(profile)


@router.post(
    "/families/{family_id}/people", response_model=PersonRead, status_code=201
)
def create_family_person(
    family_id: str,
    payload: PersonCreate,
    db: Session = Depends(get_db),
) -> Person:
    require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    person = Person(
        family_id=family_id,
        role=payload.role.strip(),
        display_name=payload.display_name.strip(),
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


@router.get("/families/{family_id}/people", response_model=list[PersonRead])
def list_family_people(
    family_id: str, db: Session = Depends(get_db)
) -> list[Person]:
    require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    return list(
        db.scalars(
            select(Person)
            .where(Person.family_id == family_id)
            .order_by(Person.created_at)
        ).all()
    )


@router.post(
    "/families/{family_id}/relationships",
    response_model=PersonRelationshipRead,
    status_code=201,
)
def create_person_relationship(
    family_id: str,
    payload: PersonRelationshipCreate,
    db: Session = Depends(get_db),
) -> PersonRelationship:
    require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
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
        return existing
    relationship = PersonRelationship(
        family_id=family_id,
        from_person_id=from_person.id,
        to_person_id=to_person.id,
        relationship_type=payload.relationship_type,
        custom_label=payload.custom_label.strip() if payload.custom_label else None,
        confirmed_by=payload.confirmed_by.strip(),
    )
    db.add(relationship)
    db.commit()
    db.refresh(relationship)
    return relationship


@router.get(
    "/families/{family_id}/relationships",
    response_model=list[PersonRelationshipRead],
)
def list_person_relationships(
    family_id: str, db: Session = Depends(get_db)
) -> list[PersonRelationship]:
    require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    return list(
        db.scalars(
            select(PersonRelationship)
            .where(PersonRelationship.family_id == family_id)
            .order_by(PersonRelationship.created_at)
        ).all()
    )


@router.get("/elder-profiles", response_model=list[ElderProfileRead])
def list_elder_profiles(db: Session = Depends(get_db)) -> list[ElderProfileRead]:
    profiles = db.scalars(select(ElderProfile).order_by(ElderProfile.created_at.desc())).all()
    return [elder_read(profile) for profile in profiles]


@router.get("/elder-profiles/{profile_id}", response_model=ElderProfileRead)
def get_elder_profile(profile_id: str, db: Session = Depends(get_db)) -> ElderProfileRead:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。"
    )
    return elder_read(profile)


@router.put(
    "/elder-profiles/{profile_id}/topic-preferences/{topic_key}",
    response_model=TopicPreferenceRead,
)
def upsert_topic_preference(
    profile_id: str,
    topic_key: str,
    payload: TopicPreferenceUpsert,
    db: Session = Depends(get_db),
) -> TopicPreference:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
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
    db.commit()
    db.refresh(preference)
    return preference


@router.get(
    "/elder-profiles/{profile_id}/topic-preferences",
    response_model=list[TopicPreferenceRead],
)
def list_topic_preferences(
    profile_id: str, db: Session = Depends(get_db)
) -> list[TopicPreference]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    return list(
        db.scalars(
            select(TopicPreference)
            .where(TopicPreference.elder_id == profile_id)
            .order_by(TopicPreference.topic_key)
        ).all()
    )


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
    profile_id: str, db: Session = Depends(get_db)
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
        preferences=[TopicPreferenceRead.model_validate(item) for item in profile.topic_preferences],
        confirmed_facts=[
            MemoryFactRead.model_validate(item)
            for item in profile.memory_facts
            if item.status == "active"
        ],
    )


@router.post("/memory-sessions", response_model=MemorySessionRead, status_code=201)
def create_memory_session(
    payload: MemorySessionCreate, db: Session = Depends(get_db)
) -> MemorySession:
    elder = require(
        db, ElderProfile, payload.elder_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。"
    )
    try:
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
    db.commit()
    db.refresh(session)
    return session


@router.get("/memory-sessions/{session_id}", response_model=SessionDetail)
def get_memory_session(session_id: str, db: Session = Depends(get_db)) -> SessionDetail:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    assets = [media_read(asset) for asset in session.media_assets]
    tasks = sorted(session.tasks, key=lambda item: item.created_at, reverse=True)
    return SessionDetail(
        session=MemorySessionRead.model_validate(session),
        media_assets=assets,
        transcript=TranscriptRead.model_validate(session.transcript)
        if session.transcript
        else None,
        story_draft=StoryDraftRead.model_validate(session.story_draft)
        if session.story_draft
        else None,
        tasks=[TaskRead.model_validate(task) for task in tasks],
    )


@router.post("/memory-sessions/{session_id}/skip", response_model=MemorySessionRead)
def skip_memory_session(session_id: str, db: Session = Depends(get_db)) -> MemorySession:
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
    db.add(
        ConsentEvent(
            action="skip",
            actor_label="family_tester",
            object_type="memory_session",
            object_id=session.id,
        )
    )
    db.commit()
    db.refresh(session)
    return session


@router.post(
    "/memory-sessions/{session_id}/audio", response_model=MediaAssetRead, status_code=201
)
async def upload_audio(
    session_id: str,
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
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
    if session.status != "AUDIO_UPLOADED":
        session.status = transition(session.status, "AUDIO_UPLOADED")
    db.commit()
    db.refresh(asset)
    return media_read(asset)


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
    db: Session, session: MemorySession, consent_event_id: str | None
) -> str | None:
    family = session.elder.person.family
    if not requires_explicit_model_consent(
        get_settings().llm_provider, family.data_classification
    ):
        return None
    consent = db.get(ModelConsentEvent, consent_event_id) if consent_event_id else None
    error_code = model_consent_error(
        consent,
        family_id=family.id,
        session_id=session.id,
        data_classification=family.data_classification,
        corrected_text=session.transcript.corrected_text if session.transcript else "",
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
) -> WorkflowTask:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status != "AUDIO_UPLOADED":
        raise DomainError("SESSION_NOT_READY_FOR_TRANSCRIPTION", "请先上传音频。", 409)
    task = create_task(db, session, "transcription", "AUDIO_UPLOADED")
    background_tasks.add_task(process_transcription, task.id)
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
            db, session, payload.consent_event_id if payload else None
        )
    new_task = create_task(
        db,
        session,
        old_task.task_type,
        target,
        model_consent_event_id=consent_event_id,
    )
    worker = process_transcription if old_task.task_type == "transcription" else process_organization
    background_tasks.add_task(worker, new_task.id)
    return new_task


@router.patch("/transcripts/{transcript_id}", response_model=TranscriptRead)
def update_transcript(
    transcript_id: str,
    payload: TranscriptUpdate,
    db: Session = Depends(get_db),
) -> Transcript:
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
    transcript.corrected_text = payload.corrected_text.strip()
    transcript.version += 1
    if session.status == "DRAFT_REVIEW":
        session.status = transition(session.status, "TRANSCRIPT_REVIEW")
        if session.story_draft:
            session.story_draft.status = "outdated"
    db.commit()
    db.refresh(transcript)
    return transcript


@router.post(
    "/memory-sessions/{session_id}/model-consents",
    response_model=ModelConsentRead,
    status_code=201,
)
def create_model_consent(
    session_id: str,
    payload: ModelConsentCreate,
    db: Session = Depends(get_db),
) -> ModelConsentEvent:
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
        input_sha256=corrected_text_sha256(session.transcript.corrected_text),
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent


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
) -> WorkflowTask:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status != "TRANSCRIPT_REVIEW" or not session.transcript:
        raise DomainError("SESSION_NOT_READY_FOR_ORGANIZATION", "请先完成转写校对。", 409)
    consent_event_id = reserve_model_consent(
        db, session, payload.consent_event_id if payload else None
    )
    task = create_task(
        db,
        session,
        "organization",
        "TRANSCRIPT_REVIEW",
        model_consent_event_id=consent_event_id,
    )
    background_tasks.add_task(process_organization, task.id)
    return task


@router.get("/story-drafts/{draft_id}", response_model=StoryDraftRead)
def get_story_draft(draft_id: str, db: Session = Depends(get_db)) -> StoryDraft:
    return require(db, StoryDraft, draft_id, "DRAFT_NOT_FOUND", "没有找到这份故事草稿。")


@router.post("/story-drafts/{draft_id}/reject", response_model=StoryDraftRead)
def reject_story_draft(draft_id: str, db: Session = Depends(get_db)) -> StoryDraft:
    draft = require(db, StoryDraft, draft_id, "DRAFT_NOT_FOUND", "没有找到这份故事草稿。")
    session = draft.session
    if session.status != "DRAFT_REVIEW":
        raise DomainError("DRAFT_NOT_REVIEWABLE", "这份草稿当前不能退回。", 409)
    draft.status = "rejected"
    session.status = transition(session.status, "TRANSCRIPT_REVIEW")
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/story-drafts/{draft_id}/confirm", response_model=StoryRead)
def confirm_story_draft(
    draft_id: str,
    payload: ConfirmDraftRequest,
    db: Session = Depends(get_db),
) -> Story:
    draft = require(db, StoryDraft, draft_id, "DRAFT_NOT_FOUND", "没有找到这份故事草稿。")
    existing = db.scalar(select(Story).where(Story.source_draft_id == draft.id))
    if existing:
        return existing
    session = draft.session
    if session.status != "DRAFT_REVIEW" or draft.status != "pending_review":
        raise DomainError("DRAFT_NOT_REVIEWABLE", "这份草稿当前不能确认归档。", 409)
    if draft.transcript_version != session.transcript.version:
        raise DomainError("DRAFT_OUTDATED", "转写已经修改，请重新整理后再归档。", 409)

    session.status = transition(session.status, "CONFIRMED")
    story = Story(
        elder_id=session.elder_id,
        source_draft_id=draft.id,
        title=draft.title,
        body=draft.body,
        confirmed_by=payload.confirmed_by.strip(),
    )
    db.add(story)
    db.flush()
    db.add(
        MemoryFact(
            elder_id=session.elder_id,
            story_id=story.id,
            fact_type="confirmed_story",
            subject_label=session.elder.preferred_name,
            value_text=story.body,
            content_sha256=hashlib.sha256(story.body.encode("utf-8")).hexdigest(),
            confidence="confirmed",
            status="active",
        )
    )
    for mention in draft.timeline_mentions:
        db.add(
            TimelineEvent(
                story_id=story.id,
                time_expression=mention.get("expression"),
                normalized_time=mention.get("normalized"),
                confidence=mention.get("confidence", "uncertain"),
            )
        )
    draft.status = "confirmed"
    db.add(
        ConsentEvent(
            action="confirm_archive",
            actor_label=payload.confirmed_by.strip(),
            object_type="story_draft",
            object_id=draft.id,
        )
    )
    session.status = transition(session.status, "ARCHIVED")
    db.commit()
    db.refresh(story)
    return story


@router.get("/elder-profiles/{profile_id}/timeline", response_model=list[TimelineItem])
def get_timeline(profile_id: str, db: Session = Depends(get_db)) -> list[TimelineItem]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。")
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
                MediaAsset.is_original.is_(True),
            )
            .order_by(MediaAsset.created_at.desc())
        )
        items.append(
            TimelineItem(
                story=StoryRead.model_validate(story),
                events=[TimelineEventRead.model_validate(event) for event in story.timeline_events],
                audio_url=f"/api/v1/media-assets/{original.id}/content" if original else None,
            )
        )
    return items


@router.get("/media-assets/{asset_id}/content")
def get_media_content(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
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
    return FileResponse(path, media_type=asset.mime_type, filename=asset.original_filename)
