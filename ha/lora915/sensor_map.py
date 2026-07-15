"""Single source of truth: sdr/lora915 MQTT topics -> HA discovery definitions.

The sdr-fleet `lora915` profile (DAVIS1 @ sdr-indoor) is an ACTIVITY detector,
not a decoder: it reports that *a* LoRa device transmitted in a watched 915 MHz
channel, when, how long, and how strong — but not which device or what it said.
YoLink gate sensors and Meshtastic US LongFast share the 906.88 MHz window and
are indistinguishable here, so these entities are named generically ("LoRa 915
activity"), NOT "YoLink". Split/rename when decode-based tagging lands.

Two source topics:
  sdr/lora915/activity  retain=0, per burst:
      {ts, t_rel, channel, dur_s, snr_db, kind}
  sdr/lora915/status    retain=1, every 60 s (heartbeat / health):
      {ts, uptime_s, events, rejected, deduped, floors_db{ch:dB}, fc_mhz, channels}

The status heartbeat doubles as the availability signal: STATUS_EXPIRE_S marks
every diagnostic entity unavailable if the detector pipeline dies (a silent
rtl_sdr | detector stall looks identical to a quiet band otherwise).
"""

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "lora915"

ACTIVITY_TOPIC = "sdr/lora915/activity"
STATUS_TOPIC = "sdr/lora915/status"

# Momentary activity: HA drops the binary sensor back to off this many seconds
# after the last burst (a YoLink open+close pair lands ~2 s apart; supervision
# check-ins are sparse). off_delay resets on every new message.
ACTIVITY_OFF_DELAY_S = 8

# Heartbeat is every 60 s; 3 missed beats -> unavailable.
STATUS_EXPIRE_S = 200

DEVICE = {
    "identifiers": ["sdr_lora915"],
    "name": "LoRa 915 Monitor",
    "manufacturer": "sdr-fleet",
    "model": "lora915 CSS activity detector (RTL-SDR, DAVIS1 @ sdr-indoor)",
    # receiver is the indoor whip; it monitors 915 MHz LoRa (YoLink gates +
    # Meshtastic) from wherever those transmit — the device itself is infra.
    "suggested_area": "Network",
}

# Momentary activity blip + last-burst detail as attributes.
BINARY_SENSORS: list[dict] = [
    {
        "object_id": "lora915_activity",
        "name": "LoRa 915 activity",
        "state_topic": ACTIVITY_TOPIC,
        # every message = a burst; template yields the payload_on token.
        "value_template": "ON",
        "off_delay": ACTIVITY_OFF_DELAY_S,
        "json_attributes_topic": ACTIVITY_TOPIC,
        "icon": "mdi:access-point",
    },
]

# state = last-event field (activity) or heartbeat field (status).
SENSORS: list[dict] = [
    {
        "object_id": "lora915_last_channel",
        "name": "LoRa 915 last channel",
        "state_topic": ACTIVITY_TOPIC,
        "value_template": "{{ value_json.channel }}",
        "icon": "mdi:radio-tower",
    },
    {
        "object_id": "lora915_last_snr",
        "name": "LoRa 915 last SNR",
        "state_topic": ACTIVITY_TOPIC,
        "value_template": "{{ value_json.snr_db }}",
        "unit_of_measurement": "dB",
        "state_class": "measurement",
        "suggested_display_precision": 1,
    },
    {
        "object_id": "lora915_last_duration",
        "name": "LoRa 915 last burst length",
        "state_topic": ACTIVITY_TOPIC,
        "value_template": "{{ value_json.dur_s }}",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "state_class": "measurement",
        "suggested_display_precision": 2,
    },
    # ---- diagnostics off the retained heartbeat (expire => pipeline dead) ----
    {
        "object_id": "lora915_uptime",
        "name": "LoRa 915 detector uptime",
        "state_topic": STATUS_TOPIC,
        "value_template": "{{ value_json.uptime_s }}",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "state_class": "total_increasing",
        "entity_category": "diagnostic",
        "expire_after": STATUS_EXPIRE_S,
    },
    {
        "object_id": "lora915_event_count",
        "name": "LoRa 915 event count",
        "state_topic": STATUS_TOPIC,
        "value_template": "{{ value_json.events }}",
        # counter resets to 0 on detector restart — total_increasing handles it.
        "state_class": "total_increasing",
        "entity_category": "diagnostic",
        "expire_after": STATUS_EXPIRE_S,
        "icon": "mdi:counter",
    },
    {
        "object_id": "lora915_noise_floor_906",
        "name": "LoRa 915 noise floor 906.88",
        "state_topic": STATUS_TOPIC,
        # floors_db keys carry a dot — bracket-index, not dotted attribute.
        "value_template": "{{ value_json.floors_db['906.88'] }}",
        "unit_of_measurement": "dB",
        "state_class": "measurement",
        "suggested_display_precision": 1,
        "entity_category": "diagnostic",
        "expire_after": STATUS_EXPIRE_S,
    },
    {
        "object_id": "lora915_noise_floor_908",
        "name": "LoRa 915 noise floor 908.40",
        "state_topic": STATUS_TOPIC,
        "value_template": "{{ value_json.floors_db['908.40'] }}",
        "unit_of_measurement": "dB",
        "state_class": "measurement",
        "suggested_display_precision": 1,
        "entity_category": "diagnostic",
        "expire_after": STATUS_EXPIRE_S,
    },
]
