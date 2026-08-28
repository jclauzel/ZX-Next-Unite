# extra/ — maintainer tooling

Odds and ends that support the project but are not part of the app.

| File | Role |
|---|---|
| `tour_build_demo_env.py` | Builds the throwaway demo environment (`C:\Users\Public\ZX-Next-Unite-demo`) the tour GIF is captured from: app copy, junctioned emulators, sample sync folder, demo HDF, seeded `hdfg.cfg` |
| `tour_capture.py` | Drives the demo app through every tab and grabs the animation frames (real Qt platform — a window appears; host name/IPs are masked to placeholders) |
| `tour_assemble_gif.py` | Assembles the frames into `zx-next-unite-tour.gif` (140 ms frames, crossfades, ffmpeg palette pipeline). Needs **Pillow** — `pip install pillow` |
| `MAME_ROM_HOWTO_CREATE.md` | How to (re)build the `tbblue.zip` boot-ROM package MAME's `tbblue` / `specnext_ks1..3` machines need: which four ROMs go in it and why (not six), where each one comes from (pinned FPGA-repo commits — the `30204` branch tip has moved on and no longer matches), how to decode the two VHDL-embedded ones, and why the whole thing is GPLv3 and therefore yours to rebuild and pass on |
| `Get-PyLineCounts.ps1` | Per-module line-count report for the Python sources |
| `detectenvironnement.bas` / `.txt` | NextBASIC environment-detection helper and its notes |
| `Send-ToNext.ps1` | Push a build to a real Next over Unite's NextSync HTTP bridge, verified end-to-end (see below) |
| `ZxNextRemote/` | PowerShell module (classes + cmdlets, PS 5.1 and 7): a typed client for the NextSync HTTP bridge - sessions, ls/get/put/ren/rcpy..., bridge-scoped bearer token, and typed errors that tell the two 401s apart (token vs OS protection). Docs: `PowerShell/PowerShellHelperClass.md`; tests: `tests/test_powershell_module.py` |
| `PS-Send-ToNext.ps1` | `Send-ToNext.ps1` rebuilt on that module - same cfg/exit codes, plus `-autoexec:On/Off/Deploy` to manage the Next-side loop file remotely and `-AndSend` to combine it with a push. VS Code one-click sample: `PowerShell/vscode-sample/`; a ready-made demo project: [SampleNex](https://github.com/jclauzel/SampleNex) |
| `autoexec.bas` / `.txt` | The Next-side loop `Send-ToNext.ps1` pushes into: listen, receive, run, repeat — in either ZX Next Remote flavour. Drop `autoexec.bas` into `/nextzxos/` on the card as-is |

## Regenerating the README/wiki tour GIF

Windows, with the repo's `downloads/` populated (MAME + the itch.io CSpect —
they are junctioned, not copied), `ffmpeg` on PATH and **Pillow installed**
(`pip install pillow` — the assembler imports `PIL`, and it is deliberately
not in `REQUIREMENTS.txt` because the app itself never needs it):

```powershell
python extra\tour_build_demo_env.py     # build C:\Users\Public\ZX-Next-Unite-demo
python extra\tour_capture.py            # ~4 min; a 1500x950 window appears
                                        # -- now READ A FRAME, see "Verify" below --
python extra\tour_assemble_gif.py       # writes %TEMP%\zxnu-tour\zx-next-unite-tour.gif
```

Then copy the result over `docs/zx-next-unite-tour.gif` and commit.

**Run all three, in that order, every time.** `tour_build_demo_env.py` is not
a one-off setup step: the app writes its last-look UX state (window size,
explorer column widths, splitter positions) back into the demo `hdfg.cfg` on
exit, so a second capture against the same demo environment inherits the
first run's geometry. Symptom seen in the field: the Classic sync left
explorer came out squeezed to ~230 px with its prepare/scan block missing,
while the frames from the freshly-seeded run were a clean 50/50 split.
Rebuilding takes seconds; debugging the squeezed frames does not.

### Verify before assembling

Open `%TEMP%\zxnu-tour\tour_frames\seg1_nextsync_classic_08.png` and read the
host banner in the log pane. Every line must be a placeholder:

```
Running on host:
    <your PC name>
IP addresses:
    <your LAN address 1>
    <your LAN address 2>
Primary IP:
    <your primary IP>
```

A real address there means the masking did not take — do **not** assemble.
`seg2_nextsync_re_08.png` shows the same banner in the Remote Explorer's idle
overlay and is worth a second look. As a cheap cross-check, the demo run's own
log must be clean too:

```powershell
Select-String -Path C:\Users\Public\ZX-Next-Unite-demo\zx-next-unite.log `
  -Pattern '10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.' 
```

Notes (learned the hard way):

- The capture needs the REAL Qt platform — pygame-ce crashes natively under
  `QT_QPA_PLATFORM=offscreen`, so a window is visible while it runs.
- Privacy, and the trap that got past it once: `socket.gethostname` /
  `gethostbyname_ex` are patched before the app imports, which masks the host
  name and the addresses `gethostbyname_ex` reports — but **not the PRIMARY
  address**. `detect_local_ipv4` works that one out by opening a UDP socket
  and reading `getsockname()`, which no socket patch here touches. The
  orchestrator's own `patch_ip` step cannot save it either: the NextSync tab
  prints its host/IP banner once at STARTUP, long before that step runs. The
  result shipped in the frames as a real `Primary IP: 10.0.0.31`.
  `tour_capture.py` therefore rebinds `zxnu_network.detect_local_ipv4` at
  module scope, **before the app imports anything**, so every
  `from zxnu_network import detect_local_ipv4` in the app binds the fake.
  Keep it there; moving it into the orchestrator re-opens the hole. Verify
  anyway (above) — the app is free to grow another way of asking.
- The demo cfg seeds `content_disclaimer_agreed=1` (the gate checks the
  literal `"1"`) — without it the online panes block the run on a modal.
- The Windows Firewall prompt for the NextSync port may appear once per
  Python interpreter; approve it or pre-authorize.
- The first tab-entry to NextSync auto-runs the prepare/perform-checks, so
  the capture never clicks Start (the real server would block on its modal
  progress dialog waiting for a Next).
- Another copy of Unite already running on the machine is harmless to the
  capture (it never starts a server), but it does hold ports 2048/80 — do not
  read a "port already in use" line in the demo log as a capture failure.


## Push-to-hardware from VS Code (`Send-ToNext.ps1` + `autoexec`)

Save in the editor, run one task, watch the build on real hardware. The two
halves are `extra\Send-ToNext.ps1` (PC) and `extra\autoexec.bas` (Next).

**On the Next, once:**

1. Decide what serves the push. Three choices, and the loop asks you on
   first boot:
   * **`.sync5` (the default).** Just the dot command — no `.nex` on the
     card at all. Put `sync5` in the **`/dot/`** folder at the card root
     (it is an asset on every
     [release](https://github.com/jclauzel/ZX-Next-Unite/releases)). The
     lightest option, and the one that returns to BASIC when a session
     ends, so the loop closes itself.
   * **`zxnextremote-n2n.nex`** or **`zxnextremote-httpbridge.nex`**, copied
     to `/home/` on the SD card. Both carry the NextSync **Listener**, and
     the Listener is what Unite's HTTP bridge drives, so a push lands the
     same way either way. The flavour only decides which transport you
     *also* get as a Controler when you are not pushing builds.
2. *(the `.nex` flavours only)* In its Settings, set
   **NextSync → controller IP** to the PC running
   Unite. That is the field the Listener dials out on — *not* the bridge IP
   and port, which belong to Http Bridge (Controler) mode and play no part
   in receiving a push. The two are separate on purpose, so the two modes
   can face different machines.
3. *(the `.nex` flavours only)* Set **Auto start** to `2 Listener`.
   Without it every cycle stops at the Home menu waiting for a keypress,
   and the loop is not unattended. Since ZX Next Remote 0.9.56 that setting
   also drops the goodbye banner on exit, so each cycle is ~2 s quicker.

   Using `.sync5` instead? Run `.sync5 <PC ip>` once to save the server
   address; the dot keeps it in `c:/sys/config/nextsync.cfg` and the loop
   passes only `-listen` and your speed switch after that.
4. Copy `autoexec.bas` into the **`/nextzxos/` folder** on the card — not
   the card root, where NextZXOS will not run it. No renaming: the file
   ships under the name the machine looks for.
5. Boot it. With no `autoexec.cfg` yet the loop opens **configuration
   mode** and asks for all of the above — flavour, `.sync5` speed, the two
   folders, and what to do when a new push would overwrite the previous
   build. It saves your answers to `c:/nextzxos/autoexec.cfg` and starts
   the loop. Nothing needs re-tokenising to change your mind: press **`c`**
   at the overwrite prompt, or **hold `C` while the machine boots**, to get
   back in.

On screen the loop announces itself as **`nextdev:`** — that is the prefix
to look for in its messages (`nextdev: waiting for a push...`).

### The settings file

`c:/nextzxos/autoexec.cfg`, plain text, six lines, hand-editable on the card:

```
ZXNU1      format marker - anything else means "reconfigure"
1          flavour: 1 .sync5, 2 n2n .nex, 3 httpbridge .nex
-f         .sync5 speed: -s (slow, and what MAME needs) | -default | -f
/home      transfer folder - where pushes land
/home      folder holding the .nex flavours
ask        when a push would overwrite the previous build:
           ask | always (retire it) | never (discard it)
loop       at boot: loop (serve a push) | menu (hand the machine
           straight to the boot menu instead)
```

Both folders must be **absolute** — starting `/` or a drive letter. The loop
`CD`s into the transfer folder *and* probes paths inside it, so a relative
name would be applied twice and every push would land somewhere the loop
never looks.

**Picking flavour 2 or 3 is checked immediately.** If that `.nex` is not in
the `.nex` folder, configuration says so — flashing, on its own screen — and
offers to take a different folder there and then. Give it one holding the
file and the flavour stands; decline, or name a folder that does not hold it
either, and it keeps `.sync5` (which needs no `.nex` at all) rather than
saving a setting whose file is not on the card. The same check runs again if
you change the `.nex` folder later in the walk.

**Switching the loop off** is entry `4` on the first configuration screen, and
it toggles — pick `4` again to switch it back on. It is deliberately not a
fourth flavour: your transport choice survives being switched off, so turning
it back on does not make you pick one again. While it is off the program still
*runs* at boot (NextZXOS starts it before anything else) but hands the machine
straight back, so you land at the boot menu. **Hold `C` at boot** to get back
into configuration and re-enable it.

Delete the file and the next boot re-enters configuration mode. Anything the
loop cannot find — a missing `/dot/sync5`, a missing `.nex`, a transfer
folder that is not there — says so, waits for ENTER, and takes you there too,
rather than halting.

`always` and `never` are what make the loop truly unattended: `ask` is the one
setting that stops it for a keypress. With either of the other two, **hold `C`
at boot** is how you get back into configuration.

> **The transfer folder must match the PC.** Configuration mode prints the
> exact line to put in `Send-ToNext.cfg` (`remote_path = /home/incoming.nex`).
> A PC writing to one folder while the Next watches another fails silently —
> the push succeeds and nothing ever runs.

> **`/home`, not `/dev` (9.6.3).** Earlier versions defaulted to `/dev/`, which
> does not exist on a stock NextZXOS card and had to be created by hand.
> `/home` ships with the OS. If you have an existing setup on `/dev`, either
> keep it (set the transfer folder to `/dev` in configuration mode and leave
> `remote_path` alone) or move both halves together.

The loop runs at every boot: if a pushed file is waiting it moves it aside
and `.nexload`s it; otherwise it hands the machine to your chosen flavour,
which enters the Listener and waits. ZX Next Remote soft-resets when it
exits, which is what closes the loop — the reset IS the `GO TO`.

A pushed build runs **once**. Before loading anything, the loop retires the
previous build to `/home/last.nex`, so `/home/run.nex` only ever holds a
freshly pushed one: exit the game, and the next boot lands back on the
Listener ready for your next push instead of re-running the old build for
ever. The retired copy is kept, not deleted — `.nexload /home/last.nex`
re-runs it by hand whenever you want it again.

It cannot be done the other way round: a successful `.nexload` never comes
back, so anything written *after* it — a tidy-up, a prompt — never runs at
all on the one path that matters.

The PC side is even more agnostic: anything that puts a NextSync Listener
behind Unite satisfies `Send-ToNext.ps1` — even a `.sync5 -listen` dot
session, since the script only ever talks to the bridge — but the
unattended loop wants the `.nex` flavours, whose exit-and-soft-reset is
what closes the cycle.

**In Unite, once:** turn on **Settings → Enable NextSync HTTP bridge**
(port 80 by default), then open the **NextSync** tab and start the
**Remote Explorer** listen server. Both are prerequisites, and neither is
optional: without the bridge the script cannot reach Unite at all, and
without the listen server there is nothing for the Next's Listener to dial
out to — the script just polls for ever, printing *"bridge is up but NOT
listening"*.

**On the PC, once:** run the script; it writes a commented
`Send-ToNext.cfg` beside itself and stops. Set `bridge_ip` (the PC running
Unite, not the Next), `bridge_port` if you moved the bridge off port 80
(Unite's Settings has its own port box next to the bridge toggle), `file`
(your build), `token` only if Unite's Settings has "Require bearer token"
on, and `wait_timeout` if you want the script to give up rather than wait
for a Next indefinitely — it ships as `0`, meaning wait for ever (the
`-TimeoutSeconds` parameter overrides it).

**Then, every build:**

```powershell
extra\Send-ToNext.ps1
```

(or `extra\PS-Send-ToNext.ps1`, the module-based rebuild - same cfg and
exit codes, plus the `-autoexec` loop management; see
`PowerShell/PowerShellHelperClass.md` for it, the ZxNextRemote module and
the ready-to-copy VS Code tasks in `PowerShell/vscode-sample/`)

It polls `/status` every 2 s until a Next appears, PUTs the file, and then
**verifies it**: `/sum` returns the 16-bit additive checksum and size of the
file *as it landed*, compared against the same sum computed locally. A
transfer is only ever reported as success when those match — an HTTP 200 on
its own is not proof the bytes arrived intact. Finally `/forceexit` tells the
Next to exit and reboot into the pushed build.

Exit codes, for a VS Code task or CI to branch on:

| Code | Meaning |
|---|---|
| 0 | sent and **verified** on the Next |
| 1 | configuration problem (missing `.cfg` value, missing file) |
| 2 | the bridge refused the token (HTTP 401) |
| 3 | the send itself failed |
| 4 | sent, but **not verified** — the bytes on the Next differ, or the checksum could not be read back |
| 5 | timed out waiting for a Next — only reachable once `wait_timeout` / `-TimeoutSeconds` is set (it ships as wait-for-ever) |

A `tasks.json` entry that fails the task on anything but a verified send:

```json
{
  "label": "Send to Next",
  "type": "shell",
  "command": "pwsh -File extra/Send-ToNext.ps1",
  "problemMatcher": []
}
```

### A ready-made demo project: SampleNex

Rather than wiring a project up from scratch to try this, clone
**[SampleNex](https://github.com/jclauzel/SampleNex)** — a deliberately tiny z88dk
ZX Spectrum Next app that exists purely to demo this integration, already
assembled and confirmed working on real hardware:

* a `build.ps1` that finds z88dk, bumps a build counter and compiles a
  `.nex` (modelled on this project's sibling build script),
* a `.vscode/tasks.json` where **`Ctrl+Shift+B`** runs *build → push →
  verify → run on the Next*, plus tasks for the three `-autoexec` actions,
* `tools/` carrying the vendored trio, so the repo is self-contained.

The app prints `Hello Dev Builders <n>!` where `n` increments on **every**
build — so the number on the TV is proof the Next is running the build you
just made, not a stale copy on the SD card. It ends with a soft reset,
which hands the machine back to the `autoexec` loop and re-arms the
Listener for the next push.

Vendoring into your own project means copying **three** things from here —
`PS-Send-ToNext.ps1`, the `ZxNextRemote/` module folder (imported relative
to the script) and `autoexec.bas` (what `-autoexec:Deploy` sends, looked up
beside the script). SampleNex's README documents refreshing them.

### Editing the loop

`autoexec.txt` is the readable source; `autoexec.bas` is the tokenised
NextBASIC the Next loads.

**Most changes need no editing at all.** Flavour, `.sync5` speed, both
folders and the retire rule all live in `autoexec.cfg` on the card and are
set from configuration mode on the Next — that is the whole point of it.
Edit the source only to change the loop's *behaviour*.

After any edit, re-tokenise with
[txt2bas](https://www.npmjs.com/package/txt2bas) — the Next loads the `.bas`,
so an edited `.txt` alone changes nothing:

```powershell
txt2bas -i extra\autoexec.txt -o extra\autoexec.bas
```

Worth reading it back to be sure the tokeniser understood you:

```powershell
bas2txt -i extra\autoexec.bas -o roundtrip.txt
```

Then copy the rebuilt `autoexec.bas` into `/nextzxos/` on the card. Editing
the `.txt`, or even re-tokenising, changes nothing on the machine until the
`.bas` is back on the SD card.
