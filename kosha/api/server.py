"""koshad: FastAPI + SQLite daemon on 127.0.0.1:8765.

Adapters only call /decide and /settle (through kosha/adapters/client.py).
/approvals, /sessions, /stream, /price_table are dashboard-only.
"""
from __future__ import annotations

import asyncio
import json
import os
import typing
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from kosha.pricing import policy
from kosha.pricing.pricing import load_table
from kosha.system import parser, resolvers
from kosha.system.action import Action, Harness, Tool
from kosha.system.kosha_db import KoshaDB

TOOLS = set(typing.get_args(Tool))
HARNESSES = set(typing.get_args(Harness))
UNKNOWN_LEVEL, UNKNOWN_CELL = 4, "irrev|shared|nopriv"


def coerce_action(body: dict) -> Action:
    """Malformed payloads never error: missing fields get safe defaults, an unknown tool
    becomes "other", and classification then prices it as unknown (expensive)."""
    def strs(v):
        return [str(x) for x in v] if isinstance(v, list) else []

    tool = body.get("tool")
    harness = body.get("harness")
    return Action(
        action_id=str(body.get("action_id") or uuid.uuid4().hex),
        session_id=str(body.get("session_id") or "unknown"),
        agent_id=str(body.get("agent_id") or "unknown"),
        harness=harness if harness in HARNESSES else "replay",
        tool=tool if tool in TOOLS else "other",
        raw=body.get("raw") if isinstance(body.get("raw"), dict) else {},
        argv=strs(body.get("argv")),
        cwd=str(body.get("cwd") or ""),
        targets=strs(body.get("targets")),
        ts=str(body.get("ts") or datetime.now(timezone.utc).isoformat()),
    )


FILE_LEASE, COMMAND_LEASE = 30.0, 300.0   # seconds; settle shrinks either to a short grace


def write_leases(action: Action) -> tuple[tuple[str, float], ...]:
    """Paths fs_guard should accept writes under if this action is allowed.

    File tools lease their exact paths. Commands can write anywhere, so they lease
    their cwd: a native write under that directory while the command runs is
    indistinguishable from the command's own and is accepted (documented gap).
    db_exec leases nothing; the DB file is fs_guard's ignore list's business.
    """
    if action.tool in ("edit_file", "write_file"):
        paths = [*action.targets, *(v for k, v in (action.raw or {}).items()
                                    if k in ("path", "file_path") and isinstance(v, str))]
        out = {resolvers.abspath(p, action.cwd) for p in paths}
        return tuple((p, FILE_LEASE) for p in sorted(x for x in out if x))
    if action.tool == "db_exec" or not action.cwd:
        return ()
    return ((resolvers.abspath(action.cwd, "/"), COMMAND_LEASE),)


def classify(action: Action) -> tuple[int, str]:
    c = parser.classify(action)
    return c.level, c.cell


class Settle(BaseModel):
    action_id: str
    outcome: typing.Literal["success", "failure"]


class Resolve(BaseModel):
    decision: typing.Literal["approve_once", "approve_reset", "deny"]
    note: Optional[str] = None


def create_app(db: Optional[KoshaDB] = None) -> FastAPI:
    table = load_table()
    if db is None:
        db = KoshaDB()
    app = FastAPI(title="koshad")
    app.state.db = db

    @app.post("/decide")
    def decide(body: dict = Body(...)) -> dict:
        action = coerce_action(body)
        try:
            level, cell = classify(action)
        except Exception:
            level, cell = UNKNOWN_LEVEL, UNKNOWN_CELL
        return asdict(db.decide(action, level, cell, policy.decide, write_leases(action)))

    @app.post("/settle")
    def settle(s: Settle) -> dict:
        return db.settle(s.action_id, s.outcome)

    @app.get("/approvals")
    def approvals() -> list[dict]:
        return db.pending_approvals()

    @app.post("/approvals/{approval_id}")
    def resolve(approval_id: int, r: Resolve) -> dict:
        out = db.resolve_approval(approval_id, r.decision, r.note)
        if out["status"] == "unknown_approval":
            raise HTTPException(404, "unknown approval")
        return out

    @app.get("/sessions/{session_id}")
    def session(session_id: str) -> dict:
        return db.session(session_id)

    @app.get("/stream")
    async def stream(last_id: int = 0, once: bool = False):
        async def gen():
            nonlocal last_id
            while True:
                for e in db.events_after(last_id):
                    last_id = e["id"]
                    yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
                if once:
                    return
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/price_table")
    def price_table() -> dict:
        return table

    return app


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("KOSHAD_PORT", 8765)))


if __name__ == "__main__":
    main()
