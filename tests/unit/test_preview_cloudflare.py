"""Tests for scripts/preview/cloudflare.py's Cloudflare Tunnel + DNS helpers."""

from __future__ import annotations

import pytest

from scripts.preview import cloudflare
from scripts.preview.http import HTTPRequestError

ACCOUNT_ID = "acct-1"
API_TOKEN = "cf-token"


def _capture(monkeypatch):
    calls = []

    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return fake_request_json.next_result

    fake_request_json.next_result = {}
    monkeypatch.setattr("scripts.preview.cloudflare.request_json", fake_request_json)
    return calls, fake_request_json


# --- tunnel create/reuse -----------------------------------------------------


def test_create_or_reuse_tunnel_returns_id_on_successful_create(monkeypatch):
    calls, fake = _capture(monkeypatch)
    fake.next_result = {"result": {"id": "tunnel-abc"}}

    tunnel_id = cloudflare.create_or_reuse_tunnel(ACCOUNT_ID, API_TOKEN, "pr-205-run-1")

    assert tunnel_id == "tunnel-abc"
    assert calls[0]["method"] == "POST"
    assert calls[0]["body"]["name"] == "pr-205-run-1"
    assert calls[0]["body"]["config_src"] == "cloudflare"
    assert "tunnel_secret" in calls[0]["body"]


def test_create_or_reuse_tunnel_reuses_existing_on_name_conflict(monkeypatch):
    lookup_result = {"result": [{"id": "existing-tunnel"}]}

    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        if method == "POST":
            raise HTTPRequestError(method, url, 409, "tunnel with name already exists")
        return lookup_result

    monkeypatch.setattr("scripts.preview.cloudflare.request_json", fake_request_json)

    tunnel_id = cloudflare.create_or_reuse_tunnel(ACCOUNT_ID, API_TOKEN, "pr-205-run-1")

    assert tunnel_id == "existing-tunnel"


def test_create_or_reuse_tunnel_raises_when_conflict_and_no_existing_found(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        if method == "POST":
            raise HTTPRequestError(method, url, 409, "conflict")
        return {"result": []}

    monkeypatch.setattr("scripts.preview.cloudflare.request_json", fake_request_json)

    with pytest.raises(cloudflare.TunnelCreationError):
        cloudflare.create_or_reuse_tunnel(ACCOUNT_ID, API_TOKEN, "pr-205-run-1")


# --- tunnel token / ingress / delete ------------------------------------------


def test_get_tunnel_token_returns_result_string(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = {"result": "the-token"}

    assert cloudflare.get_tunnel_token(ACCOUNT_ID, API_TOKEN, "tunnel-abc") == "the-token"


def test_configure_ingress_sends_hostname_rules_with_catchall(monkeypatch):
    calls, _ = _capture(monkeypatch)

    cloudflare.configure_ingress(
        ACCOUNT_ID, API_TOKEN, "tunnel-abc",
        [("pr-205.example.com", "http://localhost:8000"), ("kc-pr-205.example.com", "http://localhost:8001")],
    )

    ingress = calls[0]["body"]["config"]["ingress"]
    assert ingress[0] == {"hostname": "pr-205.example.com", "service": "http://localhost:8000"}
    assert ingress[1] == {"hostname": "kc-pr-205.example.com", "service": "http://localhost:8001"}
    assert ingress[-1] == {"service": "http_status:404"}


def test_delete_tunnel_calls_delete_endpoint(monkeypatch):
    calls, _ = _capture(monkeypatch)

    cloudflare.delete_tunnel(ACCOUNT_ID, API_TOKEN, "tunnel-abc")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/accounts/acct-1/cfd_tunnel/tunnel-abc")


# --- DNS -----------------------------------------------------------------------


def test_resolve_zone_id_returns_first_match(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = {"result": [{"id": "zone-1"}]}

    assert cloudflare.resolve_zone_id(API_TOKEN, "example.com") == "zone-1"


def test_resolve_zone_id_raises_when_not_found(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = {"result": []}

    with pytest.raises(cloudflare.ZoneNotFoundError):
        cloudflare.resolve_zone_id(API_TOKEN, "example.com")


def test_create_dns_record_returns_record_id(monkeypatch):
    calls, fake = _capture(monkeypatch)
    fake.next_result = {"result": {"id": "record-1"}}

    record_id = cloudflare.create_dns_record(API_TOKEN, "zone-1", "pr-205.example.com", "tunnel-abc.cfargotunnel.com")

    assert record_id == "record-1"
    assert calls[0]["body"] == {
        "type": "CNAME",
        "name": "pr-205.example.com",
        "content": "tunnel-abc.cfargotunnel.com",
        "proxied": True,
    }


def test_delete_dns_record_calls_delete_endpoint(monkeypatch):
    calls, _ = _capture(monkeypatch)

    cloudflare.delete_dns_record(API_TOKEN, "zone-1", "record-1")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/zones/zone-1/dns_records/record-1")


# --- CLI (main) ---------------------------------------------------------------


def test_main_create_tunnel_writes_outputs_and_masks_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudflare, "create_or_reuse_tunnel", lambda *a, **k: "tunnel-abc")
    monkeypatch.setattr(cloudflare, "get_tunnel_token", lambda *a, **k: "tok-xyz")
    configured = {}
    monkeypatch.setattr(
        cloudflare, "configure_ingress",
        lambda account_id, api_token, tunnel_id, hosts: configured.update(hosts=hosts),
    )
    out_file = tmp_path / "output"
    env_file = tmp_path / "env"
    out_file.write_text("")
    env_file.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    rc = cloudflare.main([
        "cloudflare", "create-tunnel",
        "--account-id", "acct-1", "--api-token", "tok", "--name", "pr-205-1",
        "--preview-hostname", "pr-205.example.com", "--preview-service", "http://localhost:8000",
        "--keycloak-hostname", "kc-pr-205.example.com", "--keycloak-service", "http://localhost:8001",
    ])

    assert rc == 0
    assert "tunnel_id=tunnel-abc" in out_file.read_text()
    assert "TUNNEL_ID=tunnel-abc" in env_file.read_text()
    assert "TUNNEL_TOKEN=tok-xyz" in env_file.read_text()
    assert configured["hosts"] == [
        ("pr-205.example.com", "http://localhost:8000"),
        ("kc-pr-205.example.com", "http://localhost:8001"),
    ]


def test_main_create_tunnel_exits_1_on_failure(monkeypatch, capsys):
    def boom(*a, **k):
        raise cloudflare.TunnelCreationError("no dice")

    monkeypatch.setattr(cloudflare, "create_or_reuse_tunnel", boom)

    rc = cloudflare.main([
        "cloudflare", "create-tunnel",
        "--account-id", "acct-1", "--api-token", "tok", "--name", "pr-205-1",
        "--preview-hostname", "pr-205.example.com", "--preview-service", "http://localhost:8000",
        "--keycloak-hostname", "kc-pr-205.example.com", "--keycloak-service", "http://localhost:8001",
    ])

    assert rc == 1
    assert "::error::" in capsys.readouterr().out


def test_main_delete_dns_noops_when_zone_id_missing(monkeypatch):
    called = []
    monkeypatch.setattr(cloudflare, "delete_dns_record", lambda *a, **k: called.append(a))

    rc = cloudflare.main(["cloudflare", "delete-dns", "--api-token", "tok", "--zone-id", "", "--record-id", "r1"])

    assert rc == 0
    assert called == []


def test_main_delete_dns_deletes_each_nonempty_record_id(monkeypatch):
    called = []
    monkeypatch.setattr(cloudflare, "delete_dns_record", lambda api_token, zone_id, record_id: called.append(record_id))

    rc = cloudflare.main([
        "cloudflare", "delete-dns", "--api-token", "tok", "--zone-id", "zone-1",
        "--record-id", "r1", "--record-id", "",
    ])

    assert rc == 0
    assert called == ["r1"]


def test_main_delete_dns_swallows_api_errors(monkeypatch):
    def boom(*a, **k):
        raise HTTPRequestError("DELETE", "https://x", 500, "boom")

    monkeypatch.setattr(cloudflare, "delete_dns_record", boom)

    rc = cloudflare.main(["cloudflare", "delete-dns", "--api-token", "tok", "--zone-id", "zone-1", "--record-id", "r1"])

    assert rc == 0  # cleanup steps are best-effort, never fail the job


def test_main_delete_tunnel_noops_when_tunnel_id_missing(monkeypatch):
    called = []
    monkeypatch.setattr(cloudflare, "delete_tunnel", lambda *a, **k: called.append(a))

    rc = cloudflare.main(["cloudflare", "delete-tunnel", "--account-id", "acct-1", "--api-token", "tok", "--tunnel-id", ""])

    assert rc == 0
    assert called == []
