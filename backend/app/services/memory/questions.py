from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MemorySession, QuestionPrompt, TopicPreference


DEFAULT_QUESTIONS = {
    "童年": [
        "小时候，有没有一件现在想起来还很清楚的小事？",
        "小时候住过的地方，有哪个角落您一直记得？",
        "小时候家里有没有一种熟悉的声音或味道？",
    ],
    "求学": [
        "上学的时候，有没有一位老师或同学让您一直记到现在？",
        "第一次走进学校时，您还记得当时看见了什么吗？",
        "学生时代有没有一件让您觉得自己长大了的事？",
    ],
    "工作": [
        "刚开始工作时，哪件事最让您印象深刻？",
        "工作里有没有一位教会您很多东西的人？",
        "哪一次工作经历让您觉得自己很有成就感？",
    ],
    "婚恋": [
        "年轻时，有没有一段相识相伴的往事愿意讲给家人听？",
        "两个人一起生活后，有哪件小事让您一直觉得温暖？",
        "如果愿意，您想从哪一段相伴的日子讲起？",
    ],
    "育儿": [
        "孩子小时候，有没有一件让您至今还会笑起来的事？",
        "第一次照顾孩子时，有什么情景您一直没有忘记？",
        "陪孩子长大时，哪一刻让您觉得很欣慰？",
    ],
    "价值观": [
        "这些年里，您最想留给晚辈的一句话是什么？",
        "遇到难处的时候，什么想法一直支撑着您？",
        "您觉得一家人相处，最重要的是什么？",
    ],
    "老物件": [
        "家里有没有一件旧物，背后藏着一段您愿意讲的故事？",
        "有没有一件东西，虽然普通却一直舍不得丢？",
        "看到哪件旧物时，您会马上想起一个人或一段日子？",
    ],
}


@dataclass(frozen=True)
class SelectedQuestion:
    prompt_id: str
    question_text: str


def ensure_question_bank(db: Session) -> None:
    if db.scalar(select(func.count(QuestionPrompt.id))) > 0:
        return
    for stage, questions in DEFAULT_QUESTIONS.items():
        for index, question in enumerate(questions, start=1):
            db.add(
                QuestionPrompt(
                    prompt_key=f"{stage}-builtin-{index}-v1",
                    life_stage=stage,
                    question_text=question,
                    sensitivity="careful" if stage == "婚恋" else "normal",
                    version=1,
                    enabled=True,
                    source="built_in",
                )
            )
    db.flush()


def select_question(
    db: Session, *, elder_id: str, life_stage: str, topic_confirmed: bool = False
) -> SelectedQuestion:
    preference = db.scalar(
        select(TopicPreference).where(
            TopicPreference.elder_id == elder_id,
            TopicPreference.topic_key == life_stage,
        )
    )
    if preference and preference.preference == "avoid":
        raise ValueError("TOPIC_BLOCKED_BY_PREFERENCE")
    if preference and preference.preference == "ask_first" and not topic_confirmed:
        raise ValueError("TOPIC_CONFIRMATION_REQUIRED")

    ensure_question_bank(db)
    prompts = db.scalars(
        select(QuestionPrompt)
        .where(
            QuestionPrompt.life_stage == life_stage,
            QuestionPrompt.enabled.is_(True),
        )
        .order_by(QuestionPrompt.prompt_key)
    ).all()
    if not prompts:
        return SelectedQuestion(
            prompt_id=f"{life_stage}-fallback-v1",
            question_text="有没有一段您愿意慢慢讲给家人听的往事？",
        )

    usage = {
        prompt_id: count
        for prompt_id, count in db.execute(
            select(MemorySession.prompt_id, func.count(MemorySession.id))
            .where(
                MemorySession.elder_id == elder_id,
                MemorySession.prompt_id.in_([item.prompt_key for item in prompts]),
            )
            .group_by(MemorySession.prompt_id)
        ).all()
    }
    selected = min(
        prompts, key=lambda item: (usage.get(item.prompt_key, 0), item.prompt_key)
    )
    return SelectedQuestion(
        prompt_id=selected.prompt_key,
        question_text=selected.question_text,
    )
