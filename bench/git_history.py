"""Bench D: real multi-author git history as a fleet, edit_file actions only.

A small proof-of-mechanism diagnostic, not a benchmark. Question: in real shared
repos, does cross-author target convergence (several authors touching the same file
in one 30-minute window) go with commits that later turn out bad, and does real
elapsed time change anything compared with treating a window as one block?

Data: blobless clones of llvm/llvm-project, python/cpython and microsoft/vscode
under bench/data/git_history/ (clone commands in CLONE below; HEAD shas are recorded
in the output). Non-merge commits only, --no-renames (a rename is a delete plus an
add), shallow-boundary commits dropped (their diff is against an empty tree), bot
authors dropped.

Commit -> actions. One edit_file action per changed file, never a shell command
(a diff carries no argv, so none is reconstructed):
  status M/T/D  file existed before -> file_write_tracked_clean (L2, rev|local)
  status A      new file            -> file_write_untracked      (L3, irrev|local)
  agent_id = author email, ts = committer time (when the change landed in the shared
  tree), branch = "main" if on the default branch's first-parent chain, else "side".

Rogue-ish label (a cheap SZZ proxy, NOT a validated bug-inducing-commit algorithm:
no blame tracing, no issue linking). A commit is rogue-ish if, within LOOKAHEAD_DAYS
after it, a later commit
  - reverts it: "This reverts commit <sha>", "Reverts <owner>/<repo>#<PR>" (resolved
    through the "(#PR)" suffix of squash-merge subjects), or Revert "<its subject>"
  - names its sha (8+ hex, resolved against the repo) in a message that says fix /
    hotfix / fixup / broke / breakage / regression.
Everything else is "clean", which includes bad commits nobody reverted or referenced
by sha. The later commit is its "fixer". Commits in the last LOOKAHEAD_DAYS before
HEAD are not analysed (their label window is incomplete).

Windows. Fixed 30-minute buckets (epoch-aligned) of committer time; a bucket
qualifies when 3+ distinct authors commit in it.

Convergence
  window    shared_files = files touched by 2+ distinct authors in the window
  commit    crosses = touches a file some other author touches in the same window
            (before or after it); crosses_prior = only earlier in the window, which
            is what a runtime rule could see
  Confound: a fixer often lands in the same window and touches the same files as
  what it fixes, so convergence can come from the label itself. Every test is run
  twice, with fixers kept and with fixers removed from the history.
  Size confound: big commits both overlap more and get reverted more. Tests are
  permutation tests that shuffle the rogue-ish label within strata of
  (repo, files-changed bin), so the null keeps the size mix; unstratified Fisher
  exact p is reported next to it.

Timing
  decide() takes no time input: inside one window, time only sets the order. So
  "equally spaced" and "real" differ only in which actions share a window:
    bucket  each qualifying bucket is its own fresh ledger window
    real    the whole analysis stream runs through kosha_db's rule (a window
            starts at the first action after the previous one expired and lasts
            window_minutes), on real committer timestamps
  Convergence with real gaps: crosses_sliding = touches a file another author
  touched in the previous 30 real minutes (not bucket-bounded), plus the
  gap to that touch, compared for rogue-ish vs clean.

decide() runs as in replay.py: an ask is not charged and does not enter the window.
A commit is flagged if any of its file actions is asked.

Offline and deterministic (fixed seeds, pinned HEAD shas); no model calls.

Usage: python bench/git_history.py [--out bench/results/git_history.json] [--perms 5000]
"""
import argparse
import bisect
import json
import math
import random
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from kosha.pricing.policy import LedgerState, decide
from kosha.pricing.pricing import load_table

from replay import BY_ID, _level_price

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "bench/data/git_history"
OUT = ROOT / "bench/results/git_history.json"

CLONE = "git clone --filter=blob:none --no-checkout --single-branch --shallow-since=2026-07-01 {url}"
REPOS = {
    "llvm-project": {"url": "https://github.com/llvm/llvm-project.git", "since": "2026-09-05"},
    "cpython": {"url": "https://github.com/python/cpython.git", "since": "2026-07-08"},
    "vscode": {"url": "https://github.com/microsoft/vscode.git", "since": "2026-08-22"},
}
LOOKAHEAD_DAYS = 7
WINDOW_S = 30 * 60
MIN_AUTHORS = 3
SIZE_BINS = (1, 3, 10, 50)            # files changed: 1 | 2-3 | 4-10 | 11-50 | 51+
SEED = 20260926

BOT = re.compile(r"\[bot\]|(^|[-_.])bot@|noreply@github\.com$|^github-actions|dependabot", re.I)
REVERTS_SHA = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
REVERTS_PR = re.compile(r"\bReverts [\w.-]+/[\w.-]+#(\d+)")
REVERT_SUBJECT = re.compile(r'\brevert "(.+)"', re.I)
PR_SUFFIX = re.compile(r"\s*\((?:#|GH-)(\d+)\)\s*$")
FIX_WORDS = re.compile(r"\b(fix(es|ed)?|hotfix|fixup|broke|breakage|regression)\b", re.I)
# the sha must be the object of a fix/breakage phrase: "fix <sha>", "Fixes: <sha>",
# "broken by <sha>", "regression from <sha>", "follow-up to <sha>", "build after <sha>".
# "port the fix from <sha>" does not match.
FIX_REF = re.compile(r"\b(?:broken|broke|caused|introduced|regress(?:ed|ion)?|fix(?:es|ed|up)?|"
                     r"hotfix(?:es)?|follow[- ]?up|after):?\s+(?:by\s+|in\s+|since\s+|from\s+|to\s+|"
                     r"for\s+)?(?:commit\s+)?([0-9a-f]{8,40})\b", re.I)

TRACKED = _level_price(BY_ID["file_write_tracked_clean"])     # (level, cell, price)
UNTRACKED = _level_price(BY_ID["file_write_untracked"])


@dataclass
class Commit:
    sha: str
    author: str
    t: int
    subject: str
    body: str
    files: list[tuple[str, str]]       # (status, path)
    branch: str = "main"
    rogue: bool = False
    reasons: list[str] = field(default_factory=list)
    fixer: bool = False
    analysed: bool = False

    @property
    def paths(self) -> set[str]:
        return {p for _, p in self.files}


# --- load ---

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, errors="replace").stdout


def load_repo(repo: Path) -> tuple[list[Commit], dict]:
    """All non-merge commits reachable from HEAD, oldest first."""
    shallow = set((repo / ".git/shallow").read_text().split()) if (repo / ".git/shallow").exists() else set()
    first_parent = set(git(repo, "rev-list", "--first-parent", "HEAD").split())
    raw = git(repo, "log", "--no-merges", "--no-renames", "--name-status",
              "--format=%x1e%H%x1f%ae%x1f%ct%x1f%s%x1f%b%x1d", "HEAD")
    commits, stats = [], Counter()
    for rec in raw.split("\x1e")[1:]:
        head, _, tail = rec.partition("\x1d")
        sha, email, ct, subject, body = head.split("\x1f", 4)
        files = []
        for line in tail.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0]:
                files.append((parts[0][0], parts[-1]))
        if sha in shallow:
            stats["shallow_boundary"] += 1
            continue
        if BOT.search(email):
            stats["bot"] += 1
            continue
        if not files:
            stats["no_files"] += 1
            continue
        commits.append(Commit(sha, email.lower(), int(ct), subject, body, files,
                              "main" if sha in first_parent else "side"))
    commits.sort(key=lambda c: (c.t, c.sha))
    return commits, dict(stats)


# --- rogue-ish labels ---

def label(commits: list[Commit], all_shas: list[str]) -> Counter:
    by_sha = {c.sha: c for c in commits}
    by_pr = {}
    by_subject = defaultdict(list)
    for c in commits:
        m = PR_SUFFIX.search(c.subject)
        if m:
            by_pr[m.group(1)] = c
        by_subject[PR_SUFFIX.sub("", c.subject).strip()].append(c)
    sorted_shas = sorted(all_shas)

    def resolve(prefix: str) -> Optional[Commit]:
        i = bisect.bisect_left(sorted_shas, prefix)
        hits = [s for s in sorted_shas[i:i + 2] if s.startswith(prefix)]
        return by_sha.get(hits[0]) if len(hits) == 1 else None

    reasons = Counter()
    horizon = LOOKAHEAD_DAYS * 86400
    for fx in commits:
        msg = f"{fx.subject}\n{fx.body}"
        found: list[tuple[Commit, str]] = []
        found += [(c, "revert") for c in map(resolve, REVERTS_SHA.findall(msg)) if c]
        found += [(by_pr[n], "revert") for n in REVERTS_PR.findall(msg) if n in by_pr]
        m = REVERT_SUBJECT.search(fx.subject)
        if m:
            pr = PR_SUFFIX.search(m.group(1))
            prior = [c for c in by_subject.get(PR_SUFFIX.sub("", m.group(1)).strip(), []) if c.t <= fx.t]
            if pr and pr.group(1) in by_pr:
                found.append((by_pr[pr.group(1)], "revert"))
            elif prior:
                found.append((prior[-1], "revert"))
        if not found and FIX_WORDS.search(msg):
            found += [(c, "fix_ref") for c in map(resolve, (h.lower() for h in FIX_REF.findall(msg))) if c]
        for c, why in found:
            if c is fx or not (0 <= fx.t - c.t <= horizon):
                continue
            if not c.rogue or why not in c.reasons:
                reasons[why] += 1
            c.rogue = True
            c.reasons.append(why)
            fx.fixer = True
    return reasons


# --- windows ---

def size_bin(n: int) -> int:
    return bisect.bisect_left(SIZE_BINS, n)


def buckets(commits: list[Commit]) -> list[list[Commit]]:
    """Qualifying 30-minute buckets of analysed commits, in time order."""
    groups = defaultdict(list)
    for c in commits:
        if c.analysed:
            groups[c.t // WINDOW_S].append(c)
    return [g for _, g in sorted(groups.items()) if len({c.author for c in g}) >= MIN_AUTHORS]


def window_convergence(win: list[Commit]) -> dict:
    touch = defaultdict(list)               # path -> [(t, idx, author)]
    for i, c in enumerate(win):
        for p in c.paths:
            touch[p].append((c.t, i, c.author))
    shared = {p for p, ts in touch.items() if len({a for _, _, a in ts}) >= 2}
    crosses, prior = [], []
    for i, c in enumerate(win):
        others = [(t, j) for p in c.paths for t, j, a in touch[p] if a != c.author]
        crosses.append(bool(others))
        prior.append(any(j < i for _, j in others))
    return {"shared_files": len(shared), "crosses": crosses, "crosses_prior": prior}


def sliding_convergence(commits: list[Commit]) -> dict[str, Optional[int]]:
    """sha -> seconds since another author last touched one of its files within the
    previous WINDOW_S real seconds (None if no such touch). Over all analysed commits."""
    last: dict[str, list[tuple[int, str]]] = defaultdict(list)   # path -> recent (t, author)
    out = {}
    for c in commits:
        if not c.analysed:
            continue
        gaps = []
        for p in c.paths:
            last[p] = [(t, a) for t, a in last[p] if c.t - t <= WINDOW_S]
            gaps += [c.t - t for t, a in last[p] if a != c.author]
        out[c.sha] = min(gaps) if gaps else None
        for p in c.paths:
            last[p].append((c.t, c.author))
    return out


# --- decide() ---

def actions_of(c: Commit):
    for status, path in c.files:
        level, cell, _ = UNTRACKED if status == "A" else TRACKED
        yield path, level, cell


def run_decide(stream: list[Commit], table: dict, mode: str) -> dict[str, dict]:
    """sha -> {"flagged", "rules"}. mode "bucket": stream is one window. mode "real":
    kosha_db tumbling windows on committer time over the whole stream."""
    out = {}
    fleet, spent, recent, start = 0.0, Counter(), [], None
    for c in stream:
        if mode == "real" and (start is None or c.t - start >= WINDOW_S):
            fleet, spent, recent, start = 0.0, Counter(), [], c.t
        rules = Counter()
        for path, level, cell in actions_of(c):
            action = SimpleNamespace(action_id=f"{c.sha[:12]}:{path}", session_id="git", agent_id=c.author,
                                     harness="replay", tool="edit_file", raw={"path": path}, argv=[],
                                     cwd="", targets=[path], ts=str(c.t))
            state = LedgerState(fleet, table["fleet_budget"], spent[c.author], table["agent_cap"], list(recent))
            d = decide(action, level, cell, state)
            if d.decision == "allow":
                fleet, spent[c.author] = d.fleet_after, d.agent_after
                recent.append((c.author, level, c.sha))   # one commit = one escalation batch
            else:
                rules[d.rule] += 1
        out[c.sha] = {"flagged": bool(rules), "rules": dict(rules)}
    return out


# --- stats ---

def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2x2 [[a, b], [c, d]], two-sided Fisher exact p."""
    r1, c1, n = a + b, a + c, a + b + c + d

    def pmf(x):
        return math.comb(c1, x) * math.comb(n - c1, r1 - x) / math.comb(n, r1)

    p_obs = pmf(a)
    lo, hi = max(0, r1 - (n - c1)), min(r1, c1)
    return min(1.0, sum(pmf(x) for x in range(lo, hi + 1) if pmf(x) <= p_obs * (1 + 1e-9)))


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None}


def binom_cdf(k: int, n: int, p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 1.0 if p <= 0.0 or k >= n else 0.0
    lp, lq, lf = math.log(p), math.log1p(-p), math.lgamma(n + 1)
    return min(1.0, sum(math.exp(lf - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
                        for i in range(k + 1)))


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> list[float]:
    """Exact 95% CI for k/n, by bisection on the binomial tails."""
    def solve(f):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else solve(lambda p: 1 - binom_cdf(k - 1, n, p) < alpha / 2)
    upper = 1.0 if k == n else solve(lambda p: binom_cdf(k, n, p) > alpha / 2)
    return [round(lower, 4), round(upper, 4)]


def min_detectable(n_rogue: int, c: int, n_clean: int) -> Optional[float]:
    """Smallest rogue rate that would give Fisher p < 0.05 (above the clean rate)
    against these clean counts. A power check: the test cannot see anything smaller."""
    for k in range(n_rogue + 1):
        if k / n_rogue > c / n_clean and fisher_two_sided(k, n_rogue - k, c, n_clean - c) < 0.05:
            return round(k / n_rogue, 4)
    return None


def two_by_two(rows: list[dict], feature: str) -> dict:
    rg = [r for r in rows if r["rogue"]]
    cl = [r for r in rows if not r["rogue"]]
    a, c = sum(r[feature] for r in rg), sum(r[feature] for r in cl)
    return {"rogue": {**rate(a, len(rg)), "ci95": clopper_pearson(a, len(rg)) if rg else None},
            "clean": {**rate(c, len(cl)), "ci95": clopper_pearson(c, len(cl)) if cl else None},
            "fisher_p": round(fisher_two_sided(a, len(rg) - a, c, len(cl) - c), 4),
            "min_detectable_rogue_rate": min_detectable(len(rg), c, len(cl)) if rg and cl else None}


def stratified_perm(rows: list[dict], stat, perms: int, seed: int) -> dict:
    """Two-sided permutation p for stat(labels) with rogue labels shuffled within
    each row's "stratum". stat takes the list of labels aligned with rows."""
    rng = random.Random(seed)
    strata = defaultdict(list)
    for i, r in enumerate(rows):
        strata[r["stratum"]].append(i)
    labels = [r["rogue"] for r in rows]
    obs = stat(labels)
    null = []
    for _ in range(perms):
        perm = labels[:]
        for idx in strata.values():
            vals = [labels[i] for i in idx]
            rng.shuffle(vals)
            for i, v in zip(idx, vals):
                perm[i] = v
        null.append(stat(perm))
    mu = statistics.fmean(null)
    extreme = sum(abs(x - mu) >= abs(obs - mu) - 1e-12 for x in null)
    return {"observed": round(obs, 4), "null_mean": round(mu, 4),
            "p_two_sided": round((extreme + 1) / (perms + 1), 4)}


def diff_in_rate(rows: list[dict], feature: str):
    vals = [r[feature] for r in rows]

    def stat(labels):
        rg = [v for v, l in zip(vals, labels) if l]
        cl = [v for v, l in zip(vals, labels) if not l]
        return (sum(rg) / len(rg) if rg else 0) - (sum(cl) / len(cl) if cl else 0)
    return stat


def window_stat(wins: list[list[int]], shared: list[int]):
    """Diff in share of converging windows, windows with a rogue-ish commit minus
    without. wins: row indices per window."""
    def stat(labels):
        has = [any(labels[i] for i in w) for w in wins]
        a = [s > 0 for s, h in zip(shared, has) if h]
        b = [s > 0 for s, h in zip(shared, has) if not h]
        return (sum(a) / len(a) if a else 0) - (sum(b) / len(b) if b else 0)
    return stat


# --- one analysis pass ---

def analyse(repos: dict[str, list[Commit]], table: dict, perms: int, drop_fixers: bool) -> dict:
    rows, wins, shared, win_summ = [], [], [], []
    stream_rows = []
    for name, commits in repos.items():
        cs = [c for c in commits if not (drop_fixers and c.fixer)]
        slide = sliding_convergence(cs)
        real = run_decide([c for c in cs if c.analysed], table, "real")
        for w in buckets(cs):
            conv = window_convergence(w)
            bucket = run_decide(w, table, "bucket")
            idx = []
            for c, x, xp in zip(w, conv["crosses"], conv["crosses_prior"]):
                g = slide[c.sha]
                idx.append(len(rows))
                rows.append({"repo": name, "sha": c.sha, "rogue": c.rogue, "stratum": (name, size_bin(len(c.files))),
                             "files": len(c.files), "new_files": sum(s == "A" for s, _ in c.files),
                             "branch": c.branch, "crosses": x, "crosses_prior": xp,
                             "crosses_sliding": g is not None, "gap_s": g,
                             **{f"crosses_sliding_{m}m": g is not None and g <= m * 60 for m in (5, 10)},
                             "flag_bucket": bucket[c.sha]["flagged"], "flag_real": real[c.sha]["flagged"],
                             "rules_bucket": bucket[c.sha]["rules"], "rules_real": real[c.sha]["rules"]})
            wins.append(idx)
            shared.append(conv["shared_files"])
            win_summ.append({"repo": name, "commits": len(w), "authors": len({c.author for c in w}),
                             "shared_files": conv["shared_files"], "rogue": any(c.rogue for c in w)})
        stream_rows += [{"repo": name, "rogue": c.rogue, "stratum": (name, size_bin(len(c.files))),
                         "crosses_sliding": slide[c.sha] is not None, "flag_real": real[c.sha]["flagged"]}
                        for c in cs if c.analysed]

    res = {"qualifying_windows": len(wins), "commits_in_windows": len(rows),
           "rogue_in_windows": sum(r["rogue"] for r in rows),
           "per_repo": {n: {"windows": sum(w["repo"] == n for w in win_summ),
                            "commits": sum(r["repo"] == n for r in rows),
                            "rogue": sum(r["rogue"] for r in rows if r["repo"] == n)} for n in repos}}

    # window level
    rw = [w for w in win_summ if w["rogue"]]
    cw = [w for w in win_summ if not w["rogue"]]
    conv_r, conv_c = sum(w["shared_files"] > 0 for w in rw), sum(w["shared_files"] > 0 for w in cw)
    res["window_convergence"] = {
        "with_rogue": {**rate(conv_r, len(rw)),
                       "mean_shared_files": round(statistics.fmean([w["shared_files"] for w in rw]), 3) if rw else None,
                       "mean_commits": round(statistics.fmean([w["commits"] for w in rw]), 2) if rw else None},
        "without_rogue": {**rate(conv_c, len(cw)),
                          "mean_shared_files": round(statistics.fmean([w["shared_files"] for w in cw]), 3) if cw else None,
                          "mean_commits": round(statistics.fmean([w["commits"] for w in cw]), 2) if cw else None},
        "fisher_p_unstratified": round(fisher_two_sided(conv_r, len(rw) - conv_r, conv_c, len(cw) - conv_c), 4),
        "perm_stratified": stratified_perm(rows, window_stat(wins, shared), perms, SEED),
    }

    # commit level
    commit = {}
    for f in ("crosses", "crosses_prior", "crosses_sliding", "crosses_sliding_10m", "crosses_sliding_5m",
              "flag_bucket", "flag_real"):
        commit[f] = {**two_by_two(rows, f), "perm_stratified": stratified_perm(rows, diff_in_rate(rows, f), perms, SEED)}
    res["commit_level"] = commit
    res["commit_level_by_repo"] = {n: {f: two_by_two([r for r in rows if r["repo"] == n], f)
                                       for f in ("crosses", "flag_bucket")} for n in repos}

    # whole analysed stream (not only qualifying windows)
    res["stream"] = {"commits": len(stream_rows), "rogue": sum(r["rogue"] for r in stream_rows)}
    for f in ("crosses_sliding", "flag_real"):
        res["stream"][f] = {**two_by_two(stream_rows, f),
                            "perm_stratified": stratified_perm(stream_rows, diff_in_rate(stream_rows, f), perms, SEED)}

    # timing
    gaps_r = [r["gap_s"] for r in rows if r["rogue"] and r["gap_s"] is not None]
    gaps_c = [r["gap_s"] for r in rows if not r["rogue"] and r["gap_s"] is not None]
    agree = Counter((r["flag_bucket"], r["flag_real"]) for r in rows)
    res["timing"] = {
        "bucket_vs_real_flags": {"both": agree[(True, True)], "bucket_only": agree[(True, False)],
                                 "real_only": agree[(False, True)], "neither": agree[(False, False)]},
        "crosses_prior_bucket_vs_sliding": dict(Counter(
            f"bucket={r['crosses_prior']}/sliding={r['crosses_sliding']}" for r in rows)),
        "gap_minutes_median": {"rogue": round(statistics.median(gaps_r) / 60, 2) if gaps_r else None,
                               "clean": round(statistics.median(gaps_c) / 60, 2) if gaps_c else None,
                               "n_rogue": len(gaps_r), "n_clean": len(gaps_c)},
        "gap_mannwhitney": mann_whitney_perm(gaps_r, gaps_c, perms, SEED),
    }
    res["flag_rules"] = {"bucket": dict(sum((Counter(r["rules_bucket"]) for r in rows), Counter())),
                         "real": dict(sum((Counter(r["rules_real"]) for r in rows), Counter()))}
    res["flagged_by_new_files"] = {
        k: rate(sum(r["flag_bucket"] for r in rows if (r["new_files"] > 0) == (k == "has_new")),
                sum((r["new_files"] > 0) == (k == "has_new") for r in rows)) for k in ("has_new", "edits_only")}
    return res


def mann_whitney_perm(a: list, b: list, perms: int, seed: int) -> Optional[dict]:
    if not a or not b:
        return None
    pool = a + b
    order = sorted(range(len(pool)), key=lambda i: pool[i])
    ranks = [0.0] * len(pool)
    i = 0
    while i < len(order):                      # midranks for ties
        j = i
        while j + 1 < len(order) and pool[order[j + 1]] == pool[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    na, nb = len(a), len(b)

    def auc(r):
        return (sum(r[:na]) - na * (na + 1) / 2) / (na * nb)
    obs = auc(ranks)
    rng, null = random.Random(seed), []
    for _ in range(perms):
        rng.shuffle(ranks)
        null.append(auc(ranks))
    extreme = sum(abs(x - 0.5) >= abs(obs - 0.5) - 1e-12 for x in null)
    return {"auc_rogue_gt_clean": round(obs, 4), "p_two_sided": round((extreme + 1) / (len(null) + 1), 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--perms", type=int, default=5000)
    args = ap.parse_args()
    table = load_table()

    repos, meta = {}, {}
    for name, cfg in REPOS.items():
        path = DATA / name
        if not path.exists():
            raise SystemExit(f"missing {path}; run: cd {DATA} && {CLONE.format(url=cfg['url'])}")
        commits, dropped = load_repo(path)
        reasons = label(commits, [c.sha for c in commits])
        head_t = max(c.t for c in commits)
        since = int(datetime.fromisoformat(cfg["since"]).replace(tzinfo=timezone.utc).timestamp())
        until = head_t - LOOKAHEAD_DAYS * 86400
        for c in commits:
            c.analysed = since <= c.t < until
        an = [c for c in commits if c.analysed]
        repos[name] = commits
        meta[name] = {"url": cfg["url"], "head": git(path, "rev-parse", "HEAD").strip(),
                      "analysed_from": cfg["since"],
                      "analysed_until": datetime.fromtimestamp(until, timezone.utc).isoformat(timespec="minutes"),
                      "analysed_commits": len(an), "analysed_rogue": sum(c.rogue for c in an),
                      "analysed_fixers": sum(c.fixer for c in an), "authors": len({c.author for c in an}),
                      "label_links_all_history": dict(reasons), "dropped": dropped}

    out = {
        "bench": "D git_history",
        "caveat": ("rogue-ish = reverted or fix-referenced by sha within "
                   f"{LOOKAHEAD_DAYS} days: a cheap SZZ proxy, not a validated bug-inducing-commit "
                   "label. edit_file actions only; no shell commands reconstructed."),
        "window_minutes": WINDOW_S // 60, "min_authors": MIN_AUTHORS, "lookahead_days": LOOKAHEAD_DAYS,
        "levels": {"tracked (M/T/D)": list(TRACKED), "new (A)": list(UNTRACKED)},
        "perms": args.perms, "seed": SEED, "repos": meta,
        "fixers_kept": analyse(repos, table, args.perms, drop_fixers=False),
        "fixers_removed": analyse(repos, table, args.perms, drop_fixers=True),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
