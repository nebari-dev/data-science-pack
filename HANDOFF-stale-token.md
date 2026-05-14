# RESOLVED 2026-05-15 — switched hub to GenericOAuthenticator

The env-list stale-token symptom is gone in production. Hub now runs
JupyterHub's `KeyCloakOAuthenticator(GenericOAuthenticator)` instead of
reading Envoy-set cookies; the built-in `refresh_user` runs the
refresh_token grant on `auth_refresh_age` (240s) so the stored
access_token never goes stale.

**Proof from hub log on hetzner**:

```
23:28:27  token-exchange step 1: refresh succeeded — valid for 300s
23:34:29  token-exchange step 1: refresh succeeded — valid for 300s   (6 min later)
nebi-envs: listed 0 ready environments (test user has no workspaces)
```

Pre-fix the 23:34 probe would have returned `EXPIRED` and `400
invalid_request` from KC at step 2.

**Landed PRs**:
- nebari-data-science-pack #53 — `KeyCloakOAuthenticator`, chart wiring,
  `enforceAtGateway: false` for hub, starlette<1 cap, jhub-apps 2025.11.1
  (off the git pin), unit tests.
- hetzner-nebari main (PR #1) — Secret mount, OAUTH_* env vars,
  service_workers: 1 to fit under hub's 10s managed-service timeout,
  JUPYTERHUB_OIDC_CLIENT_SECRET re-exposed as env for 03-nebi-envs.py.

The old design notes below are kept for the archive; they describe the
symptom + the dead-ends we ruled out (cache nebi JWT, plumb Bearer
through jhub-apps) before landing the GenericOAuthenticator switch.

---

# Stale access-token in jhub-apps env selector — handoff

## Symptom

`/services/japps/conda-environments/` returns `[]` ~5 min after a fresh Keycloak login, so the **Software Environment** dropdown on the *Create App* page disappears. Refreshing the page does not help. Hard-clearing cookies + logging in again restores it for another ~5 min.

## What works today

End-to-end chain is wired correctly:

| Layer | Confirmed |
|-------|-----------|
| Keycloak v26.5 standard token exchange (V2) on `jupyterhub-…` client | `standard.token.exchange.enabled=true` (operator) |
| Audience mapper for `nebi-…` on hub client | `oidc-audience-mapper` (operator) |
| Hub client allowed to exchange to nebi audience | yes |
| Nebi accepts the resulting JWT | yes |
| ds-pack chart passthrough for `enforceAtGateway` / `forwardAccessToken` | yes |
| `_extract_envoy_cookies` reads `Authorization: Bearer` first, cookie fallback | yes (PR #53) |
| Stale-token re-fetch in `03-nebi-envs.py` | yes (PR #53) |

## Why it still breaks at ~5 min

Hub stores per-user `auth_state` (id_token + access_token) in its DB at OAuth callback time. After that:

- Envoy Gateway v1.6 **does not rotate the `AccessToken-*` cookie content** even with `oidc.refreshToken: true` set on the SecurityPolicy. Envoy refreshes the access token internally (server-side) but only updates cookies in some narrow paths — not on every upstream request.
- Hub's `EnvoyOIDCAuthenticator.refresh_user` only fires when JupyterHub itself receives a request **with a `handler` AND with the IdToken cookie**. jhub-apps service requests to `/services/japps/*` go to japps, not hub, so they never trigger `refresh_user`. Hub's `auth_state` stays at whatever was captured at last `/hub/*` browser hit.
- The env-listing callable (`get_nebi_environments`) reads `user["auth_state"]` via the hub API, which returns the stale stored value — there is no Hub API endpoint that "refresh and return". The recent `_fetch_fresh_auth_state` mitigation in `03-nebi-envs.py` is a no-op because the hub's stored value is itself stale.
- Default Keycloak access-token lifespan is **5 min**. After that, Keycloak rejects the token at step 2 of token exchange with `400 invalid_request "Invalid token"` and the callable returns `[]`.

## What we tried and ruled out

### `forwardAccessToken: true` on the SecurityPolicy
Makes Envoy inject `Authorization: Bearer <fresh access_token>` on every upstream request, fixing the freshness problem. **But** jhub-apps's `service.security.get_current_user` reads the `Authorization` header **before** its own cookie:

```python
auth_header = OAuth2AuthorizationCodeBearer(...)   # <-- reads Authorization
...
token = auth_param or auth_header or auth_cookie
token = _get_jhub_token_from_jwt_token(token)      # tries HS256 decode
```

The Keycloak access token is RS256, so `jwt.decode(..., algorithms=["HS256"])` throws `InvalidAlgorithmError`, jhub-apps returns 401, the browser is redirected to `/jhub-login`, OAuth round-trips, sets a new cookie, and the next request hits the same path — **infinite loop in the UI**.

`forwardAccessToken` is therefore defaulted to `false` in `values.yaml` until jhub-apps is patched.

### Cache `workspace_list` for 10 min (`87cd6c7`, reverted in `ec6f135`)
Hides the symptom for 10 min, but the next request after the cache expires still requires a fresh access token, which is no longer fresh — same failure mode at a longer interval.

### Cache the **Nebi JWT** for 24 h
The third leg of the exchange returns a Nebi JWT with a 24 h lifetime. Caching that per user means token exchange runs **once per 24 h per user** — virtually always within the post-login window where the access token is still fresh. Workspace list is re-fetched from Nebi on every page load using the cached Bearer.

This is the recommended fix and has not been merged yet. Skeleton:

```python
# 03-nebi-envs.py
import time, threading, json, base64

_jwt_cache = {}                 # username -> (nebi_jwt, exp_unix)
_jwt_cache_lock = threading.Lock()

def _cached_nebi_jwt(username):
    with _jwt_cache_lock:
        entry = _jwt_cache.get(username)
        if entry and entry[1] > time.time() + 30:
            return entry[0]
        return None

def _store_nebi_jwt(username, jwt_str):
    payload = jwt_str.split(".")[1] + "=" * (4 - len(jwt_str.split(".")[1]) % 4)
    exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
    with _jwt_cache_lock:
        _jwt_cache[username] = (jwt_str, exp)

def get_nebi_environments(user):
    username = user.get("name", "<unknown>")
    nebi_jwt = _cached_nebi_jwt(username)
    if not nebi_jwt:
        # cold path — needs fresh access token
        nebi_jwt = _do_token_exchange(...)   # existing logic
        if nebi_jwt:
            _store_nebi_jwt(username, nebi_jwt)
    if not nebi_jwt:
        return []
    return _list_workspaces_with_jwt(nebi_jwt, username)
```

Caveats:
- Nebi role / group changes for the user only take effect on next exchange (next login or 24 h). Acceptable.
- Cache lives in the japps **service** process (one of `service_workers` uvicorn workers). Each worker has its own dict — first miss in any worker triggers an exchange. Optional: shared cache via Redis or file. Not urgent.
- Cold-start path still fails if the very first env-listing call after hub restart happens >5 min after the user's last `/hub/*` hit. Mitigation: the spawner already runs the same exchange at spawn time. Hub could write the resulting JWT to a per-user file (`/tmp/nebi-jwt-<username>`) and the env-listing callable could read that as the warm-cache source. Optional follow-up.

## Other live work

- **operator PR #116** — V2 attribute + audience mapper, merged-ready. The build-multiarch workflow has a temp `needs: []` stub that must be reverted before review.
- **ds-pack PR #53** — stale-token re-fetch + chart passthrough + Bearer-header reader + `forwardAccessToken: false` default.
- **hetzner-nebari main** — already pointing at the operator fix branch and the ds-pack PR branch. Revert to stable tags once both PRs land in releases.
- **nebari.openteams.ai** — local commit `15790f8` bumps the operator kustomize ref to the fix branch. Not pushed.

## Files of interest

- `config/jupyterhub/01-spawner.py` — `get_nebi_jwt` helper (3-step exchange), `_fetch_fresh_auth_state`, `_nebi_pre_spawn_hook`.
- `config/jupyterhub/03-nebi-envs.py` — current `get_nebi_environments` callable. Add the JWT cache here.
- `config/jupyterhub/00-gateway-auth.py` — `EnvoyOIDCAuthenticator.refresh_user`. Already reads `Authorization: Bearer` first when present.
- `templates/nebariapp.yaml` — chart passes `enforceAtGateway`, `forwardAccessToken`, `tokenExchange` through to the operator.
- `values.yaml` — `nebariapp.auth.enforceAtGateway: true`, `forwardAccessToken: false` (default).
