"""SQLite ledger for koshad: accounts, actions, approvals, events, expected_writes.

The decide-and-reserve step (read window state -> policy -> charge) runs inside one
BEGIN IMMEDIATE transaction, so two concurrent subagents can't both read the same
balance and both slip under a cap. BEGIN IMMEDIATE takes the write lock at the start
of the transaction, not at the first write, which closes the check-then-increment race.

One connection per call: cheap for SQLite, and safe across FastAPI's threadpool,
fs_guard's watcher thread, and test threads.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from kosha.pricing.convergence import touch as convergence_touch
from kosha.pricing.policy import Decision, LedgerState
from kosha.pricing.pricing import load_table, price as cell_price
from kosha.system.action import Action

FLEET = "__fleet__"        # accounts row holding the fleet-wide total for a session
LEASE_GRACE = 0.5          # seconds a write lease survives its action's settle: inotify
                           # delivers events a few ms after the write. A bypass write to the
                           # same path inside this window is accepted (tested, documented).
DEFAULT_DB = Path(__file__).resolve().parents[2] / "kosha.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    session_id   TEXT NOT NULL,
    agent_id     TEXT NOT NULL,           -- FLEET for the fleet-wide row
    cap          REAL NOT NULL,
    spent        REAL NOT NULL DEFAULT 0, -- reserved + confirmed this window
    window_start TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
);
CREATE TABLE IF NOT EXISTS actions(
    action_id  TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    harness    TEXT, tool TEXT,
    argv       TEXT, targets TEXT, raw TEXT, cwd TEXT,
    fingerprint TEXT,
    level      INTEGER, cell TEXT, price REAL,
    decision   TEXT, rule TEXT, reason TEXT, suggestion TEXT,
    status     TEXT NOT NULL,             -- reserved|confirmed|cancelled|pending|denied
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_session ON actions(session_id, created_at);
CREATE TABLE IF NOT EXISTS approvals(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    bundle      TEXT NOT NULL,            -- JSON: window actions + the asked action
    status      TEXT NOT NULL,            -- pending|approve_once|approve_reset|deny|consumed
    note        TEXT,
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS events(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL,
    session_id TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expected_writes(
    path       TEXT NOT NULL,             -- a file, or a directory = lease on everything under it
    action_id  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (path, action_id)         -- concurrent actions can lease the same path
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    # fixed-width UTC format so string comparison == time comparison
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def fingerprint(action: Action) -> str:
    """Identity of 'the same action' across retries: an approved ask is consumed by the
    next /decide with the same session, agent, tool, argv, targets and raw tool input.
    raw must be in it: for db_exec and file tools argv is empty and targets is only the
    alias or path, so without raw an approval of one SQL statement or file body would let
    any other through (and the approved path skips policy, hard deny included)."""
    key = json.dumps([action.session_id, action.agent_id, action.tool, action.argv,
                      sorted(action.targets), action.raw], sort_keys=True, default=str)
    return hashlib.sha256(key.encode()).hexdigest()


class KoshaDB:
    def __init__(self, path: Optional[str | Path] = None, *, fleet_budget: Optional[float] = None,
                 agent_cap: Optional[float] = None, window_minutes: Optional[float] = None):
        """Budgets default to the current price table, so no caller runs on stale numbers."""
        table = load_table()
        self.path = str(path or os.environ.get("KOSHA_DB") or DEFAULT_DB)
        self.fleet_budget = table["fleet_budget"] if fleet_budget is None else fleet_budget
        self.agent_cap = table["agent_cap"] if agent_cap is None else agent_cap
        self.window = timedelta(minutes=table["window_minutes"] if window_minutes is None else window_minutes)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def _txn(self):
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    # --- accounts / window ---

    def _account(self, c, session_id: str, agent_id: str, t: datetime) -> sqlite3.Row:
        cap = self.fleet_budget if agent_id == FLEET else self.agent_cap
        c.execute("INSERT OR IGNORE INTO accounts VALUES (?,?,?,0,?)",
                  (session_id, agent_id, cap, iso(t)))
        return c.execute("SELECT * FROM accounts WHERE session_id=? AND agent_id=?",
                         (session_id, agent_id)).fetchone()

    def _roll_window(self, c, session_id: str, t: datetime) -> str:
        """Reset the session's window if it's older than window_minutes. Returns window_start."""
        fleet = self._account(c, session_id, FLEET, t)
        start = datetime.strptime(fleet["window_start"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if t - start >= self.window:
            self._reset(c, session_id, t)
            return iso(t)
        return fleet["window_start"]

    def _reset(self, c, session_id: str, t: datetime) -> None:
        c.execute("UPDATE accounts SET spent=0, window_start=? WHERE session_id=?",
                  (iso(t), session_id))

    def _charge(self, c, session_id: str, agent_id: str, amount: float) -> None:
        c.execute("UPDATE accounts SET spent=spent+? WHERE session_id=? AND agent_id IN (?,?)",
                  (amount, session_id, agent_id, FLEET))

    def _window_actions(self, c, session_id: str, window_start: str) -> list[sqlite3.Row]:
        """This window's reserved, confirmed and pending actions, oldest first."""
        return c.execute(
            "SELECT action_id, session_id, agent_id, harness, tool, argv, targets, raw, cwd, level, "
            "cell, price, status FROM actions WHERE session_id=? AND created_at>=? AND "
            "status IN ('reserved','confirmed','pending') ORDER BY created_at, rowid",
            (session_id, window_start)).fetchall()

    @staticmethod
    def _window_touches(window: list[sqlite3.Row]) -> list:
        """convergence.touch() per window action, oldest first, rebuilt from the stored
        columns. Pending (asked) actions count as soon as they are asked (policy.LedgerState)."""
        touches = []
        for r in window:
            stored = Action(action_id=r["action_id"], session_id=r["session_id"],
                            agent_id=r["agent_id"], harness=r["harness"], tool=r["tool"],
                            raw=json.loads(r["raw"]), argv=json.loads(r["argv"]), cwd=r["cwd"],
                            targets=json.loads(r["targets"]), ts="")
            t = convergence_touch(stored, r["level"])
            if t is not None:
                touches.append(t)
        return touches

    # --- events ---

    def _event(self, c, type_: str, session_id: Optional[str], payload: dict) -> None:
        c.execute("INSERT INTO events(ts,type,session_id,payload) VALUES (?,?,?,?)",
                  (iso(now()), type_, session_id, json.dumps(payload)))

    def log_event(self, type_: str, session_id: Optional[str], payload: dict) -> None:
        with self._txn() as c:
            self._event(c, type_, session_id, payload)

    def events_after(self, last_id: int, limit: int = 500) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?",
                             (last_id, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    # --- decide + reserve ---

    def decide(self, action: Action, level: int, cell: str,
               policy: Callable[[Action, int, str, LedgerState], Decision],
               write_leases: tuple[tuple[str, float], ...] = ()) -> Decision:
        """Read window state, run policy, and reserve on allow, all under one write lock.

        write_leases: (path, ttl_seconds) pairs recorded in expected_writes on allow, in
        the same transaction, so fs_guard never sees an allowed write before its lease.
        """
        t = now()
        fp = fingerprint(action)
        with self._txn() as c:
            window_start = self._roll_window(c, action.session_id, t)
            fleet = self._account(c, action.session_id, FLEET, t)
            agent = self._account(c, action.session_id, action.agent_id, t)
            window = self._window_actions(c, action.session_id, window_start)
            settled = [r for r in window if r["status"] in ("reserved", "confirmed")]

            approved = c.execute(
                "SELECT id FROM approvals WHERE fingerprint=? AND status IN "
                "('approve_once','approve_reset') ORDER BY id LIMIT 1", (fp,)).fetchone()
            if approved:
                p = cell_price(cell, level=level)
                c.execute("UPDATE approvals SET status='consumed' WHERE id=?", (approved["id"],))
                d = Decision("allow", f"Kosha: allowed, a human approved this action "
                             f"(approval #{approved['id']}).", level, cell, p,
                             fleet["spent"] + p, agent["spent"] + p, "ok")
            else:
                # escalation keeps counting reserved/confirmed only; convergence also sees pending
                state = LedgerState(fleet_spent=fleet["spent"], fleet_budget=fleet["cap"],
                                    agent_spent=agent["spent"], agent_cap=agent["cap"],
                                    recent_actions=[(r["agent_id"], r["level"]) for r in settled],
                                    window_touches=self._window_touches(window))
                d = policy(action, level, cell, state)

            status = {"allow": "reserved", "ask": "pending", "deny": "denied"}[d.decision]
            c.execute("INSERT OR REPLACE INTO actions VALUES "
                      "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (action.action_id, action.session_id, action.agent_id, action.harness,
                       action.tool, json.dumps(action.argv), json.dumps(action.targets),
                       json.dumps(action.raw, default=str), action.cwd, fp, d.level, d.cell,
                       d.price, d.decision, d.rule, d.reason, d.suggestion, status, iso(t)))
            summary = {"action_id": action.action_id, "agent_id": action.agent_id,
                       "tool": action.tool, "argv": action.argv, "targets": action.targets,
                       "decision": asdict(d)}
            if d.decision == "allow":
                self._charge(c, action.session_id, action.agent_id, d.price)
                for path, ttl in write_leases:
                    c.execute("INSERT OR REPLACE INTO expected_writes VALUES (?,?,?)",
                              (path, action.action_id, iso(t + timedelta(seconds=ttl))))
                self._event(c, "action_reserved", action.session_id, summary)
            elif d.decision == "ask":
                # raw is what the human is approving (the SQL, the file body): it has to be shown
                bundle = [{"action_id": r["action_id"], "agent_id": r["agent_id"],
                           "tool": r["tool"], "argv": json.loads(r["argv"]),
                           "raw": json.loads(r["raw"]), "level": r["level"], "cell": r["cell"],
                           "price": r["price"], "status": r["status"]} for r in settled]
                bundle.append({"action_id": action.action_id, "agent_id": action.agent_id,
                               "tool": action.tool, "argv": action.argv, "raw": action.raw,
                               "level": d.level, "cell": d.cell, "price": d.price,
                               "status": "pending"})
                cur = c.execute("INSERT INTO approvals(action_id,session_id,agent_id,fingerprint,"
                                "bundle,status,created_at) VALUES (?,?,?,?,?,'pending',?)",
                                (action.action_id, action.session_id, action.agent_id, fp,
                                 json.dumps(bundle), iso(t)))
                self._event(c, "approval_requested", action.session_id,
                            {**summary, "approval_id": cur.lastrowid, "bundle": bundle})
            else:
                self._event(c, "action_denied", action.session_id, summary)
        return d

    # --- settle ---

    def settle(self, action_id: str, outcome: str) -> dict:
        """success -> confirmed (spend stays); failure -> cancelled (reservation refunded).
        Either way the action's write leases shrink to LEASE_GRACE: fs events can arrive
        a moment after the write that caused them."""
        with self._txn() as c:
            c.execute("UPDATE expected_writes SET expires_at=MIN(expires_at, ?) WHERE action_id=?",
                      (iso(now() + timedelta(seconds=LEASE_GRACE)), action_id))
            row = c.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                return {"status": "unknown_action"}
            if row["status"] != "reserved":
                return {"status": row["status"]}
            if outcome == "success":
                c.execute("UPDATE actions SET status='confirmed' WHERE action_id=?", (action_id,))
                self._event(c, "action_confirmed", row["session_id"], {"action_id": action_id})
                return {"status": "confirmed"}
            c.execute("UPDATE actions SET status='cancelled' WHERE action_id=?", (action_id,))
            self._charge(c, row["session_id"], row["agent_id"], -row["price"])
            self._event(c, "action_cancelled", row["session_id"], {"action_id": action_id})
            return {"status": "cancelled"}

    # --- approvals ---

    def pending_approvals(self) -> list[dict]:
        """Pending approvals with their bundle, and why they were held (the held action's
        rule, reason and suggestion) so a human can decide."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ap.*, a.rule, a.reason, a.suggestion FROM approvals ap "
                "LEFT JOIN actions a ON a.action_id = ap.action_id "
                "WHERE ap.status='pending' ORDER BY ap.id").fetchall()
        return [{"id": r["id"], "action_id": r["action_id"], "session_id": r["session_id"],
                 "agent_id": r["agent_id"], "bundle": json.loads(r["bundle"]),
                 "status": r["status"], "created_at": r["created_at"], "rule": r["rule"],
                 "reason": r["reason"], "suggestion": r["suggestion"]} for r in rows]

    def approval(self, approval_id: int) -> Optional[dict]:
        """One approval's current state (for an adapter waiting on a human), no bundle."""
        with self._conn() as c:
            r = c.execute("SELECT id, action_id, status, note FROM approvals WHERE id=?",
                          (approval_id,)).fetchone()
        return dict(r) if r else None

    def resolve_approval(self, approval_id: int, decision: str, note: Optional[str] = None) -> dict:
        """approve_once: the agent's next identical /decide is allowed and charged.
        approve_reset: same, and the session's window is reset to zero spend.
        deny: the asked action is denied."""
        if decision not in ("approve_once", "approve_reset", "deny"):
            raise ValueError(f"unknown approval decision {decision!r}")
        with self._txn() as c:
            row = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                return {"status": "unknown_approval"}
            if row["status"] != "pending":
                return {"status": row["status"]}
            c.execute("UPDATE approvals SET status=?, note=?, resolved_at=? WHERE id=?",
                      (decision, note, iso(now()), approval_id))
            c.execute("UPDATE actions SET status=? WHERE action_id=?",
                      ("denied" if decision == "deny" else "cancelled", row["action_id"]))
            if decision == "approve_reset":
                self._reset(c, row["session_id"], now())
            self._event(c, "approval_resolved", row["session_id"],
                        {"approval_id": approval_id, "action_id": row["action_id"],
                         "decision": decision, "note": note})
            return {"status": decision}

    # --- sessions ---

    def session(self, session_id: str) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM accounts WHERE session_id=?", (session_id,)).fetchall()
        fleet = next((r for r in rows if r["agent_id"] == FLEET), None)
        return {
            "session_id": session_id,
            "window_start": fleet["window_start"] if fleet else None,
            "fleet": {"spent": fleet["spent"], "budget": fleet["cap"]} if fleet else None,
            "agents": {r["agent_id"]: {"spent": r["spent"], "cap": r["cap"]}
                       for r in rows if r["agent_id"] != FLEET},
        }

    # --- expected_writes (fs_guard) ---

    def expect_write(self, path: str, action_id: str, ttl_seconds: float = 10) -> None:
        """Lease outside /decide (tests, fs_guard's own reverts)."""
        with self._txn() as c:
            c.execute("INSERT OR REPLACE INTO expected_writes VALUES (?,?,?)",
                      (path, action_id, iso(now() + timedelta(seconds=ttl_seconds))))

    def match_expected(self, path: str) -> Optional[str]:
        """action_id of an unexpired lease on path or a directory above it, or None.
        Not consumed: one gated write can fire several fs events (create + modify)."""
        with self._txn() as c:
            c.execute("DELETE FROM expected_writes WHERE expires_at<?", (iso(now()),))
            row = c.execute(
                "SELECT action_id FROM expected_writes WHERE path=? "
                "OR substr(?, 1, length(path) + 1) = path || '/' ORDER BY expires_at DESC LIMIT 1",
                (path, path)).fetchone()
        return row["action_id"] if row else None
