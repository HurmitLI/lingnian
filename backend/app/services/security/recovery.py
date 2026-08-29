from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.services.security.crypto import MASTER_KEY_BYTES, NONCE_BYTES


RECOVERY_FORMAT = "niannian-recovery"
RECOVERY_VERSION = 1
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16


class RecoveryPackageError(ValueError):
    """Raised when a recovery package or passphrase cannot restore the master key."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: Any, field_name: str) -> bytes:
    if not isinstance(value, str):
        raise RecoveryPackageError(f"恢复包缺少有效的 {field_name}。")
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as exc:
        raise RecoveryPackageError(f"恢复包中的 {field_name} 格式无效。") from exc


def _validate_passphrase(passphrase: str) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise RecoveryPackageError("恢复口令至少需要 12 个字符。")
    return passphrase.encode("utf-8")


def _derive_recovery_key(passphrase: bytes, *, salt: bytes, n: int, r: int, p: int) -> bytes:
    try:
        return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase)
    except (TypeError, ValueError) as exc:
        raise RecoveryPackageError("恢复包的密钥派生参数无效。") from exc


def _authenticated_metadata(package: dict[str, Any]) -> bytes:
    metadata = {
        "cipher": package["cipher"],
        "created_at": package["created_at"],
        "format": package["format"],
        "kdf": package["kdf"],
        "version": package["version"],
    }
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def build_recovery_package(master_key: bytes, passphrase: str) -> str:
    if not isinstance(master_key, bytes) or len(master_key) != MASTER_KEY_BYTES:
        raise RecoveryPackageError("家庭档案主密钥格式无效。")
    passphrase_bytes = _validate_passphrase(passphrase)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    package: dict[str, Any] = {
        "format": RECOVERY_FORMAT,
        "version": RECOVERY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cipher": "AES-256-GCM",
        "kdf": {
            "name": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "length": 32,
            "salt": _b64encode(salt),
        },
    }
    recovery_key = _derive_recovery_key(
        passphrase_bytes,
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    wrapped_key = AESGCM(recovery_key).encrypt(
        nonce,
        master_key,
        _authenticated_metadata(package),
    )
    package["nonce"] = _b64encode(nonce)
    package["wrapped_master_key"] = _b64encode(wrapped_key)
    return json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)


def recover_master_key(package_json: str, passphrase: str) -> bytes:
    passphrase_bytes = _validate_passphrase(passphrase)
    try:
        package = json.loads(package_json)
    except json.JSONDecodeError as exc:
        raise RecoveryPackageError("恢复包不是有效的 JSON 文件。") from exc
    if not isinstance(package, dict):
        raise RecoveryPackageError("恢复包格式无效。")
    if package.get("format") != RECOVERY_FORMAT or package.get("version") != RECOVERY_VERSION:
        raise RecoveryPackageError("不支持的恢复包版本。")
    if package.get("cipher") != "AES-256-GCM":
        raise RecoveryPackageError("不支持的恢复包加密算法。")

    kdf = package.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("name") != "scrypt" or kdf.get("length") != 32:
        raise RecoveryPackageError("不支持的恢复包密钥派生配置。")
    try:
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryPackageError("恢复包的密钥派生参数无效。") from exc
    if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
        raise RecoveryPackageError("恢复包的密钥派生参数不符合当前安全策略。")

    salt = _b64decode(kdf.get("salt"), "salt")
    nonce = _b64decode(package.get("nonce"), "nonce")
    wrapped_key = _b64decode(package.get("wrapped_master_key"), "wrapped_master_key")
    if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
        raise RecoveryPackageError("恢复包中的 salt 或 nonce 长度无效。")

    recovery_key = _derive_recovery_key(passphrase_bytes, salt=salt, n=n, r=r, p=p)
    try:
        master_key = AESGCM(recovery_key).decrypt(
            nonce,
            wrapped_key,
            _authenticated_metadata(package),
        )
    except (InvalidTag, KeyError, TypeError) as exc:
        raise RecoveryPackageError("恢复失败：恢复口令错误或恢复包已被篡改。") from exc
    if len(master_key) != MASTER_KEY_BYTES:
        raise RecoveryPackageError("恢复出的家庭档案主密钥长度无效。")
    return master_key


def write_recovery_package(path: Path, package_json: str) -> None:
    """Create a new recovery-package file with owner-only permissions.

    Existing files are never overwritten so a previous known-good package cannot be replaced silently.
    """

    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RecoveryPackageError("恢复包文件已存在，未执行覆盖。") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(package_json)
            handle.write("\n")
    except Exception:
        resolved.unlink(missing_ok=True)
        raise
