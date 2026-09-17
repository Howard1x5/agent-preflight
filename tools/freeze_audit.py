#!/usr/bin/env python3
"""Derive a shareable, content-free dataset from a raw v2 audit log.

The raw log contains user prompt text, tool arguments, file paths, hostnames and
IP addresses. None of it leaves this script. Feature derivation is delegated to
`hooks/records.py` -- the same code the live hook uses -- so the pre- and
post-intervention datasets are comparable by construction rather than by
assertion.

  python3 tools/freeze_audit.py --in ~/.claude/hooks/ob-audit.log \
                                --out data/before-2026-09-16.jsonl

The session salt is random per run and never stored, so session identifiers
cannot be recovered. Freeze once; the emitted file is the artifact.
"""

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import records  # noqa: E402

# v2 wrote one flat record per classified decision. v3 records carry phase,
# mode, rule and result fields that did not exist then; they are absent here
# rather than invented, and schema_version distinguishes the two.
SCHEMA_VERSION = 1

RULE_IDENT_RE = re.compile(r"open[-_ ]brain|search_brain|list_recent|add_memory", re.I)


def reconstruct_tool_input(tool, query):
    """v2 stored a serialized tool input. Rebuild enough of it to derive from.

    Bash and WebFetch stored the raw string; everything else stored JSON that
    may be truncated at 300 chars and therefore unparseable.
    """
    if tool == "Bash":
        return {"command": query}
    if tool == "WebFetch":
        return {"url": query}
    try:
        val = json.loads(query)
        return val if isinstance(val, dict) else {}
    except ValueError:
        return {}


def derive(rec, salt):
    tool = rec.get("tool") or ""
    tool_input = reconstruct_tool_input(tool, rec.get("query") or "")
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": rec.get("ts"),
        "session_hash": records.session_hash(rec.get("session") or "", salt),
        "tool_kind": records.tool_kind(tool),
        "classification": rec.get("classification"),
        "verdict": rec.get("verdict"),
        "features": records.features(
            tool, tool_input, rec.get("prompt") or "", RULE_IDENT_RE),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--until", dest="until",
                    help="ISO instant; records at or after it are excluded. "
                         "Makes the snapshot reproducible as the source log grows.")
    args = ap.parse_args()

    salt = secrets.token_hex(16)  # never written anywhere
    rows, skipped, excluded = [], 0, 0
    with open(os.path.expanduser(args.src), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if args.until and (rec.get("ts") or "") >= args.until:
                excluded += 1
                continue
            rows.append(derive(rec, salt))

    with open(args.dst, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    print(f"wrote {len(rows)} records to {args.dst}"
          + (f", {excluded} at/after --until excluded" if excluded else "")
          + (f", {skipped} unparsable lines skipped" if skipped else ""))


if __name__ == "__main__":
    main()
