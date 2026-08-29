from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ENVELOPE_FORMAT = "niannian-aesgcm"
ENVELOPE_VERSION = 1
MASTER_KEY_BYTES = 32
NONCE_BYTES = 12


class EncryptionError(ValueError):
    """Raised when encrypted content is invalid, tampered with, or uses the wrong key."""


def generate_master_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def _validate_master_key(master_key: bytes) -> None:
    if not isinstance(master_key, bytes) or len(master_key) != MASTER_KEY_BYTES:
        raise EncryptionError("家庭档案主密钥格式无效。")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: Any, field_name: str) -> bytes:
    if not isinstance(value, str):
        raise EncryptionError(f"加密数据缺少有效的 {field_name}。")
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as exc:
        raise EncryptionError(f"加密数据中的 {field_name} 格式无效。") from exc


def encrypt_payload(master_key: bytes, plaintext: bytes, *, associated_data: bytes) -> str:
    """Encrypt a small text/metadata payload with authenticated context.

    Large media files use a separate streaming format in the media-encryption milestone.
    """

    _validate_master_key(master_key)
    if not isinstance(plaintext, bytes) or not isinstance(associated_data, bytes):
        raise TypeError("plaintext 和 associated_data 必须是 bytes。")

    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(master_key).encrypt(nonce, plaintext, associated_data)
    envelope = {
        "format": ENVELOPE_FORMAT,
        "version": ENVELOPE_VERSION,
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(ciphertext),
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decrypt_payload(master_key: bytes, envelope_json: str, *, associated_data: bytes) -> bytes:
    _validate_master_key(master_key)
    if not isinstance(envelope_json, str) or not isinstance(associated_data, bytes):
        raise TypeError("envelope_json 必须是字符串，associated_data 必须是 bytes。")

    try:
        envelope = json.loads(envelope_json)
    except json.JSONDecodeError as exc:
        raise EncryptionError("加密数据格式无效。") from exc

    if not isinstance(envelope, dict):
        raise EncryptionError("加密数据格式无效。")
    if envelope.get("format") != ENVELOPE_FORMAT or envelope.get("version") != ENVELOPE_VERSION:
        raise EncryptionError("不支持的加密数据版本。")

    nonce = _b64decode(envelope.get("nonce"), "nonce")
    ciphertext = _b64decode(envelope.get("ciphertext"), "ciphertext")
    if len(nonce) != NONCE_BYTES:
        raise EncryptionError("加密数据中的 nonce 长度无效。")

    try:
        return AESGCM(master_key).decrypt(nonce, ciphertext, associated_data)
    except InvalidTag as exc:
        raise EncryptionError("资料无法解密：密钥错误或内容已被篡改。") from exc


def encrypt_text(master_key: bytes, plaintext: str, *, context: str) -> str:
    if not isinstance(plaintext, str) or not isinstance(context, str) or not context:
        raise TypeError("plaintext 和非空 context 必须是字符串。")
    return encrypt_payload(
        master_key,
        plaintext.encode("utf-8"),
        associated_data=context.encode("utf-8"),
    )


def decrypt_text(master_key: bytes, envelope_json: str, *, context: str) -> str:
    if not isinstance(context, str) or not context:
        raise TypeError("context 必须是非空字符串。")
    plaintext = decrypt_payload(
        master_key,
        envelope_json,
        associated_data=context.encode("utf-8"),
    )
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EncryptionError("解密后的文字编码无效。") from exc
