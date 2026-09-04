from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from .comfyui import ComfyUiClient
from .media import MediaRenderer, make_card
from .models import (
    AuthorizedPackage,
    ConfigurationError,
    PackageError,
    RenderReport,
    StaticWorkflowError,
    WorkerTask,
)


ProgressCallback = Callable[[int, str, int | None, int | None, str | None], None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class DocumentaryExecutor:
    def __init__(
        self,
        *,
        renderer: MediaRenderer,
        comfyui: ComfyUiClient,
        work_dir: Path,
    ) -> None:
        self.renderer = renderer
        self.comfyui = comfyui
        self.work_dir = work_dir

    def execute(
        self,
        task: WorkerTask,
        package: AuthorizedPackage,
        progress: ProgressCallback,
    ) -> RenderReport:
        if task.generation_type != "scene_video":
            raise ConfigurationError("当前 v2 执行器只启用了纪实故事影片。")
        if package.plan.get("format") != "lingnian-documentary-storyboard" or int(package.plan.get("version", 0)) != 2:
            raise PackageError("纪实影片分镜不是 v2 格式。")
        spec = dict(package.plan.get("production_spec") or task.production_spec)
        width = int((spec.get("output") or {}).get("width", 0))
        height = int((spec.get("output") or {}).get("height", 0))
        fps = int((spec.get("output") or {}).get("fps", 24))
        target_duration = int(spec.get("target_duration_seconds", 0))
        if (width, height) not in {(1280, 720), (720, 1280)} or fps != 24 or target_duration not in {45, 60, 90}:
            raise PackageError("影片规格不在允许范围内。")
        if spec.get("synthetic_voice_allowed") is not False:
            raise PackageError("影片规格没有明确关闭声音克隆。")
        scenes = list(package.plan.get("scenes") or [])
        if not 3 <= len(scenes) <= 10:
            raise PackageError("分镜数量不正确。")
        if sum(int(scene.get("duration_seconds", 0)) for scene in scenes) != target_duration:
            raise PackageError("分镜总时长与影片规格不一致。")

        job_dir = self.work_dir / "jobs" / task.id
        clip_dir = job_dir / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = job_dir / "checkpoint.json"
        completed, scene_reports = self._load_checkpoint(checkpoint_path, scenes)
        clips: list[Path] = []
        generated_visual_hashes: set[str] = set()
        total = len(scenes)

        for index, scene in enumerate(scenes, start=1):
            clip = clip_dir / f"scene-{index:02d}.mp4"
            expected = completed.get(str(index))
            saved_report = scene_reports.get(str(index), {})
            is_context_scene = str(scene.get("kind", "")) == "documentary_context"
            can_reuse_context = not is_context_scene or bool(saved_report.get("visual_sha256"))
            if expected and clip.is_file() and _sha256(clip) == expected and can_reuse_context:
                if saved_report.get("visual_sha256"):
                    generated_visual_hashes.add(saved_report["visual_sha256"])
                clips.append(clip)
                progress(
                    10 + round(75 * index / total),
                    f"已复用第 {index}/{total} 个镜头",
                    index,
                    total,
                    f"scene-{index:02d}",
                )
                continue
            scene_report = self._render_scene(
                scene,
                clip,
                package=package,
                width=width,
                height=height,
                fps=fps,
                generated_visual_hashes=generated_visual_hashes,
                keepalive=lambda: progress(
                    10 + round(75 * (index - 1) / total),
                    f"正在生成第 {index}/{total} 个镜头",
                    index - 1,
                    total,
                    f"scene-{index - 1:02d}" if index > 1 else None,
                ),
            )
            completed[str(index)] = _sha256(clip)
            scene_reports[str(index)] = scene_report
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "task_id": task.id,
                        "clips": completed,
                        "scene_reports": scene_reports,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            clips.append(clip)
            progress(
                10 + round(75 * index / total),
                f"已完成第 {index}/{total} 个镜头",
                index,
                total,
                f"scene-{index:02d}",
            )

        progress(90, "正在合并镜头、原声和字幕", total, total, f"scene-{total:02d}")
        result = job_dir / f"lingnian-{task.id}.mp4"
        self.renderer.assemble(clips, result, audio=package.audio_path, duration=target_duration)
        metadata = self.renderer.probe(result)
        if abs(metadata["duration"] - target_duration) > 0.75:
            raise PackageError("最终影片时长与授权规格不一致。")
        if (metadata["width"], metadata["height"]) != (width, height):
            raise PackageError("最终影片分辨率与授权规格不一致。")
        context_reports = [
            scene_reports.get(str(index), {})
            for index, scene in enumerate(scenes, start=1)
            if str(scene.get("kind", "")) == "documentary_context"
        ]
        unique_visuals = {
            report["visual_sha256"]
            for report in context_reports
            if report.get("visual_sha256")
        }
        return RenderReport(
            result_path=result,
            rendered_scene_count=total,
            duration_seconds=round(metadata["duration"], 3),
            width=width,
            height=height,
            generated_context_scene_count=len(context_reports),
            generated_video_scene_count=sum(
                report.get("source_type") == "generated_video"
                for report in context_reports
            ),
            unique_generated_visual_count=len(unique_visuals),
            duplicate_visual_check_passed=(
                bool(context_reports) and len(unique_visuals) == len(context_reports)
            ),
        )

    @staticmethod
    def _load_checkpoint(
        path: Path,
        scenes: list[dict],
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        if not path.is_file():
            return {}, {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, {}
        clips = value.get("clips") if isinstance(value, dict) else None
        if not isinstance(clips, dict):
            return {}, {}
        reports = value.get("scene_reports") if isinstance(value, dict) else None
        if not isinstance(reports, dict):
            reports = {}
        return (
            {
                key: str(digest)
                for key, digest in clips.items()
                if key.isdigit() and 1 <= int(key) <= len(scenes)
            },
            {
                key: {
                    item_key: str(item_value)
                    for item_key, item_value in report.items()
                    if item_key in {"source_type", "visual_sha256"}
                }
                for key, report in reports.items()
                if key.isdigit()
                and 1 <= int(key) <= len(scenes)
                and isinstance(report, dict)
            },
        )

    def _render_scene(
        self,
        scene: dict,
        clip: Path,
        *,
        package: AuthorizedPackage,
        width: int,
        height: int,
        fps: int,
        generated_visual_hashes: set[str],
        keepalive: Callable[[], None],
    ) -> dict[str, str]:
        kind = str(scene.get("kind", ""))
        subtitle = str(scene.get("subtitle", "")).strip()
        duration = int(scene.get("duration_seconds", 0))
        motion = str(scene.get("camera_motion", "none"))
        if duration <= 0 or not subtitle:
            raise PackageError("分镜缺少时长或字幕。")
        if kind in {"title_card", "source_card"}:
            card = make_card(
                clip.with_suffix(".card.png"),
                text=subtitle,
                width=width,
                height=height,
                source_card=kind == "source_card",
            )
            self.renderer.image_clip(
                card,
                clip,
                subtitle="",
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                motion="none",
            )
            return {"source_type": kind}
        if kind == "archival_photo":
            if package.image_path is None:
                raise PackageError("档案照片镜头缺少授权照片。")
            self.renderer.image_clip(
                package.image_path,
                clip,
                subtitle=subtitle,
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                motion=motion,
            )
            return {"source_type": "archival_photo"}
        if kind != "documentary_context":
            raise PackageError("分镜包含未知镜头类型。")
        self.comfyui.doctor()
        generated: Path | None = None
        for seed_offset in range(2):
            candidate = self.comfyui.generate_scene(
                scene=scene,
                output_path=clip.with_suffix(f".generated-{seed_offset}"),
                width=width,
                height=height,
                fps=fps,
                source_image=None,
                seed_offset=seed_offset,
                on_wait=keepalive,
            )
            if candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                raise StaticWorkflowError(
                    "纪实空镜工作流只输出静态图片；请改用能输出 MP4、WebM 或 GIF 的动态视频工作流。"
                )
            visual_hash = self.renderer.visual_fingerprint(candidate)
            if visual_hash not in generated_visual_hashes:
                generated_visual_hashes.add(visual_hash)
                generated = candidate
                break
        if generated is None:
            raise PackageError("多个纪实空镜生成了相同画面，已阻止低质量重复成片。")
        self.renderer.video_clip(
            generated,
            clip,
            subtitle=subtitle,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
        )
        return {"source_type": "generated_video", "visual_sha256": visual_hash}
