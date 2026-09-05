# ZxNextRemote — drive a ZX Spectrum Next from PowerShell

`extra/ZxNextRemote/` is a PowerShell **module** (classes + cmdlets, full
`Get-Help`) that wraps ZX-Next-Unite's **NextSync HTTP bridge** in a typed
client, so a script — or an interactive prompt — can browse, download to,
upload from and reorganise the SD card of a Next connected to the bridge
without writing a single HTTP call:

```powershell
$bearer   = ''                       # bridge token, '' = unprotected bridge
$con      = New-ZxNextRemoteConnection -IpAddress 10.0.0.8 -Port 80
$remote   = New-ZxNextRemote -Connection $con -Token $bearer

$sessions = $remote.Session()        # who is connected?
"{0} Next(s) connected" -f $sessions.Current

$mySession  = $remote.ManageSession($sessions.List[0])   # bind the first one
$remoteList = $mySession.Ls()                            # list its SD root
$oneFile    = $mySession.Get($remoteList[0])             # fetch the first file
$remote.Close()                      # disconnect this client (NOT a /forceexit)
```

> **Want to see it working before reading any of this?** Clone
> **[SampleNex](https://github.com/jclauzel/SampleNex)** — a tiny z88dk ZX
> Spectrum Next app wired to this module end to end, where `Ctrl+Shift+B`
> in VS Code builds it, pushes it to your Next, verifies it and runs it.
> It is the worked example for everything below.

It runs on **Windows PowerShell 5.1** (the stock PowerShell of Windows
10/11 — the module's compatibility floor, no 6/7-only syntax anywhere) and
on **PowerShell 7+** (`pwsh`), which is what macOS and Linux users run.
Both editions are exercised by the repo's test suite
(`tests/test_powershell_module.py`).

The wire contract behind it is documented in
[`nextsync/sync/server/HTTP_BRIDGE.md`](../nextsync/sync/server/HTTP_BRIDGE.md)
— routes, security model, and why the bridge is a **LAN-only** convenience,
not a hardened service.

---

## Install

Nothing to compile; the module is the `extra/ZxNextRemote/` folder.

**Option 1 — import by path** (no installation, good for scripts):

```powershell
Import-Module C:\path\to\ZX-Next-Unite\extra\ZxNextRemote\ZxNextRemote.psd1
```

**Option 2 — install for your user** (then `Import-Module ZxNextRemote`
works from anywhere, and PowerShell auto-loads it on first use):

```powershell
# Windows PowerShell 5.1
$dest = "$HOME\Documents\WindowsPowerShell\Modules\ZxNextRemote"
# PowerShell 7+ on Windows
$dest = "$HOME\Documents\PowerShell\Modules\ZxNextRemote"
# PowerShell 7+ on Linux / macOS
$dest = "$HOME/.local/share/powershell/Modules/ZxNextRemote"

Copy-Item -Recurse -Force C:\path\to\ZX-Next-Unite\extra\ZxNextRemote $dest
Import-Module ZxNextRemote
```

**Option 3 — `using module`** (scripts that want the classes and typed
`catch [ZxNextRemoteError]` blocks — see *Classes vs functions* below):

```powershell
using module ZxNextRemote                # installed per option 2, or:
using module C:\path\to\extra\ZxNextRemote\ZxNextRemote.psd1
```

Check it landed, and read the help:

```powershell
Get-Command -Module ZxNextRemote
Get-Help New-ZxNextRemote -Full
Get-Help about_ZxNextRemote              # the class/method reference
```

## Uninstall

```powershell
Remove-Module ZxNextRemote -ErrorAction SilentlyContinue   # this session
Remove-Item -Recurse -Force $dest                          # if installed via option 2
```

(Option 1 needs no uninstall — nothing was copied.)

---

## Prerequisites on the ZX-Next-Unite side

1. **Settings → "Enable NextSync HTTP bridge"** (port 80 by default).
2. **NextSync tab → start the Remote Explorer listen server** (or launch
   the app with `-start-remote-explorer-listener`).
3. On the Next: `.sync5 -listen` (or a ZX Next Remote **Listener**), dialled
   at the PC running Unite.

`nextsync5.py -w` (the standalone server) works too — it is a
single-session host: `/sessions` shows one seat while a Next is
connected, none otherwise.

---

## Classes vs functions

Both surfaces build the **same objects**:

| You use… | You get |
|---|---|
| `Import-Module` + `New-ZxNextRemoteConnection` / `New-ZxNextRemote` / `Test-ZxNextRemoteBridge` | everything — method calls on returned objects need no `using module` |
| `using module` + `[ZxNextRemoteConnection]::new(...)` / `[ZxNextRemote]::new(...)` | the same, plus the class *names*: typed parameters, `catch [ZxNextRemoteError]` |

The class shapes (see `Get-Help about_ZxNextRemote` for every method):

```powershell
using module ZxNextRemote

$bearer = ''                          # see "Bearer token" below
$con    = [ZxNextRemoteConnection]::new('10.0.0.8', 80)
$remote = [ZxNextRemote]::new($con, $bearer)

# or, one line, no intermediate objects:
$s = [ZxNextRemoteSession]::new('10.0.0.8', 80, $bearer)   # the active seat
```

---

## Sessions — when more than one Next is connected

The app's Remote Explorer seats up to **four** `-listen` Nexts at once.
`Session()` lists them; `ManageSession(...)` binds one, and every call on
the bound object rides **that** seat (the `ZXNEXTUNITE-BRIDGE-SESSION`
header) — it never moves the app's own active selection:

```powershell
$sessions = $remote.Session()
$sessions.Current                     # how many Nexts are connected
$sessions.List | Format-Table Sid, Addr, Name, Active

$s1 = $remote.ManageSession($sessions.List[0])   # by entry
$s2 = $remote.ManageSession(2)                   # by sid
$sa = $remote.ManageSession()                    # no argument: the ACTIVE seat
```

Sids are minted once per app run and never reused: a seat that left answers
`SessionGone` (HTTP 410) rather than silently driving a different machine —
call `Session()` again and rebind.

## Working with files

```powershell
$s = $remote.ManageSession()

# list — entries carry Dir/Size/Name and a pre-joined Path
$list = $s.Ls('/games')
$list | Where-Object { -not $_.Dir } | Format-Table

# download
$bytes = $s.Get('/games/boot.bas')            # as [byte[]]
$s.Get('/games/boot.bas', 'C:\tmp\boot.bas')  # to a local file
$s.Get($list[0], 'C:\tmp\first.bin')          # straight from an Ls() entry

# upload, then PROVE it landed intact — an HTTP 200 alone is not proof.
# Verify() asks the Next for the CRC-32 of the file it holds (/crc: the
# Next hashes it, 8 hex digits come back, nothing is pulled over Wi-Fi) and
# compares it with the local file's; on a listener that predates the crc op
# (.sync5 dot < 5.9.2, ZX Next Remote < 1.0.8) it falls back to the size +
# 16-bit checksum read-back (/sum) — slower, but still a real verdict.
$s.Put('build\game.nex', '/home/incoming.nex')
if (-not $s.Verify('build\game.nex', '/home/incoming.nex')) { throw 'bad copy' }

# the same proof, shown: the Next's digest and yours
$s.Crc('/home/incoming.nex').Crc32            # e.g. 1A2B3C4D, computed on the Next
$s.LocalCrc32('build\game.nex')               # 1A2B3C4D too when it landed intact
$s.VerifyCrc('build\game.nex', '/home/incoming.nex')   # strict: $true/$false, or it
                                              # THROWS (NextRefused) when the Next
                                              # cannot be asked — never a silent $false

# manage
$s.Exists('/nextzxos/autoexec.bas')           # $true / $false
$s.MkDir('/backup')
$s.Ren('/games/a.tap', '/games/b.tap')
$s.Rcpy('/games', 'm:/backup/games')          # copy ON the Next, no network hop
$s.RfSize('/games')                           # files / folders / bytes
$s.Free('C'); $s.Drives()

# tell the Next to leave -listen and exit cleanly to BASIC.
# NOTE: a BROADCAST - every seated Next leaves, not just this session's.
$remote.ForceExit()

# done with THIS CLIENT - nothing is sent, the Next stays in -listen
$remote.Close()
```

Remote paths accept an optional drive prefix (`m:/backup`) exactly like
every other NextSync command.

---

## Bearer token (bridge-scoped)

When Unite's **Settings → "Require bearer token"** is on, every request
must carry the shared secret (the `ZXNEXTUNITE-BRIDGE-TOKEN` header). The
token is **bridge-scoped**: one bridge = one connection object = one token.
Set it once — on the connection, or through the `ZxNextRemote` constructor
— and the client root plus every session it mints sends it automatically;
seats never carry tokens of their own.

The recommended script pattern — initialise `$bearer = ''` at the top,
always pass it:

```powershell
$bearer = ''                                   # '' = unprotected bridge
# $bearer = 'PASTE_THE_64_CHAR_TOKEN_HERE'     # when the Setting is on

$con    = [ZxNextRemoteConnection]::new('10.0.0.8', 80)
$remote = [ZxNextRemote]::new($con, $bearer)   # '' leaves the connection as-is
```

Same through the functions: `New-ZxNextRemoteConnection -Token $bearer`, or
`New-ZxNextRemote -Connection $con -Token $bearer`.

> The token gates access; it does **not** encrypt anything. The transport
> is plain HTTP — run the bridge on a trusted LAN only (see the Security
> section of HTTP_BRIDGE.md).

## Error handling — including the TWO different 401s

Every failure throws a **`ZxNextRemoteError`** whose `.Reason` names the
failure class — branch on it, never on message text:

| `.Reason` | HTTP | Meaning | Fix |
|---|---|---|---|
| `TokenRequired` | 401 | bearer token missing or wrong | set `$bearer` / the connection's `Token` from Unite's Settings |
| `OsProtected` | 401 | the remote machine is ZX Next Remote with **OS protection** on, and a *write* (`put`/`mkdir`/`rmdir`/`rm`/`ren`/`rcpy`) hit a guarded folder | on **that machine**: adjust its "OS protection" setting or folder list. The token is fine; reads still work |
| `SessionGone` | 410 | the bound seat left the bridge | `Session()` again, rebind |
| `NoNextConnected` | 503 | bridge up, no Next in `-listen` | start the Listener on the Next |
| `Timeout` | 504 / local | the Next (or the bridge) did not answer in time — the bridge itself gives up after 45 s on quick verbs and 270 s on transfers | check the Next; the bridge's own 504 usually arrives first |
| `BadRequest` | 400 | bad arguments (e.g. `Get` of a folder) | fix the call |
| `NextRefused` | 502 | the Next reported a failure (missing file, full card…) | read `.Detail` |
| `Unsupported` | 501 | this bridge host lacks the verb (`nextsync5.py` has no `/rmtree`) | use the app's bridge |
| `BridgeUnreachable` | — | no HTTP answer at all | address/port/firewall; is the bridge on? |
| `ConnectionClosed` | — | this client object was `Close()`d | make a new one |

```powershell
using module ZxNextRemote                      # makes the catch type visible
try {
    $s.Put('build\game.nex', '/home/incoming.nex')
} catch [ZxNextRemoteError] {
    switch ($_.Exception.Reason) {
        'TokenRequired' { Write-Warning 'set $bearer from Unite''s Settings'; exit 2 }
        'OsProtected'   { Write-Warning 'OS protection on the remote machine blocked the write' }
        default         { throw }
    }
}
```

With plain `Import-Module` (no `using module`), catch everything and probe
the reason: `$_.Exception.PSObject.Properties['Reason']` — it exists only
on this module's errors.

`Test-ZxNextRemoteBridge $con` is the polling-friendly variant: `$null`
when the bridge is unreachable (loop and retry) but it **throws** on
`TokenRequired` — a wrong token should fail fast, not poll for ever.

---

# PS-Send-ToNext.ps1 — push a build to real hardware

`extra/PS-Send-ToNext.ps1` is `Send-ToNext.ps1` rebuilt on this module:
same `Send-ToNext.cfg` (it writes a commented sample on first run), same
exit codes, same cycle — wait for a Next on the bridge, `Put` the build,
**verify** it, `/forceexit` so the Next-side `autoexec.bas` loop boots into
it. The verify step compares the **CRC-32 the Next computes** of the file
it holds (`Crc`, 8 hex digits over the wire) with `LocalCrc32` of the bytes
that were pushed — seconds for a big build, and the report names the
digest. A ZX Next Remote older than 1.0.8 (or a `.sync5` dot older than
5.9.2) does not know the crc op; the script then says so and falls back to
`Send-ToNext.ps1`'s size + 16-bit checksum read-back (`Sum`), so the
verdict is real either way. The Next-side setup is documented in
[`extra/README.md`](../extra/README.md) ("Push-to-hardware from VS Code").

New here: **`-autoexec`** manages that Next-side loop file without pulling
the SD card. The loop lives at `c:/nextzxos/autoexec.bas`; "parked" is the
same file renamed `autoexec_.bas`:

| Flag | Does |
|---|---|
| `-autoexec:Deploy` | `autoexec.bas` missing on the Next → sends `extra/autoexec.bas` there (verified), and removes a stale parked `autoexec_.bas` it supersedes. Never overwrites an existing `autoexec.bas` |
| `-autoexec:On` | `autoexec_.bas` exists → renamed to `autoexec.bas` (loop armed again) |
| `-autoexec:Off` | `autoexec.bas` exists → renamed to `autoexec_.bas` (machine boots normally). Refuses (exit 3) when **both** files already exist — renaming onto an existing file fails on the Next, and deleting either is your call, not the script's |

`-autoexec` on its own does **only** that and exits — it does not also push
a build. Add `-AndSend` (push the cfg's `file`) or an explicit `-File` to
do both in one run. Re-running an action that is already in effect says
"already deployed / already On / already Off" and exits 0 (the one
exception is `Off`'s both-files-exist refusal above).

Exit codes (a task or CI can branch on them): `0` verified success ·
`1` configuration · `2` token refused · `3` operation failed (including an
OS-protection refusal, which is named as such) · `4` sent but **not**
verified · `5` timed out waiting for a Next.

---

# VS Code: build → push to hardware in one click

A complete, working project using exactly this setup is
**[SampleNex](https://github.com/jclauzel/SampleNex)** — clone it and press
`Ctrl+Shift+B`. To wire up your *own* project instead, read on.

The sample lives in [`PowerShell/vscode-sample/`](vscode-sample/) — copy
`tasks.json` into your project's `.vscode/` folder (or merge the tasks into
yours) and adjust three things: the path to `PS-Send-ToNext.ps1`, your
build command, and (first run) the generated `Send-ToNext.cfg`.

Vendoring into a `tools/` folder means copying **three** things —
`PS-Send-ToNext.ps1`, the `ZxNextRemote/` module folder (imported
relative to the script), and `autoexec.bas` (what `-autoexec:Deploy`
sends, looked up next to the script) — all three sit in `extra/`.

What you get, on the **Terminal → Run Task…** menu (and `Ctrl+Shift+B` for
the default one):

| Task | What it does |
|---|---|
| **Build + Send to Next** | your build task, then a verified push, then `/forceexit` — save, one key, watch it on hardware |
| **Send to Next** | push the already-built file (no build step) |
| **Next: deploy autoexec loop** | one-time `-autoexec:Deploy` of the Next-side loop |
| **Next: autoexec On / Off** | arm / park the loop without touching the SD card |

The pattern, in `tasks.json` terms (the sample carries the full file):

```jsonc
{
  "label": "Send to Next",
  "type": "shell",
  "command": "powershell",      // or "pwsh" - the script runs on both
  "args": [
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", "${workspaceFolder}/tools/PS-Send-ToNext.ps1",
    "-Config", "${workspaceFolder}/Send-ToNext.cfg"
  ],
  "problemMatcher": []
},
{
  "label": "Build + Send to Next",
  "dependsOrder": "sequence",
  "dependsOn": ["Build .nex", "Send to Next"],   // your build task first
  "group": { "kind": "build", "isDefault": true } // = Ctrl+Shift+B
}
```

Notes that save the first afternoon:

* **The task fails when the push fails.** VS Code marks a task failed on a
  non-zero exit code, and the script's codes are deliberate — `4` means
  "the bytes on the Next differ", which you want loud, not scrolled away.
* **`dependsOrder: "sequence"`** is what chains build → push; without it
  VS Code runs `dependsOn` tasks in parallel and the push races the build.
* **First run**: the script writes a commented `Send-ToNext.cfg` at the
  path `-Config` names — with the sample tasks, your workspace root —
  and exits 1. Set `bridge_ip` (the PC running Unite — not the Next) and
  `file` (your build artifact, relative paths resolve against the cfg),
  then run the task again.
* **Wrong machine?** With several Nexts seated the push rides the app's
  ACTIVE seat and warns; for an always-the-same-machine setup, script it
  yourself with `ManageSession($sid)` — ten lines with this module.
* **CI**: the same script works headless (`powershell -File ... -TimeoutSeconds 120`);
  branch on the exit code.

---

## Linting — PSScriptAnalyzer

The module, the push script and the test driver are kept **clean under
[PSScriptAnalyzer](https://github.com/PowerShell/PSScriptAnalyzer)** (all
severities, zero findings). To reuse the same check after editing any of
them:

```powershell
# one-time install (works in Windows PowerShell 5.1 and pwsh 7)
Install-Module PSScriptAnalyzer -Scope CurrentUser -Force

# from the repo root - analyze every PowerShell file this doc covers
foreach ($f in 'extra/ZxNextRemote/ZxNextRemote.psm1',
               'extra/ZxNextRemote/ZxNextRemote.psd1',
               'extra/PS-Send-ToNext.ps1',
               'tests/test_powershell_module.ps1') {
    Invoke-ScriptAnalyzer -Path $f
}
```

No output = clean. (Pass the files one at a time — `-Path` takes a single
file or a folder, not an array.)

Three rules are **deliberately suppressed**, each with an inline
`[Diagnostics.CodeAnalysis.SuppressMessageAttribute(...)]` carrying its
justification — keep the pattern if you extend the code:

| Rule | Where | Why it does not apply |
|---|---|---|
| `PSUseShouldProcessForStateChangingFunctions` | the two `New-*` factories | they build in-memory objects; no system state changes, so `-WhatIf` would be dead weight |
| `PSAvoidUsingWriteHost` | `PS-Send-ToNext.ps1`, the test driver | interactive console scripts: the coloured report is the product, and since PS 5 `Write-Host` writes the capturable information stream |
| `PSAvoidUsingPositionalParameters` | the test driver | `Check 'name' (cond) detail` is the suite-wide compact test idiom |

---
## Files

| File | Role |
|---|---|
| `extra/ZxNextRemote/ZxNextRemote.psd1` | module manifest (`Import-Module` target) |
| `extra/ZxNextRemote/ZxNextRemote.psm1` | the classes + exported functions |
| `extra/ZxNextRemote/en-US/about_ZxNextRemote.help.txt` | `Get-Help about_ZxNextRemote` — the class/method reference |
| `extra/PS-Send-ToNext.ps1` | module-based push-to-hardware script (`-autoexec`, `-AndSend`) |
| `extra/Send-ToNext.ps1` | the original, module-free push script (kept as-is) |
| `PowerShell/PowerShellHelperClass.md` | this document |
| `PowerShell/vscode-sample/tasks.json` | ready-to-copy VS Code tasks |
| `tests/test_powershell_module.py` / `.ps1` | the module's test suite (runs under 5.1 AND 7) |

## See also

* **[SampleNex](https://github.com/jclauzel/SampleNex)** — the demo project: a
  z88dk `.nex` app, a build script and the VS Code tasks, assembled and
  hardware-tested.
* [`nextsync/sync/server/HTTP_BRIDGE.md`](../nextsync/sync/server/HTTP_BRIDGE.md)
  — the wire contract this module speaks.
* [`extra/README.md`](../extra/README.md) — the Next-side `autoexec` loop
  and the original `Send-ToNext.ps1`.
