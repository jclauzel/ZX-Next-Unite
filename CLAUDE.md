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

Or install everything (including the optional extras `pygame-ce`, `itch-dl`,
`Send2Trash` and `flask` — Send2Trash powers the "Send deleted files to the
Recycle Bin" Settings toggle, Flask the NextSync HTTP bridge; each toggle is
greyed out until its package is installed) with:

```
python -m pip install -r REQUIREMENTS.txt
```

No build step is needed for development. The test suite lives in `tests/` — run it all with `python tests/run_all.py` (plain scripts, no pytest; see `tests/README.md`).

Linting: `ruff check .` must stay green (config in `pyproject.toml` — deliberately permissive so the legacy monolith isn't churned; don't add ignores casually, and don't `ruff format` legacy files wholesale — format only new modules).

For quickly eyeballing the retro "Alien Floyd" Sir Clive promenade animations
(the ones drawn in `zxnu_pygame.py`), pass `--anim` to force one to play first
and switch the mode on without touching Settings — e.g.
`python zx-next-unite.py --anim aliens`. Choices: `walk`, `c5`, `ufo`, `aliens`
(the last spawns Clive's saucer plus the attacking green alien squadron).
Requires pygame.

`python zx-next-unite.py -start-remote-explorer-listener` opens the NextSync
tab's Remote Explorer view and starts its `.sync5 -listen` server right at
startup using the saved sync root (this run only — the saved Settings are not
modified; without a saved sync root the usual "pick a sync root first"
advisory is logged instead).

## Packaging (optional)

Create a standalone executable with PyInstaller:

```
pip install pyinstaller
pyinstaller --clean --onefile --windowed --noupx zx-next-unite.py
```

If the optional itch.io feature is in use, also fully bundle `itch-dl` and its
dependencies so installs work in the frozen exe (its submodules are not pulled
in by a bare `import itch_dl`, and the in-process installer in `zxnu_itchio.py`
imports `itch_dl.config` / `handlers` / `downloader` / `keys` / `api`):

```
pyinstaller --clean --onefile --windowed --noupx --collect-all itch_dl --collect-all bs4 zx-next-unite.py
```

Note: in a frozen build `sys.executable` is the GUI exe, not a Python
interpreter, so itch-dl must be driven in-process — never via
`[sys.executable, "-m", "itch_dl", …]`, which would just relaunch the app. See
`_install_in_process` in `zxnu_itchio.py`.

## Regenerating embedded Qt resources

Background images are compiled into `rc_backgrounds.py` from `rc_backgrounds.qrc`. Regenerate after adding/removing image assets:

```
pyside6-rcc rc_backgrounds.qrc -o rc_backgrounds.py
```

## Source file map

| File | Role |
|---|---|
| `zx-next-unite.py` | Entry point; contains the single `MainWindow(QMainWindow)` class and most tab/pane UI logic (shrinking via the strangler extraction below) |
| `zxnu_sdcard_explorer.py` | `SdCardExplorerPane`: the SD Card tab's dual local ⇄ disk-image explorer (widgets + navigation/model layer, incl. the lazy `hdfmonkey ls` tree). First strangler extraction from `MainWindow.__init__`: the operation layer (transfers, deletes, context menus, DnD, load pipeline) stays in `zx-next-unite.py`, reaching the pane through a documented `hooks` protocol; MainWindow keeps aliases under the historical attribute names (`self.treeview`, `self.image_treeview`, …) plus one-line delegating wrappers, so existing code and the test suite work unchanged. Also the single source of the `IMG_*_ROLE` item-data constants |
| `zxnu_config.py` | Constants, `SETTING_*` keys, API base URLs, UI string tables, color defaults, and pure helpers (`resource_path`, `qcolor_to_hex`, etc.) |
| `zxnu_api.py` | Online catalogue API layer (strangler extraction #2): shared HTTP retry helpers plus the GetIt / ZXDB / zxArt fetchers, response parsers, website-URL builders and thread-safe zxArt name caches. Pure Python — no Qt — unit-tested by `tests/test_api_parsers.py`; star-imported by `zx-next-unite.py` so historical names keep working |
| `zxnu_workers.py` | Background threading primitives: `WorkerSignals`, `NextSyncSignals`, `HdfTaskSignals`, `HdfTaskWorker`, `HdfProgressDialog`, `DotDotFirstProxyModel`; the NextSync `-listen` worker (`run_remote_listen_server` + `RemoteExplorerSignals`) behind the Remote Explorer; and (strangler extraction #3) the classic Sync3/Sync4 server loop `run_classic_sync_server` (tested by `tests/test_classic_sync.py`) plus the hdfmonkey transfer worker bodies (`_run_get_task` / `_run_put_task` / `_run_delete_task`, scan helpers, sync framing + `getFileList`/syncpoint helpers) — all UI-free, driven by injected callables |
| `zxnu_remote_explorer.py` | `RemoteExplorerWidget`: the dual-pane local ⇄ Next file manager of the NextSync tab (drives the `-listen` worker via a command queue; covered headlessly by `tests/test_remote_listen.py` for the worker side) |
| `zxnu_http_bridge.py` | NextSync HTTP bridge: reusable Flask web server (stdlib-only import; Flask optional, loaded on start — `flask_available()` gates the UI) republishing a `-listen` session as HTTP routes for the Next's `.http` dot command. Used by the app (Settings toggle + port and max-connections boxes, `SETTING_NEXTSYNC_HTTP_BRIDGE`/`SETTING_NEXTSYNC_HTTP_PORT`/`SETTING_NEXTSYNC_HTTP_CONNECTION_LIMIT`, greyed without Flask) and `nextsync5.py -w`/`-http[=port]` (+`-flask-connection-limit:<n>`, default 1); docs + call samples in `nextsync/sync/server/HTTP_BRIDGE.md`, e2e test in `tests/test_http_bridge.py`. Optional bearer-token protection: `NextSyncHttpBridge(auth_token=…)` installs a `before_request` guard (constant-time `hmac.compare_digest` on the `ZXNEXTUNITE-BRIDGE-TOKEN` header, else HTTP 401); the app exposes it via a Settings "Require bearer token" checkbox + token field + Generate button (`SETTING_NEXTSYNC_HTTP_TOKEN_ENABLED`/`SETTING_NEXTSYNC_HTTP_TOKEN`, off by default — a 64-char token minted by `generate_bridge_token()` on first enable) |
| `nextsync5.py` | Standalone Sync4 NextSync command-line server (moved to the repo root so it sits next to `zxnu_http_bridge.py`); `-listen` console, `-w`/`-http[=port]` web bridge, `-v` also traces every HTTP request/response. Protocol tests in `tests/test_listen.py` |
| `zxnu_media.py` | ZX Spectrum `SCREEN$` decoder (`ZxSpectrumScreen`), placeholder-pixmap rendering, file-format tag helpers, and the shared pixmap cache |
| `zxnu_gallery.py` | Reusable gallery widgets: `GalleryCell`, the scrollable grid view (`GalleryView`), the `AnimatedBackground` widget, and `_DblClickFilter` (the double-click event filter shared by all three gallery panes' preview labels) |
| `zxnu_getit_pane.py` | `build_getit_pane(host, …)`: the GetIt (`zxnext.uk`) gallery tab's ~1.5k-line UI-construction blob — the first of the three gallery-pane builder extractions and the template the ZXDB/zxArt twins follow (see `zxnu_zxdb_pane.py` for the seam details). Same injected-hooks + `host.getit_*` attribute pattern; outputs re-bound to `__init__` are `getit_run_search`/`getit_on_latest`/`getit_on_random`/`_getit_open_gallery_viewer` |
| `zxnu_zxdb_pane.py` | `build_zxdb_pane(host, …)`: the ZXDB (ZXInfo API v3) gallery tab's ~3k-line UI-construction blob (widgets + navigation + search/detail/download/context-menu closures), strangled out of `MainWindow.__init__`. Unlike the SD-Card pane this is a *builder function*, not a class: every closure that assigned `self.zxdb_*` now writes `host.zxdb_*`, so MainWindow keeps all its historical `zxdb_*` attributes automatically. The `__init__`-locals it reads (tab spinners, cross-search dispatch, `execute_hdf_monkey`, `save_configuration_file`, shared retro/gallery UI helpers, the module-global disk-image content) are injected as keyword-only params — passed by identity when defined before the call, as forwarding lambdas when defined later in `__init__`. The four closures the rest of `__init__` still calls (`zxdb_run_search`/`zxdb_on_latest`/`zxdb_on_random`/`_zxdb_open_gallery_viewer`) are written to `host` and re-bound to bare `__init__` locals right after the call |
| `zxnu_zxart_pane.py` | `build_zxart_pane(host, …)`: the zxArt (zxart.ee) gallery tab — the twin of `zxnu_zxdb_pane.py`, built with the identical seam (see that row). Its extra dependencies are the zxArt name-resolution/scrape helpers and the `_zxart_lang`/`_zxart_set_language`/`_zxart_tr` i18n helpers (all from `zxnu_config`/`zxnu_api`). Both pane modules are guarded by `tests/test_pane_imports.py`, a free-variable tripwire asserting the builder resolves every name it uses (params + imports + star exports), which catches the star-import-skips-underscores trap |
| `zxnu_unite_pane.py` | `build_unite_pane(host, …)` + `build_unite_ops(host, …)`: the Unite! (AllInOne) tab, extracted as two builders sharing the gallery panes' seam. The first is the widget layer (search row, table/gallery stack, preview panel, tab insertion + colour-cycle timer), called at the tab-construction spot; the second is the operation layer (aggregation of the last GetIt/ZXDB/zxArt/itch.io results, fan-out Search/Latest/Random, merged autocomplete, the view-mode apply helper and the optional pygame "Retro" mode), called right after the itch.io tab block at the ops blob's historical position — the view-mode/pygame sections were folded in from further down (safe: they only define closures/connect signals; their one construction-time call touches only widgets built by the first builder). Inside the three pygame closures the original local variable `host` was renamed `pg_host` so it can't shadow the builder's `host` param. Guarded by `tests/test_pane_imports.py` |
| `zxnu_settings_pane.py` | `build_settings_pane(host, …)`: the whole Settings-tab construction blob (~1.6k lines — every settings row widget plus its inline persistence-handler closures: self-update/theme/colors/gallery/NextSync HTTP bridge + bearer token/MAME/CSpect/Alien Floyd's/itch.io/conflict policy/sort mode…). Writes every historical `self.settings_*` attribute to `host` (load_configuration_file restores checkbox state through them) and hands back `host.settings_scroll` for the monolith's one remaining `addTab` line |
| `zxnu_nextsync_pane.py` | `build_nextsync_pane(host, …)`: the NextSync tab's widget + wiring layer (classic local explorer with drive combo/filter/tree, sync-root row, log window + retro toggle, the Remote Explorer ⇄ Classic experience selector, the `RemoteExplorerWidget` + `-listen` server-control block, HTTP-bridge plumbing, sync-mode radio group, start/cancel/progress row). The `nextsync_*` operation closures stay in the monolith and are injected as keyword-only params; `nextsync_container` and `_re_try_send_folder` are handed back as host attributes and re-bound to bare locals at the call site. `available_drives` must stay a param (not module state): the non-Windows path appends to the `__init__` list without ever rebinding it first |
| `zxnu_i18n.py` | UI internationalisation (ES/PT/PL/RU/CS/FR): per-language catalogs keyed by the exact English source string plus `translate_widget_tree`, a walk over the constructed widget tree that swaps label/button/checkbox/radio/groupbox/placeholder/tooltip texts in place (no tr() wrapping, no .ts/.qm toolchain). Each widget's source text is cached so switching languages — including back to English — is lossless, and app-rewritten texts are adopted, not clobbered. Deliberately never touches QTabWidget titles or QComboBox item texts (dispatch keys / compared option values). Driven by Settings → "Application language:" (`SETTING_UI_LANGUAGE`, live re-translate) and applied at the end of `MainWindow.__init__`; runtime-generated strings (logs, toasts, dialogs) stay English until their call sites adopt `ui_tr`. Tested by `tests/test_i18n.py` + offscreen phase 9 |
| `zxnu_itchio.py` | Optional itch.io integration: `itch-dl` detection, itch.io API access (collections/owned/search via the user's API key) and install-via-`itch-dl`. Drives the optional itch.io tab |
| `rc_backgrounds.py` | Auto-generated Qt resource module (do not edit by hand) |

## Key architecture patterns

**Configuration** is stored in `hdfg.cfg` (a `key = value` file, INI-like) created in the same directory as the script. All setting key names are `SETTING_*` constants in `zxnu_config.py`. The `_initialising` guard on `MainWindow` prevents `save_configuration_file()` from firing while widgets are being constructed.

**Threading** follows a consistent pattern: long-running work (hdfmonkey commands, network fetches, NextSync transfers) runs in a `QRunnable` submitted to `QThreadPool`. Results are marshalled back to the UI thread via `Signal`/`Slot`. The signal classes (`HdfTaskSignals`, `NextSyncSignals`, etc.) live in `zxnu_workers.py`.

**External tool dependencies** — `hdfmonkey` is invoked as a subprocess for all HDF image operations. When it is missing, the app can auto-download a pre-compiled build for the current platform (Windows/Linux/macOS) via the "Download and install HDF Monkey" button. The source is the jjjs release at `HDF_MONKEY_JJJS_URL` (`specnext.com` forum attachment `id=1159`): a zip-inside-a-zip whose inner archive is ZipCrypto-encrypted (password in `password.txt`, currently `jjjs`) and carries per-platform folders (`windows-64/`, `linux-musl/`, `macos-intel/`, `macos-mn/`). `extract_hdfmonkey_from_jjjs_zip` (in `zxnu_config.py`) unpacks just the current platform's binary into `downloads/hdfmonkey/<platform>/`, which `find_hdfmonkey_in_downloads` re-discovers on later launches. The recommended route remains a full CSpect install from the itch.io tab, which also bundles hdfmonkey (discovered by `find_hdfmonkey_near_cspect` / `find_emulators_in_downloads`). CSpect and MAME are detected via PATH and launched as subprocesses. `resource_path()` in `zxnu_config.py` handles path resolution for both development (source tree) and PyInstaller-frozen (`sys._MEIPASS`) contexts.

**Gallery panes** (GetIt, ZXDB, zxArt, Unite!) share the `GalleryCell` widget and a common pagination + async thumbnail-fetch pattern. Feature flags in `zxnu_config.py` (`ZX_NEXT_UNITE_SHOW_ZXDB_PANE`, `ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS`, etc.) can hide entire panes or their download actions without touching the UI code.

**Optional itch.io tab** — built only when the optional `itch-dl` package is importable (`zxnu_itchio.itchdl_available()`). Authentication is a personal itch.io API key stored in `hdfg.cfg` (`SETTING_ITCHIO_API_KEY`); browsing of the user's collections/owned games/search uses the public itch.io API directly, while installing a selected item is delegated to `itch-dl` (downloaded to `downloads/itchio/`). The tab registers itself with the shared `_fav_fetchers` dispatch so its items also appear in the Unite! multi-search when a key is set. A Settings checkbox (`SETTING_SHOW_ITCHIO_TAB`, default on) shows/hides the tab the same way the Alien Floyd's tab toggle does.

**Logging** has two layers. An **always-on rotating diagnostic log** (`zx-next-unite.log` next to the exe, `%TEMP%` fallback; `RotatingFileHandler`, ~1 MB × 3) is configured by `_zxnu_configure_logging()` and captures all `logging.*` output plus uncaught exceptions — critical because in a `--windowed` build `sys.stderr` is `None`, so without it every `logging.error(...)` is thrown away. Prefer `logging.exception(...)` (UI stays quiet) over `except …: pass` for load-bearing failures; cosmetic best-effort guards (deleted-widget `RuntimeError`, tooltip/animation) can stay bare. **Crash logging** on top of that is opt-in (Settings → "Enable crash log file generation"): `_zxnu_open_crash_log()` wires `faulthandler` (and the excepthooks' extra file write) to `zx-next-unite-crash.log` for C-level crashes.
