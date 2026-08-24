---
title: MLflow integration
description: Letting notebooks log experiments to an MLflow deployment — tracking URI, NetworkPolicy, and the client library.
---

[mlflow-pack](https://packs.nebari.dev/mlflow-pack/) deploys MLflow with its own hostname,
Keycloak SSO, and PostgreSQL backend. Connecting notebooks to it takes two values in this
chart, plus a client library in the user's environment.

Nothing here is enabled by default — the two packs are independent, and this is the wiring
between them.

## Why notebooks bypass the gateway

MLflow's browser UI sits behind Envoy's OIDC filter: unauthenticated requests get a 302 to
Keycloak. The MLflow Python client has no cookie jar and no interactive login, so pointing
`MLFLOW_TRACKING_URI` at `https://mlflow.example.com` gets a redirect the client cannot
follow — usually surfacing as a parse error rather than an auth error.

Notebooks therefore talk to MLflow's ClusterIP service directly, over the cluster network.

```
  browser ──► Envoy Gateway ──► MLflow UI        (Keycloak login)
  notebook ─────────────────► mlflow-pack.mlflow.svc:80   (no auth)
```

## Configuration

Both halves go under `jupyterhub.singleuser`:

```yaml
jupyterhub:
  singleuser:
    extraEnv:
      MLFLOW_TRACKING_URI: "http://mlflow-pack.mlflow.svc.cluster.local:80"
    networkPolicy:
      egress:
        - ports:
            - port: 5000
              protocol: TCP
          to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: mlflow
```

Adjust `mlflow-pack` and `mlflow` to the release name and namespace MLflow was installed
under. The MLflow service is named after its **release**, not `<release>-mlflow` — the
community chart's fullname helper collapses when the release name contains the chart name.

:::caution[The two port numbers are different, and both are correct]
The tracking URI uses **80**, the Service port. The NetworkPolicy uses **5000**, the pod
port.

NetworkPolicy is enforced at the pod IP level, *after* kube-proxy has already translated the
ClusterIP's 80 to the container's 5000. A rule written against port 80 matches nothing and
every connection times out with no error anywhere.
:::

This is the single most common reason the integration silently fails.

### Why a NetworkPolicy is needed at all

Zero to JupyterHub defaults `singleuser.networkPolicy.enabled: true` with
`egressAllowRules.privateIPs: false`. User pods can reach the public internet and DNS, and
nothing else in the cluster. Every in-cluster destination needs an explicit rule.

The blunt alternative opens all private addresses:

```yaml
jupyterhub:
  singleuser:
    networkPolicy:
      egressAllowRules:
        privateIPs: true
```

That works, and it also lets user code reach every other in-cluster service — databases,
internal APIs, the Kubernetes API's private endpoints. Prefer the targeted rule unless you
have a reason not to.

`extraEnv` is a dict, so adding `MLFLOW_TRACKING_URI` merges with the chart's existing
entries. `networkPolicy.egress` is a list, but its default is empty, so setting it is
additive in practice — entries there are rendered verbatim into the generated policy and
unioned with the built-in rules.

## Restart to pick it up

Running servers keep the environment and the policy they started with:

```bash
kubectl -n data-science rollout restart deployment/hub
```

Then users must stop and start their server from the hub control panel. A pod that predates
the change has neither the variable nor the new egress rule.

## The client library is not in the image

`mlflow` is **not** part of the JupyterLab image's package set. Setting the tracking URI
does nothing on its own — `import mlflow` fails.

Users install it in their own environment. With [Nebi](/nebi-integration/), add it to a
workspace:

```toml
[dependencies]
mlflow = ">=3"
```

or, for a quick check in the base environment:

```bash
pip install mlflow
```

For a cluster where MLflow is standard, adding it to a shared registry environment (see
[Nebi integration](/nebi-integration/#admin-provisioned-registries)) means users get it
without asking.

## Verify

From a notebook, confirm the environment reached the pod:

```python
import os
print(os.environ.get("MLFLOW_TRACKING_URI"))
```

Empty means the server predates the change — restart it. Then log a run:

```python
import mlflow

mlflow.set_experiment("connectivity-check")
with mlflow.start_run():
    mlflow.log_param("framework", "pytorch")
    mlflow.log_metric("accuracy", 0.95)

print("Run ID:", mlflow.last_active_run().info.run_id)
```

It should appear in the MLflow UI under `connectivity-check`. A hang that eventually times
out is the NetworkPolicy port.

From outside the notebook:

```bash
kubectl -n data-science exec <user-pod> -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://mlflow-pack.mlflow.svc.cluster.local:80/health').status)"
```

## What this does not give you

**Identity.** In-cluster access to MLflow is unauthenticated — any pod the NetworkPolicy
permits can read and write every experiment, with no user attached. Runs are attributed by
whatever the client sets, not by who is logged into JupyterHub. Scope the egress rule to
the namespaces that genuinely need it and treat MLflow as a shared, trusted-network
service.

**Durable artifacts.** By default mlflow-pack stores run metadata in PostgreSQL but writes
artifacts to a path inside the MLflow pod, with no volume behind it. `log_artifact()` and
`log_model()` from a notebook succeed and are lost on the next MLflow restart — leaving runs
that reference models which no longer exist. Configure an artifact bucket on the MLflow side
before people start logging models; see
[Artifact storage](https://packs.nebari.dev/mlflow-pack/artifact-storage/).

## Other in-cluster services

The same two-part pattern — an env var plus a targeted egress rule naming the **pod** port —
works for any in-cluster endpoint you want notebooks to reach. The port trap applies every
time.
