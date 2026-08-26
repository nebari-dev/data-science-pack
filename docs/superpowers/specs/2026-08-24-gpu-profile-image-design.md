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

A new per-profile key `image-variant: <name>` in `jupyterhub.custom.profiles`:

```yaml
- slug: gpu
  display_name: "GPU Access"
  image-variant: gpu
  access: yaml
  groups: [gpu-access]
  kubespawner_override:
    # no image needed — injected automatically
    node_selector: {node.kubernetes.io/instance-type: g4dn.xlarge}
    extra_resource_limits: {nvidia.com/gpu: 1}
```

### Components

1. **Spawner config** (`config/jupyterhub/01-spawner.py`): at load,
   `_resolve_image_variants(profiles, base_name, base_tag, overrides)`
   walks `custom.profiles`, reading `singleuser.image.name`/`.tag` and
   `custom.image-variants` from z2jh. For each `image-variant: <name>`:
   * no `kubespawner_override.image` → inject, in order of precedence,
     `custom.image-variants.<name>` if set, else
     `<base_name>-<name>:<base_tag>`; if neither can be produced
     (`singleuser.image.name`/`tag` empty, schema-valid in z2jh) warn and
     fall back to the CPU default image
   * explicit image → leave it alone (explicit wins)
   * the key is always stripped so KubeSpawner never sees it
   * `profile_options.image` present → warn (see Rejected alternatives)

   Resolution happens once at module load, before `_render_profile_list`
   filtering, so jhub-apps' server-types endpoint sees the same image.

2. **Override map** `jupyterhub.custom.image-variants: {}` in `values.yaml`
   — full refs per variant, for mirrored registries or a variant published
   elsewhere. Read directly via z2jh `get_config` (it is a mapping, so the
   `get_chart_config` empty-string convention does not apply).

No Helm-side change: the derivation needs the variant name, which only the
profile knows, so it lives in Python where both inputs are available.

### Why `image-variant: <name>` rather than a boolean `gpu: true`

* The image naming already has a variant axis (`-gpu`), and a `-rocm` or
  arm64 runtime image is plausible. A boolean would have to coexist with a
  string key forever once it shipped in `values.yaml`.
* Same amount of code today; the derivation string is the only place the
  variant name appears.
* Cost: `image-variant: gpu` reads slightly less naturally than `gpu: true`
  in an overlay. Mitigated by the example in `values.yaml` and the docs.

### Rejected alternatives

* **Boolean `gpu: true`.** The first draft of this PR. Rejected in review
  for the reason above: it is an API surface we cannot drop without a
  deprecation cycle, and the string key costs nothing extra.
* **Deriving in Helm (`_CHART_DERIVED["gpu-image"]`).** Also the first
  draft. Works for one hardcoded variant but cannot be generic — Helm does
  not see the profile list the z2jh subchart consumes, so it cannot know
  which variant names are in use.
* **Injecting into `profile_options.image.choices` too.** Choices always
  carry an explicit image (that is their purpose), so injecting there
  would overwrite explicit deployer values and contradict "explicit wins".
  Instead the hub warns when a variant profile declares
  `profile_options.image`, and the docs say not to combine them.
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
  choices keep winning at spawn time; the hub warns when a variant profile
  declares that option (see Rejected alternatives).
* Teaching `scripts/bump_image_tags.py` to bump explicitly pinned `-gpu`
  refs, and recording the build invariant (both jupyterlab images from one
  `build-images.yaml` run) — follow-ups.

## Testing

* Unit (`tests/unit/test_spawner_profiles.py`): derivation, generic
  variant names, override map, explicit-image precedence, key stripping
  (including empty variant), empty-base fallback + warning,
  `profile_options.image` warning (+ negative), input non-mutation,
  load-time wiring from z2jh keys, load-time log naming the injected ref.

## Docs

* `docs/src/content/docs/server-profiles.md` GPU section: document `image-variant`.
* `docs/src/content/docs/values-reference.md`: `image-variants` row.
* `values.yaml` comments for `image-variants` + profile example.
