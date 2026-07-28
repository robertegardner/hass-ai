from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from conftest import make_pattern

from pae.llm.prompt import RegistryInfo
from pae.miner.sun import SunCalculator
from pae.proposer.grouping import build_groups
from pae.proposer.service import _process_groups

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
REG = {"switch.a": RegistryInfo("switch", "Patio", "Patio", None)}
DOMAINS = {"switch.a": "switch"}

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
