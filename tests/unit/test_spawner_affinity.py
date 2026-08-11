"""Tests for the user-pod co-location affinity in `01-spawner.py`.

The chart co-locates all of a user's pods on one node (required podAffinity)
because the home PVC is RWO. The affinity originally selected on kubespawner's
own `hub.jupyter.org/username` label, but kubespawner escapes that label value
with a different scheme (label-safe slug, e.g. `tpotts-openteams-com---c9bc22a3`)
than the one it uses to expand `{username}` in `extra_pod_config` templates
(DNS escaping, e.g. `tpotts-40openteams-2ecom`). For any username needing
escaping (emails), label != affinity value, the scheduler's self-match
bootstrap never fires, and every spawn deadlocks in Pending.

The contract pinned here: the affinity must select on a chart-owned label
that is set via `extra_labels` with the *identical* template string, so both
sides go through the same expansion and are equal for every username shape.
"""

from __future__ import annotations

import sys
import types

# 01-spawner.py imports `z2jh.get_config`; stub it so the module exec's standalone.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import FakeConfig, load_config_module  # noqa: E402


def _affinity_terms(c):
    pod_config = getattr(c.KubeSpawner, "extra_pod_config", None)
    assert pod_config, "KubeSpawner.extra_pod_config is not set"
    affinity = pod_config.get("affinity", {})
    terms = affinity.get("podAffinity", {}).get(
        "requiredDuringSchedulingIgnoredDuringExecution", []
    )
    assert terms, "required podAffinity is missing — RWO co-location is not enforced"
    return terms


def test_affinity_selects_on_chart_owned_label_not_kubespawner_username():
    """The affinity must not key on hub.jupyter.org/username.

    kubespawner renders that label with a different escaping than the
    {username} template, so self-match can never satisfy the required
    affinity for escaped usernames and spawns hang in Pending.
    """
    c = FakeConfig()
    load_config_module("01-spawner.py", inject_c=c)

    for term in _affinity_terms(c):
        for expr in term["labelSelector"].get("matchExpressions", []):
            assert expr["key"] != "hub.jupyter.org/username", (
                "podAffinity selects on kubespawner's username label, whose "
                "escaping differs from {username} template expansion"
            )


def test_extra_labels_preserve_z2jh_singleuser_extra_labels():
    """extra_labels must merge with singleuser.extraLabels, not replace them.

    z2jh's default ``singleuser.extraLabels`` carries
    ``hub.jupyter.org/network-access-hub: "true"``, which the hub
    NetworkPolicy requires for singleuser -> hub API traffic (port 8081).
    Assigning ``c.KubeSpawner.extra_labels`` wholesale drops that label; the
    singleuser server then can't complete its startup handshake with the hub
    (``check_hub_version`` retries and blocks), never binds its port, and
    every spawn dies on the hub's ``http_timeout``.
    """
    netpol_labels = {"hub.jupyter.org/network-access-hub": "true"}
    orig = sys.modules["z2jh"].get_config
    sys.modules["z2jh"].get_config = lambda key, default=None: (
        dict(netpol_labels) if key == "singleuser.extraLabels" else default
    )
    try:
        c = FakeConfig()
        load_config_module("01-spawner.py", inject_c=c)
    finally:
        sys.modules["z2jh"].get_config = orig

    labels = getattr(c.KubeSpawner, "extra_labels", None)
    assert labels, "KubeSpawner.extra_labels is not set"
    assert labels.get("hub.jupyter.org/network-access-hub") == "true", (
        "extra_labels dropped z2jh's singleuser.extraLabels — the hub "
        "NetworkPolicy only admits pods labeled "
        "hub.jupyter.org/network-access-hub=true, so spawned servers can't "
        "reach the hub API and never come up"
    )
    assert "nebari.dev/colocate-user" in labels


def test_affinity_label_is_applied_with_identical_template():
    """extra_labels must define the exact key/value the affinity selects on.

    Both extra_labels and extra_pod_config templates expand through the same
    kubespawner code path, so using the identical template string guarantees
    label == affinity value for every username, which keeps the scheduler's
    self-match bootstrap working for the user's first pod.
    """
    c = FakeConfig()
    load_config_module("01-spawner.py", inject_c=c)

    extra_labels = getattr(c.KubeSpawner, "extra_labels", None)
    assert extra_labels, "KubeSpawner.extra_labels is not set"

    for term in _affinity_terms(c):
        exprs = term["labelSelector"].get("matchExpressions", [])
        assert exprs, "affinity term has no matchExpressions"
        for expr in exprs:
            key, values = expr["key"], expr["values"]
            assert key in extra_labels, (
                f"affinity selects on {key!r} but extra_labels does not set it"
            )
            assert values == [extra_labels[key]], (
                f"affinity values {values!r} != extra_labels template "
                f"{extra_labels[key]!r} — the two sides must use the identical "
                "template string"
            )
