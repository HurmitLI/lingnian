"""add local keepsakes

Revision ID: c4a7b8d9e0f1
Revises: a9d53e7f4b21
Create Date: 2026-08-29 23:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4a7b8d9e0f1"
down_revision: Union[str, None] = "a9d53e7f4b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "keepsake_authorizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("actor_label", sa.String(length=80), nullable=False),
        sa.Column("story_ids", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("original_voice_authorized", sa.Boolean(), nullable=False),
        sa.Column("private_family_use", sa.Boolean(), nullable=False),
        sa.Column("no_impersonation", sa.Boolean(), nullable=False),
        sa.Column("original_audio_only", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "keepsakes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("authorization_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("story_manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=80), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("encryption_version", sa.Integer(), nullable=False),
        sa.Column("plaintext_size_bytes", sa.Integer(), nullable=True),
        sa.Column("plaintext_sha256", sa.String(length=64), nullable=True),
        sa.Column("renderer", sa.String(length=80), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("source_mode", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["authorization_id"], ["keepsake_authorizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("authorization_id"),
        sa.UniqueConstraint("elder_id", "idempotency_key", name="uq_elder_keepsake_idempotency"),
        sa.UniqueConstraint("elder_id", "version", name="uq_elder_keepsake_version"),
        sa.UniqueConstraint("relative_path"),
    )


def downgrade() -> None:
    op.drop_table("keepsakes")
    op.drop_table("keepsake_authorizations")
