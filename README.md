# nebari-data-science-pack

[![Lint](https://github.com/nebari-dev/nebari-data-science-pack/actions/workflows/lint.yaml/badge.svg)](https://github.com/nebari-dev/nebari-data-science-pack/actions/workflows/lint.yaml)
[![Test](https://github.com/nebari-dev/nebari-data-science-pack/actions/workflows/test.yaml/badge.svg)](https://github.com/nebari-dev/nebari-data-science-pack/actions/workflows/test.yaml)
[![Release](https://github.com/nebari-dev/nebari-data-science-pack/actions/workflows/release.yaml/badge.svg)](https://github.com/nebari-dev/nebari-data-science-pack/actions/workflows/release.yaml)

A Helm chart for deploying JupyterHub with [jhub-apps](https://github.com/nebari-dev/jhub-apps) on Kubernetes.

## Features

- JupyterHub with Nebari's custom images
- jhub-apps integration for deploying data science applications
- Dummy authenticator for local development (OAuth/Keycloak configurable for production)

## Quick Start

### Install from Helm Repository

The chart is published to the central Nebari Helm repository:

```bash
helm repo add nebari https://raw.githubusercontent.com/nebari-dev/helm-repository/gh-pages/
helm repo update
helm install data-science-pack nebari/nebari-data-science-pack
```

It is also available as an OCI artifact on quay.io (no `helm repo add` needed):

```bash
helm install data-science-pack oci://quay.io/nebari/charts/nebari-data-science-pack --version <version>
```

> **Cutover note:** releases from `0.1.0-alpha.16` onward publish to the central
> repository above. The previous per-repo index at
> `https://nebari-dev.github.io/nebari-data-science-pack` is frozen; releases
> packaged there before the cutover remain installable from it, but new
> versions land only in the central repository.

### Install from Source

```bash
git clone https://github.com/nebari-dev/nebari-data-science-pack.git
cd nebari-data-science-pack
helm dependency update
helm install data-science-pack . --namespace default
```

### Access JupyterHub

```bash
kubectl port-forward svc/proxy-public 8000:80
```

Open http://localhost:8000 - with dummy auth, any username/password works.

## Local Development

Prerequisites: [Docker](https://docs.docker.com/get-docker/), [ctlptl](https://github.com/tilt-dev/ctlptl), [Tilt](https://docs.tilt.dev/install.html)

```bash
# Start local k3d cluster + Tilt dev loop
make up

# Tilt UI: http://localhost:10350
# JupyterHub: http://localhost:8000

# Tear down
make down
```

## Configuration

See `values.yaml` for all configuration options. The chart wraps the [JupyterHub Helm chart](https://z2jh.jupyter.org/) - all `jupyterhub.*` values are passed through.

### Nebi Registries

Admins can provision OCI registries for every user's nebi instance via
`nebi.registries`. Only public (unauthenticated) registries are supported;
entries carry no credentials:

```yaml
nebi:
  registries:
    - name: acme-registry
      url: registry.acme.com
      namespace: acme-envs
      default: true
```

Each entry follows nebi's own `registries.entries` schema (`name`, `url`,
`namespace`, `default`) and is rendered into a ConfigMap mounted into user
pods, so entries are locked in the UI rather than editable per-user.

Set `nebi.seedDefaultRegistry: false` to remove the built-in
`quay.io/nebari_environments` registry that nebi seeds by default.

Both settings only take effect for user servers started after the hub pod
restarts, since the mount wiring lives in the hub ConfigMap.

## Shared Storage

Per-group shared directories (`/shared/<group>` in every user pod) need a
ReadWriteMany `StorageClass` on the cluster. On NIC-managed clusters that's
[Longhorn](https://longhorn.io/), installed by NIC's storage layer:

```yaml
sharedStorage:
  enabled: true
  storageClass: longhorn
  size: 100Gi
```

For clusters where NIC has not yet wired up an RWX class (local dev, current
GCP/Azure paths), the chart includes a transitional
`sharedStorage.nfsServer.enabled=true` mode that runs an in-cluster NFS
server pod. It depends on the `quay.io/nebari/volume-nfs` workaround image
and is tracked for removal in
[issue #29](https://github.com/nebari-dev/nebari-data-science-pack/issues/29).

## Architecture

```
┌─────────────────────────────────────────────────┐
│                    proxy                         │
│              (configurable-http-proxy)           │
└─────────────────┬───────────────────────────────┘
                  │
      ┌───────────┴───────────┐
      │                       │
      ▼                       ▼
┌───────────┐          ┌─────────────┐
│    hub    │◄────────►│  jhub-apps  │
│ (JupyterHub)         │  (service)  │
└─────┬─────┘          └─────────────┘
      │
      ▼
┌─────────────┐
│ user pods   │
│ (notebooks) │
└─────────────┘
```

## CI/CD

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `lint.yaml` | push/PR | Helm lint and template validation |
| `test.yaml` | push/PR | Full deployment test on k3d |
| `release.yaml` | push to main | Publish chart to GitHub Pages |

## Releasing

To release a new version:

1. Update `version` in `Chart.yaml`
2. Push to `main`
3. The release workflow automatically:
   - Creates a GitHub release tagged with the chart version
   - Publishes the chart to GitHub Pages

**Note:** Enable GitHub Pages on the `gh-pages` branch in repo settings after the first release.

## Documentation

The docs site lives in [`docs/`](docs/) and is built with [Astro](https://astro.build) +
[Starlight](https://starlight.astro.build) using the shared `@nebari/starlight` theme. It
deploys to [packs.nebari.dev/data-science-pack/](https://packs.nebari.dev/data-science-pack/)
on every merge to `main`; pull requests that touch `docs/` get a preview URL posted as a
comment.

Administrator guides:

- [Admin setup](https://packs.nebari.dev/data-science-pack/admin-setup/) - cluster
  prerequisites, the one required value, and what the chart creates.
- [Values reference](https://packs.nebari.dev/data-science-pack/values-reference/) -
  field-by-field detail for every value.
- [Server profiles](https://packs.nebari.dev/data-science-pack/server-profiles/) - sizing
  JupyterLab servers and gating profiles by group.
- [Nebi integration](https://packs.nebari.dev/data-science-pack/nebi-integration/) - images,
  OIDC clients, token exchange, registries.
- [MLflow integration](https://packs.nebari.dev/data-science-pack/mlflow-integration/) -
  letting notebooks log experiments to MLflow.

```bash
cd docs
npm ci
npm run dev     # dev server with hot reload at http://localhost:4321
npm run build   # static build into docs/dist/
npm test        # unit tests
```

Pages live in `docs/src/content/docs/` - each `.md` or `.mdx` file becomes a page, and the
sidebar is configured in `docs/astro.config.mjs`. See [`docs/README.md`](docs/README.md) for
details.

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

