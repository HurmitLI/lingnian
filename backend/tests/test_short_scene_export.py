"""Synthetic PCM/images only; HTTP packaging checks are not video acceptance."""
import hashlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

import pytest
from sqlalchemy import select

from app.api import short_scene_export_routes as exports
from app.api import short_scene_routes as selection
from app.core.config import get_settings
from app.models import InterviewTurn, MediaAsset, ModelConsentEvent, Transcript
from app.services.auth import AuthContext, current_auth
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401


@pytest.fixture
def export_case(client, db, bundle_args, monkeypatch):
    family = client.post("/api/v1/families", json={"display_name": "制作包测试", "idempotency_key": "export-fixture"}).json()
    elder = client.post("/api/v1/elder-profiles", json={"family_id": family["id"], "display_name": "外公", "preferred_name": "外公"}).json()
    session = client.post("/api/v1/memory-sessions", json={"elder_id": elder["id"], "life_stage": "工作"}).json()
    sid = session["id"]
    asset_root = get_settings().resolved_asset_root
    shutil.copyfile(bundle_args["normalized_audio"], asset_root / "fixture.wav")
    audio_data = (asset_root / "fixture.wav").read_bytes()
    db.add(MediaAsset(id="source", session_id=sid, kind="audio_original", relative_path="fixture.wav",
                      original_filename="测试.wav", mime_type="audio/wav", size_bytes=len(audio_data),
                      sha256=hashlib.sha256(audio_data).hexdigest()))
    text = bundle_args["raw_answers"]["turn"]
    db.add(InterviewTurn(id="turn", session_id=sid, turn_index=1, question_text=bundle_args["raw_questions"]["turn"],
                         raw_answer_text=text, corrected_answer_text=text, asr_provider="fixture", asr_model="fixture"))
    db.add(Transcript(session_id=sid, raw_text=text, corrected_text=text, asr_provider="fixture", asr_model="fixture",
                      asr_metadata=bundle_args["metadata"]))
    db.commit()
    monkeypatch.setattr(selection, "get_llm_provider", lambda: SimpleNamespace(
        provider_name="qwen", model_name="test", select_short_scene=lambda *args: bundle_args["model_response"]))
    preview = client.get(f"/api/v1/memory-sessions/{sid}/short-scene-selection-input").json()
    response = client.post(f"/api/v1/memory-sessions/{sid}/short-scene-plans", json={
        "input_sha256": preview["input_sha256"], "idempotency_key": "export-plan", "authorize_text_send": True})
    assert response.status_code == 200, response.text
    plan_id = response.json()["id"]
    roots = []
    original = exports.tempfile.mkdtemp
    def tracked(*args, **kwargs):
        directory = original(*args, **kwargs)
        roots.append(Path(directory))
        return directory
    monkeypatch.setattr(exports.tempfile, "mkdtemp", tracked)
    return SimpleNamespace(
        url=f"/api/v1/memory-sessions/{sid}/short-scene-plans/{plan_id}/production-package",
        family_id=family["id"], sid=sid, plan_id=plan_id, roots=roots,
        image=bundle_args["reference"].read_bytes(), audio=audio_data,
        consent={"input_sha256": preview["input_sha256"], "authorize_material_export": True,
                 "reference_rights_confirmed": True, "subject_consent": True, "no_impersonation": True})


def submit(client, case, *, consent=None, image=None, url=None):
    return client.post(url or case.url, data={"consent": json.dumps(case.consent if consent is None else consent)},
                       files={"reference": ("../../untrusted-name.png", case.image if image is None else image, "image/png")})


def test_export_saved_interview_and_model_plan_exactly(client, db, export_case, monkeypatch):
    s = export_case
    monkeypatch.setattr(selection, "get_llm_provider", lambda: pytest.fail("Export cannot invoke a model"))
    result = submit(client, s)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-lingnian-generation-ready"] == "false"
    assert hashlib.sha256(result.content).hexdigest() == result.headers["x-lingnian-package-sha256"]
    with zipfile.ZipFile(io.BytesIO(result.content)) as z:
        assert z.read("recording.wav") == s.audio
        plan = json.loads(z.read("plan.json"))
        assert plan["source_text"] == "我在站台等车。后来我到了无锡。"
        assert plan["status"] == "awaiting_input_review" and not plan["generation_ready"]
        assert plan["reference"]["kind"] == "generated_reference"
        assert "input-review.json" not in z.namelist()
    events = db.scalars(select(ModelConsentEvent).where(ModelConsentEvent.purpose == "short_scene_material_export")).all()
    assert len(events) == 1 and events[0].input_sha256 == hashlib.sha256(result.content).hexdigest()
    assert s.roots and all(not p.exists() for p in s.roots)


@pytest.mark.parametrize("field", ["authorize_material_export", "reference_rights_confirmed", "subject_consent", "no_impersonation"])
def test_all_export_permissions_required(client, db, export_case, field):
    s = export_case
    assert submit(client, s, consent={**s.consent, field: False}).status_code == 409
    assert not s.roots
    assert db.scalar(select(ModelConsentEvent).where(ModelConsentEvent.purpose == "short_scene_material_export")) is None


@pytest.mark.parametrize("damage", ["boolean", "extra_path", "stale_hash", "changed_question", "bad_image", "tampered_audio"])
def test_invalid_or_stale_material_fails_closed(client, db, export_case, damage):
    s = export_case
    consent, image = dict(s.consent), s.image
    if damage == "boolean": consent["subject_consent"] = "true"
    elif damage == "extra_path": consent["recording_path"] = "/private/file"
    elif damage == "stale_hash": consent["input_sha256"] = "f" * 64
    elif damage == "changed_question":
        db.get(InterviewTurn, "turn").question_text = "另一个问题"; db.commit()
    elif damage == "bad_image": image = b"not a png"
    else: (get_settings().resolved_asset_root / "fixture.wav").write_bytes(b"changed")
    result = submit(client, s, consent=consent, image=image)
    assert result.status_code in {409, 422}, result.text
    assert all(not p.exists() for p in s.roots)
    assert db.scalar(select(ModelConsentEvent).where(ModelConsentEvent.purpose == "short_scene_material_export")) is None


def test_cross_family_and_cross_session_export_denied(client, export_case):
    s = export_case
    token = current_auth.set(AuthContext("outsider", "another-family", "owner"))
    try:
        assert submit(client, s).status_code == 404
    finally:
        current_auth.reset(token)
    assert submit(client, s, url=s.url.replace(s.sid, "not-this-session")).status_code == 404
    assert not s.roots


def test_cloud_no_snapshot_no_download(client, export_case, monkeypatch):
    s = export_case
    settings = get_settings()
    monkeypatch.setattr(exports, "get_settings", lambda: SimpleNamespace(
        formal_auth_required=True, resolved_asset_root=settings.resolved_asset_root))
    monkeypatch.setattr(exports, "persist_configured_database_snapshot", lambda settings: None)
    result = submit(client, s)
    assert result.status_code == 503, result.text
    assert all(not p.exists() for p in s.roots)


def test_encrypted_archive_exports_verified_plaintext_and_cleans_temporary_files(client, db, export_case):
    from app.main import app
    from app.models import ArchiveSecurity, FamilyArchive, ShortScenePlan
    from app.models.entities import now_utc
    from app.services.security import InMemorySecretStore, get_secret_store, get_family_key_manager
    from app.services.security.archive_encryption import activate_archive_encryption
    s = export_case
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        key, _ = get_family_key_manager(s.family_id, store).get_or_create()
        security = ArchiveSecurity(family_id=s.family_id, recovery_package_created_at=now_utc(), recovery_verified_at=now_utc())
        db.add(security); db.commit()
        activate_archive_encryption(db, family=db.get(FamilyArchive, s.family_id), metadata=security,
                                    master_key=key, target_classification="authorized_sensitive", settings=get_settings())
        db.expire_all()
        assert db.get(ShortScenePlan, s.plan_id).result_payload == {}
        encrypted = (get_settings().resolved_asset_root / "fixture.wav").read_bytes()
        assert encrypted != s.audio
        result = submit(client, s)
        assert result.status_code == 200, result.text
        with zipfile.ZipFile(io.BytesIO(result.content)) as z:
            assert z.read("recording.wav") == s.audio
        assert (get_settings().resolved_asset_root / "fixture.wav").read_bytes() == encrypted
        assert all(not p.exists() for p in s.roots)
    finally:
        app.dependency_overrides.pop(get_secret_store, None)


def test_reference_stream_limit_does_not_leave_materials(client, db, export_case, monkeypatch):
    s = export_case
    monkeypatch.setitem(exports.LIMITS, "reference.png", 16)
    assert submit(client, s).status_code == 413
    assert all(not p.exists() for p in s.roots)
    assert db.scalar(select(ModelConsentEvent).where(ModelConsentEvent.purpose == "short_scene_material_export")) is None
