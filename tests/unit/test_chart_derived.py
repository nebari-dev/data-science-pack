"""Regression test for chart-derived `get_chart_config`.

Renders the chart with `helm template`, extracts the generated
`00-chart-derived.py` from the hub-config ConfigMap, exec's it with a
stub `z2jh`, and asserts that the chart-rendered default wins when
z2jh's `get_config("custom.<key>")` returns the empty string that
values.yaml's `custom.* :  ""` placeholders bake into the hub Secret.

This is the trap that took down the bind_url path in production:
z2jh sees the key (because it's in the Secret) but the value is "",
so a naive `if value is not _MISSING:` check short-circuits BEFORE the
helper ever consults `_CHART_DERIVED`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _extract_configmap_key(rendered: str, key: str) -> str:
    """Pull a single literal-block ConfigMap data key out of `helm template`
    output. Avoids the pyyaml dependency by relying on the well-known
    `<key>: |\n    <indented body>` layout helm emits.

    Body lines are 4-space-indented; blank lines render with no indent.
    The block ends at the next sibling data key (2-space indent + name + ":")
    or the next top-level YAML key (no indent).
    """
    start = rf"^  {re.escape(key)}: \|\n"
    body = r"((?:(?:    .*|)\n)+?)"
    end = r"(?=^  [\w.-]+: |^[\w-]+: |^---|\Z)"
    match = re.search(start + body + end, rendered, flags=re.MULTILINE)
    if not match:
        raise AssertionError(
            f"Key {key!r} not found in rendered chart output. "
            "Did the ConfigMap layout change?"
        )
    return textwrap.dedent(match.group(1))


@pytest.fixture(scope="module")
def rendered_chart_derived(tmp_path_factory):
    """Render the chart with a minimal zero-config values file and return
    the source of `00-chart-derived.py` as a string."""
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm not on PATH")

    # Subchart deps must be present before `helm template` will render.
    # Build them on demand so this test works on a fresh CI checkout that
    # hasn't run `helm dependency update`.
    charts_dir = REPO_ROOT / "charts"
    has_deps = charts_dir.exists() and any(charts_dir.glob("jupyterhub-*.tgz"))
    if not has_deps:
        subprocess.run(
            [helm, "dependency", "build", str(REPO_ROOT)],
            capture_output=True, text=True, check=True,
        )

    values = tmp_path_factory.mktemp("values") / "values.yaml"
    values.write_text("keycloak:\n  hostname: keycloak.example.com\n")

    proc = subprocess.run(
        [helm, "template", "data-science-pack", str(REPO_ROOT),
         "-f", str(values), "--namespace", "jupyterhub"],
        capture_output=True, text=True, check=True,
    )

    return _extract_configmap_key(proc.stdout, "00-chart-derived.py")


def _exec_chart_derived(source: str, z2jh_values: dict) -> dict:
    """Exec the rendered file in a fresh namespace with a stub z2jh that
    returns whatever the test put into `z2jh_values`. Returns the namespace
    so the test can call `get_chart_config` directly."""
    fake = types.ModuleType("z2jh")

    _DEFAULT_SENTINEL = object()  # noqa: N806 — z2jh-style sentinel

    def get_config(key, default=_DEFAULT_SENTINEL):
        if key in z2jh_values:
            return z2jh_values[key]
        if default is _DEFAULT_SENTINEL:
            return None
        return default

    fake.get_config = get_config
    prior = sys.modules.get("z2jh")
    sys.modules["z2jh"] = fake
    try:
        ns: dict = {}
        exec(compile(source, "<00-chart-derived.py>", "exec"), ns)
        return ns
    finally:
        if prior is None:
            sys.modules.pop("z2jh", None)
        else:
            sys.modules["z2jh"] = prior


def test_get_chart_config_returns_chart_default_when_z2jh_returns_empty_string(rendered_chart_derived):
    """Pin the contract: an empty string from z2jh MUST fall through to
    the chart-rendered _CHART_DERIVED. Without this, every `custom.*`
    placeholder in values.yaml short-circuits the helper, the hub binds
    to 0.0.0.0:8000, OAuth redirects break, and the cluster is broken."""
    ns = _exec_chart_derived(
        rendered_chart_derived,
        z2jh_values={"custom.external-url": ""},
    )
    got = ns["get_chart_config"]("external-url")
    assert got == "hub.example.com", (
        f"get_chart_config('external-url') returned {got!r}; "
        "when z2jh.get_config returns '' the helper must fall through "
        "to _CHART_DERIVED (rendered from keycloak.hostname). Otherwise "
        "02-jhub-apps.py sets bind_url = http://0.0.0.0:8000 and the "
        "external OAuth redirect is broken."
    )


def test_get_chart_config_returns_chart_default_when_z2jh_key_absent(rendered_chart_derived):
    """If z2jh doesn't know the key at all (no Secret entry), the chart
    rendering must still win — i.e. _MISSING sentinel + _CHART_DERIVED."""
    ns = _exec_chart_derived(rendered_chart_derived, z2jh_values={})
    got = ns["get_chart_config"]("external-url")
    assert got == "hub.example.com", (
        f"get_chart_config('external-url') returned {got!r}; "
        "when z2jh has no key, the helper must return _CHART_DERIVED."
    )


def test_get_chart_config_explicit_override_wins(rendered_chart_derived):
    """An explicit non-empty deployer override must win over the chart
    default — that's the whole point of leaving the override surface."""
    ns = _exec_chart_derived(
        rendered_chart_derived,
        z2jh_values={"custom.external-url": "explicit.example.com"},
    )
    got = ns["get_chart_config"]("external-url")
    assert got == "explicit.example.com"
