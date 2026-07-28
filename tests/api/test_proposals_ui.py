from datetime import date

import pytest
from fastapi.testclient import TestClient

import pae.proposer.repo as repo
from pae.api.app import create_app

PROPOSAL = {
    "id": 1,
    "group_key": "tod:weekday:14:abc",
    "kind": "time_of_day",
    "title": "Morning lights",
    "rationale": "Observed daily.",
    "automation_json": {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    },
    "source_pattern_keys": ["tod:switch.a:on:weekday:14"],
    "entity_ids": ["switch.a"],
    "model_name": "m",
    "prompt_version": 1,
    "status": "shadowing",
    "reject_reason": None,
    "friendly_names": {"switch.a": "Patio Lights"},
}
HISTORY = [
    {"day": date(2026, 7, d), "expected_fires": 1, "human_matches": 1, "human_total": 1}
    for d in range(10, 28)
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(repo, "list_proposals", lambda status: [PROPOSAL])
    monkeypatch.setattr(repo, "get_proposal", lambda pid: PROPOSAL if pid == 1 else None)
    monkeypatch.setattr(repo, "shadow_history", lambda pid, days=30: HISTORY)
    calls = []
    monkeypatch.setattr(
        repo, "set_status", lambda pid, status, reason=None: calls.append((pid, status, reason))
    )
    c = TestClient(create_app())
    c.calls = calls
    return c


def test_list_page_renders(client):
    r = client.get("/proposals")
    assert r.status_code == 200
    assert "Morning lights" in r.text
    assert "Patio Lights" in r.text


def test_detail_page_shows_yaml_and_scores(client):
    r = client.get("/proposals/1")
    assert r.status_code == 200
    assert "platform: time" in r.text  # YAML rendering
    assert "precision" in r.text.lower()


def test_missing_proposal_404(client):
    assert client.get("/proposals/999").status_code == 404


def test_approve_posts_and_redirects(client):
    r = client.post("/proposals/1/approve", follow_redirects=False)
    assert r.status_code == 303
    assert client.calls == [(1, "approved", None)]


def test_reject_with_reason(client):
    client.post("/proposals/1/reject", data={"reason": "not useful"}, follow_redirects=False)
    assert client.calls == [(1, "rejected", "not useful")]


def test_sparkbar_svg():
    from pae.api.viz import sparkbar

    svg = sparkbar([0.0, 0.5, 1.0])
    assert svg.startswith("<svg") and svg.count("<rect") == 3
