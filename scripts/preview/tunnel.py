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
import os
import subprocess
import sys
import time

from .comment import render_ready
from .http import request_json

STICKY_MARKER = "<!-- Sticky Pull Request Commentk8s-preview -->"
INITIAL_SECONDS = 1200
EXTEND_SECONDS = 1200
POLL_SECONDS = 15
EXTEND_LABEL = "extend-preview"


def _api(method: str, path: str, body: dict | None = None) -> dict | list:
    """GitHub REST call, authenticated with the workflow's GH_TOKEN."""
    headers = {
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }
    return request_json(method, f"https://api.github.com/{path}", headers=headers, body=body)


def _pr_labels(repo: str, pr_number: int) -> list[str]:
    return [item["name"] for item in _api("GET", f"repos/{repo}/issues/{pr_number}/labels")]


def _remove_label(repo: str, pr_number: int, label: str) -> None:
    _api("DELETE", f"repos/{repo}/issues/{pr_number}/labels/{label}")


def _sticky_comment_id(repo: str, pr_number: int) -> int | None:
    comments = _api("GET", f"repos/{repo}/issues/{pr_number}/comments?per_page=100")
    return next((c["id"] for c in comments if STICKY_MARKER in c.get("body", "")), None)


def _update_comment(repo: str, comment_id: int, body: str) -> None:
    _api("PATCH", f"repos/{repo}/issues/comments/{comment_id}", body={"body": body})


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _refresh_comment_expiry(args: argparse.Namespace, seconds_remaining: float) -> None:
    """Rewrite the sticky comment's Expires line for the new deadline. Best-effort."""
    deadline = time.gmtime(time.time() + seconds_remaining)
    expires_at = time.strftime("%Y-%m-%d %H:%M UTC", deadline)
    expires_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", deadline)
    print(f"{EXTEND_LABEL} seen -- new deadline: {expires_at}")
    try:
        comment_id = _sticky_comment_id(args.repo, args.pr)
        if comment_id is not None:
            body = render_ready(
                args.url, args.keycloak_url, args.run_url, args.kc_admin_password,
                args.deployed_at, args.deployed_at_iso, expires_at, expires_at_iso, args.fork,
            )
            _update_comment(args.repo, comment_id, body + "\n" + STICKY_MARKER)
    except Exception as exc:  # noqa: BLE001 - the tunnel staying up matters more than the comment being exact
        print(f"warning: failed to update the PR comment after extend: {exc}")


def run(args: argparse.Namespace) -> int:
    """Run cloudflared until the deadline, extending it when the label appears.

    Returns cloudflared's exit code if it crashed, 0 on a clean deadline stop.
    """
    proc = subprocess.Popen([args.cloudflared, "tunnel", "--no-autoupdate", "run", "--token", args.token])
    deadline = time.monotonic() + INITIAL_SECONDS

    while proc.poll() is None and time.monotonic() < deadline:
        if EXTEND_LABEL in _pr_labels(args.repo, args.pr):
            deadline = time.monotonic() + EXTEND_SECONDS
            _remove_label(args.repo, args.pr, EXTEND_LABEL)
            _refresh_comment_expiry(args, deadline - time.monotonic())
        time.sleep(POLL_SECONDS)

    if proc.poll() is not None:
        return proc.returncode
    _stop(proc)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tunnel")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("--cloudflared", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--run-url", required=True)
    p.add_argument("--kc-admin-password", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--keycloak-url", required=True)
    p.add_argument("--deployed-at", required=True)
    p.add_argument("--deployed-at-iso", required=True)
    p.add_argument("--fork", action="store_true")
    p.set_defaults(func=run)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
