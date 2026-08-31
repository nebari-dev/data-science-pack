"""jhub-apps integration configuration."""

# ruff: noqa: F821 - `c` is a magic global provided by JupyterHub
import os
import shlex
from urllib.parse import urlsplit, urlunsplit

from jhub_apps import theme_template_paths, themes
from jhub_apps.configuration import install_jhub_apps
from kubespawner import KubeSpawner
from z2jh import get_config


def _localhost_hub_api_url(url: str) -> str:
    """Rewrite a hub API URL's host to localhost, keeping port and path.

    jhub-apps runs as a managed-service subprocess inside the SAME pod as
    hub, but z2jh's JUPYTERHUB_API_URL points at the `hub` Service
    (ClusterIP self-reference). Routing same-pod traffic through a Service
    depends on the CNI supporting hairpin NAT for a pod reaching its own
    Service -- observed to time out (httpcore.ConnectTimeout) on a
    kind/kindnet cluster. localhost is always reliable for same-pod
    traffic regardless of hairpin NAT support.
    """
    if not url:
        return url
    parsed = urlsplit(url)
    netloc = f"localhost:{parsed.port}" if parsed.port else "localhost"
    return urlunsplit(parsed._replace(netloc=netloc))


# Configure jhub-apps
# bind_url must include the real external hostname so JupyterHub constructs
# correct OAuth redirect URLs for internal services like jhub-apps.
# See: nebari's 02-spawner.py for the same pattern.
domain = get_chart_config("external-url")
if domain:
    c.JupyterHub.bind_url = f"https://{domain}"
else:
    c.JupyterHub.bind_url = "http://0.0.0.0:8000"
c.JupyterHub.default_url = "/hub/home"
c.JupyterHub.template_paths = theme_template_paths
# Match JupyterLab default (IBM Plex Sans, PR #75) on hub + JApps pages.
# Requires jhub-apps >= 2026.6.1 for font_family / font_url theme vars.
c.JupyterHub.template_vars = {
    **themes.DEFAULT_THEME,
    "font_family": "'IBM Plex Sans', sans-serif",
    "font_url": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap",
    # Footer in page.html only renders the version string when this is truthy.
    "display_version": True,
}
c.JAppsConfig.jupyterhub_config_path = "/usr/local/etc/jupyterhub/jupyterhub_config.py"

# Apply JAppsConfig overrides from Helm values (jupyterhub.custom.japps-config).
# Any key in the dict is set as an attribute on c.JAppsConfig, e.g.:
#   japps-config:
#     app_title: "My Launcher"
#     service_workers: 2
#     allowed_frameworks: ["panel", "streamlit"]
japps_config = get_config("custom.japps-config", {})

# Auto-inject a Nebi card into additional_services when nebi-remote-url is
# set/derivable and the deployer hasn't already declared additional_services.
# Deployer override (passing additional_services in japps-config) wins.
_nebi_remote = get_chart_config("nebi-remote-url")
if _nebi_remote and "additional_services" not in japps_config:
    japps_config = {
        **japps_config,
        "additional_services": [{
            "name": "Nebi",
            "url": _nebi_remote,
            "description": "Workspace & environment management",
            "pinned": True,
            "thumbnail": (
                "https://raw.githubusercontent.com/nebari-dev/nebi/"
                "6b6cef63c67dafd7444f1a3940a0ef8f1dcebb31/assets/nebi-icon.png"
            ),
        }],
    }

# Pin jhub-app-proxy to a version that supports pixi/nebi env activation.
# Older proxies (<= v0.2.2) only do conda activation, so apps launched into a
# Nebi (pixi) environment fail to find their packages. Configurable via
# jupyterhub.custom.jhub-app-proxy-version (an explicit japps-config entry
# still wins).
japps_config.setdefault(
    "jhub_app_proxy_version",
    get_config("custom.jhub-app-proxy-version", "v0.2.3"),
)

for key, value in japps_config.items():
    setattr(c.JAppsConfig, key, value)

# Install jhub-apps (sets up service, roles, etc.)
c = install_jhub_apps(c, spawner_to_subclass=KubeSpawner)

# Extend the `user` role with the scopes the jhub-apps sharing dropdown
# ("Individuals and group access") needs: the dropdown is (other hub users +
# hub groups) filtered by the requesting user's token scopes, and without
# read:users:name / read:groups:name it is empty for every user - admins
# included (#188). This MUST extend, in place, the `user` role that
# install_jhub_apps just appended to load_roles: defining a second role via
# z2jh's hub.loadRoles (which runs before this file) makes JupyterHub abort
# startup with "Role user multiply defined". Mirrors jhub-apps' reference
# jupyterhub_config.py.
#
# NOTE: read:users:name lets any authenticated user enumerate all usernames,
# inherent to sharing by name. Opt out via
# ``jupyterhub.custom.sharing-scopes-enabled: false``.
if get_config("custom.sharing-scopes-enabled", True):
    for _role in c.JupyterHub.load_roles:
        if _role.get("name") == "user":
            _role["scopes"] = sorted(
                set(_role["scopes"])
                | {"read:users:name", "read:groups:name", "shares!user"}
            )
            break

# Forward JUPYTERHUB_OIDC_CLIENT_SECRET to the jhub-apps subprocess so that
# 03-nebi-envs.py (which is re-evaluated inside the subprocess via
# get_jupyterhub_config()) can read it for Keycloak token exchange.
_oidc_secret = os.environ.get("JUPYTERHUB_OIDC_CLIENT_SECRET", "")
if _oidc_secret:
    for svc in c.JupyterHub.services:
        if svc.get("name") == "japps":
            svc.setdefault("environment", {})["JUPYTERHUB_OIDC_CLIENT_SECRET"] = _oidc_secret
            break

# Point jhub-apps' own hub-API client at localhost instead of the `hub`
# Service it inherits from z2jh -- see _localhost_hub_api_url's docstring.
#
# Setting it via svc["environment"] (like the OIDC secret above) does NOT
# work here: JupyterHub's Spawner.get_env() computes
# env['JUPYTERHUB_API_URL'] = hub_api_url from self.hub.api_url AFTER
# merging self.environment, unconditionally overwriting whatever we set
# -- confirmed live (the resulting subprocess still saw the `hub` Service
# URL). Wrapping the service's own command with a shell-level `env
# VAR=value` assignment is the only thing that can win: it sets the
# variable for jhub-apps' uvicorn process specifically, after JupyterHub
# has already finished building the parent env.
_hub_api_url = _localhost_hub_api_url(os.environ.get("JUPYTERHUB_API_URL", ""))
if _hub_api_url:
    for svc in c.JupyterHub.services:
        if svc.get("name") == "japps" and svc.get("command"):
            quoted_cmd = " ".join(shlex.quote(part) for part in svc["command"])
            svc["command"] = [
                "sh",
                "-c",
                f"exec env JUPYTERHUB_API_URL={shlex.quote(_hub_api_url)} {quoted_cmd}",
            ]
            break
