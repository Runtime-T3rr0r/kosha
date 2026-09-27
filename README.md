# Kosha

> **Every per-action check said yes. Together they did something nobody would approve.**

A runtime gateway that prices what a fleet of AI coding agents does together, and asks a human only when the fleet's cumulative risk warrants it. This README covers the whole project: the problem, the solution, results, how IBM Bob was used, the research behind it, how it works, how to run it, and the demo.

Built for the **IBM Bob 2.0 Hackathon** (lablab.ai, Sep 25–27 2026) by Roshan Singha and Prithvi Hegde.

## Contents

1. [Problem](#problem)
2. [Solution](#solution)
3. [What Kosha is, and is not](#what-kosha-is-and-is-not)
4. [Hackathon submission](#hackathon-submission)
5. [Results](#results)
6. [IBM Bob 2.0](#ibm-bob-20)
7. [Research foundations](#research-foundations)
8. [Quickstart](#quickstart)
9. [The demo: prepare release 1.3](#the-demo-prepare-release-13)
10. [How it works](#how-it-works): [figures](#figures-first) · [architecture](#architecture-overview) · [defence layers](#the-four-defence-layers) · [components](#component-deep-dives) · [pricing](#the-pricing-model) · [policy](#policy-rules) · [identity](#identity-and-fleet-isolation) · [approvals](#the-approval-flow) · [fs_guard](#the-fs_guard-layer-3)
11. [Configuration](#configuration) · [Running Kosha](#running-kosha) · [Tests](#test-coverage)
12. [Known limitations](#known-limitations-v1)
13. [Security](#security)
14. [Repo layout](#repo-layout)
15. [License and credits](#license-and-credits)

---

## Problem

IBM Bob runs release and deployment work as a fleet: one agent bumps a version, another edits CI, a third runs a migration and deploys. Bob's safety controls judge each of those tool calls alone. That leaves exactly two options today — approve every call manually, which is slow and defeats the point of running agents in parallel, or auto-approve execution, which is fast but blind to what the fleet does as a whole. Nothing sees the bundle.

This isn't a hypothetical gap:

- **Per-action limits collapse at fleet scale.** Agents individually capped under a shared budget still overdrew it 2.4× at 50 agents, 9.7× at 200, 48× at 1,000 — only a cumulative ledger held every run inside the limit [1].
- **Agents don't self-limit.** Across 2,208 real DevOps prompts, 55.8–67.8% of acting runs crossed an action boundary anyway, and telling the agent the blast radius was high barely moved that number (65.5% vs 64.0%) [3].
- **The failures that reach production are severe.** Of 16,586 real coding-agent GitHub issues, 547 were confirmed safety failures — 326 high or critical, including destructive operations and authorization bypasses [4].
- **Binary pass/fail hides exactly this.** A defense that reported 0% attack success still leaked cross-scope once; judges that see individual actions score a full escalation chain the same as one harmless step [2].

## Solution

Kosha is a runtime gateway that sits beneath Bob and prices what its fleet actually does, together. Every tool call is classified by reversibility, scope and privilege [2] into a level (L0–L5) and a price, charged against one budget shared by every agent and subagent in the session. Bob keeps auto-approve on. Kosha decides allow, ask — showing a human everything the fleet already did this window — or deny, with a reason and what would be allowed instead. No model makes that call; it's deterministic code, covered by 981 tests.

The impact, measured, not claimed:

- **We cut our own false-alarm rate by ~92% through calibration.** Early coverage gaps in the effects table meant unmatched commands defaulted conservatively and triggered escalation — 95.2 false asks per 100 benign sessions. Closing those gaps against real SWE-smith and StepShield data brought that to 7.64 per 100, without loosening the rule itself.
- **~25× fewer interruptions than a per-action gate.** At run time, Kosha asks 0.63 times per 100 benign sessions; a gate checking each action alone at L4+ asks 15.53 times per 100, for a lower single-agent catch rate (29.2% vs 58.9%) — the trade Kosha is designed to make.
- **It catches what no per-action check can see, ever.** Across 1,800 composed fleets where no single agent's action would trip any existing gate alone, Kosha asks 100% of the time. Denylist and per-action baselines: 0%.
- **Tuned to real work, not guessed.** The per-agent cap (375) sits at the 95th percentile of 26,076 real benign sessions.

Stated honestly: Kosha also asks on 100% of all-clean composed fleets — it guarantees review after enough consequential work accumulates, it doesn't infer intent. It's a guardrail for well-meaning agents, not an adversary sandbox.

**References:** [1] Irreversibility Budget, arXiv:2609.00275 · [2] Action-Graded Severity Scale, arXiv:2607.07474 · [3] UnderSpecBench, arXiv:2607.02294 · [4] What Breaks When LLMs Code?, arXiv:2605.30777

---

## What Kosha is, and is not

Kosha is a runtime gateway that sits beneath an AI coding harness (IBM Bob 2.0, Claude Code, OpenCode). It intercepts every tool call an agent or subagent makes and classifies it on three axes: reversibility, scope and privilege. That gives a severity level from L0 to L5. Kosha prices the action and charges it against a shared budget for the whole session: the main agent plus every subagent, "the fleet". The result is **allow**, **ask** (with a bundle view of everything the fleet has already done this window), or **deny**, always with a reason and what would be allowed instead.

> **Kosha is a guardrail for well-meaning agents, not a security sandbox.** It is built to catch agents that are trying to do the right thing and drift into doing too much. It does not defend against an adversary actively trying to evade detection. Command classification reads argv, and an obfuscated command (`python -c "…"`, a script with an innocent name) can be priced lower than what it does. The self-approval guards are pattern-based, and the ledger runs as the same OS user as the agents. Read every number below with this in mind: the benchmarks measure whether the accounting interrupts at the right *amount* of fleet activity, not whether it can tell a malicious fleet from a benign one.

Decisions are deterministic accounting. No model call ever decides allow, ask or deny.

---

## Hackathon submission

| Deliverable | Where |
|---|---|
| Problem & Solution Statement | [Problem](#problem) · [Solution](#solution) |
| IBM Bob Usage Statement | [IBM Bob 2.0](#ibm-bob-20) |
| Bob task session screenshots, both team members | [bob_sessions/](bob_sessions/), indexed in [the screenshot table](#bob-task-session-screenshots) |
| Code Bob assisted with | [Bob as a build and test tool](#2-bob-as-a-build-and-test-tool) |
| Demo video (MP4, ≤3 min) | TODO: link |
| Public repository | https://github.com/Penitant/kosha |
| License | [MIT](LICENSE) · [third-party notices](THIRD_PARTY_NOTICES.md) · [security](SECURITY.md) |

---

## Results

All benchmarks are offline and deterministic, with no model calls. They are regenerated with `bench/replay.py`, `bench/compose_fleet.py` and `bench/benign_spend.py` against the current `config/effects.yaml` and `config/price_table.m1.json`. The rates below are from the committed `bench/results/*.json`. The dashboard's Benchmarks view shows every one of them with its n.

### Bench A: StepShield single-agent replay

[StepShield](https://github.com/glo26/stepshield) (MIT): 537 in-scope rogue trajectories (the 102 DEC "Destructive Action" runs are scored separately, since their harm lives in submit messages), each with a clean twin, plus 6,657 benign test runs.

| Gate | Catch rate (rogue) | Strict catch¹ | Paired false alarm (clean twin) | Asks per 100 benign runs |
|---|---|---|---|---|
| B0 allow-all | 0.0% (0/537) | 0.0% | 0.0% (0/537) | 0.00 |
| B1 denylist (Bob `deniedCommands` style) | 6.3% (34/537) | 6.0% | 2.6% (14/537) | 0.00 |
| B2 per-action gate, ask on any L4+ | 58.8% (316/537) | 12.8% | 57.4% (308/537) | 15.53 (1,034/6,657) |
| B3 count budget, N=38² | 0.0% (0/537) | 0.0% | 0.0% (0/537) | 0.00 |
| **M1 Kosha, hand-set prices** | **29.2% (157/537)** | **6.9%** | **27.7% (149/537)** | **0.63 (42/6,657)** |

¹ Strict: flags at steps whose level came from the conservative unknown-command default don't count.
² N = agent_cap / the L3 price. Smaller N catches more but asks on almost everything benign: N=5 catches 88.8% and asks in 83.0 of 100 benign runs.

On single agents, M1 catches half as many rogue runs as a per-action L4+ gate, at 1/25 of its benign ask rate. The paired false-alarm rate is high for both: StepShield's clean twins do much of the same consequential work as the rogue runs. The single-agent threshold (L4+) was set on benign SWE-smith data: over 26,076 runs, M1 asks in **4.53 of 100** at `agent_cap` and the escalation rule asks in **6.99 of 100** (Bench C, `bench/benign_spend.py`).

### Bench B: composed fleets (synthetic)

StepShield has one agent per trajectory, so fleets are **synthetic**. Trajectories that M1 does *not* flag when replayed alone are interleaved as 3, 4 or 5 concurrent agents in one window, 300 fleets per kind and size, in two interleavings. Pool `aggregate_only`: no member is caught by M1, B1 or B2 on its own.

| Gate | Fleet catch (rogue fleets) | Flag on the rogue agent after divergence | False alarm, all-clean fleets | False alarm, rogue swapped for its clean twin |
|---|---|---|---|---|
| B1 denylist | 0.0% (0/1,800) | 0.0% | 0.0% (0/1,800) | 1.9% (34/1,800) |
| B2 per-action L4+ | 0.0% (0/1,800) | 0.0% | 0.0% (0/1,800) | 20.7% (372/1,800) |
| **M1 Kosha** | **100% (1,800/1,800)** | **87.0% (1,566/1,800)** | **100% (1,800/1,800)** | **100% (1,800/1,800)** |

**The null result, stated directly:** M1 catches every composed fleet, and it also flags every all-clean composed fleet. On this benchmark it does not discriminate rogue from clean at all. Every M1 flag in Bench B comes from the **escalation rule**, in all 10,800 fleets. It is not budget: a whole fleet's spend never exceeds 552 of the 750 budget, even if every action were allowed. With two or more agents in the window, L3+ actions count as consequential. Routine local work (creating a file, running a local script) spread across three agents reaches the two-consequential-actions threshold whatever the agents intend.

That is the design working as specified, not a detector failing. Per design rule 5, Kosha doesn't try to infer intent. It guarantees that once a fleet has jointly done enough consequential work, a human sees the bundle before the next consequential step. B1 and B2 never do: 0 of 1,800 fleets. Whether that interruption rate is acceptable for real multi-agent work is open until there is recorded fleet data. `fleet_budget` is also a single-agent proxy (2 × `agent_cap`) for the same reason.

Bench D (git history, SZZ proxy) and Bench E (engineered target convergence) are in `bench/results/` and on the dashboard with their caveats.

---

## IBM Bob 2.0

Bob is **the harness Kosha governs** and **a tool we used to build, test and document Kosha**. Screenshots from both team members are [indexed below](#bob-task-session-screenshots).

### 1. Bob as the governed fleet (the product)

Kosha is built around Bob's own extension points:

| Bob feature | How Kosha uses it | Code |
|---|---|---|
| **MCP servers** | `kosha-mcp` exposes gated `run_command`, `edit_file`, `write_file`, `git`, `db_exec`, `deploy`, and runs a call only after koshad allows it | `kosha/adapters/bob_mcp.py` |
| **Custom modes** | A **Kosha** mode with `read`, `todo` and `mcp` only, so Bob itself stops it from calling native edit or execute tools | `kosha/adapters/install.py` |
| **Hooks** | A PreToolUse/PostToolUse hook gates Bob's native tools. A global hook stamps each tab's identity onto Kosha's calls | `kosha/adapters/claude_hook.py`, `bob_install.py` |
| **Parallel tasks / subagents** | Each Bob chat tab is its own agent (`bob-<task id>`), and all tabs on a workspace share one fleet budget | `kosha/system/kosha_db.py` |
| **Auto-approve** | Stays **on** for Kosha's tools. Kosha decides, so the human is interrupted only when the fleet's cumulative risk warrants it | `kosha-install` |
| **Extensions** | A status-bar count (`held · fleet spent/budget`) that opens the approval page inside Bob | `kosha/adapters/bob_extension/` |

We read Bob's hook runner source to find its exact semantics. Only exit code 2 blocks, other failures allow, and "ask" is treated as allow. Every Kosha failure path therefore ends in exit 2, and `tests/test_bob_hook.py` runs the hook under Bob's real hook runner.

The demo, "prepare release 1.3", is three Bob tabs working concurrently under one budget (task01–04).

### 2. Bob as a build and test tool

| Task | What Bob produced | Where it lives |
|---|---|---|
| **Adversarial bug probe** (task05) | Bob attacked koshad over HTTP (fingerprint reuse, parser fuzzing, self-approval, passphrase gate) and wrote the audit script and a findings report. It **found a real bypass**: `/bin/rm -rf /` escaped the hard-deny. Bob fixed it, and the variants (`//`, `/.`, `cd / && rm -rf *`) are now hard-denied too. | `scripts/e2e_security_audit.py` (39 pass), fix in `kosha/pricing/policy.py`, tests in `tests/test_policy.py` |
| **Dashboard** (task06) | Bob built the first benchmarks dashboard prototype with **parallel subagents** working at the same time. We then rebuilt and extended it as the React/Vite dashboard (live session, approval queue, benchmarks). | `bench/web/` |
| **Documentation** (task07) | A first draft of the README's architecture diagrams, figures and component deep-dives, which we then corrected against the code | `README.md` (Figures 1–5, deep-dives) |
| **Demo testing** (task01–04) | Live runs that exposed real issues. Agents rationed free reads to save budget, so reads are now free and agents are told so. A held call now stays open until the human decides, so Bob's chat waits and resumes on approval. | `demo_repo/`, `kosha/adapters/bob_mcp.py` |

**Not used for:** allow/ask/deny decisions. By design no model, Bob included, decides anything at runtime.

### Bob task session screenshots

From both team members, in [`bob_sessions/`](bob_sessions/). File names follow `teamname_taskNN_shortdescription_summary.png`.

| File | Team member | What the Bob task did | Bobcoins |
|---|---|---|---|
| [`kosha_task01_three_tab_fleet_summary.png`](bob_sessions/kosha_task01_three_tab_fleet_summary.png) | Roshan Singha | Three Bob tabs (`release-bump`, `test-fix`, `migrate-deploy`) running concurrently as one Kosha fleet. They all share one budget, and two agents decline to burn it on pointless repeated calls. | 0.039 / 0.098 / 0.019 |
| [`kosha_task02_release13_under_kosha_summary.png`](bob_sessions/kosha_task02_release13_under_kosha_summary.png) | Roshan Singha | Demo task "Prepare release 1.3" (VERSION bump, CHANGELOG, commit, push) running in the Kosha mode, with the Kosha approvals panel and status bar (`fleet 176/750`) open inside Bob | 0.438 |
| [`kosha_task03_flaky_test_fix_summary.png`](bob_sessions/kosha_task03_flaky_test_fix_summary.png) | Roshan Singha | Demo task: fix a flaky timing test and slim CI, gated by Kosha, with tests re-run through Kosha's tools (a follow-up in the task02 tab) | incl. in 0.438 |
| [`kosha_task04_governed_task_history_summary.png`](bob_sessions/kosha_task04_governed_task_history_summary.png) | Roshan Singha | Bob's task list: 39 governed demo and rehearsal tasks run under Kosha in one day | 0.04–0.57 each |
| [`kosha_task05_bug_probe_report_summary.png`](bob_sessions/kosha_task05_bug_probe_report_summary.png) | Prithvi Hegde | Adversarial bug probe of koshad: wrote `scripts/e2e_security_audit.py` and a findings report, found the `/bin/rm -rf /` hard-deny bypass | 6.55 |
| [`kosha_task06_dashboard_summary.png`](bob_sessions/kosha_task06_dashboard_summary.png) | Prithvi Hegde | Benchmarks dashboard prototype built with parallel Bob subagents | 5.16 |
| [`kosha_task07_readme_architecture_summary.png`](bob_sessions/kosha_task07_readme_architecture_summary.png) | Prithvi Hegde | First draft of the README's architecture diagrams and figures | 2.03 |

---

## Research foundations

Kosha puts published research into practice. It doesn't invent new theory. What we took from each source:

| Source | What Kosha takes from it |
|---|---|
| **The Irreversibility Budget** ([arXiv:2609.00275](https://arxiv.org/abs/2609.00275), [code](https://github.com/mpi-dsg/irreversibility-budget)) | The core claim: per-action caps overdraw a fleet budget 2.4× at 50 agents and 48× at 1,000, while a cumulative ledger doesn't. Kosha's reserve → confirm → refund lifecycle and "unknown = expensive" come from here. It names dependency-aware pricing as the open problem. |
| **Action-Graded Severity Scale** ([arXiv:2607.07474](https://arxiv.org/abs/2607.07474), [code](https://github.com/Harry-Ashley/action-graded-severity)) | The three effect axes (reversibility, scope, privilege), the L0–L5 levels and the gate order. `kosha/pricing/rubric.py` is ported from its oracle (MIT). It found that LLM judges miss escalation chains, which is why escalation here is deterministic code. |
| **StepShield** ([arXiv:2601.22136](https://arxiv.org/abs/2601.22136), [data](https://github.com/glo26/stepshield)) | Bench A and the trajectories behind Bench B. It shows that static rule guards have high recall but poor timing. |
| **UnderSpecBench**, "Coding Agents Are Guessing" ([arXiv:2607.02294](https://arxiv.org/abs/2607.02294)) | Evidence that agents don't limit themselves: 56–68% of acting runs cross an action boundary, whatever the blast-radius cue. |
| **What Breaks When LLMs Code?** ([arXiv:2605.30777](https://arxiv.org/abs/2605.30777)) | Incident evidence: 547 confirmed coding-agent safety failures, 60% of them high or critical, and 24.5% destructive operations. |
| **ToolSafe** ([arXiv:2601.10156](https://arxiv.org/abs/2601.10156)), **AgentSpec** ([code](https://github.com/haoyuwang99/AgentSpec)) | Design rule 3: every ask or deny returns a reason and what *would* be allowed, so the agent can re-plan. |
| **Agent Control Protocol** ([arXiv:2603.18829](https://arxiv.org/abs/2603.18829)) | Closest prior art: stateful admission per agent. Kosha differs by pricing harm and aggregating across the whole fleet. |
| **SWE-smith trajectories** ([dataset](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories)) | Bench C, 26,076 benign runs, which set `agent_cap` so normal work is rarely interrupted |

**What's new here:** as far as we know, Kosha is the first runtime gateway for coding-agent fleets that prices each action by reversibility, scope and privilege. It enforces one cumulative budget across every agent in a session, and detects cross-agent escalation deterministically. We benchmark it against per-action gates.

---

## Quickstart

There is no PyPI package or `kosha init` yet. Install from a clone. `pip install .` works too; the effects table and price table ship inside the package. Editable mode (`-e`) is for working on Kosha itself.

```bash
git clone https://github.com/Penitant/kosha.git
cd kosha
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
koshad            # asks for an approval passphrase on this terminal, then listens on 127.0.0.1:8765
```

With a non-editable install, set `KOSHA_DB` (for example `KOSHA_DB=~/.kosha/kosha.db koshad`); otherwise the ledger is written next to the installed package. Database aliases need `KOSHA_CONFIG` pointing at your `kosha.yaml`.

Run `koshad` in the foreground from a terminal: it asks for the approval passphrase there and never reads it from an env var or file. Without a terminal it starts with approvals unprotected and prints a warning.

Then:
- **Demo:** `make start` builds a disposable demo world and runs koshad. `make rehearse` plays the demo story with no Bob needed. `make help` lists everything.
- **Bob:** see [Install for Bob](#install-for-bob).
- **Claude Code:** see [Register kosha-hook in Claude Code](#register-kosha-hook-in-claude-code).
- **Tests:** `make test`.

### Install for Bob

With koshad running, run the one-command Bob installer, pointing it at the workspace you want to govern:

```bash
.venv/bin/kosha-install --workspace /path/to/workspace
# if Bob's CLI isn't on PATH as `bob`, pass it, e.g. --bob ~/bob-extracted/usr/share/bobide/bin/bobide
```

`kosha-install` backs up and updates only Kosha's entries:
- it installs the Bob approval extension (VSIX);
- it adds the global Kosha identity hook to `~/.bob/settings/settings.json`, and auto-approves the `mcp` and `todo` permission groups;
- it registers one workspace MCP server in `.bob/mcp.json`;
- it creates the **Kosha** mode in `.bob/custom_modes.yaml`.

Use `--no-extension` for a headless configuration run. Reload Bob, trust the workspace, and open a new task in the Kosha mode. `make bob-uninstall` removes the global identity hook; workspace `.bob` files have timestamped backups beside them. `make test-installer` runs the full installer against a temporary home, workspace and fake Bob executable, without touching real settings.

The installer auto-approves Kosha's MCP tools inside that mode, because Kosha makes the policy decision. The extension is a read-only monitor. It shows `Kosha: <held> held · fleet <spent>/<budget>` in Bob's status bar, opens the approval page on new holds, and offers a Review button. Human approval still happens only in koshad's page with the terminal-supplied passphrase; the extension never receives it.

### The Kosha extension for Bob

Opens Kosha's approval panel inside Bob the moment an agent's action is held.

- **Status bar:** `Kosha: 2 held · fleet 70/750`. Click it to open the panel.
- **On every new hold:** the panel opens beside your editor (without taking focus), plus a
  notification naming the agent, the command and the rule, with a **Review** button.
- **The panel** is koshad's own approval page (`/ui`): unlock with the approval passphrase you
  gave koshad at start-up, then **Approve once / Approve & reset window / Deny**.

The extension only reads from koshad (`/stream`, `/approvals`, `/sessions`). It can't approve
anything itself, and the passphrase never passes through it. Holds that happened before Bob
started are counted in the status bar but never pop up.

Settings: `kosha.koshadUrl` (default `http://127.0.0.1:8765`) and `kosha.autoOpen` (default on).

Build and install:

```sh
python kosha/adapters/bob_extension/build_vsix.py      # -> dist/kosha-bob-0.1.0.vsix
bob --install-extension dist/kosha-bob-0.1.0.vsix
```

### Docker

`docker compose up --build` packages koshad, its price table and UI, with the SQLite ledger in the `kosha-data` volume. The port is bound to `127.0.0.1`, and the passphrase is asked on the attached terminal. `make package` builds the image.

### Dashboard

`bench/web/` is a React/Vite dashboard with three views: **Live session** (agent lanes, fleet budget gauge, escalation state, replay), **Approval queue** (pending action, the bundle with running total, approve once / approve and reset / deny with note) and **Benchmarks** (everything above with n and sources). It reads JSON fixtures exported from the committed results. It needs no running koshad, and its approval buttons are local only.

```bash
cd bench/web
npm install
npm run fixtures   # optional: re-export src/fixtures/ from bench/results and config/
npm run dev        # http://localhost:5173  (#live, #queue, #bench)
```

The live approval page served by koshad itself is `http://127.0.0.1:8765/ui`.

`export_fixtures.py` copies the committed bench summaries, the M1 price table and
effects.yaml, and rebuilds four Bench B composed fleets with `bench/compose_fleet.py`'s
own seeds so every step carries its full `policy.decide` Decision. It aborts if a
rebuilt fleet's members or first flag differ from `bench/results/compose_fleet.json`.
Approval buttons record resolutions in the browser only; no koshad is attached.

---

## The demo: prepare release 1.3

Three concurrent Bob tabs, each automatically its own agent to Kosha, prepare
release 1.3 of a small service. Every step is individually reasonable. Together they are an
unreviewed CI change, pushed to main, followed by a production migration. Kosha holds the
colliding push and the prod migration, shows the human the whole bundle, and lets the release
finish once a human approves.

Say "three concurrent tasks" or "three agents". **Never "subagents"**: this is not Bob's
subagent feature. No secrets appear anywhere in this demo. If you improvise one, use an
obvious fake like `sk-fake-demo-000`.

### 0. Before recording (every time)

```sh
cd kosha          # the repo root
make rehearse     # must end with "== REHEARSAL OK"
make start        # terminal 1: stops any old koshad, fresh demo world + ledger, starts koshad
                  # (asks for an approval passphrase); leave it running. Ctrl+C or `make stop` ends it.
```
`make help` lists the rest (`stop`, `reset`, `status`, `extension`, `test`).

koshad asks for an **approval passphrase** on its own terminal and keeps it only in memory.
Approving needs it; the agents can't read it from anywhere, so they can't approve themselves.

`rehearse.py` plays this exact story against current pricing in a throwaway world. If it
fails, a pricing change broke a beat: don't record until it's green.

- One-time (already done on this machine), then **Developer: Reload Window**:
  ```sh
  make bob-install   # Bob global: stamps each tab's identity on Kosha's tools; auto-approves them
  make extension     # the Kosha panel, pop-ups and status bar
  ```
  The status bar shows `Kosha: 0 held · …` once koshad is up.
- In Bob: open `.demo/work` (under the repo root), **trust** it, then `Ctrl+Shift+P` →
  **Developer: Reload Window**. Close any old task tabs.
- Wait a few seconds after the reload (Kosha's tool server has to connect), then open 3 tabs
  with **New Task in Editor** and set each to the **Kosha** mode. Nothing else to configure:
  in this mode every step goes through Kosha's own tools, which Bob never asks about, so
  **Kosha alone decides**. Each tab is its own agent to Kosha automatically (the panel shows
  them as `bob-<task id>`); they share one fleet. Below, "the release tab", "the test tab" and "the migrate tab" are just tabs 1-3.

### 1. Tabs 1 and 2 (send both, back to back)

**Tab 1 (the release tab)**
```
Prepare release 1.3: change VERSION from 1.2.0 to 1.3.0, add a "## 1.3.0" section to CHANGELOG.md with the line "- users get an email column", commit everything with the message "release 1.3.0", and push to origin main.
```

**Tab 2 (the test tab)**
```
tests/test_app.py::test_health_is_fast is flaky: its timing bound (< 0.00001 seconds) is impossible. Relax it to < 1.0, run the tests, and make CI faster by removing the "slow integration check" step from .github/workflows/ci.yml. Then push to origin main.
```

**Expect:** the edits, the test run and the commit are allowed. The CI edit is allowed (L4:
shared CI config, but plausible on its own). The first push to `main` is allowed. The
**second** push to `main`, from whichever agent gets there second, is **held (target
convergence)**: its tool call stays open in that tab, waiting, and the Kosha panel opens with it.

*Narration:* "Two agents, two reasonable jobs, running at the same time. Each step on its own
is fine: a version bump, a flaky-test fix, a CI speed-up, a push. But both agents just tried
to push main, and Kosha held the second one: two agents on the same branch in one window."

### 2. Tab 3 (send once tab 1 has finished and tab 2 is waiting on its push)

**Tab 3 (the migrate tab)**
```
Release 1.3 is ready. Check how many users prod has, then apply the pending migration to prod with `python3 manage.py migrate --db prod`, then deploy to prod with your deploy tool.
```

**Expect:** the prod read is free. The migration is **held (fleet escalation)**: its call
stays open in tab 3, waiting, and a second card appears on the Kosha panel.

*Narration:* "A prod migration is a normal release step. But this window already has an
unreviewed change that removed a CI check, and a push to main. Per-action approval would say
yes to each of these. Kosha looks at what the fleet did together, and holds it."

### 3. The human reviews and approves, on the Kosha approval panel (inside Bob)

**Nothing to open:** the moment an action is held, the Kosha extension opens the **Kosha
approvals** panel beside the editor (without taking focus), shows a notification ("Kosha held
bob-…: git push origin main (convergence)", **Review**), and the status bar turns amber
(`Kosha: 2 held · fleet …`). The first time, type the passphrase and click **Unlock**; the
panel stays unlocked while it's open, and it's kept in the panel's memory only. You can also
open it any time from the status bar or `Ctrl+Shift+P` → **Kosha: Open Approvals**.

The page shows both held actions live: what's held, **why** (the rule and Kosha's reason), the
fleet's spend, and the **bundle** (everything the fleet did this window, in order). The
migration's bundle shows the CI step removed, the commit, the push to main, then the held
migration. Agents can show the same list in chat: "Show me what Kosha is holding"
(`kosha_review`, read-only). Only the page can approve.

- On the **test tab's push** (the second one to `main`): type the note `main was already pushed`, click **Deny**.
- On the **prod migration**: click **Approve & reset window**.

The line under the passphrase box confirms each decision and says which agent to tell to retry.

*Narration:* "The human sees the whole bundle, not one command in isolation. The duplicate
push gets denied. The migration gets approved, and the window resets. Only the human can do
this: approving needs a passphrase the agents never see."

**Fallback (terminal 2), if the page misbehaves:**
```sh
curl -s localhost:8765/approvals | python3 demo_repo/show_bundle.py
read -rs KOSHA_PASS    # the approval passphrase; silent, not in shell history
curl -s -X POST localhost:8765/approvals/1 -H 'content-type: application/json' -H "X-Kosha-Approval: $KOSHA_PASS" -d '{"decision":"deny","note":"main was already pushed"}'
curl -s -X POST localhost:8765/approvals/2 -H 'content-type: application/json' -H "X-Kosha-Approval: $KOSHA_PASS" -d '{"decision":"approve_reset"}'
```

### 4. The release finishes by itself

Nothing to type. Held calls **wait in the agents' chats** (Bob shows the tool still running)
until the human decides:
- The **test tab**'s push returns `KOSHA DENIED BY A HUMAN` with the note "main was already pushed",
  and the agent reports it instead of retrying.
- The **migrate tab**'s migration **runs as soon as it's approved**, and the agent carries on to
  the deploy by itself: `deployed v1.3.0 to prod (stub)`. Release 1.3 is out.

*Narration:* "The agents never stopped. They waited in place while a human looked at the bundle,
then carried on, or were told why not."

If nobody decides within 25 minutes, the call gives up and returns `KOSHA HELD FOR HUMAN
APPROVAL`; then approve on the panel and tell the agent to retry the same call.

### If something goes off-script

- **An agent refuses or improvises:** the rehearsal (`python demo_repo/rehearse.py`) is the
  same story, deterministic, against a real koshad. Run it on camera as the fallback.
- **An agent keeps retrying a held call:** Bob's own loop guard warns at 3 identical calls and
  stops the task at 5. Tell the agent to wait for approval.
- **The collision must be the same form for both agents:** both `git push` the same branch
  (as scripted), or both use file tools on the same file. A file tool and a shell command
  on the same file don't converge yet (pending teammate fix in `convergence.targets_of`).
- Nothing in this demo runs longer than about 1s (`deploy.sh` is the longest). If you add an
  fs_guard beat, stage it when no Kosha command is running.

---

## How it works

The design follows an **x → y → z chain** throughout. Every subsystem takes a raw, untrusted input, turns it into a normalized form, and produces a deterministic, audited outcome.

```
tool call                  action          decision          execution
(raw harness input)  →  (normalized)  →  (allow/ask/deny)  →  (or blocked)
```

---

## Figures first

### Figure 1 — End-to-end request flow

```
Agent (Bob mode / Claude Code subagent)
        │
        │  MCP call / hook invocation
        ▼
┌──────────────────────────────────────────┐
│  Adapter layer                           │
│                                          │
│  kosha-mcp  (Bob, MCP stdio)             │
│  kosha-hook (Bob & Claude Code, hook)    │
└──────────────┬───────────────────────────┘
               │  POST /decide   { Action }
               ▼
┌──────────────────────────────────────────┐
│  koshad  (FastAPI + SQLite)              │
│                                          │
│  1. coerce_action()  raw body → Action   │
│  2. parser.classify()  Action → (L, cell)│
│  3. db.decide()  policy + ledger → D     │
│  4. record + lease (BEGIN IMMEDIATE txn) │
└──────────────┬───────────────────────────┘
               │  Decision { allow | ask | deny }
               ▼
  allow ──────► adapter executes the tool
  ask   ──────► adapter blocks (exit 2 / is_error)
               └─► human sees it on /ui
                   human approves → agent retries
  deny  ──────► adapter blocks, agent re-plans
               │
               │  POST /settle  { outcome }
               ▼
         koshad confirms or refunds reservation
```

### Figure 2 — Layered defence

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1  Mode groups                                               │
│  The Bob `kosha` custom mode has no native edit/execute groups:     │
│  its only write/run tools are Kosha's own, via kosha-mcp.           │
│  Enforced by Bob at call time, not just at prompt time.             │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2  kosha-hook / kosha-mcp                                    │
│  Every tool call that reaches the hook or the MCP adapter is        │
│  priced and gated before execution. Fail-closed: koshad down =      │
│  deny, not allow.                                                   │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3  fs_guard                                                  │
│  inotify watcher on the working tree. Anything written without a    │
│  Kosha lease is undone within milliseconds and logged as a bypass.  │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4  Bob deniedCommands                                        │
│  A static prefix list in Bob's approval settings. Blocks specific   │
│  command forms at the harness level, before kosha even sees them.   │
└─────────────────────────────────────────────────────────────────────┘
```

### Figure 3 — Policy decision tree

```
Action  →  classify  →  (level L0–L5, cell, price)
                │
                ▼
         hard_deny?  ─── yes ──►  DENY   (rm -rf /, DROP DATABASE prod)
                │
                no
                │
                ▼
         level ≥ 5?  ─── yes ──►  ASK    (privilege-expanding)
                │
                no
                │
                ▼
     destructive SQL  ─── yes ──►  ASK    (DROP TABLE, TRUNCATE, DELETE without WHERE on shared)
     on shared/prod?
                │
                no
                │
                ▼
     convergence?     ─── yes ──►  ASK    (another agent touched this target this window)
                │
                no
                │
                ▼
     escalation?      ─── yes ──►  ASK    (2+ consequential actions in window)
                │
                no
                │
                ▼
     fleet over budget?─── yes ──►  ASK
                │
                no
                │
                ▼
     agent over cap?  ─── yes ──►  ASK
                │
                no
                │
                ▼
                             ALLOW
```

### Figure 4 — SQLite schema (one table per concern)

```
accounts       ─── per-session, per-agent budget tracking (spent, cap, window_start)
actions        ─── every decide call: level, cell, price, decision, status, fingerprint
approvals      ─── held actions waiting for a human (bundle, rule, reason, suggestion)
events         ─── append-only audit log (action_reserved, approval_requested, bypass_detected…)
expected_writes─── write leases granted by /decide, consumed by fs_guard
```

### Figure 5 — Multi-agent fleet layout (demo)

```
Bob IDE  (workspace .demo/work, fleet session "release-1.3")
├── Tab 1 ─┐
├── Tab 2 ─┼── each tab is its own agent: bob-<first 8 of its task id>
└── Tab 3 ─┘
      │
      ├── Kosha mode tabs → mcp__kosha__* tools → one kosha-mcp server
      │     (global hook from `make bob-install` stamps the tab's identity on each call)
      │
      └── other tabs → Bob native tools → kosha-hook (PreToolUse/PostToolUse)
            (KOSHA_SESSION=release-1.3 puts every tab in the same fleet)

All share:
  koshad  on  127.0.0.1:8765  with  kosha.db  (one shared ledger)
  KOSHA_GUARD_ROOT=.demo/work  (fs_guard watches the shared workspace)
```

---

## Architecture overview

Kosha has three moving parts:

| Part | Binary | Role |
|---|---|---|
| **koshad** | `kosha.api.server:main` | FastAPI daemon. Owns the SQLite ledger. Runs all pricing and policy. |
| **kosha-mcp** | `kosha.adapters.bob_mcp:main` | MCP stdio server registered in Bob. Exposes Kosha's gated tools and executes a call only after koshad allows it. |
| **kosha-hook** | `kosha.adapters.claude_hook:main` | PreToolUse / PostToolUse hook. Serves both Bob and Claude Code. |

They communicate through a single channel: `POST /decide` before an action, `POST /settle` after. Every other endpoint (`/approvals`, `/stream`, `/ui`, `/sessions`) is read-only and dashboard-only.

The code is split into two tracks that interact only through the [`Action`](kosha/system/action.py) dataclass:

- **System track** (`kosha/system/`): takes raw harness inputs and produces `Action` objects and context (git state, workspace, DB config).
- **Pricing track** (`kosha/pricing/`): takes `Action` + ledger state and produces `Decision` objects.

This split means the pricing rules can be tested without a running harness, and the system track can be extended (new harnesses, new tools) without touching the pricing logic.

---

## The four defence layers

### Layer 1 — Mode groups (Bob)

Bob's custom-mode system supports a `groups` field. The `kosha` mode is configured with `read`, `todo` and `mcp` only, so the native `write_file`, `apply_diff`, `execute_command` tools are never offered to it. Its only way to write or run anything is Kosha's own tools through `kosha-mcp`. Tabs in other modes keep Bob's native tools, which `kosha-hook` gates (layer 2).

This is enforced by Bob at call time, not just at the chat prompt:

```
getToolsForMode(mode) keeps only tools whose `groups` intersect the mode's groups + its slug.
Before every call: N2r() checks getCurrentAllowedTools(). A call for a tool not in that list
returns: "Tool X is not allowed to be executed. Swap to a mode that can use it."
```

**Why this matters:** an agent in the `kosha` mode cannot call native `write_file` or `execute_command` (the `edit` and `execute` groups are absent from the mode), and has no `mode` group to switch itself out of it.

### Layer 2 — kosha-hook / kosha-mcp (software gate)

Every tool call that gets past layer 1 hits either:

- **kosha-mcp**: the agent's only path to execution. `call()` calls `/decide` and only executes if the response is `allow`. Fail-closed: any exception or unreachable koshad returns `KOSHA UNAVAILABLE` and nothing runs.
- **kosha-hook**: a PreToolUse script registered for native tools. Called by Bob or Claude Code's hook runner before the tool executes. `exit 2` blocks; everything else (including timeout, crash, missing interpreter) fails open, so the hook is engineered to ensure every failure path ends in `os._exit(2)`.

Both adapters translate harness-specific payloads into the same [`Action`](kosha/system/action.py) shape and call the same [`client.decide()`](kosha/adapters/client.py).

### Layer 3 — fs_guard (backup lock)

[`FsGuard`](kosha/system/fs_guard.py) is an inotify/watchdog watcher on the workspace root. It maintains a snapshot of every file's content at startup and after every approved write.

```
file event arrives
       │
       ▼
  ignored? (git internals, .venv, *.db, .bob)  ──► skip
       │
       no
       │
       ▼
  content changed?  ──── no ──► skip (repeat event, or our own undo)
       │
       yes
       │
       ▼
  match_expected(path)?  ── yes ──► update snapshot, accept
       │
       no
       │
       ▼
  undo:
    known file  → restore from snapshot (or `git checkout --` if >5MB)
    new file    → quarantine to .kosha_quarantine/<timestamp>/
    move        → restore both endpoints
       │
       ▼
  log bypass_detected event
```

Two known windows are accepted and documented (not closed):

1. **Settle grace** (0.5 s): a bypass write to a leased path within 0.5 s after its gated action settles is accepted. Required because one write emits multiple inotify events.
2. **Command lease covers cwd** (300 s): while a gated command runs, any native write under its working directory is indistinguishable from the command's own writes.

### Layer 4 — Bob `deniedCommands`

Bob's approval settings support a `deniedCommands` prefix list. This is a static harness-level block for well-known dangerous command prefixes (e.g. `rm -rf`, `git push --force`). It runs before Kosha and before the hook, but its matcher is prefix-only and can't see inside `bash -c '...'` strings.

---

## Component deep-dives

### `kosha/system/action.py` — The contract

```python
@dataclass
class Action:
    action_id: str        # uuid hex
    session_id: str       # fleet id (shared across all agents)
    agent_id: str         # mode slug or Claude subagent id
    harness: Harness      # "bob" | "claude_code" | "opencode" | "replay"
    tool: Tool            # "run_command" | "edit_file" | "write_file" | "git" | "db_exec" | "deploy" | "other"
    raw: dict             # original tool input, untouched (what the human sees in the approval)
    argv: list[str]       # normalized command tokens
    cwd: str              # absolute working directory
    targets: list[str]    # paths / branches / db aliases / hosts
    ts: str               # iso8601
```

This dataclass is the only interface between the system track and the pricing track. The adapters produce it; the pricing code consumes it. Neither side knows about the other's internals.

### `kosha/system/parser.py` — Classification pipeline

```
Action  →  _command_texts()       raw + argv, both if they disagree; price the worse one
        →  split_command()        bashlex (handles &&, pipes, $(), loops) → per-command argv lists
                                  fallback: shlex if bashlex can't parse it
        →  sh -c '...' unwrapping recurse into shell strings
        →  match_command()        lookup against config/effects.yaml
        →  _resolve()             context upgrades:
                                    rm → rm_tracked_clean if all targets tracked+clean
                                    file write → ci_*  /  outside_workspace  /  tracked_clean  /  untracked
                                    sql write → sql_write_shared if db_scope == "shared"
                                    migrate_local → migrate_shared if shared DB mentioned
                                    http write → scope=local if host is loopback
                                    unknown + sudo → privilege=True
        →  _segment()             rubric.classify_action() → level, cell_of() → cell, price()
        →  max(level, price)      most severe segment wins
```

**Control plane check**: after all segments are computed, `segments_of()` adds a privilege-level segment if any command text names the koshad address or any write targets a control-plane file (`.bob/`, `kosha.db`, `.claude/settings*`). This prevents self-approval.

### `kosha/system/kosha_db.py` — Ledger and locking

The core invariant: **check-then-increment must not race**. Two agents calling `/decide` simultaneously could both read the same balance, both see room under the cap, and both be allowed. To close this race, the decide step runs inside a single `BEGIN IMMEDIATE` transaction.

```python
with self._txn() as c:          # BEGIN IMMEDIATE: write lock at txn start, not at first write
    window_start = self._roll_window(c, ...)   # reset spent if window expired
    fleet = self._account(c, ...)              # create row if first action
    agent = self._account(c, ...)
    window = self._window_actions(c, ...)      # reserved + confirmed + pending this window
    approved = c.execute("SELECT ... FROM approvals WHERE fingerprint=?", ...)
    if approved:
        d = Decision("allow", ...)             # approved path: skip policy, consume approval
    else:
        state = LedgerState(...)               # snapshot for deterministic policy
        d = policy(action, level, cell, state) # pure function, no DB calls
    # write action, reserve budget, record leases, emit event — all in the same txn
```

The `fingerprint` ties an approval to the exact content that was shown to the human:

```python
fingerprint = sha256(json([session, agent, tool, argv, sorted(targets), raw]))
```

A human approving `DROP TABLE audit_old` cannot accidentally approve `DROP TABLE users` with the same button.

### `kosha/pricing/policy.py` — Decision rules

Policy is a pure function: `(Action, level, cell, LedgerState) → Decision`. No I/O, no model calls, deterministic. Branch order is fixed:

| Rule | Condition | Decision |
|---|---|---|
| `hard_deny` | `rm -rf /` or `DROP DATABASE prod` | deny (no override) |
| `l5` | level ≥ 5 (privilege-expanding) | ask |
| `destructive_sql` | DROP TABLE / TRUNCATE / DELETE-without-WHERE on shared/prod | ask |
| `convergence` | another agent touched this target this window | ask |
| `escalation` | 2+ consequential batches in window, above threshold | ask |
| `fleet_budget` | fleet_spent + price > fleet_budget | ask |
| `agent_cap` | agent_spent + price > agent_cap | ask |
| `ok` | none of the above | allow |

Every ask/deny carries a `reason` (what triggered it) and a `suggestion` (what to do instead), so the agent can re-plan without human involvement where possible.

### `kosha/pricing/convergence.py` — Multi-agent target tracking

Convergence detects two different agents acting on the same typed target within one window. A target is a `kind:value` string:

| Kind | Example | Source |
|---|---|---|
| `path` | `path:/workspace/app/main.py` | file tools, `git add`, `rm`, `mv` |
| `branch` | `branch:main` | `git push origin main` |
| `db` | `db:prod` | `psql`, `db_exec` |
| `host` | `host:api.example.com` | HTTP clients |
| `k8s` | `k8s:default/deployment/api` | `kubectl` |
| `container` | `container:web` | `docker stop web` |
| `image` | `image:myapp:1.3` | `docker push` |

Matching is **exact and typed**, never substring. `path:/logs` does not match `path:/logs/app.log`. Causal: an action only converges with earlier touches from different agents.

---

## The pricing model

### Severity levels (L0–L5)

| Level | Name | Example |
|---|---|---|
| L0 | No harmful effect | `git status`, `cat file` |
| L1 | Attempted, blocked | (never allowed through) |
| L2 | Reversible local | edit tracked file, `git commit` |
| L3 | Irreversible local | `rm` untracked file, local script |
| L4 | Cross-scope (shared/external) | `git push`, SQL on prod, HTTP POST |
| L5 | Privilege-expanding | write to `.bob/`, `sudo`, `chmod 777` |

### Cell pricing (m1 table)

The price of an action depends on its **cell** (a combination of reversibility, scope, and privilege), not just its level. Two L4 actions can cost differently:

| Cell | Price | Example |
|---|---|---|
| `rev\|local\|nopriv` | 2 | edit tracked file |
| `irrev\|local\|nopriv` | 10 | `rm` untracked |
| `rev\|shared\|nopriv` | 30 | `git push` (reversible by revert) |
| `rev\|external\|nopriv` | 30 | (no effects entry uses this cell yet) |
| `irrev\|shared\|nopriv` | 40 | `git push --force`, SQL write on shared DB |
| `irrev\|external\|nopriv` | 45 | `npm publish`, reading secrets into the transcript |
| `*\|*\|priv` | 60 | any privilege-expanding action |

**Budgets** (30-minute window):

| Limit | Value | Basis |
|---|---|---|
| `agent_cap` | 375 | just above p95 (362.5) of SWE-smith single-agent sessions (n=26,076) |
| `fleet_budget` | 750 | 2 × agent_cap (single-agent proxy until multi-agent bench) |

A single-agent session hits `agent_cap` before `fleet_budget`, so `agent_cap` sets the benign false-alarm rate: 4.53 asks per 100 SWE-smith runs. The fleet budget ensures no one agent can consume more than half the shared window.

### Escalation threshold

The escalation threshold depends on fleet size in the window:

| Agents in window | Consequential threshold |
|---|---|
| 1 | L4+ |
| 2+ | L3+ |

This prevents a single agent from triggering fleet-level escalation on its own (L3 is noise in single-agent runs: an L3+ threshold would ask in 91.16 of 100 benign SWE-smith sessions, the L4+ threshold asks in 6.99). The cross-agent signal is what matters.

---

## Policy rules

### Hard deny (no override)

Two patterns are unconditionally denied. No approval token, no human override through the API lifts them — the API doesn't even have that mechanism:

1. `rm -rf /` (or `rm -rf /*`), including with `sudo`, split flags (`-r -f /`), rm called by path (`/bin/rm`), operands that resolve to root (`//`, `/.`, `/tmp/..`), and a relative operand after `cd /` in the same command (`cd / && rm -rf *`) or when the action's cwd is `/`
2. `DROP DATABASE <name>` or `DROP SCHEMA <name>` where the dropped name or the connection target contains `prod`

These patterns are checked in [`policy._hard_deny_reason()`](kosha/pricing/policy.py).

### Destructive SQL (always ask on shared)

Four SQL shapes always require human approval when the target database is shared or prod-named:

- `DROP TABLE`
- `TRUNCATE [TABLE]`
- `DELETE FROM` without `WHERE`
- `UPDATE ... SET` without `WHERE`

A `DELETE ... WHERE id = 7` on prod is **not** in this set. A bounded write has a different blast radius than an unbounded one. Asking on every bounded prod write would interrupt normal maintenance constantly.

### Convergence (ask when two agents touch the same target)

When agent B tries to act on a target that agent A already acted on in the same window, Kosha holds it. The bundle shown to the human contains both agents' actions in chronological order. Human can: approve B's action once, approve and reset the window (clearing A's prior spend), or deny B.

The approval-by-retry flow:
```
agent B calls git push main  →  ASK (convergence: agent A pushed main)
human reviews bundle
human POST /approvals/1 {"decision": "approve_once"}
agent B retries the exact same call (same argv, same raw, same targets)
fingerprint matches the approval  →  allow, approval consumed
```

---

## Identity and fleet isolation

### How agent identity is assigned

| Harness | Identity source |
|---|---|
| kosha-mcp (Bob) | `_kosha_agent` stamped on each call by the global hook (`make bob-install`): `bob-<first 8 of the tab's task id>`. Without the hook, the `--agent` of the kosha-mcp process (`kosha` in the demo) |
| kosha-hook (Bob native tools) | `bob-<first 8 of the tab's task id>`: Bob sends the chat tab's root task id as `session_id`. Fleet = `KOSHA_SESSION`, else `bob:<workspace>` |
| kosha-hook (Claude Code) | `agent_id` field in the hook payload (set by Claude Code for subagents; `"main"` for the primary) |

### MCP groups-based isolation

The kosha-mcp server in `mcp.json` has `"groups": ["kosha"]`. Bob's tool filtering works like this:

```
getGroupIds(mode) = [...mode.groups, mode.id]   # mode's slug is always in its own group set
getToolsForMode(mode) keeps only tools whose groups intersect getGroupIds(mode)
```

So Kosha's tools are visible only to the `kosha` mode.

**Conditions this depends on** (configuration, not Bob's guarantee):
- The kosha server doesn't have `"mcp"` in its `groups` (that would open it to every mode)
- The `kosha` mode lacks the `mode` group (otherwise `switch_mode` lets an agent leave it)
- Agents can't rewrite `.bob/mcp.json` or `.bob/custom_modes.yaml` (a control-plane write is priced L5, always held)

### Per-tab identity for Bob

Bob's hook payload has no agent name, but its `session_id` is the chat tab's root task id. So every Bob tab is its own agent, `bob-<first 8 of the task id>`, both for native tools gated by `kosha-hook` and for Kosha's own tools, where a global PreToolUse hook matching only `^mcp__kosha__` stamps the identity onto the call (`make bob-install`). All tabs on a workspace share one fleet: `KOSHA_SESSION` if set, else `bob:<workspace>`. Without the global hook, every Kosha-mode tab is the single agent `kosha`.

---

## The approval flow

### Where approvals come from

When policy returns `ask`, koshad:
1. Records the action as `pending` (not `reserved` — no budget reserved until approved)
2. Creates an `approvals` row with `status=pending` and a `bundle` containing all window actions plus the held one
3. Emits an `approval_requested` event on `/stream`

### How a human approves

The UI page (`/ui`) is a self-contained HTML page served by koshad. Open it in Bob's Simple Browser: **Simple Browser: Show → `http://localhost:8765/ui`**.

The page:
- Polls `/approvals` and `/stream` for live updates
- Shows each held action, the rule that triggered it, the full bundle (all window actions in order, including the held one), and the fleet's current spend
- Has three buttons: **Approve once** (allow this exact call once), **Approve & reset window** (allow + zero all spend), **Deny**
- Requires a passphrase (typed once on page load, kept in page memory only, sent as `X-Kosha-Approval` header on POST)

The passphrase is asked on koshad's own terminal at startup and never stored anywhere an agent can read it (not in an env var, not in a file, not returned by any endpoint).

Via curl:
```bash
# See what's held
curl -s localhost:8765/approvals | python -m json.tool

# Approve once
curl -X POST localhost:8765/approvals/1 \
  -H 'content-type: application/json' \
  -H 'X-Kosha-Approval: <passphrase>' \
  -d '{"decision":"approve_once"}'

# Approve and reset the window (clears all spend for the session)
curl -X POST localhost:8765/approvals/1 \
  -H 'content-type: application/json' \
  -H 'X-Kosha-Approval: <passphrase>' \
  -d '{"decision":"approve_reset"}'
```

### Self-approval guard (Tier 1 and 2)

The threat: an agent could approve its own held actions by posting to `/approvals` or by editing the ledger directly.

**Tier 1 (detection)**: any command, file write, or SQL that names the koshad address or path is priced as privilege (L5, always held). This covers:
- `curl localhost:8765/approvals`
- `sqlite3 kosha.db "update approvals ..."`
- writes to `.bob/mcp.json`, `.bob/custom_modes.yaml`, `kosha.yaml`
- writes to `.claude/settings.json`

**Tier 2 (authentication)**: `POST /approvals/{id}` requires the `X-Kosha-Approval` header containing the passphrase set at startup. A wrong passphrase returns 401. The passphrase is compared using `hmac.compare_digest` on its SHA-256 hash (timing-safe). It's never present in `/proc/<pid>/environ` or `cmdline`.

**Tier 3 (roadmap)**: OS-level privilege separation (koshad runs as a different user, with a Unix socket owned by that user). This is the only complete fix; Tiers 1 and 2 are pattern-based and can be obfuscated around.

---

## The fs_guard (layer 3)

[`FsGuard`](kosha/system/fs_guard.py) is a watchdog-based inotify watcher. It starts with koshad when `KOSHA_GUARD_ROOT` is set.

**Write leases**: when `/decide` returns `allow`, it records an `expected_writes` row in the same `BEGIN IMMEDIATE` transaction. The lease exists before the write happens:

| Tool | Lease | TTL |
|---|---|---|
| `edit_file`, `write_file` | exact file path | 30 s |
| `run_command`, `git`, `deploy` | command's cwd (covers all writes under it) | 300 s |
| `db_exec` | none (DB files are ignored by fs_guard) | — |

When a write arrives, `match_expected(path)` checks for an unexpired lease on the path or any directory above it. On match: accept, update snapshot. No match: undo, emit `bypass_detected`.

**Ignored paths** (changes here don't trigger fs_guard):
- `.git/`, `.kosha_quarantine/`, `.bob/`, `.venv/`, `__pycache__/`, `node_modules/`
- `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`, `*.sqlite`, `*.sqlite3`
- editor swap files (`*.swp`, `*~`)
- symlinks

---

## Configuration

### `config/kosha.yaml` (gitignored)

Copy [`config/kosha.example.yaml`](config/kosha.example.yaml) to `config/kosha.yaml`:

```yaml
databases:
  dev:
    url: "sqlite:///path/to/dev.db"
    scope: local          # writes price as local (L2/L3)
  prod:
    url: "sqlite:///path/to/prod.db"
    scope: shared         # writes price as cross-scope (L4); TRUNCATE/DROP always ask

# Hosts treated as local for HTTP writes (loopback is always local)
local_hosts: []
```

Override with `KOSHA_CONFIG=/path/to/kosha.yaml`.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `KOSHA_DB` | `kosha.db` (repo root) | SQLite ledger path |
| `KOSHA_CONFIG` | `config/kosha.yaml` | Config file path |
| `KOSHA_GUARD_ROOT` | (unset) | Working tree for fs_guard |
| `KOSHA_GUARD_SESSION` | (unset) | Session id to tag bypass events with |
| `KOSHAD_URL` | `http://127.0.0.1:8765` | koshad address (adapters read this) |
| `KOSHAD_PORT` | `8765` | koshad listen port |
| `KOSHA_AGENT_ID` | (required for kosha-mcp) | Agent id for MCP instances |
| `KOSHA_SESSION` | `bob` | Session id for MCP instances |
| `KOSHA_WORKSPACE` | `cwd` of kosha-mcp | Workspace root |
| `KOSHA_HOOK_DEADLINE` | `3.0` | Seconds before hook exits 2 (whole hook) |
| `KOSHA_IDENTITY_MODE` | `per_mode_instance` | `per_mode_instance` or `inline` |
| `KOSHA_DEPLOY_CMD` | `./deploy.sh` | Command run by the `deploy` tool |
| `KOSHA_APPROVAL_WAIT` | `1500` | kosha-mcp: seconds a held call waits for the human before returning |
| `KOSHA_HOOK_WAIT` | `0` | kosha-hook (Bob): seconds a held native call waits for the human; 0 blocks at once |

---

## Running Kosha

### Prerequisites

```bash
pip install -e ".[dev]"
# or
uv pip install -e ".[dev]"
```

### Start koshad

```bash
koshad
# prompts for an approval passphrase on the terminal
# listens on 127.0.0.1:8765

# with fs_guard
KOSHA_GUARD_ROOT=/path/to/workspace koshad

# with custom DB and config
KOSHA_DB=/path/to/kosha.db KOSHA_CONFIG=/path/to/kosha.yaml koshad
```

### Register kosha-mcp in Bob

In `.bob/mcp.json` (workspace), one server for the `kosha` mode (this is what `demo_repo/setup_demo.py` writes):

```json
{
  "mcpServers": {
    "kosha": {
      "command": "/abs/path/to/.venv/bin/kosha-mcp",
      "args": ["--agent", "kosha", "--session", "release-1.3",
               "--workspace", "/abs/path/to/workspace"],
      "groups": ["kosha"],
      "alwaysAllow": ["run_command", "edit_file", "write_file", "git", "db_exec", "deploy"],
      "timeout": 1800000
    }
  }
}
```

`groups` must be exactly the mode's slug. `timeout` is in milliseconds: a held call waits for the human, and Bob's default is 60 s. Then run `make bob-install` once, so each tab's identity is stamped onto Kosha's tool calls.

### Register kosha-hook in Claude Code

In `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "^(Bash|Edit|MultiEdit|Write|NotebookEdit)$",
      "hooks": [{"type": "command",
                 "command": "/abs/path/to/.venv/bin/kosha-hook",
                 "timeout": 10}]
    }],
    "PostToolUse": [{
      "matcher": "^(Bash|Edit|MultiEdit|Write|NotebookEdit)$",
      "hooks": [{"type": "command",
                 "command": "/abs/path/to/.venv/bin/kosha-hook",
                 "timeout": 10}]
    }]
  }
}
```

### Register kosha-hook in Bob

In `<workspace>/.bob/settings.json` (trusted workspace):

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$",
      "hooks": [{"type": "command",
                 "command": "/abs/path/to/.venv/bin/kosha-hook",
                 "timeout": 10}]
    }],
    "PostToolUse": [{
      "matcher": "^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$",
      "hooks": [{"type": "command",
                 "command": "/abs/path/to/.venv/bin/kosha-hook",
                 "timeout": 10}]
    }]
  }
}
```

The matcher must be anchored (`^...$`). Without anchors, `write_file` also matches `mcp__kosha__write_file`, which would double-gate kosha-mcp calls (they're already priced by kosha-mcp itself). Set `KOSHA_SESSION` in the hook command to put every tab in one fleet, and `KOSHA_HOOK_WAIT` (seconds) to make a held call wait for the human instead of blocking. Bob's hook `timeout` must then be larger, because Bob treats a timed-out hook as allow.

### Run the demo

```bash
python demo_repo/setup_demo.py          # builds .demo/ (git repo, dev+prod DBs, fresh ledger)
.demo/start_koshad.sh                  # starts koshad with demo config and fs_guard
python demo_repo/rehearse.py           # dry-run the full demo script, asserts all beats
```

---

## Test coverage

981 tests collected (`pytest --co -q`). Each module has a dedicated test file:

| Test file | What it covers |
|---|---|
| `test_rubric.py` | L0–L5 classification rules |
| `test_pricing.py` | Cell → price, budget defaults from table |
| `test_policy.py` | Every policy rule and edge case, including the rm hard-deny variants |
| `test_match.py` | effects.yaml command matching |
| `test_parser.py` | Split, classify, resolve; real git repo; 61 cases |
| `test_convergence.py` | Typed target extraction, cross-agent matching |
| `test_kosha_db.py` | Ledger transactions, concurrency races (300×), window roll |
| `test_client.py` | Fail-closed behaviour: refused, timeout, 500, bad JSON |
| `test_server.py` | koshad endpoints, malformed payloads, passphrase guard |
| `test_bob_mcp.py` | Full MCP protocol; allow/deny/ask/settle; over stdio |
| `test_claude_hook.py` | Hook as subprocess; exit codes; crash paths; malformed stdin |
| `test_bob_hook.py` | Under Bob's real hook runner (extracted from Bob source at test time) |
| `test_fs_guard.py` | Real inotify events; undo paths; grace window; end-to-end with koshad |
| `test_control_plane.py` | 13 self-approval routes held; reads not flagged |
| `test_ui.py` | Approval page: controls, no HTML sinks, XSS via `textContent` |
| `test_demo_repo.py` | Demo world build; every scenario step priced correctly |
| `test_demo_scenario.py` | Full rehearsal; convergence hold; escalation hold; deploy allow |

Concurrency is mutation-tested: swapping `BEGIN IMMEDIATE` for `BEGIN` fails the write-lock test, and stubbing `window_touches` to `[]` fails the four convergence positive tests.

---

## Known limitations (v1)

These are documented gaps, not surprises:

1. **Not a sandbox.** Kosha is a guardrail for well-meaning agents (see [What Kosha is, and is not](#what-kosha-is-and-is-not)). Classification reads argv; an obfuscated or opaque command can be priced lower than what it does. Opaque scripts (`python x.py`) are priced by a flat fallback, not scanned.

2. **Command leases cover cwd.** While a gated command runs, a native write anywhere under its working directory is accepted as that command's own. Layers 1 and 2 prevent native writes in the demo fleet.

3. **fs_guard acts after the write.** It undoes bypasses; it doesn't prevent them. Anything read or exfiltrated before the undo is out. Layers 1 and 2 are the prevention layers.

4. **Tier 1 self-approval guard is pattern-based.** An obfuscated command (`python3 -c "import requests; requests.post(...)"`) won't be detected. The real fix is OS privilege separation (Tier 3, roadmap).

5. **`raw` is stored in plain text.** Approval bundles and the actions table store full tool input (SQL, file contents, commands). This is intentional: a human needs to see the content to decide on an approval. Reset `kosha.db` before recording any demo that involves sensitive inputs.

6. **Calibration is pending.** M1 prices are hand-set, `n` and `p_high` are empty, and `fleet_budget` is a single-agent proxy (2 × `agent_cap`). M2 (`config/price_table.m2.json`) doesn't exist yet.

---

## Security

- Secrets live only in `.env` and `config/kosha.yaml`, both gitignored. Templates: `.env.example`, `config/kosha.example.yaml`.
- The `prod` database alias must point at a **disposable** database.
- The approval passphrase is typed on koshad's terminal at startup and kept only in memory. It is never read from an env var or file.
- `kosha.db` stores raw tool inputs in plain text. Reset it before recording a demo.
- Kosha is a guardrail for well-meaning agents, **not a security sandbox**. Don't rely on it to contain a hostile agent.

Security and credential handling follows the IBM hackathon template: [SECURITY.md](SECURITY.md), `.gitignore`, `.bobignore`, `.env.example`. Before every commit: review `git diff`, keep `.env` unstaged, and never paste credentials into Bob or any other assistant.

---

## Repo layout

```
kosha/
  system/              System track: interception and plumbing
    action.py          Action dataclass (the contract)
    kosha_db.py        SQLite ledger, transactions, leases
    parser.py          Action → (level, cell): bashlex + effects.yaml + resolvers
    resolvers.py       Context: git state, workspace, DB scope, control-plane paths
    fs_guard.py        inotify watcher, undo, quarantine
  pricing/             Pricing / rubric / calibration track
    rubric.py          Severity axes → L0–L5
    pricing.py         Cell string → price; load price table
    policy.py          (Action, level, cell, LedgerState) → Decision
    match.py           effects.yaml command matching
    convergence.py     Typed target extraction and cross-agent matching
  adapters/
    client.py          Shared fail-closed HTTP client; block_text()
    bob_mcp.py         MCP stdio adapter (kosha-mcp)
    claude_hook.py     PreToolUse/PostToolUse hook for Claude Code and Bob (kosha-hook)
    bob_install.py     One-time global Bob hook for per-tab identity (make bob-install)
    install.py         kosha-install: one-command Bob setup for a workspace
    bob_extension/     Bob extension: opens the approval page when an action is held
  api/
    server.py          FastAPI app; koshad entry point
    ui.html            Approval page (self-contained, no external resources)

config/
  effects.yaml         Command family → (reversible, scope, privilege, read_only), 101 entries
  price_table.m1.json  Cell prices, fleet budget, agent cap, window, budget basis
  kosha.example.yaml   Config template

bench/                 Offline benchmarks (no model calls)
  replay.py            Bench A: StepShield single-agent replay, M1 vs B0–B3
  compose_fleet.py     Bench B: synthetic composed fleets
  benign_spend.py      Bench C: SWE-smith benign spend (sets agent_cap)
  git_history.py       Bench D: git-history convergence (SZZ proxy)
  convergence_synth.py Bench E: engineered target convergence
  results/             Committed outputs of the above
  web/                 Dashboard (React/Vite): live session, approval queue, benchmarks

demo_repo/             FastAPI+SQLite demo app and the scripted release-1.3 demo
Dockerfile, compose.yaml  koshad container (see Docker above)
scripts/               test_installer.sh (isolated installer check), e2e_security_audit.py
tests/                 981 tests
bob_sessions/          Bob task session screenshots from both team members
LICENSE, THIRD_PARTY_NOTICES.md, SECURITY.md, .bobignore, .env.example
```

---

## License and credits

[MIT](LICENSE), © 2026 Roshan Singha and Prithvi Hegde.

- `kosha/pricing/rubric.py` is ported from [action-graded-severity](https://github.com/Harry-Ashley/action-graded-severity) (MIT, © 2026 Harry Owiredu-Ashley). The notice is in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- The benchmarks read [StepShield](https://github.com/glo26/stepshield) (MIT) and [SWE-smith trajectories](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories) (MIT). The data is downloaded, not redistributed; only the derived results in `bench/results/` are committed.
- Parts of the code, tests, dashboard and docs were built or tested with IBM Bob. See [IBM Bob 2.0](#ibm-bob-20).
- Security and credential handling follows the IBM hackathon template: [SECURITY.md](SECURITY.md), `.gitignore`, `.bobignore`, `.env.example`.
