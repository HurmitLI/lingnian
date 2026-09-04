from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime

import pytest
from PIL import Image
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.api.generation_node_routes import PACKAGE_MAGIC, SAFE_WORKER_FAILURE_MESSAGES
from app.main import app
from app.models import GenerationNode
from app.services.security import InMemorySecretStore, get_secret_store
from app.services.auth import session_token_hash
from test_api_flow import create_profile
from test_memory_experience import create_confirmed_story


@pytest.fixture(autouse=True)
def generation_secret_store():
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    yield
    app.dependency_overrides.pop(get_secret_store, None)


def decrypt_package(payload: bytes, *, token: str, request_id: str) -> bytes:
    assert payload.startswith(PACKAGE_MAGIC)
    offset = len(PACKAGE_MAGIC)
    nonce = payload[offset : offset + 12]
    ciphertext = payload[offset + 12 :]
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=request_id.encode("ascii"),
        info=b"lingnian-generation-package-v1",
    ).derive(token.encode("utf-8"))
    return AESGCM(key).decrypt(nonce, ciphertext, request_id.encode("ascii"))


def test_worker_failure_messages_are_safe_and_actionable():
    assert SAFE_WORKER_FAILURE_MESSAGES["PACKAGE_INVALID"] == "本次授权素材包不完整或校验未通过。"
    assert "选择更长时长" in SAFE_WORKER_FAILURE_MESSAGES["AUDIO_DURATION_MISMATCH"]


def test_home_generation_node_claims_encrypted_package_and_uploads_review_result(client, db):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile, with_photo=True)
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "portrait_video",
            "actor_label": "测试家庭管理员",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": True,
            "max_cost_cents": 100,
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    assert created.json()["progress_stage"] == "等待家用生成节点"

    token = "ln_node_test-token-with-enough-entropy-123456789"
    node = GenerationNode(
        display_name="测试 RTX 5080",
        token_hash=session_token_hash(token),
        capabilities=["photo_restore", "portrait_video", "scene_video"],
        status="active",
    )
    db.add(node)
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}

    denied = client.post("/api/v1/generation-worker/tasks/claim")
    assert denied.status_code == 401
    heartbeat = client.post(
        "/api/v1/generation-worker/heartbeat",
        headers=headers,
        json={
            "software_version": "lingnian-worker/2.0.2",
            "device_summary": "Windows 11 · RTX 5080 16GB",
            "capabilities": ["portrait_video", "scene_video"],
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["accepted_capabilities"] == ["portrait_video", "scene_video"]

    claimed = client.post("/api/v1/generation-worker/tasks/claim", headers=headers)
    assert claimed.status_code == 200, claimed.text
    task = claimed.json()
    assert task["id"] == request_id
    assert task["attempt_count"] == 1
    lease_headers = {**headers, "X-Lingnian-Lease": task["lease_token"]}

    package = client.get(task["package_url"], headers=lease_headers)
    assert package.status_code == 200, package.text
    assert package.headers["content-type"].startswith("application/vnd.lingnian.encrypted-package")
    archive_bytes = decrypt_package(package.content, token=token, request_id=request_id)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        manifest = archive.read("manifest.json").decode("utf-8")
        assert '"status": "authorized_node_job"' in manifest
        assert '"external_upload_authorized": true' in manifest
        assert any(name.startswith("sources/original-audio") for name in archive.namelist())
        assert any(name.startswith("sources/authorized-image") for name in archive.namelist())

    progress = client.patch(
        f"/api/v1/generation-worker/tasks/{request_id}/progress",
        headers=lease_headers,
        json={"progress_percent": 62, "progress_stage": "正在合成口型与原声"},
    )
    assert progress.status_code == 200, progress.text

    video_bytes = b"\x00\x00\x00\x18ftypisom" + b"moov" + b"worker-generated" + b"mdat" + b"frames"
    uploaded = client.post(
        f"/api/v1/generation-worker/tasks/{request_id}/result",
        headers={**lease_headers, "X-Content-Sha256": hashlib.sha256(video_bytes).hexdigest()},
        data={
            "actual_cost_cents": "0",
            "rendered_scene_count": "1",
            "duration_seconds": "12.4",
            "width": "1280",
            "height": "720",
        },
        files={"result": ("portrait.mp4", video_bytes, "video/mp4")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "pending_human_review"
    listed = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests"
    ).json()
    completed = next(item for item in listed if item["id"] == request_id)
    assert completed["status"] == "pending_human_review"
    assert completed["progress_percent"] == 100
    assert completed["provider_key"].startswith("home_comfyui:")
    assert completed["result_report"] == {
        "rendered_scene_count": 1,
        "duration_seconds": 12.4,
        "width": 1280,
        "height": 720,
    }
    assert completed["result_content_url"]


def test_photo_restore_worker_uploads_a_real_png(client, db):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile, with_photo=True)
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "photo_restore",
            "actor_label": "虚构示例管理员",
            "subject_consent": False,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": True,
            "max_cost_cents": 0,
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    token = "ln_node_png-upload-with-enough-entropy-123456789"
    db.add(
        GenerationNode(
            display_name="PNG 测试节点",
            token_hash=session_token_hash(token),
            capabilities=["photo_restore"],
            status="active",
        )
    )
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    task = client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json()
    png = io.BytesIO()
    Image.new("RGB", (1024, 1024), (96, 112, 128)).save(png, format="PNG")
    payload = png.getvalue()
    uploaded = client.post(
        f"/api/v1/generation-worker/tasks/{task['id']}/result",
        headers={
            **headers,
            "X-Lingnian-Lease": task["lease_token"],
            "X-Content-Sha256": hashlib.sha256(payload).hexdigest(),
        },
        data={"actual_cost_cents": "0"},
        files={"result": ("restored.png", payload, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "pending_human_review"
    listed = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests"
    ).json()
    completed = next(item for item in listed if item["id"] == request_id)
    assert completed["result_content_url"]


def test_family_can_retry_a_failed_generation_without_admin_console(client, db):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile, with_photo=True)
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "scene_video",
            "actor_label": "测试家庭管理员",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": True,
            "max_cost_cents": 100,
            "target_duration_seconds": 90,
            "aspect_ratio": "9:16",
        },
    )
    request_id = created.json()["id"]
    assert created.json()["production_spec"]["target_duration_seconds"] == 90
    assert created.json()["production_spec"]["aspect_ratio"] == "9:16"
    token = "ln_node_failed-task-with-enough-entropy-123456789"
    db.add(
        GenerationNode(
            display_name="失败恢复测试节点",
            token_hash=session_token_hash(token),
            capabilities=["scene_video"],
            software_version="lingnian-worker/2.0.2",
            status="active",
        )
    )
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    task = client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json()
    assert task["production_spec"]["target_duration_seconds"] == 90
    progress = client.patch(
        f"/api/v1/generation-worker/tasks/{request_id}/progress",
        headers={**headers, "X-Lingnian-Lease": task["lease_token"]},
        json={
            "progress_percent": 30,
            "progress_stage": "已完成前三个镜头",
            "completed_scene_count": 3,
            "total_scene_count": 10,
            "checkpoint_key": "scene-03",
        },
    )
    assert progress.status_code == 200, progress.text
    failed = client.post(
        f"/api/v1/generation-worker/tasks/{request_id}/fail",
        headers={**headers, "X-Lingnian-Lease": task["lease_token"]},
        json={
            "error_code": "WORKFLOW_FAILED",
            "message": "unsafe worker detail",
            "retryable": False,
        },
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"

    retried = client.post(f"/api/v1/generative-media-requests/{request_id}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    assert retried.json()["attempt_count"] == 0
    assert retried.json()["progress_percent"] == 30
    assert retried.json()["progress_stage"] == "等待节点从已完成镜头继续"

    denied = client.post(f"/api/v1/generative-media-requests/{request_id}/retry")
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "GENERATION_REQUEST_NOT_RETRYABLE"


def test_worker_failure_requeues_until_attempt_limit(client, db):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile)
    created = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json={
            "story_id": story["id"],
            "generation_type": "scene_video",
            "actor_label": "测试家庭管理员",
            "subject_consent": True,
            "rights_confirmed": True,
            "no_impersonation": True,
            "allow_external_upload": True,
            "max_cost_cents": 0,
        },
    ).json()
    token = "ln_node_failure-token-with-enough-entropy-123456"
    db.add(
        GenerationNode(
            display_name="测试节点",
            token_hash=session_token_hash(token),
            capabilities=["scene_video"],
            software_version="lingnian-worker/2.0.2",
            status="active",
        )
    )
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    claimed = client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json()
    failed = client.post(
        f"/api/v1/generation-worker/tasks/{created['id']}/fail",
        headers={**headers, "X-Lingnian-Lease": claimed["lease_token"]},
        json={"error_code": "COMFYUI_TEMPORARY_FAILURE", "message": "模型临时加载失败。", "retryable": True},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "queued"

    cancelled = client.post(f"/api/v1/generative-media-requests/{created['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json() is None


def test_outdated_scene_worker_is_visible_but_cannot_receive_or_start_new_tasks(client, db):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile)
    request_payload = {
        "story_id": story["id"],
        "generation_type": "scene_video",
        "actor_label": "测试家庭管理员",
        "subject_consent": True,
        "rights_confirmed": True,
        "no_impersonation": True,
        "allow_external_upload": True,
        "max_cost_cents": 0,
        "target_duration_seconds": 45,
    }
    queued = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json=request_payload,
    )
    assert queued.status_code == 201, queued.text

    token = "ln_node_outdated-version-with-enough-entropy-123456"
    db.add(
        GenerationNode(
            display_name="旧版测试节点",
            token_hash=session_token_hash(token),
            capabilities=["scene_video"],
            software_version="lingnian-worker/2.0.1",
            device_summary="Windows 11 · RTX 5080 16GB",
            last_seen_at=datetime.now(UTC).replace(tzinfo=None),
            status="active",
        )
    )
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}

    capabilities = client.get("/api/v1/generative-media/capabilities").json()
    scene = next(item for item in capabilities if item["generation_type"] == "scene_video")
    assert scene["submission_blocked"] is True
    assert scene["minimum_worker_version"] == "2.0.2"
    assert client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json() is None

    blocked = client.post(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
        json=request_payload,
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "GENERATION_NODE_UPGRADE_REQUIRED"

    heartbeat = client.post(
        "/api/v1/generation-worker/heartbeat",
        headers=headers,
        json={
            "software_version": "lingnian-worker/2.0.2",
            "device_summary": "Windows 11 · RTX 5080 16GB",
            "capabilities": ["scene_video"],
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["accepted_capabilities"] == ["scene_video"]
    assert heartbeat.json()["upgrade_required_capabilities"] == {}
    assert client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json()["id"] == queued.json()["id"]


def test_scene_result_must_pass_duplicate_and_dynamic_visual_checks(client, db):
    profile = create_profile(client)
    story, _ = create_confirmed_story(client, profile)
    token = "ln_node_quality-report-with-enough-entropy-123456789"
    db.add(
        GenerationNode(
            display_name="质量测试节点",
            token_hash=session_token_hash(token),
            capabilities=["scene_video"],
            software_version="lingnian-worker/2.0.2",
            status="active",
        )
    )
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}

    def create_and_claim():
        created = client.post(
            f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",
            json={
                "story_id": story["id"],
                "generation_type": "scene_video",
                "actor_label": "测试家庭管理员",
                "subject_consent": True,
                "rights_confirmed": True,
                "no_impersonation": True,
                "allow_external_upload": True,
                "max_cost_cents": 0,
                "target_duration_seconds": 45,
            },
        )
        assert created.status_code == 201, created.text
        task = client.post("/api/v1/generation-worker/tasks/claim", headers=headers).json()
        return created.json(), task

    video_bytes = b"\x00\x00\x00\x18ftypisom" + b"moov" + b"quality-video" + b"mdat" + b"frames"
    created, task = create_and_claim()
    rejected = client.post(
        f"/api/v1/generation-worker/tasks/{task['id']}/result",
        headers={
            **headers,
            "X-Lingnian-Lease": task["lease_token"],
            "X-Content-Sha256": hashlib.sha256(video_bytes).hexdigest(),
        },
        data={
            "rendered_scene_count": "4",
            "duration_seconds": "45",
            "width": "1280",
            "height": "720",
            "generated_context_scene_count": "2",
            "generated_video_scene_count": "0",
            "unique_generated_visual_count": "2",
            "duplicate_visual_check_passed": "true",
        },
        files={"result": ("static.mp4", video_bytes, "video/mp4")},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "AUTOMATED_QUALITY_CHECK_FAILED"
    failed = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests"
    ).json()
    assert next(item for item in failed if item["id"] == created["id"])["status"] == "failed"

    accepted, task = create_and_claim()
    uploaded = client.post(
        f"/api/v1/generation-worker/tasks/{task['id']}/result",
        headers={
            **headers,
            "X-Lingnian-Lease": task["lease_token"],
            "X-Content-Sha256": hashlib.sha256(video_bytes).hexdigest(),
        },
        data={
            "rendered_scene_count": "4",
            "duration_seconds": "45",
            "width": "1280",
            "height": "720",
            "generated_context_scene_count": "2",
            "generated_video_scene_count": "2",
            "unique_generated_visual_count": "2",
            "duplicate_visual_check_passed": "true",
        },
        files={"result": ("dynamic.mp4", video_bytes, "video/mp4")},
    )
    assert uploaded.status_code == 200, uploaded.text
    completed = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests"
    ).json()
    report = next(item for item in completed if item["id"] == accepted["id"])["result_report"]
    assert report["automated_quality_status"] == "passed"
    assert report["generated_video_scene_count"] == 2
    assert report["unique_generated_visual_count"] == 2
