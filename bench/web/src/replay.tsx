import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ledgerAt, sessions, type Gated, type LedgerView, type Session } from "./data";

/** Human resolution of an asked action, recorded in the browser only. The fixture
 * build has no koshad behind it, so nothing here reaches a ledger; the recorded
 * decisions in the stream stay as policy.decide produced them. */
export type Resolution =
  | { kind: "approve_once"; at: number }
  | { kind: "approve_reset"; at: number }
  | { kind: "deny"; at: number; note: string };

interface ReplayState {
  sessions: Session[];
  session: Session;
  setSessionId: (id: string) => void;
  /** Number of stream steps revealed so far (0..steps.length). */
  cursor: number;
  setCursor: (n: number) => void;
  playing: boolean;
  play: () => void;
  pause: () => void;
  restart: () => void;
  /** Milliseconds between steps. */
  interval: number;
  setInterval: (ms: number) => void;
  ledger: LedgerView;
  selected: number | null;
  select: (i: number | null) => void;
  resolutions: Record<string, Record<number, Resolution>>;
  resolve: (stepIndex: number, r: Resolution) => void;
  /** Stream index where the current window starts (moves on approve-and-reset). */
  windowStart: number;
  pending: Gated[];
}

const Ctx = createContext<ReplayState | null>(null);

export function ReplayProvider({ children }: { children: ReactNode }) {
  const [sessionId, setSessionIdRaw] = useState(sessions.sessions[0].id);
  const session = sessions.sessions.find((s) => s.id === sessionId) ?? sessions.sessions[0];
  const [cursor, setCursorRaw] = useState(session.steps.length);
  const [playing, setPlaying] = useState(false);
  const [interval, setIntervalMs] = useState(450);
  const [selected, select] = useState<number | null>(null);
  const [resolutions, setResolutions] = useState<Record<string, Record<number, Resolution>>>({});
  const timer = useRef<number | undefined>(undefined);

  const setCursor = useCallback(
    (n: number) => setCursorRaw(Math.max(0, Math.min(session.steps.length, n))),
    [session.steps.length],
  );

  const setSessionId = useCallback((id: string) => {
    const s = sessions.sessions.find((x) => x.id === id);
    if (!s) return;
    setPlaying(false);
    setSessionIdRaw(id);
    setCursorRaw(s.steps.length);
    select(null);
  }, []);

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setCursorRaw((c) => {
        if (c >= session.steps.length) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, interval);
    return () => window.clearInterval(timer.current);
  }, [playing, interval, session.steps.length]);

  useEffect(() => {
    const onHide = () => document.hidden && setPlaying(false);
    document.addEventListener("visibilitychange", onHide);
    return () => document.removeEventListener("visibilitychange", onHide);
  }, []);

  const play = useCallback(() => {
    setCursorRaw((c) => (c >= session.steps.length ? 0 : c));
    select(null);
    setPlaying(true);
  }, [session.steps.length]);
  const pause = useCallback(() => setPlaying(false), []);
  const restart = useCallback(() => {
    setCursorRaw(0);
    select(null);
    setPlaying(true);
  }, []);

  const resolve = useCallback(
    (stepIndex: number, r: Resolution) =>
      setResolutions((all) => ({ ...all, [session.id]: { ...(all[session.id] ?? {}), [stepIndex]: r } })),
    [session.id],
  );

  const mine = resolutions[session.id] ?? {};
  const windowStart = useMemo(() => {
    let start = 0;
    for (const [i, r] of Object.entries(mine)) if (r.kind === "approve_reset" && Number(i) + 1 > start) start = Number(i) + 1;
    return start;
  }, [mine]);
  const ledger = useMemo(() => ledgerAt(session, cursor, windowStart), [session, cursor, windowStart]);
  const pending = ledger.asked.filter((s) => !mine[s.i]);

  const value: ReplayState = {
    sessions: sessions.sessions,
    session,
    setSessionId,
    cursor,
    setCursor,
    playing,
    play,
    pause,
    restart,
    interval,
    setInterval: setIntervalMs,
    ledger,
    selected,
    select,
    resolutions,
    resolve,
    windowStart,
    pending,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useReplay(): ReplayState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useReplay outside ReplayProvider");
  return v;
}
