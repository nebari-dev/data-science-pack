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


def test_list_labels_returns_names(monkeypatch):
    calls, fake = _capture(monkeypatch)
    fake.next_result = [{"name": "deploy-preview"}, {"name": "extend-preview"}]

    result = github_api.list_labels(REPO, 205, TOKEN)

    assert result == ["deploy-preview", "extend-preview"]
    assert calls[0]["url"] == f"https://api.github.com/repos/{REPO}/issues/205/labels"
    assert calls[0]["method"] == "GET"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_delete_label_calls_delete_endpoint(monkeypatch):
    calls, _ = _capture(monkeypatch)

    github_api.delete_label(REPO, 205, "extend-preview", TOKEN)

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"] == f"https://api.github.com/repos/{REPO}/issues/205/labels/extend-preview"


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


def test_ensure_label_exists_swallows_422_already_exists(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        raise HTTPRequestError(method, url, 422, "already_exists")

    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)

    github_api.ensure_label_exists(REPO, "extend-preview", "BFD4F2", "desc", TOKEN)


def test_ensure_label_exists_reraises_other_errors(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        raise HTTPRequestError(method, url, 500, "boom")

    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)

    with pytest.raises(HTTPRequestError):
        github_api.ensure_label_exists(REPO, "extend-preview", "BFD4F2", "desc", TOKEN)


# --- deployments --------------------------------------------------------------


def test_create_deployment_posts_expected_payload_and_returns_id(monkeypatch):
    calls, fake = _capture(monkeypatch)
    fake.next_result = {"id": 6199751384}

    deployment_id = github_api.create_deployment(
        REPO, ref="abc123", environment="pr-205-preview", token=TOKEN,
        task="deploy:preview", description="K8s stack preview",
    )

    assert deployment_id == 6199751384
    body = calls[0]["body"]
    assert body["ref"] == "abc123"
    assert body["environment"] == "pr-205-preview"
    assert body["task"] == "deploy:preview"
    assert body["auto_merge"] is False
    assert body["transient_environment"] is True
    assert body["production_environment"] is False
    assert body["required_contexts"] == []


def test_set_deployment_status_omits_unset_optional_fields(monkeypatch):
    calls, _ = _capture(monkeypatch)

    github_api.set_deployment_status(REPO, 42, "success", TOKEN, environment_url="https://x")

    body = calls[0]["body"]
    assert body["state"] == "success"
    assert body["environment_url"] == "https://x"
    assert "log_url" not in body
    assert "description" not in body


def test_mark_deployment_inactive_sets_inactive_state(monkeypatch):
    calls, _ = _capture(monkeypatch)

    github_api.mark_deployment_inactive(REPO, 42, TOKEN, description="Preview expired")

    assert calls[0]["url"] == f"https://api.github.com/repos/{REPO}/deployments/42/statuses"
    assert calls[0]["body"]["state"] == "inactive"
    assert calls[0]["body"]["description"] == "Preview expired"


def test_find_latest_deployment_id_returns_first_result(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = [{"id": 111}, {"id": 222}]

    assert github_api.find_latest_deployment_id(REPO, "pr-205-preview", TOKEN) == 111


def test_find_latest_deployment_id_returns_none_when_empty(monkeypatch):
    _, fake = _capture(monkeypatch)
    fake.next_result = []

    assert github_api.find_latest_deployment_id(REPO, "pr-205-preview", TOKEN) is None


# --- runs ---------------------------------------------------------------------


def test_cancel_in_flight_run_finds_and_cancels_matching_run(monkeypatch):
    calls = []

    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if "workflow_runs" in url or url.endswith("status=in_progress"):
            return {
                "workflow_runs": [
                    {"id": 1, "name": "Other Workflow", "pull_requests": [{"number": 205}]},
                    {"id": 2, "name": "K8s Stack Preview", "pull_requests": [{"number": 999}]},
                    {"id": 3, "name": "K8s Stack Preview", "pull_requests": [{"number": 205}]},
                ]
            }
        return {}

    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)

    cancelled = github_api.cancel_in_flight_run(REPO, "K8s Stack Preview", 205, TOKEN)

    assert cancelled == 3
    cancel_calls = [c for c in calls if c["url"].endswith("/runs/3/cancel")]
    assert len(cancel_calls) == 1
    assert cancel_calls[0]["method"] == "POST"


def test_cancel_in_flight_run_returns_none_when_no_match(monkeypatch):
    def fake_request_json(method, url, headers=None, body=None, timeout=15):
        return {"workflow_runs": []}

    monkeypatch.setattr("scripts.preview.github_api.request_json", fake_request_json)

    assert github_api.cancel_in_flight_run(REPO, "K8s Stack Preview", 205, TOKEN) is None
