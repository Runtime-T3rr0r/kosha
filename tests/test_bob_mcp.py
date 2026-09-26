"""kosha-mcp end to end: real koshad, temp git workspace, sqlite dev/prod aliases.
Nothing may execute unless koshad said allow; canary files prove it."""
import asyncio
import os
import sqlite3
import stat
import subprocess
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from kosha.adapters import bob_mcp
from kosha.adapters.bob_mcp import Gateway, build_server, resolve_agent_id
from kosha.system import resolvers


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A git workspace plus sqlite dev/prod databases wired into the db alias config."""
    monkeypatch.delenv("KOSHA_WORKSPACE", raising=False)
    w = tmp_path / "ws"
    w.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (w / "app.py").write_text("VERSION = '1.2'\n")
    git("add", ".")
    git("commit", "-qm", "init")
    for name in ("dev", "prod"):
        c = sqlite3.connect(tmp_path / f"{name}.db")
        c.execute("create table users(id integer)")
        c.commit()
        c.close()
    cfg = {"databases": {"dev": {"url": f"sqlite:///{tmp_path / 'dev.db'}", "scope": "local"},
                         "prod": {"url": f"sqlite:///{tmp_path / 'prod.db'}", "scope": "shared"}}}
    monkeypatch.setattr(resolvers, "load_config", lambda *a: cfg)
    return w


def gw(ws, agent="sub1"):
    return Gateway(agent, "fleet1", str(ws))


def raw(args):
    return {"arguments": args, "_meta": {}}


def rows(k, sql, *params):
    with k.db._conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def last_action(k):
    return rows(k, "SELECT * FROM actions ORDER BY created_at DESC LIMIT 1")[0]


# --- identity ---

def test_per_mode_instance_ignores_payload_identity(monkeypatch):
    monkeypatch.setattr(bob_mcp, "IDENTITY_MODE", "per_mode_instance")
    spoof = {"arguments": {"agent": "someone_else"}, "_meta": {"agent_id": "spoofed"}}
    assert resolve_agent_id(spoof, "sub1") == "sub1"


def test_inline_mode_reads_meta_then_argument_then_falls_back(monkeypatch):
    monkeypatch.setattr(bob_mcp, "IDENTITY_MODE", "inline")
    assert resolve_agent_id({"arguments": {"agent": "a"}, "_meta": {"agent_id": "m"}}, "s") == "m"
    assert resolve_agent_id({"arguments": {"agent": "a"}, "_meta": {}}, "s") == "a"
    assert resolve_agent_id({"arguments": None}, "s") == "s"


# --- action building ---

def test_build_action_shapes(ws):
    g = gw(ws)
    run = g.build_action("run_command", {"command": "git push -f origin main", "cwd": "."}, "sub1")
    edit = g.build_action("edit_file", {"path": "app.py", "old": "a", "new": "b"}, "sub1")
    git = g.build_action("git", {"args": "commit -m 'x y'"}, "sub1")
    db = g.build_action("db_exec", {"db": "prod", "sql": "delete from users"}, "sub1")
    dep = g.build_action("deploy", {"target": "prod"}, "sub1")
    real = os.path.realpath(ws)
    assert (run.argv, run.cwd, run.harness, run.session_id) == (
        ["git", "push", "-f", "origin", "main"], real, "bob", "fleet1")
    assert edit.targets == [os.path.join(real, "app.py")]
    assert git.argv == ["git", "commit", "-m", "x y"]
    assert (db.tool, db.targets) == ("db_exec", ["prod"])
    assert (dep.argv, dep.targets) == (["deploy", "prod"], ["prod"])


# --- allow -> execute -> settle ---

def test_allowed_command_runs_and_confirms(live_koshad, ws):
    is_error, text = gw(ws).call("run_command", raw({"command": "echo hello"}))
    assert not is_error and "hello" in text
    a = last_action(live_koshad)
    assert (a["agent_id"], a["status"], a["level"]) == ("sub1", "confirmed", 0)


def test_failed_command_cancels_and_refunds(live_koshad, ws):
    is_error, text = gw(ws).call("run_command", raw({"command": "rm does_not_exist.txt"}))
    assert is_error and "exit 1" in text
    assert last_action(live_koshad)["status"] == "cancelled"
    assert live_koshad.db.session("fleet1")["fleet"]["spent"] == 0


def test_write_file_writes_confirms_and_leases(live_koshad, ws):
    is_error, _ = gw(ws).call("write_file", raw({"path": "notes/new.md", "content": "hi"}))
    assert not is_error and (ws / "notes" / "new.md").read_text() == "hi"
    a = last_action(live_koshad)
    assert (a["status"], a["level"]) == ("confirmed", 3)          # untracked write
    target = os.path.join(os.path.realpath(ws), "notes", "new.md")
    assert rows(live_koshad, "SELECT path FROM expected_writes WHERE action_id=?", a["action_id"]) \
        == [{"path": target}]


def test_edit_file_tracked_clean_is_l2(live_koshad, ws):
    is_error, _ = gw(ws).call("edit_file", raw({"path": "app.py", "old": "1.2", "new": "1.3"}))
    assert not is_error and "1.3" in (ws / "app.py").read_text()
    assert last_action(live_koshad)["level"] == 2


def test_edit_file_with_ambiguous_old_fails_and_refunds(live_koshad, ws):
    before = (ws / "app.py").read_text()
    is_error, text = gw(ws).call("edit_file", raw({"path": "app.py", "old": "nope", "new": "x"}))
    assert is_error and "found 0" in text
    assert (ws / "app.py").read_text() == before
    assert last_action(live_koshad)["status"] == "cancelled"


def test_db_exec_dev_write_and_select(live_koshad, ws):
    g = gw(ws)
    assert g.call("db_exec", raw({"db": "dev", "sql": "insert into users values (1)"})) == \
        (False, "ok, 1 row(s) changed")
    assert g.call("db_exec", raw({"db": "dev", "sql": "select id from users"})) == (False, "1")
    assert g.call("db_exec", raw({"db": "nope", "sql": "select 1"}))[0] is True


def test_deploy_runs_the_configured_command(live_koshad, ws, monkeypatch, tmp_path):
    marker = tmp_path / "deployed"
    script = tmp_path / "fake_deploy.sh"
    script.write_text(f"#!/bin/sh\necho \"$1\" > {marker}\n")
    script.chmod(0o755)
    monkeypatch.setenv("KOSHA_DEPLOY_CMD", str(script))
    is_error, _ = gw(ws).call("deploy", raw({"target": "staging"}))
    assert not is_error and marker.read_text().strip() == "staging"
    assert last_action(live_koshad)["level"] == 4


# --- deny / ask never execute ---

def test_hard_deny_prod_drop_never_runs(live_koshad, ws, tmp_path):
    is_error, text = gw(ws).call("db_exec", raw({"db": "prod", "sql": "drop database app; drop table users"}))
    assert is_error and "hard-deny" in text
    c = sqlite3.connect(tmp_path / "prod.db")
    assert c.execute("select count(*) from sqlite_master where name='users'").fetchone() == (1,)
    assert last_action(live_koshad)["status"] == "denied"


def test_ask_holds_then_approval_lets_the_retry_run(live_koshad, ws):
    target = ws / "run.sh"
    target.write_text("echo hi\n")
    target.chmod(0o644)
    g = gw(ws)
    is_error, text = g.call("run_command", raw({"command": "chmod 777 run.sh"}))
    assert is_error and "human approval" in text and "retry this exact call" in text
    assert stat.S_IMODE(target.stat().st_mode) == 0o644                # held, not run

    [ap] = live_koshad.db.pending_approvals()
    assert ap["agent_id"] == "sub1"
    live_koshad.db.resolve_approval(ap["id"], "approve_once")
    is_error, _ = g.call("run_command", raw({"command": "chmod 777 run.sh"}))
    assert not is_error and stat.S_IMODE(target.stat().st_mode) == 0o777


def test_koshad_down_fails_closed_nothing_runs(dead_koshad, ws):
    g = gw(ws)
    is_error, text = g.call("run_command", raw({"command": "touch canary"}))
    assert is_error and "failing closed" in text
    is_error, _ = g.call("write_file", raw({"path": "canary2", "content": "x"}))
    assert is_error
    assert not (ws / "canary").exists() and not (ws / "canary2").exists()


def test_unknown_tool_is_rejected(dead_koshad, ws):
    assert gw(ws).call("launch_missiles", raw({})) == (True, "kosha-mcp: unknown tool launch_missiles")


# --- over the MCP protocol ---

async def _mcp_calls(server, calls):
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        results = [await c.call_tool(n, a) for n, a in calls]
    return names, results


def test_mcp_protocol_two_instances_two_identities(live_koshad, ws):
    for agent in ("sub1", "sub2"):
        names, [res] = asyncio.run(_mcp_calls(build_server(gw(ws, agent)),
                                              [("run_command", {"command": "echo hi"})]))
        assert names == bob_mcp.TOOL_NAMES
        assert not res.is_error and "hi" in res.content[0].text
    agents = [r["agent_id"] for r in rows(live_koshad, "SELECT agent_id FROM actions ORDER BY created_at")]
    assert agents == ["sub1", "sub2"]


def test_mcp_protocol_adapter_crash_is_an_error_not_a_success(ws, monkeypatch):
    g = gw(ws)
    monkeypatch.setattr(g, "call", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    _, [res] = asyncio.run(_mcp_calls(build_server(g), [("run_command", {"command": "touch canary"})]))
    assert res.is_error and "nothing was run" in res.content[0].text
    assert not (ws / "canary").exists()


def test_stdio_entrypoint(live_koshad, ws):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kosha.adapters.bob_mcp", "--agent", "stdio-mode", "--session", "fleet1",
              "--workspace", str(ws)],
        env={**os.environ, "KOSHAD_URL": live_koshad.url})
    names, [res] = asyncio.run(_mcp_calls(params, [("run_command", {"command": "echo via-stdio"})]))
    assert "run_command" in names
    assert not res.is_error and "via-stdio" in res.content[0].text
    assert last_action(live_koshad)["agent_id"] == "stdio-mode"


def test_agent_is_required():
    with pytest.raises(SystemExit):
        bob_mcp.main([])


def test_mcp_protocol_meta_reaches_identity_in_inline_mode(live_koshad, ws, monkeypatch):
    # regression: SDK 2.x hands _meta over as a plain dict
    monkeypatch.setattr(bob_mcp, "IDENTITY_MODE", "inline")

    async def go():
        async with Client(build_server(gw(ws, "fallback"))) as c:
            return await c.call_tool("run_command", {"command": "echo m"}, meta={"agent_id": "from-meta"})

    res = asyncio.run(go())
    assert not res.is_error
    assert last_action(live_koshad)["agent_id"] == "from-meta"


def test_every_tool_description_carries_the_gating_rules():
    # Bob ignores MCP server `instructions`; tool descriptions are what the model sees
    for t in bob_mcp.TOOLS:
        assert "KOSHA HELD FOR HUMAN APPROVAL" in t.description and "retry this exact call" in t.description
        assert "KOSHA DENIED" in t.description, t.name


def test_undeclared_arguments_never_reach_koshad(ws):
    # regression: a self-issued approval_token once passed straight into raw and lifted a hard deny
    a = gw(ws).build_action("db_exec", {"db": "prod", "sql": "drop database app",
                                        "approval_token": "i-made-this-up", "junk": 1}, "sub1")
    assert a.raw == {"db": "prod", "sql": "drop database app"}


def test_self_issued_token_does_not_lift_hard_deny_end_to_end(live_koshad, ws):
    is_error, text = gw(ws).call("db_exec", raw({"db": "prod", "sql": "drop database app",
                                                 "approval_token": "i-made-this-up"}))
    assert is_error and text.startswith("KOSHA DENIED")
    assert last_action(live_koshad)["rule"] == "hard_deny"


def test_tool_descriptions_say_reads_are_free():
    # live finding: agents refused cheap reads "to save budget"
    for t in bob_mcp.TOOLS:
        assert "read-only calls are free" in t.description and "don't ration normal work" in t.description
