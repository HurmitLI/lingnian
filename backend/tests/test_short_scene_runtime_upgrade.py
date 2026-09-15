from sqlalchemy import create_engine, inspect, text


def test_restored_short_scene_schema_adds_fields_without_losing_jobs(monkeypatch, tmp_path):
    from app.core import database

    restored = create_engine(f"sqlite:///{tmp_path / 'restored.sqlite'}")
    with restored.begin() as connection:
        for table in ("short_scene_jobs", "short_scene_reference_jobs"):
            connection.execute(text(f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY, status VARCHAR(40))"))
            connection.execute(text(f"INSERT INTO {table} VALUES ('existing-job', 'queued')"))
    monkeypatch.setattr(database, "engine", restored)
    database._upgrade_short_scene_schema()
    database._upgrade_short_scene_schema()
    with restored.connect() as connection:
        inspector = inspect(connection)
        for table in ("short_scene_jobs", "short_scene_reference_jobs"):
            assert connection.execute(text(f"SELECT id, status, claim_request_key FROM {table}")).one() == ("existing-job", "queued", None)
            assert any(index["unique"] and index["column_names"] == ["claim_request_key"] for index in inspector.get_indexes(table))
        assert connection.execute(text("SELECT input_review, reference_review_sha256 FROM short_scene_jobs")).one() == ("{}", None)
    restored.dispose()
