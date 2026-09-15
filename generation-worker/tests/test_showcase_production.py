"""Blueprint checks only; fake hashes below never leave isolated test objects."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from lingnian_worker.film_contract import validate_film_contract
from lingnian_worker.models import PackageError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_showcase_film", ROOT / "scripts/prepare_showcase_film.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def inputs():
    blueprint = json.loads((ROOT / "examples/blue-bag-70-production.json").read_text())
    source = "\n".join(q for item in blueprint["shots"] + blueprint["scenes"] for q in item["source_quotes"])
    manifest = {"source_text": source, "audio_sha256": blueprint["narration_sha256"], "duration_seconds": 69.9}
    quote = blueprint["shots"][0]["source_quotes"][0]
    alignment = {"narration_sha256": blueprint["narration_sha256"], "narration_cues": [
        {"text": quote, "source_quote": quote, "start_seconds": n * 10,
         "end_seconds": min((n + 1) * 10, 69.9)} for n in range(7)]}
    return blueprint, manifest, alignment


def test_draft_keeps_missing_assets_explicit_and_cannot_run(inputs):
    draft = MODULE.build_draft(*inputs)
    assert draft["executable"] is False
    assert len(draft["segments"]) == 15
    assert draft["segments"][-1]["end_seconds"] == 70
    assert sum(s["anchor_sha256"] is None for s in draft["scenes"]) == 5
    assert draft["scenes"][0]["reference_status"] == "existing_image_pending_scene_review"
    with pytest.raises(PackageError):
        validate_film_contract(draft)
    draft["format"] = "lingnian-continuous-film"
    with pytest.raises(PackageError):
        validate_film_contract(draft)


def test_blueprint_state_and_render_fields_fit_contract_once_assets_exist_in_test_only(inputs):
    draft = MODULE.build_draft(*inputs)
    simulated = copy.deepcopy(draft)
    simulated["format"] = "lingnian-continuous-film"
    for scene in simulated["scenes"]:
        scene["anchor_sha256"] = "a" * 64
        scene["usage_authorized"] = True
    validate_film_contract(simulated)
    assert draft["scenes"][1]["anchor_sha256"] is None


def test_later_scene_prop_scope_survives_draft_preparation(inputs):
    from lingnian_worker.film_contract import render_plan_for_shot

    draft = MODULE.build_draft(*inputs)
    scene = next(s for s in draft["scenes"] if s["id"] == "later-departure")
    scoped = render_plan_for_shot(draft, 10)
    assert scoped["render_negative"] == scene["render_negative"]
    assert "empty" in scoped["render_bible"]
    for prompt in (scoped["render_bible"], scoped["render_negative"]):
        assert "bag" not in prompt.lower()
        assert "strap" not in prompt.lower()
    assert draft["render_negative"] == inputs[0]["render_negative"]


def test_arrival_context_does_not_override_current_shot_state(inputs):
    from lingnian_worker.continuity import segment_prompt
    from lingnian_worker.film_contract import render_plan_for_shot

    draft = MODULE.build_draft(*inputs)
    context = render_plan_for_shot(draft, 14)
    # A scene can span opening and already-open states. The persistent context
    # must not instruct a future opening or insist the bag is always closed.
    assert "will open" not in context["render_bible"].lower()
    assert "closed blue bag" not in context["render_bible"].lower()
    waiting = {**draft["segments"][13],
               "render_action": "Keep hands beside the bag. Do not open it in this shot."}
    prompt = segment_prompt(context, waiting)
    assert "Do not open it in this shot" in prompt
    assert "will open" not in prompt.lower()
    # The authored opening action is still used by the original shot14.
    assert "Then open the same bag" in segment_prompt(context, draft["segments"][13])


@pytest.mark.parametrize("defect", ["audio", "quote", "sum", "order", "scene_gap"])
def test_mismatched_production_inputs_rejected(inputs, defect):
    blueprint, manifest, alignment = inputs
    if defect == "audio": alignment["narration_sha256"] = "0" * 64
    elif defect == "quote": blueprint["shots"][0]["source_quotes"] = ["不在原文的新故事"]
    elif defect == "sum": blueprint["shots"][0]["duration_seconds"] = 5
    elif defect == "order": blueprint["shots"][0]["id"] = 99
    else: blueprint["scenes"][1]["start_shot"] = 4
    with pytest.raises(PackageError):
        MODULE.build_draft(blueprint, manifest, alignment)
