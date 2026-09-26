"""Command matching: shell command -> config/effects.yaml entry.

Minimal offline parser: first word -> (family, sub). Context resolvers (git tracked
state, db scope, host allowlists) are not consulted here, so context-dependent rows
take their conservative variant (e.g. rm -> rm_untracked, a redirect -> file_write
untracked).

Candidates: same family, sub equal (or entry sub null), flags satisfied. Among them:
  1. a privilege entry beats any non-privilege entry;
  2. then specificity: a flags_any condition beats none, a sub beats sub-null, so
     `git checkout -- f` matches git_checkout_discard over git_checkout and
     `kubectl rollout status` matches kubectl_rollout_status over kubectl_rollout;
  3. then the higher rubric level (conservative).
File order in effects.yaml never decides between entries with different axes, so
reordering the file cannot downgrade a match (e.g. `chmod a+rwx +x` is chmod_777,
not chmod_exec, wherever the two entries sit).

`xargs [opts] CMD ...` and `npx [opts] CMD ...` are matched as CMD. Local scripts
run through an interpreter (python <file>, bash|sh <file>, npm run <script>), `make`,
and `npm|yarn|pnpm test` (a package.json script, so just as opaque) match the flat
opaque_script fallback and carry opaque_script=True, so callers can count how often
it fires.

A leading `sudo` (also behind env/time/nohup or VAR=1) always sets privilege=True on
the result, whatever the underlying command matched, including `unknown`.

File-writing commands (sed -i, cp, mv, tee FILE, a redirect) also return
write_paths: the paths a resolver must find tracked and clean before the match can
be lowered to its *_tracked_clean entry. Offline they take the untracked variant.

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

from kosha.pricing.rubric import classify

EFFECTS = Path(__file__).resolve().parents[2] / "config" / "effects.yaml"

OPERATORS = {"&&", "||", ";", "|", "&"}
REDIRECTS = {">", ">>"}
PREFIXES = {"sudo", "env", "time", "nohup"}
SHELL_READ = {"cd", "sort", "head", "echo"}
XARGS_OPTS_WITH_VALUE = {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "--max-args",
                         "--max-procs", "--max-lines", "--delimiter", "--arg-file"}
SECRET_VAR = re.compile(r"\$\{?\w*(key|token|secret|passw|credential)\w*", re.I)
# prod/staging as a whole name token: matches prod, prod_orders, db.prod.internal,
# production, staging2; not products, reproduce_db, preprod_x
SHARED_NAME = re.compile(r"(?<![a-z0-9])(?:prod(?:uction)?|staging)\d*(?![a-z0-9])", re.I)
PROD_NAME = re.compile(r"(?<![a-z0-9])prod(?:uction)?\d*(?![a-z0-9])", re.I)
SED_ARG_OPTS = {"-e", "-f", "-l", "--expression", "--file", "--line-length"}
READ_UTILS = {"tail", "rg", "wc", "jq", "date", "du", "uptime"}
# flags that make a read-only utility do something else: date -s sets the clock,
# rg --pre runs a command on every file. With one of these the command stays unknown.
READ_UTIL_UNSAFE = {"date": ("-s", "--set"), "rg": ("--pre",)}
LINTERS = {"flake8", "shellcheck"}
PKG_RUNNERS = {"npm", "yarn", "pnpm"}
KUBECTL_OPTS_WITH_VALUE = {"-n", "--namespace", "--context", "--kubeconfig", "--cluster",
                           "--user", "-s", "--server"}
NPX_OPTS_WITH_VALUE = {"-p", "--package", "-c", "--call"}


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


def has_sudo(argv: list[str]) -> bool:
    """sudo among the prefixes strip_prefixes removes (sudo x, env A=1 sudo x, ...)."""
    while argv and (argv[0] in PREFIXES or re.match(r"^\w+=", argv[0])):
        if argv[0] == "sudo":
            return True
        argv = argv[1:]
    return False


def sql_sub(argv: list[str]) -> Optional[str]:
    text = " ".join(argv)
    m = re.search(r"(?:-c|-e)\s+(.+)$", text) or re.search(r"\b(select|insert|update|delete|drop|truncate)\b.*", text, re.I)
    if not m:
        return None
    sql = m.group(0 if m.re.pattern.startswith("\\b") else 1).strip().lower()
    verb = sql.split()[0] if sql.split() else ""
    shared = SHARED_NAME.search(text)
    if verb == "select":
        return "select"
    if verb in {"insert", "update", "delete", "drop", "truncate"} and shared:
        return "write_shared"
    if verb == "update":
        return "update_where" if " where " in f" {sql} " else None
    if verb == "delete":
        return "delete_where" if " where " in f" {sql} " else "delete_no_where"
    return verb if verb in {"insert", "drop", "truncate"} else None


def sql_file(argv: list[str]) -> bool:
    """psql -f FILE / --file=FILE (mysql reads files via redirect, not a flag)."""
    return any(a in {"-f", "--file"} or a.startswith("--file=") for a in argv[1:])


def script_arg(args: list[str]) -> Optional[str]:
    """Script path handed to an interpreter, or None for inline code (-c) or a module (-m)."""
    for a in args:
        if a in {"-c", "-m"}:
            return None
        if not a.startswith("-"):
            return a
    return None


def operands(args: list[str]) -> list[str]:
    """Non-option arguments; everything after `--` counts."""
    out, rest = [], False
    for a in args:
        if rest or not a.startswith("-") or a == "-":
            out.append(a)
        elif a == "--":
            rest = True
    return out


def sed_in_place(args: list[str]) -> bool:
    """-i / --in-place, also inside a short-option cluster (-ni, -Ei). -i takes an
    attached suffix, so -ie is in-place with suffix "e"."""
    for a in args:
        if a == "--":
            break
        if a.startswith("--in-place"):
            return True
        if a.startswith("-") and not a.startswith("--"):
            for ch in a[1:]:
                if ch == "i":
                    return True
                if ch in "efl":
                    break
    return False


def sed_files(args: list[str]) -> list[str]:
    """Input files of a sed call: operands minus the script (the first operand, unless
    the script came from -e/-f)."""
    files, script_given, i = [], False, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            files.extend(args[i + 1:])
            break
        if a in SED_ARG_OPTS:
            script_given |= a in {"-e", "-f", "--expression", "--file"}
            i += 2
            continue
        if a.startswith(("--expression=", "--file=")) or (
                a.startswith("-") and not a.startswith("--") and len(a) > 2 and a[1] in "ef"):
            script_given = True
        elif not a.startswith("-") or a == "-":
            files.append(a)
        i += 1
    return files if script_given else files[1:]


def copy_paths(cmd: str, args: list[str]) -> list[str]:
    """Paths cp/mv write: the destination (dest/<name> per source when dest is a
    directory by -t or by 3+ operands), plus the sources for mv."""
    target, rest, i = None, [], 0
    while i < len(args):
        a = args[i]
        if a in {"-t", "--target-directory"} and i + 1 < len(args):
            target, i = args[i + 1], i + 2
            continue
        if a.startswith("--target-directory="):
            target = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    ops = operands(rest)
    if target is None and len(ops) < 2:
        return ops
    sources, dest = (ops, target) if target is not None else (ops[:-1], ops[-1])
    if target is not None or len(sources) > 1:
        written = [f"{dest.rstrip('/')}/{Path(src).name}" for src in sources]
    else:
        written = [dest]
    return written + (sources if cmd == "mv" else [])


def write_paths(argv: list[str]) -> list[str]:
    """Paths a file-writing command modifies (see module docstring); [] otherwise."""
    if not argv:
        return []
    if argv[0] == "__redirect__":
        return argv[1:2]
    cmd, args = Path(argv[0]).name, argv[1:]
    if cmd == "sed" and sed_in_place(args):
        return sed_files(args)
    if cmd == "tee":
        return [a for a in operands(args) if a != "/dev/null"]
    if cmd in {"cp", "mv"}:
        return copy_paths(cmd, args)
    return []


def kubectl_args(args: list[str]) -> list[str]:
    """kubectl args with global options before the subcommand removed, so
    `kubectl -n ns get pods` reads as `get pods`."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in KUBECTL_OPTS_WITH_VALUE else 1
    return args[i:]


def npx_command(argv: list[str]) -> list[str]:
    """The command npx runs, with npx and its options stripped ([] if none)."""
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in NPX_OPTS_WITH_VALUE else 1
    return argv[i:]


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
    if cmd in READ_UTILS:
        unsafe = READ_UTIL_UNSAFE.get(cmd, ())
        if any(a in unsafe or a.startswith(tuple(f"{u}=" for u in unsafe if u.startswith("--")))
               for a in args):
            return "unknown", None
        return "read_util", cmd
    if cmd in LINTERS:
        return "lint", cmd
    if (cmd == "playwright" and first == "test") or (cmd == "cypress" and first == "run"):
        return "test", "e2e"
    if cmd == "xargs":
        return "shell", "xargs"
    if cmd == "pytest" or (cmd.startswith("python") and args[:2] == ["-m", "pytest"]):
        return "test", "pytest"
    if cmd == "mkdir":
        return "mkdir", None
    if cmd == "rm":
        return "rm", "untracked"
    if cmd == "kubectl":
        rest = kubectl_args(args)
        if rest[:2] == ["create", "rolebinding"]:
            return "kubectl", "create_rolebinding"
        return "kubectl", rest[0] if rest else None
    if cmd in {"git", "helm", "terraform", "docker"}:
        if cmd == "git" and args == ["branch"]:
            return "git", "branch_list"
        return cmd, first
    if cmd in {"psql", "mysql", "sqlite3"}:
        if sql_file(argv):
            return "sql", "write_shared" if SHARED_NAME.search(text) else "file"
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
    if cmd == "sed" and sed_in_place(args):
        return "sed", "untracked"
    if cmd in {"cp", "mv"}:
        return cmd, "untracked"
    if cmd == "tee":
        return ("file_write", "untracked") if write_paths(argv) else ("shell", "tee")
    if cmd == "make":
        return "script", "opaque"
    if cmd == "alembic" or (cmd.startswith("python") and "migrate" in args):
        return "migrate", "local"
    if "deploy" in cmd:
        return "deploy", None
    if (cmd.startswith("python") or cmd in {"bash", "sh"}) and script_arg(args):
        return "script", "opaque"
    if cmd == "npm" and first == "run" and len(args) > 1:
        return "script", "opaque"
    if cmd in PKG_RUNNERS and first in {"test", "t"}:
        return "script", "opaque"
    return "unknown", None


def rank(entry: dict) -> tuple[bool, int, int]:
    m = entry["match"]
    specificity = (m["sub"] is not None) + 2 * bool(m["flags_any"])
    level = 0 if entry["read_only"] else classify(entry["reversible"], entry["scope"], entry["privilege"])
    return entry["privilege"], specificity, level


def match_command(argv: list[str], effects: Optional[tuple[dict, ...]] = None) -> dict:
    """Most specific effects entry for one argv.

    Returns {id, family, sub, flags, matched, opaque_script, reversible, scope,
    privilege, read_only, write_paths}; `flags` are the argv tokens that satisfied the
    entry's flags_any condition. privilege is forced True under a sudo prefix.
    """
    effects = effects if effects is not None else load_effects()
    sudo = has_sudo(list(argv))
    argv = strip_prefixes(list(argv))
    for name, inner in (("xargs", xargs_command), ("npx", npx_command)):
        if argv and Path(argv[0]).name == name and inner(argv):
            r = match_command(inner(argv), effects)
            return {**r, "privilege": r["privilege"] or sudo}
    family, sub = family_sub(argv) if argv else ("unknown", None)
    tokens = set(argv[1:])
    best = None
    if family != "unknown":
        for e in effects:
            m = e["match"]
            if m["family"] != family or (m["sub"] is not None and m["sub"] != sub):
                continue
            if m["flags_any"] and not tokens & set(m["flags_any"]):
                continue
            if best is None or rank(e) > rank(best):
                best = e
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
        "privilege": best["privilege"] or sudo,
        "read_only": best["read_only"],
        "write_paths": write_paths(argv) if family in {"file_write", "sed", "cp", "mv"} else [],
    }
