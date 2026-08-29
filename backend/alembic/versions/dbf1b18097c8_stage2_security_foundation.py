"""stage2 security foundation

Revision ID: dbf1b18097c8
Revises: b3a1f44d9164
Create Date: 2026-08-29 19:16:01.882185
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "dbf1b18097c8"
down_revision: str | Sequence[str] | None = "b3a1f44d9164"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "archive_security",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("encryption_status", sa.String(length=40), nullable=False),
        sa.Column("initialized_at", sa.DateTime(), nullable=False),
        sa.Column("recovery_package_created_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("family_id"),
    )
    op.create_table(
        "model_consent_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("actor_label", sa.String(length=80), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("data_classification", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("one_time", sa.Boolean(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["memory_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "family_archives",
        sa.Column(
            "data_classification",
            sa.String(length=32),
            nullable=False,
            server_default="test",
        ),
    )
    op.add_column(
        "media_assets",
        sa.Column(
            "encryption_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("media_assets", sa.Column("integrity_checked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_assets", "integrity_checked_at")
    op.drop_column("media_assets", "encryption_version")
    op.drop_column("family_archives", "data_classification")
    op.drop_table("model_consent_events")
    op.drop_table("archive_security")
