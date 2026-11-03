"""The MCP server (zxnu_mcp, 9.7.57), headless:

  * the JSON-RPC dispatcher (initialize / ping / tools/list / tools/call,
    notifications, batches, every error code);
  * the loopback Streamable-HTTP transport on an OS-chosen port (202 for a
    notification, 405 on GET /mcp, the bearer token, the Origin guard, the
    port-in-use verdict);
  * the stdio shim relaying to that transport, and refusing cleanly when the
    app is not running;
  * every tool over a FAKE host implementing the protocol zxnu_mcp.py lists
    (an in-memory disk image for hdfmonkey, an in-memory Next for next_cmd),
    incl. next_put's CRC / read-back verification and next_deploy's order;
  * the wiring tripwires: the settings keys, the Settings rows, the restore,
    the six catalogs, pyproject, the launchers' -mcp switch.

Run with: python tests/test_mcp_server.py
"""
import ast
import io
import json
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, REPO)

from zxnu_mcp import (  # noqa: E402
    INVALID_PARAMS, INVALID_REQUEST, MCP_DEFAULT_PORT, MCP_PATH, MCP_TOKEN_HEADER,
    METHOD_NOT_FOUND, NEXT_DEFAULT_DEPLOY_PATH, PARSE_ERROR, PROTOCOL_VERSIONS,
    SERVER_REFUSED, SERVER_UNREACHABLE, McpHttpServer, McpServer, McpTool,
    McpToolError, build_mcp_tools, param, parse_hdfmonkey_ls, stdio_main,
    stdio_target, validate_arguments)

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        ok = False


def rpc(method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        msg["id"] = rid
    if params is not None:
        msg["params"] = params
    return msg


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# =====================================================================
#  A fake host: the protocol zxnu_mcp.py documents, in memory
# =====================================================================
class FakeHost:
    def __init__(self):
        self.image = ""
        self.fs = {"/": {}}          # image folder -> {name: bytes | dict(folder)}
        self.calls = []
        self.refreshed = []
        self.roster = None           # (active, rows, max) once "listening"
        self.connected = False
        self.nfs = {}                # remote path -> bytes
        self.ndirs = {"/", "/home"}
        self.ident = {"ok": True, "type": "sync", "number": "5.9.14"}
        self.lacks_crc = False
        self.crc_fails = False
        self.nops = []
        self.put_corrupt = False

    # ---- app
    def version(self):
        return "9.9.9"

    def status(self):
        return {"version": "9.9.9", "image": {"path": self.image}}

    def log_tail(self, console, lines):
        return [f"{console} line {i}" for i in range(lines)]

    # ---- image
    def image_status(self):
        return {"path": self.image, "loaded": bool(self.image)}

    def image_path(self):
        return self.image

    def image_load(self, path, timeout):
        self.calls.append(("image_load", path))
        self.image = path
        return True, ("loaded" if path else "image unloaded")

    def image_recent(self):
        return [{"path": self.image, "exists": True}] if self.image else []

    def image_download(self, size, dest_dir, load):
        self.calls.append(("image_download", size, dest_dir, load))
        return {"ok": True, "zip": "x.zip", "image": "x.img", "loaded": load}

    def image_refresh(self, path):
        self.refreshed.append(path)

    def _node(self, path, create=False):
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        node = self.fs["/"]
        for p in parts:
            if p not in node:
                if not create:
                    return None
                node[p] = {}
            node = node[p]
            if not isinstance(node, dict):
                return node
        return node

    def hdfmonkey(self, verb, argv):
        if not self.image:
            raise McpToolError("no disk image is loaded: image_load first")
        self.calls.append(("hdfmonkey", verb, tuple(argv)))
        if verb == "ls":
            node = self._node(argv[0])
            if not isinstance(node, dict):
                return 1, b"", b"ls: not found\n"
            out = ""
            for name, v in node.items():
                out += ("[DIR]\t%s\n" % name) if isinstance(v, dict) else ("[%d bytes]\t%s\n" % (len(v), name))
            return 0, out.encode(), b""
        if verb == "mkdir":
            parent, name = argv[0].rstrip("/").rsplit("/", 1)
            node = self._node(parent or "/")
            if node is None or name in node:
                return 1, b"", b"mkdir: exists\n"
            node[name] = {}
            return 0, b"", b""
        if verb == "put":
            src, dst = argv
            parent, name = dst.rsplit("/", 1)
            node = self._node(parent or "/")
            if node is None:
                return 1, b"", b"put: no such folder\n"
            with open(src, "rb") as fh:
                node[name] = fh.read()
            return 0, b"", b""
        if verb == "get":
            src, dst = argv
            node = self._node(src)
            if not isinstance(node, (bytes, bytearray)):
                return 1, b"", b"get: not a file\n"
            with open(dst, "wb") as fh:
                fh.write(node)
            return 0, b"", b""
        if verb == "rm":
            parent, name = argv[0].rstrip("/").rsplit("/", 1)
            node = self._node(parent or "/")
            if node is None or name not in node:
                return 1, b"", b"rm: not found\n"
            if isinstance(node[name], dict) and node[name]:
                return 1, b"", b"rm: directory not empty\n"
            del node[name]
            return 0, b"", b""
        if verb == "rename":
            old, new = argv
            p1, n1 = old.rsplit("/", 1)
            p2, n2 = new.rsplit("/", 1)
            a = self._node(p1 or "/")
            b = self._node(p2 or "/")
            if a is None or b is None or n1 not in a:
                return 1, b"", b"rename: not found\n"
            b[n2] = a.pop(n1)
            return 0, b"", b""
        return 2, b"", b"unknown verb\n"

    # ---- emulators
    def emulators(self):
        return [{"name": "MAME", "installed": True, "blocked": ""},
                {"name": "CSpect", "installed": True, "blocked": "no image"}]

    def launch(self, emulator, file, card_path, cspect_mcp=None, cspect_mcp_port=None):
        self.calls.append(("launch", emulator, file, card_path, cspect_mcp, cspect_mcp_port))
        if emulator == "CSpect" and file is None and not self.image:
            return False, "no image loaded", None, {}
        extra = {}
        if emulator == "CSpect" and cspect_mcp:
            extra["cspect_mcp_port"] = cspect_mcp_port or 2051
        return True, f"{emulator} started", 4242, extra

    def processes(self):
        return [{"pid": 4242, "emulator": "MAME", "image": self.image}]

    def stop_process(self, pid):
        return (pid == 4242), ("stopped" if pid == 4242 else "no such pid")

    # ---- esp
    def esp_status(self):
        return {"running": False, "port": 2222}

    def esp_start(self, port, verbose):
        self.calls.append(("esp_start", port, verbose))
        return True, "running", port or 2222

    def esp_stop(self, force):
        return (True, "stopped") if force else (False, "1 MAME uses it")

    # ---- nextsync
    def nextsync_status(self):
        return {"listener": {"running": self.roster is not None}}

    def listener_start(self, sync_root):
        self.calls.append(("listener_start", sync_root))
        self.roster = (1, [(1, "10.0.0.5", "bench")], 4)
        return True, "listening"

    def listener_stop(self):
        self.roster = None
        self.connected = False
        return True, "stopped"

    def set_sync_root(self, path):
        self.calls.append(("set_sync_root", path))
        return os.path.isdir(path), "set" if os.path.isdir(path) else "not a folder"

    def classic_start(self, mode):
        return True, f"classic {mode}"

    def classic_stop(self):
        return True, "classic stopped"

    def bridge_start(self, port):
        return True, f"bridge {port}"

    def bridge_stop(self):
        return True, "bridge stopped"

    # ---- the Next
    def next_sessions(self):
        return self.roster

    def next_select(self, sid):
        return (sid == 1), "selected" if sid == 1 else "gone"

    def _dir_of(self, p):
        return p.rsplit("/", 1)[0] or "/"

    def next_cmd(self, op, a1, a2, session, timeout):
        if self.roster is None:
            raise McpToolError("the NextSync listener is not running: nextsync_listener_start first")
        if not self.connected:
            raise McpToolError("no Next is seated on the listener")
        self.nops.append((op, a1, a2, session))
        if op == "version":
            return dict(self.ident)
        if op == "drives":
            return {"ok": True, "current": "C", "letters": ["C", "M"]}
        if op == "free":
            return {"ok": True, "free": 1234567}
        if op == "ls":
            d = a1.rstrip("/") or "/"
            if d not in self.ndirs:
                return {"ok": False, "error": "ls FAIL"}
            entries = []
            for sub in sorted(self.ndirs):
                if sub != d and self._dir_of(sub) == d:
                    entries.append((True, 0, sub.rsplit("/", 1)[-1]))
            for p, data in sorted(self.nfs.items()):
                if self._dir_of(p) == d:
                    entries.append((False, len(data), p.rsplit("/", 1)[-1]))
            return {"ok": True, "entries": entries}
        if op == "mkdir":
            d = a1.rstrip("/")
            if d in self.ndirs:
                return {"ok": False}
            self.ndirs.add(d)
            return {"ok": True}
        if op == "put":
            remote, local = a1, a2
            if remote.endswith("/"):
                remote += os.path.basename(local)
            with open(local, "rb") as fh:
                data = fh.read()
            if self.put_corrupt and data:
                data = data[:-1] + bytes([data[-1] ^ 0xFF])   # flip the last byte
            self.nfs[remote] = data
            return {"ok": True}
        if op == "get":
            remote, dest = a1, a2
            if remote in self.nfs:
                last = os.path.join(dest, remote.rsplit("/", 1)[-1])
                with open(last, "wb") as fh:
                    fh.write(self.nfs[remote])
                return {"ok": True, "count": 1, "last": last}
            count = 0
            last = None
            for p, data in self.nfs.items():
                if p.startswith(remote.rstrip("/") + "/"):
                    rel = p[len(remote.rstrip("/")) + 1:]
                    last = os.path.join(dest, remote.rstrip("/").rsplit("/", 1)[-1], *rel.split("/"))
                    os.makedirs(os.path.dirname(last), exist_ok=True)
                    with open(last, "wb") as fh:
                        fh.write(data)
                    count += 1
            return {"ok": count > 0, "count": count, "last": last, "error": "" if count else "get FAIL"}
        if op == "crc":
            if self.crc_fails or a1 not in self.nfs:
                return {"ok": False, "error": "crc failed (the file did not open)"}
            return {"ok": True, "path": a1, "crc32": "%08X" % (zlib.crc32(self.nfs[a1]) & 0xFFFFFFFF)}
        if op in ("rm", "rmdir", "rmtree"):
            return {"ok": True}
        if op == "ren":
            return {"ok": a1 in self.nfs}
        if op == "rcpy":
            return {"ok": True, "files": 1}
        if op == "rfsize":
            return {"ok": True, "files": 2, "dirs": 1, "bytes": 99}
        if op == "forceexit":
            self.connected = False
            return {"ok": True}
        return {"ok": False, "error": f"unknown op {op}"}


def make_server(host=None, log=None):
    host = host or FakeHost()
    srv = McpServer(build_mcp_tools(host), "9.9.9", instructions="hi", log=log)
    return host, srv


def call(srv, name, args=None, rid=7):
    reply = srv.handle_message(rpc("tools/call", {"name": name, "arguments": args or {}}, rid))
    return reply


def result_of(reply):
    """(is_error, text, structured) of a tools/call reply."""
    res = reply.get("result")
    if res is None:
        return True, json.dumps(reply.get("error")), None
    text = "".join(c.get("text", "") for c in res.get("content", []))
    return bool(res.get("isError")), text, res.get("structuredContent")


# =====================================================================
#  1. the dispatcher
# =====================================================================
def test_dispatcher():
    print("-- dispatcher")
    calls = []

    def echo(args):
        calls.append(args)
        return {"got": args}

    def boom(args):
        raise McpToolError("no image loaded")

    def crash(args):
        raise RuntimeError("kaboom")

    tools = [
        McpTool("echo", "echo", echo,
                {"s": param("string", "a string"), "n": param("integer", "an int"),
                 "e": param("string", "enum", enum=["a", "b"])}, required=("s",)),
        McpTool("boom", "boom", boom),
        McpTool("crash", "crash", crash),
        McpTool("text", "text", lambda a: "plain"),
    ]
    srv = McpServer(tools, "1.2.3", instructions="do this")
    try:
        McpServer(tools + [McpTool("echo", "dup", echo)], "1")
        check("duplicate tool name refused", False)
    except ValueError:
        check("duplicate tool name refused", True)

    r = srv.handle_message(rpc("initialize", {"protocolVersion": "2025-03-26",
                                              "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "9"}}))
    res = r["result"]
    check("initialize echoes a supported version", res["protocolVersion"] == "2025-03-26")
    check("initialize names the server", res["serverInfo"]["name"] == "zx-next-unite"
          and res["serverInfo"]["version"] == "1.2.3")
    check("initialize carries instructions + tools capability",
          res.get("instructions") == "do this" and "tools" in res["capabilities"])
    check("last client recorded", srv.last_client == "t 9")
    r = srv.handle_message(rpc("initialize", {"protocolVersion": "1999-01-01"}))
    check("unknown version -> latest", r["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0])
    check("notification answered with None",
          srv.handle_message(rpc("notifications/initialized", rid=None)) is None)
    check("ping -> {}", srv.handle_message(rpc("ping"))["result"] == {})
    r = srv.handle_message(rpc("tools/list"))
    names = [t["name"] for t in r["result"]["tools"]]
    check("tools/list lists every tool with a schema", names == ["echo", "boom", "crash", "text"]
          and all("inputSchema" in t for t in r["result"]["tools"]))
    r = srv.handle_message(rpc("tools/call", {"name": "echo", "arguments": {"s": "x", "n": 3}}))
    is_err, text, structured = result_of(r)
    check("tools/call dict result -> text + structuredContent",
          not is_err and structured == {"got": {"s": "x", "n": 3}} and '"s": "x"' in text)
    check("call counted", srv.calls == 1)
    r = srv.handle_message(rpc("tools/call", {"name": "text", "arguments": {}}))
    check("str result -> one text block", result_of(r)[1] == "plain")
    r = srv.handle_message(rpc("tools/call", {"name": "boom"}))
    is_err, text, _ = result_of(r)
    check("McpToolError -> isError result, never a JSON-RPC error",
          is_err and "result" in r and text == "no image loaded")
    r = srv.handle_message(rpc("tools/call", {"name": "crash"}))
    is_err, text, _ = result_of(r)
    check("a crash -> isError result naming the tool", is_err and "crash failed: kaboom" in text)
    for bad, why in (({"n": 1}, "missing required"), ({"s": "x", "zz": 1}, "unknown argument"),
                     ({"s": 1}, "must be a string"), ({"s": "x", "n": "3"}, "must be a integer"),
                     ({"s": "x", "n": True}, "must be a integer"), ({"s": "x", "e": "c"}, "one of")):
        r = srv.handle_message(rpc("tools/call", {"name": "echo", "arguments": bad}))
        check(f"invalid arguments {bad} -> -32602",
              r.get("error", {}).get("code") == INVALID_PARAMS and why in r["error"]["message"],
              r.get("error"))
    r = srv.handle_message(rpc("tools/call", {"name": "nope"}))
    check("unknown tool -> -32602", r["error"]["code"] == INVALID_PARAMS and "unknown tool" in r["error"]["message"])
    r = srv.handle_message(rpc("resources/list"))
    check("unknown method -> -32601", r["error"]["code"] == METHOD_NOT_FOUND)
    r = srv.handle_message({"id": 1, "method": "ping"})
    check("missing jsonrpc -> -32600", r["error"]["code"] == INVALID_REQUEST)
    check("a client response is ignored", srv.handle_message({"jsonrpc": "2.0", "id": 9, "result": {}}) is None)
    reply, batch = srv.handle_text(json.dumps([rpc("ping", rid=1), rpc("notifications/x", rid=None), rpc("ping", rid=2)]))
    check("batch: one reply per request", batch and [x["id"] for x in reply] == [1, 2])
    reply, batch = srv.handle_text("[]")
    check("empty batch -> -32600", reply["error"]["code"] == INVALID_REQUEST)
    reply, _ = srv.handle_text("{not json")
    check("parse error -> -32700 id null", reply["error"]["code"] == PARSE_ERROR and reply["id"] is None)
    probs = validate_arguments({"type": "object", "properties": {"a": {"type": "number"}},
                                "required": ["a"], "additionalProperties": False}, {"a": 1.5})
    check("validator accepts a float number", probs == [])


# =====================================================================
#  2. the HTTP transport
# =====================================================================
def _http(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_http():
    print("-- http transport")
    logs = []
    _host, srv = make_server(log=logs.append)
    http = McpHttpServer(srv, port=0, log=logs.append)
    http.start()
    try:
        url = http.url
        check("port 0 reads back the chosen port", http.port != 0 and url.endswith(f":{http.port}{MCP_PATH}"))
        check("running", http.running and http.started_at is not None)
        st, body, hdr = _http(url, rpc("initialize", {"protocolVersion": "2025-06-18"}))
        check("POST initialize -> 200 json", st == 200 and json.loads(body)["result"]["protocolVersion"] == "2025-06-18")
        check("Mcp-Session-Id issued", hdr.get("Mcp-Session-Id") == srv.session_id)
        st, body, _ = _http(url, rpc("notifications/initialized", rid=None))
        check("notification -> 202 no body", st == 202 and body == b"")
        st, body, _ = _http(url, rpc("tools/list"))
        check("tools/list over HTTP", st == 200 and "app_status" in [t["name"] for t in json.loads(body)["result"]["tools"]])
        st, body, _ = _http(url + "/", rpc("ping"))
        check("trailing slash accepted", st == 200)
        st, body, hdr = _http(url, method="GET")
        check("GET /mcp -> 405 with Allow", st == 405 and "POST" in hdr.get("Allow", ""))
        st, body, _ = _http(url.rsplit("/", 1)[0] + "/", method="GET")
        check("GET / -> help text naming tools", st == 200 and b"next_deploy" in body)
        st, body, _ = _http(url, method="DELETE")
        check("DELETE -> 204", st == 204)
        st, body, _ = _http(url.rsplit("/", 1)[0] + "/nope", rpc("ping"))
        check("wrong path -> 404", st == 404)
        st, body, _ = _http(url, b"{bad json", headers={})
        check("bad JSON -> 400 + -32700", st == 400 and json.loads(body)["error"]["code"] == PARSE_ERROR)
        st, body, _ = _http(url, rpc("ping"), headers={"Origin": "http://evil.example"})
        check("foreign Origin -> 403", st == 403)
        st, body, _ = _http(url, rpc("ping"), headers={"Origin": "http://localhost:3000"})
        check("loopback Origin accepted", st == 200)
        st, body, _ = _http(url, rpc("tools/call", {"name": "app_status"}))
        check("a tool over HTTP", st == 200 and json.loads(body)["result"]["structuredContent"]["version"] == "9.9.9")
    finally:
        http.stop()
    check("stopped", not http.running)
    check("log saw the client", any("client connected" in s for s in logs))

    # bearer token
    _host, srv = make_server()
    http = McpHttpServer(srv, port=0, auth_token="s3cret")
    http.start()
    try:
        st, body, hdr = _http(http.url, rpc("ping"))
        check("no token -> 401 + WWW-Authenticate", st == 401 and "Bearer" in hdr.get("WWW-Authenticate", "")
              and json.loads(body)["error"]["code"] == SERVER_REFUSED)
        st, _, _ = _http(http.url, rpc("ping"), headers={"Authorization": "Bearer wrong"})
        check("wrong token -> 401", st == 401)
        st, _, _ = _http(http.url, rpc("ping"), headers={"Authorization": "Bearer s3cret"})
        check("Authorization: Bearer accepted", st == 200)
        st, _, _ = _http(http.url, rpc("ping"), headers={MCP_TOKEN_HEADER: "s3cret"})
        check("the custom header accepted too", st == 200)
        st, _, _ = _http(http.url, method="DELETE")
        check("DELETE needs the token too", st == 401)
        st, _, _ = _http(http.url.rsplit("/", 1)[0] + "/", method="GET")
        check("the help page needs no token", st == 200)
    finally:
        http.stop()

    # a client that RESETS its keep-alive connection must leave no traceback
    # on stderr (socketserver's default handle_error prints one; a Claude
    # Code health probe or a shim ending did exactly that on Windows).
    _host, srv = make_server()
    http = McpHttpServer(srv, port=0)
    http.start()
    old_err = sys.stderr
    sys.stderr = io.StringIO()
    try:
        s = socket.create_connection(("127.0.0.1", http.port), timeout=5)
        s.sendall(json.dumps(rpc("ping")).encode().join(
            [f"POST {MCP_PATH} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
             f"Content-Length: {len(json.dumps(rpc('ping')))}\r\n\r\n".encode(), b""]))
        s.recv(4096)
        # SO_LINGER 0: the close sends RST, not FIN - the Windows 10054 case.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()
        s2 = socket.create_connection(("127.0.0.1", http.port), timeout=5)
        s2.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s2.close()                       # reset before a single byte
        time.sleep(0.5)
        st, _, _ = _http(http.url, rpc("ping"))
        check("the server still answers after client resets", st == 200)
        leaked = sys.stderr.getvalue()
    finally:
        sys.stderr = old_err
        http.stop()
    check("a client reset prints no traceback", "Traceback" not in leaked and "10054" not in leaked, leaked[:300])

    # port in use
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0) if os.name != "nt" else None
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    http = McpHttpServer(srv, port=taken)
    try:
        http.start()
        started = True
    except OSError:
        started = False
    check("a port in use raises and says so", not started and http.port_in_use)
    if started:
        http.stop()
    blocker.close()


# =====================================================================
#  3. the stdio shim
# =====================================================================
def test_stdio():
    print("-- stdio shim")
    url, tok, timeout = stdio_target(["--url", "http://x/mcp", "--token", "T"], environ={})
    check("stdio_target: args", (url, tok) == ("http://x/mcp", "T") and timeout == 7200.0)
    url, tok, _ = stdio_target([], environ={"ZX_NEXT_UNITE_MCP_PORT": "4321", "ZX_NEXT_UNITE_MCP_TOKEN": "E"})
    check("stdio_target: env port + token", url == f"http://127.0.0.1:4321{MCP_PATH}" and tok == "E")
    url, _, _ = stdio_target(["--port=77"], environ={"ZX_NEXT_UNITE_MCP_URL": "http://env/mcp"})
    check("stdio_target: env URL wins over a port", url == "http://env/mcp")
    url, _, _ = stdio_target([], environ={})
    check("stdio_target: default", url == f"http://127.0.0.1:{MCP_DEFAULT_PORT}{MCP_PATH}")

    _host, srv = make_server()
    http = McpHttpServer(srv, port=0, auth_token="tok")
    http.start()
    try:
        stdin = io.BytesIO(b"\n".join([
            json.dumps(rpc("initialize", {"protocolVersion": "2025-06-18"}, rid=1)).encode(),
            json.dumps(rpc("notifications/initialized", rid=None)).encode(),
            b"",
            json.dumps(rpc("tools/call", {"name": "app_log_tail", "arguments": {"lines": 2}}, rid=2)).encode(),
            b"{broken",
        ]) + b"\n")
        stdout = io.BytesIO()
        rc = stdio_main(["--url", http.url, "--token", "tok"], stdin=stdin, stdout=stdout, environ={})
        lines = [json.loads(l) for l in stdout.getvalue().decode().splitlines() if l.strip()]
        by_id = {l.get("id"): l for l in lines}
        check("shim exit 0", rc == 0)
        check("shim relayed initialize", by_id.get(1, {}).get("result", {}).get("serverInfo", {}).get("name") == "zx-next-unite")
        check("shim relayed a tool call", "nextsync line 0" in json.dumps(by_id.get(2, {})))
        check("shim wrote nothing for the notification", len(lines) == 3)
        check("shim reports a broken line as -32700", by_id.get(None, {}).get("error", {}).get("code") == PARSE_ERROR)
        for l in lines:
            check("every shim line is one JSON object", isinstance(l, dict))
        # wrong token -> the server's 401 relayed as a JSON-RPC error
        stdout = io.BytesIO()
        stdio_main(["--url", http.url, "--token", "bad"],
                   stdin=io.BytesIO(json.dumps(rpc("ping", rid=5)).encode() + b"\n"),
                   stdout=stdout, environ={})
        l = json.loads(stdout.getvalue().decode().strip())
        check("shim relays a refusal as an error with the request id", l["id"] == 5 and l["error"]["code"] == SERVER_REFUSED)
    finally:
        http.stop()
    dead = _free_port()
    stdout = io.BytesIO()
    stdio_main(["--port", str(dead)], stdin=io.BytesIO(json.dumps(rpc("ping", rid=3)).encode() + b"\n"),
               stdout=stdout, environ={})
    l = json.loads(stdout.getvalue().decode().strip())
    check("app not running -> -32000 naming Settings", l["id"] == 3 and l["error"]["code"] == SERVER_UNREACHABLE
          and "MCP server" in l["error"]["message"])


# =====================================================================
#  4. the tools over the fake host
# =====================================================================
def test_tools():
    print("-- tools")
    host, srv = make_server()
    names = [t.name for t in srv.tools]
    check("40 tools", len(names) >= 40, len(names))
    check("tool names are unique", len(set(names)) == len(names))
    for prefix in ("app_", "image_", "emulator_", "esp_emulation_", "nextsync_", "next_"):
        check(f"a {prefix}* family exists", any(n.startswith(prefix) for n in names))
    for t in srv.tools:
        check(f"{t.name} has a description", len(t.description) > 20)

    is_err, text, s = result_of(call(srv, "app_status"))
    check("app_status", not is_err and s["version"] == "9.9.9")
    is_err, text, s = result_of(call(srv, "app_log_tail", {"console": "sdcard", "lines": 3}))
    check("app_log_tail", not is_err and s["lines"] == ["sdcard line 0", "sdcard line 1", "sdcard line 2"])
    r = call(srv, "app_log_tail", {"console": "nope"})
    check("app_log_tail enum validated", r.get("error", {}).get("code") == INVALID_PARAMS)

    # ---- image tools
    is_err, text, _ = result_of(call(srv, "image_ls"))
    check("image_ls without an image -> isError naming image_load", is_err and "image_load" in text)
    is_err, text, _ = result_of(call(srv, "image_load", {"path": os.path.join(REPO, "no-such.img")}))
    check("image_load of a missing file -> isError", is_err and "no such file" in text)
    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, "card.img")
        open(img, "wb").write(b"\0" * 16)
        is_err, text, s = result_of(call(srv, "image_load", {"path": img}))
        check("image_load", not is_err and s["path"] == img and host.image == img)
        is_err, _, s = result_of(call(srv, "image_mkdir", {"path": "/games"}))
        check("image_mkdir", not is_err and "games" in host.fs["/"] and host.refreshed[-1] == "/")
        f1 = os.path.join(tmp, "a.nex")
        open(f1, "wb").write(b"NEX" * 100)
        is_err, _, s = result_of(call(srv, "image_put", {"local_path": f1, "image_dir": "/games"}))
        check("image_put file", not is_err and s["files"] == 1 and host.fs["/"]["games"]["a.nex"] == b"NEX" * 100)
        folder = os.path.join(tmp, "pack")
        os.makedirs(os.path.join(folder, "sub"))
        open(os.path.join(folder, "x.txt"), "wb").write(b"x")
        open(os.path.join(folder, "sub", "y.txt"), "wb").write(b"yy")
        is_err, text, s = result_of(call(srv, "image_put", {"local_path": folder, "image_dir": "/games"}))
        check("image_put folder (recursive, folders created)", not is_err and s["files"] == 2
              and host.fs["/"]["games"]["pack"]["sub"]["y.txt"] == b"yy", "" if not is_err else text)
        is_err, _, s = result_of(call(srv, "image_ls", {"path": "/games"}))
        check("image_ls", not is_err and {e["name"]: (e["is_dir"], e["size"]) for e in s["entries"]}
              == {"a.nex": (False, 300), "pack": (True, 0)})
        is_err, _, s = result_of(call(srv, "image_get", {"image_path": "/games/a.nex", "local_path": tmp}))
        check("image_get into a folder keeps the name", not is_err and s["local_path"].endswith("a.nex")
              and s["bytes"] == 300)
        is_err, _, s = result_of(call(srv, "image_rename", {"old_path": "/games/a.nex", "new_path": "/games/b.nex"}))
        check("image_rename", not is_err and "b.nex" in host.fs["/"]["games"])
        is_err, text, _ = result_of(call(srv, "image_rm", {"path": "/games/pack"}))
        check("image_rm of a full folder without recursive -> isError", is_err and "recursive=true" in text)
        is_err, _, s = result_of(call(srv, "image_rm", {"path": "/games/pack", "recursive": True}))
        check("image_rm recursive: every file first, then folders deepest-first", not is_err
              and s["deleted"] == ["/games/pack/x.txt", "/games/pack/sub/y.txt", "/games/pack/sub", "/games/pack"]
              and "pack" not in host.fs["/"]["games"], s and s.get("deleted"))
        is_err, text, _ = result_of(call(srv, "image_rm", {"path": "/"}))
        check("image_rm refuses the root", is_err)
        is_err, text, _ = result_of(call(srv, "image_rm", {"path": "/games/zzz"}))
        check("image_rm of a missing entry -> isError", is_err and "not found" in text)
        is_err, _, s = result_of(call(srv, "image_download_nextzxos", {"size": "4gb"}))
        check("image_download_nextzxos passes size/dest/load", not is_err and host.calls[-1] == ("image_download", "4gb", "", True))

        # ---- emulators
        is_err, _, s = result_of(call(srv, "emulator_start", {"emulator": "CSpect", "cspect_mcp": True, "cspect_mcp_port": 2099}))
        check("emulator_start CSpect with its MCP server reports the port",
              not is_err and s["pid"] == 4242 and s["cspect_mcp_port"] == 2099
              and host.calls[-1] == ("launch", "CSpect", None, None, True, 2099))
        is_err, _, s = result_of(call(srv, "emulator_start", {"emulator": "MAME", "file": f1, "image_dir": "/home"}))
        check("emulator_start MAME with file + image_dir puts the file then passes card_path",
              not is_err and s["card_path"] == "/home/a.nex" and host.fs["/"]["home"]["a.nex"] == b"NEX" * 100
              and host.calls[-1] == ("launch", "MAME", f1, "/home/a.nex", None, None))
        is_err, text, _ = result_of(call(srv, "emulator_start", {"emulator": "MAME", "file": "/no/such.nex"}))
        check("emulator_start with a missing file -> isError", is_err and "no such file" in text)
        r = call(srv, "emulator_start", {"emulator": "ZEsarUX"})
        check("emulator enum validated", r.get("error", {}).get("code") == INVALID_PARAMS)
        is_err, text, _ = result_of(call(srv, "emulator_stop", {"pid": 1}))
        check("emulator_stop wrong pid -> isError", is_err and "no such pid" in text)
        is_err, _, s = result_of(call(srv, "emulator_stop", {"pid": 4242}))
        check("emulator_stop", not is_err and s["pid"] == 4242)
        is_err, _, s = result_of(call(srv, "emulator_list"))
        check("emulator_list", not is_err and s["emulators"][1]["blocked"] == "no image")

        # ---- esp
        is_err, _, s = result_of(call(srv, "esp_emulation_start", {"port": 2300}))
        check("esp_emulation_start", not is_err and s["port"] == 2300 and host.calls[-1] == ("esp_start", 2300, None))
        is_err, text, _ = result_of(call(srv, "esp_emulation_stop"))
        check("esp_emulation_stop refused while used", is_err and "MAME" in text)
        is_err, _, _ = result_of(call(srv, "esp_emulation_stop", {"force": True}))
        check("esp_emulation_stop force", not is_err)

        # ---- next tools before a listener
        is_err, text, _ = result_of(call(srv, "next_ls"))
        check("next_ls without a listener -> isError naming nextsync_listener_start",
              is_err and "nextsync_listener_start" in text)
        is_err, text, _ = result_of(call(srv, "nextsync_listener_start", {"sync_root": "/no/such/dir"}))
        check("nextsync_listener_start with a bad sync root -> isError", is_err)
        is_err, _, s = result_of(call(srv, "nextsync_listener_start", {"sync_root": tmp}))
        check("nextsync_listener_start sets the root then starts", not is_err
              and host.calls[-2:] == [("set_sync_root", tmp), ("listener_start", tmp)])
        is_err, _, s = result_of(call(srv, "next_sessions"))
        check("next_sessions", not is_err and s["active"] == 1 and s["sessions"][0]["name"] == "bench")
        is_err, text, _ = result_of(call(srv, "next_ls"))
        check("next_ls without a seated Next -> isError", is_err and "seated" in text)
        host.connected = True
        host.nfs["/home/old.nex"] = b"OLD"
        is_err, _, s = result_of(call(srv, "next_ls", {"path": "/home"}))
        check("next_ls", not is_err and s["entries"] == [{"name": "old.nex", "is_dir": False, "size": 3}])
        is_err, _, s = result_of(call(srv, "next_version"))
        check("next_version", not is_err and s == {"type": "sync", "number": "5.9.14"})
        is_err, _, s = result_of(call(srv, "next_drives"))
        check("next_drives", not is_err and s["drives"] == ["C", "M"])
        is_err, _, s = result_of(call(srv, "next_free", {"drive": "C"}))
        check("next_free", not is_err and s["free_bytes"] == 1234567)
        is_err, text, _ = result_of(call(srv, "next_select", {"session": 2}))
        check("next_select of a gone sid -> isError", is_err)

        # next_put: a file, verified by the CRC the Next computes
        host.nops.clear()
        is_err, text, s = result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/"}))
        check("next_put into a folder keeps the name and verifies by crc", not is_err
              and s["remote_path"] == "/home/a.nex" and s["verified"] and s["method"] == "crc"
              and host.nfs["/home/a.nex"] == b"NEX" * 100, "" if not is_err else text)
        check("next_put wire order: version (cached ident), put, crc",
              [o[0] for o in host.nops] == ["put", "version", "crc"], host.nops)
        # the ident is cached per sid - never for the active-session bucket
        host.nops.clear()
        result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/b.nex", "session": 1}))
        result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/c.nex", "session": 1}))
        check("ident asked once per sid", [o[0] for o in host.nops].count("version") == 1)
        # a corrupt landing is reported, never called ok
        host.put_corrupt = True
        is_err, text, _ = result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/bad.nex"}))
        check("next_put: a crc mismatch is an error", is_err and "did not verify" in text)
        host.put_corrupt = False
        # a listener without the crc op -> read-back compare (the ACTIVE
        # seat's ident is never cached, so the older build is seen at once)
        host.ident = {"ok": True, "type": "sync", "number": "5.8.0"}
        host.nops.clear()
        is_err, text, s = result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/rb.nex"}))
        check("next_put against an old listener verifies by read-back", not is_err
              and s["method"] == "readback" and s["verified"] and "crc" not in [o[0] for o in host.nops]
              and "get" in [o[0] for o in host.nops], "" if not is_err else text)
        host.ident = {"ok": True, "type": "sync", "number": "5.9.14"}
        # the crc op failing on one file -> read-back, not a silent pass
        host.crc_fails = True
        host.nops.clear()
        is_err, _, s = result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/rb2.nex", "session": 1}))
        check("a failed crc falls back to read-back", not is_err and s["method"] == "readback" and s["verified"])
        host.crc_fails = False
        is_err, _, s = result_of(call(srv, "next_put", {"local_path": f1, "remote_path": "/home/nv.nex", "verify": False}))
        check("next_put verify=false skips verification", not is_err and "verified" not in s)
        # a folder
        host.nops.clear()
        is_err, text, s = result_of(call(srv, "next_put", {"local_path": folder, "remote_path": "/home"}))
        check("next_put folder: mkdir root + sub, every file put and verified", not is_err
              and s["files"] == 2 and s["root"] == "/home/pack"
              and host.nfs["/home/pack/sub/y.txt"] == b"yy"
              and [o[:2] for o in host.nops if o[0] == "mkdir"] == [("mkdir", "/home/pack"), ("mkdir", "/home/pack/sub")],
              "" if not is_err else text)
        host.nops.clear()
        is_err, _, _ = result_of(call(srv, "next_put", {"local_path": folder, "remote_path": "/home"}))
        check("next_put folder again: an existing folder is tolerated (mkdir F, ls ok)",
              not is_err and [o[0] for o in host.nops][:2] == ["mkdir", "ls"])
        # get
        dl = os.path.join(tmp, "dl")
        is_err, _, s = result_of(call(srv, "next_get", {"remote_path": "/home/pack", "local_dir": dl}))
        check("next_get folder", not is_err and s["files"] == 2
              and open(os.path.join(dl, "pack", "sub", "y.txt"), "rb").read() == b"yy")
        for name, args, op in (("next_mkdir", {"path": "/new"}, "mkdir"), ("next_rmdir", {"path": "/new"}, "rmdir"),
                               ("next_rm", {"path": "/home/old.nex"}, "rm"), ("next_rmtree", {"path": "/home/pack"}, "rmtree"),
                               ("next_rename", {"old_path": "/home/a.nex", "new_path": "/home/z.nex"}, "ren"),
                               ("next_copy", {"src": "/home/a.nex", "dst": "m:/a.nex"}, "rcpy"),
                               ("next_size", {"path": "/home"}, "rfsize"), ("next_crc", {"path": "/home/a.nex"}, "crc")):
            host.nops.clear()
            is_err, text, _ = result_of(call(srv, name, args))
            check(f"{name} -> {op}", not is_err and host.nops[-1][0] == op, "" if not is_err else text)
        # next_deploy: put, verify, exit - and never exit on a bad landing
        host.nops.clear()
        is_err, _, s = result_of(call(srv, "next_deploy", {"local_path": f1}))
        check("next_deploy: put + (ident) + crc + forceexit, default remote path", not is_err
              and s["remote_path"] == NEXT_DEFAULT_DEPLOY_PATH and s["exited"]
              and [o[0] for o in host.nops] == ["put", "version", "crc", "forceexit"], host.nops)
        check("next_deploy frees the seat", not host.connected)
        host.connected = True
        host.put_corrupt = True
        host.nops.clear()
        is_err, text, _ = result_of(call(srv, "next_deploy", {"local_path": f1, "remote_path": "/home/x.nex"}))
        check("next_deploy never exits on a bad landing", is_err and "forceexit" not in [o[0] for o in host.nops])
        host.put_corrupt = False
        host.nops.clear()
        is_err, _, s = result_of(call(srv, "next_deploy", {"local_path": f1, "run": False}))
        check("next_deploy run=false leaves the Next seated", not is_err and "exited" not in s and host.connected)
        is_err, _, _ = result_of(call(srv, "next_exit"))
        check("next_exit", not is_err and host.nops[-1][0] == "forceexit")
        is_err, _, _ = result_of(call(srv, "nextsync_listener_stop"))
        check("nextsync_listener_stop", not is_err and host.roster is None)
        is_err, _, s = result_of(call(srv, "nextsync_classic_start", {"mode": "always"}))
        check("nextsync_classic_start", not is_err and s["mode"] == "always")
        is_err, _, _ = result_of(call(srv, "nextsync_http_bridge_start", {"port": 8080}))
        check("nextsync_http_bridge_start", not is_err)
        is_err, _, s = result_of(call(srv, "image_unload"))
        check("image_unload", not is_err and host.image == "")


# =====================================================================
#  5. the ls parser against the explorer's own
# =====================================================================
def test_parse_ls():
    print("-- hdfmonkey ls parser")
    sample = b"[DIR]\tgames\n[1234 bytes]\tboot.bas\n[0 bytes]\tempty\njunk line\n"
    mine = parse_hdfmonkey_ls(sample.decode())
    check("parse_hdfmonkey_ls", mine == [{"name": "games", "is_dir": True, "size": 0},
                                         {"name": "boot.bas", "is_dir": False, "size": 1234},
                                         {"name": "empty", "is_dir": False, "size": 0}])
    try:
        from zxnu_sdcard_explorer import SdCardExplorerPane
        theirs = SdCardExplorerPane.image_parse_ls(sample)
        check("agrees with SdCardExplorerPane.image_parse_ls",
              [(e["name"], e["is_dir"], str(e["size"]) if not e["is_dir"] else "") for e in mine]
              == [(n, d, s) for n, d, s in theirs], theirs)
    except Exception as ex:                                    # noqa: BLE001
        check("agrees with SdCardExplorerPane.image_parse_ls (import)", False, ex)


# =====================================================================
#  6. the wiring tripwires
# =====================================================================
def _src(name):
    with open(os.path.join(REPO, name), "r", encoding="utf-8") as fh:
        return fh.read()


def test_wiring():
    print("-- wiring")
    import zxnu_config as C
    keys = list(C.CONFIG_FILE_SETTINGS)
    for k in ("SETTING_MCP_SERVER", "SETTING_MCP_PORT", "SETTING_MCP_TOKEN_ENABLED", "SETTING_MCP_TOKEN",
              "SETTING_CSPECT_MCP", "SETTING_CSPECT_MCP_PORT"):
        check(f"{k} persisted (in CONFIG_FILE_SETTINGS)", getattr(C, k) in keys)
    i = keys.index(C.SETTING_NEXTSYNC_HTTP_TOKEN)
    check("the MCP keys follow the bridge's", keys[i + 1:i + 5] == [C.SETTING_MCP_SERVER, C.SETTING_MCP_PORT,
                                                                   C.SETTING_MCP_TOKEN_ENABLED, C.SETTING_MCP_TOKEN])
    check("mcp_server_enabled default OFF", not C.mcp_server_enabled({}) and not C.mcp_server_enabled({C.SETTING_MCP_SERVER: ""})
          and C.mcp_server_enabled({C.SETTING_MCP_SERVER: "true"}))
    check("mcp_server_port default + bounds", C.mcp_server_port({}) == MCP_DEFAULT_PORT
          and C.mcp_server_port({C.SETTING_MCP_PORT: "2345"}) == 2345
          and C.mcp_server_port({C.SETTING_MCP_PORT: "99999"}) == MCP_DEFAULT_PORT
          and C.mcp_server_port({C.SETTING_MCP_PORT: "x"}) == MCP_DEFAULT_PORT)
    check("cspect_mcp_argument", C.cspect_mcp_argument(False, 1) == "" and C.cspect_mcp_argument(True, 2099) == "-mcp=2099"
          and C.cspect_mcp_argument(True, "bad") == "-mcp=%d" % C.CSPECT_MCP_DEFAULT_PORT)
    check("strip_cspect_mcp_tokens", C.strip_cspect_mcp_tokens("-basickeys -mcp -zxnext -MCP=12 -w3") == "-basickeys -zxnext -w3")
    check("cspect_mcp_port default", C.cspect_mcp_port({}) == C.CSPECT_MCP_DEFAULT_PORT and C.CSPECT_MCP_DEFAULT_PORT != MCP_DEFAULT_PORT)

    pane = _src("zxnu_settings_pane.py")
    m = re.search(r"SETTINGS_TAB_ROWS = \((.*?)\n\)", pane, re.S)
    rows = ast.literal_eval("(" + m.group(1) + "\n)")
    check("Settings row 'mcp_server' right after 'http_bridge'", rows.index("mcp_server") == rows.index("http_bridge") + 1)
    check("Settings row 'cspect_mcp' right after 'cspect_params'", rows.index("cspect_mcp") == rows.index("cspect_params") + 1)
    for row in ("mcp_server", "cspect_mcp"):
        check(f"{row} placed by name", f'settings_grid_row("{row}")' in pane)
    for w in ("settings_mcp_checkbox", "settings_mcp_port_spinbox", "settings_mcp_token_checkbox",
              "settings_mcp_token_edit", "settings_mcp_token_generate_btn", "settings_mcp_hint_label",
              "settings_mcp_copy_btn",
              "settings_cspect_mcp_checkbox", "settings_cspect_mcp_port_spinbox", "_mcp_retranslate", "_mcp_refresh_hint"):
        check(f"pane writes host.{w}", f"host.{w} =" in pane)
    check("the Copy button puts the COMMAND on the clipboard (never the translated prefix)",
          "QApplication.clipboard().setText(cmd)" in pane)
    cio = _src("zxnu_config_io.py")
    check("restore decodes through mcp_server_enabled / cspect_mcp_enabled",
          "mcp_server_enabled(configuration_dictionary)" in cio and "cspect_mcp_enabled(configuration_dictionary)" in cio
          and "mcp_server_port(configuration_dictionary)" in cio and "cspect_mcp_port(configuration_dictionary)" in cio)
    check("restore defers the start to the event loop", "QTimer.singleShot(0, host._mcp_server_start)" in cio)
    main = _src("zxnu_main.py")
    check("MainWindow builds the MCP ops before the restore",
          main.index("build_mcp_ops(") < main.index("load_configuration_file()\n") and "_mcp_retranslate" in main)
    check("the graceful shutdown stops the server", '"_mcp_server_stop"' in main)
    for shim in ("zx-next-unite.py", "zxnu_app.py"):
        check(f"{shim} branches to the stdio shim before Qt", "--mcp-stdio" in _src(shim))
    emu = _src("zxnu_emulator_ops.py")
    check("launch_cspect carries the -mcp switch", "cspect_mcp_argument(" in emu and "strip_cspect_mcp_tokens(" in emu
          and "def launch_cspect(autostart_file=None, *, mcp=None, mcp_port=None)" in emu)
    check("the launchers hand their process back", "return cspect_proc" in emu and "return mame_proc" in emu)
    check("the launch failure is recorded for the MCP server", "host._last_launch_failure = (emulator, message)" in emu)
    check("_start_esp_emulation exposed", "host._start_esp_emulation = _start_esp_emulation" in emu)
    check("the bridge callables are exposed", "host._re_bridge_callables = {" in _src("zxnu_nextsync_pane.py"))
    py = _src("pyproject.toml")
    check("pyproject: the shim script + both modules",
          'zx-next-unite-mcp = "zxnu_mcp:main"' in py and '"zxnu_mcp",' in py and '"zxnu_mcp_ops",' in py)
    check("run_all registers this suite", "test_mcp_server.py" in _src(os.path.join("tests", "run_all.py")))

    from zxnu_i18n import CATALOGS
    texts = ["Enable MCP server (lets an AI agent such as Claude Code drive ZX Next Unite)",
             "Connect Claude Code with: {command}",
             "Start CSpect with its MCP server (-mcp, CSpect 4.2.0 or later)",
             "MCP server listening on {url}", "MCP server NOT started: {error}", "MCP server stopped.",
             "CSpect starts with its own MCP server on port {port}",
             "MCP server started", "MCP server not started",
             "Port {port} is already in use; the MCP server has not been started.",
             "MCP server (AI agents):", "Copy to clipboard", "Copied to clipboard"]
    for lang in ("es", "pt", "pl", "ru", "cs", "fr"):
        for t in texts:
            tr = CATALOGS[lang].get(t)
            fields = set(re.findall(r"\{(\w+)\}", t))
            check(f"[{lang}] catalogued with its placeholders: {t[:40]}",
                  bool(tr) and tr != t and fields == set(re.findall(r"\{(\w+)\}", tr)))
    help_lines = [l for l in C.INIT_HELP if isinstance(l, str) and "mcp" in l.lower() and len(l) > 40]
    check("Help text documents the MCP server", len(help_lines) >= 3)
    for l in help_lines:
        for lang in ("es", "fr"):
            check(f"[{lang}] Help line catalogued: {l[:30]}", l in CATALOGS[lang] and "http://127.0.0.1:2050/mcp" in CATALOGS[lang][l]
                  if "http://" in l else l in CATALOGS[lang])


def main():
    test_dispatcher()
    test_http()
    test_stdio()
    test_tools()
    test_parse_ls()
    test_wiring()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
