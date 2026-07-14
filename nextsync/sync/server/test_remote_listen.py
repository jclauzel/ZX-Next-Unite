"""Localhost test for zxnu_workers.run_remote_listen_server (the app-side
-listen server worker). A mock Next connects over a real socket and speaks the
dot's half of the protocol; we drive the worker via its command queue and check
the emitted signals."""
import os, sys, socket, threading, queue, tempfile, shutil, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from PySide6.QtCore import QCoreApplication, Qt
from zxnu_workers import RemoteExplorerSignals, run_remote_listen_server

PORT = 2049

def cs(p):
    c0 = c1 = 0
    for x in p:
        c0 = (c0 ^ x) & 0xff; c1 = (c1 + c0) & 0xff
    return c0, c1

def frame(payload, pkt=0):
    c0, c1 = cs(payload)
    return (len(payload)+5).to_bytes(2, "big") + bytes(payload) + bytes([c0, c1, pkt & 0xff])

def rx_exact(s, n):
    b = b''
    while len(b) < n:
        c = s.recv(n-len(b))
        if not c: return None
        b += c
    return b

def rx_payload(s):
    hdr = rx_exact(s, 2); total = (hdr[0] << 8) | hdr[1]
    return rx_exact(s, total-2)[:-3]

def settle():
    time.sleep(0.003)

def mock_next(sock, entries, filebytes, cap):
    sock.sendall(b"Listen")
    assert rx_payload(sock) == b"Listening"
    def push(payload, pkt):
        settle(); sock.sendall(frame(payload, pkt))
        assert rx_payload(sock)[0:1] == b'O'
    while True:
        settle(); sock.sendall(b"Poll")
        cmd = rx_payload(sock)
        op, arg = cmd[0:1], cmd[1:].decode()
        if op == b'Q': break
        if op == b'I': continue
        if op == b'L':
            pl = b'D'
            for is_dir, size, name in entries:
                pl += bytes([1 if is_dir else 0]) + int(size).to_bytes(4, "little") + bytes([len(name)]) + name.encode()
            push(pl, 0); push(b'E', 1)
        elif op == b'G':
            name = os.path.basename(arg) or "f.bin"
            push(b'N' + len(filebytes).to_bytes(4, "big") + bytes([len(name)]) + name.encode(), 0)
            push(b'D' + filebytes, 1); push(b'E', 2); push(b'B', 3)
        elif op == b'P':
            buf = b''
            while True:
                settle(); sock.sendall(b"Get")
                d = rx_payload(sock)
                if not d: break
                buf += d
            cap['put'] = (arg, buf)
        elif op in (b'M', b'R', b'X'):
            push(b'O', 0)

def main():
    app = QCoreApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="re_test_")
    getdir = os.path.join(tmp, "dl")
    putfile = os.path.join(tmp, "up.bin"); put_bytes = bytes(range(256)) * 8
    open(putfile, "wb").write(put_bytes)

    entries = [(True, 0, "GAMES"), (False, 1234, "boot.bas")]
    filebytes = b"Hello Next!\r\n" * 5
    cap = {}
    got = {'listing': None, 'got': None, 'put': None, 'ops': []}

    sig = RemoteExplorerSignals()
    sig.listing.connect(lambda p, e: got.update(listing=(p, e)), Qt.DirectConnection)
    sig.got.connect(lambda r, l: got.update(got=(r, l)), Qt.DirectConnection)
    sig.put_done.connect(lambda ok, r: got.update(put=(ok, r)), Qt.DirectConnection)
    sig.op_done.connect(lambda ok, o, p: got['ops'].append((ok, o, p)), Qt.DirectConnection)

    cmd_q = queue.Queue()
    stop = threading.Event()
    for c in [("mkdir", "/ho"), ("ls", "/"), ("get", "boot.bas", getdir),
              ("put", putfile, "/ho/"), ("rm", "/x.tap"), ("rmdir", "/y"), ("quit",)]:
        cmd_q.put(c)

    t = threading.Thread(target=run_remote_listen_server, args=(sig, cmd_q, stop, PORT), daemon=True)
    t.start()
    time.sleep(0.3)  # let it bind/accept
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    try:
        mock_next(s, entries, filebytes, cap)
    finally:
        stop.set(); t.join(timeout=5); s.close()

    ok = True
    if got['listing'] and got['listing'][0] == "/" and len(got['listing'][1]) == 2:
        print("PASS ls   :", got['listing'][1])
    else:
        print("FAIL ls   :", got['listing']); ok = False
    gp = got['got']
    if gp and os.path.isfile(gp[1]) and open(gp[1], "rb").read() == filebytes:
        print("PASS get  : wrote", gp[1])
    else:
        print("FAIL get  :", gp); ok = False
    if cap.get('put') and cap['put'][1] == put_bytes and cap['put'][0] == "/ho/up.bin":
        print("PASS put  : delivered to", cap['put'][0])
    else:
        print("FAIL put  :", cap.get('put', None), "sig:", got['put']); ok = False
    if got['put'] and got['put'][0] and got['put'][1] == "/ho/up.bin":
        print("PASS put-sig: put_done", got['put'])
    else:
        print("FAIL put-sig:", got['put']); ok = False
    if any(o == (True, "mkdir", "/ho") for o in got['ops']) and any(o[1] == "rm" for o in got['ops']):
        print("PASS ops  :", got['ops'])
    else:
        print("FAIL ops  :", got['ops']); ok = False

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
