"""Integration tests run against a real Keycloak.

The sibling fixture file makes the bootstrap module importable without
pulling ``files/`` onto ``sys.path`` permanently. Tests then talk to a
live KC over HTTP — there is no in-process fake here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "files" / "keycloak_rbac_bootstrap.py"


def _load_rbac():
    spec = importlib.util.spec_from_file_location("rbac_bootstrap", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rbac_bootstrap"] = module
    spec.loader.exec_module(module)
    return module


rbac = _load_rbac()
