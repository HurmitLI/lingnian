"""Dedicated source-reference node leases, separate from video rendering."""
from alembic import op
import sqlalchemy as sa

revision = "d8b01e34c5af"
down_revision = "c7a9f01234ab"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("short_scene_reference_jobs") as batch:
        batch.add_column(sa.Column("assigned_node_id", sa.String(36)))
        batch.add_column(sa.Column("lease_token_hash", sa.String(64)))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime()))
        batch.add_column(sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("result_asset_id", sa.String(36)))
        batch.create_foreign_key("fk_reference_node", "generation_nodes", ["assigned_node_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_reference_result", "media_assets", ["result_asset_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_short_scene_reference_jobs_assigned_node_id", ["assigned_node_id"])


def downgrade():
    with op.batch_alter_table("short_scene_reference_jobs") as batch:
        batch.drop_index("ix_short_scene_reference_jobs_assigned_node_id")
        batch.drop_constraint("fk_reference_result", type_="foreignkey")
        batch.drop_constraint("fk_reference_node", type_="foreignkey")
        for name in ("result_asset_id", "progress_percent", "lease_expires_at", "lease_token_hash", "assigned_node_id"):
            batch.drop_column(name)
