"""Source-bound contract for native, multi-shot memory films.

Planning is separate from generation: model output never becomes an executable
ComfyUI graph, and only quoted source passages become narration/subtitles.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Character(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    source_label: str = Field(min_length=1, max_length=80)
    appearance: str = Field(min_length=10, max_length=700)
    wardrobe: str = Field(min_length=5, max_length=300)


class MemoryShot(StrictModel):
    scene: int = Field(ge=1, le=30)
    source_quote: str = Field(min_length=1, max_length=1200)
    kind: Literal["interview", "memory_action", "detail", "environment"]
    character_ids: list[str] = Field(max_length=4)
    location: str = Field(min_length=1, max_length=300)
    opening_prompt: str = Field(min_length=10, max_length=1800)
    motion_prompt: str = Field(min_length=10, max_length=1200)
    prop_owners: dict[str, str] = Field(default_factory=dict, max_length=8)


class MemoryDirection(StrictModel):
    characters: list[Character] = Field(min_length=1, max_length=4)
    reference_prompt: str = Field(min_length=10, max_length=1800)
    shots: list[MemoryShot] = Field(min_length=6, max_length=30)

    @model_validator(mode="after")
    def references_exist(self):
        ids = {c.id for c in self.characters}
        if len(ids) != len(self.characters):
            raise ValueError("人物标识重复")
        for shot in self.shots:
            if len(set(shot.character_ids)) != len(shot.character_ids) or not set(shot.character_ids) <= ids:
                raise ValueError("镜头引用了未定义的人物")
            if any(owner not in ids | {"none"} for owner in shot.prop_owners.values()):
                raise ValueError("道具归属不明确")
        # Unshown handovers caused continuity errors in the native experiment.
        owners: dict[str, str] = {}
        for shot in self.shots:
            for prop, owner in shot.prop_owners.items():
                # An unattended object is not a new owner. Retain the last real
                # holder so putting it down cannot conceal a later handover.
                if owner == "none":
                    continue
                if prop in owners and owners[prop] != owner:
                    raise ValueError("同一道具不能在镜头间无交接地更换持有人")
                owners[prop] = owner
        return self


def source_segments(body: str, duration: int) -> list[dict]:
    if duration not in {30, 45, 60, 90} or not body.strip() or len(body) > 20000:
        raise ValueError("故事内容或目标时长不正确")
    # Preserve every character. Adjacent short clauses are merged; long clauses
    # are split at punctuation where possible. These are editorial slots, not ASR.
    count = duration // 5
    units = [s for s in re.split(r"(?<=[。！？；，\n])", body.strip()) if s]
    while len(units) > count:
        i = min(range(len(units) - 1), key=lambda i: len(units[i]) + len(units[i + 1]))
        units[i:i + 2] = [units[i] + units[i + 1]]
    while len(units) < count:
        i = max(range(len(units)), key=lambda i: len(units[i]))
        if len(units[i]) < 2:
            raise ValueError("故事太短，不能靠重复画面凑目标时长")
        cut = len(units[i]) // 2
        units[i:i + 1] = [units[i][:cut], units[i][cut:]]
    return [{"scene": i + 1, "source_quote": quote, "duration_seconds": 5,
             "start_seconds": i * 5} for i, quote in enumerate(units)]


def validate_direction(value: dict, *, body: str, duration: int, slots: list[dict] | None = None) -> dict:
    direction = MemoryDirection.model_validate(value)
    if any(character.source_label not in body for character in direction.characters):
        raise ValueError("人物称谓缺少故事原文依据")
    slots = slots if slots is not None else source_segments(body, duration)
    if len(direction.shots) != len(slots):
        raise ValueError("分镜数量与原文时间槽不匹配")
    for slot, shot in zip(slots, direction.shots):
        if shot.scene != slot["scene"] or shot.source_quote != slot["source_quote"]:
            raise ValueError("分镜改写、遗漏或重排了原文")
        if any(not re.search(r"[A-Za-z]{3,}", p) for p in (shot.opening_prompt, shot.motion_prompt)):
            raise ValueError("模型画面描述必须含完整英文主体和动作")
    result = direction.model_dump()
    result["shots"] = [{**shot, **slot} for shot, slot in zip(result["shots"], slots)]
    result["source_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    result["timing_basis"] = "editorial_equal_slots_not_forced_audio_alignment"
    result["identity_claim"] = "illustrative_unless_authorized_photo_provided"
    return result


def planning_input(body: str, duration: int, *, has_photo: bool, slots: list[dict] | None = None) -> str:
    return json.dumps({"story": body, "slots": slots if slots is not None else source_segments(body, duration),
                       "authorized_photo_available": has_photo}, ensure_ascii=False)
