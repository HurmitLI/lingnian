import json

import httpx
import pytest

from lingnian_worker.comfyui import ComfyUiClient
from lingnian_worker.models import ConfigurationError, TemporaryWorkerError


def client_for(tmp_path, handler):
    client = ComfyUiClient("http://127.0.0.1:8188", tmp_path / "plan.json", timeout_seconds=5)
    client._client.close()
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    return client


def finished(outputs=None):
    return {"job-1": {"status": {"completed": True, "status_str": "success"},
                      "outputs": outputs if outputs is not None else {
                          "12": {"videos": [{"filename": "trial.mp4", "subfolder": "trial", "type": "output"}]}}}}


def test_failed_download_resumes_same_job_without_second_generation(tmp_path):
    calls = []
    downloads = 0
    def handler(request):
        nonlocal downloads
        calls.append((request.method, request.url.path))
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "job-1"})
        if request.url.path == "/history/job-1":
            return httpx.Response(200, json=finished())
        downloads += 1
        return httpx.Response(503 if downloads == 1 else 200, content=b"fake-video")
    client = client_for(tmp_path, handler)
    journal, output = tmp_path / "job.json", tmp_path / "raw.mp4"
    graph = {"private_prompt": "do not store interview text in journal"}
    try:
        with pytest.raises(TemporaryWorkerError, match="下载失败"):
            client.run_workflow(graph, output_path=output, journal_path=journal)
        assert json.loads(journal.read_text())["stage"] == "downloading"
        assert client.run_workflow(graph, output_path=output, journal_path=journal).read_bytes() == b"fake-video"
        assert calls.count(("POST", "/prompt")) == 1
        state = json.loads(journal.read_text())
        assert state["stage"] == "downloaded" and len(state["output_sha256"]) == 64
        assert "interview" not in journal.read_text()
        with pytest.raises(ConfigurationError, match="不一致"):
            client.run_workflow({"different": True}, output_path=output, journal_path=journal)
    finally:
        client.close()


def test_unknown_submit_is_not_automatically_repeated(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("lost response", request=request)
    client = client_for(tmp_path, handler)
    try:
        for message in ("未能启动", "提交结果未知"):
            with pytest.raises(TemporaryWorkerError, match=message):
                client.run_workflow({}, output_path=tmp_path / "raw.mp4", journal_path=tmp_path / "job.json")
        assert len(calls) == 1
    finally:
        client.close()


def test_existing_lock_prevents_all_network_requests(tmp_path):
    lock = tmp_path / "job.json.lock"
    lock.touch()
    client = client_for(tmp_path, lambda request: pytest.fail("No network allowed"))
    try:
        with pytest.raises(TemporaryWorkerError, match="执行锁"):
            client.run_workflow({}, output_path=tmp_path / "raw.mp4", journal_path=tmp_path / "job.json")
        assert lock.exists()
    finally:
        client.close()


def test_corrupt_journal_is_preserved_without_submission(tmp_path):
    journal = tmp_path / "job.json"
    journal.write_text("{broken")
    client = client_for(tmp_path, lambda request: pytest.fail("No network allowed"))
    try:
        with pytest.raises(ConfigurationError, match="损坏"):
            client.run_workflow({}, output_path=tmp_path / "raw.mp4", journal_path=journal)
        assert journal.read_text() == "{broken"
        assert not (tmp_path / "job.json.lock").exists()
    finally:
        client.close()


def test_failed_generation_keeps_id_and_does_not_submit_again(tmp_path):
    submissions = 0
    def handler(request):
        nonlocal submissions
        if request.url.path == "/prompt":
            submissions += 1
            return httpx.Response(200, json={"prompt_id": "job-1"})
        return httpx.Response(200, json={"job-1": {"status": {"status_str": "error", "completed": False}}})
    client = client_for(tmp_path, handler)
    try:
        for _ in range(2):
            with pytest.raises(TemporaryWorkerError, match="生成失败"):
                client.run_workflow({}, output_path=tmp_path / "raw.mp4", journal_path=tmp_path / "job.json")
        assert submissions == 1
        assert json.loads((tmp_path / "job.json").read_text())["stage"] == "generation_failed"
    finally:
        client.close()


def test_completed_without_artifact_fails_immediately_and_records_reason(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"prompt_id": "job-1"} if request.url.path == "/prompt" else finished({}))
    client = client_for(tmp_path, handler)
    try:
        with pytest.raises(TemporaryWorkerError, match="未返回支持"):
            client.run_workflow({}, output_path=tmp_path / "raw.mp4", journal_path=tmp_path / "job.json")
        assert json.loads((tmp_path / "job.json").read_text())["stage"] == "missing_output"
    finally:
        client.close()


def test_incomplete_history_waits_instead_of_failing(tmp_path, monkeypatch):
    histories = 0
    monkeypatch.setattr("lingnian_worker.comfyui.time.sleep", lambda _: None)
    def handler(request):
        nonlocal histories
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "job-1"})
        if request.url.path.startswith("/history"):
            histories += 1
            data = {"job-1": {"status": {"completed": False}}} if histories == 1 else finished()
            return httpx.Response(200, json=data)
        return httpx.Response(200, content=b"fake-video")
    client = client_for(tmp_path, handler)
    try:
        client.run_workflow({}, output_path=tmp_path / "raw.mp4", journal_path=tmp_path / "job.json")
        assert histories == 2
    finally:
        client.close()
