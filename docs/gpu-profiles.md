# GPU Profiles

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

## Scheduling onto tainted GPU nodes (EKS)

On EKS, NIC taints GPU node groups with `nvidia.com/gpu=true:NoSchedule` so
ordinary pods stay off GPU nodes. EKS cannot run the `ExtendedResourceToleration`
admission controller (it is not available on the managed control plane), so
nothing injects the matching toleration for you. Without it, a GPU server stays
`Pending` and never schedules onto the GPU node.

Add the toleration to the GPU profile's `kubespawner_override`. `kubespawner_override`
accepts any KubeSpawner trait, and `tolerations` is one of them, so no chart code
change is needed:

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

`operator: Exists` matches the taint regardless of its value, so it works with
NIC's `value: "true"` and any other value. Non-GPU profiles need no toleration.

> Auto-injecting this toleration for any GPU profile is tracked in
> [issue #117](https://github.com/nebari-dev/nebari-data-science-pack/issues/117).
