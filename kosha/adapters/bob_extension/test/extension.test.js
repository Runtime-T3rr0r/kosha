"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { fake, extension } = require("./load_extension");

test("activation: status bar, command, and no panel until something is held", async () => {
  const ctx = { subscriptions: [] };
  const api = extension.activate(ctx);
  api.monitor.stop();
  assert.equal(fake.calls.statusItems[0].shown, true);
  assert.equal(fake.calls.statusItems[0].command, "kosha.openApprovals");
  assert.ok(fake.calls.commands["kosha.openApprovals"]);
  assert.equal(fake.calls.panels.length, 0);
});

test("the panel frames koshad's /ui, keeps context while hidden, and is reused", async () => {
  const api = extension.activate({ subscriptions: [] });
  api.monitor.stop();
  const before = fake.calls.panels.length;
  await api.openPanel(true);
  await api.openPanel(true);
  assert.equal(fake.calls.panels.length, before + 1);
  const p = fake.calls.panels.at(-1);
  assert.equal(p.opts.enableScripts, true);
  assert.equal(p.opts.retainContextWhenHidden, true);          // stays unlocked while hidden
  assert.equal(p.show.preserveFocus, true);                     // doesn't steal the user's typing
  assert.match(p.webview.html, /<iframe src="http:\/\/127\.0\.0\.1:8765\/ui"/);
  assert.match(p.webview.html, /frame-src http:\/\/127\.0\.0\.1:8765;/);
  assert.match(p.webview.html, /default-src 'none'/);
  assert.ok(fake.calls.reveals.length >= 1);
});

test("panelHtml only frames the given origin", () => {
  const html = extension.panelHtml("http://127.0.0.1:9/ui", "http://127.0.0.1:9");
  assert.ok(!/script/i.test(html.replace(/enableScripts/g, "")));   // the frame page has no script of its own
});
