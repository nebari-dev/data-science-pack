---
title: Server profiles
description: Sizing JupyterLab servers, offering image choices, and gating profiles by group.
---

Profiles are the server sizes users pick from at spawn. Each entry in
`jupyterhub.custom.profiles` maps directly to a
[KubeSpawner](https://jupyterhub-kubespawner.readthedocs.io/) `profile_list` item, so
anything KubeSpawner accepts works without a chart change.

The chart ships two:

| Profile | Slug | Resources |
|---|---|---|
| Small Instance *(default)* | `small-instance` | 1 CPU / 2 GB limit, 0.5 CPU / 1 GB guarantee |
| Medium Instance | `medium-instance` | 4 CPU / 8 GB limit, 2 CPU / 4 GB guarantee |

Set `profiles: []` to remove the selector entirely and run in single-instance mode.

## Adding a profile

```yaml
jupyterhub:
  custom:
    profiles:
      - slug: large-instance
        display_name: "Large Instance"
        description: "16 CPU / 64 GB RAM — large in-memory datasets."
        kubespawner_override:
          image: quay.io/nebari/nebari-data-science-pack-jupyterlab:sha-16c1922
          cpu_limit: 16
          cpu_guarantee: 8
          mem_limit: "64G"
          mem_guarantee: "32G"
```

`slug` is a stable identifier independent of the human-facing `display_name`; omit it and
KubeSpawner slugifies the display name (`"Large Instance"` → `large-instance`). Set it
explicitly — the slug is what `access: keycloak` gating matches on, and renaming a display
name would otherwise silently change it.

`default: true` marks the pre-selected profile. Exactly one should have it.

`cpu_guarantee` and `mem_guarantee` become the pod's *requests*; `cpu_limit` and `mem_limit`
become its *limits*. Guarantees drive scheduling, so a guarantee larger than any node can
satisfy leaves the server `Pending` forever with no message in the UI.

`kubespawner_override` accepts any KubeSpawner trait — `node_selector`, `image`,
`extra_resource_limits`, `tolerations`, `environment`, and the rest.

## Image choices within a profile

`profile_options` adds a second dropdown under the selected profile:

```yaml
      - slug: small-instance
        display_name: "Small Instance"
        default: true
        kubespawner_override:
          image: quay.io/nebari/nebari-data-science-pack-jupyterlab:sha-16c1922
          cpu_limit: 1
          mem_limit: "2G"
        profile_options:
          image:
            display_name: Image
            choices:
              default:
                display_name: "nebari-data-science-pack-jupyterlab:sha-16c1922"
                default: true
                kubespawner_override:
                  image: quay.io/nebari/nebari-data-science-pack-jupyterlab:sha-16c1922
              rlang:
                display_name: "R"
                kubespawner_override:
                  image: quay.io/nebari/nebari-data-science-pack-jupyterlab-r:sha-16c1922
```

:::caution[The image tag appears in three places per profile]
The outer `kubespawner_override.image` is what jhub-apps' Create App form reads for its
image field; the inner `profile_options.image.choices.default` is what the JupyterLab
profile selector shows. Both must be bumped alongside `jupyterhub.singleuser.image.tag`.

z2jh values cannot reference other values, so the duplication is unavoidable.
`scripts/bump_image_tags.py` syncs all three on an automated bump, and
`tests/unit/test_image_ref_sync.py` fails CI if a hand edit lets any jupyterlab-tagged ref
in this repo's `values.yaml` drift from `singleuser.image`. Choices pointing at other
images (like the R image above) are left alone by both.
:::

## Gating profiles by group

Each profile can declare an `access` mode controlling who sees it. This is parity with
classic Nebari.

| `access` | Visible to |
|---|---|
| `all` *(or omitted)* | everyone |
| `yaml` | users whose Keycloak groups intersect `groups`, or whose `preferred_username` is in `users` |
| `keycloak` | users whose `jupyterlab-profiles` Keycloak role lists this profile's `slug` |

```yaml
      - slug: gpu-instance
        display_name: "G4 GPU Instance"
        access: yaml
        groups:
          - gpu-access
        users:
          - alice
        kubespawner_override:
          extra_resource_limits:
            nvidia.com/gpu: 1
```

:::note[Unknown access modes fail closed]
Anything other than `all`, `yaml`, or `keycloak` hides the profile and logs a warning.
Restricted profiles gate expensive resources, so a typo must not expose a GPU instance to
the whole cluster.
:::

The `access`, `groups`, and `users` keys are gating-only — they are stripped before the
profile reaches KubeSpawner.

### `access: keycloak`

Moves the allow-list out of the values file and into Keycloak, which is what you want when
the people granting access are not the people editing Helm values.

Create a `jupyterlab-profiles` client role on the hub client with:

- attribute `profiles` — the allowed slugs
- attribute `component=jupyterhub-profiles`

then assign the role to users or groups. The authenticator resolves it at login through the
Keycloak Admin API and stamps the result into `auth_state`, where the spawner reads it.

Note this requires the hub client's service account to hold the `realm-management` view
roles — which is exactly what the [Keycloak bootstrap Job](/admin-setup/#the-keycloak-bootstrap-job)
provisions.

### Groups come from the token

Both `yaml` gating and shared storage read the user's Keycloak groups from the `groups`
claim. If that claim is empty, `access: yaml` profiles are invisible to everyone and shared
directories do not mount.

The usual cause is a missing `oidc-group-membership-mapper` on the `groups` client scope —
which is the first thing the bootstrap Job fixes. Check with:

```bash
kubectl -n data-science logs deploy/hub | grep -i "profiles:\|groups"
```

## GPU profiles

A GPU profile requests the resource through `extra_resource_limits`, but scheduling onto a
tainted GPU node group also needs a toleration — whether you must add it yourself depends on
whether the cluster runs the `ExtendedResourceToleration` admission controller (EKS and GKE
do; AKS and most self-managed clusters do not).

That, plus the fact that `tolerations` in `kubespawner_override` *replaces* rather than
appends, is covered in detail on the GPU profiles page added by
[PR #139](https://github.com/nebari-dev/data-science-pack/pull/139).

## Idle culling

Two independent cullers, both on by default.

| | Value | Default | Scope |
|---|---|---|---|
| In-pod | `singleuserCuller.*` | 15 min | Kernels, terminals, and the server itself — fires even with a browser tab open |
| Hub-level | `jupyterhub.cull` | 30 min | Servers the hub sees as inactive |

The in-pod culler is the one that actually reclaims resources from users who leave a tab
open overnight; the hub-level culler is the backstop. Raising one without the other rarely
does what you want — see [Values reference](/values-reference/#singleuserculler).

## Storage per user

Each user gets two PVCs, both `ReadWriteOnce`:

| PVC | Size value | Default | Contents |
|---|---|---|---|
| `claim-{username}` | `jupyterhub.custom.storage-capacity` | `20Gi` | Home directory, mounted at `/home/jovyan` |
| `nebi-workspaces-{slug}` | `jupyterhub.custom.workspace-storage-capacity` | `20Gi` | Nebi (pixi) environments |

Set `workspace-storage-class` and let `storage-capacity` follow your home-directory policy.
Pixi environments run 2–5 GiB each, so the workspace PVC fills faster than people expect.

:::note[Both PVCs are RWO, so a user's pods land on one node]
The chart adds a pod-affinity rule keeping a user's JupyterLab pod and their jhub-apps pods
together for that reason. A user with several running apps is pinned to a single node.
:::

## After changing profiles

Profile changes live in the hub ConfigMap, so the hub must restart:

```bash
kubectl -n data-science rollout restart deployment/hub
```

Running servers keep the profile they spawned with. Users see the new list on their next
spawn.
