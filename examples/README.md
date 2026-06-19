# Examples

Ready-to-use configurations for deploying the data science pack.

| File | What it does |
|------|--------------|
| [`nebari-values.yaml`](nebari-values.yaml) | Full Nebari deployment. Edit `keycloak.hostname`, then `helm install`. |
| [`argocd-application.yaml`](argocd-application.yaml) | ArgoCD Application referencing the published chart. |

## Helm

```bash
helm install data-science-pack nebari/nebari-data-science-pack \
  -f examples/nebari-values.yaml
```

This assumes `nebari-operator` is running and an RWX `StorageClass` (longhorn
on NIC clusters) is available. See the comments in the file for prerequisites.

## ArgoCD

Edit the two hostnames in `argocd-application.yaml`, then:

```bash
kubectl apply -f examples/argocd-application.yaml
```

The file documents inline why each non-default setting is needed
(`managedNamespaceMetadata`, the five `ignoreDifferences` paths,
`SkipDryRunOnMissingResource`).

One operational note: the rbac-bootstrap job runs as a PostSync hook, and hook
results don't affect sync status. If it fails, the app sits `Synced`
indefinitely; the hook only re-runs on a new sync operation:

```bash
kubectl -n argocd patch application data-science-pack \
  --type merge -p '{"operation":{"sync":{}}}'
```
