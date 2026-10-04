#!/usr/bin/env python3
"""
agent-preflight gate (v4).

Blocks tool calls until a required prerequisite has been VERIFIED this turn.

WHAT v4 ADDS OVER v3

v3 removed the advertised bypass. It still decided satisfaction from the
inspected *request*, so a classification never established that the work
happened -- the gap that made 689 of v2's records unconfirmable.

  C. Post-execution confirmation (D5.1). PreToolUse admits a CANDIDATE and
     lets it run; PostToolUse confirms and only then satisfies the gate.
     Non-error, not non-empty: a filtered search correctly returning zero rows
     is diligence. And a non-error status alone is not enough -- Incident 2
     was a well-formed HTTP 200 carrying a failure -- so where the rule
     declares an expected response shape, the body must parse into it.

  D. Consecutive-failure counter and degraded state (D4/D5.5). Failures 1-3
     block with a retry message; the 4th enters degraded, which warns on every
     invocation. Failure never silently counts as satisfaction. No liveness
     probe in the hot path: the signal comes free from the confirmations in C.

  E. Session receipt. A log nobody reads for 162 days is this project's own
     Incident 3, so the numbers are pushed at the operator at session end
     rather than stored for later. Compact when healthy, expanded when not.
     Delivered via `systemMessage` on JSON stdout -- the harness's documented
     operator channel -- because stderr on exit 0 reaches nobody.

Rules are data and shell is admitted by parsing; both live in hooks/rules.py.

Output protocol:
  Exit 0            -> allow
  Exit 2 + stderr   -> block (message returned to the agent)

Register for PreToolUse, PostToolUse and Stop in ~/.claude/settings.json.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authority  # noqa: E402
import records  # noqa: E402
import rules as R  # noqa: E402

RULE = R.load_rule("consult-backend")

# "observe" records every decision but never blocks. It is the shadow-deploy
# and the intended public default: an install that fails closed against a
# backend the installer has not finished configuring bricks the first session.
# Set in the rule config so the mode can change without editing source.
MODE = (RULE or {}).get("mode", "enforce")
DEGRADE_AFTER = (RULE or {}).get("degraded_after_consecutive_failures", 4)

# ---------------------------------------------------------------------------
# Agent-facing messages. Load-bearing: Incident 4's root cause was message
# text, so each is hashed into every record via MESSAGE_VERSION.
#
# RULE: state the requirement and name the configured clients. Never include a
# worked example, and never name an action that does not perform the work.
# ---------------------------------------------------------------------------

def _named_clients(rule):
    """The configured clients, by name, drawn from the rule itself.

    "One of the configured clients" names nothing, so an agent that does not
    already know them guesses -- and its guess is a piped curl, which the gate
    refuses. Only actions that perform a query are listed, which is the half of
    Incident 4's lesson the old wording kept. Endpoints are deliberately absent:
    the wrapper resolves them, and agent-visible text is recorded downstream.
    """
    r = rule or {}
    parts = []
    wrappers = sorted(r.get("wrapper_clients") or [])
    if wrappers:
        parts.append(f"the shipped wrapper {', '.join(wrappers)} "
                     "(requires --query carrying the search terms)")
    clients = sorted(r.get("clients") or [])
    if clients:
        parts.append(f"{', '.join(clients)} as a single simple command")
    tools = sorted(r.get("mcp_search_tools") or [])
    if tools:
        parts.append(f"the MCP tool {', '.join(tools)}")
    if not parts:
        return "No clients are configured for this rule."
    return "Configured clients: " + "; ".join(parts) + "."


CLIENTS = _named_clients(RULE)
WRAPPERS = ", ".join(sorted((RULE or {}).get("wrapper_clients") or []))

MSG_BLOCKED = (
    "PREFLIGHT GATE: this turn has not yet consulted the required backend.\n\n"
    "Satisfy it by running a filtered query against the configured endpoint\n"
    "through one of the configured clients. The query must carry search terms\n"
    "drawn from what the user actually asked.\n\n"
    f"{CLIENTS}\n\n"
    "Reading a local file does not satisfy this. Neither does printing text\n"
    "that mentions the backend. The gate opens on a confirmed result, not on\n"
    "the shape of the request."
)

MSG_GENERIC = (
    "PREFLIGHT GATE: query rejected -- no filtering.\n\n"
    "The query addresses the backend but retrieves whatever is most recent\n"
    "rather than what the user asked about. Add search terms drawn from the\n"
    "request.\n\n"
    "A filtered query that legitimately returns zero rows does satisfy this\n"
    "gate. Finding nothing is a result; not looking is not."
)

MSG_COMPOUND = (
    "PREFLIGHT GATE: this command was not interpreted.\n\n"
    "Only a single simple command run through a configured client counts.\n"
    "Pipelines, chained commands, substitutions, redirections and remote\n"
    "wrappers are not refused because they are dangerous -- they are refused\n"
    "because this control cannot establish what they did.\n\n"
    + (f"Use the shipped wrapper, {WRAPPERS}, which is itself a configured\n"
       "client and does the compound work internally."
       if WRAPPERS else
       "No wrapper is configured for this rule; use a configured client as a\n"
       "single simple command.")
)

MSG_WRAPPER_NO_TERMS = (
    f"PREFLIGHT GATE: {WRAPPERS or 'the wrapper'} was run without search terms.\n\n"
    "It counts only when it carries search terms drawn from what the user\n"
    "asked -- passed as plain arguments or with --query. Running it bare, or\n"
    "with only options such as --help or --limit, retrieves nothing specific."
)

MSG_RETRY = (
    "PREFLIGHT GATE: the backend query ran but did not confirm ({reason}).\n\n"
    "Consecutive failures: {n}. The dependency may be unreachable or returning\n"
    "a well-formed failure. Retry, or investigate the backend."
)

MESSAGE_VERSION = hashlib.sha256(
    (MSG_BLOCKED + MSG_GENERIC + MSG_COMPOUND + MSG_WRAPPER_NO_TERMS
     + MSG_RETRY).encode()).hexdigest()[:8]

RULE_CONFIG_HASH = hashlib.sha256(
    json.dumps({k: v for k, v in (RULE or {}).items() if not k.startswith("_")},
               sort_keys=True, default=str).encode()).hexdigest()[:8]


# ===== State ===============================================================
# D5.3: outside /tmp, unguessable, missing state fails closed.
# Honest limit: hook and agent shell share a uid, so this is "not trivially
# writable", not "unwritable". See ARCHITECTURE.md D1 and D5.

def gate_path(session_id: str) -> Path:
    secret = records.install_secret()
    name = hashlib.sha256((secret + "gate" + (session_id or "")).encode()).hexdigest()[:32]
    return records.STATE_DIR / f"{name}.state"


def read_state(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def write_state(p: Path, st: dict):
    try:
        p.write_text(json.dumps(st))
    except OSError:
        pass


# ===== Recording ===========================================================

def emit(phase, session_id, tool_name, tool_input, classification, outcome,
         result=None, integrity=None, failures=0):
    rec = records.build(
        phase=phase, session_id=session_id, tool_name=tool_name,
        tool_input=tool_input, classification=classification, outcome=outcome,
        mode=MODE, rule_id=(RULE or {}).get("rule_id", "unknown"),
        rule_kind=(RULE or {}).get("rule_kind", "unknown"),
        rule_config_hash=RULE_CONFIG_HASH, message_version=MESSAGE_VERSION,
        consecutive_failures=failures, result=result,
        rule_identifier_re=None, integrity=integrity,
    )
    if not records.write(rec):
        print("preflight: decision record could not be written", file=sys.stderr)
        return
    bump_tally(session_id, outcome, bool(integrity))


# ===== Per-session tally (T4) ==============================================
# The receipt used to scan all of decisions.jsonl on every session end, which
# is fine at hundreds of records and wrong at hundreds of thousands. The tally
# is bumped only after the record itself is written, so it can never count a
# decision the record file does not hold.

def tally_path(session_id: str) -> Path:
    secret = records.install_secret()
    name = hashlib.sha256((secret + "tally" + (session_id or "")).encode()).hexdigest()[:32]
    return records.STATE_DIR / f"{name}.tally"


def bump_tally(session_id, outcome, incomplete):
    p = tally_path(session_id)
    t = read_state(p) or {"outcomes": {}, "incomplete": 0}
    t["outcomes"][outcome] = t["outcomes"].get(outcome, 0) + 1
    t["incomplete"] += 1 if incomplete else 0
    write_state(p, t)


def session_counts(session_id):
    """(outcome counts, incomplete count), or None if the session has no records.

    Sessions that began before the tally existed have no tally file; they fall
    back to the full scan rather than reporting nothing.
    """
    t = read_state(tally_path(session_id))
    if t is not None:
        return t.get("outcomes", {}), t.get("incomplete", 0)
    sh = records.session_hash(session_id)
    try:
        lines = records.RECORD_FILE.read_text().splitlines()
    except OSError:
        return None
    mine = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("session_hash") == sh:
            mine.append(r)
    if not mine:
        return None
    return collections_count(mine, "outcome"), sum(1 for r in mine if r.get("integrity"))


def notify(msg):
    """The operator-visible channel.

    VERIFIED against the harness, because assuming this is exactly the mistake
    this project documents. Claude Code surfaces `systemMessage` on a hook's
    JSON stdout -- "Display a message to the user (all hooks)". Writing to
    stderr and exiting 0 is NOT an operator channel: for PreToolUse/PostToolUse
    stderr is shown to the model on exit 2, and a Stop hook's exit-0 stderr
    reaches nobody. A receipt that wrote to stderr would have looked correct --
    it writes, it exits clean, its tests pass -- while never being seen. That
    is this project's own failure mode.

    Agent-facing post-phase feedback is a DIFFERENT channel
    (hookSpecificOutput.additionalContext) and is not yet wired; see TODO T3.
    """
    try:
        print(json.dumps({"systemMessage": msg}))
    except (TypeError, ValueError, OSError):
        pass


REHEARSAL_PREFIX = (
    "PREFLIGHT REHEARSAL: this call was allowed. Under enforcement it would have\n"
    "been blocked with the message below.\n\n"
)


def block(msg, exit_code=2):
    if MODE == "enforce":
        print(msg, file=sys.stderr)
        sys.exit(exit_code)
    if MODE == "rehearsal":
        # D7: the agent sees the real block message, but the call proceeds, so
        # whether the message changes behaviour is measurable with no lockout.
        # It is told this is a rehearsal: the call visibly succeeds, so a
        # message pretending otherwise would be false. additionalContext on
        # PreToolUse reaches the model alongside the tool result (verified
        # against the harness docs; see test_rehearsal_* for the shape).
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": REHEARSAL_PREFIX + msg}}))
    sys.exit(0)


# ===== Pre phase ===========================================================

def handle_pre(session_id, tool_name, tool_input, tool_use_id):
    if MODE == "enforce":
        # D7: the operator's escape hatch. Only root can create it, it expires
        # on its own, and every call it lets through is loud and recorded.
        ov = authority.active_override()
        if ov:
            emit("pre", session_id, tool_name, tool_input, None, "override-allow",
                 result={"until": ov.get("until")})
            notify(f"PREFLIGHT ADMIN OVERRIDE until {ov.get('until')} "
                   f"({ov.get('reason')}). Enforcement is NOT in effect.")
            sys.exit(0)

    gate = gate_path(session_id)
    st = read_state(gate)

    if st is None:
        # v2 allowed here. Failing closed is the D4 posture: a missing
        # precondition is a block, and deleting state must not open the gate.
        emit("pre", session_id, tool_name, tool_input, None, "blocked",
             integrity={"missing_state": True})
        block(MSG_BLOCKED + "\n\n(no gate state for this session)")

    failures = st.get("failures", 0)

    if st.get("state") == "satisfied":
        emit("pre", session_id, tool_name, tool_input, None, "already-satisfied",
             failures=failures)
        sys.exit(0)

    if st.get("degraded"):
        # D4: degraded warns on every invocation and never counts as
        # satisfaction. It is bounded, not a state to live in for eight days.
        emit("pre", session_id, tool_name, tool_input, None, "degraded-allow",
             failures=failures)
        notify(f"PREFLIGHT DEGRADED: backend unconfirmed after {failures} "
               f"consecutive failures. Enforcement is NOT in effect.")
        sys.exit(0)

    classification = R.classify(tool_name, tool_input, RULE)

    if classification == "targeted":
        # Admitted as a candidate only. The gate does not open here -- the call
        # has to run and be confirmed first (D5.1).
        st.update({"state": "candidate", "candidate_id": tool_use_id or "",
                   "candidate_tool": tool_name})
        write_state(gate, st)
        emit("pre", session_id, tool_name, tool_input, classification,
             "candidate-admitted", failures=failures)
        sys.exit(0)

    if classification == "capture-only":
        emit("pre", session_id, tool_name, tool_input, classification,
             "permitted-non-qualifying", failures=failures)
        sys.exit(0)

    if classification == "generic":
        emit("pre", session_id, tool_name, tool_input, classification, "blocked",
             failures=failures)
        argv = R.parse_simple_command((tool_input or {}).get("command", "")) \
            if tool_name == "Bash" else None
        if argv and argv[0].rsplit("/", 1)[-1] in (RULE or {}).get("_wrappers", ()):
            block(MSG_WRAPPER_NO_TERMS)
        block(MSG_GENERIC)

    emit("pre", session_id, tool_name, tool_input, None, "blocked", failures=failures)
    if tool_name == "Bash" and R.parse_simple_command(
            (tool_input or {}).get("command", "")) is None:
        block(MSG_COMPOUND)
    block(MSG_BLOCKED)


# ===== Post phase ==========================================================

def handle_post(session_id, tool_name, tool_input, tool_response, tool_use_id):
    gate = gate_path(session_id)
    st = read_state(gate)
    if st is None:
        sys.exit(0)

    classification = R.classify(tool_name, tool_input, RULE)
    if classification is None:
        sys.exit(0)

    is_candidate = (st.get("state") == "candidate"
                    and st.get("candidate_id") == (tool_use_id or ""))
    ok, reason = R.confirms(tool_response, RULE)
    failures = st.get("failures", 0)

    if not is_candidate:
        emit("post", session_id, tool_name, tool_input, classification, "observed",
             result={"status": "ok" if ok else reason}, failures=failures)
        sys.exit(0)

    if ok:
        st.update({"state": "satisfied", "failures": 0, "degraded": False})
        write_state(gate, st)
        emit("post", session_id, tool_name, tool_input, classification, "satisfies",
             result={"status": "ok"}, failures=0)
        sys.exit(0)

    failures += 1
    degraded = failures >= DEGRADE_AFTER
    st.update({"state": "pending", "candidate_id": "", "failures": failures,
               "degraded": degraded})
    write_state(gate, st)
    emit("post", session_id, tool_name, tool_input, classification,
         "unconfirmed", result={"status": reason}, failures=failures)

    if degraded:
        notify(f"PREFLIGHT DEGRADED: {failures} consecutive unconfirmed backend "
               f"queries ({reason}). Enforcement suspended until one confirms.")
    else:
        notify(MSG_RETRY.format(reason=reason, n=failures))
    sys.exit(0)


# ===== Session receipt =====================================================

def receipt(session_id):
    """Push the numbers at the operator at session end.

    Not the block message -- that reaches the agent, and only reaches the
    operator if the agent chooses to relay it. Compact when healthy, expanded
    when not, and never silent about an eligible session.
    """
    counts = session_counts(session_id)
    if counts is None:
        return
    n, incomplete = counts
    confirmed = n.get("satisfies", 0)
    unconfirmed = n.get("unconfirmed", 0)
    blocked = n.get("blocked", 0)
    degraded = n.get("degraded-allow", 0)
    overridden = n.get("override-allow", 0)
    admitted = n.get("candidate-admitted", 0)

    healthy = unconfirmed == 0 and degraded == 0 and overridden == 0 and incomplete == 0

    if healthy:
        notify(f"preflight: {confirmed} confirmed consultation(s), "
               f"{blocked} blocked. rule={(RULE or {}).get('rule_id')} mode={MODE}")
        return

    header = {"observe": "PREFLIGHT SESSION RECEIPT — observing, not enforcing",
              "rehearsal": "PREFLIGHT SESSION RECEIPT — rehearsing, not enforcing",
              }.get(MODE, "PREFLIGHT SESSION RECEIPT — attention required")
    out = [
        "",
        header,
        f"  rule            {(RULE or {}).get('rule_id')}  mode={MODE}",
        f"  confirmed       {confirmed}",
        f"  admitted        {admitted}   (allowed to run, awaiting confirmation)",
        f"  UNCONFIRMED     {unconfirmed}   (ran, did not establish the work)",
        f"  blocked         {blocked}",
    ]
    if degraded:
        out.append(f"  DEGRADED ALLOWS {degraded}   (enforcement was NOT in effect)")
    if overridden:
        out.append(f"  ADMIN OVERRIDE  {overridden}   (allowed under sudo override)")
    if incomplete:
        out.append(f"  INCOMPLETE      {incomplete}   (records missing — measurement gap)")
    if MODE == "rehearsal":
        out += ["",
                "  Rehearsal: the agent was shown each block message, but nothing",
                "  was blocked. Compare refusals-before-first-admission against",
                "  observe-mode sessions to see whether the message changes behaviour.",
                ""]
    elif MODE == "observe":
        out += ["",
                "  Shadow mode: these are the decisions this rule WOULD have made.",
                "  Nothing was blocked. `blocked` counts calls that would not have",
                "  satisfied the rule, which is the friction an enforce switch buys.",
                ""]
    else:
        out += ["",
                "  Unconfirmed queries ran but did not establish that the backend",
                "  answered. A well-formed failure looks like success; that is the",
                "  failure mode this control exists to catch.",
                ""]
    notify("\n".join(out))


def collections_count(rows, key):
    out = {}
    for r in rows:
        out[r.get(key)] = out.get(r.get(key), 0) + 1
    return out


# ===== Entry ===============================================================

def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    session_id = data.get("session_id", "default")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_use_id = data.get("tool_use_id", "")
    phase = data.get("hook_event_name", "PreToolUse")

    if phase == "PostToolUse":
        handle_post(session_id, tool_name, tool_input,
                    data.get("tool_response"), tool_use_id)
    elif phase == "Stop":
        receipt(session_id)
        sys.exit(0)
    else:
        handle_pre(session_id, tool_name, tool_input, tool_use_id)


if __name__ == "__main__":
    main()
