import hashlib
import json
import stat
import struct
import wave
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from lingnian_worker.models import PackageError
from lingnian_worker.short_scene_bundle import CAPS, receive_bundle


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_files(tmp_path: Path) -> dict[str, bytes]:
    wav = tmp_path / "source.wav"
    with wave.open(str(wav), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 160000)
    png = tmp_path / "source.png"
    Image.new("RGB", (1280, 704), "gray").save(png)
    recording, reference = wav.read_bytes(), png.read_bytes()
    plan = {
        "format": "lingnian-short-scene", "version": 1, "duration_seconds": 10, "test_fixture_only": True,
        "source_text": "这是一个完整的合成测试句。", "provenance": {"exporter": "offline-test"},
        "recording": {"sha256": sha(recording), "frames": 160000, "kind": "synthetic_pcm_fixture", "usage_authorized": True},
        "reference": {"sha256": sha(reference), "kind": "generated_reference", "usage_authorized": True,
                      "identity_claim": "illustrative_not_verified_likeness"},
        "timing": {"recording_sha256": sha(recording), "basis": "actual_asr_sentence_timestamps", "items": [
            {"role": "answer", "start_frame": 0, "end_frame": 144000, "text": "这是一个完整的合成测试句。"}]},
        "selection": {"first_sentence": 0, "last_sentence": 0},
        "scene": {"context_summary": "synthetic offline test", "character": "one fictional woman", "wardrobe": "plain blouse",
                  "location": "generic room", "era": "unspecified", "opening_state": "woman standing",
                  "action": "small head turn", "render_bible": "One fictional woman in a generic room.",
                  "render_action": "She makes one small head turn with a locked camera.",
                  "source_quotes": ["这是一个完整的合成测试句。"], "unknowns": ["identity"],
                  "shot_count": 1, "subject_count": 1, "camera": "locked", "style": "consistent_color_live_action"},
    }
    plan_bytes = json.dumps(plan, ensure_ascii=False).encode()
    manifest = {"format": "lingnian-short-scene-bundle", "version": 1, "files": {
        "plan.json": {"sha256": sha(plan_bytes), "size_bytes": len(plan_bytes)},
        "recording.wav": {"sha256": sha(recording), "size_bytes": len(recording)},
        "reference.png": {"sha256": sha(reference), "size_bytes": len(reference)},
    }}
    return {"manifest.json": json.dumps(manifest, separators=(",", ":")).encode(),
            "plan.json": plan_bytes, "recording.wav": recording, "reference.png": reference}


def bundle(path: Path, files: dict[str, bytes], names=None) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names or files:
            archive.writestr(name, files[name] if name in files else files["plan.json"])
    return sha(path.read_bytes())


def test_valid_package_and_optional_provenance(tmp_path):
    files = fixture_files(tmp_path); z = tmp_path / "valid.zip"; digest = bundle(z, files)
    result = receive_bundle(z, expected_sha256=digest, output_dir=tmp_path / "out")
    assert result["status"] == "accepted_offline_preparation"
    assert result["test_fixture_only"] is True and result["recording_kind"] == "synthetic_pcm_fixture"
    assert result["comfyui_called"] is False and result["approval_granted"] is False


def test_bad_full_zip_hash(tmp_path):
    files = fixture_files(tmp_path); z = tmp_path / "bad.zip"; bundle(z, files)
    with pytest.raises(PackageError, match="ZIP 的 SHA-256"):
        receive_bundle(z, expected_sha256="0" * 64, output_dir=tmp_path / "out")


def test_bad_member_hash(tmp_path):
    files = fixture_files(tmp_path); manifest = json.loads(files["manifest.json"])
    manifest["files"]["plan.json"]["sha256"] = "0" * 64
    files["manifest.json"] = json.dumps(manifest).encode(); z = tmp_path / "bad-member.zip"
    digest = bundle(z, files)
    with pytest.raises(PackageError, match="实际 SHA-256"):
        receive_bundle(z, expected_sha256=digest, output_dir=tmp_path / "out")


def test_path_and_duplicate_rejected(tmp_path):
    files = fixture_files(tmp_path)
    for names, message in [(["manifest.json", "plan.json", "recording.wav", "../reference.png"], "四个扁平"),
                           (["manifest.json", "plan.json", "recording.wav", "reference.png", "plan.json"], "重复")]:
        z = tmp_path / ("x" + str(len(names)) + ".zip"); digest = bundle(z, files, names)
        with pytest.raises(PackageError, match=message):
            receive_bundle(z, expected_sha256=digest, output_dir=tmp_path / ("out" + str(len(names))))


def test_link_and_encrypted_entry_rejected(tmp_path):
    files = fixture_files(tmp_path)
    link_zip = tmp_path / "link.zip"
    with zipfile.ZipFile(link_zip, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            if name == "reference.png":
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, data)
    with pytest.raises(PackageError, match="链接"):
        receive_bundle(link_zip, expected_sha256=sha(link_zip.read_bytes()), output_dir=tmp_path / "link-out")

    encrypted_zip = tmp_path / "encrypted-flag.zip"
    bundle(encrypted_zip, files)
    payload = bytearray(encrypted_zip.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        offset = 0
        while (found := payload.find(signature, offset)) >= 0:
            flags = struct.unpack_from("<H", payload, found + flag_offset)[0]
            struct.pack_into("<H", payload, found + flag_offset, flags | 1)
            offset = found + 4
    encrypted_zip.write_bytes(payload)
    with pytest.raises(PackageError, match="加密"):
        receive_bundle(encrypted_zip, expected_sha256=sha(payload), output_dir=tmp_path / "encrypted-out")


def test_oversize_actual_stream_rejected(tmp_path, monkeypatch):
    files = fixture_files(tmp_path); z = tmp_path / "large.zip"; digest = bundle(z, files)
    monkeypatch.setitem(CAPS, "recording.wav", 32)
    with pytest.raises(PackageError, match="大小|限制"):
        receive_bundle(z, expected_sha256=digest, output_dir=tmp_path / "out")


def test_truncated_zip_rejected(tmp_path):
    files = fixture_files(tmp_path); z = tmp_path / "truncated.zip"; bundle(z, files)
    z.write_bytes(z.read_bytes()[:-22]); digest = sha(z.read_bytes())
    with pytest.raises(PackageError, match="完整 ZIP"):
        receive_bundle(z, expected_sha256=digest, output_dir=tmp_path / "out")


def test_replay_is_idempotent_and_different_existing_file_is_preserved(tmp_path):
    files = fixture_files(tmp_path); z = tmp_path / "valid.zip"; digest = bundle(z, files); out = tmp_path / "out"
    first = receive_bundle(z, expected_sha256=digest, output_dir=out)
    before = {p.name: (sha(p.read_bytes()), p.stat().st_mtime_ns) for p in out.iterdir()}
    second = receive_bundle(z, expected_sha256=digest, output_dir=out)
    after = {p.name: (sha(p.read_bytes()), p.stat().st_mtime_ns) for p in out.iterdir()}
    assert before == after and second["written_files"] == []
    (out / "plan.json").write_bytes(b"different")
    with pytest.raises(PackageError, match="拒绝覆盖"):
        receive_bundle(z, expected_sha256=digest, output_dir=out)
    assert (out / "plan.json").read_bytes() == b"different"


def test_existing_conflict_does_not_publish_other_members(tmp_path):
    files = fixture_files(tmp_path); z = tmp_path / "valid.zip"; digest = bundle(z, files)
    out = tmp_path / "out"; out.mkdir()
    (out / "reference.png").write_bytes(b"existing")
    with pytest.raises(PackageError, match="拒绝覆盖"):
        receive_bundle(z, expected_sha256=digest, output_dir=out)
    assert sorted(p.name for p in out.iterdir()) == ["reference.png"]


@pytest.mark.parametrize("directory", [True, False])
def test_output_links_rejected(tmp_path, directory):
    files = fixture_files(tmp_path); z = tmp_path / "valid.zip"; digest = bundle(z, files)
    out = tmp_path / "out"
    if directory:
        real = tmp_path / "real"; real.mkdir(); out.symlink_to(real, target_is_directory=True)
    else:
        out.mkdir(); (out / "plan.json").symlink_to(tmp_path / "missing")
    with pytest.raises(PackageError, match="链接"):
        receive_bundle(z, expected_sha256=digest, output_dir=out)
