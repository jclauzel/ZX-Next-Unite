"""End-to-end test of the NextSync HTTP bridge (zxnu_http_bridge) over BOTH
hosts, against the mock Next of test_remote_listen:

  phase A: real HTTP -> bridge -> app worker (run_remote_listen_server) -> mock dot
  phase B: real HTTP -> bridge -> nextsync5.listen_session               -> mock dot

Run with: python test_http_bridge.py
"""
import json
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

    st, body = http(HTTP_B, "/get?path=boot.bas")
    check("B /get", st == 200 and body == filebytes, len(body))

    st, body = http(HTTP_B, "/put?path=/ho/up3.bin", body=b"np5" * 50)
    check("B /put", st == 200 and b"150 bytes" in body, body)

    st, body = http(HTTP_B, "/mkdir?path=/newdir")
    check("B /mkdir", st == 200, body)

    st, body = http(HTTP_B, "/ren?from=/a&to=/b")
    check("B /ren", st == 200, body)

    st, body = http(HTTP_B, "/free?drive=m:&json=1")
    j = json.loads(body)
    check("B /free", st == 200 and j["free_bytes"] == 2048 * 512, j)

    st, body = http(HTTP_B, "/rfsize?path=/games/lev&json=1")
    j = json.loads(body)
    check("B /rfsize", st == 200 and j["bytes"] == (1 << 32) + 512, j)

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


def main():
    phase_a()
    print()
    phase_b()
    print()
    phase_token()
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
