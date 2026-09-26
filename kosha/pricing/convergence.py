"""Target convergence (prototype): two or more distinct agents acting on the same
target within one window.

Used by policy.decide() as the "convergence" ask rule; measured first as a standalone
check in bench/convergence_synth.py.

A target is a typed string, "kind:value" (path, branch, db, host, k8s, container,
image, volume, patch). Action.targets holds paths for file tools but is empty for
run_command/git, so targets are also extracted from the command itself. Matching is
exact on the normalized value, never substring or prefix: `production-a1` and
`production-a2` are different targets, and `logs` does not match `logs/app.log`.

Causal: an action converges only with touches that already happened, i.e. earlier
entries of the window history, from a different agent. That is all a live system
can see when it decides.

Deterministic; no model calls.
"""
from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional

from kosha.pricing.match import strip_prefixes
from kosha.system.parser import split_command

if TYPE_CHECKING:
    from kosha.system.action import Action

MIN_LEVEL = 2               # read-only (L0) and blocked (L1) actions touch nothing
SHELL_OPERATORS = {"&&", "||", ";", "|", "&"}
REDIRECTS = {">", ">>", "1>", "2>", "&>", "__redirect__"}
SHELLS = {"sh", "bash", "zsh", "dash"}
FILE_KINDS = {"path", "patch"}
PATH_WRITERS = {"rm", "rmdir", "cp", "mv", "touch", "mkdir", "tee", "truncate", "ln"}
GIT_BRANCH_CMDS = {"push", "checkout", "switch", "branch", "merge", "rebase"}
NOT_A_BRANCH = re.compile(r"^(HEAD|FETCH_HEAD|ORIG_HEAD|@)([~^]\d*)*$|^[0-9a-f]{7,40}$")
GIT_PATH_CMDS = {"add", "rm", "mv", "restore", "apply", "am"}
DB_CLIENTS = {"psql", "mysql", "mariadb", "redis-cli", "sqlite3", "mongo", "mongosh"}
DOCKER_CONTAINER_CMDS = {"rm", "stop", "start", "restart", "kill", "exec", "inspect", "logs", "pause"}
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\s*(?=\n|$)", re.S)
# a target value looks like a name or path, not code or an operator
TARGET_VALUE = re.compile(r"^[\w$~@+./:=,%-]+$")
KUBECTL_VERBS = {"apply", "delete", "edit", "patch", "scale", "rollout", "set", "label",
                 "annotate", "create", "replace", "exec", "logs", "get", "describe", "cordon",
                 "drain", "expose", "autoscale"}
# option -> value consumed, per tool; values that are targets are handled separately
KUBECTL_OPTS = {"-n", "--namespace", "-f", "--filename", "-l", "--selector", "-o", "--output",
                "-c", "--container", "--context", "--replicas", "--image", "-p", "--patch", "--type"}
DB_OPTS = {"-d", "--dbname", "-h", "--host", "-U", "--username", "-p", "--port", "-c", "--command",
           "-f", "--file", "-u", "--user", "-D", "--database", "-n", "-a", "--pass"}


@dataclass(frozen=True)
class Touch:
    agent_id: str
    targets: frozenset[str]


@dataclass(frozen=True)
class Convergence:
    target: str
    earlier_agent: str
    earlier_index: int      # position in the window history of the earlier touch


def norm_path(p: str, cwd: str = "") -> str:
    p = p.strip()
    if not p:
        return ""
    if cwd and not p.startswith(("/", "~", "$")):
        p = posixpath.join(cwd, p)
    return posixpath.normpath(p)


def _positionals(args: list[str], opts_with_value: set[str]) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in opts_with_value:
            skip = True
        elif not a.startswith("-"):
            out.append(a)
    return out


def _opt(args: list[str], names: Iterable[str]) -> list[str]:
    names = set(names)
    out = []
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            out.append(args[i + 1])
        for n in names:
            if n.startswith("--") and a.startswith(n + "="):
                out.append(a.split("=", 1)[1])
    return out


def _git(args: list[str]) -> set[str]:
    if not args:
        return set()
    sub, rest = args[0], args[1:]
    pos = _positionals(rest, {"-m", "-b", "-B", "-c", "-C", "--message"})
    out = {f"branch:{b}" for b in _opt(rest, ("-b", "-B", "-c", "-C"))}
    if sub in GIT_BRANCH_CMDS:
        if sub == "push" and pos:
            pos = pos[1:]           # remote
        for ref in pos:
            if "--" in rest and rest.index("--") < rest.index(ref):
                out.add(f"path:{norm_path(ref)}")
            else:
                name = ref.split(":")[-1].removeprefix("refs/heads/")
                if not NOT_A_BRANCH.match(name):
                    out.add(f"branch:{name}")
    elif sub in GIT_PATH_CMDS:
        kind = "patch" if sub in ("apply", "am") else "path"
        out |= {f"{kind}:{norm_path(p)}" for p in pos if p != "."}
    return out


def _kubectl(args: list[str]) -> set[str]:
    ns = (_opt(args, ("-n", "--namespace")) or ["default"])[-1]
    out = {f"path:{norm_path(f)}" for f in _opt(args, ("-f", "--filename"))}
    pos = _positionals(args, KUBECTL_OPTS)
    if not pos or pos[0] not in KUBECTL_VERBS:
        return out
    pos = pos[1:]
    if pos and pos[0] in ("restart", "status", "undo", "history", "pause", "resume", "image"):
        pos = pos[1:]               # rollout restart deployment/x, set image deployment/x
    if pos and "/" in pos[0]:
        kind, name = pos[0].split("/", 1)
        out.add(f"k8s:{ns}/{kind}/{name}")
    elif len(pos) >= 2 and "=" not in pos[1]:
        out.add(f"k8s:{ns}/{pos[0]}/{pos[1]}")
    return out


def _db(cmd: str, args: list[str]) -> set[str]:
    out = {f"db:{d}" for d in _opt(args, ("-d", "--dbname", "-D", "--database"))}
    out |= {f"host:{h}" for h in _opt(args, ("-h", "--host"))}
    out |= {f"path:{norm_path(f)}" for f in _opt(args, ("-f", "--file"))}
    pos = _positionals(args, DB_OPTS)
    if cmd != "redis-cli" and pos:
        out.add(f"db:{pos[0]}")     # psql DBNAME / URL, mysql DB, sqlite3 FILE
    return out


def _docker(args: list[str]) -> set[str]:
    pos = _positionals(args, {"-t", "--tag", "--file", "-e", "--env", "-v", "--volume", "-p",
                              "--name", "-w", "-u", "--network"})
    if not pos:
        return set()
    sub, rest = pos[0], pos[1:]
    if sub in ("container", "image", "volume", "network") and rest:
        obj, verb, names = sub, rest[0], rest[1:]
        if verb in ("rm", "prune", "inspect", "create"):
            return {f"{'container' if obj == 'container' else obj}:{n}" for n in names}
        return set()
    if sub in DOCKER_CONTAINER_CMDS and rest:
        return {f"container:{rest[0]}"}
    if sub in ("build", "push", "pull", "tag", "rmi"):
        tags = _opt(args, ("-t", "--tag")) + (rest if sub != "build" else [])
        return {f"image:{t}" for t in tags if t != "-"}
    if sub == "run":
        return {f"container:{n}" for n in _opt(args, ("--name",))}
    return set()


def _paths(cmd: str, args: list[str]) -> set[str]:
    pos = [a for a in args if not a.startswith("-")]
    if cmd in ("chmod", "chown", "chgrp") and pos:
        pos = pos[1:]               # mode / owner
    if cmd == "sed":
        if not any(a.startswith("-i") or a == "--in-place" for a in args):
            return set()
        pos = pos[1:]               # the script
    return {f"path:{norm_path(p)}" for p in pos}


def segment_targets(argv: list[str], cwd: str = "") -> set[str]:
    """Targets of one simple command (no shell operators)."""
    argv = strip_prefixes([a for a in argv if a not in ("-E",)] if argv[:1] == ["sudo"] else argv)
    if argv[:1] == ["-u"] and len(argv) > 2:     # sudo -u USER cmd, after sudo was stripped
        argv = argv[2:]
    if not argv:
        return set()
    out = set()
    for i, a in enumerate(argv[:-1]):
        if a in REDIRECTS:
            out.add(f"path:{norm_path(argv[i + 1], cwd)}")
    argv = [a for i, a in enumerate(argv) if a not in REDIRECTS and (i == 0 or argv[i - 1] not in REDIRECTS)]
    if not argv:
        return out
    cmd, args = argv[0].rsplit("/", 1)[-1], argv[1:]
    if cmd in SHELLS and "-c" in args[:-1]:
        return out | command_targets(args[args.index("-c") + 1], cwd)
    if cmd == "git":
        out |= _git(args)
    elif cmd == "kubectl":
        out |= _kubectl(args)
    elif cmd in DB_CLIENTS:
        out |= _db(cmd, args)
    elif cmd == "docker":
        out |= _docker(args)
    elif cmd in PATH_WRITERS | {"chmod", "chown", "chgrp", "sed"}:
        out |= _paths(cmd, args)
    return {_resolve(t, cwd) for t in out if _valid(t)}


def _resolve(target: str, cwd: str) -> str:
    """File targets (path:, patch:) resolved against cwd, so a relative path in a
    command and the absolute path a file tool sends name the same file. Every
    extractor above normalises without cwd; this is the one place cwd is applied."""
    kind, value = target.split(":", 1)
    return f"{kind}:{norm_path(value, cwd)}" if kind in FILE_KINDS else target


def _valid(target: str) -> bool:
    value = target.split(":", 1)[1]
    return bool(value) and value not in (".", "/", "=") and not value.isdigit() and bool(TARGET_VALUE.match(value))


def command_targets(command: str, cwd: str = "") -> set[str]:
    """Targets of a full command line. Tracks `cd DIR` so relative paths after it
    resolve against DIR. Heredoc bodies are data, not commands, and are dropped."""
    out = set()
    command = HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], command)   # body is data
    for argv in split_command(command):
        if argv[:1] == ["cd"] and len(argv) > 1:
            cwd = norm_path(argv[1], cwd)
            continue
        out |= segment_targets(argv, cwd)
    return out


def targets_of(action: Action) -> frozenset[str]:
    """Action.targets (file-tool paths) plus targets extracted from the command, with
    relative file paths in either resolved against action.cwd."""
    out = set()
    cwd = getattr(action, "cwd", "") or ""
    for t in action.targets or []:
        out.add(t if ":" in t.split("/", 1)[0] else f"path:{norm_path(t, cwd)}")
    raw = action.raw or {}
    command = raw.get("command")
    if not command and action.argv:
        command = shlex.join(action.argv)
    if command:
        out |= command_targets(str(command), cwd)
    if raw.get("db"):
        out.add(f"db:{raw['db']}")
    return frozenset(out)


def touch(action: Action, level: int) -> Optional[Touch]:
    """What the ledger records for LedgerState.window_touches once this action is decided
    allow or ask (pending included); None if it touches nothing (below L2, or no target)."""
    if level < MIN_LEVEL:
        return None
    targets = targets_of(action)
    return Touch(action.agent_id, targets) if targets else None


def converges(history: list[Touch], agent_id: str, targets: Iterable[str]) -> Optional[Convergence]:
    """First earlier touch, by a different agent, of any of these targets."""
    targets = set(targets)
    for i, h in enumerate(history):
        if h.agent_id != agent_id:
            shared = targets & h.targets
            if shared:
                return Convergence(min(shared), h.agent_id, i)
    return None


def first_convergence(history: list[Touch]) -> Optional[tuple[int, Convergence]]:
    """Scan a window in order: index of the first touch that converges with an
    earlier one, and the match."""
    for i, h in enumerate(history):
        c = converges(history[:i], h.agent_id, h.targets)
        if c:
            return i, c
    return None
