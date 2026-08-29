from __future__ import annotations

from functools import lru_cache

from app.services.security.key_store import (
    KEYCHAIN_SERVICE,
    MacOSKeychainStore,
    MasterKeyManager,
    SecretStore,
)


def family_key_account(family_id: str, key_version: int = 1) -> str:
    return f"family:{family_id}:master-key-v{key_version}"


@lru_cache
def get_secret_store() -> SecretStore:
    return MacOSKeychainStore()


def get_family_key_manager(
    family_id: str,
    store: SecretStore,
    *,
    key_version: int = 1,
) -> MasterKeyManager:
    return MasterKeyManager(
        store,
        service=KEYCHAIN_SERVICE,
        account=family_key_account(family_id, key_version),
    )
