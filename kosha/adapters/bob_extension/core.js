"use strict";
// Kosha for Bob: the logic, free of the VS Code API so it can be tested in plain Node.
// Watches koshad's /stream; when an action is held it tells the host (open the approval
// panel, notify), and keeps a status line ("Kosha: 2 held · fleet 70/750") up to date.
// Read-only toward koshad: approving happens in the embedded /ui page, with the passphrase.

const http = require("http");
const https = require("https");

/** Incremental text/event-stream parser: feed() chunks, get complete events back. */
class SseParser {
  constructor() { this.buf = ""; this.cur = {}; }
  feed(chunk) {
    this.buf += chunk;
    const out = [];
    let nl;
    while ((nl = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, nl).replace(/\r$/, "");
      this.buf = this.buf.slice(nl + 1);
      if (line === "") {                          // blank line ends an event
        if (this.cur.data !== undefined) out.push(this.cur);
        this.cur = {};
        continue;
      }
      if (line.startsWith(":")) continue;          // comment / keep-alive
      const i = line.indexOf(":");
      const field = i < 0 ? line : line.slice(0, i);
      const value = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
      if (field === "data") this.cur.data = this.cur.data === undefined ? value : this.cur.data + "\n" + value;
      else if (field === "event") this.cur.event = value;
      else if (field === "id") this.cur.id = value;
    }
    return out;
  }
}

/** One line for a notification, from an approval_requested payload. Agent-written text
 * is truncated, never interpreted. */
function describeHeld(payload) {
  const p = payload || {};
  const d = p.decision || {};
  const raw = (p.bundle && p.bundle.length ? p.bundle[p.bundle.length - 1].raw : null) || {};
  let what = (p.argv && p.argv.length) ? p.argv.join(" ") : String(raw.sql || raw.path || raw.command || raw.args || p.tool || "");
  if (what.length > 80) what = what.slice(0, 77) + "...";
  return `Kosha held ${p.agent_id || "an agent"}: ${what}${d.rule ? ` (${d.rule})` : ""}`;
}

function statusText(s) {
  if (!s.connected) return "$(shield) Kosha: offline";
  const fleet = s.fleet ? ` · fleet ${s.fleet.spent}/${s.fleet.budget}` : "";
  return `$(shield) Kosha: ${s.held} held${fleet}`;
}

function getText(url, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https:") ? https : http;
    const req = lib.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (body += c));
      res.on("end", () => (res.statusCode === 200 ? resolve(body) : reject(new Error(`HTTP ${res.statusCode}`))));
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

function getJson(url, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https:") ? https : http;
    const req = lib.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        if (res.statusCode !== 200) return reject(new Error(`HTTP ${res.statusCode}`));
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

/** Opens koshad's event stream; returns a handle with close(). */
function openStream(url, { onEvent, onOpen, onClose }) {
  const lib = url.startsWith("https:") ? https : http;
  const parser = new SseParser();
  let closed = false;
  const req = lib.get(url, (res) => {
    if (res.statusCode !== 200) { res.resume(); if (!closed) { closed = true; onClose(new Error(`HTTP ${res.statusCode}`)); } return; }
    onOpen();
    res.setEncoding("utf8");
    res.on("data", (c) => { for (const ev of parser.feed(c)) onEvent(ev); });
    res.on("end", () => { if (!closed) { closed = true; onClose(null); } });
    res.on("error", (e) => { if (!closed) { closed = true; onClose(e); } });
  });
  req.on("error", (e) => { if (!closed) { closed = true; onClose(e); } });
  return { close: () => { closed = true; req.destroy(); } };
}

/**
 * Watches koshad. host: { onHeld(payload), onStatus(status), log(msg) }.
 * Past events are skipped at start-up, so opening Bob never replays old holds as popups.
 */
class Monitor {
  constructor(baseUrl, host, deps = {}) {
    this.base = baseUrl.replace(/\/+$/, "");
    this.host = host;
    this.getJson = deps.getJson || getJson;
    this.getText = deps.getText || getText;
    this.openStream = deps.openStream || openStream;
    this.retryMs = deps.retryMs || [1000, 2000, 5000];
    this.lastId = 0;
    this.session = null;
    this.status = { connected: false, held: 0, fleet: null };
    this.stopped = false;
    this.baselined = false;
    this.attempt = 0;
    this.stream = null;
    this.timer = null;
  }

  async start() {
    this.stopped = false;
    await this.baseline();
    await this.refresh();
    this.connect();
  }

  /** Skip history: /stream?once=true returns every past event and closes. Remember the
   * newest id, so only holds that happen from now on pop anything open. Until this has
   * succeeded once (koshad down at start-up), no live stream is opened. */
  async baseline() {
    if (this.baselined) return true;
    try {
      const past = new SseParser().feed(await this.getText(`${this.base}/stream?once=true`));
      for (const ev of past) if (Number(ev.id) > this.lastId) this.lastId = Number(ev.id);
      this.baselined = true;
    } catch { this.baselined = false; }
    return this.baselined;
  }

  stop() {
    this.stopped = true;
    if (this.stream) this.stream.close();
    if (this.timer) clearTimeout(this.timer);
  }

  connect() {
    if (this.stopped) return;
    if (!this.baselined) return this.retry();
    this.stream = this.openStream(`${this.base}/stream?last_id=${this.lastId}`, {
      onOpen: () => { this.attempt = 0; this.setStatus({ connected: true }); },
      onEvent: (ev) => this.handle(ev),
      onClose: () => { this.setStatus({ connected: false }); this.retry(); },
    });
  }

  retry() {
    if (this.stopped) return;
    const wait = this.retryMs[Math.min(this.attempt++, this.retryMs.length - 1)];
    this.timer = setTimeout(async () => {
      await this.baseline();
      await this.refresh();
      this.connect();
    }, wait);
  }

  async handle(ev) {
    const id = Number(ev.id);
    if (Number.isFinite(id)) {
      if (id <= this.lastId) return;              // already seen (reconnect overlap)
      this.lastId = id;
    }
    let data = {};
    try { data = JSON.parse(ev.data || "{}"); } catch { return; }
    if (data.session_id) this.session = data.session_id;
    if (ev.event === "approval_requested") {
      // refresh first so the status bar already shows the new count when the panel opens
      await this.refresh();
      this.host.onHeld(data.payload || {});
      return;
    }
    if (/^(approval_resolved|action_)/.test(ev.event || "")) await this.refresh();
  }

  async refresh() {
    try {
      const pending = await this.getJson(`${this.base}/approvals`);
      const session = (pending[pending.length - 1] || {}).session_id || this.session;
      let fleet = null;
      if (session) {
        const s = await this.getJson(`${this.base}/sessions/${encodeURIComponent(session)}`).catch(() => null);
        if (s && s.fleet) fleet = s.fleet;
      }
      this.setStatus({ held: pending.length, fleet, reachable: true });
    } catch (e) {
      this.setStatus({ connected: false });
    }
  }

  setStatus(patch) {
    const { reachable, ...rest } = patch;
    this.status = { ...this.status, ...rest };
    this.host.onStatus({ ...this.status });
  }
}

module.exports = { SseParser, describeHeld, statusText, getText, getJson, openStream, Monitor };
