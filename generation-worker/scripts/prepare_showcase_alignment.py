"""Local existing-model timestamps for the frozen fictional narration only.

No network, model installation, TTS, microphone, family database or cloud API.
Raw recognition remains evidence; it never replaces the approved story text.
"""
from pathlib import Path
import json
import os
import sys
import time


def main():
    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
            raise RuntimeError("This isolated alignment process is offline; downloads are forbidden")

    sys.addaudithook(offline)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["MODELSCOPE_OFFLINE"] = "1"
    os.environ["OMP_NUM_THREADS"] = "2"
    from lingnian_worker.continuity import digest
    from lingnian_worker.film_executor import _read, _save
    from prepare_showcase_narration import SOURCE_SHA256

    root = Path(__file__).resolve().parents[2]
    model = root / "backend/.model-cache/models/iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master"
    for name in ("model.pt", "config.yaml", "tokens.json", "am.mvn", "configuration.json", "seg_dict"):
        if not (model / name).is_file():
            raise RuntimeError("Existing model incomplete; nothing will be downloaded")
    output = root / ".worker-data/film-narration/169d73a4f135ee1ae92d8970"
    manifest = _read(output / "manifest.json")
    if digest(output / "narration.wav") != manifest["audio_sha256"]:
        raise RuntimeError("Narration changed; do not reuse alignment")
    inputs = []
    for index in (0, 1, 2, 4):
        path = root / f"data/runtime/showcase/video-work-cosyvoice-warm/audio-{index:02d}.wav"
        if digest(path) != SOURCE_SHA256[index]:
            raise RuntimeError("Frozen fictional audio source changed")
        inputs.append((index, path))
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    from funasr import AutoModel
    started = time.monotonic()
    recognizer = AutoModel(model=str(model), disable_update=True, device="cpu", ncpu=2,
                           disable_pbar=True, trust_remote_code=False)
    for number, path in inputs:
        destination = output / f"asr-chapter-{number + 1}.json"
        if destination.exists():
            prior = _read(destination)
            if prior.get("source_sha256") != SOURCE_SHA256[number] or prior.get("narration_sha256") != manifest["audio_sha256"]:
                raise RuntimeError("Old recognition evidence does not match; preserved")
            print(f"Reusing chapter {number + 1}", flush=True)
            continue
        result = recognizer.generate(input=str(path), pred_timestamp=True, batch_size=1)
        if not isinstance(result, list) or len(result) != 1 or not result[0].get("text") or not result[0].get("timestamp"):
            raise RuntimeError("Real local ASR did not return usable text and timestamps")
        _save(destination, {"format": "lingnian-local-alignment-evidence", "source_sha256": SOURCE_SHA256[number],
                            "narration_sha256": manifest["audio_sha256"], "model": "existing-local-seaco-paraformer",
                            "source_chapter": number + 1, "recognition": result[0],
                            "network_enabled": False, "subtitle_alignment_accepted": False})
        print(f"Chapter {number + 1} timestamps saved; alignment review still required", flush=True)
    print(f"Local recognition finished in {time.monotonic() - started:.1f}s", flush=True)
    from lingnian_worker.film_captions import align_narration
    records = {chapter["id"]: _read(output / f"asr-chapter-{chapter['id'].split('-')[-1]}.json") for chapter in manifest["chapters"]}
    alignment = align_narration(manifest, records, narration=output / "narration.wav")
    destination = output / "caption-alignment.json"
    if destination.exists():
        prior = _read(destination)
        if {k: v for k, v in prior.items() if k != "updated_at"} != alignment:
            raise RuntimeError("Existing caption alignment differs; preserved for review")
    else:
        _save(destination, alignment)
    print(f"Saved {len(alignment['narration_cues'])} timestamp-based subtitle cues; listening review pending", flush=True)


if __name__ == "__main__":
    main()
