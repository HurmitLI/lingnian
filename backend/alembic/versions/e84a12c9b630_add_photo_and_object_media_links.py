"""add photo and object media links

Revision ID: e84a12c9b630
Revises: d723bf91e50c
Create Date: 2026-08-29 23:35:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e84a12c9b630"
down_revision: str | Sequence[str] | None = "d723bf91e50c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("media_asset_id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("trigger_kind", sa.String(length=24), nullable=False),
        sa.Column("user_annotation", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("model_inference", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_asset_id"),
    )


def downgrade() -> None:
    op.drop_table("media_links")
