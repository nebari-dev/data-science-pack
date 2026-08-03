---
title: Nebi in JupyterLab
description: Common workflows for using Nebi from inside JupyterLab — your own environments and team-shared ones.
---

Every JupyterLab session on this pack ships the `nebi` CLI, pre-authenticated
to your organization's Nebi server. You shouldn't need to run `nebi login` —
JupyterHub already knows who you are, so `nebi` does too, from the first
terminal you open. (If the hub's token exchange with Nebi ever fails, `nebi`
falls back to asking you to log in — a deployment issue, not something wrong
with your workspace.) This page walks through the workflows people actually
run day to day.

There are two kinds of workspace you'll deal with:

- **Your own** — live only on your notebook server's persistent storage.
  Nobody else can see them until you choose to publish one.
- **Team-shared** — pulled from (or pushed to) your organization's Nebi
  server, so a teammate can use the exact same environment you built.

## Start a new environment for a project

Open a terminal in JupyterLab, create a project directory, and register it
with Nebi:

```bash
mkdir my-project && cd my-project
nebi init
```

This creates a `pixi.toml` in the directory and tracks it as a workspace.
Add packages the normal Pixi way — this is what produces `pixi.lock`:

```bash
pixi add numpy pandas scikit-learn
```

Add `ipykernel` too if you want to open notebooks against this environment
directly from JupyterLab's kernel picker (see the next section) rather than
only from a terminal:

```bash
pixi add ipykernel
```

Use the workspace from a terminal or a script:

```bash
nebi shell my-project     # drop into an interactive shell with the env active
nebi run my-project train.py   # or run one command without an interactive shell
```

Check what you have and where it lives at any point:

```bash
nebi status
nebi workspace list
```

Nothing here touches the team server — this environment is yours alone
until you decide to share it.

## Open a notebook against a Nebi workspace

Any tracked workspace with an installed environment shows up directly in
JupyterLab's kernel picker — no need to drop to a terminal first. Open
**File → New → Notebook** (or **Change Kernel** on an existing one) and look
for it by name:

- `my-project` — if the workspace only has a single (default) Pixi
  environment.
- `my-project (gpu)` — the environment name is appended when a workspace
  defines more than one, e.g. a `pixi.toml` with `[environments] gpu = ...`.

This works for team-published workspaces too, once you've pulled and
installed them (see the next section) — local and remote workspaces show
up in the same picker.

If you pick a kernel for a workspace that hasn't been installed yet (or is
remote and hasn't been pulled locally), the notebook still opens — the
first cell shows exactly which command to run (`nebi pull ...` or
`nebi workspace install ...`) instead of failing silently. If the
environment is installed but has no Jupyter kernel package in it, the same
happens — run `pixi add ipykernel` inside the workspace and reopen it.

## Share your environment with your team

Once your environment is working, publish it so a teammate can pull the
exact same thing rather than re-solving dependencies from scratch:

```bash
nebi push my-project
```

Tag a specific milestone (e.g. before a risky dependency bump) instead of
overwriting the latest version:

```bash
nebi push my-project:v1.0
```

Your teammate, on their own notebook server, pulls it:

```bash
nebi pull my-project:v1.0
nebi workspace install my-project   # materialize the environment on disk
```

Because your JupyterHub login already carries your identity, `nebi push`
and `nebi pull` work the moment you open a terminal — no separate sign-in,
no server URL to type in.

## Use a team-standard environment someone else published

Rather than building an environment from scratch, browse what your
organization already publishes:

```bash
nebi workspace list --remote
```

Pull and install the one you need:

```bash
nebi pull team-baseline:latest
nebi workspace install team-baseline
nebi shell team-baseline
```

If your organization's Nebi server is reachable at all, this works for any
environment you have at least read access to — you don't need edit rights
to use someone else's published environment, only to change it.

## See what changed between two versions

Before pulling an update, or before overwriting your own environment, check
what actually changed:

```bash
nebi diff my-project:v1.0 my-project:v1.1
nebi workspace tags my-project   # see every published version
```

`nebi diff` compares the manifests (and, with `--lock`, the resolved
lockfiles) so you can see exactly which packages moved before you commit to
an update.

## Clean up a workspace you no longer need

Free disk space without losing your work, or stop tracking something
entirely:

```bash
nebi workspace uninstall my-project   # remove .pixi/envs, keep pixi.toml/pixi.lock
nebi workspace remove my-project      # stop tracking it locally (files untouched)
nebi workspace remove my-project --remote   # delete it from the team server (owner only)
```

`uninstall` is the one to reach for when a large environment is just eating
disk space — `nebi workspace install my-project` brings it straight back
from the lockfile. `remove` only forgets the workspace locally; your
project directory and its files are never touched. If you've moved or
deleted project directories outside of `nebi` and just want the stale
tracking entries gone, `nebi workspace prune` removes every tracked
workspace whose directory no longer exists.

## Use a Nebi environment for a deployed app, without touching a terminal

If you're deploying a Streamlit app, a Panel dashboard, or a custom command
through the **jhub-apps** launcher, you can point it at any Nebi workspace
you have access to instead of the base JupyterLab image:

1. Open the app launcher and start creating a new app.
2. In the environment picker, choose your (or your team's) Nebi workspace —
   it's listed as `<owner>/<workspace-name>`, alongside your own local
   workspaces.
3. Deploy. The app runs inside that exact environment — same packages, same
   versions your notebook uses.

This is the fastest way to get a teammate's environment running behind a
shareable app URL without either of you touching the CLI.

## Browse and manage everything visually

If you'd rather click than type, there are two places to reach the Nebi web
UI — same account, no extra sign-in either way.

**From inside a running notebook server:** open the JupyterLab **Launcher**
tab (the screen with the Notebook/Console/Terminal tiles) and click the
**Nebi** tile. It opens in the current browser tab, proxied through your own
notebook server, and shows both your local workspaces and your team's.

**Without starting a notebook server at all:** the jhub-apps launcher (the
page you land on after logging into JupyterHub) has a pinned **Nebi** card
that opens your organization's Nebi server directly in a new tab.

From either one you can:

- Browse every workspace you have access to, and who else has access.
- Grant or revoke access for a teammate on a workspace you own.
- See a workspace's version history, view a version's manifest/lockfile,
  and roll back to an older one.

Comparing two versions' manifests (`nebi diff`) is still CLI-only — the web
UI doesn't have a diff view yet.

## Quick reference

| I want to... | Command |
|---|---|
| Start tracking a new project | `nebi init` |
| See my local workspaces | `nebi workspace list` |
| See workspaces on the team server | `nebi workspace list --remote` |
| Activate an environment in this shell | `nebi shell <workspace>` |
| Run one command in an environment | `nebi run <workspace> <command>` |
| Publish my environment for others | `nebi push <workspace>[:tag]` |
| Pull someone else's published environment | `nebi pull <workspace>[:tag]` |
| Install a pulled workspace's environment on disk | `nebi workspace install <workspace>` |
| Compare two versions | `nebi diff <ref-a> <ref-b>` |
| Check what I'm running and where it came from | `nebi status` |
| Free disk space, keep the manifest/lock | `nebi workspace uninstall <workspace>` |
| Stop tracking a workspace locally | `nebi workspace remove <workspace>` |
| Delete a workspace from the team server | `nebi workspace remove <workspace> --remote` |
| Drop tracking entries for deleted directories | `nebi workspace prune` |

If a command comes back with an authentication or connection error instead
of the expected result, that's a deployment-level issue (the notebook
server's connection to the Nebi server), not something fixable from the
CLI — ask whoever manages your Nebari deployment to check it.
