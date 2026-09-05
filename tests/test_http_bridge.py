"""End-to-end test of the NextSync HTTP bridge (zxnu_http_bridge) over BOTH
hosts, against the mock Next of test_remote_listen:

  phase A: real HTTP -> bridge -> app worker (run_remote_listen_server) -> mock dot
  phase B: real HTTP -> bridge -> nextsync5.listen_session               -> mock dot

Run with: python test_http_bridge.py
"""
import json
import zlib
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication, Qt          # noqa: E402
from zxnu_workers import RemoteExplorerSignals, run_remote_listen_server  # noqa: E402
from zxnu_http_bridge import NextSyncHttpBridge, QueueBridgeHost          # noqa: E402
from test_remote_listen import mock_next                 # noqa: E402

WORKER_PORT = 2050
HTTP_A = 18080
HTTP_B = 18081
HTTP_TOK1 = 18082
HTTP_TOK2 = 18083
HTTP_OSP = 18084

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def http(port, path, body=None, method=None):
    """(status, bytes) for one request against 127.0.0.1:port."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body,
        method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_until(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


# =====================================================================
#  Phase A: bridge over the app's -listen worker
# =====================================================================
def phase_a():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    entries = [(True, 0, "GAMES"), (False, 1234, "boot.bas")]
    filebytes = b"Hello Next!\r\n" * 5
    fs = {"del": {"a.txt": b"AA", "sub": {"b.txt": b"BB"}}}
    cap = {}

    sig = RemoteExplorerSignals()
    state = {"connected": False, "current": "", "drives": None}
    sig.connected.connect(lambda: state.update(connected=True), Qt.DirectConnection)
    sig.disconnected.connect(
        lambda: state.update(connected=False, current="", drives=None),
        Qt.DirectConnection)
    sig.drives.connect(
        lambda cur, ls: state.update(current=cur or "",
                                     drives=list(ls) if ls else None),
        Qt.DirectConnection)

    cmd_q = queue.Queue()
    stop = threading.Event()
    running = {"on": True}

    # The same command-tuple dialect the app's adapter builds (zx-next-unite).
    def make_cmd(op, a1, a2, reply):
        if op == "ls":
            return ("ls", a1, reply)
        if op == "get":
            return ("get", a1, a2, reply)
        if op == "put":
            return ("put", a2, a1, reply)
        if op in ("mkdir", "rmdir", "rm", "rmtree"):
            return (op, a1, reply)
        if op == "ren":
            return ("rename", a1, a2, reply)
        if op == "rcpy":
            return ("rcpy", a1, a2, reply)
        if op == "rfsize":
            return ("fsize", a1, reply)
        if op == "free":
            return ("free", a1, reply)
        if op == "drives":
            return ("drives", reply)
        if op == "version":
            return ("version", reply)
        if op == "crc":
            return ("crc", a1, reply)
        if op == "forceexit":
            return ("quit", reply)
        return None

    def enqueue(cmd):
        if not running["on"]:
            return False
        cmd_q.put(cmd)
        return True

    def state_fn():
        return {"listening": running["on"], "connected": state["connected"],
                "current": state["current"], "drives": state["drives"]}

    bridge = NextSyncHttpBridge(
        QueueBridgeHost(enqueue, make_cmd, state_fn), port=HTTP_A)
    okd, err = bridge.start()
    check("A bridge started", okd, err)

    t = threading.Thread(target=run_remote_listen_server,
                         args=(sig, cmd_q, stop, WORKER_PORT), daemon=True)
    t.start()
    time.sleep(0.3)
    s = socket.create_connection(("127.0.0.1", WORKER_PORT), timeout=10)
    mt = threading.Thread(target=mock_next,
                          args=(s, entries, filebytes, cap, fs), daemon=True)
    mt.start()
    check("A connected", wait_until(lambda: state["connected"]))

    # status (json): triggers an on-demand drives query -> partitions
    st, body = http(HTTP_A, "/status?json=1")
    j = json.loads(body)
    check("A /status", st == 200 and j["connected"] and j["listening"]
          and j["partitions"] == 2 and j["drives"] == ["C", "M"], j)

    st, body = http(HTTP_A, "/ls?path=/")
    lines = body.decode().splitlines()
    check("A /ls", st == 200 and lines[0] == "OK 2 entries"
          and "D\t0\tGAMES" in lines and "F\t1234\tboot.bas" in lines, lines)

    st, body = http(HTTP_A, "/get?path=boot.bas")
    check("A /get file", st == 200 and body == filebytes, len(body))

    st, body = http(HTTP_A, "/get?path=/games/lev")
    check("A /get folder -> 400", st == 400 and b"folder" in body, body)

    # Ranged slices (ZXNextRemote 0.7.10's overrun-proof retry): &off=&len=
    # windows of one cached relay; EOF = a short slice; the cache drops
    # once the end is served and a mid-file cache miss recovers by
    # re-relaying.
    got = b""
    ok_slices = True
    while True:
        st, part = http(HTTP_A, f"/get?path=boot.bas&off={len(got)}&len=16")
        ok_slices = ok_slices and st == 200
        got += part
        if len(part) < 16:
            break
    check("A /get ranged reassembles", ok_slices and got == filebytes,
          (ok_slices, len(got)))
    st, part = http(HTTP_A, "/get?path=boot.bas&off=13&len=16")
    check("A /get ranged cache-miss recovers",
          st == 200 and part == filebytes[13:29], part)
    st, part = http(HTTP_A, "/get?path=boot.bas&off=999&len=16")
    check("A /get ranged past EOF -> empty", st == 200 and part == b"", part)
    st, part = http(HTTP_A, "/get?path=boot.bas&off=-1&len=16")
    check("A /get ranged bad off -> 400", st == 400, (st, part))

    st, body = http(HTTP_A, "/put?path=/ho/up2.bin", body=b"\x01\x02" * 100)
    check("A /put", st == 200 and b"OK put /ho/up2.bin (200 bytes)" in body, body)

    st, body = http(HTTP_A, "/put?path=/locked/up.bin", body=b"x")
    check("A /put fail -> 502", st == 502, (st, body))

    for verb in ("mkdir", "rmdir", "rm"):
        st, body = http(HTTP_A, f"/{verb}?path=/zz")
        check(f"A /{verb}", st == 200 and body.decode().startswith(f"OK {verb}"), body)

    st, body = http(HTTP_A, "/rmtree?path=/del")
    check("A /rmtree", st == 200 and "del" not in fs, (body, list(fs)))

    st, body = http(HTTP_A, "/ren?from=/ho/a.txt&to=/ho/b.txt")
    check("A /ren", st == 200 and b"OK ren" in body, body)

    st, body = http(HTTP_A, "/rcpy?src=/games/lev&dst=M:/bk/lev&json=1")
    j = json.loads(body)
    check("A /rcpy", st == 200 and j["ok"] and j["files"] == 1, j)

    st, body = http(HTTP_A, "/rcpy?src=/games/lev&dst=/games/lev/x")
    check("A /rcpy self-trap -> 400", st == 400, body)

    st, body = http(HTTP_A, "/rcpy?src=/locked/t&dst=/t2")
    check("A /rcpy fail -> 502", st == 502 and b"copied files stay" in body, body)

    st, body = http(HTTP_A, "/rfsize?path=/games/lev&json=1")
    j = json.loads(body)
    check("A /rfsize", st == 200 and j["files"] == 7 and j["dirs"] == 3
          and j["bytes"] == (1 << 32) + 512, j)

    st, body = http(HTTP_A, "/rfsize?path=/gone")
    check("A /rfsize fail -> 502", st == 502, body)

    # /crc: the CRC-32 computed ON the Next ('K'), 8 upper-case hex digits;
    # &bare=1 is just the digits; a file that does not open is 502; no path
    # is 400.
    _want_crc = "%08X" % (zlib.crc32(b"/games/a.tap") & 0xffffffff)
    st, body = http(HTTP_A, "/crc?path=/games/a.tap&json=1")
    j = json.loads(body)
    check("A /crc (computed on the Next)", st == 200 and j["crc32"] == _want_crc, j)
    st, body = http(HTTP_A, "/crc?path=/games/a.tap&bare=1")
    check("A /crc bare", st == 200 and body.strip() == _want_crc.encode(), body)
    st, body = http(HTTP_A, "/crc?path=/gone")
    check("A /crc fail -> 502", st == 502, body)
    st, body = http(HTTP_A, "/crc")
    check("A /crc without a path -> 400", st == 400, body)

    st, body = http(HTTP_A, "/free?drive=m:&json=1")
    j = json.loads(body)
    check("A /free", st == 200 and j["free_bytes"] == 2048 * 512, j)

    st, body = http(HTTP_A, "/free?drive=E")
    check("A /free fail -> 502", st == 502, body)

    st, body = http(HTTP_A, "/drives")
    check("A /drives", st == 200 and b"partitions: 2" in body, body)

    st, body = http(HTTP_A, "/help")
    check("A /help lists routes", st == 200 and b"/rcpy" in body
          and b"/status" in body and b"/forceexit" in body)

    st, body = http(HTTP_A, "/ls")   # defaults to "/"
    check("A /ls default path", st == 200, body)

    # End the session over HTTP: /forceexit sends 'Q', the mock leaves, the
    # worker fills the bridge reply and disconnects.
    st, body = http(HTTP_A, "/forceexit?json=1")
    j = json.loads(body)
    check("A /forceexit", st == 200 and j["ok"], j)
    check("A disconnected", wait_until(lambda: not state["connected"]))
    st, body = http(HTTP_A, "/status?json=1")
    j = json.loads(body)
    check("A /status after quit", st == 200 and not j["connected"]
          and j["partitions"] == 0, j)
    st, body = http(HTTP_A, "/ls?path=/")
    check("A /ls after quit -> 503", st == 503, body)

    stop.set()
    bridge.stop()
    t.join(timeout=5)


# =====================================================================
#  Phase B: bridge over nextsync5's listen_session
# =====================================================================
def phase_b():
    _argv, sys.argv = sys.argv, ["nextsync5.py"]   # its module-level arg loop
    import nextsync5
    sys.argv = _argv

    # No console reader in a test: stdin may be closed, and input() hitting
    # EOF would push a "quit" that kills the session instantly.
    nextsync5._ensure_listen_console = nextsync5._listen_queue

    bridge = nextsync5._start_http_bridge(HTTP_B)
    check("B bridge started (-http)", bridge is not None)

    st, body = http(HTTP_B, "/status?json=1")
    j = json.loads(body)
    check("B /status before session", st == 200 and not j["connected"], j)
    st, body = http(HTTP_B, "/mkdir?path=/x")
    check("B command without session -> 503", st == 503, body)
    st, body = http(HTTP_B, "/forceexit")
    check("B /forceexit without session -> 503", st == 503, body)

    entries = [(True, 0, "GAMES"), (False, 1234, "boot.bas")]
    filebytes = b"Hi from nextsync5\r\n" * 3
    srv, cli = socket.socketpair()
    stats = {'packets': 0}
    sess = threading.Thread(target=nextsync5.listen_session,
                            args=(srv, stats), daemon=True)
    sess.start()
    cap = {}
    mt = threading.Thread(
        target=mock_next, args=(cli, entries, filebytes, cap, {}),
        kwargs={"send_listen": False}, daemon=True)
    mt.start()
    check("B session active",
          wait_until(lambda: nextsync5._listen_state['active']))

    st, body = http(HTTP_B, "/status?json=1")
    j = json.loads(body)
    check("B /status connected + partitions", st == 200 and j["connected"]
          and j["partitions"] == 2, j)

    st, body = http(HTTP_B, "/ls?path=/&json=1")
    j = json.loads(body)
    check("B /ls", st == 200 and len(j["entries"]) == 2, j)

    # No roster on this host: /sessions synthesizes the one seat (sid 1),
    # session=1 is accepted, anything else can only be stale -> 410.
    st, body = http(HTTP_B, "/sessions?json=1")
    j = json.loads(body)
    check("B /sessions synthetic seat", st == 200 and j["count"] == 1
          and j["max"] == 1 and j["active"] == 1
          and j["sessions"][0]["sid"] == 1, j)
    st, body = http(HTTP_B, "/ls?path=/&session=1&json=1")
    j = json.loads(body)
    check("B /ls session=1", st == 200 and len(j["entries"]) == 2, j)
    st, body = http(HTTP_B, "/ls?path=/&session=2")
    check("B stale sid -> 410", st == 410, (st, body))

    st, body = http(HTTP_B, "/get?path=boot.bas")
    check("B /get", st == 200 and body == filebytes, len(body))

    st, body = http(HTTP_B, "/put?path=/ho/up3.bin", body=b"np5" * 50)
    check("B /put", st == 200 and b"150 bytes" in body, body)

    st, body = http(HTTP_B, "/mkdir?path=/newdir")
    check("B /mkdir", st == 200, body)

    st, body = http(HTTP_B, "/ren?from=/a&to=/b")
    check("B /ren", st == 200, body)

    st, body = http(HTTP_B, "/version-type?json=1")
    j = json.loads(body)
    check("B /version-type", st == 200 and j["version-type"] == "sync", j)
    st, body = http(HTTP_B, "/version-number")
    check("B /version-number (text)", st == 200
          and b"version-number: 9.9.9" in body, body)

    st, body = http(HTTP_B, "/free?drive=m:&json=1")
    j = json.loads(body)
    check("B /free", st == 200 and j["free_bytes"] == 2048 * 512, j)

    st, body = http(HTTP_B, "/rfsize?path=/games/lev&json=1")
    j = json.loads(body)
    check("B /rfsize", st == 200 and j["bytes"] == (1 << 32) + 512, j)

    st, body = http(HTTP_B, "/crc?path=/games/a.tap&json=1")
    j = json.loads(body)
    check("B /crc (console server)", st == 200
          and j["crc32"] == "%08X" % (zlib.crc32(b"/games/a.tap") & 0xffffffff), j)

    st, body = http(HTTP_B, "/rmtree?path=/del")
    check("B /rmtree unsupported -> 501", st == 501, body)

    # End the session through the CLI client (-forceexit): same /forceexit
    # route, driven by nextsync5's own stdlib HTTP caller.
    rc = nextsync5._cli_forceexit(f"127.0.0.1:{HTTP_B}")
    check("B -forceexit CLI", rc == 0, rc)
    check("B session ended",
          wait_until(lambda: not nextsync5._listen_state['active']))
    rc = nextsync5._cli_forceexit("127.0.0.1:1")   # nothing listens there
    check("B -forceexit unreachable -> 1", rc == 1, rc)
    st, body = http(HTTP_B, "/status?json=1")
    j = json.loads(body)
    check("B /status after quit", st == 200 and not j["connected"], j)
    st, body = http(HTTP_B, "/sessions?json=1")
    j = json.loads(body)
    check("B /sessions after quit empty", st == 200 and j["count"] == 0
          and j["active"] is None, j)

    bridge.stop()
    srv.close()
    cli.close()


def http_h(port, path, headers=None):
    """(status, bytes) for one GET against 127.0.0.1:port with optional headers."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# =====================================================================
#  Phase SESSIONS: /sessions + session-targeted ops over the app worker
# =====================================================================
WORKER_S = 2051
HTTP_S = 18086


def phase_sessions():
    """Multi-session bridge: GET /sessions lists the seated Nexts with the
    combo's exact labels, ?session=N (or the header; the param wins)
    targets one seat WITHOUT moving the baton, a malformed selector is
    400, a departed sid is 410, and the sid counter survives the routine
    worker restart — so a stale id can only ever mean "gone", never
    "another machine"."""
    print("=== phase SESSIONS: /sessions + session-targeted ops ===")
    from zxnu_http_bridge import BRIDGE_SESSION_HEADER

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    sig = RemoteExplorerSignals()
    state = {"connected": False}
    roster_seen = {"last": (None, [])}
    sig.connected.connect(lambda: state.update(connected=True),
                          Qt.DirectConnection)
    sig.disconnected.connect(lambda: state.update(connected=False),
                             Qt.DirectConnection)
    sig.peers.connect(lambda p: roster_seen.update(last=p),
                      Qt.DirectConnection)

    cmd_q = queue.Queue()
    stop = threading.Event()
    control = {"seq": 0}                     # ONE dict across both workers
    names = {"127.0.0.1": "Next"}            # the pane's address-keyed names

    def make_cmd(op, a1, a2, reply):
        if op == "ls":
            return ("ls", a1, reply)
        if op == "drives":
            return ("drives", reply)
        if op == "version":
            return ("version", reply)
        if op == "crc":
            return ("crc", a1, reply)
        if op == "forceexit":
            return ("quit", reply)
        return None

    def enqueue(cmd):
        cmd_q.put(cmd)
        return True

    def state_fn():
        return {"listening": True, "connected": state["connected"],
                "current": "", "drives": None}

    # Mirrors zxnu_nextsync_pane's wiring over the worker's control surface.
    def sessions_fn():
        r = control.get("roster")
        active, plist = r() if r is not None else (None, [])
        return (active, [(s, a, names.get(a, "")) for s, a in plist],
                control.get("max_peers", 4))

    def enqueue_to(sid, cmd):
        fn = control.get("enqueue_to")
        return bool(fn is not None and fn(sid, cmd))

    bridge = NextSyncHttpBridge(
        QueueBridgeHost(enqueue, make_cmd, state_fn,
                        sessions=sessions_fn, enqueue_to=enqueue_to),
        port=HTTP_S)
    okd, err = bridge.start()
    check("S bridge started", okd, err)

    t = threading.Thread(target=run_remote_listen_server,
                         args=(sig, cmd_q, stop, WORKER_S),
                         kwargs={"control": control}, daemon=True)
    t.start()
    time.sleep(0.3)

    def seat(entries, filebytes=b"x"):
        s = socket.create_connection(("127.0.0.1", WORKER_S), timeout=10)
        threading.Thread(target=mock_next,
                         args=(s, entries, filebytes, {}, {}),
                         daemon=True).start()
        return s

    def roster_size():
        return len(roster_seen["last"][1])

    s1 = seat([(False, 111, "one.txt")])
    check("S first Next seated", wait_until(lambda: roster_size() == 1))
    s2 = seat([(False, 222, "two.txt")])
    check("S second Next seated", wait_until(lambda: roster_size() == 2))

    st, body = http(HTTP_S, "/sessions?json=1")
    j = json.loads(body)
    check("S /sessions json", st == 200 and j["ok"] and j["count"] == 2
          and j["active"] == 1 and j["max"] == 4
          and [x["sid"] for x in j["sessions"]] == [1, 2], j)
    check("S /sessions labels", j["sessions"][0]["label"]
          == "127.0.0.1 #1 - Next"
          and j["sessions"][0]["active"] is True
          and j["sessions"][1]["active"] is False, j["sessions"])

    st, body = http(HTTP_S, "/sessions")
    lines = body.decode().splitlines()
    check("S /sessions text", st == 200
          and lines[0] == "OK active: 1 count: 2 max: 4"
          and lines[1] == "1\t127.0.0.1 #1 - Next"
          and lines[2] == "2\t127.0.0.1 #2 - Next", lines)

    # Targeting: the benched session answers, the baton never moves.
    st, body = http(HTTP_S, "/ls?path=/&session=2")
    check("S /ls session=2 -> the benched Next", st == 200
          and b"two.txt" in body and b"one.txt" not in body, body)
    st, body = http(HTTP_S, "/ls?path=/")
    check("S /ls unselected -> still the active Next", st == 200
          and b"one.txt" in body, body)
    check("S baton untouched", roster_seen["last"][0] == 1,
          roster_seen["last"])

    st, body = http_h(HTTP_S, "/ls?path=/",
                      {BRIDGE_SESSION_HEADER: "2"})
    check("S header selector", st == 200 and b"two.txt" in body, body)
    st, body = http_h(HTTP_S, "/ls?path=/&session=1",
                      {BRIDGE_SESSION_HEADER: "2"})
    check("S param beats header", st == 200 and b"one.txt" in body, body)

    st, body = http(HTTP_S, "/ls?path=/&session=abc")
    check("S malformed selector -> 400", st == 400
          and b"bad session selector" in body, (st, body))
    st, body = http(HTTP_S, "/ls?path=/&session=99")
    check("S unknown sid -> 410", st == 410 and b"gone" in body, (st, body))

    st, body = http(HTTP_S, "/status?json=1")
    j = json.loads(body)
    check("S /status json roster fields", st == 200
          and j["sessions"] == 2 and j["active"] == 1, j)
    st, body = http(HTTP_S, "/status")
    text = body.decode()
    check("S /status text additive", "connected: yes" in text
          and "sessions: 2" in text and "active: 1" in text, text)

    # Departure: the reaper prunes the seat, its sid answers 410 forever.
    s2.close()
    check("S departed Next reaped", wait_until(lambda: roster_size() == 1))
    st, body = http(HTTP_S, "/ls?path=/&session=2")
    check("S departed sid -> 410", st == 410, (st, body))

    # Restart: the last Next leaves, the worker returns (the pane would
    # auto-relisten); a new worker over the SAME control dict must keep
    # counting — the next seat is #3, and the old sids stay 410.
    s1.close()
    check("S last-leave ends the worker",
          wait_until(lambda: not state["connected"]))
    t.join(timeout=5)
    check("S worker returned", not t.is_alive())
    t2 = threading.Thread(target=run_remote_listen_server,
                          args=(sig, cmd_q, stop, WORKER_S),
                          kwargs={"control": control}, daemon=True)
    t2.start()
    time.sleep(0.3)
    s3 = seat([(False, 333, "three.txt")])
    check("S reseated", wait_until(lambda: roster_size() == 1
                                   and state["connected"]))
    st, body = http(HTTP_S, "/sessions?json=1")
    j = json.loads(body)
    check("S sid continuity across restart", st == 200 and j["count"] == 1
          and j["sessions"][0]["sid"] == 3 and j["active"] == 3, j)
    st, body = http(HTTP_S, "/ls?path=/&session=3")
    check("S /ls new sid", st == 200 and b"three.txt" in body, body)
    st, body = http(HTTP_S, "/ls?path=/&session=1")
    check("S pre-restart sid stays 410", st == 410, (st, body))

    stop.set()
    s3.close()
    bridge.stop()
    t2.join(timeout=5)


def phase_token():
    """Bearer-token guard: with auth_token set, only requests carrying the
    correct ZXNEXTUNITE-BRIDGE-TOKEN header are answered; everything else gets
    HTTP 401. The guard runs in before_request ahead of any adapter call, so a
    trivial fake adapter is enough."""
    print("=== phase TOKEN: bearer-token 401 guard ===")
    from zxnu_http_bridge import BRIDGE_TOKEN_HEADER

    class _FakeAdapter:
        def state(self):
            return {"listening": True, "connected": False,
                    "current": "", "drives": []}

        def run(self, op, a1="", a2="", body=None, timeout=None):
            return {"ok": False, "http": 503, "error": "no next"}

    TOKEN = "Tk" + "A1b2C3d4E5" * 6        # 62 chars, alphanumeric
    bridge = NextSyncHttpBridge(_FakeAdapter(), port=HTTP_TOK1, auth_token=TOKEN)
    okd, err = bridge.start()
    check("TOKEN bridge started", okd, err)
    try:
        st, body = http_h(HTTP_TOK1, "/help")
        check("no header -> 401", st == 401, (st, body[:40]))
        st, body = http_h(HTTP_TOK1, "/help", {BRIDGE_TOKEN_HEADER: "wrong"})
        check("wrong token -> 401", st == 401, (st, body[:40]))
        st, body = http_h(HTTP_TOK1, "/help", {BRIDGE_TOKEN_HEADER: TOKEN})
        check("correct token -> 200 help",
              st == 200 and b"NextSync HTTP bridge" in body, (st, body[:40]))
        st, body = http_h(HTTP_TOK1, "/status?json=1", {BRIDGE_TOKEN_HEADER: TOKEN})
        check("correct token -> 200 status",
              st == 200 and b'"ok": true' in body, (st, body[:60]))
        st, body = http_h(HTTP_TOK1, "/status")
        check("protected /status no header -> 401", st == 401, (st, body[:40]))
    finally:
        bridge.stop()

    # With NO auth_token the bridge stays open (the historical behaviour).
    bridge2 = NextSyncHttpBridge(_FakeAdapter(), port=HTTP_TOK2)
    okd, err = bridge2.start()
    check("open bridge started", okd, err)
    try:
        st, body = http_h(HTTP_TOK2, "/help")
        check("no token configured -> open 200", st == 200, (st, body[:40]))
    finally:
        bridge2.stop()


def phase_osprot():
    """OS protection: an operation refused by the far side's OS protection
    reaches the adapter as {'ok': False, 'http': 401, 'error': 'os-protected: …'}
    (the worker maps ZXNextRemote's 'F'+OSP marker to that). The bridge must
    relay it as HTTP 401 with the 'os-protected' body — distinct from the
    bearer-token 401 — so the ZXNextRemote HTTP client can name it. Since 9.7.4
    that covers the READ verbs too (ls/get into a Read+write-protected root):
    those used to reach the bridge as a plain failure and relay as the "missing
    folder?" 502, which read as an unexplained error on a protected browse. The
    dotN is never involved; a plain Next simply never produces this."""
    print("=== phase OSPROT: refusal -> 401 os-protected (writes + reads) ===")
    from zxnu_workers import RE_OSP_ERROR

    class _OspAdapter:
        def state(self):
            return {"listening": True, "connected": True,
                    "current": "C", "drives": ["C"]}

        def run(self, op, a1="", a2="", body=None, timeout=None):
            # A path under the protected root is 401 whether the verb reads or
            # writes (Read+write protection); free/drives touch no path and are
            # unaffected, proving the guard is the path, not a blanket block.
            if op in ("mkdir", "rmdir", "rm", "ren", "rcpy", "put",
                      "ls", "get"):
                return {"ok": False, "http": 401, "error": RE_OSP_ERROR}
            return {"ok": True}

    bridge = NextSyncHttpBridge(_OspAdapter(), port=HTTP_OSP)
    okd, err = bridge.start()
    check("OSPROT bridge started", okd, err)
    try:
        for path, verb in (("/mkdir?path=/sys/x", "mkdir"),
                           ("/rm?path=/sys/x", "rm"),
                           ("/rmdir?path=/sys/x", "rmdir"),
                           ("/ls?path=/sys", "ls"),
                           ("/get?path=/sys/x", "get")):
            st, body = http(HTTP_OSP, path)
            check(f"{verb} in a protected root -> 401", st == 401, (st, body[:40]))
            check(f"{verb} 401 body says os-protected",
                  b"os-protected" in body, body[:60])
        # A verb that touches no path still succeeds -> the guard is the
        # protected path, not a blanket block.
        st, body = http(HTTP_OSP, "/free?drive=C&json=1")
        check("a pathless verb is unaffected", st == 200, (st, body[:40]))
    finally:
        bridge.stop()


def main():
    phase_a()
    print()
    phase_b()
    print()
    phase_sessions()
    print()
    phase_token()
    print()
    phase_osprot()
    print()
    phase_trace()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


def phase_trace():
    """-v request tracing + the in-flight gauge.

    These are the "who is stuck?" diagnostics: with tracing on, every
    request and its answer is logged with the live in-flight count, the
    body size and the elapsed time; the gauge itself is tracked whether
    or not tracing is on, so /status can always report it."""
    lines = []

    class _SlowAdapter:
        def state(self):
            return {"listening": True, "connected": False,
                    "current": "", "drives": []}

        def run(self, op, a1="", a2="", body=None, timeout=None):
            return {"ok": False, "http": 503, "error": "no next"}

    port = 18084
    bridge = NextSyncHttpBridge(_SlowAdapter(), port=port, verbose=True,
                                log=lines.append)
    okd, err = bridge.start()
    check("TRACE bridge started", okd, err)
    try:
        check("trace: idle gauge reads zero", bridge.inflight == 0,
              bridge.inflight)
        st, _ = http(port, "/help")
        check("trace: request served", st == 200, st)

        req = [ln for ln in lines if ln.startswith("HTTP -> ")]
        resp = [ln for ln in lines if ln.startswith("HTTP bridge: 200")]
        check("trace: the request was logged", len(req) == 1, req)
        check("trace: with the in-flight count and the path",
              bool(req) and req[0].startswith("HTTP -> [1] GET /help"), req)
        check("trace: the response was logged", len(resp) == 1, resp)
        check("trace: with status, size and elapsed time",
              bool(resp) and "200 GET /help (" in resp[0]
              and " bytes, " in resp[0] and resp[0].rstrip().endswith("s)"),
              resp)
        check("trace: the gauge came back to zero", bridge.inflight == 0,
              bridge.inflight)

        # /status reports the gauge (excluding itself) for a UI to show.
        st, body = http(port, "/status?json=1")
        payload = json.loads(body)
        check("trace: /status carries the in-flight count",
              payload.get("inflight") == 0, payload.get("inflight"))
        check("trace: /status carries the busy list", payload.get("busy") == [],
              payload.get("busy"))
    finally:
        bridge.stop()

    # The stall watchdog names a request that outlives its threshold, and
    # it does so with tracing OFF — that is the point of it.
    quiet = []
    hold = threading.Event()

    class _HangAdapter:
        def state(self):
            return {"listening": True, "connected": True,
                    "current": "C", "drives": ["C"]}

        def run(self, op, a1="", a2="", body=None, timeout=None):
            hold.wait(6.0)
            return {"ok": False, "http": 504, "error": "timed out"}

    port2 = 18085
    b2 = NextSyncHttpBridge(_HangAdapter(), port=port2, log=quiet.append)
    b2.SLOW_AFTER = 0.5
    b2.SLOW_EVERY = 0.5
    b2._WATCH_TICK = 0.2
    okd, err = b2.start()
    check("WATCH bridge started", okd, err)
    try:
        th = threading.Thread(
            target=lambda: http(port2, "/ls?path=/"), daemon=True)
        th.start()
        seen = wait_until(
            lambda: any("still waiting" in ln for ln in quiet), timeout=5.0)
        check("watchdog: a stuck request is named while it hangs", seen,
              [ln for ln in quiet][-3:])
        check("watchdog: the gauge shows it in flight", b2.inflight >= 1,
              b2.inflight)
        busy = b2.inflight_detail()
        check("watchdog: inflight_detail names the request",
              bool(busy) and "/ls" in busy[0][1], busy)
        hold.set()
        th.join(timeout=10)
        check("watchdog: gauge empties once it finishes",
              wait_until(lambda: b2.inflight == 0, timeout=5.0), b2.inflight)
        # The OUTCOME line is unconditional: "was it answered, and with
        # what?" is the first question a stalled transfer raises, and the
        # user should not need to have enabled tracing beforehand.
        done = [ln for ln in quiet if ln.startswith("HTTP bridge: ")
                and " GET /ls" in ln]
        check("outcome line is logged with tracing OFF", bool(done), quiet[-3:])
        check("outcome line carries the status code and timing",
              bool(done) and "504 GET /ls" in done[-1] and "s)" in done[-1],
              done)
    finally:
        hold.set()
        b2.stop()


if __name__ == "__main__":
    main()
