"""Isolated continuation of the exact reviewed v5 candidate, not a long-film claim.

Run from the packaged directory beside lingnian_worker/. No installation, cloud
access, service restart, old-result overwrite or task cancellation is performed.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil

import httpx

from lingnian_worker.continuity import build_wan_graph, digest, run_trial, validate_plan
from lingnian_worker.media import MediaRenderer


FINGERPRINT = "853ea8b0333f0f4a7fc674b944d59d52f89872611a7a00d2d04770300e4eeb71"
EXPECTED = {
    "segment-01-raw.mp4": "4bcb86f6013b3810ad9f86474af6acbbfe6825c00053555fc290d4cc50c4bb21",
    "segment-01.mp4": "55db31940b60a925ab0b6a25d84570341739a1c98f0945e0cf23ba8fb639f6b4",
    "segment-01-tail.png": "2bbf958d1927f10db7e2f0f9fc1e3c30a986baeeae9304dbd1978630129ee8c2",
}


def main():
    root = Path(__file__).resolve().parent
    prior = root.parent / "lingnian-motion-v5"
    plan_path = prior / "round-02-plan.json"
    source = prior / "results" / FINGERPRINT[:16]
    reference = root.parent / "lingnian-continuity.cF4qFO/results/81b756df5bcf0a08/segment-01-tail.png"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan, reference)
    graph_version = hashlib.sha256(json.dumps(build_wan_graph(plan, plan["segments"][0], image_name="anchor", prefix="version"), sort_keys=True).encode()).hexdigest()
    actual = hashlib.sha256((json.dumps(plan, sort_keys=True, ensure_ascii=False) + graph_version).encode()).hexdigest()
    if actual != FINGERPRINT:
        raise RuntimeError("Plan or renderer changed; cannot reuse the reviewed candidate.")
    state = json.loads((source / "checkpoint.json").read_text(encoding="utf-8"))
    if state.get("fingerprint") != FINGERPRINT or len(state.get("segments", [])) != 1:
        raise RuntimeError("Prior result changed. Preserve it; do not restart the first shot.")
    first = state["segments"][0]
    if first.get("input_sha256") != digest(reference) or any(first.get(key) != EXPECTED[name] for key, name in (
            ("raw_sha256", "segment-01-raw.mp4"), ("clip_sha256", "segment-01.mp4"), ("tail_sha256", "segment-01-tail.png"))):
        raise RuntimeError("Prior checkpoint changed; no generation permitted.")
    for name, expected in EXPECTED.items():
        if digest(source / name) != expected:
            raise RuntimeError("Reviewed candidate changed; stop instead of regenerating it.")
    output = root / "results" / FINGERPRINT[:16]
    output.mkdir(parents=True, exist_ok=True)
    if (root / "STOP").exists():
        raise RuntimeError("STOP present; no GPU submission.")
    lock = root / "continue.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as target:
            target.write(str(os.getpid()))
        for name, expected in EXPECTED.items():
            target = output / name
            if target.exists() and digest(target) != expected:
                raise RuntimeError("Isolated candidate changed; preserve and diagnose.")
            if not target.exists():
                shutil.copyfile(source / name, target)
        if not (output / "checkpoint.json").exists():
            shutil.copyfile(source / "checkpoint.json", output / "checkpoint.json")
        resumed = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
        if resumed.get("fingerprint") != FINGERPRINT or not resumed.get("segments") or resumed["segments"][0] != first:
            raise RuntimeError("Isolated checkpoint changed; never silently rerender the first shot.")
        # Recovery of a known task must not be prevented by its own queued job.
        journal = output / "segment-02-job.json"
        if not journal.exists():
            response = httpx.get("http://127.0.0.1:8188/queue", timeout=10)
            response.raise_for_status()
            queue = response.json()
            if not isinstance(queue.get("queue_running"), list) or not isinstance(queue.get("queue_pending"), list):
                raise RuntimeError("Unrecognized queue response; no submission.")
            if queue["queue_running"] or queue["queue_pending"]:
                raise RuntimeError("ComfyUI busy; existing jobs remain untouched.")
        links = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Links"
        ffmpeg = shutil.which("ffmpeg") or str(links / "ffmpeg.EXE")
        ffprobe = shutil.which("ffprobe") or str(links / "ffprobe.EXE")
        if not Path(ffmpeg).is_file() or not Path(ffprobe).is_file():
            raise RuntimeError("Existing FFmpeg tools missing; nothing installed.")
        result = run_trial(plan_path, reference, root / "results", base_url="http://127.0.0.1:8188",
                           renderer=MediaRenderer(ffmpeg=ffmpeg, ffprobe=ffprobe), segments=2)
        print(f"READY FOR VISUAL REVIEW ONLY: {result}", flush=True)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
