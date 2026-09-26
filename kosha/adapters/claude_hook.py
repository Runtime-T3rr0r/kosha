"""kosha-hook: PreToolUse / PostToolUse / PostToolUseFailure hook for Claude Code and Bob.

Both harnesses send the same payload shape ({session_id, cwd, hook_event_name,
tool_name, tool_input, tool_use_id}); the harness is told apart by tool name.
Bob's payload carries no agent identity, so Bob's native tools are charged to one
pooled agent, "bob-native": fleet budget and escalation see them, per-agent caps can't
separate them. kosha-mcp calls (mcp__kosha-*) are already gated by kosha-mcp and pass.
Bob only fires PostToolUse for tools that succeeded, so a failed native Bob call is
never refunded (the conservative side).

PreToolUse asks koshad about gated tools. Deny and ask both block with exit 2 and the
reason on stderr: the one blocking channel both Claude Code and Bob honor (Bob ignores
permissionDecision "ask" and every exit code except 2). Allow prints nothing and exits
0, leaving the call to the harness's own permission mode: kosha adds a gate, it never
removes one. PostToolUse / PostToolUseFailure settle the reservation and never block.

Fail-closed, and it has to be: both harnesses treat a crashed, killed, timed-out or
oddly-exiting hook as allow. So for PreToolUse:
  - the deadline alarm is armed before anything but the stdlib is imported;
  - every path, including the alarm and any exception, ends in os._exit, so no
    unhandled exception, atexit hook or broken stdout pipe can change the exit code;
  - output stays far below the harnesses' 1 MB buffer limit.

Register in .claude/settings.json (absolute path: a missing interpreter exits 127,
which the harness treats as allow):
  {"hooks": {
    "PreToolUse":         [{"matcher": "^(Bash|Edit|MultiEdit|Write|NotebookEdit)$",
                            "hooks": [{"type": "command", "command": "/abs/.venv/bin/kosha-hook", "timeout": 10}]}],
    "PostToolUse":        [{"matcher": "^(Bash|Edit|MultiEdit|Write|NotebookEdit)$",
                            "hooks": [{"type": "command", "command": "/abs/.venv/bin/kosha-hook", "timeout": 10}]}],
    "PostToolUseFailure": [{"matcher": "^(Bash|Edit|MultiEdit|Write|NotebookEdit)$",
                            "hooks": [{"type": "command", "command": "/abs/.venv/bin/kosha-hook", "timeout": 10}]}]}}

Bob: same "hooks" block in <workspace>/.bob/settings.json (trusted workspace) or the
user settings, PreToolUse and PostToolUse only, matcher
  "^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$"
Anchor it: Bob tests the matcher unanchored, so "write_file" alone would also match
kosha-mcp's own mcp__kosha-<mode>__write_file.
"""
import json
import os
import signal
import sys

DEADLINE = float(os.environ.get("KOSHA_HOOK_DEADLINE", "3.0"))   # seconds, whole hook
CLIENT_TIMEOUT = max(DEADLINE - 0.5, 0.5)                         # HTTP, inside the deadline
MAX_REASON = 2000                                                 # chars written to stderr
MAX_STDIN = 8 * 1024 * 1024

# Harness tool -> Action.tool. Anything not listed and not read-only is priced as
# "other" (unknown = expensive); read-only tools are not kosha's call.
CLAUDE_TOOLS = {"Bash": "run_command", "Edit": "edit_file", "MultiEdit": "edit_file",
                "NotebookEdit": "edit_file", "Write": "write_file"}
BOB_TOOLS = {"execute_command": "run_command", "write_file": "write_file",
             "apply_diff": "edit_file", "insert_content": "edit_file",
             "search_and_replace": "edit_file", "office_edit": "edit_file"}
READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite",
                   "TaskOutput", "ListMcpResourcesTool", "ReadMcpResourceTool",
                   # Bob
                   "read_file", "glob", "grep", "list_files", "office_read", "web_fetch",
                   "update_todo_list", "search_ibm_docs", "list_ibm_doc_libraries"}
KOSHA_MCP_PREFIX = "mcp__kosha"   # kosha-mcp tools: already priced by kosha-mcp itself
BOB_AGENT = "bob-native"
POST_EVENTS = {"PostToolUse": "success", "PostToolUseFailure": "failure"}

_pre = True   # until the event is known, a failure blocks


def _exit(code: int, message: str = "") -> None:
    """The only way out. os._exit: no exception, atexit hook or flush can alter the code."""
    if message:
        try:
            os.write(2, message[:MAX_REASON].encode("utf-8", "replace"))
        except OSError:
            pass
    os._exit(code)


UNAVAILABLE = ("KOSHA UNAVAILABLE: nothing was run (Kosha could not decide, so it failed closed).\n"
               "{reason}.\nNext step: tell the user Kosha is not responding, and retry this "
               "exact call once they say it is back. Do not work around it.")   # = client.block_text, stdlib-only


def _fail(reason: str) -> None:
    if _pre:
        _exit(2, UNAVAILABLE.format(reason=reason))
    _exit(0)


def _on_alarm(signum, frame) -> None:
    _fail(f"kosha-hook timed out after {DEADLINE:g}s")


def harness_of(payload: dict) -> str:
    name = payload.get("tool_name") or ""
    if name in CLAUDE_TOOLS:
        return "claude_code"
    if name in BOB_TOOLS:
        return "bob"
    return "claude_code" if "transcript_path" in payload else "bob"


def passes_through(name: str) -> bool:
    return name in READ_ONLY_TOOLS or name.startswith(KOSHA_MCP_PREFIX)


def action_id_for(payload: dict, harness: str) -> str:
    tool_use_id = payload.get("tool_use_id")
    if tool_use_id:
        return f"{harness}:{payload.get('session_id') or 'unknown'}:{tool_use_id}"
    import uuid
    return uuid.uuid4().hex


def build_action(payload: dict):
    """PreToolUse payload -> Action, or None if the tool is not kosha's to gate."""
    import shlex
    from datetime import datetime, timezone

    from kosha.system.action import Action

    name = payload.get("tool_name") or ""
    if passes_through(name):
        return None
    harness = harness_of(payload)
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool = (CLAUDE_TOOLS if harness == "claude_code" else BOB_TOOLS).get(name, "other")
    cwd = str(payload.get("cwd") or "")
    argv, targets = [], []
    if tool == "run_command":
        command = str(tool_input.get("command") or "")
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
        if harness == "bob" and isinstance(tool_input.get("cwd"), str) and tool_input["cwd"]:
            cwd = os.path.normpath(os.path.join(cwd, tool_input["cwd"]))   # Bob's cwd is workspace-relative
    else:
        keys = ("file_path", "notebook_path") if harness == "claude_code" else ("path",)
        targets = [os.path.normpath(os.path.join(cwd, str(tool_input[k])))
                   for k in keys if tool_input.get(k)]
    agent = str(payload.get("agent_id") or "main") if harness == "claude_code" else BOB_AGENT
    return Action(action_id=action_id_for(payload, harness),
                  session_id=str(payload.get("session_id") or "unknown"),
                  agent_id=agent, harness=harness, tool=tool, raw=tool_input, argv=argv,
                  cwd=cwd, targets=targets, ts=datetime.now(timezone.utc).isoformat())


def pre_tool_use(payload: dict) -> None:
    action = build_action(payload)
    if action is None:
        _exit(0)
    from kosha.adapters import client
    d = client.decide(action, timeout=CLIENT_TIMEOUT)
    if d.decision == "allow":
        _exit(0)
    _exit(2, client.block_text(d))


def post_tool_use(payload: dict, outcome: str) -> None:
    if payload.get("tool_use_id") and not passes_through(payload.get("tool_name") or ""):
        from kosha.adapters import client
        client.settle(action_id_for(payload, harness_of(payload)), outcome, timeout=CLIENT_TIMEOUT)
    _exit(0)


def main() -> None:
    global _pre
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE)
    try:
        payload = json.loads(sys.stdin.read(MAX_STDIN))
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        event = payload.get("hook_event_name")
        if event in POST_EVENTS:
            _pre = False
            post_tool_use(payload, POST_EVENTS[event])
        elif event == "PreToolUse":
            pre_tool_use(payload)
        else:
            _pre = False
            _exit(0)
    except BaseException as e:   # SystemExit, KeyboardInterrupt included: nothing leaves unhandled
        _fail(f"kosha-hook crashed: {type(e).__name__}: {e}")
    _fail("kosha-hook reached no decision")


if __name__ == "__main__":
    main()
