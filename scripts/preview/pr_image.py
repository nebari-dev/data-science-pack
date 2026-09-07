"""Point the preview deploy's JupyterLab image at this PR's own build.

build-images.yaml builds and pushes a `pr-<number>` tag of the JupyterLab
image for every non-fork PR that touches images/**. This rewrites the
checked-out values.yaml's singleuser and per-profile image refs to that
tag in place; nothing here is committed, it only affects this ephemeral
k8s-preview deploy.

Usage:
    python -m scripts.preview.pr_image set-tag --tag pr-205 [values.yaml]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML

from scripts.bump_image_tags import _bump_profile_list

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VALUES = REPO_ROOT / "values.yaml"


def set_tag(values_path: Path, tag: str) -> bool:
    """Rewrite every jupyterlab image ref in ``values_path`` to ``tag``."""
    if not tag or any(c.isspace() for c in tag):
        raise ValueError(f"refusing to write empty/whitespace tag: {tag!r}")

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    data = yaml.load(values_path)
    singleuser = data["jupyterhub"]["singleuser"]["image"]
    changed = singleuser["tag"] != tag
    singleuser["tag"] = tag
    changed = _bump_profile_list(data, tag) | changed
    if changed:
        yaml.dump(data, values_path)
    return changed


def _cmd_set_tag(args: argparse.Namespace) -> int:
    values_path = Path(args.values) if args.values else DEFAULT_VALUES
    changed = set_tag(values_path, args.tag)
    print(f"{'updated' if changed else 'unchanged'}: {values_path} -> {args.tag}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr_image")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("set-tag")
    p.add_argument("--tag", required=True)
    p.add_argument("values", nargs="?")
    p.set_defaults(func=_cmd_set_tag)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
