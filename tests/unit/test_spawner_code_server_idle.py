"""CODE_SERVER_IDLE_TIMEOUT_SECONDS wiring in `01-spawner.py`.

code-server >= 4.106 exits N seconds after its last browser connection
closes when this env var is set. It must mirror the hub idle culler's
`cull.timeout` (issue #208) — and must be ABSENT when culling is disabled
or the timeout is <= 60, because code-server refuses to start for values
<= 60 and that would take down every user pod's VS Code.
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


def _load_spawner(c: FakeConfig, z2jh_values: dict | None = None,
                  chart_values: dict | None = None):
    """Exec raw 01-spawner.py with per-test z2jh + chart config stubs."""
    z2jh_values = z2jh_values or {}
    chart_values = chart_values or {}
    z2jh = sys.modules["z2jh"]
    orig = z2jh.get_config
    z2jh.get_config = lambda key, default=None: z2jh_values.get(key, default)
    try:
        path = CONFIG_DIR / "01-spawner.py"
        spec = importlib.util.spec_from_file_location("_spawner_idle", path)
        module = importlib.util.module_from_spec(spec)
        module.__dict__["c"] = c
        module.__dict__["get_chart_config"] = (
            lambda key, default="": chart_values.get(key, default)
        )
        spec.loader.exec_module(module)
    finally:
        z2jh.get_config = orig


def test_idle_timeout_matches_cull_timeout():
    c = FakeConfig()
    _load_spawner(c, z2jh_values={"cull.enabled": True, "cull.timeout": 1800})
    assert c.KubeSpawner.environment["CODE_SERVER_IDLE_TIMEOUT_SECONDS"] == "1800"


def test_idle_timeout_absent_when_culling_disabled():
    c = FakeConfig()
    _load_spawner(c, z2jh_values={"cull.enabled": False, "cull.timeout": 1800})
    assert "CODE_SERVER_IDLE_TIMEOUT_SECONDS" not in c.KubeSpawner.environment


def test_idle_timeout_absent_when_60_or_less():
    """code-server errors out at startup for values <= 60 — never set them."""
    c = FakeConfig()
    _load_spawner(c, z2jh_values={"cull.enabled": True, "cull.timeout": 60})
    assert "CODE_SERVER_IDLE_TIMEOUT_SECONDS" not in c.KubeSpawner.environment


def test_proxy_activity_env_absent_by_default():
    """Default (vscodeActivity.enabled=true): the image config's default of
    update_last_activity=False must apply, so no env var is set."""
    c = FakeConfig()
    _load_spawner(c, chart_values={"vscode-activity-enabled": True})
    assert "VSCODE_PROXY_UPDATE_LAST_ACTIVITY" not in c.KubeSpawner.environment


def test_proxy_activity_env_set_when_vscode_activity_disabled():
    """vscodeActivity.enabled=false is the field escape hatch: proxied VS
    Code traffic counts as activity again (pre-#208 behavior)."""
    c = FakeConfig()
    _load_spawner(c, chart_values={"vscode-activity-enabled": False})
    assert (
        c.KubeSpawner.environment["VSCODE_PROXY_UPDATE_LAST_ACTIVITY"] == "true"
    )
