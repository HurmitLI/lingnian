from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base, WorkflowTask


settings = get_settings()
engine = create_engine(
    settings.resolved_database_url,
    connect_args={"check_same_thread": False}
    if settings.resolved_database_url.startswith("sqlite")
    else {},
)


if settings.resolved_database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    settings.resolved_asset_root.mkdir(parents=True, exist_ok=True)
    for relative in ("db", "assets/original", "assets/derived", "quarantine"):
        (settings.resolved_asset_root / relative).mkdir(parents=True, exist_ok=True)
    _upgrade_compatible_runtime_schema()
    Base.metadata.create_all(bind=engine)
    _ensure_generation_queue_indexes()
    with SessionLocal() as db:
        db.execute(
            update(WorkflowTask)
            .where(WorkflowTask.status.in_(["queued", "running"]))
            .values(status="failed_retryable", error_code="PROCESS_INTERRUPTED")
        )
        db.commit()


def _upgrade_compatible_runtime_schema() -> None:
    """Apply additive compatibility upgrades after a cloud snapshot is restored.

    The formal serverless runtime restores SQLite inside the application lifespan,
    so a shell-level Alembic command would run before the real database exists.
    Keep this hook additive and idempotent; destructive migrations still belong in
    Alembic and must not be performed automatically at startup.
    """
    if not settings.resolved_database_url.startswith("sqlite:///"):
        return
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "user_accounts" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("user_accounts")}
        if "platform_role" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE user_accounts ADD COLUMN platform_role "
                    "VARCHAR(24) NOT NULL DEFAULT 'user'"
                )
            )
            connection.execute(
                text(
                    "UPDATE user_accounts SET platform_role = 'admin' "
                    "WHERE id = (SELECT id FROM user_accounts "
                    "ORDER BY created_at ASC LIMIT 1)"
                )
            )

        table_names = set(inspector.get_table_names())
        if "generative_media_requests" not in table_names:
            return
        request_columns = {
            column["name"]
            for column in inspector.get_columns("generative_media_requests")
        }
        additive_columns = {
            "assigned_node_id": "VARCHAR(36)",
            "lease_token_hash": "VARCHAR(64)",
            "lease_expires_at": "DATETIME",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "progress_percent": "INTEGER NOT NULL DEFAULT 0",
            "progress_stage": "VARCHAR(80)",
            "queued_at": "DATETIME",
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
            "last_error_message": "VARCHAR(500)",
            "production_spec": "JSON NOT NULL DEFAULT '{}'",
            "progress_detail": "JSON NOT NULL DEFAULT '{}'",
            "result_report": "JSON NOT NULL DEFAULT '{}'",
        }
        for name, definition in additive_columns.items():
            if name not in request_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE generative_media_requests "
                        f"ADD COLUMN {name} {definition}"
                    )
                )


def _ensure_generation_queue_indexes() -> None:
    if not settings.resolved_database_url.startswith("sqlite:///"):
        return
    with engine.begin() as connection:
        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_generation_requests_assigned_node "
            "ON generative_media_requests (assigned_node_id)",
            "CREATE INDEX IF NOT EXISTS ix_generation_requests_lease_expires "
            "ON generative_media_requests (lease_expires_at)",
            "CREATE INDEX IF NOT EXISTS ix_generation_requests_queued_at "
            "ON generative_media_requests (queued_at)",
        ):
            connection.execute(text(statement))
