"""A ZX Spectrum Next seat that went silent (9.7.47), against a real listen
socket and fake peers on OS-chosen ports.

The field case: a Next running ZX Next Remote's NextSync Listener (ident
"httpbridge" / "n2n") or the .sync5 dot in -L (ident "sync") is hard-reset.
Its ESP keeps the TCP link up, so no FIN arrives and the dead seat sat for
PEER_SILENCE_LIMIT (620 s); Disconnect could not clear it, and its quit
stayed on the shared queue for the next baton holder. 9.7.47 extends the
three 9.7.45 QLNextRemote pieces to such a seat when it dials from ANOTHER
machine (seat['local'] is False) and Settings → "Drop a silent Next after 2
minutes" is on.

Every fake peer here dials 127.0.0.1, which the worker rightly takes for an
emulator on this PC (_re_same_host). REMOTE patches that helper so the
fakes count as real Nexts; a peer connected with local=True stays local.
What this locks down, with the limits shortened:

* Each kind - "sync", "n2n", "httpbridge", a seat never asked, a listener
  too old for 'Y' (("", "")) and a seat whose 'Y' query FAILED (the same
  ("", "")) - is reaped at NEXT_PEER_SILENCE_LIMIT with the existing "no
  word" line, naming the real silence; a "test" twin keeps
  PEER_SILENCE_LIMIT and a QL keeps its own limit.
* A LOCAL seat (no patch: loopback) keeps today's path: no Next limit, no
  drop, the quit collected at its Poll.
* The clock: a long command, a get, a crc, then a late Poll never reap;
  put frame gaps under the limit never reap; a put is never dropped
  mid-pull (its lost frame's Retry and its EOF pull included).
* Disconnect's drop for every Next kind - not for "test"; never for a live
  seat or after a stale mark; a press landing while a command waits on a
  peer that is already dead is NOT made moot by that wait's timeout; the
  drop looks past reply-less reads and a "Switch to this Next" ahead of the
  quit (they then run on the next baton holder) but never past a write, a
  write chain's link (a paste check's fsize), a bridge read or a queued
  walk step - and never with a write anywhere on the shared queue while
  the dead seat holds the baton, BEHIND the quit included.
* A dead baton holder with a write - or a widget write chain's get, mark
  or fsize - queued is NOT reaped at the Next limit (the rest of the batch
  would go to another machine's card): the 620 s path, until Cancel drains
  the queue.
* The transfer hold: while ANOTHER remote seat moves FILE data (a get's
  'D' blocks, a put's frames) or the bridge hook says an HTTP body moves, a
  silent Next keeps the 620 s path; listings on another seat and a seat
  dialing from this PC never hold; a raising hook holds, logged once; the
  drop ignores the hold.
* The stall guard: a simulated sleep/resume of this PC (the worker's clock
  jumps ahead) reaps nothing - QL, Next and 620 s arms alike.
* TCP keepalive on every seated link (and only those), best effort - the
  probe count first, or nothing short; the seat's classification is logged
  either way.
* The Settings switch: Off is the pre-9.7.47 620 s path with no drop (the
  9.7.47 keepalive, stall guard, end-of-turn clock and exit purge apply
  either way: a dead Next's quit is purged, not handed on), it is read
  live, and a hook that raises means Off, logged once.
* A quit meant for a dead Next never reaches another seat, whether it ends
  by the drop, the Next limit or EOF; a benched Next that ends never takes
  the driven seat's quit.
* Sessions Off: the inheritance is as before; the only seat reaped or
  dropped ends the worker, its quit purged first.
* The update macros (the dot, ZX Next Remote, QLNextRemote) with a 'K'
  slower than the Next limit and a Disconnect pressed mid-update are
  neither reaped nor dropped: the same wire ops and verdicts.
* The pure helpers and the Settings switch's wiring (decoder, cfg key,
  row, hook, catalogs).

Run with: python tests/test_next_stale_seat.py
"""
import ast
import logging
import os
import queue
import shutil
import socket
import string
import sys
import tempfile
import threading
import time
import zlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication, Qt                  # noqa: E402
import zxnu_workers                                              # noqa: E402
import zxnu_config                                               # noqa: E402
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
TEST = b"test\x001.5.4"
QUIT_X = b"Q" + RE_QUIT_EXIT_MARK
NO_WORD = "no word from the Next for"
DROPPED = "Disconnect closes its seat now"
NEXT_DROP = "the Next at"
QL_DROP = "the QL at"


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


class _FileLog(logging.Handler):
    """The worker's FILE log lines (logging.*), kept for the checks that
    name a seat - the console lines do not."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:                                    # noqa: BLE001
            pass


FILELOG = _FileLog()
logging.getLogger().addHandler(FILELOG)
logging.getLogger().setLevel(logging.INFO)


def filelogged(text, since=0):
    return [m for m in FILELOG.lines[since:] if text in m]


def next_port():
    global PORT
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("", 0))
        PORT = probe.getsockname()[1]
    finally:
        probe.close()
    return PORT


class Limits:
    """Patch the worker's timing constants (and the patchable helpers) for
    one scenario and put back exactly what was there - they are all read at
    call time."""
    NAMES = ("PEER_SILENCE_LIMIT", "QLNR_PEER_SILENCE_LIMIT",
             "QLNR_DROP_SILENCE", "RE_REPLY_TIMEOUT",
             "NEXT_PEER_SILENCE_LIMIT", "NEXT_DROP_SILENCE",
             "NEXT_XFER_HOLD", "RE_TICK_STALL_S", "_re_same_host",
             "_re_set_keepalive", "time")

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


# Peers connected with local=True keep the real answer (an emulator on this
# PC); every other fake counts as a Next dialing from another machine.
LOCAL_PORTS = set()


class Remote:
    def __enter__(self):
        self.saved = zxnu_workers._re_same_host
        zxnu_workers._re_same_host = (
            lambda conn, addr: (isinstance(addr, tuple) and len(addr) > 1
                                and addr[1] in LOCAL_PORTS))
        return self

    def __exit__(self, *exc):
        zxnu_workers._re_same_host = self.saved
        LOCAL_PORTS.clear()
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
    try:
        return poll(sock, timeout)
    except (OSError, AssertionError):
        return None


def reply(sock, payload, pkt=0):
    sock.sendall(frame(payload, pkt))
    return rx_payload(sock)


def answer_y(sock, ident, label):
    """The seat's next Poll carries 'Y': answer it with ``ident``; None = a
    listener too old for 'Y' (it polls again); "fail" = say nothing at all,
    so the worker's reply call times out (a FAILED 'Y')."""
    got = poll(sock)
    check(f"{label}: asked its build ('Y')", got == b"Y", got)
    if ident is None:
        sock.sendall(b"Poll")
        check(f"{label}: the stray Poll is answered idle",
              rx_payload(sock) == b"I")
    elif ident == "fail":
        pass
    else:
        reply(sock, b"O" + ident)


def link_closed(sock, timeout=0.3):
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


def start_server(cmd_q, stop, control, **kw):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": 0, "disconnected": 0, "peers": [], "logs": [],
             "errors": [], "idents": [], "upd": [], "puts": []}
    sig.connected.connect(lambda: state.update(
        connected=state["connected"] + 1), Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(
        disconnected=state["disconnected"] + 1), Qt.DirectConnection)
    sig.peers.connect(lambda p: state["peers"].append(p), Qt.DirectConnection)
    sig.log.connect(lambda m: state["logs"].append(m), Qt.DirectConnection)
    sig.error.connect(lambda m: state["errors"].append(m), Qt.DirectConnection)
    sig.ident.connect(lambda t, n: state["idents"].append((t, n)),
                      Qt.DirectConnection)
    sig.dot_update.connect(lambda okf, msg, brand: state["upd"].append(
        (okf, msg, brand)), Qt.DirectConnection)
    sig.put_done.connect(lambda okf, r: state["puts"].append((okf, r)),
                         Qt.DirectConnection)
    kwargs = {"port": PORT, "control": control}
    kwargs.update(kw)
    th = threading.Thread(target=run_remote_listen_server,
                          args=(sig, cmd_q, stop), kwargs=kwargs, daemon=True)
    th.start()
    return th, state


def connect_raw():
    """A plain connection to the listen server, retried while the worker
    thread has not bound its port yet (start_server returns at once, and a
    first connect can beat the bind: Linux CI runners do)."""
    end = time.time() + 5.0
    while time.time() < end:
        try:
            return socket.create_connection((ADDR, PORT), timeout=5)
        except OSError:
            time.sleep(0.05)
    raise AssertionError("listen server never came up")


def connect_next(local=False):
    s = connect_raw()
    if local:
        LOCAL_PORTS.add(s.getsockname()[1])
    s.sendall(b"Listen")
    got = rx_payload(s)
    if got != b"Listening":
        raise AssertionError(f"not seated: {got!r}")
    return s


def seated(control):
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


class LivePeer:
    """A LIVE seat on its own thread: polls every ``period`` seconds like an
    idle Next, answers what a command needs (M/R/X -> 'O', L -> 'E', Y ->
    its ident, G -> an empty 'B'), records every payload that is not the
    idle 'I', and stops at a 'Q' (closing its end) or a dead link."""

    def __init__(self, sock, ident=N2N, period=0.3):
        self.sock = sock
        self.ident = ident
        self.period = period
        self.seen = []
        self.halted = threading.Event()
        self.dead = False
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        while not self.halted.is_set():
            got = safe_poll(self.sock, 5.0)
            if got is None:
                self.dead = True
                return
            if got != b"I":
                self.seen.append(got)
            try:
                op = got[:1]
                if op in (b"M", b"R", b"X"):
                    reply(self.sock, b"O")
                elif op == b"L":
                    reply(self.sock, b"E")
                elif op == b"Y":
                    reply(self.sock, b"O" + self.ident)
                elif op == b"G":
                    reply(self.sock, b"B")
                elif op == b"Q":
                    self.dead = True
                    self.sock.close()
                    return
            except (OSError, AssertionError):
                self.dead = True
                return
            time.sleep(self.period)

    def halt(self):
        self.halted.set()
        self.th.join(timeout=8)


def _server(label, body, sessions=None, drop_silent_next=None,
            verify_crc=None, bridge_xfer_at=None, **limits):
    """One server under REMOTE with the given limits: body(cmd_q, control,
    state, socks) runs inside it; socks it appends are closed after."""
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    kw = {}
    if sessions is not None:
        kw["sessions"] = sessions
    if drop_silent_next is not None:
        kw["drop_silent_next"] = drop_silent_next
    if verify_crc is not None:
        kw["verify_crc"] = verify_crc
    if bridge_xfer_at is not None:
        kw["bridge_xfer_at"] = bridge_xfer_at
    with Limits(**limits), Remote():
        th, state = start_server(cmd_q, stop, control, **kw)
        try:
            body(cmd_q, control, state, socks)
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)
    return state


def _seat(socks, control, ident, label, cmd_q=None, local=False):
    """Seat one fake peer and have it asked its build: through the shared
    queue when ``cmd_q`` is given (the driven seat, as the widget asks),
    else through its own queue. ``ident``: the 'Y' payload, None (too old
    for 'Y'), "fail" (never answers) or "never" (nobody asks)."""
    before = set(seated(control))
    s = connect_next(local=local)
    socks.append(s)
    check(f"{label}: seated", wait_until(
        lambda: len(set(seated(control)) - before) == 1), seated(control))
    sid = (set(seated(control)) - before or {None}).pop()
    if ident == "never":
        check(f"{label}: only ever idles", poll(s) == b"I")
    elif cmd_q is not None:
        cmd_q.put(("version",))
        answer_y(s, ident, label)
    else:
        control["enqueue_to"](sid, ("version",))
        answer_y(s, ident, label)
    if ident is None:
        # A live old listener polls on after answering 'Y' with a raw Poll.
        # (Its last main-loop word is otherwise that Poll's "ll" tail, which
        # the drop gate does not read as a Poll - a seat that dies in the
        # ~0.3 s between the two is left to the Next limit.)
        check(f"{label}: polls on", poll(s) == b"I")
    return s, sid


# ---------------------------------------------------------------------------
# 0. the pure helpers and the Settings switch's wiring
# ---------------------------------------------------------------------------
def test_helpers():
    print("\n== helpers ==")
    f = zxnu_workers.re_ident_is_next
    for ident in (None, ("", ""), ("sync", "5.9.13"), (" N2N ", ""),
                  ("httpbridge", "1.5.4"), ("Sync", "")):
        check(f"Next ident: {ident!r}", f(ident) is True)
    for ident in (("qlnextremote", "1.1.4"), ("test", "1.5.4"),
                  ("syncx", "1"), (5, ""), (), "", ("ql", "")):
        check(f"not a Next ident: {ident!r}", f(ident) is False)
    check("the Next types are the dot's plus zxnu_config's ZXNR flavours",
          zxnu_workers.RE_NEXT_IDENT_TYPES
          == ("sync",) + tuple(zxnu_config.ZXNR_NEX_FLAVORS),
          zxnu_workers.RE_NEXT_IDENT_TYPES)
    check("...each of them answers 'K' at some build (RE_CRC_FLOORS)",
          all(t in zxnu_workers.RE_CRC_FLOORS
              for t in zxnu_workers.RE_NEXT_IDENT_TYPES))
    check("...and never the QL's", zxnu_workers.RE_QLNR_IDENT_TYPE
          not in zxnu_workers.RE_NEXT_IDENT_TYPES)

    class FakeConn:
        def __init__(self, own, broken=False):
            self.own = own
            self.broken = broken

        def getsockname(self):
            if self.broken:
                raise OSError("not connected")
            return (self.own, 2048)

    h = zxnu_workers._re_same_host
    check("same host: a 127.x peer", h(FakeConn("10.0.0.2"), ("127.0.0.5", 1)))
    check("same host: the peer is this PC's own address",
          h(FakeConn("10.0.0.2"), ("10.0.0.2", 51000)))
    check("NOT same host: a Next on the LAN",
          h(FakeConn("10.0.0.2"), ("10.0.0.185", 51000)) is False)
    check("unknown (getsockname fails) = today's path",
          h(FakeConn("10.0.0.2", broken=True), ("10.0.0.185", 1)) is True)
    check("unknown (no conn) = today's path", h(None, ("10.0.0.185", 1)) is True)
    check("unknown (no addr) = today's path", h(None, None) is True)
    check("the 127.0.0.1 fakes of every other suite are local",
          h(None, ("127.0.0.1", 0)) is True)

    r = BridgeReply()
    q = queue.Queue()
    for c in [("ls", "/"), ("select_next", 2), ("drives",), ("version",),
              ("free", "C"), ("quit_app",)]:
        q.put(c)
    check("drop peek: reads and a baton move are looked past",
          zxnu_workers._re_queue_first_unskippable(q) == (True, ("quit_app",)),
          zxnu_workers._re_queue_first_unskippable(q))
    check("drop peek: nothing taken", len(shared_items(q)) == 6)
    q = queue.Queue()
    for c in [("ls", "/"), ("fsize", "/x"), ("free", "C"), ("quit_app",)]:
        q.put(c)
    check("drop peek: a paste check's fsize (a write chain's link) is NOT "
          "looked past",
          zxnu_workers._re_queue_first_unskippable(q) == (True, ("fsize", "/x")),
          zxnu_workers._re_queue_first_unskippable(q))
    q = queue.Queue()
    for c in [("ls", "/", r), ("quit_app",)]:
        q.put(c)
    check("drop peek: a bridge read (it has a sink) is NOT looked past",
          zxnu_workers._re_queue_first_unskippable(q) == (True, ("ls", "/", r)))
    q = queue.Queue()
    for c in [("ls", "/"), ("mkdir", "/a"), ("quit_app",)]:
        q.put(c)
    check("drop peek: a write is NOT looked past",
          zxnu_workers._re_queue_first_unskippable(q) == (True, ("mkdir", "/a")))
    q = queue.Queue()
    q.put(("ls", "/"))
    check("drop peek: only reads = nothing found",
          zxnu_workers._re_queue_first_unskippable(q) == (False, None))
    check("drop peek: not a queue = nothing found (fails safe)",
          zxnu_workers._re_queue_first_unskippable([]) == (False, None))
    check("the 9.7.45 head peek is gone (its one caller moved on)",
          not hasattr(zxnu_workers, "_re_queue_head"))

    w = zxnu_workers._re_queue_has_write
    for items, want in (([], False), ([("ls", "/")], False),
                        ([("crc", "/x")], False),
                        ([("ls", "/"), ("free", "C"), ("drives",),
                          ("version",), ("select_next", 2)], False),
                        ([("get", "/x", "d")], True),
                        ([("mark", "t1")], True),
                        ([("fsize", "/x")], True),
                        ([("get", "/x", "d", BridgeReply())], False),
                        ([("fsize", "/x", BridgeReply())], False),
                        ([("ls", "/"), ("put", "a", "/b")], True),
                        ([("mkdir", "/x", BridgeReply())], True),
                        ([("rcpy", "/a", "/b")], True),
                        ([("rename", "/a", "/b")], True),
                        ([("rmtree", "/a")], True)):
        q = queue.Queue()
        for c in items:
            q.put(c)
        label = repr([c[:3] if not isinstance(c[-1], BridgeReply)
                      else c[:-1] + ("<sink>",) for c in items])
        check(f"write guard: {label} -> {want}", w(q) is want)
    check("write guard: not a queue = False", w(None) is False)
    sets = {"RE_WRITE_OPS": zxnu_workers.RE_WRITE_OPS,
            "RE_WRITE_CHAIN_OPS": zxnu_workers.RE_WRITE_CHAIN_OPS,
            "RE_DROP_SKIP_OPS": zxnu_workers.RE_DROP_SKIP_OPS}
    for (na, a_), (nb, b_) in ((("RE_WRITE_OPS", sets["RE_WRITE_OPS"]),
                                ("RE_DROP_SKIP_OPS", sets["RE_DROP_SKIP_OPS"])),
                               (("RE_WRITE_CHAIN_OPS", sets["RE_WRITE_CHAIN_OPS"]),
                                ("RE_DROP_SKIP_OPS", sets["RE_DROP_SKIP_OPS"])),
                               (("RE_WRITE_OPS", sets["RE_WRITE_OPS"]),
                                ("RE_WRITE_CHAIN_OPS", sets["RE_WRITE_CHAIN_OPS"]))):
        check(f"{na} and {nb} never overlap", not (a_ & b_), a_ & b_)
    check("every write the design names is a write",
          {"put", "mkdir", "rmdir", "rm", "rmtree", "rename", "rcpy"}
          <= zxnu_workers.RE_WRITE_OPS)
    check("the widget's write chains are held: a move's get + mark, a paste "
          "check's fsize", zxnu_workers.RE_WRITE_CHAIN_OPS
          == frozenset(("get", "mark", "fsize")))

    # TCP keepalive on a real accepted socket (best effort per platform).
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((ADDR, 0))
    srv.listen(1)
    c = socket.create_connection(srv.getsockname(), timeout=5)
    a, _ = srv.accept()
    try:
        took = zxnu_workers._re_set_keepalive(a)
        check("keepalive: SO_KEEPALIVE on", "SO_KEEPALIVE" in took
              and a.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0, took)
        if hasattr(socket, "TCP_KEEPCNT"):
            check("keepalive: the probe count goes first", len(took) > 1
                  and took[1] == "TCP_KEEPCNT", took)
        if {"TCP_KEEPIDLE", "TCP_KEEPINTVL"} <= set(took):
            check("keepalive: SIO_KEEPALIVE_VALS only stands in (not needed "
                  "here)", "SIO_KEEPALIVE_VALS" not in took, took)
        elif hasattr(socket, "SIO_KEEPALIVE_VALS"):
            check("keepalive: Windows' SIO_KEEPALIVE_VALS stood in",
                  "SIO_KEEPALIVE_VALS" in took, took)
        for name, want in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 30),
                           ("TCP_KEEPCNT", 20)):
            opt = getattr(socket, name, None)
            if opt is None:
                print(f"  (no {name} on this platform)")
                continue
            try:
                got = a.getsockopt(socket.IPPROTO_TCP, opt)
            except OSError as ex:
                print(f"  ({name} not readable here: {ex})")
                continue
            check(f"keepalive: {name} == {want}", got == want, got)
    finally:
        close_all([a, c, srv])
    dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dead.close()
    try:
        check("keepalive: a closed socket is a no-op, never an exception",
              zxnu_workers._re_set_keepalive(dead) == [])
    except Exception as ex:                                  # noqa: BLE001
        check("keepalive: a closed socket is a no-op, never an exception",
              False, repr(ex))

    class _KAConn:
        """A socket stand-in whose TCP options named in ``refuse`` fail,
        the way an older Windows build answers them."""

        def __init__(self, refuse=()):
            self.refuse = {getattr(socket, n, object()) for n in refuse}
            self.tcp = {}
            self.ioctls = []

        def setsockopt(self, level, opt, value):
            if level == socket.IPPROTO_TCP:
                if opt in self.refuse:
                    raise OSError("not on this build")
                self.tcp[opt] = value

        def ioctl(self, code, vals):
            self.ioctls.append(vals)

    if hasattr(socket, "TCP_KEEPCNT"):
        k = _KAConn(refuse=("TCP_KEEPCNT",))
        took = zxnu_workers._re_set_keepalive(k)
        check("keepalive: no probe count = nothing short armed (a Windows "
              "before 10 1703 would cut at 330 s)",
              took == ["SO_KEEPALIVE"] and k.tcp == {} and k.ioctls == [],
              (took, k.tcp, k.ioctls))
        if hasattr(socket, "TCP_KEEPIDLE") and hasattr(socket, "TCP_KEEPINTVL"):
            k = _KAConn(refuse=("TCP_KEEPIDLE",))
            took = zxnu_workers._re_set_keepalive(k)
            want_sio = hasattr(socket, "SIO_KEEPALIVE_VALS")
            check("keepalive: the count took, the idle did not - "
                  + ("SIO_KEEPALIVE_VALS stands in" if want_sio
                     else "the interval still set"),
                  "TCP_KEEPCNT" in took and "TCP_KEEPINTVL" in took
                  and (not want_sio or k.ioctls == [(1, 30000, 30000)]),
                  (took, k.ioctls))


def test_settings_switch_wiring():
    print("\n== the Settings switch: decoder, cfg key, row, hook, catalogs ==")
    dec = zxnu_config.nextsync_drop_silent_next_enabled
    key = zxnu_config.SETTING_NEXTSYNC_DROP_SILENT_NEXT
    check("the key is the documented name", key == "nextsync_drop_silent_next")
    check("an absent key reads as ON", dec({}) is True)
    check("an empty value (pre-seed / upgraded cfg) reads as ON", dec({key: ""}) is True)
    check("no cfg at all reads as ON", dec(None) is True)
    check("'true' reads as ON", dec({key: " TRUE "}) is True)
    for off in ("false", "0", "no", " NO ", "False"):
        check(f"{off!r} reads as OFF", dec({key: off}) is False)
    cfs = zxnu_config.CONFIG_FILE_SETTINGS
    check("registered in CONFIG_FILE_SETTINGS (persisted + pre-seeded)", key in cfs)
    check("...right after the Sessions key",
          key in cfs and cfs.index(zxnu_config.SETTING_NEXTSYNC_SESSIONS) + 1
          == cfs.index(key))

    def src(name):
        return open(os.path.join(REPO, name), encoding="utf-8").read()

    pane_src = src("zxnu_settings_pane.py")
    pane_ast = ast.parse(pane_src)
    rows = None
    for node in pane_ast.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SETTINGS_TAB_ROWS"):
            rows = ast.literal_eval(node.value)
    rows = rows or ()
    check("the row is registered right under Sessions (next to the NextSync rows)",
          "nextsync_drop_silent_next" in rows and "nextsync_sessions" in rows
          and rows.index("nextsync_sessions") + 1
          == rows.index("nextsync_drop_silent_next"))
    check("the checkbox is placed through the registrar",
          'settings_grid_row("nextsync_drop_silent_next")' in pane_src)
    check("the handler persists true/false and saves",
          "configuration_dictionary[SETTING_NEXTSYNC_DROP_SILENT_NEXT] = (" in pane_src)
    check("the checkbox defaults on",
          "host.settings_nextsync_drop_silent_next_checkbox.setChecked(True)" in pane_src)
    cio = src("zxnu_config_io.py")
    check("the restore stanza reaches the checkbox through the decoder",
          "settings_nextsync_drop_silent_next_checkbox" in cio
          and "nextsync_drop_silent_next_enabled(" in cio
          and "host.settings_nextsync_drop_silent_next_checkbox.blockSignals(True)" in cio)
    npane = src("zxnu_nextsync_pane.py")
    check("the worker is handed the switch as a 0-arg hook",
          '"drop_silent_next": lambda: nextsync_drop_silent_next_enabled(configuration_dictionary)'
          in npane)
    check("...and the bridge's bodies in motion as the bridge_xfer_at hook",
          '"bridge_xfer_at": _re_bridge_body_at}' in npane
          and "return b.body_moving_at()" in npane)
    wk = src("zxnu_workers.py")
    head = wk[wk.find("def run_remote_listen_server("):
              wk.find('"""', wk.find("def run_remote_listen_server("))]
    check("run_remote_listen_server takes drop_silent_next=None (callers unchanged)",
          "drop_silent_next=None" in head)
    check("...and bridge_xfer_at=None", "bridge_xfer_at=None" in head)
    sess = wk[wk.find("def _re_session("):wk.find("def run_remote_listen_server(")]
    check("the Next gate reads the seat's local flag, missing = local",
          "seat.get('local', True)" in sess)
    check("the exit purge is gated on the seat alone (no switch)",
          "if _ql_self() or _next_seat():" in sess)
    check("the Next drop carries the write guard, the QL's does not",
          "write_guard=True" in sess and sess.count("write_guard=True") == 1)
    check("only the three file pulls (get, the two update read-backs) stamp "
          "the transfer clock",
          sess.count("_re_pull_call(conn, _h)") == 3
          # the def, the pull's wrapper and the two put-frame arms
          and sess.count("_xfer_stamp()") == 4,
          (sess.count("_re_pull_call(conn, _h)"), sess.count("_xfer_stamp()")))
    check("the drop gate refuses open update / verify / rmtree jobs (QL and Next)",
          "and not upd_jobs and not vjobs and not rmtree_jobs" in sess
          and sess.count("if _drop_due(quiet, ") == 2)
    check("both drop lines and the reap line are ui_tr_now literals",
          '"Remote explorer: the Next at {address} has "' in sess
          and '"Remote explorer: the QL at {address} has "' in sess)
    check("the seat is classified once, in the accept loop",
          "local = _re_same_host(conn, addr)" in wk
          and "'evicted': False, 'local': local}" in wk)

    from zxnu_i18n import CATALOGS
    label = tooltip = None
    for node in ast.walk(pane_ast):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Attribute)
                and fn.value.attr == "settings_nextsync_drop_silent_next_checkbox"
                and fn.attr == "setToolTip" and node.args
                and isinstance(node.args[0], ast.Constant)):
            tooltip = node.args[0].value
        if (isinstance(fn, ast.Name) and fn.id == "QCheckBox" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "Drop a silent Next after 2 minutes"):
            label = node.args[0].value
    check("the label is Julien's words", label == "Drop a silent Next after 2 minutes",
          repr(label))
    check("the tooltip says what Off is for and that QL seats keep their rule",
          isinstance(tooltip, str) and "10-minute limit" in tooltip
          and "CSpect (UART Replacement plugin)" in tooltip
          and "paused in a" in tooltip and "QLNextRemote seats keep their own rule" in tooltip,
          repr(tooltip)[:80])
    drop_line = ("Remote explorer: the Next at {address} has been silent for "
                 "{seconds}s — Disconnect closes its seat now instead of waiting "
                 "for a quit it cannot collect.")

    def fields(s):
        return {fl for _l, fl, _s, _c in string.Formatter().parse(s) if fl}

    for code in ("es", "pt", "pl", "ru", "cs", "fr"):
        cat = CATALOGS[code]
        for what, k in (("label", label), ("tooltip", tooltip),
                        ("drop line", drop_line)):
            val = cat.get(k) if k else None
            check(f"{code}: {what} translated", bool(val) and val != k)
            if val:
                check(f"{code}: {what} keeps its placeholders",
                      fields(val) == fields(k), f"{fields(val)} != {fields(k)}")
        if tooltip and cat.get(tooltip):
            check(f"{code}: tooltip keeps the line structure",
                  cat[tooltip].count("\n") == tooltip.count("\n"),
                  cat[tooltip].count("\n"))
            for verbatim in ("'.sync5 -L'", "CSpect", "UART Replacement",
                             "ZX Next Remote", "QLNextRemote", "MAME",
                             "ZX-Next-Unite", "ZX Spectrum Next"):
                check(f"{code}: tooltip keeps {verbatim} verbatim",
                      verbatim in cat[tooltip])


# ---------------------------------------------------------------------------
# 1. the Next limit, kind by kind
# ---------------------------------------------------------------------------
def _limit_round(label, kinds, next_limit=2.0, peer_limit=10.0, ql_limit=4.0,
                 reply_timeout=1.0):
    """One server under REMOTE: ``kinds`` [(name, ident, expect)] seated in
    order (the first one driven); ident as in _seat; expect "next" / "peer"
    / "ql". Everyone goes silent together; record when each seat leaves."""
    def body(cmd_q, control, state, socks):
        last, gone, meta = {}, {}, {}
        watching = threading.Event()

        def _watch():
            while not watching.is_set():
                now_seated = set(seated(control))
                for s_ in list(last):
                    if s_ not in now_seated and s_ not in gone:
                        gone[s_] = time.monotonic()
                time.sleep(0.05)
        watcher = threading.Thread(target=_watch, daemon=True)
        n_file = len(FILELOG.lines)
        for i, (name, ident, expect) in enumerate(kinds):
            s, sid = _seat(socks, control, ident, f"{label} {name}",
                           cmd_q=cmd_q if i == 0 else None)
            # A failed 'Y' ends at the reply timeout: that is when its
            # session goes back to the idle recv and its clock starts.
            last[sid] = time.monotonic() + (reply_timeout if ident == "fail" else 0)
            meta[sid] = (name, expect, s)
            if i == 0:
                watcher.start()
        wait_until(lambda: len(gone) == len(last), timeout=peer_limit + 8.0)
        watching.set()
        watcher.join(timeout=2)
        for sid, (name, expect, s) in meta.items():
            at = gone[sid] - last[sid] if sid in gone else None
            want = {"next": next_limit, "peer": peer_limit, "ql": ql_limit}[expect]
            check(f"{label}: {name} leaves at its {expect} limit ({want}s)",
                  at is not None and want - 0.3 <= at < want + 3.0,
                  f"{at!r}s after its last word")
            check(f"{label}: {name}'s link is closed", link_closed(s, 2.0))
            if expect == "next":
                hits = filelogged(f"(seat #{sid}, ", n_file)
                check(f"{label}: {name}'s file line is the Next one, naming its "
                      "real silence",
                      any("Next peer silent for" in h and int(
                          h.split("silent for ")[1].split("s")[0]) >= int(next_limit)
                          for h in hits), hits)
        check(f"{label}: the console line is the existing translated one",
              logged(state, f"{NO_WORD} {int(next_limit)}s")
              or logged(state, f"{NO_WORD} {int(next_limit) + 1}s"),
              logged(state, NO_WORD))
        if any(e == "peer" for _n, _i, e in kinds):
            check(f"{label}: the 620 s line still names PEER_SILENCE_LIMIT",
                  logged(state, f"{NO_WORD} {int(peer_limit)}s"),
                  logged(state, NO_WORD))
        check(f"{label}: no drop line (nothing was marked)",
              not logged(state, DROPPED))
        if any(i == "fail" for _n, i, _e in kinds):
            check(f"{label}: the failed 'Y' cached (\"\", \"\") - widget told",
                  ("", "") in state["idents"], state["idents"])
    _server(label, body, NEXT_PEER_SILENCE_LIMIT=next_limit,
            PEER_SILENCE_LIMIT=peer_limit, QLNR_PEER_SILENCE_LIMIT=ql_limit,
            QLNR_DROP_SILENCE=60.0, NEXT_DROP_SILENCE=60.0,
            RE_REPLY_TIMEOUT=reply_timeout)


def test_next_limit_reaps_each_kind():
    print("\n== the Next limit, kind by kind (remote seats) ==")
    _limit_round("round 1", [("a QL", QL, "ql"), ("sync", SYNC, "next"),
                             ("n2n", N2N, "next"), ("test", TEST, "peer")])
    _limit_round("round 2", [("httpbridge", HTTPB, "next"),
                             ("a seat never asked", "never", "next"),
                             ("a listener too old for 'Y'", None, "next"),
                             ("a seat whose 'Y' failed", "fail", "next")])


# ---------------------------------------------------------------------------
# 2. a seat from this PC keeps today's path
# ---------------------------------------------------------------------------
def test_local_seat_keeps_todays_path():
    print("\n== a seat dialing from this PC (an emulator) keeps today's path ==")
    next_port()
    cmd_q, stop = queue.Queue(), threading.Event()
    control = {"seq": 0}
    socks = []
    n_file = len(FILELOG.lines)
    with Limits(NEXT_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=8.0,
                QLNR_PEER_SILENCE_LIMIT=3.0, QLNR_DROP_SILENCE=60.0,
                NEXT_DROP_SILENCE=1.0):
        th, state = start_server(cmd_q, stop, control)   # no patch: loopback
        try:
            d, _ = _seat(socks, control, SYNC, "local sync", cmd_q=cmd_q)
            n, _ = _seat(socks, control, N2N, "local n2n")
            q, _ = _seat(socks, control, QL, "local QL")
            h, _ = _seat(socks, control, HTTPB, "local httpbridge")
            t0 = time.monotonic()
            check("each seat is logged as dialed from this PC",
                  len(filelogged("dialed from this PC (an emulator)", n_file)) == 4,
                  filelogged("dialed from this PC", n_file))
            cmd_q.put(("quit_app",))                  # Disconnect on the dot
            control["enqueue_to"](2, ("quit_app",))   # ...and on the n2n
            control["drop_silent"](1)
            control["drop_silent"](2)
            time.sleep(3.5)
            check("the local sync and n2n are NOT reaped at the Next limit "
                  "nor dropped", 1 in seated(control) and 2 in seated(control)
                  and not logged(state, DROPPED), seated(control))
            check("the local QL keeps the 9.7.45 QL limit",
                  wait_until(lambda: 3 not in seated(control), timeout=3.0)
                  and logged(state, f"{NO_WORD} 3s"),
                  (seated(control), logged(state, NO_WORD)))
            got = safe_poll(d)
            check("the marked local dot still gets the quit at its Poll", got == QUIT_X, got)
            got = safe_poll(n)
            check("so does the marked local n2n", got == QUIT_X, got)
            check("the silent local httpbridge is reaped at PEER_SILENCE_LIMIT",
                  wait_until(lambda: 4 not in seated(control), timeout=9.0)
                  and time.monotonic() - t0 >= 8.0 - 0.3,
                  f"{time.monotonic() - t0:.1f}s")
            check("...with the 620 s line, naming the limit",
                  logged(state, f"{NO_WORD} 8s"), logged(state, NO_WORD))
            check("no Next silence line in the file log",
                  not filelogged("Next peer silent", n_file))
        finally:
            stop.set()
            close_all(socks)
            th.join(timeout=10)
    _ = (q, h)


# ---------------------------------------------------------------------------
# 3. the clock: long commands, late polls, put gaps
# ---------------------------------------------------------------------------
def test_next_long_command_then_late_poll():
    print("\n== a Next that just finished a long command ==")
    for name, ident in (("sync", SYNC), ("n2n", N2N)):
        def body(cmd_q, control, state, socks, name=name, ident=ident):
            q, sid = _seat(socks, control, ident, name, cmd_q=cmd_q)
            cmd_q.put(("mkdir", "/slow"))
            got = poll(q)
            check(f"{name}: the mkdir goes out", got == b"M/slow", got)
            time.sleep(3.2)
            reply(q, b"O")
            time.sleep(1.6)
            got = safe_poll(q)
            check(f"{name}: after a 3.2 s reply, the late Poll is answered",
                  got == b"I", got)
            tdir = tempfile.mkdtemp(prefix="zxnu-nextseat-")
            try:
                cmd_q.put(("get", "/big.bin", tdir))
                got = poll(q)
                check(f"{name}: the get goes out", got == b"G/big.bin", got)
                reply(q, b"N" + bytes(4) + bytes([7]) + b"big.bin", 0)
                time.sleep(1.1)
                reply(q, b"D" + b"x" * 100, 1)
                time.sleep(1.1)
                reply(q, b"E", 2)
                time.sleep(1.1)
                reply(q, b"B", 3)
                time.sleep(1.6)
                got = safe_poll(q)
                check(f"{name}: after a get longer than the limit, the late "
                      "Poll is answered", got == b"I", got)
            finally:
                shutil.rmtree(tdir, ignore_errors=True)
            cmd_q.put(("crc", "/big.bin"))
            got = poll(q)
            check(f"{name}: the crc goes out", got == b"K/big.bin", got)
            time.sleep(3.2)
            reply(q, b"O12345678")
            time.sleep(1.6)
            got = safe_poll(q)
            check(f"{name}: after a 3.2 s crc, the late Poll is answered",
                  got == b"I", got)
            check(f"{name}: still seated, no silence verdict",
                  seated(control) == [sid] and not logged(state, NO_WORD),
                  (seated(control), logged(state, NO_WORD)))
            t0 = time.monotonic()
            check(f"{name}: once really silent it is reaped",
                  wait_until(lambda: seated(control) == [], timeout=6.0))
            check(f"{name}: ...at about the Next limit",
                  time.monotonic() - t0 < 4.0, f"{time.monotonic() - t0:.1f}s")
        # PEER 2.5: the old last_rx-only arm WOULD have fired after each
        # of those commands.
        _server(name, body, NEXT_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=2.5,
                NEXT_DROP_SILENCE=60.0, RE_REPLY_TIMEOUT=10.0)


def test_next_put_gaps():
    print("\n== put gaps: never dropped mid-put, never reaped between frames ==")
    fd, tmp = tempfile.mkstemp(suffix=".bin")
    os.write(fd, b"z" * 300)
    os.close(fd)
    fd, big = tempfile.mkstemp(suffix=".bin")
    os.write(fd, bytes(range(256)) * 10)           # 2560 B = 5 frames
    os.close(fd)

    def lost_frame(cmd_q, control, state, socks):
        q, _ = _seat(socks, control, SYNC, "eof pull", cmd_q=cmd_q)
        r = BridgeReply()
        cmd_q.put(("put", tmp, "/dest.bin", r))
        got = poll(q)
        check("eof pull: the put header goes out", got == b"P/dest.bin", got)
        q.sendall(b"Get")
        check("eof pull: the one data frame", len(rx_payload(q)) == 300)
        res = r.wait(3.0)
        check("eof pull: reported delivered", isinstance(res, dict)
              and res.get("ok") is True, res)
        time.sleep(0.2)
        n_log = len(state["logs"])
        cmd_q.put(("quit_app",))
        check("eof pull: marked", control["drop_silent"](1) is True)
        time.sleep(3.2)                             # the lost frame's wait
        try:
            q.sendall(b"Retry")
            again = len(rx_payload(q, 3.0))
            q.sendall(b"Get")
            eof = len(rx_payload(q, 3.0))
        except (OSError, AssertionError) as ex:
            again = eof = repr(ex)
        check("eof pull: the frame is sent again after the long wait", again == 300, again)
        check("eof pull: and the empty EOF frame follows", eof == 0, eof)
        check("eof pull: not dropped mid-put", not logged(state, DROPPED, n_log),
              state["logs"][n_log:])
        got = safe_poll(q)
        check("eof pull: back to polling, the Next collects the quit", got == QUIT_X, got)
    _server("eof pull", lost_frame, NEXT_PEER_SILENCE_LIMIT=60.0,
            NEXT_DROP_SILENCE=2.0)

    def slow_frames(cmd_q, control, state, socks):
        q, sid = _seat(socks, control, SYNC, "slow frames", cmd_q=cmd_q)
        r = BridgeReply()
        cmd_q.put(("put", big, "/big.bin", r))
        got = poll(q)
        check("slow frames: the put header goes out", got == b"P/big.bin", got)
        sizes = []
        t0 = time.monotonic()
        for _ in range(6):                          # 5 data frames + EOF
            time.sleep(1.4)                         # under the 2 s limit
            q.sendall(b"Get")
            sizes.append(len(rx_payload(q, 3.0)))
        check("slow frames: every frame served", sizes == [512] * 5 + [0], sizes)
        check("slow frames: the whole put outlasted the Next limit",
              time.monotonic() - t0 > 6.0)
        res = r.wait(3.0)
        check("slow frames: delivered", isinstance(res, dict) and res.get("ok"), res)
        got = safe_poll(q)
        check("slow frames: never reaped mid-put", got == b"I"
              and seated(control) == [sid] and not logged(state, NO_WORD), got)
    try:
        _server("slow frames", slow_frames, NEXT_PEER_SILENCE_LIMIT=2.0,
                NEXT_DROP_SILENCE=60.0)
    finally:
        os.remove(tmp)
        os.remove(big)


# ---------------------------------------------------------------------------
# 4. Disconnect's drop
# ---------------------------------------------------------------------------
def _drop_round(label, kinds, reply_timeout=60.0):
    """kinds [(name, ident, expect)]: the first is driven (its quit rides the
    shared queue); every seat gets Disconnect (quit + mark); expect "drop"
    or "keep" (it then collects 'Q'+'X' at its Poll)."""
    def body(cmd_q, control, state, socks):
        seats = []
        for i, (name, ident, expect) in enumerate(kinds):
            s, sid = _seat(socks, control, ident, f"{label} {name}",
                           cmd_q=cmd_q if i == 0 else None)
            seats.append((name, expect, s, sid))
        n_log = len(state["logs"])
        time.sleep(0.2)
        for i, (name, expect, s, sid) in enumerate(seats):
            if i == 0:
                cmd_q.put(("quit_app",))
            else:
                control["enqueue_to"](sid, ("quit_app",))
            check(f"{label}: mark {name}", control["drop_silent"](sid) is True)
        t0 = time.monotonic()
        drops = [sid for _n, e, _s, sid in seats if e == "drop"]
        check(f"{label}: every Next seat is dropped",
              wait_until(lambda: not (set(drops) & set(seated(control))),
                         timeout=6.0), seated(control))
        took = time.monotonic() - t0
        check(f"{label}: ...within about NEXT_DROP_SILENCE", took < 3.5, f"{took:.1f}s")
        lines = logged(state, NEXT_DROP, n_log)
        check(f"{label}: one new translated line per dropped seat, naming it",
              len(lines) == len(drops) and all(ADDR in m and DROPPED in m
                                               for m in lines), lines)
        check(f"{label}: never the QL's line, never a silence verdict",
              not logged(state, QL_DROP, n_log) and not logged(state, NO_WORD, n_log),
              state["logs"][n_log:])
        check(f"{label}: the driven seat's quit left the shared queue with it",
              shared_items(cmd_q) == [], shared_items(cmd_q))
        time.sleep(1.0)
        for name, expect, s, sid in seats:
            if expect == "drop":
                check(f"{label}: {name}'s link is closed", link_closed(s, 2.0))
            else:
                check(f"{label}: {name} is untouched", sid in seated(control))
                got = safe_poll(s)
                check(f"{label}: {name} still gets the marked quit at its Poll",
                      got == QUIT_X, got)
        check(f"{label}: no error signal", state["errors"] == [], state["errors"])
    _server(label, body, NEXT_PEER_SILENCE_LIMIT=60.0, PEER_SILENCE_LIMIT=600.0,
            NEXT_DROP_SILENCE=1.0, RE_REPLY_TIMEOUT=reply_timeout)


def test_next_disconnect_drop():
    print("\n== Disconnect drops a silent Next seat, of every kind ==")
    _drop_round("drop A", [("sync", SYNC, "drop"), ("n2n", N2N, "drop"),
                           ("a seat never asked", "never", "drop"),
                           ("test", TEST, "keep")])
    _drop_round("drop B", [("a listener too old for 'Y'", None, "drop"),
                           ("httpbridge", HTTPB, "drop"),
                           ("a seat whose 'Y' failed", "fail", "drop")],
                reply_timeout=1.0)

    def live(cmd_q, control, state, socks):
        q, _ = _seat(socks, control, SYNC, "live", cmd_q=cmd_q)
        n_log = len(state["logs"])
        control["drop_silent"](1)
        idles = []
        end = time.monotonic() + 2.6
        while time.monotonic() < end:
            idles.append(safe_poll(q))
            time.sleep(0.3)
        check("live: a polling Next keeps being answered while marked",
              idles and all(x == b"I" for x in idles), idles)
        cmd_q.put(("quit_app",))
        got = safe_poll(q)
        check("live: the quit arrives as 'Q' + 'X', as before", got == QUIT_X, got)
        check("live: never dropped", not logged(state, DROPPED, n_log))
    _server("live", live, NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=1.0)

    def stale(cmd_q, control, state, socks):
        q, _ = _seat(socks, control, N2N, "stale mark", cmd_q=cmd_q)
        time.sleep(0.2)
        control["drop_silent"](1)                # its quit was drained again
        idles = []
        for _ in range(3):
            time.sleep(0.3)
            idles.append(safe_poll(q))
        check("stale mark: the Next keeps polling after the press",
              idles and all(x == b"I" for x in idles), idles)
        r = BridgeReply()
        cmd_q.put(("quit_app", r))               # a later /forceexit
        time.sleep(3.0)                          # one long gap (> 2 s)
        got = safe_poll(q)
        check("stale mark: not dropped by it - the later quit is collected",
              got == QUIT_X, (got, state["logs"][-2:]))
        res = r.wait(3.0)
        check("stale mark: the /forceexit caller is answered ok",
              isinstance(res, dict) and res.get("ok") is True, res)
    _server("stale mark", stale, NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=2.0)

    def dead_reply(cmd_q, control, state, socks):
        # The refutation's flow: the Next dies while Unite waits for a
        # reply; the user presses Disconnect DURING that wait. The wait's
        # timeout ends the turn after the press - that is no sign of life.
        q, _ = _seat(socks, control, SYNC, "dead reply", cmd_q=cmd_q)
        cmd_q.put(("ls", "/x"))
        got = poll(q)
        check("dead reply: the ls goes out (and is never answered)",
              got == b"L/x", got)
        time.sleep(0.3)
        n_log = len(state["logs"])
        cmd_q.put(("quit_app",))
        t0 = time.monotonic()
        control["drop_silent"](1)
        check("dead reply: the seat is dropped after its reply timed out",
              wait_until(lambda: seated(control) == [], timeout=8.0),
              seated(control))
        took = time.monotonic() - t0
        check("dead reply: ...about NEXT_DROP_SILENCE after the 2 s wait",
              2.0 <= took < 5.5, f"{took:.1f}s")
        check("dead reply: by the drop, not a silence verdict",
              logged(state, NEXT_DROP, n_log) and not logged(state, NO_WORD, n_log),
              state["logs"][n_log:])
        check("dead reply: the ls reported its loss once",
              state["errors"] == ["ls /x: connection dropped"], state["errors"])
        check("dead reply: the quit left with the seat", shared_items(cmd_q) == [])
    _server("dead reply", dead_reply, NEXT_PEER_SILENCE_LIMIT=60.0,
            NEXT_DROP_SILENCE=1.0, RE_REPLY_TIMEOUT=2.0)


def test_next_drop_looks_past_reads_never_writes():
    print("\n== the drop looks past reads and a switch, never past a write ==")

    def reads(cmd_q, control, state, socks):
        d, _ = _seat(socks, control, SYNC, "reads dot", cmd_q=cmd_q)
        n, _ = _seat(socks, control, N2N, "reads n2n")
        live = LivePeer(n)
        try:
            # After the reset: the widget's connect-time version, a folder
            # click, "Switch to this Next" on the relaunched machine, THEN
            # Disconnect on the dead driven seat.
            for c in [("version",), ("ls", "/"), ("select_next", 2),
                      ("quit_app",)]:
                cmd_q.put(c)
            n_log = len(state["logs"])
            t0 = time.monotonic()
            control["drop_silent"](1)
            check("reads: the dead dot is dropped all the same",
                  wait_until(lambda: 1 not in seated(control), timeout=6.0),
                  seated(control))
            check("reads: ...within about NEXT_DROP_SILENCE",
                  time.monotonic() - t0 < 3.5, f"{time.monotonic() - t0:.1f}s")
            check("reads: by the Next drop line", logged(state, NEXT_DROP, n_log))
            check("reads: the switch takes the baton to the n2n",
                  wait_until(lambda: active(control) == 2), active(control))
            check("reads: the queued reads run there, in order, then idles",
                  wait_until(lambda: len(live.seen) >= 2 and not shared_items(cmd_q),
                             timeout=5.0)
                  and live.seen[:2] == [b"Y", b"L/"], live.seen)
            time.sleep(0.8)
            check("reads: the n2n never receives a 'Q'",
                  not any(x[:1] == b"Q" for x in live.seen) and not live.dead,
                  live.seen)
        finally:
            live.halt()
    _server("reads", reads, NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=1.0)

    for label, ahead, want, answer in (
            ("a write ahead", [("mkdir", "/w")], b"M/w", b"O"),
            ("a bridge read ahead", [("ls", "/", BridgeReply())], b"L/", b"E"),
            # A Next->Next paste check: its verdict would start the rcpy on
            # whichever Next answers it (RE_WRITE_CHAIN_OPS).
            ("a paste check's fsize ahead", [("fsize", "/x"), ("free", "C")],
             b"S/x", b"F")):
        def refused(cmd_q, control, state, socks, label=label, ahead=ahead,
                    want=want, answer=answer):
            q, _ = _seat(socks, control, SYNC, label, cmd_q=cmd_q)
            for c in ahead + [("quit_app",)]:
                cmd_q.put(c)
            n_log = len(state["logs"])
            control["drop_silent"](1)
            time.sleep(3.0)
            check(f"{label}: NOT dropped", seated(control) == [1]
                  and not logged(state, DROPPED, n_log), state["logs"][n_log:])
            got = safe_poll(q)
            check(f"{label}: the seat comes back and gets that command first",
                  got == want, got)
            reply(q, answer)
            if ahead[-1][0] == "free":
                got = safe_poll(q)
                check(f"{label}: then the free-space read", got == b"ZC", got)
                reply(q, b"F")
            got = safe_poll(q)
            check(f"{label}: then the quit", got == QUIT_X, got)
        _server(label, refused, NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=1.0)

    # A write BEHIND the quit, or a /forceexit for this sid on the seat's
    # own queue with a write on the shared one: dropping the dead holder
    # would hand that write to the next baton holder at once.
    for label, route in (("a write behind the quit", "shared"),
                         ("a targeted /forceexit, a write queued", "own")):
        def behind(cmd_q, control, state, socks, label=label, route=route):
            d, _ = _seat(socks, control, SYNC, f"{label} dot", cmd_q=cmd_q)
            n, _ = _seat(socks, control, N2N, f"{label} n2n")
            live = LivePeer(n)
            r = BridgeReply()
            try:
                if route == "shared":
                    cmd_q.put(("quit_app",))
                else:
                    control["enqueue_to"](1, ("quit_app", r))
                cmd_q.put(("mkdir", "/late"))
                n_log = len(state["logs"])
                control["drop_silent"](1)
                time.sleep(3.0)
                check(f"{label}: NOT dropped", 1 in seated(control)
                      and not logged(state, DROPPED, n_log), state["logs"][n_log:])
                check(f"{label}: the live n2n never got the write",
                      not any(x[:1] == b"M" for x in live.seen), live.seen)
                with cmd_q.mutex:                # Cancel: the pane's _re_drain
                    cmd_q.queue.clear()
                if route == "shared":
                    # The drain took the quit too: nothing left to drop for.
                    check(f"{label}: drained, the seat is still left to its limit",
                          1 in seated(control))
                else:
                    check(f"{label}: drained, the drop goes ahead",
                          wait_until(lambda: 1 not in seated(control), timeout=4.0)
                          and logged(state, NEXT_DROP, n_log), seated(control))
                    res = r.wait(3.0)
                    check(f"{label}: the /forceexit caller is answered 410",
                          isinstance(res, dict) and res.get("http") == 410, res)
                time.sleep(0.6)
                check(f"{label}: the n2n never receives a 'Q' or an 'M'",
                      not any(x[:1] in (b"Q", b"M") for x in live.seen), live.seen)
            finally:
                live.halt()
        _server(label, behind, NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=1.0)

    def walk(cmd_q, control, state, socks):
        q, _ = _seat(socks, control, N2N, "walk", cmd_q=cmd_q)
        cmd_q.put(("rmtree", "/dir"))
        got = poll(q)
        check("walk: the listing step goes out", got == b"L/dir", got)
        reply(q, b"E")                       # empty: the rmdir step is next
        n_log = len(state["logs"])
        time.sleep(0.2)
        cmd_q.put(("quit_app",))
        control["drop_silent"](1)
        time.sleep(2.5)
        check("walk: NOT dropped with a step of the walk still queued",
              seated(control) == [1] and not logged(state, DROPPED, n_log),
              state["logs"][n_log:])
        got = safe_poll(q)
        check("walk: the Next resumes with the next step", got == b"R/dir", got)
        if got == b"R/dir":
            reply(q, b"O")
        got = safe_poll(q)
        check("walk: then it collects the quit", got == QUIT_X, got)
    _server("walk", walk, NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=1.0)


# ---------------------------------------------------------------------------
# 5. a dead baton holder with a write queued; the transfer hold
# ---------------------------------------------------------------------------
def test_dead_holder_with_a_queued_write():
    print("\n== a dead baton holder with a write queued keeps the 620 s path ==")
    for drain in (False, True):
        label = "drained by Cancel" if drain else "write guard"

        def body(cmd_q, control, state, socks, label=label, drain=drain):
            d, _ = _seat(socks, control, SYNC, f"{label} dot", cmd_q=cmd_q)
            n, _ = _seat(socks, control, N2N, f"{label} n2n")
            live = LivePeer(n)
            try:
                t0 = time.monotonic()
                n_file = len(FILELOG.lines)
                for c in [("mkdir", "/a"), ("mkdir", "/b")]:
                    cmd_q.put(c)
                time.sleep(3.0)
                check(f"{label}: NOT reaped at the Next limit (2 s)",
                      1 in seated(control), seated(control))
                if drain:
                    with cmd_q.mutex:            # the pane's _re_drain
                        cmd_q.queue.clear()
                    t1 = time.monotonic()
                    check(f"{label}: once drained, reaped at the next tick",
                          wait_until(lambda: 1 not in seated(control), timeout=4.0)
                          and time.monotonic() - t1 < 2.5,
                          f"{time.monotonic() - t1:.1f}s")
                    check(f"{label}: by the Next arm, naming the real silence",
                          filelogged("Next peer silent for 3s", n_file)
                          or filelogged("Next peer silent for 4s", n_file),
                          filelogged("silent for", n_file))
                    time.sleep(1.0)
                    check(f"{label}: the n2n got nothing but idles",
                          live.seen == [], live.seen)
                else:
                    check(f"{label}: reaped by the 620 s path at PEER",
                          wait_until(lambda: 1 not in seated(control), timeout=6.0)
                          and time.monotonic() - t0 >= 6.0 - 0.3,
                          f"{time.monotonic() - t0:.1f}s")
                    check(f"{label}: with today's line",
                          logged(state, f"{NO_WORD} 6s")
                          and filelogged("peer silent for 6s", n_file)
                          and not filelogged("Next peer silent", n_file),
                          filelogged("silent for", n_file))
                    check(f"{label}: and then, as at 620 s before, the queue "
                          "goes on with the next baton holder",
                          wait_until(lambda: live.seen[:2] == [b"M/a", b"M/b"],
                                     timeout=5.0), live.seen)
            finally:
                live.halt()
        _server(label, body, NEXT_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=6.0,
                NEXT_DROP_SILENCE=60.0)

    # The widget's write CHAINS (RE_WRITE_CHAIN_OPS): nothing on the queue
    # writes by itself, but the widget writes when they complete - a
    # Move-to-PC's mark makes it rm the source on whoever holds the baton,
    # a paste check's verdict starts the rcpy there. Held the same way.
    tdir = tempfile.mkdtemp(prefix="zxnu-nextchain-")
    try:
        for label, items in (
                ("move to PC", [("mark", "t1"), ("get", "/games/f.tap", tdir),
                                ("mark", "t2")]),
                ("paste check", [("fsize", "/games"), ("free", "C")])):
            def chain(cmd_q, control, state, socks, label=label, items=items):
                d, _ = _seat(socks, control, SYNC, f"{label} dot", cmd_q=cmd_q)
                n, _ = _seat(socks, control, N2N, f"{label} n2n")
                live = LivePeer(n)
                try:
                    n_file = len(FILELOG.lines)
                    for c in items:
                        cmd_q.put(c)
                    time.sleep(3.0)
                    check(f"{label}: NOT reaped at the Next limit (2 s)",
                          1 in seated(control), seated(control))
                    check(f"{label}: nothing popped - the chain waits whole",
                          shared_items(cmd_q) == items, shared_items(cmd_q))
                    check(f"{label}: the live n2n got none of it",
                          live.seen == [], live.seen)
                    with cmd_q.mutex:            # Cancel: the pane's _re_drain
                        cmd_q.queue.clear()
                    t1 = time.monotonic()
                    check(f"{label}: once drained, reaped at the next tick",
                          wait_until(lambda: 1 not in seated(control), timeout=4.0)
                          and time.monotonic() - t1 < 2.5,
                          f"{time.monotonic() - t1:.1f}s")
                    check(f"{label}: by the Next arm",
                          filelogged("Next peer silent for", n_file),
                          filelogged("silent for", n_file))
                    time.sleep(1.0)
                    check(f"{label}: the n2n still got nothing", live.seen == [],
                          live.seen)
                finally:
                    live.halt()
            _server(label, chain, NEXT_PEER_SILENCE_LIMIT=2.0,
                    PEER_SILENCE_LIMIT=30.0, NEXT_DROP_SILENCE=60.0)

        # A BRIDGE get on the shared queue is not a chain: it answers its
        # own caller, who follows the "active machine" contract - so the
        # dead holder is reaped at the Next limit and the get runs on the
        # next baton holder, as any reap has always left it.
        def bridge_get(cmd_q, control, state, socks):
            d, _ = _seat(socks, control, SYNC, "bridge get dot", cmd_q=cmd_q)
            n, _ = _seat(socks, control, N2N, "bridge get n2n")
            live = LivePeer(n)
            try:
                r = BridgeReply()
                cmd_q.put(("get", "/games/f.tap", tdir, r))
                t0 = time.monotonic()
                check("bridge get: the dead holder is reaped at the Next limit",
                      wait_until(lambda: 1 not in seated(control), timeout=5.0)
                      and time.monotonic() - t0 < 4.0,
                      f"{time.monotonic() - t0:.1f}s")
                check("bridge get: the next holder answers its caller",
                      wait_until(lambda: b"G/games/f.tap" in live.seen,
                                 timeout=5.0)
                      and isinstance(r.wait(5.0), dict), live.seen)
            finally:
                live.halt()
        _server("bridge get", bridge_get, NEXT_PEER_SILENCE_LIMIT=2.0,
                PEER_SILENCE_LIMIT=30.0, NEXT_DROP_SILENCE=60.0)
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def test_transfer_hold():
    print("\n== the transfer hold: another seat moving data ==")
    tdir = tempfile.mkdtemp(prefix="zxnu-nexthold-")
    fd, big = tempfile.mkstemp(suffix=".bin")
    os.write(fd, bytes(range(256)) * 20)            # 5120 B = 10 frames
    os.close(fd)

    def _gone_watch(control, sid):
        out = {}

        def run():
            while sid in seated(control):
                time.sleep(0.05)
            out["at"] = time.monotonic()
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return out

    def get_hold(cmd_q, control, state, socks):
        a, _ = _seat(socks, control, N2N, "get mover", cmd_q=cmd_q)
        b, _ = _seat(socks, control, SYNC, "get silent")
        t_quiet = time.monotonic()
        gone = _gone_watch(control, 2)
        n_file = len(FILELOG.lines)
        cmd_q.put(("get", "/big.bin", tdir))
        got = poll(a)
        check("get hold: the get goes out on the mover", got == b"G/big.bin", got)
        reply(a, b"N" + bytes(4) + bytes([7]) + b"big.bin", 0)
        for i in range(8):                          # 'D' blocks for ~4 s
            time.sleep(0.5)
            reply(a, b"D" + b"x" * 400, 1 + i)
        t_last = time.monotonic()
        reply(a, b"E", 9)
        reply(a, b"B", 10)
        live = LivePeer(a)                          # the mover stays alive
        try:
            check("get hold: the silent seat outlived the Next limit while "
                  "the other moved data", "at" not in gone and 2 in seated(control),
                  f"{time.monotonic() - t_quiet:.1f}s silent")
            check("get hold: then it is reaped once the hold ends",
                  wait_until(lambda: "at" in gone, timeout=6.0))
            at = gone.get("at", 0) - t_last
            # hold 1.5 s + one 1 s idle tick + the accept loop's 1 s reap
            check("get hold: ...about NEXT_XFER_HOLD after the last block",
                  1.2 <= at < 4.5, f"{at:.1f}s")
            hits = filelogged("Next peer silent for", n_file)
            check("get hold: the line names the real silence, not the limit",
                  any(int(h.split("silent for ")[1].split("s")[0]) >= 4
                      for h in hits), hits)
            check("get hold: the mover is still seated", 1 in seated(control))
        finally:
            live.halt()
    _server("get hold", get_hold, NEXT_PEER_SILENCE_LIMIT=2.0,
            PEER_SILENCE_LIMIT=30.0, NEXT_XFER_HOLD=1.5, NEXT_DROP_SILENCE=60.0,
            RE_REPLY_TIMEOUT=10.0)

    def put_hold(cmd_q, control, state, socks):
        a, _ = _seat(socks, control, N2N, "put mover", cmd_q=cmd_q)
        b, _ = _seat(socks, control, SYNC, "put silent")
        gone = _gone_watch(control, 2)
        r = BridgeReply()
        cmd_q.put(("put", big, "/big.bin", r))
        got = poll(a)
        check("put hold: the put goes out on the mover", got == b"P/big.bin", got)
        for _ in range(11):                         # 10 frames + EOF, ~4.4 s
            time.sleep(0.4)
            a.sendall(b"Get")
            rx_payload(a, 3.0)
        t_last = time.monotonic()
        check("put hold: the silent seat outlived the Next limit during the put",
              "at" not in gone, seated(control))
        check("put hold: reaped once the hold ends",
              wait_until(lambda: "at" in gone, timeout=6.0)
              and gone["at"] - t_last < 4.5,
              f"{gone.get('at', t_last) - t_last:.1f}s after the last frame")
        check("put hold: the put was delivered", (r.wait(1.0) or {}).get("ok") is True)
    _server("put hold", put_hold, NEXT_PEER_SILENCE_LIMIT=2.0,
            PEER_SILENCE_LIMIT=30.0, NEXT_XFER_HOLD=1.5, NEXT_DROP_SILENCE=60.0)

    def drop_under_hold(cmd_q, control, state, socks):
        a, _ = _seat(socks, control, N2N, "hold drop mover", cmd_q=cmd_q)
        b, _ = _seat(socks, control, SYNC, "hold drop silent")
        cmd_q.put(("get", "/big.bin", tdir))
        got = poll(a)
        check("hold drop: the get goes out", got == b"G/big.bin", got)
        reply(a, b"N" + bytes(4) + bytes([7]) + b"big.bin", 0)
        control["enqueue_to"](2, ("quit_app",))
        t0 = time.monotonic()
        control["drop_silent"](2)
        dropped_at = None
        for i in range(8):
            time.sleep(0.5)
            reply(a, b"D" + b"x" * 400, 1 + i)
            if dropped_at is None and 2 not in seated(control):
                dropped_at = time.monotonic() - t0
        reply(a, b"E", 9)
        reply(a, b"B", 10)
        check("hold drop: a marked silent seat is dropped DURING the other's "
              "transfer", dropped_at is not None and dropped_at < 3.5,
              dropped_at)
        check("hold drop: by the drop line", logged(state, NEXT_DROP))
    _server("hold drop", drop_under_hold, NEXT_PEER_SILENCE_LIMIT=60.0,
            NEXT_XFER_HOLD=30.0, NEXT_DROP_SILENCE=1.0, RE_REPLY_TIMEOUT=10.0)

    # What does NOT hold: browsing the other Next (a listing's 'D' blocks
    # are no transfer), and a mover dialing from this PC (loopback, no air).
    # HOLD 30 s here: a stamp would keep the silent seat to PEER (30 s).
    for label, mover_local in (("listings", False), ("local mover", True)):
        def no_hold(cmd_q, control, state, socks, label=label,
                    mover_local=mover_local):
            a, _ = _seat(socks, control, N2N, f"{label} mover", cmd_q=cmd_q,
                         local=mover_local)
            b, _ = _seat(socks, control, SYNC, f"{label} silent")
            t_quiet = time.monotonic()
            gone = _gone_watch(control, 2)
            if label == "listings":
                gots = []
                for i in range(6):              # a folder click every ~0.7 s
                    cmd_q.put(("ls", f"/d{i}"))
                    gots.append(poll(a))
                    # one entry: flags, size (4), name length, name
                    reply(a, b"D" + bytes([1]) + bytes(4) + bytes([5]) + b"GAMES", 0)
                    reply(a, b"E", 1)
                    time.sleep(0.5)
                check(f"{label}: every listing went out, each answered with a "
                      "'D' block", gots == [f"L/d{i}".encode() for i in range(6)],
                      gots)
            else:
                cmd_q.put(("get", "/big.bin", tdir))
                got = poll(a)
                check(f"{label}: the get goes out", got == b"G/big.bin", got)
                reply(a, b"N" + bytes(4) + bytes([7]) + b"big.bin", 0)
                for i in range(8):
                    time.sleep(0.5)
                    reply(a, b"D" + b"x" * 400, 1 + i)
                reply(a, b"E", 9)
                reply(a, b"B", 10)
            live = LivePeer(a)
            try:
                check(f"{label}: the silent seat left at about the Next limit, "
                      "unheld", wait_until(lambda: "at" in gone, timeout=6.0)
                      and gone["at"] - t_quiet < 4.0,
                      f"{gone.get('at', time.monotonic()) - t_quiet:.1f}s")
                check(f"{label}: by the Next arm, not the 620 s one",
                      logged(state, NO_WORD)
                      and not logged(state, f"{NO_WORD} 30s"),
                      logged(state, NO_WORD))
            finally:
                live.halt()
        _server(label, no_hold, NEXT_PEER_SILENCE_LIMIT=2.0,
                PEER_SILENCE_LIMIT=30.0, NEXT_XFER_HOLD=30.0,
                NEXT_DROP_SILENCE=60.0, RE_REPLY_TIMEOUT=10.0)

    # The HTTP bridge's own legs (bridge_xfer_at): a body in motion holds
    # every silent Next; one that ended long ago does not; a hook that
    # raises holds, logged once.
    def raising():
        raise RuntimeError("bridge gone")
    for label, hook, want in (
            ("bridge body moving", lambda: time.monotonic(), "held"),
            ("bridge never moved", lambda: None, "free"),
            ("bridge body ended long ago",
             lambda: time.monotonic() - 100.0, "free"),
            ("bridge hook raises", raising, "held")):
        def bridge(cmd_q, control, state, socks, label=label, want=want):
            n_file = len(FILELOG.lines)
            b, _ = _seat(socks, control, SYNC, f"{label} silent", cmd_q=cmd_q)
            t_quiet = time.monotonic()
            gone = _gone_watch(control, 1)
            check(f"{label}: gone in time",
                  wait_until(lambda: "at" in gone, timeout=9.0),
                  seated(control))
            at = gone.get("at", time.monotonic()) - t_quiet
            if want == "held":
                check(f"{label}: held to the 620 s path (PEER 5 s)",
                      at >= 5.0 - 0.3, f"{at:.1f}s")
                check(f"{label}: with today's line", logged(state, f"{NO_WORD} 5s"))
            else:
                check(f"{label}: reaped at the Next limit", at < 4.0, f"{at:.1f}s")
            if label == "bridge hook raises":
                check(f"{label}: the failure is logged exactly once",
                      len(filelogged("bridge_xfer_at hook failed", n_file)) == 1,
                      filelogged("hook failed", n_file))
        _server(label, bridge, bridge_xfer_at=hook, NEXT_PEER_SILENCE_LIMIT=2.0,
                PEER_SILENCE_LIMIT=5.0, NEXT_XFER_HOLD=30.0,
                NEXT_DROP_SILENCE=60.0)
    shutil.rmtree(tdir, ignore_errors=True)
    os.remove(big)


# ---------------------------------------------------------------------------
# 6. the stall guard, keepalive
# ---------------------------------------------------------------------------
class _ShiftedTime:
    """The worker's ``time`` module with monotonic() shifted by ``offset`` -
    a PC that slept: the clock jumps, nothing was received meanwhile."""

    def __init__(self, real):
        self._real = real
        self.offset = 0.0

    def monotonic(self):
        return self._real.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_stall_guard():
    print("\n== the stall guard: a sleep/resume of this PC reaps nothing ==")
    shifted = _ShiftedTime(time)

    def body(cmd_q, control, state, socks):
        q, _ = _seat(socks, control, QL, "stall QL", cmd_q=cmd_q)
        n, _ = _seat(socks, control, SYNC, "stall remote dot")
        h, _ = _seat(socks, control, N2N, "stall local n2n", local=True)
        n_file = len(FILELOG.lines)
        time.sleep(0.5)
        shifted.offset += 700.0          # a 700 s sleep, past every limit
        t0 = time.monotonic()
        time.sleep(1.6)
        check("stall: nothing reaped at the first ticks after the resume",
              seated(control) == [1, 2, 3] and not logged(state, NO_WORD),
              (seated(control), logged(state, NO_WORD)))
        check("stall: each session noticed it was frozen (file log)",
              len(filelogged("this process was frozen", n_file)) >= 3,
              filelogged("frozen", n_file))
        check("stall: then the QL and the remote dot go at their limits, "
              "counted from the resume",
              wait_until(lambda: seated(control) == [3], timeout=6.0)
              and time.monotonic() - t0 >= 3.0 - 0.3,
              f"{time.monotonic() - t0:.1f}s {seated(control)}")
        check("stall: and the local seat at PEER_SILENCE_LIMIT, from the resume",
              wait_until(lambda: seated(control) == [], timeout=6.0)
              and time.monotonic() - t0 >= 6.0 - 0.3,
              f"{time.monotonic() - t0:.1f}s")
    _server("stall", body, time=shifted, QLNR_PEER_SILENCE_LIMIT=3.0,
            NEXT_PEER_SILENCE_LIMIT=3.0, PEER_SILENCE_LIMIT=6.0,
            QLNR_DROP_SILENCE=60.0, NEXT_DROP_SILENCE=60.0)


def test_keepalive_on_every_seat():
    print("\n== TCP keepalive on every seated link, and only those ==")
    calls = []
    real = zxnu_workers._re_set_keepalive

    def recorder(conn):
        took = real(conn)
        vals = {}
        for name in ("TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT"):
            opt = getattr(socket, name, None)
            if opt is not None:
                try:
                    vals[name] = conn.getsockopt(socket.IPPROTO_TCP, opt)
                except OSError:
                    pass
        calls.append((took, vals))
        return took

    def body(cmd_q, control, state, socks):
        n_file = len(FILELOG.lines)
        probe = connect_raw()
        probe.close()                             # a silent probe
        other = socket.create_connection((ADDR, PORT), timeout=5)
        other.sendall(b"Sync3!")                  # not a -listen client
        socks.append(other)
        _seat(socks, control, SYNC, "keepalive dot", cmd_q=cmd_q)
        _seat(socks, control, N2N, "keepalive n2n")
        _seat(socks, control, N2N, "keepalive local n2n", local=True)
        time.sleep(0.5)
        check("keepalive: armed once per SEATED link (not the probe, not the "
              "refused client), local or not", len(calls) == 3, calls)
        check("each seat's classification is in the file log: two from "
              "another machine, one from this PC",
              len(filelogged("dialed from another machine", n_file)) == 2
              and len(filelogged("dialed from this PC", n_file)) == 1,
              filelogged("dialed from", n_file))
        for took, vals in calls:
            check("keepalive: SO_KEEPALIVE on", "SO_KEEPALIVE" in took, took)
            for name, want in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 30),
                               ("TCP_KEEPCNT", 20)):
                if name in vals:
                    check(f"keepalive: {name} == {want}", vals[name] == want, vals)
    _server("keepalive", body, _re_set_keepalive=recorder)


# ---------------------------------------------------------------------------
# 7. the Settings switch, live
# ---------------------------------------------------------------------------
def test_switch_off_is_the_620s_path():
    print("\n== Settings switch Off: the 620 s path, no drop (the purge stays) ==")

    def off(cmd_q, control, state, socks):
        d, _ = _seat(socks, control, SYNC, "off dot", cmd_q=cmd_q)
        n, _ = _seat(socks, control, N2N, "off n2n")
        live = LivePeer(n)
        try:
            t0 = time.monotonic()
            n_log = len(state["logs"])
            n_file = len(FILELOG.lines)
            cmd_q.put(("quit_app",))
            control["drop_silent"](1)
            time.sleep(3.0)
            check("off: the silent marked dot is neither dropped nor reaped at "
                  "the Next limit", 1 in seated(control)
                  and not logged(state, DROPPED, n_log), seated(control))
            check("off: reaped by the 620 s path", wait_until(
                lambda: 1 not in seated(control), timeout=6.0)
                and time.monotonic() - t0 >= 6.0 - 0.3,
                f"{time.monotonic() - t0:.1f}s")
            check("off: with today's line", logged(state, f"{NO_WORD} 6s", n_log))
            # The purge is no silence rule (9.7.47 review): Off does not
            # bring back the hand-on of a dead Next's quit to another seat.
            check("off: its quit left the shared queue with it (purged)",
                  wait_until(lambda: shared_items(cmd_q) == [], timeout=3.0)
                  and filelogged("quit(s) queued for Next seat #1", n_file),
                  (shared_items(cmd_q), filelogged("quit(s)", n_file)))
            time.sleep(1.0)
            check("off: the next baton holder never receives a 'Q'",
                  not any(x[:1] == b"Q" for x in live.seen) and not live.dead,
                  live.seen)
            check("off: the n2n holds the baton", active(control) == 2)
        finally:
            live.halt()
    _server("off", off, drop_silent_next=lambda: False,
            NEXT_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=6.0,
            NEXT_DROP_SILENCE=1.0)

    flag = {"on": False}

    def flip(cmd_q, control, state, socks):
        q, _ = _seat(socks, control, SYNC, "flip", cmd_q=cmd_q)
        time.sleep(3.0)
        check("flip: Off, the silent dot outlives the Next limit",
              seated(control) == [1])
        flag["on"] = True
        t1 = time.monotonic()
        check("flip: switched On, it is reaped at the next tick",
              wait_until(lambda: seated(control) == [], timeout=3.0)
              and time.monotonic() - t1 < 2.0, f"{time.monotonic() - t1:.1f}s")
        check("flip: by the Next arm", logged(state, NO_WORD)
              and not logged(state, f"{NO_WORD} 30s"), logged(state, NO_WORD))
    _server("flip", flip, drop_silent_next=lambda: flag["on"],
            NEXT_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=30.0)

    def broken():
        raise RuntimeError("cfg gone")

    def raises(cmd_q, control, state, socks):
        n_file = len(FILELOG.lines)
        q, _ = _seat(socks, control, N2N, "raises", cmd_q=cmd_q)
        t0 = time.monotonic()
        check("raises: a hook that raises means Off - the 620 s path",
              wait_until(lambda: seated(control) == [], timeout=9.0)
              and time.monotonic() - t0 >= 5.0 - 0.3,
              f"{time.monotonic() - t0:.1f}s")
        check("raises: with today's line", logged(state, f"{NO_WORD} 5s"))
        check("raises: the failure is logged exactly once",
              len(filelogged("drop_silent_next hook failed", n_file)) == 1,
              filelogged("hook failed", n_file))
    _server("raises", raises, drop_silent_next=broken,
            NEXT_PEER_SILENCE_LIMIT=2.0, PEER_SILENCE_LIMIT=5.0)


# ---------------------------------------------------------------------------
# 8. a quit meant for a dead Next never reaches another seat
# ---------------------------------------------------------------------------
def _no_quit_for_the_survivor(label, how):
    """``how``: "drop" (a read queued BEHIND the quit is handed on, as
    after a quit - a WRITE there refuses the drop, see
    test_next_drop_looks_past_reads_never_writes), "limit" (only reads
    queued, so no write holds it: they are handed on as a reap always did)
    or "eof" (a link that ends hands even a write on, as it always did)."""
    def body(cmd_q, control, state, socks):
        d, _ = _seat(socks, control, SYNC, f"{label} dot", cmd_q=cmd_q)
        n, _ = _seat(socks, control, N2N, f"{label} n2n")
        live = LivePeer(n)
        try:
            r = BridgeReply()
            if how == "limit":
                items = [("ls", "/a"), ("quit_app",), ("quit_app", r), ("ls", "/b")]
                rest = [("ls", "/a"), ("ls", "/b")]
                work = [b"L/a", b"L/b"]
            elif how == "drop":
                items = [("quit_app",), ("quit_app", r), ("ls", "/b")]
                rest = [("ls", "/b")]
                work = [b"L/b"]
            else:
                items = [("quit_app",), ("quit_app", r), ("mkdir", "/b")]
                rest = [("mkdir", "/b")]
                work = [b"M/b"]
            for c in items:
                cmd_q.put(c)
            if how in ("drop", "eof"):
                check(f"{label}: Disconnect marks the dot",
                      control["drop_silent"](1) is True)
            if how == "eof":
                d.close()
            check(f"{label}: the dead dot's seat ends",
                  wait_until(lambda: 1 not in seated(control), timeout=6.0),
                  seated(control))
            check(f"{label}: by the path under test", any(logged(state, t) for t in {
                "drop": [NEXT_DROP], "limit": [f"{NO_WORD} 3s", f"{NO_WORD} 4s"],
                "eof": ["closed the connection", "connection error"]}[how])
                and not (how != "drop" and logged(state, DROPPED)),
                state["logs"][-3:])
            res = r.wait(5.0)
            check(f"{label}: the /forceexit caller is answered 410",
                  isinstance(res, dict) and res.get("ok") is False
                  and res.get("http") == 410, res)
            check(f"{label}: both quits left the shared queue, the rest kept "
                  "in order", shared_items(cmd_q) in (rest, rest[1:], []),
                  shared_items(cmd_q))
            check(f"{label}: the n2n runs the queued work, in order",
                  wait_until(lambda: [x for x in live.seen if x[:1] in b"ML"]
                             == work, timeout=5.0), live.seen)
            time.sleep(0.6)
            check(f"{label}: and receives NO 'Q' at all",
                  not any(x[:1] == b"Q" for x in live.seen) and not live.dead,
                  live.seen)
            check(f"{label}: the n2n holds the baton", active(control) == 2)
        finally:
            live.halt()
    _server(label, body,
            NEXT_PEER_SILENCE_LIMIT=3.0 if how == "limit" else 60.0,
            PEER_SILENCE_LIMIT=600.0,
            NEXT_DROP_SILENCE=1.0 if how == "drop" else 60.0)


def test_dead_next_quit_never_reaches_another_seat():
    print("\n== a quit meant for a dead Next never reaches another seat ==")
    _no_quit_for_the_survivor("drop", "drop")
    _no_quit_for_the_survivor("Next limit", "limit")
    _no_quit_for_the_survivor("EOF", "eof")

    def benched(cmd_q, control, state, socks):
        # The driven dot dials from THIS PC (an emulator: 620 s, no drop),
        # so it may sit silent here while benched remote Nexts end.
        d, _ = _seat(socks, control, SYNC, "benched: the local dot",
                     cmd_q=cmd_q, local=True)
        cmd_q.put(("quit_app",))                 # Disconnect on the driven dot
        for how in ("drop", "Next limit", "EOF"):
            s, sid = _seat(socks, control, N2N, f"benched {how} n2n")
            n_log = len(state["logs"])
            if how == "drop":
                control["enqueue_to"](sid, ("quit_app",))
                time.sleep(0.2)
                control["drop_silent"](sid)
            elif how == "EOF":
                s.close()
            check(f"benched {how}: the n2n seat ends",
                  wait_until(lambda: sid not in seated(control), timeout=8.0),
                  seated(control))
            tell = {"drop": [NEXT_DROP], "Next limit": [NO_WORD],
                    "EOF": ["closed the connection", "connection error"]}
            check(f"benched {how}: by the path under test",
                  any(logged(state, t, n_log) for t in tell[how]),
                  state["logs"][n_log:])
            check(f"benched {how}: the dot's quit is still on the shared queue",
                  shared_items(cmd_q) == [("quit_app",)], shared_items(cmd_q))
            check(f"benched {how}: the dot still drives", active(control) == 1)
        got = safe_poll(d)
        check("benched: the dot still gets ITS quit ('Q' + 'X')", got == QUIT_X, got)
    _server("benched", benched, NEXT_PEER_SILENCE_LIMIT=3.0,
            PEER_SILENCE_LIMIT=600.0, NEXT_DROP_SILENCE=1.0)


# ---------------------------------------------------------------------------
# 9. Sessions Off
# ---------------------------------------------------------------------------
def test_sessions_off_next():
    print("\n== Sessions Off with a remote Next ==")
    for route in ("own", "shared"):
        def inherit(cmd_q, control, state, socks, route=route):
            a, _ = _seat(socks, control, SYNC, f"inherit {route} old", cmd_q=cmd_q)
            if route == "own":
                control["enqueue_to"](1, ("quit_app",))
                control["enqueue_to"](1, ("mkdir", "/keep"))
            else:
                cmd_q.put(("quit_app",))
                control["drop_silent"](1)
                cmd_q.put(("mkdir", "/keep"))
            b = connect_next()
            socks.append(b)
            check(f"inherit {route}: the old link is dropped", link_closed(a, 5.0))
            answer_y(b, SYNC, f"inherit {route} newcomer")
            got = safe_poll(b)
            check(f"inherit {route}: the newcomer inherits the quit, as before",
                  got == QUIT_X, got)
            check(f"inherit {route}: under the SAME sid", seated(control) in ([1], []))
        _server(f"inherit {route}", inherit, sessions=lambda: False,
                NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=60.0)

    def reaped(cmd_q, control, state, socks):
        _seat(socks, control, N2N, "only seat", cmd_q=cmd_q)
        t0 = time.monotonic()
        check("only seat: reaped at the Next limit",
              wait_until(lambda: state["disconnected"] == 1, timeout=6.0)
              and time.monotonic() - t0 < 4.5, f"{time.monotonic() - t0:.1f}s")
        check("only seat: the worker returned (the pane relistens; a re-dial "
              "now gets a NEW sid - the documented 9.7.47 trade-off)",
              logged(state, NO_WORD) and seated(control) == [])
    _server("only seat", reaped, sessions=lambda: False,
            NEXT_PEER_SILENCE_LIMIT=2.0, NEXT_DROP_SILENCE=60.0)

    def dropped(cmd_q, control, state, socks):
        _seat(socks, control, SYNC, "only seat drop", cmd_q=cmd_q)
        # Settle first: the worker acks the 'Y' reply block ("Ok") BEFORE
        # it stamps alive_at, so a press landing in that instant would
        # count as "heard since the press" and the mark would be moot (as
        # it should for a live seat) - the other drop rounds settle too.
        time.sleep(0.2)
        cmd_q.put(("quit_app",))
        control["drop_silent"](1)
        check("only seat drop: dropped, the worker returns",
              wait_until(lambda: state["disconnected"] == 1, timeout=6.0))
        check("only seat drop: its quit was purged first",
              shared_items(cmd_q) == [], shared_items(cmd_q))
        check("only seat drop: by the drop line", logged(state, NEXT_DROP))
    _server("only seat drop", dropped, sessions=lambda: False,
            NEXT_PEER_SILENCE_LIMIT=60.0, NEXT_DROP_SILENCE=1.0)


# ---------------------------------------------------------------------------
# 10. the update macros
# ---------------------------------------------------------------------------
def test_update_macro_not_reaped():
    print("\n== update macros: a slow 'K' and a press mid-update ==")
    import test_remote_listen as trl
    tmp = tempfile.mkdtemp(prefix="zxnu-nextupd-")
    try:
        dot_blob = b"\x00\x01" * 50 + b"NextSync 5.9.0" + bytes(range(256)) * 5
        dot_file = os.path.join(tmp, "sync5.bin")
        open(dot_file, "wb").write(dot_blob)
        zx_blob = (b"Next\x00" + bytes(range(256)) * 5
                   + b"ZXNextRemote\x00" + b"9.9.9\x00tail")
        zx_file = os.path.join(tmp, "zxnextremote-n2n.nex")
        open(zx_file, "wb").write(zx_blob)
        ql_hdr = (b"]!QDOS File Header" + bytes([0, 15]) + bytes([0, 1])
                  + (140 * 1024).to_bytes(4, "big") + bytes(4))
        ql_blob = ql_hdr + (b"\x60\x00" + bytes(range(256)) * 3
                            + b"QLNextRemote\x00" + b"0.5.1\x00tail")
        ql_pkg = os.path.join(tmp, "qlnextremote-0.5.1")
        os.makedirs(os.path.join(ql_pkg, "qemulator"))
        ql_file = os.path.join(ql_pkg, "qemulator", "qlnextremote_exe")
        open(ql_file, "wb").write(ql_blob)
        ql_readme = os.path.join(ql_pkg, "README.md")
        ql_readme_b = b"# QLNextRemote 0.5.1\r\n"
        open(ql_readme, "wb").write(ql_readme_b)
        zb = "c:/apps/zxnextremote-n2n.nex"
        qb = "W:/HOME/qlnextremote_exe"
        qr = "W:/HOME/README.md"
        cases = [
            ("dot", ("update_dot", dot_file, "c:/dot", "5.9.0"), dot_blob,
             b'Osync' + bytes([0]) + b'9.9.9', "NextSync",
             [('P', "c:/dot/sync5.new"), ('Y', ""), ('K', "c:/dot/sync5.new"),
              ('U', ""), ('X', "c:/dot/sync5.bak"),
              ('V', "c:/dot/sync5\x00c:/dot/sync5.bak"),
              ('V', "c:/dot/sync5.new\x00c:/dot/sync5"), ('Q', "")]),
            ("ZXNR", ("update_dot", zx_file, "c:/apps", "9.9.9",
                      "zxnextremote-n2n.nex", "ZXNextRemote", True), zx_blob,
             b'On2n' + bytes([0]) + b'1.0.8', "ZXNextRemote",
             [('P', zb + ".new"), ('Y', ""), ('K', zb + ".new"), ('U', ""),
              ('X', zb + ".bak"), ('V', zb + "\x00" + zb + ".bak"),
              ('V', zb + ".new\x00" + zb), ('Q', "X")]),
            ("QL", ("update_dot", ql_file, "W:/HOME", "0.5.1", "qlnextremote_exe",
                    "QLNextRemote", True, [("put", ql_readme, "README.md")]),
             ql_blob, b'Oqlnextremote' + bytes([0]) + b'0.5.0', "QLNextRemote",
             [('P', qr), ('Y', ""), ('K', qr), ('P', qb + ".new"),
              ('K', qb + ".new"), ('U', ""), ('X', qb + ".bak"),
              ('V', qb + "\x00" + qb + ".bak"), ('V', qb + ".new\x00" + qb),
              ('Q', "X")]),
        ]
        for name, cmd, blob, ident, brand, want_ops in cases:
            for press in (False, True):
                label = f"{name} update" + (" + Disconnect mid-update" if press else "")

                def body(cmd_q, control, state, socks, cmd=cmd, blob=blob,
                         ident=ident, brand=brand, want_ops=want_ops,
                         press=press, label=label):
                    cmd_q.put(cmd)
                    s = connect_raw()
                    socks.append(s)
                    ops, staged = [], []

                    def on_k():
                        if press:
                            cmd_q.put(("quit_app",))
                            control["drop_silent"](active(control))
                    trl.mock_update_next(s, ops, staged, "ok", blob, ident=ident,
                                         k_delay=2.5, on_k=on_k)
                    check(f"{label}: the same wire ops as ever", ops == want_ops, ops)
                    check(f"{label}: one verdict, ok, its brand",
                          wait_until(lambda: len(state["upd"]) == 1, timeout=3.0)
                          and state["upd"][0][0] and state["upd"][0][2] == brand,
                          state["upd"])
                    check(f"{label}: never reaped, never dropped",
                          not logged(state, NO_WORD) and not logged(state, DROPPED),
                          [m for m in state["logs"] if NO_WORD in m or DROPPED in m])
                    check(f"{label}: no stray put_done", state["puts"] == [],
                          state["puts"])
                    if press:
                        check(f"{label}: the press's quit left with the seat "
                              "(never handed on)",
                              wait_until(lambda: shared_items(cmd_q) == [],
                                         timeout=3.0), shared_items(cmd_q))
                _server(label, body, verify_crc=lambda: True,
                        NEXT_PEER_SILENCE_LIMIT=1.5, NEXT_DROP_SILENCE=0.5,
                        QLNR_PEER_SILENCE_LIMIT=1.5, QLNR_DROP_SILENCE=0.5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_helpers()
    test_settings_switch_wiring()
    test_next_limit_reaps_each_kind()
    test_local_seat_keeps_todays_path()
    test_next_long_command_then_late_poll()
    test_next_put_gaps()
    test_next_disconnect_drop()
    test_next_drop_looks_past_reads_never_writes()
    test_dead_holder_with_a_queued_write()
    test_transfer_hold()
    test_stall_guard()
    test_keepalive_on_every_seat()
    test_switch_off_is_the_620s_path()
    test_dead_next_quit_never_reaches_another_seat()
    test_sessions_off_next()
    test_update_macro_not_reaped()
    print("\nRESULT: " + ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
