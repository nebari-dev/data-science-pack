"""Point the preview deploy's JupyterLab image at this PR's own build.

build-images.yaml pushes a `pr-<number>` tag of the JupyterLab image for
PRs that change it. If that tag exists on the registry, every jupyterlab
image ref in values.yaml is rewritten to it and the pinned image is kept
as a second choice in each profile's image selector. If not, values.yaml
is left alone and the preview runs the pinned image.

values.preview.yaml's profiles carry no image refs of their own; this
fills in the same image and selector so the pin lives in one place.

Usage:
    pipx run scripts/preview/pr_image.py set-tag --tag pr-205 [values.yaml ...]
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
from scripts.bump_image_tags import DEFAULT_VALUES, JUPYTERLAB_IMAGE, _bump_profile_list
from scripts.preview.http import request_json

PREVIEW_VALUES = REPO_ROOT / "values.preview.yaml"


def tag_exists(image: str, tag: str) -> bool:
    """True if ``image:tag`` is an active tag on quay.io."""
    registry, repo = image.split("/", 1)
    if registry != "quay.io":
        raise ValueError(f"only quay.io images are supported, got {image!r}")
    body = request_json("GET", f"https://quay.io/api/v1/repository/{repo}/tag/?specificTag={tag}&onlyActiveTags=true")
    return bool(body.get("tags"))


def _choice(ref: str, suffix: str = "") -> dict:
    return {"display_name": ref.rsplit("/", 1)[-1] + suffix, "kubespawner_override": {"image": ref}}


def set_image(values_path: Path, ref: str, pinned_ref: str) -> bool:
    """Point ``values_path``'s singleuser image (if any) and every profile at ``ref``.

    Profiles without an image get one plus an image selector; when ``ref``
    differs from ``pinned_ref`` the selector also offers the pinned image.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    data = yaml.load(values_path)
    tag = ref.rsplit(":", 1)[1]
    changed = False
    singleuser = data["jupyterhub"].get("singleuser", {}).get("image")
    if singleuser and singleuser["tag"] != tag:
        singleuser["tag"] = tag
        changed = True
    changed = _bump_profile_list(data, tag) | changed

    for profile in data["jupyterhub"]["custom"]["profiles"]:
        outer = profile.setdefault("kubespawner_override", {})
        if not outer.get("image", "").startswith(JUPYTERLAB_IMAGE + ":"):
            outer["image"] = ref
            profile.setdefault("profile_options", {})["image"] = {
                "display_name": "Image",
                "choices": {"default": {**_choice(ref), "default": True}},
            }
            changed = True
        choices = profile.get("profile_options", {}).get("image", {}).get("choices")
        if ref != pinned_ref and choices is not None and "main" not in choices:
            choices["main"] = _choice(pinned_ref, " (main)")
            changed = True

    if changed:
        yaml.dump(data, values_path)
    return changed


def _cmd_set_tag(args: argparse.Namespace) -> int:
    paths = [Path(v) for v in args.values] or [DEFAULT_VALUES, PREVIEW_VALUES]
    singleuser = YAML(typ="safe").load(DEFAULT_VALUES)["jupyterhub"]["singleuser"]["image"]
    pinned_ref = f'{singleuser["name"]}:{singleuser["tag"]}'
    if tag_exists(JUPYTERLAB_IMAGE, args.tag):
        ref = f"{JUPYTERLAB_IMAGE}:{args.tag}"
    else:
        ref = pinned_ref
        print(f"{JUPYTERLAB_IMAGE}:{args.tag} not on registry, using pinned {pinned_ref}")
    for values_path in paths:
        changed = set_image(values_path, ref, pinned_ref)
        print(f"{'updated' if changed else 'unchanged'}: {values_path} -> {ref}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr_image")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("set-tag")
    p.add_argument("--tag", required=True)
    p.add_argument("values", nargs="*")
    p.set_defaults(func=_cmd_set_tag)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
