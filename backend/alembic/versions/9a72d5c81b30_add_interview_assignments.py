"""add interview assignments

Revision ID: 9a72d5c81b30
Revises: 8d1403c72fa0
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9a72d5c81b30"
down_revision: str | Sequence[str] | None = "8d1403c72fa0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_invite_id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("narrator_person_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_user_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("life_stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["family_invite_id"], ["family_invites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["narrator_person_id"], ["people.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["memory_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interview_assignments_assigned_user_id", "interview_assignments", ["assigned_user_id"])
    op.create_index("ix_interview_assignments_elder_id", "interview_assignments", ["elder_id"])
    op.create_index("ix_interview_assignments_family_id", "interview_assignments", ["family_id"])
    op.create_index("ix_interview_assignments_family_invite_id", "interview_assignments", ["family_invite_id"], unique=True)
    op.create_index("ix_interview_assignments_narrator_person_id", "interview_assignments", ["narrator_person_id"])
    op.create_index("ix_interview_assignments_session_id", "interview_assignments", ["session_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_interview_assignments_session_id", table_name="interview_assignments")
    op.drop_index("ix_interview_assignments_narrator_person_id", table_name="interview_assignments")
    op.drop_index("ix_interview_assignments_family_invite_id", table_name="interview_assignments")
    op.drop_index("ix_interview_assignments_family_id", table_name="interview_assignments")
    op.drop_index("ix_interview_assignments_elder_id", table_name="interview_assignments")
    op.drop_index("ix_interview_assignments_assigned_user_id", table_name="interview_assignments")
    op.drop_table("interview_assignments")
