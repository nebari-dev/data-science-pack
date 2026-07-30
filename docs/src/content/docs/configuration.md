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
