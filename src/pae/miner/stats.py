"""Circular time-of-day statistics for the pattern miner.

Times of day live on a 1440-minute circle: 23:55 and 00:05 are ten minutes
apart. All functions take minutes-of-day floats in [0, 1440).
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

DAY_MINUTES = 1440.0


def minutes_of_day(dt: datetime, tz: ZoneInfo) -> float:
    local = dt.astimezone(tz)
    return local.hour * 60 + local.minute + local.second / 60


def circular_diff(a: float, b: float) -> float:
    """Signed minimal distance a-b on the day circle, in (-720, 720]."""
    d = (a - b) % DAY_MINUTES
    return d - DAY_MINUTES if d > DAY_MINUTES / 2 else d


def circular_mean(minutes: list[float]) -> float:
    angles = [m / DAY_MINUTES * 2 * math.pi for m in minutes]
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    return (math.atan2(s, c) / (2 * math.pi) * DAY_MINUTES) % DAY_MINUTES


def circular_std(minutes: list[float]) -> float:
    """Circular standard deviation in minutes; capped at 360 when the times
    show no concentration at all (uniform/opposite)."""
    if len(minutes) < 2:
        return 0.0
    angles = [m / DAY_MINUTES * 2 * math.pi for m in minutes]
    s = sum(math.sin(a) for a in angles) / len(angles)
    c = sum(math.cos(a) for a in angles) / len(angles)
    r = math.hypot(s, c)
    if r < 1e-9:
        return 360.0
    if r >= 1.0:
        return 0.0
    std = math.sqrt(-2.0 * math.log(r)) / (2 * math.pi) * DAY_MINUTES
    return min(std, 360.0)


def cluster_minutes(minutes: list[float], gap: float = 90.0) -> list[list[int]]:
    """Group indexes of ``minutes`` into time-of-day clusters, splitting where
    consecutive sorted times are more than ``gap`` apart; the first and last
    cluster merge when the gap across midnight is within ``gap``."""
    if not minutes:
        return []
    order = sorted(range(len(minutes)), key=lambda i: minutes[i])
    clusters: list[list[int]] = [[order[0]]]
    for i in order[1:]:
        if minutes[i] - minutes[clusters[-1][-1]] > gap:
            clusters.append([i])
        else:
            clusters[-1].append(i)
    if len(clusters) > 1 and minutes[order[0]] + DAY_MINUTES - minutes[order[-1]] <= gap:
        clusters[0] = clusters.pop() + clusters[0]
    return clusters
