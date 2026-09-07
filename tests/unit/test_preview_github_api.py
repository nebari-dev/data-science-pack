"""Tests for scripts/preview/github_api.py's GitHub REST helpers."""

from __future__ import annotations

import pytest

from scripts.preview import github_api
from scripts.preview.http import HTTPRequestError

REPO = "nebari-dev/data-science-pack"
TOKEN = "gh-token"


def _capture(monkeypatch):
    calls = []

    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return fake_request_json.next_result

    fake_request_json.next_result = {}
    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)
    return calls, fake_request_json


# --- labels -----------------------------------------------------------------


def test_delete_label_swallows_404_already_removed(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        raise HTTPRequestError(method, url, 404, "Not Found")

    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)

    github_api.delete_label(REPO, 205, "extend-preview", TOKEN)  # must not raise


def test_delete_label_reraises_other_errors(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        raise HTTPRequestError(method, url, 403, "Forbidden")

    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)

    with pytest.raises(HTTPRequestError):
        github_api.delete_label(REPO, 205, "extend-preview", TOKEN)


# --- comments -----------------------------------------------------------------


def test_find_comment_id_returns_id_of_comment_containing_marker(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = [
        {"id": 111, "body": "unrelated comment"},
        {"id": 222, "body": "some text\n<!-- Sticky Pull Request Commentk8s-preview -->"},
    ]

    found = github_api.find_comment_id(REPO, 205, "<!-- Sticky Pull Request Commentk8s-preview -->", TOKEN)

    assert found == 222


def test_find_comment_id_returns_none_when_not_found(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = [{"id": 111, "body": "unrelated comment"}]

    assert github_api.find_comment_id(REPO, 205, "<!-- Sticky Pull Request Commentk8s-preview -->", TOKEN) is None
