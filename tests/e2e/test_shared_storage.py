"""End-to-end behavior of /shared/<group> directories.

Per-group RWX shared dirs at /shared/<group> are set up by the chart with:

  - ownership root:users (uid=0, gid=100), mode 2775 (setgid on 'users')
  - pod runs with fsGroup=100 + umask 0002 so new files are 664/775,
    group-writable, and inherit gid=100 by setgid propagation
  - only the groups a user belongs to are mounted (no cross-group leakage)
  - shared dir is RWX across all members of a group (multi-pod, multi-user)

Test usernames encode group membership via the test DummyAuthenticator
(see tests/e2e/fixtures/test-values.yaml):

    "alice-data"     -> User('alice', groups=['data'])
    "alice-data-ml"  -> User('alice', groups=['data','ml'])
    "bob-ml"         -> User('bob',   groups=['ml'])
"""

import pytest

# Constants used in assertions across the suite. They read out as English
# next to `==` so a failing assertion explains itself.
USERS_GID = 100               # Linux 'users' group; nebari's fsGroup
ROOT_UID = 0                  # init container chown's dir to root for setgid
SHARED_DIR_MODE = 0o2775      # rwxrwsr-x — setgid + group-writable
SETGID_BIT = 0o2000           # inherited by subdirs from a setgid parent


# --- Helpers ---------------------------------------------------------------


def _write_under_pod_umask(user, shell_cmd):
    """Run `shell_cmd` with the same umask (0002) the singleuser server uses.

    A `kubectl exec` shell is not a child of the jupyterhub-singleuser
    server process, so it does NOT inherit the server's umask — tests re-apply
    it explicitly here so writes are group-writable. This means the resulting
    file *mode* is a property of umask itself, not of the chart, and is
    therefore not asserted below; that the *server* (and thus kernels/terminals)
    actually runs with umask 0002 is verified separately by
    test_singleuser_server_runs_with_umask_0002. What these tests do verify is
    the chart's directory setup — gid=100 ownership and setgid propagation.
    """
    rc, out = user.exec("bash", "-c", f'umask 0002; {shell_cmd}')
    assert rc == 0, f"setup command failed (rc={rc}): {out}"


# --- Directory attributes (chart-rendered, before any user write) ----------


@pytest.mark.parametrize("group", ["data", "ml"])
def test_group_dir_is_root_users_with_setgid_2775(spawn_user, group):
    """Per-group dir is owned root:users, mode 2775. Setgid forces gid=100
    on every new file regardless of the creator's primary gid — this is
    what makes shared collaboration work across users."""
    u = spawn_user(f"alice-{group}")
    s = u.stat(f"/shared/{group}")
    assert s.uid == ROOT_UID
    assert s.gid == USERS_GID
    assert s.mode == SHARED_DIR_MODE


# --- Pod identity (groups + umask the chart configured) --------------------


def test_pod_is_member_of_users_group(spawn_user):
    """fsGroup=100 — pod's effective gids include 100, which is what
    grants it write access to the group-writable shared dirs."""
    u = spawn_user("alice-data")
    rc, out = u.exec("id", "-G")
    assert rc == 0
    assert str(USERS_GID) in out.split()


def test_singleuser_server_runs_with_umask_0002(spawn_user):
    """The jupyterhub-singleuser server process must actually run with umask
    0002 — this is what kernels and terminals inherit. We read the live Umask
    from /proc/<pid>/status, which cannot be faked by a re-applied exec shell
    (the exec shell is not a child of the server). Regression guard for #144:
    the umask was declared but never actually applied to the server, so the
    kernel ran the default 0022."""
    u = spawn_user("alice-data")
    rc, out = u.exec(
        "sh", "-c",
        "grep -i Umask /proc/$(pgrep -f jupyterhub-singleuser | head -1)/status",
    )
    assert rc == 0, f"could not read server umask: {out}"
    assert "0002" in out, f"server umask is not 0002: {out!r}"


# --- File/dir creation inherits group via setgid ---------------------------


def test_new_file_inherits_users_group(spawn_user):
    """A file created in /shared/<group> inherits gid 100 from the parent's
    setgid bit — the core multi-tenancy invariant: files land in the shared
    'users' group regardless of the creator's primary gid, so any teammate can
    access them. (The file *mode* follows from umask 0002, a property of umask
    itself rather than the chart, so it is not asserted here — see
    _write_under_pod_umask.)"""
    u = spawn_user("alice-data")
    _write_under_pod_umask(u, "touch /shared/data/file_from_alice")

    s = u.stat("/shared/data/file_from_alice")
    assert s.gid == USERS_GID


def test_new_subdir_inherits_setgid_and_users_group(spawn_user):
    """A subdir created under a setgid parent inherits the setgid bit and
    gid=100. Without this, nested files would silently fall back to the
    user's primary gid and become invisible to teammates. (The lower mode
    bits follow from umask 0002 and are not asserted — see
    _write_under_pod_umask.)"""
    u = spawn_user("alice-data")
    _write_under_pod_umask(u, "mkdir /shared/data/subdir_from_alice")

    s = u.stat("/shared/data/subdir_from_alice")
    assert s.gid == USERS_GID
    assert s.mode & SETGID_BIT, f"setgid bit not inherited: {oct(s.mode)}"


# --- Multi-tenancy across users and groups ---------------------------------


def test_user_in_multiple_groups_sees_each_groups_dir(spawn_user):
    """A user who belongs to N groups gets N per-group dirs mounted, each
    of them writable. Group membership composes — there is no max."""
    u = spawn_user("alice-data-ml")

    for group in ("data", "ml"):
        path = f"/shared/{group}"
        assert u.path_exists(path), f"{path} should be mounted for alice"
        rc, out = u.exec("touch", f"{path}/probe-{group}")
        assert rc == 0, f"write to {path} failed: {out}"


def test_user_does_not_see_groups_they_dont_belong_to(spawn_user):
    """Group isolation is enforced at mount time: a user not in group X
    does not get /shared/X mounted at all (as opposed to mounted-but-
    unreadable). Cleaner failure mode and one less attack surface."""
    u = spawn_user("bob-ml")
    assert u.path_exists("/shared/ml")
    assert not u.path_exists("/shared/data")


def test_files_are_visible_and_writable_to_groupmates(spawn_user):
    """alice and carol both belong to 'data'. alice writes a file from
    her pod; carol reads + appends to it from hers. Same RWX PVC, same
    subPath, same setgid'd gid=100 — the actual collaboration story."""
    alice = spawn_user("alice-data")
    _write_under_pod_umask(
        alice, "echo hello-from-alice > /shared/data/handoff.txt"
    )

    carol = spawn_user("carol-data")
    rc, out = carol.exec("cat", "/shared/data/handoff.txt")
    assert rc == 0
    assert out == "hello-from-alice"

    rc, out = carol.exec(
        "bash", "-c", "echo carol-was-here >> /shared/data/handoff.txt"
    )
    assert rc == 0, f"carol could not append to alice's file: {out}"
