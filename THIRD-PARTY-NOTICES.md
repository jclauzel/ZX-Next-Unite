# Third-Party Notices

ZX-Next-Unite is released under the **MIT** license (see [LICENSE](LICENSE)).
It builds on, bundles, or launches the third-party software listed below.
Nothing in this file changes the license of ZX-Next-Unite itself; it documents
the provenance and licenses of those components.

## Bundled / derived code

### NextSync — Jari Komppa (Unlicense)

The NextSync protocol, the original `.sync` dot command and the original
server are by Jari Komppa (<https://github.com/jarikomppa/nextsync>), released
under the **Unlicense** (effectively public domain — see
[nextsync/LICENSE](nextsync/LICENSE)). The `nextsync/` subtree of this
repository retains his original files and license headers.

The Sync4/Sync5 protocol extensions, the `.sync5` dot command builds,
`nextsync5.py`, the Remote Explorer and the HTTP bridge are by Julien Clauzel
and are covered by this project's MIT license.

### HDFM-Gooey — em00k

The SD Card utility side of ZX-Next-Unite originated as a rewrite of
**HDFM-Gooey** by em00k (<https://github.com/em00k/HDFM-Gooey>). em00k has
confirmed in written correspondence with the author that he is happy for
ZX-Next-Unite to build on HDFM-Gooey; copies of that correspondence are kept
on file. Many thanks to em00k for the original tool.

### jesperl — Janko Stamenović (GNU Affero GPL v3)

The idea behind the optional RS232 ESP Emulation for MAME — proxying MAME's
`-rs232_esp`/`-bitb` bitbanger socket to the real network by emulating the
ESP8266 AT command set — comes from **jesperl** by Janko Stamenović
(<https://sourceforge.net/projects/jesperl/>), released under the GNU Affero
GPL v3. Many thanks for the inspirational idea.

ZX-Next-Unite's `espemu.py` is a clean full reimplementation, not a port: it
shares no code, structure or text with jesperl and was written independently
from Espressif's publicly documented AT instruction set and this project's
own Next-side clients. It is by Julien Clauzel and covered by this project's
MIT license.

## Libraries

These are installed separately (via `pip`) for source installs. The frozen
(PyInstaller) release executables include PySide6/Qt and any optional packages
that were installed at build time; because the complete source code of
ZX-Next-Unite is published, anyone can rebuild the executables with modified
versions of these libraries.

| Library | License | Role |
|---|---|---|
| [PySide6 / Qt for Python](https://www.qt.io/qt-for-python) (The Qt Company) | LGPL v3 | GUI toolkit (Qt 6) — required |
| [pygame-ce](https://pyga.me) | LGPL v2.1 | optional — animated backgrounds and the Alien Floyd's effects |
| [Flask](https://flask.palletsprojects.com/) (Pallets team) | BSD-3-Clause | optional — the web server behind the NextSync HTTP bridge |
| [Send2Trash](https://github.com/arsenetar/send2trash) (Andrew Senetar and contributors) | BSD-3-Clause | optional — sends locally-deleted files to the Recycle Bin / Trash |
| [itch-dl](https://github.com/DragoonAethis/itch-dl) (Dragoon Aethis) | MIT | optional — powers installs from the itch.io tab |

## External tools (separate programs, launched as subprocesses)

These are stand-alone programs that ZX-Next-Unite invokes as separate
processes. They are not linked into, nor distributed with, ZX-Next-Unite.

### hdfmonkey — Matt Westcott (GPL v3)

All HDF disk-image operations are performed by invoking the `hdfmonkey`
command-line tool (<https://github.com/gasman/hdfmonkey>), licensed under the
**GNU GPL v3**. It is not bundled with ZX-Next-Unite: the app discovers an
existing installation, or — at the user's request — downloads a pre-compiled
build (the "jjjs" release hosted on the specnext.com forum, or the copy
bundled with a CSpect install from the itch.io tab).

### CSpect — Mike Dailly (closed-source freeware)

The CSpect ZX Spectrum Next emulator (<https://mdf200.itch.io/cspect>) is
closed-source freeware, launched as a separate application. It is not bundled;
the user installs it themselves (for example via the itch.io tab).

### MAME (GPL v2 or later)

The MAME emulator (<https://www.mamedev.org>) is distributed under the
**GNU GPL v2 or later** (with much of its code under BSD-3-Clause). It is
launched as a separate application and is not bundled; on Windows the app can
download the official precompiled binary at the user's request.

## Online services

The GetIt (`zxnext.uk`), ZXDB/ZXInfo (`api.zxinfo.dk`), zxArt (`zxart.ee`) and
itch.io catalogue tabs query those services' public APIs; all content is
served by the respective services, not by ZX-Next-Unite. See the
"Legal disclaimer — third-party content" section of the [README](README.md).
