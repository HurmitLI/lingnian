import json
from pathlib import Path

import pytest

from lingnian_worker import film_bridge as module
from lingnian_worker.film_bridge import command_for, read_request, serve
from lingnian_worker.models import PackageError


@pytest.mark.parametrize("payload", [
    {"id": "001", "operation": "shell", "command": "anything"},
    {"id": "001", "operation": "film", "project": "../other"},
    {"id": "001", "operation": "film", "project": "x;echo hi"},
    {"id": "002", "operation": "snapshot"},
    {"id": "001", "operation": "snapshot", "project": "unexpected"},
    {"id": "001", "operation": "snapshot", "url": "https://external.example"},
])
def test_request_cannot_expand_into_general_command_or_remote_endpoint(tmp_path, payload):
    path = tmp_path / "001.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(PackageError):
        read_request(path)


def test_film_command_is_fixed_no_shell_with_scoped_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/existing/" + name)
    folder = tmp_path / "inputs/story"
    folder.mkdir(parents=True)
    for name in ("plan.json", "reference.png", "narration.wav"):
        (folder / name).write_bytes(b"fixture")
    command = command_for({"id": "x", "operation": "film", "project": "story"}, tmp_path, 4000)
    assert "lingnian_worker.film_watch" in command
    assert command[-1] == "2200"
    (folder / "plan.json").unlink()
    outside = tmp_path / "outside"
    outside.write_text("private")
    (folder / "plan.json").symlink_to(outside)
    with pytest.raises(PackageError):
        command_for({"id": "x", "operation": "film", "project": "story"}, tmp_path, 4000)


def fake_clock(monkeypatch, hook=lambda: None):
    now = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    def sleep(seconds):
        now[0] += seconds
        hook()

    monkeypatch.setattr(module.time, "sleep", sleep)


def test_snapshot_processed_once_and_old_claim_never_replayed(tmp_path, monkeypatch):
    fake_clock(monkeypatch)
    inbox = tmp_path / "requests"
    inbox.mkdir()
    (inbox / "001.json").write_text(json.dumps({"id": "001", "operation": "snapshot"}))
    calls = []
    monkeypatch.setattr(module, "snapshot", lambda: calls.append(1) or {"status": "completed"})
    serve(tmp_path, seconds=60)
    assert calls == [1]
    (tmp_path / "responses/001.json").unlink()
    serve(tmp_path, seconds=60)
    assert calls == [1]
    assert (tmp_path / "claims/001.started").exists()


def test_stop_does_not_launch_or_clear_any_work(tmp_path, monkeypatch):
    fake_clock(monkeypatch)
    (tmp_path / "STOP").touch()
    inbox = tmp_path / "requests"
    inbox.mkdir()
    (inbox / "001.json").write_text(json.dumps({"id": "001", "operation": "snapshot"}))
    monkeypatch.setattr(module, "snapshot", lambda: pytest.fail("no new work after STOP"))
    serve(tmp_path, seconds=60)
    assert not list((tmp_path / "claims").iterdir())
    assert (tmp_path / "STOP").exists()


def test_inflight_process_preserves_lock_at_deadline_and_cannot_duplicate(tmp_path, monkeypatch):
    fake_clock(monkeypatch)
    inbox = tmp_path / "requests"
    inbox.mkdir()
    for identifier in ("001", "002"):
        (inbox / (identifier + ".json")).write_text(json.dumps({"id": identifier, "operation": "continue_v5"}))
    launches = []

    class Process:
        pid = 12345

        def __init__(self, command, **kwargs):
            assert kwargs["shell"] is False
            launches.append(command)

        def poll(self):
            return None

    monkeypatch.setattr(module, "command_for", lambda *a: ["fixed-fixture"])
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    serve(tmp_path, seconds=60)
    assert launches == [["fixed-fixture"]]
    assert (tmp_path / "bridge.lock").exists()
    status = json.loads((tmp_path / "bridge-status.json").read_text())
    assert status["status"] == "detached_active_job"
    assert status["gpu_task_cancelled"] is False
    with pytest.raises(FileExistsError):
        serve(tmp_path, seconds=60)
    assert len(launches) == 1


def test_malformed_request_does_not_retry_or_disclose_exception(tmp_path, monkeypatch):
    fake_clock(monkeypatch)
    inbox = tmp_path / "requests"
    inbox.mkdir()
    (inbox / "001.json").write_text("a private malformed payload")
    serve(tmp_path, seconds=60)
    response = (tmp_path / "responses/001.json").read_text()
    assert "private" not in response
    assert json.loads(response)["automatic_replay"] is False
