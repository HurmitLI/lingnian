"""add reminders and memory books

Revision ID: c61e09b4a2d7
Revises: 8ae39cd14f6e
Create Date: 2026-08-29 22:20:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c61e09b4a2d7"
down_revision: str | Sequence[str] | None = "8ae39cd14f6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("topic_key", sa.String(length=80), nullable=False),
        sa.Column("remind_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("last_shown_at", sa.DateTime(), nullable=True),
        sa.Column("show_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "memory_books",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("markdown_content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("story_manifest", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("elder_id", "version", name="uq_elder_memory_book_version"),
    )


def downgrade() -> None:
    op.drop_table("memory_books")
    op.drop_table("reminders")
