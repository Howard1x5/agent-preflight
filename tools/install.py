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
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = Path.home() / ".claude" / "settings.json"
STATE = Path.home() / ".claude" / "state" / "agent-preflight"
RULES = STATE / "rules"

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


def plan_install(s, enforce):
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
    changes.append(f"= mode: {'enforce' if enforce else 'observe'}")
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


def write_rule(enforce):
    RULES.mkdir(parents=True, exist_ok=True)
    dst = RULES / "consult-backend.json"
    if dst.exists():
        d = json.loads(dst.read_text())
        d["mode"] = "enforce" if enforce else "observe"
        dst.write_text(json.dumps(d, indent=2))
        return dst, False
    d = json.loads((ROOT / "rules" / "consult-backend.example.json").read_text())
    d = {k: v for k, v in d.items() if not k.startswith("_comment")}
    d["mode"] = "enforce" if enforce else "observe"
    dst.write_text(json.dumps(d, indent=2))
    return dst, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enforce", action="store_true",
                    help="enforce instead of observe (not the default, on purpose)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = load_settings()
    changes = plan_uninstall(s) if args.uninstall else plan_install(s, args.enforce)

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

    if args.uninstall:
        print("uninstalled. Records and rules under ~/.claude/state/agent-preflight "
              "were left in place — delete them yourself if you want them gone.")
        return 0

    rule_path, created = write_rule(args.enforce)
    print(f"rule: {rule_path}" + (" (created from the example)" if created else " (mode updated)"))
    if created:
        print("\nEDIT THAT FILE before enforcing: `endpoints` currently names the")
        print("example host, so nothing will match your real dependency.")
    if args.enforce:
        print("\nEnforcing. If your usual query shape is a pipeline or an ssh")
        print("wrapper it will stop qualifying — use bin/preflight-query.")
    else:
        print("\nObserve mode: every decision is recorded, nothing is blocked.")
        print("Run `python3 tools/report.py --summary` to see what it would have done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
