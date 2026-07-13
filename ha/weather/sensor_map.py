"""Single source of truth: sdr/davis MQTT topics -> HA discovery sensor definitions.

Every entity the Davis Vantage Pro2 Plus exposes in Home Assistant is defined here.
discovery.py renders these into retained MQTT discovery config payloads; the tests
pin the invariants (unique ids, state_class on numerics, expire_after on live values).

Payload reference: sample_payload.json (captured 2026-07-13). The ISS does not
transmit barometric pressure over the air (pressure comes from
sensor.weewx_barometric_pressure) and station health comes from the
sdr/davis/health JSON. Battery is a status bit only — every ISS packet carries a
battery-low flag in its header (bridged as sdr/davis/battery_ok) — there is no
battery voltage OTA.
"""

BASE_TOPIC = "sdr/davis"
DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "davis_vantage"

# Live readings older than this mark the entity unavailable instead of showing
# stale numbers (the ISS transmits every ~2.5 s; health/rain groups every ~60 s).
EXPIRE_AFTER_S = 300

DEVICE = {
    "identifiers": ["davis_vantage_pro2_plus"],
    "name": "Davis Vantage Pro2 Plus",
    "manufacturer": "Davis Instruments",
    "model": "Vantage Pro2 Plus (SDR OTA capture)",
    "suggested_area": "Outside",
}

# Each entry becomes homeassistant/<component>/davis_vantage/<object_id>/config.
# Keys map 1:1 onto MQTT discovery options; "topic" is the suffix under BASE_TOPIC.
SENSORS: list[dict] = [
    {
        "object_id": "davis_temperature",
        "name": "Outside temperature",
        "topic": "temperature_f",
        "device_class": "temperature",
        "unit_of_measurement": "°F",
        "state_class": "measurement",
        "suggested_display_precision": 1,
    },
    {
        "object_id": "davis_humidity",
        "name": "Outside humidity",
        "topic": "humidity",
        "device_class": "humidity",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "suggested_display_precision": 0,
    },
    {
        "object_id": "davis_wind_speed",
        "name": "Wind speed",
        "topic": "wind_speed_mph",
        "device_class": "wind_speed",
        "unit_of_measurement": "mph",
        "state_class": "measurement",
        "suggested_display_precision": 0,
    },
    {
        "object_id": "davis_wind_gust",
        "name": "Wind gust",
        "topic": "wind_gust_mph",
        "device_class": "wind_speed",
        "unit_of_measurement": "mph",
        "state_class": "measurement",
        "suggested_display_precision": 0,
    },
    {
        "object_id": "davis_wind_direction",
        "name": "Wind direction",
        "topic": "wind_dir_deg",
        "unit_of_measurement": "°",
        "state_class": "measurement",
        "icon": "mdi:compass-outline",
        "suggested_display_precision": 0,
    },
    {
        "object_id": "davis_rain_rate",
        "name": "Rain rate",
        "topic": "rain_rate_in_hr",
        "device_class": "precipitation_intensity",
        "unit_of_measurement": "in/h",
        "state_class": "measurement",
        "suggested_display_precision": 2,
    },
    {
        # Raw ISS bucket-tip counter (0.01 in/tip, wraps at 128). Kept as a
        # diagnostic; rain totals are derived helpers built on top of this.
        "object_id": "davis_rain_tips",
        "name": "Rain tip counter",
        "topic": "rain_tips",
        "state_class": "measurement",
        "icon": "mdi:cup-water",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "davis_uv",
        "name": "UV index",
        "topic": "uv_index",
        "unit_of_measurement": "UV index",
        "state_class": "measurement",
        "icon": "mdi:sun-wireless-outline",
        "suggested_display_precision": 1,
    },
    {
        "object_id": "davis_solar_radiation",
        "name": "Solar radiation",
        "topic": "solar_wm2",
        "device_class": "irradiance",
        "unit_of_measurement": "W/m²",
        "state_class": "measurement",
        "suggested_display_precision": 0,
    },
    {
        # Staleness indicator for the whole pipeline; deliberately no expire_after
        # so the timestamp survives an outage and shows how old the last packet is.
        "object_id": "davis_last_packet",
        "name": "Last packet",
        "topic": "last_packet_epoch",
        "device_class": "timestamp",
        "value_template": "{{ value | int | as_datetime }}",
        "entity_category": "diagnostic",
        "expire_after": None,
    },
    {
        "object_id": "davis_packets_per_min",
        "name": "Packets per minute",
        "topic": "health",
        "value_template": "{{ value_json.packets_per_min }}",
        "unit_of_measurement": "pkts/min",
        "state_class": "measurement",
        "icon": "mdi:radio-tower",
        "entity_category": "diagnostic",
        "expire_after": 600,
    },
    {
        "object_id": "davis_missed_packets",
        "name": "Missed packets",
        "topic": "health",
        "value_template": "{{ (value_json.missed_ratio * 100) | round(2) }}",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "icon": "mdi:radio-off",
        "entity_category": "diagnostic",
        "expire_after": 600,
    },
]

BINARY_SENSORS: list[dict] = [
    {
        # Battery-low header bit, bridged as battery_ok (1 = OK) on every
        # packet. device_class battery inverts: ON = low battery.
        "object_id": "davis_battery",
        "name": "Transmitter battery",
        "topic": "battery_ok",
        "value_template": "{{ 'OFF' if value | int == 1 else 'ON' }}",
        "device_class": "battery",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "davis_fhss_locked",
        "name": "FHSS locked",
        "topic": "health",
        "value_template": "{{ 'ON' if value_json.fhss_locked else 'OFF' }}",
        "device_class": "connectivity",
        "entity_category": "diagnostic",
        "expire_after": 600,
    },
]
