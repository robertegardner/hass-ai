"""Per-date sunrise/sunset minutes-of-day via astral, cached per date.

Anchors go through the same ``minutes_of_day`` conversion as mined events so
both sides of a sun-offset comparison share one tz path (DST-safe).
"""
from datetime import date
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import sun

from pae.miner.stats import minutes_of_day


class SunCalculator:
    def __init__(self, latitude: float, longitude: float, tz: ZoneInfo) -> None:
        self._observer = Observer(latitude=latitude, longitude=longitude)
        self._tz = tz
        self._cache: dict[date, tuple[float, float] | None] = {}

    def sun_minutes(self, d: date) -> tuple[float, float] | None:
        """(sunrise, sunset) minutes-of-day in local tz, or None when the sun
        never rises/sets on that date (polar latitudes)."""
        if d not in self._cache:
            try:
                s = sun(self._observer, date=d, tzinfo=self._tz)
                self._cache[d] = (
                    minutes_of_day(s["sunrise"], self._tz),
                    minutes_of_day(s["sunset"], self._tz),
                )
            except ValueError:
                self._cache[d] = None
        return self._cache[d]
