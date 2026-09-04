from __future__ import annotations

import io
import json
import zipfile

from PIL import Image
from sqlalchemy import select

from app.main import app
from app.models import (
    ConsentEvent,
    GenerativeMediaRequest,
    LegacyPlan,
    MediaAsset,
    MediaPersonTag,
    StoryContribution,
    StoryDetail,
)
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
    reviewed = client.patch(
        f"/api/v1/story-contributions/{contribution.json()['id']}/status",
        json={"status": "confirmed", "actor_label": "测试管理员"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "confirmed"
    family_answer = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/archive-questions",
        json={"question": "家里还保存着什么工作纪念？"},
    )
    assert family_answer.status_code == 200
    family_source = next(
        item
        for item in family_answer.json()["citations"]
        if item["source_kind"] == "family_contribution"
    )
    assert family_source["source_label"] == "测试女儿"
    assert "工作证" in family_source["excerpt"]

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

    imported = client.post(
        f"/api/v1/generative-media-requests/{recorded.json()['id']}/result",
        data={"provider_key": "musetalk_manual", "actual_cost_cents": "33"},
        files={
            "video": (
                "short-review.mp4",
                b"\x00\x00\x00\x18ftypisom" + b"moov" + b"generated" + b"mdat" + b"frames",
                "video/mp4",
            )
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["status"] == "pending_human_review"
    assert imported.json()["result_content_url"].endswith("/content")
    assert imported.json()["actual_cost_cents"] == 33

    incomplete_review = client.patch(
        f"/api/v1/generative-media-requests/{recorded.json()['id']}/review",
        json={
            "decision": "accepted",
            "reviewed_by": "测试验收人",
            "audio_present": True,
            "lip_sync_verified": False,
            "pauses_natural": True,
            "expression_natural": True,
            "narrative_consistent": True,
            "duration_appropriate": True,
        },
    )
    assert incomplete_review.status_code == 409
    assert incomplete_review.json()["error"]["code"] == "GENERATION_REVIEW_INCOMPLETE"

    accepted = client.patch(
        f"/api/v1/generative-media-requests/{recorded.json()['id']}/review",
        json={
            "decision": "accepted",
            "reviewed_by": "测试验收人",
            "review_notes": "声音、嘴型和故事画面逐项检查通过。",
            "audio_present": True,
            "lip_sync_verified": True,
            "pauses_natural": True,
            "expression_natural": True,
            "narrative_consistent": True,
            "duration_appropriate": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["reviewed_by"] == "测试验收人"
    assert all(accepted.json()["review_checks"].values())

    accepted_video = client.get(accepted.json()["result_content_url"])
    assert accepted_video.status_code == 200
    assert accepted_video.headers["content-type"] == "video/mp4"

    scene_request = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "scene_video",
            "actor_label": "测试家人",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": False,
            "max_cost_cents": 100,
        },
    ).json()
    assert client.post(
        f"/api/v1/generative-media-requests/{scene_request['id']}/result",
        data={"provider_key": "scene_manual", "actual_cost_cents": "0"},
        files={
            "video": (
                "scene.mp4",
                b"\x00\x00\x00\x18ftypisom" + b"moov" + b"scene" + b"mdat" + b"frames",
                "video/mp4",
            )
        },
    ).status_code == 200
    scene_review = client.patch(
        f"/api/v1/generative-media-requests/{scene_request['id']}/review",
        json={
            "decision": "accepted",
            "reviewed_by": "测试验收人",
            "audio_present": True,
            "lip_sync_verified": False,
            "pauses_natural": False,
            "expression_natural": True,
            "narrative_consistent": True,
            "shot_continuity_verified": True,
            "no_fabricated_facts": True,
            "duration_appropriate": True,
        },
    )
    assert scene_review.status_code == 200, scene_review.text
    assert scene_review.json()["status"] == "accepted"


def test_documentary_package_has_exact_duration_source_mapping_and_single_photo_limit(client):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile, with_photo=True)
    preview = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/documentary-plan-preview",
        json={
            "story_id": story["id"],
            "target_duration_seconds": 45,
            "aspect_ratio": "9:16",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["production_spec"]["target_duration_seconds"] == 45
    assert sum(scene["duration_seconds"] for scene in preview.json()["scenes"]) == 45
    package = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/production-package",
        json={
            "story_id": story["id"],
            "generation_type": "scene_video",
            "actor_label": "测试家人",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
            "target_duration_seconds": 45,
            "aspect_ratio": "9:16",
        },
    )
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        storyboard = json.loads(archive.read("production/storyboard.json"))
    assert manifest["version"] == 2
    assert manifest["production_spec"]["target_duration_seconds"] == 45
    assert manifest["production_spec"]["aspect_ratio"] == "9:16"
    assert manifest["production_spec"]["output"] == {"width": 720, "height": 1280, "fps": 24}
    assert storyboard["audio_plan"]["synthetic_voice_allowed"] is False
    assert sum(scene["duration_seconds"] for scene in storyboard["scenes"]) == 45
    assert storyboard["scenes"][0]["kind"] == "title_card"
    assert storyboard["scenes"][-1]["kind"] == "source_card"
    assert all(scene["source_story_id"] == story["id"] for scene in storyboard["scenes"])
    assert sum(scene["kind"] == "archival_photo" for scene in storyboard["scenes"]) <= 2
    photo_seconds = sum(
        scene["duration_seconds"]
        for scene in storyboard["scenes"]
        if scene["kind"] == "archival_photo"
    )
    assert photo_seconds / 45 <= 0.35


def test_local_generation_production_package_has_sources_and_reviewable_storyboard(client):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile, with_photo=True)
    package = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/production-package",
        json={
            "story_id": story["id"],
            "generation_type": "portrait_video",
            "actor_label": "测试家人",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
        },
    )
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = set(archive.namelist())
        assert "README.md" in names
        assert "manifest.json" in names
        assert "production/storyboard.json" in names
        assert any(name.startswith("sources/original-audio") for name in names)
        assert any(name.startswith("sources/authorized-image") for name in names)
        manifest = archive.read("manifest.json").decode("utf-8")
        assert '"status": "local_preproduction_only"' in manifest
        assert '"external_upload_authorized": false' in manifest
        storyboard = archive.read("production/storyboard.json").decode("utf-8")
        assert "人工确认故事原文" in storyboard
        assert "spoken_narration" in storyboard
        assert "review_required" in storyboard

    denied = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/production-package",
        json={
            "story_id": story["id"],
            "generation_type": "portrait_video",
            "actor_label": "测试家人",
            "subject_consent": False,
            "rights_confirmed": True,
            "no_impersonation": True,
        },
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "SUBJECT_CONSENT_REQUIRED"

    restoration = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/production-package",
        json={
            "story_id": story["id"],
            "generation_type": "photo_restore",
            "actor_label": "测试家人",
            "subject_consent": False,
            "rights_confirmed": True,
            "no_impersonation": True,
        },
    )
    assert restoration.status_code == 200, restoration.text
    with zipfile.ZipFile(io.BytesIO(restoration.content)) as archive:
        assert "production/restoration-plan.json" in archive.namelist()
        plan = archive.read("production/restoration-plan.json").decode("utf-8")
        assert "绝不覆盖原图" in plan
        assert "不凭空增加人物" in plan


def test_memory_experience_fields_stay_encrypted_for_real_family(client, db):
    profile = create_profile(client)
    story, image_asset_id = create_confirmed_story(client, profile, with_photo=True)
    family_id = profile["family_id"]
    person = client.post(
        f"/api/v1/families/{family_id}/people",
        json={"display_name": "虚构接管人", "role": "family_member"},
    ).json()
    preexisting_contribution = client.post(
        f"/api/v1/stories/{story['id']}/contributions",
        json={
            "contributor_person_id": person["id"],
            "contributor_label": "虚构接管人",
            "contribution_type": "context",
            "body": "启用加密以前保存的虚构家庭补充。",
        },
    ).json()
    assert client.patch(
        f"/api/v1/story-contributions/{preexisting_contribution['id']}/status",
        json={"status": "confirmed", "actor_label": "启用前虚构管理员"},
    ).status_code == 200
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

        generated_result = client.post(
            f"/api/v1/generative-media-requests/{generation.json()['id']}/result",
            data={"provider_key": "musetalk_manual", "actual_cost_cents": "0"},
            files={
                "video": (
                    "encrypted-result.mp4",
                    b"\x00\x00\x00\x18ftypisom" + b"moov" + b"secure" + b"mdat" + b"frames",
                    "video/mp4",
                )
            },
        )
        assert generated_result.status_code == 200, generated_result.text
        review = client.patch(
            f"/api/v1/generative-media-requests/{generation.json()['id']}/review",
            json={
                "decision": "accepted",
                "reviewed_by": "虚构验收人",
                "review_notes": "虚构人物视频六项检查通过。",
                "audio_present": True,
                "lip_sync_verified": True,
                "pauses_natural": True,
                "expression_natural": True,
                "narrative_consistent": True,
                "duration_appropriate": True,
            },
        )
        assert review.status_code == 200, review.text
        encrypted_video = client.get(review.json()["result_content_url"])
        assert encrypted_video.status_code == 200
        assert b"ftyp" in encrypted_video.content[:16]

        heritage = client.post(
            f"/api/v1/elder-profiles/{profile['id']}/heritage-package",
            json={"actor_label": "虚构管理员"},
        )
        assert heritage.status_code == 200
        with zipfile.ZipFile(io.BytesIO(heritage.content)) as archive:
            assert "虚构的加密家庭补充" in archive.read("index.html").decode("utf-8")

        db.expire_all()
        assert db.get(StoryDetail, detail.json()["id"]).place_name == "[niannian:encrypted:v1]"
        assert db.get(StoryContribution, preexisting_contribution["id"]).body == "[niannian:encrypted:v1]"
        assert db.get(StoryContribution, contribution.json()["id"]).body == "[niannian:encrypted:v1]"
        assert db.get(MediaPersonTag, tag.json()["id"]).note == "[niannian:encrypted:v1]"
        assert db.get(LegacyPlan, legacy.json()["id"]).note == "[niannian:encrypted:v1]"
        generation_row = db.get(GenerativeMediaRequest, generation.json()["id"])
        assert generation_row.actor_label == "[niannian:encrypted:v1]"
        assert generation_row.reviewed_by == "[niannian:encrypted:v1]"
        assert generation_row.review_notes == "[niannian:encrypted:v1]"
        assert db.get(MediaAsset, generation_row.result_asset_id).encryption_version == 1
        review_event = db.scalar(
            select(ConsentEvent).where(
                ConsentEvent.object_id == preexisting_contribution["id"],
                ConsentEvent.action == "review_story_contribution:confirmed",
            )
        )
        assert review_event.actor_label == "[niannian:encrypted:v1]"
    finally:
        app.dependency_overrides.pop(get_secret_store, None)
