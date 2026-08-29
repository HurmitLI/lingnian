#!/usr/bin/env python3
"""为聆年只读演示空间生成原创音色、旁白和故事视频。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RUNTIME_DIR = ROOT / "data" / "runtime" / "showcase"
PUBLIC_DIR = ROOT / "frontend" / "public" / "showcase"
VOICE_STATE = RUNTIME_DIR / "voice.json"
PREVIEW_PATH = RUNTIME_DIR / "voice-preview.wav"
VOICE_MODEL = "qwen3-tts-vd-2026-01-26"
VOICE_DESIGN_MODEL = "qwen-voice-design"
VOICE_NAME_PREFIX = "lingnian_suqin"
API_BASE = "https://dashscope.aliyuncs.com/api/v1"
COSYVOICE_PREVIEWS = {
    "calm": ("longyingjing_v3", 0.80, PUBLIC_DIR / "voice-preview-calm.wav"),
    "warm": ("longyan_v3", 1.00, PUBLIC_DIR / "voice-preview-warm.wav"),
}
COSYVOICE_WARM_VOICE = "longyan_v3"
COSYVOICE_WARM_RATE = 1.00

VOICE_PROMPTS = {
    "a": (
        "一位六十到六十五岁的中国女性，音色温暖偏低，带有自然的岁月感和轻微沙哑，"
        "气息真实，普通话清楚但保留一点江南地区的柔和语调。说话像在家中向女儿慢慢讲往事，"
        "不是播音腔，不刻意煽情；语速偏慢，句间有自然停顿，克制中带着温柔和回忆感。"
    ),
    "b": (
        "一位六十到六十五岁的江南女性，声音偏低、温暖、略带沙哑，能听见真实呼吸和岁月感。"
        "她不是专业播音员，而是在安静的家里对女儿口述旧事；语速缓慢，逗号处会自然停一下，"
        "句尾轻轻收住。普通话清楚，带一点柔和的江南语调。情绪克制而真诚，讲到母亲时有怀念，"
        "但不哭腔、不表演、不朗诵。"
    ),
    "c": (
        "一位六十岁左右的中国普通女性，声音偏低，略带沙哑和轻微鼻音，气息和声带质感真实，"
        "不是专业配音员。她坐在家里的饭桌边，想到哪里说到哪里，偶尔会停一下再继续，"
        "句子长短不一，句尾有时轻、有时收住，不保持整齐统一的播音节奏。普通话清楚，"
        "带一点柔和的江南口音。讲到母亲时有克制的怀念，但不哭、不朗诵、不表演，"
        "不要广告腔、播音腔、有声书腔和过度清晰的人工配音感。"
    ),
}

STORY_SEGMENTS = [
    {
        "life_stage": "1982 年春 · 第一次离家",
        "title": "我第一次一个人坐火车去无锡",
        "text": (
            "这个蓝布包啊，我一直没舍得扔。八二年春天吧，我十九岁，头一回一个人坐火车，去无锡的纺织厂。"
            "出门前，我妈也没说什么，就往包里塞了两个煮鸡蛋，一个搪瓷缸，还有一块手帕。"
            "手帕边上啊，她用红线缝了四针。她就说了一句，到了宿舍，先给家里写封信。"
        ),
        "image": "shen-suqin-station-1982.png",
    },
    {
        "life_stage": "县城月台 · 火车开了",
        "title": "她没有追，只抬了一下手",
        "text": (
            "火车开的时候，我隔着车窗，看见她还站在月台上。她没追，就抬了一下手。"
            "那时候年轻啊，心里想的都是远方。我甚至觉得，她一直站在那儿，好像有点多余。"
        ),
        "image": "shen-suqin-station-1982.png",
    },
    {
        "life_stage": "多年以后 · 我也做了母亲",
        "title": "不是舍不得你走，是想让你放心走",
        "text": (
            "后来，我自己也做了母亲。女儿第一次离家去读书，我在车站外面一直等，等到车开远了才回去。"
            "就是那个时候吧，我突然明白，我妈当年为什么只抬了一下手。不是舍不得你走，是想让你放心走。"
        ),
        "image": "shen-suqin-home.png",
    },
    {
        "life_stage": "四十二年 · 三次搬家",
        "title": "包里早就没东西了。其实不是",
        "text": (
            "这个包啊，跟着我搬过三次家。拉链早就坏了，提手也换过。去年女儿收拾柜子，问我，"
            "里面什么都没有了，怎么还留着。我嘴上说，忘了扔。其实不是。我妈那天没说出口的话，还在里面呢。"
        ),
        "image": "shen-suqin-blue-bag.png",
    },
    {
        "life_stage": "现在 · 想再说给母亲听",
        "title": "一辈子吃过最香的两个鸡蛋",
        "text": (
            "要是她现在还在啊，我就想跟她说，那两个鸡蛋，我在车上一个都没舍得吃。"
            "到了无锡，打开包才发现，都压裂了。可我到现在还记得，那是我这一辈子，吃过最香的两个鸡蛋。"
        ),
        "image": "shen-suqin-blue-bag.png",
    },
]


def _api_key() -> str:
    load_dotenv(ROOT / ".env", override=False)
    key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("LLM_API_KEY")
    if not key:
        raise RuntimeError("没有找到 LLM_API_KEY 或 DASHSCOPE_API_KEY。")
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _post_json(path: str, payload: dict) -> dict:
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{API_BASE}{path}", headers=_headers(), json=payload)
    if response.status_code != 200:
        try:
            error = response.json()
            message = error.get("message") or error.get("code") or "未知错误"
        except ValueError:
            message = "服务返回了非 JSON 错误"
        raise RuntimeError(f"百炼调用失败（HTTP {response.status_code}）：{message}")
    return response.json()


def create_voice_preview(*, suffix: str) -> Path:
    voice_prompt = VOICE_PROMPTS.get(suffix, VOICE_PROMPTS["b"])
    payload = {
        "model": VOICE_DESIGN_MODEL,
        "input": {
            "action": "create",
            "target_model": VOICE_MODEL,
            "preferred_name": f"{VOICE_NAME_PREFIX}_{suffix}",
            "voice_prompt": voice_prompt,
            "preview_text": (
                "这个蓝布包，我一直没舍得扔。1982 年春天，我十九岁，"
                "第一次一个人坐火车去无锡的纺织厂。"
            ),
        },
        "parameters": {"sample_rate": 24000, "response_format": "wav"},
    }
    result = _post_json("/services/audio/tts/customization", payload)
    output = result.get("output", {})
    voice = output.get("voice")
    preview = output.get("preview_audio", {}).get("data")
    if not voice or not preview:
        raise RuntimeError("声音设计成功响应中缺少 voice 或 preview_audio。")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    preview_path = RUNTIME_DIR / f"voice-preview-{suffix}.wav"
    preview_path.write_bytes(base64.b64decode(preview))
    VOICE_STATE.write_text(
        json.dumps(
            {"voice": voice, "model": VOICE_MODEL, "suffix": suffix},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    shutil.copyfile(preview_path, PREVIEW_PATH)
    print(f"音色预览已保存：{preview_path}")
    return preview_path


def synthesize_cosyvoice(
    text: str,
    destination: Path,
    *,
    voice: str,
    rate: float,
    seed: int,
) -> int:
    payload = {
        "model": "cosyvoice-v3-flash",
        "input": {
            "text": text,
            "voice": voice,
            "format": "wav",
            "sample_rate": 24000,
            "volume": 62,
            "rate": rate,
            "pitch": 0.88,
            "language_hints": ["zh"],
            "seed": seed,
            "enable_aigc_tag": True,
            "aigc_propagator": "LingNian",
        },
    }
    result = _post_json("/services/audio/tts/SpeechSynthesizer", payload)
    audio_url = result.get("output", {}).get("audio", {}).get("url")
    if not audio_url:
        raise RuntimeError("CosyVoice 响应中缺少音频地址。")
    response: httpx.Response | None = None
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for attempt in range(5):
            response = client.get(audio_url.replace("http://", "https://", 1))
            if response.status_code == 200:
                break
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
            time.sleep(2**attempt)
    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "未知"
        raise RuntimeError(f"CosyVoice 音频下载连续失败（HTTP {status}）。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    return result.get("usage", {}).get("characters", len(text))


def create_cosyvoice_preview(*, style: str) -> Path:
    voice, rate, destination = COSYVOICE_PREVIEWS[style]
    preview_text = (
        "这个蓝布包啊，我一直没舍得扔。八二年春天吧，我十九岁，"
        "头一回一个人坐火车，去无锡的纺织厂。出门前，我妈也没说什么……"
        "就往包里塞了两个煮鸡蛋，还有一块手帕。"
    )
    usage = synthesize_cosyvoice(
        preview_text,
        destination,
        voice=voice,
        rate=rate,
        seed=1982,
    )
    print(f"CosyVoice {style} 试听已保存：{destination}")
    print(f"计费字符数：{usage}")
    return destination


def _voice_state() -> dict:
    if not VOICE_STATE.is_file():
        raise RuntimeError("尚未生成音色，请先运行 create-preview。")
    return json.loads(VOICE_STATE.read_text(encoding="utf-8"))


def synthesize_segment(text: str, destination: Path) -> None:
    state = _voice_state()
    payload = {
        "model": state["model"],
        "input": {"text": text, "voice": state["voice"]},
    }
    result = _post_json("/services/aigc/multimodal-generation/generation", payload)
    audio_url = result.get("output", {}).get("audio", {}).get("url")
    if not audio_url:
        raise RuntimeError("语音合成成功响应中缺少音频地址。")
    secure_audio_url = audio_url.replace("http://", "https://", 1)
    response: httpx.Response | None = None
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for attempt in range(5):
            response = client.get(secure_audio_url)
            if response.status_code == 200:
                break
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
            time.sleep(2**attempt)
    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "未知"
        raise RuntimeError(f"旁白已经生成，但临时音频下载连续失败（HTTP {status}）。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)


def render_showcase(*, reuse_audio: bool, voice_provider: str) -> tuple[Path, int]:
    sys.path.insert(0, str(BACKEND))
    from app.services.keepsake.renderer import render_keepsake_video

    audio_dir = RUNTIME_DIR / ("audio-cosyvoice-warm" if voice_provider == "cosyvoice-warm" else "audio")
    work_dir = RUNTIME_DIR / ("video-work-cosyvoice-warm" if voice_provider == "cosyvoice-warm" else "video-work")
    clips: list[dict] = []
    for index, segment in enumerate(STORY_SEGMENTS, start=1):
        audio_path = audio_dir / f"segment-{index:02d}.wav"
        if reuse_audio and audio_path.is_file():
            print(f"正在复用第 {index}/{len(STORY_SEGMENTS)} 段旁白……")
        else:
            print(f"正在合成第 {index}/{len(STORY_SEGMENTS)} 段旁白……")
            if voice_provider == "cosyvoice-warm":
                synthesize_cosyvoice(
                    segment["text"],
                    audio_path,
                    voice=COSYVOICE_WARM_VOICE,
                    rate=COSYVOICE_WARM_RATE,
                    seed=1982 + index,
                )
            else:
                synthesize_segment(segment["text"], audio_path)
        clips.append(
            {
                "audio_path": audio_path,
                "story_title": segment["title"],
                "story_excerpt": segment["text"],
                "life_stage": segment["life_stage"],
                "image_path": PUBLIC_DIR / segment["image"],
            }
        )

    output_path = PUBLIC_DIR / "shen-suqin-story.mp4"
    duration_ms = render_keepsake_video(
        work_dir=work_dir,
        output_path=output_path,
        keepsake_title="包里还装着那天没说完的话",
        clips=clips,
        width=1280,
        height=720,
        max_source_seconds=300,
        timeout_seconds=300,
        footer_text="聆年 · 家庭记忆",
        media_comment="聆年 · 家庭记忆影像",
        audio_filter="loudnorm=I=-16:TP=-1.5:LRA=11",
    )
    print(f"故事视频已生成：{output_path}")
    print(f"总时长：{duration_ms / 1000:.1f} 秒")
    return output_path, duration_ms


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview_parser = subparsers.add_parser("create-preview")
    preview_parser.add_argument("--suffix", required=True)
    cosyvoice_parser = subparsers.add_parser("create-cosyvoice-preview")
    cosyvoice_parser.add_argument("--style", choices=sorted(COSYVOICE_PREVIEWS), required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--reuse-audio", action="store_true")
    render_parser.add_argument(
        "--voice-provider",
        choices=("qwen", "cosyvoice-warm"),
        default="qwen",
    )
    args = parser.parse_args()

    if args.command == "create-preview":
        create_voice_preview(suffix=args.suffix)
    elif args.command == "create-cosyvoice-preview":
        create_cosyvoice_preview(style=args.style)
    else:
        render_showcase(reuse_audio=args.reuse_audio, voice_provider=args.voice_provider)


if __name__ == "__main__":
    main()
