# extra/ — maintainer tooling

Odds and ends that support the project but are not part of the app.

| File | Role |
|---|---|
| `tour_build_demo_env.py` | Builds the throwaway demo environment (`C:\Users\Public\ZX-Next-Unite-demo`) the tour GIF is captured from: app copy, junctioned emulators, sample sync folder, demo HDF, seeded `hdfg.cfg` |
| `tour_capture.py` | Drives the demo app through every tab and grabs the animation frames (real Qt platform — a window appears; host name/IPs are masked to placeholders) |
| `tour_assemble_gif.py` | Assembles the frames into `zx-next-unite-tour.gif` (140 ms frames, crossfades, ffmpeg palette pipeline) |
| `Get-PyLineCounts.ps1` | Per-module line-count report for the Python sources |
| `detectenvironnement.bas` / `.txt` | NextBASIC environment-detection helper and its notes |
| `Send-ToNext.ps1` | Push a build to a real Next over Unite's NextSync HTTP bridge, verified end-to-end (see below) |
| `autoexec.bas` / `.txt` | The Next-side loop `Send-ToNext.ps1` pushes into: listen, receive, run, repeat — in either ZX Next Remote flavour. Drop `autoexec.bas` into `/nextzxos/` on the card as-is |

## Regenerating the README/wiki tour GIF

Windows, with the repo's `downloads/` populated (MAME + the itch.io CSpect —
they are junctioned, not copied) and `ffmpeg` on PATH:

```powershell
python extra\tour_build_demo_env.py     # build C:\Users\Public\ZX-Next-Unite-demo
python extra\tour_capture.py            # ~4 min; a 1500x950 window appears
python extra\tour_assemble_gif.py       # writes %TEMP%\zxnu-tour\zx-next-unite-tour.gif
```

Then copy the result over `docs/zx-next-unite-tour.gif` and commit.

Notes (learned the hard way):

- The capture needs the REAL Qt platform — pygame-ce crashes natively under
  `QT_QPA_PLATFORM=offscreen`, so a window is visible while it runs.
- Privacy: `socket.gethostname`/`gethostbyname_ex` are patched before the app
  imports and `detect_local_ipv4` is re-patched in the loaded modules before
  the NextSync tab opens, so the captured log/panel show
  `<your PC name>` / `<your LAN address …>` / `<your primary IP>`
  placeholders instead of the real values. Verify before publishing.
- The demo cfg seeds `content_disclaimer_agreed=1` (the gate checks the
  literal `"1"`) — without it the online panes block the run on a modal.
- The Windows Firewall prompt for the NextSync port may appear once per
  Python interpreter; approve it or pre-authorize.
- The first tab-entry to NextSync auto-runs the prepare/perform-checks, so
  the capture never clicks Start (the real server would block on its modal
  progress dialog waiting for a Next).


## Push-to-hardware from VS Code (`Send-ToNext.ps1` + `autoexec`)

Save in the editor, run one task, watch the build on real hardware. The two
halves are `extra\Send-ToNext.ps1` (PC) and `extra\autoexec.bas` (Next).

**On the Next, once:**

1. Copy **either** ZX Next Remote flavour to `/dev/` on the SD card —
   `zxnextremote-httpbridge.nex` or `zxnextremote-n2n.nex`. Both carry the
   NextSync **Listener**, and the Listener is what Unite's HTTP bridge
   drives, so a push lands the same way either way. The flavour only decides
   which transport you *also* get as a Controler when you are not pushing
   builds.
2. In its Settings, set **NextSync → controller IP** to the PC running
   Unite. That is the field the Listener dials out on — *not* the bridge IP
   and port, which belong to Http Bridge (Controler) mode and play no part
   in receiving a push. The two are separate on purpose, so the two modes
   can face different machines.
3. Set **Startup menu** to `2 Listener`. Without it every cycle stops at the
   Home menu waiting for a keypress, and the loop is not unattended.
4. Copy `autoexec.bas` into the **`/nextzxos/` folder** on the card — not
   the card root, where NextZXOS will not run it. No renaming: the file
   ships under the name the machine looks for.
5. Using the n2n flavour? Set `LET flavour=2` (line 240 of `autoexec.txt`)
   and re-tokenise — see *Editing the loop* below. If the flavour you pick
   is not on the card, the loop tries the other one rather than
   dead-ending, so a mismatch costs you nothing.

The loop runs at every boot: if a pushed file is waiting it moves it aside
and `.nexload`s it; otherwise it hands the machine to your chosen flavour,
which enters the Listener and waits. ZX Next Remote soft-resets when it
exits, which is what closes the loop — the reset IS the `GO TO`.

A pushed build runs **once**. Before loading anything, the loop retires the
previous build to `/dev/last.nex`, so `/dev/run.nex` only ever holds a
freshly pushed one: exit the game, and the next boot lands back on the
Listener ready for your next push instead of re-running the old build for
ever. The retired copy is kept, not deleted — `.nexload /dev/last.nex`
re-runs it by hand whenever you want it again.

It cannot be done the other way round: a successful `.nexload` never comes
back, so anything written *after* it — a tidy-up, a prompt — never runs at
all on the one path that matters.

The PC side is even more agnostic: anything that puts a NextSync Listener
behind Unite satisfies `Send-ToNext.ps1` — even a `.sync5 -listen` dot
session, since the script only ever talks to the bridge — but the
unattended loop wants the `.nex` flavours, whose exit-and-soft-reset is
what closes the cycle.

**On the PC, once:** run the script; it writes a commented
`Send-ToNext.cfg` beside itself and stops. Set `bridge_ip` (the PC running
Unite, not the Next), `bridge_port` if you moved the bridge off port 80
(Unite's Settings has its own port box next to the bridge toggle), `file`
(your build), and `token` only if Unite's Settings has "Require bearer
token" on.

**Then, every build:**

```powershell
extra\Send-ToNext.ps1
```

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
| 4 | sent, but **verification failed** — the bytes on the Next differ |
| 5 | timed out waiting for a Next |

A `tasks.json` entry that fails the task on anything but a verified send:

```json
{
  "label": "Send to Next",
  "type": "shell",
  "command": "pwsh -File extra/Send-ToNext.ps1",
  "problemMatcher": []
}
```

### Editing the loop

`autoexec.txt` is the readable source; `autoexec.bas` is the tokenised
NextBASIC the Next loads. The one line most people need is the flavour
choice near the top:

```
240 LET flavour=1
```

`1` waits in `zxnextremote-httpbridge.nex`, `2` in `zxnextremote-n2n.nex`.
Whichever you choose, the push itself arrives through the NextSync Listener,
which both flavours carry.

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
