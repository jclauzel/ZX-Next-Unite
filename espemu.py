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
* :class:`EspAtServer` — the socket harness: an accept loop on a background
  thread, plus one ``selectors`` loop thread per connected emulator wiring
  its serial client (MAME) and its engine's TCP links together. ``start()``
  binds and listens SYNCHRONOUSLY so the caller can Popen MAME immediately
  after; ``stop()`` tears everything down. A server object is SINGLE-USE:
  start it once, then build a new one.

ONE server, MANY emulators, A THREAD EACH: every MAME that connects to the
listener is forked off into its OWN session — its own engine, links,
CIPSERVER listener, serial queue, selector and loop thread — so several
emulated Nexts (each with its own disk image) run side by side through a
single proxy and never see a byte of each other's AT state. The session,
not the server, is the unit of freshness: it is built on connect and
dropped when that emulator quits.

The thread each is not decoration. Serving every session from one loop was
tried first and measured: a malformed ``AT+CIPSTART`` host raises out of
getaddrinfo as a UnicodeEncodeError (NOT an OSError), which tore the loop
down and took every other emulator's wire with it; and the two bounded
BLOCKING gateway calls — the connect, the send — stalled bystander
emulators for up to 9.7 s, where the Next side gives up after ~3 s and
resets its module. With a thread each, both stay strictly inside the
session that caused them, and every session behaves exactly like the
single-emulator server this module shipped with.

The one genuinely shared resource left is the PC's port space, so when two
guests ask CIPSERVER for the same port the later one is remapped upwards
and the new port is logged.

THE STATION ADDRESS (9.7.54). Nothing routes through the IP address the
module reports (``AT+CIFSR`` / ``AT+CIPSTA?``) - the real routing is this
host's own stack - but it is what the Next-side software SHOWS (ZX Next
Remote's listener screen, the NextZXOS WiFi wizard's "IP Address:" line)
and what someone on ANOTHER PC types to reach the emulated Next, whose
CIPSERVER listener binds every interface of this host. So the server
reports one of THIS HOST'S addresses: the one it was given
(``station_ip=``, the app's Settings pick), else the host's primary
outbound address (:func:`primary_ipv4`, the interface the default route
uses - the same trick MAME's own ``esp8266_at`` card plays for its "All
interfaces" mode), else loopback. Before 9.7.54 every emulated Next claimed
the made-up ``192.168.4.2``. A guest's own ``AT+CIPSTA="..."`` overrides it
for that session (as the wizard's "Set Manual IP" does on hardware) until
``AT+RST`` or a station-DHCP ``AT+CWDHCP`` hands the address back, and
:meth:`EspAtServer.set_station_ip` moves a RUNNING server, live sessions
included, so a Settings change needs no relaunch. Only an address a host
interface could carry is ever reported (:func:`is_station_ipv4`): ZX Next
Remote and QL Next Remote read ``0.0.0.0`` as "the module has not joined
yet" and would wait for ever, so it - like a broadcast or a multicast
address - is refused at every door (the server, the Settings box, the
saved setting, a guest's own ``AT+CIPSTA=``).

The Wi-Fi side is a FICTION with a consistent story: the module is always
joined to an access point named ``STA_SSID`` (``AT+CWJAP?`` names it,
``AT+CWLAP`` lists it, a ``AT+CWJAP="x","y"`` join is accepted and
remembered), because the NextZXOS WiFi wizard slices the SSID out of the
``AT+CWJAP?`` reply with a fixed-shape string routine and dies with
"B Integer out of range" on the EMPTY reply a bare OK gave it. The DNS
commands - ``AT+CIPDNS`` and its ``_CUR`` / ``_DEF`` forms - answer ERROR
(there is no DNS here to report: a ``CIPSTART`` host name is resolved by
this PC's own resolver; the bare ``CIPDNS`` is the AT 2.x spelling an AT
1.x module does not know, and MAME's ``esp8266_at`` card refuses all
three), so the wizard's DNS line stays blank instead of showing an invented
one; so does ``CIUPDATE``, an OTA firmware update no emulation can fake.
``AT+GMR`` answers the AT 1.x three-line shape the wizard slices its
"Firmware:" lines out of. Of the shipped consumers only ZX Next Remote and
QL Next Remote read the address (``AT+CIFSR``); the ``.sync5`` dot never
asks for it.

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
rates left at 115200. The Next's UART in MAME follows its prescaler, so the
Next software's fast-baud paths really do move the wire to 1152000 or
2000000 baud — and the null_modem card this proxy sits behind is pinned to
its Machine Configuration baud (115200 at most), so after the switch the two
ends cannot hear each other and the dot reports "No fast esp". Since 9.7.48
the app does not use this proxy where it can avoid it: a MAME build that
offers the built-in ``esp8266_at`` card (holub/mame PR #9, October 2026,
whose serial rate follows the dot's AT+UART_CUR) is launched with that card
instead, and every speed works there. This module stays the path for every
other MAME (see ``MAME_ESP_*`` in zxnu_config).

Standalone use (the app drives it in-process; this is for testing outside)::

    python espemu.py --port 2222 --verbose [--ip 10.0.0.5]

Clean-room note: the IDEA of proxying MAME's ESP socket to the real network
comes from Janko Stamenović's jesperl project
(https://sourceforge.net/projects/jesperl/, GNU Affero GPL v3). This module
shares no code, structure or text with jesperl: it is an independent
implementation written from the public AT specification and this project's
own Next-side clients, and is covered by ZX-Next-Unite's MIT license.
"""

import os
import re
import selectors
import socket
import subprocess
import threading
import time

ESPEMU_VERSION = "1.3"

# The station identity handed to AT+CIFSR / AT+CIPSTA?. Nothing ROUTES
# through the address (the real routing is this host's own IP stack), but
# the Next software SHOWS it and a user on another machine may TYPE it, so
# a server reports one of this host's real addresses (see the module
# docstring, "THE STATION ADDRESS"). ``STA_IP`` is only the BARE engine's
# default - an engine built without a server, i.e. the tests - so that
# layer's replies stay deterministic; it is the pre-9.7.54 made-up address.
STA_IP = "192.168.4.2"
STA_MAC = "5e:cf:7f:00:00:01"
# The access point the emulated module is always joined to - what
# AT+CWJAP? names and AT+CWLAP lists, with the BSSID beside it. There is no
# Wi-Fi here, so the SSID says what the "network" is; a JOINED module is
# what the consumers expect (the NextZXOS WiFi wizard crashes on an empty
# CWJAP? reply, see the module docstring).
STA_SSID = "ZX-Next-Unite"
AP_MAC = "5e:cf:7f:00:00:02"
STA_NETMASK = "255.255.255.0"
LOOPBACK_IP = "127.0.0.1"
# Where the outbound-interface probe points: TEST-NET-1 (RFC 5737), never
# routed on the public Internet. The UDP "connect" sends NOTHING - it only
# asks the routing table which interface would carry a packet there -
# which is MAME's esp8266_at card's own way of learning the host address.
PRIMARY_PROBE_HOST = "192.0.2.1"
# How long the host-address enumeration may block (a hostname lookup
# against a broken resolver can stall for many seconds, and this runs on
# the app's UI thread when the Settings list opens).
HOST_ADDRESS_TIMEOUT_S = 1.5

# AT+CIPSTA / AT+CIPSTA_CUR / AT+CIPSTA_DEF, query (?) or set (=) - and
# NOT AT+CIPSTAMAC (its own) nor AT+CIPSTATUS / AT+CIPSTART (routed before
# or left to the polite default). Matched on the upper-cased command.
_CIPSTA_RE = re.compile(r"^AT\+(CIPSTA(?:_CUR|_DEF)?)(\?|=)")
_CIPSTAMAC_RE = re.compile(r"^AT\+(CIPSTAMAC(?:_CUR|_DEF)?)\?$")
# The Wi-Fi fiction's commands, matched by their EXACT names (with the AT
# 1.x _CUR/_DEF spellings): a bare prefix also caught AT+CWLAPOPT (the scan
# options) and AT+CWDHCPS (the softAP's DHCP range), which are other
# commands and take the polite default.
_CWJAP_RE = re.compile(r"^AT\+CWJAP(?:_CUR|_DEF)?(?:\?|=|$)")
_CWMODE_RE = re.compile(r"^AT\+CWMODE(?:_CUR|_DEF)?(?:\?|=|$)")
_CWDHCP_RE = re.compile(r"^AT\+CWDHCP(?:_CUR|_DEF)?(?:\?|=|$)")
_CIPDNS_RE = re.compile(r"^AT\+CIPDNS(?:_CUR|_DEF)?(?:\?|=|$)")
# The first parameter of a set command when it is a QUOTED string, with
# the firmware's backslash escapes (\" \, \\) inside it.
_QUOTED_ARG_RE = re.compile(r'\s*"((?:[^"\\]|\\.)*)"')

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

# Serial-side flow control, PER SESSION: above HIGH that emulator's
# upstream link reads pause; below LOW they resume. The band is wide so the
# pause toggles rarely, and one busy emulator throttles only itself.
SERIAL_HIGH_WATER = 64 * 1024
SERIAL_LOW_WATER = 8 * 1024

# AT link ids run 0..4 on the real firmware. Per emulator session.
MAX_LINKS = 5

# How many emulators may share one server at once. Several MAME instances
# connecting to the same listener is the supported case (one per emulated
# Next), each with its own session; the cap only stops a runaway reconnect
# loop from growing the process without bound.
MAX_SESSIONS = 8

# The two BLOCKING gateway calls, both bounded so a dead peer cannot wedge
# an emulator's wire for ever. They run on the OWNING session's thread, so
# the stall is self-inflicted: no other emulator is affected.
CONNECT_TIMEOUT_S = 2.0
SEND_TIMEOUT_S = 10.0

# Two emulated Nexts running the same software both ask CIPSERVER for the
# same port, and only one can own it on this PC. The later one is remapped
# to the first free port in this window above the requested one - and told
# so, loudly, in the log.
CIPSERVER_PORT_SEARCH = 10

CRLF = b"\r\n"


def is_ipv4(text):
    """True for a dotted quad - four decimal octets 0-255, no leading
    zeros (inet_aton would read ``010`` as octal) - and for nothing else:
    not a host name, not IPv6, not a quad with a port."""
    parts = str(text or "").strip().split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not (p.isascii() and p.isdigit()) or len(p) > 3:
            return False
        if len(p) > 1 and p[0] == "0":
            return False
        if int(p) > 255:
            return False
    return True


def is_station_ipv4(text):
    """True for a dotted quad a host interface could carry - what a Next
    may be told is its own address. :func:`is_ipv4` plus three refusals:
    ``0.x.x.x`` (``0.0.0.0`` is what an UNJOINED module reports: ZX Next
    Remote and QL Next Remote wait for any other value before they listen,
    so it would hold them for ever), the multicast range 224-239 and the
    reserved 240-255 (the broadcast ``255.255.255.255`` among them).
    Loopback (127.x) is allowed on purpose: it is the automatic address of
    a PC with no network, and right for a client on this same PC."""
    if not is_ipv4(text):
        return False
    first = int(str(text).strip().split(".")[0])
    return 0 < first < 224


def _at_quoted_arg(text):
    r"""The first parameter of an AT set command when it is a QUOTED string,
    with the firmware's backslash escapes (``\"``, ``\,``, ``\\``) undone;
    None when it is not quoted. A comma inside the quotes belongs to the
    value - splitting on commas first cut an SSID such as ``My,Net``
    short."""
    m = _QUOTED_ARG_RE.match(text or "")
    if not m:
        return None
    return re.sub(r"\\(.)", r"\1", m.group(1))


def primary_ipv4(probe_host=PRIMARY_PROBE_HOST):
    """This host's primary outbound IPv4 address - the interface its
    default route uses - or None when it has no route at all (no network,
    or loopback only). No packet leaves: a UDP socket's connect() only
    consults the routing table, and getsockname() answers with the address
    of the interface it picked. Never raises."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((probe_host, 9))
            ip = s.getsockname()[0]
    except (OSError, ValueError):
        return None
    if not is_station_ipv4(ip) or ip.startswith("127."):
        return None
    return ip


def host_ipv4_addresses(timeout=HOST_ADDRESS_TIMEOUT_S):
    """Every IPv4 address this host carries, the primary outbound one
    first, loopback and 0.0.0.0 left out, no duplicates. Never raises and
    is bounded in time: the hostname lookup runs in a daemon thread that
    is abandoned past ``timeout`` (a misconfigured resolver can stall the
    C resolver for many seconds), and the POSIX interface listing is a
    subprocess with the same timeout. Three sources, because no single one
    sees every adapter everywhere: the routing table's pick, the host
    name's addresses (on Windows that is every adapter, virtual ones
    included - the case this exists for), and on POSIX ``ip -4 -o addr``
    or, where there is no ``ip`` (macOS), ``ifconfig``."""
    found = []

    def add(ip):
        ip = str(ip or "").strip()
        if is_station_ipv4(ip) and not ip.startswith("127.") and ip not in found:
            found.append(ip)

    add(primary_ipv4())
    looked_up = []

    def _lookup():
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET, socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return
        looked_up.extend(info[4][0] for info in infos)

    t = threading.Thread(target=_lookup, daemon=True, name="espemu-hostaddrs")
    t.start()
    t.join(timeout)
    for ip in list(looked_up):
        add(ip)
    if os.name != "nt":
        for argv in (["ip", "-4", "-o", "addr", "show"], ["ifconfig"]):
            try:
                out = subprocess.run(argv, capture_output=True, timeout=timeout,
                                     encoding="utf-8", errors="replace").stdout
            except (OSError, subprocess.SubprocessError, ValueError):
                continue
            for m in re.finditer(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})", out or ""):
                add(m.group(1))
            break
    return found


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

    def __init__(self, gateway, log=None, verbose=False, station_ip=None):
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
        # The address CIFSR / CIPSTA? report. A plain attribute on purpose:
        # the server writes it from another thread when Settings move the
        # address (one str assignment, atomic enough), and a guest's own
        # AT+CIPSTA= overrides it for this session until RST / CWDHCP.
        self.station_ip = (station_ip if is_station_ipv4(station_ip)
                           else STA_IP)
        self._sta_override = None
        self._ssid = STA_SSID            # the AP the module says it joined
        self.emit(b"\r\nready\r\n")      # the boot banner; drained as noise

    @property
    def reported_ip(self):
        """The address the module reports right now: a guest's AT+CIPSTA=
        override while one is set, else the configured station address."""
        return self._sta_override or self.station_ip

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
        if _CIPSTA_RE.match(up):             # CIPSTA / _CUR / _DEF, ? or =
            return self._at_cipsta
        if _CIPSTAMAC_RE.match(up):
            return self._at_cipstamac
        if _CWJAP_RE.match(up):
            return self._at_cwjap
        if up == "AT+CWLAP" or up.startswith("AT+CWLAP="):
            return self._at_cwlap
        if _CWMODE_RE.match(up):
            return self._at_cwmode
        if _CWDHCP_RE.match(up):
            return self._at_cwdhcp
        if _CIPDNS_RE.match(up) or up.startswith("AT+CIUPDATE"):
            # Not implemented, and SAID so (see the module docstring):
            # there is no DNS here to report, and an OTA firmware update
            # is not something an emulation can pretend to do. The
            # NextZXOS WiFi wizard reads the ERROR as "no DNS to show".
            return self._at_error
        if up.startswith("AT+UART"):
            return self._at_uart
        if up.startswith("AT"):
            # Anything else in the AT family (CIPSTO, CIPDINFO, CWSAP,
            # the _CUR/_DEF spellings of commands above...): agree
            # politely. The consumers only ever wait for the OK;
            # inventing per-command fictions adds nothing.
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
        self._sta_override = None
        self._ssid = STA_SSID
        self._gw.net_server(False, 0)
        self.emit(b"\r\nOK\r\n\r\nready\r\n")
        self._trace("reset to defaults")

    def _at_gmr(self, _cmd):
        # The AT 1.x three-line shape: the NextZXOS WiFi wizard slices its
        # "Firmware:" lines out of it - from the first ':' to the first CR,
        # then from that CR to the NEXT one, which is why a third line has
        # to follow (its pos() never matches the last character). The
        # version leads with the emulator's NAME, never a bare number, so
        # nothing takes this for an Espressif build whose features it lacks.
        self.emit(f"\r\nAT version:espemu-{ESPEMU_VERSION}(zx-next-unite)\r\n"
                  f"SDK version:RS232-ESP-emulation\r\n"
                  f"compile time:zx-next-unite\r\n\r\nOK\r\n".encode("ascii"))

    def _at_cifsr(self, _cmd):
        # STAIP line strictly BEFORE the OK: the reply is parsed after the
        # OK is matched, from everything collected so far.
        self.emit(f"\r\n+CIFSR:STAIP,\"{self.reported_ip}\"\r\n"
                  f"+CIFSR:STAMAC,\"{STA_MAC}\"\r\n\r\nOK\r\n"
                  .encode("ascii"))

    def _at_cipsta(self, cmd):
        # AT+CIPSTA? (and the _CUR / _DEF spellings) answer the AT 1.x
        # three-line shape - ip, gateway, netmask, each prefixed with the
        # command's own name - the one a real Next's module and MAME's
        # esp8266_at card give; the ip is the first quoted value either
        # way, which is all the WiFi wizard reads. The SET form takes a
        # quoted address (gateway and netmask optional, ignored) and makes
        # it THIS session's reported address, exactly what the wizard's
        # "Set Manual IP" does on hardware; anything else - unquoted, not
        # a dotted quad, or an address no interface carries (0.0.0.0, a
        # broadcast, a multicast) - is ERROR, like the firmware's.
        m = _CIPSTA_RE.match(cmd.strip().upper())
        name = m.group(1)
        if m.group(2) == "?":
            ip = self.reported_ip
            gateway = ip.rsplit(".", 1)[0] + ".1"
            self.emit(f"\r\n+{name}:ip:\"{ip}\"\r\n"
                      f"+{name}:gateway:\"{gateway}\"\r\n"
                      f"+{name}:netmask:\"{STA_NETMASK}\"\r\n\r\nOK\r\n"
                      .encode("ascii"))
            return
        want = _at_quoted_arg(cmd.split("=", 1)[1])
        if not is_station_ipv4(want):
            self.emit(b"\r\nERROR\r\n")
            return
        want = want.strip()
        self._sta_override = want
        self.emit(b"\r\nOK\r\n")
        self._trace(f"station address set by the guest to {want} "
                    f"(this session only; AT+RST or a station DHCP "
                    f"AT+CWDHCP hand it back)")

    def _at_cipstamac(self, cmd):
        name = _CIPSTAMAC_RE.match(cmd.strip().upper()).group(1)
        self.emit(f"\r\n+{name}:\"{STA_MAC}\"\r\n\r\nOK\r\n".encode("ascii"))

    def _at_cwjap(self, cmd):
        # The module is ALWAYS joined: the query names the access point in
        # the AT 1.x shape (ssid, bssid, channel, rssi), and a join is
        # accepted with the firmware's own chatter and remembered as the
        # name the module now reports. An empty reply here is what crashed
        # the NextZXOS WiFi wizard (see the module docstring). The SSID is
        # read with the AT quoting grammar - the first QUOTED parameter,
        # backslash escapes undone - so a name with a comma in it is not
        # cut short; an unquoted one is ERROR, as on the firmware. The test
        # form (=?) only says OK.
        up = cmd.strip().upper()
        if up.endswith("=?"):
            self.emit(b"\r\nOK\r\n")
            return
        if "=" in up:
            ssid = _at_quoted_arg(cmd.split("=", 1)[1])
            if not ssid or len(ssid) > 32:
                self.emit(b"\r\nERROR\r\n")
                return
            self._ssid = ssid
            self.emit(b"\r\nWIFI CONNECTED\r\nWIFI GOT IP\r\n\r\nOK\r\n")
            self._trace(f"join accepted: the module now says it is on '{ssid}'")
            return
        if not up.endswith("?"):
            self.emit(b"\r\nERROR\r\n")
            return
        name = up[3:-1]                  # CWJAP / CWJAP_CUR / CWJAP_DEF
        # Re-escaped the way it came in, so the quoted field stays whole.
        shown = self._ssid.replace("\\", "\\\\").replace('"', '\\"')
        self.emit(f"\r\n+{name}:\"{shown}\",\"{AP_MAC}\",1,-40"
                  f"\r\n\r\nOK\r\n".encode("ascii"))

    def _at_cwlap(self, cmd):
        # One network in range, open, strong: the fiction's own AP. The
        # filtered form (AT+CWLAP="<ssid>",...) lists it only when it is
        # the one asked for.
        if "=" in cmd:
            want = _at_quoted_arg(cmd.split("=", 1)[1])
            if want is not None and want != STA_SSID:
                self.emit(b"\r\nOK\r\n")
                return
        self.emit(f"\r\n+CWLAP:(0,\"{STA_SSID}\",-40,\"{AP_MAC}\",1,0)"
                  f"\r\n\r\nOK\r\n".encode("ascii"))

    def _at_cwmode(self, cmd):
        # Station mode, and only station mode: the query says 1, the test
        # form lists the firmware's three, a set of 1-3 is agreed to.
        up = cmd.strip().upper()
        if up.endswith("=?"):
            self.emit(f"\r\n+{up[3:-2]}:(1-3)\r\n\r\nOK\r\n".encode("ascii"))
        elif up.endswith("?"):
            self.emit(f"\r\n+{up[3:-1]}:1\r\n\r\nOK\r\n".encode("ascii"))
        elif "=" in up and up.split("=", 1)[1].strip() in ("1", "2", "3"):
            self.emit(b"\r\nOK\r\n")
        else:
            self.emit(b"\r\nERROR\r\n")

    def _at_cwdhcp(self, cmd):
        # DHCP turned back ON for the station = the guest's static address
        # is given back: the wizard's "Set automatic IP/DNS" sends
        # AT+CWDHCP=17,1 (WIFI.BAS: =1,1). Its "Set Manual IP" and "Set
        # Manual DNS" both send =0,1 first, and that must NOT: under the
        # AT 1.x reading (<mode>,<en>) it enables the softAP's DHCP, under
        # the 2.x one (<operate>,<mode>) it DISABLES the station's - either
        # way the station keeps its address, so on hardware a manual IP
        # set from one menu survives the other. Hence: the address comes
        # back only when BOTH numbers are non-zero. Anything but two
        # integers is ERROR. The query reports DHCP on for both
        # interfaces, as the firmware does.
        up = cmd.strip().upper()
        if up.endswith("?"):
            self.emit(f"\r\n+{up[3:-1]}:3\r\n\r\nOK\r\n".encode("ascii"))
            return
        try:
            args = [int(a) for a in up.split("=", 1)[1].split(",")]
        except (IndexError, ValueError):
            self.emit(b"\r\nERROR\r\n")
            return
        if len(args) != 2:
            self.emit(b"\r\nERROR\r\n")
            return
        if args[0] and args[1]:
            if self._sta_override is not None:
                self._trace("station address handed back to the configured "
                            "one (DHCP re-enabled by the guest)")
            self._sta_override = None
        self.emit(b"\r\nOK\r\n")

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


class _EspSession:
    """One emulator connection: its socket, its engine, its links — and its
    OWN loop thread.

    Every MAME that connects to the listener gets one of these. A session is
    a complete little server in itself: its own selector, its own engine,
    its own links and CIPSERVER listener, its own serial queue and its own
    thread. That isolation is the whole design, and it is deliberate rather
    than merely tidy — the alternative (one loop thread serving every
    session) makes one guest able to hurt all the others in two ways that
    were both measured before this shape was chosen:

    * an exception escaping ONE session's event handling (a malformed
      ``AT+CIPSTART`` host raises UnicodeEncodeError out of getaddrinfo,
      which is NOT an OSError) tore the loop down and took every other
      emulator's wire with it;
    * the two BLOCKING gateway calls (the bounded connect, the bounded
      send) stalled every other emulator's UART - 9.7 s measured with a
      wedged peer, where the Next side gives up after ~3 s and resets its
      module.

    With a thread each, both stay strictly inside the session that caused
    them, and every session behaves exactly like the single-emulator server
    this module shipped with before several MAMEs were supported.

    A session is also the unit of FRESHNESS: it is built when the emulator
    connects and dropped when that emulator goes away, which is what the
    old one-server-per-launch rule was really buying.
    """

    _POLL_S = 0.2       # loop heartbeat when nothing is happening

    def __init__(self, server, sock, addr, index):
        self.server = server
        self.sock = sock
        self.addr = addr
        self.index = index               # 1-based, only used for labelling
        self.out = bytearray()           # queued serial-bound bytes
        self.links = {}                  # link id -> connected TCP socket
        self.paused = set()              # reads paused by serial backpressure
        self.net_listen = None           # this guest's CIPSERVER listener
        self.net_listen_port = None      # the port it actually got
        self.closed = False
        self._sel = selectors.DefaultSelector()
        self._wake_r = None
        self._wake_w = None
        self._thread = None
        self._stopping = False
        self._close_lock = threading.Lock()
        # The address is resolved per SESSION (on connect): "automatic"
        # follows the host's routing table as it is when this emulator
        # joins, and set_station_ip() rewrites it live afterwards.
        # station_cfg is the CONFIGURED value this session resolved from:
        # the server re-checks it under its session lock when the session
        # joins the list, so a set_station_ip() landing while this session
        # was being built (it is not in the list for the live rewrite yet)
        # is never missed - measured: without the check that session kept
        # the old address for its whole life.
        self.station_cfg = server.station_ip
        self.engine = EspAtEngine(self, log=self.log,
                                  verbose=server._verbose,
                                  station_ip=server._resolve(self.station_cfg))

    def log(self, text):
        """A session-scoped log line.

        The ``[emulator N]`` label appears only once a second emulator has
        actually shared this server, so the single-MAME case — still the
        common one — reads exactly as it always did.
        """
        self.server._log(f"{self.server._label(self)}{text}")

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        """Register the serial socket and run this session's loop."""
        self.sock.setblocking(False)
        self._sel.register(self.sock, selectors.EVENT_READ,
                           ("serial", self.sock))
        # Self-pipe so stop() can interrupt a quiet select() immediately.
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._sel.register(self._wake_r, selectors.EVENT_READ, ("wake",))
        self._thread = threading.Thread(
            target=self._run, name=f"espemu-emu{self.index}", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self._stopping:
                for key, events in self._sel.select(self._POLL_S):
                    try:
                        self._handle(key, events)
                    except Exception as ex:               # noqa: BLE001
                        # Defence in depth: one bad event must cost one
                        # event, never this emulator's whole session (and,
                        # with a thread each, never anybody else's).
                        self.log("RS232 ESP emulation: ignoring a failed "
                                 f"event: {ex}")
                if self.closed:
                    break
                self.flush_serial()
        except Exception as ex:                           # noqa: BLE001
            self.log(f"RS232 ESP emulation: session error: {ex}")
        finally:
            self.close()
            self.server._forget(self)

    def stop(self):
        """Called from ANOTHER thread (the server's teardown): end this
        session and join its loop, bounded, then make sure its sockets are
        shut whatever the join did."""
        thread = self._thread
        self._stopping = True
        if self._wake_w is not None:
            try:
                self._wake_w.send(b"x")
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
            if thread.is_alive():
                # Break whatever it is blocked on: a closed socket fails
                # the call immediately and every failure path ends in
                # close(). Double-closing is harmless throughout.
                for s in ([self.sock, self.net_listen]
                          + list(self.links.values())):
                    if s is not None:
                        try:
                            s.close()
                        except OSError:
                            pass
        self.close()

    def _handle(self, key, events):
        tag = key.data[0]
        if tag == "wake":
            try:
                self._wake_r.recv(64)
            except OSError:
                pass
        elif tag == "serial":
            # Identity guard: an earlier event in this very select() batch
            # may have closed the session, and acting on the dead socket
            # would work on freed state.
            if self.closed or self.sock is not key.data[1]:
                return
            if events & selectors.EVENT_WRITE:
                self.drain_serial()
            if events & selectors.EVENT_READ and not self.closed:
                self._read_serial()
        elif tag == "accept-net":
            if self.closed or self.net_listen is not key.data[1]:
                return                    # replaced by a re-listen
            self._accept_net()
        elif tag == "link":
            link, sock = key.data[1], key.data[2]
            if self.closed or self.links.get(link) is not sock:
                return                    # closed/reused id, stale event
            self._read_link(link)

    def _read_serial(self):
        try:
            data = self.sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return                        # spurious wake, not a disconnect
        except OSError:
            data = b""
        if not data:
            self.log("RS232 ESP emulation: emulator disconnected.")
            self.close()
            return
        self.engine.feed_serial(data)

    def _accept_net(self):
        try:
            conn, addr = self.net_listen.accept()
        except OSError:
            return
        link = next((i for i in range(MAX_LINKS) if i not in self.links), None)
        if link is None:
            conn.close()
            return
        conn.setblocking(False)
        self.links[link] = conn
        self._sel.register(conn, selectors.EVENT_READ, ("link", link, conn))
        self.engine.net_accepted(link)
        if self.server._verbose:
            self.log(f"RS232 ESP: inbound client {addr[0]}:{addr[1]} "
                     f"-> link {link}")

    def _read_link(self, link):
        s = self.links.get(link)
        if s is None:
            return
        try:
            data = s.recv(IPD_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self.drop_link(link, tell_engine=True)
            return
        self.engine.net_data(link, data)

    # ---- the gateway contract (called by OUR engine, on OUR thread) ------
    # These run on this session's loop thread, so they may touch its
    # selector directly. The two BLOCKING calls in here (the connect
    # timeout, the bounded send) freeze THIS emulator's wire while they
    # run and nobody else's - the reason each session owns a thread.
    def net_connect(self, link, host, port):
        try:
            s = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
        except Exception as ex:                           # noqa: BLE001
            # NOT just OSError: create_connection resolves the name, and
            # a guest-supplied host with an empty or over-long DNS label
            # comes back as UnicodeEncodeError (a ValueError). Whatever
            # goes wrong, a bad CIPSTART means exactly one thing to the
            # guest - this connect failed - and must never escape onto a
            # loop thread.
            if self.server._verbose:
                self.log(f"RS232 ESP: connect {host}:{port} failed: {ex}")
            return False
        s.setblocking(False)
        self.links[link] = s
        self._sel.register(s, selectors.EVENT_READ, ("link", link, s))
        return True

    def net_send(self, link, data):
        s = self.links.get(link)
        if s is None:
            return False
        # The link sockets live non-blocking for the read side; sendall on
        # a non-blocking socket raises the moment the OS buffer fills, and
        # a fat upload would then read as a dead link. Flip to a bounded
        # blocking send for the write, restore after.
        try:
            s.settimeout(SEND_TIMEOUT_S)
            try:
                s.sendall(data)
            finally:
                s.setblocking(False)
            return True
        except OSError:
            self.drop_link(link, tell_engine=False)
            return False

    def net_close(self, link):
        self.drop_link(link, tell_engine=False)

    def net_server(self, enable, port):
        # A re-listen (the guest reset and came back, or simply asked
        # again) REPLACES this session's listener: without the
        # close-first, Linux refuses the second bind and Windows silently
        # double-binds and leaks the old socket.
        self.drop_net_listen()
        if not enable:
            return True
        want = int(port)
        if not 1 <= want <= 65535:
            # The guest picks this number; a nonsense one must come back
            # as ERROR, not as an OverflowError out of bind() (which the
            # OSError handler below would not catch and which would leave
            # the guest waiting for a reply that never comes).
            if self.server._verbose:
                self.log(f"RS232 ESP: CIPSERVER port {want} is out of range")
            return False
        srv = None
        chosen = None
        for candidate in range(want, min(want + CIPSERVER_PORT_SEARCH, 65536)):
            # Two guests asking for the same server port is NORMAL (they
            # run the same Next software); only one can own it on this
            # host, so the later one walks up to the next free port. The
            # server's claim table settles it between OUR sessions
            # deterministically - a bind alone would not on Windows, where
            # SO_REUSEADDR lets a second bind of a live listening port
            # succeed and then hands every connect to the first binder.
            if not self.server._claim_net_port(self, candidate):
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("", candidate))
                s.listen(MAX_LINKS)
            except OSError as ex:
                s.close()
                self.server._release_net_port(self, candidate)
                if self.server._verbose:
                    self.log(f"RS232 ESP: CIPSERVER bind {candidate} "
                             f"failed: {ex}")
                continue
            srv, chosen = s, candidate
            break
        if srv is None:
            self.log(f"RS232 ESP emulation: this emulator's CIPSERVER could "
                     f"not listen on port {want} (nor on the "
                     f"{CIPSERVER_PORT_SEARCH} ports above it).")
            return False
        if chosen != want:
            self.log(f"RS232 ESP emulation: CIPSERVER port {want} is already "
                     f"in use on this PC, so THIS emulator's server listens "
                     f"on {chosen} instead - connect to 127.0.0.1:{chosen} "
                     f"to reach it.")
        srv.setblocking(False)
        self.net_listen = srv
        self.net_listen_port = chosen
        self._sel.register(srv, selectors.EVENT_READ, ("accept-net", srv))
        return True

    # ---- serial write queue + flow control -------------------------------
    def flush_serial(self):
        """Move engine output into the write queue and push what fits.

        MAME drains this socket at emulated UART pace while the LAN fills
        it at LAN pace, so the write is a QUEUE, never a blocking send: a
        full kernel buffer parks the remainder for the next writable
        event instead of reading as a lost emulator connection — the
        exact failure a 100+ KB HTTP body used to hit.
        """
        if self.closed or self.sock is None:
            return
        out = self.engine.take_output()
        if out:
            self.out += out
        self.drain_serial()

    def drain_serial(self):
        if self.closed or self.sock is None:
            return
        if self.out:
            try:
                sent = self.sock.send(self.out)
                if sent:
                    del self.out[:sent]
            except (BlockingIOError, InterruptedError):
                pass                      # kernel buffer full: wait writable
            except OSError:
                self.log("RS232 ESP emulation: emulator connection lost "
                         "mid-write.")
                self.close()
                return
        self._update_serial_interest()
        self._apply_backpressure()

    def _update_serial_interest(self):
        """Watch the serial socket for writability only while the queue
        holds bytes — a permanently-writable socket would turn the select
        into a busy loop."""
        if self.closed or self.sock is None:
            return
        want = selectors.EVENT_READ
        if self.out:
            want |= selectors.EVENT_WRITE
        try:
            key = self._sel.get_key(self.sock)
            if key.events != want:
                self._sel.modify(self.sock, want, ("serial", self.sock))
        except KeyError:
            pass

    def _apply_backpressure(self):
        """Pause THIS session's link reads while its serial queue is deep.

        Reading the LAN at LAN speed while writing the guest at UART
        speed would grow the queue without bound; pausing above a high
        water mark and resuming below a low one bounds it to tens of KB
        and lets TCP push the pressure back to the sender. The band is
        per-session on purpose: one emulator hauling a big file must not
        throttle another's links."""
        depth = len(self.out)
        if depth > SERIAL_HIGH_WATER:
            for link, s in self.links.items():
                if link not in self.paused:
                    try:
                        self._sel.unregister(s)
                    except (KeyError, ValueError):
                        continue
                    self.paused.add(link)
        elif depth < SERIAL_LOW_WATER and self.paused:
            for link in list(self.paused):
                s = self.links.get(link)
                if s is not None:
                    try:
                        self._sel.register(
                            s, selectors.EVENT_READ, ("link", link, s))
                    except (KeyError, ValueError):
                        pass
                self.paused.discard(link)

    # ---- plumbing --------------------------------------------------------
    def drop_link(self, link, tell_engine):
        s = self.links.pop(link, None)
        self.paused.discard(link)
        if s is None:
            return
        try:
            self._sel.unregister(s)
        except (KeyError, ValueError):
            pass
        try:
            s.close()
        except OSError:
            pass
        if tell_engine:
            self.engine.net_closed(link)

    def drop_net_listen(self):
        if self.net_listen is None:
            return
        try:
            self._sel.unregister(self.net_listen)
        except (KeyError, ValueError):
            pass
        try:
            self.net_listen.close()
        except OSError:
            pass
        self.server._release_net_port(self, self.net_listen_port)
        self.net_listen = None
        self.net_listen_port = None

    def close(self):
        """Tear this emulator's session down.

        Called from this session's own thread on the way out AND from the
        server's teardown, so it is idempotent and locked: two threads
        closing the same sockets at once must not double-unregister."""
        with self._close_lock:
            if self.closed and self.sock is None:
                return
            self.closed = True
            for link in list(self.links):
                self.drop_link(link, tell_engine=False)
            self.paused.clear()
            self.drop_net_listen()
            if self.sock is not None:
                try:
                    self._sel.unregister(self.sock)
                except (KeyError, ValueError):
                    pass
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            self.out.clear()
            for s in (self._wake_r, self._wake_w):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
            self._wake_r = self._wake_w = None
            try:
                self._sel.close()
            except Exception:                             # noqa: BLE001
                pass


class EspAtServer:
    """The socket harness around :class:`EspAtEngine`.

    ONE listener, MANY emulators, a THREAD EACH. ``start()`` binds and
    listens SYNCHRONOUSLY — when it returns, MAME can be launched and its
    ``-bitb socket.…`` connect will be accepted — then a daemon thread
    accepts connections. Every accepted connection is forked off into its
    own :class:`_EspSession`: its own engine, links, CIPSERVER listener,
    serial queue, selector and loop thread. A second (third, fourth…)
    MAME launched against the same port is therefore served ALONGSIDE the
    first, and cannot stall it, tear it down or see a byte of its AT
    state — the isolation the single shared loop could not give (see the
    session's own docstring for the two measured failures that settled
    it). A session dies with its emulator; the server outlives it.

    ``stop()`` wakes the accept loop, stops every session (bounded joins),
    releases the port deterministically and joins the thread; the object
    is SINGLE-USE by design — a second ``start()`` raises.

    The only state shared across threads is deliberately tiny: the session
    list and the CIPSERVER port-claim table, each behind its own lock.
    """

    _POLL_S = 0.2       # loop heartbeat when nothing is happening

    def __init__(self, port=2222, host="127.0.0.1", log=None, verbose=False,
                 station_ip=""):
        self.port = int(port)
        self.host = host
        self._log = log or (lambda s: None)
        self._verbose = bool(verbose)
        # The address every session reports as the Next's own: a dotted
        # quad, or "" for "this host's primary address, resolved when each
        # emulator connects" (see the module docstring). Garbage is refused
        # here rather than reported to a Next as its address.
        self._station_ip = self._check_station_ip(station_ip)
        self._sel = None
        self._listen = None            # the bitbanger listener (for MAME)
        self._sessions = []            # live _EspSession objects
        self._sessions_lock = threading.Lock()
        self._next_index = 0           # session labels, 1-based, never reused
        self._multi = False            # two emulators have shared this server
        self._net_claims = {}          # CIPSERVER port -> owning session
        self._claims_lock = threading.Lock()
        self._thread = None
        self._wake_r = None
        self._wake_w = None
        self._stopping = False
        self._used = False

    # ---- lifecycle -------------------------------------------------------
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def session_count(self):
        """How many emulators are connected to this server right now."""
        with self._sessions_lock:
            return len(self._sessions)

    @property
    def verbose(self):
        """Whether tracing is on (read-only; use set_verbose to change it)."""
        return self._verbose

    @staticmethod
    def _check_station_ip(value):
        value = str(value or "").strip()
        if value and not is_station_ipv4(value):
            raise ValueError(f"the station IP must be an address a host "
                             f"interface could carry (a dotted quad, not "
                             f"0.x.x.x, a broadcast or a multicast "
                             f"address) or empty (automatic), not {value!r}")
        return value

    @property
    def station_ip(self):
        """The CONFIGURED station address: a dotted quad, or "" when the
        host's primary address is used (``resolve_station_ip`` tells which
        one that is right now)."""
        return self._station_ip

    def resolve_station_ip(self):
        """The address a session built now would report: the configured
        one, else this host's primary outbound address, else loopback -
        never empty, so a Next always gets a dotted quad."""
        return self._resolve(self._station_ip)

    @staticmethod
    def _resolve(configured):
        """``resolve_station_ip`` for a given CONFIGURED value ("" =
        automatic) - the one rule, shared by the sessions and the live
        rewrite."""
        return configured or primary_ipv4() or LOOPBACK_IP

    def set_station_ip(self, value, quiet=False):
        """Move the station address on a RUNNING server, live sessions
        included: the Settings pick can change while a MAME holds the
        shared worker, and stopping it to apply the change would cut that
        emulator's wire. Each live engine gets the newly resolved address
        as one attribute write; the Next sees it at its next AT+CIFSR /
        AT+CIPSTA? (ZX Next Remote polls that until an address appears,
        the WiFi wizard's "Refresh" re-reads it). A guest's own
        AT+CIPSTA= override, where one is set, still wins for that
        session. Raises ValueError for anything but an address a host
        interface could carry, or "". Returns the resolved address.

        The write and the rewrite happen under the session lock, the same
        lock a joining session is checked under (see
        ``_EspSession.station_cfg``), so no session can be missed.
        ``quiet`` skips this module's own log line, for a caller that
        announces the change itself (the app does, translated)."""
        value = self._check_station_ip(value)
        with self._sessions_lock:
            self._station_ip = value
            resolved = self._resolve(value)
            for session in self._sessions:
                session.engine.station_ip = resolved
        if not quiet:
            how = ("" if value
                   else " (this PC's primary address)"
                   if resolved != LOOPBACK_IP
                   else " (this PC has no network route: loopback)")
            self._log(f"RS232 ESP emulation: the emulated Next now reports "
                      f"the IP address {resolved}{how}")
        return resolved

    def set_verbose(self, verbose):
        """Turn tracing on/off on a RUNNING server.

        The Settings toggle can change between launches while an earlier
        MAME still holds the server, and stopping it to apply the change
        would cut that emulator's wire. New sessions read the flag at
        construction; the live ones are updated in place — a lone bool
        write, atomic enough to cross threads without a lock.
        """
        self._verbose = bool(verbose)
        for session in self._session_snapshot():
            session.engine._verbose = self._verbose

    def _session_snapshot(self):
        with self._sessions_lock:
            return list(self._sessions)

    def _forget(self, session):
        """A session's loop has ended (called from that session's thread)."""
        with self._sessions_lock:
            try:
                self._sessions.remove(session)
            except ValueError:
                pass

    def _label(self, session):
        """``''`` while this server has only ever served one emulator,
        ``'[emulator N] '`` once two have shared it."""
        return f"[emulator {session.index}] " if self._multi else ""

    # ---- the CIPSERVER port claim table (shared by the session threads) --
    def _claim_net_port(self, session, port):
        """Reserve ``port`` for ``session`` unless another session holds
        it. The caller binds afterwards and releases the claim if the bind
        fails; claiming first is what keeps two sessions off one port on
        Windows, where SO_REUSEADDR would let both binds succeed."""
        with self._claims_lock:
            owner = self._net_claims.get(port)
            if owner is not None and owner is not session:
                return False
            self._net_claims[port] = session
            return True

    def _release_net_port(self, session, port):
        if port is None:
            return
        with self._claims_lock:
            if self._net_claims.get(port) is session:
                self._net_claims.pop(port, None)

    def start(self):
        """Bind + listen + spawn the accept thread. Raises OSError when the
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
            #
            # NOTE this is about two SERVERS, never about two emulators:
            # several MAMEs share ONE listener here, each as its own
            # accepted connection and its own session.
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
            # Backlog for a whole fleet: emulators are launched back to
            # back and each connects on its own.
            srv.listen(MAX_SESSIONS)
        except OSError:
            srv.close()
            raise
        srv.setblocking(False)
        self._listen = srv
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
        """Tear the whole server down — every session with it.

        Every join is BOUNDED and the LISTENER is closed from this thread
        regardless of their outcome: a session mid-way through a bounded
        network call can outlive its join, and the port must be free for
        the next launch anyway (the field-reported every-other-launch
        failure). A still-running session then finds its sockets closed,
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
            self._log("RS232 ESP emulation: the listener is slow to stop; "
                      "forcing its socket closed.")
            if self._listen is not None:
                try:
                    self._listen.close()
                except OSError:
                    pass
        self._thread = None
        for session in self._session_snapshot():
            session.stop()
        if self._listen is not None:
            try:
                self._listen.close()
            except OSError:
                pass
        self._log("RS232 ESP emulation stopped.")

    # ---- the accept loop -------------------------------------------------
    def _run(self):
        try:
            while not self._stopping:
                for key, events in self._sel.select(self._POLL_S):
                    try:
                        self._handle(key, events)
                    except Exception as ex:               # noqa: BLE001
                        self._log("RS232 ESP emulation: ignoring a failed "
                                  f"listener event: {ex}")
        except Exception as ex:                           # noqa: BLE001
            self._log(f"RS232 ESP emulation error: {ex}")
        finally:
            self._teardown()

    def _handle(self, key, _events):
        tag = key.data[0]
        if tag == "wake":
            try:
                self._wake_r.recv(64)
            except OSError:
                pass
        elif tag == "accept-serial":
            self._accept_serial()

    def _accept_serial(self):
        try:
            conn, addr = self._listen.accept()
        except OSError:
            return
        with self._sessions_lock:
            full = len(self._sessions) >= MAX_SESSIONS
        if full:
            # A cap, not a policy: several emulators at once is the whole
            # point, but a runaway reconnect loop must not grow the
            # process without bound.
            self._log(f"RS232 ESP emulation: refusing another emulator "
                      f"connection ({MAX_SESSIONS} already connected).")
            try:
                conn.close()
            except OSError:
                pass
            return
        self._next_index += 1            # accept loop only: no lock needed
        session = _EspSession(self, conn, addr, self._next_index)
        with self._sessions_lock:
            self._sessions.append(session)
            live = len(self._sessions)
            if session.station_cfg != self._station_ip:
                # set_station_ip ran while this session was being built
                # (see _EspSession.station_cfg): catch it up, under the
                # same lock that rewrite holds, so no session can slip
                # between the two.
                session.engine.station_ip = self._resolve(self._station_ip)
        if live > 1:
            # From here on every session-scoped line carries its label so
            # two emulators' logs can be told apart. The latch STAYS set:
            # the labels must not vanish from a survivor's lines when the
            # other emulator quits.
            self._multi = True
        session.start()
        session.log(f"RS232 ESP emulation: emulator connected from "
                    f"{addr[0]}:{addr[1]}"
                    + (f" ({live} emulators connected)."
                       if live > 1 else "."))

    def _teardown(self):
        for session in self._session_snapshot():
            session.stop()
        for s in (self._listen, self._wake_r, self._wake_w):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if self._sel is not None:
            self._sel.close()


def _station_ip_arg(text):
    import argparse
    if text and not is_station_ipv4(text):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an address the emulated Next can report: an "
            f"IPv4 address (four numbers 0-255 separated by dots), not "
            f"0.x.x.x, a broadcast or a multicast address")
    return text


def build_arg_parser():
    """The standalone CLI's options (its own function so a test can parse
    without serving)."""
    import argparse
    parser = argparse.ArgumentParser(
        description="ESP-AT emulator for MAME's -rs232_esp bitbanger socket "
                    "(serves several MAME instances at once, one session each)")
    parser.add_argument("--port", "-p", type=int, default=2222,
                        help="TCP port to listen on (default 2222)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="trace commands and traffic")
    parser.add_argument("--ip", type=_station_ip_arg, default="",
                        metavar="A.B.C.D",
                        help="the IPv4 address the emulated Next reports as "
                             "its own (default: this PC's primary address)")
    return parser


def main(argv=None):
    """Standalone console entry point:
    ``python espemu.py [--port N] [-v] [--ip A.B.C.D]``."""
    args = build_arg_parser().parse_args(argv)
    server = EspAtServer(port=args.port, log=print, verbose=args.verbose,
                         station_ip=args.ip)
    server.start()
    print(f"The emulated Next reports the IP address "
          f"{server.resolve_station_ip()}"
          + ("" if args.ip else " (this PC's primary address; --ip picks another)"))
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
