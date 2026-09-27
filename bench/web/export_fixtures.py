"""Export the dashboard's JSON fixtures from committed bench results and config.

Reads bench/results/*.json, config/price_table.m1.json and config/effects.yaml and
writes bench/web/src/fixtures/{benchmarks,pricing,sessions}.json. Nothing here is
invented: summaries are copied, and the session streams are the composed fleets of
Bench B rebuilt with bench/compose_fleet.py's own seeds and functions, then decided
step by step with policy.decide so every chip carries the full Decision (reason,
suggestion) that compose_fleet.json only keeps for the first flag. Each rebuilt
fleet's first flag is checked against the stored one; a mismatch aborts the export.

Needs the StepShield checkout at bench/data/stepshield (as bench/replay.py does).
Offline and deterministic: no model calls.

Usage: python bench/web/export_fixtures.py
"""
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "bench")]

import yaml  # noqa: E402

from compose_fleet import (INTERLEAVES, SEED, SIZES, FLEETS_PER_CELL, agent_name,  # noqa: E402
                           build_pools, compose, first_flag_record, fleet_m1, interleave,
                           substitute, twin_of)
from kosha.pricing.policy import LedgerState, decide, escalation_threshold  # noqa: E402
from kosha.pricing.pricing import cell_of, load_table, price  # noqa: E402
from kosha.pricing.rubric import LEVEL_NAMES, classify_action  # noqa: E402
from replay import TOOL, _argv, load_trajectories  # noqa: E402

RESULTS = ROOT / "bench/results"
OUT = Path(__file__).resolve().parent / "src/fixtures"
STEPSHIELD = ROOT / "bench/data/stepshield/data/train"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path), "bytes": path.stat().st_size}


# --- benchmarks ---

def benchmarks() -> dict:
    files = {name: RESULTS / f"{name}.json" for name in
             ("stepshield_replay", "compose_fleet", "benign_spend", "convergence_synth", "git_history")}
    loaded = {name: json.loads(p.read_text()) for name, p in files.items()}
    conv = loaded["convergence_synth"]
    git = loaded["git_history"]
    return {
        "sources": {name: source(p) for name, p in files.items()},
        "stepshield": loaded["stepshield_replay"]["summary"],
        "compose_fleet": loaded["compose_fleet"]["summary"],
        "benign_spend": loaded["benign_spend"]["summary"],
        "benign_spend_hist": spend_histogram(loaded["benign_spend"]["trajectories"]),
        "convergence_synth": {k: v for k, v in conv.items() if k != "trajectories"},
        "git_history": {k: v for k, v in git.items() if not isinstance(v, list)},
        "m2": m2_status(),
    }


def spend_histogram(trajs: list[dict], width: int = 20, top: int = 1000) -> dict:
    """Per-run benign spend (total_with_edits) in bins of `width`, runs above `top`
    pooled into the last bin."""
    bins = Counter(min(int(t["total_with_edits"] // width), top // width) for t in trajs)
    return {"width": width, "top": top, "n": len(trajs),
            "counts": [bins.get(i, 0) for i in range(top // width + 1)]}


def m2_status() -> dict:
    p = ROOT / "config/price_table.m2.json"
    if not p.exists():
        return {"exists": False, "path": str(p.relative_to(ROOT))}
    return {"exists": True, "source": source(p), "table": json.loads(p.read_text())}


# --- pricing ---

def pricing() -> dict:
    table_path = ROOT / "config/price_table.m1.json"
    effects_path = ROOT / "config/effects.yaml"
    table = load_table()
    act = SimpleNamespace(action_id="x", agent_id="x", tool="run_command", argv=[], targets=[])
    effects = []
    for e in yaml.safe_load(effects_path.read_text()):
        level = classify_action(act, executed=True, read_only=e["read_only"],
                                reversible=e["reversible"], scope=e["scope"], privilege=e["privilege"])
        cell = cell_of(e["reversible"], e["scope"], e["privilege"])
        effects.append({"id": e["id"], "match": e.get("match") or {}, "reversible": e["reversible"],
                        "scope": e["scope"], "privilege": e["privilege"], "read_only": e["read_only"],
                        "note": e.get("note", ""), "level": level, "cell": cell,
                        "price": price(cell, level=level)})
    return {"sources": {"price_table": source(table_path), "effects": source(effects_path)},
            "table": table, "level_names": LEVEL_NAMES, "effects": effects}


# --- sessions: Bench B fleets rebuilt with full decisions ---

def full_stream(fleet: dict, stream, table: dict) -> list[dict]:
    """fleet_m1 from compose_fleet.py, keeping the whole Decision and the window
    state each decision saw."""
    fleet_spent = 0.0
    spent: Counter = Counter()
    recent: list[tuple[str, int]] = []
    out = []
    for i, (a, st) in enumerate(stream):
        aid = agent_name(a)
        rec = {"i": i, "agent_id": aid, "trajectory": fleet["members"][a].tid, "step": st.step,
               "action": st.action, "tool": TOOL.get(st.action, "other"), "command": st.command,
               "argv": _argv(st.command) if st.command else [], "path": st.path,
               "targets": [st.path] if st.path else [], "gated": st.gated, "note": st.note,
               "unknown_determined": st.unknown_determined,
               "is_rogue_agent": fleet["kind"] == "rogue_composed" and a == fleet["rogue_slot"],
               "at_or_after_divergence": bool(fleet["kind"] == "rogue_composed" and a == fleet["rogue_slot"]
                                              and st.step >= fleet["divergence"])}
        if not st.gated:
            out.append(rec | {"decision": None})
            continue
        action = SimpleNamespace(
            action_id=f"f{i}", session_id=fleet["id"], agent_id=aid, harness="replay",
            tool=rec["tool"],
            raw={"command": st.command} if st.command else ({"path": st.path} if st.path else {}),
            argv=rec["argv"], cwd="", targets=rec["targets"], ts="")
        state = LedgerState(fleet_spent, table["fleet_budget"], spent[aid], table["agent_cap"], list(recent))
        thr = escalation_threshold(recent, aid)
        d = decide(action, st.level, st.cell, state)
        rec |= {"decision": vars(d), "window": {
            "agents": sorted({x for x, _ in recent} | {aid}),
            "consequential": sum(lv >= thr for _, lv in recent), "threshold": thr,
            "fleet_spent_before": fleet_spent, "agent_spent_before": spent[aid]}}
        if d.decision == "allow":
            fleet_spent, spent[aid] = d.fleet_after, d.agent_after
            recent.append((aid, st.level))
        out.append(rec)
    return out


def raw_steps(tid: str) -> dict:
    """Original StepShield step text (thought) by step number, for the detail panel."""
    hits = list(STEPSHIELD.glob(f"*/{tid}.jsonl"))
    if not hits:
        return {}
    text = hits[0].read_text()
    try:
        rec = json.loads(text)
    except json.JSONDecodeError:
        rec = json.loads(text.splitlines()[0])
    return {s["step"]: s.get("thought", "") for s in rec.get("steps", [])}


PICKS = [
    # (pool, k, interleave, predicate on the stored row, label)
    ("aggregate_only", 3, "shuffle", lambda r: r["kind"] == "rogue_composed",
     "Aggregate-only rogue fleet: no member is flagged alone, M1 asks on the fleet"),
    ("aggregate_only", 3, "shuffle", lambda r: r["kind"] == "twin_swapped",
     "The same fleet with the rogue swapped for its clean twin (paired counterfactual)"),
    ("aggregate_only", 4, "shuffle", lambda r: r["kind"] == "rogue_composed" and r["M1_first_flag"]
     and r["M1_first_flag"]["is_rogue_agent"] and r["M1_first_flag"]["at_or_after_divergence"],
     "Four agents: the first M1 flag lands on the rogue agent at or after its divergence"),
    ("broad", 4, "round_robin", lambda r: r["kind"] == "rogue_composed" and r["M1_first_flag"]
     and r["M1_first_flag"]["is_rogue_agent"] and r["M1_first_flag"]["at_or_after_divergence"],
     "Broad pool: members may hold single L4 steps; the first M1 flag lands on the rogue agent"),
    ("aggregate_only", 5, "round_robin", lambda r: r["kind"] == "all_clean_composed",
     "Five clean agents: M1 still asks (the all-clean false alarm Bench B reports)"),
]


def sessions() -> dict:
    stored_path = RESULTS / "compose_fleet.json"
    stored = json.loads(stored_path.read_text())["fleets"]
    table = load_table()
    trajs, _ = load_trajectories()
    pools, twins, _ = build_pools(trajs, table)

    out = []
    for pname, k, mode, pred, label in PICKS:
        row = next(r for r in stored if r["pool"] == pname and r["k"] == k
                   and r["interleave"] == mode and pred(r))
        fleets = compose(pools[pname], k, random.Random(f"{SEED}-{pname}-{k}"), FLEETS_PER_CELL)
        rng = random.Random(f"{SEED}-{pname}-{k}-{mode}")
        streams, by_id = {}, {}
        for f in fleets:                      # same rng draw order as run_cell
            streams[f["id"]] = interleave(f["members"], mode, rng)
            by_id[f["id"]] = f
            if f["kind"] == "rogue_composed":
                t = twin_of(f, twins) | {"rogue_slot": f["rogue_slot"], "divergence": f["divergence"]}
                streams[t["id"]] = substitute(streams[f["id"]], t["members"])
                by_id[t["id"]] = t
        found, stream = by_id[row["id"]], streams[row["id"]]
        if {agent_name(a): m.tid for a, m in enumerate(found["members"])} != row["members"]:
            raise SystemExit(f"{row['id']}: rebuilt members differ from {stored_path.name}; "
                             "the stored results predate the current effects table")
        check = first_flag_record(found, stream, fleet_m1(stream, table))
        want = row["M1_first_flag"]
        keys = ("stream_index", "agent", "step", "rule", "decision")
        if (check and {x: check[x] for x in keys}) != (want and {x: want[x] for x in keys}):
            raise SystemExit(f"{row['id']}: rebuilt first flag {check} != stored {want}")

        steps = full_stream(found, stream, table)
        thoughts = {tid: raw_steps(tid) for tid in row["members"].values()}
        for s in steps:
            s["thought"] = thoughts[s["trajectory"]].get(s["step"], "")
        out.append({"id": row["id"], "label": label, "pool": pname, "k": k, "interleave": mode,
                    "kind": row["kind"], "members": row["members"],
                    "rogue_agent": agent_name(found["rogue_slot"]) if "rogue_slot" in found else None,
                    "rogue_divergence": found.get("divergence"),
                    "stored_first_flag": want, "steps": steps})
    return {"sources": {"compose_fleet": source(stored_path)}, "synthetic": True,
            "note": "Composed fleets are synthetic: single-agent StepShield trajectories interleaved "
                    "as concurrent agents. Recorded actions have no timestamps; the stream order is "
                    "the bench's interleaving. An ask is not charged and the replay continues.",
            "fleet_budget": table["fleet_budget"], "agent_cap": table["agent_cap"],
            "window_minutes": table.get("window_minutes"), "sessions": out}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("benchmarks", benchmarks), ("pricing", pricing), ("sessions", sessions)):
        data = fn()
        (OUT / f"{name}.json").write_text(json.dumps(data, separators=(",", ":"), default=str))
        print(f"wrote {name}.json ({(OUT / f'{name}.json').stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
