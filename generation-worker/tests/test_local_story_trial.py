"""Regression checks for local-only story acceptance, without model calls."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "local_story_trial", Path(__file__).parents[1] / "scripts" / "local_story_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_successful_generation_without_visual_review_cannot_pass(tmp_path):
    artifact = tmp_path / "shot.mp4"
    artifact.write_bytes(b"generated output")
    with pytest.raises(RuntimeError, match="Awaiting motion review"):
        trial.require_review(tmp_path / "review.json", artifact,
                             {"id": 1, "expected_action": "Water lands in soil"}, "motion")


def test_review_does_not_survive_changed_story_action_or_artifact(tmp_path):
    artifact = tmp_path / "shot.mp4"
    artifact.write_bytes(b"reviewed output")
    shot = {"id": 1, "expected_action": "Water lands in soil"}
    review_path = tmp_path / "review.json"
    trial.save(review_path, {
        "sha256": trial.sha(artifact), "shot": 1,
        "expected_action": shot["expected_action"],
        "observed_action": "A narrow stream falls into the pot and stops.",
        "reviewer": "assistant_visual", "decision": "accepted",
        "checks": dict.fromkeys(["identity", "anatomy", "story", "motion", "continuity"], True),
    })
    trial.require_review(review_path, artifact, shot, "motion")
    with pytest.raises(RuntimeError):
        trial.require_review(review_path, artifact, {**shot, "expected_action": "Cut a leaf"}, "motion")
    artifact.write_bytes(b"unreviewed replacement")
    with pytest.raises(RuntimeError):
        trial.require_review(review_path, artifact, shot, "motion")


def test_unrelated_scene_cannot_enter_60_second_plan():
    shot = {"seconds": 5, "quote": "watering", "expected_action": "water soil",
            "opening": "spout over soil", "motion": "water falls"}
    plan = {"fictional": True, "local_only": True, "source_text": "watering jasmine",
            "shots": [{**shot, "id": i} for i in range(1, 13)]}
    trial.validate(plan)
    plan["shots"][4]["quote"] = "train station"
    with pytest.raises(AssertionError):
        trial.validate(plan)
