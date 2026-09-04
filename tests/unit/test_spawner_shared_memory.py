"""Regression tests for singleuser ``/dev/shm`` configuration."""

from __future__ import annotations

import copy
import sys
import types
from pathlib import Path

import pytest
import yaml
from kubespawner import KubeSpawner

sys.modules.setdefault("z2jh", types.ModuleType("z2jh"))

from conftest import FakeConfig, load_config_module

REPO_ROOT = Path(__file__).resolve().parents[2]
DSHM_NAME = "dshm"
DSHM_PATH = "/dev/shm"


def _load(values=None):
    values = values or {}
    z2jh = sys.modules["z2jh"]
    previous = getattr(z2jh, "get_config", None)
    z2jh.get_config = lambda key, default=None: values.get(key, default)
    try:
        config = FakeConfig()
        module = load_config_module("01-spawner.py", inject_c=config)
    finally:
        if previous is None:
            delattr(z2jh, "get_config")
        else:
            z2jh.get_config = previous
    return module, config


def _named(entries, name):
    return next((entry for entry in entries if entry.get("name") == name), None)


def _count(entries, key, value):
    return sum(entry.get(key) == value for entry in entries)


def test_values_enable_eight_gibibytes_by_default():
    values = yaml.safe_load((REPO_ROOT / "values.yaml").read_text())

    assert values["singleuser"]["sharedMemory"] == {
        "enabled": True,
        "sizeLimit": "8Gi",
    }


def test_global_default_adds_memory_backed_volume_and_mount():
    _, config = _load()

    volume = _named(config.KubeSpawner.volumes, DSHM_NAME)
    mount = _named(config.KubeSpawner.volume_mounts, DSHM_NAME)

    assert volume == {
        "name": DSHM_NAME,
        "emptyDir": {"medium": "Memory", "sizeLimit": "8Gi"},
    }
    assert mount == {"name": DSHM_NAME, "mountPath": DSHM_PATH}
    assert _named(config.KubeSpawner.volumes, "home") is not None
    assert _named(config.KubeSpawner.volume_mounts, "home") is not None


def test_standard_extra_volumes_and_mounts_are_preserved():
    _, config = _load(
        {
            "singleuser.extraVolumes": [
                {"name": "scratch", "emptyDir": {}},
            ],
            "singleuser.extraVolumeMounts": [
                {"name": "scratch", "mountPath": "/scratch"},
            ],
        }
    )

    assert _named(config.KubeSpawner.volumes, "scratch") is not None
    assert _named(config.KubeSpawner.volume_mounts, "scratch") is not None
    assert _named(config.KubeSpawner.volumes, DSHM_NAME) is not None


def test_disabled_global_setting_adds_no_shared_memory_entries():
    _, config = _load({"custom.shared-memory-enabled": False})

    assert _named(config.KubeSpawner.volumes, DSHM_NAME) is None
    assert _named(config.KubeSpawner.volume_mounts, DSHM_NAME) is None


def test_profile_override_is_translated_without_mutating_input():
    module, _ = _load()
    profile = {
        "slug": "gpu",
        "display_name": "GPU",
        "kubespawner_override": {
            "image": "example.invalid/notebook:test",
            "shm_size_limit": "16Gi",
        },
    }
    original = copy.deepcopy(profile)

    visible = module._filter_profiles([profile], groups=[], username="alice")
    overrides = visible[0]["kubespawner_override"]

    assert profile == original
    assert "shm_size_limit" not in overrides
    assert overrides["image"] == "example.invalid/notebook:test"

    spawner = types.SimpleNamespace(
        volumes=[{"name": "home"}],
        volume_mounts=[{"name": "home", "mountPath": "/home/jovyan"}],
    )
    volumes = overrides["volumes"](spawner)
    mounts = overrides["volume_mounts"](spawner)

    assert _named(volumes, "home") is not None
    assert _named(mounts, "home") is not None
    assert _named(volumes, DSHM_NAME)["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "16Gi",
    }
    assert _named(mounts, DSHM_NAME)["mountPath"] == DSHM_PATH


def test_profile_volume_overrides_preserve_global_shared_memory():
    module, _ = _load()
    profile = {
        "slug": "scratch",
        "display_name": "Scratch storage",
        "kubespawner_override": {
            "volumes": [{"name": "scratch", "emptyDir": {}}],
            "volume_mounts": [{"name": "scratch", "mountPath": "/scratch"}],
        },
    }

    overrides = module._filter_profiles([profile], groups=[], username="alice")[0][
        "kubespawner_override"
    ]
    spawner = types.SimpleNamespace(volumes=[], volume_mounts=[])

    volumes = overrides["volumes"](spawner)
    mounts = overrides["volume_mounts"](spawner)

    assert _named(volumes, "scratch") is not None
    assert _named(mounts, "scratch") is not None
    assert _named(volumes, DSHM_NAME)["emptyDir"]["sizeLimit"] == "8Gi"
    assert _named(mounts, DSHM_NAME)["mountPath"] == DSHM_PATH


def test_profile_override_replaces_conflicts_without_duplicates():
    module, _ = _load()
    profile = {
        "slug": "large",
        "display_name": "Large",
        "kubespawner_override": {"shm_size_limit": "12Gi"},
    }
    overrides = module._filter_profiles([profile], groups=[], username="alice")[0][
        "kubespawner_override"
    ]
    spawner = types.SimpleNamespace(
        volumes=[
            {"name": DSHM_NAME, "emptyDir": {"medium": "Memory", "sizeLimit": "1Gi"}},
        ],
        volume_mounts=[
            {"name": "legacy-shm", "mountPath": DSHM_PATH},
        ],
        log=types.SimpleNamespace(debug=lambda *args: None),
    )

    KubeSpawner._apply_overrides(spawner, overrides)

    assert _count(spawner.volumes, "name", DSHM_NAME) == 1
    assert _named(spawner.volumes, DSHM_NAME)["emptyDir"]["sizeLimit"] == "12Gi"
    assert _count(spawner.volume_mounts, "mountPath", DSHM_PATH) == 1
    assert _named(spawner.volume_mounts, DSHM_NAME) is not None

    # JupyterHub and KubeSpawner can each apply user options during one spawn.
    # Reapplying the callable must update, not duplicate, the generated entries.
    KubeSpawner._apply_overrides(spawner, overrides)

    assert _count(spawner.volumes, "name", DSHM_NAME) == 1
    assert _count(spawner.volume_mounts, "mountPath", DSHM_PATH) == 1


def test_disabled_setting_strips_profile_pseudo_trait():
    module, _ = _load({"custom.shared-memory-enabled": False})
    profile = {
        "slug": "gpu",
        "display_name": "GPU",
        "kubespawner_override": {"shm_size_limit": "16Gi"},
    }

    visible = module._filter_profiles([profile], groups=[], username="alice")

    assert "shm_size_limit" not in visible[0]["kubespawner_override"]
    assert "volumes" not in visible[0]["kubespawner_override"]
    assert "volume_mounts" not in visible[0]["kubespawner_override"]


def test_profile_override_rejects_empty_size_limit():
    module, _ = _load()
    profile = {
        "slug": "gpu",
        "display_name": "GPU",
        "kubespawner_override": {"shm_size_limit": ""},
    }

    with pytest.raises(ValueError, match="must be a non-empty string"):
        module._filter_profiles([profile], groups=[], username="alice")
