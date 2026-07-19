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
  bridge op (ls/get/put/mkdir/rmdir/rmtree/rm/ren/rcpy/rfsize/free/drives)
  into the host's own command-tuple dialect, with ``reply`` (a
  :class:`BridgeReply`) riding along as the LAST element (None = the host
  doesn't support that op → HTTP 501);
* ``state() -> dict``   {'listening': bool, 'connected': bool,
  'current': str, 'drives': list|None}.

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
"""

import importlib.util
import json
import os
import queue
import shutil
import socket
import tempfile
import threading

DEFAULT_PORT = 80          # .http's default; HTTP only (no TLS on the Next)


def flask_available():
    """True when the optional Flask package is installed. Cheap (no import
    happens), so hosts can gate their UI / -w flag on it without ever
    triggering an ImportError at startup."""
    try:
        return importlib.util.find_spec("flask") is not None
    except (ImportError, ValueError):
        return False
DEFAULT_TIMEOUT = 45.0     # quick verbs: one poll round-trip + margin
LONG_TIMEOUT = 900.0       # get/put/rcpy/rfsize/rmtree can move real data
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
    queue. The executor calls :meth:`put` exactly once with the result dict;
    the HTTP thread blocks in :meth:`wait`. Hosts detect a bridge command by
    ``isinstance(cmd[-1], BridgeReply)``."""

    def __init__(self):
        self._q = queue.Queue()

    def put(self, result):
        self._q.put(dict(result))

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

    def __init__(self, enqueue, make_cmd, state):
        self._enqueue = enqueue
        self._make_cmd = make_cmd
        self._state = state
        self._lock = threading.Lock()

    def state(self):
        try:
            return dict(self._state())
        except Exception as ex:                      # noqa: BLE001
            return {"listening": False, "connected": False,
                    "current": "", "drives": None, "error": str(ex)}

    def run(self, op, a1="", a2="", body=None, timeout=None):
        """Run one canonical bridge op against the connected Next. Returns the
        executor's result dict; on bridge-level failures the dict carries
        ``ok: False`` plus an ``http`` status suggestion (501/503/504)."""
        st = self.state()
        if not st.get("connected"):
            return {"ok": False, "http": 503,
                    "error": "no Next is connected in '.sync5 -listen' mode"}
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
                if not self._enqueue(cmd):
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


class NextSyncHttpBridge:
    """The Flask web server. Construct with a :class:`QueueBridgeHost` (or
    anything exposing ``state()`` and ``run()``), then :meth:`start` /
    :meth:`stop`. Every route answers plain text by default (friendly to a
    Next parsing with ``.http``); append ``&json=1`` for JSON."""

    ROUTES_HELP = (
        "NextSync HTTP bridge - drive the Next connected in '.sync5 -listen'\n"
        "Routes (text by default; append &json=1 for JSON):\n"
        "  GET  /status                     server + Next state, partitions\n"
        "  GET  /drives                     mounted drive letters\n"
        "  GET  /free?drive=C               free space on a partition\n"
        "  GET  /ls?path=/games             directory listing\n"
        "  GET  /get?path=/games/a.tap      download one file (raw bytes)\n"
        "  POST /put?path=/games/a.tap      upload (request body = the file)\n"
        "  GET  /mkdir?path=/newdir         create a directory\n"
        "  GET  /rmdir?path=/olddir         remove an EMPTY directory\n"
        "  GET  /rmtree?path=/olddir        remove a directory recursively\n"
        "  GET  /rm?path=/old.tap           delete a file\n"
        "  GET  /ren?from=/a&to=/b          rename / move\n"
        "  GET  /rcpy?src=/a&dst=m:/b       copy ON the Next (across drives)\n"
        "  GET  /rfsize?path=/games         total size of a file / tree\n")

    def __init__(self, host_adapter, listen_host="0.0.0.0", port=DEFAULT_PORT,
                 log=None, verbose=False):
        self._adapter = host_adapter
        self._listen_host = listen_host
        self._port = int(port)
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

    # ------------------------------------------------------------------
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
        self._log(f"HTTP bridge: serving on port {self._port}")
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

        if self._verbose:
            # -v troubleshooting: one log line per request with its payload,
            # and one per response with status + body preview.
            @app.before_request
            def _trace_request():          # noqa: ANN202
                body = request.get_data(cache=True)
                line = f"HTTP > {request.method} {request.full_path.rstrip('?')}"
                if body:
                    line += f" payload: {self._peek(body)}"
                self._log(line)

            @app.after_request
            def _trace_response(resp):     # noqa: ANN202
                preview = "(streamed)"
                if not resp.direct_passthrough:
                    preview = self._peek(resp.get_data())
                self._log(f"HTTP < {resp.status_code} "
                          f"{request.method} {request.path} {preview}")
                return resp

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

        def run(op, a1="", a2="", body=None):
            self._log(f"HTTP bridge: {op} {a1} {a2}".rstrip())
            return self._adapter.run(op, a1, a2, body=body)

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

        # ---- status ---------------------------------------------------
        @app.route("/status")
        def _status():
            st = self._adapter.state()
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
                            "current": res.get("current", ""),
                            "drives": list(res.get("letters") or [])}
                if self._drives_cache is not None:
                    st = dict(st, current=self._drives_cache["current"],
                              drives=self._drives_cache["drives"])
                    drives = st["drives"]
            listening = bool(st.get("listening"))
            connected = bool(st.get("connected"))
            parts = len(drives) if drives else 0
            payload = {"ok": True, "listening": listening,
                       "connected": connected,
                       "current": st.get("current") or "",
                       "drives": list(drives or []), "partitions": parts}
            return answer(payload, [
                f"listening: {'yes' if listening else 'no'}",
                f"connected: {'yes' if connected else 'no'}",
                f"current: {st.get('current') or '-'}",
                f"drives: {' '.join(drives) if drives else '-'}",
                f"partitions: {parts}",
            ])

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
            res = run("get", v[0])
            if not res.get("ok"):
                return fail(res, f"get {v[0]}")
            name = os.path.basename(v[0].rstrip("/"))
            return Response(
                res.get("data") or b"", mimetype="application/octet-stream",
                headers={"Content-Disposition":
                         f'attachment; filename="{name}"'})

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
            res = run("put", path, body=body)
            if not res.get("ok"):
                return fail(res, f"put {path}")
            return answer({"ok": True, "path": path, "bytes": len(body)},
                          [f"OK put {path} ({len(body)} bytes)"])

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
