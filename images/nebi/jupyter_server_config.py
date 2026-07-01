import mimetypes
import os
import shutil
from pathlib import Path

# Browser rejects woff2 with "Failed to decode" if the server returns
# application/octet-stream. Register the correct MIME type.
mimetypes.add_type("font/woff2", ".woff2")

# Seed apputils-extension theme settings (IBM Plex Sans + Fira Code) into the
# user's home on every spawn. We *must* use user-settings, not admin-level
# overrides.json — `@jupyterlab/apputils-extension:themes` reads
# `settings.user.overrides` (NOT composite), so the schema-default path is
# bypassed. Only seed when no file exists, so user-customized themes win.
_skel = Path("/etc/skel/.jupyter/lab/user-settings/@jupyterlab/apputils-extension/themes.jupyterlab-settings")
_dest = Path(os.environ.get("HOME", "")) / ".jupyter/lab/user-settings/@jupyterlab/apputils-extension/themes.jupyterlab-settings"
if _skel.exists() and not _dest.exists():
    _dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_skel, _dest)

# jupyter-server-proxy configuration for Nebi
# Launches `nebi serve` when the user clicks "Nebi" in the JupyterLab launcher.
# jupyter-server-proxy picks a free port, starts the process, and proxies to it.

ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "icons",
    "nebi.svg",
)

# Build environment for nebi serve.
# NEBI_REMOTE_URL is set by the JupyterHub spawner when a Nebi team server
# is deployed alongside this pack. When present, the local Nebi instance
# will auto-connect to the remote server using the user's Keycloak cookie.
nebi_env = {
    "NEBI_SERVER_BASE_PATH": "{base_url}nebi",
    "NEBI_MODE": "local",
    "NEBI_DATABASE_DSN": os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "nebi",
        "nebi.db",
    ),
    "NEBI_STORAGE_WORKSPACES_DIR": "/var/lib/nebi/workspaces",
}
nebi_remote_url = os.environ.get("NEBI_REMOTE_URL", "")
if nebi_remote_url:
    nebi_env["NEBI_REMOTE_URL"] = nebi_remote_url

nebi_auth_token = os.environ.get("NEBI_AUTH_TOKEN", "")
if nebi_auth_token:
    nebi_env["NEBI_AUTH_TOKEN"] = nebi_auth_token

c.ServerApp.terminado_settings = {"shell_command": ["/bin/bash", "-l"]}
c.ServerApp.kernel_spec_manager_class = "nb_nebi_kernels.NebiKernelSpecManager"

c.ServerProxy.servers = {
    "nebi": {
        "command": ["nebi", "serve", "--port", "{port}"],
        "timeout": 20,
        "absolute_url": True,
        "new_browser_tab": False,
        "environment": nebi_env,
        "launcher_entry": {
            "title": "Nebi",
            "enabled": True,
            "icon_path": ICON_PATH,
        },
    }
}
