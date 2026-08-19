# Interaction-based idle culling for VS Code (code-server)

**Issue:** https://github.com/nebari-dev/data-science-pack/issues/208

> **Pre-implementation corrections** (found while writing the plan; the
> behavior below is unchanged, three mechanisms moved):
>
> 1. **Registration lives in the image, not the chart ConfigMap.** The image
>    already owns `c.ServerProxy.servers` as a dict *assignment* in
>    `images/nebi/jupyter_server_config.py` (the nebi tile), and Jupyter
>    loads `/etc/jupyter` (chart CM) *before* `/usr/local/etc/jupyter`
>    (image), so a CM-side entry would be clobbered. The vscode entry is
>    added next to nebi's in the image file; the `vscodeActivity.enabled`
>    escape hatch reaches it via a spawner-set pod env var
>    (`VSCODE_PROXY_UPDATE_LAST_ACTIVITY`), the same pattern nebi uses for
>    `NEBI_REMOTE_URL`.
> 2. **No `@vscode/vsce`.** The build environment has no node/npm toolchain;
>    a `.vsix` is a zip with a static manifest, so a ~50-line stdlib Python
>    script (`images/scripts/build-vsix.py`) packages it at image build.
>    postStart still installs via `code-server --install-extension`
>    (non-fatal, `{ ... || true; }`).
> 3. **E2E is kind + HTTP, not playwright.** The existing harness has no
>    browser. Adapted e2e: proxied `/vscode/` traffic does NOT advance
>    `last_activity`; a contents-API ping DOES; the extension is installed
>    in the pod; `CODE_SERVER_IDLE_TIMEOUT_SECONDS` is set. Extension
>    *activation* under a real VS Code client is covered by the manual soak
>    only.
> 4. The code-server 4.104.3 → 4.133.0 bump needs no hash change:
>    `install.sh` is byte-identical between the two tags (verified).

## Problem

A user pod running VS Code (code-server, proxied by jupyter-server-proxy) is
never idle-culled while a browser tab stays connected, no matter how long the
user has been away. This keeps expensive pods alive indefinitely.

### Root cause (verified against source)

The issue's framing — "the heartbeat function artificially creates network
activity on JHub" — is slightly off. The code-server heartbeat is a local file
touch, not network traffic. The actual mechanism:

1. An open VS Code tab holds a persistent websocket to code-server. The VS
   Code client sends protocol keepalives over it regardless of user
   interaction.
2. That traffic flows through jupyter-server-proxy, whose handlers set
   `settings["api_last_activity"] = utcnow()` on every proxied request and
   websocket message (`jupyter_server_proxy/handlers.py`, gated by the
   `update_last_activity` option, default `True`).
3. jupyter-server's `last_activity` therefore never goes stale, so neither the
   in-pod `shutdown_no_activity_timeout` (900s) nor the hub-level
   jupyterhub-idle-culler (`cull.timeout: 1800`) ever fires.

The fix proposed in the issue (`CODE_SERVER_IDLE_TIMEOUT_SECONDS`) does not
cover the main case: code-server's idle timer only starts when the heartbeat
expires, and the heartbeat's `isActive()` check is literally
`server.getConnections() > 0` (`src/node/routes/index.ts`). An open tab —
foreground or background — keeps a connection, so the timeout never fires
while a tab is connected. It only helps once every connection is gone (tab
closed, browser quit, laptop asleep, tab discarded by the browser's memory
saver). Additionally, the feature does not exist in the code-server version
this repo pins (4.104.3): it landed in 4.106.0 via
https://github.com/coder/code-server/pull/7539, and code-server refuses to
start when the value is ≤ 60.

## Desired behavior

VS Code users get the same idle semantics as notebook users:

- Real interaction (typing, scrolling, terminal use, focus changes) keeps the
  pod alive.
- Running work keeps the pod alive: a terminal command still executing counts
  as active, mirroring the kernel culler's `cullBusy: false`. (Decision made
  during design review.)
- An open-but-idle tab does **not** keep the pod alive; the existing cullers
  fire on their normal schedule.

## Design

Two complementary mechanisms (decision made during design review: ship both).
The extension handles connected-but-idle tabs; the idle-timeout env var makes
lingering code-server processes exit after all connections drop, closing issue
208 as written.

### 1. Stop counting raw VS Code proxy traffic as activity

Remove the `jupyter-vscode-proxy` git dependency from
`images/jupyterlab/pixi.toml` and register the `vscode` server directly in
`jupyter_server_config.py` (the chart-owned singleuser ConfigMap,
`templates/singleuser-config.yaml`) via `c.ServerProxy.servers`. The entry
reproduces what `jupyter_vscode_proxy` generates — `code-server --auth none
--disable-telemetry`, port templating, `CODE_WORKINGDIR` /
`CODE_EXTENSIONSDIR` env handling, launcher entry — plus the one thing the
packaged entry point cannot express: `update_last_activity: False`.

Why removal rather than override: jupyter-server-proxy concatenates
`c.ServerProxy.servers` with entry-point-registered servers
(`jupyter_server_proxy/__init__.py::_load_jupyter_server_extension`), so a
same-named config entry would double-register route handlers. Removing the
package is the unambiguous path. The launcher icon SVG currently ships inside
that package, so it gets baked into the image instead.

With `update_last_activity: False`, an idle tab's keepalives no longer touch
`api_last_activity`, and the existing cullers govern VS Code pods exactly as
they do notebook pods:

- in-pod: kernels/terminals cull at 900s idle; the server exits 900s after the
  last kernel/terminal if no API activity (`singleuserCuller` values).
- hub: `jupyterhub.cull.timeout: 1800` culls on hub-side staleness.

### 2. Activity-reporter extension

New artifact: `nebari-activity-reporter`, a plain-JavaScript VS Code extension
in `images/vscode-activity-reporter/`. It runs in code-server's server-side
extension host inside the pod (so it sees the pod environment) and reports
*genuine* user activity to jupyter-server, which then propagates to the hub
through the normal singleuser activity-notification path.

**Events subscribed:**

- `workspace.onDidChangeTextDocument` (edits)
- `window.onDidChangeTextEditorSelection` and
  `onDidChangeTextEditorVisibleRanges` (cursor movement, scrolling)
- `window.onDidChangeWindowState` (focus changes)
- `window.onDidOpenTerminal` / `onDidCloseTerminal`
- `window.onDidStartTerminalShellExecution` /
  `onDidEndTerminalShellExecution` (stable API since VS Code 1.93)

**Busy tracking:** while any shell execution is in flight (start event seen,
no matching end), the extension counts the pod as active and keeps reporting
each interval even with no interaction. Documented limitation: this relies on
VS Code shell integration, which code-server auto-injects for bash/zsh;
long-running jobs in shells without integration are not detected.

**Reporting:** throttled to at most one request per 60s. On activity, the
extension makes one authenticated request to the local jupyter server:

- URL base from `JUPYTERHUB_SERVICE_URL` (present in every singleuser pod).
- `Authorization: token $JUPYTERHUB_API_TOKEN`.
- Endpoint must count toward activity tracking. Verified upstream: handlers
  with `_track_activity = False` (`/api/status`, `/api/`) are excluded;
  every other authenticated `APIHandler` request updates `api_last_activity`
  in `finish()` (`jupyter_server/base/handlers.py`). Use
  `GET {base}/api/contents/?content=0` (cheap, tracked).

**Robustness:** activation on `onStartupFinished`; all failures logged and
retried with backoff; the extension must never throw out of its handlers. It
logs one line on activation so a live deploy can confirm it is running.

### 3. Extension delivery

- Image build packages the extension to a `.vsix` with `@vscode/vsce` and
  bakes the file into the image (e.g. `/opt/code-server/extensions/`).
- Installation happens in a **postStart lifecycle hook** merged into the
  existing hook composition in `config/jupyterhub/01-spawner.py` (the
  nss-wrapper hook already merges into `spawner.lifecycle_hooks`; this change
  refactors that merge so multiple postStart contributors compose instead of
  overwrite).
- Why postStart instead of build-time install: the default code-server
  extensions dir lives under the user's home PVC, which mounts over anything
  installed there at build time. postStart `code-server --install-extension
  <vsix>` is idempotent, re-runs on image upgrades (vsix version bumps), and
  preserves the user's ability to install their own extensions.

### 4. Issue 208 mechanism: code-server idle timeout

- Bump `CODE_SERVER_VERSION` to 4.133.0 in
  `images/scripts/install-code-server.sh` (update the pinned `install.sh`
  sha256 and the version comment).
- In `config/jupyterhub/01-spawner.py`, set
  `CODE_SERVER_IDLE_TIMEOUT_SECONDS = str(cull.timeout)` in the KubeSpawner
  environment, sourced from z2jh's `get_config("cull.timeout")`. Skip when
  culling is disabled or the timeout is ≤ 60 (code-server rejects ≤ 60 at
  startup). No new values knob: the value derives from the idle-culler
  setting, per the issue.

### 5. Escape hatch

One new values flag:

```yaml
vscodeActivity:
  enabled: true
```

`false` renders the vscode server entry with `update_last_activity: True` —
the pre-change behavior — as field insurance if the extension misbehaves. The
extension itself always ships and runs; its pings are harmless either way.

### 6. Failure modes

- **Extension silently broken** (packaging bug, API change): active VS Code
  users are culled after `cull.timeout` (30 min default). This is the worst
  failure mode — worse than today's overspending — and is mitigated by the
  escape hatch, activation logging, and the e2e test below, which fails CI if
  typing stops advancing `last_activity`.
- **No shell integration** for a long-running terminal job: not detected as
  busy (documented limitation; bash/zsh get integration automatically).
- Token lifetime: `JUPYTERHUB_API_TOKEN` is stable for the pod's life; no
  rotation handling needed.

### 7. Testing

- **Unit (pytest, existing patterns in `tests/unit/`):**
  - `CODE_SERVER_IDLE_TIMEOUT_SECONDS` wiring: set from `cull.timeout`;
    absent when culling disabled or timeout ≤ 60.
  - singleuser ConfigMap rendering: vscode server entry present with
    `update_last_activity` False/True per `vscodeActivity.enabled`.
  - postStart hook composition: nss-wrapper and extension-install both
    present after the merge refactor.
- **E2E (playwright, existing `tests/e2e/`):** open `/vscode` through the
  hub; assert jupyter `last_activity` advances while synthesizing typing;
  assert it does **not** advance over a few minutes with the tab open and
  idle.
- **Manual soak** on a live deploy with shortened timeouts before merge.

### 8. Documentation

`docs/src/content/docs/configuration.md` (and README where relevant) gain an
idle-culling section: how VS Code idleness now behaves, the
`vscodeActivity.enabled` escape hatch, the shell-integration caveat, the
code-server idle timeout's derivation from `cull.timeout`, and
`jupyterhub.cull.maxAge` as an optional hard cost cap for always-connected
tabs.

## Decisions log

- **Ship both mechanisms** (extension + `CODE_SERVER_IDLE_TIMEOUT_SECONDS`):
  user decision, 2026-08-19.
- **Busy terminal counts as active** (mirrors `cullBusy: false`): user
  decision, 2026-08-19.
- **Rejected: `update_last_activity: False` alone** — would cull actively
  working VS Code users; dealbreaker.
- **Rejected as primary fix: `cull.maxAge`** — blunt hard cap that kills
  active users; documented as an optional backstop only.

## Verified upstream facts

- code-server idle timeout: added in
  https://github.com/coder/code-server/pull/7539 (first release 4.106.0);
  env var `CODE_SERVER_IDLE_TIMEOUT_SECONDS`; startup error when ≤ 60; timer
  runs only while heartbeat state is `expired`; heartbeat `isActive()` is
  `getConnections() > 0` (`src/node/{main,heart}.ts`,
  `src/node/routes/index.ts`).
- jupyter-server-proxy: per-server `update_last_activity` trait (default
  `True`) controls whether proxied traffic sets `api_last_activity`
  (`jupyter_server_proxy/{config,handlers}.py`); entry-point servers are
  appended to config servers, so same-name overrides double-register.
- jupyter_server: `APIHandler.finish()` updates `api_last_activity` for
  authenticated requests unless the handler sets `_track_activity = False`
  (`/api/status` and `/api/` do; contents API does not)
  (`jupyter_server/base/handlers.py`,
  `jupyter_server/services/api/handlers.py`).
