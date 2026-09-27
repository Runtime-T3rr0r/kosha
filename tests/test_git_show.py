"""git show is a read-only L0 entry, same tier as git status/log/diff, and never counts
toward fleet escalation.

Levels come from the live classification path (kosha.system.parser.classify), so the
test covers argv -> effects.yaml entry -> level, not a hand-picked level.
"""
from types import SimpleNamespace

import pytest

from kosha.pricing.match import match_command
from kosha.pricing.policy import LedgerState, decide, escalation_threshold, escalation_triggered
from kosha.system.parser import classify

UNREACHABLE = 1e9


def act(argv, agent_id="main", i=0):
    return SimpleNamespace(action_id=f"a{i}", session_id="s1", agent_id=agent_id,
                           harness="replay", tool="run_command", raw={"command": " ".join(argv)},
                           argv=list(argv), cwd="/repo", targets=[], ts="2026-09-27T00:00:00Z")


@pytest.mark.parametrize("argv", [
    ["git", "show"],
    ["git", "show", "HEAD"],
    ["git", "show", "HEAD", "--stat"],
    ["git", "show", "3f035ac"],
])
def test_git_show_is_l0_like_git_log_and_status(argv):
    assert match_command(argv)["id"] == "git_show"
    c = classify(act(argv))
    ref = {tuple(a): classify(act(a)) for a in (["git", "log"], ["git", "status"])}
    assert (c.level, c.cell, c.price) == (0, "rev|local|nopriv", 0)
    for r in ref.values():
        assert (c.level, c.cell, c.price) == (r.level, r.cell, r.price)


FLEET = [
    ("sub1", ["git", "status"]),
    ("sub2", ["git", "log", "--oneline", "-5"]),
    ("sub1", ["git", "show", "HEAD"]),
    ("sub2", ["git", "push", "origin", "feature"]),
    ("sub1", ["git", "show", "HEAD", "--stat"]),
    ("sub2", ["git", "diff"]),
    ("sub1", ["git", "show", "3f035ac"]),
    ("sub2", ["git", "show"]),
]


def test_git_show_does_not_move_escalation_in_a_fleet_window():
    """Reads plus one real push, across two agents. Every call is allowed, and the
    escalation state the next action would see is the same before and after each
    git show; only the push counts as consequential."""
    recent, fleet, agents = [], 0.0, {}
    for i, (agent, argv) in enumerate(FLEET):
        c = classify(act(argv, agent, i))
        before = {a: (escalation_threshold(recent, a), escalation_triggered(recent, a))
                  for a in ("sub1", "sub2")}
        s = LedgerState(fleet, UNREACHABLE, agents.get(agent, 0.0), UNREACHABLE, list(recent))
        d = decide(act(argv, agent, i), c.level, c.cell, s)
        assert d.decision == "allow", (argv, d)
        fleet += d.price
        agents[agent] = agents.get(agent, 0.0) + d.price
        recent.append((agent, c.level))
        after = {a: (escalation_threshold(recent, a), escalation_triggered(recent, a))
                 for a in ("sub1", "sub2")}
        if argv[:2] == ["git", "show"]:
            assert (c.level, d.price) == (0, 0)
            assert after == before, argv
    consequential = [lvl for _, lvl in recent if lvl >= escalation_threshold(recent)]
    assert consequential == [4]
    assert not escalation_triggered(recent)
    assert fleet == 30


def test_git_show_below_the_fleet_threshold_even_at_l3():
    """With two agents in the window the threshold is L3; git show is still below it,
    so a window of one push plus any number of git show calls never triggers."""
    recent = [("sub1", 4)] + [("sub2", classify(act(["git", "show", f"HEAD~{n}"])).level)
                              for n in range(10)]
    assert escalation_threshold(recent) == 3
    assert not escalation_triggered(recent)
