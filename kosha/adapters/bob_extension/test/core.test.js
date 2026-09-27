"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { SseParser, describeHeld, statusText, Monitor } = require("../core");

const sse = (id, event, obj) => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(obj)}\n\n`;
const held = (id, agent = "migrate-deploy") => sse(id, "approval_requested",
  { id, type: "approval_requested", session_id: "release-1.3",
    payload: { agent_id: agent, tool: "run_command", argv: ["python3", "manage.py", "migrate", "--db", "prod"],
               decision: { rule: "escalation" } } });

test("parser handles chunks split anywhere, CRLF, comments, multi-line data", () => {
  const p = new SseParser();
  const text = ": keep-alive\r\n" + sse(1, "a", { x: 1 }) + "id: 2\nevent: b\ndata: line1\ndata: line2\n\n";
  const out = [];
  for (const ch of text) out.push(...p.feed(ch));            // one character at a time
  assert.deepEqual(out.map((e) => [e.id, e.event]), [["1", "a"], ["2", "b"]]);
  assert.equal(out[1].data, "line1\nline2");
});

test("notification text names agent, command and rule, and truncates", () => {
  assert.equal(describeHeld(JSON.parse(held(1).split("data: ")[1]).payload),
    "Kosha held migrate-deploy: python3 manage.py migrate --db prod (escalation)");
  const long = describeHeld({ agent_id: "a", argv: ["x".repeat(200)] });
  assert.ok(long.length < 120 && long.includes("..."));
});

test("status line", () => {
  assert.equal(statusText({ connected: false }), "$(shield) Kosha: offline");
  assert.equal(statusText({ connected: true, held: 2, fleet: { spent: 70, budget: 750 } }),
    "$(shield) Kosha: 2 held · fleet 70/750");
});

function harness({ history = "", pending = [], baselineFails = 0 } = {}) {
  const heldCalls = [], statuses = [], streams = [];
  let failures = baselineFails;
  const deps = {
    retryMs: [5],
    getText: async () => { if (failures-- > 0) throw new Error("down"); return history; },
    getJson: async (url) => url.endsWith("/approvals") ? pending
      : { fleet: { spent: 70, budget: 750 } },
    openStream: (url, h) => { const s = { url, h, closed: false, close() { this.closed = true; } }; streams.push(s); h.onOpen(); return s; },
  };
  const m = new Monitor("http://k:1/", { onHeld: (p) => heldCalls.push(p), onStatus: (s) => statuses.push(s), log() {} }, deps);
  return { m, heldCalls, statuses, streams, deps };
}
const tick = () => new Promise((r) => setTimeout(r, 20));

test("history never pops up: only holds after start-up fire", async () => {
  const h = harness({ history: held(1) + held(2), pending: [{ id: 1, session_id: "release-1.3" }] });
  await h.m.start();
  assert.equal(h.streams[0].url, "http://k:1/stream?last_id=2");      // streams from after history
  assert.equal(h.heldCalls.length, 0);
  assert.equal(h.statuses.at(-1).held, 1);                             // but counted in the status
  h.streams[0].h.onEvent({ id: "3", event: "approval_requested", data: JSON.parse(JSON.stringify(held(3).split("data: ")[1])) });
  await tick();
  assert.equal(h.heldCalls.length, 1);
  assert.equal(h.heldCalls[0].agent_id, "migrate-deploy");
  h.m.stop();
});

test("duplicate or older event ids are ignored (reconnect overlap)", async () => {
  const h = harness({ history: held(5) });
  await h.m.start();
  const ev = (id) => ({ id: String(id), event: "approval_requested", data: held(id).split("data: ")[1] });
  h.streams[0].h.onEvent(ev(4)); h.streams[0].h.onEvent(ev(5)); h.streams[0].h.onEvent(ev(6)); h.streams[0].h.onEvent(ev(6));
  await tick();
  assert.equal(h.heldCalls.length, 1);
  h.m.stop();
});

test("koshad down at start-up: no live stream until the history baseline succeeds", async () => {
  const h = harness({ history: held(1) + held(2), baselineFails: 2 });
  await h.m.start();
  assert.equal(h.streams.length, 0);                                   // would otherwise replay 1 and 2
  await new Promise((r) => setTimeout(r, 80));
  assert.equal(h.streams.length, 1);
  assert.equal(h.streams[0].url, "http://k:1/stream?last_id=2");
  assert.equal(h.heldCalls.length, 0);
  h.m.stop();
});

test("a dropped stream reconnects from the last id and shows offline meanwhile", async () => {
  const h = harness({ history: held(1) });
  await h.m.start();
  h.streams[0].h.onEvent({ id: "2", event: "action_reserved", data: "{}" });
  h.streams[0].h.onClose(new Error("koshad restarted"));
  assert.equal(h.statuses.at(-1).connected, false);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(h.streams.at(-1).url, "http://k:1/stream?last_id=2");
  h.m.stop();
});
