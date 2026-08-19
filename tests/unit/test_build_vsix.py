"""build-vsix.py packages an extension dir into a code-server-installable
.vsix (zip with manifest) without needing node/vsce in the image build."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "images" / "scripts" / "build-vsix.py"


def _build(tmp_path: Path) -> Path:
    src = tmp_path / "ext"
    src.mkdir()
    (src / "package.json").write_text(json.dumps({
        "name": "nebari-activity-reporter",
        "publisher": "nebari",
        "version": "0.1.0",
        "displayName": "Nebari Activity Reporter",
        "description": "test fixture",
    }))
    (src / "extension.js").write_text("module.exports = {};\n")
    dest = tmp_path / "out" / "ext.vsix"

    spec = importlib.util.spec_from_file_location("_build_vsix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(str(src), str(dest))
    return dest


def test_vsix_layout(tmp_path):
    dest = _build(tmp_path)
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
        assert "extension.vsixmanifest" in names
        assert "[Content_Types].xml" in names
        assert "extension/package.json" in names
        assert "extension/extension.js" in names


def test_vsix_manifest_identity(tmp_path):
    dest = _build(tmp_path)
    with zipfile.ZipFile(dest) as z:
        manifest = z.read("extension.vsixmanifest").decode()
    assert 'Id="nebari-activity-reporter"' in manifest
    assert 'Publisher="nebari"' in manifest
    assert 'Version="0.1.0"' in manifest
