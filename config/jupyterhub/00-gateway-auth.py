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

import os
from pathlib import Path
from urllib.parse import urlencode

from oauthenticator.generic import GenericOAuthenticator
from oauthenticator.oauth2 import OAuthLogoutHandler


def _build_logout_url(
    *,
    end_session_url: str,
    id_token: str | None,
    post_logout_redirect_uri: str,
) -> str:
    """Build a KC end-session URL with id_token_hint + post_logout_redirect_uri.

    Keycloak v18+ rejects logout without ``id_token_hint`` when a
    ``post_logout_redirect_uri`` is also given. ``id_token`` may be None
    if the user's auth_state was never populated (legacy session); fall
    back to just the redirect.
    """
    params = {"post_logout_redirect_uri": post_logout_redirect_uri}
    if id_token:
        params["id_token_hint"] = id_token
    return f"{end_session_url}?{urlencode(params)}"


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
        url = _build_logout_url(
            end_session_url=self.authenticator._kc_end_session_url,
            id_token=id_token,
            post_logout_redirect_uri=self.authenticator._kc_post_logout_redirect_uri,
        )
        self.redirect(url)


class KeyCloakOAuthenticator(GenericOAuthenticator):
    """Keycloak-flavoured GenericOAuthenticator.

    Swaps in :class:`KeyCloakLogoutHandler` via the ``logout_handler``
    class hook on :class:`oauthenticator.OAuthenticator`, which is what
    ``get_handlers`` reads when registering the ``/logout`` route.
    """

    logout_handler = KeyCloakLogoutHandler

    # Stashed by configure() so the logout handler can build URLs at request time.
    _kc_end_session_url: str = ""
    _kc_post_logout_redirect_uri: str = ""


def _kc_urls(issuer: str) -> dict:
    """Derive Keycloak OIDC endpoint URLs from the realm issuer URL."""
    base = f"{issuer}/protocol/openid-connect"
    return {
        "authorize_url": f"{base}/auth",
        "token_url": f"{base}/token",
        "userdata_url": f"{base}/userinfo",
        "end_session_url": f"{base}/logout",
    }


def configure(
    c,
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    callback_url: str,
    external_url: str,
    admin_groups=None,
):
    """Wire KeyCloakOAuthenticator onto JupyterHub's `c` config object."""
    urls = _kc_urls(issuer)
    c.JupyterHub.authenticator_class = KeyCloakOAuthenticator
    c.KeyCloakOAuthenticator.client_id = client_id
    c.KeyCloakOAuthenticator.client_secret = client_secret
    c.KeyCloakOAuthenticator.oauth_callback_url = callback_url
    c.KeyCloakOAuthenticator.authorize_url = urls["authorize_url"]
    c.KeyCloakOAuthenticator.token_url = urls["token_url"]
    c.KeyCloakOAuthenticator.userdata_url = urls["userdata_url"]
    c.KeyCloakOAuthenticator.username_claim = "preferred_username"
    # Explicit scopes — GenericOAuthenticator defaults to [] which omits the
    # scope param entirely; KC then issues a token without `openid` and
    # /userinfo returns 403 at token_to_user.
    c.KeyCloakOAuthenticator.scope = ["openid", "profile", "email", "groups"]
    c.KeyCloakOAuthenticator.claim_groups_key = "groups"
    c.KeyCloakOAuthenticator.admin_groups = set(admin_groups or ["admin"])
    # Persist tokens so refresh_user can use the stored refresh_token.
    c.KeyCloakOAuthenticator.enable_auth_state = True
    c.KeyCloakOAuthenticator.refresh_pre_spawn = True
    # Refresh ~1 min before KC's 5-min access-token TTL expires.
    c.KeyCloakOAuthenticator.auth_refresh_age = 240
    # Leave logout_redirect_url empty so LogoutHandler.get falls through
    # to render_logout_page (our subclass) instead of short-circuiting
    # to a static URL that can't include id_token_hint.
    c.KeyCloakOAuthenticator.logout_redirect_url = ""
    # Stash the pieces KeyCloakLogoutHandler.render_logout_page reads at
    # request time to build the per-user end-session URL. These are
    # plain class attributes (not traitlets), so set them directly on
    # the class instead of via `c.<Class>.<attr> = ...` — traitlets'
    # config-loader rejects unknown names with a warning and never
    # propagates the value.
    KeyCloakOAuthenticator._kc_end_session_url = urls["end_session_url"]
    KeyCloakOAuthenticator._kc_post_logout_redirect_uri = external_url
    # Skip hub's local /hub/login form — go straight to Keycloak's
    # authorize endpoint. One IdP, no point making the user click a
    # "Sign in with OAuth 2.0" button.
    c.Authenticator.auto_login = True
    # Any KC-authenticated user is admitted (matches prior EnvoyOIDC policy);
    # tighten via admin_groups / allowed_groups per-deploy if needed.
    c.Authenticator.allow_all = True


def _read_secret_file(secret_dir: Path, key: str) -> str:
    """Read a single value out of the operator-mounted KC client Secret."""
    return (secret_dir / key).read_text().strip()


# When loaded by JupyterHub, `c` is a magic global. On host imports (tests),
# `c` is undefined and the production wiring is skipped.
#
# Production wiring is gated TWICE:
#   1. `c` must exist (real JupyterHub run, not a host import).
#   2. `OAUTH_CALLBACK_URL` must be set (deployer opted into KC OAuth).
# Without (2), the chart's default authenticator (dummy) stays in place,
# so plain `kind` deploys come up without needing the operator Secret.
try:
    c  # type: ignore[used-before-def]
except NameError:
    pass
else:
    if os.environ.get("OAUTH_CALLBACK_URL"):
        _secret_dir = Path(os.environ.get("OAUTH_SECRET_DIR", "/etc/oauth"))
        configure(
            c,  # noqa: F821
            issuer=_read_secret_file(_secret_dir, "issuer-url"),
            client_id=_read_secret_file(_secret_dir, "client-id"),
            client_secret=_read_secret_file(_secret_dir, "client-secret"),
            callback_url=os.environ["OAUTH_CALLBACK_URL"],
            external_url=os.environ["OAUTH_EXTERNAL_URL"],
        )
