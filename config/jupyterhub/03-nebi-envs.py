"""Nebi environment listing for jhub-apps environment selector."""
# ruff: noqa: F821 - `c` is a magic global provided by JupyterHub

import json
import logging
import os
from urllib.request import Request, urlopen

from z2jh import get_config

log = logging.getLogger(__name__)

# Token exchange functions (_extract_error_body, _sync_refresh_access_token,
# _sync_exchange_access_token_for_nebi_id_token,
# _sync_exchange_nebi_id_token_for_jwt, get_nebi_jwt) are defined in
# 01-spawner.py which loads first.  JupyterHub executes config files in the
# same namespace, so they are available here as globals.


# ---------------------------------------------------------------------------
# Nebi environment listing callable
# ---------------------------------------------------------------------------

def get_nebi_environments(user):
    """Return list of ready nebi workspaces as '{owner_username}/{workspace_name}' strings.

    Called synchronously by jhub-apps to populate the environment selector dropdown.
    The `user` parameter is a dict from the JupyterHub API, containing:
      {"name": "alice", "auth_state": {"access_token": "...", "refresh_token": "..."}, ...}

    On any failure, returns an empty list (graceful degradation).
    """
    username = user.get("name", "<unknown>")

    # Read config values
    keycloak_url = get_config("custom.keycloak-token-url", "")
    nebi_cid = get_config("custom.nebi-client-id", "")
    hub_cid = get_config("custom.jupyterhub-client-id", "")
    hub_secret = os.environ.get("JUPYTERHUB_OIDC_CLIENT_SECRET", "")
    nebi_url = get_config("custom.nebi-internal-url", "")

    if not all([keycloak_url, nebi_cid, hub_cid, hub_secret, nebi_url]):
        missing = []
        if not keycloak_url:
            missing.append("keycloak-token-url")
        if not nebi_cid:
            missing.append("nebi-client-id")
        if not hub_cid:
            missing.append("jupyterhub-client-id")
        if not hub_secret:
            missing.append("JUPYTERHUB_OIDC_CLIENT_SECRET")
        if not nebi_url:
            missing.append("nebi-internal-url")
        log.error(
            "nebi-envs: cannot list environments for %s, missing config: %s",
            username, ", ".join(missing),
        )
        return []

    # Extract auth_state from user dict.  jhub-apps fetches the user with a
    # per-user token that lacks admin:auth_state scope, so auth_state is
    # usually None.  Fall back to fetching it directly using the jhub-apps
    # service API token (JUPYTERHUB_API_TOKEN) which has admin:auth_state.
    # This is still per-user: we read THIS user's Keycloak tokens, then
    # exchange them for a nebi JWT scoped to this user.
    auth_state = user.get("auth_state")
    if not auth_state:
        api_url = os.environ.get("JUPYTERHUB_API_URL", "")
        api_token = os.environ.get("JUPYTERHUB_API_TOKEN", "")
        if api_url and api_token:
            try:
                req = Request(
                    f"{api_url}/users/{username}",
                    headers={"Authorization": f"token {api_token}"},
                )
                with urlopen(req, timeout=10) as resp:
                    auth_state = json.loads(resp.read()).get("auth_state")
                if auth_state:
                    log.info(
                        "nebi-envs: fetched auth_state for %s via hub API — keys=%s, "
                        "has_refresh_token=%s, has_access_token=%s",
                        username, list(auth_state.keys()),
                        bool(auth_state.get("refresh_token")),
                        bool(auth_state.get("access_token")),
                    )
                else:
                    log.warning("nebi-envs: hub API returned auth_state=None for %s", username)
            except Exception as exc:
                log.error("nebi-envs: failed to fetch auth_state for %s: %s", username, exc)
        else:
            log.warning("nebi-envs: JUPYTERHUB_API_URL/TOKEN not set, cannot fetch auth_state")
    if not auth_state:
        log.warning("nebi-envs: no auth_state for user %s, cannot list environments", username)
        return []

    log.info(
        "nebi-envs: auth_state for %s — keys=%s, has_refresh_token=%s, has_access_token=%s, source=%s",
        username, list(auth_state.keys()),
        bool(auth_state.get("refresh_token")),
        bool(auth_state.get("access_token")),
        "user_dict" if user.get("auth_state") else "hub_api_fallback",
    )
    refresh_token = auth_state.get("refresh_token")
    access_token = auth_state.get("access_token", "")

    if not refresh_token and not access_token:
        log.warning(
            "nebi-envs: no access_token or refresh_token for user %s, cannot list environments",
            username,
        )
        return []

    try:
        # Reuse the shared token exchange from 01-spawner.py
        nebi_jwt = get_nebi_jwt(
            refresh_token, access_token,
            keycloak_url, nebi_cid, hub_cid, hub_secret, nebi_url,
        )
        if not nebi_jwt:
            log.warning("nebi-envs: token exchange returned no JWT for %s", username)
            return []

        # Fetch workspaces from Nebi API
        workspaces_url = f"{nebi_url.rstrip('/')}/api/v1/workspaces"
        req = Request(
            workspaces_url,
            headers={"Authorization": f"Bearer {nebi_jwt}"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                workspaces = json.loads(resp.read())
        except Exception as exc:
            log.error(
                "nebi-envs: failed to fetch workspaces for %s: %s (url=%s)",
                username, exc, workspaces_url,
            )
            return []

        # Validate response shape and filter to ready workspaces
        if not isinstance(workspaces, list):
            log.error(
                "nebi-envs: unexpected workspaces response type %s for %s (expected list)",
                type(workspaces).__name__, username,
            )
            return []

        envs = []
        for ws in workspaces:
            if ws.get("status") != "ready":
                continue
            owner = ws.get("owner", {})
            owner_username = owner.get("username", "")
            ws_name = ws.get("name", "")
            if owner_username and ws_name:
                envs.append(f"{owner_username}/{ws_name}")

        log.info(
            "nebi-envs: listed %d ready environments for %s (total workspaces: %d)",
            len(envs), username, len(workspaces),
        )
        return envs

    except Exception:
        log.exception(
            "nebi-envs: unexpected error listing environments for %s", username,
        )
        return []


# ---------------------------------------------------------------------------
# Register the callable with jhub-apps (only when enabled)
# ---------------------------------------------------------------------------
_nebi_env_selector = get_config("custom.nebi-environment-selector", False)
_nebi_internal_url = get_config("custom.nebi-internal-url", "")

if _nebi_env_selector and _nebi_internal_url:
    c.JAppsConfig.conda_envs = get_nebi_environments
    log.info("nebi-envs: environment selector enabled (nebi_url=%s)", _nebi_internal_url)
else:
    if _nebi_env_selector and not _nebi_internal_url:
        log.warning(
            "nebi-envs: nebi-environment-selector is true but nebi-internal-url is not set, "
            "environment selector will not be enabled"
        )
