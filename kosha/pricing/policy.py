"""Policy: turn one classified, priced action plus fleet ledger state into allow | ask | deny.

Branch order (first match wins):
  hard_deny -> l5 -> destructive_sql -> convergence -> escalation -> fleet_budget -> agent_cap -> ok

Deterministic by design. Classification lives in rubric.py, pricing in pricing.py;
this module only applies the rules. Every ask/deny carries a reason and a
suggestion of what would be allowed, so the agent can re-plan.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

from kosha.pricing.convergence import MIN_LEVEL as TOUCH_LEVEL
from kosha.pricing.convergence import Touch, converges, norm_path, targets_of
from kosha.pricing.match import PROD_NAME
from kosha.pricing.pricing import price as cell_price
from kosha.pricing.rubric import LEVEL_NAMES
from kosha.system.parser import split_command

if TYPE_CHECKING:
    from kosha.system.action import Action

CONSEQUENTIAL = 3          # L3+ counts toward the escalation rule once 2+ agents are in the window
SINGLE_AGENT_CONSEQUENTIAL = 4   # L4+ while only one agent is in the window
SHELL_OPERATORS = {"&&", "||", ";", "|", "&"}
DROP_DB = re.compile(r"\bdrop\s+(?:database|schema)\s+(?:if\s+exists\s+)?[`\"'\[]?([\w.$-]+)", re.I)
SQL_CLIENT = re.compile(r"\b(psql|mysql|mariadb|sqlite3)\b")
# one SQL statement, up to a terminator, quote or shell operator; what's left of the
# command once these are cut out names the connection (URL, alias, db file, host)
SQL_STATEMENT = re.compile(
    r"\b(?:drop|truncate|delete|update|insert|select|create|alter|grant)\b[^;'\"&|]*", re.I)
DROP_TABLE = re.compile(r"\bdrop\s+table\b", re.I)
TRUNCATE = re.compile(r"\btruncate\s+(?:table\s+)?[`\"\[]?\w", re.I)
UNSCOPED_WRITE = re.compile(r"\bdelete\s+from\b|\bupdate\s+\S+\s+set\b", re.I)
WHERE = re.compile(r"\bwhere\b", re.I)


@dataclass
class Decision:
    decision: Literal["allow", "ask", "deny"]
    reason: str
    level: int             # 0-5
    cell: str              # e.g. "irrev|shared|nopriv"
    price: float
    fleet_after: float
    agent_after: float
    rule: str              # "ok" | "hard_deny" | "l5" | "destructive_sql" | "convergence" | "escalation" |
                           # "fleet_budget" | "agent_cap" | "fail_closed" (adapters/client.py)
    suggestion: Optional[str] = None


@dataclass
class LedgerState:
    """Stand-in for the window state the ledger database (kosha_db.py, not yet built)
    will supply. Exists so policy can be tested in isolation; the real source must
    provide this same shape.

    recent_actions: (agent_id, level) or (agent_id, level, batch) for actions already
    reserved/confirmed in the current window, excluding the action being decided.
    batch (batch_of(action)) groups actions that are one unit of work, e.g. the file
    writes of one commit; the escalation rule counts a batch once. An entry without a
    batch, or with batch None, is its own batch.
    window_touches: typed targets (convergence.touch) of the window's actions that were
    decided allow OR ask, pending ones included, in decision order, excluding the action
    being decided. An asked action counts as soon as it is asked, not once approved or
    confirmed: waiting for confirmation hid 18 of 40 engineered convergences in
    bench/convergence_synth.py.
    """
    fleet_spent: float
    fleet_budget: float
    agent_spent: float     # spend of the agent making this action
    agent_cap: float
    recent_actions: list[tuple] = field(default_factory=list)
    window_touches: list[Touch] = field(default_factory=list)


# --- hard-deny patterns ---

def _segments(argv: list[str]) -> list[list[str]]:
    out, cur = [], []
    for tok in argv:
        if tok in SHELL_OPERATORS:
            out.append(cur)
            cur = []
        else:
            cur.append(tok)
    return [*out, cur]


def _resolves_to_root(arg: str, cwd: str) -> bool:
    """Whether an rm operand names / or everything directly under it, once repeated
    slashes are collapsed, `.`/`..` resolved and a relative operand joined to cwd
    (norm_path, as convergence resolves targets). `*` and `DIR/*` count as DIR."""
    if arg.endswith("*") and (arg == "*" or arg[:-1].endswith("/")):
        arg = arg[:-1] or "."
    arg = re.sub(r"/{2,}", "/", arg.strip())
    if not arg.startswith("/") and not cwd.startswith("/"):
        return False
    return norm_path(arg, cwd) == "/"


def _raw_command_argv(action: Action) -> list[str]:
    """raw["command"] split by the runtime parser (which also splits `a&&b` with no
    spaces and unwraps `bash -c`), segments joined with `;`, for the cases argv
    alone doesn't show."""
    command = (action.raw or {}).get("command")
    if not isinstance(command, str):
        return []
    out: list[str] = []
    for seg in split_command(command):
        out += [*seg, ";"]
    return out


def _rm_rf_root(argv: list[str], cwd: str = "") -> bool:
    """rm with recursive + force whose operand resolves to / or /*, including sudo,
    split flags, rm called by path (/bin/rm), `rm -rf //`, `rm -rf /.`, and a relative
    operand after `cd /` in the same command line (`cd / && rm -rf *`). cwd is the
    action's working directory; `cd DIR` segments move it, as in
    convergence.command_targets.

    The /bin/rm bypass was found and first fixed by IBM Bob in its adversarial review
    task (scripts/e2e_security_audit.py, bob_sessions/kosha_task05_*)."""
    for seg in _segments(argv):
        if seg[:1] == ["cd"]:
            cwd = norm_path(seg[1], cwd) if len(seg) > 1 else "~"
            continue
        for i, tok in enumerate(seg):
            if posixpath.basename(tok).strip().rstrip("\x00") != "rm":
                continue
            args = seg[i + 1:]
            flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
            recursive = "r" in flags.lower() or "--recursive" in args
            force = "f" in flags or "--force" in args
            operands = [a for a in args if not a.startswith("-")]
            if recursive and force and any(_resolves_to_root(a, cwd) for a in operands):
                return True
    return False


def _sql_text(action: Action) -> Optional[str]:
    """argv plus raw tool input of an action that runs SQL (db_exec, raw `sql`, or a
    psql/mysql/sqlite3 command), else None. File edits only write SQL, never run it."""
    if action.tool in ("edit_file", "write_file"):
        return None
    raw = action.raw or {}
    text = " ".join([*action.argv, *(str(v) for v in raw.values())])
    if action.tool == "db_exec" or "sql" in raw or SQL_CLIENT.search(text):
        return text
    return None


def _targets_prod(action: Action, text: str) -> bool:
    """prod as a whole name token (match.PROD_NAME) in the targets or the connection
    part of the command, i.e. the text with SQL statements cut out, so a table or
    database named in the SQL itself never counts."""
    return any(PROD_NAME.search(t) for t in [*action.targets, SQL_STATEMENT.sub(" ", text)])


def _drops_prod_db(action: Action) -> bool:
    """DROP DATABASE/SCHEMA where the dropped name or the connection names prod."""
    text = _sql_text(action)
    if text is None:
        return False
    dropped = DROP_DB.findall(text)
    if not dropped:
        return False
    return any(PROD_NAME.search(n) for n in dropped) or _targets_prod(action, text)


def _destructive_sql(text: str) -> Optional[str]:
    """DROP TABLE, TRUNCATE, or DELETE/UPDATE without WHERE, per statement."""
    for stmt in re.split(r";|&&", text):
        if DROP_TABLE.search(stmt):
            return "DROP TABLE"
        if TRUNCATE.search(stmt):
            return "TRUNCATE"
        m = UNSCOPED_WRITE.search(stmt)
        if m and not WHERE.search(stmt[m.start():]):
            return f"{m.group(0).split()[0].upper()} without WHERE"
    return None


def _destructive_sql_on_shared(action: Action, cell: str) -> Optional[str]:
    """What the destructive statement is, if the action runs one against a shared
    database (cell scope not local) or a prod-named one; else None."""
    text = _sql_text(action)
    what = _destructive_sql(text) if text is not None else None
    if what is None:
        return None
    parts = cell.split("|")
    shared = len(parts) == 3 and parts[1] in ("shared", "external")
    return what if shared or _targets_prod(action, text) else None


def _hard_deny_reason(action: Action) -> Optional[str]:
    """Hard-deny patterns are final: no field on the action overrides them. Nothing
    issues or verifies approval tokens yet, so an override would accept any string
    the caller sets. An override comes back only with real issuance, tied to a human
    approval in the queue."""
    cwd = getattr(action, "cwd", "") or ""
    if _rm_rf_root(action.argv, cwd) or _rm_rf_root(_raw_command_argv(action), cwd):
        return "rm -rf on the filesystem root"
    if _drops_prod_db(action):
        return "dropping a prod database"
    return None


# --- fleet escalation ---

def batch_of(action: Action) -> Optional[str]:
    """Batch key the ledger records with an action (third element of a recent_actions
    entry). Action has no batch field yet, so this reads an optional batch_id
    attribute; None (every live action today) means the action is its own batch."""
    return getattr(action, "batch_id", None)


def _consequential_batches(recent_actions: list[tuple], threshold: int) -> int:
    """Distinct batches with at least one action at or above threshold. Entries
    without a batch key count one each."""
    keys = set()
    for i, entry in enumerate(recent_actions):
        if entry[1] >= threshold:
            batch = entry[2] if len(entry) > 2 else None
            keys.add(("batch", entry[0], batch) if batch is not None else ("solo", i))
    return len(keys)


def escalation_threshold(recent_actions: list[tuple], agent_id: Optional[str] = None) -> int:
    """Level at which an action counts as consequential, by fleet size in the window.

    agent_id is the agent making the action being decided; it counts toward the
    window's distinct agents, so a second agent acting brings the threshold down.

    One distinct agent -> L4. Two or more -> L3. On benign single-agent SWE-smith runs
    (bench/benign_spend.py, n=26076, bash commands only) the rule asked in 95.21 of
    100 sessions before this split (effects table as of d77c6af) and asks in 6.99 with
    the current table. Most of that drop is the threshold itself: with the current
    table, an L3+ threshold for one agent would still ask in 91.16 of 100, driven by
    routine L3 work (local scripts, rm of scratch files). For one agent L3+ is noise;
    consequential actions spread across several agents are the cross-agent signal
    the rule exists for, and keep the L3+ threshold.
    """
    agents = {e[0] for e in recent_actions} | ({agent_id} if agent_id is not None else set())
    return CONSEQUENTIAL if len(agents) >= 2 else SINGLE_AGENT_CONSEQUENTIAL


def escalation_triggered(recent_actions: list[tuple], agent_id: Optional[str] = None) -> bool:
    """Two-plus consequential actions in the window, at escalation_threshold(), counted
    by batch: one batch (e.g. one commit writing many new files) counts once however
    many of its actions are consequential, so a single large commit can't trigger the
    rule on its own. Separate batches, even one action each, count separately.

    No distinctness requirement on the triggering actions themselves: one agent
    repeating consequential actions counts the same as several agents each doing one.
    Fleet size only sets the threshold (L4+ for one agent, L3+ for two or more).

    Deliberately does NOT require strictly rising levels. The rising requirement came
    from the source paper's single-trajectory escalation (one agent ramping up over
    steps) and was carried over without being re-derived for a fleet setting.
    Same-level consequential actions are not obviously less dangerous than one rising
    sequence, so requiring a rise would under-trigger on that case. Rising pairs still
    trigger: they are a subset of this condition, not a separate one.
    """
    threshold = escalation_threshold(recent_actions, agent_id)
    return _consequential_batches(recent_actions, threshold) >= 2


def escalation_asks(recent_actions: list[tuple], agent_id: str, level: int) -> bool:
    """Whether this action (agent_id, level) is stopped by the escalation rule: it is
    itself consequential at the window's threshold and the window already triggered."""
    return (level >= escalation_threshold(recent_actions, agent_id)
            and escalation_triggered(recent_actions, agent_id))


# --- target convergence ---

def convergence_of(action: Action, level: int, window_touches: list[Touch]):
    """Earlier touch, by another agent, of a target this action acts on (convergence.py:
    typed targets, exact match, causal). Read-only and blocked actions (below L2)
    neither touch nor converge."""
    if level < TOUCH_LEVEL:
        return None
    return converges(window_touches, action.agent_id, targets_of(action))


def decide(action: Action, level: int, cell: str, ledger_state: LedgerState) -> Decision:
    """Decide one action. fleet_after/agent_after are the balances after this decision:
    spent + price on allow, unchanged on ask/deny (nothing is reserved yet)."""
    p = cell_price(cell, level=level)
    s = ledger_state
    name = LEVEL_NAMES.get(level, f"L{level}")

    def out(decision, rule, reason, suggestion=None):
        charged = p if decision == "allow" else 0.0
        return Decision(decision, reason, level, cell, p,
                        s.fleet_spent + charged, s.agent_spent + charged, rule, suggestion)

    hard = _hard_deny_reason(action)
    if hard:
        return out("deny", "hard_deny", f"Kosha: denied, {hard} is a hard-deny pattern.",
                   "Target a specific path inside the workspace, or run against a non-prod "
                   "database. Hard-deny patterns have no override; a human has to do this "
                   "outside Kosha.")

    if level >= 5:
        return out("ask", "l5", f"Kosha: {name} ({cell}) always needs human approval.",
                   "Wait for approval, or use a variant that doesn't grant access or add "
                   "persistence (e.g. chmod 644 instead of 777).")

    destructive = _destructive_sql_on_shared(action, cell)
    if destructive:
        return out("ask", "destructive_sql",
                   f"Kosha: {destructive} against a shared/prod database always needs human "
                   f"approval, whatever budget is left.",
                   "Wait for approval, run it against a local dev database first, or scope it "
                   "with a WHERE clause.")

    conv = convergence_of(action, level, s.window_touches)
    if conv:
        return out("ask", "convergence",
                   f"Kosha: target convergence. {conv.earlier_agent} already acted on "
                   f"{conv.target} this window; {action.agent_id} acting on it too needs human "
                   f"approval, whatever budget is left.",
                   f"Wait for a human to review both actions on {conv.target}, or leave that "
                   f"target to {conv.earlier_agent} and act on a different one.")

    if escalation_asks(s.recent_actions, action.agent_id, level):
        threshold = escalation_threshold(s.recent_actions, action.agent_id)
        prior = ", ".join(f"{e[0]} L{e[1]}" for e in s.recent_actions if e[1] >= threshold)
        return out("ask", "escalation",
                   f"Kosha: fleet escalation. Two-plus consequential actions this "
                   f"window ({prior}); this {name} action needs human approval.",
                   "Wait for a human to review the bundle, or switch to a non-consequential "
                   "alternative (dry run, plan, local-only change).")

    fleet_left = s.fleet_budget - s.fleet_spent
    if s.fleet_spent + p > s.fleet_budget:
        return out("ask", "fleet_budget",
                   f"Kosha: fleet budget. {s.fleet_spent:g}/{s.fleet_budget:g} spent this window; "
                   f"this {name} action ({cell}) costs {p:g}.",
                   f"Actions priced at most {max(fleet_left, 0):g} are still allowed, or ask a "
                   f"human to approve and reset the window.")

    agent_left = s.agent_cap - s.agent_spent
    if s.agent_spent + p > s.agent_cap:
        return out("ask", "agent_cap",
                   f"Kosha: agent cap. This agent has spent {s.agent_spent:g}/{s.agent_cap:g} "
                   f"this window; this {name} action ({cell}) costs {p:g}.",
                   f"Actions priced at most {max(agent_left, 0):g} are still allowed for this "
                   f"agent, or hand the step to a human.")

    return out("allow", "ok", f"Kosha: allowed, {name} ({cell}) costs {p:g}.")
