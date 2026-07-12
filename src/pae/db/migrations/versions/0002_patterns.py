"""Phase 2 schema: patterns table (regular table, not a hypertable).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patterns",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("pattern_key", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("day_type", sa.Text),
        sa.Column("trigger_entity_id", sa.Text),
        sa.Column("trigger_state", sa.Text),
        sa.Column("tod_minutes", sa.Float),
        sa.Column("tod_std_minutes", sa.Float),
        sa.Column("support", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("lift", sa.Float, nullable=False),
        sa.Column("temporal_consistency", sa.Float),
        sa.Column("occurrences", sa.BigInteger, nullable=False),
        sa.Column("days_observed", sa.BigInteger, nullable=False),
        sa.Column(
            "suspected_schedule", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
        sa.Column("evidence", JSONB),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mined_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ux_patterns_key", "patterns", ["pattern_key"], unique=True)
    op.create_index("ix_patterns_kind", "patterns", ["kind"])


def downgrade() -> None:
    op.drop_table("patterns")
