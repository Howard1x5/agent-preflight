"""Failing tests that reproduce the defects documented in docs/INCIDENTS.md.

These are expected to FAIL against the current implementation. That is the
point: every claim in INCIDENTS.md should be an assertion that a stranger can
run, not a statement they have to take on trust.

When the D5 classification decision is implemented, these become the
acceptance criteria.

Run:  pytest tests/ -v
"""
import importlib.util
import sys
from pathlib import Path

import pytest

GATE_PATH = Path(__file__).resolve().parents[1] / "hooks" / "preflight_gate.py"


def _load_gate():
    """Import preflight_gate.py by path (it is a hook script, not a package)."""
    spec = importlib.util.spec_from_file_location("preflight_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["preflight_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()
classify = gate.classify_ob_query


# --------------------------------------------------------------------------
# Incident 1 — a local file read satisfies a requirement to query a backend
# --------------------------------------------------------------------------

@pytest.mark.xfail(reason="INCIDENT-1: local file read satisfies a live-backend requirement", strict=True)
def test_local_memory_read_must_not_satisfy_backend_requirement():
    """Reading a local file is not evidence the backend was consulted.

    Observed 2026-09-10..13: the backend was unreachable for four days while
    the gate was satisfied every turn by reads of a local MEMORY.md.
    """
    result = classify("Read", {"file_path": "~/.claude/memory/MEMORY.md"})
    assert result != "read-memory", (
        "a local file read currently satisfies the gate, so the control passes "
        "while its dependency is entirely unavailable"
    )


# --------------------------------------------------------------------------
# Incident 1 (compounding) — a legitimate backend query is blocked
# --------------------------------------------------------------------------

@pytest.mark.xfail(reason="INCIDENT-1b: real backend query blocked for lacking a literal substring", strict=True)
def test_real_backend_query_must_not_be_blocked_for_naming():
    """A genuine API query should satisfy the gate regardless of URL wording.

    Observed: a POST to /api/search was blocked because the command text did
    not contain the literal string "open-brain".
    """
    cmd = ("curl -s -X POST http://memory-backend.example/api/search "
           "-H 'Content-Type: application/json' "
           "-d '{\"query\":\"deployment history\",\"limit\":5}'")
    result = classify("Bash", {"command": cmd})
    assert result == "targeted", (
        f"a real targeted backend query classified as {result!r}; the control "
        "blocks correct behaviour because of a substring mismatch"
    )


# --------------------------------------------------------------------------
# Spoofability — the gate opens on a command that does no work
# --------------------------------------------------------------------------

@pytest.mark.xfail(reason="SPOOF: echo of a tool name opens the gate", strict=True)
def test_echo_must_not_satisfy_the_gate():
    """Printing the name of a required tool is not performing the work."""
    result = classify("Bash", {"command": "echo search_brain"})
    assert result != "targeted", (
        "`echo search_brain` currently satisfies the gate; a control that opens "
        "on a printed string is a convention, not a control"
    )


@pytest.mark.xfail(reason="SPOOF: echoing SQL keywords opens the gate", strict=True)
def test_echoed_sql_keywords_must_not_satisfy_the_gate():
    """Filter keywords in arbitrary text are not a filtered query."""
    result = classify("Bash", {"command": "echo 'open-brain WHERE ILIKE'"})
    assert result != "targeted", (
        "echoing SQL keywords satisfies the gate without any query executing"
    )


# --------------------------------------------------------------------------
# Behaviour that is currently CORRECT — these should pass, and keep passing
# --------------------------------------------------------------------------

def test_generic_dump_is_not_targeted():
    """A query with no filtering must not satisfy the gate."""
    cmd = "psql -c 'SELECT * FROM memories ORDER BY created_at DESC LIMIT 10' open-brain"
    assert classify("Bash", {"command": cmd}) == "generic"


def test_write_does_not_satisfy_a_read_requirement():
    """Capture-only calls are permitted but must not open a read gate."""
    assert classify("mcp__add_memory", {"text": "a note"}) == "capture-only"


def test_unrelated_command_is_not_classified():
    """Commands with nothing to do with the requirement return None."""
    assert classify("Bash", {"command": "ls -la /tmp"}) is None
