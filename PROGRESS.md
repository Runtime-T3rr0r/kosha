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

## Next (task 6 onward), not started
bob_mcp.py → claude_hook.py → kosha-hook for Bob → fs_guard.py (stop and report) → demo_repo → demo scenario → web/.
