"""Shared koshad client. Every adapter talks to koshad through this module and nothing
else calls requests directly, so the fail-closed policy lives in exactly one place.

Fail-closed: if koshad is unreachable, slow, erroring, or returns something that isn't
a Decision, the action is denied. A guardrail that goes invisible under its own
failure mode is worse than no guardrail.
"""
from __future__ import annotations

import os
from dataclasses import asdict

import requests

from kosha.pricing.policy import Decision
from kosha.system.action import Action

KOSHAD_URL = os.environ.get("KOSHAD_URL", "http://127.0.0.1:8765")


def fail_closed(reason: str = "koshad unreachable — failing closed") -> Decision:
    return Decision(decision="deny", reason=reason, level=-1, cell="unknown", price=0,
                    fleet_after=-1, agent_after=-1, rule="fail_closed")


def decide(action: Action, timeout: float = 2.0) -> Decision:
    try:
        r = requests.post(f"{KOSHAD_URL}/decide", json=asdict(action), timeout=timeout)
        r.raise_for_status()
        return Decision(**r.json())
    except (requests.RequestException, ValueError, TypeError):
        # RequestException covers Timeout/ConnectionError/HTTPError; ValueError/TypeError
        # cover a non-JSON body or a JSON body that isn't a Decision.
        return fail_closed()


def settle(action_id: str, outcome: str, timeout: float = 2.0) -> bool:
    """Best effort: a lost settle leaves the reservation charged (the conservative side)."""
    try:
        r = requests.post(f"{KOSHAD_URL}/settle",
                          json={"action_id": action_id, "outcome": outcome}, timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException:
        return False
