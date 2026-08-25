"""Unit tests for espemu.py — the RS232 ESP-AT emulator behind MAME's
``-bitb socket`` bitbanger.

Two layers, mirroring the module's own split:

* ENGINE tests drive :class:`espemu.EspAtEngine` with a fake gateway — no
  sockets, no threads — and pin the exact reply shapes the Next-side
  consumers key on (the ``.sync5`` dot's bare-CRLF liveness probe wants
  ``ERROR``; ``CIPCLOSE`` with no link must answer ``ERROR`` fast; client
  data is ``+IPD,<len>:`` with NO link id; ``SEND OK`` on its own short
  line; a success reply never contains "busy"/"ERROR").
* SERVER tests run :class:`espemu.EspAtServer` on an ephemeral port with a
  scripted fake MAME (a plain TCP client) and a fake upstream TCP server,
  covering the whole relay end to end plus lifecycle: synchronous bind,
  restart-replaces-instance, stop() joining the thread, a second MAME
  connection replacing the first.

Run: python tests/test_esp_emu.py  (no pytest, matching the suite).
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import espemu
from espemu import EspAtEngine, EspAtServer, _TraceGate

ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        ok = False


# ---------------------------------------------------------------------------
# engine harness
# ---------------------------------------------------------------------------
class FakeGateway:
    """Records every network request; scripted results."""

    def __init__(self):
        self.connects = []          # (link, host, port)
        self.sends = []             # (link, bytes)
        self.closes = []            # link
        self.servers = []           # (enable, port)
        self.connect_ok = True
        self.send_ok = True
        self.server_ok = True

    def net_connect(self, link, host, port):
        self.connects.append((link, host, port))
        return self.connect_ok

    def net_send(self, link, data):
        self.sends.append((link, bytes(data)))
        return self.send_ok

    def net_close(self, link):
        self.closes.append(link)

    def net_server(self, enable, port):
        self.servers.append((enable, port))
        return self.server_ok


def make_engine(verbose=False, log=None):
    gw = FakeGateway()
    eng = EspAtEngine(gw, log=log, verbose=verbose)
    eng.take_output()               # discard the boot banner
    return eng, gw


def ask(eng, command):
    """One command line in, the reply bytes out."""
    eng.feed_serial(command)
    return eng.take_output()


# ---------------------------------------------------------------------------
# engine: the load-bearing reply shapes
# ---------------------------------------------------------------------------
def test_engine_basics():
    print("== engine: basic replies ==")
    eng, _gw = make_engine()
    ask(eng, b"ATE0\r\n")           # echo off for byte-exact asserts

    check("bare CRLF answers ERROR (the dot's liveness probe)",
          b"ERROR" in ask(eng, b"\r\n"))
    check("AT answers OK", ask(eng, b"AT\r\n") == b"\r\nOK\r\n")
    r = ask(eng, b"AT+GMR\r\n")
    check("AT+GMR names the emulator and ends OK",
          b"espemu" in r and r.endswith(b"\r\nOK\r\n"), r)
    r = ask(eng, b"AT+CIFSR\r\n")
    check("CIFSR: STAIP line strictly before the OK",
          b'+CIFSR:STAIP,"' in r and r.index(b"STAIP") < r.index(b"OK"), r)
    check("CIFSR carries a dotted quad the parser accepts",
          espemu.STA_IP.encode() in r)
    r = ask(eng, b"AT+CIPSTA?\r\n")
    check("CIPSTA? answers the station IP + OK",
          espemu.STA_IP.encode() in r and b"OK" in r)
    check("UART set form accepted (no-op on a socket)",
          ask(eng, b"AT+UART_CUR=1152000,8,1,0,0\r\n") == b"\r\nOK\r\n")
    check("UART query form answers ERROR (minimal-firmware shape)",
          b"ERROR" in ask(eng, b"AT+UART_CUR?\r\n"))
    check("transparent mode refused", b"ERROR" in ask(eng, b"AT+CIPMODE=1\r\n"))
    check("normal mode accepted", ask(eng, b"AT+CIPMODE=0\r\n") == b"\r\nOK\r\n")
    check("unknown AT command agreed to (generic OK)",
          ask(eng, b"AT+CWMODE=1\r\n") == b"\r\nOK\r\n")
    check("non-AT garbage answers ERROR",
          b"ERROR" in ask(eng, b"hello world\r\n"))
    check("undecodable bytes answer ERROR",
          b"ERROR" in ask(eng, b"\xff\xfe\x01\r\n"))


def test_engine_echo():
    print("\n== engine: echo discipline ==")
    eng, _gw = make_engine()
    r = ask(eng, b"AT\r\n")
    check("echo is ON at boot (spec-faithful)", r.startswith(b"AT\r\n"), r)
    r = ask(eng, b"ATE0\r\n")
    check("ATE0 still echoes itself, then OK",
          r.startswith(b"ATE0") and r.endswith(b"\r\nOK\r\n"), r)
    check("after ATE0 nothing echoes", ask(eng, b"AT\r\n") == b"\r\nOK\r\n")
    ask(eng, b"ATE1\r\n")
    check("ATE1 turns the echo back on",
          ask(eng, b"AT\r\n").startswith(b"AT\r\n"))


def test_engine_overlong_line():
    print("\n== engine: runaway line guard ==")
    eng, _gw = make_engine()
    ask(eng, b"ATE0\r\n")
    eng.feed_serial(b"A" * (espemu.MAX_COMMAND_LEN + 50) + b"\r\n")
    check("an over-long line is discarded silently",
          eng.take_output() == b"")
    check("and the engine still answers afterwards",
          ask(eng, b"AT\r\n") == b"\r\nOK\r\n")
    # Junk whose terminator only arrives later: the discard mode must
    # swallow all of it without corrupting the NEXT real command.
    eng.feed_serial(b"B" * (espemu.MAX_COMMAND_LEN + 9))
    eng.feed_serial(b"still the same junk line\n")
    eng.take_output()
    check("a terminator arriving later ends the discard cleanly",
          ask(eng, b"AT\r\n") == b"\r\nOK\r\n")


def test_engine_connect_and_send():
    print("\n== engine: CIPSTART / CIPSEND / CIPCLOSE, single link ==")
    eng, gw = make_engine()
    ask(eng, b"ATE0\r\n")

    # The dot's pre-connect discipline: CIPCLOSE with nothing open must
    # say ERROR immediately or it burns ten timeouts.
    check("CIPCLOSE with no link answers ERROR",
          b"ERROR" in ask(eng, b"AT+CIPCLOSE\r\n"))
    r = ask(eng, b"AT+CIPSEND=5\r\n")
    check("CIPSEND with no link answers ERROR",
          b"ERROR" in r)
    check("and speaks 'link is not valid' first - ZXNR's fast link-death"
          " verdict, on its own sub-24-char line",
          b"link is not valid\r\n" in r, r)

    r = ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",2048\r\n')
    check("CIPSTART connects and says CONNECT + OK",
          gw.connects == [(0, "10.0.0.9", 2048)]
          and b"CONNECT" in r and b"OK" in r and b"ERROR" not in r, r)
    check("a second CIPSTART on a live link says ALREADY CONNECTED + ERROR",
          b"ALREADY CONNECTED" in ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",2048\r\n'))

    r = ask(eng, b"AT+CIPSENDEX=5\r\n")
    check("CIPSENDEX answers the prompt with a '>'",
          b">" in r and b"ERROR" not in r, r)
    r = ask(eng, b"Sync5")
    check("the payload reaches the gateway exactly",
          gw.sends == [(0, b"Sync5")])
    check("Recv line + SEND OK on its own line, and short enough for the"
          " 23-char line window",
          b"\r\nRecv 5 bytes\r\n" in r and b"\r\nSEND OK\r\n" in r
          and b"busy" not in r and b"ERROR" not in r, r)

    # A payload split across serial reads must still count correctly.
    ask(eng, b"AT+CIPSEND=8\r\n")
    eng.feed_serial(b"abc")
    check("partial payload: no reply yet", eng.take_output() == b"")
    r = ask(eng, b"de fgh")
    check("split payload completes at the announced length",
          gw.sends[-1] == (0, b"abcde fg")
          and b"Recv 8 bytes" in r, r)
    check("payload bytes are NEVER parsed as commands: the leftover 'h' is",
          ask(eng, b"\r\n") != b"" )

    r = ask(eng, b"AT+CIPCLOSE\r\n")
    check("CIPCLOSE closes: CLOSED + OK",
          gw.closes == [0] and b"CLOSED" in r and b"OK" in r, r)
    check("closed means closed: CIPSEND refused again",
          b"ERROR" in ask(eng, b"AT+CIPSEND=3\r\n"))
    # The firmware's own payload cap: the guest names the size, the host
    # must never grow an unbounded buffer on a guest's say-so.
    ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",2048\r\n')
    check("a CIPSEND above the 2048 firmware cap is refused",
          b"ERROR" in ask(
              eng, f"AT+CIPSEND={espemu.MAX_SEND_LEN + 1}\r\n".encode()))
    check("the cap itself is accepted",
          b">" in ask(eng, f"AT+CIPSEND={espemu.MAX_SEND_LEN}\r\n".encode()))
    ask(eng, b"x" * espemu.MAX_SEND_LEN)      # complete it, leave clean
    ask(eng, b"AT+CIPCLOSE\r\n")


def test_engine_send_failure():
    print("\n== engine: SEND FAIL path ==")
    eng, gw = make_engine()
    ask(eng, b"ATE0\r\n")
    ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",80\r\n')
    gw.send_ok = False
    ask(eng, b"AT+CIPSEND=2\r\n")
    r = ask(eng, b"hi")
    check("a dead upstream answers SEND FAIL, not SEND OK",
          b"SEND FAIL" in r and b"SEND OK" not in r, r)


def test_engine_connect_refused():
    print("\n== engine: refused CIPSTART ==")
    eng, gw = make_engine()
    ask(eng, b"ATE0\r\n")
    gw.connect_ok = False
    r = ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",2048\r\n')
    check("a refused connect answers ERROR without CONNECT",
          b"ERROR" in r and b"CONNECT" not in r, r)
    check("and no link came up", b"ERROR" in ask(eng, b"AT+CIPSEND=1\r\n"))
    check("UDP is declined (nothing this project ships uses it)",
          b"ERROR" in ask(eng, b'AT+CIPSTART="UDP","10.0.0.9",2048\r\n'))


def test_engine_ipd_framing():
    print("\n== engine: +IPD framing ==")
    eng, _gw = make_engine()
    ask(eng, b"ATE0\r\n")
    ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",2048\r\n')

    eng.net_data(0, b"Poll")
    r = eng.take_output()
    check("client-mode +IPD carries NO link id (the dot cannot parse one)",
          b"+IPD,4:Poll" in r and b"+IPD,0,4:" not in r, r)

    big = bytes(range(256)) * 5          # 1280 bytes, binary
    eng.net_data(0, big)
    r = eng.take_output()
    check("payloads above the chunk cap are split, nothing lost",
          f"+IPD,{espemu.IPD_CHUNK}:".encode() in r
          and f"+IPD,{1280 - espemu.IPD_CHUNK}:".encode() in r
          and r.count(b"+IPD,") == 2)

    eng.net_closed(0)
    check("a remote close speaks the CLOSED chatter line",
          b"CLOSED\r\n" in eng.take_output())
    check("and the link is gone", b"ERROR" in ask(eng, b"AT+CIPSEND=1\r\n"))


def test_engine_mux_and_server():
    print("\n== engine: CIPMUX=1 + CIPSERVER ==")
    eng, gw = make_engine()
    ask(eng, b"ATE0\r\n")
    check("CIPMUX=1 accepted", ask(eng, b"AT+CIPMUX=1\r\n") == b"\r\nOK\r\n")
    r = ask(eng, b"AT+CIPSERVER=1,2048\r\n")
    check("CIPSERVER=1 starts the listener",
          gw.servers == [(True, 2048)] and b"OK" in r, r)

    eng.net_accepted(1)
    check("an inbound client is announced as '<n>,CONNECT'",
          b"1,CONNECT\r\n" in eng.take_output())
    eng.net_data(1, b"Ok")
    check("mux data is framed with the link id",
          b"+IPD,1,2:Ok" in eng.take_output())

    ask(eng, b"AT+CIPSEND=1,4\r\n")
    r = ask(eng, b"Sync")
    check("a link-addressed send reaches the right link",
          gw.sends == [(1, b"Sync")] and b"SEND OK" in r)

    r = ask(eng, b"AT+CIPCLOSE=1\r\n")
    check("a link-addressed close speaks '<n>,CLOSED'",
          b"1,CLOSED\r\n" in r and b"OK" in r, r)
    check("CIPSERVER=0 stops the listener",
          b"OK" in ask(eng, b"AT+CIPSERVER=0\r\n")
          and gw.servers[-1] == (False, 0))


def test_engine_rst():
    print("\n== engine: AT+RST resets to defaults ==")
    eng, gw = make_engine()
    ask(eng, b"ATE0\r\n")
    ask(eng, b"AT+CIPMUX=1\r\n")
    ask(eng, b'AT+CIPSTART=1,"TCP","10.0.0.9",80\r\n')
    r = ask(eng, b"AT+RST\r\n")
    check("RST answers OK then the boot banner",
          b"OK" in r and b"ready" in r, r)
    check("RST closed the open links", 1 in gw.closes)
    check("RST turned the echo back on",
          ask(eng, b"AT\r\n").startswith(b"AT"))
    ask(eng, b"ATE0\r\n")
    eng.net_data(0, b"x")
    check("RST fell back to single-link framing",
          b"+IPD,1:x" in eng.take_output())


def test_trace_gate():
    print("\n== verbose trace stays bounded ==")
    # A 60 s window makes the burst check immune to CI stalls: no pause
    # between iterations can cross it.
    gate = _TraceGate(interval=60.0)
    said = [gate.say("k", 10) for _ in range(50)]
    spoken = [s for s in said if s is not None]
    check("a burst of 50 events logs exactly once", len(spoken) == 1)
    gate = _TraceGate(interval=0.15)
    for _ in range(50):
        gate.say("k", 10)
    time.sleep(0.2)
    s = gate.say("k", 10)
    check("the next line reports what was suppressed",
          s is not None and "more" in s and "bytes" in s, s)

    lines = []
    eng, _gw = make_engine(verbose=True, log=lines.append)
    ask(eng, b"ATE0\r\n")
    ask(eng, b'AT+CIPSTART="TCP","10.0.0.9",2048\r\n')
    for _ in range(200):
        eng.net_data(0, b"x" * 64)
    check("200 +IPD frames produce at most a couple of trace lines",
          sum(1 for ln in lines if "+IPD" in ln) <= 2,
          f"{sum(1 for ln in lines if '+IPD' in ln)} lines")
    check("every trace line is prefixed for the log console",
          all(ln.startswith("RS232 ESP") for ln in lines), lines[:3])


# ---------------------------------------------------------------------------
# server: sockets end to end
# ---------------------------------------------------------------------------
class FakeUpstream:
    """A one-client TCP server standing in for a NextSync/HTTP server."""

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.conn = None
        self.received = b""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.conn, _ = self.srv.accept()
            while True:
                data = self.conn.recv(4096)
                if not data:
                    return
                self.received += data
        except OSError:
            pass

    def send(self, data):
        self.conn.sendall(data)

    def close_client(self):
        """Hang up so the PEER actually sees it.

        shutdown() before close(), and it matters: close() alone races the
        reader thread blocked in recv() on the same socket. On Windows
        closesocket() aborts that recv and the peer gets a reset, but on
        Linux the blocked recv keeps the socket alive, NO FIN is sent, and
        the peer never learns we hung up - which is exactly how the
        "remote close is announced as CLOSED" check passed locally and
        failed on CI. shutdown() sends the FIN immediately and wakes the
        reader on both platforms.
        """
        if self.conn is not None:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass                      # already gone: close() still runs
            self.conn.close()
            self._thread.join(timeout=2)

    def close(self):
        try:
            self.srv.close()
        except OSError:
            pass
        self.close_client()


class FakeMame:
    """The bitbanger side: a plain TCP client speaking AT."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(0.05)

    def say(self, data):
        self.sock.sendall(data)

    def collect(self, want=b"", timeout=3.0):
        """Read until ``want`` appears (or the timeout runs out)."""
        got = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                got += chunk
            except socket.timeout:
                pass
            if want and want in got:
                break
        return got

    def close(self):
        self.sock.close()


def test_server_end_to_end():
    print("\n== server: the whole relay over real sockets ==")
    logs = []
    upstream = FakeUpstream()
    server = EspAtServer(port=0, log=logs.append)   # port 0: ephemeral
    server.start()
    port = server._listen.getsockname()[1]
    check("start() binds synchronously and the thread runs",
          server.running and port > 0)
    check("the start banner names the MAME arguments",
          any("-rs232_esp null_modem" in ln for ln in logs), logs[:2])
    check("the slow-UART requirement is announced up front",
          any("Slow" in ln for ln in logs))

    mame = FakeMame(port)
    got = mame.collect(b"ready")
    check("a connecting emulator is greeted with the boot banner",
          b"ready" in got, got)

    mame.say(b"ATE0\r\n")
    mame.collect(b"OK")
    mame.say(b"\r\n")
    check("liveness probe over the wire: ERROR",
          b"ERROR" in mame.collect(b"ERROR"))

    mame.say(f'AT+CIPSTART="TCP","127.0.0.1",{upstream.port}\r\n'.encode())
    got = mame.collect(b"OK")
    check("CIPSTART reaches the real upstream: CONNECT + OK",
          b"CONNECT" in got and b"OK" in got, got)

    mame.say(b"AT+CIPSENDEX=5\r\n")
    check("the prompt arrives", b">" in mame.collect(b">"))
    mame.say(b"Sync5")
    got = mame.collect(b"SEND OK")
    check("payload relayed and acknowledged",
          b"Recv 5 bytes" in got and b"SEND OK" in got, got)
    deadline = time.monotonic() + 3
    while upstream.received != b"Sync5" and time.monotonic() < deadline:
        time.sleep(0.02)
    check("the upstream really received the bytes",
          upstream.received == b"Sync5", upstream.received)

    upstream.send(b"\x00\x0e" + b"H" * 12)          # binary downstream
    got = mame.collect(b"+IPD,14:")
    check("downstream bytes come back as one +IPD frame",
          b"+IPD,14:" in got, got)

    upstream.close_client()
    check("the remote close is announced as CLOSED",
          b"CLOSED" in mame.collect(b"CLOSED"))

    # A SECOND MAME (emulator restart) replaces the first, fresh session.
    mame2 = FakeMame(port)
    check("a new emulator connection is greeted afresh",
          b"ready" in mame2.collect(b"ready"))
    mame2.say(b"ATE0\r\nAT+CIPCLOSE\r\n")
    check("the fresh session has no leftover links",
          b"ERROR" in mame2.collect(b"ERROR"))
    mame2.close()
    mame.close()
    server.stop()
    check("stop() joins the loop thread", not server.running)
    upstream.close()


def test_server_lifecycle():
    print("\n== server: lifecycle rules ==")
    logs = []
    a = EspAtServer(port=0, log=logs.append)
    a.start()
    port = a._listen.getsockname()[1]

    # The app's rule: every MAME launch stops the old worker and starts a
    # fresh one — so the port must be reusable immediately after stop().
    a.stop()
    b = EspAtServer(port=port, log=logs.append)
    try:
        b.start()
        check("the port is free again right after stop()", b.running)
    except OSError as ex:
        check("the port is free again right after stop()", False, str(ex))
    finally:
        b.stop()

    # SINGLE-USE is a real contract now: a second start() raises whether
    # the server is still running OR was already stopped - a restarted
    # object would carry a dead selector and stale sockets.
    srv = EspAtServer(port=port)
    srv.start()
    running_raises = False
    try:
        srv.start()
    except RuntimeError:
        running_raises = True
    srv.stop()
    stopped_raises = False
    try:
        srv.start()
    except RuntimeError:
        stopped_raises = True
    check("start() on a running server raises instead of double-binding",
          running_raises)
    check("start() after stop() raises too (single-use, fresh state rule)",
          stopped_raises)

    # A taken port must fail LOUDLY at start() so the app can toast it.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    # SO_REUSEADDR lets two sockets share a port on some platforms; on
    # Windows a second bind of a LISTENING port still fails, which is the
    # case the toast is for.
    failed = False
    c = EspAtServer(port=taken)
    try:
        c.start()
        c.stop()
    except OSError:
        failed = True
    if os.name == "nt":
        check("a taken port raises OSError at start()", failed)
    else:
        print("SKIP taken-port check (SO_REUSEADDR semantics differ off Windows)")
    blocker.close()


def test_server_cipserver_relisten():
    """CIPSERVER=1 twice (the emulated Next reset and asked again) must
    REPLACE the listener: without the close-first, Linux refuses the
    second bind and Windows silently double-binds and leaks."""
    print("\n== server: CIPSERVER re-listen ==")
    server = EspAtServer(port=0)
    server.start()
    port = server._listen.getsockname()[1]
    mame = FakeMame(port)
    mame.collect(b"ready")
    mame.say(b"ATE0\r\n")
    mame.collect(b"OK")

    net_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    net_probe.bind(("127.0.0.1", 0))
    net_port = net_probe.getsockname()[1]
    net_probe.close()

    mame.say(b"AT+CIPMUX=1\r\n")
    mame.collect(b"OK")
    mame.say(f"AT+CIPSERVER=1,{net_port}\r\n".encode())
    check("first CIPSERVER=1 answers OK", b"OK" in mame.collect(b"OK"))
    mame.say(f"AT+CIPSERVER=1,{net_port}\r\n".encode())
    got = mame.collect(b"OK")
    check("a SECOND CIPSERVER=1 on the same port replaces and answers OK",
          b"OK" in got and b"ERROR" not in got, got)
    # ... and the replacement listener actually accepts.
    client = socket.create_connection(("127.0.0.1", net_port), timeout=3)
    check("the replacement listener accepts a client",
          b",CONNECT" in mame.collect(b",CONNECT"))
    client.close()
    mame.close()
    server.stop()


def test_server_reconnect_race():
    """A reconnecting MAME races its own predecessor: unread bytes on the
    OLD serial socket put a stale read event in the same select() batch
    as the new accept, and acting on it used to read EOF and tear the
    FRESH connection down (reproduced 5/6 in review). The socket-identity
    guard makes the stale event a no-op."""
    print("\n== server: reconnect race ==")
    server = EspAtServer(port=0)
    server.start()
    port = server._listen.getsockname()[1]
    for attempt in range(6):
        m1 = FakeMame(port)
        m1.collect(b"ready")
        # Unread data on the old socket + an immediate replacement: both
        # events can land in one select() batch.
        m1.say(b"AT\r\n")
        m2 = FakeMame(port)
        got = m2.collect(b"ready")
        ok_ready = b"ready" in got
        m2.say(b"ATE0\r\n")
        ok_alive = b"OK" in m2.collect(b"OK")
        check(f"attempt {attempt}: the fresh connection survives its "
              f"predecessor's stale event",
              ok_ready and ok_alive)
        m1.close()
        m2.close()
    server.stop()


def test_server_backpressure():
    """A multi-hundred-KB downstream body (an HTTP-bridge-sized response)
    must FLOW, not kill the session: MAME drains at UART pace while the
    LAN fills at LAN pace, so output queues and the upstream reads pause
    at the high-water mark (reproduced in review: 500 KB used to tear the
    session down at ~137 KB with 'connection lost mid-write')."""
    print("\n== server: serial backpressure ==")
    logs = []
    upstream = FakeUpstream()
    server = EspAtServer(port=0, log=logs.append)
    server.start()
    port = server._listen.getsockname()[1]
    mame = FakeMame(port)
    mame.collect(b"ready")
    mame.say(b"ATE0\r\n")
    mame.collect(b"OK")
    mame.say(f'AT+CIPSTART="TCP","127.0.0.1",{upstream.port}\r\n'.encode())
    mame.collect(b"OK")

    TOTAL = 200 * 1024
    payload = b"A" * TOTAL               # no '+' bytes: +IPD regex-safe

    def push():
        # sendall blocks as TCP pushes the pressure back - exactly the
        # point; it completes as the reader drains. Guarded: the teardown
        # at the end of the test can close this socket while it is still
        # blocked, and an unhandled raise in a daemon thread only sprays
        # stderr.
        try:
            upstream.send(payload)
        except OSError:
            pass
    pusher = threading.Thread(target=push, daemon=True)
    pusher.start()

    # Let the queue build WITHOUT reading: the pause must engage instead
    # of the session dying.
    time.sleep(1.0)
    check("the un-drained flood does not kill the session",
          not any("lost mid-write" in ln for ln in logs), logs[-3:])

    # Now drain everything and account for every byte.
    import re
    got = b""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        chunk = mame.collect(timeout=0.5)
        got += chunk
        seen = sum(int(m) for m in re.findall(rb"\+IPD,(\d+):", got))
        if seen >= TOTAL:
            break
    seen = sum(int(m) for m in re.findall(rb"\+IPD,(\d+):", got))
    check("every byte of the 200 KB body arrives, none lost",
          seen == TOTAL, f"{seen}/{TOTAL}")
    check("and the session is still alive afterwards",
          (mame.say(b"AT\r\n") or b"OK" in mame.collect(b"OK")))
    check("still no mid-write teardown in the log",
          not any("lost mid-write" in ln for ln in logs))
    pusher.join(timeout=5)
    mame.close()
    server.stop()
    upstream.close()


def test_server_relaunch_hammer():
    """The field-reported pattern: launch MAME, close it, relaunch - the
    SECOND session found a dead wire (a zombie listener sharing the port
    swallowed the connect). Five stop/start cycles on ONE fixed port, each
    cycle proving its client is actually SERVED, pin the fix."""
    print("\n== server: relaunch hammer (one port, five sessions) ==")
    probe = EspAtServer(port=0)
    probe.start()
    port = probe._listen.getsockname()[1]
    probe.stop()

    served = 0
    for cycle in range(5):
        s = EspAtServer(port=port)
        try:
            s.start()
        except OSError as ex:
            check(f"cycle {cycle}: port bound again after the last stop()",
                  False, str(ex))
            break
        client = FakeMame(port)
        client.say(b"ATE0\r\n")
        got = client.collect(b"OK")
        if b"OK" in got:
            served += 1
        client.close()
        s.stop()
    check("every relaunch cycle was actually SERVED (no zombie steals)",
          served == 5, f"{served}/5")

    # No port sharing, ever: while one server runs, a second on the same
    # port must fail loudly at start() - silently splitting the port is
    # exactly the every-other-launch field failure. This holds on both
    # platforms after the fix (Windows: SO_EXCLUSIVEADDRUSE; POSIX:
    # SO_REUSEADDR never allowed a second listener).
    a = EspAtServer(port=port)
    a.start()
    b = EspAtServer(port=port)
    shared = True
    t0 = time.monotonic()
    try:
        b.start()
        b.stop()
    except OSError:
        shared = False
    check("a second server on a live port fails loudly instead of sharing",
          not shared, f"bind unexpectedly succeeded after {time.monotonic() - t0:.1f}s")
    a.stop()


def main():
    test_engine_basics()
    test_engine_echo()
    test_engine_overlong_line()
    test_engine_connect_and_send()
    test_engine_send_failure()
    test_engine_connect_refused()
    test_engine_ipd_framing()
    test_engine_mux_and_server()
    test_engine_rst()
    test_trace_gate()
    test_server_end_to_end()
    test_server_lifecycle()
    test_server_cipserver_relisten()
    test_server_reconnect_race()
    test_server_backpressure()
    test_server_relaunch_hammer()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
