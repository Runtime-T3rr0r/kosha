"""The recorded demo's storyline still plays as scripted against current pricing/policy.
If this fails, a pricing or policy change broke a demo beat: fix it before recording."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo_repo"))
import rehearse  # noqa: E402

from kosha.adapters import client  # noqa: E402
from kosha.system import resolvers  # noqa: E402


def test_demo_story_plays_as_scripted(tmp_path):
    env, url = dict(os.environ), client.KOSHAD_URL
    lines = []
    try:
        problems = rehearse.run(tmp_path / ".demo", out=lines.append)
    finally:
        os.environ.clear()
        os.environ.update(env)
        client.KOSHAD_URL = url
        resolvers.load_config.cache_clear()
    assert problems == [], "\n".join(lines)
    beats = {(b.agent, b.tool, b.result) for b in rehearse.STORY + rehearse.AFTER_APPROVAL}
    assert ("test-fix", "git", "held:convergence") in beats
    assert ("migrate-deploy", "run_command", "held:escalation") in beats
    assert ("migrate-deploy", "deploy", "allow") in beats
    # held calls stayed open and finished on their own once the human decided
    finals = {(b.agent, b.tool, b.final) for b in rehearse.STORY if b.resolves}
    assert finals == {("test-fix", "git", "denied_by_human"), ("migrate-deploy", "run_command", "allow")}


def test_show_bundle_formats_real_approvals(tmp_path):
    import json
    import subprocess
    from kosha.pricing.policy import decide as policy_decide
    from kosha.system.action import Action
    from kosha.system.kosha_db import KoshaDB
    db = KoshaDB(tmp_path / "k.db")
    db.decide(Action("a1", "s", "release-bump", "bob", "git", {"args": "push origin main"},
                     ["git", "push", "origin", "main"], "/w", [], ""), 4, "rev|shared|nopriv", policy_decide)
    db.decide(Action("a2", "s", "migrate-deploy", "bob", "db_exec", {"db": "prod", "sql": "drop table t"},
                     [], "/w", ["prod"], ""), 5, "*|*|priv", policy_decide)
    script = Path(__file__).resolve().parents[1] / "demo_repo" / "show_bundle.py"
    out = subprocess.run([sys.executable, str(script)], input=json.dumps(db.pending_approvals()),
                         capture_output=True, text=True, check=True).stdout
    assert "HELD: migrate-deploy db_exec drop table t" in out
    assert "release-bump    L4  git         git push origin main" in out and "<- held" in out
