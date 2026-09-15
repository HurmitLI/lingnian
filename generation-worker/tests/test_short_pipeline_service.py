from pathlib import Path
from types import SimpleNamespace

import pytest

from lingnian_worker import short_pipeline_service as service
from lingnian_worker.models import ConfigurationError, TemporaryWorkerError


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(work_dir=tmp_path, comfyui_url="http://127.0.0.1:8188")


def test_busy_gpu_never_claims(config, monkeypatch):
    monkeypatch.setattr(service, "run_reference_once", lambda *a, **k: pytest.fail("must not claim"))
    monkeypatch.setattr(service, "run_short_once", lambda *a, **k: pytest.fail("must not claim"))
    assert service.run_cycle(config, object(), object(), queue_probe=lambda _: "busy") == {
        "lane": "none", "status": "gpu_busy", "gpu_submitted": False}


def test_reference_has_priority_and_cycle_stops_after_one_job(config, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "run_reference_once", lambda *a, **k: calls.append("reference") or {"status": "awaiting_reference_review"})
    monkeypatch.setattr(service, "run_short_once", lambda *a, **k: calls.append("video") or {"status": "idle"})
    result = service.run_cycle(config, object(), object(), queue_probe=lambda _: "idle")
    assert result == {"lane": "reference", "status": "awaiting_reference_review"}
    assert calls == ["reference"]


def test_video_is_checked_after_reference_queue_is_empty(config, monkeypatch):
    probes = []
    monkeypatch.setattr(service, "run_reference_once", lambda *a, **k: {"status": "idle"})
    monkeypatch.setattr(service, "run_short_once", lambda *a, **k: {"status": "awaiting_full_playback_review"})
    result = service.run_cycle(config, object(), object(), queue_probe=lambda _: probes.append(1) or "idle")
    assert result == {"lane": "video", "status": "awaiting_full_playback_review"}
    assert len(probes) == 2


def test_active_video_recovers_without_queue_or_reference_claim(config, monkeypatch):
    folder = config.work_dir / "native-short-scene"; folder.mkdir(); (folder / "active.json").write_text("{}")
    monkeypatch.setattr(service, "run_reference_once", lambda *a, **k: pytest.fail("must not claim reference"))
    monkeypatch.setattr(service, "run_short_once", lambda *a, **k: {"status": "interrupted", "automatic_retry": False})
    result = service.run_cycle(config, object(), object(), queue_probe=lambda _: pytest.fail("must not probe"))
    assert result["lane"] == "video" and result["status"] == "interrupted"


def test_queue_probe_rejects_remote_or_malformed(monkeypatch):
    with pytest.raises(ConfigurationError): service.gpu_queue_state("https://example.com")
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"queue_running": [], "queue_pending": "bad"}
    monkeypatch.setattr(service.httpx, "get", lambda *a, **k: Response())
    with pytest.raises(TemporaryWorkerError): service.gpu_queue_state("http://127.0.0.1:8188")
