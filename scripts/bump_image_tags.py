"""Bump image tags in values.yaml after a build-images workflow run.

Updates `jupyterhub.hub.image.tag` and `jupyterhub.singleuser.image.tag` to
`sha-<short>`. Uses ruamel.yaml round-trip mode to preserve comments and
formatting in the file.

Usage:
    python scripts/bump_image_tags.py <short-sha> [path/to/values.yaml]
"""

from __future__ import annotations

import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALUES = REPO_ROOT / "values.yaml"

TARGETS = (
    ("jupyterhub", "hub", "image", "tag"),
    ("jupyterhub", "singleuser", "image", "tag"),
)


def bump(values_path: Path, short_sha: str) -> bool:
    """Set both target tags to ``sha-<short_sha>``. Returns True if file changed."""
    if not short_sha or any(c.isspace() for c in short_sha):
        raise ValueError(f"refusing to write empty/whitespace sha: {short_sha!r}")

    new_tag = f"sha-{short_sha}"

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096  # don't reflow long lines
    yaml.indent(mapping=2, sequence=4, offset=2)

    data = yaml.load(values_path)

    changed = False
    for keys in TARGETS:
        node = data
        for k in keys[:-1]:
            node = node[k]
        leaf = keys[-1]
        if node[leaf] != new_tag:
            node[leaf] = new_tag
            changed = True

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
