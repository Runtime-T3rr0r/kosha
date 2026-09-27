import { useEffect, useMemo, useRef } from "react";
import { AGENT_CAP, FLEET_BUDGET, agentsOf, fmt, isGated, pricing, sessions, type StreamStep } from "../data";
import { AgentTag, Cell, Level, Panel, Stat, VerdictTag, agentColor } from "../components/primitives";

/** The six levels, named once, inline with the lanes that use them. */
function LevelKey() {
  return (
    <span className="level-key">
      {[0, 1, 2, 3, 4, 5].map((l) => (
        <span key={l} className="level-key-item">
          <Level level={l} /> {pricing.level_names[String(l)]?.replace(/^L\d\s*/, "")}
        </span>
      ))}
    </span>
  );
}
import { Command, StepCommand } from "../components/Command";
import { useReplay } from "../replay";
import "./LiveSession.css";

const SPEEDS = [
  { ms: 150, label: "fast · 150ms" },
  { ms: 300, label: "300ms" },
  { ms: 450, label: "450ms" },
  { ms: 800, label: "800ms" },
  { ms: 1500, label: "slow · 1.5s" },
];

export function LiveSession() {
  const { session, cursor, selected, select } = useReplay();
  const visible = session.steps.slice(0, cursor);
  const sel = selected !== null ? session.steps[selected] : null;

  return (
    <div className="live">
      <SessionHeader />
      <Lanes steps={visible} />
      {sel && <Detail step={sel} onClose={() => select(null)} />}
      <EscalationState />
      <BudgetGauge />
      <EventLog steps={visible} />
    </div>
  );
}

// --- header: session meta + replay controls ---

function SessionHeader() {
  const { session, cursor, setCursor, playing, play, pause, restart, interval, setInterval, select } = useReplay();
  const total = session.steps.length;
  const firstAsk = session.steps.find((s) => s.decision && s.decision.decision !== "allow");
  const jumpFirstAsk = () => {
    if (!firstAsk) return;
    pause();
    setCursor(firstAsk.i + 1);
    select(firstAsk.i);
  };
  return (
    <div className="live-head">
      <div className="live-meta">
        <div className="live-title">
          <span className="tag-synth mono">SYNTHETIC</span>
          <span className="mono live-id">{session.id}</span>
          <span className="live-label">{session.label}</span>
        </div>
        <div className="live-kv mono">
          <span><span className="label">kind</span> {session.kind.replace(/_/g, " ")}</span>
          <span><span className="label">pool</span> {session.pool}</span>
          <span><span className="label">k</span> {session.k}</span>
          <span><span className="label">interleave</span> {session.interleave}</span>
          {session.rogue_divergence !== null && (
            <span><span className="label">divergence</span> step {session.rogue_divergence}</span>
          )}
          <span className="live-members">
            <span className="label">members</span>{" "}
            {Object.entries(session.members).map(([a, t]) => (
              <span key={a} className="live-member">
                <span className="swatch" style={{ background: agentColor(a) }} />
                {a}={t}
              </span>
            ))}
          </span>
        </div>
        <div className="faint live-note">{sessions.note}</div>
      </div>
      <div className="live-controls">
        <div className="live-buttons">
          <button className="btn primary" onClick={restart} title="Stream this fleet from step 0, chip by chip">
            ⟲ Replay
          </button>
          {playing ? (
            <button className="btn" onClick={pause}>❚❚ Pause</button>
          ) : (
            <button className="btn" onClick={play}>▶ Play</button>
          )}
          <button className="btn" onClick={() => setCursor(cursor - 1)} disabled={cursor === 0} title="Step back">◀</button>
          <button className="btn" onClick={() => setCursor(cursor + 1)} disabled={cursor >= total} title="Step forward">▶|</button>
          <button className="btn ask" onClick={jumpFirstAsk} disabled={!firstAsk}>
            Jump to first ask{firstAsk ? ` · #${firstAsk.i}` : ""}
          </button>
          <select className="live-speed mono" value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
            {SPEEDS.map((s) => (
              <option key={s.ms} value={s.ms}>{s.label}</option>
            ))}
          </select>
        </div>
        <div className="live-scrub">
          <input
            type="range"
            min={0}
            max={total}
            value={cursor}
            onChange={(e) => {
              pause();
              setCursor(Number(e.target.value));
            }}
            aria-label="Stream position"
          />
          <span className="mono live-counter">
            {String(cursor).padStart(String(total).length, "0")}/{total}
          </span>
        </div>
      </div>
    </div>
  );
}

// --- lanes ---

const COL_W = 156;

function Lanes({ steps }: { steps: StreamStep[] }) {
  const { session, playing, selected, select } = useReplay();
  const agents = agentsOf(session);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (playing && scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth;
  }, [steps.length, playing]);

  const firstDivergent = steps.find((s) => s.at_or_after_divergence)?.i;
  const twinSlot = session.kind === "twin_swapped" ? session.rogue_agent : null;
  const rogue = session.kind === "rogue_composed" ? session.rogue_agent : null;

  return (
    <Panel
      title="What each agent did"
      intro={
        <>
          One row per agent, in the order the actions happened ({steps.length} so far; the recording has no timestamps).
          Select an action to see why Kosha priced it the way it did. <LevelKey />
        </>
      }
      bodyClass="lanes-body"
    >
      <div className="lanes-labels">
        <div className="lane-axis-label label">#</div>
        {agents.map((a) => (
          <div key={a} className={`lane-label ${a === rogue ? "is-rogue" : ""}`}>
            <AgentTag agent={a} rogue={a === rogue} />
            <span className="mono faint lane-traj">{session.members[a]}</span>
            {a === twinSlot && <span className="mono lane-twin">CLEAN TWIN (swapped slot)</span>}
          </div>
        ))}
      </div>
      <div className="lanes-scroll" ref={scroller}>
        <div
          className="lanes-grid"
          style={{
            gridTemplateColumns: `repeat(${Math.max(steps.length, 1)}, ${COL_W}px)`,
            gridTemplateRows: `18px repeat(${agents.length}, 92px)`,
          }}
        >
          {steps.map((s) => (
            <div key={`ax${s.i}`} className="lane-axis mono" style={{ gridColumn: s.i + 1, gridRow: 1 }}>
              #{s.i}
            </div>
          ))}
          {agents.map((a, r) => (
            <div
              key={`bg${a}`}
              className={`lane-bg ${a === rogue ? "is-rogue" : ""}`}
              style={{ gridColumn: `1 / ${Math.max(steps.length, 1) + 1}`, gridRow: r + 2 }}
            />
          ))}
          {steps.map((s) => (
            <Chip
              key={s.i}
              step={s}
              row={agents.indexOf(s.agent_id) + 2}
              selected={selected === s.i}
              divergence={s.i === firstDivergent}
              onClick={() => select(selected === s.i ? null : s.i)}
            />
          ))}
          {steps.length === 0 && (
            <div className="faint lanes-empty" style={{ gridColumn: 1, gridRow: 2 }}>
              No steps yet. Press Replay.
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function Chip({
  step,
  row,
  selected,
  divergence,
  onClick,
}: {
  step: StreamStep;
  row: number;
  selected: boolean;
  divergence: boolean;
  onClick: () => void;
}) {
  const d = step.decision;
  const verdict = d?.decision ?? null;
  const cls = [
    "chip",
    d ? `v-${verdict}` : "ungated",
    selected ? "sel" : "",
    step.at_or_after_divergence ? "post-div" : "",
  ].join(" ");
  return (
    <button className={cls} style={{ gridColumn: step.i + 1, gridRow: row }} onClick={onClick} title={step.command || step.path || step.action}>
      <div className="chip-top">
        {d ? <Level level={d.level} /> : <span className="lvl chip-na">—</span>}
        <span className="mono chip-price">{d ? `${fmt.price(d.price)}u` : "0u"}</span>
        {verdict && verdict !== "allow" && <VerdictTag verdict={verdict} />}
        {divergence && <span className="chip-div mono">DIV</span>}
        <span className="mono faint chip-i">s{step.step}</span>
      </div>
      <div className="chip-cmd">
        <StepCommand step={step} maxLines={2} />
      </div>
      <div className="chip-foot mono">
        {d ? <span>{d.cell}</span> : <span className="faint">not gated · {step.note || "skip"}</span>}
        {d && d.rule !== "ok" && <span className="chip-rule">{d.rule}</span>}
      </div>
    </button>
  );
}

// --- fleet budget gauge ---

function BudgetGauge() {
  const { session, ledger } = useReplay();
  const agents = agentsOf(session);
  const left = FLEET_BUDGET - ledger.fleetSpent;
  const counts = useMemo(() => {
    const c: Record<string, { allowed: number; asked: number }> = {};
    for (const a of agents) c[a] = { allowed: 0, asked: 0 };
    for (const s of ledger.allowed) c[s.agent_id].allowed++;
    for (const s of ledger.asked) c[s.agent_id].asked++;
    return c;
  }, [agents, ledger]);
  return (
    <Panel title="The shared budget" intro={`Every allowed action is charged to one budget for the whole fleet, and to the agent that took it. The window is ${sessions.window_minutes ?? "—"} minutes.`}>
      <div className="gauge-stats">
        <Stat label="spent" value={fmt.price(ledger.fleetSpent)} sub={`of ${FLEET_BUDGET}`} />
        <Stat label="remaining" value={fmt.price(left)} tone={left < AGENT_CAP ? "var(--ask)" : "var(--allow)"} sub={fmt.pct(left / FLEET_BUDGET)} />
        <Stat label="used" value={fmt.pct(ledger.fleetSpent / FLEET_BUDGET)} sub={`agent cap ${AGENT_CAP}`} />
        <Stat label="asks" value={ledger.asked.length} tone={ledger.asked.length ? "var(--ask)" : undefined} sub={`${ledger.denied.length} denied`} />
      </div>
      <div className="meter gauge-bar" aria-label={`Fleet spend ${ledger.fleetSpent} of ${FLEET_BUDGET}`}>
        {agents.map((a) => (
          <span
            key={a}
            title={`${a}: ${fmt.price(ledger.agentSpent[a] ?? 0)}`}
            style={{ width: `${((ledger.agentSpent[a] ?? 0) / FLEET_BUDGET) * 100}%`, background: agentColor(a) }}
          />
        ))}
        <span className="tick" style={{ left: `${(AGENT_CAP / FLEET_BUDGET) * 100}%` }} title={`agent cap ${AGENT_CAP}`} />
      </div>
      <div className="gauge-scale mono faint">
        <span>0</span>
        <span style={{ left: `${(AGENT_CAP / FLEET_BUDGET) * 100}%` }}>cap {AGENT_CAP}</span>
        <span>{FLEET_BUDGET}</span>
      </div>
      <table className="grid gauge-table">
        <thead>
          <tr>
            <th>agent</th>
            <th className="num">spent</th>
            <th className="num">cap</th>
            <th className="num">left</th>
            <th className="num">% cap</th>
            <th className="num">% fleet</th>
            <th className="num">allow</th>
            <th className="num">ask</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => {
            const sp = ledger.agentSpent[a] ?? 0;
            return (
              <tr key={a}>
                <td><AgentTag agent={a} rogue={session.kind === "rogue_composed" && a === session.rogue_agent} /></td>
                <td className="num">{fmt.price(sp)}</td>
                <td className="num faint">{AGENT_CAP}</td>
                <td className="num">{fmt.price(AGENT_CAP - sp)}</td>
                <td className="num">
                  <span className="pctbar"><span style={{ width: `${(sp / AGENT_CAP) * 100}%`, background: agentColor(a) }} /></span>
                  {fmt.pct(sp / AGENT_CAP)}
                </td>
                <td className="num">{fmt.pct(ledger.fleetSpent ? sp / ledger.fleetSpent : 0, 0)}</td>
                <td className="num">{counts[a].allowed}</td>
                <td className="num" style={{ color: counts[a].asked ? "var(--ask)" : undefined }}>{counts[a].asked}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

// --- escalation rule ---

function EscalationState() {
  const { ledger, select } = useReplay();
  const n = ledger.consequential.length;
  const state = ledger.triggered ? "Yes, the next consequential action asks" : `Not yet: ${n} of 2`;
  return (
    <Panel
      title="Has the fleet done enough to need a person?"
      right={<span className={`esc-state mono ${ledger.triggered ? "on" : n === 1 ? "warm" : ""}`}>{state}</span>}
    >
      <div className="esc-explain faint">
        Once the window already holds two-plus consequential actions, the next consequential action asks.
        Consequential = L4+ with one agent in the window, L3+ with two or more.
      </div>
      <div className="esc-row">
        <div className="esc-slots" aria-label={`${n} of 2 consequential actions`}>
          {[0, 1].map((k) => (
            <span key={k} className={`esc-slot ${k < n ? "full" : ""}`} />
          ))}
          {n > 2 && <span className="mono esc-more">+{n - 2}</span>}
        </div>
        <Stat label="threshold" value={<span><Level level={ledger.threshold} />+</span>} sub={`${ledger.windowAgents.length} agent(s) in window`} />
        <Stat label="consequential" value={n} tone={ledger.triggered ? "var(--ask)" : undefined} sub="in window" />
        <Stat label="asked by rule" value={ledger.asked.filter((s) => s.decision.rule === "escalation").length} sub="so far" />
      </div>
      <div className="esc-agents mono">
        <span className="label">window agents</span>{" "}
        {ledger.windowAgents.length ? ledger.windowAgents.map((a) => <AgentTag key={a} agent={a} />) : <span className="faint">none</span>}
      </div>
      <table className="grid">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>agent</th>
            <th>lvl</th>
            <th>command</th>
            <th className="num">price</th>
          </tr>
        </thead>
        <tbody>
          {ledger.consequential.map((s) => (
            <tr key={s.i} onClick={() => select(s.i)} style={{ cursor: "pointer" }}>
              <td className="num faint">{s.i}</td>
              <td><AgentTag agent={s.agent_id} /></td>
              <td><Level level={s.decision.level} /></td>
              <td className="esc-cmd"><StepCommand step={s} maxLines={1} /></td>
              <td className="num">{fmt.price(s.decision.price)}</td>
            </tr>
          ))}
          {n === 0 && (
            <tr>
              <td colSpan={5} className="faint">No consequential actions allowed in this window yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </Panel>
  );
}

// --- event log ---

function EventLog({ steps }: { steps: StreamStep[] }) {
  const { select, selected } = useReplay();
  const rows = [...steps].reverse();
  return (
    <Panel title={`Every step so far (${steps.filter(isGated).length} priced)`} collapsible bodyClass="log-body">
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
            <th>rule</th>
            <th className="num">fleet after</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const d = s.decision;
            return (
              <tr key={s.i} onClick={() => select(s.i)} className={`${selected === s.i ? "log-sel" : ""} ${d?.decision === "ask" ? "log-ask" : ""}`}>
                <td className="num faint">{s.i}</td>
                <td><AgentTag agent={s.agent_id} /></td>
                <td>{d ? <Level level={d.level} /> : <span className="faint mono">—</span>}</td>
                <td className="log-cmd"><StepCommand step={s} maxLines={1} /></td>
                <td>{d ? <Cell cell={d.cell} /> : <span className="faint mono">{s.note || "skip"}</span>}</td>
                <td className="num">{d ? fmt.price(d.price) : "0"}</td>
                <td><VerdictTag verdict={d?.decision ?? null} /></td>
                <td className="mono">{d?.rule ?? ""}</td>
                <td className="num">{d ? fmt.price(d.fleet_after) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

// --- detail panel ---

function Detail({ step, onClose }: { step: StreamStep | null; onClose: () => void }) {
  const { session } = useReplay();
  if (!step) {
    return (
      <Panel title="Action detail">
        <div className="faint">Click a chip or log row to inspect its Decision: full command, cell, price, rule, reason and suggestion.</div>
      </Panel>
    );
  }
  const d = step.decision;
  const w = step.window;
  const rogue = session.kind === "rogue_composed" && step.agent_id === session.rogue_agent;
  return (
    <Panel
      title={<>Why step #{step.i} was {d ? (d.decision === "allow" ? "allowed" : d.decision === "ask" ? "held for a person" : "denied") : "not priced"}</>}
      right={<button className="btn" onClick={onClose} aria-label="Close detail">✕</button>}
      bodyClass="detail-body"
    >
      <div className="detail-verdict">
        {d ? <Level level={d.level} /> : null}
        <VerdictTag verdict={d?.decision ?? null} />
        {d && <span className="mono detail-rule">rule: {d.rule}</span>}
        <span className="spacer" />
        {d && <span className="mono detail-price">{fmt.price(d.price)}u</span>}
      </div>

      {d && (
        <div className={`detail-reason v-${d.decision}`}>
          <div className="label">reason</div>
          <div>{d.reason}</div>
        </div>
      )}
      {d?.suggestion && (
        <div className="detail-suggest">
          <div className="label">suggestion · what would be allowed</div>
          <div>{d.suggestion}</div>
        </div>
      )}

      <div className="detail-section">
        <div className="label">command</div>
        <div className="detail-cmd">
          <StepCommand step={step} />
        </div>
        {step.command && step.command.length > 0 && step.argv.length > 0 && (
          <>
            <div className="label" style={{ marginTop: 6 }}>argv ({step.argv.length})</div>
            <div className="detail-argv">
              {step.argv.map((a, k) => (
                <span key={k} className="argv-tok mono">
                  <span className="faint">{k}</span>
                  <Command text={a} />
                </span>
              ))}
            </div>
          </>
        )}
      </div>

      <dl className="detail-kv mono">
        <dt>agent</dt>
        <dd><AgentTag agent={step.agent_id} rogue={rogue} /></dd>
        <dt>tool</dt>
        <dd>{step.tool}</dd>
        <dt>action</dt>
        <dd>{step.action}</dd>
        <dt>targets</dt>
        <dd>{step.targets.length ? step.targets.join(", ") : <span className="faint">none</span>}</dd>
        <dt>trajectory</dt>
        <dd>{step.trajectory} · step {step.step}</dd>
        {d && (
          <>
            <dt>level</dt>
            <dd><Level level={d.level} /> {pricing.level_names[String(d.level)]}</dd>
            <dt>cell</dt>
            <dd>{d.cell}</dd>
            <dt>price</dt>
            <dd>{fmt.price(d.price)} {d.decision !== "allow" && <span className="faint">(not charged: {d.decision})</span>}</dd>
            <dt>fleet after</dt>
            <dd>{fmt.price(d.fleet_after)} / {FLEET_BUDGET}</dd>
            <dt>agent after</dt>
            <dd>{fmt.price(d.agent_after)} / {AGENT_CAP}</dd>
          </>
        )}
        <dt>gated</dt>
        <dd>{step.gated ? "yes" : `no · ${step.note || "skip"}`}</dd>
        <dt>unknown-det.</dt>
        <dd style={{ color: step.unknown_determined ? "var(--ask)" : undefined }}>
          {step.unknown_determined ? "yes · level from the unknown default" : "no"}
        </dd>
        {step.at_or_after_divergence && (
          <>
            <dt>divergence</dt>
            <dd style={{ color: "var(--deny)" }}>rogue agent, at/after step {session.rogue_divergence}</dd>
          </>
        )}
      </dl>

      {w && (
        <div className="detail-section">
          <div className="label">window at decision</div>
          <dl className="detail-kv mono">
            <dt>agents</dt>
            <dd>{w.agents.join(", ")}</dd>
            <dt>threshold</dt>
            <dd><Level level={w.threshold} />+</dd>
            <dt>consequential</dt>
            <dd>{w.consequential} {w.consequential >= 2 ? <span style={{ color: "var(--ask)" }}>(triggered)</span> : <span className="faint">of 2</span>}</dd>
            <dt>fleet before</dt>
            <dd>{fmt.price(w.fleet_spent_before)}</dd>
            <dt>agent before</dt>
            <dd>{fmt.price(w.agent_spent_before)}</dd>
          </dl>
        </div>
      )}

      {step.thought && (
        <div className="detail-section">
          <div className="label">StepShield thought (original trajectory text, not Kosha output)</div>
          <div className="detail-thought">{step.thought}</div>
        </div>
      )}
    </Panel>
  );
}
