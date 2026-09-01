"""add lingnian memory experience

Revision ID: a13f8c7d2e90
Revises: f72c9e14a601
Create Date: 2026-09-02 01:10:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a13f8c7d2e90"
down_revision: str | Sequence[str] | None = "f72c9e14a601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "story_details",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("place_name", sa.String(length=160), nullable=True),
        sa.Column("event_year", sa.Integer(), nullable=True),
        sa.Column("theme_tags", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id"),
    )
    op.create_table(
        "story_contributions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("contributor_person_id", sa.String(length=36), nullable=True),
        sa.Column("contributor_label", sa.String(length=80), nullable=False),
        sa.Column("contribution_type", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contributor_person_id"], ["people.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "legacy_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("successor_person_ids", sa.JSON(), nullable=False),
        sa.Column("access_policy", sa.String(length=40), nullable=False),
        sa.Column("steward_label", sa.String(length=80), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["family_id"], ["family_archives.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("family_id"),
    )
    op.create_table(
        "media_person_tags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("media_asset_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("tagged_by", sa.String(length=80), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["family_id"], ["family_archives.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_asset_id", "person_id", name="uq_media_person_tag"),
    )
    op.create_table(
        "generative_media_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("elder_id", sa.String(length=36), nullable=False),
        sa.Column("story_id", sa.String(length=36), nullable=True),
        sa.Column("generation_type", sa.String(length=40), nullable=False),
        sa.Column("provider_key", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_label", sa.String(length=80), nullable=False),
        sa.Column("subject_consent", sa.Boolean(), nullable=False),
        sa.Column("rights_confirmed", sa.Boolean(), nullable=False),
        sa.Column("no_impersonation", sa.Boolean(), nullable=False),
        sa.Column("allow_external_upload", sa.Boolean(), nullable=False),
        sa.Column("estimated_cost_cents", sa.Integer(), nullable=False),
        sa.Column("max_cost_cents", sa.Integer(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["elder_id"], ["elder_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("generative_media_requests")
    op.drop_table("media_person_tags")
    op.drop_table("legacy_plans")
    op.drop_table("story_contributions")
    op.drop_table("story_details")
