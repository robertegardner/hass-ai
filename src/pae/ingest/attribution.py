"""triggered_by attribution — the most important data-quality logic in PAE.

How Home Assistant contexts work:
- Every event carries a context (id, parent_id, user_id).
- When an automation or script runs, HA fires an ``automation_triggered`` /
  ``script_started`` event; the state changes that automation causes carry
  either the SAME context id or a child context whose parent_id is that id.
- A human acting through the UI or a companion app produces a context with
  user_id set and no parent.
- A physical action at the device (wall switch, remote) produces a context
  with neither user_id nor parent_id.

So: we cache the context ids announced by automation_triggered/script_started
and classify each state_changed against that cache. ``pae`` contexts are those
announced by automations whose entity_id is registered as PAE-generated
(none exist until Phase 5+, but the plumbing is here so attribution never
needs a schema change).
"""

import time as time_mod

MANUAL = "manual"
AUTOMATION = "automation"
PAE = "pae"


class ContextCache:
    """TTL cache of context ids attributed to automation/script activity."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, str]] = {}  # context_id -> (expiry, source)

    def add(self, context_id: str, source: str, now: float | None = None) -> None:
        now = now if now is not None else time_mod.monotonic()
        self._prune(now)
        self._entries[context_id] = (now + self._ttl, source)

    def get(self, context_id: str | None, now: float | None = None) -> str | None:
        if context_id is None:
            return None
        now = now if now is not None else time_mod.monotonic()
        entry = self._entries.get(context_id)
        if entry is None or entry[0] < now:
            return None
        return entry[1]

    def _prune(self, now: float) -> None:
        if len(self._entries) < 10_000:
            return
        self._entries = {k: v for k, v in self._entries.items() if v[0] >= now}


def attribute(
    context_id: str | None,
    parent_id: str | None,
    user_id: str | None,
    cache: ContextCache,
    now: float | None = None,
) -> str:
    """Classify a state change as manual, automation, or pae."""
    source = cache.get(context_id, now) or cache.get(parent_id, now)
    if source is not None:
        return source
    if parent_id is not None:
        # caused by some other actor's context we didn't witness — not a human
        return AUTOMATION
    # user_id set: human via UI/app. user_id absent: physical action at the
    # device. Both are manual for pattern-mining purposes.
    return MANUAL
