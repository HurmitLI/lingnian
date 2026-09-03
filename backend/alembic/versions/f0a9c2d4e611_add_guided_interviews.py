"""add guided interview sessions and turns

Revision ID: f0a9c2d4e611
Revises: e7c21a54d0b3
Create Date: 2026-09-03 17:20:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f0a9c2d4e611"
down_revision: str | Sequence[str] | None = "e7c21a54d0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_sessions") as batch_op:
        batch_op.add_column(sa.Column("narrator_person_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column(
                "interview_mode",
                sa.String(length=32),
                nullable=False,
                server_default="single",
            )
        )
        batch_op.create_foreign_key(
            "fk_memory_sessions_narrator_person_id_people",
            "people",
            ["narrator_person_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "interview_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("raw_answer_text", sa.Text(), nullable=False),
        sa.Column("corrected_answer_text", sa.Text(), nullable=False),
        sa.Column("answer_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("audio_asset_id", sa.String(length=36), nullable=True),
        sa.Column("asr_provider", sa.String(length=80), nullable=False),
        sa.Column("asr_model", sa.String(length=160), nullable=False),
        sa.Column("asr_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("followup_mode", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="answer_review"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["audio_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["memory_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audio_asset_id"),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_interview_turn_index"),
    )


def downgrade() -> None:
    op.drop_table("interview_turns")
    with op.batch_alter_table("memory_sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_memory_sessions_narrator_person_id_people", type_="foreignkey"
        )
        batch_op.drop_column("interview_mode")
        batch_op.drop_column("narrator_person_id")
