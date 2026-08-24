"""Behavioural tests for scripts/bump_image_tags.py.

Drives the script's public ``bump()`` function against a minimal values.yaml
fixture and asserts the resulting file content, not internal helpers.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from scripts.bump_image_tags import bump

HUB_IMAGE = "quay.io/nebari/nebari-data-science-pack-jupyterhub"

FIXTURE = f"""\
jupyterhub:
  hub:
    image:
      name: {HUB_IMAGE}
      tag: sha-oldhub
    initContainers:
      - name: merge-ca-bundle
        image: {HUB_IMAGE}:sha-oldinit
  singleuser:
    image:
      name: quay.io/nebari/nebari-data-science-pack-jupyterlab
      tag: sha-oldsingle
"""


def _write_fixture(tmp_path: Path) -> Path:
    values_path = tmp_path / "values.yaml"
    values_path.write_text(FIXTURE)
    return values_path


def _load(values_path: Path):
    yaml = YAML(typ="safe")
    with values_path.open() as f:
        return yaml.load(f)


def test_bump_syncs_merge_ca_bundle_init_container_to_new_hub_tag(tmp_path):
    """merge-ca-bundle must track hub.image so the init container's system
    CA store matches what the hub container runs at runtime."""
    values_path = _write_fixture(tmp_path)

    bump(values_path, "newsha")

    data = _load(values_path)
    hub = data["jupyterhub"]["hub"]
    merge = next(
        c for c in hub["initContainers"] if c["name"] == "merge-ca-bundle"
    )
    assert merge["image"] == f"{HUB_IMAGE}:sha-newsha"
