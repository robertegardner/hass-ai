from datetime import date
from zoneinfo import ZoneInfo

from pae.miner.sun import SunCalculator

TZ = ZoneInfo("America/Chicago")
CHICAGO = SunCalculator(41.85, -87.65, TZ)


def test_chicago_july_sun_minutes():
    result = CHICAGO.sun_minutes(date(2026, 7, 6))
    assert result is not None
    sunrise, sunset = result
    assert 300 < sunrise < 360  # ~05:20 CDT
    assert 1200 < sunset < 1260  # ~20:29 CDT


def test_dst_fallback_day_sunset_drops_an_hour():
    before = CHICAGO.sun_minutes(date(2026, 10, 31))
    after = CHICAGO.sun_minutes(date(2026, 11, 1))
    assert before is not None and after is not None
    assert 50 < before[1] - after[1] < 70


def test_polar_summer_returns_none():
    svalbard = SunCalculator(78.0, 15.6, ZoneInfo("Arctic/Longyearbyen"))
    assert svalbard.sun_minutes(date(2026, 6, 21)) is None


def test_cached_result_is_stable():
    d = date(2026, 7, 6)
    assert CHICAGO.sun_minutes(d) == CHICAGO.sun_minutes(d)
    assert d in CHICAGO._cache
