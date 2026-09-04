from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.services.tts import prepare_tts_text


DOCUMENTARY_DURATIONS = {45, 60, 90}
DOCUMENTARY_ASPECT_RATIOS = {"16:9", "9:16"}


@dataclass(frozen=True)
class ProductionMedia:
    source_path: Path
    archive_path: str
    mime_type: str
    sha256: str


def normalize_production_spec(
    generation_type: str,
    production_spec: dict | None = None,
) -> dict:
    """Return the stable, provider-neutral contract sent to a generation node."""

    requested = production_spec or {}
    if generation_type != "scene_video":
        return {}
    duration = requested.get("target_duration_seconds", 60)
    aspect_ratio = requested.get("aspect_ratio", "16:9")
    if duration not in DOCUMENTARY_DURATIONS:
        duration = 60
    if aspect_ratio not in DOCUMENTARY_ASPECT_RATIOS:
        aspect_ratio = "16:9"
    width, height = (1280, 720) if aspect_ratio == "16:9" else (720, 1280)
    return {
        "format_version": 1,
        "target_duration_seconds": duration,
        "aspect_ratio": aspect_ratio,
        "narrative_style": "family_documentary",
        "voice_strategy": "original_recording_first",
        "synthetic_voice_allowed": False,
        "single_photo_max_screen_ratio": 0.35,
        "subtitles_required": True,
        "output": {"width": width, "height": height, "fps": 24},
    }


def _story_units(body: str) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?])|\n+", body) if item.strip()]
    if len(sentences) >= 4:
        return sentences
    clauses = [item.strip() for item in re.split(r"(?<=[，；,;。！？!?])|\n+", body) if item.strip()]
    return clauses or [body.strip()]


def _select_evenly(items: list[str], limit: int) -> list[str]:
    if len(items) <= limit:
        return items
    if limit <= 1:
        return ["".join(items)]

    # A documentary plan must not silently drop the year, place or object merely
    # because spoken Chinese was split into many short comma clauses.  Merge the
    # shortest neighbouring clauses until the requested shot count is reached so
    # every confirmed word remains represented in the storyboard.
    selected = list(items)
    while len(selected) > limit:
        merge_at = min(
            range(len(selected) - 1),
            key=lambda index: len(selected[index]) + len(selected[index + 1]),
        )
        selected[merge_at : merge_at + 2] = [
            f"{selected[merge_at]}{selected[merge_at + 1]}"
        ]
    return selected


def _scene_durations(total_seconds: int, scene_count: int) -> list[int]:
    opening = 4
    closing = 5
    middle_count = max(1, scene_count - 2)
    available = total_seconds - opening - closing
    base, extra = divmod(available, middle_count)
    return [opening, *[base + (1 if index < extra else 0) for index in range(middle_count)], closing]


def _documentary_storyboard(
    body: str,
    *,
    title: str,
    story_id: str,
    place_name: str | None,
    event_year: int | None,
    has_image: bool,
    production_spec: dict,
) -> list[dict]:
    target_duration = production_spec["target_duration_seconds"]
    target_scene_count = {45: 6, 60: 8, 90: 10}[target_duration]
    quotes = _select_evenly(_story_units(body), target_scene_count - 2)
    durations = _scene_durations(target_duration, len(quotes) + 2)
    context = "、".join(
        item for item in (str(event_year) if event_year else None, place_name) if item
    )
    period_guard = ""
    if event_year:
        period_guard = (
            f"时代固定为{event_year}年前后的中国；服装、交通工具、建筑、室内陈设和物件都要符合当时，"
            "不得出现现代高楼天际线、现代汽车、智能手机或当代商业标识。"
        )
    place_guard = (
        f"地点背景仅限{place_name}；无法准确还原时使用不含地标的近景物件，不用通用城市航拍替代。"
        if place_name
        else ""
    )
    story_context = body.strip()[:600]
    scenes: list[dict] = [
        {
            "scene": 1,
            "kind": "title_card",
            "duration_seconds": durations[0],
            "narration": "",
            "spoken_narration": "",
            "subtitle": title,
            "source": "人工确认故事标题",
            "source_story_id": story_id,
            "visual_direction": "使用克制的家庭档案标题卡，不增加人物或事件信息。",
            "camera_motion": "none",
            "transition": "fade",
            "review_required": True,
        }
    ]
    motions = ("slow_push", "slow_pan_left", "slow_pan_right", "gentle_pull_back")
    photo_slots: set[int] = set()
    if has_image:
        photo_budget = target_duration * production_spec["single_photo_max_screen_ratio"]
        photo_seconds = 0
        for slot in dict.fromkeys((0, max(0, len(quotes) - 1))):
            slot_seconds = durations[slot + 1]
            if photo_seconds + slot_seconds <= photo_budget:
                photo_slots.add(slot)
                photo_seconds += slot_seconds
    for index, quote in enumerate(quotes):
        uses_photo = index in photo_slots
        scene_number = index + 2
        scenes.append(
            {
                "scene": scene_number,
                "kind": "archival_photo" if uses_photo else "documentary_context",
                "duration_seconds": durations[index + 1],
                "narration": quote,
                "spoken_narration": prepare_tts_text(quote),
                "subtitle": quote,
                "source": "人工确认故事原文",
                "source_story_id": story_id,
                "visual_direction": (
                    "使用已授权原图做轻微纪录片运镜，保留人物身份、五官和原始构图。"
                    if uses_photo
                    else (
                        f"家庭纪实空镜或物件意象；{context + '；' if context else ''}"
                        f"整段已确认故事仅为「{story_context}」；当前镜头必须直接对应「{quote}」。"
                        f"{period_guard}{place_guard}"
                        "优先中近景、物件、动作和环境细节，禁止无关城市航拍或通用城市全景；"
                        "不生成具体真人正脸，不新增身份、对白、因果、地点或年代事实。"
                    )
                ),
                "camera_motion": motions[index % len(motions)],
                "transition": "crossfade",
                "review_required": True,
            }
        )
    scenes.append(
        {
            "scene": len(scenes) + 1,
            "kind": "source_card",
            "duration_seconds": durations[-1],
            "narration": "",
            "spoken_narration": "",
            "subtitle": "这段记忆来自家人确认的口述与家庭档案",
            "source": "聆年档案来源说明",
            "source_story_id": story_id,
            "visual_direction": "使用简洁来源卡收束影片，不展示未经授权的个人信息。",
            "camera_motion": "none",
            "transition": "fade",
            "review_required": True,
        }
    )
    return scenes


def _legacy_storyboard(
    body: str,
    *,
    story_id: str,
    place_name: str | None,
    event_year: int | None,
) -> list[dict]:
    sentences = _story_units(body)
    context = "、".join(
        item for item in (str(event_year) if event_year else None, place_name) if item
    )
    return [
        {
            "scene": index,
            "narration": narration,
            "spoken_narration": prepare_tts_text(narration),
            "subtitle": narration,
            "source": "人工确认故事原文",
            "source_story_id": story_id,
            "visual_direction": (
                f"家庭纪实影像风格；{context + '；' if context else ''}"
                "只表现原文明确提到的环境和动作，不新增具体人物身份、事件或年代细节。"
            ),
            "review_required": True,
        }
        for index, narration in enumerate(sentences[:8], start=1)
    ]


def build_documentary_plan(
    *,
    story_id: str,
    title: str,
    body: str,
    place_name: str | None,
    event_year: int | None,
    has_image: bool,
    production_spec: dict | None = None,
) -> dict:
    normalized_spec = normalize_production_spec("scene_video", production_spec)
    scenes = _documentary_storyboard(
        body,
        title=title,
        story_id=story_id,
        place_name=place_name,
        event_year=event_year,
        has_image=has_image,
        production_spec=normalized_spec,
    )
    return {
        "format": "lingnian-documentary-storyboard",
        "version": 2,
        "production_spec": normalized_spec,
        "audio_plan": {
            "strategy": "original_recording_first",
            "exact_story_alignment_required": True,
            "synthetic_voice_allowed": False,
            "fallback": "没有对应原声时保留字幕和环境声，不仿制讲述者声音。",
        },
        "scenes": scenes,
        "review_checklist": [
            "声音完整",
            "画面自然",
            "故事一致",
            "镜头连贯",
            "没有新增家庭事实",
            "时长合适",
        ],
    }


def build_production_package(
    *,
    output_path: Path,
    generation_type: str,
    storyteller_name: str,
    story_id: str,
    title: str,
    body: str,
    life_stage: str,
    place_name: str | None,
    event_year: int | None,
    theme_tags: list[str],
    actor_label: str,
    subject_consent: bool,
    rights_confirmed: bool,
    no_impersonation: bool,
    audio: ProductionMedia | None,
    image: ProductionMedia | None,
    production_spec: dict | None = None,
    external_upload_authorized: bool = False,
    package_status: str = "local_preproduction_only",
) -> Path:
    """Create a local-only, provider-neutral production handoff package."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    normalized_spec = normalize_production_spec(generation_type, production_spec)
    documentary_plan = None
    if generation_type == "scene_video":
        documentary_plan = build_documentary_plan(
            title=title,
            story_id=story_id,
            body=body,
            place_name=place_name,
            event_year=event_year,
            has_image=image is not None,
            production_spec=normalized_spec,
        )
        storyboard = documentary_plan["scenes"]
    else:
        storyboard = _legacy_storyboard(
            body,
            story_id=story_id,
            place_name=place_name,
            event_year=event_year,
        )
    if generation_type == "photo_restore":
        plan_path = "production/restoration-plan.json"
        plan = {
            "operations": ["去除灰尘和划痕", "修复褪色与局部破损", "保留原始构图和人物特征"],
            "constraints": [
                "只生成独立副本，绝不覆盖原图",
                "不凭空增加人物、服饰、文字、场景或年代细节",
                "修复前后必须由家人并排核对",
            ],
            "review_required": True,
        }
    elif generation_type == "scene_video":
        plan_path = "production/storyboard.json"
        plan = documentary_plan
    else:
        plan_path = "production/storyboard.json"
        plan = {
            "format": "lingnian-storyboard",
            "version": 1,
            "production_spec": normalized_spec,
            "audio_plan": {
                "strategy": "original_recording_first",
                "exact_story_alignment_required": True,
                "synthetic_voice_allowed": False,
                "fallback": "没有对应原声时保留字幕和环境声，不仿制讲述者声音。",
            },
            "scenes": storyboard,
            "review_checklist": ["声音完整", "嘴型与停顿自然", "人物与故事一致", "时长合适"],
        }
    target_label = {
        "photo_restore": "老照片修复副本",
        "portrait_video": "人物讲述视频",
        "scene_video": "纪实故事影片",
    }[generation_type]
    preview_instruction = (
        "先生成低分辨率修复预览，家人并排确认后再输出高清副本。"
        if generation_type == "photo_restore"
        else "先制作 5 至 10 秒低成本预览，确认人物、口型、情绪和画面后再生成完整视频。"
    )
    manifest = {
        "format": "lingnian-generation-production-package",
        "version": 2 if generation_type == "scene_video" else 1,
        "status": package_status,
        "generated_at": generated_at,
        "generation_type": generation_type,
        "story": {
            "id": story_id,
            "title": title,
            "body": body,
            "life_stage": life_stage,
            "place_name": place_name,
            "event_year": event_year,
            "theme_tags": theme_tags,
        },
        "authorization": {
            "actor_label": actor_label,
            "subject_consent": subject_consent,
            "rights_confirmed": rights_confirmed,
            "no_impersonation": no_impersonation,
            "external_upload_authorized": external_upload_authorized,
        },
        "production_spec": normalized_spec,
        "media": [
            {
                "path": item.archive_path,
                "mime_type": item.mime_type,
                "sha256": item.sha256,
            }
            for item in (audio, image)
            if item
        ],
        "plan_path": plan_path,
    }
    readme = f"""# 聆年生成制作包

讲述者：{storyteller_name}
故事：{title}
目标：{target_label}

这个 ZIP 只在本机整理素材，没有上传第三方，也没有生成最终视频或产生模型费用。

## 使用顺序

1. 家人先检查 `{plan_path}`，确认修复或镜头计划没有增加原资料之外的事实。
2. 选择供应商和工作流后，再逐次确认是否允许上传 `sources` 目录中的照片与原声。
3. {preview_instruction}
4. 最终成片必须保留“经授权的家庭记忆演绎”说明，不得用于冒充本人或误导公众。

生成时间：{generated_at}
"""
    with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("README.md", readme)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("production/story.txt", f"{title}\n\n{body}\n")
        archive.writestr(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        for media in (audio, image):
            if media:
                archive.write(media.source_path, media.archive_path)
    output_path.chmod(0o600)
    return output_path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
