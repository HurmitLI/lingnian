"""Persist consent-bound short scene selections separately from GPU tasks."""
from alembic import op
import sqlalchemy as sa

revision = "0e931ab821c7"
down_revision = "c8f4b2d9e701"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "short_scene_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(36), sa.ForeignKey("family_archives.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("memory_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("consent_id", sa.String(36), sa.ForeignKey("model_consent_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("reference_mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_short_scene_key"),
        sa.UniqueConstraint("session_id", "input_sha256", name="uq_short_scene_input"),
    )


def downgrade():
    op.drop_table("short_scene_plans")
