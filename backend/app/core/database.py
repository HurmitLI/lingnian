from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, update
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
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.execute(
            update(WorkflowTask)
            .where(WorkflowTask.status.in_(["queued", "running"]))
            .values(status="failed_retryable", error_code="PROCESS_INTERRUPTED")
        )
        db.commit()

