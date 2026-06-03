"""Tests for the enterprise CA bundle wiring in `01-spawner.py`.

`_setup_trust_bundle` mounts the trust-manager ConfigMap (optional), runs an
init container using the spawn image to merge the org CA with the image's
system bundle into an emptyDir, and points the standard CA env vars at the
merged file. These assertions pin that contract.
"""

from __future__ import annotations

import sys
import types

# 01-spawner.py imports z2jh.get_config; stub it like the other spawner tests.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import FakeConfig, load_config_module  # noqa: E402

MERGED = "/etc/ssl/certs-extra/ca-bundle.crt"


class FakeSpawner:
    """Records the bits `_setup_trust_bundle` mutates."""

    def __init__(self):
        self.volumes = []
        self.volume_mounts = []
        self.init_containers = []
        self.environment = {}
        self.image = "quay.io/nebari/nebari-data-science-pack-jupyterlab:test"


def _load(custom):
    c = FakeConfig()
    base = {
        "custom.storage-capacity": "20Gi",
        "custom.shared-storage-enabled": False,
    }
    base.update(custom)
    sys.modules["z2jh"].get_config = lambda key, default=None: base.get(key, default)
    return load_config_module("01-spawner.py", inject_c=c)


def test_setup_trust_bundle_mounts_merges_and_sets_env():
    mod = _load({"custom.trust-bundle-enabled": True})
    spawner = FakeSpawner()
    mod._setup_trust_bundle(spawner)

    # optional ConfigMap volume + emptyDir
    org_ca = next(v for v in spawner.volumes if v["name"] == "org-ca")
    assert org_ca["configMap"]["name"] == "nebari-trust-bundle"
    assert org_ca["configMap"]["optional"] is True
    assert any(v["name"] == "ca-merged" and "emptyDir" in v for v in spawner.volumes)

    # main-container mount of the merged dir
    assert any(
        m["name"] == "ca-merged" and m["mountPath"] == "/etc/ssl/certs-extra"
        for m in spawner.volume_mounts
    )

    # merge init container using the spawn image
    init = next(c for c in spawner.init_containers if c["name"] == "merge-ca-bundle")
    assert init["image"] == spawner.image
    cmd = init["command"][2]
    assert "cp /etc/ssl/certs/ca-certificates.crt /merged/ca-bundle.crt" in cmd
    assert "cat /org-ca/ca-certificates.crt >> /merged/ca-bundle.crt" in cmd
    mounts = {m["name"]: m["mountPath"] for m in init["volumeMounts"]}
    assert mounts == {"org-ca": "/org-ca", "ca-merged": "/merged"}

    # all five CA env vars point at the merged file
    for var in (
        "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
        "CURL_CA_BUNDLE", "GIT_SSL_CAINFO",
    ):
        assert spawner.environment[var] == MERGED


def test_setup_trust_bundle_respects_custom_configmap_and_key():
    mod = _load({
        "custom.trust-bundle-enabled": True,
        "custom.trust-bundle-configmap": "my-ca",
        "custom.trust-bundle-key": "tls.crt",
    })
    spawner = FakeSpawner()
    mod._setup_trust_bundle(spawner)

    org_ca = next(v for v in spawner.volumes if v["name"] == "org-ca")
    assert org_ca["configMap"]["name"] == "my-ca"
    init = next(c for c in spawner.init_containers if c["name"] == "merge-ca-bundle")
    assert "cat /org-ca/tls.crt >> /merged/ca-bundle.crt" in init["command"][2]


def test_setup_trust_bundle_appends_without_clobbering_existing():
    mod = _load({"custom.trust-bundle-enabled": True})
    spawner = FakeSpawner()
    spawner.volumes = [{"name": "home"}]
    spawner.volume_mounts = [{"name": "home", "mountPath": "/home/jovyan"}]
    spawner.init_containers = [{"name": "install-nebi"}]
    spawner.environment = {"HOME": "/home/jovyan"}

    mod._setup_trust_bundle(spawner)

    assert any(v["name"] == "home" for v in spawner.volumes)
    assert any(v["name"] == "org-ca" for v in spawner.volumes)
    assert any(m["name"] == "home" for m in spawner.volume_mounts)
    assert any(c["name"] == "install-nebi" for c in spawner.init_containers)
    assert any(c["name"] == "merge-ca-bundle" for c in spawner.init_containers)
    assert spawner.environment["HOME"] == "/home/jovyan"
    assert spawner.environment["REQUESTS_CA_BUNDLE"] == MERGED


def test_trust_bundle_enabled_flag_reflects_config():
    enabled = _load({"custom.trust-bundle-enabled": True})
    assert enabled._trust_bundle_enabled is True

    disabled = _load({})  # key absent -> default False
    assert disabled._trust_bundle_enabled is False


def test_pre_spawn_hook_skips_trust_bundle_when_disabled():
    """Orchestrator must not touch CA volumes/env when the toggle is off."""
    import asyncio

    mod = _load({})  # disabled

    class _User:
        name = "alice@example.test"

        async def get_auth_state(self):
            return None

    spawner = FakeSpawner()
    spawner.user = _User()
    spawner.lifecycle_hooks = None  # _setup_nss_wrapper writes this

    asyncio.run(mod._pre_spawn_hook(spawner))

    assert not any(v["name"] == "org-ca" for v in spawner.volumes)
    assert "REQUESTS_CA_BUNDLE" not in spawner.environment


def test_pre_spawn_hook_applies_trust_bundle_when_enabled():
    import asyncio

    mod = _load({"custom.trust-bundle-enabled": True})

    class _User:
        name = "alice@example.test"

        async def get_auth_state(self):
            return None

    spawner = FakeSpawner()
    spawner.user = _User()
    spawner.lifecycle_hooks = None

    asyncio.run(mod._pre_spawn_hook(spawner))

    assert any(v["name"] == "org-ca" for v in spawner.volumes)
    assert spawner.environment["REQUESTS_CA_BUNDLE"] == MERGED
