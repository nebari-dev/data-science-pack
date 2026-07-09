"""Tests for external-auth provider token delivery."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import textwrap
import types

import pytest

from conftest import FakeConfig, load_config_module
from test_chart_derived import REPO_ROOT, _extract_configmap_key


class _Log:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class _User:
    name = "alice@example.test"

    def __init__(self, auth_state):
        self._auth_state = auth_state

    async def get_auth_state(self):
        return self._auth_state


class _Spawner:
    def __init__(self, auth_state):
        self.environment = {}
        self.lifecycle_hooks = None
        self.log = _Log()
        self.user = _User(auth_state)


def _load_spawner_module():
    fake = types.ModuleType("z2jh")
    fake.get_config = lambda _key, default=None: default
    prior = sys.modules.get("z2jh")
    sys.modules["z2jh"] = fake
    try:
        return load_config_module("01-spawner.py", inject_c=FakeConfig())
    finally:
        if prior is None:
            sys.modules.pop("z2jh", None)
        else:
            sys.modules["z2jh"] = prior


def test_external_auth_defaults_are_disabled_when_spawner_is_unrendered():
    """Unit tests import raw config files, before Helm replaces placeholders."""
    mod = _load_spawner_module()

    assert mod.external_auth_enabled is False
    assert mod.external_auth_broker_url == ""
    assert mod.external_auth_providers == []
    assert mod.external_auth_env_var_map == {}
    assert mod.external_auth_timeout_seconds == 10


def test_external_auth_injects_github_token_and_gh_token(monkeypatch):
    mod = _load_spawner_module()
    mod.external_auth_enabled = True
    mod.external_auth_broker_url = "http://external-auth.nebari-system.svc.cluster.local"
    mod.external_auth_providers = ["github"]
    mod.external_auth_env_var_map = {"github": "GITHUB_TOKEN"}

    calls = []

    def fake_provider_token(broker_url, provider, bearer_token):
        calls.append((broker_url, provider, bearer_token))
        return {"status": "token_valid", "access_token": "ghp_example"}

    monkeypatch.setattr(mod, "_external_auth_provider_token", fake_provider_token)

    spawner = _Spawner({"access_token": "keycloak-access-token"})
    asyncio.run(mod._external_auth_pre_spawn_hook(spawner, {"access_token": "keycloak-access-token"}))

    assert calls == [
        (
            "http://external-auth.nebari-system.svc.cluster.local",
            "github",
            "keycloak-access-token",
        )
    ]
    assert spawner.environment["GITHUB_TOKEN"] == "ghp_example"
    assert spawner.environment["GH_TOKEN"] == "ghp_example"
    cmd = spawner.lifecycle_hooks["postStart"]["exec"]["command"]
    assert cmd[:2] == ["/bin/sh", "-c"]
    assert "credential.https://github.com.username x-access-token" in cmd[2]
    assert "credential.https://github.com.helper" in cmd[2]
    assert "GITHUB_TOKEN" in cmd[2]
    assert "ghp_example" not in cmd[2]
    assert "url.https://github.com/.insteadOf git@github.com:" in cmd[2]


def test_git_identity_uses_auth_email_and_preserves_existing_config():
    mod = _load_spawner_module()
    spawner = _Spawner({"access_token": "keycloak-access-token"})
    spawner.environment = {
        "PREFERRED_USERNAME": "Alice Example",
        "PREFERRED_EMAIL": "alice@example.test",
    }

    mod._configure_git_user_identity(spawner)

    cmd = spawner.lifecycle_hooks["postStart"]["exec"]["command"]
    assert cmd[:2] == ["/bin/sh", "-c"]
    assert "git config --global --get user.name" in cmd[2]
    assert "git config --global user.name \"$PREFERRED_USERNAME\"" in cmd[2]
    assert "git config --global --get user.email" in cmd[2]
    assert "git config --global user.email \"$PREFERRED_EMAIL\"" in cmd[2]
    assert "Alice Example" not in cmd[2]
    assert "alice@example.test" not in cmd[2]


def test_git_identity_appends_to_existing_poststart():
    mod = _load_spawner_module()
    spawner = _Spawner({"access_token": "keycloak-access-token"})
    spawner.environment = {
        "PREFERRED_USERNAME": "Alice Example",
        "PREFERRED_EMAIL": "alice@example.test",
    }
    spawner.lifecycle_hooks = {
        "postStart": {"exec": {"command": ["/bin/sh", "-c", "echo nss-wrapper"]}},
    }

    mod._configure_git_user_identity(spawner)

    cmd = spawner.lifecycle_hooks["postStart"]["exec"]["command"][2]
    assert cmd.startswith("echo nss-wrapper && ")
    assert "git config --global user.name \"$PREFERRED_USERNAME\"" in cmd


def test_git_identity_email_falls_back_to_email_username():
    mod = _load_spawner_module()

    assert mod._preferred_git_email({}, "alice@example.test") == "alice@example.test"
    assert mod._preferred_git_email({}, "alice") == ""


def test_external_auth_git_config_preserves_existing_poststart(monkeypatch):
    mod = _load_spawner_module()
    mod.external_auth_enabled = True
    mod.external_auth_broker_url = "http://external-auth.nebari-system.svc.cluster.local"
    mod.external_auth_providers = ["github"]
    mod.external_auth_env_var_map = {"github": "GITHUB_TOKEN"}

    monkeypatch.setattr(
        mod,
        "_external_auth_provider_token",
        lambda *_args: {"status": "token_valid", "access_token": "ghp_example"},
    )

    spawner = _Spawner({"access_token": "keycloak-access-token"})
    spawner.lifecycle_hooks = {
        "postStart": {"exec": {"command": ["/bin/sh", "-c", "echo existing"]}},
    }
    asyncio.run(mod._external_auth_pre_spawn_hook(spawner, {"access_token": "keycloak-access-token"}))

    cmd = spawner.lifecycle_hooks["postStart"]["exec"]["command"][2]
    assert cmd.startswith("echo existing && ")
    assert "credential.https://github.com.helper" in cmd


def test_external_auth_skips_when_broker_has_no_valid_provider_token(monkeypatch):
    mod = _load_spawner_module()
    mod.external_auth_enabled = True
    mod.external_auth_broker_url = "http://external-auth.nebari-system.svc.cluster.local"
    mod.external_auth_providers = ["github"]
    mod.external_auth_env_var_map = {"github": "GITHUB_TOKEN"}

    monkeypatch.setattr(
        mod,
        "_external_auth_provider_token",
        lambda *_args: {"status": "not_linked"},
    )

    spawner = _Spawner({"access_token": "keycloak-access-token"})
    asyncio.run(mod._external_auth_pre_spawn_hook(spawner, {"access_token": "keycloak-access-token"}))

    assert spawner.environment == {}


def test_external_auth_rendered_into_spawner_config(tmp_path):
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm not on PATH")

    charts_dir = REPO_ROOT / "charts"
    has_deps = charts_dir.exists() and any(charts_dir.glob("jupyterhub-*.tgz"))
    if not has_deps:
        subprocess.run(
            [helm, "dependency", "update", str(REPO_ROOT)],
            capture_output=True,
            text=True,
            check=True,
        )

    values = tmp_path / "values.yaml"
    values.write_text(
        textwrap.dedent(
            """
            keycloak:
              hostname: keycloak.example.com
            externalAuth:
              enabled: true
              brokerURL: http://external-auth.nebari-system.svc.cluster.local
              providers:
                - github
              envVarMap:
                github: GITHUB_TOKEN
              timeoutSeconds: 7
            """
        )
    )

    proc = subprocess.run(
        [
            helm,
            "template",
            "data-science-pack",
            str(REPO_ROOT),
            "-f",
            str(values),
            "--namespace",
            "jupyterhub",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    spawner_config = _extract_configmap_key(proc.stdout, "01-spawner.py")
    assert 'if _external_auth_unrendered("true")' in spawner_config
    assert 'else "true".lower() == "true"' in spawner_config
    assert (
        'else "http://external-auth.nebari-system.svc.cluster.local".strip()'
        in spawner_config
    )
    assert 'external_auth_providers = _external_auth_json(r\'\'\'["github"]\'\'\', [])' in spawner_config
    assert (
        'external_auth_env_var_map = _external_auth_json(r\'\'\'{"github":"GITHUB_TOKEN"}\'\'\', {})'
        in spawner_config
    )
    assert 'external_auth_timeout_seconds = _external_auth_int("7", 10)' in spawner_config
