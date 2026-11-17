"""9.7.73: Cancel stops the transfer in flight, and the progress dialog
shows how far the current file has got.

Cancel used to drop what was queued and WAIT for the file in flight to
land - minutes, for a large one. The worker now ends it:

* a PUT: every pull the Next makes is answered with a framed "Error"
  numbered (expected packet XOR 0x80). Every listener counts that as a
  failed round and gives the put up after six; a .sync5 5.9.18+ reads the
  number as "the server is cancelling" and stops at that frame. The Next
  reports the put failed, and one 'X' then clears the part-file a build
  older than .sync5 5.9.17 / ZX Next Remote 1.14.0 leaves behind.
* a GET: the block in flight is answered "Abort" in place of "Ok". Every
  listener resends (twelve tries in the dot) and then ends the stream; a
  .sync5 5.9.18+ stops at once. The local part-file is removed.

Only a PANE transfer is ended, and only one that began before the press.

The per-file figure rides a new signal, xfer_progress(kind, path, done,
total); a get's total comes from the 'N' block's size bytes, which
.sync5 5.9.18 fills in (they were always zero).

Drives the real listen worker against fake Nexts, and the real widget and
dialog. Headless; runs in CI."""
import os
import queue
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zxnu_http_bridge import BridgeReply  # noqa: E402
from zxnu_workers import (RE_ABORT_REPLY, RE_NAK, RE_PUT_ABORT_FRAME,  # noqa: E402
                          HdfProgressDialog, RemoteExplorerSignals,
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


def rx_frame(s):
    """(payload, packet byte) of the next framed reply."""
    hdr = rx_exact(s, 2)
    total = (hdr[0] << 8) | hdr[1]
    body = rx_exact(s, total - 2)
    return body[:-3], body[-1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Fake:
    """A Next in -listen mode. *new* = a .sync5 5.9.18: it stops a put at
    the first cancelling "Error" and a get at the first "Abort"; otherwise
    it counts six bad rounds of a put / twelve resends of a block, as every
    listener before it. *cancel_at*: press Cancel (the worker's own hook)
    once that many data frames have moved."""

    def __init__(self, new, cancel_at, cancel, get_size=4000, sized=True,
                 clears=None):
        self.new = new
        self.cancel_at = cancel_at
        self.cancel = cancel
        self.get_size = get_size
        self.sized = sized                 # the 'N' block carries the size
        self.clears = new if clears is None else clears
        self.seen = []
        self.data_frames = 0               # put data frames received
        self.data_after_cancel = 0
        self.error_frames = []             # (packet byte, expected) of each
        self.aborts = 0                    # "Abort" acks of a get block
        self.oks_after_cancel = 0
        self.pressed = False
        self.put_bytes = b""
        self.rm = []                       # paths the PC asked to delete

    def press(self):
        if not self.pressed and self.cancel is not None:
            self.pressed = True
            self.cancel()

    def serve(self, sock, want):
        sock.sendall(b"Listen")
        assert rx_frame(sock)[0] == b"Listening"
        idle_after = 0
        while len(self.seen) < want or idle_after < 6:
            time.sleep(0.002)
            sock.sendall(b"Poll")
            cmd, _ = rx_frame(sock)
            op = cmd[0:1]
            if op == b"I":
                if len(self.seen) >= want:
                    idle_after += 1
                continue
            self.seen.append(cmd)
            if op == b"P":
                self.put(sock)
            elif op == b"G":
                self.get(sock, cmd[1:].decode())
            elif op == b"X":
                self.rm.append(cmd[1:].decode())
                sock.sendall(frame(b"F" if self.clears else b"O", 0))
                assert rx_frame(sock)[0] == b"Ok"
            else:
                raise AssertionError("unscripted command %r" % cmd)

    def put(self, sock):
        pkt = 0
        fails = 0
        word = b"Get"
        while True:
            if self.data_frames == self.cancel_at:
                self.press()
            time.sleep(0.001)
            sock.sendall(word)
            payload, num = rx_frame(sock)
            if num != pkt:
                if payload == RE_PUT_ABORT_FRAME:
                    self.error_frames.append((num, pkt))
                    if self.new and num == (pkt ^ 0x80):
                        break                       # 5.9.18: cancelled, stop
                    fails += 1
                    if fails > 5:
                        break
                    word = b"Retry"
                    continue
                raise AssertionError("unexpected packet %r #%d (want #%d)" % (payload, num, pkt))
            if self.pressed:
                self.data_after_cancel += 1
            if not payload:
                return                              # the empty end-of-file frame
            self.put_bytes += payload
            self.data_frames += 1
            pkt = (pkt + 1) & 0xff
            fails = 0
            word = b"Get"
        # gave up: the put failed
        time.sleep(0.002)
        sock.sendall(frame(b"F", 0))
        assert rx_frame(sock)[0] == b"Ok"

    def block(self, sock, payload, pkt):
        """Push one block; True when acked, False when the file is given up."""
        tries = 12
        while tries:
            tries -= 1
            time.sleep(0.001)
            sock.sendall(frame(payload, pkt))
            ack, _ = rx_frame(sock)
            if ack == b"Ok":
                if self.pressed and payload[0:1] in (b"N", b"D"):
                    self.oks_after_cancel += 1
                return True
            if ack == RE_ABORT_REPLY:
                self.aborts += 1
                if self.new:
                    return False
                continue
            raise AssertionError("unexpected ack %r" % ack)
        return False

    def get(self, sock, name):
        raw = name.encode()
        size = self.get_size.to_bytes(4, "little") if self.sized else bytes(4)
        pkt = 0
        sent = 0
        ok = self.block(sock, b"N" + size + bytes([len(raw)]) + raw, pkt)
        while ok and sent < self.get_size:
            pkt += 1
            if self.data_frames == self.cancel_at:
                self.press()
            n = min(500, self.get_size - sent)
            ok = self.block(sock, b"D" + b"g" * n, pkt)
            if ok:
                sent += n
                self.data_frames += 1
        if ok:
            pkt += 1
            self.block(sock, b"E", pkt)
        # the 'B' carries the next packet number either way
        self.block(sock, b"B", pkt + 1 if ok else pkt)


def run(fake_kw, cmds, press_first=False, want=None):
    """One session. Returns (events, progress list, the fake)."""
    sig = RemoteExplorerSignals()
    ev, prog = [], []
    sig.put_done.connect(lambda ok, r: ev.append(("put_done", ok, r)), Qt.DirectConnection)
    sig.got.connect(lambda r, l: ev.append(("got", r)), Qt.DirectConnection)
    sig.error.connect(lambda m: ev.append(("error", m)), Qt.DirectConnection)
    sig.log.connect(lambda m: LOGS.append(m), Qt.DirectConnection)
    sig.xfer_progress.connect(lambda k, p, d, t: prog.append((k, p, d, t)), Qt.DirectConnection)
    del LOGS[:]
    q = queue.Queue()
    control = {}
    stop = threading.Event()
    port = free_port()
    t = threading.Thread(target=run_remote_listen_server, args=(sig, q, stop, port),
                         kwargs={"verify_crc": lambda: False, "control": control},
                         daemon=True)
    t.start()
    for _ in range(100):
        if "cancel_xfer" in control:
            break
        time.sleep(0.02)
    if press_first:
        control["cancel_xfer"]()
        time.sleep(0.05)
    for c in cmds:
        q.put(c)
    fake = Fake(cancel=control.get("cancel_xfer"), **fake_kw)
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.settimeout(10)
    try:
        fake.serve(s, want if want is not None else len(cmds))
        time.sleep(0.3)
    finally:
        stop.set()
        s.close()
        t.join(timeout=5)
    return ev, prog, fake


def cleared():
    return [m for m in LOGS if m.startswith("Cleared partially transferred file: ")]


def test_put():
    tmp = tempfile.mkdtemp(prefix="cancel_")
    local = os.path.join(tmp, "up.bin")
    body = bytes(range(256)) * 40                       # 10240 bytes, 20 frames
    with open(local, "wb") as f:
        f.write(body)

    # --- an older listener: six refused rounds, then the part-file is cleared
    ev, prog, fk = run(dict(new=False, cancel_at=3), [("put", local, "c:/up.bin")], want=2)
    check("put: no data frame is served after the press",
          fk.data_frames == 3 and fk.data_after_cancel == 0, (fk.data_frames, fk.data_after_cancel))
    check("put: every pull after it gets the framed Error, six in all",
          len(fk.error_frames) == 6, fk.error_frames)
    check("put: numbered expected XOR 0x80 - never the expected number",
          all(num == (exp ^ 0x80) for num, exp in fk.error_frames), fk.error_frames)
    check("put: it fails once, as a put", ev == [("put_done", False, "c:/up.bin")], ev)
    check("put: the part-file is deleted on the Next", fk.rm == ["c:/up.bin"], fk.rm)
    check("put: and the console names the file it cleared",
          cleared() == ["Cleared partially transferred file: c:/up.bin"], LOGS)
    check("put: the console says the cancel came from here",
          any("cancelled here" in m for m in LOGS), LOGS)
    puts = [p for p in prog if p[0] == "put"]
    check("put: progress went out, from 0, growing, against the file's size",
          puts and puts[0][2] == 0 and all(p[3] == len(body) and p[1] == "c:/up.bin" for p in puts)
          and [p[2] for p in puts] == sorted(p[2] for p in puts)
          and puts[-1][2] <= 3 * 512, puts)

    # --- a .sync5 5.9.18: the first cancelling frame ends it
    ev, prog, fk = run(dict(new=True, cancel_at=3), [("put", local, "c:/up.bin")], want=2)
    check("put (5.9.18): one Error frame is enough",
          len(fk.error_frames) == 1 and fk.error_frames[0][0] == (fk.error_frames[0][1] ^ 0x80),
          fk.error_frames)
    check("put (5.9.18): fails once, and the rm that follows is harmless",
          ev == [("put_done", False, "c:/up.bin")] and fk.rm == ["c:/up.bin"], (ev, fk.rm))

    # --- a press made BEFORE the put began does not touch it
    ev, prog, fk = run(dict(new=False, cancel_at=-1), [("put", local, "c:/up.bin")],
                       press_first=True)
    check("put: a Cancel older than the put leaves it alone",
          ev == [("put_done", True, "c:/up.bin")] and fk.put_bytes == body
          and not fk.error_frames and not fk.rm, (ev, len(fk.put_bytes), fk.error_frames))
    puts = [p for p in prog if p[0] == "put"]
    check("put: a whole put's last progress is its full size",
          puts and puts[-1][2] == len(body) and puts[-1][3] == len(body), puts[-3:])

    # --- a bridge put (it has a reply sink) is never ended by the pane's Cancel
    reply = BridgeReply()
    ev, prog, fk = run(dict(new=False, cancel_at=3), [("put", local, "c:/up.bin", reply)])
    res = reply.wait(5)
    check("put: a bridge put is not the pane's to cancel",
          fk.put_bytes == body and not fk.error_frames and not fk.rm
          and bool(res and res.get("ok")), (len(fk.put_bytes), fk.error_frames, res))
    check("put: and it reports no progress to the pane", not prog, prog[:3])


def test_get():
    # --- an older listener resends the refused block, then ends the stream
    tmp = tempfile.mkdtemp(prefix="cancel_")
    ev, prog, fk = run(dict(new=False, cancel_at=3), [("get", "c:/a/big.bin", tmp)])
    check("get: no block is accepted after the press",
          fk.data_frames == 3 and fk.oks_after_cancel == 0, (fk.data_frames, fk.oks_after_cancel))
    check("get: the block is refused with Abort until the Next gives up (12)",
          fk.aborts == 12, fk.aborts)
    check("get: it is reported once, as cancelled",
          ev == [("error", "get c:/a/big.bin: cancelled")], ev)
    check("get: the part-file is removed from this PC", os.listdir(tmp) == [], os.listdir(tmp))
    check("get: and the console names it", len(cleared()) == 1 and cleared()[0].endswith("big.bin"), LOGS)
    gets = [p for p in prog if p[0] == "get"]
    check("get: progress carries the size the 'N' block gave",
          gets and gets[0][2] == 0 and all(p[3] == 4000 for p in gets)
          and gets[-1][2] <= 1500 and gets[0][1] == "c:/a/big.bin", gets)

    # --- a .sync5 5.9.18 stops at the first Abort
    tmp = tempfile.mkdtemp(prefix="cancel_")
    ev, prog, fk = run(dict(new=True, cancel_at=3), [("get", "c:/a/big.bin", tmp)])
    check("get (5.9.18): one Abort is enough", fk.aborts == 1, fk.aborts)
    check("get (5.9.18): cancelled, nothing left on disk",
          ev == [("error", "get c:/a/big.bin: cancelled")] and os.listdir(tmp) == [], (ev, os.listdir(tmp)))

    # --- untouched: a whole get, an older press, a listener that sends no size
    tmp = tempfile.mkdtemp(prefix="cancel_")
    ev, prog, fk = run(dict(new=False, cancel_at=-1, sized=False), [("get", "c:/a/big.bin", tmp)],
                       press_first=True)
    check("get: a Cancel older than the get leaves it alone",
          ev == [("got", "c:/a/big.bin")] and fk.aborts == 0
          and os.path.getsize(os.path.join(tmp, "big.bin")) == 4000, ev)
    gets = [p for p in prog if p[0] == "get"]
    check("get: four zero size bytes are 'not said' (total 0), bytes still counted",
          gets and all(p[3] == 0 for p in gets) and gets[-1][2] > 0, gets[-3:])

    # --- a bridge get is not the pane's to cancel
    tmp = tempfile.mkdtemp(prefix="cancel_")
    reply = BridgeReply()
    ev, prog, fk = run(dict(new=False, cancel_at=3), [("get", "c:/a/big.bin", tmp, reply)])
    res = reply.wait(5)
    check("get: a bridge get is not ended by the pane's Cancel",
          fk.aborts == 0 and bool(res and res.get("ok"))
          and os.path.getsize(os.path.join(tmp, "big.bin")) == 4000, (fk.aborts, res))


def test_reader():
    check("the refusal a handler returns is not truthy-by-accident",
          RE_NAK is not True and RE_NAK and RE_ABORT_REPLY[0:1] == b"A"
          and RE_PUT_ABORT_FRAME == b"Error")
    c = open(os.path.join(ROOT, "nextsync", "sync", "z88dk", "nextsync.c"),
             encoding="utf-8", errors="replace").read()
    check("the dot ends a put on the cancelling Error frame",
          "if (dp[len - 1] == (packetno ^ 0x80)) goto failure;" in c)
    check("the dot ends a get block on an 'A' reply, without retries",
          "if (inbuf[2] == 'A') return 2;" in c and "if (r) break;" in c)
    check("the dot's 'N' block carries the file's size",
          "memcpy((char*)scratch + 3, (char*)&run_total, 4);" in c)
    check("the dot clears the transfer display when a command arrives",
          "run_cmd(op);" in c)
    a = open(os.path.join(ROOT, "nextsync", "sync", "z88dk", "run.asm"),
             encoding="utf-8", errors="replace").read()
    body = a.split("_run_cmd:", 1)[1].split("_run_hide:", 1)[0]
    check("...except for the idle poll and a crc",
          "cp 'I'" in body and "cp 'K'" in body and body.count("ret z") == 3, body)


def test_dialog():
    dlg = HdfProgressDialog("Uploading")
    try:
        check("the per-file bar is hidden until someone drives it",
              dlg.file_progress()[0] is False)
        dlg.set_file_progress(40, "%p%   4 K / 10 K")
        check("driven, it shows with its value", dlg.file_progress() == (True, 40, False))
        dlg.set_file_progress(-1)
        check("-1 is the busy marquee (size not known)", dlg.file_progress()[2] is True)
        dlg.set_file_progress(70)
        check("...and a value brings the scale back", dlg.file_progress() == (True, 70, False))
        dlg.set_file_progress(None)
        check("None hides it again", dlg.file_progress()[0] is False)
        lay = dlg.layout()
        check("it sits right above the batch bar",
              lay.indexOf(dlg._file_bar) == lay.indexOf(dlg._bar) - 1)
    finally:
        dlg.deleteLater()


def test_escape_asks():
    """9.7.74: Escape on the progress dialog used to CLOSE it (QDialog's
    reject) while the operation carried on unseen. It asks now, in the
    dialog, and the operation keeps running under the question."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtTest import QTest
    app = QApplication.instance()
    hits = []
    dlg = HdfProgressDialog("Uploading")
    dlg.cancel_requested.connect(lambda: hits.append(1))
    try:
        dlg.show()
        app.processEvents()
        check("the question is not there until asked",
              not dlg.asking() and dlg._cancel_btn.isVisible())
        QTest.keyClick(dlg, Qt.Key_Escape)
        app.processEvents()
        check("Escape does not close the dialog", dlg.isVisible())
        check("...it asks, in the dialog, with Yes and No",
              dlg.asking() and dlg._ask_yes.isVisible() and dlg._ask_no.isVisible()
              and "Abort" in dlg._ask_label.text()
              and dlg._ask_yes.text() == "Yes" and dlg._ask_no.text() == "No",
              (dlg._ask_label.text(), dlg._ask_yes.text(), dlg._ask_no.text()))
        check("...nothing is cancelled by asking, and the Cancel button steps aside",
              hits == [] and not dlg._cancelled and not dlg._cancel_btn.isVisible())
        dlg.set_progress(40)
        dlg.set_status("Uploading\nfile.bin")
        check("the operation's progress keeps showing under the question",
              dlg._bar.value() == 40 and dlg._file_label.text() == "file.bin")
        QTest.mouseClick(dlg._ask_no, Qt.LeftButton)
        app.processEvents()
        check("No takes the question away and nothing is cancelled",
              not dlg.asking() and dlg._cancel_btn.isVisible() and hits == []
              and dlg.isVisible())
        QTest.keyClick(dlg, Qt.Key_Escape)
        QTest.keyClick(dlg, Qt.Key_Escape)
        app.processEvents()
        check("Escape again answers No", not dlg.asking() and hits == [] and dlg.isVisible())
        QTest.keyClick(dlg, Qt.Key_Escape)
        QTest.keyClick(dlg, Qt.Key_N)
        check("the N key answers No", not dlg.asking() and hits == [])
        # the window manager's close asks too; the owner's own close() closes
        ev = QCloseEvent()
        ev.setSpontaneous() if hasattr(ev, "setSpontaneous") else None
        if ev.spontaneous():
            app.sendEvent(dlg, ev)
            check("a close from the window manager asks instead of closing",
                  dlg.isVisible() and dlg.asking() and not ev.isAccepted())
            dlg._ask_answer(False)
        QTest.keyClick(dlg, Qt.Key_Escape)
        QTest.mouseClick(dlg._ask_yes, Qt.LeftButton)
        app.processEvents()
        check("Yes is the Cancel button: one cancel request, the dialog stays for the outcome",
              hits == [1] and dlg._cancelled and dlg.isVisible() and not dlg.asking()
              and not dlg._cancel_btn.isEnabled(), hits)
        QTest.keyClick(dlg, Qt.Key_Escape)
        check("once cancelling, Escape asks nothing more", not dlg.asking() and hits == [1])
        dlg.close()
        app.processEvents()
        check("the owner's close() still closes it", not dlg.isVisible())
    finally:
        dlg.deleteLater()

    # the Y key, on a fresh dialog
    hits2 = []
    d2 = HdfProgressDialog("Uploading")
    d2.cancel_requested.connect(lambda: hits2.append(1))
    try:
        d2.show()
        QTest.keyClick(d2, Qt.Key_Escape)
        QTest.keyClick(d2, Qt.Key_Y)
        check("the Y key answers Yes", hits2 == [1] and d2._cancelled)
    finally:
        d2.deleteLater()

    # a dialog whose button is not a cancel: Escape does nothing at all
    hits3 = []
    d3 = HdfProgressDialog("Copying", cancel_label="Continue in the background",
                           esc_asks=False)
    d3.cancel_requested.connect(lambda: hits3.append(1))
    try:
        d3.show()
        QTest.keyClick(d3, Qt.Key_Escape)
        app.processEvents()
        check("esc_asks=False: Escape neither closes nor asks",
              d3.isVisible() and not d3.asking() and hits3 == [])
    finally:
        d3.deleteLater()
    check("the question and its No are in all six catalogs",
          all(lang_has(k) for k in ("Abort the operation in progress?", "No", "Yes")))
    wid = open(os.path.join(ROOT, "zxnu_remote_explorer.py"), encoding="utf-8").read()
    check("the Remote Explorer's background-copy dialog opts out",
          "esc_asks=not self._op_background_label" in wid)


def lang_has(key):
    from zxnu_i18n import CATALOGS
    return all(key in CATALOGS[lang] for lang in ("es", "pt", "pl", "ru", "cs", "fr"))


def test_widget():
    import test_remote_explorer_widget as tw
    app = QApplication.instance()
    pressed = []
    w, calls = tw.make_widget(cancel_transfer=lambda: pressed.append(1) or True,
                              drain=lambda: 0)
    try:
        tw.connect_widget(w, calls, listing=[(False, 8000, "big.bin"), (True, 0, "dir")])

        # ---- one file: ONE bar, and it is the file's own progress
        w._run_op("Uploading to the Next…", lambda: w._enqueue(("put", "x", "/up.bin")))
        w._show_op_dialog_if_running()
        dlg = w._op_dialog
        check("premise: the operation is running with its dialog", dlg is not None and w._op_total == 1)
        w.on_xfer_progress("put", "/up.bin", 2500, 10000)
        check("one file: the single bar shows the file's percentage",
              dlg._bar.value() == 25 and dlg.file_progress()[0] is False,
              (dlg._bar.value(), dlg.file_progress()))
        check("...with the bytes written on it", "/" in dlg._bar.format() and "%p%" in dlg._bar.format(),
              dlg._bar.format())
        check("...and the file's name in the status", "up.bin" in dlg._file_label.text(),
              dlg._file_label.text())
        # Cancel: the hook is called, after the drain, and the note says so
        w._on_op_cancel()
        check("Cancel asks the host to end the transfer in flight", pressed == [1], pressed)
        check("...and the dialog says it is being stopped, not waited for",
              "stopping the transfer in flight" in dlg._action_label.text(), dlg._action_label.text())
        check("the operation still waits for the put's own report", w._op_active)
        w.on_put_done(False, "/up.bin")
        for _ in range(5):
            app.processEvents()
        check("...and ends with it, without a failure toast (a cancel is expected)",
              not w._op_active and not [t for t in calls["toasts"] if t[2] == "red"], calls["toasts"])

        # ---- Escape on the operation's dialog: asked, then Yes cancels the op
        tw.drain(calls)
        del pressed[:]
        from PySide6.QtTest import QTest
        w._run_op("Uploading to the Next…", lambda: w._enqueue(("put", "x", "/esc.bin")))
        w._show_op_dialog_if_running()
        dlg = w._op_dialog
        QTest.keyClick(dlg, Qt.Key_Escape)
        check("Escape during a transfer: the dialog stays and asks, the op runs on",
              dlg.isVisible() and dlg.asking() and w._op_active and not w._op_cancelled
              and pressed == [])
        QTest.mouseClick(dlg._ask_no, Qt.LeftButton)
        check("No: the transfer continues, nothing was asked of the worker",
              w._op_active and not w._op_cancelled and pressed == [] and not dlg.asking())
        QTest.keyClick(dlg, Qt.Key_Escape)
        QTest.mouseClick(dlg._ask_yes, Qt.LeftButton)
        check("Yes: the operation is cancelled the Cancel button's way",
              w._op_cancelled and pressed == [1], pressed)
        w.on_put_done(False, "/esc.bin")
        for _ in range(5):
            app.processEvents()
        check("...and ends with the put's report", not w._op_active)

        # ---- more than one command: TWO bars
        tw.drain(calls)

        def _two():
            w._enqueue(("put", "x", "/a.bin"))
            w._enqueue(("put", "y", "/b.bin"))
        w._run_op("Uploading to the Next…", _two)
        w._show_op_dialog_if_running()
        dlg = w._op_dialog
        w.on_xfer_progress("put", "/a.bin", 5000, 10000)
        check("two files: the per-file bar appears above the batch bar",
              dlg.file_progress() == (True, 50, False) and dlg._bar.value() == 0,
              (dlg.file_progress(), dlg._bar.value()))
        w.on_put_done(True, "/a.bin")
        w.on_xfer_progress("put", "/b.bin", 1000, 10000)
        check("...the batch bar counts files, the top one the file moving",
              dlg._bar.value() == 50 and dlg.file_progress() == (True, 10, False),
              (dlg._bar.value(), dlg.file_progress()))
        w.on_put_done(True, "/b.bin")
        for _ in range(5):
            app.processEvents()
        check("the batch ends", not w._op_active)

        # ---- a get whose listener gives no size: the listing on show does
        tw.drain(calls)
        w._run_op("Downloading…", lambda: w._enqueue(("get", "/big.bin", "d")))
        w._show_op_dialog_if_running()
        dlg = w._op_dialog
        w.on_xfer_progress("get", "/big.bin", 2000, 0)
        check("a get with no size on the wire takes it from the listing",
              dlg._bar.value() == 25 and dlg._bar.maximum() == 100, (dlg._bar.value(), dlg._bar.maximum()))
        w.on_xfer_progress("get", "/dir/inside.bin", 3000, 0)
        check("a file that is not in the listing: a busy bar, the bytes in the status",
              dlg._bar.maximum() == 0 and "inside.bin" in dlg._file_label.text()
              and "K" in dlg._file_label.text(), dlg._file_label.text())
        w.on_got("/big.bin", "d")
        for _ in range(5):
            app.processEvents()
        check("progress with no operation running is ignored",
              w.on_xfer_progress("put", "/z", 1, 2) is None and not w._op_active)

        # ---- without the hook, Cancel keeps its old words (and waits)
        w2, calls2 = tw.make_widget(drain=lambda: 0)
        try:
            tw.connect_widget(w2, calls2)
            w2._run_op("Uploading to the Next…", lambda: w2._enqueue(("put", "x", "/up.bin")))
            w2._show_op_dialog_if_running()
            w2._on_op_cancel()
            check("no hook: Cancel says it waits for the current file",
                  "will stop once the current file" in w2._op_dialog._action_label.text(),
                  w2._op_dialog._action_label.text())
            w2.on_put_done(True, "/up.bin")
        finally:
            w2.deleteLater()
    finally:
        w.deleteLater()
    pane = open(os.path.join(ROOT, "zxnu_nextsync_pane.py"), encoding="utf-8").read()
    check("the pane wires the hook and the signal",
          "cancel_transfer=_re_cancel_xfer" in pane
          and "xfer_progress.connect(widget.on_xfer_progress)" in pane
          and "host._re_control.get('cancel_xfer')" in pane)


def main():
    QApplication.instance() or QApplication(sys.argv)
    test_reader()
    test_put()
    test_get()
    test_dialog()
    test_escape_asks()
    test_widget()
    print("RESULT:", "ALL CHECKS PASSED" if not FAILS else "FAILED: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
