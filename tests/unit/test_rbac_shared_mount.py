"""Tests for role-gated shared-directory mounting.

Public contract (chart-side):

  After a user authenticates, ``auth_state["groups_with_permission_to_mount"]``
  lists exactly the KC groups that BOTH (a) the user belongs to AND (b)
  hold a specific role on the hub's KC client. The chart's spawner reads
  this list to decide which /shared/<group> dirs to mount — being in a
  KC group is necessary but not sufficient, the group also has to be
  granted the mount permission.

  The role is identified by:
    * configurable role name (default: ``shared-directory-mount``)
    * fixed attribute pair ``component=shared-directory``,
      ``scopes=write:shared-mount``

  When the realm-admin path is unavailable (KC down, no permissions,
  service-account misconfigured), the user STILL logs in — they just
  see no shared mounts. Login MUST NOT fail because of realm-admin
  problems.

These tests drive the authenticator's public ``update_auth_model``
through the JupyterHub contract with the KC Admin API mocked at the HTTP
boundary (``httpfetch``). They do not rely on internal helpers — any
refactor that preserves the observable auth_model contents passes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock

import pytest
from tornado.httpclient import HTTPClientError, HTTPRequest, HTTPResponse

# 00-gateway-auth.py imports cleanly without a chart `c` config object.
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "config" / "jupyterhub" / "00-gateway-auth.py"
spec = importlib.util.spec_from_file_location("_gateway_auth", MODULE_PATH)
ga = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ga)


HUB_CLIENT_ID = "jupyterhub-data-science-pack-nebari-data-science-pack"
HUB_CLIENT_UUID = "53008075-f97e-4efb-8b8b-03c1d13f054a"
REALM_API = "https://kc.example.test/admin/realms/nebari"
TOKEN_URL = "https://kc.example.test/realms/nebari/protocol/openid-connect/token"
SHARED_MOUNT_ROLE = "allow-group-directory-creation-role"


def _kc_role_payload(name=SHARED_MOUNT_ROLE):
    """The exact shape KC's `GET clients/{id}/roles/{name}` returns for a
    rich role with the shared-directory component + write:shared-mount
    scope. Captured from a real kcadm dump."""
    return {
        "id": "00000000-aaaa-bbbb-cccc-000000000001",
        "name": name,
        "description": "",
        "composite": False,
        "clientRole": True,
        "containerId": HUB_CLIENT_UUID,
        "attributes": {
            "component": ["shared-directory"],
            "scopes": ["write:shared-mount"],
        },
    }


def _make_authenticator(**overrides):
    auth = ga.KeyCloakOAuthenticator()
    auth.token_url = TOKEN_URL
    auth.client_id = HUB_CLIENT_ID
    auth.client_secret = "sek"
    # New traitlets introduced by this PR. Test sets them like configure() would.
    auth.realm_api_url = REALM_API
    auth.shared_mount_role_name = SHARED_MOUNT_ROLE
    for k, v in overrides.items():
        setattr(auth, k, v)
    return auth


def _kc_admin_api_dispatch(role_groups, role_payload=None):
    """Build an httpfetch side-effect that mimics KC's HTTP responses.

    role_groups -- list of {"name":"admin","path":"/admin"} dicts the
                   role assigns to.
    role_payload -- override role JSON (use to test missing attrs).
    """
    payload = role_payload if role_payload is not None else _kc_role_payload()

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        # client_credentials grant
        if url == TOKEN_URL and method == "POST":
            assert "grant_type=client_credentials" in body, (
                f"expected client_credentials grant for realm-admin token, got body={body!r}"
            )
            return {"access_token": "admin-bearer-xxx", "expires_in": 60}
        # clients lookup by clientId
        if url.startswith(f"{REALM_API}/clients?clientId="):
            assert HUB_CLIENT_ID in url
            return [{"id": HUB_CLIENT_UUID, "clientId": HUB_CLIENT_ID}]
        # role fetch
        if url == f"{REALM_API}/clients/{HUB_CLIENT_UUID}/roles/{SHARED_MOUNT_ROLE}":
            return payload
        # groups holding role
        if url == f"{REALM_API}/clients/{HUB_CLIENT_UUID}/roles/{SHARED_MOUNT_ROLE}/groups":
            return role_groups
        raise AssertionError(f"unexpected httpfetch call: {method} {url}")

    return fetch


def _auth_model_for(user_groups):
    """A baseline auth_model dict the way oauthenticator builds one."""
    return {
        "name": "alice",
        "admin": False,
        "auth_state": {
            "access_token": "user-at",
            "refresh_token": "user-rt",
            "id_token": "user-id",
            "scope": "openid groups",
            "token_response": {},
            "oauth_user": {
                "preferred_username": "alice",
                "groups": user_groups,
            },
        },
    }


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tracer bullet: end-to-end role → groups_with_permission_to_mount
# ---------------------------------------------------------------------------

def test_groups_with_permission_to_mount_is_user_groups_intersect_role_groups():
    """User in [/admin, /data]. Role 'shared-directory-mount' assigned only
    to /admin. Result should expose ONLY /admin in
    auth_state['groups_with_permission_to_mount'] — the intersection,
    not the union, and not the user's full group list.

    This is the saga's core requirement: being in 10 KC groups does NOT
    mean mounting 10 shared dirs. Only the groups that explicitly hold
    the shared-directory role get mounted."""
    auth = _make_authenticator()
    auth.httpfetch = AsyncMock(side_effect=_kc_admin_api_dispatch(
        role_groups=[{"name": "admin", "path": "/admin"}],
    ))
    am = _auth_model_for(user_groups=["/admin", "/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["groups_with_permission_to_mount"] == ["/admin"], (
        f"expected only /admin (intersection of user groups and role-holders); "
        f"got {out['auth_state'].get('groups_with_permission_to_mount')!r}"
    )


# ---------------------------------------------------------------------------
# Spawner consumes the filtered set
# ---------------------------------------------------------------------------

def _load_spawner_module(custom=None):
    """Load 01-spawner.py with a z2jh stub; returns the module."""
    z = types.ModuleType("z2jh")
    cfg = {
        "custom.shared-storage-enabled": True,
        "custom.shared-storage-groups": [],
        "custom.shared-storage-mount-prefix": "/shared",
        "custom.storage-capacity": "20Gi",
        **(custom or {}),
    }
    z.get_config = lambda k, default=None: cfg.get(k, default)
    sys.modules["z2jh"] = z
    sp_spec = importlib.util.spec_from_file_location(
        "_spawner", REPO_ROOT / "config" / "jupyterhub" / "01-spawner.py",
    )
    sp = importlib.util.module_from_spec(sp_spec)
    sp.__dict__["c"] = _ChartConfig()  # bare FakeConfig stand-in
    sp_spec.loader.exec_module(sp)
    return sp


class _ChartConfig:
    """Minimal stand-in for JupyterHub's `c`. Records assignments; serves
    fresh sub-namespaces on attribute access so `c.KubeSpawner.x = y`
    works without raising. Real value of `c.KubeSpawner.volumes` etc. is
    irrelevant to these tests — they only inspect what the SPAWNER FUNCTION
    returns or stores on its argument."""

    def __getattr__(self, name):
        ns = types.SimpleNamespace(
            init_containers=[], volumes=[], volume_mounts=[],
            environment={}, extra_pod_config={},
        )
        self.__dict__[name] = ns
        return ns


def test_spawner_uses_groups_with_permission_to_mount_when_present():
    """The spawner must prefer auth_state['groups_with_permission_to_mount']
    over the broader auth_state['groups'] list. Being in 10 KC groups
    does NOT mean mounting 10 shared dirs — only the groups with the
    shared-mount role get mounted."""
    sp = _load_spawner_module()
    auth_state = {
        "groups": ["/admin", "/data", "/random"],
        "groups_with_permission_to_mount": ["/admin"],
    }
    groups = sp._get_user_groups(auth_state)
    assert groups == ["admin"], (
        f"expected only [admin] (the role-gated subset); got {groups!r}. "
        "auth_state['groups'] should not leak into the mount decision."
    )


def test_spawner_falls_back_to_groups_when_filter_not_set():
    """When auth_state has no 'groups_with_permission_to_mount' key
    (RBAC disabled, legacy auth_state, KC Admin API unreachable on this
    login), fall back to auth_state['groups'] — the chart still works
    on clusters that haven't deployed the RBAC bootstrap yet."""
    sp = _load_spawner_module()
    auth_state = {"groups": ["/admin", "/data"]}
    groups = sp._get_user_groups(auth_state)
    assert sorted(groups) == ["admin", "data"]


def test_spawner_filter_normalises_paths_and_dedups():
    """The filtered set should still go through Path(g).name normalisation
    and dedup — `/projects/myproj` collapses to `myproj`, duplicates
    drop. Pre-existing contract, must survive."""
    sp = _load_spawner_module()
    auth_state = {
        "groups_with_permission_to_mount": [
            "/projects/myproj", "/projects/myproj", "/data",
        ],
    }
    assert sorted(sp._get_user_groups(auth_state)) == ["data", "myproj"]


# ---------------------------------------------------------------------------
# KC Admin API failures degrade — login must still succeed
# ---------------------------------------------------------------------------

def _http_error(code, body=b""):
    from io import BytesIO
    fake_req = HTTPRequest("https://kc.test")
    fake_resp = HTTPResponse(fake_req, code, buffer=BytesIO(body))
    return HTTPClientError(code, f"HTTP {code}", fake_resp)


def test_admin_api_5xx_does_not_break_login():
    """If KC Admin API is unreachable / 5xx, the user must still log in.
    auth_state['groups_with_permission_to_mount'] is set to [] and a
    WARNING is logged. update_auth_model MUST NOT raise."""
    auth = _make_authenticator()

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        if url == TOKEN_URL:
            return {"access_token": "admin-bearer-xxx"}
        raise _http_error(503, b'{"error":"upstream"}')

    auth.httpfetch = AsyncMock(side_effect=fetch)
    am = _auth_model_for(user_groups=["/admin", "/data"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["groups_with_permission_to_mount"] == []
    assert out["name"] == "alice"  # login itself succeeded


def test_oauth_client_not_a_service_account_does_not_break_login():
    """Realistic failure mode: the OAuth client doesn't have
    serviceAccountsEnabled=true (operator hasn't been updated) or lacks
    realm-management roles. client_credentials grant 401s. Login still
    succeeds; filter is empty; WARNING logged."""
    auth = _make_authenticator()

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        if url == TOKEN_URL:
            raise _http_error(401, b'{"error":"unauthorized_client"}')
        raise AssertionError("should not have reached Admin API without a token")

    auth.httpfetch = AsyncMock(side_effect=fetch)
    am = _auth_model_for(user_groups=["/admin"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["groups_with_permission_to_mount"] == []
    assert out["name"] == "alice"


def test_role_missing_attributes_returns_empty_filter():
    """KC role exists but is missing the component / scopes attributes
    (deployer created the role manually without the right metadata).
    Treat as 'no groups can mount' — safer to grant nothing than to
    grant all groups."""
    auth = _make_authenticator()
    bad_payload = dict(_kc_role_payload())
    bad_payload["attributes"] = {}
    auth.httpfetch = AsyncMock(side_effect=_kc_admin_api_dispatch(
        role_groups=[{"name": "admin", "path": "/admin"}],
        role_payload=bad_payload,
    ))
    am = _auth_model_for(user_groups=["/admin"])

    out = _run(auth.update_auth_model(am))

    assert out["auth_state"]["groups_with_permission_to_mount"] == []


def test_realm_api_url_empty_skips_filter_entirely():
    """When realm_api_url is empty (chart deployed with rbac disabled,
    or operator hasn't surfaced the URL yet), update_auth_model must
    not call the Admin API at all and must not set the filter key —
    spawner's fallback to auth_state['groups'] kicks in."""
    auth = _make_authenticator(realm_api_url="")
    auth.httpfetch = AsyncMock(side_effect=AssertionError(
        "httpfetch must not be called when realm_api_url is empty"
    ))
    am = _auth_model_for(user_groups=["/admin"])

    out = _run(auth.update_auth_model(am))

    assert "groups_with_permission_to_mount" not in out["auth_state"], (
        f"expected key absent when RBAC disabled; got "
        f"{out['auth_state'].get('groups_with_permission_to_mount')!r}"
    )


# ---------------------------------------------------------------------------
# refresh_user re-runs the filter (KC role changes take effect mid-session)
# ---------------------------------------------------------------------------

class _FakeUser:
    def __init__(self, name, auth_state):
        self.name = name
        self._auth_state = auth_state

    async def get_auth_state(self):
        return self._auth_state


def test_refresh_user_recomputes_groups_with_permission_to_mount():
    """A user's KC role assignments change mid-session (admin adds them
    to /developer and grants the developer group the shared-mount role).
    The next refresh_user cycle (default every 240s) must re-evaluate
    auth_state['groups_with_permission_to_mount'] so the *next* spawn
    in this session picks up the new mount — no force-relogin required.
    """
    auth = _make_authenticator()
    # auth_state before refresh: user already had /admin, no developer yet.
    user = _FakeUser("alice", {
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "id_token": "old-id",
        "oauth_user": {
            "preferred_username": "alice",
            "groups": ["/admin", "/developer"],  # KC just added developer
        },
    })
    # KC state at refresh time: now /developer ALSO holds the role.
    role_groups_at_refresh = [
        {"name": "admin", "path": "/admin"},
        {"name": "developer", "path": "/developer"},
    ]
    base_admin_dispatch = _kc_admin_api_dispatch(role_groups=role_groups_at_refresh)

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        # refresh_token grant
        if url == TOKEN_URL and method == "POST" and "refresh_token" in (body or ""):
            return {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "id_token": "new-id",
                "expires_in": 300,
            }
        # otherwise it's the admin-token + Admin API path
        return await base_admin_dispatch(url, method=method, headers=headers, body=body)

    auth.httpfetch = AsyncMock(side_effect=fetch)

    result = _run(auth.refresh_user(user))

    assert isinstance(result, dict), f"refresh_user must return a dict; got {result!r}"
    new_state = result["auth_state"]
    assert new_state["access_token"] == "new-at", "token rotation still works"
    assert new_state["groups_with_permission_to_mount"] == ["/admin", "/developer"], (
        f"refresh_user must re-evaluate the role-gated filter against current "
        f"KC state; got {new_state.get('groups_with_permission_to_mount')!r}"
    )


def test_refresh_user_skips_rbac_when_realm_api_url_empty():
    """RBAC disabled at the chart level: refresh_user is a pure token
    refresh, does not touch the Admin API, and does NOT write a
    groups_with_permission_to_mount key into auth_state."""
    auth = _make_authenticator(realm_api_url="")
    user = _FakeUser("alice", {
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "oauth_user": {"preferred_username": "alice", "groups": ["/admin"]},
    })

    async def fetch(url, method="GET", headers=None, body=None, **kw):
        # only the refresh-token grant should fire
        assert url == TOKEN_URL and "refresh_token" in (body or ""), (
            f"realm_api_url empty must mean no Admin API call; got {method} {url}"
        )
        return {"access_token": "new-at", "refresh_token": "new-rt"}

    auth.httpfetch = AsyncMock(side_effect=fetch)
    result = _run(auth.refresh_user(user))

    assert isinstance(result, dict)
    assert "groups_with_permission_to_mount" not in result["auth_state"]


# ---------------------------------------------------------------------------
# configure() wires the chart's RBAC values onto the authenticator
# ---------------------------------------------------------------------------

def test_configure_propagates_realm_api_url_and_role_name():
    """The chart's configure() entrypoint must accept realm_api_url +
    shared_mount_role_name and set them so the authenticator's
    update_auth_model knows where to call. Default role name
    ``allow-group-directory-creation-role`` is the stable identifier
    realm-setup tooling expects."""
    c = ga.KeyCloakOAuthenticator.kc_config  # may be stale from earlier tests
    # Build a FakeConfig-like object: namespace per traitlets class.
    cfg = types.SimpleNamespace(
        JupyterHub=types.SimpleNamespace(),
        KeyCloakOAuthenticator=types.SimpleNamespace(),
        Authenticator=types.SimpleNamespace(),
    )
    ga.configure(
        cfg,
        issuer="https://kc.example/realms/nebari",
        client_id="hub-client",
        client_secret="sek",
        callback_url="https://hub.example/hub/oauth_callback",
        external_url="https://hub.example/",
        realm_api_url="https://kc.example/admin/realms/nebari",
    )

    assert cfg.KeyCloakOAuthenticator.realm_api_url == (
        "https://kc.example/admin/realms/nebari"
    )
    assert cfg.KeyCloakOAuthenticator.shared_mount_role_name == (
        "allow-group-directory-creation-role"
    )


def test_configure_defaults_rbac_off_when_realm_api_url_unset():
    """Backwards-compat: configure() callers that don't pass realm_api_url
    get RBAC disabled — the chart works for deployers who haven't
    enabled the bootstrap Job yet. update_auth_model returns without
    touching the Admin API."""
    cfg = types.SimpleNamespace(
        JupyterHub=types.SimpleNamespace(),
        KeyCloakOAuthenticator=types.SimpleNamespace(),
        Authenticator=types.SimpleNamespace(),
    )
    ga.configure(
        cfg,
        issuer="https://kc.example/realms/nebari",
        client_id="hub-client",
        client_secret="sek",
        callback_url="https://hub.example/hub/oauth_callback",
        external_url="https://hub.example/",
    )
    # Empty string is the documented "RBAC disabled" sentinel.
    assert cfg.KeyCloakOAuthenticator.realm_api_url == ""
