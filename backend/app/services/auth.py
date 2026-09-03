from __future__ import annotations

import hashlib
import hmac
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthSession, FamilyMembership, UserAccount


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    family_id: str
    role: str


current_auth: ContextVar[AuthContext | None] = ContextVar("current_auth", default=None)


def normalize_username(value: str) -> str:
    return value.strip().lower()


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=actual_salt, n=2**14, r=8, p=1, dklen=32
    )
    return actual_salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    try:
        _, candidate = hash_password(password, bytes.fromhex(salt_hex))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, digest_hex)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: UserAccount, *, days: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC).replace(tzinfo=None)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=session_token_hash(token),
            expires_at=now + timedelta(days=days),
            last_seen_at=now,
        )
    )
    user.last_login_at = now
    db.commit()
    return token


def authenticate_session(db: Session, token: str) -> AuthContext | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == session_token_hash(token))
    )
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
        or auth_session.user.status != "active"
    ):
        return None
    membership = db.scalar(
        select(FamilyMembership).where(
            FamilyMembership.user_id == auth_session.user_id,
            FamilyMembership.status == "active",
        )
    )
    if membership is None:
        return None
    auth_session.last_seen_at = now
    db.commit()
    return AuthContext(
        user_id=auth_session.user_id,
        family_id=membership.family_id,
        role=membership.role,
    )


def revoke_session(db: Session, token: str) -> None:
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == session_token_hash(token))
    )
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
