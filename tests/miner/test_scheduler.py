import threading
from datetime import UTC, datetime

import pytest

from pae.worker.scheduler import run_daily, seconds_until_next


def test_target_later_today():
    now = datetime(2026, 7, 12, 5, 0, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(4 * 3600)


def test_target_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 7, 12, 10, 30, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(22.5 * 3600)


def test_exactly_at_target_rolls_to_tomorrow():
    now = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
    assert seconds_until_next(9, now) == pytest.approx(24 * 3600)


def test_run_daily_survives_enqueue_failure():
    class FastStop(threading.Event):
        def wait(self, timeout=None):  # don't actually sleep in tests
            return super().wait(timeout=0)

    stop = FastStop()
    calls = []

    times = iter(
        [
            datetime(2026, 7, 12, 8, 59, 59, tzinfo=UTC),
            datetime(2026, 7, 12, 9, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 13, 8, 59, 59, tzinfo=UTC),
            datetime(2026, 7, 13, 9, 0, 0, tzinfo=UTC),
        ]
    )

    def now_fn():
        return next(times)

    def enqueue(now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("redis down")
        stop.set()

    run_daily(enqueue, 9, stop, now_fn)

    assert len(calls) == 2  # survived the first failure and enqueued again
