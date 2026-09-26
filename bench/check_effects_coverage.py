"""Coverage of config/effects.yaml over StepShield run_command steps.

WHY data/test_holdout/ IS EXCLUDED -- do not add it back:
test_holdout (108 rogue + 108 clean, novel templates) is reserved, untouched, for the
final Bench A generalization number. Anything used to decide effects.yaml changes
leaks into that number. Tuning the table against test_holdout, even just by looking
at which commands it misses, turns the held-out result into a training result.
Only data/train/ and data/test/ may inform the table.

Commands are split with kosha.system.parser.split_command, the splitter /decide uses
(bashlex, with a shlex fallback when bashlex can't parse), so the coverage number is
what the runtime sees. Commands bashlex rejects are counted separately; their pieces
come from the fallback splitter.

Usage: python bench/check_effects_coverage.py [--stepshield bench/data/stepshield]
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import yaml

import bashlex

from kosha.pricing.match import match_command, strip_prefixes
from kosha.system.parser import split_command

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "test")          # never "test_holdout", see module docstring
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


def match(rules: tuple[dict, ...], argv: list[str]) -> str | None:
    r = match_command(argv, rules)
    return r["id"] if r["matched"] else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stepshield", type=Path, default=ROOT / "bench/data/stepshield")
    ap.add_argument("--effects", type=Path, default=ROOT / "config/effects.yaml")
    a = ap.parse_args()

    rules = tuple(yaml.safe_load(a.effects.read_text()))
    per_rule, unknown_words = Counter(), Counter()
    steps = placeholders = segs_total = segs_unknown = steps_unknown = fallback = 0

    for command in iter_commands(a.stepshield):
        steps += 1
        if PLACEHOLDER.match(command.strip()):
            placeholders += 1
            continue
        try:
            bashlex.parse(command)
        except Exception:
            fallback += 1
        step_unknown = False
        for argv in split_command(command):
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
    print(f"bashlex fallback steps: {fallback} (split by the shlex fallback, as at runtime)")
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
