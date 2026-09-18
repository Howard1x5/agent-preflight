"""The wrapper is what makes refusing compound commands defensible.

Without it, admitting only single simple commands means ordinary work stops
counting -- measured at 100% of real queries refused in shadow mode. With it,
an unbounded parsing problem becomes a one-line install.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import rules as R  # noqa: E402

RULE = R.load_rule_file(ROOT / "rules" / "consult-backend.example.json")
WRAPPER = ROOT / "bin" / "preflight-query"


def c(cmd):
    return R.classify("Bash", {"command": cmd}, RULE)


@pytest.mark.parametrize("cmd,expected", [
    ('preflight-query --query "deployment history"', "targeted"),
    ('preflight-query -q "deploys" --limit 3', "targeted"),
    ('preflight-query --query=deploys', "targeted"),
    ('/usr/local/bin/preflight-query --query "x"', "targeted"),
])
def test_wrapper_with_a_query_qualifies(cmd, expected):
    assert c(cmd) == expected


@pytest.mark.parametrize("cmd", [
    'preflight-query',
    'preflight-query --query ""',
    'preflight-query --limit 5',
])
def test_wrapper_without_a_query_does_not_qualify(cmd):
    """The wrapper is a trusted path to the dependency, not a trusted
    assertion that work happened."""
    assert c(cmd) == "generic"


def test_wrapper_is_still_refused_when_compound():
    """Being a configured client does not exempt it from the parse rule."""
    assert c('preflight-query --query x | tee /tmp/out') is None
    assert c('preflight-query --query x ; rm -rf /tmp/x') is None


def test_wrapper_need_not_name_the_endpoint():
    """It resolves the endpoint from the same config the gate reads, so the
    two cannot disagree about what the dependency is."""
    assert "memory-backend" not in 'preflight-query --query "x"'
    assert c('preflight-query --query "x"') == "targeted"


def test_wrapper_rejects_an_empty_query_at_runtime():
    p = subprocess.run([sys.executable, str(WRAPPER), "--query", "   "],
                       capture_output=True, text=True)
    assert p.returncode == 1
    assert "must not be empty" in p.stderr


def test_wrapper_reports_an_unknown_rule_rather_than_guessing():
    p = subprocess.run([sys.executable, str(WRAPPER), "--query", "x",
                        "--rule", "no-such-rule-here"],
                       capture_output=True, text=True)
    assert p.returncode == 1


def test_wrapper_does_not_leak_endpoints_on_failure(tmp_path):
    """Its stderr is read by an agent and recorded downstream, so operator
    configuration must not appear in it."""
    import json
    rules_dir = tmp_path / ".claude" / "state" / "agent-preflight" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "t.json").write_text(json.dumps({
        "rule_id": "t", "rule_kind": "consult-backend",
        "clients": [], "wrapper_clients": ["preflight-query"],
        "endpoints": ["secret-host.internal:9999"], "filter_evidence": ["x"],
    }))
    import os
    env = dict(os.environ, HOME=str(tmp_path))
    p = subprocess.run([sys.executable, str(WRAPPER), "--query", "x",
                        "--rule", "t", "--timeout", "1"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1
    assert "secret-host.internal" not in p.stderr
    assert "9999" not in p.stderr
