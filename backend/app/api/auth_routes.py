from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import ArchiveSecurity, FamilyArchive, FamilyInvite, FamilyMembership, UserAccount
from app.services.security import SecretStore, get_family_key_manager, get_secret_store
from app.services.auth import (
    create_session,
    current_auth,
    hash_password,
    normalize_username,
    revoke_session,
    verify_password,
)


router = APIRouter(prefix="/api/v1/auth", tags=["formal-auth"])


class RegisterRequest(BaseModel):
    invitation_code: str = Field(min_length=8, max_length=128)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.@+-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    family_name: str = Field(default="我的家庭", min_length=1, max_length=80)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class AuthRead(BaseModel):
    user_id: str
    username: str
    display_name: str
    family_id: str
    family_name: str
    role: str


class InvitationCreate(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationCreated(BaseModel):
    id: str
    invitation_code: str
    expires_at: datetime
    max_uses: int


class InvitationRead(BaseModel):
    id: str
    expires_at: datetime
    max_uses: int
    use_count: int
    revoked: bool
    status: str


def require_formal_mode() -> None:
    if not get_settings().formal_auth_required:
        raise DomainError("FORMAL_AUTH_DISABLED", "正式账号模式尚未开启。", 404)


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )


def invitation_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def current_owner() -> tuple[str, str]:
    context = current_auth.get()
    if context is None:
        raise DomainError("AUTH_REQUIRED", "请先登录。", 401)
    if context.role != "owner":
        raise DomainError("OWNER_REQUIRED", "只有家庭空间管理员可以管理邀请码。", 403)
    return context.user_id, context.family_id


def auth_read(db: Session, user: UserAccount, membership: FamilyMembership) -> AuthRead:
    family = db.get(FamilyArchive, membership.family_id)
    if family is None:
        raise DomainError("FAMILY_NOT_FOUND", "没有找到这个家庭空间。", 404)
    return AuthRead(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        family_id=family.id,
        family_name=family.display_name,
        role=membership.role,
    )


@router.post("/register", response_model=AuthRead, status_code=201)
def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> AuthRead:
    require_formal_mode()
    settings = get_settings()
    username = normalize_username(payload.username)
    if db.scalar(select(UserAccount).where(UserAccount.username == username)):
        raise DomainError("USERNAME_TAKEN", "这个登录名已经被使用。", 409)

    account_count = db.scalar(select(func.count()).select_from(UserAccount)) or 0
    family = None
    membership_role = "owner" if account_count == 0 else "member"
    pending_invitation = None
    if account_count == 0:
        expected = settings.formal_invite_code.get_secret_value() if settings.formal_invite_code else ""
        if not hmac.compare_digest(payload.invitation_code.strip(), expected.strip()):
            raise DomainError("INVITATION_INVALID", "邀请码不正确或已经失效。", 403)
        if settings.formal_family_id:
            family = db.get(FamilyArchive, settings.formal_family_id)
            if family is None:
                raise DomainError("FORMAL_FAMILY_NOT_FOUND", "正式家庭空间配置无效。", 503)
        else:
            families = list(db.scalars(select(FamilyArchive).limit(2)))
            if len(families) > 1:
                raise DomainError("FORMAL_FAMILY_AMBIGUOUS", "存在多个家庭空间，请先指定正式空间。", 503)
            family = families[0] if families else None
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        pending_invitation = db.scalar(
            select(FamilyInvite).where(
                FamilyInvite.code_hash == invitation_hash(payload.invitation_code.strip()),
                FamilyInvite.revoked_at.is_(None),
                FamilyInvite.expires_at > now,
                FamilyInvite.use_count < FamilyInvite.max_uses,
            )
        )
        if pending_invitation is None:
            raise DomainError("INVITATION_INVALID", "邀请码不正确或已经失效。", 403)
        family = pending_invitation.family
        membership_role = pending_invitation.role
    if family is None:
        family = FamilyArchive(
            display_name=payload.family_name.strip(),
            data_classification="authorized_sensitive",
        )
        db.add(family)
        db.flush()
        get_family_key_manager(family.id, secret_store).get_or_create()
        db.add(
            ArchiveSecurity(
                family_id=family.id,
                key_version=1,
                encryption_status="active_encrypted",
            )
        )

    salt, password_hash = hash_password(payload.password)
    user = UserAccount(
        username=username,
        display_name=payload.display_name.strip(),
        password_salt=salt,
        password_hash=password_hash,
    )
    db.add(user)
    db.flush()
    membership = FamilyMembership(
        user_id=user.id,
        family_id=family.id,
        role=membership_role,
    )
    db.add(membership)
    if pending_invitation is not None:
        pending_invitation.use_count += 1
    db.commit()
    db.refresh(user)
    db.refresh(membership)
    token = create_session(db, user, days=settings.auth_session_days)
    set_session_cookie(response, token)
    return auth_read(db, user, membership)


@router.post("/invitations", response_model=InvitationCreated, status_code=201)
def create_invitation(payload: InvitationCreate, db: Session = Depends(get_db)) -> InvitationCreated:
    require_formal_mode()
    user_id, family_id = current_owner()
    code = f"LN-{secrets.token_urlsafe(12)}"
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=payload.expires_in_days)
    invitation = FamilyInvite(
        family_id=family_id,
        created_by_user_id=user_id,
        code_hash=invitation_hash(code),
        role="member",
        max_uses=1,
        use_count=0,
        expires_at=expires_at,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return InvitationCreated(
        id=invitation.id,
        invitation_code=code,
        expires_at=invitation.expires_at,
        max_uses=invitation.max_uses,
    )


@router.get("/invitations", response_model=list[InvitationRead])
def list_invitations(db: Session = Depends(get_db)) -> list[InvitationRead]:
    require_formal_mode()
    _, family_id = current_owner()
    now = datetime.now(UTC).replace(tzinfo=None)
    invitations = db.scalars(
        select(FamilyInvite)
        .where(FamilyInvite.family_id == family_id)
        .order_by(FamilyInvite.created_at.desc())
        .limit(30)
    ).all()
    result = []
    for item in invitations:
        if item.revoked_at is not None:
            status = "revoked"
        elif item.use_count >= item.max_uses:
            status = "used"
        elif item.expires_at <= now:
            status = "expired"
        else:
            status = "active"
        result.append(
            InvitationRead(
                id=item.id,
                expires_at=item.expires_at,
                max_uses=item.max_uses,
                use_count=item.use_count,
                revoked=item.revoked_at is not None,
                status=status,
            )
        )
    return result


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: str, response: Response, db: Session = Depends(get_db)) -> Response:
    require_formal_mode()
    _, family_id = current_owner()
    invitation = db.get(FamilyInvite, invitation_id)
    if invitation is None or invitation.family_id != family_id:
        raise DomainError("INVITATION_NOT_FOUND", "没有找到这个邀请码。", 404)
    if invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    response.status_code = 204
    return response


@router.post("/login", response_model=AuthRead)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthRead:
    require_formal_mode()
    user = db.scalar(
        select(UserAccount).where(UserAccount.username == normalize_username(payload.username))
    )
    if user is None or not verify_password(payload.password, user.password_salt, user.password_hash):
        raise DomainError("LOGIN_FAILED", "登录名或密码不正确。", 401)
    if user.status != "active":
        raise DomainError("ACCOUNT_DISABLED", "这个账号暂时无法使用。", 403)
    membership = db.scalar(
        select(FamilyMembership).where(
            FamilyMembership.user_id == user.id,
            FamilyMembership.status == "active",
        )
    )
    if membership is None:
        raise DomainError("MEMBERSHIP_MISSING", "账号尚未加入家庭空间。", 403)
    settings = get_settings()
    token = create_session(db, user, days=settings.auth_session_days)
    set_session_cookie(response, token)
    return auth_read(db, user, membership)


@router.get("/me", response_model=AuthRead)
def me(db: Session = Depends(get_db)) -> AuthRead:
    require_formal_mode()
    context = current_auth.get()
    if context is None:
        raise DomainError("AUTH_REQUIRED", "请先登录。", 401)
    user = db.get(UserAccount, context.user_id)
    membership = db.scalar(
        select(FamilyMembership).where(
            FamilyMembership.user_id == context.user_id,
            FamilyMembership.family_id == context.family_id,
            FamilyMembership.status == "active",
        )
    )
    if user is None or membership is None:
        raise DomainError("AUTH_REQUIRED", "登录状态已经失效，请重新登录。", 401)
    return auth_read(db, user, membership)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    require_formal_mode()
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        revoke_session(db, token)
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.status_code = 204
    return response
