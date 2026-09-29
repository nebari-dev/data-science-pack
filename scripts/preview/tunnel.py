"""Runs cloudflared for INITIAL_SECONDS; an extend-preview label on the PR
resets the deadline to now + EXTEND_SECONDS and is removed once consumed.
Each extend re-renders the PR comment's expiry (best-effort).

Also runs the `kubectl port-forward`s cloudflared sends traffic to, with
their output in this step's log. A port-forward that exits after it was
forwarding is restarted; one that exits before it ever forwarded fails the run.

Usage:
    KC_ADMIN_PASSWORD=... python -m scripts.preview.tunnel run \\
        --cloudflared PATH --token TOKEN \\
        --repo OWNER/REPO --pr N --run-url URL \\
        --url URL --keycloak-url URL \\
        --deployed-at STR --deployed-at-iso ISO \\
        --port-forward NAMESPACE RESOURCE LOCAL:REMOTE [--port-forward ...]

The Keycloak admin password (re-rendered into the PR comment on each extend)
comes from KC_ADMIN_PASSWORD, not argv; see keycloak.admin_password_from_env.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time

from .comment import render_ready
from .http import request_json
from .keycloak import admin_password_from_env

STICKY_MARKER = "<!-- Sticky Pull Request Commentk8s-preview -->"
INITIAL_SECONDS = 1200
EXTEND_SECONDS = 1200
POLL_SECONDS = 15
CHECK_SECONDS = 1
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


class PortForward:
    """One `kubectl port-forward`, restarted each time it exits after forwarding.

    kubectl prints "Forwarding from ..." only once it has connected to the pod
    and is listening locally; after that it exits when the pod connection
    closes ("lost connection to pod"). Exiting without that line means it
    could not set the forward up at all, and its error says why.
    """

    def __init__(self, namespace: str, resource: str, ports: str) -> None:
        self.name = f"{namespace}/{resource} {ports}"
        self._cmd = ["kubectl", "-n", namespace, "port-forward", resource, ports]
        self._start()

    def _start(self) -> None:
        self._forwarding = False
        self._proc = subprocess.Popen(self._cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self._relay = threading.Thread(target=self._relay_output, args=(self._proc,), daemon=True)
        self._relay.start()

    def _relay_output(self, proc: subprocess.Popen) -> None:
        # kubectl prints "Handling connection for N" per connection; drop it, keep the rest.
        for line in proc.stdout:
            if line.startswith("Forwarding from"):
                self._forwarding = True
            if not line.startswith("Handling connection for"):
                print(f"[port-forward {self.name}] {line.rstrip()}")

    def ensure_running(self) -> bool:
        """Restart kubectl if it exited after forwarding. False if it exited before it ever forwarded."""
        code = self._proc.poll()
        if code is None:
            return True
        self._relay.join()  # kubectl's output is closed once it exits; the relay has seen every line
        if not self._forwarding:
            print(f"port-forward {self.name} exited with code {code} before it started forwarding, giving up")
            return False
        print(f"port-forward {self.name} exited with code {code}, restarting")
        self._start()
        return True

    def stop(self) -> None:
        _stop(self._proc)


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
                args.url, args.keycloak_url, args.run_url, admin_password_from_env(),
                args.deployed_at, args.deployed_at_iso, expires_at, expires_at_iso,
            )
            _update_comment(args.repo, comment_id, body + "\n" + STICKY_MARKER)
    except Exception as exc:  # noqa: BLE001 - the tunnel staying up matters more than the comment being exact
        print(f"warning: failed to update the PR comment after extend: {exc}")


def run(args: argparse.Namespace) -> int:
    """Run the port-forwards and cloudflared until the deadline, extending it
    when the label appears.

    Returns cloudflared's exit code if it crashed, 1 if a port-forward could
    not be kept running, 0 on a clean deadline stop.
    """
    forwards = [PortForward(*spec) for spec in args.port_forward]
    proc = subprocess.Popen([args.cloudflared, "tunnel", "--no-autoupdate", "run", "--token", args.token])
    deadline = time.monotonic() + INITIAL_SECONDS
    next_label_check = time.monotonic()
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            if not all(forward.ensure_running() for forward in forwards):
                return 1
            if time.monotonic() >= next_label_check:
                if EXTEND_LABEL in _pr_labels(args.repo, args.pr):
                    deadline = time.monotonic() + EXTEND_SECONDS
                    _remove_label(args.repo, args.pr, EXTEND_LABEL)
                    _refresh_comment_expiry(args, deadline - time.monotonic())
                next_label_check = time.monotonic() + POLL_SECONDS
            time.sleep(CHECK_SECONDS)
        if proc.poll() is not None:
            return proc.returncode
        return 0
    finally:
        for forward in forwards:
            forward.stop()
        if proc.poll() is None:
            _stop(proc)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tunnel")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("--cloudflared", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--run-url", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--keycloak-url", required=True)
    p.add_argument("--deployed-at", required=True)
    p.add_argument("--deployed-at-iso", required=True)
    p.add_argument("--port-forward", nargs=3, action="append", default=[],
                   metavar=("NAMESPACE", "RESOURCE", "LOCAL:REMOTE"))
    p.set_defaults(func=run)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
