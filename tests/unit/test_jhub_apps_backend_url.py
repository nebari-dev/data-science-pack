"""jhub-apps' own hub-API base URL wiring in 02-jhub-apps.py.

jhub-apps runs as a managed-service subprocess inside the SAME pod as
hub, but z2jh injects JUPYTERHUB_API_URL pointing at the `hub` Service
(ClusterIP self-reference). Routing same-pod traffic through a Service
depends on the CNI supporting hairpin NAT for a pod reaching its own
Service via that Service's ClusterIP -- confirmed to time out
(httpcore.ConnectTimeout) on a kind/kindnet cluster. Rewriting the host
to localhost (same port/path) is always reliable for same-pod traffic
and doesn't depend on hairpin NAT support.

02-jhub-apps.py isn't independently importable: it needs `jhub_apps`
and `z2jh` (real chart-image dependencies, not installed in the unit
test venv) at module load time for things unrelated to this fix. Stub
both minimally so the real file executes end-to-end and this test
exercises the actual production code path, not a reimplementation of
it elsewhere.
"""

from __future__ import annotations

import sys
import types

from conftest import FakeConfig, load_config_module


def _install_stub_dependencies(monkeypatch):
    """Stub jhub_apps + z2jh just enough for 02-jhub-apps.py to load."""
    jhub_apps_mod = types.ModuleType("jhub_apps")
    jhub_apps_mod.theme_template_paths = []
    jhub_apps_mod.themes = types.SimpleNamespace(DEFAULT_THEME={})

    def _fake_install_jhub_apps(c, spawner_to_subclass=None):
        c.JupyterHub.services = [
            {"name": "japps", "oauth_client_id": "service-japps"}
        ]
        c.JupyterHub.load_roles = [{"name": "user", "scopes": []}]
        return c

    configuration_mod = types.ModuleType("jhub_apps.configuration")
    configuration_mod.install_jhub_apps = _fake_install_jhub_apps

    def _fake_get_config(key, default=None):
        return default

    z2jh_mod = types.ModuleType("z2jh")
    z2jh_mod.get_config = _fake_get_config

    monkeypatch.setitem(sys.modules, "jhub_apps", jhub_apps_mod)
    monkeypatch.setitem(sys.modules, "jhub_apps.configuration", configuration_mod)
    monkeypatch.setitem(sys.modules, "z2jh", z2jh_mod)


def _load(monkeypatch, **env):
    """Set env vars, then load 02-jhub-apps.py (which reads them at exec time)."""
    _install_stub_dependencies(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    c = FakeConfig()
    mod = load_config_module("02-jhub-apps.py", inject_c=c)
    return c, mod


def _japps_service(c):
    return next(svc for svc in c.JupyterHub.services if svc.get("name") == "japps")


def test_japps_service_env_points_hub_api_at_localhost(monkeypatch):
    c, _ = _load(monkeypatch, JUPYTERHUB_API_URL="http://hub:8081/hub/api")
    japps = _japps_service(c)
    assert japps["environment"]["JUPYTERHUB_API_URL"] == "http://localhost:8081/hub/api"


def test_preserves_a_nonstandard_port(monkeypatch):
    c, _ = _load(monkeypatch, JUPYTERHUB_API_URL="http://hub:9999/hub/api")
    japps = _japps_service(c)
    assert japps["environment"]["JUPYTERHUB_API_URL"] == "http://localhost:9999/hub/api"


def test_no_jupyterhub_api_url_set_leaves_japps_env_untouched(monkeypatch):
    monkeypatch.delenv("JUPYTERHUB_API_URL", raising=False)
    c, _ = _load(monkeypatch)
    japps = _japps_service(c)
    assert "JUPYTERHUB_API_URL" not in japps.get("environment", {})
