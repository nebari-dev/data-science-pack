#!/usr/bin/env python3
# Copyright (c) Nebari Development Team.
# Distributed under the terms of the Modified BSD License.
"""Package a VS Code extension directory as a .vsix, without vsce.

A .vsix is a zip containing extension.vsixmanifest, [Content_Types].xml
and the extension files under extension/. `code-server
--install-extension` reads the manifest Identity plus
extension/package.json. Used at image build time because the build
environment has no node/npm toolchain.

Usage: build-vsix.py <extension-src-dir> <dest.vsix>
"""

import json
import sys
import zipfile
from pathlib import Path

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}"/>
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Categories>Other</Categories>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
</Types>
"""


def main(src, dest):
    src, dest = Path(src), Path(dest)
    pkg = json.loads((src / "package.json").read_text())
    manifest = MANIFEST.format(
        name=pkg["name"],
        version=pkg["version"],
        publisher=pkg["publisher"],
        display=pkg.get("displayName", pkg["name"]),
        description=pkg.get("description", ""),
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("extension.vsixmanifest", manifest)
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, "extension/" + str(f.relative_to(src)))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
