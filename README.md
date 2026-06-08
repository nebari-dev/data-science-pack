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

```bash
helm repo add nebari https://nebari-dev.github.io/nebari-data-science-pack
helm repo update
helm install data-science-pack nebari/nebari-data-science-pack
```

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

## Gallery

Set `singleuser.gallery.enabled=true` and populate `singleuser.gallery.exhibits`
to surface curated starter notebooks/tutorials in JupyterLab's launcher. Each
exhibit is a Git repo that clones into the user's home on first open:

```yaml
singleuser:
  gallery:
    enabled: true
    title: "Examples"
    destination: "/home/jovyan/gallery"
    exhibits:
      - title: "Tutorials"
        git: "https://github.com/your-org/tutorials"
        description: "End-to-end tutorial notebooks"
      - title: "Starters"
        git: "https://github.com/your-org/starter-notebooks"
```

The [jupyterlab-gallery](https://github.com/nebari-dev/jupyterlab-gallery)
extension is baked into the singleuser image; the values block above is the
only deploy-time configuration needed. See the extension README for the full
per-exhibit schema (icons, private-repo auth, etc.).

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

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

