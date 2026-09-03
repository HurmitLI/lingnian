"""add platform family invites

Revision ID: b6d3e1a9c4f2
Revises: 9a72d5c81b30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b6d3e1a9c4f2"
down_revision: str | Sequence[str] | None = "9a72d5c81b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_accounts",
        sa.Column("platform_role", sa.String(length=24), nullable=False, server_default="user"),
    )
    op.execute(
        "UPDATE user_accounts SET platform_role = 'admin' "
        "WHERE id = (SELECT id FROM user_accounts ORDER BY created_at ASC LIMIT 1)"
    )
    op.create_table(
        "platform_family_invites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_family_invites_code_hash",
        "platform_family_invites",
        ["code_hash"],
        unique=True,
    )
    op.create_index(
        "ix_platform_family_invites_created_by_user_id",
        "platform_family_invites",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_platform_family_invites_expires_at",
        "platform_family_invites",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_family_invites_expires_at",
        table_name="platform_family_invites",
    )
    op.drop_index(
        "ix_platform_family_invites_created_by_user_id",
        table_name="platform_family_invites",
    )
    op.drop_index(
        "ix_platform_family_invites_code_hash",
        table_name="platform_family_invites",
    )
    op.drop_table("platform_family_invites")
    op.drop_column("user_accounts", "platform_role")
