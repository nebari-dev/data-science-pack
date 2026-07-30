"""Tests for the nebi-config Secret mount wiring in `01-spawner.py`.

Helm substitutes __NEBI_CONFIG_SECRET__ with the Secret name when the
deployer sets `nebi.registries` or disables `nebi.seedDefaultRegistry`,
and with "" otherwise. The spawner must add the /etc/nebi/config.yaml
mount only when a real name was substituted.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import CONFIG_DIR, FakeConfig, load_config_module  # noqa: E402


def _load_spawner_with_secret(tmp_path: Path, c: FakeConfig, secret_name: str):
    """Exec 01-spawner.py with the Helm placeholder substituted."""
    source = (CONFIG_DIR / "01-spawner.py").read_text()
    rendered = source.replace("__NEBI_CONFIG_SECRET__", secret_name)
    path = tmp_path / "01-spawner-rendered.py"
    path.write_text(rendered)

    spec = importlib.util.spec_from_file_location("_spawner_rendered", path)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["c"] = c
    module.__dict__["get_chart_config"] = lambda key, default="": default
    spec.loader.exec_module(module)


def _nebi_volume(c: FakeConfig):
    return next(
        (v for v in c.KubeSpawner.volumes if v.get("name") == "nebi-config"), None
    )


def _nebi_mount(c: FakeConfig):
    return next(
        (m for m in c.KubeSpawner.volume_mounts if m.get("name") == "nebi-config"),
        None,
    )


def test_no_mount_when_placeholder_unrendered():
    """Raw source (placeholder intact) must not add the mount. This is
    what unit tests and a template-render failure would see."""
    c = FakeConfig()
    load_config_module("01-spawner.py", inject_c=c)
    assert _nebi_volume(c) is None
    assert _nebi_mount(c) is None


def test_no_mount_when_secret_name_empty(tmp_path):
    """Helm substitutes "" when the deployer customizes nothing."""
    c = FakeConfig()
    _load_spawner_with_secret(tmp_path, c, "")
    assert _nebi_volume(c) is None
    assert _nebi_mount(c) is None


def test_mount_added_when_secret_rendered(tmp_path):
    c = FakeConfig()
    _load_spawner_with_secret(tmp_path, c, "my-pack-nebi-config")

    volume = _nebi_volume(c)
    assert volume is not None, "nebi-config volume missing"
    assert volume["secret"]["secretName"] == "my-pack-nebi-config"

    mount = _nebi_mount(c)
    assert mount is not None, "nebi-config volume_mount missing"
    assert mount["mountPath"] == "/etc/nebi/config.yaml"
    assert mount["subPath"] == "config.yaml"
