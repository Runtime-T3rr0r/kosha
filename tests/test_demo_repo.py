"""demo_repo/setup_demo.py builds a working, disposable demo world, and every step the
"prepare release 1.3" scenario takes is priced the way the scenario needs."""
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo_repo"))
import setup_demo  # noqa: E402

from kosha.system import parser, resolvers  # noqa: E402
from kosha.system.action import Action  # noqa: E402

BOB_HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact",
                   "PreToolUse", "PostToolUse", "Stop"}


@pytest.fixture(autouse=True)
def no_live_koshad(monkeypatch):
    # tests must not depend on whether a real koshad is running on this machine's 8765;
    # the refuse-while-running guard has its own test that turns this back on
    monkeypatch.setattr(setup_demo, "koshad_running", lambda port=8765: False)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(setup_demo, "koshad_running", lambda port=8765: False)
        return setup_demo.build(tmp_path_factory.mktemp("d") / ".demo")


@pytest.fixture
def demo_config(demo, monkeypatch):
    monkeypatch.setenv("KOSHA_CONFIG", str(demo["config"]))
    monkeypatch.delenv("KOSHA_WORKSPACE", raising=False)
    resolvers.load_config.cache_clear()
    yield demo
    resolvers.load_config.cache_clear()


def bob_tool_id(server: str, tool: str) -> str:
    # Bob's SVt()/mke()
    clean = lambda s: re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_-]", "_", s)).strip("_")
    return f"mcp__{clean(server)}__{clean(tool)}"


# --- the world ---

def test_git_repo_pushed_to_local_origin(demo):
    work = demo["work"]
    log = lambda *a: subprocess.run(["git", *a], cwd=work, capture_output=True, text=True).stdout.strip()
    assert log("log", "--format=%s") == "release 1.2.0"
    assert log("status", "--porcelain") == ""
    assert log("rev-parse", "HEAD") == log("rev-parse", "origin/main")
    assert Path(log("remote", "get-url", "origin")) == demo["root"] / "origin.git"
    assert not (work / "setup_demo.py").exists()


def test_prod_is_one_migration_behind_dev(demo):
    applied = lambda p: [r[0] for r in sqlite3.connect(p).execute("SELECT name FROM schema_migrations ORDER BY name")]
    assert applied(demo["work"] / "data" / "dev.db") == ["001_init.sql", "002_add_email.sql"]
    assert applied(demo["root"] / "prod.db") == ["001_init.sql"]


def test_exactly_the_flaky_test_fails(demo):
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no"],
                       cwd=demo["work"], capture_output=True, text=True)
    assert "1 failed, 3 passed" in p.stdout and "test_health_is_fast" in p.stdout


def test_deploy_stub_is_short_and_local(demo):
    t = time.monotonic()
    p = subprocess.run(["./deploy.sh", "staging"], cwd=demo["work"], capture_output=True, text=True)
    assert p.returncode == 0 and "deployed v1.2.0 to staging (stub)" in p.stdout
    assert time.monotonic() - t < 3     # a gated command leases its cwd while it runs


def test_start_script_points_at_the_demo(demo):
    text = demo["start"].read_text()
    for needle in (f'KOSHA_DB="{demo["root"] / "kosha.db"}"', f'KOSHA_GUARD_ROOT="{demo["work"]}"',
                   f'KOSHA_CONFIG="{demo["config"]}"', "koshad"):
        assert needle in text
    assert os.access(demo["start"], os.X_OK)


def test_reset_refuses_a_directory_it_did_not_create(tmp_path):
    victim = tmp_path / "not_a_demo"
    victim.mkdir()
    (victim / "important.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        setup_demo.build(victim)
    assert (victim / "important.txt").exists()


def test_reset_rebuilds_from_scratch(tmp_path):
    first = setup_demo.build(tmp_path / ".demo")
    (first["work"] / "junk.txt").write_text("x")
    (first["root"] / "kosha.db").write_text("old ledger")
    second = setup_demo.build(tmp_path / ".demo")
    assert not (second["work"] / "junk.txt").exists() and not (second["root"] / "kosha.db").exists()


# --- Bob config, checked against Bob's own rules ---

def test_one_kosha_mode_on_kosha_tools_and_the_gating_rules(demo):
    modes = yaml.safe_load((demo["work"] / ".bob" / "custom_modes.yaml").read_text())["customModes"]
    assert [m["slug"] for m in modes] == ["kosha"]
    m = modes[0]
    assert re.fullmatch(r"[a-zA-Z0-9-]+", m["slug"]) and m["name"]           # Bob's mode schema
    assert set(m["groups"]) == {"read", "todo", "mcp"}                         # no native edit/execute
    role = m["roleDefinition"]
    for tag in ("mcp__kosha__run_command", "KOSHA HELD FOR HUMAN APPROVAL", "KOSHA DENIED BY A HUMAN",
                "KOSHA UNAVAILABLE", "retry the exact same call", "never try to approve a held step yourself"):
        assert tag in role


def test_one_kosha_server_pinned_to_the_mode_and_never_prompted(demo):
    servers = json.loads((demo["work"] / ".bob" / "mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"kosha"}
    e = servers["kosha"]
    assert e["groups"] == ["kosha"] and set(e["alwaysAllow"]) == set(setup_demo.KOSHA_TOOLS)
    args = e["args"]
    assert args[args.index("--session") + 1] == setup_demo.SESSION
    assert e["timeout"] / 1000 >= int(e["env"]["KOSHA_APPROVAL_WAIT"]) + 270
    assert len(bob_tool_id("kosha", "kosha_review")) <= 64
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    async def tools():
        params = StdioServerParameters(command=e["command"], args=e["args"], env={**os.environ, **e["env"]})
        async with Client(params) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert asyncio.run(tools()) == set(setup_demo.KOSHA_TOOLS)


def test_venv_link_lets_agents_run_the_app_tests(demo):
    link = demo["work"] / ".venv"
    assert link.is_symlink() and (link / "bin" / "python").exists()
    p = subprocess.run([str(link / "bin" / "python"), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no"],
                       cwd=demo["work"], capture_output=True, text=True)
    assert "1 failed, 3 passed" in p.stdout                                   # the app's deps are there
    tracked = subprocess.run(["git", "ls-files"], cwd=demo["work"], capture_output=True, text=True).stdout
    assert ".venv" not in tracked


def test_hook_settings_match_bobs_strict_schema(demo):
    cfg = json.loads((demo["work"] / ".bob" / "settings.json").read_text())
    assert set(cfg) == {"hooks"} and set(cfg["hooks"]) <= BOB_HOOK_EVENTS
    for entries in cfg["hooks"].values():
        for entry in entries:
            assert set(entry) == {"matcher", "hooks"}
            for h in entry["hooks"]:
                assert set(h) <= {"type", "command", "timeout", "disabled"} and h["type"] == "command"
                *env, binary = h["command"].split()
                assert os.path.isabs(binary) and os.access(binary, os.X_OK)
                env = dict(e.split("=", 1) for e in env)
                assert env["KOSHA_SESSION"] == setup_demo.SESSION        # same fleet as the modes
                # Bob treats a timed-out hook as ALLOW: its timeout must outlast our wait
                assert h["timeout"] > int(env["KOSHA_HOOK_WAIT"]) + 60
            m = re.compile(entry["matcher"])
            assert m.search("write_file") and m.search("execute_command")
            assert not m.search(bob_tool_id("kosha-release-bump", "write_file"))   # anchored


# --- the scenario's steps are priced as the story needs ---

def classify(demo, tool, raw=None, argv=(), targets=()):
    a = Action("x", setup_demo.SESSION, "release-bump", "bob", tool, raw or {}, list(argv),
               str(demo["work"]), [str(demo["work"] / t) if tool.endswith("_file") else t for t in targets], "")
    return parser.classify(a)


@pytest.mark.parametrize("step, tool, raw, targets, level", [
    ("bump VERSION (tracked, clean)", "edit_file", {}, ["VERSION"], 2),
    ("update CHANGELOG", "edit_file", {}, ["CHANGELOG.md"], 2),
    ("fix the flaky test", "edit_file", {}, ["tests/test_app.py"], 2),
    ("edit the CI workflow", "edit_file", {}, [".github/workflows/ci.yml"], 4),
    ("run the tests", "run_command", {"command": "python -m pytest -q"}, [], 0),
    ("commit", "run_command", {"command": "git commit -am 'release 1.3.0'"}, [], 2),
    ("push to origin", "git", {"args": "push origin main"}, [], 4),
    ("migrate dev", "run_command", {"command": "python manage.py migrate --db dev"}, [], 3),
    ("migrate prod", "run_command", {"command": "python manage.py migrate --db prod"}, [], 4),
    ("read prod", "db_exec", {"db": "prod", "sql": "select count(*) from users"}, ["prod"], 0),
    ("deploy prod", "deploy", {"target": "prod"}, ["prod"], 4),
])
def test_scenario_step_levels(demo_config, step, tool, raw, targets, level):
    argv = ["git", *raw["args"].split()] if tool == "git" else []
    assert classify(demo_config, tool, raw, argv, targets).level == level, step


def test_reset_refuses_while_koshad_is_running(tmp_path, monkeypatch):
    # a dying koshad re-creates kosha.db mid-delete, so never wipe under a live one
    first = setup_demo.build(tmp_path / ".demo")
    monkeypatch.setattr(setup_demo, "koshad_running", lambda port=8765: True)
    with pytest.raises(SystemExit, match="koshad is still running"):
        setup_demo.build(tmp_path / ".demo")
    assert (first["root"] / setup_demo.MARKER).exists() and first["work"].exists()   # untouched


def test_role_tells_agents_reads_are_free_and_not_to_ration(demo):
    # live finding: agents refused free reads "to save budget"
    [m] = yaml.safe_load((demo["work"] / ".bob" / "custom_modes.yaml").read_text())["customModes"]
    assert "Read-only steps" in m["roleDefinition"] and "are free" in m["roleDefinition"]
    assert "Do not ration, skip or refuse normal work" in m["roleDefinition"]


def test_agents_workspace_has_no_demo_tooling(demo):
    # the run sheet (with expected Kosha outcomes) must never be readable by the agents
    work_files = {p.name for p in demo["work"].rglob("*") if ".git" not in p.parts}
    for leaked in ("DEMO.md", "rehearse.py", "show_bundle.py", "setup_demo.py"):
        assert leaked not in work_files
    tracked = subprocess.run(["git", "ls-files"], cwd=demo["work"], capture_output=True, text=True).stdout
    assert "DEMO.md" not in tracked and "rehearse.py" not in tracked


def test_workspace_settings_open_localhost_in_bob_and_deny_agents_browser_tools(demo):
    cfg = json.loads((demo["work"] / ".vscode" / "settings.json").read_text())
    assert cfg["workbench.browser.openLocalhostLinks"] is True
    assert cfg["workbench.browser.enableChatTools"] is False
    tracked = subprocess.run(["git", "ls-files"], cwd=demo["work"], capture_output=True, text=True).stdout
    assert ".vscode" not in tracked
