"""Tests for the user-pod co-location affinity in `01-spawner.py`.

The chart co-locates all of a user's pods on one node (required podAffinity)
because the home PVC is RWO. The scheduler lets a pod satisfy its own
required affinity only when the required value equals the pod's own label,
so the invariant that keeps spawns schedulable is: the affinity value must
equal the pod's rendered `hub.jupyter.org/username` label, byte for byte.

kubespawner renders that label and `{username}` template expansion through
different code paths (the label ignores `slug_scheme` and applies label
validation; the template follows `slug_scheme` with a different truncation
budget), so any fix that writes a template into the affinity is trusting two
renderings to agree. These tests exercise the real kubespawner rendering so
a kubespawner upgrade that breaks the invariant fails here, in the version
bump PR, instead of deadlocking spawns in production.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest import mock

import pytest

# 01-spawner.py imports `z2jh.get_config`; stub it so the module exec's standalone.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import FakeConfig, load_config_module  # noqa: E402

USERNAME_LABEL = "hub.jupyter.org/username"

# Long enough that the stripped name exceeds every truncation budget in play
# (the shape that diverged under kubespawner 7's default scheme), and short
# enough to fit them all (the shape that only diverges under scheme mismatch).
LONG_EMAIL = "alexandrina.woolstonecraft@example-corporation.com"
SHORT_EMAIL = "alice@example.com"


def _load_hook():
    c = FakeConfig()
    load_config_module("01-spawner.py", inject_c=c)
    hook = c.KubeSpawner.__dict__.get("modify_pod_hook")
    assert hook is not None, "01-spawner.py must set KubeSpawner.modify_pod_hook"
    return hook


def _rendered_pod(username, slug_scheme, node_affinity_preferred=None):
    """Build a real pod manifest through kubespawner and apply the hook.

    The Kubernetes API client is stubbed out: constructing KubeSpawner
    otherwise loads whatever kubeconfig the host happens to have, and
    manifest rendering never talks to a cluster anyway.
    """
    hook = _load_hook()

    async def go():
        with (
            mock.patch("kubespawner.spawner.load_config"),
            mock.patch("kubespawner.spawner.shared_client"),
        ):
            from kubespawner import KubeSpawner

            s = KubeSpawner(_mock=True)
            s.user.name = username
            s.slug_scheme = slug_scheme
            if node_affinity_preferred is not None:
                s.node_affinity_preferred = node_affinity_preferred
            pod = await s.get_pod_manifest()
            return hook(s, pod)

    return asyncio.run(go())


def _single_required_term(pod):
    affinity = pod.spec.affinity
    assert affinity is not None, "pod has no affinity — co-location is not enforced"
    pod_affinity = affinity.pod_affinity
    assert pod_affinity is not None, "pod has no podAffinity — co-location is not enforced"
    terms = pod_affinity.required_during_scheduling_ignored_during_execution
    assert terms and len(terms) == 1, (
        f"expected exactly one required podAffinity term, got {terms!r}"
    )
    return terms[0]


@pytest.mark.parametrize("slug_scheme", ["safe", "escape"])
@pytest.mark.parametrize(
    "username",
    [LONG_EMAIL, SHORT_EMAIL],
    ids=["long-email", "short-email"],
)
def test_affinity_value_equals_rendered_username_label(username, slug_scheme):
    """The invariant that keeps spawns schedulable, pinned on rendered output.

    With the current hook the affinity value is copied from the label, so the
    equality holds by construction; what this test guards is the rest of the
    chain: the username label still exists on rendered pods (if a kubespawner
    upgrade dropped or renamed it, the hook would fail open and co-location
    would silently disappear), the co-location affinity is enforced at all,
    and any future reimplementation (e.g. templating the value again) still
    produces a value equal to the pod's own label.
    """
    pod = _rendered_pod(username, slug_scheme)
    label = pod.metadata.labels.get(USERNAME_LABEL)
    assert label, f"pod for {username!r} has no {USERNAME_LABEL} label"

    term = _single_required_term(pod)
    (expr,) = term["labelSelector"]["matchExpressions"]
    assert expr["key"] == USERNAME_LABEL
    assert expr["values"] == [label], (
        f"affinity value {expr['values']!r} != pod label {label!r} — the "
        "required affinity is unsatisfiable and every spawn will hang Pending"
    )
    assert term["topologyKey"] == "kubernetes.io/hostname"


def test_hook_preserves_node_affinity():
    """The hook must add podAffinity without discarding node affinity.

    z2jh's matchNodePurpose preference travels in node affinity on the same
    rendered pod; replacing the whole affinity object would silently drop it.
    """
    preference = {
        "preference": {
            "matchExpressions": [
                {
                    "key": "hub.jupyter.org/node-purpose",
                    "operator": "In",
                    "values": ["user"],
                }
            ]
        },
        "weight": 100,
    }
    pod = _rendered_pod(SHORT_EMAIL, "safe", node_affinity_preferred=[preference])

    node_affinity = pod.spec.affinity.node_affinity
    assert node_affinity is not None, (
        "node affinity was discarded when the co-location podAffinity was added"
    )
    preferred = node_affinity.preferred_during_scheduling_ignored_during_execution
    assert preferred is not None and len(preferred) == 1
    # kubespawner may render the term as a dict or an API model object;
    # compare content, not type.
    assert "hub.jupyter.org/node-purpose" in str(preferred[0])
    assert pod.spec.affinity.pod_affinity is not None
