---
title: Admin setup
description: Deploying and configuring the Data Science Pack as a cluster administrator.
---

This is the administrator's entry point: what the chart needs from the cluster, what it
derives on its own, and where each knob lives. For a five-minute install, start with
[Quick Start](/quick-start/) instead.

## What the cluster must provide

| Requirement | Why | Optional? |
|---|---|---|
| [nebari-operator](https://github.com/nebari-dev/nebari-operator) | Reconciles the `NebariApp` into routing, TLS, and a Keycloak OIDC client | Yes — set `nebariapp.enabled: false` |
| Envoy Gateway | The `NebariApp`'s HTTPRoute attaches to it | With the operator |
| cert-manager | Issues the TLS certificate for the hub hostname | With the operator |
| Keycloak (`bitnami/keycloakx`) | Identity provider; the operator provisions the hub client in it | With the operator |
| A ReadWriteMany StorageClass | Per-group shared directories | Yes — see [Shared Storage](/shared-storage/) |
| A default (RWO) StorageClass | Per-user home PVCs and Nebi workspace PVCs | No |
| Namespace label `nebari.dev/managed=true` | The operator ignores `NebariApp`s in unlabeled namespaces | No, when the operator is used |

Without the operator the chart still installs — dummy authenticator, no routing, no shared
Keycloak. That is the local-development path, not a deployment mode.

## One required field

The chart is built around a single input. Everything else is derived by subdomain
convention and can be overridden individually:

```yaml
keycloak:
  hostname: keycloak.example.com
```

From that one value:

| Derived | Rule | Example |
|---|---|---|
| Base domain | `keycloak.hostname` minus its first label | `example.com` |
| Hub hostname | `<subdomains.hub>.<base>` | `hub.example.com` |
| Nebi external URL | `https://<subdomains.nebi>.<base>` | `https://nebi.example.com` |
| Keycloak token URL | `https://<keycloak.hostname>/realms/<realm>/…/token` | — |
| Hub OIDC client ID | `jupyterhub-<release>-<chart>` | `jupyterhub-data-science-pack-nebari-data-science-pack` |
| Nebi OIDC client ID | `nebi-<nebi.releaseName>-nebari-nebi-pack` | `nebi-nebi-pack-nebari-nebi-pack` |

:::caution[Derivation needs a dotted hostname]
The base domain is `keycloak.hostname` with its first label stripped. A single-label value
like `keycloak` yields an empty base domain, and the hub hostname, Nebi URL, and token URL
all come out empty — the chart renders, and nothing routes. Set `nebariapp.hostname` and
`nebi.remoteURL` explicitly in that case.
:::

## Install

```bash
helm repo add nebari https://raw.githubusercontent.com/nebari-dev/helm-repository/gh-pages/
helm repo update

kubectl create namespace data-science
kubectl label namespace data-science nebari.dev/managed=true

helm install data-science-pack nebari/nebari-data-science-pack \
  --namespace data-science \
  --set keycloak.hostname=keycloak.example.com
```

Also available as an OCI artifact:

```bash
helm install data-science-pack \
  oci://quay.io/nebari/charts/nebari-data-science-pack --version <version>
```

:::note[The release name is load-bearing]
Two things are derived from it: the hub OIDC client ID, and the Secret name the hub mounts
at `/etc/oauth`
(`{Release.Name}-{Chart.Name}-oidc-client`, hardcoded in
`jupyterhub.hub.extraVolumes`). Installing under a non-default release name means updating
that `secretName` — and the matching `secretKeyRef` under `jupyterhub.hub.extraEnv` — or the
hub silently falls back to dummy auth.
:::

## Where each knob lives

Configuration splits across three layers. Knowing which one you are in explains most
"my value did nothing" reports.

| Layer | Path | What it is |
|---|---|---|
| Chart values | `keycloak`, `subdomains`, `nebariapp`, `singleuser`, `singleuserCuller`, `sharedStorage`, `nebi`, `rbac` | This chart's own values |
| Chart-derived hub config | `jupyterhub.custom.*` | Read by the Python files in `jupyterhub_config.d/` via `get_chart_config()` |
| Upstream passthrough | everything else under `jupyterhub.*` | Handed verbatim to [Zero to JupyterHub](https://z2jh.jupyter.org/) |

Field-by-field detail for all three is in the [Values reference](/values-reference/).

:::caution[Two z2jh lists replace rather than merge]
`jupyterhub.hub.extraVolumes` and `extraVolumeMounts` are lists, so overriding either
**replaces** the chart's entries. Both carry required mounts — `custom-config`
(the `jupyterhub_config.d` ConfigMap) and `oauth-client` (the OIDC Secret). Drop them and
the hub comes up with an empty config directory and dummy auth.

Re-include the chart's entries in any override. The same applies to
`jupyterhub.hub.initContainers`, which carries the CA-bundle merge step.
:::

## What the chart creates

Beyond the z2jh subchart's own objects:

| Object | Template | Purpose |
|---|---|---|
| `NebariApp` | `nebariapp.yaml` | Routing, TLS, Keycloak client, landing-page card |
| Hub config ConfigMap | `hub-config.yaml` | The four `jupyterhub_config.d/` Python files |
| Singleuser config ConfigMap | `singleuser-config.yaml` | Per-pod config mounted by the spawner |
| Nebi config ConfigMap | `singleuser-nebi-config.yaml` | Admin-provisioned Nebi registries — only when customized |
| Shared PVC (+ NFS server) | `shared-pvc.yaml`, `nfs-server.yaml` | Per-group shared storage |
| NFS client installer | `nfs-client-installer.yaml` | DaemonSet installing `nfs-common`, opt-in |
| Keycloak RBAC bootstrap Job | `keycloak-rbac-bootstrap-job.yaml` | post-install/upgrade hook; groups mapper + shared-mount role |
| Two NetworkPolicies | `singleuser-gateway-egress.yaml`, `hub-nebi-networkpolicy.yaml` | Egress the subchart's policy does not cover |

## The Keycloak bootstrap job

`rbac.bootstrap.enabled` defaults to `true`. It runs as a post-install/post-upgrade hook in
the `keycloak` namespace, authenticates with the admin credentials Secret, and is
idempotent — it skips cleanly when `kcAdminCredentialSecret` is unset, so the chart still
installs on clusters that have not surfaced one.

It does four things:

1. Adds the `oidc-group-membership-mapper` to the `groups` client scope. Without it the
   `groups` claim is empty, and both shared storage and `access: yaml` profile gating
   silently fall back to "no groups".
2. Creates the `allow-group-directory-creation-role` client role on the hub client.
3. Enables `serviceAccountsEnabled` on the hub client and binds
   `realm-management.{view-clients,view-groups,view-realm}` to its service account.
4. Assigns the shared-mount role to the groups listed in `rbac.bootstrap.sharedMountGroups`.

Set `enabled: false` for BYO-Keycloak or local development. Override `namespace`,
`kcAdminCredentialSecret`, and `kcHost` for non-bitnami Keycloak layouts.

## Integrations

- **[Nebi](/nebi-integration/)** — the environment manager. Ships into user pods via an init
  container and needs a matching OIDC client for token exchange.
- **[MLflow](/mlflow-integration/)** — experiment tracking. Two values, one of which is a
  NetworkPolicy that has to name the pod port rather than the service port.
- **[NebariApp](/nebariapp-integration/)** — the CRD fields this chart sets and why.

## User-facing configuration

- **[Server profiles](/server-profiles/)** — sizes, images, and per-group gating.
- **[Shared Storage](/shared-storage/)** — per-group directories and RWX requirements.

## Verify a deployment

```bash
kubectl -n data-science get pods
kubectl -n data-science get nebariapp,httproute,certificate

# The operator only acts on labeled namespaces
kubectl get namespace data-science -o jsonpath='{.metadata.labels}'

# The hub reads its OAuth client from this Secret; absent means dummy auth
kubectl -n data-science get secret data-science-pack-nebari-data-science-pack-oidc-client

# Did the Keycloak bootstrap hook succeed?
kubectl -n keycloak get jobs -l app.kubernetes.io/instance=data-science-pack
```

Then log in through Keycloak and check that the profile selector appears with the sizes you
expect. An empty or unexpectedly short list usually means the `groups` claim is missing —
see [Server profiles](/server-profiles/#gating-profiles-by-group).
