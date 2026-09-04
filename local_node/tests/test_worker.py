from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image

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
    Image.new("RGB", (1024, 576)).save(cached)
    package = tmp_path / "package"
    source = package / "sources" / "original.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (1664, 936)).save(source)
    task = SimpleNamespace(id="task-1", generation_type="photo_restore")
    manifest = {"media": [{"path": "sources/original.png", "mime_type": "image/png"}]}
    assert worker._generate(task, manifest, package) == cached


def test_photo_restore_workflow_defaults_to_uncropped_16_by_9():
    workflow = __import__("json").loads((Path(__file__).parents[1] / "workflows" / "老照片修复.json").read_text(encoding="utf-8"))
    scale = workflow["4"]["inputs"]
    assert (scale["width"], scale["height"], scale["crop"]) == (1024, 576, "disabled")
