#!/usr/bin/env python3
"""The gate as a service under its own uid (ARCHITECTURE D7, stage A).

Runs as the dedicated `preflight` user with HOME=/var/lib/agent-preflight, so
records, gate state, tallies and the install secret live in a directory the
operator's uid -- and therefore the agent -- cannot read or write. Hooks become
thin clients (preflight_client.py) that relay each event here and relay the
answer back.

What stage A does and does not buy, stated rather than implied: the agent can
no longer delete or edit records or state files. It CAN still connect to the
socket and send a well-formed but fabricated event, because the hook and the
agent share a uid. A fabricated "query ran and confirmed" event is closed only
by stage B, where the service performs the required query itself.

Each request runs the existing hook code unchanged, in-process, with stdin,
stdout and stderr redirected and SystemExit captured. Modules are reloaded per
request so a rule change takes effect without a restart. The server is
single-threaded on purpose: gate state is read-modify-write, and serialising
requests is simpler and more obviously correct than locking it.
"""

import argparse
import contextlib
import importlib
import io
import json
import os
import socketserver
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

SOCKET = Path("/run/agent-preflight/gate.sock")
MAX_REQUEST = 1 << 20

HOOKS = {"gate": "preflight_gate", "init": "preflight_init", "status": "preflight_status"}
# Read-only operator tools, with fixed arguments. Nothing a client sends can
# choose a path for the service to write.
TOOLS = {"report": ("report", ["--summary"]), "verify": ("verify_chain", [])}
RELOAD = ("authority", "records", "rules", "preflight_gate", "preflight_init",
          "preflight_status", "report", "verify_chain")


def _fresh(name):
    for m in RELOAD:
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    return importlib.import_module(name)


def _capture(fn, stdin_text="", argv=None):
    out, err = io.StringIO(), io.StringIO()
    old_stdin, old_argv = sys.stdin, sys.argv
    sys.stdin = io.StringIO(stdin_text)
    if argv is not None:
        sys.argv = argv
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            r = fn()
            code = r if isinstance(r, int) else 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.stdin, sys.argv = old_stdin, old_argv
    return {"exit": code, "stdout": out.getvalue(), "stderr": err.getvalue()}


def handle(request):
    """One request dict in, one response dict out. Never raises."""
    kind = request.get("hook")
    try:
        if kind in HOOKS:
            mod = _fresh(HOOKS[kind])
            return _capture(mod.main, request.get("stdin") or "")
        if kind in TOOLS:
            name, args = TOOLS[kind]
            mod = _fresh(name)
            return _capture(mod.main, "", [name] + args)
    except Exception as e:  # noqa: BLE001 -- a crashed request must not kill the service
        if kind == "gate":
            return {"exit": 2, "stdout": "",
                    "stderr": f"PREFLIGHT: the gate service failed on this event ({type(e).__name__})."}
        return {"exit": 0, "stdout": "", "stderr": ""}
    return {"exit": 0, "stdout": "", "stderr": f"preflight service: unknown request {kind!r}"}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(MAX_REQUEST)
        try:
            request = json.loads(line)
        except ValueError:
            request = {}
        self.wfile.write((json.dumps(handle(request)) + "\n").encode())


def serve(socket_path, group_mode=0o660):
    socket_path = Path(socket_path)
    if socket_path.exists():
        socket_path.unlink()
    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    os.chmod(socket_path, group_mode)
    return server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default=str(SOCKET))
    args = ap.parse_args()
    serve(args.socket).serve_forever()


if __name__ == "__main__":
    main()
