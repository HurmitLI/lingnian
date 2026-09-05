"""Local-only, reference-conditioned film trial. Never polls/uploads cloud jobs.

Unlike the legacy executor this consumes one reviewed whole-story treatment,
passes an actual image to Wan, carries each segment's final frame forward, and
refuses to loop short clips into a longer film. Identity still needs visual QA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw

from .comfyui import ComfyUiClient
from .media import MediaRenderer
from .models import ConfigurationError, PackageError


MODEL_FILES = {
    "unet": "wan2.2_ti2v_5B_fp16.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan2.2_vae.safetensors",
}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def validate_plan(plan: dict, reference: Path) -> None:
    if plan.get("format") != "lingnian-continuity-trial" or plan.get("version") != 1:
        raise PackageError("不是连续叙事试验方案。")
    if plan.get("fictional_only") is not True:
        raise PackageError("当前试验入口仅允许虚构样片，不接收真实家庭素材。")
    if not reference.is_file() or digest(reference) != plan.get("reference_sha256"):
        raise PackageError("统一人物参考图缺失或摘要不一致。")
    source = plan.get("source_text")
    bible = plan.get("film_bible")
    if not isinstance(source, str) or not source.strip() or not isinstance(bible, dict):
        raise PackageError("缺少完整故事和整片设定。")
    for key in ("summary", "character", "wardrobe", "prop", "era", "location", "journey", "visual_style", "forbidden_changes"):
        if not isinstance(bible.get(key), str) or not bible[key].strip():
            raise PackageError(f"整片设定缺少 {key}。")
    if plan.get("reference_policy") != "first_reference_then_previous_last_frame":
        raise PackageError("必须启用统一参考图和前段尾帧接续。")
    scenes = plan.get("segments")
    if not isinstance(scenes, list) or not 2 <= len(scenes) <= 3:
        raise PackageError("先验证 2–3 段连续短片，不直接制作长片。")
    previous_state = plan.get("opening_state")
    if not isinstance(previous_state, str) or not previous_state.strip():
        raise PackageError("缺少明确的开场状态。")
    for index, scene in enumerate(scenes, 1):
        if scene.get("id") != index or scene.get("duration_seconds") not in (4, 5):
            raise PackageError("片段必须顺序编号且每段 4–5 秒。")
        if scene.get("before") != previous_state:
            raise PackageError("前后片段状态不衔接，不能开始生成。")
        if not all(isinstance(scene.get(key), str) and scene[key].strip() for key in ("after", "action", "camera", "staging_note")):
            raise PackageError("片段缺少动作、摄影或演绎说明。")
        quotes = scene.get("source_quotes")
        if not isinstance(quotes, list) or not quotes or any(not isinstance(q, str) or not q or q not in source for q in quotes):
            raise PackageError("片段必须引用完整故事中的真实原文，不能编造出处。")
        previous_state = scene["after"]


def segment_prompt(plan: dict, segment: dict) -> str:
    bible = plan["film_bible"]
    return "\n".join([
        "把输入图作为本段第一帧。连续拍摄同一个人的同一段经历，不是独立的关键词插画。",
        f"完整故事：{plan['source_text']}",
        *[f"{key}：{value}" for key, value in bible.items()],
        f"开场状态：{segment['before']}",
        f"本段动作：{segment['action']}",
        f"摄影：{segment['camera']}",
        f"结束状态：{segment['after']}",
        "脸、发型、衣着、包的结构与颜色、列车和光线延续输入画面。动作缓慢自然，不换演员，不突然换场。",
    ])


def build_wan_graph(plan: dict, segment: dict, *, image_name: str, prefix: str) -> dict:
    if not image_name:
        raise PackageError("不能在没有参考图的情况下退回文生视频。")
    seconds = segment["duration_seconds"]
    # Wan's temporal length is 4n+1. Final delivery trims the extra boundary frame.
    frames = 4 * math.ceil(seconds * 24 / 4) + 1
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL_FILES["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODEL_FILES["clip"], "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": MODEL_FILES["vae"]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": segment_prompt(plan, segment)}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "换脸，年龄突变，服装变化，包的形状颜色变化，皮质肩带，刺绣图案，黑白，蒸汽机车，现代高铁，突然切镜，跳跃位移，肢体畸形，彩色噪纹，背景闪烁，字幕，水印"}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "start_image": ["4", 0], "width": 1280, "height": 704, "length": frames, "batch_size": 1}},
        "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": 198200 + segment["id"], "steps": 20, "cfg": 5.0, "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 24.0}},
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"}},
    }


def checked_run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise PackageError("连续影片的媒体处理失败，未提交云端。")


def normalize_segment(renderer: MediaRenderer, raw: Path, target: Path, seconds: int) -> None:
    meta = renderer.probe(raw)
    if meta["duration"] + 0.02 < seconds:
        raise PackageError("原始视频不足片段时长；禁止循环、静帧或慢放凑时长。")
    checked_run([
        renderer.ffmpeg, "-y", "-i", str(raw), "-t", str(seconds),
        "-vf", "scale=1280:704:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p",
        "-an", "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", str(target),
    ])


def tail_frame(renderer: MediaRenderer, raw: Path, target: Path, seconds: int) -> None:
    checked_run([renderer.ffmpeg, "-y", "-ss", str(seconds), "-i", str(raw), "-frames:v", "1", str(target)])
    if not target.is_file():
        raise PackageError("缺少接续尾帧，禁止重新随机开场。")


def contact_sheet(renderer: MediaRenderer, raw: Path, target: Path, seconds: int) -> None:
    canvas = Image.new("RGB", (960, 540), "#171717")
    draw = ImageDraw.Draw(canvas)
    for index, timestamp in enumerate((0, seconds / 4, seconds / 2, seconds * 0.75, seconds - 0.25, seconds)):
        frame = target.with_name(f"{target.stem}-{index}.png")
        checked_run([renderer.ffmpeg, "-y", "-ss", str(timestamp), "-i", str(raw), "-frames:v", "1", str(frame)])
        with Image.open(frame) as img:
            img.thumbnail((320, 160))
            x, y = (index % 3) * 320, (index // 3) * 270
            canvas.paste(img, (x, y))
            draw.text((x + 10, y + 170), f"{timestamp:.2f}s", fill="white")
    canvas.save(target)


def run_trial(plan_path: Path, reference: Path, output_root: Path, *, base_url: str, renderer: MediaRenderer, segments: int) -> Path:
    if urlparse(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError("试验仅连接本机 ComfyUI，不上传第三方。")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan, reference)
    if not 1 <= segments <= len(plan["segments"]):
        raise PackageError("片段数超出方案。")
    graph_version = hashlib.sha256(json.dumps(build_wan_graph(plan, plan["segments"][0], image_name="anchor", prefix="version"), sort_keys=True).encode()).hexdigest()
    fingerprint = hashlib.sha256((json.dumps(plan, sort_keys=True, ensure_ascii=False) + graph_version).encode()).hexdigest()
    target_dir = output_root / fingerprint[:16]
    target_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = target_dir / "checkpoint.json"
    state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"fingerprint": fingerprint, "segments": []}
    if state.get("fingerprint") != fingerprint:
        raise PackageError("方案或模型配置改变，不允许复用旧片段。")
    client = ComfyUiClient(base_url, plan_path, timeout_seconds=3600)
    input_image = reference
    clips = []
    try:
        with httpx.Client(base_url=base_url, timeout=30) as check:
            info_response = check.get("/object_info")
            info_response.raise_for_status()
            info = info_response.json()
            graph = build_wan_graph(plan, plan["segments"][0], image_name="anchor", prefix="check")
            if any(node["class_type"] not in info for node in graph.values()):
                raise ConfigurationError("ComfyUI 缺少 Wan 原生节点，不能退回 AnimateDiff。")
        for segment in plan["segments"][:segments]:
            index, seconds = segment["id"], segment["duration_seconds"]
            prefix = f"segment-{index:02d}"
            raw = target_dir / f"{prefix}-raw.mp4"
            clip = target_dir / f"{prefix}.mp4"
            tail = target_dir / f"{prefix}-tail.png"
            sheet = target_dir / f"{prefix}-review.png"
            previous = state["segments"][index - 1] if len(state["segments"]) >= index else None
            valid = previous and previous.get("input_sha256") == digest(input_image) and all(
                file.is_file() and previous.get(key) == digest(file)
                for file, key in ((raw, "raw_sha256"), (clip, "clip_sha256"), (tail, "tail_sha256"))
            )
            if not valid:
                # Any changed segment invalidates all later segments in the chain.
                state["segments"] = state["segments"][:index - 1]
                started = time.monotonic()
                image_name = client.upload_image(input_image)
                graph = build_wan_graph(plan, segment, image_name=image_name, prefix=f"lingnian-continuity/{fingerprint[:12]}-{index}")
                raw = client.run_workflow(graph, output_path=raw)
                if raw.suffix.lower() != ".mp4":
                    raise PackageError("连续试验只接受真实 MP4 视频。")
                normalize_segment(renderer, raw, clip, seconds)
                tail_frame(renderer, raw, tail, seconds)
                contact_sheet(renderer, raw, sheet, seconds)
                state["segments"].append({
                    "id": index, "input_sha256": digest(input_image), "raw_sha256": digest(raw),
                    "clip_sha256": digest(clip), "tail_sha256": digest(tail),
                    "wall_seconds": round(time.monotonic() - started, 2),
                    "review_status": "not_reviewed",
                })
                checkpoint_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            input_image = tail
            clips.append(clip)
        output = target_dir / "continuity-trial.mp4"
        duration = sum(s["duration_seconds"] for s in plan["segments"][:segments])
        renderer.assemble(clips, output, audio=None, duration=duration)
        # Deliberately no synthesized narration, cloud submission or automatic acceptance.
        report = {"purpose": "fictional_visual_continuity_trial", "duration_seconds": renderer.probe(output)["duration"], "segments": segments, "audio": "silent_visual_test", "looping": False, "review_status": "not_reviewed", "output": str(output)}
        (target_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return output
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="本机虚构连续影片验证，不领取或上传云端任务")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".worker-data/continuity-trial"))
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--segments", type=int, default=1, help="默认先做一段；复核后再续作全部")
    args = parser.parse_args()
    output = run_trial(args.plan, args.reference, args.output, base_url=args.comfy_url, renderer=MediaRenderer(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe), segments=args.segments)
    print(f"仅供视觉复核，未发布：{output}")


if __name__ == "__main__":
    main()
