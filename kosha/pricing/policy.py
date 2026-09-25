# kosha/pricing/policy.py — Pricing track owns this file, System track only ever imports from it
from dataclasses import dataclass
from typing import Literal, Optional


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
