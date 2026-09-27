"""Package the Kosha Bob extension as a .vsix, with no network and no vsce.

    python kosha/adapters/bob_extension/build_vsix.py      # -> dist/kosha-bob-<version>.vsix
    bob --install-extension dist/kosha-bob-<version>.vsix

A .vsix is a zip: [Content_Types].xml, extension.vsixmanifest, and the extension under
extension/. Only the runtime files ship (no tests).
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# dist/ in a source checkout; installed from a wheel, ROOT is site-packages, so build
# into a temp dir instead of writing there
OUT_DIR = ROOT / "dist" if (ROOT / "pyproject.toml").exists() else Path(tempfile.gettempdir()) / "kosha-bob"
SHIP = ["package.json", "extension.js", "core.js", "README.md"]

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension=".json" ContentType="application/json"/>\
<Default Extension=".js" ContentType="application/javascript"/>\
<Default Extension=".md" ContentType="text/markdown"/>\
<Default Extension=".vsixmanifest" ContentType="text/xml"/></Types>
"""


def manifest(pkg: dict) -> str:
    e = lambda k: escape(str(pkg.get(k, "")))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" \
xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{e('name')}" Version="{e('version')}" Publisher="{e('publisher')}" />
    <DisplayName>{e('displayName')}</DisplayName>
    <Description xml:space="preserve">{e('description')}</Description>
    <Categories>{escape(",".join(pkg.get("categories", [])))}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{escape(pkg['engines']['vscode'])}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace" />
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value="" />
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
  </Assets>
</PackageManifest>
"""


def build(out_dir: Path = OUT_DIR) -> Path:
    pkg = json.loads((HERE / "package.json").read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{pkg['name']}-{pkg['version']}.vsix"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("extension.vsixmanifest", manifest(pkg))
        for name in SHIP:
            z.write(HERE / name, f"extension/{name}")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR))
