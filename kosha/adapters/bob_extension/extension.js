"use strict";
// Kosha for Bob: when koshad holds an agent's action, open the approval panel inside Bob
// on its own, raise a notification, and keep a status-bar count. The panel embeds
// koshad's /ui page, where the human unlocks with the passphrase and decides. Nothing
// here can approve anything by itself, and the passphrase never passes through it.
const vscode = require("vscode");
const { Monitor, describeHeld, statusText } = require("./core");

let panel = null;

function panelHtml(pageUrl, origin) {
  // the page is koshad's own; the panel only frames it
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src ${origin}; style-src 'unsafe-inline';">
<style>html,body{margin:0;padding:0;height:100%;overflow:hidden;background:transparent}
iframe{border:0;width:100%;height:100%}</style></head>
<body><iframe src="${pageUrl}" title="Kosha approvals"></iframe></body></html>`;
}

async function openPanel(baseUrl, preserveFocus) {
  if (panel) { panel.reveal(vscode.ViewColumn.Beside, preserveFocus); return panel; }
  const external = await vscode.env.asExternalUri(vscode.Uri.parse(baseUrl));
  const origin = external.toString(true).replace(/\/+$/, "");
  panel = vscode.window.createWebviewPanel(
    "kosha.approvals", "Kosha approvals", { viewColumn: vscode.ViewColumn.Beside, preserveFocus },
    { enableScripts: true, retainContextWhenHidden: true }); // keeps the page unlocked while hidden
  panel.webview.html = panelHtml(`${origin}/ui`, origin);
  panel.onDidDispose(() => { panel = null; });
  return panel;
}

function activate(context) {
  const cfg = vscode.workspace.getConfiguration("kosha");
  const baseUrl = cfg.get("koshadUrl", "http://127.0.0.1:8765").replace(/\/+$/, "");
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  status.command = "kosha.openApprovals";
  status.text = "$(shield) Kosha: connecting…";
  status.tooltip = "Kosha approvals: click to open";
  status.show();

  const monitor = new Monitor(baseUrl, {
    onStatus: (s) => {
      status.text = statusText(s);
      status.backgroundColor = s.held > 0 ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
    },
    onHeld: async (payload) => {
      if (vscode.workspace.getConfiguration("kosha").get("autoOpen", true)) await openPanel(baseUrl, true);
      const pick = await vscode.window.showWarningMessage(describeHeld(payload), "Review");
      if (pick === "Review") await openPanel(baseUrl, false);
    },
    log: (m) => console.log(`[kosha] ${m}`),
  });

  context.subscriptions.push(
    status,
    vscode.commands.registerCommand("kosha.openApprovals", () => openPanel(baseUrl, false)),
    { dispose: () => monitor.stop() });
  monitor.start();
  return { monitor, openPanel: (f) => openPanel(baseUrl, f) };   // exposed for tests
}

function deactivate() {}

module.exports = { activate, deactivate, panelHtml };
