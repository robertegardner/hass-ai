from datetime import date

from pae.ingest.context import NullTravelCalendar, day_type, season


def test_day_type_weekday_weekend():
    assert day_type(date(2026, 7, 7)) == "weekday"  # Tuesday
    assert day_type(date(2026, 7, 11)) == "weekend"  # Saturday
    assert day_type(date(2026, 7, 12)) == "weekend"  # Sunday


def test_day_type_us_holiday():
    assert day_type(date(2026, 12, 25)) == "holiday"  # Christmas (Friday)
    assert day_type(date(2026, 1, 1)) == "holiday"  # New Year's Day
    assert day_type(date(2026, 11, 26)) == "holiday"  # Thanksgiving


def test_travel_calendar_interface():
    class AlwaysTravel:
        def is_travel_day(self, day: date) -> bool:
            return True

    assert day_type(date(2026, 7, 7), AlwaysTravel()) == "travel"
    assert day_type(date(2026, 7, 7), NullTravelCalendar()) == "weekday"


def test_season_meteorological():
    assert season(date(2026, 1, 15)) == "winter"
    assert season(date(2026, 12, 1)) == "winter"
    assert season(date(2026, 3, 1)) == "spring"
    assert season(date(2026, 7, 7)) == "summer"
    assert season(date(2026, 9, 1)) == "fall"
    assert season(date(2026, 11, 30)) == "fall"
