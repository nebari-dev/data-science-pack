"""Nebi environment listing for jhub-apps environment selector."""
# ruff: noqa: F821 - `c` is a magic global provided by JupyterHub

import asyncio
import json
import logging
import os
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

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

    # Read config values (chart-derived defaults fill in when blank)
    keycloak_url = get_chart_config("keycloak-token-url")
    nebi_cid = get_chart_config("nebi-client-id")
    hub_cid = get_chart_config("jupyterhub-client-id")
    hub_secret = os.environ.get("JUPYTERHUB_OIDC_CLIENT_SECRET", "")
    nebi_url = get_chart_config("nebi-internal-url")

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
            except Exception:
                log.exception(
                    "nebi-envs: failed to fetch auth_state for %s", username,
                )
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
        except Exception:
            log.exception(
                "nebi-envs: failed to fetch workspaces for %s (url=%s)",
                username, workspaces_url,
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
# Local workspace listing (no remote Nebi server)
# ---------------------------------------------------------------------------

# In-cluster address of the hub's proxy. The service name is hardcoded
# because JupyterHub starts jhub-apps with a scrubbed environment, so the
# PROXY_PUBLIC_SERVICE_* variables Kubernetes injects are not visible here.
_PROXY_URL = "http://proxy-public"


# Where the hub remembers each user's last list, so the selector still offers
# it while their JupyterLab is stopped. Workspaces are only created and
# changed from the main JupyterLab, so the last list it gave stays correct
# until it runs again. /srv/jupyterhub is the hub's own persistent volume.
_SAVED_LISTS_DIR = "/srv/jupyterhub/nebi-envs"


def _saved_list_path(username):
    return os.path.join(_SAVED_LISTS_DIR, f"{quote(username, safe='')}.json")


def _save_list(username, envs):
    # The hub (at stop) and jhub-apps (on form open) both write: each write
    # goes to its own temporary file, then replaces the list in one step.
    try:
        os.makedirs(_SAVED_LISTS_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_SAVED_LISTS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(envs, f)
        os.replace(tmp, _saved_list_path(username))
    except OSError:
        log.exception("nebi-envs: could not save the environment list for %s", username)


def _saved_list(username):
    try:
        with open(_saved_list_path(username)) as f:
            envs = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.exception("nebi-envs: could not read the saved environment list for %s", username)
        return []
    # Same check as a fresh listing: the file is only ever written from one.
    return [env for env in envs if isinstance(env, str) and _local_workspace_dir(env)]


def get_local_nebi_environments(user):
    """Return the user's local Nebi workspaces, each as the directory it lives in.

    Used when no remote Nebi server is deployed. The workspaces live on the
    user's own volumes, which the hub cannot read, so ask the Nebi instance
    inside the user's running JupyterLab and remember the answer. While
    JupyterLab is stopped, or if asking it fails, offer the remembered list.

    The directory, not the name, is what gets selected: at launch the app pod
    copies pixi.toml and pixi.lock out of it and builds its own environment
    (_add_local_workspace_container in 01-spawner.py), so starting an app
    needs neither JupyterLab nor the Nebi database, only those two files.
    """
    username = user.get("name", "<unknown>")
    lab = (user.get("servers") or {}).get("") or {}
    if not lab.get("ready"):
        log.info(
            "nebi-envs: JupyterLab is not running for %s, offering the saved list",
            username,
        )
        return _saved_list(username)

    envs = _list_from_lab(username, lab["url"])
    if envs is None:
        return _saved_list(username)
    _save_list(username, envs)
    return envs


def _list_from_lab(username, lab_url):
    """Ask the Nebi instance in the user's running JupyterLab for its workspaces.

    jupyter-server-proxy serves it at /user/<name>/nebi/, behind the
    JupyterLab server's hub authentication. Returns None on any failure.
    """
    token = _short_lived_user_token(username)
    if token is None:
        return None
    return _fetch_lab_workspaces(username, f"{_PROXY_URL}{lab_url}", token)


def _short_lived_user_token(username):
    """Ask the hub for a 60s token that acts as the user; None on failure."""
    req = Request(
        f"{os.environ.get('JUPYTERHUB_API_URL', '')}/users/{username}/tokens",
        data=json.dumps({"expires_in": 60}).encode(),
        headers={
            "Authorization": f"token {os.environ.get('JUPYTERHUB_API_TOKEN', '')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["token"]
    except Exception:
        log.exception("nebi-envs: could not get a token for %s", username)
        return None


def _fetch_lab_workspaces(username, server_url, token):
    """GET the workspaces from a JupyterLab server; None on any failure."""
    # The first request may have to start `nebi serve`, which
    # jupyter-server-proxy waits up to 20s for.
    req = Request(f"{server_url}nebi/api/v1/workspaces", headers={"Authorization": f"token {token}"})
    try:
        with urlopen(req, timeout=30) as resp:
            workspaces = json.loads(resp.read())
    except Exception:
        log.exception("nebi-envs: failed to list local environments for %s", username)
        return None

    if not isinstance(workspaces, list):
        log.error(
            "nebi-envs: unexpected local workspaces response type %s for %s (expected list)",
            type(workspaces).__name__, username,
        )
        return None

    envs = _usable_workspace_dirs(workspaces)
    log.info(
        "nebi-envs: listed %d local environments for %s (total workspaces: %d)",
        len(envs), username, len(workspaces),
    )
    return envs


def _usable_workspace_dirs(workspaces):
    """The directories of the ready workspaces an app pod can copy from.

    _local_workspace_dir (01-spawner.py) is the same check the spawner applies
    to the selection.
    """
    return [
        ws["path"] for ws in workspaces
        if ws.get("status") == "ready" and _local_workspace_dir(ws.get("path"))
    ]


async def save_list_before_stop(spawner):
    """Save the final list of a main JupyterLab that is about to stop.

    Called from the spawner's stop() while the pod is still up, so the list
    saved is exactly what JupyterLab had, even if the user never opened the
    app form after their last change. The hub asks the pod directly with the
    server's own token. Never raises: a stop must not fail because of this.
    """
    # JupyterHub also calls stop() to clean up a start that failed, when
    # there may be no server to ask.
    if not _local_nebi_workspaces or spawner.name or spawner.server is None:
        return
    envs = await asyncio.to_thread(
        _fetch_lab_workspaces, spawner.user.name, spawner.server.url, spawner.api_token,
    )
    if envs is not None:
        _save_list(spawner.user.name, envs)


# ---------------------------------------------------------------------------
# Register the callable with jhub-apps
# ---------------------------------------------------------------------------
# _local_nebi_workspaces is set in 01-spawner.py, so the selector and the
# spawner always agree on which kind of selection is in play.
if not _local_nebi_workspaces:
    c.JAppsConfig.conda_envs = get_nebi_environments
    log.info(
        "nebi-envs: environment selector enabled (nebi_url=%s)",
        get_chart_config("nebi-internal-url"),
    )
else:
    c.JAppsConfig.conda_envs = get_local_nebi_environments
    log.info(
        "nebi-envs: no remote Nebi server configured, "
        "environment selector lists local workspaces"
    )
