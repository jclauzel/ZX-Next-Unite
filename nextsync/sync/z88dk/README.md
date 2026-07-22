# NextSync `.sync` — z88dk **dotN** build (no 8 KB limit)

This directory builds the NextSync dot command as a z88dk **dotN** command so it
is no longer bound by the 8 KB ceiling of a classic dot command.

## Why

A classic esxDOS/NextZXOS dot command loads at `$2000` and must fit in
`$2000–$3FFF` (8 KB). The SDCC build one level up (`../build.ps1`,
`../nextsync.c` + `../*.s`) sits right against that wall (~8075 bytes). A **dotN**
command is loaded differently: appmake splits it into the 8 KB page at `$2000`
**plus** extra 8 KB pages that the dotN loader allocates from NextZXOS at run
time and maps into the MMU. That removes the size limit.

The SDCC build is left untouched as the known-good fallback. This is a parallel
build of the *same* `nextsync.c` / `gfx.c` logic, compiled by z88dk instead.

## Build

Requires z88dk (tested with the build at `C:\z88dk`). PySide app / hdfmonkey are
not involved.

```
.\build_dotn.ps1                       # z88dk at C:\z88dk (or $env:Z88DK_DIR)
.\build_dotn.ps1 -Z88dkDir "D:\z88dk"
.\build_dotn.ps1 -Keep                 # keep .o / .map intermediates
```

Output: `syncdev` (the dotN), also copied to `..\server\dot\syncdev`.

The raw z88dk command (what the script runs) is:

```
zcc +zxn -startup=30 -clib=sdcc_iy -SO3 --max-allocs-per-node200000 \
    --opt-code-size @zproject.lst -o syncdev -pragma-include:zpragma.inc \
    -subtype=dotn -Cz"--clean" -create-app -m
```

## Files

| File | Role |
|---|---|
| `nextsync.c` | The port. Same protocol logic as `../nextsync.c`; the SDCC externs are replaced by `#include "syncsys.h"`, buffers moved to bss, `main()` takes the raw command line, `createfilewithpath` uses `esx_f_mkdir`. |
| `gfx.c` | Unchanged copy of `../gfx.c` (pure C helpers). |
| `free.c` | Head-page `-listen` helpers: the free-space query (`'Z'`, psize/pfull, v5.2+; `sync_getfree` = F_GETFREE with a temporary M_GETSETDRV drive switch) plus the print-free `listen_ls` replier, moved here from `nextsync.c` to reclaim main-bank stack headroom. |
| `rcpy.c` | The `-listen` local-copy command (`'C'`, rcpy, v5.2+): file & recursive directory copy entirely on the Next, across partitions too, with `'D'` progress/keepalive blocks. Head-page resident like `free.c`; its two growing path buffers live in the tail of `scratch` (+512/+768), so it adds zero main-bank bss. |
| `rfsize.c` | The `-listen` tree-size command (`'S'`, rfsize, v5.2+): total size + file/folder counts of a file or whole directory tree — rcpy's "will it fit" companion. Head-page resident; O(n) per-directory sweep with the handle open (the `listen_ls`-proven pattern), 48-bit byte total, depth-capped recursion. |
| `syncsys.h` | Shim the app compiles against: `fopen/fread/...` become macros onto `esx_f_*`; `readnextreg/writenextreg`, `conprint`, `receive`, `checksum`, `mulby10`. |
| `syncsys.c` | Shim implementations over z88dk's esxDOS/nextreg/stdout. `checksum` is now C (was asm). |
| `uart.asm` | z80asm port of the timing-critical `receive()` loop (the only piece kept in assembly). |
| `zproject.lst` | Source list for `zcc @`. |
| `zpragma.inc` | Memory model (see below) and crt options. |
| `build_dotn.ps1` | Build + deploy script. |

## Memory model (see `zpragma.inc`)

```
$2000–$3FFF   primary 8 KB dot page : crt + clib + esxDOS driver code
$8000–$BFFF   "main bank" (mmu4/5)  : our C code, rodata, data, bss buffers, stack
$C000–$FFFF   (mmu6/7)              : NextZXOS (saved/restored by the crt)
```

`CRT_ORG_MAIN=0x8000`, `REGISTER_SP=0xC000` (v5.3 — the stack starts at the
very top of mmu5; v5.2's `0xBF00` wasted the last 256 bytes, which nothing in
the dotn crt or clib touches). The large buffers (`inbuf`,
`scratch`, …) are file-scope statics so they land in the main bank and keep the
stack small. Current layout: main-bank content ends at `0xBDDC`
(`__BSS_END_tail` in `syncdev.map`), leaving ~548 bytes of stack below `0xC000`;
the primary dot page ends at `0x3EE8` (`__CODE_END_tail`), 24 bytes below the
hard `0x3F00` line — content past it triggers appmake's "may overlap stack
area" warning and sits where the dotn loader's own startup/exit stack (SP =
`0x4000`) and its 128-byte exit-message buffer (`0x3F76+`) can scribble.
**Both pools are tight — check those two numbers after any change, and treat
that appmake warning as an error.**
To protect the stack headroom, the code and const data of `anim.c` (sprites +
the `-v` spinner), `free.c`, `rcpy.c` and `rfsize.c` live in the free space of
the primary 8 KB dot page instead:
`build_dotn.ps1` compiles each to asm, retargets their sections at
`code_dot`/`rodata_dot` — the dotn memory model's head-page sections reserved
for user dot content, placed *after* the crt+clib chains — and links the
patched `*_head.asm` (zsdcc itself has no per-file section control —
`#pragma codeseg` and `--codeseg` are silently ignored). Retargeting at `CODE`
itself must be avoided: that appends into the crt's startup fall-through chain
and crashes on launch. Only bss (sprite state, rcpy's 11-byte in-flight copy
state) stays in the main bank — bss is never retargeted. This mirrors z88dk's
own `ls`/`dzx7` dotN
examples (same `0xf0` allocation mask): the crt saves NextZXOS's bank/MMU state
on entry and restores it on exit, and esxDOS file/dir calls work through divMMC
regardless of what is paged at mmu6/7.

`CRT_ENABLE_COMMANDLINE=2` hands `main(len, cmdline)` the **unprocessed** command
tail, so the app's own parser still sees `:` in paths like `.sync -send c:/foo`.
`CRT_ENABLE_COMMANDLINE_EX=0` forces the tail to come from HL **without** the
`.sync` program name (the dotn default is `0x80` = from BC *with* the name, which
made a bare `.sync` behave as if given `sync` as a server argument).

Console output goes through z88dk's ROM-print driver, which **ignores `\r`** and
only newlines on `\n`; the app uses `\r` throughout, so `conprint()` translates
`\r`→`\n`.

## `-listen` — remote file server

`.sync5 -listen` connects to the saved server and then acts as a small remote
file server the PC drives. **Fully backward compatible:** it is only entered
after a *new* handshake keyword `"Listen"`; every Sync3/Sync4/`-send` path and
the block framing are untouched, and an un-upgraded dot never sends `"Listen"`
(an old server just replies `"Error"` and the dot exits).

The on-screen trace of each received command and action (`> P /ho/bj.txt`,
`open:`/`mkdir:`/`open ok`, `wrote N`, `put done`, `mkdir ok`, …) is ON by
default since v5.0 — handy for diagnosing a transfer on the hardware. Disable
it with `-nv` (likewise `-na` disables the sprite animation and `-nr` the
retro green-on-black look, both also on by default; the old opt-in flags
`-v`/`-anim`/`-a`/`-dark`/`-d` are still accepted as no-ops). `-help`/`-h`
show the help screen. On the PC side, `nextsync5.py -v` logs every packet.

Long operations stay visibly alive: a `| / - \` **spinner** twirls at the end
of the open trace line while a file downloads (`put`, plain sync) or copies
(`rcpy`), and the sprite animation now also ticks *during* transfers — at
every **link-idle safe point** (between acked packets, between 2 KB copy
chunks, on every keepalive, and through the idle-poll throttle), self-limited
to one step per video frame via the ROM's 50 Hz `FRAMES` counter, so movement
is wall-clock smooth at the default/fast UART rates and never touches a byte
in flight. An interrupt-driven animation was considered and rejected: an IM2
vector table + ISR must live in memory that is mapped at *every* instant an
interrupt can fire, and neither dot home qualifies — the head page is paged
away by every esxDOS call and by the print driver, and the main bank would
have to donate its last few hundred bytes of stack headroom (and take ISR
pushes on whatever stack is live inside esxDOS). The frame-locked safe-point
ticks give the same visual result with none of that crash surface.

The Next keeps driving — it *polls* the server for the next command and runs it.
All frames use the existing `[2B total][payload][cs0][cs1][packetno]` framing:

```
Next  -> server : "Poll"
server-> Next   : one command frame, payload = opcode + optional path:
     'I'          idle, nothing queued -> the Next re-polls (throttled)
     'L' <path>   ls    : the Next pushes a directory listing back
     'G' <path>   get   : the Next pushes the file / whole dir back
     'P' <path>   put   : the Next pulls the file from the server
     'M' <path>   mkdir      'R' <path>  rmdir      'X' <path>  rm
     'W'          getdrives : the Next pushes the mounted drive letters
     'Z' [drive]  free  : free space on a partition (psize/pfull, v5.2+)
     'C' <src>\0<dst>  rcpy : copy a file/dir LOCALLY on the Next (v5.2+)
     'S' <path>   rfsize : total size of a file / directory tree (v5.2+)
     'Q'          quit  -> leave listen mode
```

`ls`/`get`/`mkdir`/`rmdir`/`rm` answer by pushing blocks (each acked `"Ok"`, like
`-send`): `ls` sends `'D'` blocks of packed `[flags][size LE][namelen][name]`
entries then `'E'`; `get` reuses `send_file`/`send_dir` (`'N'`/`'D'`/`'E'` then a
final `'B'`); the status ops send one `'O'`/`'F'` block. `put` reuses `transfer()`
— the Next pulls with `"Get"` and the server serves the bytes, exactly like a
normal download. `getdrives` (v5.1+) sends one `'O'` block carrying the current
drive letter (M_GETDRV) then the letters `{C, M, current}` — C and M are
guaranteed by NextZXOS and the current drive is mounted by definition. Drives
are **never probed**: any file call on an unmounted letter (and any touch of
the A:/B: floppy letters, and any M_P3DOS-routed call) remaps `$8000-$BFFF`
mid-call and crashes the dotN — three separate real-hardware crashes confirmed
this. Every `<path>` may carry an optional drive prefix (`m:/games`), and one
without lands on the dot's current drive as before.

`rcpy` (`'C'`, v5.2+) copies a file or a whole directory tree **entirely on
the Next** — no data crosses the wire, and because every esxDOS call takes
drive-prefixed paths it works across partitions (`c:/x` → `m:/y`) with no
drive switching. The paths travel NUL-separated like `ren`'s. The Next pushes
`'D'` progress blocks (one per file carrying the destination path, plus
empty keepalives every 64 KB inside big files **and every 256 entries inside
the directory skip-loops** — on a large directory the quadratic skip
otherwise left the link silent long enough for the PC to give up mid-copy,
the field-reported "hang") and ends with `'O'` (all copied) or `'F'`
(something failed). The walk is **skip-and-continue**: an item that cannot
be copied (strange name, unreadable, destination full, too deep) is counted
and skipped, the rest keeps copying — and with `-v` (on by default) the Next
traces every item on screen: `f-> <file>` printed **before** the bytes move
with a `| / - \` spinner twirling at the end of the line while they do (so a
multi-MB file no longer freezes the screen until done), `d-> <dir>`, and
`/!\ error -> <source item>` for the skipped ones, ending with `rcpy done`.
The walk itself is **iterative** (explicit level stack in the scratch tail,
one C-stack frame at any depth — the main bank is too tight for recursion)
and lives in the head page, print-free; the main-bank entry point drives it
one item per step, and each file copy is **chunk-stepped** too
(`rcpy_fbegin`/`rcpy_fchunk`, one 2 KB chunk per call), so every trace line
and spinner pose is drawn with no head-page frame on the stack. The spinner
itself never touches the print driver: it writes its 8 pixel bytes straight
into the display-file cell at the ROM print position (DF_CC), which is also
why it is safe. A mid-file read error can no
longer pass as success (the copy is checked against the source's stat size).
Same safe-walk core as `send_dir` (never file I/O with a readdir handle
open); the destination-inside-source infinite trap is guarded on the PC
side. In the app, Copy → Paste inside the Remote Explorer's Next pane rides
this command — and first runs an `rfsize` + fresh `free` **precheck**: a
copy that cannot fit is refused up front with the exact shortfall, and when
it fits the measured totals drive the progress dialog off the `'D'`
heartbeats (named = a file starting, empty = 64 KB copied). The dialog's
button doesn't cancel (a Next-side copy can't be interrupted): it closes
the window and the copy finishes in the background behind a "Remote copy in
progress" overlay on the Next pane, ending in a success/failure toast.

`rfsize` (`'S'`, v5.2+) measures a file or a whole directory tree on the Next
— the natural check before an `rcpy` ("will it fit?", together with `free`).
It pushes `'D'` progress blocks (one per directory entered, carrying its
path, plus empty keepalives every 256 enumerated entries) and ends with
`'O'` + `[4B files][4B dirs][4B size_lo][2B size_hi]` (all little-endian;
total bytes = `size_hi·2³² + size_lo` — a tree can exceed 32 bits) or `'F'`.
Counting needs **no file I/O** (sizes come from the dirents), so each
directory is swept in one O(n) pass with the handle held open — the exact
readdir+network-only pattern `listen_ls` proved on hardware; descending into
sub-directories uses the re-open/skip/close dance, and like `rcpy` the walk
is **iterative** (explicit level stack, no recursion) with a clean `'F'`
beyond the depth cap. In the app, right-click → "Get size" in the Remote
Explorer's Next pane rides this command.

`free` (`'Z'`, v5.2+) answers with one `'O'` block carrying 4 bytes
little-endian = the partition's free 512-byte blocks, or `'F'` on failure —
this backs the PC's `psize` (exact bytes) and `pfull` (human-readable)
commands. It uses `F_GETFREE` (`$b1`), the **only** storage metric NextZXOS
exposes through the dotN-safe divMMC API — total partition size would need
+3DOS/IDEDOS calls via M_P3DOS, which is fatal here (above), so free space is
deliberately all these commands report. `F_GETFREE` only accepts `'*'`
(default drive), so another partition is measured by temporarily re-pointing
the default drive with `M_GETSETDRV` and always switching back; `'A'`/`'B'`
are rejected outright (the floppy trap), and unmounted letters remain the
user's responsibility exactly as for every other drive-prefixed command.

Server side: `nextsync5.py` gains a `listen_session()` (triggered by `"Listen"`)
with a console CLI (`ls`/`get`/`put`/`mkdir`/`rmdir`/`rm`/`ren`/`drives`/
`psize`/`pfull`/`rcpy`/`rfsize`/`quit`). The whole wire
protocol is covered by `server/test_listen.py`, which drives `listen_session()`
over a socketpair with a mock Next — run it with `python test_listen.py`.
(The app-side twin, `zxnu_workers.run_remote_listen_server`, is covered by
`server/test_remote_listen.py`.)

## Status / testing

- Builds clean as a valid dotN (~13 KB of code+data past the old 8 KB page;
  24 KB command file).
- **Server `-listen` protocol is validated on localhost** by `test_listen.py`
  (ls/get/put/mkdir/rmdir/rm all pass against a mock Next). The **dot** side
  compiles clean and reuses the proven send/receive paths, but the Next half of
  `-listen` still needs a real-Next run to confirm.
- **Not yet run on hardware or a NextZXOS emulator card.** This environment has
  CSpect but no bootable NextZXOS system SD image, so a load test could not be
  performed here. To smoke-test: copy `syncdev` to your card's `C:/DOT/` folder
  and run `.syncdev` from NextZXOS BASIC — it should print
  `NextSync 5.4 Clauzel/Komppa` and return cleanly. Then exercise a real sync
  against the ZX-Next-Unite server as usual.
- **`-listen` dead-link watchdog (v5.4):** if the server vanishes without its
  goodbye `Q` reaching the dot (app killed/crashed, PC asleep, wifi drop, or
  the clean-shutdown `Q` losing its race against a long transfer), the esp
  answers every send on the closed connection with `ERROR` and the dot used to
  re-poll forever — "listening" but ignoring all commands, with only BREAK as
  a way out. Since v5.4 the poll loop counts consecutive bad/empty polls and
  exits with `Connection lost - stopping` after 8 in a row (each one burns the
  full uart timeout, so that is many seconds of true silence); any good frame
  resets the count, so uart noise or a brief wifi hiccup still just re-polls.
- The UART `receive` timing was preserved but should be re-verified at 2 Mbaud
  on real hardware.
