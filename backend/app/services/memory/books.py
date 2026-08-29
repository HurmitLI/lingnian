from __future__ import annotations

import hashlib
import html
from collections import defaultdict
from pathlib import Path

from app.models import ElderProfile, Story
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


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


FONT_NAME = "NiannianCJK"
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
]


def _register_cjk_font() -> str:
    if FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return FONT_NAME
    font_path = next((path for path in FONT_CANDIDATES if path.is_file()), None)
    if font_path is None:
        raise RuntimeError("PDF_CJK_FONT_MISSING")
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(font_path)))
    return FONT_NAME


def render_memory_book_pdf(
    profile: ElderProfile,
    stories: list[Story],
    *,
    title: str,
    output_path: Path,
) -> str:
    font_name = _register_cjk_font()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=24 * mm,
        rightMargin=24 * mm,
        topMargin=23 * mm,
        bottomMargin=22 * mm,
        title=title,
        author=profile.preferred_name,
        subject="聆年家庭回忆录",
    )
    base = getSampleStyleSheet()
    cover = ParagraphStyle(
        "Cover",
        parent=base["Title"],
        fontName=font_name,
        fontSize=28,
        leading=40,
        alignment=TA_CENTER,
        textColor=HexColor("#74312b"),
        spaceAfter=18 * mm,
    )
    subtitle = ParagraphStyle(
        "Subtitle",
        parent=base["Normal"],
        fontName=font_name,
        fontSize=12,
        leading=22,
        alignment=TA_CENTER,
        textColor=HexColor("#706d62"),
    )
    stage_style = ParagraphStyle(
        "Stage",
        parent=base["Heading1"],
        fontName=font_name,
        fontSize=20,
        leading=28,
        textColor=HexColor("#74312b"),
        spaceBefore=4 * mm,
        spaceAfter=5 * mm,
    )
    story_title_style = ParagraphStyle(
        "StoryTitle",
        parent=base["Heading2"],
        fontName=font_name,
        fontSize=15,
        leading=23,
        textColor=HexColor("#29271f"),
        spaceBefore=6 * mm,
        spaceAfter=3 * mm,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName=font_name,
        fontSize=11,
        leading=21,
        firstLineIndent=22,
        textColor=HexColor("#29271f"),
        spaceAfter=3 * mm,
    )
    source_style = ParagraphStyle(
        "Source",
        parent=base["Normal"],
        fontName=font_name,
        fontSize=8.5,
        leading=14,
        textColor=HexColor("#706d62"),
        spaceAfter=5 * mm,
    )

    grouped: dict[str, list[Story]] = defaultdict(list)
    for story in stories:
        grouped[story.source_draft.session.life_stage].append(story)
    ordered_stages = [stage for stage in STAGE_ORDER if stage in grouped]
    ordered_stages.extend(sorted(set(grouped) - set(ordered_stages)))

    flow = [
        Spacer(1, 42 * mm),
        Paragraph(html.escape(title), cover),
        Paragraph(f"讲述者：{html.escape(profile.preferred_name)}", subtitle),
        Spacer(1, 8 * mm),
        Paragraph("只收录经过家庭成员人工确认的故事", subtitle),
        PageBreak(),
    ]
    for stage in ordered_stages:
        flow.append(Paragraph(html.escape(stage), stage_style))
        for story in sorted(grouped[stage], key=lambda item: item.confirmed_at):
            flow.append(Paragraph(html.escape(story.title), story_title_style))
            paragraphs = [part.strip() for part in story.body.splitlines() if part.strip()]
            for paragraph in paragraphs or [story.body.strip()]:
                flow.append(Paragraph(html.escape(paragraph), body_style))
            flow.append(
                Paragraph(
                    f"来源故事：{story.id}　确认人：{html.escape(story.confirmed_by)}",
                    source_style,
                )
            )

    def draw_page_number(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 8.5)
        canvas.setFillColor(HexColor("#8a867c"))
        canvas.drawCentredString(A4[0] / 2, 11 * mm, f"聆年 · 第 {doc.page} 页")
        canvas.restoreState()

    document.build(flow, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    return hashlib.sha256(output_path.read_bytes()).hexdigest()
