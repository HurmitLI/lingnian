"""link model consent to workflow tasks

Revision ID: 2cb78dd8f82a
Revises: dbf1b18097c8
Create Date: 2026-08-29 20:40:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2cb78dd8f82a"
down_revision: str | Sequence[str] | None = "dbf1b18097c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("model_consent_event_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_workflow_tasks_model_consent_event",
            "model_consent_events",
            ["model_consent_event_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_tasks") as batch_op:
        batch_op.drop_constraint(
            "fk_workflow_tasks_model_consent_event", type_="foreignkey"
        )
        batch_op.drop_column("model_consent_event_id")
