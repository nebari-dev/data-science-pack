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
    """
    path = CONFIG_DIR / filename
    mod_name = "_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    if inject_c is not None:
        module.__dict__["c"] = inject_c
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
