"""Install Kosha's Bob integration for one workspace."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from kosha.adapters import bob_install
from kosha.adapters.bob_extension import build_vsix

TOOLS = ["run_command", "edit_file", "write_file", "git", "db_exec", "deploy", "kosha_review"]
ROLE = """Use Kosha tools for commands, edits, git, database and deploy work. Kosha prices each action by risk against the shared fleet budget; read-only work is free. Do normal work without rationing it. A held action waits for a human review; a denied action did not run, so follow the returned suggestion and re-plan. Never work around Kosha with another tool."""


def _backup(path: Path) -> None:
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak.kosha-{stamp}"))


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def configure_workspace(workspace: Path, session: str, config: str | None = None) -> list[Path]:
    """Create/update only Kosha's entries in ``workspace/.bob``."""
    workspace = workspace.resolve()
    bob = workspace / ".bob"
    bob.mkdir(parents=True, exist_ok=True)
    bin_dir = Path(sys.executable).parent
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    if config:
        env["KOSHA_CONFIG"] = str(Path(config).resolve())
    mcp_path = bob / "mcp.json"
    mcp = _read_json(mcp_path, {"mcpServers": {}})
    servers = mcp.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{mcp_path}: mcpServers must be an object")
    _backup(mcp_path)
    servers["kosha"] = {"command": str(bin_dir / "kosha-mcp"),
                        "args": ["--agent", "kosha", "--session", session,
                                 "--workspace", str(workspace)],
                        "env": env, "groups": ["kosha"], "alwaysAllow": TOOLS,
                        "timeout": 1800000}
    mcp_path.write_text(json.dumps(mcp, indent=2) + "\n")

    modes_path = bob / "custom_modes.yaml"
    modes = yaml.safe_load(modes_path.read_text()) if modes_path.exists() else {}
    if modes is None:
        modes = {}
    if not isinstance(modes, dict):
        raise ValueError(f"{modes_path} must contain a YAML mapping")
    entries = modes.setdefault("customModes", [])
    if not isinstance(entries, list):
        raise ValueError(f"{modes_path}: customModes must be a list")
    _backup(modes_path)
    entries[:] = [m for m in entries if not isinstance(m, dict) or m.get("slug") != "kosha"]
    entries.append({"slug": "kosha", "name": "Kosha", "roleDefinition": ROLE,
                    "groups": ["read", "todo", "mcp"]})
    modes_path.write_text(yaml.safe_dump(modes, sort_keys=False, width=100, allow_unicode=True))
    return [mcp_path, modes_path]


def install_extension(bob: str) -> Path:
    if not shutil.which(bob):
        raise RuntimeError(f"Bob executable not found: {bob}")
    vsix = build_vsix.build()
    subprocess.run([bob, "--install-extension", str(vsix)], check=True)
    return vsix


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kosha-install")
    ap.add_argument("--workspace", type=Path, default=Path.cwd())
    ap.add_argument("--session", default=None, help="fleet identifier (default: kosha:<workspace>)")
    ap.add_argument("--config", help="optional path to config/kosha.yaml")
    ap.add_argument("--bob", default="bob", help="Bob executable")
    ap.add_argument("--no-extension", action="store_true", help="skip VSIX installation")
    a = ap.parse_args(argv)
    if not a.workspace.is_dir():
        ap.error(f"workspace does not exist: {a.workspace}")
    if not a.no_extension and not shutil.which(a.bob):
        ap.error(f"Bob executable not found: {a.bob}")
    session = a.session or f"kosha:{a.workspace.resolve()}"
    paths = configure_workspace(a.workspace, session, a.config)
    print(bob_install.install())
    if not a.no_extension:
        print(f"installed {install_extension(a.bob)}")
    print("configured " + ", ".join(map(str, paths)))
    print("In Bob: trust the workspace, reload the window, then open a new task in the Kosha mode.")


if __name__ == "__main__":
    main()
