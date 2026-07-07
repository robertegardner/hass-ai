"""Presence snapshot derivation.

person.* state changes give identified, house/zone-level presence.
UniFi Protect person detections give anonymous, room-level presence; the
room comes from the camera's friendly name (authoritative per operator —
several entity_ids are stale from camera renames).
"""

import re

_PERSON_DETECTED_SUFFIX = re.compile(r"\s*person\s+detected\s*$", re.IGNORECASE)


def room_from_camera_name(friendly_name: str) -> str:
    """'Kitchen Patio  Person detected' -> 'Kitchen Patio'."""
    room = _PERSON_DETECTED_SUFFIX.sub("", friendly_name)
    return re.sub(r"\s+", " ", room).strip()


def person_object_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[1]
