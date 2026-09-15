"""Reuse authorized fictional sample and actual ASR; never a user E2E claim."""
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path

from lingnian_worker.continuity import digest
from lingnian_worker.short_scene import _require, _save, validate_plan


def prepare(bundle: Path, output: Path) -> dict:
    recording, reference = bundle / "narration.wav", bundle / "reference.png"
    alignment = json.loads((bundle / "caption-alignment.json").read_text(encoding="utf-8"))
    _require(digest(recording) == "69bdacab8dc06e4edc70c73bbe90b521568eeb60199b95db8ea9a9eee93658a5"
             and digest(reference) == "ebfb9f80a9dfaf174777bc5787a27fa5ee9cad15e5ddeb41d43d290909a54dd7",
             "只复用已核对的虚构样片素材，不自动换成家庭资料。")
    _require(alignment.get("narration_sha256") == digest(recording)
             and alignment.get("alignment_method") == "actual_local_asr_token_timestamps"
             and alignment.get("source_text_preserved") is True, "缺少已核对的真实ASR对齐。")
    items, group, previous = [], [], 0
    for cue in alignment["narration_cues"]:
        a, b = cue["start_seconds"], cue["end_seconds"]
        _require(0 <= previous <= a < b <= 69.9180625 and cue["text"] == cue["source_quote"], "原始对齐损坏。")
        previous = b
        group.append(cue)
        if re.search(r"[。！？.!?]$", cue["text"].strip()):
            items.append({"role": "answer", "start_frame": round(group[0]["start_seconds"] * 16000),
                          "end_frame": round(b * 16000), "text": "".join(c["text"] for c in group)})
            group = []
    _require(not group, "最后一句不完整。")
    with wave.open(str(recording)) as audio:
        frames = audio.getnframes()
    plan = {
        "format": "lingnian-short-scene", "version": 1, "duration_seconds": 10, "test_fixture_only": True,
        "source_text": "".join(i["text"] for i in items),
        "recording": {"sha256": digest(recording), "frames": frames, "kind": "licensed_synthetic_voice", "usage_authorized": True},
        "reference": {"sha256": digest(reference), "kind": "generated_reference", "usage_authorized": True,
                      "identity_claim": "illustrative_not_verified_likeness"},
        "timing": {"recording_sha256": digest(recording), "basis": "actual_asr_sentence_timestamps", "items": items},
        "selection": {"first_sentence": 0, "last_sentence": 1},
        "scene": {
            "context_summary": "虚构素琴回忆1982年首次去无锡工作与母亲送别；此处只呈现出发站台，不混入后来送女儿或抵达开包。",
            "character": "既有参考中的19岁成年素琴，双辫，同一侧脸；其他背景乘客只保持原位置，不新增角色。",
            "wardrobe": "浅色小花长袖衬衫、深蓝裤，抱着同一深蓝旅行布包。",
            "location": "出发站台，出发城市未知；不是无锡到达站。", "era": "1982年春天，日常彩色生活演绎。",
            "opening_state": "年轻女子抱住蓝包，侧脸朝画面左方的绿皮客车；背景乘客和客车保持原位置。",
            "action": "抱包站立，轻轻向左侧车门转头后自然停住；轻微呼吸，不说话、不开包、不上车。",
            "render_bible": "Same young adult woman as input, twin braids, pale patterned blouse, navy trousers, same soft blue bag held in both arms. Same platform and stationary green passenger carriage on the left. Preserve existing background people without adding or moving them into the foreground. Overcast natural daylight, consistent color. One fixed medium shot. Departure city is unknown; no new signs or locomotive.",
            "render_action": "The foreground woman gently turns her head a little toward the carriage door on her left, then settles, breathing naturally. Keep both arms supporting the same closed bag. Relaxed closed mouth. Camera and train stay still. Continuous ten-second live action, no cuts, no speaking, no bag opening.",
            "source_quotes": ["这个蓝布包啊，我一直没舍得扔。", "八二年春天吧，我十九岁，头一回一个人坐火车，去无锡的纺织厂。"],
            "unknowns": ["真实人物长相", "出发城市", "车次与机车型号", "站台细节为示意，不是实拍历史"],
            "shot_count": 1, "subject_count": 1, "camera": "locked", "style": "consistent_color_live_action",
        },
    }
    validate_plan(plan, recording=recording, reference=reference)
    output.parent.mkdir(parents=True, exist_ok=True)
    _save(output, plan)
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.bundle, args.output)
    print("已准备隔离合成声音样片方案；不是新采访或自动选景验证。")
