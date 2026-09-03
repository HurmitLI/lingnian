from __future__ import annotations

import base64
import sys
from dataclasses import dataclass, field
from typing import Protocol

from app.services.security.crypto import MASTER_KEY_BYTES, generate_master_key


KEYCHAIN_SERVICE = "cn.niannian.local-archive"
MASTER_KEY_ACCOUNT = "master-key-v1"


class SecretStoreError(RuntimeError):
    """Raised when a secret cannot be read from or written to the system store."""


class SecretStore(Protocol):
    def get_secret(self, service: str, account: str) -> str | None: ...

    def set_secret(self, service: str, account: str, secret: str) -> None: ...


@dataclass
class InMemorySecretStore:
    values: dict[tuple[str, str], str] = field(default_factory=dict)

    def get_secret(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_secret(self, service: str, account: str, secret: str) -> None:
        self.values[(service, account)] = secret


@dataclass
class EnvironmentSecretStore:
    """Read one deployment-scoped archive key from the managed secret environment."""

    encoded_master_key: str

    def get_secret(self, service: str, account: str) -> str | None:
        return self.encoded_master_key

    def set_secret(self, service: str, account: str, secret: str) -> None:
        if secret != self.encoded_master_key:
            raise SecretStoreError("云端主密钥只能通过部署环境的加密变量轮换。")


class MacOSKeychainStore:
    """Store small secrets in the current macOS user's Keychain via keyring."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise SecretStoreError("Mac 钥匙串只可在 macOS 用户环境中使用。")
        try:
            import keyring
        except ImportError as exc:
            raise SecretStoreError("缺少 keyring 依赖，无法访问 Mac 钥匙串。") from exc
        self._keyring = keyring

    def get_secret(self, service: str, account: str) -> str | None:
        try:
            return self._keyring.get_password(service, account)
        except Exception as exc:  # keyring backends expose platform-specific exceptions
            raise SecretStoreError("无法从 Mac 钥匙串读取家庭档案密钥。") from exc

    def set_secret(self, service: str, account: str, secret: str) -> None:
        try:
            self._keyring.set_password(service, account, secret)
        except Exception as exc:  # keyring backends expose platform-specific exceptions
            raise SecretStoreError("无法把家庭档案密钥保存到 Mac 钥匙串。") from exc


class MasterKeyManager:
    def __init__(
        self,
        store: SecretStore,
        *,
        service: str = KEYCHAIN_SERVICE,
        account: str = MASTER_KEY_ACCOUNT,
    ) -> None:
        self.store = store
        self.service = service
        self.account = account

    @staticmethod
    def _encode_key(master_key: bytes) -> str:
        if not isinstance(master_key, bytes) or len(master_key) != MASTER_KEY_BYTES:
            raise SecretStoreError("家庭档案主密钥格式无效。")
        return base64.urlsafe_b64encode(master_key).decode("ascii")

    @staticmethod
    def _decode_key(value: str) -> bytes:
        try:
            key = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
        except (ValueError, UnicodeError) as exc:
            raise SecretStoreError("Mac 钥匙串中的家庭档案密钥格式无效。") from exc
        if len(key) != MASTER_KEY_BYTES:
            raise SecretStoreError("Mac 钥匙串中的家庭档案密钥长度无效。")
        return key

    def get_existing(self) -> bytes | None:
        value = self.store.get_secret(self.service, self.account)
        return None if value is None else self._decode_key(value)

    def get_or_create(self) -> tuple[bytes, bool]:
        existing = self.get_existing()
        if existing is not None:
            return existing, False
        master_key = generate_master_key()
        self.store.set_secret(self.service, self.account, self._encode_key(master_key))
        return master_key, True

    def import_recovered(self, master_key: bytes, *, overwrite: bool = False) -> None:
        existing = self.get_existing()
        if existing is not None and existing != master_key and not overwrite:
            raise SecretStoreError("Mac 钥匙串中已有不同的家庭档案密钥，未执行覆盖。")
        self.store.set_secret(self.service, self.account, self._encode_key(master_key))
