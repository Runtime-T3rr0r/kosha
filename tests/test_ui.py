"""The human's approval page: served by koshad, self-contained, safe against
agent-written text, and driving only existing endpoints."""
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from kosha.api import server
from kosha.system.kosha_db import KoshaDB

SECRET = "ui-test-passphrase"


@pytest.fixture
def api(tmp_path):
    return TestClient(server.create_app(KoshaDB(tmp_path / "k.db"), approval_passphrase=SECRET))


def page(api) -> str:
    r = api.get("/ui")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    return r.text


def test_page_has_the_controls(api):
    html = page(api)
    for needle in ('id="pass"', 'id="unlock"', 'id="list"', 'id="done"', "Approve once", "Approve & reset window",
                   "Deny", "X-Kosha-Approval", 'new EventSource("/stream")'):
        assert needle in html, needle


def test_page_is_self_contained(api):
    # Simple Browser in Bob may have no network; nothing loads from outside
    assert not re.search(r"""(src|href)=["']https?://""", page(api))
    assert "@import" not in page(api)


def test_page_never_renders_agent_text_as_html(api):
    # commands/SQL/paths in the bundle are agent-written; they go in via textContent only
    html = page(api)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in html, sink


def test_passphrase_is_kept_in_page_memory_only(api):
    html = page(api)
    assert "localStorage" not in html and "sessionStorage" not in html and "document.cookie" not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_script_parses(api, tmp_path):
    script = re.search(r"<script>(.*)</script>", page(api), re.S).group(1)
    js = tmp_path / "ui.js"
    js.write_text(script)
    p = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_approvals_carry_why_they_were_held(api):
    api.post("/decide", json={"action_id": "a1", "session_id": "s", "agent_id": "sub1", "harness": "bob",
                              "tool": "run_command", "raw": {"command": "chmod 777 x.sh"},
                              "argv": ["chmod", "777", "x.sh"], "cwd": "/tmp", "targets": [], "ts": ""})
    [ap] = api.get("/approvals").json()
    assert ap["rule"] == "l5" and "always needs human approval" in ap["reason"] and ap["suggestion"]


def test_the_page_flow_against_koshad(api):
    # exactly what the page's buttons send
    api.post("/decide", json={"action_id": "a1", "session_id": "s", "agent_id": "sub1", "harness": "bob",
                              "tool": "run_command", "raw": {"command": "chmod 777 x.sh"},
                              "argv": ["chmod", "777", "x.sh"], "cwd": "/tmp", "targets": [], "ts": ""})
    [ap] = api.get("/approvals").json()
    assert api.post(f"/approvals/{ap['id']}", json={"decision": "approve_once", "note": None}).status_code == 401
    r = api.post(f"/approvals/{ap['id']}", json={"decision": "approve_once", "note": None},
                 headers={"X-Kosha-Approval": SECRET})
    assert r.json() == {"status": "approve_once"} and api.get("/approvals").json() == []
