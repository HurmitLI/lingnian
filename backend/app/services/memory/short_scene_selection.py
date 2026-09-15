"""Pure request/response boundary for semantic scene selection.

No external calls, DB mutations, implicit consent or visual approval. The route
that eventually calls a provider must atomically reserve a NEW purpose-specific
grant for this exact input hash; story-organization consent is not reusable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .short_scene import propose_short_scenes, with_sentence_timing


PURPOSE = "short_scene_selection"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "short_scene_selection_v1.md"


def _input_digest(system: str, user: str) -> str:
    return hashlib.sha256(json.dumps({"purpose": PURPOSE, "system": system, "user": user},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class InterviewContext(StrictModel):
    subject_label: str | None = Field(default=None, min_length=1, max_length=160)
    narrator_label: str | None = Field(default=None, min_length=1, max_length=160)
    narrator_is_subject: bool | None = None

    @model_validator(mode="after")
    def named_roles_required(self):
        if self.narrator_is_subject is not None and (not self.subject_label or not self.narrator_label):
            raise ValueError("没有身份依据时不能猜测是否本人讲述")
        return self


class Fact(StrictModel):
    field: Literal["character", "location", "era", "wardrobe", "prop"]
    status: Literal["known", "unknown"]
    value: str | None = Field(default=None, min_length=1, max_length=500)
    source_quotes: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def evidence_required(self):
        if self.status == "known" and (not self.value or not self.source_quotes):
            raise ValueError("已知事实必须有原文依据")
        if self.status == "unknown" and (self.value is not None or self.source_quotes):
            raise ValueError("未知内容不能夹带猜测值")
        return self


class Scene(StrictModel):
    context_summary: str = Field(min_length=1, max_length=1500)
    action_kind: Literal["standing_waiting", "looking_around", "walking_slowly", "sitting_remembering", "holding_object"]
    opening_state: str = Field(min_length=1, max_length=600)
    action: str = Field(min_length=1, max_length=600)
    facts: list[Fact] = Field(min_length=5, max_length=5)
    illustrative_details: list[str] = Field(max_length=10)
    source_quotes: list[str] = Field(min_length=1, max_length=6)
    render_bible: str = Field(min_length=1, max_length=2500)
    render_action: str = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def finite_single_scene(self):
        if {item.field for item in self.facts} != {"character", "location", "era", "wardrobe", "prop"}:
            raise ValueError("事实字段不能重复或遗漏")
        if any(not value.strip() or len(value) > 1000 for value in self.illustrative_details + self.source_quotes):
            raise ValueError("依据或示意说明为空或过长")
        if len((self.render_bible + " " + self.render_action).split()) > 220:
            raise ValueError("渲染描述过长")
        return self


class Selection(StrictModel):
    decision: Literal["selected", "unsuitable"]
    candidate_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=1500)
    scene: Scene | None = None

    @model_validator(mode="after")
    def consistent_decision(self):
        if self.decision == "selected" and (self.candidate_id is None or self.scene is None):
            raise ValueError("选择缺少候选或场景")
        if self.decision == "unsuitable" and (self.candidate_id is not None or self.scene is not None):
            raise ValueError("不合适时不能偷偷继续生成")
        return self


def prepare_selection_request(metadata: dict, *, raw_answers: dict[str, str], audio_asset_id: str,
                              reference_mode: Literal["user_photo", "illustrative"],
                              raw_questions: dict[str, str] | None = None,
                              interview_context: dict | None = None) -> dict:
    if reference_mode not in {"user_photo", "illustrative"}:
        raise ValueError("SHORT_SCENE_REFERENCE_MODE_INVALID")
    aligned = with_sentence_timing(metadata, raw_answers)
    preview = propose_short_scenes(aligned, audio_asset_id=audio_asset_id)
    if not preview["candidates"]:
        return {"status": preview["status"], "generation_ready": False, "external_call_ready": False}
    context = []
    for segment in aligned["interview_timeline"]["segments"]:
        if segment["role"] == "answer":
            text = raw_answers.get(segment.get("turn_id"))
            if not isinstance(text, str) or not text.strip():
                raise ValueError("SHORT_SCENE_FULL_CONTEXT_MISSING")
            question = None if raw_questions is None else raw_questions.get(segment.get("turn_id"))
            if raw_questions is not None and (not isinstance(question, str) or not question.strip()):
                raise ValueError("SHORT_SCENE_QUESTION_CONTEXT_MISSING")
            context.append({"speaker_role": "interview_answer", "text": text,
                            "question": question, "question_is_fact_evidence": False})
    full_text = "\n".join(item["text"] for item in context)
    if len(full_text) > 20000 or any(candidate["text"] not in full_text for candidate in preview["candidates"]):
        raise ValueError("SHORT_SCENE_RAW_TEXT_MISMATCH")
    # Send only necessary text and opaque candidate IDs. Audio/photo paths,
    # file hashes, family/session IDs and credentials are not provider payload.
    participants = InterviewContext.model_validate(interview_context or {}).model_dump()
    payload = {"version": 2, "reference_mode": reference_mode, "answers": context,
               "interview_context": participants,
               "candidates": [{k: c[k] for k in ("id", "text", "duration_seconds")} for c in preview["candidates"]]}
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(user) > 30000:
        raise ValueError("SHORT_SCENE_CONTEXT_TOO_LONG")
    request_hash = _input_digest(system, user)
    return {"status": "awaiting_purpose_specific_consent", "purpose": PURPOSE, "input_sha256": request_hash,
            "system_prompt": system, "user_content": user, "payload": payload,
            "generation_ready": False, "external_call_ready": False}


def validate_selection_response(request: dict, response: dict, *, input_sha256: str) -> dict:
    """Check against freshly re-prepared server request, not user-owned JSON.

    A passing response is a STRUCTURALLY GROUNDED proposal. A matching quote
    alone cannot prove the model understood relationships or historical facts.
    """
    if (request.get("status") != "awaiting_purpose_specific_consent"
            or request.get("purpose") != PURPOSE or request.get("input_sha256") != input_sha256):
        raise ValueError("SHORT_SCENE_MODEL_INPUT_CHANGED")
    if (not isinstance(request.get("system_prompt"), str) or not isinstance(request.get("user_content"), str)
            or _input_digest(request["system_prompt"], request["user_content"]) != input_sha256
            or json.loads(request["user_content"]) != request.get("payload")):
        raise ValueError("SHORT_SCENE_MODEL_INPUT_CHANGED")
    selection = Selection.model_validate(response)
    if selection.decision == "unsuitable":
        return {"status": "no_suitable_scene", "reason": selection.reason, "generation_ready": False}
    candidate = next((c for c in request["payload"]["candidates"] if c["id"] == selection.candidate_id), None)
    if candidate is None:
        raise ValueError("SHORT_SCENE_MODEL_CANDIDATE_INVALID")
    scene = selection.scene
    assert scene is not None
    full_text = "\n".join(a["text"] for a in request["payload"]["answers"])
    if any(q not in candidate["text"] for q in scene.source_quotes):
        raise ValueError("SHORT_SCENE_SCENE_QUOTE_OUTSIDE_EXCERPT")
    for fact in scene.facts:
        if fact.status == "known" and (any(not q.strip() or q not in full_text for q in fact.source_quotes)
                                        or not any(fact.value in q for q in fact.source_quotes)):
            raise ValueError("SHORT_SCENE_FACT_NOT_GROUNDED")
    return {"status": "awaiting_scene_context_review", "input_sha256": input_sha256,
            "selection": selection.model_dump(), "candidate": dict(candidate),
            "generation_ready": False, "semantic_accepted": False, "visual_accepted": False,
            "notice": "仅验证格式与逐字出处；人物关系、地点方向、年代和画面仍须核对，尚未调用GPU。"}
