"""Tiny in-process daily scheduler: enqueue the nightly mining job.

Deliberately not rq-scheduler: one daemon thread sleeps until the next
HH:00 UTC and enqueues. The per-date job_id plus the miner's idempotent
upsert make an occasional double-enqueue (e.g. two workers during a deploy
overlap) harmless.
"""
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from rq import Queue

from pae.logging import get_logger

log = get_logger(__name__)


def seconds_until_next(hour_utc: int, now: datetime) -> float:
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_daily(
    enqueue: Callable[[datetime], None],
    hour_utc: int,
    stop: threading.Event,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    while not stop.is_set():
        delay = seconds_until_next(hour_utc, now_fn())
        if stop.wait(timeout=delay):
            return
        try:
            enqueue(now_fn())
        except Exception:
            log.exception("mine_enqueue_failed")


def start_daily_scheduler(queue: Queue, hour_utc: int) -> threading.Event:
    from pae.miner.job import mine_patterns_job

    stop = threading.Event()

    def enqueue(now: datetime) -> None:
        job = queue.enqueue(mine_patterns_job, job_id=f"mine-{now:%Y%m%d}")
        log.info("mine_job_enqueued", job_id=job.id)

    thread = threading.Thread(
        target=run_daily, args=(enqueue, hour_utc, stop), daemon=True, name="pae-mine-scheduler"
    )
    thread.start()
    log.info("mine_scheduler_started", hour_utc=hour_utc)
    return stop
