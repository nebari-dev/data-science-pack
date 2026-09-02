"""Tests for scripts/preview/k8s_wait.py's kubectl readiness retry loops."""

from __future__ import annotations

from scripts.preview import k8s_wait


def _fake_time():
    state = {"t": 0.0}
    return (lambda: state["t"]), (lambda s: state.__setitem__("t", state["t"] + s))


# --- wait_for_secret_key -------------------------------------------------


def test_wait_for_secret_key_returns_true_once_value_nonempty():
    calls = []

    def get_value():
        calls.append(1)
        return "" if len(calls) == 1 else "aGVsbG8="

    clock, sleep = _fake_time()

    ok = k8s_wait.wait_for_secret_key(get_value, timeout_s=300, poll_interval_s=5, clock=clock, sleep=sleep)

    assert ok is True
    assert len(calls) == 2


def test_wait_for_secret_key_returns_false_after_timeout():
    clock, sleep = _fake_time()

    ok = k8s_wait.wait_for_secret_key(lambda: "", timeout_s=10, poll_interval_s=5, clock=clock, sleep=sleep)

    assert ok is False


# --- restart_until_ready ---------------------------------------------------


def test_restart_until_ready_stops_on_first_successful_attempt():
    restart_calls = []
    status_calls = []

    ok, attempts = k8s_wait.restart_until_ready(
        restart=lambda: restart_calls.append(1),
        check_status=lambda: (status_calls.append(1), True)[1],
        max_attempts=5,
    )

    assert ok is True
    assert attempts == 1
    assert len(restart_calls) == 1
    assert len(status_calls) == 1


def test_restart_until_ready_retries_until_success():
    status_results = iter([False, False, True])

    ok, attempts = k8s_wait.restart_until_ready(
        restart=lambda: None,
        check_status=lambda: next(status_results),
        max_attempts=5,
    )

    assert ok is True
    assert attempts == 3


def test_restart_until_ready_gives_up_after_max_attempts():
    ok, attempts = k8s_wait.restart_until_ready(
        restart=lambda: None,
        check_status=lambda: False,
        max_attempts=3,
    )

    assert ok is False
    assert attempts == 3
