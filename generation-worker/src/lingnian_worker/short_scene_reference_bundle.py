"""Verify and stage a reference-only package. No network, GPU or review approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from .models import PackageError
from .short_scene_bundle import (_file_hash, _json_object, _regular, _require, _stream_hash,
                                 _write_if_absent_or_same, HEX64)
from .short_scene_reference import validate_brief

CAPS = {"manifest.json": 16 * 1024, "brief.json": 512 * 1024,
        "source.png": 32 * 1024**2, "source.jpg": 32 * 1024**2}
TOTAL_CAP = 34 * 1024**2


def receive_reference_bundle(bundle: Path, *, expected_sha256: str,
                             expected_brief_sha256: str, output_dir: Path) -> dict:
    for value in (expected_sha256, expected_brief_sha256):
        _require(isinstance(value, str) and HEX64.fullmatch(value) is not None,
                 "必须提供可信调用方绑定的ZIP与参考方案摘要。")
    _require(bundle.is_file() and not bundle.is_symlink() and 0 < bundle.stat().st_size <= TOTAL_CAP,
             "参考包不存在、为链接或超过大小限制。")
    package_sha, package_size = _file_hash(bundle)
    _require(package_sha == expected_sha256.lower(), "参考包SHA-256不匹配。")
    _require(not any(p.is_symlink() for p in (output_dir, *output_dir.parents)), "参考输出路径不能包含链接。")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".reference-receive-", dir=output_dir.parent))
    lock = output_dir.with_name(output_dir.name + ".reference-receive.lock")
    owns_lock = False
    try:
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            _require(len(names) == len(set(names)), "参考包包含重复文件名。")
            allowed = ({"manifest.json", "brief.json"}, {"manifest.json", "brief.json", "source.png"},
                       {"manifest.json", "brief.json", "source.jpg"})
            _require(set(names) in allowed, "参考包只允许方案与一张可选原照片，不能夹带录音或其他文件。")
            for info in infos:
                _require(_regular(info) and info.flag_bits & 1 == 0 and info.compress_type == zipfile.ZIP_STORED,
                         "参考包只允许未压缩普通文件，不允许链接、目录或加密条目。")
                _require(0 < info.file_size <= CAPS[info.filename], "参考包条目声明大小不正确。")
            _require(sum(i.file_size for i in infos) <= TOTAL_CAP, "参考包总内容过大。")
            with archive.open("manifest.json") as stream:
                raw_manifest = stream.read(CAPS["manifest.json"] + 1)
            _require(len(raw_manifest) <= CAPS["manifest.json"], "参考清单过大。")
            manifest = _json_object(raw_manifest, "参考清单")
            _require(set(manifest) == {"format", "version", "brief_sha256", "files"}
                     and manifest["format"] == "lingnian-reference-bundle"
                     and type(manifest["version"]) is int and manifest["version"] == 1,
                     "参考清单格式或版本不支持。")
            _require(manifest["brief_sha256"] == expected_brief_sha256.lower(), "参考方案不是本任务指定的依据。")
            files = manifest["files"]
            _require(isinstance(files, dict) and set(files) == set(names) - {"manifest.json"}, "参考文件清单不一致。")
            expected = {"manifest.json": (hashlib.sha256(raw_manifest).hexdigest(), len(raw_manifest))}
            for name, evidence in files.items():
                _require(isinstance(evidence, dict) and set(evidence) == {"sha256", "size_bytes"}
                         and isinstance(evidence["sha256"], str) and HEX64.fullmatch(evidence["sha256"])
                         and type(evidence["size_bytes"]) is int and 0 < evidence["size_bytes"] <= CAPS[name],
                         "参考文件摘要或字节数格式不正确。")
                target = temporary / name
                with target.open("xb") as output, archive.open(name) as source:
                    target.chmod(0o600)
                    actual = _stream_hash(source, output, limit=CAPS[name])
                expected[name] = (evidence["sha256"].lower(), evidence["size_bytes"])
                _require(actual == expected[name], "参考文件实际摘要或大小与清单不符。")
            envelope = _json_object((temporary / "brief.json").read_bytes(), "参考方案")
            _require(set(envelope) == {"brief", "brief_sha256", "status", "generation_ready"}
                     and envelope["status"] == "awaiting_reference_generation_consent"
                     and envelope["generation_ready"] is False
                     and envelope["brief_sha256"] == expected_brief_sha256.lower(),
                     "参考方案不能夹带执行授权或核对通过标记。")
            photo_name = next((n for n in ("source.png", "source.jpg") if n in files), None)
            validate_brief(envelope, temporary / photo_name if photo_name else None)
            with (temporary / "manifest.json").open("xb") as target:
                (temporary / "manifest.json").chmod(0o600)
                target.write(raw_manifest)
        # Lock publication, not generation. Never erase a stale lock or a
        # conflicting file: these may be another process's incomplete evidence.
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise PackageError("已有参考接收任务，保留现场。") from exc
        owns_lock = True
        os.close(fd)
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require(output_dir.is_dir() and not output_dir.is_symlink(), "参考输出目录不正确。")
        report = {"format": "lingnian-reference-reception", "version": 1,
            "status": "reference_input_received", "bundle_sha256": package_sha, "bundle_size_bytes": package_size,
            "brief_sha256": expected_brief_sha256.lower(),
            "brief_path": str((output_dir / "brief.json").resolve()),
            "photo_path": str((output_dir / photo_name).resolve()) if photo_name else None,
            "identity_claim": envelope["brief"]["identity_claim"],
            "comfyui_called": False, "generation_authorized": False, "visual_accepted": False}
        report_bytes = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
        with (temporary / "reception.json").open("xb") as target:
            (temporary / "reception.json").chmod(0o600)
            target.write(report_bytes)
        expected["reception.json"] = (hashlib.sha256(report_bytes).hexdigest(), len(report_bytes))
        for name, evidence in expected.items():
            target = output_dir / name
            _require(not target.is_symlink(), "已有参考文件是链接，拒绝覆盖。")
            if target.exists():
                _require(target.is_file() and _file_hash(target) == evidence, "已有不同参考文件，拒绝覆盖。")
        wrote = []
        for name, evidence in expected.items():
            if _write_if_absent_or_same(temporary / name, output_dir / name, *evidence):
                (output_dir / name).chmod(0o600)
                wrote.append(name)
        return {**report, "written_files": wrote}
    except PackageError:
        raise
    except Exception as exc:
        raise PackageError("参考包读取或校验失败，未调用生成。") from exc
    finally:
        if owns_lock:
            lock.unlink(missing_ok=True)
        shutil.rmtree(temporary, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="校验参考素材包，不调用GPU")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-brief-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(receive_reference_bundle(args.bundle, expected_sha256=args.expected_sha256,
        expected_brief_sha256=args.expected_brief_sha256, output_dir=args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
