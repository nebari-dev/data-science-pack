"""Tests for per-profile group gating in `01-spawner.py`.

The data science pack mirrors classic Nebari's ``access:`` semantics on each
``custom.profiles`` entry:

  * ``access: all`` (or omitted) — every authenticated user sees the profile.
  * ``access: yaml`` — only users in the listed ``groups``/``users`` see it.
  * ``access: keycloak``: only users whose ``jupyterlab-profiles`` Keycloak
    role grants the profile's ``slug`` see it (the authenticator resolves the
    role's ``profiles`` attribute into ``auth_state`` at login).

Gating is applied per user at spawn time by setting
``c.KubeSpawner.profile_list`` to an async callable. The callable resolves the
user's Keycloak groups from ``auth_state`` and returns only the admitted
profiles, with the gating-only keys (``access``/``groups``/``users``) stripped
so KubeSpawner never sees them.
"""

from __future__ import annotations

import asyncio
import sys
import types

# 01-spawner.py imports `z2jh.get_config`; stub it so the module can be exec'd
# standalone in the host venv.
sys.modules.setdefault("z2jh", types.ModuleType("z2jh"))
# Reference whichever z2jh module actually landed in sys.modules (another test
# module may have registered its own stub first via setdefault).
_z2jh = sys.modules["z2jh"]
_z2jh.get_config = lambda key, default=None: default

from conftest import FakeConfig, load_config_module  # noqa: E402


def _load():
    c = FakeConfig()
    return load_config_module("01-spawner.py", inject_c=c), c


def test_profile_without_access_is_visible_to_all():
    """A profile with no ``access`` key is shown to every user (current behavior)."""
    mod, _ = _load()

    profiles = [{"slug": "small", "display_name": "Small"}]
    visible = mod._filter_profiles(profiles, groups=[], username="alice")

    assert visible == [{"slug": "small", "display_name": "Small"}]


def test_yaml_access_visible_when_group_matches_and_keys_stripped():
    """A restricted profile is shown to a user in one of its groups, and the
    gating-only keys never reach KubeSpawner."""
    mod, _ = _load()

    profiles = [
        {
            "slug": "gpu",
            "display_name": "GPU",
            "access": "yaml",
            "groups": ["gpu-access"],
            "kubespawner_override": {"extra_resource_limits": {"nvidia.com/gpu": 1}},
        }
    ]
    visible = mod._filter_profiles(profiles, groups=["gpu-access"], username="alice")

    assert visible == [
        {
            "slug": "gpu",
            "display_name": "GPU",
            "kubespawner_override": {"extra_resource_limits": {"nvidia.com/gpu": 1}},
        }
    ]


def test_yaml_access_hidden_when_user_not_in_groups_or_users():
    """A restricted profile is hidden from a user in none of its groups/users."""
    mod, _ = _load()

    profiles = [
        {"slug": "gpu", "display_name": "GPU", "access": "yaml", "groups": ["gpu-access"]}
    ]
    visible = mod._filter_profiles(profiles, groups=["data-team"], username="alice")

    assert visible == []


def test_yaml_access_visible_when_user_in_users_list():
    """A restricted profile is shown to a named user even with no group match."""
    mod, _ = _load()

    profiles = [
        {
            "slug": "gpu",
            "display_name": "GPU",
            "access": "yaml",
            "groups": ["gpu-access"],
            "users": ["alice"],
        }
    ]
    visible = mod._filter_profiles(profiles, groups=["data-team"], username="alice")

    assert [p["slug"] for p in visible] == ["gpu"]


def test_unknown_access_value_is_hidden():
    """An unrecognized access mode fails closed (hidden), not open.

    Restricted profiles gate expensive resources; an access typo must never
    silently expose a GPU/large profile to everyone."""
    mod, _ = _load()

    profiles = [{"slug": "gpu", "display_name": "GPU", "access": "ldap"}]
    visible = mod._filter_profiles(profiles, groups=["gpu-access"], username="alice")

    assert visible == []


def test_keycloak_access_visible_when_slug_in_role_allowlist():
    """access: keycloak shows a profile only if its ``slug`` is in the
    allow-list the user's ``jupyterlab-profiles`` Keycloak role grants.
    Gating keys are stripped."""
    mod, _ = _load()

    profiles = [
        {"slug": "gpu", "display_name": "GPU", "access": "keycloak"},
    ]
    visible = mod._filter_profiles(
        profiles, groups=[], username="alice", keycloak_profile_slugs=["gpu"]
    )

    assert visible == [{"slug": "gpu", "display_name": "GPU"}]


def test_keycloak_access_hidden_when_slug_not_in_allowlist():
    mod, _ = _load()

    profiles = [{"slug": "gpu", "display_name": "GPU", "access": "keycloak"}]
    visible = mod._filter_profiles(
        profiles, groups=[], username="alice", keycloak_profile_slugs=["other"]
    )

    assert visible == []


def test_keycloak_access_matches_slug_not_display_name():
    """The allow-list is keyed on the stable ``slug``; passing the
    human-facing ``display_name`` must NOT make the profile visible."""
    mod, _ = _load()

    profiles = [{"slug": "gpu", "display_name": "GPU", "access": "keycloak"}]
    visible = mod._filter_profiles(
        profiles, groups=[], username="alice", keycloak_profile_slugs=["GPU"]
    )

    assert visible == []


def test_get_profile_groups_normalizes_and_dedups():
    """Group names are reduced to the leaf (/projects/foo -> foo) and deduped.

    Profile gating uses the user's FULL group list, not the mount-role gated
    subset — so it does not depend on shared-storage RBAC being deployed."""
    mod, _ = _load()

    auth_state = {"groups": ["/projects/foo", "gpu-access", "foo"]}
    groups = mod._get_profile_groups(auth_state)

    assert groups == ["foo", "gpu-access"]


def test_get_profile_groups_empty_without_auth_state():
    mod, _ = _load()

    assert mod._get_profile_groups(None) == []


def test_get_keycloak_profile_slugs_reads_role_allowlist_from_auth_state():
    """The keycloak-mode profile slugs come from
    ``auth_state["allowed_jupyterlab_profiles"]``, which the authenticator
    resolves from the user's ``jupyterlab-profiles`` Keycloak role."""
    mod, _ = _load()

    auth_state = {"allowed_jupyterlab_profiles": ["gpu", "high-ram"]}
    assert mod._get_keycloak_profile_slugs(auth_state) == ["gpu", "high-ram"]


def test_get_keycloak_profile_slugs_empty_without_allowlist():
    mod, _ = _load()

    assert mod._get_keycloak_profile_slugs({}) == []
    assert mod._get_keycloak_profile_slugs(None) == []


def test_render_profile_list_applies_keycloak_role_gating():
    """The async callable threads the role-granted slug allow-list
    (``auth_state["allowed_jupyterlab_profiles"]``) into keycloak gating."""
    mod, _ = _load()

    mod._profiles = [
        {"slug": "small", "display_name": "Small"},
        {"slug": "gpu", "display_name": "GPU", "access": "keycloak"},
        {"slug": "hpc", "display_name": "HPC", "access": "keycloak"},
    ]
    auth_state = {
        "groups": [],
        "allowed_jupyterlab_profiles": ["gpu"],
        "oauth_user": {"preferred_username": "alice"},
    }
    visible = asyncio.run(mod._render_profile_list(_FakeSpawner(auth_state)))

    assert [p["slug"] for p in visible] == ["small", "gpu"]


def test_profile_username_prefers_preferred_username():
    """The name matched against ``users:`` is the Keycloak preferred_username,
    matching classic Nebari (works in the jhub-apps fake-spawner path too)."""
    mod, _ = _load()

    auth_state = {"oauth_user": {"preferred_username": "alice"}}
    assert mod._profile_username(auth_state) == "alice"


class _FakeUser:
    def __init__(self, auth_state):
        self._auth_state = auth_state

    async def get_auth_state(self):
        return self._auth_state


class _FakeSpawner:
    def __init__(self, auth_state):
        self.user = _FakeUser(auth_state)


def test_render_profile_list_filters_for_the_spawning_user(monkeypatch):
    """The async callable resolves the user's groups from auth_state and
    returns only the admitted profiles (gating keys stripped)."""
    mod, _ = _load()

    profiles = [
        {"slug": "small", "display_name": "Small"},
        {"slug": "gpu", "display_name": "GPU", "access": "yaml", "groups": ["gpu-access"]},
    ]
    monkeypatch.setattr(mod, "_profiles", profiles, raising=False)

    auth_state = {
        "groups": ["/gpu-access"],
        "oauth_user": {"preferred_username": "alice"},
    }
    visible = asyncio.run(mod._render_profile_list(_FakeSpawner(auth_state)))

    assert [p["slug"] for p in visible] == ["small", "gpu"]
    assert all("access" not in p for p in visible)


def test_render_profile_list_hides_restricted_profile_from_outsider():
    mod, _ = _load()

    mod._profiles = [
        {"slug": "small", "display_name": "Small"},
        {"slug": "gpu", "display_name": "GPU", "access": "yaml", "groups": ["gpu-access"]},
    ]
    auth_state = {"groups": ["data-team"], "oauth_user": {"preferred_username": "bob"}}
    visible = asyncio.run(mod._render_profile_list(_FakeSpawner(auth_state)))

    assert [p["slug"] for p in visible] == ["small"]


GPU_IMAGE = "quay.io/nebari/nebari-data-science-pack-jupyterlab-gpu:sha-5dfee5e"


def test_gpu_profile_gets_derived_image_injected():
    """A ``gpu: true`` profile with no explicit image gets the chart-derived
    GPU image, so deployers stop hardcoding SHAs (issue #230)."""
    mod, _ = _load()

    profiles = [{"slug": "gpu", "gpu": True, "kubespawner_override": {"cpu_limit": 4}}]
    resolved = mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    override = resolved[0]["kubespawner_override"]
    assert override["image"] == GPU_IMAGE, (
        f"expected the derived GPU image to be injected, got {override!r}"
    )
    assert override["cpu_limit"] == 4, "other kubespawner_override keys must survive"


def test_gpu_profile_explicit_image_wins():
    """An explicit ``kubespawner_override.image`` is never replaced — the
    deployer opted out of derivation for that profile."""
    mod, _ = _load()

    profiles = [{"slug": "gpu", "gpu": True, "kubespawner_override": {"image": "custom:1"}}]
    resolved = mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    got = resolved[0]["kubespawner_override"]["image"]
    assert got == "custom:1", f"explicit image was overwritten with {got!r}"


def test_gpu_key_is_stripped_before_kubespawner():
    """The ``gpu`` marker is chart-only: KubeSpawner must never see it, and a
    profile with no ``kubespawner_override`` at all still gets the image."""
    mod, _ = _load()

    profiles = [
        {"slug": "gpu", "gpu": True},
        {"slug": "gpu2", "gpu": True, "kubespawner_override": {"image": "custom:1"}},
    ]
    resolved = mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    assert all("gpu" not in p for p in resolved), f"gpu key leaked: {resolved!r}"
    assert resolved[0]["kubespawner_override"]["image"] == GPU_IMAGE, (
        "a gpu profile without kubespawner_override should still get the image"
    )


def test_gpu_false_is_also_stripped():
    """``gpu: false`` is a valid way to write "not a GPU profile"; the key is
    stripped regardless of value, as the docs promise, and nothing is injected."""
    mod, _ = _load()

    profiles = [{"slug": "cpu", "gpu": False, "kubespawner_override": {"cpu_limit": 1}}]
    resolved = mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    assert resolved == [{"slug": "cpu", "kubespawner_override": {"cpu_limit": 1}}], (
        f"gpu: false must be stripped without injecting an image, got {resolved!r}"
    )


def test_non_gpu_profile_is_untouched():
    """Profiles without the ``gpu`` key pass through byte-for-byte."""
    mod, _ = _load()

    profiles = [{"slug": "small", "kubespawner_override": {"cpu_limit": 1}}]
    resolved = mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    assert resolved == profiles, f"non-gpu profile was modified: {resolved!r}"


def test_gpu_profile_without_derived_image_falls_back_to_default(caplog):
    """When the chart cannot derive a GPU image (singleuser.image.name unset
    or empty — schema-valid in z2jh), the gpu key is still stripped, no image
    is injected, and the hub warns: this silently lands the CPU image on a GPU
    node, but raising would break hub startup and therefore login."""
    mod, _ = _load()

    profiles = [{"slug": "gpu", "gpu": True, "kubespawner_override": {"cpu_limit": 4}}]
    with caplog.at_level("WARNING"):
        resolved = mod._resolve_gpu_profiles(profiles, "")

    assert "gpu" not in resolved[0], "gpu key must be stripped even without an image"
    assert "image" not in resolved[0]["kubespawner_override"], (
        "no image should be injected when the derived ref is empty"
    )
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("gpu" in w and "gpu-image" in w for w in warnings), (
        f"expected a warning naming the profile and custom.gpu-image, got {warnings!r}"
    )


def test_gpu_profile_with_image_choices_warns(caplog):
    """KubeSpawner applies ``profile_options.image.choices.*.kubespawner_override``
    AFTER the profile-level override and replaces rather than merges, so an
    image choice silently defeats the injection. The hub must say so."""
    mod, _ = _load()

    profiles = [
        {
            "slug": "gpu",
            "gpu": True,
            "profile_options": {
                "image": {
                    "display_name": "Image",
                    "choices": {
                        "default": {
                            "display_name": "cpu-lab:sha-1",
                            "default": True,
                            "kubespawner_override": {"image": "cpu-lab:sha-1"},
                        }
                    },
                }
            },
        }
    ]
    with caplog.at_level("WARNING"):
        mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("profile_options" in w and "gpu" in w for w in warnings), (
        f"expected a warning that profile_options.image overrides the GPU image, got {warnings!r}"
    )


def test_gpu_profile_without_image_choices_does_not_warn(caplog):
    """The choices warning is specific: a plain gpu profile (or one with
    non-image profile_options) stays quiet."""
    mod, _ = _load()

    profiles = [
        {"slug": "gpu", "gpu": True},
        {"slug": "gpu2", "gpu": True, "profile_options": {"size": {"choices": {}}}},
    ]
    with caplog.at_level("WARNING"):
        mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [], f"unexpected warnings: {warnings!r}"


def test_gpu_resolution_does_not_mutate_input_profiles():
    """Resolution returns new dicts; the z2jh-provided list is left intact."""
    mod, _ = _load()

    profiles = [{"slug": "gpu", "gpu": True, "kubespawner_override": {"cpu_limit": 4}}]
    mod._resolve_gpu_profiles(profiles, GPU_IMAGE)

    assert profiles == [{"slug": "gpu", "gpu": True, "kubespawner_override": {"cpu_limit": 4}}], (
        f"input profiles were mutated: {profiles!r}"
    )


def _load_with_gpu_profile():
    """Load 01-spawner.py with one ``gpu: true`` profile and a derived image."""
    z2jh = sys.modules["z2jh"]
    prior = z2jh.get_config

    def fake_get_config(key, default=None):
        if key == "custom.profiles":
            return [{"slug": "gpu", "gpu": True}]
        if key == "custom.gpu-image":
            return GPU_IMAGE
        return default

    z2jh.get_config = fake_get_config
    try:
        return _load()
    finally:
        z2jh.get_config = prior


def test_gpu_image_injected_at_load_time():
    """Module load resolves gpu profiles from custom.profiles + custom.gpu-image,
    so both the spawner and jhub-apps see the injected image."""
    mod, _ = _load_with_gpu_profile()

    assert mod._profiles == [{"slug": "gpu", "kubespawner_override": {"image": GPU_IMAGE}}], (
        f"load-time resolution did not inject the GPU image: {mod._profiles!r}"
    )


def test_load_log_names_the_injected_gpu_image(caplog):
    """``kubectl logs deploy/hub`` must be able to answer which image a GPU
    profile got — the load-time info line carries the derived ref."""
    with caplog.at_level("INFO"):
        _load_with_gpu_profile()

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(GPU_IMAGE in m and "gpu" in m for m in infos), (
        f"expected an info line naming the injected GPU image, got {infos!r}"
    )


def test_profile_list_is_the_filtering_callable_when_profiles_configured():
    """When profiles exist, KubeSpawner.profile_list is wired to the per-user
    callable, not the raw static list."""
    # Patch the live z2jh module (another test may have swapped it via
    # sys.modules["z2jh"] = ...), so 01-spawner's `from z2jh import get_config`
    # picks up the profiles below.
    z2jh = sys.modules["z2jh"]
    prior = z2jh.get_config
    z2jh.get_config = lambda key, default=None: (
        [{"slug": "small", "display_name": "Small"}]
        if key == "custom.profiles"
        else default
    )
    try:
        mod, c = _load()
        assert c.KubeSpawner.profile_list is mod._render_profile_list
    finally:
        z2jh.get_config = prior
