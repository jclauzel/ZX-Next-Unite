# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ZX-Next-Unite is a cross-platform (Windows/Linux/macOS) PySide6 (Qt6) GUI application written in Python. It combines two tools for ZX Spectrum Next users:

1. **SD Card Utility** — mounts HDF disk images and provides a file explorer for uploading/downloading content to them, then launching the CSpect or MAME emulator.
2. **NextSync** — implements the server side of Jari Komppa's NextSync protocol (TCP port 2048), allowing files to be pushed from the PC over Wi-Fi to a physical Spectrum Next machine. The app also implements a backwards-compatible **Sync4** extension that adds the reverse direction (`.sync -send <file|dir>` on the Next pushes files/directories *back* to the app); legacy **Sync3** dots keep working unchanged (PC → Next only). The `.dot` client source lives under `nextsync/sync/`.

Additional tabs provide online browsing of ZX Spectrum software via three third-party APIs: **GetIt** (`zxnext.uk`), **ZXDB** (`api.zxinfo.dk/v3`), and **zxArt** (`zxart.ee/api`).

## Running the application

```
python zx-next-unite.py
```

Requires Python 3.13+ and PySide6:

```
python -m pip install pyside6
```

No build step is needed for development. There are no tests or linters configured in this project.

## Packaging (optional)

Create a standalone executable with PyInstaller:

```
pip install pyinstaller
pyinstaller --onefile --windowed --noupx zx-next-unite.py
```

## Regenerating embedded Qt resources

Background images are compiled into `rc_backgrounds.py` from `rc_backgrounds.qrc`. Regenerate after adding/removing image assets:

```
pyside6-rcc rc_backgrounds.qrc -o rc_backgrounds.py
```

## Source file map

| File | Role |
|---|---|
| `zx-next-unite.py` | Entry point; contains the single `MainWindow(QMainWindow)` class and all tab/pane UI logic |
| `zxnu_config.py` | Constants, `SETTING_*` keys, API base URLs, UI string tables, color defaults, and pure helpers (`resource_path`, `qcolor_to_hex`, etc.) |
| `zxnu_workers.py` | Background threading primitives: `WorkerSignals`, `NextSyncSignals`, `HdfTaskSignals`, `HdfTaskWorker`, `HdfProgressDialog`, `DotDotFirstProxyModel` |
| `zxnu_media.py` | ZX Spectrum `SCREEN$` decoder (`ZxSpectrumScreen`), placeholder-pixmap rendering, file-format tag helpers, and the shared pixmap cache |
| `zxnu_gallery.py` | Reusable gallery widgets: `GalleryCell`, the scrollable grid view, and the `AnimatedBackground` widget |
| `zxnu_itchio.py` | Optional itch.io integration: `itch-dl` detection, itch.io API access (collections/owned/search via the user's API key) and install-via-`itch-dl`. Drives the optional itch.io tab |
| `rc_backgrounds.py` | Auto-generated Qt resource module (do not edit by hand) |

## Key architecture patterns

**Configuration** is stored in `hdfg.cfg` (a `key = value` file, INI-like) created in the same directory as the script. All setting key names are `SETTING_*` constants in `zxnu_config.py`. The `_initialising` guard on `MainWindow` prevents `save_configuration_file()` from firing while widgets are being constructed.

**Threading** follows a consistent pattern: long-running work (hdfmonkey commands, network fetches, NextSync transfers) runs in a `QRunnable` submitted to `QThreadPool`. Results are marshalled back to the UI thread via `Signal`/`Slot`. The signal classes (`HdfTaskSignals`, `NextSyncSignals`, etc.) live in `zxnu_workers.py`.

**External tool dependencies** — `hdfmonkey` is invoked as a subprocess for all HDF image operations. On Windows, the app can auto-download it from `https://uto.speccy.org/downloads/hdfmonkey_windows.zip`. CSpect and MAME are detected via PATH and launched as subprocesses. `resource_path()` in `zxnu_config.py` handles path resolution for both development (source tree) and PyInstaller-frozen (`sys._MEIPASS`) contexts.

**Gallery panes** (GetIt, ZXDB, zxArt, Unite!) share the `GalleryCell` widget and a common pagination + async thumbnail-fetch pattern. Feature flags in `zxnu_config.py` (`ZX_NEXT_UNITE_SHOW_ZXDB_PANE`, `ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS`, etc.) can hide entire panes or their download actions without touching the UI code.

**Optional itch.io tab** — built only when the optional `itch-dl` package is importable (`zxnu_itchio.itchdl_available()`). Authentication is a personal itch.io API key stored in `hdfg.cfg` (`SETTING_ITCHIO_API_KEY`); browsing of the user's collections/owned games/search uses the public itch.io API directly, while installing a selected item is delegated to `itch-dl` (downloaded to `downloads/itchio/`). The tab registers itself with the shared `_fav_fetchers` dispatch so its items also appear in the Unite! multi-search when a key is set. A Settings checkbox (`SETTING_SHOW_ITCHIO_TAB`, default on) shows/hides the tab the same way the Alien Floyd's tab toggle does.

**Crash logging** is opt-in (Settings → "Enable crash log file generation"). `_zxnu_open_crash_log()` wires both `sys.excepthook` and `threading.excepthook` to a file alongside the executable, and `faulthandler` to catch C-level crashes.
