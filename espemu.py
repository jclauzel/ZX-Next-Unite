"""espemu.py — a clean-room ESP-AT emulator behind a TCP socket, so MAME's
emulated ZX Spectrum Next can reach the network the way a real one does.

The real machine talks to an ESP8266 Wi-Fi module over its UART, speaking
Espressif's public "AT" command set (AT+CIPSTART / AT+CIPSEND / +IPD ...).
MAME emulates the UART and, with::

    -rs232_esp null_modem -bitb socket.127.0.0.1:<port>

relays those serial bytes to a TCP socket instead — MAME is the CLIENT and
connects to whatever is listening on that port. This module is that
listener: it interprets the AT dialect on the serial side and performs real
TCP work on the other, so ``.sync5``, ZX Next Remote and friends run inside
MAME against the same NextSync/HTTP servers a physical Next would use::

    Next software (in MAME)  --UART/AT-->  MAME -bitb  --TCP-->  EspAtServer
                                                                    |
                                              real TCP to the LAN <-+

Two layers, deliberately separate so the protocol is testable without a
single socket:

* :class:`EspAtEngine` — the pure AT state machine. Bytes in via
  :meth:`EspAtEngine.feed_serial`, bytes out via :meth:`EspAtEngine.take_output`;
  all network activity goes through an injected gateway object, and network
  events come back in via ``net_data`` / ``net_closed`` / ``net_accepted``.
* :class:`EspAtServer` — the socket harness: a ``selectors`` loop on a
  background thread wiring one serial client (MAME) and the engine's TCP
  links together. ``start()`` binds and listens SYNCHRONOUSLY so the caller
  can Popen MAME immediately after; ``stop()`` tears everything down. A
  server object is SINGLE-USE: one MAME session, then build a new one.

The command inventory and the exact reply shapes were derived from the
publicly documented Espressif AT instruction set and from the AT dialogue of
the Next-side clients this project already ships or drives (the ``.sync``/
``.sync5`` dots under ``nextsync/sync/``, ZX Next Remote's esp layer). The
load-bearing subtleties, all pinned by ``tests/test_esp_emu.py``:

* a BARE empty command line answers ``ERROR`` — the dot probes liveness with
  exactly that and waits for the word;
* ``AT+CIPCLOSE`` with NO link open answers ``ERROR`` — the dot's
  pre-connect close loop exits on it instead of burning ten timeouts;
* a CIPSEND on a link that is GONE answers ``link is not valid`` before the
  ``ERROR`` — that chatter line is ZX Next Remote's fast link-death verdict;
  a bare ERROR would cost it eight 3-second retries and a spurious module
  reset;
* client-mode data is framed strictly ``+IPD,<len>:<bytes>`` with no link
  id — the dot's parser cannot read the multi-connection form (which is
  only emitted once ``AT+CIPMUX=1`` selected it);
* ``SEND OK`` sits on its own short line — ZX Next Remote's line watcher
  reads it through a 23-character window;
* a success reply never contains "busy" or "ERROR" — both consumers match
  substrings anywhere in the stream and would resend or abort.

Serial-side FLOW CONTROL: MAME drains the bitbanger socket at emulated wire
pace (~11.5 KB/s at 115200) while the upstream TCP side can fill +IPD output
at LAN speed. Output is therefore queued and written as the socket accepts
it, and once the queue passes a high-water mark the upstream link reads
PAUSE until it drains — so a multi-hundred-KB HTTP body flows through a
bounded buffer instead of bursting the socket and reading as a lost
emulator connection.

TRANSFER SPEED NOTE: multi-KB transfers through the emulated wire need the
Next side set to its SLOW pacing — ``.sync5 -s`` for the dot, or UART speed
"Slow" in ZX Next Remote's settings — and MAME's Machine Configuration RX/TX
rates left at 115200. The Next software's fast-baud paths switch a REAL
UART's rate; on the emulated wire that handshake has nothing to act on.

Standalone use (the app drives it in-process; this is for testing outside)::

    python espemu.py --port 2222 --verbose

Clean-room note: the IDEA of proxying MAME's ESP socket to the real network
comes from Janko Stamenović's jesperl project
(https://sourceforge.net/projects/jesperl/, GNU Affero GPL v3). This module
shares no code, structure or text with jesperl: it is an independent
implementation written from the public AT specification and this project's
own Next-side clients, and is covered by ZX-Next-Unite's MIT license.
"""

import os
import selectors
import socket
import threading
import time

ESPEMU_VERSION = "1.1"

# The station identity handed to AT+CIFSR / AT+CIPSTA?. The Next software
# only ever LOGS these (nothing routes through them — the real routing is
# this host's own IP stack), so fixed values are fine and keep every reply
# deterministic for the tests.
STA_IP = "192.168.4.2"
STA_MAC = "5e:cf:7f:00:00:01"

# Longest accepted command line; a real module rejects runaway lines too,
# and the guard stops a binary stream mistaken for commands from growing an
# unbounded buffer.
MAX_COMMAND_LEN = 512

# Largest CIPSEND payload accepted — the real firmware's own cap. Anything
# larger is an ERROR, not a buffer: the guest commands this size, and the
# host must never let untrusted guest software grow an unbounded payload.
MAX_SEND_LEN = 2048

# Largest single +IPD payload. ZX Next Remote drains its receive ring
# between pumps (1023-byte ring + the UART FIFO) and the dot caps a frame
# at 2048 — 1024 keeps every consumer comfortable, and MAME re-serialises
# to UART pace anyway so smaller frames cost nothing.
IPD_CHUNK = 1024

# Serial-side flow control: above HIGH the upstream link reads pause;
# below LOW they resume. The band is wide so the pause toggles rarely.
SERIAL_HIGH_WATER = 64 * 1024
SERIAL_LOW_WATER = 8 * 1024

# AT link ids run 0..4 on the real firmware.
MAX_LINKS = 5

CRLF = b"\r\n"


class _TraceGate:
    """Rate limiter for the verbose trace: per-key, at most one line per
    ``interval`` seconds, with a count of what was suppressed folded into
    the next line that passes. Keeps a busy transfer from writing one log
    line per +IPD frame — thousands during a sync — while still showing
    that traffic flowed."""

    def __init__(self, interval=5.0):
        self._interval = float(interval)
        self._last = {}       # key -> monotonic timestamp of the last line
        self._held = {}       # key -> (suppressed count, running total)

    def say(self, key, amount=0):
        """Report one event of ``key`` covering ``amount`` units (bytes).
        Returns None to stay quiet, or the suffix to log now: an empty
        string for a lone event, or " (+N more, M bytes)" summarising the
        held-back ones."""
        now = time.monotonic()
        held_n, held_total = self._held.get(key, (0, 0))
        if now - self._last.get(key, 0.0) < self._interval:
            self._held[key] = (held_n + 1, held_total + amount)
            return None
        self._last[key] = now
        self._held[key] = (0, 0)
        if held_n:
            return f" (+{held_n} more, {held_total} bytes)"
        return ""


class EspAtEngine:
    """The AT protocol state machine, free of sockets and threads.

    ``gateway`` performs the real network work:

    * ``net_connect(link, host, port) -> bool`` — open a TCP connection
    * ``net_send(link, data) -> bool``          — write to that connection
    * ``net_close(link)``                       — close it (idempotent)
    * ``net_server(enable, port) -> bool``      — start/stop a listen socket

    The harness feeds serial bytes to :meth:`feed_serial` and network events
    to :meth:`net_data` / :meth:`net_closed` / :meth:`net_accepted`; it
    drains the serial-bound reply bytes with :meth:`take_output`.
    """

    def __init__(self, gateway, log=None, verbose=False):
        self._gw = gateway
        self._log = log or (lambda s: None)
        self._verbose = bool(verbose)
        self._trace_gate = _TraceGate()
        self._out = bytearray()
        self._cmd = bytearray()          # the line being accumulated
        self._discarding = False         # a runaway line: eat to its end
        self._echo = True                # the real module boots echoing
        self._mux = False                # False: single link 0; True: ids
        self._links = set()              # link ids currently open
        # An armed CIPSEND: (link id, bytes still expected, payload so far).
        self._pending = None
        self.emit(b"\r\nready\r\n")      # the boot banner; drained as noise

    # ---- serial side -----------------------------------------------------
    def feed_serial(self, data):
        """Bytes that arrived from the Next (via MAME's bitbanger)."""
        i = 0
        while i < len(data):
            if self._pending is not None:
                i = self._absorb_payload(data, i)
                continue
            b = data[i:i + 1]
            i += 1
            if self._echo:
                self.emit(b)
            if b == b"\n":
                if self._discarding:
                    # The runaway line just ended: the NEXT line starts
                    # clean instead of inheriting the junk's tail.
                    self._discarding = False
                    self._cmd.clear()
                    continue
                line = bytes(self._cmd).rstrip(b"\r")
                self._cmd.clear()
                self._dispatch(line)
            elif self._discarding:
                continue
            elif len(self._cmd) >= MAX_COMMAND_LEN:
                # A runaway line can only be noise (a binary stream taken
                # for commands): eat it whole, up to and including its
                # terminator, and say so once.
                self._cmd.clear()
                self._discarding = True
                self._trace(f"discarded an over-long command line (> {MAX_COMMAND_LEN} bytes)")
            else:
                self._cmd += b

    def take_output(self):
        """Serial-bound bytes produced since the last call."""
        out = bytes(self._out)
        self._out.clear()
        return out

    def emit(self, data):
        self._out += data

    # ---- network events (called by the harness) --------------------------
    def net_data(self, link, data):
        """Incoming TCP bytes: frame as +IPD. The single-link form carries
        NO link id — the dot's parser cannot read the multi-link shape, so
        that one is reserved for CIPMUX=1 sessions which asked for it."""
        for ofs in range(0, len(data), IPD_CHUNK):
            chunk = data[ofs:ofs + IPD_CHUNK]
            if self._mux:
                head = f"\r\n+IPD,{link},{len(chunk)}:".encode("ascii")
            else:
                head = f"\r\n+IPD,{len(chunk)}:".encode("ascii")
            self.emit(head + chunk)
        suffix = self._trace_gate.say("ipd", len(data))
        if suffix is not None:
            self._trace(f"net -> serial: +IPD {len(data)} bytes{suffix}")

    def net_closed(self, link):
        """The remote end went away: say so the way the firmware does —
        the chatter line is what the Next-side de-framers key on."""
        self._links.discard(link)
        if self._mux:
            self.emit(f"{link},CLOSED\r\n".encode("ascii"))
        else:
            self.emit(b"CLOSED\r\n")
        self._trace(f"link {link}: remote closed")

    def net_accepted(self, link):
        """A client dialled our CIPSERVER listener."""
        self._links.add(link)
        self.emit(f"{link},CONNECT\r\n".encode("ascii"))
        self._trace(f"link {link}: inbound client connected")

    # ---- internals -------------------------------------------------------
    def _trace(self, text):
        if self._verbose:
            self._log(f"RS232 ESP: {text}")

    def _absorb_payload(self, data, i):
        """CIPSEND armed: swallow payload bytes until the count is met,
        then perform the send and speak the Recv/SEND OK epilogue."""
        link, want, buf = self._pending
        take = data[i:i + want]
        buf += take
        want -= len(take)
        if want:
            self._pending = (link, want, buf)
            return len(data)
        self._pending = None
        ok = link in self._links and self._gw.net_send(link, bytes(buf))
        # "Recv <n> bytes" fits ZX Next Remote's 23-char line window for
        # every length CIPSEND can express; SEND OK must be its own line.
        self.emit(f"\r\nRecv {len(buf)} bytes\r\n".encode("ascii"))
        self.emit(b"\r\nSEND OK\r\n" if ok else b"\r\nSEND FAIL\r\n")
        suffix = self._trace_gate.say("send", len(buf))
        if suffix is not None:
            self._trace(f"serial -> net: {len(buf)} bytes "
                        f"{'sent' if ok else 'FAILED'}{suffix}")
        return i + len(take)

    def _dispatch(self, line):
        """One complete command line (terminator stripped)."""
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            self.emit(b"\r\nERROR\r\n")
            return
        cmd = text.strip()
        if not cmd:
            # The dot's liveness probe: a bare CRLF, answered with the very
            # word it waits for. Never reply OK here.
            self.emit(b"\r\nERROR\r\n")
            return
        if self._verbose and not cmd.upper().startswith("AT+CIPSEND"):
            # CIPSEND is traced from the payload side, counted and gated.
            self._trace(f"command: {cmd[:60]}")
        handler = self._route(cmd)
        handler(cmd)

    def _route(self, cmd):
        up = cmd.upper()
        if up == "AT":
            return self._at_ok
        if up in ("ATE0", "ATE1"):
            return self._at_echo
        if up == "AT+RST":
            return self._at_rst
        if up == "AT+GMR":
            return self._at_gmr
        if up.startswith("AT+CIPSTART"):
            return self._at_cipstart
        if up.startswith("AT+CIPSEND"):          # CIPSEND and CIPSENDEX
            return self._at_cipsend
        if up.startswith("AT+CIPCLOSE"):
            return self._at_cipclose
        if up.startswith("AT+CIPMUX="):
            return self._at_cipmux
        if up.startswith("AT+CIPSERVER="):
            return self._at_cipserver
        if up.startswith("AT+CIPMODE="):
            return self._at_cipmode
        if up == "AT+CIFSR":
            return self._at_cifsr
        if up == "AT+CIPSTA?":
            return self._at_cipsta
        if up.startswith("AT+UART"):
            return self._at_uart
        if up.startswith("AT"):
            # Anything else in the AT family (CWJAP, CWLAP, CIPDNS,
            # CWMODE, ...): agree politely. The consumers only ever wait
            # for the OK; inventing per-command fictions adds nothing.
            return self._at_ok
        return self._at_error

    # -- simple replies --
    def _at_ok(self, _cmd):
        self.emit(b"\r\nOK\r\n")

    def _at_error(self, _cmd):
        self.emit(b"\r\nERROR\r\n")

    def _at_echo(self, cmd):
        self._echo = cmd.endswith("1")
        self.emit(b"\r\nOK\r\n")
        self._trace(f"echo {'on' if self._echo else 'off'}")

    def _at_rst(self, _cmd):
        # A soft reset: drop everything and come back with the boot banner.
        # (The Next hardware pulls a reset LINE instead — MAME may or may
        # not relay that — so this is belt and braces, not a hot path.)
        for link in sorted(self._links):
            self._gw.net_close(link)
        self._links.clear()
        self._pending = None
        self._mux = False
        self._echo = True
        self._gw.net_server(False, 0)
        self.emit(b"\r\nOK\r\n\r\nready\r\n")
        self._trace("reset to defaults")

    def _at_gmr(self, _cmd):
        self.emit(f"\r\nespemu {ESPEMU_VERSION} "
                  f"(zx-next-unite RS232 ESP emulation)\r\n\r\nOK\r\n"
                  .encode("ascii"))

    def _at_cifsr(self, _cmd):
        # STAIP line strictly BEFORE the OK: the reply is parsed after the
        # OK is matched, from everything collected so far.
        self.emit(f"\r\n+CIFSR:STAIP,\"{STA_IP}\"\r\n"
                  f"+CIFSR:STAMAC,\"{STA_MAC}\"\r\n\r\nOK\r\n"
                  .encode("ascii"))

    def _at_cipsta(self, _cmd):
        self.emit(f"\r\n+CIPSTA:\"{STA_IP}\"\r\n\r\nOK\r\n".encode("ascii"))

    def _at_uart(self, cmd):
        # Baud is meaningless on a socket, but the SET form must succeed —
        # the clients change speed, drain, then re-probe at the new rate
        # (which for us is the same socket). The QUERY form answers ERROR
        # like the minimal firmware builds the Next software tolerates.
        if "=" in cmd:
            self.emit(b"\r\nOK\r\n")
            self._trace(f"uart request accepted (no-op on a socket): {cmd}")
        else:
            self.emit(b"\r\nERROR\r\n")

    def _at_cipmode(self, cmd):
        # Transparent mode is out of scope (nothing this project ships
        # uses it) — refuse it honestly rather than wedge the stream.
        if cmd.rstrip().endswith("=0"):
            self.emit(b"\r\nOK\r\n")
        else:
            self.emit(b"\r\nERROR\r\n")

    def _at_cipmux(self, cmd):
        want = cmd.split("=", 1)[1].strip()
        if want not in ("0", "1"):
            self.emit(b"\r\nERROR\r\n")
            return
        self._mux = want == "1"
        self.emit(b"\r\nOK\r\n")
        self._trace(f"multi-connection mode {'on' if self._mux else 'off'}")

    def _at_cipserver(self, cmd):
        args = cmd.split("=", 1)[1].split(",")
        enable = args[0].strip() == "1"
        port = 0
        if enable:
            try:
                port = int(args[1])
            except (IndexError, ValueError):
                self.emit(b"\r\nERROR\r\n")
                return
        if self._gw.net_server(enable, port):
            self.emit(b"\r\nOK\r\n")
            self._trace(f"server {'listening on ' + str(port) if enable else 'stopped'}")
        else:
            self.emit(b"\r\nERROR\r\n")

    def _at_cipstart(self, cmd):
        # Accepted shapes: AT+CIPSTART="TCP","host",port
        #                  AT+CIPSTART=<id>,"TCP","host",port  (CIPMUX=1)
        try:
            args = cmd.split("=", 1)[1]
            parts = [p.strip() for p in args.split(",")]
            if parts[0].startswith('"'):
                link = 0
                kind, host, port = parts[0], parts[1], int(parts[2])
            else:
                link = int(parts[0])
                kind, host, port = parts[1], parts[2], int(parts[3])
            kind = kind.strip('"').upper()
            host = host.strip('"')
        except (IndexError, ValueError):
            self.emit(b"\r\nERROR\r\n")
            return
        if kind != "TCP" or not (0 <= link < MAX_LINKS) or not host:
            self.emit(b"\r\nERROR\r\n")
            return
        if link in self._links:
            self.emit(b"ALREADY CONNECTED\r\n\r\nERROR\r\n")
            return
        if self._gw.net_connect(link, host, port):
            self._links.add(link)
            if self._mux:
                self.emit(f"{link},CONNECT\r\n\r\nOK\r\n".encode("ascii"))
            else:
                self.emit(b"CONNECT\r\n\r\nOK\r\n")
            self._trace(f"link {link}: connected to {host}:{port}")
        else:
            self.emit(b"\r\nERROR\r\n")
            self._trace(f"link {link}: connect to {host}:{port} FAILED")

    def _at_cipsend(self, cmd):
        # AT+CIPSEND=<len> / AT+CIPSEND=<link>,<len> / AT+CIPSENDEX=<len>.
        # CIPSENDEX's early-stop-on-NUL refinement is deliberately not
        # implemented: the dot always announces exact lengths, and a
        # fixed-length read can never desynchronise on binary payloads.
        if "=" not in cmd:
            self.emit(b"\r\nERROR\r\n")
            return
        try:
            parts = [int(p) for p in cmd.split("=", 1)[1].split(",")]
            if len(parts) == 1:
                link, length = 0, parts[0]
            else:
                link, length = parts[0], parts[1]
        except ValueError:
            self.emit(b"\r\nERROR\r\n")
            return
        if length <= 0 or length > MAX_SEND_LEN:
            # The firmware's own cap. The guest names the size, and the
            # host must never grow an unbounded payload buffer on a
            # guest's say-so.
            self.emit(b"\r\nERROR\r\n")
            return
        if link not in self._links:
            # "link is not valid" is ZX Next Remote's FAST link-death
            # verdict (its line watcher latches the phrase); a bare ERROR
            # here costs it eight 3-second retries and a spurious module
            # reset. The dot never latches chatter, so the extra line is
            # invisible to it. Under 24 chars, as the watcher requires.
            self.emit(b"link is not valid\r\n\r\nERROR\r\n")
            return
        self._pending = (link, length, bytearray())
        self.emit(b"\r\nOK\r\n> ")

    def _at_cipclose(self, cmd):
        if "=" in cmd:
            try:
                link = int(cmd.split("=", 1)[1])
            except ValueError:
                self.emit(b"\r\nERROR\r\n")
                return
        else:
            link = 0
        if link not in self._links:
            # No link to close: ERROR, quickly — the dot loops CIPCLOSE
            # until it sees exactly this before connecting fresh.
            self.emit(b"\r\nERROR\r\n")
            return
        self._gw.net_close(link)
        self._links.discard(link)
        if self._mux:
            self.emit(f"{link},CLOSED\r\n\r\nOK\r\n".encode("ascii"))
        else:
            self.emit(b"CLOSED\r\n\r\nOK\r\n")
        self._trace(f"link {link}: closed by command")


class EspAtServer:
    """The socket harness around :class:`EspAtEngine`.

    ``start()`` binds and listens synchronously — when it returns, MAME can
    be launched and its ``-bitb socket.…`` connect will be accepted — then
    a daemon thread runs the whole show: one serial client (a newer MAME
    connection replaces an older one, so an emulator restart just works),
    the engine, and its TCP links. ``stop()`` wakes the loop, releases the
    port deterministically and joins the thread (bounded); the object is
    SINGLE-USE by design — the app builds a fresh one per MAME launch, so
    no state survives between sessions, and a second ``start()`` raises.

    Every selector registration carries the socket it was made for, and
    every event handler re-checks that socket against the CURRENT one —
    a select() batch can deliver an event for a socket that an earlier
    event in the same batch already replaced (a reconnecting MAME races
    its own predecessor), and acting on the stale event would tear the
    fresh connection down.
    """

    _POLL_S = 0.2       # loop heartbeat when nothing is happening

    def __init__(self, port=2222, host="127.0.0.1", log=None, verbose=False):
        self.port = int(port)
        self.host = host
        self._log = log or (lambda s: None)
        self._verbose = bool(verbose)
        self._sel = None
        self._listen = None            # the bitbanger listener (for MAME)
        self._serial = None            # the connected MAME socket
        self._serial_out = bytearray()  # queued serial-bound bytes
        self._engine = None
        self._links = {}               # link id -> connected TCP socket
        self._paused_links = set()     # reads paused by serial backpressure
        self._net_listen = None        # the CIPSERVER listener, if any
        self._thread = None
        self._wake_r = None
        self._wake_w = None
        self._stopping = False
        self._used = False

    # ---- lifecycle -------------------------------------------------------
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Bind + listen + spawn the loop thread. Raises OSError when the
        port is taken — the caller decides what that means for its launch."""
        if self._used:
            raise RuntimeError("EspAtServer is single-use: build a new one")
        self._used = True
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            # EXCLUSIVE on Windows (field report, first live test): with
            # SO_REUSEADDR two live listeners can share the port and the
            # OS hands connects to the FIRST binder - so a zombie from the
            # previous MAME session silently swallowed the new MAME's
            # -bitb connect ("second launch didn't get the ESP"). An
            # exclusive bind turns that residue into a LOUD start()
            # failure instead. POSIX keeps SO_REUSEADDR, where it only
            # bridges TIME_WAIT and never permits a second listener.
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # A short retry ladder: stop() releases the previous listener
            # synchronously, so a relaunch only ever races OS bookkeeping
            # by milliseconds - but losing that race must not cost the
            # user a launch.
            for attempt in range(10):
                try:
                    srv.bind((self.host, self.port))
                    break
                except OSError:
                    if attempt == 9:
                        raise
                    time.sleep(0.1)
            srv.listen(1)
        except OSError:
            srv.close()
            raise
        srv.setblocking(False)
        self._listen = srv
        self._engine = EspAtEngine(self, log=self._log, verbose=self._verbose)
        self._sel = selectors.DefaultSelector()
        self._sel.register(srv, selectors.EVENT_READ, ("accept-serial",))
        # Self-pipe so stop() can interrupt a quiet select() immediately.
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._sel.register(self._wake_r, selectors.EVENT_READ, ("wake",))
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run, name="espemu", daemon=True)
        self._thread.start()
        self._log(f"RS232 ESP emulation listening on {self.host}:{self.port}"
                  " (MAME: -rs232_esp null_modem -bitb "
                  f"socket.{self.host}:{self.port})")
        self._log("RS232 ESP emulation: transfers need the Next side on "
                  "SLOW pacing ('.sync5 -s', or UART speed 'Slow' in "
                  "ZX Next Remote).")
        return self

    def stop(self):
        """Tear the whole session down.

        The join is BOUNDED and the LISTENER is closed from this thread
        regardless of its outcome: a worker mid-way through a bounded
        network call can outlive the join, and the port must be free for
        the next launch anyway (the field-reported every-other-launch
        failure). A still-running worker then finds its sockets closed,
        errors out of whatever it was blocked on and finishes its own
        teardown; double-closing is harmless throughout.
        """
        if self._thread is None:
            return
        self._stopping = True
        try:
            self._wake_w.send(b"x")
        except OSError:
            pass
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            self._log("RS232 ESP emulation: the worker is slow to stop; "
                      "forcing its sockets closed.")
            # Break whatever it is blocked on: a closed socket fails the
            # call immediately, and every failure path lands in teardown.
            for s in ([self._serial, self._net_listen]
                      + list(self._links.values())):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
        self._thread = None
        if self._listen is not None:
            try:
                self._listen.close()
            except OSError:
                pass
        self._log("RS232 ESP emulation stopped.")

    # ---- the gateway contract (called by the engine, same thread) --------
    def net_connect(self, link, host, port):
        try:
            s = socket.create_connection((host, port), timeout=2)
        except OSError as ex:
            if self._verbose:
                self._log(f"RS232 ESP: connect {host}:{port} failed: {ex}")
            return False
        s.setblocking(False)
        self._links[link] = s
        self._sel.register(s, selectors.EVENT_READ, ("link", link, s))
        return True

    def net_send(self, link, data):
        s = self._links.get(link)
        if s is None:
            return False
        # The link sockets live non-blocking for the read side; sendall on
        # a non-blocking socket raises the moment the OS buffer fills, and
        # a fat upload would then read as a dead link. Flip to a bounded
        # blocking send for the write, restore after.
        try:
            s.settimeout(10.0)
            try:
                s.sendall(data)
            finally:
                s.setblocking(False)
            return True
        except OSError:
            self._drop_link(link, tell_engine=False)
            return False

    def net_close(self, link):
        self._drop_link(link, tell_engine=False)

    def net_server(self, enable, port):
        # A re-listen (the guest reset and came back, or simply asked
        # again) REPLACES the existing listener: without the close-first,
        # Linux refuses the second bind and Windows silently double-binds
        # and leaks the old socket.
        if self._net_listen is not None:
            try:
                self._sel.unregister(self._net_listen)
            except (KeyError, ValueError):
                pass
            self._net_listen.close()
            self._net_listen = None
        if not enable:
            return True
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", int(port)))
            srv.listen(MAX_LINKS)
        except OSError as ex:
            if self._verbose:
                self._log(f"RS232 ESP: CIPSERVER bind {port} failed: {ex}")
            return False
        srv.setblocking(False)
        self._net_listen = srv
        self._sel.register(srv, selectors.EVENT_READ, ("accept-net", srv))
        return True

    # ---- the loop --------------------------------------------------------
    def _run(self):
        try:
            while not self._stopping:
                for key, events in self._sel.select(self._POLL_S):
                    self._handle(key, events)
                self._flush_serial()
        except Exception as ex:                       # noqa: BLE001
            self._log(f"RS232 ESP emulation error: {ex}")
        finally:
            self._teardown()

    def _handle(self, key, events):
        tag = key.data[0]
        if tag == "wake":
            try:
                self._wake_r.recv(64)
            except OSError:
                pass
        elif tag == "accept-serial":
            self._accept_serial()
        elif tag == "serial":
            # Guard against a STALE event: an earlier event in this very
            # select() batch may have replaced the serial socket (a
            # reconnecting MAME races its own predecessor), and acting on
            # the old one would read EOF from a dead socket and tear the
            # fresh connection down.
            if key.data[1] is not self._serial:
                return
            if events & selectors.EVENT_WRITE:
                self._drain_serial()
            if events & selectors.EVENT_READ:
                self._read_serial()
        elif tag == "accept-net":
            if key.data[1] is not self._net_listen:
                return                    # replaced by a re-listen
            self._accept_net()
        elif tag == "link":
            link, sock = key.data[1], key.data[2]
            if self._links.get(link) is not sock:
                return                    # closed/reused id, stale event
            self._read_link(link)

    def _accept_serial(self):
        try:
            conn, addr = self._listen.accept()
        except OSError:
            return
        if self._serial is not None:
            # A newer MAME replaces an older one (an emulator restart is
            # routine); the fresh session gets a fresh engine so no link
            # or half-armed CIPSEND leaks across.
            self._log("RS232 ESP emulation: a new emulator connection "
                      "replaces the previous one.")
            self._drop_serial()
        conn.setblocking(False)
        self._serial = conn
        self._sel.register(conn, selectors.EVENT_READ, ("serial", conn))
        self._reset_links()
        self._serial_out.clear()
        self._engine = EspAtEngine(self, log=self._log, verbose=self._verbose)
        self._log(f"RS232 ESP emulation: emulator connected from {addr[0]}:{addr[1]}.")

    def _read_serial(self):
        try:
            data = self._serial.recv(4096)
        except (BlockingIOError, InterruptedError):
            return                        # spurious wake, not a disconnect
        except OSError:
            data = b""
        if not data:
            self._log("RS232 ESP emulation: emulator disconnected.")
            self._drop_serial()
            self._reset_links()
            return
        self._engine.feed_serial(data)

    def _accept_net(self):
        try:
            conn, addr = self._net_listen.accept()
        except OSError:
            return
        link = next((i for i in range(MAX_LINKS) if i not in self._links), None)
        if link is None:
            conn.close()
            return
        conn.setblocking(False)
        self._links[link] = conn
        self._sel.register(conn, selectors.EVENT_READ, ("link", link, conn))
        self._engine.net_accepted(link)
        if self._verbose:
            self._log(f"RS232 ESP: inbound client {addr[0]}:{addr[1]} -> link {link}")

    def _read_link(self, link):
        s = self._links.get(link)
        if s is None:
            return
        try:
            data = s.recv(IPD_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self._drop_link(link, tell_engine=True)
            return
        self._engine.net_data(link, data)

    # ---- serial write queue + flow control -------------------------------
    def _flush_serial(self):
        """Move engine output into the write queue and push what fits.

        MAME drains this socket at emulated UART pace while the LAN fills
        it at LAN pace, so the write is a QUEUE, never a blocking send: a
        full kernel buffer parks the remainder for the next writable
        event instead of reading as a lost emulator connection — the
        exact failure a 100+ KB HTTP body used to hit.
        """
        if self._engine is None or self._serial is None:
            return
        out = self._engine.take_output()
        if out:
            self._serial_out += out
        self._drain_serial()

    def _drain_serial(self):
        if self._serial is None:
            return
        if self._serial_out:
            try:
                sent = self._serial.send(self._serial_out)
                if sent:
                    del self._serial_out[:sent]
            except (BlockingIOError, InterruptedError):
                pass                      # kernel buffer full: wait writable
            except OSError:
                self._log("RS232 ESP emulation: emulator connection lost mid-write.")
                self._drop_serial()
                self._reset_links()
                return
        self._update_serial_interest()
        self._apply_backpressure()

    def _update_serial_interest(self):
        """Watch the serial socket for writability only while the queue
        holds bytes — a permanently-writable socket would turn the select
        into a busy loop."""
        if self._serial is None:
            return
        want = selectors.EVENT_READ
        if self._serial_out:
            want |= selectors.EVENT_WRITE
        try:
            key = self._sel.get_key(self._serial)
            if key.events != want:
                self._sel.modify(self._serial, want, ("serial", self._serial))
        except KeyError:
            pass

    def _apply_backpressure(self):
        """Pause the upstream link reads while the serial queue is deep.

        Reading the LAN at LAN speed while writing the guest at UART
        speed would grow the queue without bound; pausing above a high
        water mark and resuming below a low one bounds it to tens of KB
        and lets TCP push the pressure back to the sender."""
        depth = len(self._serial_out)
        if depth > SERIAL_HIGH_WATER:
            for link, s in self._links.items():
                if link not in self._paused_links:
                    try:
                        self._sel.unregister(s)
                    except (KeyError, ValueError):
                        continue
                    self._paused_links.add(link)
        elif depth < SERIAL_LOW_WATER and self._paused_links:
            for link in list(self._paused_links):
                s = self._links.get(link)
                if s is not None:
                    try:
                        self._sel.register(
                            s, selectors.EVENT_READ, ("link", link, s))
                    except (KeyError, ValueError):
                        pass
                self._paused_links.discard(link)

    # ---- plumbing --------------------------------------------------------
    def _drop_serial(self):
        if self._serial is None:
            return
        try:
            self._sel.unregister(self._serial)
        except (KeyError, ValueError):
            pass
        self._serial.close()
        self._serial = None
        self._serial_out.clear()

    def _drop_link(self, link, tell_engine):
        s = self._links.pop(link, None)
        self._paused_links.discard(link)
        if s is None:
            return
        try:
            self._sel.unregister(s)
        except (KeyError, ValueError):
            pass
        s.close()
        if tell_engine:
            self._engine.net_closed(link)

    def _reset_links(self):
        for link in list(self._links):
            self._drop_link(link, tell_engine=False)
        self._paused_links.clear()
        self.net_server(False, 0)

    def _teardown(self):
        self._drop_serial()
        self._reset_links()
        for s in (self._listen, self._wake_r, self._wake_w):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if self._sel is not None:
            self._sel.close()


def main(argv=None):
    """Standalone console entry point: ``python espemu.py [--port N] [-v]``."""
    import argparse
    parser = argparse.ArgumentParser(
        description="ESP-AT emulator for MAME's -rs232_esp bitbanger socket")
    parser.add_argument("--port", "-p", type=int, default=2222,
                        help="TCP port to listen on (default 2222)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="trace commands and traffic")
    args = parser.parse_args(argv)
    server = EspAtServer(port=args.port, log=print, verbose=args.verbose)
    server.start()
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
