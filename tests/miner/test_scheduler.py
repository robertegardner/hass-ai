from datetime import UTC, datetime

import pytest

from pae.worker.scheduler import seconds_until_next


def test_target_later_today():
    now = datetime(2026, 7, 12, 5, 0, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(4 * 3600)


def test_target_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 7, 12, 10, 30, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(22.5 * 3600)


def test_exactly_at_target_rolls_to_tomorrow():
    now = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(24 * 3600)
