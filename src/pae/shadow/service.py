"""Shadow evaluation: replay a proposal's automation against real events.

Scores the automation_json itself (what would ship), not the source pattern.
Only the weekday time-condition is simulated in Phase 3; other stored
conditions (after/before/state/sun) are recorded on the proposal but ignored
here — a documented limitation, not an oversight. Aggregation is per-action-
entity so precision (human_matches / expected_fires) and coverage
(human_matches / human_total) both stay in [0, 1]: `human_matches` counts
would-fire *occurrences* that had at least one in-window human event, not the
human events themselves, which makes coverage slightly conservative when a
human acts more than once near a single fire (documented on `ShadowResult`).

Runs synchronously (it is an RQ job); uses its own short-lived sync engine.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from pydantic import ValidationError

from pae.config import get_settings
from pae.db.models import Event, Proposal, ShadowResult
from pae.logging import get_logger
from pae.metrics import SHADOW_DAYS_SCORED
from pae.miner.stats import circular_diff, minutes_of_day
from pae.miner.sun import SunCalculator
from pae.proposer.schema import (
    SERVICE_STATE,
    Automation,
    StateTrigger,
    SunTrigger,
    TimeCondition,
    TimeTrigger,
    _parse_hms,
    _parse_offset,
)

log = get_logger(__name__)

INSERT_CHUNK = 500
SHADOW_STATUSES = ("shadowing", "approved")


@dataclass
class DayScore:
    expected_fires: int
    human_matches: int
    human_total: int


@dataclass
class ShadowEvent:
    time: datetime
    entity_id: str
    new_state: str
    triggered_by: str


def _day_passes_conditions(conditions: list, day: date) -> bool:
    weekday = day.strftime("%a").lower()[:3]
    for c in conditions:
        if isinstance(c, TimeCondition) and c.weekday is not None and weekday not in c.weekday:
            return False
    return True


def _fire_minutes(
    trig, conditions: list, day: date, *, tz: ZoneInfo, sun: SunCalculator | None,
    day_events: list[ShadowEvent],
) -> list[float] | None:
    """Would-fire minutes-of-day for ``day``, or None when not evaluable."""
    if isinstance(trig, TimeTrigger):
        return [_parse_hms(trig.at)] if _day_passes_conditions(conditions, day) else []
    if isinstance(trig, SunTrigger):
        if sun is None:
            return None
        sm = sun.sun_minutes(day)
        if sm is None:
            return None
        anchor = sm[0] if trig.event == "sunrise" else sm[1]
        minute = anchor + _parse_offset(trig.offset)
        return [minute] if _day_passes_conditions(conditions, day) else []
    if isinstance(trig, StateTrigger):
        return [
            minutes_of_day(e.time, tz)
            for e in day_events
            if e.entity_id == trig.entity_id and e.new_state == trig.to
        ]
    return None  # unreachable given the schema's discriminated trigger union


def _in_window(kind: str, ev_minute: float, fire_minute: float, *,
                tolerance_minutes: float, pair_window_minutes: float) -> bool:
    if kind == "event_pair":
        return 0 <= (ev_minute - fire_minute) <= pair_window_minutes
    return abs(circular_diff(ev_minute, fire_minute)) <= tolerance_minutes


def evaluate_day(
    automation: dict,
    kind: str,
    day: date,
    *,
    tz: ZoneInfo,
    sun: SunCalculator | None,
    day_events: list[ShadowEvent],
    tolerance_minutes: float = 45.0,
    pair_window_minutes: float = 5.0,
) -> DayScore | None:
    try:
        parsed = Automation.model_validate(automation)
    except ValidationError as exc:
        log.error("shadow_automation_parse_failed", errors=str(exc))
        return None

    fire_minutes = _fire_minutes(
        parsed.trigger[0], parsed.condition, day, tz=tz, sun=sun, day_events=day_events
    )
    if fire_minutes is None:
        return None

    expected_fires = 0
    human_matches = 0
    human_total = 0
    for action in parsed.action:
        service_name = action.service.split(".", 1)[1]
        expected_state = SERVICE_STATE[service_name]
        for entity_id in action.target.entity_id:
            human_minutes = [
                minutes_of_day(e.time, tz)
                for e in day_events
                if e.entity_id == entity_id
                and e.new_state == expected_state
                and e.triggered_by == "manual"
            ]
            expected_fires += len(fire_minutes)
            human_total += len(human_minutes)
            human_matches += sum(
                1
                for m in fire_minutes
                if any(
                    _in_window(
                        kind, ev_m, m,
                        tolerance_minutes=tolerance_minutes,
                        pair_window_minutes=pair_window_minutes,
                    )
                    for ev_m in human_minutes
                )
            )

    return DayScore(
        expected_fires=expected_fires, human_matches=human_matches, human_total=human_total
    )


def _involved_entities(automation_json: dict) -> set[str]:
    """Trigger + action entities referenced by a stored automation_json — the
    ``entity_ids`` column on Proposal only carries action entities, so an
    event_pair proposal's trigger entity (never an action target) would
    otherwise be missing from the single bulk events query."""
    parsed = Automation.model_validate(automation_json)
    entities: set[str] = set()
    trig = parsed.trigger[0]
    if isinstance(trig, StateTrigger):
        entities.add(trig.entity_id)
    for action in parsed.action:
        entities.update(action.target.entity_id)
    return entities


def run_shadow_eval(now: datetime | None = None) -> dict:
    settings = get_settings()
    now = now or datetime.now(UTC)
    tz = ZoneInfo(settings.miner_local_tz)
    sun = None
    if settings.miner_latitude is not None and settings.miner_longitude is not None:
        sun = SunCalculator(settings.miner_latitude, settings.miner_longitude, tz)

    engine = sa.create_engine(settings.db_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            proposals = list(
                conn.execute(
                    sa.select(Proposal).where(Proposal.status.in_(SHADOW_STATUSES))
                )
            )
            proposals_evaluated = len(proposals)
            if not proposals:
                log.info("shadow_done", proposals=0, days=0)
                return {"proposals_evaluated": 0, "days_scored": 0}

            today_local = now.astimezone(tz).date()
            yesterday = today_local - timedelta(days=1)
            lookback_floor = today_local - timedelta(days=settings.shadow_lookback_days)

            existing = set(
                conn.execute(sa.select(ShadowResult.proposal_id, ShadowResult.day)).all()
            )

            plans: list[tuple[Proposal, list[date]]] = []
            all_entities: set[str] = set()
            earliest_day: date | None = None
            for p in proposals:
                created_local = p.created_at.astimezone(tz).date()
                start_day = max(created_local, lookback_floor)
                span = (yesterday - start_day).days + 1
                needed_days = [
                    start_day + timedelta(days=i)
                    for i in range(max(span, 0))
                    if (p.id, start_day + timedelta(days=i)) not in existing
                ]
                plans.append((p, needed_days))
                if not needed_days:
                    continue
                try:
                    all_entities |= _involved_entities(p.automation_json)
                except ValidationError:
                    log.error("shadow_schema_drift", proposal_id=p.id)
                    continue
                day0 = min(needed_days)
                earliest_day = day0 if earliest_day is None else min(earliest_day, day0)

            days_scored = 0
            if earliest_day is not None and all_entities:
                range_start = datetime.combine(earliest_day, time.min, tzinfo=tz).astimezone(UTC)
                range_end = datetime.combine(today_local, time.min, tzinfo=tz).astimezone(UTC)
                event_rows = conn.execute(
                    sa.select(Event.time, Event.entity_id, Event.new_state, Event.triggered_by)
                    .where(
                        Event.entity_id.in_(all_entities),
                        Event.time >= range_start,
                        Event.time < range_end,
                    )
                ).all()
                by_day: dict[date, list[ShadowEvent]] = defaultdict(list)
                for row in event_rows:
                    d = row.time.astimezone(tz).date()
                    by_day[d].append(
                        ShadowEvent(
                            time=row.time,
                            entity_id=row.entity_id,
                            new_state=row.new_state,
                            triggered_by=row.triggered_by,
                        )
                    )

                inserts: list[dict] = []
                for p, needed_days in plans:
                    for d in needed_days:
                        score = evaluate_day(
                            p.automation_json,
                            p.kind,
                            d,
                            tz=tz,
                            sun=sun,
                            day_events=by_day.get(d, []),
                            tolerance_minutes=settings.shadow_tolerance_minutes,
                            pair_window_minutes=settings.miner_pair_window_minutes,
                        )
                        if score is None:
                            continue
                        inserts.append(
                            {
                                "proposal_id": p.id,
                                "day": d,
                                "expected_fires": score.expected_fires,
                                "human_matches": score.human_matches,
                                "human_total": score.human_total,
                            }
                        )
                        days_scored += 1

                for start in range(0, len(inserts), INSERT_CHUNK):
                    conn.execute(sa.insert(ShadowResult), inserts[start : start + INSERT_CHUNK])

        if days_scored:
            SHADOW_DAYS_SCORED.inc(days_scored)
        log.info("shadow_done", proposals=proposals_evaluated, days=days_scored)
        return {"proposals_evaluated": proposals_evaluated, "days_scored": days_scored}
    finally:
        engine.dispose()
