"""Time-of-day pattern miner: recurring manual actions at consistent times.

Metrics per pattern:
- support: active days / observed days of the matching day_type
- confidence: equal to support (a time-of-day rule has no separate antecedent)
- temporal_consistency: fraction of occurrences within ±tolerance of the
  circular mean time
- lift: consistency density vs. a uniform-over-the-day baseline
- suspected_schedule: no user_id on any occurrence AND either clockwork
  regularity (circular std ≤ schedule_std_minutes) or, over a long enough
  window, tight tracking of that date's sunrise/sunset (offset std ≤
  sun_std_minutes and a strictly better fit than the clock) — either way a
  device or external controller schedule (Pentair, dusk-to-dawn photocells),
  not a human habit
"""
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from statistics import fmean, pstdev
from typing import Any
from zoneinfo import ZoneInfo

from pae.miner.stats import (
    DAY_MINUTES,
    circular_diff,
    circular_mean,
    circular_std,
    cluster_minutes,
    minutes_of_day,
)
from pae.miner.sun import SunCalculator
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
    sun: SunCalculator | None = None,
    sun_std_minutes: float = 5.0,
    sun_min_span_days: float = 10.0,
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
            no_user = all(e.user_id is None for e in cevs)
            suspected = std <= schedule_std_minutes and no_user
            anchor = "clock" if suspected else None
            sun_offset_mean: float | None = None
            sun_offset_std: float | None = None
            # The sun rule needs a window long enough for the sun to drift
            # measurably, else sun-offset std ≈ clock std and the wider
            # sun threshold would silently loosen the clock rule; it must
            # also fit strictly better than the clock so long-window clock
            # habits with < sun_std_minutes jitter are not swept up.
            span_days = (max(active_days) - min(active_days)).days
            if sun is not None and no_user and not suspected and span_days >= sun_min_span_days:
                per_day = [sun.sun_minutes(e.time.astimezone(tz).date()) for e in cevs]
                if all(p is not None for p in per_day):
                    best: tuple[float, float, str] | None = None
                    for i, name in ((0, "sunrise"), (1, "sunset")):
                        pairs = zip(cmins, per_day, strict=True)
                        offsets = [circular_diff(m, p[i]) for m, p in pairs]
                        off_std = pstdev(offsets)
                        if off_std <= sun_std_minutes and off_std < std:
                            if best is None or off_std < best[0]:
                                best = (off_std, fmean(offsets), name)
                    if best is not None:
                        suspected = True
                        sun_offset_std, sun_offset_mean, anchor = best
            evidence: dict[str, Any] = {}
            if anchor is not None:
                evidence["schedule_anchor"] = anchor
            if sun_offset_std is not None and sun_offset_mean is not None:
                evidence["sun_offset_mean_minutes"] = round(sun_offset_mean, 1)
                evidence["sun_offset_std_minutes"] = round(sun_offset_std, 1)
            times = sorted(e.time for e in cevs)
            evidence["sample_times"] = [t.isoformat() for t in times[-10:]]
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
                    evidence=evidence,
                    first_seen=times[0],
                    last_seen=times[-1],
                )
            )
    return out
