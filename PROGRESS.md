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
- **Default approval block:** `approval.allowedExecutors = [{toolId: "execute_command", approvedCommands: [...read-only prefixes], deniedCommands: []}]`. Denied wins over approved, and it's matched per sub-command.

## 2026-09-26: task 6 done (kosha-mcp)

- `kosha/adapters/bob_mcp.py`, console script `kosha-mcp`: a stdio MCP server on the `mcp` 2.2 SDK's low-level `Server`.
  - Tools: `run_command(command, cwd?)`, `edit_file(path, old, new)`, `write_file(path, content)`, `git(args)`, `db_exec(db, sql)`, `deploy(target)`.
  - Flow: build Action → `client.decide` → execute **only** on allow → `client.settle`.
  - ask/deny return `is_error` with the reason and suggestion. For ask, it also tells the agent to retry the same call once a human approves.
  - Any exception inside the handler becomes an `is_error` result that says "nothing was run". Nothing surfaces as a success.
- **Identity:** `resolve_agent_id()` as specified, defaulting to `per_mode_instance` (`--agent <mode slug>`). One tweak: inline mode falls back to the instance name instead of returning `None`.
  - Pinning each instance to its mode with the MCP entry's `groups` field: see "Verification: groups, MCP timeout, hook fail-open" below. It's enforced at execution time in Bob's source, but not yet tested in a live IDE session, and it comes with caveats.
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

### (a) Does MCP `groups` restrict which mode can call an instance? Yes in the source, with caveats. Not yet tested live.

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

## Next (task 8 onward), not started
kosha-hook for Bob → fs_guard.py (stop and report) → demo_repo → demo scenario → web/.
