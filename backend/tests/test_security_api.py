from __future__ import annotations

from sqlalchemy import select

from app.main import app
from app.core.config import get_settings
from app.models import (
    ConsentEvent,
    ElderProfile,
    EncryptedField,
    FamilyArchive,
    MediaAsset,
    MemoryBook,
    Person,
    Story,
    StoryDraft,
    Transcript,
)
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


def test_verified_recovery_activates_real_encryption_and_preserves_api_reads(client, db):
    from test_api_flow import wav_bytes

    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    passphrase = "虚构恢复口令-长度足够-2026"
    try:
        family = create_family(client)
        profile = client.post(
            "/api/v1/elder-profiles",
            json={
                "family_id": family["id"],
                "display_name": "机密测试姓名",
                "preferred_name": "机密称呼",
                "birth_year": 1948,
                "native_place": "机密测试地点",
            },
        ).json()
        session = client.post(
            "/api/v1/memory-sessions",
            json={"elder_id": profile["id"], "life_stage": "童年"},
        ).json()
        original_audio = wav_bytes()
        uploaded = client.post(
            f"/api/v1/memory-sessions/{session['id']}/audio",
            files={"audio": ("机密录音.wav", original_audio, "audio/wav")},
        ).json()
        initialized = client.post(
            f"/api/v1/families/{family['id']}/security/initialize",
            json={"actor_label": "测试家庭成员"},
        )
        assert initialized.status_code == 201
        recovery = client.post(
            f"/api/v1/families/{family['id']}/security/recovery-package",
            json={
                "actor_label": "测试家庭成员",
                "recovery_passphrase": passphrase,
            },
        )
        assert recovery.status_code == 200

        blocked = client.post(
            f"/api/v1/families/{family['id']}/security/activate",
            json={
                "actor_label": "测试家庭成员",
                "data_classification": "authorized_sensitive",
            },
        )
        assert blocked.status_code == 409

        verified = client.post(
            f"/api/v1/families/{family['id']}/security/verify-recovery",
            data={
                "recovery_passphrase": passphrase,
                "actor_label": "测试家庭成员",
            },
            files={"package": ("recovery.json", recovery.content, "application/json")},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["recovery_verified_at"] is not None

        activated = client.post(
            f"/api/v1/families/{family['id']}/security/activate",
            json={
                "actor_label": "测试家庭成员",
                "data_classification": "authorized_sensitive",
            },
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["encryption_status"] == "active_encrypted"
        assert activated.json()["activated_at"] is not None

        repeated_family = create_family(client)
        assert repeated_family["id"] == family["id"]
        assert repeated_family["display_name"] == "虚构安全测试家庭"

        db.expire_all()
        raw_family = db.get(FamilyArchive, family["id"])
        raw_profile = db.get(ElderProfile, profile["id"])
        raw_person = db.get(Person, raw_profile.person_id)
        raw_asset = db.get(MediaAsset, uploaded["id"])
        assert raw_family.display_name == "[niannian:encrypted:v1]"
        assert raw_person.display_name == "[niannian:encrypted:v1]"
        assert raw_profile.preferred_name == "[niannian:encrypted:v1]"
        assert raw_profile.birth_year is None
        assert raw_asset.original_filename == "[niannian:encrypted:v1]"
        assert raw_asset.encryption_version == 1
        assert db.scalars(
            select(EncryptedField).where(EncryptedField.family_id == family["id"])
        ).all()
        stored_path = get_settings().resolved_asset_root / raw_asset.relative_path
        assert stored_path.read_bytes().startswith(b"NNMEDIA1")
        assert not stored_path.read_bytes().startswith(b"RIFF")

        readable_profile = client.get(f"/api/v1/elder-profiles/{profile['id']}")
        assert readable_profile.status_code == 200, readable_profile.text
        assert readable_profile.json()["display_name"] == "机密测试姓名"
        assert readable_profile.json()["preferred_name"] == "机密称呼"
        assert readable_profile.json()["birth_year"] == 1948
        readable_audio = client.get(f"/api/v1/media-assets/{uploaded['id']}/content")
        assert readable_audio.status_code == 200
        assert readable_audio.content == original_audio

        transcription_task = client.post(
            f"/api/v1/memory-sessions/{session['id']}/transcription-tasks"
        )
        assert transcription_task.status_code == 202, transcription_task.text
        detail = client.get(f"/api/v1/memory-sessions/{session['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["transcript"]["raw_text"]
        transcript_id = detail.json()["transcript"]["id"]
        db.expire_all()
        assert db.get(Transcript, transcript_id).raw_text == "[niannian:encrypted:v1]"

        corrected_text = "这是启用加密后的虚构家庭故事。"
        corrected = client.patch(
            f"/api/v1/transcripts/{transcript_id}",
            json={"corrected_text": corrected_text},
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["corrected_text"] == corrected_text

        organization = client.post(
            f"/api/v1/memory-sessions/{session['id']}/organization-tasks"
        )
        assert organization.status_code == 202, organization.text
        detail = client.get(f"/api/v1/memory-sessions/{session['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["story_draft"]["body"] == corrected_text
        draft_id = detail.json()["story_draft"]["id"]
        db.expire_all()
        assert db.get(StoryDraft, draft_id).body == "[niannian:encrypted:v1]"

        confirmed = client.post(
            f"/api/v1/story-drafts/{draft_id}/confirm",
            json={"confirmed_by": "虚构测试子女"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["body"] == corrected_text
        story_id = confirmed.json()["id"]
        db.expire_all()
        assert db.get(Story, story_id).body == "[niannian:encrypted:v1]"
        timeline = client.get(f"/api/v1/elder-profiles/{profile['id']}/timeline")
        assert timeline.status_code == 200, timeline.text
        assert timeline.json()[0]["story"]["body"] == corrected_text

        book = client.post(
            f"/api/v1/elder-profiles/{profile['id']}/memory-books",
            json={"created_by": "虚构测试子女"},
        )
        assert book.status_code == 201, book.text
        book_id = book.json()["id"]
        markdown = client.get(f"/api/v1/memory-books/{book_id}/markdown")
        assert markdown.status_code == 200
        assert corrected_text in markdown.text
        pdf = client.get(f"/api/v1/memory-books/{book_id}/pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.content.startswith(b"%PDF")
        db.expire_all()
        raw_book = db.get(MemoryBook, book_id)
        assert raw_book.markdown_content == "[niannian:encrypted:v1]"
        assert raw_book.pdf_encryption_version == 1
        pdf_path = get_settings().resolved_asset_root / raw_book.pdf_relative_path
        assert pdf_path.read_bytes().startswith(b"NNMEDIA1")

        second = client.post(
            "/api/v1/elder-profiles",
            json={
                "family_id": family["id"],
                "display_name": "启用后新建姓名",
                "preferred_name": "启用后称呼",
            },
        )
        assert second.status_code == 201, second.text
        assert second.json()["display_name"] == "启用后新建姓名"
        db.expire_all()
        second_profile = db.get(ElderProfile, second.json()["id"])
        assert db.get(Person, second_profile.person_id).display_name == "[niannian:encrypted:v1]"

        disposable_session = client.post(
            "/api/v1/memory-sessions",
            json={"elder_id": profile["id"], "life_stage": "青年"},
        ).json()
        disposable_asset = client.post(
            f"/api/v1/memory-sessions/{disposable_session['id']}/audio",
            files={"audio": ("待清除录音.wav", wav_bytes(), "audio/wav")},
        ).json()
        assert client.post(
            f"/api/v1/memory-sessions/{disposable_session['id']}/transcription-tasks"
        ).status_code == 202
        disposable_detail = client.get(
            f"/api/v1/memory-sessions/{disposable_session['id']}"
        ).json()
        disposable_transcript_id = disposable_detail["transcript"]["id"]
        assert client.patch(
            f"/api/v1/transcripts/{disposable_transcript_id}",
            json={"corrected_text": "这段虚构文字应随跳过一起清除。"},
        ).status_code == 200
        assert client.post(
            f"/api/v1/memory-sessions/{disposable_session['id']}/organization-tasks"
        ).status_code == 202
        disposable_detail = client.get(
            f"/api/v1/memory-sessions/{disposable_session['id']}"
        ).json()
        disposable_draft_id = disposable_detail["story_draft"]["id"]
        disposable_object_ids = [
            disposable_asset["id"],
            disposable_transcript_id,
            disposable_draft_id,
        ]
        db.expire_all()
        assert db.scalars(
            select(EncryptedField).where(
                EncryptedField.object_id.in_(disposable_object_ids)
            )
        ).all()
        assert client.post(
            f"/api/v1/memory-sessions/{disposable_session['id']}/skip"
        ).status_code == 200
        db.expire_all()
        assert not db.scalars(
            select(EncryptedField).where(
                EncryptedField.object_id.in_(disposable_object_ids)
            )
        ).all()

        from test_media_triggers import png_bytes

        photo_session = client.post(
            "/api/v1/memory-sessions",
            json={
                "elder_id": profile["id"],
                "life_stage": "照片",
                "trigger_kind": "photo",
            },
        ).json()
        photo = client.post(
            f"/api/v1/memory-sessions/{photo_session['id']}/trigger-image",
            data={"trigger_kind": "photo", "user_annotation": "待清除虚构标注"},
            files={"image": ("待清除照片.png", png_bytes(), "image/png")},
        ).json()
        db.expire_all()
        photo_asset = db.get(MediaAsset, photo["media_asset_id"])
        photo_object_ids = [photo_asset.id, *[link.id for link in photo_asset.links]]
        assert db.scalars(
            select(EncryptedField).where(EncryptedField.object_id.in_(photo_object_ids))
        ).all()
        assert client.delete(f"/api/v1/media-assets/{photo_asset.id}").status_code == 204
        db.expire_all()
        assert not db.scalars(
            select(EncryptedField).where(EncryptedField.object_id.in_(photo_object_ids))
        ).all()

        raw_dump = "\n".join(
            db.connection().connection.driver_connection.iterdump()
        )
        for plaintext in [
            "机密测试姓名",
            "机密称呼",
            "机密测试地点",
            "机密录音.wav",
            corrected_text,
            "虚构测试子女",
            "启用后新建姓名",
            "启用后称呼",
            "待清除录音.wav",
            "这段虚构文字应随跳过一起清除。",
            "待清除虚构标注",
            "待清除照片.png",
        ]:
            assert plaintext not in raw_dump
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
