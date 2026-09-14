"""Structural tests that every hand-editable jupyterlab image reference in
values.yaml agrees with ``jupyterhub.singleuser.image``.

e2e derives its cache key and kind side-load from ``singleuser.image``, but
the pod that actually spawns comes from the *default profile*: the spawn
POST has no body, so kubespawner falls through to the ``default: true``
profile, whose ``profile_options`` default choice overwrites the image.
A bump that moves ``singleuser.image.tag`` but misses a profile ref would
therefore have e2e report the new tag while the pod pulls the old image —
green CI on stale code. ``scripts/bump_image_tags.py`` keeps these in sync
on the automated path; these asserts catch the hand-edit path.

Scope mirrors the script's guards exactly: only refs pointing at the
jupyterlab image (``JUPYTERLAB_IMAGE:`` / ``JUPYTERLAB_DISPLAY_PREFIX:``)
must agree. Additional choices pointing elsewhere (e.g. the R image in
server-profiles.md) and variant profiles that carry no image are
intentionally out of scope, just as the script leaves them alone.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.bump_image_tags import JUPYTERLAB_DISPLAY_PREFIX, JUPYTERLAB_IMAGE

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES_YAML = REPO_ROOT / "values.yaml"


def _jupyterhub_values():
    with VALUES_YAML.open() as f:
        return yaml.safe_load(f)["jupyterhub"]


def _singleuser_ref(jh):
    image = jh["singleuser"]["image"]
    return f'{image["name"]}:{image["tag"]}'


def test_singleuser_image_is_the_jupyterlab_image():
    """Anchor for the guarded tests below: if singleuser.image were renamed
    away from the script's JUPYTERLAB_IMAGE constant, every guard would stop
    matching and the other tests would pass vacuously. Fail loudly instead."""
    jh = _jupyterhub_values()
    assert jh["singleuser"]["image"]["name"] == JUPYTERLAB_IMAGE, (
        "singleuser.image.name no longer matches bump_image_tags.py's "
        "JUPYTERLAB_IMAGE — update the script constant and these tests "
        "together, or the sync guards silently stop guarding"
    )


def test_profile_images_match_singleuser():
    """Every jupyterlab-tagged image ref in the profiles — the outer
    kubespawner_override.image and any profile_options choice pointing at
    the jupyterlab image — must equal singleuser.image. The default choice
    is what the spawned pod actually runs."""
    jh = _jupyterhub_values()
    ref = _singleuser_ref(jh)
    profiles = jh["custom"]["profiles"]
    assert profiles, "no profiles found under jupyterhub.custom.profiles"
    for profile in profiles:
        slug = profile["slug"]
        outer = profile.get("kubespawner_override", {}).get("image", "")
        if outer.startswith(JUPYTERLAB_IMAGE + ":"):
            assert outer == ref, (
                f"profile {slug!r}: kubespawner_override.image does not "
                f"match singleuser.image ({ref}) — jhub-apps' Create App "
                "shows this value; a half-bump here spawns a stale image"
            )
        options = profile.get("profile_options", {}).get("image", {})
        for name, choice in options.get("choices", {}).items():
            img = choice.get("kubespawner_override", {}).get("image", "")
            if img.startswith(JUPYTERLAB_IMAGE + ":"):
                assert img == ref, (
                    f"profile {slug!r} choice {name!r}: image does not match "
                    f"singleuser.image ({ref}) — this choice overwrites the "
                    "pod image at spawn, so e2e would report the new tag "
                    "while the pod pulls the old one"
                )


def test_profile_choice_display_names_match_image():
    """Every choice whose display_name carries the jupyterlab image name
    embeds the tag the profile selector shows; it must name the image
    singleuser.image actually spawns."""
    jh = _jupyterhub_values()
    tag = jh["singleuser"]["image"]["tag"]
    expected = f"{JUPYTERLAB_DISPLAY_PREFIX}:{tag}"
    for profile in _jupyterhub_values()["custom"]["profiles"]:
        options = profile.get("profile_options", {}).get("image", {})
        for name, choice in options.get("choices", {}).items():
            display = choice.get("display_name", "")
            if display.startswith(JUPYTERLAB_DISPLAY_PREFIX + ":"):
                assert display == expected, (
                    f'profile {profile["slug"]!r} choice {name!r}: '
                    f"display_name {display!r} does not match the image it "
                    f"spawns ({expected}) — the selector would show one tag "
                    "and run another"
                )


def test_hub_tag_matches_singleuser_tag():
    """hub and jupyterlab images are built from the same commit and tagged
    with the same sha; a half-bump that moves the hub pair but not the
    jupyterlab refs (or vice versa) must not pass unnoticed."""
    jh = _jupyterhub_values()
    assert jh["hub"]["image"]["tag"] == jh["singleuser"]["image"]["tag"], (
        "hub.image.tag and singleuser.image.tag are bumped together from the "
        "same commit's build; a mismatch means a partial hand-bump"
    )
