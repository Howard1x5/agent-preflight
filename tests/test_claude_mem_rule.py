"""A memory backend reached through MCP tools rather than shell (claude-mem).

Three things the Open Brain rule never exercised: MCP names are matched exactly,
MCP results are prose rather than JSON, and the harness defers MCP schemas until
ToolSearch loads them.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import rules as R  # noqa: E402

RULE = R.load_rule_file(ROOT / "rules" / "consult-backend.claude-mem.example.json")
MCP = "mcp__plugin_claude-mem_mcp-search__"


def c(tool, tool_input=None):
    return R.classify(tool, tool_input or {}, RULE)


# --- classification --------------------------------------------------------

@pytest.mark.parametrize("tool", ["search", "smart_search"])
def test_search_with_a_query_is_targeted(tool):
    assert c(MCP + tool, {"query": "agent-preflight backend"}) == "targeted"


def test_search_without_a_query_is_generic():
    assert c(MCP + "search", {"limit": 20}) == "generic"


@pytest.mark.parametrize("tool", ["WebSearch", "mcp__other__search", "search"])
def test_similar_names_do_not_satisfy(tool):
    """Substring matching would hand these the gate: `search` is inside
    `WebSearch`, and another server's tool can share the final segment only if
    the rule names it by its short form -- this rule names full tool names."""
    assert c(tool, {"query": "x"}) is None


def test_short_names_still_match_their_server_prefix():
    """Rules written with short names (the Open Brain rule) keep working."""
    rule = R._compile({"mcp_search_tools": ["search_brain"]})
    assert R.classify("mcp__open-brain__search_brain", {"query": "x"}, rule) == "targeted"
    assert R.classify("mcp__open-brain__search_brain_v2", {"query": "x"}, rule) is None


def test_reading_by_id_without_searching_is_generic():
    assert c(MCP + "get_observations", {"ids": [1, 2]}) == "generic"
    assert c(MCP + "timeline", {"anchor": 5}) == "generic"


def test_toolsearch_is_permitted_never_satisfying():
    """Refusing ToolSearch makes the configured search tool uncallable."""
    assert c("ToolSearch", {"query": "select:" + MCP + "search"}) == "permitted"


def test_work_state_is_unrelated():
    assert c(MCP + "work_state_write", {"list": "x", "fields": {}}) is None


def test_curl_get_with_a_query_is_targeted():
    cmd = "curl -s 'http://127.0.0.1:37700/api/search?query=backups&limit=5'"
    assert R.classify("Bash", {"command": cmd}, RULE) == "targeted"


def test_curl_get_without_a_query_is_generic():
    cmd = "curl -s 'http://127.0.0.1:37700/api/search?limit=5'"
    assert R.classify("Bash", {"command": cmd}, RULE) == "generic"


# --- confirmation ----------------------------------------------------------

def test_mcp_prose_result_confirms():
    resp = [{"type": "text", "text": "Found 3 result(s) matching \"x\""}]
    assert R.confirms(resp, RULE, MCP + "search") == (True, "ok")


def test_mcp_zero_results_confirms():
    resp = {"content": [{"type": "text", "text": "No results found"}]}
    assert R.confirms(resp, RULE, MCP + "search") == (True, "ok")


def test_mcp_error_flag_does_not_confirm():
    assert R.confirms({"isError": True, "content": []}, RULE, MCP + "search")[0] is False


def test_mcp_error_pattern_does_not_confirm():
    rule = dict(RULE, mcp_expect_response={"error_patterns": ["^Error:"]})
    resp = [{"type": "text", "text": "Error: worker unavailable"}]
    assert R.confirms(resp, rule, MCP + "search") == (False, "error-text")


def test_http_result_still_needs_the_json_shape():
    """The HTTP path keeps its stricter check (Incident 2)."""
    assert R.confirms('{"content": []}', RULE, "Bash") == (True, "ok")
    assert R.confirms("Cannot GET /api/search", RULE, "Bash")[0] is False
