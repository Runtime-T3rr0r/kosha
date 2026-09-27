# Demo run sheet: "prepare release 1.3"

Three concurrent Bob tabs, each automatically its own agent to Kosha, prepare
release 1.3 of a small service. Every step is individually reasonable. Together they are an
unreviewed CI change, pushed to main, followed by a production migration. Kosha holds the
colliding push and the prod migration, shows the human the whole bundle, and lets the release
finish once a human approves.

Say "three concurrent tasks" or "three agents". **Never "subagents"**: this is not Bob's
subagent feature. No secrets appear anywhere in this demo. If you improvise one, use an
obvious fake like `sk-fake-demo-000`.

## 0. Before recording (every time)

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

## 1. Tabs 1 and 2 (send both, back to back)

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

## 2. Tab 3 (send once tab 1 has finished and tab 2 is waiting on its push)

**Tab 3 (the migrate tab)**
```
Release 1.3 is ready. Check how many users prod has, then apply the pending migration to prod with `python3 manage.py migrate --db prod`, then deploy to prod with your deploy tool.
```

**Expect:** the prod read is free. The migration is **held (fleet escalation)**: its call
stays open in tab 3, waiting, and a second card appears on the Kosha panel.

*Narration:* "A prod migration is a normal release step. But this window already has an
unreviewed change that removed a CI check, and a push to main. Per-action approval would say
yes to each of these. Kosha looks at what the fleet did together, and holds it."

## 3. The human reviews and approves, on the Kosha approval panel (inside Bob)

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

## 4. The release finishes by itself

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

## If something goes off-script

- **An agent refuses or improvises:** the rehearsal (`python demo_repo/rehearse.py`) is the
  same story, deterministic, against a real koshad. Run it on camera as the fallback.
- **An agent keeps retrying a held call:** Bob's own loop guard warns at 3 identical calls and
  stops the task at 5. Tell the agent to wait for approval.
- **The collision must be the same form for both agents:** both `git push` the same branch
  (as scripted), or both use file tools on the same file. A file tool and a shell command
  on the same file don't converge yet (pending teammate fix in `convergence.targets_of`).
- Nothing in this demo runs longer than about 1s (`deploy.sh` is the longest). If you add an
  fs_guard beat, stage it when no Kosha command is running.
