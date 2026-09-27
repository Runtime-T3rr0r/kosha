"""kosha-mcp: Bob's MCP adapter (stdio). Exposes the gated tools, asks koshad before
running anything, executes only on allow, and settles afterwards.

Identity: Bob's MCP calls carry no agent identity, so the default is one kosha-mcp
instance per Bob custom mode, registered with `--agent <mode slug>` and restricted to
that mode with the MCP entry's `groups` field. The instance name is the agent id.

Every path to execution goes through client.decide(); if koshad is unreachable the
client fails closed and nothing runs.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from kosha.adapters import client
from kosha.system import resolvers
from kosha.system.action import Action

IDENTITY_MODE = os.environ.get("KOSHA_IDENTITY_MODE", "per_mode_instance")
KOSHA_ROOT = Path(__file__).resolve().parents[2]
COMMAND_TIMEOUT = 270          # seconds; Bob's own execute_command max
APPROVAL_WAIT = 1500           # seconds a held call waits for a human (Bob's MCP timeout: 1800s)
MAX_OUTPUT = 20_000            # characters returned to the agent per stream
MAX_ROWS = 200


def resolve_agent_id(raw_request: dict, server_instance_name: str) -> str:
    if IDENTITY_MODE == "inline":
        return ((raw_request.get("_meta") or {}).get("agent_id")
                or (raw_request.get("arguments") or {}).get("agent")
                or server_instance_name)
    return server_instance_name  # one kosha-mcp instance per Bob custom mode


# Bob never forwards an MCP server's `instructions` to the model (its MCP client stores
# them, nothing reads them), so the gating rules ride on every tool description, the
# one channel Bob always shows. The mode's roleDefinition repeats them in full.
GATING = (" Gated by Kosha: each call is priced by its risk against a budget shared by the whole "
          "agent fleet; read-only calls are free, and Kosha, not you, decides what is too risky, so "
          "don't ration normal work. A call that needs human approval waits while a human reviews it "
          "(this can take minutes): just wait, you then get the real result, or KOSHA DENIED BY A "
          "HUMAN with their note. A result starting with KOSHA HELD FOR HUMAN APPROVAL means nobody "
          "decided in time and nothing ran: tell the user and, once they say it is approved, retry "
          "this exact call. KOSHA DENIED means retrying will not help: re-plan. Never work around a "
          "block with other tools.")


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {k: {"type": "string", "description": d}
                                             for k, d in props.items()},
            "required": required}


TOOLS = [
    types.Tool(name="run_command", description="Run a shell command in the workspace. "
               "Use this for every command; it is priced and may need human approval." + GATING,
               input_schema=_schema({"command": "Shell command line",
                                     "cwd": "Working directory, relative to the workspace"},
                                    ["command"])),
    types.Tool(name="edit_file", description="Replace one exact occurrence of `old` with `new` in a file." + GATING,
               input_schema=_schema({"path": "File path, relative to the workspace",
                                     "old": "Exact text to replace (must occur exactly once)",
                                     "new": "Replacement text"}, ["path", "old", "new"])),
    types.Tool(name="write_file", description="Create or overwrite a file with `content`." + GATING,
               input_schema=_schema({"path": "File path, relative to the workspace",
                                     "content": "Full file content"}, ["path", "content"])),
    types.Tool(name="git", description="Run a git command, e.g. args='push origin main'." + GATING,
               input_schema=_schema({"args": "Arguments after `git`"}, ["args"])),
    types.Tool(name="db_exec", description="Run SQL against a configured database alias (e.g. dev, prod)." + GATING,
               input_schema=_schema({"db": "Database alias from config/kosha.yaml",
                                     "sql": "SQL to run"}, ["db", "sql"])),
    types.Tool(name="deploy", description="Deploy the workspace to a target environment." + GATING,
               input_schema=_schema({"target": "Environment name, e.g. staging or prod"}, ["target"])),
]
ACTION_TOOLS = {t.name for t in TOOLS}

# The approval queue, read-only, from inside Bob: an agent (or the human, via the agent)
# can see what is held and why. Approving is not a tool: it needs the human's approval
# passphrase, which must never enter the agent's chat, so humans approve on the /ui page.
# Not priced: reading the queue isn't an action on the workspace.
CONTROL_TOOLS = [
    types.Tool(name="kosha_review",
               description="Show what Kosha is holding for human approval: each held action and its "
                           "bundle (everything the fleet did this window, in order). Read-only. Only "
                           "the human can approve, on the Kosha approval page.",
               input_schema={"type": "object", "properties": {}}),
]
TOOLS = TOOLS + CONTROL_TOOLS
TOOL_NAMES = {t.name for t in TOOLS}
DECLARED = {t.name: set(t.input_schema["properties"]) for t in TOOLS}


class Gateway:
    """Everything except the MCP wire protocol, so tests can drive it directly."""

    def __init__(self, agent: str, session: str, workspace: str,
                 approval_wait: Optional[float] = None, poll: float = 1.0):
        """approval_wait: seconds a held call waits for the human's decision before giving
        up and returning KOSHA HELD (0 = never wait). Default KOSHA_APPROVAL_WAIT, else
        APPROVAL_WAIT; keep it under Bob's MCP timeout minus the longest command."""
        self.agent = agent
        self.session = session
        self.workspace = os.path.realpath(workspace)
        self.approval_wait = float(os.environ.get("KOSHA_APPROVAL_WAIT", APPROVAL_WAIT)
                                   if approval_wait is None else approval_wait)
        self.poll = poll

    def _path(self, p: str) -> str:
        return os.path.realpath(os.path.join(self.workspace, os.path.expanduser(p)))

    def build_action(self, name: str, args: dict, agent_id: str) -> Action:
        cwd = self._path(args.get("cwd") or ".") if name == "run_command" else self.workspace
        argv, targets = [], []
        if name == "run_command":
            argv = _split(args.get("command", ""))
        elif name == "git":
            argv = ["git", *_split(args.get("args", ""))]
        elif name in ("edit_file", "write_file"):
            targets = [self._path(args.get("path", ""))]
        elif name == "db_exec":
            targets = [str(args.get("db", ""))]
        elif name == "deploy":
            argv, targets = ["deploy", str(args.get("target", ""))], [str(args.get("target", ""))]
        # Only declared arguments reach koshad: an agent must not be able to smuggle
        # fields policy reads (e.g. approval_token) through a tool call.
        raw = {k: v for k, v in args.items() if k in DECLARED.get(name, ())}
        return Action(action_id=uuid.uuid4().hex, session_id=self.session, agent_id=agent_id,
                      harness="bob", tool=name if name in TOOL_NAMES else "other",
                      raw=raw, argv=argv, cwd=cwd, targets=targets,
                      ts=datetime.now(timezone.utc).isoformat())

    def execute(self, action: Action) -> tuple[bool, str]:
        a = action.raw
        if action.tool == "run_command":
            return _run(a.get("command", ""), action.cwd, shell=True)
        if action.tool == "git":
            return _run(action.argv, action.cwd)
        if action.tool == "edit_file":
            path = action.targets[0]
            text = Path(path).read_text()
            n = text.count(a.get("old", ""))
            if not a.get("old") or n != 1:
                return False, f"edit_file: `old` must occur exactly once in {a.get('path')}, found {n}"
            Path(path).write_text(text.replace(a["old"], a.get("new", ""), 1))
            return True, f"edited {a.get('path')}"
        if action.tool == "write_file":
            path = Path(action.targets[0])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(a.get("content", ""))
            return True, f"wrote {a.get('path')} ({len(a.get('content', ''))} chars)"
        if action.tool == "db_exec":
            return _db_exec(str(a.get("db", "")), str(a.get("sql", "")))
        if action.tool == "deploy":
            cmd = _split(os.environ.get("KOSHA_DEPLOY_CMD", "./deploy.sh"))
            return _run([*cmd, str(a.get("target", ""))], self.workspace)
        return False, f"unknown tool {action.tool}"

    def wait_for_human(self, action_id: str) -> Optional[dict]:
        """The approval created for this held action, once a human has decided it; None if
        nobody decided within approval_wait (koshad unreachable meanwhile counts as waiting)."""
        deadline = time.monotonic() + self.approval_wait
        approval_id = None
        while time.monotonic() < deadline:
            try:
                if approval_id is None:
                    approval_id = next((a["id"] for a in client.approvals()
                                        if a["action_id"] == action_id), None)
                if approval_id is not None:
                    state = client.approval_status(approval_id)
                    if state["status"] != "pending":
                        return state
            except Exception:
                pass
            time.sleep(min(self.poll, max(deadline - time.monotonic(), 0)))
        return None

    def call(self, name: str, raw_request: dict) -> tuple[bool, str]:
        """(is_error, text). Nothing executes unless koshad said allow."""
        if name not in TOOL_NAMES:
            return True, f"kosha-mcp: unknown tool {name}"
        args = raw_request.get("arguments") or {}
        if name == "kosha_review":
            return _review()
        action = self.build_action(name, args, resolve_agent_id(raw_request, self.agent))
        d = client.decide(action)
        if d.decision == "ask" and d.rule != "fail_closed" and self.approval_wait > 0:
            # hold the call open (Bob's chat keeps waiting) until the human decides
            human = self.wait_for_human(action.action_id)
            if human and human["status"] == "deny":
                return True, client.denied_by_human_text(human.get("note"))
            if human and human["status"] in ("approve_once", "approve_reset"):
                # the exact same call again: it matches the approval, which lets it through once
                action = dataclasses.replace(action, action_id=uuid.uuid4().hex,
                                             ts=datetime.now(timezone.utc).isoformat())
                d = client.decide(action)
        if d.decision != "allow":
            return True, client.block_text(d)
        try:
            ok, out = self.execute(action)
        except Exception as e:
            ok, out = False, f"{name} failed: {type(e).__name__}: {e}"
        client.settle(action.action_id, "success" if ok else "failure")
        return not ok, out


def _what(entry: dict) -> str:
    raw = entry.get("raw") or {}
    return " ".join(map(str, entry.get("argv") or [raw.get("sql") or raw.get("path")
                                                   or raw.get("command") or raw.get("args") or ""]))


def _review() -> tuple[bool, str]:
    try:
        pending = client.approvals()
    except Exception as e:
        return True, f"Kosha is not responding ({type(e).__name__}); nothing to show."
    if not pending:
        return False, "Nothing is held for approval."
    lines = []
    for ap in pending:
        held = ap["bundle"][-1]
        lines.append(f"#{ap['id']}  HELD: {ap['agent_id']} {held['tool']} {_what(held)} (L{held['level']})")
        lines.append("   this window, in order:")
        for e in ap["bundle"]:
            flag = "   <- held" if e["status"] == "pending" else ""
            lines.append(f"     {e['agent_id']:15} L{e['level']}  {e['tool']:11} {_what(e)}{flag}")
    return False, "\n".join(lines)


def _split(s: str) -> list[str]:
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n... [{len(s) - MAX_OUTPUT} chars truncated]"


def _run(cmd, cwd: str, shell: bool = False) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True,
                           timeout=COMMAND_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {COMMAND_TIMEOUT}s"
    out = f"exit {p.returncode}"
    if p.stdout:
        out += f"\n--- stdout\n{_clip(p.stdout)}"
    if p.stderr:
        out += f"\n--- stderr\n{_clip(p.stderr)}"
    return p.returncode == 0, out


def _db_exec(alias: str, sql: str) -> tuple[bool, str]:
    """sqlite only (the demo's dev and prod aliases). Never echoes the URL: it may
    carry credentials for a non-sqlite DB."""
    spec = (resolvers.load_config().get("databases") or {}).get(alias)
    if not spec:
        return False, f"db_exec: unknown database alias {alias!r} (see config/kosha.yaml)"
    url = str(spec.get("url", ""))
    if not url.startswith("sqlite:///"):
        return False, f"db_exec: alias {alias!r} is not a sqlite URL; only sqlite is supported"
    path = url[len("sqlite:///"):]
    path = path if os.path.isabs(path) else str(KOSHA_ROOT / path)
    conn = sqlite3.connect(path)
    try:
        if sql.lstrip().lower().startswith(("select", "with", "pragma")):
            rows = conn.execute(sql).fetchmany(MAX_ROWS + 1)
            lines = [" | ".join(map(str, r)) for r in rows[:MAX_ROWS]]
            more = "\n... more rows" if len(rows) > MAX_ROWS else ""
            return True, "\n".join(lines) + more if lines else "(no rows)"
        before = conn.total_changes
        conn.executescript(sql)
        conn.commit()
        return True, f"ok, {conn.total_changes - before} row(s) changed"
    finally:
        conn.close()


def build_server(gw: Gateway) -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=TOOLS)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            meta = params.meta or {}
            raw = {"name": params.name, "arguments": params.arguments or {},
                   "_meta": meta if isinstance(meta, dict) else meta.model_dump()}
            is_error, text = await anyio.to_thread.run_sync(gw.call, params.name, raw)
        except Exception as e:  # never let an adapter bug look like a success
            is_error, text = True, f"kosha-mcp internal error, nothing was run: {type(e).__name__}: {e}"
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                    is_error=is_error)

    return Server(f"kosha-mcp-{gw.agent}", version="0.0.1",
                  instructions="Use these tools for every command, file edit, git, database "
                               "and deploy step." + GATING,   # for MCP clients that read it; Bob doesn't
                  on_list_tools=list_tools, on_call_tool=call_tool)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kosha-mcp")
    ap.add_argument("--agent", default=os.environ.get("KOSHA_AGENT_ID"),
                    help="agent id = the Bob custom mode this instance serves")
    ap.add_argument("--session", default=os.environ.get("KOSHA_SESSION", "bob"),
                    help="fleet id; give every instance of one fleet the same value")
    ap.add_argument("--workspace", default=os.environ.get("KOSHA_WORKSPACE", os.getcwd()))
    a = ap.parse_args(argv)
    if not a.agent:
        ap.error("--agent (or KOSHA_AGENT_ID) is required in per_mode_instance mode")
    gw = Gateway(a.agent, a.session, a.workspace)
    server = build_server(gw)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main(sys.argv[1:])
