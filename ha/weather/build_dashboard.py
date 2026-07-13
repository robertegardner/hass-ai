"""Generate the "Vantage" weather dashboard config (dashboard.json).

Design system: "Instrument at night" — near-monochrome midnight glass; one
condition-reactive accent hue (mapped from live outside temperature, ice blue at
10 °F to ember at 95 °F, modulated by condition) tints the hero glow, tile
hairlines, and wind needle. Rain data is the only other color; amber is reserved
for station-health alerts.

All styling constants live here (storage-mode dashboards can't share YAML anchors
or theme files). Never hand-edit the deployed dashboard — change this generator,
rebuild, redeploy:

    uv run --group weather python ha/weather/build_dashboard.py
    uv run --group weather python ha/weather/deploy_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

# Set False if the wall tablet stutters: swaps blur for a slightly more opaque
# flat fill (GPU-free, visually ~90% the same).
BLUR_ENABLED = True

# ---------------------------------------------------------------- tokens

BG_VIEW = (
    "radial-gradient(ellipse 120% 80% at 50% -10%, "
    "#0b1322 0%, #060b15 55%, #04070d 100%) fixed"
)
GLASS_BG = "rgba(140,165,215,0.055)" if BLUR_ENABLED else "rgba(20,30,50,0.82)"
GLASS_EDGE = "rgba(150,175,225,0.13)"
BLUR = "blur(14px) saturate(1.2)" if BLUR_ENABLED else "none"
TEXT_HI = "#eef2fa"
TEXT_LO = "rgba(238,242,250,0.52)"
TEXT_FAINT = "rgba(238,242,250,0.30)"
RAIN = "#55c2f0"
ALERT = "#ffb454"
STEEL = "hsl(210 40% 65%)"  # design-time neutral for static configs (rose petals)
FONT = (
    '-apple-system, BlinkMacSystemFont, "SF Pro Display", Roboto, '
    '"Segoe UI", "Noto Sans", sans-serif'
)

E = {
    "temp": "sensor.davis_temperature",
    "humidity": "sensor.davis_humidity",
    "dewpoint": "sensor.davis_dewpoint",
    "feels": "sensor.davis_feels_like",
    "wind": "sensor.davis_wind_speed",
    "gust": "sensor.davis_wind_gust",
    "wind_dir": "sensor.davis_wind_direction",
    "cardinal": "sensor.davis_wind_cardinal",
    "rain_rate": "sensor.davis_rain_rate",
    "rain_today": "sensor.davis_rain_today",
    "rain_total": "sensor.davis_rain_total",
    "baro": "sensor.weewx_barometric_pressure",
    "baro_deriv": "sensor.davis_pressure_derivative",
    "baro_trend": "sensor.davis_pressure_trend",
    "uv": "sensor.davis_uv",
    "solar": "sensor.davis_solar_radiation",
    "pkts": "sensor.davis_packets_per_min",
    "missed": "sensor.davis_missed_packets",
    "fhss": "binary_sensor.davis_fhss_locked",
    "battery": "binary_sensor.davis_battery",
    "last_packet": "sensor.davis_last_packet",
    "forecast_daily": "weather.kmocapeg36",
    "forecast_hourly": "weather.pirateweather",
    "hi_today": "sensor.kmocapeg36_forecast_temperature_0d",
    "lo_tonight": "sensor.kmocapeg36_forecast_temperature_1n",
}

# ------------------------------------------------- shared JS + CSS constants

# The signature: one hue for the whole board, derived from live temperature.
_ACCENT_BODY = f"""
var t = parseFloat(states['{E["temp"]}'] ? states['{E["temp"]}'].state : 'NaN');
var w = states['{E["forecast_daily"]}'];
var cond = w ? w.state : '';
if (isNaN(t)) return 'hsl(215 25% 62% / ALPHA)';
var f = Math.min(1, Math.max(0, (t - 10) / 85));
var h = 225 - f * 213, s = 65, l = 68;
if (['rainy','pouring','lightning','lightning-rainy'].indexOf(cond) >= 0) {{ h = 205; s = 70; }}
if (['snowy','snowy-rainy','hail'].indexOf(cond) >= 0) {{ h = 210; s = 30; l = 80; }}
if (['fog','cloudy'].indexOf(cond) >= 0) {{ s = 25; }}
return 'hsl(' + h + ' ' + s + '% ' + l + '% / ALPHA)';
"""


def accent_js(alpha: float = 1.0) -> str:
    """button-card JS template returning the live accent color at given alpha."""
    return "[[[" + _ACCENT_BODY.replace("ALPHA", str(alpha)) + "]]]"


GLASS_CARD_MOD = f"""
ha-card {{
  background: {GLASS_BG};
  border: 1px solid {GLASS_EDGE};
  border-radius: 22px;
  backdrop-filter: {BLUR};
  -webkit-backdrop-filter: {BLUR};
  box-shadow: 0 8px 30px rgba(0,0,0,0.35);
  color: {TEXT_HI};
  font-family: {FONT};
  --primary-text-color: {TEXT_HI};
  --secondary-text-color: {TEXT_LO};
  --divider-color: {GLASS_EDGE};
  --ha-card-background: transparent;
}}
"""

INNER_CLEAR_MOD = """
ha-card {
  background: none !important;
  border: none !important;
  box-shadow: none !important;
}
"""

APEX_BASE = {
    "chart": {"animations": {"enabled": False}, "toolbar": {"show": False}, "fontFamily": FONT},
    "grid": {"borderColor": "rgba(150,175,225,0.10)", "strokeDashArray": 3},
    "xaxis": {
        "labels": {"style": {"colors": "rgba(238,242,250,0.45)", "fontSize": "10px"}},
        "axisBorder": {"show": False},
        "axisTicks": {"show": False},
    },
    "yaxis": {"labels": {"style": {"colors": "rgba(238,242,250,0.45)", "fontSize": "10px"}}},
    "tooltip": {"theme": "dark"},
    "legend": {"labels": {"colors": "rgba(238,242,250,0.62)"}, "fontSize": "11px"},
    "dataLabels": {"enabled": False},
}

APEX_HEADER_MOD = GLASS_CARD_MOD + f"""
ha-card {{ padding: 12px 8px 4px 8px; }}
#header__title {{
  font-size: 11px; font-weight: 500; letter-spacing: 0.12em;
  text-transform: uppercase; color: {TEXT_LO}; padding: 4px 12px 0 12px;
}}
"""

# Same ramp as accent_js, quantized for apexcharts (it can't run the JS map).
TEMP_STOPS = [
    {"value": -20, "color": "hsl(225 65% 68%)"},
    {"value": 10, "color": "hsl(225 65% 68%)"},
    {"value": 32, "color": "hsl(200 60% 66%)"},
    {"value": 50, "color": "hsl(170 45% 60%)"},
    {"value": 65, "color": "hsl(110 40% 62%)"},
    {"value": 78, "color": "hsl(55 60% 62%)"},
    {"value": 88, "color": "hsl(28 70% 62%)"},
    {"value": 95, "color": "hsl(12 65% 62%)"},
]

EYEBROW_CSS = [
    {"font-size": "11px"},
    {"font-weight": "500"},
    {"letter-spacing": "0.12em"},
    {"text-transform": "uppercase"},
    {"color": TEXT_LO},
    {"justify-self": "start"},
]

# ------------------------------------------------- button-card templates


def _glass_template() -> dict:
    return {
        "show_name": False,
        "show_icon": False,
        "show_state": False,
        "show_label": False,
        "styles": {
            "card": [
                {"background": GLASS_BG},
                {"border": f"1px solid {GLASS_EDGE}"},
                {"border-radius": "22px"},
                {"backdrop-filter": BLUR},
                {"-webkit-backdrop-filter": BLUR},
                {"box-shadow": "0 8px 30px rgba(0,0,0,0.35)"},
                {"color": TEXT_HI},
                {"font-family": FONT},
                {"padding": "16px 18px"},
                {"height": "100%"},
                {"box-sizing": "border-box"},
            ]
        },
    }


def _metric_tile_template() -> dict:
    return {
        "template": "glass",
        "styles": {
            "card": [{"border-top": "[[[ return '1px solid ' + " + _accent_expr(0.55) + " ]]]"}],
            "grid": [
                {"grid-template-areas": '"eyebrow glyph" "value glyph" "sub sub" "extra extra"'},
                {"grid-template-columns": "1fr auto"},
                {"grid-template-rows": "auto 1fr auto auto"},
                {"gap": "2px"},
                {"height": "100%"},
            ],
            "custom_fields": {
                "eyebrow": EYEBROW_CSS,
                "value": [
                    {"font-size": "30px"},
                    {"font-weight": "300"},
                    {"font-variant-numeric": "tabular-nums"},
                    {"letter-spacing": "-0.01em"},
                    {"justify-self": "start"},
                    {"align-self": "center"},
                    {"line-height": "1.15"},
                ],
                "sub": [
                    {"font-size": "12.5px"},
                    {"color": TEXT_LO},
                    {"justify-self": "start"},
                    {"font-variant-numeric": "tabular-nums"},
                ],
                "glyph": [{"align-self": "start"}, {"justify-self": "end"}],
                "extra": [{"width": "100%"}],
            },
        },
    }


def _accent_expr(alpha: float) -> str:
    """The accent computation as a bare JS expression (for embedding in styles)."""
    return (
        "(function(){"
        + _ACCENT_BODY.replace("ALPHA", str(alpha)).replace("\n", " ")
        + "})()"
    )


# ------------------------------------------------- helpers


def unit_span(value_js: str, unit: str, unit_size: str = "14px") -> str:
    """JS returning value + a small muted unit suffix."""
    return (
        f"[[[ var v = {value_js}; return v + "
        f"'<span style=\"font-size:{unit_size};color:{TEXT_LO};font-weight:400\"> {unit}</span>'; ]]]"
    )


def state_num(entity: str, digits: int = 0) -> str:
    return (
        f"(isNaN(parseFloat(states['{entity}'] ? states['{entity}'].state : 'NaN')) ? '–' : "
        f"parseFloat(states['{entity}'].state).toFixed({digits}))"
    )


# ------------------------------------------------- cards: hero


def hero_card() -> dict:
    cond_icon_js = f"""[[[
var w = states['{E["forecast_daily"]}'];
var sun = states['sun.sun'];
var night = sun && sun.state === 'below_horizon';
var map = {{'clear-night':'mdi:weather-night','cloudy':'mdi:weather-cloudy','fog':'mdi:weather-fog',
 'hail':'mdi:weather-hail','lightning':'mdi:weather-lightning','lightning-rainy':'mdi:weather-lightning-rainy',
 'partlycloudy': night ? 'mdi:weather-night-partly-cloudy' : 'mdi:weather-partly-cloudy',
 'pouring':'mdi:weather-pouring','rainy':'mdi:weather-rainy','snowy':'mdi:weather-snowy',
 'snowy-rainy':'mdi:weather-snowy-rainy','sunny': night ? 'mdi:weather-night' : 'mdi:weather-sunny',
 'windy':'mdi:weather-windy','windy-variant':'mdi:weather-windy-variant','exceptional':'mdi:alert-circle-outline'}};
var icon = w ? (map[w.state] || 'mdi:weather-partly-cloudy') : 'mdi:weather-partly-cloudy';
var c = {_accent_expr(0.95)};
return '<ha-icon icon="' + icon + '" style="--mdc-icon-size:50px;color:' + c + '"></ha-icon>';
]]]"""

    temp_js = f"""[[[
var t = {state_num(E["temp"], 1)};
return t + '<span style="font-size:0.38em;font-weight:300;color:{TEXT_LO};vertical-align:super">°F</span>';
]]]"""

    meta_js = f"""[[[
var w = states['{E["forecast_daily"]}'];
var cond = w ? w.state.replace(/-/g, ' ') : '';
cond = cond.replace('partlycloudy','partly cloudy');
cond = cond.charAt(0).toUpperCase() + cond.slice(1);
var f = {state_num(E["feels"], 0)};
return cond + '<span style="color:{TEXT_FAINT}">  ·  </span>Feels ' + f + '°';
]]]"""

    hilo_js = f"""[[[
var hi = {state_num(E["hi_today"], 0)};
var lo = {state_num(E["lo_tonight"], 0)};
var out = [];
if (hi !== '–') out.push('H ' + hi + '°');
if (lo !== '–') out.push('L ' + lo + '°');
return out.join('<span style="color:{TEXT_FAINT}">  ·  </span>');
]]]"""

    return {
        "type": "custom:button-card",
        "template": "glass",
        "entity": E["temp"],
        "view_layout": {"grid-area": "hero"},
        "styles": {
            "card": [{"padding": "22px 26px"}],
            "grid": [
                {"grid-template-areas": '"icon" "temp" "meta" "hilo"'},
                {"grid-template-rows": "auto 1fr auto auto"},
                {"justify-items": "start"},
                {"gap": "4px"},
                {"height": "100%"},
            ],
            "custom_fields": {
                "temp": [
                    {"font-size": "clamp(72px, 8.5vw, 128px)"},
                    {"max-width": "100%"},
                    {"font-weight": "200"},
                    {"letter-spacing": "-0.03em"},
                    {"line-height": "0.95"},
                    {"font-variant-numeric": "tabular-nums"},
                    {
                        "text-shadow": "[[[ return '0 0 60px ' + "
                        + _accent_expr(0.45)
                        + " ]]]"
                    },
                ],
                "meta": [{"font-size": "17px"}, {"color": TEXT_LO}, {"font-weight": "400"}],
                "hilo": [{"font-size": "15px"}, {"color": TEXT_LO},
                         {"font-variant-numeric": "tabular-nums"}],
            },
        },
        "custom_fields": {
            "icon": cond_icon_js,
            "temp": temp_js,
            "meta": meta_js,
            "hilo": hilo_js,
        },
    }


# ------------------------------------------------- cards: metric tiles


def wind_tile() -> dict:
    glyph = f"""[[[
var d = parseFloat(states['{E["wind_dir"]}'] ? states['{E["wind_dir"]}'].state : 'NaN');
if (isNaN(d)) return '';
var c = {_accent_expr(0.9)};
return '<ha-icon icon="mdi:navigation" style="--mdc-icon-size:30px;color:' + c +
  ';transform:rotate(' + ((d + 180) % 360) + 'deg);transition:transform 1s ease"></ha-icon>';
]]]"""
    value = f"""[[[
var card = states['{E["cardinal"]}'] ? states['{E["cardinal"]}'].state : '';
var v = {state_num(E["wind"], 0)};
return card + ' ' + v +
  '<span style="font-size:14px;color:{TEXT_LO};font-weight:400"> mph</span>';
]]]"""
    sub = f"[[[ return 'Gust ' + {state_num(E['gust'], 0)} + ' mph'; ]]]"
    return {
        "type": "custom:button-card",
        "template": "metric_tile",
        "entity": E["wind"],
        "triggers_update": [E["wind"], E["gust"], E["wind_dir"], E["cardinal"], E["temp"]],
        "view_layout": {"grid-area": "wind"},
        "custom_fields": {"eyebrow": "Wind", "value": value, "sub": sub, "glyph": glyph},
    }


def rain_tile() -> dict:
    value = f"""[[[
var today = parseFloat(states['{E["rain_today"]}'] ? states['{E["rain_today"]}'].state : 'NaN');
var shown = isNaN(today) ? '0.00' : today.toFixed(2);
return shown + '<span style="font-size:14px;color:{TEXT_LO};font-weight:400"> in today</span>';
]]]"""
    sub = f"""[[[
var r = parseFloat(states['{E["rain_rate"]}'] ? states['{E["rain_rate"]}'].state : 'NaN');
var total = {state_num(E["rain_total"], 2)};
var rate = isNaN(r) ? '–' : r.toFixed(2);
var rateHtml = (r > 0)
  ? '<span style="color:{RAIN};font-weight:500">' + rate + ' in/h</span>'
  : rate + ' in/h';
return 'Rate ' + rateHtml + '<span style="color:{TEXT_FAINT}"> · </span>Total ' + total + ' in';
]]]"""
    glyph = f"""[[[
var r = parseFloat(states['{E["rain_rate"]}'] ? states['{E["rain_rate"]}'].state : '0');
var c = (r > 0) ? '{RAIN}' : '{TEXT_FAINT}';
return '<ha-icon icon="mdi:weather-pouring" style="--mdc-icon-size:22px;color:' + c + '"></ha-icon>';
]]]"""
    card = {
        "type": "custom:button-card",
        "template": "metric_tile",
        "entity": E["rain_rate"],
        "triggers_update": [E["rain_rate"], E["rain_today"], E["rain_total"], E["temp"]],
        "view_layout": {"grid-area": "rain"},
        "custom_fields": {"eyebrow": "Rain", "value": value, "sub": sub, "glyph": glyph},
        "styles": {
            "card": [
                {
                    "border-top": f"[[[ var r = parseFloat(states['{E['rain_rate']}'] ? "
                    f"states['{E['rain_rate']}'].state : '0'); "
                    f"return '1px solid ' + (r > 0 ? '{RAIN}' : " + _accent_expr(0.55) + "); ]]]"
                }
            ]
        },
    }
    return card


def pressure_tile() -> dict:
    value = unit_span(state_num(E["baro"], 2), "inHg")
    sub = f"""[[[
var trend = states['{E["baro_trend"]}'] ? states['{E["baro_trend"]}'].state : 'unknown';
var d = parseFloat(states['{E["baro_deriv"]}'] ? states['{E["baro_deriv"]}'].state : 'NaN');
var word = trend.charAt(0).toUpperCase() + trend.slice(1);
var rate = isNaN(d) ? '' : '<span style="color:{TEXT_FAINT}"> · </span>' +
  (d >= 0 ? '+' : '') + d.toFixed(3) + ' inHg/h';
return word + rate;
]]]"""
    glyph = f"""[[[
var trend = states['{E["baro_trend"]}'] ? states['{E["baro_trend"]}'].state : 'unknown';
var d = parseFloat(states['{E["baro_deriv"]}'] ? states['{E["baro_deriv"]}'].state : '0');
var icon = trend === 'rising' ? 'mdi:arrow-top-right' :
           trend === 'falling' ? 'mdi:arrow-bottom-right' : 'mdi:arrow-right';
var c = (trend === 'falling' && d < -0.06) ? '{ALERT}' :
        trend === 'rising' ? {_accent_expr(0.9)} : '{TEXT_LO}';
return '<ha-icon icon="' + icon + '" style="--mdc-icon-size:22px;color:' + c + '"></ha-icon>';
]]]"""
    spark = {
        "type": "custom:mini-graph-card",
        "entities": [E["baro"]],
        "hours_to_show": 24,
        "points_per_hour": 2,
        "line_width": 2,
        "height": 42,
        "line_color": "rgba(150,175,225,0.65)",
        "show": {
            "name": False, "icon": False, "state": False, "legend": False,
            "labels": False, "points": False, "fill": "fade",
        },
        "card_mod": {"style": INNER_CLEAR_MOD},
    }
    return {
        "type": "custom:button-card",
        "template": "metric_tile",
        "entity": E["baro"],
        "triggers_update": [E["baro"], E["baro_trend"], E["baro_deriv"], E["temp"]],
        "view_layout": {"grid-area": "press"},
        "custom_fields": {
            "eyebrow": "Pressure", "value": value, "sub": sub, "glyph": glyph,
            "extra": {"card": spark},
        },
    }


def humidity_tile() -> dict:
    value = unit_span(state_num(E["humidity"], 0), "%")
    sub = f"""[[[
var dp = parseFloat(states['{E["dewpoint"]}'] ? states['{E["dewpoint"]}'].state : 'NaN');
if (isNaN(dp)) return 'Dewpoint –';
var word = dp <= 55 ? 'Dry' : dp <= 62 ? 'Comfortable' : dp <= 68 ? 'Sticky' : 'Oppressive';
return 'Dew ' + dp.toFixed(0) + '°<span style="color:{TEXT_FAINT}"> · </span>' + word;
]]]"""
    glyph = (
        f"[[[ return '<ha-icon icon=\"mdi:water-percent\" "
        f"style=\"--mdc-icon-size:22px;color:{TEXT_FAINT}\"></ha-icon>'; ]]]"
    )
    return {
        "type": "custom:button-card",
        "template": "metric_tile",
        "entity": E["humidity"],
        "triggers_update": [E["humidity"], E["dewpoint"], E["temp"]],
        "view_layout": {"grid-area": "humid"},
        "custom_fields": {"eyebrow": "Humidity", "value": value, "sub": sub, "glyph": glyph},
    }


def uv_tile() -> dict:
    value = f"""[[[
var uv = parseFloat(states['{E["uv"]}'] ? states['{E["uv"]}'].state : 'NaN');
if (isNaN(uv)) return '–';
var band = uv < 3 ? 'Low' : uv < 6 ? 'Moderate' : uv < 8 ? 'High' : uv < 11 ? 'Very high' : 'Extreme';
return uv.toFixed(1) + '<span style="font-size:14px;color:{TEXT_LO};font-weight:400"> UV · ' + band + '</span>';
]]]"""
    sub = f"[[[ return {state_num(E['solar'], 0)} + ' W/m² solar'; ]]]"
    # A thin conic ring filling with solar radiation as a fraction of 1000 W/m².
    glyph = f"""[[[
var s = parseFloat(states['{E["solar"]}'] ? states['{E["solar"]}'].state : '0');
var pct = Math.min(100, Math.max(0, s / 10));
var c = {_accent_expr(0.85)};
return '<div style="width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;' +
  'background:conic-gradient(' + c + ' ' + pct + '%, rgba(150,175,225,0.12) ' + pct + '%)">' +
  '<div style="width:26px;height:26px;border-radius:50%;background:#0a111e;display:flex;align-items:center;justify-content:center">' +
  '<ha-icon icon="mdi:white-balance-sunny" style="--mdc-icon-size:14px;color:{TEXT_LO}"></ha-icon></div></div>';
]]]"""
    return {
        "type": "custom:button-card",
        "template": "metric_tile",
        "entity": E["uv"],
        "triggers_update": [E["uv"], E["solar"], "sun.sun", E["temp"]],
        "view_layout": {"grid-area": "uv"},
        "styles": {
            "card": [
                {
                    "opacity": "[[[ var s = states['sun.sun']; "
                    "return (s && s.state === 'below_horizon') ? '0.45' : '1'; ]]]"
                }
            ]
        },
        "custom_fields": {"eyebrow": "UV · Solar", "value": value, "sub": sub, "glyph": glyph},
    }


# ------------------------------------------------- cards: strips, rose, sun, charts


def hourly_card() -> dict:
    return {
        "type": "custom:hourly-weather",
        "entity": E["forecast_hourly"],
        "num_segments": 12,
        "name": " ",
        "view_layout": {"grid-area": "hourly"},
        "label_spacing": 2,
        "card_mod": {
            "style": GLASS_CARD_MOD
            + "ha-card { padding: 10px 16px; } .card-header { display: none; }"
        },
    }


def daily_card() -> dict:
    return {
        "type": "weather-forecast",
        "entity": E["forecast_daily"],
        "forecast_type": "daily",
        "show_current": False,
        "show_forecast": True,
        "view_layout": {"grid-area": "daily"},
        "card_mod": {"style": GLASS_CARD_MOD + "ha-card { padding: 10px 16px; }"},
    }


def windrose_card() -> dict:
    return {
        "type": "custom:windrose-card",
        "view_layout": {"grid-area": "rose"},
        "data_period": {"period_back": "-24h"},
        "wind_direction_entity": {"entity": E["wind_dir"]},
        "windspeed_entities": [
            {"entity": E["wind"], "name": "Speed", "speed_unit": "mph"}
        ],
        "card_mod": {"style": GLASS_CARD_MOD},
    }


def horizon_card() -> dict:
    return {
        "type": "custom:horizon-card",
        "moon": True,
        "view_layout": {"grid-area": "sun"},
        "fields": {"sunrise": True, "sunset": True, "dawn": False, "dusk": False},
        "card_mod": {
            "style": GLASS_CARD_MOD
            + """
ha-card {
  --hc-sun-color: hsl(45 80% 70%);
  --hc-moon-color: rgba(238,242,250,0.75);
  --hc-lines-color: rgba(150,175,225,0.25);
  padding: 8px 14px;
}
"""
        },
    }


def chart_temp() -> dict:
    return {
        "type": "custom:apexcharts-card",
        "view_layout": {"grid-area": "chart_temp"},
        "graph_span": "24h",
        "update_interval": "120s",
        "cache": True,
        "header": {"show": True, "title": "Temperature · 24h", "show_states": False},
        "apex_config": APEX_BASE,
        "series": [
            {
                "entity": E["temp"],
                "name": "Outside",
                "type": "area",
                "stroke_width": 2.5,
                "opacity": 0.18,
                "color_threshold": TEMP_STOPS,
                "group_by": {"func": "avg", "duration": "10min"},
            },
            {
                "entity": E["dewpoint"],
                "name": "Dewpoint",
                "type": "line",
                "stroke_width": 1.5,
                "color": "rgba(238,242,250,0.35)",
                "group_by": {"func": "avg", "duration": "10min"},
            },
            {
                "entity": E["feels"],
                "name": "Feels like",
                "type": "line",
                "stroke_width": 1.5,
                "stroke_dash": 4,
                "color": "rgba(238,242,250,0.55)",
                "group_by": {"func": "avg", "duration": "10min"},
            },
        ],
        "card_mod": {"style": APEX_HEADER_MOD},
    }


def chart_rainwind() -> dict:
    return {
        "type": "custom:apexcharts-card",
        "view_layout": {"grid-area": "chart_rainwind"},
        "graph_span": "24h",
        "update_interval": "120s",
        "cache": True,
        "header": {"show": True, "title": "Rain · Wind · 24h", "show_states": False},
        "apex_config": APEX_BASE,
        "yaxis": [
            {"id": "rain", "min": 0, "decimals": 2},
            {"id": "wind", "opposite": True, "min": 0, "decimals": 0},
        ],
        "series": [
            {
                "entity": E["rain_rate"],
                "name": "Rain rate",
                "type": "area",
                "yaxis_id": "rain",
                "stroke_width": 2,
                "opacity": 0.25,
                "color": RAIN,
                "group_by": {"func": "max", "duration": "10min"},
            },
            {
                "entity": E["gust"],
                "name": "Gust",
                "type": "column",
                "yaxis_id": "wind",
                "color": "rgba(150,175,225,0.40)",
                "group_by": {"func": "max", "duration": "30min"},
            },
            {
                "entity": E["wind"],
                "name": "Wind",
                "type": "line",
                "yaxis_id": "wind",
                "stroke_width": 1.5,
                "color": "rgba(238,242,250,0.45)",
                "group_by": {"func": "avg", "duration": "10min"},
            },
        ],
        "card_mod": {"style": APEX_HEADER_MOD},
    }


def footer_card() -> dict:
    chips_js = f"""[[[
var pk = parseFloat(states['{E["pkts"]}'] ? states['{E["pkts"]}'].state : 'NaN');
var mi = parseFloat(states['{E["missed"]}'] ? states['{E["missed"]}'].state : 'NaN');
var fh = states['{E["fhss"]}'] ? states['{E["fhss"]}'].state : 'unknown';
var lp = states['{E["last_packet"]}'] ? states['{E["last_packet"]}'].state : null;
var age = lp ? Math.max(0, (Date.now() - new Date(lp).getTime()) / 1000) : NaN;
var chip = function(c, icon, text) {{
  return '<span style="display:inline-flex;align-items:center;gap:6px;margin-right:26px;' +
    'font-size:12.5px;font-variant-numeric:tabular-nums;color:' + c + '">' +
    '<ha-icon icon="' + icon + '" style="--mdc-icon-size:15px;color:' + c + '"></ha-icon>' +
    text + '</span>';
}};
var out = '<span style="font-size:11px;font-weight:500;letter-spacing:0.12em;' +
  'text-transform:uppercase;color:{TEXT_FAINT};margin-right:26px">Station</span>';
out += chip((isNaN(pk) || pk < 15) ? '{ALERT}' : '{TEXT_LO}', 'mdi:radio-tower',
  (isNaN(pk) ? '–' : pk.toFixed(1)) + ' pkts/min');
out += chip((isNaN(mi) || mi > 5) ? '{ALERT}' : '{TEXT_LO}', 'mdi:radio-off',
  (isNaN(mi) ? '–' : mi.toFixed(1)) + '% missed');
out += chip((fh !== 'on') ? '{ALERT}' : '{TEXT_LO}', 'mdi:radar',
  (fh === 'on') ? 'FHSS locked' : 'FHSS unlocked');
var bat = states['{E["battery"]}'] ? states['{E["battery"]}'].state : 'unknown';
out += chip((bat === 'off') ? '{TEXT_LO}' : '{ALERT}',
  (bat === 'on') ? 'mdi:battery-alert' : 'mdi:battery',
  (bat === 'off') ? 'Battery OK' : (bat === 'on') ? 'Battery low' : 'Battery –');
out += chip((isNaN(age) || age > 300) ? '{ALERT}' : '{TEXT_LO}', 'mdi:clock-outline',
  isNaN(age) ? 'no packets' :
  (age < 90 ? age.toFixed(0) + ' s ago' : (age / 60).toFixed(0) + ' min ago'));
return out;
]]]"""
    return {
        "type": "custom:button-card",
        "template": "glass",
        "entity": E["pkts"],
        "triggers_update": [E["pkts"], E["missed"], E["fhss"], E["battery"], E["last_packet"]],
        "view_layout": {"grid-area": "foot"},
        "styles": {
            "card": [{"padding": "12px 18px"}, {"min-height": "0"}],
            "grid": [{"grid-template-areas": '"chips"'}],
            "custom_fields": {"chips": [{"justify-self": "start"}, {"text-align": "left"}]},
        },
        "custom_fields": {"chips": chips_js},
    }


# ------------------------------------------------- trends view charts


def trend_chart(title: str, series: list[dict], yaxis: list[dict] | None = None) -> dict:
    card = {
        "type": "custom:apexcharts-card",
        "graph_span": "7d",
        "update_interval": "300s",
        "cache": True,
        "header": {"show": True, "title": title, "show_states": False},
        "apex_config": APEX_BASE,
        "series": series,
        "card_mod": {"style": APEX_HEADER_MOD},
    }
    if yaxis:
        card["yaxis"] = yaxis
    return card


def trends_cards() -> list[dict]:
    return [
        trend_chart(
            "Temperature · 7 days",
            [
                {"entity": E["temp"], "name": "Mean", "type": "line", "stroke_width": 2.5,
                 "color_threshold": TEMP_STOPS,
                 "statistics": {"type": "mean", "period": "hour"}},
                {"entity": E["temp"], "name": "Max", "type": "line", "stroke_width": 1,
                 "color": "rgba(238,242,250,0.25)",
                 "statistics": {"type": "max", "period": "hour"}},
                {"entity": E["temp"], "name": "Min", "type": "line", "stroke_width": 1,
                 "color": "rgba(238,242,250,0.25)",
                 "statistics": {"type": "min", "period": "hour"}},
            ],
        ),
        trend_chart(
            "Rain · 7 days",
            [
                {"entity": E["rain_today"], "name": "Daily rain", "type": "column",
                 "color": RAIN, "statistics": {"type": "max", "period": "day"}},
            ],
            yaxis=[{"min": 0, "decimals": 2}],
        ),
        trend_chart(
            "Pressure · 7 days",
            [
                {"entity": E["baro"], "name": "Barometer", "type": "line", "stroke_width": 2,
                 "color": "rgba(150,175,225,0.85)",
                 "statistics": {"type": "mean", "period": "hour"}},
            ],
        ),
        trend_chart(
            "Wind · 7 days",
            [
                {"entity": E["gust"], "name": "Max gust", "type": "column",
                 "color": "rgba(150,175,225,0.35)",
                 "statistics": {"type": "max", "period": "hour"}},
                {"entity": E["wind"], "name": "Mean wind", "type": "line", "stroke_width": 2,
                 "color": "rgba(238,242,250,0.65)",
                 "statistics": {"type": "mean", "period": "hour"}},
            ],
            yaxis=[{"min": 0, "decimals": 0}],
        ),
    ]


# ------------------------------------------------- layout + assembly

GRID_DESKTOP = {
    "grid-template-columns": "repeat(6, 1fr)",
    "grid-template-rows": "auto auto auto auto auto",
    "grid-template-areas": (
        '"hero hero hourly hourly hourly hourly" '
        '"hero hero daily daily daily daily" '
        '"wind rain press humid uv sun" '
        '"rose rose chart_temp chart_temp chart_rainwind chart_rainwind" '
        '"foot foot foot foot foot foot"'
    ),
    "grid-gap": "14px",
    "margin": "18px",
}

GRID_TABLET = {
    "grid-template-columns": "repeat(3, 1fr)",
    "grid-template-areas": (
        '"hero hero hero" '
        '"hourly hourly hourly" '
        '"daily daily daily" '
        '"wind rain press" '
        '"humid uv sun" '
        '"rose chart_temp chart_temp" '
        '"rose chart_rainwind chart_rainwind" '
        '"foot foot foot"'
    ),
    "grid-gap": "12px",
    "margin": "14px",
}

GRID_PHONE = {
    "grid-template-columns": "1fr 1fr",
    "grid-template-areas": (
        '"hero hero" '
        '"hourly hourly" '
        '"daily daily" '
        '"wind rain" '
        '"press humid" '
        '"uv sun" '
        '"rose rose" '
        '"chart_temp chart_temp" '
        '"chart_rainwind chart_rainwind" '
        '"foot foot"'
    ),
    "grid-gap": "10px",
    "margin": "10px",
}


def build() -> dict:
    now_view = {
        "title": "Now",
        "path": "now",
        "icon": "mdi:weather-partly-cloudy",
        "type": "custom:grid-layout",
        "background": BG_VIEW,
        "layout": {
            **GRID_DESKTOP,
            "mediaquery": {
                "(max-width: 640px)": GRID_PHONE,
                "(max-width: 1099px)": GRID_TABLET,
            },
        },
        "cards": [
            hero_card(),
            hourly_card(),
            daily_card(),
            wind_tile(),
            rain_tile(),
            pressure_tile(),
            humidity_tile(),
            uv_tile(),
            windrose_card(),
            horizon_card(),
            chart_temp(),
            chart_rainwind(),
            footer_card(),
        ],
    }

    trends_view = {
        "title": "Trends",
        "path": "trends",
        "icon": "mdi:chart-line",
        "type": "custom:grid-layout",
        "background": BG_VIEW,
        "layout": {
            "grid-template-columns": "1fr 1fr",
            "grid-gap": "14px",
            "margin": "18px",
            "mediaquery": {
                "(max-width: 900px)": {"grid-template-columns": "1fr", "margin": "10px"},
            },
        },
        "cards": trends_cards(),
    }

    return {
        "button_card_templates": {
            "glass": _glass_template(),
            "metric_tile": _metric_tile_template(),
        },
        # Kiosk: off by default; wall tablet opts in with /weather/now?wp_enabled=true
        "wallpanel": {
            "enabled": False,
            "fullscreen": True,
            "hide_toolbar": True,
            "hide_sidebar": True,
            "keep_screen_on": True,
            "idle_time": 0,
        },
        "views": [now_view, trends_view],
    }


def main() -> None:
    out = Path(__file__).parent / "dashboard.json"
    out.write_text(json.dumps(build(), indent=1, ensure_ascii=False))
    print(f"wrote {out} ({out.stat().st_size:,} bytes, "
          f"blur={'on' if BLUR_ENABLED else 'off'})")


if __name__ == "__main__":
    main()
