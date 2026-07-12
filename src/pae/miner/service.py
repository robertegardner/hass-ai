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
    for _dist, _i, c, key in sorted(scored, key=lambda t: (t[0], t[1])):
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
