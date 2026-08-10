---
title: NebariApp Integration
description: The NebariApp CRD fields this chart sets and why.
---

When `nebariapp.enabled: true` (the default), the chart renders a
`NebariApp` custom resource that the [Nebari Operator](https://github.com/nebari-dev/nebari-operator)
reconciles into routing, an OIDC client, and an optional landing-page card.

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
