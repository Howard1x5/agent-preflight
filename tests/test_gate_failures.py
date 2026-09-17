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
classify = gate.classify


# --------------------------------------------------------------------------
# Incident 1 — a local file read satisfies a requirement to query a backend
# --------------------------------------------------------------------------

def test_local_memory_read_must_not_satisfy_backend_requirement():
    """Reading a local file is not evidence the backend was consulted.

    Observed 2026-09-07..14: the backend was unreachable for eight days while
    the gate was satisfied on every turn by reads of a local MEMORY.md.

    Fixed in v3 -- the classifier has no local-read branch. A local read cannot
    entail a live consultation, so it is unrelated to the rule rather than a
    weaker form of satisfying it.
    """
    result = classify("Read", {"file_path": ".claude/memory/MEMORY.md"})
    assert result is None, (
        f"a local file read classified as {result!r}; it must be unrelated to a "
        "live-backend requirement, not a lesser way of meeting it"
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

def test_echo_must_not_satisfy_the_gate():
    """Printing the name of a required tool is not performing the work.

    Fixed in v3 by deleting the "tool name appears in the command" shortcut.
    Note this is a partial fix: the command is still *classified* rather than
    verified, so it lands on "generic" instead of being rejected outright.
    Full closure needs post-execution confirmation (D5.1).
    """
    result = classify("Bash", {"command": "echo search_brain"})
    assert result != "targeted", (
        "`echo search_brain` satisfies the gate; a control that opens on a "
        "printed string is a convention, not a control"
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


# --------------------------------------------------------------------------
# Incident 4 — the control must not advertise its own bypass
#
# The root cause was message text, not classifier logic. These pin it.
# --------------------------------------------------------------------------

def test_no_classification_accepts_a_local_read():
    """No input of any shape may produce a local-read satisfaction."""
    for tool, inp in [
        ("Read", {"file_path": ".claude/memory/MEMORY.md"}),
        ("Grep", {"path": ".claude/projects/x/memory/note.md"}),
        ("Glob", {"pattern": "**/memory/*.md"}),
        ("Read", {"file_path": "open-brain/notes.md"}),
    ]:
        assert classify(tool, inp) is None, f"{tool} {inp} must not satisfy"


def test_messages_never_name_a_non_performing_action():
    """The block message must not offer an action that does no work.

    Incident 4: the v2 message listed "Read local memory files" as method 2 of
    3. It was the cheapest listed method, so agents took it -- 115 times.
    """
    for msg in (gate.MSG_BLOCKED, gate.MSG_GENERIC):
        low = msg.lower()
        assert "read local memory" not in low
        assert "memory/*.md" not in low
        assert ".claude/projects" not in low


def test_messages_contain_no_worked_example():
    """A worked example is a copyable minimal satisfying shape.

    v2's generic-rejection message handed the agent
    `WHERE summary ILIKE '%keyword_from_user_request%'` -- the same defect as
    the block message, in a second place.
    """
    for msg in (gate.MSG_BLOCKED, gate.MSG_GENERIC):
        assert "example:" not in msg.lower()
        assert "ILIKE '%" not in msg
        assert "SELECT " not in msg


def test_every_message_is_versioned_into_records():
    """Message text is load-bearing, so it must be attributable in the data."""
    assert gate.MESSAGE_VERSION
    import hashlib
    expected = hashlib.sha256(
        (gate.MSG_BLOCKED + gate.MSG_GENERIC).encode()).hexdigest()[:8]
    assert gate.MESSAGE_VERSION == expected, (
        "MESSAGE_VERSION must be derived from the live message text, or a "
        "message-induced behaviour change cannot be attributed to the message"
    )


# --------------------------------------------------------------------------
# D5.3 — state handling
# --------------------------------------------------------------------------

def test_gate_state_is_not_in_tmp_and_is_unguessable():
    """v2 used /tmp/claude-ob-gate-{session_id}: predictable and world-listable."""
    p = gate.gate_path("abc-123")
    assert "/tmp/" not in str(p), "gate state must not live in /tmp"
    assert "abc-123" not in p.name, "the filename must not embed the session id"


def test_records_carry_no_free_text():
    """The record schema is shareable only if it holds no content."""
    import records
    rec = records.build(
        phase="pre", session_id="s1", tool_name="Bash",
        tool_input={"command": "curl http://secret.host/api/search -d 'password'"},
        classification="targeted", outcome="satisfies",
        prompt="my private prompt about a personal matter",
    )
    blob = str(rec)
    for leak in ("secret.host", "password", "private prompt", "personal matter", "curl"):
        assert leak not in blob, f"record leaked {leak!r}"
