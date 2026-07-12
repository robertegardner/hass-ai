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
