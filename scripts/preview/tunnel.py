"""Runs cloudflared and lets an extend-preview label push its deadline back.

Bounded to `initial_seconds` by default so the live preview doesn't sit
open (and burn CI minutes) indefinitely. Polls for the `extend_label` on
the PR between checks; each occurrence resets the deadline to
now + extend_seconds (not a cumulative add onto whatever's left) and is
consumed by removing the label, so it can be reused any number of times
before expiry. Still ultimately bounded by the calling job's own
timeout-minutes regardless of how many times it's extended.

The PR comment's "Expires" text is otherwise only ever rendered once, at
deploy time (see comment.py + the "Comment preview link on PR" workflow
step) -- extending the tunnel's internal deadline alone does nothing to
it, confirmed live: the comment kept showing the original 20-minute mark
after two real extends. On every successful extend, this module now
re-renders the ready comment with the new expiry and PATCHes it directly
via the GitHub API (the sticky-comment action only runs at fixed workflow
steps, not from inside this loop, so it can't be reused here). That side
effect is best-effort: any failure updating the comment is logged and
swallowed, never allowed to take down the tunnel itself.

Usage:
    python -m scripts.preview.tunnel run --cloudflared PATH --token TOKEN \\
        --repo OWNER/REPO --pr N --github-token TOKEN \\
        --url URL --keycloak-url URL \\
        --deployed-at STR --deployed-at-iso ISO [--fork] \\
        [--initial-seconds 1200] [--poll-seconds 15] [--extend-seconds 1200] \\
        [--extend-label extend-preview]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable

from .comment import render_ready
from .github_api import delete_label, find_comment_id, list_labels, update_comment

STICKY_MARKER = "<!-- Sticky Pull Request Commentk8s-preview -->"


def next_deadline(now: float, current_deadline: float, label_present: bool, extend_seconds: int) -> float:
    """What should the deadline be this tick?

    A reset to now + extend_seconds when the label is present, not a
    cumulative add onto whatever's left, so an extend always means
    "extend_seconds more from right now."
    """
    return now + extend_seconds if label_present else current_deadline


def should_stop(alive: bool, now: float, deadline: float) -> bool:
    return (not alive) or now >= deadline


def format_deadline(deadline: float) -> tuple[str, str]:
    """Render a deadline (seconds since epoch) as (human, ISO) strings."""
    human = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(deadline))
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(deadline))
    return human, iso


def run(
    cloudflared_path: str,
    tunnel_token: str,
    repo: str,
    pr_number: int,
    github_token: str,
    url: str,
    keycloak_url: str,
    deployed_at: str,
    deployed_at_iso: str,
    is_fork: bool = False,
    initial_seconds: int = 1200,
    poll_seconds: int = 15,
    extend_seconds: int = 1200,
    extend_label: str = "extend-preview",
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    list_labels_fn: Callable[[str, int, str], list[str]] = list_labels,
    delete_label_fn: Callable[[str, int, str, str], None] = delete_label,
    find_comment_id_fn: Callable[[str, int, str, str], int | None] = find_comment_id,
    update_comment_fn: Callable[[str, int, str, str], None] = update_comment,
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
            # `deadline` lives in `clock`'s namespace (time.monotonic() by
            # default), which has no relationship to the real calendar --
            # feeding it straight into format_deadline() produced garbage
            # like "1970-01-01" (confirmed live). Convert the remaining
            # duration into a real timestamp via `wall_clock` instead.
            seconds_remaining = deadline - now
            expires_at, expires_at_iso = format_deadline(wall_clock() + seconds_remaining)
            print(f"{extend_label} seen -- new deadline: {expires_at}")
            try:
                comment_id = find_comment_id_fn(repo, pr_number, STICKY_MARKER, github_token)
                if comment_id is not None:
                    body = (
                        render_ready(url, keycloak_url, deployed_at, deployed_at_iso, expires_at, expires_at_iso, is_fork)
                        + "\n" + STICKY_MARKER
                    )
                    update_comment_fn(repo, comment_id, body, github_token)
            except Exception as exc:  # noqa: BLE001 - the tunnel staying up matters more than the comment being exact
                print(f"warning: failed to update the PR comment after extend: {exc}")

        sleep(poll_seconds)


def _cmd_run(args: argparse.Namespace) -> int:
    return run(
        args.cloudflared, args.token, args.repo, args.pr, args.github_token,
        args.url, args.keycloak_url, args.deployed_at, args.deployed_at_iso,
        is_fork=args.fork,
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
    p.add_argument("--url", required=True)
    p.add_argument("--keycloak-url", required=True)
    p.add_argument("--deployed-at", required=True)
    p.add_argument("--deployed-at-iso", required=True)
    p.add_argument("--fork", action="store_true")
    p.add_argument("--initial-seconds", type=int, default=1200)
    p.add_argument("--poll-seconds", type=int, default=15)
    p.add_argument("--extend-seconds", type=int, default=1200)
    p.add_argument("--extend-label", default="extend-preview")
    p.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
