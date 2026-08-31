"""Tests for JupyterLab real-time collaboration wiring."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import subprocess
import sys
import types

import pytest
import yaml
from traitlets.config import Config

from conftest import CONFIG_DIR, REPO_ROOT, FakeConfig

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


def _load_jhub_apps_config(
    monkeypatch,
    sharing_enabled: bool = True,
    existing_oauth_scopes=None,
    config=None,
):
    fake_jhub_apps = types.ModuleType("jhub_apps")
    fake_jhub_apps.__path__ = []
    fake_jhub_apps.theme_template_paths = []
    fake_jhub_apps.themes = types.SimpleNamespace(DEFAULT_THEME={})

    fake_jhub_apps_configuration = types.ModuleType("jhub_apps.configuration")

    def install_jhub_apps(c, spawner_to_subclass=None):
        c.JupyterHub.load_roles = [{"name": "user", "scopes": ["self"]}]
        c.JupyterHub.services = []
        return c

    fake_jhub_apps_configuration.install_jhub_apps = install_jhub_apps

    fake_kubespawner = types.ModuleType("kubespawner")
    fake_kubespawner.KubeSpawner = object

    fake_z2jh = types.ModuleType("z2jh")

    def get_config(key, default=None):
        values = {
            "custom.japps-config": {},
            "custom.jhub-app-proxy-version": "v0.2.3",
            "custom.sharing-scopes-enabled": sharing_enabled,
        }
        return values.get(key, default)

    fake_z2jh.get_config = get_config

    monkeypatch.setitem(sys.modules, "jhub_apps", fake_jhub_apps)
    monkeypatch.setitem(
        sys.modules, "jhub_apps.configuration", fake_jhub_apps_configuration
    )
    monkeypatch.setitem(sys.modules, "kubespawner", fake_kubespawner)
    monkeypatch.setitem(sys.modules, "z2jh", fake_z2jh)

    c = config if config is not None else FakeConfig()
    if existing_oauth_scopes is not None:
        c.Spawner.oauth_client_allowed_scopes = existing_oauth_scopes

    path = CONFIG_DIR / "02-jhub-apps.py"
    spec = importlib.util.spec_from_file_location("_jhub_apps_config", path)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["c"] = c
    module.__dict__["get_chart_config"] = lambda key, default="": default
    spec.loader.exec_module(module)
    return c


def test_jupyterlab_image_installs_jupyter_collaboration():
    with (REPO_ROOT / "images" / "jupyterlab" / "pixi.toml").open("rb") as f:
        manifest = tomllib.load(f)

    assert manifest["dependencies"]["jupyter-collaboration"] == ">=5.0.0,<6"


def test_sharing_scopes_allow_jupyterlab_rtc_ui(monkeypatch):
    c = _load_jhub_apps_config(monkeypatch)

    user_role = next(role for role in c.JupyterHub.load_roles if role["name"] == "user")
    assert set(user_role["scopes"]) >= {
        "list:groups",
        "list:users",
        "self",
        "read:groups:name",
        "read:users:name",
        "shares!user",
    }
    assert set(c.Spawner.oauth_client_allowed_scopes) >= {
        "access:servers!server",
        "list:groups",
        "list:users",
        "read:groups:name",
        "read:users:name",
        "servers!user",
        "shares!server",
        "shares!user",
    }
    assert "server_token_scopes" not in c.Spawner.__dict__


def test_sharing_scopes_preserve_existing_oauth_allowlist(monkeypatch):
    c = _load_jhub_apps_config(
        monkeypatch,
        existing_oauth_scopes=["custom:example-scope"],
    )

    assert "custom:example-scope" in c.Spawner.oauth_client_allowed_scopes
    assert "shares!server" in c.Spawner.oauth_client_allowed_scopes


def test_sharing_scopes_preserve_existing_async_oauth_allowlist(monkeypatch):
    async def existing_oauth_scopes(_spawner):
        return ["custom:async-scope"]

    c = _load_jhub_apps_config(
        monkeypatch,
        existing_oauth_scopes=existing_oauth_scopes,
    )

    scopes = asyncio.run(c.Spawner.oauth_client_allowed_scopes(object()))
    assert "custom:async-scope" in scopes
    assert "shares!server" in scopes


def test_sharing_scopes_materialize_traitlets_lazy_oauth_allowlist(monkeypatch):
    c = Config()
    c.Spawner.oauth_client_allowed_scopes.extend(["custom:lazy-scope"])

    c = _load_jhub_apps_config(monkeypatch, config=c)

    assert "custom:lazy-scope" in c.Spawner.oauth_client_allowed_scopes
    assert "shares!server" in c.Spawner.oauth_client_allowed_scopes


def test_sharing_switch_disables_jupyterlab_rtc_share_scopes(monkeypatch):
    c = _load_jhub_apps_config(monkeypatch, sharing_enabled=False)

    user_role = next(role for role in c.JupyterHub.load_roles if role["name"] == "user")
    assert user_role["scopes"] == ["self"]

    spawner = c.__dict__.get("Spawner")
    assert spawner is None or "oauth_client_allowed_scopes" not in spawner.__dict__


def test_hub_pod_is_allowed_to_reach_hub_api_under_network_policy():
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm not on PATH")

    rendered = subprocess.run(
        [
            helm,
            "template",
            "data-science-pack",
            str(REPO_ROOT),
            "--set",
            "keycloak.hostname=keycloak.example.com",
            "--show-only",
            "charts/jupyterhub/templates/hub/deployment.yaml",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    deployment = yaml.safe_load(rendered)
    labels = deployment["spec"]["template"]["metadata"]["labels"]

    assert labels.get("hub.jupyter.org/network-access-hub") == "true"
