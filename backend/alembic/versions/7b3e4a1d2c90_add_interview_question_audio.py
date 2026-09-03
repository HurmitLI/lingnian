"""add interview question audio links

Revision ID: 7b3e4a1d2c90
Revises: f0a9c2d4e611
Create Date: 2026-09-03 18:10:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "7b3e4a1d2c90"
down_revision: str | Sequence[str] | None = "f0a9c2d4e611"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interview_turns") as batch_op:
        batch_op.add_column(
            sa.Column("question_audio_asset_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_interview_turns_question_audio_asset_id_media_assets",
            "media_assets",
            ["question_audio_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_interview_turns_question_audio_asset_id",
            ["question_audio_asset_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("interview_turns") as batch_op:
        batch_op.drop_constraint(
            "uq_interview_turns_question_audio_asset_id", type_="unique"
        )
        batch_op.drop_constraint(
            "fk_interview_turns_question_audio_asset_id_media_assets",
            type_="foreignkey",
        )
        batch_op.drop_column("question_audio_asset_id")
