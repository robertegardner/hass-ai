# LoRa 915 — sdr-fleet lora915 activity in Home Assistant

HA-side discovery for the sdr-fleet `lora915` detector (RTL-SDR on DAVIS1 @
sdr-indoor). Publishes retained MQTT discovery configs that surface 915 MHz
LoRa **activity** in HA.

Data path: LoRa TX (YoLink gates + Meshtastic) → RTL-SDR OTA capture →
`lora_detect.py` (sdr-fleet) → MQTT `sdr/lora915/{activity,status}` on
192.168.6.39 (emqx.srvr) → discovery entities → HA.

## What it is (and isn't)

An **activity** monitor, not a decoder. It reports that a LoRa device
transmitted in a watched channel, when, how long, and how strong — never which
device or what it said. YoLink and Meshtastic US LongFast share the 906.88 MHz
window; entities are named generically. Empirically YoLink bursts run
~0.64–0.68 s and Meshtastic LongFast ~1.0–1.05 s (longer payload + flood
rebroadcast), which is the seed of a future duration-based discriminator.

## Entities (device "LoRa 915 Monitor")

| Entity | Source | Notes |
|---|---|---|
| `binary_sensor.lora915_activity` | activity | Momentary; `off_delay` 8 s; last burst as attributes (channel/snr/dur) |
| `sensor.lora915_last_channel` | activity | e.g. `906.88` |
| `sensor.lora915_last_snr` | activity | dB |
| `sensor.lora915_last_duration` | activity | s |
| `sensor.lora915_uptime` | status (diag) | expires → unavailable if detector dies |
| `sensor.lora915_event_count` | status (diag) | `total_increasing` (resets on restart) |
| `sensor.lora915_noise_floor_906` | status (diag) | dB |
| `sensor.lora915_noise_floor_908` | status (diag) | dB |

The retained `sdr/lora915/status` heartbeat (every 60 s) is the availability
signal: `expire_after` 200 s marks the diagnostics unavailable when the SDR
pipeline stalls silently (indistinguishable from a quiet band otherwise).

## Order of operations

```bash
set -a && . ./.env && set +a
uv run --group weather python ha/lora915/discovery.py --dry-run   # review
uv run --group weather python ha/lora915/discovery.py             # publish (writes HA)
uv run --group weather python ha/lora915/discovery.py --remove    # delete
```

Publishing writes HA entities — operator confirmation required (CLAUDE.md hard
rule); `--dry-run` is always safe.
