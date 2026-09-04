from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .models import AuthorizedPackage, PackageError


PACKAGE_MAGIC = b"LINGNIANPKG1"


def decrypt_package(payload: bytes, *, token: str, request_id: str) -> bytes:
    if not payload.startswith(PACKAGE_MAGIC) or len(payload) <= len(PACKAGE_MAGIC) + 28:
        raise PackageError("素材包格式不正确。")
    offset = len(PACKAGE_MAGIC)
    nonce = payload[offset : offset + 12]
    ciphertext = payload[offset + 12 :]
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=request_id.encode("ascii"),
        info=b"lingnian-generation-package-v1",
    ).derive(token.encode("utf-8"))
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, request_id.encode("ascii"))
    except Exception as exc:
        raise PackageError("素材包校验失败，未写入明文文件。") from exc


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for info in archive.infolist():
        relative = PurePosixPath(info.filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise PackageError("素材包包含不安全路径。")
        target = destination.joinpath(*relative.parts)
        target.resolve().relative_to(destination.resolve())
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError("素材包缺少有效的制作说明。") from exc
    if not isinstance(value, dict):
        raise PackageError("制作说明格式不正确。")
    return value


def _source_paths(root: Path, manifest: dict) -> tuple[Path | None, Path | None]:
    audio: Path | None = None
    image: Path | None = None
    for item in manifest.get("media") or []:
        relative = PurePosixPath(str(item.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise PackageError("素材路径不安全。")
        path = root.joinpath(*relative.parts)
        if not path.is_file():
            raise PackageError("授权素材不完整。")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest.lower() != str(item.get("sha256", "")).lower():
            raise PackageError("授权素材校验失败。")
        mime_type = str(item.get("mime_type", ""))
        if mime_type.startswith("audio/"):
            audio = path
        elif mime_type.startswith("image/"):
            image = path
    return audio, image


def open_authorized_package(
    encrypted_payload: bytes,
    *,
    token: str,
    request_id: str,
    destination: Path,
) -> AuthorizedPackage:
    archive_payload = decrypt_package(encrypted_payload, token=token, request_id=request_id)
    try:
        with zipfile.ZipFile(io.BytesIO(archive_payload)) as archive:
            _safe_extract(archive, destination)
    except zipfile.BadZipFile as exc:
        raise PackageError("解密后的素材包不是有效 ZIP。") from exc
    manifest = _read_json(destination / "manifest.json")
    if manifest.get("status") != "authorized_node_job":
        raise PackageError("素材包没有本次外发授权。")
    authorization = manifest.get("authorization") or {}
    if not authorization.get("external_upload_authorized"):
        raise PackageError("素材包没有外发授权。")
    if str(manifest.get("generation_type")) == "scene_video" and int(manifest.get("version", 0)) < 2:
        raise PackageError("纪实影片需要 v2 制作包。")
    plan_relative = PurePosixPath(str(manifest.get("plan_path", "")))
    if plan_relative.is_absolute() or ".." in plan_relative.parts:
        raise PackageError("分镜路径不安全。")
    plan = _read_json(destination.joinpath(*plan_relative.parts))
    audio, image = _source_paths(destination, manifest)
    return AuthorizedPackage(
        root=destination,
        manifest=manifest,
        plan=plan,
        audio_path=audio,
        image_path=image,
    )
