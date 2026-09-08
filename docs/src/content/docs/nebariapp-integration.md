---
title: NebariApp Integration
description: The NebariApp CRD fields this chart sets and why.
---

When `nebariapp.enabled: true` (the default), the chart renders a
`NebariApp` custom resource that the [Nebari Operator](https://github.com/nebari-dev/nebari-operator)
reconciles into routing, an OIDC client, and an optional landing-page card.

## Pass-through and validation

Everything under `nebariapp` except `enabled` becomes the `spec` of the
`NebariApp` verbatim. The chart does not enumerate the fields, so any field the
operator's `NebariAppSpec` accepts can be set from values, including ones this
page does not mention. The operator's CRD is the authority for what exists and
what it means; it is versioned with the operator, not with this chart, so
upgrading the operator can change what is accepted without a chart release.

Values are not template-expanded. A `{{ ... }}` in a value reaches the resource
as literal text rather than being evaluated.

The chart itself checks only that a hostname is set or derivable, and that
`service`, `service.name` and `service.port` are present with a port of at least
1. Everything else is checked by the API server when the resource is applied, and
the two install paths behave differently there:

- `helm install` / `helm upgrade` **succeeds**. An unrecognised key is dropped
  before the resource is stored, and the only signal is a line on stderr:
  `Warning: unknown field "spec.auth.provisionCleint"`. If that field had a
  default in the CRD, the stored resource silently keeps the default, so a typo
  looks exactly like never having set the value.
- `kubectl apply` **rejects** the resource, naming every unrecognised field:
  `Error from server (BadRequest): ... strict decoding error: unknown field
  "spec.auth.provisionCleint", unknown field "spec.typoField"`.

Because Helm will not fail on a typo, check a values change against a live API
server before deploying it:

```bash
helm template . -n <namespace> -f my-values.yaml -s templates/nebariapp.yaml \
  | kubectl apply -n <namespace> --dry-run=server -f -
```

This uses the same strict decoding as a real `kubectl apply`, so a typo is
reported as `unknown field "spec.auth.provisionCleint"` while clean values report
`created (server dry run)`. Nothing is persisted either way.

## Routing

```yaml
nebariapp:
  hostname: ""   # derived as hub.<base-domain-of-keycloak.hostname> when empty
  service:
    name: proxy-public   # created by the jupyterhub subchart
    port: 80
  routing:
    routes:
      - pathPrefix: /
```

`routing.routes` must be set (all paths to the proxy is the default) — without
it the operator reports `RoutingNotConfigured` and the hub is never exposed.

## Auth

```yaml
nebariapp:
  auth:
    enabled: true
    provider: keycloak
    provisionClient: true
    redirectURI: /hub/oauth_callback
    scopes: [openid, profile, email, groups]
    enforceAtGateway: false
    forwardAccessToken: false
```

JupyterHub runs its own OAuth flow (`GenericOAuthenticator`/
`KeyCloakOAuthenticator`) and persists tokens to `auth_state`, so:

- `redirectURI` is JupyterHub's own callback path, not Envoy's.
- `enforceAtGateway: false` — Envoy's OIDC filter adds nothing on top of the
  hub's own auth, and its cookie rotation lag was found to stale out
  `auth_state` for `/services/japps/*` paths.
- `forwardAccessToken: false` — no upstream component needs the
  Envoy-injected bearer token; the hub already has one via `auth_state`.

The operator still provisions the Keycloak client and its Secret regardless
of `enforceAtGateway`, since `provisionClient` is independent of enforcement.

## Landing page

```yaml
nebariapp:
  landingPage:
    enabled: true
    displayName: "JupyterHub"
    description: "Interactive Python notebooks for data science"
    category: "Data Science"
    priority: 1
    healthCheck:
      enabled: true
      path: "/hub/api/health"
      intervalSeconds: 30
      timeoutSeconds: 5
```

Requires a Nebari Operator build with `LandingPageConfig` support. `icon` /
`iconLight` / `iconDark` accept a built-in icon ID or a URL to a custom
image — this chart ships its own Jupyter icon with light/dark variants
because the upstream jupyter.org logo has dark-gray elements invisible in
dark mode.
