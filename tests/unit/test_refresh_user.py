"""Tests for KeyCloakOAuthenticator.refresh_user.

JupyterHub calls `refresh_user(user)` every `auth_refresh_age` seconds. The
default Authenticator.refresh_user is a no-op (returns True), so without
this override the chart's stored `auth_state.refresh_token` is never
rotated and expires after Keycloak's SSO idle timeout (~30 min by
default). nebi-envs's 3-step exchange then fails at step 1 with
`invalid_grant: Token is not active`, env list silently returns [], and
the Create-App Software Environment dropdown disappears.

These tests pin the contract for that override.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock

import pytest
from io import BytesIO

from tornado.httpclient import HTTPClientError, HTTPResponse, HTTPRequest

# 00-gateway-auth.py imports cleanly without a chart `c` config object.
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "config" / "jupyterhub" / "00-gateway-auth.py"
spec = importlib.util.spec_from_file_location("_gateway_auth", MODULE_PATH)
ga = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ga)


class FakeUser:
    """Stand-in for jupyterhub.user.User exposing the bits refresh_user reads."""

    def __init__(self, name: str, auth_state: dict):
        self.name = name
        self._auth_state = auth_state

    async def get_auth_state(self):
        return self._auth_state


def _make_authenticator() -> ga.KeyCloakOAuthenticator:
    """KC authenticator pre-wired with the URLs refresh_user needs."""
    auth = ga.KeyCloakOAuthenticator()
    auth.token_url = "https://kc.test/realms/r/protocol/openid-connect/token"
    auth.client_id = "cid"
    auth.client_secret = "sek"
    return auth


@pytest.fixture
def auth():
    return _make_authenticator()


def _run(coro):
    # `asyncio.get_event_loop()` is deprecated since 3.10 and raises in
    # 3.12+ once any other test in the same process has called
    # `asyncio.run` (which creates + closes a fresh loop, leaving the
    # thread with no current loop). `asyncio.run` is the supported
    # pattern for sync test bodies.
    return asyncio.run(coro)


def test_refresh_user_returns_updated_auth_state_on_success(auth):
    """A successful refresh-token grant must surface as a dict so JupyterHub
    persists the new tokens (and KC's rotated refresh_token) back to the DB.
    Returning True would silently drop the rotation."""
    user = FakeUser("alice", {
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "id_token": "old-id",
    })
    auth.httpfetch = AsyncMock(return_value={
        "access_token": "new-at",
        "refresh_token": "new-rt",
        "id_token": "new-id",
        "expires_in": 300,
    })

    result = _run(auth.refresh_user(user))

    assert isinstance(result, dict), (
        f"refresh_user returned {result!r}; must return a dict so JupyterHub "
        "writes the rotated refresh_token back to auth_state."
    )
    new_state = result["auth_state"]
    assert new_state["access_token"] == "new-at"
    assert new_state["refresh_token"] == "new-rt"
    assert new_state["id_token"] == "new-id"

    # httpfetch must hit the KC token endpoint with grant_type=refresh_token
    call_kwargs = auth.httpfetch.call_args.kwargs
    assert "/token" in auth.httpfetch.call_args.args[0]
    body = call_kwargs.get("body") or auth.httpfetch.call_args.args[1]
    assert "grant_type=refresh_token" in body
    assert "refresh_token=old-rt" in body


def test_refresh_user_returns_false_on_invalid_grant(auth):
    """KC returns 400 invalid_grant when the refresh_token has expired or
    been revoked. The user must be sent back through the auth flow — return
    False so JupyterHub forces a re-login on the next request."""
    user = FakeUser("alice", {"refresh_token": "expired-rt"})
    err_body = json.dumps({
        "error": "invalid_grant",
        "error_description": "Token is not active",
    }).encode()
    fake_resp = HTTPResponse(
        HTTPRequest(auth.token_url), 400, buffer=BytesIO(err_body),
    )
    auth.httpfetch = AsyncMock(
        side_effect=HTTPClientError(400, "Bad Request", fake_resp)
    )

    result = _run(auth.refresh_user(user))

    assert result is False, (
        f"got {result!r}; invalid_grant must return False so JupyterHub "
        "drops the session and re-authenticates the user."
    )


def test_refresh_user_returns_true_on_transient_error(auth):
    """Network blips, 5xx, etc. shouldn't log the user out — return True
    and let the next refresh tick try again."""
    user = FakeUser("alice", {"refresh_token": "rt"})
    auth.httpfetch = AsyncMock(
        side_effect=HTTPClientError(503, "Service Unavailable")
    )

    result = _run(auth.refresh_user(user))

    assert result is True


def test_refresh_user_returns_true_when_no_refresh_token(auth):
    """If auth_state has no refresh_token (legacy session, Envoy-era), we
    can't refresh — but we also shouldn't log the user out unprompted.
    Leave it for the next request's normal auth check to handle."""
    user = FakeUser("alice", {"access_token": "at-only"})
    auth.httpfetch = AsyncMock()

    result = _run(auth.refresh_user(user))

    assert result is True
    auth.httpfetch.assert_not_called()
