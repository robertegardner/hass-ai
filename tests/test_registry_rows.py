from pae.ingest.registry import build_registry_rows

ENTITIES = [
    {  # area set directly on the entity
        "entity_id": "light.kitchen",
        "name": "Kitchen Light",
        "original_name": "Hue Bulb 1",
        "area_id": "kitchen",
        "device_id": "dev1",
        "original_device_class": None,
    },
    {  # no own area -> inherits the device's area
        "entity_id": "binary_sensor.hall_motion",
        "name": None,
        "original_name": "Hall Motion",
        "area_id": None,
        "device_id": "dev2",
        "original_device_class": "motion",
    },
    {  # no device at all
        "entity_id": "scene.movie_night",
        "name": None,
        "original_name": "Movie Night",
        "area_id": None,
        "device_id": None,
    },
]
DEVICES = [
    {"id": "dev1", "area_id": "kitchen"},
    {"id": "dev2", "area_id": "hallway"},
]
AREAS = [
    {"area_id": "kitchen", "name": "Kitchen"},
    {"area_id": "hallway", "name": "Hallway"},
]


def test_build_registry_rows():
    rows = {r["entity_id"]: r for r in build_registry_rows(ENTITIES, DEVICES, AREAS)}

    kitchen = rows["light.kitchen"]
    assert kitchen["friendly_name"] == "Kitchen Light"  # renamed name wins
    assert kitchen["area_name"] == "Kitchen"
    assert kitchen["domain"] == "light"

    hall = rows["binary_sensor.hall_motion"]
    assert hall["friendly_name"] == "Hall Motion"  # falls back to original_name
    assert hall["area_id"] == "hallway"  # inherited from device
    assert hall["area_name"] == "Hallway"
    assert hall["device_class"] == "motion"

    scene = rows["scene.movie_night"]
    assert scene["area_id"] is None
    assert scene["area_name"] is None
