"""add encrypted health notes

Revision ID: f72c9e14a601
Revises: c4a7b8d9e0f1
Create Date: 2026-08-31 04:55:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f72c9e14a601"
down_revision: str | Sequence[str] | None = "c4a7b8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "elder_profiles",
        sa.Column("health_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("elder_profiles", "health_notes")
