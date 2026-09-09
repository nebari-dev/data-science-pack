"""Point the preview deploy's JupyterLab image at this PR's own build.

build-images.yaml pushes a `pr-<number>` tag of the JupyterLab image for
PRs that change it. If that tag exists on the registry, this rewrites the
checked-out values.yaml's singleuser and per-profile image refs to it in
place; if not, values.yaml is left alone and the preview runs the pinned
image.

Usage:
    pipx run scripts/preview/pr_image.py set-tag --tag pr-205 [values.yaml]
"""

# /// script
# dependencies = ["ruamel.yaml==0.19.1"]
# ///

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]
# Run as a file by pipx: the repo isn't on sys.path, and this directory is,
# where http.py would shadow the stdlib `http` that urllib needs.
sys.path.remove(str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT))
from scripts.bump_image_tags import _bump_profile_list
from scripts.preview.http import request_json

DEFAULT_VALUES = REPO_ROOT / "values.yaml"


def tag_exists(image: str, tag: str) -> bool:
    """True if ``image:tag`` is an active tag on quay.io."""
    registry, repo = image.split("/", 1)
    if registry != "quay.io":
        raise ValueError(f"only quay.io images are supported, got {image!r}")
    body = request_json("GET", f"https://quay.io/api/v1/repository/{repo}/tag/?specificTag={tag}&onlyActiveTags=true")
    return bool(body.get("tags"))


def set_tag(values_path: Path, tag: str) -> bool:
    """Rewrite every jupyterlab image ref in ``values_path`` to ``tag``.

    Each profile's image selector keeps the pinned image as a second
    choice, so a reviewer can spawn either this PR's build or main's.
    """
    if not tag or any(c.isspace() for c in tag):
        raise ValueError(f"refusing to write empty/whitespace tag: {tag!r}")

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    data = yaml.load(values_path)
    singleuser = data["jupyterhub"]["singleuser"]["image"]
    pinned_ref = f'{singleuser["name"]}:{singleuser["tag"]}'
    changed = singleuser["tag"] != tag
    singleuser["tag"] = tag
    changed = _bump_profile_list(data, tag) | changed

    for profile in data["jupyterhub"]["custom"]["profiles"]:
        choices = profile.get("profile_options", {}).get("image", {}).get("choices", {})
        if choices and "main" not in choices:
            choices["main"] = {
                "display_name": f"{pinned_ref.rsplit('/', 1)[-1]} (main)",
                "kubespawner_override": {"image": pinned_ref},
            }
            changed = True

    if changed:
        yaml.dump(data, values_path)
    return changed


def _cmd_set_tag(args: argparse.Namespace) -> int:
    values_path = Path(args.values) if args.values else DEFAULT_VALUES
    image = YAML(typ="safe").load(values_path)["jupyterhub"]["singleuser"]["image"]["name"]
    if not tag_exists(image, args.tag):
        print(f"skipped: {image}:{args.tag} not on registry, keeping pinned tag")
        return 0
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
