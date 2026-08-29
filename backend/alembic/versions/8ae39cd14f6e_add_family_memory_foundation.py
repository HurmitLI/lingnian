"""add family memory foundation

Revision ID: 8ae39cd14f6e
Revises: 2cb78dd8f82a
Create Date: 2026-08-29 21:30:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "8ae39cd14f6e"
down_revision: str | Sequence[str] | None = "2cb78dd8f82a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_prompts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("prompt_key", sa.String(length=100), nullable=False),
        sa.Column("life_stage", sa.String(length=40), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("sensitivity", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_key"),
    )
    op.create_table(
        "person_relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("from_person_id", sa.String(length=36), nullable=False),
        sa.Column("to_person_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=40), nullable=False),
        sa.Column("custom_label", sa.String(length=80), nullable=True),
        sa.Column("confirmed_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["family_archives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_person_id"], ["people.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_person_id",
            "to_person_id",
            "relationship_type",
            name="uq_person_relationship_direction",
        ),
    )
    op.create_table(
        "topic_preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("topic_key", sa.String(length=80), nullable=False),
        sa.Column("preference", sa.String(length=24), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("elder_id", "topic_key", name="uq_elder_topic_preference"),
    )
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("fact_type", sa.String(length=40), nullable=False),
        sa.Column("subject_label", sa.String(length=120), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "fact_type", name="uq_story_memory_fact_type"),
    )


def downgrade() -> None:
    op.drop_table("memory_facts")
    op.drop_table("topic_preferences")
    op.drop_table("person_relationships")
    op.drop_table("question_prompts")
