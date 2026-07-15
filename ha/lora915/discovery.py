"""Publish (or preview) MQTT discovery configs for the sdr-fleet lora915 detector.

Renders sensor_map.py into retained discovery payloads on
homeassistant/<component>/lora915/<object_id>/config.

Usage:
    uv run --group weather python ha/lora915/discovery.py --dry-run   # print payloads
    uv run --group weather python ha/lora915/discovery.py             # publish (retained)
    uv run --group weather python ha/lora915/discovery.py --remove    # delete configs

Broker settings come from MQTT_HOST / MQTT_USER / MQTT_PASSWORD in the environment
(source .env first). Publishing is idempotent: re-running updates definitions in
place. Publishing writes HA entities — operator confirmation required (CLAUDE.md);
--dry-run is always safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import sensor_map


def build_configs() -> list[tuple[str, dict]]:
    """Return (discovery_topic, payload) for every entity in sensor_map."""
    out: list[tuple[str, dict]] = []
    for component, entries in (
        ("binary_sensor", sensor_map.BINARY_SENSORS),
        ("sensor", sensor_map.SENSORS),
    ):
        for entry in entries:
            entry = dict(entry)
            object_id = entry.pop("object_id")
            payload = {
                "name": entry.pop("name"),
                "unique_id": object_id,
                "object_id": object_id,
                "device": sensor_map.DEVICE,
                **entry,
            }
            discovery_topic = (
                f"{sensor_map.DISCOVERY_PREFIX}/{component}/{sensor_map.NODE_ID}"
                f"/{object_id}/config"
            )
            out.append((discovery_topic, payload))
    return out


def connect_client():
    import paho.mqtt.client as mqtt

    host = os.environ.get("MQTT_HOST", "192.168.6.39")
    user = os.environ.get("MQTT_USER")
    password = os.environ.get("MQTT_PASSWORD")
    if not user or not password:
        sys.exit("MQTT_USER / MQTT_PASSWORD not set (source .env first)")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(user, password)
    client.connect(host, int(os.environ.get("MQTT_PORT", "1883")), 30)
    client.loop_start()
    return client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print payloads, publish nothing")
    parser.add_argument("--remove", action="store_true", help="publish empty retained payloads")
    args = parser.parse_args()

    configs = build_configs()

    if args.dry_run:
        for topic, payload in configs:
            print(f"=== {topic} ===")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\n{len(configs)} discovery configs (dry run — nothing published)")
        return

    client = connect_client()
    for topic, payload in configs:
        body = "" if args.remove else json.dumps(payload, ensure_ascii=False)
        info = client.publish(topic, body, qos=1, retain=True)
        info.wait_for_publish(timeout=10)
        print(f"{'removed' if args.remove else 'published'} {topic}")
    time.sleep(1)
    client.loop_stop()
    client.disconnect()
    print(f"\n{len(configs)} configs {'removed' if args.remove else 'published'}")


if __name__ == "__main__":
    main()
