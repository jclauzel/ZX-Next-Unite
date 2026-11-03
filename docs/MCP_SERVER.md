# The MCP server — driving ZX Next Unite from an AI agent

ZX Next Unite 9.7.57 can host a [Model Context Protocol](https://modelcontextprotocol.io)
server. An MCP client — Claude Code, Claude Desktop, Cursor, any agent that
speaks MCP — then gets **tools** for what the app does, and can chain them
into a workflow: *build the game, put it on the card, boot MAME on it*, or
*push the build to the Next on the desk, verify it, run it*.

Every tool is the same operation a button performs, through the same code,
so an agent can do what you can and nothing more. The server is **off by
default**, binds **127.0.0.1 only**, and can require a bearer token.

## Enabling it

Settings → **Enable MCP server**. The row carries:

* the **port** (2050 by default; 2048 is NextSync's, 2222 the RS232 ESP
  emulation's). A running server is restarted on a new port a moment after
  you stop typing;
* **Require bearer token** + the token field + *Generate* — the HTTP bridge's
  rule: every request must carry `Authorization: Bearer <token>` (or the
  `ZXNEXTUNITE-MCP-TOKEN` header), others get HTTP 401;
* a selectable **hint line** with the one-line Claude Code setup for the
  current settings, and a **Copy to clipboard** button beside it that copies
  the command alone (port and token included), ready to paste in a terminal.

Everything persists in `hdfg.cfg` (`mcp_server`, `mcp_port`,
`mcp_token_enabled`, `mcp_token`); when enabled, the server starts with the
app and the NextSync console says so.

## Connecting a client

**Claude Code** (Streamable HTTP, no shim):

```bash
claude mcp add --transport http zx-next-unite http://127.0.0.1:2050/mcp
```

With a token: append `--header "Authorization: Bearer <token>"` (the hint
line in Settings already includes it).

**Claude Desktop** and other stdio-only clients spawn a small shim that
relays every JSON-RPC line to the running app:

```json
{
  "mcpServers": {
    "zx-next-unite": {
      "command": "zx-next-unite-mcp",
      "args": ["--port", "2050"]
    }
  }
}
```

`zx-next-unite-mcp` is the console script of a pipx / pip install; a source
checkout or the portable build uses `python zx-next-unite.py --mcp-stdio`
/ `zx-next-unite.exe --mcp-stdio`. The shim takes `--url`, `--port`,
`--token`, `--timeout`, or the environment variables `ZX_NEXT_UNITE_MCP_URL`,
`ZX_NEXT_UNITE_MCP_PORT`, `ZX_NEXT_UNITE_MCP_TOKEN`. When the app is not
running (or its server is off) every request is answered with a JSON-RPC
error saying so, rather than a dead pipe.

Any other client: `POST` JSON-RPC 2.0 to `/mcp`; `GET /` prints a help line
with the tool names.

## The tools

| Family | Tools |
|---|---|
| App | `app_status` (everything at a glance), `app_log_tail` (the SD Card console, the NextSync console or the file log) |
| Disk image | `image_status`, `image_load`, `image_unload`, `image_recent`, `image_download_nextzxos` (the stock 2/4/8 GB zxnext.uk image), `image_ls`, `image_get`, `image_put` (a file or a whole folder), `image_mkdir`, `image_rm` (`recursive` for folders), `image_rename` |
| Emulators | `emulator_list`, `emulator_start` (MAME / CSpect on the loaded image, optionally starting a program file; CSpect optionally with its own MCP server), `emulator_processes`, `emulator_stop` |
| RS232 ESP emulation | `esp_emulation_status`, `esp_emulation_start` (optionally on another port), `esp_emulation_stop` |
| NextSync servers | `nextsync_status`, `nextsync_listener_start` / `_stop` (the Remote Explorer's `-listen` server), `nextsync_set_sync_root`, `nextsync_classic_start` / `_stop` (Jari Komppa's one-shot or continuous sync), `nextsync_http_bridge_start` / `_stop` |
| The seated Nexts | `next_sessions`, `next_select`, `next_version`, `next_drives`, `next_free`, `next_ls`, `next_get`, `next_put` (verified by the CRC-32 the Next computes, or a read-back on an older listener), `next_mkdir`, `next_rmdir`, `next_rm`, `next_rmtree`, `next_rename`, `next_copy`, `next_size`, `next_crc`, `next_exit`, **`next_deploy`** |

A refused operation (no image loaded, no Next seated, hdfmonkey said no, the
bytes on the Next differ) comes back as an `isError` result carrying the
reason, so the agent reads it and tries something else. Every `next_*` tool
takes an optional `session` (a sid from `next_sessions`) to target one seated
Next; without it the active one answers.

## Two workflows

**Deploy to the Next on the desk** — the `extra/Send-ToNext.ps1` loop in one
call. Install `extra/autoexec.bas` in `/nextzxos` on the card once (it boots
the Next into `.sync5 -L` or ZX Next Remote's Listener and runs whatever lands
at `/home/incoming.nex`). Then:

1. `nextsync_listener_start` (optionally `sync_root=…`) — the Next dials in;
   `next_sessions` shows it;
2. `next_deploy local_path=build/game.nex` — upload, CRC-32 verify **on the
   Next**, then leave listen mode; the loop soft-resets and `.nexload`s it.
   A build that does not verify is never run.

**Deploy to an emulator** — `image_load` (or `image_download_nextzxos`), then
`emulator_start emulator=MAME file=build/game.nex image_dir=/home`: the file
is put into the image, MAME boots the card and NextZXOS is typed the
`.nexload` command (9.7.55). For CSpect: `emulator_start emulator=CSpect
file=build/game.nex`.

## CSpect's own MCP server

CSpect 4.2.0 and later carries an MCP server behind `-mcp[=port]`
(see its `ReadMe.txt`; in 4.5.0 it is a Patreon-build feature). Settings →
**Start CSpect with its MCP server** makes every CSpect launch add
`-mcp=<port>` (2051 by default, next to this app's 2050), and `emulator_start`
accepts `cspect_mcp` / `cspect_mcp_port` to override it for one launch. The
answer and `emulator_processes` name the port, so an agent can register
CSpect's server as a second MCP server and drive the emulator itself
(breakpoints, memory, keys) while driving the app through this one — two
servers, two ports, at the same time.

## Security

* Loopback only; never reachable from another machine.
* An `Origin` header that is not loopback is refused (403): a web page in your
  browser cannot reach the server.
* The optional bearer token is compared in constant time; the token is stored
  in `hdfg.cfg` in plain text like the HTTP bridge's.
* What an agent can do is what *you* can do from the app on this PC: it reads
  and writes local files you name, writes into the loaded image, and moves
  files to and from the Nexts seated on the listener. `image_rm`, `next_rm`
  and `next_rmtree` ask no confirmation.

## Under the hood

`zxnu_mcp.py` is stdlib-only (no `mcp` package, no Qt): the JSON-RPC
dispatcher, the Streamable HTTP transport (`POST /mcp`, JSON answers, 202 for
notifications, 405 on `GET`), the stdio shim and the tool definitions over a
documented host protocol. `zxnu_mcp_ops.py` is the app side: the server
lifecycle, and the host adapter that marshals every Qt touch onto the UI
thread, runs hdfmonkey on the server's own thread, and drives the seated
Nexts through the same silent `BridgeReply` commands the HTTP bridge uses.
`tests/test_mcp_server.py` covers all of it headlessly.
