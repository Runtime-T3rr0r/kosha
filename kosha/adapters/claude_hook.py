"""kosha-hook: Claude Code PreToolUse / PostToolUse / PostToolUseFailure hook.

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
"""
import json
import os
import signal
import sys

DEADLINE = float(os.environ.get("KOSHA_HOOK_DEADLINE", "3.0"))   # seconds, whole hook
CLIENT_TIMEOUT = max(DEADLINE - 0.5, 0.5)                         # HTTP, inside the deadline
MAX_REASON = 2000                                                 # chars written to stderr
MAX_STDIN = 8 * 1024 * 1024

# Claude Code tool -> Action.tool. Anything not listed and not read-only is priced
# as "other" (unknown = expensive); read-only tools are not kosha's call.
CLAUDE_TOOLS = {"Bash": "run_command", "Edit": "edit_file", "MultiEdit": "edit_file",
                "NotebookEdit": "edit_file", "Write": "write_file"}
READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite",
                   "TaskOutput", "ListMcpResourcesTool", "ReadMcpResourceTool"}
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


def _fail(reason: str) -> None:
    if _pre:
        _exit(2, f"Kosha: blocked, {reason} (failing closed).")
    _exit(0)


def _on_alarm(signum, frame) -> None:
    _fail(f"kosha-hook timed out after {DEADLINE:g}s")


def action_id_for(payload: dict, harness: str) -> str:
    tool_use_id = payload.get("tool_use_id")
    if tool_use_id:
        return f"{harness}:{payload.get('session_id') or 'unknown'}:{tool_use_id}"
    import uuid
    return uuid.uuid4().hex


def build_action(payload: dict):
    """Claude Code PreToolUse payload -> Action, or None if the tool is not gated."""
    import shlex
    from datetime import datetime, timezone

    from kosha.system.action import Action

    name = payload.get("tool_name") or ""
    if name in READ_ONLY_TOOLS:
        return None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool = CLAUDE_TOOLS.get(name, "other")
    argv, targets = [], []
    if tool == "run_command":
        command = str(tool_input.get("command") or "")
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
    else:
        targets = [str(tool_input[k]) for k in ("file_path", "notebook_path") if tool_input.get(k)]
    return Action(action_id=action_id_for(payload, "claude_code"),
                  session_id=str(payload.get("session_id") or "unknown"),
                  agent_id=str(payload.get("agent_id") or "main"),
                  harness="claude_code", tool=tool, raw=tool_input, argv=argv,
                  cwd=str(payload.get("cwd") or ""), targets=targets,
                  ts=datetime.now(timezone.utc).isoformat())


def block_message(d) -> str:
    text = " ".join(x for x in (d.reason, d.suggestion or "") if x)
    if d.decision == "ask":
        text += (" Kosha queued this for human approval. Once a human approves it in the "
                 "Kosha dashboard, retry this exact call and it will run.")
    return text


def pre_tool_use(payload: dict) -> None:
    action = build_action(payload)
    if action is None:
        _exit(0)
    from kosha.adapters import client
    d = client.decide(action, timeout=CLIENT_TIMEOUT)
    if d.decision == "allow":
        _exit(0)
    _exit(2, block_message(d))


def post_tool_use(payload: dict, outcome: str) -> None:
    if payload.get("tool_use_id") and (payload.get("tool_name") or "") not in READ_ONLY_TOOLS:
        from kosha.adapters import client
        client.settle(action_id_for(payload, "claude_code"), outcome, timeout=CLIENT_TIMEOUT)
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
