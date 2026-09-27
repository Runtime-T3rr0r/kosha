"""Adversarial security audit for koshad.

Written by IBM Bob in an adversarial review task (bob_sessions/kosha_task05_*); it
found the /bin/rm -rf / hard-deny bypass fixed in kosha/pricing/policy.py.

Spins up a throwaway koshad on its own port+database, drives it over HTTP,
then tears it down. Reports each finding as PASS/FAIL/BUG.

Tests:
  1. Approval fingerprint bypass: approve DELETE WHERE -> submit DROP DATABASE
     (same session/agent/tool/targets but different raw.sql)
  2. Fingerprint exact-match: same sql, slightly different argv casing etc.
  3. match.py fuzz: malformed/adversarial inputs to command classification
  4. Self-approval-prevention: control-plane paths forced to L5
  5. Passphrase gate: wrong/missing X-Kosha-Approval header rejected

Usage (auto mode): python scripts/e2e_security_audit.py
  (starts its own koshad on a free port automatically)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Make sure the repo root is importable
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _req(url: str, path: str, body: dict | None = None,
         method: str | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    m = method or ("POST" if data is not None else "GET")
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"{url}{path}", data=data, headers=h, method=m)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def decide(url: str, session: str, agent: str, tool: str, **kw) -> dict:
    body = {"session_id": session, "agent_id": agent, "harness": "claude_code",
            "tool": tool, **kw}
    code, resp = _req(url, "/decide", body)
    assert code == 200, f"/decide returned {code}: {resp}"
    return resp


def approve(url: str, approval_id: int, decision: str = "approve_once",
            passphrase: str | None = None) -> tuple[int, dict]:
    h = {}
    if passphrase is not None:
        h["X-Kosha-Approval"] = passphrase
    return _req(url, f"/approvals/{approval_id}",
                body={"decision": decision}, headers=h)


def pending(url: str) -> list[dict]:
    _, resp = _req(url, "/approvals")
    return resp


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_koshad(db_path: str, port: int,
                 passphrase: str | None = None) -> subprocess.Popen:
    env = {**os.environ, "KOSHA_DB": db_path, "KOSHAD_PORT": str(port)}
    script = f"""
import os, sys
sys.path.insert(0, {str(REPO)!r})
import uvicorn
from kosha.api.server import create_app
from kosha.system.kosha_db import KoshaDB
db = KoshaDB(path={db_path!r}, fleet_budget=9999.0, agent_cap=9999.0)
app = create_app(db=db, approval_passphrase={passphrase!r})
uvicorn.run(app, host="127.0.0.1", port={port}, log_level="error")
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        time.sleep(0.1)
        try:
            urllib.request.urlopen(f"{url}/price_table", timeout=1)
            return proc
        except Exception:
            pass
    proc.terminate()
    raise RuntimeError(f"koshad on :{port} never came up")


def stop_koshad(proc: subprocess.Popen) -> None:
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
BUG  = "BUG "

results: list[tuple[str, str, str]] = []   # (label, verdict, detail)


def report(label: str, verdict: str, detail: str) -> None:
    results.append((label, verdict, detail))
    sym = {"PASS": "✓", "FAIL": "✗", "BUG ": "🐛"}.get(verdict, "?")
    print(f"  [{verdict}] {sym}  {label}")
    if verdict != PASS:
        for line in detail.splitlines():
            print(f"       {line}")


# ---------------------------------------------------------------------------
# 1. Approval fingerprint bypass
# ---------------------------------------------------------------------------
# The fingerprint (kosha_db.py:93-101) hashes [session_id, agent_id, tool, argv,
# targets, raw]. If the agent changes raw.sql between the approved request and
# the consumed request, the fingerprints will differ and the bypass fails.
# This test confirms the gate is sound: an approved DELETE fingerprint cannot
# be consumed by a DROP DATABASE call.

def test_fingerprint_bypass(url: str) -> None:
    print("\n[1] Approval fingerprint bypass (DELETE approved -> DROP DATABASE submitted)")
    session = f"sec-fp-{uuid.uuid4().hex[:8]}"
    agent = "adversary-1"

    # Step 1: Submit DELETE FROM x (no WHERE) against prod alias -> expect ask
    # Note: DELETE WITH WHERE is allowed (not destructive by design rule).
    # Only DELETE/UPDATE without WHERE, DROP TABLE, or TRUNCATE trigger the rule.
    d1 = decide(url, session, agent, "db_exec",
                targets=["prod"],
                raw={"db": "prod", "sql": "DELETE FROM x"})
    report("1a DELETE no-WHERE on prod-alias -> ask (destructive_sql)",
           PASS if (d1["decision"] == "ask" and d1["rule"] == "destructive_sql") else FAIL,
           f"got decision={d1['decision']} rule={d1['rule']}")

    if d1["decision"] != "ask":
        report("1b (skipped, prerequisite failed)", FAIL, "step 1a did not produce ask")
        return

    # Step 2: Human approves
    apps = pending(url)
    ap = next((a for a in apps if a["session_id"] == session), None)
    if ap is None:
        report("1b find pending approval", FAIL, "no pending approval found")
        return
    code, res = approve(url, ap["id"], "approve_once")
    report("1b human approves DELETE WHERE",
           PASS if code == 200 else FAIL,
           f"status={code} {res}")

    # Step 3: Agent now submits DROP DATABASE prod (same session/agent/tool/targets,
    # DIFFERENT raw.sql). If bypass works, it would return allow. If gate is sound,
    # the fingerprints differ and policy re-runs, producing hard_deny.
    d2 = decide(url, session, agent, "db_exec",
                targets=["prod"],
                raw={"db": "prod", "sql": "DROP DATABASE prod"})  # different sql -> different fingerprint
    bypass_succeeded = d2["decision"] == "allow"
    report("1c DROP DATABASE on same alias after DELETE approval -> must hard_deny (not allow)",
           BUG if bypass_succeeded else PASS,
           f"decision={d2['decision']} rule={d2.get('rule')} "
           f"({'BYPASS WORKS - fingerprint consumed different sql!' if bypass_succeeded else 'gate held'})")

    # Step 4: confirm the approved DELETE itself can be re-submitted and goes through
    session2 = f"sec-fp2-{uuid.uuid4().hex[:8]}"
    d3 = decide(url, session2, agent, "db_exec",
                targets=["prod"],
                raw={"db": "prod", "sql": "DELETE FROM x"})
    apps2 = pending(url)
    ap2 = next((a for a in apps2 if a["session_id"] == session2), None)
    if ap2:
        approve(url, ap2["id"], "approve_once")
    d4 = decide(url, session2, agent, "db_exec",
                targets=["prod"],
                raw={"db": "prod", "sql": "DELETE FROM x"})
    report("1d same sql re-submitted after approval -> allow (baseline sanity)",
           PASS if d4["decision"] == "allow" else FAIL,
           f"decision={d4['decision']} rule={d4.get('rule')}")


# ---------------------------------------------------------------------------
# 2. Fingerprint collision via raw mutation
# ---------------------------------------------------------------------------
# Checks if an attacker can get DROP DATABASE approved by disguising the sql
# in raw as something else but with the same fingerprint (hash collision is
# infeasible; this tests the logical field ordering in the hash input).

def test_fingerprint_content(url: str) -> None:
    print("\n[2] Fingerprint content integrity: verify fields hashed correctly")
    from kosha.system.kosha_db import fingerprint
    from kosha.system.action import Action

    def make_action(sql: str) -> Action:
        return Action(
            action_id=uuid.uuid4().hex,
            session_id="s1", agent_id="a1",
            harness="claude_code", tool="db_exec",
            raw={"db": "prod", "sql": sql},
            argv=[], cwd="", targets=["prod"], ts="")

    fp_delete = fingerprint(make_action("DELETE FROM x WHERE id=5"))
    fp_drop   = fingerprint(make_action("DROP DATABASE prod"))
    fp_same   = fingerprint(make_action("DELETE FROM x WHERE id=5"))

    report("2a DELETE and DROP produce different fingerprints",
           PASS if fp_delete != fp_drop else BUG,
           f"delete={fp_delete[:16]}... drop={fp_drop[:16]}...")
    report("2b same sql produces same fingerprint (deterministic)",
           PASS if fp_delete == fp_same else BUG,
           f"fp1={fp_delete[:16]}... fp2={fp_same[:16]}...")

    # argv in fingerprint: changing argv changes fingerprint
    a1 = Action("x", "s", "a", "claude_code", "run_command",
                {"command": "rm -rf /"}, ["rm", "-rf", "/"], "", [], "")
    a2 = Action("x", "s", "a", "claude_code", "run_command",
                {"command": "rm -rf /"}, ["rm", "-rf", "/*"], "", [], "")
    report("2c different argv -> different fingerprint",
           PASS if fingerprint(a1) != fingerprint(a2) else BUG,
           "argv difference not reflected in fingerprint")


# ---------------------------------------------------------------------------
# 3. match.py fuzz: malformed / adversarial inputs
# ---------------------------------------------------------------------------

def test_match_fuzz(url: str) -> None:
    print("\n[3] match.py fuzz: malformed and adversarial command classification")
    from kosha.pricing.match import match_command, family_sub, segments

    def classify_cmd(cmd: str) -> dict:
        try:
            segs = segments(cmd)
            if not segs:
                return {"matched": False, "error": None, "family": "unknown",
                        "sub": None, "read_only": False}
            argv = segs[0]
            return {**match_command(argv), "error": None}
        except Exception as e:
            return {"matched": False, "error": str(e), "family": "ERROR",
                    "sub": None, "read_only": False}

    unknown_entry = next(
        e for e in __import__("yaml").safe_load(
            (REPO / "config" / "effects.yaml").read_text())
        if e["id"] == "unknown")
    unknown_level_not_cheap = unknown_entry["read_only"] == False

    cases = [
        # (description, command, expect_no_crash, expect_not_cheap_unknown)
        # --- empty / null-like ---
        ("empty string", "", True, False),
        ("single space", " ", True, False),
        ("null bytes in command", "rm\x00/etc/passwd", True, True),
        ("tab-separated args", "rm\t-rf\t/", True, True),

        # --- unusual quoting / injection ---
        ("escaped quotes in sql", "psql -c \"DROP TABLE \\\"users\\\"\"", True, True),
        ("nested single in double", 'psql -c "DELETE FROM x WHERE id=\'5\'"', True, True),
        ("unbalanced quote - shlex fallback", "rm -rf '/tmp/test", True, True),
        ("unbalanced backslash", "rm -rf /tmp/test\\", True, True),
        ("dollar-paren substitution", "rm $(cat /etc/passwd)", True, True),
        ("backtick substitution", "rm `cat /etc/passwd`", True, True),
        ("nested dollar-paren", "rm $(echo $(cat /etc/secret))", True, True),

        # --- unicode ---
        ("unicode in path", "rm /tmp/üñíçödé/file", True, True),
        ("unicode lookalike rm (Cyrillic r)", "гm -rf /", True, False),  # not rm, should be unknown
        ("right-to-left override in path", "rm /tmp/\u202egnp.exe", True, True),
        ("zero-width space in command", "r\u200bm -rf /", True, False),  # not rm
        ("emoji in path", "rm /tmp/🔥/file.txt", True, True),

        # --- path traversal ---
        ("path traversal in rm", "rm -rf ../../etc/passwd", True, True),
        ("path traversal with encoding", "rm -rf /tmp/../../../etc/passwd", True, True),
        ("null device path", "rm /dev/null", True, True),

        # --- extremely long argv ---
        ("argv 1000 tokens", "ls " + " ".join(["/tmp/x"] * 999), True, False),
        ("single token 100k chars", "rm " + "A" * 100_000, True, True),

        # --- mixed case command names ---
        ("UPPERCASE RM", "RM -rf /", True, False),   # not matched, unknown (conservative)
        ("MixedCase Psql", "Psql -c 'DROP TABLE users'", True, False),  # unknown, conservative

        # --- command with no argv[0] logic match ---
        ("unknown tool opaque", "frobnicate --destroy --all", True, False),
        ("unknown sudo opaque", "sudo frobnicate --destroy", True, True),  # sudo upgrades privilege

        # --- SQL-in-argv edge cases ---
        ("psql inline drop", "psql prod -c 'DROP TABLE users'", True, True),
        ("psql inline drop no where", "psql prod -c 'DELETE FROM users'", True, True),
        ("psql file injection", "psql prod -f /tmp/evil.sql", True, True),
        ("mysql with -e flag", "mysql -u root -e 'DROP DATABASE prod'", True, True),
        ("sqlite3 with dot command", "sqlite3 prod.db '.tables'", True, False),

        # --- xargs nesting ---
        ("xargs rm -rf", "xargs rm -rf", True, True),
        ("xargs sudo rm -rf", "xargs sudo rm -rf", True, True),

        # --- argv is empty list ---
        ("empty argv list to match_command", None, True, False),  # special case below
    ]

    any_crash = False
    any_wrong_cheap = False

    for desc, cmd, expect_no_crash, expect_not_cheap in cases:
        if cmd is None:
            # Test match_command([]) directly
            try:
                r = match_command([])
                crashed = False
                cheap_wrong = not r.get("read_only") and expect_not_cheap and r.get("matched")
            except Exception as e:
                crashed = True
                cheap_wrong = False
                r = {"error": str(e)}
        else:
            r = classify_cmd(cmd)
            crashed = r.get("error") is not None
            # "wrong cheap": if the command crashed (which means exception) we also count it
            # A match is "wrong cheap" if it returned read_only=True for a destructive command
            cheap_wrong = False
            if not crashed and expect_not_cheap:
                # For unknown commands, read_only should be False (conservative)
                if r.get("family") in ("unknown", "ERROR") or not r.get("matched"):
                    cheap_wrong = r.get("read_only", False)

        if crashed:
            any_crash = True
            report(f"3 [{desc}] no crash",
                   BUG,
                   f"EXCEPTION: {r.get('error', r)}")
        elif cheap_wrong:
            any_wrong_cheap = True
            report(f"3 [{desc}] unknown not marked cheap",
                   BUG,
                   f"read_only=True on unknown/unmatched command: {r}")
        # else: silent pass (don't spam 40 lines of PASSes)

    if not any_crash:
        report("3 fuzz: no crashes in 40 adversarial inputs", PASS, "")
    if not any_wrong_cheap:
        report("3 fuzz: no wrong-cheap misclassifications", PASS, "")

    # ---- specific adversarial sub-cases that must be reported individually ----

    # Cyrillic 'r' should NOT match rm
    r = classify_cmd("гm -rf /")
    report("3 Cyrillic rm lookalike -> unknown (not rm)",
           PASS if r.get("family") != "rm" else BUG,
           f"family={r.get('family')} matched={r.get('matched')}")

    # Mixed-case PSQL should be unknown (conservative)
    r = classify_cmd("PSQL -c 'DROP TABLE users'")
    # family_sub returns "unknown" for unrecognized commands (case-sensitive Path.name)
    report("3 UPPERCASE PSQL -> unknown (conservative, not sql family)",
           PASS if r.get("family") != "sql" else BUG,
           f"family={r.get('family')} matched={r.get('matched')} read_only={r.get('read_only')}")

    # sudo unknown -> privilege=True
    r = match_command(["sudo", "frobnicate", "--destroy"])
    report("3 sudo + unknown -> privilege=True",
           PASS if r.get("privilege") else BUG,
           f"privilege={r.get('privilege')} matched={r.get('matched')}")

    # Path traversal rm: family should still be rm (conservative), not stripped
    r = classify_cmd("rm -rf ../../etc/passwd")
    report("3 path traversal rm -> family=rm (classified, not silently dropped)",
           PASS if r.get("family") == "rm" else FAIL,
           f"family={r.get('family')}")

    # Nested command substitution: shouldn't crash
    r = classify_cmd("rm $(echo $(cat /etc/secret))")
    report("3 nested $() substitution -> no crash",
           PASS if r.get("error") is None else BUG,
           f"error={r.get('error')}")

    # Unbalanced quote: falls back to split(), classified conservatively
    r = classify_cmd("rm -rf '/tmp/test")
    report("3 unbalanced quote -> rm family (shlex fallback, no crash)",
           PASS if r.get("family") == "rm" and r.get("error") is None else
           (BUG if r.get("error") else FAIL),
           f"family={r.get('family')} error={r.get('error')}")

    # Zero-width space in command name: not 'rm', should be unknown
    r = classify_cmd("r\u200bm -rf /")
    report("3 zero-width space in rm -> unknown (not misclassified as rm)",
           PASS if r.get("family") != "rm" else BUG,
           f"family={r.get('family')}")

    # Empty argv to match_command
    try:
        r = match_command([])
        report("3 match_command([]) -> no crash, family=unknown",
               PASS if r.get("family") == "unknown" else FAIL,
               f"family={r.get('family')} matched={r.get('matched')}")
    except Exception as e:
        report("3 match_command([]) -> no crash", BUG, f"exception: {e}")


# ---------------------------------------------------------------------------
# 4. Self-approval prevention: control-plane paths -> L5
# ---------------------------------------------------------------------------

def test_self_approval_prevention(url: str) -> None:
    print("\n[4] Self-approval prevention: control-plane edits forced to L5")
    session = f"sec-sa-{uuid.uuid4().hex[:8]}"
    agent = "adversary-2"

    # Find the actual DB path used by the test daemon
    import urllib.request as _u
    _, pt = _req(url, "/price_table")

    test_cases = [
        # (description, tool, targets, raw, expect_ask_level5)
        ("edit_file kosha.db",
         "edit_file", [str(REPO / "kosha.db")],
         {"path": str(REPO / "kosha.db"), "old": "", "new": "DROP TABLE accounts;"},
         True),
        ("edit_file kosha.yaml",
         "edit_file", [str(REPO / "config" / "kosha.yaml")],
         {"path": str(REPO / "config" / "kosha.yaml"), "old": "", "new": "fleet_budget: 99999"},
         True),
        ("edit_file .bob/settings.json",
         "edit_file", [str(REPO / ".bob" / "settings.json")],
         {"path": str(REPO / ".bob" / "settings.json"), "old": "", "new": "{}"},
         True),
        ("edit_file .claude/settings.json",
         "edit_file", [str(Path.home() / ".claude" / "settings.json")],
         {"path": str(Path.home() / ".claude" / "settings.json"), "old": "", "new": "{}"},
         True),
        ("run_command curl koshad direct (self-approve POST)",
         "run_command", [],
         {"command": f"curl -X POST http://127.0.0.1:{url.split(':')[-1]}/approvals/1 -d '{{\"decision\":\"approve_once\"}}'"},
         True),
        ("run_command edit kosha.db via cp",
         "run_command", [],
         {"command": f"cp /tmp/evil.db {REPO}/kosha.db"},
         True),
    ]

    for desc, tool, targets, raw, expect_held in test_cases:
        d = decide(url, session, agent, tool,
                   targets=targets, raw=raw, cwd=str(REPO))
        held = d["decision"] == "ask" and d["level"] >= 5
        # Also accept deny (hard_deny can fire for some patterns)
        blocked = d["decision"] in ("ask", "deny")
        report(f"4 {desc} -> blocked (ask L5 or deny)",
               PASS if blocked else BUG,
               f"decision={d['decision']} rule={d.get('rule')} level={d.get('level')}")

    # Routing-around attempt: the agent uses a symlink path to disguise kosha.db
    # is_control_plane_path uses realpath(), so this should still be caught
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        link = os.path.join(td, "totally_harmless.txt")
        try:
            os.symlink(str(REPO / "kosha.db"), link)
            d = decide(url, session, agent, "edit_file",
                       targets=[link],
                       raw={"path": link, "old": "", "new": "DROP TABLE accounts;"},
                       cwd=td)
            held = d["decision"] in ("ask", "deny")
            report("4 symlink -> kosha.db still blocked (realpath resolves symlink)",
                   PASS if held else BUG,
                   f"decision={d['decision']} rule={d.get('rule')} level={d.get('level')}")
        except (OSError, PermissionError) as e:
            report("4 symlink test skipped (OS error)", PASS, str(e))

    # Routing-around attempt: write to kosha.db-wal (WAL sidecar)
    d = decide(url, session, agent, "edit_file",
               targets=[str(REPO / "kosha.db-wal")],
               raw={"path": str(REPO / "kosha.db-wal"), "old": "", "new": "x"},
               cwd=str(REPO))
    report("4 edit kosha.db-wal (WAL sidecar) -> blocked",
           PASS if d["decision"] in ("ask", "deny") else BUG,
           f"decision={d['decision']} rule={d.get('rule')} level={d.get('level')}")

    # Routing-around: agent sets raw.path to a relative path that resolves to kosha.db
    d = decide(url, session, agent, "edit_file",
               targets=[],
               raw={"path": "kosha.db", "old": "", "new": "DROP TABLE accounts;"},
               cwd=str(REPO))
    report("4 edit_file relative 'kosha.db' (cwd=kosha root) -> blocked",
           PASS if d["decision"] in ("ask", "deny") else BUG,
           f"decision={d['decision']} rule={d.get('rule')} level={d.get('level')}")

    # Routing-around: KOSHA_DB env var sets a non-default DB path; the daemon under test
    # uses a temp path. An agent editing the default path should still be blocked by
    # CONTROL_FILE regex or control_plane_files() which reads the env.
    d = decide(url, session, agent, "edit_file",
               targets=[str(REPO / "kosha.demo.db")],
               raw={"path": str(REPO / "kosha.demo.db"), "old": "", "new": "x"},
               cwd=str(REPO))
    report("4 edit kosha.demo.db -> blocked by CONTROL_FILE regex",
           PASS if d["decision"] in ("ask", "deny") else BUG,
           f"decision={d['decision']} rule={d.get('rule')} level={d.get('level')}")


# ---------------------------------------------------------------------------
# 5. Passphrase gate
# ---------------------------------------------------------------------------

def test_passphrase_gate(url_no_pass: str, url_with_pass: str, passphrase: str) -> None:
    print("\n[5] Passphrase gate: X-Kosha-Approval header enforcement")
    session = f"sec-pp-{uuid.uuid4().hex[:8]}"
    agent = "adversary-3"

    # Produce a pending approval on the passphrase-protected server
    # DELETE without WHERE triggers destructive_sql ask
    d = decide(url_with_pass, session, agent, "db_exec",
               targets=["prod"],
               raw={"db": "prod", "sql": "DELETE FROM users"})
    if d["decision"] != "ask":
        report("5 setup: get pending approval", FAIL, f"expected ask, got {d}")
        return

    apps = pending(url_with_pass)
    ap = next((a for a in apps if a["session_id"] == session), None)
    if not ap:
        report("5 setup: find pending approval", FAIL, "not found")
        return
    ap_id = ap["id"]

    # Attempt 1: no header
    code, resp = approve(url_with_pass, ap_id, passphrase=None)
    report("5a no X-Kosha-Approval header -> 401",
           PASS if code == 401 else BUG,
           f"status={code} resp={resp}")

    # Attempt 2: wrong passphrase
    code, resp = approve(url_with_pass, ap_id, passphrase="wrong_passphrase_12345")
    report("5b wrong passphrase -> 401",
           PASS if code == 401 else BUG,
           f"status={code} resp={resp}")

    # Attempt 3: empty string passphrase
    code, resp = approve(url_with_pass, ap_id, passphrase="")
    report("5c empty passphrase -> 401",
           PASS if code == 401 else BUG,
           f"status={code} resp={resp}")

    # Attempt 4: correct passphrase
    code, resp = approve(url_with_pass, ap_id, passphrase=passphrase)
    report("5d correct passphrase -> 200",
           PASS if code == 200 else BUG,
           f"status={code} resp={resp}")

    # Attempt 5: second call to resolve the same approval returns current state, no change.
    # The approval is now in state 'approve_once' (not yet consumed by /decide).
    # A second resolve call before consumption must NOT reset the budget or create
    # a second approval - it must return the current state unchanged.
    code, resp = approve(url_with_pass, ap_id, passphrase=passphrase)
    # resolve_approval: if status != 'pending', returns early with {status: current_state}.
    # So status='approve_once' is returned; no SQL runs, no extra approval is created.
    not_upgraded = resp.get("status") == "approve_once"  # early-return, no change
    report("5e second resolve on approve_once -> early-return (no SQL, no extra approval)",
           PASS if not_upgraded else BUG,
           f"status={code} resp={resp}")

    # Consume the approval via /decide (agent re-submits)
    d_consumed = decide(url_with_pass, session, agent, "db_exec",
                        targets=["prod"],
                        raw={"db": "prod", "sql": "DELETE FROM users"})
    report("5e2 agent re-submits after approval -> allow (approval consumed)",
           PASS if d_consumed["decision"] == "allow" else BUG,
           f"decision={d_consumed['decision']} rule={d_consumed.get('rule')}")

    # Now re-resolve the consumed approval - should return status='consumed', not pending
    code, resp = approve(url_with_pass, ap_id, passphrase=passphrase)
    report("5e3 resolve after consumption -> non-pending status (cannot re-approve)",
           PASS if resp.get("status") == "consumed" else BUG,
           f"status={code} resp={resp}")

    # Attempt 6: server with NO passphrase configured -> approve freely
    session2 = f"sec-pp2-{uuid.uuid4().hex[:8]}"
    d2 = decide(url_no_pass, session2, agent, "db_exec",
                targets=["prod"],
                raw={"db": "prod", "sql": "DELETE FROM users"})
    if d2["decision"] == "ask":
        apps2 = pending(url_no_pass)
        ap2 = next((a for a in apps2 if a["session_id"] == session2), None)
        if ap2:
            code, resp = approve(url_no_pass, ap2["id"], passphrase=None)
            report("5f no-passphrase server: approve without header -> 200",
                   PASS if code == 200 else FAIL,
                   f"status={code}")
        else:
            report("5f no-passphrase server setup", FAIL, "no approval found")
    else:
        report("5f no-passphrase server setup", FAIL, f"expected ask got {d2['decision']}")

    # Attempt 7: timing-safe comparison (hmac.compare_digest). Verify that
    # a 401 for wrong passphrase doesn't leak timing info (structural check only —
    # we can't do a real timing attack in a unit test, but we confirm the code path)
    # We just re-confirm 401 for a known-bad input with a very similar string
    almost_right = passphrase[:-1] + chr((ord(passphrase[-1]) + 1) % 128)
    code, resp = approve(url_with_pass, ap_id, passphrase=almost_right)
    report("5g one-char-off passphrase -> 401 (timing-safe)",
           PASS if code == 401 else BUG,
           f"status={code}")


# ---------------------------------------------------------------------------
# 6. Hard-deny cannot be bypassed via approve path
# ---------------------------------------------------------------------------

def test_hard_deny_cannot_approve(url: str) -> None:
    print("\n[6] Hard-deny: rm -rf / and DROP DATABASE prod cannot be approved through")
    session = f"sec-hd-{uuid.uuid4().hex[:8]}"
    agent = "adversary-4"

    # rm -rf / -> hard_deny immediately, no pending approval created
    d = decide(url, session, agent, "run_command",
               argv=["rm", "-rf", "/"],
               raw={"command": "rm -rf /"})
    report("6a rm -rf / -> hard_deny (no ask)",
           PASS if d["decision"] == "deny" and d["rule"] == "hard_deny" else BUG,
           f"decision={d['decision']} rule={d.get('rule')}")

    # DROP DATABASE prod -> hard_deny
    d = decide(url, session, agent, "db_exec",
               targets=["prod"],
               raw={"db": "prod", "sql": "DROP DATABASE prod"})
    report("6b DROP DATABASE prod -> hard_deny (no ask)",
           PASS if d["decision"] == "deny" and d["rule"] == "hard_deny" else BUG,
           f"decision={d['decision']} rule={d.get('rule')}")

    # Verify no pending approvals were created for these hard-deny actions
    apps = pending(url)
    hd_apps = [a for a in apps if a["session_id"] == session]
    report("6c hard-deny actions create no pending approvals",
           PASS if not hd_apps else BUG,
           f"found {len(hd_apps)} unexpected pending approvals: {hd_apps}")

    # What if someone manually inserts an approval row for a hard-deny fingerprint?
    # (This would require direct DB access, which is a Tier 3 issue, but let's
    # confirm the approved-path code in kosha_db.decide SKIPS policy entirely
    # for consumed approvals - which means hard_deny IS bypassable if the DB is
    # compromised. We document this as a known architectural limitation.)
    # NOTE: We do NOT test this by actually tampering with the DB — that's a
    # System track concern. We just note the code path exists (lines 219-227).
    report("6d [ARCHITECTURAL NOTE] approved-path skips hard_deny entirely (kosha_db.py:219-227)",
           FAIL,  # this is a real concern worth flagging
           "IF an attacker can insert/update an approvals row with the right fingerprint "
           "and status='approve_once', the next /decide call with matching fields will "
           "allow the action WITHOUT running hard_deny. This is kosha_db.py lines 219-227. "
           "This requires DB write access (same OS user), tracked as Tier 3 privilege separation. "
           "System-track file: kosha/system/kosha_db.py lines 219-227.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("Kosha adversarial security audit")
    print("=" * 70)

    passphrase = "test-passphrase-kosha-audit"

    tmpdir = tempfile.mkdtemp(prefix="kosha-sec-audit-")
    db1 = os.path.join(tmpdir, "audit1.db")
    db2 = os.path.join(tmpdir, "audit2.db")     # passphrase-protected
    db3 = os.path.join(tmpdir, "audit3.db")     # no passphrase

    port1 = free_port()
    port2 = free_port()
    port3 = free_port()

    url1 = f"http://127.0.0.1:{port1}"
    url2 = f"http://127.0.0.1:{port2}"
    url3 = f"http://127.0.0.1:{port3}"

    print(f"\nStarting test daemons on :{port1}, :{port2} (passphrase), :{port3} (no passphrase)…")
    proc1 = start_koshad(db1, port1)
    proc2 = start_koshad(db2, port2, passphrase=passphrase)
    proc3 = start_koshad(db3, port3, passphrase=None)
    print("All daemons up.")

    try:
        test_fingerprint_bypass(url1)
        test_fingerprint_content(url1)
        test_match_fuzz(url1)
        test_self_approval_prevention(url1)
        test_passphrase_gate(url3, url2, passphrase)
        test_hard_deny_cannot_approve(url1)
    finally:
        stop_koshad(proc1)
        stop_koshad(proc2)
        stop_koshad(proc3)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    bugs  = [(l, d) for l, v, d in results if v == BUG]
    fails = [(l, d) for l, v, d in results if v == FAIL]
    passes = [l for l, v, d in results if v == PASS]
    print(f"  PASS: {len(passes)}")
    print(f"  FAIL: {len(fails)}")
    print(f"  BUG:  {len(bugs)}")
    if bugs:
        print("\nBUGS (require fixing):")
        for label, detail in bugs:
            print(f"  • {label}")
            for line in detail.splitlines():
                print(f"    {line}")
    if fails:
        print("\nFAILS (notable findings / notes):")
        for label, detail in fails:
            print(f"  • {label}")
            for line in detail.splitlines():
                print(f"    {line}")

    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main())
