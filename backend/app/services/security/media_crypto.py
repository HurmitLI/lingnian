from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.security.crypto import MASTER_KEY_BYTES, NONCE_BYTES


MEDIA_MAGIC = b"NNMEDIA1"
MEDIA_TAG_BYTES = 16
MEDIA_VERSION = 1
CHUNK_BYTES = 1024 * 1024


class MediaEncryptionError(ValueError):
    """Raised when an encrypted media file is invalid or cannot be authenticated."""


@dataclass(frozen=True)
class MediaEncryptionResult:
    plaintext_size: int
    plaintext_sha256: str
    ciphertext_size: int
    ciphertext_sha256: str


def media_context(family_id: str, asset_id: str) -> bytes:
    if not family_id or not asset_id:
        raise TypeError("family_id 和 asset_id 不能为空。")
    return f"family:{family_id}:media:{asset_id}:v{MEDIA_VERSION}".encode("utf-8")


def _validate_key(master_key: bytes) -> None:
    if not isinstance(master_key, bytes) or len(master_key) != MASTER_KEY_BYTES:
        raise MediaEncryptionError("家庭档案主密钥格式无效。")


def encrypt_media_file(
    source_path: Path,
    target_path: Path,
    master_key: bytes,
    *,
    associated_data: bytes,
) -> MediaEncryptionResult:
    _validate_key(master_key)
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(master_key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(associated_data)
    plaintext_digest = hashlib.sha256()
    try:
        with source_path.open("rb") as source, target_path.open("xb") as target:
            target.write(MEDIA_MAGIC)
            target.write(nonce)
            while chunk := source.read(CHUNK_BYTES):
                plaintext_digest.update(chunk)
                target.write(encryptor.update(chunk))
            target.write(encryptor.finalize())
            target.write(encryptor.tag)
        target_path.chmod(0o600)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return MediaEncryptionResult(
        plaintext_size=source_path.stat().st_size,
        plaintext_sha256=plaintext_digest.hexdigest(),
        ciphertext_size=target_path.stat().st_size,
        ciphertext_sha256=_sha256_file(target_path),
    )


def decrypt_media_file(
    source_path: Path,
    target_path: Path,
    master_key: bytes,
    *,
    associated_data: bytes,
) -> MediaEncryptionResult:
    _validate_key(master_key)
    total_size = source_path.stat().st_size
    minimum_size = len(MEDIA_MAGIC) + NONCE_BYTES + MEDIA_TAG_BYTES
    if total_size < minimum_size:
        raise MediaEncryptionError("加密媒体文件格式无效。")
    plaintext_digest = hashlib.sha256()
    try:
        with source_path.open("rb") as source:
            magic = source.read(len(MEDIA_MAGIC))
            nonce = source.read(NONCE_BYTES)
            if magic != MEDIA_MAGIC or len(nonce) != NONCE_BYTES:
                raise MediaEncryptionError("加密媒体文件格式无效。")
            source.seek(total_size - MEDIA_TAG_BYTES)
            tag = source.read(MEDIA_TAG_BYTES)
            source.seek(len(MEDIA_MAGIC) + NONCE_BYTES)
            remaining = total_size - minimum_size
            decryptor = Cipher(algorithms.AES(master_key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(associated_data)
            with target_path.open("xb") as target:
                while remaining:
                    chunk = source.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise MediaEncryptionError("加密媒体文件不完整。")
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    plaintext_digest.update(plaintext)
                    target.write(plaintext)
                final = decryptor.finalize()
                plaintext_digest.update(final)
                target.write(final)
        target_path.chmod(0o600)
    except InvalidTag as exc:
        target_path.unlink(missing_ok=True)
        raise MediaEncryptionError("媒体无法解密：密钥错误或文件已被篡改。") from exc
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return MediaEncryptionResult(
        plaintext_size=target_path.stat().st_size,
        plaintext_sha256=plaintext_digest.hexdigest(),
        ciphertext_size=total_size,
        ciphertext_sha256=_sha256_file(source_path),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
