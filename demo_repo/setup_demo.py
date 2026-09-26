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
  work/.bob/     Bob config: one kosha-mcp per fleet mode (groups-pinned), the fleet
                 modes (no native edit/execute: layer 1), kosha-hook on native tools
                 (layer 2, for any other mode opened in this workspace)

Every path is local. No secrets are created or needed. Any secret-like string used in
the demo must be an obvious fake (sk-fake-demo-...).
"""
from __future__ import annotations

import argparse
import json
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
TEMPLATE_SKIP = {"setup_demo.py", "__pycache__", "data", ".bob"}

# slug -> the part of "prepare release 1.3" this agent (one Bob task in this mode) owns
FLEET = {
    "release-bump": "Bump the version to 1.3.0, update CHANGELOG.md, commit, and push to origin.",
    "test-fix": "Make the test suite pass (tests/test_app.py has a flaky test) and keep CI "
                "(.github/workflows/ci.yml) green.",
    "migrate-deploy": "Apply pending database migrations to prod (python manage.py migrate --db "
                      "prod) and deploy the release to prod with the deploy tool.",
}

ROLE = """You are {slug}, one agent in a fleet preparing release 1.3 of this service. Your part: {task}

Your actions are governed by Kosha. Kosha prices each command, file edit, git, database and deploy step by its risk against one budget shared by the whole fleet, and holds risky steps for a human to approve. Read-only steps (status, logs, reading files, running tests) are free. Do not ration, skip or refuse normal work to save budget: deciding what is too risky is Kosha's job, not yours. Just do the work the user asks for, through your Kosha tools, and follow Kosha's answer.

Rules:
1. Do all commands, file edits, git, database and deploy work through your Kosha tools: mcp__kosha-{slug}__run_command, edit_file, write_file, git, db_exec, deploy. Never reach the same effect another way (scripts, other tools, switching modes).
2. A Kosha tool result that starts with "KOSHA HELD FOR HUMAN APPROVAL" means nothing ran and a human must approve it. Stop, tell the user which call is waiting and the reason Kosha gave, and wait. When the user says it is approved, retry the exact same call with the same arguments. Do not give up on the step and do not work around it.
3. A result that starts with "KOSHA DENIED" means nothing ran and retrying the same call will be denied again. Follow the suggestion in the message, or ask the user.
4. A result that starts with "KOSHA UNAVAILABLE" means Kosha could not decide and nothing ran. Tell the user, and retry the same call once they say Kosha is back.
5. Never report a held, denied or unavailable step as done."""

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
    env = {"KOSHA_CONFIG": str(config), "DEMO_PROD_DB": str(root / "prod.db")}
    servers = {f"kosha-{slug}": {
        "command": str(VENV_BIN / "kosha-mcp"),
        "args": ["--agent", slug, "--session", SESSION, "--workspace", str(work)],
        "env": env,
        "groups": [slug],                   # only the mode with this slug sees these tools
        "alwaysAllow": ["run_command", "edit_file", "write_file", "git", "db_exec", "deploy"],
        "timeout": 300000,                  # ms; Bob's default is 60s
    } for slug in FLEET}
    (bob / "mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")

    modes = [{"slug": slug, "name": slug, "roleDefinition": ROLE.format(slug=slug, task=task),
              "groups": ["read", "mcp"]}    # no edit/execute (layer 1), no mode switching
             for slug, task in FLEET.items()]
    (bob / "custom_modes.yaml").write_text(
        yaml.safe_dump({"customModes": modes}, sort_keys=False, width=100, allow_unicode=True))

    hook = {"type": "command", "command": str(VENV_BIN / "kosha-hook"), "timeout": 10}
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
          f"  2. open in Bob:    {out['work']}  (trust the workspace; reload MCP servers and modes)\n"
          f"  3. fleet modes:    {', '.join(FLEET)}  (session {SESSION})\n"
          f"  prerequisite: ~/.bob/settings/settings.json needs \"mcp\" in approval.allowed_permissions,\n"
          f"                or Bob asks for approval on every kosha tool call (Kosha already decides).")


if __name__ == "__main__":
    main(sys.argv[1:])
