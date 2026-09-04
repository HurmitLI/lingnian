from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.services.tts import prepare_tts_text


@dataclass(frozen=True)
class ProductionMedia:
    source_path: Path
    archive_path: str
    mime_type: str
    sha256: str


def _storyboard(body: str, *, place_name: str | None, event_year: int | None) -> list[dict]:
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])|\n+", body)
        if item.strip()
    ]
    if not sentences:
        sentences = [body.strip()]
    scenes: list[dict] = []
    for index, narration in enumerate(sentences[:8], start=1):
        context = "、".join(
            item for item in (str(event_year) if event_year else None, place_name) if item
        )
        scenes.append(
            {
                "scene": index,
                "narration": narration,
                "spoken_narration": prepare_tts_text(narration),
                "source": "人工确认故事原文",
                "visual_direction": (
                    f"家庭纪实影像风格；{context + '；' if context else ''}"
                    "只表现原文明确提到的环境和动作，不新增具体人物身份、事件或年代细节。"
                ),
                "review_required": True,
            }
        )
    return scenes


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
    external_upload_authorized: bool = False,
    package_status: str = "local_preproduction_only",
) -> Path:
    """Create a local-only, provider-neutral production handoff package."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    storyboard = _storyboard(body, place_name=place_name, event_year=event_year)
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
    else:
        plan_path = "production/storyboard.json"
        plan = {"scenes": storyboard}
    target_label = {
        "photo_restore": "老照片修复副本",
        "portrait_video": "人物讲述视频",
        "scene_video": "故事情景视频",
    }[generation_type]
    preview_instruction = (
        "先生成低分辨率修复预览，家人并排确认后再输出高清副本。"
        if generation_type == "photo_restore"
        else "先制作 5 至 10 秒低成本预览，确认人物、口型、情绪和画面后再生成完整视频。"
    )
    manifest = {
        "format": "lingnian-generation-production-package",
        "version": 1,
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
