"""The "prepare release 1.3" demo fleet: the escalation rule alone stops it.

Three subagents act in sequence within one window; each action goes through
policy.decide() against a running ledger (charged on allow, like kosha_db). Levels
and cells come from the effects.yaml entries the parser resolves these actions to,
so the test follows the table rather than hardcoded numbers.

Run twice: at the M1 defaults, and with fleet_budget/agent_cap set far out of reach,
so the budget path can't be what catches the fleet.
"""
from types import SimpleNamespace

import pytest

from kosha.pricing.match import load_effects
from kosha.pricing.policy import LedgerState, decide
from kosha.pricing.pricing import cell_of, load_table
from kosha.pricing.rubric import classify

BY_ID = {e["id"]: e for e in load_effects()}
TABLE = load_table()
UNREACHABLE = 1e9


def step(agent, what, entry_id):
    e = BY_ID[entry_id]
    return agent, what, classify(e["reversible"], e["scope"], e["privilege"]), \
        cell_of(e["reversible"], e["scope"], e["privilege"])


def demo(test_edit_entry):
    return [
        step("sub1", "bump version", "file_write_tracked_clean"),
        step("sub1", "git push", "git_push"),
        step("sub2", "edit test file", test_edit_entry),
        step("sub2", "edit CI workflow", "ci_github_workflow_edit"),
        step("sub3", "migrate shared DB", "migrate_shared"),
        step("sub3", "deploy", "deploy_trigger"),
    ]


def replay(steps, fleet_budget, agent_cap):
    """Run steps through decide() in order. Returns (decisions, fleet spend when the
    first ask came back, index of that step)."""
    fleet, agents, recent, out = 0.0, {}, [], []
    first_ask = None
    for i, (agent, what, level, cell) in enumerate(steps):
        action = SimpleNamespace(action_id=f"a{i}", session_id="release-1.3", agent_id=agent,
                                 harness="replay", tool="run_command", raw={}, argv=[what],
                                 cwd="/repo", targets=[], ts="2026-09-26T00:00:00Z")
        s = LedgerState(fleet, fleet_budget, agents.get(agent, 0.0), agent_cap, list(recent))
        d = decide(action, level, cell, s)
        out.append(d)
        if d.decision == "allow":
            fleet += d.price
            agents[agent] = agents.get(agent, 0.0) + d.price
            recent.append((agent, level))
        elif first_ask is None:
            first_ask = (i, fleet)
    return out, first_ask


def test_demo_levels_are_what_the_scenario_assumes():
    assert [lvl for *_, lvl, _ in demo("file_write_tracked_clean")] == [2, 4, 2, 4, 4, 4]
    assert [lvl for *_, lvl, _ in demo("file_write_untracked")] == [2, 4, 3, 4, 4, 4]


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
