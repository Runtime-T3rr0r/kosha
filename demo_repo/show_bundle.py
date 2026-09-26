"""Pretty-print `curl -s localhost:8765/approvals` for the camera:
    curl -s localhost:8765/approvals | python3 demo_repo/show_bundle.py
Read-only: it only formats what curl returned. Approving stays a plain curl."""
import json
import sys

for ap in json.load(sys.stdin):
    held = ap["bundle"][-1]
    what = held["argv"] or [held["raw"].get("sql") or held["raw"].get("path") or held["raw"].get("command")]
    print(f"\n#{ap['id']}  HELD: {ap['agent_id']} {held['tool']} {' '.join(map(str, what))}  (L{held['level']})")
    print("   this window, in order:")
    for e in ap["bundle"]:
        what = e["argv"] or [e["raw"].get("sql") or e["raw"].get("path") or e["raw"].get("command")]
        flag = "  <- held" if e["status"] == "pending" else ""
        print(f"     {e['agent_id']:15} L{e['level']}  {e['tool']:11} {' '.join(map(str, what))}{flag}")
