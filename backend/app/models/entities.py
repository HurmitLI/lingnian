from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class FamilyArchive(TimestampMixin, Base):
    __tablename__ = "family_archives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    data_classification: Mapped[str] = mapped_column(String(32), default="test")

    people: Mapped[list[Person]] = relationship(back_populates="family", cascade="all, delete-orphan")
    security_metadata: Mapped[ArchiveSecurity | None] = relationship(
        back_populates="family", uselist=False, cascade="all, delete-orphan"
    )
    relationships: Mapped[list[PersonRelationship]] = relationship(
        back_populates="family", cascade="all, delete-orphan"
    )


class Person(TimestampMixin, Base):
    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("family_archives.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(24))
    display_name: Mapped[str] = mapped_column(String(80))

    family: Mapped[FamilyArchive] = relationship(back_populates="people")
    elder_profile: Mapped[ElderProfile | None] = relationship(
        back_populates="person", uselist=False, cascade="all, delete-orphan"
    )


class ElderProfile(TimestampMixin, Base):
    __tablename__ = "elder_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), unique=True
    )
    birth_year: Mapped[int | None] = mapped_column(Integer)
    birth_era: Mapped[str | None] = mapped_column(String(40))
    native_place: Mapped[str | None] = mapped_column(String(120))
    occupation_summary: Mapped[str | None] = mapped_column(String(240))
    preferred_name: Mapped[str] = mapped_column(String(80))

    person: Mapped[Person] = relationship(back_populates="elder_profile")
    sessions: Mapped[list[MemorySession]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )
    stories: Mapped[list[Story]] = relationship(back_populates="elder")
    topic_preferences: Mapped[list[TopicPreference]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )
    memory_facts: Mapped[list[MemoryFact]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )
    memory_books: Mapped[list[MemoryBook]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )


class MemorySession(TimestampMixin, Base):
    __tablename__ = "memory_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elder_profiles.id", ondelete="CASCADE"))
    life_stage: Mapped[str] = mapped_column(String(40))
    prompt_id: Mapped[str] = mapped_column(String(80))
    question_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="PROMPT_READY")

    elder: Mapped[ElderProfile] = relationship(back_populates="sessions")
    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    story_draft: Mapped[StoryDraft | None] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    tasks: Mapped[list[WorkflowTask]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class MediaAsset(TimestampMixin, Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("memory_sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    relative_path: Mapped[str] = mapped_column(String(500), unique=True)
    original_filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="ready")
    is_original: Mapped[bool] = mapped_column(Boolean, default=True)
    encryption_version: Mapped[int] = mapped_column(Integer, default=0)
    integrity_checked_at: Mapped[datetime | None] = mapped_column()

    session: Mapped[MemorySession] = relationship(back_populates="media_assets")
    links: Mapped[list[MediaLink]] = relationship(
        back_populates="media_asset", cascade="all, delete-orphan"
    )


class Transcript(TimestampMixin, Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("memory_sessions.id", ondelete="CASCADE"), unique=True
    )
    raw_text: Mapped[str] = mapped_column(Text)
    corrected_text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    asr_provider: Mapped[str] = mapped_column(String(80))
    asr_model: Mapped[str] = mapped_column(String(160))
    asr_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    session: Mapped[MemorySession] = relationship(back_populates="transcript")


class StoryDraft(TimestampMixin, Base):
    __tablename__ = "story_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("memory_sessions.id", ondelete="CASCADE"), unique=True
    )
    transcript_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    timeline_mentions: Mapped[list] = mapped_column(JSON, default=list)
    people_mentions: Mapped[list] = mapped_column(JSON, default=list)
    uncertainties: Mapped[list] = mapped_column(JSON, default=list)
    added_facts: Mapped[list] = mapped_column(JSON, default=list)
    source_coverage: Mapped[float] = mapped_column(default=1.0)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="pending_review")

    session: Mapped[MemorySession] = relationship(back_populates="story_draft")
    story: Mapped[Story | None] = relationship(back_populates="source_draft", uselist=False)


class Story(TimestampMixin, Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elder_profiles.id", ondelete="RESTRICT"))
    source_draft_id: Mapped[str] = mapped_column(
        ForeignKey("story_drafts.id", ondelete="RESTRICT"), unique=True
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(String(80))
    confirmed_at: Mapped[datetime] = mapped_column(default=now_utc)

    elder: Mapped[ElderProfile] = relationship(back_populates="stories")
    source_draft: Mapped[StoryDraft] = relationship(back_populates="story")
    timeline_events: Mapped[list[TimelineEvent]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )


class TimelineEvent(TimestampMixin, Base):
    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))
    time_expression: Mapped[str | None] = mapped_column(String(160))
    normalized_time: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(24), default="uncertain")

    story: Mapped[Story] = relationship(back_populates="timeline_events")


class WorkflowTask(TimestampMixin, Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (UniqueConstraint("session_id", "task_type", "attempt", name="uq_task_attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("memory_sessions.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(80))
    output_ref: Mapped[str | None] = mapped_column(String(36))
    model_consent_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_consent_events.id", ondelete="RESTRICT")
    )

    session: Mapped[MemorySession] = relationship(back_populates="tasks")


class ConsentEvent(Base):
    __tablename__ = "consent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(40))
    actor_label: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class ArchiveSecurity(TimestampMixin, Base):
    __tablename__ = "archive_security"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(
        ForeignKey("family_archives.id", ondelete="CASCADE"), unique=True
    )
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    encryption_status: Mapped[str] = mapped_column(String(40), default="key_ready")
    initialized_at: Mapped[datetime] = mapped_column(default=now_utc)
    recovery_package_created_at: Mapped[datetime | None] = mapped_column()

    family: Mapped[FamilyArchive] = relationship(back_populates="security_metadata")


class ModelConsentEvent(Base):
    __tablename__ = "model_consent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(
        ForeignKey("family_archives.id", ondelete="CASCADE")
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_sessions.id", ondelete="CASCADE")
    )
    actor_label: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(40))
    data_classification: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(24))
    one_time: Mapped[bool] = mapped_column(Boolean, default=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class PersonRelationship(TimestampMixin, Base):
    __tablename__ = "person_relationships"
    __table_args__ = (
        UniqueConstraint(
            "from_person_id",
            "to_person_id",
            "relationship_type",
            name="uq_person_relationship_direction",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(
        ForeignKey("family_archives.id", ondelete="CASCADE")
    )
    from_person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE")
    )
    to_person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE")
    )
    relationship_type: Mapped[str] = mapped_column(String(40))
    custom_label: Mapped[str | None] = mapped_column(String(80))
    confirmed_by: Mapped[str] = mapped_column(String(80))

    family: Mapped[FamilyArchive] = relationship(back_populates="relationships")


class QuestionPrompt(TimestampMixin, Base):
    __tablename__ = "question_prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prompt_key: Mapped[str] = mapped_column(String(100), unique=True)
    life_stage: Mapped[str] = mapped_column(String(40))
    question_text: Mapped[str] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(String(24), default="normal")
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(40), default="built_in")


class TopicPreference(TimestampMixin, Base):
    __tablename__ = "topic_preferences"
    __table_args__ = (
        UniqueConstraint("elder_id", "topic_key", name="uq_elder_topic_preference"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    elder_id: Mapped[str] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE")
    )
    topic_key: Mapped[str] = mapped_column(String(80))
    preference: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(80))

    elder: Mapped[ElderProfile] = relationship(back_populates="topic_preferences")


class MemoryFact(TimestampMixin, Base):
    __tablename__ = "memory_facts"
    __table_args__ = (
        UniqueConstraint("story_id", "fact_type", name="uq_story_memory_fact_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    elder_id: Mapped[str] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE")
    )
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))
    fact_type: Mapped[str] = mapped_column(String(40))
    subject_label: Mapped[str] = mapped_column(String(120))
    value_text: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[str] = mapped_column(String(24), default="confirmed")
    status: Mapped[str] = mapped_column(String(24), default="active")

    elder: Mapped[ElderProfile] = relationship(back_populates="memory_facts")


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    elder_id: Mapped[str] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE")
    )
    topic_key: Mapped[str] = mapped_column(String(80))
    remind_at: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    last_shown_at: Mapped[datetime | None] = mapped_column()
    show_count: Mapped[int] = mapped_column(Integer, default=0)

    elder: Mapped[ElderProfile] = relationship(back_populates="reminders")


class MemoryBook(TimestampMixin, Base):
    __tablename__ = "memory_books"
    __table_args__ = (
        UniqueConstraint("elder_id", "version", name="uq_elder_memory_book_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    elder_id: Mapped[str] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    markdown_content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    story_manifest: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="ready")
    pdf_status: Mapped[str] = mapped_column(String(24), default="not_generated")
    pdf_relative_path: Mapped[str | None] = mapped_column(String(500))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))

    elder: Mapped[ElderProfile] = relationship(back_populates="memory_books")


class MediaLink(TimestampMixin, Base):
    __tablename__ = "media_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), unique=True
    )
    elder_id: Mapped[str] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE")
    )
    trigger_kind: Mapped[str] = mapped_column(String(24))
    user_annotation: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    model_inference: Mapped[str | None] = mapped_column(Text)

    media_asset: Mapped[MediaAsset] = relationship(back_populates="links")
