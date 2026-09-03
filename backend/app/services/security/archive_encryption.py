from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    ArchiveSecurity,
    ConsentEvent,
    ElderProfile,
    FamilyArchive,
    GenerativeMediaRequest,
    InterviewTurn,
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
    Story,
    StoryContribution,
    StoryDetail,
    StoryDraft,
    TimelineEvent,
    TopicPreference,
    Transcript,
)
from app.services.archive.assets import resolve_controlled_path, verify_asset_integrity
from app.services.security.media_crypto import encrypt_media_file, media_context
from app.services.security.secure_fields import TEXT_PLACEHOLDER, protect_field


@dataclass(frozen=True)
class ArchiveEncryptionResult:
    encrypted_fields: int
    encrypted_media: int
    encrypted_books: int


def _protect_object(
    db: Session,
    *,
    family: FamilyArchive,
    obj,
    fields: dict[str, object],
    master_key: bytes,
) -> int:
    count = 0
    for field_name, placeholder in fields.items():
        value = getattr(obj, field_name)
        if value is None:
            continue
        protect_field(
            db,
            family=family,
            object_type=obj.__tablename__,
            object_id=obj.id,
            field_name=field_name,
            value=value,
            master_key=master_key,
        )
        setattr(obj, field_name, placeholder)
        count += 1
    return count


def activate_archive_encryption(
    db: Session,
    *,
    family: FamilyArchive,
    metadata: ArchiveSecurity,
    master_key: bytes,
    target_classification: str,
    settings: Settings,
) -> ArchiveEncryptionResult:
    if target_classification not in {"authorized_non_sensitive", "authorized_sensitive"}:
        raise ValueError("DATA_CLASSIFICATION_INVALID")
    if metadata.recovery_package_created_at is None:
        raise ValueError("RECOVERY_PACKAGE_REQUIRED")
    if metadata.recovery_verified_at is None:
        raise ValueError("RECOVERY_VERIFICATION_REQUIRED")
    if metadata.encryption_status == "active_encrypted":
        return ArchiveEncryptionResult(0, 0, 0)

    metadata.encryption_status = "migrating"
    db.commit()
    prepared: list[tuple[Path, Path, MediaAsset | MemoryBook, object]] = []
    replaced: list[tuple[Path, Path]] = []
    field_count = 0
    media_count = 0
    book_count = 0
    try:
        field_count += _protect_object(
            db,
            family=family,
            obj=family,
            fields={"display_name": TEXT_PLACEHOLDER},
            master_key=master_key,
        )
        people = list(
            db.scalars(select(Person).where(Person.family_id == family.id)).all()
        )
        person_ids = [item.id for item in people]
        for person in people:
            field_count += _protect_object(
                db,
                family=family,
                obj=person,
                fields={"display_name": TEXT_PLACEHOLDER},
                master_key=master_key,
            )
        profiles = (
            list(
                db.scalars(
                    select(ElderProfile).where(ElderProfile.person_id.in_(person_ids))
                ).all()
            )
            if person_ids
            else []
        )
        profile_ids = [item.id for item in profiles]
        for profile in profiles:
            field_count += _protect_object(
                db,
                family=family,
                obj=profile,
                fields={
                    "preferred_name": TEXT_PLACEHOLDER,
                    "birth_year": None,
                    "birth_era": TEXT_PLACEHOLDER,
                    "native_place": TEXT_PLACEHOLDER,
                    "occupation_summary": TEXT_PLACEHOLDER,
                    "health_notes": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        relationships = list(
            db.scalars(
                select(PersonRelationship).where(PersonRelationship.family_id == family.id)
            ).all()
        )
        for relationship in relationships:
            field_count += _protect_object(
                db,
                family=family,
                obj=relationship,
                fields={
                    "custom_label": TEXT_PLACEHOLDER,
                    "confirmed_by": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )

        sessions = (
            list(
                db.scalars(
                    select(MemorySession).where(MemorySession.elder_id.in_(profile_ids))
                ).all()
            )
            if profile_ids
            else []
        )
        session_ids = [item.id for item in sessions]
        for session in sessions:
            field_count += _protect_object(
                db,
                family=family,
                obj=session,
                fields={"question_text": TEXT_PLACEHOLDER},
                master_key=master_key,
            )
        interview_turns = (
            list(
                db.scalars(
                    select(InterviewTurn).where(InterviewTurn.session_id.in_(session_ids))
                ).all()
            )
            if session_ids
            else []
        )
        for turn in interview_turns:
            field_count += _protect_object(
                db,
                family=family,
                obj=turn,
                fields={
                    "question_text": TEXT_PLACEHOLDER,
                    "raw_answer_text": TEXT_PLACEHOLDER,
                    "corrected_answer_text": TEXT_PLACEHOLDER,
                    "asr_metadata": {},
                },
                master_key=master_key,
            )
        transcripts = (
            list(
                db.scalars(
                    select(Transcript).where(Transcript.session_id.in_(session_ids))
                ).all()
            )
            if session_ids
            else []
        )
        for transcript in transcripts:
            field_count += _protect_object(
                db,
                family=family,
                obj=transcript,
                fields={
                    "raw_text": TEXT_PLACEHOLDER,
                    "corrected_text": TEXT_PLACEHOLDER,
                    "asr_metadata": {},
                },
                master_key=master_key,
            )
        drafts = (
            list(
                db.scalars(
                    select(StoryDraft).where(StoryDraft.session_id.in_(session_ids))
                ).all()
            )
            if session_ids
            else []
        )
        for draft in drafts:
            field_count += _protect_object(
                db,
                family=family,
                obj=draft,
                fields={
                    "title": TEXT_PLACEHOLDER,
                    "body": TEXT_PLACEHOLDER,
                    "timeline_mentions": [],
                    "people_mentions": [],
                    "uncertainties": [],
                    "added_facts": [],
                },
                master_key=master_key,
            )
        stories = (
            list(
                db.scalars(select(Story).where(Story.elder_id.in_(profile_ids))).all()
            )
            if profile_ids
            else []
        )
        story_ids = [item.id for item in stories]
        for story in stories:
            field_count += _protect_object(
                db,
                family=family,
                obj=story,
                fields={
                    "title": TEXT_PLACEHOLDER,
                    "body": TEXT_PLACEHOLDER,
                    "confirmed_by": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        details = (
            list(db.scalars(select(StoryDetail).where(StoryDetail.story_id.in_(story_ids))).all())
            if story_ids
            else []
        )
        for detail in details:
            field_count += _protect_object(
                db,
                family=family,
                obj=detail,
                fields={
                    "place_name": TEXT_PLACEHOLDER,
                    "event_year": None,
                    "theme_tags": [],
                    "summary": TEXT_PLACEHOLDER,
                    "updated_by": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        contributions = (
            list(
                db.scalars(
                    select(StoryContribution).where(StoryContribution.story_id.in_(story_ids))
                ).all()
            )
            if story_ids
            else []
        )
        for contribution in contributions:
            field_count += _protect_object(
                db,
                family=family,
                obj=contribution,
                fields={
                    "contributor_label": TEXT_PLACEHOLDER,
                    "body": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        events = (
            list(
                db.scalars(
                    select(TimelineEvent).where(TimelineEvent.story_id.in_(story_ids))
                ).all()
            )
            if story_ids
            else []
        )
        for event in events:
            field_count += _protect_object(
                db,
                family=family,
                obj=event,
                fields={
                    "time_expression": TEXT_PLACEHOLDER,
                    "normalized_time": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        for model, fields in [
            (TopicPreference, {"note": TEXT_PLACEHOLDER, "updated_by": TEXT_PLACEHOLDER}),
            (MemoryFact, {"subject_label": TEXT_PLACEHOLDER, "value_text": TEXT_PLACEHOLDER}),
            (
                MemoryBook,
                {
                    "title": TEXT_PLACEHOLDER,
                    "markdown_content": TEXT_PLACEHOLDER,
                    "story_manifest": [],
                    "created_by": TEXT_PLACEHOLDER,
                },
            ),
            (MediaLink, {"user_annotation": TEXT_PLACEHOLDER, "model_inference": TEXT_PLACEHOLDER}),
        ]:
            objects = (
                list(db.scalars(select(model).where(model.elder_id.in_(profile_ids))).all())
                if profile_ids
                else []
            )
            for obj in objects:
                field_count += _protect_object(
                    db,
                    family=family,
                    obj=obj,
                    fields=fields,
                    master_key=master_key,
                )
        for consent in db.scalars(
            select(ModelConsentEvent).where(ModelConsentEvent.family_id == family.id)
        ).all():
            field_count += _protect_object(
                db,
                family=family,
                obj=consent,
                fields={"actor_label": TEXT_PLACEHOLDER},
                master_key=master_key,
            )
        legacy_plan = db.scalar(select(LegacyPlan).where(LegacyPlan.family_id == family.id))
        if legacy_plan:
            field_count += _protect_object(
                db,
                family=family,
                obj=legacy_plan,
                fields={
                    "successor_person_ids": [],
                    "steward_label": TEXT_PLACEHOLDER,
                    "note": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        media_person_tags = list(
            db.scalars(
                select(MediaPersonTag).where(MediaPersonTag.family_id == family.id)
            ).all()
        )
        for tag in media_person_tags:
            field_count += _protect_object(
                db,
                family=family,
                obj=tag,
                fields={"tagged_by": TEXT_PLACEHOLDER, "note": TEXT_PLACEHOLDER},
                master_key=master_key,
            )
        requests = (
            list(
                db.scalars(
                    select(GenerativeMediaRequest).where(
                        GenerativeMediaRequest.elder_id.in_(profile_ids)
                    )
                ).all()
            )
            if profile_ids
            else []
        )
        for request in requests:
            field_count += _protect_object(
                db,
                family=family,
                obj=request,
                fields={
                    "actor_label": TEXT_PLACEHOLDER,
                    "reviewed_by": TEXT_PLACEHOLDER,
                    "review_notes": TEXT_PLACEHOLDER,
                },
                master_key=master_key,
            )
        family_object_ids = {
            family.id,
            *person_ids,
            *profile_ids,
            *session_ids,
            *[item.id for item in drafts],
            *story_ids,
            *[item.id for item in details],
            *[item.id for item in contributions],
            *([legacy_plan.id] if legacy_plan else []),
            *[item.id for item in media_person_tags],
            *[item.id for item in requests],
        }
        for consent in db.scalars(
            select(ConsentEvent).where(ConsentEvent.object_id.in_(family_object_ids))
        ).all():
            field_count += _protect_object(
                db,
                family=family,
                obj=consent,
                fields={"actor_label": TEXT_PLACEHOLDER},
                master_key=master_key,
            )

        assets = (
            list(
                db.scalars(
                    select(MediaAsset).where(MediaAsset.session_id.in_(session_ids))
                ).all()
            )
            if session_ids
            else []
        )
        for asset in assets:
            field_count += _protect_object(
                db,
                family=family,
                obj=asset,
                fields={"original_filename": TEXT_PLACEHOLDER},
                master_key=master_key,
            )
            if asset.encryption_version:
                continue
            source = resolve_controlled_path(settings.resolved_asset_root, asset.relative_path)
            if not verify_asset_integrity(
                source, expected_size=asset.size_bytes, expected_sha256=asset.sha256
            ):
                raise ValueError("ASSET_INTEGRITY_FAILED")
            temp = source.with_name(f".{source.name}.encrypting-{uuid4().hex}")
            result = encrypt_media_file(
                source,
                temp,
                master_key,
                associated_data=media_context(family.id, asset.id),
            )
            prepared.append((source, temp, asset, result))
            media_count += 1

        books = (
            list(
                db.scalars(select(MemoryBook).where(MemoryBook.elder_id.in_(profile_ids))).all()
            )
            if profile_ids
            else []
        )
        for book in books:
            if not book.pdf_relative_path or book.pdf_encryption_version:
                continue
            source = resolve_controlled_path(settings.resolved_asset_root, book.pdf_relative_path)
            if not source.is_file():
                continue
            temp = source.with_name(f".{source.name}.encrypting-{uuid4().hex}")
            result = encrypt_media_file(
                source,
                temp,
                master_key,
                associated_data=media_context(family.id, f"memory-book-{book.id}"),
            )
            prepared.append((source, temp, book, result))
            book_count += 1

        db.flush()
        for source, temp, obj, result in prepared:
            backup = source.with_name(f".{source.name}.plaintext-backup-{uuid4().hex}")
            os.replace(source, backup)
            replaced.append((source, backup))
            os.replace(temp, source)
            if isinstance(obj, MediaAsset):
                obj.plaintext_size_bytes = result.plaintext_size
                obj.plaintext_sha256 = result.plaintext_sha256
                obj.size_bytes = result.ciphertext_size
                obj.sha256 = result.ciphertext_sha256
                obj.encryption_version = 1
            else:
                obj.pdf_ciphertext_size = result.ciphertext_size
                obj.pdf_ciphertext_sha256 = result.ciphertext_sha256
                obj.pdf_encryption_version = 1
        family.data_classification = target_classification
        metadata.encryption_status = "active_encrypted"
        from app.models.entities import now_utc

        metadata.activated_at = now_utc()
        db.commit()
        for _, backup in replaced:
            backup.unlink(missing_ok=True)
        return ArchiveEncryptionResult(field_count, media_count, book_count)
    except Exception:
        db.rollback()
        for source, backup in reversed(replaced):
            if backup.is_file():
                source.unlink(missing_ok=True)
                os.replace(backup, source)
        for _, temp, _, _ in prepared:
            temp.unlink(missing_ok=True)
        metadata = db.get(ArchiveSecurity, metadata.id)
        if metadata:
            metadata.encryption_status = "encryption_failed"
            db.commit()
        raise
