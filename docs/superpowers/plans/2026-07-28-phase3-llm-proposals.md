# PAE Phase 3 — LLM Proposals + Shadow Evaluation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn mined behavioral patterns into LLM-authored, schema-validated Home Assistant automation proposals, score them nightly against real events (shadow evaluation), and review them in a web UI — with zero writes to Home Assistant.

**Architecture:** Deterministic code gates and groups patterns; one Ollama call per group authors automation JSON under constrained decoding; pydantic + registry validation rejects anything unsafe; a nightly RQ chain (mine → propose → shadow-eval) keeps it hands-off; server-rendered FastAPI pages are the review surface. Spec: `docs/superpowers/specs/2026-07-28-phase3-llm-proposals-design.md`.

**Tech Stack:** Python 3.13, SQLAlchemy/Alembic (Postgres/Timescale), pydantic v2, stdlib `urllib` for the Ollama REST calls (sync, runs in the RQ worker), FastAPI + Jinja2 + PyYAML for the UI, pytest.

## Global Constraints

- **Zero HA writes.** Nothing in this plan touches `src/pae/ha/client.py`; `ALLOWED_OUTBOUND_TYPES` must be bit-identical at the end.
- **Ollama-only, no cloud.** The LLM client must raise `ValueError` for any model name containing `:cloud`.
- **The miner never touches `patterns.status`** — only Phase 3 code (proposer service, UI actions) may.
- Line length 100 (`uv run ruff check src tests scripts` must pass after every task).
- Tests offline by default; anything needing live services is `@pytest.mark.live`.
- Test imports come from `conftest`, never `tests.conftest`.
- TDD per task: write the failing test, watch it fail, implement, watch it pass, commit.
- All new thresholds are `Settings` fields (env-overridable), defaults exactly as the spec states.
- Commit messages: conventional style, trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure

```
src/pae/db/migrations/versions/0003_proposals.py   new — proposals + shadow_results
src/pae/db/models.py                               modify — Proposal, ShadowResult ORM
src/pae/config.py                                  modify — llm_*/proposer_*/shadow_* settings
src/pae/llm/__init__.py                            new (empty)
src/pae/llm/client.py                              new — sync Ollama client, failover, :cloud guard
src/pae/llm/prompt.py                              new — prompt v1 + response JSON schema
src/pae/proposer/__init__.py                       new (empty)
src/pae/proposer/gates.py                          new — eligibility gates + sched-sibling set
src/pae/proposer/grouping.py                       new — ProposalGroup + group_key
src/pae/proposer/schema.py                         new — automation subset models + validation
src/pae/proposer/service.py                        new — run_proposing orchestration
src/pae/proposer/job.py                            new — propose_job RQ entry
src/pae/proposer/repo.py                           new — UI-facing SQL (list/get/set_status)
src/pae/shadow/__init__.py                         new (empty)
src/pae/shadow/service.py                          new — interpreter + run_shadow_eval
src/pae/shadow/job.py                              new — shadow_eval_job RQ entry
src/pae/api/app.py                                 modify — include proposals router
src/pae/api/proposals.py                           new — routes
src/pae/api/viz.py                                 new — inline-SVG sparkbars
src/pae/api/templates/{base,proposals_list,proposal_detail}.html  new
src/pae/worker/scheduler.py                        modify — mine→propose→shadow chain
src/pae/metrics.py                                 modify — LLM/proposer/shadow metrics
src/pae/cli.py                                     modify — `pae propose`, `pae shadow`
tests/proposer/{test_gates,test_grouping,test_schema,test_service,test_repo_stub}.py
tests/llm/{test_client,test_prompt}.py
tests/shadow/test_service.py
tests/api/test_proposals_ui.py
tests/miner/test_scheduler.py                      modify — chain test
tests/test_config.py                               modify — new defaults
```

Shared test helper: `tests/proposer/conftest.py` defines `make_pattern(**kw)` returning a
`pae.db.models.Pattern` ORM instance (no DB needed) with sensible defaults; several test
files use it.

---

### Task 1: Migration 0003 + ORM models

**Files:**
- Create: `src/pae/db/migrations/versions/0003_proposals.py`
- Modify: `src/pae/db/models.py` (append)
- Test: `tests/proposer/__init__.py` (empty), `tests/proposer/test_models.py`

**Interfaces:**
- Produces: `Proposal` and `ShadowResult` ORM classes; statuses are plain text
  (`shadowing|approved|rejected|stale`).

- [ ] **Step 1: Write the failing test**

`tests/proposer/__init__.py`: empty file. `tests/proposer/test_models.py`:

```python
from datetime import UTC, date, datetime

from pae.db.models import Proposal, ShadowResult


def test_proposal_model_fields():
    now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
    p = Proposal(
        group_key="tod:weekday:14:abc123def456",
        kind="time_of_day",
        title="Morning lights",
        rationale="You turn these on together every weekday morning.",
        automation_json={"trigger": [], "condition": [], "action": []},
        source_pattern_keys=["tod:switch.a:on:weekday:14"],
        entity_ids=["switch.a"],
        model_name="test-model",
        prompt_version=1,
        status="shadowing",
        last_eligible_at=now,
        created_at=now,
        updated_at=now,
    )
    assert p.status == "shadowing"
    assert p.reject_reason is None


def test_shadow_result_model_fields():
    r = ShadowResult(
        proposal_id=1, day=date(2026, 7, 27), expected_fires=1, human_matches=1, human_total=2
    )
    assert r.human_total == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/proposer/test_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'Proposal'`

- [ ] **Step 3: Append models to `src/pae/db/models.py`**

```python
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
```

Add to the imports at the top of `models.py`: `from datetime import date, datetime` (replace
the existing `from datetime import datetime`) and `Date` in the `sqlalchemy` import list.

- [ ] **Step 4: Create `src/pae/db/migrations/versions/0003_proposals.py`**

```python
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
```

- [ ] **Step 5: Run tests + lint, verify pass**

Run: `uv run pytest tests/proposer/test_models.py -q && uv run ruff check src tests scripts`
Expected: PASS / clean

- [ ] **Step 6: Commit**

```bash
git add src/pae/db/models.py src/pae/db/migrations/versions/0003_proposals.py tests/proposer/
git commit -m "feat(proposer): proposals + shadow_results schema (migration 0003)"
```

---

### Task 2: Settings

**Files:**
- Modify: `src/pae/config.py` (after the miner block)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: settings names used verbatim by later tasks (see code below).

- [ ] **Step 1: Add failing assertions to `tests/test_config.py::test_miner_defaults`** (append a new test)

```python
def test_phase3_defaults():
    from pae.config import Settings

    s = Settings(_env_file=None)
    assert s.llm_model_primary == "alibayram/Qwen3-30B-A3B-Instruct-2507:latest"
    assert s.llm_model_fallback == "qwen3.5:9b"
    assert s.llm_timeout_seconds == 120.0
    assert s.proposer_tod_min_consistency == 0.8
    assert s.proposer_tod_max_std_minutes == 30.0
    assert s.proposer_tod_min_support == 0.6
    assert s.proposer_tod_min_days == 14
    assert s.proposer_min_occurrences == 8
    assert s.proposer_pair_min_confidence == 0.7
    assert s.proposer_pair_min_lift == 5.0
    assert s.proposer_group_window_minutes == 20.0
    assert s.proposer_stale_days == 7
    assert s.shadow_tolerance_minutes == 45.0
    assert s.shadow_lookback_days == 30
    assert s.shadow_ready_days == 14
    assert s.shadow_ready_precision == 0.8
    assert s.shadow_ready_coverage == 0.8
```

- [ ] **Step 2: Run, expect AttributeError.** `uv run pytest tests/test_config.py -q`

- [ ] **Step 3: Add to `src/pae/config.py`** (after `miner_pair_min_lift`):

```python
    # LLM + proposer + shadow (Phase 3) — ollama_primary/ollama_fallback URLs above
    llm_model_primary: str = "alibayram/Qwen3-30B-A3B-Instruct-2507:latest"
    llm_model_fallback: str = "qwen3.5:9b"
    llm_timeout_seconds: float = 120.0
    proposer_tod_min_consistency: float = 0.8
    proposer_tod_max_std_minutes: float = 30.0
    proposer_tod_min_support: float = 0.6
    proposer_tod_min_days: int = 14
    proposer_min_occurrences: int = 8
    proposer_pair_min_confidence: float = 0.7
    proposer_pair_min_lift: float = 5.0
    proposer_group_window_minutes: float = 20.0
    proposer_stale_days: int = 7
    shadow_tolerance_minutes: float = 45.0
    shadow_lookback_days: int = 30
    shadow_ready_days: int = 14
    shadow_ready_precision: float = 0.8
    shadow_ready_coverage: float = 0.8
```

- [ ] **Step 4: Verify pass + lint; commit**

```bash
git add src/pae/config.py tests/test_config.py
git commit -m "feat(config): Phase 3 llm/proposer/shadow settings"
```

---

### Task 3: Eligibility gates

**Files:**
- Create: `src/pae/proposer/__init__.py` (empty), `src/pae/proposer/gates.py`
- Test: `tests/proposer/conftest.py`, `tests/proposer/test_gates.py`

**Interfaces:**
- Consumes: `pae.db.models.Pattern` attribute names (kind, entity_id, trigger_entity_id,
  suspected_schedule, temporal_consistency, tod_std_minutes, support, occurrences,
  days_observed, confidence, lift, status).
- Produces:
  - `sched_flagged_entities(patterns: Sequence) -> set[str]`
  - `eligible_patterns(patterns, *, registry_ids, tod_min_consistency=0.8,
    tod_max_std_minutes=30.0, tod_min_support=0.6, tod_min_days=14, min_occurrences=8,
    pair_min_confidence=0.7, pair_min_lift=5.0) -> list` (same objects filtered; patterns
    with `status == "rejected"` are excluded)

- [ ] **Step 1: `tests/proposer/conftest.py`**

```python
from datetime import UTC, datetime

from pae.db.models import Pattern

NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)


def make_pattern(**kw) -> Pattern:
    defaults = dict(
        pattern_key="tod:light.bar:on:weekday:14",
        kind="time_of_day",
        entity_id="light.bar",
        action="on",
        day_type="weekday",
        trigger_entity_id=None,
        trigger_state=None,
        tod_minutes=432.0,
        tod_std_minutes=8.0,
        support=0.9,
        confidence=0.9,
        lift=20.0,
        temporal_consistency=0.95,
        occurrences=15,
        days_observed=16,
        suspected_schedule=False,
        status="candidate",
        evidence={},
        first_seen=NOW,
        last_seen=NOW,
        mined_at=NOW,
    )
    defaults.update(kw)
    return Pattern(**defaults)
```

- [ ] **Step 2: `tests/proposer/test_gates.py` (write, watch fail)**

```python
from conftest import make_pattern  # noqa: F401 — repo rule: never tests.conftest
from pae.proposer.gates import eligible_patterns, sched_flagged_entities

REG = {"light.bar", "switch.a", "switch.b", "binary_sensor.door"}


def elig(pats, **kw):
    return eligible_patterns(pats, registry_ids=REG, **kw)


def test_good_tod_pattern_is_eligible():
    assert elig([make_pattern()]) != []


def test_sched_flagged_excluded():
    assert elig([make_pattern(suspected_schedule=True)]) == []


def test_sched_sibling_excluded_across_kinds():
    flagged = make_pattern(
        pattern_key="tod:switch.a:on:weekday:40", entity_id="switch.a", suspected_schedule=True
    )
    pair = make_pattern(
        pattern_key="pair:binary_sensor.door:on->switch.a:on",
        kind="event_pair",
        entity_id="switch.a",
        trigger_entity_id="binary_sensor.door",
        trigger_state="on",
        confidence=0.9,
        lift=10.0,
    )
    assert elig([flagged, pair]) == []
    assert sched_flagged_entities([flagged, pair]) == {"switch.a"}


def test_pair_with_flagged_trigger_entity_excluded():
    flagged = make_pattern(
        pattern_key="tod:binary_sensor.door:on:weekday:40",
        entity_id="binary_sensor.door",
        suspected_schedule=True,
    )
    pair = make_pattern(
        pattern_key="pair:binary_sensor.door:on->light.bar:on",
        kind="event_pair",
        trigger_entity_id="binary_sensor.door",
        trigger_state="on",
        confidence=0.9,
        lift=10.0,
    )
    assert elig([flagged, pair]) == []


def test_threshold_gates():
    assert elig([make_pattern(temporal_consistency=0.7)]) == []
    assert elig([make_pattern(tod_std_minutes=31.0)]) == []
    assert elig([make_pattern(support=0.5)]) == []
    assert elig([make_pattern(occurrences=7)]) == []
    assert elig([make_pattern(days_observed=13)]) == []
    pair_kw = dict(
        kind="event_pair", trigger_entity_id="binary_sensor.door", trigger_state="on"
    )
    assert elig([make_pattern(**pair_kw, confidence=0.6, lift=10.0)]) == []
    assert elig([make_pattern(**pair_kw, confidence=0.9, lift=4.0)]) == []
    assert elig([make_pattern(**pair_kw, confidence=0.9, lift=10.0)]) != []


def test_unknown_entity_and_rejected_status_excluded():
    assert elig([make_pattern(entity_id="light.gone")]) == []
    assert elig([make_pattern(status="rejected")]) == []
```

Run: `uv run pytest tests/proposer/test_gates.py -q` → ImportError.

- [ ] **Step 3: Implement `src/pae/proposer/gates.py`**

```python
"""Deterministic eligibility gates: what the LLM is allowed to see.

Precision-biased: any doubt excludes the pattern. The sched-sibling rule
covers both kinds — an entity with ANY suspected_schedule time_of_day
pattern is off-limits everywhere (CLAUDE.md miner invariant)."""
from collections.abc import Sequence


def sched_flagged_entities(patterns: Sequence) -> set[str]:
    return {
        p.entity_id
        for p in patterns
        if p.kind == "time_of_day" and p.suspected_schedule
    }


def eligible_patterns(
    patterns: Sequence,
    *,
    registry_ids: set[str],
    tod_min_consistency: float = 0.8,
    tod_max_std_minutes: float = 30.0,
    tod_min_support: float = 0.6,
    tod_min_days: int = 14,
    min_occurrences: int = 8,
    pair_min_confidence: float = 0.7,
    pair_min_lift: float = 5.0,
) -> list:
    flagged = sched_flagged_entities(patterns)
    out = []
    for p in patterns:
        if p.suspected_schedule or p.status == "rejected":
            continue
        if p.entity_id in flagged or p.entity_id not in registry_ids:
            continue
        if p.occurrences < min_occurrences:
            continue
        if p.kind == "time_of_day":
            if p.temporal_consistency is None or p.temporal_consistency < tod_min_consistency:
                continue
            if p.tod_std_minutes is None or p.tod_std_minutes > tod_max_std_minutes:
                continue
            if p.support < tod_min_support or p.days_observed < tod_min_days:
                continue
        elif p.kind == "event_pair":
            if p.trigger_entity_id in flagged or p.trigger_entity_id not in registry_ids:
                continue
            if p.confidence < pair_min_confidence or p.lift < pair_min_lift:
                continue
        else:
            continue
        out.append(p)
    return out
```

- [ ] **Step 4: Verify pass + lint; commit**

```bash
git add src/pae/proposer/ tests/proposer/
git commit -m "feat(proposer): deterministic eligibility gates"
```

---

### Task 4: Grouping + group_key

**Files:**
- Create: `src/pae/proposer/grouping.py`
- Test: `tests/proposer/test_grouping.py`

**Interfaces:**
- Consumes: eligible `Pattern` objects (Task 3), `circular_mean`/`circular_diff` from
  `pae.miner.stats`.
- Produces:
  - `@dataclass ProposalGroup: kind, day_type (str|None), group_key (str),
    patterns (list), mean_minutes (float|None), trigger_entity_id (str|None),
    trigger_state (str|None)` with property `entity_actions -> list[tuple[str, str]]`
    (sorted, deduped) and property `entity_ids -> list[str]` (sorted, deduped).
  - `build_groups(eligible: Sequence, *, window_minutes: float = 20.0)
    -> list[ProposalGroup]`

- [ ] **Step 1: `tests/proposer/test_grouping.py` (write, watch fail)**

```python
from conftest import make_pattern
from pae.proposer.grouping import build_groups


def tod(entity, minutes, day_type="weekday", action="on"):
    return make_pattern(
        pattern_key=f"tod:{entity}:{action}:{day_type}:{int(minutes // 30):02d}",
        entity_id=entity,
        action=action,
        day_type=day_type,
        tod_minutes=minutes,
    )


def test_close_tod_patterns_group_together():
    groups = build_groups([tod("switch.a", 432.0), tod("switch.b", 440.0)])
    (g,) = groups
    assert g.kind == "time_of_day"
    assert g.entity_actions == [("switch.a", "on"), ("switch.b", "on")]
    assert 432.0 <= g.mean_minutes <= 440.0


def test_distant_tod_patterns_split():
    groups = build_groups([tod("switch.a", 432.0), tod("switch.b", 500.0)])
    assert len(groups) == 2


def test_day_types_never_mix():
    groups = build_groups([tod("switch.a", 432.0), tod("switch.a", 432.0, day_type="weekend")])
    assert len(groups) == 2


def test_pairs_group_by_trigger():
    def pair(entity):
        return make_pattern(
            pattern_key=f"pair:binary_sensor.door:on->{entity}:on",
            kind="event_pair",
            entity_id=entity,
            day_type=None,
            tod_minutes=None,
            trigger_entity_id="binary_sensor.door",
            trigger_state="on",
        )

    (g,) = build_groups([pair("light.bar"), pair("switch.a")])
    assert g.kind == "event_pair"
    assert g.trigger_entity_id == "binary_sensor.door"
    assert g.entity_ids == ["light.bar", "switch.a"]


def test_group_key_stable_regardless_of_input_order():
    a, b = tod("switch.a", 432.0), tod("switch.b", 440.0)
    (g1,) = build_groups([a, b])
    (g2,) = build_groups([b, a])
    assert g1.group_key == g2.group_key
    assert g1.group_key.startswith("tod:weekday:")
```

- [ ] **Step 2: Implement `src/pae/proposer/grouping.py`**

```python
"""Group eligible patterns into proposal candidates (deterministic, pre-LLM).

tod: same day_type, cluster means within window_minutes of each other (the
seven 07:12 switches become one group). pair: same (trigger, state).
group_key is stable across nights so proposals never duplicate."""
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from pae.miner.stats import circular_mean


@dataclass
class ProposalGroup:
    kind: str
    day_type: str | None
    group_key: str
    patterns: list = field(default_factory=list)
    mean_minutes: float | None = None
    trigger_entity_id: str | None = None
    trigger_state: str | None = None

    @property
    def entity_actions(self) -> list[tuple[str, str]]:
        return sorted({(p.entity_id, p.action) for p in self.patterns})

    @property
    def entity_ids(self) -> list[str]:
        return sorted({p.entity_id for p in self.patterns})


def _digest(entity_actions: list[tuple[str, str]]) -> str:
    joined = ",".join(f"{e}={a}" for e, a in entity_actions)
    return hashlib.sha1(joined.encode()).hexdigest()[:12]


def build_groups(eligible: Sequence, *, window_minutes: float = 20.0) -> list[ProposalGroup]:
    groups: list[ProposalGroup] = []

    tods = [p for p in eligible if p.kind == "time_of_day"]
    by_day_type: dict[str, list] = {}
    for p in tods:
        by_day_type.setdefault(p.day_type or "", []).append(p)
    for day_type, members in sorted(by_day_type.items()):
        members = sorted(members, key=lambda p: (p.tod_minutes, p.entity_id, p.action))
        cluster: list = []
        for p in members:
            if cluster and p.tod_minutes - cluster[-1].tod_minutes > window_minutes:
                groups.append(_close_tod(day_type, cluster))
                cluster = []
            cluster.append(p)
        if cluster:
            groups.append(_close_tod(day_type, cluster))

    pairs = [p for p in eligible if p.kind == "event_pair"]
    by_trigger: dict[tuple[str, str], list] = {}
    for p in pairs:
        by_trigger.setdefault((p.trigger_entity_id, p.trigger_state), []).append(p)
    for (trig, state), members in sorted(by_trigger.items()):
        g = ProposalGroup(
            kind="event_pair",
            day_type=None,
            group_key="",
            patterns=members,
            trigger_entity_id=trig,
            trigger_state=state,
        )
        g.group_key = f"pair:{trig}:{state}:{_digest(g.entity_actions)}"
        groups.append(g)
    return groups


def _close_tod(day_type: str, cluster: list) -> ProposalGroup:
    g = ProposalGroup(
        kind="time_of_day",
        day_type=day_type,
        group_key="",
        patterns=list(cluster),
        mean_minutes=circular_mean([p.tod_minutes for p in cluster]),
    )
    bucket = int(g.mean_minutes // 30)
    g.group_key = f"tod:{day_type}:{bucket:02d}:{_digest(g.entity_actions)}"
    return g
```

Note: consecutive-gap clustering (not distance-to-first) is intentional and matches the
miner's `cluster_minutes` philosophy; midnight wrap is not needed here because grouped tod
means come from already-clustered patterns.

- [ ] **Step 3: Verify pass + lint; commit**

```bash
git add src/pae/proposer/grouping.py tests/proposer/test_grouping.py
git commit -m "feat(proposer): sibling grouping with stable group keys"
```

---

### Task 5: Automation subset schema + validation

**Files:**
- Create: `src/pae/proposer/schema.py`
- Test: `tests/proposer/test_schema.py`

**Interfaces:**
- Consumes: `ProposalGroup` (Task 4), `SunCalculator` (`pae.miner.sun`),
  `circular_diff` (`pae.miner.stats`).
- Produces:
  - pydantic models `Automation`, `ServiceAction`, `TimeTrigger`, `SunTrigger`,
    `StateTrigger`, `TimeCondition`, `StateCondition`, `SunCondition` (all
    `extra="forbid"`)
  - `ALLOWED_SERVICES: dict[str, set[str]]`
  - `SERVICE_STATE: dict[str, str]` (`turn_on -> "on"`, `turn_off -> "off"`,
    `lock -> "locked"`, `unlock -> "unlocked"`, `open_cover -> "open"`,
    `close_cover -> "closed"`)
  - `validate_proposal(automation: dict, group, *, registry_domains: dict[str, str],
    tz, sun, grace_minutes: float = 60.0) -> tuple[Automation | None, list[str]]`
    — `(parsed, [])` on success, `(None, errors)` on failure.

- [ ] **Step 1: `tests/proposer/test_schema.py` (write, watch fail)**

```python
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from conftest import make_pattern
from pae.miner.sun import SunCalculator
from pae.proposer.grouping import build_groups
from pae.proposer.schema import validate_proposal

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
REG = {"switch.a": "switch", "light.bar": "light", "binary_sensor.door": "binary_sensor"}


def tod_group(minutes=432.0):
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="tod:switch.a:on:weekday:14",
                entity_id="switch.a",
                tod_minutes=minutes,
            )
        ]
    )
    return g


def pair_group():
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="pair:binary_sensor.door:on->light.bar:on",
                kind="event_pair",
                entity_id="light.bar",
                day_type=None,
                tod_minutes=None,
                trigger_entity_id="binary_sensor.door",
                trigger_state="on",
            )
        ]
    )
    return g


def check(automation, group, **kw):
    return validate_proposal(
        automation, group, registry_domains=REG, tz=TZ, sun=SUN, **kw
    )


GOOD_TOD = {
    "trigger": [{"platform": "time", "at": "07:12:00"}],
    "condition": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}],
    "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
}


def test_valid_tod_automation_passes():
    parsed, errors = check(GOOD_TOD, tod_group())
    assert errors == []
    assert parsed.trigger[0].at == "07:12:00"


def test_unknown_top_level_key_rejected():
    bad = dict(GOOD_TOD, mode="restart")
    parsed, errors = check(bad, tod_group())
    assert parsed is None and errors


def test_foreign_entity_rejected():
    bad = {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ["light.bar"]}}],
    }
    parsed, errors = check(bad, tod_group())
    assert parsed is None
    assert any("light.bar" in e for e in errors)


def test_service_domain_mismatch_rejected():
    bad = {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    parsed, errors = check(bad, tod_group())
    assert parsed is None


def test_trigger_time_far_from_mined_mean_rejected():
    bad = dict(GOOD_TOD, trigger=[{"platform": "time", "at": "12:00:00"}])
    parsed, errors = check(bad, tod_group())
    assert parsed is None
    assert any("mined" in e for e in errors)


def test_template_smuggling_rejected():
    bad = dict(
        GOOD_TOD,
        action=[
            {
                "service": "switch.turn_on",
                "target": {"entity_id": ["switch.a"]},
                "data": {"x": "{{ states('sensor.hack') }}"},
            }
        ],
    )
    parsed, errors = check(bad, tod_group())
    assert parsed is None


def test_sun_trigger_near_mined_mean_passes():
    # group mean at today's sunset; sun trigger with zero offset must pass
    sunset = SUN.sun_minutes(datetime.now(UTC).astimezone(TZ).date())[1]
    auto = {
        "trigger": [{"platform": "sun", "event": "sunset", "offset": "00:00:00"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    parsed, errors = check(auto, tod_group(minutes=sunset))
    assert errors == []


def test_pair_trigger_must_match_mined_trigger():
    good = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ["light.bar"]}}],
    }
    parsed, errors = check(good, pair_group())
    assert errors == []
    bad = dict(
        good, trigger=[{"platform": "state", "entity_id": "binary_sensor.door", "to": "off"}]
    )
    parsed, errors = check(bad, pair_group())
    assert parsed is None
```

- [ ] **Step 2: Implement `src/pae/proposer/schema.py`**

```python
"""The constrained HA-automation subset the LLM may author, and its validation.

Declarative only: time/sun/state triggers, time/state/sun conditions, service
actions. No delay/wait/templates/scripts — pydantic extra="forbid" plus the
checks below. A validation failure never stores anything."""
from datetime import UTC, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from pae.miner.stats import circular_diff

ALLOWED_SERVICES: dict[str, set[str]] = {
    "light": {"turn_on", "turn_off"},
    "switch": {"turn_on", "turn_off"},
    "fan": {"turn_on", "turn_off"},
    "media_player": {"turn_on", "turn_off"},
    "lock": {"lock", "unlock"},
    "cover": {"open_cover", "close_cover"},
}
SERVICE_STATE: dict[str, str] = {
    "turn_on": "on",
    "turn_off": "off",
    "lock": "locked",
    "unlock": "unlocked",
    "open_cover": "open",
    "close_cover": "closed",
}
WEEKDAYS = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hms(value: str) -> float:
    """'HH:MM:SS' -> minutes-of-day; raises ValueError on junk."""
    h, m, s = value.split(":")
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59 and 0 <= int(s) <= 59):
        raise ValueError(f"not a time of day: {value!r}")
    return int(h) * 60 + int(m) + int(s) / 60


def _parse_offset(value: str) -> float:
    """'[+-]HH:MM:SS' -> signed minutes; raises ValueError on junk."""
    sign = -1.0 if value.startswith("-") else 1.0
    return sign * _parse_hms(value.lstrip("+-"))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeTrigger(_Strict):
    platform: Literal["time"]
    at: str

    @field_validator("at")
    @classmethod
    def _at_is_time(cls, v: str) -> str:
        _parse_hms(v)
        return v


class SunTrigger(_Strict):
    platform: Literal["sun"]
    event: Literal["sunrise", "sunset"]
    offset: str = "00:00:00"

    @field_validator("offset")
    @classmethod
    def _offset_ok(cls, v: str) -> str:
        _parse_offset(v)
        return v


class StateTrigger(_Strict):
    platform: Literal["state"]
    entity_id: str
    to: str
    from_: str | None = Field(default=None, alias="from")


Trigger = Annotated[
    Union[TimeTrigger, SunTrigger, StateTrigger], Field(discriminator="platform")
]


class TimeCondition(_Strict):
    condition: Literal["time"]
    weekday: list[WEEKDAYS] | None = None
    after: str | None = None
    before: str | None = None


class StateCondition(_Strict):
    condition: Literal["state"]
    entity_id: str
    state: str


class SunCondition(_Strict):
    condition: Literal["sun"]
    after: Literal["sunrise", "sunset"] | None = None
    before: Literal["sunrise", "sunset"] | None = None


Condition = Annotated[
    Union[TimeCondition, StateCondition, SunCondition], Field(discriminator="condition")
]


class Target(_Strict):
    entity_id: list[str]


class ServiceAction(_Strict):
    service: str
    target: Target


class Automation(_Strict):
    trigger: list[Trigger] = Field(min_length=1, max_length=1)
    condition: list[Condition] = Field(default_factory=list)
    action: list[ServiceAction] = Field(min_length=1)


def validate_proposal(
    automation: dict,
    group,
    *,
    registry_domains: dict[str, str],
    tz,
    sun,
    grace_minutes: float = 60.0,
) -> tuple[Automation | None, list[str]]:
    try:
        parsed = Automation.model_validate(automation)
    except ValidationError as exc:
        return None, [str(e["loc"]) + ": " + e["msg"] for e in exc.errors()]

    errors: list[str] = []
    allowed_entities = set(group.entity_ids)
    if group.trigger_entity_id:
        allowed_entities.add(group.trigger_entity_id)

    referenced: list[str] = []
    for t in parsed.trigger:
        if isinstance(t, StateTrigger):
            referenced.append(t.entity_id)
    for c in parsed.condition:
        if isinstance(c, StateCondition):
            referenced.append(c.entity_id)
    for a in parsed.action:
        referenced.extend(a.target.entity_id)
    for e in referenced:
        if e not in allowed_entities:
            errors.append(f"entity {e} is not part of the mined pattern group")
        elif e not in registry_domains:
            errors.append(f"entity {e} is not in the registry")

    for a in parsed.action:
        if "." not in a.service:
            errors.append(f"malformed service {a.service!r}")
            continue
        s_domain, s_name = a.service.split(".", 1)
        if s_name not in ALLOWED_SERVICES.get(s_domain, set()):
            errors.append(f"service {a.service} not in the allowed set")
        for e in a.target.entity_id:
            if registry_domains.get(e) not in (None, s_domain):
                errors.append(f"service {a.service} does not match domain of {e}")

    trig = parsed.trigger[0]
    if group.kind == "time_of_day":
        if isinstance(trig, TimeTrigger):
            minutes = _parse_hms(trig.at)
        elif isinstance(trig, SunTrigger):
            if sun is None:
                errors.append("sun trigger but sun calculation is disabled (no coordinates)")
                minutes = None
            else:
                today = datetime.now(UTC).astimezone(tz).date()
                sm = sun.sun_minutes(today)
                minutes = (
                    None
                    if sm is None
                    else sm[0 if trig.event == "sunrise" else 1] + _parse_offset(trig.offset)
                )
        else:
            errors.append("time_of_day proposals must use a time or sun trigger")
            minutes = None
        if minutes is not None and abs(circular_diff(minutes, group.mean_minutes)) > grace_minutes:
            errors.append(
                f"trigger time {minutes:.0f}min is over {grace_minutes:.0f}min from the "
                f"mined mean {group.mean_minutes:.0f}min"
            )
    else:  # event_pair
        if not isinstance(trig, StateTrigger):
            errors.append("event_pair proposals must use a state trigger")
        elif trig.entity_id != group.trigger_entity_id or trig.to != group.trigger_state:
            errors.append("state trigger must match the mined trigger entity and state")

    return (parsed, []) if not errors else (None, errors)
```

- [ ] **Step 3: Verify pass + lint; commit**

```bash
git add src/pae/proposer/schema.py tests/proposer/test_schema.py
git commit -m "feat(proposer): automation subset schema + validation"
```

---

### Task 6: Ollama client

**Files:**
- Create: `src/pae/llm/__init__.py` (empty), `src/pae/llm/client.py`
- Test: `tests/llm/__init__.py` (empty), `tests/llm/test_client.py`

**Interfaces:**
- Produces:
  - `class LLMError(Exception)`
  - `@dataclass LLMResponse: content: dict; model: str; host: str; duration_seconds: float`
  - `class OllamaClient(primary_url, fallback_url, primary_model, fallback_model,
    timeout_seconds=120.0)` with `chat_json(messages: list[dict], schema: dict)
    -> LLMResponse`. Raises `ValueError` at construction for `:cloud` models;
    raises `LLMError` when both hosts fail.

- [ ] **Step 1: `tests/llm/test_client.py` (write, watch fail)**

Uses a stdlib threaded HTTP server as a fake Ollama — no new dependencies.

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pae.llm.client import LLMError, OllamaClient


class FakeOllama:
    """Scriptable /api/chat: each queued item is a JSON-able content dict,
    the string 'garbage' (non-JSON content), or an int HTTP status to fail with."""

    def __init__(self):
        self.responses = []
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                item = outer.responses.pop(0) if outer.responses else 500
                if isinstance(item, int):
                    self.send_response(item)
                    self.end_headers()
                    return
                content = "garbage{" if item == "garbage" else json.dumps(item)
                payload = json.dumps({"message": {"role": "assistant", "content": content}})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    servers = []

    def _make():
        s = FakeOllama()
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
MSGS = [{"role": "user", "content": "hi"}]


def client(primary, fallback, **kw):
    return OllamaClient(
        primary_url=primary.url,
        fallback_url=fallback.url,
        primary_model="m-primary",
        fallback_model="m-fallback",
        timeout_seconds=5.0,
        **kw,
    )


def test_happy_path_uses_primary(fake):
    p, f = fake(), fake()
    p.responses = [{"ok": True}]
    resp = client(p, f).chat_json(MSGS, SCHEMA)
    assert resp.content == {"ok": True}
    assert resp.model == "m-primary"
    path, body = p.requests[0]
    assert path == "/api/chat"
    assert body["format"] == SCHEMA and body["stream"] is False


def test_failover_to_fallback(fake):
    p, f = fake(), fake()
    p.responses = [500, 500]
    f.responses = [{"ok": True}]
    resp = client(p, f).chat_json(MSGS, SCHEMA)
    assert resp.model == "m-fallback"


def test_bad_json_content_retries_then_fails_over(fake):
    p, f = fake(), fake()
    p.responses = ["garbage", "garbage"]
    f.responses = [{"ok": True}]
    assert client(p, f).chat_json(MSGS, SCHEMA).model == "m-fallback"


def test_both_hosts_down_raises(fake):
    p, f = fake(), fake()
    p.responses = [500, 500]
    f.responses = [500, 500]
    with pytest.raises(LLMError):
        client(p, f).chat_json(MSGS, SCHEMA)


def test_cloud_model_refused():
    with pytest.raises(ValueError, match="cloud"):
        OllamaClient(
            primary_url="http://x",
            fallback_url="http://y",
            primary_model="qwen3.5:cloud",
            fallback_model="m",
        )
```

- [ ] **Step 2: Implement `src/pae/llm/client.py`**

```python
"""Sync Ollama chat client with structured output and primary->fallback failover.

Runs inside the RQ worker (SimpleWorker, in-process) — hence stdlib urllib,
no async. `format` carries a JSON schema: Ollama constrains decoding to it,
so content parses as JSON in the overwhelming majority of calls; a parse
failure is treated like any transport failure (retry, then fail over).
Model names containing ':cloud' are refused outright — Ollama would route
those to its hosted service, violating the local-only rule."""
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from pae.logging import get_logger

log = get_logger(__name__)

ATTEMPTS_PER_HOST = 2


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    content: dict
    model: str
    host: str
    duration_seconds: float


class OllamaClient:
    def __init__(
        self,
        primary_url: str,
        fallback_url: str,
        primary_model: str,
        fallback_model: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        for model in (primary_model, fallback_model):
            if ":cloud" in model:
                raise ValueError(f"model {model!r} is an Ollama cloud model — local only")
        self._targets = [(primary_url, primary_model), (fallback_url, fallback_model)]
        self._timeout = timeout_seconds

    def chat_json(self, messages: list[dict], schema: dict) -> LLMResponse:
        last_error: Exception | None = None
        for host, model in self._targets:
            for attempt in range(ATTEMPTS_PER_HOST):
                try:
                    return self._request(host, model, messages, schema)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    last_error = exc
                    log.warning(
                        "llm_attempt_failed",
                        host=host,
                        model=model,
                        attempt=attempt,
                        error=str(exc),
                    )
        raise LLMError(f"all Ollama hosts failed: {last_error}")

    def _request(self, host: str, model: str, messages: list[dict], schema: dict) -> LLMResponse:
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0.2},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            payload = json.load(resp)
        try:
            content = json.loads(payload["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"unparseable Ollama response: {exc}") from exc
        if not isinstance(content, dict):
            raise ValueError("Ollama response content is not a JSON object")
        return LLMResponse(
            content=content,
            model=model,
            host=host,
            duration_seconds=time.monotonic() - started,
        )
```

- [ ] **Step 3: Verify pass + lint; commit**

```bash
git add src/pae/llm/ tests/llm/
git commit -m "feat(llm): sync Ollama client with failover and cloud-model guard"
```

---

### Task 7: Prompt v1 + response schema

**Files:**
- Create: `src/pae/llm/prompt.py`
- Test: `tests/llm/test_prompt.py`

**Interfaces:**
- Consumes: `ProposalGroup` (Task 4).
- Produces:
  - `PROMPT_VERSION = 1`
  - `RESPONSE_SCHEMA: dict` — JSON schema for
    `{propose: bool, decline_reason?: str, title?: str, rationale?: str, automation?: object}`
  - `@dataclass RegistryInfo: domain: str; friendly_name: str | None;
    area_name: str | None; device_class: str | None`
  - `build_messages(group, registry: dict[str, RegistryInfo], tz) -> list[dict]`

- [ ] **Step 1: `tests/llm/test_prompt.py` (write, watch fail)**

```python
from zoneinfo import ZoneInfo

from conftest import make_pattern  # tests/proposer/conftest via tests path? see note below
from pae.llm.prompt import PROMPT_VERSION, RESPONSE_SCHEMA, RegistryInfo, build_messages
from pae.proposer.grouping import build_groups

TZ = ZoneInfo("America/Chicago")
REG = {
    "switch.a": RegistryInfo("switch", "Patio Lights", "Patio", None),
    "binary_sensor.door": RegistryInfo("binary_sensor", "Front Door", "Foyer", "door"),
    "light.bar": RegistryInfo("light", "Bar Light", "Basement", None),
}


def test_response_schema_shape():
    assert RESPONSE_SCHEMA["required"] == ["propose"]
    assert set(RESPONSE_SCHEMA["properties"]) == {
        "propose", "decline_reason", "title", "rationale", "automation"
    }


def test_tod_prompt_contains_the_evidence():
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="tod:switch.a:on:weekday:14",
                entity_id="switch.a",
                tod_minutes=432.0,
                evidence={"sample_times": ["2026-07-27T12:12:00+00:00"]},
            )
        ]
    )
    messages = build_messages(g, REG, TZ)
    assert messages[0]["role"] == "system"
    body = messages[1]["content"]
    assert "Patio Lights" in body and "Patio" in body
    assert "07:12" in body  # 432 minutes local
    assert "weekday" in body
    assert str(PROMPT_VERSION)  # exists


def test_pair_prompt_names_trigger_and_action():
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="pair:binary_sensor.door:on->light.bar:on",
                kind="event_pair",
                entity_id="light.bar",
                day_type=None,
                tod_minutes=None,
                trigger_entity_id="binary_sensor.door",
                trigger_state="on",
            )
        ]
    )
    body = build_messages(g, REG, TZ)[1]["content"]
    assert "Front Door" in body and "Bar Light" in body
```

Note: `tests/llm/` needs access to `make_pattern`. Move `make_pattern` into the top-level
`tests/conftest.py` in this step (append it there; keep `tests/proposer/conftest.py`
re-exporting `from conftest import make_pattern, NOW  # noqa: F401` so Task 3-5 imports
keep working). Repo rule: import from `conftest`, never `tests.conftest`.

- [ ] **Step 2: Implement `src/pae/llm/prompt.py`**

```python
"""Prompt v1: frame one pattern group as an automation-drafting request.

The model may decline (propose=false) — it doubles as a plausibility filter.
The response is constrained by RESPONSE_SCHEMA via Ollama structured output;
everything the model returns is re-validated by pae.proposer.schema before
storage, so this prompt is about giving good evidence, not enforcing rules."""
from dataclasses import dataclass

PROMPT_VERSION = 1

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "propose": {"type": "boolean"},
        "decline_reason": {"type": "string"},
        "title": {"type": "string"},
        "rationale": {"type": "string"},
        "automation": {"type": "object"},
    },
    "required": ["propose"],
}

SYSTEM = """You draft Home Assistant automations from observed human habits.

You will get one mined behavior pattern group: statistics about manual actions a
household member performs regularly, plus context about each entity involved.

Decide whether this habit makes sense as an automation a careful home operator
would want. If not (too personal, safety-relevant, coincidental, or better left
manual), return {"propose": false, "decline_reason": "..."}.

If yes, return propose=true with:
- title: short, human, specific (e.g. "Morning path lights on weekdays")
- rationale: 2-3 sentences: what was observed and why automating it helps
- automation: a Home Assistant automation object using ONLY:
  - exactly one trigger: {"platform":"time","at":"HH:MM:SS"} or
    {"platform":"sun","event":"sunset"|"sunrise","offset":"[+-]HH:MM:SS"} or
    {"platform":"state","entity_id":...,"to":...}
  - optional conditions: {"condition":"time","weekday":[...]} or
    {"condition":"state",...} or {"condition":"sun",...}
  - actions: {"service":"<domain>.<service>","target":{"entity_id":[...]}} only
Use only the entities given. No templates, delays, or scripts. If the observed
times track sunset or sunrise, prefer a sun trigger over a fixed time."""


@dataclass
class RegistryInfo:
    domain: str
    friendly_name: str | None
    area_name: str | None
    device_class: str | None


def _entity_line(entity_id: str, info: RegistryInfo | None) -> str:
    if info is None:
        return f"- {entity_id}"
    parts = [f"- {entity_id}: \"{info.friendly_name or entity_id}\""]
    if info.area_name:
        parts.append(f"area: {info.area_name}")
    if info.device_class:
        parts.append(f"class: {info.device_class}")
    return ", ".join(parts)


def _hhmm(minutes: float) -> str:
    return f"{int(minutes // 60):02d}:{int(minutes % 60):02d}"


def build_messages(group, registry, tz) -> list[dict]:
    lines: list[str] = []
    entity_ids = set(group.entity_ids)
    if group.trigger_entity_id:
        entity_ids.add(group.trigger_entity_id)
    lines.append("Entities:")
    for e in sorted(entity_ids):
        lines.append(_entity_line(e, registry.get(e)))
    lines.append("")

    if group.kind == "time_of_day":
        lines.append(
            f"Habit: on {group.day_type} days, around {_hhmm(group.mean_minutes)} local "
            f"({tz.key}), these manual actions occur together:"
        )
        for p in group.patterns:
            lines.append(
                f"- {p.entity_id} -> {p.action} at ~{_hhmm(p.tod_minutes)} "
                f"(±{p.tod_std_minutes:.0f} min, {p.occurrences} times over "
                f"{p.days_observed} days, support {p.support:.2f}, "
                f"consistency {p.temporal_consistency:.2f})"
            )
            sample = (p.evidence or {}).get("sample_times", [])[-3:]
            if sample:
                lines.append(f"  recent occurrences: {', '.join(sample)}")
    else:
        lines.append(
            f"Habit: after {group.trigger_entity_id} changes to "
            f"'{group.trigger_state}', these manual actions follow within minutes:"
        )
        for p in group.patterns:
            lines.append(
                f"- {p.entity_id} -> {p.action} (confidence {p.confidence:.2f}, "
                f"lift {p.lift:.1f}, {p.occurrences} times)"
            )

    lines.append("")
    lines.append("Draft the automation, or decline.")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]
```

- [ ] **Step 3: Verify pass + lint (including the conftest move); commit**

```bash
git add src/pae/llm/prompt.py tests/llm/test_prompt.py tests/conftest.py tests/proposer/conftest.py
git commit -m "feat(llm): prompt v1 + structured response schema"
```

---

### Task 8: Metrics + generation service + job + CLI

**Files:**
- Modify: `src/pae/metrics.py` (append), `src/pae/cli.py`
- Create: `src/pae/proposer/service.py`, `src/pae/proposer/job.py`
- Test: `tests/proposer/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7. LLM is injected: any object with
  `chat_json(messages, schema) -> LLMResponse`-shaped return works.
- Produces:
  - metrics: `LLM_REQUESTS = Counter("pae_llm_requests_total", ..., ["model", "status"])`,
    `LLM_SECONDS = Gauge("pae_llm_last_duration_seconds", ...)`,
    `PROPOSALS_BY_STATUS = Gauge("pae_proposals", ..., ["status"])`,
    `PROPOSER_VALIDATION_FAILURES = Counter("pae_proposer_validation_failures_total", ...)`,
    `SHADOW_DAYS_SCORED = Counter("pae_shadow_days_scored_total", ...)`
  - `@dataclass ProposeResult: groups_considered: int; generated: int; declined: int;
    validation_failed: int; skipped_existing: int; stale_marked: int`
  - `run_proposing(now: datetime | None = None, llm=None) -> ProposeResult` — the
    orchestration entry, used by job, CLI, and tests.
  - `propose_job() -> dict` in `src/pae/proposer/job.py`
  - CLI: `pae propose`

**Service logic (implement exactly this):**

1. `settings = get_settings()`; `tz`, `sun` (SunCalculator if lat+lon set, else None),
   engine from `settings.db_url`.
2. Load all `Pattern` rows; load registry as `dict[entity_id, RegistryInfo]` plus
   `registry_domains: dict[entity_id, domain]` and `registry_ids` set.
3. `eligible = eligible_patterns(...)` wired from `proposer_*` settings;
   `groups = build_groups(eligible, window_minutes=settings.proposer_group_window_minutes)`.
4. Load existing proposals keyed by `group_key`. Per group:
   - existing `rejected` → skip (count `skipped_existing`).
   - existing `shadowing`/`approved` → `last_eligible_at = now`, `updated_at = now`, skip
     (count `skipped_existing`).
   - existing `stale` → revive: `status = "shadowing"`, `last_eligible_at = now`; set
     member `patterns.status = "proposed"`; count as `generated`? No — count `skipped_existing`
     and log `proposal_revived`.
   - no row → build messages; `llm.chat_json(messages, RESPONSE_SCHEMA)`; on `LLMError`
     log + continue. If `content.get("propose") is not True` → `declined += 1`, log with
     `decline_reason`, continue. Else `validate_proposal(...)`; on errors, retry ONCE with
     `messages + [{"role":"user","content":"Validation failed: <errors>. Return corrected JSON."}]`;
     on second failure `validation_failed += 1`, `PROPOSER_VALIDATION_FAILURES.inc()`, continue.
     On success insert `Proposal(status="shadowing", last_eligible_at=now, created_at=now,
     updated_at=now, prompt_version=PROMPT_VERSION, model_name=resp.model,
     automation_json=parsed.model_dump(by_alias=True, exclude_none=True), ...)`; update member
     `patterns.status = "proposed"` (only rows currently `candidate`); `generated += 1`;
     `LLM_REQUESTS.labels(model=resp.model, status="ok").inc()`.
5. Staleness sweep: proposals in (`shadowing`, `approved`) with
   `last_eligible_at < now - timedelta(days=settings.proposer_stale_days)` → `status="stale"`,
   `updated_at=now`, `stale_marked += 1`; revert their source patterns' status
   `proposed → candidate` (by `pattern_key in source_pattern_keys`).
6. Refresh `PROPOSALS_BY_STATUS` gauges from a `GROUP BY status` count. Log `propose_done`
   with all counters. Everything in ONE `engine.begin()` transaction; the LLM calls happen
   inside it (acceptable: nightly batch, low contention).

- [ ] **Step 1: `tests/proposer/test_service.py` (write, watch fail)**

Testing strategy: `run_proposing` hits Postgres — but every piece around the db is already
unit-tested, so here we test the *decision core* extracted as a pure function. Split the
service into `_process_groups(groups, existing, llm, registry, registry_domains, tz, sun,
now)` returning `(ProposeResult-ish counters, inserts, status_updates)` and a thin
`run_proposing` that does IO. Tests drive `_process_groups` with a `FakeLLM`; `run_proposing`
itself is exercised live in Task 12.

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from conftest import make_pattern
from pae.llm.prompt import RegistryInfo
from pae.miner.sun import SunCalculator
from pae.proposer.grouping import build_groups
from pae.proposer.service import _process_groups

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
REG = {"switch.a": RegistryInfo("switch", "Patio", "Patio", None)}
DOMAINS = {"switch.a": "switch"}

GOOD = {
    "propose": True,
    "title": "Patio on",
    "rationale": "Observed daily.",
    "automation": {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    },
}


@dataclass
class FakeResp:
    content: dict
    model: str = "fake"
    host: str = "fake"
    duration_seconds: float = 0.1


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, schema):
        self.calls.append(messages)
        return FakeResp(self.responses.pop(0))


def groups():
    return build_groups(
        [make_pattern(pattern_key="tod:switch.a:on:weekday:14", entity_id="switch.a")]
    )


def run(llm, existing=None):
    return _process_groups(
        groups(), existing or {}, llm, REG, DOMAINS, TZ, SUN, NOW
    )


def test_good_response_produces_insert():
    counters, inserts, status_updates = run(FakeLLM([GOOD]))
    assert counters["generated"] == 1
    (ins,) = inserts
    assert ins["title"] == "Patio on"
    assert ins["status"] == "shadowing"
    assert status_updates == {"tod:switch.a:on:weekday:14": "proposed"}


def test_decline_is_counted_not_stored():
    counters, inserts, _ = run(FakeLLM([{"propose": False, "decline_reason": "meh"}]))
    assert counters["declined"] == 1 and inserts == []


def test_validation_failure_retries_once_with_errors():
    bad = dict(GOOD, automation={"trigger": [], "condition": [], "action": []})
    llm = FakeLLM([bad, GOOD])
    counters, inserts, _ = run(llm)
    assert counters["generated"] == 1 and len(llm.calls) == 2
    assert "Validation failed" in llm.calls[1][-1]["content"]


def test_second_validation_failure_skips():
    bad = dict(GOOD, automation={"trigger": [], "condition": [], "action": []})
    counters, inserts, _ = run(FakeLLM([bad, bad]))
    assert counters["validation_failed"] == 1 and inserts == []


def test_existing_live_proposal_skips_llm():
    llm = FakeLLM([])
    counters, inserts, _ = run(llm, existing={g.group_key: "shadowing" for g in groups()})
    assert counters["skipped_existing"] == 1 and llm.calls == []
```

- [ ] **Step 2: Implement metrics, `_process_groups` + `run_proposing` + job + CLI**

`src/pae/metrics.py` — append:

```python
LLM_REQUESTS = Counter("pae_llm_requests_total", "LLM chat requests", ["model", "status"])
LLM_SECONDS = Gauge("pae_llm_last_duration_seconds", "Duration of the last LLM request")
PROPOSALS_BY_STATUS = Gauge("pae_proposals", "Proposals by status", ["status"])
PROPOSER_VALIDATION_FAILURES = Counter(
    "pae_proposer_validation_failures_total",
    "LLM outputs rejected by schema/registry validation (after one retry)",
)
SHADOW_DAYS_SCORED = Counter(
    "pae_shadow_days_scored_total", "Proposal-days scored by the shadow evaluator"
)
```

`src/pae/proposer/service.py` — implement the logic block above. `_process_groups`
signature and return exactly as the test drives it: counters dict with keys
`groups_considered, generated, declined, validation_failed, skipped_existing`;
`inserts` = list of dicts with all `Proposal` column values except id/created/updated
(`run_proposing` stamps times); `status_updates` = `dict[pattern_key, new_status]`.
`existing` = `dict[group_key, status]`. `run_proposing` loads inputs, calls
`_process_groups`, applies inserts/updates + the staleness sweep (step 5 above) in one
transaction, refreshes gauges, logs, returns `ProposeResult`.

`src/pae/proposer/job.py`:

```python
"""RQ job entry points — imported by the worker via dotted path."""
from pae.proposer.service import run_proposing


def propose_job() -> dict:
    result = run_proposing()
    return result.__dict__
```

`src/pae/cli.py` — add subparser `sub.add_parser("propose", help="generate proposals once, now")`
and the handler (mirror the `mine` block):

```python
    if args.command == "propose":
        configure_logging(settings.log_level, "console")
        from pae.proposer.service import run_proposing

        result = run_proposing()
        print(
            f"groups={result.groups_considered} generated={result.generated} "
            f"declined={result.declined} validation_failed={result.validation_failed} "
            f"skipped={result.skipped_existing} stale={result.stale_marked}"
        )
        return 0
```

In `run_proposing`, construct the default client when `llm is None`:

```python
    if llm is None:
        llm = OllamaClient(
            primary_url=settings.ollama_primary,
            fallback_url=settings.ollama_fallback,
            primary_model=settings.llm_model_primary,
            fallback_model=settings.llm_model_fallback,
            timeout_seconds=settings.llm_timeout_seconds,
        )
```

- [ ] **Step 3: Verify pass + lint; commit**

```bash
git add src/pae/metrics.py src/pae/proposer/ src/pae/cli.py tests/proposer/test_service.py
git commit -m "feat(proposer): generation service, RQ job, pae propose CLI"
```

---

### Task 9: Shadow evaluator + job + CLI

**Files:**
- Create: `src/pae/shadow/__init__.py` (empty), `src/pae/shadow/service.py`,
  `src/pae/shadow/job.py`
- Test: `tests/shadow/__init__.py` (empty), `tests/shadow/test_service.py`

**Interfaces:**
- Consumes: `Automation` model + `SERVICE_STATE` (Task 5), `SunCalculator`,
  `minutes_of_day`/`circular_diff` from `pae.miner.stats`.
- Produces:
  - `@dataclass DayScore: expected_fires: int; human_matches: int; human_total: int`
  - `@dataclass ShadowEvent: time: datetime; entity_id: str; new_state: str;
    triggered_by: str` (input row shape)
  - `evaluate_day(automation: dict, kind: str, day: date, *, tz, sun,
    day_events: list[ShadowEvent], tolerance_minutes: float = 45.0,
    pair_window_minutes: float = 5.0) -> DayScore | None` (None = not evaluable:
    sun trigger with sun disabled/polar)
  - `run_shadow_eval(now: datetime | None = None) -> dict` (proposals_evaluated,
    days_scored)
  - `shadow_eval_job() -> dict`; CLI `pae shadow`

**Semantics (implement exactly):**

- Parse the automation with `Automation.model_validate` (it was validated at storage;
  a parse failure here means schema drift — log + return None).
- Would-fire minutes for `day`:
  - `time` trigger → `[_parse_hms(at)]` if the day passes conditions, else `[]`
  - `sun` trigger → `[sun_minutes(day)[event] + offset]` (None from sun → not evaluable)
    if the day passes conditions
  - `state` trigger → minutes-of-day of every `day_events` row with matching
    `entity_id` and `new_state == to` (any `triggered_by` — external triggers count)
- Conditions: only `TimeCondition.weekday` participates (`day.strftime("%a").lower()[:3]`
  must be in the list when set). `after`/`before`/state/sun conditions are stored but not
  simulated in Phase 3 — document in the module docstring.
- Per **action entity** e with expected state `SERVICE_STATE[service_name]`:
  - `human_events_e` = minutes of `day_events` rows with `entity_id == e`,
    `new_state == expected`, `triggered_by == "manual"`
  - windows: tod/sun fire at minute m → `|circular_diff(ev, m)| <= tolerance_minutes`;
    pair fire at m → `0 <= ev - m <= pair_window_minutes`
  - `expected_e = len(fires)`; `matches_e` = number of fires with ≥1 in-window human
    event; `total_e = len(human_events_e)`; but `human_matches_e` = number of human
    events in-window of ≥1 fire (covered events).
- `DayScore(expected_fires=Σ expected_e, human_matches=Σ covered_e, human_total=Σ total_e)`.
  (Both precision = matches/expected and coverage = matches/total then stay in [0,1]
  because expected is per-entity too.) Wait — matches used for precision is
  fires-with-a-match; for coverage it is covered-events. To keep ONE stored number, store
  `human_matches = Σ covered_e` and accept precision as `min(1, matches/expected)` at
  display time — NO. Resolution (implement this): store `human_matches = Σ matches_e`
  (fires that had a human match). Coverage at display time = `human_matches / human_total`
  is then slightly conservative when a human acts twice near one fire — acceptable and
  simple; document in the `ShadowResult` docstring (already worded that way in Task 1).
- `run_shadow_eval`: load proposals with status in (`shadowing`, `approved`); for each,
  days from `max(created_at local date, today - shadow_lookback_days)` .. yesterday
  (local); skip `(proposal_id, day)` rows that already exist; load events once for the
  full [earliest_needed_day 00:00 local, today 00:00 local) range restricted to the
  union of all involved entity_ids, bucket by local date; `evaluate_day` per proposal-day;
  bulk-insert `ShadowResult` rows; `SHADOW_DAYS_SCORED.inc(days)`; log `shadow_done`.

- [ ] **Step 1: `tests/shadow/test_service.py` (write, watch fail)**

```python
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pae.miner.sun import SunCalculator
from pae.shadow.service import ShadowEvent, evaluate_day

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
DAY = date(2026, 7, 27)  # a Monday


def ev(hh, mm, entity="switch.a", state="on", triggered_by="manual", day=DAY):
    t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
    return ShadowEvent(time=t.astimezone(UTC), entity_id=entity, new_state=state,
                       triggered_by=triggered_by)


TOD = {
    "trigger": [{"platform": "time", "at": "07:12:00"}],
    "condition": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}],
    "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
}


def test_tod_hit():
    score = evaluate_day(TOD, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[ev(7, 20)])
    assert (score.expected_fires, score.human_matches, score.human_total) == (1, 1, 1)


def test_tod_miss_and_out_of_window_human():
    score = evaluate_day(TOD, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[ev(12, 0)])
    assert (score.expected_fires, score.human_matches, score.human_total) == (1, 0, 1)


def test_weekday_condition_blocks_weekend():
    sunday = date(2026, 7, 26)
    score = evaluate_day(
        TOD, "time_of_day", sunday, tz=TZ, sun=SUN, day_events=[ev(7, 12, day=sunday)]
    )
    assert score.expected_fires == 0 and score.human_total == 1


def test_automation_events_never_match():
    score = evaluate_day(
        TOD, "time_of_day", DAY, tz=TZ, sun=SUN,
        day_events=[ev(7, 12, triggered_by="automation")],
    )
    assert (score.human_matches, score.human_total) == (0, 0)


def test_sun_trigger_fires_at_sunset():
    auto = dict(TOD, trigger=[{"platform": "sun", "event": "sunset", "offset": "00:10:00"}],
                condition=[])
    sunset = SUN.sun_minutes(DAY)[1]
    hh, mm = int((sunset + 10) // 60), int((sunset + 10) % 60)
    score = evaluate_day(auto, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[ev(hh, mm)])
    assert (score.expected_fires, score.human_matches) == (1, 1)


def test_pair_counts_each_trigger_occurrence():
    auto = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    events = [
        ev(9, 0, entity="binary_sensor.door"),
        ev(9, 2),                                  # within 5-min pair window -> match
        ev(15, 0, entity="binary_sensor.door"),    # no follow-up -> miss
    ]
    score = evaluate_day(auto, "event_pair", DAY, tz=TZ, sun=SUN, day_events=events)
    assert (score.expected_fires, score.human_matches, score.human_total) == (2, 1, 1)


def test_pair_window_is_forward_only():
    auto = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    events = [ev(9, 0), ev(9, 2, entity="binary_sensor.door")]  # human BEFORE trigger
    score = evaluate_day(auto, "event_pair", DAY, tz=TZ, sun=SUN, day_events=events)
    assert score.human_matches == 0


def test_dst_fallback_sun_eval():
    # sun trigger evaluates on the DST fall-back day without error
    d = date(2026, 11, 1)
    auto = dict(TOD, trigger=[{"platform": "sun", "event": "sunset", "offset": "00:00:00"}],
                condition=[])
    sunset = SUN.sun_minutes(d)[1]
    hh, mm = int(sunset // 60), int(sunset % 60)
    score = evaluate_day(auto, "time_of_day", d, tz=TZ, sun=SUN,
                         day_events=[ev(hh, mm, day=d)])
    assert score.human_matches == 1
```

- [ ] **Step 2: Implement `src/pae/shadow/service.py` + job + CLI**

Implement per the semantics block. Skeleton:

```python
"""Shadow evaluation: replay a proposal's automation against real events.

Scores the automation_json itself (what would ship), not the source pattern.
Only the weekday time-condition is simulated in Phase 3; other stored
conditions are ignored here (documented limitation). Aggregation is
per-action-entity so precision and coverage both stay in [0, 1]."""
```

with `ShadowEvent` dataclass, `evaluate_day` (uses `Automation.model_validate`,
`_parse_hms`/`_parse_offset` imported from `pae.proposer.schema`, `minutes_of_day`,
`circular_diff`), and `run_shadow_eval` per the semantics block (SQLAlchemy Core selects
on `Proposal`, `ShadowResult`, `Event`; bulk insert chunked ≤500 rows — Postgres bind-param
gotcha). `src/pae/shadow/job.py` mirrors `propose_job` (`shadow_eval_job`). CLI subcommand
`shadow` mirrors `propose` printing `proposals=N days=N`.

- [ ] **Step 3: Verify pass + lint; commit**

```bash
git add src/pae/shadow/ src/pae/cli.py tests/shadow/
git commit -m "feat(shadow): nightly shadow evaluator, RQ job, pae shadow CLI"
```

---

### Task 10: Nightly chain in the scheduler

**Files:**
- Modify: `src/pae/worker/scheduler.py`
- Test: `tests/miner/test_scheduler.py` (append)

**Interfaces:**
- Consumes: `propose_job` (Task 8), `shadow_eval_job` (Task 9).
- Produces: nightly chain `mine → propose → shadow` via RQ `depends_on`.

- [ ] **Step 1: Append failing test to `tests/miner/test_scheduler.py`**

```python
def test_nightly_chain_enqueues_three_dependent_jobs():
    from datetime import UTC, datetime

    from pae.worker.scheduler import enqueue_nightly_chain

    calls = []

    class FakeQueue:
        def enqueue(self, fn, job_id=None, depends_on=None):
            calls.append((fn.__name__, job_id, depends_on))
            return type("J", (), {"id": job_id})()

    enqueue_nightly_chain(FakeQueue(), datetime(2026, 7, 28, 9, 0, tzinfo=UTC))
    names = [c[0] for c in calls]
    assert names == ["mine_patterns_job", "propose_job", "shadow_eval_job"]
    assert calls[0][1] == "mine-20260728"
    assert calls[1][2].id == "mine-20260728"      # propose depends on mine
    assert calls[2][2].id == "propose-20260728"   # shadow depends on propose
```

- [ ] **Step 2: Implement** — in `scheduler.py`, extract the enqueue into a module-level
function and use it in `start_daily_scheduler`:

```python
def enqueue_nightly_chain(queue: Queue, now: datetime) -> None:
    from pae.miner.job import mine_patterns_job
    from pae.proposer.job import propose_job
    from pae.shadow.job import shadow_eval_job

    mine = queue.enqueue(mine_patterns_job, job_id=f"mine-{now:%Y%m%d}")
    propose = queue.enqueue(propose_job, job_id=f"propose-{now:%Y%m%d}", depends_on=mine)
    shadow = queue.enqueue(shadow_eval_job, job_id=f"shadow-{now:%Y%m%d}", depends_on=propose)
    log.info("nightly_chain_enqueued", mine=mine.id, propose=propose.id, shadow=shadow.id)
```

`start_daily_scheduler`'s inner `enqueue` becomes `enqueue_nightly_chain(queue, now)`; keep
the docstring's double-enqueue-harmless argument (all three jobs are idempotent upserts).

- [ ] **Step 3: Verify pass + lint; commit**

```bash
git add src/pae/worker/scheduler.py tests/miner/test_scheduler.py
git commit -m "feat(worker): nightly mine->propose->shadow job chain"
```

---

### Task 11: Web UI

**Files:**
- Create: `src/pae/proposer/repo.py`, `src/pae/api/proposals.py`, `src/pae/api/viz.py`,
  `src/pae/api/templates/base.html`, `.../proposals_list.html`, `.../proposal_detail.html`
- Modify: `src/pae/api/app.py`, `pyproject.toml` (deps)
- Test: `tests/api/__init__.py` (empty), `tests/api/test_proposals_ui.py`

**Interfaces:**
- Consumes: `Proposal`/`ShadowResult` models; settings `shadow_ready_*`.
- Produces:
  - `pae.proposer.repo`: `list_proposals(status: str) -> list[dict]`,
    `get_proposal(proposal_id: int) -> dict | None`,
    `shadow_history(proposal_id: int, days: int = 30) -> list[dict]`
    (`{day, expected_fires, human_matches, human_total}` ascending),
    `set_status(proposal_id: int, status: str, reason: str | None = None) -> bool`
    (also flips source patterns: `rejected` → patterns `rejected`; each returns plain
    dicts so the UI layer never touches the ORM). All use a module-level
    `@lru_cache get_engine()`.
  - `pae.api.viz.sparkbar(values: list[float]) -> str` — inline `<svg>` (one bar per
    value, 0..1 scaled, no external assets)
  - `ready(history, *, min_days, min_precision, min_coverage) -> bool` in repo:
    ≥`min_days` rows AND rolling precision/coverage over the last 14 rows ≥ thresholds.
  - Routes: `GET /proposals` (`?status=shadowing` default), `GET /proposals/{id}`,
    `POST /proposals/{id}/approve`, `POST /proposals/{id}/reject` (form field `reason`,
    optional) — POSTs redirect (303) to the detail page.

- [ ] **Step 1: Add deps**

```bash
uv add jinja2 pyyaml
```

- [ ] **Step 2: `tests/api/test_proposals_ui.py` (write, watch fail)**

UI tests monkeypatch the repo module — no database.

```python
from datetime import date

import pytest
from fastapi.testclient import TestClient

import pae.proposer.repo as repo
from pae.api.app import create_app

PROPOSAL = {
    "id": 1,
    "group_key": "tod:weekday:14:abc",
    "kind": "time_of_day",
    "title": "Morning lights",
    "rationale": "Observed daily.",
    "automation_json": {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    },
    "source_pattern_keys": ["tod:switch.a:on:weekday:14"],
    "entity_ids": ["switch.a"],
    "model_name": "m",
    "prompt_version": 1,
    "status": "shadowing",
    "reject_reason": None,
    "friendly_names": {"switch.a": "Patio Lights"},
}
HISTORY = [
    {"day": date(2026, 7, d), "expected_fires": 1, "human_matches": 1, "human_total": 1}
    for d in range(10, 28)
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(repo, "list_proposals", lambda status: [PROPOSAL])
    monkeypatch.setattr(repo, "get_proposal", lambda pid: PROPOSAL if pid == 1 else None)
    monkeypatch.setattr(repo, "shadow_history", lambda pid, days=30: HISTORY)
    calls = []
    monkeypatch.setattr(
        repo, "set_status", lambda pid, status, reason=None: calls.append((pid, status, reason))
    )
    c = TestClient(create_app())
    c.calls = calls
    return c


def test_list_page_renders(client):
    r = client.get("/proposals")
    assert r.status_code == 200
    assert "Morning lights" in r.text
    assert "Patio Lights" in r.text


def test_detail_page_shows_yaml_and_scores(client):
    r = client.get("/proposals/1")
    assert r.status_code == 200
    assert "platform: time" in r.text  # YAML rendering
    assert "precision" in r.text.lower()


def test_missing_proposal_404(client):
    assert client.get("/proposals/999").status_code == 404


def test_approve_posts_and_redirects(client):
    r = client.post("/proposals/1/approve", follow_redirects=False)
    assert r.status_code == 303
    assert client.calls == [(1, "approved", None)]


def test_reject_with_reason(client):
    client.post("/proposals/1/reject", data={"reason": "not useful"}, follow_redirects=False)
    assert client.calls == [(1, "rejected", "not useful")]


def test_sparkbar_svg():
    from pae.api.viz import sparkbar

    svg = sparkbar([0.0, 0.5, 1.0])
    assert svg.startswith("<svg") and svg.count("<rect") == 3
```

- [ ] **Step 3: Implement**

`src/pae/proposer/repo.py` — plain-SQL layer (SQLAlchemy Core over the ORM tables),
returning dicts as specified; `set_status` updates `proposals.status/reject_reason/
updated_at` and, when `rejected`, sets the source patterns' `status='rejected'`
(`WHERE pattern_key = ANY(source_pattern_keys) AND status = 'proposed'`).
`list_proposals` joins `entity_registry` to build `friendly_names` and attaches
`precision14`/`coverage14`/`ready` computed from the last 14 `shadow_results` rows.
`ready(history, *, min_days, min_precision, min_coverage)` is a pure function in the
same module (unit-testable; the fixture above bypasses it, the live check is Task 12's).

`src/pae/api/viz.py`:

```python
"""Dependency-free inline-SVG sparkbars for the proposals UI."""


def sparkbar(values: list[float], width_per_bar: int = 6, height: int = 20) -> str:
    bars = []
    for i, v in enumerate(values):
        v = max(0.0, min(1.0, v))
        h = max(1, round(v * height))
        bars.append(
            f'<rect x="{i * width_per_bar}" y="{height - h}" '
            f'width="{width_per_bar - 1}" height="{h}" />'
        )
    return (
        f'<svg class="spark" width="{len(values) * width_per_bar}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">' + "".join(bars) + "</svg>"
    )
```

`src/pae/api/proposals.py` — `APIRouter`, Jinja2 `Environment(loader=PackageLoader
("pae.api", "templates"), autoescape=True)`; sync `def` route handlers (FastAPI thread
pool) calling `repo.*`; YAML block via `yaml.safe_dump(automation_json, sort_keys=False)`;
`GET /proposals` passes `status` query param (default `"shadowing"`) and tab counts;
POST handlers call `repo.set_status` then `RedirectResponse(url, status_code=303)`.
Templates: `base.html` (dark, system font stack, no external assets — same spirit as the
Grafana-adjacent tooling), `proposals_list.html` (status tabs + table with sparkbars via
`{{ spark|safe }}`), `proposal_detail.html` (title, rationale, `<pre>` YAML, source
pattern list, shadow table, approve/reject forms). Keep templates under ~80 lines each.

`src/pae/api/app.py` — after the Instrumentator line:

```python
    from pae.api.proposals import router as proposals_router

    app.include_router(proposals_router)
```

- [ ] **Step 4: Verify pass + lint; commit**

```bash
git add pyproject.toml uv.lock src/pae/proposer/repo.py src/pae/api/ tests/api/
git commit -m "feat(api): proposals review UI (list/detail/approve/reject)"
```

---

### Task 12: Docs, full verification, deploy, live checks

**Files:**
- Modify: `README.md` (status line + Phase 3 section), `CLAUDE.md` (commands + invariants),
  `.env.example` (LLM model vars, commented)

- [ ] **Step 1: Docs.** README: flip status to Phase 3, add a "Proposals (Phase 3)" section
  describing generation, shadow scores (precision/coverage), lifecycle, `pae propose` /
  `pae shadow`, and `/proposals` UI. CLAUDE.md: add `pae propose|shadow` to Commands; add
  invariants — "proposer/UI are the only writers of `patterns.status`", "LLM client refuses
  `:cloud` models", "shadow evaluator simulates weekday conditions only". `.env.example`:
  commented `LLM_MODEL_PRIMARY` / `LLM_MODEL_FALLBACK` / `MINER_LATITUDE` / `MINER_LONGITUDE`.

- [ ] **Step 2: Full suite + guard check**

```bash
uv run pytest -q
uv run ruff check src tests scripts
git diff 5ad21b7..HEAD -- src/pae/ha/client.py   # MUST be empty (zero HA-write surface)
```

- [ ] **Step 3: Deploy + migrate**

```bash
sudo docker compose up -d --build worker api ingester
sudo docker compose exec -T db psql -U pae -d pae -c "select version_num from alembic_version;"
# expect 0003 (ingester runs migrate on start)
```

- [ ] **Step 4: Live generation run (operator-visible)**

```bash
sudo docker compose exec worker pae propose
sudo docker compose exec worker pae shadow
sudo docker compose exec -T db psql -U pae -d pae -c \
  "select id, kind, title, status, entity_ids from proposals order by id;"
sudo docker compose exec -T db psql -U pae -d pae -c \
  "select proposal_id, count(*), sum(expected_fires), sum(human_matches) \
   from shadow_results group by proposal_id;"
```

Verify: no proposal references dusk-to-dawn/Pentair entities (sched-flagged);
`patterns.status` only changed for proposed patterns; UI reachable at
`http://<host>:8000/proposals`; worker metrics on :9100 include `pae_llm_requests_total`
and `pae_proposals`.

- [ ] **Step 5: Commit docs + report to operator for the phase-acceptance soak**

```bash
git add README.md CLAUDE.md .env.example
git commit -m "docs: Phase 3 proposals + shadow evaluation"
git push
```

Then: several nights of unattended chain runs before the acceptance conversation
(spec's "Acceptance" section).

---

## Self-Review Notes

- Spec coverage: migration/models (T1), settings (T2), gates incl. sibling rule (T3),
  grouping/group_key (T4), schema+validation incl. hostile payloads (T5), client incl.
  `:cloud` guard + failover (T6), prompt v1 (T7), generation service with
  decline/retry/staleness/revive + metrics + CLI (T8), shadow evaluator with sun/DST/
  pair-window semantics + backfill (T9), nightly chain (T10), UI (T11), docs/deploy/live
  acceptance prep (T12). Spec's "one `-m live` Ollama round-trip test" is intentionally
  folded into Task 12's live `pae propose` run — a live pytest would duplicate it.
- Shadow `human_matches` ambiguity (fires-matched vs events-covered) resolved: store
  fires-matched; documented in Task 1 docstring and Task 9 semantics.
- Type consistency: `RegistryInfo` defined once (T7) and consumed by T8;
  `_parse_hms`/`_parse_offset` defined in T5's schema module and imported by T9;
  `enqueue_nightly_chain` name matches T10 test; repo function names match T11 routes.
