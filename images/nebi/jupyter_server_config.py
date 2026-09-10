import glob
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
        # jupyter-server-proxy forwards the browser's original Host and
        # Origin headers (e.g. hub.example.com) unchanged to the proxied
        # backend. nebi's local-mode listener only accepts loopback Host
        # and, when present, loopback Origin headers (netguard). Host
        # alone isn't enough: the initial page load has no Origin header
        # and passes, but every fetch/XHR the Nebi frontend then makes
        # DOES send Origin, so without this override those calls 403 and
        # the app renders blank after the shell loads. Force both to
        # loopback values.
        "request_headers_override": {
            "Host": "localhost:{port}",
            "Origin": "http://localhost:{port}",
        },
        "launcher_entry": {
            "title": "Nebi",
            "enabled": True,
            "icon_path": ICON_PATH,
        },
    }
}

# jupyter-server-proxy configuration for VS Code (code-server).
# Registered here instead of via the jupyter-vscode-proxy package so the
# entry can set update_last_activity=False: with it True (the packaged
# default), the VS Code browser client's websocket keepalives count as
# jupyter API activity, and the in-pod shutdown_no_activity_timeout — the
# mechanism that actually culls idle-tab pods — never fires while a tab is
# open (https://github.com/nebari-dev/data-science-pack/issues/208). (The
# hub-level idle culler is defeated separately and regardless of this
# setting: configurable-http-proxy counts websocket data as route
# activity.) Real user interaction is reported instead by the bundled
# nebari-activity-reporter extension (see
# images/jupyterlab/vscode-activity-reporter/).
VSCODE_ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "icons",
    "code-server.svg",
)


def _vscode_command():
    # Mirrors the command jupyter-vscode-proxy generated (minus unix-socket
    # support, which nothing here used). {port} is templated by
    # jupyter-server-proxy at launch.
    cmd = ["code-server", "--auth", "none", "--disable-telemetry", "--port={port}"]
    extensions_dir = os.environ.get("CODE_EXTENSIONSDIR")
    if extensions_dir:
        cmd += ["--extensions-dir", extensions_dir]
    cmd.append(os.environ.get("CODE_WORKINGDIR", "."))
    return cmd


def _vscode_reporter_installed():
    # Shared fate with the keep-alive channel: the chart env var (set
    # reliably by the spawner) and the reporter vsix install (per-pod
    # postStart under `|| true`) live in separate failure domains, so a
    # pod can otherwise land with proxy activity disabled AND no reporter
    # — and cull an actively-working user at shutdown_no_activity_timeout.
    # Gate on the installed artifact so a failed install degrades to
    # over-spending (proxied traffic counts as activity again) instead.
    # Caveat: postStart runs concurrently with the container entrypoint,
    # so on a user's first-ever spawn the install may not have finished
    # when this file is evaluated; that session over-spends, and the
    # PVC-backed extensions dir makes every later spawn see the artifact.
    ext_dir = os.environ.get("CODE_EXTENSIONSDIR") or os.path.expanduser(
        "~/.local/share/code-server/extensions"
    )
    return bool(
        glob.glob(os.path.join(ext_dir, "nebari.nebari-activity-reporter-*"))
    )


# Fail-safe default: absent/empty means the OLD behavior (count proxied
# traffic as activity) applies. The chart actively opts pods into the new
# interaction-based behavior by setting this env var to "false" when
# vscodeActivity.enabled is true (config/jupyterhub/01-spawner.py). This
# polarity means chart/image skew (e.g. a newer image tag paired with an
# older chart release that doesn't yet set the env var) degrades to the
# safe failure mode (pods over-spend on proxied traffic staying "active")
# rather than the dangerous one (culling active VS Code users who have no
# activity-reporter extension installed to keep them alive). The same
# polarity covers per-pod install failure via _vscode_reporter_installed().
_value = os.environ.get("VSCODE_PROXY_UPDATE_LAST_ACTIVITY", "").strip().lower()
_vscode_count_proxy_traffic = (
    _value not in ("0", "false", "no") or not _vscode_reporter_installed()
)

c.ServerProxy.servers["vscode"] = {
    "command": _vscode_command(),
    "timeout": 300,
    "new_browser_tab": True,
    "update_last_activity": _vscode_count_proxy_traffic,
    "launcher_entry": {
        "title": "VS Code",
        "enabled": True,
        "icon_path": VSCODE_ICON_PATH,
    },
}
