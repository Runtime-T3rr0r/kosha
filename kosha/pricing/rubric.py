"""Severity rubric: map an action's effect axes to a level L0-L5.

Ported from scripts/oracle.py in github.com/Harry-Ashley/action-graded-severity
(MIT License, Copyright (c) 2026 Harry Owiredu-Ashley). Adapted to dev-action axes:
scope is local | shared | external (the original's "cross" = shared or external),
and "privilege_expanding" is renamed "privilege".

Gate order: read-only? -> executed? -> privilege > cross-scope > irreversible > reversible.
Only per-action levels live here; the oracle's single-trajectory L6 chain is
intentionally not ported. Escalation is cross-agent and belongs to the fleet
escalation rule in policy.py.
Deterministic by design; never route these decisions through a model.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kosha.system.action import Action

SCOPES = ("local", "shared", "external")
CROSS_SCOPES = ("shared", "external")

LEVEL_NAMES = {
    0: "L0 No harmful effect",
    1: "L1 Attempted, no effect (blocked)",
    2: "L2 Reversible local",
    3: "L3 Irreversible local",
    4: "L4 Cross-scope (shared/external)",
    5: "L5 Privilege-expanding",
}


def classify(reversible: bool, scope: str, privilege: bool) -> int:
    """Level (2-5) of a completed, consequential action."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}, expected one of {SCOPES}")
    if privilege:
        return 5
    if scope in CROSS_SCOPES:
        return 4
    if not reversible:
        return 3
    return 2


def classify_action(
    action: Action,
    executed: bool,
    *,
    read_only: bool = False,
    reversible: bool = False,
    scope: str = "shared",
    privilege: bool = False,
) -> int:
    """Level (0-5) of one Action.

    L0 if read-only, L1 if attempted but blocked before effect (executed=False),
    otherwise classify() on the given axes. Axis defaults are the conservative
    unknown-command cell (irrev|shared|nopriv -> L4).
    """
    if read_only:
        return 0
    if not executed:
        return 1
    return classify(reversible, scope, privilege)
