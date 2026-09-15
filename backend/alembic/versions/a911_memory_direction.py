"""Persist native-memory direction with existing family field encryption."""
from alembic import op
import sqlalchemy as sa
revision = 'a911_memory_direction'
down_revision = 'f3a090710001'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('generative_media_requests') as batch:
        batch.add_column(sa.Column('idempotency_key', sa.String(100), nullable=True))
        batch.create_unique_constraint('uq_generative_request_key', ['idempotency_key'])
        batch.add_column(sa.Column('production_direction', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

def downgrade():
    with op.batch_alter_table('generative_media_requests') as batch:
        batch.drop_column('production_direction')
        batch.drop_constraint('uq_generative_request_key', type_='unique')
        batch.drop_column('idempotency_key')
