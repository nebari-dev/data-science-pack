---
title: Quick Start
description: Install the Data Science Pack and access JupyterHub.
---

## Operator-managed install (default)

On a NIC-managed cluster, the [Nebari Operator](https://github.com/nebari-dev/nebari-operator)
and ArgoCD install this chart for you — you don't run `helm` commands
yourself. The values most deployers adjust:

```yaml
keycloak:
  hostname: keycloak.example.com   # only field required for a zero-config deploy

nebariapp:
  enabled: true                    # register with the Nebari Operator via the NebariApp CRD
  hostname: ""                     # derived as hub.<base-domain-of-keycloak.hostname> when empty

sharedStorage:
  enabled: true
  storageClass: longhorn           # set on clusters with a native RWX StorageClass
```

## Install from the Helm repository

```bash
helm repo add nebari https://raw.githubusercontent.com/nebari-dev/helm-repository/gh-pages/
helm repo update
helm install data-science-pack nebari/nebari-data-science-pack
```

Also available as an OCI artifact (no `helm repo add` needed):

```bash
helm install data-science-pack oci://quay.io/nebari/charts/nebari-data-science-pack --version <version>
```

## Install from source

```bash
git clone https://github.com/nebari-dev/data-science-pack.git
cd data-science-pack
helm dependency update
helm install data-science-pack . --namespace default
```

## Access JupyterHub

```bash
kubectl port-forward svc/proxy-public 8000:80
```

Open <http://localhost:8000> — with the dummy authenticator, any
username/password works.

## Local development

Prerequisites: [Docker](https://docs.docker.com/get-docker/),
[ctlptl](https://github.com/tilt-dev/ctlptl), [Tilt](https://docs.tilt.dev/install.html).

```bash
# Start local k3d cluster + Tilt dev loop
make up

# Tilt UI: http://localhost:10350
# JupyterHub: http://localhost:8000

# Tear down
make down
```
