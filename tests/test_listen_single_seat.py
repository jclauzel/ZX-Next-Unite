"""Single-seat -listen server: Settings → "NextSync — Sessions" Off (9.7.20).

What this locks down, against a real listen socket and fake Nexts:

* With the ``sessions`` hook answering False the server seats ONE Next. A
  second dialer is NOT turned away Busy (test_listen_busy.py pins the
  On behaviour): it EVICTS the held seat as the same machine coming back
  over a dead link — the old socket is shut down at once, the newcomer
  takes the seat under the SAME sid with the baton, and the roster the
  widget sees keeps that sid, so it never resets the pane. No second
  ``connected`` (the roster never emptied), no ``disconnected``.
* The newcomer is asked its build again ('Y' at its first Poll: the
  sid-keyed ident caches would otherwise keep the build that left), and
  inherits every targeted command parked on the old seat's queue.
* The evicted session pops nothing more from the shared queue even though
  it holds the same sid (the seat's evicted flag), and the queued commands
  are served by the newcomer.
* Exactly ONE report per command the eviction cuts — the widget counts
  each as a step: a put mid-pull settles put_done(False); a verify-after-
  put owed settles its put_done(True) with no error beside it; an rmtree
  walk parked between steps settles op_done(False, "delete", root); a
  get mid-stream reports one error; and a Poll that raced the eviction
  (the old seat's idle 'I' landing on the shut socket) reports NOTHING —
  that was a phantom "command lost" step before the review round.
* The hook is read PER DIAL: flipped On, a newcomer gets a second seat;
  flipped Off again, the next dialer evicts every seat and inherits the
  DRIVEN one's sid. control['max_peers'] follows the mode. A hook that
  raises reads as On.
* The last Next leaving still ends the worker exactly once.

Run with: python tests/test_listen_single_seat.py
"""
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication, Qt                  # noqa: E402
import zxnu_workers                                              # noqa: E402
from zxnu_workers import (RemoteExplorerSignals,                 # noqa: E402
                          run_remote_listen_server)
from zxnu_http_bridge import BridgeReply                         # noqa: E402

PORT = 2059
ok = True
ADDR = "127.0.0.1"


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


def reply(sock, payload):
    """Push one framed block and read the server's 'Ok' ack."""
    sock.sendall(frame(payload))
    return rx_payload(sock)


def answer_version(sock, ident=b"sync\x005.9.3"):
    """The re-seat's first Poll carries the 'Y' version query: answer it."""
    got = poll(sock)
    check("the re-seated Next is asked its build again ('Y' first)",
          got == b"Y", got)
    if got == b"Y":
        reply(sock, b"O" + ident)


def wait_until(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def dropped(sock, timeout=5.0):
    """True once the server has let this link go: recv returns EOF (its
    FIN) or fails (the reset a discarded close produces on Windows)."""
    sock.settimeout(timeout)
    try:
        return sock.recv(64) == b""
    except OSError:
        return True


def start_server(cmd_q, stop, sessions, control, verify=None):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": 0, "disconnected": 0, "peers": [], "logs": [],
             "errors": [], "put_done": [], "ops": [], "idents": [], "got": []}
    sig.connected.connect(lambda: state.update(
        connected=state["connected"] + 1), Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(
        disconnected=state["disconnected"] + 1), Qt.DirectConnection)
    sig.peers.connect(lambda p: state["peers"].append(p), Qt.DirectConnection)
    sig.log.connect(lambda m: state["logs"].append(m), Qt.DirectConnection)
    sig.error.connect(lambda m: state["errors"].append(m), Qt.DirectConnection)
    sig.put_done.connect(lambda okk, r: state["put_done"].append((okk, r)),
                         Qt.DirectConnection)
    sig.op_done.connect(lambda okk, op, p: state["ops"].append((okk, op, p)),
                        Qt.DirectConnection)
    sig.ident.connect(lambda t, n: state["idents"].append((t, n)),
                      Qt.DirectConnection)
    sig.got.connect(lambda r, l: state["got"].append((r, l)), Qt.DirectConnection)
    kwargs = {"port": PORT, "sessions": sessions, "control": control}
    if verify is not None:
        kwargs["verify_crc"] = verify
    th = threading.Thread(
        target=run_remote_listen_server,
        args=(sig, cmd_q, stop), kwargs=kwargs, daemon=True)
    th.start()
    return th, state


def connect_next():
    end = time.time() + 5.0
    while time.time() < end:
        try:
            s = socket.create_connection((ADDR, PORT), timeout=5)
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise AssertionError("listen server never came up")
    s.sendall(b"Listen")
    return s


def roster(state):
    return state["peers"][-1] if state["peers"] else None


def seat_after(state, n_before, expect):
    return wait_until(lambda: len(state["peers"]) > n_before
                      and roster(state) == expect)


def close_all(socks):
    for s in socks:
        try:
            s.close()
        except OSError:
            pass


def test_single_seat():
    cmd_q, stop = queue.Queue(), threading.Event()
    mode = {"on": False}                       # the Settings toggle, live
    control = {"seq": 0}
    th, state = start_server(cmd_q, stop, lambda: mode["on"], control)
    socks = []
    tmp = None
    tdir = tempfile.mkdtemp(prefix="zxnu-seat-")
    orig_send = zxnu_workers._re_sendpacket
    try:
        # ---- the first Next: a normal session, one seat ---------------
        a = connect_next()
        socks.append(a)
        check("first Next gets Listening", rx_payload(a) == b"Listening")
        check("connected fired once", wait_until(lambda: state["connected"] == 1))
        check("roster: seat #1 active, alone",
              wait_until(lambda: roster(state) == (1, [(1, ADDR)])), roster(state))
        check("control reports ONE seat while Sessions is Off",
              control.get("max_peers") == 1, control.get("max_peers"))
        cmd_q.put(("mkdir", "/from-a"))
        got = poll(a)
        check("the seated Next receives the command", got == b"M/from-a", got)
        reply(a, b"O")

        # ---- a second dialer EVICTS the first (same Next coming back) --
        n_peers = len(state["peers"])
        b = connect_next()
        socks.append(b)
        check("the newcomer is seated, not turned away Busy",
              rx_payload(b) == b"Listening")
        check("the old link is dropped at once", dropped(a))
        check("roster: the newcomer holds the SAME seat #1, alone",
              seat_after(state, n_peers, (1, [(1, ADDR)])), roster(state))
        check("no second connected (the roster never emptied)",
              state["connected"] == 1, state["connected"])
        check("no disconnected", state["disconnected"] == 0)
        check("the console names the eviction",
              any("dialed in while" in m and "Sessions is Off" in m
                  for m in state["logs"]),
              [m for m in state["logs"] if "Remote explorer" in m][-2:])
        check("the sid counter was NOT spent on the re-dial",
              control.get("seq") == 1, control.get("seq"))
        answer_version(b)
        check("the fresh ident reaches the widget under the same seat",
              wait_until(lambda: state["idents"][-1:] == [("sync", "5.9.3")]),
              state["idents"])
        check("an idle eviction reports no error at all", state["errors"] == [],
              state["errors"])

        # ---- the shared queue is served by the newcomer ---------------
        cmd_q.put(("mkdir", "/from-b"))
        got = poll(b)
        check("the newcomer holds the baton (receives the command)",
              got == b"M/from-b", got)
        reply(b, b"O")

        # ---- a put mid-pull when the link is replaced -----------------
        fd, tmp = tempfile.mkstemp(suffix=".bin")
        os.write(fd, bytes(range(256)) * 12)                 # 3072 B = 6 frames
        os.close(fd)
        cmd_q.put(("put", tmp, "/dest.bin"))
        got = poll(b)
        check("the put header reaches the Next", got == b"P/dest.bin", got)
        b.sendall(b"Get")
        first = rx_payload(b)
        check("the Next pulled the first frame", len(first) == 512, len(first))
        n_peers = len(state["peers"])
        c = connect_next()
        socks.append(c)
        check("the third dialer is seated (evicting the put's link)",
              rx_payload(c) == b"Listening")
        check("the pulling link is dropped", dropped(b))
        check("the abandoned put settles exactly one put_done(False)",
              wait_until(lambda: state["put_done"] == [(False, "/dest.bin")]),
              state["put_done"])
        time.sleep(0.5)
        check("...and no error signal for the same death (one step, not two)",
              state["errors"] == [], state["errors"])
        check("roster: still seat #1, alone",
              seat_after(state, n_peers, (1, [(1, ADDR)])), roster(state))
        answer_version(c)
        check("the newcomer's session then idles", poll(c) == b"I")

        # ---- the hook is read per dial: flipped On, a second seat -----
        mode["on"] = True
        n_peers = len(state["peers"])
        d = connect_next()
        socks.append(d)
        check("with Sessions On the newcomer gets a seat of its own",
              rx_payload(d) == b"Listening")
        check("roster: two seats, #1 still driven, the newcomer #2",
              seat_after(state, n_peers, (1, [(1, ADDR), (2, ADDR)])), roster(state))
        check("control reports the full table again",
              control.get("max_peers") == zxnu_workers.RE_MAX_PEERS,
              control.get("max_peers"))
        check("the held seat was NOT evicted (still serving)", poll(c) == b"I")
        check("the benched seat idles (no version query for a plain seat)",
              poll(d) == b"I")

        # ---- flipped Off again: the next dialer evicts EVERY seat ------
        mode["on"] = False
        n_peers = len(state["peers"])
        e = connect_next()
        socks.append(e)
        check("seated", rx_payload(e) == b"Listening")
        check("both held links are dropped", dropped(c) and dropped(d))
        check("roster: the newcomer inherits the DRIVEN seat's sid (#1), alone",
              seat_after(state, n_peers, (1, [(1, ADDR)])), roster(state))
        check("control back to one seat", control.get("max_peers") == 1)
        answer_version(e)
        cmd_q.put(("mkdir", "/from-e"))
        got = poll(e)
        check("the survivor drives the queue", got == b"M/from-e", got)
        reply(e, b"O")
        check("still exactly one connected", state["connected"] == 1)
        check("still no disconnected", state["disconnected"] == 0)

        # ---- a targeted command parked on the dead seat moves over -----
        br = BridgeReply()
        check("the bridge can target seat #1",
              control["enqueue_to"](1, ("mkdir", "/parked", br)))
        n_peers = len(state["peers"])
        f = connect_next()
        socks.append(f)
        check("seated", rx_payload(f) == b"Listening")
        check("old link dropped", dropped(e))
        check("roster unchanged", seat_after(state, n_peers, (1, [(1, ADDR)])))
        answer_version(f)
        got = poll(f)
        check("the parked targeted command is served by the newcomer",
              got == b"M/parked", got)
        reply(f, b"O")
        res = br.wait(5.0)
        check("...and its bridge caller gets a real answer, not a 410",
              res == {"ok": True}, res)

        # ---- an rmtree walk parked between steps ------------------------
        n_ops = len(state["ops"])
        cmd_q.put(("rmtree", "/tree"))
        got = poll(f)
        check("the walk starts with the root listing", got == b"L/tree", got)
        entry = bytes([0]) + (10).to_bytes(4, "little") + bytes([5]) + b"a.tap"
        reply(f, b"D" + entry)
        reply(f, b"E")
        # The walk queued rm + rmdir on local_cmds and now waits for a Poll.
        n_peers = len(state["peers"])
        n_err = len(state["errors"])
        g = connect_next()
        socks.append(g)
        check("seated", rx_payload(g) == b"Listening")
        check("old link dropped", dropped(f))
        check("the cut walk settles exactly one op_done(False, delete, root)",
              wait_until(lambda: state["ops"][n_ops:] == [(False, "delete", "/tree")]),
              state["ops"][n_ops:])
        time.sleep(0.5)
        check("...with no error beside it", len(state["errors"]) == n_err,
              state["errors"][n_err:])
        check("roster unchanged", seat_after(state, n_peers, (1, [(1, ADDR)])))
        answer_version(g)
        check("the newcomer does not inherit the dead walk's steps",
              poll(g) == b"I")

        # ---- a get cut mid-stream -----------------------------------------
        cmd_q.put(("get", "/big.bin", tdir))
        got = poll(g)
        check("the get is sent", got == b"G/big.bin", got)
        reply(g, b"N" + b"\0\0\0\0" + bytes([7]) + b"big.bin")
        reply(g, b"D" + b"x" * 100)
        n_peers = len(state["peers"])
        n_err = len(state["errors"])
        n_got = len(state["got"])
        h = connect_next()
        socks.append(h)
        check("seated", rx_payload(h) == b"Listening")
        check("old link dropped", dropped(g))
        check("the cut get reports exactly one error",
              wait_until(lambda: len(state["errors"]) == n_err + 1),
              state["errors"][n_err:])
        time.sleep(0.5)
        check("...only one", len(state["errors"]) == n_err + 1, state["errors"][n_err:])
        check("...and no got", len(state["got"]) == n_got, state["got"][n_got:])
        check("roster unchanged", seat_after(state, n_peers, (1, [(1, ADDR)])))
        answer_version(h)

        # ---- a Poll racing the eviction: no phantom "command lost" -------
        gate = {"armed": True, "ev": threading.Event()}

        def gated_send(conn, payload, pktno):
            if payload == b"I" and gate["armed"]:
                gate["armed"] = False
                gate["ev"].wait(5.0)      # let the accept thread evict us first
            return orig_send(conn, payload, pktno)
        zxnu_workers._re_sendpacket = gated_send
        n_err = len(state["errors"])
        n_peers = len(state["peers"])
        try:
            h.sendall(b"Poll")            # nothing queued: an 'I' is coming...
            time.sleep(0.3)               # ...and is parked inside the gate
            i = connect_next()
            socks.append(i)
            check("seated", rx_payload(i) == b"Listening")
            time.sleep(0.2)
            gate["ev"].set()              # the 'I' now lands on the shut socket
        finally:
            zxnu_workers._re_sendpacket = orig_send
        check("old link dropped", dropped(h))
        time.sleep(0.6)
        check("an idle reply that met the eviction reports NOTHING",
              len(state["errors"]) == n_err, state["errors"][n_err:])
        check("roster unchanged", seat_after(state, n_peers, (1, [(1, ADDR)])))
        answer_version(i)
        check("the newcomer idles", poll(i) == b"I")
        check("still exactly one connected", state["connected"] == 1)
        check("still no disconnected", state["disconnected"] == 0)

        # ---- the last one leaving ends the worker, once ---------------
        i.close()
        socks.remove(i)
        check("worker exits when the last Next leaves",
              wait_until(lambda: not th.is_alive(), timeout=10.0))
        check("disconnected fired exactly once", state["disconnected"] == 1)
    finally:
        zxnu_workers._re_sendpacket = orig_send
        stop.set()
        close_all(socks)
        th.join(timeout=10)
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        shutil.rmtree(tdir, ignore_errors=True)


def test_verify_owed_at_eviction():
    """Verify CRC on: the 'K' wait is the longest window in a verified copy.
    A re-dial that lands inside it must settle the put's ONE put_done
    (kept, unverified) and nothing else."""
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    th, state = start_server(cmd_q, stop, lambda: False, control,
                             verify=lambda: True)
    socks = []
    tmp = None
    try:
        j = connect_next()
        socks.append(j)
        check("verify: seated", rx_payload(j) == b"Listening")
        wait_until(lambda: state["connected"] == 1)
        fd, tmp = tempfile.mkstemp(suffix=".bin")
        os.write(fd, bytes(range(256)) * 12)
        os.close(fd)
        cmd_q.put(("put", tmp, "/v.bin"))
        check("verify: put header", poll(j) == b"P/v.bin")
        for _ in range(6):
            j.sendall(b"Get")
            rx_payload(j)
        time.sleep(0.2)
        check("verify: put_done is held back for the verdict",
              state["put_done"] == [], state["put_done"])
        got = poll(j)
        check("verify: the self-probe asks 'Y' first", got == b"Y", got)
        reply(j, b"O" + b"sync\x005.9.3")
        got = poll(j)
        check("verify: then the 'K' goes out", got == b"K/v.bin", got)
        n_peers = len(state["peers"])
        k = connect_next()                    # the re-dial lands in the wait
        socks.append(k)
        check("verify: seated", rx_payload(k) == b"Listening")
        check("verify: old link dropped", dropped(j))
        check("verify: the owed put settles exactly one put_done(True) (kept, unverified)",
              wait_until(lambda: state["put_done"] == [(True, "/v.bin")]),
              state["put_done"])
        time.sleep(0.5)
        check("verify: ...and no error beside it", state["errors"] == [],
              state["errors"])
        check("verify: roster unchanged",
              seat_after(state, n_peers, (1, [(1, ADDR)])), roster(state))
        answer_version(k)
        k.close()
        socks.remove(k)
        check("verify: worker exits when the last Next leaves",
              wait_until(lambda: not th.is_alive(), timeout=10.0))
    finally:
        stop.set()
        close_all(socks)
        th.join(timeout=10)
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def test_hook_that_raises_reads_as_on():
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}

    def broken():
        raise RuntimeError("settings unavailable")
    th, state = start_server(cmd_q, stop, broken, control)
    socks = []
    try:
        m = connect_next()
        socks.append(m)
        check("raising hook: the Next is still seated", rx_payload(m) == b"Listening")
        wait_until(lambda: state["connected"] == 1)
        check("raising hook: reads as On (the full table)",
              control.get("max_peers") == zxnu_workers.RE_MAX_PEERS,
              control.get("max_peers"))
        n = connect_next()
        socks.append(n)
        check("raising hook: a second dialer gets a seat of its own",
              rx_payload(n) == b"Listening")
        check("raising hook: two seats on the roster",
              wait_until(lambda: roster(state) is not None
                         and len(roster(state)[1]) == 2), roster(state))
        check("raising hook: the first link was not dropped", poll(m) == b"I")
    finally:
        stop.set()
        close_all(socks)
        th.join(timeout=10)


if __name__ == "__main__":
    test_single_seat()
    test_verify_owed_at_eviction()
    test_hook_that_raises_reads_as_on()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
