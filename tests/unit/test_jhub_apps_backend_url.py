"""jhub-apps' own hub-API base URL wiring in 02-jhub-apps.py.

jhub-apps runs as a managed-service subprocess inside the SAME pod as
hub, but z2jh injects JUPYTERHUB_API_URL pointing at the `hub` Service
(ClusterIP self-reference). Routing same-pod traffic through a Service
depends on the CNI supporting hairpin NAT for a pod reaching its own
Service via that Service's ClusterIP -- confirmed to time out
(httpcore.ConnectTimeout) on a kind/kindnet cluster. Rewriting the host
to localhost (same port/path) is always reliable for same-pod traffic
and doesn't depend on hairpin NAT support.

The rewrite can't be precomputed in Python at config-load time: the hub
container's own os.environ has no JUPYTERHUB_API_URL there (JupyterHub
only computes and injects that value into a service's own environment
at spawn time) -- confirmed live, an earlier version of this fix that
read os.environ.get("JUPYTERHUB_API_URL") here always got "", so the
rewrite never applied and jhub-apps kept timing out against the `hub`
Service. So the command is wrapped with a shell snippet that rewrites
$JUPYTERHUB_API_URL to localhost at the moment the subprocess execs,
using whatever JupyterHub has actually put in its environment by then.

02-jhub-apps.py isn't independently importable: it needs `jhub_apps`
and `z2jh` (real chart-image dependencies, not installed in the unit
test venv) at module load time for things unrelated to this fix. Stub
both minimally so the real file executes end-to-end and this test
exercises the actual production code path, not a reimplementation of
it elsewhere.
"""

from __future__ import annotations

import subprocess
import sys
import types

from conftest import FakeConfig, load_config_module

ORIGINAL_COMMAND = [
    "python",
    "-m",
    "uvicorn",
    "jhub_apps.service.app:app",
    "--port=10202",
    "--host=0.0.0.0",
    "--workers=1",
]


def _install_stub_dependencies(monkeypatch):
    """Stub jhub_apps + z2jh just enough for 02-jhub-apps.py to load."""
    jhub_apps_mod = types.ModuleType("jhub_apps")
    jhub_apps_mod.theme_template_paths = []
    jhub_apps_mod.themes = types.SimpleNamespace(DEFAULT_THEME={})

    def _fake_install_jhub_apps(c, spawner_to_subclass=None):
        c.JupyterHub.services = [
            {
                "name": "japps",
                "oauth_client_id": "service-japps",
                "command": list(ORIGINAL_COMMAND),
            }
        ]
        c.JupyterHub.load_roles = [{"name": "user", "scopes": []}]
        return c

    configuration_mod = types.ModuleType("jhub_apps.configuration")
    configuration_mod.install_jhub_apps = _fake_install_jhub_apps

    def _fake_get_config(key, default=None):
        return default

    z2jh_mod = types.ModuleType("z2jh")
    z2jh_mod.get_config = _fake_get_config

    monkeypatch.setitem(sys.modules, "jhub_apps", jhub_apps_mod)
    monkeypatch.setitem(sys.modules, "jhub_apps.configuration", configuration_mod)
    monkeypatch.setitem(sys.modules, "z2jh", z2jh_mod)


def _load(monkeypatch):
    _install_stub_dependencies(monkeypatch)
    c = FakeConfig()
    mod = load_config_module("02-jhub-apps.py", inject_c=c)
    return c, mod


def _japps_service(c):
    return next(svc for svc in c.JupyterHub.services if svc.get("name") == "japps")


def _run_wrapped_command(command, jupyterhub_api_url):
    """Actually run the wrapped shell command's rewrite in a real shell.

    The rewrite lives in a shell snippet, not Python, so the only honest
    test is exec'ing it in a real /bin/sh with a fake JUPYTERHUB_API_URL
    and observing the rewritten value -- a unit test of the Python string
    construction alone couldn't catch a shell syntax error or a wrong sed
    pattern. Splits the rewrite off the trailing `exec <original argv>`
    (which would actually try to launch uvicorn) and prints the result
    instead of exec'ing it.
    """
    assert command[:2] == ["sh", "-c"]
    rewrite = command[2].split("; exec ", 1)[0]
    result = subprocess.run(
        ["sh", "-c", f'{rewrite}; printf %s "$JUPYTERHUB_API_URL"'],
        env={"JUPYTERHUB_API_URL": jupyterhub_api_url, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_japps_command_is_wrapped_in_a_shell_rewrite(monkeypatch):
    c, _ = _load(monkeypatch)
    japps = _japps_service(c)
    assert japps["command"][:2] == ["sh", "-c"]
    # The original argv is still there, just appended after the rewrite.
    assert "exec python -m uvicorn jhub_apps.service.app:app" in japps["command"][2]


def test_rewrite_replaces_host_with_localhost_keeping_port_and_path(monkeypatch):
    c, _ = _load(monkeypatch)
    japps = _japps_service(c)
    out = _run_wrapped_command(japps["command"], "http://hub:8081/hub/api")
    assert out == "http://localhost:8081/hub/api"


def test_rewrite_handles_a_url_with_no_explicit_port(monkeypatch):
    c, _ = _load(monkeypatch)
    japps = _japps_service(c)
    out = _run_wrapped_command(japps["command"], "http://hub/hub/api")
    assert out == "http://localhost/hub/api"
