---
title: Shared Storage
description: Per-group shared directories, StorageClass requirements, and the transitional in-cluster NFS mode.
---

Per-group shared directories (`/shared/<group>` by default) are mounted into
every user pod. This requires a `ReadWriteMany` (RWX) `StorageClass` on the
cluster.

## On NIC-managed clusters

Use [Longhorn](https://longhorn.io/), installed by NIC's storage layer:

```yaml
sharedStorage:
  enabled: true
  storageClass: longhorn
  size: 100Gi
```

## Clusters without a native RWX class

For local development or clusters where NIC hasn't yet wired up an RWX class
(current GCP/Azure paths), the chart includes a transitional in-cluster NFS
server mode, on by default so the chart self-bootstraps RWX storage on any
cluster with a default `ReadWriteOnce` StorageClass:

```yaml
sharedStorage:
  nfsServer:
    enabled: true   # set false once the cluster has a native RWX class
```

This mode depends on the `quay.io/nebari/volume-nfs` workaround image and is
tracked for removal in
[issue #29](https://github.com/nebari-dev/data-science-pack/issues/29).

Two knobs matter on non-standard node types:

- `sharedStorage.nfsServer.nodeSelector` / `nodeAffinity` — pin the NFS
  server pod to specific nodes to avoid slow RWO PVC reattachment (detach +
  reattach can take 30-120s on some providers, e.g. Hetzner).
- `sharedStorage.nfsServer.mountOptions: ["nfsvers=3"]` — set on overlayfs
  nodes (kind, k3d, some containerd setups) where the `volume-nfs` image's
  NFSv4 export is broken.

## Scoping which groups get a shared mount

```yaml
sharedStorage:
  groups: []   # empty = mount every group from the user's Keycloak token
```

Set an explicit allowlist to limit which Keycloak groups get a shared
directory. Group-directory creation itself is gated by a Keycloak client
role (`allow-group-directory-creation-role`) that the RBAC bootstrap Job
creates — see the `rbac.bootstrap` section of `values.yaml`.
