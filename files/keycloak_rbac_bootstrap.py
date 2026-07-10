"""Keycloak realm bootstrap for role-gated shared-mount RBAC.

Idempotent. Provisions, in order:

    1. ``group-membership`` mapper on the ``groups`` client scope so KC
       emits a ``groups`` claim in tokens.
    2. ``serviceAccountsEnabled=true`` on the hub OIDC client.
    3. ``realm-management.view-{clients,groups,realm,users}`` bound to
       the hub client's service-account user (so the hub can call the
       KC Admin API at runtime to filter user groups by role).
    4. The shared-directory client role on the hub client with the
       required attributes ``component=shared-directory`` /
       ``scopes=write:shared-mount``.
    5. Assignment of that role to the configured KC group paths.

Re-running the script is safe: every step is a check-then-create
pattern that returns no-op if the desired state already exists. This
matters because the Helm chart runs the bootstrap on every install AND
every upgrade.

Configuration is taken from environment variables — the Helm Job
template wires these to a Secret reference (admin password) and chart
values (everything else). See ``BootstrapConfig.from_env``.

Implementation notes:

* Uses ``urllib.request`` from the standard library — no third-party
  dependencies, so the Job runs on a vanilla ``python:3.x-slim``
  image. ``pip install`` adds 5-10s per Job run and a network
  dependency we don't need.

* All HTTP calls go through :py:class:`KCAdmin._request`, which knows
  how to: attach the bearer token, JSON-encode the body, raise on
  4xx/5xx (except 409 when the caller explicitly opts in for
  idempotent ``POST create``), and decode the response.

* The class methods are deliberately small and single-purpose so the
  unit tests can mock at the HTTP boundary
  (``KCAdmin._request``) and exercise real control flow.

Reference: Keycloak Admin REST API
https://www.keycloak.org/docs-api/latest/rest-api/index.html
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Iterable

log = logging.getLogger("rbac-bootstrap")


SHARED_DIR_ATTRIBUTES = {
    "component": ["shared-directory"],
    "scopes": ["write:shared-mount"],
}
REALM_MGMT_ROLES = ("view-clients", "view-groups", "view-realm", "view-users")


@dataclasses.dataclass(frozen=True)
class BootstrapConfig:
    kc_host: str
    admin_password: str
    realm: str
    hub_client_id: str
    role_name: str
    shared_mount_groups: tuple[str, ...]
    hub_external_url: str
    hub_client_secret_name: str = ""
    hub_client_secret_namespace: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "BootstrapConfig":
        env = env if env is not None else dict(os.environ)
        raw_groups = env.get("SHARED_MOUNT_GROUPS", "").strip()
        return cls(
            kc_host=env["KC_HOST"],
            admin_password=env["KC_ADMIN_PASSWORD"],
            realm=env["REALM"],
            hub_client_id=env.get("HUB_CLIENT_ID", ""),
            role_name=env["ROLE_NAME"],
            shared_mount_groups=tuple(g for g in raw_groups.split(",") if g),
            hub_external_url=env.get("HUB_EXTERNAL_URL", "").rstrip("/"),
            hub_client_secret_name=env.get("HUB_CLIENT_SECRET_NAME", ""),
            hub_client_secret_namespace=env.get(
                "HUB_CLIENT_SECRET_NAMESPACE", ""
            ),
        )


class K8sSecretReader:
    """Reads keys out of a Kubernetes Secret via the API server.

    Used to fetch the hub OIDC client-id from the Secret the
    nebari-operator provisions next to the NebariApp, so the bootstrap
    never has to guess the operator's client-naming convention.
    Standard library only, mirroring :py:class:`KCAdmin`.
    """

    SERVICEACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        opener: Any = None,
        request_timeout: int = 30,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._opener = opener or urllib.request.build_opener()
        self._timeout = request_timeout

    @classmethod
    def in_cluster(cls) -> "K8sSecretReader":
        """Build a reader from the pod's mounted ServiceAccount
        credentials and the standard in-cluster API endpoint."""
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if ":" in host:  # bare IPv6 address
            host = f"[{host}]"
        with open(f"{cls.SERVICEACCOUNT_DIR}/token") as f:
            token = f.read().strip()
        context = ssl.create_default_context(
            cafile=f"{cls.SERVICEACCOUNT_DIR}/ca.crt"
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context)
        )
        return cls(
            base_url=f"https://{host}:{port}", token=token, opener=opener,
        )

    def read_key(
        self,
        namespace: str,
        name: str,
        key: str,
        *,
        retries: int = 60,
        sleep_seconds: float = 5.0,
    ) -> str:
        """The operator writes the Secret asynchronously after the
        NebariApp is created, so a fresh install's post-install hook can
        race it: poll through 404s instead of failing the Job."""
        url = f"{self._base_url}/api/v1/namespaces/{namespace}/secrets/{name}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}"},
        )
        for attempt in range(retries):
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    secret = json.loads(resp.read())
                data = secret.get("data") or {}
                if key not in data:
                    raise RuntimeError(
                        f"secret {namespace}/{name} has no {key!r} key; "
                        f"found keys: {sorted(data)}"
                    )
                return base64.b64decode(data[key]).decode()
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                if attempt < retries - 1:
                    log.info(
                        "secret %s/%s not there yet (operator still "
                        "provisioning?); retrying", namespace, name,
                    )
                    time.sleep(sleep_seconds)
        raise RuntimeError(
            f"secret {namespace}/{name} never appeared; is the "
            "nebari-operator running and reconciling the NebariApp?"
        )


class KCAdmin:
    """Thin wrapper around Keycloak's Admin REST API.

    Acquires a password-grant token on first use against the
    ``master`` realm's ``admin`` user. All subsequent calls attach
    that token. Errors surface as raised ``HTTPError`` from the
    underlying ``urlopen`` — callers either propagate or pass
    ``accept_409=True`` to swallow the duplicate-create case.

    The class doesn't cache anything beyond the token; pollers below
    drive idempotency by reading current state before writing.
    """

    PASSWORD_GRANT_PATH = "/realms/master/protocol/openid-connect/token"

    def __init__(
        self,
        base_url: str,
        admin_password: str,
        *,
        opener: Any = None,
        request_timeout: int = 30,
    ):
        self._base_url = base_url.rstrip("/")
        self._admin_password = admin_password
        self._opener = opener or urllib.request.build_opener()
        self._timeout = request_timeout
        self._token: str | None = None

    # --- token + transport -------------------------------------------

    def _password_grant(self) -> str:
        body = (
            "grant_type=password"
            "&client_id=admin-cli"
            "&username=admin"
            f"&password={self._admin_password}"
        ).encode()
        req = urllib.request.Request(
            f"{self._base_url}{self.PASSWORD_GRANT_PATH}",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with self._opener.open(req, timeout=self._timeout) as resp:
            return json.loads(resp.read())["access_token"]

    def _token_now(self) -> str:
        if self._token is None:
            self._token = self._password_grant()
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        accept_409: bool = False,
    ) -> Any:
        """One HTTP request against ``/admin/realms/...``.

        Returns the parsed JSON body, or ``None`` for 2xx with empty
        body. ``accept_409=True`` lets idempotent creates swallow KC's
        "already exists" response.
        """
        url = f"{self._base_url}/admin/realms{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"Bearer {self._token_now()}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                payload = resp.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            if e.code == 409 and accept_409:
                return None
            err_body = ""
            try:
                err_body = e.read().decode(errors="replace")
            except Exception:
                pass
            log.error("KC %s %s -> HTTP %d: %s", method, url, e.code, err_body)
            raise

    # --- group-membership mapper on a client scope -------------------

    def get_client_scope_id(self, realm: str, name: str) -> str | None:
        for scope in self._request("GET", f"/{realm}/client-scopes") or []:
            if scope["name"] == name:
                return scope["id"]
        return None

    def ensure_groups_mapper(self, realm: str, scope_name: str = "groups") -> None:
        scope_id = self.get_client_scope_id(realm, scope_name)
        if scope_id is None:
            log.info("creating client-scope %r", scope_name)
            self._request(
                "POST",
                f"/{realm}/client-scopes",
                body={
                    "name": scope_name,
                    "protocol": "openid-connect",
                    "attributes": {
                        "include.in.token.scope": "true",
                        "display.on.consent.screen": "true",
                    },
                },
                accept_409=True,
            )
            scope_id = self.get_client_scope_id(realm, scope_name)
            if scope_id is None:
                raise RuntimeError(f"client-scope {scope_name!r} creation failed")

        desired_config = {
            "full.path": "true",
            "introspection.token.claim": "true",
            "userinfo.token.claim": "true",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "claim.name": "groups",
        }
        mappers = self._request(
            "GET",
            f"/{realm}/client-scopes/{scope_id}/protocol-mappers/models",
        ) or []
        existing = next((m for m in mappers if m["name"] == "group-membership"), None)
        if existing is not None:
            # An operator-managed scope may pre-create the mapper with
            # ``full.path: false`` — that yields ``groups: ["admin"]`` in
            # the token, but the KC admin API returns role-groups as
            # ``["/admin"]``, so the spawner's intersection comes up
            # empty and ``/shared/<group>`` never mounts. Reconcile the
            # mapper config to the chart's desired state on every run.
            current = existing.get("config") or {}
            if all(current.get(k) == v for k, v in desired_config.items()):
                log.info("scope %r group-membership mapper already in desired state", scope_name)
                return
            log.info(
                "reconciling group-membership mapper config on scope %r (was %s)",
                scope_name, {k: current.get(k) for k in desired_config},
            )
            merged = {**current, **desired_config}
            self._request(
                "PUT",
                f"/{realm}/client-scopes/{scope_id}/protocol-mappers/models/{existing['id']}",
                body={**existing, "config": merged},
            )
            return

        log.info("adding oidc-group-membership-mapper to scope %r", scope_name)
        self._request(
            "POST",
            f"/{realm}/client-scopes/{scope_id}/protocol-mappers/models",
            body={
                "name": "group-membership",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "config": desired_config,
            },
            accept_409=True,
        )

    # --- hub client + service account --------------------------------

    def get_client_uuid(self, realm: str, client_id: str) -> str:
        clients = self._request(
            "GET",
            f"/{realm}/clients?clientId={client_id}",
        ) or []
        if not clients:
            raise RuntimeError(
                f"client {client_id!r} not found in realm {realm!r}"
            )
        return clients[0]["id"]

    def enable_service_accounts(self, realm: str, client_uuid: str) -> None:
        client = self._request("GET", f"/{realm}/clients/{client_uuid}")
        if client.get("serviceAccountsEnabled"):
            log.info("client %s already has serviceAccountsEnabled", client_uuid)
            return
        log.info("enabling serviceAccountsEnabled on client %s", client_uuid)
        client["serviceAccountsEnabled"] = True
        self._request("PUT", f"/{realm}/clients/{client_uuid}", body=client)

    def ensure_hub_client_urls(
        self,
        realm: str,
        client_uuid: str,
        hub_external_url: str,
    ) -> None:
        """Set ``rootUrl`` / ``baseUrl`` / ``initiate.login.uri`` on the hub
        OIDC client so KC-initiated flows (account console "Sign in",
        third-party launchers) route through ``/hub/oauth_login`` first,
        which gives JupyterHub a chance to set its ``oauthenticator-state``
        cookie before the callback runs. Without these the
        OAuth flow lands directly on ``/hub/oauth_callback`` with no
        matching cookie and JupyterHub raises a 400 "OAuth state mismatch".
        """
        if not hub_external_url:
            log.info(
                "hub client URL reconcile skipped: HUB_EXTERNAL_URL unset",
            )
            return
        client = self._request("GET", f"/{realm}/clients/{client_uuid}")
        desired_root = hub_external_url
        desired_base = "/hub"
        desired_initiate = f"{hub_external_url}/hub/oauth_login"
        attrs = dict(client.get("attributes") or {})
        if (
            client.get("rootUrl") == desired_root
            and client.get("baseUrl") == desired_base
            and attrs.get("initiate.login.uri") == desired_initiate
        ):
            log.info("hub client URLs already in desired state")
            return
        log.info(
            "reconciling hub client URLs (rootUrl=%r, baseUrl=%r, initiate.login.uri=%r)",
            desired_root, desired_base, desired_initiate,
        )
        attrs["initiate.login.uri"] = desired_initiate
        client["rootUrl"] = desired_root
        client["baseUrl"] = desired_base
        client["attributes"] = attrs
        self._request("PUT", f"/{realm}/clients/{client_uuid}", body=client)

    def get_service_account_user_id(
        self,
        realm: str,
        client_uuid: str,
        *,
        retries: int = 5,
        sleep_seconds: float = 2.0,
    ) -> str:
        """KC creates the SA user lazily after ``serviceAccountsEnabled``
        flips to true. Poll briefly before giving up."""
        last_exc: Exception | None = None
        for _ in range(retries):
            try:
                user = self._request(
                    "GET",
                    f"/{realm}/clients/{client_uuid}/service-account-user",
                )
                return user["id"]
            except urllib.error.HTTPError as e:
                last_exc = e
                if e.code != 404:
                    raise
                time.sleep(sleep_seconds)
        assert last_exc is not None
        raise RuntimeError(
            f"SA user for client {client_uuid} never appeared"
        ) from last_exc

    # --- realm-management roles on the SA ----------------------------

    def bind_realm_management_roles(
        self,
        realm: str,
        sa_user_id: str,
        role_names: Iterable[str],
    ) -> None:
        rm_uuid = self.get_client_uuid(realm, "realm-management")
        existing = self._request(
            "GET",
            f"/{realm}/users/{sa_user_id}/role-mappings/clients/{rm_uuid}",
        ) or []
        existing_names = {r["name"] for r in existing}
        to_bind: list[dict[str, str]] = []
        for name in role_names:
            if name in existing_names:
                continue
            try:
                role = self._request(
                    "GET",
                    f"/{realm}/clients/{rm_uuid}/roles/{name}",
                )
                to_bind.append({"id": role["id"], "name": role["name"]})
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    log.warning(
                        "realm-management role %r not found in this KC version; "
                        "skipping",
                        name,
                    )
                    continue
                raise
        if not to_bind:
            log.info("SA already has all required realm-management roles")
            return
        log.info(
            "binding %s on SA %s",
            [r["name"] for r in to_bind], sa_user_id,
        )
        self._request(
            "POST",
            f"/{realm}/users/{sa_user_id}/role-mappings/clients/{rm_uuid}",
            body=to_bind,
        )

    # --- shared-directory client role --------------------------------

    def ensure_client_role(
        self,
        realm: str,
        client_uuid: str,
        role_name: str,
        attributes: dict[str, list[str]],
    ) -> str:
        """Returns the role's UUID."""
        try:
            role = self._request(
                "GET",
                f"/{realm}/clients/{client_uuid}/roles/{role_name}",
            )
            if role.get("attributes") == attributes:
                log.info("role %r already in desired state", role_name)
                return role["id"]
            log.info("reconciling attributes on role %r", role_name)
            role["attributes"] = attributes
            self._request(
                "PUT",
                f"/{realm}/clients/{client_uuid}/roles/{role_name}",
                body=role,
            )
            return role["id"]
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        log.info("creating client role %r", role_name)
        self._request(
            "POST",
            f"/{realm}/clients/{client_uuid}/roles",
            body={"name": role_name, "attributes": attributes},
            accept_409=True,
        )
        role = self._request(
            "GET",
            f"/{realm}/clients/{client_uuid}/roles/{role_name}",
        )
        return role["id"]

    # --- group → role assignment -------------------------------------

    def get_group_id_by_path(self, realm: str, path: str) -> str | None:
        path_no_lead = path.lstrip("/")
        try:
            group = self._request(
                "GET",
                f"/{realm}/group-by-path/{path_no_lead}",
            )
            return group["id"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def assign_role_to_group(
        self,
        realm: str,
        group_id: str,
        client_uuid: str,
        role_id: str,
        role_name: str,
    ) -> None:
        existing = self._request(
            "GET",
            f"/{realm}/groups/{group_id}/role-mappings/clients/{client_uuid}",
        ) or []
        if any(r["name"] == role_name for r in existing):
            log.info("group %s already has role %r", group_id, role_name)
            return
        log.info("assigning role %r to group %s", role_name, group_id)
        self._request(
            "POST",
            f"/{realm}/groups/{group_id}/role-mappings/clients/{client_uuid}",
            body=[{"id": role_id, "name": role_name}],
        )


def resolve_hub_client_id(
    config: BootstrapConfig, secret_reader: K8sSecretReader
) -> str:
    """The clientId the nebari-operator gave the hub OIDC client.

    An explicit ``HUB_CLIENT_ID`` (chart value) always wins. Otherwise
    read the id out of the operator-provisioned OIDC client Secret,
    the same source of truth the hub itself mounts at ``/etc/oauth``,
    so the bootstrap never depends on the operator's client-naming
    convention.
    """
    if config.hub_client_id:
        return config.hub_client_id
    if not (
        config.hub_client_secret_name and config.hub_client_secret_namespace
    ):
        raise RuntimeError(
            "no hub client id available: set HUB_CLIENT_ID or both "
            "HUB_CLIENT_SECRET_NAME and HUB_CLIENT_SECRET_NAMESPACE"
        )
    return secret_reader.read_key(
        config.hub_client_secret_namespace,
        config.hub_client_secret_name,
        "client-id",
    )


def run(config: BootstrapConfig, kc: KCAdmin) -> None:
    log.info("==> 1. group-membership mapper on 'groups' scope")
    kc.ensure_groups_mapper(config.realm)

    log.info("==> 2. hub OIDC client + service account")
    hub_uuid = kc.get_client_uuid(config.realm, config.hub_client_id)
    kc.enable_service_accounts(config.realm, hub_uuid)
    kc.ensure_hub_client_urls(config.realm, hub_uuid, config.hub_external_url)
    sa_user_id = kc.get_service_account_user_id(config.realm, hub_uuid)

    log.info("==> 3. realm-management roles bound to hub SA")
    kc.bind_realm_management_roles(config.realm, sa_user_id, REALM_MGMT_ROLES)

    log.info("==> 4. shared-directory client role on hub client")
    role_id = kc.ensure_client_role(
        config.realm, hub_uuid, config.role_name, SHARED_DIR_ATTRIBUTES,
    )

    log.info("==> 5. assign role to configured groups")
    for path in config.shared_mount_groups:
        group_id = kc.get_group_id_by_path(config.realm, path)
        if group_id is None:
            log.warning("group path %r not found in realm; skipping", path)
            continue
        kc.assign_role_to_group(
            config.realm, group_id, hub_uuid, role_id, config.role_name,
        )

    log.info("bootstrap complete")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = BootstrapConfig.from_env()
    except KeyError as missing:
        log.error("missing required env var: %s", missing)
        return 2
    secret_reader = (
        None if config.hub_client_id else K8sSecretReader.in_cluster()
    )
    hub_client_id = resolve_hub_client_id(config, secret_reader)
    log.info("hub OIDC clientId: %s", hub_client_id)
    config = dataclasses.replace(config, hub_client_id=hub_client_id)
    kc = KCAdmin(config.kc_host, config.admin_password)
    run(config, kc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
