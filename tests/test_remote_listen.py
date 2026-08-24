"""Localhost test for zxnu_workers.run_remote_listen_server (the app-side
-listen server worker). A mock Next connects over a real socket and speaks the
dot's half of the protocol; we drive the worker via its command queue and check
the emitted signals."""
import os, sys, socket, threading, queue, tempfile, shutil, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QCoreApplication, Qt
from zxnu_http_bridge import BridgeReply
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

def fs_node(fs, path):
    """Walk the mock filesystem dict to ``path``; None if missing. Dirs are
    dicts, files are bytes."""
    node = fs
    for part in [p for p in path.split("/") if p]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node

def fs_parent(fs, path):
    """(parent dict, leaf name) for ``path``, or (None, None) if unreachable."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None, None
    node = fs
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None, None
        node = node[part]
    return (node, parts[-1]) if isinstance(node, dict) else (None, None)

def mock_next(sock, entries, filebytes, cap, fs, send_listen=True):
    # send_listen=False: play the dot for nextsync5's listen_session, which
    # is entered AFTER its main loop already consumed the "Listen" handshake
    # (used by test_http_bridge.py; the app worker consumes it itself).
    if send_listen:
        sock.sendall(b"Listen")
    assert rx_payload(sock) == b"Listening"
    def push(payload, pkt):
        settle(); sock.sendall(frame(payload, pkt))
        assert rx_payload(sock)[0:1] == b'O'
    def push_status(ok):
        push(b'O' if ok else b'F', 0)
    while True:
        settle(); sock.sendall(b"Poll")
        cmd = rx_payload(sock)
        op, arg = cmd[0:1], cmd[1:].decode()
        # esxDOS accepts an optional drive prefix ("m:/games") on every path;
        # the mock fs is drive-less, so strip it exactly like esxDOS resolves it.
        arg_np = arg[2:] if (len(arg) >= 2 and arg[1] == ":") else arg
        if op == b'Q':
            # 'Q' alone = leave listen mode (a server shutting down);
            # 'Q'+'X' = leave AND end the application (/forceexit).
            cap['quit'] = arg or ""
            break
        if op == b'I': continue
        if op == b'L':
            if arg_np.rstrip("/") == "/gone":
                push(b'F', 0)            # opendir failed on the Next: folder is gone
                continue
            if arg_np.rstrip("/").startswith("/del"):
                # rmtree playground: list from the mock fs, dirs first, with the
                # "." / ".." entries a real readdir yields (the walker must skip
                # them or it would recurse forever).
                node = fs_node(fs, arg_np)
                if not isinstance(node, dict):
                    push(b'F', 0)
                    continue
                pl = b'D'
                for name, child in [(".", {}), ("..", {})] + sorted(node.items()):
                    is_dir = isinstance(child, dict)
                    size = 0 if is_dir else len(child)
                    pl += bytes([1 if is_dir else 0]) + int(size).to_bytes(4, "little") + bytes([len(name)]) + name.encode()
                push(pl, 0); push(b'E', 1)
                continue
            pl = b'D'
            for is_dir, size, name in entries:
                pl += bytes([1 if is_dir else 0]) + int(size).to_bytes(4, "little") + bytes([len(name)]) + name.encode()
            push(pl, 0); push(b'E', 1)
        elif op == b'G':
            # A directory get streams every file with its FULL Next path, exactly
            # as the dot's send_dir does (e.g. get "/games/lev" -> "/games/lev/..").
            if arg.rstrip("/").endswith("/lev"):
                pkt = 0
                for rel, data in (("/games/lev/a.bin", b"AAAA"),
                                  ("/games/lev/sub/b.bin", b"BBBBBB")):
                    push(b'N' + len(data).to_bytes(4, "big") + bytes([len(rel)]) + rel.encode(), pkt); pkt += 1
                    push(b'D' + data, pkt); pkt += 1
                    push(b'E', pkt); pkt += 1
                push(b'B', pkt)
            else:
                name = os.path.basename(arg) or "f.bin"
                push(b'N' + len(filebytes).to_bytes(4, "big") + bytes([len(name)]) + name.encode(), 0)
                push(b'D' + filebytes, 1); push(b'E', 2); push(b'B', 3)
        elif op == b'P':
            if arg.startswith("/sys"):
                # A protecting ZXNextRemote (put_plain=0) refuses the put with
                # the MARKED 'F'+"OSP" status block so the controller can say
                # WHY; it still expects the server's "Ok" ack like any 'F'.
                settle(); sock.sendall(frame(b'FOSP', 0))
                assert rx_payload(sock)[0:1] == b'O'
                cap.setdefault('put_osp', []).append(arg)
                continue
            if arg.startswith("/locked"):
                # Simulate a put the Next can't create: push an 'F' status block
                # (like the dotN's listen_status(0)) and expect the server's "Ok".
                settle(); sock.sendall(frame(b'F', 0))
                assert rx_payload(sock)[0:1] == b'O'   # server acks the 'F' block
                cap['put_fail'] = arg
                continue
            buf = b''
            while True:
                settle(); sock.sendall(b"Get")
                d = rx_payload(sock)
                if not d: break
                buf += d
            cap['put'] = (arg, buf)
        elif op == b'V':                     # ren: arg is "old\x00new"
            # A protecting ZXNextRemote listener refuses a write whose src OR
            # dst is under a protected root with 'F' + "OSP" (0.9.0).
            if arg.split("\x00", 1)[0].startswith("/sys") or \
               arg.split("\x00", 1)[-1].startswith("/sys"):
                push(b'FOSP', 0)
            else:
                cap['ren'] = arg
                push(b'O', 0)
        elif op == b'X':
            if arg_np.startswith("/sys"):
                push(b'FOSP', 0)             # OS-protected rm refusal
            elif arg_np.startswith("/del"):
                # rm against the mock fs; "locked.txt" simulates an esxDOS
                # delete failure (read-only/open file).
                parent, name = fs_parent(fs, arg_np)
                if (parent is not None and name != "locked.txt"
                        and not isinstance(parent.get(name), dict)
                        and name in parent):
                    del parent[name]
                    push_status(1)
                else:
                    push_status(0)
            else:
                push(b'O', 0)
        elif op == b'R':
            if arg_np.startswith("/sys"):
                push(b'FOSP', 0)             # OS-protected rmdir refusal
            elif arg_np.startswith("/del"):
                # esxDOS semantics: rmdir only removes an EMPTY directory.
                parent, name = fs_parent(fs, arg_np)
                node = parent.get(name) if parent is not None else None
                if isinstance(node, dict) and len(node) == 0:
                    del parent[name]
                    push_status(1)
                else:
                    push_status(0)
            else:
                push(b'O', 0)
        elif op == b'M':
            if arg_np.startswith("/sys"):
                push(b'FOSP', 0)             # OS-protected mkdir refusal
            else:
                push(b'O', 0)
        elif op == b'W':
            # getdrives (dot v5.1+): 'O' + current drive letter + mounted letters.
            push(b'OC' + b"CM", 0)
        elif op == b'Z':
            # free space (dot v5.2+): 'O' + 4B little-endian free 512-byte
            # blocks, or 'F' when the drive can't be measured (like the dot's
            # sync_getfree failing). "E" plays the unmeasurable drive.
            if arg == "E":
                push(b'F', 0)
            else:
                push(b'O' + (2048).to_bytes(4, "little"), 0)   # 2048 blocks = 1 MB
        elif op == b'S':
            # rfsize (dot v5.2+): 'D' per directory + keepalive, then 'O' +
            # [4B files][4B dirs][4B size_lo][2B size_hi]; hi=1 exercises the
            # 48-bit reassembly (1*2^32 + lo). 'F' for the missing "/gone".
            if arg.rstrip("/") == "/gone":
                push(b'F', 0)
            else:
                push(b'D' + arg.encode(), 0)
                push(b'D', 1)
                push(b'O' + (7).to_bytes(4, "little") + (3).to_bytes(4, "little")
                     + (512).to_bytes(4, "little") + (1).to_bytes(2, "little"), 2)
        elif op == b'C':
            # rcpy (dot v5.2+): local copy on the Next. arg is "src\x00dst";
            # reply = named 'D' per file + empty keepalive + terminal 'O', or
            # 'F' for the unreadable "/locked" source.
            cap.setdefault('rcpy', []).append(arg)
            csrc, cdst = arg.split("\x00", 1)
            if csrc.startswith("/locked"):
                push(b'F', 0)
            else:
                push(b'D' + cdst.encode(), 0)
                push(b'D', 1)
                push(b'O', 2)

def main():
    app = QCoreApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="re_test_")
    getdir = os.path.join(tmp, "dl")
    foldl = os.path.join(tmp, "foldl")   # destination for a folder get
    putfile = os.path.join(tmp, "up.bin"); put_bytes = bytes(range(256)) * 8
    open(putfile, "wb").write(put_bytes)

    entries = [(True, 0, "GAMES"), (False, 1234, "boot.bas")]
    filebytes = b"Hello Next!\r\n" * 5
    # Mock Next-side filesystem for the rmtree tests: /del is a healthy nested
    # tree (with an empty folder); /del2 holds a file the Next refuses to rm.
    fs = {"del": {"a.txt": b"AA", "sub": {"b.txt": b"BB", "empty": {}}},
          "del2": {"locked.txt": b"LL"},
          "del3": {"m1.txt": b"M1", "msub": {"m2.txt": b"M2"}}}
    cap = {}
    got = {'listing': None, 'gets': [], 'put': None, 'puts': [], 'ops': [],
           'ls_failed': [], 'drives': None, 'free': [], 'fsize': [], 'hb': [],
           'osp': []}

    sig = RemoteExplorerSignals()
    sig.listing.connect(lambda p, e: got.update(listing=(p, e)), Qt.DirectConnection)
    sig.ls_failed.connect(lambda p: got['ls_failed'].append(p), Qt.DirectConnection)
    sig.got.connect(lambda r, l: got['gets'].append((r, l)), Qt.DirectConnection)
    sig.put_done.connect(lambda ok, r: (got.update(put=(ok, r)), got['puts'].append((ok, r))), Qt.DirectConnection)
    sig.op_done.connect(lambda ok, o, p: got['ops'].append((ok, o, p)), Qt.DirectConnection)
    sig.os_protected.connect(lambda o, p: got['osp'].append((o, p)), Qt.DirectConnection)
    sig.drives.connect(lambda cur, ls: got.update(drives=(cur, list(ls))), Qt.DirectConnection)
    sig.free_space.connect(lambda d, n: got['free'].append((d, n)), Qt.DirectConnection)
    sig.fsize.connect(lambda p, d: got['fsize'].append((p, d)), Qt.DirectConnection)
    sig.op_progress.connect(lambda o, n: got['hb'].append((o, n)), Qt.DirectConnection)

    cmd_q = queue.Queue()
    stop = threading.Event()
    bp = BridgeReply()   # rides the second protected put below
    # "ls /gone" sits between real commands on purpose: if the 'F' (opendir-fail)
    # reply were mishandled it would desync the stream and break everything after.
    for c in [("mkdir", "/ho"), ("ls", "/"), ("ls", "/gone"),
              ("get", "boot.bas", getdir),
              ("get", "/games/lev", foldl),
              ("put", putfile, "/ho/"),
              ("put", putfile, "/locked/up.bin"),   # put that fails with 'F'
              ("rm", "/x.tap"), ("rmdir", "/y"),
              ("rmtree", "/del"),                   # recursive delete, must empty the tree
              ("rmtree", "/del2"),                  # contains an undeletable file -> ok=False
              ("drives",),                          # getdrives: 'O' + current + letters
              ("free", "m:"),                       # free space: 'O' + 4B LE blocks
              ("free", "E"),                        # unmeasurable drive -> 'F' -> None
              ("rcpy", "/games/lev", "M:/bk/lev"),  # local Next-side copy -> ok
              ("rcpy", "/locked/t", "/t2"),         # unreadable source -> 'F'
              ("fsize", "/games/lev"),              # tree size incl. 48-bit hi
              ("fsize", "/gone"),                   # missing path -> 'F' -> None
              ("rmtree", "M:/del3"),                # drive-prefixed recursive delete
              ("rename", "/ho/a.txt", "/ho/b.txt"),
              # OS protection (0.9.0): a protecting listener refuses these with
              # 'F' + "OSP"; the worker must raise os_protected, NOT a plain
              # op_done(False), and the stream must stay in sync afterwards.
              ("mkdir", "/sys/evil"),               # protected mkdir -> OSP
              ("rm", "/sys/config/boot"),           # protected rm    -> OSP
              ("rename", "/games/x", "/sys/x"),     # protected dst   -> OSP
              ("put", putfile, "/sys/up.bin"),      # protected put -> 'F'+OSP
              ("put", putfile, "/sys/up2.bin", bp), # same, bridge flavour -> 401
              ("ls", "/"),                          # proves the stream survived
              ("quit_app",)]:
        cmd_q.put(c)

    t = threading.Thread(target=run_remote_listen_server, args=(sig, cmd_q, stop, PORT), daemon=True)
    t.start()
    time.sleep(0.3)  # let it bind/accept
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    try:
        mock_next(s, entries, filebytes, cap, fs)
    finally:
        stop.set(); t.join(timeout=5); s.close()

    ok = True
    if got['listing'] and got['listing'][0] == "/" and len(got['listing'][1]) == 2:
        print("PASS ls   :", got['listing'][1])
    else:
        print("FAIL ls   :", got['listing']); ok = False
    gp = next((g for g in got['gets'] if g[0] == "boot.bas"), None)
    if gp and os.path.isfile(gp[1]) and open(gp[1], "rb").read() == filebytes:
        print("PASS get  : wrote", gp[1])
    else:
        print("FAIL get  :", gp); ok = False
    # Folder get: files must be recreated under the fetched folder name only
    # (dest/lev/…), preserving sub-structure and NOT nesting the whole Next path
    # (no stray "games" parent).
    a = os.path.join(foldl, "lev", "a.bin")
    b = os.path.join(foldl, "lev", "sub", "b.bin")
    if (os.path.isfile(a) and open(a, "rb").read() == b"AAAA"
            and os.path.isfile(b) and open(b, "rb").read() == b"BBBBBB"
            and not os.path.exists(os.path.join(foldl, "games"))):
        print("PASS getdir: recreated lev/ tree under", foldl)
    else:
        print("FAIL getdir: a=", os.path.isfile(a), "b=", os.path.isfile(b),
              "stray games=", os.path.exists(os.path.join(foldl, "games"))); ok = False
    if cap.get('put') and cap['put'][1] == put_bytes and cap['put'][0] == "/ho/up.bin":
        print("PASS put  : delivered to", cap['put'][0])
    else:
        print("FAIL put  :", cap.get('put', None), "sig:", got['put']); ok = False
    if (True, "/ho/up.bin") in got['puts']:
        print("PASS put-sig: put_done(ok) for /ho/up.bin")
    else:
        print("FAIL put-sig:", got['puts']); ok = False
    # A put the Next rejects ('F' status) must surface as put_done(ok=False) and
    # the server must have acked the block (so cap['put_fail'] was recorded).
    if (False, "/locked/up.bin") in got['puts'] and cap.get('put_fail') == "/locked/up.bin":
        print("PASS put-fail: put_done(False) + 'F' acked for /locked/up.bin")
    else:
        print("FAIL put-fail:", got['puts'], "cap:", cap.get('put_fail')); ok = False
    if any(o == (True, "mkdir", "/ho") for o in got['ops']) and any(o[1] == "rm" for o in got['ops']):
        print("PASS ops  :", got['ops'])
    else:
        print("FAIL ops  :", got['ops']); ok = False
    if cap.get('ren') == "/ho/a.txt\x00/ho/b.txt" and any(o[1] == "rename" for o in got['ops']):
        print("PASS ren  :", cap['ren'].replace("\x00", " -> "))
    else:
        print("FAIL ren  :", cap.get('ren'), got['ops']); ok = False
    # rmtree must have deleted the whole /del tree (files first, folders
    # bottom-up: esxDOS rmdir only removes empty folders, and the mock enforces
    # that) and reported ONE op_done(True, "delete", "/del").
    if "del" not in fs and (True, "delete", "/del") in got['ops']:
        print("PASS rmtree: /del fully removed, one delete op reported")
    else:
        print("FAIL rmtree: fs=", fs, "ops:", got['ops']); ok = False
    # /del2 holds an undeletable file: the tree must survive and the job must
    # report ok=False (exactly once) without desyncing later commands (ren above).
    if (fs.get("del2") == {"locked.txt": b"LL"}
            and (False, "delete", "/del2") in got['ops']
            and sum(1 for o in got['ops'] if o[1] == "delete") == 3):
        print("PASS rmtree-fail: /del2 kept, delete reported failed")
    else:
        print("FAIL rmtree-fail: fs=", fs.get("del2"), "ops:", got['ops']); ok = False
    # getdrives: the mock reports current C with C and M mounted.
    if got['drives'] == ("C", ["C", "M"]):
        print("PASS drives: ", got['drives'])
    else:
        print("FAIL drives: ", got['drives']); ok = False
    # free space: "m:" must normalise to drive M and report 2048 blocks * 512 =
    # 1 MB; the unmeasurable "E" answers 'F' and must surface as None (and must
    # not desync the commands that follow - rmtree-drive below still passes).
    if got['free'] == [("M", 2048 * 512), ("E", None)]:
        print("PASS free : ", got['free'])
    else:
        print("FAIL free : ", got['free']); ok = False
    # rcpy: the worker frames src\0dst like rename, reads the 'D' progress +
    # terminal status, and reports op_done(ok, "copy", src) both ways; the 'F'
    # must not desync what follows (rmtree-drive below still passes).
    if (cap.get('rcpy') == ["/games/lev\x00M:/bk/lev", "/locked/t\x00/t2"]
            and (True, "copy", "/games/lev") in got['ops']
            and (False, "copy", "/locked/t") in got['ops']):
        print("PASS rcpy : ", cap['rcpy'])
    else:
        print("FAIL rcpy : ", cap.get('rcpy'), got['ops']); ok = False
    # rfsize ('S'): the totals must decode - bytes = size_hi*2^32 + size_lo =
    # 1*4294967296 + 512 (the 48-bit path) - and both signals must fire:
    # op_done(ok, "size", path) then fsize(path, data|None). The 'F' must
    # not desync what follows.
    want = {'files': 7, 'dirs': 3, 'bytes': (1 << 32) + 512}
    if (got['fsize'] == [("/games/lev", want), ("/gone", None)]
            and (True, "size", "/games/lev") in got['ops']
            and (False, "size", "/gone") in got['ops']):
        print("PASS fsize: ", got['fsize'])
    else:
        print("FAIL fsize: ", got['fsize'], got['ops']); ok = False
    # op_progress heartbeats: every 'D' block of the successful rcpy and
    # rfsize must surface as (op, name) - the NAMED one carries the item the
    # Next reported, the EMPTY one is the keepalive that pulses the UI's byte
    # estimate. The failed rcpy/rfsize send no 'D' at all.
    if got['hb'] == [("copy", "M:/bk/lev"), ("copy", ""),
                     ("size", "/games/lev"), ("size", "")]:
        print("PASS hb   : ", got['hb'])
    else:
        print("FAIL hb   : ", got['hb']); ok = False
    # A drive-prefixed rmtree must walk and delete exactly like a bare one
    # (the worker builds every child path off the "M:/del3" base).
    if "del3" not in fs and (True, "delete", "M:/del3") in got['ops']:
        print("PASS rmtree-drive: M:/del3 fully removed")
    else:
        print("FAIL rmtree-drive: fs=", fs.get("del3"), "ops:", got['ops']); ok = False
    # A missing folder must raise ls_failed (never a phantom empty listing) and
    # leave the stream in sync so the later commands above still passed.
    if got['ls_failed'] == ["/gone"]:
        print("PASS lsfail:", got['ls_failed'])
    else:
        print("FAIL lsfail:", got['ls_failed']); ok = False
    # OS protection (0.9.0): each protected write raised os_protected(op, path)
    # and NOT a plain op_done(False) — and the marker was never mistaken for a
    # normal failure. The trailing ls "/" still succeeded, proving the OSP 'F'
    # blocks did not desync the stream.
    if (got['osp'] == [("mkdir", "/sys/evil"), ("rm", "/sys/config/boot"),
                       ("rename", "/games/x"), ("put", "/sys/up.bin")]
            and not any(o[2] and str(o[2]).startswith("/sys") for o in got['ops'])):
        print("PASS osprot:", got['osp'])
    else:
        print("FAIL osprot:", got['osp'], "ops:", got['ops']); ok = False
    # A protected PUT: ZXNextRemote (put_plain=0) refuses with the MARKED
    # 'F'+OSP block. UI path: os_protected("put", path) and never a plain
    # put_done(False); bridge path: the same 401 + explanation every other
    # blocked write gets. Both blocks were acked (cap) so the far side
    # stops retrying, and the trailing ls proves the stream stayed in sync.
    if (cap.get('put_osp') == ["/sys/up.bin", "/sys/up2.bin"]
            and not any(p.startswith("/sys") for _okf, p in got['puts'])):
        print("PASS osprot-put: os_protected('put'), marked blocks acked")
    else:
        print("FAIL osprot-put:", got['osp'], got['puts'], cap.get('put_osp'))
        ok = False
    br_res = bp.wait(5)
    if (br_res and br_res.get('http') == 401
            and "os-protected" in str(br_res.get('error', ''))):
        print("PASS osprot-put-bridge: 401 os-protected reply")
    else:
        print("FAIL osprot-put-bridge:", br_res); ok = False
    if got['listing'] and got['listing'][0] == "/":
        print("PASS osprot-sync: stream survived the OSP refusals")
    else:
        print("FAIL osprot-sync:", got['listing']); ok = False
    # /forceexit sends the MARKED quit: the far side is asked to end its
    # application, not merely leave the session. Stopping the server keeps
    # sending the bare 'Q' (covered by the other tests, which all end that
    # way), because a shutdown must never take the operator's app with it.
    if cap.get('quit') == "X":
        print("PASS quitmark: /forceexit sent the marked quit ('Q'+'X')")
    else:
        print("FAIL quitmark:", repr(cap.get('quit'))); ok = False

    # ── unconnected listener must stop promptly on the stop event ──────
    # The pane's stop path skips the 10 s "Q" goodbye grace when no Next
    # is connected (there is nobody to say goodbye to and the command
    # queue is never polled while accepting) and relies on the worker's
    # accept() noticing the stop event within its 1 s poll — the Ctrl-C
    # exit time on an idle listener depends on this staying fast.
    sig2 = RemoteExplorerSignals()
    stop2 = threading.Event()
    t2 = threading.Thread(target=run_remote_listen_server,
                          args=(sig2, queue.Queue(), stop2, PORT + 1),
                          daemon=True)
    t2.start()
    time.sleep(0.3)                       # let it bind and start accepting
    t0 = time.time()
    stop2.set()
    t2.join(timeout=5)
    dt = time.time() - t0
    if not t2.is_alive() and dt < 2.5:
        print(f"PASS idle-stop: unconnected listener exited in {dt:.2f}s")
    else:
        print(f"FAIL idle-stop: alive={t2.is_alive()} after {dt:.2f}s")
        ok = False

    # ── a peer that hangs up must be NAMED, not a silent break ─────────
    # The empty-recv and OSError exits used to break without a word: the
    # session just "blinked reconnecting" and nobody knew who hung up
    # (the N-Go tree-copy report). Close the socket right after the
    # handshake and require the reason line + the disconnected signal.
    sig3 = RemoteExplorerSignals()
    logs3, disc3 = [], []
    sig3.log.connect(logs3.append, Qt.DirectConnection)
    sig3.disconnected.connect(lambda: disc3.append(1), Qt.DirectConnection)
    stop3 = threading.Event()
    t3 = threading.Thread(target=run_remote_listen_server,
                          args=(sig3, queue.Queue(), stop3, PORT + 2),
                          daemon=True)
    t3.start()
    time.sleep(0.3)
    s3 = socket.create_connection(("127.0.0.1", PORT + 2), timeout=5)
    s3.sendall(b"Listen")
    assert rx_payload(s3) == b"Listening"
    s3.close()                            # FIN with no Bye, mid-session
    t3.join(timeout=5)
    stop3.set()
    if (not t3.is_alive() and disc3
            and any("closed the connection" in ln for ln in logs3)):
        print("PASS hangup: peer FIN is named in the log + disconnected")
    else:
        print("FAIL hangup: alive=", t3.is_alive(), "disc=", disc3,
              "logs=", logs3)
        ok = False

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
