import { useEffect, useState } from "react";
import { pricing, sessions } from "./data";
import { ReplayProvider, useReplay } from "./replay";
import { ApprovalQueue } from "./views/ApprovalQueue";
import { Benchmarks } from "./views/Benchmarks";
import { LiveSession } from "./views/LiveSession";
import "./app.css";

type Tab = "live" | "queue" | "bench";

const TABS: Tab[] = ["live", "queue", "bench"];

const TITLE: Record<Tab, string> = {
  live: "Watching the fleet",
  queue: "Waiting for a person",
  bench: "How well it works",
};

const SUBHEAD: Record<Tab, string> = {
  live: "Watching a fleet of agents spend one ledger. Every tool call is priced before it runs.",
  queue: "Actions waiting for a person, each shown with everything the fleet did before it.",
  bench: "How often Kosha stops a fleet, on replayed and synthetic runs, and how often it stops one that meant no harm.",
};

function tabFromHash(): Tab {
  const h = window.location.hash.slice(1) as Tab;
  return TABS.includes(h) ? h : "live";
}

export function App() {
  return (
    <ReplayProvider>
      <Shell />
    </ReplayProvider>
  );
}

function Shell() {
  const [tab, setTabRaw] = useState<Tab>(tabFromHash);
  const setTab = (t: Tab) => {
    setTabRaw(t);
    window.history.replaceState(null, "", `#${t}`);
  };
  useEffect(() => {
    const onHash = () => setTabRaw(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const { session, setSessionId, sessions: all, pending } = useReplay();
  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "live", label: "Live session" },
    { id: "queue", label: "Approval queue", badge: pending.length },
    { id: "bench", label: "Benchmarks" },
  ];
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>K</span>
          <span className="brand-name">Kosha</span>
        </div>
        <nav className="tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={`tab ${tab === t.id ? "on" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.badge ? <span className="tab-badge mono">{t.badge}</span> : null}
            </button>
          ))}
        </nav>
        <label className="session-pick">
          <span className="label">Fleet</span>
          <select value={session.id} onChange={(e) => setSessionId(e.target.value)}>
            {all.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id}: {s.kind.replace(/_/g, " ").replace(" composed", "")} fleet of {s.k}
              </option>
            ))}
          </select>
        </label>
      </header>
      <main className="content">
        <div className="page">
          <div className="intro">
            <h1 className="intro-title">{TITLE[tab]}</h1>
            <p className="intro-lead">{SUBHEAD[tab]}</p>
            <p className="intro-sub">
              Kosha prices every action an AI coding agent takes and charges it to one budget shared by the whole
              fleet. When the fleet has done enough that matters, it stops and asks a person.
              {sessions.synthetic && ` This page replays recorded benchmark fleets (synthetic, price table ${pricing.table.version}); no live koshad is attached.`}
            </p>
          </div>
          {tab === "live" && <LiveSession />}
          {tab === "queue" && <ApprovalQueue />}
          {tab === "bench" && <Benchmarks />}
        </div>
      </main>
    </div>
  );
}
