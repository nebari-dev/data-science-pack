# GPU profile image auto-derivation

Issue: https://github.com/nebari-dev/data-science-pack/issues/230

## Problem

Deployers who add GPU JupyterLab profiles must hardcode
`quay.io/nebari/nebari-data-science-pack-jupyterlab-gpu:sha-<short>` in their
overlay. CPU profiles inherit the chart's image tag on every pack update; GPU
profiles silently fall behind until someone remembers to bump the SHA.

Both `nebari-data-science-pack-jupyterlab` and
`nebari-data-science-pack-jupyterlab-gpu` are built by the same
`build-images.yaml` workflow from the same commit, so they always share the
same `sha-<short>` tag. The chart therefore already knows the correct GPU
image ref: `<singleuser.image.name>-gpu:<singleuser.image.tag>`.

## Design

A new per-profile boolean `gpu: true` in `jupyterhub.custom.profiles`:

```yaml
- slug: gpu
  display_name: "GPU Access"
  gpu: true
  access: yaml
  groups: [gpu-access]
  kubespawner_override:
    # no image needed — injected automatically
    node_selector: {node.kubernetes.io/instance-type: g4dn.xlarge}
    extra_resource_limits: {nvidia.com/gpu: 1}
```

### Components

1. **Helm helper** `nebari-data-science-pack.gpuJupyterlabImage`
   (`templates/_helpers.tpl`): renders
   `<.Values.jupyterhub.singleuser.image.name>-gpu:<tag>`; empty when
   name/tag unset.

2. **Chart-derived config** (`templates/hub-config.yaml`): add
   `"gpu-image"` to `_CHART_DERIVED`, following the existing `nebi-image`
   pattern. Deployers can override via `jupyterhub.custom.gpu-image`
   (documented as `""` placeholder in `values.yaml`).

3. **Spawner config** (`config/jupyterhub/01-spawner.py`): at load,
   `_resolve_gpu_profiles(profiles, gpu_image)` walks `custom.profiles`:
   * `gpu: true` and no `kubespawner_override.image` → inject `gpu_image`
   * `gpu: true` with explicit image → leave the image alone (explicit wins)
   * the `gpu` key is always stripped so KubeSpawner never sees it
   * `gpu_image` empty → strip key, inject nothing (falls back to the
     z2jh singleuser default image, today's behavior)

   Resolution happens once at module load, before `_render_profile_list`
   filtering, so jhub-apps' server-types endpoint sees the same image.

### Automatic tag currency

`scripts/bump_image_tags.py` already bumps `jupyterhub.singleuser.image.tag`
every release; the derived GPU ref follows with zero script changes.

### Out of scope (YAGNI)

* Auto-injecting `extra_resource_limits` / tolerations — cluster-specific,
  already documented.
* `profile_options.image.choices` for GPU profiles — deployer-defined
  profiles rarely carry them; explicit choices keep winning if present.

## Testing

* Unit (`tests/unit/test_spawner_profiles.py`): injection, explicit-image
  precedence, key stripping, empty-gpu-image fallback, load-time wiring.
* Chart (`tests/unit/test_chart_derived.py`): rendered `_CHART_DERIVED`
  contains the derived `gpu-image` ref from the default values.

## Docs

* `docs/src/content/docs/server-profiles.md` GPU section: document `gpu: true`.
* `values.yaml` comments for `gpu-image` + profile example.
