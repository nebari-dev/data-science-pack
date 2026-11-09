"""`helm dependency update` in the e2e harness retries transient failures.

The command fetches https://hub.jupyter.org/helm-chart/index.yaml. A single
TCP reset on a GitHub runner killed an otherwise-green e2e leg before the
test even ran (PR #226, run 34242091168). One retry loop is cheaper than
re-running a 4-minute matrix leg.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

import _cluster  # noqa: E402


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(_cluster.time, "sleep", slept.append)
    return slept


def _flaky_run(fail_times):
    calls = []

    def run(*args, **kw):
        calls.append(args)
        if len(calls) <= fail_times:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    run.calls = calls
    return run


def test_succeeds_first_try_without_sleeping(monkeypatch, no_sleep):
    run = _flaky_run(fail_times=0)
    monkeypatch.setattr(_cluster, "run", run)
    _cluster.helm_dependency_update()
    assert run.calls == [("helm", "dependency", "update")]
    assert no_sleep == []


def test_retries_after_transient_failure(monkeypatch, no_sleep):
    run = _flaky_run(fail_times=2)
    monkeypatch.setattr(_cluster, "run", run)
    _cluster.helm_dependency_update(attempts=3, delay=7)
    assert len(run.calls) == 3
    assert no_sleep == [7, 7]


def test_raises_after_exhausting_attempts(monkeypatch, no_sleep):
    run = _flaky_run(fail_times=99)
    monkeypatch.setattr(_cluster, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        _cluster.helm_dependency_update(attempts=3, delay=1)
    assert len(run.calls) == 3
    assert no_sleep == [1, 1]


def test_helm_install_uses_retrying_update(monkeypatch, no_sleep):
    seen = []
    monkeypatch.setattr(_cluster, "helm_dependency_update",
                        lambda: seen.append("dep"))
    monkeypatch.setattr(_cluster, "run",
                        lambda *a, **kw: seen.append(a[:3]))
    _cluster.helm_install("rel", ".", Path("values.yaml"))
    assert seen[0] == "dep"
    assert seen[1] == ("helm", "upgrade", "--install")
