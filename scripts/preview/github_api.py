"""GitHub REST calls tunnel.py makes while cloudflared is running.

Everything the workflow itself needs from GitHub is done with `gh` in
k8s-preview.yaml; only the calls made from inside the tunnel loop live here.
"""

from __future__ import annotations

from .http import HTTPRequestError, request_json

API_ROOT = "https://api.github.com"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


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


def find_comment_id(repo: str, pr_number: int, marker: str, token: str) -> int | None:
    """Return the id of the PR comment containing ``marker``, or None."""
    comments = request_json(
        "GET", f"{API_ROOT}/repos/{repo}/issues/{pr_number}/comments?per_page=100", headers=_headers(token)
    )
    for c in comments:
        if marker in c.get("body", ""):
            return c["id"]
    return None


def update_comment(repo: str, comment_id: int, body: str, token: str) -> None:
    request_json(
        "PATCH",
        f"{API_ROOT}/repos/{repo}/issues/comments/{comment_id}",
        headers=_headers(token),
        body={"body": body},
    )
