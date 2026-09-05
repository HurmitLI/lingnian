import json
from pathlib import Path

import pytest

from lingnian_worker.continuity import build_wan_graph, normalize_segment, segment_prompt, validate_plan
from lingnian_worker.models import PackageError


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def plan():
    return json.loads((ROOT / "generation-worker/examples/1982-departure-continuity.json").read_text())


def test_fictional_plan_has_grounded_story_and_linked_states(plan):
    validate_plan(plan, ROOT / "frontend/public/showcase/shen-suqin-station-1982.png")
    assert sum(s["duration_seconds"] for s in plan["segments"]) == 15


@pytest.mark.parametrize("defect", ["identity", "direction", "quote", "real", "reference", "duration", "jump"])
def test_invalid_continuity_plan_is_rejected(plan, defect):
    if defect == "identity":
        del plan["film_bible"]["character"]
    elif defect == "direction":
        del plan["film_bible"]["journey"]
    elif defect == "quote":
        plan["segments"][0]["source_quotes"] = ["她已经离开无锡。"]
    elif defect == "real":
        plan["fictional_only"] = False
    elif defect == "reference":
        plan["reference_sha256"] = "0" * 64
    elif defect == "duration":
        plan["segments"][0]["duration_seconds"] = 2
    else:
        plan["segments"][1]["before"] = "她已经到达另一个城市。"
    with pytest.raises(PackageError):
        validate_plan(plan, ROOT / "frontend/public/showcase/shen-suqin-station-1982.png")


def test_wan_graph_really_binds_reference_and_native_video_length(plan):
    graph = build_wan_graph(plan, plan["segments"][0], image_name="anchor.png", prefix="local/trial")
    assert graph["4"]["inputs"]["image"] == "anchor.png"
    assert graph["7"]["inputs"]["start_image"] == ["4", 0]
    assert graph["7"]["inputs"]["length"] == 121
    assert (graph["7"]["inputs"]["width"], graph["7"]["inputs"]["height"]) == (1280, 704)
    assert graph["11"]["inputs"]["fps"] == 24
    assert graph["1"]["inputs"]["unet_name"].startswith("wan2.2")
    assert not any("AnimateDiff" in node["class_type"] for node in graph.values())
    assert plan["source_text"] in graph["5"]["inputs"]["text"]
    for value in plan["film_bible"].values():
        assert value in graph["5"]["inputs"]["text"]
    with pytest.raises(PackageError):
        build_wan_graph(plan, plan["segments"][0], image_name="", prefix="local/trial")


def test_short_generation_cannot_be_looped_to_meet_requested_length(tmp_path):
    class ShortRenderer:
        def probe(self, path):
            return {"duration": 2.0}

    with pytest.raises(PackageError, match="禁止循环"):
        normalize_segment(ShortRenderer(), tmp_path / "short.mp4", tmp_path / "out.mp4", 5)


def test_normalization_never_uses_loop_or_slow_motion(monkeypatch, tmp_path):
    class Renderer:
        ffmpeg = "ffmpeg"

        def probe(self, path):
            return {"duration": 5.04}

    commands = []
    monkeypatch.setattr("lingnian_worker.continuity.checked_run", commands.append)
    normalize_segment(Renderer(), tmp_path / "raw.mp4", tmp_path / "out.mp4", 5)
    assert "-stream_loop" not in commands[0]
    assert not any("setpts" in arg or "tpad" in arg for arg in commands[0])


def test_each_segment_uses_whole_film_bible_and_current_action(plan):
    first = segment_prompt(plan, plan["segments"][0])
    second = segment_prompt(plan, plan["segments"][1])
    assert first != second
    assert plan["segments"][0]["after"] in second
    assert plan["film_bible"]["prop"] in first and plan["film_bible"]["prop"] in second
