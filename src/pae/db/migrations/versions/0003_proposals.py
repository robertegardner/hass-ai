"""proposals + shadow_results (Phase 3)

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("group_key", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("automation_json", postgresql.JSONB, nullable=False),
        sa.Column("source_pattern_keys", postgresql.JSONB, nullable=False),
        sa.Column("entity_ids", postgresql.JSONB, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="shadowing"),
        sa.Column("reject_reason", sa.Text),
        sa.Column("last_eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ux_proposals_group_key", "proposals", ["group_key"], unique=True)
    op.create_index("ix_proposals_status", "proposals", ["status"])
    op.create_table(
        "shadow_results",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.BigInteger,
            sa.ForeignKey("proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("expected_fires", sa.BigInteger, nullable=False),
        sa.Column("human_matches", sa.BigInteger, nullable=False),
        sa.Column("human_total", sa.BigInteger, nullable=False),
    )
    op.create_index(
        "ux_shadow_proposal_day", "shadow_results", ["proposal_id", "day"], unique=True
    )


def downgrade() -> None:
    op.drop_table("shadow_results")
    op.drop_table("proposals")
