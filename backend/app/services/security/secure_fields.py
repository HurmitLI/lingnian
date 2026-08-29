from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EncryptedField, FamilyArchive
from app.services.security.crypto import decrypt_text, encrypt_text
from app.services.security.key_store import SecretStore
from app.services.security.service import get_family_key_manager


TEXT_PLACEHOLDER = "[niannian:encrypted:v1]"


def field_context(
    family_id: str, object_type: str, object_id: str, field_name: str, key_version: int
) -> str:
    return (
        f"family:{family_id}:object:{object_type}:{object_id}:"
        f"field:{field_name}:key-v{key_version}"
    )


def is_encrypted_family(family: FamilyArchive) -> bool:
    metadata = family.security_metadata
    return bool(
        family.data_classification != "test"
        and metadata
        and metadata.encryption_status == "active_encrypted"
    )


def require_family_master_key(family: FamilyArchive, store: SecretStore) -> bytes:
    metadata = family.security_metadata
    if metadata is None:
        raise ValueError("SECURITY_NOT_INITIALIZED")
    key = get_family_key_manager(
        family.id, store, key_version=metadata.key_version
    ).get_existing()
    if key is None:
        raise ValueError("MASTER_KEY_MISSING")
    return key


def protect_field(
    db: Session,
    *,
    family: FamilyArchive,
    object_type: str,
    object_id: str,
    field_name: str,
    value: Any,
    master_key: bytes,
) -> None:
    existing = db.scalar(
        select(EncryptedField).where(
            EncryptedField.family_id == family.id,
            EncryptedField.object_type == object_type,
            EncryptedField.object_id == object_id,
            EncryptedField.field_name == field_name,
        )
    )
    if value is None:
        if existing:
            db.delete(existing)
        return
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    ciphertext = encrypt_text(
        master_key,
        serialized,
        context=field_context(
            family.id,
            object_type,
            object_id,
            field_name,
            family.security_metadata.key_version,
        ),
    )
    content_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    if existing:
        existing.ciphertext = ciphertext
        existing.content_sha256 = content_sha256
        existing.key_version = family.security_metadata.key_version
    else:
        db.add(
            EncryptedField(
                family_id=family.id,
                object_type=object_type,
                object_id=object_id,
                field_name=field_name,
                ciphertext=ciphertext,
                content_sha256=content_sha256,
                key_version=family.security_metadata.key_version,
            )
        )


def reveal_field(
    db: Session,
    *,
    family: FamilyArchive,
    object_type: str,
    object_id: str,
    field_name: str,
    stored_value: Any,
    master_key: bytes | None,
) -> Any:
    if not is_encrypted_family(family):
        return stored_value
    if master_key is None:
        raise ValueError("MASTER_KEY_MISSING")
    encrypted = db.scalar(
        select(EncryptedField).where(
            EncryptedField.family_id == family.id,
            EncryptedField.object_type == object_type,
            EncryptedField.object_id == object_id,
            EncryptedField.field_name == field_name,
        )
    )
    if encrypted is None:
        return None
    serialized = decrypt_text(
        master_key,
        encrypted.ciphertext,
        context=field_context(
            family.id,
            object_type,
            object_id,
            field_name,
            encrypted.key_version,
        ),
    )
    if hashlib.sha256(serialized.encode("utf-8")).hexdigest() != encrypted.content_sha256:
        raise ValueError("ENCRYPTED_FIELD_INTEGRITY_FAILED")
    return json.loads(serialized)
