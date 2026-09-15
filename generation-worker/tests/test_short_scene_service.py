import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from lingnian_worker import short_scene_service as service
from lingnian_worker.models import PackageError, TemporaryWorkerError


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(work_dir=tmp_path, node_token="synthetic-token-only", comfyui_url="http://127.0.0.1:8188",
                           comfy_timeout_seconds=30, ffmpeg="test", ffprobe="test")


class FakeApi:
    def __init__(self, status="awaiting_input_review", fixture=False):
        self.progresses, self.uploads = [], []
        self.fixture, self.status = fixture, status
        self.task = {"id": "test-job", "package_sha256": "a" * 64, "lease_token": "secret-not-for-journal"}
    def claim_short(self):
        return self.task
    def receive(self, task, token, root):
        (root / "plan.json").write_text(json.dumps({"test_fixture_only": self.fixture, "recording": {"kind": "authorized_recording"}}))
        return {"paths": {"plan": str(root / "plan.json"), "recording": str(root / "recording.wav"), "reference": str(root / "reference.png")}}
    def short_progress(self, task, stage, percent):
        self.progresses.append(stage)
    def short_result(self, task, path):
        self.uploads.append(path)


def test_idle_does_not_invoke_generator(config):
    api = FakeApi(); api.task = None
    result = service.run_once(config, api, runner=lambda *a, **kw: pytest.fail("no task"))
    assert result == {"status": "idle", "gpu_submitted": False}


def test_real_api_idle_and_lost_claim_preserve_one_request_key(config):
    api = service.ShortSceneApi("https://example.invalid", "synthetic-token")
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            raise httpx.ReadTimeout("lost response")
        return httpx.Response(200, text="null")
    api._client.close()
    api._client = httpx.Client(base_url="https://example.invalid", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TemporaryWorkerError):
            service.run_once(config, api)
        assert (config.work_dir / "native-short-scene/active.json").is_file()
        assert service.run_once(config, api) == {"status": "idle", "gpu_submitted": False}
        assert requests[0]["request_key"] == requests[1]["request_key"]
        assert not (config.work_dir / "native-short-scene/active.json").exists()
    finally:
        api.close()


def test_pending_input_review_is_reported_not_approved(config):
    api = FakeApi()
    result = service.run_once(config, api, runner=lambda *a, **kw: {"status": "awaiting_input_review"})
    assert result["status"] == "awaiting_input_review" and not result["visual_accepted"]
    assert api.progresses == ["preparing", "awaiting_input_review"] and not api.uploads
    ledger = config.work_dir / "native-short-scene/test-job/delivery.json"
    assert "secret-not-for-journal" not in ledger.read_text()


def test_fixture_cannot_submit_gpu(config):
    api = FakeApi(fixture=True)
    with pytest.raises(PackageError, match="测试素材"):
        service.run_once(config, api, runner=lambda *a, **kw: pytest.fail("test fixture submitted"))
    assert not api.uploads


def test_cached_candidate_is_uploaded_but_never_marked_accepted(config):
    api = FakeApi()
    def runner(*args, **kwargs):
        candidate = kwargs["work_dir"].parent / "candidate.mp4"; candidate.write_bytes(b"fixture")
        return {"status": "awaiting_full_playback_review", "candidate": str(candidate)}
    result = service.run_once(config, api, runner=runner)
    assert api.progresses == ["preparing", "generating"] and len(api.uploads) == 1
    assert not result["visual_accepted"]


def test_lease_failure_prevents_generation_and_upload(config):
    api = FakeApi()
    original = api.short_progress
    def progress(task, stage, percent):
        if stage == "generating": raise TemporaryWorkerError("test expiry")
        original(task, stage, percent)
    api.short_progress = progress
    def runner(*args, **kwargs):
        kwargs["on_generating"]()
        pytest.fail("must stop before GPU")
    with pytest.raises(TemporaryWorkerError):
        service.run_once(config, api, runner=runner)
    assert not api.uploads


def test_existing_connector_lock_prevents_claim(config):
    folder = config.work_dir / "native-short-scene"; folder.mkdir()
    (folder / "connector.lock").write_text("existing")
    api = FakeApi(); api.claim_short = lambda: pytest.fail("must not claim")
    with pytest.raises(TemporaryWorkerError):
        service.run_once(config, api)
    assert (folder / "connector.lock").read_text() == "existing"


@pytest.mark.parametrize("override", [{"package_url": "https://attacker.invalid/package"}, {"id": "../../outside"},
                                     {"package_sha256": "bad"}, {"protocol": "legacy"}])
def test_claim_response_cannot_leak_token_or_escape_workspace(override):
    task = {"id": "test-id", "protocol": "native-short-scene-v1", "lease_token": "a" * 40,
            "package_sha256": "b" * 64, "package_url": service.PREFIX + "/test-id/package"}
    api = service.ShortSceneApi("https://example.invalid", "test-token")
    api._json = lambda *a, **kw: {**task, **override}
    try:
        with pytest.raises(PackageError): api.claim_short()
    finally: api.close()
