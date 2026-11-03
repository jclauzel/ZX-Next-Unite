"""zxnu_mcp.py — the MCP (Model Context Protocol) server of ZX-Next-Unite.

An AI agent (Claude Code, Claude Desktop, Cursor, any MCP client) drives the
running app through it: load a disk image, put files into it, start MAME or
CSpect on it, start the RS232 ESP emulation, start the NextSync listener,
list the Nexts seated on it, push files to one and run them, read the
consoles. The tools are the same operations the GUI offers, reached through
the same operation closures, so an agent can never do what a user cannot.

Shape:

    MCP client --stdio--> zx-next-unite-mcp (this module's stdio_main)
                              |  HTTP, loopback, JSON-RPC 2.0
                              v
    MCP client --Streamable HTTP--> McpHttpServer (inside the running GUI)
                                        |  tool handler
                                        v
                                   McpHost adapter (zxnu_mcp_ops: marshals
                                   onto the Qt UI thread, or straight to
                                   the -listen worker's queue)

Why TWO transports. The GUI is already running, so an MCP client cannot
spawn it the way it spawns a stdio server; what it CAN do is talk HTTP to
it. The app therefore hosts the Streamable HTTP transport (``POST /mcp``,
JSON-RPC in, JSON out) on 127.0.0.1 — ``claude mcp add --transport http
zx-next-unite http://127.0.0.1:2050/mcp`` is the whole client-side setup.
Clients that only speak stdio (Claude Desktop's config file, older hosts)
get ``zx-next-unite-mcp``, a shim that reads JSON-RPC lines from stdin and
POSTs each one to the GUI; it carries no logic of its own.

Why no ``mcp`` package. The official SDK pulls pydantic, anyio, httpx,
starlette and uvicorn into a PyInstaller --onefile GUI, for a protocol
surface this server needs five methods of (initialize, ping, tools/list,
tools/call, notifications/initialized). Like espemu and the HTTP bridge this
module is STDLIB-ONLY: no Qt, no third-party import, so it is unit-tested
headlessly (tests/test_mcp_server.py) and the frozen build needs nothing.

Protocol notes (MCP 2025-06-18, also answering 2025-03-26 / 2024-11-05
clients with the version they asked for):

* a tools/call answer is ``{"content": [{"type": "text", ...}],
  "isError": bool}``; a dict result is ALSO returned as
  ``structuredContent`` and rendered as indented JSON in the text block,
  so both a schema-aware client and a plain one read the same facts;
* a tool failure the user could act on (no image loaded, no Next
  connected, hdfmonkey refused) is an ``isError`` RESULT, never a JSON-RPC
  error: the agent reads the reason and tries something else, while a
  protocol error (unknown tool, bad arguments) is JSON-RPC -32602;
* the HTTP transport answers a notification with 202 and no body, a
  request with 200 ``application/json`` (never SSE — nothing here streams),
  ``GET /mcp`` with 405 (no server-initiated stream), and refuses an
  ``Origin`` header that is not loopback with 403 (the DNS-rebinding
  guard the spec asks for — this server binds loopback only and the
  guard is what keeps a web page in the user's browser from reaching it);
* an optional bearer token (``Authorization: Bearer <token>``, or the
  ``ZXNEXTUNITE-MCP-TOKEN`` header for clients that cannot set
  Authorization) answers 401 when missing or wrong, compared in constant
  time, the HTTP bridge's rule.
"""

import hmac
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MCP_DEFAULT_PORT = 2050          # loopback only; 2048 is NextSync, 2222 espemu
MCP_PATH = "/mcp"
MCP_TOKEN_HEADER = "ZXNEXTUNITE-MCP-TOKEN"
SERVER_NAME = "zx-next-unite"
SERVER_TITLE = "ZX Next Unite"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY_BYTES = 16 * 1024 * 1024   # tool arguments are paths and names, never file bodies

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Application errors the stdio shim reports when the GUI cannot be reached.
SERVER_UNREACHABLE = -32000
SERVER_REFUSED = -32001

ENV_URL = "ZX_NEXT_UNITE_MCP_URL"
ENV_TOKEN = "ZX_NEXT_UNITE_MCP_TOKEN"
ENV_PORT = "ZX_NEXT_UNITE_MCP_PORT"


class McpToolError(Exception):
    """Raised by a tool handler when the operation cannot be done for a
    reason the agent should read and act on (no image loaded, no Next
    connected, the file is not there). Becomes an ``isError`` RESULT with
    the message as its text — never a JSON-RPC error, which clients treat
    as a broken server rather than a refused operation."""


def param(ptype, description, **extra):
    """One JSON-schema property: ``param("string", "the file")``. ``extra``
    adds ``enum=``, ``default=``, ``minimum=`` … verbatim."""
    d = {"type": ptype, "description": description}
    d.update(extra)
    return d


class McpTool:
    """One tool: its name, description, input schema and handler.

    ``handler(args)`` takes the validated argument dict and returns a str
    (one text block), a dict (text = indented JSON, plus
    ``structuredContent``), a list of MCP content blocks, or raises
    :class:`McpToolError`."""

    __slots__ = ("name", "title", "description", "input_schema", "handler")

    def __init__(self, name, description, handler, properties=None,
                 required=(), title=None):
        self.name = name
        self.title = title
        self.description = description
        self.handler = handler
        self.input_schema = {
            "type": "object",
            "properties": dict(properties or {}),
            "required": list(required),
            "additionalProperties": False,
        }

    def spec(self):
        d = {"name": self.name, "description": self.description,
             "inputSchema": self.input_schema}
        if self.title:
            d["title"] = self.title
        return d


_JSON_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_arguments(schema, args):
    """A light validator: required present, declared types right, no
    unknown names, enums honoured. Returns a list of problems (empty =
    valid). Deliberately small — the schemas here are flat and an agent
    reads the message, so precision beats completeness."""
    problems = []
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    props = schema.get("properties") or {}
    for name in schema.get("required") or ():
        if name not in args:
            problems.append(f"missing required argument '{name}'")
    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            if schema.get("additionalProperties") is False:
                problems.append(f"unknown argument '{name}'")
            continue
        want = spec.get("type")
        if want in _JSON_TYPES and value is not None:
            ok_types = _JSON_TYPES[want]
            # bool is an int in Python; keep the JSON distinction.
            if isinstance(value, bool) and want in ("integer", "number"):
                problems.append(f"argument '{name}' must be a {want}")
            elif not isinstance(value, ok_types):
                problems.append(f"argument '{name}' must be a {want}")
        enum = spec.get("enum")
        if enum and value is not None and value not in enum:
            problems.append(f"argument '{name}' must be one of "
                            + ", ".join(repr(e) for e in enum))
    return problems


def _error(rid, code, message, data=None):
    err = {"code": int(code), "message": str(message)}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def tool_result(value, is_error=False):
    """Render a handler's return value as a tools/call result."""
    if isinstance(value, list):
        content = value
        res = {"content": content}
    elif isinstance(value, dict):
        res = {"content": [{"type": "text",
                            "text": json.dumps(value, indent=2,
                                               ensure_ascii=False)}],
               "structuredContent": value}
    else:
        res = {"content": [{"type": "text", "text": "" if value is None
                            else str(value)}]}
    if is_error:
        res["isError"] = True
    return res


class McpServer:
    """The JSON-RPC 2.0 dispatcher: transport-agnostic, one instance shared
    by every HTTP thread (handlers must be thread-safe themselves — the
    tool layer serialises what needs serialising)."""

    def __init__(self, tools, version, instructions="", log=None,
                 on_initialize=None):
        self._tools = {}
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"duplicate MCP tool name {t.name!r}")
            self._tools[t.name] = t
        self._version = str(version)
        self._instructions = instructions or ""
        self._log = log or (lambda s: None)
        self._on_initialize = on_initialize
        self.session_id = uuid.uuid4().hex
        self.calls = 0            # tools/call count, for the status surface
        self.last_client = ""     # "name version" of the last initialize

    @property
    def tools(self):
        return list(self._tools.values())

    # ---- dispatch ----------------------------------------------------
    def handle_text(self, text):
        """Parse one transport payload (a message or a batch) and dispatch.
        Returns (reply, is_batch): reply is a dict, a list (batch) or None
        (nothing to send back — notifications only)."""
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as ex:
            return _error(None, PARSE_ERROR, f"parse error: {ex}"), False
        if isinstance(data, list):
            if not data:
                return _error(None, INVALID_REQUEST,
                              "invalid request: empty batch"), True
            replies = [r for r in (self.handle_message(m) for m in data)
                       if r is not None]
            return (replies or None), True
        return self.handle_message(data), False

    def handle_message(self, msg):
        """Dispatch one JSON-RPC message. Returns the reply dict, or None
        for a notification (or a client response, which this server never
        solicits and therefore ignores)."""
        if not isinstance(msg, dict):
            return _error(None, INVALID_REQUEST,
                          "invalid request: not an object")
        rid = msg.get("id")
        method = msg.get("method")
        if msg.get("jsonrpc") != "2.0":
            return _error(rid, INVALID_REQUEST,
                          "invalid request: jsonrpc must be '2.0'")
        if method is None:
            # A response to a request we never sent: nothing to say.
            return None
        if not isinstance(method, str):
            return _error(rid, INVALID_REQUEST,
                          "invalid request: method must be a string")
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object")
        is_notification = "id" not in msg
        try:
            if method.startswith("notifications/"):
                # initialized / cancelled / progress / roots-list-changed:
                # nothing to do, nothing to answer.
                return None
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t.spec() for t in self._tools.values()]}
            elif method == "tools/call":
                result = self._call(params)
            else:
                if is_notification:
                    return None
                return _error(rid, METHOD_NOT_FOUND,
                              f"method not found: {method}")
        except _ParamsError as ex:
            if is_notification:
                return None
            return _error(rid, INVALID_PARAMS, str(ex))
        except Exception as ex:                          # noqa: BLE001
            self._log("MCP: internal error in %s: %s" % (method, ex))
            self._log(traceback.format_exc())
            if is_notification:
                return None
            return _error(rid, INTERNAL_ERROR, f"internal error: {ex}")
        if is_notification:
            return None
        return _result(rid, result)

    def _initialize(self, params):
        asked = str(params.get("protocolVersion") or "")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        info = params.get("clientInfo") or {}
        if isinstance(info, dict):
            self.last_client = " ".join(
                s for s in (str(info.get("name") or ""),
                            str(info.get("version") or "")) if s).strip()
        self._log("MCP: client connected%s (protocol %s)"
                  % (f": {self.last_client}" if self.last_client else "",
                     version))
        if self._on_initialize is not None:
            try:
                self._on_initialize(self.last_client, version)
            except Exception:                            # noqa: BLE001
                pass
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE,
                           "version": self._version},
        }
        if self._instructions:
            result["instructions"] = self._instructions
        return result

    def _call(self, params):
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _ParamsError("tools/call needs a tool name")
        tool = self._tools.get(name)
        if tool is None:
            raise _ParamsError(f"unknown tool: {name}")
        args = params.get("arguments")
        if args is None:
            args = {}
        problems = validate_arguments(tool.input_schema, args)
        if problems:
            raise _ParamsError(f"invalid arguments for {name}: "
                               + "; ".join(problems))
        self.calls += 1
        t0 = time.monotonic()
        try:
            value = tool.handler(dict(args))
        except McpToolError as ex:
            self._log("MCP: %s -> refused (%.1fs): %s"
                      % (name, time.monotonic() - t0, ex))
            return tool_result(str(ex), is_error=True)
        except Exception as ex:                          # noqa: BLE001
            self._log("MCP: %s -> failed (%.1fs): %s"
                      % (name, time.monotonic() - t0, ex))
            self._log(traceback.format_exc())
            return tool_result(f"{name} failed: {ex}", is_error=True)
        self._log("MCP: %s -> ok (%.1fs)" % (name, time.monotonic() - t0))
        return tool_result(value)


class _ParamsError(Exception):
    """Internal: a tools/call whose name or arguments are wrong."""


# ---------------------------------------------------------------------------
# The Streamable HTTP transport
# ---------------------------------------------------------------------------
def _is_loopback_origin(origin):
    """True when an Origin header names this machine (any scheme, any
    port). An absent header is a non-browser client and is accepted by
    the caller; a present one that is not loopback is a web page in the
    user's browser trying to reach a loopback server — refused."""
    try:
        host = urllib.parse.urlsplit(origin).hostname or ""
    except ValueError:
        return False
    host = host.strip().lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


class _McpHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    # http.server defaults allow_reuse_address to 1. On Windows that is
    # SO_REUSEADDR, which lets a SECOND server bind a port already served,
    # so "port in use" would never be reported and the two would share the
    # connections. Keep it for the POSIX TIME_WAIT case only.
    allow_reuse_address = os.name != "nt"

    def __init__(self, addr, handler, mcp):
        self.mcp = mcp
        super().__init__(addr, handler)

    def handle_error(self, request, client_address):
        """socketserver's default prints a traceback to stderr for ANY
        exception a handler thread raises - and with HTTP/1.1 keep-alive a
        client that simply closes its connection (Claude Code's health
        probe, a shim ending, any disconnect) raises ConnectionResetError
        out of the next readline on Windows (WinError 10054), BrokenPipe
        elsewhere. That is the link ending, not an error: swallowed. Any
        other exception goes to the file log, never to a console nobody
        reads."""
        ex = sys.exc_info()[1]
        if isinstance(ex, (ConnectionResetError, ConnectionAbortedError,
                           BrokenPipeError, TimeoutError)):
            return
        logging.exception("MCP: request from %s failed", client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ZXNextUnite-MCP"
    sys_version = ""

    def log_message(self, format, *args):     # noqa: A002 - BaseHTTPRequestHandler's name
        # Quiet: the dispatcher logs what matters (tool calls, clients).
        return

    # ---- helpers ---------------------------------------------------
    def _send_json(self, status, payload, extra_headers=()):
        body = (json.dumps(payload) + "\n").encode("utf-8") if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", self.server.mcp.server.session_id)
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_text(self, status, text, extra_headers=()):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _path_ok(self):
        return self.path.split("?", 1)[0].rstrip("/") == MCP_PATH

    def _authorised(self):
        token = self.server.mcp.auth_token
        if token is None:
            return True
        supplied = ""
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not supplied:
            supplied = (self.headers.get(MCP_TOKEN_HEADER) or "").strip()
        return bool(supplied) and hmac.compare_digest(supplied, token)

    def _refuse_if_needed(self):
        """The guards every /mcp request passes: path, token, origin.
        Returns True when a refusal was sent."""
        if not self._path_ok():
            self._send_json(404, _error(None, INVALID_REQUEST,
                                        f"not found: POST {MCP_PATH}"))
            return True
        if not self._authorised():
            self._send_json(401, _error(None, SERVER_REFUSED,
                                        "missing or invalid bearer token"),
                            [("WWW-Authenticate", 'Bearer realm="zx-next-unite"')])
            return True
        origin = self.headers.get("Origin")
        if origin and not _is_loopback_origin(origin):
            self._send_json(403, _error(None, SERVER_REFUSED,
                                        "origin not allowed"))
            return True
        return False

    # ---- verbs -------------------------------------------------------
    def do_POST(self):            # noqa: N802 - http.server's name
        if self._refuse_if_needed():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(413 if length > MAX_BODY_BYTES else 400,
                            _error(None, INVALID_REQUEST, "bad Content-Length"))
            return
        raw = self.rfile.read(length) if length else b""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._send_json(400, _error(None, PARSE_ERROR,
                                        "body is not UTF-8"))
            return
        reply, _is_batch = self.server.mcp.server.handle_text(text)
        if reply is None:
            # A notification (or a batch of them): accepted, nothing to say.
            self._send_json(202, None)
            return
        status = 200
        if isinstance(reply, dict) and "error" in reply and reply.get("id") is None \
                and reply["error"].get("code") in (PARSE_ERROR, INVALID_REQUEST):
            status = 400
        self._send_json(status, reply)

    def do_GET(self):             # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/help"):
            self._send_text(200, self.server.mcp.help_text())
            return
        if path == MCP_PATH:
            # No server-initiated stream here: nothing is ever pushed.
            self._send_json(405, _error(None, INVALID_REQUEST,
                                        "GET not supported: POST JSON-RPC here"),
                            [("Allow", "POST, DELETE")])
            return
        self._send_json(404, _error(None, INVALID_REQUEST, "not found"))

    def do_DELETE(self):          # noqa: N802
        if self._refuse_if_needed():
            return
        # The client ends its session. Nothing is held per session.
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


class McpHttpServer:
    """The loopback HTTP front of an :class:`McpServer`. ``start()`` binds
    synchronously (so a port in use is reported by the call, and port 0
    reads back the chosen port) and serves on a daemon thread; ``stop()``
    is safe from any other thread."""

    def __init__(self, server, port=MCP_DEFAULT_PORT, auth_token=None,
                 listen_host="127.0.0.1", log=None):
        self.server = server
        self._port = int(port)
        self._listen_host = listen_host
        self.auth_token = (auth_token or "").strip() or None
        self._log = log or (lambda s: None)
        self._httpd = None
        self._thread = None
        self.port_in_use = False
        self.started_at = None

    @property
    def running(self):
        return self._httpd is not None

    @property
    def port(self):
        if self._httpd is not None:
            return self._httpd.server_address[1]
        return self._port

    @property
    def url(self):
        return f"http://{self._listen_host}:{self.port}{MCP_PATH}"

    def help_text(self):
        srv = self.server
        return ("ZX Next Unite MCP server %s\n"
                "POST JSON-RPC 2.0 (MCP %s) to %s\n"
                "tools: %s\n"
                "stdio shim: zx-next-unite-mcp --url %s\n"
                % (srv._version, PROTOCOL_VERSIONS[0], MCP_PATH,
                   ", ".join(t.name for t in srv.tools), self.url))

    def start(self):
        if self._httpd is not None:
            return True
        self.port_in_use = False
        try:
            httpd = _McpHttpServer((self._listen_host, self._port), _Handler, self)
        except OSError as ex:
            # EADDRINUSE / WinError 10048 (and 10013, Windows' answer when
            # a reserved port is asked for).
            if getattr(ex, "errno", None) in (98, 48, 10048, 10013) or \
                    getattr(ex, "winerror", None) in (10048, 10013):
                self.port_in_use = True
            raise
        self._httpd = httpd
        self.started_at = time.time()
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        kwargs={"poll_interval": 0.5},
                                        name="zxnu-mcp-http", daemon=True)
        self._thread.start()
        self._log("MCP server listening on %s" % self.url)
        return True

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        try:
            httpd.shutdown()
        except Exception:                                # noqa: BLE001
            pass
        try:
            httpd.server_close()
        except Exception:                                # noqa: BLE001
            pass
        self._thread = None
        self.started_at = None
        self._log("MCP server stopped")


# ---------------------------------------------------------------------------
# The stdio shim (zx-next-unite-mcp / zx-next-unite --mcp-stdio)
# ---------------------------------------------------------------------------
STDIO_USAGE = """\
zx-next-unite-mcp - stdio front for the MCP server inside a running ZX Next Unite

  zx-next-unite-mcp [--url http://127.0.0.1:2050/mcp] [--port N] [--token T]
                    [--timeout SECONDS]

Reads MCP JSON-RPC lines on stdin and relays each one to the app's loopback
HTTP transport (Settings -> MCP server). Environment: %s, %s, %s.
Claude Desktop: {"mcpServers": {"zx-next-unite": {"command": "zx-next-unite-mcp"}}}
Claude Code can skip the shim: claude mcp add --transport http zx-next-unite <url>
""" % (ENV_URL, ENV_PORT, ENV_TOKEN)


def stdio_target(argv=None, environ=None):
    """(url, token, timeout_s) from the shim's arguments and environment,
    arguments winning. ``--help`` raises SystemExit(0) after printing."""
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    url = env.get(ENV_URL) or ""
    port = env.get(ENV_PORT) or ""
    token = env.get(ENV_TOKEN) or ""
    timeout = 7200.0
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None

        def take():
            if nxt is None:
                raise SystemExit(f"{a} needs a value")
            return nxt
        if a in ("-h", "--help"):
            print(STDIO_USAGE, end="")
            raise SystemExit(0)
        if a == "--url":
            url = take(); i += 2
        elif a.startswith("--url="):
            url = a[6:]; i += 1
        elif a == "--port":
            port = take(); i += 2
        elif a.startswith("--port="):
            port = a[7:]; i += 1
        elif a == "--token":
            token = take(); i += 2
        elif a.startswith("--token="):
            token = a[8:]; i += 1
        elif a == "--timeout":
            timeout = float(take()); i += 2
        elif a.startswith("--timeout="):
            timeout = float(a[10:]); i += 1
        elif a == "--mcp-stdio":
            i += 1                      # the app's own switch, already honoured
        else:
            raise SystemExit(f"unknown option {a}\n" + STDIO_USAGE)
    if not url:
        try:
            p = int(port) if port else MCP_DEFAULT_PORT
        except ValueError:
            raise SystemExit(f"bad port {port!r}") from None
        url = f"http://127.0.0.1:{p}{MCP_PATH}"
    return url, token.strip(), timeout


def _unreachable_message(url, ex):
    return ("ZX Next Unite is not running, or its MCP server is off "
            "(Settings -> \"MCP server\") - could not reach %s: %s" % (url, ex))


def relay_one(url, token, text, timeout):
    """POST one JSON-RPC payload; return the decoded reply (dict / list /
    None for 202). Raises urllib errors through to the caller."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if resp.status == 202 or not body.strip():
            return None
        return json.loads(body.decode("utf-8"))


def stdio_main(argv=None, stdin=None, stdout=None, environ=None):
    """Run the shim until stdin closes. Every REQUEST is relayed on its own
    thread, so a long tool call never blocks a ping; replies are written
    one per line under a lock. Returns the process exit code."""
    try:
        url, token, timeout = stdio_target(argv, environ)
    except SystemExit as ex:
        if ex.code and not isinstance(ex.code, int):
            sys.stderr.write(str(ex.code) + "\n")
            return 2
        return int(ex.code or 0)
    inp = stdin if stdin is not None else sys.stdin.buffer
    out = stdout if stdout is not None else sys.stdout.buffer
    write_lock = threading.Lock()
    threads = []

    def emit(obj):
        line = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        with write_lock:
            out.write(line)
            out.flush()

    def relay(raw, rid, has_id):
        try:
            reply = relay_one(url, token, raw, timeout)
        except urllib.error.HTTPError as ex:
            if not has_id:
                return
            body = b""
            try:
                body = ex.read()
            except Exception:                            # noqa: BLE001
                pass
            try:
                parsed = json.loads(body.decode("utf-8"))
                if isinstance(parsed, dict) and "error" in parsed:
                    parsed["id"] = rid
                    emit(parsed)
                    return
            except (ValueError, UnicodeDecodeError):
                pass
            code = SERVER_REFUSED if ex.code in (401, 403) else SERVER_UNREACHABLE
            emit(_error(rid, code, "the MCP server answered HTTP %d: %s"
                        % (ex.code, body.decode("utf-8", "replace").strip()[:300])))
            return
        except (urllib.error.URLError, OSError, ValueError) as ex:
            if has_id:
                emit(_error(rid, SERVER_UNREACHABLE,
                            _unreachable_message(url, getattr(ex, "reason", ex))))
            return
        if reply is None:
            return
        if isinstance(reply, list):
            for r in reply:
                emit(r)
        else:
            emit(reply)

    for raw in iter(inp.readline, b""):
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            emit(_error(None, PARSE_ERROR, f"parse error: {ex}"))
            continue
        has_id = isinstance(msg, dict) and "id" in msg
        rid = msg.get("id") if isinstance(msg, dict) else None
        t = threading.Thread(target=relay, args=(line, rid, has_id),
                             daemon=True)
        t.start()
        threads.append(t)
        threads = [x for x in threads if x.is_alive()]
    for t in threads:
        t.join(timeout=5.0)
    return 0


# ---------------------------------------------------------------------------
# The tools, over a host protocol
# ---------------------------------------------------------------------------
# ``build_mcp_tools(host)`` builds every tool over a HOST object the app
# supplies (zxnu_mcp_ops._AppMcpHost; tests supply a fake). The protocol, so
# both stay honest — every method is called from an HTTP thread and may raise
# McpToolError:
#
#   version() -> str
#   status() -> dict                                  app_status's whole answer
#   log_tail(console, lines) -> [str]                 "sdcard" | "nextsync" | "file"
#   image_status() -> dict
#   image_load(path, timeout) -> (ok, message)        "" unloads
#   image_recent() -> [dict]
#   image_download(size, dest_dir, load) -> dict      {"zip","image","loaded"}
#   image_path() -> str                               "" when none is loaded
#   hdfmonkey(verb, argv) -> (rc, stdout, stderr)     on the LOADED image
#   image_refresh(path)                               re-list a folder in the explorer
#   emulators() -> [dict{name, blocked}]
#   launch(emulator, file, card_path, cspect_mcp, cspect_mcp_port)
#       -> (ok, message, pid, extra: dict)   extra = {"cspect_mcp_port": N} or {}
#   processes() -> [dict]
#   stop_process(pid) -> (ok, message)
#   esp_status() -> dict
#   esp_start(port, verbose) -> (ok, message, port)
#   esp_stop(force) -> (ok, message)
#   nextsync_status() -> dict
#   listener_start(sync_root) -> (ok, message)
#   listener_stop() -> (ok, message)
#   set_sync_root(path) -> (ok, message)
#   classic_start(mode) -> (ok, message)              "once" | "always"
#   classic_stop() -> (ok, message)
#   bridge_start(port) -> (ok, message)
#   bridge_stop() -> (ok, message)
#   next_sessions() -> (active, [(sid, addr, name)], max_peers) | None
#   next_cmd(op, a1, a2, session, timeout) -> dict    the worker's reply dict
#   next_select(sid) -> (ok, message)

from zxnu_http_bridge import DEFAULT_TIMEOUT, LONG_TIMEOUT, peer_lacks_crc  # noqa: E402

LOAD_TIMEOUT = 180.0          # hdfmonkey listing a huge image's root
LAUNCH_TIMEOUT = 150.0        # launch_mame probes -listslots (20 s) + stages a file
NEXT_DEFAULT_DEPLOY_PATH = "/home/incoming.nex"   # extra/autoexec.bas's drop point

MCP_INSTRUCTIONS = """\
ZX Next Unite drives ZX Spectrum Next machines - real ones over Wi-Fi \
(NextSync) and emulated ones (MAME, CSpect) booting a NextZXOS SD-card \
image (.img/.hdf) on this PC.

Workflows:
- Real Next: nextsync_listener_start, then on the Next run `.sync5 -L` \
(or ZX Next Remote's Listener) pointed at this PC's address; next_sessions \
lists the seated machines, next_ls/next_get/next_put move files, \
next_deploy pushes a build, verifies it (CRC-32 computed ON the Next) and \
makes the Next leave listen mode - with the autoexec loop from \
extra/autoexec.bas on the card the Next then runs it.
- Emulator: image_load (or image_download_nextzxos), image_put to place \
files on the card, emulator_start("MAME", file=...) / \
emulator_start("CSpect", file=...) to boot the image and start a .nex.
- app_status says what is loaded, running, connected and installed; \
app_log_tail shows the consoles.
Paths on the Next and inside an image use forward slashes: /games/a.nex, \
c:/dot, m:/. Tool failures come back as isError results with the reason.
"""


def parse_hdfmonkey_ls(stdout):
    """hdfmonkey's ``ls`` lines (``<type>\\t<name>``, type ``[DIR]`` or
    ``[N bytes]``) -> [{"name", "is_dir", "size"}]. The stdlib twin of
    SdCardExplorerPane.image_parse_ls, kept here so this module stays
    Qt-free; tests/test_mcp_server.py pins the two against each other."""
    out = []
    for line in str(stdout or "").splitlines():
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        ftype, name = parts[0].strip(), parts[1]
        if ftype.upper().startswith("[DIR"):
            out.append({"name": name, "is_dir": True, "size": 0})
        else:
            try:
                size = int(ftype.strip("[]").split()[0])
            except (ValueError, IndexError):
                size = 0
            out.append({"name": name, "is_dir": False, "size": size})
    return out


def _join_remote(folder, name):
    folder = str(folder or "").replace("\\", "/")
    if not folder.endswith("/"):
        folder += "/"
    return folder + name


def _result_error(res, what):
    """The worker's reply dict as a McpToolError."""
    err = (res or {}).get("error") or f"{what} failed on the Next"
    return McpToolError(str(err))


def _text(b):
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return "" if b is None else str(b)


def build_mcp_tools(host):
    """Every tool of the server, bound to ``host`` (see the protocol above)."""
    tools = []

    def tool(name, description, properties=None, required=(), title=None):
        def deco(fn):
            tools.append(McpTool(name, description, fn, properties, required,
                                 title=title))
            return fn
        return deco

    session_param = param(
        "integer", "the seated Next to target (its sid from next_sessions); "
        "default = the active one", minimum=1)

    def _session(args):
        s = args.get("session")
        return int(s) if s is not None else None

    def _ok_or_raise(pair, what):
        ok, message = pair[0], pair[1]
        if not ok:
            raise McpToolError(message or f"{what} failed")
        return message

    # ---- app ------------------------------------------------------------
    @tool("app_status",
          "What ZX Next Unite is doing right now: version, data folder, the "
          "loaded disk image and its usage, the tools found (hdfmonkey, CSpect, "
          "MAME), the emulators started from the app, the RS232 ESP emulation, "
          "the NextSync listener / classic server / HTTP bridge and the Nexts "
          "seated on the listener.")
    def app_status(args):
        return host.status()

    @tool("app_log_tail",
          "The last lines of one of the app's consoles: 'sdcard' (the SD Card "
          "Utility log: hdfmonkey, emulator launches, downloads), 'nextsync' "
          "(the NextSync console: listener, transfers, protocol diagnostics) "
          "or 'file' (the rotating zx-next-unite.log).",
          {"console": param("string", "which console", enum=["sdcard", "nextsync", "file"],
                            default="nextsync"),
           "lines": param("integer", "how many lines (newest last)", default=50,
                          minimum=1, maximum=2000)})
    def app_log_tail(args):
        console = args.get("console") or "nextsync"
        n = int(args.get("lines") or 50)
        return {"console": console, "lines": host.log_tail(console, n)}

    # ---- disk image -----------------------------------------------------
    @tool("image_status",
          "The disk image loaded on the SD Card tab: path, whether it exists, "
          "FAT usage (used %, free bytes), whether an emulator started from "
          "the app holds it, and the write verdict.")
    def image_status(args):
        return host.image_status()

    @tool("image_load",
          "Load a NextZXOS SD-card image (.img or .hdf) on the SD Card tab, as "
          "'Select NextZXOS disk Image' does: it becomes the image every "
          "image_* tool and emulator_start work on. Waits for the root "
          "listing.",
          {"path": param("string", "local path of the .img/.hdf file")},
          required=("path",))
    def image_load(args):
        path = str(args.get("path") or "").strip()
        if not path:
            raise McpToolError("path is empty")
        if not os.path.isfile(path):
            raise McpToolError(f"no such file: {path}")
        msg = _ok_or_raise(host.image_load(path, LOAD_TIMEOUT), "image_load")
        return {"ok": True, "path": host.image_path(), "message": msg}

    @tool("image_unload",
          "Unload the disk image from the SD Card tab (clears the image box).")
    def image_unload(args):
        msg = _ok_or_raise(host.image_load("", LOAD_TIMEOUT), "image_unload")
        return {"ok": True, "message": msg}

    @tool("image_recent",
          "The disk images remembered in the SD Card tab's image box (most "
          "recent first), with whether each file still exists and the cached "
          "write verdict.")
    def image_recent(args):
        return {"images": host.image_recent()}

    @tool("image_download_nextzxos",
          "Download a stock NextZXOS distribution SD-card image from zxnext.uk "
          "(the 'Download NextZXOS Image' button), extract the .img next to "
          "the zip and, by default, load it. Hundreds of MB: this can take "
          "minutes.",
          {"size": param("string", "which distribution image", enum=["2gb", "4gb", "8gb"],
                         default="2gb"),
           "dest_dir": param("string", "folder to save the zip and the extracted image "
                             "into (default: <data folder>/downloads/nextzxos)"),
           "load": param("boolean", "load the extracted image when done", default=True)})
    def image_download_nextzxos(args):
        size = args.get("size") or "2gb"
        dest = str(args.get("dest_dir") or "").strip()
        load = args.get("load", True)
        return host.image_download(size, dest, bool(load))

    def _hdf(verb, argv):
        rc, out, err = host.hdfmonkey(verb, list(argv))
        return rc, _text(out), _text(err)

    def _hdf_ok(verb, argv, what):
        rc, out, err = _hdf(verb, argv)
        if rc != 0:
            msg = (err or out).strip() or f"hdfmonkey {verb} failed (rc {rc})"
            raise McpToolError(f"{what}: {msg}")
        return out

    def _image_ls(path):
        return parse_hdfmonkey_ls(_hdf_ok("ls", [path], f"image_ls {path}"))

    def _image_walk(path):
        """(files, dirs) under `path` inside the image, files first, dirs
        deepest-first - the order a recursive delete needs."""
        files, dirs = [], []
        for e in _image_ls(path):
            full = _join_remote(path, e["name"])
            if e["is_dir"]:
                f2, d2 = _image_walk(full)
                files += f2
                dirs += d2
                dirs.append(full)
            else:
                files.append(full)
        return files, dirs

    def _image_entry(path):
        """The parent listing's entry for `path`, or None."""
        p = str(path).replace("\\", "/").rstrip("/")
        if not p or "/" not in p:
            return None
        parent, name = p.rsplit("/", 1)
        parent = parent or "/"
        for e in _image_ls(parent):
            if e["name"] == name:
                return e
        return None

    @tool("image_ls",
          "List a folder inside the loaded disk image (hdfmonkey ls).",
          {"path": param("string", "folder inside the image", default="/")})
    def image_ls(args):
        path = str(args.get("path") or "/")
        return {"path": path, "entries": _image_ls(path)}

    @tool("image_get",
          "Copy one file OUT of the loaded disk image to this PC.",
          {"image_path": param("string", "file inside the image, e.g. /games/a.nex"),
           "local_path": param("string", "destination file, or an existing folder "
                               "(the file keeps its name)")},
          required=("image_path", "local_path"))
    def image_get(args):
        src = str(args.get("image_path") or "").strip()
        dst = str(args.get("local_path") or "").strip()
        if not src or not dst:
            raise McpToolError("image_path and local_path are required")
        if os.path.isdir(dst):
            dst = os.path.join(dst, src.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])
        _hdf_ok("get", [src, dst], f"image_get {src}")
        return {"ok": True, "image_path": src, "local_path": dst,
                "bytes": os.path.getsize(dst) if os.path.isfile(dst) else None}

    @tool("image_put",
          "Copy a local file or a whole folder INTO the loaded disk image "
          "(hdfmonkey put; folders are created as needed). Refused while an "
          "emulator started from the app holds the image.",
          {"local_path": param("string", "local file or folder"),
           "image_dir": param("string", "destination FOLDER inside the image, "
                              "e.g. /games (created if missing)", default="/")},
          required=("local_path",))
    def image_put(args):
        local = str(args.get("local_path") or "").strip()
        image_dir = str(args.get("image_dir") or "/").replace("\\", "/")
        if not local or not os.path.exists(local):
            raise McpToolError(f"no such file or folder: {local}")
        sent, made = [], []

        def ensure_dir(d):
            if d in ("", "/"):
                return
            rc, _o, _e = _hdf("mkdir", [d])
            if rc == 0:
                made.append(d)
            else:
                # Exists already? hdfmonkey says so with a failure; a
                # listing that succeeds is the proof.
                rc2, _o2, _e2 = _hdf("ls", [d])
                if rc2 != 0:
                    raise McpToolError(f"could not create {d} in the image: "
                                       f"{(_e or _o).strip()}")

        if os.path.isfile(local):
            ensure_dir(image_dir)
            target = _join_remote(image_dir, os.path.basename(local))
            _hdf_ok("put", [local.replace("\\", "/"), target], f"image_put {local}")
            sent.append(target)
            host.image_refresh(image_dir)
            return {"ok": True, "files": 1, "folders_created": made, "sent": sent}
        root = _join_remote(image_dir, os.path.basename(os.path.normpath(local)))
        ensure_dir(image_dir)
        ensure_dir(root)
        for dirpath, dirnames, filenames in os.walk(local):
            rel = os.path.relpath(dirpath, local).replace("\\", "/")
            here = root if rel == "." else _join_remote(root, rel)
            if rel != ".":
                ensure_dir(here)
            dirnames.sort()
            for fn in sorted(filenames):
                lp = os.path.join(dirpath, fn)
                target = _join_remote(here, fn)
                _hdf_ok("put", [lp.replace("\\", "/"), target], f"image_put {lp}")
                sent.append(target)
        host.image_refresh(image_dir)
        return {"ok": True, "files": len(sent), "folders_created": made,
                "root": root, "sent": sent}

    @tool("image_mkdir", "Create a folder inside the loaded disk image.",
          {"path": param("string", "folder to create, e.g. /games/new")},
          required=("path",))
    def image_mkdir(args):
        path = str(args.get("path") or "").strip()
        if not path:
            raise McpToolError("path is empty")
        _hdf_ok("mkdir", [path], f"image_mkdir {path}")
        host.image_refresh(path.rsplit("/", 1)[0] or "/")
        return {"ok": True, "path": path}

    @tool("image_rm",
          "Delete a file - or, with recursive=true, a whole folder - inside "
          "the loaded disk image. No confirmation, no Recycle Bin.",
          {"path": param("string", "file or folder inside the image"),
           "recursive": param("boolean", "delete a folder and everything in it",
                              default=False)},
          required=("path",))
    def image_rm(args):
        path = str(args.get("path") or "").strip().replace("\\", "/")
        if not path or path == "/":
            raise McpToolError("refusing to delete the image root")
        entry = _image_entry(path)
        if entry is None:
            raise McpToolError(f"not found in the image: {path}")
        deleted = []
        if entry["is_dir"]:
            files, dirs = _image_walk(path)
            if (files or dirs) and not args.get("recursive"):
                raise McpToolError(f"{path} is a folder with {len(files)} file(s) "
                                   f"and {len(dirs)} folder(s): pass recursive=true")
            for f in files:
                _hdf_ok("rm", [f], f"image_rm {f}")
                deleted.append(f)
            for d in dirs:
                _hdf_ok("rm", [d], f"image_rm {d}")
                deleted.append(d)
        _hdf_ok("rm", [path], f"image_rm {path}")
        deleted.append(path)
        host.image_refresh(path.rsplit("/", 1)[0] or "/")
        return {"ok": True, "deleted": deleted}

    @tool("image_rename",
          "Rename or move a file or folder inside the loaded disk image. "
          "hdfmonkey has no rename: the entry is copied out, written back "
          "under the new name, checked, and only then is the original deleted "
          "(a folder recursively).",
          {"old_path": param("string", "current path inside the image"),
           "new_path": param("string", "new path inside the image")},
          required=("old_path", "new_path"))
    def image_rename(args):
        import shutil
        import tempfile
        old = str(args.get("old_path") or "").strip().replace("\\", "/").rstrip("/")
        new = str(args.get("new_path") or "").strip().replace("\\", "/").rstrip("/")
        if not old or not new:
            raise McpToolError("old_path and new_path are required")
        if old == "/" or new == "/":
            raise McpToolError("refusing to rename the image root")
        entry = _image_entry(old)
        if entry is None:
            raise McpToolError(f"not found in the image: {old}")
        if _image_entry(new) is not None:
            raise McpToolError(f"{new} already exists in the image")
        tmp = tempfile.mkdtemp(prefix="zxnu_mcp_rename_")
        try:
            # 1. copy OUT (the app's _run_rename_task order: hdfmonkey has
            #    no mv, and the original goes only once the copy is listed)
            local = os.path.join(tmp, old.rsplit("/", 1)[-1])
            if entry["is_dir"]:
                files, _dirs = _image_walk(old)
                for f in files:
                    rel = f[len(old) + 1:]
                    dst = os.path.join(local, *rel.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    _hdf_ok("get", [f, dst], f"image_rename get {f}")
                os.makedirs(local, exist_ok=True)
            else:
                _hdf_ok("get", [old, local], f"image_rename get {old}")
            # 2. write back under the new name
            if entry["is_dir"]:
                _hdf_ok("mkdir", [new], f"image_rename mkdir {new}")
                for dirpath, dirnames, filenames in os.walk(local):
                    rel = os.path.relpath(dirpath, local).replace("\\", "/")
                    here = new if rel == "." else _join_remote(new, rel)
                    if rel != ".":
                        _hdf_ok("mkdir", [here], f"image_rename mkdir {here}")
                    dirnames.sort()
                    for fn in sorted(filenames):
                        _hdf_ok("put", [os.path.join(dirpath, fn).replace("\\", "/"),
                                        _join_remote(here, fn)], f"image_rename put {fn}")
            else:
                _hdf_ok("put", [local.replace("\\", "/"), new], f"image_rename put {new}")
            if _image_entry(new) is None:
                raise McpToolError(f"{new} was not written - the original is kept")
            # 3. remove the original
            if entry["is_dir"]:
                files, dirs = _image_walk(old)
                for f in files:
                    _hdf_ok("rm", [f], f"image_rename rm {f}")
                for d in dirs:
                    _hdf_ok("rm", [d], f"image_rename rm {d}")
            _hdf_ok("rm", [old], f"image_rename rm {old}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        host.image_refresh(old.rsplit("/", 1)[0] or "/")
        if new.rsplit("/", 1)[0] != old.rsplit("/", 1)[0]:
            host.image_refresh(new.rsplit("/", 1)[0] or "/")
        return {"ok": True, "old_path": old, "new_path": new}

    # ---- emulators --------------------------------------------------------
    @tool("emulator_list",
          "The emulators ZX Next Unite found (MAME, CSpect) and whether each "
          "can start right now; a non-empty 'blocked' says why not (no image "
          "loaded, the image held by a running emulator...).")
    def emulator_list(args):
        return {"emulators": host.emulators()}

    @tool("emulator_start",
          "Start MAME or CSpect on the loaded disk image, as the Launch "
          "buttons do. With `file` (a local .nex/.tap/.sna/.z80 ...) the "
          "emulator starts that program: CSpect loads it directly; MAME needs "
          "it ON the card, so it is put into `image_dir` first (default: the "
          "app stages it under /zxnu-start) and NextZXOS is typed the "
          ".nexload command. CSpect 4.2.0+ can start its OWN MCP server "
          "(`-mcp=<port>`): the Settings toggle decides, `cspect_mcp` / "
          "`cspect_mcp_port` override it for this launch, and the answer "
          "names the port so you can connect to CSpect's server as well. "
          "Returns the process id (see emulator_processes / emulator_stop).",
          {"emulator": param("string", "which emulator", enum=["MAME", "CSpect"]),
           "file": param("string", "local path of a program to start (optional)"),
           "image_dir": param("string", "MAME only: folder inside the image to copy "
                              "`file` into before starting (optional)"),
           "cspect_mcp": param("boolean", "CSpect only: start CSpect's own MCP server "
                               "(default: the Settings toggle)"),
           "cspect_mcp_port": param("integer", "CSpect only: the port for CSpect's MCP "
                                    "server (default: the Settings value, 2051)",
                                    minimum=1, maximum=65535)},
          required=("emulator",))
    def emulator_start(args):
        emu = args.get("emulator")
        file = str(args.get("file") or "").strip() or None
        image_dir = str(args.get("image_dir") or "").strip().replace("\\", "/") or None
        card_path = None
        if file and not os.path.isfile(file):
            raise McpToolError(f"no such file: {file}")
        if emu == "MAME" and file and image_dir:
            res = image_put({"local_path": file, "image_dir": image_dir})
            card_path = res["sent"][0]
        cmcp = args.get("cspect_mcp")
        cport = args.get("cspect_mcp_port")
        ok, message, pid, extra = host.launch(
            emu, file, card_path, cmcp, int(cport) if cport is not None else None)
        if not ok:
            raise McpToolError(message or f"{emu} did not start")
        out = {"ok": True, "emulator": emu, "pid": pid, "message": message}
        if card_path:
            out["card_path"] = card_path
        if extra:
            out.update(extra)
        return out

    @tool("emulator_processes",
          "The emulators started from ZX Next Unite that are still running "
          "(pid, emulator, the image they hold).")
    def emulator_processes(args):
        return {"processes": host.processes()}

    @tool("emulator_stop",
          "Stop an emulator started from ZX Next Unite (its whole process "
          "tree), by pid from emulator_processes.",
          {"pid": param("integer", "process id")}, required=("pid",))
    def emulator_stop(args):
        pid = int(args.get("pid"))
        msg = _ok_or_raise(host.stop_process(pid), "emulator_stop")
        return {"ok": True, "pid": pid, "message": msg}

    # ---- RS232 ESP emulation ----------------------------------------------
    @tool("esp_emulation_status",
          "The RS232 ESP emulation for MAME (the Wi-Fi module an emulated "
          "Next talks to): running, port, sessions, the address it reports, "
          "how many MAMEs share it, and the Settings toggle.")
    def esp_emulation_status(args):
        return host.esp_status()

    @tool("esp_emulation_start",
          "Start the RS232 ESP emulation (and turn its Settings toggle on so "
          "MAME launches wire to it), optionally on another port. A worker "
          "already held by a running MAME keeps its port; the answer says "
          "which port is live.",
          {"port": param("integer", "TCP port for MAME's -bitb socket (default: the "
                         "Settings value, 2222)", minimum=1, maximum=65535),
           "verbose": param("boolean", "trace every AT command to the SD Card console")})
    def esp_emulation_start(args):
        port = args.get("port")
        ok, message, live_port = host.esp_start(int(port) if port is not None else None,
                                                args.get("verbose"))
        if not ok:
            raise McpToolError(message)
        return {"ok": True, "port": live_port, "message": message}

    @tool("esp_emulation_stop",
          "Stop the RS232 ESP emulation. Refused while a MAME started from "
          "the app still uses it, unless force=true (that MAME loses its "
          "Wi-Fi).",
          {"force": param("boolean", "stop even while a MAME uses it", default=False)})
    def esp_emulation_stop(args):
        msg = _ok_or_raise(host.esp_stop(bool(args.get("force"))), "esp_emulation_stop")
        return {"ok": True, "message": msg}

    # ---- NextSync servers ---------------------------------------------------
    @tool("nextsync_status",
          "The NextSync side: the Remote Explorer listener (running, port, "
          "sync root, the Nexts seated), the classic Sync3/Sync4 server, and "
          "the HTTP bridge.")
    def nextsync_status(args):
        return host.nextsync_status()

    @tool("nextsync_listener_start",
          "Start the Remote Explorer's NextSync listener (TCP 2048): a Next "
          "running `.sync5 -L` or ZX Next Remote's Listener then dials this "
          "PC and is seated for the next_* tools. Optionally sets the sync "
          "root first (the local folder the Remote Explorer's left pane "
          "shows). The classic server must not be running.",
          {"sync_root": param("string", "local folder to use as the sync root")})
    def nextsync_listener_start(args):
        root = str(args.get("sync_root") or "").strip()
        if root:
            _ok_or_raise(host.set_sync_root(root), "nextsync_set_sync_root")
        msg = _ok_or_raise(host.listener_start(root or None), "nextsync_listener_start")
        return {"ok": True, "message": msg, "status": host.nextsync_status()}

    @tool("nextsync_listener_stop",
          "Stop the Remote Explorer's NextSync listener: every seated Next "
          "is told to leave listen mode (the .sync5 dot returns to BASIC).")
    def nextsync_listener_stop(args):
        msg = _ok_or_raise(host.listener_stop(), "nextsync_listener_stop")
        return {"ok": True, "message": msg}

    @tool("nextsync_set_sync_root",
          "Set the NextSync sync root: the local folder the classic server "
          "sends and the Remote Explorer's left pane shows. Persisted.",
          {"path": param("string", "an existing local folder")}, required=("path",))
    def nextsync_set_sync_root(args):
        path = str(args.get("path") or "").strip()
        if not path or not os.path.isdir(path):
            raise McpToolError(f"not a folder: {path}")
        msg = _ok_or_raise(host.set_sync_root(path), "nextsync_set_sync_root")
        return {"ok": True, "sync_root": path, "message": msg}

    @tool("nextsync_classic_start",
          "Start the classic NextSync server (Jari Komppa's protocol, TCP "
          "2048): the Next runs `.sync5` and pulls every changed file under "
          "the sync root. 'once' serves one sync and stops; 'always' keeps "
          "serving until nextsync_classic_stop. The listener must not be "
          "running. Returns at once; the transfer runs in the background.",
          {"mode": param("string", "sync once, or keep serving", enum=["once", "always"],
                         default="once")})
    def nextsync_classic_start(args):
        mode = args.get("mode") or "once"
        msg = _ok_or_raise(host.classic_start(mode), "nextsync_classic_start")
        return {"ok": True, "mode": mode, "message": msg}

    @tool("nextsync_classic_stop", "Stop the classic NextSync server.")
    def nextsync_classic_stop(args):
        msg = _ok_or_raise(host.classic_stop(), "nextsync_classic_stop")
        return {"ok": True, "message": msg}

    @tool("nextsync_http_bridge_start",
          "Start the NextSync HTTP bridge (Flask; the web routes the Next's "
          ".http command, curl and the PowerShell module use), optionally on "
          "another port. Not persisted: the Settings toggle is left alone.",
          {"port": param("integer", "TCP port (default: the Settings value, 80)",
                         minimum=1, maximum=65535)})
    def nextsync_http_bridge_start(args):
        port = args.get("port")
        msg = _ok_or_raise(host.bridge_start(int(port) if port is not None else None),
                           "nextsync_http_bridge_start")
        return {"ok": True, "message": msg}

    @tool("nextsync_http_bridge_stop", "Stop the NextSync HTTP bridge.")
    def nextsync_http_bridge_stop(args):
        msg = _ok_or_raise(host.bridge_stop(), "nextsync_http_bridge_stop")
        return {"ok": True, "message": msg}

    # ---- the Nexts seated on the listener ------------------------------------
    def _roster():
        r = host.next_sessions()
        if r is None:
            return {"active": None, "max": 0, "sessions": []}
        active, rows, maxp = r
        return {"active": active, "max": maxp,
                "sessions": [{"sid": s, "addr": a, "name": n, "active": s == active}
                             for s, a, n in rows]}

    @tool("next_sessions",
          "The Nexts seated on the listener: sid, address, friendly name and "
          "which one is active (the one next_* tools target by default).")
    def next_sessions(args):
        return _roster()

    @tool("next_select",
          "Make a seated Next the active one (the Remote Explorer switches to "
          "it); the others idle on.",
          {"session": param("integer", "sid from next_sessions", minimum=1)},
          required=("session",))
    def next_select(args):
        sid = int(args.get("session"))
        msg = _ok_or_raise(host.next_select(sid), "next_select")
        return {"ok": True, "active": sid, "message": msg}

    def _cmd(op, a1="", a2="", session=None, long=False):
        res = host.next_cmd(op, a1, a2, session,
                            LONG_TIMEOUT if long else DEFAULT_TIMEOUT)
        if not isinstance(res, dict):
            raise McpToolError(f"{op}: no answer from the Next")
        return res

    _ident_cache = {}

    def _ident(session):
        if session is not None and session in _ident_cache:
            return _ident_cache[session]
        res = _cmd("version", session=session)
        if session is not None:
            _ident_cache[session] = res
        return res

    @tool("next_version",
          "Which program answers on a seated Next and its build: type 'sync' "
          "(the .sync5 dot), 'httpbridge' / 'n2n' / 'zxnt' (ZX Next Remote "
          "flavors), 'qlnextremote' / 'qlnt' (the QL jobs), plus its version "
          "number.",
          {"session": session_param})
    def next_version(args):
        res = _ident(_session(args))
        if not res.get("ok"):
            raise _result_error(res, "version")
        return {"type": res.get("type", ""), "number": res.get("number", "")}

    @tool("next_drives", "The drive letters mounted on a seated Next and the current one.",
          {"session": session_param})
    def next_drives(args):
        res = _cmd("drives", session=_session(args))
        if not res.get("ok"):
            raise _result_error(res, "drives")
        return {"current": res.get("current", ""), "drives": list(res.get("letters") or [])}

    @tool("next_free", "Free space on a drive of a seated Next (bytes).",
          {"drive": param("string", "drive letter, e.g. C (default: the current one)"),
           "session": session_param})
    def next_free(args):
        drive = str(args.get("drive") or "").strip()
        res = _cmd("free", drive, session=_session(args))
        if not res.get("ok"):
            raise _result_error(res, f"free {drive}")
        return {"drive": drive or res.get("drive", ""), "free_bytes": int(res.get("free") or 0)}

    @tool("next_ls", "List a folder on a seated Next.",
          {"path": param("string", "folder on the Next, e.g. /games or c:/dot", default="/"),
           "session": session_param})
    def next_ls(args):
        path = str(args.get("path") or "/")
        res = _cmd("ls", path, session=_session(args))
        if not res.get("ok"):
            raise _result_error(res, f"ls {path}")
        entries = [{"name": n, "is_dir": bool(d), "size": int(s or 0)}
                   for d, s, n in (res.get("entries") or [])]
        return {"path": path, "entries": entries}

    @tool("next_get",
          "Download a file - or a whole folder, recreated intact - from a "
          "seated Next into a local folder.",
          {"remote_path": param("string", "file or folder on the Next"),
           "local_dir": param("string", "local folder to receive it (created if missing)"),
           "session": session_param},
          required=("remote_path", "local_dir"))
    def next_get(args):
        remote = str(args.get("remote_path") or "").strip()
        local_dir = str(args.get("local_dir") or "").strip()
        if not remote or not local_dir:
            raise McpToolError("remote_path and local_dir are required")
        os.makedirs(local_dir, exist_ok=True)
        res = _cmd("get", remote, local_dir, session=_session(args), long=True)
        if not res.get("ok"):
            raise _result_error(res, f"get {remote}")
        return {"ok": True, "remote_path": remote, "local_dir": local_dir,
                "files": int(res.get("count") or 0), "last": res.get("last") or ""}

    def _crc_local(path):
        import zlib
        crc = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                crc = zlib.crc32(chunk, crc)
        return "%08X" % (crc & 0xFFFFFFFF)

    def _verify(local, remote, session):
        """Verify the file that landed as `remote` against `local`: the CRC-32
        the Next computes of it ('K') when its listener has the op (dot
        5.9.2 / ZXNR 1.0.8 / QLNR 0.1.0), else a read-back byte compare.
        Returns {"verified", "method", ...}."""
        method = "crc"
        if peer_lacks_crc(_ident(session)):
            method = "readback"
        else:
            res = _cmd("crc", remote, session=session, long=True)
            if res.get("ok"):
                want = _crc_local(local)
                got = str(res.get("crc32") or "").upper()
                return {"verified": got == want, "method": "crc",
                        "crc32_local": want, "crc32_next": got}
            method = "readback"
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="zxnu_mcp_verify_")
        try:
            res = _cmd("get", remote, tmp, session=session, long=True)
            if not res.get("ok") or not res.get("last"):
                return {"verified": False, "method": method,
                        "error": (res or {}).get("error") or "read-back failed"}
            with open(res["last"], "rb") as a, open(local, "rb") as b:
                same = a.read() == b.read()
            return {"verified": same, "method": "readback"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _put_one(local, remote, verify, session):
        res = _cmd("put", remote, local, session=session, long=True)
        if not res.get("ok"):
            raise _result_error(res, f"put {local} -> {remote}")
        landed = remote
        if remote.endswith("/") or remote.endswith("\\"):
            landed = _join_remote(remote, os.path.basename(local))
        out = {"local_path": local, "remote_path": landed,
               "bytes": os.path.getsize(local)}
        if verify:
            out.update(_verify(local, landed, session))
            if not out.get("verified"):
                raise McpToolError(
                    f"{landed} landed but did not verify ({out.get('method')}): "
                    + str(out.get("error") or "the bytes on the Next differ"))
        return out

    def _mkdir_tolerant(path, session):
        res = _cmd("mkdir", path, session=session)
        if res.get("ok"):
            return True
        # ZX Next Remote 1.4.1+ says ok for an existing folder; an older
        # listener and the dot answer F - a listing that succeeds is the proof.
        ls = _cmd("ls", path, session=session)
        if ls.get("ok"):
            return False
        raise _result_error(res, f"mkdir {path}")

    @tool("next_put",
          "Upload a local file - or a whole folder, recursively - to a seated "
          "Next, verifying each file by the CRC-32 the Next computes of it "
          "(or a read-back when the listener predates the crc op). A file "
          "whose bytes differ is reported as an error.",
          {"local_path": param("string", "local file or folder"),
           "remote_path": param("string", "destination on the Next: the full file "
                                "path, or a folder ending in / (the file keeps its "
                                "name); for a folder upload, the folder to create it "
                                "under"),
           "verify": param("boolean", "verify every file after it lands", default=True),
           "session": session_param},
          required=("local_path", "remote_path"))
    def next_put(args):
        local = str(args.get("local_path") or "").strip()
        remote = str(args.get("remote_path") or "").strip().replace("\\", "/")
        verify = args.get("verify", True)
        session = _session(args)
        if not local or not os.path.exists(local):
            raise McpToolError(f"no such file or folder: {local}")
        if not remote:
            raise McpToolError("remote_path is required")
        if os.path.isfile(local):
            return dict(_put_one(local, remote, verify, session), ok=True, files=1)
        root = _join_remote(remote, os.path.basename(os.path.normpath(local)))
        _mkdir_tolerant(root.rstrip("/"), session)
        sent = []
        for dirpath, dirnames, filenames in os.walk(local):
            rel = os.path.relpath(dirpath, local).replace("\\", "/")
            here = root if rel == "." else _join_remote(root, rel)
            if rel != ".":
                _mkdir_tolerant(here.rstrip("/"), session)
            dirnames.sort()
            for fn in sorted(filenames):
                sent.append(_put_one(os.path.join(dirpath, fn),
                                     _join_remote(here, fn), verify, session))
        return {"ok": True, "files": len(sent), "root": root.rstrip("/"), "sent": sent}

    def _simple(op, path, session, what, long=False):
        res = _cmd(op, path, session=session, long=long)
        if not res.get("ok"):
            raise _result_error(res, f"{what} {path}")
        return res

    @tool("next_mkdir", "Create a folder on a seated Next.",
          {"path": param("string", "folder to create"), "session": session_param},
          required=("path",))
    def next_mkdir(args):
        path = str(args.get("path") or "").strip()
        _simple("mkdir", path, _session(args), "mkdir")
        return {"ok": True, "path": path}

    @tool("next_rmdir", "Remove an EMPTY folder on a seated Next.",
          {"path": param("string", "folder"), "session": session_param}, required=("path",))
    def next_rmdir(args):
        path = str(args.get("path") or "").strip()
        _simple("rmdir", path, _session(args), "rmdir")
        return {"ok": True, "path": path}

    @tool("next_rm", "Delete a file on a seated Next.",
          {"path": param("string", "file"), "session": session_param}, required=("path",))
    def next_rm(args):
        path = str(args.get("path") or "").strip()
        _simple("rm", path, _session(args), "rm")
        return {"ok": True, "path": path}

    @tool("next_rmtree",
          "Delete a folder and everything in it on a seated Next. No confirmation.",
          {"path": param("string", "folder"), "session": session_param}, required=("path",))
    def next_rmtree(args):
        path = str(args.get("path") or "").strip()
        _simple("rmtree", path, _session(args), "rmtree", long=True)
        return {"ok": True, "path": path}

    @tool("next_rename", "Rename or move a file or folder on a seated Next.",
          {"old_path": param("string", "current path"), "new_path": param("string", "new path"),
           "session": session_param},
          required=("old_path", "new_path"))
    def next_rename(args):
        old = str(args.get("old_path") or "").strip()
        new = str(args.get("new_path") or "").strip()
        res = _cmd("ren", old, new, session=_session(args))
        if not res.get("ok"):
            raise _result_error(res, f"rename {old}")
        return {"ok": True, "old_path": old, "new_path": new}

    @tool("next_copy",
          "Copy a file ON a seated Next (also across its drives, e.g. c:/a "
          "-> m:/a); ZX Next Remote copies files only, the dot and the QL "
          "jobs also folders.",
          {"src": param("string", "source on the Next"), "dst": param("string", "destination on the Next"),
           "session": session_param},
          required=("src", "dst"))
    def next_copy(args):
        src = str(args.get("src") or "").strip()
        dst = str(args.get("dst") or "").strip()
        res = _cmd("rcpy", src, dst, session=_session(args), long=True)
        if not res.get("ok"):
            raise _result_error(res, f"copy {src}")
        return {"ok": True, "src": src, "dst": dst, "files": int(res.get("files") or 0)}

    @tool("next_size", "Total size of a file or folder tree on a seated Next "
          "(the .sync5 dot and the QL jobs answer; ZX Next Remote declines).",
          {"path": param("string", "file or folder"), "session": session_param},
          required=("path",))
    def next_size(args):
        path = str(args.get("path") or "").strip()
        res = _simple("rfsize", path, _session(args), "size", long=True)
        return {"path": path, "files": int(res.get("files") or 0),
                "dirs": int(res.get("dirs") or 0), "bytes": int(res.get("bytes") or 0)}

    @tool("next_crc",
          "CRC-32 of one file computed ON a seated Next (8 hex digits; needs "
          ".sync5 5.9.2 / ZX Next Remote 1.0.8 / QLNextRemote 0.1.0).",
          {"path": param("string", "file on the Next"), "session": session_param},
          required=("path",))
    def next_crc(args):
        path = str(args.get("path") or "").strip()
        res = _simple("crc", path, _session(args), "crc", long=True)
        return {"path": path, "crc32": str(res.get("crc32") or "").upper()}

    @tool("next_exit",
          "Make a seated Next leave listen mode and exit its program (the "
          ".sync5 dot returns to BASIC; ZX Next Remote saves its settings and "
          "soft-resets into NextZXOS). With the autoexec loop on the card "
          "this is what RUNS a pushed build. The seat is freed.",
          {"session": session_param})
    def next_exit(args):
        res = _cmd("forceexit", session=_session(args))
        if not res.get("ok"):
            raise _result_error(res, "exit")
        return {"ok": True}

    @tool("next_deploy",
          "Push a build to a seated Next and run it - the extra/Send-ToNext.ps1 "
          "workflow in one call: upload `local_path` to `remote_path`, verify "
          "it (CRC-32 computed on the Next, else a read-back), then make the "
          "Next leave listen mode; with extra/autoexec.bas installed in "
          "/nextzxos the Next soft-resets, finds the file at /home/incoming.nex "
          "and .nexloads it. A build that does not verify is never run.",
          {"local_path": param("string", "the local file to push (a .nex)"),
           "remote_path": param("string", "where it lands on the Next",
                                default=NEXT_DEFAULT_DEPLOY_PATH),
           "verify": param("boolean", "verify after the upload", default=True),
           "run": param("boolean", "leave listen mode so the autoexec loop runs it",
                        default=True),
           "session": session_param},
          required=("local_path",))
    def next_deploy(args):
        local = str(args.get("local_path") or "").strip()
        remote = str(args.get("remote_path") or NEXT_DEFAULT_DEPLOY_PATH).strip()
        if not local or not os.path.isfile(local):
            raise McpToolError(f"no such file: {local}")
        session = _session(args)
        out = _put_one(local, remote.replace("\\", "/"), args.get("verify", True), session)
        out["ok"] = True
        if args.get("run", True):
            res = _cmd("forceexit", session=session)
            out["exited"] = bool(res.get("ok"))
            if not res.get("ok"):
                raise McpToolError(f"{out['remote_path']} landed and verified, but the "
                                   "Next did not acknowledge the exit: "
                                   + str(res.get("error") or ""))
            out["note"] = ("the Next left listen mode; with extra/autoexec.bas on the "
                           "card it now runs the pushed file")
        return out

    return tools


def main():
    """Console-script entry (``zx-next-unite-mcp``)."""
    return stdio_main()


if __name__ == "__main__":
    sys.exit(main())
