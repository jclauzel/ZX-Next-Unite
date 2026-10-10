"""A QLNextRemote seat that went silent (9.7.45), against a real listen
socket and fake peers on OS-chosen ports.

The field case: a ZX Spectrum Next running the QL core, its QLNextRemote
Listener seated here, is hard-reset. The Next's ESP8266 (which the QL core
cannot reset) keeps the TCP link up, so no FIN arrives and the dead seat
used to sit, shown connected, for PEER_SILENCE_LIMIT (620 s). Disconnect
could not clear it - its quit is only served in reply to a Poll - and the
quit even stayed on the shared queue for the NEXT baton holder to pop.

Everything new is gated on the SESSION'S OWN 'Y' answer being exactly
"qlnextremote". What this locks down, with the limits shortened:

* A silent seat that answered "qlnextremote" is reaped at
  QLNR_PEER_SILENCE_LIMIT with the existing "no word from the Next" line;
  twins answering "sync" / "n2n" / "httpbridge", one never asked and one
  too old for 'Y' (("", "")) are NOT - they wait for PEER_SILENCE_LIMIT,
  exactly as before.
* A QL that has just finished a command longer than the QL limit (its
  reply exchange runs inside _re_reply_call and never touched last_rx)
  and then polls after a >1 s pause is NOT reaped.
* Disconnect's drop hook (control['drop_silent']) ends a marked, silent
  QL seat within about QLNR_DROP_SILENCE; a marked "sync" seat and a
  marked seat nobody asked are untouched, and the "sync" one still gets
  the plain marked quit ('Q' + 'X') at its next Poll. A LIVE marked QL is
  never dropped and still receives 'Q' + 'X'.
* The drop gate, piece by piece (review findings): never mid-put, the EOF
  pull after the last data frame included; never with a batch queued ahead
  of the quit (the rest would go to the next baton holder); never once the
  QL has been heard after the press (a stale mark is moot); not before
  QLNR_DROP_SILENCE has passed - with a threshold well above the 1 s recv
  tick - and counted from the end of the last command, not its start.
* With an "n2n" seat benched behind an ACTIVE dead QL, a Disconnect on
  the QL (the shared queue's plain quit, plus a bridge /forceexit's) never
  reaches the n2n seat once it holds the baton - whether the QL ends by the
  drop, by the QL limit or by EOF; the bridge's sink gets the 410, every
  other queued item stays, in order. A BENCHED QL that ends never touches
  the driven dot's own quit.
* Sessions Off: a newcomer that takes over an evicted QL seat is not told
  to exit by its plain quit - parked on the seat's own queue or, the
  button's real route, on the shared one; taking over a "sync" seat it
  gets that quit, as before.
* 9.7.47 widened the QL drop gate (shared with the Next's): a press that
  lands while a command waits on a QL that is already dead is not made
  moot by that wait's timeout, and reply-less reads plus a "Switch to this
  Next" queued ahead of the quit are looked past (they run on the next
  baton holder) - a write still is not (the batch case above).

Every fake peer here dials 127.0.0.1, which the worker takes for a seat
dialing from THIS PC (zxnu_workers._re_same_host; checked first below):
so since 9.7.47 the "sync" / "n2n" / "httpbridge" / never-asked / too-old
twins are LOCAL seats, and pin that an emulator on this PC keeps the 620 s
path, no drop and the old routing. The same seats dialing from another
machine follow the 9.7.47 Next rule: tests/test_next_stale_seat.py.

Run with: python tests/test_qlnr_stale_seat.py
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
                          run_remote_listen_server, RE_QUIT_EXIT_MARK)
from zxnu_http_bridge import BridgeReply                         # noqa: E402

ADDR = "127.0.0.1"
PORT = 0
ok = True

QL = b"qlnextremote\x001.1.4"
SYNC = b"sync\x005.9.13"
N2N = b"n2n\x001.5.4"
HTTPB = b"httpbridge\x001.5.4"
QUIT_X = b"Q" + RE_QUIT_EXIT_MARK
NO_WORD = "no word from the Next for"
DROPPED = "Disconnect closes its seat now"


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def next_port():
    """An OS-chosen port per scenario (see test_listen_single_seat.py's
    next_port: a fixed port let another test process dial into this run)."""
    global PORT
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("", 0))
        PORT = probe.getsockname()[1]
    finally:
        probe.close()
    return PORT


class Limits:
    """Patch the worker's timing constants for one scenario and put back
    exactly what was there (they are read at call time)."""
    NAMES = ("PEER_SILENCE_LIMIT", "QLNR_PEER_SILENCE_LIMIT",
             "QLNR_DROP_SILENCE", "RE_REPLY_TIMEOUT")

    def __init__(self, **kw):
        self.kw = kw
        self.saved = {}

    def __enter__(self):
        for n in self.NAMES:
            self.saved[n] = getattr(zxnu_workers, n)
        for n, v in self.kw.items():
            setattr(zxnu_workers, n, v)
        return self

    def __exit__(self, *exc):
        for n, v in self.saved.items():
            setattr(zxnu_workers, n, v)
        return False


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


def safe_poll(sock, timeout=5.0):
    """poll(), but a closed or reset link reads as None instead of raising."""
    try:
        return poll(sock, timeout)
    except (OSError, AssertionError):
        return None


def reply(sock, payload, pkt=0):
    """One framed block from the peer, then the server's ack."""
    sock.sendall(frame(payload, pkt))
    return rx_payload(sock)


def answer_y(sock, ident, label):
    """The seat's next Poll carries 'Y': answer it with ``ident`` (type NUL
    number), or - ident None - like a listener too old for 'Y', which
    ignores the opcode and simply polls again."""
    got = poll(sock)
    check(f"{label}: asked its build ('Y')", got == b"Y", got)
    if ident is None:
        sock.sendall(b"Poll")
        check(f"{label}: the stray Poll is answered idle",
              rx_payload(sock) == b"I")
    else:
        reply(sock, b"O" + ident)


def link_closed(sock, timeout=0.3):
    """True once the server has let this link go (EOF, or the reset a
    discarded close gives on Windows); False while it is still open."""
    sock.settimeout(timeout)
    try:
        return sock.recv(64) == b""
    except socket.timeout:
        return False
    except OSError:
        return True


def wait_until(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def start_server(cmd_q, stop, control, sessions=None):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": 0, "disconnected": 0, "peers": [], "logs": [],
             "errors": [], "idents": []}
    sig.connected.connect(lambda: state.update(
        connected=state["connected"] + 1), Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(
        disconnected=state["disconnected"] + 1), Qt.DirectConnection)
    sig.peers.connect(lambda p: state["peers"].append(p), Qt.DirectConnection)
    sig.log.connect(lambda m: state["logs"].append(m), Qt.DirectConnection)
    sig.error.connect(lambda m: state["errors"].append(m), Qt.DirectConnection)
    sig.ident.connect(lambda t, n: state["idents"].append((t, n)),
                      Qt.DirectConnection)
    kwargs = {"port": PORT, "control": control}
    if sessions is not None:
        kwargs["sessions"] = sessions
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
    got = rx_payload(s)
    if got != b"Listening":
        raise AssertionError(f"not seated: {got!r}")
    return s


def seated(control):
    """The live roster's sids (control['roster'] reads it under plock)."""
    fn = control.get("roster")
    if fn is None:
        return []
    return [s for s, _a in fn()[1]]


def active(control):
    fn = control.get("roster")
    return fn()[0] if fn is not None else None


def logged(state, text, since=0):
    return [m for m in state["logs"][since:] if text in m]


def close_all(socks):
    for s in socks:
        try:
            s.close()
        except OSError:
            pass


def shared_items(q):
    with q.mutex:
        return list(q.queue)


# ---------------------------------------------------------------------------
# 1. the QL-only silence limit, against every other brand
# ---------------------------------------------------------------------------
def _limit_round(label, twins, ql_limit=2.0, peer_limit=8.0):
    """One server: a QL seat (sid 1, active) plus `twins` [(name, ident)]
    benched behind it. Everyone goes silent together; record when each
    seat leaves the roster."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    watching = threading.Event()        # set = the roster watcher stops
    with Limits(QLNR_PEER_SILENCE_LIMIT=ql_limit, PEER_SILENCE_LIMIT=peer_limit,
                QLNR_DROP_SILENCE=60.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            check(f"{label}: the QL holds the baton (first seated)",
                  wait_until(lambda: active(control) == 1), active(control))
            cmd_q.put(("version",))         # what the widget asks the driven seat
            answer_y(q, QL, f"{label} QL")
            # Each seat's own last word, and a watcher recording when each
            # leaves the roster - started NOW, so a slow setup below cannot
            # hide when the QL went.
            last = {1: time.monotonic()}
            gone = {}

            def _watch():
                while not watching.is_set():
                    now_seated = set(seated(control))
                    for s_ in list(last):
                        if s_ not in now_seated and s_ not in gone:
                            gone[s_] = time.monotonic()
                    time.sleep(0.05)
            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()
            check(f"{label}: the QL's ident reached the widget",
                  wait_until(lambda: ("qlnextremote", "1.1.4") in state["idents"]),
                  state["idents"])
            peers = []
            for i, (name, ident) in enumerate(twins):
                s = connect_next()
                socks.append(s)
                sid = i + 2
                check(f"{label}: {name} seated as #{sid}",
                      wait_until(lambda sid=sid: sid in seated(control)),
                      seated(control))
                if ident == "never":
                    check(f"{label}: {name} only ever idles", poll(s) == b"I")
                else:
                    check(f"{label}: {name} is asked through its own queue",
                          control["enqueue_to"](sid, ("version",)))
                    answer_y(s, None if ident == "old" else ident,
                             f"{label} {name}")
                last[sid] = time.monotonic()
                peers.append((name, sid, s))
            # From here on, silence everywhere.
            wait_until(lambda: len(gone) == len(last), timeout=peer_limit + 8.0)
            watching.set()
            watcher.join(timeout=2)
            ql_at = gone[1] - last[1] if 1 in gone else None
            check(f"{label}: the silent QL is reaped at the QL limit",
                  ql_at is not None and ql_limit - 0.3 <= ql_at < ql_limit + 3.0,
                  f"{ql_at!r}s after its last word (limit {ql_limit}s)")
            check(f"{label}: with the existing translated line, at the QL limit",
                  logged(state, f"{NO_WORD} {int(ql_limit)}s"),
                  logged(state, NO_WORD))
            check(f"{label}: the QL's link is closed", link_closed(q, 2.0))
            for name, sid, s in peers:
                at = gone[sid] - last[sid] if sid in gone else None
                check(f"{label}: {name} (a local seat) is NOT reaped at the QL "
                      "limit", at is None or at >= peer_limit - 0.3,
                      f"{at!r}s after its last word (QL limit {ql_limit}s, "
                      f"patched PEER_SILENCE_LIMIT {peer_limit}s)")
                check(f"{label}: {name} (a local seat) IS reaped at "
                      "PEER_SILENCE_LIMIT",
                      at is not None and at < peer_limit + 4.0, f"{at!r}s")
            check(f"{label}: the local seats' line still names PEER_SILENCE_LIMIT",
                  logged(state, f"{NO_WORD} {int(peer_limit)}s"),
                  logged(state, NO_WORD))
            check(f"{label}: no error signal along the way", state["errors"] == [],
                  state["errors"])
        finally:
            watching.set()
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def test_ql_limit_reaps_only_the_ql():
    print("\n== the QL-only silence limit ==")
    _limit_round("round 1", [("sync", SYNC), ("n2n", N2N),
                             ("a seat never asked", "never")])
    _limit_round("round 2", [("httpbridge", HTTPB),
                             ("a listener too old for 'Y'", "old")])


# ---------------------------------------------------------------------------
# 2. a long command must not count as silence for a QL
# ---------------------------------------------------------------------------
def test_long_command_then_late_poll():
    print("\n== a QL that just finished a long command ==")
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=60.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, "QL")
            # A slow reply exchange, longer than the QL limit: the reply is
            # read inside _re_reply_call, so last_rx stays at the Poll that
            # carried the command.
            cmd_q.put(("mkdir", "/slow"))
            got = poll(q)
            check("the command goes out", got == b"M/slow", got)
            time.sleep(3.2)
            reply(q, b"O")
            # The QL logs, flushes, and polls again a while later: more than
            # the 1 s idle tick, less than the QL limit.
            time.sleep(1.6)
            got = safe_poll(q)
            check("the late Poll is answered (the seat was NOT reaped)",
                  got == b"I", got)
            check("still seated", seated(control) == [1], seated(control))
            check("no silence verdict was logged", not logged(state, NO_WORD),
                  logged(state, NO_WORD))
            # A multi-block exchange (a get: N, D, E, B with pauses between
            # the blocks) over the QL limit, then the same late Poll.
            tdir = tempfile.mkdtemp(prefix="zxnu-qlseat-")
            try:
                cmd_q.put(("get", "/big.bin", tdir))
                got = poll(q)
                check("the get goes out", got == b"G/big.bin", got)
                reply(q, b"N" + bytes(4) + bytes([7]) + b"big.bin", 0)
                time.sleep(1.1)
                reply(q, b"D" + b"x" * 100, 1)
                time.sleep(1.1)
                reply(q, b"E", 2)
                time.sleep(1.1)
                reply(q, b"B", 3)
                time.sleep(1.6)
                got = safe_poll(q)
                check("after a get longer than the QL limit, the late Poll is "
                      "answered", got == b"I", got)
                check("still seated after the get", seated(control) == [1],
                      seated(control))
            finally:
                shutil.rmtree(tdir, ignore_errors=True)
            # ...and once it really goes quiet, the QL limit still applies.
            t0 = time.monotonic()
            check("a QL that then stops polling is reaped",
                  wait_until(lambda: seated(control) == [], timeout=6.0))
            check("...at about the QL limit", time.monotonic() - t0 < 4.0,
                  f"{time.monotonic() - t0:.1f}s")
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


# ---------------------------------------------------------------------------
# 3. Disconnect's drop hook
# ---------------------------------------------------------------------------
def test_disconnect_drop():
    print("\n== Disconnect drops a silent QL seat, and only that ==")
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=60.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=1.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, "QL")
            s = connect_next()
            socks.append(s)
            check("the sync seat is #2",
                  wait_until(lambda: seated(control) == [1, 2]), seated(control))
            control["enqueue_to"](2, ("version",))
            answer_y(s, SYNC, "sync")
            u = connect_next()
            socks.append(u)
            check("a seat nobody asked is #3",
                  wait_until(lambda: seated(control) == [1, 2, 3]),
                  seated(control))
            check("it only idles", poll(u) == b"I")

            check("the hook is installed beside roster/enqueue_to",
                  callable(control.get("drop_silent")))
            check("the hook refuses a sid that is not seated",
                  control["drop_silent"](99) is False)
            # Disconnect on each: the driven QL's quit rides the SHARED queue,
            # a named seat's its own - exactly the widget's routing - and
            # every target is marked.
            n_log = len(state["logs"])
            cmd_q.put(("quit_app",))
            check("enqueue_to the sync seat", control["enqueue_to"](2, ("quit_app",)))
            check("enqueue_to the unasked seat",
                  control["enqueue_to"](3, ("quit_app",)))
            t0 = time.monotonic()
            check("mark the QL", control["drop_silent"](1) is True)
            check("mark the sync seat", control["drop_silent"](2) is True)
            check("mark the unasked seat", control["drop_silent"](3) is True)
            check("the silent QL seat is dropped",
                  wait_until(lambda: 1 not in seated(control), timeout=5.0),
                  seated(control))
            took = time.monotonic() - t0
            check("...within about QLNR_DROP_SILENCE", took < 3.5, f"{took:.1f}s")
            check("...its link is closed", link_closed(q, 2.0))
            check("...saying why, in the new translated line, naming the QL",
                  any(ADDR in m for m in logged(state, DROPPED, n_log)),
                  state["logs"][n_log:])
            check("...and not as a silence verdict",
                  not logged(state, NO_WORD, n_log), state["logs"][n_log:])
            check("...and the quit meant for it left the shared queue with it",
                  shared_items(cmd_q) == [], shared_items(cmd_q))
            # The other two are marked too, and well past the drop silence.
            time.sleep(1.5)
            check("the marked sync seat is untouched",
                  2 in seated(control), seated(control))
            check("the marked seat nobody asked is untouched",
                  3 in seated(control), seated(control))
            got = safe_poll(s)
            check("the sync seat still gets the plain marked quit at its Poll",
                  got == QUIT_X, got)
            got = safe_poll(u)
            check("so does the unasked seat", got == QUIT_X, got)
            check("only ONE drop line was logged",
                  len(logged(state, DROPPED, n_log)) == 1, state["logs"][n_log:])
            check("no error signal", state["errors"] == [], state["errors"])
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def test_live_marked_ql_still_quits():
    print("\n== a LIVE marked QL is never dropped ==")
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=60.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=1.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, "QL")
            n_log = len(state["logs"])
            check("marked", control["drop_silent"](1) is True)
            # Polling as a live QL does (every 0.2-0.5 s), well past the
            # drop silence: never dropped.
            idles = []
            end = time.monotonic() + 2.6
            while time.monotonic() < end:
                idles.append(safe_poll(q))
                time.sleep(0.3)
            check("a polling QL keeps being answered while marked",
                  idles and all(x == b"I" for x in idles), idles)
            check("still seated", seated(control) == [1], seated(control))
            check("no drop line", not logged(state, DROPPED, n_log))
            cmd_q.put(("quit_app",))
            got = safe_poll(q)
            check("the Disconnect quit arrives as 'Q' + 'X', as before",
                  got == QUIT_X, got)
            q.close()
            check("the seat then leaves as it always did",
                  wait_until(lambda: seated(control) == [], timeout=6.0))
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def test_busy_marked_ql_is_not_dropped():
    """The drop only ever ends a seat BETWEEN commands: not while a put is
    being pulled (served from the idle loop, so the idle timeout runs during
    it) and not while a multi-step command (an rmtree walk, a macro, a
    verify) still has steps queued. Such a seat is left to the QL limit,
    or to finish and collect the quit."""
    print("\n== a marked QL in the middle of a command is not dropped ==")
    # -- a put being pulled ------------------------------------------------
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    fd, tmp = tempfile.mkstemp(suffix=".bin")
    os.write(fd, bytes(range(256)) * 12)              # 3072 B = 6 frames
    os.close(fd)
    with Limits(QLNR_PEER_SILENCE_LIMIT=4.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=1.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, "put QL")
            cmd_q.put(("put", tmp, "/dest.bin"))
            got = poll(q)
            check("put: the put header goes out", got == b"P/dest.bin", got)
            q.sendall(b"Get")
            t_last = time.monotonic()
            check("put: the QL pulled one frame", len(rx_payload(q)) == 512)
            n_log = len(state["logs"])
            time.sleep(0.2)
            # Disconnect as the widget does it: the raw quit, then the mark.
            cmd_q.put(("quit_app",))
            check("put: marked", control["drop_silent"](1) is True)
            time.sleep(2.5)
            check("put: NOT dropped while the put is being pulled",
                  seated(control) == [1] and not logged(state, DROPPED, n_log),
                  (seated(control), state["logs"][n_log:]))
            check("put: the QL limit still ends it",
                  wait_until(lambda: seated(control) == [], timeout=6.0))
            check("put: ...at about the QL limit, with its own line",
                  time.monotonic() - t_last < 4.0 + 3.0
                  and logged(state, f"{NO_WORD} 4s", n_log),
                  state["logs"][n_log:])
            check("put: ...and its quit left the shared queue with it",
                  shared_items(cmd_q) == [], shared_items(cmd_q))
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)
            try:
                os.remove(tmp)
            except OSError:
                pass
    # -- a walk with steps still queued -------------------------------------
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=60.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=1.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, "walk QL")
            cmd_q.put(("rmtree", "/dir"))
            got = poll(q)
            check("walk: the listing step goes out", got == b"L/dir", got)
            reply(q, b"E")                   # empty: the rmdir step is next
            n_log = len(state["logs"])
            time.sleep(0.2)
            cmd_q.put(("quit_app",))
            check("walk: marked", control["drop_silent"](1) is True)
            time.sleep(2.5)
            check("walk: NOT dropped with a step of the walk still queued",
                  seated(control) == [1] and not logged(state, DROPPED, n_log),
                  (seated(control), state["logs"][n_log:]))
            got = safe_poll(q)
            check("walk: the QL resumes and gets the next step", got == b"R/dir",
                  got)
            if got == b"R/dir":
                reply(q, b"O")
            got = safe_poll(q)
            check("walk: once the walk is done the live QL collects the quit "
                  "('Q' + 'X')", got == QUIT_X, got)
            check("walk: ...and was never dropped",
                  not logged(state, DROPPED, n_log), state["logs"][n_log:])
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def _with_ql(label, body, twin=None, **limits):
    """One server with a QL seated as #1 (its own 'Y' answered) and, with
    ``twin`` = (name, ident), a second seat benched as #2 and asked through
    its own queue. Runs body(q, cmd_q, control, state, twin_sock)."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(**limits):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, f"{label} QL")
            t = None
            if twin is not None:
                t = connect_next()
                socks.append(t)
                check(f"{label}: {twin[0]} benched as #2 behind the QL",
                      wait_until(lambda: seated(control) == [1, 2])
                      and active(control) == 1,
                      (active(control), seated(control)))
                control["enqueue_to"](2, ("version",))
                answer_y(t, twin[1], f"{label} {twin[0]}")
            body(q, cmd_q, control, state, t)
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def test_drop_spares_the_put_eof_pull():
    """Review finding: `pending` is cleared when a put's LAST DATA frame
    goes out (the put is reported delivered there), but the QL still pulls
    an empty EOF frame - and when a frame is lost it waits LS_DATA_TICKS
    (10 s, the default QLNR_DROP_SILENCE) before its "Retry". Cut there,
    QLNextRemote deletes the file it was just reported to hold. The drop
    needs the QL's last word to have been a Poll."""
    print("\n== the drop spares a put's EOF pull ==")
    fd, tmp = tempfile.mkstemp(suffix=".bin")
    os.write(fd, b"z" * 300)                        # one 300-byte frame
    os.close(fd)

    def body(q, cmd_q, control, state, _t):
        r = BridgeReply()
        cmd_q.put(("put", tmp, "/dest.bin", r))     # a bridge put: no verify step
        got = poll(q)
        check("eof pull: the put header goes out", got == b"P/dest.bin", got)
        q.sendall(b"Get")
        check("eof pull: the one data frame", len(rx_payload(q)) == 300)
        res = r.wait(3.0)
        check("eof pull: reported delivered at its last data frame",
              isinstance(res, dict) and res.get("ok") is True, res)
        time.sleep(0.2)
        n_log = len(state["logs"])
        cmd_q.put(("quit_app",))                    # Disconnect: the quit...
        check("eof pull: marked", control["drop_silent"](1) is True)
        # The QL lost that frame: it waits longer than QLNR_DROP_SILENCE
        # before asking again (LS_DATA_TICKS on the real thing).
        time.sleep(3.2)
        try:
            q.sendall(b"Retry")
            again = len(rx_payload(q, 3.0))
            q.sendall(b"Get")
            eof = len(rx_payload(q, 3.0))
        except (OSError, AssertionError) as ex:
            again = eof = repr(ex)
        check("eof pull: the frame is sent again after the long wait",
              again == 300, again)
        check("eof pull: and the empty EOF frame follows (file closed, kept)",
              eof == 0, eof)
        check("eof pull: not dropped mid-put", not logged(state, DROPPED, n_log),
              state["logs"][n_log:])
        got = safe_poll(q)
        check("eof pull: back to polling, the QL collects the quit",
              got == QUIT_X, got)
    try:
        _with_ql("eof pull", body, QLNR_PEER_SILENCE_LIMIT=60.0,
                 PEER_SILENCE_LIMIT=600.0, QLNR_DROP_SILENCE=2.0)
    finally:
        os.remove(tmp)


def test_batch_ahead_of_the_quit():
    """Review finding: Disconnect pressed while a LIVE QL works through a
    batch (a paste, a selector-less bridge script) queues the quit BEHIND
    the batch. One late Poll between two steps must not drop the QL: the
    rest of the batch would go to the n2n seat that inherits the baton, and
    the QL would redial instead of exiting. The drop waits until the quit is
    next in line - pressed before step 1 or between two steps alike."""
    print("\n== a batch queued ahead of the quit is never handed on ==")
    for label, press_first in (("press before step 1", True),
                               ("press between steps", False)):
        def body(q, cmd_q, control, state, n, label=label,
                 press_first=press_first):
            for c in [("mkdir", "/q1"), ("mkdir", "/q2"), ("quit_app",)]:
                cmd_q.put(c)
            if press_first:
                time.sleep(0.2)
                control["drop_silent"](1)
            got = poll(q)
            check(f"{label}: step 1 goes to the QL", got == b"M/q1", got)
            reply(q, b"O")
            if not press_first:
                time.sleep(0.2)
                control["drop_silent"](1)
            seen = [safe_poll(n)]
            time.sleep(3.0)                 # one late Poll (> 2 s drop silence)
            got = safe_poll(q)
            check(f"{label}: the late QL is NOT dropped - it gets step 2",
                  got == b"M/q2", (got, state["logs"][-2:]))
            if got == b"M/q2":
                reply(q, b"O")
            got = safe_poll(q)
            check(f"{label}: then the quit, as before", got == QUIT_X, got)
            q.close()
            check(f"{label}: the baton moves to the n2n",
                  wait_until(lambda: active(control) == 2), active(control))
            for _ in range(3):
                seen.append(safe_poll(n))
            check(f"{label}: the n2n received only idles - none of the QL's "
                  "work, no 'Q'", seen and all(x == b"I" for x in seen), seen)
            check(f"{label}: no drop line", not logged(state, DROPPED),
                  logged(state, DROPPED))
        _with_ql(label, body, twin=("n2n", N2N), QLNR_PEER_SILENCE_LIMIT=60.0,
                 PEER_SILENCE_LIMIT=600.0, QLNR_DROP_SILENCE=2.0)


def test_drop_threshold_and_clock():
    """Review finding: with QLNR_DROP_SILENCE patched to the 1 s recv tick,
    "silent for the threshold" could not be told from "any idle timeout",
    nor "since the last command ENDED" from "since it began". With 3 s: a
    marked QL quiet for 2 s collects its quit; one marked just after a 3.5 s
    command that polls 1.6 s after it does too; a silent one is dropped at
    about 3 s - not at the first tick."""
    print("\n== the drop's threshold and its clock ==")
    lim = dict(QLNR_PEER_SILENCE_LIMIT=60.0, PEER_SILENCE_LIMIT=600.0,
               QLNR_DROP_SILENCE=3.0)

    def short_gap(q, cmd_q, control, state, _t):
        time.sleep(0.2)
        cmd_q.put(("quit_app",))
        control["drop_silent"](1)
        time.sleep(2.0)                     # two idle ticks, under 3 s
        got = safe_poll(q)
        check("threshold: a marked QL quiet for 2 s (< 3 s) is not dropped "
              "- it collects the quit", got == QUIT_X,
              (got, state["logs"][-2:]))

    def after_long_command(q, cmd_q, control, state, _t):
        cmd_q.put(("mkdir", "/slow"))
        got = poll(q)
        check("clock: the command goes out", got == b"M/slow", got)
        time.sleep(3.5)                     # its reply takes 3.5 s (> 3 s)
        reply(q, b"O")
        time.sleep(0.3)
        cmd_q.put(("quit_app",))            # Disconnect right after it
        control["drop_silent"](1)
        time.sleep(1.3)                     # polls 1.6 s after the reply
        got = safe_poll(q)
        check("clock: counted from the END of the last command - the QL "
              "polling 1.6 s after a 3.5 s one collects the quit",
              got == QUIT_X, (got, state["logs"][-2:]))

    def silent(q, cmd_q, control, state, _t):
        time.sleep(0.2)
        cmd_q.put(("quit_app",))
        t0 = time.monotonic()
        control["drop_silent"](1)
        wait_until(lambda: seated(control) == [], timeout=8.0)
        took = time.monotonic() - t0
        check("threshold: a silent marked QL IS dropped",
              logged(state, DROPPED), state["logs"][-2:])
        check("threshold: ...at about QLNR_DROP_SILENCE, not at the first "
              "idle tick", 2.5 <= took < 5.5, f"{took:.1f}s")

    _with_ql("short gap", short_gap, **lim)
    _with_ql("long command", after_long_command, **lim)
    _with_ql("silent", silent, **lim)


def test_stale_mark_is_moot():
    """Review finding: the mark was never cleared. A Disconnect whose quit
    was drained again (an operation's Cancel empties the shared queue) left
    the seat marked for good, and a LATER quit - say a selector-less
    /forceexit - plus one long gap of a live QL then dropped it. A QL heard
    after the press is alive: that mark no longer counts."""
    print("\n== a QL heard after the press is not dropped by that mark ==")

    def body(q, cmd_q, control, state, _t):
        time.sleep(0.2)
        control["drop_silent"](1)           # its quit was drained again
        idles = []
        for _ in range(3):
            time.sleep(0.3)
            idles.append(safe_poll(q))
        check("stale mark: the QL keeps polling after the press",
              idles and all(x == b"I" for x in idles), idles)
        r = BridgeReply()
        cmd_q.put(("quit_app", r))          # later: a /forceexit, no selector
        time.sleep(3.0)                     # ...and one long gap (> 2 s)
        got = safe_poll(q)
        check("stale mark: not dropped by it - the QL collects the later quit",
              got == QUIT_X, (got, state["logs"][-2:]))
        res = r.wait(3.0)
        check("stale mark: and the /forceexit caller is answered ok",
              isinstance(res, dict) and res.get("ok") is True, res)
    _with_ql("stale mark", body, QLNR_PEER_SILENCE_LIMIT=60.0,
             PEER_SILENCE_LIMIT=600.0, QLNR_DROP_SILENCE=2.0)


def test_benched_ql_leaves_the_dot_quit_alone():
    """Review finding: the purge's baton gate had no test. A dot drives (#1)
    and QL seats sit benched. Disconnect on the DOT puts its plain quit on
    the shared queue while the dot is busy for a moment; meanwhile benched
    QLs end - by the drop, by the QL limit, by EOF. None may take the dot's
    quit: the dot must still get 'Q' + 'X' at its next Poll."""
    print("\n== a benched QL that ends never takes the driven dot's quit ==")
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=3.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=1.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            d = connect_next()
            socks.append(d)
            cmd_q.put(("version",))
            answer_y(d, SYNC, "the dot")
            cmd_q.put(("quit_app",))        # Disconnect on the driven dot
            for sid, how in ((2, "drop"), (3, "QL limit"), (4, "EOF")):
                q = connect_next()
                socks.append(q)
                check(f"benched {how}: the QL is seated as #{sid}",
                      wait_until(lambda s=sid: s in seated(control)),
                      seated(control))
                control["enqueue_to"](sid, ("version",))
                answer_y(q, QL, f"benched {how} QL")
                n_log = len(state["logs"])
                if how == "drop":
                    control["enqueue_to"](sid, ("quit_app",))
                    time.sleep(0.2)
                    control["drop_silent"](sid)
                elif how == "EOF":
                    q.close()
                check(f"benched {how}: the QL seat ends",
                      wait_until(lambda s=sid: s not in seated(control),
                                 timeout=8.0), seated(control))
                tell = {"drop": [DROPPED], "QL limit": [f"{NO_WORD} 3s"],
                        "EOF": ["closed the connection", "connection error"]}
                check(f"benched {how}: by the path under test",
                      any(logged(state, t, n_log) for t in tell[how]),
                      state["logs"][n_log:])
                check(f"benched {how}: the dot's quit is still on the shared "
                      "queue", shared_items(cmd_q) == [("quit_app",)],
                      shared_items(cmd_q))
                check(f"benched {how}: the dot still drives",
                      active(control) == 1, active(control))
            got = safe_poll(d)
            check("benched: the dot still gets ITS quit ('Q' + 'X')",
                  got == QUIT_X, got)
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


# ---------------------------------------------------------------------------
# 4. a quit meant for a dead QL never reaches another seat
# ---------------------------------------------------------------------------
def _no_quit_for_the_survivor(label, how):
    """``how``: "drop" (Disconnect's drop; the quit is next in line, work
    queued BEHIND it is handed on as it would be after a quit), "limit"
    (the QL limit; a step queued AHEAD of the quit is handed on too, as at
    620 s before) or "eof" (the link closes before the drop could fire - a
    restarted QLNextRemote closes the ESP's stale link)."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=3.0 if how == "limit" else 60.0,
                PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=1.0 if how == "drop" else 60.0):
        th, state = start_server(cmd_q, stop, control)
        try:
            q = connect_next()
            socks.append(q)
            cmd_q.put(("version",))
            answer_y(q, QL, f"{label} QL")
            n = connect_next()
            socks.append(n)
            check(f"{label}: n2n benched as #2 behind the active QL",
                  wait_until(lambda: seated(control) == [1, 2])
                  and active(control) == 1, (active(control), seated(control)))
            control["enqueue_to"](2, ("version",))
            answer_y(n, N2N, f"{label} n2n")
            # The QL is dead from here. (Work queued for the driven seat,)
            # then Disconnect (the raw shared quit) and a selector-less
            # /forceexit, then more work.
            r = BridgeReply()
            ahead = [("mkdir", "/a")] if how == "limit" else []
            for c in ahead + [("quit_app",), ("quit_app", r), ("mkdir", "/b")]:
                cmd_q.put(c)
            # Benched behind the dead QL, the n2n is only ever idled.
            got = safe_poll(n)
            check(f"{label}: the benched n2n idles", got == b"I", got)
            if how in ("drop", "eof"):
                # Disconnect's second half, as the widget sends it.
                check(f"{label}: Disconnect marks the QL",
                      control["drop_silent"](1) is True)
            if how == "eof":
                q.close()
            # (No polling while the QL goes: the moment the baton moves the
            # n2n's Poll would carry the work, and that is checked below.)
            check(f"{label}: the dead QL seat ends",
                  wait_until(lambda: 1 not in seated(control), timeout=6.0),
                  seated(control))
            check(f"{label}: by the path under test",
                  any(logged(state, t) for t in {
                      "drop": [DROPPED], "limit": [f"{NO_WORD} 3s"],
                      # a FIN, or the reset a discarded close gives
                      "eof": ["closed the connection", "connection error"],
                  }[how]) and not (how != "drop" and logged(state, DROPPED)),
                  state["logs"][-3:])
            res = r.wait(5.0)
            check(f"{label}: the /forceexit caller is answered 410 (session gone)",
                  isinstance(res, dict) and res.get("ok") is False
                  and res.get("http") == 410, res)
            work = [c[1].encode() for c in ahead] + [b"/b"]
            check(f"{label}: both quits left the shared queue, the rest kept in order",
                  shared_items(cmd_q) == ahead + [("mkdir", "/b")],
                  shared_items(cmd_q))
            check(f"{label}: the baton moves to the n2n",
                  wait_until(lambda: active(control) == 2), active(control))
            seen = []
            for _ in range(6):
                got = safe_poll(n)
                seen.append(got)
                if got in (b"M/a", b"M/b"):
                    reply(n, b"O")
                if got is None:
                    break
            check(f"{label}: the n2n runs the queued work, in order",
                  [x for x in seen if x and x[:1] == b"M"]
                  == [b"M" + p for p in work], seen)
            check(f"{label}: and receives NO 'Q' at all",
                  all(x is not None and x[:1] != b"Q" for x in seen), seen)
            check(f"{label}: still seated", seated(control) == [2],
                  seated(control))
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def test_dead_ql_quit_never_reaches_another_seat():
    print("\n== a quit meant for a dead QL never reaches another seat ==")
    _no_quit_for_the_survivor("drop", "drop")
    _no_quit_for_the_survivor("QL limit", "limit")
    _no_quit_for_the_survivor("EOF", "eof")


# ---------------------------------------------------------------------------
# 5. Sessions Off: what a newcomer inherits
# ---------------------------------------------------------------------------
def _inherit(label, old_ident, expect_quit, route="own"):
    """``route`` "own": the quit parked on the seat's own queue (a targeted
    enqueue); "shared": the Disconnect BUTTON's real route under Sessions
    Off - the one seat is always the driven one, so the plain quit rides
    the shared queue and the seat is marked - with the newcomer dialing in
    before QLNR_DROP_SILENCE could drop the old seat."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    with Limits(QLNR_PEER_SILENCE_LIMIT=60.0, PEER_SILENCE_LIMIT=600.0,
                QLNR_DROP_SILENCE=60.0):
        th, state = start_server(cmd_q, stop, control, sessions=lambda: False)
        try:
            a = connect_next()
            socks.append(a)
            cmd_q.put(("version",))
            answer_y(a, old_ident, f"{label} old seat")
            # The old seat is dead: a Disconnect on it parks the plain quit,
            # and a command waits behind it.
            if route == "own":
                check(f"{label}: quit parked on the dead seat",
                      control["enqueue_to"](1, ("quit_app",)))
                check(f"{label}: and a command behind it",
                      control["enqueue_to"](1, ("mkdir", "/keep")))
            else:
                cmd_q.put(("quit_app",))
                check(f"{label}: Disconnect marks the driven seat",
                      control["drop_silent"](1) is True)
                cmd_q.put(("mkdir", "/keep"))
            b = connect_next()
            socks.append(b)
            check(f"{label}: the old link is dropped (Sessions Off)",
                  link_closed(a, 5.0))
            answer_y(b, old_ident, f"{label} newcomer")
            got = safe_poll(b)
            if expect_quit:
                check(f"{label}: the newcomer inherits the quit, as before",
                      got == QUIT_X, got)
            else:
                check(f"{label}: the newcomer is NOT told to exit",
                      got == b"M/keep", got)
                reply(b, b"O")
                got = safe_poll(b)
                check(f"{label}: nothing else was parked for it", got == b"I", got)
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)


def test_sessions_off_inheritance():
    print("\n== Sessions Off: inheriting an evicted seat's queue ==")
    _inherit("from a QL seat", QL, expect_quit=False)
    _inherit("from a sync seat", SYNC, expect_quit=True)
    _inherit("button route, from a QL seat", QL, expect_quit=False,
             route="shared")
    _inherit("button route, from a sync seat", SYNC, expect_quit=True,
             route="shared")


# ---------------------------------------------------------------------------
# 6. the pure helpers
# ---------------------------------------------------------------------------
def test_ql_drop_after_dead_reply():
    """9.7.47 (the refutation round): the QL dies while Unite waits for the
    reply to a command, and the user presses Disconnect during that wait.
    The wait's timeout ends Unite's turn AFTER the press, which used to make
    the press look moot (the QL "heard since"); only a byte or a reply that
    COMPLETED counts now, so the QL is dropped after the timeout."""
    print("\n== a press during a reply the dead QL never sends ==")

    def body(q, cmd_q, control, state, _t):
        cmd_q.put(("ls", "/x"))
        got = poll(q)
        check("dead reply: the ls goes out (and is never answered)",
              got == b"L/x", got)
        time.sleep(0.3)
        n_log = len(state["logs"])
        cmd_q.put(("quit_app",))
        t0 = time.monotonic()
        control["drop_silent"](1)
        check("dead reply: the QL is dropped after its reply timed out",
              wait_until(lambda: seated(control) == [], timeout=8.0),
              seated(control))
        took = time.monotonic() - t0
        check("dead reply: ...about QLNR_DROP_SILENCE after the 2 s wait",
              2.0 <= took < 5.5, f"{took:.1f}s")
        check("dead reply: by the QL drop line, not a silence verdict",
              logged(state, DROPPED, n_log) and not logged(state, NO_WORD, n_log),
              state["logs"][n_log:])
        check("dead reply: the ls reported its loss once",
              state["errors"] == ["ls /x: connection dropped"], state["errors"])
    _with_ql("dead reply", body, QLNR_PEER_SILENCE_LIMIT=60.0,
             PEER_SILENCE_LIMIT=600.0, QLNR_DROP_SILENCE=1.0,
             RE_REPLY_TIMEOUT=2.0)


def test_ql_drop_looks_past_reads():
    """9.7.47: after a reset the natural moves are a folder click and
    "Switch to this Next" on the other machine, both queued for the dead
    baton holder AHEAD of the Disconnect. Such reply-less reads and a baton
    move no longer block the drop; they stay queued and run on the next
    baton holder, exactly as a reap leaves them."""
    print("\n== the QL drop looks past reads and a switch ==")

    def body(q, cmd_q, control, state, n):
        for c in [("ls", "/"), ("select_next", 2), ("quit_app",)]:
            cmd_q.put(c)
        n_log = len(state["logs"])
        t0 = time.monotonic()
        control["drop_silent"](1)
        check("reads: the dead QL is dropped all the same",
              wait_until(lambda: 1 not in seated(control), timeout=6.0),
              seated(control))
        check("reads: ...within about QLNR_DROP_SILENCE",
              time.monotonic() - t0 < 3.5, f"{time.monotonic() - t0:.1f}s")
        check("reads: by the QL drop line", logged(state, DROPPED, n_log))
        check("reads: the quit left, the reads stayed, in order",
              shared_items(cmd_q) == [("ls", "/"), ("select_next", 2)],
              shared_items(cmd_q))
        check("reads: the baton moves to the n2n",
              wait_until(lambda: active(control) == 2), active(control))
        got = safe_poll(n)
        check("reads: the n2n runs the folder click", got == b"L/", got)
        if got == b"L/":
            reply(n, b"E")
        seen = [safe_poll(n) for _ in range(3)]
        check("reads: then idles - the switch kept the baton there, no 'Q'",
              seen and all(x == b"I" for x in seen) and active(control) == 2,
              seen)
    _with_ql("reads", body, twin=("n2n", N2N), QLNR_PEER_SILENCE_LIMIT=60.0,
             PEER_SILENCE_LIMIT=600.0, QLNR_DROP_SILENCE=1.0)


def test_helpers():
    print("\n== helpers ==")
    check("every fake peer of this file is a LOCAL seat (127.0.0.1): the "
          "twins pin the emulator exemption",
          zxnu_workers._re_same_host(None, ("127.0.0.1", 0)) is True)
    f = zxnu_workers.re_ident_is_qlnr
    check("QL ident: exact type", f(("qlnextremote", "1.1.4")))
    check("QL ident: stripped and case-folded", f((" QLNextRemote ", "")))
    check("not QL: None (never asked)", not f(None))
    check("not QL: ('', '') (too old for 'Y')", not f(("", "")))
    check("not QL: the other brands",
          not any(f((t, "1")) for t in ("sync", "n2n", "httpbridge", "test",
                                        "qlnextremote2", "ql", "QLNR")))
    q = queue.Queue()
    r = BridgeReply()
    for c in [("mkdir", "/a"), ("quit_app",), ("ls", "/"), ("quit_app", r),
              ("quit",), ("quit_app", "not-a-reply"), ("mkdir", "/b")]:
        q.put(c)
    n, sinks = zxnu_workers._re_purge_quit_apps(q)
    check("purge: two quit_app commands taken", n == 2, n)
    check("purge: the bridge sink handed back", sinks == [r], sinks)
    check("purge: everything else kept, in order",
          shared_items(q) == [("mkdir", "/a"), ("ls", "/"), ("quit",),
                              ("quit_app", "not-a-reply"), ("mkdir", "/b")],
          shared_items(q))
    check("purge: get() still works after", q.get_nowait() == ("mkdir", "/a"))
    check("purge: nothing to take is a no-op",
          zxnu_workers._re_purge_quit_apps(queue.Queue()) == (0, []))


if __name__ == "__main__":
    test_helpers()
    test_ql_limit_reaps_only_the_ql()
    test_long_command_then_late_poll()
    test_disconnect_drop()
    test_live_marked_ql_still_quits()
    test_busy_marked_ql_is_not_dropped()
    test_drop_spares_the_put_eof_pull()
    test_batch_ahead_of_the_quit()
    test_drop_threshold_and_clock()
    test_stale_mark_is_moot()
    test_benched_ql_leaves_the_dot_quit_alone()
    test_dead_ql_quit_never_reaches_another_seat()
    test_sessions_off_inheritance()
    test_ql_drop_after_dead_reply()
    test_ql_drop_looks_past_reads()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
