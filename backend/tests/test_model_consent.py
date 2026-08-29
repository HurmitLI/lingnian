from __future__ import annotations

import io
import wave

from app.core.config import get_settings
from app.models import ElderProfile, FamilyArchive, ModelConsentEvent
from app.services.llm.provider import MockLLMProvider
from app.services.privacy import requires_explicit_model_consent
from app.services.workflow import tasks


class FakeQwenProvider(MockLLMProvider):
    provider_name = "qwen"
    model_name = "fake-qwen-for-tests"

    def __init__(self) -> None:
        self.organization_calls = 0

    def organize_story(self, corrected_text: str, question: str):
        self.organization_calls += 1
        return super().organize_story(corrected_text, question)


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 2400)
    return buffer.getvalue()


def prepare_reviewed_real_session(client, db, monkeypatch):
    family = client.post(
        "/api/v1/families",
        json={"display_name": "授权真实资料家庭", "idempotency_key": "real-consent"},
    ).json()
    profile = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": family["id"],
            "display_name": "受访者",
            "preferred_name": "长辈",
        },
    ).json()
    stored_family = db.get(FamilyArchive, family["id"])
    stored_family.data_classification = "authorized_sensitive"
    db.commit()

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "qwen")
    session_response = client.post(
        "/api/v1/memory-sessions",
        json={"elder_id": profile["id"], "life_stage": "童年"},
    )
    assert session_response.status_code == 201
    session = session_response.json()

    uploaded = client.post(
        f"/api/v1/memory-sessions/{session['id']}/audio",
        files={"audio": ("memory.wav", wav_bytes(), "audio/wav")},
    )
    assert uploaded.status_code == 201
    transcribed = client.post(
        f"/api/v1/memory-sessions/{session['id']}/transcription-tasks"
    )
    assert transcribed.status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    updated = client.patch(
        f"/api/v1/transcripts/{detail['transcript']['id']}",
        json={"corrected_text": "这是已经由家人校对过的真实回忆文本。"},
    )
    assert updated.status_code == 200
    return family, profile, session, updated.json()


def test_real_family_question_is_local_and_cloud_requires_one_time_consent(
    client, db, monkeypatch
):
    provider = FakeQwenProvider()
    monkeypatch.setattr(tasks, "get_llm_provider", lambda: provider)
    family, _, session, _ = prepare_reviewed_real_session(client, db, monkeypatch)

    blocked = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks", json={}
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MODEL_CONSENT_REQUIRED"
    assert provider.organization_calls == 0

    granted = client.post(
        f"/api/v1/memory-sessions/{session['id']}/model-consents",
        json={"actor_label": "家庭资料管理员"},
    )
    assert granted.status_code == 201
    consent_id = granted.json()["id"]

    submitted = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks",
        json={"consent_event_id": consent_id},
    )
    assert submitted.status_code == 202
    assert submitted.json()["model_consent_event_id"] == consent_id
    task = client.get(f"/api/v1/tasks/{submitted.json()['id']}").json()
    assert task["status"] == "succeeded"
    assert provider.organization_calls == 1
    db.expire_all()
    assert db.get(ModelConsentEvent, consent_id).used_at is not None

    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    client.post(f"/api/v1/story-drafts/{detail['story_draft']['id']}/reject")
    reused = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks",
        json={"consent_event_id": consent_id},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "MODEL_CONSENT_ALREADY_USED"
    assert provider.organization_calls == 1

    stored_family = db.get(FamilyArchive, family["id"])
    assert stored_family.data_classification == "authorized_sensitive"


def test_editing_transcript_revokes_unused_cloud_consent(client, db, monkeypatch):
    monkeypatch.setattr(tasks, "get_llm_provider", lambda: FakeQwenProvider())
    _, _, session, transcript = prepare_reviewed_real_session(client, db, monkeypatch)
    granted = client.post(
        f"/api/v1/memory-sessions/{session['id']}/model-consents",
        json={"actor_label": "家庭资料管理员"},
    ).json()

    changed = client.patch(
        f"/api/v1/transcripts/{transcript['id']}",
        json={"corrected_text": "这段校对稿后来又被家人修改了。"},
    )
    assert changed.status_code == 200
    db.expire_all()
    assert db.get(ModelConsentEvent, granted["id"]).revoked_at is not None

    blocked = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks",
        json={"consent_event_id": granted["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MODEL_CONSENT_NOT_GRANTED"


def test_real_data_family_cannot_be_created_before_encryption(client):
    response = client.post(
        "/api/v1/families",
        json={
            "display_name": "尚未加密的真实家庭",
            "data_classification": "authorized_sensitive",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REAL_DATA_MODE_LOCKED"


def test_future_non_local_provider_fails_closed_for_real_data():
    assert requires_explicit_model_consent("future-cloud", "authorized_sensitive")
    assert not requires_explicit_model_consent("mock", "authorized_sensitive")
    assert not requires_explicit_model_consent("qwen", "test")
