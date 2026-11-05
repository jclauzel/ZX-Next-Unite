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
| Disk image | `image_status`, `image_load`, `image_unload`, `image_recent`, `image_download_nextzxos` (the stock 2/4/8 GB zxnext.uk image; never replaces a zip or an image already in the folder unless `overwrite` is true), `image_ls`, `image_get`, `image_put` (a file or a whole folder), `image_mkdir`, `image_rm` (`recursive` for folders), `image_rename` |
| Emulators | `emulator_list`, `emulator_start` (MAME / CSpect on the loaded image, optionally starting a program file; CSpect optionally with its own MCP server), `emulator_processes`, `emulator_stop` |
| RS232 ESP emulation | `esp_emulation_status`, `esp_emulation_start` (optionally on another port), `esp_emulation_stop` |
| NextSync servers | `nextsync_status`, `nextsync_listener_start` / `_stop` (the Remote Explorer's `-listen` server), `nextsync_set_sync_root`, `nextsync_classic_start` / `_stop` (Jari Komppa's one-shot or continuous sync), `nextsync_http_bridge_start` / `_stop` |
| Online catalogues *(9.7.58)* | `catalog_sources`, `catalog_search`, `catalog_latest`, `catalog_random` (GetIt, ZXDB, zxArt, your itch.io library, or `unite` = the Unite! global search), `catalog_detail`, `catalog_download`, `catalog_send_to_image`, `catalog_send_to_next` (download, unpack, then the same verified put as `image_put` / `next_put`) |
| The seated Nexts | `next_sessions`, `next_select`, `next_version`, `next_drives`, `next_free`, `next_ls`, `next_get`, `next_put` (verified by the CRC-32 the Next computes, or a read-back on an older listener), `next_mkdir`, `next_rmdir`, `next_rm`, `next_rmtree`, `next_rename`, `next_copy`, `next_size`, `next_crc`, `next_exit`, **`next_deploy`** |

A refused operation (no image loaded, no Next seated, hdfmonkey said no, the
bytes on the Next differ) comes back as an `isError` result carrying the
reason, so the agent reads it and tries something else. Every `next_*` tool
takes an optional `session` (a sid from `next_sessions`) to target one seated
Next; without it the active one answers.

`image_download_nextzxos` always saves under the archive's own name
(`cspect-next-2gb.zip`, the image beside it as `cspect-next-2gb.img`). Since
9.7.60 it refuses when the folder already holds either file, before
downloading anything, and its error lists what is there: load that image with
`image_load`, pass another `dest_dir`, or pass `overwrite: true` to replace
both. An image an emulator started by the app is running on is never replaced,
whatever `overwrite` says.

## Getting started: your first session

Five minutes from a cold start to an agent driving the app.

### 1. Start the app from this checkout and switch the server on

```bash
python zx-next-unite.py
```

(or the portable `zx-next-unite.exe`, or the `zx-next-unite` command of a pipx
install). On the **Settings** tab tick **Enable MCP server**. The NextSync
console answers:

```
MCP server listening on http://127.0.0.1:2050/mcp
```

The setting persists, so from now on the server starts with the app.

### 2. Register the server with Claude Code

Press **Copy to clipboard** beside the hint line in Settings and paste it in a
terminal — or type it:

```bash
claude mcp add --transport http zx-next-unite http://127.0.0.1:2050/mcp
```

```
Added HTTP MCP server zx-next-unite with URL: http://127.0.0.1:2050/mcp to local config
```

Check it:

```bash
claude mcp list
```

`zx-next-unite: http://127.0.0.1:2050/mcp (HTTP) - ✓ Connected` is what you
want. `✘ Failed to connect` means the app is not running, the toggle is off, or
the port in the command differs from the one in Settings — re-copy the command.

### 3. Open a fresh Claude Code session in this folder

Claude Code discovers a server's tools when a session **starts** (in a session
that is already open, `/mcp` reconnects). The 54 tools appear as
`mcp__zx-next-unite__<name>`, and Claude picks them from plain requests — you
never type a tool name unless you want to.

### 4. Try these prompts

| You type | What runs |
|---|---|
| *What is ZX Next Unite doing right now?* | `app_status` |
| *Load C:\temp\cspect-next-2gb.img and list /games* | `image_load`, `image_ls` |
| *Put C:\projects\mygame\mygame.nex into /games on the image and start MAME with it* | `image_put`, then `emulator_start` with `file` and `image_dir` |
| *Start CSpect on the image with its own MCP server on port 2051* | `emulator_start emulator=CSpect cspect_mcp=true` |
| *Start the NextSync listener with sync root C:\temp\sync and tell me when a Next connects* | `nextsync_listener_start`, then `next_sessions` |
| *Deploy C:\projects\mygame\mygame.nex to the Next and run it* | `next_deploy` — upload, CRC-32 verify on the Next, leave listen mode; with `autoexec.bas` on the card the Next runs it |
| *Show the last 20 lines of the NextSync console* | `app_log_tail console=nextsync lines=20` |
| *What are the latest ZX Spectrum Next games?* | `catalog_latest source=getit category=game` |
| *Search every catalogue for "knight"* | `catalog_search query=knight` — the Unite! global search |
| *Put the three newest Next games on the card, in /games* | `catalog_latest`, then `catalog_send_to_image` for each |
| *Send the newest Next game to the Next that is connected* | `catalog_latest`, then `catalog_send_to_next` (CRC-verified) |
| *Stop the emulator you started* | `emulator_processes`, `emulator_stop` |

To name a tool directly: *call the app_status tool of zx-next-unite*.

### 5. A build-and-run loop, as one prompt

> Build my game with its build.ps1, then put the .nex on the loaded image under
> /games and start MAME with it; if MAME refuses, tell me why.

Claude runs the build, then `image_put`, then `emulator_start`; on a refusal it
reads the reason from the result (no image loaded, the image held by a running
emulator, the file missing) and tells you. Swap the last step for *deploy it to
the Next and run it* and the same loop lands the build on real hardware through
`next_deploy`.

### 6. What you see in the app meanwhile

Every call is a line on the NextSync console — `MCP: image_put -> ok (0.1s)`,
`MCP: next_ls -> refused (0.0s): no Next is seated on the listener…` — and in
the file log. A refusal always comes back to the agent as readable text, so you
can simply ask Claude what went wrong.

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

**Find software and put it on a Next** *(9.7.58)* — the `catalog_*` tools are the
app's own catalogue tabs: `catalog_latest`, `catalog_search` and
`catalog_random` run the tab's Latest / Search / Random (source `getit` =
software written for the Next, `zxdb`, `zxart`, `itchio` = your own library,
`unite` = the Unite! global search, all of them at once), and the result also
appears in the app. Each entry carries a `source` and an `id`:

1. `catalog_latest source=getit category=game limit=10` — the newest Next games;
2. `catalog_send_to_image source=getit id=… image_dir=/games` — download,
   unpack the zip, put the game's folder on the loaded card; or
   `catalog_send_to_next source=getit id=… remote_dir=/games` — the same to a
   Next seated on the listener, every file verified by the Next's CRC-32.

`catalog_download` only fetches to this PC. GetIt entries always download;
itch.io entries your account owns do too; ZXDB and zxArt can be searched but
not downloaded — the app does not offer their downloads, so neither does an
agent. Only download what you have the right to use.

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
