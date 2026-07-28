"""Plain-dict data access for the proposals review UI.

Sync SQLAlchemy Core over the ORM tables (see ``pae.miner.report`` for the
existing fetch-and-render CLI pattern this mirrors). Every public function
returns plain dicts / bools — the UI layer never touches the ORM, and tests
monkeypatch this module directly rather than a database.
"""
from functools import lru_cache

import sqlalchemy as sa

from pae.config import get_settings
from pae.db.models import EntityRegistryEntry, Pattern, Proposal, ShadowResult
from pae.logging import get_logger

log = get_logger(__name__)


@lru_cache
def get_engine() -> sa.Engine:
    return sa.create_engine(get_settings().db_url)


def _proposal_to_dict(row) -> dict:
    return {
        "id": row.id,
        "group_key": row.group_key,
        "kind": row.kind,
        "title": row.title,
        "rationale": row.rationale,
        "automation_json": row.automation_json,
        "source_pattern_keys": row.source_pattern_keys,
        "entity_ids": row.entity_ids,
        "model_name": row.model_name,
        "prompt_version": row.prompt_version,
        "status": row.status,
        "reject_reason": row.reject_reason,
    }


def _friendly_names(conn, entity_ids: list[str]) -> dict[str, str]:
    if not entity_ids:
        return {}
    rows = conn.execute(
        sa.select(EntityRegistryEntry.entity_id, EntityRegistryEntry.friendly_name).where(
            EntityRegistryEntry.entity_id.in_(entity_ids)
        )
    )
    return {r.entity_id: r.friendly_name or r.entity_id for r in rows}


def _last_n_shadow_rows(conn, proposal_id: int, n: int) -> list[dict]:
    rows = conn.execute(
        sa.select(
            ShadowResult.day,
            ShadowResult.expected_fires,
            ShadowResult.human_matches,
            ShadowResult.human_total,
        )
        .where(ShadowResult.proposal_id == proposal_id)
        .order_by(ShadowResult.day.desc())
        .limit(n)
    )
    return [
        {
            "day": r.day,
            "expected_fires": r.expected_fires,
            "human_matches": r.human_matches,
            "human_total": r.human_total,
        }
        for r in rows
    ]


def ready(
    history: list[dict],
    *,
    min_days: int,
    min_precision: float,
    min_coverage: float,
) -> bool:
    """A proposal is ready to graduate once it has at least ``min_days`` of
    shadow history AND its rolling precision/coverage over the last 14 rows
    each meet their threshold. Precision = matches/expected, coverage =
    matches/total; both guard against zero-division (no denominator -> not
    ready, since there's nothing to be confident about)."""
    if len(history) < min_days:
        return False
    window = history[-14:]
    expected = sum(h["expected_fires"] for h in window)
    matches = sum(h["human_matches"] for h in window)
    total = sum(h["human_total"] for h in window)
    if expected <= 0 or total <= 0:
        return False
    precision = matches / expected
    coverage = matches / total
    return precision >= min_precision and coverage >= min_coverage


def list_proposals(status: str) -> list[dict]:
    settings = get_settings()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(Proposal)
            .where(Proposal.status == status)
            .order_by(Proposal.created_at.desc())
        )
        out = []
        for row in rows:
            d = _proposal_to_dict(row)
            d["friendly_names"] = _friendly_names(conn, row.entity_ids)
            history = list(
                reversed(_last_n_shadow_rows(conn, row.id, 14))
            )  # ascending for ready()
            if history:
                expected = sum(h["expected_fires"] for h in history)
                matches = sum(h["human_matches"] for h in history)
                total = sum(h["human_total"] for h in history)
                d["precision14"] = (matches / expected) if expected > 0 else None
                d["coverage14"] = (matches / total) if total > 0 else None
            else:
                d["precision14"] = None
                d["coverage14"] = None
            d["ready"] = ready(
                history,
                min_days=settings.shadow_ready_days,
                min_precision=settings.shadow_ready_precision,
                min_coverage=settings.shadow_ready_coverage,
            )
            out.append(d)
        return out


def get_proposal(proposal_id: int) -> dict | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(sa.select(Proposal).where(Proposal.id == proposal_id)).first()
        if row is None:
            return None
        d = _proposal_to_dict(row)
        d["friendly_names"] = _friendly_names(conn, row.entity_ids)
        return d


def shadow_history(proposal_id: int, days: int = 30) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                ShadowResult.day,
                ShadowResult.expected_fires,
                ShadowResult.human_matches,
                ShadowResult.human_total,
            )
            .where(ShadowResult.proposal_id == proposal_id)
            .order_by(ShadowResult.day.desc())
            .limit(days)
        )
        history = [
            {
                "day": r.day,
                "expected_fires": r.expected_fires,
                "human_matches": r.human_matches,
                "human_total": r.human_total,
            }
            for r in rows
        ]
        return list(reversed(history))


def set_status(proposal_id: int, status: str, reason: str | None = None) -> bool:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            sa.update(Proposal)
            .where(Proposal.id == proposal_id)
            .values(status=status, reject_reason=reason, updated_at=sa.func.now())
        )
        if result.rowcount == 0:
            return False
        if status == "rejected":
            proposal = conn.execute(
                sa.select(Proposal.source_pattern_keys).where(Proposal.id == proposal_id)
            ).first()
            if proposal is not None and proposal.source_pattern_keys:
                conn.execute(
                    sa.update(Pattern)
                    .where(
                        Pattern.pattern_key.in_(proposal.source_pattern_keys),
                        Pattern.status == "proposed",
                    )
                    .values(status="rejected")
                )
        return True
