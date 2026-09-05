"""Localhost test for zxnu_workers.run_remote_listen_server (the app-side
-listen server worker). A mock Next connects over a real socket and speaks the
dot's half of the protocol; we drive the worker via its command queue and check
the emitted signals."""
import os, sys, socket, threading, queue, tempfile, shutil, time
import zlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QCoreApplication, Qt
from zxnu_http_bridge import BridgeReply
import zxnu_workers
from zxnu_workers import (RemoteExplorerSignals, run_remote_listen_server,
                          re_peer_answers_crc, re_verify_wait)

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
        if op != b'I':
            cap.setdefault('ops', []).append((op.decode(), arg))
        if op == b'Q':
            # 'Q' alone = leave listen mode (a server shutting down);
            # 'Q'+'X' = leave AND end the application (/forceexit).
            cap['quit'] = arg or ""
            # A <= 5.7.4 dot answers the 'Q' with the classic raw "Bye" and
            # waits for "Later" before closing; the goodbye linger must
            # answer it or the dot burns its full cipxfer timeout staring
            # at silence - the long "Closing.." hang on quit/forceexit.
            settle()
            sock.sendall(b"Bye")
            sock.settimeout(5.0)
            try:
                cap['bye_answer'] = rx_payload(sock)
            except (socket.timeout, OSError):
                cap['bye_answer'] = None
            # Close OUR end at once: the linger's follow-up drain then hits
            # its EOF arm immediately (the tested path a 5.7.5 dot's clean
            # close also takes) instead of eating its full 2 s timeout.
            try:
                sock.close()
            except OSError:
                pass
            break
        if op == b'I': continue
        if op == b'L':
            if arg_np.rstrip("/") == "/gone":
                push(b'F', 0)            # opendir failed on the Next: folder is gone
                continue
            if arg_np.rstrip("/").startswith("/sys"):
                # Read+write OS protection refuses the LISTING with the MARKED
                # 'F'+"OSP" (fsrv.c) - distinct from a plain miss so the worker
                # raises os_protected / relays 401, not "folder gone" / 502.
                push(b'FOSP', 0)
                cap.setdefault('ls_osp', []).append(arg)
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
            if arg.startswith("/sys"):
                # Read-protected source: a get has no failure frame, so a ZXNR
                # listener LEADS the empty walk with the marked 'F'+OSP purely so
                # the refusal is nameable, then the 'B' every peer expects.
                push(b'FOSP', 0); push(b'B', 1)
                cap.setdefault('get_osp', []).append(arg)
                continue
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
            stored = buf
            if arg_np.startswith("/corrupt") and buf:
                stored = buf[:-1] + bytes([buf[-1] ^ 0xFF])   # a bad SD write
            cap.setdefault('files', {})[arg] = stored
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
            elif arg_np.startswith("/corrupt/locked"):
                push_status(0)               # a delete the Next refuses
            elif arg_np.startswith("/corrupt/osp"):
                push(b'FOSP', 0)             # ZXNR OS protection refuses it
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
        elif op == b'Y':
            # version query (ZXNR 1.0.2+ / dot v5.8+): one status block,
            # 'O' + type + NUL + build number.
            ident = cap.get('ident', b'Osync' + bytes([0]) + b'9.9.9')
            if ident is None:
                continue                     # a pre-5.8 listener ignores 'Y' and re-polls
            push(ident, 0)
        elif op == b'K':
            # crc (dot v5.9.2+ / ZXNR 1.0.8+): 'O' + 8 upper-case hex digits
            # of the file's CRC-32 (zlib.crc32's value), 'F' when it did not
            # open. The digits here are the CRC of the PATH, so a test can
            # predict them without a file table.
            if cap.get('k_silent'):
                continue                     # a listener that ignores 'K' re-polls
            if arg.rstrip("/") == "/gone" or arg_np.startswith("/unv"):
                push(b'F', 0)
            elif arg_np.startswith("/ospread"):
                push(b'FOSP', 0)             # ZXNR read-side OS protection
            elif arg_np.startswith("/die"):
                sock.close(); break          # the link drops mid-check
            elif arg in cap.get('files', {}):
                push(b'O' + ("%08X" % (zlib.crc32(cap['files'][arg]) & 0xffffffff)).encode(), 0)
                if arg_np.startswith("/corrupt/hangup"):
                    sock.close(); break      # hangs up before the 'X' can be served
            else:
                push(b'O' + ("%08X" % (zlib.crc32(arg.encode()) & 0xffffffff)).encode(), 0)
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

def mock_update_next(sock, ops, staged, scenario, verify_bytes):
    """Play the dot's half of an ("update_dot", ...) macro session. Records
    every command the wire carries into ``ops`` as (op, arg) — 'I' idle
    answers excluded, they are the worker saying "nothing queued" — and each
    staging put into ``staged`` as (path, bytes). ``verify_bytes`` is what
    the 'G' read-back verify serves (the corrupt scenario hands back
    something other than what was staged); ``scenario`` == "no_release"
    refuses the 'U' handle-release with 'F' (a pre-5.9 dot),
    "ren1_refuse" answers the first rename's 'V' with 'F', "ren1_osp"
    answers it with the marked 'F'+"OSP" (ZXNR's OS protection), and the
    two "kill_after_*" scenarios drop the link right after acking the 'U' /
    the first 'V' (a session dying mid-macro)."""
    sock.sendall(b"Listen")
    assert rx_payload(sock) == b"Listening"
    def push(payload, pkt):
        settle(); sock.sendall(frame(payload, pkt))
        assert rx_payload(sock)[0:1] == b'O'
    while True:
        settle(); sock.sendall(b"Poll")
        cmd = rx_payload(sock)
        op, arg = cmd[0:1], cmd[1:].decode()
        if op == b'I':
            continue
        ops.append((op.decode(), arg))
        if op == b'Q':
            # Same goodbye dance as mock_next: answer with the <= 5.7.4
            # raw "Bye", swallow the "Later", close our end.
            settle(); sock.sendall(b"Bye")
            sock.settimeout(5.0)
            try:
                rx_payload(sock)
            except (socket.timeout, OSError):
                pass
            try:
                sock.close()
            except OSError:
                pass
            break
        if op == b'P':
            buf = b''
            while True:
                settle(); sock.sendall(b"Get")
                d = rx_payload(sock)
                if not d: break
                buf += d
            staged.append((arg, buf))
        elif op == b'G':
            # The read-back verify pull: 'D' data blocks then the 'B' end.
            pkt = 0
            for i in range(0, len(verify_bytes), 500):
                push(b'D' + verify_bytes[i:i + 500], pkt); pkt += 1
            push(b'B', pkt)
        elif op == b'X':
            # sync5.bak missing on a first-ever update answers 'F' — the
            # macro must shrug and carry on; the cleanup rm of the staged
            # sync5.new succeeds.
            push(b'F' if arg.endswith(".bak") else b'O', 0)
        elif op == b'U':
            push(b'F' if scenario == "no_release" else b'O', 0)
            if scenario == "kill_after_u":
                # Session dies right after the release is acked: the
                # worker's finally block owes the exactly-once verdict.
                sock.close()
                break
        elif op == b'V':
            ren1 = "\x00" in arg and arg.split("\x00", 1)[1].endswith(".bak")
            if ren1 and scenario == "ren1_osp":
                push(b'FOSP', 0)        # refused by the OS protection (marked)
            else:
                push(b'F' if (scenario == "ren1_refuse" and ren1) else b'O', 0)
            if scenario == "kill_after_ren1" and ren1:
                sock.close()
                break
        else:
            push(b'F', 0)                # unexpected op: refuse loudly

def run_update_scenario(port, cmds, scenario, verify_bytes):
    """Fresh worker + mock Next for one update_dot scenario. Returns
    (ops, staged, upd, puts): the wire ops seen, the staged put(s), every
    dot_update emission and every (stray) put_done emission."""
    sig = RemoteExplorerSignals()
    upd, puts = [], []
    sig.dot_update.connect(lambda okf, msg: upd.append((okf, msg)), Qt.DirectConnection)
    sig.put_done.connect(lambda okf, r: puts.append((okf, r)), Qt.DirectConnection)
    q = queue.Queue()
    for c in cmds:
        q.put(c)
    stop = threading.Event()
    t = threading.Thread(target=run_remote_listen_server,
                         args=(sig, q, stop, port),
                         # verify ON: the exact want_ops equality below pins
                         # that the staging put never draws a 'K' (9.7.3)
                         kwargs={"verify_crc": lambda: True}, daemon=True)
    t.start()
    time.sleep(0.3)                       # let it bind/accept
    ops, staged = [], []
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        mock_update_next(s, ops, staged, scenario, verify_bytes)
    finally:
        stop.set(); t.join(timeout=5); s.close()
    return ops, staged, upd, puts

def run_verify_scenario(port, cmds, cap=None, verify=lambda: True):
    """Fresh worker + mock_next for one verify-after-put scenario (9.7.3).
    Returns (cap, events): cap['ops'] is the wire ('I' excluded), events the
    ORDERED emissions - ('put', ok, remote), ('red', message), ('log', line)."""
    sig = RemoteExplorerSignals()
    events = []
    sig.put_done.connect(lambda okf, r: events.append(('put', okf, r)), Qt.DirectConnection)
    sig.put_verify_failed.connect(lambda m: events.append(('red', m)), Qt.DirectConnection)
    sig.log.connect(lambda s: events.append(('log', s)), Qt.DirectConnection)
    q = queue.Queue()
    for c in cmds:
        q.put(c)
    stop = threading.Event()
    t = threading.Thread(target=run_remote_listen_server, args=(sig, q, stop, port),
                         kwargs={"verify_crc": verify}, daemon=True)
    t.start()
    time.sleep(0.3)
    cap = {} if cap is None else cap
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        mock_next(s, [], b"", cap, {})
    finally:
        stop.set(); t.join(timeout=5); s.close()
    return cap, events

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
    ls_osp_bp = BridgeReply()  # the bridge-flavour protected LISTING (9.7.4)
    get_osp_bp = BridgeReply()  # the bridge-flavour protected GET (9.7.4)
    crc_ok = BridgeReply()   # the crc answers ride reply sinks (bridge shape)
    crc_no = BridgeReply()
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
              ("crc", "/games/a.tap", crc_ok),      # CRC-32 ON the Next: 'O' + 8 hex
              ("crc", "/gone", crc_no),             # missing file -> 'F' -> not ok
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
              # Read-side OS protection (9.7.4): a listing or a get refused by
              # the far side's Read+write protection answers the marked 'F'+OSP.
              # It must raise os_protected (UI) / relay 401 (bridge), NEVER the
              # "missing folder?" 502 that made a protected browse unreadable.
              ("ls", "/sys"),                       # protected listing -> OSP
              ("ls", "/sys", ls_osp_bp),            # same, bridge flavour -> 401
              ("get", "/sys/secret.bin", getdir),   # protected get -> OSP
              ("get", "/sys/secret.bin", getdir, get_osp_bp),  # bridge -> 401
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
    # ── pure gates of the verify-after-put (9.7.3) ─────────────────────
    _yes = [('sync', '5.9.2'), ('sync', '5.10.0'), ('sync', '5.9.10'),
            ('httpbridge', '1.0.8'), ('n2n', '1.0.9'), (' SYNC ', '5.9.2')]
    _no = [('sync', '5.9.1'), ('n2n', '1.0.7'), ('', ''), ('sync', ''),
           ('sync', 'x.y'), ('other', '9.9.9'), (None, None)]
    if (all(re_peer_answers_crc(*p) for p in _yes)
            and not any(re_peer_answers_crc(*p) for p in _no)):
        print("PASS vcrc-gate: dot >= 5.9.2 / ZXNR >= 1.0.8 answer 'K', nothing else")
    else:
        print("FAIL vcrc-gate:", [(p, re_peer_answers_crc(*p)) for p in _yes + _no]); ok = False
    if (re_verify_wait(0) == 60.0 and re_verify_wait(1_500_000) == 160.0
            and re_verify_wait(10**12) == 3600.0):
        print("PASS vcrc-wait: 60 s + size/15 KB/s, capped at an hour")
    else:
        print("FAIL vcrc-wait:", re_verify_wait(0), re_verify_wait(1_500_000),
              re_verify_wait(10**12)); ok = False
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
    # verify_crc left at its None default: the worker's behaviour is
    # byte-identical to 9.7.2 - no 'K' follows a put (9.7.3).
    if ('K', '/ho/up.bin') not in cap.get('ops', []):
        print("PASS put-noverify: no 'K' after the put with verify_crc=None")
    else:
        print("FAIL put-noverify:", cap.get('ops')); ok = False
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
    # crc ('K', dot v5.9.2+ / ZXNR 1.0.8+): 'O' + 8 upper-case hex digits to
    # the reply sink, 'F' reported as a failure, the stream intact after.
    _want_crc = "%08X" % (zlib.crc32(b"/games/a.tap") & 0xffffffff)
    _c_ok = crc_ok.wait(5)
    _c_no = crc_no.wait(5)
    if (_c_ok and _c_ok.get('ok') and _c_ok.get('crc32') == _want_crc
            and _c_no and not _c_no.get('ok')):
        print("PASS crc  : ", _c_ok.get('crc32'))
    else:
        print("FAIL crc  : ", _c_ok, _c_no); ok = False
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
    # OS protection: each protected write raised os_protected(op, path) and NOT
    # a plain op_done(False) — and, since 9.7.4, the READ verbs (ls of a
    # protected folder, get of a protected source) do the same instead of
    # reading as "folder gone" / an empty pull. The trailing ls "/" still
    # succeeded, proving the OSP 'F' blocks did not desync the stream.
    if (got['osp'] == [("mkdir", "/sys/evil"), ("rm", "/sys/config/boot"),
                       ("rename", "/games/x"), ("put", "/sys/up.bin"),
                       ("ls", "/sys"), ("get", "/sys/secret.bin")]
            and not any(o[2] and str(o[2]).startswith("/sys") for o in got['ops'])
            and "/sys" not in [p for p in got['ls_failed']]):
        print("PASS osprot:", got['osp'])
    else:
        print("FAIL osprot:", got['osp'], "ops:", got['ops']); ok = False
    # Read-side OS protection over the BRIDGE (9.7.4): a protected listing and
    # a protected get each relay the same 401 + os-protected body a blocked
    # write does — the "missing folder?" 502 (or a phantom empty listing) is
    # exactly the mislabelling this fixes. The mock saw both the UI and the
    # bridge attempt of each (cap), proving the marked block was acked and the
    # stream stayed in sync for the trailing ls "/".
    ls_bres = ls_osp_bp.wait(5)
    get_bres = get_osp_bp.wait(5)
    if (ls_bres and ls_bres.get('http') == 401
            and "os-protected" in str(ls_bres.get('error', ''))
            and get_bres and get_bres.get('http') == 401
            and cap.get('ls_osp') == ["/sys", "/sys"]
            and cap.get('get_osp') == ["/sys/secret.bin", "/sys/secret.bin"]):
        print("PASS osprot-read-bridge: ls & get -> 401 os-protected")
    else:
        print("FAIL osprot-read-bridge:", ls_bres, get_bres,
              cap.get('ls_osp'), cap.get('get_osp')); ok = False
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
    # The goodbye linger must answer a <= 5.7.4 dot's post-quit "Bye" with
    # the framed "Later" (unanswered, the dot stared at silence for its
    # full cipxfer timeout before closing - the "Closing.." hang on quit).
    if cap.get('bye_answer') == b"Later":
        print("PASS byeAns : post-quit Bye answered with Later")
    else:
        print("FAIL byeAns :", repr(cap.get('bye_answer'))); ok = False

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

    # ── remote .sync5 self-update macro ("update_dot") ─────────────────
    # The blob embeds the version banner the macro insists on, and is big
    # enough (~1.4 KB) that the staging put and the verify pull both span
    # several 512-byte frames.
    upd_blob = b"\x00\x01" * 50 + b"NextSync 5.9.0" + bytes(range(256)) * 5
    upd_file = os.path.join(tmp, "sync5.bin")
    open(upd_file, "wb").write(upd_blob)

    # Happy path: stage put -> read-back verify -> 'U' release -> rm
    # sync5.bak ('F' = missing, must NOT abort) -> the two renames -> the
    # macro's own TARGETED quit — in that exact order, every path fully
    # qualified under the given remote dir. The 'U' comes BEFORE the .bak
    # delete on purpose: a pre-5.9 dot refuses it, and failing there must
    # leave nothing of the user's deleted. dot_update fires once with
    # ok=True; the staging put must never leak a put_done.
    ops, staged, upd, puts = run_update_scenario(
        PORT + 3, [("update_dot", upd_file, "c:/dot", "5.9.0")],
        "ok", upd_blob)
    want_ops = [('P', "c:/dot/sync5.new"),
                ('G', "c:/dot/sync5.new"),
                ('U', ""),
                ('X', "c:/dot/sync5.bak"),
                ('V', "c:/dot/sync5\x00c:/dot/sync5.bak"),
                ('V', "c:/dot/sync5.new\x00c:/dot/sync5"),
                ('Q', "")]
    if (ops == want_ops and staged == [("c:/dot/sync5.new", upd_blob)]
            and len(upd) == 1 and upd[0][0] and not puts):
        print("PASS updot: staged+verified+swapped in order, dot_update(True) once")
    else:
        print("FAIL updot: ops=", ops, "staged=",
              [(p, len(b)) for p, b in staged], "upd=", upd, "puts=", puts)
        ok = False

    # Verify mismatch: the staged copy reads back corrupted. The macro must
    # clean up the stage (rm sync5.new), never send the 'U' release — the
    # dot's file handle stays untouched — and report dot_update(ok=False).
    # The trailing 'Q' is the follow-up ("quit",), not the macro's.
    ops, staged, upd, puts = run_update_scenario(
        PORT + 4, [("update_dot", upd_file, "c:/dot", "5.9.0"), ("quit",)],
        "ok", upd_blob[:-1] + bytes([upd_blob[-1] ^ 0xFF]))
    if (ops == [('P', "c:/dot/sync5.new"), ('G', "c:/dot/sync5.new"),
                ('X', "c:/dot/sync5.new"), ('Q', "")]
            and not any(o == 'U' for o, _a in ops)
            and len(upd) == 1 and not upd[0][0] and not puts):
        print("PASS updot-corrupt: cleanup rm, no 'U', dot_update(False)")
    else:
        print("FAIL updot-corrupt: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # Release refused: a pre-5.9 dot answers the 'U' with 'F'. Because the
    # 'U' now runs BEFORE the rm of sync5.bak, failing here must have
    # deleted NOTHING of the user's — no 'X' of sync5.bak may ever have
    # gone out. The macro still cleans up its own stage (rm sync5.new),
    # never attempts a rename, and the session SURVIVES (release failure
    # means not released): the trailing 'Q' is the follow-up ("quit",),
    # served after the macro reported dot_update(ok=False).
    ops, staged, upd, puts = run_update_scenario(
        PORT + 5, [("update_dot", upd_file, "c:/dot", "5.9.0"), ("quit",)],
        "no_release", upd_blob)
    if (ops == [('P', "c:/dot/sync5.new"), ('G', "c:/dot/sync5.new"),
                ('U', ""), ('X', "c:/dot/sync5.new"), ('Q', "")]
            and ('X', "c:/dot/sync5.bak") not in ops
            and not any(o == 'V' for o, _a in ops)
            and len(upd) == 1 and not upd[0][0] and not puts):
        print("PASS updot-norel: 'U' refused -> cleanup only, no .bak rm, "
              "no 'V', session survives, dot_update(False)")
    else:
        print("FAIL updot-norel: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # Post-'U' discipline: the release succeeded but the FIRST rename was
    # refused ('V' answered 'F'). Nothing moved — sync5 is untouched — but
    # the dot's file handle is gone, so the macro must report ONE
    # dot_update(ok=False) (the "nothing swapped" wording, no mid-swap
    # scare) and end the session itself with a targeted 'Q': the queued
    # follow-up rm must never be served.
    ops, staged, upd, puts = run_update_scenario(
        PORT + 7, [("update_dot", upd_file, "c:/dot", "5.9.0"),
                   ("rm", "/never.txt")],
        "ren1_refuse", upd_blob)
    if (ops == [('P', "c:/dot/sync5.new"), ('G', "c:/dot/sync5.new"),
                ('U', ""), ('X', "c:/dot/sync5.bak"),
                ('V', "c:/dot/sync5\x00c:/dot/sync5.bak"), ('Q', "")]
            and ('X', "/never.txt") not in ops
            and len(upd) == 1 and not upd[0][0]
            and "could not rename" in upd[0][1]
            and "may be missing" not in upd[0][1] and not puts):
        print("PASS updot-ren1F: refused ren1 -> dot_update(False) once, "
              "targeted 'Q', no further ops served")
    else:
        print("FAIL updot-ren1F: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # Session killed mid-macro BEFORE any rename: the mock acks the 'U'
    # then drops the link. The loop never reaches a terminal arm, so the
    # finally block must emit the exactly-once dot_update(False) — with
    # the "nothing swapped" wording, since no 'V' ever went out.
    ops, staged, upd, puts = run_update_scenario(
        PORT + 8, [("update_dot", upd_file, "c:/dot", "5.9.0")],
        "kill_after_u", upd_blob)
    if (ops == [('P', "c:/dot/sync5.new"), ('G', "c:/dot/sync5.new"),
                ('U', "")]
            and len(upd) == 1 and not upd[0][0]
            and "may be missing" not in upd[0][1] and not puts):
        print("PASS updot-killU: mid-macro death -> one dot_update(False), "
              "no mid-swap scare")
    else:
        print("FAIL updot-killU: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # Session killed mid-SWAP: the mock acks the first rename then drops
    # the link. The finally block owes the one dot_update(False) and it
    # must carry the mid-swap recovery wording (a 'V' HAD been sent, so
    # the card's state is unknown and .bak is the way back).
    ops, staged, upd, puts = run_update_scenario(
        PORT + 9, [("update_dot", upd_file, "c:/dot", "5.9.0")],
        "kill_after_ren1", upd_blob)
    if (ops == [('P', "c:/dot/sync5.new"), ('G', "c:/dot/sync5.new"),
                ('U', ""), ('X', "c:/dot/sync5.bak"),
                ('V', "c:/dot/sync5\x00c:/dot/sync5.bak")]
            and len(upd) == 1 and not upd[0][0]
            and "may be missing" in upd[0][1] and not puts):
        print("PASS updot-killV: mid-swap death -> one dot_update(False) "
              "with the recovery wording")
    else:
        print("FAIL updot-killV: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # Banner refusal: a local file without the expected "NextSync <ver>"
    # banner is the wrong (or stale) build — refuse before a single byte
    # moves. The only wire op is the follow-up quit's 'Q'.
    nb_file = os.path.join(tmp, "not-sync5.bin")
    open(nb_file, "wb").write(b"something else entirely")
    ops, staged, upd, puts = run_update_scenario(
        PORT + 6, [("update_dot", nb_file, "c:/dot", "5.9.0"), ("quit",)],
        "ok", upd_blob)
    if (ops == [('Q', "")] and not staged
            and len(upd) == 1 and not upd[0][0] and not puts):
        print("PASS updot-banner: refused locally, nothing sent for the job")
    else:
        print("FAIL updot-banner: ops=", ops, "staged=",
              [(p, len(b)) for p, b in staged], "upd=", upd, "puts=", puts)
        ok = False

    # ZXNR flavor: the generalized macro updates a ZX Next Remote .nex.
    # cmd = ("update_dot", local, dir, ver, base_file, brand, marked_exit):
    # every step path derives from the BASE file name, the brand + version
    # are verified as SEPARATE substrings (ZXNR's title and version
    # literals sit apart in the binary), the 'U' release is answered 'O',
    # and the macro's final quit is the MARKED one ('Q'+'X') — the .nex
    # saves its settings and soft-resets the Next into NextZXOS, where the
    # swapped build relaunches.
    zx_blob = (b"Next\x00" + bytes(range(256)) * 5
               + b"ZXNextRemote\x00" + b"9.9.9\x00tail")
    zx_file = os.path.join(tmp, "zxnextremote-n2n.nex")
    open(zx_file, "wb").write(zx_blob)
    ops, staged, upd, puts = run_update_scenario(
        PORT + 10, [("update_dot", zx_file, "c:/apps", "9.9.9",
                     "zxnextremote-n2n.nex", "ZXNextRemote", True)],
        "ok", zx_blob)
    zb = "c:/apps/zxnextremote-n2n.nex"
    if (ops == [('P', zb + ".new"), ('G', zb + ".new"), ('U', ""),
                ('X', zb + ".bak"), ('V', zb + "\x00" + zb + ".bak"),
                ('V', zb + ".new\x00" + zb), ('Q', "X")]
            and staged == [(zb + ".new", zx_blob)]
            and len(upd) == 1 and upd[0][0]
            and "soft-reset" in upd[0][1] and not puts):
        print("PASS updot-zxnr: .nex-base paths, 'U' ok, marked quit "
              "('Q'+'X'), dot_update(True) once")
    else:
        print("FAIL updot-zxnr: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # ...and a first rename the far side's OS PROTECTION refuses (the
    # marked 'F'+"OSP" — ZXNR protects apps/, dot/, sys/ by default):
    # nothing moved, but the verdict must NAME the protection, or the user
    # hunts a phantom SD error. Post-'U' discipline still ends the session
    # with a targeted quit — PLAIN 'Q', failure quits are never marked.
    ops, staged, upd, puts = run_update_scenario(
        PORT + 11, [("update_dot", zx_file, "c:/apps", "9.9.9",
                     "zxnextremote-n2n.nex", "ZXNextRemote", True)],
        "ren1_osp", zx_blob)
    if (ops[-2:] == [('V', zb + "\x00" + zb + ".bak"), ('Q', "")]
            and len(upd) == 1 and not upd[0][0]
            and "OS protection" in upd[0][1]
            and "may be missing" not in upd[0][1] and not puts):
        print("PASS updot-osp: ren1 'F'+OSP -> dot_update(False) names the "
              "OS protection, plain targeted 'Q'")
    else:
        print("FAIL updot-osp: ops=", ops, "upd=", upd, "puts=", puts)
        ok = False

    # ── verify-after-put (Settings → Verify CRC, 9.7.3) ────────────────
    # A UI put is followed by the worker's own 'K' exchange as a local_cmds
    # continuation, gated on the session's 'Y' ident (self-probed when nobody
    # asked); a DIFFERENT digest deletes the corrupted copy ('X') and reports
    # the red verdict THEN put_done(False); every doubt ('F', 'F'+OSP,
    # silence, an old listener) keeps the file and reports put_done(True).
    # One put_done per put, in every path - session death included.
    want = "%08X" % (zlib.crc32(put_bytes) & 0xffffffff)
    bad = "%08X" % (zlib.crc32(put_bytes[:-1] + bytes([put_bytes[-1] ^ 0xFF])) & 0xffffffff)
    zxnu_workers.RE_VERIFY_WAIT_FLOOR = 2.0   # read at call time; nothing here waits it out

    def _split(events):
        return ([(e[1], e[2]) for e in events if e[0] == 'put'],
                [e[1] for e in events if e[0] == 'red'],
                [e[1] for e in events if e[0] == 'log'])

    def _order(events):
        # index of the first 'red' and the first 'put' emission
        r = next((i for i, e in enumerate(events) if e[0] == 'red'), None)
        p = next((i for i, e in enumerate(events) if e[0] == 'put'), None)
        return r, p

    # A: verified - the worker self-probes 'Y' (nobody asked), then 'K'.
    vcap, ev = run_verify_scenario(PORT + 12, [("put", putfile, "/ho/"), ("quit",)])
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Y', ''), ('K', '/ho/up.bin'), ('Q', '')]
            and puts == [(True, '/ho/up.bin')] and reds == []
            and "crc32 /ho/up.bin: verifying 2048 bytes on the Next…" in logs
            and f"crc32 /ho/up.bin: {want} — verified" in logs):
        print("PASS vcrc-ok: P, self-probed Y, K, verified, one put_done(True)")
    else:
        print("FAIL vcrc-ok: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # B: the copy on the Next differs -> 'X', red verdict, put_done(False).
    vcap, ev = run_verify_scenario(PORT + 13, [("put", putfile, "/corrupt/"), ("quit",)])
    puts, reds, logs = _split(ev)
    ri, pi = _order(ev)
    if (vcap.get('ops') == [('P', '/corrupt/up.bin'), ('Y', ''), ('K', '/corrupt/up.bin'),
                            ('X', '/corrupt/up.bin'), ('Q', '')]
            and puts == [(False, '/corrupt/up.bin')]
            and len(reds) == 1 and "/corrupt/up.bin" in reds[0] and want in reds[0]
            and bad in reds[0] and "has been deleted" in reds[0]
            and ri is not None and pi is not None and ri < pi
            and any("MISMATCH" in ln for ln in logs)):
        print("PASS vcrc-bad: mismatch -> X, red verdict, then put_done(False)")
    else:
        print("FAIL vcrc-bad: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "order=", (ri, pi), "logs=", logs); ok = False

    # C: mismatch, but the Next refuses the delete ('F' on 'X').
    vcap, ev = run_verify_scenario(PORT + 14, [("put", putfile, "/corrupt/locked.bin"), ("quit",)])
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/corrupt/locked.bin'), ('Y', ''),
                            ('K', '/corrupt/locked.bin'), ('X', '/corrupt/locked.bin'), ('Q', '')]
            and puts == [(False, '/corrupt/locked.bin')]
            and len(reds) == 1 and "could NOT be deleted" in reds[0]
            and "the Next refused the delete" in reds[0]):
        print("PASS vcrc-badlock: refused delete -> red 'could NOT be deleted', put_done(False)")
    else:
        print("FAIL vcrc-badlock: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds); ok = False

    # D: mismatch, the delete refused by ZXNR's OS protection ('F'+OSP on 'X').
    vcap, ev = run_verify_scenario(PORT + 15, [("put", putfile, "/corrupt/osp.bin"), ("quit",)])
    puts, reds, logs = _split(ev)
    if (puts == [(False, '/corrupt/osp.bin')] and len(reds) == 1
            and "OS protection refused the delete" in reds[0]):
        print("PASS vcrc-badosp: OSP-refused delete named in the red verdict")
    else:
        print("FAIL vcrc-badosp: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds); ok = False

    # E: 'F' on 'K' (the file did not open) is doubt, not corruption.
    vcap, ev = run_verify_scenario(PORT + 16, [("put", putfile, "/unv/up.bin"), ("quit",)])
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/unv/up.bin'), ('Y', ''), ('K', '/unv/up.bin'), ('Q', '')]
            and puts == [(True, '/unv/up.bin')] and reds == []
            and any("not verified" in ln and "did not open" in ln for ln in logs)):
        print("PASS vcrc-F: 'F' on K -> not verified, file kept, put_done(True), no X")
    else:
        print("FAIL vcrc-F: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # F: 'F'+OSP on 'K' (read-side OS protection) - same shape, named.
    vcap, ev = run_verify_scenario(PORT + 17, [("put", putfile, "/ospread/up.bin"), ("quit",)])
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ospread/up.bin'), ('Y', ''), ('K', '/ospread/up.bin'), ('Q', '')]
            and puts == [(True, '/ospread/up.bin')] and reds == []
            and any("OS protection refused the read" in ln for ln in logs)):
        print("PASS vcrc-Fosp: 'F'+OSP on K -> not verified, OS protection named")
    else:
        print("FAIL vcrc-Fosp: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # G: an old dot (5.9.1) cached by the version arm: no 'K' ever, ONE
    # advisory for the session, every put reported done.
    vcap, ev = run_verify_scenario(
        PORT + 18, [("version",), ("put", putfile, "/ho/"), ("put", putfile, "/ho2/"), ("quit",)],
        cap={'ident': b'Osync' + bytes([0]) + b'5.9.1'})
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('Y', ''), ('P', '/ho/up.bin'), ('P', '/ho2/up.bin'), ('Q', '')]
            and puts == [(True, '/ho/up.bin'), (True, '/ho2/up.bin')] and reds == []
            and sum(1 for ln in logs if "verification skipped" in ln) == 1):
        print("PASS vcrc-old: dot 5.9.1 -> one Y, zero K/X, one advisory, puts done")
    else:
        print("FAIL vcrc-old: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # H: a pre-5.8 listener ignores 'Y' (re-polls): unsupported, unverified.
    vcap, ev = run_verify_scenario(PORT + 19, [("put", putfile, "/ho/"), ("quit",)],
                                   cap={'ident': None})
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Y', ''), ('Q', '')]
            and puts == [(True, '/ho/up.bin')] and reds == []
            and any("verification skipped" in ln for ln in logs)):
        print("PASS vcrc-noY: silent 'Y' -> no K, advisory, put_done(True)")
    else:
        print("FAIL vcrc-noY: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # I / J: the ZXNR floor - 1.0.8 is asked, 1.0.7 is not.
    vcap, ev = run_verify_scenario(PORT + 20, [("put", putfile, "/ho/"), ("quit",)],
                                   cap={'ident': b'On2n' + bytes([0]) + b'1.0.8'})
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Y', ''), ('K', '/ho/up.bin'), ('Q', '')]
            and puts == [(True, '/ho/up.bin')] and reds == []
            and f"crc32 /ho/up.bin: {want} — verified" in logs):
        print("PASS vcrc-zxnr: n2n 1.0.8 -> K asked, verified")
    else:
        print("FAIL vcrc-zxnr: ops=", vcap.get('ops'), "puts=", puts, "logs=", logs); ok = False
    vcap, ev = run_verify_scenario(PORT + 21, [("put", putfile, "/ho/"), ("quit",)],
                                   cap={'ident': b'Ohttpbridge' + bytes([0]) + b'1.0.7'})
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Y', ''), ('Q', '')]
            and puts == [(True, '/ho/up.bin')] and reds == []
            and any("verification skipped" in ln for ln in logs)):
        print("PASS vcrc-zxnrold: httpbridge 1.0.7 -> no K, advisory")
    else:
        print("FAIL vcrc-zxnrold: ops=", vcap.get('ops'), "puts=", puts, "logs=", logs); ok = False

    # K: the toggle off -> the 9.7.2 behaviour, not even a 'Y'.
    vcap, ev = run_verify_scenario(PORT + 22, [("put", putfile, "/ho/"), ("quit",)],
                                   verify=lambda: False)
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Q', '')]
            and puts == [(True, '/ho/up.bin')] and reds == []):
        print("PASS vcrc-off: verify off -> P, Q only, put_done(True)")
    else:
        print("FAIL vcrc-off: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds); ok = False

    # L: the toggle is read PER PUT (a flip applies to the next file, no restart).
    flag = [True, False]
    vcap, ev = run_verify_scenario(
        PORT + 23, [("put", putfile, "/ho/"), ("put", putfile, "/ho2/"), ("quit",)],
        verify=lambda: flag.pop(0))
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Y', ''), ('K', '/ho/up.bin'),
                            ('P', '/ho2/up.bin'), ('Q', '')]
            and puts == [(True, '/ho/up.bin'), (True, '/ho2/up.bin')] and reds == []):
        print("PASS vcrc-flip: toggle read per put - second put unverified, no restart")
    else:
        print("FAIL vcrc-flip: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds); ok = False

    # M: a bridge put (BridgeReply sink) is exempt - /put + /crc stay the
    # caller's own two-step; no UI put_done either.
    bpv = BridgeReply()
    vcap, ev = run_verify_scenario(PORT + 24, [("put", putfile, "/ho/", bpv), ("quit",)])
    puts, reds, logs = _split(ev)
    bres = bpv.wait(5)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Q', '')]
            and bres == {'ok': True} and puts == [] and reds == []):
        print("PASS vcrc-bridge: bridge put exempt - no Y/K, reply {'ok': True}, no put_done")
    else:
        print("FAIL vcrc-bridge: ops=", vcap.get('ops'), "bres=", bres, "puts=", puts); ok = False

    # N: a listener that answers 'Y' but ignores 'K' (re-polls): no answer,
    # file kept, no X.
    vcap, ev = run_verify_scenario(PORT + 25, [("put", putfile, "/ho/"), ("quit",)],
                                   cap={'k_silent': True})
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/ho/up.bin'), ('Y', ''), ('K', '/ho/up.bin'), ('Q', '')]
            and puts == [(True, '/ho/up.bin')] and reds == []
            and any("no answer" in ln for ln in logs)):
        print("PASS vcrc-Ksilent: ignored K -> no answer, file kept, put_done(True)")
    else:
        print("FAIL vcrc-Ksilent: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # O: the link drops while the 'K' is out: unverified, ONE put_done(True).
    vcap, ev = run_verify_scenario(PORT + 26, [("put", putfile, "/die/up.bin")])
    puts, reds, logs = _split(ev)
    if (vcap.get('ops') == [('P', '/die/up.bin'), ('Y', ''), ('K', '/die/up.bin')]
            and puts == [(True, '/die/up.bin')] and reds == []
            and any("no answer" in ln for ln in logs)):
        print("PASS vcrc-drop: link drop on K -> exactly one put_done(True), no red")
    else:
        print("FAIL vcrc-drop: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "logs=", logs); ok = False

    # P: the Next hangs up AFTER answering a bad digest, before the 'X' can be
    # served: the finally backstop owes the red verdict + ONE put_done(False).
    vcap, ev = run_verify_scenario(PORT + 27, [("put", putfile, "/corrupt/hangup.bin")])
    puts, reds, logs = _split(ev)
    ri, pi = _order(ev)
    if (vcap.get('ops') == [('P', '/corrupt/hangup.bin'), ('Y', ''), ('K', '/corrupt/hangup.bin')]
            and puts == [(False, '/corrupt/hangup.bin')]
            and len(reds) == 1 and "could NOT be deleted" in reds[0]
            and "session ended" in reds[0]
            and ri is not None and pi is not None and ri < pi):
        print("PASS vcrc-hangup: death before X -> finally reports red, one put_done(False)")
    else:
        print("FAIL vcrc-hangup: ops=", vcap.get('ops'), "puts=", puts, "reds=", reds,
              "order=", (ri, pi), "logs=", logs); ok = False

    # The new signal's shape: put_verify_failed(str).
    seen_v = []
    sv = RemoteExplorerSignals()
    sv.put_verify_failed.connect(seen_v.append, Qt.DirectConnection)
    sv.put_verify_failed.emit("m")
    if seen_v == ["m"]:
        print("PASS vcrc-signal: put_verify_failed(str)")
    else:
        print("FAIL vcrc-signal:", seen_v); ok = False

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
