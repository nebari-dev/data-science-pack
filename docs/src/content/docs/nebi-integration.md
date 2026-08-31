---
title: Nebi integration
description: Wiring the Nebi environment manager into JupyterHub — images, OIDC clients, token exchange, and registries.
---

[Nebi](https://github.com/nebari-dev/nebi) is the environment manager users reach from
inside JupyterLab. It runs as its own service (`nebari-nebi-pack`, in its own namespace) and
this chart wires JupyterHub to it: the binary ships into every user pod, and the hub
performs a token exchange so each user's Nebi session is scoped to them.

This page is the administrator's side. For what users *do* with it once it works, see the
Nebi in JupyterLab page added by
[PR #204](https://github.com/nebari-dev/data-science-pack/pull/204).

:::note[Nebi is not NebariApp]
Similar names, unrelated things. **Nebi** is the environment manager described here.
**NebariApp** is the CRD the operator reconciles into routing and auth — see
[NebariApp Integration](/nebariapp-integration/).
:::

## What has to exist

1. **nebi-pack deployed**, in its own namespace (`nebi` by default), from
   [nebari-nebi-pack](https://github.com/nebari-dev/nebari-nebi-pack).
2. **Its own Keycloak OIDC client**, provisioned by the operator from nebi-pack's own
   `NebariApp`.
3. **Network reachability** — the hub must reach Nebi in-cluster, and user pods must reach
   it through the gateway.

This chart contributes the third and the wiring; it does not deploy Nebi itself.

## Minimum configuration

With `keycloak.hostname` set and nebi-pack installed under its default release name and
namespace, nothing else is required. The defaults derive:

| Value | Derived as |
|---|---|
| `nebi.remoteURL` | `https://<subdomains.nebi>.<base domain>` |
| `nebi.internalURL` | `http://nebi-pack-nebari-nebi-pack.<nebi.namespace>.svc.cluster.local` |
| Nebi OIDC client ID | `nebi-<nebi.releaseName>-nebari-nebi-pack` |
| Hub OIDC client ID | `jupyterhub-<release>-<chart>` |

Override any of them when your layout differs:

```yaml
nebi:
  namespace: nebi
  releaseName: nebi-pack
  remoteURL: https://nebi.example.com
  internalURL: http://nebi-pack-nebari-nebi-pack.nebi.svc.cluster.local
  port: 8460
```

:::caution[The derived client ID follows nebi-pack's *release* name]
`nebi.releaseName` is not this chart's release name — it is the Helm release nebi-pack was
installed under, because the operator names the client after it. Install nebi-pack as
anything other than `nebi-pack` and this must match, or the token exchange fails with an
invalid-client error from Keycloak.
:::

## How the binary reaches user pods

The Nebi binary is **not** baked into the JupyterLab image. An init container copies it out
of `nebi.image` into an `emptyDir` mounted at `/usr/local/bin/nebi` in the user pod.

That means the Nebi version is a deploy-time decision:

```yaml
nebi:
  image:
    repository: quay.io/nebari/nebi
    tag: "sha-5ca877a"
    pullPolicy: IfNotPresent
```

Leaving `tag` empty disables the init container entirely — no Nebi in user pods. It is
pinned per chart release (`scripts/bump_image_tags.py` only handles the JupyterLab images),
so override it to test a PR build or to roll forward between chart releases.

## The token exchange

Nebi needs a per-user credential, and the hub is the only component holding the user's
Keycloak tokens. At spawn, the hub runs a three-step exchange:

1. **Refresh** the user's access token at Keycloak, using the hub client's ID and secret.
2. **Exchange** that access token for an ID token with the *Nebi* audience — a standard
   Keycloak token-exchange grant from the hub client to the Nebi client.
3. **Exchange** the Nebi ID token at Nebi's own `/api/v1/auth/session` for a Nebi JWT.

The resulting JWT is injected into the user pod, so Nebi acts as that user rather than as a
shared service account. The same helper is reused by jhub-apps to populate its environment
dropdown.

Five inputs must all resolve or the exchange aborts:

| Input | Source |
|---|---|
| `keycloak-token-url` | derived from `keycloak.hostname` / `serviceHost` + realm |
| `nebi-client-id` | derived from `nebi.releaseName` |
| `jupyterhub-client-id` | derived from the release and chart names |
| `JUPYTERHUB_OIDC_CLIENT_SECRET` | env var from the operator-provisioned Secret |
| `nebi-internal-url` | derived from `nebi.namespace` |

The hub logs exactly which are missing:

```bash
kubectl -n data-science logs deploy/hub | grep -i "token-exchange\|nebi-envs"
```

## NetworkPolicy

Two paths, two policies.

**Hub → Nebi** is rendered automatically whenever `nebi.internalURL` and `nebi.namespace`
are both set (`hub-nebi-networkpolicy.yaml`), allowing egress to `nebi.port` in the Nebi
namespace. It is needed for the token exchange at spawn time.

**User pods → Nebi** goes through the gateway, not the Service, because users reach Nebi at
its external URL. That is what `singleuser.networkPolicy.allowEgressToGateway` (default
`true`) covers — an additional NetworkPolicy permitting egress to the Envoy Gateway pod:

```yaml
singleuser:
  networkPolicy:
    allowEgressToGateway: true
    gatewayNamespace: envoy-gateway-system
    gatewayName: nebari-gateway
    gatewayPort: 10443
```

Kubernetes unions egress rules across policies selecting the same pod, so both are additive
to the z2jh subchart's own policy.

## Workspace storage

Each user gets a dedicated RWO PVC, `nebi-workspaces-{slug}`, created by the spawner and
mounted at `/var/lib/nebi/workspaces`:

```yaml
jupyterhub:
  custom:
    workspace-storage-class: ""     # empty = cluster default
    workspace-storage-capacity: "20Gi"
```

Pixi environments run 2–5 GiB each, so 20 GiB is a handful of environments, not dozens.
Size it against how many environments you expect a user to keep.

## Admin-provisioned registries

Registries set here are rendered into a ConfigMap mounted at `/etc/nebi/config.yaml` in
every user pod, which Nebi reads at boot. They appear locked in the UI rather than
per-user editable:

```yaml
nebi:
  seedDefaultRegistry: true
  registries:
    - name: acme-registry
      url: registry.acme.com
      namespace: acme-envs
      default: true
```

Each entry follows Nebi's own `registries.entries` schema (`name`, `url`, `namespace`,
`default`).

:::caution[Public registries only]
Entries carry no credentials — this is a plain ConfigMap, and Nebi does not accept
authentication here. Private registries are out of scope for this mechanism.
:::

Set `seedDefaultRegistry: false` to remove the built-in `quay.io/nebari_environments`
registry Nebi seeds by default.

:::note[Both settings need a hub restart]
The mount wiring lives in the hub ConfigMap (`01-spawner.py`), so changes take effect only
for servers started *after* the hub pod restarts:

```bash
kubectl -n data-science rollout restart deployment/hub
```
:::

## jhub-apps and Nebi environments

jhub-apps offers a Nebi environment when deploying an app. Two things gate it:

- The chart auto-injects a Nebi card into `japps-config.additional_services` when
  `nebi.remoteURL` is set or derivable. Overriding `additional_services` replaces that
  default — re-include it if you add your own.
- `jupyterhub.custom.jhub-app-proxy-version` must be **≥ v0.2.3**. Older versions only
  activate conda environments and silently fall back to the base environment, so an app
  launched into a Nebi environment comes up missing its packages.

## Behind a TLS-inspecting proxy

The init container that pre-pulls Nebi environments makes its own outbound HTTPS calls
(`nebi pull`, then `pixi install` from PyPI and conda). When
`jupyterhub.custom.trust-bundle-enabled` is on, it receives the merged CA bundle too — the
CA merge step is ordered before it, so the bundle is ready. See
[Values reference](/values-reference/#enterprise-ca-bundle).

## Troubleshooting

| Symptom | Check |
|---|---|
| No Nebi in the pod at all | `nebi.image.tag` is empty, so the init container is not wired. |
| Token exchange aborts | Hub logs name the missing input — usually `nebi-client-id` from a non-default `nebi.releaseName`. |
| `invalid_client` from Keycloak | The derived Nebi client ID does not match the client the operator created for nebi-pack. |
| Empty environment dropdown in jhub-apps | Exchange failure, or `auth_state` missing — `kubectl logs deploy/hub \| grep nebi-envs`. |
| Registry changes not visible | Hub not restarted since the change. |
| Apps missing packages in a Nebi env | `jhub-app-proxy-version` below v0.2.3. |

```bash
# Is the binary in the pod?
kubectl -n data-science exec <user-pod> -- nebi --version

# Did the workspace PVC bind?
kubectl -n data-science get pvc -l app=nebi-workspaces

# What did the hub resolve?
kubectl -n data-science logs deploy/hub | grep -i "token-exchange"
```
