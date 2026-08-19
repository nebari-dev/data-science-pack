"""VS Code idle-culling behavior (issue #208), verified inside a live pod.

What e2e can and cannot cover: these tests exercise the proxy-activity
plumbing, the extension delivery, and the reporting endpoint — via HTTP
from inside the pod. Extension *activation* needs a real VS Code browser
client, which this harness doesn't have; that path is validated by manual
soak (see the design spec).

These tests curl `127.0.0.1:8888` directly from inside the pod, bypassing
configurable-http-proxy (CHP) entirely. That means they cannot observe
CHP-level route activity tracking: the mechanism that keeps the hub-level
`jupyterhub.cull` culler's last-activity fresh independent of
`update_last_activity` while a tab stays connected (see the design spec's
corrected mental model). What they DO verify is the in-pod
`api_last_activity` signal that `singleuserCuller.server.
shutdownNoActivityTimeout` actually reads. Also: because the co-installed
`nebari-activity-reporter` extension never activates without a real VS Code
client in this harness, it never fires its own contents-API pings during
these tests, which is exactly why
`test_vscode_proxy_traffic_does_not_count_as_activity` below can assert a
stable `last_activity` across repeated proxied requests: nothing else in
the pod is nudging it forward.
"""

import json
import time

import pytest

# JUPYTERHUB_SERVICE_PREFIX ends with "/" — concatenate with ${VAR} (no
# added slash): the proxy route regex does not tolerate "//vscode/".
CURL_STATUS = (
    'curl -sf -H "Authorization: token $JUPYTERHUB_API_TOKEN" '
    '"http://127.0.0.1:8888${JUPYTERHUB_SERVICE_PREFIX}api/status"'
)


def _wait_for_server(user, timeout_s=180):
    """Block until the singleuser jupyter server answers on :8888.

    `spawn_user` waits for the POD Ready condition, but singleuser pods
    have no readiness probe on the jupyter port, so `kubectl exec` can win
    the race against `jupyterhub-singleuser` binding :8888 (first observed
    as curl rc=7 in CI). Poll the status endpoint until it answers; every
    other exec in these tests can then assume the server is up.
    """
    deadline = time.time() + timeout_s
    rc, out = 1, "<never ran>"
    while time.time() < deadline:
        rc, out = user.exec("bash", "-c", CURL_STATUS)
        if rc == 0:
            return
        time.sleep(3)
    pytest.fail(
        f"singleuser server never answered /api/status within {timeout_s}s "
        f"(last rc={rc}: {out})"
    )


def _last_activity(user):
    rc, out = user.exec("bash", "-c", CURL_STATUS)
    assert rc == 0, f"/api/status failed (rc={rc}): {out}"
    return json.loads(out)["last_activity"]


def test_code_server_idle_timeout_env_matches_cull_timeout(spawn_user):
    """Chart default cull.timeout=1800 must reach the pod env verbatim."""
    u = spawn_user("alice-data")
    rc, out = u.exec("printenv", "CODE_SERVER_IDLE_TIMEOUT_SECONDS")
    assert rc == 0, "CODE_SERVER_IDLE_TIMEOUT_SECONDS not set on the pod"
    assert out.strip() == "1800"


def test_activity_reporter_extension_installed(spawn_user):
    """postStart must install the bundled vsix. This is the tripwire for
    the worst failure mode: silently-broken delivery would get active VS
    Code users culled mid-session."""
    u = spawn_user("alice-data")
    rc, out = u.exec(
        "bash", "-c", "ls /home/jovyan/.local/share/code-server/extensions/"
    )
    assert rc == 0, out
    assert "nebari.nebari-activity-reporter" in out


def test_vscode_proxy_traffic_does_not_count_as_activity(spawn_user):
    """The core #208 behavior: requests through /vscode/ (which is exactly
    what an open tab's keepalives are) must NOT advance last_activity."""
    u = spawn_user("alice-data")
    _wait_for_server(u)
    # First hit starts code-server via jupyter-server-proxy (timeout 300 in
    # the server entry; jsp blocks the request until the backend is up).
    rc, out = u.exec(
        "bash", "-c",
        'curl -sf -o /dev/null -H "Authorization: token $JUPYTERHUB_API_TOKEN" '
        '"http://127.0.0.1:8888${JUPYTERHUB_SERVICE_PREFIX}vscode/"',
    )
    assert rc == 0, f"vscode proxy route failed to start code-server: {out}"

    before = _last_activity(u)
    for _ in range(3):
        time.sleep(2)
        u.exec(
            "bash", "-c",
            'curl -s -o /dev/null -H "Authorization: token $JUPYTERHUB_API_TOKEN" '
            '"http://127.0.0.1:8888${JUPYTERHUB_SERVICE_PREFIX}vscode/"',
        )
    after = _last_activity(u)
    assert after == before, (
        f"proxied vscode traffic advanced last_activity {before} -> {after}; "
        "update_last_activity=False is not applied on the vscode entry"
    )


def test_contents_api_ping_counts_as_activity(spawn_user):
    """The extension's reporting mechanism: an authenticated contents-API
    request must advance last_activity (ISO8601 compares lexicographically)."""
    u = spawn_user("alice-data")
    _wait_for_server(u)
    before = _last_activity(u)
    time.sleep(1.1)  # ensure a strictly later timestamp is observable
    rc, out = u.exec(
        "bash", "-c",
        'curl -sf -o /dev/null -H "Authorization: token $JUPYTERHUB_API_TOKEN" '
        '"http://127.0.0.1:8888${JUPYTERHUB_SERVICE_PREFIX}api/contents/?content=0"',
    )
    assert rc == 0, out
    after = _last_activity(u)
    assert after > before, (
        "contents-API ping did not advance last_activity — the extension's "
        "reporting endpoint would be ineffective"
    )
