"""zxnu_http_bridge.py — a self-hosted HTTP (Flask) man-in-the-middle that
exposes a NextSync ``-listen`` session as plain web routes.

The point: the Spectrum Next has a built-in ``.http`` dot command
(https://github.com/remy/next-http) that can perform HTTP GET/POST calls
(no TLS, so port 80 by default). By publishing every ``-listen`` verb as an
HTTP route, ANY http client — another Next running ``.http``, curl, a
browser, a script — can drive the file system of the Next currently
connected to this server in ``.sync5 -listen`` mode:

    caller (.http / curl)  --HTTP-->  this bridge  --NextSync-->  remote Next

The module is host-agnostic and reusable: it is wired into both the
ZX-Next-Unite app (over the Remote Explorer's listen worker) and the
standalone ``nextsync5.py`` server (over its -listen console session). Only
the standard library is imported at module load; Flask/werkzeug are imported
lazily in :meth:`NextSyncHttpBridge.start`, so hosts can import this module
unconditionally and report a friendly "pip install flask" message when the
server is actually asked to run.

Wire-up contract (all a host must provide — see :class:`QueueBridgeHost`):

* ``enqueue(cmd) -> bool``   put one command tuple on the live -listen
  session's queue (False when no session is running);
* ``make_cmd(op, a1, a2, reply) -> tuple | None``   translate a canonical
  bridge op (ls/get/put/mkdir/rmdir/rmtree/rm/ren/rcpy/rfsize/free/drives/version/
  forceexit) into the host's own command-tuple dialect, with ``reply`` (a
  :class:`BridgeReply`) riding along as the LAST element (None = the host
  doesn't support that op → HTTP 501);
* ``state() -> dict``   {'listening': bool, 'connected': bool,
  'current': str, 'drives': list|None}.

Multi-session hosts (the app seats up to four ``-listen`` Nexts) may also
provide two OPTIONAL callables:

* ``sessions() -> (active_sid, [(sid, addr, name), …], max_peers)``   the
  live roster snapshot (names may be "");
* ``enqueue_to(sid, cmd) -> bool``   put one command tuple on THAT
  session's own queue (False when the sid is gone) — delivery must not
  move the host's notion of the active session.

With those provided, every op route accepts a session selector
(``?session=N`` or the ``ZXNEXTUNITE-BRIDGE-SESSION`` header; the query
param wins) and ``GET /sessions`` lists the roster. No selector = the
active session, exactly the pre-session behaviour. A selector naming a
session that is gone answers HTTP 410 — never a silent retarget: sids are
minted once per app run and never reused, so a stale id can only mean
"that Next left". Hosts without the callables stay single-session:
/sessions synthesizes one entry (sid 1) from ``state()`` (plus its
``addr`` key when provided), and only ``session=1`` is accepted.

The host's command executor must then fill ``reply`` with a result dict —
and, by convention, a command carrying a reply is SILENT: it reports only
through the reply, never through the host's usual UI signals/prints, so
bridge traffic cannot hijack an open Remote Explorer pane.

Executor reply shapes (both hosts emit these):
    ls      {'ok': True, 'entries': [(is_dir, size, name), …]} | {'ok': False, 'error': str}
    get     {'ok': True, 'count': n, 'last': local_path} | error
    put     {'ok': bool, 'error'?}
    mkdir/rmdir/rm/ren/rmtree   {'ok': bool}
    drives  {'ok': True, 'current': 'C', 'letters': ['C','M']} | error
    free    {'ok': True, 'free': bytes} | error
    rcpy    {'ok': bool, 'files': n, 'error'?}
    rfsize  {'ok': True, 'files': n, 'dirs': n, 'bytes': n} | error
    forceexit   {'ok': True}   (the Next then closes the link and exits)
"""

import base64
import hmac
import importlib.util
import json
import os
import queue
import shutil
import socket
import tempfile
import threading
import time

DEFAULT_PORT = 80          # .http's default; HTTP only (no TLS on the Next)

# HTTP header carrying the optional shared secret. Kept in sync with
# zxnu_config.NEXTSYNC_BRIDGE_TOKEN_HEADER (this module stays stdlib-only so it
# does not import the app's config).
BRIDGE_TOKEN_HEADER = "ZXNEXTUNITE-BRIDGE-TOKEN"

# HTTP header carrying the optional session selector (ZXNextRemote sends the
# header rather than &session= so deep paths keep their whole 250-char query
# budget). The query param, when both are present, wins: explicit beats
# ambient.
BRIDGE_SESSION_HEADER = "ZXNEXTUNITE-BRIDGE-SESSION"


def session_label(sid, addr, name=""):
    """THE session label, one composer for every surface: the Remote
    Explorer's machine dropdown, /sessions, and ZXNextRemote's title line
    all show exactly this string — "10.0.0.185 #1 - Next" (the " - name"
    tail only when the machine has a friendly name)."""
    base = f"{addr} #{sid}"
    return f"{base} - {name}" if name else base


def flask_available():
    """True when the optional Flask package is installed. Cheap (no import
    happens), so hosts can gate their UI / -w flag on it without ever
    triggering an ImportError at startup."""
    try:
        return importlib.util.find_spec("flask") is not None
    except (ImportError, ValueError):
        return False
DEFAULT_TIMEOUT = 45.0     # quick verbs: one poll round-trip + margin
# get/put/rcpy/rfsize/rmtree can move real data. Deliberately just UNDER
# the patience of the strictest known client (ZXNextRemote gives a relayed
# transfer 300 s to produce its first byte): whoever gives up first decides
# what the user sees, and a 504 naming the stalled op beats silence. It
# costs nothing real — a relay that needs longer than the client will wait
# has already failed from the client's seat. Raise BOTH together if the
# bridge ever streams instead of collect-then-respond.
LONG_TIMEOUT = 270.0
_LONG_OPS = ("get", "put", "rcpy", "rfsize", "rmtree")


def fmt_size(nbytes):
    """Human-readable size: 512 -> '512 bytes', 1536000 -> '1.5 MB'."""
    if nbytes < 1024:
        return f"{nbytes} bytes"
    v = float(nbytes)
    for unit in ("KB", "MB", "GB", "TB"):
        v /= 1024.0
        if v < 1024.0 or unit == "TB":
            return f"{v:.1f} {unit}"


class BridgeReply:
    """The result sink a bridge command carries through a host's command
    queue. The executor calls :meth:`put` with the result dict; the HTTP
    thread blocks in :meth:`wait`. Hosts detect a bridge command by
    ``isinstance(cmd[-1], BridgeReply)``.

    :meth:`put` is IDEMPOTENT — the first result wins and later ones are
    dropped. That is what lets an executor blanket-resolve every reply it
    might still owe when a session dies (see ``_fail_inflight`` in
    zxnu_workers.py) without any risk of overwriting a real answer that
    the HTTP thread has not collected yet. Before that guarantee existed,
    an exception raised mid-command left the reply unresolved forever and
    the HTTP caller blocked for the whole bridge timeout, receiving no
    bytes and no status — the "transfer wedges on the Nth file" bug."""

    def __init__(self):
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._done = False

    def put(self, result):
        with self._lock:
            if self._done:
                return False
            self._done = True
        self._q.put(dict(result))
        return True

    @property
    def resolved(self):
        return self._done

    def wait(self, timeout):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class QueueBridgeHost:
    """Generic adapter over a queue-drained -listen executor (both the app's
    worker and nextsync5's session are exactly that). Serialises bridge
    commands (one at a time — the underlying session is serial anyway) and
    owns the temp-file plumbing for get (download to a temp dir, hand back
    bytes) and put (write the request body to a temp file the executor can
    stream)."""

    def __init__(self, enqueue, make_cmd, state, sessions=None,
                 enqueue_to=None):
        self._enqueue = enqueue
        self._make_cmd = make_cmd
        self._state = state
        self._sessions = sessions
        self._enqueue_to = enqueue_to
        self._lock = threading.Lock()

    def state(self):
        try:
            return dict(self._state())
        except Exception as ex:                      # noqa: BLE001
            return {"listening": False, "connected": False,
                    "current": "", "drives": None, "error": str(ex)}

    def roster(self):
        """(active_sid, [(sid, addr, name), …], max_peers) or None when this
        host has no session roster (nextsync5's single-session console)."""
        if self._sessions is None:
            return None
        try:
            active, rows, maxp = self._sessions()
            return (active,
                    [(int(s), str(a), str(n or "")) for s, a, n in rows],
                    int(maxp))
        except Exception:                            # noqa: BLE001
            return None

    @staticmethod
    def _gone(session):
        return {"ok": False, "http": 410,
                "error": f"session {session} is gone - "
                         "GET /sessions for the live list"}

    def run(self, op, a1="", a2="", body=None, timeout=None, session=None):
        """Run one canonical bridge op against the connected Next — the
        active session by default, ``session=<sid>`` to target a seated one
        (delivered on that session's own queue; the active/baton state is
        never touched). Returns the executor's result dict; on bridge-level
        failures the dict carries ``ok: False`` plus an ``http`` status
        suggestion (410/501/503/504)."""
        st = self.state()
        if session is not None and self._enqueue_to is None:
            # Single-session host: sid 1 IS the one session; anything else
            # can only be stale. (Validated before the connected gate so a
            # stale id answers 410, not 503, when nothing is connected.)
            if session != 1:
                return self._gone(session)
            session = None
        if not st.get("connected"):
            return {"ok": False, "http": 503,
                    "error": "no Next is connected in '.sync5 -L' (-l or -listen) mode"}
        if timeout is None:
            timeout = LONG_TIMEOUT if op in _LONG_OPS else DEFAULT_TIMEOUT
        with self._lock:
            tmp = None
            try:
                if op == "get":
                    tmp = tempfile.mkdtemp(prefix="zxnu_http_get_")
                    a2 = tmp
                elif op == "put":
                    tmp = tempfile.mkdtemp(prefix="zxnu_http_put_")
                    local = os.path.join(
                        tmp, os.path.basename(a1.rstrip("/").rsplit("/", 1)[-1])
                        or "upload.bin")
                    with open(local, "wb") as fh:
                        fh.write(body or b"")
                    a2 = local
                reply = BridgeReply()
                cmd = self._make_cmd(op, a1, a2, reply)
                if cmd is None:
                    return {"ok": False, "http": 501,
                            "error": f"'{op}' is not supported by this server"}
                if session is not None:
                    # Targeted delivery. No roster pre-check: enqueue_to
                    # validates the sid under the worker's own lock, so the
                    # check and the put cannot straddle a departure.
                    if not self._enqueue_to(session, cmd):
                        return self._gone(session)
                elif not self._enqueue(cmd):
                    return {"ok": False, "http": 503,
                            "error": "the -listen session is not running"}
                res = reply.wait(timeout)
                if res is None:
                    return {"ok": False, "http": 504,
                            "error": "timed out waiting for the Next"}
                if op == "get" and res.get("ok"):
                    # Exactly one file downloaded -> hand its bytes back. More
                    # (or none) means the path was a folder: that tree is not
                    # something a single HTTP body can carry.
                    if res.get("count") == 1 and res.get("last"):
                        with open(res["last"], "rb") as fh:
                            res["data"] = fh.read()
                    else:
                        res = {"ok": False, "http": 400,
                               "error": "not a single file (a folder?) — "
                                        "list it with /ls instead"}
                return res
            finally:
                if tmp is not None:
                    shutil.rmtree(tmp, ignore_errors=True)


class _ConnectionLimitMiddleware:
    """WSGI wrapper bounding how many HTTP requests are processed at once.

    Excess requests are not rejected — they block until a slot frees. With
    the default limit of 1 the bridge is strictly serial, which matches the
    '.sync5 -listen' session it drives (one command at a time) and avoids
    concurrent access altogether.

    The slot is held while the wrapped app produces the response and the
    body is fully materialised, then released in this frame's ``finally`` —
    deliberately NOT via a close() callback on the returned iterable:
    werkzeug's serving handler drains the socket before calling close(),
    and a client connection reset during that drain (routine on Windows)
    skips the close() entirely, which would leak the slot forever.
    Buffering is free here — every bridge response is small text or file
    bytes already fully in memory."""

    def __init__(self, wsgi_app, limit):
        self._wsgi_app = wsgi_app
        self._sem = threading.BoundedSemaphore(max(1, int(limit)))

    def __call__(self, environ, start_response):
        self._sem.acquire()
        try:
            rv = self._wsgi_app(environ, start_response)
            try:
                return list(rv)
            finally:
                close = getattr(rv, "close", None)
                if close is not None:
                    close()
        finally:
            self._sem.release()


class NextSyncHttpBridge:
    """The Flask web server. Construct with a :class:`QueueBridgeHost` (or
    anything exposing ``state()`` and ``run()``), then :meth:`start` /
    :meth:`stop`. Every route answers plain text by default (friendly to a
    Next parsing with ``.http``); append ``&json=1`` for JSON."""

    ROUTES_HELP = (
        "NextSync HTTP bridge - drive the Next connected in '.sync5 -L' (-l or -listen)\n"
        "Routes (text by default; append &json=1 for JSON):\n"
        "  GET  /status                     server + Next state, partitions\n"
        "  GET  /sessions                   list the seated -listen Nexts\n"
        "       (every op route below also takes &session=<sid> — or the\n"
        "       ZXNEXTUNITE-BRIDGE-SESSION header — to target one seated\n"
        "       Next; no selector = the active one; a departed sid = 410)\n"
        "  GET  /drives                     mounted drive letters\n"
        "  GET  /free?drive=C               free space on a partition\n"
        "  GET  /version-type               what answers this session: httpbridge |\n"
        "       n2n (ZX Next Remote flavors) | sync (the .sync5 dot)\n"
        "  GET  /version-number             that responder's own build number\n"
        "  GET  /ls?path=/games             directory listing\n"
        "  GET  /get?path=/games/a.tap      download one file (raw bytes)\n"
        "  POST /put?path=/games/a.tap      upload (request body = the file)\n"
        "  POST /put?path=/f&append=1&size=N  chunked upload: POST pieces\n"
        "       until N bytes arrived, then the file is written in one go\n"
        "  GET  /mkdir?path=/newdir         create a directory\n"
        "  GET  /rmdir?path=/olddir         remove an EMPTY directory\n"
        "  GET  /rmtree?path=/olddir        remove a directory recursively\n"
        "  GET  /rm?path=/old.tap           delete a file\n"
        "  GET  /ren?from=/a&to=/b          rename / move\n"
        "  GET  /rcpy?src=/a&dst=m:/b       copy ON the Next (across drives)\n"
        "  GET  /rfsize?path=/games         total size of a file / tree\n"
        "  GET  /sum?path=/f                16-bit additive checksum + size\n"
        "       of one file (&bare=1: just the checksum digits)\n"
        "  GET  /forceexit                  make the Next leave -listen and exit\n")

    def __init__(self, host_adapter, listen_host="0.0.0.0", port=DEFAULT_PORT,
                 log=None, verbose=False, connection_limit=1, auth_token=None):
        self._adapter = host_adapter
        self._listen_host = listen_host
        self._port = int(port)
        # Optional shared secret. When set, every request must carry the
        # BRIDGE_TOKEN_HEADER header equal to this value or it is answered with
        # HTTP 401 (see the auth guard installed in _install_routes). None or ""
        # = no authentication (the historical, open behaviour).
        self._auth_token = (auth_token or "").strip() or None
        # How many HTTP requests may be served concurrently. 1 (the default
        # and the recommended value) fully serialises the bridge, matching
        # the serial -listen session behind it.
        self._connection_limit = max(1, int(connection_limit or 1))
        self._log = log or (lambda s: None)
        # verbose: log every HTTP request (method, path, query, payload) and
        # its response (status, body) through self._log — the troubleshooting
        # view behind nextsync5.py's -v.
        self._verbose = bool(verbose)
        self._server = None
        self._thread = None
        # Set by a failed start() when the OS refused the port because
        # something else already holds it (WinError 10048 / EADDRINUSE — and
        # WinError 10013, which Windows raises when http.sys/IIS owns port
        # 80). Hosts use it to show a targeted "port already in use" error.
        self.port_in_use = False
        # Drive letters cached per connection (invalidated when the Next
        # disconnects), so /status can report partition counts without a
        # round-trip on every poll.
        self._drives_cache = None
        # /version-type + /version-number answers, keyed by skey("version").
        # Positive AND negative results are kept: the wire probe against an
        # old listener costs a false-disconnect log and a brief self-healing
        # desync, a toll worth paying once per seat, never per route. Only
        # sid-keyed entries are cached - sids are never reused within a run,
        # while the None (active-session) bucket can move between machines
        # mid-flight and must be re-asked every time.
        self._ident_cache = {}
        # /put?append=1 chunked uploads: per-remote-path spool of the chunks
        # received so far (the Next's .http can POST at most one 16K bank per
        # request, so big files arrive in pieces). Guarded by its own lock —
        # connection_limit may be >1.
        self._put_spool = {}
        self._spool_lock = threading.Lock()
        # Ranged /get relay cache: the last file pulled from the Next, kept
        # for slice serving (ZXNextRemote's overrun-proof retry pulls ~90
        # slices per 115KB — one dot-speed relay, then RAM). One entry:
        # any off=0 request refreshes it, serving the final slice drops it.
        self._get_cache = {"path": None, "data": b""}
        self._get_cache_lock = threading.Lock()
        # In-flight request registry: rid -> [method, path, start, last_note].
        # Every request is tracked whether or not -v is on, because the two
        # things it powers are worth having always: the live count (exposed
        # on /status and to the UI) and the stall watchdog below. Guarded by
        # its own lock — connection_limit may be >1.
        self._inflight = {}
        self._inflight_lock = threading.Lock()
        self._req_seq = 0
        self._watch_stop = None
        self._watch_thread = None

    # A request still unanswered after SLOW_AFTER seconds is announced, then
    # re-announced every SLOW_EVERY, until it finishes. This is the "who is
    # stuck?" answer: a relay that is merely slow keeps ticking (the client
    # shows its own wait badge meanwhile), while one that is truly wedged
    # says so in the console instead of failing silently minutes later.
    SLOW_AFTER = 15.0
    SLOW_EVERY = 15.0
    _WATCH_TICK = 5.0

    # Hard cap on a single chunked upload's declared size: everything is
    # assembled in memory before the one-shot push to the Next (just like a
    # plain /put buffers its whole body), so refuse absurd declarations.
    MAX_SPOOL = 256 * 1024 * 1024

    # ------------------------------------------------------------------
    @property
    def inflight(self):
        """How many HTTP requests are being served right now. A UI can poll
        this for a live gauge; /status reports it too."""
        with self._inflight_lock:
            return len(self._inflight)

    def inflight_detail(self):
        """[(seconds_running, "GET /get?path=…"), …], longest-waiting first —
        what a gauge shows when the user asks "stuck on WHAT?"."""
        now = time.monotonic()
        with self._inflight_lock:
            rows = [(now - v[2], f"{v[0]} {v[1]}") for v in self._inflight.values()]
        rows.sort(key=lambda r: -r[0])
        return rows

    def _watch_inflight(self):
        """Announce requests that outlive SLOW_AFTER (see the constants)."""
        while not self._watch_stop.is_set():
            self._watch_stop.wait(self._WATCH_TICK)
            if self._watch_stop.is_set():
                break
            now = time.monotonic()
            with self._inflight_lock:
                snapshot = [(rid, list(v)) for rid, v in self._inflight.items()]
            for rid, (method, path, start, noted) in snapshot:
                elapsed = now - start
                if elapsed < self.SLOW_AFTER or elapsed - noted < self.SLOW_EVERY:
                    continue
                with self._inflight_lock:
                    if rid not in self._inflight:
                        continue          # finished while we were looking
                    self._inflight[rid][3] = elapsed
                self._log(f"HTTP .. still waiting {elapsed:.0f}s: {method} {path}")

    @property
    def running(self):
        return self._server is not None

    @property
    def port(self):
        return self._port

    def start(self):
        """Build the Flask app and serve it on a daemon thread. Returns
        (True, "") or (False, "human-readable error") — a missing Flask or an
        occupied port must be a friendly message, not a crash."""
        if self._server is not None:
            return True, ""
        self.port_in_use = False
        if not flask_available():
            return False, ("Flask is not installed - install it with: "
                           "pip install flask")
        from flask import Flask
        from werkzeug.serving import make_server
        # Detach werkzeug's per-request logging (and Flask's error logger)
        # from the host's root logging handlers. Besides being console noise,
        # this is a DEADLOCK guard: those log calls run on the serving
        # threads, and a host whose root handler marshals into a UI thread
        # (the Qt app) would deadlock the moment that UI thread is itself
        # waiting on an HTTP response. The bridge reports through self._log
        # instead.
        import logging
        for name in ("werkzeug", "zxnu_http_bridge"):
            lg = logging.getLogger(name)
            lg.handlers = [logging.NullHandler()]
            lg.propagate = False
        app = Flask("zxnu_http_bridge")
        self._install_routes(app)
        app.wsgi_app = _ConnectionLimitMiddleware(app.wsgi_app,
                                                  self._connection_limit)
        # Pre-flight probe: werkzeug binds with SO_REUSEADDR, which on
        # Windows silently SUCCEEDS even when another program already owns
        # the port (the classic WinError 10048 only surfaces for exclusive
        # listeners such as IIS). Probing with SO_EXCLUSIVEADDRUSE first
        # turns "port already in use" into a reliable, friendly error on
        # every platform instead of a half-working server.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):       # Windows
                probe.setsockopt(socket.SOL_SOCKET,
                                 socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((self._listen_host, self._port))
        except OSError as ex:
            # WinError 10048 (WSAEADDRINUSE) / errno 98 (Linux) / 48 (macOS):
            # something already listens on the port. WinError 10013
            # (WSAEACCES) usually means the same on Windows when http.sys
            # (IIS/W3SVC) owns port 80; errno 13 (EACCES) on Linux is a
            # privileged-port refusal and gets the generic message instead.
            if getattr(ex, "winerror", None) in (10048, 10013) or \
                    getattr(ex, "errno", None) in (98, 48):
                self.port_in_use = True
                return False, (f"port {self._port} is already in use by "
                               "another program (a web server such as IIS, "
                               "or another bridge?) - the web server has "
                               "not been started")
            return False, (f"could not bind {self._listen_host}:{self._port} "
                           f"({ex}) - the web server has not been started")
        finally:
            try:
                probe.close()
            except OSError:
                pass
        try:
            server = make_server(self._listen_host, self._port, app,
                                 threaded=True)
        except OSError as ex:
            if getattr(ex, "winerror", None) in (10048, 10013) or \
                    getattr(ex, "errno", None) in (98, 48):
                self.port_in_use = True
                return False, (f"port {self._port} is already in use by "
                               "another program - the web server has not "
                               "been started")
            return False, (f"could not bind {self._listen_host}:{self._port} "
                           f"({ex}) - the web server has not been started")
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever,
                                        daemon=True, name="zxnu-http-bridge")
        self._thread.start()
        # Stall watchdog: names any request still unanswered after
        # SLOW_AFTER seconds, so a wedge is visible while it happens.
        self._watch_stop = threading.Event()
        self._watch_thread = threading.Thread(
            target=self._watch_inflight, daemon=True, name="zxnu-http-watch")
        self._watch_thread.start()
        self._log(f"HTTP bridge: serving on port {self._port} "
                  f"(max {self._connection_limit} concurrent "
                  f"connection{'s' if self._connection_limit != 1 else ''})")
        return True, ""

    def stop(self):
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:                        # noqa: BLE001
                pass
            self._log("HTTP bridge: stopped")
        self._thread = None
        if self._watch_stop is not None:
            self._watch_stop.set()
        self._watch_thread = None
        with self._inflight_lock:
            self._inflight.clear()
        with self._spool_lock:
            self._put_spool.clear()   # drop any unfinished chunked uploads
        with self._get_cache_lock:
            self._get_cache = {"path": None, "data": b""}

    # ------------------------------------------------------------------
    @staticmethod
    def _peek(data, limit=256):
        """Loggable preview of a request/response body: printable text is
        shown as-is (truncated), binary as a hex prefix + size."""
        if not data:
            return "(empty)"
        try:
            text = data.decode("utf-8")
            if all(32 <= ord(c) or c in "\r\n\t" for c in text):
                text = text.replace("\r", "\\r").replace("\n", "\\n")
                return (text[:limit] + f"… ({len(data)} bytes)"
                        if len(text) > limit else text)
        except UnicodeDecodeError:
            pass
        return f"<binary {len(data)} bytes: {data[:24].hex()}…>"

    def _install_routes(self, app):
        from flask import request, Response

        # ---- bearer-token guard (registered FIRST so an unauthorised request
        # is rejected before the verbose tracer below could log its payload) --
        @app.before_request
        def _require_token():          # noqa: ANN202
            if self._auth_token is None:
                return None            # authentication disabled
            supplied = request.headers.get(BRIDGE_TOKEN_HEADER, "")
            # Constant-time compare so a wrong token can't be timed byte by byte.
            if supplied and hmac.compare_digest(supplied, self._auth_token):
                return None            # authorised
            msg = f"missing or invalid {BRIDGE_TOKEN_HEADER}"
            if (request.args.get("json") in ("1", "true", "yes")
                    or "application/json" in (request.headers.get("Accept") or "")):
                return Response(
                    json.dumps({"ok": False, "error": msg, "status": 401}) + "\n",
                    status=401, mimetype="application/json")
            return Response(f"ERR {msg}\n", status=401, mimetype="text/plain")

        # ---- session selector (after the token guard: an unauthorised
        # request never learns whether its sid parses) ---------------------
        @app.before_request
        def _resolve_session():        # noqa: ANN202
            v = ((request.args.get("session") or "").strip()
                 or (request.headers.get(BRIDGE_SESSION_HEADER) or "").strip())
            if not v:
                request.environ["zxnu.session"] = None
                return None
            try:
                n = int(v, 10)
                if n <= 0:
                    raise ValueError
            except ValueError:
                msg = f"bad session selector '{v}' (want a positive integer)"
                if (request.args.get("json") in ("1", "true", "yes")
                        or "application/json"
                        in (request.headers.get("Accept") or "")):
                    return Response(
                        json.dumps({"ok": False, "error": msg,
                                    "status": 400}) + "\n",
                        status=400, mimetype="application/json")
                return Response(f"ERR {msg}\n", status=400,
                                mimetype="text/plain")
            request.environ["zxnu.session"] = n
            return None

        # ---- in-flight tracking (always) + -v tracing --------------------
        # Registered unconditionally: the live count and the stall watchdog
        # are diagnostics you want available the moment something wedges,
        # not after reproducing it with a flag flipped. Only the per-request
        # log lines are gated on -v.
        @app.before_request
        def _track_request():              # noqa: ANN202
            path = request.full_path.rstrip("?")
            with self._inflight_lock:
                self._req_seq += 1
                rid = self._req_seq
                self._inflight[rid] = [request.method, path, time.monotonic(), 0.0]
                n = len(self._inflight)
            request.environ["zxnu.rid"] = rid
            if self._verbose:
                line = f"HTTP -> [{n}] {request.method} {path}"
                body = request.get_data(cache=True)
                if body:
                    line += f" payload: {self._peek(body)}"
                self._log(line)

        @app.after_request
        def _trace_response(resp):         # noqa: ANN202
            rid = request.environ.get("zxnu.rid")
            with self._inflight_lock:
                entry = self._inflight.pop(rid, None)
                n = len(self._inflight)
            if request.environ.get("zxnu.trace_quiet"):
                return resp        # mid-file ranged slice: tracked, unlogged
            elapsed = time.monotonic() - entry[2] if entry else 0.0
            # A streamed body has no length to report yet; everything the
            # bridge serves today is a complete in-memory response.
            if resp.direct_passthrough:
                size = "streamed"
            else:
                size = f"{len(resp.get_data()):,} bytes"
            # ALWAYS logged, not just under -v: the request line alone says
            # what was ASKED, never whether it was answered, and "did the
            # bridge reply?" is the first question every stalled transfer
            # raises. One line per request, carrying the status, the body
            # size, how long it took and how many requests are still in
            # flight — the last of which is the live gauge, visible without
            # having to know where to look for it.
            self._log(f"HTTP bridge: {resp.status_code} {request.method} "
                      f"{request.path} ({size}, {elapsed:.1f}s"
                      + (f", {n} still in flight)" if n else ")"))
            return resp

        @app.teardown_request
        def _untrack_request(_exc=None):   # noqa: ANN202
            # after_request is skipped when the view raises, so the registry
            # is swept here too — a leaked entry would haunt the gauge and
            # the watchdog forever.
            rid = request.environ.get("zxnu.rid") if request else None
            if rid is not None:
                with self._inflight_lock:
                    self._inflight.pop(rid, None)

        def wants_json():
            return (request.args.get("json") in ("1", "true", "yes")
                    or "application/json" in (request.headers.get("Accept") or ""))

        def answer(payload, text_lines, status=200):
            """One reply, both shapes: `payload` is the JSON dict, `text_lines`
            the plain-text lines (.http-friendly)."""
            if wants_json():
                return Response(json.dumps(payload) + "\n", status=status,
                                mimetype="application/json")
            return Response("\n".join(text_lines) + "\n", status=status,
                            mimetype="text/plain")

        def fail(res, what):
            status = int(res.get("http", 502))
            err = res.get("error") or f"{what} FAILED on the Next"
            return answer({"ok": False, "error": err, "status": status},
                          [f"ERR {err}"], status)

        def sid_now():
            """This request's session selector (already validated by the
            before_request guard), or None for the active session."""
            return request.environ.get("zxnu.session")

        def skey(path):
            """Cache key for per-session state: sids are never reused within
            an app run, so (sid, path) can't alias across machines. None
            (the active session) is its own bucket — it may move between
            machines mid-flight, which is exactly the pre-session behaviour
            those callers opted into."""
            return (sid_now(), path)

        def run(op, a1="", a2="", body=None):
            sid = sid_now()
            self._log(f"HTTP bridge: {op} {a1} {a2}".rstrip()
                      + (f" [session {sid}]" if sid is not None else ""))
            if sid is None:
                # No selector -> the pre-session call shape, so adapters
                # (and test fakes) that never learned the kwarg still work.
                return self._adapter.run(op, a1, a2, body=body)
            return self._adapter.run(op, a1, a2, body=body, session=sid)

        def need(*names):
            """Fetch required query args (supporting aliases per name tuple);
            returns list of values or None when one is missing."""
            vals = []
            for aliases in names:
                v = ""
                for n in aliases:
                    v = (request.args.get(n) or "").strip()
                    if v:
                        break
                if not v:
                    return None
                vals.append(v)
            return vals

        def bad(msg):
            return answer({"ok": False, "error": msg, "status": 400},
                          [f"ERR {msg}"], 400)

        # ---- help -----------------------------------------------------
        @app.route("/")
        @app.route("/help")
        def _help():
            return Response(self.ROUTES_HELP, mimetype="text/plain")

        def adapter_roster():
            # getattr, not a straight call: adapters predating (or ignoring)
            # the session surface — tests ship minimal fakes — stay valid.
            fn = getattr(self._adapter, "roster", None)
            return fn() if fn is not None else None

        # ---- sessions -------------------------------------------------
        @app.route("/sessions")
        def _sessions():
            r = adapter_roster()
            if r is None:
                # Single-session host (nextsync5's console): synthesize the
                # one seat from state() so every client sees the same shape.
                st = self._adapter.state()
                if st.get("connected"):
                    rows = [(1, str(st.get("addr") or ""), "")]
                    active = 1
                else:
                    rows, active = [], None
                maxp = 1
            else:
                active, rows, maxp = r
            return answer(
                {"ok": True, "active": active, "count": len(rows),
                 "max": maxp,
                 "sessions": [{"sid": s, "addr": a, "name": n,
                               "label": session_label(s, a, n),
                               "active": s == active}
                              for s, a, n in rows]},
                [f"OK active: {active if active is not None else '-'} "
                 f"count: {len(rows)} max: {maxp}"]
                + [f"{s}\t{session_label(s, a, n)}" for s, a, n in rows])

        # ---- status ---------------------------------------------------
        @app.route("/status")
        def _status():
            st = self._adapter.state()
            roster = adapter_roster()
            # The drives/current pair below describes the ACTIVE session;
            # when the baton moves the cached answer is another machine's.
            active_now = roster[0] if roster else None
            if (self._drives_cache is not None
                    and self._drives_cache.get("sid") != active_now):
                self._drives_cache = None
            drives = st.get("drives")
            if not st.get("connected"):
                self._drives_cache = None
            elif drives is None:
                # The host doesn't track drives itself: query the Next once
                # per connection and cache.
                if self._drives_cache is None:
                    res = self._adapter.run("drives", timeout=15.0)
                    if res and res.get("ok"):
                        self._drives_cache = {
                            "sid": active_now,
                            "current": res.get("current", ""),
                            "drives": list(res.get("letters") or [])}
                if self._drives_cache is not None:
                    st = dict(st, current=self._drives_cache["current"],
                              drives=self._drives_cache["drives"])
                    drives = st["drives"]
            listening = bool(st.get("listening"))
            connected = bool(st.get("connected"))
            parts = len(drives) if drives else 0
            # /status is itself in flight while it answers; report the OTHER
            # requests, which is what "is something stuck?" actually asks.
            busy = self.inflight_detail()[1:]
            payload = {"ok": True, "listening": listening,
                       "connected": connected,
                       "current": st.get("current") or "",
                       "drives": list(drives or []), "partitions": parts,
                       "inflight": len(busy),
                       "busy": [{"seconds": round(s, 1), "request": r}
                                for s, r in busy]}
            # Additive multi-session lines (roster hosts only): appended
            # LAST so strict line-order parsers of the original shape —
            # ZXNextRemote strstr's "connected: yes" — never notice them.
            extra = []
            if roster is not None:
                payload["sessions"] = len(roster[1])
                payload["active"] = active_now
                extra = [f"sessions: {len(roster[1])}",
                         f"active: {active_now if active_now is not None else '-'}"]
            return answer(payload, [
                f"listening: {'yes' if listening else 'no'}",
                f"connected: {'yes' if connected else 'no'}",
                f"current: {st.get('current') or '-'}",
                f"drives: {' '.join(drives) if drives else '-'}",
                f"partitions: {parts}",
                f"inflight: {len(busy)}",
            ] + [f"busy: {s:.0f}s {r}" for s, r in busy] + extra)

        # ---- drives / free -------------------------------------------
        @app.route("/drives")
        def _drives():
            res = run("drives")
            if not res.get("ok"):
                return fail(res, "drives")
            letters = list(res.get("letters") or [])
            return answer(
                {"ok": True, "current": res.get("current", ""),
                 "drives": letters, "partitions": len(letters)},
                ["OK",
                 f"current: {res.get('current', '')}",
                 f"drives: {' '.join(letters)}",
                 f"partitions: {len(letters)}"])

        @app.route("/free")
        def _free():
            drive = (request.args.get("drive") or "").strip()
            res = run("free", drive)
            if not res.get("ok"):
                return fail(res, f"free {drive}")
            n = int(res.get("free") or 0)
            return answer(
                {"ok": True, "drive": drive or res.get("drive", ""),
                 "free_bytes": n, "free_human": fmt_size(n)},
                ["OK", f"drive: {drive or '(current)'}",
                 f"free: {n} bytes ({fmt_size(n)})"])

        # ---- identity (ZX Next Remote 1.0.2+ / .sync5 5.8+) -----------
        # Both routes ride the ONE wire ident query ('Y'): the responder
        # answers its flavor and its build in a single block, and the
        # session layer caches it, so asking for both costs one exchange
        # -- and against an OLD listener (which answers an unknown opcode
        # with silence and a brief self-healing desync) the toll is paid
        # once, then remembered. Built for update automation: the type
        # names the artifact to fetch, the number says whether to.
        def _ident_run():
            k = skey("version")
            if k[0] is not None and k in self._ident_cache:
                return self._ident_cache[k]
            res = run("version")
            if k[0] is not None:
                self._ident_cache[k] = res
            return res

        @app.route("/version-type")
        def _version_type():
            res = _ident_run()
            if not res.get("ok"):
                return fail(res, "version-type")
            v = res.get("type", "")
            return answer({"ok": True, "version-type": v},
                          ["OK", f"version-type: {v}"])

        @app.route("/version-number")
        def _version_number():
            res = _ident_run()
            if not res.get("ok"):
                return fail(res, "version-number")
            v = res.get("number", "")
            return answer({"ok": True, "version-number": v},
                          ["OK", f"version-number: {v}"])

        # ---- listing --------------------------------------------------
        @app.route("/ls")
        def _ls():
            v = need(("path", "dir"))
            path = v[0] if v else "/"
            res = run("ls", path)
            if not res.get("ok"):
                return fail(res, f"ls {path}")
            entries = res.get("entries") or []
            lines = [f"OK {len(entries)} entries"]
            for is_dir, size, name in entries:
                lines.append(f"{'D' if is_dir else 'F'}\t{size}\t{name}")
            return answer(
                {"ok": True, "path": path,
                 "entries": [{"dir": bool(d), "size": s, "name": n}
                             for d, s, n in entries]},
                lines)

        # ---- file transfer -------------------------------------------
        @app.route("/get")
        def _get():
            v = need(("path", "file"))
            if not v:
                return bad("missing ?path=")
            path = v[0]
            name = os.path.basename(path.rstrip("/"))
            b64 = request.args.get("b64") in ("1", "true", "yes")
            off_arg = request.args.get("off")
            if off_arg is None:
                res = run("get", path)
                if not res.get("ok"):
                    return fail(res, f"get {path}")
                data = res.get("data") or b""
                if b64:
                    # Base64 body: 7-bit-safe for CSpect's emulated ESP, where
                    # the caller adds .http's -7 flag to decode it back.
                    return Response(base64.b64encode(data) + b"\n",
                                    mimetype="text/plain")
                return Response(
                    data, mimetype="application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{name}"'})
            # ---- ranged slices (ZXNextRemote 0.7.10's overrun-proof retry).
            # &off=&len= serve windows of ONE relay, cached bridge-side: a
            # client whose UART cannot survive a streamed body (FIFO overrun
            # during its SD writes) pulls the file in verified bites while
            # the far Next still streams it only once. EOF for the client is
            # a short slice; X-Total-Size rides along for the curious.
            try:
                off = int(off_arg)
                ln = int(request.args.get("len") or "1280")
            except ValueError:
                return bad("off/len must be integers")
            if off < 0 or not (1 <= ln <= 16384):
                return bad("off/len out of range")
            with self._get_cache_lock:
                c = self._get_cache
                data = c["data"] if (off and c["path"] == skey(path)) else None
            if data is None:
                # A fresh file (off=0) — or an evicted cache: relay anew.
                res = run("get", path)
                if not res.get("ok"):
                    return fail(res, f"get {path}")
                data = res.get("data") or b""
                with self._get_cache_lock:
                    self._get_cache = {"path": skey(path), "data": data}
            chunk = bytes(data[off:off + ln])
            done = off + ln >= len(data)
            if done:
                with self._get_cache_lock:
                    if self._get_cache["path"] == skey(path):
                        self._get_cache = {"path": None, "data": b""}
            elif off and not self._verbose:
                # ~90 mid-file slices would bury the console: only the
                # relay (off=0) and the final slice keep their trace line.
                request.environ["zxnu.trace_quiet"] = True
            headers = {"X-Total-Size": str(len(data))}
            if b64:
                return Response(base64.b64encode(chunk) + b"\n",
                                mimetype="text/plain", headers=headers)
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
            return Response(chunk, mimetype="application/octet-stream",
                            headers=headers)

        def _put_append(path, body):
            """Chunked upload for callers that cannot send a whole file in
            one request — the Next's .http POSTs at most one 16K bank.
            Chunks accumulate in a bridge-side spool; when the declared
            total (&size=) has arrived, the assembled file is pushed to the
            Next in a single put (the -listen transfer handles any size).
            Re-declaring a different size for the same path restarts the
            upload, so a failed transfer can simply be sent again."""
            try:
                total = int(request.args.get("size") or "")
            except ValueError:
                total = -1
            if total < 0:
                return bad("append=1 needs &size=<total file bytes> "
                           "(read the file's length first)")
            if total > self.MAX_SPOOL:
                return bad(f"size {total} exceeds the chunked-upload cap "
                           f"of {self.MAX_SPOOL} bytes")
            with self._spool_lock:
                sp = self._put_spool.get(skey(path))
                if sp is None or sp["size"] != total:
                    sp = {"size": total, "data": bytearray(), "chunks": 0}
                    self._put_spool[skey(path)] = sp
                sp["data"] += body
                sp["chunks"] += 1
                got, chunks = len(sp["data"]), sp["chunks"]
                if got > total:
                    del self._put_spool[skey(path)]
                    return bad(f"append overflow: got {got} of {total} "
                               "declared bytes - upload dropped, send it "
                               "again from the first chunk")
                if got < total:
                    return answer(
                        {"ok": True, "path": path, "done": False,
                         "bytes": got, "size": total, "chunks": chunks},
                        [f"OK append {path} ({got}/{total} bytes)"])
                data = bytes(sp["data"])
                del self._put_spool[skey(path)]
            res = run("put", path, body=data)
            if not res.get("ok"):
                return fail(res, f"put {path}")
            return answer({"ok": True, "path": path, "done": True,
                           "bytes": total, "chunks": chunks},
                          [f"OK put {path} ({total} bytes, "
                           f"{chunks} chunks)"])

        @app.route("/put", methods=["POST", "PUT"])
        def _put():
            v = need(("path", "file"))
            if not v:
                return bad("missing ?path=")
            path = v[0]
            if path.endswith("/") or path.endswith("\\"):
                name = (request.args.get("name") or "").strip()
                if not name:
                    return bad("path ends with '/': add &name=<filename> "
                               "or give the full file path")
                path = path + name
            body = request.get_data() or b""
            if request.args.get("append") in ("1", "true", "yes"):
                return _put_append(path, body)
            with self._spool_lock:
                # A plain put overwrites: also discard any half-done chunked
                # upload spooled for the same path.
                self._put_spool.pop(skey(path), None)
            res = run("put", path, body=body)
            if not res.get("ok"):
                return fail(res, f"put {path}")
            return answer({"ok": True, "path": path, "bytes": len(body)},
                          [f"OK put {path} ({len(body)} bytes)"])

        def _sum16(data):
            return sum(data) & 0xFFFF

        @app.route("/sum")
        def _sum():
            """16-bit additive checksum (sum of all bytes mod 65536) + size
            of one remote file. Cheap for a NextBASIC caller to mirror while
            it uploads, so a transfer can be verified end-to-end; &bare=1
            answers just the checksum digits for trivial parsing."""
            v = need(("path", "file"))
            if not v:
                return bad("missing ?path=")
            res = run("get", v[0])
            if not res.get("ok"):
                return fail(res, f"sum {v[0]}")
            data = res.get("data") or b""
            csum = _sum16(data)
            if request.args.get("bare") in ("1", "true", "yes"):
                return Response(f"{csum}\n", mimetype="text/plain")
            return answer(
                {"ok": True, "path": v[0], "bytes": len(data), "sum16": csum},
                ["OK", f"path: {v[0]}", f"bytes: {len(data)}",
                 f"sum16: {csum}"])

        # ---- single-path verbs ---------------------------------------
        def _path_verb(op, what):
            v = need(("path",))
            if not v:
                return bad("missing ?path=")
            res = run(op, v[0])
            if not res.get("ok"):
                return fail(res, f"{what} {v[0]}")
            return answer({"ok": True, "path": v[0]}, [f"OK {what} {v[0]}"])

        @app.route("/mkdir")
        def _mkdir():
            return _path_verb("mkdir", "mkdir")

        @app.route("/rmdir")
        def _rmdir():
            return _path_verb("rmdir", "rmdir")

        @app.route("/rmtree")
        def _rmtree():
            return _path_verb("rmtree", "rmtree")

        @app.route("/rm")
        def _rm():
            return _path_verb("rm", "rm")

        # ---- two-path verbs ------------------------------------------
        @app.route("/ren")
        def _ren():
            v = need(("from", "old"), ("to", "new"))
            if not v:
                return bad("missing ?from=&to=")
            res = run("ren", v[0], v[1])
            if not res.get("ok"):
                return fail(res, f"ren {v[0]}")
            return answer({"ok": True, "from": v[0], "to": v[1]},
                          [f"OK ren {v[0]} -> {v[1]}"])

        @app.route("/rcpy")
        def _rcpy():
            v = need(("src", "from"), ("dst", "to"))
            if not v:
                return bad("missing ?src=&dst=")
            src, dst = v
            # Same infinite-trap guard as every other rcpy front-end: a
            # folder copied into itself makes the Next-side walk re-read its
            # own growing output forever.
            s = src.rstrip("/").lower()
            d = dst.rstrip("/").lower()
            if d == s or d.startswith(s + "/"):
                return bad("destination equals or is inside the source")
            res = run("rcpy", src, dst)
            if not res.get("ok"):
                return fail(res, f"rcpy {src}")
            n = int(res.get("files") or 0)
            return answer({"ok": True, "src": src, "dst": dst, "files": n},
                          [f"OK rcpy {src} -> {dst} ({n} file(s))"])

        @app.route("/rfsize")
        def _rfsize():
            v = need(("path",))
            if not v:
                return bad("missing ?path=")
            res = run("rfsize", v[0])
            if not res.get("ok"):
                return fail(res, f"rfsize {v[0]}")
            files = int(res.get("files") or 0)
            dirs = int(res.get("dirs") or 0)
            nbytes = int(res.get("bytes") or 0)
            return answer(
                {"ok": True, "path": v[0], "files": files, "dirs": dirs,
                 "bytes": nbytes, "human": fmt_size(nbytes)},
                ["OK", f"files: {files}", f"folders: {dirs}",
                 f"bytes: {nbytes} ({fmt_size(nbytes)})"])

        # ---- session control -----------------------------------------
        @app.route("/forceexit")
        def _forceexit():
            # Tell the connected Next to leave -listen mode: the dot's next
            # poll is answered with the protocol's 'Q', on which it closes
            # the connection and exits cleanly to BASIC. The -listen server
            # keeps running, so a fresh '.sync5 -listen' can reconnect.
            res = run("forceexit")
            if not res.get("ok"):
                return fail(res, "forceexit")
            return answer({"ok": True},
                          ["OK forceexit - the Next is disconnecting"])
