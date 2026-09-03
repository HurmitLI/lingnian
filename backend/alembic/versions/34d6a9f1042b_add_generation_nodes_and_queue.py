"""add generation nodes and worker queue fields

Revision ID: 34d6a9f1042b
Revises: b6d3e1a9c4f2
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "34d6a9f1042b"
down_revision: str | Sequence[str] | None = "b6d3e1a9c4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("software_version", sa.String(length=80), nullable=True),
        sa.Column("device_summary", sa.String(length=240), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_nodes_token_hash", "generation_nodes", ["token_hash"], unique=True)
    op.create_index("ix_generation_nodes_status", "generation_nodes", ["status"], unique=False)
    op.create_index("ix_generation_nodes_last_seen_at", "generation_nodes", ["last_seen_at"], unique=False)

    with op.batch_alter_table("generative_media_requests") as batch:
        batch.add_column(sa.Column("assigned_node_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("lease_token_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("progress_stage", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("queued_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_error_message", sa.String(length=500), nullable=True))
        batch.create_foreign_key(
            "fk_generation_request_node",
            "generation_nodes",
            ["assigned_node_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_generation_requests_assigned_node", ["assigned_node_id"], unique=False)
        batch.create_index("ix_generation_requests_lease_expires", ["lease_expires_at"], unique=False)
        batch.create_index("ix_generation_requests_queued_at", ["queued_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("generative_media_requests") as batch:
        batch.drop_index("ix_generation_requests_queued_at")
        batch.drop_index("ix_generation_requests_lease_expires")
        batch.drop_index("ix_generation_requests_assigned_node")
        batch.drop_constraint("fk_generation_request_node", type_="foreignkey")
        for name in (
            "last_error_message",
            "completed_at",
            "started_at",
            "queued_at",
            "progress_stage",
            "progress_percent",
            "attempt_count",
            "lease_expires_at",
            "lease_token_hash",
            "assigned_node_id",
        ):
            batch.drop_column(name)
    op.drop_index("ix_generation_nodes_last_seen_at", table_name="generation_nodes")
    op.drop_index("ix_generation_nodes_status", table_name="generation_nodes")
    op.drop_index("ix_generation_nodes_token_hash", table_name="generation_nodes")
    op.drop_table("generation_nodes")
