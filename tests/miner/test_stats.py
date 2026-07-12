from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from pae.miner.stats import (
    circular_diff,
    circular_mean,
    circular_std,
    cluster_minutes,
    minutes_of_day,
)

TZ = ZoneInfo("America/Chicago")


def test_minutes_of_day_converts_to_local_tz():
    # 03:12 UTC on a July date is 22:12 CDT the previous evening
    dt = datetime(2026, 7, 12, 3, 12, tzinfo=UTC)
    assert minutes_of_day(dt, TZ) == pytest.approx(22 * 60 + 12)


def test_circular_mean_simple():
    assert circular_mean([10.0, 20.0, 30.0]) == pytest.approx(20.0)


def test_circular_mean_wraps_midnight():
    mean = circular_mean([1430.0, 10.0])
    assert min(mean, 1440.0 - mean) == pytest.approx(0.0, abs=0.01)


def test_circular_diff_wraps():
    assert circular_diff(10.0, 1430.0) == pytest.approx(20.0)
    assert circular_diff(1430.0, 10.0) == pytest.approx(-20.0)


def test_circular_std_identical_is_zero():
    assert circular_std([100.0, 100.0, 100.0]) == pytest.approx(0.0)


def test_circular_std_opposite_is_capped():
    assert circular_std([0.0, 720.0]) == pytest.approx(360.0)


def test_cluster_minutes_splits_on_gap():
    clusters = cluster_minutes([60.0, 70.0, 80.0, 600.0, 610.0], gap=90.0)
    assert sorted(sorted(c) for c in clusters) == [[0, 1, 2], [3, 4]]


def test_cluster_minutes_merges_across_midnight():
    clusters = cluster_minutes([5.0, 1435.0, 700.0], gap=90.0)
    assert sorted(sorted(c) for c in clusters) == [[0, 1], [2]]


def test_cluster_minutes_empty():
    assert cluster_minutes([]) == []
