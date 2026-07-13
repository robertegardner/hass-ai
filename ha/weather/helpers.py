"""Create the derived weather helpers in HA via config-flow API.

Helpers created (all UI helpers — no HA config file access needed):
  - derivative:    sensor.davis_pressure_derivative  (3 h window on the weewx barometer)
  - template:      sensor.davis_dewpoint             (Magnus, from temp + humidity)
  - template:      sensor.davis_feels_like           (heat index / wind chill blend)
  - template:      sensor.davis_wind_cardinal        (degrees -> N/NNE/...)
  - template:      sensor.davis_rain_total           (tips x 0.01 in)
  - template:      sensor.davis_pressure_trend       (rising/steady/falling)
  - utility_meter: sensor.davis_rain_today           (daily cycle, wrap-tolerant)

The ISS tip counter wraps at 128; davis_rain_today is a utility_meter with
periodically_resetting=True over davis_rain_total, so a wrap registers as a meter
reset instead of lost (or phantom negative) rain.

Usage:
    uv run --group weather python ha/weather/helpers.py --dry-run   # walk flows, create nothing
    uv run --group weather python ha/weather/helpers.py             # create (idempotent by title)

Needs HA_TOKEN in the environment (source .env first).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

HA_URL = os.environ.get("HA_URL", "http://homeassistant.iot:8123")

DEWPOINT_TEMPLATE = """\
{% set t = states('sensor.davis_temperature') | float(none) %}
{% set rh = states('sensor.davis_humidity') | float(none) %}
{% if t is not none and rh is not none and rh > 0 %}
{% set tc = (t - 32) * 5 / 9 %}
{% set g = log(rh / 100) + (17.62 * tc) / (243.12 + tc) %}
{{ ((243.12 * g / (17.62 - g)) * 9 / 5 + 32) | round(1) }}
{% endif %}"""

# NWS conventions: Rothfusz heat index at/above 80 °F, wind chill at/below 50 °F
# with wind > 3 mph, otherwise the plain temperature.
FEELS_LIKE_TEMPLATE = """\
{% set t = states('sensor.davis_temperature') | float(none) %}
{% set rh = states('sensor.davis_humidity') | float(none) %}
{% set v = states('sensor.davis_wind_speed') | float(none) %}
{% if t is none %}
{% elif t >= 80 and rh is not none %}
{% set hi = -42.379 + 2.04901523*t + 10.14333127*rh - 0.22475541*t*rh
   - 0.00683783*t*t - 0.05481717*rh*rh + 0.00122874*t*t*rh
   + 0.00085282*t*rh*rh - 0.00000199*t*t*rh*rh %}
{{ hi | round(1) }}
{% elif t <= 50 and v is not none and v > 3 %}
{{ (35.74 + 0.6215*t - 35.75*(v**0.16) + 0.4275*t*(v**0.16)) | round(1) }}
{% else %}
{{ t | round(1) }}
{% endif %}"""

WIND_CARDINAL_TEMPLATE = """\
{% set d = states('sensor.davis_wind_direction') | float(none) %}
{% if d is not none %}
{{ ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
   [((d / 22.5) + 0.5) | int % 16] }}
{% endif %}"""

RAIN_TOTAL_TEMPLATE = """\
{% set tips = states('sensor.davis_rain_tips') | float(none) %}
{% if tips is not none %}
{{ (tips * 0.01) | round(2) }}
{% endif %}"""

PRESSURE_TREND_TEMPLATE = """\
{% set d = states('sensor.davis_pressure_derivative') | float(none) %}
{% if d is none %}
unknown
{% elif d > 0.02 %}
rising
{% elif d < -0.02 %}
falling
{% else %}
steady
{% endif %}"""

# Each flow: handler + the menu choice (template flows) + form data per step.
# Titles double as the idempotency key against existing config entries.
HELPERS: list[dict] = [
    {
        "handler": "derivative",
        "title": "Davis pressure derivative",
        "steps": {
            "user": {
                "name": "Davis pressure derivative",
                "source": "sensor.weewx_barometric_pressure",
                "round": 3,
                "time_window": {"hours": 3, "minutes": 0, "seconds": 0},
                "unit_time": "h",
            }
        },
    },
    {
        "handler": "template",
        "title": "Davis dewpoint",
        "menu": "sensor",
        "steps": {
            "sensor": {
                "name": "Davis dewpoint",
                "state": DEWPOINT_TEMPLATE,
                "unit_of_measurement": "°F",
                "device_class": "temperature",
                "state_class": "measurement",
            }
        },
    },
    {
        "handler": "template",
        "title": "Davis feels like",
        "menu": "sensor",
        "steps": {
            "sensor": {
                "name": "Davis feels like",
                "state": FEELS_LIKE_TEMPLATE,
                "unit_of_measurement": "°F",
                "device_class": "temperature",
                "state_class": "measurement",
            }
        },
    },
    {
        "handler": "template",
        "title": "Davis wind cardinal",
        "menu": "sensor",
        "steps": {
            "sensor": {
                "name": "Davis wind cardinal",
                "state": WIND_CARDINAL_TEMPLATE,
            }
        },
    },
    {
        "handler": "template",
        "title": "Davis rain total",
        "menu": "sensor",
        "steps": {
            "sensor": {
                "name": "Davis rain total",
                "state": RAIN_TOTAL_TEMPLATE,
                "unit_of_measurement": "in",
                "device_class": "precipitation",
                "state_class": "total_increasing",
            }
        },
    },
    {
        "handler": "template",
        "title": "Davis pressure trend",
        "menu": "sensor",
        "steps": {
            "sensor": {
                "name": "Davis pressure trend",
                "state": PRESSURE_TREND_TEMPLATE,
            }
        },
    },
    {
        "handler": "utility_meter",
        "title": "Davis rain today",
        "steps": {
            "user": {
                "name": "Davis rain today",
                "source": "sensor.davis_rain_total",
                "cycle": "daily",
                "offset": 0,
                "periodically_resetting": True,
                "delta_values": False,
                "net_consumption": False,
                "tariffs": [],
            }
        },
    },
]


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{HA_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['HA_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or "{}")


def existing_titles() -> set[str]:
    entries = api("GET", "/api/config/config_entries/entry")
    return {e["title"] for e in entries}


def drive_flow(helper: dict, dry_run: bool) -> str:
    """Walk one config flow to completion (or abort it in dry-run)."""
    flow = api("POST", "/api/config/config_entries/flow", {"handler": helper["handler"]})
    try:
        for _ in range(6):  # menu -> form(s) -> create_entry
            kind = flow["type"]
            if kind == "menu":
                flow = api(
                    "POST",
                    f"/api/config/config_entries/flow/{flow['flow_id']}",
                    {"next_step_id": helper["menu"]},
                )
            elif kind == "form":
                step = flow["step_id"]
                if step not in helper["steps"]:
                    # Trailing confirm-style steps (e.g. template preview) take {}.
                    data = {}
                else:
                    data = helper["steps"][step]
                if dry_run:
                    schema = [f"{f.get('name')}{'*' if f.get('required') else ''}"
                              for f in flow.get("data_schema") or []]
                    print(f"  step '{step}' schema: {schema}")
                    print(f"  would submit: {json.dumps(data)[:200]}")
                    return "dry-run ok"
                flow = api(
                    "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", data
                )
                if flow.get("errors"):
                    return f"ERRORS: {flow['errors']}"
            elif kind == "create_entry":
                return "created"
            else:
                return f"unexpected flow type: {kind}"
        return "did not converge"
    finally:
        if dry_run:
            try:
                api("DELETE", f"/api/config/config_entries/flow/{flow['flow_id']}")
            except Exception:
                pass  # flow already finished or expired


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if "HA_TOKEN" not in os.environ:
        sys.exit("HA_TOKEN not set (source .env first)")

    have = existing_titles()
    for helper in HELPERS:
        if helper["title"] in have:
            print(f"skip (exists): {helper['title']}")
            continue
        print(f"{'dry-run' if args.dry_run else 'create'}: {helper['title']} "
              f"[{helper['handler']}]")
        print(f"  -> {drive_flow(helper, args.dry_run)}")


if __name__ == "__main__":
    main()
