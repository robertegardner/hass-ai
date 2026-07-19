"""Sync guard: the CYD bar panel YAML must track the tablet's entity tables."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha" / "basement_tablet"))

from entities import ALL_OFF_TARGETS, ZONES  # noqa: E402

YAML_PATH = Path(__file__).resolve().parents[1] / "ha" / "cyd_bar" / "basement-cyd-bar.yaml"


class _EsphomeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates ESPHome's !secret / !lambda tags."""


_EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _load():
    return yaml.load(YAML_PATH.read_text(), Loader=_EsphomeLoader)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _service_calls(config, service):
    return [d for d in _walk(config) if d.get("action") == service]


def test_all_off_matches_tablet():
    calls = _service_calls(_load(), "homeassistant.turn_off")
    assert len(calls) == 1
    targets = [e.strip() for e in calls[0]["data"]["entity_id"].split(",")]
    assert targets == ALL_OFF_TARGETS


def test_imported_entities_are_tablet_zones():
    imported = {
        d["entity_id"]
        for d in _walk(_load())
        if d.get("platform") == "homeassistant" and "entity_id" in d
    }
    assert imported == {ZONES["bar"], ZONES["cans"]}


def test_toggle_and_dim_target_tablet_zones():
    cfg = _load()
    toggles = {c["data"]["entity_id"] for c in _service_calls(cfg, "homeassistant.toggle")}
    assert toggles == {ZONES["bar"], ZONES["cans"]}
    dims = {c["data"]["entity_id"] for c in _service_calls(cfg, "light.turn_on")}
    assert dims == {ZONES["bar"]}
    offs = {c["data"]["entity_id"] for c in _service_calls(cfg, "light.turn_off")}
    assert offs == {ZONES["bar"]}
