"""Only synthetic interviews and mocked provider calls, isolated test database."""
from copy import deepcopy
from datetime import timedelta
import json
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.api import short_scene_routes as routes
from app.main import app
from app.models import (ArchiveSecurity, FamilyArchive, GenerativeMediaRequest, InterviewTurn,
                        MediaAsset, ModelConsentEvent, ShortScenePlan, Transcript)
from app.models.entities import now_utc
from app.services.auth import AuthContext, current_auth
from app.services.security import InMemorySecretStore, get_secret_store, get_family_key_manager
from app.services.security.archive_encryption import activate_archive_encryption
from app.core.config import get_settings
from test_short_scene_selection import prepared  # noqa: F401


@pytest.fixture
def setup_scene(client, db, prepared, monkeypatch):
    args, request, response = prepared
    family = client.post("/api/v1/families", json={"display_name": "虚构选景家庭", "idempotency_key": "short-scene"}).json()
    elder = client.post("/api/v1/elder-profiles", json={"family_id": family["id"], "display_name": "妈妈", "preferred_name": "妈妈"}).json()
    session = client.post("/api/v1/memory-sessions", json={"elder_id": elder["id"], "life_stage": "工作"}).json()
    sid = session["id"]
    db.add(MediaAsset(id="secret-id", session_id=sid, kind="audio_original", relative_path="fixture.wav",
                      original_filename="fixture.wav", mime_type="audio/wav", size_bytes=1, sha256="a" * 64))
    db.add(InterviewTurn(id="turn", session_id=sid, turn_index=1, question_text="什么事？",
                         raw_answer_text=args["raw_answers"]["turn"], corrected_answer_text=args["raw_answers"]["turn"],
                         asr_provider="fixture", asr_model="fixture"))
    db.add(Transcript(session_id=sid, raw_text=args["raw_answers"]["turn"], corrected_text=args["raw_answers"]["turn"],
                      asr_provider="fixture", asr_model="fixture", asr_metadata=args["metadata"]))
    db.commit()
    from app.services.memory.short_scene_selection import prepare_selection_request
    args = {**args, "raw_questions": {"turn": "什么事？"}, "interview_context": {
        "subject_label": "妈妈", "narrator_label": "妈妈", "narrator_is_subject": True,
    }}
    request = prepare_selection_request(**args)
    calls = []

    def complete(system, user):
        calls.append((system, user))
        return deepcopy(response)

    provider = SimpleNamespace(provider_name="qwen", model_name="test-only", select_short_scene=complete)
    monkeypatch.setattr(routes, "get_llm_provider", lambda: provider)
    return SimpleNamespace(sid=sid, family_id=family["id"], args=args, request=request, response=response,
                           calls=calls, provider=provider,
                           url=f"/api/v1/memory-sessions/{sid}/short-scene-plans",
                           payload={"reference_mode": "illustrative", "input_sha256": request["input_sha256"],
                                    "idempotency_key": "first-request", "authorize_text_send": True})


def test_preview_is_read_only_and_contains_exact_text(client, db, setup_scene):
    s = setup_scene
    result = client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-selection-input").json()
    assert result["input_sha256"] == s.request["input_sha256"]
    assert result["payload"]["answers"][0]["text"] == s.args["raw_answers"]["turn"]
    assert "secret-id" not in json.dumps(result) and "system_prompt" not in result
    assert s.calls == [] and db.scalar(select(func.count()).select_from(ModelConsentEvent)) == 0


def test_single_call_persisted_result_and_purpose_consent(client, db, setup_scene):
    s = setup_scene
    for key in ("first-request", "first-request", "different-request"):
        result = client.post(s.url, json={**s.payload, "idempotency_key": key})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "awaiting_scene_context_review"
        assert not result.json()["generation_ready"]
    assert len(s.calls) == 1
    records = client.get(s.url).json()
    assert len(records) == 1 and records[0]["result"]["semantic_accepted"] is False
    consent = db.scalar(select(ModelConsentEvent))
    assert consent.purpose == "short_scene_selection" and consent.used_at and consent.one_time
    assert consent.input_sha256 == s.request["input_sha256"]
    assert db.scalar(select(func.count()).select_from(GenerativeMediaRequest)) == 0


@pytest.mark.parametrize("change,code", [("deny", 409), ("string_bool", 422), ("stale", 409), ("photo", 409), ("extra", 422)])
def test_bad_submission_never_sends_or_consumes(client, db, setup_scene, change, code):
    s = setup_scene
    payload = dict(s.payload)
    if change == "deny": payload["authorize_text_send"] = False
    elif change == "string_bool": payload["authorize_text_send"] = "true"
    elif change == "stale": payload["input_sha256"] = "b" * 64
    elif change == "photo": payload["reference_mode"] = "user_photo"
    else: payload["model_consent_event_id"] = "a-story-consent-cannot-be-reused"
    assert client.post(s.url, json=payload).status_code == code
    assert not s.calls and db.scalar(select(func.count()).select_from(ModelConsentEvent)) == 0


@pytest.mark.parametrize("failure,status", [("timeout", "outcome_unknown"), ("bad_output", "invalid_proposal"), ("unsuitable", "no_suitable_scene")])
def test_failure_and_unsuitable_do_not_automatically_retry(client, setup_scene, failure, status):
    s = setup_scene
    def respond(*args):
        s.calls.append(args)
        if failure == "timeout": raise TimeoutError("secret-key-and-private-interview")
        if failure == "bad_output": return {"visual_accepted": True}
        return {"decision": "unsuitable", "reason": "没有单一场景"}
    s.provider.select_short_scene = respond
    result = client.post(s.url, json=s.payload)
    assert result.status_code == 200 and result.json()["status"] == status
    assert "secret-key" not in result.text
    assert client.post(s.url, json=s.payload).json()["status"] == status
    assert len(s.calls) == 1


def test_other_family_cannot_read_or_send(client, setup_scene):
    s = setup_scene
    token = current_auth.set(AuthContext("other-user", "other-family", "owner"))
    try:
        assert client.get(s.url).status_code == 404
        assert client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-selection-input").status_code == 404
        assert client.post(s.url, json=s.payload).status_code == 404
    finally:
        current_auth.reset(token)
    assert not s.calls


def test_mock_does_not_pretend_selection_works(client, db, setup_scene):
    s = setup_scene
    s.provider.provider_name = "mock"
    assert client.post(s.url, json=s.payload).status_code == 503
    assert not s.calls and db.scalar(select(func.count()).select_from(ShortScenePlan)) == 0


def test_interrupted_request_not_reported_running_forever(client, db, setup_scene):
    s = setup_scene
    client.post(s.url, json=s.payload)
    item = db.scalar(select(ShortScenePlan))
    item.status = "dispatching"
    item.deadline_at = now_utc() - timedelta(seconds=1)
    db.commit()
    assert client.get(s.url).json()[0]["status"] == "outcome_unknown"
    assert client.post(s.url, json=s.payload).json()["status"] == "outcome_unknown"
    assert len(s.calls) == 1


def test_existing_key_cannot_bind_changed_input(client, setup_scene):
    s = setup_scene
    client.post(s.url, json=s.payload)
    result = client.post(s.url, json={**s.payload, "input_sha256": "b" * 64})
    assert result.status_code == 409 and len(s.calls) == 1


def test_modified_question_requires_new_input_preview_before_send(client, db, setup_scene):
    s = setup_scene
    turn = db.get(InterviewTurn, "turn")
    turn.question_text = "这是您自己还是别人等车？"
    db.commit()
    response = client.post(s.url, json=s.payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SHORT_SCENE_INPUT_CHANGED"
    assert not s.calls


def test_relatives_interview_preserves_distinct_narrator_and_subject(client, db, setup_scene):
    from app.models import MemorySession, Person
    s = setup_scene
    narrator = Person(family_id=s.family_id, display_name="女儿", role="family_member")
    db.add(narrator)
    db.flush()
    session = db.get(MemorySession, s.sid)
    session.narrator_person_id = narrator.id
    db.commit()
    result = client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-selection-input").json()
    assert result["payload"]["interview_context"] == {
        "subject_label": "妈妈", "narrator_label": "女儿", "narrator_is_subject": False,
    }
    assert result["input_sha256"] != s.payload["input_sha256"]
    assert not s.calls


def test_encryption_activation_includes_saved_plans_and_new_calls(client, db, setup_scene):
    s = setup_scene
    client.post(s.url, json=s.payload)
    family = db.get(FamilyArchive, s.family_id)
    fixture_audio = b"synthetic-encryption-test-only"
    (get_settings().resolved_asset_root / "fixture.wav").write_bytes(fixture_audio)
    asset = db.get(MediaAsset, "secret-id")
    asset.size_bytes = len(fixture_audio)
    asset.sha256 = hashlib.sha256(fixture_audio).hexdigest()
    db.commit()
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        key, _ = get_family_key_manager(family.id, store).get_or_create()
        security = ArchiveSecurity(family_id=family.id, recovery_package_created_at=now_utc(), recovery_verified_at=now_utc())
        db.add(security)
        db.commit()
        activate_archive_encryption(db, family=family, metadata=security, master_key=key,
                                    target_classification="authorized_sensitive", settings=get_settings())
        db.expire_all()
        saved = db.scalar(select(ShortScenePlan))
        assert saved.request_payload == {} and saved.result_payload == {}
        assert client.get(s.url).json()[0]["result"]["selection"]["scene"]["facts"][-1]["value"] == "蓝布包"
        inp = client.get(f"/api/v1/memory-sessions/{s.sid}/short-scene-selection-input?reference_mode=user_photo").json()
        result = client.post(s.url, json={**s.payload, "idempotency_key": "second-request", "reference_mode": "user_photo", "input_sha256": inp["input_sha256"]})
        assert result.status_code == 200, result.text
        db.expire_all()
        assert all(p.request_payload == {} and p.result_payload == {} for p in db.scalars(select(ShortScenePlan)))
    finally:
        app.dependency_overrides.pop(get_secret_store, None)


def test_cloud_intent_must_be_durable_before_sending(client, db, setup_scene, monkeypatch):
    s = setup_scene
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(formal_auth_required=True))
    monkeypatch.setattr(routes, "persist_configured_database_snapshot", lambda _: None)
    assert client.post(s.url, json=s.payload).status_code == 503
    assert not s.calls
    assert db.scalar(select(ShortScenePlan)).status == "not_dispatched"


def test_qwen_selection_sdk_has_no_retries_or_extra_material():
    from app.services.llm.provider import QwenLLMProvider
    options, sent = {}, {}
    class Client:
        def with_options(self, **kwargs):
            options.update(kwargs)
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"decision":"unsuitable","reason":"测试"}'))])
    provider = object.__new__(QwenLLMProvider)
    provider.client, provider.model_name = Client(), "fixture"
    assert provider.select_short_scene("system", "text")["decision"] == "unsuitable"
    assert options == {"max_retries": 0, "timeout": 30}
    assert sent["messages"] == [{"role": "system", "content": "system"}, {"role": "user", "content": "text"}]


def test_double_click_during_provider_call_only_dispatches_once(client, setup_scene):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    s = setup_scene
    entered, release = Event(), Event()
    def complete(*args):
        s.calls.append(args)
        entered.set()
        assert release.wait(10)
        return deepcopy(s.response)
    s.provider.select_short_scene = complete
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, s.url, json=s.payload)
        try:
            assert entered.wait(10)
            duplicate = client.post(s.url, json=s.payload)
            assert duplicate.status_code == 200 and duplicate.json()["status"] == "dispatching"
            assert len(s.calls) == 1
        finally:
            release.set()
        assert first.result().json()["status"] == "awaiting_scene_context_review"
