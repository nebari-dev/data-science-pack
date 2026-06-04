"""Tests for resolving JupyterLab profiles from a Keycloak role.

Public contract (chart-side):

  After a user authenticates, ``auth_state["allowed_jupyterlab_profiles"]``
  lists the profile *slugs* the user may select for ``access: keycloak``
  profiles. The slugs come from a client role on the hub's KC client:

    * configurable role name (default: ``jupyterlab-profiles``)
    * the role's ``profiles`` attribute holds the slug allow-list
    * the role must carry ``component=jupyterhub-profiles`` (a typo'd or
      unmarked role grants nothing)

  The slugs apply only when the user effectively holds the role, whether
  it was assigned directly or via a group. The role itself is created and
  assigned by the deployer in Keycloak; the chart only reads it.

  When the realm-admin path is unavailable (KC down, no permissions,
  service-account misconfigured), the user STILL logs in; they just see
  no keycloak-gated profiles. Login MUST NOT fail.

These tests drive the authenticator's public ``update_auth_model`` /
``refresh_user`` through the JupyterHub contract with the KC Admin API
mocked at the HTTP boundary (``httpfetch``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import types
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

from tornado.httpclient import HTTPClientError, HTTPRequest, HTTPResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "config" / "jupyterhub" / "00-gateway-auth.py"
spec = importlib.util.spec_from_file_location("_gateway_auth_profiles", MODULE_PATH)
ga = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ga)


HUB_CLIENT_ID = "jupyterhub-data-science-pack-nebari-data-science-pack"
HUB_CLIENT_UUID = "53008075-f97e-4efb-8b8b-03c1d13f054a"
REALM_API = "https://kc.example.test/admin/realms/nebari"
TOKEN_URL = "https://kc.example.test/realms/nebari/protocol/openid-connect/token"
SHARED_MOUNT_ROLE = "allow-group-directory-creation-role"
PROFILES_ROLE = "jupyterlab-profiles"
USER_ID = "11111111-2222-3333-4444-555555555555"


def _profiles_role_payload(profiles=("gpu", "hpc"), component="jupyterhub-profiles"):
    """Shape KC's `GET clients/{id}/roles/{name}` returns for the
    jupyterlab-profiles role carrying a slug allow-list."""
    attrs = {"profiles": list(profiles)}
    if component is not None:
        attrs["component"] = [component]
    return {
        "id": "00000000-aaaa-bbbb-cccc-000000000002",
        "name": PROFILES_ROLE,
        "composite": False,
        "clientRole": True,
        "containerId": HUB_CLIENT_UUID,
        "attributes": attrs,
    }


def _make_authenticator(**overrides):
    auth = ga.KeyCloakOAuthenticator()
    auth.token_url = TOKEN_URL
    auth.client_id = HUB_CLIENT_ID
    auth.client_secret = "sek"
    auth.realm_api_url = REALM_API
    auth.shared_mount_role_name = SHARED_MOUNT_ROLE
    for k, v in overrides.items():
        setattr(auth, k, v)
    return auth


def _http_error(code, body=b""):
    fake_req = HTTPRequest("https://kc.test")
    fake_resp = HTTPResponse(fake_req, code, buffer=BytesIO(body))
    return HTTPClientError(code, f"HTTP {code}", fake_resp)


def _dispatch(*, profiles_role=None, user_holds_profiles_role=True):
    """httpfetch side-effect covering BOTH the shared-mount path (degraded
    to no mounts) and the jupyterlab-profiles path under test."""
    role_payload = profiles_role if profiles_role is not None else _profiles_role_payload()

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        if url == TOKEN_URL and method == "POST":
            return {"access_token": "admin-bearer-xxx", "expires_in": 60}
        if url.startswith(f"{REALM_API}/clients?clientId="):
            return [{"id": HUB_CLIENT_UUID, "clientId": HUB_CLIENT_ID}]
        # shared-mount role: present but assigned to no groups -> no mounts
        if url == f"{REALM_API}/clients/{HUB_CLIENT_UUID}/roles/{SHARED_MOUNT_ROLE}":
            return {
                "id": "shared", "name": SHARED_MOUNT_ROLE, "clientRole": True,
                "attributes": {
                    "component": ["shared-directory"],
                    "scopes": ["write:shared-mount"],
                },
            }
        if url == f"{REALM_API}/clients/{HUB_CLIENT_UUID}/roles/{SHARED_MOUNT_ROLE}/groups":
            return []
        # jupyterlab-profiles role
        if url == f"{REALM_API}/clients/{HUB_CLIENT_UUID}/roles/{PROFILES_ROLE}":
            if role_payload is _MISSING:
                raise _http_error(404, b'{"error":"role not found"}')
            return role_payload
        # user's effective client roles
        if url == (
            f"{REALM_API}/users/{USER_ID}/role-mappings/clients/"
            f"{HUB_CLIENT_UUID}/composite"
        ):
            return [role_payload] if user_holds_profiles_role else []
        raise AssertionError(f"unexpected httpfetch call: {method} {url}")

    return fetch


_MISSING = object()


def _auth_model_for(user_groups, sub=USER_ID):
    return {
        "name": "alice",
        "admin": False,
        "auth_state": {
            "access_token": "user-at",
            "refresh_token": "user-rt",
            "id_token": "user-id",
            "token_response": {},
            "oauth_user": {
                "sub": sub,
                "preferred_username": "alice",
                "groups": user_groups,
            },
        },
    }


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tracer bullet: role profiles attribute -> allowed_jupyterlab_profiles
# ---------------------------------------------------------------------------

def test_allowed_profiles_is_role_attribute_when_user_holds_role():
    """User holds the jupyterlab-profiles role; the role's ``profiles``
    attribute (slugs) becomes auth_state['allowed_jupyterlab_profiles']."""
    auth = _make_authenticator()
    auth.httpfetch = AsyncMock(side_effect=_dispatch())
    am = _auth_model_for(user_groups=["/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["allowed_jupyterlab_profiles"] == ["gpu", "hpc"]


def test_allowed_profiles_empty_when_user_does_not_hold_role():
    """The role exists with a slug list, but the user is not assigned it
    (directly or via a group): they get no keycloak-gated profiles."""
    auth = _make_authenticator()
    auth.httpfetch = AsyncMock(side_effect=_dispatch(user_holds_profiles_role=False))
    am = _auth_model_for(user_groups=["/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["allowed_jupyterlab_profiles"] == []


def test_allowed_profiles_empty_when_role_missing_component_marker():
    """A role without ``component=jupyterhub-profiles`` is ignored even if
    it has a ``profiles`` attribute: the marker is required so an unrelated
    role can't accidentally grant profiles."""
    auth = _make_authenticator()
    auth.httpfetch = AsyncMock(side_effect=_dispatch(
        profiles_role=_profiles_role_payload(component=None),
    ))
    am = _auth_model_for(user_groups=["/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["allowed_jupyterlab_profiles"] == []


def test_allowed_profiles_empty_when_role_absent():
    """No jupyterlab-profiles role created yet (404): login still succeeds,
    the user simply sees no keycloak-gated profiles."""
    auth = _make_authenticator()
    auth.httpfetch = AsyncMock(side_effect=_dispatch(profiles_role=_MISSING))
    am = _auth_model_for(user_groups=["/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["allowed_jupyterlab_profiles"] == []
    assert out["name"] == "alice"


def test_allowed_profiles_empty_when_user_has_no_sub():
    """Without a KC user id (``sub``) the membership check can't run, so no
    profiles are granted (fail closed rather than read the role for nobody)."""
    auth = _make_authenticator()
    auth.httpfetch = AsyncMock(side_effect=_dispatch())
    am = _auth_model_for(user_groups=["/data"], sub=None)

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["allowed_jupyterlab_profiles"] == []


def test_admin_api_5xx_does_not_break_login():
    """If the KC Admin API is unreachable / 5xx while resolving profiles,
    the user must still log in; allowed_jupyterlab_profiles is []."""
    auth = _make_authenticator()

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        if url == TOKEN_URL:
            return {"access_token": "admin-bearer-xxx"}
        raise _http_error(503, b'{"error":"upstream"}')

    auth.httpfetch = AsyncMock(side_effect=fetch)
    am = _auth_model_for(user_groups=["/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["allowed_jupyterlab_profiles"] == []
    assert out["name"] == "alice"


def test_realm_api_url_empty_skips_profiles_entirely():
    """RBAC disabled (no realm_api_url): update_auth_model must not call the
    Admin API and must not set the key; the spawner then shows no
    keycloak-gated profiles (fail closed)."""
    auth = _make_authenticator(realm_api_url="")
    auth.httpfetch = AsyncMock(side_effect=AssertionError(
        "httpfetch must not be called when realm_api_url is empty"
    ))
    am = _auth_model_for(user_groups=["/data"])

    out = _run(auth.update_auth_model(am))

    assert "allowed_jupyterlab_profiles" not in out["auth_state"]


# ---------------------------------------------------------------------------
# refresh_user re-runs the resolution (KC role changes take effect mid-session)
# ---------------------------------------------------------------------------

class _FakeUser:
    def __init__(self, name, auth_state):
        self.name = name
        self._auth_state = auth_state

    async def get_auth_state(self):
        return self._auth_state


def test_refresh_user_recomputes_allowed_jupyterlab_profiles():
    """An admin grants the user the jupyterlab-profiles role mid-session.
    The next refresh_user cycle must re-resolve
    auth_state['allowed_jupyterlab_profiles'] so the next spawn sees the
    new profiles (no force-relogin needed)."""
    auth = _make_authenticator()
    user = _FakeUser("alice", {
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "id_token": "old-id",
        "oauth_user": {"sub": USER_ID, "preferred_username": "alice", "groups": ["/data"]},
    })
    base = _dispatch()

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        if url == TOKEN_URL and method == "POST" and "refresh_token" in (body or ""):
            return {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "id_token": "new-id",
                "expires_in": 300,
            }
        return await base(url, method=method, headers=headers, body=body)

    auth.httpfetch = AsyncMock(side_effect=fetch)

    result = _run(auth.refresh_user(user))

    assert isinstance(result, dict)
    new_state = result["auth_state"]
    assert new_state["access_token"] == "new-at", "token rotation still works"
    assert new_state["allowed_jupyterlab_profiles"] == ["gpu", "hpc"]


def test_refresh_user_skips_profiles_when_realm_api_url_empty():
    """RBAC disabled: refresh_user is a pure token refresh and does not
    write an allowed_jupyterlab_profiles key."""
    auth = _make_authenticator(realm_api_url="")
    user = _FakeUser("alice", {
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "oauth_user": {"sub": USER_ID, "preferred_username": "alice", "groups": ["/data"]},
    })

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        assert url == TOKEN_URL and "refresh_token" in (body or ""), (
            f"realm_api_url empty must mean no Admin API call; got {method} {url}"
        )
        return {"access_token": "new-at", "refresh_token": "new-rt"}

    auth.httpfetch = AsyncMock(side_effect=fetch)
    result = _run(auth.refresh_user(user))

    assert isinstance(result, dict)
    assert "allowed_jupyterlab_profiles" not in result["auth_state"]


# ---------------------------------------------------------------------------
# configure() wires the role name onto the authenticator
# ---------------------------------------------------------------------------

def _configure_cfg():
    return types.SimpleNamespace(
        JupyterHub=types.SimpleNamespace(),
        KeyCloakOAuthenticator=types.SimpleNamespace(),
        Authenticator=types.SimpleNamespace(),
    )


def test_configure_defaults_jupyterlab_profiles_role_name():
    """Deployers who don't override get the stable default role name."""
    cfg = _configure_cfg()
    ga.configure(
        cfg,
        issuer="https://kc.example/realms/nebari",
        client_id="hub-client",
        client_secret="sek",
        callback_url="https://hub.example/hub/oauth_callback",
        external_url="https://hub.example/",
        realm_api_url="https://kc.example/admin/realms/nebari",
    )
    assert cfg.KeyCloakOAuthenticator.jupyterlab_profiles_role_name == (
        "jupyterlab-profiles"
    )


def test_configure_propagates_custom_jupyterlab_profiles_role_name():
    """A deployer can rename the role without editing chart code."""
    cfg = _configure_cfg()
    ga.configure(
        cfg,
        issuer="https://kc.example/realms/nebari",
        client_id="hub-client",
        client_secret="sek",
        callback_url="https://hub.example/hub/oauth_callback",
        external_url="https://hub.example/",
        realm_api_url="https://kc.example/admin/realms/nebari",
        jupyterlab_profiles_role_name="custom-profiles",
    )
    assert cfg.KeyCloakOAuthenticator.jupyterlab_profiles_role_name == (
        "custom-profiles"
    )
