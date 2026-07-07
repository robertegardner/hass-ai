"""Pure context computations: day_type and season.

Travel-day support: TravelCalendar is the interface a future ICS-backed
implementation plugs into (operator request). Until then NullTravelCalendar
keeps day_type to weekday/weekend/holiday.
"""

from datetime import date
from typing import Protocol

import holidays

_US_HOLIDAYS = holidays.US()

WEEKDAY = "weekday"
WEEKEND = "weekend"
HOLIDAY = "holiday"
TRAVEL = "travel"


class TravelCalendar(Protocol):
    def is_travel_day(self, day: date) -> bool: ...


class NullTravelCalendar:
    def is_travel_day(self, day: date) -> bool:
        return False


def day_type(day: date, travel: TravelCalendar | None = None) -> str:
    if travel is not None and travel.is_travel_day(day):
        return TRAVEL
    if day in _US_HOLIDAYS:
        return HOLIDAY
    if day.weekday() >= 5:
        return WEEKEND
    return WEEKDAY


def season(day: date) -> str:
    # meteorological seasons
    if day.month in (12, 1, 2):
        return "winter"
    if day.month in (3, 4, 5):
        return "spring"
    if day.month in (6, 7, 8):
        return "summer"
    return "fall"
