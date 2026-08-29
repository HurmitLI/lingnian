from __future__ import annotations

import base64
import json

import pytest

from app.services.security import (
    EncryptionError,
    InMemorySecretStore,
    MasterKeyManager,
    RecoveryPackageError,
    SecretStoreError,
    build_recovery_package,
    decrypt_text,
    encrypt_text,
    generate_master_key,
    recover_master_key,
    write_recovery_package,
)


def test_encrypted_text_round_trip_and_context_binding():
    key = generate_master_key()
    envelope = encrypt_text(key, "虚构家庭故事", context="story:story-1:body")

    assert "虚构家庭故事" not in envelope
    assert decrypt_text(key, envelope, context="story:story-1:body") == "虚构家庭故事"
    with pytest.raises(EncryptionError, match="密钥错误或内容已被篡改"):
        decrypt_text(key, envelope, context="story:story-2:body")


def test_encrypted_text_detects_wrong_key_and_tampering():
    key = generate_master_key()
    envelope = encrypt_text(key, "测试文字", context="profile:1:name")

    with pytest.raises(EncryptionError, match="密钥错误或内容已被篡改"):
        decrypt_text(generate_master_key(), envelope, context="profile:1:name")

    payload = json.loads(envelope)
    ciphertext = bytearray(base64.urlsafe_b64decode(payload["ciphertext"]))
    ciphertext[-1] ^= 1
    payload["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode("ascii")
    tampered = json.dumps(payload)
    with pytest.raises(EncryptionError, match="密钥错误或内容已被篡改"):
        decrypt_text(key, tampered, context="profile:1:name")


def test_master_key_manager_creates_once_and_refuses_silent_overwrite():
    store = InMemorySecretStore()
    manager = MasterKeyManager(store)

    first, created = manager.get_or_create()
    second, created_again = manager.get_or_create()

    assert created is True
    assert created_again is False
    assert first == second
    with pytest.raises(SecretStoreError, match="已有不同"):
        manager.import_recovered(generate_master_key())


def test_recovery_package_round_trip_wrong_password_and_metadata_tampering():
    key = generate_master_key()
    passphrase = "虚构恢复口令-长度足够-2026"
    package = build_recovery_package(key, passphrase)

    assert base64.urlsafe_b64encode(key).decode("ascii") not in package
    assert recover_master_key(package, passphrase) == key
    with pytest.raises(RecoveryPackageError, match="口令错误或恢复包已被篡改"):
        recover_master_key(package, "另一个错误恢复口令-2026")

    payload = json.loads(package)
    payload["created_at"] = "2000-01-01T00:00:00+00:00"
    with pytest.raises(RecoveryPackageError, match="口令错误或恢复包已被篡改"):
        recover_master_key(json.dumps(payload), passphrase)


def test_recovery_package_file_is_private_and_never_overwritten(tmp_path):
    package_path = tmp_path / "家庭档案.念念恢复包.json"
    package = build_recovery_package(generate_master_key(), "虚构恢复口令-长度足够-2026")

    write_recovery_package(package_path, package)

    assert package_path.read_text("utf-8").strip() == package
    assert package_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RecoveryPackageError, match="已存在"):
        write_recovery_package(package_path, package)
