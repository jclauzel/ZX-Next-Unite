# NextSync HTTP bridge — drive a remote Next's file system over HTTP

The HTTP bridge is a small self-hosted web server (Flask) that republishes a
NextSync **`.sync5 -listen`** session as plain HTTP routes:

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

* **ZX-Next-Unite app** — Settings tab → *"Enable NextSync HTTP bridge (web
  server for the Next's .http command)"*. Off by default; the choice is saved
  in `hdfg.cfg` (`nextsync_http_bridge`) and the server then starts
  automatically with the app. The port defaults to **80**; set
  `nextsync_http_port = 8080` in `hdfg.cfg` to change it. The bridge drives
  the Next connected to the **Remote Explorer**'s listen server.
* **Standalone server** — `python nextsync5.py -http` (or `-http=8080`).
  Requires `pip install flask` and `zxnu_http_bridge.py` (from the repo root;
  a copy next to `nextsync5.py` also works).

Every route answers **plain text** (easy to show or parse on a Next); append
`&json=1` (or send `Accept: application/json`) for JSON. Failures use real
HTTP status codes: `400` bad arguments, `501` unsupported, `502` the Next
said no, `503` no Next connected, `504` timed out.

Remote paths accept an optional drive prefix (`m:/backup`) exactly like every
other NextSync command. URL-encode special characters (space = `%20`).

---

## Route reference & call samples

The `curl` lines below talk to a bridge at `192.168.1.10`; the `.http` lines
are what the **calling Next** would run (`-f` saves the reply to a file,
`-b`/`-l` use a memory bank — see the next-http README).

### `GET /status` — is a Next connected, how many partitions?

```
curl "http://192.168.1.10/status"
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
curl "http://192.168.1.10/drives"
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
curl "http://192.168.1.10/free?drive=m"
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
curl "http://192.168.1.10/ls?path=/games"
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
curl -o boot.bas "http://192.168.1.10/get?path=/games/boot.bas"
.http -h 192.168.1.10 -u /get?path=/games/boot.bas -f boot.bas
.http get -b 20 -h 192.168.1.10 -u /get?path=/games/scr.bin
```
The response body IS the file. Folders are refused with `400` — list them
with `/ls` and fetch file by file.

### `POST /put?path=/games/new.tap` — upload (request body = the file)

```
curl --data-binary @new.tap "http://192.168.1.10/put?path=/games/new.tap"
.http post -b 22 -l 1024 -h 192.168.1.10 -u /put?path=/games/bank.bin
```
```
OK put /games/new.tap (13456 bytes)
```
A `path` ending in `/` needs `&name=<filename>` (the file's name inside that
folder). On the calling Next, `post -b 22 -l 1024` sends 1024 bytes from
memory bank 22 — that is how a Next pushes data through the bridge.

### `GET /mkdir?path=/backup` — create a directory

```
curl "http://192.168.1.10/mkdir?path=/backup"
.http -h 192.168.1.10 -u /mkdir?path=/backup -f ok.txt
```
```
OK mkdir /backup
```

### `GET /rmdir?path=/backup` — remove an EMPTY directory

```
curl "http://192.168.1.10/rmdir?path=/backup"
```
```
OK rmdir /backup
```

### `GET /rmtree?path=/backup` — remove a directory recursively

```
curl "http://192.168.1.10/rmtree?path=/backup"
```
```
OK rmtree /backup
```
Deletes the folder with everything inside it (the bridge walks the tree).
Available through the **app**'s bridge; `nextsync5.py` answers `501`.

### `GET /rm?path=/old.tap` — delete a file

```
curl "http://192.168.1.10/rm?path=/old.tap"
```
```
OK rm /old.tap
```

### `GET /ren?from=/a.tap&to=/b.tap` — rename / move

```
curl "http://192.168.1.10/ren?from=/games/a.tap&to=/games/b.tap"
.http -h 192.168.1.10 -u "/ren?from=/games/a.tap&to=/games/b.tap" -f ok.txt
```
```
OK ren /games/a.tap -> /games/b.tap
```
Same-drive moves too (`from=/x.tap&to=/backup/x.tap`).

### `GET /rcpy?src=/games&dst=m:/backup/games` — copy ON the Next

```
curl "http://192.168.1.10/rcpy?src=/games&dst=m:/backup/games"
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
curl "http://192.168.1.10/rfsize?path=/games"
.http -h 192.168.1.10 -u /rfsize?path=/games -f size.txt
```
```
OK
files: 42
folders: 7
bytes: 1234567 (1.2 MB)
```
The natural "will it fit" companion of `/rcpy` (check `/free` too).

### `GET /` or `GET /help` — the route list

```
curl "http://192.168.1.10/"
```
Prints the reference above in one screen — handy straight on a Next:
`.http -h 192.168.1.10 -u /help -f help.txt`

---

## JSON example

```
curl "http://192.168.1.10/rfsize?path=/games&json=1"
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
* Covered end-to-end by `test_http_bridge.py` (mock Next, both hosts).
