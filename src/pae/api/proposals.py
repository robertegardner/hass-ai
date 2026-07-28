"""Proposals review UI: list, detail, approve/reject.

Sync route handlers (FastAPI runs them in its thread pool) calling straight
into ``pae.proposer.repo``, which is the only thing that touches the
database. Nothing here ever writes to Home Assistant — approve/reject only
flip status columns in the PAE db.
"""
import yaml
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from pae.api import viz
from pae.config import get_settings
from pae.proposer import repo

router = APIRouter()

_env = Environment(
    loader=PackageLoader("pae.api", "templates"),
    autoescape=select_autoescape(["html"]),
)

# Every status a Proposal row can carry (mirrors pae.proposer.service.PROPOSAL_STATUSES);
# fixed so the tab bar and counts are stable regardless of what's populated today.
STATUSES = ("shadowing", "approved", "rejected", "stale")


def _render(name: str, **context) -> HTMLResponse:
    return HTMLResponse(_env.get_template(name).render(**context))


def _precision_series(history: list[dict]) -> list[float]:
    return [
        (h["human_matches"] / h["expected_fires"]) if h["expected_fires"] > 0 else 0.0
        for h in history
    ]


def _rolling(history: list[dict], key_num: str, key_den: str) -> float | None:
    window = history[-14:]
    num = sum(h[key_num] for h in window)
    den = sum(h[key_den] for h in window)
    return (num / den) if den > 0 else None


@router.get("/proposals")
def list_proposals_page(status: str = "shadowing") -> HTMLResponse:
    proposals = repo.list_proposals(status)
    rows = []
    for p in proposals:
        history = repo.shadow_history(p["id"], days=14)
        rows.append({"proposal": p, "spark": viz.sparkbar(_precision_series(history))})
    counts = {s: len(repo.list_proposals(s)) for s in STATUSES}
    return _render(
        "proposals_list.html", rows=rows, status=status, statuses=STATUSES, counts=counts
    )


@router.get("/proposals/{proposal_id}")
def proposal_detail_page(proposal_id: int) -> HTMLResponse:
    proposal = repo.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    history = repo.shadow_history(proposal_id, days=30)
    settings = get_settings()
    is_ready = repo.ready(
        history,
        min_days=settings.shadow_ready_days,
        min_precision=settings.shadow_ready_precision,
        min_coverage=settings.shadow_ready_coverage,
    )
    return _render(
        "proposal_detail.html",
        proposal=proposal,
        history=history,
        yaml_text=yaml.safe_dump(proposal["automation_json"], sort_keys=False),
        precision=_rolling(history, "human_matches", "expected_fires"),
        coverage=_rolling(history, "human_matches", "human_total"),
        ready=is_ready,
        spark=viz.sparkbar(_precision_series(history)),
    )


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int) -> RedirectResponse:
    repo.set_status(proposal_id, "approved")
    return RedirectResponse(url=f"/proposals/{proposal_id}", status_code=303)


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, reason: str | None = Form(default=None)) -> RedirectResponse:
    repo.set_status(proposal_id, "rejected", reason)
    return RedirectResponse(url=f"/proposals/{proposal_id}", status_code=303)
