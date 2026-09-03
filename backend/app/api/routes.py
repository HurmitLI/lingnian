from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import (
    ArchiveSecurity,
    BackupManifest,
    ConsentEvent,
    ElderProfile,
    EncryptedField,
    FamilyArchive,
    InterviewTurn,
    Keepsake,
    KeepsakeAuthorization,
    LegacyPlan,
    MediaAsset,
    MediaLink,
    MediaPersonTag,
    MemoryBook,
    MemoryFact,
    MemorySession,
    ModelConsentEvent,
    Person,
    PersonRelationship,
    QuestionPrompt,
    Reminder,
    GenerativeMediaRequest,
    Story,
    StoryContribution,
    StoryDetail,
    StoryDraft,
    TimelineEvent,
    Transcript,
    TopicPreference,
    WorkflowTask,
)
from app.models.entities import now_utc
from app.schemas.api import (
    ArchiveAnswer,
    ArchiveAskRequest,
    ArchiveCitation,
    ArchiveGapCreate,
    ConfirmDraftRequest,
    BackupCreate,
    BackupRead,
    ElderProfileCreate,
    ElderProfileRead,
    ElderProfileUpdate,
    FamilySecurityRead,
    FamilyCreate,
    FamilyRead,
    HealthRead,
    HeritageExportCreate,
    ProductionPackageCreate,
    GenerativeMediaCapability,
    GenerativeMediaRequestCreate,
    GenerativeMediaRequestRead,
    GenerativeMediaReviewCreate,
    InterviewContinueRequest,
    InterviewContinueResult,
    InterviewTurnRead,
    InterviewTurnUpdate,
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
    KeepsakeAuthorizationCreate,
    KeepsakeAuthorizationRead,
    KeepsakeCatalogItem,
    KeepsakeCreate,
    KeepsakeRead,
    LegacyPlanRead,
    LegacyPlanUpsert,
    MediaPersonTagCreate,
    MediaPersonTagRead,
    PersonCreate,
    PersonRead,
    PersonUpdate,
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
    StoryContributionCreate,
    StoryContributionRead,
    StoryContributionStatusUpdate,
    StoryDetailRead,
    StoryDetailUpsert,
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
    store_video_upload,
    verify_asset_integrity,
)
from app.services.memory import (
    HeritageMedia,
    HeritageStory,
    ProductionMedia,
    SearchDocument,
    build_heritage_package,
    build_production_package,
    compose_grounded_answer,
    ensure_question_bank,
    markdown_sha256,
    render_memory_book,
    render_memory_book_pdf,
    rank_archive,
    file_sha256,
    select_question,
    SelectedQuestion,
)
from app.services.generative_media import capability_catalog, estimate_request
from app.services.asr import get_asr_provider
from app.services.asr.audio import get_ffmpeg_binary, normalize_audio
from app.services.llm import (
    clean_local_interview_transcript,
    generate_local_interview_followup,
    get_llm_provider,
)
from app.services.tts import get_tts_provider
from app.services.keepsake import (
    build_keepsake_manifest,
    manifest_sha256 as keepsake_manifest_sha256,
    process_keepsake,
)
from app.services.privacy import (
    STORY_ORGANIZATION_PURPOSE,
    corrected_text_sha256,
    model_consent_error,
    requires_explicit_model_consent,
)
from app.services.security import (
    RecoveryPackageError,
    BackupError,
    activate_archive_encryption,
    build_recovery_package,
    build_local_backup,
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
    rehearse_family_recovery,
    verify_local_backup,
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
        health_notes=secure_value(db, family, profile, "health_notes", store, master_key=key),
        created_at=profile.created_at,
    )


def family_read(
    db: Session, family: FamilyArchive, store: SecretStore
) -> FamilyRead:
    return FamilyRead(
        id=family.id,
        display_name=secure_value(db, family, family, "display_name", store),
        schema_version=family.schema_version,
        data_classification=family.data_classification,
        created_at=family.created_at,
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


def keepsake_authorization_read(
    db: Session,
    authorization: KeepsakeAuthorization,
    store: SecretStore,
) -> KeepsakeAuthorizationRead:
    family = authorization.elder.person.family
    return KeepsakeAuthorizationRead(
        id=authorization.id,
        elder_id=authorization.elder_id,
        actor_label=secure_value(db, family, authorization, "actor_label", store),
        story_ids=authorization.story_ids,
        manifest_sha256=authorization.manifest_sha256,
        original_voice_authorized=authorization.original_voice_authorized,
        private_family_use=authorization.private_family_use,
        no_impersonation=authorization.no_impersonation,
        original_audio_only=authorization.original_audio_only,
        decision=authorization.decision,
        used_at=authorization.used_at,
        created_at=authorization.created_at,
    )


def keepsake_read(
    db: Session,
    keepsake: Keepsake,
    store: SecretStore,
) -> KeepsakeRead:
    family = keepsake.elder.person.family
    return KeepsakeRead(
        id=keepsake.id,
        elder_id=keepsake.elder_id,
        authorization_id=keepsake.authorization_id,
        version=keepsake.version,
        title=secure_value(db, family, keepsake, "title", store),
        story_manifest=keepsake.story_manifest,
        status=keepsake.status,
        progress=keepsake.progress,
        attempt=keepsake.attempt,
        error_code=keepsake.error_code,
        mime_type=keepsake.mime_type,
        duration_ms=keepsake.duration_ms,
        width=keepsake.width,
        height=keepsake.height,
        size_bytes=keepsake.plaintext_size_bytes if keepsake.encryption_version else keepsake.size_bytes,
        renderer=keepsake.renderer,
        cost_cents=keepsake.cost_cents,
        source_mode=keepsake.source_mode,
        content_url=f"/api/v1/keepsakes/{keepsake.id}/content" if keepsake.status == "ready" else None,
        created_at=keepsake.created_at,
        updated_at=keepsake.updated_at,
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
        narrator_person_id=session.narrator_person_id,
        interview_mode=session.interview_mode,
        life_stage=session.life_stage,
        prompt_id=session.prompt_id,
        question_text=secure_value(db, family, session, "question_text", store),
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def interview_turn_read(
    db: Session, turn: InterviewTurn, store: SecretStore
) -> InterviewTurnRead:
    family = turn.session.elder.person.family
    key = family_master_key(family, store)
    return InterviewTurnRead(
        id=turn.id,
        session_id=turn.session_id,
        turn_index=turn.turn_index,
        question_text=secure_value(
            db, family, turn, "question_text", store, master_key=key
        ),
        raw_answer_text=secure_value(
            db, family, turn, "raw_answer_text", store, master_key=key
        ),
        corrected_answer_text=secure_value(
            db, family, turn, "corrected_answer_text", store, master_key=key
        ),
        answer_version=turn.answer_version,
        audio_asset_id=turn.audio_asset_id,
        audio_url=(
            f"/api/v1/media-assets/{turn.audio_asset_id}/content"
            if turn.audio_asset_id
            else None
        ),
        question_audio_asset_id=turn.question_audio_asset_id,
        question_audio_url=(
            f"/api/v1/media-assets/{turn.question_audio_asset_id}/content"
            if turn.question_audio_asset_id
            else None
        ),
        asr_provider=turn.asr_provider,
        asr_model=turn.asr_model,
        asr_metadata=secure_value(
            db, family, turn, "asr_metadata", store, master_key=key
        ),
        followup_mode=turn.followup_mode,
        status=turn.status,
        created_at=turn.created_at,
        updated_at=turn.updated_at,
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


def story_detail_read(
    db: Session, detail: StoryDetail, store: SecretStore
) -> StoryDetailRead:
    family = detail.story.elder.person.family
    key = family_master_key(family, store)
    return StoryDetailRead(
        id=detail.id,
        story_id=detail.story_id,
        place_name=secure_value(db, family, detail, "place_name", store, master_key=key),
        event_year=secure_value(db, family, detail, "event_year", store, master_key=key),
        theme_tags=secure_value(db, family, detail, "theme_tags", store, master_key=key),
        summary=secure_value(db, family, detail, "summary", store, master_key=key),
        updated_by=secure_value(db, family, detail, "updated_by", store, master_key=key),
        updated_at=detail.updated_at,
    )


def story_contribution_read(
    db: Session, contribution: StoryContribution, store: SecretStore
) -> StoryContributionRead:
    family = contribution.story.elder.person.family
    key = family_master_key(family, store)
    return StoryContributionRead(
        id=contribution.id,
        story_id=contribution.story_id,
        contributor_person_id=contribution.contributor_person_id,
        contributor_label=secure_value(
            db, family, contribution, "contributor_label", store, master_key=key
        ),
        contribution_type=contribution.contribution_type,
        body=secure_value(db, family, contribution, "body", store, master_key=key),
        status=contribution.status,
        created_at=contribution.created_at,
    )


def legacy_plan_read(
    db: Session, plan: LegacyPlan, family: FamilyArchive, store: SecretStore
) -> LegacyPlanRead:
    key = family_master_key(family, store)
    return LegacyPlanRead(
        id=plan.id,
        family_id=plan.family_id,
        successor_person_ids=secure_value(
            db, family, plan, "successor_person_ids", store, master_key=key
        ),
        access_policy=plan.access_policy,
        steward_label=secure_value(
            db, family, plan, "steward_label", store, master_key=key
        ),
        note=secure_value(db, family, plan, "note", store, master_key=key),
        confirmed_at=plan.confirmed_at,
        updated_at=plan.updated_at,
    )


def media_person_tag_read(
    db: Session, tag: MediaPersonTag, store: SecretStore
) -> MediaPersonTagRead:
    family = require(db, FamilyArchive, tag.family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    person = require(db, Person, tag.person_id, "PERSON_NOT_FOUND", "没有找到这位家庭成员。")
    key = family_master_key(family, store)
    return MediaPersonTagRead(
        id=tag.id,
        family_id=tag.family_id,
        media_asset_id=tag.media_asset_id,
        person_id=tag.person_id,
        person_name=secure_value(db, family, person, "display_name", store, master_key=key),
        tagged_by=secure_value(db, family, tag, "tagged_by", store, master_key=key),
        note=secure_value(db, family, tag, "note", store, master_key=key),
        created_at=tag.created_at,
    )


def generative_request_read(
    db: Session, request: GenerativeMediaRequest, store: SecretStore
) -> GenerativeMediaRequestRead:
    family = request.elder.person.family
    return GenerativeMediaRequestRead(
        id=request.id,
        elder_id=request.elder_id,
        story_id=request.story_id,
        result_asset_id=request.result_asset_id,
        result_content_url=(
            f"/api/v1/media-assets/{request.result_asset_id}/content"
            if request.result_asset_id
            else None
        ),
        generation_type=request.generation_type,
        provider_key=request.provider_key,
        status=request.status,
        actor_label=secure_value(db, family, request, "actor_label", store),
        subject_consent=request.subject_consent,
        rights_confirmed=request.rights_confirmed,
        no_impersonation=request.no_impersonation,
        allow_external_upload=request.allow_external_upload,
        estimated_cost_cents=request.estimated_cost_cents,
        actual_cost_cents=request.actual_cost_cents,
        max_cost_cents=request.max_cost_cents,
        error_code=request.error_code,
        review_checks=request.review_checks or {},
        reviewed_by=secure_value(db, family, request, "reviewed_by", store),
        review_notes=secure_value(db, family, request, "review_notes", store),
        reviewed_at=request.reviewed_at,
        created_at=request.created_at,
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
        session = story.source_draft.session
        narrator = session.narrator or session.elder.person
        narrator_label = secure_value(
            db, family, narrator, "display_name", store, master_key=key
        )
        session_view = SimpleNamespace(
            life_stage=session.life_stage,
            narrator_label=narrator_label,
            narration_kind=(
                "first_person"
                if narrator.id == session.elder.person_id
                else "family_recollection"
            ),
        )
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


def delete_secure_fields(
    db: Session, *, family_id: str, object_ids: list[str]
) -> None:
    if not object_ids:
        return
    db.execute(
        delete(EncryptedField).where(
            EncryptedField.family_id == family_id,
            EncryptedField.object_id.in_(object_ids),
        )
    )


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
def create_family(
    payload: FamilyCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FamilyRead:
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
            return family_read(db, existing, secret_store)
    family = FamilyArchive(
        display_name=payload.display_name.strip(),
        idempotency_key=payload.idempotency_key,
        data_classification=payload.data_classification,
    )
    db.add(family)
    db.commit()
    db.refresh(family)
    return family_read(db, family, secret_store)


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


@router.post("/backups", response_model=BackupRead, status_code=201)
def create_local_backup(
    payload: BackupCreate,
    db: Session = Depends(get_db),
) -> BackupManifest:
    unsafe_families = db.scalars(
        select(FamilyArchive).where(FamilyArchive.data_classification != "test")
    ).all()
    for family in unsafe_families:
        if (
            family.security_metadata is None
            or family.security_metadata.encryption_status != "active_encrypted"
        ):
            raise DomainError(
                "BACKUP_BLOCKED_BY_ENCRYPTION",
                "存在尚未完成加密的真实家庭资料，已阻止备份。",
                409,
            )
    backup_id = str(uuid4())
    relative_path = f"backups/niannian-local-backup-{backup_id}.zip"
    output_path = resolve_controlled_path(
        get_settings().resolved_asset_root, relative_path
    )
    try:
        result = build_local_backup(get_settings(), output_path)
    except BackupError as exc:
        raise DomainError("BACKUP_CREATE_FAILED", str(exc), 409) from exc
    backup = BackupManifest(
        id=backup_id,
        family_id=None,
        backup_version=1,
        relative_path=relative_path,
        archive_sha256=result.archive_sha256,
        database_sha256=result.database_sha256,
        asset_count=result.asset_count,
        status="ready",
        verification_summary={"scope": "whole_local_archive"},
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


@router.get("/backups", response_model=list[BackupRead])
def list_local_backups(db: Session = Depends(get_db)) -> list[BackupManifest]:
    return list(
        db.scalars(
            select(BackupManifest).order_by(BackupManifest.created_at.desc())
        ).all()
    )


@router.get("/backups/{backup_id}/download")
def download_local_backup(
    backup_id: str, db: Session = Depends(get_db)
) -> FileResponse:
    backup = require(
        db, BackupManifest, backup_id, "BACKUP_NOT_FOUND", "没有找到这份本机备份。"
    )
    path = resolve_controlled_path(
        get_settings().resolved_asset_root, backup.relative_path
    )
    if not path.is_file() or calculate_sha256(path) != backup.archive_sha256:
        backup.status = "corrupt"
        db.commit()
        raise DomainError(
            "BACKUP_INTEGRITY_FAILED", "备份完整性校验失败，已阻止下载。", 409
        )
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"niannian-local-backup-{backup.id}.zip",
    )


@router.post("/backups/{backup_id}/verify", response_model=BackupRead)
def verify_backup_copy(
    backup_id: str, db: Session = Depends(get_db)
) -> BackupManifest:
    backup = require(
        db, BackupManifest, backup_id, "BACKUP_NOT_FOUND", "没有找到这份本机备份。"
    )
    path = resolve_controlled_path(
        get_settings().resolved_asset_root, backup.relative_path
    )
    try:
        with tempfile.TemporaryDirectory(prefix="niannian-restore-rehearsal-") as name:
            result = verify_local_backup(path, Path(name))
    except BackupError as exc:
        backup.status = "verification_failed"
        backup.verification_summary = {"error": str(exc)}
        db.commit()
        raise DomainError("BACKUP_VERIFICATION_FAILED", str(exc), 409) from exc
    backup.status = "verified"
    backup.verified_at = now_utc()
    backup.verification_summary = {
        "database_sha256": result.database_sha256,
        "asset_count": result.asset_count,
        "alembic_revision": result.alembic_revision,
        "restored_to_new_directory": True,
    }
    db.commit()
    db.refresh(backup)
    return backup


@router.post("/backups/{backup_id}/rehearse-recovery", response_model=BackupRead)
async def rehearse_backup_recovery(
    backup_id: str,
    family_id: str = Form(...),
    package: UploadFile = File(...),
    recovery_passphrase: str = Form(..., min_length=12, max_length=200),
    db: Session = Depends(get_db),
) -> BackupManifest:
    backup = require(
        db, BackupManifest, backup_id, "BACKUP_NOT_FOUND", "没有找到这份本机备份。"
    )
    path = resolve_controlled_path(
        get_settings().resolved_asset_root, backup.relative_path
    )
    try:
        package_bytes = await package.read(512 * 1024 + 1)
    finally:
        await package.close()
    if len(package_bytes) > 512 * 1024:
        raise DomainError("RECOVERY_PACKAGE_TOO_LARGE", "恢复包文件过大。", 413)
    try:
        master_key = recover_master_key(
            package_bytes.decode("utf-8"),
            recovery_passphrase,
            expected_scope=f"family:{family_id}",
        )
        with tempfile.TemporaryDirectory(prefix="niannian-full-recovery-") as name:
            verification = verify_local_backup(path, Path(name))
            recovery = rehearse_family_recovery(
                verification, family_id=family_id, master_key=master_key
            )
    except (BackupError, RecoveryPackageError, UnicodeDecodeError) as exc:
        backup.status = "recovery_failed"
        backup.verification_summary = {"recovery_verified": False}
        db.commit()
        raise DomainError(
            "BACKUP_RECOVERY_FAILED",
            "备份恢复演练失败：恢复包、口令、密文或备份完整性不匹配。",
            409,
        ) from exc
    backup.status = "recovery_verified"
    backup.verified_at = now_utc()
    backup.verification_summary = {
        "restored_to_new_directory": True,
        "recovery_verified": True,
        "family_id": family_id,
        "decrypted_field_count": recovery.decrypted_field_count,
        "decrypted_media_count": recovery.decrypted_media_count,
        "alembic_revision": verification.alembic_revision,
    }
    db.commit()
    db.refresh(backup)
    return backup


@router.post("/elder-profiles", response_model=ElderProfileRead, status_code=201)
def create_elder_profile(
    payload: ElderProfileCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ElderProfileRead:
    family = require(db, FamilyArchive, payload.family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    if payload.health_notes and family.data_classification != "authorized_sensitive":
        raise DomainError(
            "SENSITIVE_PROFILE_REQUIRES_ENCRYPTION",
            "健康与照护备注只能在完成恢复演练并启用敏感资料加密后保存。",
            409,
        )
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
        health_notes=payload.health_notes,
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
            "health_notes": payload.health_notes,
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


@router.patch("/people/{person_id}", response_model=PersonRead)
def update_family_person(
    person_id: str,
    payload: PersonUpdate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> PersonRead:
    person = require(db, Person, person_id, "PERSON_NOT_FOUND", "没有找到这位家庭成员。")
    if person.elder_profile and payload.role and payload.role != "elder":
        raise DomainError(
            "ELDER_ROLE_LOCKED", "讲述者的老人角色不能直接改为其他角色。", 409
        )
    if payload.role:
        person.role = payload.role.strip()
    if payload.display_name:
        display_name = payload.display_name.strip()
        person.display_name = display_name
        protect_values(
            db,
            person.family,
            person,
            {"display_name": display_name},
            secret_store,
        )
    db.commit()
    db.refresh(person)
    return person_read(db, person, secret_store)


@router.delete("/people/{person_id}", status_code=204)
def delete_family_person(
    person_id: str,
    db: Session = Depends(get_db),
) -> Response:
    person = require(db, Person, person_id, "PERSON_NOT_FOUND", "没有找到这位家庭成员。")
    if person.elder_profile:
        raise DomainError(
            "ELDER_DELETE_BLOCKED",
            "讲述者档案可能包含故事与媒体，不能在家谱界面中删除。",
            409,
        )
    relationship_ids = db.scalars(
        select(PersonRelationship.id).where(
            (PersonRelationship.from_person_id == person.id)
            | (PersonRelationship.to_person_id == person.id)
        )
    ).all()
    object_ids = [person.id, *relationship_ids]
    db.execute(
        delete(EncryptedField).where(
            EncryptedField.family_id == person.family_id,
            EncryptedField.object_id.in_(object_ids),
        )
    )
    db.delete(person)
    db.commit()
    return Response(status_code=204)


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


@router.delete("/relationships/{relationship_id}", status_code=204)
def delete_person_relationship(
    relationship_id: str,
    db: Session = Depends(get_db),
) -> Response:
    relationship = require(
        db,
        PersonRelationship,
        relationship_id,
        "RELATIONSHIP_NOT_FOUND",
        "没有找到这条家庭关系。",
    )
    db.execute(
        delete(EncryptedField).where(
            EncryptedField.family_id == relationship.family_id,
            EncryptedField.object_type == relationship.__tablename__,
            EncryptedField.object_id == relationship.id,
        )
    )
    db.delete(relationship)
    db.commit()
    return Response(status_code=204)


@router.get("/elder-profiles", response_model=list[ElderProfileRead])
def list_elder_profiles(
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[ElderProfileRead]:
    profiles = db.scalars(select(ElderProfile).order_by(ElderProfile.created_at.desc())).all()
    return [elder_read(db, profile, secret_store) for profile in profiles]


@router.patch("/elder-profiles/{profile_id}", response_model=ElderProfileRead)
def update_elder_profile(
    profile_id: str,
    payload: ElderProfileUpdate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ElderProfileRead:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。"
    )
    family = profile.person.family
    changes = payload.model_dump(exclude_unset=True)

    if "display_name" in changes and changes["display_name"] is not None:
        display_name = changes["display_name"].strip()
        profile.person.display_name = display_name
        protect_values(
            db,
            family,
            profile.person,
            {"display_name": display_name},
            secret_store,
        )

    profile_changes: dict[str, object | None] = {}
    if (
        changes.get("health_notes")
        and family.data_classification != "authorized_sensitive"
    ):
        raise DomainError(
            "SENSITIVE_PROFILE_REQUIRES_ENCRYPTION",
            "健康与照护备注只能在完成恢复演练并启用敏感资料加密后保存。",
            409,
        )
    for field_name in (
        "preferred_name",
        "birth_year",
        "birth_era",
        "native_place",
        "occupation_summary",
        "health_notes",
    ):
        if field_name not in changes:
            continue
        value = changes[field_name]
        if isinstance(value, str):
            value = value.strip() or None
        if field_name == "preferred_name" and value is None:
            continue
        setattr(profile, field_name, value)
        profile_changes[field_name] = value

    if profile_changes:
        protect_values(db, family, profile, profile_changes, secret_store)

    db.commit()
    db.refresh(profile)
    return elder_read(db, profile, secret_store)


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


@router.get(
    "/elder-profiles/{profile_id}/memory-sessions",
    response_model=list[MemorySessionRead],
)
def list_elder_memory_sessions(
    profile_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[MemorySessionRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    sessions = db.scalars(
        select(MemorySession)
        .where(MemorySession.elder_id == profile_id)
        .order_by(MemorySession.updated_at.desc(), MemorySession.created_at.desc())
        .limit(limit)
    ).all()
    return [memory_session_read(db, item, secret_store) for item in sessions]


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


@router.get(
    "/elder-profiles/{profile_id}/keepsake-catalog",
    response_model=list[KeepsakeCatalogItem],
)
def get_keepsake_catalog(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[KeepsakeCatalogItem]:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。"
    )
    family = profile.person.family
    key = family_master_key(family, secret_store)
    catalog: list[KeepsakeCatalogItem] = []
    for story in sorted(profile.stories, key=lambda item: item.confirmed_at):
        session = story.source_draft.session
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
        image = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.session_id == session.id,
                MediaAsset.kind.in_(["photo_original", "old_object_original"]),
                MediaAsset.status == "ready",
            )
            .order_by(MediaAsset.created_at.desc())
        )
        catalog.append(
            KeepsakeCatalogItem(
                story_id=story.id,
                title=secure_value(
                    db, family, story, "title", secret_store, master_key=key
                ),
                life_stage=session.life_stage,
                confirmed_at=story.confirmed_at,
                has_original_audio=audio is not None,
                audio_asset_id=audio.id if audio else None,
                image_asset_id=image.id if image else None,
                unavailable_reason=None if audio else "这篇故事没有保留可用的原始录音。",
            )
        )
    return catalog


@router.post(
    "/elder-profiles/{profile_id}/keepsake-authorizations",
    response_model=KeepsakeAuthorizationRead,
    status_code=201,
)
def create_keepsake_authorization(
    profile_id: str,
    payload: KeepsakeAuthorizationCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> KeepsakeAuthorizationRead:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。"
    )
    if not all(
        [
            payload.original_voice_authorized,
            payload.private_family_use,
            payload.no_impersonation,
            payload.original_audio_only,
        ]
    ):
        raise DomainError(
            "KEEPSAKE_AUTHORIZATION_INCOMPLETE",
            "四项授权说明都确认后才能制作数字念想。",
            409,
        )
    manifest = build_keepsake_manifest(db, profile, payload.story_ids)
    authorization = KeepsakeAuthorization(
        elder_id=profile.id,
        actor_label=payload.actor_label.strip(),
        story_ids=list(payload.story_ids),
        manifest_sha256=keepsake_manifest_sha256(manifest),
        original_voice_authorized=True,
        private_family_use=True,
        no_impersonation=True,
        original_audio_only=True,
        decision="granted",
    )
    db.add(authorization)
    db.flush()
    protect_values(
        db,
        profile.person.family,
        authorization,
        {"actor_label": payload.actor_label.strip()},
        secret_store,
    )
    db.commit()
    db.refresh(authorization)
    return keepsake_authorization_read(db, authorization, secret_store)


@router.get(
    "/elder-profiles/{profile_id}/keepsakes",
    response_model=list[KeepsakeRead],
)
def list_keepsakes(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[KeepsakeRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    items = list(
        db.scalars(
            select(Keepsake)
            .where(Keepsake.elder_id == profile_id)
            .order_by(Keepsake.version.desc())
        ).all()
    )
    return [keepsake_read(db, item, secret_store) for item in items]


@router.post(
    "/elder-profiles/{profile_id}/keepsakes",
    response_model=KeepsakeRead,
    status_code=201,
)
def create_keepsake(
    profile_id: str,
    payload: KeepsakeCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> KeepsakeRead:
    profile = require(
        db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。"
    )
    existing = db.scalar(
        select(Keepsake).where(
            Keepsake.elder_id == profile.id,
            Keepsake.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if existing.authorization_id != payload.authorization_id:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT",
                "这个提交标识已用于另一份念想。",
                409,
            )
        return keepsake_read(db, existing, secret_store)
    authorization = require(
        db,
        KeepsakeAuthorization,
        payload.authorization_id,
        "KEEPSAKE_AUTHORIZATION_REQUIRED",
        "请先确认本次原始录音使用授权。",
    )
    if authorization.elder_id != profile.id or authorization.decision != "granted":
        raise DomainError(
            "KEEPSAKE_AUTHORIZATION_MISMATCH", "这份授权不属于当前讲述者。", 409
        )
    if authorization.used_at is not None:
        raise DomainError(
            "KEEPSAKE_AUTHORIZATION_USED", "这份一次性授权已经使用。", 409
        )
    manifest = build_keepsake_manifest(db, profile, authorization.story_ids)
    if keepsake_manifest_sha256(manifest) != authorization.manifest_sha256:
        raise DomainError(
            "KEEPSAKE_AUTHORIZATION_MISMATCH",
            "所选故事或原始素材已变化，请重新确认授权。",
            409,
        )
    version = (
        db.scalar(select(func.max(Keepsake.version)).where(Keepsake.elder_id == profile.id))
        or 0
    ) + 1
    settings = get_settings()
    keepsake = Keepsake(
        elder_id=profile.id,
        authorization_id=authorization.id,
        version=version,
        title=payload.title.strip(),
        story_manifest=manifest,
        status="queued",
        progress=0,
        attempt=1,
        idempotency_key=payload.idempotency_key,
        width=settings.keepsake_width,
        height=settings.keepsake_height,
        renderer="local_ffmpeg",
        cost_cents=0,
        source_mode="original_audio_only",
    )
    db.add(keepsake)
    db.flush()
    protect_values(
        db,
        profile.person.family,
        keepsake,
        {"title": payload.title.strip()},
        secret_store,
    )
    authorization.used_at = now_utc()
    db.commit()
    db.refresh(keepsake)
    result = keepsake_read(db, keepsake, secret_store)
    background_tasks.add_task(process_keepsake, keepsake.id, secret_store)
    return result


@router.get("/keepsakes/{keepsake_id}", response_model=KeepsakeRead)
def get_keepsake(
    keepsake_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> KeepsakeRead:
    keepsake = require(
        db, Keepsake, keepsake_id, "KEEPSAKE_NOT_FOUND", "没有找到这份数字念想。"
    )
    return keepsake_read(db, keepsake, secret_store)


@router.post("/keepsakes/{keepsake_id}/retry", response_model=KeepsakeRead)
def retry_keepsake(
    keepsake_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> KeepsakeRead:
    keepsake = require(
        db, Keepsake, keepsake_id, "KEEPSAKE_NOT_FOUND", "没有找到这份数字念想。"
    )
    if keepsake.status != "failed_retryable" or keepsake.attempt >= 3:
        raise DomainError(
            "KEEPSAKE_NOT_RETRYABLE", "当前状态不能再次重试。", 409
        )
    keepsake.attempt += 1
    keepsake.status = "queued"
    keepsake.progress = 0
    keepsake.error_code = None
    db.commit()
    db.refresh(keepsake)
    result = keepsake_read(db, keepsake, secret_store)
    background_tasks.add_task(process_keepsake, keepsake.id, secret_store)
    return result


@router.get("/keepsakes/{keepsake_id}/content")
def download_keepsake(
    keepsake_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FileResponse:
    keepsake = require(
        db, Keepsake, keepsake_id, "KEEPSAKE_NOT_FOUND", "没有找到这份数字念想。"
    )
    if keepsake.status != "ready" or not keepsake.relative_path:
        raise DomainError("KEEPSAKE_NOT_READY", "这份数字念想还没有生成完成。", 409)
    settings = get_settings()
    stored = resolve_controlled_path(settings.resolved_asset_root, keepsake.relative_path)
    if not keepsake.size_bytes or not keepsake.sha256 or not verify_asset_integrity(
        stored, expected_size=keepsake.size_bytes, expected_sha256=keepsake.sha256
    ):
        keepsake.status = "corrupt"
        db.commit()
        raise DomainError("KEEPSAKE_CORRUPT", "视频完整性校验失败，已阻止播放和下载。", 409)
    filename = f"niannian-keepsake-v{keepsake.version}.mp4"
    if keepsake.encryption_version == 0:
        return FileResponse(stored, media_type="video/mp4", filename=filename)
    family = keepsake.elder.person.family
    key = family_master_key(family, secret_store)
    runtime_dir = settings.resolved_asset_root / "runtime" / "decrypted"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"keepsake-{keepsake.id}-", suffix=".mp4", dir=runtime_dir
    )
    os.close(descriptor)
    decrypted = Path(temporary_name)
    decrypted.unlink(missing_ok=True)
    try:
        result = decrypt_media_file(
            stored,
            decrypted,
            key,
            associated_data=media_context(family.id, keepsake.id),
        )
    except Exception as exc:
        decrypted.unlink(missing_ok=True)
        raise DomainError(
            "KEEPSAKE_DECRYPTION_FAILED", "视频无法解密，请从备份恢复。", 409
        ) from exc
    if (
        result.plaintext_size != keepsake.plaintext_size_bytes
        or result.plaintext_sha256 != keepsake.plaintext_sha256
    ):
        decrypted.unlink(missing_ok=True)
        raise DomainError(
            "KEEPSAKE_CORRUPT", "视频解密后完整性校验失败。", 409
        )
    return FileResponse(
        decrypted,
        media_type="video/mp4",
        filename=filename,
        background=BackgroundTask(decrypted.unlink, missing_ok=True),
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
    narrator_person_id = payload.narrator_person_id or elder.person_id
    narrator = require(
        db,
        Person,
        narrator_person_id,
        "NARRATOR_NOT_FOUND",
        "没有找到这位讲述人。",
    )
    if narrator.family_id != elder.person.family_id:
        raise DomainError(
            "NARRATOR_FAMILY_MISMATCH", "讲述人必须属于当前家庭档案。", 409
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
        narrator_person_id=narrator.id,
        interview_mode=payload.interview_mode,
        life_stage=payload.life_stage.strip(),
        prompt_id=question.prompt_id,
        question_text=question.question_text,
        status="INTERVIEWING" if payload.interview_mode == "guided_voice" else "PROMPT_READY",
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
    interview_turns = [
        interview_turn_read(db, turn, secret_store)
        for turn in sorted(session.interview_turns, key=lambda item: item.turn_index)
    ]
    tasks = sorted(session.tasks, key=lambda item: item.created_at, reverse=True)
    return SessionDetail(
        session=memory_session_read(db, session, secret_store),
        media_assets=assets,
        interview_turns=interview_turns,
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
    deleted_object_ids: list[str] = [turn.id for turn in session.interview_turns]
    for asset in list(session.media_assets):
        deleted_object_ids.append(asset.id)
        deleted_object_ids.extend(link.id for link in asset.links)
        path = resolve_controlled_path(settings.resolved_asset_root, asset.relative_path)
        path.unlink(missing_ok=True)
        db.delete(asset)
    if session.transcript:
        deleted_object_ids.append(session.transcript.id)
        db.delete(session.transcript)
    if session.story_draft:
        deleted_object_ids.append(session.story_draft.id)
        db.delete(session.story_draft)
    for task in list(session.tasks):
        db.delete(task)
    delete_secure_fields(
        db,
        family_id=session.elder.person.family_id,
        object_ids=deleted_object_ids,
    )
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


def _interview_history(
    db: Session, session: MemorySession, store: SecretStore
) -> list[dict[str, str]]:
    family = session.elder.person.family
    key = family_master_key(family, store)
    return [
        {
            "question": secure_value(
                db, family, turn, "question_text", store, master_key=key
            ),
            "answer": secure_value(
                db, family, turn, "corrected_answer_text", store, master_key=key
            ),
        }
        for turn in sorted(session.interview_turns, key=lambda item: item.turn_index)
    ]


@router.get("/memory-sessions/{session_id}/interview-question-audio")
def get_interview_question_audio(
    session_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> Response:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次采访。"
    )
    if session.interview_mode != "guided_voice" or session.status != "INTERVIEWING":
        raise DomainError("SESSION_NOT_INTERVIEWING", "当前记录不在语音采访中。", 409)
    question_text = secure_value(
        db, session.elder.person.family, session, "question_text", secret_store
    )
    try:
        result = get_tts_provider().synthesize(question_text)
    except Exception as exc:
        raise DomainError(
            "INTERVIEW_SPEECH_FAILED",
            "自然语音暂时不可用，可以继续查看文字问题。",
            503,
        ) from exc
    return Response(
        content=result.audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="lingnian-question.wav"',
            "X-Lingnian-Speech-Provider": result.provider,
            "X-Lingnian-Speech-Model": result.model,
            "X-Lingnian-Speech-Voice": result.voice,
        },
    )


async def _persist_interview_question_audio(
    db: Session,
    session: MemorySession,
    question_text: str,
    secret_store: SecretStore,
    upload: UploadFile | None,
) -> MediaAsset:
    settings = get_settings()
    family = session.elder.person.family
    if upload is not None:
        stored = await store_audio_upload(upload, session.id, settings)
    else:
        speech = get_tts_provider().synthesize(question_text)
        asset_id = str(uuid4())
        relative_path = f"assets/generated/{session.id}/{asset_id}.wav"
        path = resolve_controlled_path(settings.resolved_asset_root, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(speech.audio)
        path.chmod(0o600)
        stored = {
            "relative_path": relative_path,
            "original_filename": f"聆年提问-{len(session.interview_turns) + 1}.wav",
            "mime_type": "audio/wav",
            "size_bytes": path.stat().st_size,
            "sha256": calculate_sha256(path),
        }
    asset = MediaAsset(
        session_id=session.id,
        kind="interview_question_audio",
        status="ready",
        is_original=False,
        **stored,
    )
    db.add(asset)
    db.flush()
    protect_values(
        db,
        family,
        asset,
        {"original_filename": asset.original_filename},
        secret_store,
    )
    encrypt_asset_if_needed(db, asset, family, secret_store)
    return asset


@router.post(
    "/memory-sessions/{session_id}/interview-turns/audio",
    response_model=InterviewTurnRead,
    status_code=201,
)
async def upload_interview_turn_audio(
    session_id: str,
    audio: UploadFile = File(...),
    question_audio: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> InterviewTurnRead:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次采访。"
    )
    if session.interview_mode != "guided_voice" or session.status != "INTERVIEWING":
        raise DomainError("SESSION_NOT_INTERVIEWING", "当前记录不在语音采访中。", 409)
    if any(turn.status == "answer_review" for turn in session.interview_turns):
        raise DomainError("INTERVIEW_TURN_PENDING", "请先确认上一段回答。", 409)
    if len(session.interview_turns) >= 12:
        raise DomainError("INTERVIEW_TURN_LIMIT", "这次采访已经达到 12 轮，请先结束并整理。", 409)

    stored = await store_audio_upload(audio, session.id, get_settings())
    asset_path = resolve_controlled_path(
        get_settings().resolved_asset_root, stored["relative_path"]
    )
    question_asset_path: Path | None = None
    try:
        asset = MediaAsset(
            session_id=session.id,
            kind="interview_turn_audio",
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

        with tempfile.TemporaryDirectory(prefix="lingnian-interview-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = asset_path
            if asset.encryption_version == 1:
                source_path = temp_dir / "answer.source"
                key = family_master_key(family, secret_store)
                if key is None:
                    raise DomainError("MASTER_KEY_MISSING", "无法解锁家庭档案。", 409)
                decrypted = decrypt_media_file(
                    asset_path,
                    source_path,
                    key,
                    associated_data=media_context(family.id, asset.id),
                )
                if (
                    decrypted.plaintext_size != asset.plaintext_size_bytes
                    or decrypted.plaintext_sha256 != asset.plaintext_sha256
                ):
                    raise RuntimeError("ASSET_PLAINTEXT_INTEGRITY_FAILED")
            normalized_path = temp_dir / "answer.wav"
            normalize_audio(source_path, normalized_path)
            result = get_asr_provider().transcribe(normalized_path)

        question_text = secure_value(
            db, family, session, "question_text", secret_store
        )
        question_asset = await _persist_interview_question_audio(
            db,
            session,
            question_text,
            secret_store,
            question_audio,
        )
        question_asset_path = resolve_controlled_path(
            get_settings().resolved_asset_root, question_asset.relative_path
        )
        turn = InterviewTurn(
            session_id=session.id,
            turn_index=len(session.interview_turns) + 1,
            question_text=question_text,
            raw_answer_text=result.text,
            corrected_answer_text=result.text,
            answer_version=1,
            audio_asset_id=asset.id,
            question_audio_asset_id=question_asset.id,
            asr_provider=result.provider,
            asr_model=result.model,
            asr_metadata=result.metadata,
            followup_mode="pending",
            status="answer_review",
        )
        db.add(turn)
        db.flush()
        protect_values(
            db,
            family,
            turn,
            {
                "question_text": question_text,
                "raw_answer_text": result.text,
                "corrected_answer_text": result.text,
                "asr_metadata": result.metadata,
            },
            secret_store,
        )
        db.commit()
        db.refresh(turn)
        return interview_turn_read(db, turn, secret_store)
    except DomainError:
        db.rollback()
        asset_path.unlink(missing_ok=True)
        if question_asset_path:
            question_asset_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        db.rollback()
        asset_path.unlink(missing_ok=True)
        if question_asset_path:
            question_asset_path.unlink(missing_ok=True)
        raise DomainError(
            "INTERVIEW_TRANSCRIPTION_FAILED",
            "这段回答暂时没有转写成功，请保留原录音并重试。",
            503,
        ) from exc


@router.patch(
    "/interview-turns/{turn_id}", response_model=InterviewTurnRead
)
def update_interview_turn(
    turn_id: str,
    payload: InterviewTurnUpdate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> InterviewTurnRead:
    turn = require(
        db, InterviewTurn, turn_id, "INTERVIEW_TURN_NOT_FOUND", "没有找到这轮回答。"
    )
    if turn.session.status != "INTERVIEWING" or turn.status != "answer_review":
        raise DomainError("INTERVIEW_TURN_LOCKED", "当前回答不能再修改。", 409)
    corrected = payload.corrected_answer_text.strip()
    turn.corrected_answer_text = corrected
    turn.answer_version += 1
    protect_values(
        db,
        turn.session.elder.person.family,
        turn,
        {"corrected_answer_text": corrected},
        secret_store,
    )
    db.commit()
    db.refresh(turn)
    return interview_turn_read(db, turn, secret_store)


@router.post(
    "/memory-sessions/{session_id}/interview-turns/{turn_id}/continue",
    response_model=InterviewContinueResult,
)
def continue_guided_interview(
    session_id: str,
    turn_id: str,
    payload: InterviewContinueRequest,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> InterviewContinueResult:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次采访。"
    )
    turn = require(
        db, InterviewTurn, turn_id, "INTERVIEW_TURN_NOT_FOUND", "没有找到这轮回答。"
    )
    if turn.session_id != session.id or turn.status != "answer_review":
        raise DomainError("INTERVIEW_TURN_LOCKED", "这轮回答已经确认或不属于当前采访。", 409)
    if session.status != "INTERVIEWING":
        raise DomainError("SESSION_NOT_INTERVIEWING", "当前记录不在语音采访中。", 409)

    family = session.elder.person.family
    source_text = payload.corrected_answer_text.strip()
    history = _interview_history(db, session, secret_store)
    subject_name = secure_value(
        db, family, session.elder, "preferred_name", secret_store
    )
    narrator = session.narrator or session.elder.person
    narrator_name = secure_value(
        db, family, narrator, "display_name", secret_store
    )
    settings = get_settings()
    followup_mode = "local_private"
    cloud_succeeded = False
    if (
        settings.llm_provider == "qwen"
        and family.data_classification != "test"
        and payload.allow_cloud_followup
    ):
        try:
            cleanup = get_llm_provider().clean_interview_transcript(source_text)
            corrected = cleanup.polished_text
            history[-1]["answer"] = corrected
            followup = get_llm_provider().generate_interview_followup(
                subject_name, narrator_name, session.life_stage, history
            )
            cloud_succeeded = True
            followup_mode = "qwen_auto_polish_and_followup"
        except Exception:
            cleanup = clean_local_interview_transcript(source_text)
            corrected = cleanup.polished_text
            history[-1]["answer"] = corrected
            followup = generate_local_interview_followup(
                subject_name, narrator_name, session.life_stage, history
            )
            followup_mode = "local_fallback"
    else:
        cleanup = (
            get_llm_provider().clean_interview_transcript(source_text)
            if family.data_classification == "test"
            else clean_local_interview_transcript(source_text)
        )
        corrected = cleanup.polished_text
        history[-1]["answer"] = corrected
        followup = (
            get_llm_provider().generate_interview_followup(
                subject_name, narrator_name, session.life_stage, history
            )
            if family.data_classification == "test"
            else generate_local_interview_followup(
                subject_name, narrator_name, session.life_stage, history
            )
        )
        followup_mode = "test_or_local_model"

    if cloud_succeeded:
        consent_input = json.dumps(
            {
                "raw_transcript": source_text,
                "memory_subject": subject_name,
                "narrator": narrator_name,
                "life_stage": session.life_stage,
                "turns": history[-6:],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        consent = ModelConsentEvent(
            family_id=family.id,
            session_id=session.id,
            actor_label=payload.actor_label.strip(),
            purpose="interview_auto_polish_and_followup",
            data_classification=family.data_classification,
            decision="granted",
            one_time=True,
            input_sha256=hashlib.sha256(consent_input.encode("utf-8")).hexdigest(),
            used_at=now_utc(),
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

    turn.corrected_answer_text = corrected
    turn.answer_version += 1
    turn.followup_mode = followup_mode
    turn.status = "complete"
    current_metadata = secure_value(
        db, family, turn, "asr_metadata", secret_store
    )
    turn.asr_metadata = {
        **(current_metadata if isinstance(current_metadata, dict) else {}),
        "cleanup_mode": followup_mode,
        "cleanup_uncertainties": cleanup.uncertainties,
    }
    session.question_text = followup.next_question.strip()
    protect_values(
        db,
        family,
        turn,
        {
            "corrected_answer_text": corrected,
            "asr_metadata": turn.asr_metadata,
        },
        secret_store,
    )
    protect_values(
        db,
        family,
        session,
        {"question_text": followup.next_question.strip()},
        secret_store,
    )
    db.commit()
    db.refresh(turn)
    db.refresh(session)
    return InterviewContinueResult(
        session=memory_session_read(db, session, secret_store),
        turn=interview_turn_read(db, turn, secret_store),
        acknowledgement=followup.acknowledgement,
        next_question=followup.next_question,
        should_end=followup.should_end,
        followup_mode=followup_mode,
    )


@router.post(
    "/memory-sessions/{session_id}/interview-question/replace",
    response_model=MemorySessionRead,
)
def replace_interview_question(
    session_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MemorySessionRead:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次采访。"
    )
    if session.interview_mode != "guided_voice" or session.status != "INTERVIEWING":
        raise DomainError("SESSION_NOT_INTERVIEWING", "当前记录不在语音采访中。", 409)
    if any(turn.status == "answer_review" for turn in session.interview_turns):
        raise DomainError("INTERVIEW_TURN_PENDING", "请先确认已经录下的回答。", 409)
    family = session.elder.person.family
    subject_name = secure_value(
        db, family, session.elder, "preferred_name", secret_store
    )
    narrator = session.narrator or session.elder.person
    narrator_name = secure_value(
        db, family, narrator, "display_name", secret_store
    )
    history = _interview_history(db, session, secret_store)
    history.append({"question": "", "answer": "不想回答这一题"})
    followup = generate_local_interview_followup(
        subject_name, narrator_name, session.life_stage, history
    )
    session.question_text = followup.next_question
    protect_values(
        db,
        family,
        session,
        {"question_text": followup.next_question},
        secret_store,
    )
    db.commit()
    db.refresh(session)
    return memory_session_read(db, session, secret_store)


def _create_complete_interview_audio(
    db: Session,
    session: MemorySession,
    store: SecretStore,
) -> MediaAsset | None:
    existing = db.scalar(
        select(MediaAsset).where(
            MediaAsset.session_id == session.id,
            MediaAsset.kind == "audio_original",
            MediaAsset.status == "ready",
        )
    )
    if existing:
        return existing
    turns = [turn for turn in session.interview_turns if turn.audio_asset_id]
    if not turns:
        return None
    settings = get_settings()
    family = session.elder.person.family
    key = family_master_key(family, store)
    asset_id = str(uuid4())
    relative_path = f"assets/original/{session.id}/{asset_id}.wav"
    final_path = resolve_controlled_path(settings.resolved_asset_root, relative_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="lingnian-interview-merge-") as temp_name:
            temp_dir = Path(temp_name)
            normalized_paths: list[Path] = []
            for index, turn in enumerate(turns):
                sequence = (
                    ("question", turn.question_audio_asset),
                    ("answer", turn.audio_asset),
                )
                for segment, source_asset in sequence:
                    if source_asset is None:
                        continue
                    stored_path = resolve_controlled_path(
                        settings.resolved_asset_root, source_asset.relative_path
                    )
                    source_path = stored_path
                    if source_asset.encryption_version == 1:
                        if key is None:
                            raise RuntimeError("MASTER_KEY_MISSING")
                        source_path = temp_dir / f"source-{index}-{segment}"
                        decrypted = decrypt_media_file(
                            stored_path,
                            source_path,
                            key,
                            associated_data=media_context(family.id, source_asset.id),
                        )
                        if (
                            decrypted.plaintext_size != source_asset.plaintext_size_bytes
                            or decrypted.plaintext_sha256 != source_asset.plaintext_sha256
                        ):
                            raise RuntimeError("ASSET_PLAINTEXT_INTEGRITY_FAILED")
                    normalized = temp_dir / f"turn-{index}-{segment}.wav"
                    normalize_audio(source_path, normalized)
                    normalized_paths.append(normalized)
            if not normalized_paths:
                return None
            concat_file = temp_dir / "inputs.txt"
            concat_file.write_text(
                "".join(f"file '{path.as_posix()}'\n" for path in normalized_paths),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    get_ffmpeg_binary(),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(final_path),
                ],
                capture_output=True,
                text=True,
                timeout=240,
            )
            if completed.returncode != 0 or not final_path.is_file():
                raise RuntimeError("INTERVIEW_AUDIO_MERGE_FAILED")
        final_path.chmod(0o600)
        asset = MediaAsset(
            id=asset_id,
            session_id=session.id,
            kind="audio_original",
            relative_path=relative_path,
            original_filename="完整采访录音.wav",
            mime_type="audio/wav",
            size_bytes=final_path.stat().st_size,
            sha256=calculate_sha256(final_path),
            status="ready",
            is_original=True,
        )
        db.add(asset)
        db.flush()
        protect_values(
            db,
            family,
            asset,
            {"original_filename": "完整采访录音.wav"},
            store,
        )
        encrypt_asset_if_needed(db, asset, family, store)
        return asset
    except Exception:
        final_path.unlink(missing_ok=True)
        raise


@router.post(
    "/memory-sessions/{session_id}/interview-finalize",
    response_model=SessionDetail,
)
def finalize_guided_interview(
    session_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> SessionDetail:
    session = require(
        db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到这次采访。"
    )
    if session.interview_mode != "guided_voice" or session.status != "INTERVIEWING":
        raise DomainError("SESSION_NOT_INTERVIEWING", "当前记录不能结束采访。", 409)
    if not session.interview_turns:
        raise DomainError("INTERVIEW_EMPTY", "至少留下一个回答后再结束采访。", 409)
    family = session.elder.person.family
    narrator = session.narrator or session.elder.person
    narrator_name = secure_value(
        db, family, narrator, "display_name", secret_store
    )
    key = family_master_key(family, secret_store)
    ordered_turns = sorted(session.interview_turns, key=lambda item: item.turn_index)
    raw_blocks: list[str] = []
    corrected_blocks: list[str] = []
    for turn in ordered_turns:
        question = secure_value(
            db, family, turn, "question_text", secret_store, master_key=key
        )
        raw_answer = secure_value(
            db, family, turn, "raw_answer_text", secret_store, master_key=key
        )
        corrected_answer = secure_value(
            db, family, turn, "corrected_answer_text", secret_store, master_key=key
        )
        raw_blocks.append(f"采访者：{question}\n{narrator_name}：{raw_answer}")
        corrected_blocks.append(
            f"采访者：{question}\n{narrator_name}：{corrected_answer}"
        )
        if turn.status == "answer_review":
            turn.status = "complete"
            turn.followup_mode = "interview_ended"
    raw_text = "\n\n".join(raw_blocks)
    corrected_text = "\n\n".join(corrected_blocks)
    transcript = session.transcript
    if transcript is None:
        transcript = Transcript(
            session_id=session.id,
            raw_text=raw_text,
            corrected_text=corrected_text,
            version=1,
            asr_provider="interview_aggregate",
            asr_model="per-turn-local-asr",
            asr_metadata={"turn_count": len(ordered_turns)},
        )
        db.add(transcript)
        db.flush()
    else:
        transcript.raw_text = raw_text
        transcript.corrected_text = corrected_text
        transcript.version += 1
        transcript.asr_metadata = {"turn_count": len(ordered_turns)}
    protect_values(
        db,
        family,
        transcript,
        {
            "raw_text": raw_text,
            "corrected_text": corrected_text,
            "asr_metadata": {"turn_count": len(ordered_turns)},
        },
        secret_store,
    )
    try:
        _create_complete_interview_audio(db, session, secret_store)
    except Exception as exc:
        db.rollback()
        raise DomainError(
            "INTERVIEW_AUDIO_MERGE_FAILED",
            "回答已经保留，但完整录音暂时没有合并成功，请稍后重试结束采访。",
            503,
        ) from exc
    session.status = "TRANSCRIPT_REVIEW"
    db.commit()
    db.expire_all()
    return get_memory_session(session.id, db, secret_store)


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
    delete_secure_fields(
        db,
        family_id=asset.session.elder.person.family_id,
        object_ids=[asset.id, *[link.id for link in asset.links]],
    )
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
        session = story.source_draft.session
        narrator = session.narrator or session.elder.person
        narrator_label = secure_value(
            db, profile.person.family, narrator, "display_name", secret_store
        )
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
        trigger_image = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.session_id == session.id,
                MediaAsset.kind.in_(["photo_original", "old_object_original"]),
                MediaAsset.is_original.is_(True),
            )
            .order_by(MediaAsset.created_at.desc())
        )
        trigger_link = (
            trigger_image.links[0]
            if trigger_image and trigger_image.links
            else None
        )
        items.append(
            TimelineItem(
                story=story_read(db, story, secret_store),
                life_stage=session.life_stage,
                narrator_person_id=narrator.id,
                narrator_label=narrator_label,
                narration_kind=(
                    "first_person"
                    if narrator.id == session.elder.person_id
                    else "family_recollection"
                ),
                events=[
                    timeline_event_read(
                        db, event, profile.person.family, secret_store
                    )
                    for event in story.timeline_events
                ],
                audio_url=f"/api/v1/media-assets/{original.id}/content" if original else None,
                image_url=(
                    f"/api/v1/media-assets/{trigger_image.id}/content"
                    if trigger_image
                    else None
                ),
                image_asset_id=trigger_image.id if trigger_image else None,
                image_annotation=(
                    media_link_read(db, trigger_link, secret_store).user_annotation
                    if trigger_link
                    else None
                ),
                detail=(
                    story_detail_read(db, story.detail, secret_store)
                    if story.detail
                    else None
                ),
                contributions=[
                    story_contribution_read(db, item, secret_store)
                    for item in sorted(story.contributions, key=lambda value: value.created_at)
                ],
                person_tags=(
                    [
                        media_person_tag_read(db, tag, secret_store)
                        for tag in db.scalars(
                            select(MediaPersonTag)
                            .where(MediaPersonTag.media_asset_id == trigger_image.id)
                            .order_by(MediaPersonTag.created_at)
                        ).all()
                    ]
                    if trigger_image
                    else []
                ),
            )
        )
    return items


@router.post(
    "/elder-profiles/{profile_id}/archive-questions",
    response_model=ArchiveAnswer,
)
def ask_family_archive(
    profile_id: str,
    payload: ArchiveAskRequest,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ArchiveAnswer:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    family = profile.person.family
    key = family_master_key(family, secret_store)
    stories = db.scalars(
        select(Story).where(Story.elder_id == profile.id).order_by(Story.confirmed_at.desc())
    ).all()
    documents: list[SearchDocument] = []
    for story in stories:
        session = story.source_draft.session
        narrator = session.narrator or session.elder.person
        narrator_label = secure_value(
            db, family, narrator, "display_name", secret_store, master_key=key
        )
        audio = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.session_id == session.id,
                MediaAsset.kind == "audio_original",
                MediaAsset.is_original.is_(True),
            )
            .order_by(MediaAsset.created_at.desc())
        )
        image = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.session_id == session.id,
                MediaAsset.kind.in_(["photo_original", "old_object_original"]),
                MediaAsset.is_original.is_(True),
            )
            .order_by(MediaAsset.created_at.desc())
        )
        documents.append(
            SearchDocument(
                story_id=story.id,
                title=secure_value(db, family, story, "title", secret_store, master_key=key),
                body=secure_value(db, family, story, "body", secret_store, master_key=key),
                life_stage=session.life_stage,
                audio_url=f"/api/v1/media-assets/{audio.id}/content" if audio else None,
                image_url=f"/api/v1/media-assets/{image.id}/content" if image else None,
                source_id=story.id,
                source_kind=(
                    "elder_story"
                    if narrator.id == session.elder.person_id
                    else "family_recollection"
                ),
                source_label=narrator_label,
            )
        )
        story_title = secure_value(db, family, story, "title", secret_store, master_key=key)
        for contribution in story.contributions:
            if contribution.status not in {"confirmed", "resolved"}:
                continue
            contribution_view = story_contribution_read(db, contribution, secret_store)
            documents.append(
                SearchDocument(
                    story_id=story.id,
                    title=f"关于《{story_title}》的家人补充",
                    body=contribution_view.body,
                    life_stage=session.life_stage,
                    audio_url=None,
                    image_url=None,
                    source_id=contribution.id,
                    source_kind="family_contribution",
                    source_label=contribution_view.contributor_label,
                )
            )
    ranked = [item for item in rank_archive(payload.question, documents) if item.score >= 0.2]
    selected = ranked[: payload.max_citations]
    answer, follow_up = compose_grounded_answer(payload.question, selected)
    return ArchiveAnswer(
        question=payload.question.strip(),
        status="grounded" if selected else "not_found",
        answer=answer,
        citations=[
            ArchiveCitation(
                source_id=item.document.source_id or item.document.story_id,
                story_id=item.document.story_id,
                source_kind=item.document.source_kind,
                source_label=item.document.source_label,
                title=item.document.title,
                life_stage=item.document.life_stage,
                excerpt=item.excerpt,
                audio_url=item.document.audio_url,
                image_url=item.document.image_url,
                score=item.score,
            )
            for item in selected
        ],
        follow_up_question=follow_up,
    )


@router.post(
    "/elder-profiles/{profile_id}/archive-gaps",
    response_model=MemorySessionRead,
    status_code=201,
)
def create_archive_gap_session(
    profile_id: str,
    payload: ArchiveGapCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MemorySessionRead:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    family = profile.person.family
    question = payload.question.strip()
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    session = MemorySession(
        elder_id=profile.id,
        life_stage=payload.life_stage.strip(),
        prompt_id=f"family-question:{digest[:20]}:{uuid4().hex[:8]}",
        question_text=question,
        status="PROMPT_READY",
    )
    db.add(session)
    db.flush()
    protect_values(db, family, session, {"question_text": question}, secret_store)
    event = ConsentEvent(
        action="create_family_question",
        actor_label=payload.actor_label.strip(),
        object_type="memory_session",
        object_id=session.id,
    )
    db.add(event)
    db.flush()
    protect_values(
        db,
        family,
        event,
        {"actor_label": payload.actor_label.strip()},
        secret_store,
    )
    db.commit()
    db.refresh(session)
    return memory_session_read(db, session, secret_store)


@router.put("/stories/{story_id}/detail", response_model=StoryDetailRead)
def upsert_story_detail(
    story_id: str,
    payload: StoryDetailUpsert,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> StoryDetailRead:
    story = require(db, Story, story_id, "STORY_NOT_FOUND", "没有找到这篇故事。")
    family = story.elder.person.family
    detail = db.scalar(select(StoryDetail).where(StoryDetail.story_id == story.id))
    values = {
        "place_name": payload.place_name.strip() if payload.place_name else None,
        "event_year": payload.event_year,
        "theme_tags": payload.theme_tags,
        "summary": payload.summary.strip() if payload.summary else None,
        "updated_by": payload.updated_by.strip(),
    }
    if detail is None:
        detail = StoryDetail(story_id=story.id, **values)
        db.add(detail)
        db.flush()
    else:
        for field_name, value in values.items():
            setattr(detail, field_name, value)
    protect_values(db, family, detail, values, secret_store)
    db.commit()
    db.refresh(detail)
    return story_detail_read(db, detail, secret_store)


@router.post(
    "/stories/{story_id}/contributions",
    response_model=StoryContributionRead,
    status_code=201,
)
def create_story_contribution(
    story_id: str,
    payload: StoryContributionCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> StoryContributionRead:
    story = require(db, Story, story_id, "STORY_NOT_FOUND", "没有找到这篇故事。")
    family = story.elder.person.family
    if payload.contributor_person_id:
        person = require(db, Person, payload.contributor_person_id, "PERSON_NOT_FOUND", "没有找到这位家庭成员。")
        if person.family_id != family.id:
            raise DomainError("CROSS_FAMILY_PERSON", "不能使用其他家庭的成员身份。", 409)
    contribution = StoryContribution(
        story_id=story.id,
        contributor_person_id=payload.contributor_person_id,
        contributor_label=payload.contributor_label.strip(),
        contribution_type=payload.contribution_type,
        body=payload.body.strip(),
        status="open",
    )
    db.add(contribution)
    db.flush()
    protect_values(
        db,
        family,
        contribution,
        {
            "contributor_label": payload.contributor_label.strip(),
            "body": payload.body.strip(),
        },
        secret_store,
    )
    db.commit()
    db.refresh(contribution)
    return story_contribution_read(db, contribution, secret_store)


@router.get(
    "/stories/{story_id}/contributions",
    response_model=list[StoryContributionRead],
)
def list_story_contributions(
    story_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[StoryContributionRead]:
    require(db, Story, story_id, "STORY_NOT_FOUND", "没有找到这篇故事。")
    contributions = db.scalars(
        select(StoryContribution)
        .where(StoryContribution.story_id == story_id)
        .order_by(StoryContribution.created_at)
    ).all()
    return [story_contribution_read(db, item, secret_store) for item in contributions]


@router.patch(
    "/story-contributions/{contribution_id}/status",
    response_model=StoryContributionRead,
)
def update_story_contribution_status(
    contribution_id: str,
    payload: StoryContributionStatusUpdate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> StoryContributionRead:
    contribution = require(
        db,
        StoryContribution,
        contribution_id,
        "CONTRIBUTION_NOT_FOUND",
        "没有找到这条家人补充。",
    )
    family = contribution.story.elder.person.family
    contribution.status = payload.status
    event = ConsentEvent(
        action=f"review_story_contribution:{payload.status}",
        actor_label=payload.actor_label.strip(),
        object_type="story_contribution",
        object_id=contribution.id,
    )
    db.add(event)
    db.flush()
    protect_values(
        db,
        family,
        event,
        {"actor_label": payload.actor_label.strip()},
        secret_store,
    )
    db.commit()
    db.refresh(contribution)
    return story_contribution_read(db, contribution, secret_store)


@router.post(
    "/media-assets/{asset_id}/person-tags",
    response_model=MediaPersonTagRead,
    status_code=201,
)
def create_media_person_tag(
    asset_id: str,
    payload: MediaPersonTagCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> MediaPersonTagRead:
    asset = require(db, MediaAsset, asset_id, "ASSET_NOT_FOUND", "没有找到这张照片。")
    if asset.kind not in {"photo_original", "old_object_original"}:
        raise DomainError("IMAGE_REQUIRED", "只有照片或老物件图片可以标注人物。", 409)
    family = asset.session.elder.person.family
    person = require(db, Person, payload.person_id, "PERSON_NOT_FOUND", "没有找到这位家庭成员。")
    if person.family_id != family.id:
        raise DomainError("CROSS_FAMILY_PERSON", "不能把其他家庭成员标注到这张照片。", 409)
    existing = db.scalar(
        select(MediaPersonTag).where(
            MediaPersonTag.media_asset_id == asset.id,
            MediaPersonTag.person_id == person.id,
        )
    )
    if existing:
        return media_person_tag_read(db, existing, secret_store)
    tag = MediaPersonTag(
        family_id=family.id,
        media_asset_id=asset.id,
        person_id=person.id,
        tagged_by=payload.tagged_by.strip(),
        note=payload.note.strip() if payload.note else None,
    )
    db.add(tag)
    db.flush()
    protect_values(
        db,
        family,
        tag,
        {
            "tagged_by": payload.tagged_by.strip(),
            "note": payload.note.strip() if payload.note else None,
        },
        secret_store,
    )
    db.commit()
    db.refresh(tag)
    return media_person_tag_read(db, tag, secret_store)


@router.get(
    "/media-assets/{asset_id}/person-tags",
    response_model=list[MediaPersonTagRead],
)
def list_media_person_tags(
    asset_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[MediaPersonTagRead]:
    require(db, MediaAsset, asset_id, "ASSET_NOT_FOUND", "没有找到这张照片。")
    tags = db.scalars(
        select(MediaPersonTag)
        .where(MediaPersonTag.media_asset_id == asset_id)
        .order_by(MediaPersonTag.created_at)
    ).all()
    return [media_person_tag_read(db, item, secret_store) for item in tags]


@router.get("/families/{family_id}/legacy-plan", response_model=LegacyPlanRead | None)
def get_legacy_plan(
    family_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> LegacyPlanRead | None:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    plan = db.scalar(select(LegacyPlan).where(LegacyPlan.family_id == family.id))
    return legacy_plan_read(db, plan, family, secret_store) if plan else None


@router.put("/families/{family_id}/legacy-plan", response_model=LegacyPlanRead)
def upsert_legacy_plan(
    family_id: str,
    payload: LegacyPlanUpsert,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> LegacyPlanRead:
    family = require(db, FamilyArchive, family_id, "FAMILY_NOT_FOUND", "没有找到这个家庭档案。")
    people = db.scalars(
        select(Person).where(Person.id.in_(payload.successor_person_ids))
    ).all()
    if len({person.id for person in people}) != len(set(payload.successor_person_ids)) or any(
        person.family_id != family.id for person in people
    ):
        raise DomainError("INVALID_SUCCESSOR", "指定接管人必须全部属于当前家庭。", 409)
    plan = db.scalar(select(LegacyPlan).where(LegacyPlan.family_id == family.id))
    values = {
        "successor_person_ids": list(dict.fromkeys(payload.successor_person_ids)),
        "steward_label": payload.steward_label.strip(),
        "note": payload.note.strip() if payload.note else None,
    }
    if plan is None:
        plan = LegacyPlan(
            family_id=family.id,
            access_policy=payload.access_policy,
            confirmed_at=now_utc(),
            **values,
        )
        db.add(plan)
        db.flush()
    else:
        plan.access_policy = payload.access_policy
        plan.confirmed_at = now_utc()
        for field_name, value in values.items():
            setattr(plan, field_name, value)
    protect_values(db, family, plan, values, secret_store)
    db.commit()
    db.refresh(plan)
    return legacy_plan_read(db, plan, family, secret_store)


@router.post("/elder-profiles/{profile_id}/heritage-package")
def download_heritage_package(
    profile_id: str,
    payload: HeritageExportCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FileResponse:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    family = profile.person.family
    key = family_master_key(family, secret_store)
    family_view = family_read(db, family, secret_store)
    profile_view = elder_read(db, profile, secret_store)
    people = db.scalars(
        select(Person).where(Person.family_id == family.id).order_by(Person.created_at)
    ).all()
    relationships = db.scalars(
        select(PersonRelationship)
        .where(PersonRelationship.family_id == family.id)
        .order_by(PersonRelationship.created_at)
    ).all()
    stories = db.scalars(
        select(Story).where(Story.elder_id == profile.id).order_by(Story.confirmed_at)
    ).all()
    temp_root = Path(tempfile.mkdtemp(prefix="lingnian-heritage-"))
    try:
        media_root = temp_root / "materialized"
        media_root.mkdir(mode=0o700)

        def materialize(asset: MediaAsset, target_name: str) -> Path:
            source_path = resolve_controlled_path(
                get_settings().resolved_asset_root, asset.relative_path
            )
            if not verify_asset_integrity(
                source_path,
                expected_size=asset.size_bytes,
                expected_sha256=asset.sha256,
            ):
                raise DomainError(
                    "ASSET_INTEGRITY_FAILED",
                    "传承包中的媒体校验失败，请先从备份恢复。",
                    409,
                )
            target = media_root / target_name
            if asset.encryption_version == 0:
                shutil.copyfile(source_path, target)
            elif asset.encryption_version == 1 and key is not None:
                decrypt_media_file(
                    source_path,
                    target,
                    key,
                    associated_data=media_context(family.id, asset.id),
                )
            else:
                raise DomainError("ASSET_ENCRYPTION_UNSUPPORTED", "无法导出这份加密媒体。", 409)
            target.chmod(0o600)
            return target

        heritage_stories: list[HeritageStory] = []
        type_labels = {
            "context": "补充背景",
            "correction": "更正线索",
            "question": "继续追问",
            "alternate_memory": "另一种记忆",
        }
        for story in stories:
            story_view = story_read(db, story, secret_store)
            session = story.source_draft.session
            audio = db.scalar(
                select(MediaAsset)
                .where(
                    MediaAsset.session_id == session.id,
                    MediaAsset.kind == "audio_original",
                    MediaAsset.is_original.is_(True),
                )
                .order_by(MediaAsset.created_at.desc())
            )
            image = db.scalar(
                select(MediaAsset)
                .where(
                    MediaAsset.session_id == session.id,
                    MediaAsset.kind.in_(["photo_original", "old_object_original"]),
                    MediaAsset.is_original.is_(True),
                )
                .order_by(MediaAsset.created_at.desc())
            )
            audio_media = None
            if audio:
                audio_extension = {
                    "audio/wav": ".wav",
                    "audio/mpeg": ".mp3",
                    "audio/mp4": ".m4a",
                    "audio/webm": ".webm",
                    "audio/ogg": ".ogg",
                }.get(audio.mime_type, ".audio")
                audio_archive_path = f"media/audio/{story.id}{audio_extension}"
                audio_media = HeritageMedia(
                    source_path=materialize(audio, f"{story.id}-audio{audio_extension}"),
                    archive_path=audio_archive_path,
                    mime_type=audio.mime_type,
                )
            image_media = None
            if image:
                image_extension = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                }.get(image.mime_type, ".image")
                image_archive_path = f"media/images/{story.id}{image_extension}"
                image_media = HeritageMedia(
                    source_path=materialize(image, f"{story.id}-image{image_extension}"),
                    archive_path=image_archive_path,
                    mime_type=image.mime_type,
                )
            detail = story_detail_read(db, story.detail, secret_store) if story.detail else None
            contributions = [
                story_contribution_read(db, item, secret_store)
                for item in sorted(story.contributions, key=lambda value: value.created_at)
            ]
            heritage_stories.append(
                HeritageStory(
                    story_id=story.id,
                    title=story_view.title,
                    body=story_view.body,
                    life_stage=session.life_stage,
                    confirmed_at=story_view.confirmed_at.isoformat(),
                    place_name=detail.place_name if detail else None,
                    event_year=detail.event_year if detail else None,
                    theme_tags=detail.theme_tags if detail else [],
                    contributions=[
                        {
                            "contributor_label": item.contributor_label,
                            "contribution_type": item.contribution_type,
                            "type_label": type_labels.get(item.contribution_type, "家人补充"),
                            "body": item.body,
                            "status": item.status,
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in contributions
                    ],
                    audio=audio_media,
                    image=image_media,
                )
            )
        output = temp_root / f"lingnian-heritage-{profile.id}.zip"
        build_heritage_package(
            output_path=output,
            family_name=family_view.display_name,
            storyteller_name=profile_view.preferred_name,
            profile={
                "id": profile_view.id,
                "display_name": profile_view.display_name,
                "preferred_name": profile_view.preferred_name,
                "birth_year": profile_view.birth_year,
                "birth_era": profile_view.birth_era,
                "native_place": profile_view.native_place,
                "occupation_summary": profile_view.occupation_summary,
            },
            people=[person_read(db, person, secret_store).model_dump(mode="json") for person in people],
            relationships=[
                relationship_read(db, item, secret_store).model_dump(mode="json")
                for item in relationships
            ],
            stories=heritage_stories,
        )
        event = ConsentEvent(
            action="export_heritage_package",
            actor_label=payload.actor_label.strip(),
            object_type="elder_profile",
            object_id=profile.id,
        )
        db.add(event)
        db.flush()
        protect_values(
            db,
            family,
            event,
            {"actor_label": payload.actor_label.strip()},
            secret_store,
        )
        db.commit()
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    filename = f"lingnian-heritage-{profile.id}.zip"
    return FileResponse(
        output,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(shutil.rmtree, temp_root, ignore_errors=True),
    )


@router.post("/elder-profiles/{profile_id}/production-package")
def download_generation_production_package(
    profile_id: str,
    payload: ProductionPackageCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> FileResponse:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    story = require(db, Story, payload.story_id, "STORY_NOT_FOUND", "没有找到这篇故事。")
    if story.elder_id != profile.id:
        raise DomainError("CROSS_ELDER_STORY", "不能使用其他讲述者的故事。", 409)
    if not payload.rights_confirmed or not payload.no_impersonation:
        raise DomainError("MEDIA_RIGHTS_REQUIRED", "请先确认素材使用权和不用于冒充本人。", 409)
    if payload.generation_type != "photo_restore" and not payload.subject_consent:
        raise DomainError("SUBJECT_CONSENT_REQUIRED", "人物或家庭故事演绎必须有讲述者本人明确授权。", 409)

    family = profile.person.family
    key = family_master_key(family, secret_store)
    story_view = story_read(db, story, secret_store)
    profile_view = elder_read(db, profile, secret_store)
    session = story.source_draft.session
    audio = db.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.session_id == session.id,
            MediaAsset.kind == "audio_original",
            MediaAsset.is_original.is_(True),
        )
        .order_by(MediaAsset.created_at.desc())
    )
    image = db.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.session_id == session.id,
            MediaAsset.kind.in_(["photo_original", "old_object_original"]),
            MediaAsset.is_original.is_(True),
        )
        .order_by(MediaAsset.created_at.desc())
    )
    if payload.generation_type != "photo_restore" and not audio:
        raise DomainError("ORIGINAL_AUDIO_REQUIRED", "这篇故事没有可用的原始录音。", 409)
    if payload.generation_type in {"photo_restore", "portrait_video"} and not image:
        message = (
            "老照片修复需要先为这篇故事关联一张原始照片。"
            if payload.generation_type == "photo_restore"
            else "人物讲述视频需要先为这篇故事关联一张本人授权照片。"
        )
        raise DomainError("PORTRAIT_IMAGE_REQUIRED", message, 409)

    temp_root = Path(tempfile.mkdtemp(prefix="lingnian-production-"))
    try:
        sources_root = temp_root / "sources"
        sources_root.mkdir(mode=0o700)

        def materialize(asset: MediaAsset, name: str) -> Path:
            source_path = resolve_controlled_path(
                get_settings().resolved_asset_root, asset.relative_path
            )
            if not verify_asset_integrity(
                source_path,
                expected_size=asset.size_bytes,
                expected_sha256=asset.sha256,
            ):
                raise DomainError("ASSET_INTEGRITY_FAILED", "制作包素材校验失败，请先从备份恢复。", 409)
            target = sources_root / name
            if asset.encryption_version == 0:
                shutil.copyfile(source_path, target)
            elif asset.encryption_version == 1 and key is not None:
                decrypt_media_file(
                    source_path,
                    target,
                    key,
                    associated_data=media_context(family.id, asset.id),
                )
            else:
                raise DomainError("ASSET_ENCRYPTION_UNSUPPORTED", "无法读取这份加密素材。", 409)
            target.chmod(0o600)
            return target

        audio_media = None
        if audio:
            audio_extension = {
                "audio/wav": ".wav",
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "audio/webm": ".webm",
                "audio/ogg": ".ogg",
            }.get(audio.mime_type, ".audio")
            audio_path = materialize(audio, f"original-audio{audio_extension}")
            audio_media = ProductionMedia(
                source_path=audio_path,
                archive_path=f"sources/original-audio{audio_extension}",
                mime_type=audio.mime_type,
                sha256=file_sha256(audio_path),
            )
        image_media = None
        if image:
            image_extension = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }.get(image.mime_type, ".image")
            image_path = materialize(image, f"authorized-image{image_extension}")
            image_media = ProductionMedia(
                source_path=image_path,
                archive_path=f"sources/authorized-image{image_extension}",
                mime_type=image.mime_type,
                sha256=file_sha256(image_path),
            )
        detail = story_detail_read(db, story.detail, secret_store) if story.detail else None
        output = temp_root / f"lingnian-production-{story.id}.zip"
        build_production_package(
            output_path=output,
            generation_type=payload.generation_type,
            storyteller_name=profile_view.preferred_name,
            story_id=story.id,
            title=story_view.title,
            body=story_view.body,
            life_stage=session.life_stage,
            place_name=detail.place_name if detail else None,
            event_year=detail.event_year if detail else None,
            theme_tags=detail.theme_tags if detail else [],
            actor_label=payload.actor_label.strip(),
            subject_consent=payload.subject_consent,
            rights_confirmed=payload.rights_confirmed,
            no_impersonation=payload.no_impersonation,
            audio=audio_media,
            image=image_media,
        )
        event = ConsentEvent(
            action=f"export_{payload.generation_type}_production_package",
            actor_label=payload.actor_label.strip(),
            object_type="story",
            object_id=story.id,
        )
        db.add(event)
        db.flush()
        protect_values(
            db,
            family,
            event,
            {"actor_label": payload.actor_label.strip()},
            secret_store,
        )
        db.commit()
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    return FileResponse(
        output,
        media_type="application/zip",
        filename=f"lingnian-production-{story.id}.zip",
        background=BackgroundTask(shutil.rmtree, temp_root, ignore_errors=True),
    )


@router.get(
    "/generative-media/capabilities",
    response_model=list[GenerativeMediaCapability],
)
def get_generative_media_capabilities() -> list[GenerativeMediaCapability]:
    return [GenerativeMediaCapability(**item.__dict__) for item in capability_catalog()]


@router.get(
    "/elder-profiles/{profile_id}/generative-media-requests",
    response_model=list[GenerativeMediaRequestRead],
)
def list_generative_media_requests(
    profile_id: str,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> list[GenerativeMediaRequestRead]:
    require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    requests = db.scalars(
        select(GenerativeMediaRequest)
        .where(GenerativeMediaRequest.elder_id == profile_id)
        .order_by(GenerativeMediaRequest.created_at.desc())
    ).all()
    return [generative_request_read(db, item, secret_store) for item in requests]


@router.post(
    "/elder-profiles/{profile_id}/generative-media-requests",
    response_model=GenerativeMediaRequestRead,
    status_code=201,
)
def create_generative_media_request(
    profile_id: str,
    payload: GenerativeMediaRequestCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> GenerativeMediaRequestRead:
    profile = require(db, ElderProfile, profile_id, "ELDER_NOT_FOUND", "没有找到这位讲述者。")
    family = profile.person.family
    if payload.story_id:
        story = require(db, Story, payload.story_id, "STORY_NOT_FOUND", "没有找到这篇故事。")
        if story.elder_id != profile.id:
            raise DomainError("CROSS_ELDER_STORY", "不能使用其他讲述者的故事。", 409)
    if not payload.rights_confirmed or not payload.no_impersonation:
        raise DomainError("MEDIA_RIGHTS_REQUIRED", "请先确认素材使用权和不用于冒充本人。", 409)
    capability = next(
        (item for item in capability_catalog() if item.generation_type == payload.generation_type),
        None,
    )
    if capability is None:
        raise DomainError("UNSUPPORTED_GENERATION_TYPE", "暂不支持这种生成类型。", 422)
    if capability.requires_subject_consent and not payload.subject_consent:
        raise DomainError("SUBJECT_CONSENT_REQUIRED", "人物影像或声音生成必须有讲述者本人明确授权。", 409)
    provider_key, estimated_cost, status = estimate_request(payload.generation_type)
    canonical = json.dumps(
        {
            "elder_id": profile.id,
            "story_id": payload.story_id,
            "generation_type": payload.generation_type,
            "allow_external_upload": payload.allow_external_upload,
            "max_cost_cents": payload.max_cost_cents,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    request = GenerativeMediaRequest(
        elder_id=profile.id,
        story_id=payload.story_id,
        generation_type=payload.generation_type,
        provider_key=provider_key,
        status=status,
        actor_label=payload.actor_label.strip(),
        subject_consent=payload.subject_consent,
        rights_confirmed=payload.rights_confirmed,
        no_impersonation=payload.no_impersonation,
        allow_external_upload=payload.allow_external_upload,
        estimated_cost_cents=estimated_cost,
        max_cost_cents=payload.max_cost_cents,
        request_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        error_code="PROVIDER_NOT_CONFIGURED",
    )
    db.add(request)
    db.flush()
    protect_values(
        db,
        family,
        request,
        {"actor_label": payload.actor_label.strip()},
        secret_store,
    )
    db.commit()
    db.refresh(request)
    return generative_request_read(db, request, secret_store)


@router.post(
    "/generative-media-requests/{request_id}/result",
    response_model=GenerativeMediaRequestRead,
)
async def import_generative_media_result(
    request_id: str,
    video: UploadFile = File(...),
    provider_key: str = Form(default="manual_pipeline"),
    actual_cost_cents: int = Form(default=0),
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> GenerativeMediaRequestRead:
    request = require(
        db,
        GenerativeMediaRequest,
        request_id,
        "GENERATION_REQUEST_NOT_FOUND",
        "没有找到这项影像制作任务。",
    )
    if request.generation_type not in {"portrait_video", "scene_video"}:
        raise DomainError("VIDEO_RESULT_NOT_APPLICABLE", "这项任务不接收视频成片。", 409)
    if request.status != "awaiting_provider" or request.result_asset_id:
        raise DomainError("GENERATION_RESULT_ALREADY_IMPORTED", "这项任务已经导入过成片，请新建任务后重试。", 409)
    if request.story is None:
        raise DomainError("GENERATION_STORY_REQUIRED", "视频成片必须对应一篇已确认故事。", 409)
    normalized_provider = provider_key.strip()
    if not normalized_provider or len(normalized_provider) > 80 or not all(
        character.isalnum() or character in {"-", "_", "."}
        for character in normalized_provider
    ):
        raise DomainError("GENERATION_PROVIDER_INVALID", "生成方式标识无效。", 422)
    if actual_cost_cents < 0:
        raise DomainError("GENERATION_COST_INVALID", "实际费用不能小于零。", 422)
    if actual_cost_cents > request.max_cost_cents:
        raise DomainError("GENERATION_COST_LIMIT_EXCEEDED", "实际费用超过了这项任务的单次预算。", 409)

    session_id = request.story.source_draft.session_id
    stored = await store_video_upload(video, session_id, get_settings())
    asset = MediaAsset(
        session_id=session_id,
        kind=f"generated_{request.generation_type}",
        status="pending_human_review",
        is_original=False,
        **stored,
    )
    db.add(asset)
    db.flush()
    family = request.elder.person.family
    protect_values(
        db,
        family,
        asset,
        {"original_filename": asset.original_filename},
        secret_store,
    )
    encrypt_asset_if_needed(db, asset, family, secret_store)
    request.result_asset_id = asset.id
    request.provider_key = normalized_provider
    request.actual_cost_cents = actual_cost_cents
    request.status = "pending_human_review"
    request.error_code = None
    request.review_checks = {}
    db.commit()
    db.refresh(request)
    return generative_request_read(db, request, secret_store)


@router.patch(
    "/generative-media-requests/{request_id}/review",
    response_model=GenerativeMediaRequestRead,
)
def review_generative_media_result(
    request_id: str,
    payload: GenerativeMediaReviewCreate,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> GenerativeMediaRequestRead:
    request = require(
        db,
        GenerativeMediaRequest,
        request_id,
        "GENERATION_REQUEST_NOT_FOUND",
        "没有找到这项影像制作任务。",
    )
    if request.status != "pending_human_review" or not request.result_asset_id:
        raise DomainError("GENERATION_RESULT_NOT_REVIEWABLE", "当前没有等待验收的成片。", 409)
    checks = {
        "audio_present": payload.audio_present,
        "lip_sync_verified": payload.lip_sync_verified,
        "pauses_natural": payload.pauses_natural,
        "expression_natural": payload.expression_natural,
        "narrative_consistent": payload.narrative_consistent,
        "duration_appropriate": payload.duration_appropriate,
    }
    required_checks = (
        tuple(checks)
        if request.generation_type == "portrait_video"
        else (
            "audio_present",
            "expression_natural",
            "narrative_consistent",
            "duration_appropriate",
        )
    )
    if payload.decision == "accepted" and not all(
        checks[name] for name in required_checks
    ):
        raise DomainError(
            "GENERATION_REVIEW_INCOMPLETE",
            (
                "人物视频六项验收必须全部通过，才能标记为可用成片。"
                if request.generation_type == "portrait_video"
                else "故事情景视频四项验收必须全部通过，才能标记为可用成片。"
            ),
            409,
        )
    notes = payload.review_notes.strip() if payload.review_notes else None
    if payload.decision == "rejected" and not notes:
        raise DomainError("GENERATION_REJECTION_REASON_REQUIRED", "驳回成片时请写明具体问题。", 409)

    family = request.elder.person.family
    request.status = payload.decision
    request.error_code = (
        None if payload.decision == "accepted" else "HUMAN_REVIEW_REJECTED"
    )
    request.review_checks = checks
    request.reviewed_by = payload.reviewed_by.strip()
    request.review_notes = notes
    request.reviewed_at = now_utc()
    result_asset = require(
        db,
        MediaAsset,
        request.result_asset_id,
        "GENERATION_RESULT_MISSING",
        "成片文件已经不存在。",
    )
    result_asset.status = "ready" if payload.decision == "accepted" else "rejected"
    protect_values(
        db,
        family,
        request,
        {
            "reviewed_by": payload.reviewed_by.strip(),
            "review_notes": notes,
        },
        secret_store,
    )
    db.commit()
    db.refresh(request)
    return generative_request_read(db, request, secret_store)


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
