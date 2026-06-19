"""KubeSpawner configuration."""
# ruff: noqa: F821 - `c` is a magic global provided by JupyterHub

import asyncio
import json
import logging
import os
import shlex
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import escapism
import string
from kubernetes_asyncio.client.rest import ApiException
from kubespawner.objects import make_pvc
from z2jh import get_config

log = logging.getLogger(__name__)


def _external_auth_unrendered(value):
    """Return true for Helm placeholders in unit-test/import contexts."""
    return str(value).startswith("__EXTERNAL_AUTH_")


def _external_auth_json(value, default):
    if _external_auth_unrendered(value):
        return default
    return json.loads(value)


def _external_auth_int(value, default):
    if _external_auth_unrendered(value) or not str(value).strip():
        return default
    return int(value)


external_auth_enabled = (
    False
    if _external_auth_unrendered("__EXTERNAL_AUTH_ENABLED__")
    else "__EXTERNAL_AUTH_ENABLED__".lower() == "true"
)
external_auth_broker_url = (
    ""
    if _external_auth_unrendered("__EXTERNAL_AUTH_BROKER_URL__")
    else "__EXTERNAL_AUTH_BROKER_URL__".strip()
)
external_auth_providers = _external_auth_json(r'''__EXTERNAL_AUTH_PROVIDERS_JSON__''', [])
external_auth_env_var_map = _external_auth_json(r'''__EXTERNAL_AUTH_ENV_VAR_MAP_JSON__''', {})
external_auth_timeout_seconds = _external_auth_int("__EXTERNAL_AUTH_TIMEOUT_SECONDS__", 10)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
# Dynamic PVC per user via the cluster's default StorageClass.
# We configure volumes here instead of via singleuser.storage in values.yaml because
# jhub-apps' JHubSpawner expects volumes as a list, but the subchart's dynamic storage
# generates a dict, causing a TraitError on startup.
#
# Using fixed /home/jovyan mount path because {username} expands differently in
# KubeSpawner (escaped slug) vs base Spawner traits like notebook_dir (raw username),
# causing a path mismatch when usernames contain special characters (e.g. emails).
# Each user still gets an isolated PVC via claim-{username}.
c.KubeSpawner.storage_pvc_ensure = True
c.KubeSpawner.storage_capacity = get_config("custom.storage-capacity", "20Gi")
c.KubeSpawner.storage_access_modes = ["ReadWriteOnce"]
# Without this override, KubeSpawner's default template is
# `claim-{username}--{servername}`, so for jhub-apps named servers it ensures
# a per-server PVC — while the `volumes` block below mounts the per-user
# `claim-{username}`. Users with a pre-existing per-user PVC (legacy
# JupyterLab launch) survived this mismatch; fresh users hit
# FailedScheduling because the per-user PVC is never auto-created. Forcing
# the template to `{username}` makes ensure + mount converge on the same
# RWO claim, which the pod-affinity rule below keeps to a single node.
c.KubeSpawner.pvc_name_template = "claim-{username}"
c.KubeSpawner.volumes = [
    {
        "name": "home",
        "persistentVolumeClaim": {
            "claimName": "claim-{username}",
        },
    },
    {
        "name": "singleuser-config",
        "configMap": {
            "name": "__SINGLEUSER_CONFIG_CM__",  # replaced by Helm template
        },
    },
]
c.KubeSpawner.volume_mounts = [
    {
        "name": "home",
        "mountPath": "/home/jovyan",
    },
    {
        "name": "singleuser-config",
        "mountPath": "/etc/jupyter/jupyter_server_config.py",
        "subPath": "jupyter_server_config.py",
    },
]

c.KubeSpawner.notebook_dir = "/home/jovyan"
c.KubeSpawner.working_dir = "/home/jovyan"

# affinity — co-locate all pods for the same user on the same node.
# hcloud-volumes is ReadWriteOnce — only one node can mount it at a time.
# Pod affinity ensures jhub-apps app pods land on the same node as the
# user's JupyterLab pod so the shared home PVC can be mounted by all of them.
#
# securityContext.fsGroup: 100 — GID 100 (users group) as the pod's fsGroup.
# Kubernetes adds GID 100 as a supplemental group and chgrps mounted volumes
# to 100 at pod start. This ensures shared dirs (created by init container as
# root:root) become root:100, writable by every member of the users group.
#
# securityContext.fsGroupChangePolicy: OnRootMismatch — the kubelet's default
# recursive chown of every file on the home PVC to match fsGroup can take
# 30-150s on PVCs with large pixi envs / model caches and time out the spawn.
# OnRootMismatch only chowns when the root dir's GID is wrong, so the cost is
# paid once and skipped on subsequent spawns.
#
# fsGroup is set here (not via c.KubeSpawner.fs_gid) because kubespawner's
# extra_pod_config applies pod.spec attributes with a top-level overwrite —
# any securityContext we set here REPLACES the one fs_gid would produce, so
# both fields must live in this dict together.
c.KubeSpawner.extra_pod_config = {
    "affinity": {
        "podAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
                {
                    "labelSelector": {
                        "matchExpressions": [
                            {
                                "key": "hub.jupyter.org/username",
                                "operator": "In",
                                "values": ["{username}"],
                            }
                        ]
                    },
                    "topologyKey": "kubernetes.io/hostname",
                }
            ]
        }
    },
    "securityContext": {
        "fsGroup": 100,
        "fsGroupChangePolicy": "OnRootMismatch",
    },
}


# ---------------------------------------------------------------------------
# Shared storage (RWX PVC for group directories)
# ---------------------------------------------------------------------------
# When enabled, mount the shared-storage PVC so users can collaborate via
# /shared/<group>. Group-specific subPaths and init containers are added
# dynamically in _setup_shared_storage() at spawn time based on group membership.
shared_storage_enabled = get_chart_config("shared-storage-enabled", False)
shared_storage_groups_allowlist = get_config("custom.shared-storage-groups", [])
shared_storage_mount_prefix = get_config("custom.shared-storage-mount-prefix", "/shared")

if shared_storage_enabled:
    c.KubeSpawner.volumes.append({
        "name": "shared",
        "persistentVolumeClaim": {"claimName": "shared-storage"},
    })

# ---------------------------------------------------------------------------
# Nebi binary (init container)
# ---------------------------------------------------------------------------
# Copy the nebi binary from the nebi server image into the JupyterLab pod
# so the version matches what the deployment specifies, not what was baked
# into the jupyterlab image at build time.
nebi_image = get_chart_config("nebi-image")
if nebi_image:
    c.KubeSpawner.volumes.append({
        "name": "nebi-bin",
        "emptyDir": {},
    })
    c.KubeSpawner.volume_mounts.append({
        "name": "nebi-bin",
        "mountPath": "/usr/local/bin/nebi",
        "subPath": "nebi",
    })
    c.KubeSpawner.init_containers.append({
        "name": "install-nebi",
        "image": nebi_image,
        "command": ["sh", "-c", "cp /app/nebi /nebi-bin/nebi && chmod +x /nebi-bin/nebi"],
        "imagePullPolicy": get_config("custom.nebi-image-pull-policy", "IfNotPresent"),
        "volumeMounts": [{
            "name": "nebi-bin",
            "mountPath": "/nebi-bin",
        }],
    })


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
# Start with extraEnv from values.yaml so deployers can inject env vars
# (e.g. MLFLOW_TRACKING_URI) without modifying this config file.
env = dict(get_config("singleuser.extraEnv", {}))
env["HOME"] = "/home/jovyan"

nebi_remote_url = get_chart_config("nebi-remote-url")
if nebi_remote_url:
    env["NEBI_REMOTE_URL"] = nebi_remote_url

env["NEBI_STORAGE_WORKSPACES_DIR"] = "/var/lib/nebi/workspaces"

c.KubeSpawner.environment = env


# ---------------------------------------------------------------------------
# Profiles (resource sizing)
# ---------------------------------------------------------------------------
# Each profile maps directly to a KubeSpawner profile_list entry. The
# optional ``slug`` key gives a stable identifier independent of the
# human-facing ``display_name``; kubespawner falls back to slugifying
# ``display_name`` when ``slug`` is omitted.
# ``kubespawner_override`` accepts any valid KubeSpawner trait so deployers
# can add node_selector, image, extra_resource_limits (GPU), etc. without
# code changes. Empty list = no profile selector (single-instance mode).
# Keys used only for group gating; KubeSpawner must never see them.
_PROFILE_GATING_KEYS = ("access", "groups", "users")


def _get_profile_groups(auth_state):
    """Resolve the user's full Keycloak group list for profile gating.

    Unlike _get_user_groups (shared-storage), this uses the user's COMPLETE
    group list — not the mount-role gated subset and not the shared-storage
    allowlist — so profile visibility does not depend on shared-storage RBAC
    being deployed. Group names are normalised to the leaf segment
    (/projects/foo -> foo) and deduplicated, matching classic Nebari.
    """
    if not auth_state:
        return []
    raw_groups = auth_state.get("groups")
    if raw_groups is None:
        raw_groups = (auth_state.get("oauth_user") or {}).get("groups", [])

    seen = set()
    result = []
    for g in raw_groups or []:
        name = Path(g).name
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _get_keycloak_profile_slugs(auth_state):
    """Profile slugs allowed via ``access: keycloak``.

    Read from ``auth_state["allowed_jupyterlab_profiles"]``, which the
    authenticator resolves at login from the user's ``jupyterlab-profiles``
    Keycloak role (its ``profiles`` attribute) on the hub client.
    """
    return (auth_state or {}).get("allowed_jupyterlab_profiles", []) or []


def _profile_username(auth_state):
    """Username matched against a profile's ``users`` list.

    Uses the Keycloak ``preferred_username`` (mirrors classic Nebari) so it
    resolves identically in the spawner and in jhub-apps' fake-spawner path,
    where ``spawner.user.name`` is a Mock rather than a real username.
    """
    oauth_user = (auth_state or {}).get("oauth_user") or {}
    return oauth_user.get("preferred_username") or ""


def _filter_profiles(profiles, groups, username, keycloak_profile_slugs=()):
    """Return the profiles a user may select, stripped of gating-only keys.

    Mirrors classic Nebari's ``access:`` semantics on each profile:
      * ``access: all`` (or omitted) — visible to everyone.
      * ``access: yaml`` — visible only if the user is in the profile's
        ``users`` list or shares one of the profile's ``groups``.
      * ``access: keycloak``: visible only if the profile's ``slug`` is in
        the allow-list granted by the user's ``jupyterlab-profiles`` Keycloak
        role (its ``profiles`` attribute), resolved at login by the
        authenticator and stamped into auth_state.
    """
    group_set = set(groups)
    profile_slug_set = set(keycloak_profile_slugs)
    visible = []
    for profile in profiles:
        access = profile.get("access", "all")
        if access == "yaml":
            in_users = username in set(profile.get("users") or [])
            in_groups = bool(group_set & set(profile.get("groups") or []))
            if not in_users and not in_groups:
                continue
        elif access == "keycloak":
            if profile.get("slug") not in profile_slug_set:
                continue
        elif access != "all":
            # Fail closed on an unrecognized access mode: restricted profiles
            # gate expensive resources, so a typo must hide the profile rather
            # than expose it to everyone.
            log.warning(
                "profiles: hiding %r — unsupported access mode %r "
                "(use 'all', 'yaml', or 'keycloak')",
                profile.get("slug") or profile.get("display_name"),
                access,
            )
            continue
        clean = {k: v for k, v in profile.items() if k not in _PROFILE_GATING_KEYS}
        visible.append(clean)
    return visible


async def _render_profile_list(spawner):
    """Per-user profile_list callable — filters profiles by group membership.

    KubeSpawner (and jhub-apps' server-types endpoint) accept an async callable
    here and invoke it with the spawner at selection time. We resolve the
    user's Keycloak groups from auth_state and return only the profiles they
    are allowed to see, with gating-only keys stripped.
    """
    auth_state = await spawner.user.get_auth_state()
    groups = _get_profile_groups(auth_state)
    username = _profile_username(auth_state)
    keycloak_profile_slugs = _get_keycloak_profile_slugs(auth_state)
    visible = _filter_profiles(_profiles, groups, username, keycloak_profile_slugs)
    log.info(
        "profiles: user %s (groups=%s) sees %d/%d profile(s): %s",
        username, groups, len(visible), len(_profiles),
        [p.get("slug") or p.get("display_name") for p in visible],
    )
    return visible


_profiles = get_config("custom.profiles", [])
if _profiles:
    c.KubeSpawner.profile_list = _render_profile_list
    log.info(
        "profiles: loaded %d profile(s): %s",
        len(_profiles),
        [p.get("slug") or p.get("display_name") for p in _profiles],
    )
else:
    log.info("profiles: none configured — single-instance mode")


# ---------------------------------------------------------------------------
# Keycloak token exchange helpers (synchronous, shared)
# ---------------------------------------------------------------------------
# These synchronous functions are the single implementation of the 3-step
# Keycloak → Nebi token exchange.  They are used by:
#   - _nebi_pre_spawn_hook (below) via asyncio.to_thread()
#   - 03-nebi-envs.py directly (jhub-apps calls conda_envs synchronously)
#
# Using urllib.request (not Tornado AsyncHTTPClient) so the same code works
# in both sync and async contexts.


def _extract_error_body(exc):
    """Extract HTTP response body from an exception for diagnostic logging.

    urllib.error.HTTPError exposes the body via .read(); other exceptions
    do not.  Returns an empty string if the body cannot be read.
    """
    if hasattr(exc, "read"):
        try:
            return exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _decode_jwt_claims(token):
    """Decode JWT payload without verification, for diagnostic logging only.

    Returns a dict of claims or {} if the token is not a valid JWT.
    """
    import base64
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _log_token_diagnostics(label, token):
    """Log JWT claims relevant to token exchange debugging."""
    claims = _decode_jwt_claims(token)
    if not claims:
        log.warning(
            "token-exchange: %s token is not a JWT (opaque token, len=%d)",
            label, len(token),
        )
        return
    import time
    exp = claims.get("exp", 0)
    now = int(time.time())
    log.info(
        "token-exchange: %s token — iss=%s, aud=%s, azp=%s, exp=%s (%s), sub=%s, scope=%s",
        label,
        claims.get("iss", "<missing>"),
        claims.get("aud", "<missing>"),
        claims.get("azp", "<missing>"),
        exp,
        "EXPIRED" if exp and exp < now else f"valid for {exp - now}s",
        claims.get("sub", "<missing>"),
        claims.get("scope", "<missing>"),
    )


def _sync_refresh_access_token(refresh_token, keycloak_url, hub_client_id, hub_client_secret):
    """Use the refresh token to get a fresh access token from Keycloak.

    Returns the new access token string, or empty string on failure.
    """
    log.info("token-exchange step 1: refreshing access token (url=%s, client_id=%s)", keycloak_url, hub_client_id)
    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": hub_client_id,
        "client_secret": hub_client_secret,
    }).encode("utf-8")
    req = Request(
        keycloak_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            token = data.get("access_token", "")
            if token:
                log.info("token-exchange step 1: refresh succeeded")
                _log_token_diagnostics("refreshed access", token)
            else:
                log.error("token-exchange step 1: refresh response had no access_token (keys: %s)", list(data.keys()))
            return token
    except Exception as exc:
        resp_body = _extract_error_body(exc)
        log.error(
            "token-exchange step 1 FAILED: %s response=%s (url=%s, client_id=%s)",
            exc, resp_body, keycloak_url, hub_client_id,
        )
        return ""


def _sync_exchange_access_token_for_nebi_id_token(
    access_token, keycloak_url, nebi_client_id, hub_client_id, hub_client_secret,
):
    """Exchange a JupyterHub access token for a Nebi-audience ID token via Keycloak.

    Returns the Nebi ID token string, or empty string on failure.
    """
    log.info(
        "token-exchange step 2: exchanging access token for nebi ID token "
        "(url=%s, audience=%s, client_id=%s)",
        keycloak_url, nebi_client_id, hub_client_id,
    )
    body = urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": access_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": nebi_client_id,
        "client_id": hub_client_id,
        "client_secret": hub_client_secret,
    }).encode("utf-8")
    req = Request(
        keycloak_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            # Prefer id_token if present (has user identity claims), fall back to access_token
            token = data.get("id_token") or data.get("access_token", "")
            if token:
                log.info("token-exchange step 2: exchange succeeded (got %s)", "id_token" if data.get("id_token") else "access_token")
                _log_token_diagnostics("exchanged nebi", token)
            else:
                log.error("token-exchange step 2: exchange response had no id_token or access_token (keys: %s)", list(data.keys()))
            return token
    except Exception as exc:
        resp_body = _extract_error_body(exc)
        log.error(
            "token-exchange step 2 FAILED: %s response=%s (url=%s, audience=%s, client_id=%s)",
            exc, resp_body, keycloak_url, nebi_client_id, hub_client_id,
        )
        return ""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following redirects (matches Tornado follow_redirects=False)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, msg, headers, fp)


def _sync_exchange_nebi_id_token_for_jwt(nebi_id_token, nebi_internal_url):
    """Exchange a Nebi-audience ID token for a Nebi JWT via the session endpoint.

    Uses a no-redirect opener so auth failures surface as errors instead of
    silently following a redirect to a login page.
    Returns the Nebi JWT string, or empty string on failure.
    """
    session_url = f"{nebi_internal_url.rstrip('/')}/api/v1/auth/session"
    log.info("token-exchange step 3: exchanging nebi ID token for JWT (url=%s)", session_url)
    req = Request(
        session_url,
        headers={"Cookie": f"IdToken={nebi_id_token}"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(req, timeout=10) as resp:
            data = json.loads(resp.read())
            token = data.get("token", "")
            if token:
                log.info("token-exchange step 3: nebi JWT obtained (len=%d)", len(token))
            else:
                log.error("token-exchange step 3: nebi session response had no 'token' key (keys: %s)", list(data.keys()))
            return token
    except Exception as exc:
        resp_body = _extract_error_body(exc)
        log.error(
            "token-exchange step 3 FAILED: %s response=%s (url=%s)",
            exc, resp_body, session_url,
        )
        return ""


def get_nebi_jwt(refresh_token, access_token, keycloak_url, nebi_cid, hub_cid, hub_secret, nebi_url):
    """Run the full 3-step token exchange and return a Nebi JWT.

    This is a synchronous, self-contained helper used by both the spawner hook
    (via asyncio.to_thread) and the jhub-apps environment listing callable
    (03-nebi-envs.py).

    Returns the Nebi JWT string, or empty string on any failure.
    """
    log.info("token-exchange: starting 3-step exchange (keycloak=%s, nebi=%s)", keycloak_url, nebi_url)

    # Step 1: Refresh the access token (preferred) or use the existing one
    if refresh_token:
        access_token = _sync_refresh_access_token(
            refresh_token, keycloak_url, hub_cid, hub_secret,
        )
        if not access_token:
            log.error("token-exchange: aborting — step 1 (refresh) returned no token")
            return ""
    elif access_token:
        log.info("token-exchange: no refresh_token, using existing access_token")
        _log_token_diagnostics("existing access", access_token)
    else:
        log.error("token-exchange: aborting — no refresh_token and no access_token")
        return ""

    # Step 2: Exchange access token for Nebi-audience ID token
    nebi_id_token = _sync_exchange_access_token_for_nebi_id_token(
        access_token, keycloak_url, nebi_cid, hub_cid, hub_secret,
    )
    if not nebi_id_token:
        log.error("token-exchange: aborting — step 2 (exchange) returned no token")
        return ""

    # Step 3: Exchange Nebi ID token for Nebi JWT
    nebi_jwt = _sync_exchange_nebi_id_token_for_jwt(nebi_id_token, nebi_url)
    if not nebi_jwt:
        log.error("token-exchange: aborting — step 3 (nebi session) returned no token")
        return ""

    log.info("token-exchange: all 3 steps succeeded")
    return nebi_jwt


# ---------------------------------------------------------------------------
# Nebi auto-authentication (pre-spawn hook)
# ---------------------------------------------------------------------------


async def _nebi_pre_spawn_hook(spawner):
    """Authenticate the user with the remote Nebi server at spawn time.

    Exchanges the user's Keycloak token for a Nebi JWT and injects it as
    NEBI_AUTH_TOKEN into the pod environment.
    Non-fatal: if any step fails, the pod still spawns without auto-auth.
    """
    # Tell jhub-app-proxy to use pixi activation (instead of conda) for app pods.
    # Set this first, before any early returns, because pixi activation is needed
    # regardless of whether the token exchange succeeds — the nebi binary in the
    # pod handles workspace pull and pixi env activation independently.
    spawner.environment = {**spawner.environment, "JHUB_APP_ENV_MANAGER": "pixi"}

    auth_state = await spawner.user.get_auth_state()
    if not auth_state:
        log.warning("No auth_state for %s, skipping Nebi auto-auth", spawner.user.name)
        return

    keycloak_url = get_chart_config("keycloak-token-url")
    nebi_cid = get_chart_config("nebi-client-id")
    hub_cid = get_chart_config("jupyterhub-client-id")
    hub_secret = os.environ.get("JUPYTERHUB_OIDC_CLIENT_SECRET", "")
    nebi_url = get_chart_config("nebi-internal-url")

    if not all([keycloak_url, nebi_cid, hub_cid, hub_secret, nebi_url]):
        log.warning("Nebi auto-auth not fully configured, skipping")
        return

    try:
        refresh_token = auth_state.get("refresh_token")
        access_token = auth_state.get("access_token") or ""

        if not refresh_token and not access_token:
            log.warning("No access_token or refresh_token for %s, skipping", spawner.user.name)
            return

        # Run the synchronous token exchange in a thread to avoid blocking
        # the Tornado event loop.
        nebi_jwt = await asyncio.to_thread(
            get_nebi_jwt,
            refresh_token, access_token,
            keycloak_url, nebi_cid, hub_cid, hub_secret, nebi_url,
        )
        if not nebi_jwt:
            log.warning("Nebi token exchange returned no JWT for %s", spawner.user.name)
            return

        spawner.environment = {**spawner.environment, "NEBI_AUTH_TOKEN": nebi_jwt}
        log.info("Nebi auto-auth succeeded for %s", spawner.user.name)

        # If a nebi workspace is selected (via jhub-apps environment selector),
        # add an init container to pull it into an ephemeral volume.
        # Each app pod gets its own emptyDir at /tmp/nebi-env so multiple apps
        # don't overwrite each other's workspace files on the shared user PVC.
        conda_env = getattr(spawner, "user_options", {}).get("conda_env", "")
        if conda_env:
            workspace_name = conda_env.rsplit("/", 1)[-1] if "/" in conda_env else conda_env
            nebi_remote = get_chart_config("nebi-remote-url")
            nebi_env_dir = "/tmp/nebi-env"
            ws_dir = f"{nebi_env_dir}/workspace"
            log.info(
                "Adding nebi-pull init container for workspace %s (user %s)",
                workspace_name, spawner.user.name,
            )

            # Add ephemeral volume for this pod's workspace files + nebi DB
            spawner.volumes = list(spawner.volumes) + [{
                "name": "nebi-env",
                "emptyDir": {},
            }]
            # Mount in main container so nebi workspace list + pixi run can find it
            spawner.volume_mounts = list(spawner.volume_mounts) + [{
                "name": "nebi-env",
                "mountPath": nebi_env_dir,
            }]
            # Tell nebi in the main container to use the ephemeral DB
            spawner.environment = {
                **spawner.environment,
                "NEBI_DATA_DIR": nebi_env_dir,
            }

            spawner.init_containers = list(spawner.init_containers) + [{
                "name": "nebi-pull",
                "image": spawner.image,
                "workingDir": ws_dir,
                "command": [
                    "/bin/sh", "-c",
                    # Pull workspace files into the ephemeral dir, then
                    # pre-install the pixi environment so jhub-app-proxy's
                    # `pixi run` doesn't hit the ready-check timeout.
                    f"mkdir -p {ws_dir} && "
                    f"nebi pull {workspace_name} -o {ws_dir} --force && "
                    f"pixi install --manifest-path {ws_dir}/pixi.toml && "
                    f"chmod -R a+rw {nebi_env_dir}/nebi.db* || "
                    f"echo 'WARNING: nebi pull or pixi install failed for {workspace_name}'",
                ],
                "env": [
                    {"name": "HOME", "value": nebi_env_dir},
                    {"name": "NEBI_DATA_DIR", "value": nebi_env_dir},
                    {"name": "NEBI_AUTH_TOKEN", "value": nebi_jwt},
                    {"name": "NEBI_REMOTE_URL", "value": nebi_remote},
                ],
                "volumeMounts": [
                    {"name": "nebi-bin", "mountPath": "/usr/local/bin/nebi", "subPath": "nebi"},
                    {"name": "nebi-env", "mountPath": nebi_env_dir},
                ],
            }]
    except Exception:
        log.exception("Nebi auto-auth failed for %s (pod will still spawn)", spawner.user.name)


# ---------------------------------------------------------------------------
# Shared storage hook helpers
# ---------------------------------------------------------------------------

def _get_user_groups(auth_state):
    """Extract and filter user groups from auth_state.

    Source priority:
      1. ``auth_state["groups_with_permission_to_mount"]`` if present —
         this is the role-gated subset stamped by KeyCloakOAuthenticator
         when RBAC is enabled. Being in 10 KC groups should NOT mean
         mounting 10 shared dirs; only those holding the
         ``shared-directory-mount`` role on the hub client get to mount.
      2. ``auth_state["groups"]`` — the user's full group list, used as
         a fallback for clusters that have not yet deployed the RBAC
         bootstrap (legacy / chart-default behaviour).

    Then applies the chart's static allowlist, Path(g).name normalisation
    (so /projects/myproj → myproj), and dedup.

    Note: does NOT fall back to spawner.user.groups — accessing that
    SQLAlchemy relationship from an async hook causes
    DetachedInstanceError.
    """
    raw_groups = []
    if auth_state:
        if "groups_with_permission_to_mount" in auth_state:
            raw_groups = auth_state["groups_with_permission_to_mount"]
            log.debug(
                "shared-storage: role-gated groups from auth_state: %s",
                raw_groups,
            )
        else:
            raw_groups = auth_state.get("groups", [])
            log.debug(
                "shared-storage: fallback groups from auth_state (no RBAC filter): %s",
                raw_groups,
            )
    else:
        log.debug("shared-storage: no auth_state (DummyAuthenticator?), groups will be empty")

    if shared_storage_groups_allowlist:
        before = raw_groups
        raw_groups = [g for g in raw_groups if g in shared_storage_groups_allowlist]
        log.debug(
            "shared-storage: allowlist %s filtered %s → %s",
            shared_storage_groups_allowlist, before, raw_groups,
        )

    seen = set()
    result = []
    for g in raw_groups:
        name = Path(g).name
        if not name:
            log.debug("shared-storage: skipping empty group name from %r", g)
            continue
        if name in seen:
            log.debug("shared-storage: deduplicating group %r (already have it)", name)
            continue
        seen.add(name)
        result.append(name)

    log.info("shared-storage: resolved groups for user: %s", result)
    return result


async def _setup_shared_storage(spawner, groups):
    """Add per-group volume mounts and a single init container for shared directories.

    Creates /shared/<group> on the PVC with:
    - chown 0:100 so group owner is GID 100 (users), matching pod fs_gid
    - chmod 2775 (rwxrwsr-x) so group has write and setgid propagates GID to new files
    Combined with NB_UMASK=0002, new files are group-writable (664/775).
    """
    log.info(
        "shared-storage: setting up PVC mounts for user %s, groups: %s",
        spawner.user.name, groups,
    )
    for group in groups:
        spawner.volume_mounts = list(spawner.volume_mounts) + [{
            "mountPath": f"{shared_storage_mount_prefix}/{group}",
            "name": "shared",
            "subPath": f"shared/{group}",
        }]
        log.debug("shared-storage: added volume mount for group %r", group)

    # chown 0:100 sets GID 100 (users group) as owner, matching the pod's fs_gid=100.
    # chmod 2775: group has rwx + setgid bit so new files inherit GID 100.
    mkdir_cmds = " && ".join([
        f"mkdir -p /mnt/shared/{g} && chown 0:100 /mnt/shared/{g} && chmod 2775 /mnt/shared/{g}"
        for g in groups
    ])
    spawner.init_containers = list(spawner.init_containers) + [{
        "name": "initialize-shared-mounts",
        "image": "busybox:1.31",
        "command": ["sh", "-c", mkdir_cmds],
        "securityContext": {"runAsUser": 0},
        "volumeMounts": [
            {
                "mountPath": f"/mnt/shared/{g}",
                "name": "shared",
                "subPath": f"shared/{g}",
            }
            for g in groups
        ],
    }]
    log.info(
        "shared-storage: added initialize-shared-mounts init container for groups: %s",
        groups,
    )


def _generate_nss_files(username, uid=1000, gid=1000):
    """Generate /tmp/passwd and /tmp/group content for libnss_wrapper.

    Maps uid 1000 to the real username so whoami/id show the correct name.
    Home dir is /home/jovyan to match the actual PVC mount point.
    GID 1000 (jovyan) matches the pod's primary GID from the container image.
    GID 100 (users) is included as a supplemental group for shared dir access.
    Additional supplemental GIDs suppress 'missing GID' warnings at startup.
    """
    passwd = f"{username}:x:{uid}:{gid}:{username}:/home/jovyan:/bin/bash"
    additional_gids = [4, 20, 24, 25, 27, 29, 30, 44, 46]
    group_lines = [
        f"jovyan:x:{gid}:",    # primary group — matches pod securityContext GID 1000
        "users:x:100:",         # supplemental — needed for shared dir write access
    ] + [f"nogroup{g}:x:{g}:" for g in additional_gids]
    return passwd, "\n".join(group_lines)


async def _setup_nss_wrapper(spawner, username, groups):
    """Configure libnss_wrapper so whoami/id report the real username.

    Sets LD_PRELOAD, NSS_WRAPPER_* paths, and NB_UMASK=0002.
    Adds a postStart lifecycle hook that:
    - writes /tmp/passwd and /tmp/group using printf (safe for special chars in username)
    - when shared PVC is enabled: symlinks ~/shared → PVC mount prefix
    - when shared PVC is disabled: creates local ~/shared/<group> dirs so
      users can see their group directories (not cross-user shared, but visible)
    Merges into existing lifecycle_hooks to avoid overwriting hooks set elsewhere.
    """
    log.info(
        "nss-wrapper: configuring for user %s (groups=%s, shared_storage=%s)",
        username, groups, shared_storage_enabled,
    )

    etc_passwd, etc_group = _generate_nss_files(username)
    log.debug("nss-wrapper: passwd entry: %s", etc_passwd)

    spawner.environment = {
        **spawner.environment,
        "LD_PRELOAD": "libnss_wrapper.so",
        "NSS_WRAPPER_PASSWD": "/tmp/passwd",
        "NSS_WRAPPER_GROUP": "/tmp/group",
        "NB_UMASK": "0002",
    }
    log.debug("nss-wrapper: LD_PRELOAD and NSS_WRAPPER_* set in spawner environment")

    # Use printf instead of echo '...' to safely handle special characters in
    # usernames (e.g. '@', colons) without shell quoting issues. /tmp/group
    # is multi-line: pass each entry as a separate printf argument so
    # `'%s\n'` is reused per arg and produces real newlines between entries.
    # A single-arg `printf '%s\n' 'a\nb\nc'` would NOT split — the inner
    # `\n` is a literal backslash-n inside a single-quoted shell string,
    # libnss_wrapper would see one malformed group entry, and getgrgid()
    # would fail for every supplementary GID.
    etc_group_lines = etc_group.split("\n")
    nss_cmds = [
        f"printf '%s\\n' {etc_passwd!r} > /tmp/passwd",
        "printf '%s\\n' "
        + " ".join(repr(line) for line in etc_group_lines)
        + " > /tmp/group",
    ]

    # Group membership changes between spawns (gain, lose, swap) are a
    # normal operational scenario. The home PVC persists, so the shape
    # `~/shared` took on the LAST spawn is still there at the start of
    # THIS spawn. We must reconcile WITHOUT touching user data: the
    # only thing safe to delete unconditionally is a symlink, because
    # the actual data it points at lives elsewhere (either on the RWX
    # shared PVC mounted at /shared, or — in the chart's RWO-only
    # fallback — in per-group dirs that the spawner mkdir-ed but never
    # wiped). Real directories at ~/shared may hold user files; never
    # blow them away.
    #
    #   groups + shared_storage_enabled: chart wants a symlink to /shared.
    #     A prior real-dir at ~/shared (e.g. from an earlier
    #     shared_storage=false spawn) would block `ln -sfn`. The dir's
    #     contents are stale local placeholders — the live data is at
    #     /shared/<group>. Safe to wipe the dir AND any prior symlink.
    #
    #   groups + !shared_storage_enabled: per-group local dirs live IN
    #     the home PVC at ~/shared/<group>. Users may have written to
    #     them. Preserve everything; just remove any pre-existing
    #     symlink (left over from a shared_storage=true era) so the
    #     subsequent `mkdir -p ~/shared` doesn't follow a dangling link.
    #
    #   no groups: the original failure mode — user lost group
    #     membership, prior symlink to /shared dangles, `mkdir -p`
    #     followed it and errored with "File exists" exit 1, kubelet
    #     killed the container, CrashLoop. Fix: remove the symlink and
    #     create nothing. Preserve any real dir (may hold the user's
    #     last-known-good data from a previous shared_storage=false
    #     spawn) so re-joining a group later sees it.
    REMOVE_SYMLINK_ONLY = (
        "[ -L /home/jovyan/shared ] && rm /home/jovyan/shared; true"
    )
    if groups and shared_storage_enabled:
        log.debug("nss-wrapper: symlinking ~/shared → %s (PVC-backed)", shared_storage_mount_prefix)
        # rm -rf is safe here: ~/shared is either a symlink (deletes
        # only the link), an empty placeholder dir (no data), or stale
        # per-group dirs whose live data is at /shared/<group>.
        nss_cmds.append("rm -rf /home/jovyan/shared")
        nss_cmds.append(f"ln -sfn {shared_storage_mount_prefix} /home/jovyan/shared")
    elif groups:
        log.debug("nss-wrapper: creating local ~/shared/<group> dirs (no PVC): %s", groups)
        nss_cmds.append(REMOVE_SYMLINK_ONLY)
        nss_cmds.append("mkdir -p /home/jovyan/shared")
        for group in groups:
            nss_cmds.append(f"mkdir -p /home/jovyan/shared/{group}")
    else:
        log.debug("nss-wrapper: no groups — clearing any stale symlink")
        nss_cmds.append(REMOVE_SYMLINK_ONLY)

    # Merge into existing lifecycle_hooks rather than replacing, so other
    # hooks (e.g. from future jhub-apps versions) are not silently overwritten.
    existing = dict(getattr(spawner, "lifecycle_hooks", None) or {})
    if "postStart" in existing:
        log.warning(
            "nss-wrapper: existing postStart lifecycle hook found for %s — overwriting",
            username,
        )
    spawner.lifecycle_hooks = {
        **existing,
        "postStart": {"exec": {"command": ["/bin/sh", "-c", " && ".join(nss_cmds)]}},
    }
    log.info("nss-wrapper: postStart lifecycle hook registered for %s", username)


# ---------------------------------------------------------------------------
# Workspace PVC helper
# ---------------------------------------------------------------------------

async def _ensure_workspace_pvc(spawner):
    """Create a per-user RWO PVC for nebi workspaces if it doesn't exist.

    KubeSpawner only manages one PVC natively (the home directory).  We
    create the second PVC here via the Kubernetes API, mirroring the same
    retry logic KubeSpawner uses internally.
    """
    username = spawner.user.name
    safe_chars = set(string.ascii_lowercase + string.digits)
    slug = escapism.escape(username, safe=safe_chars, escape_char='-').lower()
    pvc_name = f"nebi-workspaces-{slug}"
    namespace = spawner.namespace

    storage_class = get_config("custom.workspace-storage-class", "") or None
    storage_capacity = get_config("custom.workspace-storage-capacity", "20Gi")

    pvc = make_pvc(
        name=pvc_name,
        storage_class=storage_class,
        access_modes=["ReadWriteOnce"],
        selector=None,
        storage=storage_capacity,
        labels={
            "app": "nebi-workspaces",
            "hub.jupyter.org/username": slug,
        },
    )

    try:
        await spawner.api.create_namespaced_persistent_volume_claim(
            namespace=namespace,
            body=pvc,
        )
        log.info("Created workspace PVC %s in namespace %s", pvc_name, namespace)
    except ApiException as exc:
        if exc.status == 409:
            log.info("Workspace PVC %s already exists", pvc_name)
        else:
            log.error("Failed to create workspace PVC %s: %s", pvc_name, exc)
            raise

    return pvc_name


# ---------------------------------------------------------------------------
# External auth pack integration
# ---------------------------------------------------------------------------

def _external_auth_provider_token(broker_url, provider, bearer_token):
    """Fetch one provider token from the external-auth broker."""
    req = Request(
        f"{broker_url.rstrip('/')}/external-auth/{quote(provider)}/token",
        headers={"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"},
        method="GET",
    )
    with urlopen(req, timeout=external_auth_timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _append_post_start_commands(spawner, commands):
    """Append shell commands to the pod postStart hook."""
    if not commands:
        return

    existing = dict(getattr(spawner, "lifecycle_hooks", None) or {})
    post_start = existing.get("postStart", {})
    command = post_start.get("exec", {}).get("command") if isinstance(post_start, dict) else None
    new_script = " && ".join(commands)

    if command and len(command) == 3 and command[:2] == ["/bin/sh", "-c"]:
        new_script = f"{command[2]} && {new_script}"
    elif command:
        log.warning("postStart lifecycle hook has unsupported shape; replacing it")

    spawner.lifecycle_hooks = {
        **existing,
        "postStart": {"exec": {"command": ["/bin/sh", "-c", new_script]}},
    }


def _configure_git_for_github_token(spawner):
    """Teach git to use the injected GITHUB_TOKEN for normal GitHub HTTPS URLs."""
    helper = (
        '!f() { test "$1" = get || exit 0; '
        'while IFS= read -r line; do test -z "$line" && break; done; '
        'test -n "${GITHUB_TOKEN:-}" || exit 0; '
        'printf "username=x-access-token\\npassword=%s\\n" "$GITHUB_TOKEN"; }; f'
    )
    _append_post_start_commands(
        spawner,
        [
            "if command -v git >/dev/null 2>&1 && [ -n \"${GITHUB_TOKEN:-}\" ]; then "
            "git config --global credential.https://github.com.username x-access-token && "
            f"git config --global credential.https://github.com.helper {shlex.quote(helper)} && "
            "git config --global url.https://github.com/.insteadOf git@github.com:; "
            "fi",
        ],
    )


async def _external_auth_pre_spawn_hook(spawner, auth_state):
    """Deliver external-auth provider tokens from configured contract fields."""
    if not external_auth_enabled or not external_auth_broker_url:
        return
    access_token = (auth_state or {}).get("access_token")
    if not access_token:
        log.debug("external-auth: no access_token for %s; skipping", spawner.user.name)
        return

    providers = external_auth_providers or list(external_auth_env_var_map)
    for provider in providers:
        try:
            payload = await asyncio.to_thread(
                _external_auth_provider_token,
                external_auth_broker_url,
                provider,
                access_token,
            )
        except urllib.error.HTTPError as exc:
            log.debug("external-auth: token request failed provider=%s status=%s", provider, exc.code)
            continue
        except Exception as exc:
            log.debug("external-auth: token request failed provider=%s error=%s", provider, exc)
            continue

        token = str(payload.get("access_token", "")).strip()
        if payload.get("status") != "token_valid" or not token:
            continue

        env_var = str(external_auth_env_var_map.get(provider) or f"{provider.upper()}_TOKEN").strip()
        spawner.environment = {**spawner.environment, env_var: token}
        if provider == "github":
            spawner.environment = {**spawner.environment, "GH_TOKEN": token}
            _configure_git_for_github_token(spawner)


# ---------------------------------------------------------------------------
# Pre-spawn hook orchestrator
# ---------------------------------------------------------------------------
# Chains the independent concerns: 
# 1. Nebi auto-auth
# 2. workspace PVC
# 3. Resolve groups
# 5. shared storage mounts
# 4. NSS wrapper setup
# 
# Each is implemented as its own focused function above. The orchestrator always
# runs so NSS wrapper is active even without Nebi/shared.

_nebi_auth_configured = bool(nebi_remote_url and get_chart_config("nebi-internal-url"))
log.info(
    "pre-spawn: Nebi auth configured=%s, shared storage enabled=%s, mount prefix=%s",
    _nebi_auth_configured, shared_storage_enabled, shared_storage_mount_prefix,
)


async def _pre_spawn_hook(spawner):
    """Orchestrate all pre-spawn setup: Nebi auth, shared storage, NSS wrapper."""
    username = spawner.user.name
    log.info("pre-spawn: starting hook for user %s", username)

    auth_state = await spawner.user.get_auth_state()
    if not auth_state:
        log.warning("pre-spawn: no auth_state for %s (DummyAuthenticator or auth state disabled)", username)
    else:
        log.debug("pre-spawn: auth_state keys for %s: %s", username, list(auth_state.keys()))

    # Expose PREFERRED_USERNAME for the singleuser pod (matches classic
    # nebari's 02-spawner.get_username_hook). The terminal prompt and any
    # downstream tooling read this env var.
    oauth_user = (auth_state or {}).get("oauth_user") or {}
    preferred_username = oauth_user.get("preferred_username") or username
    spawner.environment = {**spawner.environment, "PREFERRED_USERNAME": preferred_username}

    # 1. Nebi auto-auth (non-fatal)
    if _nebi_auth_configured:
        log.debug("pre-spawn: running Nebi auto-auth for %s", username)
        await _nebi_pre_spawn_hook(spawner)
    else:
        log.debug("pre-spawn: Nebi auto-auth not configured, skipping")

    # 2. Workspace PVC — per-user RWO (non-fatal)
    log.debug("pre-spawn: ensuring workspace PVC for %s", username)
    try:
        workspace_pvc_name = await _ensure_workspace_pvc(spawner)
        workspaces_volume = {
            "name": "nebi-workspaces",
            "persistentVolumeClaim": {"claimName": workspace_pvc_name},
        }
        workspaces_volume_mount = {
            "name": "nebi-workspaces",
            "mountPath": "/var/lib/nebi/workspaces",
        }
        log.info("pre-spawn: workspace PVC %s configured for %s", workspace_pvc_name, username)
    except Exception:
        log.exception("pre-spawn: workspace PVC setup FAILED for %s", username)
        workspaces_volume = workspaces_volume_mount = None
    if workspaces_volume is not None:
        spawner.volumes.append(workspaces_volume)
    if workspaces_volume_mount is not None:
        spawner.volume_mounts.append(workspaces_volume_mount)

    # 3. Resolve groups from auth_state (stored by KeyCloakOAuthenticator)
    groups = _get_user_groups(auth_state)
    log.info("pre-spawn: user %s resolved groups: %s", username, groups)

    # 4. Shared group directory PVC mounts (only when RWX PVC is configured)
    if shared_storage_enabled:
        if groups:
            log.info("pre-spawn: setting up shared storage mounts for %s", username)
            try:
                await _setup_shared_storage(spawner, groups)
                log.info("pre-spawn: shared storage mounts configured for %s", username)
            except Exception:
                log.exception(
                    "pre-spawn: shared storage setup FAILED for %s (pod will still spawn)",
                    username,
                )
        else:
            log.info(
                "pre-spawn: shared storage enabled but user %s has no groups — skipping PVC mounts",
                username,
            )
    else:
        log.debug("pre-spawn: shared storage disabled, skipping PVC mounts for %s", username)

    # 5. NSS wrapper — always runs; independently guarded so shared storage
    #    failures never prevent whoami/id from showing the real username
    log.debug("pre-spawn: running NSS wrapper setup for %s", username)
    try:
        await _setup_nss_wrapper(spawner, username, groups)
        log.info("pre-spawn: NSS wrapper configured for %s", username)
    except Exception:
        log.exception("pre-spawn: NSS wrapper setup FAILED for %s", username)

    # 6. Optional external auth provider tokens (disabled unless configured)
    try:
        await _external_auth_pre_spawn_hook(spawner, auth_state)
    except Exception:
        log.exception("pre-spawn: external-auth setup FAILED for %s", username)

    log.info("pre-spawn: hook complete for user %s", username)


c.KubeSpawner.pre_spawn_hook = _pre_spawn_hook
