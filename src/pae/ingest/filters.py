"""Which entities are behavioral signal vs. noise.

Allowlist of actionable/behavioral domains, plus occupancy-ish binary
sensors and UniFi person detections. Everything else (power sensors, RSSI,
firmware update entities, ...) is dropped before it touches the database.
"""

import fnmatch

BEHAVIORAL_DOMAINS = frozenset(
    {"light", "switch", "media_player", "climate", "fan", "scene", "cover", "person", "lock"}
)
OCCUPANCY_DEVICE_CLASSES = frozenset({"motion", "occupancy", "presence"})
PERSON_DETECTED_PATTERN = "binary_sensor.*person_detected*"


def parse_denylist(raw: str) -> tuple[str, ...]:
    return tuple(g.strip() for g in raw.split(",") if g.strip())


def should_ingest(
    entity_id: str,
    device_class: str | None,
    denylist: tuple[str, ...] = (),
) -> bool:
    for glob in denylist:
        if fnmatch.fnmatch(entity_id, glob):
            return False
    domain = entity_id.split(".", 1)[0]
    if domain in BEHAVIORAL_DOMAINS:
        return True
    if domain == "binary_sensor":
        if device_class in OCCUPANCY_DEVICE_CLASSES:
            return True
        # UniFi Protect person detections have no device_class
        return fnmatch.fnmatch(entity_id, PERSON_DETECTED_PATTERN)
    return False
