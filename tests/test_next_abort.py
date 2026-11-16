"""9.7.72: the user of a ZX Next Remote 1.14.0+ can press BREAK during a
transfer and answer Yes. The Next marks the void file with BRK - a put's
refusal is 'F'+BRK, a get's terminal block is 'B'+BRK with no 'E' before
it - and the PC keeps no half file: a download cut short is removed and
reported failed (with or without the mark), and the pane is told so it
can drop what is still queued.

Drives the real listen worker against a fake Next. Headless; runs in CI."""
import os
import queue
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QCoreApplication, Qt
from zxnu_workers import (RemoteExplorerSignals, _re_is_abort,
                          run_remote_listen_server)

FAILS = []
LOGS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILS.append(name)


def cs(p):
    c0 = c1 = 0
    for x in p:
        c0 = (c0 ^ x) & 0xff
        c1 = (c1 + c0) & 0xff
    return c0, c1


def frame(payload, pkt=0):
    c0, c1 = cs(payload)
    return (len(payload) + 5).to_bytes(2, "big") + bytes(payload) + bytes([c0, c1, pkt & 0xff])


def rx_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            return None
        b += c
    return b


def rx_payload(s):
    hdr = rx_exact(s, 2)
    total = (hdr[0] << 8) | hdr[1]
    return rx_exact(s, total - 2)[:-3]


def push(sock, payload, pkt):
    time.sleep(0.003)
    sock.sendall(frame(payload, pkt))
    assert rx_payload(sock)[0:1] == b"O"


def name_block(name):
    raw = name.encode()
    return b"N" + bytes(4) + bytes([len(raw)]) + raw


def fake_next(sock, script, seen, want):
    """Serves *want* commands. script: 'get-brk' (N, D, then 'B'+BRK),
    'get-cut' (N, D, plain 'B'), 'get-ok', 'put-brk' (two pulls, then
    'F'+BRK), 'put-f' (a plain 'F')."""
    sock.sendall(b"Listen")
    assert rx_payload(sock) == b"Listening"
    while len(seen) < want:
        time.sleep(0.003)
        sock.sendall(b"Poll")
        cmd = rx_payload(sock)
        op = cmd[0:1]
        if op == b"I":
            continue
        seen.append(cmd)
        if op == b"G":
            push(sock, name_block(cmd[1:].decode()), 0)
            push(sock, b"D" + b"x" * 300, 1)
            if script == "get-drop":
                sock.close()               # the link dies under the file
                return
            if script == "get-ok":
                push(sock, b"E", 2)
                push(sock, b"B", 3)
            elif script == "get-brk":
                push(sock, b"BBRK", 2)
            else:
                push(sock, b"B", 2)
        elif op == b"P":
            for _ in range(2):
                time.sleep(0.002)
                sock.sendall(b"Get")
                rx_payload(sock)
            time.sleep(0.003)
            sock.sendall(frame(b"FBRK" if script == "put-brk" else b"F", 0))
            assert rx_payload(sock) == b"Ok"
        else:
            raise AssertionError("unscripted command %r" % cmd)
    # idle a little so the worker's reports are all out
    for _ in range(5):
        time.sleep(0.003)
        sock.sendall(b"Poll")
        rx_payload(sock)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run(script, cmds):
    """Returns (events in the order the worker emitted them, the commands
    the fake saw)."""
    sig = RemoteExplorerSignals()
    ev = []
    sig.next_aborted.connect(lambda k, p: ev.append(("aborted", k, p)), Qt.DirectConnection)
    sig.put_done.connect(lambda ok, r: ev.append(("put_done", ok, r)), Qt.DirectConnection)
    sig.got.connect(lambda r, l: ev.append(("got", r)), Qt.DirectConnection)
    sig.error.connect(lambda m: ev.append(("error", m)), Qt.DirectConnection)
    sig.log.connect(lambda m: LOGS.append(m), Qt.DirectConnection)
    del LOGS[:]
    q = queue.Queue()
    for c in cmds:
        q.put(c)
    stop = threading.Event()
    port = free_port()
    t = threading.Thread(target=run_remote_listen_server, args=(sig, q, stop, port),
                         kwargs={"verify_crc": lambda: False}, daemon=True)
    t.start()
    time.sleep(0.3)
    seen = []
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.settimeout(10)
    try:
        fake_next(s, script, seen, len(cmds))
        time.sleep(0.4)
    finally:
        stop.set()
        s.close()
        t.join(timeout=5)
    return ev, seen


def test_mark():
    check("'F'+BRK and 'B'+BRK are the abort mark",
          _re_is_abort(b"FBRK") and _re_is_abort(b"BBRK"))
    check("a plain 'F', a plain 'B', OSP and DRV are not",
          not _re_is_abort(b"F") and not _re_is_abort(b"B")
          and not _re_is_abort(b"FOSP") and not _re_is_abort(b"FDRV")
          and not _re_is_abort(b"DBRK"))


def test_get():
    tmp = tempfile.mkdtemp(prefix="abort_")
    ev, _ = run("get-brk", [("get", "c:/a/big.bin", tmp)])
    kinds = [e[0] for e in ev]
    check("get: the abort is announced before the failure report",
          kinds == ["aborted", "error"] and ev[0][1:] == ("get", "c:/a/big.bin"), ev)
    check("get: the partial download is removed", os.listdir(tmp) == [], os.listdir(tmp))
    said = [m for m in LOGS if m.startswith("Cleared partially transferred file: ")]
    check("get: and the console names the file it cleared",
          len(said) == 1 and said[0].endswith("big.bin"), LOGS)

    tmp = tempfile.mkdtemp(prefix="abort_")
    ev, _ = run("get-drop", [("get", "c:/a/big.bin", tmp)])
    said = [m for m in LOGS if m.startswith("Cleared partially transferred file: ")]
    check("get: a link that dies under the file leaves no prefix either",
          os.listdir(tmp) == [] and len(said) == 1, (os.listdir(tmp), LOGS))
    check("get: and no abort is announced for it",
          "aborted" not in [e[0] for e in ev], ev)

    tmp = tempfile.mkdtemp(prefix="abort_")
    ev, _ = run("get-cut", [("get", "c:/a/big.bin", tmp)])
    check("get: a stream that ends without the file's 'E' is a failure, "
          "with no abort announced", [e[0] for e in ev] == ["error"], ev)
    check("get: and its prefix is removed too", os.listdir(tmp) == [], os.listdir(tmp))

    tmp = tempfile.mkdtemp(prefix="abort_")
    ev, _ = run("get-ok", [("get", "c:/a/big.bin", tmp)])
    check("get: a whole file clears nothing",
          not [m for m in LOGS if m.startswith("Cleared")], LOGS)
    check("get: a whole file is kept and reported as before",
          [e[0] for e in ev] == ["got"] and os.listdir(tmp) == ["big.bin"]
          and os.path.getsize(os.path.join(tmp, "big.bin")) == 300, ev)


def test_put():
    tmp = tempfile.mkdtemp(prefix="abort_")
    local = os.path.join(tmp, "up.bin")
    with open(local, "wb") as f:
        f.write(bytes(5000))
    ev, _ = run("put-brk", [("put", local, "c:/up.bin")])
    check("put: the abort is announced, then the put fails once",
          ev == [("aborted", "put", "c:/up.bin"), ("put_done", False, "c:/up.bin")], ev)
    ev, _ = run("put-f", [("put", local, "c:/up.bin")])
    check("put: a plain refusal announces no abort",
          ev == [("put_done", False, "c:/up.bin")], ev)


def test_console():
    # nextsync5.py's console get keeps no half file either.
    import nextsync5
    src = open(nextsync5.__file__, encoding="utf-8").read()
    check("nextsync5: a get cut short removes its file and fails",
          "os.remove(st['last'])" in src and "if st.get('cut'):" in src)


def test_widget_wiring():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pane = open(os.path.join(root, "zxnu_nextsync_pane.py"), encoding="utf-8").read()
    wid = open(os.path.join(root, "zxnu_remote_explorer.py"), encoding="utf-8").read()
    check("the pane hands the signal to the widget",
          "next_aborted.connect(widget.on_next_aborted)" in pane)
    body = wid.split("def on_next_aborted", 1)[1].split("\n    def ", 1)[0]
    check("the widget stops what is still queued, the Cancel button's way",
          "self._on_op_cancel()" in body)


def main():
    QCoreApplication.instance() or QCoreApplication(sys.argv)
    test_mark()
    test_get()
    test_put()
    test_console()
    test_widget_wiring()
    print("RESULT:", "ALL CHECKS PASSED" if not FAILS else "FAILED: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
