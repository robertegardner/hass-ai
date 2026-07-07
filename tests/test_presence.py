from pae.ingest.presence import person_object_id, room_from_camera_name


def test_room_from_camera_name():
    assert room_from_camera_name("Kitchen Person detected") == "Kitchen"
    assert room_from_camera_name("Living Room Person detected") == "Living Room"
    # real data has double spaces from camera renames
    assert room_from_camera_name("Pool Patio  Person detected") == "Pool Patio"
    assert room_from_camera_name("Front Walk  Person detected") == "Front Walk"


def test_room_without_suffix_passes_through():
    assert room_from_camera_name("Driveway") == "Driveway"


def test_person_object_id():
    assert person_object_id("person.robert_gardner") == "robert_gardner"
