"""kosha-hook for Claude Code, run as a real subprocess the way the harness runs it.

Blocking contract (both harnesses): exit 2 + reason on stderr blocks; exit 0 with no
output defers to the harness. Anything else (a traceback's exit 1, a signal, a hang)
would be treated as allow, so every failure test asserts exit 2 explicitly.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from tests.conftest import free_port

HOOK = [sys.executable, "-m", "kosha.adapters.claude_hook"]


def payload(event="PreToolUse", tool="Bash", tool_input=None, tool_use_id="tu1", **extra):
    base = {"session_id": "cc-sess", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp",
            "permission_mode": "default", "hook_event_name": event, "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {"command": "ls"},
            "tool_use_id": tool_use_id}
    return {**base, **extra}


def run_hook(stdin, env_extra=None, argv=HOOK, timeout=30):
    env = {**os.environ, **(env_extra or {})}
    data = stdin if isinstance(stdin, str) else json.dumps(stdin)
    t = time.monotonic()
    p = subprocess.run(argv, input=data, capture_output=True, text=True, env=env, timeout=timeout)
    return p.returncode, p.stdout, p.stderr, time.monotonic() - t


def patched_hook(code: str):
    """Run the real main() after patching kosha internals in the hook's own process."""
    return [sys.executable, "-c", f"import kosha.adapters.client as c\n{code}\n"
                                  "from kosha.adapters.claude_hook import main\nmain()"]


def rows(k, sql, *params):
    with k.db._conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def assert_blocked(code, out, err, needle):
    assert code == 2, (code, out, err)
    assert out == ""                         # nothing on stdout; the reason goes to stderr
    assert needle in err and "Traceback" not in err


# --- allow / deny / ask against a live koshad ---

def test_allow_is_silent_and_recorded(live_koshad):
    code, out, err, _ = run_hook(payload(tool_input={"command": "ls -la"}))
    assert (code, out, err) == (0, "", "")
    [a] = rows(live_koshad, "SELECT * FROM actions")
    assert (a["action_id"], a["harness"], a["agent_id"], a["tool"], a["status"]) == \
        ("claude_code:cc-sess:tu1", "claude_code", "main", "run_command", "reserved")


def test_subagent_identity_comes_from_payload(live_koshad):
    run_hook(payload(agent_id="agent-abc", agent_type="general-purpose"))
    assert rows(live_koshad, "SELECT agent_id FROM actions") == [{"agent_id": "agent-abc"}]


def test_hard_deny_blocks_with_reason(live_koshad):
    code, out, err, _ = run_hook(payload(tool_input={"command": "ls && rm -rf /"}))
    assert_blocked(code, out, err, "hard-deny")


def test_ask_blocks_then_approval_lets_retry_through(live_koshad):
    p = payload(tool_input={"command": "chmod 777 deploy.sh"})
    code, out, err, _ = run_hook(p)
    assert_blocked(code, out, err, "human approval")
    assert "retry this exact call" in err
    [ap] = live_koshad.db.pending_approvals()
    live_koshad.db.resolve_approval(ap["id"], "approve_once")
    code, out, err, _ = run_hook({**p, "tool_use_id": "tu2"})     # the retry is a new tool call
    assert (code, out, err) == (0, "", "")


def test_edit_tools_map_to_file_actions_with_leases(live_koshad):
    run_hook(payload(tool="Edit", tool_use_id="e1",
                     tool_input={"file_path": "/tmp/x.py", "old_string": "a", "new_string": "b"}))
    run_hook(payload(tool="Write", tool_use_id="w1", tool_input={"file_path": "/tmp/y.py", "content": "c"}))
    got = rows(live_koshad, "SELECT action_id, tool, targets FROM actions ORDER BY action_id")
    assert [(g["tool"], json.loads(g["targets"])) for g in got] == \
        [("edit_file", ["/tmp/x.py"]), ("write_file", ["/tmp/y.py"])]
    leased = {r["path"] for r in rows(live_koshad, "SELECT path FROM expected_writes")}
    assert {"/tmp/x.py", "/tmp/y.py"} <= leased


# --- settle ---

def test_post_tool_use_confirms(live_koshad):
    run_hook(payload())
    code, out, err, _ = run_hook(payload(event="PostToolUse", tool_response={"stdout": "x"}))
    assert (code, out, err) == (0, "", "")
    assert rows(live_koshad, "SELECT status FROM actions") == [{"status": "confirmed"}]


def test_post_tool_use_failure_refunds(live_koshad):
    run_hook(payload(tool_input={"command": "rm notes.txt"}))
    assert live_koshad.db.session("cc-sess")["fleet"]["spent"] > 0
    code, _, _, _ = run_hook(payload(event="PostToolUseFailure", error="exit 1", is_interrupt=False))
    assert code == 0
    assert rows(live_koshad, "SELECT status FROM actions") == [{"status": "cancelled"}]
    assert live_koshad.db.session("cc-sess")["fleet"]["spent"] == 0


def test_post_events_never_block_even_with_koshad_down(dead_koshad):
    for event in ("PostToolUse", "PostToolUseFailure"):
        code, out, err, _ = run_hook(payload(event=event))
        assert (code, out) == (0, "")


# --- fail closed ---

def test_koshad_down_blocks(dead_koshad):
    code, out, err, _ = run_hook(payload())
    assert_blocked(code, out, err, "failing closed")


def test_koshad_hanging_blocks_within_deadline(monkeypatch):
    # accepts connections, never answers
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    url = f"http://127.0.0.1:{srv.getsockname()[1]}"
    code, out, err, took = run_hook(payload(), {"KOSHAD_URL": url, "KOSHA_HOOK_DEADLINE": "1.5"})
    srv.close()
    assert_blocked(code, out, err, "failing closed")
    assert took < 5


def test_hang_inside_the_hook_hits_the_alarm(dead_koshad):
    code, out, err, took = run_hook(
        payload(), {"KOSHA_HOOK_DEADLINE": "1"},
        argv=patched_hook("import time\nc.decide = lambda *a, **k: time.sleep(60)"))
    assert_blocked(code, out, err, "timed out after 1s")
    assert took < 5


@pytest.mark.parametrize("exc", ["RuntimeError('boom')", "KeyboardInterrupt()", "SystemExit(0)",
                                 "SystemExit(1)", "MemoryError()"])
def test_crash_mid_execution_still_exits_2(dead_koshad, exc):
    code, out, err, _ = run_hook(
        payload(), argv=patched_hook(f"def boom(*a, **k): raise {exc}\nc.decide = boom"))
    assert_blocked(code, out, err, "kosha-hook crashed")


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"hook_event_name": "PreToolUse"',
                                   json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                               "tool_input": "not a dict"})])
def test_malformed_input_blocks(dead_koshad, stdin):
    code, out, err, _ = run_hook(stdin)
    assert code == 2 and out == "" and "Traceback" not in err


def test_huge_reason_is_truncated_far_below_1mb(dead_koshad):
    code, out, err, _ = run_hook(payload(), argv=patched_hook(
        "from kosha.adapters.client import fail_closed\n"
        "c.decide = lambda *a, **k: fail_closed('x' * 5_000_000)"))
    assert code == 2 and out == "" and len(err) <= 2000


# --- not kosha's call ---

def test_read_only_tools_pass_without_asking_koshad(dead_koshad):
    code, out, err, _ = run_hook(payload(tool="Read", tool_input={"file_path": "/etc/hosts"}))
    assert (code, out, err) == (0, "", "")


def test_unknown_tools_are_priced_not_waved_through(dead_koshad):
    code, out, err, _ = run_hook(payload(tool="SomeFutureWriteTool", tool_input={"x": 1}))
    assert_blocked(code, out, err, "failing closed")


def test_other_events_pass(dead_koshad):
    code, out, err, _ = run_hook({"hook_event_name": "SessionStart", "session_id": "s"})
    assert (code, out) == (0, "")


def test_console_script_is_installed_and_fails_closed(dead_koshad):
    script = os.path.join(os.path.dirname(sys.executable), "kosha-hook")
    code, out, err, _ = run_hook(payload(), argv=[script])
    assert_blocked(code, out, err, "failing closed")
