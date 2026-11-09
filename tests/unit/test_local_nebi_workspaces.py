"""Tests for running jhub-apps apps in a user's local Nebi workspace.

With no Nebi server (`nebi.remote.enabled: false`) the environment selector
offers the user's own workspaces. The app pod must not lean on anything that
only works while all of a user's pods share one node: it does not open the
Nebi database in the home, and it does not run from the environment JupyterLab
installed. It copies the workspace's `pixi.toml` and `pixi.lock` out of the
user's storage into a private directory and builds its own environment there,
the same way an app built from a Nebi server workspace does.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import types

import pytest

# 01-spawner.py imports z2jh.get_config; stub it like the other spawner tests.
_z2jh = types.ModuleType("z2jh")
_z2jh.get_config = lambda key, default=None: default
sys.modules.setdefault("z2jh", _z2jh)

from conftest import CONFIG_DIR, FakeConfig, load_config_module  # noqa: E402


class _User:
    name = "alice@example.test"

    async def get_auth_state(self):
        return None


class FakeSpawner:
    """Records what the pre-spawn hook puts on the pod."""

    def __init__(self, conda_env=""):
        self.volumes = []
        self.volume_mounts = []
        self.init_containers = []
        self.environment = {}
        self.image = "quay.io/nebari/nebari-data-science-pack-jupyterlab:test"
        self.user = _User()
        self.user_options = {"conda_env": conda_env} if conda_env else {}
        self.lifecycle_hooks = None


def _load(custom=None):
    base = {"custom.storage-capacity": "20Gi", "custom.shared-storage-enabled": False}
    base.update(custom or {})
    sys.modules["z2jh"].get_config = lambda key, default=None: base.get(key, default)
    return load_config_module("01-spawner.py", inject_c=FakeConfig())


def _spawn(conda_env, custom=None):
    mod = _load(custom)
    spawner = FakeSpawner(conda_env)
    asyncio.run(mod._pre_spawn_hook(spawner))
    return spawner


def _copy_container(spawner):
    return next((c for c in spawner.init_containers if c["name"] == "nebi-local-workspace"), None)


def test_app_builds_its_own_copy_of_the_selected_workspace():
    spawner = _spawn("/home/jovyan/sales-dashboard")

    init = _copy_container(spawner)
    assert init is not None
    env = {e["name"]: e["value"] for e in init["env"]}
    assert env["WORKSPACE_DIR"] == "/home/jovyan/sales-dashboard"
    # jhub-app-proxy looks the workspace up by the last part of the selection.
    assert env["WORKSPACE_NAME"] == "sales-dashboard"

    # It reads the user's home, and writes only to the pod's own directory.
    mounts = {m["name"]: m for m in init["volumeMounts"]}
    assert mounts["home"]["mountPath"] == "/home/jovyan"
    assert mounts["home"]["readOnly"] is True
    assert mounts["nebi-env"]["mountPath"] == "/tmp/nebi-env"
    assert any(v["name"] == "nebi-env" and "emptyDir" in v for v in spawner.volumes)

    # Every nebi process in the app pod uses the private database, in the init
    # container and in the app itself; none opens the one in the home.
    assert env["NEBI_DATA_DIR"] == "/tmp/nebi-env"
    assert spawner.environment["NEBI_DATA_DIR"] == "/tmp/nebi-env"
    assert any(
        m["name"] == "nebi-env" and m["mountPath"] == "/tmp/nebi-env"
        for m in spawner.volume_mounts
    )


@pytest.mark.parametrize(
    "selection",
    [
        "/etc",
        "/home/jovyan",                    # the home itself, not a workspace in it
        "/home/jovyan/../../etc",
        "/home/jovyan/demo/../../../etc",
        "/var/lib/nebi/workspaces/../../../etc",
        "sales-dashboard",                 # a bare name: nothing says where it is
        "alice/sales-dashboard",           # a Nebi server selection
        "/home/jovyan/{username}",         # KubeSpawner would expand it
        "/home/jovyan/demo}",
    ],
)
def test_selection_outside_the_users_storage_is_ignored(selection):
    spawner = _spawn(selection)

    assert _copy_container(spawner) is None
    assert "NEBI_DATA_DIR" not in spawner.environment


def test_selection_reaches_the_container_as_data_not_as_script():
    """The selection comes from the jhub-apps API, so a user chooses it."""
    hostile = "/home/jovyan/demo$(touch /tmp/pwned);echo"
    spawner = _spawn(hostile)

    init = _copy_container(spawner)
    assert "pwned" not in init["command"][2]
    env = {e["name"]: e["value"] for e in init["env"]}
    assert env["WORKSPACE_DIR"] == hostile


def test_copy_container_survives_kubespawner_templating():
    """KubeSpawner runs `str.format` over every string of an init container.

    A brace in the script is read as a template field: the spawn fails with
    an unhandled KeyError before the pod is ever created.
    """
    init = _copy_container(_spawn("/home/jovyan/sales-dashboard"))

    strings = [*init["command"], *(e["value"] for e in init["env"])]
    for text in strings:
        assert text.format() == text


def test_jupyterlab_itself_gets_no_copy_container():
    spawner = _spawn("")

    assert _copy_container(spawner) is None
    assert "NEBI_DATA_DIR" not in spawner.environment


def test_workspace_made_in_the_nebi_tile_is_read_from_the_workspace_volume():
    """The Nebi tile keeps its workspaces on the per-user workspace volume."""

    class _Api:
        async def create_namespaced_persistent_volume_claim(self, namespace, body):
            return None

    mod = _load()
    spawner = FakeSpawner("/var/lib/nebi/workspaces/apptest-1f0c")
    spawner.api = _Api()
    spawner.namespace = "default"
    asyncio.run(mod._pre_spawn_hook(spawner))

    mounts = {m["name"]: m for m in _copy_container(spawner)["volumeMounts"]}
    assert mounts["nebi-workspaces"]["mountPath"] == "/var/lib/nebi/workspaces"
    assert mounts["nebi-workspaces"]["readOnly"] is True


def test_copy_container_trusts_the_org_ca_when_the_bundle_is_enabled():
    """`pixi install` in the container downloads packages through the proxy too."""
    spawner = _spawn("/home/jovyan/sales-dashboard", {"custom.trust-bundle-enabled": True})

    init = _copy_container(spawner)
    env = {e["name"]: e["value"] for e in init["env"]}
    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs-extra/ca-bundle.crt"
    assert any(m["name"] == "ca-merged" for m in init["volumeMounts"])
    names = [c["name"] for c in spawner.init_containers]
    assert names.index("merge-ca-bundle") < names.index("nebi-local-workspace")


# --- what the environment selector offers ------------------------------------


def _load_selector(custom=None):
    """01-spawner.py and 03-nebi-envs.py in one namespace, as JupyterHub runs them."""
    mod = _load(custom)
    source = (CONFIG_DIR / "03-nebi-envs.py").read_text()
    exec(compile(source, "03-nebi-envs.py", "exec"), mod.__dict__)
    return mod


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def selector(monkeypatch, tmp_path):
    mod = _load_selector()
    monkeypatch.setattr(mod, "_SAVED_LISTS_DIR", str(tmp_path / "nebi-envs"))
    return mod


_RUNNING_LAB = {"name": "alice", "servers": {"": {"ready": True, "url": "/user/alice/"}}}


def test_selector_offers_each_ready_workspace_as_its_directory(monkeypatch, selector):
    mod = selector
    workspaces = [
        {"name": "sales-dashboard", "status": "ready", "path": "/home/jovyan/sales-dashboard"},
        {"name": "apptest", "status": "ready", "path": "/var/lib/nebi/workspaces/apptest-1f0c"},
        {"name": "building", "status": "creating", "path": "/home/jovyan/building"},
        {"name": "elsewhere", "status": "ready", "path": "/opt/elsewhere"},
        {"name": "no-path", "status": "ready"},
    ]

    def fake_urlopen(request, timeout=None):
        if request.full_url.endswith("/tokens"):
            return _Response(json.dumps({"token": "user-token"}).encode())
        assert request.full_url == "http://proxy-public/user/alice/nebi/api/v1/workspaces"
        return _Response(json.dumps(workspaces).encode())

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    monkeypatch.setenv("JUPYTERHUB_API_URL", "http://hub:8081/hub/api")
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "service-token")

    assert mod.c.JAppsConfig.conda_envs is mod.get_local_nebi_environments
    assert mod.get_local_nebi_environments(_RUNNING_LAB) == [
        "/home/jovyan/sales-dashboard",
        "/var/lib/nebi/workspaces/apptest-1f0c",
    ]


def test_selector_is_empty_for_a_user_whose_jupyterlab_never_ran(selector):
    mod = selector

    stopped = {"name": "alice", "servers": {"": {"ready": False, "url": "/user/alice/"}}}
    assert mod.get_local_nebi_environments(stopped) == []
    assert mod.get_local_nebi_environments({"name": "alice", "servers": {}}) == []


def test_selector_asks_the_nebi_server_when_one_is_configured():
    mod = _load_selector({
        "custom.nebi-remote-url": "https://nebi.example.test",
        "custom.nebi-internal-url": "https://nebi.internal.test",
    })

    assert mod.c.JAppsConfig.conda_envs is mod.get_nebi_environments


def test_nebi_server_selection_is_left_to_the_server_path():
    """With a Nebi server configured, the local path stays out of the way."""
    spawner = _spawn("/home/jovyan/sales-dashboard", {
        "custom.nebi-remote-url": "https://nebi.example.test",
        "custom.nebi-internal-url": "https://nebi.internal.test",
    })

    assert _copy_container(spawner) is None


# --- remembering the list while JupyterLab is stopped ------------------------


_STOPPED_LAB = {"name": "alice", "servers": {}}


def _fake_lab(monkeypatch, mod, workspaces_by_user):
    """Answer the hub token call and each user's JupyterLab Nebi listing."""
    def fake_urlopen(request, timeout=None):
        if request.full_url.endswith("/tokens"):
            return _Response(json.dumps({"token": "user-token"}).encode())
        for name, workspaces in workspaces_by_user.items():
            if request.full_url == f"http://proxy-public/user/{name}/nebi/api/v1/workspaces":
                if isinstance(workspaces, Exception):
                    raise workspaces
                return _Response(json.dumps(workspaces).encode())
        raise AssertionError(f"unexpected request {request.full_url}")

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    monkeypatch.setenv("JUPYTERHUB_API_URL", "http://hub:8081/hub/api")
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "service-token")


def _workspace(path):
    return {"name": path.rsplit("/", 1)[-1], "status": "ready", "path": path}


def test_selector_offers_the_last_list_after_jupyterlab_stops(monkeypatch, selector):
    _fake_lab(monkeypatch, selector, {"alice": [_workspace("/home/jovyan/sales-dashboard")]})
    assert selector.get_local_nebi_environments(_RUNNING_LAB) == ["/home/jovyan/sales-dashboard"]

    assert selector.get_local_nebi_environments(_STOPPED_LAB) == ["/home/jovyan/sales-dashboard"]


def test_a_failed_listing_keeps_the_last_list(monkeypatch, selector):
    _fake_lab(monkeypatch, selector, {"alice": [_workspace("/home/jovyan/sales-dashboard")]})
    selector.get_local_nebi_environments(_RUNNING_LAB)

    _fake_lab(monkeypatch, selector, {"alice": OSError("nebi serve did not start")})
    assert selector.get_local_nebi_environments(_RUNNING_LAB) == ["/home/jovyan/sales-dashboard"]
    assert selector.get_local_nebi_environments(_STOPPED_LAB) == ["/home/jovyan/sales-dashboard"]


def test_an_emptied_workspace_list_is_remembered_as_empty(monkeypatch, selector):
    _fake_lab(monkeypatch, selector, {"alice": [_workspace("/home/jovyan/sales-dashboard")]})
    selector.get_local_nebi_environments(_RUNNING_LAB)

    _fake_lab(monkeypatch, selector, {"alice": []})
    selector.get_local_nebi_environments(_RUNNING_LAB)
    assert selector.get_local_nebi_environments(_STOPPED_LAB) == []


def test_each_user_is_offered_only_their_own_list(monkeypatch, selector):
    _fake_lab(monkeypatch, selector, {"alice": [_workspace("/home/jovyan/sales-dashboard")]})
    selector.get_local_nebi_environments(_RUNNING_LAB)

    assert selector.get_local_nebi_environments({"name": "bob", "servers": {}}) == []



class _StoppingSpawner:
    """The spawner of a server that is being stopped while its pod is still up."""

    def __init__(self, server_name="", user="alice"):
        self.name = server_name
        self.user = types.SimpleNamespace(name=user)
        self.api_token = "server-token"
        path = f"/user/{user}/{server_name + '/' if server_name else ''}"
        self.server = types.SimpleNamespace(url=f"http://10.0.0.7:8888{path}")


def _fake_pod(monkeypatch, mod, answer):
    """The stopping server's pod, which the hub asks directly with the
    server's own token."""
    asked = []

    def fake_urlopen(request, timeout=None):
        asked.append(request.full_url)
        assert request.full_url == "http://10.0.0.7:8888/user/alice/nebi/api/v1/workspaces"
        assert request.headers["Authorization"] == "token server-token"
        if isinstance(answer, Exception):
            raise answer
        return _Response(json.dumps(answer).encode())

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    return asked


def test_stopping_jupyterlab_saves_its_final_list(monkeypatch, selector):
    """A user who changes workspaces and stops JupyterLab without opening the form."""
    _fake_pod(monkeypatch, selector, [_workspace("/home/jovyan/sales-dashboard")])

    asyncio.run(selector.save_list_before_stop(_StoppingSpawner()))

    assert selector.get_local_nebi_environments(_STOPPED_LAB) == ["/home/jovyan/sales-dashboard"]


def test_stopping_an_app_does_not_touch_the_list(monkeypatch, selector):
    _fake_lab(monkeypatch, selector, {"alice": [_workspace("/home/jovyan/sales-dashboard")]})
    selector.get_local_nebi_environments(_RUNNING_LAB)
    asked = _fake_pod(monkeypatch, selector, [])

    asyncio.run(selector.save_list_before_stop(_StoppingSpawner(server_name="dashboard")))

    assert asked == []
    assert selector.get_local_nebi_environments(_STOPPED_LAB) == ["/home/jovyan/sales-dashboard"]


def test_a_failed_listing_at_stop_keeps_the_last_list(monkeypatch, selector):
    _fake_lab(monkeypatch, selector, {"alice": [_workspace("/home/jovyan/sales-dashboard")]})
    selector.get_local_nebi_environments(_RUNNING_LAB)
    _fake_pod(monkeypatch, selector, OSError("connection refused"))

    asyncio.run(selector.save_list_before_stop(_StoppingSpawner()))

    assert selector.get_local_nebi_environments(_STOPPED_LAB) == ["/home/jovyan/sales-dashboard"]


def test_stopping_a_jupyterlab_that_never_came_up_still_stops(monkeypatch, selector):
    """JupyterHub also calls stop() to clean up a failed start, with no server address."""
    asked = _fake_pod(monkeypatch, selector, [])
    spawner = _StoppingSpawner()
    spawner.server = None

    asyncio.run(selector.save_list_before_stop(spawner))

    assert asked == []


def test_stopping_jupyterlab_with_a_nebi_server_configured_asks_nothing(monkeypatch):
    mod = _load_selector({
        "custom.nebi-remote-url": "https://nebi.example.test",
        "custom.nebi-internal-url": "https://nebi.internal.test",
    })
    asked = _fake_pod(monkeypatch, mod, [])

    asyncio.run(mod.save_list_before_stop(_StoppingSpawner()))

    assert asked == []
