#!/usr/bin/env python3
"""Rule loading, shell parsing and classification for agent-preflight.

Two things here are load-bearing.

RULES ARE DATA (D5.4). A rule names its configured clients and endpoints. The
gate matches calls against those, not against a project name appearing in
command text. v2 matched on the literal string "open-brain", which blocked
genuine queries whose URL lacked it (Incident 1b) and pressured agents into
injecting that word into semantic search strings so the gate would open.

SHELL IS ADMITTED BY PARSING, NEVER BY DENYLIST (D5.6). A denylist over raw
command text is the same class of unsound control this project documents: miss
one separator and `curl <configured endpoint> ; anything-else` passes an
argv[0] check while doing something else. The rule is an allowlist -- a single
simple command whose argv[0] is a configured client. Everything else is
unrelated to the rule and goes through the shipped wrapper.
"""

import json
import os
import re
import shlex
from pathlib import Path

RULES_DIR = Path.home() / ".claude" / "state" / "agent-preflight" / "rules"
BUNDLED = Path(__file__).resolve().parent.parent / "rules" / "consult-backend.example.json"

# Constructs `shlex` does NOT surface as separate tokens, verified empirically:
#   newline  -> consumed as whitespace, so `curl <ok>\nrm -rf x` tokenizes as
#               one command and a token-operator check never sees the break
#   backtick -> stays glued inside a word ('`echo', 'http://h`/api/search')
# These two are checked on the raw string because the tokenizer provably cannot
# report them. Everything else is decided structurally, on tokens.
_RAW_DISQUALIFIERS = ("\n", "\r", "`", "$(", "${", "<(", ">(")


def load_rule(rule_id="consult-backend"):
    """Load a rule. Local config wins; the bundled example is the fallback."""
    for candidate in (RULES_DIR / f"{rule_id}.json", BUNDLED):
        try:
            if candidate.exists():
                d = json.loads(candidate.read_text())
                return _compile(d)
        except (OSError, ValueError):
            continue
    return None


def _compile(d):
    d = dict(d)
    d["_filter_re"] = re.compile("|".join(d.get("filter_evidence") or ["(?!)"]), re.I)
    d["_clients"] = set(d.get("clients") or [])
    d["_endpoints"] = [e.lower() for e in (d.get("endpoints") or [])]
    d["_capture_paths"] = [p.lower() for p in (d.get("capture_paths") or [])]
    return d


def parse_simple_command(cmd):
    """argv for a single simple command, or None if it is anything else.

    Returns None for compound commands, substitutions, redirections,
    assignment prefixes and multi-line input. None means "not interpreted",
    which means "does not satisfy" -- never "assumed harmless".
    """
    if not cmd or any(bad in cmd for bad in _RAW_DISQUALIFIERS):
        return None
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        tokens = list(lx)
    except ValueError:
        return None          # unbalanced quotes: unparseable, so unqualified
    if not tokens:
        return None
    # Any token that is pure punctuation is an operator: ; | & && > < ( ) etc.
    for t in tokens:
        if t and all(c in lx.punctuation_chars for c in t):
            return None
    # An assignment prefix (FOO=1 cmd ...) means argv[0] is not what it appears.
    if "=" in tokens[0] and not tokens[0].startswith("-"):
        return None
    return tokens


def _addresses_endpoint(text, rule):
    t = (text or "").lower()
    return any(e in t for e in rule["_endpoints"])


def _is_capture(text, rule):
    t = (text or "").lower()
    return any(p in t for p in rule["_capture_paths"])


def classify(tool_name, tool_input, rule):
    """Classify a call against a rule.

      "targeted"     -- addresses the configured dependency, with filtering
      "generic"      -- addresses it, no filtering
      "capture-only" -- writes to it; never satisfies a read requirement
      None           -- unrelated to this rule

    There is no classification for reading a local file. A local read cannot
    entail a live consultation at any price, so it is unrelated rather than a
    weaker form of satisfying (Incident 4, requirement F).
    """
    if rule is None:
        return None
    tool_input = tool_input or {}

    if tool_name == "Bash":
        argv = parse_simple_command(tool_input.get("command", ""))
        if not argv or argv[0] not in rule["_clients"]:
            return None
        args = " ".join(argv[1:])
        if not _addresses_endpoint(args, rule):
            return None
        if _is_capture(args, rule):
            return "capture-only"
        # Filtering is judged on the ARGUMENTS, never on the raw shell string.
        return "targeted" if rule["_filter_re"].search(args) else "generic"

    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        if not _addresses_endpoint(url, rule):
            return None
        return "capture-only" if _is_capture(url, rule) else "targeted"

    name = tool_name or ""
    if any(t in name for t in rule.get("mcp_search_tools", [])):
        return "targeted" if str(tool_input.get("query", "")).strip() else "generic"
    if any(t in name for t in rule.get("mcp_generic_tools", [])):
        return "generic"
    if any(t in name for t in rule.get("mcp_capture_tools", [])):
        return "capture-only"

    return None


def confirms(tool_response, rule):
    """Does this result confirm a real consultation?

    Non-error, NOT non-empty: a filtered search correctly returning zero rows
    is diligence, not failure.

    But a non-error status alone is exactly what Incident 2 forged -- an
    exception handler returned a well-formed default and the write succeeded,
    HTTP 200, no alert. So where the rule declares an expected response shape,
    the response must actually parse into it.

    Returns (ok: bool, reason: str).
    """
    if tool_response is None:
        return False, "absent"
    if isinstance(tool_response, dict):
        if tool_response.get("error") or tool_response.get("is_error"):
            return False, "error"
    text = tool_response if isinstance(tool_response, str) else \
        (tool_response.get("stdout") or tool_response.get("output") or "") \
        if isinstance(tool_response, dict) else ""

    expect = (rule or {}).get("expect_response") or {}
    if expect.get("format") == "json":
        if not str(text).strip():
            return False, "empty-body"
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return False, "unparseable"
        for k in expect.get("required_keys") or []:
            if isinstance(parsed, dict) and k not in parsed:
                return False, "shape-mismatch"
        return True, "ok"
    return True, "ok"
