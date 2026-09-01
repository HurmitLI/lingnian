from __future__ import annotations

import io
import zipfile

from PIL import Image

from app.main import app
from app.models import GenerativeMediaRequest, LegacyPlan, MediaPersonTag, StoryContribution, StoryDetail
from app.services.security import InMemorySecretStore, get_secret_store
from test_api_flow import create_profile, upload_test_audio


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (360, 260), color=(205, 188, 164)).save(buffer, format="PNG")
    return buffer.getvalue()


def create_confirmed_story(client, profile: dict, *, with_photo: bool = False) -> tuple[dict, str | None]:
    session_payload = {
        "elder_id": profile["id"],
        "life_stage": "工作",
        "trigger_kind": "photo" if with_photo else "question",
    }
    session = client.post("/api/v1/memory-sessions", json=session_payload)
    assert session.status_code == 201, session.text
    image_asset_id = None
    if with_photo:
        image = client.post(
            f"/api/v1/memory-sessions/{session.json()['id']}/trigger-image",
            data={"trigger_kind": "photo", "user_annotation": "虚构的旧厂房照片"},
            files={"image": ("factory.png", png_bytes(), "image/png")},
        )
        assert image.status_code == 201, image.text
        image_asset_id = image.json()["media_asset_id"]
    upload_test_audio(client, session.json()["id"])
    transcription = client.post(
        f"/api/v1/memory-sessions/{session.json()['id']}/transcription-tasks"
    )
    assert transcription.status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session.json()['id']}").json()
    corrected = "1978 年我在虚构的红星纺织厂工作，师傅教我修机器。后来我在那里认识了许多朋友。"
    updated = client.patch(
        f"/api/v1/transcripts/{detail['transcript']['id']}",
        json={"corrected_text": corrected},
    )
    assert updated.status_code == 200
    organized = client.post(
        f"/api/v1/memory-sessions/{session.json()['id']}/organization-tasks"
    )
    assert organized.status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session.json()['id']}").json()
    confirmed = client.post(
        f"/api/v1/story-drafts/{detail['story_draft']['id']}/confirm",
        json={"confirmed_by": "测试家人"},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json(), image_asset_id


def test_archive_question_is_grounded_and_gap_becomes_interview(client):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile)

    answer = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/archive-questions",
        json={"question": "奶奶年轻时在哪里工作？"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "grounded"
    assert answer.json()["citations"][0]["story_id"] == story["id"]
    assert "纺织厂" in answer.json()["citations"][0]["excerpt"]
    assert answer.json()["answer_mode"] == "local_extract_with_sources"

    missing = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/archive-questions",
        json={"question": "小时候养过什么宠物？"},
    )
    assert missing.status_code == 200
    assert missing.json()["status"] == "not_found"
    assert missing.json()["follow_up_question"]

    gap = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/archive-gaps",
        json={
            "question": missing.json()["follow_up_question"],
            "actor_label": "测试孙辈",
        },
    )
    assert gap.status_code == 201
    assert gap.json()["life_stage"] == "家人提问"
    assert gap.json()["status"] == "PROMPT_READY"


def test_family_can_enrich_story_photo_and_legacy_plan(client):
    profile = create_profile(client)
    story, image_asset_id = create_confirmed_story(client, profile, with_photo=True)
    child = client.post(
        f"/api/v1/families/{profile['family_id']}/people",
        json={"display_name": "测试女儿", "role": "family_member"},
    ).json()

    detail = client.put(
        f"/api/v1/stories/{story['id']}/detail",
        json={
            "place_name": "虚构的江南小城",
            "event_year": 1978,
            "theme_tags": ["工作", "师徒", "工作"],
            "summary": "一段关于第一份工作的虚构测试故事。",
            "updated_by": "测试女儿",
        },
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["theme_tags"] == ["工作", "师徒"]

    contribution = client.post(
        f"/api/v1/stories/{story['id']}/contributions",
        json={
            "contributor_person_id": child["id"],
            "contributor_label": "测试女儿",
            "contribution_type": "context",
            "body": "家里还保留着一张虚构的工作证照片。",
        },
    )
    assert contribution.status_code == 201

    tag = client.post(
        f"/api/v1/media-assets/{image_asset_id}/person-tags",
        json={"person_id": child["id"], "tagged_by": "测试管理员"},
    )
    assert tag.status_code == 201, tag.text
    assert tag.json()["person_name"] == "测试女儿"

    legacy = client.put(
        f"/api/v1/families/{profile['family_id']}/legacy-plan",
        json={
            "successor_person_ids": [child["id"]],
            "access_policy": "manual_handoff",
            "steward_label": "测试管理员",
            "note": "只记录安排，不自动移交权限。",
        },
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["successor_person_ids"] == [child["id"]]

    timeline = client.get(f"/api/v1/elder-profiles/{profile['id']}/timeline").json()
    assert timeline[0]["detail"]["place_name"] == "虚构的江南小城"
    assert timeline[0]["contributions"][0]["body"].startswith("家里还保留")
    assert timeline[0]["person_tags"][0]["person_name"] == "测试女儿"


def test_open_heritage_package_contains_story_and_original_media(client):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile, with_photo=True)
    response = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/heritage-package",
        json={"actor_label": "测试家人"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "index.html" in names
        assert "data/stories.json" in names
        assert "data/family.json" in names
        assert "data/media-manifest.json" in names
        assert any(name.startswith("media/audio/") for name in names)
        assert any(name.startswith("media/images/") for name in names)
        index = archive.read("index.html").decode("utf-8")
        assert story["title"] in index
        assert "红星纺织厂" in index
        family_data = archive.read("data/family.json").decode("utf-8")
        assert "api_key" not in family_data.lower()
        assert "recovery" not in family_data.lower()


def test_paid_media_requests_are_recorded_but_never_executed(client):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile)
    capabilities = client.get("/api/v1/generative-media/capabilities")
    assert capabilities.status_code == 200
    assert all(item["available"] is False for item in capabilities.json())
    assert {item["generation_type"] for item in capabilities.json()} == {
        "photo_restore",
        "portrait_video",
        "scene_video",
        "voice_replica",
    }

    denied = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "portrait_video",
            "actor_label": "测试家人",
            "subject_consent": False,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": False,
            "max_cost_cents": 300,
        },
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "SUBJECT_CONSENT_REQUIRED"

    recorded = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "portrait_video",
            "actor_label": "测试家人",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": False,
            "max_cost_cents": 300,
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["status"] == "awaiting_provider"
    assert recorded.json()["error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert recorded.json()["estimated_cost_cents"] == 0


def test_memory_experience_fields_stay_encrypted_for_real_family(client, db):
    profile = create_profile(client)
    story, image_asset_id = create_confirmed_story(client, profile, with_photo=True)
    family_id = profile["family_id"]
    person = client.post(
        f"/api/v1/families/{family_id}/people",
        json={"display_name": "虚构接管人", "role": "family_member"},
    ).json()
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    passphrase = "虚构恢复口令-家族记忆-长度足够"
    try:
        assert client.post(
            f"/api/v1/families/{family_id}/security/initialize",
            json={"actor_label": "虚构管理员"},
        ).status_code == 201
        recovery = client.post(
            f"/api/v1/families/{family_id}/security/recovery-package",
            json={"actor_label": "虚构管理员", "recovery_passphrase": passphrase},
        )
        assert recovery.status_code == 200
        assert client.post(
            f"/api/v1/families/{family_id}/security/verify-recovery",
            data={"actor_label": "虚构管理员", "recovery_passphrase": passphrase},
            files={"package": ("recovery.json", recovery.content, "application/json")},
        ).status_code == 200
        assert client.post(
            f"/api/v1/families/{family_id}/security/activate",
            json={
                "actor_label": "虚构管理员",
                "data_classification": "authorized_sensitive",
            },
        ).status_code == 200

        detail = client.put(
            f"/api/v1/stories/{story['id']}/detail",
            json={
                "place_name": "虚构加密地点",
                "event_year": 1986,
                "theme_tags": ["家庭", "迁居"],
                "summary": "虚构的加密摘要。",
                "updated_by": "虚构管理员",
            },
        )
        contribution = client.post(
            f"/api/v1/stories/{story['id']}/contributions",
            json={
                "contributor_person_id": person["id"],
                "contributor_label": "虚构接管人",
                "contribution_type": "context",
                "body": "虚构的加密家庭补充。",
            },
        )
        tag = client.post(
            f"/api/v1/media-assets/{image_asset_id}/person-tags",
            json={
                "person_id": person["id"],
                "tagged_by": "虚构管理员",
                "note": "虚构的加密照片说明。",
            },
        )
        legacy = client.put(
            f"/api/v1/families/{family_id}/legacy-plan",
            json={
                "successor_person_ids": [person["id"]],
                "access_policy": "manual_handoff",
                "steward_label": "虚构管理员",
                "note": "虚构的加密交接说明。",
            },
        )
        generation = client.post(
            f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
            json={
                "story_id": story["id"],
                "generation_type": "portrait_video",
                "actor_label": "虚构管理员",
                "subject_consent": True,
                "rights_confirmed": True,
                "no_impersonation": True,
                "allow_external_upload": False,
                "max_cost_cents": 0,
            },
        )
        assert all(
            response.status_code in {200, 201}
            for response in (detail, contribution, tag, legacy, generation)
        )
        assert detail.json()["place_name"] == "虚构加密地点"
        assert contribution.json()["body"] == "虚构的加密家庭补充。"

        heritage = client.post(
            f"/api/v1/elder-profiles/{profile['id']}/heritage-package",
            json={"actor_label": "虚构管理员"},
        )
        assert heritage.status_code == 200
        with zipfile.ZipFile(io.BytesIO(heritage.content)) as archive:
            assert "虚构的加密家庭补充" in archive.read("index.html").decode("utf-8")

        db.expire_all()
        assert db.get(StoryDetail, detail.json()["id"]).place_name == "[niannian:encrypted:v1]"
        assert db.get(StoryContribution, contribution.json()["id"]).body == "[niannian:encrypted:v1]"
        assert db.get(MediaPersonTag, tag.json()["id"]).note == "[niannian:encrypted:v1]"
        assert db.get(LegacyPlan, legacy.json()["id"]).note == "[niannian:encrypted:v1]"
        assert db.get(GenerativeMediaRequest, generation.json()["id"]).actor_label == "[niannian:encrypted:v1]"
    finally:
        app.dependency_overrides.pop(get_secret_store, None)
