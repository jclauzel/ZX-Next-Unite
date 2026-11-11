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
import zipfile
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

    def image_download(self, size, dest_dir, load, overwrite):
        self.calls.append(("image_download", size, dest_dir, load, overwrite))
        return {"ok": True, "zip": "x.zip", "image": "x.img", "loaded": load}

    def image_create(self, path, size, load, overwrite):
        self.calls.append(("image_create", path, size, load, overwrite))
        return {"ok": True, "image": path, "version": "26.11a",
                "distribution": "sn-nosrc-26.11a.zip", "size": size, "loaded": load}

    def image_mame_check(self, path):
        self.calls.append(("image_mame_check", path))
        return {"path": path or self.image, "verdict": "fat32-small", "mame_bootable": False}

    def image_rebuild(self, source, target, size, load, overwrite):
        self.calls.append(("image_rebuild", source, target, size, load, overwrite))
        return {"ok": True, "source": source or self.image, "target": target,
                "files": 3, "size": size, "loaded": load}

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

    # ---- the online catalogues (9.7.58). Entries are shaped like the
    # app's own: private keys and all, tagged with their source.
    CATALOG = {
        "getit": [
            {"id": "52841c", "title": "Nemesis", "author": "Paul", "size": "61KB",
             "category": "Game"},
            {"id": "570b3c", "title": "Knight Lore Next", "author": "WhizzBang",
             "size": "40KB", "category": "Game"},
            {"id": "aa11bb", "title": "NextDAW", "author": "Gari", "size": "300KB",
             "category": "Utility"},
        ],
        "zxdb": [
            {"id": "0002259", "title": "Jet Set Willy", "year": 1984, "author": "M. Smith",
             "machine": "ZX-Spectrum 48K", "genre": "Arcade Game: Platform",
             "score": 8.6, "_kind": "game"},
        ],
        "zxart": [
            {"id": 9001, "title": "Some Demo", "year": "2024", "author": "A Group",
             "machine": "ZX Spectrum Next", "genre": "Demo", "_kind": "zxart_prod",
             "_source": {"a whole": "server payload " * 200}},
        ],
        "itchio": [
            {"id": 777, "title": "My Next Game", "url": "https://me.itch.io/my-next-game",
             "author": "me", "short_text": "a game", "classification": "game",
             "_fav_source": "itchio", "source": "itchio"},
        ],
    }
    URLS = {"zxdb": "https://zxinfo.dk/details/0002259", "zxart": "https://zxart.ee/eng/x/"}

    def catalog_sources(self):
        itch = getattr(self, "itch_connected", True)
        zxdb = "zxdb" not in getattr(self, "catalog_hidden", ())
        return [
            {"source": "unite", "available": True, "reason": "", "search": True,
             "latest": True, "random": True, "download": False},
            {"source": "getit", "available": True, "reason": "", "search": True,
             "latest": True, "random": True, "download": True},
            {"source": "zxdb", "available": zxdb,
             "reason": "" if zxdb else "this tab is hidden in this build of the app",
             "search": zxdb, "latest": zxdb, "random": zxdb, "download": False},
            {"source": "zxart", "available": True, "reason": "", "search": True,
             "latest": True, "random": True, "download": False},
            {"source": "itchio", "available": itch,
             "reason": "" if itch else "not connected", "search": itch,
             "latest": False, "random": False, "download": itch},
        ]

    def catalog_query(self, kind, source, query, timeout):
        self.calls.append(("catalog_query", kind, source, query))
        if getattr(self, "catalog_stuck", False):
            return False, f"the {source} {kind} did not finish within {int(timeout)} s", []
        rows = {r["source"]: r for r in self.catalog_sources()}
        if not rows[source]["available"]:
            raise McpToolError(f"{source}: {rows[source]['reason']}")
        if not rows[source].get(kind):
            raise McpToolError(f"{source} has no '{kind}'")
        names = list(self.CATALOG) if source == "unite" else [source]
        out = []
        for name in names:
            if name == "itchio" and kind != "search":
                continue
            # A catalogue that did not answer, or whose tab is hidden: the
            # Unite! fan-out carries on without it, as the tab does.
            if name in getattr(self, "catalog_missing", ()) or (
                    source == "unite" and not rows[name]["available"]):
                continue
            for e in self.CATALOG[name]:
                if kind == "search" and query.lower() not in e["title"].lower():
                    continue
                row = dict(e, _fav_source=name)
                if name in self.URLS:
                    row["_url"] = self.URLS[name]
                out.append(row)
        return True, "", out

    def catalog_detail(self, source, item_id):
        self.calls.append(("catalog_detail", source, item_id))
        for e in self.CATALOG.get(source, []):
            if str(e["id"]) == str(item_id):
                return {"source": source, "id": str(item_id), "title": e["title"],
                        "download": source in ("getit", "itchio")}
        raise McpToolError(f"{source} has no entry {item_id}")

    def catalog_download(self, source, item_id, dest_dir):
        self.calls.append(("catalog_download", source, item_id, dest_dir))
        if source in ("zxdb", "zxart"):
            raise McpToolError("the app does not offer downloads from "
                               + ("ZXDB" if source == "zxdb" else "zxArt"))
        for e in self.CATALOG.get(source, []):
            if str(e["id"]) == str(item_id):
                root = dest_dir or self.download_root
                folder = os.path.join(root, e["title"])
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, "game.nex")
                with open(path, "wb") as fh:
                    fh.write(b"NEX!" + e["title"].encode())
                if getattr(self, "download_empty", False):
                    return {"source": source, "id": str(item_id), "title": e["title"],
                            "folder": folder, "files": []}
                if getattr(self, "download_zip", None):
                    # What GetIt really serves: the game inside a zip. One
                    # member tries to climb out of the folder.
                    os.remove(path)
                    path = os.path.join(folder, "Game r1.zip")
                    if self.download_zip == "corrupt":
                        with open(path, "wb") as fh:
                            fh.write(b"PK this is not a zip")
                    else:
                        with zipfile.ZipFile(path, "w") as zf:
                            zf.writestr("game.nex", b"NEX!zipped")
                            zf.writestr("docs/readme.txt", b"read me")
                            zf.writestr("../evil.txt", b"outside?")
                return {"source": source, "id": str(item_id), "title": e["title"],
                        "folder": folder, "files": [path]}
        raise McpToolError(f"{source} has no entry {item_id}")


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
        check("image_download_nextzxos passes size/dest/load, and never asks to overwrite by itself",
              not is_err and host.calls[-1] == ("image_download", "4gb", "", True, False))

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
    check("Settings row 'mcp_server' right after the HTTP bridge's three rows",
          rows.index("mcp_server") == rows.index("http_bridge_trace") + 1
          and rows.index("http_bridge_trace") == rows.index("http_bridge") + 2)
    mb = re.search(r"SETTINGS_BASIC_ROWS = frozenset\(\((.*?)\n\)\)", pane, re.S)
    basic = set(ast.literal_eval("(" + mb.group(1) + "\n)"))
    check("the bearer-token rows wait behind Advanced, the rest of both stays Basic (9.7.63)",
          {"http_bridge", "http_bridge_trace", "mcp_server", "mcp_server_hint"} <= basic
          and not {"http_bridge_token", "mcp_server_token"} & basic)
    check("Settings row 'cspect_mcp' right after 'cspect_params'", rows.index("cspect_mcp") == rows.index("cspect_params") + 1)
    for row in ("mcp_server", "cspect_mcp"):
        check(f"{row} placed by name", f'_place("{row}", ' in pane)
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


# =====================================================================
#  the online catalogues (9.7.58)
# =====================================================================
def test_catalog():
    """catalog_*: the tabs' Search / Latest / Random and their downloads.

    The host runs the tab's own handler; what is checked here is the tool
    layer over it - argument rules, the public shape of an entry, the
    filters, and the two "send" tools being the download followed by the
    SAME image_put / next_put the other tools are (CRC verification
    included). The app's adapter is driven live against the real app."""
    print("-- catalogue tools")
    from zxnu_mcp import (CATALOG_MIN_QUERY, CATALOG_SOURCES,
                          catalog_public_entry)
    host, srv = make_server()
    names = [t.name for t in srv.tools]
    for name in ("catalog_sources", "catalog_search", "catalog_latest",
                 "catalog_random", "catalog_detail", "catalog_download",
                 "catalog_send_to_image", "catalog_send_to_next"):
        check(f"{name} exists", name in names)
    check("the five sources, Unite! first (it is the default)",
          CATALOG_SOURCES == ("unite", "getit", "zxdb", "zxart", "itchio"))
    check("the query floor is the tabs' own three characters", CATALOG_MIN_QUERY == 3)

    # ---- the public shape of an entry
    pub = catalog_public_entry(dict(FakeHost.CATALOG["zxart"][0], _fav_source="zxart",
                                    _url="https://zxart.ee/x"))
    check("an entry says where it comes from, its id and its title",
          pub["source"] == "zxart" and pub["id"] == "9001" and pub["title"] == "Some Demo",
          pub)
    check("...its descriptive fields, its kind and its page",
          pub.get("year") == "2024" and pub.get("genre") == "Demo"
          and pub.get("kind") == "zxart_prod" and pub.get("url") == "https://zxart.ee/x", pub)
    check("...and never the server's whole payload or a private key",
          not any(k.startswith("_") for k in pub) and len(json.dumps(pub)) < 400, pub)
    pub = catalog_public_entry(dict(FakeHost.CATALOG["zxdb"][0], _fav_source="zxdb"))
    check("the plain 'game' kind is not repeated on every ZXDB row", "kind" not in pub, pub)
    check("an id is always a string (ZXDB's are zero-padded)", pub["id"] == "0002259")
    check("an empty entry does not raise", catalog_public_entry(None)["title"] == "")

    # ---- sources
    is_err, _, s = result_of(call(srv, "catalog_sources"))
    check("catalog_sources lists every source with what it allows",
          not is_err and [r["source"] for r in s["sources"]] == list(CATALOG_SOURCES)
          and s["sources"][1]["download"] is True and s["sources"][2]["download"] is False, s)

    # ---- latest
    is_err, text, s = result_of(call(srv, "catalog_latest", {"source": "getit"}))
    check("catalog_latest getit", not is_err and s["source"] == "getit"
          and [e["title"] for e in s["entries"]] == ["Nemesis", "Knight Lore Next", "NextDAW"]
          and s["found"] == 3 and s["matching"] == 3, text)
    check("...ran the tab's Latest for that source",
          host.calls[-1] == ("catalog_query", "latest", "getit", ""))
    is_err, _, s = result_of(call(srv, "catalog_latest",
                                  {"source": "getit", "category": "GAME", "limit": 1}))
    check("category filters (any capitalisation), limit caps",
          not is_err and s["found"] == 3 and s["matching"] == 2
          and [e["title"] for e in s["entries"]] == ["Nemesis"] and s["category"] == "game", s)
    is_err, _, s = result_of(call(srv, "catalog_latest"))
    check("no source = Unite!: every catalogue at once, each entry tagged",
          not is_err and s["source"] == "unite"
          and {e["source"] for e in s["entries"]} == {"getit", "zxdb", "zxart"}
          and host.calls[-1][2] == "unite", s)
    check("...the genre counts for the category filter too",
          result_of(call(srv, "catalog_latest", {"category": "platform"}))[2]["entries"][0]["title"]
          == "Jet Set Willy")
    check("by_source counts every source that answered, before any filter",
          s["by_source"] == {"getit": 3, "zxdb": 1, "zxart": 1}, s.get("by_source"))
    is_err, _, s = result_of(call(srv, "catalog_latest", {"category": "game"}))
    check("...a filter narrows the entries, not that count",
          not is_err and s["by_source"] == {"getit": 3, "zxdb": 1, "zxart": 1}
          and s["matching"] < s["found"] == 5, s)
    host.catalog_missing = {"zxart"}
    is_err, _, s = result_of(call(srv, "catalog_latest"))
    check("a catalogue that did not answer: the others still do, and it is "
          "visibly absent from by_source",
          not is_err and s["by_source"] == {"getit": 3, "zxdb": 1} and s["found"] == 4, s)
    host.catalog_missing = set()
    host.catalog_hidden = {"zxdb"}
    is_err, text, _ = result_of(call(srv, "catalog_latest", {"source": "zxdb"}))
    check("a hidden catalogue is refused with the reason, not queried",
          is_err and "hidden" in text, text)
    is_err, _, s = result_of(call(srv, "catalog_latest"))
    check("...and the Unite! view simply goes on without it",
          not is_err and s["by_source"] == {"getit": 3, "zxart": 1}, s)
    is_err, _, s = result_of(call(srv, "catalog_sources"))
    check("...catalog_sources says so",
          not is_err and s["sources"][2]["available"] is False
          and "hidden" in s["sources"][2]["reason"], s["sources"][2])
    host.catalog_hidden = set()
    is_err, text, _ = result_of(call(srv, "catalog_latest", {"source": "itchio"}))
    check("itch.io has no 'latest': said, not invented",
          is_err and "no 'latest'" in text, text)
    is_err, text, _ = result_of(call(srv, "catalog_latest", {"source": "nope"}))
    check("an unknown source is refused", is_err, text)
    is_err, text, _ = result_of(call(srv, "catalog_latest", {"limit": 0}))
    check("limit must be at least 1", is_err, text)

    # ---- search
    is_err, text, _ = result_of(call(srv, "catalog_search", {"query": "kn"}))
    check("a query under three characters is refused before anything runs",
          is_err and "3 characters" in text
          and host.calls[-1][0:2] != ("catalog_query", "search"), text)
    is_err, text, _ = result_of(call(srv, "catalog_search", {}))
    check("catalog_search needs a query", is_err, text)
    is_err, _, s = result_of(call(srv, "catalog_search", {"query": "  knight "}))
    check("catalog_search: the Unite! global search, the query trimmed",
          not is_err and s["query"] == "knight" and s["source"] == "unite"
          and [e["title"] for e in s["entries"]] == ["Knight Lore Next"]
          and host.calls[-1] == ("catalog_query", "search", "unite", "knight"), s)
    is_err, _, s = result_of(call(srv, "catalog_search", {"query": "next", "source": "itchio"}))
    check("itch.io can be searched (your library)",
          not is_err and s["entries"][0]["source"] == "itchio"
          and s["entries"][0]["url"] == "https://me.itch.io/my-next-game", s)
    host.itch_connected = False
    is_err, text, _ = result_of(call(srv, "catalog_search", {"query": "next", "source": "itchio"}))
    check("itch.io not connected: the reason comes back as the error",
          is_err and "not connected" in text, text)
    host.itch_connected = True
    host.catalog_stuck = True
    is_err, text, _ = result_of(call(srv, "catalog_random", {"source": "zxdb"}))
    check("a search that never reports back is an error, not an empty list",
          is_err and "did not finish" in text, text)
    host.catalog_stuck = False
    is_err, _, s = result_of(call(srv, "catalog_random", {"source": "zxdb"}))
    check("catalog_random", not is_err and s["entries"][0]["url"].startswith("https://zxinfo"), s)

    # ---- detail
    is_err, _, s = result_of(call(srv, "catalog_detail", {"source": "getit", "id": "570b3c"}))
    check("catalog_detail", not is_err and s["title"] == "Knight Lore Next", s)
    is_err, text, _ = result_of(call(srv, "catalog_detail", {"source": "unite", "id": "x"}))
    check("'unite' is a view, not a source an entry belongs to", is_err, text)
    is_err, text, _ = result_of(call(srv, "catalog_detail", {"source": "getit", "id": " "}))
    check("an id is required", is_err and "id is required" in text, text)

    # ---- download and the two sends
    with tempfile.TemporaryDirectory() as tmp:
        host.download_root = os.path.join(tmp, "downloads")
        is_err, text, s = result_of(call(srv, "catalog_download",
                                         {"source": "getit", "id": "570b3c"}))
        check("catalog_download returns the folder and the files",
              not is_err and s["ok"] and os.path.isfile(s["files"][0])
              and os.path.basename(s["folder"]) == "Knight Lore Next", text)
        other = os.path.join(tmp, "elsewhere")
        is_err, _, s = result_of(call(srv, "catalog_download",
                                      {"source": "getit", "id": "570b3c", "dest_dir": other}))
        check("...under dest_dir when one is given",
              not is_err and s["folder"].startswith(other), s)
        for source, label in (("zxdb", "ZXDB"), ("zxart", "zxArt")):
            is_err, text, _ = result_of(call(srv, "catalog_download",
                                             {"source": source, "id": "1"}))
            check(f"{label} downloads are refused, as in the app",
                  is_err and "does not offer downloads" in text, text)
        host.download_empty = True
        is_err, text, _ = result_of(call(srv, "catalog_download",
                                         {"source": "getit", "id": "570b3c"}))
        check("a download that produced no file is an error",
              is_err and "nothing was downloaded" in text, text)
        host.download_empty = False

        # GetIt serves most games zipped, and a Next cannot run a zip: by
        # default the archive is unpacked into the entry's folder.
        from zxnu_mcp import unpack_downloaded_archives
        host.download_zip = "good"
        zroot = os.path.join(tmp, "zipped")
        is_err, text, s = result_of(call(srv, "catalog_download",
                                         {"source": "getit", "id": "52841c", "dest_dir": zroot}))
        got = sorted(os.path.relpath(f, s["folder"]).replace("\\", "/") for f in s["files"])
        check("a downloaded zip is unpacked into the entry's folder",
              not is_err and got == ["docs/readme.txt", "evil.txt", "game.nex"], got)
        check("...the archive itself is gone (nothing is sent twice)",
              not os.path.exists(os.path.join(s["folder"], "Game r1.zip")))
        check("...and a member that tried to climb out stayed inside",
              not os.path.exists(os.path.join(zroot, "evil.txt"))
              and os.path.isfile(os.path.join(s["folder"], "evil.txt")))
        is_err, _, s = result_of(call(srv, "catalog_download",
                                      {"source": "getit", "id": "52841c", "dest_dir": zroot,
                                       "unzip": False}))
        check("unzip=false keeps the archive, as the tab's Send does",
              not is_err and [os.path.basename(f) for f in s["files"]] == ["Game r1.zip"]
              and os.path.isfile(s["files"][0]), s)
        host.download_zip = "corrupt"
        is_err, _, s = result_of(call(srv, "catalog_download",
                                      {"source": "getit", "id": "52841c", "dest_dir": zroot}))
        check("an archive that cannot be read is kept, with a note saying why",
              not is_err and [os.path.basename(f) for f in s["files"]] == ["Game r1.zip"]
              and "left as an archive" in s["notes"][0], s)
        host.download_zip = "good"
        big = os.path.join(tmp, "big")
        os.makedirs(big)
        bigzip = os.path.join(big, "many.zip")
        with zipfile.ZipFile(bigzip, "w") as zf:
            for i in range(6):
                zf.writestr(f"f{i}.bin", b"x" * 100)
        files, notes = unpack_downloaded_archives(big, [bigzip], max_files=5)
        check("an archive with too many files is not unpacked",
              files == [bigzip] and "6 files" in notes[0] and os.listdir(big) == ["many.zip"],
              notes)
        files, notes = unpack_downloaded_archives(big, [bigzip], max_bytes=599)
        check("...nor one that would unpack too large",
              files == [bigzip] and "more than" in notes[0], notes)
        files, notes = unpack_downloaded_archives(big, [bigzip, os.path.join(big, "nope.zip")])
        check("within the limits it unpacks; a path that is not there is passed through",
              len(files) == 7 and notes == [] and not os.path.exists(bigzip), files)

        # into the image: catalog_download + the SAME image_put
        host.download_zip = None
        is_err, text, _ = result_of(call(srv, "catalog_send_to_image",
                                         {"source": "getit", "id": "570b3c"}))
        check("catalog_send_to_image needs a loaded image (image_put's own refusal)",
              is_err, text)
        img = os.path.join(tmp, "card.img")
        with open(img, "wb") as fh:
            fh.write(b"x")
        result_of(call(srv, "image_load", {"path": img}))
        is_err, text, s = result_of(call(srv, "catalog_send_to_image",
                                         {"source": "getit", "id": "570b3c"}))
        check("catalog_send_to_image puts the entry's folder under /games",
              not is_err and s["image_folder"] == "/games/Knight Lore Next"
              and s["sent"] == ["/games/Knight Lore Next/game.nex"] and s["files"] == 1, text)
        check("...the bytes are on the card",
              host._node("/games/Knight Lore Next/game.nex") == b"NEX!Knight Lore Next",
              repr(host._node("/games")))
        is_err, _, s = result_of(call(srv, "catalog_send_to_image",
                                      {"source": "getit", "id": "52841c", "image_dir": "/demos"}))
        check("...or under another image_dir",
              not is_err and s["image_folder"] == "/demos/Nemesis", s)

        # to a Next: catalog_download + the SAME verified next_put
        for label, roster in (("no listener", None), ("a listener but no Next", (None, [], 4))):
            host.roster = roster
            before = len(host.calls)
            is_err, text, _ = result_of(call(srv, "catalog_send_to_next",
                                             {"source": "getit", "id": "570b3c"}))
            check(f"catalog_send_to_next with {label} says what to do",
                  is_err and "no Next is seated" in text, text)
            check("...before downloading anything",
                  not any(c[0] == "catalog_download" for c in host.calls[before:]),
                  host.calls[before:])
        host.roster = (1, [(1, "10.0.0.9", "next")], 4)
        host.connected = True
        host.ndirs = {"/", "/home"}
        host.nfs = {}
        is_err, text, s = result_of(call(srv, "catalog_send_to_next",
                                         {"source": "getit", "id": "570b3c"}))
        check("catalog_send_to_next uploads the entry's folder under /games",
              not is_err and s["remote_folder"] == "/games/Knight Lore Next"
              and s["files"] == 1, text)
        check("...created the folders on the Next",
              "/games" in host.ndirs and "/games/Knight Lore Next" in host.ndirs,
              sorted(host.ndirs))
        check("...and verified the file by the Next's own CRC-32",
              s["sent"][0]["verified"] is True and s["sent"][0]["method"] == "crc"
              and host.nfs.get("/games/Knight Lore Next/game.nex") == b"NEX!Knight Lore Next",
              s["sent"])
        host.put_corrupt = True
        is_err, text, _ = result_of(call(srv, "catalog_send_to_next",
                                         {"source": "getit", "id": "52841c",
                                          "remote_dir": "/home"}))
        check("a file that lands damaged is an error here too", is_err, text)
        host.put_corrupt = False


def test_emulator_label():
    """emulator_processes names the emulator the LAUNCHER said it started.

    Found 2026-11-05 through the server itself: a MAME started on a freshly
    downloaded stock image was listed as CSpect. The label was guessed from
    the whole command line, "cspect" first - and the stock NextZXOS images
    are named cspect-next-*.img, so a MAME booting one has the word in its
    own arguments. The fake host above answers `processes` itself, so the
    adapter's real rule had no test at all."""
    print("-- emulator_processes: which emulator a process is")
    from zxnu_mcp_ops import _emulator_label

    class Proc:
        def __init__(self, args, stamp=None):
            self.args = args
            if stamp is not None:
                self.zxnu_emulator = stamp

    mame_argv = [r"C:\zxnu\downloads\mame\mame.exe", "specnext_ks2", "-window",
                 "-hard1", r"C:\temp\cspect-next-4gb.img",
                 "-rs232_esp", "null_modem", "-bitb", "socket.127.0.0.1:2220"]
    cspect_cmd = r'"C:\zxnu\cspect\CSpect.exe" -zxnext -mmc=C:\mame\next.img'
    check("the reported launch: MAME on cspect-next-4gb.img is MAME",
          _emulator_label(Proc(mame_argv, "MAME")) == "MAME")
    check("a CSpect whose card sits in a mame folder is CSpect",
          _emulator_label(Proc(cspect_cmd, "CSpect")) == "CSpect")
    check("a process nobody stamped is unknown, never guessed from its arguments",
          _emulator_label(Proc(mame_argv)) == "emulator"
          and _emulator_label(Proc(cspect_cmd)) == "emulator"
          and _emulator_label(object()) == "emulator")
    check("a stamp that is not a name is not passed on",
          _emulator_label(Proc(mame_argv, "")) == "emulator"
          and _emulator_label(Proc(mame_argv, 7)) == "emulator"
          and _emulator_label(Proc(mame_argv, None)) == "emulator")

    # Both launchers stamp the process, and do it before the process joins
    # the record emulator_processes reads.
    emu = _src("zxnu_emulator_ops.py")
    stamps = {}
    for var in ("mame_proc", "cspect_proc"):
        found = re.findall(r'\b%s\.zxnu_emulator = "([^"]+)"' % var, emu)
        stamps[var] = found
        stamp_at = emu.find("%s.zxnu_emulator = " % var)
        record_at = emu.find("_images_held_by_us.setdefault(_held_key, []).append(%s)" % var)
        check(f"{var} is stamped once, before it is recorded as holding the image",
              len(found) == 1 and 0 <= stamp_at < record_at, f"{found} {stamp_at} {record_at}")
    _, srv = make_server()
    tools = {t["name"]: t for t in srv.handle_message(rpc("tools/list"))["result"]["tools"]}
    names = tools["emulator_start"]["inputSchema"]["properties"]["emulator"]["enum"]
    check("the stamps are the names emulator_start takes",
          stamps["mame_proc"] + stamps["cspect_proc"] == names == ["MAME", "CSpect"],
          f"{stamps} {names}")


class _FakeDownload:
    """What urlopen answers in test_image_download_guard: ``body`` in the
    64 KB reads download_nextzxos_archive makes, optionally dying after
    ``fail_after`` of them (a link that drops mid-download)."""

    def __init__(self, body, fail_after=None):
        self.body = io.BytesIO(body)
        self.size = len(body)
        self.fail_after = fail_after
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getheader(self, name):
        return str(self.size) if name == "Content-Length" else None

    def read(self, n):
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise ConnectionResetError("the link dropped")
        self.reads += 1
        return self.body.read(n)


def test_image_download_guard():
    """image_download_nextzxos never replaces a file unless told to.

    Noticed 2026-11-05 while using the server: the download is always saved
    under the archive's own name (cspect-next-2gb.zip, the image extracted
    next to it as cspect-next-2gb.img), both opened for writing, so asking for
    an image in a folder that already held one of that size would have written
    over it - with everything put on that card since. The wizard's save dialog
    asks its user; an agent's call asked nobody."""
    print("-- image_download_nextzxos: an existing zip or image is never replaced")
    import zxnu_sdcard_ops as sd
    from zxnu_mcp_ops import nextzxos_overwrite_refusal

    # ---- the tool: overwrite is off unless a JSON true says otherwise
    host, srv = make_server()
    tools = {t["name"]: t for t in srv.handle_message(rpc("tools/list"))["result"]["tools"]}
    spec = tools["image_download_nextzxos"]["inputSchema"]["properties"].get("overwrite") or {}
    check("the tool declares overwrite, a boolean that defaults to false",
          spec.get("type") == "boolean" and spec.get("default") is False, spec)
    check("...and its description says an existing file is never replaced",
          "never replaced" in tools["image_download_nextzxos"]["description"])
    is_err, _, _ = result_of(call(srv, "image_download_nextzxos", {"overwrite": True}))
    check("overwrite=true reaches the host", not is_err
          and host.calls[-1] == ("image_download", "2gb", "", True, True), host.calls[-1])
    is_err, _, _ = result_of(call(srv, "image_download_nextzxos", {"overwrite": None}))
    check("a null overwrite is not consent", not is_err
          and host.calls[-1] == ("image_download", "2gb", "", True, False), host.calls[-1])
    before = len(host.calls)
    for bad in ("true", 1, "yes"):
        reply = call(srv, "image_download_nextzxos", {"overwrite": bad})
        refused = "error" in reply or result_of(reply)[0]
        check(f"overwrite={bad!r} is refused, not read as true",
              refused and len(host.calls) == before, reply)

    with tempfile.TemporaryDirectory() as tmp:
        # ---- which names a download writes, and which are taken
        zip_path = os.path.join(tmp, "cspect-next-2gb.zip")
        img_path = os.path.join(tmp, "cspect-next-2gb.img")
        hdf_path = os.path.join(tmp, "cspect-next-2gb.hdf")
        check("the image is named after the archive, extension lower-cased",
              sd.nextzxos_image_target(zip_path, ".IMG") == img_path
              and sd.nextzxos_image_target(zip_path, ".hdf") == hdf_path)
        check("an empty folder: nothing is taken", sd.nextzxos_download_occupied(zip_path) == [])
        check("a folder that does not exist yet: nothing is taken",
              sd.nextzxos_download_occupied(os.path.join(tmp, "new", "cspect-next-2gb.zip")) == [])
        with open(img_path, "wb") as fh:
            fh.write(b"MY WORK")
        check("an image of that name is taken, the archive being absent",
              sd.nextzxos_download_occupied(zip_path) == [img_path])
        check("...and another size's names are free beside it",
              sd.nextzxos_download_occupied(os.path.join(tmp, "cspect-next-4gb.zip")) == [])
        with open(zip_path, "wb") as fh:
            fh.write(b"OLD ZIP")
        check("archive and image: both, in writing order",
              sd.nextzxos_download_occupied(zip_path) == [zip_path, img_path])
        os.remove(img_path)
        os.mkdir(hdf_path)
        check("whatever holds the name counts (.hdf, and a folder at that)",
              sd.nextzxos_download_occupied(zip_path) == [zip_path, hdf_path])
        os.rmdir(hdf_path)
        os.remove(zip_path)

        # ---- the refusal an agent reads
        with open(zip_path, "wb") as fh:
            fh.write(b"z" * 10)
        with open(img_path, "wb") as fh:
            fh.write(b"i" * (3 * 1048576))
        text = nextzxos_overwrite_refusal([zip_path, img_path])
        check("the refusal names both files with their sizes, and that nothing was fetched",
              "nothing was downloaded" in text and "cspect-next-2gb.zip (10 bytes" in text
              and "cspect-next-2gb.img (3 MB" in text and tmp in text, text)
        check("...and the three ways on: load what is there, another folder, overwrite",
              f"image_load {img_path}" in text and "another dest_dir" in text
              and "overwrite=true to replace them" in text, text)
        text = nextzxos_overwrite_refusal([zip_path])
        check("an archive alone: no image to load is offered, and it is 'it'",
              "image_load" not in text and "overwrite=true to replace it" in text, text)
        check("a file that vanished meanwhile is still named",
              "gone.img" in nextzxos_overwrite_refusal([os.path.join(tmp, "gone.img")]))
        os.remove(zip_path)
        os.remove(img_path)

        # ---- the download itself: create or fail
        real_urlopen = sd.urllib.request.urlopen
        answers = []
        sd.urllib.request.urlopen = lambda request: answers.pop(0)
        try:
            body = b"A" * 200000
            answers.append(_FakeDownload(body))
            sd.download_nextzxos_archive("http://x/cspect-next-2gb.zip", zip_path, overwrite=False)
            with open(zip_path, "rb") as fh:
                check("overwrite=False into a free name downloads the file", fh.read() == body)
            answers.append(_FakeDownload(b"B" * 200000))
            try:
                sd.download_nextzxos_archive("http://x/cspect-next-2gb.zip", zip_path, overwrite=False)
                raised = None
            except FileExistsError as ex:
                raised = ex
            with open(zip_path, "rb") as fh:
                check("overwrite=False onto an existing file raises and leaves every byte",
                      raised is not None and fh.read() == body, raised)
            answers.append(_FakeDownload(b"C" * 200000))
            sd.download_nextzxos_archive("http://x/cspect-next-2gb.zip", zip_path)
            with open(zip_path, "rb") as fh:
                check("the wizard's default still replaces the file its dialog asked about",
                      fh.read() == b"C" * 200000)
            os.remove(zip_path)
            answers.append(_FakeDownload(b"D" * 200000, fail_after=2))
            try:
                sd.download_nextzxos_archive("http://x/cspect-next-2gb.zip", zip_path, overwrite=False)
                raised = None
            except ConnectionResetError as ex:
                raised = ex
            check("overwrite=False: a download that dies removes its own partial file",
                  raised is not None and not os.path.exists(zip_path), raised)
            answers.append(_FakeDownload(b"E" * 200000, fail_after=2))
            try:
                sd.download_nextzxos_archive("http://x/cspect-next-2gb.zip", zip_path)
                raised = None
            except ConnectionResetError as ex:
                raised = ex
            check("the wizard's default keeps its contract: the partial file is left",
                  raised is not None and os.path.getsize(zip_path) == 2 * 65536, raised)
            os.remove(zip_path)
        finally:
            sd.urllib.request.urlopen = real_urlopen

        # ---- the extraction: create or fail
        image = bytes(range(256)) * 4096                       # 1 MB
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("2gb/cspect-next-2gb.IMG", image)
        with open(img_path, "wb") as fh:
            fh.write(b"MY WORK")
        try:
            sd.extract_nextzxos_image(zip_path, overwrite=False)
            raised = None
        except FileExistsError as ex:
            raised = ex
        with open(img_path, "rb") as fh:
            check("overwrite=False onto an existing image raises and leaves every byte",
                  raised is not None and fh.read() == b"MY WORK", raised)
        check("...and the error names that image",
              raised is not None and os.path.normcase(str(raised.filename)) == os.path.normcase(img_path),
              raised and raised.filename)
        os.remove(img_path)
        got = sd.extract_nextzxos_image(zip_path, overwrite=False)
        with open(img_path, "rb") as fh:
            check("overwrite=False into a free name extracts the image",
                  got == img_path and fh.read() == image, got)
        with open(img_path, "wb") as fh:
            fh.write(b"MY WORK")
        got = sd.extract_nextzxos_image(zip_path)
        with open(img_path, "rb") as fh:
            check("the wizard's default still replaces the image named after its archive",
                  got == img_path and fh.read() == image)
        os.remove(img_path)

        def dies():
            raise OSError("the disk filled up")
        try:
            sd.extract_nextzxos_image(zip_path, pump=dies, overwrite=False)
            raised = None
        except OSError as ex:
            raised = ex
        check("overwrite=False: an extraction that dies removes its own partial image",
              raised is not None and not os.path.exists(img_path), raised)
        with open(img_path, "wb") as fh:
            fh.write(b"MY WORK")
        for overwrite in (False, True):
            try:
                sd.extract_nextzxos_image(zip_path, refuse=lambda target: "an emulator holds it",
                                          overwrite=overwrite)
                raised = None
            except RuntimeError as ex:
                raised = ex
            with open(img_path, "rb") as fh:
                check(f"overwrite={overwrite}: the emulator gate still refuses first, image untouched",
                      raised is not None and "emulator holds it" in str(raised)
                      and fh.read() == b"MY WORK", raised)

    # ---- the adapter: asks before it fetches, and passes the answer down
    ops = _src("zxnu_mcp_ops.py")
    start = ops.find("def image_download(self, size, dest_dir, load, overwrite=False):")
    body = ops[start:ops.find("def hdfmonkey(self", start)]
    asked = body.find("nextzxos_download_occupied(save_path)")
    fetched = body.find("download_nextzxos_archive(url, save_path, overwrite=overwrite)")
    made = body.find("os.makedirs(dest")
    check("the adapter's overwrite defaults to False", start >= 0)
    check("it looks for existing files before it creates the folder or fetches a byte",
          0 <= asked < made < fetched, f"{asked} {made} {fetched}")
    check("the check is skipped only for overwrite",
          "if not overwrite:" in body[:asked] and "raise McpToolError(nextzxos_overwrite_refusal(taken))" in body)
    check("both the download and the extraction are told not to replace",
          fetched >= 0 and "extract_nextzxos_image(save_path, refuse=blocker," in body
          and body.count("overwrite=overwrite") == 2, body.count("overwrite=overwrite"))
    wizard = _src("zxnu_sdcard_ops.py")
    check("neither file is opened for plain writing any more: the mode follows overwrite",
          wizard.count('"wb" if overwrite else "xb"') == 2
          and 'open(save_path, "wb")' not in wizard and 'open(target, "wb")' not in wizard)


def test_image_create_tools():
    """image_create_nextzxos / image_mame_check / image_make_mame_bootable
    (9.7.65): the Create NextZXOS Image button and the MAME-bootable rebuild
    as tools - the argument rules (path/target required, the size enum, the
    JSON-true overwrite rule of image_download_nextzxos) and, in the
    adapter's source, the order hdfmonkey -> size -> existing-target refusal
    -> the task."""
    print("-- image_create_nextzxos / image_mame_check / image_make_mame_bootable")
    host, srv = make_server()
    tools = {t["name"]: t for t in srv.handle_message(rpc("tools/list"))["result"]["tools"]}
    check("the three tools are listed",
          {"image_create_nextzxos", "image_mame_check", "image_make_mame_bootable"} <= set(tools))
    create = tools["image_create_nextzxos"]["inputSchema"]
    check("create: path required, size an enum defaulting to 2 GB, overwrite false by default",
          "path" in create.get("required", [])
          and create["properties"]["size"].get("enum") == ["2 GB", "4 GB", "8 GB"]
          and create["properties"]["size"].get("default") == "2 GB"
          and create["properties"]["overwrite"].get("default") is False, create)
    check("...and the descriptions say an existing file is never replaced",
          "never replaced" in tools["image_create_nextzxos"]["description"]
          and "never replaced" in tools["image_make_mame_bootable"]["description"])
    is_err, text, _ = result_of(call(srv, "image_create_nextzxos", {}))
    check("create without a path is refused", is_err and "path" in text, text)
    is_err, _, s = result_of(call(srv, "image_create_nextzxos", {"path": "D:/x.img", "size": "4 GB"}))
    check("create passes path/size/load and never asks to overwrite by itself",
          not is_err and host.calls[-1] == ("image_create", "D:/x.img", "4 GB", True, False)
          and s["version"] == "26.11a", str((host.calls[-1], s)))
    is_err, _, _ = result_of(call(srv, "image_create_nextzxos", {"path": "D:/x.img", "overwrite": None, "load": False}))
    check("a null overwrite is not consent, the size defaults to 2 GB",
          not is_err and host.calls[-1] == ("image_create", "D:/x.img", "2 GB", False, False), host.calls[-1])
    is_err, _, _ = result_of(call(srv, "image_create_nextzxos", {"path": "D:/x.img", "overwrite": True}))
    check("overwrite=true reaches the host", not is_err and host.calls[-1][4] is True)
    before = len(host.calls)
    reply = call(srv, "image_create_nextzxos", {"path": "D:/x.img", "overwrite": "true"})
    check("overwrite='true' is refused, not read as true",
          ("error" in reply or result_of(reply)[0]) and len(host.calls) == before, reply)
    reply = call(srv, "image_create_nextzxos", {"path": "D:/x.img", "size": "1 GB"})
    check("a size outside 2/4/8 GB is refused",
          ("error" in reply or result_of(reply)[0]) and len(host.calls) == before, reply)
    is_err, _, s = result_of(call(srv, "image_mame_check", {}))
    check("mame_check defaults to the loaded image and answers the verdict",
          not is_err and host.calls[-1] == ("image_mame_check", "") and s["verdict"] == "fat32-small", str(s))
    is_err, text, _ = result_of(call(srv, "image_make_mame_bootable", {}))
    check("rebuild without a target is refused", is_err and "target" in text, text)
    is_err, _, s = result_of(call(srv, "image_make_mame_bootable", {"target": "D:/y.img"}))
    check("rebuild passes source (default: loaded)/target/size/load/overwrite",
          not is_err and host.calls[-1] == ("image_rebuild", "", "D:/y.img", "2 GB", False, False)
          and s["files"] == 3, str((host.calls[-1], s)))
    is_err, _, _ = result_of(call(srv, "image_make_mame_bootable", {"target": "D:/y.img", "source": "D:/z.img", "size": "8 GB", "load": True, "overwrite": True}))
    check("...every argument reaches the host",
          not is_err and host.calls[-1] == ("image_rebuild", "D:/z.img", "D:/y.img", "8 GB", True, True), host.calls[-1])
    repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ops = open(os.path.join(repo, "zxnu_mcp_ops.py"), encoding="utf-8").read()
    for fn, task in (("def image_create(self", "create_nextzxos_image_task("),
                     ("def image_rebuild(self", "make_image_mame_bootable_task(")):
        start = ops.find(fn)
        body = ops[start:ops.find("\n        def ", start + 10)]
        a, b, c, d = (body.find("self._hdfmonkey_or_raise()"), body.find("os.path.lexists("),
                      body.find("not overwrite"), body.find(task))
        check(f"{fn[4:-5]}: hdfmonkey checked, an existing target refused unless overwrite, before the task runs",
              0 <= a < b < c < d, f"{a} {b} {c} {d}")


def main():
    test_dispatcher()
    test_http()
    test_stdio()
    test_tools()
    test_catalog()
    test_parse_ls()
    test_wiring()
    test_emulator_label()
    test_image_download_guard()
    test_image_create_tools()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
