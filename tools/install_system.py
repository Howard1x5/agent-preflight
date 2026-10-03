#!/usr/bin/env python3
"""Install agent-preflight with root-owned authority (ARCHITECTURE D7).

  sudo python3 tools/install_system.py --dry-run
  sudo python3 tools/install_system.py
  sudo python3 tools/install_system.py --uninstall

Enforcement installed by tools/install.py lives entirely in the operator's
uid, so the agent can edit the hook source, unregister the hooks, or flip the
rule to observe. This installer moves every one of those out of reach:

  /opt/agent-preflight/                 hook source, root-owned
  /etc/agent-preflight/rules/           the rule, mode=enforce, root-owned
  /etc/claude-code/managed-settings.json  hook registration; Claude Code lets
                                        no user or project setting override it
  /usr/local/bin/preflight-query        the wrapper, by name
  /usr/local/bin/preflight-admin        the sudo-only override

Records and gate state stay in the operator's home -- the residual weakness
D7 states, closed later by a dedicated-uid service.
"""

import argparse
import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import authority  # noqa: E402

OPT = Path("/opt/agent-preflight")
MANAGED = Path("/etc/claude-code/managed-settings.json")
BIN = Path("/usr/local/bin")
LINKS = ("preflight-query", "preflight-admin", "preflight-report")
SERVICE_USER = "preflight"
SERVICE_HOME = Path("/var/lib/agent-preflight")
UNIT = Path("/etc/systemd/system/agent-preflight.service")
CLIENT_KIND = {"preflight_init.py": "init", "preflight_gate.py": "gate",
               "preflight_status.py": "status"}

HOOKS = {
    "UserPromptSubmit": "preflight_init.py",
    "PreToolUse": "preflight_gate.py",
    "PostToolUse": "preflight_gate.py",
    "Stop": "preflight_gate.py",
}
STATUS = "preflight_status.py"


def operator_home():
    """The home of the human who ran sudo, not root's."""
    name = os.environ.get("SUDO_USER")
    return Path(pwd.getpwnam(name).pw_dir) if name else Path.home()


def is_ours(command):
    return "preflight_" in (command or "")


def user_hooks_present(home):
    """Hooks from tools/install.py still registered for the operator.

    Both sets running would record every decision twice -- the duplicate-count
    defect tools/install.py exists to prevent -- and the user-space set is the
    one the agent can edit.
    """
    try:
        s = json.loads((home / ".claude" / "settings.json").read_text())
    except (OSError, ValueError):
        return False
    return any(is_ours(h.get("command"))
               for groups in (s.get("hooks") or {}).values()
               for g in groups for h in g.get("hooks", []))


def build_rule(home, bundled):
    """The operator's current rule (their real endpoints), forced to enforce."""
    for p in (home / ".claude" / "state" / "agent-preflight" / "rules" / "consult-backend.json",
              bundled):
        try:
            d = json.loads(Path(p).read_text())
            break
        except (OSError, ValueError):
            continue
    d = {k: v for k, v in d.items() if not k.startswith("_")}
    d["mode"] = "enforce"
    return d


def hook_command(opt, script, service=False):
    """Direct hooks run the gate in the operator's uid; service hooks relay to
    the dedicated-uid service (D7 stage A)."""
    if service:
        return f"python3 {opt / 'hooks' / 'preflight_client.py'} {CLIENT_KIND[script]}"
    return f"python3 {opt / 'hooks' / script}"


def plan_managed(s, opt, service=False):
    """Add our hooks to a managed-settings dict. Never touches anything else.

    Our own entries whose command differs from the wanted one (switching
    between direct and service hooks) are replaced, not duplicated.
    """
    changes = []
    hooks = s.setdefault("hooks", {})
    for event, script in HOOKS.items():
        want = hook_command(opt, script, service)
        groups = hooks.setdefault(event, [])
        if any((h.get("command") or "") == want for g in groups for h in g.get("hooks", [])):
            continue
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not is_ours(h.get("command"))]
        hooks[event] = [g for g in groups if g["hooks"]]
        hooks[event].append({"matcher": "", "hooks": [{"type": "command", "command": want}]})
        changes.append(f"+ managed {event}: {want.split('/')[-1]}")
    want = hook_command(opt, STATUS, service)
    cur = (s.get("statusLine") or {}).get("command")
    if cur != want and (cur is None or is_ours(cur)):
        s["statusLine"] = {"type": "command", "command": want}
        changes.append("+ managed statusLine")
    return changes


def unit_text(group):
    """systemd unit for the service. The hardening is not decoration: the
    service holds the evidence, so it gets only the write access it needs."""
    return f"""[Unit]
Description=agent-preflight gate service (dedicated uid; ARCHITECTURE D7 stage A)
After=network-online.target

[Service]
User={SERVICE_USER}
Group={group}
Environment=HOME={SERVICE_HOME}
ExecStart=/usr/bin/python3 {OPT}/hooks/preflight_service.py
StateDirectory=agent-preflight
StateDirectoryMode=0700
RuntimeDirectory=agent-preflight
RuntimeDirectoryMode=0750
UMask=0077
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths={SERVICE_HOME}

[Install]
WantedBy=multi-user.target
"""


def ensure_service_user():
    try:
        pwd.getpwnam(SERVICE_USER)
        return False
    except KeyError:
        subprocess.run(["useradd", "--system", "--home-dir", str(SERVICE_HOME),
                        "--no-create-home", "--shell", "/usr/sbin/nologin", SERVICE_USER],
                       check=True)
        return True


def operator_group():
    name = os.environ.get("SUDO_USER")
    gid = pwd.getpwnam(name).pw_gid if name else os.getgid()
    return grp.getgrgid(gid).gr_name


def plan_unmanaged(s):
    """Remove only our hooks and status line from a managed-settings dict."""
    changes = []
    for event, groups in list((s.get("hooks") or {}).items()):
        kept = []
        for g in groups:
            hs = [h for h in g.get("hooks", []) if not is_ours(h.get("command"))]
            if len(hs) != len(g.get("hooks", [])):
                changes.append(f"- managed {event}")
            if hs:
                g["hooks"] = hs
                kept.append(g)
        if kept:
            s["hooks"][event] = kept
        else:
            s["hooks"].pop(event, None)
    if not s.get("hooks"):
        s.pop("hooks", None)
    if is_ours((s.get("statusLine") or {}).get("command")):
        s.pop("statusLine")
        changes.append("- managed statusLine")
    return changes


def copy_source(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    for sub in ("hooks", "bin", "rules", "tools"):
        shutil.copytree(src / sub, dst / sub,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for p in dst.rglob("*"):
        os.chmod(p, 0o755 if p.is_dir() or p.parent.name == "bin" else 0o644)
    os.chmod(dst, 0o755)


def load_json(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def write_json(p, d, mode=0o644):
    p.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.chmod(tmp, mode)
    tmp.replace(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--service", action="store_true",
                    help="run the gate as a dedicated-uid service (D7 stage A)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        print("install_system: this needs root -- run it with sudo.", file=sys.stderr)
        return 1
    home = operator_home()
    if not args.uninstall and user_hooks_present(home):
        print("install_system: user-level preflight hooks are still registered in\n"
              f"{home}/.claude/settings.json. Remove them first, as yourself (not sudo):\n"
              "  python3 tools/install.py --uninstall", file=sys.stderr)
        return 1

    managed = load_json(MANAGED)
    changes = plan_unmanaged(managed) if args.uninstall else plan_managed(managed, OPT, args.service)
    if args.uninstall:
        changes += [f"- {p}" for p in (OPT, authority.SYSTEM_RULES_DIR) if p.exists()]
        changes += [f"- link {BIN / n}" for n in LINKS if (BIN / n).is_symlink()]
    else:
        changes += [f"= copy source -> {OPT}",
                    f"= rule -> {authority.SYSTEM_RULES_DIR / 'consult-backend.json'} (mode=enforce)"]
        changes += [f"= link {BIN / n}" for n in LINKS]
        if args.service:
            changes += [f"= user {SERVICE_USER}, state {SERVICE_HOME} (0700)",
                        f"= unit {UNIT} (enabled, started)"]
    if args.uninstall and UNIT.exists():
        changes += [f"- unit {UNIT} (records in {SERVICE_HOME} are kept)"]
    print("\n".join(f"  {c}" for c in changes))
    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    os.umask(0o022)
    if MANAGED.exists():
        b = MANAGED.with_name(f"{MANAGED.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(MANAGED, b)
        print(f"\nbacked up managed settings to {b}")

    if args.uninstall:
        if UNIT.exists():
            subprocess.run(["systemctl", "disable", "--now", UNIT.name], check=False)
            UNIT.unlink()
            subprocess.run(["systemctl", "daemon-reload"], check=False)
        write_json(MANAGED, managed)
        for n in LINKS:
            if (BIN / n).is_symlink():
                (BIN / n).unlink()
        shutil.rmtree(OPT, ignore_errors=True)
        shutil.rmtree(authority.SYSTEM_RULES_DIR, ignore_errors=True)
        authority.end_override()
        try:
            authority.SYSTEM_DIR.rmdir()   # only if empty; never removes anything else
        except OSError:
            pass
        print("uninstalled. Records under ~/.claude/state/agent-preflight were left in place.\n"
              "To return to user-level observe mode: python3 tools/install.py")
        return 0

    copy_source(ROOT, OPT)
    write_json(authority.SYSTEM_RULES_DIR / "consult-backend.json",
               build_rule(home, ROOT / "rules" / "consult-backend.example.json"))
    write_json(MANAGED, managed)
    BIN.mkdir(parents=True, exist_ok=True)
    for n in LINKS:
        if (BIN / n).is_symlink() or (BIN / n).exists():
            (BIN / n).unlink()
        (BIN / n).symlink_to(OPT / "bin" / n)

    if args.service:
        if ensure_service_user():
            print(f"created system user {SERVICE_USER}")
        UNIT.write_text(unit_text(operator_group()))
        os.chmod(UNIT, 0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", UNIT.name], check=True)
        subprocess.run(["systemctl", "restart", UNIT.name], check=True)
        print(f"service running as {SERVICE_USER}; records now in {SERVICE_HOME}.")
        print("Read them with: preflight-report")
    elif UNIT.exists():
        subprocess.run(["systemctl", "disable", "--now", UNIT.name], check=False)
        UNIT.unlink()
        subprocess.run(["systemctl", "daemon-reload"], check=False)

    print("\nEnforcing, with root-owned authority. Start a new Claude Code session for")
    print("the managed hooks to load. Escape hatch (sudo only):")
    print("  sudo preflight-admin dev --minutes 30 --reason \"why\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
