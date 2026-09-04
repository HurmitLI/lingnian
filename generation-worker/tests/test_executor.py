from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lingnian_worker.executor import DocumentaryExecutor
from lingnian_worker.models import AuthorizedPackage, PackageError, StaticWorkflowError, WorkerTask


class FakeRenderer:
    def __init__(self) -> None:
        self.rendered: list[str] = []

    def image_clip(self, image: Path, target: Path, **kwargs) -> Path:
        self.rendered.append(target.name)
        target.write_bytes(f"image:{target.name}".encode())
        return target

    def video_clip(self, source: Path, target: Path, **kwargs) -> Path:
        self.rendered.append(target.name)
        target.write_bytes(f"video:{target.name}".encode())
        return target

    def assemble(self, clips: list[Path], target: Path, **kwargs) -> Path:
        target.write_bytes(b"final-video")
        return target

    def probe(self, path: Path) -> dict:
        return {"duration": 45.0, "width": 1280, "height": 720}

    def visual_fingerprint(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeComfy:
    def __init__(self) -> None:
        self.generated = 0

    def doctor(self) -> None:
        return None

    def generate_scene(self, *, output_path: Path, **kwargs) -> Path:
        self.generated += 1
        target = output_path.with_suffix(".mp4")
        target.write_bytes(f"generated-scene-{self.generated}".encode())
        return target


def make_package(tmp_path: Path) -> AuthorizedPackage:
    image = tmp_path / "source.jpg"
    image.write_bytes(b"image")
    spec = {
        "target_duration_seconds": 45,
        "synthetic_voice_allowed": False,
        "output": {"width": 1280, "height": 720, "fps": 24},
    }
    scenes = [
        {"scene": 1, "kind": "title_card", "duration_seconds": 4, "subtitle": "回忆", "camera_motion": "none", "source_story_id": "story-1"},
        {"scene": 2, "kind": "archival_photo", "duration_seconds": 18, "subtitle": "照片", "camera_motion": "slow_push", "source_story_id": "story-1"},
        {"scene": 3, "kind": "documentary_context", "duration_seconds": 18, "subtitle": "火车驶离站台", "camera_motion": "slow_pan_left", "source_story_id": "story-1", "visual_direction": "只表现火车与站台。"},
        {"scene": 4, "kind": "source_card", "duration_seconds": 5, "subtitle": "来自家庭档案", "camera_motion": "none", "source_story_id": "story-1"},
    ]
    return AuthorizedPackage(
        root=tmp_path,
        manifest={"generation_type": "scene_video"},
        plan={"format": "lingnian-documentary-storyboard", "version": 2, "production_spec": spec, "scenes": scenes},
        audio_path=None,
        image_path=image,
    )


def make_task() -> WorkerTask:
    return WorkerTask(
        id="task-1",
        generation_type="scene_video",
        lease_token="lease",
        package_url="/package",
        max_cost_cents=0,
        attempt_count=1,
        production_spec={},
        resume_checkpoint={},
    )


def test_executes_all_scenes_and_reports_exact_output(tmp_path, monkeypatch):
    monkeypatch.setattr("lingnian_worker.executor.make_card", lambda path, **kwargs: path.write_bytes(b"card") or path)
    renderer = FakeRenderer()
    comfy = FakeComfy()
    updates = []
    executor = DocumentaryExecutor(renderer=renderer, comfyui=comfy, work_dir=tmp_path / "worker")
    report = executor.execute(make_task(), make_package(tmp_path), lambda *args: updates.append(args))
    assert report.rendered_scene_count == 4
    assert report.duration_seconds == 45.0
    assert report.result_path.read_bytes() == b"final-video"
    assert report.generated_context_scene_count == 1
    assert report.generated_video_scene_count == 1
    assert report.unique_generated_visual_count == 1
    assert report.duplicate_visual_check_passed is True
    assert comfy.generated == 1
    assert updates[-1][2:] == (4, 4, "scene-04")


def test_reuses_verified_scene_checkpoints_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("lingnian_worker.executor.make_card", lambda path, **kwargs: path.write_bytes(b"card") or path)
    package = make_package(tmp_path)
    first_renderer = FakeRenderer()
    first_comfy = FakeComfy()
    DocumentaryExecutor(renderer=first_renderer, comfyui=first_comfy, work_dir=tmp_path / "worker").execute(
        make_task(), package, lambda *args: None
    )
    checkpoint = json.loads((tmp_path / "worker/jobs/task-1/checkpoint.json").read_text())
    assert len(checkpoint["clips"]) == 4
    assert checkpoint["version"] == 2
    assert checkpoint["scene_reports"]["3"]["source_type"] == "generated_video"

    second_renderer = FakeRenderer()
    second_comfy = FakeComfy()
    DocumentaryExecutor(renderer=second_renderer, comfyui=second_comfy, work_dir=tmp_path / "worker").execute(
        make_task(), package, lambda *args: None
    )
    assert second_renderer.rendered == []
    assert second_comfy.generated == 0


def test_retries_then_blocks_duplicate_context_visuals(tmp_path, monkeypatch):
    monkeypatch.setattr("lingnian_worker.executor.make_card", lambda path, **kwargs: path.write_bytes(b"card") or path)
    package = make_package(tmp_path)
    package.plan["scenes"] = [
        package.plan["scenes"][0],
        {
            "scene": 2,
            "kind": "documentary_context",
            "duration_seconds": 18,
            "subtitle": "蓝布包",
            "narration": "蓝布包",
            "camera_motion": "slow_push",
            "source_story_id": "story-1",
            "visual_direction": "蓝布包近景",
        },
        {
            "scene": 3,
            "kind": "documentary_context",
            "duration_seconds": 18,
            "subtitle": "火车站台",
            "narration": "火车站台",
            "camera_motion": "slow_pan_left",
            "source_story_id": "story-1",
            "visual_direction": "火车站台远景",
        },
        package.plan["scenes"][-1],
    ]

    class DuplicateComfy(FakeComfy):
        def generate_scene(self, *, output_path: Path, **kwargs) -> Path:
            self.generated += 1
            target = output_path.with_suffix(".mp4")
            target.write_bytes(b"same-generated-video")
            return target

    duplicate = DuplicateComfy()
    executor = DocumentaryExecutor(renderer=FakeRenderer(), comfyui=duplicate, work_dir=tmp_path / "worker")
    with pytest.raises(PackageError, match="相同画面"):
        executor.execute(make_task(), package, lambda *args: None)
    assert duplicate.generated == 3


def test_blocks_static_image_only_context_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr("lingnian_worker.executor.make_card", lambda path, **kwargs: path.write_bytes(b"card") or path)

    class StaticComfy(FakeComfy):
        def generate_scene(self, *, output_path: Path, **kwargs) -> Path:
            target = output_path.with_suffix(".png")
            target.write_bytes(b"static-image")
            return target

    executor = DocumentaryExecutor(renderer=FakeRenderer(), comfyui=StaticComfy(), work_dir=tmp_path / "worker")
    with pytest.raises(StaticWorkflowError, match="只输出静态图片"):
        executor.execute(make_task(), make_package(tmp_path), lambda *args: None)
