from __future__ import annotations

import re
from dataclasses import dataclass


_STOP_CHARS = set("的了呢吗啊呀和与及是有在把被给想问一下关于请告诉我我们你们他们她们这个那个什么怎样怎么为什么")
_SYNONYMS = {
    "工作": ("工作", "上班", "单位", "职业", "做工", "厂"),
    "家乡": ("家乡", "故乡", "老家", "出生", "籍贯"),
    "搬家": ("搬家", "搬到", "迁", "离开", "来到"),
    "认识": ("认识", "相识", "遇见", "介绍", "媒人"),
    "结婚": ("结婚", "成家", "婚礼", "对象"),
    "童年": ("童年", "小时候", "小时", "儿时", "小的时候"),
    "学校": ("学校", "上学", "读书", "老师", "同学"),
    "过年": ("过年", "春节", "年夜饭", "拜年"),
    "孩子": ("孩子", "儿子", "女儿", "育儿", "出生"),
}


@dataclass(frozen=True)
class SearchDocument:
    story_id: str
    title: str
    body: str
    life_stage: str
    audio_url: str | None
    image_url: str | None


@dataclass(frozen=True)
class RankedDocument:
    document: SearchDocument
    score: float
    excerpt: str


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    chunks = re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", normalized)
    terms: set[str] = set()
    for chunk in chunks:
        if re.fullmatch(r"[a-z0-9]+", chunk):
            if len(chunk) > 1:
                terms.add(chunk)
            continue
        meaningful = "".join(char for char in chunk if char not in _STOP_CHARS)
        for size in (2, 3, 4):
            for index in range(max(0, len(meaningful) - size + 1)):
                terms.add(meaningful[index : index + size])
        if meaningful:
            terms.add(meaningful)
    for key, values in _SYNONYMS.items():
        if key in normalized or any(value in normalized for value in values):
            terms.update(values)
    return {term for term in terms if term}


def _best_excerpt(text: str, query_terms: set[str], *, limit: int = 180) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])|\n+", text) if part.strip()]
    if not parts:
        return text.strip()[:limit]
    best = max(parts, key=lambda part: sum(1 for term in query_terms if term in part))
    if len(best) <= limit:
        return best
    return best[: limit - 1].rstrip() + "…"


def rank_archive(question: str, documents: list[SearchDocument]) -> list[RankedDocument]:
    query_terms = _terms(question)
    ranked: list[RankedDocument] = []
    for document in documents:
        title_terms = _terms(document.title)
        body_terms = _terms(document.body)
        stage_terms = _terms(document.life_stage)
        title_hits = query_terms & title_terms
        body_hits = query_terms & body_terms
        stage_hits = query_terms & stage_terms
        exact_bonus = 4 if question.strip() in f"{document.title}{document.body}" else 0
        raw_score = len(title_hits) * 4 + len(body_hits) * 1.5 + len(stage_hits) * 2 + exact_bonus
        if raw_score <= 0:
            continue
        denominator = max(3, min(12, len(query_terms)))
        score = round(min(1.0, raw_score / denominator), 3)
        ranked.append(
            RankedDocument(
                document=document,
                score=score,
                excerpt=_best_excerpt(document.body, query_terms),
            )
        )
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def compose_grounded_answer(question: str, ranked: list[RankedDocument]) -> tuple[str, str | None]:
    if not ranked:
        return (
            "现有的已确认故事里还没有找到足够依据。聆年不会替家人猜测这段经历，可以把它留成下一次采访的问题。",
            question.strip().rstrip("？?") + "，您愿意从头讲讲吗？",
        )
    first = ranked[0]
    answer = f"在已确认的家庭档案里，最相关的是《{first.document.title}》：{first.excerpt}"
    if len(ranked) > 1:
        answer += f" 另外，《{ranked[1].document.title}》也记录了相关片段。"
    answer += " 下方保留了原故事来源；请以家人确认过的原文和原声为准。"
    return answer, None
