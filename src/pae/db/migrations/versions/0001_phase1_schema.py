"""Phase 1 schema: events, presence_snapshots, context_frames hypertables;
entity_registry mirror; 12-month retention on events; events_hourly aggregate.

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("old_state", sa.Text),
        sa.Column("new_state", sa.Text),
        sa.Column("attrs", JSONB),
        sa.Column("context_id", sa.Text),
        sa.Column("context_parent_id", sa.Text),
        sa.Column("user_id", sa.Text),
        sa.Column("triggered_by", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", "time"),
    )
    op.create_index("ix_events_entity_time", "events", ["entity_id", "time"])
    op.create_index("ix_events_domain_time", "events", ["domain", "time"])
    op.create_index("ix_events_triggered_by_time", "events", ["triggered_by", "time"])
    op.execute("SELECT create_hypertable('events', 'time')")
    op.execute("SELECT add_retention_policy('events', INTERVAL '12 months')")

    op.create_table(
        "presence_snapshots",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("person", sa.Text, nullable=False),
        sa.Column("room", sa.Text),
        sa.Column("source", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", "time"),
    )
    op.create_index("ix_presence_person_time", "presence_snapshots", ["person", "time"])
    op.execute("SELECT create_hypertable('presence_snapshots', 'time')")

    op.create_table(
        "context_frames",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persons_home", JSONB),
        sa.Column("sun_state", sa.Text),
        sa.Column("day_type", sa.Text),
        sa.Column("season", sa.Text),
        sa.Column("outside_temp", sa.Float),
        sa.PrimaryKeyConstraint("time"),
    )
    op.execute("SELECT create_hypertable('context_frames', 'time')")

    op.create_table(
        "entity_registry",
        sa.Column("entity_id", sa.Text, primary_key=True),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("friendly_name", sa.Text),
        sa.Column("area_id", sa.Text),
        sa.Column("area_name", sa.Text),
        sa.Column("device_id", sa.Text),
        sa.Column("device_class", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # continuous aggregates cannot be created inside a transaction
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE MATERIALIZED VIEW events_hourly
            WITH (timescaledb.continuous) AS
            SELECT time_bucket('1 hour', time) AS bucket,
                   domain,
                   triggered_by,
                   count(*) AS n
            FROM events
            GROUP BY bucket, domain, triggered_by
            WITH NO DATA
            """
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy('events_hourly',
                start_offset => INTERVAL '3 hours',
                end_offset => INTERVAL '1 hour',
                schedule_interval => INTERVAL '30 minutes')
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS events_hourly")
    op.drop_table("entity_registry")
    op.drop_table("context_frames")
    op.drop_table("presence_snapshots")
    op.drop_table("events")
