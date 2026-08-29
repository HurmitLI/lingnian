from __future__ import annotations

import io
import wave

from sqlalchemy import select

from app.core.config import get_settings
from app.models import ConsentEvent, MediaAsset, Story, WorkflowTask


def wav_bytes(seconds: float = 0.15) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


def create_profile(client):
    family_response = client.post(
        "/api/v1/families",
        json={"display_name": "虚构的林家", "idempotency_key": "test-family"},
    )
    assert family_response.status_code == 201
    family_id = family_response.json()["id"]
    profile_response = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": family_id,
            "display_name": "林奶奶（虚构）",
            "preferred_name": "林奶奶",
            "birth_year": 1948,
            "native_place": "测试地点",
        },
    )
    assert profile_response.status_code == 201
    return profile_response.json()


def create_session(client, elder_id: str):
    response = client.post(
        "/api/v1/memory-sessions",
        json={"elder_id": elder_id, "life_stage": "童年"},
    )
    assert response.status_code == 201
    return response.json()


def upload_test_audio(client, session_id: str):
    response = client.post(
        f"/api/v1/memory-sessions/{session_id}/audio",
        files={"audio": ("memory.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_complete_vertical_slice(client, db):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    asset = upload_test_audio(client, session["id"])
    assert len(asset["sha256"]) == 64
    assert asset["is_original"] is True

    task_response = client.post(
        f"/api/v1/memory-sessions/{session['id']}/transcription-tasks"
    )
    assert task_response.status_code == 202
    task = client.get(f"/api/v1/tasks/{task_response.json()['id']}").json()
    assert task["status"] == "succeeded"

    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    assert detail["session"]["status"] == "TRANSCRIPT_REVIEW"
    assert detail["transcript"]["raw_text"]
    transcript_id = detail["transcript"]["id"]
    raw_text = detail["transcript"]["raw_text"]

    corrected = "1978 年那阵子，我常跟着家里人去河边。这是虚构测试内容。"
    updated = client.patch(
        f"/api/v1/transcripts/{transcript_id}", json={"corrected_text": corrected}
    )
    assert updated.status_code == 200
    assert updated.json()["raw_text"] == raw_text
    assert updated.json()["corrected_text"] == corrected
    assert updated.json()["version"] == 2

    organization_response = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks"
    )
    assert organization_response.status_code == 202
    organization_task = client.get(
        f"/api/v1/tasks/{organization_response.json()['id']}"
    ).json()
    assert organization_task["status"] == "succeeded"

    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    assert detail["session"]["status"] == "DRAFT_REVIEW"
    assert detail["story_draft"]["body"] == corrected
    draft_id = detail["story_draft"]["id"]

    reject = client.post(f"/api/v1/story-drafts/{draft_id}/reject")
    assert reject.status_code == 200
    assert client.get(f"/api/v1/memory-sessions/{session['id']}").json()["session"][
        "status"
    ] == "TRANSCRIPT_REVIEW"

    second_organization = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks"
    )
    assert second_organization.status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    draft_id = detail["story_draft"]["id"]
    confirm = client.post(
        f"/api/v1/story-drafts/{draft_id}/confirm",
        json={"confirmed_by": "测试子女"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["body"] == corrected

    repeated = client.post(
        f"/api/v1/story-drafts/{draft_id}/confirm",
        json={"confirmed_by": "测试子女"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == confirm.json()["id"]
    assert len(db.scalars(select(Story)).all()) == 1

    timeline = client.get(f"/api/v1/elder-profiles/{profile['id']}/timeline")
    assert timeline.status_code == 200
    assert len(timeline.json()) == 1
    assert timeline.json()[0]["audio_url"].startswith("/api/v1/media-assets/")


def test_skip_removes_unarchived_content(client, db):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    asset = upload_test_audio(client, session["id"])
    stored = db.get(MediaAsset, asset["id"])
    path = get_settings().resolved_asset_root / stored.relative_path
    assert path.exists()

    response = client.post(f"/api/v1/memory-sessions/{session['id']}/skip")
    assert response.status_code == 200
    assert response.json()["status"] == "SKIPPED"
    db.expire_all()
    assert db.get(MediaAsset, asset["id"]) is None
    assert not path.exists()
    event = db.scalar(select(ConsentEvent).where(ConsentEvent.object_id == session["id"]))
    assert event.action == "skip"


def test_invalid_audio_has_safe_error(client):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    response = client.post(
        f"/api/v1/memory-sessions/{session['id']}/audio",
        files={"audio": ("fake.wav", b"not a wav", "audio/wav")},
    )
    assert response.status_code == 415
    payload = response.json()
    assert payload["error"]["code"] == "AUDIO_CONTENT_MISMATCH"
    assert "Traceback" not in response.text


def test_corrupted_audio_is_blocked_on_read(client, db):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    asset = upload_test_audio(client, session["id"])
    stored = db.get(MediaAsset, asset["id"])
    path = get_settings().resolved_asset_root / stored.relative_path
    path.write_bytes(path.read_bytes() + b"tampered")

    response = client.get(f"/api/v1/media-assets/{asset['id']}/content")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSET_INTEGRITY_FAILED"
    db.expire_all()
    assert db.get(MediaAsset, asset["id"]).status == "corrupt"


def test_family_idempotency_and_validation(client):
    payload = {"display_name": "虚构家庭", "idempotency_key": "same-request"}
    first = client.post("/api/v1/families", json=payload)
    second = client.post("/api/v1/families", json=payload)
    assert first.json()["id"] == second.json()["id"]

    invalid = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": first.json()["id"],
            "display_name": "测试",
            "preferred_name": "测试",
            "birth_year": 1800,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_running_tasks_are_recovered(client, db):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    task = WorkflowTask(
        session_id=session["id"],
        task_type="transcription",
        status="running",
        progress=30,
        attempt=1,
    )
    db.add(task)
    db.commit()
    task_id = task.id

    from app.core.database import initialize_database

    initialize_database()
    db.expire_all()
    recovered = db.get(WorkflowTask, task_id)
    assert recovered.status == "failed_retryable"
    assert recovered.error_code == "PROCESS_INTERRUPTED"
