"""Run pieces of the locally installed Bob against kosha, at test time.

Bob is proprietary: nothing extracted here is ever written into the repo. Functions
are copied verbatim out of the installed bob-code extension into a temp dir and run
with node, so the tests exercise Bob's real hook runner and deniedCommands matcher.
Tests using this skip when Bob or node is not installed.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

EXTENSION_JS = Path(os.environ.get(
    "KOSHA_BOB_EXTENSION_JS",
    Path.home() / ".local/opt/bobide/resources/app/extensions/bob-code/dist/extension.js"))

HOOK_RUNNER = ["async function Tb(", "function iei(", "function aei(", "function oei(",
               "async function sei(", "function EYt(", "function cei(", "function CYt(",
               "function uei(", "function uke(", "function dei("]
MATCHER = ["HZ=(r,t)=>", "ZVt=(r,t,n)=>"]


def available() -> bool:
    return EXTENSION_JS.exists() and shutil.which("node") is not None


def _extract(src: str, sig: str) -> str:
    """Source of the function starting at sig, by brace matching (strings respected)."""
    i = src.find(sig)
    if i < 0:
        raise LookupError(f"{sig!r} not found in {EXTENSION_JS}; Bob version changed?")
    k, depth, quote = src.index("{", i), 0, None
    while True:
        ch = src[k]
        if quote:
            if ch == "\\":
                k += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1


RUNNER_JS = r"""
const warns = [];
const XT = { warn: (...a) => warns.push(a.map(String).join(' ')) };
const eei = require('child_process').exec;   // Bob: import{exec as eei}from"child_process"
const tei = 10;                               // Bob: default hook timeout (s)
const rei = 1024 * 1024;                      // Bob: maxBuffer
const lei = () => ({});
%s
(async () => {
  const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
  const r = await Tb(input.hooks, input.payload);
  process.stdout.write(JSON.stringify({ ...r, warns }));
})();
"""

MATCHER_JS = r"""
const %s;
const %s;
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => ZVt(c.command, c.approved, c.denied) ?? null)));
"""


class BobRuntime:
    def __init__(self, workdir: Path):
        src = EXTENSION_JS.read_text(encoding="utf-8", errors="replace")
        self.runner = workdir / "bob_hook_runner.js"
        self.runner.write_text(RUNNER_JS % "\n".join(_extract(src, s) for s in HOOK_RUNNER))
        self.matcher = workdir / "bob_matcher.js"
        self.matcher.write_text(MATCHER_JS % tuple(_extract(src, s) for s in MATCHER))

    def run_hooks(self, hooks: dict, payload: dict, env: dict | None = None) -> dict:
        """Bob's Tb(): {blocked, reason?, warns}."""
        p = subprocess.run(["node", str(self.runner)], input=json.dumps({"hooks": hooks, "payload": payload}),
                           capture_output=True, text=True, timeout=60, env={**os.environ, **(env or {})})
        if p.returncode != 0:
            raise RuntimeError(p.stderr)
        return json.loads(p.stdout)

    def denied_verdicts(self, cases: list[dict]) -> list:
        """Bob's ZVt(command, approvedCommands, deniedCommands): "allow" | "deny" | None."""
        p = subprocess.run(["node", str(self.matcher)], input=json.dumps(cases),
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError(p.stderr)
        return json.loads(p.stdout)
