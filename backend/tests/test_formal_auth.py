from __future__ import annotations

import base64

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuthSession, ElderProfile, FamilyArchive, Person, UserAccount
from app.services.security import get_secret_store


@pytest.fixture(autouse=True)
def clear_secret_store_cache():
    get_secret_store.cache_clear()
    yield
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
    assert client.delete(f"/api/v1/auth/invitations/{invitation_id}").status_code == 204
