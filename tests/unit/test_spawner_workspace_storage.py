"""Tests for per-profile workspace-PVC storage in `01-spawner.py`.

The home PVC is managed by KubeSpawner, so a profile can steer it through
``kubespawner_override``. The workspace PVC is not: ``_ensure_workspace_pvc()``
creates it directly against the Kubernetes API from a pre-spawn hook. These
tests cover the ``workspace_storage_class`` / ``workspace_storage_capacity``
profile keys that give it the same per-profile control, and the PVC renaming
that stops two profiles from sharing one volume across different backends.
"""

from __future__ import annotations

import asyncio
import sys
import types

# 01-spawner.py imports `z2jh.get_config`; stub it so the module can be exec'd
# standalone in the host venv.
sys.modules.setdefault("z2jh", types.ModuleType("z2jh"))
_z2jh = sys.modules["z2jh"]
_z2jh.get_config = lambda key, default=None: default

from conftest import FakeConfig, load_config_module  # noqa: E402


def _load():
    c = FakeConfig()
    return load_config_module("01-spawner.py", inject_c=c), c


class _RecordingAPI:
    """Captures the PVC body instead of talking to a cluster."""

    def __init__(self):
        self.created = []

    async def create_namespaced_persistent_volume_claim(self, namespace, body):
        self.created.append(body)


class _FakeSpawner:
    def __init__(self, username, profile=None):
        self.user = types.SimpleNamespace(name=username)
        self.namespace = "dsp"
        self.user_options = {"profile": profile} if profile else {}
        self.api = _RecordingAPI()


def _create(mod, spawner):
    """Run the hook and return (pvc_name, storage_class, capacity)."""
    name = asyncio.run(mod._ensure_workspace_pvc(spawner))
    body = spawner.api.created[-1]
    return (
        name,
        body.spec.storage_class_name,
        body.spec.resources.requests["storage"],
    )


def _access_modes(mod, spawner):
    asyncio.run(mod._ensure_workspace_pvc(spawner))
    return spawner.api.created[-1].spec.access_modes


def test_profile_without_override_keeps_the_original_pvc_name():
    """Existing users must not be migrated onto a new volume by this change."""
    mod, _ = _load()
    mod._profiles = [{"slug": "small", "display_name": "Small"}]

    name, storage_class, _ = _create(mod, _FakeSpawner("alice", profile="small"))

    assert name == "nebi-workspaces-alice"
    assert storage_class is None  # falls back to the cluster default


def test_no_profile_selected_keeps_the_original_pvc_name():
    """Single-instance mode (no profile_list) is unaffected."""
    mod, _ = _load()
    mod._profiles = []

    name, _, _ = _create(mod, _FakeSpawner("alice"))

    assert name == "nebi-workspaces-alice"


def test_profile_override_sets_the_storage_class_and_suffixes_the_name():
    """A profile-specific backend needs a profile-specific volume."""
    mod, _ = _load()
    mod._profiles = [{"slug": "efs", "workspace_storage_class": "efs-sc"}]

    name, storage_class, _ = _create(mod, _FakeSpawner("alice", profile="efs"))

    assert name == "nebi-workspaces-alice-efs"
    assert storage_class == "efs-sc"


def test_two_profiles_give_one_user_two_distinct_volumes():
    """The whole point: shapes must not collide on a shared PVC."""
    mod, _ = _load()
    mod._profiles = [
        {"slug": "longhorn", "workspace_storage_class": "longhorn"},
        {"slug": "efs", "workspace_storage_class": "efs-sc"},
    ]

    first, _, _ = _create(mod, _FakeSpawner("alice", profile="longhorn"))
    second, _, _ = _create(mod, _FakeSpawner("alice", profile="efs"))

    assert first != second


def test_profile_can_override_capacity():
    mod, _ = _load()
    mod._profiles = [
        {"slug": "big", "workspace_storage_class": "efs-sc",
         "workspace_storage_capacity": "100Gi"},
    ]

    _, _, capacity = _create(mod, _FakeSpawner("alice", profile="big"))

    assert capacity == "100Gi"


def test_username_needing_escaping_still_resolves():
    """Email-shaped usernames are escaped the same way as before."""
    mod, _ = _load()
    mod._profiles = [{"slug": "efs", "workspace_storage_class": "efs-sc"}]

    name, _, _ = _create(mod, _FakeSpawner("a@b.com", profile="efs"))

    assert name.startswith("nebi-workspaces-a-40b")
    assert name.endswith("-efs")


def test_storage_keys_are_stripped_before_kubespawner_sees_the_profile():
    """KubeSpawner rejects unknown profile keys, so they must not leak."""
    mod, _ = _load()
    profiles = [{
        "slug": "efs",
        "display_name": "EFS",
        "workspace_storage_class": "efs-sc",
        "workspace_storage_capacity": "100Gi",
        "workspace_storage_access_modes": ["ReadWriteMany"],
    }]

    visible = mod._filter_profiles(profiles, groups=[], username="alice")

    assert visible == [{"slug": "efs", "display_name": "EFS"}]


def test_workspace_defaults_to_rwo():
    mod, _ = _load()
    mod._profiles = [{"slug": "small"}]

    assert _access_modes(mod, _FakeSpawner("alice", profile="small")) == [
        "ReadWriteOnce"
    ]


def test_profile_can_override_access_modes():
    """An RWX home with an RWO workspace still pins the user to one node."""
    mod, _ = _load()
    mod._profiles = [{
        "slug": "efs",
        "workspace_storage_class": "efs-sc",
        "workspace_storage_access_modes": ["ReadWriteMany"],
    }]

    assert _access_modes(mod, _FakeSpawner("alice", profile="efs")) == [
        "ReadWriteMany"
    ]
