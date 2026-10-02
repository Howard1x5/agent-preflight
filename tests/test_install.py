"""The installer touches a live settings file, so its failure modes matter more
than most: a duplicate registration silently doubles every count in the data,
and clobbering a foreign hook breaks something the operator relies on."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "install.py"


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir(parents=True)
    return dict(os.environ, HOME=str(tmp_path))


def run(env, *args):
    p = subprocess.run([sys.executable, str(TOOL), *args],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return p.stdout


def settings(env):
    p = Path(env["HOME"]) / ".claude" / "settings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def commands(s, event=None):
    """Commands registered, optionally for one event.

    preflight_gate.py is legitimately registered on PreToolUse, PostToolUse
    and Stop, so counting across events conflates three correct registrations
    with a duplicate.
    """
    hooks = s.get("hooks") or {}
    events = [event] if event else list(hooks)
    return [h.get("command") for ev in events for g in hooks.get(ev, [])
            for h in g.get("hooks", [])]


def test_dry_run_writes_nothing(home):
    out = run(home, "--dry-run")
    assert "nothing written" in out
    assert not (Path(home["HOME"]) / ".claude" / "settings.json").exists()


def test_default_mode_is_observe(home):
    """A fresh install that fails closed against an unconfigured dependency
    bricks the first session."""
    out = run(home)
    assert "Observe mode" in out
    rule = json.loads((Path(home["HOME"]) / ".claude" / "state" /
                       "agent-preflight" / "rules" / "consult-backend.json").read_text())
    assert rule["mode"] == "observe"


def test_install_is_idempotent(home):
    run(home)
    first = commands(settings(home))
    run(home)
    assert commands(settings(home)) == first, "second install duplicated hooks"


def test_idempotent_across_path_spellings(home):
    """A hand-wired `~/...` entry and a generated absolute one are the same
    hook. Registering both records every decision twice."""
    p = Path(home["HOME"]) / ".claude" / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "", "hooks": [{"type": "command",
         "command": "python3 ~/somewhere/hooks/preflight_gate.py"}]}]}}))
    run(home)
    pre = [c for c in commands(settings(home), "PreToolUse") if "preflight_gate" in c]
    assert len(pre) == 1, f"duplicate registration on PreToolUse: {pre}"


def test_foreign_hooks_survive_install_and_uninstall(home):
    p = Path(home["HOME"]) / ".claude" / "settings.json"
    foreign = "python3 ~/.claude/hooks/something-else.py"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "", "hooks": [{"type": "command", "command": foreign}]}]}}))
    run(home)
    assert foreign in commands(settings(home))
    run(home, "--uninstall")
    assert foreign in commands(settings(home)), "uninstall removed a foreign hook"
    assert not [c for c in commands(settings(home)) if "preflight_" in c]


def test_foreign_statusline_is_not_clobbered(home):
    p = Path(home["HOME"]) / ".claude" / "settings.json"
    p.write_text(json.dumps({"statusLine": {"type": "command", "command": "mine.sh"}}))
    out = run(home)
    assert "left alone" in out
    assert settings(home)["statusLine"]["command"] == "mine.sh"


def test_install_backs_up_before_writing(home):
    run(home)
    out = run(home, "--enforce")
    assert "backed up settings to" in out
    backups = list((Path(home["HOME"]) / ".claude").glob("settings.json.bak-*"))
    assert backups


def test_enforce_warns_about_the_query_shape(home):
    """Measured at 100% of real queries refused in shadow mode, because every
    one was piped. Enforcing without saying so is a trap."""
    out = run(home, "--enforce")
    assert "preflight-query" in out


WRAPPER = Path(__file__).resolve().parent.parent / "bin" / "preflight-query"


def link(env):
    return Path(env["HOME"]) / ".local" / "bin" / "preflight-query"


def test_install_puts_wrapper_on_path(home):
    """An agent told to use preflight-query gets command-not-found if the
    wrapper only lives inside the repo, and falls back to a piped query the
    gate refuses."""
    run(home)
    assert link(home).is_symlink()
    assert link(home).resolve() == WRAPPER.resolve()


def test_wrapper_link_is_idempotent(home):
    run(home)
    out = run(home)
    assert "+ link" not in out
    assert link(home).resolve() == WRAPPER.resolve()


def test_foreign_wrapper_on_path_is_not_clobbered(home):
    link(home).parent.mkdir(parents=True)
    link(home).write_text("#!/bin/sh\necho mine\n")
    out = run(home)
    assert "left alone" in out
    assert link(home).read_text() == "#!/bin/sh\necho mine\n"
    run(home, "--uninstall")
    assert link(home).exists(), "uninstall removed a foreign file"


def test_uninstall_removes_only_our_link(home):
    run(home)
    run(home, "--uninstall")
    assert not link(home).exists() and not link(home).is_symlink()


def test_dry_run_creates_no_link(home):
    out = run(home, "--dry-run")
    assert "+ link" in out
    assert not link(home).is_symlink()


def test_rehearsal_flag_sets_rehearsal_mode(home):
    out = run(home, "--rehearsal")
    assert "Rehearsal mode" in out
    rule = json.loads((Path(home["HOME"]) / ".claude" / "state" /
                       "agent-preflight" / "rules" / "consult-backend.json").read_text())
    assert rule["mode"] == "rehearsal"


def test_enforce_and_rehearsal_are_exclusive(home):
    p = subprocess.run([sys.executable, str(TOOL), "--enforce", "--rehearsal"],
                       capture_output=True, text=True, env=home)
    assert p.returncode != 0
