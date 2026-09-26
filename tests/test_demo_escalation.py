"""The "prepare release 1.3" demo fleet: the escalation rule alone stops it.

Three subagents act in sequence within one window; each action goes through
policy.decide() against a running ledger (charged on allow, like kosha_db). Levels
and cells come from the effects.yaml entries the parser resolves these actions to,
so the test follows the table rather than hardcoded numbers.

Run twice: at the M1 defaults, and with fleet_budget/agent_cap set far out of reach,
so the budget path can't be what catches the fleet.

Convergence variant: the same fleet with real targets, where sub3's migration runs the
migration file sub2 just edited. The control is identical except that sub3 migrates a
different file, so the shared target is the only difference between the two.
"""
from types import SimpleNamespace

import pytest

from kosha.pricing import policy
from kosha.pricing.convergence import touch
from kosha.pricing.match import load_effects
from kosha.pricing.policy import LedgerState, decide
from kosha.pricing.pricing import cell_of, load_table
from kosha.pricing.rubric import classify

BY_ID = {e["id"]: e for e in load_effects()}
TABLE = load_table()
UNREACHABLE = 1e9


def step(agent, what, entry_id, tool="run_command", argv=None, targets=()):
    e = BY_ID[entry_id]
    return agent, what, classify(e["reversible"], e["scope"], e["privilege"]), \
        cell_of(e["reversible"], e["scope"], e["privilege"]), tool, argv or [what], list(targets)


def demo(test_edit_entry):
    return [
        step("sub1", "bump version", "file_write_tracked_clean"),
        step("sub1", "git push", "git_push"),
        step("sub2", "edit test file", test_edit_entry),
        step("sub2", "edit CI workflow", "ci_github_workflow_edit"),
        step("sub3", "migrate shared DB", "migrate_shared"),
        step("sub3", "deploy", "deploy_trigger"),
    ]


def targeted_demo(sub3_migration):
    """The demo fleet with real targets. sub2 edits migrations/013_release.sql; sub3
    migrates the shared DB with sub3_migration."""
    return [
        step("sub1", "bump version", "file_write_tracked_clean", "edit_file", [], ["pyproject.toml"]),
        step("sub1", "git push", "git_push", argv=["git", "push", "origin", "release-1.3"]),
        step("sub2", "edit migration", "file_write_tracked_clean", "edit_file", [],
             ["migrations/013_release.sql"]),
        step("sub2", "edit CI workflow", "ci_github_workflow_edit", "edit_file", [],
             [".github/workflows/release.yml"]),
        step("sub3", "migrate shared DB", "migrate_shared",
             argv=["psql", "-d", "shared_db", "-f", sub3_migration]),
        step("sub3", "deploy", "deploy_trigger", argv=["gh", "workflow", "run", "release.yml"]),
    ]


CONVERGENT = "migrations/013_release.sql"      # the file sub2 edited
DISJOINT = "migrations/014_backfill.sql"


def replay(steps, fleet_budget, agent_cap):
    """Run steps through decide() in order. Returns (decisions, fleet spend when the
    first ask came back, index of that step). Like kosha_db, an allowed action is
    charged and enters the window; allowed and asked actions both record their
    targets as touches."""
    fleet, agents, recent, touches, out = 0.0, {}, [], [], []
    first_ask = None
    for i, (agent, what, level, cell, tool, argv, targets) in enumerate(steps):
        action = SimpleNamespace(action_id=f"a{i}", session_id="release-1.3", agent_id=agent,
                                 harness="replay", tool=tool, raw={}, argv=argv,
                                 cwd="/repo", targets=targets, ts="2026-09-26T00:00:00Z")
        s = LedgerState(fleet, fleet_budget, agents.get(agent, 0.0), agent_cap, list(recent),
                        list(touches))
        d = decide(action, level, cell, s)
        out.append(d)
        if d.decision in ("allow", "ask") and touch(action, level):
            touches.append(touch(action, level))
        if d.decision == "allow":
            fleet += d.price
            agents[agent] = agents.get(agent, 0.0) + d.price
            recent.append((agent, level))
        elif first_ask is None:
            first_ask = (i, fleet)
    return out, first_ask


def test_demo_levels_are_what_the_scenario_assumes():
    assert [st[2] for st in demo("file_write_tracked_clean")] == [2, 4, 2, 4, 4, 4]
    assert [st[2] for st in demo("file_write_untracked")] == [2, 4, 3, 4, 4, 4]
    for mig in (CONVERGENT, DISJOINT):
        assert [st[2] for st in targeted_demo(mig)] == [2, 4, 2, 4, 4, 4]


@pytest.mark.parametrize("budgets", ["m1", "unreachable"])
def test_escalation_alone_asks_at_agent_3_with_tracked_test_edit(budgets):
    fleet_budget, agent_cap = ((TABLE["fleet_budget"], TABLE["agent_cap"]) if budgets == "m1"
                               else (UNREACHABLE, UNREACHABLE))
    decisions, (i, spent) = replay(demo("file_write_tracked_clean"), fleet_budget, agent_cap)
    assert [d.decision for d in decisions[:4]] == ["allow"] * 4
    assert i == 4                                   # sub3's first action, the shared migration
    assert (decisions[4].decision, decisions[4].rule) == ("ask", "escalation")
    assert spent == 2 + 30 + 2 + 30 == 64           # nowhere near 750 / 375
    assert decisions[5].rule == "escalation"        # the deploy is held too


@pytest.mark.parametrize("budgets", ["m1", "unreachable"])
def test_escalation_asks_earlier_with_untracked_test_edit(budgets):
    """An untracked test edit is L3, which counts once two agents are in the window,
    so the CI edit (sub2's second action) is already the third consequential action."""
    fleet_budget, agent_cap = ((TABLE["fleet_budget"], TABLE["agent_cap"]) if budgets == "m1"
                               else (UNREACHABLE, UNREACHABLE))
    decisions, (i, spent) = replay(demo("file_write_untracked"), fleet_budget, agent_cap)
    assert i == 3
    assert (decisions[3].decision, decisions[3].rule) == ("ask", "escalation")
    assert spent == 2 + 30 + 10 == 42
    assert all(d.rule == "escalation" for d in decisions[3:])


def test_m1_defaults_are_the_ones_in_the_table():
    assert (TABLE["fleet_budget"], TABLE["agent_cap"]) == (750, 375)


BUDGETS = {"m1": (TABLE["fleet_budget"], TABLE["agent_cap"]), "unreachable": (UNREACHABLE, UNREACHABLE)}


@pytest.mark.parametrize("budgets", BUDGETS)
def test_convergence_asks_when_sub3_migrates_the_file_sub2_edited(budgets):
    decisions, (i, spent) = replay(targeted_demo(CONVERGENT), *BUDGETS[budgets])
    assert [d.decision for d in decisions[:4]] == ["allow"] * 4
    assert i == 4
    d = decisions[4]
    assert (d.decision, d.rule) == ("ask", "convergence")
    assert "path:migrations/013_release.sql" in d.reason and "sub2" in d.reason
    assert spent == 2 + 30 + 2 + 30


@pytest.mark.parametrize("budgets", BUDGETS)
def test_control_disjoint_migration_is_caught_by_escalation_not_convergence(budgets):
    decisions, (i, _) = replay(targeted_demo(DISJOINT), *BUDGETS[budgets])
    assert i == 4
    assert decisions[4].rule == "escalation"
    assert all(d.rule != "convergence" for d in decisions)


@pytest.mark.parametrize("budgets", BUDGETS)
def test_convergence_alone_catches_it_with_escalation_disabled(budgets, monkeypatch):
    """With the escalation rule switched off, the convergent fleet is still asked at
    sub3's migration, by convergence; the disjoint control then goes through."""
    monkeypatch.setattr(policy, "escalation_asks", lambda *a, **k: False)
    decisions, (i, _) = replay(targeted_demo(CONVERGENT), *BUDGETS[budgets])
    assert (i, decisions[4].decision, decisions[4].rule) == (4, "ask", "convergence")
    control, first_ask = replay(targeted_demo(DISJOINT), *BUDGETS[budgets])
    assert control[4].decision == "allow" and first_ask is None


def test_convergence_on_pending_ask_in_the_demo():
    """sub3's migration is asked (convergence) and never approved, so it never ran.
    Only sub3 has touched db:shared_db; sub1 then migrates that database with another
    file, and converges with sub3's pending touch alone."""
    steps = targeted_demo(CONVERGENT) + [
        step("sub1", "run another migration", "migrate_shared",
             argv=["psql", "-d", "shared_db", "-f", "migrations/015_other.sql"])]
    decisions, _ = replay(steps, UNREACHABLE, UNREACHABLE)
    assert decisions[4].decision == "ask"                    # sub3's migration: pending
    d = decisions[6]
    assert (d.decision, d.rule) == ("ask", "convergence")
    assert "db:shared_db" in d.reason and "sub3" in d.reason
