# extra/ — maintainer tooling

Odds and ends that support the project but are not part of the app.

| File | Role |
|---|---|
| `tour_build_demo_env.py` | Builds the throwaway demo environment (`C:\Users\Public\ZX-Next-Unite-demo`) the tour GIF is captured from: app copy, junctioned emulators, sample sync folder, demo HDF, seeded `hdfg.cfg` |
| `tour_capture.py` | Drives the demo app through every tab and grabs the animation frames (real Qt platform — a window appears; host name/IPs are masked to placeholders) |
| `tour_assemble_gif.py` | Assembles the frames into `zx-next-unite-tour.gif` (140 ms frames, crossfades, ffmpeg palette pipeline) |
| `Get-PyLineCounts.ps1` | Per-module line-count report for the Python sources |
| `detectenvironnement.bas` / `.txt` | NextBASIC environment-detection helper and its notes |

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
