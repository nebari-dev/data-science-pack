---
title: Data Science Pack
description: A Helm chart for deploying JupyterHub with jhub-apps on Kubernetes, integrated with the Nebari Operator via the NebariApp CRD.
---

The **Data Science Pack** is a Helm chart that deploys [JupyterHub](https://jupyter.org/hub)
with [jhub-apps](https://github.com/nebari-dev/jhub-apps) on Kubernetes. It wraps
the upstream [Zero to JupyterHub](https://z2jh.jupyter.org/) chart, adding
Nebari's custom images, per-group shared storage, and integration with the
[Nebari Operator](https://github.com/nebari-dev/nebari-operator) via the
`NebariApp` CRD.

## What it does

| Capability | Description |
|---|---|
| **JupyterHub** | Multi-user notebook server with Nebari's custom images |
| **jhub-apps** | Deploy and share data science applications (Streamlit, Panel, custom commands) alongside notebooks |
| **NebariApp integration** | Registers routing, Keycloak OAuth, and a landing-page card via the Nebari Operator |
| **Shared storage** | Per-group directories (`/shared/<group>`) mounted into every user pod |
| **Nebi integration** | Ships the `nebi` environment-manager binary into JupyterLab pods via an init container |
| **RBAC bootstrap** | One-shot Keycloak Job that wires group-membership claims and shared-mount roles |
| **Dummy authenticator** | Any username/password works for local development; OAuth/Keycloak configurable for production |

## Guides

- [Quick Start](/quick-start/) — install the chart and access JupyterHub.
- [Architecture](/architecture/) — how the proxy, hub, jhub-apps, and user pods fit together.
- [Shared Storage](/shared-storage/) — per-group directories, StorageClass requirements, and the transitional NFS mode.
- [Nebi in JupyterLab](/nebi-in-jupyterlab/) — everyday workflows: your own environments, sharing them with a team, and using shared ones.

## Administration

- [Admin setup](/admin-setup/) — cluster prerequisites, the one required value, and what the chart creates.
- [Server profiles](/server-profiles/) — sizing JupyterLab servers and gating profiles by group.
- [Nebi integration](/nebi-integration/) — wiring the environment manager: images, OIDC, registries.
- [MLflow integration](/mlflow-integration/) — letting notebooks log experiments to MLflow.

## Reference

- [Configuration](/configuration/) — the top-level `values.yaml` sections.
- [Values reference](/values-reference/) — field-by-field detail for every value.
- [NebariApp Integration](/nebariapp-integration/) — the CRD fields this chart sets and why.

Source, issues, and the full `values.yaml` live in the
[`data-science-pack`](https://github.com/nebari-dev/data-science-pack) repository.
