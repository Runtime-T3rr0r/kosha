// Shapes of the fixtures written by export_fixtures.py. Field names follow
// kosha/pricing/policy.py (Decision) and bench/results/*.json; nothing here is
// added beyond what those files contain.

export type Verdict = "allow" | "ask" | "deny";

export interface Decision {
  decision: Verdict;
  reason: string;
  level: number;
  cell: string;
  price: number;
  fleet_after: number;
  agent_after: number;
  rule: string;
  suggestion: string | null;
}

export interface WindowAtDecision {
  agents: string[];
  consequential: number;
  threshold: number;
  fleet_spent_before: number;
  agent_spent_before: number;
}

export interface StreamStep {
  i: number;
  agent_id: string;
  trajectory: string;
  step: number;
  action: string;
  tool: string;
  command: string;
  argv: string[];
  path: string;
  targets: string[];
  gated: boolean;
  note: string;
  unknown_determined: boolean;
  is_rogue_agent: boolean;
  at_or_after_divergence: boolean;
  decision: Decision | null;
  window?: WindowAtDecision;
  thought: string;
}

export interface FirstFlag {
  stream_index: number;
  stream_len: number;
  agent: string;
  trajectory: string;
  step: number;
  action: string;
  level: number;
  cell: string;
  rule: string;
  decision: Verdict;
  is_rogue_agent: boolean;
  at_or_after_divergence: boolean;
  fleet_spent: number;
  agent_spent: number;
  price: number;
  window_agents: number;
  window_consequential: number;
  threshold: number;
}

export interface Session {
  id: string;
  label: string;
  pool: string;
  k: number;
  interleave: string;
  kind: "rogue_composed" | "twin_swapped" | "all_clean_composed";
  members: Record<string, string>;
  rogue_agent: string | null;
  rogue_divergence: number | null;
  stored_first_flag: FirstFlag | null;
  steps: StreamStep[];
}

export interface Source {
  path: string;
  sha256: string;
  bytes: number;
}

export interface SessionsFixture {
  sources: Record<string, Source>;
  synthetic: boolean;
  note: string;
  fleet_budget: number;
  agent_cap: number;
  window_minutes: number | null;
  sessions: Session[];
}

export interface PriceCell {
  price: number;
  n: number | null;
  p_high: number | null;
}

export interface PriceTable {
  version: string;
  method: string;
  unit_budget: number;
  cells: Record<string, PriceCell>;
  fleet_budget: number;
  agent_cap: number;
  budget_basis: Record<string, string | number>;
  window_minutes: number;
}

export interface EffectEntry {
  id: string;
  match: { family?: string; sub?: string; flags_any?: string[] };
  reversible: boolean;
  scope: string;
  privilege: boolean;
  read_only: boolean;
  note: string;
  level: number;
  cell: string;
  price: number;
}

export interface PricingFixture {
  sources: Record<string, Source>;
  table: PriceTable;
  level_names: Record<string, string>;
  effects: EffectEntry[];
}

export interface Rate {
  n: number;
  of: number;
  rate: number | null;
}

// bench/results/*.json summaries are deep and gate-keyed; views index into them
// with the key paths documented in those files, so they stay loosely typed here.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export interface BenchmarksFixture {
  sources: Record<string, Source>;
  stepshield: Json;
  compose_fleet: Json;
  benign_spend: Json;
  benign_spend_hist: { width: number; top: number; n: number; counts: number[] };
  convergence_synth: Json;
  git_history: Json;
  m2: { exists: false; path: string } | { exists: true; source: Source; table: PriceTable };
}
