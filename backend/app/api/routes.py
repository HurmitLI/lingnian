from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import (
    ConsentEvent,
    ElderProfile,
    FamilyArchive,
    MediaAsset,
    MemorySession,
    Person,
    Story,
    StoryDraft,
    TimelineEvent,
    Transcript,
    WorkflowTask,
)
from app.schemas.api import (
    ConfirmDraftRequest,
    ElderProfileCreate,
    ElderProfileRead,
    FamilyCreate,
    FamilyRead,
    HealthRead,
    MediaAssetRead,
    MemorySessionCreate,
    MemorySessionRead,
    SessionDetail,
    StoryDraftRead,
    StoryRead,
    TaskRead,
    TimelineEventRead,
    TimelineItem,
    TranscriptRead,
    TranscriptUpdate,
)
from app.services.archive.assets import resolve_controlled_path, store_audio_upload
from app.services.llm import get_llm_provider
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
    )
    db.add(family)
    db.commit()
    db.refresh(family)
    return family


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


@router.post("/memory-sessions", response_model=MemorySessionRead, status_code=201)
def create_memory_session(
    payload: MemorySessionCreate, db: Session = Depends(get_db)
) -> MemorySession:
    elder = require(
        db, ElderProfile, payload.elder_id, "ELDER_NOT_FOUND", "没有找到这位老人的测试档案。"
    )
    try:
        question = get_llm_provider().generate_question(
            elder.preferred_name, payload.life_stage.strip()
        )
    except Exception as exc:
        raise DomainError("QUESTION_GENERATION_FAILED", "暂时没能生成回忆问题，请重试。", 503) from exc
    session = MemorySession(
        elder_id=elder.id,
        life_stage=payload.life_stage.strip(),
        prompt_id=f"{payload.life_stage.strip()}-v1",
        question_text=question.question,
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
    db: Session, session: MemorySession, task_type: str, target_status: str
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
    )
    session.status = target_status
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


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
    new_task = create_task(db, session, old_task.task_type, target)
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
    "/memory-sessions/{session_id}/organization-tasks",
    response_model=TaskRead,
    status_code=202,
)
def submit_organization(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> WorkflowTask:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次回忆记录。"
    )
    if session.status != "TRANSCRIPT_REVIEW" or not session.transcript:
        raise DomainError("SESSION_NOT_READY_FOR_ORGANIZATION", "请先完成转写校对。", 409)
    task = create_task(db, session, "organization", "TRANSCRIPT_REVIEW")
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
    return FileResponse(path, media_type=asset.mime_type, filename=asset.original_filename)

