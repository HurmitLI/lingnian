from __future__ import annotations

import base64

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.models import (
    AuthSession,
    ElderProfile,
    FamilyArchive,
    InterviewAssignment,
    MemorySession,
    Person,
    UserAccount,
)
from app.services.request_limits import auth_attempt_limiter
from app.services.security import get_secret_store


@pytest.fixture(autouse=True)
def clear_secret_store_cache():
    get_secret_store.cache_clear()
    auth_attempt_limiter.clear()
    yield
    auth_attempt_limiter.clear()
    get_secret_store.cache_clear()


def enable_formal_auth(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "formal_auth_required", True)
    monkeypatch.setattr(settings, "formal_invite_code", SecretStr("family-invite-2026"))
    monkeypatch.setattr(settings, "formal_family_id", None)
    monkeypatch.setattr(
        settings,
        "formal_archive_master_key",
        SecretStr(base64.urlsafe_b64encode(b"k" * 32).decode("ascii")),
    )
    monkeypatch.setattr(settings, "auth_cookie_secure", False)


def test_invitation_registration_login_and_logout(client, db, monkeypatch):
    enable_formal_auth(monkeypatch)

    anonymous = client.get("/api/v1/elder-profiles")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "AUTH_REQUIRED"

    wrong_invite = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "wrong-code",
            "username": "daughter@example.com",
            "display_name": "女儿",
            "password": "a-secure-password",
            "family_name": "我们的家",
        },
    )
    assert wrong_invite.status_code == 403

    registered = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "family-invite-2026",
            "username": "Daughter@example.com",
            "display_name": "女儿",
            "password": "a-secure-password",
            "family_name": "我们的家",
        },
    )
    assert registered.status_code == 201
    payload = registered.json()
    assert payload["username"] == "daughter@example.com"
    assert payload["family_name"] == "我们的家"
    assert payload["role"] == "owner"
    assert client.cookies.get("lingnian_session")

    account = db.scalar(select(UserAccount).where(UserAccount.username == "daughter@example.com"))
    assert account is not None
    assert account.password_hash != "a-secure-password"
    auth_session = db.scalar(select(AuthSession).where(AuthSession.user_id == account.id))
    assert auth_session is not None
    assert auth_session.token_hash != client.cookies.get("lingnian_session")

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["family_id"] == payload["family_id"]
    assert client.get("/api/v1/elder-profiles").status_code == 200

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": "daughter@example.com", "password": "a-secure-password"},
    )
    assert logged_in.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200


def test_duplicate_username_is_rejected(client, monkeypatch):
    enable_formal_auth(monkeypatch)
    request = {
        "invitation_code": "family-invite-2026",
        "username": "family.member",
        "display_name": "家人",
        "password": "a-secure-password",
        "family_name": "我们的家",
    }
    assert client.post("/api/v1/auth/register", json=request).status_code == 201
    client.cookies.clear()
    duplicate = client.post("/api/v1/auth/register", json=request)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USERNAME_TAKEN"


def test_repeated_failed_login_is_rate_limited(client, monkeypatch):
    enable_formal_auth(monkeypatch)
    for _ in range(8):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "unknown.account", "password": "wrong-password"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/v1/auth/login",
        json={"username": "unknown.account", "password": "wrong-password"},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


def test_formal_session_cannot_read_another_family(client, db, monkeypatch):
    enable_formal_auth(monkeypatch)
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "family-invite-2026",
            "username": "family.owner",
            "display_name": "管理员",
            "password": "a-secure-password",
            "family_name": "我的家庭",
        },
    )
    assert registered.status_code == 201
    own_family_id = registered.json()["family_id"]

    other_family = FamilyArchive(display_name="其他家庭", data_classification="test")
    other_person = Person(family=other_family, role="elder", display_name="其他讲述者")
    other_profile = ElderProfile(person=other_person, preferred_name="其他讲述者")
    db.add(other_profile)
    db.commit()
    db.refresh(other_profile)

    profiles = client.get("/api/v1/elder-profiles")
    assert profiles.status_code == 200
    assert profiles.json() == []
    hidden = client.get(f"/api/v1/elder-profiles/{other_profile.id}")
    assert hidden.status_code == 404

    create_second_family = client.post(
        "/api/v1/families",
        json={"display_name": "绕过创建", "data_classification": "test"},
    )
    assert create_second_family.status_code == 403
    assert db.get(FamilyArchive, own_family_id) is not None


def test_owner_creates_one_time_invitation_for_a_member(client, monkeypatch):
    enable_formal_auth(monkeypatch)
    owner = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "family-invite-2026",
            "username": "owner.account",
            "display_name": "管理员",
            "password": "owner-secure-password",
            "family_name": "我们的家",
        },
    )
    assert owner.status_code == 201

    created = client.post("/api/v1/auth/invitations", json={"expires_in_days": 3})
    assert created.status_code == 201
    invitation_code = created.json()["invitation_code"]
    assert invitation_code.startswith("LN-")
    invitation_id = created.json()["id"]
    assert all("invitation_code" not in item for item in client.get("/api/v1/auth/invitations").json())

    client.post("/api/v1/auth/logout")
    member = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": invitation_code,
            "username": "member.account",
            "display_name": "受邀家人",
            "password": "member-secure-password",
            "family_name": "不会另建家庭",
        },
    )
    assert member.status_code == 201
    assert member.json()["family_id"] == owner.json()["family_id"]
    assert member.json()["role"] == "member"
    assert client.post("/api/v1/auth/invitations", json={}).status_code == 403

    client.post("/api/v1/auth/logout")
    reused = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": invitation_code,
            "username": "another.member",
            "display_name": "另一位家人",
            "password": "another-secure-password",
        },
    )
    assert reused.status_code == 403

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": "owner.account", "password": "owner-secure-password"},
    )
    assert logged_in.status_code == 200
    members = client.get("/api/v1/auth/members")
    assert members.status_code == 200
    assert [item["display_name"] for item in members.json()] == ["管理员", "受邀家人"]
    owner_membership = next(item for item in members.json() if item["role"] == "owner")
    invited_membership = next(item for item in members.json() if item["role"] == "member")
    cannot_remove_owner = client.delete(
        f"/api/v1/auth/members/{owner_membership['membership_id']}"
    )
    assert cannot_remove_owner.status_code == 409
    assert client.delete(
        f"/api/v1/auth/members/{invited_membership['membership_id']}"
    ).status_code == 204
    assert client.delete(f"/api/v1/auth/invitations/{invitation_id}").status_code == 204

    client.post("/api/v1/auth/logout")
    revoked_member = client.post(
        "/api/v1/auth/login",
        json={"username": "member.account", "password": "member-secure-password"},
    )
    assert revoked_member.status_code == 403


def test_interview_invitation_starts_assigned_family_interview(client, db, monkeypatch):
    enable_formal_auth(monkeypatch)
    owner = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "family-invite-2026",
            "username": "interview.owner",
            "display_name": "管理员",
            "password": "owner-secure-password",
            "family_name": "我们的家",
        },
    )
    assert owner.status_code == 201
    family_id = owner.json()["family_id"]
    elder = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": family_id,
            "display_name": "外公",
            "preferred_name": "外公",
        },
    )
    assert elder.status_code == 201
    narrator = client.post(
        f"/api/v1/families/{family_id}/people",
        json={"display_name": "妈妈", "role": "family_member"},
    )
    assert narrator.status_code == 201

    incomplete = client.post(
        "/api/v1/auth/invitations",
        json={"elder_id": elder.json()["id"], "life_stage": "童年"},
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "INTERVIEW_INVITATION_INCOMPLETE"

    created = client.post(
        "/api/v1/auth/invitations",
        json={
            "expires_in_days": 7,
            "elder_id": elder.json()["id"],
            "narrator_person_id": narrator.json()["id"],
            "life_stage": "童年",
        },
    )
    assert created.status_code == 201
    invitation = created.json()
    assert invitation["purpose"] == "interview"
    assert invitation["life_stage"] == "童年"

    client.post("/api/v1/auth/logout")
    member = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": invitation["invitation_code"],
            "username": "invited.narrator",
            "display_name": "受邀讲述人",
            "password": "member-secure-password",
        },
    )
    assert member.status_code == 201
    next_path = member.json()["next_path"]
    assert next_path.startswith(f"/record?elder={elder.json()['id']}&session=")
    session_id = next_path.split("session=", 1)[1].split("&", 1)[0]

    detail = client.get(f"/api/v1/memory-sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["session"]["interview_mode"] == "guided_voice"
    assert detail.json()["session"]["narrator_person_id"] == narrator.json()["id"]
    assert detail.json()["session"]["life_stage"] == "童年"
    assert detail.json()["session"]["status"] == "INTERVIEWING"

    assignment = db.scalar(
        select(InterviewAssignment).where(InterviewAssignment.session_id == session_id)
    )
    assert assignment is not None
    assert assignment.status == "claimed"
    assert db.get(MemorySession, session_id) is not None

    client.post("/api/v1/auth/logout")
    resumed = client.post(
        "/api/v1/auth/login",
        json={"username": "invited.narrator", "password": "member-secure-password"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["next_path"] == next_path

    client.post("/api/v1/auth/logout")
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "interview.owner", "password": "owner-secure-password"},
    ).status_code == 200
    listed = client.get("/api/v1/auth/invitations")
    listed_item = next(item for item in listed.json() if item["id"] == invitation["id"])
    assert listed_item["purpose"] == "interview"
    assert listed_item["session_id"] == session_id
    assert listed_item["interview_status"] == "INTERVIEWING"


def test_account_can_change_password_and_other_sessions_are_revoked(client, monkeypatch):
    enable_formal_auth(monkeypatch)
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "family-invite-2026",
            "username": "password.owner",
            "display_name": "管理员",
            "password": "old-secure-password",
            "family_name": "我的家庭",
        },
    )
    assert registered.status_code == 201

    second_client = type(client)(client.app)
    try:
        assert second_client.post(
            "/api/v1/auth/login",
            json={"username": "password.owner", "password": "old-secure-password"},
        ).status_code == 200
        wrong_current = client.post(
            "/api/v1/auth/password",
            json={"current_password": "wrong-password", "new_password": "new-secure-password"},
        )
        assert wrong_current.status_code == 403

        changed = client.post(
            "/api/v1/auth/password",
            json={
                "current_password": "old-secure-password",
                "new_password": "new-secure-password",
            },
        )
        assert changed.status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 200
        assert second_client.get("/api/v1/auth/me").status_code == 401
    finally:
        second_client.close()

    client.post("/api/v1/auth/logout")
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "password.owner", "password": "old-secure-password"},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "password.owner", "password": "new-secure-password"},
    ).status_code == 200


def test_formal_mode_disables_local_keychain_and_whole_archive_backup(client, monkeypatch):
    enable_formal_auth(monkeypatch)
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_code": "family-invite-2026",
            "username": "cloud.owner",
            "display_name": "云端管理员",
            "password": "cloud-secure-password",
            "family_name": "云端家庭",
        },
    )
    assert registered.status_code == 201
    family_id = registered.json()["family_id"]

    security = client.get(f"/api/v1/families/{family_id}/security")
    assert security.status_code == 200
    assert security.json()["encryption_status"] == "active_encrypted"

    initialize = client.post(
        f"/api/v1/families/{family_id}/security/initialize",
        json={"actor_label": "管理员"},
    )
    assert initialize.status_code == 403
    assert initialize.json()["error"]["code"] == "CLOUD_SECURITY_MANAGED"

    backups = client.get("/api/v1/backups")
    assert backups.status_code == 403
    assert backups.json()["error"]["code"] == "CLOUD_SECURITY_MANAGED"
