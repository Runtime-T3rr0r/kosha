import benchmarksJson from "../fixtures/benchmarks.json";
import pricingJson from "../fixtures/pricing.json";
import sessionsJson from "../fixtures/sessions.json";
import type {
  BenchmarksFixture,
  Decision,
  PricingFixture,
  Session,
  SessionsFixture,
  StreamStep,
} from "./types";

export * from "./types";

export const benchmarks = benchmarksJson as unknown as BenchmarksFixture;
export const pricing = pricingJson as unknown as PricingFixture;
export const sessions = sessionsJson as unknown as SessionsFixture;

export const FLEET_BUDGET = sessions.fleet_budget;
export const AGENT_CAP = sessions.agent_cap;

/** Consequential at the window's threshold (policy.escalation_threshold): L4+ with one
 * agent in the window, L3+ with two or more. */
export const SINGLE_AGENT_CONSEQUENTIAL = 4;
export const CONSEQUENTIAL = 3;

export type Gated = StreamStep & { decision: Decision };

export const isGated = (s: StreamStep): s is Gated => s.decision !== null;

export function agentsOf(session: Session): string[] {
  return Object.keys(session.members).sort();
}

/** Ledger state after the first `cursor` steps of a session's stream, computed only
 * from the recorded decisions (allowed actions are charged, asks are not, as in
 * policy.decide and bench/compose_fleet.py). `windowStart` is the stream index where
 * the current window opened (moved by a local approve-and-reset); spend and the
 * escalation count only include allowed actions from there on. Asks and denies are
 * listed over the whole stream so older ones stay in the queue. */
export interface LedgerView {
  fleetSpent: number;
  agentSpent: Record<string, number>;
  allowed: Gated[];
  asked: Gated[];
  denied: Gated[];
  windowAgents: string[];
  threshold: number;
  consequential: Gated[];
  triggered: boolean;
  windowStart: number;
}

export function ledgerAt(session: Session, cursor: number, windowStart = 0): LedgerView {
  const seen = session.steps.slice(0, cursor).filter(isGated);
  const allowed = seen.filter((s) => s.decision.decision === "allow" && s.i >= windowStart);
  const agentSpent: Record<string, number> = Object.fromEntries(agentsOf(session).map((a) => [a, 0]));
  let fleetSpent = 0;
  for (const s of allowed) {
    fleetSpent += s.decision.price;
    agentSpent[s.agent_id] += s.decision.price;
  }
  const windowAgents = [...new Set(allowed.map((s) => s.agent_id))].sort();
  const threshold = windowAgents.length >= 2 ? CONSEQUENTIAL : SINGLE_AGENT_CONSEQUENTIAL;
  const consequential = allowed.filter((s) => s.decision.level >= threshold);
  return {
    fleetSpent,
    agentSpent,
    allowed,
    asked: seen.filter((s) => s.decision.decision === "ask"),
    denied: seen.filter((s) => s.decision.decision === "deny"),
    windowAgents,
    threshold,
    consequential,
    triggered: consequential.length >= 2,
    windowStart,
  };
}

/** One-line readable form of a step: the shell command, or tool + path for file tools. */
export function commandLine(s: StreamStep): string {
  if (s.command) return s.command;
  if (s.path) return `${s.action} ${s.path}`;
  return s.action;
}

export const fmt = {
  pct: (r: number | null | undefined, digits = 1) =>
    r === null || r === undefined ? "—" : `${(r * 100).toFixed(digits)}%`,
  num: (n: number | null | undefined, digits = 0) =>
    n === null || n === undefined ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }),
  price: (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1)),
  sha: (h: string) => h.slice(0, 10),
};
