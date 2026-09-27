from types import SimpleNamespace

import pytest

from kosha.pricing.convergence import touch
from kosha.pricing.policy import (Decision, LedgerState, batch_of, decide, escalation_threshold,
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
    ["/bin/rm", "-rf", "/"],                    # rm by absolute path
    ["/usr/bin/rm", "-rf", "/*"],
    ["sudo", "/bin/rm", "-fr", "/"],
    ["rm", "-rf", "//"],                        # repeated slashes
    ["rm", "-rf", "///"],
    ["rm", "-rf", "/."],                        # trailing /.
    ["rm", "-rf", "/tmp/.."],
    ["cd", "/", "&&", "rm", "-rf", "*"],        # relative operand after cd /
    ["cd", "/", ";", "rm", "-rf", "./*"],
    ["cd", "/tmp", "&&", "cd", "..", "&&", "rm", "-rf", "*"],
])
def test_hard_deny_rm_variants(argv):
    d = decide(act(argv), 3, "irrev|local|nopriv", state())
    assert (d.decision, d.rule) == ("deny", "hard_deny")


@pytest.mark.parametrize("command", [
    "cd /&&rm -rf *",                           # no spaces: argv can't show the split
    "bash -c 'rm -rf //'",
])
def test_hard_deny_rm_variants_in_raw_command(command):
    d = decide(act(["bash", "-c", command], raw={"command": command}), 3, "irrev|local|nopriv", state())
    assert (d.decision, d.rule) == ("deny", "hard_deny")


def test_hard_deny_relative_rm_when_action_cwd_is_root():
    a = act(["rm", "-rf", "*"])
    a.cwd = "/"
    assert decide(a, 3, "irrev|local|nopriv", state()).rule == "hard_deny"


@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "*"],                          # cwd /repo, not root
    ["cd", "/tmp", "&&", "rm", "-rf", "*"],
    ["cd", "&&", "rm", "-rf", "*"],               # bare cd goes home
    ["rm", "-rf", "//tmp"],
    ["rm", "-rf", "/tmp/."],
    ["/bin/rm", "-r", "/"],                      # no force
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

@pytest.mark.parametrize("token", ["tok-123", "approved", "human-ok", "x" * 64, " ", True, 1])
def test_approval_token_does_not_bypass_hard_deny(token):
    """Nothing issues or verifies approval tokens yet, so any value set by the caller
    is ignored: hard deny is final in this version."""
    for a in (act(["psql", "postgres://prod/app", "-c", "DROP DATABASE app"],
                  raw={"approval_token": token}),
              act(["db_exec"], targets=["prod"], raw={"sql": "drop database app", "approval_token": token}),
              act(["rm", "-rf", "/"], raw={"approval_token": token})):
        d = decide(a, 4, "irrev|shared|nopriv", state(fleet_budget=1e9, agent_cap=1e9))
        assert (d.decision, d.rule) == ("deny", "hard_deny")

def test_hard_deny_suggestion_offers_no_override():
    d = decide(act(["psql", "postgres://prod/app", "-c", "DROP DATABASE app"]), 4,
               "irrev|shared|nopriv", state())
    assert "token" not in d.reason.lower() and "token" not in d.suggestion.lower()
    assert "non-prod" in d.suggestion

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


# --- convergence ---

def test_convergence_asks_regardless_of_budget_headroom():
    first = act(["kubectl", "apply", "-f", "deploy.yaml"], agent_id="agent-1")
    later = act(["sed", "-i", "s/1/2/", "deploy.yaml"], agent_id="agent-2")
    s = state(fleet_budget=1e9, agent_cap=1e9)
    s.window_touches = [touch(first, 4)]
    d = decide(later, 2, "rev|local|nopriv", s)
    assert (d.decision, d.rule) == ("ask", "convergence")
    assert "path:/repo/deploy.yaml" in d.reason and "agent-1" in d.reason
    assert d.suggestion and "path:/repo/deploy.yaml" in d.suggestion
    assert (d.fleet_after, d.agent_after) == (0, 0)          # an ask charges nothing


def test_convergence_ignores_same_agent_and_read_only():
    first = act(["kubectl", "apply", "-f", "deploy.yaml"], agent_id="agent-1")
    s = state()
    s.window_touches = [touch(first, 4)]
    same = act(["sed", "-i", "s/1/2/", "deploy.yaml"], agent_id="agent-1")
    assert decide(same, 2, "rev|local|nopriv", s).rule == "ok"
    reader = act(["cat", "deploy.yaml"], agent_id="agent-2")
    assert decide(reader, 0, "rev|local|nopriv", s).rule == "ok"
    assert touch(reader, 0) is None


def test_pending_ask_counts_as_touch():
    """agent-1's chmod 777 is asked (L5) and still pending: never approved, never ran.
    Its target is recorded as a touch at decision time, so agent-2 acting on the same
    path converges. Recording only allowed actions would miss it."""
    ledger = state(fleet_budget=1e9, agent_cap=1e9)
    first = act(["chmod", "-R", "777", "logs/"], agent_id="agent-1")
    d1 = decide(first, 5, "*|*|priv", ledger)
    assert (d1.decision, d1.rule) == ("ask", "l5")
    ledger.window_touches.append(touch(first, 5))              # recorded on ask, still pending

    later = act(["rm", "-rf", "logs"], agent_id="agent-2")
    d2 = decide(later, 3, "irrev|local|nopriv", ledger)
    assert (d2.decision, d2.rule) == ("ask", "convergence")
    assert "path:/repo/logs" in d2.reason

    allowed_only = state(fleet_budget=1e9, agent_cap=1e9)      # ask never recorded
    assert decide(later, 3, "irrev|local|nopriv", allowed_only).rule == "ok"


def test_convergence_comes_before_escalation_and_after_l5():
    first = act(["git", "push", "origin", "release"], agent_id="agent-1")
    s = state(recent=[("agent-1", 4), ("agent-2", 4)])
    s.window_touches = [touch(first, 4)]
    later = act(["git", "push", "--force", "origin", "release"], agent_id="agent-2")
    assert decide(later, 4, "irrev|shared|nopriv", s).rule == "convergence"
    assert decide(later, 5, "*|*|priv", s).rule == "l5"


# --- escalation counts by batch (one commit = one unit) ---
NEW_FILE, L4_PUSH = (3, "irrev|local|nopriv"), (4, "rev|shared|nopriv")


def run(steps):
    """steps: (agent_id, batch_id, (level, cell)). Budgets out of reach, so only the
    escalation rule can ask. Allowed actions enter the window with their batch key."""
    recent, out = [], []
    for i, (agent, batch, (level, cell)) in enumerate(steps):
        a = act(["touch", f"f{i}.py"], agent_id=agent)
        a.batch_id = batch
        d = decide(a, level, cell, state(fleet_budget=1e9, agent_cap=1e9, recent=recent))
        out.append(d)
        if d.decision == "allow":
            recent.append((agent, level, batch_of(a)))
    return out


def test_one_commit_of_ten_new_files_does_not_escalate_in_a_fleet():
    """agent-2 in the window drops the threshold to L3, so new files (L3) count."""
    steps = [("agent-2", "c0", (2, "rev|local|nopriv"))] + [("agent-1", "c1", NEW_FILE)] * 10
    assert all(d.decision == "allow" for d in run(steps))
    unbatched = run([(a, None, lc) for a, _, lc in steps])     # before: every file counted
    assert [d.rule for d in unbatched].index("escalation") == 3


def test_one_batch_of_ten_l4_actions_does_not_escalate_single_agent():
    assert all(d.decision == "allow" for d in run([("agent-1", "c1", L4_PUSH)] * 10))
    assert run([("agent-1", None, L4_PUSH)] * 10)[2].rule == "escalation"


def test_two_separate_commits_still_escalate():
    single = run([("agent-1", "c1", L4_PUSH), ("agent-1", "c2", L4_PUSH), ("agent-1", "c3", L4_PUSH)])
    assert [d.rule for d in single] == ["ok", "ok", "escalation"]
    fleet = run([("agent-2", "c0", (2, "rev|local|nopriv")), ("agent-1", "c1", NEW_FILE),
                 ("agent-1", "c2", NEW_FILE), ("agent-1", "c3", NEW_FILE)])
    assert [d.rule for d in fleet] == ["ok", "ok", "ok", "escalation"]


def test_batch_after_two_commits_asks_on_every_consequential_action():
    """Once two batches are in the window, a third batch asks from its first action."""
    steps = [("agent-1", "c1", L4_PUSH), ("agent-1", "c2", L4_PUSH)] + [("agent-1", "c3", L4_PUSH)] * 3
    assert [d.rule for d in run(steps)] == ["ok", "ok", "escalation", "escalation", "escalation"]


def test_same_batch_key_from_two_agents_counts_twice():
    steps = [("agent-1", "c1", NEW_FILE), ("agent-2", "c1", NEW_FILE), ("agent-2", "c9", NEW_FILE)]
    assert run(steps)[2].rule == "escalation"


def test_entries_without_batch_count_one_each():
    assert escalation_triggered([("a", 4), ("a", 4)])
    assert escalation_triggered([("a", 4, None), ("a", 4, None)])
    assert not escalation_triggered([("a", 4, "c1"), ("a", 4, "c1")])
    assert escalation_triggered([("a", 4, "c1"), ("a", 4)])
