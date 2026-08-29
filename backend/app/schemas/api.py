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


class SecurityInitializeRequest(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)


class FamilySecurityRead(BaseModel):
    family_id: str
    key_version: int | None
    encryption_status: str
    key_initialized: bool
    recovery_package_created_at: datetime | None


class RecoveryPackageCreate(BaseModel):
    actor_label: str = Field(min_length=1, max_length=80)
    recovery_passphrase: SecretStr = Field(min_length=12, max_length=200)


class ElderProfileCreate(BaseModel):
    family_id: str
    display_name: str = Field(min_length=1, max_length=80)
    preferred_name: str = Field(min_length=1, max_length=80)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_era: str | None = Field(default=None, max_length=40)
    native_place: str | None = Field(default=None, max_length=120)
    occupation_summary: str | None = Field(default=None, max_length=240)


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
    created_at: datetime


class MemorySessionCreate(BaseModel):
    elder_id: str
    life_stage: str = Field(min_length=1, max_length=40)


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


class TimelineEventRead(ORMModel):
    id: str
    story_id: str
    time_expression: str | None
    normalized_time: str | None
    confidence: str


class TimelineItem(BaseModel):
    story: StoryRead
    events: list[TimelineEventRead]
    audio_url: str | None = None


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
