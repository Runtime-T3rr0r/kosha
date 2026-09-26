"""Action -> (level, cell): split the command, match each piece against effects.yaml,
refine context-dependent matches with the resolvers, and keep the most severe piece.

Matching itself is kosha.pricing.match (Pricing track); this module only feeds it
argv lists and upgrades its conservative context defaults where a resolver can tell:
  rm            -> rm_tracked_clean when every target is tracked and clean
  file write    -> ci_* / file_write_outside_workspace / _tracked_clean / _untracked
  sql write     -> sql_write_shared when the target DB resolves shared
  migrate       -> migrate_shared when the command mentions a shared DB
  http write    -> scope local when the host is loopback (per the effects.yaml note)
  unknown+sudo  -> privilege (AGENTS.md: unknown = L4, or L5 if privilege detected)

Anything that fails to parse or classify prices as the `unknown` entry.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

import bashlex

from kosha.pricing.match import load_effects, match_command
from kosha.pricing.match import segments as shlex_segments
from kosha.pricing.pricing import cell_of, price
from kosha.pricing.rubric import classify_action
from kosha.system import resolvers
from kosha.system.action import Action

SHELLS = {"bash", "sh", "zsh", "dash"}
WRITE_REDIRECTS = {">", ">>", ">|", "&>"}
ASSIGN = re.compile(r"^\w+=")


@dataclass
class Segment:
    argv: list[str]
    entry: str
    level: int
    cell: str
    price: float


@dataclass
class Classification:
    level: int
    cell: str
    price: float
    segments: list[Segment] = field(default_factory=list)


# --- splitting ---

class _Commands(bashlex.ast.nodevisitor):
    def __init__(self):
        self.out: list[list[str]] = []

    def visitcommand(self, node, parts):
        argv, redirects = [], []
        for p in parts:
            if p.kind == "word":
                argv.append(p.word)
            elif p.kind == "assignment" and not argv:
                argv.append(p.word)            # keep FOO=1 prefix; match strips it
            elif (p.kind == "redirect" and p.type in WRITE_REDIRECTS
                  and getattr(p.output, "kind", None) == "word"
                  and p.output.word != "/dev/null"):
                redirects.append(["__redirect__", p.output.word])
        if argv:
            self.out.append(argv)
        self.out.extend(redirects)


def split_command(command: str) -> list[list[str]]:
    """Command line -> argv per simple command, including ones inside $(...), loops and
    `sh -c '...'`; write redirects become ["__redirect__", target]. bashlex first,
    kosha.pricing.match's shlex splitter if bashlex can't parse it."""
    try:
        v = _Commands()
        for tree in bashlex.parse(command):
            v.visit(tree)
        segs = v.out
    except Exception:
        segs = shlex_segments(command)
    out = []
    for argv in segs:
        core = [a for a in argv if not ASSIGN.match(a)]
        if core and core[0].rsplit("/", 1)[-1] in SHELLS and "-c" in core[:-1]:
            out.extend(split_command(core[core.index("-c") + 1]))
        else:
            out.append(argv)
    return out


# --- per-segment classification ---

def _entry(eid: str) -> dict:
    e = next(e for e in load_effects() if e["id"] == eid)
    return {"id": eid, "reversible": e["reversible"], "scope": e["scope"],
            "privilege": e["privilege"], "read_only": e["read_only"]}


def _non_flags(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _file_write_entry(path: str, cwd: str, workspace: Optional[str]) -> dict:
    p = path.replace("\\", "/")
    if "/.github/workflows/" in f"/{p}":
        return _entry("ci_github_workflow_edit")
    if p.endswith(".gitlab-ci.yml"):
        return _entry("ci_gitlab_edit")
    if not resolvers.in_workspace(path, cwd, workspace):
        return _entry("file_write_outside_workspace")
    if resolvers.is_tracked_clean(path, cwd):
        return _entry("file_write_tracked_clean")
    return _entry("file_write_untracked")


def _db_targets(argv: list[str]) -> list[str]:
    out = []
    for i, a in enumerate(argv[1:], 1):
        prev = argv[i - 1]
        if prev in ("-d", "--dbname", "-h", "--host", "-D", "--database"):
            out.append(a)
        elif "://" in a or resolvers.SQLITE_FILE.search(a) or a in (resolvers.load_config().get("databases") or {}):
            out.append(a)
    return out


def _resolve(raw_argv: list[str], m: dict, command: str, cwd: str,
             workspace: Optional[str]) -> dict:
    argv = [a for a in raw_argv if not ASSIGN.match(a)]
    e = {k: m[k] for k in ("id", "reversible", "scope", "privilege", "read_only")}
    if m["id"] == "file_write_untracked" and argv and argv[0] == "__redirect__":
        return _file_write_entry(argv[1], cwd, workspace)
    if m["id"] == "rm_untracked":
        paths = _non_flags(argv[1:])
        if paths and any(not resolvers.in_workspace(p, cwd, workspace) for p in paths):
            return {**e, "scope": "shared"}
        if paths and all(resolvers.is_tracked_clean(p, cwd) for p in paths):
            return _entry("rm_tracked_clean")
    if m["family"] == "sql" and not m["read_only"] and m["id"] != "sql_write_shared":
        if any(resolvers.db_scope(t) == "shared" for t in _db_targets(argv)):
            return _entry("sql_write_shared")
    if m["id"] == "migrate_local":
        if resolvers.SHARED_HINT.search(command) or any(n in command for n in resolvers.shared_db_names()):
            return _entry("migrate_shared")
    if m["family"] == "http" and not m["read_only"]:
        hosts = [a for a in argv[1:] if "://" in a or (not a.startswith("-") and "." in a)]
        if hosts and all(resolvers.host_scope(h) == "local" for h in hosts):
            return {**e, "scope": "local"}
    if not m["matched"] and raw_argv and raw_argv[0] == "sudo":
        return {**e, "privilege": True}
    return e


def _segment(argv: list[str], e: dict, action: Action) -> Segment:
    level = classify_action(action, executed=True, read_only=e["read_only"],
                            reversible=e["reversible"], scope=e["scope"],
                            privilege=e["privilege"])
    cell = cell_of(e["reversible"], e["scope"], e["privilege"])
    return Segment(argv, e["id"], level, cell, price(cell, level=level))


def _command_texts(action: Action) -> list[str]:
    """raw command and argv, both if they disagree: which one actually runs is the
    adapter's business, so price whichever is worse."""
    raw = action.raw or {}
    texts = [raw[k] for k in ("command", "cmd") if isinstance(raw.get(k), str) and raw[k].strip()]
    if action.argv:
        joined = " ".join(shlex.quote(a) for a in action.argv)
        if not texts or split_command(texts[0]) != split_command(joined):
            texts.append(joined)
    return texts[:2]


def _write_paths(action: Action) -> list[str]:
    raw = action.raw or {}
    paths = list(action.targets)
    for k in ("path", "file_path", "filePath"):
        if isinstance(raw.get(k), str) and raw[k] not in paths:
            paths.append(raw[k])
    return paths


def segments_of(action: Action) -> list[Segment]:
    workspace = resolvers.workspace_of(action.cwd)
    cwd = action.cwd

    if action.tool in ("edit_file", "write_file"):
        paths = _write_paths(action)
        return [_segment(["__write__", p], _file_write_entry(p, cwd, workspace), action)
                for p in paths] or [_segment([], _entry("unknown"), action)]

    if action.tool == "deploy":
        return [_segment(["deploy"], _entry("deploy_trigger"), action)]

    if action.tool == "db_exec":
        raw = action.raw or {}
        sql, db = str(raw.get("sql") or ""), str(raw.get("db") or "")
        m = match_command(["psql", "-c", sql])
        e = {k: m[k] for k in ("id", "reversible", "scope", "privilege", "read_only")}
        if not m["read_only"] and resolvers.db_scope(db) == "shared":
            e = _entry("sql_write_shared")
        return [_segment(["db_exec", db, sql], e, action)]

    out = []
    for command in _command_texts(action):
        if action.tool == "git" and not command.startswith("git"):
            command = f"git {command}"
        for argv in split_command(command):
            m = match_command(argv)
            out.append(_segment(argv, _resolve(argv, m, command, cwd, workspace), action))
    return out or [_segment([], _entry("unknown"), action)]


def classify(action: Action) -> Classification:
    """Most severe segment wins: highest level, then highest price."""
    try:
        segs = segments_of(action)
    except Exception:
        segs = [_segment(list(action.argv), _entry("unknown"), action)]
    worst = max(segs, key=lambda s: (s.level, s.price))
    return Classification(worst.level, worst.cell, worst.price, segs)
