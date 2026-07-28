from datetime import UTC, date, datetime

from pae.db.models import Proposal, ShadowResult


def test_proposal_model_fields():
    now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
    p = Proposal(
        group_key="tod:weekday:14:abc123def456",
        kind="time_of_day",
        title="Morning lights",
        rationale="You turn these on together every weekday morning.",
        automation_json={"trigger": [], "condition": [], "action": []},
        source_pattern_keys=["tod:switch.a:on:weekday:14"],
        entity_ids=["switch.a"],
        model_name="test-model",
        prompt_version=1,
        status="shadowing",
        last_eligible_at=now,
        created_at=now,
        updated_at=now,
    )
    assert p.status == "shadowing"
    assert p.reject_reason is None


def test_shadow_result_model_fields():
    r = ShadowResult(
        proposal_id=1, day=date(2026, 7, 27), expected_fires=1, human_matches=1, human_total=2
    )
    assert r.human_total == 2
