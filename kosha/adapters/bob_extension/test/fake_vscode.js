"use strict";
// Minimal stand-in for the `vscode` module: records what the extension asks Bob to do.
const calls = { panels: [], reveals: [], warnings: [], commands: {}, statusItems: [] };
let warningAnswer;
const fake = {
  calls,
  answerWarningsWith: (v) => { warningAnswer = v; },
  ViewColumn: { Beside: -2 },
  StatusBarAlignment: { Left: 1 },
  ThemeColor: class { constructor(id) { this.id = id; } },
  Uri: { parse: (s) => ({ toString: () => s }) },
  env: { asExternalUri: async (u) => u },
  workspace: { getConfiguration: () => ({ get: (k, d) => d }) },
  commands: { registerCommand: (id, fn) => { calls.commands[id] = fn; return { dispose() {} }; } },
  window: {
    createStatusBarItem: () => { const s = { text: "", show() { this.shown = true; }, dispose() {} }; calls.statusItems.push(s); return s; },
    createWebviewPanel: (viewType, title, show, opts) => {
      const p = { viewType, title, show, opts, webview: { html: "" }, disposed: false,
                  reveal: (col, preserveFocus) => calls.reveals.push({ col, preserveFocus }),
                  onDidDispose: (fn) => { p._dispose = fn; } };
      calls.panels.push(p);
      return p;
    },
    showWarningMessage: async (msg, ...items) => { calls.warnings.push({ msg, items }); return warningAnswer; },
  },
};
module.exports = fake;
