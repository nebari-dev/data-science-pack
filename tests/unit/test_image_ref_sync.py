"""Structural tests that every hand-editable image reference in values.yaml
agrees with ``jupyterhub.singleuser.image``.

e2e derives its cache key and kind side-load from ``singleuser.image``, but
the pod that actually spawns comes from the *default profile*: the spawn
POST has no body, so kubespawner falls through to the ``default: true``
profile, whose ``profile_options`` default choice overwrites the image.
A bump that moves ``singleuser.image.tag`` but misses a profile ref would
therefore have e2e report the new tag while the pod pulls the old image —
green CI on stale code. ``scripts/bump_image_tags.py`` keeps these in sync
on the automated path; these asserts catch the hand-edit path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES_YAML = REPO_ROOT / "values.yaml"


def _jupyterhub_values():
    with VALUES_YAML.open() as f:
        return yaml.safe_load(f)["jupyterhub"]


def _singleuser_ref(jh):
    image = jh["singleuser"]["image"]
    return f'{image["name"]}:{image["tag"]}'


def test_profile_images_match_singleuser():
    """Every profile's outer kubespawner_override.image AND its
    profile_options default-choice image must equal singleuser.image —
    the default choice is what the spawned pod actually runs."""
    jh = _jupyterhub_values()
    ref = _singleuser_ref(jh)
    profiles = jh["custom"]["profiles"]
    assert profiles, "no profiles found under jupyterhub.custom.profiles"
    for profile in profiles:
        slug = profile["slug"]
        assert profile["kubespawner_override"]["image"] == ref, (
            f"profile {slug!r}: kubespawner_override.image does not match "
            f"singleuser.image ({ref}) — jhub-apps' Create App shows this "
            "value; a half-bump here spawns a stale image"
        )
        choices = profile["profile_options"]["image"]["choices"]
        for name, choice in choices.items():
            assert choice["kubespawner_override"]["image"] == ref, (
                f"profile {slug!r} choice {name!r}: image does not match "
                f"singleuser.image ({ref}) — this choice overwrites the pod "
                "image at spawn, so e2e would report the new tag while the "
                "pod pulls the old one"
            )


def test_profile_choice_display_names_match_image():
    """The default choice's display_name embeds the tag the profile selector
    shows; it must name the image the choice actually spawns."""
    jh = _jupyterhub_values()
    image = jh["singleuser"]["image"]
    expected = f'{image["name"].rsplit("/", 1)[-1]}:{image["tag"]}'
    for profile in jh["custom"]["profiles"]:
        choices = profile["profile_options"]["image"]["choices"]
        for name, choice in choices.items():
            assert choice["display_name"] == expected, (
                f'profile {profile["slug"]!r} choice {name!r}: display_name '
                f'{choice["display_name"]!r} does not match the image it '
                f"spawns ({expected}) — the selector would show one tag and "
                "run another"
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
