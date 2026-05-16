"""Bump image tags in values.yaml after a build-images workflow run.

Updates every image reference in ``values.yaml`` to ``sha-<short>``:

* ``jupyterhub.hub.image.tag`` (string)
* ``jupyterhub.singleuser.image.tag`` (string)
* Per-profile ``profile_list[*].kubespawner_override.image`` (full ref)
* Per-profile ``profile_list[*].profile_options.image.choices.<key>.display_name``
* Per-profile ``profile_list[*].profile_options.image.choices.<key>.kubespawner_override.image``

Uses ruamel.yaml round-trip mode to preserve comments and formatting.

Usage:
    python scripts/bump_image_tags.py <short-sha> [path/to/values.yaml]
"""

from __future__ import annotations

import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALUES = REPO_ROOT / "values.yaml"

TAG_TARGETS = (
    ("jupyterhub", "hub", "image", "tag"),
    ("jupyterhub", "singleuser", "image", "tag"),
)

JUPYTERLAB_IMAGE = "quay.io/nebari/nebari-data-science-pack-jupyterlab"
JUPYTERLAB_DISPLAY_PREFIX = "nebari-data-science-pack-jupyterlab"


def _bump_tag_leaves(data, new_tag: str) -> bool:
    changed = False
    for keys in TAG_TARGETS:
        node = data
        for k in keys[:-1]:
            node = node[k]
        leaf = keys[-1]
        if node[leaf] != new_tag:
            node[leaf] = new_tag
            changed = True
    return changed


def _bump_profile_list(data, new_tag: str) -> bool:
    """Walk profile_list and rewrite every jupyterlab image ref to ``new_tag``."""
    full_ref = f"{JUPYTERLAB_IMAGE}:{new_tag}"
    display_ref = f"{JUPYTERLAB_DISPLAY_PREFIX}:{new_tag}"

    # The chart's profile_list is loaded from ``jupyterhub.custom.profiles``
    # by 01-spawner.py, not z2jh's ``singleuser.profileList``.
    profiles = data.get("jupyterhub", {}).get("custom", {}).get("profiles", [])

    changed = False
    for profile in profiles:
        outer = profile.get("kubespawner_override", {})
        if outer.get("image", "").startswith(JUPYTERLAB_IMAGE + ":") and outer["image"] != full_ref:
            outer["image"] = full_ref
            changed = True

        choices = (
            profile.get("profile_options", {})
            .get("image", {})
            .get("choices", {})
        )
        for choice in choices.values():
            if choice.get("display_name", "").startswith(JUPYTERLAB_DISPLAY_PREFIX + ":") and choice["display_name"] != display_ref:
                choice["display_name"] = display_ref
                changed = True
            ks = choice.get("kubespawner_override", {})
            if ks.get("image", "").startswith(JUPYTERLAB_IMAGE + ":") and ks["image"] != full_ref:
                ks["image"] = full_ref
                changed = True

    return changed


def bump(values_path: Path, short_sha: str) -> bool:
    """Rewrite every image ref in ``values_path`` to ``sha-<short_sha>``."""
    if not short_sha or any(c.isspace() for c in short_sha):
        raise ValueError(f"refusing to write empty/whitespace sha: {short_sha!r}")

    new_tag = f"sha-{short_sha}"

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    data = yaml.load(values_path)
    changed = _bump_tag_leaves(data, new_tag) | _bump_profile_list(data, new_tag)
    if changed:
        yaml.dump(data, values_path)
    return changed


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) > 3:
        print(__doc__, file=sys.stderr)
        return 2
    short_sha = argv[1]
    values_path = Path(argv[2]) if len(argv) == 3 else DEFAULT_VALUES
    changed = bump(values_path, short_sha)
    print(f"{'updated' if changed else 'unchanged'}: {values_path} -> sha-{short_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
