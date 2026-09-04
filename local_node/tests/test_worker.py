from pathlib import Path
from types import SimpleNamespace

import httpx

from lingnian_node.worker import ProductionWorker


def response_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://cloud.example/result")
    response = httpx.Response(status, request=request, text="temporary")
    return httpx.HTTPStatusError("upload failed", request=request, response=response)


def test_result_upload_retries_server_error(monkeypatch, tmp_path):
    attempts = []

    class Cloud:
        def upload_result(self, task, result, actual_cost_cents=0):
            attempts.append(result)
            if len(attempts) < 3:
                raise response_error(500)

        def progress(self, task, percent, stage):
            assert percent == 95

    worker = ProductionWorker.__new__(ProductionWorker)
    worker.cloud = Cloud()
    worker.token = "not-a-real-token"
    worker.log_path = tmp_path / "worker.jsonl"
    monkeypatch.setattr("lingnian_node.worker.time.sleep", lambda _: None)
    result = tmp_path / "real.png"
    result.write_bytes(b"real-result")
    worker._upload_with_retry(SimpleNamespace(id="task-1"), result)
    assert len(attempts) == 3


def test_generate_reuses_real_cached_task_output(tmp_path):
    worker = ProductionWorker.__new__(ProductionWorker)
    worker.comfy_root = tmp_path
    output = tmp_path / "output" / "lingnian"
    output.mkdir(parents=True)
    cached = output / "production-task-1_00001_.png"
    cached.write_bytes(b"real-result")
    task = SimpleNamespace(id="task-1", generation_type="photo_restore")
    assert worker._generate(task, {"media": []}, Path("unused")) == cached
