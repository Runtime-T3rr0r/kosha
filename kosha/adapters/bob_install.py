"""One-time Bob setup for Kosha, in Bob's global settings (~/.bob/settings/settings.json).

    python -m kosha.adapters.bob_install install     # or: make bob-install
    python -m kosha.adapters.bob_install uninstall   # or: make bob-uninstall

install:
  - backs the file up (settings.json.bak.kosha-<time>);
  - adds a PreToolUse hook matching only Kosha's own MCP tools (^mcp__kosha__): kosha-hook
    stamps each call with its Bob tab's identity, so every tab is its own agent. It gates
    nothing (kosha-mcp does) and needs no koshad, so it can't break other projects;
  - makes sure the `mcp` and `todo` permission groups are auto-approved, so Bob doesn't
    ask before Kosha's tools (Kosha decides) or the agent's to-do list.
uninstall removes the hook entry (the permission groups stay: they may predate Kosha).
Global, not per workspace: Bob didn't run workspace hooks in testing; global ones need no
workspace trust or file watching.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".bob" / "settings" / "settings.json"
MATCHER = "^mcp__kosha__"
PERMISSIONS = ("mcp", "todo")


def hook_command() -> str:
    return str(Path(sys.executable).parent / "kosha-hook")


def _is_ours(entry: dict) -> bool:
    return entry.get("matcher") == MATCHER and any(
        "kosha-hook" in h.get("command", "") for h in entry.get("hooks", []))


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _save(path: Path, cfg: dict) -> Path | None:
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak.kosha-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(path)
    return backup


def install(path: Path = SETTINGS) -> str:
    cfg = _load(path)
    pre = cfg.setdefault("hooks", {}).setdefault("PreToolUse", [])
    pre[:] = [e for e in pre if not _is_ours(e)]          # idempotent: replace, never duplicate
    pre.append({"matcher": MATCHER,
                "hooks": [{"type": "command", "command": hook_command(), "timeout": 10}]})
    perms = cfg.setdefault("approval", {}).setdefault("allowed_permissions", [])
    for p in PERMISSIONS:
        if p not in perms:
            perms.append(p)
    backup = _save(path, cfg)
    return f"installed into {path}" + (f" (backup: {backup})" if backup else "")


def uninstall(path: Path = SETTINGS) -> str:
    cfg = _load(path)
    pre = cfg.get("hooks", {}).get("PreToolUse", [])
    kept = [e for e in pre if not _is_ours(e)]
    if len(kept) == len(pre):
        return "nothing to remove"
    cfg["hooks"]["PreToolUse"] = kept
    backup = _save(path, cfg)
    return f"removed the Kosha hook from {path} (backup: {backup})"


def main(argv: list[str]) -> None:
    if argv[:1] == ["install"]:
        print(install())
    elif argv[:1] == ["uninstall"]:
        print(uninstall())
    else:
        sys.exit("usage: python -m kosha.adapters.bob_install install|uninstall")


if __name__ == "__main__":
    main(sys.argv[1:])
