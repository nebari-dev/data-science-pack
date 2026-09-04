"""Tests for scripts/preview/tunnel.py's cloudflared runner + extend-preview loop."""

from __future__ import annotations

from scripts.preview import tunnel

REPO = "nebari-dev/data-science-pack"
TOKEN = "gh-token"
URL = "https://pr-205-data-science-pack.openteams.app"
KC_URL = "https://keycloak-pr-205-data-science-pack.openteams.app"
DEPLOYED_AT = "2026-09-03 11:05 UTC"
DEPLOYED_AT_ISO = "2026-09-03T11:05:55Z"


def _run_kwargs(**overrides):
    kwargs = {
        "cloudflared_path": "/tmp/cloudflared", "tunnel_token": "tok", "repo": REPO, "pr_number": 205,
        "github_token": TOKEN, "url": URL, "keycloak_url": KC_URL,
        "deployed_at": DEPLOYED_AT, "deployed_at_iso": DEPLOYED_AT_ISO,
        "wall_clock": lambda: 0.0,
        "list_labels_fn": lambda *a: [], "delete_label_fn": lambda *a: None,
        "find_comment_id_fn": lambda *a: None, "update_comment_fn": lambda *a: None,
    }
    kwargs.update(overrides)
    return kwargs


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


# --- format_deadline (pure) ----------------------------------------------------


def test_format_deadline_formats_human_and_iso_strings():
    # 2026-09-03T15:01:16Z as a Unix timestamp.
    import calendar
    import datetime

    epoch = calendar.timegm(datetime.datetime(2026, 9, 3, 15, 1, 16, tzinfo=datetime.timezone.utc).timetuple())

    human, iso = tunnel.format_deadline(epoch)

    assert human == "2026-09-03 15:01 UTC"
    assert iso == "2026-09-03T15:01:16Z"


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

    rc = tunnel.run(**_run_kwargs(
        initial_seconds=1200, poll_seconds=15,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
    ))

    assert rc == 7
    assert proc.terminated is False


def test_run_terminates_process_and_returns_0_at_deadline():
    proc = _FakeProcess()  # never exits on its own
    clock, sleep = _fake_clock_sleep()

    rc = tunnel.run(**_run_kwargs(
        initial_seconds=30, poll_seconds=15,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
    ))

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

    rc = tunnel.run(**_run_kwargs(
        initial_seconds=30, poll_seconds=15, extend_seconds=30,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=list_labels_fn, delete_label_fn=delete_label_fn,
    ))

    assert rc == 0
    assert len(delete_calls) == 1
    assert delete_calls[0] == (REPO, 205, "extend-preview", TOKEN)
    # Stopped at t=45 (the extended deadline), not t=30 (the original one).
    assert clock() == 45.0


def test_run_updates_the_pr_comment_with_the_new_expiry_on_extend():
    # Root cause of the bug this covers: the PR comment's "Expires" text
    # is only ever rendered once, at deploy time -- extending the tunnel's
    # internal deadline did nothing to it. On a successful extend, run()
    # must now re-render the ready comment with the NEW expiry and PATCH
    # it directly (the sticky-comment action only runs at fixed workflow
    # steps, not from inside this loop).
    proc = _FakeProcess()
    clock, sleep = _fake_clock_sleep()
    label_calls = []
    find_calls = []
    update_calls = []

    def list_labels_fn(*a):
        label_calls.append(1)
        return ["extend-preview"] if len(label_calls) == 1 else []

    def find_comment_id_fn(*a):
        find_calls.append(a)
        return 999

    def update_comment_fn(*a):
        update_calls.append(a)

    tunnel.run(**_run_kwargs(
        initial_seconds=30, poll_seconds=15, extend_seconds=30,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=list_labels_fn, delete_label_fn=lambda *a: None,
        find_comment_id_fn=find_comment_id_fn, update_comment_fn=update_comment_fn,
    ))

    assert len(find_calls) == 1
    assert find_calls[0][:3] == (REPO, 205, tunnel.STICKY_MARKER)
    assert len(update_calls) == 1
    repo, comment_id, body, token = update_calls[0]
    assert repo == REPO
    assert comment_id == 999
    assert token == TOKEN
    assert "🟢 [Ready]" in body
    assert URL in body
    assert KC_URL in body
    # New expiry (t=0 + 30s = 1970-01-01 00:00:30 UTC), not the original.
    assert "1970-01-01T00:00:30Z" in body
    assert body.endswith(tunnel.STICKY_MARKER)


def test_run_computes_comment_expiry_from_wall_clock_not_monotonic_clock():
    # Regression test for a real bug found live: format_deadline() must
    # never be fed the monotonic `clock`'s value directly. time.monotonic()
    # has no relationship to the real epoch (often just seconds since
    # process/boot start) -- interpreting it as Unix time produced
    # "1970-01-01" in the actual PR comment. The extended deadline must be
    # computed as wall_clock() + the remaining duration (deadline - the
    # monotonic now), never the monotonic deadline interpreted as epoch
    # time and never wall_clock() alone (ignoring how much time is left).
    proc = _FakeProcess()
    # Both clocks advance in lockstep on sleep() (as real time.monotonic()
    # and time.time() would), just starting from very different epochs --
    # that mismatch is exactly what the fix must account for.
    state = {"mono": 1913.0, "wall": 1893456000.0}

    def sleep(seconds):
        state["mono"] += seconds
        state["wall"] += seconds

    label_calls = []
    update_calls = []

    def list_labels_fn(*a):
        label_calls.append(1)
        return ["extend-preview"] if len(label_calls) == 1 else []

    tunnel.run(**_run_kwargs(
        initial_seconds=30, poll_seconds=15, extend_seconds=30,
        popen=lambda *a, **k: proc,
        clock=lambda: state["mono"], wall_clock=lambda: state["wall"], sleep=sleep,
        list_labels_fn=list_labels_fn, delete_label_fn=lambda *a: None,
        find_comment_id_fn=lambda *a: 999,
        update_comment_fn=lambda *a: update_calls.append(a),
    ))

    assert len(update_calls) == 1
    body = update_calls[0][2]
    # Extend at mono now=1913 -> new deadline=1943 -> 30s remaining.
    # Wall-clock expiry = wall_now(1893456000) + 30 = 1893456030.
    expected_iso = tunnel.format_deadline(1893456030)[1]
    assert expected_iso in body
    assert "1970-01-01" not in body


def test_run_skips_comment_update_when_comment_not_found():
    proc = _FakeProcess()
    clock, sleep = _fake_clock_sleep()
    label_calls = []
    update_calls = []

    def list_labels_fn(*a):
        label_calls.append(1)
        return ["extend-preview"] if len(label_calls) == 1 else []

    tunnel.run(**_run_kwargs(
        initial_seconds=30, poll_seconds=15, extend_seconds=30,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=list_labels_fn, delete_label_fn=lambda *a: None,
        find_comment_id_fn=lambda *a: None,
        update_comment_fn=lambda *a: update_calls.append(a),
    ))

    assert update_calls == []


def test_run_survives_a_comment_update_failure():
    # A GitHub API hiccup while updating the comment must never take down
    # the tunnel itself -- the preview staying up matters more than the
    # comment being perfectly accurate.
    proc = _FakeProcess()
    clock, sleep = _fake_clock_sleep()
    label_calls = []

    def list_labels_fn(*a):
        label_calls.append(1)
        return ["extend-preview"] if len(label_calls) == 1 else []

    def boom(*a):
        raise RuntimeError("GitHub API is down")

    rc = tunnel.run(**_run_kwargs(
        initial_seconds=30, poll_seconds=15, extend_seconds=30,
        popen=lambda *a, **k: proc, clock=clock, sleep=sleep,
        list_labels_fn=list_labels_fn, delete_label_fn=lambda *a: None,
        find_comment_id_fn=boom,
    ))

    assert rc == 0  # the tunnel still ran to completion despite the failure
