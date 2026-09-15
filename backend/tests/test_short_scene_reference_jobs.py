"""Encrypted reference intents with synthetic text/images; no real GPU or API calls."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from sqlalchemy import func, select

from app.api import short_scene_reference_job_routes as jobs
from app.api.routes import family_master_key
from app.core.config import get_settings
from app.main import app
from app.models import FamilyArchive, MediaAsset, ModelConsentEvent, ShortSceneJob, ShortSceneReferenceJob
from app.services.auth import AuthContext, current_auth
from app.services.security import decrypt_media_file, get_secret_store, media_context
from test_short_scene_jobs import queued_case, claim  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_binding import source  # noqa: F401


def prepare(client, s, mode="illustrative"):
    base = f"/api/v1/memory-sessions/{s.sid}/short-scene-plans"
    pid, inp = s.plan_id, s.consent["input_sha256"]
    if mode == "user_photo":
        inp = client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-selection-input",
                         params={"reference_mode": mode}).json()["input_sha256"]
        selected = client.post(base, json={"input_sha256": inp, "reference_mode": mode,
                                           "idempotency_key": "photo-plan", "authorize_text_send": True})
        assert selected.status_code == 200, selected.text
        pid = selected.json()["id"]
    options = {"input_sha256": inp, "photo_use_authorized": mode == "user_photo",
               "subject_consent": mode == "user_photo"}
    files = {"photo": ("private.png", s.image, "image/png")} if mode == "user_photo" else None
    response = client.post(f"{base}/{pid}/reference-input", data={"options": json.dumps(options)}, files=files)
    assert response.status_code == 200, response.text
    consent = {**options, "brief_sha256": response.json()["brief_sha256"],
               "idempotency_key": "reference-trial-key", "authorize_reference_generation": True,
               "authorize_node_delivery": True, "no_impersonation": True}
    return f"{base}/{pid}/reference-jobs", consent, files


def submit(client, prepared, **changes):
    url, consent, files = prepared
    return client.post(url, data={"consent": json.dumps({**consent, **changes})}, files=files)


@pytest.mark.parametrize("mode", ["illustrative", "user_photo"])
def test_encrypted_intent_binds_exact_brief_and_photo_without_audio(client, db, queued_case, tmp_path, monkeypatch, mode):
    s = queued_case; prepared = prepare(client, s, mode)
    first = submit(client, prepared)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "queued" and first.json()["generation_submitted"] is False
    assert first.json()["node_dispatch_available"] is True
    assert submit(client, prepared).json()["id"] == first.json()["id"]
    assert submit(client, prepared, idempotency_key="different-key").json()["id"] == first.json()["id"]
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 1
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 0
    item = db.scalar(select(ShortSceneReferenceJob)); asset = db.get(MediaAsset, item.package_asset_id)
    path = get_settings().resolved_asset_root / asset.relative_path
    assert asset.encryption_version == 1 and path.read_bytes().startswith(b"NNMEDIA1")
    store = app.dependency_overrides[get_secret_store]()
    key = family_master_key(db.get(FamilyArchive, s.family_id), store)
    target = tmp_path / "private-decrypted.zip"
    result = decrypt_media_file(path, target, key, associated_data=media_context(s.family_id, asset.id))
    assert result.plaintext_sha256 == item.bundle_sha256
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "generation-worker" / "src"))
    from lingnian_worker.short_scene_reference_bundle import receive_reference_bundle
    received = receive_reference_bundle(target, expected_sha256=item.bundle_sha256,
        expected_brief_sha256=item.brief_sha256, output_dir=tmp_path / "node-received")
    assert received["generation_authorized"] is False and received["comfyui_called"] is False
    assert (received["photo_path"] is None) == (mode == "illustrative")
    with zipfile.ZipFile(target) as archive:
        names = {"manifest.json", "brief.json"} | ({"source.png"} if mode == "user_photo" else set())
        assert set(archive.namelist()) == names
        manifest = json.loads(archive.read("manifest.json"))
        brief = json.loads(archive.read("brief.json"))
        assert manifest["brief_sha256"] == brief["brief_sha256"] == item.brief_sha256
        assert brief["brief"]["generation_authorized"] is False
        assert not first.json()["visual_accepted"]
        for name, facts in manifest["files"].items():
            data = archive.read(name)
            assert len(data) == facts["size_bytes"] and hashlib.sha256(data).hexdigest() == facts["sha256"]
        if mode == "user_photo": assert archive.read("source.png") == s.image
    event = db.get(ModelConsentEvent, item.consent_id)
    assert event.purpose == "short_scene_reference_generation" and event.input_sha256 == item.brief_sha256
    assert event.used_at and event.one_time
    assert db.scalar(select(func.count()).select_from(ModelConsentEvent).where(
        ModelConsentEvent.purpose == "short_scene_reference_generation")) == 1
    assert claim(client).json() is None  # Existing native-video queue cannot claim references.
    recovered = client.get(prepared[0] + "/by-request/reference-trial-key")
    assert recovered.json()["id"] == item.id
    assert "package_asset_id" not in recovered.text and "lease_token" not in recovered.text
    assert client.get(prepared[0] + "/by-request/different-key").json() is None
    assert client.get(prepared[0] + "/by-brief/" + item.brief_sha256).json()["id"] == item.id
    assert client.get(f"/api/v1/memory-sessions/{s.sid}/short-reference-jobs").json()[0]["id"] == item.id


@pytest.mark.parametrize("field", ["authorize_reference_generation", "authorize_node_delivery", "no_impersonation"])
def test_consent_must_be_explicit(client, db, queued_case, field):
    prepared = prepare(client, queued_case)
    assert submit(client, prepared, **{field: False}).status_code == 409
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 0
    assert db.scalar(select(func.count()).select_from(ModelConsentEvent).where(
        ModelConsentEvent.purpose == "short_scene_reference_generation")) == 0


def test_changed_photo_cannot_reuse_preview_authorization(client, db, queued_case):
    from PIL import Image
    url, consent, files = prepare(client, queued_case, "user_photo")
    changed = io.BytesIO(); Image.new("RGB", (512,512), "blue").save(changed, "PNG")
    result = client.post(url, data={"consent": json.dumps(consent)},
                         files={"photo": ("private.png", changed.getvalue(), "image/png")})
    assert result.status_code == 409 and result.json()["error"]["code"] == "SHORT_REFERENCE_BRIEF_CHANGED"
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 0


def test_cancel_is_idempotent_and_never_requeues(client, db, queued_case):
    prepared = prepare(client, queued_case); item = submit(client, prepared).json()
    cancel = f'/api/v1/short-reference-jobs/{item["id"]}/cancel'
    assert client.post(cancel).json()["status"] == "cancelled"
    assert client.post(cancel).json()["status"] == "cancelled"
    assert submit(client, prepared).json()["status"] == "cancelled"
    assert submit(client, prepared, idempotency_key="new-key-after-cancel").json()["status"] == "cancelled"
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 1


def test_interrupted_reference_requires_node_check_before_requeueing_same_job(client, db, queued_case):
    prepared = prepare(client, queued_case); item = submit(client, prepared).json()
    job = db.get(ShortSceneReferenceJob, item["id"])
    job.status = "interrupted"; job.error_code = "LEASE_EXPIRED"; job.progress_percent = 20
    db.commit()
    url = f'/api/v1/short-reference-jobs/{job.id}/resume'
    rejected = client.post(url, json={"authorize_resume": True, "node_checked_no_gpu_submission": False})
    assert rejected.status_code == 409
    resumed = client.post(url, json={"authorize_resume": True, "node_checked_no_gpu_submission": True})
    assert resumed.status_code == 200 and resumed.json()["status"] == "queued"
    db.refresh(job)
    assert job.error_code is None and job.progress_percent == 0 and job.lease_expires_at is None


def test_other_family_cannot_create_read_recover_or_cancel(client, queued_case):
    prepared = prepare(client, queued_case); item = submit(client, prepared).json()
    token = current_auth.set(AuthContext("outsider", "another-family", "owner"))
    try:
        assert submit(client, prepared).status_code == 404
        assert client.get(prepared[0]+"/by-request/reference-trial-key").status_code == 404
        assert client.get(prepared[0]+"/by-brief/"+prepared[1]["brief_sha256"]).status_code == 404
        assert client.get(f"/api/v1/memory-sessions/{queued_case.sid}/short-reference-jobs").status_code == 404
        assert client.post(f'/api/v1/short-reference-jobs/{item["id"]}/cancel').status_code == 404
        assert client.post(f'/api/v1/short-reference-jobs/{item["id"]}/resume', json={
            "authorize_resume": True, "node_checked_no_gpu_submission": True}).status_code == 404
    finally: current_auth.reset(token)


def test_failed_snapshot_preserves_single_recoverable_intent(client, db, queued_case, monkeypatch):
    prepared = prepare(client, queued_case)
    root = get_settings().resolved_asset_root
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(formal_auth_required=True, resolved_asset_root=root))
    monkeypatch.setattr(jobs, "persist_configured_database_snapshot", lambda _: None)
    assert submit(client, prepared).status_code == 503
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 1
    assert client.get(prepared[0]+"/by-request/reference-trial-key").status_code == 503
    monkeypatch.setattr(jobs, "persist_configured_database_snapshot", lambda _: "test-snapshot")
    assert client.get(prepared[0]+"/by-request/reference-trial-key").status_code == 200
    assert submit(client, prepared).status_code == 200
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 1


def test_existing_key_cannot_bind_a_second_plan(client, db, queued_case):
    first = prepare(client, queued_case)
    assert submit(client, first).status_code == 200
    second = prepare(client, queued_case, "user_photo")
    response = submit(client, second)
    assert response.status_code == 409 and response.json()["error"]["code"] == "SHORT_REFERENCE_KEY_CONFLICT"
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 1


def test_storage_failure_does_not_leave_plaintext_or_grant(client, db, queued_case, monkeypatch):
    prepared = prepare(client, queued_case)
    def fail(*args, **kwargs):
        raise OSError("private-path-and-secret-must-not-appear")
    monkeypatch.setattr(jobs, "encrypt_media_file", fail)
    result = submit(client, prepared)
    assert result.status_code == 503 and "private-path" not in result.text
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 0
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind == "short_reference_package")) == 0
    assert all(not root.exists() for root in queued_case.roots)


def test_simultaneous_same_request_commits_one_intent(client, db, queued_case, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    prepared = prepare(client, queued_case)
    entered, release, lock = Event(), Event(), Lock()
    original = jobs.encrypt_media_file
    count = 0
    def paused(*args, **kwargs):
        nonlocal count
        with lock:
            count += 1
            first = count == 1
        if first:
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, "encrypt_media_file", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, client, prepared)
        try:
            assert entered.wait(10)
            second = submit(client, prepared)
        finally:
            release.set()
        result = first.result()
    assert result.status_code == second.status_code == 200
    assert result.json()["id"] == second.json()["id"]
    assert db.scalar(select(func.count()).select_from(ShortSceneReferenceJob)) == 1
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind == "short_reference_package")) == 1
    assert len(list(get_settings().resolved_asset_root.glob("assets/derived/*/*.lnref"))) == 1
