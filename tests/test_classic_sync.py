"""Localhost end-to-end test for the classic (Sync3/Sync4) NextSync server.

Drives zxnu_workers.run_classic_sync_server — the loop extracted from
MainWindow's nextsync_do_server_job — with a mock Next on the other end of a
real socket, speaking the dot's half of the protocol exactly as
nextsync/sync/z88dk/nextsync.c does:

  * PC -> Next: Sync4 handshake, Next/Get file pull (respecting max_payload
    chunking), end-of-sync marker, Bye — then the syncpoint must record the
    sent file.
  * Next -> PC: Send handshake, framed N/D/E/B upload — the file must land
    (sanitized) under the sync root, respect the conflict policy, and be
    recorded in the syncpoint.

No Qt, no widgets: the server's UI-facing bits are injected callables.
Run with: python tests/test_classic_sync.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from zxnu_workers import run_classic_sync_server  # noqa: E402
from zxnu_config import SYNCPOINT  # noqa: E402

PORT = 20770

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)

# ---- framing (mirrors sendpacket / the dot's send_block) --------------------
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
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def recv_frame(sock):
    hdr = recv_exact(sock, 2)
    assert hdr is not None, "server closed while a frame was expected"
    total = (hdr[0] << 8) | hdr[1]
    rest = recv_exact(sock, total - 2)
    assert rest is not None, "server closed mid-frame"
    payload, cs0, cs1, pktno = rest[:-3], rest[-3], rest[-2], rest[-1]
    c0, c1 = _cs(payload)
    assert (c0, c1) == (cs0, cs1), "bad checksum from server"
    return payload, pktno

def start_server(root, port, **kw):
    logs = []
    done = threading.Event()
    def _run():
        try:
            run_classic_sync_server(
                root, logs.append, force_sync_once=True, port=port, **kw)
        finally:
            done.set()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # Wait for the listening socket.
    for _ in range(100):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            return t, done, logs, s
        except OSError:
            time.sleep(0.05)
    raise AssertionError("server never started listening")

# =============================== PC -> Next ==================================
tmp = tempfile.mkdtemp(prefix="zxnu-classic-sync-")
root = tmp.replace("\\", "/") + "/"
payload_bytes = bytes(range(256)) * 2 + b"TAIL"          # 516 bytes
with open(os.path.join(tmp, "game.tap"), "wb") as f:
    f.write(payload_bytes)

t, done, logs, s = start_server(root, PORT, max_payload=64)
s.settimeout(10)

s.sendall(b"Sync4")
pl, _ = recv_frame(s)
check("Sync4 handshake answered", pl == b"NextSync4", pl)

s.sendall(b"Next")
pl, _ = recv_frame(s)
flen = int.from_bytes(pl[0:4], "big")
namelen = pl[4]
name = pl[5:5 + namelen].decode()
check("file header: length", flen == len(payload_bytes), flen)
check("file header: name", name.endswith("game.tap"), name)

received = b''
pkt_expected = 0
while len(received) < flen:
    s.sendall(b"Get")
    pl, pktno = recv_frame(s)
    check_ok = len(pl) <= 64
    if not check_ok:
        check("chunk respects max_payload=64", False, str(len(pl)))
        break
    if pktno != (pkt_expected & 0xff):
        check("chunk packet numbering", False, f"got {pktno} want {pkt_expected}")
        break
    received += pl
    pkt_expected += 1
check("file content transferred intact", received == payload_bytes,
      f"{len(received)}/{len(payload_bytes)} bytes")
check("chunk count matches max_payload", pkt_expected == (len(payload_bytes) + 63) // 64,
      str(pkt_expected))

s.sendall(b"Next")
pl, _ = recv_frame(s)
check("end-of-sync marker", pl == b"\x00\x00\x00\x00\x00", pl)

s.sendall(b"Bye")
pl, _ = recv_frame(s)
check("Bye answered with Later", pl == b"Later", pl)
s.close()
check("server exited after sync-once", done.wait(15))

sp = os.path.join(tmp, SYNCPOINT)
check("syncpoint written", os.path.isfile(sp))
if os.path.isfile(sp):
    check("syncpoint records the sent file",
          any("game.tap" in line for line in open(sp).read().splitlines()),
          open(sp).read())

# =============================== Next -> PC ==================================
def push_file(port, name, data, policy, pre_existing=None, root_dir=None):
    """Run a fresh server and push one file via the Send path. Returns
    (logs, done_ok)."""
    rd = root_dir or root
    if pre_existing is not None:
        with open(os.path.join(rd.rstrip("/"), name), "wb") as f:
            f.write(pre_existing)
    t2, done2, logs2, c = start_server(rd, port, get_conflict_policy=lambda: policy)
    c.settimeout(10)
    c.sendall(b"Sync4")
    recv_frame(c)
    c.sendall(b"Send")
    pl, _ = recv_frame(c)
    assert pl == b"Send", pl
    blocks = [
        b"N" + len(data).to_bytes(4, "big") + bytes([len(name)]) + name.encode(),
        b"D" + data,
        b"E",
        b"B",
    ]
    for i, blk in enumerate(blocks):
        c.sendall(frame(blk, i))
        pl, _ = recv_frame(c)
        assert pl[:1] == b"O", (blk[:1], pl)   # "Ok"
    c.close()                                   # ends the post-B linger promptly
    ok = done2.wait(15)
    return logs2, ok

data2 = b"PUSHED-FROM-NEXT" * 10
logs2, ok = push_file(PORT + 1, "incoming.scr", data2, "overwrite")
check("send-mode server exited", ok)
dest = os.path.join(tmp, "incoming.scr")
check("pushed file landed under the sync root", os.path.isfile(dest))
if os.path.isfile(dest):
    check("pushed file content intact", open(dest, "rb").read() == data2)
check("syncpoint records the received file",
      any("incoming.scr" in line for line in open(sp).read().splitlines()),
      open(sp).read() if os.path.isfile(sp) else "no syncpoint")

# Conflict policy: ignore must keep the existing local content.
keep = b"DO-NOT-TOUCH"
logs3, ok = push_file(PORT + 2, "conflict.bin", b"NEW-CONTENT", "ignore",
                      pre_existing=keep)
check("ignore-policy server exited", ok)
check("ignore policy keeps the local file",
      open(os.path.join(tmp, "conflict.bin"), "rb").read() == keep)
check("skip logged", any("Skipped (already exists)" in ln for ln in logs3))

# Path sanitation: a hostile name must not escape the sync root.
logs4, ok = push_file(PORT + 3, "evil.bin", b"X", "overwrite")
# (the straightforward name above keeps the server happy; now the hostile one)
t5, done5, logs5, c = start_server(root, PORT + 4, get_conflict_policy=lambda: "overwrite")
c.settimeout(10)
c.sendall(b"Sync4"); recv_frame(c)
c.sendall(b"Send"); recv_frame(c)
hostile = "C:/../../escape.bin"
blocks = [b"N" + (1).to_bytes(4, "big") + bytes([len(hostile)]) + hostile.encode(),
          b"D" + b"Z", b"E", b"B"]
for i, blk in enumerate(blocks):
    c.sendall(frame(blk, i))
    recv_frame(c)
c.close()
done5.wait(15)
outside = os.path.abspath(os.path.join(tmp, "..", "escape.bin"))
check("hostile path cannot escape the sync root", not os.path.exists(outside))
check("sanitized file stays inside the root",
      os.path.isfile(os.path.join(tmp, "escape.bin")))

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    for line in logs[-6:]:
        print("server log:", line)
    sys.exit(1)
print("RESULT: ALL CLASSIC SYNC CHECKS PASSED")
