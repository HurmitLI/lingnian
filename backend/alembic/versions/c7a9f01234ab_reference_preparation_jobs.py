"""Separate encrypted reference preparation intents; no GPU worker dispatch yet."""
from alembic import op
import sqlalchemy as sa

revision = "c7a9f01234ab"
down_revision = "b6011f2c307a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("short_scene_reference_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        *[sa.Column(name, sa.String(36), sa.ForeignKey(target, ondelete=action), nullable=False)
          for name, target, action in (
              ("family_id", "family_archives.id", "CASCADE"),
              ("session_id", "memory_sessions.id", "CASCADE"),
              ("plan_id", "short_scene_plans.id", "CASCADE"),
              ("package_asset_id", "media_assets.id", "RESTRICT"),
              ("consent_id", "model_consent_events.id", "RESTRICT"))],
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("brief_sha256", sa.String(64), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("family_id", "idempotency_key", name="uq_reference_job_key"),
        sa.UniqueConstraint("plan_id", "brief_sha256", name="uq_reference_job_brief"))
    op.create_index("ix_short_scene_reference_jobs_status", "short_scene_reference_jobs", ["status"])


def downgrade():
    op.drop_index("ix_short_scene_reference_jobs_status", table_name="short_scene_reference_jobs")
    op.drop_table("short_scene_reference_jobs")
