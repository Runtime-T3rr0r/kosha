import json

import yaml

from kosha.adapters.install import TOOLS, configure_workspace


def test_workspace_installer_preserves_other_bob_entries_and_is_idempotent(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr("sys.executable", str(bin_dir / "python"))
    bob = tmp_path / ".bob"
    bob.mkdir()
    (bob / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
    (bob / "custom_modes.yaml").write_text(yaml.safe_dump({"customModes": [{"slug": "other"}]}))
    configure_workspace(tmp_path, "fleet-a", "/tmp/kosha.yaml")
    configure_workspace(tmp_path, "fleet-a", "/tmp/kosha.yaml")
    mcp = json.loads((bob / "mcp.json").read_text())["mcpServers"]
    assert mcp["other"] == {"command": "other"}
    assert mcp["kosha"]["args"][-2:] == ["--workspace", str(tmp_path.resolve())]
    assert mcp["kosha"]["alwaysAllow"] == TOOLS
    modes = yaml.safe_load((bob / "custom_modes.yaml").read_text())["customModes"]
    assert [m["slug"] for m in modes] == ["other", "kosha"]
    assert len(list(bob.glob("*.bak.kosha-*"))) >= 2
