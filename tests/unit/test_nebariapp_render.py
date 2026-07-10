"""Render tests for templates/nebariapp.yaml's auth block.

The template used to hand-render a subset of the operator's AuthConfig
fields, silently dropping the rest (groups, issuerURL, spaClient, ...).
These tests pin the passthrough contract: every `nebariapp.auth.*` value
lands on the rendered NebariApp spec.auth, and the values.yaml defaults
survive a render with no auth overrides.

Follows test_chart_derived.py's convention: shell out to `helm template`,
assert on the emitted text, no pyyaml dependency.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _render(values_yaml: str, tmp_path: Path) -> str:
    """helm-template the chart with the given values file content and
    return the NebariApp document from the multi-doc output."""
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

    values = tmp_path / "values.yaml"
    values.write_text(values_yaml)
    proc = subprocess.run(
        [helm, "template", "data-science-pack", str(REPO_ROOT),
         "-f", str(values), "--namespace", "jupyterhub"],
        capture_output=True, text=True, check=True,
    )
    docs = [d for d in proc.stdout.split("\n---\n") if "kind: NebariApp" in d]
    assert len(docs) == 1, "expected exactly one NebariApp in rendered output"
    return docs[0]


def _auth_block(nebariapp_doc: str) -> str:
    """Slice out the `  auth:` block (2-space indent) of the NebariApp spec."""
    match = re.search(
        r"^  auth:\n((?:    .*\n?)+)", nebariapp_doc, flags=re.MULTILINE
    )
    assert match, "no auth block rendered on the NebariApp"
    return match.group(0)


def test_auth_fields_beyond_the_hand_rendered_subset_reach_the_nebariapp(tmp_path):
    """Fields the old template dropped must land on spec.auth."""
    auth = _auth_block(_render(
        """\
keycloak:
  hostname: keycloak.example.com
nebariapp:
  auth:
    groups:
      - data-science-users
    issuerURL: https://keycloak.example.com/realms/nebari
    denyRedirect: true
    spaClient:
      enabled: true
    deviceFlowClient:
      enabled: true
    clientSecretRef:
      name: my-oidc-secret
    keycloakConfig:
      clientRoles:
        - analyst
""",
        tmp_path,
    ))
    assert "- data-science-users" in auth
    assert "issuerURL: https://keycloak.example.com/realms/nebari" in auth
    assert "denyRedirect: true" in auth
    assert re.search(r"spaClient:\n\s+enabled: true", auth)
    assert re.search(r"deviceFlowClient:\n\s+enabled: true", auth)
    assert re.search(r"clientSecretRef:\n\s+name: my-oidc-secret", auth)
    assert re.search(r"keycloakConfig:\n\s+clientRoles:\n\s+- analyst", auth)


def test_default_auth_block_renders_from_values_yaml(tmp_path):
    """Zero-config render keeps the chart's shipped auth defaults."""
    auth = _auth_block(_render(
        "keycloak:\n  hostname: keycloak.example.com\n", tmp_path
    ))
    assert "enabled: true" in auth
    assert "provider: keycloak" in auth
    assert "provisionClient: true" in auth
    assert "redirectURI: /hub/oauth_callback" in auth
    assert "enforceAtGateway: false" in auth
    assert "forwardAccessToken: false" in auth
    for scope in ("openid", "profile", "email", "groups"):
        assert f"- {scope}" in auth
