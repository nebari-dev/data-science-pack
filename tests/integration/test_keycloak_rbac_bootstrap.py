"""End-to-end tests for ``files/keycloak_rbac_bootstrap.py`` against a
real Keycloak.

Spun up in CI by ``nebari-dev/action-nebari-sandbox`` (platform
profile), which deploys NIC's foundational stack — Keycloak + a
``nebari`` realm. The runner forwards ``localhost:18080`` to the
in-cluster ``keycloak-keycloakx-http`` service; this module reads
``KC_URL`` and ``KC_ADMIN_PASSWORD`` from the env and talks to that
endpoint over plain HTTP.

Why these tests exist:

The unit tests pin orchestration against an in-memory fake. They will
not catch KC version skew (e.g. an endpoint moves under
``/auth/admin/...``, a payload field gets renamed, a 4xx status is
emitted where the fake returned 2xx). These tests do — at the cost of
needing a live KC.

What's tested:

* Fresh provisioning end-to-end: groups-mapper, SA enable, RM role
  bindings, hub-side client role with required attributes, group→role
  assignment.
* Idempotency: a second run produces NO mutating POST/PUT calls.
* Attribute drift on the role: bootstrap PUTs the desired attribute
  pair back.
* Unknown group path: logged and skipped, real groups still get the
  role.

Each test gets its own freshly-created OIDC client + KC group with a
UUID suffix so they cannot collide with each other or with prior runs.
Cleanup is best-effort on teardown — Keycloak in CI is throwaway.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from conftest import rbac


log = logging.getLogger("rbac-integration")

REALM = "nebari"
ROLE_NAME = "allow-group-directory-creation-role"


# ---------------------------------------------------------------------------
# Live KC connection — module-scoped so we only password-grant once.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kc_url() -> str:
    url = os.environ.get("KC_URL")
    if not url:
        pytest.skip("KC_URL not set — these tests need a live Keycloak")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def kc_admin_password() -> str:
    pw = os.environ.get("KC_ADMIN_PASSWORD")
    if not pw:
        pytest.skip("KC_ADMIN_PASSWORD not set")
    return pw


@pytest.fixture(scope="module")
def kc(kc_url, kc_admin_password):
    """Authenticated admin client against the master realm."""
    client = rbac.KCAdmin(kc_url, kc_admin_password)
    # Force a token grant up-front so a misconfigured CI surfaces the
    # auth failure here (one place), not inside every test.
    client._token_now()
    return client


# ---------------------------------------------------------------------------
# Per-test scratch realm objects
# ---------------------------------------------------------------------------


@dataclass
class Scratch:
    client_id: str
    client_uuid: str
    group_path: str
    group_id: str


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture
def scratch(kc: rbac.KCAdmin) -> Iterator[Scratch]:
    """Create a throwaway OIDC client + group in the nebari realm.

    Bootstrap operates on whatever ``hubClientId`` the deployer hands
    it, so any KC client will do — the test client need not look like
    a real hub. The group path mirrors the production convention so
    ``get_group_id_by_path`` exercises the same code path.
    """
    suffix = _suffix()
    client_id = f"test-hub-{suffix}"
    group_path = f"/test-{suffix}"

    log.info("creating scratch client %s and group %s", client_id, group_path)
    kc._request(
        "POST",
        f"/{REALM}/clients",
        body={
            "clientId": client_id,
            "protocol": "openid-connect",
            "publicClient": False,
            "serviceAccountsEnabled": False,
            "standardFlowEnabled": True,
        },
    )
    client_uuid = kc.get_client_uuid(REALM, client_id)

    group = kc._request(
        "POST",
        f"/{REALM}/groups",
        body={"name": group_path.lstrip("/")},
    )
    group_id = kc.get_group_id_by_path(REALM, group_path)
    assert group_id is not None, f"group {group_path} not created"

    yield Scratch(client_id, client_uuid, group_path, group_id)

    # Best-effort cleanup. Test failures should leave artifacts in place
    # for diagnosis; CI tears down the whole cluster.
    for method, path in (
        ("DELETE", f"/{REALM}/clients/{client_uuid}"),
        ("DELETE", f"/{REALM}/groups/{group_id}"),
    ):
        try:
            kc._request(method, path)
        except Exception as exc:  # noqa: BLE001 — diagnostic only
            log.warning("cleanup %s %s failed: %s", method, path, exc)


def _config_for(scratch: Scratch, kc_url: str, kc_admin_password: str,
                groups: tuple[str, ...] = ()) -> rbac.BootstrapConfig:
    return rbac.BootstrapConfig(
        kc_host=kc_url,
        admin_password=kc_admin_password,
        realm=REALM,
        hub_client_id=scratch.client_id,
        role_name=ROLE_NAME,
        shared_mount_groups=groups or (scratch.group_path,),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fresh_provision_applies_every_step(
    kc: rbac.KCAdmin, scratch: Scratch, kc_url, kc_admin_password,
):
    """End-to-end happy path on a freshly-created client + group."""
    cfg = _config_for(scratch, kc_url, kc_admin_password)
    rbac.run(cfg, rbac.KCAdmin(kc_url, kc_admin_password))

    # 1. groups scope has a group-membership mapper.
    scope_id = kc.get_client_scope_id(REALM, "groups")
    assert scope_id is not None, "nebari realm should ship a 'groups' client-scope"
    mappers = kc._request(
        "GET", f"/{REALM}/client-scopes/{scope_id}/protocol-mappers/models",
    ) or []
    assert any(m["name"] == "group-membership" for m in mappers), (
        "bootstrap must add an oidc-group-membership-mapper to 'groups'"
    )

    # 2. serviceAccountsEnabled flipped on the hub client.
    client = kc._request("GET", f"/{REALM}/clients/{scratch.client_uuid}")
    assert client["serviceAccountsEnabled"] is True

    # 3. Realm-management roles bound to the SA user.
    sa_user_id = kc.get_service_account_user_id(REALM, scratch.client_uuid)
    rm_uuid = kc.get_client_uuid(REALM, "realm-management")
    bindings = kc._request(
        "GET",
        f"/{REALM}/users/{sa_user_id}/role-mappings/clients/{rm_uuid}",
    ) or []
    bound = {b["name"] for b in bindings}
    assert set(rbac.REALM_MGMT_ROLES).issubset(bound), (
        f"SA missing realm-management roles; have {bound}, "
        f"want {rbac.REALM_MGMT_ROLES}"
    )

    # 4. The shared-directory client role exists with the required attrs.
    role = kc._request(
        "GET", f"/{REALM}/clients/{scratch.client_uuid}/roles/{ROLE_NAME}",
    )
    assert role["attributes"] == rbac.SHARED_DIR_ATTRIBUTES

    # 5. The scratch group has the role assigned.
    group_bindings = kc._request(
        "GET",
        f"/{REALM}/groups/{scratch.group_id}"
        f"/role-mappings/clients/{scratch.client_uuid}",
    ) or []
    assert any(b["name"] == ROLE_NAME for b in group_bindings), (
        f"role {ROLE_NAME} not assigned to group {scratch.group_path}"
    )


def test_second_run_makes_no_mutating_calls(
    kc: rbac.KCAdmin, scratch: Scratch, kc_url, kc_admin_password,
):
    """Helm runs the Job on every install AND every upgrade. The second
    invocation MUST be read-only — otherwise every chart upgrade
    produces audit-log noise and risks reverting deployer overrides.
    """
    cfg = _config_for(scratch, kc_url, kc_admin_password)
    rbac.run(cfg, rbac.KCAdmin(kc_url, kc_admin_password))

    # Wrap _request to record every call the second pass makes.
    second_kc = rbac.KCAdmin(kc_url, kc_admin_password)
    calls: list[tuple[str, str]] = []
    real_request = second_kc._request

    def recording(method, path, **kw):
        calls.append((method, path))
        return real_request(method, path, **kw)

    second_kc._request = recording  # type: ignore[method-assign]
    rbac.run(cfg, second_kc)

    mutating = [c for c in calls if c[0] in ("POST", "PUT", "DELETE")]
    assert mutating == [], (
        f"second run must be read-only; got mutating calls: {mutating}"
    )


def test_role_attribute_drift_is_reconciled(
    kc: rbac.KCAdmin, scratch: Scratch, kc_url, kc_admin_password,
):
    """A pre-existing role with wrong attributes must be PUT back to
    the desired pair. Without reconciliation, the hub's KCRealmAdmin
    sees 'no required attrs' and silently returns an empty
    ``groups_with_permission_to_mount`` filter — the role looks bound
    but does nothing.
    """
    # Pre-seed the role with bad attributes so bootstrap finds it on
    # the GET-before-create path and has to PUT to fix it.
    kc._request(
        "POST",
        f"/{REALM}/clients/{scratch.client_uuid}/roles",
        body={
            "name": ROLE_NAME,
            "attributes": {"component": ["wrong-component"]},
        },
    )

    cfg = _config_for(scratch, kc_url, kc_admin_password)
    rbac.run(cfg, rbac.KCAdmin(kc_url, kc_admin_password))

    role = kc._request(
        "GET", f"/{REALM}/clients/{scratch.client_uuid}/roles/{ROLE_NAME}",
    )
    assert role["attributes"] == rbac.SHARED_DIR_ATTRIBUTES, (
        f"role attrs not reconciled; got {role['attributes']!r}"
    )


def test_unknown_group_path_is_skipped(
    kc: rbac.KCAdmin, scratch: Scratch, kc_url, kc_admin_password, caplog,
):
    """Deployer typo in ``sharedMountGroups``: bootstrap must continue
    with the other (real) groups rather than abort and leave the realm
    half-configured. The Helm Job exit code feeds Argo health; a hard
    failure here would block every chart upgrade until somebody fixes
    the typo.
    """
    fake_path = f"/no-such-group-{_suffix()}"
    cfg = _config_for(
        scratch, kc_url, kc_admin_password,
        groups=(scratch.group_path, fake_path),
    )

    with caplog.at_level(logging.WARNING, logger="rbac-bootstrap"):
        rbac.run(cfg, rbac.KCAdmin(kc_url, kc_admin_password))

    assert any(fake_path in r.message for r in caplog.records), (
        f"expected WARNING mentioning {fake_path}; got "
        f"{[r.message for r in caplog.records]}"
    )

    # Real group still got the role.
    bindings = kc._request(
        "GET",
        f"/{REALM}/groups/{scratch.group_id}"
        f"/role-mappings/clients/{scratch.client_uuid}",
    ) or []
    assert any(b["name"] == ROLE_NAME for b in bindings)
