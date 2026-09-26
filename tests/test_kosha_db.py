"""kosha_db: reserve atomicity under real thread races, settle, approvals, expected_writes."""
import sqlite3
import threading
import time
import uuid

import pytest

from kosha.pricing.policy import decide as policy_decide
from kosha.system.action import Action
from kosha.system.kosha_db import FLEET, KoshaDB

L3_CELL = "irrev|local|nopriv"     # price 10 in the M1 table


def act(agent="a1", session="s1", argv=("rm", "x.txt")):
    return Action(action_id=uuid.uuid4().hex, session_id=session, agent_id=agent,
                  harness="replay", tool="run_command", raw={}, argv=list(argv),
                  cwd="/repo", targets=[], ts="2026-09-26T00:00:00Z")


@pytest.fixture
def db(tmp_path):
    return KoshaDB(tmp_path / "k.db")


def slow_policy(*args):
    # widen the read -> decide -> write window so a racy implementation would lose
    time.sleep(0.002)
    return policy_decide(*args)


def race(db, agents, session):
    """Two threads released together, each reserving one L3 (price 10)."""
    barrier = threading.Barrier(len(agents))
    out = [None] * len(agents)

    def run(i):
        a = act(agent=agents[i], session=session)
        barrier.wait()
        out[i] = db.decide(a, 3, L3_CELL, slow_policy)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(len(agents))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def test_agent_cap_race_never_overdraws(tmp_path):
    # cap 15, two concurrent reserves of 10 from the same agent: exactly one may pass
    db = KoshaDB(tmp_path / "k.db", agent_cap=15, fleet_budget=1000)
    for i in range(300):
        s = f"s{i}"
        out = race(db, ["a1", "a1"], s)
        assert sorted(d.decision for d in out) == ["allow", "ask"], out
        assert [d.rule for d in out if d.decision == "ask"] == ["agent_cap"]
        acct = db.session(s)
        assert acct["agents"]["a1"]["spent"] <= 15
        assert acct["fleet"]["spent"] <= 1000


def test_fleet_budget_race_across_agents_never_overdraws(tmp_path):
    # two different subagents racing one fleet budget
    db = KoshaDB(tmp_path / "k.db", agent_cap=50, fleet_budget=15)
    for i in range(300):
        s = f"s{i}"
        out = race(db, ["sub1", "sub2"], s)
        assert sorted(d.decision for d in out) == ["allow", "ask"], out
        assert [d.rule for d in out if d.decision == "ask"] == ["fleet_budget"]
        assert db.session(s)["fleet"]["spent"] <= 15


def test_write_lock_is_taken_at_transaction_start(db):
    # BEGIN IMMEDIATE, not DEFERRED: another writer is locked out before any statement runs
    with db._txn():
        other = sqlite3.connect(db.path, timeout=0, isolation_level=None)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("BEGIN IMMEDIATE")
        other.close()


def test_allow_charges_agent_and_fleet(db):
    d = db.decide(act(), 3, L3_CELL, policy_decide)
    assert (d.decision, d.price, d.fleet_after, d.agent_after) == ("allow", 10, 10, 10)
    s = db.session("s1")
    assert s["fleet"]["spent"] == 10 and s["agents"]["a1"]["spent"] == 10


def test_settle_success_keeps_spend_failure_refunds(db):
    a, b = act(), act()
    db.decide(a, 3, L3_CELL, policy_decide)
    db.decide(b, 3, L3_CELL, policy_decide)
    assert db.settle(a.action_id, "success") == {"status": "confirmed"}
    assert db.settle(b.action_id, "failure") == {"status": "cancelled"}
    assert db.session("s1")["fleet"]["spent"] == 10
    assert db.settle(b.action_id, "success") == {"status": "cancelled"}   # idempotent
    assert db.settle("nope", "success") == {"status": "unknown_action"}


def test_ask_creates_approval_with_bundle_and_approve_once_allows_retry(db):
    # two L3s in window -> third L3 escalates
    for agent in ("sub1", "sub2"):
        assert db.decide(act(agent=agent), 3, L3_CELL, policy_decide).decision == "allow"
    asked = act(agent="sub3", argv=("rm", "y.txt"))
    d = db.decide(asked, 3, L3_CELL, policy_decide)
    assert (d.decision, d.rule) == ("ask", "escalation")
    [ap] = db.pending_approvals()
    assert [b["agent_id"] for b in ap["bundle"]] == ["sub1", "sub2", "sub3"]

    assert db.resolve_approval(ap["id"], "approve_once") == {"status": "approve_once"}
    assert db.pending_approvals() == []
    retry = act(agent="sub3", argv=("rm", "y.txt"))
    d2 = db.decide(retry, 3, L3_CELL, policy_decide)
    assert d2.decision == "allow" and "approval" in d2.reason
    # approval is single-use
    assert db.decide(act(agent="sub3", argv=("rm", "y.txt")), 3, L3_CELL, policy_decide).decision == "ask"


def test_approve_reset_zeroes_window(db):
    db2 = KoshaDB(db.path, agent_cap=15)
    db2.decide(act(), 3, L3_CELL, policy_decide)
    d = db2.decide(act(), 3, L3_CELL, policy_decide)
    assert d.rule == "agent_cap"
    [ap] = db2.pending_approvals()
    db2.resolve_approval(ap["id"], "approve_reset")
    assert db2.session("s1")["agents"]["a1"]["spent"] == 0


def test_deny_approval(db):
    db.decide(act(), 5, "*|*|priv", policy_decide)
    [ap] = db.pending_approvals()
    assert db.resolve_approval(ap["id"], "deny") == {"status": "deny"}
    assert db.resolve_approval(ap["id"], "approve_once") == {"status": "deny"}
    with pytest.raises(ValueError):
        db.resolve_approval(ap["id"], "yolo")


def test_window_expiry_resets_spend(tmp_path):
    db = KoshaDB(tmp_path / "k.db", window_minutes=0)
    db.decide(act(), 3, L3_CELL, policy_decide)
    d = db.decide(act(), 3, L3_CELL, policy_decide)
    assert d.fleet_after == 10          # window rolled before the second decide


def test_events_are_logged_in_order(db):
    a = act()
    db.decide(a, 3, L3_CELL, policy_decide)
    db.settle(a.action_id, "success")
    types = [e["type"] for e in db.events_after(0)]
    assert types == ["action_reserved", "action_confirmed"]


def test_expected_writes_ttl(db):
    db.expect_write("/r/a.py", "x1", ttl_seconds=60)
    db.expect_write("/r/b.py", "x2", ttl_seconds=-1)
    assert db.match_expected("/r/a.py") == "x1"
    assert db.match_expected("/r/a.py") == "x1"      # not consumed within TTL
    assert db.match_expected("/r/b.py") is None      # expired
    assert db.match_expected("/r/c.py") is None
