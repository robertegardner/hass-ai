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


def test_nightly_chain_enqueues_three_dependent_jobs():
    from pae.worker.scheduler import enqueue_nightly_chain

    calls = []

    class FakeQueue:
        def enqueue(self, fn, job_id=None, depends_on=None, job_timeout=None):
            calls.append((fn.__name__, job_id, depends_on, job_timeout))
            return type("J", (), {"id": job_id})()

    enqueue_nightly_chain(FakeQueue(), datetime(2026, 7, 28, 9, 0, tzinfo=UTC))
    names = [c[0] for c in calls]
    assert names == ["mine_patterns_job", "propose_job", "shadow_eval_job"]
    assert calls[0][1] == "mine-20260728"
    assert calls[1][2].id == "mine-20260728"      # propose depends on mine
    assert calls[2][2].id == "propose-20260728"   # shadow depends on propose
    # explicit job_timeout on every enqueue: rq's Queue.DEFAULT_TIMEOUT (180s)
    # would otherwise kill run_proposing mid-transaction (multiple LLM calls
    # at up to 120s each can legitimately exceed 180s) and skip the dependent
    # shadow job.
    assert calls[0][3] == 900   # mine
    assert calls[1][3] == 3600  # propose
    assert calls[2][3] == 900   # shadow
