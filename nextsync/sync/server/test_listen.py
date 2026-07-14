"""Localhost end-to-end test for the Sync4 -listen protocol.

Drives nextsync4.listen_session() (the server) over a socketpair with a mock
Next on the other end that implements the dot's half of the protocol, exactly
as nextsync/sync/z88dk/nextsync.c does. Validates ls / get / put / mkdir /
rmdir / rm framing without any hardware.
"""
import os, sys, socket, threading, tempfile, shutil, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nextsync4 as ns

if os.environ.get("TL_DEBUG"):
    _orig_sp = ns.sendpacket
    def _sp(conn, payload, pktno):
        sys.stderr.write(f"[SRV send] {bytes(payload)[:18]!r} pkt={pktno}\n"); sys.stderr.flush()
        return _orig_sp(conn, payload, pktno)
    ns.sendpacket = _sp
    def _dbg(msg):
        sys.stderr.write(msg + "\n"); sys.stderr.flush()
else:
    def _dbg(msg):
        pass


# --- framing (mirrors sendpacket / the dot's send_block) --------------------
def _cs(payload):
    c0 = c1 = 0
    for x in payload:
        c0 = (c0 ^ x) & 0xff
        c1 = (c1 + c0) & 0xff
    return c0, c1

def frame(payload, pktno=0):
    c0, c1 = _cs(payload)
    return (len(payload) + 5).to_bytes(2, "big") + bytes(payload) + bytes([c0, c1, pktno & 0xff])

def recv_exact(sock, n):
    b = b''
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c:
            return None
        b += c
    return b

def recv_payload(sock):
    """Read one framed block, return just the payload (drop cs0/cs1/pktno)."""
    hdr = recv_exact(sock, 2)
    total = (hdr[0] << 8) | hdr[1]
    rest = recv_exact(sock, total - 2)
    return rest[:-3]


# --- mock Next: the dot's side of -listen -----------------------------------
# The real transport is an ESP link that delivers each command/frame as a
# discrete +IPD message. A localhost socketpair is a raw byte stream that can
# coalesce or split those, so this mock stays strictly lockstep (one message in
# flight) and settles briefly between turns to emulate that discrete delivery.
def _settle():
    time.sleep(0.002)

def mock_next(sock, fake_entries, fake_file, captured):
    # NB: the test calls listen_session() directly, i.e. *after* the main
    # dispatch loop would have consumed the "Listen" handshake keyword - so the
    # mock does NOT send "Listen" here; it just reads the "Listening" ack that
    # listen_session() emits on entry.
    assert recv_payload(sock) == b"Listening"

    def push(payload, pkt):
        _settle()
        sock.sendall(frame(payload, pkt))
        assert recv_payload(sock)[0:1] == b'O'      # server acks "Ok"

    while True:
        _settle()
        sock.sendall(b"Poll")
        cmd = recv_payload(sock)
        _dbg(f"[MOCK recv-cmd] {cmd[:18]!r}")
        op, arg = cmd[0:1], cmd[1:].decode()
        if op == b'Q':
            break
        if op == b'I':
            continue
        if op == b'L':                              # ls: push entries then 'E'
            pkt = 0
            payload = b'D'
            for is_dir, size, name in fake_entries:
                payload += (bytes([1 if is_dir else 0]) +
                            int(size).to_bytes(4, "little") +
                            bytes([len(name)]) + name.encode())
            push(payload, pkt); pkt += 1
            push(b'E', pkt)
        elif op == b'G':                            # get: push one file then 'B'
            pkt = 0
            name = arg
            push(b'N' + len(fake_file).to_bytes(4, "big") + bytes([len(name)]) + name.encode(), pkt); pkt += 1
            push(b'D' + fake_file, pkt); pkt += 1
            push(b'E', pkt); pkt += 1
            push(b'B', pkt)
        elif op == b'P':                            # put: pull the file the server sends
            buf = b''
            while True:
                _settle()
                sock.sendall(b"Get")
                data = recv_payload(sock)
                if len(data) == 0:
                    break
                buf += data
            captured.setdefault('puts', []).append((arg, buf))
        elif op in (b'M', b'R', b'X'):              # mkdir/rmdir/rm: status OK
            push(b'O', 0)


def main():
    tmp = tempfile.mkdtemp(prefix="listen_test_")
    getdest = os.path.join(tmp, "getdest")
    putfile = os.path.join(tmp, "upload.bin")
    put_bytes = bytes(range(256)) * 10          # 2560 bytes, multi-packet
    with open(putfile, "wb") as f:
        f.write(put_bytes)

    fake_entries = [(1, 0, "GAMES"), (0, 1234, "boot.bas"), (0, 49152, "screen.scr")]
    fake_file = b"Hello from the ZX Spectrum Next!\r\n" * 4
    captured = {}

    srv, nxt = socket.socketpair()
    for s in (srv, nxt):
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
    stats = {'packets': 0, 'totalbytes': 0, 'payloadbytes': 0, 'retries': 0, 'restarts': 0, 'gee': 0}

    cmds = [
        ("mkdir", "/games/new", ""),
        ("ls", "/", ""),
        ("get", "boot.bas", getdest),
        ("put", putfile, "c:/uploads/upload.bin"),  # explicit remote name
        ("put", putfile, "/ho/"),                   # dir remote -> keep basename
        ("rm", "/games/old.tap", ""),
        ("rmdir", "/games/tmp", ""),
    ]

    t = threading.Thread(target=ns.listen_session, args=(srv, stats, cmds), daemon=True)
    t.start()
    try:
        mock_next(nxt, fake_entries, fake_file, captured)
    finally:
        t.join(timeout=5)
        srv.close(); nxt.close()

    ok = True
    # get: the fake file should have been written under getdest
    got_path = os.path.join(getdest, "boot.bas")
    if os.path.isfile(got_path) and open(got_path, "rb").read() == fake_file:
        print("PASS get   : file received and bytes match")
    else:
        print("FAIL get   : file missing or mismatched"); ok = False
    # put: the mock Next should have received the exact upload bytes
    puts = captured.get('puts', [])
    if len(puts) == 2 and all(p[1] == put_bytes for p in puts):
        print(f"PASS put   : {len(put_bytes)} bytes delivered, remotes {[p[0] for p in puts]}")
    else:
        print("FAIL put   : bytes not delivered / mismatch"); ok = False
    # a remote ending in "/" must get the local basename appended
    if len(puts) == 2 and puts[1][0] == "/ho/upload.bin":
        print("PASS put-dir: trailing-slash remote resolved to /ho/upload.bin")
    else:
        got = puts[1][0] if len(puts) == 2 else None
        print(f"FAIL put-dir: expected /ho/upload.bin, got {got!r}"); ok = False
    # the session must have run to completion (thread ended)
    if not t.is_alive():
        print("PASS session: ls/mkdir/rmdir/rm framed and completed cleanly")
    else:
        print("FAIL session: did not finish"); ok = False

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
