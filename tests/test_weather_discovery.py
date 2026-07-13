"""Invariants for the Davis MQTT discovery layer (ha/weather)."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ha" / "weather"))

import discovery  # noqa: E402
import sensor_map  # noqa: E402


def test_unique_ids_and_topics_are_unique():
    configs = discovery.build_configs()
    unique_ids = [p["unique_id"] for _, p in configs]
    topics = [t for t, _ in configs]
    assert len(set(unique_ids)) == len(unique_ids)
    assert len(set(topics)) == len(topics)


def test_discovery_topic_shape():
    for topic, payload in discovery.build_configs():
        parts = topic.split("/")
        assert parts[0] == "homeassistant"
        assert parts[1] in ("sensor", "binary_sensor")
        assert parts[2] == "davis_vantage"
        assert parts[3] == payload["unique_id"]
        assert parts[4] == "config"


def test_state_topics_stay_under_base():
    for _, payload in discovery.build_configs():
        assert payload["state_topic"].startswith("sdr/davis/")


def test_numeric_sensors_have_state_class_for_lts():
    # Long-term statistics (7-day dashboard charts) require state_class on
    # every numeric sensor; timestamp is the only exempt sensor entity.
    for topic, payload in discovery.build_configs():
        if "/binary_sensor/" in topic or payload.get("device_class") == "timestamp":
            continue
        assert payload.get("state_class") == "measurement", payload["unique_id"]


def test_live_values_expire_but_last_packet_does_not():
    by_id = {p["unique_id"]: p for _, p in discovery.build_configs()}
    # A dead SDR pipeline must show unavailable, never stale numbers.
    for uid, p in by_id.items():
        if uid == "davis_last_packet":
            assert "expire_after" not in p
        else:
            assert p["expire_after"] >= 300

    assert by_id["davis_temperature"]["expire_after"] == sensor_map.EXPIRE_AFTER_S


def test_json_templates_only_on_health_topic():
    for _, payload in discovery.build_configs():
        uses_json = "value_json" in payload.get("value_template", "")
        on_health = payload["state_topic"].endswith("/health")
        assert uses_json == on_health, payload["unique_id"]


def test_all_entities_share_one_device():
    for _, payload in discovery.build_configs():
        assert payload["device"]["identifiers"] == ["davis_vantage_pro2_plus"]


def test_sample_payload_topics_are_all_mapped():
    # Every live topic captured from the real station must be consumed by at
    # least one entity definition (no silently dropped data).
    sample = json.loads((REPO_ROOT / "ha" / "weather" / "sample_payload.json").read_text())
    captured = {t for t in sample if t.startswith("sdr/davis/")}
    mapped = {p["state_topic"] for _, p in discovery.build_configs()}
    assert captured == mapped


def test_payloads_are_json_serializable():
    for _, payload in discovery.build_configs():
        json.dumps(payload)
