"""Behaviour of the KeyCloakOAuthenticator wiring in 00-gateway-auth.py."""

from __future__ import annotations

from conftest import FakeConfig, load_config_module


ISSUER = "https://kc.example.test/realms/nebari"
CLIENT_ID = "hub"
CLIENT_SECRET = "shhh"
CALLBACK = "https://hub.example.test/hub/oauth_callback"
EXTERNAL = "https://hub.example.test/"


def _configure_with_defaults(**overrides):
    """Helper: call configure() with sensible defaults; return the FakeConfig."""
    mod = load_config_module("00-gateway-auth.py")
    c = FakeConfig()
    kwargs = dict(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        callback_url=CALLBACK,
        external_url=EXTERNAL,
    )
    kwargs.update(overrides)
    mod.configure(c, **kwargs)
    return c, mod


# ---------------------------------------------------------------------------
# Cycle 1 — class wiring + URL derivation
# ---------------------------------------------------------------------------

def test_configure_selects_keycloak_authenticator():
    c, mod = _configure_with_defaults()
    assert c.JupyterHub.authenticator_class is mod.KeyCloakOAuthenticator


def test_configure_derives_keycloak_urls_from_issuer():
    c, _ = _configure_with_defaults()
    kc = c.KeyCloakOAuthenticator
    assert kc.authorize_url == f"{ISSUER}/protocol/openid-connect/auth"
    assert kc.token_url == f"{ISSUER}/protocol/openid-connect/token"
    assert kc.userdata_url == f"{ISSUER}/protocol/openid-connect/userinfo"


def test_configure_sets_username_claim_to_preferred_username():
    c, _ = _configure_with_defaults()
    assert c.KeyCloakOAuthenticator.username_claim == "preferred_username"


# ---------------------------------------------------------------------------
# Cycle 2 — auth_state persistence + group/admin claims
# ---------------------------------------------------------------------------

def test_configure_enables_auth_state_so_refresh_user_can_run():
    c, _ = _configure_with_defaults()
    kc = c.KeyCloakOAuthenticator
    assert kc.enable_auth_state is True
    assert kc.refresh_pre_spawn is True
    # auth_refresh_age must be smaller than KC's 5-min access-token TTL
    # so refresh fires before the token actually expires.
    assert 0 < kc.auth_refresh_age <= 300


def test_configure_reads_groups_claim_for_authorization():
    c, _ = _configure_with_defaults()
    assert c.KeyCloakOAuthenticator.claim_groups_key == "groups"


def test_admin_groups_default_to_admin_when_unset():
    c, _ = _configure_with_defaults()
    assert c.KeyCloakOAuthenticator.admin_groups == {"admin"}


def test_admin_groups_can_be_overridden_per_deployment():
    c, _ = _configure_with_defaults(admin_groups=["site-admins", "platform"])
    assert c.KeyCloakOAuthenticator.admin_groups == {"site-admins", "platform"}


# ---------------------------------------------------------------------------
# Cycle 3 — logout terminates the Keycloak session
# ---------------------------------------------------------------------------

def test_configure_leaves_logout_redirect_url_empty_so_handler_runs():
    """LogoutHandler.get short-circuits to authenticator.logout_redirect_url
    when auto_login=True, never calling render_logout_page. Keep it empty
    so our subclass's render_logout_page (which builds a per-user URL
    with id_token_hint) actually fires.
    """
    c, _ = _configure_with_defaults()
    assert c.KeyCloakOAuthenticator.logout_redirect_url == ""


def test_configure_stashes_kc_end_session_pieces_for_handler():
    """KeyCloakLogoutHandler reads these at request time."""
    c, _ = _configure_with_defaults()
    kc = c.KeyCloakOAuthenticator
    assert kc._kc_end_session_url == f"{ISSUER}/protocol/openid-connect/logout"
    assert kc._kc_post_logout_redirect_uri == EXTERNAL


# ---------------------------------------------------------------------------
# Cycle 4 — authorization policy (any KC-authenticated user is allowed)
# ---------------------------------------------------------------------------

def test_configure_requests_openid_scope_so_userinfo_endpoint_works():
    """Without the openid scope, KC returns 403 at /userinfo.

    GenericOAuthenticator defaults to an empty scope list, which makes
    KC omit the openid scope from the issued token; that token can't
    call /userinfo, so token_to_user blows up with HTTP 403.
    """
    c, _ = _configure_with_defaults()
    scopes = c.KeyCloakOAuthenticator.scope
    assert "openid" in scopes
    # Groups + email round out the claims the spawner / env-list rely on.
    assert "groups" in scopes
    assert "email" in scopes


def test_configure_enables_auto_login_so_hub_skips_local_login_form():
    """auto_login=True makes /hub/login 302 to the IdP, not render a form.

    Without this, users see hub's "Sign in with OAuth 2.0" page with a
    button to click — pointless friction when there's one IdP.
    """
    c, _ = _configure_with_defaults()
    assert c.Authenticator.auto_login is True


def test_keycloak_authenticator_uses_custom_logout_handler():
    """oauthenticator.OAuthenticator.get_handlers reads the class-level
    ``logout_handler`` attribute when registering /logout. Swapping
    that to our subclass is the supported way to override logout
    behaviour without duplicating the /logout route.
    """
    c, mod = _configure_with_defaults()
    assert mod.KeyCloakOAuthenticator.logout_handler is mod.KeyCloakLogoutHandler


def test_build_logout_url_includes_id_token_hint_and_post_redirect():
    c, mod = _configure_with_defaults()
    url = mod._build_logout_url(
        end_session_url=f"{ISSUER}/protocol/openid-connect/logout",
        id_token="header.payload.signature",
        post_logout_redirect_uri=EXTERNAL,
    )
    assert url.startswith(f"{ISSUER}/protocol/openid-connect/logout?")
    assert "id_token_hint=header.payload.signature" in url
    assert "post_logout_redirect_uri=" in url
    assert "https%3A%2F%2Fhub.example.test" in url


def test_build_logout_url_omits_id_token_hint_when_missing():
    """Token may be absent (legacy session): still produce a usable URL
    that at least clears local cookies and bounces back."""
    c, mod = _configure_with_defaults()
    url = mod._build_logout_url(
        end_session_url=f"{ISSUER}/protocol/openid-connect/logout",
        id_token=None,
        post_logout_redirect_uri=EXTERNAL,
    )
    assert "id_token_hint=" not in url
    assert "post_logout_redirect_uri=" in url


def test_any_keycloak_authenticated_user_is_allowed_by_default():
    """The gateway path admitted any KC user; this path must match.

    Restricting to specific users/groups is a separate decision; the
    default keeps parity with EnvoyOIDCAuthenticator so existing users
    don't suddenly lose access on flip-day.
    """
    c, _ = _configure_with_defaults()
    assert c.Authenticator.allow_all is True


# ---------------------------------------------------------------------------
# Cycle 5 — production wiring is opt-in via env var
# ---------------------------------------------------------------------------

def test_module_loads_in_jupyterhub_context_without_oauth_env(monkeypatch):
    """Even when `c` is in scope (real JupyterHub run), missing env must not crash.

    Plain `kind` deploys ship the chart without the operator Secret. Hub
    must come up with the chart's default authenticator (dummy) instead
    of crashing on a missing /etc/oauth/client-id.
    """
    for key in ("OAUTH_CALLBACK_URL", "OAUTH_EXTERNAL_URL", "OAUTH_SECRET_DIR"):
        monkeypatch.delenv(key, raising=False)

    c = FakeConfig()
    mod = load_config_module("00-gateway-auth.py", inject_c=c)

    # Sanity: the module still exposes its public surface.
    assert callable(mod.configure)
    # And it didn't try to wire up the authenticator on an empty config.
    assert "JupyterHub" not in c.__dict__, (
        "Authenticator was configured despite missing OAUTH env vars"
    )
