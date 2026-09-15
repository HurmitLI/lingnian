"""Bind a video task to a family-confirmed reference and original audio."""
from alembic import op
import sqlalchemy as sa
revision = "f3a090710001"
down_revision = "e902c14b8ad3"
branch_labels = None
depends_on = None

def upgrade():
    for table in ("short_scene_jobs", "short_scene_reference_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("claim_request_key", sa.String(80), nullable=True))
            batch.create_unique_constraint("uq_" + table + "_claim", ["claim_request_key"])
    with op.batch_alter_table("short_scene_jobs") as batch:
        batch.add_column(sa.Column("input_review", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("reference_review_sha256", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_short_job_reference_review", ["reference_review_sha256"])

def downgrade():
    for table in ("short_scene_jobs", "short_scene_reference_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint("uq_" + table + "_claim", type_="unique")
            batch.drop_column("claim_request_key")
    with op.batch_alter_table("short_scene_jobs") as batch:
        batch.drop_constraint("uq_short_job_reference_review", type_="unique")
        batch.drop_column("reference_review_sha256")
        batch.drop_column("input_review")
