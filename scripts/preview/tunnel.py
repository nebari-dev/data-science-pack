"""Runs cloudflared and lets an extend-preview label push its deadline back.

Bounded to `initial_seconds` by default so the live preview doesn't sit
open (and burn CI minutes) indefinitely. Polls for the `extend_label` on
the PR between checks; each occurrence resets the deadline to
now + extend_seconds (not a cumulative add onto whatever's left) and is
consumed by removing the label, so it can be reused any number of times
before expiry. Still ultimately bounded by the calling job's own
timeout-minutes regardless of how many times it's extended.

Usage:
    python -m scripts.preview.tunnel run --cloudflared PATH --token TOKEN \\
        --repo OWNER/REPO --pr N --github-token TOKEN \\
        [--initial-seconds 1200] [--poll-seconds 15] [--extend-seconds 1200] \\
        [--extend-label extend-preview]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable

from .github_api import delete_label, list_labels


def next_deadline(now: float, current_deadline: float, label_present: bool, extend_seconds: int) -> float:
    """What should the deadline be this tick?

    A reset to now + extend_seconds when the label is present, not a
    cumulative add onto whatever's left, so an extend always means
    "extend_seconds more from right now."
    """
    return now + extend_seconds if label_present else current_deadline


def should_stop(alive: bool, now: float, deadline: float) -> bool:
    return (not alive) or now >= deadline


def run(
    cloudflared_path: str,
    tunnel_token: str,
    repo: str,
    pr_number: int,
    github_token: str,
    initial_seconds: int = 1200,
    poll_seconds: int = 15,
    extend_seconds: int = 1200,
    extend_label: str = "extend-preview",
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    list_labels_fn: Callable[[str, int, str], list[str]] = list_labels,
    delete_label_fn: Callable[[str, int, str, str], None] = delete_label,
) -> int:
    """Run cloudflared until its deadline, or until it exits on its own.

    Returns cloudflared's real exit code if it exited on its own (a
    genuine crash), or 0 if we closed it ourselves (deadline reached).
    """
    proc = popen([cloudflared_path, "tunnel", "--no-autoupdate", "run", "--token", tunnel_token])
    deadline = clock() + initial_seconds

    while True:
        alive = proc.poll() is None
        now = clock()
        if should_stop(alive, now, deadline):
            if not alive:
                return proc.returncode
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return 0

        if extend_label in list_labels_fn(repo, pr_number, github_token):
            deadline = next_deadline(now, deadline, True, extend_seconds)
            delete_label_fn(repo, pr_number, extend_label, github_token)
            print(f"{extend_label} seen -- new deadline: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(deadline))}")

        sleep(poll_seconds)


def _cmd_run(args: argparse.Namespace) -> int:
    return run(
        args.cloudflared, args.token, args.repo, args.pr, args.github_token,
        initial_seconds=args.initial_seconds, poll_seconds=args.poll_seconds,
        extend_seconds=args.extend_seconds, extend_label=args.extend_label,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tunnel")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("--cloudflared", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--github-token", required=True)
    p.add_argument("--initial-seconds", type=int, default=1200)
    p.add_argument("--poll-seconds", type=int, default=15)
    p.add_argument("--extend-seconds", type=int, default=1200)
    p.add_argument("--extend-label", default="extend-preview")
    p.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
