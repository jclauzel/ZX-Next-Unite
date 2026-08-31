"""Localhost end-to-end test for the Sync4 -listen protocol.

Drives nextsync5.listen_session() (the server) over a socketpair with a mock
Next on the other end that implements the dot's half of the protocol, exactly
as nextsync/sync/z88dk/nextsync.c does. Validates ls / get / put / mkdir /
rmdir / rm framing without any hardware, plus the 'update' console verb's
stage/verify/swap macro (its own mock + sessions, since its happy path ends
the session with 'Q').
"""
import os, sys, socket, threading, tempfile, shutil, time, io, contextlib

# nextsync5.py lives at the repo root (next to zxnu_http_bridge.py, which it
# imports for the optional -w/-http web server).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import nextsync5 as ns
from zxnu_http_bridge import BridgeReply

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
            captured['quit'] = arg or ""    # "" = plain, "X" = also exit app
            # A <= 5.7.4 dot answers the server's 'Q' with the classic raw
            # "Bye" and then waits for "Later" before closing. The goodbye
            # linger must answer it, or the dot stares at silence for its
            # full cipxfer timeout - the long "Closing.." hang on
            # quit/forceexit. (5.7.5+ skips the Bye and just closes.)
            _settle()
            sock.sendall(b"Bye")
            sock.settimeout(5.0)
            try:
                captured['bye_answer'] = recv_payload(sock)
            except (socket.timeout, OSError):
                captured['bye_answer'] = None
            # Close OUR end at once: the linger's follow-up drain then hits
            # its EOF arm immediately (the tested path a 5.7.5 dot's clean
            # close also takes) instead of eating its full 2 s timeout.
            try:
                sock.close()
            except OSError:
                pass
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
            if arg.startswith("/sys"):              # OS-protected put refusal:
                push(b'FOSP', 0)                     # marked 'F'+OSP (push asserts the ack)
                captured.setdefault('put_osp', []).append(arg)
                continue
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
            # A protecting ZXNextRemote refuses a rename whose src OR dst is
            # under a protected root with the marked 'F'+"OSP" (0.9.0).
            if any(s.startswith("/sys") for s in arg.split("\x00")):
                captured.setdefault('osp', []).append("ren")
                push(b'FOSP', 0)
            else:
                captured['ren'] = arg
                push(b'O', 0)
        elif op == b'U':                            # release (dot v5.9+): the dot
            captured['release'] = True              # closes its OWN file handle;
            push(b'O', 0)                           # one status block back
        elif op in (b'M', b'R', b'X'):              # mkdir/rmdir/rm: status
            # "/sys" is OS-protected (marked refusal); "/locked" is an
            # ordinary failure, so both status paths are exercised.
            if arg.startswith("/sys"):
                captured.setdefault('osp', []).append(op.decode())
                push(b'FOSP', 0)
            else:
                push(b'F' if arg.rstrip("/") == "/locked" else b'O', 0)
        elif op == b'C':                            # rcpy: local copy on the Next
            # arg is "src\x00dst". Mock the dot's reply: a named 'D' progress
            # block per "file", an empty keepalive, then 'O' - or 'F' when the
            # source is the unreadable "/locked" tree.
            # Only a COMPLETED copy is recorded here: the refused one below
            # would otherwise clobber it and fail the older rcpy assertion.
            if not arg.split(chr(0))[-1].startswith("/sys"):
                captured['rcpy'] = arg
            csrc, cdst = arg.split("\x00", 1)
            if cdst.startswith("/sys"):
                # Copying INTO a protected folder: refused on the DESTINATION
                # (reads stay free, so a protected SOURCE is not refused).
                captured.setdefault('osp', []).append("rcpy")
                push(b'FOSP', 0)
            elif csrc.startswith("/locked"):
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


# --- mock Next for the 'update' macro ----------------------------------------
# The update verb's happy path ends the session itself (updc_done sends 'Q'),
# so it gets its own scripted mock rather than flags bolted onto mock_next.
# This one records the wire ORDER in captured['wire'] (the macro's whole
# point), remembers what each 'P' staged so a later 'G' of the same path can
# echo the exact bytes back (or deliberately corrupted ones), answers the 'X'
# of sync5.bak with 'F' (the macro must tolerate a missing .bak), and answers
# 'U'/'V' per scenario (refuse_ren1 refuses the FIRST rename's 'V').
def mock_update_next(sock, captured, corrupt_verify=False, refuse_release=False,
                     refuse_ren1=False):
    assert recv_payload(sock) == b"Listening"
    staged = {}                                     # remote path -> bytes from 'P'

    def push(payload, pkt):
        _settle()
        sock.sendall(frame(payload, pkt))
        assert recv_payload(sock)[0:1] == b'O'      # server acks "Ok"

    while True:
        _settle()
        sock.sendall(b"Poll")
        cmd = recv_payload(sock)
        _dbg(f"[MOCK-upd recv-cmd] {cmd[:24]!r}")
        op, arg = cmd[0:1], cmd[1:].decode()
        if op != b'I':                              # idle poll answers are not commands
            captured.setdefault('wire', []).append((op.decode(), arg))
        if op == b'Q':
            _settle()
            sock.sendall(b"Bye")                    # <= 5.7.4 goodbye, answered "Later"
            sock.settimeout(5.0)
            try:
                recv_payload(sock)
            except (socket.timeout, OSError):
                pass
            try:
                sock.close()
            except OSError:
                pass
            break
        if op == b'I':
            continue
        if op == b'P':                              # staging put: pull the bytes
            buf = b''
            while True:
                _settle()
                sock.sendall(b"Get")
                data = recv_payload(sock)
                if len(data) == 0:
                    break
                buf += data
            staged[arg] = buf
            captured.setdefault('puts', []).append((arg, buf))
        elif op == b'G':                            # verify read-back: echo what was
            body = staged.get(arg, b'')             # staged (or corrupt the first byte)
            if corrupt_verify and body:
                body = bytes([body[0] ^ 0xff]) + body[1:]
            pkt = 0
            push(b'N' + len(body).to_bytes(4, "big")
                 + bytes([len(arg)]) + arg.encode(), pkt); pkt += 1
            push(b'D' + body, pkt); pkt += 1
            push(b'E', pkt); pkt += 1
            push(b'B', pkt)
        elif op == b'X':                            # rm: no .bak exists -> 'F'
            push(b'F' if arg.endswith("sync5.bak") else b'O', 0)
        elif op == b'U':                            # release
            push(b'F' if refuse_release else b'O', 0)
        elif op == b'V':                            # the swap renames
            ren1 = "\x00" in arg and arg.split("\x00", 1)[1].endswith(".bak")
            push(b'F' if (refuse_ren1 and ren1) else b'O', 0)
        elif op == b'L':                            # post-failure liveness ls
            push(b'E', 0)


def run_update_tests(tmp):
    """The 'update' console verb: wire order of the stage/verify/swap macro,
    the failure cleanups, the ident/banner gates, and _dot_ver_older itself.
    Each scenario drives a fresh listen_session over its own socketpair with
    ns._listen_state['ident'] (the cached 'version' answer the verb gates on)
    set directly. Prints PASS/FAIL lines; returns True when all pass."""
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        if cond:
            print(f"PASS {label}")
        else:
            print(f"FAIL {label}: {detail}")
            ok = False

    # A plausible dot build: binary-ish, > MAX_PAYLOAD so the staging put is
    # multi-packet, with the "NextSync <version> " banner embedded verbatim.
    dot590 = os.path.join(tmp, "sync5-590.dot")
    upd_bytes = b"\x7fDOT" + bytes(range(256)) * 6 + b"NextSync 5.9.0 build\x00tail"
    with open(dot590, "wb") as f:
        f.write(upd_bytes)
    bannerless = os.path.join(tmp, "notadot.bin")
    with open(bannerless, "wb") as f:
        f.write(b"just bytes, no version banner at all " * 4)

    def run(cmds, ident, corrupt_verify=False, refuse_release=False,
            refuse_ren1=False):
        srv, nxt = socket.socketpair()
        for s in (srv, nxt):
            try:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                pass
        nxt.settimeout(10.0)      # a wedged macro fails the run, never hangs it
        stats = {'packets': 0, 'totalbytes': 0, 'payloadbytes': 0,
                 'retries': 0, 'restarts': 0, 'gee': 0}
        captured = {}
        ns._listen_state['ident'] = ident           # the cached 'version' answer
        t = threading.Thread(target=ns.listen_session, args=(srv, stats, cmds),
                             daemon=True)
        srv_log = io.StringIO()
        try:
            with contextlib.redirect_stdout(srv_log):
                t.start()
                mock_update_next(nxt, captured,
                                 corrupt_verify=corrupt_verify,
                                 refuse_release=refuse_release,
                                 refuse_ren1=refuse_ren1)
                t.join(timeout=5)
        finally:
            ns._listen_state['ident'] = None        # module-global: never leak
            srv.close(); nxt.close()
        return captured, srv_log.getvalue(), not t.is_alive()

    # 1. Happy path: a 5.8.0 dot listening, staging a 5.9.0 build. The whole
    # macro in order — the 'U' release BEFORE the rm of sync5.bak, so a
    # pre-5.9 dot fails with nothing of the user's deleted — fully-qualified
    # default c:/dot paths, the 'F' answer to rm sync5.bak tolerated, and
    # the session ended by the macro's own 'Q'.
    cap, out, done = run([("update", dot590, "")], ("sync", "5.8.0"))
    want = [('P', 'c:/dot/sync5.new'),
            ('G', 'c:/dot/sync5.new'),
            ('U', ''),
            ('X', 'c:/dot/sync5.bak'),
            ('V', 'c:/dot/sync5\x00c:/dot/sync5.bak'),
            ('V', 'c:/dot/sync5.new\x00c:/dot/sync5'),
            ('Q', '')]
    check("updOrder: P/G/U/X/V/V/Q with c:/dot paths ('F' on rm .bak tolerated)",
          cap.get('wire') == want, f"{cap.get('wire')}")
    check("updBytes: staged sync5.new byte-identical to the local file",
          cap.get('puts') == [('c:/dot/sync5.new', upd_bytes)],
          f"{[(p, len(b)) for p, b in cap.get('puts', [])]}")
    check("updDone : reported COMPLETE and the session ended on the macro's 'Q'",
          "update COMPLETE" in out and done, out)

    # 2. Verify mismatch: the read-back differs -> cleanup rm of sync5.new,
    # 'U' never sent, "update failed" reported, and the session stays alive
    # (the queued ls after it still answers).
    cap, out, done = run([("update", dot590, ""), ("ls", "/", "")],
                         ("sync", "5.8.0"), corrupt_verify=True)
    wire = cap.get('wire', [])
    wire_ops = [w[0] for w in wire]
    check("updVerF : mismatch cleaned up sync5.new; no 'U'/'V'; reported failed",
          ('X', 'c:/dot/sync5.new') in wire
          and 'U' not in wire_ops and 'V' not in wire_ops
          and "update failed" in out and "read back different" in out,
          f"{wire} / {out}")
    check("updAlive: session survives the failed update (ls still answers)",
          ('L', '/') in wire and "Listing (0 entries)" in out and done, out)

    # 3. Release refused: 'U' answered 'F'. The 'U' runs BEFORE the rm of
    # sync5.bak, so a refusal must have deleted NOTHING of the user's — no
    # 'X' of sync5.bak ever goes out. The macro still cleans up its own
    # stage (rm sync5.new AFTER the 'U'), no 'V' ever goes out, and the
    # session STAYS alive (release failure means not released): the queued
    # ls after it still answers.
    cap, out, done = run([("update", dot590, ""), ("ls", "/", "")],
                         ("sync", "5.8.0"), refuse_release=True)
    wire = cap.get('wire', [])
    seen = ('U', '') in wire and ('X', 'c:/dot/sync5.new') in wire
    check("updRelF : refused 'U' -> cleanup rm of sync5.new, no rm of .bak, "
          "no 'V' ever sent",
          seen and wire.index(('X', 'c:/dot/sync5.new')) > wire.index(('U', ''))
          and ('X', 'c:/dot/sync5.bak') not in wire
          and not any(o == 'V' for o, _ in wire)
          and "update failed" in out and "did not release" in out,
          f"{wire} / {out}")
    check("updRelA : session survives the refused release (ls still answers)",
          ('L', '/') in wire and "Listing (0 entries)" in out and done, out)

    # 3b. First rename refused post-'U': the release succeeded but ren1
    # ('V' sync5 -> sync5.bak) answered 'F'. Nothing moved — but the dot's
    # handle is gone, so the console must print the failure AND the macro
    # must end the session itself with its own 'Q': the queued ls after it
    # is never served, and nothing follows the 'Q' on the wire.
    cap, out, done = run([("update", dot590, ""), ("ls", "/", "")],
                         ("sync", "5.8.0"), refuse_ren1=True)
    wire = cap.get('wire', [])
    check("updRen1F: refused ren1 -> failure printed, session ends on the "
          "macro's 'Q', nothing after",
          wire == [('P', 'c:/dot/sync5.new'),
                   ('G', 'c:/dot/sync5.new'),
                   ('U', ''),
                   ('X', 'c:/dot/sync5.bak'),
                   ('V', 'c:/dot/sync5\x00c:/dot/sync5.bak'),
                   ('Q', '')]
          and "update failed" in out and "could not rename" in out
          and "Nothing was swapped" in out and ('L', '/') not in wire
          and done,
          f"{wire} / {out}")

    # 4a-d. Gating refusals: each leaves NOTHING update-related on the wire
    # (only the harness's closing quit) and names its reason on the console.
    for label, ident, needle in (
            ("None", None, "run 'version' first"),
            ("False", False, "did not answer the version query"),
            ("ZXNR", ("httpbridge", "1.0.2"), "needs ZXNR 1.0.3+"),
            ("Same", ("sync", "5.9.0"), "add 'force' to push anyway")):
        cap, out, done = run([("update", dot590, "")], ident)
        check(f"updGate{label}: refused ({needle!r}), nothing on the wire",
              cap.get('wire') == [('Q', '')] and needle in out and done,
              f"{cap.get('wire')} / {out}")

    # 4e. ...but update_force overrides the same-version gate: the wire sees
    # the staging 'P' (and the macro then runs to completion).
    cap, out, done = run([("update_force", dot590, "")], ("sync", "5.9.0"))
    check("updForce: same version + force DOES stage (wire sees the 'P')",
          cap.get('wire', [])[:1] == [('P', 'c:/dot/sync5.new')]
          and "update COMPLETE" in out and done,
          f"{cap.get('wire')} / {out}")

    # 5. A local file with no "NextSync <version>" banner is not a dot build:
    # refused before anything touches the wire.
    cap, out, done = run([("update", bannerless, "")], ("sync", "5.8.0"))
    check("updNoBan: bannerless local file refused, nothing on the wire",
          cap.get('wire') == [('Q', '')] and "carries no 'NextSync" in out and done,
          f"{cap.get('wire')} / {out}")

    # 6. ZXNR flavor: a ZX Next Remote listener (1.0.3+) updates its own
    # .nex over the same macro. Everything the sync5 flow hardcoded now
    # derives from the file's BASE name; the staged build's version rides
    # the CONTAINING folder's itch.io-extract name (zxnextremote-9.9.9,
    # the .nex has no parseable banner); and the macro ends with the
    # MARKED quit ('Q'+'X') - the .nex saves its settings and soft-resets
    # the Next into NextZXOS, where the swapped build relaunches.
    zdir = os.path.join(tmp, "zxnextremote-9.9.9")
    os.makedirs(zdir, exist_ok=True)
    nexfile = os.path.join(zdir, "zxnextremote-n2n.nex")
    nex_bytes = (b"Next\x00" + bytes(range(256)) * 6
                 + b"ZXNextRemote\x00" + b"9.9.9\x00tail")
    with open(nexfile, "wb") as f:
        f.write(nex_bytes)
    cap, out, done = run([("update", nexfile, "c:/apps")], ("n2n", "1.0.3"))
    zbase = "c:/apps/zxnextremote-n2n.nex"
    want = [('P', zbase + ".new"),
            ('G', zbase + ".new"),
            ('U', ''),
            ('X', zbase + ".bak"),
            ('V', zbase + "\x00" + zbase + ".bak"),
            ('V', zbase + ".new\x00" + zbase),
            ('Q', 'X')]
    check("updZXNR : .nex-base paths, marked quit ('Q'+'X') ends the macro",
          cap.get('wire') == want
          and cap.get('puts') == [(zbase + ".new", nex_bytes)]
          and "update COMPLETE" in out and "soft-reset" in out and done,
          f"{cap.get('wire')} / {out}")

    # 6b. ...while a pre-1.0.3 ZXNR is refused before a byte moves (an older
    # build answers the 'U' release with silence): nothing on the wire.
    cap, out, done = run([("update", nexfile, "c:/apps")],
                         ("httpbridge", "1.0.2"))
    check("updZXNRold: pre-1.0.3 ZXNR refused, nothing on the wire",
          cap.get('wire') == [('Q', '')] and "needs ZXNR 1.0.3+" in out
          and done,
          f"{cap.get('wire')} / {out}")

    # The version gate's comparator, directly: int-tuple compare (so 5.10.0 is
    # NEWER than 5.9.0) and unparseable = NOT older (update then demands force).
    vcases = [(("5.8.0", "5.9.0"), True), (("5.9.0", "5.9.0"), False),
              (("5.10.0", "5.9.0"), False), (("5.9.90", "5.9.0"), False),
              (("dev", "5.9.0"), False)]
    bad = [(args, ns._dot_ver_older(*args))
           for args, wanted in vcases if ns._dot_ver_older(*args) != wanted]
    check("verOlder: int-tuple compare (5.10 > 5.9) + unparseable = not older",
          not bad, f"{bad}")

    return ok


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
    bridge_reply = BridgeReply()   # rides the second protected put below
    osp_reply = BridgeReply()      # rides the protected mkdir below

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
        ("put", putfile, "/sys/up.bin"),            # OS-protected -> 'F'+OSP
        ("put", putfile, "/sys/up2.bin", bridge_reply),  # same, bridge flavour
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
        # OS protection: every status-block verb must report the marked
        # 'F'+OSP refusal BY NAME (and answer a bridge caller 401
        # os-protected), not as a generic "FAILED on the Next".
        ("mkdir", "/sys/evil", ""),                 # protected mkdir  -> OSP
        ("rm", "/sys/config/boot", ""),             # protected rm     -> OSP
        ("ren", "/games/x", "/sys/x"),              # protected dst    -> OSP
        ("rcpy", "/games/a.tap", "/sys/a.tap"),     # copy INTO it     -> OSP
        ("mkdir", "/sys/evil2", "", osp_reply),     # same, bridge flavour
        ("ls", "/", ""),                            # stream still in sync
        # release rides LAST before quit, mirroring the update flow's contract:
        # after 'U' the server sends only path-based ops, then ends the session.
        ("release", "", ""),                        # 'U': dot frees its own file
        # LAST: the marked quit ends the session, so nothing may follow.
        ("quit_app", "", ""),                       # /forceexit: marked quit
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
    # release ('U', dot v5.9+): framed as a bare opcode, one 'O' status back,
    # reported OK by name (the update flow's enabling step).
    if captured.get('release') and "release: OK" in server_out:
        print("PASS release: 'U' framed and acknowledged OK")
    else:
        print("FAIL release: not framed or not reported OK"); ok = False
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
    # OS-protected puts: the console names the OS protection, the bridge
    # reply carries the 401 + os-protected explanation, and both marked
    # blocks were acked (push asserted it) so the far side stops retrying.
    if ("put /sys/up.bin: BLOCKED by the remote OS protection" in server_out
            and captured.get('put_osp') == ["/sys/up.bin", "/sys/up2.bin"]):
        print("PASS putOSP : marked refusal named on the console + acked")
    else:
        print("FAIL putOSP :", captured.get('put_osp')); ok = False
    br_res = bridge_reply.wait(5)
    if (br_res and br_res.get('http') == 401
            and "os-protected" in str(br_res.get('error', ''))):
        print("PASS putOSPb: bridge reply is 401 os-protected")
    else:
        print("FAIL putOSPb:", br_res); ok = False
    # Every status-block verb names the OS protection instead of reporting a
    # generic failure - before this they all said "FAILED on the Next", which
    # sent the operator to debug the network instead of the setting.
    want_osp = ["M", "X", "ren", "rcpy", "M"]
    said = [ln for ln in server_out.splitlines()
            if "BLOCKED by the remote OS protection" in ln
            and "| *** put " not in ln]      # the put path has its own checks
    if (captured.get('osp') == want_osp and len(said) == len(want_osp)):
        print(f"PASS ospVerbs: {len(said)} verbs named the OS protection")
    else:
        print("FAIL ospVerbs:", captured.get('osp'), said); ok = False
    if all(v in " ".join(said) for v in ("mkdir /sys/evil", "rm /sys/config/boot",
                                         "ren /games/x", "rcpy /games/a.tap")):
        print("PASS ospWhich: each refusal names its own command and path")
    else:
        print("FAIL ospWhich:", said); ok = False
    ospr = osp_reply.wait(5)
    if (ospr and ospr.get('http') == 401
            and "os-protected" in str(ospr.get('error', ''))):
        print("PASS ospHttp : bridge reply is 401 os-protected, not 502")
    else:
        print("FAIL ospHttp :", ospr); ok = False
    # /forceexit asks the far side to END ITS APPLICATION, so it sends the
    # MARKED quit; a plain server stop keeps sending the bare 'Q'.
    if captured.get('quit') == "X" and "exit application" in server_out:
        print("PASS quitmark: /forceexit sent the marked quit and said so")
    else:
        print("FAIL quitmark:", repr(captured.get('quit'))); ok = False
    # The goodbye linger must answer a <= 5.7.4 dot's post-quit "Bye" with
    # the framed "Later" (unanswered, the dot burned its full cipxfer
    # timeout before closing - the long "Closing.." hang on quit).
    if captured.get('bye_answer') == b"Later":
        print("PASS byeAns : post-quit Bye answered with Later")
    else:
        print("FAIL byeAns :", repr(captured.get('bye_answer'))); ok = False
    if "put /locked/up.bin: FAILED" in server_out and captured.get('put_fail') == "/locked/up.bin":
        print("PASS putF   : put 'F' reported + acked")
    else:
        print("FAIL putF   :", captured.get('put_fail')); ok = False
    # the session must have run to completion (thread ended)
    if not t.is_alive():
        print("PASS session: ls/mkdir/rmdir/rm/ren framed and completed cleanly")
    else:
        print("FAIL session: did not finish"); ok = False

    # The 'update' console verb rides fresh sessions of its own (its happy
    # path ends the session with 'Q', so it cannot share the run above).
    upd_ok = run_update_tests(tmp)
    ok = ok and upd_ok

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
