from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from conftest import make_pattern

from pae.llm.prompt import RegistryInfo
from pae.metrics import PROPOSALS_BY_STATUS
from pae.miner.sun import SunCalculator
from pae.proposer.grouping import build_groups
from pae.proposer.service import PROPOSAL_STATUSES, _process_groups, _refresh_proposal_gauges

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
REG = {"switch.a": RegistryInfo("switch", "Patio", "Patio", None)}
DOMAINS = {"switch.a": "switch"}
REG_PAIR = dict(REG, **{"binary_sensor.door": RegistryInfo("binary_sensor", "Door", "Patio", None)})
DOMAINS_PAIR = dict(DOMAINS, **{"binary_sensor.door": "binary_sensor"})

GOOD = {
    "propose": True,
    "title": "Patio on",
    "rationale": "Observed daily.",
    "automation": {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    },
}


@dataclass
class FakeResp:
    content: dict
    model: str = "fake"
    host: str = "fake"
    duration_seconds: float = 0.1


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, schema):
        self.calls.append(messages)
        return FakeResp(self.responses.pop(0))


def groups():
    return build_groups(
        [make_pattern(pattern_key="tod:switch.a:on:weekday:14", entity_id="switch.a")]
    )


def run(llm, existing=None):
    return _process_groups(
        groups(), existing or {}, llm, REG, DOMAINS, TZ, SUN, NOW
    )


def pair_groups():
    return build_groups(
        [
            make_pattern(
                pattern_key="pair:binary_sensor.door:on:switch.a:on",
                kind="event_pair",
                entity_id="switch.a",
                action="on",
                day_type=None,
                trigger_entity_id="binary_sensor.door",
                trigger_state="on",
            )
        ]
    )


def run_pair(llm, existing=None):
    return _process_groups(
        pair_groups(), existing or {}, llm, REG_PAIR, DOMAINS_PAIR, TZ, SUN, NOW
    )


GOOD_PAIR = {
    "propose": True,
    "title": "Patio on when door opens",
    "rationale": "Observed daily.",
    "automation": {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    },
}


def test_good_response_produces_insert():
    counters, inserts, status_updates = run(FakeLLM([GOOD]))
    assert counters["generated"] == 1
    (ins,) = inserts
    assert ins["title"] == "Patio on"
    assert ins["status"] == "shadowing"
    assert status_updates == {"tod:switch.a:on:weekday:14": "proposed"}


def test_decline_is_counted_not_stored():
    counters, inserts, _ = run(FakeLLM([{"propose": False, "decline_reason": "meh"}]))
    assert counters["declined"] == 1 and inserts == []


def test_validation_failure_retries_once_with_errors():
    bad = dict(GOOD, automation={"trigger": [], "condition": [], "action": []})
    llm = FakeLLM([bad, GOOD])
    counters, inserts, _ = run(llm)
    assert counters["generated"] == 1 and len(llm.calls) == 2
    assert "Validation failed" in llm.calls[1][-1]["content"]


def test_second_validation_failure_skips():
    bad = dict(GOOD, automation={"trigger": [], "condition": [], "action": []})
    counters, inserts, _ = run(FakeLLM([bad, bad]))
    assert counters["validation_failed"] == 1 and inserts == []


def test_existing_live_proposal_skips_llm():
    llm = FakeLLM([])
    counters, inserts, _ = run(llm, existing={g.group_key: "shadowing" for g in groups()})
    assert counters["skipped_existing"] == 1 and llm.calls == []


def test_missing_title_treated_as_validation_failure():
    no_title = {k: v for k, v in GOOD.items() if k != "title"}
    llm = FakeLLM([no_title, GOOD])
    counters, inserts, _ = run(llm)
    assert counters["generated"] == 1 and len(llm.calls) == 2
    assert "Validation failed" in llm.calls[1][-1]["content"]
    (ins,) = inserts
    assert ins["title"] == "Patio on"


def test_missing_rationale_second_failure_skips():
    no_rationale = {k: v for k, v in GOOD.items() if k != "rationale"}
    counters, inserts, _ = run(FakeLLM([no_rationale, no_rationale]))
    assert counters["validation_failed"] == 1 and inserts == []


def test_blank_title_treated_as_validation_failure():
    blank_title = dict(GOOD, title="   ")
    llm = FakeLLM([blank_title, GOOD])
    counters, inserts, _ = run(llm)
    assert counters["generated"] == 1 and len(llm.calls) == 2


def test_pair_insert_entity_ids_includes_trigger_entity():
    counters, inserts, _ = run_pair(FakeLLM([GOOD_PAIR]))
    assert counters["generated"] == 1
    (ins,) = inserts
    assert ins["entity_ids"] == sorted({"switch.a", "binary_sensor.door"})


def test_refresh_proposal_gauges_zeroes_missing_statuses():
    _refresh_proposal_gauges({"shadowing": 3, "stale": 1})
    assert PROPOSALS_BY_STATUS.labels(status="shadowing")._value.get() == 3
    assert PROPOSALS_BY_STATUS.labels(status="stale")._value.get() == 1
    assert PROPOSALS_BY_STATUS.labels(status="approved")._value.get() == 0
    assert PROPOSALS_BY_STATUS.labels(status="rejected")._value.get() == 0

    # a status bucket that drops to zero must not keep serving its old value
    _refresh_proposal_gauges({"approved": 2})
    assert PROPOSALS_BY_STATUS.labels(status="approved")._value.get() == 2
    assert PROPOSALS_BY_STATUS.labels(status="shadowing")._value.get() == 0
    assert PROPOSALS_BY_STATUS.labels(status="stale")._value.get() == 0
    assert set(PROPOSAL_STATUSES) == {"shadowing", "approved", "rejected", "stale"}
