from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, get_settings
from app.schemas.api import (
    InterviewCleanupOutput,
    InterviewFollowupOutput,
    QuestionOutput,
    StoryOrganizationOutput,
)


QUESTION_BANK = {
    "童年": "小时候，有没有一件现在想起来还很清楚的小事？",
    "求学": "上学的时候，有没有一位老师或同学让您一直记到现在？",
    "工作": "刚开始工作时，哪件事最让您印象深刻？",
    "婚恋": "年轻时，有没有一段相识相伴的往事愿意讲给家人听？",
    "育儿": "孩子小时候，有没有一件让您至今还会笑起来的事？",
    "价值观": "这些年里，您最想留给晚辈的一句话是什么？",
    "老物件": "家里有没有一件旧物，背后藏着一段您愿意讲的故事？",
}

FOLLOWUP_BANK = {
    "童年": [
        "那时候家里住的地方是什么样的？",
        "这件事里，您印象最深的是谁？",
        "当时您心里是什么感受？",
        "后来这件事还影响过您吗？",
        "关于那段童年，还有哪个小细节您愿意留下？",
    ],
    "求学": [
        "当时学校和教室是什么样的？",
        "有没有一位老师或同学让您印象很深？",
        "那时候上学最不容易的是什么？",
        "有没有一件现在想起来还会笑的事？",
        "那段求学经历后来怎样影响了您？",
    ],
    "工作": [
        "刚到那个地方时，您最先注意到的是什么？",
        "当时和您一起做事的人里，谁让您印象最深？",
        "那份工作最辛苦的地方是什么？",
        "有没有一件让您觉得自己做得很好的事？",
        "现在回头看，那段工作经历留给了您什么？",
    ],
    "婚恋": [
        "您还记得第一次见面时的情景吗？",
        "当时最打动您的是什么？",
        "一起生活以后，有哪件小事一直记到现在？",
        "遇到难处的时候，你们通常怎么一起面对？",
        "关于这段相伴，您最想让晚辈记住什么？",
    ],
    "育儿": [
        "孩子小时候最像谁，又最爱做什么？",
        "当时照顾孩子最辛苦的是什么？",
        "有没有一件现在想起来还会笑的事？",
        "孩子长大以后，哪一刻让您特别欣慰？",
        "如果能对那时的自己说句话，您会说什么？",
    ],
    "价值观": [
        "是什么经历让您慢慢有了这个想法？",
        "您有没有把这个道理讲给家里人听过？",
        "人生遇到难处时，什么一直支撑着您？",
        "现在最想提醒晚辈少走哪一段弯路？",
        "还有哪句话是您希望家里人以后仍能记得的？",
    ],
    "照片": [
        "这张照片最早是谁保存下来的？",
        "关于照片里的人，家里还流传着什么说法？",
        "这张照片拍摄前后，家里发生过什么事？",
        "哪些是您亲身记得的，哪些是后来听长辈说的？",
        "关于这张照片，还有什么需要向其他家人核实？",
    ],
    "老物件": [
        "这件东西最早是谁用过的？",
        "它平时放在哪里，家里人怎么使用它？",
        "围绕这件东西，发生过什么让您难忘的事？",
        "它后来为什么被一直保存下来？",
        "关于它，还有什么需要向其他家人核实？",
    ],
}


def generate_local_question(preferred_name: str, life_stage: str) -> QuestionOutput:
    return MockLLMProvider().generate_question(preferred_name, life_stage)


def generate_local_interview_followup(
    subject_name: str,
    narrator_name: str,
    life_stage: str,
    turns: list[dict[str, str]],
) -> InterviewFollowupOutput:
    return MockLLMProvider().generate_interview_followup(
        subject_name, narrator_name, life_stage, turns
    )


def clean_local_interview_transcript(text: str) -> InterviewCleanupOutput:
    cleaned = re.sub(r"\s+", "", text.strip())
    cleaned = re.sub(
        r"(^|[，。！？；])(?:(?:嗯+|呃+|额+|啊+|这个|那个)[，、 ]*)+",
        r"\1",
        cleaned,
    )
    cleaned = re.sub(r"(?:嗯+|呃+|额+)(?=[，。！？；]|$)", "", cleaned)
    cleaned = re.sub(r"([，。！？；])\1+", r"\1", cleaned)
    cleaned = cleaned.lstrip("，、； ")
    if cleaned and cleaned[-1] not in "。！？!?":
        cleaned += "。"
    uncertainties = ["原转写含有听不清内容，请在整场采访结束后核对。"] if "[听不清]" in cleaned else []
    return InterviewCleanupOutput(
        polished_text=cleaned or text.strip(),
        uncertainties=uncertainties,
    )


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    def generate_question(self, preferred_name: str, life_stage: str) -> QuestionOutput: ...

    def generate_interview_followup(
        self,
        subject_name: str,
        narrator_name: str,
        life_stage: str,
        turns: list[dict[str, str]],
    ) -> InterviewFollowupOutput: ...

    def clean_interview_transcript(self, text: str) -> InterviewCleanupOutput: ...

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

    def clean_interview_transcript(self, text: str) -> InterviewCleanupOutput:
        return clean_local_interview_transcript(text)

    def generate_interview_followup(
        self,
        subject_name: str,
        narrator_name: str,
        life_stage: str,
        turns: list[dict[str, str]],
    ) -> InterviewFollowupOutput:
        questions = FOLLOWUP_BANK.get(life_stage, FOLLOWUP_BANK["童年"])
        latest_answer = turns[-1]["answer"].strip() if turns else ""
        reluctant = any(
            phrase in latest_answer
            for phrase in ("不记得", "想不起来", "不知道", "不想说", "不愿意")
        )
        turn_count = len(turns)
        if reluctant:
            acknowledgement = "没关系，不记得或者不想讲都可以。"
        else:
            acknowledgement = "我听到了，谢谢您把这段记忆讲下来。"
        return InterviewFollowupOutput(
            acknowledgement=acknowledgement,
            next_question=questions[min(turn_count - 1, len(questions) - 1)],
            uncertainties=[],
            should_end=turn_count >= 7,
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


def _has_meaningful_story_content(value: str) -> bool:
    compact = re.sub(r"[\s，。！？、,.!?…~～—-]", "", value.strip())
    without_fillers = re.sub(r"[啊阿呀哦噢喔嗯呃额诶哎唉哈呵哼嘛呢吧啦喽]", "", compact)
    without_fillers = re.sub(
        r"^(?:没了|没有了|不知道|不记得|想不起来|不想说)*$",
        "",
        without_fillers,
    )
    return len(without_fillers) >= 4


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(
                item.get("name")
                or item.get("value")
                or item.get("expression")
                or ""
            ).strip()
        else:
            text = str(item).strip()
        if text:
            items.append(text[:300])
    return items[:limit]


def normalize_story_payload(payload: dict, corrected_text: str) -> dict:
    """Tolerate common JSON-shape drift without inventing story facts."""
    title = str(payload.get("title") or "一段愿意留给家人的回忆").strip()[:200]
    body = str(payload.get("body") or "").strip()
    if not body:
        if not _has_meaningful_story_content(corrected_text):
            raise RuntimeError("INSUFFICIENT_STORY_CONTENT")
        body = corrected_text.strip()

    timeline_mentions: list[dict[str, object]] = []
    raw_timeline = payload.get("timeline_mentions")
    if isinstance(raw_timeline, list):
        for item in raw_timeline:
            if isinstance(item, str):
                expression = item.strip()
                normalized = None
                confidence = "uncertain"
            elif isinstance(item, dict):
                expression = str(item.get("expression") or item.get("time") or "").strip()
                raw_normalized = item.get("normalized")
                normalized = (
                    None if raw_normalized in (None, "") else str(raw_normalized)[:40]
                )
                confidence = (
                    item.get("confidence")
                    if item.get("confidence") in {"confirmed", "uncertain"}
                    else "uncertain"
                )
            else:
                continue
            if expression:
                timeline_mentions.append(
                    {
                        "expression": expression[:160],
                        "normalized": normalized,
                        "confidence": confidence,
                    }
                )

    raw_coverage = payload.get("source_coverage", 1.0)
    try:
        if isinstance(raw_coverage, str) and raw_coverage.strip().endswith("%"):
            source_coverage = float(raw_coverage.strip().rstrip("%")) / 100
        else:
            source_coverage = float(raw_coverage)
    except (TypeError, ValueError):
        source_coverage = 1.0
    source_coverage = min(1.0, max(0.0, source_coverage))

    return {
        "title": title or "一段愿意留给家人的回忆",
        "body": body[:100_000],
        "timeline_mentions": timeline_mentions,
        "people_mentions": _string_list(payload.get("people_mentions"), limit=100),
        "uncertainties": _string_list(payload.get("uncertainties"), limit=100),
        "source_coverage": source_coverage,
    }


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
        self.interview_prompt = (prompt_root / "interview_followup_v1.md").read_text("utf-8")
        self.cleanup_prompt = (prompt_root / "interview_cleanup_v1.md").read_text("utf-8")
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
        try:
            payload = normalize_story_payload(parse_json_object(raw), corrected_text)
            return StoryOrganizationOutput.model_validate(payload)
        except RuntimeError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
            if not _has_meaningful_story_content(corrected_text):
                raise RuntimeError("INSUFFICIENT_STORY_CONTENT")
            # The model call succeeded but its JSON shape drifted. Keeping the
            # reviewed transcript verbatim is safer than losing the interview
            # or trying to infer facts from a malformed response.
            return StoryOrganizationOutput(
                title="一段愿意留给家人的回忆",
                body=corrected_text.strip(),
                timeline_mentions=[],
                people_mentions=[],
                uncertainties=[],
                source_coverage=1.0,
            )

    def clean_interview_transcript(self, text: str) -> InterviewCleanupOutput:
        raw = self._complete(
            self.cleanup_prompt,
            json.dumps({"raw_transcript": text}, ensure_ascii=False),
        )
        return InterviewCleanupOutput.model_validate(parse_json_object(raw))

    def generate_interview_followup(
        self,
        subject_name: str,
        narrator_name: str,
        life_stage: str,
        turns: list[dict[str, str]],
    ) -> InterviewFollowupOutput:
        raw = self._complete(
            self.interview_prompt,
            json.dumps(
                {
                    "memory_subject": subject_name,
                    "narrator": narrator_name,
                    "life_stage": life_stage,
                    "turns": turns[-6:],
                },
                ensure_ascii=False,
            ),
        )
        return InterviewFollowupOutput.model_validate(parse_json_object(raw))


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    if settings.llm_provider == "qwen":
        return QwenLLMProvider()
    raise RuntimeError(f"不支持的 LLM_PROVIDER：{settings.llm_provider}")
