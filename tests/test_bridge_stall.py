"""Regression test: a stalled Next must never strand a blocked HTTP caller.

The bug this locks down (found from a real three-box tree copy: remote N-Go
dot -> PC listen server -> HTTP bridge -> ZXNextRemote). Copying a folder
transferred two files, then wedged on the third:

  * the session loop parks the socket at a 1 s timeout so idle polling stays
    responsive, and `get` read its reply under that same 1 s;
  * the Next legitimately goes quiet for longer than a second sometimes (SD
    seeks, its directory re-open/skip walk, its own retry backoff);
  * `socket.timeout` IS an `OSError`, so the exception escaped
    `_re_recv_reply`, escaped the `get` dispatch BEFORE `reply.put(...)`,
    and landed in the session-level handler — killing the whole -listen
    session and orphaning the sink the HTTP thread was blocked on;
  * that thread then waited out the entire bridge timeout receiving neither
    bytes nor a status, so the client saw pure silence and eventually gave
    up on its own. The dot meanwhile printed "get done" (it ignores the
    final block's ack result) and kept polling, looking perfectly healthy.

Three properties are asserted here:

  1. BridgeReply.put is idempotent — the first answer wins, so a
     blanket "resolve everything we still owe" on teardown can never
     clobber a real result the HTTP thread has not collected yet.
  2. A stalled command resolves its caller (with an error) instead of
     hanging, AND the session survives to serve the next command.
  3. A session that dies mid-command still answers whoever is blocked.

Run with: python test_bridge_stall.py
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
from zxnu_http_bridge import BridgeReply                         # noqa: E402

PORT = 2057
ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def frame(payload, pkt):
    c0 = c1 = 0
    for b in payload:
        c0 ^= b
        c1 = (c1 + c0) & 0xFF
    return ((len(payload) + 5).to_bytes(2, "big") + bytes(payload) +
            bytes([c0, c1, pkt & 0xFF]))


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


# ---------------------------------------------------------------- 1. sink
def test_reply_idempotent():
    r = BridgeReply()
    check("reply: starts unresolved", r.resolved is False)
    check("reply: first put accepted", r.put({'ok': True, 'n': 1}) is True)
    check("reply: now resolved", r.resolved is True)
    check("reply: second put refused", r.put({'ok': False, 'error': 'late'}) is False)
    got = r.wait(1.0)
    check("reply: the FIRST answer is what the caller reads",
          got == {'ok': True, 'n': 1}, got)
    # The teardown path blanket-resolves everything it might owe; doing that
    # to an already-answered sink must not corrupt the queue.
    check("reply: nothing queued behind it", r.wait(0.05) is None)


# ------------------------------------------------------- 2 & 3. the session
def start_session(cmd_q, stop):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": False, "errors": []}
    # DirectConnection: no Qt event loop runs in this thread, so a queued
    # connection would never deliver and every state assertion would pass
    # vacuously (which is exactly what happened the first time).
    sig.connected.connect(lambda: state.update(connected=True),
                          Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(connected=False),
                             Qt.DirectConnection)
    sig.error.connect(lambda m: state["errors"].append(m), Qt.DirectConnection)
    sig.log.connect(lambda m: state["errors"].append(m), Qt.DirectConnection)
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


def test_stall_resolves_and_session_survives():
    # A real minute is not worth waiting for in a test; the helper reads the
    # module attribute at call time exactly so this is possible.
    zxnu_workers.RE_REPLY_TIMEOUT = 1.5

    cmd_q, stop = queue.Queue(), threading.Event()
    th, state = start_session(cmd_q, stop)
    dot = connect_dot()
    try:
        # --- a get whose reply never arrives ---------------------------
        r1 = BridgeReply()
        cmd_q.put(("get", "/getit/install.bas", os.path.dirname(__file__), r1))
        dot.sendall(b"Poll")
        cmd = rx_payload(dot)
        check("stall: the server issued the get", cmd[0:1] == b'G', cmd[:20])
        # ...and now the "Next" says nothing at all, like a dot that burned
        # its retries on the final block and went back to polling.

        t0 = time.time()
        res = r1.wait(20.0)
        waited = time.time() - t0
        check("stall: the blocked caller IS answered (not left hanging)",
              res is not None, res)
        check("stall: answered as a failure", bool(res) and res.get('ok') is False, res)
        check("stall: answered promptly, not after the bridge timeout",
              waited < 15.0, f"{waited:.1f}s")

        # --- and the session is still alive ----------------------------
        # THE property that regressed: before the per-command timeout, the
        # escaping socket.timeout tore the whole session down, so this poll
        # met a closed socket. Catch that rather than crashing, so a
        # regression reads as a FAIL line instead of a traceback.
        r2 = BridgeReply()
        cmd_q.put(("mkdir", "/afterwards", r2))
        try:
            dot.sendall(b"Poll")
            cmd2 = rx_payload(dot)
            alive = cmd2[0:1] == b'M'
            detail = cmd2[:20]
        except (OSError, AssertionError) as ex:
            alive, detail = False, f"session died: {ex!r}"
        check("stall: session SURVIVED - next command still dispatched",
              alive, detail)
        if alive:
            dot.sendall(frame(b'O', 0))
            rx_payload(dot)                      # the server's ack
            res2 = r2.wait(10.0)
            check("stall: the follow-up command answers normally",
                  bool(res2) and res2.get('ok') is True, res2)
    finally:
        stop.set()
        try:
            dot.close()
        except OSError:
            pass
        th.join(timeout=10)
        zxnu_workers.RE_REPLY_TIMEOUT = 60.0


def test_dropped_link_resolves_caller():
    """The dot vanishing mid-command must answer the blocked caller too."""
    zxnu_workers.RE_REPLY_TIMEOUT = 5.0
    cmd_q, stop = queue.Queue(), threading.Event()
    th, state = start_session(cmd_q, stop)
    dot = connect_dot()
    try:
        r = BridgeReply()
        cmd_q.put(("get", "/whatever.bin", os.path.dirname(__file__), r))
        dot.sendall(b"Poll")
        check("drop: the server issued the get", rx_payload(dot)[0:1] == b'G')
        dot.close()                       # the Next disappears mid-transfer
        res = r.wait(20.0)
        check("drop: the blocked caller IS answered", res is not None, res)
        check("drop: answered as a failure",
              bool(res) and res.get('ok') is False, res)
    finally:
        stop.set()
        th.join(timeout=10)
        zxnu_workers.RE_REPLY_TIMEOUT = 60.0


def test_silent_peer_is_reaped():
    """A Next that is switched off never sends a FIN — the socket just goes
    quiet. Before this, the session sat "connected" forever and the UI kept
    claiming a Next was attached (reported after a machine had been off for
    hours). The peer drives the session and polls continuously when idle,
    so silence is the signal; no probe is needed."""
    zxnu_workers.PEER_SILENCE_LIMIT = 2.0
    zxnu_workers.RE_REPLY_TIMEOUT = 5.0
    cmd_q, stop = queue.Queue(), threading.Event()
    th, state = start_session(cmd_q, stop)
    dot = connect_dot()
    try:
        # Behave normally first, so this is a live session going quiet
        # rather than one that never started.
        dot.sendall(b"Poll")
        check("silent: a poll is answered while alive",
              len(rx_payload(dot)) >= 1)
        # The accept loop starts the session thread BEFORE it emits
        # connected, so the poll reply above can beat the signal on a
        # loaded machine (CI run 31823405610) — and a missed True here
        # would also let the wait below return vacuously before the
        # silence limit ever fired. Poll for the verdict instead.
        check("silent: session reports connected",
              wait_until(lambda: state["connected"] is True, timeout=10.0))

        # ...and now the Next "loses power": no FIN, no RST, just silence.
        t0 = time.time()
        ended = wait_until(lambda: state["connected"] is False, timeout=20.0)
        waited = time.time() - t0
        check("silent: the dead peer IS noticed", ended, state)
        check("silent: noticed at about the limit, not forever",
              waited < 12.0, f"{waited:.1f}s")
        check("silent: it says why, in the log",
              any("no word from the Next" in e for e in state["errors"]),
              state["errors"][-1:])
    finally:
        stop.set()
        try:
            dot.close()
        except OSError:
            pass
        th.join(timeout=10)
        zxnu_workers.PEER_SILENCE_LIMIT = 45.0
        zxnu_workers.RE_REPLY_TIMEOUT = 60.0


def test_long_timeout_under_client_patience():
    """The bridge must give up BEFORE its strictest client does, or the user
    sees silence instead of a 504 naming the stalled operation."""
    from zxnu_http_bridge import LONG_TIMEOUT
    # ZXNextRemote's HTTP_FIRSTBYTE_TICKS is 5 minutes (300 s).
    check("timeout: bridge answers before a 300 s client gives up",
          LONG_TIMEOUT < 300.0, LONG_TIMEOUT)
    check("timeout: still long enough for a real relayed transfer",
          LONG_TIMEOUT >= 120.0, LONG_TIMEOUT)


if __name__ == "__main__":
    test_reply_idempotent()
    test_long_timeout_under_client_patience()
    test_stall_resolves_and_session_survives()
    test_dropped_link_resolves_caller()
    test_silent_peer_is_reaped()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
