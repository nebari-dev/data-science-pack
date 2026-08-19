---
title: Configuration
description: The top-level values.yaml sections and what each one controls.
---

The chart aims to need only one field for a fresh deploy —
`keycloak.hostname` — with everything else derived by subdomain convention.
Every derived value can still be overridden explicitly. Values under
`jupyterhub.*` are passed straight through to the upstream
[Zero to JupyterHub](https://z2jh.jupyter.org/) chart.

| Section | Controls |
|---|---|
| `keycloak` | External Keycloak FQDN, realm name, in-cluster service host |
| `subdomains` | Subdomain convention (`hub`, `nebi`) used to derive hostnames from `keycloak.hostname` |
| `nebariapp` | Whether/how the `NebariApp` CRD is rendered — routing, auth, landing-page card. See [NebariApp Integration](/nebariapp-integration/) |
| `singleuser` | Egress NetworkPolicy allowing user pods to reach the Nebari gateway |
| `singleuserCuller` | In-pod idle culling for kernels, terminals, and the server itself (separate from the hub-level `jupyterhub.cull`) |
| `vscodeActivity` | Interaction-based idle culling for VS Code; `enabled: false` reverts to counting raw proxied traffic as activity |
| `sharedStorage` | Per-group RWX directories and the transitional in-cluster NFS mode. See [Shared Storage](/shared-storage/) |
| `nebi` | The companion Nebi service — image, external/internal URLs, namespace, release name |
| `rbac.bootstrap` | One-shot Keycloak Job that adds the groups-claim mapper and the shared-mount client role |
| `jupyterhub` | Passed through verbatim to the `jupyterhub` subchart (proxy, hub, singleuser images, auth, etc.) |

For the full set of fields and their defaults, read
[`values.yaml`](https://github.com/nebari-dev/data-science-pack/blob/main/values.yaml)
directly — it is heavily commented and is the source of truth.

## Local development

The dummy authenticator is used by default so any username/password works
without a Keycloak dependency. To test against real OAuth, configure
`jupyterhub.hub.config` per the
[Zero to JupyterHub authentication docs](https://z2jh.jupyter.org/en/stable/administrator/authentication.html).

## VS Code idle culling

An open VS Code tab holds a websocket whose keepalives used to count as
Jupyter activity, so pods with an idle VS Code tab were never culled
([#208](https://github.com/nebari-dev/data-science-pack/issues/208)). The
pack now handles VS Code idleness like notebook idleness:

- **Real interaction counts.** A bundled extension
  (`nebari-activity-reporter`, installed automatically on every spawn)
  reports typing, scrolling, terminal use, and window focus to the Jupyter
  server. A running terminal command also counts as active — same policy
  as `cullBusy: false` for kernels — provided the shell has VS Code shell
  integration (automatic for bash/zsh; exotic shells running long jobs are
  not detected).
- **Raw traffic no longer defeats the in-pod culler.** The `/vscode/` proxy
  route runs with `update_last_activity` disabled, so keepalives from an
  idle tab no longer keep jupyter-server's own activity clock fresh. The
  hub-level `jupyterhub.cull` culler is **not** fixed by this: proxied
  websocket traffic is still visible to configurable-http-proxy at the
  route level, so the hub keeps seeing activity for as long as a tab stays
  connected, regardless of `update_last_activity`. The setting that
  actually culls an idle-tab pod is the in-pod
  `singleuserCuller.server.shutdownNoActivityTimeout` (default `900`
  seconds / 15 minutes), which jupyter-server evaluates from its own
  activity clock; set it to `0` to disable this feature.
- **Disconnected sessions exit promptly.** `CODE_SERVER_IDLE_TIMEOUT_SECONDS`
  is set to `jupyterhub.cull.timeout` (skipped when culling is disabled or
  the timeout is ≤ 60 seconds, which code-server rejects), so a code-server
  process whose last browser connection has closed exits on the culler's
  schedule instead of lingering.

To revert to the previous behavior (any open tab keeps the pod alive), set:

```yaml
vscodeActivity:
  enabled: false
```

For a hard cost cap regardless of activity — e.g. a tab left open on an
always-awake machine — the hub culler's max-age is available separately:

```yaml
jupyterhub:
  cull:
    maxAge: 86400  # kill servers after 24h no matter what
```
