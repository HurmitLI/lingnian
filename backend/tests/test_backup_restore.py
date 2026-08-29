from __future__ import annotations

from app.main import app
from app.services.security import InMemorySecretStore, get_secret_store
from test_api_flow import create_profile, create_session, upload_test_audio


def test_local_backup_download_and_new_directory_verification(client):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    upload_test_audio(client, session["id"])

    created = client.post(
        "/api/v1/backups", json={"actor_label": "虚构本机管理员"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["asset_count"] == 1
    backup_id = created.json()["id"]

    verified = client.post(f"/api/v1/backups/{backup_id}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"
    assert verified.json()["verification_summary"]["restored_to_new_directory"] is True

    downloaded = client.get(f"/api/v1/backups/{backup_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"PK")
    assert b"master-key" not in downloaded.content
    assert b"recovery_passphrase" not in downloaded.content


def test_backup_recovery_rehearsal_decrypts_restored_text_and_media(client):
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    passphrase = "虚构备份恢复口令-长度足够-2026"
    try:
        family = client.post(
            "/api/v1/families",
            json={
                "display_name": "虚构加密备份家庭",
                "idempotency_key": "encrypted-backup-family",
            },
        ).json()
        profile = client.post(
            "/api/v1/elder-profiles",
            json={
                "family_id": family["id"],
                "display_name": "虚构备份讲述者",
                "preferred_name": "备份讲述者",
            },
        ).json()
        session = create_session(client, profile["id"])
        upload_test_audio(client, session["id"])
        client.post(
            f"/api/v1/families/{family['id']}/security/initialize",
            json={"actor_label": "虚构本机管理员"},
        )
        recovery = client.post(
            f"/api/v1/families/{family['id']}/security/recovery-package",
            json={
                "actor_label": "虚构本机管理员",
                "recovery_passphrase": passphrase,
            },
        )
        verified_recovery = client.post(
            f"/api/v1/families/{family['id']}/security/verify-recovery",
            data={
                "actor_label": "虚构本机管理员",
                "recovery_passphrase": passphrase,
            },
            files={"package": ("recovery.json", recovery.content, "application/json")},
        )
        assert verified_recovery.status_code == 200
        activated = client.post(
            f"/api/v1/families/{family['id']}/security/activate",
            json={
                "actor_label": "虚构本机管理员",
                "data_classification": "authorized_sensitive",
            },
        )
        assert activated.status_code == 200, activated.text

        backup = client.post(
            "/api/v1/backups", json={"actor_label": "虚构本机管理员"}
        )
        assert backup.status_code == 201, backup.text
        rehearsal = client.post(
            f"/api/v1/backups/{backup.json()['id']}/rehearse-recovery",
            data={
                "family_id": family["id"],
                "recovery_passphrase": passphrase,
            },
            files={"package": ("recovery.json", recovery.content, "application/json")},
        )
        assert rehearsal.status_code == 200, rehearsal.text
        summary = rehearsal.json()["verification_summary"]
        assert rehearsal.json()["status"] == "recovery_verified"
        assert summary["restored_to_new_directory"] is True
        assert summary["recovery_verified"] is True
        assert summary["decrypted_field_count"] > 0
        assert summary["decrypted_media_count"] == 1
    finally:
        app.dependency_overrides.pop(get_secret_store, None)
