"""add encrypted archive storage

Revision ID: f1b76c3a802e
Revises: e84a12c9b630
Create Date: 2026-08-30 00:30:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f1b76c3a802e"
down_revision: str | Sequence[str] | None = "e84a12c9b630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("archive_security", sa.Column("activated_at", sa.DateTime(), nullable=True))
    op.add_column(
        "archive_security", sa.Column("recovery_verified_at", sa.DateTime(), nullable=True)
    )
    op.add_column("media_assets", sa.Column("plaintext_size_bytes", sa.Integer(), nullable=True))
    op.add_column(
        "media_assets", sa.Column("plaintext_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "memory_books",
        sa.Column(
            "pdf_encryption_version", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "memory_books", sa.Column("pdf_ciphertext_size", sa.Integer(), nullable=True)
    )
    op.add_column(
        "memory_books",
        sa.Column("pdf_ciphertext_sha256", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "encrypted_fields",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("object_type", sa.String(length=60), nullable=False),
        sa.Column("object_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=80), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "family_id",
            "object_type",
            "object_id",
            "field_name",
            name="uq_encrypted_object_field",
        ),
    )


def downgrade() -> None:
    op.drop_table("encrypted_fields")
    op.drop_column("memory_books", "pdf_ciphertext_sha256")
    op.drop_column("memory_books", "pdf_ciphertext_size")
    op.drop_column("memory_books", "pdf_encryption_version")
    op.drop_column("media_assets", "plaintext_sha256")
    op.drop_column("media_assets", "plaintext_size_bytes")
    op.drop_column("archive_security", "recovery_verified_at")
    op.drop_column("archive_security", "activated_at")
