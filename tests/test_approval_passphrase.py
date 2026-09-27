"""Tier 2 self-approval guard: approvals need a passphrase only the human has."""
import io

import pytest
from fastapi.testclient import TestClient

from kosha.api import server
from kosha.system.kosha_db import KoshaDB

SECRET = "correct horse battery staple"


def held(api, action_id="a1"):
    body = {"action_id": action_id, "session_id": "s", "agent_id": "sub1", "harness": "bob",
            "tool": "run_command", "raw": {"command": "chmod 777 x.sh"}, "argv": ["chmod", "777", "x.sh"],
            "cwd": "/tmp", "targets": [], "ts": ""}
    assert api.post("/decide", json=body).json()["decision"] == "ask"
    [ap] = api.get("/approvals").json()
    return ap["id"]


@pytest.fixture
def api(tmp_path):
    return TestClient(server.create_app(KoshaDB(tmp_path / "k.db"), approval_passphrase=SECRET))


@pytest.mark.parametrize("headers", [{}, {"X-Kosha-Approval": "guess"}, {"X-Kosha-Approval": ""}])
def test_approval_without_the_passphrase_is_refused(api, headers):
    aid = held(api)
    r = api.post(f"/approvals/{aid}", json={"decision": "approve_once"}, headers=headers)
    assert r.status_code == 401
    assert [a["id"] for a in api.get("/approvals").json()] == [aid]          # still pending


def test_approval_with_the_passphrase_works(api):
    aid = held(api)
    r = api.post(f"/approvals/{aid}", json={"decision": "approve_once"}, headers={"X-Kosha-Approval": SECRET})
    assert r.json() == {"status": "approve_once"} and api.get("/approvals").json() == []


def test_the_passphrase_is_never_readable_from_koshad(api):
    aid = held(api)
    api.post(f"/approvals/{aid}", json={"decision": "deny", "note": "n"}, headers={"X-Kosha-Approval": SECRET})
    for path in ("/approvals", "/sessions/s", "/price_table", "/stream?once=true"):
        assert SECRET not in api.get(path).text, path


def test_without_a_passphrase_approvals_stay_open(tmp_path):
    api = TestClient(server.create_app(KoshaDB(tmp_path / "k.db")))
    aid = held(api)
    assert api.post(f"/approvals/{aid}", json={"decision": "approve_once"}).status_code == 200


# --- asking the human, on koshad's own terminal only ---

def test_no_terminal_means_no_passphrase_and_a_warning(monkeypatch, capsys):
    monkeypatch.setattr(server.sys, "stdin", io.StringIO(""))
    assert server.ask_passphrase() is None
    assert "NOT protected" in capsys.readouterr().err


def test_terminal_prompt_confirms_and_retries(monkeypatch):
    monkeypatch.setattr(server.sys.stdin, "isatty", lambda: True, raising=False)
    answers = iter(["one", "typo", "two", "two"])
    monkeypatch.setattr(server.getpass, "getpass", lambda prompt="": next(answers))
    assert server.ask_passphrase() == "two"


def test_empty_passphrase_is_an_explicit_opt_out(monkeypatch, capsys):
    monkeypatch.setattr(server.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(server.getpass, "getpass", lambda prompt="": "")
    assert server.ask_passphrase() is None and "NOT protected" in capsys.readouterr().err


def test_main_hands_the_passphrase_to_the_app_and_never_reads_env(monkeypatch):
    monkeypatch.setenv("KOSHA_APPROVAL_PASSPHRASE", "from-env-must-be-ignored")
    monkeypatch.setattr(server, "ask_passphrase", lambda: SECRET)
    seen = {}
    monkeypatch.setattr(server, "create_app", lambda **kw: seen.update(kw) or "app")
    import uvicorn
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, *a, **kw: None)
    server.main()
    assert seen == {"approval_passphrase": SECRET}
