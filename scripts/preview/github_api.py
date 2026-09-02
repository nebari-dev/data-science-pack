"""GitHub REST API calls shared by the preview-deploy scripts.

All calls go through ``http.request_json`` (stdlib ``urllib.request``, no new
dependency) rather than shelling out to the ``gh`` CLI, so the actual request
being made is a plain, testable function call.
"""

from __future__ import annotations

from .http import HTTPRequestError, request_json

API_ROOT = "https://api.github.com"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


# --- labels -------------------------------------------------------------


def list_labels(repo: str, pr_number: int, token: str) -> list[str]:
    """Return the names of every label currently on the PR."""
    result = request_json(
        "GET", f"{API_ROOT}/repos/{repo}/issues/{pr_number}/labels", headers=_headers(token)
    )
    return [item["name"] for item in result]


def delete_label(repo: str, pr_number: int, label: str, token: str) -> None:
    """Remove ``label`` from the PR. A no-op if it's already gone."""
    try:
        request_json(
            "DELETE",
            f"{API_ROOT}/repos/{repo}/issues/{pr_number}/labels/{label}",
            headers=_headers(token),
        )
    except HTTPRequestError as exc:
        if exc.status != 404:
            raise


def ensure_label_exists(repo: str, name: str, color: str, description: str, token: str) -> None:
    """Create the repo label ``name`` if it doesn't already exist."""
    try:
        request_json(
            "POST",
            f"{API_ROOT}/repos/{repo}/labels",
            headers=_headers(token),
            body={"name": name, "color": color, "description": description},
        )
    except HTTPRequestError as exc:
        if exc.status != 422:
            raise


# --- deployments ----------------------------------------------------------


def create_deployment(
    repo: str,
    ref: str,
    environment: str,
    token: str,
    task: str = "deploy:preview",
    description: str = "",
) -> int:
    """Create a GitHub Deployment and return its id."""
    result = request_json(
        "POST",
        f"{API_ROOT}/repos/{repo}/deployments",
        headers=_headers(token),
        body={
            "ref": ref,
            "environment": environment,
            "task": task,
            "auto_merge": False,
            "transient_environment": True,
            "production_environment": False,
            "required_contexts": [],
            "description": description,
        },
    )
    return result["id"]


def set_deployment_status(
    repo: str,
    deployment_id: int,
    state: str,
    token: str,
    environment_url: str | None = None,
    log_url: str | None = None,
    description: str | None = None,
) -> None:
    """Post a new status onto a deployment. Optional fields are omitted, not sent empty."""
    body: dict[str, str] = {"state": state}
    if environment_url is not None:
        body["environment_url"] = environment_url
    if log_url is not None:
        body["log_url"] = log_url
    if description is not None:
        body["description"] = description
    request_json(
        "POST",
        f"{API_ROOT}/repos/{repo}/deployments/{deployment_id}/statuses",
        headers=_headers(token),
        body=body,
    )


def mark_deployment_inactive(repo: str, deployment_id: int, token: str, description: str = "") -> None:
    set_deployment_status(repo, deployment_id, "inactive", token, description=description)


def find_latest_deployment_id(repo: str, environment: str, token: str) -> int | None:
    """Return the most recent deployment id for ``environment``, or None."""
    result = request_json(
        "GET",
        f"{API_ROOT}/repos/{repo}/deployments?environment={environment}&per_page=1",
        headers=_headers(token),
    )
    return result[0]["id"] if result else None


# --- workflow runs ----------------------------------------------------------


def cancel_in_flight_run(repo: str, workflow_name: str, pr_number: int, token: str) -> int | None:
    """Cancel the in-progress run of ``workflow_name`` for this PR, if any.

    Returns the cancelled run's id, or None if no matching run was running.
    """
    result = request_json(
        "GET",
        f"{API_ROOT}/repos/{repo}/actions/runs?event=pull_request&status=in_progress",
        headers=_headers(token),
    )
    for run in result.get("workflow_runs", []):
        if run["name"] != workflow_name:
            continue
        if any(pr["number"] == pr_number for pr in run.get("pull_requests", [])):
            request_json(
                "POST",
                f"{API_ROOT}/repos/{repo}/actions/runs/{run['id']}/cancel",
                headers=_headers(token),
            )
            return run["id"]
    return None
