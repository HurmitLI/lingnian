"""Read-only local ComfyUI diagnosis. No generation, queue clearing or cloud calls."""
import json
import hashlib
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode

BASE = "http://127.0.0.1:8188"
PREFIX = "lingnian-continuity/17f51fc02e8b-1"
EXPECTED_RAW = "963c3c9787b0d6afa2f56e7b884e08f096b6a99a214f7481940c6ce5cdf86bb1"


def get_json(path):
    with urlopen(BASE + path, timeout=30) as response:
        return json.load(response)


def matches(entry):
    prompt = entry.get("prompt", [])
    graph = prompt[2] if isinstance(prompt, list) and len(prompt) > 2 else {}
    return isinstance(graph, dict) and any(
        isinstance(node, dict) and node.get("inputs", {}).get("filename_prefix") == PREFIX
        for node in graph.values()
    )


def main():
    root = Path(__file__).resolve().parent
    result = {"purpose": "read_only_local_diagnosis", "prefix": PREFIX, "matches": []}
    try:
        queue = get_json("/queue")
        result["queue"] = {key: len(queue.get(key, [])) for key in ("queue_running", "queue_pending")}
        history = get_json("/history")
        for prompt_id, entry in history.items():
            if not isinstance(entry, dict) or not matches(entry):
                continue
            outputs = entry.get("outputs", {})
            item = {"prompt_id": prompt_id, "status": entry.get("status", {}).get("status_str"),
                    "completed": entry.get("status", {}).get("completed"),
                    "output_keys": {key: list(value) for key, value in outputs.items()}, "artifacts": []}
            # Never dump full prompt graphs, status messages or unrelated family tasks.
            for value in outputs.values():
                for group in ("videos", "gifs", "images"):
                    for media in value.get(group, []):
                        if not isinstance(media, dict) or not str(media.get("filename", "")).endswith(".mp4"):
                            continue
                        params = {key: str(media.get(key, "output" if key == "type" else ""))
                                  for key in ("filename", "subfolder", "type")}
                        with urlopen(BASE + "/view?" + urlencode(params), timeout=30) as response:
                            data = response.read()
                        sha = hashlib.sha256(data).hexdigest()
                        item["artifacts"].append({**params, "bytes": len(data), "sha256": sha,
                                                  "matches_reviewed_raw": sha == EXPECTED_RAW})
            result["matches"].append(item)
        info = get_json("/object_info")
        result["control_nodes"] = {}
        for name in ("Wan22ImageToVideoLatent", "WanFirstLastFrameToVideo", "Wan22FunControlToVideo"):
            node = info.get(name)
            result["control_nodes"][name] = {"available": node is not None,
                "required": list((node or {}).get("input", {}).get("required", {})),
                "optional": list((node or {}).get("input", {}).get("optional", {}))}
        result["diagnosis_completed"] = True
    except Exception as exc:
        result["diagnosis_completed"] = False
        result["error_type"] = type(exc).__name__
    (root / "diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Diagnostic saved. No jobs submitted, stopped or deleted.", flush=True)


if __name__ == "__main__":
    main()
