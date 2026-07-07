from pae.ingest.filters import parse_denylist, should_ingest


def test_behavioral_domains_ingested():
    for entity in (
        "light.kitchen",
        "switch.fan",
        "media_player.living_room",
        "climate.main",
        "fan.bedroom",
        "scene.movie_night",
        "cover.garage_door",
        "person.robert_gardner",
        "lock.front_door",
    ):
        assert should_ingest(entity, None)


def test_noise_domains_dropped():
    for entity in (
        "sensor.power_meter",
        "button.restart",
        "device_tracker.phone",
        "update.firmware",
        "number.volume",
        "automation.morning",
    ):
        assert not should_ingest(entity, None)


def test_binary_sensor_occupancy_classes_only():
    assert should_ingest("binary_sensor.hall_motion", "motion")
    assert should_ingest("binary_sensor.office_occupancy", "occupancy")
    assert should_ingest("binary_sensor.desk_presence", "presence")
    assert not should_ingest("binary_sensor.front_door", "door")
    assert not should_ingest("binary_sensor.leak", "moisture")
    assert not should_ingest("binary_sensor.random", None)


def test_unifi_person_detection_ingested_despite_no_device_class():
    assert should_ingest("binary_sensor.g6_instant_person_detected_2", None)
    assert should_ingest("binary_sensor.pool_deck_person_detected", None)


def test_denylist_globs():
    denylist = parse_denylist("light.debug_*, switch.test_bench")
    assert denylist == ("light.debug_*", "switch.test_bench")
    assert not should_ingest("light.debug_strip", None, denylist)
    assert not should_ingest("switch.test_bench", None, denylist)
    assert should_ingest("light.kitchen", None, denylist)
