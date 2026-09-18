"""Shell is admitted by parsing, never by denylist (D5.6).

Every case that is not a single simple command whose argv[0] is a configured
client must return None. None means "not interpreted, does not satisfy" --
never "assumed harmless".
"""

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import rules as R  # noqa: E402

RULE = R.load_rule_file(
    Path(__file__).resolve().parent.parent / "rules" / "consult-backend.example.json")
EP = "memory-backend.example"


def c(cmd):
    return R.classify("Bash", {"command": cmd}, RULE)


# --- what must be admitted -------------------------------------------------

def test_genuine_query_is_admitted_regardless_of_naming():
    """INCIDENT-1b: a real query was blocked for lacking a literal substring.

    Matching on the configured endpoint rather than a project name fixes the
    false block, and removes the pressure that made agents inject that name
    into semantic search strings so the gate would open.
    """
    cmd = (f"curl -s -X POST http://{EP}/api/search "
           "-H 'Content-Type: application/json' "
           '-d \'{"query":"deployment history","limit":5}\'')
    assert c(cmd) == "targeted"


def test_unfiltered_dump_is_generic_not_targeted():
    cmd = f"psql -c 'SELECT * FROM memories ORDER BY created_at DESC LIMIT 10' {EP}"
    assert c(cmd) == "generic"


def test_write_endpoint_is_capture_only():
    assert c(f"curl -s -X POST http://{EP}/api/add -d '{{\"text\":\"x\"}}'") == "capture-only"


# --- what must be refused --------------------------------------------------

@pytest.mark.parametrize("name,cmd", [
    ("echo a tool name",   "echo search_brain"),
    ("echo sql keywords",  "echo 'open-brain WHERE ILIKE'"),
    ("non-client binary",  f"cat http://{EP}/api/search"),
    ("unrelated command",  "ls -la /var/log"),
])
def test_non_client_commands_are_unrelated(name, cmd):
    assert c(cmd) is None, name


@pytest.mark.parametrize("name,cmd", [
    # Each begins with a REAL client hitting the REAL endpoint, so an argv[0]
    # check alone would admit every one of them.
    ("semicolon",  f"curl http://{EP}/api/search?q=x ; rm -rf /tmp/x"),
    ("pipe",       f"curl http://{EP}/api/search?q=x | tee /tmp/out"),
    ("and-and",    f"curl http://{EP}/api/search?q=x && echo done"),
    ("or-or",      f"curl http://{EP}/api/search?q=x || echo fail"),
    ("background", f"curl http://{EP}/api/search?q=x & echo hi"),
    ("redirect",   f"curl http://{EP}/api/search?q=x > /tmp/out"),
    ("heredoc",    f"psql -c 'SELECT 1 WHERE x' {EP} <<EOF"),
    ("subshell",   f"curl $(echo http://{EP})/api/search?q=x"),
    ("backtick",   f"curl `echo http://{EP}`/api/search?q=x"),
    ("brace expr", f"curl http://{EP}/api/search?q=${{X}}"),
    ("procsub",    f"curl http://{EP}/api/search -d @<(cat /etc/passwd)"),
    ("env prefix", f"FOO=1 curl http://{EP}/api/search?q=x"),
    ("unbalanced", f"curl 'http://{EP}/api/search?q=x"),
])
def test_compound_commands_are_refused(name, cmd):
    assert c(cmd) is None, f"{name} was admitted; argv[0] alone is not enough"


def test_newline_smuggling_is_refused():
    """The tokenizer consumes a newline as whitespace.

    `curl <real endpoint>\\nrm -rf x` tokenizes as one flat token list, so a
    check that only looks for operator tokens never sees the second command.
    This is why the parser also inspects the raw string for exactly the
    constructs the tokenizer provably cannot surface.
    """
    cmd = f"curl http://{EP}/api/search?q=x\nrm -rf /tmp/x"
    assert R.parse_simple_command(cmd) is None
    assert c(cmd) is None


def test_ssh_wrapped_query_does_not_count():
    """Legitimate, but the gate cannot see inside it. Use the shipped wrapper."""
    assert c(f"ssh host 'curl http://{EP}/api/search?q=x'") is None


# --- confirmation ----------------------------------------------------------

def test_zero_results_confirms():
    """A filtered search that correctly returns nothing is diligence."""
    ok, why = R.confirms('{"results": []}', RULE)
    assert ok, why


def test_wellformed_failure_does_not_confirm():
    """Incident 2 was an HTTP 200 carrying a well-formed default.

    A non-error status is not on its own evidence the work happened.
    """
    ok, why = R.confirms('{"error": "connection refused"}', RULE)
    assert not ok and why == "shape-mismatch"


def test_unparseable_body_does_not_confirm():
    ok, why = R.confirms("Internal Server Error", RULE)
    assert not ok and why == "unparseable"


def test_explicit_error_does_not_confirm():
    ok, why = R.confirms({"is_error": True, "stdout": ""}, RULE)
    assert not ok and why == "error"


def test_rule_loading_never_silently_uses_local_config_in_tests():
    """A test that picks up whatever is configured on the machine is not a test.

    load_rule() prefers local config by design; tests must name the file.
    """
    assert RULE is not None
    assert "memory-backend.example" in RULE["_endpoints"], (
        "tests must load the bundled example rule explicitly, not whatever "
        "rule happens to be installed"
    )
