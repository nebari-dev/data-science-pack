"""Runs cloudflared for INITIAL_SECONDS; an extend-preview label on the PR
resets the deadline to now + EXTEND_SECONDS and is removed once consumed.
Each extend re-renders the PR comment's expiry (best-effort).

Usage:
    python -m scripts.preview.tunnel run --cloudflared PATH --token TOKEN \\
        --repo OWNER/REPO --pr N --run-url URL --kc-admin-password PW \\
        --url URL --keycloak-url URL \\
        --deployed-at STR --deployed-at-iso ISO [--fork]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from .comment import render_ready

STICKY_MARKER = "<!-- Sticky Pull Request Commentk8s-preview -->"
INITIAL_SECONDS = 1200
EXTEND_SECONDS = 1200
POLL_SECONDS = 15
EXTEND_LABEL = "extend-preview"


def _gh(*args: str) -> str:
    """Run gh (auth via GH_TOKEN) and return its trimmed stdout."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


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
    url: str,
    keycloak_url: str,
    run_url: str,
    kc_admin_password: str,
    deployed_at: str,
    deployed_at_iso: str,
    is_fork: bool = False,
) -> int:
    """Run cloudflared until its deadline, or until it exits on its own.

    Returns cloudflared's real exit code if it exited on its own (a
    genuine crash), or 0 if we closed it ourselves (deadline reached).
    """
    proc = subprocess.Popen([cloudflared_path, "tunnel", "--no-autoupdate", "run", "--token", tunnel_token])
    deadline = time.monotonic() + INITIAL_SECONDS

    while True:
        alive = proc.poll() is None
        now = time.monotonic()
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

        if EXTEND_LABEL in _gh("pr", "view", str(pr_number), "-R", repo, "--json", "labels", "--jq", ".labels[].name").split():
            deadline = next_deadline(now, deadline, True, EXTEND_SECONDS)
            _gh("pr", "edit", str(pr_number), "-R", repo, "--remove-label", EXTEND_LABEL)
            # deadline is monotonic, not epoch; convert via the remaining duration.
            expires_at, expires_at_iso = format_deadline(time.time() + (deadline - now))
            print(f"{EXTEND_LABEL} seen -- new deadline: {expires_at}")
            try:
                comment_id = _gh("api", f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
                                 "--jq", f'[.[] | select(.body | contains("{STICKY_MARKER}")) | .id] | first // empty')
                if comment_id:
                    body = (
                        render_ready(url, keycloak_url, run_url, kc_admin_password, deployed_at, deployed_at_iso, expires_at, expires_at_iso, is_fork)
                        + "\n" + STICKY_MARKER
                    )
                    _gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "-f", f"body={body}")
            except Exception as exc:  # noqa: BLE001 - the tunnel staying up matters more than the comment being exact
                print(f"warning: failed to update the PR comment after extend: {exc}")

        time.sleep(POLL_SECONDS)


def _cmd_run(args: argparse.Namespace) -> int:
    return run(
        args.cloudflared, args.token, args.repo, args.pr,
        args.url, args.keycloak_url, args.run_url, args.kc_admin_password,
        args.deployed_at, args.deployed_at_iso,
        is_fork=args.fork,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tunnel")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("--cloudflared", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--url", required=True)
    p.add_argument("--keycloak-url", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--kc-admin-password", required=True)
    p.add_argument("--deployed-at", required=True)
    p.add_argument("--deployed-at-iso", required=True)
    p.add_argument("--fork", action="store_true")
    p.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
