"""D7 stage A: the gate as a service under its own uid, driven through the
thin client over a real Unix socket. The service's HOME stands in for
/var/lib/agent-preflight; the test's own HOME stands in for the operator's."""

import io
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "tools"))
import authority  # noqa: E402
import preflight_client as client  # noqa: E402
import preflight_service as service  # noqa: E402

spec_path = ROOT / "tools" / "install_system.py"
import importlib.util  # noqa: E402
_s = importlib.util.spec_from_file_location("install_system_t", spec_path)
install_system = importlib.util.module_from_spec(_s)
_s.loader.exec_module(install_system)

EP = "memory-backend.example"
GOOD = f"curl -s -X POST http://{EP}/api/search -d '{{\"query\":\"deploys\"}}'"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    home = tmp_path / "var-lib-agent-preflight"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    sock = tmp_path / "gate.sock"
    server = service.serve(sock)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield {"home": home, "sock": sock}
    server.shutdown()
    server.server_close()


def call(sock, kind, payload, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = client.main([kind], sock_path=sock)
    out = capsys.readouterr()
    return code, out.out, out.err


def pre(cmd, tuid="t1"):
    return {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
            "tool_input": {"command": cmd}, "tool_use_id": tuid}


def post(cmd, response, tuid="t1"):
    return {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash",
            "tool_input": {"command": cmd}, "tool_response": response, "tool_use_id": tuid}


def test_records_land_in_the_service_home_not_the_operators(svc, monkeypatch, capsys):
    call(svc["sock"], "init", {"session_id": "s1"}, monkeypatch, capsys)
    call(svc["sock"], "gate", pre("ls"), monkeypatch, capsys)
    assert (svc["home"] / ".claude" / "state" / "agent-preflight" / "decisions.jsonl").exists()


def test_a_block_is_relayed_to_the_agent(svc, monkeypatch, capsys):
    call(svc["sock"], "init", {"session_id": "s1"}, monkeypatch, capsys)
    code, _, err = call(svc["sock"], "gate", pre("ls"), monkeypatch, capsys)
    assert code == 2 and "not yet consulted" in err


def test_a_real_query_flows_end_to_end(svc, monkeypatch, capsys):
    call(svc["sock"], "init", {"session_id": "s1"}, monkeypatch, capsys)
    assert call(svc["sock"], "gate", pre(GOOD), monkeypatch, capsys)[0] == 0
    call(svc["sock"], "gate", post(GOOD, '{"results": []}'), monkeypatch, capsys)
    assert call(svc["sock"], "gate", pre("ls", "t2"), monkeypatch, capsys)[0] == 0


def test_the_service_chains_its_records(svc, monkeypatch, capsys):
    call(svc["sock"], "init", {"session_id": "s1"}, monkeypatch, capsys)
    call(svc["sock"], "gate", pre("ls"), monkeypatch, capsys)
    call(svc["sock"], "gate", pre("ls", "t2"), monkeypatch, capsys)
    code, out, _ = call(svc["sock"], "verify", {}, monkeypatch, capsys)
    assert "breaks     0" in out and code == 0


def test_report_is_readable_through_the_service(svc, monkeypatch, capsys):
    call(svc["sock"], "init", {"session_id": "s1"}, monkeypatch, capsys)
    call(svc["sock"], "gate", pre("ls"), monkeypatch, capsys)
    _, out, _ = call(svc["sock"], "report", {}, monkeypatch, capsys)
    assert "records" in out


def test_unknown_requests_do_nothing(svc):
    r = client.ask("rm -rf /", "", svc["sock"])
    assert r["exit"] == 0 and "unknown request" in r["stderr"]


# --- service unreachable ---------------------------------------------------------

def test_unreachable_service_fails_closed_on_pretooluse(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(authority, "active_override", lambda: None)
    code, _, err = call(tmp_path / "no.sock", "gate", pre("ls"), monkeypatch, capsys)
    assert code == 2 and "fails closed" in err


def test_unreachable_service_does_not_block_other_events(tmp_path, monkeypatch, capsys):
    assert call(tmp_path / "no.sock", "gate", post("ls", "x"), monkeypatch, capsys)[0] == 0
    assert call(tmp_path / "no.sock", "init", {"session_id": "s1"}, monkeypatch, capsys)[0] == 0
    _, out, _ = call(tmp_path / "no.sock", "status", {}, monkeypatch, capsys)
    assert "DOWN" in out


def test_override_still_opens_the_hatch_when_the_service_is_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(authority, "active_override",
                        lambda: {"until": "2026-10-02T13:00:00+00:00", "reason": "service fix"})
    code, out, _ = call(tmp_path / "no.sock", "gate", pre("ls"), monkeypatch, capsys)
    assert code == 0 and "OVERRIDE" in out and "DOWN" in out


# --- installer ---------------------------------------------------------------------

def test_service_install_registers_the_client_not_the_gate():
    s = {}
    install_system.plan_managed(s, Path("/opt/agent-preflight"), service=True)
    cmds = [h["command"] for gs in s["hooks"].values() for g in gs for h in g["hooks"]]
    assert all("preflight_client.py" in c for c in cmds)
    assert s["statusLine"]["command"].endswith("preflight_client.py status")


def test_switching_to_the_service_replaces_rather_than_duplicates():
    s = {}
    install_system.plan_managed(s, Path("/opt/agent-preflight"), service=False)
    install_system.plan_managed(s, Path("/opt/agent-preflight"), service=True)
    for event, groups in s["hooks"].items():
        cmds = [h["command"] for g in groups for h in g["hooks"]]
        assert len(cmds) == 1 and "preflight_client.py" in cmds[0], event


def test_unit_runs_as_the_dedicated_user_with_a_private_state_dir():
    u = install_system.unit_text("lordfarquad")
    for line in ("User=preflight", "StateDirectoryMode=0700", "ProtectHome=yes",
                 "NoNewPrivileges=yes", "Environment=HOME=/var/lib/agent-preflight"):
        assert line in u, line
