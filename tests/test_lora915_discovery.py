"""Invariants for the lora915 MQTT discovery layer (ha/lora915)."""

import importlib.util
import json
import sys
from pathlib import Path

# ha/weather and ha/lora915 both expose modules named `discovery`/`sensor_map`.
# Load ours from an explicit path and DON'T leave the bare names in sys.modules,
# so the weather test (run in the same pytest process) still imports its own.
_PKG = Path(__file__).resolve().parent.parent / "ha" / "lora915"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # so discovery's `import sensor_map` resolves
    spec.loader.exec_module(mod)
    return mod


sensor_map = _load("sensor_map")
discovery = _load("discovery")
# discovery holds its own ref to sensor_map; drop the bare names so a later
# `import discovery`/`import sensor_map` (weather test) loads fresh from its path.
for _n in ("discovery", "sensor_map"):
    sys.modules.pop(_n, None)


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
        assert parts[2] == "lora915"
        assert parts[3] == payload["unique_id"]
        assert parts[4] == "config"


def test_state_topics_are_known_sources():
    allowed = {sensor_map.ACTIVITY_TOPIC, sensor_map.STATUS_TOPIC}
    for _, payload in discovery.build_configs():
        assert payload["state_topic"] in allowed


def test_activity_binary_sensor_is_momentary():
    by_id = {p["unique_id"]: p for _, p in discovery.build_configs()}
    a = by_id["lora915_activity"]
    # off_delay makes it self-clear; a stuck-on activity sensor would be a bug.
    assert a["off_delay"] == sensor_map.ACTIVITY_OFF_DELAY_S
    assert a["state_topic"] == sensor_map.ACTIVITY_TOPIC
    assert a["json_attributes_topic"] == sensor_map.ACTIVITY_TOPIC


def test_diagnostics_expire_so_dead_pipeline_shows_unavailable():
    # Every entity sourced from the heartbeat must expire (a silent detector
    # stall must read unavailable, never stale). Activity-sourced entities are
    # event-driven and must NOT expire (they hold the last burst's detail).
    for _, p in discovery.build_configs():
        if p["state_topic"] == sensor_map.STATUS_TOPIC:
            assert p.get("expire_after") == sensor_map.STATUS_EXPIRE_S, p["unique_id"]
        else:
            assert "expire_after" not in p, p["unique_id"]


def test_numeric_measurement_sensors_have_state_class():
    for topic, p in discovery.build_configs():
        if "/binary_sensor/" in topic:
            continue
        if p.get("unit_of_measurement") and p.get("device_class") != "timestamp":
            assert "state_class" in p, p["unique_id"]


def test_counter_uses_total_increasing():
    by_id = {p["unique_id"]: p for _, p in discovery.build_configs()}
    # detector restarts reset the counter to 0; total_increasing tolerates it.
    assert by_id["lora915_event_count"]["state_class"] == "total_increasing"
    assert by_id["lora915_uptime"]["state_class"] == "total_increasing"


def test_all_entities_share_one_device():
    for _, payload in discovery.build_configs():
        assert payload["device"]["identifiers"] == ["sdr_lora915"]


def test_payloads_are_json_serializable():
    for _, payload in discovery.build_configs():
        json.dumps(payload)


def test_expected_entity_set():
    ids = {p["unique_id"] for _, p in discovery.build_configs()}
    assert ids == {
        "lora915_activity",
        "lora915_last_channel",
        "lora915_last_snr",
        "lora915_last_duration",
        "lora915_uptime",
        "lora915_event_count",
        "lora915_noise_floor_906",
        "lora915_noise_floor_908",
    }
