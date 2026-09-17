"""The report turns records into something an operator reads and an export
that can be combined across installs without pooling incomparable things."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "report.py"


def rec(**kw):
    base = {"schema_version": 2, "ts": "2026-09-17T12:00:00+00:00",
            "phase": "pre", "mode": "enforce", "session_hash": "aaa",
            "rule_id": "consult-backend", "rule_kind": "consult-backend",
            "rule_config_hash": "cfg1", "message_version": "msg1",
            "tool_kind": "shell", "classification": "targeted",
            "outcome": "satisfies", "consecutive_failures": 0,
            "result": None, "features": {}, "integrity": {}}
    base.update(kw)
    return base


@pytest.fixture
def recfile(tmp_path):
    def _write(rows):
        p = tmp_path / "decisions.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return p
    return _write


def run(*args):
    p = subprocess.run([sys.executable, str(TOOL), *args],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_summary_counts_confirmations(recfile):
    p = recfile([rec(), rec(), rec(outcome="blocked")])
    out = run("--records", str(p), "--summary")
    assert "confirmed          2" in out
    assert "blocked" in out


def test_dangling_candidate_is_surfaced(recfile):
    """A candidate admitted and never resolved is a turn that proceeded on a
    request rather than a result -- the v2 failure mode."""
    p = recfile([rec(outcome="candidate-admitted"),
                 rec(outcome="candidate-admitted"),
                 rec(outcome="satisfies")])
    out = run("--records", str(p), "--summary")
    assert "DANGLING" in out and "attention" in out.lower()


def test_degraded_allows_are_called_unenforced(recfile):
    p = recfile([rec(outcome="degraded-allow")])
    out = run("--records", str(p), "--summary")
    assert "UNENFORCED ALLOWS" in out


def test_integrity_gaps_are_never_silent(recfile):
    p = recfile([rec(integrity={"missing_state": True})])
    out = run("--records", str(p), "--summary")
    assert "INCOMPLETE" in out


def test_unparsable_lines_are_reported_not_skipped(recfile, tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps(rec()) + "\nnot json at all\n")
    out = run("--records", str(p), "--summary")
    assert "UNPARSABLE" in out


def test_since_filters(recfile):
    p = recfile([rec(ts="2026-09-16T00:00:00+00:00"),
                 rec(ts="2026-09-18T00:00:00+00:00")])
    out = run("--records", str(p), "--summary", "--since", "2026-09-17")
    assert "records            1" in out


# --- export ----------------------------------------------------------------

def test_export_carries_counts_only(recfile, tmp_path):
    p = recfile([rec(features={"query_len": 120, "prompt_len": 40}),
                 rec(outcome="blocked")])
    dst = tmp_path / "bundle.json"
    run("--records", str(p), "--export", str(dst))
    b = json.loads(dst.read_text())
    blob = json.dumps(b)
    assert "features" not in blob, "per-decision features must not be exported"
    assert "session_hash" not in blob, "no per-session identifiers in an export"
    assert "query_len" not in blob
    assert sum(c["n"] for c in b["counts"]) == 2


def test_export_does_not_pool_different_rule_contracts(recfile, tmp_path):
    """Two installs on different rule configs are not measuring the same thing."""
    p = recfile([rec(rule_config_hash="cfg1"), rec(rule_config_hash="cfg2")])
    dst = tmp_path / "b.json"
    run("--records", str(p), "--export", str(dst))
    b = json.loads(dst.read_text())
    hashes = {c["rule_config_hash"] for c in b["counts"]}
    assert hashes == {"cfg1", "cfg2"}, "rule config must be a grouping key"


def test_export_does_not_pool_different_message_versions(recfile, tmp_path):
    """Message text caused Incident 4, so behaviour under different text is
    not the same measurement."""
    p = recfile([rec(message_version="m1"), rec(message_version="m2")])
    dst = tmp_path / "b.json"
    run("--records", str(p), "--export", str(dst))
    b = json.loads(dst.read_text())
    assert {c["message_version"] for c in b["counts"]} == {"m1", "m2"}


def test_export_reports_its_own_completeness(recfile, tmp_path):
    p = recfile([rec(integrity={"missing_state": True}), rec()])
    dst = tmp_path / "b.json"
    run("--records", str(p), "--export", str(dst))
    b = json.loads(dst.read_text())
    assert b["completeness"]["incomplete_records"] == 1


# --- pre-intervention records ----------------------------------------------

def v1(**kw):
    base = {"schema_version": 1, "ts": "2026-05-01T12:00:00",
            "session_hash": "bbb", "tool_kind": "shell",
            "classification": "targeted", "verdict": "ALLOW", "features": {}}
    base.update(kw)
    return base


def test_v1_satisfactions_are_never_counted_as_confirmed(recfile):
    """v1 decided from the request and never observed a result.

    Folding those into a confirmed-consultation count would manufacture a
    before/after comparison the data cannot support.
    """
    p = recfile([v1(), v1(), v1(classification="read-memory")])
    out = run("--records", str(p), "--summary")
    assert "confirmed          0" in out
    assert "satisfies-unverified 2" in out
    assert "satisfies-substitute 1" in out


def test_v1_substitution_is_named_for_what_it_did(recfile):
    p = recfile([v1(classification="read-memory")])
    out = run("--records", str(p), "--summary")
    assert "contacted nothing" in out


def test_mixed_schemas_are_flagged_not_pooled(recfile):
    p = recfile([v1(), rec()])
    out = run("--records", str(p), "--summary")
    assert "mixes schema versions" in out
    assert "must not be added to confirmed" in out


def test_export_separates_pre_and_post_intervention(recfile, tmp_path):
    p = recfile([v1(), rec()])
    dst = tmp_path / "b.json"
    run("--records", str(p), "--export", str(dst))
    b = json.loads(dst.read_text())
    outcomes = {c["outcome"] for c in b["counts"]}
    assert "satisfies-unverified" in outcomes
    assert "satisfies" in outcomes
    assert len({c["message_version"] for c in b["counts"]}) == 2, (
        "v1 records carry an unknown message version and must not pool with v4"
    )
