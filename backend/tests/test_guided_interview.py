from __future__ import annotations

import io
import wave

from sqlalchemy import select

from app.main import app
from app.models import EncryptedField, InterviewTurn, MediaAsset
from app.services.security import InMemorySecretStore, get_secret_store


def wav_bytes(seconds: float = 0.15) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


def create_subject_and_narrator(client):
    family = client.post(
        "/api/v1/families",
        json={"display_name": "测试家庭", "idempotency_key": "guided-family"},
    ).json()
    subject = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": family["id"],
            "display_name": "外公",
            "preferred_name": "外公",
        },
    ).json()
    narrator = client.post(
        f"/api/v1/families/{family['id']}/people",
        json={"display_name": "妈妈", "role": "family_member"},
    ).json()
    return subject, narrator


def test_guided_interview_keeps_subject_and_narrator_separate(client):
    subject, narrator = create_subject_and_narrator(client)
    created = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": narrator["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["status"] == "INTERVIEWING"
    assert session["narrator_person_id"] == narrator["id"]

    uploaded = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/audio",
        files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
    )
    assert uploaded.status_code == 201, uploaded.text
    turn = uploaded.json()
    assert turn["status"] == "answer_review"
    assert turn["raw_answer_text"]
    assert turn["audio_url"].startswith("/api/v1/media-assets/")

    continued = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/{turn['id']}/continue",
        json={
            "corrected_answer_text": "这是妈妈根据亲身经历讲述的一段测试回忆。",
            "allow_cloud_followup": False,
        },
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["next_question"]
    assert continued.json()["followup_mode"] == "test_or_local_model"

    finalized = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-finalize"
    )
    assert finalized.status_code == 200, finalized.text
    detail = finalized.json()
    assert detail["session"]["status"] == "TRANSCRIPT_REVIEW"
    assert "妈妈：这是妈妈根据亲身经历讲述的一段测试回忆。" in detail["transcript"]["corrected_text"]
    assert any(asset["kind"] == "audio_original" for asset in detail["media_assets"])


def test_family_recollection_keeps_narrator_provenance_in_archive_and_book(client):
    subject, narrator = create_subject_and_narrator(client)
    session = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": narrator["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    ).json()
    uploaded = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/audio",
        files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
    ).json()
    answer = "外公小时候常在村口的大树下等家人回家。"
    assert client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/{uploaded['id']}/continue",
        json={"corrected_answer_text": answer, "allow_cloud_followup": False},
    ).status_code == 200
    assert client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-finalize"
    ).status_code == 200
    organized = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks",
        json={},
    )
    assert organized.status_code == 202, organized.text
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    draft_id = detail["story_draft"]["id"]
    confirmed = client.post(
        f"/api/v1/story-drafts/{draft_id}/confirm",
        json={"confirmed_by": "妈妈"},
    )
    assert confirmed.status_code == 200, confirmed.text

    archive = client.post(
        f"/api/v1/elder-profiles/{subject['id']}/archive-questions",
        json={"question": "外公小时候在哪里等家人？"},
    )
    assert archive.status_code == 200, archive.text
    citation = archive.json()["citations"][0]
    assert citation["source_kind"] == "family_recollection"
    assert citation["source_label"] == "妈妈"

    book = client.post(
        f"/api/v1/elder-profiles/{subject['id']}/memory-books",
        json={"created_by": "测试家庭成员"},
    )
    assert book.status_code == 201, book.text
    assert book.json()["story_manifest"][0]["narrator_label"] == "妈妈"
    markdown = client.get(f"/api/v1/memory-books/{book.json()['id']}/markdown")
    assert "记忆人物：外公" in markdown.text
    assert "讲述来源：妈妈回忆讲述" in markdown.text


def test_guided_interview_rejects_narrator_from_another_family(client):
    subject, _ = create_subject_and_narrator(client)
    other_family = client.post(
        "/api/v1/families",
        json={"display_name": "另一家庭", "idempotency_key": "other-family"},
    ).json()
    outsider = client.post(
        f"/api/v1/families/{other_family['id']}/people",
        json={"display_name": "外部人员", "role": "family_member"},
    ).json()
    response = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": outsider["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NARRATOR_FAMILY_MISMATCH"


def test_guided_interview_remains_encrypted_for_real_family_data(client, db):
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    passphrase = "虚构采访恢复口令-长度足够-2026"
    try:
        subject, narrator = create_subject_and_narrator(client)
        family_id = subject["family_id"]
        assert client.post(
            f"/api/v1/families/{family_id}/security/initialize",
            json={"actor_label": "测试家庭成员"},
        ).status_code == 201
        package = client.post(
            f"/api/v1/families/{family_id}/security/recovery-package",
            json={
                "actor_label": "测试家庭成员",
                "recovery_passphrase": passphrase,
            },
        )
        assert package.status_code == 200
        verified = client.post(
            f"/api/v1/families/{family_id}/security/verify-recovery",
            data={"recovery_passphrase": passphrase, "actor_label": "测试家庭成员"},
            files={"package": ("recovery.json", package.content, "application/json")},
        )
        assert verified.status_code == 200
        activated = client.post(
            f"/api/v1/families/{family_id}/security/activate",
            json={
                "actor_label": "测试家庭成员",
                "data_classification": "authorized_sensitive",
            },
        )
        assert activated.status_code == 200, activated.text

        session = client.post(
            "/api/v1/memory-sessions",
            json={
                "elder_id": subject["id"],
                "narrator_person_id": narrator["id"],
                "interview_mode": "guided_voice",
                "life_stage": "童年",
            },
        ).json()
        uploaded = client.post(
            f"/api/v1/memory-sessions/{session['id']}/interview-turns/audio",
            files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
        )
        assert uploaded.status_code == 201, uploaded.text
        turn_payload = uploaded.json()
        continued = client.post(
            f"/api/v1/memory-sessions/{session['id']}/interview-turns/{turn_payload['id']}/continue",
            json={
                "corrected_answer_text": "这是一段只保存在本机的加密采访回答。",
                "allow_cloud_followup": False,
            },
        )
        assert continued.status_code == 200, continued.text
        finalized = client.post(
            f"/api/v1/memory-sessions/{session['id']}/interview-finalize"
        )
        assert finalized.status_code == 200, finalized.text
        assert "这是一段只保存在本机的加密采访回答。" in finalized.json()["transcript"]["corrected_text"]

        db.expire_all()
        turn = db.get(InterviewTurn, turn_payload["id"])
        assert turn.corrected_answer_text == "[niannian:encrypted:v1]"
        assert db.scalars(
            select(EncryptedField).where(EncryptedField.object_id == turn.id)
        ).all()
        turn_audio = db.get(MediaAsset, turn.audio_asset_id)
        assert turn_audio.encryption_version == 1
        merged_audio = db.scalar(
            select(MediaAsset).where(
                MediaAsset.session_id == session["id"],
                MediaAsset.kind == "audio_original",
            )
        )
        assert merged_audio is not None
        assert merged_audio.encryption_version == 1
    finally:
        app.dependency_overrides.pop(get_secret_store, None)
