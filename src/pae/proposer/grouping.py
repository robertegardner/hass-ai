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
