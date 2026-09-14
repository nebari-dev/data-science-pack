"""Structural tests for the hub-side enterprise CA bundle wiring in values.yaml.

The singleuser side is covered by ``test_spawner_ca_bundle.py`` (runtime Python
in ``01-spawner.py``). The hub-side wiring lives in the chart's static
``values.yaml`` — z2jh renders the hub Deployment from those values, so
there is no Python to unit-test. Instead we assert the shape of the values
file itself: the required volumes, mounts, init container, and env vars
are present under ``jupyterhub.hub.*`` with the expected shape. These
assertions pin the contract described in the ``trust-bundle-enabled``
docstring so a future edit that accidentally drops one of them fails CI
instead of silently breaking outbound HTTPS from the hub.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES_YAML = REPO_ROOT / "values.yaml"

MERGED = "/etc/ssl/certs-extra/ca-bundle.crt"


def _hub_values():
    """Load the ``jupyterhub.hub`` block from the chart's values.yaml."""
    with VALUES_YAML.open() as f:
        values = yaml.safe_load(f)
    return values["jupyterhub"]["hub"]


# ---------------------------------------------------------------------------
# extraVolumes — org-ca (optional ConfigMap) + ca-merged (emptyDir)
# ---------------------------------------------------------------------------

def test_extra_volumes_contains_org_ca_optional_configmap():
    """org-ca must be an OPTIONAL ConfigMap volume — missing trust-manager
    is a no-op, not a spawn/pod-start failure."""
    volumes = _hub_values()["extraVolumes"]
    org_ca = next((v for v in volumes if v["name"] == "org-ca"), None)
    assert org_ca is not None, "missing 'org-ca' entry in hub.extraVolumes"
    assert "configMap" in org_ca, "org-ca must be a ConfigMap volume"
    assert org_ca["configMap"].get("optional") is True, (
        "org-ca ConfigMap must be optional so hub startup does not depend on "
        "trust-manager being installed"
    )


def test_extra_volumes_contains_ca_merged_emptydir():
    """ca-merged is the emptyDir the merge init container writes into."""
    volumes = _hub_values()["extraVolumes"]
    merged = next((v for v in volumes if v["name"] == "ca-merged"), None)
    assert merged is not None, "missing 'ca-merged' entry in hub.extraVolumes"
    assert "emptyDir" in merged, "ca-merged must be an emptyDir volume"


def test_extra_volumes_preserves_pre_existing_chart_entries():
    """The two chart defaults (custom-config, oauth-client) must stay in the
    list so this edit does not regress the existing wiring."""
    names = {v["name"] for v in _hub_values()["extraVolumes"]}
    assert {"custom-config", "oauth-client"}.issubset(names)


# ---------------------------------------------------------------------------
# extraVolumeMounts — the merged bundle must be visible in the hub container
# ---------------------------------------------------------------------------

def test_extra_volume_mounts_exposes_ca_merged_at_expected_path():
    mounts = _hub_values()["extraVolumeMounts"]
    ca_merged = next((m for m in mounts if m["name"] == "ca-merged"), None)
    assert ca_merged is not None, "missing 'ca-merged' mount in hub.extraVolumeMounts"
    assert ca_merged["mountPath"] == "/etc/ssl/certs-extra", (
        "hub-side must use the same merged-bundle mount path as the "
        "singleuser side for path consistency"
    )


def test_extra_volume_mounts_preserves_pre_existing_chart_entries():
    names = {m["name"] for m in _hub_values()["extraVolumeMounts"]}
    assert {"custom-config", "oauth-client"}.issubset(names)


# ---------------------------------------------------------------------------
# initContainers — merge-ca-bundle produces /etc/ssl/certs-extra/ca-bundle.crt
# ---------------------------------------------------------------------------

def test_init_container_merge_ca_bundle_present():
    init_containers = _hub_values().get("initContainers", [])
    merge = next(
        (c for c in init_containers if c["name"] == "merge-ca-bundle"), None,
    )
    assert merge is not None, (
        "missing 'merge-ca-bundle' init container — hub cannot reach TLS-"
        "inspected external endpoints (Keycloak external URL, etc.) without it"
    )


def test_init_container_uses_hub_image():
    """The init container must read the SAME system CA the main container
    has. Using the hub image guarantees that; a generic busybox would not.

    Kept as a soft assertion (contains 'jupyterhub' in name) so a maintainer
    who bumps the tag doesn't have to update this test in lockstep with the
    hub.image field. If the image reference is refactored to a chart-side
    helper later, this assertion can be removed.
    """
    init_containers = _hub_values().get("initContainers", [])
    merge = next(c for c in init_containers if c["name"] == "merge-ca-bundle")
    hub_image = _hub_values()["image"]
    assert merge["image"] == f'{hub_image["name"]}:{hub_image["tag"]}', (
        "merge-ca-bundle image must match hub.image so the system CA store "
        "the init container reads matches what the hub container has at "
        "runtime; if you bump hub.image, bump the init container image too"
    )


def test_init_container_appends_org_ca_conditionally():
    """The shell command must:
      1. Copy the image's system CA into /merged/ca-bundle.crt (always)
      2. Append org CA IFF the mounted file exists (so missing trust-manager
         is a no-op — merged bundle == system bundle)
    """
    init_containers = _hub_values().get("initContainers", [])
    merge = next(c for c in init_containers if c["name"] == "merge-ca-bundle")
    # command list is ['/bin/sh', '-c', '<script>'] — join defensively
    script = merge["command"][-1]
    assert "cp /etc/ssl/certs/ca-certificates.crt /merged/ca-bundle.crt" in script
    assert "if [ -f /org-ca/" in script and "]; then" in script
    assert "cat /org-ca/" in script and ">> /merged/ca-bundle.crt" in script


def test_init_container_mounts_org_ca_and_merged():
    init_containers = _hub_values().get("initContainers", [])
    merge = next(c for c in init_containers if c["name"] == "merge-ca-bundle")
    mounts = {m["name"]: m["mountPath"] for m in merge["volumeMounts"]}
    assert mounts.get("org-ca") == "/org-ca"
    assert mounts.get("ca-merged") == "/merged"


def test_init_container_declares_non_root_security_context():
    """z2jh's pod-level ``securityContext`` sets ``runAsNonRoot: true`` but
    leaves ``runAsUser`` unset, so containers whose image default is root
    are blocked by kubelet with ``container has runAsNonRoot and image
    will run as root``. The hub image ships with the default USER as root,
    so ``merge-ca-bundle`` (which uses the hub image) must set its own
    ``runAsUser`` — matching the hub container itself keeps ownership on
    the shared ``ca-merged`` emptyDir coherent.
    """
    init_containers = _hub_values().get("initContainers", [])
    merge = next(c for c in init_containers if c["name"] == "merge-ca-bundle")
    sc = merge.get("securityContext") or {}
    assert sc.get("runAsUser") == 1000, (
        "merge-ca-bundle must set runAsUser=1000 to satisfy z2jh's pod-level "
        "runAsNonRoot=true — the hub image's default USER is root"
    )
    assert sc.get("runAsGroup") == 1000, (
        "runAsGroup should match runAsUser so ownership of the shared "
        "ca-merged emptyDir is consistent with the hub container"
    )
    assert sc.get("allowPrivilegeEscalation") is False
    assert "ALL" in (sc.get("capabilities") or {}).get("drop", []), (
        "drop ALL capabilities to match the hub container's hardening"
    )


# ---------------------------------------------------------------------------
# extraEnv — five CA env vars for requests / tornado / curl / node / git
# ---------------------------------------------------------------------------

def test_extra_env_sets_all_five_ca_env_vars():
    """All five must point at the merged bundle. Rust tools (rustls-native-
    certs) honour SSL_CERT_FILE but do not iterate SSL_CERT_DIR — the
    explicit file path is required for pixi/rattler/uv. requests and tornado
    honour REQUESTS_CA_BUNDLE / SSL_CERT_FILE respectively; curl / node / git
    each have their own env var. Setting all five keeps every outbound HTTPS
    path in the hub aligned on the merged bundle."""
    env = _hub_values()["extraEnv"]
    for var in (
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "NODE_EXTRA_CA_CERTS",
        "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO",
    ):
        assert env.get(var) == MERGED, (
            f"hub.extraEnv[{var!r}] must be {MERGED!r} (points at the merged "
            f"bundle the merge-ca-bundle init container produces)"
        )


def test_extra_env_preserves_jupyterhub_oidc_client_secret():
    """The existing OIDC client-secret env var must stay wired to the
    operator-provisioned Secret; regressing this breaks OAuth."""
    entry = _hub_values()["extraEnv"].get("JUPYTERHUB_OIDC_CLIENT_SECRET")
    assert entry is not None
    ref = entry.get("valueFrom", {}).get("secretKeyRef", {})
    assert ref.get("key") == "client-secret"
    assert ref.get("optional") is True


# ---------------------------------------------------------------------------
# Cross-cutting: values must reference the same trust-bundle-configmap that
# 01-spawner.py's _setup_trust_bundle reads at spawn time (via
# custom.trust-bundle-configmap). Hub-side is static and reads the chart's
# default value directly; if a deployer changes the value only in
# `custom.trust-bundle-configmap`, the hub-side static reference still
# points at the default — this test pins that alignment so any drift
# gets caught in CI.
# ---------------------------------------------------------------------------

def test_hub_org_ca_configmap_matches_singleuser_default():
    """The chart default ``custom.trust-bundle-configmap`` MUST match the
    ``configMap.name`` in the hub-side org-ca volume; otherwise deployers who
    accept the default get an inconsistent picture (hub reads
    ``nebari-trust-bundle``, singleuser also reads ``nebari-trust-bundle``,
    fine; but any drift means one side would look up a ConfigMap that does
    not exist)."""
    with VALUES_YAML.open() as f:
        values = yaml.safe_load(f)
    singleuser_default = values["jupyterhub"]["custom"]["trust-bundle-configmap"]
    volumes = _hub_values()["extraVolumes"]
    org_ca = next(v for v in volumes if v["name"] == "org-ca")
    assert org_ca["configMap"]["name"] == singleuser_default, (
        "hub org-ca ConfigMap name must match "
        "jupyterhub.custom.trust-bundle-configmap"
    )
