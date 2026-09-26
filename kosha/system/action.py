"""Action: one normalized tool call from any harness. The contract between the
System track (producers: adapters, parser) and the Pricing track (rubric/pricing/policy).
Don't change its shape without telling the other side."""
from dataclasses import dataclass
from typing import Literal

Harness = Literal["bob", "claude_code", "opencode", "replay"]
Tool = Literal["run_command", "edit_file", "write_file", "git", "db_exec", "deploy", "other"]


@dataclass
class Action:
    action_id: str
    session_id: str        # fleet id
    agent_id: str          # main agent or subagent id / mode name
    harness: Harness
    tool: Tool
    raw: dict              # original tool input, untouched
    argv: list[str]        # normalized command, if applicable
    cwd: str
    targets: list[str]     # paths / hosts / db aliases / branches touched
    ts: str                # iso8601
