"""Multi-Next listen server (option B) + the turn-away and handshake fixes.

What this locks down, in the order the hardware taught it:

* A second '.sync5 -L' used to sit in the TCP backlog until its timeout
  and then blame the server's age. Option A answered a framed "Busy";
  option B goes further: the second Next gets a REAL session and a combo
  in the UI switches which machine the command queue drives. "Busy" is
  now the over-capacity answer (RE_MAX_PEERS).
* Only the ACTIVE session pops the shared command queue; benched
  sessions answer their Next's polls with the idle reply. A
  ("select_next", sid) hands the baton over.
* The handshake is read PATIENTLY (2026-08-13 field report): the old
  single recv on a 1 s-inherited timeout misread a split/late "Listen"
  (or an ESP connect-retry probe that closed silently) as "did not
  request -listen mode" AND EXITED THE WHOLE SERVER — the "Remote
  Explorer server stops by itself" console loop. Split keywords and
  silent probes must both leave the server serving.
* The active Next leaving hands the baton to a survivor without a
  disconnected signal; only the LAST one leaving ends the worker.

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
import zxnu_workers                                              # noqa: E402
from zxnu_workers import (RemoteExplorerSignals,                 # noqa: E402
                          run_remote_listen_server)

PORT = 2058
ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def frame(payload, pkt=0):
    c0 = c1 = 0
    for b in payload:
        c0 ^= b
        c1 = (c1 + c0) & 0xFF
    return ((len(payload) + 5).to_bytes(2, "big") + bytes(payload) +
            bytes([c0, c1, pkt & 0xFF]))


def rx_payload(sock, timeout=10.0):
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


def poll(sock, timeout=10.0):
    sock.sendall(b"Poll")
    return rx_payload(sock, timeout)


def wait_until(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def start_session(cmd_q, stop):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": 0, "disconnected": 0, "peers": [], "logs": []}
    sig.connected.connect(lambda: state.update(
        connected=state["connected"] + 1), Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(
        disconnected=state["disconnected"] + 1), Qt.DirectConnection)
    sig.peers.connect(lambda p: state["peers"].append(p), Qt.DirectConnection)
    sig.log.connect(lambda m: state["logs"].append(m), Qt.DirectConnection)
    th = threading.Thread(
        target=run_remote_listen_server,
        args=(sig, cmd_q, stop), kwargs={"port": PORT}, daemon=True)
    th.start()
    return th, state


def connect_next(handshake=b"Listen"):
    end = time.time() + 5.0
    while time.time() < end:
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise AssertionError("listen server never came up")
    if handshake:
        s.sendall(handshake)
    return s


def test_multi_next():
    cmd_q, stop = queue.Queue(), threading.Event()
    th, state = start_session(cmd_q, stop)
    socks = []
    try:
        # ---- a silent probe must not kill the server ------------------
        probe = connect_next(handshake=b"")
        probe.close()

        # ---- first Next: normal session -------------------------------
        a = connect_next()
        socks.append(a)
        check("first Next gets Listening", rx_payload(a) == b"Listening")
        check("connected fired once",
              wait_until(lambda: state["connected"] == 1))
        check("probe did not kill the server (still serving)",
              len(poll(a)) >= 1)

        # ---- split handshake: "Lis" + "ten" ---------------------------
        b = connect_next(handshake=b"Lis")
        socks.append(b)
        time.sleep(0.3)
        b.sendall(b"ten")
        check("split handshake still gets Listening",
              rx_payload(b) == b"Listening")
        check("no second connected signal (roster grew instead)",
              state["connected"] == 1)
        check("roster shows two Nexts with #1 active",
              wait_until(lambda: state["peers"] and
                         state["peers"][-1][0] == 1 and
                         len(state["peers"][-1][1]) == 2),
              state["peers"][-1:])

        # ---- only the ACTIVE session pops the queue -------------------
        cmd_q.put(("mkdir", "/from-active"))
        check("benched Next polls idle", poll(b) == b"I")
        got = poll(a)
        check("active Next receives the command", got[0:1] == b"M", got)
        a.sendall(frame(b"O"))
        rx_payload(a)                        # the server's ack

        # ---- select_next hands the baton over -------------------------
        cmd_q.put(("select_next", 2))
        check("switch answered with an idle to whoever polled it",
              poll(a) == b"I")
        check("roster now shows #2 active",
              wait_until(lambda: state["peers"] and
                         state["peers"][-1][0] == 2),
              state["peers"][-1:])
        cmd_q.put(("mkdir", "/from-new-active"))
        check("old active now polls idle", poll(a) == b"I")
        got = poll(b)
        check("new active receives the command", got[0:1] == b"M", got)
        b.sendall(frame(b"O"))
        rx_payload(b)

        # ---- over capacity: the option-A Busy turn-away ---------------
        extras = []
        for _ in range(zxnu_workers.RE_MAX_PEERS - 2):
            e = connect_next()
            extras.append(e)
            socks.append(e)
            assert rx_payload(e) == b"Listening"
        over = connect_next()
        socks.append(over)
        check("one past the cap gets the framed Busy",
              rx_payload(over) == b"Busy")

        # ---- the active Next leaving hands the baton on ---------------
        b.close()                            # active (#2) hangs up
        check("baton moves to a survivor, no disconnected",
              wait_until(lambda: state["peers"] and
                         state["peers"][-1][0] not in (None, 2)) and
              state["disconnected"] == 0,
              state["peers"][-1:])

        # ---- last one leaving ends the worker (pane relistens) --------
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
        socks = []
        check("worker exits when the last Next leaves",
              wait_until(lambda: not th.is_alive(), timeout=10.0))
        check("disconnected fired exactly once", state["disconnected"] == 1)
    finally:
        stop.set()
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
        th.join(timeout=10)


if __name__ == "__main__":
    test_multi_next()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
