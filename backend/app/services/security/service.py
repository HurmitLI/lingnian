from __future__ import annotations

from functools import lru_cache

from app.services.security.key_store import (
    EnvironmentSecretStore,
    KEYCHAIN_SERVICE,
    MacOSKeychainStore,
    MasterKeyManager,
    SecretStore,
)


def family_key_account(family_id: str, key_version: int = 1) -> str:
    return f"family:{family_id}:master-key-v{key_version}"


@lru_cache
def get_secret_store() -> SecretStore:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.formal_auth_required:
        if settings.formal_archive_master_key is None:
            raise RuntimeError("FORMAL_ARCHIVE_MASTER_KEY is required")
        return EnvironmentSecretStore(
            settings.formal_archive_master_key.get_secret_value()
        )
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
