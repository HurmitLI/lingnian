from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class HeritageMedia:
    source_path: Path
    archive_path: str
    mime_type: str


@dataclass(frozen=True)
class HeritageStory:
    story_id: str
    title: str
    body: str
    life_stage: str
    confirmed_at: str
    place_name: str | None = None
    event_year: int | None = None
    theme_tags: list[str] = field(default_factory=list)
    contributions: list[dict] = field(default_factory=list)
    audio: HeritageMedia | None = None
    image: HeritageMedia | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _story_html(story: HeritageStory) -> str:
    meta = [story.life_stage]
    if story.event_year:
        meta.append(str(story.event_year))
    if story.place_name:
        meta.append(story.place_name)
    tags = "".join(f"<span>{html.escape(tag)}</span>" for tag in story.theme_tags)
    image = (
        f'<img loading="lazy" src="{html.escape(story.image.archive_path)}" alt="{html.escape(story.title)}相关家庭照片">'
        if story.image
        else ""
    )
    audio = (
        f'<audio controls preload="metadata" src="{html.escape(story.audio.archive_path)}"></audio>'
        if story.audio
        else ""
    )
    contributions = ""
    if story.contributions:
        rows = "".join(
            "<li><strong>"
            + html.escape(str(item.get("contributor_label", "家人")))
            + "</strong> · "
            + html.escape(str(item.get("type_label", "补充")))
            + "<p>"
            + html.escape(str(item.get("body", "")))
            + "</p></li>"
            for item in story.contributions
        )
        contributions = f"<details><summary>家人补充（{len(story.contributions)}）</summary><ul>{rows}</ul></details>"
    body = "".join(
        f"<p>{html.escape(paragraph.strip())}</p>"
        for paragraph in story.body.splitlines()
        if paragraph.strip()
    )
    return f"""
    <article id="story-{html.escape(story.story_id)}" data-search="{html.escape(story.title + story.body + ' '.join(meta) + ' '.join(story.theme_tags))}">
      <div class="story-meta">{' · '.join(html.escape(item) for item in meta)}</div>
      <h2>{html.escape(story.title)}</h2>
      <div class="tags">{tags}</div>
      {image}
      <div class="story-body">{body}</div>
      {audio}
      {contributions}
      <small>家庭确认时间：{html.escape(story.confirmed_at)}</small>
    </article>
    """


def build_heritage_package(
    *,
    output_path: Path,
    family_name: str,
    storyteller_name: str,
    profile: dict,
    people: list[dict],
    relationships: list[dict],
    stories: list[HeritageStory],
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(output_path)
    generated_at = datetime.now(UTC).isoformat()
    story_payload = [
        {
            "story_id": item.story_id,
            "title": item.title,
            "body": item.body,
            "life_stage": item.life_stage,
            "confirmed_at": item.confirmed_at,
            "place_name": item.place_name,
            "event_year": item.event_year,
            "theme_tags": item.theme_tags,
            "contributions": item.contributions,
            "audio_path": item.audio.archive_path if item.audio else None,
            "image_path": item.image.archive_path if item.image else None,
        }
        for item in stories
    ]
    articles = "".join(_story_html(item) for item in stories)
    index_html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(storyteller_name)}的家庭记忆</title>
<style>
:root{{--paper:#f6f1e8;--ink:#27231f;--muted:#746d63;--line:#d9cfc1;--accent:#8b3d32}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:17px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
header,main,footer{{width:min(820px,calc(100% - 32px));margin:auto}}header{{padding:64px 0 28px;border-bottom:1px solid var(--line)}}
h1{{font-family:serif;font-size:clamp(34px,7vw,58px);margin:0 0 8px}}header p,.story-meta,small{{color:var(--muted)}}
.search{{position:sticky;top:0;background:color-mix(in srgb,var(--paper) 92%,transparent);padding:14px 0;backdrop-filter:blur(12px);z-index:2}}
input{{width:100%;font:inherit;border:1px solid var(--line);border-radius:999px;padding:11px 18px;background:#fffdf8}}
article{{padding:42px 0;border-bottom:1px solid var(--line)}}h2{{font-family:serif;font-size:30px;line-height:1.3;margin:8px 0 12px}}
.tags span{{display:inline-block;margin:0 7px 8px 0;padding:2px 10px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:13px}}
img{{display:block;width:100%;max-height:520px;object-fit:cover;border-radius:16px;margin:20px 0}}audio{{width:100%;margin:16px 0}}
details{{margin:18px 0;padding:12px 16px;border-left:3px solid var(--accent);background:#fff9f0}}li p{{margin:0 0 10px}}footer{{padding:32px 0 60px;color:var(--muted)}}
@media print{{.search{{display:none}}body{{background:#fff}}article{{break-inside:avoid}}}}
</style></head><body>
<header><p>{html.escape(family_name)} · 家庭记忆传承包</p><h1>{html.escape(storyteller_name)}的故事</h1><p>{len(stories)} 篇经过家人确认的故事，连同原声、照片和家人补充一起保存。</p></header>
<main><div class="search"><input id="search" type="search" placeholder="搜索人物、地点、故事或主题" aria-label="搜索家庭记忆"></div>{articles}</main>
<footer>由聆年导出于 {html.escape(generated_at)}。这是家庭私密资料，请由家人妥善保存。</footer>
<script>const q=document.querySelector('#search');q.addEventListener('input',()=>{{const v=q.value.trim().toLowerCase();document.querySelectorAll('article').forEach(x=>x.hidden=v&&!x.dataset.search.toLowerCase().includes(v))}});</script>
</body></html>"""
    files: list[dict] = []
    with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("index.html", index_html)
        archive.writestr(
            "data/stories.json",
            json.dumps(story_payload, ensure_ascii=False, indent=2) + "\n",
        )
        archive.writestr(
            "data/family.json",
            json.dumps(
                {
                    "format": "lingnian-family-heritage",
                    "version": 1,
                    "generated_at": generated_at,
                    "family_name": family_name,
                    "profile": profile,
                    "people": people,
                    "relationships": relationships,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        archive.writestr(
            "请先阅读.txt",
            "这是聆年生成的开放格式家庭记忆传承包。\n\n"
            "1. 解压后打开 index.html 即可阅读并播放本包内的媒体。\n"
            "2. data 目录保留结构化 JSON，便于未来迁移。\n"
            "3. 本包不包含主密钥、恢复口令、API Key 或模型凭据。\n"
            "4. 请至少保存两份，并放在不同的可靠存储设备中。\n",
        )
        media_items = [media for story in stories for media in (story.audio, story.image) if media]
        for media in media_items:
            archive.write(media.source_path, media.archive_path)
            files.append(
                {
                    "path": media.archive_path,
                    "mime_type": media.mime_type or mimetypes.guess_type(media.archive_path)[0],
                    "size_bytes": media.source_path.stat().st_size,
                    "sha256": _sha256(media.source_path),
                }
            )
        archive.writestr(
            "data/media-manifest.json",
            json.dumps({"files": files}, ensure_ascii=False, indent=2) + "\n",
        )
    output_path.chmod(0o600)
    return output_path
