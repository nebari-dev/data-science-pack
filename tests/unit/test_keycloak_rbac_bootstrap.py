"""Unit tests for the Keycloak RBAC bootstrap orchestrator.

These tests pin the high-level control flow of :func:`run` against an
in-memory fake of the KC Admin API. They are fast (no network) and
catch logic regressions early in dev / pre-push.

The corresponding integration tests in ``tests/integration/`` exercise
the same orchestrator against a real Keycloak — those catch KC version
skew, JSON quirks, and transport bugs the fake will never see. Unit
tests cover orchestration; integration tests cover reality. Both are
required.

Tests mock at the HTTP-method boundary (:py:meth:`KCAdmin._request`)
rather than at ``urllib.request``: assertions stay focused on which KC
endpoints are hit and with what bodies, and survive any change to how
:py:meth:`KCAdmin._request` is implemented.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "files" / "keycloak_rbac_bootstrap.py"
spec = importlib.util.spec_from_file_location("rbac_bootstrap", SCRIPT_PATH)
rbac = importlib.util.module_from_spec(spec)
sys.modules["rbac_bootstrap"] = rbac
spec.loader.exec_module(rbac)


REALM = "nebari"
HUB_CLIENT_ID = "jupyterhub-hub-client"
HUB_UUID = "00000000-0000-0000-0000-000000000001"
SA_USER_ID = "00000000-0000-0000-0000-00000000aaaa"
RM_UUID = "00000000-0000-0000-0000-00000000bbbb"
ROLE_NAME = "allow-group-directory-creation-role"
ROLE_ID = "00000000-0000-0000-0000-00000000cccc"
ADMIN_GROUP_PATH = "/admin"
ADMIN_GROUP_ID = "00000000-0000-0000-0000-00000000dddd"


def http_404() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://kc.test/", code=404, msg="Not Found", hdrs=None,
        fp=BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# Fixture: a KCAdmin with _request fully mocked
# ---------------------------------------------------------------------------

def make_kc(request_side_effect):
    """Build a KCAdmin instance with ``_request`` replaced by a
    side-effect that takes ``(method, path, body=None, accept_409=False)``
    and returns the parsed response."""
    kc = rbac.KCAdmin("http://kc.test", "admin-password")
    kc._request = MagicMock(side_effect=request_side_effect)
    return kc


def _fake_realm(state):
    """Return a callable suitable for ``KCAdmin._request`` side_effect
    that serves the given in-memory realm state. Subsequent mutating
    calls update ``state`` so the same fixture supports idempotency
    tests across two passes."""

    def respond(method, path, *, body=None, accept_409=False):
        # GET /<realm>/client-scopes
        if method == "GET" and path == f"/{REALM}/client-scopes":
            return state["client_scopes"]
        # GET /<realm>/client-scopes/<id>/protocol-mappers/models
        if method == "GET" and path.endswith("/protocol-mappers/models"):
            scope_id = path.split("/")[-3]
            return state["mappers"].get(scope_id, [])
        # POST /<realm>/client-scopes/<id>/protocol-mappers/models
        if method == "POST" and path.endswith("/protocol-mappers/models"):
            scope_id = path.split("/")[-3]
            state["mappers"].setdefault(scope_id, []).append(body)
            return None
        # PUT /<realm>/client-scopes/<id>/protocol-mappers/models/<mid>
        if method == "PUT" and "/protocol-mappers/models/" in path:
            parts = path.split("/")
            scope_id = parts[3]
            mapper_id = parts[-1]
            for m in state["mappers"].get(scope_id, []):
                if m.get("id") == mapper_id:
                    m.clear()
                    m.update(body)
                    return None
            raise AssertionError(f"mapper {mapper_id} not found on scope {scope_id}")
        # GET /<realm>/clients?clientId=<id>
        if method == "GET" and path.startswith(f"/{REALM}/clients?clientId="):
            cid = path.rsplit("=", 1)[1]
            return [c for c in state["clients"] if c["clientId"] == cid]
        # GET /<realm>/clients/<uuid>
        if (
            method == "GET"
            and path.startswith(f"/{REALM}/clients/")
            and "/roles" not in path
            and "/service-account-user" not in path
        ):
            uuid = path.split("/")[-1]
            for c in state["clients"]:
                if c["id"] == uuid:
                    return dict(c)
            raise http_404()
        # PUT /<realm>/clients/<uuid>  (full-object update)
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            for c in state["clients"]:
                if c["id"] == HUB_UUID:
                    c.update(body)
                    return None
        # GET /<realm>/clients/<uuid>/service-account-user
        if method == "GET" and path.endswith("/service-account-user"):
            sa = state.get("service_account_user")
            if sa is None:
                raise http_404()
            return sa
        # GET /<realm>/clients/<uuid>/roles/<name>
        if (
            method == "GET"
            and "/clients/" in path
            and "/roles/" in path
            and "/groups" not in path
        ):
            client_uuid = path.split("/")[3]
            role_name = path.split("/")[-1]
            roles = state["client_roles"].get(client_uuid, {})
            if role_name not in roles:
                raise http_404()
            return dict(roles[role_name])
        # POST /<realm>/clients/<uuid>/roles  (create)
        if method == "POST" and path == f"/{REALM}/clients/{HUB_UUID}/roles":
            state["client_roles"].setdefault(HUB_UUID, {})[body["name"]] = {
                "id": ROLE_ID,
                "name": body["name"],
                "attributes": body.get("attributes", {}),
            }
            return None
        # PUT role  (attribute reconcile)
        if (
            method == "PUT"
            and "/clients/" in path
            and "/roles/" in path
        ):
            role_name = path.split("/")[-1]
            client_uuid = path.split("/")[3]
            state["client_roles"][client_uuid][role_name] = dict(body)
            return None
        # GET /<realm>/users/<sa>/role-mappings/clients/<rm>
        if (
            method == "GET"
            and "/users/" in path
            and "/role-mappings/clients/" in path
        ):
            return list(state.get("sa_role_bindings", []))
        # POST role bindings on SA
        if (
            method == "POST"
            and "/users/" in path
            and "/role-mappings/clients/" in path
        ):
            state.setdefault("sa_role_bindings", []).extend(body)
            return None
        # GET /<realm>/group-by-path/<path>
        if method == "GET" and path.startswith(f"/{REALM}/group-by-path/"):
            requested = path.rsplit("/", 1)[1]
            for g in state["groups"]:
                if g["path"].lstrip("/") == requested:
                    return {"id": g["id"], "path": g["path"]}
            raise http_404()
        # GET /<realm>/groups/<gid>/role-mappings/clients/<cuuid>
        if (
            method == "GET"
            and "/groups/" in path
            and "/role-mappings/clients/" in path
        ):
            gid = path.split("/")[3]
            return list(state.get("group_role_bindings", {}).get(gid, []))
        # POST role assignment to group
        if (
            method == "POST"
            and "/groups/" in path
            and "/role-mappings/clients/" in path
        ):
            gid = path.split("/")[3]
            state.setdefault("group_role_bindings", {}).setdefault(gid, []).extend(body)
            return None
        raise AssertionError(f"unexpected KC call: {method} {path}")

    return respond


def fresh_state():
    """In-memory realm representing a freshly-created Keycloak: ``groups``
    client-scope present (KC ships it by default) but no mapper yet, hub
    client exists without serviceAccountsEnabled, no client roles, no
    bindings, /admin group present."""
    return {
        "client_scopes": [{"id": "scope-groups", "name": "groups"}],
        "mappers": {},
        "clients": [
            {
                "id": HUB_UUID,
                "clientId": HUB_CLIENT_ID,
                "serviceAccountsEnabled": False,
            },
            {
                "id": RM_UUID,
                "clientId": "realm-management",
                "serviceAccountsEnabled": True,
            },
        ],
        "service_account_user": None,
        "client_roles": {
            RM_UUID: {
                name: {"id": f"rm-role-{i}", "name": name}
                for i, name in enumerate(rbac.REALM_MGMT_ROLES)
            },
        },
        "sa_role_bindings": [],
        "groups": [{"id": ADMIN_GROUP_ID, "path": ADMIN_GROUP_PATH}],
        "group_role_bindings": {},
    }


def make_config(groups=("/admin",), hub_external_url="https://hub.example.test"):
    return rbac.BootstrapConfig(
        kc_host="http://kc.test",
        admin_password="p",
        realm=REALM,
        hub_client_id=HUB_CLIENT_ID,
        role_name=ROLE_NAME,
        shared_mount_groups=tuple(groups),
        hub_external_url=hub_external_url,
    )


# Helper: drive enabling SA + creating SA user so subsequent steps work
# the way KC actually behaves (SA user appears after the toggle flips).
def enable_sa_then_appear(state):
    """KC's actual behaviour: after PUT enables serviceAccountsEnabled,
    the SA user is created. Simulate it by patching `state` once the
    PUT fires."""
    state["service_account_user"] = {"id": SA_USER_ID, "username": "sa"}


# ---------------------------------------------------------------------------
# run() against a fresh realm — exercises the full pipeline
# ---------------------------------------------------------------------------

def test_run_on_fresh_realm_creates_mapper_role_and_assignment():
    state = fresh_state()

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc = make_kc(respond)
    rbac.run(make_config(), kc)

    # Mapper is in place.
    assert any(
        m["name"] == "group-membership"
        for m in state["mappers"]["scope-groups"]
    ), "groups scope must have a group-membership mapper"

    # serviceAccountsEnabled flipped.
    hub = next(c for c in state["clients"] if c["id"] == HUB_UUID)
    assert hub["serviceAccountsEnabled"] is True

    # All required realm-management roles bound to the SA.
    bound = {b["name"] for b in state["sa_role_bindings"]}
    assert set(rbac.REALM_MGMT_ROLES).issubset(bound), (
        f"expected SA to hold {rbac.REALM_MGMT_ROLES}, got {bound}"
    )

    # The shared-directory role exists on hub client with the required attrs.
    role = state["client_roles"][HUB_UUID][ROLE_NAME]
    assert role["attributes"] == rbac.SHARED_DIR_ATTRIBUTES

    # /admin group got the role assigned.
    assigned = [
        b["name"]
        for b in state["group_role_bindings"].get(ADMIN_GROUP_ID, [])
    ]
    assert ROLE_NAME in assigned


# ---------------------------------------------------------------------------
# Idempotency — second run is a no-op (no POST/PUT/DELETE state changes)
# ---------------------------------------------------------------------------

def test_run_twice_is_a_noop_on_second_pass():
    """The Helm Job runs on every install AND every upgrade. The second
    invocation MUST NOT mutate realm state — otherwise upgrade rolls
    produce noise in KC audit logs and the deployment becomes flaky."""
    state = fresh_state()

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc1 = make_kc(respond)
    rbac.run(make_config(), kc1)

    # Capture every call the second run makes.
    kc2 = make_kc(respond)
    rbac.run(make_config(), kc2)
    second_pass_calls = kc2._request.call_args_list

    mutating = [
        call for call in second_pass_calls
        if call.args[0] in ("POST", "PUT", "DELETE")
    ]
    assert mutating == [], (
        f"second run must be read-only; got mutating calls: "
        f"{[c.args[:2] for c in mutating]}"
    )


# ---------------------------------------------------------------------------
# Missing groups in the deployer's list don't crash the bootstrap
# ---------------------------------------------------------------------------

def test_unknown_group_path_is_skipped_with_a_warning(caplog):
    """Deployer typos a group name in ``sharedMountGroups``. The bootstrap
    must continue with the rest (real groups get the role, hub still
    works) rather than aborting and leaving the realm half-configured."""
    state = fresh_state()

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc = make_kc(respond)
    cfg = make_config(groups=("/admin", "/no-such-group"))
    with caplog.at_level("WARNING", logger="rbac-bootstrap"):
        rbac.run(cfg, kc)

    assert any(
        "no-such-group" in record.message
        for record in caplog.records
    ), "missing group path should produce a WARNING"
    # /admin still got the role.
    assert ROLE_NAME in [
        b["name"]
        for b in state["group_role_bindings"][ADMIN_GROUP_ID]
    ]


# ---------------------------------------------------------------------------
# Role with stale attributes is reconciled
# ---------------------------------------------------------------------------

def test_existing_group_mapper_with_short_group_paths_is_reconciled():
    """A nebari-operator-managed ``groups`` scope ships the mapper with
    ``full.path: false``. KC's admin API returns role-group paths with
    a leading ``/`` so the spawner's intersection silently goes empty
    and ``/shared/<group>`` never mounts. Bootstrap must PUT the existing
    mapper to flip ``full.path`` to ``true``."""
    state = fresh_state()
    state["mappers"]["scope-groups"] = [{
        "id": "mapper-1",
        "name": "group-membership",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "config": {
            "full.path": "false",
            "claim.name": "groups",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    }]

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc = make_kc(respond)
    rbac.run(make_config(), kc)

    mapper = state["mappers"]["scope-groups"][0]
    assert mapper["config"]["full.path"] == "true"
    assert mapper["config"]["claim.name"] == "groups"


def test_hub_client_urls_are_set_when_hub_external_url_given():
    """KC-initiated OAuth flows need ``rootUrl`` / ``baseUrl`` /
    ``initiate.login.uri`` on the hub client so they route through
    ``/hub/oauth_login`` first and set the JupyterHub state cookie.
    Without these, JupyterHub raises ``400 OAuth state mismatch``."""
    state = fresh_state()

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc = make_kc(respond)
    rbac.run(make_config(hub_external_url="https://hub.example.test"), kc)

    client = next(c for c in state["clients"] if c["id"] == HUB_UUID)
    assert client["rootUrl"] == "https://hub.example.test"
    assert client["baseUrl"] == "/hub"
    assert client["attributes"]["initiate.login.uri"] == (
        "https://hub.example.test/hub/oauth_login"
    )


def test_hub_client_urls_skipped_when_hub_external_url_empty():
    """Empty HUB_EXTERNAL_URL = deployer opted out; chart still bootstraps
    the rest of the RBAC config but leaves rootUrl/baseUrl alone."""
    state = fresh_state()

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc = make_kc(respond)
    rbac.run(make_config(hub_external_url=""), kc)

    client = next(c for c in state["clients"] if c["id"] == HUB_UUID)
    assert "rootUrl" not in client or client["rootUrl"] is None
    assert "baseUrl" not in client or client["baseUrl"] is None


def test_existing_role_with_wrong_attributes_is_reconciled():
    """Deployer (or an older bootstrap) left the role around with the
    wrong attribute pair. The script must PUT to fix it, not leave the
    realm in a broken state where the hub's KCRealmAdmin filter sees
    'no required attrs' and silently returns []."""
    state = fresh_state()
    state["client_roles"][HUB_UUID] = {
        ROLE_NAME: {
            "id": ROLE_ID,
            "name": ROLE_NAME,
            "attributes": {"component": ["wrong-component"]},
        },
    }

    def respond(method, path, **kw):
        if method == "PUT" and path == f"/{REALM}/clients/{HUB_UUID}":
            enable_sa_then_appear(state)
        return _fake_realm(state)(method, path, **kw)

    kc = make_kc(respond)
    rbac.run(make_config(), kc)

    role = state["client_roles"][HUB_UUID][ROLE_NAME]
    assert role["attributes"] == rbac.SHARED_DIR_ATTRIBUTES
