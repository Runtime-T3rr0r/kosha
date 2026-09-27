"""make bob-install / bob-uninstall on a copy of Bob's global settings."""
import json

from kosha.adapters import bob_install

BOB_HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact", "PreToolUse", "PostToolUse", "Stop"}


def test_install_is_idempotent_keeps_other_settings_and_backs_up(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"migrations": {"x": True}, "approval": {"allowed_permissions": ["mcp"], "forbiddenApprovalGroups": []},
                             "hooks": {"PreToolUse": [{"matcher": "^other$", "hooks": [{"type": "command", "command": "/bin/true"}]}]}}))
    bob_install.install(p)
    bob_install.install(p)
    cfg = json.loads(p.read_text())
    assert cfg["migrations"] == {"x": True} and cfg["approval"]["forbiddenApprovalGroups"] == []
    assert cfg["approval"]["allowed_permissions"] == ["mcp", "todo"]
    pre = cfg["hooks"]["PreToolUse"]
    assert [e["matcher"] for e in pre] == ["^other$", "^mcp__kosha__"]            # ours once, others kept
    assert pre[1]["hooks"][0]["command"].endswith("kosha-hook")
    assert len(list(tmp_path.glob("settings.json.bak.kosha-*"))) >= 1
    # Bob's strict hook schema
    assert set(cfg["hooks"]) <= BOB_HOOK_EVENTS
    for e in pre:
        assert set(e) == {"matcher", "hooks"} and all(set(h) <= {"type", "command", "timeout", "disabled"} for h in e["hooks"])


def test_uninstall_removes_only_ours(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "^other$", "hooks": [{"type": "command", "command": "/bin/true"}]}]}}))
    bob_install.install(p)
    assert "removed" in bob_install.uninstall(p)
    assert [e["matcher"] for e in json.loads(p.read_text())["hooks"]["PreToolUse"]] == ["^other$"]
    assert bob_install.uninstall(p) == "nothing to remove"


def test_install_creates_missing_settings(tmp_path):
    p = tmp_path / "nested" / "settings.json"
    bob_install.install(p)
    assert json.loads(p.read_text())["hooks"]["PreToolUse"][0]["matcher"] == "^mcp__kosha__"
