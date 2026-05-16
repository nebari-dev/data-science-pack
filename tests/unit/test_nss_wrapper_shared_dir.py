"""Tests for the `~/shared` lifecycle in `_setup_nss_wrapper`.

Group membership changes between spawns are a normal operational scenario:
admins add/remove users from KC groups, users self-leave projects, etc.
The home PVC is persistent across spawns, so whatever shape `~/shared`
took on the LAST spawn is still there at the START of the next spawn.

Three transitions matter:

  1. user had groups (← `~/shared` is a symlink to `/shared`)
     then user has no groups (`/shared` is NOT mounted)
     → `mkdir -p /home/jovyan/shared` follows the symlink, the target
       `/shared` doesn't exist as an accessible directory, mkdir errors
       with "File exists" exit 1, postStart fails, kubelet kills the
       container, CrashLoop.

  2. user had no groups (`~/shared` is a real empty dir)
     then user has groups (chart wants a symlink)
     → `ln -sfn /shared /home/jovyan/shared` cannot replace an existing
       directory; symlink creation fails silently and the user can't see
       the shared mount.

  3. shared-storage flipped off in chart values between spawns
     → symlink still in PVC, target unmounted, same as case (1).

The fix reconciles ~/shared per branch with data-safety in mind:

  * groups + shared_storage: `rm -rf` then `ln -sfn` — safe because
    real data is on the RWX PVC at /shared/<group>, ~/shared is only
    ever a pointer.
  * groups + no shared_storage (RWO fallback): symlink-only rm, then
    `mkdir -p` for per-group dirs. The per-group dirs in the home PVC
    MAY hold user files, so preserve them.
  * no groups: symlink-only rm, no mkdir. Leaving ~/shared absent lets
    the next groups-gained spawn's `ln -sfn` work cleanly.

This module pins that contract.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import types
from pathlib import Path

# 01-spawner.py imports z2jh.get_config; stub it like the storage test does.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import FakeConfig, load_config_module  # noqa: E402


class FakeSpawner:
    """Records the bits `_setup_nss_wrapper` mutates."""

    def __init__(self):
        self.environment = {}
        self.lifecycle_hooks = None


def _load_spawner_module(shared_storage_enabled: bool):
    c = FakeConfig()
    # Drive the module-level `shared_storage_enabled` global via the
    # custom.shared-storage-enabled chart config.
    custom = {
        "custom.shared-storage-enabled": shared_storage_enabled,
        "custom.shared-storage-groups": [],
        "custom.shared-storage-mount-prefix": "/shared",
        "custom.storage-capacity": "20Gi",
    }
    z2jh = sys.modules["z2jh"]
    z2jh.get_config = lambda key, default=None: custom.get(key, default)
    return load_config_module("01-spawner.py", inject_c=c)


def _poststart_cmd(spawner: FakeSpawner) -> str:
    """Extract the joined sh -c command string from the registered hook."""
    cmd_list = spawner.lifecycle_hooks["postStart"]["exec"]["command"]
    assert cmd_list[:2] == ["/bin/sh", "-c"]
    return cmd_list[2]


def test_no_groups_removes_dangling_symlink_without_destroying_real_dir():
    """User lost group membership. Prior shared_storage=true spawn left a
    symlink at ~/shared → /shared; /shared is no longer mounted, the
    symlink dangles. The previous implementation ran `mkdir -p ~/shared`
    which follows the symlink and errors with `File exists` exit 1,
    crashing the container. Fix: symlink-only rm. Must NOT use `rm -rf`
    here, because a real directory may hold user data from a prior
    shared_storage=false spawn — preserve it for the next groups-gained
    spawn."""
    mod = _load_spawner_module(shared_storage_enabled=True)
    spawner = FakeSpawner()
    asyncio.run(mod._setup_nss_wrapper(spawner, "alice@example.test", groups=[]))

    cmd = _poststart_cmd(spawner)
    assert "[ -L /home/jovyan/shared ]" in cmd and "rm /home/jovyan/shared" in cmd, (
        f"no-groups branch must symlink-test then rm to clear a "
        f"dangling pointer; got: {cmd!r}"
    )
    assert "rm -rf /home/jovyan/shared" not in cmd, (
        f"no-groups branch must NOT `rm -rf` — would wipe a real dir "
        f"holding user data from a prior shared_storage=false spawn; "
        f"got: {cmd!r}"
    )
    assert "mkdir -p /home/jovyan/shared" not in cmd, (
        f"no-groups branch must not recreate ~/shared — leaving nothing "
        f"lets the next groups-gained spawn's `ln -sfn` create a clean "
        f"symlink with no dance; got: {cmd!r}"
    )


def test_groups_with_shared_storage_wipes_prior_state_then_symlinks():
    """User has groups + chart has shared_storage. ~/shared must be a
    symlink to /shared. Any prior state (symlink, empty placeholder, or
    stale per-group dirs from a previous shared_storage=false spawn) is
    safe to wipe — the live data lives at /shared/<group> on the RWX
    PVC, not in the home PVC. `ln -sfn` cannot replace a directory, so
    `rm -rf` first."""
    mod = _load_spawner_module(shared_storage_enabled=True)
    spawner = FakeSpawner()
    asyncio.run(mod._setup_nss_wrapper(
        spawner, "alice@example.test", groups=["data"],
    ))

    cmd = _poststart_cmd(spawner)
    assert "rm -rf /home/jovyan/shared" in cmd
    assert "ln -sfn /shared /home/jovyan/shared" in cmd
    assert cmd.index("rm -rf /home/jovyan/shared") < cmd.index(
        "ln -sfn /shared /home/jovyan/shared"
    ), f"rm must come before ln so the symlink can be created; got: {cmd!r}"


def test_groups_without_shared_storage_preserves_user_data_in_per_group_dirs():
    """RWO-only fallback: groups present, no shared PVC. The chart
    creates ~/shared/<group> as REAL DIRECTORIES inside the home PVC
    and users may have written files into them across past spawns.
    Must NOT `rm -rf` ~/shared (would wipe user data). Just clear a
    pre-existing symlink (left by a prior shared_storage=true era) so
    the subsequent mkdir doesn't follow it."""
    mod = _load_spawner_module(shared_storage_enabled=False)
    spawner = FakeSpawner()
    asyncio.run(mod._setup_nss_wrapper(
        spawner, "alice@example.test", groups=["data", "ml"],
    ))

    cmd = _poststart_cmd(spawner)
    assert "rm -rf /home/jovyan/shared" not in cmd, (
        f"RWO fallback must preserve user data in ~/shared/<group>; "
        f"got: {cmd!r}"
    )
    assert "[ -L /home/jovyan/shared ]" in cmd and "rm /home/jovyan/shared" in cmd
    assert "mkdir -p /home/jovyan/shared" in cmd
    assert "mkdir -p /home/jovyan/shared/data" in cmd
    assert "mkdir -p /home/jovyan/shared/ml" in cmd


def _run_poststart_in_sandbox(cmd: str) -> tuple[str, str]:
    """Execute the postStart cmd against a tempdir-sandboxed FS layout.

    The cmd hardcodes /tmp/passwd, /tmp/group, and /home/jovyan paths.
    Rewrite those to a tmpdir so the test doesn't touch the host. The
    rewrite is path-prefix only; the cmd's shell-quoting is preserved.

    Returns (passwd_contents, group_contents).
    """
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "home" / "jovyan").mkdir(parents=True)
        (td_path / "tmp").mkdir()
        sandbox_cmd = cmd.replace("/tmp/", f"{td}/tmp/").replace(
            "/home/jovyan", f"{td}/home/jovyan"
        ).replace(" /shared", f" {td}/shared-mount").replace(
            "/shared/", f"{td}/shared-mount/"
        )
        (td_path / "shared-mount").mkdir()
        result = subprocess.run(
            ["/bin/sh", "-c", sandbox_cmd],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, (
            f"postStart cmd failed: rc={result.returncode}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}\n"
            f"cmd={sandbox_cmd!r}"
        )
        passwd = (td_path / "tmp" / "passwd").read_text()
        group = (td_path / "tmp" / "group").read_text()
    return passwd, group


def test_poststart_writes_tmp_group_as_real_newline_separated_entries():
    """libnss_wrapper parses NSS_WRAPPER_GROUP line-by-line. The hook
    must write each entry on its own line; if all entries land on one
    line (e.g. by using `printf '%s\\n' 'a\\nb\\nc'` where the inner
    backslash-n is a LITERAL `\\n` inside a single-quoted string, not a
    newline), only the first entry parses cleanly and getgrgid()
    fails for every supplementary GID. Symptom seen in production:
    `groups: cannot find name for group ID {4,20,24,25,27,29,30,44,46,100}`
    while primary `gid=1000(jovyan)` resolved fine (first entry only).

    This test exercises the contract behaviourally: run the hook's
    shell against a sandbox, read /tmp/group, assert each generated
    entry sits on its own physical line."""
    mod = _load_spawner_module(shared_storage_enabled=True)
    spawner = FakeSpawner()
    asyncio.run(mod._setup_nss_wrapper(
        spawner, "alice@example.test", groups=["data"],
    ))
    _passwd, group = _run_poststart_in_sandbox(_poststart_cmd(spawner))

    lines = [ln for ln in group.splitlines() if ln.strip()]
    # Expected: jovyan, users, plus nogroup{4,20,24,25,27,29,30,44,46}.
    assert len(lines) >= 11, (
        f"/tmp/group must contain one entry per line; got {len(lines)} "
        f"non-empty line(s):\n{group!r}"
    )
    # Each line must be a valid 4-field group entry (name:passwd:gid:members).
    for ln in lines:
        fields = ln.split(":")
        assert len(fields) == 4, (
            f"each /tmp/group line must have 4 colon-separated fields "
            f"(name:passwd:gid:members); got {fields!r} from line {ln!r}\n"
            f"full file:\n{group!r}"
        )
        # gid must be an integer — catches the case where a literal
        # `\n` ended up embedded inside the gid field.
        assert fields[2].isdigit(), (
            f"gid field must be an integer; got {fields[2]!r} from "
            f"line {ln!r}\nfull file:\n{group!r}"
        )

    gids = {int(ln.split(":")[2]) for ln in lines}
    expected = {1000, 100, 4, 20, 24, 25, 27, 29, 30, 44, 46}
    missing = expected - gids
    assert not missing, (
        f"/tmp/group must list every GID the pod's supplementalGroups "
        f"can carry, otherwise getgrgid() fails and `groups` prints "
        f"'cannot find name for group ID X' on terminal startup. "
        f"missing: {sorted(missing)}\nfull file:\n{group!r}"
    )
