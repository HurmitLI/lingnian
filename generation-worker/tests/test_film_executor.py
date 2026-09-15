import json
from pathlib import Path

import pytest

from test_film_contract import film
from lingnian_worker import film_executor as module
from lingnian_worker.continuity import digest
from lingnian_worker.film_executor import ContinuousFilmExecutor, REVIEW_CHECKS, read_review
from lingnian_worker.models import PackageError, TemporaryWorkerError


@pytest.fixture
def runtime(film, tmp_path, monkeypatch):
    reference, audio = tmp_path / "photo.png", tmp_path / "voice.wav"
    reference.write_bytes(b"reference")
    audio.write_bytes(b"voice")
    film["reference"]["sha256"] = digest(reference)
    film["narration"]["sha256"] = digest(audio)

    class Renderer:
        has_audio = True
        duration = 63.5

        def probe(self, path):
            return {"has_audio": self.has_audio, "duration": 64, "audio_duration": self.duration}

    class Comfy:
        base_url = "http://127.0.0.1:8188"

        def __init__(self):
            self.graphs, self.inputs = [], []

        def upload_image(self, path):
            self.inputs.append(path.read_bytes())
            return path.name

        def run_workflow(self, graph, *, output_path, journal_path):
            self.graphs.append(graph)
            output_path.write_bytes(f"raw-{len(self.graphs)}".encode())
            return output_path

    def media_stub(renderer, raw, target, seconds):
        target.write_bytes(raw.read_bytes() + target.name.encode())

    for name in ("normalize_segment", "tail_frame", "contact_sheet"):
        monkeypatch.setattr(module, name, media_stub)
    executor = ContinuousFilmExecutor(renderer=Renderer(), comfyui=Comfy(), work_dir=tmp_path / "jobs")
    return executor, dict(plan=film, reference=reference, narration=audio)


def review(result, decision="accepted", **overrides):
    folder = Path(result["job_dir"]) / f"shot-{result['shot']:02d}" / f"attempt-{result['attempt']}"
    value = {"binding": result["review_binding"], "decision": decision,
             "reviewer": "assistant_visual", "notes": "MOCK protocol review; not real visual evidence.",
             "checks": {key: True for key in REVIEW_CHECKS}, **overrides}
    (folder / "review.json").write_text(json.dumps(value))


def test_pauses_for_review_and_resume_does_not_regenerate(runtime):
    executor, args = runtime
    result = executor.execute(**args)
    assert result["status"] == "awaiting_visual_review"
    assert result["shot"] == 1
    assert executor.execute(**args)["review_binding"] == result["review_binding"]
    assert len(executor.comfyui.graphs) == 1
    review(result)
    second = executor.execute(**args)
    assert second["shot"] == 2
    assert executor.comfyui.inputs[1] == b"raw-1tail.png"


def test_all_hash_bound_reviews_reach_assembly_not_final_acceptance(runtime):
    executor, args = runtime
    for index in range(1, 17):
        result = executor.execute(**args)
        assert result["shot"] == index
        review(result)
    result = executor.execute(**args)
    assert result["status"] == "ready_for_assembly"
    assert result["duration_seconds"] == 64
    assert len(result["clips"]) == 16
    assert result["final_visual_accepted"] is False
    executor.execute(**args)
    assert len(executor.comfyui.graphs) == 16


def test_rejection_retries_only_same_shot_at_most_three_times(runtime):
    executor, args = runtime
    for attempt in range(1, 4):
        result = executor.execute(**args)
        assert (result["shot"], result["attempt"]) == (1, attempt)
        review(result, "rejected")
    assert executor.execute(**args)["status"] == "retry_limit_reached"
    assert executor.execute(**args)["status"] == "retry_limit_reached"
    assert len(executor.comfyui.graphs) == 3
    assert len({g["9"]["inputs"]["seed"] for g in executor.comfyui.graphs}) == 3


@pytest.mark.parametrize("change", ["raw", "tail", "review", "checkpoint"])
def test_corruption_cannot_extend_a_chain(runtime, change):
    executor, args = runtime
    result = executor.execute(**args)
    review(result)
    folder = Path(result["job_dir"])
    attempt = folder / "shot-01/attempt-1"
    if change in {"raw", "tail"}:
        (attempt / ("raw.mp4" if change == "raw" else "tail.png")).write_bytes(b"changed")
    elif change == "review":
        review(result, binding="wrong")
    else:
        (folder / "checkpoint.json").write_text("[]")
    with pytest.raises(PackageError):
        executor.execute(**args)
    assert len(executor.comfyui.graphs) == 1


@pytest.mark.parametrize("issue", ["no_audio", "short", "nan", "asset"])
def test_real_asset_preflight_prevents_submission(runtime, issue):
    executor, args = runtime
    if issue == "no_audio": executor.renderer.has_audio = False
    elif issue == "short": executor.renderer.duration = 4
    elif issue == "nan": executor.renderer.duration = float("nan")
    else: args["narration"].write_bytes(b"different")
    with pytest.raises(PackageError):
        executor.execute(**args)
    assert executor.comfyui.graphs == []


def test_existing_process_lock_is_not_removed(runtime):
    executor, args = runtime
    result = executor.execute(**args)
    lock = Path(result["job_dir"]) / "execution.lock"
    lock.write_text("123")
    with pytest.raises(TemporaryWorkerError):
        executor.execute(**args)
    assert lock.read_text() == "123"


def test_incomplete_acceptance_is_not_visual_pass(runtime):
    executor, args = runtime
    result = executor.execute(**args)
    review(result, checks={"identity": True})
    with pytest.raises(PackageError, match="视觉复核"):
        executor.execute(**args)


def test_review_missing_is_pending(tmp_path):
    assert read_review(tmp_path / "absent.json", "unused") == "pending"
