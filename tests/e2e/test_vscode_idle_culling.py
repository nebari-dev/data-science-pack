"""VS Code idle-culling behavior (issue #208), verified inside a live pod.

What e2e can and cannot cover: these tests exercise the proxy-activity
plumbing, the extension delivery, and the reporting endpoint — via HTTP
from inside the pod. Extension *activation* needs a real VS Code browser
client, which this harness doesn't have; that path is validated by manual
soak (see the design spec).
"""

import json
import time

# JUPYTERHUB_SERVICE_PREFIX ends with "/" — concatenate with ${VAR} (no
# added slash): the proxy route regex does not tolerate "//vscode/".
CURL_STATUS = (
    'curl -sf -H "Authorization: token $JUPYTERHUB_API_TOKEN" '
    '"http://127.0.0.1:8888${JUPYTERHUB_SERVICE_PREFIX}api/status"'
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
