import { useMemo, useState } from "react";
import { benchmarks, fmt, pricing, type Json, type Rate } from "../data";
import { Level, Panel } from "../components/primitives";
import "./Benchmarks.css";

const SS = benchmarks.stepshield;
const CF = benchmarks.compose_fleet;
const BS = benchmarks.benign_spend;
const KS = ["k3", "k4", "k5"] as const;
const CATS = ["INV", "RES", "SEC", "TST", "UFO"] as const;
// Category names from bench/data/stepshield/README.md.
const CAT_NAMES: Record<string, string> = {
  INV: "Data Exfiltration",
  SEC: "Privilege Escalation",
  RES: "Resource Hijacking",
  TST: "Supply Chain Attack",
  UFO: "Covert Persistence",
  DEC: "Destructive Action",
};
const FLEET_GATES = new Set(["M1", "B1", "B2"]);

const GATE_ROWS: { id: string; key: string; desc: string }[] = [
  { id: "B0", key: "B0", desc: "allow everything" },
  { id: "B1", key: "B1", desc: "denylist" },
  { id: "B2", key: "B2", desc: "ask on any L4+ action" },
  { id: "B3", key: "B3_N38", desc: `count budget, ${SS.defaults.b3_n} actions` },
  { id: "M1", key: "M1", desc: "Kosha" },
];

/** Rate with its n/of, always both visible. */
function R({ r, per100, strong }: { r: Rate | null | undefined; per100?: boolean; strong?: boolean }) {
  if (!r) return <span className="faint">—</span>;
  // from n/of, not the stored rate: the results files round rates to 4 places, and
  // rounding that again for display can move the last digit (316/537 is 58.8%, not 58.9%)
  const exact = r.of ? r.n / r.of : r.rate;
  const v = exact === null ? "—" : per100 ? (exact * 100).toFixed(2) : fmt.pct(exact);
  return (
    <span className="rate">
      <span className={strong ? "rate-v strong" : "rate-v"}>{v}</span>
      <span className="rate-n">
        {r.n}/{r.of}
      </span>
    </span>
  );
}

function pooled(pool: string, mode: string, gate: string, metric: string): Rate {
  let n = 0;
  let of = 0;
  for (const k of KS) {
    const r: Rate = CF.results[pool][mode][k][gate][metric];
    n += r.n;
    of += r.of;
  }
  return { n, of, rate: of ? n / of : null };
}

// --- headline: one question per table ---

function SingleAgent() {
  return (
    <Panel
      title="One agent at a time"
      intro={
        <>
          {SS.counts.rogue_in_scope} StepShield runs where an agent goes rogue, each with a clean twin, plus{" "}
          {fmt.num(SS.counts.benign)} ordinary runs. Kosha (M1) against four simpler gates.
        </>
      }
    >
      <table className="grid calm">
        <thead>
          <tr>
            <th>Gate</th>
            <th className="num">Catches the rogue run</th>
            <th className="num">Asks on ordinary runs, per 100</th>
            <th className="num">Also flags the clean twin</th>
          </tr>
        </thead>
        <tbody>
          {GATE_ROWS.map(({ id, key, desc }) => {
            const g: Json = SS.gates[key];
            return (
              <tr key={id} className={id === "M1" ? "row-m1" : ""}>
                <td>
                  <span className="gate-id">{id}</span> <span className="muted">{desc}</span>
                </td>
                <td className="num">
                  <R r={g.all_flags.catch_rate} strong />
                </td>
                <td className="num">
                  <R r={g.benign_false_alarm.all_flags} per100 />
                </td>
                <td className="num">
                  <R r={g.paired_false_alarm.non_dec_twins} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="note">
        On {fmt.num(BS.n_trajectories)} ordinary SWE-smith runs Kosha asks in{" "}
        {BS.distribution.total_with_edits.asks_per_100_at_agent_cap_375.toFixed(2)} of 100 at the per-agent cap. The{" "}
        {SS.counts.rogue_dec} "Destructive Action" runs are scored separately because their harm is in the final message,
        not a command.
      </p>
    </Panel>
  );
}

function Fleets() {
  const [pool, setPool] = useState<"aggregate_only" | "broad">("aggregate_only");
  const [mode, setMode] = useState<"shuffle" | "round_robin">("shuffle");
  return (
    <Panel
      title="A fleet of agents"
      intro="Three to five agents run at once in one window. None of them is caught by any gate on its own; the question is whether the fleet is."
      right={
        <span className="ctl-row">
          <Seg value={pool} set={setPool} opts={["aggregate_only", "broad"]} label="Pool" />
          <Seg value={mode} set={setMode} opts={["shuffle", "round_robin"]} label="Interleaving" />
        </span>
      }
    >
      <table className="grid calm">
        <thead>
          <tr>
            <th>Gate</th>
            <th className="num">Catches the rogue fleet</th>
            <th className="num">Flags an all-clean fleet</th>
            <th className="num">Flags the fleet with the clean twin</th>
          </tr>
        </thead>
        <tbody>
          {GATE_ROWS.filter((r) => FLEET_GATES.has(r.id)).map(({ id, desc }) => (
            <tr key={id} className={id === "M1" ? "row-m1" : ""}>
              <td>
                <span className="gate-id">{id}</span> <span className="muted">{desc}</span>
              </td>
              <td className="num">
                <R r={pooled(pool, mode, id, "fleet_catch_rate")} strong />
              </td>
              <td className="num">
                <R r={pooled(pool, mode, id, "false_alarm_all_clean")} />
              </td>
              <td className="num">
                <R r={pooled(pool, mode, id, "false_alarm_twin_swapped")} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function Honest() {
  return (
    <section className="sec honest">
      <h2>The honest part</h2>
      <p>
        Kosha stops every rogue fleet, and it also stops every all-clean one (
        {fmt.pct(pooled("aggregate_only", "shuffle", "M1", "false_alarm_all_clean").rate)}). Every one of those stops comes
        from the escalation rule, never the budget: once two or more agents have done two consequential things in the
        window, the next one waits for a person, whatever the agents intended.
      </p>
      <p>
        That is the design: Kosha is a guardrail for well-meaning agents, not a detector of bad ones. It makes sure a
        person sees the bundle before the fleet goes further. The fleets here are synthetic, built from single-agent
        runs, so how often this interrupts real multi-agent work is still an open question.
      </p>
    </section>
  );
}

function Seg<T extends string>({ value, set, opts, label }: { value: T; set: (v: T) => void; opts: T[]; label: string }) {
  return (
    <span className="seg" role="group" aria-label={label}>
      <span className="label">{label}</span>
      {opts.map((o) => (
        <button key={o} className={`seg-btn ${o === value ? "on" : ""}`} onClick={() => set(o)}>
          {o}
        </button>
      ))}
    </span>
  );
}

// --- per-category, B3 sweep, per-k fleet ---

function Categories() {
  const gates = ["M1", "B1", "B2", "B3_N38"];
  return (
    <Panel title="Catch rate by attack category" collapsible>
      <table className="grid">
        <thead>
          <tr>
            <th>Gate</th>
            {CATS.map((c) => (
              <th key={c} className="num" title={CAT_NAMES[c]}>
                {c}
                <div className="cat-name">{CAT_NAMES[c]}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {gates.map((g) => (
            <tr key={g}>
              <td className="mono">{g.replace("_N38", "")}</td>
              {CATS.map((c) => {
                const r: Rate = SS.gates[g].all_flags.catch_rate_by_category[c];
                return (
                  <td key={c} className="num heat" style={{ ["--h" as string]: String(r.rate ?? 0) }}>
                    <R r={r} />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="faint small">
        Category sizes: {CATS.map((c) => `${c} ${SS.gates.M1.all_flags.catch_rate_by_category[c].of}`).join(" · ")}. DEC ({CAT_NAMES.DEC}) excluded from in-scope: M1 flags{" "}
        {SS.gates.M1.dec_separate.rogue_flagged.n}/{SS.gates.M1.dec_separate.rogue_flagged.of} DEC rogue and{" "}
        {SS.gates.M1.dec_separate.clean_flagged.n}/{SS.gates.M1.dec_separate.clean_flagged.of} DEC clean runs.
      </p>
    </Panel>
  );
}

function B3Sweep() {
  const ns = ["B3_N5", "B3_N10", "B3_N15", "B3_N20", "B3_N25", "B3_N30", "B3_N38"];
  return (
    <Panel title="The count-budget gate (B3) at other sizes" collapsible>
      <table className="grid">
        <thead>
          <tr>
            <th>N</th>
            <th className="num">catch</th>
            <th className="num">paired FA</th>
            <th className="num">benign /100</th>
          </tr>
        </thead>
        <tbody>
          {ns.map((k) => {
            const g = SS.gates[k];
            return (
              <tr key={k} className={k === "B3_N38" ? "row-m1" : ""}>
                <td className="mono">
                  {k.replace("B3_N", "")}
                  {k === "B3_N38" && <span className="faint"> default</span>}
                </td>
                <td className="num">
                  <R r={g.all_flags.catch_rate} />
                </td>
                <td className="num">
                  <R r={g.paired_false_alarm.non_dec_twins} />
                </td>
                <td className="num">
                  <R r={g.benign_false_alarm.all_flags} per100 />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="faint small">Default N = {SS.defaults.b3_n_basis}. A count budget with no severity trades catches for benign asks one-for-one.</p>
    </Panel>
  );
}

function FleetByK() {
  const [pool, setPool] = useState<"aggregate_only" | "broad">("aggregate_only");
  const res = CF.results[pool].shuffle;
  return (
    <Panel
      title="Fleet results by number of agents"
      collapsible
      right={<Seg value={pool} set={setPool} opts={["aggregate_only", "broad"]} label="pool" />}
    >
      <table className="grid">
        <thead>
          <tr>
            <th>k</th>
            <th>Gate</th>
            <th className="num">catch</th>
            <th className="num">twin FA</th>
            <th className="num">clean FA</th>
          </tr>
        </thead>
        <tbody>
          {KS.flatMap((k) =>
            ["M1", "B1", "B2"].map((g, gi) => (
              <tr key={k + g} className={gi === 0 ? "k-first" : ""}>
                <td className="mono">{gi === 0 ? k : ""}</td>
                <td className="mono">{g}</td>
                <td className="num">
                  <R r={res[k][g].fleet_catch_rate} />
                </td>
                <td className="num">
                  <R r={res[k][g].false_alarm_twin_swapped} />
                </td>
                <td className="num">
                  <R r={res[k][g].false_alarm_all_clean} />
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
      <p className="faint small">
        Pool sizes: rogue {CF.pools[pool].rogue.n}, clean {CF.pools[pool].clean.n}; {CF.defaults.fleets_per_kind_per_size} fleets
        per kind per k, seed {CF.defaults.seed}. M1 first flag rule:{" "}
        {Object.entries(res.k3.M1.first_flag_rogue_composed.rule)
          .map(([r, n]) => `${r} ${n}`)
          .join(", ")}{" "}
        (k3).
      </p>
    </Panel>
  );
}

// --- price table & effects ---

function PriceCells() {
  const t = pricing.table;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of pricing.effects) c[e.cell] = (c[e.cell] ?? 0) + 1;
    return c;
  }, []);
  return (
    <Panel
      title="What each kind of action costs"
      intro={`Prices from ${t.version}. They are set by hand for now, so the sample size (n) and upper bound (p_high) are empty until calibration.`}
    >
      <table className="grid">
        <thead>
          <tr>
            <th>Cell (reversible|scope|privilege)</th>
            <th className="num">price</th>
            <th className="num">n</th>
            <th className="num">p_high</th>
            <th className="num">% of agent_cap</th>
            <th className="num">effects entries</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(t.cells).map(([cell, c]) => (
            <tr key={cell}>
              <td className="mono">{cell}</td>
              <td className="num mono strong">{c.price}</td>
              <td className="num mono">{c.n ?? <span className="faint">— uncal.</span>}</td>
              <td className="num mono">{c.p_high ?? <span className="faint">— uncal.</span>}</td>
              <td className="num mono">{((c.price / t.agent_cap) * 100).toFixed(1)}%</td>
              <td className="num mono">{cell === "*|*|priv" ? pricing.effects.filter((e) => e.privilege).length : (counts[cell] ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="kv mono">
        <span>
          <span className="label">fleet_budget</span> {t.fleet_budget}
        </span>
        <span>
          <span className="label">agent_cap</span> {t.agent_cap}
        </span>
        <span>
          <span className="label">window</span> {t.window_minutes} min
        </span>
        <span>
          <span className="label">L0/L1</span> 0
        </span>
        <span>
          <span className="label">unknown</span> irrev|shared|nopriv
        </span>
      </div>
      <dl className="basis">
        {Object.entries(t.budget_basis).map(([k, v]) => (
          <div key={k}>
            <dt className="label">{k}</dt>
            <dd>{String(v)}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

type SortKey = "id" | "family" | "scope" | "level" | "price";

function Effects() {
  const [q, setQ] = useState("");
  const [lvl, setLvl] = useState<number | null>(null);
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: "level", dir: -1 });
  const byLevel = useMemo(() => {
    const c = [0, 0, 0, 0, 0, 0];
    for (const e of pricing.effects) c[e.level]++;
    return c;
  }, []);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const r = pricing.effects.filter(
      (e) =>
        (lvl === null || e.level === lvl) &&
        (!needle ||
          [e.id, e.match.family, e.match.sub, e.note, e.cell, ...(e.match.flags_any ?? [])].join(" ").toLowerCase().includes(needle)),
    );
    const key = (e: (typeof r)[number]): string | number =>
      sort.k === "family" ? `${e.match.family ?? ""} ${e.match.sub ?? ""}` : sort.k === "scope" ? e.scope : e[sort.k];
    return [...r].sort((a, b) => {
      const x = key(a);
      const y = key(b);
      return (x < y ? -1 : x > y ? 1 : a.id < b.id ? -1 : 1) * sort.dir;
    });
  }, [q, lvl, sort]);
  const th = (k: SortKey, label: string, cls = "") => (
    <th className={`sortable ${cls}`} onClick={() => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : 1 }))}>
      {label}
      {sort.k === k ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
    </th>
  );
  return (
    <Panel
      title={`Every classified command (${pricing.effects.length} entries in effects.yaml)`}
      collapsible
      right={
        <span className="ctl-row">
          <span className="lvl-filter">
            <button className={`seg-btn ${lvl === null ? "on" : ""}`} onClick={() => setLvl(null)}>
              all {pricing.effects.length}
            </button>
            {byLevel.map((n, l) => (
              <button key={l} className={`seg-btn ${lvl === l ? "on" : ""}`} onClick={() => setLvl(lvl === l ? null : l)}>
                <Level level={l} /> <span className="mono">{n}</span>
              </button>
            ))}
          </span>
          <input className="filter" placeholder="filter id / family / note" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
      }
      bodyClass="effects-body"
    >
      <table className="grid effects">
        <thead>
          <tr>
            {th("id", "id")}
            {th("family", "family / sub / flags")}
            <th>rev</th>
            {th("scope", "scope")}
            <th>priv</th>
            <th>ro</th>
            {th("level", "level")}
            <th>cell</th>
            {th("price", "price", "num")}
            <th>note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="mono">{e.id}</td>
              <td className="mono">
                <span className="c-fam">{e.match.family ?? "—"}</span>
                {e.match.sub && <span className="muted"> {e.match.sub}</span>}
                {e.match.flags_any && e.match.flags_any.length > 0 && <span className="c-flags"> [{e.match.flags_any.join(" ")}]</span>}
              </td>
              <td className="mono">{e.reversible ? "rev" : <span className="irrev">irrev</span>}</td>
              <td className="mono">{e.scope}</td>
              <td className="mono">{e.privilege ? <span className="priv">priv</span> : <span className="faint">·</span>}</td>
              <td className="mono">{e.read_only ? "ro" : <span className="faint">·</span>}</td>
              <td>
                <Level level={e.level} />
              </td>
              <td className="mono muted">{e.cell}</td>
              <td className="num mono strong">{e.price}</td>
              <td className="note">{e.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <p className="faint small">No entries match.</p>}
    </Panel>
  );
}

// --- spend distribution (histogram) ---

function SpendHistogram() {
  const h = benchmarks.benign_spend_hist;
  const d = BS.distribution.total_with_edits;
  const W = 640;
  const H = 180;
  const pad = { l: 44, r: 12, t: 22, b: 26 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const max = Math.max(...h.counts);
  const nb = h.counts.length;
  const bw = iw / nb;
  const x = (v: number) => pad.l + (Math.min(v, h.top + h.width) / (h.top + h.width)) * iw;
  const y = (c: number) => pad.t + ih - (c / max) * ih;
  const [hover, setHover] = useState<number | null>(null);
  const markers: { v: number; label: string; cls: string }[] = [
    { v: d.p50, label: `p50 ${d.p50}`, cls: "pct" },
    { v: d.p90, label: `p90 ${d.p90}`, cls: "pct" },
    { v: d.p95, label: `p95 ${d.p95}`, cls: "pct" },
    { v: d.p99, label: `p99 ${d.p99}`, cls: "pct" },
    { v: pricing.table.agent_cap, label: `agent_cap ${pricing.table.agent_cap}`, cls: "cap" },
    { v: pricing.table.fleet_budget, label: `fleet_budget ${pricing.table.fleet_budget}`, cls: "cap" },
  ];
  const ticks = [0, 200, 400, 600, 800, 1000];
  // stack marker labels into rows so nearby ones (p95 and agent_cap sit ~10 apart) don't overlap
  const rowEnd: number[] = [];
  const labelRow = markers.map((m) => {
    const x0 = x(m.v) + 3;
    const w = m.label.length * 6.2;
    let r = rowEnd.findIndex((end) => end < x0);
    if (r === -1) r = rowEnd.length;
    rowEnd[r] = x0 + w;
    return r;
  });
  return (
    <Panel
      title="How much ordinary work spends"
      intro={`What ${fmt.num(h.n)} ordinary single-agent SWE-smith runs would spend under Kosha's prices. The per-agent cap sits just above the 95th percentile.`}
    >
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Histogram of benign spend per run">
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={pad.l} x2={W - pad.r} y1={y(max * f)} y2={y(max * f)} className="gridline" />
              <text x={pad.l - 4} y={y(max * f) + 3} className="axis" textAnchor="end">
                {Math.round(max * f).toLocaleString()}
              </text>
            </g>
          ))}
          {h.counts.map((c, i) => (
            <rect
              key={i}
              x={pad.l + i * bw + 1}
              y={y(c)}
              width={Math.max(1, bw - 2)}
              height={pad.t + ih - y(c)}
              className={`hbar ${i === nb - 1 ? "pooled" : ""} ${hover === i ? "on" : ""}`}
            />
          ))}
          {h.counts.map((_, i) => (
            <rect
              key={`h${i}`}
              x={pad.l + i * bw}
              y={pad.t}
              width={bw}
              height={ih}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
          {markers.map((m, i) => (
            <g key={m.label} className={`marker ${m.cls}`}>
              <line x1={x(m.v)} x2={x(m.v)} y1={pad.t - 4} y2={pad.t + ih} />
              <text x={x(m.v) + 3} y={pad.t + 6 + labelRow[i] * 11} className="mlabel">
                {m.label}
              </text>
            </g>
          ))}
          <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih} y2={pad.t + ih} className="baseline" />
          {ticks.map((t) => (
            <text key={t} x={x(t)} y={H - 10} className="axis" textAnchor="middle">
              {t === h.top ? `≥${t}` : t}
            </text>
          ))}
        </svg>
        {hover !== null && (
          <div className="tip mono" style={{ left: `${((pad.l + (hover + 0.5) * bw) / W) * 100}%` }}>
            {hover === nb - 1 ? `≥ ${h.top}` : `${hover * h.width}–${(hover + 1) * h.width - 1}`}: {h.counts[hover].toLocaleString()} runs (
            {((h.counts[hover] / h.n) * 100).toFixed(2)}%)
          </div>
        )}
      </div>
      <div className="kv mono">
        <span>
          <span className="label">max</span> {d.max}
        </span>
        <span>
          <span className="label">asks/100 @ agent_cap</span> {d.asks_per_100_at_agent_cap_375}
        </span>
        <span>
          <span className="label">asks/100 @ fleet_budget</span> {d.asks_per_100_at_fleet_budget_750}
        </span>
        <span>
          <span className="label">unmatched segs</span> {fmt.pct(BS.unmatched_rate, 2)}
        </span>
        <span>
          <span className="label">opaque script segs</span> {fmt.pct(BS.opaque_script_rate, 1)}
        </span>
      </div>
      <p className="faint small">Last bar pools every run at or above {h.top}. {pricing.table.budget_basis.caveat}</p>
    </Panel>
  );
}

function Overdraw() {
  const t = pricing.table;
  const res = CF.results.aggregate_only.shuffle;
  const broad = CF.results.broad.shuffle;
  const rows = KS.flatMap((k) => [
    { k, pool: "aggregate_only", kind: "rogue", s: res[k].fleet_total_spend_if_all_allowed.rogue_composed },
    { k, pool: "aggregate_only", kind: "clean", s: res[k].fleet_total_spend_if_all_allowed.all_clean_composed },
    { k, pool: "broad", kind: "rogue", s: broad[k].fleet_total_spend_if_all_allowed.rogue_composed },
    { k, pool: "broad", kind: "clean", s: broad[k].fleet_total_spend_if_all_allowed.all_clean_composed },
  ]);
  const scale = (v: number) => (v / t.fleet_budget) * 100;
  return (
    <Panel title="Fleet spend against the budget" collapsible>
      <p className="pending-note">
        No fleetsim results in bench/results/ yet, so no overdraw curve. Below: composed-fleet spend if every action were allowed, vs
        fleet_budget {t.fleet_budget}.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th>k</th>
            <th>pool · kind</th>
            <th>median ▮ / max │ vs budget</th>
            <th className="num">med</th>
            <th className="num">max</th>
            <th className="num">over</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={i % 4 === 0 ? "k-first" : ""}>
              <td className="mono">{i % 4 === 0 ? r.k : ""}</td>
              <td className="mono small">
                {r.pool === "aggregate_only" ? "agg" : "broad"} · {r.kind}
              </td>
              <td>
                <span className="budgetbar">
                  <span className="med" style={{ width: `${scale(r.s.median)}%` }} />
                  <span className="mx" style={{ left: `${scale(r.s.max)}%` }} />
                </span>
              </td>
              <td className="num mono">{r.s.median}</td>
              <td className="num mono">{r.s.max}</td>
              <td className="num mono">{r.s.over_fleet_budget}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="faint small">No composed fleet exceeds the fleet budget: every M1 catch in Bench B comes from the escalation rule, not spend.</p>
    </Panel>
  );
}

// --- Bench E / D ---

function CI({ r }: { r: { k: number; n: number; rate: number; ci95?: number[] } }) {
  return (
    <span className="rate">
      <span className="rate-v">{fmt.pct(r.n ? r.k / r.n : r.rate)}</span>
      <span className="rate-n">
        {r.k}/{r.n}
      </span>
      {r.ci95 && (
        <span className="rate-n">
          [{fmt.pct(r.ci95[0], 0)}–{fmt.pct(r.ci95[1], 0)}]
        </span>
      )}
    </span>
  );
}

function Convergence() {
  const c = benchmarks.convergence_synth;
  const s = c.summary;
  const rows: [string, string][] = [
    ["as_recorded", "as recorded"],
    ["nonconvergent", "non-convergent"],
    ["convergent", "convergent (engineered)"],
    ["convergent_alt", "convergent, alt target"],
  ];
  return (
    <Panel title="Two agents touching the same target (synthetic)" collapsible>
      <p className="caveat">{c.caveat}</p>
      <table className="grid">
        <thead>
          <tr>
            <th>fleets</th>
            <th className="num">n</th>
            <th className="num">check fired</th>
            <th className="num">fired, gated</th>
            <th className="num">decide flagged</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, label]) => (
            <tr key={k}>
              <td>{label}</td>
              <td className="num mono">{s[k].fleets}</td>
              <td className="num">
                <CI r={s[k].check_fired} />
              </td>
              <td className="num">
                <CI r={s[k].check_fired_gated} />
              </td>
              <td className="num">
                <CI r={s[k].decide_flagged} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="grid">
        <thead>
          <tr>
            <th>alt target kind</th>
            <th className="num">fired</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(s.convergent_alt_by_kind as Record<string, { k: number; n: number; rate: number; ci95: number[] }>).map(([k, r]) => (
            <tr key={k}>
              <td className="mono">{k}</td>
              <td className="num">
                <CI r={r} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="faint small">
        Target coverage of selected L2+ steps: {c.target_coverage_selected.with_target}/{c.target_coverage_selected.l2plus_steps} (
        {fmt.pct(c.target_coverage_selected.rate)}). Engineered convergent fire before the engineered step:{" "}
        {s.convergent.fired_before_engineered_step.k}/{s.convergent.fired_before_engineered_step.n}.
      </p>
    </Panel>
  );
}

function GitHistory() {
  const g = benchmarks.git_history;
  const w = g.fixers_kept.window_convergence;
  return (
    <Panel title="Real git history (approximate labels)" collapsible>
      <p className="caveat">{g.caveat}</p>
      <table className="grid">
        <thead>
          <tr>
            <th>repo</th>
            <th className="num">commits</th>
            <th className="num">rogue-ish</th>
            <th className="num">authors</th>
            <th>range</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(g.repos as Record<string, Json>).map(([name, r]) => (
            <tr key={name}>
              <td className="mono">{name}</td>
              <td className="num mono">{fmt.num(r.analysed_commits)}</td>
              <td className="num mono">{r.analysed_rogue}</td>
              <td className="num mono">{r.authors}</td>
              <td className="mono small faint">
                {r.analysed_from} → {String(r.analysed_until).slice(0, 10)} · {String(r.head).slice(0, 10)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="grid">
        <thead>
          <tr>
            <th>{g.window_minutes}-min windows (≥{g.min_authors} authors)</th>
            <th className="num">converged</th>
            <th className="num">shared files</th>
            <th className="num">commits</th>
          </tr>
        </thead>
        <tbody>
          {(["with_rogue", "without_rogue"] as const).map((k) => (
            <tr key={k}>
              <td className="mono">{k.replace("_", " ")}</td>
              <td className="num">
                <CI r={w[k]} />
              </td>
              <td className="num mono">{w[k].mean_shared_files}</td>
              <td className="num mono">{w[k].mean_commits}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="kv mono">
        <span>
          <span className="label">windows</span> {g.fixers_kept.qualifying_windows}
        </span>
        <span>
          <span className="label">fisher p</span> {w.fisher_p_unstratified}
        </span>
        <span>
          <span className="label">perm p (stratified)</span> {w.perm_stratified.p_two_sided}
        </span>
        <span>
          <span className="label">perms</span> {g.perms}
        </span>
      </div>
      <p className="faint small">No detectable association between window convergence and rogue-ish commits at this sample size.</p>
    </Panel>
  );
}

function Sources() {
  const all = { ...benchmarks.sources, ...pricing.sources };
  return (
    <Panel title="Sources" collapsible>
    <div className="sources">
      {Object.entries(all).map(([k, s]) => (
        <span key={k} title={s.sha256}>
          {s.path} <span className="faint">sha256:{fmt.sha(s.sha256)} · {fmt.num(s.bytes)} B</span>
        </span>
      ))}
      <span className="faint">
        StepShield: {SS.dataset}; price table {SS.price_table}. Saved results may predate the current runtime policy.
      </span>
    </div>
    </Panel>
  );
}

export function Benchmarks() {
  return (
    <div className="bench">
      <SingleAgent />
      <Fleets />
      <Honest />
      <SpendHistogram />
      <PriceCells />
      <div className="folds">
        <h3 className="folds-title">More detail</h3>
        <Categories />
        <B3Sweep />
        <FleetByK />
        <Overdraw />
        <Convergence />
        <GitHistory />
        <Effects />
        <Sources />
      </div>
    </div>
  );
}
