"""Strict local assembly of reviewed native clips, narration and burned captions.

Never generates, uploads or marks the final film visually accepted. Temporary
assembly folders are retained on failure for diagnosis; unrelated files untouched.
"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from PIL import Image

from .continuity import digest
from .film_contract import film_contract_digest, scene_for_shot
from .film_executor import ContinuousFilmExecutor, _hash, _read, _save, execution_binding, read_review, scene_reference_status
from .media import MediaRenderer, burn_subtitle, _run
from .models import PackageError, TemporaryWorkerError


def _duration_matches(value, expected, tolerance=0.1):
    return type(value) in (int, float) and math.isfinite(value) and abs(value - expected) <= tolerance


def reviewed_clips(plan: dict, job_dir: Path, reference: Path, *, base_url: str) -> list[Path]:
    binding = execution_binding(plan, base_url)
    state = _read(job_dir / "checkpoint.json")
    shots = state.get("shots")
    if state.get("binding") != binding or not isinstance(shots, list) or len(shots) != len(plan["segments"]):
        raise PackageError("整片镜头尚未齐全或方案不匹配。")
    source, clips, hashes = reference, [], set()
    for number, shot in enumerate(shots, 1):
        scene = scene_for_shot(plan, number)
        if scene and number == scene["start_shot"]:
            source, gate = scene_reference_status(scene, job_dir, binding)
            if gate:
                raise PackageError("存在缺失、未复核或被拒绝的场景参考，不能合成长片。")
        if not isinstance(shot, dict) or shot.get("id") != number or not isinstance(shot.get("attempts"), list) or not 1 <= len(shot["attempts"]) <= 3:
            raise PackageError("镜头候选记录不正确。")
        accepted = False
        for attempt, entry in enumerate(shot["attempts"], 1):
            folder = job_dir / f"shot-{number:02d}" / f"attempt-{attempt}"
            raw, clip, tail = (folder / name for name in ("raw.mp4", "clip.mp4", "tail.png"))
            ContinuousFilmExecutor._verify_entry(entry, source, raw, clip, tail)
            expected = _hash({"job": binding, "shot": number, "attempt": attempt,
                              **{k: entry[k] for k in ("input_sha256", "raw_sha256", "clip_sha256", "tail_sha256")}})
            if entry.get("review_binding") != expected:
                raise PackageError("旧镜头复核绑定无效。")
            decision = read_review(folder / "review.json", expected)
            if decision == "pending":
                raise PackageError("镜头还没有视觉复核，不能直接合成长片。")
            if decision == "accepted":
                if entry["raw_sha256"] in hashes:
                    raise PackageError("出现完全重复的原始视频，不能循环凑时长。")
                hashes.add(entry["raw_sha256"])
                source, accepted = tail, True
                clips.append(clip)
                break
        if not accepted:
            raise PackageError("存在未通过镜头，不能合成长片。")
    return clips


def burn_timed_captions(renderer: MediaRenderer, source: Path, target: Path, *, cues: list[dict], offset: float, seconds: int) -> Path:
    """Overlay text images only during their cue; never loop the input video."""
    local = [cue for cue in cues if cue["end_seconds"] > offset and cue["start_seconds"] < offset + seconds]
    inputs = [renderer.ffmpeg, "-y", "-i", str(source)]
    filters, previous = [], "0:v"
    for index, cue in enumerate(local, 1):
        overlay = target.with_name(f"{target.stem}-caption-{index}.png")
        Image.new("RGBA", (1280, 720), (0, 0, 0, 0)).save(overlay)
        burn_subtitle(overlay, overlay, subtitle=cue["text"])
        inputs.extend(["-loop", "1", "-i", str(overlay)])
        start, end = max(0, cue["start_seconds"] - offset), min(seconds, cue["end_seconds"] - offset)
        label = f"caption{index}"
        filters.append(f"[{previous}][{index}:v]overlay=0:0:enable='gte(t,{start:.3f})*lt(t,{end:.3f})':format=auto[{label}]")
        previous = label
    if not local:
        # Re-encode to the same settings as captioned clips without inventing text.
        filters = ["[0:v]format=yuv420p[plain]"]
        previous = "plain"
    command = inputs + ["-filter_complex", ";".join(filters), "-map", f"[{previous}]", "-an", "-t", str(seconds),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "24", str(target)]
    _run(command, description="时间轴字幕烧录")
    return target


def require_audible(renderer: MediaRenderer, path: Path) -> None:
    result = subprocess.run([renderer.ffmpeg, "-hide_banner", "-i", str(path), "-vn", "-af", "astats=metadata=0:reset=0", "-f", "null", "-"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    # volumedetect quantizes digital silence to about -91 dB for s16; astats
    # reports its actual zero RMS as -inf. The last value is the overall signal.
    levels = re.findall(r"RMS level dB:\s*(-?inf|[-+]?\d+(?:\.\d+)?)", result.stderr)
    if result.returncode != 0 or not levels or not math.isfinite(float(levels[-1])):
        raise PackageError("旁白音轨无法验证或全程静音，不能当作有声影片。")


def assemble_film(plan: dict, *, job_dir: Path, reference: Path, narration: Path, renderer: MediaRenderer,
                  base_url: str = "http://127.0.0.1:8188") -> Path:
    film_contract_digest(plan)
    binding = execution_binding(plan, base_url)
    for path, expected in ((reference, plan["reference"]["sha256"]), (narration, plan["narration"]["sha256"])):
        if not path.is_file() or digest(path) != expected:
            raise PackageError("合成素材与已确认的方案摘要不同。")
    if not job_dir.is_dir():
        raise PackageError("没有已生成的镜头目录。")
    lock = job_dir / "execution.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TemporaryWorkerError("整片正在执行，不能并发合成。") from exc
    try:
        os.close(fd)
        clips = reviewed_clips(plan, job_dir, reference, base_url=base_url)
        for clip, segment in zip(clips, plan["segments"]):
            meta = renderer.probe(clip)
            if not _duration_matches(meta["duration"], segment["duration_seconds"], 0.05) or (meta["width"], meta["height"]) != (1280, 720):
                raise PackageError("镜头实际长度或分辨率不符，不补帧、不循环。")
        audio = renderer.probe(narration)
        if not audio.get("has_audio") or not _duration_matches(audio.get("audio_duration"), plan["narration"]["duration_seconds"]):
            raise PackageError("真实旁白音轨或时长不符。")
        require_audible(renderer, narration)
        target, report_path = job_dir / "film.mp4", job_dir / "assembly-report.json"
        if target.exists():
            report = _read(report_path)
            if report.get("binding") != binding or report.get("output_sha256") != digest(target):
                raise PackageError("已有成片与合成记录不同，不覆盖。")
            return target
        staging = Path(tempfile.mkdtemp(prefix=".assembly-", dir=job_dir))
        captioned, offset = [], 0
        for index, (clip, segment) in enumerate(zip(clips, plan["segments"]), 1):
            destination = staging / f"captioned-{index:02d}.mp4"
            burn_timed_captions(renderer, clip, destination, cues=plan["narration_cues"], offset=offset, seconds=segment["duration_seconds"])
            captioned.append(destination)
            offset += segment["duration_seconds"]
        output = renderer.assemble(captioned, staging / "film.mp4", audio=narration, duration=plan["duration_seconds"])
        meta = renderer.probe(output)
        if (not _duration_matches(meta["duration"], plan["duration_seconds"], 0.15)
                or not _duration_matches(meta.get("audio_duration"), plan["duration_seconds"], 0.15)
                or not meta.get("has_audio") or (meta["width"], meta["height"]) != (1280, 720)):
            raise PackageError("最终成片音视频规格校验失败。")
        require_audible(renderer, output)
        _run([renderer.ffmpeg, "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"], description="完整音视频解码检查")
        # Exclusive link prevents overwriting an existing film even on a race.
        os.link(output, target)
        _save(report_path, {"binding": binding, "status": "awaiting_final_review", "duration_seconds": meta["duration"],
                            "output_sha256": digest(target), "has_audio": True, "captions": "burned_in",
                            "looping": False, "final_visual_accepted": False})
        return target
    finally:
        lock.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="合成已复核长片，不生成新镜头、不上传")
    for name in ("plan", "job-dir", "reference", "narration"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    args = parser.parse_args()
    output = assemble_film(_read(args.plan), job_dir=args.job_dir, reference=args.reference, narration=args.narration,
                           renderer=MediaRenderer(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe))
    print(f"已完成技术合成，待整片视听验收，未发布：{output}")


if __name__ == "__main__":
    main()
