"""The image-owned jupyter_server_config.py must register the `vscode`
jupyter-server-proxy entry itself (the jupyter-vscode-proxy package was
dropped) with update_last_activity=False, so VS Code keepalive traffic
stops defeating the idle cullers (issue #208).

This file exec's like JupyterHub does: with `c` in scope.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from conftest import FakeConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_CONFIG = REPO_ROOT / "images" / "nebi" / "jupyter_server_config.py"


def _load_image_config(c: FakeConfig):
    spec = importlib.util.spec_from_file_location("_img_jsc", IMAGE_CONFIG)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["c"] = c
    spec.loader.exec_module(module)
    return module


def test_vscode_entry_registered_alongside_nebi(monkeypatch):
    monkeypatch.delenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", raising=False)
    monkeypatch.delenv("CODE_EXTENSIONSDIR", raising=False)
    c = FakeConfig()
    _load_image_config(c)
    servers = c.ServerProxy.servers
    assert "nebi" in servers, "vscode registration must not clobber nebi"
    assert "vscode" in servers


def test_vscode_does_not_count_proxy_traffic_by_default(monkeypatch):
    monkeypatch.delenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", raising=False)
    c = FakeConfig()
    _load_image_config(c)
    assert c.ServerProxy.servers["vscode"]["update_last_activity"] is False


def test_vscode_escape_hatch_env_restores_activity_counting(monkeypatch):
    monkeypatch.setenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", "true")
    c = FakeConfig()
    _load_image_config(c)
    assert c.ServerProxy.servers["vscode"]["update_last_activity"] is True


def test_vscode_command_matches_previous_package_contract(monkeypatch):
    """Drop-in replacement for jupyter-vscode-proxy's generated command."""
    monkeypatch.delenv("CODE_EXTENSIONSDIR", raising=False)
    monkeypatch.delenv("CODE_WORKINGDIR", raising=False)
    c = FakeConfig()
    _load_image_config(c)
    cmd = c.ServerProxy.servers["vscode"]["command"]
    assert cmd[0] == "code-server"
    assert "--auth" in cmd and "none" in cmd
    assert "--disable-telemetry" in cmd
    assert "--port={port}" in cmd
    assert cmd[-1] == "."  # CODE_WORKINGDIR default


def test_vscode_command_honors_code_extensionsdir(monkeypatch):
    monkeypatch.setenv("CODE_EXTENSIONSDIR", "/custom/ext")
    c = FakeConfig()
    _load_image_config(c)
    cmd = c.ServerProxy.servers["vscode"]["command"]
    assert "--extensions-dir" in cmd
    assert "/custom/ext" in cmd


def test_vscode_launcher_entry_and_icon_exist(monkeypatch):
    monkeypatch.delenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", raising=False)
    c = FakeConfig()
    _load_image_config(c)
    entry = c.ServerProxy.servers["vscode"]["launcher_entry"]
    assert entry["title"] == "VS Code"
    assert Path(entry["icon_path"]).name == "code-server.svg"
    assert (REPO_ROOT / "images" / "nebi" / "icons" / "code-server.svg").exists()
