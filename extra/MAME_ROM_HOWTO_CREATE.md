# How to (Re)Build `tbblue.zip` for MAME

Rebuilding the ZX Spectrum Next boot-ROM package used by MAME's `tbblue`,
`specnext_ks1`, `specnext_ks2` and `specnext_ks3` machines — with an
explanation of what each file actually is and why the package is put together
the way it is.

**Revised 2026-11-11 for the KS3 era.** The package now holds **six** ROMs
instead of four: the v3.02.04 boot ROM MAME shipped from 0.267 on turned out to
be a release candidate, and the Next driver's upstream (the holub fork, July
2026) now wants the *final* v3.02.04 pair under the old names, with the RC pair
renamed `boot-30204rc*.bin`. One six-file archive satisfies both the official
MAME releases and that newer driver — section 3 explains why. What the
System/Next 26.11a distribution does and does not change for MAME is in
section 1.

---

## 0. Quick start

The whole build, start to finish. Sections 1–5 explain what these files are and
why; sections 6–8 explain every command. If you just want the package, this is
it.

Prerequisites: **Git** and **Python 3** on `PATH` (section 6).

This guide uses `C:\temp` as the working folder. **Windows does not ship with a
`C:\temp` folder** — the system temp directory lives somewhere else entirely,
under your user profile — so you almost certainly need to create it. Do that
first, then put `fetch-roms.ps1` and `extract-vhdl-roms.py` inside it.

Any folder works; `C:\temp` is just short and easy to type. If you prefer
somewhere else, substitute it consistently in every command below. Avoid paths
with spaces or non-ASCII characters — they are handled correctly here, but they
make hand-editing commands error-prone.

**Windows (PowerShell):**

```powershell
# 0. create the working folder and move into it
New-Item -ItemType Directory -Force -Path 'C:\temp' | Out-Null
Set-Location 'C:\temp'
# now save fetch-roms.ps1 and extract-vhdl-roms.py into C:\temp

# 1. clone the FPGA repo and pull the six source files
powershell -ExecutionPolicy Bypass -File .\fetch-roms.ps1

# 2. decode the two VHDL-embedded ROMs and verify all six
py extract-vhdl-roms.py "C:\temp\tbblue-build\out"

# 3. package
$O = 'C:\temp\tbblue-build\out'
$Files = 'boot-30100.bin','boot-30200-ab.bin',
         'boot-30204rc.bin','boot-30204rc-ab.bin',
         'boot-30204.bin','boot-30204-ab.bin' |
         ForEach-Object { Join-Path $O $_ }
Compress-Archive -Path $Files -DestinationPath 'C:\temp\tbblue.zip' -Force

# 4. install and verify
Copy-Item 'C:\temp\tbblue.zip' 'C:\mame\roms\tbblue.zip'
cd C:\mame
.\mame.exe -verifyroms tbblue
```

**Linux / macOS (bash):**

```bash
chmod +x fetch-roms.sh && ./fetch-roms.sh
python3 extract-vhdl-roms.py tbblue-build/out
cd tbblue-build/out
zip -9 -X ../tbblue.zip boot-30100.bin boot-30200-ab.bin \
    boot-30204rc.bin boot-30204rc-ab.bin boot-30204.bin boot-30204-ab.bin
cp ../tbblue.zip ~/mame/roms/tbblue.zip
cd ~/mame && ./mame -verifyroms tbblue
```

Two checkpoints decide whether it worked:

- Step 2 must end with `All six ROMs verified.`
- Step 4 must print `romset tbblue is good`.

Anything else, see section 10.

Then boot it — you also need a NextZXOS SD card image (see section 9 before
you pick one; the official 26.11a "image for emulators" does not boot in MAME):

```
mame specnext_ks2 -hard1 /path/to/cspect-next-2gb.img
```

---


## 1. What this package is, and what it is not

MAME needs a small set of ROM images before it will start a ZX Spectrum Next.
These are the **IPL boot ROMs that are embedded inside the FPGA core on real
hardware**. They are not ZX Spectrum machine ROMs, and they are not anything
you would normally think of as "a Spectrum ROM".

This trips people up constantly, so it is worth being precise about the two
different things called "tbblue":

| | |
|---|---|
| `gitlab.com/thesmog358/tbblue` | The **System/Next (NextZXOS) distribution** — everything that goes *on the SD card*: the OS, the machine ROMs, `config.ini`, docs, demos, and the compiled core (`TBBLUE.TBU`) the firmware flashes into real hardware. |
| `gitlab.com/SpectrumNext/ZX_Spectrum_Next_FPGA` | The **official FPGA cores repository** — the VHDL for the Next itself, and the boot ROMs baked into the core. **This is where our files come from.** |

The package is named `tbblue.zip` purely because `tbblue` is the MAME machine
name for the Next, and MAME looks up ROM archives by machine name. It has
nothing to do with the distribution repo of the same name.

So: the ROMs in this zip get you a machine that boots. The SD card image you
pass with `-hard1` supplies everything after that.

### What the KS3 distribution changes — and what it does not

The KS3-era distribution is **System/Next 26.11a**
(<https://www.specnext.com/latestdistro/>, distributions are now named by year
and month). It ships **core 3.03.03**, **firmware 1.46** and **NextZXOS 2.10a**.
It is natural to expect a "core 3.03.03 boot ROM" to go into this package. It
does not, for two independent reasons:

- **The 3.03 core only exists as a bitstream.** `TBBLUE.TBU` in the
  distribution repo was replaced on 2026-11-05 and bumped to "Core 3.03.03" on
  2026-11-08, but the public FPGA source repo has no 3.03 branch: its `master`
  is still "CORE VERSION 3.02.03" (2025-11-06) and the newest commits are the
  3.02.05 development branch. The newest core whose boot ROM exists as a plain
  `.bin` anyone can fetch is the **final 3.02.04** (branch `30204`, "CORE
  VERSION 3.02.04", 2026-05-28).
- **MAME emulates the 3.02.04 core.** Its ROM table only knows the 3.01.00,
  3.02.00 anti-brick and 3.02.04 IPLs, it matches them by hash, and the driver
  does not model the 3.03 features (the zxnDDMA blitter, zero-wait-state
  28 MHz). A 3.03.03 IPL would be refused at load time and would have nothing to
  drive if it were not.

What *did* change for MAME is narrower, and is the reason this document was
revised: the v3.02.04 boot ROM pair MAME has carried since 0.267 (taken from
the `30204` branch in January 2026) is a **release candidate**. When the final
3.02.04 core landed in May 2026, the Next driver's author added the final pair
to the ROM table as `v30204` / `v30204ab` and relabelled the old pair
"v3.02.04 (RC)" under new file names (`boot-30204rc.bin`,
`boot-30204rc-ab.bin`). That is the holub fork commit `51b5fd54` of
2026-07-08, "sinclair/next/specnext.cpp: Added final v30204 BIOS options". As
of 2026-11-11 it has **not** reached the official MAME repository — 0.289 and
`mamedev/mame` master still carry the four-entry table — but builds of that
fork are in circulation (ZX-Next-Unite's own ESP-emulation work is tested on
one). This package serves both; see section 3.

### The ready-made downloads

The SpecNext wiki's MAME page points at
`https://www.specnext.com/forum/download/file.php?id=1164`, a phpBB forum
attachment. It still downloads in a browser (2026-11-11), but it sits behind
an anti-robot check, so scripts, link checkers and `curl` are refused — which
is what the first edition of this guide mistook for a dead link. Two things
to know before relying on it: an attachment ID breaks whenever the forum is
migrated or the post is edited, and what it holds depends on when it was
last replaced — anything posted before July 2026 is the four-file package of
section 3, which newer drivers refuse. Run `-verifyroms` on it rather than
assuming. The other well-known copy,
`github.com/Threetwosevensixseven/NexCreator/…/tbblue.zip`, which
ZX-Next-Unite's Help tab used to name, is gone (the repository answers 404).
Building from the upstream repo, as below, is the durable route, and the
only one that is explicit about which six files it produces.

---

## 2. Licensing

The ZX Spectrum Next FPGA repository is published by SpecNext Ltd under the
**GNU General Public License v3**.

**The whole thing is GPLv3, so cloning and re-zipping is fine.**

This is worth stating plainly because "MAME ROM" carries an assumption of
murky legality that simply does not apply here. These are not proprietary
firmware dumps of unclear provenance — they are build artefacts of published,
copyleft-licensed source. You may clone the repo, extract the ROM images,
repackage them, and pass the result on, provided you keep the licence and
attribution intact. That is exactly what the GPL is for.

---

## 3. What you are building: six ROMs

| File | Size | CRC32 | SHA-1 | What it is |
|---|---|---|---|---|
| `boot-30100.bin` | 8192 | `ccbd55ba` | `8b3c2a301f486904d1c74929b94845a7731bf230` | Main IPL, Next core v3.01.00 |
| `boot-30200-ab.bin` | 8192 | `1d16e9d4` | `6f9c8771e5a9ef5a6b52a31b2e65f0698f0f5cfa` | Anti-brick IPL, core v3.02.00 |
| `boot-30204rc.bin` | 8192 | `95118eb6` | `acf5112e831be8c73952b8513fab33a427e88cf8` | Main IPL, core v3.02.04 **release candidate** — what MAME 0.267–0.289 ship as `boot-30204.bin` |
| `boot-30204rc-ab.bin` | 8192 | `96c32007` | `6c9fcbd282f7a18fb5a726386ac6fb9df209c36b` | Anti-brick IPL, core v3.02.04 release candidate — ditto, as `boot-30204-ab.bin` |
| `boot-30204.bin` | 8192 | `ab242e0f` | `a88f1a4dadb4584a2a25d22f1dbb0fe3092349ba` | Main IPL, core v3.02.04 **final** (default BIOS in the final-core table) |
| `boot-30204-ab.bin` | 8192 | `85d69a61` | `7c77cd7f16a37581eff539f9a0fd9a12edd34b78` | Anti-brick IPL, core v3.02.04 final |

### Two ROM tables, one archive

There are two versions of the MAME driver in the wild, and they disagree about
what `boot-30204.bin` means:

| | `boot-30204.bin` / `boot-30204-ab.bin` are… | `boot-30204rc*.bin` | BIOS options for `tbblue` |
|---|---|---|---|
| **Official MAME 0.267 → 0.289**, and `mamedev/mame` master as of 2026-11-11 | the RC pair (CRC `95118eb6` / `96c32007`) | not referenced | 4 |
| **holub fork since 2026-07-08** (the driver's upstream; the table that will presumably reach a future release) | the final pair (CRC `ab242e0f` / `85d69a61`) | the RC pair | 6 |

`mame -listroms tbblue` tells you which one you have: eight `boot-*` lines is
the official table, twelve is the final-core one.

The six-file archive above satisfies **both**, and the reason is worth knowing
because it looks impossible at first: the official table asks for a file named
`boot-30204.bin` with CRC `95118eb6`, and the archive's `boot-30204.bin` has
CRC `ab242e0f`. It works because **MAME looks up members inside a zip by CRC**,
not only by name — it finds the content it wants under `boot-30204rc.bin` and
is satisfied. Measured, not assumed: MAME 0.289 (official table) reports
`romset tbblue is good` against this exact archive, and a holub-fork build with
the final-core table reports the same. So one archive covers the MAME you have
today and the one you will have after the next upgrade. Do **not** "fix" the
archive by dropping the `rc` pair, or by renaming it back: the first breaks
every official release, the second breaks the newer driver.

### The old four-file package

If you built this package before November 2026 you have four files:
`boot-30100.bin`, `boot-30200-ab.bin`, `boot-30204.bin` (CRC `95118eb6`) and
`boot-30204-ab.bin` (CRC `96c32007`). That package is still accepted by
official MAME 0.289 and refused by the newer driver (`boot-30204rc.bin NOT
FOUND`, then a hash mismatch on `boot-30204.bin`). The two 30204 files in it
are the RC pair, under the names the newer driver gives to the final pair — so
to upgrade in place, rename them to `boot-30204rc.bin` / `boot-30204rc-ab.bin`
and add the final pair. Or simply rebuild, which takes a minute.

Older copies obtained elsewhere may also carry `bootrom.fa55357d.bin` and
`bootrom-ab.cfffa702.bin`: byte-for-byte duplicates of `boot-30100.bin` and
`boot-30200-ab.bin` under their upstream names (the filename encodes the git
commit they came from). MAME never looks at them; they are harmless to include
and harmless to drop.

---

## 4. Why "AB", and why 8 KB

### The anti-brick core

The Next has a 16 MB flash chip divided into 32 slots of 512 KB, each able to
hold one FPGA bitstream. The first two slots are reserved:

- **Slot 0 — the anti-brick core.** Deliberately never changed. At power-on
  the FPGA configures itself from this slot first.
- **Slot 1 — the main ZX Next core.** Shortly after power-on the FPGA
  reconfigures from here, and the machine boots normally.

The anti-brick core exists to rescue a machine whose main core has been
corrupted or badly flashed — if slot 1 is broken you can still get far enough
to reflash it. During an anti-brick update the machine may only produce a VGA
picture. `-ab` in the filename means "this is the boot ROM from the anti-brick
core", which is why there are two variants per core version.

### The IPL and the 8 KB / 16 KB business

Each ROM here is the **Initial Program Loader**. It is 8 KB of Z80 code loaded
into internal RAM as part of FPGA configuration, and it is **mirrored across
the 16 KB Spectrum ROM window** at `0x0000–0x3FFF`. On power-up the core
presents itself as a 48K Spectrum, but with the IPL in place of the normal ROM
and DivMMC's SPI interface active.

The IPL's whole job is to bring up the SD card, find `TBBLUE.FW` in the root,
load a module from it to `0x6000`, and jump there — at which point it unmaps
itself and RAM takes over the ROM region. If anything fails, it turns the
border red and prints an error.

You can see this directly in the binaries. Running `strings` over
`boot-30100.bin` turns up its error messages and the filenames it looks for:
`Error initializing SD card!`, `Error mounting SD card!`, `Error opening
TBBLUE.FW file`, `Error reading TBBLUE.FW file`, plus the 8.3 names
`TBBLUE  FW` and `TBBLUE  TBU` and the strings `FAT16` / `FAT32`.

This mirroring is why, in the MAME driver, each 8 KB file is loaded **twice** —
once at `0x0000` and once at `0x2000` — to fill the 16 KB region. It is not a
mistake or padding; it is what the hardware does.

### Identifying the versions

The version labels are not guesses. Beyond MAME's own naming, the binaries
carry build fingerprints:

- `boot-30100.bin` is visibly an older codebase — the `TBBLUE.FW` error-string
  block above, and no toolchain banner.
- `boot-30200-ab.bin` contains `Z88DK` and `2.20`.
- The four v3.02.04 files all contain `Z88DK` and `2.30`, and share later
  strings such as `<null>` that the older ROMs lack.
- RC and final differ in their messages: the RC pair says `SD Card Missing
  TBBLUE.FW`, the final pair `Card Missing TBBLUE.FW`. Small, but it is a
  one-line way to tell an unlabelled 8 KB file apart without hashing it.

So the v3.02.04 files were built with a newer z88dk than the v3.02.00
anti-brick ROM, which in turn is newer than the v3.01.00 main ROM. The
filenames match the actual build lineage.

---

## 5. Provenance: where each file comes from

| Output file | Upstream path | Revision | Method |
|---|---|---|---|
| `boot-30204.bin` | `cores/zxnext/src/rom/bootrom.bin` | commit `dc8d7641` ("CORE VERSION 3.02.04", 2026-05-28, tip of branch `30204`) | copy |
| `boot-30204-ab.bin` | `cores/zxnext/src/rom/bootrom_ab.bin` | commit `dc8d7641` | copy |
| `boot-30204rc.bin` | `cores/zxnext/src/rom/bootrom.bin` | commit `01676e24` (2026-01-27, same branch) | copy |
| `boot-30204rc-ab.bin` | `cores/zxnext/src/rom/bootrom_ab.bin` | commit `01676e24` | copy |
| `boot-30100.bin` | `cores/zxnext/src/rom/bootrom.vhd` | commit `fa55357d7eafb46b98cff3b2dd060f3f5a713edd` | extract from VHDL |
| `boot-30200-ab.bin` | `cores/zxnext/src/rom/bootrom_ab.vhd` | commit `cfffa702a65d1d40fa2e0d06b241c782085c3a9a` | extract from VHDL |

The `30204` branch is read at **two points in its history**: the January
snapshot MAME was originally built against (now "RC"), and the May tip that
says "CORE VERSION 3.02.04" in its commit title (now "final"). Branch `30205`
— a development branch that merged `master` in September 2026 — carries the
same two final binaries; it is not used here, but it is a second witness.

The split in method is the other interesting part. By the time of these
commits the project was committing the assembled boot ROM as a plain `.bin`
alongside the VHDL, so those four are a straight copy.

The older revisions predate that. Back then the ROM contents existed **only
inside the VHDL source**, as an array of hex byte literals that Xilinx ISE
would synthesise into block RAM initialisation. To get a usable binary you
have to parse those literals back out in order and write them to a file. Step
2 below does exactly that.

This is also why those two files carry commit hashes in their upstream names
rather than version tags — they were pulled from specific points in history,
not from releases.

---

## 6. Step 1 — Fetch the sources

### Prerequisites

You need **Git** and **Python 3**. Both are cross-platform; the extraction
script in step 2 is the same file on every OS.

On Linux, use your package manager. On macOS, `git` ships with the Xcode
command line tools and Python 3 is available via Homebrew.

On Windows:

- **Git** — install [Git for Windows](https://git-scm.com/download/win). This
  is the only real requirement; you do *not* need WSL, Cygwin or MSYS for the
  PowerShell version below.
- **Python 3** — install from [python.org](https://www.python.org/downloads/)
  or the Microsoft Store. Tick *"Add Python to PATH"* during setup. Verify with
  `py --version`.
- **A working folder.** This guide uses `C:\temp`, which **Windows does not
  create for you** — the system temp directory is elsewhere, under your user
  profile. Make it and move into it before anything else:

  ```powershell
  New-Item -ItemType Directory -Force -Path 'C:\temp' | Out-Null
  Set-Location 'C:\temp'
  ```

  Then save the scripts there. Both scripts build everything relative to the
  current directory, so `tbblue-build\` will appear inside whichever folder you
  run them from. Any folder works — just substitute it consistently.

Pick the variant below that matches your shell. They do exactly the same
thing and produce identical output.

### Linux / macOS (bash)

Save as `fetch-roms.sh`, then `chmod +x fetch-roms.sh && ./fetch-roms.sh`.

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="https://gitlab.com/SpectrumNext/ZX_Spectrum_Next_FPGA.git"
WORKDIR="$(pwd)/tbblue-build"
SRC="$WORKDIR/ZX_Spectrum_Next_FPGA"
OUT="$WORKDIR/out"

# Every revision is pinned to an immutable commit SHA.
#
# Two snapshots of the "30204" branch are needed, and NEITHER may be written
# as the branch name. 01676e24 (2026-01-27) holds the boot ROM pair MAME has
# shipped as "v3.02.04" since 0.267 -- the driver now calls it v3.02.04 (RC).
# dc8d7641 (2026-05-28, "CORE VERSION 3.02.04") holds the final pair, which
# the driver calls v3.02.04 since July 2026. The branch tip can move again;
# a SHA cannot (sibling branches 30202/30203/30205 exist for the same reason).
COMMIT_30204="dc8d76419a6968d6c93bfa5f62231a40ad8b4221"
COMMIT_30204_RC="01676e244146621974e916b813bb254c6a2fb4a0"
COMMIT_BOOTROM="fa55357d7eafb46b98cff3b2dd060f3f5a713edd"
COMMIT_BOOTROM_AB="cfffa702a65d1d40fa2e0d06b241c782085c3a9a"

mkdir -p "$OUT"

# Clone the full history: every revision we want is an old commit.
# Do NOT use --depth 1 here, the older revisions would be unreachable.
if [ ! -d "$SRC/.git" ]; then
    echo "==> Cloning $REPO"
    git clone "$REPO" "$SRC"
else
    echo "==> Repo already present, fetching updates"
    git -C "$SRC" fetch --all --tags
fi

# Resolve a user-supplied revision to something git can actually address.
# Accepts a raw SHA, a local branch/tag, or a remote branch given bare.
resolve_rev() {
    local rev="$1" candidate
    for candidate in "$rev" "origin/$rev" "refs/tags/$rev" "refs/remotes/origin/$rev"; do
        if git -C "$SRC" rev-parse --verify --quiet "${candidate}^{commit}" >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    {
        echo "ERROR: cannot resolve revision '$rev' in $SRC"
        echo "Remote branches available:"
        git -C "$SRC" branch -r
        echo "Tags available:"
        git -C "$SRC" tag
    } >&2
    return 1
}

REV_30204="$(resolve_rev "$COMMIT_30204")"
REV_30204_RC="$(resolve_rev "$COMMIT_30204_RC")"
REV_BOOTROM="$(resolve_rev "$COMMIT_BOOTROM")"
REV_BOOTROM_AB="$(resolve_rev "$COMMIT_BOOTROM_AB")"

# --- v3.02.04 final pair: committed binaries, just extract them -------------
echo "==> Extracting v3.02.04 (final) binaries"
git -C "$SRC" show "$REV_30204:cores/zxnext/src/rom/bootrom.bin"    > "$OUT/boot-30204.bin"
git -C "$SRC" show "$REV_30204:cores/zxnext/src/rom/bootrom_ab.bin" > "$OUT/boot-30204-ab.bin"

# --- v3.02.04 RC pair: the same two paths, four months earlier ---------------
echo "==> Extracting v3.02.04 (RC) binaries"
git -C "$SRC" show "$REV_30204_RC:cores/zxnext/src/rom/bootrom.bin"    > "$OUT/boot-30204rc.bin"
git -C "$SRC" show "$REV_30204_RC:cores/zxnext/src/rom/bootrom_ab.bin" > "$OUT/boot-30204rc-ab.bin"

# --- oldest pair: no .bin existed yet, pull the VHDL for step 2 --------------
echo "==> Extracting VHDL sources for v3.01.00 / v3.02.00-ab"
git -C "$SRC" show "$REV_BOOTROM:cores/zxnext/src/rom/bootrom.vhd"       > "$OUT/bootrom.vhd"
git -C "$SRC" show "$REV_BOOTROM_AB:cores/zxnext/src/rom/bootrom_ab.vhd" > "$OUT/bootrom_ab.vhd"

echo
echo "Done. Files are in: $OUT"
echo "Next: run  python3 extract-vhdl-roms.py \"$OUT\""
```

**Why every revision is a commit SHA, and `30204` appears nowhere.**

`30204` looks like a version tag. It is not. It is a **development branch**,
and the repo carries siblings named `30202`, `30203` and `30205` for the same
reason. GitLab's raw URLs give this away: the ones in the original package
README end with `?ref_type=heads`, and `heads` means branch (a tag would say
`ref_type=tags`).

Using it breaks in two separate ways, and the second is far nastier than the
first.

**It does not resolve.** `git clone` stores remote branches as
*remote-tracking* refs named `origin/30204`, not as local branches. Git
resolves a bare revision name against local branches and tags only, so
`git show 30204:path` fails with `fatal: invalid object name '30204'` even
though the branch is right there in the clone. Loud, obvious, easy to fix.

**It moves.** Fix the first problem with `origin/30204` and you get files that
extract cleanly, are exactly 8192 bytes, and are whichever version the tip
holds *today*. This has already happened once: the first edition of this guide
(August 2026) warned that the tip had moved past the snapshot MAME used — and
that moved-on content is precisely the final pair the driver wants *now*, with
the January snapshot demoted to "RC". Both snapshots are needed, so both are
pinned, and nothing about the output of a bare `origin/30204` tells you which
one you got until `-verifyroms` does. A commit SHA is content-addressed and
cannot change meaning; a branch name is a moving pointer. Anything you intend
to reproduce later has to be pinned.

**Why `resolve_rev` survives anyway.** With all four revisions now SHAs it is a
no-op, but it costs nothing and guards against anyone editing a branch or tag
name back into the table. It tries the bare name, then `origin/<name>`, then
the fully qualified tag and remote forms, and returns whichever git accepts.
On failure it prints the available branches and tags.

**Why the PowerShell version also checks blob IDs.** Pinning a commit is
necessary but not sufficient - a path could be renamed, or someone could mistype
the SHA. Comparing `git rev-parse <rev>:<path>` against a known blob object ID
catches a wrong revision at fetch time with a clear message, rather than letting
it surface as a mystifying hash mismatch two steps later.

**Why a full clone.** `--depth 1` would only give you the tip of the default
branch. The `fa55357d` and `cfffa702` commits are years old and would not be
present, and the script would fail. If clone size is a problem, see
Troubleshooting — a blobless clone (`--filter=blob:none`) is a drop-in
substitute and is what this revision of the guide was verified with.

**Why `git show` rather than `git checkout`.** Using `git show <rev>:<path>`
reads a single blob out of history and writes it to stdout. Nothing is checked
out, the working tree is never switched, and you can pull files from four
different revisions in one pass without any state juggling. It is also immune
to GitLab changing its raw-file URL format, which is the other thing that
tends to rot over time.

### Windows (PowerShell)

Save as `fetch-roms.ps1`. PowerShell blocks unsigned scripts by default, so
run it from a PowerShell window like this:

```powershell
powershell -ExecutionPolicy Bypass -File .\fetch-roms.ps1
```

That bypasses the policy for this one invocation only and changes nothing
system-wide.

```powershell
#Requires -Version 5.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo    = 'https://gitlab.com/SpectrumNext/ZX_Spectrum_Next_FPGA.git'
$WorkDir = Join-Path (Get-Location) 'tbblue-build'
$Src     = Join-Path $WorkDir 'ZX_Spectrum_Next_FPGA'
$Out     = Join-Path $WorkDir 'out'

# Every revision is pinned to an immutable commit SHA.
#
# Two snapshots of the '30204' branch are needed, and NEITHER may be written
# as the branch name. 01676e24 (2026-01-27) holds the boot ROM pair MAME has
# shipped as "v3.02.04" since 0.267 -- the driver now calls it v3.02.04 (RC).
# dc8d7641 (2026-05-28, "CORE VERSION 3.02.04") holds the final pair, which
# the driver calls v3.02.04 since July 2026. The branch tip can move again;
# a SHA cannot (sibling branches 30202/30203/30205 exist for the same reason).
$Commit30204     = 'dc8d76419a6968d6c93bfa5f62231a40ad8b4221'
$Commit30204Rc   = '01676e244146621974e916b813bb254c6a2fb4a0'
$CommitBootrom   = 'fa55357d7eafb46b98cff3b2dd060f3f5a713edd'
$CommitBootromAb = 'cfffa702a65d1d40fa2e0d06b241c782085c3a9a'

# Files we want, as: revision, path inside the repo, output name
$Wanted = @(
    @{ Rev = $Commit30204;     Path = 'cores/zxnext/src/rom/bootrom.bin';    Out = 'boot-30204.bin'      },
    @{ Rev = $Commit30204;     Path = 'cores/zxnext/src/rom/bootrom_ab.bin'; Out = 'boot-30204-ab.bin'   },
    @{ Rev = $Commit30204Rc;   Path = 'cores/zxnext/src/rom/bootrom.bin';    Out = 'boot-30204rc.bin'    },
    @{ Rev = $Commit30204Rc;   Path = 'cores/zxnext/src/rom/bootrom_ab.bin'; Out = 'boot-30204rc-ab.bin' },
    @{ Rev = $CommitBootrom;   Path = 'cores/zxnext/src/rom/bootrom.vhd';    Out = 'bootrom.vhd'         },
    @{ Rev = $CommitBootromAb; Path = 'cores/zxnext/src/rom/bootrom_ab.vhd'; Out = 'bootrom_ab.vhd'      }
)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git not found on PATH. Install Git for Windows: https://git-scm.com/download/win'
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null

# Clone the full history: every revision we want is an old commit.
# Do NOT use --depth 1 here, the older revisions would be unreachable.
if (-not (Test-Path (Join-Path $Src '.git'))) {
    Write-Host "==> Cloning $Repo"
    git clone $Repo $Src
    if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
} else {
    Write-Host '==> Repo already present, fetching updates'
    git -C $Src fetch --all --tags
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
}

# Resolve a user-supplied revision to something git can actually address.
# Accepts a raw SHA, a local branch/tag, or a remote branch given bare.
$RevCache = @{}
function Resolve-Rev {
    param([string]$Rev)

    if ($RevCache.ContainsKey($Rev)) { return $RevCache[$Rev] }

    foreach ($candidate in @($Rev, "origin/$Rev", "refs/tags/$Rev", "refs/remotes/origin/$Rev")) {
        git -C $Src rev-parse --verify --quiet "$candidate^{commit}" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $RevCache[$Rev] = $candidate
            return $candidate
        }
    }

    Write-Host ''
    Write-Host "Remote branches available:" -ForegroundColor Yellow
    git -C $Src branch -r
    Write-Host "Tags available:" -ForegroundColor Yellow
    git -C $Src tag
    throw "Cannot resolve revision '$Rev' in $Src"
}

# Expected git blob object IDs of the four committed binaries. These pin the
# content exactly and catch a silently-wrong revision before the hash check
# in step 2. (Not the raw SHA-1s MAME records: git hashes "blob <len>\0" first.)
$ExpectedBlob = @{
    'boot-30204.bin'      = '144d7d002cd640372c47535a4077a99deacf2f23'
    'boot-30204-ab.bin'   = 'df9b289f17bd122de406ba3f493ea434741e15ae'
    'boot-30204rc.bin'    = '2ac7ace3176bcd42e2648e923e2325a08b2ffeb2'
    'boot-30204rc-ab.bin' = '9c683cdb6b53960daa838486a43650bbd5f31f61'
}

foreach ($item in $Wanted) {
    $rev   = Resolve-Rev $item.Rev
    $dest  = Join-Path $Out $item.Out
    $short = $item.Rev.Substring(0, [Math]::Min(8, $item.Rev.Length))
    Write-Host "==> $short ($rev) : $($item.Path)  ->  $($item.Out)"

    # Byte-exact extraction. See the note below on why this is NOT a plain '>'.
    & cmd.exe /c "git -C `"$Src`" show `"${rev}:$($item.Path)`" > `"$dest`""
    if ($LASTEXITCODE -ne 0) { throw "git show failed for $($item.Path) at $rev" }

    if (-not (Test-Path $dest) -or (Get-Item $dest).Length -eq 0) {
        throw "Extraction produced an empty file: $dest"
    }

    if ($ExpectedBlob.ContainsKey($item.Out)) {
        $oid = (git -C $Src rev-parse "${rev}:$($item.Path)").Trim()
        if ($oid -ne $ExpectedBlob[$item.Out]) {
            throw ("Wrong content for $($item.Out): blob $oid, expected " +
                   "$($ExpectedBlob[$item.Out]). The pinned revision no longer " +
                   "holds the expected file.")
        }
    }
}

Write-Host ''
Write-Host "Done. Files are in: $Out"
Write-Host "Next: run  py extract-vhdl-roms.py `"$Out`""
```

> ### The one Windows gotcha worth knowing
>
> **Never redirect binary output with PowerShell's `>` operator.**
>
> PowerShell does not pass native command output through as raw bytes. It
> decodes it into .NET strings using the console encoding, then re-encodes on
> write. For text that is usually harmless. For an 8 KB ROM image it is
> destructive: bytes that are not valid in the current code page get replaced
> with `U+FFFD`, and on Windows PowerShell 5.1 the output also gains a BOM and
> CRLF line-ending translation. The result is a file of the wrong size, full of
> wrong bytes, that will fail `-verifyroms` in a way that looks baffling.
>
> That is why the script shells out to `cmd.exe /c "... > file"`. `cmd`'s
> redirection is a raw byte pipe and is byte-exact.
>
> The same trap applies if you ever hand-run one of these commands. In
> PowerShell, `git show <rev>:<path> > out.bin` **will** corrupt the file.
> If you prefer to avoid `cmd` entirely, the alternative is to check the
> revision out and copy the file:
>
> ```powershell
> git -C $Src checkout --quiet dc8d76419a6968d6c93bfa5f62231a40ad8b4221
> Copy-Item "$Src\cores\zxnext\src\rom\bootrom.bin" "$Out\boot-30204.bin"
> ```
>
> `Copy-Item` is byte-exact. The downside is that you end up switching the
> working tree several times and finishing in a detached HEAD state, which is
> why the script does not do this by default.

Everything explained in the bash notes above — the full clone, the use of
`git show` — applies identically here.

---

## 7. Step 2 — Extract the ROMs from VHDL and verify (python)

**This step is identical on every platform.** The script uses only the standard
library and `pathlib`, so there is no separate Windows version — save the same
file and run it.

| | |
|---|---|
| Linux / macOS | `python3 extract-vhdl-roms.py tbblue-build/out` |
| Windows | `py extract-vhdl-roms.py tbblue-build\out` |

Windows accepts forward slashes in the path argument too, so
`py extract-vhdl-roms.py tbblue-build/out` works as well.

Save as `extract-vhdl-roms.py`.

```python
#!/usr/bin/env python3
"""Extract the two oldest Next boot ROMs from the FPGA repo's VHDL sources and
verify all six ROMs against the hashes MAME expects.

Usage:
    python3 extract-vhdl-roms.py [output-directory]
    py       extract-vhdl-roms.py [output-directory]

Defaults to tbblue-build/out relative to the current directory.
Run fetch-roms.sh / fetch-roms.ps1 first to populate that directory.
"""

import hashlib
import re
import sys
import zlib
from pathlib import Path

# VHDL source -> output binary
EXTRACT = [
    ("bootrom.vhd",    "boot-30100.bin"),
    ("bootrom_ab.vhd", "boot-30200-ab.bin"),
]

# Everything MAME wants, including the four files copied verbatim in step 1.
# The two "rc" files are the v3.02.04 boot ROMs MAME shipped from 0.267 on;
# the driver relabelled them "v3.02.04 (RC)" when the final core landed.
EXPECTED = {
    "boot-30100.bin":      ("ccbd55ba", "8b3c2a301f486904d1c74929b94845a7731bf230"),
    "boot-30200-ab.bin":   ("1d16e9d4", "6f9c8771e5a9ef5a6b52a31b2e65f0698f0f5cfa"),
    "boot-30204rc.bin":    ("95118eb6", "acf5112e831be8c73952b8513fab33a427e88cf8"),
    "boot-30204rc-ab.bin": ("96c32007", "6c9fcbd282f7a18fb5a726386ac6fb9df209c36b"),
    "boot-30204.bin":      ("ab242e0f", "a88f1a4dadb4584a2a25d22f1dbb0fe3092349ba"),
    "boot-30204-ab.bin":   ("85d69a61", "7c77cd7f16a37581eff539f9a0fd9a12edd34b78"),
}

ROM_SIZE = 8192
BYTE_LITERAL = re.compile(r'[xX]"([0-9A-Fa-f]{2})"')


def extract(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SystemExit(
            f"Missing input: {src}\n"
            f"Run the fetch script first, and check you passed the right "
            f"output directory."
        )
    text = src.read_text(errors="replace")
    data = bytes(int(h, 16) for h in BYTE_LITERAL.findall(text))
    if len(data) != ROM_SIZE:
        raise SystemExit(
            f"{src.name}: got {len(data)} bytes, expected {ROM_SIZE}.\n"
            f"The VHDL at this revision probably uses a different literal "
            f"style; adjust BYTE_LITERAL to match."
        )
    dst.write_bytes(data)
    print(f"  extracted {src.name} -> {dst.name}")


def verify(path: Path) -> bool:
    crc_want, sha_want = EXPECTED[path.name]
    data = path.read_bytes()
    crc_got = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    sha_got = hashlib.sha1(data).hexdigest()
    ok = crc_got == crc_want and sha_got == sha_want
    print(f"  [{'OK ' if ok else 'BAD'}] {path.name:20} crc32={crc_got}  sha1={sha_got}")
    if not ok:
        print(f"        expected crc32={crc_want} sha1={sha_want}")
    return ok


def main() -> None:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "tbblue-build/out")

    if not outdir.is_dir():
        raise SystemExit(
            f"Not a directory: {outdir.resolve()}\n"
            f"Pass the path to the 'out' folder created by the fetch script."
        )

    print(f"Extracting from VHDL in {outdir.resolve()}")
    for src_name, dst_name in EXTRACT:
        extract(outdir / src_name, outdir / dst_name)

    print("\nVerifying against MAME hashes:")
    results = []
    for name in EXPECTED:
        path = outdir / name
        if not path.exists():
            print(f"  [MISSING] {name} - did the fetch script run?")
            results.append(False)
            continue
        results.append(verify(path))

    if not all(results):
        raise SystemExit("\nVerification FAILED. Do not package these.")
    print("\nAll six ROMs verified.")
    print(f"\nNext: zip the six boot-*.bin files from {outdir.resolve()}")
    print("into tbblue.zip (no subdirectory) and put it in MAME's roms folder.")


if __name__ == "__main__":
    main()
```

**How the extraction works.** `BYTE_LITERAL` matches VHDL hex byte literals of
the form `X"F3"` (case-insensitive on both the `X` and the digits).
`findall` returns them in source order, which is ROM address order, so
converting each to an int and packing them into a `bytes` object reproduces the
image directly.

**Why the length assertion matters.** The regex is deliberately simple and
would happily match hex literals appearing anywhere else in the file —
comments, generics, unrelated constants. The `== 8192` check is what catches
that. If it trips, you have either over-matched or the revision uses a
different literal style, and the fix is to narrow the regex to the array body.
Never skip this check.

**Why verify all six rather than just the two extracted.** The four v3.02.04
files came from step 1 and were not touched here, but checking them costs
nothing and means one run tells you whether the *entire set* is packageable.
A truncated `git show`, a wrong revision, or a stale file from an earlier
attempt all get caught before you zip anything — and the RC and final pairs
are the same size, the same toolchain and nearly the same strings, so a hash is
the only honest way to tell them apart.

**Why CRC32 and SHA-1 specifically.** Those are the two hashes MAME records in
its ROM definitions and checks at load time. Matching them here means
`-verifyroms` will pass; matching anything else proves nothing about MAME.

---

## 8. Step 3 — Package it

Run these **one at a time** so you can check each result before moving on.
The Windows commands assume you are in the folder holding the scripts —
`C:\temp` throughout this guide, which you created in section 0 since Windows
has no such folder by default. The Linux/macOS commands assume the directory
containing `tbblue-build`.

### 8.0 — Make sure you are in the working folder

Windows has no `C:\temp` by default, so if you skipped section 0, create it now
and put the scripts there:

```powershell
New-Item -ItemType Directory -Force -Path 'C:\temp' | Out-Null
Set-Location 'C:\temp'
```

`-Force` here means "do not complain if it already exists", so the command is
safe to re-run. Confirm you are in the right place and the scripts are present:

```powershell
Get-Location
Get-ChildItem *.ps1, *.py
```

`C:\temp` is only a convention for this guide — any folder works, as long as
you substitute it consistently everywhere below.

### 8.1 — Confirm the six ROMs are present and verified

Re-running the extraction script is cheap and idempotent, and it is the only
thing standing between you and a bad package.

```powershell
py extract-vhdl-roms.py "C:\temp\tbblue-build\out"
```

```bash
python3 extract-vhdl-roms.py tbblue-build/out
```

Do not continue unless the last line reads `All six ROMs verified.`
Any `[BAD]` or `[MISSING]` line means go back to section 6 or 7 —
see Troubleshooting for what each symptom indicates.

### 8.2 — Build the zip

**Windows (PowerShell).** `Compress-Archive` is built in from PowerShell 5.0
onwards, so there is nothing extra to install:

```powershell
$O = 'C:\temp\tbblue-build\out'
$Files = 'boot-30100.bin','boot-30200-ab.bin',
         'boot-30204rc.bin','boot-30204rc-ab.bin',
         'boot-30204.bin','boot-30204-ab.bin' |
         ForEach-Object { Join-Path $O $_ }
Compress-Archive -Path $Files -DestinationPath 'C:\temp\tbblue.zip' -Force
```

**Linux / macOS (bash).**

```bash
cd tbblue-build/out
zip -9 -X ../tbblue.zip \
    boot-30100.bin boot-30200-ab.bin \
    boot-30204rc.bin boot-30204rc-ab.bin \
    boot-30204.bin boot-30204-ab.bin
cd ../..
```

Passing the six *file* paths rather than the `out` folder is what keeps the
members at the archive root. `-Force` lets it overwrite an existing
`tbblue.zip`; without it `Compress-Archive` refuses.

**Explorer alternative.** Open `C:\temp\tbblue-build\out`, select just the six
`boot-*.bin` files, right-click → **Send to → Compressed (zipped) folder**, and
rename the result to `tbblue.zip`. Perfectly valid. Do *not* right-click the
`out` folder itself — that nests everything one level down and MAME will not
find it.

### 8.3 — Check the archive structure

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::OpenRead('C:\temp\tbblue.zip').Entries |
    Select-Object FullName, Length
```

```bash
unzip -l tbblue-build/tbblue.zip
```

You want six entries, each **8192** bytes, each a bare filename with no `\` or
`/` in it. A path component means you compressed the folder rather than the
files — redo 8.2.

### 8.4 — Install it

```powershell
Copy-Item 'C:\temp\tbblue.zip' 'C:\mame\roms\tbblue.zip'
```

```bash
cp tbblue-build/tbblue.zip ~/mame/roms/tbblue.zip
```

Adjust the destination to wherever your MAME binary lives. On Windows the
`roms` folder sits next to `mame.exe`, unless you have redirected `rompath` in
`mame.ini`. Copy the zip **as-is** — do not extract it. MAME reads the archive
directly. ZX-Next-Unite's own MAME lives in `downloads\mame`, so its roms
folder is `downloads\mame\roms`.

### 8.5 — Verify

```powershell
cd C:\mame
.\mame.exe -verifyroms tbblue
```

```bash
cd ~/mame
./mame -verifyroms tbblue
```

`romset tbblue is good` means you are finished. MAME matches on CRC32 and
SHA-1, so a pass is conclusive — there is nothing further to confirm. Run it
from MAME's own folder: `rompath` defaults to `roms`, which is resolved
against the *current directory*, so from anywhere else MAME answers `romset
"tbblue" not found!` about a perfectly good archive.

On PowerShell the leading `.\` is required for an executable in the current
directory. Plain `mame -verifyroms tbblue` fails with a command-not-found error
that has nothing to do with your ROMs.

### The three rules MAME enforces

1. The archive must be named **`tbblue.zip`** — MAME resolves ROM archives by
   machine name.
2. Files must sit at the **root of the archive**, not inside a subdirectory.
3. Contents are matched by **CRC32 and SHA-1**. Inside an archive MAME finds a
   member by CRC when the name it wants carries another CRC (section 3), so the
   file *names* are only load-bearing where two members share a CRC — never the
   case here — while timestamps and compression level are irrelevant. (`-9` is
   maximum compression and `-X` drops extra filesystem metadata; neither is
   required.)

Extra members are ignored, so you can safely include a README, a licence copy,
or the intermediate `.vhd` files. Adding a short README recording the commit
SHAs and hashes is worth the ten seconds — it saves rediscovering all of this
in two years.

---


## 9. Using it: one zip, four machines

MAME defines four Next variants, and **all of them share this single romset**.
In the driver, `specnext_ks1` and `specnext_ks2` are `#define`d directly to the
`tbblue` ROM definition. `specnext_ks3` has its own definition, but every file
it references is already in the same archive. On top of that, all three are
declared as *clones* of `tbblue`, and MAME falls back to the parent's archive
when a clone's own archive is absent.

Net effect: one `tbblue.zip` in `roms/` and everything works.

| Machine | Description | Year | SpecNext board | RAM |
|---|---|---|---|---|
| `tbblue` | ZX Spectrum Next: Emulators ID | 2017 | — | — |
| `specnext_ks1` | ZX Spectrum Next: KS1 | 2020 | Issue 2 | 1 MB |
| `specnext_ks2` | ZX Spectrum Next: KS2 | 2023 | Issue 4 | 2 MB |
| `specnext_ks3` | ZX Spectrum Next: KS3 | 2025 | Issue 5 | 4 MB |

They differ in machine ID, board issue and RAM size — reflecting the three
Kickstarter hardware generations — not in ROM content. That is the whole reason
one archive suffices. (The board names are SpecNext's own, as the 26.11a
distribution page uses them; the driver carries its own small integer for the
board-ID register.)

### Selecting a core version

```bash
mame specnext_ks2 -bios v30204ab -hard1 /path/to/cspect-next-2gb.img
```

With the **final-core table** (holub fork since 2026-07-08):

| BIOS option | MAME label | Loaded at `0x0000` | Loaded at `0x2000` |
|---|---|---|---|
| `v30100` | Next Core v3.01.00 | `boot-30100.bin` | `boot-30100.bin` |
| `v30200ab` | Anti-Brick Core v3.02.00 | `boot-30200-ab.bin` | `boot-30100.bin` |
| `rc30204` | Next Core v3.02.04 (RC) | `boot-30204rc.bin` | `boot-30204rc.bin` |
| `rc30204ab` | Anti-Brick Core v3.02.04 (RC) | `boot-30204rc-ab.bin` | `boot-30204rc.bin` |
| `v30204` *(default)* | Next Core v3.02.04 | `boot-30204.bin` | `boot-30204.bin` |
| `v30204ab` | Anti-Brick Core v3.02.04 | `boot-30204-ab.bin` | `boot-30204.bin` |

With the **official table** (MAME 0.267 → 0.289) there are only `v30100`,
`v30200ab`, `v30204` (default) and `v30204ab`, and the last two load the RC
content — `boot-30204rc.bin` / `boot-30204rc-ab.bin` in this archive — under
the names `boot-30204.bin` / `boot-30204-ab.bin`.

Two things to notice.

**The plain entries load the same file twice** — that is the 16 KB
mirroring described in section 4.

**The anti-brick entries do not.** They put the AB ROM in the lower half and
the *matching non-AB* ROM in the upper half. This is why `boot-30100.bin` is
still required even if the only thing you care about is the v3.02.00
anti-brick core: `v30200ab` pairs `boot-30200-ab.bin` with it. You cannot trim
the package down to just the AB files.

`specnext_ks3` offers only the v3.02.04 entries (`rc30204`, `rc30204ab`,
`v30204`, `v30204ab` in the final-core table; `v30204`, `v30204ab` in the
official one), since the KS3 hardware postdates the earlier cores.

### You also need an SD card image

The ROMs get you a booting machine and nothing more. Supply a NextZXOS image
with `-hard1`. Images from <https://zxnext.uk/hosted/> are known to work with
both MAME and CSpect, and ZX-Next-Unite's "Download NextZXOS image" fetches
one of those.

**The official 26.11a emulator image does not boot in MAME.** System/Next
26.11a publishes `sn-emulator-26.11a.zip` ("image for emulators (1Gb image)"):
a 1 GB `cspect-next-1gb.img` plus CSpect's two ROM files, `enNextZX.rom` and
`enNxtmmc.rom`. CSpect boots straight from those ROMs and never runs
`TBBLUE.FW`, so the firmware path MAME takes was never exercised. Measured on
MAME 0.289 and a holub-fork build, on `tbblue`, `specnext_ks2` and
`specnext_ks3`, with this archive: the IPL loads `TBBLUE.FW` from that image
(so the boot ROMs are fine), the firmware's "ZX Spectrum Next Configuration"
screen comes up, and it stops with `Error opening 'menu.ini/.def'!` on a red
border — even though `/machines/next/MENU.DEF` is in the image and identical
to the repository's. Two swaps narrow it down: firmware **1.46 copied onto a
zxnext.uk 2 GB image boots to NextZXOS** in the same setup, and the older
firmware 1.45 copied onto the 26.11a image fails exactly the same way. So it is
something about that image's layout that MAME's emulated card and the
firmware disagree on, not the firmware and not these ROMs. The cause was not
identified here. One measured difference, for whoever picks this up: the
1 GB image is FAT32 with 32,758 clusters of 32 KB, while the zxnext.uk images
are FAT32 with about 131,000 clusters (16 KB each on the 2 GB image, 32 KB
on the 4 GB one) — the 1 GB volume sits below the 65,525-cluster count at
which the classic rule calls a volume FAT32, a shape ZX-Next-Unite's own
image reader had to learn to recognise from the boot sector instead
(`read_image_fat_usage`, 9.7.41). Whether the firmware's FAT driver trips
on that was not checked. Until it is, keep a zxnext.uk image for MAME; its firmware
and NextZXOS can be brought up to 26.11a by copying the distribution's files
onto it (the firmware half of that was the swap just described; a full
distribution copy was not tested here).

---

## 10. Troubleshooting

**Byte count assertion fails in step 2.** The VHDL at that revision uses a
different hex literal style than `X"hh"`, or the regex is picking up literals
from outside the ROM array. Open the `.vhd`, find the array body, and narrow
`BYTE_LITERAL` accordingly. The 8192-byte length and the SHA-1 are your ground
truth — trust them over the script.

**`mame -verifyroms tbblue` reports a wrong CRC.** Almost always one of: the
correct file under the wrong name, a `git show` that hit the wrong revision, or
a VHDL extraction that over-matched. Re-run step 2, which will pinpoint which
of the six is bad.

**`mame -verifyroms tbblue` reports files missing.** Check the zip has no
enclosing directory — `unzip -l tbblue.zip` should show bare filenames with no
path component.

**`mame -verifyroms tbblue` reports `boot-30204rc.bin NOT FOUND`.** You have a
MAME with the final-core table and a four-file archive from before November
2026. Rebuild with the current scripts, or see "The old four-file package" in
section 3 for the rename-and-add route.

**`-verifyroms` on MAME 0.289 says `good` although its `boot-30204.bin` wants
CRC `95118eb6` and the archive's carries `ab242e0f`.** Not a bug: MAME found
the content it wanted under `boot-30204rc.bin` (section 3). The archive is
right. Leave it alone.

**`fatal: invalid object name '30204'`.** You are running an old copy of the
script that referenced the branch name. Current versions pin commits
`dc8d7641` and `01676e24` instead. See section 6.

**The two `rc` files verify as BAD while `boot-30204.bin` / `boot-30204-ab.bin`
are OK, or the other way round.** The two pairs were swapped — someone
"corrected" the script to read both from the same revision, or an old
four-file build's outputs were renamed the wrong way. Step 2's table says
which file holds which content; the RC pair hashes `95118eb6` / `96c32007`,
the final pair `ab242e0f` / `85d69a61`. Pull any of the four by blob ID instead,
which is exact:

```bash
git -C <clone> cat-file blob 144d7d002cd640372c47535a4077a99deacf2f23 > boot-30204.bin
git -C <clone> cat-file blob df9b289f17bd122de406ba3f493ea434741e15ae > boot-30204-ab.bin
git -C <clone> cat-file blob 2ac7ace3176bcd42e2648e923e2325a08b2ffeb2 > boot-30204rc.bin
git -C <clone> cat-file blob 9c683cdb6b53960daa838486a43650bbd5f31f61 > boot-30204rc-ab.bin
```

On Windows wrap each in `cmd /c "... > file"`, per the binary redirection note
in section 6. The clone itself is fine; nothing needs re-downloading.

**A pinned revision stops resolving.** If upstream force-pushes, a commit can
vanish from the clone. Search by content instead. These are the git blob object
IDs of the four committed binaries (note these are *not* the raw-content SHA-1s
MAME records, since git prefixes `blob <len>\0` before hashing):

| File | git blob OID |
|---|---|
| `boot-30204.bin` | `144d7d002cd640372c47535a4077a99deacf2f23` |
| `boot-30204-ab.bin` | `df9b289f17bd122de406ba3f493ea434741e15ae` |
| `boot-30204rc.bin` | `2ac7ace3176bcd42e2648e923e2325a08b2ffeb2` |
| `boot-30204rc-ab.bin` | `9c683cdb6b53960daa838486a43650bbd5f31f61` |

`git cat-file -t <oid>` tells you whether a blob is present at all, and
`git log --all --oneline --find-object=<oid>` finds the commits that touch it.
Branch `30205` is known to carry the final pair as well.

**Clone is slow or large.** The repo carries FPGA build artefacts.
`git clone --filter=blob:none` gives a blobless clone that still resolves old
commits and fetches blobs on demand, downloading far less up front (about
31 MB of `.git` at the time of writing). It is a drop-in substitution in the
script, and the run that produced this revision of the guide used one.

**Can I use a newer core version (3.03.03)?** Not without driver changes, and
not without sources that are not published yet. MAME's implementation is based
on the v3.02.04 core and has no BIOS entry for anything newer; the 3.03 core
exists only as the `TBBLUE.TBU` bitstream in the distribution (section 1).

**MAME boots the 26.11a emulator image to `Error opening 'menu.ini/.def'!`.**
Known; it is the image, not the ROMs. See the end of section 9.

**Do I need the two `bootrom*.bin` duplicates?** No. They are provenance
copies of files already present under MAME's names. Include them if you want
the archive to document its own origins; drop them if you want it minimal.

### Windows-specific

**"running scripts is disabled on this system".** PowerShell's default
execution policy. Run the script as
`powershell -ExecutionPolicy Bypass -File .\fetch-roms.ps1` rather than
changing the policy globally.

**Files come out the wrong size, or `-verifyroms` fails on files you copied by
hand.** You almost certainly used PowerShell's `>` to redirect binary output.
See the boxed note in section 6 — it silently corrupts the bytes. Re-run the
script, which routes through `cmd.exe` for exactly this reason.

**`git` or `py` not recognised.** They are not on `PATH`. Reinstall Git for
Windows accepting the default PATH option, and reinstall Python with *"Add
Python to PATH"* ticked. Reopen your terminal afterwards — an existing window
keeps the old environment.

**The zip has a folder inside it.** You compressed the directory rather than
the six files. `Compress-Archive -Path` must receive the file paths, not the
folder. Check with:
`[IO.Compression.ZipFile]::OpenRead('tbblue.zip').Entries | Select FullName` —
every entry should be a bare filename.

**`Set-Location : Cannot find path 'C:\temp'`.** The folder does not exist.
Windows has no `C:\temp` by default — the real system temp directory is under
your user profile, at `%TEMP%`, which is not the same thing. Create it:

```powershell
New-Item -ItemType Directory -Force -Path 'C:\temp'
```

**`can't open file '...\extract-vhdl-roms.py': [Errno 2] No such file or
directory`.** Python is looking in your *current* directory and the script is
not there. Check with `Get-Location` and `Get-ChildItem *.py`. Either
`Set-Location 'C:\temp'` first, or give Python the full path to the script.

**Paths in the commands do not match your setup.** Every `C:\temp` in this
guide is just a convention. If you put things elsewhere, substitute it in
*every* command — mixing the two is the usual cause of "file not found" halfway
through. `Get-Location` tells you where you actually are.

**Antivirus quarantines the `.bin` files.** Occasionally happens with small
unsigned binaries from a fresh clone. Add the `tbblue-build` folder to your
exclusions, or verify the SHA-1s and restore from quarantine.

---

## 11. References

- FPGA sources (GPLv3): <https://gitlab.com/SpectrumNext/ZX_Spectrum_Next_FPGA>
  — branch `30204`, commits `dc8d7641` (final) and `01676e24` (RC)
- System/Next distribution repo: <https://gitlab.com/thesmog358/tbblue>
  (`TBBLUE.TBU` = the compiled core, 3.03.03 since 2026-11-08)
- Latest distribution (System/Next 26.11a): <https://www.specnext.com/latestdistro/>
- MAME driver: `src/mame/sinclair/next/specnext.cpp`
- The final-core ROM table: holub/mame commit `51b5fd545d7589a9249813eeb5aedf18a5a3fae4`
  (2026-07-08, "sinclair/next/specnext.cpp: Added final v30204 BIOS options")
- SpecNext wiki, MAME setup: <https://wiki.specnext.dev/MAME:Installing>
- Next boot sequence: <https://wiki.specnext.dev/Boot_Sequence>

MAME has supported the ZX Spectrum Next since version 0.267, based on the
v3.02.04 core.

### Revision history

- **2026-08-27** — first edition: four ROMs, pinned against the `30204` branch
  snapshot MAME was built from.
- **2026-11-11** — six ROMs: the final v3.02.04 pair added under MAME's names,
  the earlier pair kept as `boot-30204rc*.bin`; verified against MAME 0.289
  (official table) and a holub-fork build (final-core table); the System/Next
  26.11a emulator image tested and documented as not booting in MAME.
