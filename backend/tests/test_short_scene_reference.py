from pathlib import Path
import json

import pytest
from PIL import Image

from app.services.memory.short_scene_reference import prepare_reference_brief
from app.services.memory.short_scene_selection import prepare_selection_request
from test_short_scene_selection import prepared  # noqa: F401


@pytest.mark.parametrize("mode", ["illustrative", "user_photo"])
def test_brief_preserves_full_context_and_worker_accepts_same_contract(prepared, tmp_path, monkeypatch, mode):
    args, _, response = prepared
    args["reference_mode"] = mode
    args["raw_questions"] = {"turn": "您讲的是谁？"}
    args["interview_context"] = {"subject_label": "外公", "narrator_label": "妈妈", "narrator_is_subject": False}
    request = prepare_selection_request(**args)
    photo = None
    if mode == "user_photo":
        photo = tmp_path / "synthetic-source.png"
        Image.new("RGB", (704, 1280)).save(photo)
    envelope = prepare_reference_brief(request, response, input_sha256=request["input_sha256"], photo=photo,
                                       photo_use_authorized=mode == "user_photo")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "generation-worker" / "src"))
    from lingnian_worker.short_scene_reference import validate_brief, make_graph
    body = validate_brief(envelope, photo)
    assert body["renderer_version"] == 2
    assert body["generation_authorized"] is False and envelope["generation_ready"] is False
    assert body["interview_context"]["narrator_label"] == "妈妈"
    assert "后来到达无锡" in body["answers"][0]["text"]
    assert "source-photo" not in str(body)
    assert ("20" in make_graph(body)) == (mode == "user_photo")
    assert type(body["candidate"]["duration_seconds"]) is int
    assert validate_brief(json.loads(json.dumps(envelope)), photo) == body


def test_photo_mode_needs_explicit_source_permission(prepared, tmp_path):
    args, _, response = prepared
    args["reference_mode"] = "user_photo"
    request = prepare_selection_request(**args)
    photo = tmp_path / "fixture.png"; Image.new("RGB", (512, 512)).save(photo)
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        prepare_reference_brief(request, response, input_sha256=request["input_sha256"], photo=photo)


def test_brief_cannot_reuse_changed_or_unsuitable_selection(prepared):
    _, request, response = prepared
    with pytest.raises(ValueError, match="INPUT_CHANGED"):
        prepare_reference_brief(request, response, input_sha256="f" * 64)
    with pytest.raises(ValueError, match="NO_SELECTED_SCENE"):
        prepare_reference_brief(request, {"decision": "unsuitable", "reason": "不适合", "candidate_id": None, "scene": None}, input_sha256=request["input_sha256"])
