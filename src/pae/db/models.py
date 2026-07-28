from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Event(Base):
    """One behavioral state change from HA. Hypertable partitioned on time."""

    __tablename__ = "events"

    # hypertables need the partition column in every unique index, so the
    # surrogate id is non-unique here; (id) alone is never used for lookups
    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    old_state: Mapped[str | None] = mapped_column(Text)
    new_state: Mapped[str | None] = mapped_column(Text)
    attrs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context_id: Mapped[str | None] = mapped_column(Text)
    context_parent_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)  # manual|automation|pae

    __table_args__ = (
        Index("ix_events_entity_time", "entity_id", "time"),
        Index("ix_events_domain_time", "domain", "time"),
        Index("ix_events_triggered_by_time", "triggered_by", "time"),
    )


class PresenceSnapshot(Base):
    """Presence observations: person.* zone changes (house granularity) and
    UniFi Protect person detections (room granularity, identity unknown)."""

    __tablename__ = "presence_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    person: Mapped[str] = mapped_column(Text, nullable=False)  # person object_id or 'unknown'
    room: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # person|unifi

    __table_args__ = (Index("ix_presence_person_time", "person", "time"),)


class ContextFrame(Base):
    """One row per minute: the household context in which events occur."""

    __tablename__ = "context_frames"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    persons_home: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sun_state: Mapped[str | None] = mapped_column(Text)
    day_type: Mapped[str | None] = mapped_column(Text)  # weekday|weekend|holiday|travel
    season: Mapped[str | None] = mapped_column(Text)
    outside_temp: Mapped[float | None] = mapped_column(Float)


class EntityRegistryEntry(Base):
    """Daily-refreshed mirror of the HA entity registry (names, areas, classes)."""

    __tablename__ = "entity_registry"

    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    friendly_name: Mapped[str | None] = mapped_column(Text)
    area_id: Mapped[str | None] = mapped_column(Text)
    area_name: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[str | None] = mapped_column(Text)
    device_class: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Pattern(Base):
    """A mined behavioral pattern (Phase 2). Upserted nightly by the miner,
    keyed on pattern_key; the status lifecycle belongs to later phases and is
    never overwritten by the miner."""

    __tablename__ = "patterns"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    pattern_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # time_of_day|event_pair
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    day_type: Mapped[str | None] = mapped_column(Text)
    trigger_entity_id: Mapped[str | None] = mapped_column(Text)
    trigger_state: Mapped[str | None] = mapped_column(Text)
    tod_minutes: Mapped[float | None] = mapped_column(Float)
    tod_std_minutes: Mapped[float | None] = mapped_column(Float)
    support: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    lift: Mapped[float] = mapped_column(Float, nullable=False)
    temporal_consistency: Mapped[float | None] = mapped_column(Float)
    occurrences: Mapped[int] = mapped_column(BigInteger, nullable=False)
    days_observed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    suspected_schedule: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="candidate")
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ux_patterns_key", "pattern_key", unique=True),
        Index("ix_patterns_kind", "kind"),
    )


class Proposal(Base):
    """An LLM-authored automation proposal (Phase 3). One row per group_key;
    lifecycle: shadowing -> approved | rejected (terminal) | stale."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    group_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # time_of_day|event_pair
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    automation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_pattern_keys: Mapped[list] = mapped_column(JSONB, nullable=False)
    entity_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="shadowing")
    reject_reason: Mapped[str | None] = mapped_column(Text)
    last_eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ux_proposals_group_key", "group_key", unique=True),
        Index("ix_proposals_status", "status"),
    )


class ShadowResult(Base):
    """Per-proposal per-local-day shadow scores. precision = human_matches /
    expected_fires; coverage = human_matches / human_total (see shadow service
    for the per-entity aggregation that makes both ratios stay in [0, 1])."""

    __tablename__ = "shadow_results"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    expected_fires: Mapped[int] = mapped_column(BigInteger, nullable=False)
    human_matches: Mapped[int] = mapped_column(BigInteger, nullable=False)
    human_total: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (Index("ux_shadow_proposal_day", "proposal_id", "day", unique=True),)
