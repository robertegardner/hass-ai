# CYD Bar Panel (basement-cyd-bar)

ESP32-2432S028R ("Cheap Yellow Display", classic 2.8" resistive) mounted portrait at
the basement bar. Bar toggle, Cans toggle, horizontal bar dimmer, All Off — same
entities and look as the wall tablet (`ha/basement_tablet/`).

Spec: `docs/superpowers/specs/2026-07-19-cyd-bar-panel-design.md`

## Source of record

`basement-cyd-bar.yaml` in this directory is canonical. It is **built and flashed in
the HA ESPHome add-on** — after any edit here, re-paste the whole file into the
add-on's editor and install. `tests/test_cyd_bar.py` fails if the entity references
drift from `ha/basement_tablet/entities.py`; when it fails, fix the YAML, then re-paste.

## First-time setup

1. Add to the add-on's `secrets.yaml` (wifi_ssid/wifi_password usually exist already):
   - `cyd_bar_api_key` — 32-byte base64 key (the add-on's "generate" button, or
     `openssl rand -base64 32`)
   - `cyd_bar_ota_password`, `cyd_bar_ap_password` — any strong strings
2. ESPHome add-on → New Device → skip wizard → paste `basement-cyd-bar.yaml`.
3. First flash over USB: Install → "Plug into this computer" (browser Web Serial),
   board in download mode if needed (hold BOOT while plugging in). Later updates go OTA.
4. HA will discover the device; adopt it in Settings → Devices & Services → ESPHome.
5. **Required:** in the ESPHome integration entry for this device, enable
   **"Allow the device to make Home Assistant actions"** — without it every tap is
   silently dropped.

## Troubleshooting

- **Colors inverted** (charcoal shows as white): set `invert_colors: true` on the
  display block. Some 2432S028R batches (notably the 2-USB variant) need it.
- **Red/blue swapped**: add `color_order: bgr` (or `rgb`, whichever fixes it) to the
  display block.
- **Touch misaligned**: enable `logger` DEBUG for `touchscreen`, tap the four corners,
  and adjust `calibration:` x/y min/max to the logged raw values. If an axis is
  reversed, add `transform: { mirror_x: true }` (or `mirror_y`) to the touchscreen.
- **Panel dead but backlit**: red dot top-center = HA API disconnected (HA restarting,
  wifi drop). It auto-reconnects; if the dot never clears, check step 5 above and the
  device logs in the add-on.

## On-device verification (after each flash)

- [ ] BAR / CANS tiles toggle their lights both directions; amber when on
- [ ] External change (tablet, Hue app) updates tiles and slider within ~1 s
- [ ] Dimmer: drag → % label tracks; release >0 sets brightness; release at 0 turns off
- [ ] Slider doesn't jump while your finger is on it during external changes
- [ ] ALL OFF kills all 16 entities (same list as the tablet)
- [ ] 30 s idle dims backlight; 5 min blanks it; wake touch lights the screen without
      firing any button
- [ ] Restart HA: red dot appears, then clears; controls work after reconnect
