# System track progress

## 2026-09-26: tasks 1–4 done (checkpoint: stop and report)

**Done and committed (one commit per task):**
1. `kosha/system/action.py`: `Action` dataclass, shape exactly as agreed. Also exports `Tool`/`Harness` literal aliases.
2. `kosha/system/kosha_db.py`: tables `accounts`, `actions`, `approvals`, `events`, `expected_writes`. The whole decide step (roll window → read balances and window actions → `policy.decide` → reserve/queue/deny → event) runs in one `BEGIN IMMEDIATE` transaction. Also has settle (success confirms, failure refunds), approvals (approve_once / approve_reset / deny), a 30-min window roll, and `expected_writes` with a TTL for fs_guard.
3. `kosha/adapters/client.py`: shared fail-closed client (`decide`, `settle`).
4. `kosha/api/server.py`: `koshad` with all 7 endpoints and nothing more. `/decide` calls the **real** `kosha.pricing.policy.decide`. `koshad` console script added to `pyproject.toml`.

**Tested (`tests/test_kosha_db.py`, `test_client.py`, `test_server.py`; full suite 315 passed, the pricing track's 284 still pass):**
- Concurrency: two threads released by a `threading.Barrier`, 300 loops each, for both the per-agent cap race (same agent) and the fleet budget race (two subagents). Spend never exceeds the cap, and exactly one of each pair is allowed. A separate test proves the write lock is held from the start of the transaction. Mutation-checked: swapping `BEGIN IMMEDIATE` for `BEGIN` makes that test fail. The race test alone did *not* catch it, because the first statement in the txn is already a write.
- Fail-closed client: connection refused, timeout, HTTP 500, non-JSON body, and JSON that isn't a Decision all give `deny/fail_closed`. Also checked by hand against a live `koshad` that was then killed.
- Malformed `/decide` payloads (empty argv, missing targets, a tool outside the Literal set, `{}`, wrong types) never 500. `ls && rm -rf /` is hard-denied.

**Classification is a placeholder right now:** `server.classify()` returns L4 / `irrev|shared|nopriv` (conservative unknown) for everything until task 5 (parser) lands.

## Flags / decisions a human should look at

1. ~~**Ownership gap**~~ resolved: the Pricing track pushed `kosha/pricing/match.py`, and task 5 builds on it (see below). Original note: **who maps Action → effects.yaml entry → (level, cell)?** Nothing does yet. `bench/check_effects_coverage.py` has an offline-only matcher, and `rubric.classify_action` needs the axes already resolved. My plan for task 5: `parser.py` does family/sub parsing *and* the effects.yaml match (read-only, no edits to the table), and resolvers fill in context subs (`tracked_clean`, `untracked`, `outside_workspace`, local/shared db). The Pricing track should confirm they're fine with the matcher living on the System side.
2. **Approval → retry semantics (my design choice, spec was silent).** Adapters only call `/decide` and `/settle`, so an approved `ask` is consumed by the agent's *next identical* `/decide` (same session, agent, tool, argv, targets), which is then allowed and charged. It's single-use. `approve_reset` also zeroes the session window.
3. **client.py catches slightly more than the PDF snippet:** it catches `requests.RequestException` plus `ValueError`/`TypeError` (a bad body), not just Timeout/ConnectionError/HTTPError. A 200 with garbage in it would otherwise raise instead of failing closed.
4. **`IDENTITY_MODE` default:** the PDF says `"inline"`, the build prompt says `"per_mode_instance"`. Going with the prompt, since it's the later, source-verified finding.
5. **PDF location:** it's at `docs/Kosha_SD.pdf`, not `kosha/docs/kosha_SD.pdf`. `AGENTS.md`, `docs/` and `ledger-context.md` are untracked and I've left them uncommitted because they're not mine.
6. **Pricing-track observations (not touched):**
   - `policy._drops_prod_db` matches the substring `prod` anywhere, so `DROP DATABASE products` gets hard-denied.
   - The `Decision.rule` comment in `policy.py` doesn't list `fail_closed`.
   - The escalation rule is "any 2 L3+ in the window", deliberately looser than AGENTS.md's "strictly rising, distinct agents". It's documented in policy.py and fine by me, but with M1 prices the third consequential action in any window always asks.
   - The PDF's agent-facing text template (`bundle_clause` naming the other agents' actions) isn't in policy's reason strings yet. The approval `bundle` in the DB does carry that data for the dashboard.
7. `pyproject.toml` (shared) got runtime deps (fastapi, uvicorn, requests, watchdog, bashlex, mcp) and a `[project.scripts]` block, additive only. Dev venv is at `.venv/` (gitignored).
8. `/stream` takes `?last_id=` (resume) and `?once=true` (drain and close, used by the tests).

## 2026-09-26: task 5 done

Rebased onto the Pricing track's `d8d7c1e` (match.py, the single-agent L4 escalation threshold), no conflicts. Human confirmed: `per_mode_instance`, the stricter client, and approval-by-retry.

- `kosha/system/parser.py`: splits the command with `bashlex` (which sees `&&`, pipes, `$(…)`, loop bodies and write redirects) and falls back to match.py's `shlex` splitter when bashlex can't parse it. It unwraps `sh -c '…'`, matches each segment via `kosha.pricing.match.match_command`, upgrades the context-dependent matches through the resolvers, and the most severe segment wins (level, then price). It also handles the `edit_file`/`write_file`/`db_exec`/`deploy`/`git` tools. If the raw command and argv disagree, both are priced and the worse one counts. Any exception falls back to the `unknown` entry.
- `kosha/system/resolvers.py`: `is_tracked_clean`, `in_workspace` (plus `workspace_of`), `host_scope`, `db_scope`, `is_secret_var`. They're conservative when unsure: untracked, outside the workspace, shared, external.
- `/decide` now uses the parser (the placeholder is gone).
- `config/kosha.example.yaml`: committed template with placeholder values only. **Human: copy it to `config/kosha.yaml` and set the `prod` URL to a disposable DB.**
- Tests: `tests/test_parser.py`, 61 tests against a real temp git repo. Full suite: 457 passed.

**Flags:**
- `is_secret_var` is built and tested but nothing calls it yet. match.py's own `SECRET_VAR` regex already covers `echo $TOKEN`.
- The http "resolve local" handling lowers the *scope* on the matched entry (the effects note says localhost resolves local), because there's no `http_post_local` entry. With M1 prices, a localhost POST is L3/10 instead of L4/45.
- `rm` of a path outside the workspace is raised to shared scope (L4). No effects entry exists for that, so it reuses the entry and bumps the scope, mirroring `file_write_outside_workspace`.
- `tee FILE`, `cp`, `mv`, `sed -i` aren't file-write families in match.py, so they price as `unknown` (L4). That's conservative, but a benign `sed -i` on a tracked file will over-charge. It's a Pricing-track call whether to add entries.

## 2026-09-26: Bob hook facts, read from the installed Bob IDE source

Source: `~/.local/opt/bobide/resources/app/extensions/bob-code/dist/extension.js` (IBM Bob 1.126.0+bob2.2.0). Read-only inspection. No credentials were touched; `~/.bob/settings/settings.json` currently holds only a migration flag.

- **The installed `bob` is Bob IDE (a VS Code fork), not a `bob run` shell CLI.** The PDF's `bob run --disable-tool-groups execute,edit` doesn't apply here. Layer 1 has to be done through custom-mode `groups` (modes live in `custom_modes.yaml`, e.g. `groups: ["read","mcp",...]` without `edit`/`execute`).
- **Hook config:** `{"hooks": {"PreToolUse": [{"matcher": "<regex>", "hooks": [{"type": "command", "command": "...", "timeout": <sec>}]}]}}`.
  - Global hooks: the `hooks` key in the user settings (`disableGlobalHooks` switches them off).
  - Workspace hooks: `<workspace>/.bob/settings.json`, and only if the workspace is trusted.
  - Also supports `"type": "http"` (a POST with the same JSON). **Don't use it for kosha:** a failed request is skipped, which means allow.
- **The matcher is a regex tested against `tool_name`.**
  - Native write/exec tools: `execute_command`, `write_file`, `apply_diff`, `insert_content`, `search_and_replace`, `office_edit`.
  - Read-only: `read_file`, `glob`, `grep`, `list_files`.
  - Subagents: `spawn_subagent`.
- **PreToolUse stdin:** `{session_id: rootTaskId, cwd, hook_event_name, tool_name, tool_input, tool_use_id}`. **There's no agent identity in the hook payload either.** `session_id` is the root task, so subagents share it, which works as the fleet id.
- **Exit codes (this answers the PDF's open question):** exit **2** blocks, with stderr or stdout as the reason. **Any other nonzero exit is logged and ignored, i.e. allowed.** A spawn failure or timeout (`exitCode === null`) is also allowed. So every kosha-hook failure path must end in exactly `exit 2`.
- **JSON output:** only `permissionDecision: "deny"` blocks. **`"ask"` is treated the same as `"allow"`**: Bob hooks have no ask. So kosha's `ask` must be sent to Bob as a deny whose reason says a human needs to approve it in kosha.
- **Default hook timeout is 10s** (the `timeout` field is in seconds), and a timeout fails open. kosha-hook's own 2s SIGALRM stays well under it.
- **PostToolUse fires only when the tool did *not* error**, and its payload adds `tool_response`. A failed native tool never settles through the hook, so its reservation stays charged. That's the conservative side, but worth knowing.
- **Default approval block:** `approval.allowedExecutors = [{toolId: "execute_command", approvedCommands: [...read-only prefixes], deniedCommands: []}]`. ~~Denied wins over approved~~ **Corrected in task 8:** the longest token-prefix wins, and **a tie goes to allow** (`ZVt`). It's matched per sub-command.

## 2026-09-26: task 6 done (kosha-mcp)

- `kosha/adapters/bob_mcp.py`, console script `kosha-mcp`: a stdio MCP server on the `mcp` 2.2 SDK's low-level `Server`.
  - Tools: `run_command(command, cwd?)`, `edit_file(path, old, new)`, `write_file(path, content)`, `git(args)`, `db_exec(db, sql)`, `deploy(target)`.
  - Flow: build Action → `client.decide` → execute **only** on allow → `client.settle`.
  - ask/deny return `is_error` with the reason and suggestion. For ask, it also tells the agent to retry the same call once a human approves.
  - Any exception inside the handler becomes an `is_error` result that says "nothing was run". Nothing surfaces as a success.
- **Identity:** `resolve_agent_id()` as specified, defaulting to `per_mode_instance` (`--agent <mode slug>`). One tweak: inline mode falls back to the instance name instead of returning `None`.
  - Pinning each instance to its mode with the MCP entry's `groups` field: see "Verification: groups, MCP timeout, hook fail-open" below. Verified live: a mode is never offered another agent's kosha tools (see "Live group-isolation test"). The conditions it depends on are listed there.
- `db_exec` supports sqlite aliases only (the demo's dev/prod). Relative sqlite paths resolve against the kosha repo root, and it never echoes a DB URL. `deploy` runs `KOSHA_DEPLOY_CMD` (default `./deploy.sh <target>`) in the workspace.
- **Write leases:** on allow, `/decide` now records `expected_writes` in the *same* transaction. File tools lease their exact path for 30s; commands lease their cwd for 300s; `db_exec` leases nothing. Settle shrinks the lease to a 2s grace. The `expected_writes` key became `(path, action_id)`, so concurrent commands in one directory don't clobber each other's lease. `match_expected` treats a directory lease as covering everything under it.
  - **Known gap:** while a gated command runs, a native bypass write under the same cwd is indistinguishable from the command's own and gets accepted. Layers 1 and 2 (mode groups, kosha-hook) still stop it.
- Tests: `tests/test_bob_mcp.py` (19). They run against a real `koshad` (`tests/conftest.py::live_koshad`, uvicorn on a free port), a temp git workspace and sqlite dev/prod DBs:
  - allow/confirm, fail/refund, and the hard-deny prod drop (the prod table survives)
  - ask held, then approved, then retry runs (the chmod only lands after approval)
  - koshad down: canary files are never created
  - over the MCP protocol: two instances record two agent ids; a crash inside the adapter comes back as an error; the real stdio entrypoint runs as a subprocess
  - Full suite: 486 passed.
  - The protocol tests caught a real bug: SDK 2.x passes `_meta` as a dict, and the line reading it sat outside the try/except. It's fixed, with a regression test.

**Human step (needs live Bob, not done by me):** register one instance per custom mode, in `.bob/mcp.json` (workspace) or `~/.bob/settings/mcp.json` (global):
```json
{"mcpServers": {
  "kosha-sub1": {"command": "/abs/path/kosha/.venv/bin/kosha-mcp",
                 "args": ["--agent", "sub1", "--session", "release-1.3", "--workspace", "${workspaceFolder}"],
                 "groups": ["sub1"], "alwaysAllow": ["run_command", "edit_file", "write_file", "git", "db_exec", "deploy"],
                 "timeout": 300000}
}}
```
Repeat for each mode, giving every instance the same `--session`. `groups` must be exactly the mode's **slug** and nothing else (no `"mcp"`). `alwaysAllow` is safe here because kosha makes the decision. `timeout` is in **ms**, and Bob's default is **60s** (corrected below; my earlier "270s" was execute_command's limit, not MCP's). Set 300000 so Bob doesn't abandon a call that kosha-mcp is still running (kosha-mcp's own command limit is 270s). Start `koshad` first. I'll put the demo's real copy of this in `demo_repo/.bob/` in task 10.

## 2026-09-26: Verification: groups, MCP timeout, hook fail-open

**How this was verified, and its limits.** There is no `bob run` on this machine: the install is Bob IDE only (`bob` is the IDE launcher, and the only other binary is `bobide-tunnel`). Running a real two-mode agent session needs the GUI plus an interactive, model-backed session, so **(a) is verified from Bob's source, not live**. (c) is verified by **executing Bob's own hook-runner functions**, extracted verbatim from `bob-code/dist/extension.js` (Bob 1.126.0+bob2.2.0), against 34 real hook scripts under Node v26. Bob itself ships Electron 42. The runner uses `child_process.exec`, whose timeout, kill and maxBuffer behaviour is the same across those versions. No credentials were read; `~/.bob/settings/settings.json` holds only a migration flag.

### (a) Does MCP `groups` restrict which mode can call an instance? Yes. List filtering was verified live (see "Live group-isolation test" below). The execution-time check is source only.

Code path:
- MCP tool: `this.groups = yVt(serverConfig)`, which is `serverConfig.groups` if it's an array, else `["mcp"]`.
- Mode: `getGroupIds(mode)` is `[...mode.groups, mode.id]`, so **the mode's own slug counts as a group**.
- `getToolsForMode(mode)` keeps only tools whose `groups` overlap `getGroupIds(mode)`.
- **Enforced at execution, not just hidden from the prompt.** Before every tool call, `N2r()` does `if (!getCurrentAllowedTools().includes(tool.id)) return {isError: true, "Tool X is not allowed to be executed. Swap to a mode that can use ..."}`.

So `"groups": ["sub1"]` on `kosha-sub1` means only the mode with slug `sub1` can list *or* execute its tools. A call from mode `sub2` is rejected. **Caveats. These are real gaps, not footnotes:**
1. **Mode switching changes identity.** `switch_mode` is in the `mode` permission group. If an agent in `sub1` can switch to `sub2`, its calls then legitimately go through `kosha-sub2` and are charged to `sub2`'s cap, so it can escape its own cap. **Fix:** leave `mode` (and `subagent`/`subtask`, unless needed) out of the fleet modes' `groups`. Still to be confirmed live.
2. **Any `"mcp"` in the server's `groups` opens it to every mode that has the `mcp` group.** It must be the slug only.
3. **Bob's own guidance contradicts its code.** The bundled `configure-mcp` skill text says `groups` "must match known Bob permission groups (read, edit, execute, mcp, ...). Anything else silently grants nothing." The code (`getGroupIds` appends `mode.id`) says a slug works. Code wins, but this is exactly what the live test must settle.
4. **Kosha's own config is writable by the agent it governs.** `.bob/mcp.json`, `.bob/custom_modes.yaml` and `.bob/settings.json` (where the hooks live) sit in the workspace. An agent can rewrite `groups` or delete the hook through `kosha-mcp write_file` or a native edit. **Proposed fix (my side, not done yet):** the parser prices any write under `.bob/` or `.claude/` as privilege (L5, always ask).
5. **Subagent mode inheritance is unverified.** `spawn_subagent` builds a subtask from a preset's groups (`createSubTask(preset, groups)`). I haven't traced whether custom-mode subagents keep their own slug.

**The live test (about 5 minutes in the IDE):** register `kosha-sub1` with `"groups": ["sub1"]`, create modes `sub1` and `sub2` (both with the `mcp` group), open a task in `sub2` and ask it to call `run_command` on `kosha-sub1`. **Expected from the source:** the tool isn't offered, and a forced call returns "Tool mcp__kosha-sub1__run_command is not allowed to be executed". Then repeat from `sub1`, which should succeed and record `agent_id=sub1` in `koshad`.

### (b) MCP tool-call timeout: 60s by default, not 270s

- **270s is `execute_command`'s own limit** (`static MAX_TIMEOUT_SECONDS=270; DEFAULT_TIMEOUT_SECONDS=30`). I had conflated it with MCP.
- **MCP tool calls:** `runToolWithRestart` calls `session.callTool(name, args, {timeout: serverConfig.timeout})`. The bundled MCP client defaults an unset timeout to `fX = 6e4`, i.e. **60,000 ms** (`let _ = a?.timeout ?? fX`). Only `{timeout}` is passed, so progress notifications do **not** extend it.
- **Units are ms.** The settings UI snaps values to `5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000`; anything else becomes 60000. Values in `mcp.json` are used as written.
- **Consequence:** with the default, Bob abandons any kosha-mcp call after 60s while the command keeps running inside kosha-mcp (up to 270s), and the agent may retry and run it twice. **Set `"timeout": 300000`.**
- Hook timeouts are separate: the default is `tei = 10` **seconds**, set by the per-hook `timeout` field (seconds).

### (c) What Bob's PreToolUse runner treats as allow (fail-open). Executed, not inferred

Only two things block:
- **exit code 2**: the reason is stderr, else stdout, else "PreToolUse blocked by hook". Stdout JSON is ignored, even if it says allow.
- **exit 0** where the entire trimmed stdout is JSON starting with `{` containing `hookSpecificOutput.hookEventName == "PreToolUse"` and `hookSpecificOutput.permissionDecision == "deny"`, exact lowercase.

**Everything else allows:**

| Allowed (fail-open) | Bob's log line |
|---|---|
| exit 1, 3, 130, 255: any nonzero except 2 (an uncaught Python exception is exit 1) | `hook exited with code N` |
| exit 1 **with** a valid JSON deny on stdout | `exited with code 1` |
| exit 127: interpreter or script not found (e.g. wrong venv path) | `exited with code 127` |
| killed by any signal (SIGTERM, SIGKILL, **SIGALRM with no handler installed**) | `hook failed` |
| hook exceeds its `timeout` | `hook failed` |
| **stdout or stderr over 1MB, even with exit 2** (maxBuffer) | `hook failed` |
| task `cwd` doesn't exist (the hook can't spawn) | `hook failed` |
| exit 0 with malformed JSON, plain text, or **any log line before the JSON** | `Ignoring invalid PreToolUse hook output` |
| exit 0 with JSON deny but `hookEventName` missing or wrong, or deny at top level, or `"DENY"` | (silent) |
| exit 0 with `permissionDecision: "ask"` | (silent) |
| exit 0, no output | (silent) |
| hook entry `disabled: true` | (silent) |
| invalid matcher regex | `Ignoring invalid ... matcher` |
| (from source, not executed) settings file fails Bob's strict schema, e.g. one unknown key: the **whole file's hooks are dropped** | `Ignoring invalid hook settings in <file>` |
| (from source) workspace hooks when the workspace isn't trusted, or `disableWorkspaceHooks` / `disableGlobalHooks` set | (silent) |

Also:
- **Matchers are unanchored `RegExp.test` on `tool_name`.** `write_file` also matches kosha's own `mcp__kosha-sub1__write_file` (MCP tool ids are `mcp__<server>__<tool>`, max 64 chars). **Anchor them:** `^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$`.
- If several hooks match, **any one blocking blocks**: one failing open doesn't cancel another's deny.
- The hook runs once, before Bob's own approval prompt. Resumed (approved) calls don't re-run it; that's by design, not a bypass.
- Subagent tasks inherit the parent's `onPreToolUse`, so hooks cover subagents.

**What kosha-hook (task 8) must do, from the above:**
1. Install the SIGALRM handler as the **first statement**, before importing anything heavy. An alarm that fires before the handler exists kills the process, and a killed hook means allow.
2. Keep the alarm well under Bob's hook timeout (default 10s; set `timeout` explicitly).
3. **Deny = `exit 2` with the reason on stderr.** Don't rely on JSON deny.
4. Print nothing else to stdout.
5. Cap all output far below 1MB.
6. Normalise **every** error path to exit 2.
7. Register the hook with the **absolute** interpreter path.
8. Anchor the matcher.
9. Keep the settings file schema-exact.

Harness used for (c): `bob_hooks_extracted.js` and `harness.js` in the session scratchpad. Not committed; I can add them under `docs/` if you want the result reproducible.

## 2026-09-26: task 7 done (kosha-hook for Claude Code)

- `kosha/adapters/claude_hook.py`, console script `kosha-hook`. Handles `PreToolUse`, `PostToolUse` and `PostToolUseFailure` (the latter exists in Claude Code 2.1.283 and carries `error`). The payload fields were checked against the installed Claude Code: `{session_id, transcript_path, cwd, permission_mode, agent_id (subagents only), agent_type, tool_name, tool_input, tool_use_id}`.
- **Mapping:** Bash → `run_command`; Edit/MultiEdit/NotebookEdit → `edit_file`; Write → `write_file`. Read-only tools (Read, Glob, Grep, ...) pass without asking koshad. **Any other tool is priced as `other`** (unknown = expensive) rather than waved through. `agent_id` comes from the payload (`main` when absent).
- **Decisions:**
  - allow → exit 0 and **no output**, so Claude Code's own permission mode still applies. Kosha adds a gate, it never removes one.
  - deny and ask → **exit 2, reason on stderr**, which is the one blocking channel both harnesses honour. For ask, the text tells the human to approve in Kosha and the agent to retry the same call (the approval-by-retry flow).
- **Settling needs no state:** `action_id = claude_code:<session_id>:<tool_use_id>`. PostToolUse confirms, PostToolUseFailure refunds, and neither ever blocks, even with koshad down.
- **Fail-closed, built to the Bob findings above:**
  - The SIGALRM deadline (`KOSHA_HOOK_DEADLINE`, default 3s) is armed before any non-stdlib import.
  - Every path, including the alarm and every `BaseException`, ends in `os._exit`.
  - Nothing ever goes to stdout, and stderr is capped at 2000 chars.
  - Malformed stdin blocks.
- **Also fixed:** `KoshaDB` still defaulted to the old 100/50 budgets while the table says 750/375. Anything constructing `KoshaDB()` directly ran on stale numbers. Budgets now default to the price table.
- Tests: `tests/test_claude_hook.py` (26). All run the hook as a **real subprocess** and assert exit code, stdout and stderr:
  - allow is silent; subagent identity is recorded; hard deny; ask blocks, and after approval the retry passes; edit/write leases are recorded
  - PostToolUse confirms; PostToolUseFailure refunds
  - koshad down, and a hanging koshad, both block within the deadline
  - a hang inside the hook hits the alarm
  - crashes with `RuntimeError`, `KeyboardInterrupt`, `SystemExit(0)`, `SystemExit(1)` and `MemoryError` all exit 2
  - five kinds of malformed input all block
  - a 5MB reason is truncated
  - the installed console script fails closed
  - Full suite: 513 passed.
- **Not installed in this repo's `.claude/settings.json`,** on purpose: it would gate this very session. The registration snippet is in the module docstring (anchored matcher, absolute path to the venv's `kosha-hook`, timeout 10).

## 2026-09-26: Live group-isolation test in Bob IDE (both directions verified)

**Setup** (`.bob/` in this repo; created for the test, not committed):
- `.bob/mcp.json`: `kosha-sub1` and `kosha-sub2`, both `/home/claude/Repos/kosha/.venv/bin/kosha-mcp --agent subN --session grouptest --workspace /home/claude/Repos/kosha`, with `"groups": ["subN"]`, `alwaysAllow` for the six tools, and `timeout: 300000`.
- `.bob/custom_modes.yaml`: modes `sub1` and `sub2`, each with `groups: [read, mcp]`. Bob adds the mode's own slug to its group set. No `mode`, `edit` or `execute` groups.
- One `koshad` (`.venv/bin/koshad`, 127.0.0.1:8765). Before the test, both entries were launched with their exact command and args over MCP stdio, and both listed all six tools.

**Results.** The verdict is koshad's `actions` table, not the agents' chat text: one agent first said it didn't have a tool it did have.

| Mode | Tool called | Chat result | koshad record |
|---|---|---|---|
| sub1 | `mcp__kosha-sub1__run_command` (`echo baseline-sub1`) | `exit 0`, stdout `baseline-sub1` | `agent_id=sub1`, allow, confirmed |
| sub2 | `mcp__kosha-sub2__run_command` (control; the text `echo cross-sub1-via-sub2` is a mislabel, it's sub2's own tool) | `exit 0`, stdout `cross-sub1-via-sub2` | `agent_id=sub2`, allow, confirmed |
| sub2 | `mcp__kosha-sub1__run_command` | tool not in sub2's list, "not available" | **no request received** |
| sub1 | `mcp__kosha-sub2__run_command` | tool not in sub1's list, "not available" | **no request received** |

Both servers were proven to work inside Bob (rows 1–2), so both cross results (rows 3–4) are informative.

**What this shows:** a mode is offered a server's tools only if the server's `groups` share a value with the mode's `groups` plus its slug (`getToolsForMode`). The tools are registered for every task and filtered per mode. With each kosha server's `groups` set to its mode's slug alone, a mode never sees another agent's kosha tools, so it can't spend another agent's budget through them.

**Conditions it depends on.** These are configuration, not something Bob guarantees:
- no `"mcp"` in any kosha server's `groups`;
- no mode lists another mode's slug in its `groups`;
- fleet modes lack the `mode` group, otherwise `switch_mode` lets an agent act as another agent (**not tested**);
- the agent can't rewrite `.bob/mcp.json` or `.bob/custom_modes.yaml`. **Today it can**, via file tools; this is still open (proposed fix: price writes under `.bob/` as L5).

**What this does NOT show:** Bob's source also has an execution-time check (`N2r`: "Tool X is not allowed to be executed. Swap to a mode that can use mcp tools."). An out-of-list call can't be issued from chat, so that check was not exercised live. It's present in source and not verified live.

## 2026-09-26: task 8 done (kosha-hook for Bob)

- **The same `kosha-hook` now serves both harnesses** (`kosha/adapters/claude_hook.py`). The harness is told apart by tool name.
  - Bob native tools (verified in Bob's source): `execute_command` (`command`, workspace-relative `cwd`) → `run_command`; `write_file` → `write_file`; `apply_diff`, `insert_content`, `search_and_replace`, `office_edit` (all `path`, workspace-relative) → `edit_file`. Paths are resolved against the payload's `cwd`.
  - Bob read-only tools and **kosha-mcp's own tools (`mcp__kosha*`) pass through**; the latter are already priced by kosha-mcp. Other MCP tools are priced as `other`.
- **Design choice: native Bob calls are priced through koshad, not blanket-denied.** That's the PDF's "reused near-unchanged". Blanket-denying would brick every non-kosha Bob mode wherever the hook is installed.
  - **Limitation:** Bob's hook payload has no agent identity, so every native call is charged to one pooled agent, **`bob-native`**. Fleet budget and escalation see these calls; per-agent caps can't separate them. For per-agent identity, agents must use the kosha-mcp tools (layer 1 removes the native ones).
  - Bob fires PostToolUse only on success, so a failed native call is never refunded (the conservative side).
- **Register for Bob** in `<workspace>/.bob/settings.json` (trusted workspace), with the same `hooks` block shape as Claude Code, PreToolUse and PostToolUse only. Use the **anchored** matcher `^(execute_command|write_file|apply_diff|insert_content|search_and_replace|office_edit)$`, the absolute path to the venv's `kosha-hook`, and `timeout: 10`. The demo copy goes into task 10.
- **Tests:** `tests/test_bob_hook.py` (30), plus helper `tests/bob_runtime.py`.
  - Mapping for all six native tools, workspace-relative `cwd`, pass-through, PostToolUse confirm.
  - **End to end through Bob's real hook runner** (`Tb` and friends), extracted from the local Bob install **at test time into a temp dir, never committed**, since Bob is proprietary. These tests skip if Bob or node is missing. Under Bob's own runner:
    - allow passes
    - hard deny, and ask, block with kosha's reason
    - koshad down blocks
    - a hang inside the hook blocks via our alarm, well before Bob's 10s
    - `RuntimeError`, `SystemExit(1)` and `KeyboardInterrupt` crashes block
    - a 5MB reason still blocks (it's truncated below Bob's 1MB fail-open limit)
    - the anchored matcher leaves kosha-mcp tools alone
    - PostToolUse settles
    - a misregistered interpreter path fails open (exit 127): **documented, not fixable from inside the hook**
  - **Mutation-checked:** making the hook's failure path exit 1 instead of 2 fails the four hang/crash tests under Bob's runner.
  - Full suite: 543 passed.
- **Layer 4, `deniedCommands`, tested against Bob's real matcher (`HZ`/`ZVt`):** it blocks the literal prefix (`rm -rf build` with `rm -rf` denied; `git push --force` wins over an approved `git push`). **Asserted gaps**, each of which gets past a denied `rm -rf`:
  - `python3 cleanup.py`
  - `bash -c 'rm -rf build'`
  - `rm -fr build`
  - `rm -r -f build`
  - `/bin/rm -rf build`
  - `sudo rm -rf build`
  - an approved entry as long as the denied one (a tie goes to allow)

  Caveat: this exercises the matcher on single commands. Bob splits compound commands first (tree-sitter), and that splitting wasn't exercised, so whether it looks inside `bash -c` strings is unknown.
- **Not done:** I haven't written `deniedCommands` into `~/.bob/settings/settings.json`. It's global and would change all Bob usage on this machine, so it goes with the demo config in task 10 unless you want it now.

## 2026-09-26: Integration decisions locked (before task 9)

**Known limitations in v1. Name these openly in the pitch deck:**
- **Native-tool calls are priced, but attributed to a shared pool, not per-agent.** Bob's native tools (`execute_command`, `write_file`, `apply_diff`, ...) are gated by kosha-hook and charged to one pooled identity, `bob-native`, because Bob's hook payload carries no agent identity. Fleet budget and escalation count them; per-agent caps can't separate subagents that use native tools. Per-agent precision holds only for calls made through kosha-mcp (one instance per mode, `groups`-pinned, verified live above). The demo modes remove native tools (layer 1), so in the demo `bob-native` should see no calls at all.
- **Future work: agent-visible budget query.** Agents can't ask Kosha about their remaining budget or the price of an action before trying it. There is deliberately no `kosha_status`/`kosha_quote` tool; adapters call only `/decide` and `/settle`. Revisit only if the task 11 demo shows it's needed.

**Decisions:**
1. **No agent-visible query tool.** Already true, and nothing contradicts it: kosha-mcp exposes exactly `run_command`, `edit_file`, `write_file`, `git`, `db_exec`, `deploy`, and adapters call only `/decide` and `/settle`.
2. **Native Bob tools stay priced and pooled under `bob-native`; no deny-all.** Already true since task 8, and nothing contradicts it. The only per-agent claims in code and docs are about kosha-mcp calls.
3. **Agents are told the gating rules.** Applied. **Finding: Bob never forwards an MCP server's `instructions` to the model.** Its MCP client stores them and nothing calls `getInstructions()`, so kosha-mcp's gating text had never reached a Bob agent. The rules now travel on the channels Bob actually shows:
   - **Every kosha-mcp tool description** ends with the gating rule (tested).
   - **Every block result** uses one shared format, `client.block_text()`, in both kosha-mcp results and kosha-hook stderr:
     - line 1 names the outcome: `KOSHA HELD FOR HUMAN APPROVAL: nothing was run.` / `KOSHA DENIED: nothing was run, and retrying the same call will be denied again.` / `KOSHA UNAVAILABLE: nothing was run (Kosha could not decide, so it failed closed).`
     - line 2 is kosha's reason and suggestion
     - line 3 is the next step: for held, stop, tell the user, and **retry this exact call once they say it's approved**; for denied, re-plan; for unavailable, tell the user and retry once Kosha is back
     - every variant says "do not work around it"
   - **Mode `roleDefinition`** (test modes in the local `.bob/custom_modes.yaml`; task 10 carries the same text into `demo_repo/.bob/`): the fleet context, the tool names, and one rule per status tag, including "retry the exact same call after the user says it's approved", plus "never report a held, denied or unavailable step as done".
   - This describes the **current** approval-by-retry behaviour. It does not decide wait-vs-bounce or how much fleet context goes in the message; both are still deferred until a real 3-subagent run.

**Demo risks found while checking #3:**
- **The approval text says "approved in the Kosha dashboard", and `web/` doesn't exist yet (task 12).** Until it does, a human approves with `curl -X POST localhost:8765/approvals/<id> -H 'content-type: application/json' -d '{"decision":"approve_once"}'` (ids from `GET /approvals`). **If the demo is recorded before `web/` exists, either build the minimal bundle view first or change that phrase.**
- **Pricing-track text** (not mine, not touched): the hard-deny suggestion says "attach an approval token from a human", but no token mechanism exists, so an agent may invent one. The L5 suggestion ("use a variant ... e.g. chmod 644") is fine re-planning, but it slightly competes with "retry the exact call".

## 2026-09-26: Approval and token gaps: real code gaps, fixed on the system side

**Found by reproducing them against a throwaway koshad through real kosha-mcp, before any fix:**
1. **An approval covered the target, not the content.** The fingerprint was `(session, agent, tool, argv, targets)`, and for `db_exec`/`edit_file`/`write_file` `argv` is empty and `targets` is just the alias or path. A human approved `drop table audit_old` on prod; the retry with `drop table users` was allowed ("a human approved this action") and dropped `users`.
2. **The approved path skips `policy.decide` entirely.** After approving that drop, `drop database prod_main`, normally a hard deny, was **allowed**.
3. **The human approved blind.** The approval bundle showed `tool` and `argv` only, so the SQL or file body being approved was never displayed.
4. **A self-issued `approval_token` lifted a hard deny.** kosha-mcp copied every agent argument, declared or not, into `raw`, and `policy._has_approval_token` accepts any non-empty value. `drop database app` on prod plus `approval_token: "i-made-this-up"` gave **allow** (not even held).

**Fixed (system side):**
- The fingerprint now includes the canonical `raw`, so an approval covers exactly the call the human saw. Any other arguments need a new approval, and a hard-deny statement can no longer ride on an unrelated approval.
- The bundle carries `raw` for every entry, so the human sees the SQL or content.
- kosha-mcp forwards only each tool's declared arguments. kosha-hook forwards only each native tool's declared parameters (Claude Code and Bob; lists in `PARAMS`), and for unknown tools it drops `approval_token`.
- Regression tests cover all four cases. Re-running the reproduction: the retry with different SQL is held; the exact approved call runs and `users` survives; the hard deny after an unrelated approval is denied; the made-up token is denied. Full suite: 743 passed.

**Still open, Pricing track (`policy.py`, not touched):**
- The token check accepts any non-empty value, and nothing issues or verifies tokens. Stripping is closed on my side, but any other path that writes `raw` would reopen it. Either have koshad issue and verify tokens, or drop the token exemption and make prod drops an ask.
- The hard-deny suggestion tells agents to "attach an approval token from a human", which invites exactly this.
- `delete from users where ...` on prod is allowed without asking (`destructive_sql` covers only deletes without a WHERE). That may be intended, but it's worth a deliberate decision.

## 2026-09-27: task 9 done (fs_guard). Stop-and-report checkpoint

**Status of other items at this checkpoint:**
- **Raw redaction: NOT done.** Proposed, not started; the token and fingerprint work took priority. Open tension: approvers must see the content, since hiding it was one of the gaps just closed. A workable split is to mask token-like values everywhere but hash file bodies only in `events` and `actions.raw`, never in the approval bundle. Awaiting a decision.
- **Approval-token bypass in `policy.py`: CLOSED, verified 2026-09-27.** Teammate commit `aa15ad0` ("removes the approval_token override on hard-deny patterns"). Checked by me, not taken on the commit message:
  - **The diff removes the exemption outright:** `_has_approval_token` is deleted, the prod-drop hard deny is unconditional (`if _drops_prod_db(action): return ...`), and the "attach an approval token" suggestion is replaced with "Hard-deny patterns have no override". `grep` finds no reader of `approval_token` left in `kosha/`; the only references are the adapters' strip lists.
  - **Re-ran tonight's reproduction** against the rebased tree. **Direct `/decide`** with `raw: {db: prod, sql: "drop database app", approval_token: "i-made-this-up"}`, bypassing the adapters' stripping so it tests the policy fix itself, gives **deny / hard_deny** (tonight: allow / ok). Through kosha-mcp it gives `KOSHA DENIED`. Full suite on the rebased tree: 799 passed.

**`kosha/system/fs_guard.py`** (layer 3, the backup lock):
- **What it does:** a watchdog/inotify watcher on a working tree, handling created, modified, deleted and moved events. A change covered by an unexpired `expected_writes` lease is accepted and becomes the new restore point. Anything else is undone and logged as `bypass_detected` (`{event, path, outcome, root}`):
  - modified/deleted known file → **restored to its last accepted content**
  - new file → **quarantined** to `.kosha_quarantine/<timestamp>/<path>`
  - move → the source is restored, the destination restored if it was known, else quarantined
  - a move out of the tree arrives as a delayed delete and is restored
- **Deviation from spec: restore last accepted content, not `git checkout --`.** Agents make approved, uncommitted edits through kosha-mcp, and resetting to HEAD would wipe those along with the bypass. For a clean tracked file the result is identical. `git checkout --` remains the fallback for files over 5MB (not snapshotted).
- **Ignored:** `.git`, `.kosha_quarantine`, `.bob`, `.venv`, `__pycache__`, `node_modules`, sqlite files (`db_exec` leases nothing; the DB is kosha's to price, not fs_guard's to revert), editor swap files, symlinks.
- **No undo loop:** its own restores are recognised by content, one bypass gives one event (tested). A guard exception is logged as `fs_guard_error` instead of dying silently.
- **Hosted by koshad** when `KOSHA_GUARD_ROOT` is set (optional `KOSHA_GUARD_SESSION` tags the events); it starts and stops with koshad. Standalone: `python -m kosha.system.fs_guard <root> [--session S]`.

**Known windows. Named, tested and documented; not closed:**
1. **Settle grace (0.5s, was 2s).** A bypass write to a leased path within 0.5s after its gated action settles is accepted. The end-to-end test caught this at 2s; I confirmed it directly and shrank it. It can't be dropped: one gated write emits several inotify events (truncate, then write), and without grace the guard would "undo" an approved write to its half-written state. A test asserts the window exists.
2. **Command leases cover their cwd for the command's duration** (up to 300s). A native write under that directory while a gated command runs is indistinguishable from the command's own.
3. **After the fact:** it undoes writes, it doesn't prevent them. Anything read or sent before the undo is out. Layers 1 and 2 prevent; this layer only undoes.
4. **Files over 5MB** aren't snapshotted: a change to an unchanged-size large file can't be detected by content, and restore falls back to git (tracked) or nothing (untracked).

**Tests: `tests/test_fs_guard.py` (17), real inotify events in a real git repo; the file passed 5 consecutive runs.**
- tracked edit reverted
- untracked new file quarantined
- untracked existing file restored
- **script-wrapper bypass caught** (`python3 wrapper.py`, the case `deniedCommands` can't see)
- `rm` untracked restored
- **`mv` tracked out of the tree restored**
- `mv` inside the tree: source restored, destination quarantined
- `rm -rf` directory restored
- `mv` onto a known file restores both
- leased write accepted and becomes the restore point (a later bypass restores to the approved 1.3, not HEAD's 1.2)
- directory lease covers a command's writes
- expired lease doesn't cover
- one bypass, one event, no loop
- git internals, quarantine and `*.db` ignored
- **end to end:** kosha-mcp `write_file`/`edit_file` through a live koshad pass, and a native write afterwards is reverted to the approved content
- the grace-window gap is asserted
- koshad hosts the guard only when configured

Full suite: 760 passed.

**Not verified:** a real Bob native tool (`write_file`/`execute_command` with the groups left enabled) driving the bypass. That needs the IDE, so the tests make the equivalent filesystem writes directly, which is what those tools end in. To check it live: run koshad with `KOSHA_GUARD_ROOT=<workspace>`, use a mode *with* `edit`, ask it to `write_file` something without the hook registered, and watch `/stream` for `bypass_detected`.

## Next (task 10 onward), not started
demo_repo → demo scenario → web/.
