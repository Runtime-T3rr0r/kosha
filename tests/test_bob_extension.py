"""The Kosha Bob extension: its Node tests, its packaging, and its core against a real
koshad (a hold that happens while it's watching pops up; one from before doesn't)."""
import json
import shutil
import subprocess
import sys
import xml.dom.minidom
import zipfile
from pathlib import Path

import pytest
import requests

EXT = Path(__file__).resolve().parents[1] / "kosha" / "adapters" / "bob_extension"
sys.path.insert(0, str(EXT))
import build_vsix  # noqa: E402

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_extension_node_tests_pass():
    tests = sorted(str(p) for p in (EXT / "test").glob("*.test.js"))
    p = subprocess.run(["node", "--test", *tests], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    assert "# fail 0" in p.stdout or "ℹ fail 0" in p.stdout


def test_vsix_structure(tmp_path):
    vsix = build_vsix.build(tmp_path)
    pkg = json.loads((EXT / "package.json").read_text())
    assert vsix.name == f"{pkg['name']}-{pkg['version']}.vsix"
    with zipfile.ZipFile(vsix) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "extension.vsixmanifest", "extension/package.json",
                "extension/extension.js", "extension/core.js"} <= names
        assert not any("/test/" in n or n.endswith(".test.js") for n in names)      # tests don't ship
        assert json.loads(z.read("extension/package.json")) == pkg
        m = xml.dom.minidom.parseString(z.read("extension.vsixmanifest"))
        ident = m.getElementsByTagName("Identity")[0]
        assert (ident.getAttribute("Id"), ident.getAttribute("Version"), ident.getAttribute("Publisher")) == \
            (pkg["name"], pkg["version"], pkg["publisher"])
        xml.dom.minidom.parseString(z.read("[Content_Types].xml"))


def test_manifest_contract():
    pkg = json.loads((EXT / "package.json").read_text())
    assert pkg["main"] == "./extension.js" and "onStartupFinished" in pkg["activationEvents"]
    assert [c["command"] for c in pkg["contributes"]["commands"]] == ["kosha.openApprovals"]
    props = pkg["contributes"]["configuration"]["properties"]
    assert props["kosha.koshadUrl"]["default"] == "http://127.0.0.1:8765" and props["kosha.autoOpen"]["default"] is True
    assert not pkg.get("dependencies")                    # nothing to npm install


WATCH = r"""
const { Monitor, describeHeld } = require(process.argv[2]);
const m = new Monitor(process.argv[3], {
  onHeld: (p) => console.log("POPUP " + describeHeld(p)),
  onStatus: (s) => console.log("STATUS " + JSON.stringify(s)),
  log() {},
});
m.start();
process.stdin.on("data", () => {});
setTimeout(() => { m.stop(); process.exit(0); }, Number(process.argv[4]));
"""


@needs_node
def test_core_against_a_real_koshad(live_koshad, tmp_path):
    def decide(aid, agent, argv):
        body = {"action_id": aid, "session_id": "release-1.3", "agent_id": agent, "harness": "bob",
                "tool": "run_command", "raw": {"command": " ".join(argv)}, "argv": argv, "cwd": "/tmp",
                "targets": [], "ts": ""}
        return requests.post(f"{live_koshad.url}/decide", json=body, timeout=5).json()["decision"]

    assert decide("old", "old-agent", ["chmod", "777", "old.sh"]) == "ask"          # before the watcher
    script = tmp_path / "watch.js"
    script.write_text(WATCH)
    watcher = subprocess.Popen(["node", str(script), str(EXT / "core.js"), live_koshad.url, "6000"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    import time
    time.sleep(2.0)                                                                  # baseline + stream open
    assert decide("a1", "release-bump", ["git", "push", "origin", "main"]) == "allow"
    assert decide("a2", "test-fix", ["git", "push", "origin", "main"]) == "ask"      # convergence
    out, err = watcher.communicate(timeout=20)
    popups = [ln for ln in out.splitlines() if ln.startswith("POPUP ")]
    assert popups == ["POPUP Kosha held test-fix: git push origin main (convergence)"], out + err
    statuses = [json.loads(ln[7:]) for ln in out.splitlines() if ln.startswith("STATUS ")]
    assert any(s.get("connected") and s.get("held") == 2 for s in statuses), statuses
