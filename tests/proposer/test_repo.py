from datetime import date

from pae.proposer.repo import _SET_STATUS_FROM_STATUSES, _ready_fetch_limit, ready


def test_ready_fetch_limit_covers_configured_min_days():
    # Below the 14-row rolling window: still fetch 14, the window ready() checks.
    assert _ready_fetch_limit(10) == 14
    # Above 14: widen the fetch so ready()'s len(history) >= min_days check is
    # actually satisfiable — this is what list_proposals uses instead of the
    # old hardcoded-14 fetch.
    assert _ready_fetch_limit(20) == 20


def test_ready_true_with_full_history_when_min_days_above_14():
    # 20 qualifying days at shadow_ready_days=20: same path list_proposals now
    # takes via _ready_fetch_limit(settings.shadow_ready_days).
    history = [
        {"day": date(2026, 7, d), "expected_fires": 1, "human_matches": 1, "human_total": 1}
        for d in range(1, 21)
    ]
    assert len(history) == 20
    assert ready(history, min_days=20, min_precision=0.8, min_coverage=0.8) is True


def test_ready_false_with_only_14_rows_when_min_days_is_20():
    # Regression for the bug: fetching only 14 rows regardless of
    # shadow_ready_days=20 means len(history) == 14 < min_days, so ready()
    # could never return True no matter how good precision/coverage were.
    history = [
        {"day": date(2026, 7, d), "expected_fires": 1, "human_matches": 1, "human_total": 1}
        for d in range(1, 15)
    ]
    assert len(history) == 14
    assert ready(history, min_days=20, min_precision=0.8, min_coverage=0.8) is False


def test_set_status_from_guard_excludes_terminal_statuses():
    # set_status's UPDATE ... WHERE status IN (...) guard: approve/reject are
    # only legal from "shadowing" or "stale" — a row already "approved" or
    # "rejected" must not be flippable through this endpoint (in particular,
    # approved -> rejected is not a legal transition in Phase 3). This is the
    # one piece of that guard with a pure seam to unit-test directly; the
    # rest (rowcount==0 -> False, no pattern flip on a miss) is a real-DB
    # integration path with no test DB fixture in this repo, per the existing
    # house pattern for pae.proposer.repo / pae.shadow.service.
    assert _SET_STATUS_FROM_STATUSES == ("shadowing", "stale")
    assert "approved" not in _SET_STATUS_FROM_STATUSES
    assert "rejected" not in _SET_STATUS_FROM_STATUSES
