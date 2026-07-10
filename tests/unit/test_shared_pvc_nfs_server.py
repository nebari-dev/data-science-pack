"""Regression test for shared-storage PV's NFS server endpoint.

The PV's `spec.nfs.server` field is mounted by kubelet in the host's
network namespace, which on most managed-Kubernetes clusters doesn't
resolve `*.svc.cluster.local`. The chart resolves the in-cluster NFS
Service's ClusterIP at install time via `helm lookup` and bakes the
IP into the PV. The kube-proxy iptables on every node make Service
IPs routable from the host namespace without any DNS lookup.

This test pins two behaviors:

1. **Fallback path** — when the Service doesn't exist at render time
   (true for `helm template`-only renders and the very first
   `helm install` before the Service is created), the PV falls back
   to the Service FQDN. Preserves backward compatibility for clusters
   whose host DNS DOES forward cluster.local to CoreDNS (k3s,
   microk8s with custom resolvers).
2. **Template still renders without errors** — guards against
   whitespace-stripping regressions in the template (the `-}}` /
   `}}` choices around variable assignments are easy to break).

The "live Service returns the ClusterIP" path is exercised end-to-end
in `helm install` (and any integration test that uses a real cluster
context). Pure `helm template` can't drive `lookup` because there's
no API server to query — that's by design and intentionally part of
the fallback contract this test pins.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rendered_chart(tmp_path_factory):
    """Render the chart with minimal values and return the full output."""
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm not on PATH")

    charts_dir = REPO_ROOT / "charts"
    has_deps = charts_dir.exists() and any(charts_dir.glob("jupyterhub-*.tgz"))
    if not has_deps:
        subprocess.run(
            [helm, "dependency", "update", str(REPO_ROOT)],
            capture_output=True, text=True, check=True,
        )

    values = tmp_path_factory.mktemp("values") / "values.yaml"
    values.write_text(
        "keycloak:\n  hostname: keycloak.example.com\n"
        "sharedStorage:\n  enabled: true\n  nfsServer:\n    enabled: true\n"
    )

    proc = subprocess.run(
        [helm, "template", "data-science-pack", str(REPO_ROOT),
         "-f", str(values), "--namespace", "jupyterhub"],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _extract_pv_block(rendered: str) -> str:
    """Return the YAML block for the chart's shared-storage PersistentVolume.

    Locates `kind: PersistentVolume` inside the shared-pvc.yaml source
    and returns content up to the next `---` document boundary.
    """
    # Pin to the chart's own shared-pvc.yaml — other subcharts (jupyterhub)
    # may emit unrelated PV manifests we shouldn't match.
    source_pat = r"^# Source: nebari-data-science-pack/templates/shared-pvc\.yaml$"
    blocks = re.split(r"^---$", rendered, flags=re.MULTILINE)
    for block in blocks:
        if re.search(source_pat, block, flags=re.MULTILINE) and \
           "kind: PersistentVolume" in block and \
           "kind: PersistentVolumeClaim" not in block:
            return block
    raise AssertionError(
        "Could not locate a PersistentVolume block from shared-pvc.yaml in "
        "the rendered chart output."
    )


def test_pv_falls_back_to_fqdn_when_service_not_present(rendered_chart):
    """No live Service in the `helm template` render context, so `lookup`
    returns nil and the PV's nfs.server falls back to the Service FQDN."""
    pv = _extract_pv_block(rendered_chart)
    match = re.search(r'^\s*server:\s*"?(?P<value>[^"\n]+)"?\s*$', pv, flags=re.MULTILINE)
    assert match, f"No server: line found in PV block:\n{pv}"
    server = match.group("value")
    assert server.endswith(".svc.cluster.local"), (
        f"PV nfs.server should fall back to the cluster-local FQDN when no "
        f"live Service is available; got {server!r}.\n"
        f"PV block:\n{pv}"
    )
    # Sanity: shape is <release>-nebari-data-science-pack-nfs.<ns>.svc.cluster.local
    assert "-nfs." in server, (
        f"FQDN should reference the chart's NFS Service; got {server!r}"
    )
    assert ".jupyterhub." in server, (
        f"FQDN should embed the release namespace; got {server!r}"
    )


def test_pv_template_renders_without_yaml_errors(rendered_chart):
    """Smoke test: the template-trimming combo (-}} vs }}) around the
    helm-lookup block is easy to break. If a `-}}` eats the newline
    before `nfs:`, the YAML parser produces 'mapping values not allowed
    in this context'. Catch that here so test_pv_falls_back_to_fqdn
    isn't the canary."""
    pv = _extract_pv_block(rendered_chart)
    # The fields directly above and below the lookup block should still be
    # on their own lines.
    assert re.search(
        r"^\s*persistentVolumeReclaimPolicy:\s*Retain\s*$",
        pv, flags=re.MULTILINE,
    ), f"persistentVolumeReclaimPolicy line malformed:\n{pv}"
    assert re.search(
        r"^\s*nfs:\s*$", pv, flags=re.MULTILINE,
    ), f"nfs: block-opener missing or malformed:\n{pv}"
