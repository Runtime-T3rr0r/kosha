"""Watch the convergence rule fire: three decide() calls in one window.

  agent-1 edits deploy.yaml      -> allow
  agent-2 edits src/app.py       -> allow
  agent-3 edits deploy.yaml      -> ask (convergence)

Each decided action (allow or ask) records its targets as a touch, as the ledger
does. Budgets are the M1 defaults; all three edits are L2, far below them.

Usage: python scripts/demo_convergence.py
"""
from types import SimpleNamespace

from kosha.pricing.convergence import targets_of, touch
from kosha.pricing.match import load_effects
from kosha.pricing.policy import LedgerState, decide
from kosha.pricing.pricing import cell_of, load_table
from kosha.pricing.rubric import classify

TABLE = load_table()
EDIT = next(e for e in load_effects() if e["id"] == "file_write_tracked_clean")
LEVEL = classify(EDIT["reversible"], EDIT["scope"], EDIT["privilege"])
CELL = cell_of(EDIT["reversible"], EDIT["scope"], EDIT["privilege"])


def main() -> None:
    fleet, spent, recent, touches = 0.0, {}, [], []
    for i, (agent, path) in enumerate([("agent-1", "deploy.yaml"), ("agent-2", "src/app.py"),
                                       ("agent-3", "deploy.yaml")], 1):
        action = SimpleNamespace(action_id=f"demo-{i}", session_id="demo", agent_id=agent,
                                 harness="replay", tool="edit_file", raw={"path": path}, argv=[],
                                 cwd="/repo", targets=[path], ts="2026-09-26T00:00:00Z")
        state = LedgerState(fleet, TABLE["fleet_budget"], spent.get(agent, 0.0), TABLE["agent_cap"],
                            list(recent), list(touches))
        d = decide(action, LEVEL, CELL, state)
        print(f"{i}. {agent} edit_file {path}  targets={sorted(targets_of(action))}")
        print(f"   decision={d.decision}  rule={d.rule}  level=L{d.level}  price={d.price:g}  "
              f"fleet_after={d.fleet_after:g}")
        print(f"   reason: {d.reason}")
        if d.suggestion:
            print(f"   suggestion: {d.suggestion}")
        if d.decision in ("allow", "ask") and touch(action, LEVEL):
            touches.append(touch(action, LEVEL))
        if d.decision == "allow":
            fleet = d.fleet_after
            spent[agent] = d.agent_after
            recent.append((agent, LEVEL))


if __name__ == "__main__":
    main()
