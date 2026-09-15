"""Protocol tests use synthetic fixtures, never visual acceptance evidence."""
import json
import wave
from pathlib import Path

import pytest
from PIL import Image

from lingnian_worker import short_scene as module
from lingnian_worker.continuity import digest
from lingnian_worker.models import PackageError, TemporaryWorkerError


@pytest.fixture
def inputs(tmp_path):
    recording, reference = tmp_path / "recording.wav", tmp_path / "reference.png"
    with wave.open(str(recording), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 960000)
    Image.new("RGB", (1280, 704)).save(reference)
    sha = digest(recording)
    plan = {
        "format": "lingnian-short-scene", "version": 1, "duration_seconds": 10,
        "source_text": "那天发生了什么？我提着蓝布包，在车站等车。后来到达无锡。",
        "recording": {"kind": "authorized_recording", "usage_authorized": True, "sha256": sha, "frames": 960000},
        "reference": {"kind": "generated_reference", "usage_authorized": True, "sha256": digest(reference),
                      "identity_claim": "illustrative_not_verified_likeness"},
        "timing": {"recording_sha256": sha, "basis": "actual_asr_sentence_timestamps", "items": [
            {"role": "question", "start_frame": 0, "end_frame": 160000, "text": "那天发生了什么？"},
            {"role": "answer", "start_frame": 176000, "end_frame": 320000, "text": "我提着蓝布包，在车站等车。"},
            {"role": "answer", "start_frame": 400000, "end_frame": 600000, "text": "后来到达无锡。"},
        ]},
        "selection": {"first_sentence": 1, "last_sentence": 1},
        "scene": {"context_summary": "TEST story context, not evidence", "character": "woman", "wardrobe": "same blouse",
                  "location": "station, departure city unknown", "era": "unknown", "opening_state": "standing, holding bag",
                  "action": "slight natural head turn", "render_bible": "One adult woman at the same station, same blue bag.",
                  "render_action": "She turns her head slightly, holding the same bag. Locked camera.",
                  "source_quotes": ["我提着蓝布包，在车站等车。"], "unknowns": ["departure city"],
                  "shot_count": 1, "subject_count": 1, "camera": "locked", "style": "consistent_color_live_action"},
    }
    return dict(plan=plan, recording=recording, reference=reference)


def test_whole_sentence_audio_cut_and_one_native_graph(inputs, tmp_path):
    excerpt = module.validate_plan(**inputs)
    assert excerpt["start_frame"] == 176000 and excerpt["duration_seconds"] == 9
    target = tmp_path / "excerpt.wav"
    module.extract_recording(inputs["recording"], target, excerpt)
    with wave.open(str(target)) as audio:
        assert audio.getnframes() == 144000
        assert audio.readframes(144000) == b"\x01\x00" * 144000
    graph = module.make_graph(inputs["plan"])
    assert graph["7"]["inputs"]["length"] == 241
    assert sum(n["class_type"] == "KSampler" for n in graph.values()) == 1
    assert "后来到达无锡" not in graph["5"]["inputs"]["text"]


def test_synthetic_pcm_requires_explicit_fixture_flag(inputs):
    inputs["plan"]["recording"]["kind"] = "synthetic_pcm_fixture"
    with pytest.raises(PackageError):
        module.validate_plan(**inputs)
    inputs["plan"]["test_fixture_only"] = True
    assert module.validate_plan(**inputs)["duration_seconds"] == 9


@pytest.mark.parametrize("damage", ["authorization", "source_hash", "invented_quote", "question", "too_long", "half_sentence", "multi_scene", "missing_timing", "old_timing", "portrait_stretch", "boolean_time"])
def test_fail_closed(inputs, damage):
    plan = inputs["plan"]
    if damage == "authorization":
        plan["recording"]["usage_authorized"] = False
    elif damage == "source_hash":
        plan["recording"]["sha256"] = "b" * 64
    elif damage == "invented_quote":
        plan["scene"]["source_quotes"] = ["这是蒸汽火车。"]
    elif damage == "question":
        plan["selection"] = {"first_sentence": 0, "last_sentence": 0}
    elif damage == "too_long":
        plan["selection"]["last_sentence"] = 2
    elif damage == "half_sentence":
        plan["timing"]["items"][1]["text"] = "我提着蓝布包"
    elif damage == "multi_scene":
        plan["scene"]["shot_count"] = 2
    elif damage == "missing_timing":
        plan["timing"]["basis"] = "estimated_by_characters"
    elif damage == "old_timing":
        plan["timing"]["recording_sha256"] = "b" * 64
    elif damage == "boolean_time":
        plan["timing"]["items"][0]["start_frame"] = False
    else:
        Image.new("RGB", (768, 1024)).save(inputs["reference"])
        plan["reference"]["sha256"] = digest(inputs["reference"])
    with pytest.raises(PackageError):
        module.validate_plan(**inputs)


@pytest.fixture
def runtime(inputs, tmp_path, monkeypatch):
    class Client:
        base_url = "http://127.0.0.1:8188"
        calls = 0
        uploads = 0

        def upload_image(self, image):
            self.uploads += 1
            return f"uploaded-{self.uploads}.png"

        def run_workflow(self, graph, *, output_path, journal_path):
            self.calls += 1
            output_path.write_bytes(b"MOCK VIDEO")
            return output_path

    class Renderer:
        ffmpeg = "MOCK"
        duration = 10.041667

        def probe(self, path):
            return {"duration": self.duration if path.name == "raw.mp4" else 10,
                    "width": 1280, "height": 704, "has_audio": path.name == "candidate.mp4"}

    monkeypatch.setattr(module, "normalize_segment", lambda renderer, raw, target, seconds: target.write_bytes(b"MOCK VISUAL"))
    monkeypatch.setattr(module, "checked_run", lambda command: Path(command[-1]).write_bytes(b"MOCK CANDIDATE"))
    return {**inputs, "work_dir": tmp_path / "jobs", "client": Client(), "renderer": Renderer()}


def review(result, filename, decision="accepted", **changes):
    value = {"binding": result["review_binding"], "decision": decision, "reviewer": "human_visual",
             "notes": "TEST FIXTURE ONLY, not real viewing", "checks": {key: True for key in result["required_checks"]}, **changes}
    (Path(result["job_dir"]) / filename).write_text(json.dumps(value))


def test_gates_then_reuses_candidate_without_gpu(runtime):
    first = module.execute(**runtime)
    assert first["status"] == "awaiting_input_review"
    assert runtime["client"].calls == 0
    review(first, "input-review.json")
    second = module.execute(**runtime)
    assert second["status"] == "awaiting_full_playback_review"
    assert second["final_visual_accepted"] is False
    assert runtime["client"].calls == 1
    assert module.execute(**runtime)["status"] == "awaiting_full_playback_review"
    assert runtime["client"].calls == 1
    review(second, "output-review.json")
    assert module.execute(**runtime)["status"] == "accepted"
    assert runtime["client"].calls == 1


def test_rejected_output_is_not_regenerated_or_accepted(runtime):
    first = module.execute(**runtime)
    review(first, "input-review.json")
    second = module.execute(**runtime)
    review(second, "output-review.json", "rejected")
    assert module.execute(**runtime)["status"] == "rejected"
    assert runtime["client"].calls == 1


def test_short_raw_cannot_be_stretched(runtime):
    first = module.execute(**runtime)
    review(first, "input-review.json")
    runtime["renderer"].duration = 5
    with pytest.raises(PackageError, match="禁止延展"):
        module.execute(**runtime)


def test_execution_lock_prevents_duplicate(runtime):
    first = module.execute(**runtime)
    (Path(first["job_dir"]) / "execution.lock").write_text("old pid")
    with pytest.raises(TemporaryWorkerError):
        module.execute(**runtime)
    assert runtime["client"].calls == 0


def test_resume_keeps_actual_uploaded_name(runtime, monkeypatch):
    first = module.execute(**runtime)
    review(first, "input-review.json")
    original = runtime["client"].run_workflow
    monkeypatch.setattr(runtime["client"], "run_workflow", lambda *a, **k: (_ for _ in ()).throw(TemporaryWorkerError("MOCK network interruption")))
    with pytest.raises(TemporaryWorkerError):
        module.execute(**runtime)
    monkeypatch.setattr(runtime["client"], "run_workflow", original)
    module.execute(**runtime)
    assert runtime["client"].uploads == 1


def test_candidate_tamper_does_not_regenerate(runtime):
    first = module.execute(**runtime)
    review(first, "input-review.json")
    second = module.execute(**runtime)
    Path(second["candidate"]).write_bytes(b"altered")
    with pytest.raises(PackageError, match="候选文件已变更"):
        module.execute(**runtime)
    assert runtime["client"].calls == 1


def test_existing_synthetic_fixture_cannot_pass_as_real_interview(runtime):
    runtime["plan"]["recording"]["kind"] = "licensed_synthetic_voice"
    with pytest.raises(PackageError, match="隔离测试"):
        module.execute(**runtime)
    runtime["plan"]["test_fixture_only"] = True
    first = module.execute(**runtime)
    review(first, "input-review.json")
    second = module.execute(**runtime)
    review(second, "output-review.json")
    final = module.execute(**runtime)
    assert final["status"] == "fixture_accepted"
    assert final["final_visual_accepted"] is False
