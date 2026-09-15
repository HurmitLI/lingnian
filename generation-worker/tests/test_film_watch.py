from pathlib import Path

import pytest

from test_film_executor import runtime, review
from test_film_contract import film
from lingnian_worker.film_executor import execution_binding
from lingnian_worker.film_watch import watch_film
from lingnian_worker.models import ConfigurationError, PackageError, TemporaryWorkerError


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_waiting_does_not_rerender_or_approve(runtime):
    executor, args = runtime
    clock = Clock()
    result = watch_film(executor, **args, max_seconds=30, clock=clock, sleep=clock.sleep)
    assert result["status"] == "paused"
    assert result["final_visual_accepted"] is False
    assert len(executor.comfyui.graphs) == 1
    assert not list(Path(result["job_dir"]).rglob("review.json"))
    assert not (Path(result["job_dir"]) / "watch.lock").exists()


def test_reviews_resume_to_assembly_in_one_launch(runtime):
    executor, args = runtime
    from lingnian_worker.film_executor import _read
    clock, assembled = Clock(), []
    folder = executor.work_dir / execution_binding(args["plan"], executor.comfyui.base_url)[:24]

    def inspect_fixture(seconds):
        # Protocol test only. Actual media still needs an actual visual reviewer.
        review(_read(folder / "status.json"))
        clock.sleep(seconds)

    def assemble(plan, **kwargs):
        assembled.append(kwargs)
        return kwargs["job_dir"] / "film.mp4"

    result = watch_film(executor, **args, clock=clock, sleep=inspect_fixture, assembler=assemble)
    assert result["status"] == "awaiting_final_review"
    assert result["final_visual_accepted"] is False
    assert len(executor.comfyui.graphs) == 16
    assert len(assembled) == 1


def test_stop_prevents_any_submission(runtime):
    executor, args = runtime
    executor.work_dir.mkdir()
    (executor.work_dir / "STOP").touch()
    result = watch_film(executor, **args)
    assert result["status"] == "stopped"
    assert executor.comfyui.graphs == []
    assert (executor.work_dir / "STOP").exists()


def test_existing_watch_lock_is_preserved(runtime):
    executor, args = runtime
    folder = executor.work_dir / execution_binding(args["plan"], executor.comfyui.base_url)[:24]
    folder.mkdir(parents=True)
    (folder / "watch.lock").write_text("other-process")
    with pytest.raises(TemporaryWorkerError):
        watch_film(executor, **args)
    assert (folder / "watch.lock").read_text() == "other-process"
    assert executor.comfyui.graphs == []


def test_unknown_submission_never_blindly_retried(runtime, monkeypatch):
    executor, args = runtime
    calls = []

    def fail(*a, **k):
        calls.append(1)
        raise TemporaryWorkerError("unknown")

    monkeypatch.setattr(executor, "execute", fail)
    with pytest.raises(TemporaryWorkerError):
        watch_film(executor, **args)
    assert calls == [1]
    from lingnian_worker.film_executor import _read
    report = next(executor.work_dir.rglob("watch-status.json"))
    assert _read(report)["automatic_resubmission"] is False


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), 43201])
def test_invalid_deadlines_rejected(runtime, seconds):
    executor, args = runtime
    with pytest.raises(ConfigurationError):
        watch_film(executor, **args, max_seconds=seconds)
    assert executor.comfyui.graphs == []
