"""Regression test: a second Next knocking mid-session is turned away
with a framed "Busy" — promptly — and the live session never notices.

The bug this locks down (2026-08-13, two-machine hardware round): the
Remote Explorer listen server accepts ONE connection and then serves it,
but the listening socket keeps its backlog — so a second '.sync5 -L'
completed its TCP connect silently, sent "Listen", and waited for a
"Listening" that never came. The dot hung for its whole timeout and then
printed the only failure line it has for that branch, the misleading
"Server too old (-listen)". The fix sweeps the backlog once per session
turn and answers a framed "Busy": busy-aware clients (dotN 5.7.2+,
ZXNextRemote 0.9.5+) print the truth, and even a stock dot now fails
instantly instead of hanging.

Run with: python test_listen_busy.py
"""
import os
import queue
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication, Qt                  # noqa: E402
from zxnu_workers import (RemoteExplorerSignals,                 # noqa: E402
                          run_remote_listen_server)

PORT = 2058
ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def rx_payload(sock, timeout=10.0):
    """Read one framed block, returning its payload."""
    sock.settimeout(timeout)
    hdr = b""
    while len(hdr) < 2:
        chunk = sock.recv(2 - len(hdr))
        if not chunk:
            raise AssertionError("peer closed while reading a block header")
        hdr += chunk
    total = (hdr[0] << 8) | hdr[1]
    rest = b""
    while len(rest) < total - 2:
        chunk = sock.recv(total - 2 - len(rest))
        if not chunk:
            raise AssertionError("peer closed mid-block")
        rest += chunk
    return rest[:-3]


def start_session(cmd_q, stop):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": False, "logs": []}
    sig.connected.connect(lambda: state.update(connected=True),
                          Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(connected=False),
                             Qt.DirectConnection)
    sig.log.connect(lambda m: state["logs"].append(m), Qt.DirectConnection)
    th = threading.Thread(
        target=run_remote_listen_server,
        args=(sig, cmd_q, stop), kwargs={"port": PORT}, daemon=True)
    th.start()
    return th, state


def wait_until(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def connect_dot():
    """Play the dot far enough to be a connected peer."""
    end = time.time() + 5.0
    while time.time() < end:
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise AssertionError("listen server never came up")
    s.sendall(b"Listen")
    assert rx_payload(s) == b"Listening"
    return s


def test_second_next_turned_away():
    cmd_q, stop = queue.Queue(), threading.Event()
    th, state = start_session(cmd_q, stop)
    dot = connect_dot()
    second = None
    try:
        # The FIRST Next is a live, served session.
        dot.sendall(b"Poll")
        check("busy: first Next is served", len(rx_payload(dot)) >= 1)

        # A SECOND Next knocks while the first is connected. Keep the
        # first one polling meanwhile — the sweep runs once per session
        # turn, and a session turn needs traffic or a 1 s timeout.
        second = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        second.sendall(b"Listen")

        t0 = time.time()
        deadline = time.time() + 10.0
        payload = None
        while time.time() < deadline and payload is None:
            dot.sendall(b"Poll")
            rx_payload(dot)                    # keep the session turning
            try:
                second.settimeout(0.3)
                payload = rx_payload(second, timeout=0.3)
            except (socket.timeout, AssertionError):
                payload = None
        waited = time.time() - t0

        check("busy: the newcomer gets a framed Busy", payload == b"Busy",
              payload)
        check("busy: promptly, not after a dot-sized timeout",
              waited < 5.0, f"{waited:.1f}s")
        # The Busy bytes reach the client a beat before the worker thread
        # executes its log line — poll briefly instead of racing it.
        check("busy: the server said so in its log",
              wait_until(lambda: any("turned away" in m
                                     for m in state["logs"])),
              state["logs"][-1:])

        # ...and the LIVE session never noticed a thing.
        dot.sendall(b"Poll")
        check("busy: first session still served afterwards",
              len(rx_payload(dot)) >= 1)
        check("busy: first session still reports connected",
              state["connected"] is True)
    finally:
        stop.set()
        for s in (dot, second):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        th.join(timeout=10)


if __name__ == "__main__":
    test_second_next_turned_away()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
