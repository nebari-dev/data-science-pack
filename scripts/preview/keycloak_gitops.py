"""Repoint Keycloak's own hostname via NIC's local GitOps repo.

Keycloak's own KC_HOSTNAME (codecentric/keycloakx, via NIC's
values/keycloak/base.yaml) is fixed to NIC's internal keycloak.nebari.local
by default -- every self-referencing URL Keycloak renders (login form
action, issuer, redirects) is absolute and uses that value regardless of
the incoming Host header.

A direct `kubectl set env` patch onto the live StatefulSet/Deployment DOES
apply immediately but gets silently reverted: both Keycloak and
nebari-operator are ArgoCD Applications with selfHeal: true, continuously
reconciled against NIC's auto-created local GitOps repo
(~/.nic/gitops/<cluster-name>). Editing the GitOps repo itself and forcing
a hard refresh lets selfHeal work for this change instead of against it.

Usage:
    python -m scripts.preview.keycloak_gitops patch \\
        --gitops-dir DIR --kc-public-url URL
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import gha

OLD_HOSTNAME_URL = "https://keycloak.nebari.local"
GITOPS_FILES = ("values/keycloak/base.yaml", "manifests/nebari-operator/deployment-patch.yaml")


class OperatorNotFoundError(RuntimeError):
    pass


def rewrite_hostname(text: str, old_url: str, new_url: str) -> str:
    """Replace every literal occurrence of ``old_url`` with ``new_url``."""
    return text.replace(old_url, new_url)


def find_operator_deployment(deployments_json: dict) -> tuple[str, str]:
    """Return (namespace, name) of the deployment whose name contains "operator"."""
    for item in deployments_json.get("items", []):
        name = item["metadata"]["name"]
        if re.search("operator", name):
            return item["metadata"]["namespace"], name
    raise OperatorNotFoundError("no deployment with 'operator' in its name was found")


def extract_env_value(env: list[dict], var_name: str) -> str:
    """Return the value of ``var_name`` in a container env list, or "" if absent."""
    for entry in env:
        if entry.get("name") == var_name:
            return entry.get("value", "")
    return ""


def wait_until_both_match(
    get_values: Callable[[], tuple[str, str]],
    expected: str,
    timeout_s: int = 120,
    poll_interval_s: int = 5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, tuple[str, str]]:
    """Poll ``get_values()`` until both returned values equal ``expected``.

    `rollout status` alone can return instantly if ArgoCD hasn't actually
    applied the refreshed spec yet (nothing new requested from kubectl's
    point of view) -- poll for the live values to actually change before
    trusting rollout status to mean anything.
    """
    deadline = clock() + timeout_s
    values = ("", "")
    while clock() < deadline:
        values = get_values()
        if values[0] == expected and values[1] == expected:
            return True, values
        sleep(poll_interval_s)
    return False, values


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True)


def _cmd_patch(args: argparse.Namespace) -> int:
    gitops_dir = Path(args.gitops_dir)

    for relative_path in GITOPS_FILES:
        path = gitops_dir / relative_path
        original = path.read_text()
        print(f"--- {relative_path}: {original.count(OLD_HOSTNAME_URL)} occurrence(s) of {OLD_HOSTNAME_URL} ---")
        path.write_text(rewrite_hostname(original, OLD_HOSTNAME_URL, args.kc_public_url))

    _run([
        "git", "-C", str(gitops_dir),
        "-c", "user.email=ci@example.com", "-c", "user.name=k8s-preview-ci",
        "commit", "-am", "Point Keycloak hostname at the public preview tunnel route",
    ])

    _run([
        "kubectl", "-n", "argocd", "annotate",
        "application/keycloak", "application/nebari-operator",
        "argocd.argoproj.io/refresh=hard", "--overwrite",
    ])

    deployments = json.loads(_run(["kubectl", "get", "deploy", "-A", "-o", "json"]).stdout)
    try:
        operator_namespace, operator_name = find_operator_deployment(deployments)
    except OperatorNotFoundError as exc:
        gha.error(str(exc))
        return 1

    def get_values() -> tuple[str, str]:
        kc_env = json.loads(_run([
            "kubectl", "-n", "keycloak", "get", "statefulset", "keycloak-keycloakx",
            "-o", "jsonpath={.spec.template.spec.containers[0].env}",
        ]).stdout or "[]")
        op_env = json.loads(_run([
            "kubectl", "-n", operator_namespace, "get", "deploy", operator_name,
            "-o", "jsonpath={.spec.template.spec.containers[0].env}",
        ]).stdout or "[]")
        return (
            extract_env_value(kc_env, "KC_HOSTNAME"),
            extract_env_value(op_env, "KEYCLOAK_EXTERNAL_URL"),
        )

    matched, (kc_live, op_live) = wait_until_both_match(get_values, args.kc_public_url)
    if not matched:
        gha.error(
            "ArgoCD never applied the GitOps hostname change within 2m "
            f"(keycloak={kc_live}, operator={op_live})"
        )
        return 1

    _run(["kubectl", "-n", "keycloak", "rollout", "status", "statefulset/keycloak-keycloakx", "--timeout=180s"])
    _run(["kubectl", "-n", operator_namespace, "rollout", "status", f"deployment/{operator_name}", "--timeout=180s"])
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="keycloak_gitops")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("patch")
    p.add_argument("--gitops-dir", required=True)
    p.add_argument("--kc-public-url", required=True)
    p.set_defaults(func=_cmd_patch)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
