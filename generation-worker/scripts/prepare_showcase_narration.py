"""Reuse the existing fictional showcase voice; no key loading or TTS calls."""
import ast
import hashlib
import json
from pathlib import Path

from lingnian_worker.continuity import digest
from lingnian_worker.film_narration import build_narration


STORY_SHA256 = "736915d3fc6aa4c8ed3b24eecc7eebc6b7b23f1d4909a999c6b53726f11dd92d"
SOURCE_SHA256 = {
    0: "00f24d8d8122f866a400cfdf7bf0cdb2753bbe4c7589b8c0d93da5af2577a721",
    1: "2ce407a752e3ad0482cb755f5ab5e143e9fa052751fe7dd3a837f0ced7d1d381",
    2: "42c0e485a8b5f9a315c9369843a2462f5ef69f314832aea0f6f9ac5d26c80d74",
    4: "aa7b6cf9d4dfb4588df3c5c23904969a5caa4fb8427bbebf33b6b77e39535cdb",
}


def main():
    root = Path(__file__).resolve().parents[2]
    # Read literal source only: importing the old generation script is unnecessary.
    tree = ast.parse((root / "scripts/generate_showcase_media.py").read_text(encoding="utf-8"))
    matches = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "STORY_SEGMENTS" for target in node.targets)]
    if len(matches) != 1:
        raise RuntimeError("Showcase source changed; preserve existing material.")
    story = matches[0]
    source_text = "\n".join(part["text"] for part in story)
    if hashlib.sha256(source_text.encode()).hexdigest() != STORY_SHA256:
        raise RuntimeError("Showcase text changed; voice-to-text match must be reviewed again.")
    audio_dir = root / "data/runtime/showcase/video-work-cosyvoice-warm"
    chapters = []
    # Keep whole chapters 1, 2, 3, 5. Omit the separate three-house-moves anecdote.
    for index in (0, 1, 2, 4):
        path = audio_dir / f"audio-{index:02d}.wav"
        if digest(path) != SOURCE_SHA256[index]:
            raise RuntimeError("Showcase audio changed; do not reuse prior fictional-voice authorization.")
        chapters.append({"id": f"showcase-{index + 1}", "path": path, "sha256": SOURCE_SHA256[index],
                         "text": story[index]["text"], "kind": "licensed_synthetic_voice", "usage_authorized": True})
    report = build_narration(chapters, source_text=source_text,
                             output=root / ".worker-data/film-narration")
    print(json.dumps({key: report[key] for key in ("directory", "duration_seconds", "binding", "audio_sha256", "listening_review")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
