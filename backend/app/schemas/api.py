from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FamilyCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    idempotency_key: str | None = Field(default=None, max_length=80)
    data_classification: str = Field(
        default="test",
        pattern="^(test|authorized_non_sensitive|authorized_sensitive)$",
    )


class FamilyRead(ORMModel):
    id: str
    display_name: str
    schema_version: int
    data_classification: str
    created_at: datetime


class PersonCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="family_member", min_length=1, max_length=24)


class PersonUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, min_length=1, max_length=24)


class PersonRead(ORMModel):
    id: str
    family_id: str
    role: str
    display_name: str
    created_at: datetime


class PersonRelationshipCreate(BaseModel):
    from_person_id: str
    to_person_id: str
    relationship_type: str = Field(
        pattern="^(parent|child|spouse|sibling|grandparent|grandchild|custom)$"
    )
    custom_label: str | None = Field(default=None, max_length=80)
    confirmed_by: str = Field(min_length=1, max_length=80)


class PersonRelationshipRead(ORMModel):
    id: str
    family_id: str
    from_person_id: str
    to_person_id: str
    relationship_type: str
    custom_label: str | None
    confirmed_by: str
    created_at: datetime


class SecurityInitializeRequest(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)


class FamilySecurityRead(BaseModel):
    family_id: str
    key_version: int | None
    encryption_status: str
    key_initialized: bool
    recovery_package_created_at: datetime | None
    recovery_verified_at: datetime | None
    activated_at: datetime | None


class RecoveryPackageCreate(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)
    recovery_passphrase: SecretStr = Field(min_length=12, max_length=200)


class SecurityActivationRequest(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)
    data_classification: str = Field(
        pattern="^(authorized_non_sensitive|authorized_sensitive)$"
    )


class BackupCreate(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)


class BackupRead(ORMModel):
    id: str
    backup_version: int
    archive_sha256: str
    database_sha256: str
    asset_count: int
    status: str
    verified_at: datetime | None
    verification_summary: dict
    created_at: datetime


class ElderProfileCreate(BaseModel):
    family_id: str
    display_name: str = Field(min_length=1, max_length=80)
    preferred_name: str = Field(min_length=1, max_length=80)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_era: str | None = Field(default=None, max_length=40)
    native_place: str | None = Field(default=None, max_length=120)
    occupation_summary: str | None = Field(default=None, max_length=240)
    health_notes: str | None = Field(default=None, max_length=2000)


class ElderProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    preferred_name: str | None = Field(default=None, min_length=1, max_length=80)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_era: str | None = Field(default=None, max_length=40)
    native_place: str | None = Field(default=None, max_length=120)
    occupation_summary: str | None = Field(default=None, max_length=240)
    health_notes: str | None = Field(default=None, max_length=2000)


class ElderProfileRead(ORMModel):
    id: str
    person_id: str
    family_id: str
    data_classification: str
    display_name: str
    preferred_name: str
    birth_year: int | None
    birth_era: str | None
    native_place: str | None
    occupation_summary: str | None
    health_notes: str | None
    created_at: datetime


class MemorySessionCreate(BaseModel):
    elder_id: str
    life_stage: str = Field(min_length=1, max_length=40)
    topic_confirmed: bool = False
    trigger_kind: str = Field(default="question", pattern="^(question|photo|old_object)$")


class MemorySessionRead(ORMModel):
    id: str
    elder_id: str
    life_stage: str
    prompt_id: str
    question_text: str
    status: str
    created_at: datetime
    updated_at: datetime


class MediaAssetRead(ORMModel):
    id: str
    kind: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: str
    is_original: bool
    content_url: str


class MediaLinkRead(ORMModel):
    id: str
    media_asset_id: str
    elder_id: str
    trigger_kind: str
    user_annotation: str | None
    width: int
    height: int
    model_inference: str | None
    created_at: datetime


class TaskRead(ORMModel):
    id: str
    session_id: str
    task_type: str
    status: str
    progress: int
    attempt: int
    error_code: str | None
    output_ref: str | None
    model_consent_event_id: str | None
    created_at: datetime
    updated_at: datetime


class TranscriptRead(ORMModel):
    id: str
    session_id: str
    raw_text: str
    corrected_text: str
    version: int
    asr_provider: str
    asr_model: str
    asr_metadata: dict
    created_at: datetime
    updated_at: datetime


class TranscriptUpdate(BaseModel):
    corrected_text: str = Field(min_length=1, max_length=100_000)


class TopicPreferenceUpsert(BaseModel):
    topic_key: str = Field(min_length=1, max_length=80)
    preference: str = Field(pattern="^(welcome|ask_first|avoid)$")
    note: str | None = Field(default=None, max_length=500)
    updated_by: str = Field(min_length=1, max_length=80)


class TopicPreferenceRead(ORMModel):
    id: str
    elder_id: str
    topic_key: str
    preference: str
    note: str | None
    updated_by: str
    updated_at: datetime


class QuestionPromptRead(ORMModel):
    prompt_key: str
    life_stage: str
    question_text: str
    sensitivity: str
    version: int


class MemoryFactRead(ORMModel):
    id: str
    elder_id: str
    story_id: str
    fact_type: str
    subject_label: str
    value_text: str
    content_sha256: str
    confidence: str
    status: str
    created_at: datetime


class StageCoverage(BaseModel):
    life_stage: str
    session_count: int
    confirmed_story_count: int


class ElderMemoryContext(BaseModel):
    coverage: list[StageCoverage]
    preferences: list[TopicPreferenceRead]
    confirmed_facts: list[MemoryFactRead]


class ReminderCreate(BaseModel):
    topic_key: str = Field(min_length=1, max_length=80)
    remind_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=100)

    @field_validator("remind_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("提醒时间必须包含时区。")
        return value


class ReminderRead(ORMModel):
    id: str
    elder_id: str
    topic_key: str
    remind_at: datetime
    status: str
    idempotency_key: str
    last_shown_at: datetime | None
    show_count: int
    created_at: datetime
    updated_at: datetime


class MemoryBookCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    created_by: str = Field(min_length=1, max_length=80)


class MemoryBookRead(ORMModel):
    id: str
    elder_id: str
    version: int
    title: str
    content_sha256: str
    story_manifest: list
    created_by: str
    status: str
    pdf_status: str
    pdf_sha256: str | None
    created_at: datetime


class HeritageExportCreate(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)


class ProductionPackageCreate(BaseModel):
    story_id: str
    generation_type: str = Field(pattern="^(photo_restore|portrait_video|scene_video)$")
    actor_label: str = Field(min_length=1, max_length=80)
    subject_consent: bool
    rights_confirmed: bool
    no_impersonation: bool


class KeepsakeCatalogItem(BaseModel):
    story_id: str
    title: str
    life_stage: str
    confirmed_at: datetime
    has_original_audio: bool
    audio_asset_id: str | None
    image_asset_id: str | None
    unavailable_reason: str | None = None


class KeepsakeAuthorizationCreate(BaseModel):
    story_ids: list[str] = Field(min_length=1, max_length=30)
    actor_label: str = Field(min_length=1, max_length=80)
    original_voice_authorized: bool
    private_family_use: bool
    no_impersonation: bool
    original_audio_only: bool


class KeepsakeAuthorizationRead(ORMModel):
    id: str
    elder_id: str
    actor_label: str
    story_ids: list
    manifest_sha256: str
    original_voice_authorized: bool
    private_family_use: bool
    no_impersonation: bool
    original_audio_only: bool
    decision: str
    used_at: datetime | None
    created_at: datetime


class KeepsakeCreate(BaseModel):
    authorization_id: str
    title: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=100)


class KeepsakeRead(ORMModel):
    id: str
    elder_id: str
    authorization_id: str
    version: int
    title: str
    story_manifest: list
    status: str
    progress: int
    attempt: int
    error_code: str | None
    mime_type: str
    duration_ms: int | None
    width: int
    height: int
    size_bytes: int | None
    renderer: str
    cost_cents: int
    source_mode: str
    content_url: str | None
    created_at: datetime
    updated_at: datetime


class ModelConsentCreate(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)
    purpose: str = Field(default="story_organization", pattern="^story_organization$")


class ModelConsentRead(ORMModel):
    id: str
    family_id: str
    session_id: str | None
    actor_label: str
    purpose: str
    data_classification: str
    decision: str
    one_time: bool
    used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class OrganizationTaskCreate(BaseModel):
    consent_event_id: str | None = None


class TaskRetryRequest(BaseModel):
    consent_event_id: str | None = None


class StoryDraftRead(ORMModel):
    id: str
    session_id: str
    transcript_version: int
    title: str
    body: str
    timeline_mentions: list
    people_mentions: list
    uncertainties: list
    added_facts: list
    source_coverage: float
    provider: str
    model: str
    status: str
    created_at: datetime
    updated_at: datetime


class ConfirmDraftRequest(BaseModel):
    confirmed_by: str = Field(min_length=1, max_length=80)


class StoryRead(ORMModel):
    id: str
    elder_id: str
    source_draft_id: str
    title: str
    body: str
    confirmed_by: str
    confirmed_at: datetime


class StoryDetailUpsert(BaseModel):
    place_name: str | None = Field(default=None, max_length=160)
    event_year: int | None = Field(default=None, ge=1800, le=2100)
    theme_tags: list[str] = Field(default_factory=list, max_length=12)
    summary: str | None = Field(default=None, max_length=1000)
    updated_by: str = Field(min_length=1, max_length=80)

    @field_validator("theme_tags")
    @classmethod
    def validate_theme_tags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                continue
            if len(item) > 24:
                raise ValueError("每个主题标签不能超过 24 个字。")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned


class StoryDetailRead(ORMModel):
    id: str
    story_id: str
    place_name: str | None
    event_year: int | None
    theme_tags: list[str]
    summary: str | None
    updated_by: str
    updated_at: datetime


class StoryContributionCreate(BaseModel):
    contributor_person_id: str | None = None
    contributor_label: str = Field(min_length=1, max_length=80)
    contribution_type: str = Field(
        pattern="^(context|correction|question|alternate_memory)$"
    )
    body: str = Field(min_length=1, max_length=5000)


class StoryContributionRead(ORMModel):
    id: str
    story_id: str
    contributor_person_id: str | None
    contributor_label: str
    contribution_type: str
    body: str
    status: str
    created_at: datetime


class StoryContributionStatusUpdate(BaseModel):
    status: str = Field(pattern="^(open|confirmed|disputed|resolved)$")
    actor_label: str = Field(min_length=1, max_length=80)


class ArchiveAskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    max_citations: int = Field(default=3, ge=1, le=5)


class ArchiveCitation(BaseModel):
    source_id: str
    story_id: str
    source_kind: str
    source_label: str | None = None
    title: str
    life_stage: str
    excerpt: str
    audio_url: str | None = None
    image_url: str | None = None
    score: float


class ArchiveAnswer(BaseModel):
    question: str
    status: str
    answer: str
    citations: list[ArchiveCitation]
    follow_up_question: str | None = None
    answer_mode: str = "local_extract_with_sources"


class ArchiveGapCreate(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    actor_label: str = Field(min_length=1, max_length=80)
    life_stage: str = Field(default="家人提问", min_length=1, max_length=40)


class LegacyPlanUpsert(BaseModel):
    successor_person_ids: list[str] = Field(min_length=1, max_length=20)
    access_policy: str = Field(
        default="manual_handoff",
        pattern="^(manual_handoff|joint_family_review|designated_steward)$",
    )
    steward_label: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=1000)


class LegacyPlanRead(ORMModel):
    id: str
    family_id: str
    successor_person_ids: list[str]
    access_policy: str
    steward_label: str
    note: str | None
    confirmed_at: datetime
    updated_at: datetime


class MediaPersonTagCreate(BaseModel):
    person_id: str
    tagged_by: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class MediaPersonTagRead(ORMModel):
    id: str
    family_id: str
    media_asset_id: str
    person_id: str
    person_name: str
    tagged_by: str
    note: str | None
    created_at: datetime


class GenerativeMediaCapability(BaseModel):
    generation_type: str
    label: str
    available: bool
    provider_key: str | None
    requires_external_upload: bool
    requires_subject_consent: bool
    estimated_cost_cents: int | None
    unavailable_reason: str | None


class GenerativeMediaRequestCreate(BaseModel):
    story_id: str | None = None
    generation_type: str = Field(
        pattern="^(photo_restore|portrait_video|scene_video|voice_replica)$"
    )
    actor_label: str = Field(min_length=1, max_length=80)
    subject_consent: bool
    rights_confirmed: bool
    no_impersonation: bool
    allow_external_upload: bool
    max_cost_cents: int = Field(default=0, ge=0, le=100_000)


class GenerativeMediaReviewCreate(BaseModel):
    decision: str = Field(pattern="^(accepted|rejected)$")
    reviewed_by: str = Field(min_length=1, max_length=80)
    review_notes: str | None = Field(default=None, max_length=2000)
    audio_present: bool = False
    lip_sync_verified: bool = False
    pauses_natural: bool = False
    expression_natural: bool = False
    narrative_consistent: bool = False
    duration_appropriate: bool = False


class GenerativeMediaRequestRead(ORMModel):
    id: str
    elder_id: str
    story_id: str | None
    result_asset_id: str | None
    result_content_url: str | None
    generation_type: str
    provider_key: str
    status: str
    actor_label: str
    subject_consent: bool
    rights_confirmed: bool
    no_impersonation: bool
    allow_external_upload: bool
    estimated_cost_cents: int
    actual_cost_cents: int
    max_cost_cents: int
    error_code: str | None
    review_checks: dict
    reviewed_by: str | None
    review_notes: str | None
    reviewed_at: datetime | None
    created_at: datetime


class TimelineEventRead(ORMModel):
    id: str
    story_id: str
    time_expression: str | None
    normalized_time: str | None
    confidence: str


class TimelineItem(BaseModel):
    story: StoryRead
    life_stage: str
    events: list[TimelineEventRead]
    audio_url: str | None = None
    image_url: str | None = None
    image_asset_id: str | None = None
    image_annotation: str | None = None
    detail: StoryDetailRead | None = None
    contributions: list[StoryContributionRead] = Field(default_factory=list)
    person_tags: list[MediaPersonTagRead] = Field(default_factory=list)


class SessionDetail(BaseModel):
    session: MemorySessionRead
    media_assets: list[MediaAssetRead]
    transcript: TranscriptRead | None
    story_draft: StoryDraftRead | None
    tasks: list[TaskRead]


class QuestionOutput(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=300)
    safety_check: str = Field(min_length=1, max_length=120)


class TimelineMention(BaseModel):
    expression: str = Field(min_length=1, max_length=160)
    normalized: str | None = Field(default=None, max_length=40)
    confidence: str = Field(default="uncertain", pattern="^(confirmed|uncertain)$")


class StoryOrganizationOutput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=100_000)
    timeline_mentions: list[TimelineMention] = Field(default_factory=list)
    people_mentions: list[str] = Field(default_factory=list, max_length=100)
    uncertainties: list[str] = Field(default_factory=list, max_length=100)
    source_coverage: float = Field(ge=0, le=1)

    @field_validator("people_mentions", "uncertainties")
    @classmethod
    def cap_item_length(cls, values: list[str]) -> list[str]:
        if any(len(value) > 300 for value in values):
            raise ValueError("列表内容过长")
        return values


class HealthRead(BaseModel):
    status: str = "ok"
    environment: str
    asr_provider: str
    llm_provider: str
