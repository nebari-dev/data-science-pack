---
title: GPU Profiles
description: Requesting GPUs and scheduling onto tainted GPU nodes.
---

A profile requests a GPU by setting `extra_resource_limits` in its
`kubespawner_override`:

```yaml
jupyterhub:
  custom:
    profiles:
      - slug: gpu-instance
        display_name: "GPU Instance"
        kubespawner_override:
          extra_resource_limits:
            nvidia.com/gpu: 1
```

## Scheduling onto tainted GPU nodes

GPU node groups are commonly tainted (e.g. NIC taints them with
`nvidia.com/gpu=true:NoSchedule`) so ordinary pods stay off GPU nodes. A pod
that requests `nvidia.com/gpu` needs a matching toleration to schedule there.

Whether you need to add that toleration yourself depends on whether the
cluster's apiserver runs the `ExtendedResourceToleration` admission
controller. When it's enabled, the apiserver auto-injects the matching
toleration for any pod that requests an extended resource like
`nvidia.com/gpu` - no action needed.

- **EKS** enables it by default - it's listed under "enabled admission
  controllers" for every current EKS platform version in [AWS's EKS platform
  versions docs](https://docs.aws.amazon.com/eks/latest/userguide/platform-versions.html).
- **GKE** enables it by default too - see
  [Google's GPU docs](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus):
  "GKE automatically applies a toleration so only Pods requesting GPUs are
  scheduled on GPU nodes."
- **AKS** does not: Microsoft's own
  [GPU best-practices doc](https://learn.microsoft.com/en-us/azure/aks/best-practices-gpu)
  instructs you to "add a matching toleration in your GPU workload pod spec"
  yourself, with no mention of automatic injection.

So on EKS or GKE, GPU profiles schedule onto tainted GPU nodes with no
extra configuration. On AKS, and on vanilla / kubeadm / kops / on-prem
clusters where the admission controller isn't enabled, a GPU server stays
`Pending` and never schedules onto the GPU node without it. Add the
toleration to the GPU profile's `kubespawner_override` instead.
`kubespawner_override` accepts any KubeSpawner trait, and `tolerations` is
one of them, so no chart code change is needed:

```yaml
jupyterhub:
  custom:
    profiles:
      - slug: gpu-instance
        display_name: "GPU Instance"
        kubespawner_override:
          extra_resource_limits:
            nvidia.com/gpu: 1
          tolerations:
            - key: "nvidia.com/gpu"
              operator: "Exists"
              effect: "NoSchedule"
```

`operator: Exists` matches the taint regardless of its value, so it works
with a `value: "true"` taint and any other value. Non-GPU profiles need no
toleration.

> Auto-injecting this toleration for any GPU profile is tracked in
> [issue #117](https://github.com/nebari-dev/nebari-data-science-pack/issues/117).

### `tolerations` in `kubespawner_override` replaces, not appends

`kubespawner_override` values are merged into the running spawner one key at
a time: dict-valued traits are merged in, but `tolerations` is a *list*
trait, so KubeSpawner replaces it outright
(`setattr(spawner, 'tolerations', [...])`) rather than appending to it. Any
global tolerations the chart set at hub startup from
`scheduling.userPods.tolerations` - for example z2jh's dedicated-user-nodes
tolerations - are dropped for that profile, which can block it from
scheduling onto nodes those tolerations were meant to reach.

Routing the toleration through `extra_pod_config` instead doesn't avoid
this - `extra_pod_config` is applied to the pod spec the same way, as a
top-level overwrite of whichever keys it sets (see the comment above
`c.KubeSpawner.extra_pod_config` in `config/jupyterhub/01-spawner.py`), so a
`tolerations` key there replaces `pod.spec.tolerations` too.

If the chart has `scheduling.userPods.tolerations` set, the only fix today
is to repeat those entries alongside the GPU toleration in the profile's
`kubespawner_override.tolerations` list. A proper chart-side fix would need
to merge rather than override when auto-injecting the GPU toleration -
worth folding into [issue #117](https://github.com/nebari-dev/nebari-data-science-pack/issues/117)
if that gets implemented.
