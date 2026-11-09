"""Regression tests for singleuser ``/dev/shm`` configuration."""

from __future__ import annotations

import asyncio
import copy
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

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


def _load_profile(profile, values=None):
    module, _ = _load({**(values or {}), "custom.profiles": [profile]})
    return module._profiles[0]


def _named(entries, name):
    return next((entry for entry in entries if entry.get("name") == name), None)


def _count(entries, key, value):
    return sum(entry.get(key) == value for entry in entries)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


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


def test_disabled_global_setting_adds_no_shared_memory_entries():
    _, config = _load({"custom.shared-memory-enabled": False})

    assert _named(config.KubeSpawner.volumes, DSHM_NAME) is None
    assert _named(config.KubeSpawner.volume_mounts, DSHM_NAME) is None


@pytest.mark.parametrize("enabled", [True, False])
def test_chart_storage_is_unchanged(enabled):
    _, config = _load({"custom.shared-memory-enabled": enabled})

    assert _named(config.KubeSpawner.volumes, "home") == {
        "name": "home",
        "persistentVolumeClaim": {"claimName": "claim-{username}"},
    }
    assert _named(config.KubeSpawner.volumes, "singleuser-config") == {
        "name": "singleuser-config",
        "configMap": {"name": "__SINGLEUSER_CONFIG_CM__"},
    }
    assert _named(config.KubeSpawner.volume_mounts, "home") == {
        "name": "home",
        "mountPath": "/home/jovyan",
    }
    assert _named(config.KubeSpawner.volume_mounts, "singleuser-config") == {
        "name": "singleuser-config",
        "mountPath": "/etc/jupyter/jupyter_server_config.py",
        "subPath": "jupyter_server_config.py",
    }


@pytest.mark.parametrize("size", ["", "  ", None, 8])
def test_invalid_global_size_warns_and_uses_chart_default(size, caplog):
    with caplog.at_level("WARNING"):
        _, config = _load({"custom.shared-memory-size-limit": size})

    assert _named(config.KubeSpawner.volumes, DSHM_NAME)["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "8Gi",
    }
    assert _named(config.KubeSpawner.volume_mounts, DSHM_NAME) is not None
    warnings = _warnings(caplog)
    assert any("singleuser.sharedMemory.sizeLimit" in w for w in warnings), warnings


def test_invalid_global_size_falls_back_for_profiles():
    profile = {
        "slug": "scratch",
        "display_name": "Scratch storage",
        "kubespawner_override": {"volumes": [{"name": "scratch", "emptyDir": {}}]},
    }

    overrides = _load_profile(profile, {"custom.shared-memory-size-limit": ""})[
        "kubespawner_override"
    ]
    spawner = types.SimpleNamespace(volumes=[], volume_mounts=[])

    assert _named(overrides["volumes"](spawner), DSHM_NAME)["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "8Gi",
    }


def test_global_size_is_trimmed():
    _, config = _load({"custom.shared-memory-size-limit": " 2Gi "})

    assert _named(config.KubeSpawner.volumes, DSHM_NAME)["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "2Gi",
    }


@pytest.mark.parametrize(
    "profile",
    [{"display_name": "Default"}, {"kubespawner_override": {"image": "custom:test"}}],
)
def test_profiles_without_storage_overrides_are_unchanged(profile):
    module, _ = _load()

    assert module._translate_profile_shared_memory(profile) == profile


def test_profile_override_is_translated_at_load_without_mutating_input():
    profile = {
        "slug": "gpu",
        "display_name": "GPU",
        "kubespawner_override": {
            "image": "example.invalid/notebook:test",
            "shm_size_limit": "16Gi",
        },
    }
    original = copy.deepcopy(profile)

    overrides = _load_profile(profile)["kubespawner_override"]

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
    profile = {
        "slug": "scratch",
        "display_name": "Scratch storage",
        "kubespawner_override": {
            "volumes": [{"name": "scratch", "emptyDir": {}}],
            "volume_mounts": [{"name": "scratch", "mountPath": "/scratch"}],
        },
    }

    overrides = _load_profile(profile)["kubespawner_override"]
    spawner = types.SimpleNamespace(volumes=[], volume_mounts=[])

    volumes = overrides["volumes"](spawner)
    mounts = overrides["volume_mounts"](spawner)

    assert _named(volumes, "scratch") is not None
    assert _named(mounts, "scratch") is not None
    assert _named(volumes, DSHM_NAME)["emptyDir"]["sizeLimit"] == "8Gi"
    assert _named(mounts, DSHM_NAME)["mountPath"] == DSHM_PATH


def test_profile_override_replaces_conflicts_without_duplicates():
    profile = {
        "slug": "large",
        "display_name": "Large",
        "kubespawner_override": {"shm_size_limit": "12Gi"},
    }
    overrides = _load_profile(profile)["kubespawner_override"]
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

    # Applying the same overrides to this spawner again must update, not
    # duplicate, the generated entries.
    KubeSpawner._apply_overrides(spawner, overrides)

    assert _count(spawner.volumes, "name", DSHM_NAME) == 1
    assert _count(spawner.volume_mounts, "mountPath", DSHM_PATH) == 1


def test_disabled_setting_strips_profile_pseudo_trait():
    profile = {
        "slug": "gpu",
        "display_name": "GPU",
        "kubespawner_override": {"shm_size_limit": "16Gi"},
    }

    overrides = _load_profile(profile, {"custom.shared-memory-enabled": False})[
        "kubespawner_override"
    ]

    assert "shm_size_limit" not in overrides
    assert "volumes" not in overrides
    assert "volume_mounts" not in overrides


@pytest.mark.parametrize("include_size", [True, False])
def test_disabled_setting_preserves_operator_managed_mount(include_size):
    module, config = _load({"custom.shared-memory-enabled": False})
    overrides = {
        "volumes": [
            {"name": "custom-shm", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}}
        ],
        "volume_mounts": [{"name": "custom-shm", "mountPath": DSHM_PATH}],
    }
    profile = {
        "display_name": "Custom shared memory",
        "kubespawner_override": copy.deepcopy(overrides),
    }
    if include_size:
        profile["kubespawner_override"]["shm_size_limit"] = "16Gi"
    original = copy.deepcopy(profile)

    translated = module._translate_profile_shared_memory(profile)

    assert translated["kubespawner_override"] == overrides
    assert profile == original
    assert _named(config.KubeSpawner.volumes, DSHM_NAME) is None
    assert _named(config.KubeSpawner.volume_mounts, DSHM_NAME) is None


def test_callable_profile_storage_is_composed_without_mutation():
    module, _ = _load()
    volumes = [{"name": "scratch", "emptyDir": {}}]
    mounts = [{"name": "scratch", "mountPath": "/scratch"}]
    original_volumes, original_mounts = copy.deepcopy((volumes, mounts))
    spawner = types.SimpleNamespace(volumes=[], volume_mounts=[], log=module.log)

    def configured_volumes(current):
        assert current is spawner
        return volumes

    def configured_mounts(current):
        assert current is spawner
        return mounts

    profile = {
        "kubespawner_override": {
            "volumes": configured_volumes,
            "volume_mounts": configured_mounts,
        }
    }
    translated = module._translate_profile_shared_memory(profile)
    KubeSpawner._apply_overrides(spawner, translated["kubespawner_override"])

    assert volumes == original_volumes
    assert mounts == original_mounts
    assert _named(spawner.volumes, "scratch") == volumes[0]
    assert _named(spawner.volume_mounts, "scratch") == mounts[0]
    assert _named(spawner.volumes, DSHM_NAME)["emptyDir"]["sizeLimit"] == "8Gi"
    assert _named(spawner.volume_mounts, DSHM_NAME)["mountPath"] == DSHM_PATH


@pytest.mark.parametrize("size", ["", "  ", None, 8])
def test_invalid_profile_size_warns_and_uses_chart_size(size, caplog):
    profile = {
        "slug": "gpu",
        "display_name": "GPU",
        "kubespawner_override": {"shm_size_limit": size},
    }

    with caplog.at_level("WARNING"):
        overrides = _load_profile(profile)["kubespawner_override"]
    spawner = types.SimpleNamespace(volumes=[], volume_mounts=[])

    assert "shm_size_limit" not in overrides
    assert _named(overrides["volumes"](spawner), DSHM_NAME)["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "8Gi",
    }
    warnings = _warnings(caplog)
    assert any("'gpu'" in w and "shm_size_limit" in w for w in warnings), warnings


def test_invalid_profile_size_does_not_break_profile_list(caplog):
    profiles = [
        {
            "slug": "gpu",
            "display_name": "GPU",
            "kubespawner_override": {"shm_size_limit": ""},
        },
        {"slug": "cpu", "display_name": "CPU"},
    ]
    module, _ = _load({"custom.profiles": profiles})
    spawner = types.SimpleNamespace(
        user=types.SimpleNamespace(get_auth_state=AsyncMock(return_value={}))
    )
    caplog.clear()

    # The size is checked once at load, not again on every profile-list render.
    with caplog.at_level("WARNING"):
        for _ in range(2):
            visible = asyncio.run(module._render_profile_list(spawner))
            assert [profile["slug"] for profile in visible] == ["gpu", "cpu"]

    assert not [r for r in caplog.records if r.levelname == "WARNING"]
