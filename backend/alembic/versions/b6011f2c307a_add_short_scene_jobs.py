"""Independent native ten-second queue; legacy renderer cannot claim these jobs."""
from alembic import op
import sqlalchemy as sa

revision = "b6011f2c307a"
down_revision = "0e931ab821c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("short_scene_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        *[sa.Column(name, sa.String(36), sa.ForeignKey(target, ondelete=action), nullable=nullable) for name, target, action, nullable in (
            ("family_id", "family_archives.id", "CASCADE", False),
            ("session_id", "memory_sessions.id", "CASCADE", False),
            ("plan_id", "short_scene_plans.id", "CASCADE", False),
            ("package_asset_id", "media_assets.id", "RESTRICT", False),
            ("result_asset_id", "media_assets.id", "SET NULL", True),
            ("consent_id", "model_consent_events.id", "RESTRICT", False),
            ("assigned_node_id", "generation_nodes.id", "SET NULL", True))],
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("lease_token_hash", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("family_id", "idempotency_key", name="uq_short_job_key"))
    op.create_index("ix_short_scene_jobs_status", "short_scene_jobs", ["status"])


def downgrade():
    op.drop_index("ix_short_scene_jobs_status", table_name="short_scene_jobs")
    op.drop_table("short_scene_jobs")
