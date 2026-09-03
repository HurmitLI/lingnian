from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import ArchiveSecurity, FamilyArchive, FamilyMembership, UserAccount
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
    expected = settings.formal_invite_code.get_secret_value() if settings.formal_invite_code else ""
    if not hmac.compare_digest(payload.invitation_code.strip(), expected.strip()):
        raise DomainError("INVITATION_INVALID", "邀请码不正确或已经失效。", 403)

    username = normalize_username(payload.username)
    if db.scalar(select(UserAccount).where(UserAccount.username == username)):
        raise DomainError("USERNAME_TAKEN", "这个登录名已经被使用。", 409)

    account_count = db.scalar(select(func.count()).select_from(UserAccount)) or 0
    family = None
    if settings.formal_family_id:
        family = db.get(FamilyArchive, settings.formal_family_id)
        if family is None:
            raise DomainError("FORMAL_FAMILY_NOT_FOUND", "正式家庭空间配置无效。", 503)
    else:
        families = list(db.scalars(select(FamilyArchive).limit(2)))
        if len(families) > 1:
            raise DomainError("FORMAL_FAMILY_AMBIGUOUS", "存在多个家庭空间，请先指定正式空间。", 503)
        family = families[0] if families else None
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
        role="owner" if account_count == 0 else "member",
    )
    db.add(membership)
    db.commit()
    db.refresh(user)
    db.refresh(membership)
    token = create_session(db, user, days=settings.auth_session_days)
    set_session_cookie(response, token)
    return auth_read(db, user, membership)


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
