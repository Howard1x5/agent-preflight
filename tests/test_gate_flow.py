"""End-to-end flow: the gate is driven as the harness drives it.

Classification tests do not exercise the part that actually decides anything --
the pre/post handshake, the failure counter, the degraded state, the receipt.
These run the hook as a subprocess with JSON on stdin, against a throwaway HOME.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "hooks" / "preflight_gate.py"
EP = "memory-backend.example"
GOOD = f"curl -s -X POST http://{EP}/api/search -d '{{\"query\":\"deploys\"}}'"


@pytest.fixture
def home(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    return env


def run(env, payload):
    p = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stderr, p.stdout


def operator_text(stdout):
    """What the operator actually sees: systemMessage on JSON stdout.

    Asserting on stderr would pass while the message reached nobody.
    """
    try:
        return json.loads(stdout).get("systemMessage", "")
    except (ValueError, AttributeError):
        return ""


def state_file(env, session="s1"):
    d = Path(env["HOME"]) / ".claude" / "state" / "agent-preflight"
    return [f for f in d.glob("*.state")] if d.exists() else []


def seed(env, session="s1", **kw):
    """Create gate state the way the turn-start hook would.

    The path derives from a per-install secret under $HOME, so it MUST be
    computed inside the same environment the hook will run in -- computing it
    in the parent process resolves against the real home directory and writes
    state the subprocess can never find.
    """
    st = {"state": "pending", "failures": 0, "degraded": False}
    st.update(kw)
    code = (
        "import sys, json, pathlib\n"
        f"sys.path.insert(0, {str(GATE.parent)!r})\n"
        "import preflight_gate as g\n"
        f"p = g.gate_path({session!r})\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        f"p.write_text(json.dumps({st!r}))\n"
        "print(p)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, check=True)
    return Path(out.stdout.strip())


def pre(env, cmd, session="s1", tuid="t1"):
    return run(env, {"hook_event_name": "PreToolUse", "session_id": session,
                     "tool_name": "Bash", "tool_input": {"command": cmd},
                     "tool_use_id": tuid})


def post(env, cmd, response, session="s1", tuid="t1"):
    return run(env, {"hook_event_name": "PostToolUse", "session_id": session,
                     "tool_name": "Bash", "tool_input": {"command": cmd},
                     "tool_response": response, "tool_use_id": tuid})


# --- fail closed -----------------------------------------------------------

def test_missing_state_blocks(home):
    """v2 failed open here, so deleting one file disabled enforcement."""
    code, err, _ = pre(home, GOOD)
    assert code == 2
    assert "no gate state" in err


# --- the pre/post handshake ------------------------------------------------

def test_targeted_request_alone_does_not_open_the_gate(home):
    """The core of D5.1: a request is admitted, not accepted.

    v2 and v3 satisfied here. That is why 689 records could not establish that
    any query ran.
    """
    p = seed(home)
    code, _, _ = pre(home, GOOD)
    assert code == 0
    st = json.loads(p.read_text())
    assert st["state"] == "candidate", "the gate must not be satisfied pre-execution"


def test_confirmed_result_opens_the_gate(home):
    p = seed(home)
    pre(home, GOOD)
    code, _, _ = post(home, GOOD, '{"results": [{"id": 1}]}')
    assert code == 0
    assert json.loads(p.read_text())["state"] == "satisfied"


def test_zero_results_still_opens_the_gate(home):
    """Non-error, not non-empty. Finding nothing is diligence."""
    p = seed(home)
    pre(home, GOOD)
    post(home, GOOD, '{"results": []}')
    assert json.loads(p.read_text())["state"] == "satisfied"


def test_wellformed_failure_does_not_open_the_gate(home):
    """Incident 2: an exception handler returned a valid-looking default and
    the write succeeded, HTTP 200, no alert."""
    p = seed(home)
    pre(home, GOOD)
    code, _, out = post(home, GOOD, '{"error": "connection refused"}')
    st = json.loads(p.read_text())
    assert st["state"] != "satisfied"
    assert st["failures"] == 1
    assert "did not confirm" in operator_text(out)


def test_confirmation_is_bound_to_the_admitted_call(home):
    """A different call's result must not satisfy the candidate."""
    p = seed(home)
    pre(home, GOOD, tuid="t1")
    post(home, GOOD, '{"results": []}', tuid="OTHER")
    assert json.loads(p.read_text())["state"] != "satisfied"


# --- failure counter and degraded state ------------------------------------

def test_failures_accumulate_then_degrade(home):
    """D4: 1-3 block with a retry message, the 4th degrades."""
    p = seed(home)
    for i in range(1, 4):
        pre(home, GOOD, tuid=f"t{i}")
        _, _, out = post(home, GOOD, '{"error": "down"}', tuid=f"t{i}")
        assert "Consecutive failures" in operator_text(out)
        assert json.loads(p.read_text())["degraded"] is False, f"degraded too early at {i}"
    pre(home, GOOD, tuid="t4")
    _, _, out = post(home, GOOD, '{"error": "down"}', tuid="t4")
    st = json.loads(p.read_text())
    assert st["failures"] == 4 and st["degraded"] is True
    assert "DEGRADED" in operator_text(out)


def test_degraded_allows_but_says_so_every_time(home):
    """Eight silent days was Incident 1. Degraded must never be quiet."""
    seed(home, failures=4, degraded=True)
    code, _, out = pre(home, "ls -la")
    assert code == 0, "degraded must not brick the session"
    msg = operator_text(out)
    assert "DEGRADED" in msg and "NOT in effect" in msg


def test_a_confirmation_clears_degraded(home):
    p = seed(home, failures=4, degraded=True)
    # pre() short-circuits while degraded, so drive the post phase directly
    p.write_text(json.dumps({"state": "candidate", "candidate_id": "t9",
                             "failures": 4, "degraded": True}))
    post(home, GOOD, '{"results": []}', tuid="t9")
    st = json.loads(p.read_text())
    assert st["state"] == "satisfied" and st["failures"] == 0 and st["degraded"] is False


# --- compound commands -----------------------------------------------------

def test_compound_command_gets_the_specific_message(home):
    seed(home)
    code, err, _ = pre(home, f"curl http://{EP}/api/search ; rm -rf /tmp/x")
    assert code == 2
    assert "not interpreted" in err and "wrapper" in err


# --- receipt ---------------------------------------------------------------

def test_receipt_is_compact_when_healthy(home):
    seed(home)
    pre(home, GOOD)
    post(home, GOOD, '{"results": []}')
    code, _, out = run(home, {"hook_event_name": "Stop", "session_id": "s1"})
    assert code == 0
    msg = operator_text(out)
    assert "confirmed consultation" in msg, "receipt must reach the OPERATOR channel"
    assert "attention required" not in msg


def test_receipt_escalates_when_unconfirmed(home):
    seed(home)
    pre(home, GOOD)
    post(home, GOOD, '{"error": "down"}')
    _, _, out = run(home, {"hook_event_name": "Stop", "session_id": "s1"})
    msg = operator_text(out)
    assert "attention required" in msg
    assert "UNCONFIRMED" in msg


def test_receipt_reports_a_measurement_gap(home):
    """A missing record is itself a finding; the receipt must not read clean."""
    seed(home)
    pre(home, GOOD)
    post(home, GOOD, '{"error": "down"}')
    run(home, {"hook_event_name": "PreToolUse", "session_id": "s2",
               "tool_name": "Bash", "tool_input": {"command": "ls"}})
    _, _, out = run(home, {"hook_event_name": "Stop", "session_id": "s2"})
    assert "INCOMPLETE" in operator_text(out)
