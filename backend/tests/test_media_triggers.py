from __future__ import annotations

import io

from PIL import Image

from test_api_flow import create_profile


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), color=(222, 216, 202)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_photo_trigger_uses_fixed_non_inference_question_and_links_image(client, db):
    profile = create_profile(client)
    session = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": profile["id"],
            "life_stage": "照片",
            "trigger_kind": "photo",
        },
    )
    assert session.status_code == 201
    assert session.json()["prompt_id"] == "photo-fixed-v1"
    assert session.json()["question_text"] == "这张照片让您想起什么？"

    uploaded = client.post(
        f"/api/v1/memory-sessions/{session.json()['id']}/trigger-image",
        data={"trigger_kind": "photo", "user_annotation": "用户明确标注：虚构院子"},
        files={"image": ("yard.png", png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    payload = uploaded.json()
    assert payload["trigger_kind"] == "photo"
    assert payload["user_annotation"] == "用户明确标注：虚构院子"
    assert payload["model_inference"] is None
    assert payload["width"] == 320
    assert payload["height"] == 240

    content = client.get(f"/api/v1/media-assets/{payload['media_asset_id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/png"

    deleted = client.delete(f"/api/v1/media-assets/{payload['media_asset_id']}")
    assert deleted.status_code == 204
    missing = client.get(f"/api/v1/media-assets/{payload['media_asset_id']}/content")
    assert missing.status_code == 404


def test_trigger_image_rejects_mismatched_or_fake_content(client):
    profile = create_profile(client)
    normal_session = client.post(
        "/api/v1/memory-sessions",
        json={"elder_id": profile["id"], "life_stage": "童年"},
    ).json()
    mismatch = client.post(
        f"/api/v1/memory-sessions/{normal_session['id']}/trigger-image",
        data={"trigger_kind": "photo"},
        files={"image": ("yard.png", png_bytes(), "image/png")},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "TRIGGER_SESSION_MISMATCH"

    trigger_session = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": profile["id"],
            "life_stage": "老物件",
            "trigger_kind": "old_object",
        },
    ).json()
    fake = client.post(
        f"/api/v1/memory-sessions/{trigger_session['id']}/trigger-image",
        data={"trigger_kind": "old_object"},
        files={"image": ("object.png", b"not an image", "image/png")},
    )
    assert fake.status_code == 415
    assert fake.json()["error"]["code"] == "IMAGE_CONTENT_MISMATCH"


def test_old_object_trigger_respects_ask_first_preference(client):
    profile = create_profile(client)
    preference = client.put(
        f"/api/v1/elder-profiles/{profile['id']}/topic-preferences/老物件",
        json={
            "topic_key": "老物件",
            "preference": "ask_first",
            "updated_by": "family_tester",
        },
    )
    assert preference.status_code == 200

    blocked = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": profile["id"],
            "life_stage": "老物件",
            "trigger_kind": "old_object",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "TOPIC_CONFIRMATION_REQUIRED"

    allowed = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": profile["id"],
            "life_stage": "老物件",
            "trigger_kind": "old_object",
            "topic_confirmed": True,
        },
    )
    assert allowed.status_code == 201
    assert allowed.json()["question_text"] == "这件老物件让您想起什么？"
