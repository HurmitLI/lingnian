"""Offline, fail-closed receiver for a single Lingnian short-scene bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

from .models import PackageError
from .short_scene import validate_plan


NAMES = ("manifest.json", "plan.json", "recording.wav", "reference.png")
PAYLOAD_NAMES = NAMES[1:]
CAPS = {
    "manifest.json": 16 * 1024,
    "plan.json": 256 * 1024,
    "reference.png": 32 * 1024 * 1024,
    "recording.wav": 256 * 1024 * 1024,
}
TOTAL_CAP = 300 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def _stream_hash(source: BinaryIO, target: BinaryIO | None = None, *, limit: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        _require(limit is None or size <= limit, "素材实际字节数超过离线包限制。")
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    return digest.hexdigest(), size


def _file_hash(path: Path) -> tuple[str, int]:
    with path.open("rb") as source:
        return _stream_hash(source)


def _regular(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    if info.create_system == 3:
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind and kind != stat.S_IFREG:
            return False
    return True


def _json_object(data: bytes, label: str) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, f"{label} 包含重复 JSON 字段。")
            value[key] = item
        return value
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"{label} 不是有效 UTF-8 JSON。") from exc
    _require(isinstance(value, dict), f"{label} 必须是 JSON 对象。")
    return value


def _validate_manifest(value: dict) -> dict[str, dict[str, object]]:
    _require(set(value) == {"format", "version", "files"}, "manifest.json 字段不符合 v1 契约。")
    _require(value.get("format") == "lingnian-short-scene-bundle"
             and type(value.get("version")) is int and value["version"] == 1,
             "离线短片包格式或版本不支持。")
    files = value.get("files")
    _require(isinstance(files, dict) and set(files) == set(PAYLOAD_NAMES), "manifest 文件清单必须精确包含三项素材。")
    for name in PAYLOAD_NAMES:
        item = files[name]
        _require(isinstance(item, dict) and set(item) == {"sha256", "size_bytes"}, f"{name} 摘要字段不正确。")
        _require(isinstance(item.get("sha256"), str) and HEX64.fullmatch(item["sha256"]), f"{name} SHA-256 不正确。")
        _require(type(item.get("size_bytes")) is int and 0 <= item["size_bytes"] <= CAPS[name], f"{name} 声明大小不正确。")
    _require(sum(files[name]["size_bytes"] for name in PAYLOAD_NAMES) + CAPS["manifest.json"] <= TOTAL_CAP,
             "离线短片包声明总大小超过限制。")
    return files


def _write_if_absent_or_same(source: Path, target: Path, expected_sha: str, expected_size: int) -> bool:
    _require(not target.is_symlink(), f"输出位置 {target.name} 不允许链接。")
    if target.exists():
        _require(target.is_file(), f"输出位置 {target.name} 不是普通文件。")
        actual_sha, actual_size = _file_hash(target)
        _require(actual_sha == expected_sha and actual_size == expected_size,
                 f"输出位置已有不同的 {target.name}，拒绝覆盖。")
        return False
    try:
        with source.open("rb") as incoming, target.open("xb") as output:
            copied_sha, copied_size = _stream_hash(incoming, output, limit=expected_size)
    except FileExistsError:
        actual_sha, actual_size = _file_hash(target)
        _require(actual_sha == expected_sha and actual_size == expected_size,
                 f"输出位置并发出现不同的 {target.name}，拒绝覆盖。")
        return False
    _require(copied_sha == expected_sha and copied_size == expected_size, "落盘复核失败。")
    return True


def receive_bundle(bundle: Path, *, expected_sha256: str, output_dir: Path) -> dict:
    _require(HEX64.fullmatch(expected_sha256 or "") is not None, "必须提供 64 位 ZIP SHA-256。")
    _require(bundle.is_file() and not bundle.is_symlink(), "离线短片 ZIP 不存在或是链接。")
    _require(bundle.stat().st_size <= TOTAL_CAP, "ZIP 文件大小超过 300MiB。")
    _require(not any(path.is_symlink() for path in (output_dir, *output_dir.parents)),
             "输出目录及其父目录不允许链接。")
    bundle_sha, bundle_size = _file_hash(bundle)
    _require(bundle_sha == expected_sha256.lower(), "离线短片 ZIP 的 SHA-256 不匹配。")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".short-scene-bundle-", dir=output_dir.parent))
    try:
        try:
            archive = zipfile.ZipFile(bundle)
        except (OSError, zipfile.BadZipFile) as exc:
            raise PackageError("离线短片包不是完整 ZIP。") from exc
        with archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            _require(len(names) == len(set(names)), "离线短片包包含重复文件名。")
            _require(len(names) == 4 and set(names) == set(NAMES), "ZIP 必须精确包含四个扁平文件。")
            for info in infos:
                _require(info.filename in NAMES and "/" not in info.filename and "\\" not in info.filename,
                         "ZIP 包含目录或不安全路径。")
                _require(_regular(info), "ZIP 只允许普通文件，不允许目录或链接。")
                _require(info.flag_bits & 1 == 0, "ZIP 不允许加密条目。")
                _require(0 <= info.file_size <= CAPS[info.filename], f"{info.filename} 中央目录大小超过限制。")

            manifest_info = next(info for info in infos if info.filename == "manifest.json")
            try:
                with archive.open(manifest_info) as source:
                    manifest_bytes = source.read(CAPS["manifest.json"] + 1)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise PackageError("manifest.json 读取或 CRC 校验失败。") from exc
            _require(len(manifest_bytes) <= CAPS["manifest.json"], "manifest.json 超过 16KiB。")
            manifest = _json_object(manifest_bytes, "manifest.json")
            declared = _validate_manifest(manifest)
            _require(sum(info.file_size for info in infos) <= TOTAL_CAP, "ZIP 中央目录总大小超过 300MiB。")

            actual_total = len(manifest_bytes)
            staged: dict[str, Path] = {}
            for name in PAYLOAD_NAMES:
                info = next(info for info in infos if info.filename == name)
                target = temporary / name
                try:
                    with archive.open(info) as source, target.open("xb") as output:
                        actual_sha, actual_size = _stream_hash(source, output, limit=CAPS[name])
                except (OSError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
                    raise PackageError(f"{name} 解压、截断或 CRC 校验失败。") from exc
                item = declared[name]
                _require(actual_size == item["size_bytes"] and actual_sha == str(item["sha256"]).lower(),
                         f"{name} 实际 SHA-256 或大小与 manifest 不符。")
                actual_total += actual_size
                _require(actual_total <= TOTAL_CAP, "离线短片包实际总字节数超过 300MiB。")
                staged[name] = target

            plan = _json_object(staged["plan.json"].read_bytes(), "plan.json")
            excerpt = validate_plan(plan, recording=staged["recording.wav"], reference=staged["reference.png"])

        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_staged = temporary / "manifest.json"
        manifest_staged.write_bytes(manifest_bytes)
        expected = {"manifest.json": (manifest_sha, len(manifest_bytes))}
        expected.update({name: (str(declared[name]["sha256"]).lower(), int(declared[name]["size_bytes"])) for name in PAYLOAD_NAMES})
        wrote = []
        # Reject all known conflicts before publishing any member of this bundle.
        for name in NAMES:
            target = output_dir / name
            _require(not target.is_symlink(), f"输出位置 {name} 不允许链接。")
            if target.exists():
                _require(target.is_file() and _file_hash(target) == expected[name],
                         f"输出位置已有不同的 {name}，拒绝覆盖。")
        for name in NAMES:
            if _write_if_absent_or_same(temporary / name, output_dir / name, *expected[name]):
                wrote.append(name)
        report = {
            "format": "lingnian-short-scene-bundle-preparation",
            "version": 1,
            "status": "accepted_offline_preparation",
            "bundle_sha256": bundle_sha,
            "bundle_size_bytes": bundle_size,
            "test_fixture_only": plan.get("test_fixture_only") is True,
            "recording_kind": plan.get("recording", {}).get("kind"),
            "paths": {name.removesuffix(Path(name).suffix): str((output_dir / name).resolve()) for name in PAYLOAD_NAMES},
            "manifest_path": str((output_dir / "manifest.json").resolve()),
            "excerpt": excerpt,
            "verified_files": list(NAMES),
            "comfyui_called": False,
            "approval_granted": False,
        }
        report_bytes = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        report_stage = temporary / "preparation-report.json"
        report_stage.write_bytes(report_bytes)
        _write_if_absent_or_same(report_stage, output_dir / "preparation-report.json",
                                 hashlib.sha256(report_bytes).hexdigest(), len(report_bytes))
        return {**report, "written_files": wrote}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="离线接收并校验单镜头短片包；不调用 ComfyUI")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(receive_bundle(args.bundle, expected_sha256=args.expected_sha256,
                                    output_dir=args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
