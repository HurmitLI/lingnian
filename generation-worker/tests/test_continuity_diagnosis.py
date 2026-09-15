import importlib.util
import json
from pathlib import Path


def load_diagnosis(tmp_path):
    path = Path(__file__).parents[1] / "scripts" / "diagnose_continuity.py"
    spec = importlib.util.spec_from_file_location("continuity_diagnosis", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / "diagnose_continuity.py")
    return module


def test_diagnosis_filters_unrelated_tasks_and_does_not_export_prompts(tmp_path, monkeypatch):
    module = load_diagnosis(tmp_path)
    calls = []
    def get_json(path):
        calls.append(path)
        if path == "/queue":
            return {"queue_running": [["private-family-data"]], "queue_pending": []}
        if path == "/history":
            return {
                "ours": {"prompt": [1, "ours", {"12": {"inputs": {"filename_prefix": module.PREFIX}}}],
                         "status": {"completed": True, "status_str": "success", "messages": ["private-message"]},
                         "outputs": {}},
                "unrelated": {"prompt": [2, "unrelated", {"1": {"inputs": {"text": "private-interview"}}}],
                              "outputs": {}},
            }
        assert path == "/object_info"
        return {"Wan22ImageToVideoLatent": {"input": {"required": {"width": [1280]}}}}
    monkeypatch.setattr(module, "get_json", get_json)
    module.main()
    text = (tmp_path / "diagnostic.json").read_text()
    result = json.loads(text)
    assert result["diagnosis_completed"] is True
    assert result["queue"] == {"queue_running": 1, "queue_pending": 0}
    assert [item["prompt_id"] for item in result["matches"]] == ["ours"]
    assert "private" not in text and "unrelated" not in text
    assert calls == ["/queue", "/history", "/object_info"]


def test_diagnosis_writes_failure_without_exception_content(tmp_path, monkeypatch):
    module = load_diagnosis(tmp_path)
    def fail(path):
        raise TimeoutError("private connection context")
    monkeypatch.setattr(module, "get_json", fail)
    module.main()
    text = (tmp_path / "diagnostic.json").read_text()
    assert json.loads(text)["error_type"] == "TimeoutError"
    assert json.loads(text)["diagnosis_completed"] is False
    assert "private" not in text
