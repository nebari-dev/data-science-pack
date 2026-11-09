"""The activity-reporter extension manifest must keep it running in
VS Code's Restricted Mode.

code-server opens the user's home in Restricted Mode until they click
Trust, and VS Code disables any extension that doesn't declare
untrusted-workspace support. The proxy opt-out in
images/nebi/jupyter_server_config.py only checks that the extension is
installed, so a disabled reporter means VS Code traffic stops counting as
activity AND nothing reports real interaction: an actively-working user
gets culled at shutdownNoActivityTimeout.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    REPO_ROOT / "images" / "jupyterlab" / "vscode-activity-reporter" / "package.json"
)


def test_reporter_supports_untrusted_workspaces():
    pkg = json.loads(MANIFEST.read_text())
    untrusted = pkg.get("capabilities", {}).get("untrustedWorkspaces", {})
    assert untrusted.get("supported") is True, (
        "nebari-activity-reporter must declare "
        "capabilities.untrustedWorkspaces.supported=true, or VS Code disables "
        "it in Restricted Mode and working users get idle-culled"
    )
