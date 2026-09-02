---
title: Jupyter Gallery
description: Configure jupyterlab-gallery exhibits, where the config has to live, and the remote-icon trap on restricted-egress clusters.
---

The [`jupyterlab-gallery`](https://github.com/nebari-dev/jupyterlab-gallery)
extension shows a curated set of tutorial "exhibits" as tiles inside
JupyterLab, each backed by a git repository the user can clone with one click.
The extension already ships in the singleuser image, so configuring it is only
a matter of telling the `GalleryManager` which exhibits to show.

## Where the config has to live

`GalleryManager` only exists in the **singleuser** Jupyter server, not in the
hub. Setting `c.GalleryManager.exhibits` under `jupyterhub.hub.extraConfig`
silently does nothing — the hub never reads it, so the gallery renders empty
with no error.

The config must be a `jupyter_gallery_config.py` file on the singleuser
server's Jupyter config path (one of `jupyter --paths` under `config`, e.g.
`/etc/jupyter/`). In this chart you inject it through the upstream
Zero to JupyterHub `singleuser.extraFiles` mechanism, which mounts the file
into every user pod:

```yaml
jupyterhub:
  singleuser:
    extraFiles:
      gallery-config:
        mountPath: /etc/jupyter/jupyter_gallery_config.py
        stringData: |
          c.GalleryManager.title = "Tutorials"
          c.GalleryManager.destination = "tutorials"
          c.GalleryManager.exhibits = [
              {
                  "title": "Xarray",
                  "git": "https://github.com/xarray-contrib/xarray-tutorial.git",
                  "homepage": "https://github.com/xarray-contrib/xarray-tutorial",
              },
          ]
```

`destination` is the directory (relative to the user's home) that exhibits are
cloned into.

## Exhibit schema

Each entry in `c.GalleryManager.exhibits` is a dict. The keys the upstream
`GalleryManager` understands:

| Key | Purpose |
|---|---|
| `git` | Clone URL of the exhibit repository (required) |
| `title` | Label shown on the tile |
| `homepage` | Link opened from the tile's "info" affordance |
| `description` | Short blurb shown on the tile |
| `icon` | Tile image (see [Tile icons](#tile-icons-and-the-remote-icon-trap) below) |
| `branch` | Branch to clone (defaults to the repo's default branch) |
| `depth` | Clone depth for shallow clones |
| `account` / `token` | Credentials for private repositories |

For private repositories, supply `account` and a personal access `token`.
Rather than embedding the token literally in the config, reference it from an
environment variable set on the singleuser pod (for example via
`jupyterhub.singleuser.extraEnv` backed by a Kubernetes Secret) so the
credential is not committed to Helm values. See the upstream
[`jupyterlab-gallery` README](https://github.com/nebari-dev/jupyterlab-gallery)
for the exact `GalleryManager` traitlets and the PAT-via-env-var pattern.

## Tile icons and the remote-icon trap

The `icon` key controls the image rendered on a tile. When `icon` points at a
**remote** URL (for example a GitHub raw link), the browser fetches it
client-side. On air-gapped or restricted-egress deployments the client cannot
reach the outside network, so that fetch fails and the tile shows the browser's
broken-image placeholder with no fallback.

For those environments, point `icon` at a **local** asset served from within
the deployment — bundled into the singleuser image, or served from the same
origin — so the tile renders without any external fetch:

```python
c.GalleryManager.exhibits = [
    {
        "title": "JATIC Checkmaite Tutorial",
        "git": "https://internal.example.com/jatic/checkmaite.git",
        "icon": "/etc/jupyter/gallery-icons/checkmaite.png",  # local, no external fetch
    },
]
```

## Looking ahead

This page documents the current, hand-rolled approach: a
`jupyter_gallery_config.py` injected through `singleuser.extraFiles`. Work is
in progress to expose a first-class `jupyterhub.singleuser.gallery` values key
that renders the `GalleryManager` config for you
([PR #118](https://github.com/nebari-dev/data-science-pack/pull/118)). Once
that lands, exhibit configuration collapses to a values block and the
`extraFiles` file is no longer needed — this page will be updated to document
the values key instead.
