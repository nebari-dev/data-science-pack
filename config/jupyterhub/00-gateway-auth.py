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
from urllib.parse import quote

from oauthenticator.generic import GenericOAuthenticator


class KeyCloakOAuthenticator(GenericOAuthenticator):
    """Marker subclass so traitlets config can target it explicitly."""


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
    # Hub logout must terminate the upstream Keycloak session, otherwise
    # the next /hub/ request silently re-uses it. Bounce through KC's
    # end_session_endpoint with post_logout_redirect_uri pointing back here.
    c.KeyCloakOAuthenticator.logout_redirect_url = (
        f"{urls['end_session_url']}"
        f"?post_logout_redirect_uri={quote(external_url, safe='')}"
    )
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
