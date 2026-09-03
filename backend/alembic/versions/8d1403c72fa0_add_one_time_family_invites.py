"""add one time family invites

Revision ID: 8d1403c72fa0
Revises: 49a3dc8f1e72
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "8d1403c72fa0"
down_revision: str | Sequence[str] | None = "49a3dc8f1e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "family_invites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_family_invites_code_hash", "family_invites", ["code_hash"], unique=True)
    op.create_index("ix_family_invites_created_by_user_id", "family_invites", ["created_by_user_id"])
    op.create_index("ix_family_invites_expires_at", "family_invites", ["expires_at"])
    op.create_index("ix_family_invites_family_id", "family_invites", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_family_invites_family_id", table_name="family_invites")
    op.drop_index("ix_family_invites_expires_at", table_name="family_invites")
    op.drop_index("ix_family_invites_created_by_user_id", table_name="family_invites")
    op.drop_index("ix_family_invites_code_hash", table_name="family_invites")
    op.drop_table("family_invites")
