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


def _install_reporter(monkeypatch, tmp_path, installed=True):
    """Point CODE_EXTENSIONSDIR at a temp extensions dir, optionally
    containing the artifact the postStart vsix install would leave behind
    (code-server unpacks to <publisher>.<name>-<version>/)."""
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir(exist_ok=True)
    if installed:
        (ext_dir / "nebari.nebari-activity-reporter-0.1.0").mkdir()
    monkeypatch.setenv("CODE_EXTENSIONSDIR", str(ext_dir))
    return ext_dir


def test_vscode_entry_registered_alongside_nebi(monkeypatch):
    monkeypatch.delenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", raising=False)
    monkeypatch.delenv("CODE_EXTENSIONSDIR", raising=False)
    c = FakeConfig()
    _load_image_config(c)
    servers = c.ServerProxy.servers
    assert "nebi" in servers, "vscode registration must not clobber nebi"
    assert "vscode" in servers


def test_vscode_counts_proxy_traffic_by_default_without_chart_plumbing(monkeypatch):
    """Fail-safe default: with the env var absent (e.g. an image deployed
    without the chart's opt-in plumbing), the image falls back to the OLD
    behavior (counting proxied traffic as activity), so chart/image skew
    over-spends rather than culling active users with no reporter."""
    monkeypatch.delenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", raising=False)
    c = FakeConfig()
    _load_image_config(c)
    assert c.ServerProxy.servers["vscode"]["update_last_activity"] is True


def test_vscode_chart_optin_disables_activity_counting(monkeypatch, tmp_path):
    """The chart opts pods into the new behavior by setting the env var to
    "false" when vscodeActivity.enabled is true — effective only with the
    reporter artifact present (shared fate)."""
    monkeypatch.setenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", "false")
    _install_reporter(monkeypatch, tmp_path, installed=True)
    c = FakeConfig()
    _load_image_config(c)
    assert c.ServerProxy.servers["vscode"]["update_last_activity"] is False


def test_vscode_optin_ineffective_without_reporter_artifact(monkeypatch, tmp_path):
    """Shared fate: the chart env var and the postStart vsix install live in
    separate failure domains. If the install failed (no artifact in the
    extensions dir), disabling proxy activity would cull actively-working
    users with no keep-alive channel — so the opt-in must NOT take effect
    and the pod degrades to over-spending instead."""
    monkeypatch.setenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", "false")
    _install_reporter(monkeypatch, tmp_path, installed=False)
    c = FakeConfig()
    _load_image_config(c)
    assert c.ServerProxy.servers["vscode"]["update_last_activity"] is True


def test_vscode_reporter_check_uses_default_extensions_dir(monkeypatch, tmp_path):
    """With CODE_EXTENSIONSDIR unset, the shared-fate check must look in
    code-server's default extensions dir (~/.local/share/code-server/
    extensions) — the same place the postStart install writes to."""
    monkeypatch.setenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", "false")
    monkeypatch.delenv("CODE_EXTENSIONSDIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    default_dir = tmp_path / ".local/share/code-server/extensions"
    (default_dir / "nebari.nebari-activity-reporter-0.1.0").mkdir(parents=True)
    c = FakeConfig()
    _load_image_config(c)
    assert c.ServerProxy.servers["vscode"]["update_last_activity"] is False


def test_vscode_escape_hatch_env_restores_activity_counting(monkeypatch, tmp_path):
    """An explicit "true" env var (e.g. set manually via extraEnv) restores
    the old activity-counting behavior regardless of chart plumbing —
    even with the reporter installed."""
    monkeypatch.setenv("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", "true")
    _install_reporter(monkeypatch, tmp_path, installed=True)
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
