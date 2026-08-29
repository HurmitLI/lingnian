from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import MemoryBook
from test_api_flow import create_profile, create_session, upload_test_audio


def confirm_test_story(client, profile: dict, body: str = "一段已确认的虚构家庭故事。"):
    session = create_session(client, profile["id"])
    upload_test_audio(client, session["id"])
    client.post(f"/api/v1/memory-sessions/{session['id']}/transcription-tasks")
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    client.patch(
        f"/api/v1/transcripts/{detail['transcript']['id']}",
        json={"corrected_text": body},
    )
    client.post(f"/api/v1/memory-sessions/{session['id']}/organization-tasks")
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    response = client.post(
        f"/api/v1/story-drafts/{detail['story_draft']['id']}/confirm",
        json={"confirmed_by": "测试家庭成员"},
    )
    assert response.status_code == 200
    return response.json()


def test_local_reminder_is_idempotent_shown_once_and_respects_avoid(client):
    profile = create_profile(client)
    due_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    payload = {
        "topic_key": "童年",
        "remind_at": due_at,
        "idempotency_key": "local-reminder-1",
    }
    first = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/reminders", json=payload
    )
    repeated = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/reminders", json=payload
    )
    assert first.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]

    due = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/reminders/due"
    ).json()
    assert [item["id"] for item in due] == [first.json()["id"]]
    shown = client.post(f"/api/v1/reminders/{first.json()['id']}/shown")
    assert shown.json()["status"] == "shown_once"
    assert shown.json()["show_count"] == 1
    shown_again = client.post(f"/api/v1/reminders/{first.json()['id']}/shown")
    assert shown_again.json()["show_count"] == 1
    assert client.get(
        f"/api/v1/elder-profiles/{profile['id']}/reminders/due"
    ).json() == []

    future = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/reminders",
        json={
            "topic_key": "工作",
            "remind_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "idempotency_key": "local-reminder-2",
        },
    )
    assert future.status_code == 201
    client.put(
        f"/api/v1/elder-profiles/{profile['id']}/topic-preferences/工作",
        json={
            "topic_key": "工作",
            "preference": "avoid",
            "updated_by": "测试家庭成员",
        },
    )
    reminders = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/reminders"
    ).json()
    paused = next(item for item in reminders if item["id"] == future.json()["id"])
    assert paused["status"] == "paused_by_preference"
    blocked = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/reminders",
        json={
            "topic_key": "工作",
            "remind_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "idempotency_key": "local-reminder-3",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "REMINDER_BLOCKED_BY_PREFERENCE"


def test_memory_book_uses_only_confirmed_stories_and_verifies_hash(client, db):
    profile = create_profile(client)
    empty = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/memory-books",
        json={"created_by": "测试家庭成员"},
    )
    assert empty.status_code == 409
    assert empty.json()["error"]["code"] == "NO_CONFIRMED_STORIES"

    story = confirm_test_story(client, profile, "这是一段会进入回忆录的已确认虚构故事。")
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/memory-books",
        json={"title": "测试家庭回忆录", "created_by": "测试家庭成员"},
    )
    assert created.status_code == 201
    assert created.json()["version"] == 1
    assert created.json()["story_manifest"][0]["story_id"] == story["id"]

    downloaded = client.get(
        f"/api/v1/memory-books/{created.json()['id']}/markdown"
    )
    assert downloaded.status_code == 200
    assert "# 测试家庭回忆录" in downloaded.text
    assert "这是一段会进入回忆录的已确认虚构故事。" in downloaded.text
    assert story["id"] in downloaded.text

    stored = db.get(MemoryBook, created.json()["id"])
    stored.markdown_content += "被篡改"
    db.commit()
    blocked = client.get(
        f"/api/v1/memory-books/{created.json()['id']}/markdown"
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MEMORY_BOOK_INTEGRITY_FAILED"
