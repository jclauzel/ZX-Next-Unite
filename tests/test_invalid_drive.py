"""The .sync5 dot 5.9.16 refuses a -listen command whose path names a drive
NextZXOS has not mounted with the status block 'F' + "DRV". This drives the
app's listen worker against a fake Next that answers that way for every
path on D:, and checks that

  * each refused command still gets its ordinary failure report (the mark
    is read as the plain 'F' it starts with),
  * the worker says WHY once per refused command (the invalid_drive signal,
    carrying the letters the command named),
  * a bridge command (a BridgeReply sink) stays silent to the pane,
  * a command on a mounted drive raises nothing,
  * the mark is not mistaken for ZX Next Remote's OS-protection refusal,

and that the widget turns the signal into its "Drive not mounted" toast.
Headless; runs in CI."""
import os
import queue
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PySide6.QtCore import QCoreApplication, Qt
from zxnu_http_bridge import BridgeReply
import zxnu_workers
from zxnu_workers import RemoteExplorerSignals, run_remote_listen_server

FAILS = []


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


def bad(path):
    return len(path) > 1 and path[1] == ":" and path[0].upper() == "D"


def fake_next(sock, wire, wanted):
    """The dot's half: every path on D: is refused with 'F'+DRV, the 5.9.16
    way (a get's refusal is followed by the 'B' that ends an empty walk);
    everything else is answered 'O' (a get: an empty walk)."""
    sock.sendall(b"Listen")
    assert rx_payload(sock) == b"Listening"

    def push(payload):
        time.sleep(0.003)
        sock.sendall(frame(payload, 0))
        assert rx_payload(sock)[0:1] == b"O"

    while len(wire) < wanted:
        time.sleep(0.003)
        sock.sendall(b"Poll")
        cmd = rx_payload(sock)
        op, arg = cmd[0:1].decode(), cmd[1:]
        if op == "I":
            continue
        paths = [p.decode() for p in arg.split(b"\x00")]
        wire.append((op, paths))
        refused = any(bad(p) for p in paths)
        if op == "G":
            if refused:
                push(b"FDRV")
            push(b"B")
        elif op == "L":
            push(b"FDRV" if refused else b"E")
        elif op == "P":
            if refused:
                push(b"FDRV")
            else:
                raise AssertionError("no good put is scripted")
        else:
            push(b"FDRV" if refused else b"O")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_helpers():
    check("'F'+DRV is the invalid-drive mark", zxnu_workers._re_is_nodrive(b"FDRV"))
    check("a plain 'F' is not", not zxnu_workers._re_is_nodrive(b"F"))
    check("'O'+DRV is not", not zxnu_workers._re_is_nodrive(b"ODRV"))
    check("the OS-protection mark is not", not zxnu_workers._re_is_nodrive(b"FOSP"))
    check("and DRV is not read as OS protection", not zxnu_workers._re_is_osp(b"FDRV"))
    letters = zxnu_workers._re_cmd_drive_letters
    check("letters: one path", letters(("mkdir", "d:/x")) == "D")
    check("letters: two drives, in order, upper-cased",
          letters(("rename", "c:/a", "d:/b")) == "CD")
    check("letters: a put counts the Next path, never the local file",
          letters(("put", "C:\\up.bin", "d:/up.bin")) == "D")
    check("letters: a get counts the Next path, never the local folder",
          letters(("get", "d:/f.bin", "C:\\dl")) == "D")
    check("letters: no drive named", letters(("mkdir", "/x")) == "")
    check("letters: a bridge command answers None",
          letters(("mkdir", "d:/x", BridgeReply())) is None)
    check("letters: nothing", letters(None) == "")


def test_worker():
    tmp = tempfile.mkdtemp(prefix="drv_test_")
    local = os.path.join(tmp, "up.bin")
    with open(local, "wb") as f:
        f.write(b"x" * 300)
    sig = RemoteExplorerSignals()
    got = {"drv": [], "ops": [], "osp": [], "ls_failed": [], "puts": [],
           "gets": [], "errors": []}
    sig.invalid_drive.connect(lambda s: got["drv"].append(s), Qt.DirectConnection)
    sig.op_done.connect(lambda ok, o, p: got["ops"].append((ok, o, p)), Qt.DirectConnection)
    sig.os_protected.connect(lambda o, p: got["osp"].append((o, p)), Qt.DirectConnection)
    sig.ls_failed.connect(lambda p: got["ls_failed"].append(p), Qt.DirectConnection)
    sig.put_done.connect(lambda ok, r: got["puts"].append((ok, r)), Qt.DirectConnection)
    sig.got.connect(lambda r, l: got["gets"].append((r, l)), Qt.DirectConnection)
    sig.error.connect(lambda m: got["errors"].append(str(m)), Qt.DirectConnection)

    bridge = BridgeReply()
    cmds = [("mkdir", "c:/ok"),
            ("mkdir", "d:/x"),
            ("ls", "d:/"),
            ("rm", "D:/f.txt"),
            ("rename", "c:/a", "d:/b"),
            ("get", "d:/f.bin", os.path.join(tmp, "dl")),
            ("put", local, "d:/up.bin"),
            ("mkdir", "d:/bridge", bridge),
            ("mkdir", "c:/after")]
    q = queue.Queue()
    for c in cmds:
        q.put(c)
    stop = threading.Event()
    port = free_port()
    t = threading.Thread(target=run_remote_listen_server, args=(sig, q, stop, port),
                         kwargs={"verify_crc": lambda: False}, daemon=True)
    t.start()
    time.sleep(0.3)
    wire = []
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.settimeout(10)
    try:
        fake_next(s, wire, len(cmds))
        time.sleep(0.2)
    finally:
        stop.set()
        s.close()
        t.join(timeout=5)

    check("every command reached the wire, in order",
          [w[0] for w in wire] == ["M", "M", "L", "X", "V", "G", "P", "M", "M"], wire)
    check("one invalid_drive per refused pane command, naming its letters",
          got["drv"] == ["D", "D", "D", "CD", "D", "D"], got["drv"])
    check("the refused mkdir, rm and rename still report their failure",
          (False, "mkdir", "d:/x") in got["ops"] and (False, "rm", "D:/f.txt") in got["ops"]
          and (False, "rename", "c:/a") in got["ops"], got["ops"])
    check("the refused listing is a failed listing", got["ls_failed"] == ["d:/"],
          got["ls_failed"])
    check("the refused put is a failed put", got["puts"] == [(False, "d:/up.bin")],
          got["puts"])
    check("the commands on C: succeed and raise nothing",
          (True, "mkdir", "c:/ok") in got["ops"] and (True, "mkdir", "c:/after") in got["ops"],
          got["ops"])
    check("nothing is reported as OS protection", got["osp"] == [], got["osp"])
    res = bridge.wait(2)
    check("the bridge command got its own failure and no pane signal",
          isinstance(res, dict) and res.get("ok") is False, res)


def test_widget():
    # The handler is plain Python over three attributes: bind it to a stub.
    from zxnu_remote_explorer import RemoteExplorerWidget

    class Stub:
        def __init__(self, drives):
            self._drives = drives
            self._invalid_drives = set()     # 9.7.69: the refused letters
            self._connected = False          # no pane to move in this stub
            self._cwd = "C:/"
            self.logs = []
            self.toasts = []

        def _log(self, s):
            self.logs.append(s)

        def _on_toast(self, title, body, colour):
            self.toasts.append((title, body, colour))

    st = Stub(["C", "M"])
    RemoteExplorerWidget.on_invalid_drive(st, "CD")
    check("the toast names the letter the Next did not report",
          len(st.toasts) == 1 and st.toasts[0][0] == "Drive not mounted"
          and "Drive D:" in st.toasts[0][1] and st.toasts[0][2] == "yellow", st.toasts)
    check("and the log says the Next refused", len(st.logs) == 1 and "D:" in st.logs[0],
          st.logs)
    st = Stub([])
    RemoteExplorerWidget.on_invalid_drive(st, "")
    check("no letter, no toast", st.toasts == [] and st.logs == [])
    st = Stub(["C", "D", "M"])
    RemoteExplorerWidget.on_invalid_drive(st, "D")
    check("a letter the pane thought mounted is still named",
          len(st.toasts) == 1 and "Drive D:" in st.toasts[0][1], st.toasts)


def test_wiring():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pane = open(os.path.join(root, "zxnu_nextsync_pane.py"), encoding="utf-8").read()
    check("the pane connects the signal to the widget",
          "_re_sig.invalid_drive.connect(widget.on_invalid_drive)" in pane)
    dot = open(os.path.join(root, "nextsync", "sync", "z88dk", "nextsync.c"),
               encoding="utf-8").read()
    check("the dot's mark is the worker's",
          "scratch[2] = 'F'; scratch[3] = 'D';" in dot
          and "scratch[4] = 'R'; scratch[5] = 'V';" in dot
          and zxnu_workers.RE_NODRIVE_MARK == b"DRV")


def main():
    QCoreApplication.instance() or QCoreApplication(sys.argv)
    test_helpers()
    test_worker()
    test_widget()
    test_wiring()
    print("RESULT:", "ALL CHECKS PASSED" if not FAILS else "FAILED: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
