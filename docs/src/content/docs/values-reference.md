---
title: Values reference
description: Field-by-field reference for every value the chart owns, plus the jupyterhub.custom keys.
---

[`values.yaml`](https://github.com/nebari-dev/data-science-pack/blob/main/values.yaml) is
heavily commented and remains the source of truth. This page is the same content organized
for reading, with the derivation rules and the fields whose defaults are worth knowing
about.

Sections marked *derived* fall back to a value computed from `keycloak.hostname` — see
[Admin setup](/admin-setup/#one-required-field).

## `keycloak`

| Field | Default | What it does |
|---|---|---|
| `keycloak.hostname` | `""` | External Keycloak FQDN. The one field a fresh deploy needs; everything else derives from it. |
| `keycloak.realm` | `nebari` | Realm name. |
| `keycloak.serviceHost` | `keycloak-keycloakx-http.keycloak.svc.cluster.local:8080` | In-cluster Keycloak service, used for the hub↔Nebi token exchange when `hostname` is empty. |
| `keycloak.backchannelURL` | `""` | Split-horizon OIDC — see below. |

### Split-horizon OIDC

`backchannelURL` exists for private-VPC clusters where in-cluster CoreDNS cannot resolve the
external Keycloak hostname. The hub pod cannot reach `hostname` at all, so token exchange
over the primary URL fails.

Set it to the in-cluster URL and the hub uses it for the backchannel legs (`token_url`,
`userdata_url`) while the browser keeps using `hostname` for authorize and end-session. The
`/realms/<realm>` suffix is appended for you.

:::caution[Keycloak needs `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`]
Otherwise Keycloak mints tokens whose `iss` claim matches the backchannel URL, and any
validator keyed to the browser-facing issuer rejects them.
:::

## `subdomains`

| Field | Default | What it does |
|---|---|---|
| `subdomains.hub` | `hub` | Label prepended to the base domain to derive the hub hostname. |
| `subdomains.nebi` | `nebi` | Same, for Nebi. |

## `nebariapp`

Rendered into the `NebariApp` CRD. Semantics of each CRD field are in
[NebariApp Integration](/nebariapp-integration/).

| Field | Default | What it does |
|---|---|---|
| `nebariapp.enabled` | `true` | Render the `NebariApp`. False outside Nebari. |
| `nebariapp.hostname` | `""` *(derived)* | External hub FQDN. |
| `nebariapp.service.name` | `proxy-public` | Backend service (created by the z2jh subchart). |
| `nebariapp.service.port` | `80` | Backend port. |
| `nebariapp.routing.routes` | `[{pathPrefix: /}]` | Sends every path to the proxy. |
| `nebariapp.auth.enabled` | `true` | Provision a Keycloak client. |
| `nebariapp.auth.provider` | `keycloak` | Identity provider. |
| `nebariapp.auth.provisionClient` | `true` | Operator creates the client and its Secret. |
| `nebariapp.auth.redirectURI` | `/hub/oauth_callback` | JupyterHub's own callback path. |
| `nebariapp.auth.scopes` | `openid, profile, email, groups` | The `groups` scope is what shared storage and profile gating read. |
| `nebariapp.auth.enforceAtGateway` | `false` | See below. |
| `nebariapp.auth.forwardAccessToken` | `false` | The hub persists tokens to `auth_state`; nothing upstream needs an injected Bearer. |
| `nebariapp.landingPage.*` | enabled, "JupyterHub" | Landing-page card: `displayName`, `description`, `icon`/`iconLight`/`iconDark`, `category`, `priority`, `externalUrl`, `healthCheck`. |

:::note[Why `enforceAtGateway` is false]
JupyterHub is its own OAuth client (`GenericOAuthenticator` with refresh-token grants).
Running Envoy's OIDC filter in front adds nothing, and its cookie-rotation lag stales out
`auth_state` for `/services/japps/*` paths. The operator drops the `SecurityPolicy` on this
flip; the Keycloak client and Secret stay provisioned because `provisionClient` is
independent of enforcement.
:::

`routing` must stay present. Remove it and the operator reports `RoutingNotConfigured` —
no HTTPRoute, no TLS listener, no reachable hub.

## `singleuser` (chart-level)

Not to be confused with `jupyterhub.singleuser`, which is the upstream passthrough.

| Field | Default | What it does |
|---|---|---|
| `singleuser.networkPolicy.allowEgressToGateway` | `true` | Renders a NetworkPolicy letting user pods reach the Envoy Gateway pod. |
| `singleuser.networkPolicy.gatewayNamespace` | `envoy-gateway-system` | Where the gateway runs. |
| `singleuser.networkPolicy.gatewayName` | `nebari-gateway` | Gateway name, matched on `gateway.envoyproxy.io/owning-gateway-name`. |
| `singleuser.networkPolicy.gatewayPort` | `10443` | Gateway port. |

Required on Hetzner k3s and any cluster where kube-proxy DNATs the LoadBalancer VIP to the
proxy pod IP *before* the subchart's pod-level policy is evaluated — without it, user pods
cannot reach `https://<hub>/services/japps` or the Nebi host. Kubernetes unions egress rules
across policies selecting the same pod, so it is harmless where it is not needed.

## `singleuserCuller`

In-pod idle culling, separate from the hub-level `jupyterhub.cull`. This one fires even when
a browser tab is left open. Defaults match classic Nebari.

| Field | Default | What it does |
|---|---|---|
| `singleuserCuller.kernel.cullConnected` | `true` | Cull kernels despite open browser connections. |
| `singleuserCuller.kernel.cullIdleTimeout` | `900` | Seconds before an idle kernel is culled. |
| `singleuserCuller.kernel.cullInterval` | `300` | Check interval. |
| `singleuserCuller.kernel.cullBusy` | `false` | Never cull a kernel running code. |
| `singleuserCuller.terminal.cullInactiveTimeout` | `900` | Seconds before an idle terminal is culled. |
| `singleuserCuller.terminal.cullInterval` | `300` | Check interval. |
| `singleuserCuller.server.shutdownNoActivityTimeout` | `900` | Seconds after the last kernel/terminal before the server self-terminates. |

The hub-level culler is `jupyterhub.cull` (`timeout: 1800`, `every: 600`). The two work
together: the in-pod culler shuts an idle server down at 15 minutes; the hub-level culler is
the backstop for servers that stop reporting activity.

## `sharedStorage`

Full treatment in [Shared Storage](/shared-storage/).

| Field | Default | What it does |
|---|---|---|
| `sharedStorage.enabled` | `true` | Mount `/shared/<group>` in user pods. |
| `sharedStorage.storageClass` | `""` | RWX StorageClass when `nfsServer.enabled: false`. Empty uses the cluster default, which must support RWX. |
| `sharedStorage.size` | `10Gi` | Shared PVC size. |
| `sharedStorage.accessModes` | `[ReadWriteMany]` | — |
| `sharedStorage.groups` | `[]` | Allowlist; empty mounts every group from the user's token. |
| `sharedStorage.mountPathPrefix` | `/shared` | Mount prefix in user pods. |
| `sharedStorage.nfsServer.enabled` | `true` | Transitional in-cluster NFS server re-exporting an RWO PVC as RWX. |
| `sharedStorage.nfsServer.storageClass` | `""` | Backing RWO class for the NFS server. |
| `sharedStorage.nfsServer.image.*` | `quay.io/nebari/volume-nfs:0.8-repack` | Repack of an abandoned upstream image. |
| `sharedStorage.nfsServer.installClient` | `false` | DaemonSet installing `nfs-common` on nodes that lack it (k3s, minimal OS images). |
| `sharedStorage.nfsServer.nodeSelector` | `{}` | Pin the NFS pod — RWO reattachment can take 30–120s when it reschedules. |
| `sharedStorage.nfsServer.nodeAffinity` | `{}` | Full affinity spec; overrides `nodeSelector`. |
| `sharedStorage.nfsServer.mountOptions` | `[]` | Set `["nfsvers=3"]` on overlayfs nodes (kind, k3d) where the image's NFSv4 export of `/` is broken. |

:::caution[Prefer a native RWX class]
`nfsServer` is a transitional fallback tracked for removal in
[issue #29](https://github.com/nebari-dev/data-science-pack/issues/29). On NIC-managed
clusters set `sharedStorage.storageClass: longhorn` and `nfsServer.enabled: false`.
:::

## `nebi`

Admin guide: [Nebi integration](/nebi-integration/).

| Field | Default | What it does |
|---|---|---|
| `nebi.image.repository` | `quay.io/nebari/nebi` | Binary copied into user pods by an init container. |
| `nebi.image.tag` | `sha-5ca877a` | Pinned per chart release. Empty disables the init container. |
| `nebi.image.pullPolicy` | `IfNotPresent` | — |
| `nebi.remoteURL` | `""` *(derived)* | Browser-facing Nebi URL, used for the OIDC redirect. |
| `nebi.internalURL` | `""` *(derived)* | In-cluster URL for the token-exchange path. |
| `nebi.namespace` | `nebi` | Where nebi-pack runs; drives the NetworkPolicy and derived URL. |
| `nebi.releaseName` | `nebi-pack` | Drives the derived Nebi OIDC client ID. |
| `nebi.port` | `8460` | Used in the hub→Nebi egress rule. |
| `nebi.seedDefaultRegistry` | `true` | Seed `quay.io/nebari_environments` in each user's Nebi. |
| `nebi.registries` | `[]` | Admin-provisioned public OCI registries. |

## `rbac.bootstrap`

The post-install Keycloak Job — see
[Admin setup](/admin-setup/#the-keycloak-bootstrap-job).

| Field | Default | What it does |
|---|---|---|
| `rbac.bootstrap.enabled` | `true` | Run the Job. False for BYO-Keycloak or local dev. |
| `rbac.bootstrap.namespace` | `keycloak` | Namespace the Job runs in, so it can read the admin Secret without a cross-namespace copy. |
| `rbac.bootstrap.kcAdminCredentialSecret` | `keycloak-admin-credentials` | Secret holding the realm-admin password. Unset makes the Job skip cleanly. |
| `rbac.bootstrap.kcAdminCredentialSecretKey` | `admin-password` | Key within that Secret. |
| `rbac.bootstrap.realmName` | `nebari` | Realm to bootstrap. |
| `rbac.bootstrap.hubClientId` | `""` | Empty reads it at runtime from the operator-provisioned OIDC Secret. |
| `rbac.bootstrap.oidcClientSecretName` | `""` | Empty derives `<fullname>-oidc-client`. |
| `rbac.bootstrap.sharedMountRoleName` | `allow-group-directory-creation-role` | Must match the hub's `KC_SHARED_MOUNT_ROLE`. |
| `rbac.bootstrap.sharedMountGroups` | `[]` | Keycloak group paths granted the role. Each must already exist. |
| `rbac.bootstrap.hubExternalUrl` | `""` | Empty defaults to `https://{nebariapp.hostname}`. |
| `rbac.bootstrap.kcHost` | in-cluster Keycloak URL | Where the Admin REST API lives. |
| `rbac.bootstrap.image` | `python:3.12-slim` | Runs a stdlib-only script; any small Python image works. |

:::note[`hubExternalUrl` prevents an "OAuth state mismatch"]
It sets `rootUrl`, `baseUrl`, and `initiate.login.uri` on the hub client. Without them,
Keycloak-initiated SSO flows (account console, third-party launchers) jump straight to
`/hub/oauth_callback` without first hitting `/hub/oauth_login`, so JupyterHub has no
`oauthenticator-state` cookie and returns `400 OAuth state mismatch`.
:::

## `jupyterhub.custom`

Read by the Python files in `jupyterhub_config.d/` through `get_chart_config()`. Every URL
and client ID here is optional — leave it empty and the chart derives it. Explicit values
always win.

| Field | Default | What it does |
|---|---|---|
| `external-url` | `""` *(derived)* | Hub bind hostname. |
| `nebi-image` | `""` *(derived)* | `repository:tag` copied into user pods. |
| `nebi-image-pull-policy` | `IfNotPresent` | — |
| `jhub-app-proxy-version` | `v0.2.3` | Installed at app-spawn time. Must be ≥ v0.2.3 for apps to run inside a Nebi (pixi) environment; older versions only activate conda and fall back to the base env. |
| `nebi-remote-url` | `""` *(derived)* | Browser-facing Nebi URL. |
| `nebi-internal-url` | `""` *(derived)* | In-cluster Nebi URL. |
| `keycloak-token-url` | `""` *(derived)* | Token endpoint for hub↔Nebi exchange. |
| `keycloak-backchannel-issuer-url` | `""` *(derived)* | Full backchannel issuer including `/realms/<realm>`. |
| `nebi-client-id` | `""` *(derived)* | — |
| `jupyterhub-client-id` | `""` *(derived)* | — |
| `trust-bundle-enabled` | `false` | Merge an org CA into user pods — see below. |
| `trust-bundle-configmap` | `nebari-trust-bundle` | ConfigMap holding the org CA. |
| `trust-bundle-key` | `ca-certificates.crt` | Key within it. |
| `profiles` | two profiles | Server sizes — see [Server profiles](/server-profiles/). |
| `terminal-customization` | `true` | Starship prompt in JupyterLab terminals. |
| `sharing-scopes-enabled` | `true` | Grants the user and browser-token scopes used by jhub-apps and JupyterLab real-time collaboration sharing UI. This lets users list Hub user/group names for recipient picking; set false to disable user-managed server sharing. |
| `shared-storage-groups` | `[]` | Allowlist; empty = every group in the token. |
| `shared-storage-mount-prefix` | `/shared` | — |
| `storage-capacity` | `20Gi` | Per-user home PVC size (`claim-{username}`, RWO). |
| `workspace-storage-class` | `""` | Class for the per-user Nebi workspace PVC. Empty uses the cluster default. |
| `workspace-storage-capacity` | `20Gi` | Pixi environments run 2–5 GiB each — size accordingly. |
| `japps-config` | `{hub_host: hub, service_workers: 1}` | Attributes set on `c.JAppsConfig`. |

:::note[`service_workers: 1` is deliberate]
Four uvicorn workers take ~12s to bind, past the hub's hardcoded 10s
`wait_for_http_server` timeout — jhub-apps then crash-loops. One worker boots in ~3s.
Raise it only if you have many concurrent users, and watch the hub logs on the first
restart.
:::

### Enterprise CA bundle

`trust-bundle-enabled` covers clusters behind a TLS-inspecting proxy, where NIC core's
trust-manager projects the org CA into every namespace as a ConfigMap. Enabling it merges
that CA with the image's system bundle via an init container and sets
`REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `CURL_CA_BUNDLE`, and
`GIT_SSL_CAINFO` on singleuser and app pods — so pip, conda, and git work without
`--trusted-host` or `ssl_verify` flags.

The **hub-side** equivalent is always on and lives in `jupyterhub.hub.extraVolumes`,
`extraVolumeMounts`, `initContainers`, and `extraEnv` (search `values.yaml` for
`merge-ca-bundle`). It mounts the ConfigMap `optional: true`, so it is a no-op where
trust-manager is absent.

The `trust-bundle-configmap` and `trust-bundle-key` values drive both sides, but the
hub-side entries also hardcode the ConfigMap name in `jupyterhub.hub.extraVolumes`. Renaming
the bundle means editing both places.

## `jupyterhub` (upstream passthrough)

Everything else goes verbatim to the
[Zero to JupyterHub](https://z2jh.jupyter.org/) chart, version 4.4.0. Values this chart sets
that are worth knowing about:

| Field | Set to | Why |
|---|---|---|
| `jupyterhub.hub.image` | `quay.io/nebari/nebari-data-science-pack-jupyterhub` | Nebari hub image with jhub-apps pre-installed. |
| `jupyterhub.hub.config.JupyterHub.authenticator_class` | `dummy` | Local development. Real OAuth is wired by `00-gateway-auth.py` from the mounted OIDC Secret. |
| `jupyterhub.hub.config.JupyterHub.admin_access` | `true` | — |
| `jupyterhub.hub.service.extraPorts` | `10202` | jhub-apps. |
| `jupyterhub.singleuser.image` | `quay.io/nebari/nebari-data-science-pack-jupyterlab` | — |
| `jupyterhub.singleuser.defaultUrl` | `/lab` | — |
| `jupyterhub.singleuser.storage.type` | `none` | jhub-apps' `JHubSpawner` expects volumes as a list; the subchart's dynamic storage generates a dict. The home PVC is configured in `01-spawner.py` instead. |
| `jupyterhub.proxy.service.type` | `ClusterIP` | Routing is the `NebariApp`'s job. |
| `jupyterhub.scheduling.userScheduler.enabled` | `false` | — |
| `jupyterhub.cull` | `enabled: true`, `timeout: 1800`, `every: 600` | Matches classic Nebari. |

:::caution[`extraVolumes` and `extraVolumeMounts` replace on override]
Both carry required entries — `custom-config` (the `jupyterhub_config.d` ConfigMap) and
`oauth-client` (the OIDC Secret), plus the CA-bundle volumes. Overriding either list without
re-including them leaves the hub with an empty config directory and dummy auth.
:::

`jupyterhub.singleuser.extraEnv` is a **dict**, so adding a key merges rather than replaces.
That is the supported hook for injecting things like `MLFLOW_TRACKING_URI` — see
[MLflow integration](/mlflow-integration/).

### Egress from user pods

z2jh defaults `singleuser.networkPolicy.enabled: true` with
`egressAllowRules.privateIPs: false`, so **user pods cannot reach other in-cluster services
by default**. Reaching an in-cluster endpoint takes an explicit rule:

```yaml
jupyterhub:
  singleuser:
    networkPolicy:
      egress:
        - ports:
            - port: <pod port>
              protocol: TCP
          to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: <namespace>
```

Entries under `egress` are rendered verbatim into the generated policy and unioned with the
built-in rules. The port must be the **pod** port, not the Service port — NetworkPolicy is
evaluated after kube-proxy has already translated the ClusterIP.

## Inspecting

```bash
helm template data-science-pack . --set keycloak.hostname=keycloak.example.com | less
helm -n data-science get values data-science-pack
helm -n data-science get values data-science-pack --all
```

To check a derived value actually landed, read it off the running hub rather than the
values:

```bash
kubectl -n data-science get cm nebari-data-science-pack-hub-config -o yaml | head -40
```
