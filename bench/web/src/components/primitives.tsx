import type { ReactNode } from "react";
import { pricing, type Verdict } from "../data";

/** Severity badge: color AND the L-label, never color alone. */
export function Level({ level, title }: { level: number; title?: string }) {
  const name = pricing.level_names[String(level)] ?? `L${level}`;
  return (
    <span className={`lvl lvl-${level}`} title={title ?? name} aria-label={name}>
      L{level}
    </span>
  );
}

export function VerdictTag({ verdict }: { verdict: Verdict | null }) {
  return <span className={`verdict ${verdict ?? "none"}`}>{verdict ?? "not gated"}</span>;
}

export function Cell({ cell }: { cell: string }) {
  return <span className="mono muted">{cell}</span>;
}

/** A page section: heading, optional aside, content. Unboxed; sections are separated by
 * whitespace and one hairline. `collapsible` renders secondary detail closed by default. */
export function Panel({
  title,
  right,
  children,
  className,
  bodyClass = "sec-body",
  collapsible = false,
  intro,
}: {
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  collapsible?: boolean;
  intro?: ReactNode;
}) {
  const head = (
    <>
      <h2>{title}</h2>
      {right && <span className="sec-aside">{right}</span>}
    </>
  );
  if (collapsible) {
    return (
      <details className={`sec sec-fold ${className ?? ""}`}>
        <summary className="sec-head">{head}</summary>
        {intro && <p className="sec-intro">{intro}</p>}
        <div className={bodyClass}>{children}</div>
      </details>
    );
  }
  return (
    <section className={`sec ${className ?? ""}`}>
      <header className="sec-head">{head}</header>
      {intro && <p className="sec-intro">{intro}</p>}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

/** A labeled figure: small uppercase label over a mono number. */
export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
      <span className="label">{label}</span>
      <span className="mono" style={{ fontSize: "var(--t-fig)", fontWeight: 600, color: tone }}>
        {value}
      </span>
      {sub !== undefined && <span className="mono faint" style={{ fontSize: "var(--t-label)" }}>{sub}</span>}
    </div>
  );
}

export const AGENT_COLORS = ["var(--a1)", "var(--a2)", "var(--a3)", "var(--a4)", "var(--a5)"];

export function agentColor(agent: string): string {
  const n = Number(agent.split("-").pop());
  return AGENT_COLORS[(Number.isFinite(n) ? n - 1 : 0) % AGENT_COLORS.length];
}

export function AgentTag({ agent, rogue }: { agent: string; rogue?: boolean }) {
  return (
    <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--t-data)", whiteSpace: "nowrap" }}>
      <span style={{ width: 8, height: 8, background: agentColor(agent), display: "inline-block" }} />
      {agent}
      {rogue && (
        <span className="mono" style={{ color: "var(--deny)", fontSize: "var(--t-label)", fontWeight: 700 }}>
          ROGUE
        </span>
      )}
    </span>
  );
}
