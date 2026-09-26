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

CONSEQUENTIAL = 3          # L3+ counts toward the escalation rule once 2+ agents are in the window
SINGLE_AGENT_CONSEQUENTIAL = 4   # L4+ while only one agent is in the window
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

def escalation_threshold(recent_actions: list[tuple[str, int]], agent_id: Optional[str] = None) -> int:
    """Level at which an action counts as consequential, by fleet size in the window.

    agent_id is the agent making the action being decided; it counts toward the
    window's distinct agents, so a second agent acting brings the threshold down.

    One distinct agent -> L4. Two or more -> L3. On benign single-agent SWE-smith runs
    (bench/benign_spend.py, n=26076, bash commands only) the rule asked in 95.21 of
    100 sessions before this split and 7.64 after. Most of that drop is the threshold
    itself: with the same effects table, L3+ still asks in 91.19 of 100, driven by
    routine L3 work (local scripts, rm of scratch files). For one agent L3+ is noise;
    consequential actions spread across several agents are the cross-agent signal
    the rule exists for, and keep the L3+ threshold.
    """
    agents = {a for a, _ in recent_actions} | ({agent_id} if agent_id is not None else set())
    return CONSEQUENTIAL if len(agents) >= 2 else SINGLE_AGENT_CONSEQUENTIAL


def escalation_triggered(recent_actions: list[tuple[str, int]], agent_id: Optional[str] = None) -> bool:
    """Two-plus consequential actions in the window, at escalation_threshold().

    No distinctness requirement on the triggering actions themselves: one agent
    repeating consequential actions counts the same as several agents each doing one.
    Fleet size only sets the threshold (L4+ for one agent, L3+ for two or more).

    Deliberately does NOT require strictly rising levels. The rising requirement came
    from the source paper's single-trajectory escalation (one agent ramping up over
    steps) and was carried over without being re-derived for a fleet setting.
    Same-level consequential actions are not obviously less dangerous than one rising
    sequence, so requiring a rise would under-trigger on that case. Rising pairs still
    trigger: they are a subset of this condition, not a separate one.
    """
    threshold = escalation_threshold(recent_actions, agent_id)
    return sum(1 for _, lvl in recent_actions if lvl >= threshold) >= 2


def escalation_asks(recent_actions: list[tuple[str, int]], agent_id: str, level: int) -> bool:
    """Whether this action (agent_id, level) is stopped by the escalation rule: it is
    itself consequential at the window's threshold and the window already triggered."""
    return (level >= escalation_threshold(recent_actions, agent_id)
            and escalation_triggered(recent_actions, agent_id))


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

    if escalation_asks(s.recent_actions, action.agent_id, level):
        threshold = escalation_threshold(s.recent_actions, action.agent_id)
        prior = ", ".join(f"{a} L{l}" for a, l in s.recent_actions if l >= threshold)
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
