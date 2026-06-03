# Singleuser CA bundle integration for TLS-inspected egress

**Issue:** https://github.com/nebari-dev/nebari-data-science-pack/issues/85
**Parent epic:** https://github.com/nebari-dev/nebari-infrastructure-core/issues/307
**Date:** 2026-06-03

## Goal

JupyterHub user pods (and jhub-apps app pods, which share spawner config)
must trust the enterprise CA so that `pip install`, `conda install`,
`git clone`, and arbitrary user-driven outbound HTTPS work through a
TLS-inspecting proxy with **no** `--trusted-host` / `ssl_verify: false`
workarounds.

## Background / the hook

NIC core's trust-manager `Bundle` (on `feat/trust-manager-309`) projects a
ConfigMap into **every** namespace, including JupyterHub's:

- ConfigMap name: `nebari-trust-bundle`
- Data key: `ca-certificates.crt`

This is a fixed trust-manager convention (the Bundle's `metadata.name`
becomes the ConfigMap name; the target key is set in `bundle.yaml`). The
pack consumes this well-known ConfigMap directly — **no cross-repo config
plumbing is required**. The Bundle's `namespaceSelector: {}` already covers
the JupyterHub namespace, so the NIC-side requirement in issue #85 is
already satisfied.

## Scope

In scope (this repo only):

- Mount the org CA into singleuser/app pods and set the standard CA env vars.
- A chart toggle so the pack is unchanged on clusters without trust-manager.

Out of scope:

- Any NIC-core change (the namespace selector is already correct).
- `pip.conf` / `.condarc` snippets — the env vars below already cover pip,
  conda, and git (see "Why no config-file snippets").
- Baking the CA into the singleuser image (image-build concern).

## Design

### CA strategy: merge via init container

An init container concatenates the singleuser image's **system** CA bundle
with the **org** CA into an `emptyDir`, and every CA env var in the main
container points at the merged file. This is robust in both directions:

- Proxy-inspected endpoints (re-signed by the org CA) verify against the org
  CA portion.
- Any endpoint NOT re-signed by the proxy (a genuine public root) still
  verifies against the system-bundle portion.

A single-file mount that points env vars at *only* the org CA was rejected:
it breaks verification for any non-inspected endpoint, which is fragile.

### Where it lives

Singleuser volumes/env are managed in `config/jupyterhub/01-spawner.py`
(not z2jh `singleuser.storage`/`extraVolumes`, because jhub-apps' JHubSpawner
requires `volumes` as a list while the subchart's dynamic storage emits a
dict). The new pieces are appended there at module-load time, mirroring the
existing `nebi-image` init-container block:

1. **ConfigMap volume** `org-ca` → `nebari-trust-bundle`, mounted
   `optional: true`. Optional means a missing ConfigMap (cluster without
   trust-manager) never blocks pod startup.
2. **emptyDir volume** `ca-merged`.
3. **Init container** `merge-ca-bundle` using `spawner.image` (so it sees the
   same Debian system bundle as the main container — busybox would not):

   ```sh
   cp /etc/ssl/certs/ca-certificates.crt /merged/ca-bundle.crt
   if [ -f /org-ca/ca-certificates.crt ]; then
     cat /org-ca/ca-certificates.crt >> /merged/ca-bundle.crt
   fi
   ```

   Mounts: `org-ca` → `/org-ca` (ro), `ca-merged` → `/merged`.
4. **Main-container mount** `ca-merged` → `/etc/ssl/certs-extra` (does not
   shadow the system `/etc/ssl/certs`).
5. **Env vars** pointing at `/etc/ssl/certs-extra/ca-bundle.crt`:
   `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`,
   `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`.

### The `nebi-pull` init container

When a Nebi workspace is selected, `_nebi_pre_spawn_hook` adds a `nebi-pull`
init container that runs `nebi pull` + `pixi install` — both of which make
outbound HTTPS calls (PyPI/conda, the Nebi server) that the inspecting proxy
re-signs. That egress needs the merged bundle too, so when the trust bundle is
enabled the same five CA env vars and the `ca-merged` mount are injected into
`nebi-pull`. Two ordering requirements follow, both satisfied by running
`_setup_trust_bundle` *before* `_nebi_pre_spawn_hook` in `_pre_spawn_hook`:

- `merge-ca-bundle` must be appended (and therefore execute) before
  `nebi-pull`, so the merged file already exists when `nebi-pull` reads it.
- The `ca-merged` emptyDir volume must exist before `nebi-pull` mounts it.

### Gating

New chart config `custom.trust-bundle-enabled` (default `false`), read in
`01-spawner.py` via `get_chart_config("trust-bundle-enabled", False)` —
mirroring the existing `shared-storage-enabled` pattern. Configurable
ConfigMap name/key default to the trust-manager convention but are
overridable:

- `custom.trust-bundle-configmap` (default `nebari-trust-bundle`)
- `custom.trust-bundle-key` (default `ca-certificates.crt`)

Default `false` keeps existing behavior byte-for-byte. NIC / the
nebari-operator flips it on when a trust bundle is configured for the
deployment. The ConfigMap mount stays `optional: true` even when enabled, so
a race between trust-manager projection and pod spawn degrades gracefully
(env vars point at a merged file that is just the system bundle until the
ConfigMap appears).

### Why no config-file snippets

- pip honors `REQUESTS_CA_BUNDLE`.
- conda honors `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`.
- git honors `GIT_SSL_CAINFO`.

The env vars satisfy the issue's definition of done without `pip.conf` or
`.condarc`. If manual testing surfaces a tool that ignores the env vars, add
the targeted snippet then — not preemptively.

## Testing

`tests/unit/conftest.py::load_config_module` exec's `01-spawner.py` with a
fake `c` config object and a stubbed `get_chart_config`. New unit tests:

- **enabled:** mock `z2jh.get_config("custom.trust-bundle-enabled")` → truthy,
  load the module, assert `c.KubeSpawner.volumes` contains the `org-ca`
  (optional configMap) and `ca-merged` (emptyDir) volumes, that
  `init_containers` contains `merge-ca-bundle`, that `volume_mounts` mounts
  `ca-merged`, and that all five CA env vars are present and point at the
  merged path.
- **disabled (default):** with the toggle false/unset, assert none of the CA
  volumes, init container, mounts, or env vars are added (no behavior change).

The toggle is plumbed through `custom.*`, so its chart-rendered default is
covered by the existing `test_chart_derived.py` mechanism if we add the key
to the chart-derived defaults.

## Manual verification (definition of done)

On a cluster with NIC trust-manager + an inspecting proxy:

- `pip install requests` from a clean user pod, no flags.
- `conda install` from a configured channel, no flags.
- `git clone` an HTTPS repo whose TLS terminates at the proxy, no flags.

## Files touched

- `config/jupyterhub/01-spawner.py` — trust-bundle block.
- `values.yaml` — `custom.trust-bundle-*` keys + docs.
- `templates/hub-config.yaml` — chart-derived default for the toggle (if
  needed for `test_chart_derived` coverage).
- `tests/unit/test_spawner_ca_bundle.py` (new) — enabled/disabled unit tests.
