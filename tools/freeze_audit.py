#!/usr/bin/env python3
"""Derive a shareable, content-free dataset from a raw preflight audit log.

The raw log contains user prompt text, tool arguments, file paths, hostnames and
IP addresses. None of it leaves this script. Only closed-set enums, counts,
booleans and one ratio are emitted.

  python3 tools/freeze_audit.py --in ~/.claude/hooks/ob-audit.log \
                                --out data/before-2026-09-16.jsonl

The session salt is random per run and never stored, so session identifiers
cannot be recovered from the output. Freeze once; the emitted file is the
artifact, not a reproducible derivation.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys

SCHEMA_VERSION = 1

FILTER_RE = re.compile(r"\b(where|ilike|like|match)\b", re.I)
WORD_RE = re.compile(r"[a-z0-9_]{3,}", re.I)
# The rule's own identifier. Presence is recorded as a boolean only -- it is the
# signal that the control is shaping the query text it supervises.
RULE_IDENT_RE = re.compile(r"open[-_ ]brain", re.I)

STOP = {"the", "and", "for", "with", "that", "this", "you", "are", "was", "not",
        "what", "how", "can", "但", "from", "have", "has", "all", "any", "out"}


def tool_kind(name):
    n = (name or "").lower()
    if n == "bash":
        return "shell"
    if n in ("read", "grep", "glob"):
        return "read"
    if n in ("write", "edit"):
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


def terms(text):
    return {w.lower() for w in WORD_RE.findall(text or "")} - STOP


def derive(rec, salt):
    raw_q = rec.get("query") or ""
    prompt = rec.get("prompt") or ""

    read_span = None
    offset_present = False
    pk = None
    # Structured tool input arrives as JSON; shell input is an opaque string.
    try:
        ti = json.loads(raw_q)
        if isinstance(ti, dict):
            read_span = ti.get("limit")
            offset_present = "offset" in ti
            pk = path_kind(ti.get("file_path") or ti.get("path") or ti.get("pattern"))
    except (ValueError, TypeError):
        pass

    qt = terms(raw_q)
    pt = terms(prompt)
    overlap = round(len(qt & pt) / len(pt), 3) if pt else None

    return {
        "schema_version": SCHEMA_VERSION,
        "ts": rec.get("ts"),
        "session_hash": hashlib.sha256((salt + (rec.get("session") or "")).encode()).hexdigest()[:12],
        "tool_kind": tool_kind(rec.get("tool")),
        "classification": rec.get("classification"),
        "verdict": rec.get("verdict"),
        "features": {
            "read_span_lines": read_span if isinstance(read_span, int) else None,
            "read_offset_present": offset_present,
            "path_kind": pk,
            "query_len": len(raw_q),
            "has_filter": bool(FILTER_RE.search(raw_q)),
            "term_count": len(qt),
            "query_contains_rule_identifier": bool(RULE_IDENT_RE.search(raw_q)),
            "prompt_len": len(prompt),
            "prompt_query_term_overlap": overlap,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    args = ap.parse_args()

    salt = secrets.token_hex(16)  # never written anywhere
    rows, skipped = [], 0
    with open(os.path.expanduser(args.src), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rows.append(derive(json.loads(line), salt))
            except ValueError:
                skipped += 1

    with open(args.dst, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    print(f"wrote {len(rows)} records to {args.dst}" + (f" ({skipped} unparsable lines skipped)" if skipped else ""))


if __name__ == "__main__":
    main()
