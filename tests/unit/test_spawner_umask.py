"""Tests for the umask application in `01-spawner.py`.

umask 0002 is applied to the singleuser server so files in /shared/<group>
are group-writable (664/2775). This image is NOT jupyter docker-stacks, so
there is no start.sh to apply the umask, and the k8s `command:` overrides any
Dockerfile ENTRYPOINT. The chart instead wraps the server command so `umask`
runs before the server is exec'd; kernels and terminals inherit it.

This unit test pins the config-level contract. The runtime effect (the server
process actually running with umask 0002) is verified by the e2e test
`tests/e2e/test_shared_storage.py::test_singleuser_server_runs_with_umask_0002`.

Regression guard for https://github.com/nebari-dev/data-science-pack/issues/144
"""

from __future__ import annotations

import sys
import types

# 01-spawner.py imports `z2jh.get_config`; stub it so the module exec's standalone.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import FakeConfig, load_config_module  # noqa: E402


def test_singleuser_cmd_wraps_with_umask():
    """KubeSpawner.cmd must wrap the server so umask is applied before exec.

    Bound to c.KubeSpawner.cmd (not c.Spawner.cmd) so it wins on trait
    precedence over z2jh's value-derived c.Spawner.cmd regardless of config
    load order.
    """
    c = FakeConfig()
    load_config_module("01-spawner.py", inject_c=c)

    cmd = getattr(c.KubeSpawner, "cmd", None)
    assert cmd is not None, "KubeSpawner.cmd is not set — umask is never applied"

    # Only pin the essential contract: the command applies umask 0002 before
    # launching the server. The exact wrapper shape (shell, exec, arg layout) is
    # an implementation detail left to review/testing, not asserted here.
    joined = " ".join(cmd)
    assert "umask 0002" in joined, f"cmd wrapper does not apply umask 0002: {cmd!r}"
