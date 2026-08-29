"""add memory book pdf metadata

Revision ID: d723bf91e50c
Revises: c61e09b4a2d7
Create Date: 2026-08-29 23:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d723bf91e50c"
down_revision: str | Sequence[str] | None = "c61e09b4a2d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_books",
        sa.Column(
            "pdf_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_generated",
        ),
    )
    op.add_column(
        "memory_books",
        sa.Column("pdf_relative_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "memory_books",
        sa.Column("pdf_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_books", "pdf_sha256")
    op.drop_column("memory_books", "pdf_relative_path")
    op.drop_column("memory_books", "pdf_status")
