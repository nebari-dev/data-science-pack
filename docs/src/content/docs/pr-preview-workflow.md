---
title: PR Preview Environments
description: How the k8s-preview CI workflow deploys and exposes an ephemeral per-PR environment.
---

The `k8s-preview` GitHub Actions workflow
(`.github/workflows/k8s-preview.yaml`) deploys this PR's chart into an
ephemeral kind cluster running the full Nebari platform stack (Keycloak +
Nebari Operator + Envoy Gateway, via `nebari-dev/action-nebari-sandbox`),
then exposes it through a per-PR Cloudflare Tunnel behind Cloudflare Access
(GitHub SSO). All business logic lives in `scripts/preview/`, a tested
Python package; the workflow itself is thin orchestration.

## Triggering it

Add the `deploy-preview` label to a PR. That label gates who can trigger a
deploy (GitHub label permissions), so a labeled fork PR still runs, using
that fork's own code. Removing the label runs the `cleanup-preview` job,
which cancels any in-flight deploy for that PR, marks the GitHub deployment
inactive, and posts a "stopped" comment.

## Lifetime and the `extend-preview` label

A preview is live for 20 minutes by default (the tunnel step's own
timeout, kept under the job's 90-minute timeout so it self-ends cleanly
instead of GitHub reporting a false "cancelled" run). Adding the
`extend-preview` label resets the deadline to 20 minutes from that moment;
it can be added any number of times, but each add is a reset, not an
addition on top of what's left. The label is polled from inside the running
tunnel step (`scripts/preview/tunnel.py`), not re-triggered as a new
workflow run, so extending is excluded from the workflow's own
`cancel-in-progress` concurrency group.

## Hostnames and TLS

Preview hostnames stay single-level
(`pr-<number>-data-science-pack.<domain>`): the free Cloudflare Universal
SSL certificate on the preview zone only covers the zone itself plus one
wildcard level.

## Authentication

`nebariapp.enabled=true` routes login through a real Operator-provisioned
Keycloak client, the same auth path production deployments use. A test
reviewer account is created directly in Keycloak so a reviewer can sign in
without a real SSO identity; Cloudflare Access in front of the tunnel is the
actual security boundary, so a simple known password for that account is
acceptable.

## Deploy steps

- **Sandbox cluster**: kind (pinned to v0.32.0+; older kind can't parse the
  sandbox action's containerd v4 config) plus the full platform stack via
  `action-nebari-sandbox`.
- **Namespace label**: the Operator only reconciles `NebariApp` resources in
  namespaces carrying `nebari.dev/managed=true`; missing it blocks
  reconciliation permanently.
- **Keycloak hostname patch**: ArgoCD's `selfHeal` reverts a direct `kubectl
  patch`, and Keycloak's own hostname must match the public tunnel route
  before login works. `scripts/preview/keycloak_gitops.py` rewrites the
  GitOps source file instead, so ArgoCD applies and keeps the change.
- **Chart deploy**: runs without `helm --wait`; the hub crash-loops until
  the Operator's Keycloak client `Secret` exists, which happens
  asynchronously. Readiness is polled separately afterward.
- **Secret + restart polling**: the `Secret` existing isn't enough, since
  the Operator populates `issuer-url` on a later reconcile pass, and a
  single hub restart afterward isn't reliable either (kubelet caches
  mounted `Secret` volumes). `scripts/preview/k8s_wait.py` polls for the
  key, then restarts the hub until the value is actually picked up.
- **jhub-apps smoke test**: jhub-apps runs as a subprocess inside the hub
  pod, so a crash there doesn't fail `helm --wait`, it only surfaces later
  as a 502. The workflow checks it directly right after deploy.

## Tunnel and GitHub visibility

- **Named Tunnel**: each PR gets its own named Cloudflare Tunnel sitting
  behind Access, rather than an anonymous quick tunnel.
- **GitHub Deployment**: a Vercel-style deployment box is created via the
  GitHub Deployments API, separate from the sticky PR comment (which
  doesn't move once posted). `required_contexts: []` keeps this preview
  link from gating on unrelated checks like lint or test.
- **Comment timestamps**: rendered with GitHub's `<relative-time>` web
  component, so "Expires in 12 minutes" keeps ticking client-side with no
  manual timezone math or re-editing needed.
- **On expiry**: the deployment is marked inactive and, if a "ready"
  comment was posted, it's re-rendered to the expired state.

## Debugging a failed run

On deploy failure, the workflow dumps ArgoCD/pod/job status
(`scripts/preview-debug-dump.sh`) and opens a `tmate` SSH session into the
runner, scoped to the triggering actor and bounded to 20 minutes so it
can't hang the job indefinitely.

## One-time Cloudflare setup

Configured once in the Cloudflare Zero Trust dashboard for this repository:

- Repository variable `PREVIEW_DOMAIN`: the zone name (e.g.
  `openteams.app`).
- A Cloudflare Access application for `*.<PREVIEW_DOMAIN>`, GitHub as the
  identity provider, and a policy scoped to this org.
- Secret `CLOUDFLARE_TUNNEL_ACCOUNT_ID`: that Cloudflare account's ID.
- Secret `CLOUDFLARE_TUNNEL_API_TOKEN`: `Tunnel:Edit` + `Zone:Read` +
  `DNS:Edit`, scoped to the `PREVIEW_DOMAIN` zone/account only. This is
  separate from `CLOUDFLARE_API_TOKEN`, which belongs to the Pages account
  used by `docs.yml`.

## Security notes

- kind shares the runner's Docker daemon and `kindnet` doesn't enforce
  `NetworkPolicy`; `GITHUB_TOKEN` permissions are scoped minimally per job.
- The `cloudflared` binary is pinned by version and a matching published
  `sha256`, updated together in the workflow's `env:` block.
