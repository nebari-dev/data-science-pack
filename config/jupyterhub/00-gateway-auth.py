"""JupyterHub authenticator that does its own OAuth flow with Keycloak.

This module replaces the earlier EnvoyOIDCAuthenticator path where Envoy
Gateway acted as the OAuth client. Envoy v1.6 does not rotate cookie
contents on every request, so `auth_state` went stale for paths that
bypassed hub (e.g. `/services/japps/*`).

With this module, hub is the OAuth client. JupyterHub's built-in
refresh_user uses the stored refresh_token to keep auth_state fresh
without depending on browser cookies or gateway-injected headers.

The chart mounts the operator-created KC client Secret at
``/etc/oauth/`` (overridable via ``OAUTH_SECRET_DIR``); ``OAUTH_CALLBACK_URL``
and ``OAUTH_EXTERNAL_URL`` come from chart-rendered envs.
"""

# ruff: noqa: F821 - `c` is a magic global provided by JupyterHub

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from oauthenticator.generic import GenericOAuthenticator
from oauthenticator.oauth2 import OAuthLogoutHandler
from tornado.httpclient import HTTPClientError
from traitlets import Unicode


class KCRealmAdmin:
    """Keycloak Admin API client, narrowed to the one question this chart
    needs to answer: *of the KC groups this user belongs to, which ones
    hold the shared-directory mount role?*

    Deep module: one public method (:meth:`filter_user_groups_by_role`)
    hides four HTTP round-trips, two JSON parsings, the
    ``client_credentials`` grant against the realm token endpoint, the
    role-attribute validation, and the set intersection. Callers don't
    need to know the realm API URL exists.

    The class takes ``http_fetch`` as a dependency rather than building
    its own client so tests can mock at the HTTP boundary without
    touching authenticator internals. In production it's bound to the
    parent ``OAuthenticator.httpfetch``.

    Construction is pure; only the public method does I/O.
    """

    REQUIRED_ATTRS = {
        "component": "shared-directory",
        "scopes": "write:shared-mount",
    }
    # A jupyterlab-profiles role must carry this component marker for its
    # ``profiles`` attribute to be honoured: an unmarked role grants nothing.
    PROFILES_COMPONENT = "jupyterhub-profiles"

    def __init__(self, http_fetch, *, token_url, client_id, client_secret, realm_api_url):
        self._http_fetch = http_fetch
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._realm_api_url = realm_api_url

    async def filter_user_groups_by_role(self, user_groups, role_name):
        """Return the subset of ``user_groups`` (KC group paths) whose
        members hold ``role_name`` on the configured OAuth client.

        Returns ``[]`` if the role exists but lacks the required
        ``component=shared-directory`` + ``scopes=write:shared-mount``
        attributes — safer to grant no mounts than to grant all groups.

        Raises whatever the underlying HTTP fetcher raises on transport
        failure; callers handle.
        """
        token = await self._client_credentials_token()
        client_uuid = await self._lookup_client_uuid(token, self._client_id)
        role = await self._admin_get(token, f"/clients/{client_uuid}/roles/{role_name}")
        if not self._role_has_required_attributes(role):
            return []
        role_groups = await self._admin_get(
            token, f"/clients/{client_uuid}/roles/{role_name}/groups",
        )
        role_paths = {g.get("path") for g in role_groups if g.get("path")}
        return [g for g in user_groups if g in role_paths]

    async def get_profile_slugs_for_user(self, user_id, role_name):
        """Return the JupyterLab profile slugs ``user_id`` may select.

        Reads the client role ``role_name``. When that role carries
        ``component=jupyterhub-profiles``, its ``profiles`` attribute is the
        slug allow-list. The slugs apply only if the user effectively holds
        the role (assigned directly or inherited from a group).

        Returns ``[]`` when: the role does not exist, lacks the
        ``jupyterhub-profiles`` component marker, has no ``profiles``
        attribute, or the user does not hold it. Raises whatever the HTTP
        fetcher raises on transport failure; callers handle.
        """
        token = await self._client_credentials_token()
        client_uuid = await self._lookup_client_uuid(token, self._client_id)
        role = await self._get_client_role_or_none(token, client_uuid, role_name)
        if role is None or not self._role_has_profiles_component(role):
            return []
        slugs = (role.get("attributes") or {}).get("profiles") or []
        if not slugs:
            return []
        if not await self._user_holds_client_role(
            token, client_uuid, user_id, role_name,
        ):
            return []
        return list(slugs)

    # --- internals (each step is one HTTP round-trip) -------------------

    async def _get_client_role_or_none(self, token, client_uuid, role_name):
        """Role representation, or ``None`` when the role does not exist.

        A missing role is the normal state on deployments that have not
        created the profiles role yet, so a 404 returns ``None`` instead of
        raising. No warning spam, no degraded login.
        """
        try:
            return await self._admin_get(
                token, f"/clients/{client_uuid}/roles/{role_name}",
            )
        except HTTPClientError as e:
            if e.code == 404:
                return None
            raise

    async def _user_holds_client_role(self, token, client_uuid, user_id, role_name):
        """True when ``user_id`` effectively holds ``role_name`` on the
        client, whether assigned directly or inherited via a group. KC's
        ``role-mappings/.../composite`` endpoint resolves both."""
        if not user_id:
            return False
        roles = await self._admin_get(
            token,
            f"/users/{user_id}/role-mappings/clients/{client_uuid}/composite",
        )
        return any(r.get("name") == role_name for r in roles or [])

    @classmethod
    def _role_has_profiles_component(cls, role):
        attrs = role.get("attributes", {}) or {}
        return cls.PROFILES_COMPONENT in (attrs.get("component") or [])

    async def _client_credentials_token(self):
        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        })
        token_info = await self._http_fetch(
            self._token_url,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
        )
        return token_info["access_token"]

    async def _lookup_client_uuid(self, token, client_id):
        clients = await self._admin_get(token, f"/clients?clientId={client_id}")
        if not clients:
            raise RuntimeError(f"KC client {client_id!r} not found in realm")
        return clients[0]["id"]

    async def _admin_get(self, token, path):
        return await self._http_fetch(
            f"{self._realm_api_url}{path}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )

    @classmethod
    def _role_has_required_attributes(cls, role):
        attrs = role.get("attributes", {}) or {}
        for key, expected in cls.REQUIRED_ATTRS.items():
            if expected not in (attrs.get(key) or []):
                return False
        return True


@dataclass(frozen=True)
class KeyCloakConfig:
    """All Keycloak strings the authenticator + handlers need at runtime.

    Replaces an earlier pattern where logout pieces lived as stray class
    attributes on the authenticator (``_kc_end_session_url`` and
    ``_kc_post_logout_redirect_uri``) while every other endpoint URL was
    set independently on traitlets. Bundling them lets ``configure()``
    derive everything from the issuer in one place and means the logout
    handler only has to grab one object off the authenticator instance.

    Use ``KeyCloakConfig.build(issuer=…, post_logout_redirect_uri=…)`` —
    the constructor takes already-derived URLs so tests can build odd
    shapes directly.
    """

    issuer: str
    authorize_url: str
    token_url: str
    userdata_url: str
    end_session_url: str
    post_logout_redirect_uri: str

    @classmethod
    def build(cls, *, issuer: str, post_logout_redirect_uri: str) -> "KeyCloakConfig":
        """Derive every KC endpoint URL from the realm issuer."""
        base = f"{issuer}/protocol/openid-connect"
        return cls(
            issuer=issuer,
            authorize_url=f"{base}/auth",
            token_url=f"{base}/token",
            userdata_url=f"{base}/userinfo",
            end_session_url=f"{base}/logout",
            post_logout_redirect_uri=post_logout_redirect_uri,
        )

    def build_logout_url(self, id_token: str | None) -> str:
        """Compose the per-user KC end-session URL.

        Keycloak v18+ rejects logout without ``id_token_hint`` when a
        ``post_logout_redirect_uri`` is also given. ``id_token`` may be
        None if the user's auth_state was never populated (legacy
        session); fall back to just the redirect.
        """
        params = {"post_logout_redirect_uri": self.post_logout_redirect_uri}
        if id_token:
            params["id_token_hint"] = id_token
        return f"{self.end_session_url}?{urlencode(params)}"


class KeyCloakLogoutHandler(OAuthLogoutHandler):
    """Bounce hub logout through Keycloak's end_session endpoint.

    KC requires ``id_token_hint`` when ``post_logout_redirect_uri`` is
    present. The token lives in the user's auth_state, so it must be
    read at request time. Override ``get`` (not ``render_logout_page``)
    because ``LogoutHandler.get`` clears ``self._jupyterhub_user`` BEFORE
    calling ``render_logout_page`` — by the time the latter runs,
    ``current_user`` is None and auth_state is unreachable.
    """

    async def get(self):
        user = self.current_user
        id_token = None
        if user is not None:
            try:
                auth_state = await user.get_auth_state()
                if auth_state:
                    id_token = auth_state.get("id_token")
            except Exception:
                self.log.warning(
                    "logout: failed reading auth_state for %s — proceeding "
                    "without id_token_hint", user.name, exc_info=True,
                )
        # Standard cleanup from base LogoutHandler.get (token revocation,
        # cookie clear, server shutdown).
        await self.default_handle_logout()
        await self.handle_logout()
        self._jupyterhub_user = None
        self.redirect(self.authenticator.kc_config.build_logout_url(id_token))


class KeyCloakOAuthenticator(GenericOAuthenticator):
    """Keycloak-flavoured GenericOAuthenticator.

    Swaps in :class:`KeyCloakLogoutHandler` via the ``logout_handler``
    class hook on :class:`oauthenticator.OAuthenticator`, which is what
    ``get_handlers`` reads when registering the ``/logout`` route.
    """

    logout_handler = KeyCloakLogoutHandler

    # Populated by configure() at startup. Logout + refresh_user read this
    # off the authenticator instance at request time. Class attribute (not
    # traitlet) because traitlets' config-loader rejects unknown names
    # via `c.<Class>.<attr>` assignment.
    kc_config: "KeyCloakConfig | None" = None

    # KC Admin API URL for the realm (e.g.
    # https://keycloak.example/admin/realms/nebari). Used by
    # update_auth_model to resolve which of a user's groups hold the
    # shared-directory mount role. Empty disables RBAC entirely —
    # auth_state["groups_with_permission_to_mount"] is not set, and the
    # spawner falls back to its existing behaviour.
    realm_api_url = Unicode("", config=True)
    # Name of the KC client role that grants /shared/<group> mount
    # permission. The role must carry attributes
    # component=["shared-directory"] and scopes=["write:shared-mount"].
    # Default name kept stable so existing realm-setup tooling
    # (kcadm scripts, deployment tests) does not need to be edited
    # to recognise the role.
    shared_mount_role_name = Unicode(
        "allow-group-directory-creation-role", config=True,
    )
    # Name of the KC client role whose ``profiles`` attribute lists the
    # JupyterLab profile slugs a holder may select (``access: keycloak``
    # profiles). The role lives on the hub client and is created + assigned
    # to users/groups by the deployer in Keycloak; the chart only reads it.
    jupyterlab_profiles_role_name = Unicode(
        "jupyterlab-profiles", config=True,
    )

    async def refresh_user(self, user, handler=None):
        """Run KC's refresh_token grant and persist rotated tokens to auth_state.

        JupyterHub's default Authenticator.refresh_user is a no-op (returns
        True), and GenericOAuthenticator doesn't override it. Result: the
        refresh_token stored in auth_state at OAuth-callback time stays
        frozen until KC's SSO idle timeout invalidates it (~30 min by
        default). The next sync caller — nebi-envs's 3-step exchange —
        then fails at step 1 with `invalid_grant: Token is not active`,
        env list returns [], and the Create-App Software Environment
        dropdown vanishes for the user.

        Contract (per JupyterHub Authenticator.refresh_user docstring):
          - return dict  -> JupyterHub merges these fields into auth_state
          - return True  -> auth_state is fine, leave it
          - return False -> auth_state is invalid, force re-login

        We always return a dict on a successful grant so KC's rotated
        refresh_token gets written back. On `invalid_grant` we return False
        to force re-auth (silent no-op would leave the session stale until
        next manual login). Transient errors keep the existing auth_state.
        """
        auth_state = await user.get_auth_state()
        if not auth_state or not auth_state.get("refresh_token"):
            return True

        body = urlencode({
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": auth_state["refresh_token"],
        })
        try:
            token_info = await self.httpfetch(
                self.token_url,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
        except HTTPClientError as e:
            err_kind = ""
            if e.response is not None and e.response.body:
                try:
                    err_kind = json.loads(e.response.body).get("error", "")
                except Exception:
                    pass
            if e.code == 400 and err_kind == "invalid_grant":
                self.log.warning(
                    "KC refresh_token expired for %s — forcing re-login",
                    user.name,
                )
                return False
            self.log.warning(
                "KC refresh failed for %s: HTTP %s %s — keeping current auth_state",
                user.name, e.code, err_kind or e.message,
            )
            return True

        # Successful grant: KC returns a new access_token (always) and a
        # rotated refresh_token (when rotation enabled). Preserve any
        # auth_state keys we don't get back (e.g. oauth_user from
        # GenericOAuthenticator.authenticate's response).
        new_state = dict(auth_state)
        new_state["access_token"] = token_info["access_token"]
        if token_info.get("refresh_token"):
            new_state["refresh_token"] = token_info["refresh_token"]
        if token_info.get("id_token"):
            new_state["id_token"] = token_info["id_token"]
        new_state["token_response"] = token_info
        if self.realm_api_url:
            # Re-evaluate the role-gated mount filter against current KC
            # state. KC role/group changes mid-session take effect on
            # this user's next spawn — no force-relogin needed.
            user_groups = new_state.get("oauth_user", {}).get("groups", [])
            try:
                new_state["groups_with_permission_to_mount"] = (
                    await self._realm_admin().filter_user_groups_by_role(
                        user_groups, self.shared_mount_role_name,
                    )
                )
            except Exception:
                self.log.warning(
                    "rbac: failed to refresh groups_with_permission_to_mount "
                    "for %s — keeping last known set",
                    user.name, exc_info=True,
                )
                # Preserve whatever was already there if the prior auth had it.
                old_filter = auth_state.get("groups_with_permission_to_mount")
                if old_filter is not None:
                    new_state["groups_with_permission_to_mount"] = old_filter
            # Re-resolve the role-granted JupyterLab profiles too, so a
            # mid-session role grant/revoke takes effect on the next spawn.
            try:
                new_state["allowed_jupyterlab_profiles"] = (
                    await self._realm_admin().get_profile_slugs_for_user(
                        new_state.get("oauth_user", {}).get("sub"),
                        self.jupyterlab_profiles_role_name,
                    )
                )
            except Exception:
                self.log.warning(
                    "rbac: failed to refresh allowed_jupyterlab_profiles "
                    "for %s, keeping last known set",
                    user.name, exc_info=True,
                )
                old_profiles = auth_state.get("allowed_jupyterlab_profiles")
                if old_profiles is not None:
                    new_state["allowed_jupyterlab_profiles"] = old_profiles
        return {"auth_state": new_state}

    async def update_auth_model(self, auth_model):
        """Stamp auth_state with the subset of KC groups that hold the
        shared-mount role.

        JupyterHub calls this on every login (and on every refresh once
        the parent class is configured to do so). The spawner reads
        ``auth_state["groups_with_permission_to_mount"]`` at spawn time
        to decide which ``/shared/<group>`` dirs to mount — being in a
        KC group is necessary but not sufficient; the group also has to
        hold the shared-directory role.

        Disabled cleanly when ``realm_api_url`` is empty. KC Admin API
        failures (no SA on the OAuth client, network blip, 5xx) are
        logged and degraded to an empty set — login itself MUST succeed
        regardless of realm-admin reachability.
        """
        auth_model = await super().update_auth_model(auth_model)
        if not self.realm_api_url:
            return auth_model
        user_groups = (
            auth_model.get("auth_state", {})
            .get("oauth_user", {})
            .get("groups", [])
        )
        try:
            filtered = await self._realm_admin().filter_user_groups_by_role(
                user_groups, self.shared_mount_role_name,
            )
        except Exception:
            self.log.warning(
                "rbac: failed to compute groups_with_permission_to_mount "
                "for %s — granting no shared mounts this session",
                auth_model.get("name"), exc_info=True,
            )
            filtered = []
        auth_model["auth_state"]["groups_with_permission_to_mount"] = filtered
        auth_model["auth_state"]["allowed_jupyterlab_profiles"] = (
            await self._resolve_allowed_profiles(
                auth_model.get("auth_state", {}).get("oauth_user", {}),
                auth_model.get("name"),
            )
        )
        return auth_model

    async def _resolve_allowed_profiles(self, oauth_user, who):
        """Resolve the JupyterLab profile slugs the user's
        ``jupyterlab-profiles`` KC role grants, degrading to ``[]`` on any
        Admin API failure so login/refresh never breaks. The spawner reads
        ``auth_state["allowed_jupyterlab_profiles"]`` to gate
        ``access: keycloak`` profiles by slug."""
        user_id = (oauth_user or {}).get("sub")
        try:
            return await self._realm_admin().get_profile_slugs_for_user(
                user_id, self.jupyterlab_profiles_role_name,
            )
        except Exception:
            self.log.warning(
                "rbac: failed to resolve allowed_jupyterlab_profiles for %s, "
                "granting no keycloak-gated profiles this session",
                who, exc_info=True,
            )
            return []

    def _realm_admin(self):
        """Build a :class:`KCRealmAdmin` from the authenticator's current
        config. Cheap to construct (no I/O); we make a fresh one per
        call so traitlet updates take effect immediately."""
        return KCRealmAdmin(
            self.httpfetch,
            token_url=self.token_url,
            client_id=self.client_id,
            client_secret=self.client_secret,
            realm_api_url=self.realm_api_url,
        )


def configure(
    c,
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    callback_url: str,
    external_url: str,
    admin_groups=None,
    realm_api_url: str = "",
    shared_mount_role_name: str = "allow-group-directory-creation-role",
    jupyterlab_profiles_role_name: str = "jupyterlab-profiles",
):
    """Wire KeyCloakOAuthenticator onto JupyterHub's `c` config object.

    ``realm_api_url`` enables the role-gated KC Admin API lookups. Pass the
    KC Admin API root for the realm
    (e.g. ``https://kc.example/admin/realms/nebari``); leave empty to
    disable. ``shared_mount_role_name`` is the KC client role whose holders
    get ``/shared/<group>`` mounts. ``jupyterlab_profiles_role_name`` is the
    KC client role whose ``profiles`` attribute lists the slugs a holder may
    select for ``access: keycloak`` profiles. Both default to the classic
    nebari names.
    """
    kc_config = KeyCloakConfig.build(
        issuer=issuer, post_logout_redirect_uri=external_url,
    )
    c.JupyterHub.authenticator_class = KeyCloakOAuthenticator
    c.KeyCloakOAuthenticator.client_id = client_id
    c.KeyCloakOAuthenticator.client_secret = client_secret
    c.KeyCloakOAuthenticator.realm_api_url = realm_api_url
    c.KeyCloakOAuthenticator.shared_mount_role_name = shared_mount_role_name
    c.KeyCloakOAuthenticator.jupyterlab_profiles_role_name = (
        jupyterlab_profiles_role_name
    )
    c.KeyCloakOAuthenticator.oauth_callback_url = callback_url
    c.KeyCloakOAuthenticator.authorize_url = kc_config.authorize_url
    c.KeyCloakOAuthenticator.token_url = kc_config.token_url
    c.KeyCloakOAuthenticator.userdata_url = kc_config.userdata_url
    c.KeyCloakOAuthenticator.username_claim = "preferred_username"
    # Explicit scopes — GenericOAuthenticator defaults to [] which omits the
    # scope param entirely; KC then issues a token without `openid` and
    # /userinfo returns 403 at token_to_user.
    c.KeyCloakOAuthenticator.scope = ["openid", "profile", "email", "groups"]
    # OAuthenticator 17 requires managed groups for admin_groups and replaces
    # GenericOAuthenticator.claim_groups_key with auth_state_groups_key.
    # Keep KC's raw full group paths in auth_state for RBAC/shared storage,
    # but expose leaf names to JupyterHub groups so admin_groups=["admin"]
    # matches a KC claim like "/admin".
    c.KeyCloakOAuthenticator.manage_groups = True
    c.KeyCloakOAuthenticator.auth_state_groups_key = _jupyterhub_group_names
    c.KeyCloakOAuthenticator.admin_groups = set(_leaf_group_names(admin_groups or ["admin"]))
    # Persist tokens so refresh_user can use the stored refresh_token.
    c.KeyCloakOAuthenticator.enable_auth_state = True
    c.KeyCloakOAuthenticator.refresh_pre_spawn = True
    # Refresh ~1 min before KC's 5-min access-token TTL expires.
    c.KeyCloakOAuthenticator.auth_refresh_age = 240
    # Leave logout_redirect_url empty so LogoutHandler.get falls through
    # to render_logout_page (our subclass) instead of short-circuiting
    # to a static URL that can't include id_token_hint.
    c.KeyCloakOAuthenticator.logout_redirect_url = ""
    # Stash all KC strings (issuer-derived URLs + post_logout redirect) on
    # the class so KeyCloakLogoutHandler can compose per-user end-session
    # URLs at request time. Class attribute (not traitlet) because
    # traitlets' config-loader rejects unknown names assigned via
    # `c.<Class>.<attr>` with a warning and silently drops the value.
    KeyCloakOAuthenticator.kc_config = kc_config
    # Skip hub's local /hub/login form — go straight to Keycloak's
    # authorize endpoint. One IdP, no point making the user click a
    # "Sign in with OAuth 2.0" button.
    c.Authenticator.auto_login = True
    # Any KC-authenticated user is admitted (matches the prior policy);
    # tighten via admin_groups / allowed_groups per-deploy if needed.
    c.Authenticator.allow_all = True


def _read_secret_file(secret_dir: Path, key: str) -> str:
    """Read a single value out of the operator-mounted KC client Secret."""
    return (secret_dir / key).read_text().strip()


def _leaf_group_names(raw_groups):
    """Return group leaf names while preserving first-seen order."""
    seen = set()
    groups = []
    for group in raw_groups or []:
        if not group:
            continue
        name = Path(group).name
        if name and name not in seen:
            seen.add(name)
            groups.append(name)
    return groups


def _jupyterhub_group_names(auth_state):
    """Return Keycloak group names in the form OAuthenticator admin groups use."""
    return _leaf_group_names((auth_state.get("oauth_user") or {}).get("groups"))


def _derive_realm_api_url(issuer_url: str) -> str:
    """Convert a KC realm issuer URL to its admin-API counterpart.

    Issuer URL:    ``https://kc.example/realms/nebari``
    Admin API URL: ``https://kc.example/admin/realms/nebari``

    Returns ``""`` when the URL doesn't match the standard KC layout —
    callers fall back to the explicit ``KC_REALM_API_URL`` env var.
    """
    marker = "/realms/"
    idx = issuer_url.find(marker)
    if idx == -1:
        return ""
    return f"{issuer_url[:idx]}/admin{issuer_url[idx:]}"


# Chart-rendered constants. ``templates/hub-config.yaml`` substitutes the
# ``__CHART_*__`` placeholders with values computed from ``nebariapp.hostname``
# at Helm render time, so deployers do not need to repeat the URLs in their
# ``hub.extraEnv``. Untouched placeholders (``__CHART_*__``) mean we are
# either running under a non-substituting renderer (a unit test, a local
# ``kind`` deploy without nebariapp) or the deployer opted out — both cases
# fall back to the historical ``OAUTH_CALLBACK_URL`` env-var path.
_CHART_OAUTH_CALLBACK_URL = "__CHART_OAUTH_CALLBACK_URL__"
_CHART_OAUTH_EXTERNAL_URL = "__CHART_OAUTH_EXTERNAL_URL__"


def _resolve_oauth_urls() -> tuple[str, str] | None:
    """Return (callback_url, external_url) or None when OAuth is opted out.

    Chart-rendered values win; env vars are the legacy escape hatch.
    """
    if _CHART_OAUTH_CALLBACK_URL.startswith("https://"):
        return _CHART_OAUTH_CALLBACK_URL, _CHART_OAUTH_EXTERNAL_URL
    env_callback = os.environ.get("OAUTH_CALLBACK_URL")
    if env_callback:
        return env_callback, os.environ["OAUTH_EXTERNAL_URL"]
    return None


# When loaded by JupyterHub, `c` is a magic global. On host imports (tests),
# `c` is undefined and the production wiring is skipped.
#
# Production wiring is gated TWICE:
#   1. `c` must exist (real JupyterHub run, not a host import).
#   2. OAuth URLs must resolve — via chart-rendered constants OR env vars.
# Without (2), the chart's default authenticator (dummy) stays in place,
# so plain `kind` deploys come up without needing the operator Secret.
try:
    c  # type: ignore[used-before-def]
except NameError:
    pass
else:
    _urls = _resolve_oauth_urls()
    if _urls is not None:
        _callback_url, _external_url = _urls
        _secret_dir = Path(os.environ.get("OAUTH_SECRET_DIR", "/etc/oauth"))
        # RBAC for role-gated /shared/<group> mounts.
        # ``realm_api_url`` is normally derived from the same issuer URL
        # we mount for the OIDC client (one host, two paths). Deployers
        # with a non-standard layout (e.g. KC admin behind a different
        # gateway) can still pin it via the ``KC_REALM_API_URL`` env var.
        # Role-name is rarely overridden; env-var path kept for parity.
        _issuer = _read_secret_file(_secret_dir, "issuer-url")
        _realm_api_url = (
            os.environ.get("KC_REALM_API_URL")
            or _derive_realm_api_url(_issuer)
        )
        configure(
            c,  # noqa: F821
            issuer=_issuer,
            client_id=_read_secret_file(_secret_dir, "client-id"),
            client_secret=_read_secret_file(_secret_dir, "client-secret"),
            callback_url=_callback_url,
            external_url=_external_url,
            realm_api_url=_realm_api_url,
            shared_mount_role_name=os.environ.get(
                "KC_SHARED_MOUNT_ROLE",
                "allow-group-directory-creation-role",
            ),
            jupyterlab_profiles_role_name=os.environ.get(
                "KC_JUPYTERLAB_PROFILES_ROLE",
                "jupyterlab-profiles",
            ),
        )
