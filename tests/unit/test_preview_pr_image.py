"""Tests for scripts/preview/pr_image.py's PR-jupyterlab-image rewriter."""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from scripts.preview import pr_image

HUB_IMAGE = "quay.io/nebari/nebari-data-science-pack-jupyterhub"
LAB_IMAGE = "quay.io/nebari/nebari-data-science-pack-jupyterlab"

FIXTURE = f"""\
jupyterhub:
  hub:
    image:
      name: {HUB_IMAGE}
      tag: preview
    initContainers:
      - name: merge-ca-bundle
        image: {HUB_IMAGE}:preview
  singleuser:
    image:
      name: {LAB_IMAGE}
      tag: sha-oldsha
  custom:
    profiles:
      - slug: small-instance
        kubespawner_override:
          image: {LAB_IMAGE}:sha-oldsha
        profile_options:
          image:
            choices:
              default:
                display_name: "nebari-data-science-pack-jupyterlab:sha-oldsha"
                kubespawner_override:
                  image: {LAB_IMAGE}:sha-oldsha
"""


def _write_fixture(tmp_path: Path) -> Path:
    values_path = tmp_path / "values.yaml"
    values_path.write_text(FIXTURE)
    return values_path


def _load(values_path: Path):
    yaml = YAML(typ="safe")
    with values_path.open() as f:
        return yaml.load(f)


def test_set_tag_updates_singleuser_image_tag(tmp_path):
    values_path = _write_fixture(tmp_path)

    pr_image.set_tag(values_path, "pr-205")

    data = _load(values_path)
    assert data["jupyterhub"]["singleuser"]["image"]["tag"] == "pr-205"


def test_set_tag_updates_the_default_profile_choice_kubespawner_override(tmp_path):
    # The default: true profile choice is what a bodiless spawn POST actually
    # uses (see tests/unit/test_image_ref_sync.py), so this ref has to move.
    values_path = _write_fixture(tmp_path)

    pr_image.set_tag(values_path, "pr-205")

    data = _load(values_path)
    profile = data["jupyterhub"]["custom"]["profiles"][0]
    assert profile["kubespawner_override"]["image"] == f"{LAB_IMAGE}:pr-205"
    choice = profile["profile_options"]["image"]["choices"]["default"]
    assert choice["kubespawner_override"]["image"] == f"{LAB_IMAGE}:pr-205"
    assert choice["display_name"] == "nebari-data-science-pack-jupyterlab:pr-205"


def test_set_tag_does_not_touch_hub_image_or_init_container(tmp_path):
    # values.preview.yaml separately pins the hub image to the locally
    # built, kind-loaded build; this rewrite is scoped to the lab image only.
    values_path = _write_fixture(tmp_path)

    pr_image.set_tag(values_path, "pr-205")

    data = _load(values_path)
    hub = data["jupyterhub"]["hub"]
    assert hub["image"]["tag"] == "preview"
    assert hub["initContainers"][0]["image"] == f"{HUB_IMAGE}:preview"


def test_set_tag_rejects_empty_tag(tmp_path):
    values_path = _write_fixture(tmp_path)

    try:
        pr_image.set_tag(values_path, "")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_set_tag_returns_false_when_already_at_that_tag(tmp_path):
    values_path = _write_fixture(tmp_path)
    pr_image.set_tag(values_path, "pr-205")

    changed = pr_image.set_tag(values_path, "pr-205")

    assert changed is False


def test_main_set_tag_writes_to_given_path(tmp_path):
    values_path = _write_fixture(tmp_path)

    rc = pr_image.main(["pr_image", "set-tag", "--tag", "pr-205", str(values_path)])

    assert rc == 0
    data = _load(values_path)
    assert data["jupyterhub"]["singleuser"]["image"]["tag"] == "pr-205"
