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
from app.models import (
    ArchiveSecurity,
    AuthSession,
    ElderProfile,
    FamilyArchive,
    FamilyInvite,
    FamilyMembership,
    InterviewAssignment,
    MemorySession,
    Person,
    PlatformFamilyInvite,
    UserAccount,
)
from app.services.memory import select_question
from app.services.security import (
    TEXT_PLACEHOLDER,
    SecretStore,
    get_family_key_manager,
    get_secret_store,
    protect_field,
)
from app.services.request_limits import auth_attempt_key, auth_attempt_limiter
from app.services.auth import (
    create_session,
    current_auth,
    hash_password,
    normalize_username,
    revoke_session,
    session_token_hash,
    verify_password,
)


router = APIRouter(prefix="/api/v1/auth", tags=["formal-auth"])
LIFE_STAGES = {"童年", "求学", "工作", "婚恋", "育儿", "价值观", "老物件"}


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
    platform_role: str
    next_path: str | None = None


class InvitationCreate(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)
    elder_id: str | None = None
    narrator_person_id: str | None = None
    life_stage: str | None = Field(default=None, max_length=40)


class InvitationCreated(BaseModel):
    id: str
    invitation_code: str
    expires_at: datetime
    max_uses: int
    purpose: str
    elder_id: str | None = None
    narrator_person_id: str | None = None
    life_stage: str | None = None


class InvitationRead(BaseModel):
    id: str
    expires_at: datetime
    max_uses: int
    use_count: int
    revoked: bool
    status: str
    purpose: str
    elder_id: str | None = None
    narrator_person_id: str | None = None
    life_stage: str | None = None
    session_id: str | None = None
    interview_status: str | None = None


class MemberRead(BaseModel):
    membership_id: str
    user_id: str
    username: str
    display_name: str
    role: str
    status: str
    last_login_at: datetime | None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class AccountDelete(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    confirmation: str = Field(min_length=1, max_length=40)


class PlatformInvitationCreate(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)


class PlatformInvitationCreated(BaseModel):
    id: str
    invitation_code: str
    expires_at: datetime
    max_uses: int


class PlatformInvitationRead(BaseModel):
    id: str
    expires_at: datetime
    max_uses: int
    use_count: int
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


def current_platform_admin(db: Session) -> UserAccount:
    context = current_auth.get()
    if context is None:
        raise DomainError("AUTH_REQUIRED", "请先登录。", 401)
    user = db.get(UserAccount, context.user_id)
    if user is None or user.platform_role != "admin":
        raise DomainError("PLATFORM_ADMIN_REQUIRED", "只有平台管理员可以发放新家庭体验码。", 403)
    return user


def next_interview_path(db: Session, user_id: str) -> str | None:
    assignment = db.scalar(
        select(InterviewAssignment)
        .join(MemorySession, MemorySession.id == InterviewAssignment.session_id)
        .where(
            InterviewAssignment.assigned_user_id == user_id,
            InterviewAssignment.status == "claimed",
            MemorySession.status.not_in({"ARCHIVED", "SKIPPED"}),
        )
        .order_by(InterviewAssignment.claimed_at.desc())
        .limit(1)
    )
    if assignment is None or assignment.session_id is None:
        return None
    return (
        f"/record?elder={assignment.elder_id}"
        f"&session={assignment.session_id}&source=family-invite"
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
        platform_role=user.platform_role,
        next_path=next_interview_path(db, user.id),
    )


def assignment_for_invite(db: Session, invitation_id: str) -> InterviewAssignment | None:
    return db.scalar(
        select(InterviewAssignment).where(
            InterviewAssignment.family_invite_id == invitation_id
        )
    )


def create_assigned_interview_session(
    db: Session,
    *,
    assignment: InterviewAssignment,
    family: FamilyArchive,
    user: UserAccount,
    secret_store: SecretStore,
) -> MemorySession:
    elder = db.get(ElderProfile, assignment.elder_id)
    narrator = db.get(Person, assignment.narrator_person_id)
    if (
        elder is None
        or narrator is None
        or elder.person.family_id != family.id
        or narrator.family_id != family.id
    ):
        raise DomainError(
            "INTERVIEW_INVITATION_CONTEXT_INVALID",
            "这份采访邀请已失效，请联系家庭管理员重新生成。",
            409,
        )
    try:
        question = select_question(
            db,
            elder_id=elder.id,
            life_stage=assignment.life_stage,
            topic_confirmed=True,
        )
    except ValueError as exc:
        if str(exc) == "TOPIC_BLOCKED_BY_PREFERENCE":
            raise DomainError(
                "TOPIC_BLOCKED_BY_PREFERENCE",
                "这位家人已选择不再聊这个话题。",
                409,
            ) from exc
        raise
    except Exception as exc:
        raise DomainError(
            "QUESTION_GENERATION_FAILED",
            "暂时没能生成采访问题，请联系家庭管理员重试。",
            503,
        ) from exc
    session = MemorySession(
        elder_id=elder.id,
        narrator_person_id=narrator.id,
        interview_mode="guided_voice",
        life_stage=assignment.life_stage,
        prompt_id=question.prompt_id,
        question_text=question.question_text,
        status="INTERVIEWING",
    )
    db.add(session)
    db.flush()
    master_key, _ = get_family_key_manager(family.id, secret_store).get_or_create()
    protect_field(
        db,
        family=family,
        object_type=session.__tablename__,
        object_id=session.id,
        field_name="question_text",
        value=question.question_text,
        master_key=master_key,
    )
    session.question_text = TEXT_PLACEHOLDER
    assignment.assigned_user_id = user.id
    assignment.session_id = session.id
    assignment.status = "claimed"
    assignment.claimed_at = datetime.now(UTC).replace(tzinfo=None)
    return session


@router.post("/register", response_model=AuthRead, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    secret_store: SecretStore = Depends(get_secret_store),
) -> AuthRead:
    require_formal_mode()
    settings = get_settings()
    username = normalize_username(payload.username)
    attempt_key = auth_attempt_key(request, "register", username)
    auth_attempt_limiter.check(attempt_key, limit=5, window_seconds=10 * 60)
    if db.scalar(select(UserAccount).where(UserAccount.username == username)):
        raise DomainError("USERNAME_TAKEN", "这个登录名已经被使用。", 409)

    account_count = db.scalar(select(func.count()).select_from(UserAccount)) or 0
    family = None
    membership_role = "owner" if account_count == 0 else "member"
    pending_invitation = None
    pending_platform_invitation = None
    pending_assignment = None
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
        code_hash = invitation_hash(payload.invitation_code.strip())
        pending_invitation = db.scalar(
            select(FamilyInvite).where(
                FamilyInvite.code_hash == code_hash,
                FamilyInvite.revoked_at.is_(None),
                FamilyInvite.expires_at > now,
                FamilyInvite.use_count < FamilyInvite.max_uses,
            )
        )
        if pending_invitation is None:
            pending_platform_invitation = db.scalar(
                select(PlatformFamilyInvite).where(
                    PlatformFamilyInvite.code_hash == code_hash,
                    PlatformFamilyInvite.revoked_at.is_(None),
                    PlatformFamilyInvite.expires_at > now,
                    PlatformFamilyInvite.use_count < PlatformFamilyInvite.max_uses,
                )
            )
            if pending_platform_invitation is None:
                raise DomainError("INVITATION_INVALID", "邀请码不正确或已经失效。", 403)
            membership_role = "owner"
        else:
            family = pending_invitation.family
            membership_role = pending_invitation.role
            pending_assignment = assignment_for_invite(db, pending_invitation.id)
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
        platform_role="admin" if account_count == 0 else "user",
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
    if pending_platform_invitation is not None:
        pending_platform_invitation.use_count += 1
    if pending_assignment is not None:
        create_assigned_interview_session(
            db,
            assignment=pending_assignment,
            family=family,
            user=user,
            secret_store=secret_store,
        )
    db.commit()
    db.refresh(user)
    db.refresh(membership)
    token = create_session(db, user, days=settings.auth_session_days)
    auth_attempt_limiter.reset(attempt_key)
    set_session_cookie(response, token)
    return auth_read(db, user, membership)


@router.post(
    "/platform-invitations",
    response_model=PlatformInvitationCreated,
    status_code=201,
)
def create_platform_invitation(
    payload: PlatformInvitationCreate,
    db: Session = Depends(get_db),
) -> PlatformInvitationCreated:
    require_formal_mode()
    admin = current_platform_admin(db)
    code = f"LN-F-{secrets.token_urlsafe(12)}"
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        days=payload.expires_in_days
    )
    invitation = PlatformFamilyInvite(
        created_by_user_id=admin.id,
        code_hash=invitation_hash(code),
        max_uses=1,
        use_count=0,
        expires_at=expires_at,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return PlatformInvitationCreated(
        id=invitation.id,
        invitation_code=code,
        expires_at=invitation.expires_at,
        max_uses=invitation.max_uses,
    )


@router.get(
    "/platform-invitations",
    response_model=list[PlatformInvitationRead],
)
def list_platform_invitations(
    db: Session = Depends(get_db),
) -> list[PlatformInvitationRead]:
    require_formal_mode()
    admin = current_platform_admin(db)
    now = datetime.now(UTC).replace(tzinfo=None)
    invitations = db.scalars(
        select(PlatformFamilyInvite)
        .where(PlatformFamilyInvite.created_by_user_id == admin.id)
        .order_by(PlatformFamilyInvite.created_at.desc())
        .limit(30)
    ).all()
    return [
        PlatformInvitationRead(
            id=item.id,
            expires_at=item.expires_at,
            max_uses=item.max_uses,
            use_count=item.use_count,
            status=(
                "revoked"
                if item.revoked_at is not None
                else "used"
                if item.use_count >= item.max_uses
                else "expired"
                if item.expires_at <= now
                else "active"
            ),
        )
        for item in invitations
    ]


@router.delete("/platform-invitations/{invitation_id}", status_code=204)
def revoke_platform_invitation(
    invitation_id: str,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    require_formal_mode()
    admin = current_platform_admin(db)
    invitation = db.get(PlatformFamilyInvite, invitation_id)
    if invitation is None or invitation.created_by_user_id != admin.id:
        raise DomainError("PLATFORM_INVITATION_NOT_FOUND", "没有找到这个新家庭体验码。", 404)
    if invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    response.status_code = 204
    return response


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
    assignment = None
    assignment_values = [payload.elder_id, payload.narrator_person_id, payload.life_stage]
    if any(value is not None for value in assignment_values):
        if not all(value is not None for value in assignment_values):
            raise DomainError(
                "INTERVIEW_INVITATION_INCOMPLETE",
                "采访邀请需要同时选择记录对象、讲述人和话题。",
                422,
            )
        life_stage = payload.life_stage.strip()
        if life_stage not in LIFE_STAGES:
            raise DomainError("LIFE_STAGE_INVALID", "请选择有效的人生阶段。", 422)
        elder = db.get(ElderProfile, payload.elder_id)
        narrator = db.get(Person, payload.narrator_person_id)
        if elder is None or elder.person.family_id != family_id:
            raise DomainError("ELDER_NOT_FOUND", "没有找到要记录的家人。", 404)
        if narrator is None or narrator.family_id != family_id:
            raise DomainError("NARRATOR_NOT_FOUND", "没有找到本次讲述人。", 404)
        try:
            select_question(
                db,
                elder_id=elder.id,
                life_stage=life_stage,
                topic_confirmed=True,
            )
        except ValueError as exc:
            if str(exc) == "TOPIC_BLOCKED_BY_PREFERENCE":
                raise DomainError(
                    "TOPIC_BLOCKED_BY_PREFERENCE",
                    "这位家人已选择不再聊这个话题。",
                    409,
                ) from exc
            raise
        db.flush()
        assignment = InterviewAssignment(
            family_invite_id=invitation.id,
            family_id=family_id,
            elder_id=elder.id,
            narrator_person_id=narrator.id,
            life_stage=life_stage,
            status="pending",
        )
        db.add(assignment)
    db.commit()
    db.refresh(invitation)
    return InvitationCreated(
        id=invitation.id,
        invitation_code=code,
        expires_at=invitation.expires_at,
        max_uses=invitation.max_uses,
        purpose="interview" if assignment else "family_access",
        elder_id=assignment.elder_id if assignment else None,
        narrator_person_id=assignment.narrator_person_id if assignment else None,
        life_stage=assignment.life_stage if assignment else None,
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
        assignment = assignment_for_invite(db, item.id)
        interview_session = (
            db.get(MemorySession, assignment.session_id)
            if assignment and assignment.session_id
            else None
        )
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
                purpose="interview" if assignment else "family_access",
                elder_id=assignment.elder_id if assignment else None,
                narrator_person_id=assignment.narrator_person_id if assignment else None,
                life_stage=assignment.life_stage if assignment else None,
                session_id=assignment.session_id if assignment else None,
                interview_status=(
                    interview_session.status
                    if interview_session
                    else assignment.status if assignment else None
                ),
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
        assignment = assignment_for_invite(db, invitation.id)
        if assignment is not None and assignment.session_id is None:
            assignment.status = "revoked"
        db.commit()
    response.status_code = 204
    return response


@router.get("/members", response_model=list[MemberRead])
def list_members(db: Session = Depends(get_db)) -> list[MemberRead]:
    require_formal_mode()
    _, family_id = current_owner()
    memberships = db.scalars(
        select(FamilyMembership)
        .where(FamilyMembership.family_id == family_id)
        .order_by(FamilyMembership.created_at)
    ).all()
    return [
        MemberRead(
            membership_id=membership.id,
            user_id=membership.user_id,
            username=membership.user.username,
            display_name=membership.user.display_name,
            role=membership.role,
            status=membership.status,
            last_login_at=membership.user.last_login_at,
        )
        for membership in memberships
    ]


@router.delete("/members/{membership_id}", status_code=204)
def revoke_member(
    membership_id: str,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    require_formal_mode()
    owner_user_id, family_id = current_owner()
    membership = db.get(FamilyMembership, membership_id)
    if membership is None or membership.family_id != family_id:
        raise DomainError("MEMBER_NOT_FOUND", "没有找到这位家庭成员。", 404)
    if membership.user_id == owner_user_id or membership.role == "owner":
        raise DomainError("OWNER_CANNOT_BE_REVOKED", "家庭空间管理员不能在这里被移除。", 409)
    membership.status = "revoked"
    now = datetime.now(UTC).replace(tzinfo=None)
    for auth_session in db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == membership.user_id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        auth_session.revoked_at = now
    db.commit()
    response.status_code = 204
    return response


@router.patch("/members/{membership_id}/make-owner", status_code=204)
def transfer_family_ownership(
    membership_id: str,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    require_formal_mode()
    owner_user_id, family_id = current_owner()
    target = db.get(FamilyMembership, membership_id)
    if (
        target is None
        or target.family_id != family_id
        or target.status != "active"
    ):
        raise DomainError("MEMBER_NOT_FOUND", "没有找到这位可接管的家庭成员。", 404)
    if target.user_id == owner_user_id or target.role == "owner":
        raise DomainError("OWNER_UNCHANGED", "这位家人已经是家庭管理员。", 409)
    owner_membership = db.scalar(
        select(FamilyMembership).where(
            FamilyMembership.user_id == owner_user_id,
            FamilyMembership.family_id == family_id,
            FamilyMembership.status == "active",
        )
    )
    if owner_membership is None:
        raise DomainError("OWNER_MEMBERSHIP_MISSING", "当前管理权状态异常。", 409)
    owner_membership.role = "member"
    target.role = "owner"
    db.commit()
    response.status_code = 204
    return response


@router.post("/login", response_model=AuthRead)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthRead:
    require_formal_mode()
    username = normalize_username(payload.username)
    attempt_key = auth_attempt_key(request, "login", username)
    auth_attempt_limiter.check(attempt_key, limit=8, window_seconds=5 * 60)
    user = db.scalar(
        select(UserAccount).where(UserAccount.username == username)
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
    auth_attempt_limiter.reset(attempt_key)
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


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    require_formal_mode()
    context = current_auth.get()
    if context is None:
        raise DomainError("AUTH_REQUIRED", "请先登录。", 401)
    user = db.get(UserAccount, context.user_id)
    if user is None or not verify_password(
        payload.current_password,
        user.password_salt,
        user.password_hash,
    ):
        raise DomainError("CURRENT_PASSWORD_INVALID", "当前密码不正确。", 403)
    if verify_password(payload.new_password, user.password_salt, user.password_hash):
        raise DomainError("PASSWORD_UNCHANGED", "新密码不能与当前密码相同。", 409)

    user.password_salt, user.password_hash = hash_password(payload.new_password)
    current_token = request.cookies.get(get_settings().auth_cookie_name)
    current_token_hash = session_token_hash(current_token) if current_token else None
    now = datetime.now(UTC).replace(tzinfo=None)
    for auth_session in db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        if auth_session.token_hash != current_token_hash:
            auth_session.revoked_at = now
    db.commit()
    response.status_code = 204
    return response


@router.delete("/account", status_code=204)
def delete_account(
    payload: AccountDelete,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
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
        raise DomainError("AUTH_REQUIRED", "登录状态已经失效。", 401)
    if not verify_password(payload.password, user.password_salt, user.password_hash):
        raise DomainError("CURRENT_PASSWORD_INVALID", "当前密码不正确。", 403)
    if payload.confirmation.strip() != "注销我的账号":
        raise DomainError("ACCOUNT_DELETE_CONFIRMATION_INVALID", "请完整输入“注销我的账号”。", 422)
    if user.platform_role == "admin":
        raise DomainError(
            "PLATFORM_ADMIN_TRANSFER_REQUIRED",
            "平台管理员账号需先完成管理权交接，不能直接注销。",
            409,
        )
    if membership.role == "owner":
        other_active_members = db.scalar(
            select(func.count())
            .select_from(FamilyMembership)
            .where(
                FamilyMembership.family_id == membership.family_id,
                FamilyMembership.status == "active",
                FamilyMembership.user_id != user.id,
            )
        ) or 0
        if other_active_members:
            raise DomainError(
                "FAMILY_OWNER_TRANSFER_REQUIRED",
                "请先把家庭管理权交给另一位家人，再注销账号。",
                409,
            )
        family_people = db.scalar(
            select(func.count())
            .select_from(Person)
            .where(Person.family_id == membership.family_id)
        ) or 0
        if family_people:
            raise DomainError(
                "FAMILY_ARCHIVE_EXPORT_REQUIRED",
                "这个家庭已有档案。请先导出传承包，并邀请一位家人接管后再注销。",
                409,
            )
        family = db.get(FamilyArchive, membership.family_id)
        if family is not None:
            db.delete(family)
            db.flush()
    db.delete(user)
    db.commit()
    response.delete_cookie(get_settings().auth_cookie_name, path="/")
    response.status_code = 204
    return response
