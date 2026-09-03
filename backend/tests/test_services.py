from __future__ import annotations

import pytest

from app.core.errors import DomainError
from app.schemas.api import StoryOrganizationOutput, TimelineMention
from app.services.asr.provider import normalize_chinese_spacing
from app.services.llm.provider import (
    PROMPT_ROOT,
    QwenLLMProvider,
    normalize_story_payload,
    parse_json_object,
)
from app.services.workflow.fact_guard import detect_added_facts
from app.services.workflow.state import transition


def test_fact_guard_detects_new_numbers_people_and_times():
    source = "那几年，我可能去过上海。"
    output = StoryOrganizationOutput(
        title="回忆",
        body="1987 年，我在上海工作。",
        timeline_mentions=[
            TimelineMention(expression="1987 年", normalized="1987", confidence="confirmed")
        ],
        people_mentions=["王老师"],
        uncertainties=[],
        source_coverage=1,
    )
    added = detect_added_facts(source, output)
    assert any("1987" in item for item in added)
    assert any("王老师" in item for item in added)


def test_json_parser_accepts_fenced_json():
    assert parse_json_object('```json\n{"question": "慢慢讲"}\n```') == {
        "question": "慢慢讲"
    }


def test_llm_prompts_are_resolved_from_the_deployed_application_package():
    assert PROMPT_ROOT.name == "prompts"
    assert PROMPT_ROOT.parent.name == "app"
    assert {
        "memory_question_v1.md",
        "interview_followup_v1.md",
        "interview_cleanup_v1.md",
        "story_organizer_v1.md",
    }.issubset(path.name for path in PROMPT_ROOT.iterdir())


def test_qwen_story_organizer_rejects_empty_story_body(monkeypatch):
    provider = QwenLLMProvider.__new__(QwenLLMProvider)
    provider.story_prompt = "test"
    monkeypatch.setattr(
        provider,
        "_complete",
        lambda *_: '{"title":"无有效回忆内容","body":"","timeline_mentions":[],"people_mentions":[],"uncertainties":[],"source_coverage":1}',
    )

    with pytest.raises(RuntimeError, match="INSUFFICIENT_STORY_CONTENT"):
        provider.organize_story("啊啊啊，没了", "小时候住在哪里？")


def test_qwen_story_organizer_keeps_reviewed_text_when_json_is_malformed(monkeypatch):
    provider = QwenLLMProvider.__new__(QwenLLMProvider)
    provider.story_prompt = "test"
    monkeypatch.setattr(provider, "_complete", lambda *_: "这不是 JSON")

    output = provider.organize_story("小时候我住在河边，最记得院子里的槐树。", "小时候住在哪里？")

    assert output.body == "小时候我住在河边，最记得院子里的槐树。"
    assert output.source_coverage == 1


def test_story_payload_normalizes_common_model_shape_drift():
    payload = normalize_story_payload(
        {
            "title": " 河边的童年 ",
            "body": "小时候我住在河边。",
            "timeline_mentions": [
                {"time": "小时候", "normalized": 1980, "confidence": "high"}
            ],
            "people_mentions": [{"name": "母亲"}],
            "uncertainties": None,
            "source_coverage": "100%",
        },
        "小时候我住在河边。",
    )

    output = StoryOrganizationOutput.model_validate(payload)
    assert output.timeline_mentions[0].expression == "小时候"
    assert output.timeline_mentions[0].confidence == "uncertain"
    assert output.people_mentions == ["母亲"]
    assert output.source_coverage == 1


def test_state_machine_rejects_direct_archive():
    with pytest.raises(DomainError) as error:
        transition("PROMPT_READY", "ARCHIVED")
    assert error.value.code == "INVALID_STATE_TRANSITION"


def test_chinese_asr_spacing_is_normalized():
    assert normalize_chinese_spacing("小 时 候 我 去 河 边") == "小时候我去河边"
    assert normalize_chinese_spacing("AI 帮我记录") == "AI 帮我记录"
