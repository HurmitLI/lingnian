"""Prepare actual narration + source-bound shot inputs; never enqueue a GPU job.

Missing scene references remain null and the output has a NON-executable format.
No substitute image hashes, synthetic acceptance, network, model or TTS calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lingnian_worker.continuity import digest
from lingnian_worker.film_contract import validate_film_contract
from lingnian_worker.models import PackageError


def build_draft(blueprint: dict, manifest: dict, alignment: dict) -> dict:
    if (blueprint.get("format") != "lingnian-production-blueprint"
            or blueprint.get("fictional_only") is not True
            or blueprint["narration_sha256"] != manifest["audio_sha256"]
            or alignment["narration_sha256"] != manifest["audio_sha256"]):
        raise PackageError("制作输入或实际声音摘要不一致。")
    source = manifest["source_text"]
    shots = blueprint["shots"]
    scene_entries = blueprint["scenes"]
    if sum(s["duration_seconds"] for s in shots) != 70 or len(shots) != 15:
        raise PackageError("本故事要求15个原生镜头，共70秒。")
    starts = {s["start_shot"]: s for s in scene_entries}
    if len(starts) != len(scene_entries):
        raise PackageError("场景范围重复。")
    scenes, segments = [], []
    opening = scene_entries[0]["opening_state"]
    state, elapsed, next_scene = opening, 0, 1
    for number, shot in enumerate(shots, 1):
        if shot["id"] != number or shot["duration_seconds"] not in (4, 5):
            raise PackageError("镜头顺序或原生时长错误。")
        if number in starts:
            entry = starts[number]
            if entry["start_shot"] != next_scene or entry["end_shot"] < number:
                raise PackageError("场景不能缺失或重叠。")
            next_scene = entry["end_shot"] + 1
            reuse_opening = entry["id"] == "platform"
            scenes.append({**entry, "from_state": state,
                           "anchor_sha256": blueprint["reference_sha256"] if reuse_opening else None,
                           "parent_reference_sha256": blueprint["reference_sha256"],
                           "usage_authorized": reuse_opening,
                           "identity_claim": "illustrative_scene_not_historical_footage",
                           "reference_status": "existing_image_pending_scene_review" if reuse_opening
                           else "awaiting_actual_image_and_review"})
            state = entry["opening_state"]
        segments.append({**shot, "before": state,
                         "camera": "使用所在场景参考的已锁定机位；段内不任意变焦或跨场景。",
                         "staging_note": "具体动作与机位属于虚构故事的视觉演绎，不增加为历史事实。",
                         "start_seconds": elapsed, "end_seconds": elapsed + shot["duration_seconds"]})
        elapsed += shot["duration_seconds"]
        state = shot["after"]
    if next_scene != len(shots) + 1:
        raise PackageError("场景没有覆盖全部镜头。")
    for item in scenes + segments:
        if not item["source_quotes"] or not all(q and q in source for q in item["source_quotes"]):
            raise PackageError("场景或镜头引用不在真实原稿中。")
    return {
        "format": "lingnian-continuous-film-draft", "version": 2,
        "executable": False, "status": "awaiting_scene_assets",
        "source_text": source, "duration_seconds": 70,
        "film_bible": blueprint["film_bible"], "render_bible": blueprint["render_bible"],
        "render_negative": blueprint["render_negative"], "opening_state": opening,
        "reference_policy": "reviewed_scene_anchors_then_previous_last_frame",
        "reference": {"kind": "generated_reference", "sha256": blueprint["reference_sha256"],
                      "usage_authorized": True, "identity_claim": "illustrative_not_verified_likeness"},
        "narration": {"sha256": manifest["audio_sha256"], "duration_seconds": manifest["duration_seconds"],
                      "kind": "licensed_synthetic_voice", "usage_authorized": True},
        "narration_cues": alignment["narration_cues"],
        "scenes": scenes, "segments": segments,
        "character_requirements": blueprint["character_requirements"],
        "prop_requirements": blueprint["prop_requirements"],
        "remaining_gates": ["actual_character_references", "actual_scene_references",
                            "reference_visual_review", "narration_listening_review",
                            "real_gpu_shots_and_reviews", "final_audiovisual_review"],
    }


def main():
    root = Path(__file__).resolve().parents[2]
    blueprint = json.loads((root / "generation-worker/examples/blue-bag-70-production.json").read_text())
    bundle = root / ".worker-data/film-narration/169d73a4f135ee1ae92d8970"
    manifest = json.loads((bundle / "manifest.json").read_text())
    alignment = json.loads((bundle / "caption-alignment.json").read_text())
    reference = root / ".worker-data/continuity-review/three-final/81b756df5bcf0a08/segment-01-tail.png"
    if digest(reference) != blueprint["reference_sha256"] or digest(bundle / "narration.wav") != blueprint["narration_sha256"]:
        raise PackageError("实际参考图或声音已改变，不能继续沿用。")
    draft = build_draft(blueprint, manifest, alignment)
    # This must fail before any renderer can use it, even though audio is real.
    try:
        validate_film_contract(draft)
    except PackageError:
        pass
    else:
        raise PackageError("未准备场景参考的草案被意外当成可执行方案。")
    encoded = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
    binding = hashlib.sha256(encoded.encode()).hexdigest()
    folder = root / ".worker-data/film-preproduction" / binding[:24]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "plan.draft.json"
    if path.exists():
        if path.read_text() != encoded:
            raise PackageError("已有制作输入被修改，保留现场，不覆盖。")
    else:
        with path.open("x", encoding="utf-8") as output:
            output.write(encoded)
    print(json.dumps({"path": str(path), "shots": len(draft["segments"]),
                      "duration_seconds": 70, "subtitle_cues": len(draft["narration_cues"]),
                      "missing_scene_references": [s["id"] for s in draft["scenes"] if s["anchor_sha256"] is None],
                      "executable": False, "gpu_submitted": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
