"""koshad endpoint contract, via FastAPI's TestClient against a temp DB."""
import pytest
from fastapi.testclient import TestClient

from kosha.api.server import create_app
from kosha.system.kosha_db import KoshaDB


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(KoshaDB(tmp_path / "k.db")))


def action(**kw):
    base = {"action_id": "a1", "session_id": "s1", "agent_id": "main", "harness": "bob",
            "tool": "run_command", "raw": {"command": "ls"}, "argv": ["ls"], "cwd": "/r",
            "targets": [], "ts": "2026-09-26T00:00:00Z"}
    return {**base, **kw}


DECISION_KEYS = {"decision", "reason", "level", "cell", "price", "fleet_after",
                 "agent_after", "rule", "suggestion"}


def test_decide_returns_a_decision(api):
    r = api.post("/decide", json=action())
    assert r.status_code == 200 and set(r.json()) == DECISION_KEYS


@pytest.mark.parametrize("payload", [
    action(argv=[]),
    {k: v for k, v in action().items() if k != "targets"},
    action(tool="launch_missiles"),
    action(argv=["ls", "&&", "rm", "-rf", "/"]),
    {},
    action(raw="not a dict", argv="not a list"),
])
def test_malformed_payloads_never_500(api, payload):
    r = api.post("/decide", json=payload)
    assert r.status_code == 200
    assert r.json()["decision"] in ("allow", "ask", "deny")


def test_compound_rm_rf_root_is_hard_denied(api):
    r = api.post("/decide", json=action(argv=["ls", "&&", "rm", "-rf", "/"])).json()
    assert (r["decision"], r["rule"]) == ("deny", "hard_deny")


def test_settle_and_session(api):
    d = api.post("/decide", json=action()).json()
    assert api.post("/settle", json={"action_id": "a1", "outcome": "success"}).json() == \
        {"status": "confirmed" if d["decision"] == "allow" else "pending"}
    s = api.get("/sessions/s1").json()
    assert s["session_id"] == "s1" and "main" in s["agents"]
    assert api.post("/settle", json={"action_id": "a1", "outcome": "maybe"}).status_code == 422


def test_approval_flow(api):
    # three L4-priced unknowns from different agents: third hits escalation or budget
    for i, agent in enumerate(["sub1", "sub2", "sub3"]):
        last = api.post("/decide", json=action(action_id=f"a{i}", agent_id=agent,
                                               argv=["frobnicate"])).json()
    assert last["decision"] == "ask"
    [ap] = api.get("/approvals").json()
    assert ap["bundle"][-1]["agent_id"] == "sub3"
    assert api.post(f"/approvals/{ap['id']}", json={"decision": "approve_once"}).json() == \
        {"status": "approve_once"}
    assert api.get("/approvals").json() == []
    assert api.post("/approvals/999", json={"decision": "deny"}).status_code == 404
    retry = api.post("/decide", json=action(action_id="r", agent_id="sub3",
                                            argv=["frobnicate"])).json()
    assert retry["decision"] == "allow"


def test_stream_emits_events(api):
    api.post("/decide", json=action())
    body = api.get("/stream", params={"once": True}).text
    assert "event: action_reserved" in body or "event: approval_requested" in body


def test_price_table(api):
    t = api.get("/price_table").json()
    assert "cells" in t and t["fleet_budget"] == 100


def test_decide_uses_the_parser(api):
    ls = api.post("/decide", json=action(action_id="r1", argv=["ls"], raw={"command": "ls"})).json()
    push = api.post("/decide", json=action(action_id="r2", raw={"command": "git push -f origin main"})).json()
    assert (ls["level"], ls["price"]) == (0, 0)
    assert (push["level"], push["cell"]) == (4, "irrev|shared|nopriv")
