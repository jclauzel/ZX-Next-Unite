# How to (Re)Build `tbblue.zip` for MAME

Rebuilding the ZX Spectrum Next boot-ROM package used by MAME's `tbblue`,
`specnext_ks1`, `specnext_ks2` and `specnext_ks3` machines — with an
explanation of what each file actually is and why the package is put together
the way it is.

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

# 1. clone the FPGA repo and pull the four source files
powershell -ExecutionPolicy Bypass -File .\fetch-roms.ps1

# 2. decode the two VHDL-embedded ROMs and verify all four
py extract-vhdl-roms.py "C:\temp\tbblue-build\out"

# 3. package
$O = 'C:\temp\tbblue-build\out'
$Files = 'boot-30100.bin','boot-30200-ab.bin','boot-30204.bin','boot-30204-ab.bin' |
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
zip -9 -X ../tbblue.zip boot-30100.bin boot-30200-ab.bin boot-30204.bin boot-30204-ab.bin
cp ../tbblue.zip ~/mame/roms/tbblue.zip
cd ~/mame && ./mame -verifyroms tbblue
```

Two checkpoints decide whether it worked:

- Step 2 must end with `All four ROMs verified.`
- Step 4 must print `romset tbblue is good`.

Anything else, see section 10.

Then boot it — you also need a NextZXOS SD card image:

```
mame specnext_ks2 -hard1 /path/to/nextzxos.img
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
| `gitlab.com/thesmog358/tbblue` | The **NextZXOS distribution** — everything that goes *on the SD card*: the OS, the machine ROMs, `config.ini`, docs, demos. |
| `gitlab.com/SpectrumNext/ZX_Spectrum_Next_FPGA` | The **official FPGA cores repository** — the VHDL for the Next itself, and the boot ROMs baked into the core. **This is where our files come from.** |

The package is named `tbblue.zip` purely because `tbblue` is the MAME machine
name for the Next, and MAME looks up ROM archives by machine name. It has
nothing to do with the distribution repo of the same name.

So: the ROMs in this zip get you a machine that boots. The SD card image you
pass with `-hard1` supplies everything after that.

### Where the old download went

The SpecNext wiki's MAME page points at
`https://www.specnext.com/forum/download/file.php?id=1164`. That is a phpBB
attachment ID, and those break whenever the forum is migrated or the post is
edited. It now returns 404 and the wiki text has not been updated. Building
from the upstream repo, as below, is the durable route.

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

## 3. What you are building: four ROMs, not six

If you have obtained a copy of `tbblue.zip` from somewhere, you may find six
`.bin` files inside. Only four are distinct. Hashing them shows two exact
byte-for-byte duplicate pairs:

| File | SHA-1 | |
|---|---|---|
| `boot-30100.bin` | `8b3c2a30…` | |
| `bootrom.fa55357d.bin` | `8b3c2a30…` | **identical to the above** |
| `boot-30200-ab.bin` | `6f9c8771…` | |
| `bootrom-ab.cfffa702.bin` | `6f9c8771…` | **identical to the above** |
| `boot-30204.bin` | `acf5112e…` | |
| `boot-30204-ab.bin` | `6c9fcbd2…` | |

The `bootrom*.bin` pair are the **upstream-named originals**, kept as
provenance — the filename encodes the git commit they were pulled from
(`fa55357d`, `cfffa702`). MAME never looks at them; it only ever asks for the
four `boot-*.bin` names. They are harmless to include and harmless to drop.

The four that matter:

| File | Size | CRC32 | SHA-1 | What it is |
|---|---|---|---|---|
| `boot-30100.bin` | 8192 | `ccbd55ba` | `8b3c2a301f486904d1c74929b94845a7731bf230` | Main IPL, Next core v3.01.00 |
| `boot-30200-ab.bin` | 8192 | `1d16e9d4` | `6f9c8771e5a9ef5a6b52a31b2e65f0698f0f5cfa` | Anti-brick IPL, core v3.02.00 |
| `boot-30204.bin` | 8192 | `95118eb6` | `acf5112e831be8c73952b8513fab33a427e88cf8` | Main IPL, Next core v3.02.04 (MAME default) |
| `boot-30204-ab.bin` | 8192 | `96c32007` | `6c9fcbd282f7a18fb5a726386ac6fb9df209c36b` | Anti-brick IPL, core v3.02.04 |

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
- `boot-30204.bin` and `boot-30204-ab.bin` both contain `Z88DK` and `2.30`, and
  share later strings such as `<null>` that the older ROM lacks.

So the two v3.02.04 files were built with a newer z88dk than the v3.02.00
anti-brick ROM, which in turn is newer than the v3.01.00 main ROM. The
filenames match the actual build lineage.

---

## 5. Provenance: where each file comes from

| Output file | Upstream path | Revision | Method |
|---|---|---|---|
| `boot-30204.bin` | `cores/zxnext/src/rom/bootrom.bin` | commit `01676e24` | copy |
| `boot-30204-ab.bin` | `cores/zxnext/src/rom/bootrom_ab.bin` | commit `01676e24` | copy |
| `boot-30100.bin` | `cores/zxnext/src/rom/bootrom.vhd` | commit `fa55357d7eafb46b98cff3b2dd060f3f5a713edd` | extract from VHDL |
| `boot-30200-ab.bin` | `cores/zxnext/src/rom/bootrom_ab.vhd` | commit `cfffa702a65d1d40fa2e0d06b241c782085c3a9a` | extract from VHDL |

The split in method is the interesting part. By the time of commit `01676e24`,
the project was committing the assembled boot ROM as a plain `.bin` alongside
the VHDL, so those two are a straight copy.

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

# All four revisions are pinned to immutable commit SHAs.
#
# Do NOT use the branch name "30204" here. It is a development branch, not a
# release tag, and its tip has moved on since MAME's ROMs were taken -- it now
# carries different boot ROM content. Commit 01676e24 is the snapshot that
# matches. Sibling branches 30202/30203/30205 exist for the same reason.
COMMIT_30204="01676e244146621974e916b813bb254c6a2fb4a0"
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
REV_BOOTROM="$(resolve_rev "$COMMIT_BOOTROM")"
REV_BOOTROM_AB="$(resolve_rev "$COMMIT_BOOTROM_AB")"

# --- v3.02.04 pair: committed binaries, just extract them ---------------------
echo "==> Extracting v3.02.04 binaries"
git -C "$SRC" show "$REV_30204:cores/zxnext/src/rom/bootrom.bin"    > "$OUT/boot-30204.bin"
git -C "$SRC" show "$REV_30204:cores/zxnext/src/rom/bootrom_ab.bin" > "$OUT/boot-30204-ab.bin"

# --- older pair: no .bin existed yet, pull the VHDL for step 2 ---------------
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
extract cleanly, are exactly 8192 bytes, and are simply *the wrong version*.
The branch tip is currently commit `dc8d7641` (2026-05-28, "CORE VERSION
3.02.04"), whose boot ROMs differ from the snapshot MAME was built against.
The commit that matches is `01676e24` (2026-01-27) - four months earlier on
the same branch. Nothing about the output looks wrong until `-verifyroms`
rejects it.

So the scripts pin all four revisions to immutable commit SHAs. A commit SHA is
content-addressed and cannot change meaning; a branch name is a moving pointer.
Anything you intend to reproduce later has to be pinned.

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
Troubleshooting.

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

# All four revisions are pinned to immutable commit SHAs.
#
# Do NOT use the branch name '30204' here. It is a development branch, not a
# release tag, and its tip has moved on since MAME's ROMs were taken -- it now
# carries different boot ROM content. Commit 01676e24 is the snapshot that
# matches. Sibling branches 30202/30203/30205 exist for the same reason.
$Commit30204     = '01676e244146621974e916b813bb254c6a2fb4a0'
$CommitBootrom   = 'fa55357d7eafb46b98cff3b2dd060f3f5a713edd'
$CommitBootromAb = 'cfffa702a65d1d40fa2e0d06b241c782085c3a9a'

# Files we want, as: revision, path inside the repo, output name
$Wanted = @(
    @{ Rev = $Commit30204;        Path = 'cores/zxnext/src/rom/bootrom.bin';    Out = 'boot-30204.bin'    },
    @{ Rev = $Commit30204;        Path = 'cores/zxnext/src/rom/bootrom_ab.bin'; Out = 'boot-30204-ab.bin' },
    @{ Rev = $CommitBootrom;   Path = 'cores/zxnext/src/rom/bootrom.vhd';    Out = 'bootrom.vhd'       },
    @{ Rev = $CommitBootromAb; Path = 'cores/zxnext/src/rom/bootrom_ab.vhd'; Out = 'bootrom_ab.vhd'    }
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

# Expected git blob object IDs, where known. These pin content exactly and
# catch a silently-wrong revision before the hash check in step 2.
$ExpectedBlob = @{
    'boot-30204.bin'    = '2ac7ace3176bcd42e2648e923e2325a08b2ffeb2'
    'boot-30204-ab.bin' = '9c683cdb6b53960daa838486a43650bbd5f31f61'
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
> git -C $Src checkout --quiet 30204
> Copy-Item "$Src\cores\zxnext\src\rom\bootrom.bin" "$Out\boot-30204.bin"
> ```
>
> `Copy-Item` is byte-exact. The downside is that you end up switching the
> working tree four times and finishing in a detached HEAD state, which is
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
"""Extract 8 KB Next boot ROMs from the FPGA repo's VHDL sources and verify
them against the hashes MAME expects.

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

# Everything MAME wants, including the two files copied verbatim in step 1.
EXPECTED = {
    "boot-30100.bin":    ("ccbd55ba", "8b3c2a301f486904d1c74929b94845a7731bf230"),
    "boot-30200-ab.bin": ("1d16e9d4", "6f9c8771e5a9ef5a6b52a31b2e65f0698f0f5cfa"),
    "boot-30204.bin":    ("95118eb6", "acf5112e831be8c73952b8513fab33a427e88cf8"),
    "boot-30204-ab.bin": ("96c32007", "6c9fcbd282f7a18fb5a726386ac6fb9df209c36b"),
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
    print(f"  [{'OK ' if ok else 'BAD'}] {path.name}  crc32={crc_got}  sha1={sha_got}")
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
    print("\nAll four ROMs verified.")
    print(f"\nNext: zip the four boot-*.bin files from {outdir.resolve()}")
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

**Why verify all four rather than just the two extracted.** The two v3.02.04
files came from step 1 and were not touched here, but checking them costs
nothing and means one run tells you whether the *entire set* is packageable.
A truncated `git show`, a wrong tag, or a stale file from an earlier attempt
all get caught before you zip anything.

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

### 8.1 — Confirm the four ROMs are present and verified

Re-running the extraction script is cheap and idempotent, and it is the only
thing standing between you and a bad package.

```powershell
py extract-vhdl-roms.py "C:\temp\tbblue-build\out"
```

```bash
python3 extract-vhdl-roms.py tbblue-build/out
```

Do not continue unless the last line reads `All four ROMs verified.`
Any `[BAD]` or `[MISSING]` line means go back to section 6 or 7 —
see Troubleshooting for what each symptom indicates.

### 8.2 — Build the zip

**Windows (PowerShell).** `Compress-Archive` is built in from PowerShell 5.0
onwards, so there is nothing extra to install:

```powershell
$O = 'C:\temp\tbblue-build\out'
$Files = 'boot-30100.bin','boot-30200-ab.bin','boot-30204.bin','boot-30204-ab.bin' |
         ForEach-Object { Join-Path $O $_ }
Compress-Archive -Path $Files -DestinationPath 'C:\temp\tbblue.zip' -Force
```

**Linux / macOS (bash).**

```bash
cd tbblue-build/out
zip -9 -X ../tbblue.zip \
    boot-30100.bin boot-30200-ab.bin boot-30204.bin boot-30204-ab.bin
cd ../..
```

Passing the four *file* paths rather than the `out` folder is what keeps the
members at the archive root. `-Force` lets it overwrite an existing
`tbblue.zip`; without it `Compress-Archive` refuses.

**Explorer alternative.** Open `C:\temp\tbblue-build\out`, select just the four
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

You want four entries, each **8192** bytes, each a bare filename with no `\` or
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
directly.

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
SHA-1, so a pass is conclusive — there is nothing further to confirm.

On PowerShell the leading `.\` is required for an executable in the current
directory. Plain `mame -verifyroms tbblue` fails with a command-not-found error
that has nothing to do with your ROMs.

### The three rules MAME enforces

1. The archive must be named **`tbblue.zip`** — MAME resolves ROM archives by
   machine name.
2. Files must sit at the **root of the archive**, not inside a subdirectory.
3. Contents are matched by **CRC32 and SHA-1**, so filenames must be exact,
   while timestamps and compression level are irrelevant. (`-9` is maximum
   compression and `-X` drops extra filesystem metadata; neither is required.)

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

| Machine | Description | Year | Board issue | RAM |
|---|---|---|---|---|
| `tbblue` | ZX Spectrum Next: Emulators ID | 2017 | — | — |
| `specnext_ks1` | ZX Spectrum Next: KS1 | 2020 | 2 | 1 MB |
| `specnext_ks2` | ZX Spectrum Next: KS2 | 2023 | 2 | 2 MB |
| `specnext_ks3` | ZX Spectrum Next: KS3 | 2025 | 3 | 4 MB |

They differ in machine ID, board issue and RAM size — reflecting the three
Kickstarter hardware generations — not in ROM content. That is the whole reason
one archive suffices.

### Selecting a core version

```bash
mame specnext_ks2 -bios v30204ab -hard1 /path/to/cspect-next-2gb.img
```

| BIOS option | MAME label | Loaded at `0x0000` | Loaded at `0x2000` |
|---|---|---|---|
| `v30100` | Next Core v3.01.00 | `boot-30100.bin` | `boot-30100.bin` |
| `v30200ab` | Anti-Brick Core v3.02.00 | `boot-30200-ab.bin` | `boot-30100.bin` |
| `v30204` *(default)* | Next Core v3.02.04 | `boot-30204.bin` | `boot-30204.bin` |
| `v30204ab` | Anti-Brick Core v3.02.04 | `boot-30204-ab.bin` | `boot-30204.bin` |

Two things to notice.

**The two plain entries load the same file twice** — that is the 16 KB
mirroring described in section 4.

**The anti-brick entries do not.** They put the AB ROM in the lower half and
the *matching non-AB* ROM in the upper half. This is why `boot-30100.bin` is
still required even if the only thing you care about is the v3.02.00
anti-brick core: `v30200ab` pairs `boot-30200-ab.bin` with it. You cannot trim
the package down to just the AB files.

`specnext_ks3` offers only `v30204` and `v30204ab`, since the KS3 hardware
postdates the earlier cores.

### You also need an SD card image

The ROMs get you a booting machine and nothing more. Supply a NextZXOS image
with `-hard1`. Images from <https://zxnext.uk/hosted/> are known to work with
both MAME and CSpect; some images published elsewhere do not currently work in
all emulators.

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
of the four is bad.

**`mame -verifyroms tbblue` reports files missing.** Check the zip has no
enclosing directory — `unzip -l tbblue.zip` should show bare filenames with no
path component.

**`fatal: invalid object name '30204'`.** You are running an old copy of the
script that referenced the branch name. Current versions pin commit
`01676e24` instead. See section 6.

**`boot-30204.bin` / `boot-30204-ab.bin` verify as BAD while the two extracted
from VHDL are OK.** You fetched from `origin/30204` rather than the pinned
commit. The branch tip has moved past the snapshot MAME uses, so you get valid
8192-byte ROMs of the wrong version. Pull them by blob ID instead, which is
exact:

```bash
git -C <clone> cat-file blob 2ac7ace3176bcd42e2648e923e2325a08b2ffeb2 > boot-30204.bin
git -C <clone> cat-file blob 9c683cdb6b53960daa838486a43650bbd5f31f61 > boot-30204-ab.bin
```

On Windows wrap each in `cmd /c "... > file"`, per the binary redirection note
in section 6. The clone itself is fine; nothing needs re-downloading.

**A pinned revision stops resolving.** If upstream force-pushes, a commit can
vanish from the clone. Search by content instead. These are the git blob object
IDs of the two committed binaries (note these are *not* the raw-content SHA-1s
MAME records, since git prefixes `blob <len>\0` before hashing):

| File | git blob OID |
|---|---|
| `boot-30204.bin` | `2ac7ace3176bcd42e2648e923e2325a08b2ffeb2` |
| `boot-30204-ab.bin` | `9c683cdb6b53960daa838486a43650bbd5f31f61` |

`git cat-file -t <oid>` tells you whether a blob is present at all, and
`git log --all --oneline --find-object=<oid>` finds the commits that touch it.


**Clone is slow or large.** The repo carries FPGA build artefacts.
`git clone --filter=blob:none` gives a blobless clone that still resolves old
commits and fetches blobs on demand, downloading far less up front. It is a
drop-in substitution in the script.

**Can I use a newer core version?** Not without driver changes. MAME's
implementation is based on the v3.02.04 core. A newer `bootrom.bin` will fail
`-verifyroms`, and the driver has no BIOS entry for it.

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
the four files. `Compress-Archive -Path` must receive the file paths, not the
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
- NextZXOS distribution repo: <https://gitlab.com/thesmog358/tbblue>
- MAME driver: `src/mame/sinclair/next/specnext.cpp`
- SpecNext wiki, MAME setup: <https://wiki.specnext.dev/MAME:Installing>
- Next boot sequence: <https://wiki.specnext.dev/Boot_Sequence>
- Latest distribution: <https://www.specnext.com/latestdistro/>

MAME has supported the ZX Spectrum Next since version 0.267, based on the
v3.02.04 core.
