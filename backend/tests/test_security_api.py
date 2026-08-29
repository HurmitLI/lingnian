from __future__ import annotations

from sqlalchemy import select

from app.main import app
from app.models import ConsentEvent
from app.services.security import (
    InMemorySecretStore,
    get_family_key_manager,
    get_secret_store,
    recover_master_key,
)


def create_family(client) -> dict:
    response = client.post(
        "/api/v1/families",
        json={
            "display_name": "虚构安全测试家庭",
            "idempotency_key": "security-family",
            "data_classification": "test",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_family_security_initialization_and_recovery_package(client, db):
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        family = create_family(client)
        status = client.get(f"/api/v1/families/{family['id']}/security")
        assert status.status_code == 200
        assert status.json()["encryption_status"] == "not_initialized"
        assert status.json()["key_initialized"] is False

        initialized = client.post(
            f"/api/v1/families/{family['id']}/security/initialize",
            json={"actor_label": "测试家庭成员"},
        )
        assert initialized.status_code == 201
        assert initialized.json()["encryption_status"] == "key_ready"
        assert initialized.json()["key_initialized"] is True

        repeated = client.post(
            f"/api/v1/families/{family['id']}/security/initialize",
            json={"actor_label": "测试家庭成员"},
        )
        assert repeated.status_code == 201
        assert len(
            db.scalars(
                select(ConsentEvent).where(
                    ConsentEvent.action == "initialize_archive_security"
                )
            ).all()
        ) == 1

        recovery = client.post(
            f"/api/v1/families/{family['id']}/security/recovery-package",
            json={
                "actor_label": "测试家庭成员",
                "recovery_passphrase": "虚构恢复口令-长度足够-2026",
            },
        )
        assert recovery.status_code == 200
        assert recovery.headers["content-disposition"].startswith("attachment;")

        expected_key = get_family_key_manager(family["id"], store).get_existing()
        recovered_key = recover_master_key(
            recovery.text,
            "虚构恢复口令-长度足够-2026",
            expected_scope=f"family:{family['id']}",
        )
        assert recovered_key == expected_key
        security_status = client.get(f"/api/v1/families/{family['id']}/security").json()
        assert security_status["recovery_package_created_at"] is not None
    finally:
        app.dependency_overrides.pop(get_secret_store, None)


def test_recovery_package_requires_initialized_security(client):
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        family = create_family(client)
        response = client.post(
            f"/api/v1/families/{family['id']}/security/recovery-package",
            json={
                "actor_label": "测试家庭成员",
                "recovery_passphrase": "虚构恢复口令-长度足够-2026",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SECURITY_NOT_INITIALIZED"
    finally:
        app.dependency_overrides.pop(get_secret_store, None)
