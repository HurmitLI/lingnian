from __future__ import annotations

import io

from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Keepsake, MediaAsset
from app.services.keepsake import recover_interrupted_keepsakes
from test_api_flow import create_profile, upload_test_audio


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), color=(176, 142, 104)).save(buffer, format="PNG")
    return buffer.getvalue()


def create_archived_story(client, profile_id: str, *, id_suffix: str = "one"):
    session_response = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": profile_id,
            "life_stage": "童年",
            "trigger_kind": "photo",
        },
    )
    assert session_response.status_code == 201
    session = session_response.json()
    image = client.post(
        f"/api/v1/memory-sessions/{session['id']}/trigger-image",
        data={"trigger_kind": "photo", "user_annotation": "虚构测试照片"},
        files={"image": (f"memory-{id_suffix}.png", png_bytes(), "image/png")},
    )
    assert image.status_code == 201, image.text
    upload_test_audio(client, session["id"])
    transcription = client.post(
        f"/api/v1/memory-sessions/{session['id']}/transcription-tasks"
    )
    assert transcription.status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    corrected = f"这是第 {id_suffix} 段虚构的家庭回忆，只用于自动化测试。"
    assert client.patch(
        f"/api/v1/transcripts/{detail['transcript']['id']}",
        json={"corrected_text": corrected},
    ).status_code == 200
    assert client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks"
    ).status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    confirmed = client.post(
        f"/api/v1/story-drafts/{detail['story_draft']['id']}/confirm",
        json={"confirmed_by": "自动化测试家人"},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def authorization_payload(story_ids: list[str]) -> dict:
    return {
        "story_ids": story_ids,
        "actor_label": "自动化虚构测试家人",
        "original_voice_authorized": True,
        "private_family_use": True,
        "no_impersonation": True,
        "original_audio_only": True,
    }


def test_local_keepsake_real_ffmpeg_flow(client, db):
    profile = create_profile(client)
    story = create_archived_story(client, profile["id"])

    catalog = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/keepsake-catalog"
    )
    assert catalog.status_code == 200
    assert catalog.json()[0]["story_id"] == story["id"]
    assert catalog.json()[0]["has_original_audio"] is True
    assert catalog.json()[0]["image_asset_id"]

    incomplete = authorization_payload([story["id"]])
    incomplete["no_impersonation"] = False
    rejected = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/keepsake-authorizations",
        json=incomplete,
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "KEEPSAKE_AUTHORIZATION_INCOMPLETE"

    authorized = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/keepsake-authorizations",
        json=authorization_payload([story["id"]]),
    )
    assert authorized.status_code == 201, authorized.text
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/keepsakes",
        json={
            "authorization_id": authorized.json()["id"],
            "title": "虚构测试念想",
            "idempotency_key": "keepsake-real-ffmpeg",
        },
    )
    assert created.status_code == 201, created.text
    ready = client.get(f"/api/v1/keepsakes/{created.json()['id']}")
    assert ready.status_code == 200
    payload = ready.json()
    assert payload["status"] == "ready"
    assert payload["progress"] == 100
    assert payload["cost_cents"] == 0
    assert payload["source_mode"] == "original_audio_only"
    assert payload["duration_ms"] > 0
    assert payload["width"] == 1280
    assert payload["height"] == 720

    video = client.get(payload["content_url"])
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    assert b"ftyp" in video.content[:64]

    repeated = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/keepsakes",
        json={
            "authorization_id": authorized.json()["id"],
            "title": "虚构测试念想",
            "idempotency_key": "keepsake-real-ffmpeg",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == payload["id"]
    assert len(db.scalars(select(Keepsake)).all()) == 1

    stored = db.get(Keepsake, payload["id"])
    stored.status = "rendering"
    stored.progress = 58
    db.commit()
    assert recover_interrupted_keepsakes() == 1
    db.expire_all()
    interrupted = db.get(Keepsake, payload["id"])
    assert interrupted.status == "failed_retryable"
    assert interrupted.error_code == "PROCESS_INTERRUPTED"
    retried = client.post(f"/api/v1/keepsakes/{payload['id']}/retry")
    assert retried.status_code == 200
    after_retry = client.get(f"/api/v1/keepsakes/{payload['id']}").json()
    assert after_retry["status"] == "ready"
    assert after_retry["attempt"] == 2


def test_keepsake_authorization_is_bound_and_one_time(client):
    first_profile = create_profile(client)
    first_story = create_archived_story(client, first_profile["id"], id_suffix="first")
    second_family = client.post(
        "/api/v1/families",
        json={"display_name": "另一个虚构家庭", "idempotency_key": "second-family"},
    ).json()
    second_profile = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": second_family["id"],
            "display_name": "虚构讲述者二",
            "preferred_name": "讲述者二",
        },
    ).json()
    cross_family = client.post(
        f"/api/v1/elder-profiles/{second_profile['id']}/keepsake-authorizations",
        json=authorization_payload([first_story["id"]]),
    )
    assert cross_family.status_code == 409
    assert cross_family.json()["error"]["code"] == "STORY_NOT_CONFIRMED"

    authorized = client.post(
        f"/api/v1/elder-profiles/{first_profile['id']}/keepsake-authorizations",
        json=authorization_payload([first_story["id"]]),
    ).json()
    first = client.post(
        f"/api/v1/elder-profiles/{first_profile['id']}/keepsakes",
        json={
            "authorization_id": authorized["id"],
            "title": "第一版",
            "idempotency_key": "first-use",
        },
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/elder-profiles/{first_profile['id']}/keepsakes",
        json={
            "authorization_id": authorized["id"],
            "title": "不应该出现的第二版",
            "idempotency_key": "second-use",
        },
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "KEEPSAKE_AUTHORIZATION_USED"


def test_corrupted_keepsake_is_blocked(client, db):
    profile = create_profile(client)
    story = create_archived_story(client, profile["id"], id_suffix="corrupt")
    authorization = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/keepsake-authorizations",
        json=authorization_payload([story["id"]]),
    ).json()
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/keepsakes",
        json={
            "authorization_id": authorization["id"],
            "title": "完整性测试",
            "idempotency_key": "corrupt-video",
        },
    ).json()
    stored = db.get(Keepsake, created["id"])
    path = get_settings().resolved_asset_root / stored.relative_path
    path.write_bytes(path.read_bytes() + b"tampered")

    response = client.get(f"/api/v1/keepsakes/{stored.id}/content")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KEEPSAKE_CORRUPT"
    db.expire_all()
    assert db.get(Keepsake, stored.id).status == "corrupt"


def test_catalog_marks_story_without_audio_unavailable(client):
    profile = create_profile(client)
    story = create_archived_story(client, profile["id"], id_suffix="missing-audio")
    from app.core.database import SessionLocal

    with SessionLocal() as session:
        source = session.scalar(
            select(MediaAsset).where(MediaAsset.kind == "audio_original")
        )
        source.status = "corrupt"
        session.commit()
    catalog = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/keepsake-catalog"
    ).json()
    item = next(value for value in catalog if value["story_id"] == story["id"])
    assert item["has_original_audio"] is False
    assert item["unavailable_reason"]
