import mimetypes
import os

# Register font MIME types so browsers don't reject them with "Failed to decode"
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/woff2", ".woff2")

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
    "NEBI_STORAGE_WORKSPACES_DIR": os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "nebi",
        "workspaces",
    ),
}
nebi_remote_url = os.environ.get("NEBI_REMOTE_URL", "")
if nebi_remote_url:
    nebi_env["NEBI_REMOTE_URL"] = nebi_remote_url

nebi_auth_token = os.environ.get("NEBI_AUTH_TOKEN", "")
if nebi_auth_token:
    nebi_env["NEBI_AUTH_TOKEN"] = nebi_auth_token

c.ServerApp.terminado_settings = {"shell_command": ["/bin/bash", "-l"]}
c.ServerApp.kernel_spec_manager_class = "nb_nebi_kernels.NebiKernelSpecManager"

# Write terminal font settings as explicit user settings at server startup.
# JupyterLab's Terminal widget has hardcoded defaultOptions that take precedence
# over overrides.json schema defaults. Writing to user-settings ensures the
# font is applied. Only writes if the file doesn't exist (preserves user edits).
import json
import pathlib
_home = pathlib.Path(os.environ.get("HOME", os.path.expanduser("~")))
_term_settings_dir = _home / ".jupyter" / "lab" / "user-settings" / "@jupyterlab" / "terminal-extension"
_term_settings_file = _term_settings_dir / "plugin.jupyterlab-settings"
if not _term_settings_file.exists():
    _term_settings_dir.mkdir(parents=True, exist_ok=True)
    _term_settings_file.write_text(json.dumps({
        "fontFamily": "'FiraCode Nerd Font', 'Fira Code', monospace",
        "fontSize": 14,
        "lineHeight": 1.2,
    }, indent=2))

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
