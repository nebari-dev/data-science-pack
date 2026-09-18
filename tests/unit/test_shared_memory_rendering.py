"""Check the shared-memory Helm contract against real KubeSpawner pods."""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from kubespawner import KubeSpawner
from traitlets.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def helm():
    executable = shutil.which("helm")
    if executable is None:
        pytest.skip("helm not on PATH")
    dependencies = yaml.safe_load((REPO_ROOT / "Chart.lock").read_text())[
        "dependencies"
    ]
    if any(
        not (REPO_ROOT / "charts" / f"{dep['name']}-{dep['version']}.tgz").exists()
        for dep in dependencies
    ):
        subprocess.run(
            [executable, "dependency", "update", str(REPO_ROOT)],
            capture_output=True,
            text=True,
            check=True,
        )
    return executable


def _render_chart(helm, values):
    result = subprocess.run(
        [
            helm,
            "template",
            "shared-memory-test",
            str(REPO_ROOT),
            "--set",
            "keycloak.hostname=keycloak.example.test",
            "--values",
            "-",
        ],
        input=yaml.safe_dump(values),
        capture_output=True,
        text=True,
        check=True,
    )
    resources = [resource for resource in yaml.safe_load_all(result.stdout) if resource]
    config = next(
        resource["data"]
        for resource in resources
        if resource["kind"] == "ConfigMap"
        and "01-spawner.py" in resource.get("data", {})
    )
    secret = next(
        resource["data"]
        for resource in resources
        if resource["kind"] == "Secret" and "values.yaml" in resource.get("data", {})
    )
    return config, yaml.safe_load(base64.b64decode(secret["values.yaml"]))


def _load_rendered_config(config, values, monkeypatch):
    def get_config(key, default=None):
        value = values
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    z2jh = types.ModuleType("z2jh")
    z2jh.get_config = get_config
    monkeypatch.setitem(sys.modules, "z2jh", z2jh)
    namespace = {"c": Config(), "__name__": "rendered_spawner_config"}
    for filename in ("00-chart-derived.py", "01-spawner.py"):
        exec(compile(config[filename], filename, "exec"), namespace)  # noqa: S102
    return namespace["c"]


async def _make_pod(config, profile):
    spawner = KubeSpawner(config=config, _mock=True)
    spawner.user.get_auth_state = AsyncMock(return_value={})
    if profile:
        spawner.user_options = {"profile": profile}
    await spawner.load_user_options()
    return await spawner.get_pod_manifest()


def _render_pod(helm, monkeypatch, values, profile=None):
    source, hub_values = _render_chart(helm, values)
    config = _load_rendered_config(source, hub_values, monkeypatch)
    # Manifest generation needs neither cluster credentials nor an API client.
    monkeypatch.setattr("kubespawner.spawner.load_config", lambda **kwargs: None)
    monkeypatch.setattr("kubespawner.spawner.shared_client", lambda *args: None)
    return asyncio.run(_make_pod(config, profile))


def _assert_shared_memory(pod, size_limit):
    volumes = [volume for volume in pod.spec.volumes if volume.name == "dshm"]
    mounts = [
        mount
        for mount in pod.spec.containers[0].volume_mounts
        if mount.mount_path == "/dev/shm"
    ]
    if size_limit is None:
        assert volumes == []
        assert mounts == []
    else:
        assert len(volumes) == len(mounts) == 1
        assert volumes[0].empty_dir == {"medium": "Memory", "sizeLimit": size_limit}
        assert mounts[0].name == "dshm"
    assert {"home", "singleuser-config"} <= {volume.name for volume in pod.spec.volumes}
    assert {"home", "singleuser-config"} <= {
        mount.name for mount in pod.spec.containers[0].volume_mounts
    }


@pytest.mark.parametrize(
    ("shared_memory", "expected_size"),
    [({}, "8Gi"), ({"enabled": False}, None), ({"sizeLimit": "2Gi"}, "2Gi")],
    ids=["default", "disabled", "custom-size"],
)
def test_chart_shared_memory_reaches_pod(
    helm, monkeypatch, shared_memory, expected_size
):
    pod = _render_pod(
        helm, monkeypatch, {"singleuser": {"sharedMemory": shared_memory}}
    )

    _assert_shared_memory(pod, expected_size)


@pytest.mark.parametrize("enabled", [True, False], ids=["enabled", "disabled"])
def test_profile_shared_memory_preserves_gpu_image_variant(helm, monkeypatch, enabled):
    values = {
        "singleuser": {"sharedMemory": {"enabled": enabled, "sizeLimit": "2Gi"}},
        "jupyterhub": {
            "singleuser": {
                "image": {"name": "example.invalid/notebook", "tag": "test"}
            },
            "custom": {
                "profiles": [
                    {
                        "slug": "gpu",
                        "display_name": "GPU",
                        "image-variant": "gpu",
                        "kubespawner_override": {
                            "shm_size_limit": "16Gi",
                            "extra_resource_limits": {"nvidia.com/gpu": "1"},
                        },
                    }
                ]
            },
        },
    }

    pod = _render_pod(helm, monkeypatch, values, profile="gpu")

    _assert_shared_memory(pod, "16Gi" if enabled else None)
    notebook = pod.spec.containers[0]
    assert notebook.image == "example.invalid/notebook-gpu:test"
    assert notebook.resources.limits["nvidia.com/gpu"] == "1"
