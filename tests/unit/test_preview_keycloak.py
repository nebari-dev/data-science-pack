"""Tests for scripts/preview/keycloak.py's admin API helpers."""

from __future__ import annotations

import pytest

from scripts.preview import keycloak
from scripts.preview.http import HTTPRequestError

BASE_URL = "http://localhost:8001"


def _capture(monkeypatch):
    calls = []

    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return fake_request_json.next_result

    fake_request_json.next_result = {}
    monkeypatch.setattr("scripts.preview.keycloak.request_json", fake_request_json)
    return calls, fake_request_json


def test_get_admin_token_sends_password_grant_as_form_body(monkeypatch):
    calls, fake = _capture(monkeypatch)
    fake.next_result = {"access_token": "the-token"}

    token = keycloak.get_admin_token(BASE_URL, "adminpw")

    assert token == "the-token"
    assert calls[0]["url"] == f"{BASE_URL}/realms/master/protocol/openid-connect/token"
    assert calls[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    body = calls[0]["body"]
    assert "grant_type=password" in body
    assert "client_id=admin-cli" in body
    assert "username=admin" in body
    assert "password=adminpw" in body


def test_get_admin_token_url_encodes_special_characters_in_password(monkeypatch):
    calls, fake = _capture(monkeypatch)
    fake.next_result = {"access_token": "tok"}

    keycloak.get_admin_token(BASE_URL, "p@ss w/ord&x")

    assert "password=p%40ss+w%2Ford%26x" in calls[0]["body"]


def test_get_admin_token_raises_when_response_has_no_access_token(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = {"error": "invalid_grant"}

    with pytest.raises(keycloak.KeycloakAuthError):
        keycloak.get_admin_token(BASE_URL, "wrong")


def test_get_admin_token_propagates_http_errors(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        raise HTTPRequestError(method, url, 401, "unauthorized")

    monkeypatch.setattr("scripts.preview.keycloak.request_json", fake_request_json)

    with pytest.raises(HTTPRequestError):
        keycloak.get_admin_token(BASE_URL, "wrong")


def test_create_reviewer_user_posts_expected_payload(monkeypatch):
    calls, _ = _capture(monkeypatch)

    keycloak.create_reviewer_user(BASE_URL, "nebari", "admin-token")

    assert calls[0]["url"] == f"{BASE_URL}/admin/realms/nebari/users"
    assert calls[0]["headers"]["Authorization"] == "Bearer admin-token"
    body = calls[0]["body"]
    assert body["username"] == "reviewer"
    assert body["enabled"] is True
    assert body["email"] == "reviewer@example.com"
    assert body["emailVerified"] is True
    assert body["firstName"] == "Preview"
    assert body["lastName"] == "Reviewer"
    assert body["credentials"] == [{"type": "password", "value": "admin", "temporary": False}]
