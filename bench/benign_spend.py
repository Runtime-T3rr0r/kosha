"""Benign-spend baseline: M1 price of every bash command in SWE-smith trajectories.

Dataset: SWE-bench/SWE-smith-trajectories (HuggingFace, MIT). Uses the `xml` split:
every action there is `<function=NAME><parameter=...>` text in the assistant message,
so bash commands extract with one pattern. The `tool` split mixes that form with
native tool_calls, and `ticks` puts bash and str_replace_editor calls in identical
bare code fences.

Per trajectory: bash command -> segments -> match_command -> classify_action ->
price, summed. One trajectory = one single-agent session = one budget window (the
data has no timestamps, so the 30-minute window reset is not modeled).

Totals:
  total             M1 as specified: unmatched commands price as `unknown` (L4, 40).
  total_matched     same, but unmatched commands price 0 -- a lower bound, not a
                    classification. The truth for a benign run sits between the two.
  total_with_edits  `total` plus str_replace_editor edits (create -> file_write_untracked,
                    str_replace/insert/undo_edit -> file_write_tracked_clean).

Offline and deterministic: no model calls. Shards download to bench/data/swesmith/.

Usage: python bench/benign_spend.py [--shards 6] [--limit N]
"""
import argparse
import json
import re
import shlex
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

from kosha.pricing.match import load_effects, match_command, segments
from kosha.pricing.policy import escalation_asks
from kosha.pricing.pricing import cell_of, load_table, price
from kosha.pricing.rubric import classify_action

ROOT = Path(__file__).resolve().parents[1]
REPO = "SWE-bench/SWE-smith-trajectories"
DATA = ROOT / "bench/data/swesmith"
OUT = ROOT / "bench/results/benign_spend.json"
SHARDS = 8
PERCENTILES = (50, 90, 95, 99)

CALL = re.compile(r"<function=(\w+)>(.*?)</function>", re.S)
PARAM = re.compile(r"<parameter=(\w+)>(.*?)</parameter>", re.S)
EDIT_ENTRY = {"create": "file_write_untracked", "str_replace": "file_write_tracked_clean",
              "insert": "file_write_tracked_clean", "undo_edit": "file_write_tracked_clean"}
ACTION = SimpleNamespace(action_id="bench", agent_id="main", tool="run_command", argv=[], targets=[])


def shard_path(i: int) -> Path:
    name = f"data/xml-{i:05d}-of-{SHARDS:05d}.parquet"
    return Path(hf_hub_download(REPO, name, repo_type="dataset", local_dir=DATA))


def calls(messages: list[dict]):
    for m in messages:
        if m["role"] == "assistant" and isinstance(m.get("content"), str):
            for fn, body in CALL.findall(m["content"]):
                yield fn, dict(PARAM.findall(body))


def level_and_price(entry: dict) -> tuple[int, float]:
    level = classify_action(ACTION, executed=True, read_only=entry["read_only"],
                            reversible=entry["reversible"], scope=entry["scope"],
                            privilege=entry["privilege"])
    return level, price(cell_of(entry["reversible"], entry["scope"], entry["privilege"]), level=level)


def shlex_ok(command: str) -> bool:
    try:
        shlex.split(command)
        return True
    except ValueError:
        return False


def score(messages: list[dict], edit_entries: dict, stats: Counter, unmatched: Counter) -> dict:
    total = matched_total = edits_total = 0.0
    levels, matched_levels, edit_levels = [], [], []
    for fn, params in calls(messages):
        if fn == "bash" and "command" in params:
            command = params["command"]
            stats["commands"] += 1
            stats["shlex_fail"] += not shlex_ok(command)
            for argv in segments(command):
                r = match_command(argv)
                if r["family"] == "unknown" and not argv:
                    continue
                stats["segments"] += 1
                level, p = level_and_price(r)
                levels.append(level)
                edit_levels.append(level)
                total += p
                stats["opaque_script"] += r["opaque_script"]
                if r["matched"]:
                    matched_total += p
                    matched_levels.append(level)
                else:
                    stats["unmatched"] += 1
                    unmatched[Path(argv[0]).name if argv[0] != "__redirect__" else ">"] += 1
        elif fn == "str_replace_editor" and params.get("command") in EDIT_ENTRY:
            stats["edits"] += 1
            level, p = edit_entries[params["command"]]
            edit_levels.append(level)
            edits_total += p
    return {"total": total, "total_matched": matched_total, "total_with_edits": total + edits_total,
            "escalation": escalates(levels), "escalation_matched": escalates(matched_levels),
            "escalation_with_edits": escalates(edit_levels)}


def escalates(levels: list[int]) -> bool:
    """Would policy's fleet escalation rule ask at any point in this session?"""
    return any(escalation_asks([("main", l) for l in levels[:i]], "main", lvl)
               for i, lvl in enumerate(levels))


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def asks_per_100(values: list[float], limit: float) -> float:
    return round(100 * sum(v > limit for v in values) / len(values), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", type=int, default=SHARDS, help="number of xml shards (1-8)")
    ap.add_argument("--limit", type=int, default=None, help="max trajectories")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()

    by_id = {e["id"]: e for e in load_effects()}
    edit_entries = {k: level_and_price(by_id[v]) for k, v in EDIT_ENTRY.items()}
    table = load_table()
    stats, unmatched, rows = Counter(), Counter(), []
    for i in range(a.shards):
        for batch in pq.ParquetFile(shard_path(i)).iter_batches(
                batch_size=256, columns=["traj_id", "model", "resolved", "messages"]):
            for rec in batch.to_pylist():
                row = score(json.loads(rec["messages"]), edit_entries, stats, unmatched)
                rows.append({"traj_id": rec["traj_id"], "model": rec["model"],
                             "resolved": rec["resolved"], **row})
                if a.limit and len(rows) >= a.limit:
                    break
            if a.limit and len(rows) >= a.limit:
                break
        if a.limit and len(rows) >= a.limit:
            break

    budget, cap = table["fleet_budget"], table["agent_cap"]
    dist = {}
    for key in ("total", "total_matched", "total_with_edits"):
        vals = [r[key] for r in rows]
        dist[key] = {
            **{f"p{q}": round(pct(vals, q), 1) for q in PERCENTILES},
            "max": max(vals),
            f"asks_per_100_at_fleet_budget_{budget:g}": asks_per_100(vals, budget),
            f"asks_per_100_at_agent_cap_{cap:g}": asks_per_100(vals, cap),
        }
    summary = {
        "dataset": f"{REPO} (xml split, {a.shards}/{SHARDS} shards)",
        "price_table": table["version"],
        "n_trajectories": len(rows),
        "n_resolved": sum(r["resolved"] for r in rows),
        "bash_commands": stats["commands"],
        "command_segments": stats["segments"],
        "unmatched_segments": stats["unmatched"],
        "unmatched_rate": round(stats["unmatched"] / max(stats["segments"], 1), 4),
        "opaque_script_segments": stats["opaque_script"],
        "opaque_script_rate": round(stats["opaque_script"] / max(stats["segments"], 1), 4),
        "shlex_fail_rate": round(stats["shlex_fail"] / max(stats["commands"], 1), 4),
        "editor_edits": stats["edits"],
        "sessions_with_escalation_ask_per_100": {
            k: round(100 * sum(r[k] for r in rows) / len(rows), 2)
            for k in ("escalation", "escalation_matched", "escalation_with_edits")},
        "distribution": dist,
        "top_unmatched_first_words": dict(unmatched.most_common(25)),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"summary": summary, "trajectories": rows}, indent=1))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
