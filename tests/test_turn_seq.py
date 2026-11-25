"""Every decision record carries the turn it belongs to.

Measured gap (rehearsal, 2026-10-02..11-25): the log had no turn id, so
"what share of turns would block" could only be answered by joining the log
against harness transcripts, and 2 of 6 sessions could not be joined at all.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


@pytest.fixture
def env(tmp_path):
    return dict(os.environ, HOME=str(tmp_path))


def run(hook, env, payload):
    return subprocess.run([sys.executable, str(HOOKS / hook)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def prompt(env, session):
    run("preflight_init.py", env, {"session_id": session, "prompt": "hi"})


def tool(env, session):
    run("preflight_gate.py", env, {"hook_event_name": "PreToolUse", "session_id": session,
                                    "tool_name": "Edit", "tool_input": {"file_path": "/x"},
                                    "tool_use_id": "t"})


def records(env):
    f = Path(env["HOME"]) / ".claude" / "state" / "agent-preflight" / "decisions.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def test_turn_counter_increments_per_prompt(env):
    prompt(env, "s1")
    tool(env, "s1")
    tool(env, "s1")
    prompt(env, "s1")
    tool(env, "s1")
    assert [r["turn_seq"] for r in records(env)] == [1, 1, 2]


def test_turn_counter_is_per_session(env):
    prompt(env, "a")
    prompt(env, "a")
    prompt(env, "b")
    tool(env, "a")
    tool(env, "b")
    assert [r["turn_seq"] for r in records(env)] == [2, 1]


def test_schema_version_marks_the_new_field(env):
    prompt(env, "s1")
    tool(env, "s1")
    r = records(env)[0]
    assert r["schema_version"] >= 3 and "turn_seq" in r


def test_turn_counter_survives_degraded_carryover(env):
    # The counter rides in the same state as failures/degraded, which the init
    # hook deliberately carries across turns (Incident 1). It must not reset them.
    prompt(env, "s1")
    p = next((Path(env["HOME"]) / ".claude" / "state" / "agent-preflight").glob("*.state"))
    st = json.loads(p.read_text())
    st.update({"failures": 3, "degraded": True})
    p.write_text(json.dumps(st))
    prompt(env, "s1")
    st = json.loads(p.read_text())
    assert (st["turn"], st["failures"], st["degraded"]) == (2, 3, True)
