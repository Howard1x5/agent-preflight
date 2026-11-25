#!/usr/bin/env python3
"""Turn-start hook: seed gate state for the session.

The gate fails closed on missing state (D5.3), so something has to create it
at the start of each turn. That is this.

Seeding is idempotent per turn and never clears a degraded flag or the failure
counter -- an outage must survive the turn boundary, or an eight-day
degradation reads as eight fresh healthy starts. Incident 1 is exactly that
failure.

Register for UserPromptSubmit in ~/.claude/settings.json.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight_gate as gate  # noqa: E402


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    session_id = data.get("session_id", "default")
    p = gate.gate_path(session_id)
    prior = gate.read_state(p) or {}

    st = {
        "state": "pending",
        "candidate_id": "",
        # Carried across the turn boundary on purpose.
        "failures": prior.get("failures", 0),
        "degraded": prior.get("degraded", False),
        # Counts user prompts in this session so every decision can be
        # grouped by turn. Without it, "how many turns would block" could
        # only be rebuilt by joining against harness transcripts.
        "turn": prior.get("turn", 0) + 1,
    }
    # A prompt can arrive while a query is still running: a message the user
    # sends mid-turn fires this hook too. The new prompt resets the turn, so
    # that query no longer counts -- it was chosen before the user said more.
    # Its id is kept only so the post phase can record it as superseded
    # rather than as an unexplained "observed".
    if prior.get("state") == "candidate" and prior.get("candidate_id"):
        st["superseded_id"] = prior["candidate_id"]
    gate.write_state(p, st)
    sys.exit(0)


if __name__ == "__main__":
    main()
