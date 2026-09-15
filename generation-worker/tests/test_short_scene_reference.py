"""Synthetic stills and fake ComfyUI only; never visual review evidence."""
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from PIL import Image

from lingnian_worker.short_scene import _hash
from lingnian_worker.continuity import digest
from lingnian_worker.comfyui import ComfyUiClient
from lingnian_worker.models import ConfigurationError, PackageError, TemporaryWorkerError
from lingnian_worker.short_scene_reference import MODELS, make_graph, prepare_reference, validate_brief


@pytest.fixture
def brief():
    body = {"format": "lingnian-short-reference", "version": 1, "selection_input_sha256": "a" * 64,
            "reference_mode": "illustrative", "source_photo": None, "identity_claim": "illustrative_not_verified_likeness",
            "candidate": {"id": "b" * 64, "text": "我在厨房坐着。", "duration_seconds": 9},
            "answers": [{"text": "我在厨房坐着。后来才去车站。", "question": "在什么地方？"}],
            "interview_context": {"subject_label": "外公", "narrator_label": "妈妈", "narrator_is_subject": False},
            "scene": {"context_summary": "出发前在家", "opening_state": "坐在厨房椅子上", "render_bible": "One adult person seated in a kitchen.",
                      "source_quotes": ["我在厨房坐着。"]},
            "target": {"width": 1280, "height": 704, "images": 1}, "generation_authorized": False, "visual_accepted": False}
    return {"brief": body, "brief_sha256": _hash(body)}


class FakeClient:
    base_url = "http://127.0.0.1:8188"
    def __init__(self):
        self.calls = []; self.uploads = []; self.preflights = 0
    def check_reference_graph(self, graph):
        self.preflights += 1
    def upload_image(self, photo):
        self.uploads.append(digest(photo)); return photo.name
    def run_workflow(self, graph, *, output_path, journal_path):
        self.calls.append(deepcopy(graph))
        journal_path.write_text(json.dumps({"prompt_id": "fake-reference-id"}))
        Image.new("RGB", (1280, 704), "gray").save(output_path)
        return output_path


def execute(brief, tmp_path, client, photo=None):
    return prepare_reference(brief, photo=photo, work_dir=tmp_path / "references", client=client,
                             authorized_brief_sha256=brief["brief_sha256"])


def test_uses_current_scene_not_old_train_fixture_and_one_native_still(brief):
    graph = make_graph(validate_brief(brief, None))
    assert "kitchen" in graph["4"]["inputs"]["text"] and "厨房椅子" in graph["4"]["inputs"]["text"]
    assert "车站" not in graph["4"]["inputs"]["text"] and "train" not in graph["4"]["inputs"]["text"]
    assert "20" not in graph and graph["6"]["inputs"] == {"width": 1280, "height": 704, "batch_size": 1}
    assert graph["7"] == make_graph(brief["brief"])["7"]
    assert sum(n["class_type"] == "SamplerCustomAdvanced" for n in graph.values()) == 1


def test_generate_once_and_cached_result_never_becomes_visual_pass(brief, tmp_path):
    client = FakeClient()
    first = execute(brief, tmp_path, client)
    assert execute(brief, tmp_path, client) == first and len(client.calls) == 1
    assert first["status"] == "awaiting_reference_review" and first["visual_accepted"] is False
    assert first["generation_ready"] is False and client.uploads == []
    assert not list(tmp_path.rglob("input-review.json"))
    Path(first["reference_path"]).write_bytes(b"changed")
    with pytest.raises(PackageError): execute(brief, tmp_path, client)
    assert len(client.calls) == 1


def test_portrait_is_latent_reference_not_stretched_output(brief, tmp_path):
    photo = tmp_path / "portrait.png"; Image.new("RGB", (704, 1280), "gray").save(photo)
    body = brief["brief"]
    body.update(reference_mode="user_photo", identity_claim="photo_reference_not_historical_footage",
                source_photo={"sha256": digest(photo), "usage_authorized": True, "identity_claim": "photo_reference_not_historical_footage"})
    brief["brief_sha256"] = _hash(body)
    client = FakeClient(); result = execute(brief, tmp_path, client, photo)
    graph = client.calls[0]
    assert graph["8"]["inputs"]["positive"] == ["23", 0]
    assert graph["21"]["class_type"] == "ImageScaleToTotalPixels"
    assert result["source_photo"]["sha256"] == digest(photo) and client.uploads == [digest(photo)]
    with Image.open(photo) as original: assert original.size == (704, 1280)


def version_two(brief):
    body = brief["brief"]
    body["renderer_version"] = 2
    body["answers"][0]["text"] = "那年我五十八岁。我在厨房坐着。后来才去车站。"
    body["scene"]["facts"] = [
        {"field": field, "status": "known" if field == "character" else "unknown",
         "value": "五十八岁" if field == "character" else None,
         "source_quotes": ["那年我五十八岁。"] if field == "character" else []}
        for field in ("character", "location", "era", "wardrobe", "prop")]
    brief["brief_sha256"] = _hash(body)
    return brief


def test_new_renderer_is_explicitly_bound_not_silent_upgrade(brief, tmp_path):
    old = deepcopy(brief)
    old_graph = make_graph(validate_brief(old, None))
    changed = version_two(brief)
    graph = make_graph(validate_brief(changed, None))
    prompt = graph["4"]["inputs"]["text"]
    assert "五十八岁" in prompt and "AT THE RECORDED EVENT" in prompt
    assert "season alone is not evidence" in prompt
    assert "后来才去车站" not in prompt
    assert old_graph == make_graph(validate_brief(old, None))
    assert "Source-backed" not in old_graph["4"]["inputs"]["text"]
    assert changed["brief_sha256"] != old["brief_sha256"]
    client = FakeClient()
    with pytest.raises(PackageError):
        prepare_reference(changed, photo=None, work_dir=tmp_path, client=client,
                          authorized_brief_sha256=old["brief_sha256"])
    assert client.calls == []


@pytest.mark.parametrize("version", [0, 3, True, "2", None])
def test_unknown_renderer_cannot_submit(brief, tmp_path, version):
    brief["brief"]["renderer_version"] = version
    brief["brief_sha256"] = _hash(brief["brief"])
    client = FakeClient()
    with pytest.raises(PackageError): execute(brief, tmp_path, client)
    assert client.calls == []


@pytest.mark.parametrize("damage", ["invented_age", "missing", "unknown_value", "bad_quotes"])
def test_v2_facts_stay_source_bound(brief, tmp_path, damage):
    version_two(brief)
    facts = brief["brief"]["scene"]["facts"]
    if damage == "invented_age": facts[0]["value"] = "八十岁"
    elif damage == "missing": facts.pop()
    elif damage == "unknown_value": facts[1]["value"] = "车站"
    else: facts[0]["source_quotes"] = [123]
    brief["brief_sha256"] = _hash(brief["brief"])
    client = FakeClient()
    with pytest.raises(PackageError): execute(brief, tmp_path, client)
    assert client.calls == []


@pytest.mark.parametrize("damage", ["no_permission", "remote", "hash", "quote", "hidden_photo", "fake_pass"])
def test_reject_before_any_gpu_or_upload(brief, tmp_path, damage):
    client = FakeClient(); photo = None
    authorized = brief["brief_sha256"]
    if damage == "no_permission": authorized = None
    elif damage == "remote": client.base_url = "https://outside.invalid"
    elif damage == "hash": brief["brief"]["scene"]["opening_state"] = "changed"
    elif damage == "quote":
        brief["brief"]["scene"]["source_quotes"] = ["后来才去车站。"]
        brief["brief_sha256"] = authorized = _hash(brief["brief"])
    elif damage == "hidden_photo": photo = tmp_path / "extra.png"
    else:
        brief["brief"]["visual_accepted"] = True
        brief["brief_sha256"] = authorized = _hash(brief["brief"])
    with pytest.raises(PackageError):
        prepare_reference(brief, photo=photo, work_dir=tmp_path, client=client, authorized_brief_sha256=authorized)
    assert not client.calls and not client.uploads


def test_interrupted_reference_reuses_same_actual_graph_and_journal(brief, tmp_path):
    client = FakeClient()
    def interrupted(graph, *, output_path, journal_path):
        journal_path.write_text('{"stage":"submission_unknown"}')
        raise TemporaryWorkerError("unknown")
    client.run_workflow = interrupted
    with pytest.raises(TemporaryWorkerError): execute(brief, tmp_path, client)
    actual = next(tmp_path.rglob("actual-api.json")); original = actual.read_bytes()
    def recover_only(graph, *, output_path, journal_path):
        assert journal_path.read_text() == '{"stage":"submission_unknown"}'
        assert graph == json.loads(original)
        raise TemporaryWorkerError("still unknown; no submission")
    client.run_workflow = recover_only
    with pytest.raises(TemporaryWorkerError): execute(brief, tmp_path, client)
    assert actual.read_bytes() == original and client.preflights == 1


@pytest.mark.parametrize("damage", [None, "missing_model", "missing_node", "busy", "invalid_queue"])
def test_readonly_preflight_never_installs_or_submits(brief, tmp_path, damage):
    graph = make_graph(brief["brief"])
    schema = {node["class_type"]: {"input": {"required": {}}} for node in graph.values()}
    for klass, (field, model) in MODELS.items(): schema[klass]["input"]["required"][field] = [[model]]
    if damage == "missing_model": schema["UNETLoader"]["input"]["required"]["unet_name"] = [[]]
    if damage == "missing_node": del schema["Flux2Scheduler"]
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/object_info": return httpx.Response(200, json=schema)
        return httpx.Response(200, json={} if damage == "invalid_queue" else {"queue_running": [1] if damage == "busy" else [], "queue_pending": []})
    client = ComfyUiClient("http://127.0.0.1:8188", tmp_path / "unused", timeout_seconds=60)
    client._client.close(); client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    try:
        if damage:
            with pytest.raises((ConfigurationError, TemporaryWorkerError)): client.check_reference_graph(graph)
        else: client.check_reference_graph(graph)
    finally: client.close()
    assert all(method == "GET" for method, _ in calls)
