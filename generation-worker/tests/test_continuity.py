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


def test_trial_carries_tail_forward_and_resumes_verified_segments(plan, tmp_path, monkeypatch):
    from lingnian_worker import continuity as c

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    reference = ROOT / "frontend/public/showcase/shen-suqin-station-1982.png"
    uploads, graphs = [], []

    class FakeComfy:
        def __init__(self, *args, **kwargs):
            pass

        def upload_image(self, path):
            uploads.append(path)
            return path.name

        def run_workflow(self, graph, *, output_path):
            graphs.append(graph)
            output_path.write_bytes(f"raw-{len(graphs)}".encode())
            return output_path

        def close(self):
            pass

    info = {n["class_type"]: {} for n in c.build_wan_graph(plan, plan["segments"][0], image_name="ref", prefix="test").values()}
    for node, field, model in (("UNETLoader", "unet_name", "unet"), ("CLIPLoader", "clip_name", "clip"), ("VAELoader", "vae_name", "vae")):
        info[node] = {"input": {"required": {field: [[c.MODEL_FILES[model]]]}}}

    class FakeHttp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path):
            assert path == "/object_info"
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return info

    class Renderer:
        def assemble(self, clips, output, *, audio, duration):
            assert audio is None
            output.write_bytes(b"".join(p.read_bytes() for p in clips))

        def probe(self, path):
            return {"duration": 15}

    def media_stub(renderer, raw, target, seconds):
        target.write_bytes(raw.read_bytes() + target.name.encode())

    monkeypatch.setattr(c, "ComfyUiClient", FakeComfy)
    monkeypatch.setattr(c.httpx, "Client", FakeHttp)
    for name in ("normalize_segment", "tail_frame", "contact_sheet"):
        monkeypatch.setattr(c, name, media_stub)
    kwargs = dict(base_url="http://127.0.0.1:8188", renderer=Renderer(), segments=3)
    result = c.run_trial(plan_path, reference, tmp_path / "output", **kwargs)
    assert uploads == [reference, result.parent / "segment-01-tail.png", result.parent / "segment-02-tail.png"]
    assert graphs[1]["4"]["inputs"]["image"] == "segment-01-tail.png"
    c.run_trial(plan_path, reference, tmp_path / "output", **kwargs)
    assert len(graphs) == 3  # Verified restart performs no extra generation.
    (result.parent / "segment-01-tail.png").write_bytes(b"corrupted")
    c.run_trial(plan_path, reference, tmp_path / "output", **kwargs)
    assert len(graphs) == 6  # A broken anchor invalidates the entire later chain.
    report = json.loads((result.parent / "report.json").read_text())
    assert report["review_status"] == "not_reviewed"
    assert report["audio"] == "silent_visual_test"


def test_trial_rejects_nonlocal_endpoint_before_reading_files(tmp_path):
    from lingnian_worker.continuity import run_trial
    from lingnian_worker.models import ConfigurationError

    with pytest.raises(ConfigurationError, match="本机"):
        run_trial(tmp_path / "missing", tmp_path / "missing", tmp_path, base_url="https://example.com", renderer=None, segments=1)
