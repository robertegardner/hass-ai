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
