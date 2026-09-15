"""Synthetic reference ZIPs only; validation must never grant GPU approval."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

from PIL import Image
import pytest

from lingnian_worker.models import PackageError
from lingnian_worker.short_scene import _hash
from lingnian_worker.short_scene_reference_bundle import receive_reference_bundle
from test_short_scene_reference import brief, version_two  # noqa: F401


def sha(data): return hashlib.sha256(data).hexdigest()


def members(envelope, mode="illustrative"):
    envelope = version_two(deepcopy(envelope))
    envelope.update(status="awaiting_reference_generation_consent", generation_ready=False)
    result = {}
    if mode != "illustrative":
        image = io.BytesIO(); Image.new("RGB", (512,704), "gray").save(image, "PNG" if mode == "png" else "JPEG")
        result["source.png" if mode == "png" else "source.jpg"] = image.getvalue()
        envelope["brief"].update(reference_mode="user_photo", identity_claim="photo_reference_not_historical_footage",
            source_photo={"sha256": sha(image.getvalue()), "usage_authorized": True,
                          "identity_claim": "photo_reference_not_historical_footage"})
    envelope["brief_sha256"] = _hash(envelope["brief"])
    result["brief.json"] = json.dumps(envelope, ensure_ascii=False).encode()
    result["manifest.json"] = json.dumps({"format": "lingnian-reference-bundle", "version": 1,
        "brief_sha256": envelope["brief_sha256"], "files": {
            name: {"sha256":sha(data), "size_bytes":len(data)} for name, data in result.items()}}).encode()
    return result, envelope["brief_sha256"]


def pack(tmp_path, files, *, compression=zipfile.ZIP_STORED, duplicate=False, link=False):
    path = tmp_path / "reference.zip"
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, data in files.items():
            if link and name == "brief.json":
                info = zipfile.ZipInfo(name); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
            else: archive.writestr(name, data)
        if duplicate: archive.writestr("brief.json", files["brief.json"])
    return path, sha(path.read_bytes())


def receive(path, digest, brief_hash, output):
    return receive_reference_bundle(path, expected_sha256=digest, expected_brief_sha256=brief_hash, output_dir=output)


@pytest.mark.parametrize("mode", ["illustrative", "png", "jpg"])
def test_accept_exact_reference_package_once_without_generation(brief, tmp_path, mode):
    files, bound = members(brief, mode); path, digest = pack(tmp_path, files)
    result = receive(path, digest, bound, tmp_path / "received")
    assert result["status"] == "reference_input_received"
    assert result["generation_authorized"] is result["comfyui_called"] is result["visual_accepted"] is False
    assert (result["photo_path"] is None) == (mode == "illustrative")
    for name, data in files.items(): assert (tmp_path / "received" / name).read_bytes() == data
    assert receive(path, digest, bound, tmp_path / "received")["written_files"] == []
    assert not list(tmp_path.glob(".reference-receive-*"))
    assert not list(tmp_path.glob("*.reference-receive.lock"))


@pytest.mark.parametrize("damage", ["zip_sha", "brief_sha", "extra_audio", "path", "duplicate", "link",
                                   "compressed", "member_sha", "member_size", "duplicate_json", "fake_approval",
                                   "hidden_photo", "missing_photo", "unknown_version"])
def test_reject_untrusted_bundle_before_publication(brief, tmp_path, damage):
    files, bound = members(brief, "png" if damage == "missing_photo" else "illustrative")
    if damage == "extra_audio": files["recording.wav"] = b"not permitted"
    elif damage == "path": files["../outside"] = b"not permitted"
    elif damage in {"member_sha", "member_size"}:
        value=json.loads(files["manifest.json"])
        value["files"]["brief.json"]["sha256" if damage == "member_sha" else "size_bytes"] = "f"*64 if damage == "member_sha" else True
        files["manifest.json"]=json.dumps(value).encode()
    elif damage == "duplicate_json": files["manifest.json"] = b'{"files":{},"files":{}}'
    elif damage == "fake_approval":
        value=json.loads(files["brief.json"]); value["generation_ready"]=True
        files["brief.json"]=json.dumps(value).encode()
        manifest=json.loads(files["manifest.json"]); manifest["files"]["brief.json"]={"sha256":sha(files["brief.json"]),"size_bytes":len(files["brief.json"])}
        files["manifest.json"]=json.dumps(manifest).encode()
    elif damage == "hidden_photo":
        image=io.BytesIO(); Image.new("RGB", (512,512)).save(image,"PNG"); files["source.png"]=image.getvalue()
        manifest=json.loads(files["manifest.json"]); manifest["files"]["source.png"]={"sha256":sha(image.getvalue()),"size_bytes":len(image.getvalue())}
        files["manifest.json"]=json.dumps(manifest).encode()
    elif damage == "missing_photo":
        del files["source.png"]
        manifest=json.loads(files["manifest.json"]); del manifest["files"]["source.png"]
        files["manifest.json"]=json.dumps(manifest).encode()
    elif damage == "unknown_version":
        manifest=json.loads(files["manifest.json"]); manifest["version"]=2; files["manifest.json"]=json.dumps(manifest).encode()
    path,digest=pack(tmp_path, files, compression=zipfile.ZIP_DEFLATED if damage=="compressed" else zipfile.ZIP_STORED,
                     duplicate=damage=="duplicate", link=damage=="link")
    with pytest.raises(PackageError):
        receive(path, "f"*64 if damage=="zip_sha" else digest, "e"*64 if damage=="brief_sha" else bound, tmp_path/"received")
    assert not (tmp_path/"received").exists()
    assert not (tmp_path/"outside").exists()
    assert not list(tmp_path.glob(".reference-receive-*"))


def test_existing_different_evidence_is_preserved(brief, tmp_path):
    files,bound=members(brief); path,digest=pack(tmp_path,files); output=tmp_path/"received"
    receive(path,digest,bound,output)
    (output/"brief.json").write_bytes(b"different-private-evidence")
    with pytest.raises(PackageError): receive(path,digest,bound,output)
    assert (output/"brief.json").read_bytes() == b"different-private-evidence"


def test_stale_lock_is_not_deleted_or_bypassed(brief, tmp_path):
    files,bound=members(brief); path,digest=pack(tmp_path,files)
    lock=tmp_path/"received.reference-receive.lock"; lock.write_text("existing")
    with pytest.raises(PackageError): receive(path,digest,bound,tmp_path/"received")
    assert lock.read_text() == "existing"
    assert not (tmp_path/"received").exists()


def test_symlink_output_cannot_escape_private_directory(brief,tmp_path):
    files,bound=members(brief); path,digest=pack(tmp_path,files)
    other=tmp_path/"other"; other.mkdir()
    output=tmp_path/"received"; output.symlink_to(other,target_is_directory=True)
    with pytest.raises(PackageError): receive(path,digest,bound,output)
    assert list(other.iterdir()) == []
