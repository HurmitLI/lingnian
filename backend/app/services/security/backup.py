from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.core.config import Settings
from app.services.security.crypto import decrypt_text
from app.services.security.media_crypto import decrypt_media_file, media_context
from app.services.security.secure_fields import field_context


BACKUP_FORMAT = "niannian-local-backup"
BACKUP_VERSION = 1
BACKUP_PREFIXES = ("assets", "exports")


class BackupError(ValueError):
    """Raised when a backup cannot be created or verified safely."""


@dataclass(frozen=True)
class BackupBuildResult:
    path: Path
    archive_sha256: str
    database_sha256: str
    asset_count: int


@dataclass(frozen=True)
class BackupVerificationResult:
    database_path: Path
    asset_root: Path
    database_sha256: str
    asset_count: int
    alembic_revision: str


@dataclass(frozen=True)
class RecoveryRehearsalResult:
    decrypted_field_count: int
    decrypted_media_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _database_path(settings: Settings) -> Path:
    prefix = "sqlite:///"
    url = settings.resolved_database_url
    if not url.startswith(prefix) or url.endswith(":memory:"):
        raise BackupError("当前只支持本机 SQLite 档案备份。")
    return Path(url[len(prefix) :]).resolve()


def _asset_entries(asset_root: Path) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for prefix in BACKUP_PREFIXES:
        base = asset_root / prefix
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise BackupError("备份资产目录中不允许符号链接。")
            if path.is_file():
                entries.append((path, path.relative_to(asset_root).as_posix()))
    return entries


def build_local_backup(settings: Settings, output_path: Path) -> BackupBuildResult:
    database_path = _database_path(settings)
    if not database_path.is_file():
        raise BackupError("没有找到本机档案数据库。")
    asset_root = settings.resolved_asset_root
    entries = _asset_entries(asset_root)
    resolved_output = output_path.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    if resolved_output.exists():
        raise BackupError("备份文件已存在，未执行覆盖。")

    with tempfile.TemporaryDirectory(prefix="niannian-backup-") as temp_name:
        temp_root = Path(temp_name)
        snapshot_path = temp_root / "niannian.db"
        with sqlite3.connect(database_path) as source, sqlite3.connect(snapshot_path) as target:
            source.backup(target)
        database_sha256 = _sha256_file(snapshot_path)
        asset_manifest = [
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for path, relative in entries
        ]
        with sqlite3.connect(snapshot_path) as snapshot:
            try:
                revision_row = snapshot.execute(
                    "select version_num from alembic_version"
                ).fetchone()
            except sqlite3.OperationalError:
                revision_row = None
        manifest = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "database": {
                "path": "database/niannian.db",
                "sha256": database_sha256,
                "alembic_revision": revision_row[0] if revision_row else "unknown",
            },
            "assets": asset_manifest,
            "security": {
                "contains_master_key": False,
                "contains_recovery_passphrase": False,
                "recovery_package_included": False,
            },
        }
        temporary_archive = temp_root / "backup.zip"
        with zipfile.ZipFile(
            temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.write(snapshot_path, "database/niannian.db")
            archive.writestr(
                "backup-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            for path, relative in entries:
                archive.write(path, f"files/{relative}")
        descriptor = os.open(
            resolved_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        os.close(descriptor)
        try:
            os.replace(temporary_archive, resolved_output)
            resolved_output.chmod(0o600)
        except Exception:
            resolved_output.unlink(missing_ok=True)
            raise
    return BackupBuildResult(
        path=resolved_output,
        archive_sha256=_sha256_file(resolved_output),
        database_sha256=database_sha256,
        asset_count=len(entries),
    )


def verify_local_backup(
    archive_path: Path, restore_root: Path
) -> BackupVerificationResult:
    resolved_archive = archive_path.resolve()
    resolved_restore = restore_root.resolve()
    if not resolved_archive.is_file():
        raise BackupError("没有找到待验证的备份文件。")
    if resolved_restore.exists() and any(resolved_restore.iterdir()):
        raise BackupError("恢复目录必须为空，未执行覆盖。")
    resolved_restore.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(resolved_archive) as archive:
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise BackupError("备份中包含不安全路径。")
        try:
            manifest = json.loads(archive.read("backup-manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise BackupError("备份清单缺失或格式无效。") from exc
        if (
            manifest.get("format") != BACKUP_FORMAT
            or manifest.get("version") != BACKUP_VERSION
        ):
            raise BackupError("不支持这个备份版本。")
        database_info = manifest.get("database")
        assets = manifest.get("assets")
        security = manifest.get("security")
        if not isinstance(database_info, dict) or not isinstance(assets, list):
            raise BackupError("备份清单格式无效。")
        if not isinstance(security, dict) or any(
            security.get(field) is not False
            for field in ("contains_master_key", "contains_recovery_passphrase", "recovery_package_included")
        ):
            raise BackupError("备份安全声明无效。")

        database_path = resolved_restore / "database" / "niannian.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("database/niannian.db") as source, database_path.open(
            "xb"
        ) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        database_path.chmod(0o600)
        if _sha256_file(database_path) != database_info.get("sha256"):
            raise BackupError("备份数据库校验失败。")
        asset_root = resolved_restore / "data"
        for item in assets:
            if not isinstance(item, dict):
                raise BackupError("备份资产清单格式无效。")
            relative = PurePosixPath(str(item.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts:
                raise BackupError("备份资产路径不安全。")
            target = asset_root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                source = archive.open(f"files/{relative.as_posix()}")
            except KeyError as exc:
                raise BackupError("备份资产文件缺失。") from exc
            with source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            target.chmod(0o600)
            if target.stat().st_size != item.get("size_bytes") or _sha256_file(
                target
            ) != item.get("sha256"):
                raise BackupError("备份资产完整性校验失败。")

    with sqlite3.connect(database_path) as restored:
        integrity = restored.execute("pragma integrity_check").fetchone()
        try:
            revision = restored.execute(
                "select version_num from alembic_version"
            ).fetchone()
        except sqlite3.OperationalError:
            revision = None
    if not integrity or integrity[0] != "ok":
        raise BackupError("恢复后的 SQLite 数据库完整性检查失败。")
    restored_revision = revision[0] if revision else "unknown"
    if restored_revision != database_info.get("alembic_revision"):
        raise BackupError("恢复后的数据库版本不匹配。")
    return BackupVerificationResult(
        database_path=database_path,
        asset_root=asset_root,
        database_sha256=database_info["sha256"],
        asset_count=len(assets),
        alembic_revision=restored_revision,
    )


def rehearse_family_recovery(
    verification: BackupVerificationResult,
    *,
    family_id: str,
    master_key: bytes,
) -> RecoveryRehearsalResult:
    with sqlite3.connect(verification.database_path) as restored:
        restored.row_factory = sqlite3.Row
        family = restored.execute(
            "select id, data_classification from family_archives where id = ?",
            (family_id,),
        ).fetchone()
        security = restored.execute(
            "select key_version, encryption_status from archive_security where family_id = ?",
            (family_id,),
        ).fetchone()
        if family is None or security is None:
            raise BackupError("备份中没有找到对应的家庭加密元数据。")
        if (
            family["data_classification"] == "test"
            or security["encryption_status"] != "active_encrypted"
        ):
            raise BackupError("这个家庭尚未启用真实资料加密。")
        fields = restored.execute(
            """
            select object_type, object_id, field_name, ciphertext,
                   content_sha256, key_version
            from encrypted_fields
            where family_id = ?
            order by created_at
            """,
            (family_id,),
        ).fetchall()
        if not fields:
            raise BackupError("备份中没有可用于恢复验证的加密文本。")
        for field in fields:
            plaintext = decrypt_text(
                master_key,
                field["ciphertext"],
                context=field_context(
                    family_id,
                    field["object_type"],
                    field["object_id"],
                    field["field_name"],
                    field["key_version"],
                ),
            )
            if hashlib.sha256(plaintext.encode("utf-8")).hexdigest() != field[
                "content_sha256"
            ]:
                raise BackupError("恢复后的加密文本校验失败。")

        media_rows = restored.execute(
            """
            select ma.id, ma.relative_path, ma.plaintext_size_bytes,
                   ma.plaintext_sha256
            from media_assets ma
            join memory_sessions ms on ms.id = ma.session_id
            join elder_profiles ep on ep.id = ms.elder_id
            join people p on p.id = ep.person_id
            where p.family_id = ? and ma.encryption_version = 1
            union all
            select k.id, k.relative_path, k.plaintext_size_bytes,
                   k.plaintext_sha256
            from keepsakes k
            join elder_profiles ep on ep.id = k.elder_id
            join people p on p.id = ep.person_id
            where p.family_id = ? and k.encryption_version = 1
            order by 1
            """,
            (family_id, family_id),
        ).fetchall()
    decrypted_media_count = 0
    if media_rows:
        with tempfile.TemporaryDirectory(prefix="niannian-media-recovery-") as name:
            for row in media_rows:
                encrypted_path = verification.asset_root / row["relative_path"]
                target = Path(name) / f"{row['id']}.restored"
                result = decrypt_media_file(
                    encrypted_path,
                    target,
                    master_key,
                    associated_data=media_context(family_id, row["id"]),
                )
                if (
                    result.plaintext_size != row["plaintext_size_bytes"]
                    or result.plaintext_sha256 != row["plaintext_sha256"]
                ):
                    raise BackupError("恢复后的媒体明文校验失败。")
                decrypted_media_count += 1
    return RecoveryRehearsalResult(
        decrypted_field_count=len(fields),
        decrypted_media_count=decrypted_media_count,
    )
