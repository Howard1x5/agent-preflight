#!/usr/bin/env python3
"""Thin hook client for the gate service (ARCHITECTURE D7, stage A).

  python3 preflight_client.py gate|init|status   (registered in managed settings)

Relays the hook event to the service and relays its answer. Holds no state.

If the service cannot be reached, a PreToolUse event FAILS CLOSED: a stopped
service must not be a bypass, and stopping it is something an agent might
try. The operator's way out is the same root-only override the gate honours,
which this client checks itself because the service it would normally ask is
the thing that is down.
"""

import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authority  # noqa: E402

SOCKET = Path("/run/agent-preflight/gate.sock")
TIMEOUT = 15

UNREACHABLE = (
    "PREFLIGHT: the gate service is unreachable, so enforcement fails closed.\n\n"
    "This is an operator problem, not something to work around. The operator can\n"
    "check it with `systemctl status agent-preflight`."
)


def ask(kind, stdin_text, sock_path=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(TIMEOUT)
        s.connect(str(sock_path or SOCKET))
        s.sendall((json.dumps({"hook": kind, "stdin": stdin_text}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)


def unreachable(kind, stdin_text):
    if kind == "status":
        print("!! preflight: gate service DOWN")
        return 0
    if kind != "gate":
        return 0
    try:
        event = json.loads(stdin_text or "{}").get("hook_event_name", "PreToolUse")
    except ValueError:
        event = "PreToolUse"
    if event != "PreToolUse":
        return 0
    ov = authority.active_override()
    if ov:
        print(json.dumps({"systemMessage": f"PREFLIGHT ADMIN OVERRIDE until {ov.get('until')} "
                                           f"({ov.get('reason')}), gate service DOWN. "
                                           "Enforcement is NOT in effect."}))
        return 0
    print(UNREACHABLE, file=sys.stderr)
    return 2


def main(argv=None, sock_path=None):
    argv = argv if argv is not None else sys.argv[1:]
    kind = argv[0] if argv else "gate"
    stdin_text = sys.stdin.read()
    try:
        r = ask(kind, stdin_text, sock_path)
    except (OSError, ValueError):
        return unreachable(kind, stdin_text)
    if r.get("stdout"):
        sys.stdout.write(r["stdout"])
    if r.get("stderr"):
        sys.stderr.write(r["stderr"])
    return int(r.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
