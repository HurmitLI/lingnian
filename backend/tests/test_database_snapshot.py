from __future__ import annotations

import sqlite3

from app.services.database_snapshot import (
    persist_database_snapshot,
    restore_latest_database_snapshot,
)


def test_database_snapshot_round_trip_and_retention(tmp_path):
    database_path = tmp_path / "runtime" / "lingnian.db"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO memories(title) VALUES ('第一段回忆')")
        connection.commit()

    snapshot_dir = tmp_path / "persistent" / "database-snapshots"
    first = persist_database_snapshot(database_path, snapshot_dir, keep=2)
    assert first.is_file()

    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO memories(title) VALUES ('第二段回忆')")
        connection.commit()
    persist_database_snapshot(database_path, snapshot_dir, keep=2)

    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO memories(title) VALUES ('第三段回忆')")
        connection.commit()
    latest = persist_database_snapshot(database_path, snapshot_dir, keep=2)
    assert latest.is_file()
    assert len(list(snapshot_dir.glob("lingnian-*.sqlite3"))) == 2

    database_path.unlink()
    assert restore_latest_database_snapshot(database_path, snapshot_dir) is True
    with sqlite3.connect(database_path) as connection:
        titles = [row[0] for row in connection.execute("SELECT title FROM memories ORDER BY id")]
    assert titles == ["第一段回忆", "第二段回忆", "第三段回忆"]


def test_restore_skips_corrupt_newer_snapshot(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE state (value TEXT)")
        connection.execute("INSERT INTO state(value) VALUES ('safe')")
        connection.commit()
    snapshot_dir = tmp_path / "snapshots"
    valid = persist_database_snapshot(source, snapshot_dir)
    corrupt = snapshot_dir / valid.name.replace("lingnian-", "lingnian-z")
    corrupt.write_bytes(b"not-a-database")

    restored = tmp_path / "restored.db"
    assert restore_latest_database_snapshot(restored, snapshot_dir) is True
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM state").fetchone() == ("safe",)
