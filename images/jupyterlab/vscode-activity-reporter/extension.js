"use strict";
// Reports real user interaction to the local Jupyter server.
//
// The vscode proxy route runs with update_last_activity=False (see
// images/nebi/jupyter_server_config.py), so VS Code traffic no longer
// counts as jupyter activity. Without this extension, actively working
// VS Code users would be idle-culled — this is the load-bearing half of
// nebari-dev/data-science-pack#208.
//
// Endpoint choice: /api/status and /api/ set _track_activity=False
// upstream (so pollers don't defeat culling); /api/contents/ is tracked.
const vscode = require("vscode");
const http = require("http");

const PING_INTERVAL_MS = 60 * 1000;

let lastPingMs = 0;
let busyExecutions = 0; // in-flight terminal shell executions
let output;

function pingUrl() {
  let base = process.env.JUPYTERHUB_SERVICE_URL;
  if (!base) {
    return null; // not running under JupyterHub — nothing to report to
  }
  try {
    // Normalize IPv6 any-host (::) to bracketed form before URL parsing
    base = base.replace("://:", "://[::]");
    const url = new URL(base);
    if (url.hostname === "0.0.0.0" || url.hostname === "[::]") {
      url.hostname = "127.0.0.1";
    }
    url.pathname = url.pathname.replace(/\/?$/, "/") + "api/contents/";
    url.search = "?content=0";
    return url;
  } catch (e) {
    output.appendLine(`bad JUPYTERHUB_SERVICE_URL: ${e.message}`);
    return null;
  }
}

function ping(reason) {
  const url = pingUrl();
  const token = process.env.JUPYTERHUB_API_TOKEN;
  if (!url || !token) {
    return;
  }
  try {
    const req = http.get(
      url,
      { headers: { Authorization: `token ${token}` } },
      (res) => {
        res.resume(); // drain — only the request itself matters
        if (res.statusCode < 200 || res.statusCode >= 300) {
          output.appendLine(`activity ping (${reason}): HTTP ${res.statusCode}`);
        }
      },
    );
    req.on("error", (e) => {
      // Never throw out of an event handler; next interaction retries.
      output.appendLine(`activity ping (${reason}) failed: ${e.message}`);
    });
  } catch (e) {
    output.appendLine(`activity ping (${reason}) failed: ${e.message}`);
  }
}

function recordActivity(reason) {
  const now = Date.now();
  if (now - lastPingMs < PING_INTERVAL_MS) {
    return;
  }
  lastPingMs = now;
  ping(reason);
}

function activate(context) {
  output = vscode.window.createOutputChannel("Nebari Activity Reporter");
  output.appendLine("activated");

  const on = (event, reason) => {
    context.subscriptions.push(event(() => recordActivity(reason)));
  };
  on(vscode.workspace.onDidChangeTextDocument, "edit");
  on(vscode.window.onDidChangeTextEditorSelection, "selection");
  on(vscode.window.onDidChangeTextEditorVisibleRanges, "scroll");
  on(vscode.window.onDidChangeWindowState, "focus");
  on(vscode.window.onDidOpenTerminal, "terminal-open");
  on(vscode.window.onDidCloseTerminal, "terminal-close");

  // Busy = active: a running terminal command keeps the pod alive, like
  // cullBusy=false does for kernels. Requires shell integration (auto-
  // injected for bash/zsh). Guarded: API is stable since 1.93 but cheap
  // to feature-detect.
  if (vscode.window.onDidStartTerminalShellExecution) {
    context.subscriptions.push(
      vscode.window.onDidStartTerminalShellExecution(() => {
        busyExecutions += 1;
        recordActivity("exec-start");
      }),
    );
    context.subscriptions.push(
      vscode.window.onDidEndTerminalShellExecution(() => {
        busyExecutions = Math.max(0, busyExecutions - 1);
        recordActivity("exec-end");
      }),
    );
  }

  const busyTimer = setInterval(() => {
    if (busyExecutions > 0) {
      recordActivity("busy");
    }
  }, PING_INTERVAL_MS);
  context.subscriptions.push({ dispose: () => clearInterval(busyTimer) });

  // A user just opened/reconnected VS Code — that is activity.
  recordActivity("startup");
}

function deactivate() {}

module.exports = { activate, deactivate };
