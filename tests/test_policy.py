from types import SimpleNamespace

import pytest

from kosha.pricing.policy import Decision, LedgerState, decide, escalation_triggered


def act(argv=("ls",), targets=(), raw=None, agent_id="main"):
    return SimpleNamespace(action_id="a1", session_id="s1", agent_id=agent_id,
                           harness="replay", tool="run_command", raw=raw or {},
                           argv=list(argv), cwd="/repo", targets=list(targets),
                           ts="2026-09-26T00:00:00Z")


def state(fleet_spent=0, fleet_budget=100, agent_spent=0, agent_cap=50, recent=()):
    return LedgerState(fleet_spent, fleet_budget, agent_spent, agent_cap, list(recent))


# --- allow ---
def test_allow_under_all_limits():
    d = decide(act(["git", "commit"]), 2, "rev|local|nopriv", state(fleet_spent=10, agent_spent=5))
    assert (d.decision, d.rule, d.price) == ("allow", "ok", 2)
    assert (d.fleet_after, d.agent_after) == (12, 7)
    assert d.suggestion is None

def test_read_only_is_free_and_allowed():
    d = decide(act(["ls"]), 0, "rev|local|nopriv", state())
    assert (d.decision, d.price) == ("allow", 0)

def test_returns_decision_with_inputs_echoed():
    d = decide(act(), 3, "irrev|local|nopriv", state())
    assert isinstance(d, Decision) and d.level == 3 and d.cell == "irrev|local|nopriv"


# --- a. hard deny ---
@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "/"],
    ["rm", "-fr", "/"],
    ["rm", "-r", "-f", "/"],
    ["rm", "-Rf", "/*"],
    ["rm", "--recursive", "--force", "/"],
    ["rm", "-rf", "--no-preserve-root", "/"],
    ["sudo", "rm", "-rf", "/"],
    ["cd", "/tmp", "&&", "rm", "-rf", "/"],
])
def test_hard_deny_rm_rf_root(argv):
    d = decide(act(argv), 3, "irrev|local|nopriv", state())
    assert (d.decision, d.rule) == ("deny", "hard_deny")
    assert d.reason and d.suggestion

@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "build/"],
    ["rm", "-rf", "/tmp/x"],
    ["rm", "/"],                      # not recursive+force
    ["rm", "-r", "/"],                # no force
    ["echo", "rm", "-rf"],
])
def test_rm_not_root_is_not_hard_denied(argv):
    assert decide(act(argv), 3, "irrev|local|nopriv", state()).rule != "hard_deny"

def test_hard_deny_drop_prod_db_in_url():
    a = act(["psql", "postgres://db.prod.internal/app", "-c", "DROP DATABASE app"])
    d = decide(a, 4, "irrev|shared|nopriv", state())
    assert (d.decision, d.rule) == ("deny", "hard_deny")

def test_hard_deny_drop_prod_db_via_alias_and_raw_sql():
    a = act(["db_exec"], targets=["prod"], raw={"sql": "drop schema public cascade"})
    assert decide(a, 4, "irrev|shared|nopriv", state()).rule == "hard_deny"

def test_drop_prod_db_with_approval_token_is_not_hard_denied():
    a = act(["psql", "postgres://prod/app", "-c", "DROP DATABASE app"],
            raw={"approval_token": "tok-123"})
    assert decide(a, 4, "irrev|shared|nopriv", state()).rule != "hard_deny"

def test_drop_non_prod_db_is_not_hard_denied():
    a = act(["psql", "postgres://localhost/dev", "-c", "DROP DATABASE dev"])
    assert decide(a, 3, "irrev|local|nopriv", state()).rule != "hard_deny"

def test_hard_deny_wins_regardless_of_budget_and_other_rules():
    s = state(fleet_spent=0, agent_spent=0, recent=[("a", 4), ("b", 4)])
    d = decide(act(["rm", "-rf", "/"]), 5, "*|*|priv", s)
    assert d.rule == "hard_deny"

def test_hard_deny_does_not_charge():
    d = decide(act(["rm", "-rf", "/"]), 3, "irrev|local|nopriv", state(fleet_spent=10, agent_spent=4))
    assert (d.fleet_after, d.agent_after) == (10, 4)


# --- b. L5 ---
def test_l5_asks_even_with_empty_ledger():
    d = decide(act(["chmod", "777", "x"]), 5, "irrev|shared|priv", state())
    assert (d.decision, d.rule, d.price) == ("ask", "l5", 60)
    assert d.reason and d.suggestion

def test_l5_beats_escalation_and_budget():
    s = state(fleet_spent=99, agent_spent=49, recent=[("a", 4), ("b", 4)])
    assert decide(act(), 5, "*|*|priv", s).rule == "l5"


# --- c. fleet escalation ---
def test_escalation_same_level_distinct_agents():
    # revised rule: equal levels are enough, no rise required
    assert escalation_triggered([("sub-1", 4), ("sub-2", 4)])
    d = decide(act(agent_id="sub-3"), 4, "rev|shared|nopriv", state(recent=[("sub-1", 4), ("sub-2", 4)]))
    assert (d.decision, d.rule) == ("ask", "escalation")
    assert d.reason and d.suggestion

def test_escalation_rising_pair_distinct_agents():
    assert escalation_triggered([("sub-1", 3), ("sub-2", 4)])
    d = decide(act(), 4, "rev|shared|nopriv", state(recent=[("sub-1", 3), ("sub-2", 4)]))
    assert d.rule == "escalation"

def test_escalation_falling_pair_distinct_agents():
    assert escalation_triggered([("sub-1", 4), ("sub-2", 3)])

def test_escalation_single_agent_two_prior_consequential():
    assert escalation_triggered([("sub-1", 3), ("sub-1", 3)])

def test_single_agent_third_consequential_action_asks():
    recent = []
    rules = []
    for _ in range(3):
        d = decide(act(agent_id="sub-1"), 3, "irrev|local|nopriv", state(recent=recent))
        rules.append(d.rule)
        if d.decision == "allow":
            recent.append(("sub-1", 3))
    assert rules == ["ok", "ok", "escalation"]

def test_escalation_mixed_agents_counts_all():
    assert escalation_triggered([("sub-1", 4), ("sub-1", 2), ("sub-2", 3)])

def test_no_escalation_one_consequential():
    assert not escalation_triggered([("sub-1", 4), ("sub-2", 2), ("sub-3", 0)])

def test_no_escalation_below_L3():
    assert not escalation_triggered([("sub-1", 2), ("sub-2", 2)])

def test_no_escalation_empty():
    assert not escalation_triggered([])

def test_escalation_only_gates_consequential_current_action():
    s = state(recent=[("sub-1", 4), ("sub-2", 4)])
    assert decide(act(), 2, "rev|local|nopriv", s).rule == "ok"
    assert decide(act(), 3, "irrev|local|nopriv", s).rule == "escalation"

def test_escalation_beats_budget():
    s = state(fleet_spent=95, recent=[("sub-1", 4), ("sub-2", 4)])
    assert decide(act(), 4, "irrev|shared|nopriv", s).rule == "escalation"


# --- d. fleet budget ---
def test_fleet_budget_ask_when_price_overdraws():
    d = decide(act(), 4, "irrev|shared|nopriv", state(fleet_spent=70, agent_spent=0))
    assert (d.decision, d.rule, d.price) == ("ask", "fleet_budget", 40)
    assert "30" in d.suggestion                  # what would still be allowed
    assert (d.fleet_after, d.agent_after) == (70, 0)

def test_fleet_budget_exactly_at_limit_is_allowed():
    d = decide(act(), 4, "irrev|shared|nopriv", state(fleet_spent=60))
    assert (d.decision, d.fleet_after) == ("allow", 100)

def test_fleet_budget_beats_agent_cap():
    d = decide(act(), 4, "irrev|shared|nopriv", state(fleet_spent=90, agent_spent=45))
    assert d.rule == "fleet_budget"


# --- e. agent cap ---
def test_agent_cap_ask_when_price_overdraws():
    d = decide(act(), 3, "irrev|local|nopriv", state(fleet_spent=20, agent_spent=45))
    assert (d.decision, d.rule) == ("ask", "agent_cap")
    assert "5" in d.suggestion and d.reason

def test_agent_cap_exactly_at_limit_is_allowed():
    assert decide(act(), 3, "irrev|local|nopriv", state(agent_spent=40)).decision == "allow"


# --- every ask/deny carries reason + suggestion (design rule 3) ---
@pytest.mark.parametrize("argv,level,cell,s", [
    (["rm", "-rf", "/"], 3, "irrev|local|nopriv", state()),
    (["chmod", "777", "x"], 5, "*|*|priv", state()),
    (["git", "push"], 4, "rev|shared|nopriv", state(recent=[("a", 3), ("b", 3)])),
    (["git", "push"], 4, "rev|shared|nopriv", state(fleet_spent=80)),
    (["git", "push"], 4, "rev|shared|nopriv", state(agent_spent=30)),
])
def test_every_non_allow_has_reason_and_suggestion(argv, level, cell, s):
    d = decide(act(argv), level, cell, s)
    assert d.decision in ("ask", "deny")
    assert d.reason.strip() and d.suggestion and d.suggestion.strip()


def test_price_comes_from_pricing_per_cell():
    # same level, different cells -> different prices
    push = decide(act(), 4, "rev|shared|nopriv", state())
    force = decide(act(), 4, "irrev|shared|nopriv", state())
    assert (push.price, force.price) == (30, 40)
