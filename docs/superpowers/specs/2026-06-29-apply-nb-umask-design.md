# Apply `NB_UMASK` so the kernel actually uses it

**Issue:** https://github.com/nebari-dev/data-science-pack/issues/144

> **Post-review update (PR #146):** review feedback pointed out that the
> `NB_UMASK` env var was set to a hardcoded `0002` and read in exactly one
> place, with no working override path. It was removed: the wrapper now applies
> `umask 0002` directly, and the `printenv NB_UMASK` e2e assertion was dropped.
> The env-var-based snippets below are the original design; the shipped code
> hardcodes the value. The rest of the reasoning (hub-side wrapper, `/proc`
> regression test, single-UID nuance) stands.

## Problem

`NB_UMASK=0002` is set on the singleuser pod environment (by
`config/jupyterhub/01-spawner.py`), but the JupyterLab kernel and terminal
processes run with the default umask `0022`. Files created by the kernel get
mode `644` instead of the intended `664`.

### Root cause

The comments in the spawner and the e2e tests assume "z2jh's `start.sh` applies
`NB_UMASK` before exec'ing the kernel." That is **false for this image**. The
singleuser image (`images/Dockerfile`, `jupyterlab` target) is a custom
Ubuntu/pixi build, **not** jupyter docker-stacks — there is no `start.sh`.
KubeSpawner launches `jupyterhub-singleuser` directly, and in Kubernetes the
pod's `command:` overrides any Dockerfile `ENTRYPOINT`. So `NB_UMASK` is an env
var that **nothing ever reads**. The kernel and terminals inherit the default
`0022`.

### Severity nuance (corrected framing vs. the issue)

The issue states the shared-storage collaboration story is "broken." That claim
is overstated for the **current** architecture:

- Every singleuser pod runs as **UID 1000** — `01-spawner.py` hardcodes
  `uid=1000` and nothing assigns per-user UIDs or overrides z2jh's default
  `singleuser.uid: 1000`. So on the shared PVC every user-created file is owned
  by UID 1000, and every user *is* UID 1000.
- Cross-user edits therefore already work via the **owner** write bit (the `6`
  in `644`), independent of umask. The existing test
  `test_files_are_visible_and_writable_to_groupmates` proves this: Carol appends
  to Alice's file with a plain `exec` (no umask re-application) and it passes
  today — because Carol is UID 1000, same as Alice.

Where `0002` genuinely matters is files in `/shared` **not** owned by UID 1000
(root-created, conda-store / service-written, or externally-synced content):
only the group-write bit makes those editable by group members. The fix is
still correct and worth doing — it honors the documented `664/2775` contract,
is correct defense-in-depth, and would be load-bearing the moment per-user UIDs
are introduced — but it is not the "collaboration is broken" emergency the
issue describes.

## Fix

Wrap the singleuser server command in `config/jupyterhub/01-spawner.py` so
`umask` runs before the server is exec'd:

```python
c.KubeSpawner.cmd = [
    "sh",
    "-c",
    'umask "${NB_UMASK:-0002}"; exec "$0" "$@"',
    "jupyterhub-singleuser",
]
```

KubeSpawner appends its computed args after `command` in the pod spec, so the
final argv is `sh -c '<script>' jupyterhub-singleuser <args...>`: inside the
script `$0` is `jupyterhub-singleuser` and `$@` is the args, and `exec "$0" "$@"`
replaces the shell with the real server — now carrying umask `0002`. Kernels and
terminals are children of the server and inherit it.

Bound to `c.KubeSpawner.cmd` (not `c.Spawner.cmd`) so it wins on trait precedence
regardless of config load order, and to match this file's convention.

### Why this approach (vs. the image)

This is configured **hub-side** (rendered into the hub ConfigMap), so it takes
effect on the next spawn with **no singleuser image rebuild and no `values.yaml`
image-tag bump**. CI's e2e suite pulls the *published* singleuser image named in
`values.yaml` (`docker pull` in `test.yaml`), so a fix baked into the image would
leave the new regression test red until a new image is built and the tag bumped
(the repo's two-step `chore: bump image tags` flow). The hub-side wrapper makes
the test pass against the existing image.

### Rejected alternatives

- **`os.umask` in the image's `jupyter_server_config.py`:** cleaner home for the
  setting, but requires an image rebuild + tag bump before the e2e test can pass.
  Rejected for the shippability reason above.
- **`/etc/profile.d` umask:** only covers login-shell terminals, not the kernel;
  redundant once the server umask is fixed. Rejected.

## Tests

The current e2e tests re-apply `umask "$NB_UMASK"` in a fresh `kubectl exec`
shell (`_write_under_pod_umask`), so they verify *"if umask were 0002, modes
would be right"* — never *"the server runs with 0002."* A `kubectl exec` shell
is not a child of the server process, so it genuinely cannot inherit the
server's umask; that helper stays for the setgid/group-propagation tests.

Add one test that inspects the **actual** server process, which no re-applied
exec shell can fake:

```python
def test_singleuser_server_runs_with_umask_0002(spawn_user):
    """The jupyterhub-singleuser server process must actually run with umask
    0002 — this is what kernels and terminals inherit. Reads the live Umask
    from /proc/<pid>/status, which cannot be faked by a re-applied exec shell.
    Regression guard for #144."""
    u = spawn_user("alice-data")
    rc, out = u.exec(
        "sh", "-c",
        "grep -i Umask /proc/$(pgrep -f jupyterhub-singleuser | head -1)/status",
    )
    assert rc == 0, f"could not read server umask: {out}"
    assert "0002" in out, f"server umask is not 0002: {out!r}"
```

`/proc/<pid>/status` exposes the live `Umask:` field on the Linux kernels used in
these images — direct proof with no re-application.

## Comment / documentation cleanup

- `config/jupyterhub/01-spawner.py` — the `_setup_nss_wrapper` docstring says it
  "Sets ... `NB_UMASK=0002`." Note that the value is consumed by the
  `c.KubeSpawner.cmd` wrapper, not by a non-existent `start.sh`.
- `tests/e2e/test_shared_storage.py` — correct the two comments
  (`_write_under_pod_umask` docstring and `test_pod_environment_sets_nb_umask_to_0002`)
  that attribute the umask application to "z2jh's start.sh."

## Out of scope

- No singleuser image change (so no rebuild / tag bump).
- No Helm value changes; the `NB_UMASK` env var stays as-is.
- Per-user UIDs are a separate, larger discussion (noted on the issue, not
  addressed here).

## Issue follow-up

Post a comment on #144 documenting the single-UID nuance so the severity framing
is accurate for whoever reviews/closes it.
