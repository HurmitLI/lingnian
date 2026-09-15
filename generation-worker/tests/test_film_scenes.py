import json
import copy

from PIL import Image
import pytest

from test_film_contract import film
from test_film_executor import runtime, review
from test_film_watch import Clock
from lingnian_worker.continuity import digest, build_wan_graph
from lingnian_worker.film_contract import validate_film_contract, render_plan_for_shot
from lingnian_worker.film_executor import SCENE_CHECKS, execution_binding, scene_reference_status, stage_scene_reference
from lingnian_worker.film_assembly import reviewed_clips
from lingnian_worker.film_watch import watch_film
from lingnian_worker.models import PackageError


@pytest.fixture
def scenes(runtime, tmp_path):
    executor, args = runtime
    plan = args["plan"]
    plan.update(version=2, reference_policy="reviewed_scene_anchors_then_previous_last_frame")
    images = []
    for index, color in enumerate(("blue", "green")):
        p = tmp_path / f"scene-{index}.png"
        Image.new("RGB", (64, 64), color).save(p)
        images.append(p)
    plan["segments"][8]["before"] = "new-era-opening"
    plan["scenes"] = [{"id": f"scene-{i}", "start_shot": i * 8 + 1, "end_shot": (i + 1) * 8,
                       "from_state": "state-0" if i == 0 else "state-8",
                       "opening_state": "state-0" if i == 0 else "new-era-opening",
                       "transition": "opening" if i == 0 else "time_cut", "transition_reason": "Explicit fictional time change",
                       "identity_notes": "Same fictional person; different age is an illustration, not verified likeness",
                       "render_bible": f"Scene {i} locked character and setting.", "anchor_sha256": digest(images[i]),
                       "parent_reference_sha256": plan["reference"]["sha256"], "usage_authorized": True,
                       "identity_claim": "illustrative_scene_not_historical_footage",
                       "source_quotes": [plan["source_text"]]} for i in range(2)]
    validate_film_contract(plan)
    binding = execution_binding(plan, executor.comfyui.base_url)
    folder = executor.work_dir / binding[:24]

    def stage(index, *, approve=True):
        scene = plan["scenes"][index]
        target = folder / "scene-references" / scene["id"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "reference.png").write_bytes(images[index].read_bytes())
        _, gate = scene_reference_status(scene, folder, binding)
        if approve:
            (target / "review.json").write_text(json.dumps({"binding": gate["review_binding"], "decision": "accepted",
                "reviewer": "assistant_visual", "notes": "Mock image contract test only, not a real portrait review",
                "opening_state_evidence": {"expected": scene["opening_state"],
                                           "observed": "Synthetic test state only; no real visual acceptance",
                                           "matches": True},
                "checks": {key: True for key in SCENE_CHECKS}}))
        return target

    return executor, args, folder, stage, images


def test_unprepared_and_unreviewed_scene_never_generates(scenes):
    executor, args, folder, stage, images = scenes
    assert executor.execute(**args)["status"] == "awaiting_scene_reference"
    stage(0, approve=False)
    assert executor.execute(**args)["status"] == "awaiting_scene_reference_review"
    assert executor.comfyui.graphs == []


@pytest.mark.parametrize("defect", ["missing", "stale", "blank", "nontext", "unchecked", "mismatch"])
def test_scene_needs_explicit_current_opening_state_review_before_gpu(scenes, defect):
    executor, args, folder, stage, _ = scenes
    path = stage(0) / "review.json"
    review_record = json.loads(path.read_text())
    evidence = review_record["opening_state_evidence"]
    if defect == "missing": review_record.pop("opening_state_evidence")
    elif defect == "stale": evidence["expected"] = "old state"
    elif defect == "blank": evidence["observed"] = "   "
    elif defect == "nontext": evidence["observed"] = ["closed"]
    elif defect == "unchecked": evidence["matches"] = 1
    else: evidence["matches"] = False
    path.write_text(json.dumps(review_record))
    saved = path.read_bytes()
    result = executor.execute(**args)
    assert result["status"] == ("scene_reference_rejected" if defect == "mismatch"
                                else "awaiting_scene_reference_review")
    assert result["required_opening_state"] == args["plan"]["scenes"][0]["opening_state"]
    assert executor.comfyui.graphs == []
    assert path.read_bytes() == saved


def test_scene_negative_override_is_isolated_and_preserves_binding(scenes):
    executor, args, _, _, _ = scenes
    plan = args["plan"]
    plan["render_negative"] = "changing bag shape, extra straps"
    previous_binding = execution_binding(plan, executor.comfyui.base_url)
    plan["scenes"][1]["render_negative"] = "extra people, speaking"
    saved = copy.deepcopy(plan)
    validate_film_contract(plan)
    first = render_plan_for_shot(plan, 1)
    later = render_plan_for_shot(plan, 9)
    assert first["render_negative"] == plan["render_negative"]
    assert later["render_negative"] == "extra people, speaking"
    graph = build_wan_graph(later, plan["segments"][8], image_name="later.png", prefix="test")
    assert "extra people, speaking" in graph["6"]["inputs"]["text"]
    assert "bag" not in graph["6"]["inputs"]["text"]
    assert plan == saved
    assert execution_binding(plan, executor.comfyui.base_url) != previous_binding


@pytest.mark.parametrize("value", ["", "   ", None, [], "x" * 2001])
def test_invalid_scene_negative_is_rejected_before_gpu(scenes, value):
    executor, args, _, _, _ = scenes
    args["plan"]["scenes"][1]["render_negative"] = value
    with pytest.raises(PackageError, match="反向约束"):
        executor.execute(**args)
    assert executor.comfyui.graphs == []


def test_reviewed_scene_switches_input_and_render_context_without_breaking_intrascene_chain(scenes):
    executor, args, folder, stage, images = scenes
    stage(0)
    for _ in range(8):
        review(executor.execute(**args))
    result = executor.execute(**args)
    assert result["status"] == "awaiting_scene_reference" and result["scene_id"] == "scene-1"
    assert len(executor.comfyui.graphs) == 8
    stage(1)
    ninth = executor.execute(**args)
    assert ninth["shot"] == 9
    assert executor.comfyui.inputs[8] == images[1].read_bytes()
    assert executor.comfyui.inputs[1] == b"raw-1tail.png"
    assert "Scene 1 locked" in executor.comfyui.graphs[8]["5"]["inputs"]["text"]
    assert "Scene 0 locked" not in executor.comfyui.graphs[8]["5"]["inputs"]["text"]
    assert "Continue the input image" in executor.comfyui.graphs[8]["5"]["inputs"]["text"]
    review(ninth)
    for _ in range(7):
        review(executor.execute(**args))
    assert executor.execute(**args)["status"] == "ready_for_assembly"
    assert len(reviewed_clips(args["plan"], folder, args["reference"], base_url=executor.comfyui.base_url)) == 16
    (folder / "scene-references/scene-1/review.json").unlink()
    with pytest.raises(PackageError, match="场景参考"):
        reviewed_clips(args["plan"], folder, args["reference"], base_url=executor.comfyui.base_url)


@pytest.mark.parametrize("defect", ["gap", "overlap", "path", "parent", "consent", "transition", "bridge", "quote", "missing", "opening"])
def test_bad_scene_contract_prevents_gpu(scenes, defect):
    executor, args, folder, stage, images = scenes
    scene = args["plan"]["scenes"][1]
    if defect == "gap": scene["start_shot"] = 10
    elif defect == "overlap": scene["start_shot"] = 8
    elif defect == "path": scene["id"] = "../private"
    elif defect == "parent": scene["parent_reference_sha256"] = "0" * 64
    elif defect == "consent": scene["usage_authorized"] = False
    elif defect == "transition": scene["transition"] = "random"
    elif defect == "bridge": scene["from_state"] = "unrelated"
    elif defect == "quote": scene["source_quotes"] = ["invented"]
    elif defect == "opening": args["plan"]["scenes"][0]["opening_state"] = "wrong"
    else: args["plan"]["scenes"].pop()
    with pytest.raises(PackageError):
        executor.execute(**args)
    assert executor.comfyui.graphs == []


def test_changed_scene_image_rejected_despite_existing_review(scenes):
    executor, args, folder, stage, images = scenes
    target = stage(0)
    (target / "reference.png").write_bytes(images[1].read_bytes())
    with pytest.raises(PackageError, match="摘要"):
        executor.execute(**args)
    assert executor.comfyui.graphs == []


def test_watch_does_not_approve_missing_scene_and_respects_deadline(scenes):
    executor, args, folder, stage, images = scenes
    clock = Clock()
    result = watch_film(executor, **args, clock=clock, sleep=clock.sleep, max_seconds=30)
    assert result["status"] == "paused"
    assert executor.comfyui.graphs == []


def test_watch_continues_when_scene_and_shot_reviews_arrive(scenes):
    executor, args, folder, stage, images = scenes
    clock = Clock()

    def inspect_fixture(seconds):
        result = json.loads((folder / "status.json").read_text())
        if result["status"] == "awaiting_scene_reference":
            stage(0 if result["scene_id"] == "scene-0" else 1)
        else:
            review(result)
        clock.sleep(seconds)

    result = watch_film(executor, **args, clock=clock, sleep=inspect_fixture,
                        assembler=lambda *a, **k: folder / "mock.mp4")
    assert result["status"] == "awaiting_final_review"
    assert len(executor.comfyui.graphs) == 16


def test_rejected_scene_stops_instead_of_retrying_random_video(scenes):
    executor, args, folder, stage, images = scenes
    target = stage(0)
    value = json.loads((target / "review.json").read_text())
    value["decision"] = "rejected"
    (target / "review.json").write_text(json.dumps(value))
    assert watch_film(executor, **args)["status"] == "scene_reference_rejected"
    assert executor.comfyui.graphs == []


def test_import_does_not_approve_or_call_gpu_and_never_overwrites(scenes):
    executor, args, folder, stage, images = scenes
    result = stage_scene_reference(args["plan"], scene_id="scene-0", image=images[0], work_dir=executor.work_dir)
    assert result["status"] == "awaiting_scene_reference_review"
    assert not list(folder.rglob("review.json"))
    again = stage_scene_reference(args["plan"], scene_id="scene-0", image=images[0], work_dir=executor.work_dir)
    assert again["review_binding"] == result["review_binding"]
    target = folder / "scene-references/scene-0/reference.png"
    assert target.read_bytes() == images[0].read_bytes()
    target.write_bytes(b"changed")
    with pytest.raises(PackageError):
        stage_scene_reference(args["plan"], scene_id="scene-0", image=images[0], work_dir=executor.work_dir)
    assert target.read_bytes() == b"changed"
    assert executor.comfyui.graphs == []


def test_scene_reference_cannot_be_non_image_with_a_matching_hash(scenes):
    executor, args, folder, stage, images = scenes
    images[0].write_bytes(b"not-a-png")
    args["plan"]["scenes"][0]["anchor_sha256"] = digest(images[0])
    with pytest.raises(PackageError, match="无法验证"):
        stage_scene_reference(args["plan"], scene_id="scene-0", image=images[0], work_dir=executor.work_dir)
    assert not list(executor.work_dir.glob("*/scene-references/*/reference.png"))


def test_scene_plan_cannot_be_silently_downgraded_to_old_tail_chain(scenes):
    executor, args, folder, stage, images = scenes
    args["plan"].update(version=1, reference_policy="first_reference_then_previous_last_frame")
    with pytest.raises(PackageError, match="旧版"):
        executor.execute(**args)
    assert executor.comfyui.graphs == []
