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

`CRT_ORG_MAIN=0x8000`, `REGISTER_SP=0xBF00`. The large buffers (`inbuf`,
`scratch`, …) are file-scope statics so they land in the main bank and keep the
stack small. Current layout: main-bank content ends at `~0xAC52`, leaving
~4.7 KB of stack below `0xBF00`. This mirrors z88dk's own `ls`/`dzx7` dotN
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

## Status / testing

- Builds clean as a valid dotN (~11 KB of code+data past the old 8 KB page;
  24 KB command file).
- **Not yet run on hardware or a NextZXOS emulator card.** This environment has
  CSpect but no bootable NextZXOS system SD image, so a load test could not be
  performed here. To smoke-test: copy `syncdev` to your card's `C:/DOT/` folder
  and run `.syncdev` from NextZXOS BASIC — it should print
  `NextSync 4.2 Clauzel/Komppa` and return cleanly. Then exercise a real sync
  against the ZX-Next-Unite server as usual.
- The UART `receive` timing was preserved but should be re-verified at 2 Mbaud
  on real hardware.
