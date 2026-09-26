"""kosha-hook for Bob: payload mapping as a subprocess, then end to end through Bob's
own hook runner (extracted from the local Bob install at test time), then the
deniedCommands gaps against Bob's real matcher."""
import json
import os
import stat
import sys
import time

import pytest

from tests import bob_runtime
from tests.test_claude_hook import assert_blocked, rows, run_hook

NATIVE_MATCHER = "^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$"


def bob_payload(tool="execute_command", tool_input=None, event="PreToolUse", tool_use_id="bt1",
                cwd="/tmp/ws"):
    # Bob: {session_id: rootTaskId, cwd, hook_event_name, tool_name, tool_input, tool_use_id}
    return {"session_id": "bob-root-1", "cwd": cwd, "hook_event_name": event, "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {"command": "ls"},
            "tool_use_id": tool_use_id}


# --- mapping (subprocess) ---

@pytest.mark.parametrize("tool, tool_input, action_tool, targets", [
    ("write_file", {"path": "src/app.py", "content": "x"}, "write_file", ["/tmp/ws/src/app.py"]),
    ("apply_diff", {"path": "app.py", "diff": "<<<<<<< SEARCH"}, "edit_file", ["/tmp/ws/app.py"]),
    ("insert_content", {"path": "a/b.py", "line": 1, "content": "x"}, "edit_file", ["/tmp/ws/a/b.py"]),
    ("search_and_replace", {"path": "c.py", "search": "a", "replace": "b"}, "edit_file", ["/tmp/ws/c.py"]),
    ("office_edit", {"path": "doc.docx", "operation": "set"}, "edit_file", ["/tmp/ws/doc.docx"]),
    ("execute_command", {"command": "ls -la"}, "run_command", []),
])
def test_bob_native_tools_map_to_actions(live_koshad, tool, tool_input, action_tool, targets):
    code, out, err, _ = run_hook(bob_payload(tool, tool_input))
    assert out == "" and code in (0, 2), err
    [a] = rows(live_koshad, "SELECT * FROM actions")
    assert (a["action_id"], a["harness"], a["agent_id"], a["tool"], json.loads(a["targets"])) == \
        ("bob:bob-root-1:bt1", "bob", "bob-native", action_tool, targets)


def test_execute_command_cwd_is_workspace_relative(live_koshad):
    run_hook(bob_payload(tool_input={"command": "ls", "cwd": "sub/dir"}))
    assert rows(live_koshad, "SELECT cwd FROM actions") == [{"cwd": "/tmp/ws/sub/dir"}]


def test_kosha_mcp_and_read_only_tools_pass_without_koshad(dead_koshad):
    for tool in ("mcp__kosha-sub1__run_command", "read_file", "list_files", "grep"):
        code, out, err, _ = run_hook(bob_payload(tool, {"path": "x"}))
        assert (code, out, err) == (0, "", ""), tool


def test_other_mcp_tools_are_priced(dead_koshad):
    code, out, err, _ = run_hook(bob_payload("mcp__github__create_issue", {"title": "x"}))
    assert_blocked(code, out, err, "failing closed")


def test_bob_post_tool_use_confirms(live_koshad):
    run_hook(bob_payload())
    code, _, _, _ = run_hook(bob_payload(event="PostToolUse"))
    assert code == 0
    assert rows(live_koshad, "SELECT status FROM actions") == [{"status": "confirmed"}]


# --- end to end through Bob's real hook runner ---

needs_bob = pytest.mark.skipif(not bob_runtime.available(), reason="Bob IDE or node not installed")
HOOK_BIN = os.path.join(os.path.dirname(sys.executable), "kosha-hook")


@pytest.fixture
def bob(tmp_path):
    return bob_runtime.BobRuntime(tmp_path)


def hooks(command=HOOK_BIN, matcher=NATIVE_MATCHER, timeout=10):
    entry = {"type": "command", "command": command, "timeout": timeout}
    return {"PreToolUse": [{"matcher": matcher, "hooks": [entry]}],
            "PostToolUse": [{"matcher": matcher, "hooks": [entry]}]}


def patched_hook_script(tmp_path, code: str) -> str:
    """A hook command that patches kosha internals, then runs the real main()."""
    script = tmp_path / "patched_hook.py"
    script.write_text(f"import kosha.adapters.client as c\n{code}\n"
                      "from kosha.adapters.claude_hook import main\nmain()\n")
    return f"{sys.executable} {script}"


@needs_bob
def test_bob_runner_allows_a_cheap_native_command(bob, live_koshad, tmp_path):
    r = bob.run_hooks(hooks(), bob_payload(cwd=str(tmp_path)))
    assert r["blocked"] is False and r["warns"] == []


@needs_bob
def test_bob_runner_blocks_hard_deny_with_kosha_reason(bob, live_koshad, tmp_path):
    r = bob.run_hooks(hooks(), bob_payload(tool_input={"command": "rm -rf /"}, cwd=str(tmp_path)))
    assert r["blocked"] is True and "hard-deny" in r["reason"]


@needs_bob
def test_bob_runner_blocks_ask_with_approval_instructions(bob, live_koshad, tmp_path):
    r = bob.run_hooks(hooks(), bob_payload(tool_input={"command": "chmod 777 x.sh"}, cwd=str(tmp_path)))
    assert r["blocked"] is True and "human approval" in r["reason"]


@needs_bob
def test_bob_runner_fails_closed_when_koshad_is_down(bob, dead_koshad, tmp_path):
    r = bob.run_hooks(hooks(), bob_payload(cwd=str(tmp_path)))
    assert r["blocked"] is True and "failing closed" in r["reason"]


@needs_bob
def test_bob_runner_fails_closed_when_the_hook_hangs(bob, dead_koshad, tmp_path):
    cmd = patched_hook_script(tmp_path, "import time\nc.decide = lambda *a, **k: time.sleep(60)")
    t = time.monotonic()
    r = bob.run_hooks(hooks(cmd), bob_payload(cwd=str(tmp_path)), env={"KOSHA_HOOK_DEADLINE": "1"})
    assert r["blocked"] is True and "timed out" in r["reason"]
    assert time.monotonic() - t < 8          # our alarm, well before Bob's 10s hook timeout


@needs_bob
@pytest.mark.parametrize("exc", ["RuntimeError('boom')", "SystemExit(1)", "KeyboardInterrupt()"])
def test_bob_runner_fails_closed_when_the_hook_crashes(bob, dead_koshad, tmp_path, exc):
    cmd = patched_hook_script(tmp_path, f"def boom(*a, **k): raise {exc}\nc.decide = boom")
    r = bob.run_hooks(hooks(cmd), bob_payload(cwd=str(tmp_path)))
    assert r["blocked"] is True and "crashed" in r["reason"]


@needs_bob
def test_bob_runner_blocks_even_with_a_huge_reason(bob, dead_koshad, tmp_path):
    cmd = patched_hook_script(tmp_path, "from kosha.adapters.client import fail_closed\n"
                                        "c.decide = lambda *a, **k: fail_closed('x' * 5_000_000)")
    r = bob.run_hooks(hooks(cmd), bob_payload(cwd=str(tmp_path)))
    assert r["blocked"] is True                       # >1MB output would have failed open


@needs_bob
def test_anchored_matcher_leaves_kosha_mcp_tools_alone(bob, dead_koshad, tmp_path):
    r = bob.run_hooks(hooks(), bob_payload("mcp__kosha-sub1__write_file", {"path": "x"}, cwd=str(tmp_path)))
    assert r["blocked"] is False and r["warns"] == []      # hook never ran
    unanchored = bob.run_hooks(hooks(matcher="write_file"),
                               bob_payload("mcp__kosha-sub1__write_file", {"path": "x"}, cwd=str(tmp_path)))
    assert unanchored["blocked"] is False                  # ran, but kosha-hook passes its own tools


@needs_bob
def test_bob_runner_post_tool_use_settles_and_never_blocks(bob, live_koshad, tmp_path):
    bob.run_hooks(hooks(), bob_payload(cwd=str(tmp_path)))
    r = bob.run_hooks(hooks(), bob_payload(event="PostToolUse", cwd=str(tmp_path)))
    assert r["blocked"] is False
    assert rows(live_koshad, "SELECT status FROM actions") == [{"status": "confirmed"}]


@needs_bob
def test_wrong_interpreter_path_fails_open_so_register_absolute_paths(bob, dead_koshad, tmp_path):
    # documents Bob's behavior: exit 127 is not 2, so a misregistered hook silently allows
    r = bob.run_hooks(hooks("/nonexistent/.venv/bin/kosha-hook"), bob_payload(cwd=str(tmp_path)))
    assert r["blocked"] is False and "code 127" in r["warns"][0]


# --- layer 4: deniedCommands, against Bob's real matcher ---

@needs_bob
def test_denied_commands_catches_the_literal_pattern(bob):
    assert bob.denied_verdicts([
        {"command": "rm -rf build", "approved": [], "denied": ["rm -rf"]},
        {"command": "git push --force origin main", "approved": ["git push"], "denied": ["git push --force"]},
    ]) == ["deny", "deny"]


@needs_bob
@pytest.mark.parametrize("command, approved", [
    ("python3 cleanup.py", []),           # script wrapper: contents never inspected
    ("bash -c 'rm -rf build'", []),       # shell wrapper
    ("rm -fr build", []),                 # same flags, other order
    ("rm -r -f build", []),               # split flags
    ("/bin/rm -rf build", []),            # absolute path
    ("sudo rm -rf build", []),            # prefix
    ("rm -rf build", ["rm -rf build"]),   # an approved entry as long as the denied one wins the tie
])
def test_denied_commands_gaps_exist(bob, command, approved):
    """Layer 4 is real enforcement for the literal prefix only. These bypasses are expected;
    if one starts failing, Bob changed its matcher: re-check before claiming more coverage."""
    [verdict] = bob.denied_verdicts([{"command": command, "approved": approved, "denied": ["rm -rf"]}])
    assert verdict != "deny"
