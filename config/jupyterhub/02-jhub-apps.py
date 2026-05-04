"""jhub-apps integration configuration."""

# ruff: noqa: F821 - `c` is a magic global provided by JupyterHub
import os
from urllib.parse import urlparse

from kubespawner import KubeSpawner
from jhub_apps import theme_template_paths, themes
from jhub_apps.configuration import install_jhub_apps
from z2jh import get_config

# Configure jhub-apps
# bind_url must include the real external hostname so JupyterHub constructs
# correct OAuth redirect URLs for internal services like jhub-apps.
# See: nebari's 02-spawner.py for the same pattern.
external_url = (get_chart_config("external-url") or "").strip()
if external_url:
    # Accept either bare hostnames (jupyter.example.com), proper URLs
    # (https://jupyter.example.com), or common typo form (https//host).
    if external_url.startswith("https//"):
        external_url = external_url.replace("https//", "https://", 1)
    elif external_url.startswith("http//"):
        external_url = external_url.replace("http//", "http://", 1)

    parsed = urlparse(external_url if "://" in external_url else f"https://{external_url}")
    host = parsed.netloc or parsed.path
    scheme = parsed.scheme or "https"
    c.JupyterHub.bind_url = f"{scheme}://{host}"
else:
    c.JupyterHub.bind_url = "http://0.0.0.0:8000"
c.JupyterHub.default_url = "/hub/home"
c.JupyterHub.template_paths = theme_template_paths
# Match JupyterLab default (IBM Plex Sans, PR #75) on hub + JApps pages.
# Requires jhub-apps >= 2026.5.1rc1 (PR #677: font_family / font_url theme vars).
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

# Forward JUPYTERHUB_OIDC_CLIENT_SECRET to the jhub-apps subprocess so that
# 03-nebi-envs.py (which is re-evaluated inside the subprocess via
# get_jupyterhub_config()) can read it for Keycloak token exchange.
_oidc_secret = os.environ.get("JUPYTERHUB_OIDC_CLIENT_SECRET", "")
if _oidc_secret:
    for svc in c.JupyterHub.services:
        if svc.get("name") == "japps":
            svc.setdefault("environment", {})["JUPYTERHUB_OIDC_CLIENT_SECRET"] = _oidc_secret
            break
