"""add backup manifests

Revision ID: a9d53e7f4b21
Revises: f1b76c3a802e
Create Date: 2026-08-30 01:30:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a9d53e7f4b21"
down_revision: str | Sequence[str] | None = "f1b76c3a802e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=True),
        sa.Column("backup_version", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("database_sha256", sa.String(length=64), nullable=False),
        sa.Column("asset_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verification_summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )


def downgrade() -> None:
    op.drop_table("backup_manifests")
