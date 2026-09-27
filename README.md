# Kosha

> **Every per-action check said yes. Together they did something nobody would approve.**

Kosha (originally "Ledger") is a runtime gateway that sits beneath an AI coding harness (IBM Bob 2.0, Claude Code, OpenCode). It intercepts every tool call an agent or subagent makes and classifies it on three axes: reversibility, scope and privilege. That gives a severity level from L0 to L5. Kosha prices the action and charges it against a shared budget for the whole session: the main agent plus every subagent, "the fleet". The result is **allow**, **ask** (with a bundle view of everything the fleet has already done this window), or **deny**, always with a reason and what would be allowed instead.

Built for the **IBM Bob 2.0 Hackathon** (lablab.ai, Sep 25–27 2026).

**Hosted dashboard:** TODO, not deployed yet. Run it locally, see [Dashboard](#dashboard).

---

## The problem

Today's gates judge one action at a time. A denylist (`rm -rf`, `git push --force`) or a per-action severity check approves or blocks each tool call on its own. A fleet of agents can collectively do something no human would sign off on as a bundle, even when every individual action looked fine alone. For example, one agent edits a migration, another runs it against the shared database, and a third pushes and deploys.

The choice today is between two bad defaults:

- **Manually approve everything:** safe, slow, and it defeats the point of parallel subagents.
- **Auto-approve everything:** fast and risky.

Kosha lets auto-approve stay on and only interrupts when the **cumulative** risk of the fleet warrants it. It keeps a ledger per session: every action's price, which agent spent it, and which consequential actions are already in the window. When it asks, the human sees the whole bundle, not one command out of context.

> **Kosha is a guardrail for well-meaning agents, not a security sandbox.** It is built to catch agents that are trying to do the right thing and drift into doing too much. It does not defend against an adversary actively trying to evade detection. Command classification reads argv, and an obfuscated command (`python -c "…"`, a script with an innocent name) can be priced lower than what it does. The self-approval guards are pattern-based, and the ledger runs as the same OS user as the agents. Read every number below with this in mind: the benchmarks measure whether the accounting interrupts at the right *amount* of fleet activity, not whether it can tell a malicious fleet from a benign one.

Decisions are deterministic accounting. No model call ever decides allow, ask or deny.

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

Bob is both the harness Kosha gates and a tool the team used to build it.

**Kosha running under Bob:**
- `kosha-mcp` is an MCP server registered in Bob. A `kosha` custom mode gets Kosha's gated tools (`run_command`, `edit_file`, `write_file`, `git`, `db_exec`, `deploy`) and no native edit or execute tools.
- `kosha-hook` runs as Bob's PreToolUse/PostToolUse hook on Bob's native tools. Each Bob chat tab is its own agent (`bob-<task id>`), and all tabs on a workspace share one fleet. Held calls wait for the human.
- A Bob extension (`kosha/adapters/bob_extension/`) opens the approval page inside Bob whenever an action is held, with a status-bar count.
- `make bob-install` adds a global Bob hook that stamps each tab's identity onto Kosha's own tool calls.

**Bob used to build Kosha:**
- An adversarial review task: Bob tried to break the gate and wrote `scripts/e2e_security_audit.py`. It found that `/bin/rm -rf /` (rm called by path) bypassed hard-deny, and fixed it in `kosha/pricing/policy.py`. The follow-up variants (`rm -rf //`, `rm -rf /.`, `cd / && rm -rf *`) are now hard-denied too, with tests in `tests/test_policy.py`.
- The first draft of this README's architecture and figures sections.
- A benchmarks page built with parallel Bob subagents, since replaced by the `bench/web/` dashboard.
- TODO: add the teammate's Bob tasks (for example the Bob adapters) once confirmed from their Bob session history.

**Bob session screenshots:** TODO, `bob_sessions/` is not in the repo yet.
**Demo video:** TODO, not recorded yet.

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

---

## Table of Contents

1. [Figures first](#figures-first)
2. [Architecture overview](#architecture-overview)
3. [The four defence layers](#the-four-defence-layers)
4. [Component deep-dives](#component-deep-dives)
5. [The pricing model](#the-pricing-model)
6. [Policy rules](#policy-rules)
7. [Identity and fleet isolation](#identity-and-fleet-isolation)
8. [The approval flow](#the-approval-flow)
9. [The fs_guard (layer 3)](#the-fs_guard-layer-3)
10. [Configuration](#configuration)
11. [Running Kosha](#running-kosha)
12. [Test coverage](#test-coverage)
13. [Known limitations](#known-limitations-v1)
14. [Repo layout](#repo-layout)

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

1. **Not a sandbox.** Kosha is a guardrail for well-meaning agents (see [The problem](#the-problem)). Classification reads argv; an obfuscated or opaque command can be priced lower than what it does. Opaque scripts (`python x.py`) are priced by a flat fallback, not scanned.

2. **Command leases cover cwd.** While a gated command runs, a native write anywhere under its working directory is accepted as that command's own. Layers 1 and 2 prevent native writes in the demo fleet.

3. **fs_guard acts after the write.** It undoes bypasses; it doesn't prevent them. Anything read or exfiltrated before the undo is out. Layers 1 and 2 are the prevention layers.

4. **Tier 1 self-approval guard is pattern-based.** An obfuscated command (`python3 -c "import requests; requests.post(...)"`) won't be detected. The real fix is OS privilege separation (Tier 3, roadmap).

5. **`raw` is stored in plain text.** Approval bundles and the actions table store full tool input (SQL, file contents, commands). This is intentional: a human needs to see the content to decide on an approval. Reset `kosha.db` before recording any demo that involves sensitive inputs.

6. **Calibration is pending.** M1 prices are hand-set, `n` and `p_high` are empty, and `fleet_budget` is a single-agent proxy (2 × `agent_cap`). M2 (`config/price_table.m2.json`) doesn't exist yet.

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

calib/                 M2 calibration (pending)
demo_repo/             FastAPI+SQLite demo app and the scripted release-1.3 demo
Dockerfile, compose.yaml  koshad container (see Docker above)
scripts/               test_installer.sh (isolated installer check), e2e_security_audit.py
tests/                 981 tests
```
