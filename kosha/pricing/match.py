"""Command matching: shell command -> config/effects.yaml entry.

Minimal offline parser: first word -> (family, sub). Context resolvers (git tracked
state, db scope, host allowlists) are not consulted here, so context-dependent rows
take their conservative variant (e.g. rm -> rm_untracked, a redirect -> file_write
untracked).

Specificity: same family, sub equal (or entry sub null), flags satisfied; entries
with a flags_any condition beat unconditional ones, entries with a sub beat sub-null
ones. So `git checkout -- f` matches git_checkout_discard over git_checkout, and
`kubectl rollout status` matches kubectl_rollout_status over kubectl_rollout.

`xargs [opts] CMD ...` is matched as CMD. Local scripts run through an interpreter
(python <file>, bash|sh <file>, npm run <script>) match the flat opaque_script
fallback and carry opaque_script=True, so callers can count how often it fires.

Unmatched commands resolve to the `unknown` entry (conservative default) with
matched=False, so callers can count coverage.
"""
from __future__ import annotations

import re
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

EFFECTS = Path(__file__).resolve().parents[2] / "config" / "effects.yaml"

OPERATORS = {"&&", "||", ";", "|", "&"}
REDIRECTS = {">", ">>"}
PREFIXES = {"sudo", "env", "time", "nohup"}
SHELL_READ = {"cd", "sort", "head", "echo"}
XARGS_OPTS_WITH_VALUE = {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "--max-args",
                         "--max-procs", "--max-lines", "--delimiter", "--arg-file"}
SECRET_VAR = re.compile(r"\$\{?\w*(key|token|secret|passw|credential)\w*", re.I)


@lru_cache(maxsize=None)
def load_effects(path: Path = EFFECTS) -> tuple[dict, ...]:
    return tuple(yaml.safe_load(Path(path).read_text()))


def segments(command: str) -> list[list[str]]:
    """Split a shell command line on operators and output redirects into argv lists.

    A redirect target becomes its own ["__redirect__", target] segment; redirects to
    /dev/null or another fd (2>&1) write nothing and are dropped.
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        tokens = command.split()
    segs, cur = [], []
    for tok in tokens:
        if tok in OPERATORS:
            segs.append(cur)
            cur = []
        elif tok in REDIRECTS:
            segs.append(cur)
            cur = ["__redirect__"]
        else:
            cur.append(tok)
    segs.append(cur)
    return [s for s in segs if s and not (s[0] == "__redirect__" and (len(s) < 2 or s[1] == "/dev/null" or s[1].startswith("&")))]


def strip_prefixes(argv: list[str]) -> list[str]:
    while argv and (argv[0] in PREFIXES or re.match(r"^\w+=", argv[0])):
        argv = argv[1:]
    return argv


def sql_sub(argv: list[str]) -> Optional[str]:
    text = " ".join(argv)
    m = re.search(r"(?:-c|-e)\s+(.+)$", text) or re.search(r"\b(select|insert|update|delete|drop|truncate)\b.*", text, re.I)
    if not m:
        return None
    sql = m.group(0 if m.re.pattern.startswith("\\b") else 1).strip().lower()
    verb = sql.split()[0] if sql.split() else ""
    shared = re.search(r"prod|staging", text, re.I)
    if verb == "select":
        return "select"
    if verb in {"insert", "update", "delete", "drop", "truncate"} and shared:
        return "write_shared"
    if verb == "update":
        return "update_where" if " where " in f" {sql} " else None
    if verb == "delete":
        return None if " where " in f" {sql} " else "delete_no_where"
    return verb if verb in {"insert", "drop", "truncate"} else None


def script_arg(args: list[str]) -> Optional[str]:
    """Script path handed to an interpreter, or None for inline code (-c) or a module (-m)."""
    for a in args:
        if a in {"-c", "-m"}:
            return None
        if not a.startswith("-"):
            return a
    return None


def xargs_command(argv: list[str]) -> list[str]:
    """The command xargs runs, with xargs and its options stripped ([] if none)."""
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in XARGS_OPTS_WITH_VALUE else 1
    return argv[i:]


def family_sub(argv: list[str]) -> tuple[str, Optional[str]]:
    if argv[0] == "__redirect__":
        return "file_write", "untracked"
    cmd, args = Path(argv[0]).name, argv[1:]
    text = " ".join(argv)
    first = args[0] if args else None
    if cmd == "cat" and any(a.endswith(".env") for a in args):
        return "secret", "cat_env"
    if cmd == "echo" and SECRET_VAR.search(text):
        return "secret", "echo_var"
    if cmd == "printenv" or (cmd == "env" and not args):
        return "secret", "printenv"
    if cmd in {"ls", "cat", "grep", "find"}:
        return "fs_read", cmd
    if cmd in SHELL_READ:
        return "shell", cmd
    if cmd == "xargs":
        return "shell", "xargs"
    if cmd == "pytest" or (cmd.startswith("python") and args[:2] == ["-m", "pytest"]):
        return "test", "pytest"
    if cmd == "mkdir":
        return "mkdir", None
    if cmd == "rm":
        return "rm", "untracked"
    if cmd in {"git", "kubectl", "helm", "terraform", "docker"}:
        if cmd == "kubectl" and args[:2] == ["create", "rolebinding"]:
            return "kubectl", "create_rolebinding"
        if cmd == "git" and args == ["branch"]:
            return "git", "branch_list"
        return cmd, first
    if cmd in {"psql", "mysql", "sqlite3"}:
        return "sql", sql_sub(argv)
    if cmd == "npm" and first in {"install", "i", "ci"}:
        return "pkg", "npm_install"
    if cmd == "npm" and first == "publish":
        return "pkg", "npm_publish"
    if cmd in {"pip", "pip3"} and first == "install":
        return "pkg", "pip_install"
    if cmd == "twine" and first == "upload":
        return "pkg", "twine_upload"
    if cmd in {"curl", "wget", "http"}:
        method = "get"
        for i, a in enumerate(args):
            if a in {"-X", "--request"} and i + 1 < len(args):
                method = args[i + 1].lower()
            elif a in {"-d", "--data", "--data-raw", "-F", "--form"} and method == "get":
                method = "post"
        return "http", method
    if cmd in {"chmod", "chown", "usermod", "crontab"}:
        return cmd, None
    if cmd == "systemctl":
        return "systemctl", first
    if "authorized_keys" in text:
        return "ssh", "authorized_keys"
    if cmd == "alembic" or (cmd.startswith("python") and "migrate" in args):
        return "migrate", "local"
    if "deploy" in cmd:
        return "deploy", None
    if (cmd.startswith("python") or cmd in {"bash", "sh"}) and script_arg(args):
        return "script", "opaque"
    if cmd == "npm" and first == "run" and len(args) > 1:
        return "script", "opaque"
    return "unknown", None


def match_command(argv: list[str], effects: Optional[tuple[dict, ...]] = None) -> dict:
    """Most specific effects entry for one argv.

    Returns {id, family, sub, flags, matched, opaque_script, reversible, scope,
    privilege, read_only}; `flags` are the argv tokens that satisfied the entry's
    flags_any condition.
    """
    effects = effects if effects is not None else load_effects()
    argv = strip_prefixes(list(argv))
    if argv and Path(argv[0]).name == "xargs" and xargs_command(argv):
        return match_command(xargs_command(argv), effects)
    family, sub = family_sub(argv) if argv else ("unknown", None)
    tokens = set(argv[1:])
    best, best_score = None, -1
    if family != "unknown":
        for e in effects:
            m = e["match"]
            if m["family"] != family or (m["sub"] is not None and m["sub"] != sub):
                continue
            if m["flags_any"] and not tokens & set(m["flags_any"]):
                continue
            score = (m["sub"] is not None) + 2 * bool(m["flags_any"])
            if score > best_score:
                best, best_score = e, score
    matched = best is not None
    if not matched:
        best = next(e for e in effects if e["id"] == "unknown")
    return {
        "id": best["id"],
        "family": family,
        "sub": sub,
        "flags": sorted(tokens & set(best["match"]["flags_any"])),
        "matched": matched,
        "opaque_script": best["id"] == "opaque_script",
        "reversible": best["reversible"],
        "scope": best["scope"],
        "privilege": best["privilege"],
        "read_only": best["read_only"],
    }
