"""kubectl readiness retry loops for the k8s preview environment.

Usage:
    python -m scripts.preview.k8s_wait wait-for-secret-key --namespace NS \\
        --secret NAME --key KEY [--timeout-s 180] [--poll-interval-s 5]
    python -m scripts.preview.k8s_wait restart-until-ready --namespace NS \\
        --deployment NAME [--rollout-timeout-s 90] [--max-attempts 5]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable

from . import gha


def wait_for_secret_key(
    get_value: Callable[[], str],
    timeout_s: int = 180,
    poll_interval_s: int = 5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll ``get_value()`` until it returns a non-empty string, or timeout.

    The operator creates the Secret with client-id/client-secret on its
    first reconcile pass, then patches in the issuer-url key on a later
    pass -- checking the Secret merely EXISTS isn't enough, a hub restart
    right after its first appearance still crashes on the not-yet-populated
    key.
    """
    deadline = clock() + timeout_s
    while clock() < deadline:
        if get_value():
            return True
        sleep(poll_interval_s)
    return False


def restart_until_ready(
    restart: Callable[[], None],
    check_status: Callable[[], bool],
    max_attempts: int = 5,
) -> tuple[bool, int]:
    """Restart, then check readiness; repeat until ready or ``max_attempts``.

    A single restart isn't reliable here even when the API server confirms
    the Secret is fully populated: kubelet's own Secret volume cache
    (node-local, ~1min TTL) can still hand a freshly-restarted pod the
    pre-population snapshot it fetched for the pod's first, crash-looped
    attempt. Retrying gives the kubelet cache time to expire between
    attempts.

    Returns (became_ready, attempts_used).
    """
    for attempt in range(1, max_attempts + 1):
        restart()
        if check_status():
            return True, attempt
    return False, max_attempts


def _cmd_wait_for_secret_key(args: argparse.Namespace) -> int:
    def get_value() -> str:
        result = subprocess.run(
            ["kubectl", "-n", args.namespace, "get", "secret", args.secret,
             "-o", f"jsonpath={{.data.{args.key}}}"],
            text=True, capture_output=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    ok = wait_for_secret_key(get_value, timeout_s=args.timeout_s, poll_interval_s=args.poll_interval_s)
    if not ok:
        gha.error(
            f"operator never populated {args.key!r} on secret {args.secret!r} "
            f"within {args.timeout_s}s"
        )
        subprocess.run(["kubectl", "-n", args.namespace, "get", "nebariapp", "-o", "yaml"], check=False)
        return 1
    return 0


def _cmd_restart_until_ready(args: argparse.Namespace) -> int:
    target = f"deployment/{args.deployment}"

    def restart() -> None:
        subprocess.run(["kubectl", "-n", args.namespace, "rollout", "restart", target], check=False)

    def check_status() -> bool:
        result = subprocess.run([
            "kubectl", "-n", args.namespace, "rollout", "status", target,
            f"--timeout={args.rollout_timeout_s}s",
        ], check=False)
        return result.returncode == 0

    ok, attempts = restart_until_ready(restart, check_status, max_attempts=args.max_attempts)
    if ok:
        print(f"{args.deployment} ready on attempt {attempts}")
        return 0
    gha.error(f"{args.deployment} never became ready after {args.max_attempts} restart attempts")
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="k8s_wait")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("wait-for-secret-key")
    p.add_argument("--namespace", required=True)
    p.add_argument("--secret", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--timeout-s", type=int, default=180)
    p.add_argument("--poll-interval-s", type=int, default=5)
    p.set_defaults(func=_cmd_wait_for_secret_key)

    p = sub.add_parser("restart-until-ready")
    p.add_argument("--namespace", required=True)
    p.add_argument("--deployment", required=True)
    p.add_argument("--rollout-timeout-s", type=int, default=90)
    p.add_argument("--max-attempts", type=int, default=5)
    p.set_defaults(func=_cmd_restart_until_ready)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
