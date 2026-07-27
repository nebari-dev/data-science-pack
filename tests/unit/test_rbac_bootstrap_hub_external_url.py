"""Regression test: rbac-bootstrap Job's HUB_EXTERNAL_URL on a zero-config
deployment (only `keycloak.hostname` set, `nebariapp.hostname` left empty
for auto-derivation).

The Job template used to build this URL from `.Values.nebariapp.hostname`
directly instead of the chart's `hubHostname` helper. On a zero-config
deployment that raw value is empty, so the rendered URL came out as the
bare string "https:" — Keycloak then rejected the client update with
"Root URL is not a valid URL", which made the post-install/post-upgrade
hook Job fail its backoff limit and blocked the whole `helm upgrade`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_hub_external_url(tmp_path_factory) -> str:
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
    values.write_text("keycloak:\n  hostname: keycloak.example.com\n")

    proc = subprocess.run(
        [helm, "template", "data-science-pack", str(REPO_ROOT),
         "-f", str(values), "--namespace", "jupyterhub",
         "--show-only", "templates/keycloak-rbac-bootstrap-job.yaml"],
        capture_output=True, text=True, check=True,
    )

    match = re.search(
        r'- name: HUB_EXTERNAL_URL\n\s+value: "([^"]*)"', proc.stdout,
    )
    if not match:
        raise AssertionError(
            "HUB_EXTERNAL_URL not found in rendered rbac-bootstrap Job. "
            "Did the env var layout change?"
        )
    return match.group(1)


def test_hub_external_url_derived_from_keycloak_hostname_on_zero_config(
    tmp_path_factory,
):
    url = _render_hub_external_url(tmp_path_factory)
    assert url == "https://hub.example.com"
