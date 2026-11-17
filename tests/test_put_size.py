"""9.7.71: a put tells a ZX Next Remote 1.13.0+ Listener the file's size, as
NUL + eight upper-case hex digits after the path, so its transfer display
can show a percentage. Every other listener gets the put it always got.

Drives the real listen worker against a fake Next whose 'Y' answer is
scripted, and reads the put command off the wire. Headless; runs in CI."""
import os
import queue
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QCoreApplication, Qt
import zxnu_workers
from zxnu_config import ZXNR_NEX_FLAVORS
from zxnu_workers import (RemoteExplorerSignals, re_peer_reads_put_size,
                          re_put_command, run_remote_listen_server)

FAILS = []
SIZE = 2500          # more than one data frame


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


def fake_next(sock, ident, wire, landed):
    """Answers 'Y' with *ident* (None: ignores it, as a listener too old for
    the op does) and pulls a put to its end. Records each command's RAW
    payload."""
    sock.sendall(b"Listen")
    assert rx_payload(sock) == b"Listening"
    while not landed:
        time.sleep(0.003)
        sock.sendall(b"Poll")
        cmd = rx_payload(sock)
        op = cmd[0:1]
        if op == b"I":
            continue
        wire.append(cmd)
        if op == b"Y":
            if ident is None:
                continue
            time.sleep(0.003)
            sock.sendall(frame(ident, 0))
            assert rx_payload(sock)[0:1] == b"O"
        elif op == b"P":
            data = b""
            while True:
                time.sleep(0.002)
                sock.sendall(b"Get")
                chunk = rx_payload(sock)
                if not chunk:
                    break
                data += chunk
            landed.append(data)
        else:
            raise AssertionError("unscripted command %r" % cmd)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_put(ident, ask_version=True, remote="c:/tmp/up.bin"):
    """One session: (optionally) the version query, then a put. Returns
    (the put command as sent, the bytes the fake pulled, put_done reports)."""
    tmp = tempfile.mkdtemp(prefix="putsize_")
    local = os.path.join(tmp, "up.bin")
    blob = bytes(range(256)) * 10
    blob = blob[:SIZE]
    with open(local, "wb") as f:
        f.write(blob)
    sig = RemoteExplorerSignals()
    puts = []
    sig.put_done.connect(lambda ok, r: puts.append((ok, r)), Qt.DirectConnection)
    q = queue.Queue()
    if ask_version:
        q.put(("version",))
    q.put(("put", local, remote))
    stop = threading.Event()
    port = free_port()
    t = threading.Thread(target=run_remote_listen_server, args=(sig, q, stop, port),
                         kwargs={"verify_crc": lambda: False}, daemon=True)
    t.start()
    time.sleep(0.3)
    wire, landed = [], []
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.settimeout(10)
    try:
        fake_next(s, ident, wire, landed)
        time.sleep(0.2)
    finally:
        stop.set()
        s.close()
        t.join(timeout=5)
    put = [w for w in wire if w[0:1] == b"P"]
    return (put[0] if put else None), (landed[0] if landed else None) == blob, puts


def test_helpers():
    reads = re_peer_reads_put_size
    for flavor in ZXNR_NEX_FLAVORS:
        check("%s 1.13.0 reads the size" % flavor, reads((flavor, "1.13.0")))
        check("%s 1.12.3 does not" % flavor, not reads((flavor, "1.12.3")))
    check("a later build reads it", reads(("n2n", "2.0.0")))
    check("the type's case and spaces do not matter", reads((" N2N ", "1.13.0")))
    check("the .sync5 dot reads it from 5.9.17, not before",
          reads(("sync", "5.9.17")) and reads(("sync", "6.0.0"))
          and not reads(("sync", "5.9.16")) and not reads(("sync", "5.9")))
    check("nor QLNextRemote", not reads(("qlnextremote", "9.9.9"))
          and not reads(("qlnt", "9.9.9")))
    check("nor an ident never asked", not reads(None))
    check("nor a listener too old for 'Y'", not reads(("", "")))
    check("nor a version that does not parse", not reads(("n2n", "1.13"))
          and not reads(("n2n", "beta")) and not reads(("n2n", "")))
    new = ("zxnt", "1.13.0")
    check("the command: path, NUL, eight upper-case hex digits",
          re_put_command("c:/a.bin", 0x1ABCD, new) == b"Pc:/a.bin\x000001ABCD",
          re_put_command("c:/a.bin", 0x1ABCD, new))
    check("an empty file's size is sent too",
          re_put_command("c:/a.bin", 0, new) == b"Pc:/a.bin\x0000000000")
    check("the bare command for anyone else",
          re_put_command("c:/a.bin", 77, ("sync", "5.9.16")) == b"Pc:/a.bin"
          and re_put_command("c:/a.bin", 77, None) == b"Pc:/a.bin")
    edge = "c:/" + "x" * (zxnu_workers.RE_PUT_SIZE_MAX_PATH - 3)
    check("the longest path that still carries the size fits the Listener's buffer",
          len(re_put_command(edge, 1, new)) - 1 == zxnu_workers.RE_MAX_REMOTE_PATH)
    check("a longer path is sent bare", re_put_command(edge + "y", 1, new)
          == b"P" + (edge + "y").encode())
    check("a size past four bytes is not sent",
          re_put_command("c:/a.bin", 1 << 32, new) == b"Pc:/a.bin")


def test_wire():
    want = b"Pc:/tmp/up.bin\x00" + (b"%08X" % SIZE)
    cmd, whole, puts = run_put(b"On2n\x001.13.0")
    check("a 1.13.0 Listener is told the size", cmd == want, cmd)
    check("and the file still lands whole, reported once",
          whole and puts == [(True, "c:/tmp/up.bin")], puts)
    cmd, whole, puts = run_put(b"On2n\x001.12.3")
    check("a 1.12.3 Listener gets the bare put", cmd == b"Pc:/tmp/up.bin" and whole, cmd)
    cmd, whole, puts = run_put(b"Osync\x005.9.16")
    check("a .sync5 dot before 5.9.17 gets the bare put",
          cmd == b"Pc:/tmp/up.bin" and whole, cmd)
    cmd, whole, puts = run_put(b"Osync\x005.9.17")
    check("a .sync5 5.9.17 dot is told the size", cmd == want and whole, cmd)
    cmd, whole, puts = run_put(b"Oqlnextremote\x001.7.0")
    check("QLNextRemote gets the bare put", cmd == b"Pc:/tmp/up.bin" and whole, cmd)
    cmd, whole, puts = run_put(None)
    check("a listener too old for 'Y' gets the bare put",
          cmd == b"Pc:/tmp/up.bin" and whole, cmd)
    cmd, whole, puts = run_put(b"On2n\x001.13.0", ask_version=False)
    check("a seat nobody asked gets the bare put, and no 'Y' is sent for it",
          cmd == b"Pc:/tmp/up.bin" and whole, cmd)
    cmd, whole, puts = run_put(b"Ozxnt\x001.13.0", remote="c:/tmp/")
    check("a folder target carries the size after the composed name",
          cmd == want and whole, cmd)


def test_twins():
    # nextsync5.py imports no app module: its copies are kept by hand.
    import nextsync5
    check("nextsync5: the same floor and path bound",
          nextsync5.PUT_SIZE_FLOOR == zxnu_workers.RE_PUT_SIZE_FLOOR
          and nextsync5.PUT_SIZE_DOT_FLOOR == zxnu_workers.RE_PUT_SIZE_DOT_FLOOR
          and nextsync5.PUT_SIZE_MAX_PATH == zxnu_workers.RE_PUT_SIZE_MAX_PATH)
    edge = "c:/" + "x" * (zxnu_workers.RE_PUT_SIZE_MAX_PATH - 3)
    idents = [None, False, ("", ""), ("sync", "9.9.9"), ("sync", "5.9.16"),
              ("sync", "5.9.17"), ("qlnt", "9.9.9"),
              ("n2n", "1.12.3"), ("n2n", "1.13.0"), (" ZXNT ", "1.13.1"),
              ("httpbridge", "2.0.0"), ("n2n", "1.13"), ("n2n", "beta")]
    same = all(nextsync5._put_command(p, n, i) == re_put_command(p, n, i)
               for i in idents for p in ("c:/a.bin", edge, edge + "y")
               for n in (0, 0x1ABCD, 1 << 32))
    check("nextsync5: the same command for every ident, path and size", same)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "nextsync5.py"), encoding="utf-8").read()
    check("nextsync5: the console put uses it, from the cached ident",
          "_put_command(\n                    remote, len(put_data), "
          "_listen_state.get('ident'))" in src.replace("\r\n", "\n"))


def main():
    QCoreApplication.instance() or QCoreApplication(sys.argv)
    test_helpers()
    test_twins()
    test_wire()
    print("RESULT:", "ALL CHECKS PASSED" if not FAILS else "FAILED: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
