#!/usr/bin/env bash
# preview-debug-dump.sh - Cluster state dump for k8s-preview.yaml on deploy
# failure. Last chance to see why before the cluster is torn down. Every
# command is best-effort (|| true): a missing resource must not stop the
# rest of the dump from running.
set -uo pipefail

echo "=== ArgoCD Applications ==="
kubectl -n argocd get applications -o wide || true
# wait-platform.sh only checks resources exist, never whether ArgoCD
# actually synced -- this shows if/why it didn't.
for app in keycloak nebari-operator cloudnative-pg; do
  echo "--- ArgoCD application/$app ---"
  kubectl -n argocd get application "$app" -o yaml || true
done

for ns in pr-preview keycloak; do
  echo "=== namespace: $ns ==="
  kubectl -n "$ns" get pods -o wide || true
  kubectl -n "$ns" get jobs || true
  kubectl -n "$ns" get secrets || true
  kubectl -n "$ns" get events --sort-by=.lastTimestamp || true
  for pod in $(kubectl -n "$ns" get pods -o name 2>/dev/null); do
    echo "--- describe $pod ($ns) ---"
    kubectl -n "$ns" describe "$pod" || true
    echo "--- logs $pod ($ns) ---"
    kubectl -n "$ns" logs "$pod" --all-containers --tail=100 || true
  done
done
kubectl -n pr-preview get nebariapp -o yaml || true

echo "=== nebari-operator deployment env ==="
for d in $(kubectl get deploy -A -o json | jq -r '.items[] | select(.metadata.name | test("operator")) | "\(.metadata.namespace)/\(.metadata.name)"'); do
  ns="${d%%/*}"; name="${d##*/}"
  echo "--- $d ---"
  kubectl -n "$ns" get deploy "$name" -o jsonpath='{.spec.template.spec.containers[0].env}' | jq . || true
done

echo "=== oidc-client secret: keys + issuer-url presence (no values printed) ==="
kubectl -n pr-preview get secret preview-nebari-data-science-pack-oidc-client -o json 2>/dev/null | jq -r '.data | keys' || true
issuer_b64=$(kubectl -n pr-preview get secret preview-nebari-data-science-pack-oidc-client -o jsonpath='{.data.issuer-url}' 2>/dev/null)
echo "issuer-url key present: $([ -n "$issuer_b64" ] && echo yes || echo no); decoded byte length: $(echo -n "$issuer_b64" | base64 -d 2>/dev/null | wc -c)"

echo "=== NIC config domain ==="
find /tmp -maxdepth 1 -iname "nic-config*.yaml" -exec grep -H "^domain:" {} \; || true
