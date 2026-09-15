"""Synthetic HTTP queue tests; no cloud or real GPU submission."""
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from sqlalchemy import select, func

from app.api import short_scene_job_routes as jobs
from app.main import app
from app.models import ArchiveSecurity, FamilyArchive, GenerationNode, MediaAsset, ShortSceneJob
from app.models.entities import now_utc
from app.services.auth import AuthContext, current_auth, session_token_hash
from app.services.security import InMemorySecretStore, get_secret_store, get_family_key_manager
from app.services.security.archive_encryption import activate_archive_encryption
from app.core.config import get_settings
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401

TOKEN = "synthetic-node-token-for-tests-only-000000000"


@pytest.fixture
def queued_case(client, db, export_case):
    s = export_case
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    key, _ = get_family_key_manager(s.family_id, store).get_or_create()
    security = ArchiveSecurity(family_id=s.family_id, recovery_package_created_at=now_utc(), recovery_verified_at=now_utc())
    node = GenerationNode(display_name="测试5080", token_hash=session_token_hash(TOKEN), capabilities=["scene_video"],
                          software_version="0.2.0", status="active")
    db.add_all([security, node]); db.commit()
    activate_archive_encryption(db, family=db.get(FamilyArchive, s.family_id), metadata=security,
                                master_key=key, target_classification="authorized_sensitive", settings=get_settings())
    db.expire_all()
    s.url = s.url.replace("production-package", "jobs")
    s.consent.update(authorize_node_delivery=True, idempotency_key="test-short-job")
    s.node = node
    try:
        yield s
    finally:
        app.dependency_overrides.pop(get_secret_store, None)


def create(client, s, **changes):
    return client.post(s.url, data={"consent": json.dumps({**s.consent, **changes})},
                       files={"reference": ("fixture.png", s.image, "image/png")})


def claim(client, token=TOKEN, **payload):
    return client.post("/api/v1/generation-worker/short-scene/tasks/claim",
                       json=payload or {"protocol": "native-short-scene-v1"}, headers={"Authorization": f"Bearer {token}"})


def headers(task, token=TOKEN):
    return {"Authorization": f"Bearer {token}", "X-Lingnian-Lease": task["lease_token"]}


def test_queue_idempotency_storage_and_legacy_isolation(client, db, queued_case):
    s = queued_case
    first = create(client, s); assert first.status_code == 200, first.text
    assert create(client, s).json()["id"] == first.json()["id"]
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 1
    job = db.scalar(select(ShortSceneJob))
    asset = db.get(MediaAsset, job.package_asset_id)
    path = get_settings().resolved_asset_root / asset.relative_path
    assert asset.encryption_version == 1 and path.read_bytes().startswith(b"NNMEDIA1")
    assert not path.read_bytes().startswith(b"PK")
    old = client.post("/api/v1/generation-worker/tasks/claim", headers={"Authorization": f"Bearer {TOKEN}"})
    assert old.status_code == 200 and old.json() is None
    listing = client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-jobs").json()
    assert listing[0]["status"] == "queued" and not listing[0]["visual_accepted"]
    assert "lease_token" not in listing[0] and "relative_path" not in listing[0]


def test_recover_exact_request_without_creating_or_delivering(client, db, queued_case):
    s = queued_case
    url = s.url + "/by-request/test-short-job"
    assert client.get(url).status_code == 200 and client.get(url).json() is None
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 0
    created = create(client, s).json()
    recovered = client.get(url)
    assert recovered.status_code == 200 and recovered.json()["id"] == created["id"]
    assert recovered.json()["status"] == "queued"
    assert "lease_token" not in recovered.json() and "idempotency_key" not in recovered.json()
    assert client.get(s.url + "/by-request/unknown-request").json() is None
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 1


def test_recovery_cannot_cross_family_or_accept_bad_keys(client, queued_case):
    s = queued_case
    create(client, s)
    assert client.get(s.url + "/by-request/bad").status_code == 422
    token = current_auth.set(AuthContext("other-user", "other-family", "owner"))
    try:
        assert client.get(s.url + "/by-request/test-short-job").status_code == 404
    finally:
        current_auth.reset(token)


def test_claim_decrypt_exact_bundle_and_progress(client, db, queued_case, monkeypatch):
    s = queued_case
    assert create(client, s).status_code == 200
    response = claim(client); assert response.status_code == 200, response.text
    task = response.json()
    assert claim(client).json() is None
    downloaded = client.get(task["package_url"], headers=headers(task))
    assert downloaded.status_code == 200, downloaded.text
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "generation-worker" / "src"))
    from lingnian_worker.package import decrypt_package
    plain = decrypt_package(downloaded.content, token=TOKEN, request_id=task["id"])
    assert hashlib.sha256(plain).hexdigest() == task["package_sha256"]
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        assert z.read("recording.wav") == s.audio
    result = client.patch(task["package_url"].replace("/package", "/progress"), headers=headers(task),
                          json={"stage": "awaiting_input_review", "percent": 5})
    assert result.status_code == 200 and result.json()["status"] == "awaiting_input_review"
    assert client.get(task["package_url"], headers=headers(task)).status_code == 409


@pytest.mark.parametrize("change", ["denied", "string_bool", "extra", "wrong_hash"])
def test_invalid_creation_never_enqueues(client, db, queued_case, change):
    updates = {"denied": {"authorize_node_delivery": False}, "string_bool": {"authorize_node_delivery": "true"},
               "extra": {"approved": True}, "wrong_hash": {"input_sha256": "f" * 64}}[change]
    assert create(client, queued_case, **updates).status_code in {409, 422}
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 0


def test_same_key_different_material_rejected(client, db, queued_case):
    s = queued_case
    assert create(client, s).status_code == 200
    assert create(client, s, input_sha256="f" * 64).status_code == 409
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 1


def test_family_boundaries(client, queued_case):
    s = queued_case
    job = create(client, s).json()
    token = current_auth.set(AuthContext("other-user", "other-family", "owner"))
    try:
        assert create(client, s).status_code == 404
        assert client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-jobs").status_code == 404
        assert client.post(f"/api/v1/short-scene-jobs/{job['id']}/cancel").status_code == 404
    finally:
        current_auth.reset(token)


def test_wrong_node_lease_and_revocation(client, db, queued_case):
    s = queued_case
    create(client, s)
    task = claim(client).json()
    assert client.get(task["package_url"], headers={**headers(task), "X-Lingnian-Lease": "wrong"}).status_code == 409
    assert client.get(task["package_url"]).status_code == 401
    other_token = TOKEN + "other"
    db.add(GenerationNode(display_name="other", token_hash=session_token_hash(other_token), capabilities=["scene_video"], status="active"))
    db.commit()
    assert client.get(task["package_url"], headers=headers(task, other_token)).status_code == 409
    db.get(GenerationNode, s.node.id).status = "revoked"; db.commit()
    assert client.get(task["package_url"], headers=headers(task)).status_code == 401


def test_protocol_capability_and_no_job(client, db, queued_case):
    s = queued_case
    assert claim(client).json() is None
    assert claim(client, protocol="legacy").status_code == 422
    db.get(GenerationNode, s.node.id).capabilities = ["photo_restore"]; db.commit()
    assert claim(client).status_code == 403


def test_expired_job_is_interrupted_not_requeued_and_blocks_node(client, db, queued_case):
    s = queued_case
    create(client, s)
    task = claim(client).json()
    job = db.get(ShortSceneJob, task["id"])
    job.lease_expires_at = jobs.utc_now() - timedelta(seconds=1); db.commit()
    create(client, s, idempotency_key="another-job")
    assert claim(client).json() is None
    db.refresh(job)
    assert job.status == "interrupted" and job.assigned_node_id == s.node.id
    assert client.get(task["package_url"], headers=headers(task)).status_code == 409
    assert client.post(f"/api/v1/short-scene-jobs/{job.id}/cancel").status_code == 200
    assert claim(client).json()["id"] != job.id


def test_interrupted_job_requires_node_check_before_requeueing_same_job(client, db, queued_case):
    s = queued_case
    created = create(client, s).json()
    task = claim(client).json()
    job = db.get(ShortSceneJob, task["id"])
    job.lease_expires_at = jobs.utc_now() - timedelta(seconds=1)
    db.commit()
    assert claim(client).json() is None
    db.refresh(job)
    assert job.status == "interrupted"
    url = f"/api/v1/short-scene-jobs/{job.id}/resume"
    assert client.post(url, json={"authorize_resume": True}).status_code == 409
    resumed = client.post(url, json={"authorize_resume": True, "node_checked_no_gpu_submission": True})
    assert resumed.status_code == 200
    assert resumed.json()["id"] == created["id"] and resumed.json()["status"] == "queued"
    db.refresh(job)
    assert job.assigned_node_id is None and job.claim_request_key is None
    assert job.lease_token_hash is None and job.lease_expires_at is None


def test_cancel_revokes_delivery_and_progress(client, queued_case):
    s = queued_case
    create(client, s)
    task = claim(client).json()
    assert client.post(f"/api/v1/short-scene-jobs/{task['id']}/cancel").status_code == 200
    assert client.get(task["package_url"], headers=headers(task)).status_code == 409
    assert client.patch(task["package_url"].replace("/package", "/progress"), headers=headers(task),
                        json={"stage": "generating", "percent": 20}).status_code == 409


def test_no_claim_returned_when_snapshot_fails(client, db, queued_case, monkeypatch):
    create(client, queued_case)
    settings = get_settings()
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(formal_auth_required=True, resolved_asset_root=settings.resolved_asset_root))
    monkeypatch.setattr(jobs, "persist_configured_database_snapshot", lambda _: None)
    result = claim(client)
    assert result.status_code == 503 and "lease_token" not in result.text


def test_uploaded_result_stays_unapproved_and_encrypted(client, db, queued_case, monkeypatch):
    create(client, queued_case)
    task = claim(client).json()
    base = task["package_url"].removesuffix("/package")
    assert client.patch(base + "/progress", headers=headers(task), json={"stage": "generating", "percent": 10}).status_code == 200
    # Container fixture, explicitly bypassing decode here; decoder is separately tested.
    video = b"\x00\x00\x00\x18ftypmp42" + b"moov" + b"mdat" + b"test-only"
    monkeypatch.setattr(jobs, "verify_candidate", lambda _: None)
    result = client.post(base + "/result", headers={**headers(task), "X-Content-Sha256": hashlib.sha256(video).hexdigest()},
                         files={"result": ("fixture.mp4", video, "video/mp4")})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "awaiting_full_playback_review" and not result.json()["visual_accepted"]
    asset = db.get(MediaAsset, result.json()["result_asset_id"])
    assert asset.encryption_version == 1 and asset.status == "pending_human_review"


@pytest.mark.parametrize("diagnostic,code", [("Duration: 00:00:10.00\nVideo: h264, 1280x720, 24 fps\nframe=240", 0),
    ("Duration: 00:00:05.00\nVideo: h264, 1280x720, 24 fps\nframe=120", 0),
    ("Duration: 00:00:10.00\nVideo: h264, 1280x720, 24 fps\nframe=240", 1)])
def test_decoder_gate(monkeypatch, diagnostic, code):
    monkeypatch.setattr(jobs, "get_ffmpeg_binary", lambda: "test-only")
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **kw: SimpleNamespace(stderr=diagnostic, returncode=code))
    if code == 0 and "10.00" in diagnostic:
        jobs.verify_candidate(Path("fixture.mp4"))
    else:
        with pytest.raises(Exception, match="10秒"):
            jobs.verify_candidate(Path("fixture.mp4"))


def test_real_connector_claims_decrypts_and_reports_pending_review(client, db, queued_case, tmp_path, monkeypatch):
    s = queued_case
    created = create(client, s); assert created.status_code == 200
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "generation-worker" / "src"))
    from lingnian_worker.short_scene_service import ShortSceneApi, run_once
    api = ShortSceneApi("http://testserver", TOKEN)
    api._client.close()
    api._client = client
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    config = SimpleNamespace(work_dir=tmp_path / "worker", node_token=TOKEN, comfyui_url="http://127.0.0.1:8188",
                             comfy_timeout_seconds=30, ffmpeg="not-called", ffprobe="not-called")
    try:
        # Actual connector, package decoder/receiver, and execute input-review gate.
        # Fresh directory has no input approval, so ComfyUI must not be contacted.
        state = run_once(config, api)
        assert state["status"] == "awaiting_input_review"
        listing = client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-jobs").json()
        assert listing[0]["status"] == "awaiting_input_review"
        root = config.work_dir / "native-short-scene" / created.json()["id"]
        assert (root / "inputs/recording.wav").read_bytes() == s.audio
        assert not list(root.rglob("job.json")) and not list(root.rglob("input-review.json"))
        assert not list(root.rglob("candidate.mp4"))
        assert TOKEN not in (root / "delivery.json").read_text()
    finally:
        client.headers.pop("Authorization", None)


def test_legacy_resume_requires_bound_family_review(client, db, queued_case):
    create(client, queued_case)
    task = claim(client).json()
    progress = task["package_url"].replace("/package", "/progress")
    client.patch(progress, headers=headers(task), json={"stage": "awaiting_input_review", "percent": 5})
    url = f"/api/v1/short-scene-jobs/{task['id']}/resume"
    assert client.post(url, json={"authorize_resume": False}).status_code == 409
    assert client.post(url, json={"authorize_resume": True}).status_code == 409
    assert claim(client).json() is None


def test_actual_decoder_accepts_ten_second_av_fixture(tmp_path):
    import subprocess
    path = tmp_path / "ten-second-test.mp4"
    made = subprocess.run([jobs.get_ffmpeg_binary(), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "color=c=gray:s=1280x720:r=24:d=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=10",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "10", str(path)],
        capture_output=True, timeout=40)
    assert made.returncode == 0
    jobs.verify_candidate(path)
