"""Tests for the storage wiring in `01-spawner.py`.

The chart maps every spawn (default JupyterLab AND jhub-apps named servers)
to the same per-user RWO PVC. KubeSpawner has two knobs that have to agree:

  * `pvc_name_template` — what KubeSpawner ensures (creates if missing)
  * `volumes[].persistentVolumeClaim.claimName` — what the pod mounts

Default `pvc_name_template` is `claim-{username}--{servername}` for named
servers, so without an override the chart would create a per-server PVC
while the pod tries to mount a different (per-user) one. Fresh users hit
FailedScheduling: PVC not found. Lock the template to `claim-{username}` so
ensure + mount always converge.
"""

from __future__ import annotations

import sys
import types

# 01-spawner.py imports `z2jh.get_config` for chart-driven values, which
# isn't installed in the host venv. Stub it with a default-returning shim
# so the module can be exec'd in isolation.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import FakeConfig, load_config_module  # noqa: E402


def test_pvc_name_template_matches_volume_claim():
    """KubeSpawner.pvc_name_template must match the home volume's claimName.

    Without this guarantee, the chart silently breaks for any user without a
    pre-existing per-user PVC — the PVC KubeSpawner ensures has a different
    name than the one the pod mounts.
    """
    c = FakeConfig()
    load_config_module("01-spawner.py", inject_c=c)

    assert c.KubeSpawner.slug_scheme == "escape", (
        "KubeSpawner 7 defaults to slug_scheme='safe'; keep 'escape' until "
        "PVC names and pod-affinity labels are intentionally migrated."
    )

    template = getattr(c.KubeSpawner, "pvc_name_template", None)
    assert template == "claim-{username}", (
        f"pvc_name_template={template!r} — must be 'claim-{{username}}' so "
        f"the ensured PVC matches the home volume's claimName."
    )

    # Cross-check: the home volume must reference the same PVC name.
    home_volume = next(
        (v for v in c.KubeSpawner.volumes if v.get("name") == "home"), None
    )
    assert home_volume is not None, "no 'home' volume defined on KubeSpawner.volumes"
    claim = home_volume["persistentVolumeClaim"]["claimName"]
    assert claim == "claim-{username}", (
        f"home volume claimName={claim!r} diverges from pvc_name_template; "
        f"new users will FailedScheduling on the missing PVC."
    )
