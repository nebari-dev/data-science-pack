#!/usr/bin/env python3
"""Fail if any installed JupyterLab extension requires a newer version of a
shared ``@jupyterlab/*`` package than the installed JupyterLab provides.

Prebuilt extensions are webpack module-federation bundles. At build time
webpack bakes the *resolved* version of every shared package into
``static/remoteEntry.<hash>.js`` as a required version, e.g.
``w("default","@jupyterlab/application",!1,[1,4,7,0,,"alpha",0])``; at runtime
JupyterLab compares that against the version its core bundle provides
(``{name:"@jupyterlab/application",version:"4.6.2",...}`` in
``share/jupyter/lab/static/*.js``) and logs ``Unsatisfied version 4.6.2 from
<ext> of shared singleton module @jupyterlab/application (required
^4.7.0-alpha.0)`` for every mismatch. The ranges in an extension's
``package.json`` (usually ``^4.0.0``) say nothing about this, so the bundles
are the only reliable thing to check. This is what nebari-dev/data-science-pack
issue #239 is about.

webpack encodes a required version as a JSON array: ``[op, major, minor,
patch]`` optionally followed by ``,,"tag",n`` for a prerelease. ``op`` 0 is
``=``, 1 is ``^``, 2 is ``~``.

Run inside the built image: ``python /opt/scripts/check-labextension-versions.py``.
Exits non-zero (with ``::error::`` annotations) on any mismatch.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import version
from pathlib import Path

JUPYTER_SHARE = Path(sys.prefix) / "share" / "jupyter"
CORE_STATIC = JUPYTER_SHARE / "lab" / "static"
LABEXTENSIONS = JUPYTER_SHARE / "labextensions"

# {name:"@jupyterlab/apputils",version:"4.7.2", ...} in the core bundle.
PROVIDED_RE = re.compile(r'\{name:"([^"]+)",version:"([^"]+)"')
# w("default","@jupyterlab/apputils",!1,[1,4,7,0,,"alpha",0]) in an extension
# bundle; the boolean/number argument between name and version is optional.
REQUIRED_RE = re.compile(
    r'"([^"]+)",(?:(?:!0|!1|\d+),)?\[(\d),(\d+),(\d+),(\d+)(?:,,"([a-z]+)",(\d+))?\]'
)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")


def provided_versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for js in CORE_STATIC.glob("*.js"):
        for name, ver in PROVIDED_RE.findall(js.read_text(encoding="utf8", errors="ignore")):
            found.setdefault(name, ver)
    return found


def satisfies(installed: str, op: int, req: tuple[int, int, int]) -> bool | None:
    """True/False, or None if the installed version can't be parsed."""
    m = SEMVER_RE.match(installed)
    if not m:
        return None
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    if pre:  # an installed prerelease needs real semver; don't guess
        return None
    have = (major, minor, patch)
    if op == 0:  # =
        return have == req
    if op == 1:  # ^  (a prerelease lower bound is satisfied by any stable >= it)
        return major == req[0] and have >= req
    if op == 2:  # ~
        return (major, minor) == req[:2] and patch >= req[2]
    return None


def main() -> int:
    lab = version("jupyterlab")
    provided = provided_versions()
    if not provided:
        print(f"::error::could not read provided package versions from {CORE_STATIC}")
        return 1
    if not LABEXTENSIONS.is_dir():
        print(f"::error::no labextensions directory at {LABEXTENSIONS}")
        return 1

    problems: list[str] = []
    skipped: list[str] = []
    bundles = sorted(LABEXTENSIONS.glob("**/static/remoteEntry.*.js"))
    for entry in bundles:
        ext = str(entry.parent.parent.relative_to(LABEXTENSIONS))
        src = entry.read_text(encoding="utf8", errors="ignore")
        for name, op, major, minor, patch, tag, tag_n in REQUIRED_RE.findall(src):
            if not name.startswith("@jupyterlab/"):
                continue  # only the lab's own packages are versioned with it
            installed = provided.get(name)
            if installed is None:
                continue  # not a core singleton; the extension bundles its own copy
            req = (int(major), int(minor), int(patch))
            ok = satisfies(installed, int(op), req)
            pretty = ("=", "^", "~")[int(op)] if int(op) < 3 else f"op{op} "
            pretty += ".".join(map(str, req)) + (f"-{tag}.{tag_n}" if tag else "")
            if ok is None:
                skipped.append(f"{ext}: {name} requires {pretty}, installed {installed}")
            elif not ok:
                problems.append(f"{ext}: {name} requires {pretty}, installed {installed}")

    print(f"jupyterlab {lab}: checked {len(bundles)} extension bundle(s) "
          f"against {len(provided)} core packages")
    for line in skipped:
        print(f"::warning::could not evaluate: {line}")
    if problems:
        for line in sorted(set(problems)):
            print(f"::error::{line}")
        print("These extensions will log 'Unsatisfied version' warnings in the browser; "
              "pin them to a release built against the installed JupyterLab (#239).")
        return 1
    print("OK: every extension's shared @jupyterlab/* requirements are satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
