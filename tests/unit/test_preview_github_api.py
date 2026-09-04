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


# --- CLI (main) ---------------------------------------------------------------


def test_main_ensure_label_exists_calls_through(monkeypatch):
    called = []
    monkeypatch.setattr(github_api, "ensure_label_exists", lambda *a: called.append(a))

    rc = github_api.main([
        "github_api", "ensure-label-exists", "--repo", REPO, "--token", TOKEN,
        "--name", "extend-preview", "--color", "BFD4F2", "--description", "desc",
    ])

    assert rc == 0
    assert called == [(REPO, "extend-preview", "BFD4F2", "desc", TOKEN)]


def test_main_create_and_activate_writes_deployment_id_env(monkeypatch, tmp_path):
    monkeypatch.setattr(github_api, "create_deployment", lambda *a, **k: 999)
    set_calls = []
    monkeypatch.setattr(github_api, "set_deployment_status", lambda *a, **k: set_calls.append((a, k)))
    env_file = tmp_path / "env"
    env_file.write_text("")
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    rc = github_api.main([
        "github_api", "create-and-activate", "--repo", REPO, "--token", TOKEN,
        "--ref", "abc123", "--environment", "pr-205-preview", "--task", "deploy:preview",
        "--description", "K8s stack preview", "--environment-url", "https://x", "--log-url", "https://y",
        "--status-description", "Live for 20 minutes",
    ])

    assert rc == 0
    assert "DEPLOYMENT_ID=999" in env_file.read_text()
    assert set_calls[0][0] == (REPO, 999, "success", TOKEN)
    assert set_calls[0][1] == {"environment_url": "https://x", "log_url": "https://y", "description": "Live for 20 minutes"}


def test_main_mark_inactive_noops_when_deployment_id_missing(monkeypatch):
    called = []
    monkeypatch.setattr(github_api, "mark_deployment_inactive", lambda *a, **k: called.append(a))

    rc = github_api.main([
        "github_api", "mark-inactive", "--repo", REPO, "--token", TOKEN,
        "--deployment-id", "", "--description", "unused",
    ])

    assert rc == 0
    assert called == []


def test_main_mark_inactive_calls_through_when_present(monkeypatch):
    called = []
    monkeypatch.setattr(github_api, "mark_deployment_inactive", lambda *a, **k: called.append((a, k)))

    rc = github_api.main([
        "github_api", "mark-inactive", "--repo", REPO, "--token", TOKEN,
        "--deployment-id", "999", "--description", "Preview expired",
    ])

    assert rc == 0
    assert called == [((REPO, 999, TOKEN), {"description": "Preview expired"})]


def test_main_mark_latest_inactive_noops_when_none_found(monkeypatch):
    monkeypatch.setattr(github_api, "find_latest_deployment_id", lambda *a: None)
    called = []
    monkeypatch.setattr(github_api, "mark_deployment_inactive", lambda *a, **k: called.append(a))

    rc = github_api.main([
        "github_api", "mark-latest-inactive", "--repo", REPO, "--token", TOKEN,
        "--environment", "pr-205-preview", "--description", "stopped",
    ])

    assert rc == 0
    assert called == []


def test_main_mark_latest_inactive_marks_when_found(monkeypatch):
    monkeypatch.setattr(github_api, "find_latest_deployment_id", lambda *a: 42)
    called = []
    monkeypatch.setattr(github_api, "mark_deployment_inactive", lambda *a, **k: called.append((a, k)))

    rc = github_api.main([
        "github_api", "mark-latest-inactive", "--repo", REPO, "--token", TOKEN,
        "--environment", "pr-205-preview", "--description", "stopped",
    ])

    assert rc == 0
    assert called == [((REPO, 42, TOKEN), {"description": "stopped"})]


def test_main_cancel_in_flight_run_calls_through(monkeypatch):
    called = []
    monkeypatch.setattr(github_api, "cancel_in_flight_run", lambda *a: called.append(a) or 3)

    rc = github_api.main([
        "github_api", "cancel-in-flight-run", "--repo", REPO, "--token", TOKEN,
        "--workflow-name", "K8s Stack Preview", "--pr", "205",
    ])

    assert rc == 0
    assert called == [(REPO, "K8s Stack Preview", 205, TOKEN)]


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


def test_update_comment_patches_the_comment_body(monkeypatch):
    calls, _ = _capture(monkeypatch)

    github_api.update_comment(REPO, 222, "new body", TOKEN)

    assert calls[0]["method"] == "PATCH"
    assert calls[0]["url"] == f"https://api.github.com/repos/{REPO}/issues/comments/222"
    assert calls[0]["body"] == {"body": "new body"}
