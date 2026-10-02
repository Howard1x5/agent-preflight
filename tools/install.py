#!/usr/bin/env python3
"""Install agent-preflight into a Claude Code settings file.

Defaults to OBSERVE mode. An install that fails closed against a dependency the
installer has not finished configuring bricks their first session, and a control
people disable is indistinguishable from no control.

  python3 tools/install.py --dry-run        # show what would change
  python3 tools/install.py                  # install, observe mode
  python3 tools/install.py --enforce        # install and enforce
  python3 tools/install.py --uninstall

Every run backs up the settings file first and prints the backup path.
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = Path.home() / ".claude" / "settings.json"
STATE = Path.home() / ".claude" / "state" / "agent-preflight"
RULES = STATE / "rules"

# The wrapper only helps if an agent can run it by name. Left at bin/ inside the
# repo, an agent told to "use preflight-query" gets command-not-found and falls
# back to a piped curl -- which the gate refuses. Observed on the maintainer's
# own sessions: every backend query took that path until the wrapper was invoked
# by its full repo path. ~/.local/bin is on PATH by default on most Linux
# distributions.
BIN_DIR = Path.home() / ".local" / "bin"
LINK = BIN_DIR / "preflight-query"
WRAPPER = ROOT / "bin" / "preflight-query"

HOOKS = {
    "UserPromptSubmit": "preflight_init.py",
    "PreToolUse": "preflight_gate.py",
    "PostToolUse": "preflight_gate.py",
    "Stop": "preflight_gate.py",
}
STATUS = "preflight_status.py"


def cmd_for(script):
    return f"python3 {ROOT / 'hooks' / script}"


def is_ours(command):
    return "preflight_" in (command or "")


def already_registered(groups, script):
    """Match by script name, not by exact command string.

    A hand-wired entry using `~/...` and a generated one using an absolute path
    are the same hook. Comparing strings registers both and every decision gets
    recorded twice, which silently doubles every count in the data.
    """
    return any(script in (h.get("command") or "")
               for g in groups for h in g.get("hooks", []))


def backup(path):
    if not path.exists():
        return None
    dst = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dst)
    return dst


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def plan_install(s, mode):
    changes = []
    hooks = s.setdefault("hooks", {})
    for event, script in HOOKS.items():
        cmd = cmd_for(script)
        groups = hooks.setdefault(event, [])
        if already_registered(groups, script):
            continue
        groups.append({"matcher": "", "hooks": [{"type": "command", "command": cmd}]})
        changes.append(f"+ {event}: {script}")
    status_cmd = cmd_for(STATUS)
    if STATUS not in ((s.get("statusLine") or {}).get("command") or ""):
        if "statusLine" in s and not is_ours(s["statusLine"].get("command")):
            changes.append("! statusLine already set by something else — left alone")
        else:
            s["statusLine"] = {"type": "command", "command": status_cmd}
            changes.append("+ statusLine")
    changes.append(f"= mode: {mode}")
    return changes


def plan_uninstall(s):
    changes = []
    for event, groups in list((s.get("hooks") or {}).items()):
        kept = []
        for g in groups:
            hs = [h for h in g.get("hooks", []) if not is_ours(h.get("command"))]
            if len(hs) != len(g.get("hooks", [])):
                changes.append(f"- {event}")
            if hs:
                g["hooks"] = hs
                kept.append(g)
        if kept:
            s["hooks"][event] = kept
        else:
            s["hooks"].pop(event, None)
    if is_ours((s.get("statusLine") or {}).get("command")):
        s.pop("statusLine")
        changes.append("- statusLine")
    return changes


def _link_is_ours():
    return LINK.is_symlink() and LINK.resolve() == WRAPPER.resolve()


def plan_link(uninstall):
    """The wrapper's PATH link, as a change line, or None if nothing to do.

    Never replaces a file it did not create: something else named
    preflight-query on PATH is the operator's, not ours.
    """
    if uninstall:
        return f"- link {LINK}" if _link_is_ours() else None
    if _link_is_ours():
        return None
    if LINK.exists() or LINK.is_symlink():
        return f"! {LINK} exists and is not ours — left alone"
    return f"+ link {LINK} -> {WRAPPER}"


def apply_link(change):
    if change and change.startswith("+ link"):
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        LINK.symlink_to(WRAPPER)
    elif change and change.startswith("- link"):
        LINK.unlink()


def write_rule(mode):
    RULES.mkdir(parents=True, exist_ok=True)
    dst = RULES / "consult-backend.json"
    if dst.exists():
        d = json.loads(dst.read_text())
        d["mode"] = mode
        dst.write_text(json.dumps(d, indent=2))
        return dst, False
    d = json.loads((ROOT / "rules" / "consult-backend.example.json").read_text())
    d = {k: v for k, v in d.items() if not k.startswith("_comment")}
    d["mode"] = mode
    dst.write_text(json.dumps(d, indent=2))
    return dst, True


def main():
    ap = argparse.ArgumentParser()
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--enforce", action="store_true",
                   help="enforce instead of observe (not the default, on purpose)")
    m.add_argument("--rehearsal", action="store_true",
                   help="show the agent the real block messages without blocking")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = load_settings()
    mode = "enforce" if args.enforce else "rehearsal" if args.rehearsal else "observe"
    changes = plan_uninstall(s) if args.uninstall else plan_install(s, mode)
    link_change = plan_link(args.uninstall)
    if link_change:
        changes.append(link_change)

    if not changes:
        print("nothing to change")
        return 0

    print("\n".join(f"  {c}" for c in changes))
    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    b = backup(SETTINGS)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2))
    if b:
        print(f"\nbacked up settings to {b}")
    apply_link(link_change)
    if not args.uninstall and str(BIN_DIR) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"\n{BIN_DIR} is not on PATH — add it, or agents will not find preflight-query.")

    if args.uninstall:
        print("uninstalled. Records and rules under ~/.claude/state/agent-preflight "
              "were left in place — delete them yourself if you want them gone.")
        return 0

    rule_path, created = write_rule(mode)
    print(f"rule: {rule_path}" + (" (created from the example)" if created else " (mode updated)"))
    if created:
        print("\nEDIT THAT FILE before enforcing: `endpoints` currently names the")
        print("example host, so nothing will match your real dependency.")
    if args.enforce:
        print("\nEnforcing. If your usual query shape is a pipeline or an ssh")
        print("wrapper it will stop qualifying — use preflight-query.")
    elif args.rehearsal:
        print("\nRehearsal mode: the agent is shown each block message, nothing is")
        print("blocked. Compare against observe-mode sessions with tools/report.py.")
    else:
        print("\nObserve mode: every decision is recorded, nothing is blocked.")
        print("Run `python3 tools/report.py --summary` to see what it would have done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
