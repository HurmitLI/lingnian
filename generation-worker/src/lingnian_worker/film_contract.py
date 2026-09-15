"""Validate the long-film handoff before spending GPU time.

This is a structural/evidence gate, NOT a semantic or visual acceptance judge.
It deliberately does not relax the existing fictional short-trial entry point.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from .models import PackageError


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_film_contract(plan: dict) -> None:
    _require(isinstance(plan, dict), "整片方案必须是对象。")
    _require(plan.get("format") == "lingnian-continuous-film" and type(plan.get("version")) is int
             and plan["version"] in {1, 2}, "不支持的整片方案版本。")
    source = plan.get("source_text")
    _require(_text(source), "缺少完整回忆原文。")
    duration = plan.get("duration_seconds")
    _require(type(duration) is int and 60 <= duration <= 75, "整片必须为 60–75 秒。")
    bible = plan.get("film_bible")
    _require(isinstance(bible, dict), "缺少整片设定。")
    for key in ("summary", "character", "wardrobe", "prop", "era", "location", "journey", "visual_style", "forbidden_changes"):
        _require(_text(bible.get(key)), f"整片设定缺少 {key}。")
    _require(_text(plan.get("opening_state")), "缺少开场状态。")
    _require(_text(plan.get("render_bible")), "缺少可执行的整片渲染设定。")
    if "render_negative" in plan:
        _require(_text(plan["render_negative"]) and len(plan["render_negative"]) <= 2000, "反向约束格式错误或过长。")
    policy = "first_reference_then_previous_last_frame" if plan["version"] == 1 else "reviewed_scene_anchors_then_previous_last_frame"
    _require(plan.get("reference_policy") == policy, "必须声明对应版本的参考接续策略。")
    if plan["version"] == 1:
        _require("scenes" not in plan, "旧版执行器不能忽略新场景设定；请使用第二版合同。")
    reference = plan.get("reference")
    _require(isinstance(reference, dict), "必须准备统一参考，不能逐镜随机生成角色。")
    _require(reference.get("kind") in {"user_photo", "generated_reference"}, "参考来源不明确。")
    _require(_sha(reference.get("sha256")), "参考图缺少有效摘要。")
    _require(reference.get("usage_authorized") is True, "参考图使用尚未授权。")
    if reference["kind"] == "generated_reference":
        _require(reference.get("identity_claim") == "illustrative_not_verified_likeness", "无照片演绎不能声称还原真实长相。")
    else:
        _require(reference.get("identity_claim") == "photo_reference_not_historical_footage", "照片参考不等于历史实拍。")

    segments = plan.get("segments")
    _require(isinstance(segments, list) and 12 <= len(segments) <= 18, "整片应由 12–18 个原生短镜头组成。")
    scenes = validate_scenes(plan, segments) if plan["version"] == 2 else {}
    state, total = plan["opening_state"], 0
    for index, segment in enumerate(segments, 1):
        _require(isinstance(segment, dict), "镜头必须是对象。")
        _require(type(segment.get("id")) is int and segment["id"] == index, "镜头编号必须连续。")
        seconds = segment.get("duration_seconds")
        _require(type(seconds) is int and seconds in {4, 5}, "镜头必须为原生 4–5 秒，不循环凑时长。")
        total += seconds
        if index in scenes:
            scene = scenes[index]
            _require(scene["from_state"] == state, "场景切换没有承接上一镜头结束状态。")
            state = scene["opening_state"]
        _require(segment.get("before") == state, "镜头起止状态断裂。")
        for key in ("after", "action", "camera", "staging_note", "render_action"):
            _require(_text(segment.get(key)), f"镜头缺少 {key}。")
        _require(len((render_plan_for_shot(plan, index)["render_bible"] + " " + segment["render_action"]).split()) <= 300, "渲染设定与动作描述过长。")
        quotes = segment.get("source_quotes")
        _require(isinstance(quotes, list) and len(quotes) > 0 and
                 all(_text(q) and q in source for q in quotes), "镜头没有可核对的回忆原文依据。")
        state = segment["after"]
    _require(total == duration, "镜头总时长与整片不一致。")

    audio = plan.get("narration")
    _require(isinstance(audio, dict) and _sha(audio.get("sha256")), "缺少已准备的旁白摘要。")
    length = audio.get("duration_seconds")
    _require(_number(length) and duration - 5 <= length <= duration, "旁白应与整片匹配，不能截断或用大段静音补齐。")
    _require(audio.get("kind") in {"authorized_recording", "licensed_synthetic_voice"}
             and audio.get("usage_authorized") is True, "旁白来源或使用授权不明确。")
    cues = plan.get("narration_cues")
    _require(isinstance(cues, list) and 1 <= len(cues) <= 60, "旁白字幕时间轴须包含 1–60 条字幕。")
    previous_end = 0
    for cue in cues:
        _require(isinstance(cue, dict), "字幕条目必须是对象。")
        start, end = cue.get("start_seconds"), cue.get("end_seconds")
        _require(_number(start) and _number(end) and 0 <= previous_end <= start < end <= length,
                 "字幕时间轴越界、重叠或非法。")
        _require(0.3 <= end - start <= 12, "字幕应按语句分段，不能闪过或整分钟不换。")
        _require(_text(cue.get("text")) and _text(cue.get("source_quote"))
                 and cue["source_quote"] in source, "旁白字幕缺少文本或原文依据。")
        _require(len(cue["text"]) <= 80, "单条字幕过长，需按语句拆分。")
        previous_end = end
    _require(previous_end >= length - 1, "字幕没有覆盖旁白结尾。")


def validate_scenes(plan: dict, segments: list) -> dict:
    scenes = plan.get("scenes")
    _require(isinstance(scenes, list) and 1 <= len(scenes) <= len(segments), "缺少完整场景分组。")
    starts, ids, next_shot = {}, set(), 1
    for i, scene in enumerate(scenes):
        _require(isinstance(scene, dict), "场景格式错误。")
        identifier = scene.get("id")
        _require(isinstance(identifier, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", identifier) is not None
                 and identifier not in ids, "场景编号重复或不安全。")
        ids.add(identifier)
        start, end = scene.get("start_shot"), scene.get("end_shot")
        _require(type(start) is int and type(end) is int and start == next_shot <= end <= len(segments), "场景镜头范围缺失、重叠或越界。")
        next_shot = end + 1
        for key in ("from_state", "opening_state", "transition_reason", "identity_notes", "render_bible"):
            _require(_text(scene.get(key)), f"场景缺少 {key}。")
        if "render_negative" in scene:
            _require(_text(scene["render_negative"]) and len(scene["render_negative"]) <= 2000,
                     "场景反向约束格式错误或过长。")
        if i == 0:
            _require(scene.get("transition") == "opening" and scene["opening_state"] == plan["opening_state"], "第一个场景必须是明确的开场。")
        else:
            _require(scene.get("transition") in {"match_cut", "location_cut", "time_cut"}, "换场必须声明剪辑或时间跳转。")
        _require(_sha(scene.get("anchor_sha256")) and scene.get("parent_reference_sha256") == plan["reference"]["sha256"], "场景参考缺少摘要或统一人物来源。")
        _require(scene.get("usage_authorized") is True and scene.get("identity_claim") == "illustrative_scene_not_historical_footage", "场景参考缺少用途授权或演绎说明。")
        quotes = scene.get("source_quotes")
        _require(isinstance(quotes, list) and quotes and all(_text(q) and q in plan["source_text"] for q in quotes), "场景没有回忆原文依据。")
        starts[start] = scene
    _require(next_shot == len(segments) + 1, "场景未覆盖全部镜头。")
    return starts


def scene_for_shot(plan: dict, number: int) -> dict | None:
    if plan.get("version") == 2:
        return next(scene for scene in plan["scenes"] if scene["start_shot"] <= number <= scene["end_shot"])
    return None


def render_plan_for_shot(plan: dict, number: int) -> dict:
    scene = scene_for_shot(plan, number)
    if not scene:
        return plan
    rendered = {**plan, "render_bible": scene["render_bible"]}
    # Explicit scene constraints replace global prop exclusions. Generic identity
    # guards still come from build_wan_graph; no scene text is silently filtered.
    if "render_negative" in scene:
        rendered["render_negative"] = scene["render_negative"]
    return rendered


def film_contract_digest(plan: dict) -> str:
    validate_film_contract(plan)
    try:
        encoded = json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise PackageError("整片方案不能安全序列化。") from exc
    return hashlib.sha256(encoded).hexdigest()
