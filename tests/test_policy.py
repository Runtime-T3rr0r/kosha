from types import SimpleNamespace

import pytest

from kosha.pricing.policy import (Decision, LedgerState, decide, escalation_threshold,
                                  escalation_triggered)


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

@pytest.mark.parametrize("argv, raw", [
    (["psql", "-c", "drop database prod"], {}),
    (["psql", "-c", "drop database prod_orders"], {}),
    (["psql", "-c", "DROP DATABASE IF EXISTS \"production\""], {}),
    (["psql", "prod", "-c", "drop database app"], {}),
    (["bash", "-c", "psql prod -c 'drop database app'"], {}),
    (["db_exec"], {"db": "prod", "sql": "drop database app"}),
])
def test_hard_deny_drop_prod_db_by_name_token(argv, raw):
    assert decide(act(argv, raw=raw), 4, "irrev|shared|nopriv", state()).rule == "hard_deny"

@pytest.mark.parametrize("argv, raw", [
    (["psql", "-c", "drop database products"], {}),
    (["psql", "-c", "DROP DATABASE products"], {}),
    (["psql", "reproduce_db", "-c", "drop database scratch"], {}),
    (["psql", "-c", "drop database reproduce_db"], {}),
    (["psql", "-c", "drop database preprod_copy"], {}),
    (["db_exec"], {"db": "dev", "sql": "drop database products"}),
])
def test_prod_substring_is_not_hard_denied(argv, raw):
    assert decide(act(argv, raw=raw), 3, "irrev|local|nopriv", state()).rule != "hard_deny"

def test_writing_drop_database_into_a_file_is_not_hard_denied():
    a = act([], targets=["/srv/prod/migrations/0001.sql"],
            raw={"path": "/srv/prod/migrations/0001.sql", "content": "DROP DATABASE prod;"})
    a.tool = "write_file"
    assert decide(a, 3, "irrev|local|nopriv", state()).rule != "hard_deny"

def test_hard_deny_wins_regardless_of_budget_and_other_rules():
    s = state(fleet_spent=0, agent_spent=0, recent=[("a", 4), ("b", 4)])
    d = decide(act(["rm", "-rf", "/"]), 5, "*|*|priv", s)
    assert d.rule == "hard_deny"

def test_hard_deny_does_not_charge():
    d = decide(act(["rm", "-rf", "/"]), 3, "irrev|local|nopriv", state(fleet_spent=10, agent_spent=4))
    assert (d.fleet_after, d.agent_after) == (10, 4)


# --- b2. destructive SQL on a shared/prod database ---
def db_act(db, sql, **kw):
    a = act(["db_exec"], targets=[db], raw={"db": db, "sql": sql}, **kw)
    a.tool = "db_exec"
    return a

def test_drop_table_on_prod_asks_with_full_headroom():
    d = decide(db_act("prod", "drop table users"), 4, "irrev|shared|nopriv", state())
    assert (d.decision, d.rule) == ("ask", "destructive_sql")
    assert "DROP TABLE" in d.reason and d.suggestion
    assert (d.fleet_after, d.agent_after) == (0, 0)

def test_drop_table_on_local_dev_prices_through_the_budget():
    d = decide(db_act("dev", "drop table users"), 3, "irrev|local|nopriv", state())
    assert (d.decision, d.rule, d.price) == ("allow", "ok", 10)
    d = decide(db_act("dev", "drop table users"), 3, "irrev|local|nopriv",
               state(fleet_spent=95, agent_spent=10))
    assert (d.decision, d.rule) == ("ask", "fleet_budget")

@pytest.mark.parametrize("sql", [
    "DROP TABLE users",
    "truncate users",
    "TRUNCATE TABLE users",
    "delete from users",
    "DELETE FROM users;",
    "update users set admin = true",
    "delete from sessions where id = 1; delete from users",
])
def test_destructive_sql_on_prod_asks(sql):
    assert decide(db_act("prod", sql), 4, "irrev|shared|nopriv", state()).rule == "destructive_sql"

@pytest.mark.parametrize("sql", [
    "delete from users where id = 1",
    "update users set admin = false where id = 2",
    "insert into users values (1)",
    "select * from users",
])
def test_scoped_or_non_destructive_sql_on_prod_uses_normal_rules(sql):
    assert decide(db_act("prod", sql), 4, "irrev|shared|nopriv", state()).rule == "ok"

def test_destructive_sql_asks_on_shared_scope_without_prod_in_the_name():
    d = decide(db_act("analytics", "truncate events"), 4, "irrev|shared|nopriv", state())
    assert d.rule == "destructive_sql"

def test_destructive_sql_asks_on_prod_name_even_if_cell_is_local():
    d = decide(db_act("prod_replica", "drop table users"), 3, "irrev|local|nopriv", state())
    assert d.rule == "destructive_sql"

def test_destructive_sql_via_psql_command():
    a = act(["psql", "postgres://db.prod.internal/app", "-c", "DROP TABLE users"])
    assert decide(a, 4, "irrev|shared|nopriv", state()).rule == "destructive_sql"
    a = act(["psql", "postgres://localhost/dev", "-c", "DROP TABLE products"])
    assert decide(a, 3, "irrev|local|nopriv", state()).rule == "ok"

def test_table_named_prod_on_dev_db_is_not_destructive_sql():
    d = decide(db_act("dev", "drop table prod_orders"), 3, "irrev|local|nopriv", state())
    assert d.rule == "ok"

@pytest.mark.parametrize("argv", [
    ["truncate", "-s", "0", "prod.log"],
    ["git", "push", "origin", "prod"],
])
def test_non_sql_commands_never_hit_destructive_sql(argv):
    assert decide(act(argv), 4, "irrev|shared|nopriv", state()).rule == "ok"

def test_destructive_sql_beats_budget_and_escalation():
    s = state(fleet_spent=99, agent_spent=49, recent=[("a", 4), ("b", 4)])
    assert decide(db_act("prod", "drop table users"), 4, "irrev|shared|nopriv", s).rule == "destructive_sql"

def test_drop_database_on_prod_stays_hard_deny_not_destructive_sql():
    assert decide(db_act("prod", "drop database app"), 4, "irrev|shared|nopriv", state()).rule == "hard_deny"


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

# single agent in the window: L4+ threshold; a second distinct agent drops it to L3+
def test_threshold_by_fleet_size():
    assert escalation_threshold([]) == 4
    assert escalation_threshold([("sub-1", 3), ("sub-1", 3)]) == 4
    assert escalation_threshold([("sub-1", 3), ("sub-1", 3)], "sub-1") == 4
    assert escalation_threshold([("sub-1", 3)], "sub-2") == 3
    assert escalation_threshold([("sub-1", 3), ("sub-2", 3)]) == 3

def test_single_agent_two_L3_does_not_trigger():
    recent = [("sub-1", 3), ("sub-1", 3)]
    assert not escalation_triggered(recent)
    assert not escalation_triggered(recent, "sub-1")
    assert decide(act(agent_id="sub-1"), 3, "irrev|local|nopriv", state(recent=recent)).rule == "ok"

def test_single_agent_two_L4_triggers():
    recent = [("sub-1", 4), ("sub-1", 4)]
    assert escalation_triggered(recent, "sub-1")
    s = state(fleet_budget=1000, agent_cap=1000, recent=recent)
    d = decide(act(agent_id="sub-1"), 4, "irrev|shared|nopriv", s)
    assert (d.decision, d.rule) == ("ask", "escalation")

def test_single_agent_L3_after_two_L4_is_not_escalated():
    s = state(fleet_budget=1000, agent_cap=1000, recent=[("sub-1", 4), ("sub-1", 4)])
    assert decide(act(agent_id="sub-1"), 3, "irrev|local|nopriv", s).rule == "ok"

def test_two_agents_two_L3_triggers():
    recent = [("sub-1", 3), ("sub-2", 3)]
    assert escalation_triggered(recent)
    d = decide(act(agent_id="sub-1"), 3, "irrev|local|nopriv", state(recent=recent))
    assert (d.decision, d.rule) == ("ask", "escalation")

def test_second_agent_acting_drops_threshold_to_L3():
    recent = [("sub-1", 3), ("sub-1", 3)]
    assert escalation_triggered(recent, "sub-2")
    assert decide(act(agent_id="sub-2"), 3, "irrev|local|nopriv", state(recent=recent)).rule == "escalation"

def test_single_agent_third_L4_action_asks():
    recent, rules = [], []
    s = dict(fleet_budget=1000, agent_cap=1000)
    for _ in range(3):
        d = decide(act(agent_id="sub-1"), 4, "irrev|shared|nopriv", state(recent=recent, **s))
        rules.append(d.rule)
        if d.decision == "allow":
            recent.append(("sub-1", 4))
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
