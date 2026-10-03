"""D7: enforcement authority must sit where the agent cannot write.

These run as an ordinary user, so root ownership is simulated by patching
authority.trusted; the ownership check itself is tested directly on files the
test user owns (which must fail it)."""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import authority  # noqa: E402
import rules  # noqa: E402

spec = importlib.util.spec_from_file_location("install_system", ROOT / "tools" / "install_system.py")
install_system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_system)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def sysdir(tmp_path, monkeypatch):
    d = tmp_path / "etc-agent-preflight"
    (d / "rules").mkdir(parents=True)
    monkeypatch.setattr(authority, "SYSTEM_DIR", d)
    monkeypatch.setattr(authority, "SYSTEM_RULES_DIR", d / "rules")
    monkeypatch.setattr(authority, "OVERRIDE_FILE", d / "override.json")
    return d


def write_rule(path, **kw):
    d = json.loads((ROOT / "rules" / "consult-backend.example.json").read_text())
    d.update(kw)
    path.write_text(json.dumps(d))


# --- ownership --------------------------------------------------------------

def test_a_file_the_agent_can_write_is_never_trusted(tmp_path):
    """Everything the test user creates is exactly what an agent could create."""
    p = tmp_path / "rule.json"
    p.write_text("{}")
    assert not authority.trusted(p)


def test_missing_file_is_not_trusted(tmp_path):
    assert not authority.trusted(tmp_path / "nope.json")


# --- rule precedence ----------------------------------------------------------

def test_system_rule_wins_and_user_rule_is_ignored(sysdir, tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "trusted", lambda p: True)
    write_rule(sysdir / "rules" / "consult-backend.json", mode="enforce")
    user = tmp_path / "user-rules"
    user.mkdir()
    write_rule(user / "consult-backend.json", mode="observe")
    monkeypatch.setattr(rules, "RULES_DIR", user)
    r = rules.load_rule()
    assert r["mode"] == "enforce" and r["_authority"] == "system"


def test_untrusted_system_rule_still_enforces(sysdir, monkeypatch):
    """Falling back to the user rule would hand authority back to the agent."""
    monkeypatch.setattr(authority, "trusted", lambda p: False)
    write_rule(sysdir / "rules" / "consult-backend.json", mode="observe")
    r = rules.load_rule()
    assert r["mode"] == "enforce" and r.get("_untrusted")


def test_without_a_system_rule_the_user_rule_applies(sysdir, tmp_path, monkeypatch):
    user = tmp_path / "user-rules"
    user.mkdir()
    write_rule(user / "consult-backend.json", mode="rehearsal")
    monkeypatch.setattr(rules, "RULES_DIR", user)
    assert rules.load_rule()["mode"] == "rehearsal"


# --- override -----------------------------------------------------------------

def override(sysdir, created, until, reason="fixing config"):
    (sysdir / "override.json").write_text(json.dumps(
        {"created": created.isoformat(), "until": until.isoformat(), "reason": reason, "by": "op"}))


def test_active_override_is_honoured(sysdir, monkeypatch):
    monkeypatch.setattr(authority, "trusted", lambda p: True)
    override(sysdir, NOW, NOW + timedelta(minutes=30))
    assert authority.active_override(NOW + timedelta(minutes=5))["reason"] == "fixing config"


def test_override_expires_on_its_own(sysdir, monkeypatch):
    monkeypatch.setattr(authority, "trusted", lambda p: True)
    override(sysdir, NOW, NOW + timedelta(minutes=30))
    assert authority.active_override(NOW + timedelta(minutes=31)) is None


def test_untrusted_override_is_ignored(sysdir, monkeypatch):
    """An override the agent could have written is not an override."""
    monkeypatch.setattr(authority, "trusted", lambda p: False)
    override(sysdir, NOW, NOW + timedelta(minutes=30))
    assert authority.active_override(NOW) is None


def test_override_longer_than_the_cap_is_refused(sysdir, monkeypatch):
    monkeypatch.setattr(authority, "trusted", lambda p: True)
    override(sysdir, NOW, NOW + timedelta(minutes=authority.MAX_OVERRIDE_MINUTES + 1))
    assert authority.active_override(NOW) is None


def test_write_override_requires_a_reason_and_a_bounded_time(sysdir):
    with pytest.raises(ValueError):
        authority.write_override(30, "  ")
    with pytest.raises(ValueError):
        authority.write_override(authority.MAX_OVERRIDE_MINUTES + 1, "x")
    o = authority.write_override(30, "fixing config", now=NOW)
    assert o["until"] == (NOW + timedelta(minutes=30)).isoformat()


def test_preflight_admin_refuses_without_root():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    p = subprocess.run([sys.executable, str(ROOT / "bin" / "preflight-admin"), "dev",
                        "--minutes", "5", "--reason", "x"], capture_output=True, text=True)
    assert p.returncode == 1 and "sudo" in p.stderr


# --- system installer -----------------------------------------------------------

def test_managed_settings_keep_everything_that_is_not_ours():
    s = {"permissions": {"deny": ["Bash(rm -rf /)"]},
         "hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "org.sh"}]}]}}
    install_system.plan_managed(s, Path("/opt/agent-preflight"))
    assert s["permissions"] == {"deny": ["Bash(rm -rf /)"]}
    cmds = [h["command"] for g in s["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "org.sh" in cmds and any("preflight_gate.py" in c for c in cmds)


def test_managed_install_is_idempotent():
    s = {}
    install_system.plan_managed(s, Path("/opt/agent-preflight"))
    assert install_system.plan_managed(s, Path("/opt/agent-preflight")) == []


def test_managed_uninstall_removes_only_ours():
    s = {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "org.sh"}]}]},
         "statusLine": {"type": "command", "command": "org-status.sh"}}
    install_system.plan_managed(s, Path("/opt/agent-preflight"))
    install_system.plan_unmanaged(s)
    cmds = [h["command"] for gs in s["hooks"].values() for g in gs for h in g["hooks"]]
    assert cmds == ["org.sh"] and s["statusLine"]["command"] == "org-status.sh"


def test_user_level_hooks_are_detected(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "", "hooks": [{"type": "command", "command": "python3 x/hooks/preflight_gate.py"}]}]}}))
    assert install_system.user_hooks_present(tmp_path)


def test_system_rule_is_built_from_the_operators_rule_and_forced_to_enforce(tmp_path):
    rd = tmp_path / ".claude" / "state" / "agent-preflight" / "rules"
    rd.mkdir(parents=True)
    write_rule(rd / "consult-backend.json", mode="observe", endpoints=["real-host:8765"])
    d = install_system.build_rule(tmp_path, ROOT / "rules" / "consult-backend.example.json")
    assert d["mode"] == "enforce" and d["endpoints"] == ["real-host:8765"]


def test_system_install_refuses_without_root():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "install_system.py")],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "sudo" in p.stderr


# --- the gate honours an override only under enforce ---------------------------

def _load_gate():
    s = importlib.util.spec_from_file_location("preflight_gate_t", ROOT / "hooks" / "preflight_gate.py")
    g = importlib.util.module_from_spec(s)
    s.loader.exec_module(g)
    return g


def test_gate_allows_loudly_under_an_active_override(monkeypatch):
    g = _load_gate()
    seen = {}
    monkeypatch.setattr(g, "MODE", "enforce")
    monkeypatch.setattr(g.authority, "active_override",
                        lambda: {"until": "2026-10-02T13:00:00+00:00", "reason": "fixing config"})
    monkeypatch.setattr(g, "emit", lambda *a, **k: seen.setdefault("outcome", a[5]))
    monkeypatch.setattr(g, "notify", lambda m: seen.setdefault("msg", m))
    with pytest.raises(SystemExit) as e:
        g.handle_pre("s1", "Bash", {"command": "ls"}, "t1")
    assert e.value.code == 0
    assert seen["outcome"] == "override-allow"
    assert "OVERRIDE" in seen["msg"] and "NOT in effect" in seen["msg"]


def test_override_is_irrelevant_outside_enforce(monkeypatch):
    """Observe and rehearsal never block, so an override must not alter their records."""
    g = _load_gate()
    called = []
    monkeypatch.setattr(g, "MODE", "rehearsal")
    monkeypatch.setattr(g.authority, "active_override", lambda: called.append(1) or {"until": "x", "reason": "y"})
    monkeypatch.setattr(g, "emit", lambda *a, **k: None)
    monkeypatch.setattr(g, "gate_path", lambda s: Path("/nonexistent/state"))
    with pytest.raises(SystemExit):
        g.handle_pre("s1", "Bash", {"command": "ls"}, "t1")
    assert not called
