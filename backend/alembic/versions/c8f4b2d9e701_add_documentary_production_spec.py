"""add documentary production specification

Revision ID: c8f4b2d9e701
Revises: 34d6a9f1042b
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8f4b2d9e701"
down_revision: str | Sequence[str] | None = "34d6a9f1042b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generative_media_requests") as batch:
        batch.add_column(
            sa.Column(
                "production_spec",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column("progress_detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.add_column(
            sa.Column("result_report", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("generative_media_requests") as batch:
        batch.drop_column("result_report")
        batch.drop_column("progress_detail")
        batch.drop_column("production_spec")
