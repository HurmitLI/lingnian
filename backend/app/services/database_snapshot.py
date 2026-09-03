from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.core.config import Settings, get_settings


_snapshot_lock = Lock()


class DatabaseSnapshotError(RuntimeError):
    """Raised when the formal cloud database cannot be restored or persisted."""


def sqlite_database_path(settings: Settings) -> Path | None:
    prefix = "sqlite:///"
    database_url = settings.resolved_database_url
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url.removeprefix(prefix)
    if raw_path == ":memory:":
        return None
    return Path(raw_path).resolve()


def _is_valid_snapshot(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 100:
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            return connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    except sqlite3.Error:
        return False


def restore_latest_database_snapshot(database_path: Path, snapshot_dir: Path) -> bool:
    """Restore the newest intact immutable snapshot into an empty local runtime."""

    if database_path.exists() and database_path.stat().st_size > 0:
        return False
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted(snapshot_dir.glob("lingnian-*.sqlite3"), reverse=True)
    latest = next((candidate for candidate in candidates if _is_valid_snapshot(candidate)), None)
    if latest is None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        return False
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="lingnian-restore-", suffix=".sqlite3", dir=database_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copyfile(latest, temporary_path)
        if not _is_valid_snapshot(temporary_path):
            raise DatabaseSnapshotError("最新云端数据库快照未通过完整性校验。")
        temporary_path.replace(database_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return True


def persist_database_snapshot(
    database_path: Path,
    snapshot_dir: Path,
    *,
    keep: int = 8,
) -> Path:
    """Create an immutable, integrity-checked SQLite snapshot on persistent storage."""

    if not database_path.is_file():
        raise DatabaseSnapshotError("本机运行数据库不存在，无法写入云端快照。")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_path = snapshot_dir / f"lingnian-{timestamp}-{uuid4().hex[:8]}.sqlite3"
    with _snapshot_lock:
        with tempfile.NamedTemporaryFile(
            prefix="lingnian-snapshot-", suffix=".sqlite3", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            with sqlite3.connect(database_path) as source, sqlite3.connect(
                temporary_path
            ) as destination:
                source.backup(destination)
            if not _is_valid_snapshot(temporary_path):
                raise DatabaseSnapshotError("新数据库快照未通过完整性校验。")
            with temporary_path.open("rb") as source, snapshot_path.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if not _is_valid_snapshot(snapshot_path):
                snapshot_path.unlink(missing_ok=True)
                raise DatabaseSnapshotError("云端数据库快照写入后校验失败。")
        finally:
            temporary_path.unlink(missing_ok=True)

        snapshots = sorted(snapshot_dir.glob("lingnian-*.sqlite3"), reverse=True)
        valid_seen = 0
        for candidate in snapshots:
            if _is_valid_snapshot(candidate):
                valid_seen += 1
                if valid_seen <= keep:
                    continue
            candidate.unlink(missing_ok=True)
    return snapshot_path


def restore_configured_database_snapshot(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    database_path = sqlite_database_path(settings)
    snapshot_dir = settings.formal_database_snapshot_dir
    if not settings.formal_auth_required or database_path is None or snapshot_dir is None:
        return False
    return restore_latest_database_snapshot(database_path, snapshot_dir.resolve())


def persist_configured_database_snapshot(settings: Settings | None = None) -> Path | None:
    settings = settings or get_settings()
    database_path = sqlite_database_path(settings)
    snapshot_dir = settings.formal_database_snapshot_dir
    if not settings.formal_auth_required or database_path is None or snapshot_dir is None:
        return None
    return persist_database_snapshot(database_path, snapshot_dir.resolve())
