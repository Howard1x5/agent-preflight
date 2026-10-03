#!/usr/bin/env python3
"""Where enforcement authority lives, and how it is checked (ARCHITECTURE D7).

The agent runs as the operator's uid, so anything the operator can write, the
agent can write. Enforcement therefore reads its rule and any admin override
from a root-owned directory the agent cannot modify without the operator's
sudo password. Modes that block nothing (observe, rehearsal) need no such
protection and stay in user space.

Paths are module constants, deliberately not environment variables: a hook's
environment is something an agent can influence for future sessions, and a
redirectable authority path is a bypass.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYSTEM_DIR = Path("/etc/agent-preflight")
SYSTEM_RULES_DIR = SYSTEM_DIR / "rules"
OVERRIDE_FILE = SYSTEM_DIR / "override.json"

# An override longer than this is refused outright. D4: a hatch that can be
# left open indefinitely becomes the steady state, and that is Incident 4.
MAX_OVERRIDE_MINUTES = 240


def trusted(path):
    """True only if the file and its directory are root-owned and not writable
    by group or other. Anything else under the system directory is treated as
    tampered or misconfigured, never as authority."""
    try:
        for p in (Path(path), Path(path).parent):
            st = p.stat()
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
        return True
    except OSError:
        return False


def system_rule_path(rule_id):
    p = SYSTEM_RULES_DIR / f"{rule_id}.json"
    return p if p.exists() else None


def active_override(now=None):
    """The admin override if one is in force, else None.

    Refused if the file is not trusted, unparsable, already expired, or claims
    an expiry further out than MAX_OVERRIDE_MINUTES from its creation.
    """
    if not OVERRIDE_FILE.exists() or not trusted(OVERRIDE_FILE):
        return None
    try:
        o = json.loads(OVERRIDE_FILE.read_text())
        created = datetime.fromisoformat(o["created"])
        until = datetime.fromisoformat(o["until"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = now or datetime.now(timezone.utc)
    if until <= now or until - created > timedelta(minutes=MAX_OVERRIDE_MINUTES):
        return None
    return o


def write_override(minutes, reason, now=None):
    """Write the override. Caller must already be root; this does not escalate."""
    if not 1 <= minutes <= MAX_OVERRIDE_MINUTES:
        raise ValueError(f"minutes must be between 1 and {MAX_OVERRIDE_MINUTES}")
    if not (reason or "").strip():
        raise ValueError("a reason is required; it is recorded with every allow")
    now = now or datetime.now(timezone.utc)
    o = {"created": now.isoformat(), "until": (now + timedelta(minutes=minutes)).isoformat(),
         "reason": reason.strip(), "by": os.environ.get("SUDO_USER") or "root"}
    SYSTEM_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = OVERRIDE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(o, indent=2))
    os.chmod(tmp, 0o644)
    tmp.replace(OVERRIDE_FILE)
    return o


def end_override():
    try:
        OVERRIDE_FILE.unlink()
        return True
    except FileNotFoundError:
        return False
