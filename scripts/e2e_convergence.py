"""End-to-end check of convergence and batch escalation through a running koshad.

Drives the real daemon path (POST /decide -> parser classification -> kosha_db
window state -> policy.decide), not decide() directly. Builds a throwaway git
workspace so file actions resolve like real ones (tracked file edits L2, new files
L3). Every run uses a fresh session id, so it never touches another session's
ledger rows.

  1. convergence: agent-1 edits deploy.yaml, agent-2 edits src/app.py, agent-3
     edits deploy.yaml -> expect ask, rule "convergence".
     Mixed form: agent-4 runs `sed -i ... deploy.yaml` (relative, with cwd) after
     agent-1's edit_file (absolute path) -> expect ask, rule "convergence": relative
     command paths resolve against cwd.
  2. batch: agent-6 does one small edit so two agents are in the window (L3
     threshold), then agent-5 writes 6 new files under one batch_id -> expect none
     asked by escalation.

Usage: start koshad (KOSHA_DB=/tmp/x.db KOSHAD_PORT=8799 koshad), then
       python scripts/e2e_convergence.py --url http://127.0.0.1:8799
"""
import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(f"{url}/decide", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="kosha-e2e-"))
    (ws / "src").mkdir()
    (ws / "deploy.yaml").write_text("replicas: 1\n")
    (ws / "src/app.py").write_text("print('hi')\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=e2e@local", "-c", "user.name=e2e",
                                               "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=ws, check=True)
    return ws


def show(n, agent, what, d):
    print(f"  {n}. {agent:8} {what:44} -> {d['decision']:5} rule={d['rule']:12} L{d['level']} "
          f"price={d['price']:g}")
    if d["decision"] != "allow":
        print(f"     reason: {d['reason']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    url = ap.parse_args().url
    ws = workspace()
    ok = True

    def act(session, agent, tool, **kw):
        body = {"session_id": session, "agent_id": agent, "harness": "claude_code", "tool": tool,
                "cwd": str(ws), **kw}
        return post(url, body)

    print(f"koshad {url}, workspace {ws}")
    s1 = f"e2e-conv-{uuid.uuid4().hex[:8]}"
    print(f"\n[1] convergence, session {s1}")
    steps = [("agent-1", "edit_file", "deploy.yaml"), ("agent-2", "edit_file", "src/app.py"),
             ("agent-3", "edit_file", "deploy.yaml")]
    out = []
    for i, (agent, tool, path) in enumerate(steps, 1):
        d = act(s1, agent, tool, targets=[str(ws / path)], raw={"path": path, "old": "1", "new": "2"})
        show(i, agent, f"{tool} {path}", d)
        out.append(d)
    got = [(d["decision"], d["rule"]) for d in out]
    want = [("allow", "ok"), ("allow", "ok"), ("ask", "convergence")]
    ok &= got == want
    print(f"  expected {want}\n  {'PASS' if got == want else 'FAIL'}")

    cmd = "sed -i 's/1/3/' deploy.yaml"
    d = act(s1, "agent-4", "run_command", argv=cmd.split(), raw={"command": cmd})
    show(4, "agent-4", f"run_command {cmd}", d)
    mixed = (d["decision"], d["rule"]) == ("ask", "convergence")
    ok &= mixed
    print(f"  mixed form (edit_file absolute vs sed relative) expected ask/convergence\n"
          f"  {'PASS' if mixed else 'FAIL'}")

    s2 = f"e2e-batch-{uuid.uuid4().hex[:8]}"
    batch = f"commit-{uuid.uuid4().hex[:8]}"
    print(f"\n[2] batch escalation, session {s2}, batch_id {batch}")
    d = act(s2, "agent-6", "edit_file", targets=[str(ws / "src/app.py")], raw={"path": "src/app.py"})
    show(1, "agent-6", "edit_file src/app.py (second agent in window)", d)
    rules = []
    for i in range(6):
        path = f"src/new_{i}.py"
        d = act(s2, "agent-5", "write_file", targets=[str(ws / path)], raw={"path": path, "content": "x"},
                batch_id=batch)
        show(i + 2, "agent-5", f"write_file {path} (batch {batch})", d)
        rules.append(d["rule"])
    escalated = rules.count("escalation")
    ok &= escalated == 0
    print(f"  expected: no escalation asks within one batch; got {escalated} of 6 asked by escalation\n"
          f"  {'PASS' if escalated == 0 else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
