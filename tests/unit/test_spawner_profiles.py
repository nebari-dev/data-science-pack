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
