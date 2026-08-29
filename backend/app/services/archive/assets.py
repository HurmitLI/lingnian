from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.core.errors import DomainError


MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
}

IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def normalized_mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def matches_magic(mime: str, header: bytes) -> bool:
    if mime in {"audio/wav", "audio/x-wav"}:
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if mime == "audio/webm":
        return header.startswith(bytes.fromhex("1a45dfa3"))
    if mime in {"audio/mp4", "audio/x-m4a"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if mime == "audio/ogg":
        return header.startswith(b"OggS")
    if mime == "audio/mpeg":
        return header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        )
    return False


def resolve_controlled_path(asset_root: Path, relative_path: str) -> Path:
    candidate = (asset_root / relative_path).resolve()
    root = asset_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise DomainError("INVALID_ASSET_PATH", "文件路径不安全。", 400)
    return candidate


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_asset_integrity(path: Path, *, expected_size: int, expected_sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    actual_sha256 = calculate_sha256(path)
    return hmac.compare_digest(actual_sha256, expected_sha256)


async def store_audio_upload(
    upload: UploadFile, session_id: str, settings: Settings
) -> dict:
    mime = normalized_mime(upload.content_type)
    extension = MIME_EXTENSIONS.get(mime)
    if not extension:
        raise DomainError(
            "AUDIO_TYPE_NOT_ALLOWED", "这段音频格式暂不支持，请换一个文件再试。", 415
        )

    asset_root = settings.resolved_asset_root
    quarantine = asset_root / "quarantine"
    original = asset_root / "assets/original"
    quarantine.mkdir(parents=True, exist_ok=True)
    original.mkdir(parents=True, exist_ok=True)

    generated_name = f"{uuid4()}{extension}"
    temp_path = quarantine / f"{generated_name}.uploading"
    digest = hashlib.sha256()
    size = 0
    header = b""

    try:
        with temp_path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                if not header:
                    header = chunk[:32]
                size += len(chunk)
                if size > settings.max_audio_bytes:
                    raise DomainError(
                        "AUDIO_TOO_LARGE", "这段音频超过当前测试阶段的大小限制。", 413
                    )
                digest.update(chunk)
                destination.write(chunk)

        if size == 0:
            raise DomainError("AUDIO_EMPTY", "没有读取到音频内容。", 400)
        if not matches_magic(mime, header):
            raise DomainError(
                "AUDIO_CONTENT_MISMATCH", "文件内容与音频格式不一致。", 415
            )

        relative_path = f"assets/original/{session_id}/{generated_name}"
        final_path = resolve_controlled_path(asset_root, relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)
        final_path.chmod(0o600)
        return {
            "relative_path": relative_path,
            "original_filename": Path(upload.filename or "recording").name[:240],
            "mime_type": mime,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
        }
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


async def store_image_upload(
    upload: UploadFile, session_id: str, settings: Settings
) -> dict:
    mime = normalized_mime(upload.content_type)
    extension = IMAGE_MIME_EXTENSIONS.get(mime)
    if not extension:
        raise DomainError(
            "IMAGE_TYPE_NOT_ALLOWED", "当前只支持 JPEG、PNG 或 WebP 图片。", 415
        )
    asset_root = settings.resolved_asset_root
    quarantine = asset_root / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    generated_name = f"{uuid4()}{extension}"
    temp_path = quarantine / f"{generated_name}.uploading"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_image_bytes:
                    raise DomainError("IMAGE_TOO_LARGE", "这张图片超过 25 MB。", 413)
                digest.update(chunk)
                destination.write(chunk)
        if size == 0:
            raise DomainError("IMAGE_EMPTY", "没有读取到图片内容。", 400)
        try:
            with Image.open(temp_path) as image:
                width, height = image.size
                detected_format = image.format
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise DomainError(
                "IMAGE_CONTENT_MISMATCH", "文件内容不是可读取的图片。", 415
            ) from exc
        expected_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]
        if detected_format != expected_format:
            raise DomainError(
                "IMAGE_CONTENT_MISMATCH", "文件内容与图片格式不一致。", 415
            )
        if width * height > 40_000_000 or width > 12_000 or height > 12_000:
            raise DomainError("IMAGE_DIMENSIONS_TOO_LARGE", "图片尺寸过大。", 413)
        relative_path = f"assets/original/{session_id}/{generated_name}"
        final_path = resolve_controlled_path(asset_root, relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)
        final_path.chmod(0o600)
        return {
            "relative_path": relative_path,
            "original_filename": Path(upload.filename or "memory-image").name[:240],
            "mime_type": mime,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "width": width,
            "height": height,
        }
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
