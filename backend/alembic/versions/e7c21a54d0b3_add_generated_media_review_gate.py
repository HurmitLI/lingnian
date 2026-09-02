"""add generated media review gate

Revision ID: e7c21a54d0b3
Revises: a13f8c7d2e90
Create Date: 2026-09-02 17:05:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7c21a54d0b3"
down_revision: str | Sequence[str] | None = "a13f8c7d2e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generative_media_requests") as batch_op:
        batch_op.add_column(sa.Column("result_asset_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("actual_cost_cents", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("review_checks", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(sa.Column("reviewed_by", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("review_notes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_generative_media_requests_result_asset_id_media_assets",
            "media_assets",
            ["result_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("generative_media_requests") as batch_op:
        batch_op.drop_constraint(
            "fk_generative_media_requests_result_asset_id_media_assets",
            type_="foreignkey",
        )
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("review_notes")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_checks")
        batch_op.drop_column("actual_cost_cents")
        batch_op.drop_column("result_asset_id")
