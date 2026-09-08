"""PR-comment body builders for the k8s preview environment.

Pure string-building functions -- the actual find-or-edit-in-place
mechanics stay in marocchino/sticky-pull-request-comment (an already-vetted
action doing exactly what it's for); only the message *content* lives here.
Uses GitHub's own <relative-time> web component (used all over its UI for
"3 minutes ago") instead of a static UTC string the reader has to convert
by hand -- confirmed via `gh api /markdown` to survive comment sanitization
unstripped.

Usage:
    python -m scripts.preview.comment render-deploying --run-url URL
    KC_ADMIN_PASSWORD=... python -m scripts.preview.comment render-ready --url URL \\
        --keycloak-url URL --run-url URL \\
        --deployed-at STR --deployed-at-iso ISO \\
        --expires-at STR --expires-at-iso ISO [--fork]
    python -m scripts.preview.comment render-expired \\
        --expires-at STR --expires-at-iso ISO
    python -m scripts.preview.comment render-failed --run-url URL
    python -m scripts.preview.comment render-stopped

render-ready takes the Keycloak admin password from KC_ADMIN_PASSWORD (see
keycloak.admin_password_from_env for why it is not an argv flag).
"""

from __future__ import annotations

import argparse
import sys
import time

from . import gha
from .keycloak import admin_password_from_env

PROJECT = "nebari-data-science-pack"


def _now_relative_time() -> str:
    now = time.gmtime()
    human = time.strftime("%Y-%m-%d %H:%M UTC", now)
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", now)
    return f'<relative-time datetime="{iso}">{human}</relative-time>'


def render_deploying(run_url: str) -> str:
    return (
        "The latest preview for this PR.\n\n"
        "| Project | Deployment | Actions | Updated |\n"
        "| --- | --- | --- | --- |\n"
        f"| `{PROJECT}` | 🟡 [Deploying]({run_url}) | [CI run]({run_url}) | {_now_relative_time()} |\n\n"
        "Should be ready in a few minutes."
    )


def render_failed(run_url: str) -> str:
    return (
        "The preview for this PR failed to deploy.\n\n"
        "| Project | Deployment | Actions | Updated |\n"
        "| --- | --- | --- | --- |\n"
        f"| `{PROJECT}` | 🔴 [Failed]({run_url}) | [CI run]({run_url}) | {_now_relative_time()} |\n\n"
        "Re-add the `deploy-preview` label to retry."
    )


def render_ready(
    url: str,
    keycloak_url: str,
    run_url: str,
    kc_admin_password: str,
    deployed_at: str,
    deployed_at_iso: str,
    expires_at: str,
    expires_at_iso: str,
    is_fork: bool,
) -> str:
    fork_warning = (
        "\n\n⚠️ **This PR is from a fork**: the code running in this preview "
        "is not from a trusted maintainer branch."
        if is_fork
        else ""
    )
    return (
        "The latest preview for this PR.\n\n"
        "| Project | Deployment | Actions | Updated |\n"
        "| --- | --- | --- | --- |\n"
        f"| `{PROJECT}` | 🟢 [Ready]({url}) | [Preview]({url}) · [Keycloak]({keycloak_url}) · [CI run]({run_url}) | "
        f'<relative-time datetime="{deployed_at_iso}">{deployed_at}</relative-time> |'
        f"{fork_warning}\n\n"
        f"Sign in with `reviewer` / `admin`. Keycloak admin: `admin` / `{kc_admin_password}`.\n\n"
        f'Expires <relative-time datetime="{expires_at_iso}">{expires_at}</relative-time>. '
        "Add the `extend-preview` label any time before then to reset it to 20 minutes from that moment, "
        "or push a new commit or re-add `deploy-preview` to redeploy from scratch."
    )


def render_expired(expires_at: str, expires_at_iso: str) -> str:
    return (
        "The preview for this PR has expired.\n\n"
        "| Project | Deployment | Actions | Updated |\n"
        "| --- | --- | --- | --- |\n"
        f"| `{PROJECT}` | ⚫ Expired | - | "
        f'<relative-time datetime="{expires_at_iso}">{expires_at}</relative-time> |\n\n'
        "Re-add the `deploy-preview` label to redeploy."
    )


def render_stopped() -> str:
    return (
        "**Preview** stopped: the `deploy-preview` label was removed.\n\n"
        "Add it again to redeploy."
    )


def _cmd_render_deploying(args: argparse.Namespace) -> int:
    gha.write_output("body", render_deploying(args.run_url))
    return 0


def _cmd_render_ready(args: argparse.Namespace) -> int:
    body = render_ready(
        args.url, args.keycloak_url, args.run_url, admin_password_from_env(),
        args.deployed_at, args.deployed_at_iso,
        args.expires_at, args.expires_at_iso, args.fork,
    )
    gha.write_output("body", body)
    return 0


def _cmd_render_expired(args: argparse.Namespace) -> int:
    gha.write_output("body", render_expired(args.expires_at, args.expires_at_iso))
    return 0


def _cmd_render_failed(args: argparse.Namespace) -> int:
    gha.write_output("body", render_failed(args.run_url))
    return 0


def _cmd_render_stopped(args: argparse.Namespace) -> int:
    gha.write_output("body", render_stopped())
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="comment")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("render-deploying")
    p.add_argument("--run-url", required=True)
    p.set_defaults(func=_cmd_render_deploying)

    p = sub.add_parser("render-ready")
    p.add_argument("--url", required=True)
    p.add_argument("--keycloak-url", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--deployed-at", required=True)
    p.add_argument("--deployed-at-iso", required=True)
    p.add_argument("--expires-at", required=True)
    p.add_argument("--expires-at-iso", required=True)
    p.add_argument("--fork", action="store_true")
    p.set_defaults(func=_cmd_render_ready)

    p = sub.add_parser("render-expired")
    p.add_argument("--expires-at", required=True)
    p.add_argument("--expires-at-iso", required=True)
    p.set_defaults(func=_cmd_render_expired)

    p = sub.add_parser("render-failed")
    p.add_argument("--run-url", required=True)
    p.set_defaults(func=_cmd_render_failed)

    p = sub.add_parser("render-stopped")
    p.set_defaults(func=_cmd_render_stopped)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
