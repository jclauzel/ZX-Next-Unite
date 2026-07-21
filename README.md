# ZX-Next-Unite

Cross-platform (Windows, Linux, macOS) desktop app for ZX Spectrum Next users.
It unites two great tools — **Hdfm-Gooey** and **NextSync** — in a single GUI,
and adds a built-in browser to discover ZX Spectrum / Spectrum Next software
across **GetIt**, **ZXDB/ZXInfo**, **zxArt** and **itch.io**.

By Julien Clauzel, based on **HDFM-GOOEY** by em00k and **NextSync** by Jari
Komppa (with the Sync4 extensions by Julien Clauzel).

Co-developed with the assistance of **Claude** (Anthropic's AI).

## Features

- **SD Card tab** — mount an HDF image, copy files in/out with a built-in
  explorer, then launch **CSpect** or **MAME** directly. No emulator command
  lines to remember.
- **NextSync tab** — push files over Wi-Fi from your PC to a real Spectrum Next
  (KS1/KS2 or clones with an ESP module) using the `.sync5` dot command.
- ⭐ **Remote Explorer** — a two-pane file manager for your Next's **real
  filesystem over Wi-Fi**: browse, drag & drop, upload/download and manage files
  (new folder, delete) directly — no SD-card swapping. Run `.sync5 -listen` on
  the Next to connect, and launch the app with the
  `-start-remote-explorer-listener` switch to have the listen server running
  from startup with no clicks. See the
  [Wiki](https://github.com/jclauzel/ZX-Next-Unite/wiki#remote-file-explorer).
- ⭐ **HTTP bridge** — remote access to a Next's file system over plain **HTTP**:
  a built-in web server (Flask) republishes the Remote Explorer's `-listen`
  session as HTTP routes, so you can browse, download, upload and manage the
  Next's SD card from a browser, `curl` — or from **another Next** using the
  built-in `.http` dot command. Enable it in the Settings tab (the port —
  80 by default — is set in the box next to the toggle). See the
  [HTTP bridge documentation](nextsync/sync/server/HTTP_BRIDGE.md).
- **Online libraries** — search and download from GetIt, ZXDB/ZXInfo, zxArt and
  (optionally) itch.io.

## Quick start

**Windows:** download the latest `ZX-Next-Unite-v9.x` executable from the
[Releases](https://github.com/jclauzel/ZX-Next-Unite/releases) page — no Python needed.

**From source (any platform):**

    git clone https://github.com/jclauzel/ZX-Next-Unite
    cd ZX-Next-Unite
    python -m pip install -r REQUIREMENTS.txt
    python zx-next-unite.py

Use `python3` on Linux/macOS. Only **PySide6** is required; `pygame-ce`,
`itch-dl` and `flask` are optional (`flask` powers the NextSync HTTP bridge —
the web server that lets a Next drive another Next's SD card via the `.http`
dot command; without it the Settings toggle is simply greyed out).

## Documentation

Full setup, emulator / hdfmonkey / Mono configuration and troubleshooting are in
the **[Wiki](https://github.com/jclauzel/ZX-Next-Unite/wiki)**:

- [Home / overview](https://github.com/jclauzel/ZX-Next-Unite/wiki)
- [Installation](https://github.com/jclauzel/ZX-Next-Unite/wiki/Installation)

## License

Released under the **MIT** license (see [LICENSE](LICENSE)). Built on **PySide6**
and **pygame-ce** (Qt 6, GPLv2/LGPL) and optionally
[itch-dl](https://github.com/DragoonAethis/itch-dl) by Dragoon Aethis (MIT) and
[Flask](https://flask.palletsprojects.com/) by the Pallets team (BSD-3-Clause),
the web server that powers the NextSync HTTP bridge.

## Legal disclaimer — third-party content

> **The author of ZX-Next-Unite does NOT distribute any files, ROMs, games, demos, graphics, music, or any other content obtained through the GetIt, ZXDB, or zxArt integrations.**

All content is served exclusively by the respective third-party services listed above. ZX-Next-Unite acts solely as a frontend client that queries their public APIs and downloads files directly from the URLs those APIs return.

**It is the sole responsibility of the end user to ensure that any content they download or use through this application complies with the applicable copyright, licensing, and legal requirements in their jurisdiction.**

If in doubt, consult the terms of service of the relevant platform and seek appropriate legal advice before downloading or using any content.

The first time you open the GetIt, ZXDB, or zxArt pane, the application will display this disclaimer and ask you to acknowledge it. You can dismiss the dialog to continue using the pane; the dialog will reappear on the next visit until you check *"I agree and understand. Do not show this message again"*, at which point your acknowledgement is saved to `hdfg.cfg` and the dialog will not be shown again.
