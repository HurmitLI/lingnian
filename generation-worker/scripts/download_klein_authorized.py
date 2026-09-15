"""Download only the three explicitly authorized, pinned Klein files on Windows.

Standalone stdlib script; no pip, cloud credentials, GPU jobs or service changes.
Interrupted partials are resumable; existing model files are never overwritten.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from urllib.request import Request, urlopen


REPO = "Comfy-Org/vae-text-encorder-for-flux-klein-4b"
REVISION = "5f526678002e43af5551dadb73ce2e8c91b43afe"
FILES = (
    ("diffusion_models/flux-2-klein-4b.safetensors", 7751105712,
     "ec3d4e733a771f61c052fb4856c48b336c55eaf2c65487c2a1faeb9bbda7a343"),
    ("text_encoders/qwen_3_4b.safetensors", 8044982048,
     "6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a"),
    ("vae/flux2-vae.safetensors", 336211292,
     "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3"),
)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_manifest(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = [("split_files/" + p, "models/" + p, size, sha) for p, size, sha in FILES]
    actual = [(v["repository_path"], v["destination_relative_to_comfy"], v["size_bytes"], v["sha256"])
              for v in data["files"]]
    if (data.get("download_authorized") is not True or data.get("repository") != REPO
            or data.get("revision") != REVISION or actual != expected
            or data.get("total_bytes") != sum(v[1] for v in FILES)):
        raise ValueError("Manifest is not the explicitly authorized three-file scope")


def fetch_file(url, destination, size, sha, report, *, opener=urlopen):
    if destination.is_symlink():
        raise ValueError("Refuse symlink destination")
    if destination.exists():
        if destination.stat().st_size != size or digest(destination) != sha:
            raise ValueError("Existing model differs; preserved, not overwritten")
        report("existing_verified", size)
        return
    part = destination.with_name(destination.name + ".lingnian.part")
    if part.is_symlink():
        raise ValueError("Refuse symlink partial")
    offset = part.stat().st_size if part.exists() else 0
    if offset > size:
        raise ValueError("Oversize partial preserved for inspection")
    if offset < size:
        headers = {"Accept-Encoding": "identity", "User-Agent": "Lingnian-authorized-model-download/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        with opener(Request(url, headers=headers), timeout=60) as response:
            status = response.status
            if offset:
                expected = f"bytes {offset}-{size - 1}/{size}"
                if status != 206 or response.headers.get("Content-Range") != expected:
                    raise ValueError("Server did not honor exact resume; partial preserved")
            elif status != 200:
                raise ValueError("Unexpected download response")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != size - offset:
                raise ValueError("Unexpected download length")
            last_report = time.monotonic()
            with part.open("ab" if part.exists() else "xb") as target:
                while chunk := response.read(4 * 1024 * 1024):
                    if offset + len(chunk) > size:
                        raise ValueError("Response exceeds authorized size")
                    target.write(chunk)
                    offset += len(chunk)
                    if time.monotonic() - last_report >= 10:
                        target.flush()
                        report("downloading", offset)
                        last_report = time.monotonic()
                target.flush()
                os.fsync(target.fileno())
    report("verifying", offset)
    if offset != size or digest(part) != sha:
        raise ValueError("Incomplete or invalid SHA256; partial preserved")
    # Exclusive installation on the same volume: never replace a concurrent file.
    os.link(part, destination)
    part.unlink()
    report("verified", size)


def local_snapshot():
    def get(path):
        with urlopen("http://127.0.0.1:8188" + path, timeout=15) as response:
            return json.load(response)
    queue = get("/queue")
    nodes = get("/object_info")
    clip_types = nodes.get("CLIPLoader", {}).get("input", {}).get("required", {}).get("type", [[]])[0]
    required = {"UNETLoader", "CLIPLoader", "VAELoader", "ReferenceLatent"}
    return {"queue_running": len(queue.get("queue_running", [])),
            "queue_pending": len(queue.get("queue_pending", [])),
            "missing_core_nodes": sorted(required - set(nodes)),
            "clip_loader_has_flux2": "flux2" in clip_types,
            "model_loading_verified": False}


def run(manifest, comfy, output):
    check_manifest(manifest)
    if os.name != "nt":
        raise ValueError("This authorized download targets the Windows 5080 only")
    if not comfy.is_dir() or not (comfy / "models").is_dir():
        raise ValueError("Existing ComfyUI/model directories missing")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / "download.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    state = {"status": "preflight", "files": {}, "pid": os.getpid(),
             "production_services_modified": False, "gpu_submitted": False}

    def save():
        state["updated_at_unix"] = time.time()
        temp = output / "download-status.tmp"
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, output / "download-status.json")

    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        save()
        state["free_disk_bytes"] = shutil.disk_usage(comfy).free
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available",
                                                       "virtual_total", "virtual_available", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError("Cannot read actual memory")
        state["memory_total_bytes"], state["memory_available_bytes"] = memory.total, memory.available
        state["comfy_snapshot"] = local_snapshot()
        save()
        if state["free_disk_bytes"] < 35000000000:
            raise ValueError("Less than 35GB free; no cleanup performed")
        for relative, size, sha in FILES:
            destination = comfy / "models" / relative
            if not destination.parent.is_dir() or destination.parent.resolve().parent != (comfy / "models").resolve():
                raise ValueError("Expected model directory missing or redirected")
            def report(status, count):
                state["status"] = "downloading"
                state["files"][relative] = {"status": status, "bytes": count, "expected_bytes": size}
                save()
            url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/split_files/{relative}"
            fetch_file(url, destination, size, sha, report)
        state["status"] = "download_verified_not_generation_accepted"
        save()
    except Exception as error:
        state["status"] = "stopped_needs_inspection"
        # Network exceptions can contain signed redirects: do not persist them.
        state["error_type"] = type(error).__name__
        state["reason"] = str(error) if isinstance(error, ValueError) else "Inspect local network/process; no automatic retry"
        save()
        raise SystemExit(1)
    finally:
        lock.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--comfy-root", type=Path, default=Path(r"E:\LingnianAI\ComfyUI"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.comfy_root, args.output)
