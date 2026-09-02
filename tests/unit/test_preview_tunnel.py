"""Tests for scripts/preview/tunnel.py's cloudflared runner + extend-preview loop."""

from __future__ import annotations

from scripts.preview import tunnel

REPO = "nebari-dev/data-science-pack"
TOKEN = "gh-token"


# --- next_deadline / should_stop (pure) ---------------------------------------


def test_next_deadline_resets_to_now_plus_extend_when_label_present():
    # A reset, not a cumulative add: the old deadline is irrelevant once
    # the label is seen.
    assert tunnel.next_deadline(now=100.0, current_deadline=105.0, label_present=True, extend_seconds=1200) == 1300.0


def test_next_deadline_unchanged_when_label_absent():
    assert tunnel.next_deadline(now=100.0, current_deadline=105.0, label_present=False, extend_seconds=1200) == 105.0


def test_should_stop_true_when_process_no_longer_alive():
    assert tunnel.should_stop(alive=False, now=0.0, deadline=1000.0) is True


def test_should_stop_true_when_deadline_reached():
    assert tunnel.should_stop(alive=True, now=1000.0, deadline=1000.0) is True


def test_should_stop_false_while_alive_and_before_deadline():
    assert tunnel.should_stop(alive=True, now=500.0, deadline=1000.0) is False


# --- run (subprocess + label polling, all injected) ---------------------------


class _FakeProcess:
    def __init__(self, exit_after_polls=None, exit_code=0):
        self._polls = 0
        self._exit_after_polls = exit_after_polls
        self.exit_code = exit_code
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        self._polls += 1
        if self._exit_after_polls is not None and self._polls >= self._exit_after_polls:
            self.returncode = self.exit_code
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _fake_clock_sleep():
    state = {"t": 0.0}
    return (lambda: state["t"]), (lambda s: state.__setitem__("t", state["t"] + s))


def test_run_returns_cloudflared_exit_code_on_real_crash():
    proc = _FakeProcess(exit_after_polls=2, exit_code=7)
    clock, sleep = _fake_clock_sleep()

    rc = tunnel.run(
        cloudflared_path="/tmp/cloudflared", tunnel_token="tok", repo=REPO, pr_number=205,
        github_token=TOKEN, initial_seconds=1200, poll_seconds=15,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=lambda *a: [], delete_label_fn=lambda *a: None,
    )

    assert rc == 7
    assert proc.terminated is False


def test_run_terminates_process_and_returns_0_at_deadline():
    proc = _FakeProcess()  # never exits on its own
    clock, sleep = _fake_clock_sleep()

    rc = tunnel.run(
        cloudflared_path="/tmp/cloudflared", tunnel_token="tok", repo=REPO, pr_number=205,
        github_token=TOKEN, initial_seconds=30, poll_seconds=15,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=lambda *a: [], delete_label_fn=lambda *a: None,
    )

    assert rc == 0
    assert proc.terminated is True


def test_run_extends_deadline_when_label_seen_and_deletes_it():
    # initial_seconds=30, poll_seconds=15: checks happen at t=0 (absent),
    # t=15 (present -> deadline resets to 15+30=45, clearly past the
    # original 30s deadline), t=30 (absent, 30 < 45 so it keeps going),
    # t=45 (stop: 45 >= 45). If the extend hadn't taken effect, the loop
    # would have stopped at t=30 instead.
    proc = _FakeProcess()
    clock, sleep = _fake_clock_sleep()
    label_calls = []
    delete_calls = []

    def list_labels_fn(*a):
        label_calls.append(1)
        return ["extend-preview"] if len(label_calls) == 2 else []

    def delete_label_fn(*a):
        delete_calls.append(a)

    rc = tunnel.run(
        cloudflared_path="/tmp/cloudflared", tunnel_token="tok", repo=REPO, pr_number=205,
        github_token=TOKEN, initial_seconds=30, poll_seconds=15, extend_seconds=30,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=list_labels_fn, delete_label_fn=delete_label_fn,
    )

    assert rc == 0
    assert len(delete_calls) == 1
    assert delete_calls[0] == (REPO, 205, "extend-preview", TOKEN)
    # Stopped at t=45 (the extended deadline), not t=30 (the original one).
    assert clock() == 45.0
