"""Localhost end-to-end test for the Sync4 -listen protocol.

Drives nextsync5.listen_session() (the server) over a socketpair with a mock
Next on the other end that implements the dot's half of the protocol, exactly
as nextsync/sync/z88dk/nextsync.c does. Validates ls / get / put / mkdir /
rmdir / rm framing without any hardware.
"""
import os, sys, socket, threading, tempfile, shutil, time, io, contextlib

# nextsync5.py lives at the repo root (next to zxnu_http_bridge.py, which it
# imports for the optional -w/-http web server).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
import nextsync5 as ns

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
            if arg.rstrip("/") == "/gone":          # missing folder: single 'F' block
                push(b'F', 0)
                continue
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
            if arg.startswith("/locked"):           # simulate a put the Next rejects
                push(b'F', 0)                        # 'F' status; server must ack 'O'
                captured['put_fail'] = arg
                continue
            buf = b''
            while True:
                _settle()
                sock.sendall(b"Get")
                data = recv_payload(sock)
                if len(data) == 0:
                    break
                buf += data
            captured.setdefault('puts', []).append((arg, buf))
        elif op == b'V':                            # ren: arg is "old\x00new"
            captured['ren'] = arg
            push(b'O', 0)
        elif op in (b'M', b'R', b'X'):              # mkdir/rmdir/rm: status
            # "/locked" fails ('F') so the FAILED-status path is exercised too.
            push(b'F' if arg.rstrip("/") == "/locked" else b'O', 0)
        elif op == b'C':                            # rcpy: local copy on the Next
            # arg is "src\x00dst". Mock the dot's reply: a named 'D' progress
            # block per "file", an empty keepalive, then 'O' - or 'F' when the
            # source is the unreadable "/locked" tree.
            captured['rcpy'] = arg
            csrc, cdst = arg.split("\x00", 1)
            if csrc.startswith("/locked"):
                push(b'F', 0)
            else:
                push(b'D' + cdst.encode(), 0)       # per-file progress
                push(b'D', 1)                       # keepalive (no name)
                push(b'O', 2)
        elif op == b'S':                            # rfsize: tree/file size
            # Named 'D' per directory + empty keepalive, then 'O' +
            # [4B files][4B dirs][4B size_lo][2B size_hi] - or 'F' for the
            # unreadable "/gone".
            if arg.rstrip("/") == "/gone":
                push(b'F', 0)
            else:
                push(b'D' + arg.encode(), 0)
                push(b'D', 1)
                push(b'O' + (3).to_bytes(4, "little") + (2).to_bytes(4, "little")
                     + (2097152).to_bytes(4, "little") + (0).to_bytes(2, "little"), 2)
        elif op == b'Z':                            # psize/pfull: free space
            # 'O' + 4B little-endian free 512-byte blocks, or 'F' when the
            # drive can't be measured (the dot's sync_getfree failing);
            # "E" plays the unmeasurable drive.
            if arg == "E":
                push(b'F', 0)
            else:
                push(b'O' + (4096).to_bytes(4, "little"), 0)   # 4096 blocks = 2 MB


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
        ("mkdir", "/locked", ""),                   # status 'F' -> FAILED reported
        ("ls", "/", ""),
        ("ls", "/gone", ""),                        # missing folder -> 'F' reply
        ("get", "boot.bas", getdest),
        ("put", putfile, "c:/uploads/upload.bin"),  # explicit remote name
        ("put", putfile, "/ho/"),                   # dir remote -> keep basename
        ("put", putfile, "/locked/up.bin"),         # put that fails with 'F'
        ("rm", "/games/old.tap", ""),
        ("rmdir", "/games/tmp", ""),
        ("psize", "m:", ""),                        # free space, exact bytes
        ("pfull", "", ""),                          # free space, human-readable
        ("psize", "E", ""),                         # unmeasurable drive -> 'F'
        ("rcpy", "/games/a.tap", "m:/backup/"),     # local copy; dst dir keeps name
        ("rcpy", "/locked/tree", "/copy2"),         # unreadable source -> 'F'
        ("rfsize", "/games", ""),                   # tree size: files/dirs/bytes
        ("rfsize", "/gone", ""),                    # missing path -> 'F'
        ("ren", "/games/a.tap", "/games/b.tap"),
    ]

    t = threading.Thread(target=ns.listen_session, args=(srv, stats, cmds), daemon=True)
    # Capture the server's stdout so we can assert the missing-folder message; it
    # is echoed back afterwards so the run stays visible.
    srv_log = io.StringIO()
    with contextlib.redirect_stdout(srv_log):
        t.start()
        try:
            mock_next(nxt, fake_entries, fake_file, captured)
        finally:
            t.join(timeout=5)
            srv.close(); nxt.close()
    server_out = srv_log.getvalue()
    print(server_out, end="")

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
    # ren: the server should have framed old+new NUL-separated in one command
    if captured.get('ren') == "/games/a.tap\x00/games/b.tap":
        print("PASS ren   :", captured['ren'].replace("\x00", " -> "))
    else:
        print(f"FAIL ren   : {captured.get('ren')!r}"); ok = False
    # a missing folder must be reported (the 'F' reply), not silently swallowed;
    # that it landed mid-stream and every later command still passed proves the
    # 'F' block was consumed without desyncing the session.
    if "ls /gone: no such directory" in server_out:
        print("PASS lsfail: missing folder reported, stream stayed in sync")
    else:
        print("FAIL lsfail: 'F' reply not handled"); ok = False
    # A failing status command ('F') must be called out with its path context.
    if "mkdir /locked: FAILED" in server_out:
        print("PASS statusF: mkdir 'F' reported with context")
    else:
        print("FAIL statusF: status 'F' not reported"); ok = False
    # psize/pfull ('Z'): "m:" must normalise to M and report exact bytes
    # (4096 blocks * 512 = 2097152); pfull shows the same figure human-readable
    # for the current drive; the unmeasurable "E" answers 'F' and must be
    # called out FAILED (and consumed - ren after it still passed).
    if "psize M: 2097152 bytes free" in server_out:
        print("PASS psize : exact free bytes reported for M")
    else:
        print("FAIL psize : missing/wrong psize output"); ok = False
    if "pfull current drive: 2.0 MB free" in server_out:
        print("PASS pfull : human-readable free space for current drive")
    else:
        print("FAIL pfull : missing/wrong pfull output"); ok = False
    if "psize E: FAILED on the Next" in server_out:
        print("PASS psizeF: 'F' reply reported as FAILED")
    else:
        print("FAIL psizeF: 'F' reply not reported"); ok = False
    # rcpy ('C'): the trailing-slash dst must have kept the source name, the
    # progress 'D' must be echoed, and the whole run reported OK with a count.
    if (captured.get('rcpy') == "/locked/tree\x00/copy2"
            and "copying m:/backup/a.tap" in server_out
            and "rcpy /games/a.tap -> m:/backup/a.tap: OK (1 file(s))" in server_out):
        print("PASS rcpy  : dst-name kept, progress echoed, OK reported")
    else:
        print("FAIL rcpy  :", captured.get('rcpy')); ok = False
    # rcpy of an unreadable source answers 'F' and must be called out FAILED
    # (and consumed - the ren after it still passed).
    if "rcpy /locked/tree -> /copy2: FAILED on the Next" in server_out:
        print("PASS rcpyF : 'F' reply reported as FAILED")
    else:
        print("FAIL rcpyF : 'F' reply not reported"); ok = False
    # rfsize ('S'): the terminal totals must decode (incl. the 48-bit split)
    # and the per-directory progress must be echoed.
    if ("scanning /games" in server_out
            and "rfsize /games: 3 file(s), 2 folder(s), 2,097,152 bytes (2.0 MB)" in server_out):
        print("PASS rfsize: totals decoded, progress echoed")
    else:
        print("FAIL rfsize: missing/wrong rfsize output"); ok = False
    if "rfsize /gone: FAILED on the Next" in server_out:
        print("PASS rfsizeF: 'F' reply reported as FAILED")
    else:
        print("FAIL rfsizeF: 'F' reply not reported"); ok = False
    # A put the Next rejects ('F') must be reported (and the block acked, or the
    # mock's push() assert would have failed and torn the session down).
    if "put /locked/up.bin: FAILED" in server_out and captured.get('put_fail') == "/locked/up.bin":
        print("PASS putF   : put 'F' reported + acked")
    else:
        print("FAIL putF   :", captured.get('put_fail')); ok = False
    # the session must have run to completion (thread ended)
    if not t.is_alive():
        print("PASS session: ls/mkdir/rmdir/rm/ren framed and completed cleanly")
    else:
        print("FAIL session: did not finish"); ok = False

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
