# Vantage — Davis weather dashboard tooling

HA-side tooling for the Davis Vantage Pro2 Plus weather dashboard. Standalone from the
PAE package on purpose: PAE's HA client is read-only enforced; these scripts write to
the MQTT broker and HA and must never route through it.

Data path: Davis ISS → SDR OTA capture → MQTT `sdr/davis/<field>` on 192.168.6.39
(creds in `.env`) → MQTT discovery entities in HA → Lovelace dashboard `weather`.

## Order of operations

```bash
set -a && . ./.env && set +a    # load HA_TOKEN + MQTT_* first

# 1. Sensor layer (retained discovery configs; idempotent)
uv run --group weather python ha/weather/discovery.py --dry-run   # review
uv run --group weather python ha/weather/discovery.py             # publish

# 2. Derived helpers (dewpoint, feels-like, rain totals, pressure trend)
uv run --group weather python ha/weather/helpers.py --dry-run
uv run --group weather python ha/weather/helpers.py

# 3. Dashboard (HACS cards must be installed first: apexcharts-card,
#    lovelace-windrose-card, hourly-weather, lovelace-horizon-card)
uv run --group weather python ha/weather/build_dashboard.py        # writes dashboard.json
uv run --group weather python ha/weather/deploy_dashboard.py       # create/update in HA
```

Every publish/deploy step writes to HA or the broker — operator confirmation required
(CLAUDE.md hard rule). `--dry-run` variants are always safe.

## Facts that shaped the design

- The ISS does **not** transmit barometric pressure or battery over the air. Pressure
  tiles use `sensor.weewx_barometric_pressure`; station health uses the
  `sdr/davis/health` JSON (packets/min, missed ratio, FHSS lock) plus packet age.
- `rain_tips` is the raw bucket-tip counter (0.01 in/tip, **wraps at 128**). Daily rain
  is a utility_meter with `periodically_resetting` over the derived total so wraps
  register as meter resets, not lost rain.
- `weather.kmocapeg36` (Wunderground) is daily-only; the hourly strip uses
  `weather.pirateweather`.
- Live sensors carry `expire_after` so a dead SDR pipeline reads `unavailable`, and
  `state_class: measurement` everywhere numeric so long-term statistics exist for the
  7-day charts.

## Kiosk

Wall tablet URL: `http://homeassistant.iot:8123/weather-station/now?wp_enabled=true`
(wallpanel is disabled by default in the dashboard config; the URL param activates it).

If the tablet stutters on blur, set `BLUR_ENABLED = False` in `build_dashboard.py`,
rebuild, redeploy — swaps glass blur for opaque fills, same look at 90%.
