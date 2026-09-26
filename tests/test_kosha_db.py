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


def race(db, agents, session, same_target=False):
    """Two threads released together, each reserving one L3 (price 10)."""
    barrier = threading.Barrier(len(agents))
    out = [None] * len(agents)

    def run(i):
        # distinct targets per racer (unless the test wants a collision): same-target
        # racers are the convergence rule's business, tested separately
        a = act(agent=agents[i], session=session,
                argv=("rm", "x.txt" if same_target else f"x{i}.txt"))
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
    for agent in ("sub1", "sub2"):   # different targets: no convergence, escalation only
        assert db.decide(act(agent=agent, argv=("rm", f"{agent}.txt")), 3, L3_CELL,
                         policy_decide).decision == "allow"
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


# --- write leases (fs_guard) ---

def test_allow_records_write_leases_in_the_same_transaction(db):
    a = act()
    db.decide(a, 3, L3_CELL, policy_decide, write_leases=(("/r/app.py", 30),))
    assert db.match_expected("/r/app.py") == a.action_id


def test_ask_and_deny_record_no_leases(db):
    db.decide(act(), 5, "*|*|priv", policy_decide, write_leases=(("/r/priv.sh", 30),))
    db.decide(act(argv=("rm", "-rf", "/")), 3, L3_CELL, policy_decide, write_leases=(("/r/x", 30),))
    assert db.match_expected("/r/priv.sh") is None
    assert db.match_expected("/r/x") is None


def test_directory_lease_covers_children_not_siblings(db):
    db.expect_write("/r/demo", "cmd1", ttl_seconds=60)
    assert db.match_expected("/r/demo") == "cmd1"
    assert db.match_expected("/r/demo/src/app.py") == "cmd1"
    assert db.match_expected("/r/demo_other/app.py") is None     # prefix, not substring
    assert db.match_expected("/r") is None


def test_concurrent_leases_on_one_path_do_not_clobber(db):
    a, b = act(), act()
    db.decide(a, 3, L3_CELL, policy_decide, write_leases=(("/r/demo", 300),))
    db.decide(b, 3, L3_CELL, policy_decide, write_leases=(("/r/demo", 300),))
    db.settle(a.action_id, "success")               # a finishing must not end b's lease
    with db._conn() as c:
        rows = dict(c.execute("SELECT action_id, expires_at FROM expected_writes").fetchall())
    assert set(rows) == {a.action_id, b.action_id}
    assert rows[a.action_id] < rows[b.action_id]


def test_settle_shrinks_lease_to_grace(db, monkeypatch):
    import kosha.system.kosha_db as kdb
    monkeypatch.setattr(kdb, "LEASE_GRACE", -1)     # grace already over
    a = act()
    db.decide(a, 3, L3_CELL, policy_decide, write_leases=(("/r/app.py", 300),))
    db.settle(a.action_id, "failure")
    assert db.match_expected("/r/app.py") is None


def test_budgets_default_to_the_price_table(tmp_path):
    from kosha.pricing.pricing import load_table
    t = load_table()
    d = KoshaDB(tmp_path / "t.db")
    assert (d.fleet_budget, d.agent_cap, d.window.total_seconds()) == \
        (t["fleet_budget"], t["agent_cap"], t["window_minutes"] * 60)


# --- approvals cover exactly what the human saw ---

def db_act(sql, agent="a1", tool="db_exec"):
    return Action(action_id=uuid.uuid4().hex, session_id="s1", agent_id=agent, harness="bob",
                  tool=tool, raw={"db": "prod", "sql": sql}, argv=[], cwd="/r", targets=["prod"],
                  ts="2026-09-26T00:00:00Z")


def test_approval_does_not_cover_different_raw_input(db):
    # regression: fingerprint once ignored raw, so approving one SQL let any SQL through
    assert db.decide(db_act("drop table audit_old"), 5, "*|*|priv", policy_decide).decision == "ask"
    [ap] = db.pending_approvals()
    db.resolve_approval(ap["id"], "approve_once")
    other = db.decide(db_act("drop table users"), 5, "*|*|priv", policy_decide)
    assert other.decision == "ask"                              # new approval needed
    same = db.decide(db_act("drop table audit_old"), 5, "*|*|priv", policy_decide)
    assert same.decision == "allow" and "approval" in same.reason


def test_approved_retry_cannot_launder_a_hard_deny(db):
    db.decide(db_act("drop table audit_old"), 5, "*|*|priv", policy_decide)
    [ap] = db.pending_approvals()
    db.resolve_approval(ap["id"], "approve_once")
    d = db.decide(db_act("drop database prod_main"), 4, "irrev|shared|nopriv", policy_decide)
    assert (d.decision, d.rule) == ("deny", "hard_deny")


def test_bundle_shows_the_raw_input_being_approved(db):
    assert db.decide(db_act("insert into audit values (1)"), 3, L3_CELL, policy_decide).decision == "allow"
    db.decide(db_act("drop table users", agent="a2"), 5, "*|*|priv", policy_decide)
    [ap] = db.pending_approvals()
    assert [b["raw"]["sql"] for b in ap["bundle"]] == ["insert into audit values (1)", "drop table users"]


# --- window_touches: the convergence rule sees the window ---

def touch_act(agent, target, tool="edit_file", session="s1"):
    return Action(action_id=uuid.uuid4().hex, session_id=session, agent_id=agent, harness="bob",
                  tool=tool, raw={"path": target}, argv=[], cwd="/r", targets=[target],
                  ts="2026-09-26T00:00:00Z")


def test_second_agent_on_the_same_target_is_held(db):
    assert db.decide(touch_act("sub1", "/r/ci.yml"), 2, "rev|local|nopriv", policy_decide).decision == "allow"
    d = db.decide(touch_act("sub2", "/r/ci.yml"), 2, "rev|local|nopriv", policy_decide)
    assert (d.decision, d.rule) == ("ask", "convergence")
    assert "sub1 already acted on path:/r/ci.yml" in d.reason


def test_same_agent_or_different_target_does_not_converge(db):
    for agent, target in (("sub1", "/r/a.py"), ("sub1", "/r/a.py"), ("sub2", "/r/b.py")):
        assert db.decide(touch_act(agent, target), 2, "rev|local|nopriv", policy_decide).decision == "allow"


def test_pending_actions_count_as_touches(db):
    # sub1's action is held (L5), not reserved: it must still count
    assert db.decide(touch_act("sub1", "/r/deploy.sh"), 5, "*|*|priv", policy_decide).decision == "ask"
    d = db.decide(touch_act("sub2", "/r/deploy.sh"), 2, "rev|local|nopriv", policy_decide)
    assert (d.decision, d.rule) == ("ask", "convergence")


def test_denied_cancelled_and_read_only_actions_do_not_touch(db):
    a = touch_act("sub1", "/r/x.py")
    db.decide(a, 2, "rev|local|nopriv", policy_decide)
    db.settle(a.action_id, "failure")                                   # cancelled
    db.decide(touch_act("sub1", "/r/y.py"), 0, "rev|local|nopriv", policy_decide)   # L0 read
    for target in ("/r/x.py", "/r/y.py"):
        assert db.decide(touch_act("sub2", target), 2, "rev|local|nopriv", policy_decide).decision == "allow"


def test_pending_does_not_count_toward_escalation(db):
    # escalation still reads reserved/confirmed only: two held L5s don't make a third L3 escalate
    for agent in ("sub1", "sub2"):
        assert db.decide(touch_act(agent, f"/r/{agent}.sh"), 5, "*|*|priv", policy_decide).decision == "ask"
    assert db.decide(touch_act("sub3", "/r/other.py"), 3, L3_CELL, policy_decide).decision == "allow"


def test_touches_are_oldest_first_and_window_scoped(tmp_path):
    db = KoshaDB(tmp_path / "w.db", window_minutes=0)                   # every decide rolls the window
    db.decide(touch_act("sub1", "/r/ci.yml"), 2, "rev|local|nopriv", policy_decide)
    assert db.decide(touch_act("sub2", "/r/ci.yml"), 2, "rev|local|nopriv", policy_decide).decision == "allow"
    db2 = KoshaDB(tmp_path / "o.db")
    for agent in ("sub1", "sub2"):
        db2.decide(touch_act(agent, f"/r/{agent}.py"), 2, "rev|local|nopriv", policy_decide)
    db2.decide(touch_act("sub2", "/r/shared.py"), 2, "rev|local|nopriv", policy_decide)
    db2.decide(touch_act("sub1", "/r/shared2.py"), 2, "rev|local|nopriv", policy_decide)
    d = db2.decide(Action(uuid.uuid4().hex, "s1", "sub3", "bob", "edit_file", {}, [], "/r",
                          ["/r/shared.py", "/r/shared2.py"], ""), 2, "rev|local|nopriv", policy_decide)
    assert "sub2 already acted on" in d.reason                         # the earliest touch wins


def test_convergence_cannot_be_raced(tmp_path):
    # two agents hit the same target at the same instant: exactly one gets through
    db = KoshaDB(tmp_path / "k.db", agent_cap=1000, fleet_budget=100000)
    for i in range(200):
        out = race(db, ["sub1", "sub2"], f"s{i}", same_target=True)
        assert sorted((d.decision, d.rule) for d in out) == [("allow", "ok"), ("ask", "convergence")], out
