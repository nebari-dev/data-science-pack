"""Tests for the nebi netguard origin allowlist in `01-spawner.py`.

nebi local mode rejects non-loopback Origin headers, which browsers send on
the SPA's crossorigin asset requests, blanking the Nebi tile
(https://github.com/nebari-dev/nebi/issues/489). The spawner must inject
NEBI_SERVER_ALLOWED_ORIGINS with the hub's public origin when one is
derivable, and must not set an empty origin otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
import types

# 01-spawner.py imports `z2jh.get_config`; stub it so the module exec's standalone.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import CONFIG_DIR, FakeConfig  # noqa: E402


def _load_spawner_with_chart_config(c: FakeConfig, chart_values: dict):
    """Exec raw 01-spawner.py with a custom get_chart_config stub.

    conftest.load_config_module routes get_chart_config through the shared
    z2jh stub, which other test modules may have installed first; injecting
    the stub directly keeps the chart values deterministic.
    """
    path = CONFIG_DIR / "01-spawner.py"
    spec = importlib.util.spec_from_file_location("_spawner_chartcfg", path)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["c"] = c
    module.__dict__["get_chart_config"] = (
        lambda key, default="": chart_values.get(key, default)
    )
    spec.loader.exec_module(module)


def test_allowed_origins_env_set_from_hub_host():
    """The hub public origin must be allowlisted for nebi netguard
    (https://github.com/nebari-dev/nebi/issues/489), or browsers get 403
    on the SPA asset bundle."""
    c = FakeConfig()
    _load_spawner_with_chart_config(c, {"external-url": "hub.example.com"})
    assert (
        c.KubeSpawner.environment["NEBI_SERVER_ALLOWED_ORIGINS"]
        == "https://hub.example.com"
    )


def test_allowed_origins_env_absent_without_hub_host():
    """No derivable hub host (plain kind deploy): do not set an empty origin."""
    c = FakeConfig()
    _load_spawner_with_chart_config(c, {})
    assert "NEBI_SERVER_ALLOWED_ORIGINS" not in c.KubeSpawner.environment
