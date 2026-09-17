#!/usr/bin/env python3
"""
agent-preflight gate (v3).

Blocks tool calls until a required prerequisite has been performed this turn.
The reference rule is "consult the configured memory backend before acting."

WHAT CHANGED IN v3, AND WHY

v2 accepted a read of a *local* file as satisfying a *live backend*
requirement, and its block message listed that read as an approved method.
Over 162 days that substitute absorbed 115 of 805 recorded satisfactions --
100% of them during an eight-day backend outage, while the gate reported
success on every turn. See docs/INCIDENTS.md Incident 4; the pre-intervention
dataset is committed at data/before-2026-09-16.jsonl.

  1. No local read satisfies a live-backend requirement. The classifier no
     longer returns "read-memory", and the block message no longer names it.
  2. Messages do not contain example queries. A worked example is a copyable
     minimal satisfying shape -- the same defect as (1) in a second place.
  3. Every decision produces a content-free record, including ordinary blocks
     and already-satisfied allows. v2 recorded only three of five paths, so its
     denominator excluded the decisions it was least likely to be right about.
  4. Gate state lives outside /tmp under an unguessable name, and missing state
     FAILS CLOSED. v2 failed open, so deleting one predictable file disabled
     enforcement silently.

NOT YET IMPLEMENTED (ARCHITECTURE.md D5): post-execution confirmation does not
yet flip satisfaction. The PostToolUse phase records results only. Until that
lands, a "targeted" classification still means an inspected *request*, not a
verified *consultation* -- the exact gap that made v2's 679 targeted
classifications unconfirmable.

Output protocol:
  Exit 0            -> allow
  Exit 2 + stderr   -> block (message returned to the agent)

Setup:
  1. Register for PreToolUse and PostToolUse in ~/.claude/settings.json
  2. Set PREFLIGHT_RULE below for your backend
"""

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import records  # noqa: E402

MODE = "enforce"            # "observe" records without blocking

# ---------------------------------------------------------------------------
# Rule definition. Rules become data (D5.4); this is the one shipped rule.
# ---------------------------------------------------------------------------
PREFLIGHT_RULE = {
    "rule_id": "consult-backend",
    "rule_kind": "consult-backend",
    # Text that identifies a call as addressing the required dependency.
    "identifiers": [
        r"open[-_ ]brain",
        r"search_brain|list_recent|add_memory",
    ],
    # Evidence that a query actually filters rather than dumping.
    "filters": [
        r"\bWHERE\b", r"\bILIKE\b", r"\bLIKE\b", r"\bSIMILAR\s+TO\b",
        r"\b@@\b", r"\bts_query\b", r"\bsimilarity\b", r"\bto_tsvector\b",
        r"information_schema",
    ],
    "capture_markers": [r"api/add", r"add_memory"],
}

IDENT_RE = re.compile("|".join(PREFLIGHT_RULE["identifiers"]), re.I)
FILTER_RE = re.compile("|".join(PREFLIGHT_RULE["filters"]), re.I)
CAPTURE_RE = re.compile("|".join(PREFLIGHT_RULE["capture_markers"]), re.I)

RULE_CONFIG_HASH = hashlib.sha256(
    json.dumps(PREFLIGHT_RULE, sort_keys=True).encode()).hexdigest()[:8]

# ---------------------------------------------------------------------------
# Agent-facing messages.
#
# These are load-bearing. Incident 4's root cause was message text, so every
# message is hashed into each record via MESSAGE_VERSION -- without that, a
# future message-induced behaviour change cannot be attributed to the message.
#
# RULE: state the requirement and name the configured methods. Never include a
# worked example, and never name an action that does not perform the work.
# ---------------------------------------------------------------------------
MSG_BLOCKED = (
    "PREFLIGHT GATE: this turn has not yet consulted the required backend.\n\n"
    "Satisfy it with a filtered query to the configured backend, through one of\n"
    "the configured clients. A query must carry search terms drawn from what the\n"
    "user actually asked.\n\n"
    "Reading a local file does not satisfy this requirement. Neither does\n"
    "printing text that mentions the backend. The work has to happen."
)

MSG_GENERIC = (
    "PREFLIGHT GATE: query rejected -- no filtering.\n\n"
    "The query reached the backend but retrieves whatever is most recent rather\n"
    "than what the user asked about. Add search terms drawn from the request.\n\n"
    "A filtered query that legitimately returns zero rows does satisfy this\n"
    "gate. Finding nothing is a result; not looking is not."
)

MESSAGE_VERSION = hashlib.sha256(
    (MSG_BLOCKED + MSG_GENERIC).encode()).hexdigest()[:8]


# ===== State ===============================================================
# D5.3: outside /tmp, unguessable, missing state fails closed.
#
# Honest limit: the hook and the agent's shell run as the same uid, so this is
# "not trivially writable", not "unwritable". A hard boundary needs a separate
# uid or a privileged helper. See ARCHITECTURE.md D1 and D5.

def gate_path(session_id: str) -> Path:
    secret = records.install_secret()
    name = hashlib.sha256((secret + "gate" + (session_id or "")).encode()).hexdigest()[:32]
    return records.STATE_DIR / f"{name}.state"


# ===== Classification ======================================================

def classify(tool_name: str, tool_input: dict):
    """Classify a call against the rule.

      "targeted"     -- addresses the dependency with real filtering
      "generic"      -- addresses the dependency without filtering
      "capture-only" -- writes to the dependency; does not satisfy a read
      None           -- unrelated to the rule

    Note what is absent: there is no classification for reading a local file.
    A local read cannot entail a live consultation at any price, so it is not a
    weaker form of satisfaction -- it is simply unrelated (Incident 4,
    requirement F).
    """
    if tool_name == "Bash":
        cmd = (tool_input or {}).get("command", "")
        if not IDENT_RE.search(cmd):
            return None
        if CAPTURE_RE.search(cmd):
            return "capture-only"
        return "targeted" if FILTER_RE.search(cmd) else "generic"

    if tool_name == "WebFetch":
        url = (tool_input or {}).get("url", "")
        if not IDENT_RE.search(url):
            return None
        return "capture-only" if CAPTURE_RE.search(url) else "targeted"

    if "search_brain" in tool_name:
        return "targeted" if (tool_input or {}).get("query", "").strip() else "generic"
    if "list_recent" in tool_name:
        return "generic"
    if "add_memory" in tool_name:
        return "capture-only"

    return None


# ===== Recording ===========================================================

def emit(phase, session_id, tool_name, tool_input, classification, outcome,
         result=None, integrity=None):
    rec = records.build(
        phase=phase, session_id=session_id, tool_name=tool_name,
        tool_input=tool_input, classification=classification, outcome=outcome,
        mode=MODE, rule_id=PREFLIGHT_RULE["rule_id"],
        rule_kind=PREFLIGHT_RULE["rule_kind"],
        rule_config_hash=RULE_CONFIG_HASH, message_version=MESSAGE_VERSION,
        result=result, rule_identifier_re=IDENT_RE, integrity=integrity,
    )
    if not records.write(rec):
        # The record is the product (D6). A write failure is a measurement gap
        # and must not be silent -- but it must also not break the tool call.
        print("preflight: decision record could not be written", file=sys.stderr)


# ===== Phases ==============================================================

def handle_pre(session_id, tool_name, tool_input):
    gate = gate_path(session_id)

    if not gate.exists():
        # v2 allowed here. Failing closed is the D4 posture: a missing
        # precondition is a block, and deletion must not open the gate.
        emit("pre", session_id, tool_name, tool_input, None, "blocked",
             integrity={"missing_state": True})
        if MODE == "enforce":
            print(MSG_BLOCKED + "\n\n(no gate state for this session)", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    if gate.read_text().strip() == "satisfied":
        emit("pre", session_id, tool_name, tool_input, None, "already-satisfied")
        sys.exit(0)

    classification = classify(tool_name, tool_input)

    if classification == "targeted":
        gate.write_text("satisfied")
        emit("pre", session_id, tool_name, tool_input, classification, "satisfies")
        sys.exit(0)

    if classification == "capture-only":
        emit("pre", session_id, tool_name, tool_input, classification,
             "permitted-non-qualifying")
        sys.exit(0)

    if classification == "generic":
        emit("pre", session_id, tool_name, tool_input, classification, "blocked")
        if MODE == "enforce":
            print(MSG_GENERIC, file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    emit("pre", session_id, tool_name, tool_input, None, "blocked")
    if MODE == "enforce":
        print(MSG_BLOCKED, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


def handle_post(session_id, tool_name, tool_input, tool_response):
    """Record the result of a call.

    v3 records only. Flipping satisfaction on a confirmed non-error result is
    D5.1 and lands next; until then this establishes the after-dataset's result
    fields so the before/after comparison has something to compare.
    """
    classification = classify(tool_name, tool_input)
    if classification is None:
        sys.exit(0)

    status = "absent"
    if isinstance(tool_response, dict):
        err = tool_response.get("error") or tool_response.get("is_error")
        status = "error" if err else "ok"
    elif tool_response is not None:
        status = "ok"

    emit("post", session_id, tool_name, tool_input, classification, "observed",
         result={"status": status})
    sys.exit(0)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    session_id = data.get("session_id", "default")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    phase = data.get("hook_event_name", "PreToolUse")

    if phase == "PostToolUse":
        handle_post(session_id, tool_name, tool_input, data.get("tool_response"))
    else:
        handle_pre(session_id, tool_name, tool_input)


if __name__ == "__main__":
    main()
