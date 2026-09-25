"""Coverage of config/effects.yaml over StepShield run_command steps.

WHY data/test_holdout/ IS EXCLUDED -- do not add it back:
test_holdout (108 rogue + 108 clean, novel templates) is reserved, untouched, for the
final Bench A generalization number. Anything used to decide effects.yaml changes
leaks into that number. Tuning the table against test_holdout, even just by looking
at which commands it misses, turns the held-out result into a training result.
Only data/train/ and data/test/ may inform the table.

Usage: python bench/check_effects_coverage.py [--stepshield bench/data/stepshield]
"""
import argparse
import json
import re
import shlex
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "test")          # never "test_holdout", see module docstring
OPERATORS = {"&&", "||", ";", "|", "&"}
REDIRECTS = {">", ">>"}
PREFIXES = {"sudo", "env", "time", "nohup"}
SECRET_VAR = re.compile(r"\$\{?\w*(key|token|secret|passw|credential)\w*", re.I)
PLACEHOLDER = re.compile(r"^(example_command_\d+|sleep\b.*|true|false|:)$")
UNKNOWN_THRESHOLD = 0.15


def iter_commands(stepshield: Path):
    for split in SPLITS:
        for f in sorted((stepshield / "data" / split).rglob("*.jsonl")):
            if "mapping" in f.parts:
                continue
            text = f.read_text()
            try:                    # train files: one pretty-printed object despite .jsonl
                records = [json.loads(text)]
            except json.JSONDecodeError:
                records = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
            for rec in records:
                for step in rec.get("steps", []):
                    if step.get("action") == "run_command":
                        yield (step.get("arguments") or {}).get("command", "")


def segments(command: str) -> list[list[str]]:
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
    # redirects to /dev/null or another fd (2>&1) write nothing
    return [s for s in segs if s and not (s[0] == "__redirect__" and (len(s) < 2 or s[1] == "/dev/null" or s[1].startswith("&")))]


def strip_prefixes(argv: list[str]) -> list[str]:
    while argv and (argv[0] in PREFIXES or re.match(r"^\w+=", argv[0])):
        argv = argv[1:]
    return argv


def sql_sub(argv: list[str]) -> str | None:
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


def family_sub(argv: list[str]) -> tuple[str, str | None]:
    """Minimal parser: first word -> (family, sub). Context resolvers are not available
    offline, so context-dependent rows take their conservative variant."""
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
    if cmd == "rm":
        return "rm", "untracked"
    if cmd in {"git", "kubectl", "helm", "terraform", "docker"}:
        if cmd == "kubectl" and args[:2] == ["create", "rolebinding"]:
            return "kubectl", "create_rolebinding"
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
    return "unknown", None


def match(rules: list[dict], argv: list[str]) -> str | None:
    """Most specific rule wins: same family, sub equal (or rule sub null), flags satisfied;
    rules with a flags_any condition beat unconditional ones."""
    family, sub = family_sub(argv)
    if family == "unknown":
        return None
    tokens = set(argv[1:])
    best, best_score = None, -1
    for r in rules:
        m = r["match"]
        if m["family"] != family or (m["sub"] is not None and m["sub"] != sub):
            continue
        if m["flags_any"] and not tokens & set(m["flags_any"]):
            continue
        score = (m["sub"] is not None) + 2 * bool(m["flags_any"])
        if score > best_score:
            best, best_score = r["id"], score
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stepshield", type=Path, default=ROOT / "bench/data/stepshield")
    ap.add_argument("--effects", type=Path, default=ROOT / "config/effects.yaml")
    a = ap.parse_args()

    rules = yaml.safe_load(a.effects.read_text())
    per_rule, unknown_words = Counter(), Counter()
    steps = placeholders = segs_total = segs_unknown = steps_unknown = 0

    for command in iter_commands(a.stepshield):
        steps += 1
        if PLACEHOLDER.match(command.strip()):
            placeholders += 1
            continue
        step_unknown = False
        for argv in segments(command):
            argv = strip_prefixes(argv)
            if not argv:
                continue
            segs_total += 1
            rid = match(rules, argv)
            if rid:
                per_rule[rid] += 1
            else:
                segs_unknown += 1
                step_unknown = True
                unknown_words[Path(argv[0]).name] += 1
        steps_unknown += step_unknown

    scored = steps - placeholders
    print(f"splits:                 {', '.join(SPLITS)} (test_holdout excluded)")
    print(f"run_command steps:      {steps}")
    print(f"placeholder steps:      {placeholders} (example_command_N, sleep, true; skipped)")
    print(f"command segments:       {segs_total}")
    print(f"unknown segments:       {segs_unknown} ({segs_unknown / max(segs_total, 1):.1%})")
    print(f"steps with any unknown: {steps_unknown} ({steps_unknown / max(scored, 1):.1%} of non-placeholder steps)")
    print("\nmatches per rule id:")
    for r in rules:
        print(f"  {r['id']:<32} {per_rule[r['id']]}")

    if segs_unknown / max(segs_total, 1) > UNKNOWN_THRESHOLD:
        print("\ntop 30 unknown first-words:")
        for word, n in unknown_words.most_common(30):
            print(f"  {word:<24} {n}")


if __name__ == "__main__":
    main()
