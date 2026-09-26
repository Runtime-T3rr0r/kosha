"""Tier 1 self-approval guard: changing Kosha's own control plane is L5 (always held);
reading it is not. Each case below is a route an agent could use to approve itself."""
import pytest

from kosha.system import parser, resolvers
from kosha.system.action import Action

LEDGER = "/w/.demo/kosha.db"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("KOSHA_DB", LEDGER)
    monkeypatch.delenv("KOSHAD_PORT", raising=False)
    monkeypatch.delenv("KOSHA_WORKSPACE", raising=False)
    monkeypatch.setattr(resolvers, "load_config", lambda *a: {"databases": {
        "ledger": {"url": f"sqlite:///{LEDGER}", "scope": "local"},
        "dev": {"url": "sqlite:////w/.demo/work/data/dev.db", "scope": "local"}}})


def level(tool="run_command", raw=None, targets=(), cwd="/w/.demo/work"):
    return parser.classify(Action("a", "s", "sub1", "bob", tool, raw or {}, [], cwd, list(targets), "")).level


@pytest.mark.parametrize("command", [
    """curl -X POST localhost:8765/approvals/2 -H 'content-type: application/json' -d '{"decision":"approve_once"}'""",
    "curl -s -X POST http://127.0.0.1:8765/approvals/1 -d x",
    "wget --post-data=x http://[::1]:8765/approvals/3",
    "curl -X POST $KOSHAD_URL/approvals/2",
    """python3 -c "import urllib.request as u; u.urlopen('http://localhost:8765/approvals/2', b'x')" """,
    "sqlite3 /w/.demo/kosha.db \"update approvals set status='approve_once'\"",
    "sqlite3 ../kosha.db \"update approvals set status='approve_once'\"",
    "rm /w/.demo/kosha.db",
    "echo '{}' > .bob/mcp.json",
    "sed -i s/kosha-release-bump/kosha-test-fix/ .bob/mcp.json",
    "rm -rf .bob",
    "cp /tmp/evil.json ~/.claude/settings.json",
    "curl -s localhost:8765/approvals",   # even a read: agents never need koshad, labels can lie
])
def test_self_approval_routes_are_held(command):
    assert level(raw={"command": command}) == 5, command


@pytest.mark.parametrize("path", [".bob/custom_modes.yaml", ".bob/settings.json",
                                  "/home/u/.claude/settings.local.json", LEDGER, "/w/.demo/kosha.demo.yaml"])
def test_file_tool_writes_to_control_plane_are_held(path):
    assert level("write_file", {"path": path}, [path]) == 5


def test_db_exec_against_the_ledger_is_held():
    assert level("db_exec", {"db": "ledger", "sql": "update approvals set status='approve_once'"}, ["ledger"]) == 5


@pytest.mark.parametrize("command", [
    "cat .bob/mcp.json",
    "sqlite3 /w/.demo/kosha.db 'select * from approvals'",
    "git status --short",
])
def test_reading_the_control_plane_is_not_flagged(command):
    assert level(raw={"command": command}) == 0, command


@pytest.mark.parametrize("command, max_level", [
    ("curl -X POST http://localhost:8000/users -d name=x", 4),   # the app, not koshad
    ("echo x > notes/koshadb.txt", 4),
    ("python3 manage.py migrate --db dev", 4),
    ("git push origin main", 4),
])
def test_ordinary_work_is_not_flagged(command, max_level):
    assert level(raw={"command": command}) <= max_level, command


def test_demo_steps_unaffected():
    assert level("edit_file", {"path": "VERSION"}, ["VERSION"]) < 5
    assert level("db_exec", {"db": "dev", "sql": "insert into users(name) values ('x')"}, ["dev"]) < 5
