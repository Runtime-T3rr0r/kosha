import type { StreamStep } from "../data";

const OPERATORS = new Set(["&&", "||", ";", "|", "&", ">", ">>", "<", "2>&1"]);

type Tok = { text: string; cls: string };

/** Split a shell command into display tokens, keeping quoted strings and whitespace
 * intact. Display only: classification is done by kosha/system/parser.py. */
function tokenize(cmd: string): Tok[] {
  const out: Tok[] = [];
  const re = /(\s+)|("(?:\\.|[^"\\])*"?|'[^']*'?)|(&&|\|\||2>&1|>>|[;|&<>])|([^\s"';|&<>]+)/g;
  let expectBin = true;
  let afterBin = false;
  for (const m of cmd.matchAll(re)) {
    const [text, ws, str, op, word] = m;
    if (ws) {
      out.push({ text, cls: "" });
    } else if (op) {
      out.push({ text, cls: "c-op" });
      expectBin = OPERATORS.has(op) && op !== ">" && op !== ">>" && op !== "<" && op !== "2>&1";
      afterBin = false;
    } else if (str) {
      out.push({ text, cls: "c-str" });
      expectBin = false;
    } else if (word) {
      if (expectBin && /^[A-Z_]+=/.test(word)) {
        out.push({ text, cls: "c-arg" });
      } else if (expectBin && (word === "sudo" || word === "env" || word === "timeout")) {
        out.push({ text, cls: "c-bin" });
      } else if (expectBin) {
        out.push({ text, cls: "c-bin" });
        expectBin = false;
        afterBin = true;
      } else if (word.startsWith("-")) {
        out.push({ text, cls: "c-flag" });
      } else if (afterBin && /^[a-z][a-z0-9-]*$/.test(word)) {
        out.push({ text, cls: "c-sub" });
        afterBin = false;
      } else if (word.includes("/") || /\.[a-z0-9]{1,5}$/i.test(word)) {
        out.push({ text, cls: "c-path" });
        afterBin = false;
      } else {
        out.push({ text, cls: "c-arg" });
        afterBin = false;
      }
    }
  }
  return out;
}

export function Command({ text, maxLines }: { text: string; maxLines?: number }) {
  const style = maxLines
    ? { display: "-webkit-box", WebkitLineClamp: maxLines, WebkitBoxOrient: "vertical" as const, overflow: "hidden" }
    : undefined;
  return (
    <code className="cmd" style={style}>
      {tokenize(text).map((t, i) =>
        t.cls ? (
          <span key={i} className={t.cls}>
            {t.text}
          </span>
        ) : (
          t.text
        ),
      )}
    </code>
  );
}

/** A step rendered readably: the shell command, or `tool path` for file tools. */
export function StepCommand({ step, maxLines }: { step: StreamStep; maxLines?: number }) {
  if (step.command) return <Command text={step.command} maxLines={maxLines} />;
  return (
    <code className="cmd">
      <span className="c-tool">{step.tool === "other" ? step.action : step.tool}</span>
      {step.path && (
        <>
          {" "}
          <span className="c-path">{step.path}</span>
        </>
      )}
    </code>
  );
}
