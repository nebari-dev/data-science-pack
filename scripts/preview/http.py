"""Shared JSON HTTP helper for scripts/preview/*.py.

Thin wrapper over ``urllib.request`` (stdlib, no new dependency -- matches
config/jupyterhub/01-spawner.py's existing convention for this same kind of
Keycloak/GitHub/Cloudflare-style REST call). Raises HTTPRequestError with the
response body on a non-2xx status, so callers get an actual error message
instead of urllib's bare HTTPError.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HTTPRequestError(RuntimeError):
    """Raised when a JSON HTTP request returns a non-2xx status."""

    def __init__(self, method: str, url: str, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} -> HTTP {status}: {body}")


def request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: float = 15,
) -> Any:
    """Send an HTTP request, returning the parsed JSON response body.

    ``body``, if given, is JSON-encoded and sent with an
    application/json Content-Type. Returns ``{}`` for an empty (e.g. 204)
    response body.
    """
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise HTTPRequestError(method, url, exc.code, exc.read().decode("utf-8", errors="replace")) from exc

    if not raw:
        return {}
    return json.loads(raw)
