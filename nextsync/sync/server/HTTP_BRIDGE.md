# NextSync HTTP bridge — drive a remote Next's file system over HTTP

The HTTP bridge is a small self-hosted web server — built on
[Flask](https://flask.palletsprojects.com/) by the Pallets team
(BSD-3-Clause) — that republishes a NextSync **`.sync5 -listen`** session as
plain HTTP routes:

```
caller (.http on a Next / curl / browser)
        │  HTTP (port 80, no TLS)
        ▼
  HTTP bridge  (inside ZX-Next-Unite, or nextsync5.py -http)
        │  NextSync -listen protocol (TCP 2048)
        ▼
  remote Next running  .sync5 -listen
```

Because the Spectrum Next has a built-in **`.http`** dot command
([remy/next-http](https://github.com/remy/next-http), HTTP only — no TLS, port
80 by default, GET **and** POST), one Next can browse, download from, upload
to, and reorganise the SD card of **another** Next over Wi-Fi — with the
bridge in the middle.

## Enabling the bridge

Flask is **optional**: install it with `python -m pip install flask` (it is
listed in `REQUIREMENTS.txt`). Neither host ever errors at startup without
it — the app greys the Settings toggle out (with an install hint in its
tooltip), and `nextsync5.py -w` prints *"please install flask first
(currently disabled)"*.

* **ZX-Next-Unite app** — Settings tab → *"Enable NextSync HTTP bridge (web
  server for the Next's .http command)"*. Off by default; the choice is saved
  in `hdfg.cfg` (`nextsync_http_bridge`) and the server then starts
  automatically with the app. The port defaults to **80** and is set in the
  **port box next to the toggle** (enabled while the bridge is on); the value
  is persisted to `hdfg.cfg` (`nextsync_http_port`, strict `key=value`, no
  spaces) and re-applied at every start — changing it while the bridge runs
  restarts the server on the new port. The bridge drives the Next connected
  to the **Remote Explorer**'s listen server; start that listen server
  automatically too by launching the app with the
  `-start-remote-explorer-listener` switch, so the whole chain comes up
  with no clicks.
* **Standalone server** — `python nextsync5.py -w` (port 80) or
  `-http=8080` for a custom port. `nextsync5.py` lives at the repo root,
  next to `zxnu_http_bridge.py`. Add `-v` to log every HTTP request, its
  payload and the response on the console for troubleshooting.

Both hosts also cap how many HTTP requests the bridge serves **concurrently**
— **1** by default, which is the recommended value to avoid concurrent
access: the `-listen` session behind the bridge runs one command at a time
anyway, so extra simultaneous requests are simply held until a slot frees
(never rejected). In the app the cap is the **"Max connections" box** next to
the port (persisted as `nextsync_http_connection_limit` in `hdfg.cfg`); for
`nextsync5.py` use `-flask-connection-limit:<n>` (`=` also accepted), e.g.
`-flask-connection-limit:5` to allow five at once.

If something already listens on the chosen port (IIS and Skype love port
80), nothing crashes: the app raises a red toast — *"You have specified to
start the flask integration server but port 80 is already in use, the web
server has not been started."* — and `nextsync5.py` prints the equivalent
console error. Stop the other program or pick another port.

Every route answers **plain text** (easy to show or parse on a Next); append
`&json=1` (or send `Accept: application/json`) for JSON. Failures use real
HTTP status codes: `400` bad arguments, `501` unsupported, `502` the Next
said no, `503` no Next connected, `504` timed out.

Remote paths accept an optional drive prefix (`m:/backup`) exactly like every
other NextSync command. URL-encode special characters (space = `%20`).

---

## Route reference & call samples

The `curl` lines below talk to a bridge running on the **same PC**
(`localhost`); the `.http` lines are what the **calling Next** would run,
using the bridge machine's LAN address instead — `192.168.1.10` in the
samples (`-f` saves the reply to a file, `-b`/`-l` use a memory bank — see
the next-http README).

### `GET /status` — is a Next connected, how many partitions?

```
curl "http://localhost/status"
.http -h 192.168.1.10 -u /status -f status.txt
```
```
listening: yes
connected: yes
current: C
drives: C M
partitions: 2
```
`listening` = the -listen server is running; `connected` = a Next is actually
in `.sync5 -listen`; `partitions` = number of mounted drives.

### `GET /drives` — mounted drive letters

```
curl "http://localhost/drives"
.http -h 192.168.1.10 -u /drives -f drives.txt
```
```
OK
current: C
drives: C M
partitions: 2
```

### `GET /free?drive=C` — free space on a partition

```
curl "http://localhost/free?drive=m"
.http -h 192.168.1.10 -u /free?drive=m -f free.txt
```
```
OK
drive: m
free: 1048576 bytes (1.0 MB)
```
Omit `drive` for the dot's current drive. (Free space is the only storage
metric a dotN can report safely — there is no total-size call.)

### `GET /ls?path=/games` — directory listing

```
curl "http://localhost/ls?path=/games"
.http -h 192.168.1.10 -u /ls?path=/games -f list.txt
```
```
OK 2 entries
D	0	GAMES
F	1234	boot.bas
```
One entry per line: `D`irectory or `F`ile, size in bytes, name (tab-separated).

### `GET /get?path=/games/boot.bas` — download one file (raw bytes)

```
curl -o boot.bas "http://localhost/get?path=/games/boot.bas"
.http -h 192.168.1.10 -u /get?path=/games/boot.bas -f boot.bas
.http get -b 20 -h 192.168.1.10 -u /get?path=/games/scr.bin
```
The response body IS the file. Folders are refused with `400` — list them
with `/ls` and fetch file by file. Add `&b64=1` for a base64-encoded body
(7-bit safe — pair it with `.http`'s `-7` flag under CSpect's stock ESP
emulation, see the emulator note below).

### `POST /put?path=/games/new.tap` — upload (request body = the file)

```
curl --data-binary @new.tap "http://localhost/put?path=/games/new.tap"
.http post -b 22 -l 1024 -h 192.168.1.10 -u /put?path=/games/bank.bin
```
```
OK put /games/new.tap (13456 bytes)
```
A `path` ending in `/` needs `&name=<filename>` (the file's name inside that
folder). On the calling Next, the POST payload always comes from a memory
bank: **`-b 22`** names the bank — a **16K** BASIC bank number, e.g. the one
`BANK NEW b` reserved — and **`-l 1024`** says how many bytes to send from
the start of that bank (16384 at most, the bank's size). So
`post -b 22 -l 1024` means "POST the first 1024 bytes of bank 22". (The
[next-http README](https://github.com/remy/next-http)'s own example ends in
`-u /send` — that is the demo route of *its* test server; against this
bridge the route is `/put?path=…` as above.)

**Chunked upload** — add `&append=1&size=<total bytes>` and POST the file in
pieces: the bridge spools the chunks and, once exactly `size` bytes have
arrived, writes the whole file to the Next in one go. Intermediate chunks
answer `OK append <path> (<got>/<size> bytes)`, the final one
`OK put <path> (<size> bytes, <n> chunks)`. Re-declaring a different `size`
for the same path (or a plain `/put` to it) discards the half-done spool, so
a failed upload is retried simply by starting again. This exists because a
Next's `.http` can POST **at most one 16K bank per request** — see the
NextBASIC sample below.

```
curl --data-binary @part1 "http://localhost/put?path=/big.tap&append=1&size=51200"
OK append /big.tap (16384/51200 bytes)
```

### Uploading a file bigger than a bank (NextBASIC sample)

The bank limit is real: `.http post` takes its payload from **one memory
bank** (`-b`, counted in 16K blocks) with `-l` giving the byte count, and
`-f` (file) only works with `get` — next-http's rolling banks apply to
downloads, not POSTs. So a single POST can carry at most **16 KB**, and a
1 MB file must be sent as chunks. The `append=1` mode above reassembles them
bridge-side.

The ready-to-run sample lives in the repo's **`samples/`** folder:
`httpput.txt` (source), `httpput.bas` (converted with
[txt2bas](https://github.com/remy/txt2bas), autostarts on LOAD), plus two
all-ASCII test payloads with known checksums — `sample-4k.txt` (4096 bytes,
sum16 **20671**, fits one bank/one POST) and `sample-50k.txt` (50000 bytes,
sum16 **64392**, four chunks). Copy the `.bas` and a sample file to the
calling Next, edit lines 70–90 (bridge host, local file, remote path), RUN.

The program opens the file as a stream, reads its length with `DIM #`, then
loops — filling a bank with the next chunk and POSTing it with `.http`
(which substitutes single-letter string variables like `b$`/`l$`/`u$` on
its command line). It keeps a running 16-bit checksum while reading and,
after the upload, asks the bridge's `/sum` route (below) for the remote
file's checksum to verify the transfer end-to-end:

```
 10 REM upload a file to a remote Next through the ZX-Next-Unite
 20 REM HTTP bridge, in 16K bank chunks, then verify its checksum.
 60 RUN AT 3
 70 LET h$="192.168.1.10": REM PC running the bridge
 80 LET f$="sample-4k.txt": REM local file to upload
 90 LET r$="/incoming/sample-4k.txt": REM destination on the remote Next
100 BANK NEW b: LET b$=STR$ b
110 OPEN #4,f$
120 DIM #4 TO t: REM t = local file length in bytes
130 LET u$="/put?path="+r$+"&append=1&size="+STR$ t
140 LET c=0: LET o=0
150 REM ---- send loop: fill the bank, POST it, repeat ----
160 IF o=t THEN GO TO 250
170 LET l=t-o: IF l>16384 THEN LET l=16384
180 FOR i=0 TO l-1
190 LET d=CODE INKEY$#4
200 BANK b POKE i,d
210 LET c=c+d: IF c>=65536 THEN LET c=c-65536
220 NEXT i
230 LET l$=STR$ l
240 .http post -b b$ -l l$ -h h$ -u u$
245 LET o=o+l: GO TO 160
250 CLOSE #4
260 PRINT "sent ";t;" bytes, local checksum ";c
270 REM ---- fetch the remote file's checksum from the bridge ----
280 LET u$="/sum?path="+r$+"&bare=1"
290 .http -h h$ -u u$ -f sum.txt
300 LET s=0
310 OPEN #4,"sum.txt"
320 LET k$=INKEY$#4
330 IF k$>="0" AND k$<="9" THEN LET s=s*10+CODE k$-48: GO TO 320
340 CLOSE #4
350 BANK CLEAR
360 IF s=c THEN PRINT "checksum OK (";s;") - transfer verified"
370 IF s<>c THEN PRINT "checksum MISMATCH: local ";c;", remote ";s
```

Notes:

* `DIM #4 TO t` (stream length) and the byte-wise `INKEY$#4` reads come
  from the +3e/NextZXOS stream commands — see the *NextBASIC file-related
  commands* document on the Next's SD card (`docs/nextzxos`).
* The checksum is the 16-bit additive sum (all bytes mod 65536) — the same
  thing `/sum` computes, so line 360 comparing the two proves every byte
  arrived intact.
* The per-byte copy loop is the simple, portable way to fill the bank; it is
  not fast (a 1 MB file takes a few minutes even at the 28 MHz `RUN AT 3`
  sets). For fixed offsets known in advance, the NextZXOS `.extract` dot
  command (`.extract big.tap +49152 16384 -mb 40`) fills a bank much
  faster, but it does not substitute BASIC variables, so it cannot drive
  this loop.
* If the transfer dies midway, just RUN it again — the first chunk of the
  retry resets the bridge's spool for that path.
* The sample files are deliberately pure 7-bit ASCII, so the program runs
  unmodified on real hardware, CSpect and MAME+jesperl — see the emulator
  note below.

### `GET /sum?path=/games/a.tap` — verify a transfer (16-bit checksum)

```
curl "http://localhost/sum?path=/incoming/sample-4k.txt"
.http -h 192.168.1.10 -u "/sum?path=/incoming/sample-4k.txt&bare=1" -f sum.txt
```
```
OK
path: /incoming/sample-4k.txt
bytes: 4096
sum16: 20671
```
Fetches the file from the Next and answers its size plus the **16-bit
additive checksum** (sum of all bytes mod 65536) — cheap for a NextBASIC
caller to mirror while uploading. `&bare=1` answers just the checksum
digits (`20671`), trivial to parse on the Next.

### Emulator note — CSpect ESP, `-7`, MAME + jesperl

**CSpect**'s built-in ESP emulation is **7-bit**: a binary byte like `0xFF`
arrives corrupted to `0x7F`. Two escape hatches exist, and which one you
need depends on your CSpect setup — **try without `-7` first**:

* The **UART Replacement plugin** (Robin Verhagen-Guest,
  [CSpectPlugins](https://github.com/Threetwosevensixseven/CSpectPlugins/releases/latest);
  see [Remy's write-up](https://remysharp.com/2021/09/09/working-with-the-esp-in-cspect))
  replaces CSpect's ESP with 8-bit-clean comms "near identical to the
  Spectrum Next's own" — with it, no `-7` and no base64 should be needed
  at all.
* Without the plugin, next-http's **`-7`** flag makes `.http`
  **base64-decode responses** — which only helps when the server actually
  sends base64. For binary downloads through the bridge add `&b64=1` to
  `/get` (the body arrives base64-encoded, 7-bit safe) together with `-7`:
  `.http -7 -h <host> -u "/get?path=/games/scr.bin&b64=1" -f scr.bin`.

The `httpput` sample sidesteps the question entirely: its payloads and every
reply it reads are plain 7-bit ASCII, so it runs identically with or
without the plugin and never needs `-7`.

**MAME + jesperl** (the `jesperl_xtr.pl` ESP-AT-over-TCP emulator feeding
MAME's emulated UART): its data path is verified 8-bit clean in both
directions — raw bytes in, raw `+IPD` frames out — so **no `-7`** and no
base64 there either; binary transfers work as on real hardware. Details:
[next-http documentation](https://github.com/remy/next-http).

### `GET /mkdir?path=/backup` — create a directory

```
curl "http://localhost/mkdir?path=/backup"
.http -h 192.168.1.10 -u /mkdir?path=/backup -f ok.txt
```
```
OK mkdir /backup
```

### `GET /rmdir?path=/backup` — remove an EMPTY directory

```
curl "http://localhost/rmdir?path=/backup"
```
```
OK rmdir /backup
```

### `GET /rmtree?path=/backup` — remove a directory recursively

```
curl "http://localhost/rmtree?path=/backup"
```
```
OK rmtree /backup
```
Deletes the folder with everything inside it (the bridge walks the tree).
Available through the **app**'s bridge; `nextsync5.py` answers `501`.

### `GET /rm?path=/old.tap` — delete a file

```
curl "http://localhost/rm?path=/old.tap"
```
```
OK rm /old.tap
```

### `GET /ren?from=/a.tap&to=/b.tap` — rename / move

```
curl "http://localhost/ren?from=/games/a.tap&to=/games/b.tap"
.http -h 192.168.1.10 -u "/ren?from=/games/a.tap&to=/games/b.tap" -f ok.txt
```
```
OK ren /games/a.tap -> /games/b.tap
```
Same-drive moves too (`from=/x.tap&to=/backup/x.tap`).

### `GET /rcpy?src=/games&dst=m:/backup/games` — copy ON the Next

```
curl "http://localhost/rcpy?src=/games&dst=m:/backup/games"
```
```
OK rcpy /games -> m:/backup/games (42 file(s))
```
Copies a file or whole tree **entirely on the remote Next** (dot v5.2+),
across partitions too — no data crosses the network. A big tree takes a
while; the request waits for the Next to finish (up to 15 minutes).
Copying a folder into itself is refused with `400`.

### `GET /rfsize?path=/games` — total size of a file / tree

```
curl "http://localhost/rfsize?path=/games"
.http -h 192.168.1.10 -u /rfsize?path=/games -f size.txt
```
```
OK
files: 42
folders: 7
bytes: 1234567 (1.2 MB)
```
The natural "will it fit" companion of `/rcpy` (check `/free` too).

### `GET /forceexit` — tell the Next to leave `-listen` and exit

```
curl "http://localhost/forceexit"
.http -h 192.168.1.10 -u /forceexit -f ok.txt
```
```
OK forceexit - the Next is disconnecting
```
The dot answers its next poll with the protocol's `Q` (quit) opcode: it
closes the connection and exits **gracefully** to BASIC (the same clean
path as `quit` in the `listen>` console or pressing BREAK on the Next —
UART speed, CPU speed and turbo/50-60 settings are all restored). The
`-listen` server keeps running, so a fresh `.sync5 -listen` reconnects at
any time. With no Next connected the route answers `503`.

Also callable straight from the command line, without writing a curl line —
`nextsync5.py -forceexit` calls the route on `127.0.0.1:80` (stdlib only, no
Flask needed on the calling side), `-forceexit=host[:port]` on any other
running bridge:

```
python nextsync5.py -forceexit=192.168.1.10
```

(The port defaults to **80**, like everything else here; append `:port` only
for a bridge started on a custom port. In PowerShell, quote the dotted form —
`"-forceexit=192.168.1.10"` — or PowerShell itself splits the argument at
the dots.)

### `GET /` or `GET /help` — the route list

```
curl "http://localhost/"
```
Prints the reference above in one screen — handy straight on a Next:
`.http -h 192.168.1.10 -u /help -f help.txt`

---

## JSON example

```
curl "http://localhost/rfsize?path=/games&json=1"
{"ok": true, "path": "/games", "files": 42, "dirs": 7, "bytes": 1234567, "human": "1.2 MB"}
```

## Notes & limits

* One command runs at a time (the -listen session is serial); concurrent
  HTTP requests queue up. `/status` never blocks behind a transfer.
* The bridge is **unauthenticated plain HTTP** for `.http` compatibility —
  run it on your own LAN only.
* Long operations (`/get`, `/put`, `/rcpy`, `/rfsize`, `/rmtree`) wait up to
  15 minutes; quick verbs time out after 45 s (`504`).
* Wire protocol: unchanged. The bridge simply queues the same commands the
  Remote Explorer / `listen>` console would.
* Troubleshooting: `nextsync5.py -w -v` logs every request and response
  (`HTTP > GET /ls?path=/ …` / `HTTP < 200 GET /ls OK 2 entries…`).
* Covered end-to-end by `test_http_bridge.py` (mock Next, both hosts).
