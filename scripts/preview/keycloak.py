"""Keycloak admin API calls for the k8s preview environment's test user.

Cloudflare Access is the real security boundary for this preview (only
allow-listed accounts reach the tunnel at all), so a simple, known password
for the Keycloak-side login is fine -- reviewers don't need to hunt for real
credentials on a throwaway cluster.

Usage:
    python -m scripts.preview.keycloak create-reviewer-user --base-url URL \\
        --realm REALM --admin-password PASSWORD
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlencode

from . import gha
from .http import request_json


class KeycloakAuthError(RuntimeError):
    pass


def get_admin_token(base_url: str, admin_password: str) -> str:
    body = urlencode(
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "admin",
            "password": admin_password,
        }
    )
    result = request_json(
        "POST",
        f"{base_url}/realms/master/protocol/openid-connect/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=body,
    )
    token = result.get("access_token")
    if not token:
        raise KeycloakAuthError(f"no access_token in Keycloak admin token response: {result}")
    return token


def create_reviewer_user(base_url: str, realm: str, admin_token: str) -> None:
    request_json(
        "POST",
        f"{base_url}/admin/realms/{realm}/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        body={
            "username": "reviewer",
            "enabled": True,
            "email": "reviewer@example.com",
            "emailVerified": True,
            "firstName": "Preview",
            "lastName": "Reviewer",
            "credentials": [{"type": "password", "value": "admin", "temporary": False}],
        },
    )


def _cmd_create_reviewer_user(args: argparse.Namespace) -> int:
    try:
        token = get_admin_token(args.base_url, args.admin_password)
        create_reviewer_user(args.base_url, args.realm, token)
    except Exception as exc:  # noqa: BLE001 - report and fail the step either way
        gha.error(f"Failed to create the Keycloak reviewer user: {exc}")
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="keycloak")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-reviewer-user")
    p.add_argument("--base-url", required=True)
    p.add_argument("--realm", required=True)
    p.add_argument("--admin-password", required=True)
    p.set_defaults(func=_cmd_create_reviewer_user)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
