import hashlib

import httpx
import pytest

from lingnian_worker.comfyui import ComfyUiClient
from lingnian_worker.models import ConfigurationError, TemporaryWorkerError


@pytest.mark.parametrize("case", ["success", "wrong_hash", "wrong_prefix", "unfinished", "existing"])
def test_legacy_recovery_never_generates_or_overwrites(tmp_path, case):
    data = b"known-reviewed-test-video"
    sha = hashlib.sha256(data).hexdigest()
    path = tmp_path / "recovered.mp4"
    if case == "existing":
        path.write_bytes(b"user-existing")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path == "/history/job-1":
            return httpx.Response(200, json={"job-1": {
                "status": {"completed": case != "unfinished", "status_str": "success"},
                "prompt": [0, "job-1", {"12": {"inputs": {"filename_prefix": "known-prefix"}}}],
                "outputs": {"12": {"images": [{"filename": "known.mp4", "subfolder": "trial", "type": "output"}],
                                   "animated": [True]}}}})
        assert request.url.path == "/view"
        return httpx.Response(200, content=data)
    client = ComfyUiClient("http://127.0.0.1:8188", tmp_path / "plan.json", timeout_seconds=5)
    client._client.close()
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    try:
        kwargs = dict(expected_prefix="wrong" if case == "wrong_prefix" else "known-prefix",
                      expected_sha256="0" * 64 if case == "wrong_hash" else sha, output_path=path)
        if case == "success":
            assert client.recover_completed_video("job-1", **kwargs).read_bytes() == data
            assert client.recover_completed_video("job-1", **kwargs) == path
        else:
            with pytest.raises((ConfigurationError, TemporaryWorkerError)):
                client.recover_completed_video("job-1", **kwargs)
            assert (path.read_bytes() == b"user-existing") if case == "existing" else not path.exists()
        assert all(request.method == "GET" for request in calls)
    finally:
        client.close()
