"""Policy: turn one classified, priced action plus fleet ledger state into allow | ask | deny.

Branch order (first match wins):
  hard_deny -> l5 -> escalation -> fleet_budget -> agent_cap -> ok

Deterministic by design. Classification lives in rubric.py, pricing in pricing.py;
this module only applies the rules. Every ask/deny carries a reason and a
suggestion of what would be allowed, so the agent can re-plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

from kosha.pricing.pricing import price as cell_price
from kosha.pricing.rubric import LEVEL_NAMES

if TYPE_CHECKING:
    from kosha.system.action import Action

CONSEQUENTIAL = 3          # L3+ counts toward the fleet escalation rule
SHELL_OPERATORS = {"&&", "||", ";", "|", "&"}
DROP_DB = re.compile(r"\bdrop\s+(database|schema)\b", re.I)


@dataclass
class Decision:
    decision: Literal["allow", "ask", "deny"]
    reason: str
    level: int             # 0-5
    cell: str              # e.g. "irrev|shared|nopriv"
    price: float
    fleet_after: float
    agent_after: float
    rule: str              # "fleet_budget" | "agent_cap" | "escalation" | "l5" | "hard_deny" | "ok"
    suggestion: Optional[str] = None


@dataclass
class LedgerState:
    """Stand-in for the window state the ledger database (kosha_db.py, not yet built)
    will supply. Exists so policy can be tested in isolation; the real source must
    provide this same shape.

    recent_actions: (agent_id, level) for actions already reserved/confirmed in the
    current window, excluding the action being decided.
    """
    fleet_spent: float
    fleet_budget: float
    agent_spent: float     # spend of the agent making this action
    agent_cap: float
    recent_actions: list[tuple[str, int]] = field(default_factory=list)


# --- hard-deny patterns ---

def _rm_rf_root(argv: list[str]) -> bool:
    """rm with recursive + force aimed at / (or /*), including sudo and split flags."""
    for i, tok in enumerate(argv):
        if tok != "rm":
            continue
        args = []
        for a in argv[i + 1:]:
            if a in SHELL_OPERATORS:
                break
            args.append(a)
        flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
        recursive = "r" in flags.lower() or "--recursive" in args
        force = "f" in flags or "--force" in args
        if recursive and force and any(a in ("/", "/*") for a in args):
            return True
    return False


def _drops_prod_db(action: Action) -> bool:
    raw = action.raw or {}
    text = " ".join([*action.argv, *(str(v) for v in raw.values())])
    if not DROP_DB.search(text):
        return False
    return any("prod" in t.lower() for t in [*action.targets, text])


def _has_approval_token(action: Action) -> bool:
    # Action has no dedicated field yet; the token is read from the raw tool input.
    return bool((action.raw or {}).get("approval_token"))


def _hard_deny_reason(action: Action) -> Optional[str]:
    if _rm_rf_root(action.argv):
        return "rm -rf on the filesystem root"
    if _drops_prod_db(action) and not _has_approval_token(action):
        return "dropping a prod database without an approval token"
    return None


# --- fleet escalation ---

def escalation_triggered(recent_actions: list[tuple[str, int]]) -> bool:
    """Two-plus consequential (>= L3) actions in the window, from any agent(s).

    Single agent or several, no distinctness check: one agent repeating consequential
    actions is covered the same as several agents each doing one.

    Deliberately does NOT require strictly rising levels. The rising requirement came
    from the source paper's single-trajectory escalation (one agent ramping up over
    steps) and was carried over without being re-derived for a fleet setting.
    Same-level consequential actions are not obviously less dangerous than one rising
    sequence, so requiring a rise would under-trigger on that case. Rising pairs still
    trigger: they are a subset of this condition, not a separate one.
    """
    return sum(1 for _, lvl in recent_actions if lvl >= CONSEQUENTIAL) >= 2


def decide(action: Action, level: int, cell: str, ledger_state: LedgerState) -> Decision:
    """Decide one action. fleet_after/agent_after are the balances after this decision:
    spent + price on allow, unchanged on ask/deny (nothing is reserved yet)."""
    p = cell_price(cell, level=level)
    s = ledger_state
    name = LEVEL_NAMES.get(level, f"L{level}")

    def out(decision, rule, reason, suggestion=None):
        charged = p if decision == "allow" else 0.0
        return Decision(decision, reason, level, cell, p,
                        s.fleet_spent + charged, s.agent_spent + charged, rule, suggestion)

    hard = _hard_deny_reason(action)
    if hard:
        return out("deny", "hard_deny", f"Kosha: denied, {hard} is a hard-deny pattern.",
                   "Target a specific path inside the workspace, or run against a non-prod "
                   "database / attach an approval token from a human.")

    if level >= 5:
        return out("ask", "l5", f"Kosha: {name} ({cell}) always needs human approval.",
                   "Wait for approval, or use a variant that doesn't grant access or add "
                   "persistence (e.g. chmod 644 instead of 777).")

    if level >= CONSEQUENTIAL and escalation_triggered(s.recent_actions):
        prior = ", ".join(f"{a} L{l}" for a, l in s.recent_actions if l >= CONSEQUENTIAL)
        return out("ask", "escalation",
                   f"Kosha: fleet escalation. Two-plus consequential actions this "
                   f"window ({prior}); this {name} action needs human approval.",
                   "Wait for a human to review the bundle, or switch to a non-consequential "
                   "alternative (dry run, plan, local-only change).")

    fleet_left = s.fleet_budget - s.fleet_spent
    if s.fleet_spent + p > s.fleet_budget:
        return out("ask", "fleet_budget",
                   f"Kosha: fleet budget. {s.fleet_spent:g}/{s.fleet_budget:g} spent this window; "
                   f"this {name} action ({cell}) costs {p:g}.",
                   f"Actions priced at most {max(fleet_left, 0):g} are still allowed, or ask a "
                   f"human to approve and reset the window.")

    agent_left = s.agent_cap - s.agent_spent
    if s.agent_spent + p > s.agent_cap:
        return out("ask", "agent_cap",
                   f"Kosha: agent cap. This agent has spent {s.agent_spent:g}/{s.agent_cap:g} "
                   f"this window; this {name} action ({cell}) costs {p:g}.",
                   f"Actions priced at most {max(agent_left, 0):g} are still allowed for this "
                   f"agent, or hand the step to a human.")

    return out("allow", "ok", f"Kosha: allowed, {name} ({cell}) costs {p:g}.")
