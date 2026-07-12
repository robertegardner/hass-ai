# Phase 2: Statistical Pattern Miner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mine the ingested event stream nightly for human behavioral patterns (recurring time-of-day actions and trigger→action pairs), scored with support/confidence/lift/temporal-consistency, stored in a `patterns` table, inspectable via `pae mine` / `pae patterns list` and a second Grafana dashboard.

**Architecture:** Pure-Python miners (`src/pae/miner/tod.py`, `src/pae/miner/pairs.py`) operate on in-memory event lists so they are exhaustively unit-testable; a thin sync-SQLAlchemy service loads the lookback window, runs both miners, and upserts results keyed on a deterministic `pattern_key`. A dependency-free daemon thread in the worker enqueues the RQ mining job nightly. No LLM, no HA writes — the read-only whitelist is untouched.

**Tech Stack:** Python 3.13, SQLAlchemy 2 (sync engine for the job), Alembic, RQ 2, TimescaleDB/Postgres 16, `holidays` (already a dependency), Grafana 13.

## Global Constraints

- **No writes to Home Assistant.** `ALLOWED_OUTBOUND_TYPES` in `src/pae/ha/client.py` must not change.
- **Precision over recall**: thresholds filter aggressively; suspicious patterns get flagged, never silently promoted.
- Externally-scheduled devices (e.g. `switch.pool_2`, driven by the Pentair controller at fixed clock minutes) must be flagged `suspected_schedule`, not mined as human habits.
- Lint: `uv run ruff check src tests scripts` (line length 100). Tests: `uv run pytest`.
- No new runtime dependencies (no rq-scheduler; scheduler is a stdlib thread).
- HA local timezone is `America/Chicago`; mining reasons about local time-of-day.
- All new SQL bulk writes chunked (Postgres 65,535 bind-param limit) — pattern counts are small, chunk at 500 rows anyway.
- Commit after each task with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Miner config, metrics, and data types

**Files:**
- Modify: `src/pae/config.py` (add miner settings block after the ingestion block)
- Modify: `src/pae/metrics.py` (append miner metrics)
- Create: `src/pae/miner/__init__.py` (empty)
- Create: `src/pae/miner/types.py`
- Test: `tests/test_config.py` (append), `tests/miner/__init__.py` (empty), `tests/miner/test_types.py`

**Interfaces:**
- Produces: `Settings.miner_*` fields; `MinedEvent(time, entity_id, domain, old_state, new_state, triggered_by, user_id)`; `PatternCandidate` dataclass with `as_row(mined_at) -> dict`; metrics `MINER_RUNS`, `MINER_RUN_SECONDS`, `MINER_LAST_SUCCESS`, `MINER_PATTERNS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_miner_defaults():
    from pae.config import Settings

    s = Settings(_env_file=None)
    assert s.miner_lookback_days == 60
    assert s.miner_run_hour_utc == 9  # 03:00/04:00 America/Chicago
    assert s.miner_local_tz == "America/Chicago"
    assert s.miner_min_occurrences == 4
    assert s.miner_tod_min_support == 0.5
    assert s.miner_tod_tolerance_minutes == 45.0
    assert s.miner_schedule_std_minutes == 2.0
    assert s.miner_pair_window_minutes == 5.0
    assert s.miner_pair_min_confidence == 0.6
    assert s.miner_pair_min_lift == 3.0
```

Create `tests/miner/__init__.py` (empty) and `tests/miner/test_types.py`:

```python
from datetime import UTC, datetime

from pae.miner.types import PatternCandidate


def _candidate() -> PatternCandidate:
    t = datetime(2026, 7, 10, 3, 12, tzinfo=UTC)
    return PatternCandidate(
        kind="time_of_day",
        pattern_key="tod:light.bar:off:weekday:44",
        entity_id="light.bar",
        action="off",
        day_type="weekday",
        trigger_entity_id=None,
        trigger_state=None,
        tod_minutes=1332.0,
        tod_std_minutes=4.0,
        support=1.0,
        confidence=1.0,
        lift=16.0,
        temporal_consistency=1.0,
        occurrences=5,
        days_observed=5,
        suspected_schedule=False,
        evidence={"sample_times": [t.isoformat()]},
        first_seen=t,
        last_seen=t,
    )


def test_as_row_matches_column_names():
    mined_at = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
    row = _candidate().as_row(mined_at)
    assert row["pattern_key"] == "tod:light.bar:off:weekday:44"
    assert row["mined_at"] == mined_at
    assert "status" not in row  # status lifecycle is owned by later phases
    assert "id" not in row
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py::test_miner_defaults tests/miner/test_types.py -v`
Expected: FAIL (`AttributeError: miner_lookback_days` / `ModuleNotFoundError: pae.miner`)

- [ ] **Step 3: Implement**

In `src/pae/config.py`, append inside `Settings` after the ingestion block:

```python
    # miner (Phase 2)
    miner_lookback_days: int = 60
    miner_run_hour_utc: int = 9  # nightly mining job; 03:00/04:00 America/Chicago
    miner_local_tz: str = "America/Chicago"
    miner_min_occurrences: int = 4
    miner_tod_min_support: float = 0.5
    miner_tod_tolerance_minutes: float = 45.0
    miner_schedule_std_minutes: float = 2.0
    miner_pair_window_minutes: float = 5.0
    miner_pair_min_confidence: float = 0.6
    miner_pair_min_lift: float = 3.0
```

Append to `src/pae/metrics.py`:

```python
MINER_RUNS = Counter("pae_miner_runs_total", "Pattern-mining runs", ["status"])
MINER_RUN_SECONDS = Gauge("pae_miner_run_seconds", "Duration of the last mining run")
MINER_LAST_SUCCESS = Gauge(
    "pae_miner_last_success_timestamp", "Unix time of the last successful mining run"
)
MINER_PATTERNS = Gauge(
    "pae_miner_patterns", "Patterns currently in the patterns table", ["kind"]
)
```

Create `src/pae/miner/__init__.py` (empty) and `src/pae/miner/types.py`:

```python
"""Data types shared by the pattern miners.

``MinedEvent`` field order matches the SELECT in ``pae.miner.service.load_events``.
``PatternCandidate`` field names match ``pae.db.models.Pattern`` column names so
``as_row`` can feed a bulk upsert directly.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

ACTIONABLE_DOMAINS = frozenset(
    {"light", "switch", "climate", "cover", "lock", "fan", "media_player"}
)


@dataclass(frozen=True)
class MinedEvent:
    time: datetime  # tz-aware UTC
    entity_id: str
    domain: str
    old_state: str | None
    new_state: str | None
    triggered_by: str
    user_id: str | None


@dataclass(frozen=True)
class PatternCandidate:
    kind: str  # time_of_day | event_pair
    pattern_key: str
    entity_id: str
    action: str
    day_type: str | None
    trigger_entity_id: str | None
    trigger_state: str | None
    tod_minutes: float | None
    tod_std_minutes: float | None
    support: float
    confidence: float
    lift: float
    temporal_consistency: float | None
    occurrences: int
    days_observed: int
    suspected_schedule: bool
    evidence: dict[str, Any]
    first_seen: datetime
    last_seen: datetime

    def as_row(self, mined_at: datetime) -> dict[str, Any]:
        return {**asdict(self), "mined_at": mined_at}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/miner -v` — Expected: PASS
Run: `uv run ruff check src tests scripts` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add src/pae/config.py src/pae/metrics.py src/pae/miner tests/miner tests/test_config.py
git commit -m "feat(miner): config, metrics, and shared data types for Phase 2"
```

---

### Task 2: Circular time-of-day statistics

**Files:**
- Create: `src/pae/miner/stats.py`
- Test: `tests/miner/test_stats.py`

**Interfaces:**
- Produces: `minutes_of_day(dt, tz) -> float`; `circular_diff(a, b) -> float`; `circular_mean(minutes) -> float`; `circular_std(minutes) -> float`; `cluster_minutes(minutes, gap=90.0) -> list[list[int]]` (clusters of *indexes* into the input); constant `DAY_MINUTES = 1440.0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/miner/test_stats.py`:

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from pae.miner.stats import (
    circular_diff,
    circular_mean,
    circular_std,
    cluster_minutes,
    minutes_of_day,
)

TZ = ZoneInfo("America/Chicago")


def test_minutes_of_day_converts_to_local_tz():
    # 03:12 UTC on a July date is 22:12 CDT the previous evening
    dt = datetime(2026, 7, 12, 3, 12, tzinfo=UTC)
    assert minutes_of_day(dt, TZ) == pytest.approx(22 * 60 + 12)


def test_circular_mean_simple():
    assert circular_mean([10.0, 20.0, 30.0]) == pytest.approx(20.0)


def test_circular_mean_wraps_midnight():
    mean = circular_mean([1430.0, 10.0])
    assert min(mean, 1440.0 - mean) == pytest.approx(0.0, abs=0.01)


def test_circular_diff_wraps():
    assert circular_diff(10.0, 1430.0) == pytest.approx(20.0)
    assert circular_diff(1430.0, 10.0) == pytest.approx(-20.0)


def test_circular_std_identical_is_zero():
    assert circular_std([100.0, 100.0, 100.0]) == pytest.approx(0.0)


def test_circular_std_opposite_is_capped():
    assert circular_std([0.0, 720.0]) == pytest.approx(360.0)


def test_cluster_minutes_splits_on_gap():
    clusters = cluster_minutes([60.0, 70.0, 80.0, 600.0, 610.0], gap=90.0)
    assert sorted(sorted(c) for c in clusters) == [[0, 1, 2], [3, 4]]


def test_cluster_minutes_merges_across_midnight():
    clusters = cluster_minutes([5.0, 1435.0, 700.0], gap=90.0)
    assert sorted(sorted(c) for c in clusters) == [[0, 1], [2]]


def test_cluster_minutes_empty():
    assert cluster_minutes([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/miner/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pae.miner.stats'`

- [ ] **Step 3: Implement**

Create `src/pae/miner/stats.py`:

```python
"""Circular time-of-day statistics for the pattern miner.

Times of day live on a 1440-minute circle: 23:55 and 00:05 are ten minutes
apart. All functions take minutes-of-day floats in [0, 1440).
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

DAY_MINUTES = 1440.0


def minutes_of_day(dt: datetime, tz: ZoneInfo) -> float:
    local = dt.astimezone(tz)
    return local.hour * 60 + local.minute + local.second / 60


def circular_diff(a: float, b: float) -> float:
    """Signed minimal distance a-b on the day circle, in (-720, 720]."""
    d = (a - b) % DAY_MINUTES
    return d - DAY_MINUTES if d > DAY_MINUTES / 2 else d


def circular_mean(minutes: list[float]) -> float:
    angles = [m / DAY_MINUTES * 2 * math.pi for m in minutes]
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    return (math.atan2(s, c) / (2 * math.pi) * DAY_MINUTES) % DAY_MINUTES


def circular_std(minutes: list[float]) -> float:
    """Circular standard deviation in minutes; capped at 360 when the times
    show no concentration at all (uniform/opposite)."""
    if len(minutes) < 2:
        return 0.0
    angles = [m / DAY_MINUTES * 2 * math.pi for m in minutes]
    s = sum(math.sin(a) for a in angles) / len(angles)
    c = sum(math.cos(a) for a in angles) / len(angles)
    r = math.hypot(s, c)
    if r < 1e-9:
        return 360.0
    if r >= 1.0:
        return 0.0
    std = math.sqrt(-2.0 * math.log(r)) / (2 * math.pi) * DAY_MINUTES
    return min(std, 360.0)


def cluster_minutes(minutes: list[float], gap: float = 90.0) -> list[list[int]]:
    """Group indexes of ``minutes`` into time-of-day clusters, splitting where
    consecutive sorted times are more than ``gap`` apart; the first and last
    cluster merge when the gap across midnight is within ``gap``."""
    if not minutes:
        return []
    order = sorted(range(len(minutes)), key=lambda i: minutes[i])
    clusters: list[list[int]] = [[order[0]]]
    for i in order[1:]:
        if minutes[i] - minutes[clusters[-1][-1]] > gap:
            clusters.append([i])
        else:
            clusters[-1].append(i)
    if len(clusters) > 1 and minutes[order[0]] + DAY_MINUTES - minutes[order[-1]] <= gap:
        clusters[0] = clusters.pop() + clusters[0]
    return clusters
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_stats.py -v` — Expected: PASS
Run: `uv run ruff check src tests scripts` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add src/pae/miner/stats.py tests/miner/test_stats.py
git commit -m "feat(miner): circular time-of-day statistics"
```

---

### Task 3: Time-of-day miner

**Files:**
- Create: `src/pae/miner/tod.py`
- Test: `tests/miner/test_tod.py`

**Interfaces:**
- Consumes: `MinedEvent`, `PatternCandidate`, `ACTIONABLE_DOMAINS` from `pae.miner.types`; stats functions from Task 2.
- Produces: `mine_time_of_day(events, *, tz, day_type_for, days_observed, min_support=0.5, min_occurrences=4, tolerance_minutes=45.0, cluster_gap_minutes=90.0, schedule_std_minutes=2.0) -> list[PatternCandidate]` where `day_type_for: Callable[[datetime], str]` and `days_observed: Mapping[str, int]` (day_type → number of observed days of that type).
- Metric definitions (documented in the module docstring): `support` = active days / observed days of the day_type; `confidence` = support; `temporal_consistency` = fraction of occurrences within ±tolerance of the circular mean; `lift` = consistency density vs. a uniform-over-the-day baseline; `suspected_schedule` = circular std ≤ `schedule_std_minutes` **and** no occurrence has a `user_id` (device-originated clockwork, e.g. the Pentair pool controller).

- [ ] **Step 1: Write the failing tests**

Create `tests/miner/test_tod.py`:

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pae.miner.tod import mine_time_of_day
from pae.miner.types import MinedEvent

TZ = ZoneInfo("America/Chicago")
BASE = datetime(2026, 7, 6, 0, 0, tzinfo=TZ)  # Monday


def day_type_for(dt: datetime) -> str:
    return "weekday" if dt.astimezone(TZ).weekday() < 5 else "weekend"


def ev(
    day: int,
    hh: int,
    mm: int,
    *,
    entity: str = "light.bar",
    state: str = "on",
    old: str = "off",
    triggered_by: str = "manual",
    user_id: str | None = None,
) -> MinedEvent:
    t = (BASE + timedelta(days=day)).replace(hour=hh, minute=mm)
    return MinedEvent(
        time=t.astimezone(UTC),
        entity_id=entity,
        domain=entity.split(".")[0],
        old_state=old,
        new_state=state,
        triggered_by=triggered_by,
        user_id=user_id,
    )


def mine(events, **kw):
    defaults = dict(
        tz=TZ,
        day_type_for=day_type_for,
        days_observed={"weekday": 5, "weekend": 2},
        min_support=0.5,
        min_occurrences=4,
        tolerance_minutes=45.0,
        schedule_std_minutes=2.0,
    )
    defaults.update(kw)
    return mine_time_of_day(events, **defaults)


def test_nightly_habit_is_mined():
    # Mon..Fri around 22:16, ±6 min of human jitter (std > schedule_std_minutes)
    events = [ev(d, 22, 10 + 3 * d) for d in range(5)]
    (p,) = mine(events)
    assert p.kind == "time_of_day"
    assert p.entity_id == "light.bar"
    assert p.action == "on"
    assert p.day_type == "weekday"
    assert p.support == pytest.approx(1.0)
    assert p.confidence == p.support
    assert p.temporal_consistency == pytest.approx(1.0)
    assert p.lift > 10
    assert p.occurrences == 5
    assert p.days_observed == 5
    assert not p.suspected_schedule
    assert p.pattern_key.startswith("tod:light.bar:on:weekday:")
    assert len(p.evidence["sample_times"]) == 5


def test_too_few_occurrences_filtered():
    assert mine([ev(d, 22, 10) for d in range(3)]) == []


def test_low_support_filtered():
    # 4 occurrences all on one weekday: support 1/5
    events = [ev(0, h, 0) for h in (21, 21, 21, 21)]
    assert mine(events) == []


def test_automation_and_nonactionable_ignored():
    auto = [ev(d, 22, 10, triggered_by="automation") for d in range(5)]
    sensor = [ev(d, 22, 10, entity="binary_sensor.motion") for d in range(5)]
    assert mine(auto + sensor) == []


def test_clockwork_device_flagged_as_schedule():
    events = [ev(d, 13, 2, entity="switch.pool_2") for d in range(5)]
    (p,) = mine(events)
    assert p.suspected_schedule
    assert p.tod_std_minutes == pytest.approx(0.0)


def test_clockwork_with_user_action_not_flagged():
    events = [ev(d, 13, 2, entity="switch.pool_2") for d in range(4)]
    events.append(ev(4, 13, 2, entity="switch.pool_2", user_id="83f9e619"))
    (p,) = mine(events)
    assert not p.suspected_schedule


def test_morning_and_evening_clusters_are_separate_patterns():
    events = [ev(d, 7, 30 + d) for d in range(5)] + [ev(d, 22, 10 + d) for d in range(5)]
    patterns = sorted(mine(events), key=lambda p: p.tod_minutes)
    assert len(patterns) == 2
    assert patterns[0].tod_minutes == pytest.approx(7 * 60 + 32, abs=1)
    assert patterns[1].tod_minutes == pytest.approx(22 * 60 + 12, abs=1)


def test_no_transition_ignored():
    events = [ev(d, 22, 10, old="on", state="on") for d in range(5)]
    assert mine(events) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/miner/test_tod.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pae.miner.tod'`

- [ ] **Step 3: Implement**

Create `src/pae/miner/tod.py`:

```python
"""Time-of-day pattern miner: recurring manual actions at consistent times.

Metrics per pattern:
- support: active days / observed days of the matching day_type
- confidence: equal to support (a time-of-day rule has no separate antecedent)
- temporal_consistency: fraction of occurrences within ±tolerance of the
  circular mean time
- lift: consistency density vs. a uniform-over-the-day baseline
- suspected_schedule: circular std ≤ schedule_std_minutes with no user_id on
  any occurrence — clockwork regularity with no app/UI touch is a device or
  external controller schedule (e.g. Pentair), not a human habit
"""
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from pae.miner.stats import (
    DAY_MINUTES,
    circular_diff,
    circular_mean,
    circular_std,
    cluster_minutes,
    minutes_of_day,
)
from pae.miner.types import ACTIONABLE_DOMAINS, MinedEvent, PatternCandidate


def mine_time_of_day(
    events: Sequence[MinedEvent],
    *,
    tz: ZoneInfo,
    day_type_for: Callable[[datetime], str],
    days_observed: Mapping[str, int],
    min_support: float = 0.5,
    min_occurrences: int = 4,
    tolerance_minutes: float = 45.0,
    cluster_gap_minutes: float = 90.0,
    schedule_std_minutes: float = 2.0,
) -> list[PatternCandidate]:
    groups: dict[tuple[str, str, str], list[MinedEvent]] = {}
    for e in events:
        if e.triggered_by != "manual" or e.domain not in ACTIONABLE_DOMAINS:
            continue
        if not e.new_state or e.new_state == e.old_state:
            continue
        groups.setdefault((e.entity_id, e.new_state, day_type_for(e.time)), []).append(e)

    out: list[PatternCandidate] = []
    for (entity_id, action, day_type), evs in groups.items():
        total_days = days_observed.get(day_type, 0)
        if total_days == 0:
            continue
        minute_vals = [minutes_of_day(e.time, tz) for e in evs]
        for idx_cluster in cluster_minutes(minute_vals, gap=cluster_gap_minutes):
            cevs = [evs[i] for i in idx_cluster]
            cmins = [minute_vals[i] for i in idx_cluster]
            if len(cevs) < min_occurrences:
                continue
            active_days = {e.time.astimezone(tz).date() for e in cevs}
            support = len(active_days) / total_days
            if support < min_support:
                continue
            mean = circular_mean(cmins)
            std = circular_std(cmins)
            within = sum(1 for m in cmins if abs(circular_diff(m, mean)) <= tolerance_minutes)
            consistency = within / len(cmins)
            lift = consistency / (2 * tolerance_minutes / DAY_MINUTES)
            suspected = std <= schedule_std_minutes and all(e.user_id is None for e in cevs)
            times = sorted(e.time for e in cevs)
            out.append(
                PatternCandidate(
                    kind="time_of_day",
                    pattern_key=f"tod:{entity_id}:{action}:{day_type}:{int(mean // 30):02d}",
                    entity_id=entity_id,
                    action=action,
                    day_type=day_type,
                    trigger_entity_id=None,
                    trigger_state=None,
                    tod_minutes=round(mean, 1),
                    tod_std_minutes=round(std, 1),
                    support=round(support, 4),
                    confidence=round(support, 4),
                    lift=round(lift, 2),
                    temporal_consistency=round(consistency, 4),
                    occurrences=len(cevs),
                    days_observed=total_days,
                    suspected_schedule=suspected,
                    evidence={"sample_times": [t.isoformat() for t in times[-10:]]},
                    first_seen=times[0],
                    last_seen=times[-1],
                )
            )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_tod.py -v` — Expected: PASS
Run: `uv run ruff check src tests scripts` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add src/pae/miner/tod.py tests/miner/test_tod.py
git commit -m "feat(miner): time-of-day pattern miner with schedule flagging"
```

---

### Task 4: Event-pair miner

**Files:**
- Create: `src/pae/miner/pairs.py`
- Test: `tests/miner/test_pairs.py`

**Interfaces:**
- Consumes: `MinedEvent`, `PatternCandidate`, `ACTIONABLE_DOMAINS` from `pae.miner.types`.
- Produces: `mine_event_pairs(events, *, tz, days_observed_total, span_minutes, window_minutes=5.0, min_gap_seconds=2.0, min_pairs=4, min_confidence=0.6, min_lift=3.0) -> list[PatternCandidate]`.
- Metric definitions: antecedent A = any state transition (any `triggered_by`); consequent B = a **manual** transition in an actionable domain within `window_minutes` after A, at least `min_gap_seconds` later (near-simultaneous events are state mirrors / group fan-out, not causation). `confidence` = distinct A occurrences followed by B / all A occurrences; `lift` = confidence / (P(B) in a random window) with `P(B) = n_B * window_minutes / span_minutes`; `support` = distinct days with the pair / days_observed_total.

- [ ] **Step 1: Write the failing tests**

Create `tests/miner/test_pairs.py`:

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pae.miner.pairs import mine_event_pairs
from pae.miner.types import MinedEvent

TZ = ZoneInfo("America/Chicago")
BASE = datetime(2026, 7, 6, 0, 0, tzinfo=TZ)  # Monday


def ev(
    day: int,
    hh: int,
    mm: int,
    ss: int = 0,
    *,
    entity: str,
    state: str = "on",
    old: str = "off",
    triggered_by: str = "manual",
) -> MinedEvent:
    t = (BASE + timedelta(days=day)).replace(hour=hh, minute=mm, second=ss)
    return MinedEvent(
        time=t.astimezone(UTC),
        entity_id=entity,
        domain=entity.split(".")[0],
        old_state=old,
        new_state=state,
        triggered_by=triggered_by,
        user_id=None,
    )


def mine(events, **kw):
    defaults = dict(
        tz=TZ,
        days_observed_total=6,
        span_minutes=6 * 1440.0,
        window_minutes=5.0,
        min_gap_seconds=2.0,
        min_pairs=4,
        min_confidence=0.6,
        min_lift=3.0,
    )
    defaults.update(kw)
    return mine_event_pairs(events, **defaults)


def tv_then_light(days: int) -> list[MinedEvent]:
    out = []
    for d in range(days):
        out.append(ev(d, 19, 0, entity="media_player.tv", state="playing", old="idle"))
        out.append(ev(d, 19, 0, 30, entity="light.den", state="on"))
    return out


def test_trigger_then_action_is_mined():
    events = tv_then_light(5)
    # one TV-on without the light, so confidence is 5/6
    events.append(ev(5, 19, 0, entity="media_player.tv", state="playing", old="idle"))
    (p,) = mine(events)
    assert p.kind == "event_pair"
    assert p.trigger_entity_id == "media_player.tv"
    assert p.trigger_state == "playing"
    assert p.entity_id == "light.den"
    assert p.action == "on"
    assert p.confidence == pytest.approx(5 / 6, abs=0.01)
    assert p.lift > 3.0
    assert p.occurrences == 5
    assert p.support == pytest.approx(5 / 6, abs=0.01)
    assert p.pattern_key == "pair:media_player.tv:playing->light.den:on"


def test_too_few_pairs_filtered():
    assert mine(tv_then_light(3)) == []


def test_low_confidence_filtered():
    events = tv_then_light(4)
    for d in range(4):  # 12 extra TV-ons with no light: confidence 4/16
        for hh in (9, 12, 15):
            events.append(ev(d, hh, 0, entity="media_player.tv", state="playing", old="idle"))
    assert mine(events) == []


def test_same_entity_excluded():
    events = []
    for d in range(5):
        events.append(ev(d, 19, 0, entity="light.den", state="on"))
        events.append(ev(d, 19, 1, entity="light.den", state="off", old="on"))
    assert mine(events) == []


def test_near_simultaneous_mirror_excluded():
    events = []
    for d in range(5):
        events.append(ev(d, 19, 0, 0, entity="switch.fan", state="on"))
        events.append(ev(d, 19, 0, 1, entity="fan.fan", state="on"))
    assert mine(events) == []


def test_automation_consequent_excluded():
    events = []
    for d in range(5):
        events.append(ev(d, 19, 0, entity="media_player.tv", state="playing", old="idle"))
        events.append(ev(d, 19, 0, 30, entity="light.den", state="on", triggered_by="automation"))
    assert mine(events) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/miner/test_pairs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pae.miner.pairs'`

- [ ] **Step 3: Implement**

Create `src/pae/miner/pairs.py`:

```python
"""Event-pair miner: a manual action B reliably follows event A within a window.

The antecedent A is any state transition (sensor, automation, or manual); the
consequent B must be a manual transition in an actionable domain, at least
``min_gap_seconds`` after A (near-simultaneous changes are state mirrors or
group fan-out, not causation).

Metrics per pattern:
- confidence: distinct A occurrences followed by B / all A occurrences
- lift: confidence / P(B lands in a random window of the same width)
- support: distinct days on which the pair occurred / days observed
"""
from collections import Counter
from collections.abc import Sequence
from datetime import timedelta
from zoneinfo import ZoneInfo

from pae.miner.types import ACTIONABLE_DOMAINS, MinedEvent, PatternCandidate


def mine_event_pairs(
    events: Sequence[MinedEvent],
    *,
    tz: ZoneInfo,
    days_observed_total: int,
    span_minutes: float,
    window_minutes: float = 5.0,
    min_gap_seconds: float = 2.0,
    min_pairs: int = 4,
    min_confidence: float = 0.6,
    min_lift: float = 3.0,
) -> list[PatternCandidate]:
    if days_observed_total <= 0 or span_minutes <= 0:
        return []
    transitions = [
        e
        for e in sorted(events, key=lambda e: e.time)
        if e.new_state and e.new_state != e.old_state
    ]
    n_a = Counter((e.entity_id, e.new_state) for e in transitions)
    n_b = Counter(
        (e.entity_id, e.new_state)
        for e in transitions
        if e.triggered_by == "manual" and e.domain in ACTIONABLE_DOMAINS
    )
    window = timedelta(minutes=window_minutes)
    min_gap = timedelta(seconds=min_gap_seconds)

    hits: dict[tuple[str, str, str, str], dict] = {}
    for i, b in enumerate(transitions):
        if b.triggered_by != "manual" or b.domain not in ACTIONABLE_DOMAINS:
            continue
        seen: set[tuple[str, str]] = set()
        j = i - 1
        while j >= 0:
            a = transitions[j]
            gap = b.time - a.time
            if gap > window:
                break
            j -= 1
            if gap < min_gap or a.entity_id == b.entity_id:
                continue
            akey = (a.entity_id, a.new_state)
            if akey in seen:  # count only the nearest occurrence per antecedent
                continue
            seen.add(akey)
            rec = hits.setdefault(
                (a.entity_id, a.new_state, b.entity_id, b.new_state),
                {"a_idx": set(), "days": set(), "times": []},
            )
            rec["a_idx"].add(j + 1)
            rec["days"].add(b.time.astimezone(tz).date())
            rec["times"].append(b.time)

    out: list[PatternCandidate] = []
    for (a_ent, a_state, b_ent, b_state), rec in hits.items():
        n_ab = len(rec["a_idx"])
        if n_ab < min_pairs:
            continue
        confidence = n_ab / n_a[(a_ent, a_state)]
        if confidence < min_confidence:
            continue
        p_b = n_b[(b_ent, b_state)] * window_minutes / span_minutes
        lift = confidence / p_b if p_b > 0 else 0.0
        if lift < min_lift:
            continue
        times = sorted(rec["times"])
        out.append(
            PatternCandidate(
                kind="event_pair",
                pattern_key=f"pair:{a_ent}:{a_state}->{b_ent}:{b_state}",
                entity_id=b_ent,
                action=b_state,
                day_type=None,
                trigger_entity_id=a_ent,
                trigger_state=a_state,
                tod_minutes=None,
                tod_std_minutes=None,
                support=round(len(rec["days"]) / days_observed_total, 4),
                confidence=round(confidence, 4),
                lift=round(lift, 2),
                temporal_consistency=None,
                occurrences=n_ab,
                days_observed=days_observed_total,
                suspected_schedule=False,
                evidence={
                    "sample_times": [t.isoformat() for t in times[-10:]],
                    "n_a": n_a[(a_ent, a_state)],
                    "n_b": n_b[(b_ent, b_state)],
                },
                first_seen=times[0],
                last_seen=times[-1],
            )
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_pairs.py -v` — Expected: PASS
Run: `uv run ruff check src tests scripts` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add src/pae/miner/pairs.py tests/miner/test_pairs.py
git commit -m "feat(miner): event-pair miner (trigger -> manual action)"
```

---

### Task 5: Pattern model and migration 0002

**Files:**
- Modify: `src/pae/db/models.py` (add `Boolean` to the sqlalchemy import; append `Pattern` class)
- Create: `src/pae/db/migrations/versions/0002_patterns.py`
- Test: `tests/miner/test_pattern_model.py`

**Interfaces:**
- Produces: `pae.db.models.Pattern` ORM class, table `patterns`, unique index `ux_patterns_key` on `pattern_key`. Column names exactly match `PatternCandidate` fields plus `id`, `status` (server default `'candidate'`), `mined_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/miner/test_pattern_model.py`:

```python
from dataclasses import fields

from pae.db.models import Pattern
from pae.miner.types import PatternCandidate


def test_candidate_fields_match_pattern_columns():
    cols = {c.name for c in Pattern.__table__.columns}
    for f in fields(PatternCandidate):
        assert f.name in cols, f"PatternCandidate.{f.name} has no patterns column"
    assert {"id", "status", "mined_at"} <= cols


def test_pattern_key_is_unique():
    assert any(
        idx.unique and [c.name for c in idx.columns] == ["pattern_key"]
        for idx in Pattern.__table__.indexes
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/miner/test_pattern_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'Pattern'`

- [ ] **Step 3: Implement**

In `src/pae/db/models.py` change the sqlalchemy import line to:

```python
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Text
```

Append to `src/pae/db/models.py`:

```python
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
```

Create `src/pae/db/migrations/versions/0002_patterns.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_pattern_model.py -v` — Expected: PASS
Run: `uv run pytest && uv run ruff check src tests scripts` — Expected: all green

- [ ] **Step 5: Commit**

```bash
git add src/pae/db/models.py src/pae/db/migrations/versions/0002_patterns.py tests/miner/test_pattern_model.py
git commit -m "feat(db): patterns table, model, and migration 0002"
```

---

### Task 6: Mining service and RQ job

**Files:**
- Create: `src/pae/miner/service.py`
- Create: `src/pae/miner/job.py`
- Test: `tests/miner/test_service.py`

**Interfaces:**
- Consumes: miners from Tasks 3–4, `Pattern` from Task 5, `Settings.miner_*`, miner metrics.
- Produces: `run_mining(now: datetime | None = None) -> MiningResult` (sync; creates its own engine); `MiningResult(events_loaded, days_observed, tod_patterns, pair_patterns)`; `day_type_of(d: date, us_holidays) -> str`; `reconcile_keys(candidates, existing, tolerance_minutes=45.0) -> list[PatternCandidate]`; `dedupe_candidates(candidates) -> list[PatternCandidate]`; `mine_patterns_job() -> dict` in `pae.miner.job` (the dotted path the worker imports).
- Key-stability rationale: the tod `pattern_key` embeds a 30-minute bucket of the circular mean. Without reconciliation, a habit whose mean drifts across a bucket boundary between nightly runs would insert a duplicate row and orphan its `status` lifecycle. `reconcile_keys` makes candidates adopt the `pattern_key` of an existing time_of_day pattern with the same (entity_id, action, day_type) whose stored `tod_minutes` is within tolerance (nearest candidate claims each existing key exactly once). `dedupe_candidates` collapses any remaining duplicate keys (keep the candidate with more occurrences) so a single `INSERT ... ON CONFLICT` never touches one row twice.
- Upsert semantics: `INSERT ... ON CONFLICT (pattern_key) DO UPDATE` refreshing metrics/evidence/`mined_at`, keeping `least(first_seen)`, and **never touching `status`**. Patterns that stop being re-found are kept (their `mined_at` goes stale — visible on the dashboard); nothing is deleted.

- [ ] **Step 1: Write the failing tests** (pure parts only — DB paths are exercised live in Task 10)

Create `tests/miner/test_service.py`:

```python
from dataclasses import replace
from datetime import UTC, date, datetime

import holidays

from pae.miner.service import day_type_of, dedupe_candidates, reconcile_keys
from pae.miner.types import PatternCandidate

US = holidays.US()


def test_weekday():
    assert day_type_of(date(2026, 7, 8), US) == "weekday"  # Wednesday


def test_weekend():
    assert day_type_of(date(2026, 7, 11), US) == "weekend"  # Saturday


def test_holiday_counts_as_weekend():
    assert day_type_of(date(2026, 7, 3), US) == "weekend"  # July 4th observed


def tod_candidate(key: str, tod_minutes: float, occurrences: int = 5) -> PatternCandidate:
    t = datetime(2026, 7, 10, 3, 12, tzinfo=UTC)
    return PatternCandidate(
        kind="time_of_day",
        pattern_key=key,
        entity_id="light.bar",
        action="off",
        day_type="weekday",
        trigger_entity_id=None,
        trigger_state=None,
        tod_minutes=tod_minutes,
        tod_std_minutes=4.0,
        support=1.0,
        confidence=1.0,
        lift=16.0,
        temporal_consistency=1.0,
        occurrences=occurrences,
        days_observed=5,
        suspected_schedule=False,
        evidence={},
        first_seen=t,
        last_seen=t,
    )


def test_reconcile_adopts_existing_key_across_bucket_boundary():
    # mean drifted 20:59 -> 21:01: new bucket (42), but same habit as stored key
    cand = tod_candidate("tod:light.bar:off:weekday:42", 21 * 60 + 1)
    existing = [("tod:light.bar:off:weekday:41", "light.bar", "off", "weekday", 20 * 60 + 59.0)]
    (out,) = reconcile_keys([cand], existing)
    assert out.pattern_key == "tod:light.bar:off:weekday:41"
    assert out.tod_minutes == cand.tod_minutes  # only the key changes


def test_reconcile_keeps_key_when_no_match_within_tolerance():
    cand = tod_candidate("tod:light.bar:off:weekday:42", 21 * 60 + 1)
    existing = [("tod:light.bar:off:weekday:14", "light.bar", "off", "weekday", 7 * 60 + 30.0)]
    (out,) = reconcile_keys([cand], existing)
    assert out.pattern_key == "tod:light.bar:off:weekday:42"


def test_reconcile_nearest_candidate_claims_key_once():
    near = tod_candidate("tod:light.bar:off:weekday:42", 21 * 60 + 5)
    far = tod_candidate("tod:light.bar:off:weekday:43", 21 * 60 + 40)
    existing = [("tod:light.bar:off:weekday:42", "light.bar", "off", "weekday", 21 * 60 + 0.0)]
    out = {c.tod_minutes: c.pattern_key for c in reconcile_keys([far, near], existing)}
    assert out[21 * 60 + 5] == "tod:light.bar:off:weekday:42"
    assert out[21 * 60 + 40] == "tod:light.bar:off:weekday:43"


def test_reconcile_ignores_event_pair_candidates():
    cand = replace(
        tod_candidate("pair:a:on->b:on", 0.0), kind="event_pair", tod_minutes=None
    )
    existing = [("tod:light.bar:off:weekday:41", "light.bar", "off", "weekday", 0.0)]
    (out,) = reconcile_keys([cand], existing)
    assert out.pattern_key == "pair:a:on->b:on"


def test_dedupe_keeps_candidate_with_more_occurrences():
    a = tod_candidate("tod:light.bar:off:weekday:42", 100.0, occurrences=4)
    b = tod_candidate("tod:light.bar:off:weekday:42", 105.0, occurrences=7)
    (out,) = dedupe_candidates([a, b])
    assert out.occurrences == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/miner/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pae.miner.service'`

- [ ] **Step 3: Implement**

Create `src/pae/miner/service.py`:

```python
"""Mining orchestration: load the event window, run both miners, upsert patterns.

Runs synchronously (it is an RQ job); uses its own short-lived sync engine.
Read-only towards Home Assistant by construction — this module never imports
the HA client.
"""
import time as _time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pae.config import get_settings
from pae.db.models import Event, Pattern
from pae.logging import get_logger
from pae.metrics import MINER_LAST_SUCCESS, MINER_PATTERNS, MINER_RUN_SECONDS, MINER_RUNS
from pae.miner.pairs import mine_event_pairs
from pae.miner.stats import circular_diff
from pae.miner.tod import mine_time_of_day
from pae.miner.types import MinedEvent, PatternCandidate

log = get_logger(__name__)

UPSERT_CHUNK = 500


@dataclass
class MiningResult:
    events_loaded: int
    days_observed: int
    tod_patterns: int
    pair_patterns: int


def day_type_of(d: date, us_holidays: holidays.HolidayBase) -> str:
    return "weekend" if d.weekday() >= 5 or d in us_holidays else "weekday"


def load_events(conn: sa.Connection, since: datetime) -> list[MinedEvent]:
    rows = conn.execute(
        sa.select(
            Event.time,
            Event.entity_id,
            Event.domain,
            Event.old_state,
            Event.new_state,
            Event.triggered_by,
            Event.user_id,
        )
        .where(Event.time >= since)
        .order_by(Event.time)
    )
    return [MinedEvent(*row) for row in rows]


def reconcile_keys(
    candidates: list[PatternCandidate],
    existing: Sequence[tuple[str, str, str, str | None, float | None]],
    tolerance_minutes: float = 45.0,
) -> list[PatternCandidate]:
    """Make time-of-day candidates adopt the pattern_key of a previously mined
    pattern for the same habit (same entity/action/day_type, circular mean
    within tolerance). The tod pattern_key embeds a 30-minute bucket of the
    mean; without this, a habit drifting across a bucket boundary between
    nightly runs would insert a duplicate row and orphan its status lifecycle.
    ``existing`` rows are (pattern_key, entity_id, action, day_type, tod_minutes).
    """
    index: dict[tuple[str, str, str | None], list[tuple[str, float]]] = {}
    for key, entity_id, action, day_type, tod_minutes in existing:
        if tod_minutes is None:
            continue
        index.setdefault((entity_id, action, day_type), []).append((key, tod_minutes))

    scored: list[tuple[float, int, PatternCandidate, str | None]] = []
    for i, c in enumerate(candidates):
        best_key: str | None = None
        best_dist = tolerance_minutes
        if c.kind == "time_of_day" and c.tod_minutes is not None:
            for key, minutes in index.get((c.entity_id, c.action, c.day_type), []):
                dist = abs(circular_diff(c.tod_minutes, minutes))
                if dist <= best_dist:
                    best_key, best_dist = key, dist
        scored.append((best_dist if best_key else float("inf"), i, c, best_key))

    out: list[PatternCandidate] = []
    claimed: set[str] = set()
    for dist, _i, c, key in sorted(scored, key=lambda t: (t[0], t[1])):
        if key is not None and key not in claimed:
            claimed.add(key)
            out.append(replace(c, pattern_key=key))
        else:
            out.append(c)
    return out


def dedupe_candidates(candidates: list[PatternCandidate]) -> list[PatternCandidate]:
    """Collapse duplicate pattern_keys (keep the candidate with more
    occurrences) so a single INSERT ... ON CONFLICT never touches a row twice."""
    best: dict[str, PatternCandidate] = {}
    for c in candidates:
        cur = best.get(c.pattern_key)
        if cur is None or c.occurrences > cur.occurrences:
            best[c.pattern_key] = c
    return list(best.values())


def upsert_patterns(
    conn: sa.Connection, candidates: list[PatternCandidate], mined_at: datetime
) -> None:
    for start in range(0, len(candidates), UPSERT_CHUNK):
        chunk = candidates[start : start + UPSERT_CHUNK]
        stmt = pg_insert(Pattern).values([c.as_row(mined_at) for c in chunk])
        refresh = (
            "support",
            "confidence",
            "lift",
            "temporal_consistency",
            "tod_minutes",
            "tod_std_minutes",
            "occurrences",
            "days_observed",
            "suspected_schedule",
            "last_seen",
            "evidence",
            "mined_at",
        )
        set_ = {col: getattr(stmt.excluded, col) for col in refresh}
        set_["first_seen"] = sa.func.least(Pattern.first_seen, stmt.excluded.first_seen)
        conn.execute(stmt.on_conflict_do_update(index_elements=["pattern_key"], set_=set_))


def run_mining(now: datetime | None = None) -> MiningResult:
    settings = get_settings()
    now = now or datetime.now(UTC)
    tz = ZoneInfo(settings.miner_local_tz)
    us_holidays = holidays.US()
    engine = sa.create_engine(settings.db_url, pool_pre_ping=True)
    started = _time.monotonic()
    try:
        with engine.begin() as conn:
            since = now - timedelta(days=settings.miner_lookback_days)
            events = load_events(conn, since)
            if not events:
                log.info("mine_no_events", since=since.isoformat())
                MINER_RUNS.labels(status="ok").inc()
                MINER_LAST_SUCCESS.set_to_current_time()
                return MiningResult(0, 0, 0, 0)

            observed_dates = {e.time.astimezone(tz).date() for e in events}
            days_by_type = {"weekday": 0, "weekend": 0}
            for d in observed_dates:
                days_by_type[day_type_of(d, us_holidays)] += 1

            tod = mine_time_of_day(
                events,
                tz=tz,
                day_type_for=lambda dt: day_type_of(dt.astimezone(tz).date(), us_holidays),
                days_observed=days_by_type,
                min_support=settings.miner_tod_min_support,
                min_occurrences=settings.miner_min_occurrences,
                tolerance_minutes=settings.miner_tod_tolerance_minutes,
                schedule_std_minutes=settings.miner_schedule_std_minutes,
            )
            existing = conn.execute(
                sa.select(
                    Pattern.pattern_key,
                    Pattern.entity_id,
                    Pattern.action,
                    Pattern.day_type,
                    Pattern.tod_minutes,
                ).where(Pattern.kind == "time_of_day")
            ).all()
            tod = reconcile_keys(
                tod, existing, tolerance_minutes=settings.miner_tod_tolerance_minutes
            )
            span_minutes = max(
                (events[-1].time - events[0].time).total_seconds() / 60.0, 1.0
            )
            pairs = mine_event_pairs(
                events,
                tz=tz,
                days_observed_total=len(observed_dates),
                span_minutes=span_minutes,
                window_minutes=settings.miner_pair_window_minutes,
                min_pairs=settings.miner_min_occurrences,
                min_confidence=settings.miner_pair_min_confidence,
                min_lift=settings.miner_pair_min_lift,
            )
            upsert_patterns(conn, dedupe_candidates(tod + pairs), mined_at=now)
            counts = dict(
                conn.execute(
                    sa.select(Pattern.kind, sa.func.count()).group_by(Pattern.kind)
                ).all()
            )
        for kind in ("time_of_day", "event_pair"):
            MINER_PATTERNS.labels(kind=kind).set(counts.get(kind, 0))
        MINER_RUNS.labels(status="ok").inc()
        MINER_LAST_SUCCESS.set_to_current_time()
        result = MiningResult(len(events), len(observed_dates), len(tod), len(pairs))
        log.info(
            "mine_done",
            events=result.events_loaded,
            days=result.days_observed,
            tod=result.tod_patterns,
            pairs=result.pair_patterns,
        )
        return result
    except Exception:
        MINER_RUNS.labels(status="error").inc()
        log.exception("mine_failed")
        raise
    finally:
        MINER_RUN_SECONDS.set(_time.monotonic() - started)
        engine.dispose()
```

Create `src/pae/miner/job.py`:

```python
"""RQ job entry points — imported by the worker via dotted path."""
from pae.miner.service import run_mining


def mine_patterns_job() -> dict:
    result = run_mining()
    return result.__dict__
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_service.py -v` — Expected: PASS
Run: `uv run pytest && uv run ruff check src tests scripts` — Expected: all green

- [ ] **Step 5: Commit**

```bash
git add src/pae/miner/service.py src/pae/miner/job.py tests/miner/test_service.py
git commit -m "feat(miner): mining service with pattern upsert and RQ job entry"
```

---

### Task 7: Nightly scheduler in the worker

**Files:**
- Create: `src/pae/worker/scheduler.py`
- Modify: `src/pae/worker/main.py` (full rewrite below)
- Test: `tests/miner/test_scheduler.py`

**Interfaces:**
- Consumes: `mine_patterns_job` from Task 6.
- Produces: `seconds_until_next(hour_utc: int, now: datetime) -> float`; `run_daily(enqueue, hour_utc, stop, now_fn) -> None`; `start_daily_scheduler(queue, hour_utc) -> threading.Event` (returns the stop event; thread is a daemon named `pae-mine-scheduler`; enqueues with `job_id=f"mine-{date}"` so a same-day restart doesn't double-run).

- [ ] **Step 1: Write the failing tests**

Create `tests/miner/test_scheduler.py`:

```python
from datetime import UTC, datetime

import pytest

from pae.worker.scheduler import seconds_until_next


def test_target_later_today():
    now = datetime(2026, 7, 12, 5, 0, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(4 * 3600)


def test_target_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 7, 12, 10, 30, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(22.5 * 3600)


def test_exactly_at_target_rolls_to_tomorrow():
    now = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(24 * 3600)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/miner/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pae.worker.scheduler'`

- [ ] **Step 3: Implement**

Create `src/pae/worker/scheduler.py`:

```python
"""Tiny in-process daily scheduler: enqueue the nightly mining job.

Deliberately not rq-scheduler: one daemon thread sleeps until the next
HH:00 UTC and enqueues. The per-date job_id makes a same-day worker restart
idempotent (RQ replaces the job instead of duplicating it).
"""
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from rq import Queue

from pae.logging import get_logger

log = get_logger(__name__)


def seconds_until_next(hour_utc: int, now: datetime) -> float:
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_daily(
    enqueue: Callable[[datetime], None],
    hour_utc: int,
    stop: threading.Event,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    while not stop.is_set():
        delay = seconds_until_next(hour_utc, now_fn())
        if stop.wait(timeout=delay):
            return
        enqueue(now_fn())


def start_daily_scheduler(queue: Queue, hour_utc: int) -> threading.Event:
    from pae.miner.job import mine_patterns_job

    stop = threading.Event()

    def enqueue(now: datetime) -> None:
        job = queue.enqueue(mine_patterns_job, job_id=f"mine-{now:%Y%m%d}")
        log.info("mine_job_enqueued", job_id=job.id)

    thread = threading.Thread(
        target=run_daily, args=(enqueue, hour_utc, stop), daemon=True, name="pae-mine-scheduler"
    )
    thread.start()
    log.info("mine_scheduler_started", hour_utc=hour_utc)
    return stop
```

Rewrite `src/pae/worker/main.py` in full:

```python
from prometheus_client import start_http_server
from redis import Redis
from rq import Queue, Worker

import pae.metrics  # noqa: F401 — registers pae_* metrics in the default registry
from pae.config import get_settings
from pae.logging import get_logger
from pae.worker.scheduler import start_daily_scheduler

log = get_logger(__name__)

QUEUE_NAME = "pae:default"


def run_worker() -> None:
    settings = get_settings()
    start_http_server(settings.worker_metrics_port)
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)
    start_daily_scheduler(queue, settings.miner_run_hour_utc)
    log.info(
        "worker_starting",
        queue=QUEUE_NAME,
        metrics_port=settings.worker_metrics_port,
        mine_hour_utc=settings.miner_run_hour_utc,
    )
    Worker([queue], connection=connection).work()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_scheduler.py -v` — Expected: PASS
Run: `uv run pytest && uv run ruff check src tests scripts` — Expected: all green

- [ ] **Step 5: Commit**

```bash
git add src/pae/worker/scheduler.py src/pae/worker/main.py tests/miner/test_scheduler.py
git commit -m "feat(worker): nightly mining scheduler thread"
```

---

### Task 8: CLI — `pae mine` and `pae patterns list`

**Files:**
- Create: `src/pae/miner/report.py`
- Modify: `src/pae/cli.py` (full rewrite below)
- Test: `tests/miner/test_report.py`

**Interfaces:**
- Consumes: `Pattern`, `run_mining`.
- Produces: `fetch_patterns(kind: str | None, limit: int) -> list` (rows ordered by lift desc); `render_patterns(rows) -> str` (fixed-width table, pure function); CLI subcommands `mine` and `patterns list [--kind ...] [--limit N]`.

- [ ] **Step 1: Write the failing test**

Create `tests/miner/test_report.py`:

```python
from types import SimpleNamespace

from pae.miner.report import render_patterns


def _rows():
    return [
        SimpleNamespace(
            kind="time_of_day",
            entity_id="light.bar",
            action="off",
            day_type="weekday",
            trigger_entity_id=None,
            trigger_state=None,
            tod_minutes=1332.0,
            support=1.0,
            confidence=1.0,
            lift=16.0,
            occurrences=5,
            suspected_schedule=False,
        ),
        SimpleNamespace(
            kind="event_pair",
            entity_id="light.den",
            action="on",
            day_type=None,
            trigger_entity_id="media_player.tv",
            trigger_state="playing",
            tod_minutes=None,
            support=0.83,
            confidence=0.83,
            lift=240.0,
            occurrences=5,
            suspected_schedule=False,
        ),
        SimpleNamespace(
            kind="time_of_day",
            entity_id="switch.pool_2",
            action="on",
            day_type="weekday",
            trigger_entity_id=None,
            trigger_state=None,
            tod_minutes=782.0,
            support=1.0,
            confidence=1.0,
            lift=16.0,
            occurrences=5,
            suspected_schedule=True,
        ),
    ]


def test_render_patterns_table():
    out = render_patterns(_rows())
    lines = out.splitlines()
    assert len(lines) == 4  # header + 3 rows
    assert "light.bar -> off ~22:12 (weekday)" in out
    assert "media_player.tv=playing => light.den -> on" in out
    assert "sched" in out  # suspected_schedule flag column


def test_render_patterns_empty():
    assert render_patterns([]) == "no patterns mined yet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/miner/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pae.miner.report'`

- [ ] **Step 3: Implement**

Create `src/pae/miner/report.py`:

```python
"""Human-facing pattern listing for the CLI."""
import sqlalchemy as sa

from pae.config import get_settings
from pae.db.models import Pattern

HEADER = (
    f"{'KIND':<12} {'PATTERN':<64} {'SUP':>5} {'CONF':>5} {'LIFT':>8} {'N':>4} {'FLAGS':<6}"
)


def describe(row) -> str:
    if row.kind == "time_of_day":
        hh, mm = divmod(int(row.tod_minutes), 60)
        return f"{row.entity_id} -> {row.action} ~{hh:02d}:{mm:02d} ({row.day_type})"
    return f"{row.trigger_entity_id}={row.trigger_state} => {row.entity_id} -> {row.action}"


def render_patterns(rows) -> str:
    if not rows:
        return "no patterns mined yet"
    lines = [HEADER]
    for r in rows:
        flags = "sched" if r.suspected_schedule else ""
        lines.append(
            f"{r.kind:<12} {describe(r)[:64]:<64} {r.support:>5.2f} {r.confidence:>5.2f}"
            f" {r.lift:>8.1f} {r.occurrences:>4d} {flags:<6}"
        )
    return "\n".join(lines)


def fetch_patterns(kind: str | None = None, limit: int = 30) -> list:
    engine = sa.create_engine(get_settings().db_url)
    try:
        query = sa.select(Pattern).order_by(Pattern.lift.desc()).limit(limit)
        if kind:
            query = query.where(Pattern.kind == kind)
        with engine.connect() as conn:
            return list(conn.execute(query))
    finally:
        engine.dispose()
```

Rewrite `src/pae/cli.py` in full:

```python
import argparse
import asyncio
import sys

from pae import __version__
from pae.config import get_settings
from pae.logging import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pae", description="Predictive Automation Engine")
    parser.add_argument("--version", action="version", version=f"pae {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("api", help="run the FastAPI service")
    sub.add_parser("worker", help="run the RQ worker")
    sub.add_parser("ingest", help="run the HA event ingester")
    sub.add_parser("migrate", help="upgrade the database schema to head")
    sub.add_parser("mine", help="run the pattern miner once, now")
    patterns = sub.add_parser("patterns", help="inspect mined patterns")
    psub = patterns.add_subparsers(dest="patterns_command", required=True)
    plist = psub.add_parser("list", help="list mined patterns by descending lift")
    plist.add_argument("--kind", choices=["time_of_day", "event_pair"])
    plist.add_argument("--limit", type=int, default=30)
    smoke = sub.add_parser("smoke", help="read-only smoke test against live Home Assistant")
    smoke.add_argument(
        "--duration", type=int, default=60, help="seconds to listen for events (default 60)"
    )

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "smoke":
        # human-facing command: console logs regardless of LOG_FORMAT
        configure_logging(settings.log_level, "console")
        from pae.smoke import run_smoke

        return asyncio.run(run_smoke(duration=args.duration))

    if args.command == "mine":
        configure_logging(settings.log_level, "console")
        from pae.miner.service import run_mining

        result = run_mining()
        print(
            f"events={result.events_loaded} days={result.days_observed} "
            f"tod={result.tod_patterns} pairs={result.pair_patterns}"
        )
        return 0

    if args.command == "patterns":
        configure_logging(settings.log_level, "console")
        from pae.miner.report import fetch_patterns, render_patterns

        print(render_patterns(fetch_patterns(kind=args.kind, limit=args.limit)))
        return 0

    configure_logging(settings.log_level, settings.log_format)

    if args.command == "api":
        import uvicorn

        uvicorn.run(
            "pae.api.app:create_app",
            factory=True,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,
        )
        return 0

    if args.command == "worker":
        from pae.worker.main import run_worker

        run_worker()
        return 0

    if args.command == "ingest":
        from pae.ingest.service import run_ingest

        run_ingest()
        return 0

    if args.command == "migrate":
        from pae.db.migrate import run_migrations

        run_migrations()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/miner/test_report.py -v` — Expected: PASS
Run: `uv run pytest && uv run ruff check src tests scripts` — Expected: all green

- [ ] **Step 5: Commit**

```bash
git add src/pae/miner/report.py src/pae/cli.py tests/miner/test_report.py
git commit -m "feat(cli): pae mine and pae patterns list"
```

---

### Task 9: Grafana dashboard #2 and README

**Files:**
- Create: `grafana/pae-dashboard-2-patterns.json`
- Modify: `README.md` (update status line; extend Grafana section; add miner section)

No unit tests — the dashboard is verified visually in Task 10.

- [ ] **Step 1: Create the dashboard JSON**

Create `grafana/pae-dashboard-2-patterns.json` (datasource type must be `grafana-postgresql-datasource` for Grafana 13; uid `afreiil0pq41se` is the existing "PAE Postgres" datasource):

```json
{
  "uid": "pae-patterns",
  "title": "PAE — Patterns (Phase 2)",
  "tags": ["pae"],
  "timezone": "browser",
  "schemaVersion": 39,
  "refresh": "15m",
  "time": {"from": "now-30d", "to": "now"},
  "panels": [
    {
      "id": 1,
      "type": "stat",
      "title": "Patterns by kind",
      "gridPos": {"h": 5, "w": 6, "x": 0, "y": 0},
      "datasource": {"type": "grafana-postgresql-datasource", "uid": "afreiil0pq41se"},
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawSql": "SELECT kind, count(*)::int AS patterns FROM patterns GROUP BY kind ORDER BY kind"
        }
      ],
      "options": {"reduceOptions": {"values": true, "fields": "/patterns/"}, "textMode": "value_and_name"}
    },
    {
      "id": 2,
      "type": "stat",
      "title": "Suspected external schedules",
      "gridPos": {"h": 5, "w": 6, "x": 6, "y": 0},
      "datasource": {"type": "grafana-postgresql-datasource", "uid": "afreiil0pq41se"},
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawSql": "SELECT count(*)::int AS suspected FROM patterns WHERE suspected_schedule"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "yellow", "value": 1}]}
        },
        "overrides": []
      }
    },
    {
      "id": 3,
      "type": "stat",
      "title": "Hours since last mine",
      "gridPos": {"h": 5, "w": 6, "x": 12, "y": 0},
      "datasource": {"type": "grafana-postgresql-datasource", "uid": "afreiil0pq41se"},
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawSql": "SELECT round(extract(epoch FROM now() - max(mined_at)) / 3600, 1) AS hours FROM patterns"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "h",
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "red", "value": 30}]}
        },
        "overrides": []
      }
    },
    {
      "id": 4,
      "type": "stat",
      "title": "Actionable manual events (30d)",
      "gridPos": {"h": 5, "w": 6, "x": 18, "y": 0},
      "datasource": {"type": "grafana-postgresql-datasource", "uid": "afreiil0pq41se"},
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawSql": "SELECT count(*)::int AS events FROM events WHERE triggered_by = 'manual' AND domain IN ('light','switch','climate','cover','lock','fan','media_player') AND time > now() - interval '30 days'"
        }
      ]
    },
    {
      "id": 5,
      "type": "table",
      "title": "Top patterns by lift",
      "gridPos": {"h": 16, "w": 24, "x": 0, "y": 5},
      "datasource": {"type": "grafana-postgresql-datasource", "uid": "afreiil0pq41se"},
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawSql": "SELECT kind, coalesce(trigger_entity_id || '=' || trigger_state || ' => ', '') || entity_id || ' -> ' || action AS pattern, day_type, to_char((tod_minutes || ' minutes')::interval, 'HH24:MI') AS around, round(support::numeric, 2) AS support, round(confidence::numeric, 2) AS confidence, round(lift::numeric, 1) AS lift, occurrences, suspected_schedule AS sched, status, last_seen FROM patterns ORDER BY lift DESC LIMIT 100"
        }
      ],
      "options": {"sortBy": [{"displayName": "lift", "desc": true}]}
    }
  ]
}
```

- [ ] **Step 2: Update README.md**

Change the status paragraph (line 8–11) to:

```markdown
**Current status: Phase 2** — statistical pattern mining. PAE still writes nothing to Home
Assistant (enforced in code: outbound WebSocket message whitelist — read-only commands
only — plus a `ReadOnlyViolation` guard on all write-shaped methods).
```

Replace the `## Grafana` section body with:

```markdown
- `grafana/pae-dashboard-1-ingestion.json` — event volume by domain, manual-vs-automation
  ratio, presence timeline, room-level detections, context frames.
- `grafana/pae-dashboard-2-patterns.json` — mined patterns: counts by kind, suspected
  external schedules, staleness, top patterns by lift.

Import into Grafana (192.168.6.51) with a PostgreSQL datasource pointing at the PAE
database (Grafana 13: datasource type `grafana-postgresql-datasource`).
```

Add after the `## Attribution` section:

```markdown
## Pattern mining (Phase 2)

A nightly RQ job (enqueued by the worker at `MINER_RUN_HOUR_UTC`, default 09:00 UTC)
mines the last `MINER_LOOKBACK_DAYS` (60) of events for two pattern kinds:

- **time_of_day** — a manual action recurring at a consistent local time, conditioned on
  weekday/weekend (US holidays count as weekend). Scored by support (active days /
  observed days), temporal consistency (share of occurrences within ±45 min of the
  circular mean), and lift vs. a uniform-over-the-day baseline.
- **event_pair** — a manual action following some other state transition within 5
  minutes. Scored by association-rule support/confidence/lift. Near-simultaneous pairs
  (< 2 s) are discarded as state mirrors.

Clockwork-regular device-originated patterns (circular std ≤ 2 min, no user_id — e.g.
the Pentair pool controller's own schedule) are flagged `suspected_schedule`, kept but
never to be proposed as automations. Patterns upsert into `patterns` by `pattern_key`;
the miner never touches `status` (lifecycle belongs to Phase 3+).

    pae mine             # run the miner once, now
    pae patterns list    # top patterns by lift (--kind, --limit)
```

- [ ] **Step 3: Lint/tests still green**

Run: `uv run pytest && uv run ruff check src tests scripts` — Expected: all green

- [ ] **Step 4: Commit**

```bash
git add grafana/pae-dashboard-2-patterns.json README.md
git commit -m "docs: Phase 2 README and Grafana patterns dashboard"
```

---

### Task 10: Deploy and live verification (operator-gated)

No new code. **Operator rules apply: this is the phase-end gate — present results and wait for explicit approval. Grafana dashboard import may need a service-account token (the old one may be revoked) — ask the operator.**

- [ ] **Step 1: Rebuild and restart the stack** (image is shared by api/worker/ingester)

```bash
sudo docker compose up -d --build
sudo docker compose ps   # all healthy
```

The ingester runs `pae migrate` on start, applying migration 0002. Verify:

```bash
sudo docker compose exec -T db psql -U pae -d pae -c "\d patterns"
```

Expected: table exists with `ux_patterns_key` unique index.

- [ ] **Step 2: Run the miner once, live**

```bash
sudo docker compose exec -T api pae mine
sudo docker compose exec -T api pae patterns list
```

Expected: `mine` prints event/day/pattern counts; `patterns list` renders the table.
With only ~5 days of soak data, expect few patterns — verify `switch.pool_2` patterns
carry the `sched` flag if they appear.

- [ ] **Step 3: Verify idempotency** — run `pae mine` again; pattern count must not grow (upsert, not insert):

```bash
sudo docker compose exec -T db psql -U pae -d pae -c "SELECT count(*) FROM patterns"
sudo docker compose exec -T api pae mine
sudo docker compose exec -T db psql -U pae -d pae -c "SELECT count(*) FROM patterns"
```

- [ ] **Step 4: Verify the scheduler thread** — worker logs show `mine_scheduler_started`:

```bash
sudo docker compose logs worker | grep -E "mine_scheduler_started|worker_starting"
```

- [ ] **Step 5: Import Grafana dashboard #2** — ask the operator: import `grafana/pae-dashboard-2-patterns.json` manually in the Grafana UI, or provide a service-account token for API import.

- [ ] **Step 6: Present Phase 2 results to the operator and wait for acceptance** — mined patterns with metrics, the pool_2 schedule flag working, nightly job scheduled. Commit anything outstanding and push only when the operator approves.

## Verification (end-to-end)

1. `uv run pytest` — all unit tests green (miners, stats, scheduler, report, config, model).
2. `uv run ruff check src tests scripts` — clean.
3. Live: migration applied, `pae mine` produces patterns from real soak data, re-run is idempotent, `pae patterns list` renders, pool_2 flagged `sched`, worker logs the scheduler start, dashboard renders in Grafana.
4. Read-only invariant: `git diff main -- src/pae/ha/client.py` is empty.
