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

1. **Ownership gap: who maps Action → effects.yaml entry → (level, cell)?** Nothing does yet. `bench/check_effects_coverage.py` has an offline-only matcher, and `rubric.classify_action` needs the axes already resolved. My plan for task 5: `parser.py` does family/sub parsing *and* the effects.yaml match (read-only, no edits to the table), and resolvers fill in context subs (`tracked_clean`, `untracked`, `outside_workspace`, local/shared db). The Pricing track should confirm they're fine with the matcher living on the System side.
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

## Next (task 5 onward), not started
parser.py + resolvers.py → bob_mcp.py → claude_hook.py → kosha-hook for Bob → fs_guard.py (stop and report) → demo_repo → demo scenario → web/.
