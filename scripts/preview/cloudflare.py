"""Cloudflare Tunnel + DNS API calls for the k8s preview environment.

One per-PR named Tunnel (not the anonymous quick-tunnel) so it can sit
behind a Cloudflare Access application and each PR gets its own hostname.

Usage (each subcommand mirrors one k8s-preview.yaml workflow step):
    python -m scripts.preview.cloudflare create-tunnel --account-id ID \\
        --api-token TOKEN --name NAME \\
        --preview-hostname HOST --preview-service URL \\
        --keycloak-hostname HOST --keycloak-service URL
    python -m scripts.preview.cloudflare create-dns --api-token TOKEN \\
        --domain DOMAIN --preview-hostname HOST --keycloak-hostname HOST \\
        --target TUNNEL_ID.cfargotunnel.com
    python -m scripts.preview.cloudflare delete-dns --api-token TOKEN \\
        --zone-id ID --record-id ID [--record-id ID ...]
    python -m scripts.preview.cloudflare delete-tunnel --account-id ID \\
        --api-token TOKEN --tunnel-id ID
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys

from . import gha
from .http import HTTPRequestError, request_json

API_ROOT = "https://api.cloudflare.com/client/v4"


class TunnelCreationError(RuntimeError):
    pass


class ZoneNotFoundError(RuntimeError):
    pass


def _headers(api_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_token}"}


def create_or_reuse_tunnel(account_id: str, api_token: str, tunnel_name: str) -> str:
    """Create a named Tunnel, or reuse one already created under this name.

    A GitHub Actions retry reuses the same run id (only run_attempt changes),
    so a re-run after an attempt that already created this tunnel (and didn't
    get to clean it up) hits a name conflict. Reuse the existing tunnel by
    name instead of failing -- it doesn't need the original tunnel secret,
    just a fresh token from ``get_tunnel_token``.
    """
    tunnel_secret = base64.b64encode(secrets.token_bytes(32)).decode()
    try:
        result = request_json(
            "POST",
            f"{API_ROOT}/accounts/{account_id}/cfd_tunnel",
            headers=_headers(api_token),
            body={"name": tunnel_name, "config_src": "cloudflare", "tunnel_secret": tunnel_secret},
        )
        return result["result"]["id"]
    except HTTPRequestError:
        existing = request_json(
            "GET",
            f"{API_ROOT}/accounts/{account_id}/cfd_tunnel?name={tunnel_name}&is_deleted=false",
            headers=_headers(api_token),
        )
        matches = existing.get("result") or []
        if not matches:
            raise TunnelCreationError(
                f"tunnel creation failed and no existing tunnel named {tunnel_name!r} was found"
            ) from None
        return matches[0]["id"]


def get_tunnel_token(account_id: str, api_token: str, tunnel_id: str) -> str:
    result = request_json(
        "GET",
        f"{API_ROOT}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token",
        headers=_headers(api_token),
    )
    return result["result"]


def configure_ingress(
    account_id: str, api_token: str, tunnel_id: str, hostname_services: list[tuple[str, str]]
) -> None:
    """Point each (hostname, service-url) pair at the tunnel, 404 for everything else."""
    ingress = [{"hostname": host, "service": service} for host, service in hostname_services]
    ingress.append({"service": "http_status:404"})
    request_json(
        "PUT",
        f"{API_ROOT}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
        headers=_headers(api_token),
        body={"config": {"ingress": ingress}},
    )


def delete_tunnel(account_id: str, api_token: str, tunnel_id: str) -> None:
    request_json(
        "DELETE",
        f"{API_ROOT}/accounts/{account_id}/cfd_tunnel/{tunnel_id}",
        headers=_headers(api_token),
    )


def resolve_zone_id(api_token: str, domain: str) -> str:
    result = request_json("GET", f"{API_ROOT}/zones?name={domain}", headers=_headers(api_token))
    matches = result.get("result") or []
    if not matches:
        raise ZoneNotFoundError(f"could not resolve a zone id for {domain!r}")
    return matches[0]["id"]


def create_dns_record(api_token: str, zone_id: str, hostname: str, target: str) -> str:
    result = request_json(
        "POST",
        f"{API_ROOT}/zones/{zone_id}/dns_records",
        headers=_headers(api_token),
        body={"type": "CNAME", "name": hostname, "content": target, "proxied": True},
    )
    return result["result"]["id"]


def delete_dns_record(api_token: str, zone_id: str, record_id: str) -> None:
    request_json(
        "DELETE",
        f"{API_ROOT}/zones/{zone_id}/dns_records/{record_id}",
        headers=_headers(api_token),
    )


def _cmd_create_tunnel(args: argparse.Namespace) -> int:
    try:
        tunnel_id = create_or_reuse_tunnel(args.account_id, args.api_token, args.name)
        token = get_tunnel_token(args.account_id, args.api_token, tunnel_id)
        configure_ingress(
            args.account_id, args.api_token, tunnel_id,
            [(args.preview_hostname, args.preview_service), (args.keycloak_hostname, args.keycloak_service)],
        )
    except (TunnelCreationError, HTTPRequestError) as exc:
        gha.error(f"Cloudflare Tunnel setup failed: {exc}")
        return 1
    gha.mask(token)
    gha.write_output("tunnel_id", tunnel_id)
    gha.write_env("TUNNEL_ID", tunnel_id)
    gha.write_env("TUNNEL_TOKEN", token)
    return 0


def _cmd_create_dns(args: argparse.Namespace) -> int:
    try:
        zone_id = resolve_zone_id(args.api_token, args.domain)
        record_id = create_dns_record(args.api_token, zone_id, args.preview_hostname, args.target)
        keycloak_record_id = create_dns_record(args.api_token, zone_id, args.keycloak_hostname, args.target)
    except (ZoneNotFoundError, HTTPRequestError) as exc:
        gha.error(f"DNS record creation failed: {exc}")
        return 1
    gha.write_env("ZONE_ID", zone_id)
    gha.write_env("DNS_RECORD_ID", record_id)
    gha.write_env("KEYCLOAK_DNS_RECORD_ID", keycloak_record_id)
    gha.write_output("url", f"https://{args.preview_hostname}")
    gha.write_output("keycloak_url", f"https://{args.keycloak_hostname}")
    return 0


def _cmd_delete_dns(args: argparse.Namespace) -> int:
    """Best-effort: cleanup steps must never fail the job."""
    if not args.zone_id:
        return 0
    for record_id in args.record_id:
        if not record_id:
            continue
        try:
            delete_dns_record(args.api_token, args.zone_id, record_id)
        except HTTPRequestError:
            pass
    return 0


def _cmd_delete_tunnel(args: argparse.Namespace) -> int:
    """Best-effort: cleanup steps must never fail the job."""
    if not args.tunnel_id:
        return 0
    try:
        delete_tunnel(args.account_id, args.api_token, args.tunnel_id)
    except HTTPRequestError:
        pass
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cloudflare")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-tunnel")
    p.add_argument("--account-id", required=True)
    p.add_argument("--api-token", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--preview-hostname", required=True)
    p.add_argument("--preview-service", required=True)
    p.add_argument("--keycloak-hostname", required=True)
    p.add_argument("--keycloak-service", required=True)
    p.set_defaults(func=_cmd_create_tunnel)

    p = sub.add_parser("create-dns")
    p.add_argument("--api-token", required=True)
    p.add_argument("--domain", required=True)
    p.add_argument("--preview-hostname", required=True)
    p.add_argument("--keycloak-hostname", required=True)
    p.add_argument("--target", required=True)
    p.set_defaults(func=_cmd_create_dns)

    p = sub.add_parser("delete-dns")
    p.add_argument("--api-token", required=True)
    p.add_argument("--zone-id", required=True)
    p.add_argument("--record-id", action="append", default=[])
    p.set_defaults(func=_cmd_delete_dns)

    p = sub.add_parser("delete-tunnel")
    p.add_argument("--account-id", required=True)
    p.add_argument("--api-token", required=True)
    p.add_argument("--tunnel-id", required=True)
    p.set_defaults(func=_cmd_delete_tunnel)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
