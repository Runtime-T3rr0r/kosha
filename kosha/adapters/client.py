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


# What an agent sees when a call is blocked. The first line names the outcome so the
# agent can tell "wait for a human, then retry" from "retrying won't help" and from
# "kosha is down"; the last line is the next step. Nothing ran in any of these cases.
HELD = "KOSHA HELD FOR HUMAN APPROVAL: nothing was run."
DENIED = "KOSHA DENIED: nothing was run, and retrying the same call will be denied again."
UNAVAILABLE = "KOSHA UNAVAILABLE: nothing was run (Kosha could not decide, so it failed closed)."

NEXT_HELD = ("Next step: stop, tell the user which call is waiting and why, and wait. Once the "
             "user says a human has approved it in Kosha, retry this exact call with the "
             "same arguments. Do not work around it with other tools, scripts or commands.")
NEXT_DENIED = ("Next step: re-plan using the suggestion above, or ask the user. Do not work "
               "around it with other tools, scripts or commands.")
NEXT_UNAVAILABLE = ("Next step: tell the user Kosha is not responding, and retry this exact call "
                    "once they say it is back. Do not work around it.")


def block_text(d: Decision) -> str:
    """Agent-facing text for an ask or deny decision (kosha-mcp result, kosha-hook stderr)."""
    if d.rule == "fail_closed":
        head, nxt = UNAVAILABLE, NEXT_UNAVAILABLE
    elif d.decision == "ask":
        head, nxt = HELD, NEXT_HELD
    else:
        head, nxt = DENIED, NEXT_DENIED
    body = " ".join(x for x in (d.reason, d.suggestion or "") if x)
    if d.decision == "ask" and d.rule != "fail_closed":
        return f"{head}\n{body}\nThe human reviews it on the Kosha approval page: {KOSHAD_URL}/ui\n{nxt}"
    return f"{head}\n{body}\n{nxt}"


# --- the approval queue (read-only), for kosha-mcp's kosha_review tool ---

def approvals(timeout: float = 2.0) -> list[dict]:
    """Pending approvals with their bundles. Raises requests.RequestException if koshad
    doesn't answer; callers report that, nothing is approved by default."""
    r = requests.get(f"{KOSHAD_URL}/approvals", timeout=timeout)
    r.raise_for_status()
    return r.json()
