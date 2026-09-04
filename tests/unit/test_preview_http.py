"""Tests for scripts/preview/http.py's shared JSON HTTP helper."""

from __future__ import annotations

import json
import urllib.error

import pytest

from scripts.preview.http import HTTPRequestError, request_json


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_returns_parsed_json_body(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        return _FakeResponse(b'{"id": 42}')

    monkeypatch.setattr("scripts.preview.http.urllib.request.urlopen", fake_urlopen)

    result = request_json("GET", "https://api.example.com/thing", timeout=5)

    assert result == {"id": 42}
    assert captured["url"] == "https://api.example.com/thing"
    assert captured["method"] == "GET"
    assert captured["timeout"] == 5


def test_post_sends_json_encoded_body_with_content_type(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        captured["content_type"] = request.get_header("Content-type")
        return _FakeResponse(b"{}")

    monkeypatch.setattr("scripts.preview.http.urllib.request.urlopen", fake_urlopen)

    request_json("POST", "https://api.example.com/thing", body={"name": "pr-205"})

    assert json.loads(captured["data"]) == {"name": "pr-205"}
    assert captured["content_type"] == "application/json"


def test_string_body_is_sent_as_is_not_json_encoded(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        captured["content_type"] = request.get_header("Content-type")
        return _FakeResponse(b"{}")

    monkeypatch.setattr("scripts.preview.http.urllib.request.urlopen", fake_urlopen)

    request_json(
        "POST",
        "https://api.example.com/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body="grant_type=password&username=admin",
    )

    assert captured["data"] == b"grant_type=password&username=admin"
    # Content-Type came from the caller's headers, not auto-set to json.
    assert captured["content_type"] == "application/x-www-form-urlencoded"


def test_custom_headers_are_forwarded(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["auth"] = request.get_header("Authorization")
        return _FakeResponse(b"{}")

    monkeypatch.setattr("scripts.preview.http.urllib.request.urlopen", fake_urlopen)

    request_json(
        "GET",
        "https://api.example.com/thing",
        headers={"Authorization": "Bearer tok"},
    )

    assert captured["auth"] == "Bearer tok"


def test_empty_response_body_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(
        "scripts.preview.http.urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(b""),
    )

    assert request_json("DELETE", "https://api.example.com/thing") == {}


def test_http_error_raises_with_status_and_body(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", hdrs=None, fp=None
        )

    monkeypatch.setattr("scripts.preview.http.urllib.request.urlopen", fake_urlopen)
    # HTTPError.read() needs a real body stream; patch it directly on the
    # instance urlopen raises rather than fighting urllib's fp plumbing.
    real_error = urllib.error.HTTPError(
        "https://api.example.com/thing", 409, "Conflict", hdrs=None, fp=None
    )
    monkeypatch.setattr(real_error, "read", lambda: b"name already exists")
    monkeypatch.setattr(
        "scripts.preview.http.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(real_error),
    )

    with pytest.raises(HTTPRequestError) as exc_info:
        request_json("POST", "https://api.example.com/thing", body={"name": "x"})

    assert exc_info.value.status == 409
    assert "name already exists" in exc_info.value.body
    assert "name already exists" in str(exc_info.value)
