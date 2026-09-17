#!/usr/bin/env python3
"""Content-free decision records for agent-preflight.

Every enforcement decision produces one record. Records carry closed-set enums,
counts, booleans and one ratio -- never prompt text, tool arguments, paths,
hostnames or addresses. This module is the single derivation used by both the
live hook and `tools/freeze_audit.py`, so pre- and post-intervention datasets
are produced by identical code and remain comparable.

No runtime dependencies. Every write is best-effort: a logging failure must
never break a tool call, but it is recorded as a measurement-integrity flag so
a gap is visible rather than silent.
"""

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

STATE_DIR = Path.home() / ".claude" / "state" / "agent-preflight"
RECORD_FILE = STATE_DIR / "decisions.jsonl"
SECRET_FILE = STATE_DIR / "install.secret"

FILTER_RE = re.compile(r"\b(where|ilike|like|match|similar\s+to|ts_query|to_tsvector)\b", re.I)
WORD_RE = re.compile(r"[a-z0-9_]{3,}", re.I)
STOP = {"the", "and", "for", "with", "that", "this", "you", "are", "was", "not",
        "what", "how", "can", "from", "have", "has", "all", "any", "out"}


def _install_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return STATE_DIR


def install_secret():
    """Per-install secret. Used to derive unguessable state filenames and to
    salt session hashes so records cannot be linked back to session ids.

    Readable by anything running as this user, including the agent's own shell.
    That is a known and documented limit: this is a cooperative-agent boundary
    (ARCHITECTURE.md D1), not a defence against a hostile operator.
    """
    try:
        _install_dir()
        if not SECRET_FILE.exists():
            fd = os.open(SECRET_FILE, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        return SECRET_FILE.read_text().strip()
    except OSError:
        return ""


def session_hash(session_id, secret=None):
    s = secret if secret is not None else install_secret()
    return hashlib.sha256((s + (session_id or "")).encode()).hexdigest()[:12]


def tool_kind(name):
    n = (name or "").lower()
    if n == "bash":
        return "shell"
    if n in ("read", "grep", "glob"):
        return "read"
    if n in ("write", "edit", "notebookedit"):
        return "write"
    if n == "webfetch":
        return "fetch"
    if "mcp" in n or n in ("search_brain", "list_recent", "add_memory"):
        return "mcp"
    return "other"


def path_kind(path):
    """Category only. The path itself is never emitted."""
    if not path:
        return None
    p = path.lower()
    if p.endswith("memory.md"):
        return "memory-index"
    if "/memory/" in p and p.endswith(".md"):
        return "memory-note"
    return "other"


def _terms(text):
    return {w.lower() for w in WORD_RE.findall(text or "")} - STOP


def features(tool_name, tool_input, prompt="", rule_identifier_re=None):
    """Derive structural features. Consumes text; emits none of it."""
    if tool_name == "Bash":
        raw = (tool_input or {}).get("command", "")
    elif tool_name == "WebFetch":
        raw = (tool_input or {}).get("url", "")
    else:
        raw = json.dumps(tool_input or {}, sort_keys=True)

    read_span, offset_present, pk = None, False, None
    if isinstance(tool_input, dict):
        lim = tool_input.get("limit")
        read_span = lim if isinstance(lim, int) else None
        offset_present = "offset" in tool_input
        pk = path_kind(tool_input.get("file_path") or tool_input.get("path")
                       or tool_input.get("pattern"))

    qt, pt = _terms(raw), _terms(prompt)
    return {
        "read_span_lines": read_span,
        "read_offset_present": offset_present,
        "path_kind": pk,
        "query_len": len(raw),
        "has_filter": bool(FILTER_RE.search(raw)),
        "term_count": len(qt),
        "query_contains_rule_identifier": bool(rule_identifier_re.search(raw)) if rule_identifier_re else False,
        "prompt_len": len(prompt or ""),
        "prompt_query_term_overlap": round(len(qt & pt) / len(pt), 3) if pt else None,
    }


def build(phase, session_id, tool_name, tool_input, classification, outcome,
          mode="enforce", rule_id="consult-backend", rule_kind="consult-backend",
          rule_config_hash="", message_version="", consecutive_failures=0,
          result=None, prompt="", rule_identifier_re=None, secret=None,
          integrity=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,                       # pre | post
        "mode": mode,                         # observe | enforce
        "session_hash": session_hash(session_id, secret),
        "rule_id": rule_id,
        "rule_kind": rule_kind,
        "rule_config_hash": rule_config_hash,
        "message_version": message_version,   # attribute behaviour to message text
        "tool_kind": tool_kind(tool_name),
        "classification": classification,
        "outcome": outcome,
        "consecutive_failures": consecutive_failures,
        "result": result,                     # filled in the post phase
        "features": features(tool_name, tool_input, prompt, rule_identifier_re),
        "integrity": integrity or {},         # non-empty means the record is incomplete
    }


def write(rec, path=None):
    """Append one record. Never raises."""
    try:
        _install_dir()
        target = Path(path) if path else RECORD_FILE
        with target.open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return True
    except OSError:
        return False
