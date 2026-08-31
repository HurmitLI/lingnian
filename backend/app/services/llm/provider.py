from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from app.core.config import PROJECT_ROOT, get_settings
from app.schemas.api import QuestionOutput, StoryOrganizationOutput


QUESTION_BANK = {
    "童年": "小时候，有没有一件现在想起来还很清楚的小事？",
    "求学": "上学的时候，有没有一位老师或同学让您一直记到现在？",
    "工作": "刚开始工作时，哪件事最让您印象深刻？",
    "婚恋": "年轻时，有没有一段相识相伴的往事愿意讲给家人听？",
    "育儿": "孩子小时候，有没有一件让您至今还会笑起来的事？",
    "价值观": "这些年里，您最想留给晚辈的一句话是什么？",
    "老物件": "家里有没有一件旧物，背后藏着一段您愿意讲的故事？",
}


def generate_local_question(preferred_name: str, life_stage: str) -> QuestionOutput:
    return MockLLMProvider().generate_question(preferred_name, life_stage)


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    def generate_question(self, preferred_name: str, life_stage: str) -> QuestionOutput: ...

    def organize_story(self, corrected_text: str, question: str) -> StoryOrganizationOutput: ...


class MockLLMProvider:
    provider_name = "mock"
    model_name = "mock-llm-v1"

    def generate_question(self, preferred_name: str, life_stage: str) -> QuestionOutput:
        question = QUESTION_BANK.get(life_stage, "有没有一段您愿意慢慢讲给家人听的往事？")
        return QuestionOutput(
            question=question,
            reason=f"从{life_stage}阶段选择一个开放式问题。",
            safety_check="safe",
        )

    def organize_story(self, corrected_text: str, question: str) -> StoryOrganizationOutput:
        uncertainties = ["原文含有听不清内容，请家人核对。"] if "[听不清]" in corrected_text else []
        title = "一段愿意留给家人的回忆"
        return StoryOrganizationOutput(
            title=title,
            body=corrected_text.strip(),
            timeline_mentions=[],
            people_mentions=[],
            uncertainties=uncertainties,
            source_coverage=1.0,
        )


def parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回 JSON 对象。")
    return json.loads(text[start : end + 1])


class QwenLLMProvider:
    provider_name = "qwen"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("未配置 LLM_API_KEY。")
        self.model_name = settings.llm_model
        self.client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        prompt_root = PROJECT_ROOT / "backend/app/prompts"
        self.question_prompt = (prompt_root / "memory_question_v1.md").read_text("utf-8")
        self.story_prompt = (prompt_root / "story_organizer_v1.md").read_text("utf-8")

    def _complete(self, system_prompt: str, user_content: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            extra_body={"enable_thinking": False},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("主模型返回了空内容。")
        return content

    def generate_question(self, preferred_name: str, life_stage: str) -> QuestionOutput:
        raw = self._complete(
            self.question_prompt,
            json.dumps(
                {"preferred_name": preferred_name, "life_stage": life_stage}, ensure_ascii=False
            ),
        )
        return QuestionOutput.model_validate(parse_json_object(raw))

    def organize_story(self, corrected_text: str, question: str) -> StoryOrganizationOutput:
        raw = self._complete(
            self.story_prompt,
            json.dumps(
                {"question": question, "corrected_transcript": corrected_text}, ensure_ascii=False
            ),
        )
        payload = parse_json_object(raw)
        if not str(payload.get("body", "")).strip():
            raise RuntimeError("INSUFFICIENT_STORY_CONTENT")
        return StoryOrganizationOutput.model_validate(payload)


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    if settings.llm_provider == "qwen":
        return QwenLLMProvider()
    raise RuntimeError(f"不支持的 LLM_PROVIDER：{settings.llm_provider}")
