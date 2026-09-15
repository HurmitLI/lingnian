"""Assemble only reviewed, native-duration local shots; never publishes media."""
import argparse
import json
import math
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg

from local_story_trial import read, save, sha, validate, require_review


def checked_sources(plan, selection):
    if [x["id"] for x in selection] != [s["id"] for s in plan["shots"]]:
        raise ValueError("Selection must cover every story shot exactly once, in order")
    sources, seen = [], set()
    for shot, selected in zip(plan["shots"], selection):
        root = Path(selected["root"]).resolve()
        local_plan = read(root / "plan.json")
        validate(local_plan)
        original = local_plan["shots"][shot["id"] - 1]
        for key in ("id", "seconds", "quote", "expected_action"):
            if original[key] != shot[key]:
                raise ValueError("Selected shot belongs to a different story beat")
        if local_plan["source_text"] != plan["source_text"]:
            raise ValueError("Selected shot belongs to a different narration")
        name = f"shot-{shot['id']:02d}"
        reference, video = root / (name + "-ref.png"), root / (name + ".mp4")
        require_review(root / (name + "-ref.review.json"), reference, original, "reference")
        require_review(root / (name + ".review.json"), video, original, "motion")
        digest = sha(video)
        if digest in seen:
            raise ValueError("Repeated raw video cannot count twice toward coverage")
        seen.add(digest)
        reader = imageio_ffmpeg.read_frames(str(video))
        try:
            meta = next(reader)
        finally:
            reader.close()
        frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(video))
        start = selected.get('start_seconds', 0)
        if (not isinstance(start, (int, float)) or isinstance(start, bool)
                or not math.isfinite(start) or start < 0
                or abs(start * 24 - round(start * 24)) > .001):
            raise ValueError('Shot trim must start at a nonnegative native frame')
        if (abs(meta["fps"] - 24) > .01 or meta["size"] not in [(1280, 704), (1280, 720)]
                or frames < round((start + shot["seconds"]) * 24)
                or seconds + .01 < start + shot["seconds"]):
            raise ValueError(f"Shot {shot['id']} lacks the required native frames")
        sources.append((video, shot, digest, frames, start))
    return sources


def ass_time(ms):
    cs = round(ms / 10)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def captions(path, cues):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Heiti SC,36,&H00FFFFFF,&H000000FF,&H00202020,&H88000000,0,0,0,0,100,100,0,0,1,2,0,2,70,70,32,1
Style: Label,Heiti SC,20,&H00FFFFFF,&H000000FF,&H00202020,&H88000000,0,0,0,0,100,100,0,0,1,1,0,7,25,25,22,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:01:00.00,Label,,0,0,0,,虚构演绎 · AI配音
"""
    previous = 0
    for cue in cues:
        start, end = cue["start_ms"], cue["end_ms"]
        if not (previous <= start < end <= 60000):
            raise ValueError("Caption timing overlaps or exceeds film")
        previous = end
        text = cue["text"].replace("{", "").replace("}", "").replace("\\", "").replace("\n", " ")
        # libass builds without Unicode line breaking can clip unspaced CJK.
        # Insert explicit balanced lines inside the 1140px subtitle safe area.
        line_count = math.ceil(len(text) / 28)
        width = math.ceil(len(text) / max(1, line_count))
        text = r"\N".join(text[i:i + width] for i in range(0, len(text), width)) if text else ""
        header += f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}\n"
    path.write_text(header, encoding="utf-8")


def assemble(plan_path, selection_path, audio, cues_path, output):
    plan = read(plan_path)
    validate(plan)
    with wave.open(str(audio), 'rb') as source:
        audio_seconds = source.getnframes() / source.getframerate()
    if abs(audio_seconds - 60) > .05:
        raise ValueError('The original WAV must cover 60 seconds without audio padding')
    sources = checked_sources(plan, read(selection_path))
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Existing film retained; choose another output name")
    staging = output.parent / (output.stem + "-assembly")
    staging.mkdir(parents=True, exist_ok=False)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    clips = []
    for video, shot, digest, frames, start in sources:
        clip = staging / f"{shot['id']:02d}.mp4"
        subprocess.run([ff, "-v", "error", "-i", str(video), "-ss", str(start), "-an", "-t", str(shot["seconds"]),
                        "-vf", "pad=1280:720:0:(oh-ih)/2:black", "-c:v", "libx264", "-crf", "18",
                        "-preset", "medium", "-pix_fmt", "yuv420p", str(clip)], check=True)
        clips.append(clip)
    (staging / "clips.txt").write_text("\n".join(f"file '{p.name}'" for p in clips), encoding="utf-8")
    captions(staging / "captions.ass", read(cues_path))
    subprocess.run([ff, "-v", "error", "-f", "concat", "-safe", "0", "-i", "clips.txt",
                    "-i", str(audio.resolve()), "-map", "0:v:0", "-map", "1:a:0", "-vf", "ass=captions.ass",
                    "-t", "60", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac",
                    "-b:a", "192k", "-movflags", "+faststart", str(output)], cwd=staging, check=True)
    frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(output))
    if frames != 1440 or abs(seconds - 60) > .05:
        raise ValueError("Final film does not contain exactly 60 seconds at native 24 fps")
    subprocess.run([ff, "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"], check=True)
    save(output.with_suffix(".report.json"), {
        "status": "awaiting_full_film_review", "local_only": True, "final_accepted": False,
        "native_frames": frames, "seconds": seconds, "output_sha256": sha(output),
        "audio_source_sha256": sha(audio), "plan_sha256": sha(plan_path),
        "audio_source_seconds": audio_seconds,
        "looping": False, "slow_motion": False, "static_padding": False,
        "shots": [{"id": s[1]["id"], "seconds": s[1]["seconds"], "raw_sha256": s[2],
                   "source_native_frames": s[3], "start_seconds": s[4], "path": str(s[0])} for s in sources],
    })
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("plan", "selection", "audio", "captions", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    assemble(args.plan, args.selection, args.audio, args.captions, args.output)
