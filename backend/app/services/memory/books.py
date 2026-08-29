from __future__ import annotations

import hashlib
from collections import defaultdict

from app.models import ElderProfile, Story


STAGE_ORDER = ["童年", "求学", "工作", "婚恋", "育儿", "价值观", "老物件"]


def render_memory_book(
    profile: ElderProfile, stories: list[Story], *, title: str
) -> tuple[str, list[dict]]:
    grouped: dict[str, list[Story]] = defaultdict(list)
    for story in stories:
        grouped[story.source_draft.session.life_stage].append(story)

    ordered_stages = [stage for stage in STAGE_ORDER if stage in grouped]
    ordered_stages.extend(sorted(set(grouped) - set(ordered_stages)))
    lines = [f"# {title}", "", f"> 讲述者：{profile.preferred_name}", "> 本文件只收录经过家庭成员人工确认的故事。", ""]
    manifest: list[dict] = []
    for stage in ordered_stages:
        lines.extend([f"## {stage}", ""])
        for story in sorted(grouped[stage], key=lambda item: item.confirmed_at):
            lines.extend(
                [
                    f"### {story.title}",
                    "",
                    story.body.strip(),
                    "",
                    f"_来源故事：{story.id}；确认人：{story.confirmed_by}_",
                    "",
                ]
            )
            manifest.append(
                {
                    "story_id": story.id,
                    "life_stage": stage,
                    "source_draft_id": story.source_draft_id,
                    "confirmed_at": story.confirmed_at.isoformat(),
                }
            )
    content = "\n".join(lines).rstrip() + "\n"
    return content, manifest


def markdown_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
