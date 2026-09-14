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
* LINK-LOSS RETRIES: a UI command the link died under (a get mid-stream,
  a put mid-pull) is not reported — it is held and re-run by the seat that
  comes back after the pause, with "retry1 in Ns: …" then "retry1: …" in
  the log, up to three times; the fourth loss reports ONE failure. A Next
  that hangs up mid-command (FIN, no eviction) leaves the worker LISTENING
  for it: the re-dial is a fresh seat and the retry runs there; with no
  re-dial the deadline passes, one failure is reported and the worker
  ends. With Sessions On nothing is retried (a survivor might be another
  machine). An rmtree walk is never retried: cut between steps it settles
  one op_done(False, "delete", root). A verify-after-put owed settles its
  put_done(True) with no error beside it. A Poll that raced the eviction
  (the old seat's idle 'I' landing on the shut socket) reports NOTHING.
* The hook is read PER DIAL: flipped On, a newcomer gets a second seat;
  flipped Off again, the next dialer evicts every seat and inherits the
  DRIVEN one's sid. control['max_peers'] follows the mode. A hook that
  raises reads as On.

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

# A port PER SCENARIO: six servers sharing one meant a socket still in
# TIME_WAIT (or a worker thread a beat from dying) could fail the next
# scenario's bind - measured flaky under load, and a bind failure is
# exactly the path review round 2 found broken.
_PORTS = iter(range(2059, 2099))
PORT = next(_PORTS)
ok = True


ADDR = "127.0.0.1"


def next_port():
    global PORT
    PORT = next(_PORTS)
    return PORT

# The retry pause and the no-seat deadline, shortened for the suite (read
# at use time from the module, so a rebinding here is what the worker sees).
PAUSE = 0.4
WAIT = 3.0
zxnu_workers.RE_LINK_RETRY_PAUSE_S = PAUSE
zxnu_workers.RE_LINK_RETRY_WAIT_S = WAIT


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


def reply(sock, payload, pkt=0):
    """Push one framed block (packet number ``pkt`` — a multi-block reply
    must count up, or the server reads a repeat as a retransmission) and
    read the server's 'Ok' ack."""
    sock.sendall(frame(payload, pkt))
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


def logged(state, text, since=0):
    return any(text in m for m in state["logs"][since:])


def start_get(sock, remote=b"/big.bin"):
    """Drive a get up to its first data block, so the link can be cut
    mid-stream: the server's 'G', then N (pkt 0) and D (pkt 1) from us."""
    got = poll(sock)
    check("the get is sent", got == b"G" + remote, got)
    reply(sock, b"N" + b"\0\0\0\0" + bytes([7]) + b"big.bin", 0)
    reply(sock, b"D" + b"x" * 100, 1)


def finish_get(sock):
    """The retried get from the top: N, D, E, B — then the file is 'got'."""
    reply(sock, b"N" + b"\0\0\0\0" + bytes([7]) + b"big.bin", 0)
    reply(sock, b"D" + b"y" * 100, 1)
    reply(sock, b"E", 2)
    reply(sock, b"B", 3)


def evict(state, socks, old, label):
    """Dial a newcomer, expect the old link dropped, the seat unchanged."""
    n_peers = len(state["peers"])
    new = connect_next()
    socks.append(new)
    check(f"{label}: seated", rx_payload(new) == b"Listening")
    check(f"{label}: old link dropped", dropped(old))
    check(f"{label}: roster unchanged (seat #1)",
          seat_after(state, n_peers, (1, [(1, ADDR)])), roster(state))
    return new


def test_single_seat():
    next_port()
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
              logged(state, "dialed in while") and logged(state, "Sessions is Off"),
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

        # ---- a put mid-pull when the link is replaced: RETRIED ----------
        fd, tmp = tempfile.mkstemp(suffix=".bin")
        os.write(fd, bytes(range(256)) * 12)                 # 3072 B = 6 frames
        os.close(fd)
        cmd_q.put(("put", tmp, "/dest.bin"))
        got = poll(b)
        check("the put header reaches the Next", got == b"P/dest.bin", got)
        b.sendall(b"Get")
        first = rx_payload(b)
        check("the Next pulled the first frame", len(first) == 512, len(first))
        n_log = len(state["logs"])
        c = evict(state, socks, b, "put cut")
        check("the cut put is HELD, not reported",
              wait_until(lambda: logged(state, "retry1 in", n_log))
              and state["put_done"] == [], (state["put_done"],
                                            state["logs"][n_log:]))
        check("...and no error either", state["errors"] == [], state["errors"])
        answer_version(c)
        time.sleep(PAUSE + 0.3)
        got = poll(c)
        check("retry1: the put is sent again from the top",
              got == b"P/dest.bin" and logged(state, "retry1: put /dest.bin", n_log),
              (got, state["logs"][n_log:]))
        for _ in range(6):
            c.sendall(b"Get")
            rx_payload(c)
        check("the retried put lands (one put_done(True))",
              wait_until(lambda: state["put_done"] == [(True, "/dest.bin")]),
              state["put_done"])
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
        f = evict(state, socks, e, "parked")
        answer_version(f)
        got = poll(f)
        check("the parked targeted command is served by the newcomer",
              got == b"M/parked", got)
        reply(f, b"O")
        res = br.wait(5.0)
        check("...and its bridge caller gets a real answer, not a 410",
              res == {"ok": True}, res)

        # ---- an rmtree walk parked between steps: never retried ---------
        n_ops = len(state["ops"])
        cmd_q.put(("rmtree", "/tree"))
        got = poll(f)
        check("the walk starts with the root listing", got == b"L/tree", got)
        entry = bytes([0]) + (10).to_bytes(4, "little") + bytes([5]) + b"a.tap"
        reply(f, b"D" + entry, 0)
        reply(f, b"E", 1)
        # The walk queued rm + rmdir on local_cmds and now waits for a Poll.
        n_err = len(state["errors"])
        g = evict(state, socks, f, "rmtree cut")
        check("the cut walk settles exactly one op_done(False, delete, root)",
              wait_until(lambda: state["ops"][n_ops:] == [(False, "delete", "/tree")]),
              state["ops"][n_ops:])
        time.sleep(0.5)
        check("...with no error beside it", len(state["errors"]) == n_err,
              state["errors"][n_err:])
        check("...and nothing held for retry", control.get("retry") is None)
        answer_version(g)
        check("the newcomer does not inherit the dead walk's steps",
              poll(g) == b"I")

        # ---- a get cut mid-stream: retried on the returning seat -------
        n_log = len(state["logs"])
        n_err = len(state["errors"])
        cmd_q.put(("get", "/big.bin", tdir))
        start_get(g)
        h = evict(state, socks, g, "get cut")
        check("the cut get is held (retry1 announced), no error",
              wait_until(lambda: logged(state, "retry1 in", n_log))
              and len(state["errors"]) == n_err, state["errors"][n_err:])
        answer_version(h)
        time.sleep(PAUSE + 0.3)
        got = poll(h)
        check("retry1: the get is sent again", got == b"G/big.bin", got)
        check("...and logged as retry1", logged(state, "retry1: get /big.bin", n_log))
        finish_get(h)
        check("the retried get lands (got)",
              wait_until(lambda: len(state["got"]) == 1
                         and state["got"][0][0] == "/big.bin"), state["got"])
        check("no error for the whole episode", len(state["errors"]) == n_err,
              state["errors"][n_err:])

        # ---- three retries spent: the fourth loss reports ONE failure --
        n_log = len(state["logs"])
        n_err = len(state["errors"])
        n_got = len(state["got"])
        cmd_q.put(("get", "/big.bin", tdir))
        start_get(h)
        cur = h
        for k in (1, 2, 3):
            cur = evict(state, socks, cur, f"loss {k}")
            check(f"retry{k} announced", wait_until(lambda: logged(
                state, f"retry{k} in", n_log)))
            answer_version(cur)
            time.sleep(PAUSE + 0.3)
            got = poll(cur)
            check(f"retry{k}: the get is sent again", got == b"G/big.bin", got)
            reply(cur, b"N" + b"\0\0\0\0" + bytes([7]) + b"big.bin", 0)
        last = evict(state, socks, cur, "loss 4")
        check("the fourth loss reports exactly one failure",
              wait_until(lambda: len(state["errors"]) == n_err + 1),
              state["errors"][n_err:])
        time.sleep(0.5)
        check("...only one", len(state["errors"]) == n_err + 1, state["errors"][n_err:])
        check("...logged as giving up after retry3",
              logged(state, "retry3 failed", n_log), state["logs"][n_log:][-3:])
        check("...nothing held any more", control.get("retry") is None)
        check("...and no got", len(state["got"]) == n_got)
        answer_version(last)
        check("the seat idles after that", poll(last) == b"I")

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
            last.sendall(b"Poll")         # nothing queued: an 'I' is coming...
            time.sleep(0.3)               # ...and is parked inside the gate
            i = connect_next()
            socks.append(i)
            check("seated", rx_payload(i) == b"Listening")
            time.sleep(0.2)
            gate["ev"].set()              # the 'I' now lands on the shut socket
        finally:
            zxnu_workers._re_sendpacket = orig_send
        check("old link dropped", dropped(last))
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


def test_hangup_then_redial_is_retried():
    """The field case: the Next's link dies mid-get and it HANGS UP (a FIN,
    no eviction). The worker must keep listening for it instead of ending,
    seat the re-dial as a fresh session, and run the held get there."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    th, state = start_server(cmd_q, stop, lambda: False, control)
    socks = []
    tdir = tempfile.mkdtemp(prefix="zxnu-redial-")
    try:
        m = connect_next()
        socks.append(m)
        check("redial: seated", rx_payload(m) == b"Listening")
        wait_until(lambda: state["connected"] == 1)
        cmd_q.put(("get", "/big.bin", tdir))
        start_get(m)
        n_log = len(state["logs"])
        m.close()                                  # the Next hangs up mid-get
        socks.remove(m)
        check("redial: the cut get is held (retry1 announced)",
              wait_until(lambda: logged(state, "retry1 in", n_log)),
              state["logs"][n_log:])
        check("redial: the roster emptied without a disconnected",
              wait_until(lambda: roster(state) == (None, []))
              and state["disconnected"] == 0, (roster(state), state["disconnected"]))
        time.sleep(1.2)
        check("redial: the worker keeps listening for the Next", th.is_alive())
        check("redial: nothing reported meanwhile",
              state["errors"] == [] and state["got"] == [])
        n = connect_next()                         # the Listener dials again
        socks.append(n)
        check("redial: the re-dial is seated", rx_payload(n) == b"Listening")
        check("redial: ...as a fresh connection (connected fired again)",
              wait_until(lambda: state["connected"] == 2), state["connected"])
        time.sleep(PAUSE + 0.3)
        got = poll(n)
        check("redial: retry1 runs on the returning seat", got == b"G/big.bin", got)
        finish_get(n)
        check("redial: the retried get lands",
              wait_until(lambda: len(state["got"]) == 1), state["got"])
        check("redial: no error at all", state["errors"] == [], state["errors"])
        n.close()
        socks.remove(n)
        check("redial: worker exits when the last Next leaves (nothing held)",
              wait_until(lambda: not th.is_alive(), timeout=10.0))
        check("redial: disconnected fired exactly once", state["disconnected"] == 1)
    finally:
        stop.set()
        close_all(socks)
        th.join(timeout=10)
        shutil.rmtree(tdir, ignore_errors=True)


def test_no_redial_gives_up():
    """No Next comes back: the deadline passes, the held command gets its
    one failure report, the worker ends."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    th, state = start_server(cmd_q, stop, lambda: False, control)
    socks = []
    tdir = tempfile.mkdtemp(prefix="zxnu-noredial-")
    try:
        o = connect_next()
        socks.append(o)
        check("give-up: seated", rx_payload(o) == b"Listening")
        wait_until(lambda: state["connected"] == 1)
        cmd_q.put(("get", "/big.bin", tdir))
        start_get(o)
        o.close()
        socks.remove(o)
        check("give-up: the get is held first",
              wait_until(lambda: logged(state, "retry1 in")))
        check("give-up: one failure once the deadline passes",
              wait_until(lambda: len(state["errors"]) == 1, timeout=WAIT + 6.0),
              state["errors"])
        check("give-up: ...naming the abandoned retry",
              logged(state, "abandoned") and "did not come back" in state["errors"][0],
              state["errors"])
        check("give-up: the worker then ends",
              wait_until(lambda: not th.is_alive(), timeout=10.0))
        check("give-up: disconnected fired exactly once", state["disconnected"] == 1)
        check("give-up: nothing held", control.get("retry") is None)
    finally:
        stop.set()
        close_all(socks)
        th.join(timeout=10)
        shutil.rmtree(tdir, ignore_errors=True)


def test_sessions_on_never_retries():
    """Sessions On: a survivor might be another machine, so a cut command
    is reported at once and the worker ends with the last seat, as before."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    th, state = start_server(cmd_q, stop, lambda: True, control)
    socks = []
    tdir = tempfile.mkdtemp(prefix="zxnu-multi-")
    try:
        p = connect_next()
        socks.append(p)
        check("multi: seated", rx_payload(p) == b"Listening")
        wait_until(lambda: state["connected"] == 1)
        cmd_q.put(("get", "/big.bin", tdir))
        start_get(p)
        p.close()
        socks.remove(p)
        check("multi: the cut get reports one failure at once",
              wait_until(lambda: len(state["errors"]) == 1), state["errors"])
        check("multi: nothing held", control.get("retry") is None)
        check("multi: the worker ends with its last seat",
              wait_until(lambda: not th.is_alive(), timeout=10.0))
        check("multi: no retry line in the log",
              not logged(state, "retry1"), [m for m in state["logs"] if "retry" in m])
    finally:
        stop.set()
        close_all(socks)
        th.join(timeout=10)
        shutil.rmtree(tdir, ignore_errors=True)


def test_verify_owed_at_eviction():
    """Verify CRC on: the 'K' wait is the longest window in a verified copy.
    A re-dial that lands inside it must settle the put's ONE put_done
    (kept, unverified) and nothing else - never re-send the file."""
    next_port()
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
        k = evict(state, socks, j, "verify cut")   # the re-dial lands in the wait
        check("verify: the owed put settles exactly one put_done(True) (kept, unverified)",
              wait_until(lambda: state["put_done"] == [(True, "/v.bin")]),
              state["put_done"])
        time.sleep(0.5)
        check("verify: ...and no error beside it", state["errors"] == [],
              state["errors"])
        check("verify: ...and nothing held for retry (the file landed)",
              control.get("retry") is None)
        answer_version(k)
        check("verify: the seat idles (no re-send)", poll(k) == b"I")
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
    next_port()
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


def test_bind_failure_still_reports():
    """Review round 2: the port is already taken. The worker must say so AND
    emit `disconnected` - its finally used to run control.pop() on the None
    DEFAULT (an AttributeError), so the pane never relistened and a widget
    operation never ended."""
    port = next_port()
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", port))
    blocker.listen(1)
    cmd_q, stop = queue.Queue(), threading.Event()
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    seen = {"busy": [], "disc": 0, "err": []}
    sig.port_in_use.connect(lambda p: seen["busy"].append(p), Qt.DirectConnection)
    sig.disconnected.connect(lambda: seen.update(disc=seen["disc"] + 1),
                             Qt.DirectConnection)
    sig.error.connect(lambda m: seen["err"].append(m), Qt.DirectConnection)
    # NO control= : the parameter's None default is the whole point.
    th = threading.Thread(target=run_remote_listen_server,
                          args=(sig, cmd_q, stop), kwargs={"port": port},
                          daemon=True)
    th.start()
    check("bind failure: the worker ends", wait_until(lambda: not th.is_alive()))
    check("bind failure: the port is reported in use", seen["busy"] == [port],
          seen["busy"])
    check("bind failure: disconnected still fires (the pane can relisten)",
          seen["disc"] == 1, seen["disc"])
    check("bind failure: no server error beside it", seen["err"] == [], seen["err"])
    stop.set()
    th.join(timeout=5)
    blocker.close()


def test_stale_retry_is_never_run():
    """Review round 2: a session that outlives its worker's finally can leave
    a retry behind. The NEXT worker must drop it - running it could write to
    a different machine - and never report it (its operation died with that
    worker's `disconnected`)."""
    port = next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    # A retry from an earlier run (generation 0; the worker starts at 1).
    control = {"seq": 0, "gen": 0,
               "retry": {"cmd": ("get", "/stale.bin", "."), "attempt": 1,
                         "due": 0.0, "deadline": time.monotonic() + 999,
                         "label": "get /stale.bin", "gen": 0}}
    th, state = start_server(cmd_q, stop, lambda: False, control)
    socks = []
    try:
        s = connect_next()
        socks.append(s)
        check("stale: seated", rx_payload(s) == b"Listening")
        wait_until(lambda: state["connected"] == 1)
        cmd_q.put(("mkdir", "/fresh"))
        got = poll(s)
        check("stale: the stale retry is NOT sent - the fresh command is",
              got == b"M/fresh", got)
        reply(s, b"O")
        check("stale: it was dropped from control", control.get("retry") is None)
        check("stale: and nothing was reported for it",
              state["errors"] == [] and state["put_done"] == [],
              (state["errors"], state["put_done"]))
        check("stale: the generation moved on", control.get("gen") == 1,
              control.get("gen"))
    finally:
        stop.set()
        close_all(socks)
        th.join(timeout=10)


if __name__ == "__main__":
    test_single_seat()
    test_hangup_then_redial_is_retried()
    test_no_redial_gives_up()
    test_sessions_on_never_retries()
    test_verify_owed_at_eviction()
    test_hook_that_raises_reads_as_on()
    test_bind_failure_still_reports()
    test_stale_retry_is_never_run()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
