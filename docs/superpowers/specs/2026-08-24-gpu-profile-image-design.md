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

### Why a boolean `gpu: true` rather than `image-variant: gpu`

* It matches how deployers already think about the profile (the issue asks
  to "specify a JupyterLab profile as a GPU node"), and reads naturally
  next to `extra_resource_limits: {nvidia.com/gpu: 1}`.
* `-gpu` is the only published runtime variant. `-base` is a build stage,
  not something a profile could select, so today there is exactly one axis.
* It is not a one-way door. If a `-rocm` or ARM-specific runtime image
  ships later, `image-variant: <name>` can be added with the same
  load-time mechanism and `gpu: true` becomes sugar for
  `image-variant: gpu` (one line in `_resolve_gpu_profiles`, no
  deprecation cycle, both keys keep working).

### Rejected alternatives

* **`image-variant: gpu` string key now.** Same code today, but generalises
  a namespace (`<name>-<variant>:<tag>` plus a per-variant override map)
  for variants that do not exist. YAGNI; see above for the upgrade path.
* **Injecting into `profile_options.image.choices` too.** Choices always
  carry an explicit image (that is their purpose), so injecting there
  would overwrite explicit deployer values and contradict "explicit wins".
  Instead the hub warns when a `gpu: true` profile declares
  `profile_options.image`, and the docs say not to combine them.
* **Templating the profile list in Helm.** Profiles are deployer-authored
  values consumed by the z2jh subchart via `custom.profiles`; this chart's
  templates never see the merged list, and z2jh values cannot reference
  each other.
* **Raising on an empty derived image.** Would break hub startup, and
  therefore login, for every user. A warning plus fallback to the CPU
  default image is the right level.

### Automatic tag currency

`scripts/bump_image_tags.py` already bumps `jupyterhub.singleuser.image.tag`
every release; the derived GPU ref follows with zero script changes.

### Out of scope (YAGNI)

* Auto-injecting `extra_resource_limits` / tolerations — cluster-specific,
  already documented.
* Rewriting `profile_options.image.choices` for GPU profiles — explicit
  choices keep winning at spawn time; the hub warns when a `gpu: true`
  profile declares that option (see Rejected alternatives).
* Teaching `scripts/bump_image_tags.py` to bump explicitly pinned `-gpu`
  refs, and recording the build invariant (both jupyterlab images from one
  `build-images.yaml` run) — follow-ups.

## Testing

* Unit (`tests/unit/test_spawner_profiles.py`): injection, explicit-image
  precedence, key stripping, empty-gpu-image fallback, load-time wiring.
* Chart (`tests/unit/test_chart_derived.py`): rendered `_CHART_DERIVED`
  contains the derived `gpu-image` ref from the default values.

## Docs

* `docs/src/content/docs/server-profiles.md` GPU section: document `gpu: true`.
* `values.yaml` comments for `gpu-image` + profile example.
