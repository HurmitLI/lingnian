"""Cross-contract fixtures; no real ASR, identity or GPU acceptance."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.services.memory.short_scene_bundle import create_short_scene_bundle
from app.services.memory.short_scene_selection import prepare_selection_request
from test_short_scene_binding import source  # noqa: F401


@pytest.fixture
def bundle_args(source, tmp_path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (1280, 704), (90, 100, 110)).save(reference)
    inputs = {"metadata": source["metadata"], "raw_answers": source["raw_answers"], "audio_asset_id": source["audio_asset_id"],
              "raw_questions": {"turn": "那次为什么等车？"},
              "interview_context": {"subject_label": "外公", "narrator_label": "妈妈", "narrator_is_subject": False}}
    request = prepare_selection_request(**inputs, reference_mode="illustrative")
    scene = {"context_summary": "TEST_ONLY: 去无锡之前等车", "opening_state": "TEST_ONLY: 站台等车", "action": "自然站立",
             "action_kind": "standing_waiting", "render_bible": "One adult man at a station. Illustrative appearance.",
             "render_action": "He stands waiting and breathes naturally. Locked camera.", "source_quotes": ["我在站台等车。"],
             "illustrative_details": ["TEST_ONLY: 人物长相为示意"],
             "facts": [{"field": key, "status": "unknown", "value": None, "source_quotes": []} for key in ("character", "location", "era", "wardrobe", "prop")]}
    return {**inputs, "input_sha256": request["input_sha256"],
            "model_response": {"decision": "selected", "candidate_id": request["payload"]["candidates"][0]["id"], "reason": "测试单镜头", "scene": scene},
            "normalized_audio": source["normalized_audio"], "reference": reference, "reference_kind": "generated_reference",
            "source_use_authorized": True, "reference_use_authorized": True,
            "destination": tmp_path / "bundle.zip", "test_fixture_only": True}


def test_bundle_binds_exact_audio_scene_reference_and_question_context(bundle_args):
    result = create_short_scene_bundle(**bundle_args)
    assert not result["generation_ready"] and result["status"] == "awaiting_input_review"
    assert result["sha256"] == hashlib.sha256(bundle_args["destination"].read_bytes()).hexdigest()
    with zipfile.ZipFile(bundle_args["destination"]) as archive:
        assert set(archive.namelist()) == {"manifest.json", "plan.json", "recording.wav", "reference.png"}
        manifest = json.loads(archive.read("manifest.json"))
        for name, expected in manifest["files"].items():
            data = archive.read(name)
            assert len(data) == expected["size_bytes"] and hashlib.sha256(data).hexdigest() == expected["sha256"]
        assert archive.read("recording.wav") == bundle_args["normalized_audio"].read_bytes()
        plan = json.loads(archive.read("plan.json"))
        assert plan["test_fixture_only"] is True
        assert plan["recording"]["kind"] == "synthetic_pcm_fixture"
        assert plan["provenance"]["interview_context"]["narrator_label"] == "妈妈"
        assert plan["selection"] == {"first_sentence": 0, "last_sentence": 0}
        assert plan["timing"]["items"][0]["start_frame"] == 176000
        assert "后来我到了无锡" in plan["source_text"]
        assert "后来" not in plan["scene"]["render_action"]
        assert "input-review.json" not in archive.namelist()


@pytest.mark.parametrize("damage", ["consent", "photo_consent", "hash", "question", "identity", "reference_kind", "portrait", "candidate", "invented_fact", "unsuitable"])
def test_bad_material_or_stale_selection_never_creates_bundle(bundle_args, damage):
    args = bundle_args
    if damage == "consent": args["source_use_authorized"] = 1
    elif damage == "photo_consent": args["reference_use_authorized"] = False
    elif damage == "hash": args["input_sha256"] = "f" * 64
    elif damage == "question": args["raw_questions"]["turn"] = "全新的问题"
    elif damage == "identity": args["interview_context"]["subject_label"] = "另一位"
    elif damage == "reference_kind": args["reference_kind"] = "unlicensed_photo"
    elif damage == "portrait": Image.new("RGB", (768, 1024)).save(args["reference"])
    elif damage == "candidate": args["model_response"]["candidate_id"] = "f" * 64
    elif damage == "invented_fact": args["model_response"]["scene"]["facts"][2] = {"field": "era", "status": "known", "value": "1928", "source_quotes": ["1928"]}
    else: args["model_response"] = {"decision": "unsuitable", "reason": "不适合"}
    with pytest.raises(ValueError):
        create_short_scene_bundle(**args)
    assert not args["destination"].exists()


def test_existing_bundle_not_overwritten(bundle_args):
    create_short_scene_bundle(**bundle_args)
    before = bundle_args["destination"].read_bytes()
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        create_short_scene_bundle(**bundle_args)
    assert bundle_args["destination"].read_bytes() == before


def test_exported_plan_is_understood_by_existing_worker(bundle_args, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "generation-worker" / "src"))
    from lingnian_worker.short_scene import validate_plan
    from lingnian_worker.short_scene_bundle import receive_bundle
    exported = create_short_scene_bundle(**bundle_args)
    with zipfile.ZipFile(bundle_args["destination"]) as archive:
        plan = json.loads(archive.read("plan.json"))
    result = validate_plan(plan, recording=bundle_args["normalized_audio"], reference=bundle_args["reference"])
    assert result["duration_seconds"] == 9
    assert result["text"] == "我在站台等车。"
    received = receive_bundle(bundle_args["destination"], expected_sha256=exported["sha256"], output_dir=tmp_path / "received")
    assert received["excerpt"] == result
    assert received["approval_granted"] is False
    assert receive_bundle(bundle_args["destination"], expected_sha256=exported["sha256"], output_dir=tmp_path / "received")["written_files"] == []


def test_asset_change_during_packaging_leaves_no_export(bundle_args, monkeypatch):
    from app.services.memory import short_scene_bundle as module
    original = module.file_evidence
    def changing_evidence(path):
        evidence = original(path)
        if path == bundle_args["normalized_audio"]:
            Image.new("RGB", (1280, 704), (1, 2, 3)).save(bundle_args["reference"])
        return evidence
    monkeypatch.setattr(module, "file_evidence", changing_evidence)
    with pytest.raises(ValueError, match="ASSET_CHANGED_DURING_PACKAGING"):
        module.create_short_scene_bundle(**bundle_args)
    assert not bundle_args["destination"].exists()
    assert list(bundle_args["destination"].parent.glob(".short-scene-*.zip")) == []
