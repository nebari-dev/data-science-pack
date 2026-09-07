"""Tests for scripts/preview/keycloak.py's admin API helpers."""

from __future__ import annotations

import pytest

from scripts.preview import keycloak

BASE_URL = "http://localhost:8001"


def _capture(monkeypatch):
    calls = []

    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return fake_request_json.next_result

    fake_request_json.next_result = {}
    monkeypatch.setattr("scripts.preview.keycloak.request_json", fake_request_json)
    return calls, fake_request_json


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
