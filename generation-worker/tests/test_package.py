from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from lingnian_worker.models import PackageError
from lingnian_worker.package import PACKAGE_MAGIC, open_authorized_package


def encrypt(archive: bytes, *, token: str, request_id: str) -> bytes:
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=request_id.encode("ascii"),
        info=b"lingnian-generation-package-v1",
    ).derive(token.encode("utf-8"))
    nonce = b"0123456789ab"
    encrypted = AESGCM(key).encrypt(nonce, archive, request_id.encode("ascii"))
    return PACKAGE_MAGIC + nonce + encrypted


def package_bytes(*, unsafe_name: str | None = None) -> tuple[bytes, str, str]:
    token = "ln_node_test-token-with-enough-entropy-123456"
    request_id = "12345678-1234-1234-1234-123456789012"
    image = b"authorized-image"
    manifest = {
        "version": 2,
        "status": "authorized_node_job",
        "generation_type": "scene_video",
        "authorization": {"external_upload_authorized": True},
        "plan_path": "production/storyboard.json",
        "media": [
            {
                "path": "sources/authorized-image.jpg",
                "mime_type": "image/jpeg",
                "sha256": hashlib.sha256(image).hexdigest(),
            }
        ],
    }
    archive_file = io.BytesIO()
    with zipfile.ZipFile(archive_file, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(
            "production/storyboard.json",
            json.dumps({"format": "lingnian-documentary-storyboard", "version": 2, "scenes": []}),
        )
        archive.writestr("sources/authorized-image.jpg", image)
        if unsafe_name:
            archive.writestr(unsafe_name, b"blocked")
    return encrypt(archive_file.getvalue(), token=token, request_id=request_id), token, request_id


def test_decrypts_authorized_package_and_verifies_source_hash(tmp_path):
    payload, token, request_id = package_bytes()
    opened = open_authorized_package(
        payload,
        token=token,
        request_id=request_id,
        destination=tmp_path / "open",
    )
    assert opened.manifest["generation_type"] == "scene_video"
    assert opened.image_path is not None
    assert opened.image_path.read_bytes() == b"authorized-image"


def test_rejects_wrong_token_before_writing_plaintext(tmp_path):
    payload, _, request_id = package_bytes()
    destination = tmp_path / "open"
    with pytest.raises(PackageError, match="校验失败"):
        open_authorized_package(
            payload,
            token="ln_node_wrong-token-with-enough-entropy-123456",
            request_id=request_id,
            destination=destination,
        )
    assert not destination.exists()


def test_rejects_zip_path_traversal(tmp_path):
    payload, token, request_id = package_bytes(unsafe_name="../escape.txt")
    with pytest.raises(PackageError, match="不安全路径"):
        open_authorized_package(
            payload,
            token=token,
            request_id=request_id,
            destination=tmp_path / "open",
        )
    assert not (tmp_path / "escape.txt").exists()
