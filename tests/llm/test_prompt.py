from zoneinfo import ZoneInfo

from conftest import make_pattern

from pae.llm.prompt import PROMPT_VERSION, RESPONSE_SCHEMA, RegistryInfo, build_messages
from pae.proposer.grouping import build_groups

TZ = ZoneInfo("America/Chicago")
REG = {
    "switch.a": RegistryInfo("switch", "Patio Lights", "Patio", None),
    "binary_sensor.door": RegistryInfo("binary_sensor", "Front Door", "Foyer", "door"),
    "light.bar": RegistryInfo("light", "Bar Light", "Basement", None),
}


def test_response_schema_shape():
    assert RESPONSE_SCHEMA["required"] == ["propose"]
    assert set(RESPONSE_SCHEMA["properties"]) == {
        "propose", "decline_reason", "title", "rationale", "automation"
    }


def test_tod_prompt_contains_the_evidence():
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="tod:switch.a:on:weekday:14",
                entity_id="switch.a",
                tod_minutes=432.0,
                evidence={"sample_times": ["2026-07-27T12:12:00+00:00"]},
            )
        ]
    )
    messages = build_messages(g, REG, TZ)
    assert messages[0]["role"] == "system"
    body = messages[1]["content"]
    assert "Patio Lights" in body and "Patio" in body
    assert "07:12" in body  # 432 minutes local
    assert "weekday" in body
    assert str(PROMPT_VERSION)  # exists


def test_pair_prompt_names_trigger_and_action():
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="pair:binary_sensor.door:on->light.bar:on",
                kind="event_pair",
                entity_id="light.bar",
                day_type=None,
                tod_minutes=None,
                trigger_entity_id="binary_sensor.door",
                trigger_state="on",
            )
        ]
    )
    body = build_messages(g, REG, TZ)[1]["content"]
    assert "Front Door" in body and "Bar Light" in body
