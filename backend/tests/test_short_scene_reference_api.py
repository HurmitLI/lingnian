"""Isolated synthetic inputs; no model/GPU generation or real family photos."""
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.api import short_scene_reference_routes as routes
from app.models import FamilyArchive, InterviewTurn, MemorySession, ModelConsentEvent, ShortSceneJob, ShortScenePlan
from app.services.auth import AuthContext, current_auth
from test_short_scene_api import setup_scene  # noqa: F401
from test_short_scene_selection import prepared  # noqa: F401
from test_short_scene_jobs import queued_case  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_binding import source  # noqa: F401


def setup_reference(client, scene, mode="illustrative"):
    inp = client.get(f"/api/v1/memory-sessions/{scene.sid}/short-scene-selection-input",
                     params={"reference_mode": mode}).json()
    plan = client.post(scene.url, json={**scene.payload, "reference_mode": mode,
                                       "input_sha256": inp["input_sha256"]}).json()
    options = {"input_sha256": inp["input_sha256"]}
    if mode == "user_photo": options.update(photo_use_authorized=True, subject_consent=True)
    return scene.url + f'/{plan["id"]}/reference-input', options


def photo_bytes():
    output = io.BytesIO()
    Image.new("RGB", (512, 704), "gray").save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("mode", ["illustrative", "user_photo"])
def test_exact_reference_preview_is_family_only_and_not_generation(client, db, setup_scene, monkeypatch, mode):
    s = setup_scene
    url, options = setup_reference(client, s, mode)
    count = db.scalar(select(func.count()).select_from(ModelConsentEvent))
    original = routes.tempfile.mkdtemp
    folders = []
    def track(**kwargs):
        folder = original(**kwargs); folders.append(Path(folder)); return folder
    monkeypatch.setattr(routes.tempfile, "mkdtemp", track)
    data = photo_bytes()
    files = {"photo": ("../../untrusted-name.png", data, "image/png")} if mode == "user_photo" else None
    response = client.post(url, data={"options": json.dumps(options)}, files=files)
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result["generation_submitted"] and not result["external_call_ready"]
    assert not result["photo_persisted"] and result["brief"]["renderer_version"] == 2
    assert result["brief"]["generation_authorized"] is False
    assert result["brief"]["answers"][0]["text"] == s.args["raw_answers"]["turn"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-lingnian-no-snapshot"] == "1"
    assert not any(folder.exists() for folder in folders)
    assert "untrusted-name" not in response.text and "lingnian-reference-preview-" not in response.text
    if files: assert result["brief"]["source_photo"]["sha256"] == hashlib.sha256(data).hexdigest()
    else: assert result["brief"]["source_photo"] is None
    assert len(s.calls) == 1  # Only the explicitly mocked preceding selection.
    assert db.scalar(select(func.count()).select_from(ModelConsentEvent)) == count
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 0
    repeat = client.post(url, data={"options": json.dumps(options)}, files=files)
    assert repeat.json()["brief_sha256"] == result["brief_sha256"]


@pytest.mark.parametrize("damage,status", [("stale",409), ("question",409), ("plan",409),
                                           ("extra",422), ("string_bool",422), ("hidden_photo",409)])
def test_bad_preview_does_not_grant_permission(client, db, setup_scene, damage, status):
    s = setup_scene; url, options = setup_reference(client, s)
    files = None
    if damage == "stale": options["input_sha256"] = "f" * 64
    elif damage == "question":
        db.get(InterviewTurn, "turn").question_text = "changed"; db.commit()
    elif damage == "plan":
        db.scalar(select(ShortScenePlan)).status = "outcome_unknown"; db.commit()
    elif damage == "extra": options["authorize_node_delivery"] = True
    elif damage == "string_bool": options["subject_consent"] = "true"
    else: files = {"photo": ("private.png", photo_bytes(), "image/png")}
    assert client.post(url, data={"options": json.dumps(options)}, files=files).status_code == status
    assert len(s.calls) == 1


@pytest.mark.parametrize("damage,status", [("missing",409), ("permission",409), ("subject",409),
                                           ("invalid",409), ("oversize",413)])
def test_photo_input_fails_closed(client, setup_scene, monkeypatch, damage, status):
    url, options = setup_reference(client, setup_scene, "user_photo")
    files = {"photo": ("photo.png", photo_bytes(), "image/png")}
    if damage == "missing": files = None
    elif damage == "permission": options["photo_use_authorized"] = False
    elif damage == "subject": options["subject_consent"] = False
    elif damage == "invalid": files = {"photo": ("photo.png", b"not-an-image", "image/png")}
    else: monkeypatch.setattr(routes, "PHOTO_LIMIT", 8)
    result = client.post(url, data={"options": json.dumps(options)}, files=files)
    assert result.status_code == status, result.text
    assert "Traceback" not in result.text and "lingnian-reference-preview-" not in result.text


def test_other_family_cannot_prepare_reference(client, setup_scene):
    url, options = setup_reference(client, setup_scene)
    token = current_auth.set(AuthContext("another", "other-family", "owner"))
    try:
        assert client.post(url, data={"options": json.dumps(options)}).status_code == 404
    finally: current_auth.reset(token)


def test_other_session_in_same_family_cannot_reuse_plan(client, db, setup_scene):
    s = setup_scene; url, options = setup_reference(client, s)
    old = db.get(MemorySession, s.sid)
    other = client.post("/api/v1/memory-sessions", json={"elder_id": old.elder_id, "life_stage": "童年"}).json()
    assert client.post(url.replace(s.sid, other["id"]), data={"options": json.dumps(options)}).status_code == 404


def test_sensitive_archive_cannot_preview_without_encryption(client, db, setup_scene):
    s = setup_scene; url, options = setup_reference(client, s)
    db.get(FamilyArchive, s.family_id).data_classification = "authorized_sensitive"
    db.commit()
    result = client.post(url, data={"options": json.dumps(options)})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "SHORT_REFERENCE_ENCRYPTION_REQUIRED"


def test_encrypted_saved_plan_can_prepare_without_persisting_plaintext(client, db, queued_case):
    s = queued_case
    plan = db.get(ShortScenePlan, s.plan_id)
    assert plan.request_payload == {} and plan.result_payload == {}
    count = db.scalar(select(func.count()).select_from(ModelConsentEvent))
    result = client.post(s.url.replace("/jobs", "/reference-input"),
                         data={"options": json.dumps({"input_sha256": s.consent["input_sha256"]})})
    assert result.status_code == 200, result.text
    assert result.json()["brief"]["candidate"]["text"] == "我在站台等车。"
    db.expire_all()
    assert plan.request_payload == {} and plan.result_payload == {}
    assert db.scalar(select(func.count()).select_from(ModelConsentEvent)) == count
    assert db.scalar(select(func.count()).select_from(ShortSceneJob)) == 0
