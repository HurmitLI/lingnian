from __future__ import annotations

import re

from app.schemas.api import StoryOrganizationOutput


NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


def detect_added_facts(source: str, output: StoryOrganizationOutput) -> list[str]:
    added: list[str] = []
    source_numbers = set(NUMBER_PATTERN.findall(source))
    for value in NUMBER_PATTERN.findall(output.body):
        if value not in source_numbers:
            added.append(f"整理稿出现原文没有的数字：{value}")

    for person in output.people_mentions:
        if person and person not in source:
            added.append(f"人物引用未在校对稿中出现：{person}")

    for mention in output.timeline_mentions:
        if mention.expression not in source:
            added.append(f"时间表达未在校对稿中出现：{mention.expression}")
        if mention.normalized and mention.normalized not in source:
            added.append(f"标准化时间需要人工核对：{mention.normalized}")

    return list(dict.fromkeys(added))

