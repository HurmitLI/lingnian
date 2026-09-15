"""Bind reference results to the exact authorized brief and node report."""
from alembic import op
import sqlalchemy as sa

revision = "e902c14b8ad3"
down_revision = "d8b01e34c5af"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("short_scene_reference_jobs") as batch:
        batch.add_column(sa.Column("result_report", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("short_scene_reference_jobs") as batch:
        batch.drop_column("result_report")
