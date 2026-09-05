import pytest
from sqlalchemy import select

from app.api.routes import trash_generative_media_request
from app.core.errors import DomainError
from app.models import GenerativeMediaRequest, MediaAsset
from app.services.auth import AuthContext, current_auth
from test_api_flow import create_profile
from test_memory_experience import create_confirmed_story


def seed_result(client, db):
    profile = create_profile(client)
    story, photo_id = create_confirmed_story(client, profile, with_photo=True)
    result = client.post(f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests", json={
        "story_id": story["id"], "generation_type": "scene_video", "actor_label": "测试家人",
        "subject_consent": True, "rights_confirmed": True, "no_impersonation": True,
        "allow_external_upload": False, "max_cost_cents": 100, "target_duration_seconds": 45,
    })
    assert result.status_code == 201, result.text
    request_id = result.json()["id"]
    uploaded = client.post(f"/api/v1/generative-media-requests/{request_id}/result", data={"provider_key": "test", "actual_cost_cents": "0"}, files={"video": ("scene.mp4", b"\x00\x00\x00\x18ftypisom" + b"moov" + b"scene" + b"mdat" + b"frames", "video/mp4")})
    assert uploaded.status_code == 200, uploaded.text
    return profile, story, photo_id, uploaded.json()


def test_delete_is_recoverable_and_preserves_sources(client, db):
    profile, story, photo_id, result = seed_result(client, db)
    base = f"/api/v1/generative-media-requests/{result['id']}"
    listing = f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests"
    ids_before = set(db.scalars(select(MediaAsset.id)))
    assert client.delete(base).status_code == 204
    assert client.delete(base).status_code == 204
    assert client.get(listing).json() == []
    trashed = client.get(listing + "?trash_only=true").json()
    assert len(trashed) == 1 and trashed[0]["status"] == "deleted"
    assert trashed[0]["result_asset_id"] == result["result_asset_id"]
    assert set(db.scalars(select(MediaAsset.id))) == ids_before
    assert photo_id in ids_before
    assert client.get(result["result_content_url"]).status_code == 200
    restored = client.post(base + "/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "pending_human_review"
    assert client.get(listing + "?trash_only=true").json() == []
    assert client.get(listing).json()[0]["id"] == result["id"]
    assert client.post(base + "/restore").status_code == 409


@pytest.mark.parametrize("status", ["accepted", "queued", "processing", "awaiting_provider"])
def test_delete_refuses_accepted_or_active_work(client, db, status):
    _, _, _, result = seed_result(client, db)
    item = db.get(GenerativeMediaRequest, result["id"])
    item.status = status
    db.commit()
    assert client.delete(f"/api/v1/generative-media-requests/{item.id}").status_code == 409
    db.refresh(item)
    assert item.status == status


def test_delete_is_family_scoped(client, db):
    _, _, _, result = seed_result(client, db)
    token = current_auth.set(AuthContext(user_id="outsider", family_id="other-family", role="admin"))
    try:
        with pytest.raises(DomainError):
            trash_generative_media_request(result["id"], db)
    finally:
        current_auth.reset(token)
    assert db.get(GenerativeMediaRequest, result["id"]).status == "pending_human_review"
