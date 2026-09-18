#!/usr/bin/env python3
"""Status line: the persistent operator surface.

The session receipt only fires at turn end. A degraded state that started two
hours ago should be visible the whole time, not announced once when you stop.

UNVERIFIED ASSUMPTION, STATED RATHER THAN HIDDEN: the `systemMessage` channel
used by the receipt was confirmed against the harness before use. This one was
not -- the status-line stdin payload could not be read out of the binary. So
this reads whatever JSON arrives, uses `session_id` when present, and falls
back to an install-wide view when it is absent. It prints one line either way.
If the payload turns out not to carry `session_id`, the fallback is what you
will see, and the line will say so.

Configure in ~/.claude/settings.json:
  "statusLine": {"type": "command",
                 "command": "python3 ~/.../hooks/preflight_status.py"}
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import records  # noqa: E402
import rules as R  # noqa: E402

RECENT = 200            # records scanned in the fallback view


def load_recent(limit=RECENT):
    try:
        lines = records.RECORD_FILE.read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    rule = R.load_rule("consult-backend")
    if rule is None:
        print("preflight: no rule configured")
        return
    mode = rule.get("mode", "enforce")

    rows = load_recent()
    if not rows:
        print(f"preflight {mode}: no records yet")
        return

    session_id = data.get("session_id")
    scoped = False
    if session_id:
        sh = records.session_hash(session_id)
        mine = [r for r in rows if r.get("session_hash") == sh]
        if mine:
            rows, scoped = mine, True

    confirmed = sum(1 for r in rows if r.get("outcome") == "satisfies")
    blocked = sum(1 for r in rows if r.get("outcome") == "blocked")
    unconfirmed = sum(1 for r in rows if r.get("outcome") == "unconfirmed")
    degraded = sum(1 for r in rows if r.get("outcome") == "degraded-allow")

    scope = "session" if scoped else f"last {len(rows)}"

    # Degradation is the one thing that must never be a small number in a row
    # of small numbers. It is the eight-day failure, made visible continuously.
    if degraded:
        print(f"!! PREFLIGHT DEGRADED — not enforcing ({degraded} allowed through, {scope})")
        return
    if unconfirmed:
        print(f"preflight {mode}: {confirmed} confirmed, {unconfirmed} UNCONFIRMED ({scope})")
        return
    verb = "would-block" if mode == "observe" else "blocked"
    print(f"preflight {mode}: {confirmed} confirmed, {blocked} {verb} ({scope})")


if __name__ == "__main__":
    main()
