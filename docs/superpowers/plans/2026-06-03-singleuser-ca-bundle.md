# Singleuser CA bundle integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JupyterHub singleuser and jhub-apps pods trust the enterprise CA so `pip`/`conda`/`git` work through a TLS-inspecting proxy with no flags.

**Architecture:** A pre-spawn hook (`_setup_trust_bundle`) mounts the trust-manager-projected `nebari-trust-bundle` ConfigMap (optional) and runs an init container — using the spawn image so it sees the same system CA store — that concatenates the system bundle with the org CA into an `emptyDir`. The main container mounts that emptyDir and the five standard CA env vars point at the merged file. Gated behind `custom.trust-bundle-enabled` (default false) so the pack is byte-for-byte unchanged on clusters without trust-manager.

**Tech Stack:** Helm chart (z2jh subchart), Python KubeSpawner config (`config/jupyterhub/01-spawner.py`), pytest unit tests that exec the spawner module with a fake `c` config and a stubbed `z2jh`.

**Spec:** `docs/superpowers/specs/2026-06-03-singleuser-ca-bundle-design.md`

---

## File Structure

- `values.yaml` — add three `jupyterhub.custom.trust-bundle-*` keys (toggle + ConfigMap name/key) with docs. (Task 1)
- `config/jupyterhub/01-spawner.py` — module-level config reads + `_setup_trust_bundle(spawner)` hook + wiring into `_pre_spawn_hook`. (Tasks 2–3)
- `tests/unit/test_spawner_ca_bundle.py` — new; enabled-content test + gating test. (Tasks 2–3)

---

## Task 1: Add chart config keys for the trust bundle

**Files:**
- Modify: `values.yaml` (inside `jupyterhub.custom:`, after the `jupyterhub-client-id: ""` line, currently ~line 352)

- [ ] **Step 1: Add the three keys**

In `values.yaml`, find these lines under `jupyterhub.custom:`:

```yaml
    nebi-client-id: ""
    jupyterhub-client-id: ""
```

Insert immediately after `jupyterhub-client-id: ""`:

```yaml
    # ---------------------------------------------------------------------------
    # Enterprise CA bundle (TLS-inspected egress).
    # When the cluster sits behind a TLS-inspecting proxy, NIC core's
    # trust-manager projects the org CA into every namespace as a ConfigMap.
    # Enabling this merges that CA with the image's system bundle (via an init
    # container) and sets REQUESTS_CA_BUNDLE / SSL_CERT_FILE / NODE_EXTRA_CA_CERTS
    # / CURL_CA_BUNDLE / GIT_SSL_CAINFO on singleuser + app pods, so pip/conda/git
    # work with no --trusted-host / ssl_verify flags.
    # Leave false on clusters without trust-manager (default; no behavior change).
    # The ConfigMap is always mounted optional, so a missing CA never blocks spawn.
    trust-bundle-enabled: false
    # ConfigMap (in the JupyterHub namespace) holding the org CA. Defaults match
    # trust-manager's Bundle convention (Bundle name -> ConfigMap name; the data
    # key set on the Bundle target).
    trust-bundle-configmap: "nebari-trust-bundle"
    trust-bundle-key: "ca-certificates.crt"
```

- [ ] **Step 2: Verify the chart still renders**

Run: `cd ~/gh/nebari-data-science-pack && helm dependency update >/dev/null 2>&1; helm template . >/dev/null && echo OK`
Expected: `OK` (no template error). If `helm` is not on PATH, skip and rely on the unit tests.

- [ ] **Step 3: Commit**

```bash
cd ~/gh/nebari-data-science-pack
git add values.yaml
git commit -m "feat(values): add custom.trust-bundle-* config for enterprise CA"
```

---

## Task 2: Implement the `_setup_trust_bundle` hook (enabled behavior)

**Files:**
- Modify: `config/jupyterhub/01-spawner.py` (add module-level reads + the function, after the Nebi-binary init-container block that ends at the `c.KubeSpawner.init_containers.append({... "install-nebi" ...})` block, i.e. before the `# Environment variables` section header ~line 163)
- Test: `tests/unit/test_spawner_ca_bundle.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_spawner_ca_bundle.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gh/nebari-data-science-pack && python -m pytest tests/unit/test_spawner_ca_bundle.py -v`
Expected: FAIL — `AttributeError: '_01_spawner' module has no attribute '_setup_trust_bundle'`

- [ ] **Step 3: Write the implementation**

In `config/jupyterhub/01-spawner.py`, after the Nebi-binary init-container block (the block that appends the `"install-nebi"` init container) and BEFORE the `# Environment variables` section header, insert:

```python
# ---------------------------------------------------------------------------
# Enterprise CA bundle (TLS-inspected egress)
# ---------------------------------------------------------------------------
# On clusters behind a TLS-inspecting proxy, NIC core's trust-manager projects
# the org CA into every namespace as a ConfigMap. We merge it with the image's
# system CA bundle into an emptyDir and point the standard CA env vars at the
# merged file, so pip/conda/git verify BOTH proxy-inspected (org-signed) and
# genuine public-root endpoints with no flags. Gated off by default.
_trust_bundle_enabled = get_chart_config("trust-bundle-enabled", False)
_trust_bundle_configmap = get_chart_config("trust-bundle-configmap", "nebari-trust-bundle")
_trust_bundle_key = get_chart_config("trust-bundle-key", "ca-certificates.crt")
_MERGED_CA_PATH = "/etc/ssl/certs-extra/ca-bundle.crt"


def _setup_trust_bundle(spawner):
    """Mount + merge the org CA into the pod and set the CA env vars.

    The merge init container runs spawner.image so it reads the SAME system CA
    store the main container has (a generic busybox would not). The org-ca
    ConfigMap is mounted optional, so a cluster without trust-manager — or a
    spawn that races the projection — still starts; the merged file is then
    just the system bundle, i.e. no behavior change.
    """
    spawner.volumes = list(spawner.volumes) + [
        {
            "name": "org-ca",
            "configMap": {"name": _trust_bundle_configmap, "optional": True},
        },
        {"name": "ca-merged", "emptyDir": {}},
    ]
    spawner.volume_mounts = list(spawner.volume_mounts) + [
        {"name": "ca-merged", "mountPath": "/etc/ssl/certs-extra"},
    ]
    existing_init = getattr(spawner, "init_containers", None)
    if not isinstance(existing_init, list):
        existing_init = []
    spawner.init_containers = existing_init + [
        {
            "name": "merge-ca-bundle",
            "image": spawner.image,
            "command": [
                "/bin/sh",
                "-c",
                "cp /etc/ssl/certs/ca-certificates.crt /merged/ca-bundle.crt && "
                f"if [ -f /org-ca/{_trust_bundle_key} ]; then "
                f"cat /org-ca/{_trust_bundle_key} >> /merged/ca-bundle.crt; fi",
            ],
            "volumeMounts": [
                {"name": "org-ca", "mountPath": "/org-ca", "readOnly": True},
                {"name": "ca-merged", "mountPath": "/merged"},
            ],
        },
    ]
    spawner.environment = {
        **spawner.environment,
        "REQUESTS_CA_BUNDLE": _MERGED_CA_PATH,
        "SSL_CERT_FILE": _MERGED_CA_PATH,
        "NODE_EXTRA_CA_CERTS": _MERGED_CA_PATH,
        "CURL_CA_BUNDLE": _MERGED_CA_PATH,
        "GIT_SSL_CAINFO": _MERGED_CA_PATH,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/gh/nebari-data-science-pack && python -m pytest tests/unit/test_spawner_ca_bundle.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
cd ~/gh/nebari-data-science-pack
git add config/jupyterhub/01-spawner.py tests/unit/test_spawner_ca_bundle.py
git commit -m "feat(spawner): merge org CA into singleuser pods for TLS-inspected egress"
```

---

## Task 3: Gate on the toggle and wire into the pre-spawn hook

**Files:**
- Modify: `config/jupyterhub/01-spawner.py` (inside `_pre_spawn_hook`, after the Nebi auto-auth step `# 1.`)
- Test: `tests/unit/test_spawner_ca_bundle.py` (add gating tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_spawner_ca_bundle.py`:

```python
def test_trust_bundle_enabled_flag_reflects_config():
    enabled = _load({"custom.trust-bundle-enabled": True})
    assert enabled._trust_bundle_enabled is True

    disabled = _load({})  # key absent -> default False
    assert disabled._trust_bundle_enabled is False


def test_pre_spawn_hook_skips_trust_bundle_when_disabled():
    """The orchestrator must not touch volumes/env for the CA when the toggle
    is off, so the pack is unchanged on clusters without trust-manager."""
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
```

- [ ] **Step 2: Run tests to verify the gating test fails**

Run: `cd ~/gh/nebari-data-science-pack && python -m pytest tests/unit/test_spawner_ca_bundle.py -v`
Expected: `test_trust_bundle_enabled_flag_reflects_config` PASSES (flag already exists from Task 2); `test_pre_spawn_hook_skips_trust_bundle_when_disabled` PASSES already too IF the hook never calls it — but it will only be a meaningful regression guard once Step 3 adds the enabled wiring. Run it now and confirm both pass (no enabled call exists yet).

- [ ] **Step 3: Wire the enabled path into `_pre_spawn_hook`**

In `config/jupyterhub/01-spawner.py`, inside `_pre_spawn_hook`, find the Nebi auto-auth step:

```python
    # 1. Nebi auto-auth (non-fatal)
    if _nebi_auth_configured:
        log.debug("pre-spawn: running Nebi auto-auth for %s", username)
        await _nebi_pre_spawn_hook(spawner)
    else:
        log.debug("pre-spawn: Nebi auto-auth not configured, skipping")
```

Insert immediately after that block:

```python
    # 1b. Enterprise CA bundle (non-fatal). Off by default; on only when the
    #     cluster runs trust-manager and the deployer/operator enables it.
    if _trust_bundle_enabled:
        try:
            _setup_trust_bundle(spawner)
            log.info("trust-bundle: CA merge configured for %s", username)
        except Exception:
            log.exception(
                "trust-bundle: setup FAILED for %s (pod will still spawn)", username,
            )
    else:
        log.debug("trust-bundle: disabled, skipping CA merge for %s", username)
```

- [ ] **Step 4: Add the enabled-orchestrator test**

Append to `tests/unit/test_spawner_ca_bundle.py`:

```python
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
```

- [ ] **Step 5: Run the full file to verify all pass**

Run: `cd ~/gh/nebari-data-science-pack && python -m pytest tests/unit/test_spawner_ca_bundle.py -v`
Expected: PASS (all five tests)

- [ ] **Step 6: Run the full unit suite for regressions**

Run: `cd ~/gh/nebari-data-science-pack && python -m pytest tests/unit -v`
Expected: PASS (no regressions in storage / nss / chart-derived tests)

- [ ] **Step 7: Commit**

```bash
cd ~/gh/nebari-data-science-pack
git add config/jupyterhub/01-spawner.py tests/unit/test_spawner_ca_bundle.py
git commit -m "feat(spawner): gate CA bundle merge behind custom.trust-bundle-enabled"
```

---

## Manual verification (definition of done)

Not automatable here; perform on a cluster with NIC trust-manager + a TLS-inspecting proxy, with `custom.trust-bundle-enabled: true`:

- [ ] `pip install requests` from a clean user pod — no `--trusted-host`.
- [ ] `conda install <pkg>` from a configured channel — no `ssl_verify: false`.
- [ ] `git clone <https-repo>` whose TLS terminates at the proxy.
- [ ] On a cluster WITHOUT trust-manager, with the toggle on: pods still spawn (optional ConfigMap) and the merged file is just the system bundle.

---

## Self-Review notes

- **Spec coverage:** merge-via-init-container (Task 2), env vars (Task 2), gating + optional mount (Tasks 1+3), no pip.conf/.condarc (intentionally omitted per spec), tests (Tasks 2–3), manual DoD (above). NIC-side namespace selector is already satisfied — no task needed.
- **Type/name consistency:** volume names `org-ca`/`ca-merged`, init container `merge-ca-bundle`, merged path `/etc/ssl/certs-extra/ca-bundle.crt`, config keys `trust-bundle-enabled`/`trust-bundle-configmap`/`trust-bundle-key` — used identically across values.yaml, the hook, and every test.
