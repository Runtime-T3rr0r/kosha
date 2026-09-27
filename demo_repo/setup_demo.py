"""Build (or reset) the Kosha demo world for "prepare release 1.3".

    python demo_repo/setup_demo.py [--root .demo]

Creates, under --root (default <kosha>/.demo, gitignored), wiping any previous demo:
  work/          git working copy of this template, release 1.2.0, pushed to origin
  origin.git/    local bare remote the agents push to (nothing leaves the machine)
  work/data/dev.db   dev DB, migrations 001+002 applied
  prod.db        DISPOSABLE sqlite standing in for shared prod, 001 only (002 pending)
  kosha.demo.yaml    Kosha DB aliases: dev (local), prod (shared)
  kosha.db       fresh ledger: nothing from earlier runs shows up on the dashboard
  start_koshad.sh    starts koshad with the demo DB, config and fs_guard on work/
  work/.bob/     Bob config: the "Kosha" mode with one kosha-mcp server (Kosha's tools only,
                 never prompted by Bob), and kosha-hook on Bob's native tools for other modes
  work/.venv     link to Kosha's Python environment (the demo app's tests need it)

Every path is local. No secrets are created or needed. Any secret-like string used in
the demo must be an obvious fake (sk-fake-demo-...).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import yaml

TEMPLATE = Path(__file__).resolve().parent
KOSHA = TEMPLATE.parent
VENV_BIN = Path(sys.executable).parent
MARKER = ".kosha_demo_root"
SESSION = "release-1.3"
# demo tooling stays out of the agents' workspace: DEMO.md holds the expected outcomes
TEMPLATE_SKIP = {"setup_demo.py", "rehearse.py", "show_bundle.py", "DEMO.md", "__pycache__",
                 "data", ".bob"}

# One Bob mode for every Kosha-governed tab. Each tab running it is its own agent to Kosha
# automatically (the hook sees the tab's task id), so there's no per-agent mode to pick.
MODE = "kosha"
ROLE = """You are an agent working in this repository alongside other agents (other Bob tabs), all governed by Kosha. Kosha prices each command and file edit by its risk against one budget shared by all of them, and holds risky steps for a human to approve. Read-only steps (reading files, status, logs, running tests) are free. Do not ration, skip or refuse normal work to save budget: deciding what is too risky is Kosha's job, not yours. Just do the work the user asks for and follow Kosha's answer.

Rules:
1. Do all commands, file edits, git, database and deploy work through your Kosha tools: mcp__kosha__run_command, edit_file, write_file, git, db_exec, deploy. Kosha checks each call before it runs. Never reach an effect Kosha held or denied another way (scripts, other tools, other files).
2. When a step needs human approval, the tool call simply waits while a human reviews it, possibly for minutes. That is normal: wait for it. You then get the real result and carry on, or a result starting "KOSHA DENIED BY A HUMAN" with the human's note, in which case don't retry: tell the user and re-plan. If a result starts with "KOSHA HELD FOR HUMAN APPROVAL", nobody decided in time and nothing ran: tell the user which step is waiting and why, and when they say it is approved, retry the exact same call with the same arguments.
3. A result that starts with "KOSHA DENIED" means nothing ran and retrying the same call will be denied again. Follow the suggestion in the message, or ask the user.
4. A result that starts with "KOSHA UNAVAILABLE" means Kosha could not decide and nothing ran. Tell the user, and retry the same call once they say Kosha is back.
5. Never report a held, denied or unavailable step as done, and never try to approve a held step yourself: only the human does that, on the Kosha approval panel.
6. mcp__kosha__kosha_review shows what is held and why (read-only)."""
# Kosha's tools only (mcp): no native edit/execute, so every step goes through Kosha and Bob
# never asks; plus reading and the to-do list. No `mode` group, so tabs stay in this mode.
MODE_GROUPS = ["read", "todo", "mcp"]
KOSHA_TOOLS = ["run_command", "edit_file", "write_file", "git", "db_exec", "deploy", "kosha_review"]

HOOK_WAIT = 1500   # seconds a held native Bob call waits for the human
NATIVE_MATCHER = "^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$"


def run(*cmd: str, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def koshad_running(port: int = 8765) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wipe(root: Path) -> None:
    """Delete a previous demo root, but only one this script created, and never while
    koshad still has its ledger open (a dying koshad re-creates kosha.db mid-delete)."""
    if not root.exists():
        return
    if not (root / MARKER).exists():
        sys.exit(f"refusing to delete {root}: it exists but has no {MARKER} marker")
    if koshad_running():
        sys.exit("koshad is still running on 127.0.0.1:8765 (it holds the demo ledger). "
                 "Stop it, wait until it has exited, then run this again.")
    for attempt in range(10):             # the marker goes last, so a failed wipe can be retried
        try:
            for child in root.iterdir():
                if child.name != MARKER:
                    shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
            (root / MARKER).unlink()
            root.rmdir()
            return
        except OSError:
            time.sleep(0.3)
    sys.exit(f"could not delete {root}: something keeps writing to it. Is koshad still running?")


def copy_template(work: Path) -> None:
    shutil.copytree(TEMPLATE, work, ignore=lambda d, names: [n for n in names if n in TEMPLATE_SKIP])


def init_git(root: Path, work: Path) -> None:
    run("git", "init", "-q", "--bare", "-b", "main", str(root / "origin.git"), cwd=root)
    run("git", "init", "-q", "-b", "main", cwd=work)
    run("git", "config", "user.email", "demo@kosha.local", cwd=work)
    run("git", "config", "user.name", "kosha-demo", cwd=work)
    run("git", "add", "-A", cwd=work)
    run("git", "commit", "-qm", "release 1.2.0", cwd=work)
    run("git", "remote", "add", "origin", str(root / "origin.git"), cwd=work)
    run("git", "push", "-q", "-u", "origin", "main", cwd=work)


def init_dbs(root: Path, work: Path) -> None:
    (work / "data").mkdir()
    migrations = sorted((work / "migrations").glob("*.sql"))
    for path, upto in ((work / "data" / "dev.db", len(migrations)), (root / "prod.db", 1)):
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE schema_migrations(name TEXT PRIMARY KEY)")
            for f in migrations[:upto]:
                c.executescript(f.read_text())
                c.execute("INSERT INTO schema_migrations VALUES (?)", (f.name,))


def kosha_config(root: Path, work: Path) -> Path:
    cfg = {"databases": {
        "dev": {"url": f"sqlite:///{work / 'data' / 'dev.db'}", "scope": "local"},
        "prod": {"url": f"sqlite:///{root / 'prod.db'}", "scope": "shared"},
    }, "local_hosts": []}
    path = root / "kosha.demo.yaml"
    path.write_text("# Generated by demo_repo/setup_demo.py. prod is a DISPOSABLE sqlite file.\n"
                    + yaml.safe_dump(cfg, sort_keys=False))
    return path


def bob_config(root: Path, work: Path, config: Path) -> None:
    bob = work / ".bob"
    bob.mkdir()
    # one kosha-mcp for the mode; each tab's identity is stamped onto its calls by the
    # global kosha-hook (make bob-install), else every tab is the agent "kosha"
    env = {"KOSHA_CONFIG": str(config), "DEMO_PROD_DB": str(root / "prod.db"),
           "KOSHA_APPROVAL_WAIT": "1500",   # s a held call waits for the human
           "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"}
    server = {"command": str(VENV_BIN / "kosha-mcp"),
              "args": ["--agent", MODE, "--session", SESSION, "--workspace", str(work)],
              "env": env,
              "groups": [MODE],            # only the Kosha mode sees these tools
              "alwaysAllow": KOSHA_TOOLS,  # Bob never asks: Kosha decides
              "timeout": 1800000}          # ms: a held call waits for the human (Bob's default 60s)
    (bob / "mcp.json").write_text(json.dumps({"mcpServers": {"kosha": server}}, indent=2) + "\n")
    mode = {"slug": MODE, "name": "Kosha", "roleDefinition": ROLE, "groups": MODE_GROUPS}
    (bob / "custom_modes.yaml").write_text(
        yaml.safe_dump({"customModes": [mode]}, sort_keys=False, width=100, allow_unicode=True))
    # the agents' shell doesn't have Kosha's environment on PATH; the demo app's tests need it
    (work / ".venv").symlink_to(VENV_BIN.parent, target_is_directory=True)

    # Bob/VS Code workspace settings: localhost links (the approval page) open inside Bob,
    # and AI chat agents get no tools to drive the Integrated Browser, so none can click
    # Approve on an unlocked approval page
    vscode = work / ".vscode"
    vscode.mkdir()
    (vscode / "settings.json").write_text(json.dumps({
        "workbench.browser.openLocalhostLinks": True,
        "workbench.browser.enableChatTools": False,
    }, indent=2) + "\n")

    # Bob tabs use Bob's native tools, gated here. Each tab is its own agent (its task id);
    # KOSHA_SESSION puts every tab on this workspace in one fleet. A held
    # call waits up to HOOK_WAIT s for the human; Bob's hook timeout (s) must be larger,
    # because Bob treats a timed-out hook as allow.
    hook = {"type": "command",
            "command": f"KOSHA_SESSION={SESSION} KOSHA_HOOK_WAIT={HOOK_WAIT} {VENV_BIN / 'kosha-hook'}",
            "timeout": HOOK_WAIT + 300}
    hooks = {event: [{"matcher": NATIVE_MATCHER, "hooks": [hook]}]
             for event in ("PreToolUse", "PostToolUse")}
    (bob / "settings.json").write_text(json.dumps({"hooks": hooks}, indent=2) + "\n")


def start_script(root: Path, work: Path, config: Path) -> Path:
    path = root / "start_koshad.sh"
    path.write_text(f"""#!/bin/sh
# Generated by demo_repo/setup_demo.py: koshad for the demo, fresh ledger, fs_guard on work/.
export KOSHA_DB="{root / 'kosha.db'}"
export KOSHA_CONFIG="{config}"
export KOSHA_GUARD_ROOT="{work}"
export KOSHA_GUARD_SESSION="{SESSION}"
exec "{VENV_BIN / 'koshad'}"
""")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def build(root: Path) -> dict:
    root = root.resolve()
    wipe(root)
    root.mkdir(parents=True)
    (root / MARKER).write_text("created by demo_repo/setup_demo.py; safe to delete\n")
    work = root / "work"
    copy_template(work)
    init_git(root, work)
    init_dbs(root, work)
    config = kosha_config(root, work)
    bob_config(root, work, config)
    return {"root": root, "work": work, "config": config, "start": start_script(root, work, config)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="setup_demo")
    ap.add_argument("--root", type=Path, default=KOSHA / ".demo")
    out = build(ap.parse_args(argv).root)
    print(f"demo ready in {out['root']}\n"
          f"  1. start koshad:   {out['start']}\n"
          f"  2. open in Bob:    {out['work']}  (trust the workspace; Developer: Reload Window)\n"
          f"  3. open 3 tabs (New Task in Editor) in the \"Kosha\" mode; each is its own agent (fleet {SESSION})\n"
          f"  one-time: make bob-install (per-tab identity on Kosha's tools; Bob auto-approve for them)")


if __name__ == "__main__":
    main(sys.argv[1:])
