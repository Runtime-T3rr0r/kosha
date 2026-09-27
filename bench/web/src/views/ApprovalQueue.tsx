import { useEffect, useMemo, useState } from "react";
import { FLEET_BUDGET, AGENT_CAP, fmt, isGated, type Gated } from "../data";
import { AgentTag, Cell, Level, Panel, Stat, VerdictTag } from "../components/primitives";
import { Command, StepCommand } from "../components/Command";
import { useReplay, type Resolution } from "../replay";
import "./ApprovalQueue.css";

const STATUS_LABEL: Record<Resolution["kind"], string> = {
  approve_once: "approve_once",
  approve_reset: "approve_reset",
  deny: "deny",
};

export function ApprovalQueue() {
  const { session, cursor, pending, resolutions, resolve, windowStart, play, playing } = useReplay();
  const [focusId, setFocusId] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [denying, setDenying] = useState(false);
  const [note, setNote] = useState("");

  const focused: Gated | undefined = pending.find((p) => p.i === focusId) ?? pending[0];

  useEffect(() => {
    setDenying(false);
    setNote("");
  }, [focused?.i, session.id]);

  const mine = resolutions[session.id] ?? {};
  const history = Object.entries(mine)
    .map(([i, r]) => ({ step: session.steps[Number(i)] as Gated, r }))
    .sort((a, b) => b.r.at - a.r.at);

  // The window as the focused action saw it: allowed actions since the last reset,
  // before the asked action, in stream order.
  const bundle = useMemo(() => {
    if (!focused) return null;
    const threshold = focused.window?.threshold ?? 3;
    const windowActions = session.steps
      .slice(windowStart, focused.i)
      .filter(isGated)
      .filter((s) => s.decision.decision === "allow");
    const conseq = windowActions.filter((s) => s.decision.level >= threshold);
    return { threshold, windowActions, conseq };
  }, [focused, session, windowStart]);

  if (!focused || !bundle) {
    return (
      <div className="aq">
        <Panel title="Pending approval" right={<span className="mono faint">cursor {cursor}/{session.steps.length}</span>}>
          <div className="aq-empty">
            <div className="aq-empty-title">Nothing is waiting for a human at step {cursor} of {session.id}.</div>
            <p className="muted">
              The queue follows the replay. Asked actions appear here as the Live session tab streams the stored
              fleet, chip by chip. {history.length > 0 && `${history.length} resolved in this browser.`}
            </p>
            <button className="btn primary" onClick={play} disabled={playing}>
              {playing ? "Replaying…" : "▶ Play replay"}
            </button>
          </div>
        </Panel>
        {history.length > 0 && <History history={history} />}
      </div>
    );
  }

  const d = focused.decision;
  const w = focused.window;
  const rows = showAll ? bundle.windowActions : bundle.conseq;
  let running = 0;
  const bundleAgents = new Set([...bundle.conseq.map((s) => s.agent_id), focused.agent_id]);
  const conseqSum = bundle.conseq.reduce((a, s) => a + s.decision.price, 0) + d.price;
  const rogue = session.rogue_agent === focused.agent_id && session.kind === "rogue_composed";

  return (
    <div className="aq">
      <div className="aq-top">
        <Panel
          className="aq-card"
          title={
            <>
              Step #{focused.i} is waiting for you
            </>
          }
          right={
            <span className="mono faint">
              {pending.length === 1 ? "the only action waiting" : `the oldest of ${pending.length} waiting`}
            </span>
          }
        >
          <div className="aq-card-head">
            <Level level={d.level} />
            <AgentTag agent={focused.agent_id} rogue={rogue} />
            <VerdictTag verdict={d.decision} />
            <span className="mono faint">
              {focused.trajectory} · step {focused.step}
            </span>
          </div>
          <div className="aq-cmd">
            <StepCommand step={focused} />
          </div>
          <div className="aq-reason">
            <span className="label">reason</span>
            <div className="aq-reason-text">{d.reason}</div>
          </div>
          {d.suggestion && (
            <div className="aq-suggest">
              <span className="label">what would be allowed</span>
              <div className="aq-suggest-text">{d.suggestion}</div>
            </div>
          )}
          <div className="aq-facts">
            <Stat label="price" value={fmt.price(d.price)} />
            <Stat label="cell" value={<span style={{ fontSize: "var(--t-body)" }}>{d.cell}</span>} />
            <Stat label="rule" value={<span style={{ color: "var(--ask)" }}>{d.rule}</span>} />
            <Stat label="tool" value={<span style={{ fontSize: "var(--t-body)" }}>{focused.tool}</span>} sub={focused.action} />
            {w && (
              <>
                <Stat label="fleet before" value={`${fmt.price(w.fleet_spent_before)}`} sub={`/ ${FLEET_BUDGET}`} />
                <Stat label="agent before" value={`${fmt.price(w.agent_spent_before)}`} sub={`/ ${AGENT_CAP}`} />
                <Stat
                  label="window conseq."
                  value={`${w.consequential}`}
                  sub={`at L${w.threshold}+ · trigger at 2`}
                  tone={w.consequential >= 2 ? "var(--ask)" : undefined}
                />
                <Stat label="window agents" value={`${w.agents.length}`} sub={w.agents.join(" ")} />
              </>
            )}
          </div>
          {focused.targets.length > 0 && (
            <div className="aq-targets mono">
              <span className="label">targets</span> {focused.targets.join("  ")}
            </div>
          )}
          <div className="aq-actions">
            <button className="btn allow" onClick={() => resolve(focused.i, { kind: "approve_once", at: Date.now() })}>
              Approve once
            </button>
            <button className="btn ask" onClick={() => resolve(focused.i, { kind: "approve_reset", at: Date.now() })}>
              Approve and reset window
            </button>
            <button className="btn deny" onClick={() => setDenying((v) => !v)} aria-expanded={denying}>
              Deny with note…
            </button>
            <span className="faint aq-local">
              Recorded in this browser only; the stored stream was decided by policy.decide and is not re-run.
            </span>
          </div>
          {denying && (
            <form
              className="aq-deny"
              onSubmit={(e) => {
                e.preventDefault();
                if (!note.trim()) return;
                resolve(focused.i, { kind: "deny", at: Date.now(), note: note.trim() });
              }}
            >
              <label className="label" htmlFor="aq-note">
                note returned to the agent
              </label>
              <textarea
                id="aq-note"
                value={note}
                autoFocus
                rows={3}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Why this is denied and what the agent should do instead"
              />
              <button className="btn deny" type="submit" disabled={!note.trim()}>
                Deny
              </button>
            </form>
          )}
        </Panel>

        <Panel title={`Also waiting (${pending.length} in all)`} className="aq-queue" bodyClass="" collapsible>
          <table className="grid">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>agent</th>
                <th>lvl</th>
                <th>command</th>
                <th>rule</th>
                <th className="num">price</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((p) => (
                <tr
                  key={p.i}
                  className={p.i === focused.i ? "aq-row-on" : "aq-row"}
                  tabIndex={0}
                  onClick={() => setFocusId(p.i)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setFocusId(p.i)}
                >
                  <td className="num">{p.i}</td>
                  <td>
                    <AgentTag agent={p.agent_id} />
                  </td>
                  <td>
                    <Level level={p.decision.level} />
                  </td>
                  <td>
                    <StepCommand step={p} maxLines={1} />
                  </td>
                  <td className="mono">{p.decision.rule}</td>
                  <td className="num">{fmt.price(p.decision.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <Panel
        title="What the fleet did first"
        className="aq-bundle"
        right={
          <label className="aq-toggle mono">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show all{" "}
            {bundle.windowActions.length} window actions
          </label>
        }
        bodyClass=""
      >
        <div className="aq-headline">
          <div className="aq-headline-text">Every individual check said yes. Together it's this bundle.</div>
          <div className="aq-headline-stats mono">
            <span>
              <b>{bundle.conseq.length + 1}</b> consequential actions (L{bundle.threshold}+)
            </span>
            <span>
              <b>{bundleAgents.size}</b> agents
            </span>
            <span>
              Σ price <b>{fmt.price(conseqSum)}</b>
            </span>
            <span>
              fleet <b>{fmt.price(w?.fleet_spent_before ?? 0)}</b>/{FLEET_BUDGET}
            </span>
            {windowStart > 0 && <span className="faint">window reset at #{windowStart}</span>}
          </div>
        </div>
        <table className="grid">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>agent</th>
              <th>lvl</th>
              <th>command</th>
              <th>cell</th>
              <th className="num">price</th>
              <th>verdict</th>
              <th className="num">running Σ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              running += s.decision.price;
              const dim = showAll && s.decision.level < bundle.threshold;
              return (
                <tr key={s.i} className={dim ? "aq-dim" : ""}>
                  <td className="num">{s.i}</td>
                  <td>
                    <AgentTag agent={s.agent_id} rogue={session.kind === "rogue_composed" && s.is_rogue_agent} />
                  </td>
                  <td>
                    <Level level={s.decision.level} />
                  </td>
                  <td>
                    <StepCommand step={s} maxLines={2} />
                  </td>
                  <td>
                    <Cell cell={s.decision.cell} />
                  </td>
                  <td className="num">{fmt.price(s.decision.price)}</td>
                  <td>
                    <VerdictTag verdict={s.decision.decision} />
                  </td>
                  <td className="num">{fmt.price(running)}</td>
                </tr>
              );
            })}
            <tr className="aq-asked">
              <td className="num">{focused.i}</td>
              <td>
                <AgentTag agent={focused.agent_id} rogue={rogue} />
              </td>
              <td>
                <Level level={d.level} />
              </td>
              <td>
                <Command text={focused.command || `${focused.tool} ${focused.path}`} maxLines={2} />
              </td>
              <td>
                <Cell cell={d.cell} />
              </td>
              <td className="num">{fmt.price(d.price)}</td>
              <td>
                <VerdictTag verdict="ask" />
              </td>
              <td className="num">{fmt.price(running + d.price)}</td>
            </tr>
          </tbody>
        </table>
      </Panel>

      {history.length > 0 && <History history={history} />}
    </div>
  );
}

function History({ history }: { history: { step: Gated; r: Resolution }[] }) {
  return (
    <Panel title={`Decided so far (${history.length})`} right={<span className="faint">recorded in this browser only</span>} bodyClass="" collapsible>
      <table className="grid">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>status</th>
            <th>agent</th>
            <th>lvl</th>
            <th>command</th>
            <th>note</th>
            <th>resolved at</th>
          </tr>
        </thead>
        <tbody>
          {history.map(({ step, r }) => (
            <tr key={step.i}>
              <td className="num">{step.i}</td>
              <td className={`mono aq-res-${r.kind}`}>{STATUS_LABEL[r.kind]}</td>
              <td>
                <AgentTag agent={step.agent_id} />
              </td>
              <td>
                <Level level={step.decision.level} />
              </td>
              <td>
                <StepCommand step={step} maxLines={1} />
              </td>
              <td>{r.kind === "deny" ? r.note : <span className="faint">—</span>}</td>
              <td className="mono faint">{new Date(r.at).toLocaleTimeString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
