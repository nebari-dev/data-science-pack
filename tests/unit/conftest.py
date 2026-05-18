"""Shared fixtures for unit tests against config/jupyterhub/*.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace, ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config" / "jupyterhub"


def load_config_module(filename: str, inject_c: "FakeConfig | None" = None) -> ModuleType:
    """Load a jupyterhub_config.d file (hyphenated, digit-prefixed) by path.

    JupyterHub exec's these files with `c` in scope; if ``inject_c`` is
    given, simulate that by exec'ing the file with `c` pre-bound in module
    globals. Otherwise the module's `try: c` guard skips the bottom wiring.

    In production, 00-chart-derived.py (rendered by Helm) defines
    `get_chart_config` in the shared exec namespace before 01/02/03 load.
    For unit tests we stub it so files can be loaded standalone.
    """
    path = CONFIG_DIR / filename
    mod_name = "_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    if inject_c is not None:
        module.__dict__["c"] = inject_c

    def _stub_get_chart_config(key, default=""):
        # Mirrors the production helper without the Helm-rendered fallback.
        # Tests pin behaviour by mocking z2jh.get_config where it matters.
        from z2jh import get_config as _gc  # noqa: PLC0415
        return _gc(f"custom.{key}", default)

    module.__dict__["get_chart_config"] = _stub_get_chart_config
    spec.loader.exec_module(module)
    return module


class FakeTraitlet(SimpleNamespace):
    """Stand-in for a traitlets class on the JupyterHub `c` config object.

    Real traitlets validate types; here we just want to record what the
    module sets, so SimpleNamespace is enough.
    """


class FakeConfig:
    """Mimics enough of JupyterHub's `c` to record assignments."""

    def __getattr__(self, name):
        # Attribute access creates a new FakeTraitlet on demand (traitlets-like).
        ft = FakeTraitlet()
        self.__dict__[name] = ft
        return ft
