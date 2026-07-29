#!/usr/bin/env python3

"""
    zx-next-unite by Julien Clauzel based on:

        HDFM-GOOEY, Getit by em00k
    &
        NextSync by Jari Komppa and Julien Clauzel
    & 
        ZXDB by https://api.zxinfo.dk/
    &
        ZXArt by https://zxart.ee/

    * Requirements:
        - Python 3.10+ (the release binaries bundle 3.13)
        - pyside6
        - CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
            feel free to support his development efforts & patreon https://www.patreon.com/mikedailly
            - Make sure Spectrum Next roms installed are installed in local directory (they should be provided in the CSpect zip package by default).
                These two files namely: enNextZX.rom and enNxtMMC.rom -MUST- be placed in the root folder of your #CSpect.
        - You will need Spectrum Next images files that you can download from https://zxspectrumnext.online/cspect/  such as https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-2gb.zip
        - hdfmonkey by Matt Westcott https://github.com/gasman/hdfmonkey is installed automatically when missing: use the
            'Download and install HDF Monkey' button (bottom right of the SD Card tab). It fetches a pre-compiled build for
            your platform (Windows / Linux / macOS). The recommended route is a full CSpect install from the itch.io tab,
            which bundles hdfmonkey as well.
        - On Mac/Linux you will need to install mono-complete

    * Additional help pages:
        - https://wiki.specnext.dev/Development_Tools:Linux_setup

    * First install pyside6 this is required for the UI to render the different controls being used:
        python -m pip install pyside6

    * Copy Cspect (with the Spectrum Next roms) and hdfmonkey in the same directory (see above).

            - hdfmonkey -

        If hdfmonkey is not present, you will see an error message in the main log window as it is missing.
           if that is the case you will see a 'Download and install HDF Monkey' button bottom right; once clicked it fetches
           a pre-compiled build for your platform (Windows / Linux / macOS) and installs it under downloads/hdfmonkey/.
               If the above automated install is successful, you should then be able to select an image and navigate it.

        Alternatively, install hdfmonkey manually based on the instructions for your platform at https://github.com/gasman/hdfmonkey ,
        or (recommended) do a full CSpect install from the itch.io tab, which bundles hdfmonkey too.

    * On Windows: OpenAL sound library is required for CSpect you may download it from here: https://openal.org/

    * On Mac/Linux: you will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete

    * Start zx-next-unite.py
        python zx-next-unite.py

    * Windows executables can be created using pyinstaller and upx https://upx.github.io/ & https://github.com/upx/upx/: 
    To update embedded images use: pyside6-rcc rc_backgrounds.qrc -o rc_backgrounds.py

    pip install pyinstaller
    pyinstaller --onefile --windowed --upx-dir C:\\upx --collect-all itch_dl --collect-all bs4 zx-next-unite.py
    pyinstaller --onefile --windowed --upx-dir C:\\upx zx-next-unite.py
    pyinstaller --onefile --windowed --noupx zx-next-unite.py
"""

# Standard library imports
import ctypes
import datetime
import faulthandler
import os as _os_early
import sys as _sys_early
import traceback as _tb_early

# ---------------------------------------------------------------------------
# Crash / unhandled-exception log
# ---------------------------------------------------------------------------
# When the app is packaged with `pyinstaller --windowed`, sys.stderr is None,
# so any exception raised inside a Qt slot (e.g. a double-click handler that
# opens GalleryItemViewer) is silently swallowed and the user just sees
# "nothing happens". To make such failures diagnosable on end-user machines
# we redirect both faulthandler and sys.excepthook to a log file next to the
# executable (or in %TEMP% as a fallback).
#
# Generation of the log file is gated by the "crash_log_enabled" setting
# stored in hdfg.cfg (Settings pane → "Enable crash log file generation").
# Default is False — no file is produced unless the user opts in.
def _zxnu_early_state_dir():
    """Where per-user state (logs, hdfg.cfg, downloads/) lives, resolved
    WITHOUT importing zxnu_config — logging must be configured before any
    heavy import so import failures are still captured. MUST mirror
    zxnu_config._zxnu_compute_data_root(); see the rationale there. Not
    __file__-based: this module may live in site-packages since the
    zxnu_main.py rename."""
    override = _os_early.environ.get("ZX_NEXT_UNITE_HOME", "").strip()
    if override:
        d = _os_early.path.abspath(_os_early.path.expanduser(override))
    elif getattr(_sys_early, "frozen", False):
        d = _os_early.path.dirname(_os_early.path.abspath(_sys_early.executable))
    elif _os_early.environ.get("ZX_NEXT_UNITE_MODE", "").strip().lower() == "installed":
        if _sys_early.platform == "win32":
            base = _os_early.environ.get("APPDATA") or _os_early.path.expanduser("~")
        elif _sys_early.platform == "darwin":
            base = _os_early.path.expanduser("~/Library/Application Support")
        else:
            base = (_os_early.environ.get("XDG_DATA_HOME", "").strip()
                    or _os_early.path.expanduser("~/.local/share"))
        d = _os_early.path.join(base, "zx-next-unite")
    else:
        d = _os_early.path.dirname(_os_early.path.abspath(_sys_early.argv[0]))
    try:
        _os_early.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _zxnu_crash_log_path():
    try:
        base = _zxnu_early_state_dir()
        candidate = _os_early.path.join(base, "zx-next-unite-crash.log")
        # Probe writability
        with open(candidate, "a", encoding="utf-8"):
            pass
        return candidate
    except Exception:
        try:
            import tempfile as _tf
            return _os_early.path.join(_tf.gettempdir(), "zx-next-unite-crash.log")
        except Exception:
            return None

def _zxnu_read_crash_log_pref():
    """Return True if the user previously enabled crash-log generation.

    Parses hdfg.cfg directly (the full config loader runs much later) and
    looks for `crash_log_enabled = true/1`. Any error or missing key →
    default False.
    """
    try:
        cfg_path = _os_early.path.join(_zxnu_early_state_dir(), "hdfg.cfg")
        if not _os_early.path.isfile(cfg_path):
            return False
        with open(cfg_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "=" not in line:
                    continue
                k, v = line.strip().split("=", 1)
                if k.strip() == "crash_log_enabled":
                    v = v.strip().lower()
                    return v in ("1", "true", "yes", "on")
    except Exception:
        pass
    return False

_ZXNU_CRASH_LOG = _zxnu_crash_log_path()
_ZXNU_CRASH_FH  = None


def _zxnu_log_file_path():
    """Path for the ALWAYS-ON rotating diagnostic log (`zx-next-unite.log`),
    next to the executable/script, with a %TEMP% fallback. Distinct from the
    opt-in crash log: this one captures the app's ordinary logging output so
    the many `logging.error(...)` calls are actually recoverable in the field
    — in a `--windowed` PyInstaller build `sys.stderr` is None, so without a
    file handler every log line is otherwise thrown away."""
    try:
        base = _zxnu_early_state_dir()
        candidate = _os_early.path.join(base, "zx-next-unite.log")
        with open(candidate, "a", encoding="utf-8"):
            pass
        return candidate
    except Exception:
        try:
            import tempfile as _tf
            return _os_early.path.join(_tf.gettempdir(), "zx-next-unite.log")
        except Exception:
            return None


_ZXNU_LOG_FILE = _zxnu_log_file_path()

def _zxnu_open_crash_log():
    """Open the crash-log file handle and wire faulthandler to it."""
    global _ZXNU_CRASH_FH
    if _ZXNU_CRASH_FH is not None:
        return
    if not _ZXNU_CRASH_LOG:
        return
    try:
        _ZXNU_CRASH_FH = open(_ZXNU_CRASH_LOG, "a", encoding="utf-8", buffering=1)
        _ZXNU_CRASH_FH.write("\n=== zx-next-unite start %s ===\n" %
                             datetime.datetime.now().isoformat(timespec="seconds"))
    except Exception:
        _ZXNU_CRASH_FH = None
        return
    try:
        faulthandler.enable(file=_ZXNU_CRASH_FH)
    except Exception:
        pass

def _zxnu_close_crash_log():
    """Close the crash-log file handle (best-effort)."""
    global _ZXNU_CRASH_FH
    try:
        faulthandler.disable()
    except Exception:
        pass
    fh = _ZXNU_CRASH_FH
    _ZXNU_CRASH_FH = None
    if fh is not None:
        try:
            fh.close()
        except Exception:
            pass

def _zxnu_set_crash_log_enabled(enabled: bool):
    """Runtime toggle invoked from the Settings checkbox.

    When *enabled* is True, opens the crash-log file (if not already open).
    When False, closes the handle and deletes the file so no log is produced.
    """
    if enabled:
        _zxnu_open_crash_log()
    else:
        _zxnu_close_crash_log()
        if _ZXNU_CRASH_LOG:
            try:
                if _os_early.path.isfile(_ZXNU_CRASH_LOG):
                    _os_early.remove(_ZXNU_CRASH_LOG)
            except Exception:
                pass

# Honour the persisted preference at startup. Default: disabled.
if _zxnu_read_crash_log_pref():
    _zxnu_open_crash_log()

def _zxnu_excepthook(exc_type, exc_value, exc_tb):
    # KeyboardInterrupt should still terminate normally.
    if issubclass(exc_type, KeyboardInterrupt):
        _sys_early.__excepthook__(exc_type, exc_value, exc_tb)
        return
    msg = "".join(_tb_early.format_exception(exc_type, exc_value, exc_tb))
    # Always record uncaught exceptions in the rotating diagnostic log, even
    # when the opt-in crash file is off — otherwise a slot/thread exception in
    # the --windowed build is completely silent ("nothing happens"). Guarded
    # so a logging failure here can never re-enter the excepthook.
    try:
        import logging as _lg_early
        _lg_early.error("Unhandled exception:\n%s", msg)
    except Exception:
        pass
    if _ZXNU_CRASH_FH is not None:
        try:
            _ZXNU_CRASH_FH.write(
                "\n--- Unhandled exception %s ---\n%s" %
                (datetime.datetime.now().isoformat(timespec="seconds"), msg))
            _ZXNU_CRASH_FH.flush()
        except Exception:
            pass
    # Also try the original hook (no-op in --windowed but useful when run from
    # a console).
    try:
        _sys_early.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass

_sys_early.excepthook = _zxnu_excepthook

# PySide6 routes slot exceptions through sys.excepthook only if
# threading.excepthook is also installed; cover both.
try:
    import threading as _th_early
    def _zxnu_thread_excepthook(args):
        _zxnu_excepthook(args.exc_type, args.exc_value, args.exc_traceback)
    _th_early.excepthook = _zxnu_thread_excepthook
except Exception:
    pass
import fnmatch
import glob
import json
from types import SimpleNamespace
import logging
import os
import pathlib
import platform
import re
import shlex
import shutil
import socket
import stat
import string
import struct
import subprocess
import sys
import tempfile
import threading
import time
import concurrent.futures
import traceback
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
import zipfile

# Third-party imports
from PySide6 import QtCore
from PySide6.QtCore import (
    QDir,
    QEvent,
    QMetaObject,
    QMimeData,
    QModelIndex,
    QObject,
    QRect,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    QStringListModel,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
    qInstallMessageHandler,
)
from PySide6.QtCore import Q_ARG
from PySide6.QtGui import QAction, QColor, QDrag, QGuiApplication, QIcon, QImage, QFontInfo, QPainter, QPixmap, QFont
from PySide6.QtGui import QImageReader, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QRadioButton,
    QLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

import rc_backgrounds
# --- Extracted modules (refactored out of this file) ------------------
from zxnu_config import *
from zxnu_workers import *
from zxnu_sdcard_explorer import (SdCardExplorerPane, IMG_PATH_ROLE,
                                  IMG_ISDIR_ROLE, IMG_LOADED_ROLE,
                                  IMG_LOADING_ROLE)
from zxnu_remote_explorer import RemoteExplorerWidget
# NextSync HTTP bridge: a self-hosted Flask web server republishing the Remote
# Explorer's -listen session as HTTP routes (for the Next's .http dot command).
# Importing is always safe — Flask itself is optional and only imported when
# the server is started (Settings → "Enable NextSync HTTP bridge");
# flask_available() gates the Settings checkbox without importing anything.
from zxnu_http_bridge import NextSyncHttpBridge, QueueBridgeHost, flask_available
from zxnu_media import *
from zxnu_gallery import *
from zxnu_gallery import _DblClickFilter  # star import skips underscore names
from zxnu_getit_pane import build_getit_pane
from zxnu_zxdb_pane import build_zxdb_pane
from zxnu_zxart_pane import build_zxart_pane
from zxnu_unite_pane import build_unite_pane, build_unite_ops
from zxnu_settings_pane import build_settings_pane
from zxnu_nextsync_pane import build_nextsync_pane
from zxnu_itchio_pane import build_itchio_pane
from zxnu_emulator_ops import build_emulator_ops
from zxnu_favorites_pane import (build_favorites_helpers,
    build_favorites_pane, build_favorites_ops)
from zxnu_i18n import (normalize_ui_language, system_ui_language,
                       translate_widget_tree, ui_tr, ui_tr_now)
import zxnu_itchio
# ----------------------------------------------------------------------


# ---------------------------------------------------------------------------
# zxArt legal-status code -> human-readable label.
#
# Codes come from the zxart.ee API (see https://zxart.ee/eng/about/api/ and
# the values observed in the `legalStatus` field of `zxProd` entries).  The
# table is the source of truth; unknown codes encountered at runtime are
# memoised in ``ZXART_LEGAL_STATUS_CACHE`` (initialised from the static map)
# so the same value is translated only once per session.


# ----------------------------------------------------------------------
# Custom completer that stays in sync with the main window.
# ----------------------------------------------------------------------
from PySide6.QtCore import QMargins
from PySide6.QtGui import QCursor


# class _MovableCompleter(QCompleter):
#     """
#     A tiny wrapper around QCompleter that ensures its popup
#     window stays attached to the main window and is repositioned
#     whenever the main window is moved.

#     The fix works by:
#       * Using a custom QCompleter instance that tracks its last\n
#         `QWidget.showEvent` call.
#       * Listening to QApplication.topLevelChanged or the\n
#         main window's `moveEvent` to reposition the popup.

#     This addresses the PySide6 bug where the auto‑generated\n
#     QCompleter popups are created with Qt.ToolTip flags and\n    become independent windows.
#     """
#     def __init__(self, parent=None):
#         super().__init__(parent)
#         self._popup = None
#         self._track_parent = parent is not None
#         self._last_parent_win = None

#     def _reposition_popup(self):
#         if not self.popup().isVisible():
#             return
#         # Grab the line edit's geometry in global screen coordinates
#         line_edit = self.parent()
#         if line_edit is None:
#             return
#         # Position popup just below the line edit.
#         pos = line_edit.mapToGlobal(line_edit.rect().bottomLeft())
#         # The popup itself might be top‑level (Qt.ToolTip), we make it child
#         # of the current active window if possible.
#         parent_win = QGuiApplication.activeWindow()
#         if parent_win:
#             self.popup().setParent(parent_win, Qt.WindowFlags(parent_win.windowFlags() | Qt.Widget))
#         # Move the popup manually (this forces relayout)
#         self.popup().move(pos)

#     def showPopup(self):
#         super().showPopup()
#         # Qt may have already positioned it wrongly; enforce relayout
#         QTimer.singleShot(0, self._reposition_popup)

#     def setWidget(self, w):
#         """
#         Hook into the widget where the completer gets attached.
#         """
#         super().setWidget(w)
#         # Make sure the popup is correctly parented if the widget changes
#         QTimer.singleShot(0, self._reposition_popup)


# def _ensure_completer_is_movable(completer: QCompleter):
#     """
#     Replaces the standard QCompleter with our wrapper if needed.
#     """
#     if isinstance(completer, _MovableCompleter):
#         return  # already fixed
#     # Store configuration of the old completer
#     model = completer.completerModel() if hasattr(completer, "completerModel") else completer.model()
#     completion_mode = completer.completionMode()
#     popup_visible = completer.popup().isVisible()
#     # Create the new custom completer
#     fixed = _MovableCompleter(completer.parent())
#     fixed.setModel(model)
#     fixed.setCompletionMode(completion_mode)
#     # Replace the completer on the line edit
#     fixed.setWidget(completer.parent())
#     if popup_visible:
#         # Re‑show the popup if it was visible already
#         fixed.popup().show()
#     return fixed






# UI translation table for the zxArt pane.  Keys are the English source
# strings; values map language codes -> localised label.  Strings not
# present in the table fall back to the source key.



# Build the disclaimer text once from INIT_HELP (the "Legal disclaimer:" block)
def _build_disclaimer_text():
    lines = []
    inside = False
    for line in INIT_HELP:
        if line.strip().startswith("Legal disclaimer:"):
            inside = True
        if inside:
            if line.strip() == "Enjoy!":
                break
            lines.append(line)
    return "\n".join(lines).rstrip()

_DISCLAIMER_TEXT = _build_disclaimer_text()

# Single-line cycling ticker text derived from the disclaimer (spaces join lines)
_DISCLAIMER_TICKER_TEXT = "  •  ".join(
    l.strip() for l in _DISCLAIMER_TEXT.splitlines() if l.strip()
) + "     "


def _make_disclaimer_ticker(parent):
    """Return a (QLabel, QTimer) pair that scrolls the legal disclaimer
    continuously across the label.  The caller must add the label to a layout
    and keep both the label and timer alive (e.g. by parenting to *parent*).
    The timer is started automatically and stops when *parent* is destroyed."""
    _COLORS = ("#ff4444", "#4488ff", "#ffee00")
    lbl = QLabel(parent)
    lbl.setFixedHeight(30)
    lbl.setTextFormat(Qt.PlainText)
    # We scroll a doubled copy so the cycle is seamless
    _full = _DISCLAIMER_TICKER_TEXT + _DISCLAIMER_TICKER_TEXT
    _state = {"pos": 0, "text": _full, "step": 1, "color_idx": 0, "color_tick": 0}

    def _tick():
        t = _state["text"]
        p = _state["pos"]
        visible = t[p:p + 120]
        lbl.setText(visible)
        _state["pos"] = (p + _state["step"]) % len(_DISCLAIMER_TICKER_TEXT)
        # Cycle colour every 8 ticks (~480 ms)
        _state["color_tick"] += 1
        if _state["color_tick"] >= 8:
            _state["color_tick"] = 0
            _state["color_idx"] = (_state["color_idx"] + 1) % len(_COLORS)
        color = _COLORS[_state["color_idx"]]
        lbl.setStyleSheet(
            f"QLabel {{ font-size: 22px; font-weight: bold; color: {color}; "
            "background: transparent; padding: 0 4px; }"
        )

    timer = QTimer(parent)
    timer.setInterval(60)   # ~16 chars/sec at step=1
    timer.timeout.connect(_tick)
    timer.start()
    _tick()  # populate immediately so label isn't blank on first paint
    return lbl, timer



assert sys.version_info >= (3, 10), (
    "zx-next-unite requires Python 3.10+ (current PySide6/pygame-ce/itch-dl "
    "releases all set that floor; CI tests 3.10 and 3.13)")

# Configure logging: an ALWAYS-ON rotating file handler (so diagnostics
# survive the `--windowed` build where sys.stderr is None and console output
# is lost), plus a console handler when a real stderr exists (source/console
# runs). This is independent of the opt-in crash log (faulthandler + uncaught
# C-level crashes) toggled in Settings — that stays off by default.
def _zxnu_configure_logging():
    import logging.handlers
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    if _ZXNU_LOG_FILE:
        try:
            # ~1 MB per file, 3 rotations kept — enough to capture a session's
            # worth of failures without growing unbounded on disk.
            fh = logging.handlers.RotatingFileHandler(
                _ZXNU_LOG_FILE, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:
            # A locked/unwritable log file must never stop the app starting.
            logging.getLogger(__name__).warning(
                "Could not open the rotating log file %s", _ZXNU_LOG_FILE)
    if getattr(sys, "stderr", None) is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


_zxnu_configure_logging()
if _ZXNU_LOG_FILE:
    logging.info("=== zx-next-unite %s starting (log: %s) ===",
                 ZX_NEXT_UNITE_VERSION, _ZXNU_LOG_FILE)



# ---------------------------------------------------------------------------
# The online catalogue API layer (shared HTTP retry helpers + the GetIt /
# ZXDB / zxArt fetchers, parsers, URL builders and name-resolution caches)
# lives in zxnu_api.py — pure Python, no Qt, unit-tested by
# tests/test_api_parsers.py. Star-imported so the historical names keep
# working throughout this module.
# ---------------------------------------------------------------------------
from zxnu_api import *
# Star import skips underscore-prefixed names, so the private helpers the
# code below still uses are imported explicitly (tests/test_api_parsers.py
# has a tripwire asserting this list stays complete).
from zxnu_api import (_filter_download_urls, _http_fetch_bytes_with_retry,
                      _http_fetch_with_cd_retry, _http_head_ok_with_retry,
                      _zxart_author_col_cached, _zxart_prefetch_names_for_entries,
                      _zxart_resolve_author_name, _zxart_resolve_author_names,
                      _zxart_resolve_group_name, _zxart_resolve_group_names,
                      _zxart_resolve_publisher_names,
                      _zxart_scrape_publishers_from_prod_url)

# ---------------------------------------------------------------------------
# GetIt QRunnable workers (must be module-level for stable C++ type identity)
# ---------------------------------------------------------------------------

# Module-level registry that strongly references in-flight WorkerSignals
# objects until the queued slot invocation has actually completed on the main
# thread. Without this the worker thread terminates (dropping its sole strong
# reference), Python GC then destroys the QObject *before* the queued event
# has been dispatched, and Qt ends up delivering a signal to a deleted C++
# sender — which on Windows manifests as an access violation in the main
# thread's event loop. This bug was especially visible at startup with the
# Gallery view active because dozens of WorkerSignals objects are created
# and destroyed in rapid succession.
_GETIT_INFLIGHT_SIGNALS = set()
_GETIT_INFLIGHT_LOCK = threading.Lock()


def _popup_height_for(popup, row_count: int, max_visible: int = 8,
                      max_pixels: int = 320) -> int:
    """Compute a completer popup height that fits *row_count* rows (capped at
    *max_visible* rows / *max_pixels* px) using the view's actual row height,
    so theme/stylesheet row metrics are respected and the list doesn't end up
    showing just one row with a scrollbar."""
    try:
        row_h = popup.sizeHintForRow(0)
    except Exception:
        row_h = 0
    if row_h <= 0:
        try:
            fm = popup.fontMetrics()
            row_h = fm.height() + 6
        except Exception:
            row_h = 22
    visible = max(1, min(max_visible, row_count))
    frame = 0
    try:
        frame = 2 * popup.frameWidth()
    except Exception:
        pass
    return min(max_pixels, row_h * visible + frame + 4)


def _qimage_from_data(data) -> QImage:
    """Decode raw image *bytes* into a QImage. Safe to call off the GUI thread
    (unlike QPixmap, which must be constructed on the main thread): worker
    threads can do the expensive decode here and the UI thread then turns the
    result into a QPixmap with the cheap ``QPixmap.fromImage()`` (the pixels are
    already decoded). Returns a null QImage on failure.

    This keeps thumbnail decoding off the UI thread so populating a gallery —
    especially the Unite! tab, which fetches from every source at once — no
    longer stutters while many images are decoded back-to-back on the main
    thread."""
    img = QImage()
    try:
        if data:
            img.loadFromData(data)
    except Exception:
        return QImage()
    return img


# Bounds how many *gated* (gallery thumbnail/asset) fetches do their HTTP +
# image-decode work at once. A full gallery page can kick off dozens of cells;
# without this cap they would all run simultaneously, starving the UI thread
# (Python GIL) and flooding the remote servers — the Unite! Latest/Random hang.
# Threads are still daemon (so app exit never blocks on an in-flight fetch); the
# semaphore just throttles concurrent work to GALLERY_THUMB_FETCH_WORKERS.
_THUMB_FETCH_SEM = threading.Semaphore(max(1, int(GALLERY_THUMB_FETCH_WORKERS)))


def getit_run_in_thread(fn, on_result, on_error, gated=False):
    """Run *fn* in a daemon thread. Results are marshalled to the main thread
    via Qt queued signal connections, which are thread-safe.

    When *gated* is True the thread waits on a shared bounded semaphore before
    doing its work, so a page full of gallery cells can't run dozens of
    concurrent fetch/decode operations at once (which hangs the UI). Leave it
    False for user-driven one-off work (searches, library builds, installs)
    that should start immediately.

    The WorkerSignals object is parented to the QApplication and kept alive in
    a module-level registry until *after* the main-thread slot has executed,
    avoiding a race where the QObject is garbage-collected while a queued
    signal is still being dispatched into Python widgets."""
    app = QApplication.instance()
    signals = WorkerSignals(app)  # parent to QApplication for stable ownership

    with _GETIT_INFLIGHT_LOCK:
        _GETIT_INFLIGHT_SIGNALS.add(signals)

    def _release(_obj=signals):
        # Runs on the main thread (queued slot). Drop our hard reference and
        # schedule Qt-side deletion via deleteLater so Qt finishes any pending
        # bookkeeping for this sender before the C++ object is destroyed.
        with _GETIT_INFLIGHT_LOCK:
            _GETIT_INFLIGHT_SIGNALS.discard(_obj)
        try:
            _obj.deleteLater()
        except RuntimeError:
            pass

    # Use Qt::QueuedConnection explicitly so user callbacks always run on the
    # main (GUI) thread, even if `fn` happens to complete synchronously.
    signals.result.connect(on_result, Qt.QueuedConnection)
    signals.error.connect(on_error,  Qt.QueuedConnection)
    signals.finished.connect(_release, Qt.QueuedConnection)

    def _run():
        # Phase 1 — run the user function. Capture its outcome locally so that
        # only genuine exceptions raised by *fn* are reported via the error
        # signal. Emitting is deliberately kept out of this try/except so a
        # failed emit can never be misclassified as an fn() error.
        try:
            payload = fn()
            emit_error = False
        except Exception as exc:
            payload = (type(exc), exc, "")
            emit_error = True

        # Phase 2 — marshal the outcome back to the main thread. These emits can
        # race with application shutdown: the QApplication (and therefore the
        # WorkerSignals child parented to it) may already be destroyed at the
        # C++ level, in which case Qt raises "RuntimeError: Signal source has
        # been deleted". There is no live receiver left to notify, so swallow
        # it instead of letting the daemon thread crash.
        try:
            if emit_error:
                signals.error.emit(payload)
            else:
                signals.result.emit(payload)
        except RuntimeError:
            pass
        finally:
            # Emitted last so _release is enqueued *after* result/error and
            # therefore runs only once the receiver slot has been dispatched.
            try:
                signals.finished.emit()
            except RuntimeError:
                pass

    target = _run
    if gated:
        # Throttle concurrent gallery fetch/decode work to the semaphore's
        # permit count; the thread blocks here (cheaply) until a slot frees.
        def _run_gated():
            _THUMB_FETCH_SEM.acquire()
            try:
                _run()
            finally:
                _THUMB_FETCH_SEM.release()
        target = _run_gated

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t


def _gif_fetch_bytes(url, on_bytes):
    """Fetch raw image bytes for *url* off the UI thread and deliver them to
    ``on_bytes(bytes_or_None)`` on the main thread. Used by GalleryItemViewer and
    by the gallery thumbnail cells (GalleryCell) to play animated GIFs as a
    looping QMovie (the static-image path stays QPixmap-based)."""
    def _fn(_u=url):
        return _http_fetch_bytes_with_retry(_u, timeout=20)
    getit_run_in_thread(_fn, lambda data: on_bytes(data),
                        lambda _e: on_bytes(None), gated=True)


# Readable-text extensions surfaced as Pygame item-viewer "log console" pages.
_GALLERY_TEXT_EXTS = (".txt", ".nfo", ".diz", ".asc", ".md")


def _gallery_text_urls(files):
    """From a list of file/download descriptors, return the URLs that point at a
    readable text file (instructions/.nfo/…), preserving order and de-duping.

    *files* items may be download dicts (``{"url", "filename"/"fileName"/"name",
    "type", "format"}``) or ``(url, name)`` pairs. Detection is by the file
    name's extension, so an extension-less download URL is still recognised when
    its filename is known (the Pygame viewer is told to treat it as text via
    GalleryItemViewer-compatible add_text_pages)."""
    out, seen = [], set()
    for f in files or []:
        if isinstance(f, dict):
            url = f.get("url") or f.get("path") or ""
            name = (f.get("filename") or f.get("fileName")
                    or f.get("name") or url)
        elif isinstance(f, (tuple, list)) and len(f) >= 2:
            url, name = f[0], f[1]
        else:
            url, name = f, f
        if not url or url in seen:
            continue
        base = str(name or url).lower().split("?", 1)[0].split("#", 1)[0]
        if base.endswith(_GALLERY_TEXT_EXTS):
            seen.add(url)
            out.append(url)
    return out


def _gallery_add_text_pages(viewer, files):
    """Surface any readable text files in *files* as console pages on *viewer*
    (no-op for the Qt GalleryItemViewer, which has no add_text_pages). Returns
    the list of URLs added so callers can decide whether a fallback description
    page is still needed."""
    add = getattr(viewer, "add_text_pages", None)
    if add is None:
        return []
    urls = _gallery_text_urls(files)
    if urls:
        add(urls)
    return urls


def _gallery_add_description_page(viewer, description, label="Description"):
    """Surface the item's description/About text as a Pygame log-console page
    (no-op for the Qt viewer or an empty description). Used as the readable-text
    fallback for sources without a standalone .txt file."""
    add = getattr(viewer, "add_text_document", None)
    if add is None or not description:
        return
    add(label, description)


def _wrap_flow_row(flow_layout):
    """Put a FlowLayout on a fresh QWidget and enable height-for-width so the
    wrapping toolbar can grow taller (onto extra rows) when the window is too
    narrow to fit it on one line. Returns the container widget to add to the
    parent layout."""
    w = QWidget()
    w.setAttribute(Qt.WA_TranslucentBackground)
    w.setAutoFillBackground(False)
    w.setLayout(flow_layout)
    sp = w.sizePolicy()
    sp.setHeightForWidth(True)
    w.setSizePolicy(sp)
    return w


def _make_retro_toggle_button(window, flag_attr, status_cb=None, on_change=None):
    """Build a checkable "Classic ↔ Retro" toggle button for a gallery pane.

    When checked the pane opens items in the Retro (pygame) item viewer — which
    renders .txt/instruction pages as a green log console — instead of the
    Classic Qt viewer. The chosen mode is stored on *window* as *flag_attr*
    (e.g. ``_zxdb_item_retro``). The label mirrors the SD/NextSync/Unite! pygame
    buttons: it shows the mode you will switch *to* ("🎮 Retro" while Classic,
    "🖼 Switch to 'Classic' view mode" while Retro). *on_change(checked)* — when
    given — is called after
    a successful toggle so the caller can persist the choice. Returns the
    QPushButton."""
    btn = QPushButton("🎮 Retro")
    btn.setCheckable(True)
    btn.setToolTip(
        "Open items in the Retro (pygame) viewer.\n"
        "Retro mode renders instruction/.txt pages as a green log console.\n"
        "Requires pygame-ce (pip install pygame-ce).")
    setattr(window, flag_attr, False)

    def _toggled(checked):
        if checked:
            try:
                from zxnu_pygame import pygame_available
                ok, why = pygame_available()
            except Exception as exc:
                ok, why = False, str(exc)
            if not ok:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
                btn.setToolTip(f"{why}\n" + zxnu_optional_install_hint("pygame-ce"))
                if status_cb is not None:
                    try:
                        status_cb("Retro mode unavailable — install pygame-ce "
                                  "(pip install pygame-ce, or for pipx: "
                                  "pipx inject zx-next-unite pygame-ce)")
                    except Exception:
                        pass
                return
            setattr(window, flag_attr, True)
            btn.setText("🖼 Switch to 'Classic' view mode")
        else:
            setattr(window, flag_attr, False)
            btn.setText("🎮 Retro")
        if on_change is not None:
            try:
                on_change(checked)
            except Exception:
                pass

    btn.toggled.connect(_toggled)
    return btn







def _apply_completer_fix_to_children(widget: QWidget):
    for child in widget.findChildren(QWidget):
        # If the child itself uses a completer, replace it
        if isinstance(child, QLineEdit):
            comp = child.completer()
            if comp is not None:
                _ensure_completer_is_movable(comp)
        # Recursively patch deeper levels
        _apply_completer_fix_to_children(child)

# ---------------------------------------------------------------------------
# Item-data roles for the SD-card image explorer tree (QTreeView +
# QStandardItemModel). The disk image is a virtual filesystem reachable only
# through hdfmonkey, so every tree item carries its full in-image path plus the
# bookkeeping needed for lazy expansion.
# ---------------------------------------------------------------------------
# The IMG_* item-data roles now live in zxnu_sdcard_explorer (the pane owns
# the tree); imported below so the operation layer here keeps using them.

# Custom MIME type carrying image-explorer entries during a drag from the
# SD-card image tree to the local file explorer. The payload is UTF-8 text,
# one entry per line, each line "D\t<path>" (directory) or "F\t<path>" (file).
# It lets a drag out of the virtual disk image trigger a "get to disk" the same
# way the ':<-' button does (local files already carry real text/uri-list URLs).
IMAGE_DRAG_MIME = "application/x-zxnu-image-paths"


class _CompleterPopupHider(QtCore.QObject):
    """Hide a manually-shown autocomplete popup when its line edit loses focus.

    The search panes show their ``QCompleter`` popup themselves (``popup().show()``)
    as a non-grabbing ``Qt.Tool`` window so the user can keep typing while the
    suggestion list stays up.  Because the popup is shown manually — not via
    ``QCompleter.complete()`` — QCompleter does not manage its lifetime, and a
    ``Qt.Tool`` (unlike a ``Qt.Popup``) window does NOT auto-close when the user
    clicks outside it.  Without this filter the suggestion list lingers on top
    of the UI after the user clicks away (e.g. onto the Pygame/Classic toggle,
    a gallery tile or another tab), which makes the rest of the pane — most
    visibly the search box — feel unclickable.  Hiding it on the line edit's
    FocusOut dismisses it for every "clicked elsewhere" path in one place.
    """

    def __init__(self, line_edit, completer, parent=None):
        super().__init__(parent)
        self._line_edit = line_edit
        self._completer = completer
        line_edit.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._line_edit and event.type() == QtCore.QEvent.FocusOut:
            # Typing-fix guard: keep the suggestion list up when focus is
            # leaving the search box *because the user is interacting with the
            # popup itself* (i.e. clicking a suggestion).  Hiding it here would
            # swallow that click and break mouse selection.  Qt flags a popup-
            # driven focus change with PopupFocusReason; as a cross-platform
            # backstop we also keep it up while the cursor is over the (top-
            # level) popup window.
            popup = None
            try:
                popup = self._completer.popup()
            except Exception:
                popup = None
            try:
                reason = event.reason()
            except Exception:
                reason = None
            keep_up = reason == QtCore.Qt.PopupFocusReason
            if not keep_up and popup is not None and popup.isVisible():
                try:
                    keep_up = popup.geometry().contains(QCursor.pos())
                except Exception:
                    keep_up = False
            if not keep_up and popup is not None:
                try:
                    popup.hide()
                except Exception:
                    pass
        return False


class MainWindow(QMainWindow):

    def _show_toast(self, title: str, message: str = "", *, variant: str = "green",
                    duration_ms: int = 10000, rich: bool = False,
                    corner: str = "bottom-right"):
        """Show a small, auto-dismissing toast anchored to a window corner
        (``corner``: "bottom-right", the default, or "bottom-left").

        ``variant`` selects the colour scheme:
          - "green"  : success / informational (default)
          - "yellow" : warning / advisory
          - "red"    : error / failure

        The toast disappears automatically after ``duration_ms`` milliseconds,
        or immediately when the user clicks the OK button. When ``rich`` is True
        the message is rendered as rich text (HTML) with clickable external
        links (use ``<br>`` for line breaks and ``<a href=…>`` for links);
        otherwise it is plain text so embedded URLs and ``\\r\\n`` line breaks
        are shown verbatim.
        """
        # Runtime i18n chokepoint: static toast titles/bodies translate here
        # via exact catalog match (see the toasts section of zxnu_i18n).
        # Composed/dynamic strings must instead be built from ui_tr_now()
        # TEMPLATES at their call sites — already-translated or unmatched
        # text passes through unchanged.
        title = ui_tr_now(title)
        message = ui_tr_now(message)
        # Colour schemes per variant: (bg, border, title_fg, btn_bg, btn_border,
        # btn_hover).
        if variant == "yellow":
            scheme = ("#2e2a14", "#f0c000", "#f7eec5", "#7d6a2e", "#f0c000", "#8f7c38")
        elif variant == "red":
            scheme = ("#2e1a1a", "#e05a4f", "#f7d5d2", "#7d3230", "#e05a4f", "#8f3a38")
        else:
            scheme = ("#1e2a1e", "#4caf50", "#c8f7c5", "#2e7d32", "#4caf50", "#388e3c")
        bg, border, title_fg, btn_bg, btn_border, btn_hover = scheme
        try:
            toast = QWidget(self, Qt.Tool | Qt.FramelessWindowHint)
            toast.setAttribute(Qt.WA_DeleteOnClose, True)
            toast.setObjectName("zxnu_toast")
            toast.setStyleSheet(
                f"#zxnu_toast {{ background: {bg}; border: 1px solid {border};"
                " border-radius: 8px; }"
            )
            lay = QVBoxLayout(toast)
            lay.setContentsMargins(14, 12, 14, 12)
            lay.setSpacing(8)

            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                f"color: {title_fg}; font-weight: bold; background: transparent;"
            )
            lay.addWidget(title_lbl)

            if message:
                msg_lbl = QLabel(message)
                msg_lbl.setWordWrap(True)
                msg_lbl.setMaximumWidth(360)
                msg_lbl.setStyleSheet("color: #e8e8e8; background: transparent;")
                if rich:
                    msg_lbl.setTextFormat(Qt.RichText)
                    msg_lbl.setOpenExternalLinks(True)
                lay.addWidget(msg_lbl)

            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            ok_btn = QPushButton("OK")
            ok_btn.setStyleSheet(
                f"QPushButton {{ color: #eee; background: {btn_bg}; border: 1px solid"
                f" {btn_border}; border-radius: 4px; padding: 4px 18px; }}"
                f"QPushButton:hover {{ background: {btn_hover}; }}"
            )
            btn_row.addWidget(ok_btn)
            lay.addLayout(btn_row)

            toast.adjustSize()

            # Position in the requested corner of the main window, and
            # remember the toast so moveEvent/resizeEvent keep it anchored
            # there while it is up (dead entries are pruned on reposition).
            # The corner rides on the widget so _reposition_toasts keeps
            # honouring it after window moves/resizes.
            toast.setProperty("zxnu_toast_corner", corner)
            self._position_toast(toast)
            if not hasattr(self, "_live_toasts"):
                self._live_toasts = []
            self._live_toasts.append(toast)

            timer = QTimer(toast)
            timer.setSingleShot(True)
            timer.setInterval(max(500, duration_ms))

            def _dismiss():
                try:
                    timer.stop()
                except Exception:
                    pass
                try:
                    toast.close()
                except Exception:
                    pass

            timer.timeout.connect(_dismiss)
            ok_btn.clicked.connect(_dismiss)

            toast.show()
            toast.raise_()
            timer.start()
        except Exception:
            pass

    def _position_toast(self, toast):
        """Anchor *toast* to its corner of the main window (the
        "zxnu_toast_corner" widget property: bottom-right unless set)."""
        try:
            geo = self.frameGeometry()
            if (toast.property("zxnu_toast_corner") or "") == "bottom-left":
                x = geo.left() + 24
            else:
                x = max(geo.left() + 8, geo.right() - toast.width() - 24)
            y = max(geo.top() + 8, geo.bottom() - toast.height() - 24)
            toast.move(x, y)
        except Exception:
            pass

    def _reposition_toasts(self):
        """Re-anchor every live toast after the main window moved or resized.
        Toasts delete themselves on close (WA_DeleteOnClose), so entries whose
        C++ widget is already gone are pruned here."""
        alive = []
        for t in getattr(self, "_live_toasts", []):
            try:
                if t.isVisible():
                    self._position_toast(t)
                    alive.append(t)
            except RuntimeError:
                pass
        self._live_toasts = alive

    def _show_emulator_detection_toast(self):
        """Show a startup toast reporting which emulators (CSpect / MAME) were
        detected on the system. A green toast lists the emulators found; if none
        are found a yellow advisory toast is shown instead. Auto-dismisses after
        5 seconds.
        """
        found = []
        if getattr(self, "_cspect_executable_path", None):
            found.append("CSpect")
        # MAME counts as available when a binary was detected or the Flatpak
        # launch option is enabled (Linux) — so a Flatpak-only setup doesn't
        # trigger the "no emulators detected" advisory.
        _mame_ok = bool(getattr(self, "_mame_usable", None)) and self._mame_usable()
        if _mame_ok:
            found.append("Mame")

        if found:
            body = ui_tr_now("Found: {emulators}.").format(
                emulators=ui_tr_now(" and ").join(found))
            # Append the resolved CSpect / hdfmonkey paths so the user can see
            # exactly which copy will be used (PATH, app directory, or a bundled
            # itch.io install discovered under downloads/cspect).
            cspect_path = getattr(self, "_cspect_executable_path", None)
            if cspect_path:
                body += "\r\nCSpect: " + cspect_path
            hdfmonkey_path = getattr(self, "_hdfmonkey_executable_path", None)
            if not hdfmonkey_path:
                hdfmonkey_path = (shutil.which(HDFMONKEY_EXECUTABLE)
                                  or shutil.which(HDFMONKEY_EXECUTABLE + ".exe"))
            if hdfmonkey_path:
                body += "\r\nhdfmonkey: " + hdfmonkey_path
            # Likewise show the resolved MAME path (usually found on PATH), or
            # note the Flatpak launch mode when that is how MAME will run.
            mame_path = getattr(self, "_mame_executable_path", None)
            if mame_path:
                body += "\r\nMame: " + mame_path
            elif (getattr(self, "_mame_flatpak_enabled", None)
                  and self._mame_flatpak_enabled()):
                body += "\r\n" + ui_tr_now("Mame: via Flatpak ({app})").format(
                    app=MAME_FLATPAK_APP_ID)
            # After a fresh itch.io CSpect install (one-shot flag), append the
            # Windows-only reminder to install OpenAL 1.1 \u2014 CSpect has no sound
            # on Windows without it (Linux/macOS ship OpenAL, so it's skipped
            # there). The link is clickable and the toast stays up \u2265 1 minute.
            _openal = (self._cspect_openal_notice_pending
                       and "CSpect" in found
                       and platform.system() == "Windows")
            self._cspect_openal_notice_pending = False
            if _openal:
                rich_body = body.replace("\r\n", "<br>")
                rich_body += "<br><br>" + ui_tr_now(
                    "\u26a0 On Windows, CSpect needs <b>OpenAL 1.1</b> "
                    "for sound. If you have no audio, install it from "
                    "<a href=\"https://www.openal.org/\">openal.org</a>.")
                self._show_toast(
                    "\u2705  CSpect installed",
                    rich_body,
                    variant="green",
                    duration_ms=65000,
                    rich=True,
                )
            else:
                self._show_toast(
                    "\u2705  Emulator(s) detected",
                    body,
                    variant="green",
                    duration_ms=5000,
                )
        else:
            _suppress = self.settings_disable_no_emulator_toast_checkbox.isChecked()
            if not _suppress:
                self._show_toast(
                    "\u26a0  No emulators detected",
                    "Neither CSpect nor Mame were found. Add the emulator(s) to your operating system PATH environment variable so they can be launched from here. \r\n\r\n"
                    "CSpect: https://mdf200.itch.io/cspect \r\nMame: https://wiki.specnext.dev/MAME:Installing",
                    variant="yellow",
                    duration_ms=10000,
                )

    def _show_hdfmonkey_installed_toast(self, hdfmonkey_path: str):
        """Show a green success toast after hdfmonkey is auto-installed via the
        'Download and install HDF Monkey' button, confirming the install and the
        exact location on disk of the binary that will be used.

        Mirrors _show_emulator_detection_toast, but fires independently of any
        emulator (CSpect / MAME) state — hdfmonkey is the SD-card tool, so its
        install should be reported on its own. Auto-dismisses after 8 seconds.
        """
        body = ui_tr_now("hdfmonkey has been installed and is ready to use.")
        if hdfmonkey_path:
            body += "\r\n" + ui_tr_now("Location: {path}").format(
                path=hdfmonkey_path)
        self._show_toast(
            "✅  hdfmonkey installed",
            body,
            variant="green",
            duration_ms=8000,
        )

    def _show_sd_notification(self, message: str):
        """Show a small, auto-dismissing toast confirming that a
        'Send to SD card' task has completed.

        The toast appears in the bottom-right corner of the main window and
        disappears automatically after 10 seconds, or immediately when the
        user clicks the OK button. Used by the GetIt / ZXDB / zxArt / Unite!
        gallery viewers which otherwise only update a status label the user
        may not notice.
        """
        self._show_toast(
            "\u2705  Send to SD card complete",
            message or "The file was sent to the SD card image.",
            variant="green",
            duration_ms=10000,
        )

    def __init__(self, *args, **kwargs):
        global right_disk_image_explorer_content
        super(MainWindow, self).__init__(*args, **kwargs)

        # Prevent any save_configuration_file() calls from firing while widgets
        # are being constructed and signals are being connected — the real config
        # has not been loaded yet at that point.
        self._initialising = True

        right_disk_image_explorer_path = []
        right_disk_image_explorer_content = []
        right_disk_image_path = ""
        self.right_disk_image_path = ""
        right_disk_image_selected_files = []
        # Current selection inside the SD-card image explorer tree. These drive
        # all image-side operations (download/upload/delete/new-folder) now that
        # the explorer is a hierarchical tree rather than a directory-by-directory
        # table. image_selected_path is the full in-image path of the selected
        # item ("" when nothing is selected); image_selected_is_dir says whether
        # that item is a directory.
        self.image_selected_path = ""
        self.image_selected_is_dir = False
        # All currently selected image entries as (full_path, is_dir) tuples.
        # image_selected_path stays as the "primary"/current item (used by
        # uploads and New Folder); this list drives multi-file deletion.
        self.image_selected_paths = []
        configuration_dictionary = {}
        # Initialise defaults for settings that may not exist in older cfg files
        configuration_dictionary[SETTING_CONTENT_DISCLAIMER_AGREED] = ""
        configuration_dictionary[SETTING_ZXART_LANGUAGE] = DEFAULT_ZXART_LANGUAGE
        # MAME command line is customisable through the cfg file; seed it with the
        # default so first-run cfg files persist a value the user can edit later.
        configuration_dictionary[SETTING_MAME_COMMAND_LINE_PARAMETERS] = MAME_DEFAULT_COMMAND_LINE
        # MAME ROM/system choice (e.g. "tbblue"); seeded with the first entry so a
        # first-run cfg persists a value the user can change in the Settings tab.
        configuration_dictionary[SETTING_MAME_ROM_CHOICE] = MAME_ROM_CHOICE[0]
        # CSpect mouse-capture combo index; a cfg file predating this option has no
        # entry, so default to 0 ("Mouse On" — no parameter passed) to avoid a
        # KeyError when the CSpect settings are restored below.
        configuration_dictionary[SETTING_MOUSE] = ""
        # CSpect default launch parameters (free-text, editable in Settings).
        # Seeded with the built-in default so a first-run cfg persists a value the
        # user can tweak later; launch_cspect appends the SD Card group options on
        # top (mirroring the MAME default-command-line handling).
        configuration_dictionary[SETTING_CUSTOM] = CSPECT_DEFAULT_LAUNCH_PARAMETERS
        # Startup "is a newer MAME available?" check (default on; "false" to skip)
        # and the release tag of the MAME build installed via the app (used to
        # compare against the latest release without re-running the binary).
        # Seeded so the config writer never KeyErrors on these new keys.
        configuration_dictionary[SETTING_MAME_UPDATE_CHECK] = ""
        configuration_dictionary[SETTING_MAME_INSTALLED_TAG] = ""
        # "Launch Mame with Flatpak" (Linux): off by default. The rom directory
        # passed to Flatpak MAME as -rompath is seeded with the per-user default
        # (~/roms) so a first-run cfg persists an editable value.
        configuration_dictionary[SETTING_MAME_FLATPAK] = "false"
        configuration_dictionary[SETTING_MAME_FLATPAK_ROMPATH] = default_mame_flatpak_rompath()
        # Optional pygame-ce "Alien Floyd's" features (both default off).
        configuration_dictionary[SETTING_ALIEN_FLOYD_BG] = ""
        configuration_dictionary[SETTING_ALIEN_FLOYD_TAB] = ""
        configuration_dictionary[SETTING_ALIEN_FLOYD_HISCORE] = "0"
        configuration_dictionary[SETTING_ALIEN_FLOYD_HISCORES] = ""
        # How to treat a file/dir received via ".sync5 -send" that already exists
        # locally: "prompt" (ask, default), "overwrite" (always), "ignore" (never
        # touch). Seeded so a first-run cfg persists a value.
        configuration_dictionary[SETTING_NEXTSYNC_SEND_CONFLICT] = DEFAULT_NEXTSYNC_SEND_CONFLICT

        # Detect the MAME emulator, applying the platform's search precedence
        # (see resolve_mame_executable). On Windows the PATH copy wins, falling
        # back to a previous in-app "Install MAME" build under downloads/mame. On
        # Linux/macOS the order is reversed: an app-managed downloads/mame build
        # is preferred over an (often older) PATH ``mame`` that may predate the ZX
        # Spectrum Next (tbblue) driver. When a build is found a "Launch Mame"
        # button is shown; when absent on Windows, the SD Card tab's "Install
        # MAME" button is offered (Linux/macOS have no official binary and are
        # detection-only — but Linux can still launch MAME via Flatpak, below).
        try:
            self._mame_executable_path = resolve_mame_executable(
                ZXNU_DATA_ROOT)
        except Exception:
            self._mame_executable_path = find_mame_executable()
        # Guard so a second click can't start a concurrent MAME install.
        self._mame_installing = False

        # ── Flatpak launch helpers (Linux) ────────────────────────────────
        # When "Launch Mame with Flatpak" is enabled, MAME is launchable even
        # without a detected local binary (Flatpak provides it), and every
        # "Launch Mame" button is relabelled "(flatpak)". These read the live cfg
        # value so a toggle takes effect immediately. Stored on self so both the
        # nested launch/wiring closures and the Settings handlers can reach them.
        def _mame_flatpak_enabled():
            if not mame_flatpak_supported():
                return False
            return str(configuration_dictionary.get(SETTING_MAME_FLATPAK, "")
                       ).strip().lower() in ("true", "1", "yes", "on")

        def _mame_usable():
            """True when MAME can be launched: a local binary was detected, or
            the Flatpak launch option is enabled (Linux)."""
            return (getattr(self, "_mame_executable_path", None) is not None
                    or _mame_flatpak_enabled())

        def _mame_launch_label():
            return ("🕹  Launch Mame (flatpak)" if _mame_flatpak_enabled()
                    else "🕹  Launch Mame")

        self._mame_flatpak_enabled = _mame_flatpak_enabled
        self._mame_usable = _mame_usable
        self._mame_launch_label = _mame_launch_label

        # Detect whether the CSpect emulator is available (application directory
        # or PATH). When absent, all CSpect controls are hidden. A background
        # scan of downloads/cspect (see the startup code) may later fill this in
        # from an itch.io CSpect install; _cspect_from_downloads records when that
        # bundled copy is in use so launch_cspect can run it from its own folder.
        self._cspect_executable_path = find_cspect_executable()
        self._cspect_from_downloads = False

        # Full path to a bundled hdfmonkey executable discovered under
        # downloads/cspect (Windows/Linux/macOS — the itch.io CSpect package
        # ships a build per platform). None means "use the PATH/'hdfmonkey'
        # default". execute_hdf_monkey and _hdfmonkey_binary_found prefer it.
        self._hdfmonkey_executable_path = None
        # Set True while the async downloads/cspect scan is in flight so the
        # startup emulator-detection toast waits for its results instead of
        # firing on a fixed timer.
        self._emulator_scan_pending = False
        # Strong reference to the in-flight downloads/cspect scan worker. The
        # generic Worker creates an unparented WorkerSignals, so this keeps the
        # worker and its signals alive until the scan finishes (otherwise Qt
        # drops the queued result/finished events when the sender is collected).
        self._emulator_scan_worker = None
        # One-shot: set True by an itch.io CSpect install so the next
        # emulator-detection toast appends the Windows-only "install OpenAL for
        # sound" notice (CSpect needs OpenAL 1.1 for audio on Windows). Consumed
        # by _show_emulator_detection_toast so it never fires on a plain startup
        # detection.
        self._cspect_openal_notice_pending = False
        # One-shot guard for the startup itch.io CSpect update check: it can be
        # kicked from two places (the startup timer and the itch.io post-login
        # callback), and _cspect_update_installing guards against overlapping
        # download+install jobs. See _check_cspect_update_async.
        self._cspect_update_checked = False
        self._cspect_update_installing = False

        # Live QColor instances for the image explorer — updated by Settings pickers
        self.img_color_up_directory = hex_to_qcolor(DEFAULT_COLOR_UP_DIRECTORY)
        self.img_color_dir_name     = hex_to_qcolor(DEFAULT_COLOR_DIR_NAME)
        self.img_color_dir_type     = hex_to_qcolor(DEFAULT_COLOR_DIR_TYPE)
        self.img_color_file_name    = hex_to_qcolor(DEFAULT_COLOR_FILE_NAME)
        self.img_color_file_ext     = hex_to_qcolor(DEFAULT_COLOR_FILE_EXT)
        self.img_color_file_size    = hex_to_qcolor(DEFAULT_COLOR_FILE_SIZE)
        # App-wide Classic-mode UI text colour (labels/checkboxes on the dark panes).
        self.img_color_general_text = hex_to_qcolor(DEFAULT_COLOR_GENERAL_TEXT)
        # Retro 8-bit (pygame) log console text colour (overridden on cfg load).
        self.img_color_retro_log    = hex_to_qcolor(DEFAULT_COLOR_RETRO_LOG)
        # Desktop theme mode driving the colours above (overridden on cfg load).
        self._desktop_theme_mode    = DEFAULT_DESKTOP_THEME

        self.left_file_explorer_selection_file_name = ""
        self.left_file_explorer_selection_full_filename_path = ""

        # Shared cross-explorer Copy/Paste clipboard (Ctrl+C / Ctrl+V). Holds
        # {"source": "local"|"image", "items": [(path, is_dir), …]} or None.
        # The serials track recency so a fresh OS-clipboard copy (e.g. Ctrl+C in
        # Windows Explorer) is preferred over a stale internal copy and vice versa.
        self._explorer_clipboard = None
        self._clip_serial_counter = 0
        self._explorer_clip_serial = 0
        self._os_clip_serial = 0
        # The committed Classic-mode NextSync sync root ("" until chosen; always
        # a directory). Changed only by the "Set current folder as new sync root
        # folder" button, a folder path typed into the sync-root box, or the
        # saved setting restored at startup — never by merely clicking or
        # navigating in the explorer.
        self.left_file_nextsync_explorer_selection_full_filename_path = ""
        # Monotonic token used to discard stale background "prepare" scans whose
        # sync root changed before the scan finished.
        self._nextsync_scan_generation = 0
        # Hard references to in-flight "prepare" scan workers, for the same
        # reason as _image_ls_workers below: a fire-and-forget worker can be
        # garbage-collected (with its signals QObject) before its queued
        # `finished` slot is dispatched, dropping the slot. Discarded in
        # _on_scan_done.
        self._nextsync_scan_workers = set()
        # Monotonic token bumped every time the image explorer tree is wiped and
        # reloaded (e.g. switching disk images). Background "hdfmonkey ls" workers
        # capture it at start and abort in their finish handler if it changed, so a
        # late-completing listing never repopulates a tree that has moved on (and
        # never touches QStandardItems that were deleted by image_model.clear()).
        self._image_load_generation = 0
        # Hard references to in-flight "hdfmonkey ls" workers. Without this a
        # fire-and-forget worker (no dlg.exec() keeping it on the stack) can be
        # garbage-collected as soon as run() returns — taking its signals QObject
        # with it — before the queued `finished` slot is dispatched, so the slot
        # is silently dropped and the tree never repopulates / buttons never
        # re-enable. Each worker is discarded from here inside its finish handler.
        self._image_ls_workers = set()
        # QTimer driving the transfer-arrow idle pulse (see
        # _start_transfer_idle_animation); created lazily, None when stopped.
        self._transfer_anim_timer = None

        # Gallery (picture view) defaults — may be overridden when the cfg file loads.
        self._gallery_anim_mode      = DEFAULT_GALLERY_ANIM_MODE
        self._gallery_rows_per_page  = DEFAULT_GALLERY_ROWS_PER_PAGE
        self._gallery_cols           = DEFAULT_GALLERY_COLS
        self._gallery_img_size       = DEFAULT_GALLERY_IMG_SIZE
        # Gallery slideshow pause time (seconds); mirrored into the shared
        # zxnu_config global so every viewer picks it up.
        self._gallery_slideshow_secs = DEFAULT_GALLERY_SLIDESHOW_SECS
        set_gallery_slideshow_secs(self._gallery_slideshow_secs)
        # Unite! multi-search result ordering; overridden when the cfg loads.
        self._search_sort_mode       = DEFAULT_SEARCH_SORT_MODE
        self._getit_view_mode        = "gallery"
        self._zxdb_view_mode         = "gallery"
        self._zxart_view_mode        = "gallery"
        self._favorites_view_mode    = "gallery"
        self._allinone_view_mode     = "gallery"
        self._itchio_view_mode       = "gallery"
        # Whether itch.io items take part in the Unite! aggregation. itch.io is
        # only merged in when it actually participated in a Unite! search (and
        # the user is connected); Latest/Random browse the catalogue sources
        # (GetIt/ZXDB/zxArt) only, so prior itch.io browsing doesn't spill onto
        # later pages of the aggregated view.
        self._allinone_include_itchio = False

        # Shared gate for search autocomplete. Honours the Settings checkbox
        # (and the persisted SETTING_SEARCH_AUTOCOMPLETE value) so every pane's
        # autocomplete trigger can consult a single source of truth. Falls back
        # to the config value when the checkbox widget isn't built yet.
        def _search_autocomplete_on() -> bool:
            cb = getattr(self, "settings_search_autocomplete_checkbox", None)
            if cb is not None:
                try:
                    return cb.isChecked()
                except RuntimeError:
                    pass
            val = configuration_dictionary.get(SETTING_SEARCH_AUTOCOMPLETE, "")
            if val == "":
                return True
            return val != "0" and str(val).lower() != "false"
        self._search_autocomplete_on = _search_autocomplete_on

        # ── Favorites (cross-pane, persisted to hdfg.cfg) ──────────────
        # Each favorite is a dict: { "source": "getit"|"zxdb"|"zxart",
        #                            "id": str, "title": str,
        #                            "author": str, "year": str,
        #                            "kind": str, "image": str (optional) }
        self._favorites = []                 # list of fav dicts
        self._favorites_index = set()        # set of (source, id)
        # Re-entrancy guard: when refresh_favorites() is called on the
        # Favorites gallery itself, avoid an infinite loop.
        self._favorites_refreshing = False

        self.image_explorer_item_list = QListWidget()

        self.threadpool = QThreadPool()

        class Worker(QRunnable):

            def __init__(self, fn, *args, **kwargs):
                super(Worker, self).__init__()

                # Store constructor arguments (re-used for processing)
                self.fn = fn
                self.args = args
                self.kwargs = kwargs
                self.signals = WorkerSignals()

                # Add the callback to our kwargs
                self.kwargs['progress_callback'] = self.signals.progress

            @Slot()
            def run(self):
                '''
                Initialise the runner function with passed args, kwargs.
                '''

                # Retrieve args/kwargs here; and fire processing using them
                try:
                    result = self.fn(*self.args, **self.kwargs)
                except Exception:
                    logging.error(f"An error occurred in Worker.run: {sys.exc_info()}")
                    traceback.print_exc()
                    exctype, value = sys.exc_info()[:2]
                    try:
                        self.signals.error.emit((exctype, value, traceback.format_exc()))
                    except RuntimeError:
                        pass  # receiver destroyed during shutdown
                else:
                    try:
                        self.signals.result.emit(result)  # Return the result of the processing
                    except RuntimeError:
                        pass  # receiver destroyed during shutdown
                finally:
                    try:
                        self.signals.finished.emit()  # Done
                    except RuntimeError:
                        pass  # receiver destroyed during shutdown

        self._Worker = Worker
        def get_tuple_value(tuple_type, text_value):
            if not tuple_type:  # empty tuple
                return None

            try:
                index = next(i for i, v in enumerate(tuple_type) if v[0] == text_value)
                return tuple_type[index][1]
            except StopIteration:
                return None  # value not found

        def get_int_value(str_value: str):
            if not str_value:
                return 0
            try:
                return int(str_value)
            except ValueError:
                logging.error(f"Invalid integer value in get_int_value: {str_value}")
                return 0

        def progress_fn(n):
                # add_nextsync_log_window ("Progress: " + str(n))
                self.nextsync_progressbar.setValue(n)

        # def execute_this_fn(progress_callback):
        #     for n in range(0, 5):
        #         time.sleep(1)
        #         progress_callback.emit(n*100/4)

        #     return "Done."

        # def print_output(s):
        #     logging.info(s)

        def thread_complete():
            add_nextsync_log_window("Sync Complete!")
            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)

        def nextsync_server_exception_occured(ex):
            add_nextsync_log_window ("NextSync exception occured while syncing: " + str(ex))

        def _nextsync_on_port_in_use(port):
            # A port clash (WinError 10048 / EADDRINUSE) almost always means a
            # NextSync server is already listening - typically another running
            # copy of this app. Warn with a yellow toast so the user knows to
            # close the other instance. Runs on the UI thread (queued signal).
            add_nextsync_log_window(
                f"NextSync: port {port} is already in use — is another "
                "ZX-Next-Unite (or NextSync server) already running?")
            self._show_toast(
                "NextSync server not started",
                ui_tr_now(
                    "Port {port} is already in use.\nIs another ZX-Next-Unite "
                    "instance (or a standalone NextSync server) already "
                    "running?").format(port=port),
                variant="yellow", duration_ms=12000)

        def nextsync_hide_start_cancel_buttons():
            self.nextsync_start_server.setVisible(False)
            self.nextsync_cancel_server.setVisible(False)

        def nextsync_show_start_cancel_buttons():
            self.nextsync_start_server.setVisible(True)
            # Cancel button is no longer shown in the pane: cancelling a sync in
            # progress is handled by the modal progress dialog's Stop button.
            self.nextsync_cancel_server.setVisible(False)


        def _update_cspect_launch_tooltip():
            """Hint on the greyed-out 'Launch CSpect' button that a disk image
            must be loaded first, and clear it once one is. The button is only
            enabled after a successful hdfmonkey listing populates
            right_disk_image_explorer_content, so key the tooltip off that same
            state — that keeps the hint absent during transfers (an image is
            already loaded; the button is only transiently disabled). Qt still
            delivers tooltip help events to disabled widgets, so the hint shows
            while the button is greyed out."""
            try:
                if right_disk_image_explorer_content:
                    self.button_start_cspect.setToolTip("")
                else:
                    self.button_start_cspect.setToolTip(
                        "Load a ZX Spectrum Next disk image first — then CSpect "
                        "can boot it from the mounted SD card.")
            except (RuntimeError, AttributeError):
                pass

        def _update_mame_launch_tooltip():
            """Mirror _update_cspect_launch_tooltip for the MAME group: hint on
            the greyed-out 'Launch Mame' button that a disk image must be
            selected first, cleared once one is. Keyed off a valid image *file*
            being selected (not the hdfmonkey listing) — MAME boots the image
            directly without hdfmonkey, so that is exactly when it is launchable.
            Qt still delivers tooltip help events to disabled widgets, so the
            hint shows while the button is greyed out."""
            try:
                img = (self.imageinput.currentText() or "").strip().strip('"')
                if img and os.path.isfile(img):
                    self.button_start_mame.setToolTip("")
                else:
                    self.button_start_mame.setToolTip(
                        "Select a ZX Spectrum Next disk image (.img/.hdf) first "
                        "— then MAME can boot it as the Next's hard disk.")
            except (RuntimeError, AttributeError):
                pass

        def set_all_buttons_disabled():

            self.imageinput.setDisabled(True)
            self.selectimage.setDisabled(True)
            self.zx_next_unite_diskdrive.setDisabled(True)
            self.filterlabel.setDisabled(True)
            self.filtertext.setDisabled(True)
            self.treeview.setDisabled(True)
            self.local_explorer_up_button.setDisabled(True)
            self.local_explorer_refresh_button.setDisabled(True)
            self.image_explorer_up_button.setDisabled(True)
            self.image_explorer_refresh_button.setDisabled(True)
            self.button_to_disk.setDisabled(True)
            self.button_to_image.setDisabled(True)
            self.image_treeview.setDisabled(True)
            self.button_new_folder.setDisabled(True)
            self.button_rename.setDisabled(True)
            self.button_delete_files.setDisabled(True)
            self.new_folder_input.setDisabled(True)
            self.button_create_directory.setDisabled(True)
            self.button_start_cspect.setDisabled(True)
            self.button_start_mame.setDisabled(True)
            self.cspect_screensize.setDisabled(True)
            self.cspect_sound.setDisabled(True)
            self.cspect_vsync.setDisabled(True)
            self.cspect_joystick.setDisabled(True)
            self.cspect_mouse.setDisabled(True)
            self.cspect_frequency.setDisabled(True)
            self.cspect_esc.setDisabled(True)
            for _mame_combo in (getattr(self, "mame_aspect", None),
                                getattr(self, "mame_sound", None),
                                getattr(self, "mame_mouse", None),
                                getattr(self, "mame_joystick", None),
                                getattr(self, "mame_esc", None)):
                if _mame_combo is not None:
                    _mame_combo.setDisabled(True)
            _update_cspect_launch_tooltip()
            _update_mame_launch_tooltip()

        def set_all_buttons_enabled():
            self.imageinput.setDisabled(False)
            self.selectimage.setDisabled(False)
            self.zx_next_unite_diskdrive.setDisabled(False)
            self.filterlabel.setDisabled(False)
            self.filtertext.setDisabled(False)
            self.treeview.setDisabled(False)
            self.local_explorer_up_button.setDisabled(False)
            self.local_explorer_refresh_button.setDisabled(False)
            self.image_explorer_up_button.setDisabled(False)
            self.image_explorer_refresh_button.setDisabled(False)
            self.button_to_disk.setDisabled(False)
            self.button_to_image.setDisabled(False)
            self.image_treeview.setDisabled(False)
            self.button_new_folder.setDisabled(False)
            self.button_rename.setDisabled(False)
            self.button_delete_files.setDisabled(False)
            self.new_folder_input.setDisabled(False)
            self.button_create_directory.setDisabled(False)
            self.button_start_cspect.setDisabled(False)
            self.button_start_mame.setDisabled(False)
            self.cspect_screensize.setDisabled(False)
            self.cspect_sound.setDisabled(False)
            self.cspect_vsync.setDisabled(False)
            self.cspect_joystick.setDisabled(False)
            self.cspect_mouse.setDisabled(False)
            self.cspect_frequency.setDisabled(False)
            self.cspect_esc.setDisabled(False)
            for _mame_combo in (getattr(self, "mame_aspect", None),
                                getattr(self, "mame_sound", None),
                                getattr(self, "mame_mouse", None),
                                getattr(self, "mame_joystick", None),
                                getattr(self, "mame_esc", None)):
                if _mame_combo is not None:
                    _mame_combo.setDisabled(False)
            _update_cspect_launch_tooltip()
            _update_mame_launch_tooltip()

        def _update_mame_controls():
            """Enable the 'Launch Mame' button whenever MAME is available and a
            real disk-image file is selected — independent of hdfmonkey. The
            option combos only need MAME itself: they are persisted launch
            settings, not image operations, so they unlock as soon as a MAME
            build is available even with no image selected yet.

            Launching MAME boots the Next with the selected HDF as its hard disk
            and never calls hdfmonkey (the SD-card file-explorer tool). So a
            missing hdfmonkey must not keep the group disabled: without this,
            set_all_buttons_enabled() (which re-enables the group) only ever runs
            after a successful image *listing*, which needs hdfmonkey — so with
            hdfmonkey absent the MAME group stayed disabled even though MAME was
            installed and ready. With no image ready only the launch button is
            greyed out, showing a 'select an image first' hint (see
            _update_mame_launch_tooltip). The controls are still hidden outright
            when MAME isn't installed (setVisible(_mame_available)) and still
            hard-disabled during transfers/loads via set_all_buttons_disabled()."""
            try:
                available = self._mame_usable()
                img = (self.imageinput.currentText() or "").strip().strip('"')
                ready = available and bool(img) and os.path.isfile(img)
                self.button_start_mame.setEnabled(ready)
                for _mame_combo in (getattr(self, "mame_aspect", None),
                                    getattr(self, "mame_sound", None),
                                    getattr(self, "mame_mouse", None),
                                    getattr(self, "mame_joystick", None),
                                    getattr(self, "mame_esc", None)):
                    if _mame_combo is not None:
                        _mame_combo.setEnabled(available)
                _update_mame_launch_tooltip()
            except (RuntimeError, AttributeError):
                pass

        def _update_cspect_controls():
            """Mirror _update_mame_controls for the CSpect group: the option
            combos (screen size, sound, vsync, …) are persisted launch settings,
            so they unlock as soon as a CSpect build is available — even before
            any disk image is selected. Only the Launch button needs the mounted
            image (its -mmc= argument requires the hdfmonkey listing), so it
            alone stays gated on right_disk_image_explorer_content, with the
            'load an image first' hint tooltip. Busy states still hard-disable
            everything via set_all_buttons_disabled()."""
            try:
                available = getattr(self, "_cspect_executable_path", None) is not None
                self.button_start_cspect.setEnabled(
                    available and bool(right_disk_image_explorer_content))
                for _cspect_combo in (self.cspect_screensize, self.cspect_sound,
                                      self.cspect_vsync, self.cspect_joystick,
                                      self.cspect_mouse, self.cspect_frequency,
                                      self.cspect_esc):
                    _cspect_combo.setEnabled(available)
                _update_cspect_launch_tooltip()
            except (RuntimeError, AttributeError):
                pass

        def _refresh_mame_launch_ui():
            """Re-evaluate the SD Card tab MAME group after a change to whether
            MAME is launchable — currently the Flatpak toggle. Enabling Flatpak
            makes MAME usable with no local binary, so the group + Launch button +
            option combos are revealed and the launch button relabelled
            "(flatpak)"; disabling it hides them again (Flatpak is Linux-only,
            where no local install button is offered). Gallery viewers pick the
            state up when next opened via _wire_viewer_emulators."""
            try:
                usable = self._mame_usable()
                self.mame_group.setVisible(usable)
                self.button_start_mame.setText(self._mame_launch_label())
                self.button_start_mame.setVisible(usable)
                for _mame_combo in (getattr(self, "mame_aspect", None),
                                    getattr(self, "mame_sound", None),
                                    getattr(self, "mame_mouse", None),
                                    getattr(self, "mame_joystick", None),
                                    getattr(self, "mame_esc", None)):
                    if _mame_combo is not None:
                        _mame_combo.setVisible(usable)
                _update_mame_controls()
            except (RuntimeError, AttributeError):
                pass
        self._refresh_mame_launch_ui = _refresh_mame_launch_ui

        def enable_image_selection():
            self.imageinput.setDisabled(False)
            self.selectimage.setDisabled(False)
            # The MAME group is gated on MAME + a valid image, not on hdfmonkey —
            # so refresh it here, the resting state used when the image explorer
            # is unavailable (e.g. hdfmonkey missing or a failed load). Keeps
            # 'Launch Mame' and its option combos usable without hdfmonkey.
            _update_mame_controls()
            # Likewise re-enable the CSpect option combos (launch stays gated on
            # a mounted image) so a found emulator isn't left fully greyed out
            # just because no image is selected yet.
            _update_cspect_controls()
            # This is the "no image loaded, picker available" resting state: if
            # an emulator is ready, pulse the image-picking buttons as the hint
            # (self-stops once an image loads; no-op without an emulator).
            _start_load_image_hint_animation()

        def disable_image_selection():
            self.imageinput.setDisabled(True)
            self.selectimage.setDisabled(True)

        def _hdfmonkey_downloads_root():
            """Top-level ``downloads`` folder next to the app (created if needed).
            This is where the auto-download saves the jjjs zip and where the
            manual-fallback flow asks the user to drop a hand-downloaded copy."""
            app_dir = ZXNU_DATA_ROOT
            root = os.path.join(app_dir, DOWNLOADS_ROOT_DIRNAME)
            os.makedirs(root, exist_ok=True)
            return root

        def _download_hdfmonkey_zip():
            """STEP 1 — download the jjjs hdfmonkey archive from
            HDF_MONKEY_JJJS_URL into the downloads folder.

            Logs each stage (URL, HTTP status, content type/length, bytes read,
            save location) so a failure points at the real cause. Returns the
            saved zip path on success, or None on failure — importantly it detects
            the common specnext.com case where an HTML login / anti-robot page is
            returned with a 200 status instead of the actual attachment, rather
            than letting that non-zip flow through to a confusing extract error.
            """
            downloads_root = _hdfmonkey_downloads_root()
            dest_zip = os.path.join(downloads_root, HDF_MONKEY_JJJS_ZIP_FILENAME)
            add_main_log_window(f"hdfmonkey: [1/2] downloading archive from {HDF_MONKEY_JJJS_URL} ...")
            logging.info(f"hdfmonkey download: requesting {HDF_MONKEY_JJJS_URL}")
            try:
                req = urllib.request.Request(
                    HDF_MONKEY_JJJS_URL, headers={"User-Agent": ZXART_USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    content_type = resp.headers.get("Content-Type", "") or ""
                    content_length = resp.headers.get("Content-Length", "") or ""
                    final_url = resp.geturl()
                    add_main_log_window(
                        f"hdfmonkey: server responded HTTP {status}; "
                        f"Content-Type='{content_type}', "
                        f"Content-Length='{content_length or 'unknown'}'.")
                    logging.info(
                        f"hdfmonkey download: HTTP {status} type={content_type!r} "
                        f"len={content_length!r} final_url={final_url!r}")
                    data = resp.read()
            except urllib.error.HTTPError as e:
                add_main_log_window(
                    f"hdfmonkey: download failed — server returned HTTP {e.code} "
                    f"{e.reason}. specnext.com may require a forum login or an "
                    f"anti-robot confirmation before the file can be downloaded.")
                logging.error(f"hdfmonkey download HTTPError: {e.code} {e.reason}")
                return None
            except urllib.error.URLError as e:
                add_main_log_window(
                    f"hdfmonkey: download failed — could not reach "
                    f"{HDF_MONKEY_JJJS_URL} ({e.reason}). Check your internet "
                    f"connection, or a firewall/proxy blocking the request.")
                logging.error(f"hdfmonkey download URLError: {e.reason}")
                return None
            except Exception as e:
                add_main_log_window(f"hdfmonkey: download failed — {e}")
                logging.error(f"hdfmonkey download error: {e}")
                return None

            add_main_log_window(f"hdfmonkey: downloaded {len(data):,} bytes.")
            logging.info(f"hdfmonkey download: read {len(data)} bytes")

            # Guard against the forum returning an HTML login / anti-robot / error
            # page (often with a 200 status) instead of the real attachment. A
            # genuine zip starts with the 'PK' local-file-header magic.
            if data[:2] != b"PK":
                head = data[:200].decode("utf-8", "replace").strip()
                head = " ".join(head.split())
                lower = data[:1024].lower()
                looks_html = b"<html" in lower or b"<!doctype" in lower
                if looks_html:
                    add_main_log_window(
                        "hdfmonkey: the server returned a web page, not the zip "
                        "file — this usually means specnext.com is asking for a "
                        "login or an anti-robot confirmation before the download "
                        "starts.")
                else:
                    add_main_log_window(
                        "hdfmonkey: the downloaded data is not a zip file "
                        f"(it begins with: {head!r}).")
                logging.error(
                    f"hdfmonkey download: not a zip (html={looks_html}); "
                    f"head={head!r}")
                return None

            try:
                with open(dest_zip, "wb") as f:
                    f.write(data)
            except OSError as e:
                add_main_log_window(
                    f"hdfmonkey: could not save the downloaded zip to "
                    f"{dest_zip} — {e}")
                logging.error(f"hdfmonkey download save error: {e}")
                return None

            # Verify the pinned SHA-256 BEFORE anything touches the archive:
            # a corrupted or tampered download is refused, not extracted.
            actual = sha256_of_file(dest_zip)
            if actual.lower() != HDF_MONKEY_JJJS_SHA256:
                add_main_log_window(
                    "hdfmonkey: SHA-256 mismatch on the downloaded archive "
                    f"(expected {HDF_MONKEY_JJJS_SHA256[:12]}…, got {actual[:12]}…) "
                    "— refusing to extract it. Retry the download, or install "
                    "hdfmonkey manually (see the wiki).")
                logging.error(
                    f"hdfmonkey download hash mismatch: expected "
                    f"{HDF_MONKEY_JJJS_SHA256}, got {actual}")
                try:
                    os.remove(dest_zip)
                except OSError:
                    pass
                return None
            add_main_log_window(
                f"hdfmonkey: saved archive to {dest_zip} (SHA-256 verified).")
            logging.info(f"hdfmonkey download: saved {dest_zip}")
            return dest_zip

        def _install_hdfmonkey_from_zip(zip_path, keep_zip=False):
            """STEP 2 — extract this platform's hdfmonkey binary out of a jjjs
            archive (auto-downloaded or manually dropped) into
            downloads/hdfmonkey/<platform>/.

            The extracted binary is recorded so execute_hdf_monkey prefers it and
            re-discovered on the next launch by find_hdfmonkey_in_downloads (so
            this works on Windows, Linux and macOS). Returns the installed binary
            path on success, or None on failure. ``keep_zip`` leaves a
            user-provided archive in place; an auto-downloaded one is removed once
            it has been unpacked.
            """
            app_dir = ZXNU_DATA_ROOT
            dest_root = os.path.join(app_dir, DOWNLOADS_HDFMONKEY_DIRNAME)
            add_main_log_window(
                f"hdfmonkey: [2/2] extracting the build for this platform from "
                f"{zip_path} ...")
            logging.info(f"hdfmonkey install: extracting from {zip_path}")
            try:
                hdfmonkey_path = extract_hdfmonkey_from_jjjs_zip(zip_path, dest_root)
            except Exception as e:
                add_main_log_window(
                    f"hdfmonkey: could not extract the binary from {zip_path} — "
                    f"{e}. The archive may be incomplete or corrupted; try "
                    f"downloading it again.")
                logging.error(f"hdfmonkey install extract error: {e}")
                return None
            finally:
                if not keep_zip:
                    try:
                        os.remove(zip_path)
                    except OSError:
                        pass
            add_main_log_window(f"hdfmonkey: extracted binary to {hdfmonkey_path}.")
            logging.info(f"hdfmonkey install: extracted {hdfmonkey_path}")
            return hdfmonkey_path

        def _finish_hdfmonkey_install(hdfmonkey_path):
            """Common UI updates once hdfmonkey has been installed (whether via
            the automatic download or the manual-drop fallback)."""
            self._hdfmonkey_executable_path = hdfmonkey_path
            self.button_new_folder.setVisible(True)
            self.button_rename.setVisible(True)
            self.button_delete_files.setVisible(True)
            self.download_and_install_hdfmonkey_button.setVisible(False)
            # hdfmonkey is now installed — stop the yellow attention pulse and
            # restore the button's normal look straight away.
            _stop_hdfmonkey_button_animation()
            logging.info(f"Successfully installed hdfmonkey: {hdfmonkey_path}")
            add_main_log_window(f"Successfully installed hdfmonkey: {hdfmonkey_path}")

            # Confirm the install with a green toast (like the emulator detection
            # one) showing where the binary landed on disk.
            self._show_hdfmonkey_installed_toast(hdfmonkey_path)

            # Reload the currently-selected image straight away so the file
            # explorer repopulates without the user having to reopen it via
            # "Select NextZXOS disk Image". The extract succeeded and
            # _hdfmonkey_executable_path now points at a verified binary, so
            # there's no need to re-probe first; load_image() restores the
            # controls once the (async) listing completes, and safely no-ops when
            # no image is selected.
            load_image()

        def _try_install_hdfmonkey_from_manual_zip():
            """Look for a jjjs hdfmonkey zip the user dropped into the downloads
            folder and, if a valid one is found, install from it. Returns True on
            a successful install."""
            app_dir = ZXNU_DATA_ROOT
            manual_zip = find_hdfmonkey_jjjs_zip_in_downloads(app_dir)
            if not manual_zip:
                return False
            add_main_log_window(
                f"hdfmonkey: found a manually-downloaded archive at "
                f"{manual_zip} — installing from it.")
            logging.info(f"hdfmonkey: installing from manual zip {manual_zip}")
            # Leave a hand-placed archive in place so the user can retry offline.
            hdfmonkey_path = _install_hdfmonkey_from_zip(manual_zip, keep_zip=True)
            if hdfmonkey_path:
                _finish_hdfmonkey_install(hdfmonkey_path)
                return True
            return False

        def _prompt_manual_hdfmonkey_download():
            """After an automatic download failure, invite the user to fetch the
            zip in a browser and drop it into the downloads folder, then retry
            detecting it from there. Returns True if a manually-dropped zip was
            subsequently found and installed."""
            downloads_root = _hdfmonkey_downloads_root()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("hdfmonkey download failed")
            box.setText(
                "The automatic hdfmonkey download from specnext.com failed — the "
                "forum may be asking for a login or an anti-robot confirmation "
                "before the download can start (see the log for details).\n\n"
                "You can install it manually instead:\n"
                f"1. Click 'Open download page' below (or browse to\n"
                f"    {HDF_MONKEY_JJJS_URL} ).\n"
                "2. Download the hdfmonkey .zip file.\n"
                f"3. Drop the downloaded .zip into this folder:\n"
                f"    {downloads_root}\n"
                "4. Click \"I've dropped the zip - try again\".")
            open_page_btn = box.addButton("Open download page", QMessageBox.ActionRole)
            open_folder_btn = box.addButton("Open downloads folder", QMessageBox.ActionRole)
            retry_btn = box.addButton("I've dropped the zip - try again", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(retry_btn)
            while True:
                box.exec()
                clicked = box.clickedButton()
                if clicked is open_page_btn:
                    try:
                        webbrowser.open(HDF_MONKEY_JJJS_URL)
                        add_main_log_window(
                            f"hdfmonkey: opened {HDF_MONKEY_JJJS_URL} in your "
                            f"browser. Save the .zip into {downloads_root}, then "
                            f"click 'try again'.")
                    except Exception as e:
                        add_main_log_window(
                            f"hdfmonkey: could not open the browser automatically "
                            f"({e}). Please browse to {HDF_MONKEY_JJJS_URL} "
                            f"manually.")
                    continue  # reshow the dialog so the user can retry afterwards
                if clicked is open_folder_btn:
                    try:
                        if sys.platform == "win32":
                            os.startfile(downloads_root)
                        elif sys.platform == "darwin":
                            subprocess.Popen(["open", downloads_root])
                        else:
                            subprocess.Popen(["xdg-open", downloads_root])
                    except Exception as e:
                        add_main_log_window(
                            f"hdfmonkey: could not open {downloads_root} ({e}).")
                    continue
                if clicked is retry_btn:
                    if _try_install_hdfmonkey_from_manual_zip():
                        return True
                    add_main_log_window(
                        f"hdfmonkey: no valid hdfmonkey .zip found in "
                        f"{downloads_root} yet. Download it from "
                        f"{HDF_MONKEY_JJJS_URL} and drop the .zip there, then try "
                        f"again.")
                    continue  # let the user place the file and retry
                return False  # Cancel / dialog closed

        def download_and_install_hdflonkey():
            """Install hdfmonkey in two explicit, individually-logged steps:
            download the jjjs archive, then extract this platform's binary. If a
            valid archive is already sitting in the downloads folder (e.g. the
            user dropped one after a previous failure) it is used directly; if the
            automatic download fails, the manual browser-download fallback is
            offered."""
            # Use an already-present (manually-dropped) archive first — this makes
            # a retry after the manual route succeed without hitting the network
            # again, and keeps the whole flow working when specnext.com blocks us.
            if _try_install_hdfmonkey_from_manual_zip():
                return True

            zip_path = _download_hdfmonkey_zip()
            if not zip_path:
                # Download blocked/failed — offer the manual browser route (drop
                # the zip into downloads, then detect it from there).
                return _prompt_manual_hdfmonkey_download()

            hdfmonkey_path = _install_hdfmonkey_from_zip(zip_path, keep_zip=False)
            if not hdfmonkey_path:
                # A file downloaded but could not be unpacked — the cleanest
                # recovery is a fresh (manual) download.
                add_main_log_window(
                    "hdfmonkey: you can install it manually from "
                    "https://github.com/gasman/hdfmonkey , or (recommended) do a "
                    "full CSpect install from the itch.io tab, which also bundles "
                    "hdfmonkey.")
                return _prompt_manual_hdfmonkey_download()

            _finish_hdfmonkey_install(hdfmonkey_path)
            return True

        def _on_hdfmonkey_button_clicked():
            """Button handler for "Download and install HDF Monkey". Shows an
            intermediary tip about the itch.io end-to-end CSpect install (which
            also bundles hdfmonkey) before running the standalone hdfmonkey
            download, so the user can choose the fuller route instead."""
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle("Install hdfmonkey")
            box.setText(
                "TIP: Did you know that if you have purchased CSpect from "
                "itch.io you can do a full end-to-end CSpect install from "
                "there?\n\n"
                "Simply log into your itch.io account in the itch.io tab, "
                "navigate to CSpect and click Install.\n\n"
                "Do you still want to install hdfmonkey only, or abort and then "
                "make an end-to-end install of CSpect using itch.io?")
            continue_btn = box.addButton(
                "Continue hdfmonkey standalone install", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(continue_btn)
            box.exec()
            if box.clickedButton() is continue_btn:
                return download_and_install_hdflonkey()
            return False

        def show_hdf_monkey_download_and_install_buttons():
            self.download_and_install_hdfmonkey_button.setVisible(True)
            self.button_new_folder.setVisible(False)
            self.button_rename.setVisible(False)
            self.button_delete_files.setVisible(False)
            # Draw the eye to the install button with a yellow 'breathing' pulse
            # while hdfmonkey is missing; it stops itself once the button is
            # hidden (i.e. hdfmonkey has been installed/detected).
            _start_hdfmonkey_button_animation()
            # hdfmonkey is confirmed missing here, so the file explorer stays
            # disabled — but MAME doesn't need hdfmonkey, so make sure its launch
            # button and option combos reflect (MAME present + a valid image)
            # rather than staying stuck disabled. The CSpect option combos don't
            # need hdfmonkey either (only Launch does, via the mounted image),
            # so refresh them too when a CSpect build is present.
            _update_mame_controls()
            _update_cspect_controls()

        def _hdfmonkey_binary_found():
            """True if the hdfmonkey executable can be located (PATH, current
            directory, the application directory, or a bundled copy discovered
            under downloads/cspect). Used to tell a genuine hdfmonkey error apart
            from "it isn't installed", without running it."""
            override = getattr(self, "_hdfmonkey_executable_path", None)
            if override and os.path.isfile(override):
                return True
            # A copy left by the standalone auto-download (downloads/hdfmonkey/
            # <platform>/) counts too, even before startup re-adopts it as the
            # active override (e.g. right after a fresh launch).
            try:
                if find_hdfmonkey_in_downloads(ZXNU_DATA_ROOT):
                    return True
            except Exception:
                pass
            if shutil.which(HDFMONKEY_EXECUTABLE):
                return True
            names = [HDFMONKEY_EXECUTABLE]
            if platform.system() == "Windows":
                names.append(HDFMONKEY_EXECUTABLE + ".exe")
            search_dirs = [os.getcwd(), ZXNU_DATA_ROOT]
            try:
                search_dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
            except Exception:
                pass
            try:
                search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
            except Exception:
                pass
            for d in search_dirs:
                for n in names:
                    if d and os.path.isfile(os.path.join(d, n)):
                        return True
            return False

        def prompt_install_hdfmonkey():
            """Offer to install hdfmonkey when it appears to be missing, on every
            platform. The jjjs auto-download ships fixed builds for Windows, Linux
            and macOS, so this runs the same "Install hdfmonkey" tip box + download
            flow as the SD-card tab button (which also points the user at the
            fuller end-to-end CSpect install via itch.io). Runs on the UI thread
            (invoked via the missing-signal so it is safe from workers)."""
            if _on_hdfmonkey_button_clicked():
                # Installed OK — allow a fresh prompt if it ever breaks again.
                self._hdfmonkey_prompt_shown = False

        # Marshals the "hdfmonkey is missing" prompt onto the UI thread: the
        # signal may be emitted from a worker thread (uploads/deletes), so the
        # dialog must not be created inline there.
        self._hdfmonkey_prompt_shown = False
        self._hdfmonkey_missing_signals = HdfMonkeyMissingSignals()
        self._hdfmonkey_missing_signals.missing.connect(prompt_install_hdfmonkey)


        # def tab_changed():
        #     # Do nothing for now has this event happens before rendering the tab
        #     # get_pyhdfmgooey_currenttab_config()

        def load_configuration_file():

            config_loaded_with_success = False

            try:

                # Load configuration dictionary
                pass

                # The cfg is written as UTF-8 (see save_configuration_file);
                # decode it explicitly instead of trusting the Windows locale
                # code page. Files written by older versions used that locale
                # encoding, so fall back to cp1252 when strict UTF-8 fails —
                # otherwise one accented character would abort the whole load.
                with open(ZX_NEXT_UNITE_CONFIG_FILE_NAME, "rb") as config_file:
                    _cfg_bytes = config_file.read()
                try:
                    _cfg_text = _cfg_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    _cfg_text = _cfg_bytes.decode("cp1252", errors="replace")
                for line in _cfg_text.splitlines():
                    if not line.strip():
                        continue
                    config_setting_name, config_setting_value = line.strip().split('=', 1)
                    configuration_dictionary[config_setting_name] = config_setting_value


                #  Now set the settings back to the application SETTING_SCREENSIZE and others

                # Restore image history into the combo (most-recent-first list
                # stored as '|'-delimited). Entries saved by older versions may
                # carry surrounding quotes / forward slashes, so tidy each one
                # (dropping any that become empty or duplicate after cleanup).
                history_raw = configuration_dictionary.get(SETTING_IMAGE_HISTORY, "")
                if history_raw:
                    history_entries = []
                    for p in history_raw.split("|"):
                        clean = normalize_sd_image_path(p)
                        if clean and clean not in history_entries:
                            history_entries.append(clean)
                    self.imageinput.blockSignals(True)
                    self.imageinput.clear()
                    for entry in history_entries[:MAX_IMAGE_HISTORY]:
                        self.imageinput.addItem(entry)
                    self.imageinput.blockSignals(False)

                # Set the active image path (most recently used), tidied the same way.
                current_hddfile = normalize_sd_image_path(configuration_dictionary[SETTING_HDDFILE])
                self.imageinput.setCurrentText(current_hddfile)
                self.cspect_sound.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_SOUND]))
                self.cspect_screensize.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_SCREENSIZE]))
                self.cspect_vsync.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_VSYNC]))
                self.cspect_joystick.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_JOYSTICK]))
                self.cspect_mouse.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MOUSE]))
                self.cspect_frequency.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_HERTZ]))
                self.cspect_esc.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_ESC]))
                # MAME option combos (aspect / sound / mouse / joystick). Stored
                # as combo indices; an absent value ("") maps to index 0 (the
                # default, i.e. "Sound On" for audio).
                if hasattr(self, "mame_aspect"):
                    self.mame_aspect.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_ASPECT]))
                    self.mame_sound.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_SOUND]))
                    self.mame_mouse.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_MOUSE]))
                    self.mame_joystick.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_JOYSTICK]))
                    self.mame_esc.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_ESC]))

                # Flatpak launch toggle + rom path (Linux-only widgets). The
                # Settings tab was built from the seeded defaults *before* this
                # load ran, so re-apply the saved values here — otherwise the
                # checkbox would always come up unchecked after a restart even
                # though the cfg has it on. An empty saved rom path falls back to
                # the per-user default. Finally re-affirm the SD Card MAME group
                # so a saved "on" reveals the Launch button with no restart.
                if not (configuration_dictionary.get(SETTING_MAME_FLATPAK_ROMPATH, "") or "").strip():
                    configuration_dictionary[SETTING_MAME_FLATPAK_ROMPATH] = default_mame_flatpak_rompath()
                if hasattr(self, "settings_mame_flatpak_checkbox"):
                    _fp_on = str(configuration_dictionary.get(
                        SETTING_MAME_FLATPAK, "")).strip().lower() in ("true", "1", "yes", "on")
                    self.settings_mame_flatpak_checkbox.blockSignals(True)
                    self.settings_mame_flatpak_checkbox.setChecked(_fp_on)
                    self.settings_mame_flatpak_checkbox.blockSignals(False)
                    self.settings_mame_flatpak_rompath_edit.setText(
                        configuration_dictionary[SETTING_MAME_FLATPAK_ROMPATH])
                    self.settings_mame_flatpak_rompath_row.setVisible(_fp_on)
                    if hasattr(self, "_refresh_mame_launch_ui"):
                        self._refresh_mame_launch_ui()

                if configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING]== "":
                    # First run (no previously saved tab): default to the
                    # AllInOne ("Unite!") tab so the user lands on the
                    # aggregated view showing the latest releases.
                    _aio_default_idx = 0
                    for _ti in range(wid_inner.tab.count()):
                        if wid_inner.tab.tabText(_ti).startswith(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
                            _aio_default_idx = _ti
                            break
                    configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING] = _aio_default_idx

                wid_inner.tab.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING]))

                if configuration_dictionary[SETTING_EXPLORERPATH] != "":
                    if not os.path.isdir(configuration_dictionary[SETTING_EXPLORERPATH]):
                        configuration_dictionary[SETTING_EXPLORERPATH] = os.path.dirname(configuration_dictionary[SETTING_EXPLORERPATH].rstrip("/\\")) + "/"


                    self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(configuration_dictionary[SETTING_EXPLORERPATH])))
                    self.left_file_explorer_selection_full_filename_path = configuration_dictionary[SETTING_EXPLORERPATH]
                    local_sync_path_box()

                if configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] != "":
                    if not os.path.isdir(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH]):
                        configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = os.path.dirname(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH].rstrip("/\\")) + "/"


                    self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH])))
                    self.left_file_nextsync_explorer_selection_full_filename_path = configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH]
                    self.nextsync_file_explorer_path.setText(self.left_file_nextsync_explorer_selection_full_filename_path)

                # Restored (or empty) sync root: sync the "Set current folder as
                # new sync root folder" button visibility with it.
                _nextsync_update_set_syncroot_button()

                # Select the "Sync mode" radio from the two legacy booleans.
                # "Sync once" wins if a legacy config somehow had both set; with
                # neither set we fall back to the incremental (default) mode.
                _sync_once_pref = configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] in ("1",) or \
                    configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE].lower() == "true"
                _always_pref = configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] in ("1",) or \
                    configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC].lower() == "true"
                if _sync_once_pref:
                    self.nextsync_synconce_checkbox.setChecked(True)
                elif _always_pref:
                    self.nextsync_alwayssync_checkbox.setChecked(True)
                else:
                    self.nextsync_syncincremental_radio.setChecked(True)

                if configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] != "":
                    if configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] == "1" or configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER].lower() == "true":
                        self.nextsync_slowtransfer_checkbox.setChecked(True)
                    else:
                        self.nextsync_slowtransfer_checkbox.setChecked(False)

                if SETTING_WARN_IMAGE_NEARLY_FULL in configuration_dictionary and configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL] != "":
                    checked = configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL] != "0" and configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL].lower() != "false"
                    self.settings_warn_image_nearly_full_checkbox.setChecked(checked)

                if SETTING_NO_PROMPT_ON_DELETION in configuration_dictionary and configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION] != "":
                    checked = configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION] != "0" and configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION].lower() != "false"
                    self.settings_no_prompt_on_deletion_checkbox.setChecked(checked)

                # Recycle Bin deletes toggle (default on). Only restored when
                # Send2Trash is available — without it the checkbox stays
                # disabled+unchecked and local deletes remain permanent.
                if send2trash_available():
                    _recycle = configuration_dictionary.get(
                        SETTING_DELETE_TO_RECYCLE_BIN, "").strip().lower()
                    _recycle_on = _recycle not in ("false", "0", "no")  # default on
                    self.settings_delete_to_recycle_bin_checkbox.blockSignals(True)
                    self.settings_delete_to_recycle_bin_checkbox.setChecked(_recycle_on)
                    self.settings_delete_to_recycle_bin_checkbox.blockSignals(False)

                # NextSync receive-conflict policy (combo): restore the saved value,
                # falling back to the default for empty/unknown entries.
                _send_conflict = configuration_dictionary.get(SETTING_NEXTSYNC_SEND_CONFLICT, "").strip().lower()
                if _send_conflict not in ("prompt", "overwrite", "ignore"):
                    _send_conflict = DEFAULT_NEXTSYNC_SEND_CONFLICT
                configuration_dictionary[SETTING_NEXTSYNC_SEND_CONFLICT] = _send_conflict
                _sc_idx = self.settings_nextsync_send_conflict_combo.findData(_send_conflict)
                self.settings_nextsync_send_conflict_combo.blockSignals(True)
                self.settings_nextsync_send_conflict_combo.setCurrentIndex(max(0, _sc_idx))
                self.settings_nextsync_send_conflict_combo.blockSignals(False)

                if SETTING_AVAIL_CHECK in configuration_dictionary and configuration_dictionary[SETTING_AVAIL_CHECK] != "":
                    checked = configuration_dictionary[SETTING_AVAIL_CHECK] != "0" and configuration_dictionary[SETTING_AVAIL_CHECK].lower() != "false"
                else:
                    checked = True
                self.settings_avail_check_checkbox.setChecked(checked)

                # Multi-search defaults to True; only turn off when explicitly saved as false/0
                if SETTING_MULTI_SEARCH in configuration_dictionary and configuration_dictionary[SETTING_MULTI_SEARCH] != "":
                    checked = configuration_dictionary[SETTING_MULTI_SEARCH] != "0" and configuration_dictionary[SETTING_MULTI_SEARCH].lower() != "false"
                    self.settings_multi_search_checkbox.setChecked(checked)

                # Search autocomplete defaults to True; only turn off when explicitly saved as false/0
                if SETTING_SEARCH_AUTOCOMPLETE in configuration_dictionary and configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE] != "":
                    checked = configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE] != "0" and configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE].lower() != "false"
                    self.settings_search_autocomplete_checkbox.setChecked(checked)

                # Crash-log generation defaults to False; only turn on when explicitly saved as true/1.
                if SETTING_CRASH_LOG_ENABLED in configuration_dictionary and configuration_dictionary[SETTING_CRASH_LOG_ENABLED] != "":
                    _crash_checked = configuration_dictionary[SETTING_CRASH_LOG_ENABLED] in ("1", "true", "True", "yes", "on")
                else:
                    _crash_checked = False
                self.settings_crash_log_enabled_checkbox.blockSignals(True)
                self.settings_crash_log_enabled_checkbox.setChecked(_crash_checked)
                self.settings_crash_log_enabled_checkbox.blockSignals(False)

                # Disable no-emulator toast defaults to False.
                if SETTING_DISABLE_NO_EMULATOR_TOAST in configuration_dictionary and configuration_dictionary[SETTING_DISABLE_NO_EMULATOR_TOAST] != "":
                    _no_toast = configuration_dictionary[SETTING_DISABLE_NO_EMULATOR_TOAST].lower() in ("true", "1", "yes", "on")
                else:
                    _no_toast = False
                self.settings_disable_no_emulator_toast_checkbox.setChecked(_no_toast)

                # NextSync HTTP bridge defaults to False; when enabled it also
                # auto-starts the web server with the app. The start is
                # deferred to the event loop so the whole window (log pane,
                # toasts, the bridge closures) exists by then.
                if SETTING_NEXTSYNC_HTTP_BRIDGE in configuration_dictionary and configuration_dictionary[SETTING_NEXTSYNC_HTTP_BRIDGE] != "":
                    _http_bridge_on = configuration_dictionary[SETTING_NEXTSYNC_HTTP_BRIDGE].lower() in ("true", "1", "yes", "on")
                else:
                    _http_bridge_on = False
                self.settings_http_bridge_checkbox.blockSignals(True)
                self.settings_http_bridge_checkbox.setChecked(_http_bridge_on)
                self.settings_http_bridge_checkbox.blockSignals(False)
                if _http_bridge_on and flask_available():
                    QTimer.singleShot(0, self._nextsync_http_bridge_start)
                elif _http_bridge_on:
                    # Enabled in the config but Flask is gone (uninstalled /
                    # different environment): keep the choice, skip the start,
                    # say why — never an error at startup.
                    add_nextsync_log_window(
                        "NextSync HTTP bridge is enabled in the settings but "
                        "the 'flask' package is not installed - install it "
                        "with: python -m pip install flask")

                # HTTP bridge port (defaults to 80). The deferred bridge start
                # above reads the port straight from the configuration
                # dictionary, so only the widget needs syncing here.
                try:
                    _http_port = int(configuration_dictionary.get(
                        SETTING_NEXTSYNC_HTTP_PORT) or 80)
                except (TypeError, ValueError):
                    _http_port = 80
                if not (1 <= _http_port <= 65535):
                    _http_port = 80
                self.settings_http_port_spinbox.blockSignals(True)
                self.settings_http_port_spinbox.setValue(_http_port)
                self.settings_http_port_spinbox.blockSignals(False)

                # HTTP bridge concurrent-connection limit (defaults to 1 —
                # the recommended value, matching the serial -listen session).
                try:
                    _http_conn = int(configuration_dictionary.get(
                        SETTING_NEXTSYNC_HTTP_CONNECTION_LIMIT) or 1)
                except (TypeError, ValueError):
                    _http_conn = 1
                if _http_conn < 1:
                    _http_conn = 1
                self.settings_http_conn_spinbox.blockSignals(True)
                self.settings_http_conn_spinbox.setValue(_http_conn)
                self.settings_http_conn_spinbox.blockSignals(False)

                # HTTP bridge bearer-token protection (checkbox + persisted
                # token). The deferred bridge start above reads both straight
                # from the config dict, so only the widgets need syncing.
                _http_token_on = configuration_dictionary.get(
                    SETTING_NEXTSYNC_HTTP_TOKEN_ENABLED, "").strip().lower() in (
                        "true", "1", "yes", "on")
                self.settings_http_token_checkbox.blockSignals(True)
                self.settings_http_token_checkbox.setChecked(_http_token_on)
                self.settings_http_token_checkbox.blockSignals(False)
                self.settings_http_token_edit.blockSignals(True)
                self.settings_http_token_edit.setText(
                    (configuration_dictionary.get(
                        SETTING_NEXTSYNC_HTTP_TOKEN) or "").strip())
                self.settings_http_token_edit.blockSignals(False)

                # This single call now also refreshes the token widgets' enabled
                # state from the checkbox value just restored above.
                self._http_port_widgets_set_enabled(_http_bridge_on)

                # MAME ROM/system choice (combo) and command-line parameters
                # (editable text). Both only exist as widgets when MAME was
                # detected at startup, so guard with hasattr.
                if hasattr(self, "settings_mame_rom_combo"):
                    _rom = configuration_dictionary.get(SETTING_MAME_ROM_CHOICE, "").strip()
                    if not _rom:
                        _rom = MAME_ROM_CHOICE[0]
                    self.settings_mame_rom_combo.blockSignals(True)
                    _idx = self.settings_mame_rom_combo.findText(_rom)
                    if _idx < 0:
                        # Persisted ROM not in the predefined list: add it so the
                        # user's saved choice is preserved and selectable.
                        self.settings_mame_rom_combo.addItem(_rom)
                        _idx = self.settings_mame_rom_combo.findText(_rom)
                    self.settings_mame_rom_combo.setCurrentIndex(max(0, _idx))
                    self.settings_mame_rom_combo.blockSignals(False)
                    configuration_dictionary[SETTING_MAME_ROM_CHOICE] = _rom

                if hasattr(self, "settings_mame_params_edit"):
                    _params = configuration_dictionary.get(
                        SETTING_MAME_COMMAND_LINE_PARAMETERS, "")
                    if not _params:
                        _params = MAME_DEFAULT_COMMAND_LINE
                    # Migrate a legacy default (the one that hard-coded
                    # "-aspect 2:1", or the one that hard-coded "-confirm_quit")
                    # to the new default now that both are combo boxes, so the
                    # editable command-line box no longer shows a stale, now
                    # combo-controlled option. (Launch-time stripping still handles
                    # any other custom occurrences — see launch_mame.)
                    elif _params.strip() in MAME_DEFAULT_COMMAND_LINE_LEGACY_ALL:
                        _params = MAME_DEFAULT_COMMAND_LINE
                    self.settings_mame_params_edit.blockSignals(True)
                    self.settings_mame_params_edit.setText(_params)
                    self.settings_mame_params_edit.blockSignals(False)
                    configuration_dictionary[SETTING_MAME_COMMAND_LINE_PARAMETERS] = _params

                # MAME "check for a newer version at startup" toggle (default on).
                # Only exists as a widget when MAME was detected at startup.
                if hasattr(self, "settings_mame_update_check_checkbox"):
                    _mame_upd = configuration_dictionary.get(
                        SETTING_MAME_UPDATE_CHECK, "").strip().lower()
                    _mame_upd_on = _mame_upd not in ("false", "0", "no")  # default on
                    self.settings_mame_update_check_checkbox.blockSignals(True)
                    self.settings_mame_update_check_checkbox.setChecked(_mame_upd_on)
                    self.settings_mame_update_check_checkbox.blockSignals(False)

                # ZX Next Unite "check for updates at startup on Github" toggle
                # (default on). Always present as a widget.
                if hasattr(self, "settings_zxnu_update_check_checkbox"):
                    _zxnu_upd = configuration_dictionary.get(
                        SETTING_ZXNU_UPDATE_CHECK, "").strip().lower()
                    _zxnu_upd_on = _zxnu_upd not in ("false", "0", "no")  # default on
                    self.settings_zxnu_update_check_checkbox.blockSignals(True)
                    self.settings_zxnu_update_check_checkbox.setChecked(_zxnu_upd_on)
                    self.settings_zxnu_update_check_checkbox.blockSignals(False)

                # CSpect "check for a newer version on itch.io at startup" toggle
                # (default on). Always present as a widget (unlike MAME's, which
                # is gated on detection).
                if hasattr(self, "settings_cspect_update_check_checkbox"):
                    _cspect_upd = configuration_dictionary.get(
                        SETTING_CSPECT_UPDATE_CHECK, "").strip().lower()
                    _cspect_upd_on = _cspect_upd not in ("false", "0", "no")  # default on
                    self.settings_cspect_update_check_checkbox.blockSignals(True)
                    self.settings_cspect_update_check_checkbox.setChecked(_cspect_upd_on)
                    self.settings_cspect_update_check_checkbox.blockSignals(False)

                # CSpect default launch parameters (editable text). Empty falls
                # back to the built-in default, mirroring the MAME params handling.
                if hasattr(self, "settings_cspect_params_edit"):
                    _cspect_params = configuration_dictionary.get(SETTING_CUSTOM, "")
                    if not _cspect_params:
                        _cspect_params = CSPECT_DEFAULT_LAUNCH_PARAMETERS
                    # Migrate an older cfg that stored only *additional* params here
                    # (the base "-basickeys -zxnext" used to be applied separately):
                    # prepend the base so those users keep it now that this field
                    # holds the full default command line.
                    elif "-zxnext" not in _cspect_params:
                        _cspect_params = (CSPECT_DEFAULT_LAUNCH_PARAMETERS
                                          + " " + _cspect_params)
                    self.settings_cspect_params_edit.blockSignals(True)
                    self.settings_cspect_params_edit.setText(_cspect_params)
                    self.settings_cspect_params_edit.blockSignals(False)
                    configuration_dictionary[SETTING_CUSTOM] = _cspect_params
                # Ensure runtime state matches the persisted setting (the
                # early-bootstrap read already honoured this, but reapply here
                # so any cfg edits made between launches take immediate effect).
                try:
                    _zxnu_set_crash_log_enabled(_crash_checked)
                except Exception:
                    pass

                # Gallery animation mode: "hover" (default), "timer" or "none"
                if SETTING_GALLERY_ANIM_MODE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_ANIM_MODE] != "":
                    val = configuration_dictionary[SETTING_GALLERY_ANIM_MODE].strip().lower()
                    if val in ("hover", "timer", "none"):
                        self._gallery_anim_mode = val
                        cb = getattr(self, "settings_gallery_anim_combo", None)
                        if cb is not None:
                            for _i in range(cb.count()):
                                if cb.itemData(_i) == val:
                                    cb.setCurrentIndex(_i)
                                    break

                # Search sort rendering preference: "getit_first" (default),
                # "mixed" or "classic"
                if SETTING_SEARCH_SORT_MODE in configuration_dictionary and configuration_dictionary[SETTING_SEARCH_SORT_MODE] != "":
                    _ssm = configuration_dictionary[SETTING_SEARCH_SORT_MODE].strip().lower()
                    if _ssm in (SEARCH_SORT_GETIT_FIRST, SEARCH_SORT_MIXED, SEARCH_SORT_CLASSIC):
                        self._search_sort_mode = _ssm
                        _sscb = getattr(self, "settings_search_sort_combo", None)
                        if _sscb is not None:
                            for _i in range(_sscb.count()):
                                if _sscb.itemData(_i) == _ssm:
                                    _sscb.setCurrentIndex(_i)
                                    break

                # Gallery rows per page: int 1..10
                if SETTING_GALLERY_ROWS_PER_PAGE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_ROWS_PER_PAGE] != "":
                    try:
                        n = int(configuration_dictionary[SETTING_GALLERY_ROWS_PER_PAGE])
                    except (TypeError, ValueError):
                        n = DEFAULT_GALLERY_ROWS_PER_PAGE
                    n = max(GALLERY_MIN_ROWS, min(GALLERY_MAX_ROWS, n))
                    self._gallery_rows_per_page = n
                    sp = getattr(self, "settings_gallery_rows_spin", None)
                    if sp is not None:
                        sp.setValue(n)

                # Gallery items per row: 2 | 4 | 8
                if SETTING_GALLERY_COLS in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_COLS] != "":
                    try:
                        _gcols = int(configuration_dictionary[SETTING_GALLERY_COLS])
                    except (TypeError, ValueError):
                        _gcols = DEFAULT_GALLERY_COLS
                    if _gcols in (2, 4, 8):
                        self._gallery_cols = _gcols
                        _gcb = getattr(self, "settings_gallery_cols_combo", None)
                        if _gcb is not None:
                            for _i in range(_gcb.count()):
                                if _gcb.itemData(_i) == _gcols:
                                    _gcb.setCurrentIndex(_i)
                                    break

                # Gallery image size: "small" | "medium" | "large"
                if SETTING_GALLERY_IMG_SIZE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_IMG_SIZE] != "":
                    _gsz = configuration_dictionary[SETTING_GALLERY_IMG_SIZE].strip().lower()
                    if _gsz in ("small", "medium", "large"):
                        self._gallery_img_size = _gsz
                        _gscb = getattr(self, "settings_gallery_img_size_combo", None)
                        if _gscb is not None:
                            for _i in range(_gscb.count()):
                                if _gscb.itemData(_i) == _gsz:
                                    _gscb.setCurrentIndex(_i)
                                    break

                # Gallery slideshow pause time (seconds): 5 (default)|10|15|30|60
                if SETTING_GALLERY_SLIDESHOW_SECS in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_SLIDESHOW_SECS] != "":
                    try:
                        _gss = int(configuration_dictionary[SETTING_GALLERY_SLIDESHOW_SECS])
                    except (TypeError, ValueError):
                        _gss = DEFAULT_GALLERY_SLIDESHOW_SECS
                    if _gss in GALLERY_SLIDESHOW_SECS_CHOICES:
                        self._gallery_slideshow_secs = _gss
                        set_gallery_slideshow_secs(_gss)
                        # Refresh the persistent detail-slideshow timers so a
                        # loaded value applies without reopening an item.
                        for _tn in ("_zxdb_slideshow_timer", "_zxart_slideshow_timer"):
                            _t = getattr(self, _tn, None)
                            if _t is not None:
                                _t.setInterval(gallery_slideshow_interval_ms())
                        _sscb = getattr(self, "settings_gallery_slideshow_combo", None)
                        if _sscb is not None:
                            for _i in range(_sscb.count()):
                                if _sscb.itemData(_i) == _gss:
                                    _sscb.setCurrentIndex(_i)
                                    break

                # Retro log (SD Card + NextSync) font size, in points.
                if SETTING_RETRO_LOG_FONT_SIZE in configuration_dictionary and configuration_dictionary[SETTING_RETRO_LOG_FONT_SIZE] != "":
                    try:
                        _rlf = int(configuration_dictionary[SETTING_RETRO_LOG_FONT_SIZE])
                    except (TypeError, ValueError):
                        _rlf = DEFAULT_RETRO_LOG_FONT_SIZE
                    if _rlf in RETRO_LOG_FONT_SIZE_CHOICES:
                        self._retro_log_font_size = _rlf
                        _rlcb = getattr(self, "settings_retro_log_font_combo", None)
                        if _rlcb is not None:
                            _rlcb.blockSignals(True)
                            for _i in range(_rlcb.count()):
                                if _rlcb.itemData(_i) == _rlf:
                                    _rlcb.setCurrentIndex(_i)
                                    break
                            _rlcb.blockSignals(False)
                        # Apply to any retro log widgets already built.
                        if hasattr(self, "_apply_retro_log_font_size"):
                            self._apply_retro_log_font_size(_rlf)

                # Per-pane view mode: "table" (default) or "gallery"
                for _pane_key, _attr in (
                    (SETTING_GETIT_VIEW_MODE, "_getit_view_mode"),
                    (SETTING_ZXDB_VIEW_MODE,  "_zxdb_view_mode"),
                    (SETTING_ZXART_VIEW_MODE, "_zxart_view_mode"),
                    (SETTING_FAVORITES_VIEW_MODE, "_favorites_view_mode"),
                    (SETTING_ALLINONE_VIEW_MODE, "_allinone_view_mode"),
                    (SETTING_ITCHIO_VIEW_MODE, "_itchio_view_mode"),
                ):
                    if _pane_key in configuration_dictionary and configuration_dictionary[_pane_key] != "":
                        val = configuration_dictionary[_pane_key].strip().lower()
                        if val in ("table", "gallery"):
                            setattr(self, _attr, val)

                # Space-Invaders background animation preference (default on).
                # Applied before the pygame-mode restore below so the widget is
                # built with the right setting.
                _allinone_anim_pref = configuration_dictionary.get(
                    SETTING_ALLINONE_PYGAME_ANIM, "").strip().lower()
                _allinone_anim_on = _allinone_anim_pref not in ("false", "0", "no")
                self._allinone_pygame_anim = _allinone_anim_on
                _anim_cb = getattr(self, "settings_pygame_anim_checkbox", None)
                if _anim_cb is not None:
                    _anim_cb.blockSignals(True)
                    _anim_cb.setChecked(_allinone_anim_on)
                    _anim_cb.blockSignals(False)

                # Restore the Unite! pygame visualization mode if it was on last
                # session. Routed through the toggle button so the lazy import /
                # graceful-fallback path is reused; guarded so a transient
                # "pygame unavailable" doesn't overwrite the saved preference.
                _allinone_pg_pref = configuration_dictionary.get(
                    SETTING_ALLINONE_PYGAME_MODE, "").strip().lower()
                if _allinone_pg_pref in ("true", "1", "yes") and \
                        hasattr(self, "allinone_pygame_button") and \
                        not self.allinone_pygame_button.isChecked():
                    self._allinone_pygame_restoring = True
                    try:
                        self.allinone_pygame_button.setChecked(True)
                    finally:
                        self._allinone_pygame_restoring = False

                # NextSync retro-log starfield animation preference (default on);
                # applied before the mode restore below so the widget is built
                # with the right setting.
                _nextsync_anim_pref = configuration_dictionary.get(
                    SETTING_NEXTSYNC_PYGAME_ANIM, "").strip().lower()
                _nextsync_anim_on = _nextsync_anim_pref not in ("false", "0", "no")
                self._nextsync_pygame_anim = _nextsync_anim_on
                _ns_anim_cb = getattr(self, "settings_nextsync_pygame_anim_checkbox", None)
                if _ns_anim_cb is not None:
                    _ns_anim_cb.blockSignals(True)
                    _ns_anim_cb.setChecked(_nextsync_anim_on)
                    _ns_anim_cb.blockSignals(False)

                # Restore the NextSync retro 8-bit log mode the same way (routed
                # through the toggle so the lazy-import / fallback path is reused).
                _nextsync_pg_pref = configuration_dictionary.get(
                    SETTING_NEXTSYNC_PYGAME_MODE, "").strip().lower()
                if _nextsync_pg_pref in ("true", "1", "yes") and \
                        hasattr(self, "nextsync_pygame_button") and \
                        not self.nextsync_pygame_button.isChecked():
                    self._nextsync_pygame_restoring = True
                    try:
                        self.nextsync_pygame_button.setChecked(True)
                    finally:
                        self._nextsync_pygame_restoring = False

                # Restore the Remote Explorer view if it was open last session by
                # selecting its tab (index 0), which drives all the show/hide +
                # widget-build side effects (the listen server itself is NOT
                # auto-started).
                _re_open_pref = configuration_dictionary.get(
                    SETTING_NEXTSYNC_REMOTE_EXPLORER, "").strip().lower()
                if _re_open_pref in ("true", "1", "yes") and \
                        hasattr(self, "nextsync_mode_tabs") and \
                        self.nextsync_mode_tabs.currentIndex() != 0:
                    self._re_open_restoring = True
                    try:
                        self.nextsync_mode_tabs.setCurrentIndex(0)
                    finally:
                        self._re_open_restoring = False

                # -start-remote-explorer-listener: the command-line switch asks
                # for the '.sync5 -listen' server to be running from startup.
                # Force the Remote Explorer view open (without persisting it —
                # this run only, hence the _re_open_restoring guard) and start
                # the server once the event loop is up, so the toasts, log
                # pane and server closures it talks to all exist by then.
                if _ZXNU_START_RE_LISTENER and hasattr(self, "nextsync_mode_tabs"):
                    if self.nextsync_mode_tabs.currentIndex() != 0:
                        self._re_open_restoring = True
                        try:
                            self.nextsync_mode_tabs.setCurrentIndex(0)
                        finally:
                            self._re_open_restoring = False

                    def _autostart_re_listener():
                        if not getattr(self, "_re_running", False):
                            self._nextsync_re_toggle_server()
                    QTimer.singleShot(0, _autostart_re_listener)

                # Restore the saved splitter positions (SD Card explorers ⇄
                # log, GetIt results ⇄ MOTD). The window is not shown yet, so
                # QSplitter re-applies the sizes on first layout; the stretch
                # factors (top pane 1, bottom pane 0) absorb any difference
                # between the saved and actual window height, keeping the
                # bottom pane at its saved height.
                for _split_key, _split_attr in (
                    (SETTING_SDCARD_SPLITTER, "sdcard_splitter"),
                    (SETTING_GETIT_SPLITTER,  "getit_splitter"),
                ):
                    _split_pref = str(configuration_dictionary.get(
                        _split_key, "")).strip()
                    _split_widget = getattr(self, _split_attr, None)
                    if _split_pref and _split_widget is not None:
                        try:
                            _top, _bottom = (int(_v) for _v in _split_pref.split(",")[:2])
                            if _top > 0 and _bottom > 0:
                                _split_widget.setSizes([_top, _bottom])
                        except (TypeError, ValueError):
                            pass

                # Restore the SD Card retro 8-bit log mode the same way.
                _sdcard_pg_pref = configuration_dictionary.get(
                    SETTING_SDCARD_PYGAME_LOG, "").strip().lower()
                if _sdcard_pg_pref in ("true", "1", "yes") and \
                        hasattr(self, "main_pygame_button") and \
                        not self.main_pygame_button.isChecked():
                    self._main_pygame_restoring = True
                    try:
                        self.main_pygame_button.setChecked(True)
                    finally:
                        self._main_pygame_restoring = False

                # Restore the Help ("?") retro 8-bit console mode the same way.
                _help_pg_pref = configuration_dictionary.get(
                    SETTING_HELP_PYGAME_LOG, "").strip().lower()
                if _help_pg_pref in ("true", "1", "yes") and \
                        hasattr(self, "help_pygame_button") and \
                        not self.help_pygame_button.isChecked():
                    self._help_pygame_restoring = True
                    try:
                        self.help_pygame_button.setChecked(True)
                    finally:
                        self._help_pygame_restoring = False

                # Restore each pane's Classic/Retro item-viewer choice. Routed
                # through the toggle button so the pygame-availability check and
                # label update are reused; persisting during restore is a no-op
                # (save_configuration_file is guarded while _initialising).
                self._retro_restoring = True
                try:
                    for _retro_key, _retro_btn_attr in (
                        (SETTING_GETIT_ITEM_RETRO,     "getit_retro_button"),
                        (SETTING_ZXDB_ITEM_RETRO,      "zxdb_retro_button"),
                        (SETTING_ZXART_ITEM_RETRO,     "zxart_retro_button"),
                        (SETTING_ITCHIO_ITEM_RETRO,    "itchio_retro_button"),
                        (SETTING_FAVORITES_ITEM_RETRO, "favorites_retro_button"),
                    ):
                        _retro_pref = configuration_dictionary.get(
                            _retro_key, "").strip().lower()
                        _retro_btn = getattr(self, _retro_btn_attr, None)
                        if (_retro_pref in ("true", "1", "yes") and _retro_btn is not None
                                and not _retro_btn.isChecked()):
                            _retro_btn.setChecked(True)
                finally:
                    self._retro_restoring = False

                # itch.io tab: prefill the saved API key and apply the saved
                # show/hide preference (the tab is built visible by default).
                try:
                    _key_field = getattr(self, "itchio_key_input", None)
                    if _key_field is not None:
                        _key_field.setText(
                            configuration_dictionary.get(SETTING_ITCHIO_API_KEY, "") or "")
                    _itch_show_pref = configuration_dictionary.get(
                        SETTING_SHOW_ITCHIO_TAB, "").strip().lower()
                    _itch_show = _itch_show_pref not in ("false", "0", "no")  # default on
                    _itch_cb = getattr(self, "settings_show_itchio_tab_checkbox", None)
                    if _itch_cb is not None and _itch_cb.isEnabled():
                        _itch_cb.blockSignals(True)
                        _itch_cb.setChecked(_itch_show)
                        _itch_cb.blockSignals(False)
                    _itch_fn = getattr(self, "_itchio_tab_set_visible", None)
                    if _itch_fn is not None:
                        _itch_fn(_itch_show)
                    # Auto-connect once at startup when the tab is enabled and a
                    # key was saved, so collections are ready without the user
                    # having to click Connect.
                    _itch_load = getattr(self, "_itchio_load_collections", None)
                    _itch_key = (configuration_dictionary.get(
                        SETTING_ITCHIO_API_KEY, "") or "").strip()
                    if (_itch_show and _itch_key and _itch_load is not None
                            and not getattr(self, "_itchio_autoconnected", False)):
                        self._itchio_autoconnected = True
                        QTimer.singleShot(0, _itch_load)
                except Exception:
                    pass

                # Alien Floyd's (pygame-ce) optional background + dedicated tab
                # (both default off). Disable the controls when pygame-ce is not
                # installed, but leave the saved preferences untouched.
                try:
                    # Seed the persisted arcade high-score table and wire the
                    # saver.  The table (top 5, NAME:SCORE pairs) is the single
                    # source of truth; the player adds an entry by spelling
                    # their name (shooting letters) when a run makes the list.
                    try:
                        import zxnu_pygame as _zpg_hs

                        def _save_alien_table(serialized):
                            configuration_dictionary[SETTING_ALIEN_FLOYD_HISCORES] = str(serialized)
                            try:
                                save_configuration_file()
                            except Exception:
                                pass
                        _zpg_hs.set_alien_table_save_cb(_save_alien_table)
                        _zpg_hs.init_alien_table(
                            configuration_dictionary.get(
                                SETTING_ALIEN_FLOYD_HISCORES, ""))
                    except Exception:
                        pass
                    _af_bg_on = configuration_dictionary.get(
                        SETTING_ALIEN_FLOYD_BG, "").strip().lower() in ("true", "1", "yes")
                    _af_tab_on = configuration_dictionary.get(
                        SETTING_ALIEN_FLOYD_TAB, "").strip().lower() in ("true", "1", "yes")
                    _af_ok = False
                    try:
                        from zxnu_pygame import pygame_available as _pg_avail
                        _af_ok = bool(_pg_avail()[0])
                    except Exception:
                        _af_ok = False
                    _af_bg_cb = getattr(self, "settings_alien_floyd_bg_checkbox", None)
                    _af_tab_cb = getattr(self, "settings_alien_floyd_tab_checkbox", None)
                    if not _af_ok:
                        for _cb in (_af_bg_cb, _af_tab_cb):
                            if _cb is not None:
                                _cb.setEnabled(False)
                                _cb.setToolTip(
                                    "Requires the optional 'pygame-ce' package.\n"
                                    + zxnu_optional_install_hint("pygame-ce"))
                    else:
                        if _af_bg_cb is not None:
                            _af_bg_cb.blockSignals(True)
                            _af_bg_cb.setChecked(_af_bg_on)
                            _af_bg_cb.blockSignals(False)
                        if _af_tab_cb is not None:
                            _af_tab_cb.blockSignals(True)
                            _af_tab_cb.setChecked(_af_tab_on)
                            _af_tab_cb.blockSignals(False)
                        if _af_bg_on and hasattr(self, "_apply_alien_floyd_bg"):
                            self._apply_alien_floyd_bg(True)
                        if _af_tab_on and hasattr(self, "_alien_floyd_tab_set_visible"):
                            self._alien_floyd_tab_set_visible(True)
                except Exception:
                    pass

                # zxART API language (eng/pol/spa)
                _zxart_lang_cfg = configuration_dictionary.get(SETTING_ZXART_LANGUAGE, "").strip().lower()
                if _zxart_lang_cfg in ("eng", "pol", "spa"):
                    _zxart_set_language(_zxart_lang_cfg)
                if hasattr(self, "zxart_language_combo"):
                    cb = self.zxart_language_combo
                    code = _zxart_lang()
                    for _i in range(cb.count()):
                        if cb.itemData(_i) == code:
                            cb.blockSignals(True)
                            cb.setCurrentIndex(_i)
                            cb.blockSignals(False)
                            break

                saved_mode = configuration_dictionary.get(SETTING_ZXDB_LAST_MODE, "").strip()
                if saved_mode:
                    for _i in range(self.zxdb_mode_combo.count()):
                        if self.zxdb_mode_combo.itemData(_i) == saved_mode:
                            self.zxdb_mode_combo.setCurrentIndex(_i)
                            break

                def _load_color_setting(setting_key, default_hex, color_attr, btn_attr):
                    hex_val = configuration_dictionary.get(setting_key, "").strip()
                    color = hex_to_qcolor(hex_val) if hex_val else hex_to_qcolor(default_hex)
                    setattr(self, color_attr, color)
                    btn = getattr(self, btn_attr)
                    btn.setStyleSheet(f"background-color: {qcolor_to_hex(color)}; border: 1px solid #888;")

                _load_color_setting(SETTING_COLOR_UP_DIRECTORY, DEFAULT_COLOR_UP_DIRECTORY, "img_color_up_directory", "settings_btn_color_up_directory")
                _load_color_setting(SETTING_COLOR_DIR_NAME,     DEFAULT_COLOR_DIR_NAME,     "img_color_dir_name",     "settings_btn_color_dir_name")
                _load_color_setting(SETTING_COLOR_DIR_TYPE,     DEFAULT_COLOR_DIR_TYPE,     "img_color_dir_type",     "settings_btn_color_dir_type")
                _load_color_setting(SETTING_COLOR_FILE_NAME,    DEFAULT_COLOR_FILE_NAME,    "img_color_file_name",    "settings_btn_color_file_name")
                _load_color_setting(SETTING_COLOR_FILE_EXT,     DEFAULT_COLOR_FILE_EXT,     "img_color_file_ext",     "settings_btn_color_file_ext")
                _load_color_setting(SETTING_COLOR_FILE_SIZE,    DEFAULT_COLOR_FILE_SIZE,    "img_color_file_size",    "settings_btn_color_file_size")
                _load_color_setting(SETTING_COLOR_GENERAL_TEXT, DEFAULT_COLOR_GENERAL_TEXT, "img_color_general_text", "settings_btn_color_general_text")
                _load_color_setting(SETTING_COLOR_RETRO_LOG,    DEFAULT_COLOR_RETRO_LOG,    "img_color_retro_log",    "settings_btn_color_retro_log")
                # Push the restored retro-log color to any already-built retro
                # consoles (normally they are built later, lazily, and seed
                # themselves from img_color_retro_log at construction).
                if hasattr(self, "_apply_retro_log_color"):
                    self._apply_retro_log_color()

                # Desktop theme mode. Automatic re-detects the OS light/dark
                # theme at every startup and Dark re-applies the dark tweaks
                # (both overriding the per-colour values just loaded); Custom
                # keeps the hand-picked colours above.
                _theme_cfg = configuration_dictionary.get(SETTING_DESKTOP_THEME, "").strip().lower()
                if _theme_cfg in (DESKTOP_THEME_AUTOMATIC, DESKTOP_THEME_WHITE,
                                  DESKTOP_THEME_DARK, DESKTOP_THEME_BLACK,
                                  DESKTOP_THEME_CUSTOM):
                    self._desktop_theme_mode = _theme_cfg
                if hasattr(self, "_select_desktop_theme_in_combo"):
                    self._select_desktop_theme_in_combo(self._desktop_theme_mode)
                if hasattr(self, "_apply_desktop_theme_colors"):
                    self._apply_desktop_theme_colors(persist=False)

                # Background opacity
                _bg_opacity_raw = configuration_dictionary.get(SETTING_BG_OPACITY, "").strip()
                _bg_opacity_val = BackgroundWidget.DEFAULT_OPACITY
                if _bg_opacity_raw:
                    try:
                        _bg_opacity_val = max(0, min(100, int(_bg_opacity_raw)))
                    except (TypeError, ValueError):
                        pass
                self.settings_bg_opacity_slider.blockSignals(True)
                self.settings_bg_opacity_spinbox.blockSignals(True)
                self.settings_bg_opacity_slider.setValue(_bg_opacity_val)
                self.settings_bg_opacity_spinbox.setValue(_bg_opacity_val)
                self.settings_bg_opacity_slider.blockSignals(False)
                self.settings_bg_opacity_spinbox.blockSignals(False)
                self._bg_widget.set_bg_opacity(_bg_opacity_val)
                _pane_alpha = max(0, min(255, int(255 - (_bg_opacity_val / 100.0) * 255)))
                self._tab_widget.setStyleSheet(self._build_tab_stylesheet(_pane_alpha))

                # Background image selection
                _bg_image_raw = configuration_dictionary.get(SETTING_BG_IMAGE, "").strip()
                if _bg_image_raw:
                    # Resource paths are stored with a :/ prefix; filesystem paths
                    # are stored as basenames relative to the script directory.
                    if _bg_image_raw.startswith(":/"):
                        _bg_full_load = _bg_image_raw
                        _path_valid = not QPixmap(_bg_full_load).isNull()
                    else:
                        _bg_dir_load = ZXNU_DATA_ROOT
                        _bg_full_load = os.path.join(_bg_dir_load, _bg_image_raw)
                        _path_valid = os.path.isfile(_bg_full_load)
                    if _path_valid:
                        # Find matching combo entry
                        _cb = getattr(self, "settings_bg_image_combo", None)
                        if _cb is not None:
                            for _ci in range(_cb.count()):
                                if _cb.itemData(_ci) == _bg_full_load:
                                    _cb.blockSignals(True)
                                    _cb.setCurrentIndex(_ci)
                                    _cb.blockSignals(False)
                                    break
                        self._bg_widget.set_bg_image(_bg_full_load)
                        _prev = getattr(self, "settings_bg_image_preview", None)
                        if _prev is not None:
                            _px = QPixmap(_bg_full_load)
                            if not _px.isNull():
                                _prev.setPixmap(
                                    _px.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                )
                # If empty / not found, BackgroundWidget is already in random-cycling mode

                # Favorites
                try:
                    _fav_raw = configuration_dictionary.get(SETTING_FAVORITES, "").strip()
                    if _fav_raw:
                        _fav_list = json.loads(_fav_raw)
                        if isinstance(_fav_list, list):
                            self._favorites = []
                            self._favorites_index = set()
                            for _it in _fav_list:
                                if not isinstance(_it, dict):
                                    continue
                                _src = str(_it.get("source") or "")
                                _id  = str(_it.get("id") or "")
                                if not _src or not _id:
                                    continue
                                self._favorites.append(_it)
                                self._favorites_index.add((_src, _id))
                    if hasattr(self, "_fav_update_tab_badge"):
                        self._fav_update_tab_badge()
                    if hasattr(self, "_fav_refresh_all"):
                        self._fav_refresh_all()
                except Exception:
                    pass

                config_loaded_with_success = True
                add_main_log_window("Loaded configuration file.")
                logging.info("Configuration file loaded successfully.")

            except ValueError as e:
                logging.error(f"Error parsing the configuration file. Value error: {e}")
            except IOError as e:
                logging.error(f"Failed to load configuration file. IOError: {e}")
            except FileNotFoundError:
                logging.error(f"Configuration file not found!")
            except Exception as e:
                logging.error(f"Failed to load configuration file. Exception: {e}")

            return config_loaded_with_success


        def save_configuration_file():

            # Skip saves that are triggered by signal emissions during __init__
            # while widgets are being set up (before load_configuration_file runs).
            if self._initialising:
                return

            get_pyhdfmgooey_currenttab_config()

            try:

                config_array = []
                # Explicit UTF-8: settings values include free-form JSON (the
                # favorites list keeps raw API metadata — Cyrillic titles etc.)
                # which the Windows locale code page cannot encode; without
                # this the write raised UnicodeEncodeError and truncated the
                # cfg, losing the favorites and every setting after them.
                with open(ZX_NEXT_UNITE_CONFIG_FILE_NAME, "w", encoding="utf-8") as config_file:
                    for cs in CONFIG_FILE_SETTINGS:
                        config_array.append(cs + "=" + str(configuration_dictionary[cs]) + '\n')

                    config_file.writelines(config_array)

                if ZX_NEXT_UNITE_VERBOSE_LOG_MODE:
                    logging.info("Configuration file saved successfully.")
                    add_main_log_window("Saved configuration file.")


            except IOError as e:
                logging.error(f"Failed to save configuration file with IOError: {e}")
                add_main_log_window(f"Failed to save configuration file with IOError: {e}")
            except Exception as e:
                logging.error(f"An unexpected error occurred while saving the configuration file. Exception: {e}")
                add_main_log_window(f"An unexpected error occurred while saving the configuration file. Exception: {e}")

        def get_pyhdfmgooey_currenttab_config():
            configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING] = wid_inner.tab.currentIndex()
            # Persist the tidied path (no surrounding quotes, native separators)
            # so the config file stays clean regardless of what is in the box.
            configuration_dictionary[SETTING_HDDFILE] = normalize_sd_image_path(self.imageinput.currentText())
            configuration_dictionary[SETTING_SCREENSIZE] = self.cspect_screensize.currentIndex()
            configuration_dictionary[SETTING_SOUND] = self.cspect_sound.currentIndex()
            configuration_dictionary[SETTING_VSYNC] = self.cspect_vsync.currentIndex()
            configuration_dictionary[SETTING_JOYSTICK] = self.cspect_joystick.currentIndex()
            configuration_dictionary[SETTING_MOUSE] = self.cspect_mouse.currentIndex()
            configuration_dictionary[SETTING_HERTZ] = self.cspect_frequency.currentIndex()
            configuration_dictionary[SETTING_ESC] = self.cspect_esc.currentIndex()
            # Persist the full history as a '|'-delimited string (each entry tidied).
            history_items = []
            for i in range(self.imageinput.count()):
                clean = normalize_sd_image_path(self.imageinput.itemText(i))
                if clean and clean not in history_items:
                    history_items.append(clean)
            configuration_dictionary[SETTING_IMAGE_HISTORY] = "|".join(history_items)
            #save_configuration_file()

        # ── Emulator ops (extracted to zxnu_emulator_ops.py): the CSpect/MAME
        # settings setters + launchers, the MAME install/update chain, the
        # zx-next-unite self-update chain, the .sync dotN version advisory,
        # the CSpect update chain and the item-viewer emulator wiring.
        build_emulator_ops(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            get_tuple_value=get_tuple_value,
            set_all_buttons_disabled=set_all_buttons_disabled,
            set_all_buttons_enabled=set_all_buttons_enabled,
            _update_mame_controls=_update_mame_controls,
            _right_disk_content=lambda: right_disk_image_explorer_content,
            getit_run_in_thread=getit_run_in_thread,
            _start_load_image_hint_animation=lambda *a, **k: _start_load_image_hint_animation(*a, **k),
            add_main_log_window=lambda *a, **k: add_main_log_window(*a, **k),
            execute_shell_command=lambda *a, **k: execute_shell_command(*a, **k),
        )
        # Re-bind the closures the widget wiring below consumes by bare name.
        set_cspect_screen_size = self.set_cspect_screen_size
        set_cspect_sound_on_off = self.set_cspect_sound_on_off
        set_cspect_vsync_on_off = self.set_cspect_vsync_on_off
        set_cspect_joystick_on_off = self.set_cspect_joystick_on_off
        set_cspect_mouse_on_off = self.set_cspect_mouse_on_off
        set_cspect_display_frequency = self.set_cspect_display_frequency
        set_cspect_esc = self.set_cspect_esc
        set_mame_aspect = self.set_mame_aspect
        set_mame_sound = self.set_mame_sound
        set_mame_mouse = self.set_mame_mouse
        set_mame_joystick = self.set_mame_joystick
        set_mame_esc = self.set_mame_esc
        open_cspect_configuration_file = self.open_cspect_configuration_file
        install_mame = self.install_mame
        launch_cspect = self._launch_cspect_fn
        launch_mame = self._launch_mame_fn


        def delete_files_button_show_confirmation_buttons():
            if not self.image_selected_path:
                logging.info("Please select an image file or folder first to delete!")
                add_main_log_window("Please select an image file or folder first to delete!")
                return
            # When "Do not prompt for confirmation on deletion" is enabled, delete
            # straight away; otherwise ask for confirmation via a popup dialog.
            if self.settings_no_prompt_on_deletion_checkbox.isChecked():
                image_delete_files()
                return
            image_confirm_deletion_dialog()


        def is_hdfmonkey_present(silent=False):

            # Pure probe (used at startup and after install): never pop the
            # "install hdfmonkey?" dialog from here — startup already surfaces the
            # download button, and the dialog is reserved for real user actions.
            # *silent* keeps a failed probe out of the log/console: at startup the
            # bundled itch.io CSpect hdfmonkey hasn't been discovered yet, so a
            # bare-"hdfmonkey"-not-on-PATH failure here is misleading — the
            # subsequent downloads/cspect scan may still find a usable copy.
            hdfmonkeyexecresult = execute_hdf_monkey("", "", silent=silent, prompt_if_missing=False)

            try:
                # Key presence off hdfmonkey's usage banner in stdout, NOT the
                # return code: the empty/unknown probe command makes hdfmonkey
                # exit non-zero (the jjjs 0.5.7 build returns 1), so a returncode
                # check would misreport a perfectly working build as missing.
                command_execution = str(hdfmonkeyexecresult.stdout)
                if "hdfmonkey help" in command_execution:
                    return True
                # It ran but produced no recognisable hdfmonkey banner. Only warn
                # when it exited cleanly — a genuine not-found / other failure is
                # already surfaced by execute_hdf_monkey.
                if hdfmonkeyexecresult.returncode == 0:
                    add_main_log_window("Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.")
                return False
            except Exception as e:
                logging.error(f"Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.... {e}")
                add_main_log_window(f"Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.... {e}")
                return False

        def _add_to_image_history(path: str):
            """Add *path* to the top of the image history combo and persist it.
            Duplicates are removed so each path appears only once.
            The list is capped at MAX_IMAGE_HISTORY entries."""
            if not path or path == '""':
                return
            # Remove any existing occurrence so the new one goes to the top
            existing_index = self.imageinput.findText(path)
            if existing_index != -1:
                self.imageinput.removeItem(existing_index)
            self.imageinput.insertItem(0, path)
            # Keep within the max size (skip index 0 which is the current text placeholder)
            while self.imageinput.count() > MAX_IMAGE_HISTORY:
                self.imageinput.removeItem(self.imageinput.count() - 1)
            self.imageinput.setCurrentText(path)
            save_configuration_file()

        def _start_transfer_idle_animation():
            """Start a soft, continuously-looping green 'breathing' background pulse
            on the two transfer-arrow buttons (Send '->:' / Get ':<-'). It runs the
            whole time the SD-card tab is active so the controls gently draw the eye
            even when nothing else is going on.

            The pulse is driven by a plain QTimer that rewrites each button's
            background colour — this is always visible, unlike a QGraphicsEffect,
            and fits the tiny (~30px) buttons without changing their text. Safe to
            call repeatedly; it restarts cleanly."""
            _stop_transfer_idle_animation()

            # Triangle wave over (2*steps) ticks → a smooth fade up and down. The
            # two buttons are offset by half a cycle so they breathe out of phase.
            steps = 22
            phase = {"n": 0}

            def _alpha_for(pos):
                pos %= (2 * steps)
                tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
                return int(150 * tri)

            def _tick():
                phase["n"] = (phase["n"] + 1) % (2 * steps)
                for btn, off in ((self.button_to_image, 0), (self.button_to_disk, steps)):
                    a = _alpha_for(phase["n"] + off)
                    try:
                        btn.setStyleSheet(
                            "QPushButton { "
                            f"background-color: rgba(46,204,113,{a}); "
                            f"border: 1px solid rgba(46,204,113,{min(a + 60, 255)}); "
                            "border-radius: 4px; }")
                    except RuntimeError:
                        pass

            timer = QTimer(self)
            timer.setInterval(55)
            timer.timeout.connect(_tick)
            timer.start()
            self._transfer_anim_timer = timer

        def _stop_transfer_idle_animation():
            """Stop the breathing pulse and restore the buttons' normal appearance."""
            timer = getattr(self, "_transfer_anim_timer", None)
            if timer is not None:
                timer.stop()
                self._transfer_anim_timer = None
            for btn in (self.button_to_image, self.button_to_disk):
                try:
                    btn.setStyleSheet("")
                except RuntimeError:
                    pass

        def _start_hdfmonkey_button_animation():
            """Pulse the 'Download and install HDF Monkey' button in a soft yellow
            'breathing' glow while hdfmonkey is missing, so it draws the eye — the
            amber counterpart to the green transfer-arrow pulse
            (_start_transfer_idle_animation).

            The timer polices itself: on each tick it checks whether the button is
            still on offer and stops (restoring the normal look) the moment the
            button is explicitly hidden — which happens as soon as hdfmonkey is
            installed or otherwise detected. So callers only ever need to *start*
            it (from show_hdf_monkey_download_and_install_buttons); every place
            that hides the button on a successful install/detection stops it for
            free. Safe to call repeatedly — a second call is a no-op while it runs.
            """
            btn = self.download_and_install_hdfmonkey_button
            if getattr(self, "_hdfmonkey_btn_anim_timer", None) is not None:
                return  # already pulsing

            steps = 22
            phase = {"n": 0}

            def _alpha_for(pos):
                pos %= (2 * steps)
                tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
                return int(150 * tri)

            def _tick():
                # Auto-stop once the button is no longer offered (hdfmonkey found
                # / installed) so a stray timer can't keep restyling a hidden or
                # deleted widget. isHidden() (not isVisible()) is used so merely
                # switching away from the SD-card tab doesn't cancel the pulse.
                try:
                    hidden = btn.isHidden()
                except RuntimeError:
                    hidden = True
                if hidden:
                    _stop_hdfmonkey_button_animation()
                    return
                phase["n"] = (phase["n"] + 1) % (2 * steps)
                a = _alpha_for(phase["n"])
                try:
                    btn.setStyleSheet(
                        "QPushButton { "
                        f"background-color: rgba(241,196,15,{a}); "
                        f"border: 1px solid rgba(241,196,15,{min(a + 60, 255)}); "
                        "border-radius: 4px; }")
                except RuntimeError:
                    _stop_hdfmonkey_button_animation()

            timer = QTimer(self)
            timer.setInterval(55)
            timer.timeout.connect(_tick)
            timer.start()
            self._hdfmonkey_btn_anim_timer = timer

        def _stop_hdfmonkey_button_animation():
            """Stop the yellow pulse and restore the download button's normal look."""
            timer = getattr(self, "_hdfmonkey_btn_anim_timer", None)
            if timer is not None:
                timer.stop()
                self._hdfmonkey_btn_anim_timer = None
            try:
                self.download_and_install_hdfmonkey_button.setStyleSheet("")
            except RuntimeError:
                pass

        def _load_image_hint_wanted():
            """True while the 'load an image' hint applies: at least one
            emulator (CSpect or MAME) is usable but no disk image is loaded.
            Mirrors _maybe_show_no_image_toast's emulator gating — with no
            emulator installed the detection toast's "install one" advice is
            the right message, not this pulse."""
            if right_disk_image_explorer_content:
                return False
            _cspect_found = getattr(self, "_cspect_executable_path", None) is not None
            return _cspect_found or self._mame_usable()

        def _start_load_image_hint_animation():
            """Pulse 'Select NextZXOS disk Image' and 'Download NextZXOS Image' in the
            same soft yellow 'breathing' glow as the hdfmonkey install button
            while an emulator is ready but no disk image is loaded — the hint
            that picking an image is the user's next step.

            The timer polices itself: on each tick it re-checks the condition
            and stops (restoring the normal look) the moment an image finishes
            loading. So callers only ever need to *start* it at the moments the
            hint may become due — no image at startup, a failed or cleared
            load, or an emulator detected/installed later. Safe to call
            repeatedly — a no-op while it already runs or while the hint
            doesn't apply."""
            if getattr(self, "_load_image_hint_anim_timer", None) is not None:
                return
            if not _load_image_hint_wanted():
                return

            steps = 22
            phase = {"n": 0}

            def _alpha_for(pos):
                pos %= (2 * steps)
                tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
                return int(150 * tri)

            def _tick():
                if not _load_image_hint_wanted():
                    _stop_load_image_hint_animation()
                    return
                phase["n"] = (phase["n"] + 1) % (2 * steps)
                # Half a cycle apart so the two buttons breathe out of phase,
                # exactly like the transfer arrows.
                for btn, off in ((self.selectimage, 0), (self.downloadimage, steps)):
                    a = _alpha_for(phase["n"] + off)
                    try:
                        btn.setStyleSheet(
                            "QPushButton { "
                            f"background-color: rgba(241,196,15,{a}); "
                            f"border: 1px solid rgba(241,196,15,{min(a + 60, 255)}); "
                            "border-radius: 4px; }")
                    except RuntimeError:
                        _stop_load_image_hint_animation()
                        return

            timer = QTimer(self)
            timer.setInterval(55)
            timer.timeout.connect(_tick)
            timer.start()
            self._load_image_hint_anim_timer = timer

        def _stop_load_image_hint_animation():
            """Stop the yellow hint pulse and restore both buttons' normal look."""
            timer = getattr(self, "_load_image_hint_anim_timer", None)
            if timer is not None:
                timer.stop()
                self._load_image_hint_anim_timer = None
            for btn in (self.selectimage, self.downloadimage):
                try:
                    btn.setStyleSheet("")
                except RuntimeError:
                    pass

        def _maybe_show_no_image_toast():
            """One-shot (per session) yellow advisory pointing at the image
            picker. Shown ONLY when at least one emulator (CSpect or MAME) is
            actually installed — with none installed the detection toast's
            "install one" advice is the right message instead — and only while
            no disk image is selected. Called from load_image's no-image branch
            and from the emulator scan (an emulator found *after* the startup
            load still deserves the hint). Deferred so it positions against the
            shown window at startup; 30 s so it survives a look around the UI,
            OK to dismiss."""
            if getattr(self, "_no_image_toast_shown", False):
                return
            if (self.imageinput.currentText() or "").strip().strip('"'):
                return
            _cspect_found = getattr(self, "_cspect_executable_path", None) is not None
            if not (_cspect_found or self._mame_usable()):
                return
            self._no_image_toast_shown = True
            QTimer.singleShot(800, lambda: self._show_toast(
                "⚠  No disk image selected/found for your emulator",
                "To start an emulator please select first a disk image "
                "at the top of the screen on the SD Card Utility tab.",
                variant="yellow", duration_ms=30000))

        def load_image(on_done=None):
            """Select and load the disk image named in the image-path combo.

            The actual directory listing runs on a worker thread (see
            image_load_root), so this returns immediately rather than blocking the
            UI thread while hdfmonkey reads the image. *on_done* (optional) is
            invoked on the UI thread with True/False once loading completes."""

            global right_disk_image_explorer_content
            global right_disk_image_explorer_path

            # Tidy whatever is in the box (typed, pasted, picked or restored):
            # drop stray surrounding quotes and, on Windows, show native
            # backslash separators. Reflect the cleaned value back into the box
            # so the user sees e.g. C:\temp\next.img rather than "C:/temp\next.img".
            # blockSignals avoids re-entering load_image while we rewrite the text.
            _clean_image_path = normalize_sd_image_path(self.imageinput.currentText())
            if _clean_image_path != self.imageinput.currentText():
                self.imageinput.blockSignals(True)
                self.imageinput.setCurrentText(_clean_image_path)
                self.imageinput.blockSignals(False)

            # Populate right image path content
            self.right_disk_image_path = _clean_image_path

            right_disk_image_explorer_path = []
            right_disk_image_explorer_content = []
            # image_clear_model() bumps the load generation, invalidating any
            # in-flight listing from a previous image so it can't repopulate the
            # tree we are about to rebuild.
            image_clear_model()

            if self.right_disk_image_path and self.right_disk_image_path != '""':
                # Lock the controls while the image is being read; the load
                # callback restores them to the right state for success/failure.
                set_all_buttons_disabled()
                self.diskimageexplorerpathinput.setText("Loading image…")

                def _after(success):
                    if success:
                        self.diskimageexplorerpathinput.setText(generate_disk_file_path().replace('//', '/'))
                        set_all_buttons_enabled()
                        _add_to_image_history(self.right_disk_image_path)
                        # Kick the idle pulse so it's running right after a load,
                        # not only when the tab is (re)entered — and retire the
                        # yellow "load an image" hint, its job is done.
                        _stop_load_image_hint_animation()
                        _start_transfer_idle_animation()
                    else:
                        logging.error(f"Failed loading image :{self.right_disk_image_path}.")
                        add_main_log_window(f"Failed loading image :{self.right_disk_image_path}.")
                        set_all_buttons_disabled()
                        enable_image_selection()
                        _update_image_usage_gauge("")
                    if on_done is not None:
                        on_done(success)

                image_load_root(_after)
                return

            set_all_buttons_disabled()
            enable_image_selection()
            _update_image_usage_gauge("")
            # Make the launch-button gating discoverable: with no image selected
            # the Launch buttons stay greyed out (CSpect needs the mounted image
            # for -mmc=, MAME boots the image file directly), and the only other
            # hint is a hover tooltip on the disabled buttons.
            add_main_log_window(
                "No SD-card disk image selected — pick or create a .img/.hdf "
                "at the top of this tab to unlock the emulator Launch buttons.")
            # Same hint as a yellow advisory toast (see the helper for the
            # emulator-installed gating).
            _maybe_show_no_image_toast()

            if on_done is not None:
                on_done(False)

        def apply_file_extension_filter_nextsync():
            text = self.nextsync_filtertext.text().strip()
            self.nextsync_model.setFilterFixedString(text)
            set_treeview_properties()
            self.nextsync_treeview.show()

        def add_main_log_window(string_to_log:str):
            newItem = QListWidgetItem()
            newItem.setText(string_to_log)
            self.listWidgetLog.insertItem(0, newItem)

            # Mirror into the optional retro 8-bit pygame log (terminal-style,
            # newest at the bottom) whenever it has been built.
            retro = getattr(self, "_main_retro_log", None)
            if retro is not None:
                try:
                    retro.append(string_to_log)
                except Exception:
                    pass

        def add_nextsync_log_window(string_to_log:str, from_top:bool = True):

            newItem = QListWidgetItem()
            newItem.setText(string_to_log)
            if from_top:
                self.nextsync_log.insertItem(0, newItem)
            else:
                self.nextsync_log.insertItem(self.nextsync_log.count(), newItem)

            # Mirror into the optional retro 8-bit pygame log (terminal-style,
            # newest at the bottom) whenever it has been built.
            retro = getattr(self, "_nextsync_retro_log", None)
            if retro is not None:
                try:
                    retro.append(string_to_log)
                except Exception:
                    pass

        def add_help_content(string_to_log:str, from_top:bool = True):

            newItem = QListWidgetItem()
            newItem.setText(string_to_log)
            if from_top:
                self.listWidgetHelp.insertItem(0, newItem)
            else:
                self.listWidgetHelp.insertItem(self.listWidgetHelp.count(), newItem)

            # Mirror into the optional retro 8-bit pygame console (terminal-style,
            # newest at the bottom) whenever it has been built.
            retro = getattr(self, "_help_retro_log", None)
            if retro is not None:
                try:
                    retro.append(string_to_log)
                except Exception:
                    pass

        def set_treeview_properties():
            self.treeview.setSortingEnabled(True)
            self.treeview.sortByColumn(0, Qt.SortOrder.AscendingOrder)
            self.treeview.setSelectionMode(QAbstractItemView.SingleSelection)
            self.nextsync_treeview.setSortingEnabled(True)
            self.nextsync_treeview.sortByColumn(0, Qt.SortOrder.AscendingOrder)
            self.nextsync_treeview.setSelectionMode(QAbstractItemView.SingleSelection)


        def image_newfolder():

            global right_disk_image_explorer_content

            if right_disk_image_explorer_content:  # check that we have an image content first
                # hide create folder and delete folder buttons
                self.button_new_folder.setVisible(False)
                self.button_rename.setVisible(False)
                self.button_delete_files.setVisible(False)
                self.new_folder_input.setVisible(True)
                self.button_create_directory.setVisible(True)
                self.button_create_directory_cancel.setVisible(True)
            else:
                logging.info("Please load an image file first !")
                add_main_log_window("Please load an image file first !")

            save_configuration_file()

        def image_newfolder_cancel():

            global right_disk_image_explorer_content

            if right_disk_image_explorer_content:  # check that we have an image content first
                # hide create folder and delete folder buttons
                self.button_new_folder.setVisible(True)
                self.button_rename.setVisible(True)
                self.button_delete_files.setVisible(True)
                self.new_folder_input.setVisible(False)
                self.button_create_directory.setVisible(False)
                self.button_create_directory_cancel.setVisible(False)
            else:
                logging.info("Please load an image file first !")
                add_main_log_window("Please load an image file first !")

            save_configuration_file()

        def image_invalid_folder_name(name):
            """Return an error message if *name* is not a usable folder name, else ""."""
            if name.strip() == "":
                return "Please enter a folder name."
            for not_allowed_chars in DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS:
                if not_allowed_chars in name:
                    nachars = "".join(DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS)
                    return f"These characters are not allowed: {nachars}"
            return ""

        def image_create_folder_named(name):
            """Create folder *name* inside the current target directory and
            refresh the tree. Assumes *name* has already been validated."""
            directory_to_create = (generate_disk_file_path() + "/" + name).replace("//", "/")
            hdfmonkeyexecresult = execute_hdf_monkey("mkdir", self.right_disk_image_path, extra_argv=[directory_to_create])
            if hdfmonkeyexecresult.returncode != 0:
                logging.error(f"Failed creating directory - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                add_main_log_window(f"Failed creating directory - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
            update_disk_manager_widget_table()

        def image_newfolder_create():

            directory_to_create = self.new_folder_input.text().strip()

            error = image_invalid_folder_name(directory_to_create)
            if error:
                logging.warning(error)
                add_main_log_window(error)
                return

            self.button_new_folder.setVisible(True)
            self.button_rename.setVisible(True)
            self.button_delete_files.setVisible(True)
            self.new_folder_input.setVisible(False)
            self.button_create_directory.setVisible(False)
            self.button_create_directory_cancel.setVisible(False)

            image_create_folder_named(directory_to_create)

        def image_newfolder_dialog():
            # Popup dialog used by the tree's right-click "New Folder" action.
            global right_disk_image_explorer_content

            if not right_disk_image_explorer_content:
                logging.info("Please load an image file first !")
                add_main_log_window("Please load an image file first !")
                return

            nachars = "".join(DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS)

            dialog = QDialog(self)
            dialog.setWindowTitle("Create New Folder")
            dialog.setMinimumWidth(380)
            layout = QVBoxLayout(dialog)

            dest = generate_disk_file_path().replace("//", "/")
            info_label = QLabel(f"Create a new folder in:  {dest}", dialog)
            layout.addWidget(info_label)

            name_input = QLineEdit(dialog)
            name_input.setPlaceholderText("New folder name…")
            name_input.setToolTip(f"Enter a folder name ({nachars} are not allowed).")
            layout.addWidget(name_input)

            error_label = QLabel("", dialog)
            error_label.setStyleSheet("color: #d33;")
            error_label.setVisible(False)
            layout.addWidget(error_label)

            button_box = QDialogButtonBox(dialog)
            create_button = button_box.addButton("Create", QDialogButtonBox.AcceptRole)
            button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
            layout.addWidget(button_box)

            def _on_create():
                name = name_input.text().strip()
                error = image_invalid_folder_name(name)
                if error:
                    error_label.setText(error)
                    error_label.setVisible(True)
                    return
                image_create_folder_named(name)
                dialog.accept()

            create_button.clicked.connect(_on_create)
            button_box.rejected.connect(dialog.reject)
            name_input.returnPressed.connect(_on_create)

            name_input.setFocus()
            dialog.exec()

        def select_image():

            global right_disk_image_explorer_path
            global right_disk_image_explorer_content
            global right_disk_image_path
            global right_disk_image_selected_files

            dialog = QFileDialog(self) # https://doc.qt.io/qtforpython-6.2/PySide6/QtWidgets/QFileDialog.html
            dialog.setFileMode(QFileDialog.AnyFile)
            dialog.setViewMode(QFileDialog.Detail)
            fileName = QFileDialog.getOpenFileName(self,"Open File","/home/", "Images (*.img *.hdf)" )
            self.imageinput.setCurrentText(normalize_sd_image_path(fileName[0]))
            configuration_dictionary[SETTING_HDDFILE] = self.imageinput.currentText()

            right_disk_image_explorer_path = []
            right_disk_image_explorer_content = []
            right_disk_image_path = ""
            right_disk_image_selected_files = []
            image_clear_model()

            # Now try to load it
            def _on_loaded(success):
                if success:
                    save_configuration_file()
                    if self.settings_warn_image_nearly_full_checkbox.isChecked():
                        _warn_if_image_nearly_full(self.right_disk_image_path)
            load_image(_on_loaded)

        def download_nextzxos_image():
            """Quick wizard to download a ready-to-use NextZXOS SD card image from
            zxnext.uk, save it to disk, extract the contained disk image, select it
            into self.imageinput and load it automatically."""

            NEXTZXOS_IMAGES = [
                ("Next distribution 2Gb SD Card Image",
                 "https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-2gb.zip"),
                ("Next distribution 4Gb SD Card Image",
                 "https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-4gb.zip"),
                ("Next distribution 8Gb SD Card Image",
                 "https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-8gb.zip"),
            ]

            dialog = QDialog(self)
            dialog.setWindowTitle("Download NextZXOS Image")
            dialog.setMinimumWidth(480)

            dialog_layout = QVBoxLayout(dialog)

            info_label = QLabel(
                "Select a NextZXOS SD card image to download from zxnext.uk.\n"
                "The image will be saved to a location of your choice and then\n"
                "loaded automatically so you can start using it right away."
            )
            dialog_layout.addWidget(info_label)

            image_combo = QComboBox(dialog)
            for label, url in NEXTZXOS_IMAGES:
                image_combo.addItem(label, url)
            dialog_layout.addWidget(image_combo)

            download_progress = QProgressBar(dialog)
            download_progress.setRange(0, 100)
            download_progress.setValue(0)
            download_progress.setVisible(False)
            dialog_layout.addWidget(download_progress)

            button_box = QDialogButtonBox(dialog)
            download_button = button_box.addButton("Download", QDialogButtonBox.AcceptRole)
            cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
            dialog_layout.addWidget(button_box)

            cancel_button.clicked.connect(dialog.reject)

            def do_download():
                selected_label = image_combo.currentText()
                selected_url = image_combo.currentData()

                suggested_name = os.path.basename(urllib.parse.urlparse(selected_url).path)

                save_path, _selected_filter = QFileDialog.getSaveFileName(
                    dialog,
                    "Save NextZXOS Image",
                    suggested_name,
                    "Zip Archives (*.zip);;All Files (*)"
                )

                if not save_path:
                    return

                download_button.setEnabled(False)
                cancel_button.setEnabled(False)
                image_combo.setEnabled(False)
                download_progress.setVisible(True)
                download_progress.setValue(0)

                add_main_log_window(f"Downloading {selected_label} from {selected_url}")

                try:
                    request = urllib.request.Request(
                        selected_url,
                        headers={"User-Agent": "ZX-Next-Unite"}
                    )
                    with urllib.request.urlopen(request) as response:
                        total_size = response.getheader("Content-Length")
                        total_size = int(total_size) if total_size else 0
                        downloaded = 0
                        chunk_size = 65536
                        with open(save_path, "wb") as out_file:
                            while True:
                                chunk = response.read(chunk_size)
                                if not chunk:
                                    break
                                out_file.write(chunk)
                                downloaded += len(chunk)
                                if total_size:
                                    percent = int(downloaded * 100 / total_size)
                                    download_progress.setValue(min(percent, 100))
                                QApplication.processEvents()
                    download_progress.setValue(100)
                except Exception as download_error:
                    logging.error(f"Failed downloading NextZXOS image: {download_error}")
                    add_main_log_window(f"Failed downloading NextZXOS image: {download_error}")
                    QMessageBox.critical(
                        dialog,
                        "Download Failed",
                        f"Failed to download the NextZXOS image:\n{download_error}"
                    )
                    download_button.setEnabled(True)
                    cancel_button.setEnabled(True)
                    image_combo.setEnabled(True)
                    download_progress.setVisible(False)
                    return

                # Extract the disk image from the downloaded archive so it can be loaded
                image_to_load = save_path
                try:
                    if zipfile.is_zipfile(save_path):
                        extract_dir = os.path.dirname(save_path)
                        with zipfile.ZipFile(save_path) as archive:
                            image_members = [
                                name for name in archive.namelist()
                                if name.lower().endswith((".img", ".hdf"))
                            ]
                            if image_members:
                                archive.extract(image_members[0], extract_dir)
                                image_to_load = os.path.join(extract_dir, image_members[0])
                                add_main_log_window(f"Extracted disk image: {image_to_load}")
                except Exception as extract_error:
                    logging.error(f"Failed extracting NextZXOS image: {extract_error}")
                    add_main_log_window(f"Failed extracting NextZXOS image: {extract_error}")
                    QMessageBox.critical(
                        dialog,
                        "Extraction Failed",
                        f"The image was downloaded but could not be extracted:\n{extract_error}"
                    )
                    download_button.setEnabled(True)
                    cancel_button.setEnabled(True)
                    image_combo.setEnabled(True)
                    download_progress.setVisible(False)
                    return

                dialog.accept()

                global right_disk_image_explorer_path
                global right_disk_image_explorer_content
                global right_disk_image_path
                global right_disk_image_selected_files

                # Select the downloaded image into the image input
                self.imageinput.setCurrentText(normalize_sd_image_path(image_to_load))
                configuration_dictionary[SETTING_HDDFILE] = self.imageinput.currentText()

                right_disk_image_explorer_path = []
                right_disk_image_explorer_content = []
                right_disk_image_path = ""
                right_disk_image_selected_files = []
                image_clear_model()

                # Now try to load it
                def _on_loaded(success):
                    if success:
                        save_configuration_file()
                        if self.settings_warn_image_nearly_full_checkbox.isChecked():
                            _warn_if_image_nearly_full(self.right_disk_image_path)
                load_image(_on_loaded)

            download_button.clicked.connect(do_download)

            dialog.exec()

        def _get_image_free_space_pct(image_path):
            """Parse the FAT layout of image_path and return (free_pct, free_mb, total_mb).
            Returns None if the image cannot be read or is not a recognised FAT volume."""
            try:
                clean = image_path.strip('"').strip("'")
                with open(clean, 'rb') as f:
                    mbr = f.read(512)
                    pte = mbr[446:462]
                    lba_start = struct.unpack_from('<I', pte, 8)[0]
                    f.seek(lba_start * 512)
                    vbr = f.read(512)
                    bps      = struct.unpack_from('<H', vbr, 11)[0]
                    spc      = vbr[13]
                    rsvd     = struct.unpack_from('<H', vbr, 14)[0]
                    nfats    = vbr[16]
                    root_ent = struct.unpack_from('<H', vbr, 17)[0]
                    total16  = struct.unpack_from('<H', vbr, 19)[0]
                    fat_sz16 = struct.unpack_from('<H', vbr, 22)[0]
                    total32  = struct.unpack_from('<I', vbr, 32)[0]
                    fat_sz32 = struct.unpack_from('<I', vbr, 36)[0]
                    fat_sz   = fat_sz32 if fat_sz16 == 0 else fat_sz16
                    total    = total32  if total16  == 0 else total16
                    if not (bps and spc and fat_sz and total):
                        return None
                    data_start     = rsvd + nfats * fat_sz + (root_ent * 32 + bps - 1) // bps
                    total_clusters = (total - data_start) // spc
                    is_fat32       = (total_clusters >= 65525)
                    entry_size     = 4 if is_fat32 else 2
                    fat_offset     = (lba_start + rsvd) * bps
                    fat_size_bytes = fat_sz * bps
                    f.seek(fat_offset)
                    fat_data = f.read(fat_size_bytes)
                    free_clusters = sum(
                        1 for c in range(2, min(total_clusters + 2, len(fat_data) // entry_size))
                        if (struct.unpack_from('<I', fat_data, c * entry_size)[0] & 0x0FFFFFFF
                            if is_fat32
                            else struct.unpack_from('<H', fat_data, c * entry_size)[0]) == 0
                    )
                    cluster_bytes = spc * bps
                    total_mb = total_clusters * cluster_bytes // (1024 * 1024)
                    free_mb  = free_clusters  * cluster_bytes // (1024 * 1024)
                    free_pct = (free_clusters / total_clusters * 100) if total_clusters else 0
                    return (free_pct, free_mb, total_mb)
            except Exception:
                return None

        def _update_image_usage_gauge(image_path=None):
            """Refresh the SD card usage gauge below the image explorer.
            Reads the FAT free-space data from the image and updates the bar colour and tooltip.
            Call with no argument (or empty string) to reset the gauge to an empty state."""
            if not image_path:
                image_path = self.right_disk_image_path if hasattr(self, 'right_disk_image_path') else ""
            result = _get_image_free_space_pct(image_path) if image_path else None
            gauge = self.image_usage_gauge
            if result is None:
                gauge.setValue(0)
                gauge.setFormat("No image loaded")
                gauge.setToolTip("No SD card image is currently loaded.")
                gauge.setStyleSheet("")
                return
            free_pct, free_mb, total_mb = result
            used_pct = 100.0 - free_pct
            used_mb  = total_mb - free_mb
            gauge.setValue(int(round(used_pct)))
            gauge.setFormat(f"{used_pct:.1f} % used")
            gauge.setToolTip(
                f"SD Card Image usage: {used_pct:.1f} % used\n"
                f"{used_mb} MB used / {total_mb} MB total\n"
                f"{free_mb} MB remaining ({free_pct:.1f} % free)"
            )
            if used_pct < 70:
                color = "#4caf50"   # green
            elif used_pct < 90:
                color = "#ff9800"   # orange/yellow
            else:
                color = "#f44336"   # red
            gauge.setStyleSheet(
                f"QProgressBar {{"
                f"  border: 1px solid #555; border-radius: 4px;"
                f"  background: #2b2b2b; text-align: center; color: #ffffff;"
                f"}}"
                f"QProgressBar::chunk {{"
                f"  background-color: {color}; border-radius: 3px;"
                f"}}"
            )

        def _warn_if_image_nearly_full(image_path):
            """Show a warning dialog if the SD image has less than 10 % free space."""
            result = _get_image_free_space_pct(image_path)
            if result is None:
                return
            free_pct, free_mb, total_mb = result
            used_pct = 100 - free_pct
            if free_pct < 10:
                if free_pct == 0:
                    space_line = f"The image is completely full ({total_mb} MB capacity, 0 MB free)."
                else:
                    space_line = (f"Only {free_mb} MB free out of {total_mb} MB "
                                  f"({used_pct:.1f} % used, {free_pct:.1f} % free).")
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("SD Image Nearly Full")
                msg.setText(
                    f"\u26a0\ufe0f  The SD card image is nearly full.\n\n"
                    f"{space_line}\n\n"
                    f"Delete files from the image to free space, or switch to a larger image.\n"
                    f"Larger SD card images can be downloaded from:\n"
                    f"https://zxnext.uk/hosted/"
                )
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec()

        def _check_image_writable(image_path, check_free_space=True):
            """Return None if image_path is writable, or an error string explaining why not.
            Also checks that the FAT volume has at least one free cluster."""
            if not image_path:
                return "No image file selected."
            try:
                clean = image_path.strip('"').strip("'")
                p = pathlib.Path(clean)
                if not p.exists():
                    return f"Image file not found: {clean}"
                # Check for offline cloud file (OneDrive file not downloaded locally)
                if hasattr(p.stat(), 'st_file_attributes'):
                    OFFLINE = 0x1000  # FILE_ATTRIBUTE_OFFLINE
                    if p.stat().st_file_attributes & OFFLINE:
                        return (f"The image file is an offline cloud file (e.g. OneDrive).\n"
                                f"Please right-click the file in Explorer and choose\n"
                                f"'Always keep on this device' to pin it locally before writing.")
                # Definitive write test
                with open(clean, 'r+b') as f:
                    # --- FAT free-cluster check (skipped for delete operations) ---
                    if check_free_space:
                        try:
                            mbr = f.read(512)
                            pte = mbr[446:462]
                            lba_start = struct.unpack_from('<I', pte, 8)[0]
                            f.seek(lba_start * 512)
                            vbr = f.read(512)
                            bps      = struct.unpack_from('<H', vbr, 11)[0]
                            spc      = vbr[13]
                            rsvd     = struct.unpack_from('<H', vbr, 14)[0]
                            nfats    = vbr[16]
                            root_ent = struct.unpack_from('<H', vbr, 17)[0]
                            total16  = struct.unpack_from('<H', vbr, 19)[0]
                            fat_sz16 = struct.unpack_from('<H', vbr, 22)[0]
                            total32  = struct.unpack_from('<I', vbr, 32)[0]
                            fat_sz32 = struct.unpack_from('<I', vbr, 36)[0]
                            fat_sz   = fat_sz32 if fat_sz16 == 0 else fat_sz16
                            total    = total32  if total16  == 0 else total16
                            if bps and spc and fat_sz and total:
                                data_start = rsvd + nfats * fat_sz + (root_ent * 32 + bps - 1) // bps
                                total_clusters = (total - data_start) // spc
                                is_fat32 = (total_clusters >= 65525)
                                entry_size = 4 if is_fat32 else 2
                                fat_offset = (lba_start + rsvd) * bps
                                fat_size_bytes = fat_sz * bps
                                f.seek(fat_offset)
                                fat_data = f.read(fat_size_bytes)
                                free = sum(
                                    1 for c in range(2, min(total_clusters + 2, len(fat_data) // entry_size))
                                    if (struct.unpack_from('<I', fat_data, c * entry_size)[0] & 0x0FFFFFFF
                                        if is_fat32
                                        else struct.unpack_from('<H', fat_data, c * entry_size)[0]) == 0
                                )
                                if free == 0:
                                    cap_mb = total_clusters * spc * bps // 1024 // 1024
                                    return (f"The image volume is full (0 free clusters, {cap_mb} MB capacity).\n"
                                            f"Delete files from the image before adding new content.")
                        except Exception:
                            pass  # FAT parse failure is non-fatal for the write check
            except OSError as e:
                return (f"The image file cannot be opened for writing:\n{e}\n\n"
                        f"If the file is in OneDrive, right-click it and choose\n"
                        f"'Always keep on this device'.")
            except Exception as e:
                return f"Cannot check image file: {e}"
            return None

        def execute_hdf_monkey(command_to_execute, image_path, additional_args="", silent=False, extra_argv=None, prompt_if_missing=True):
            # Sentinel with a non-zero returncode in case we never reach subprocess.run
            exec_process = subprocess.CompletedProcess(args=[], returncode=-1)
            # Prefer a bundled hdfmonkey discovered under downloads/cspect
            # (Windows only); otherwise fall back to the PATH/"hdfmonkey" default.
            hdfmonkey_exe = getattr(self, "_hdfmonkey_executable_path", None) or HDFMONKEY_EXECUTABLE
            execution_cmd = f'{hdfmonkey_exe} {command_to_execute} {image_path} {additional_args}'
            # On Linux/macOS the itch.io CSpect bundle ships hdfmonkey without
            # its executable bit set, so the OS would refuse to launch it
            # (EACCES). Add the bit ourselves (the binary is in the user's own
            # downloads dir, no sudo needed) so it runs like a manually compiled
            # build. No-op on Windows / for the bare "hdfmonkey" PATH default.
            ensure_hdfmonkey_executable(hdfmonkey_exe)
            try:
                img = image_path.strip('"')
                argv = [hdfmonkey_exe, command_to_execute, img]
                if extra_argv is not None:
                    # Caller passes a clean list of path strings – no quoting/parsing needed
                    argv += extra_argv
                elif additional_args:
                    argv += shlex.split(additional_args, posix=True)
                exec_process = subprocess.run(argv, shell=False, check=True,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              **subprocess_no_window_kwargs())

            except PermissionError as ex:
                    # The proactive chmod above couldn't make the bundled
                    # hdfmonkey executable (e.g. read-only filesystem or a file
                    # the user doesn't own). Surface that in the SD-card log
                    # window with the exact fix and full path, rather than
                    # letting it bubble up as an opaque failure.
                    exec_process = subprocess.CompletedProcess(args=[hdfmonkey_exe], returncode=-1)
                    if silent:
                        logging.debug(f"hdfmonkey {command_to_execute} permission denied (silent): {execution_cmd} - {ex}")
                    else:
                        logging.error(f"Permission denied running hdfmonkey: {execution_cmd} - {ex}")
                        add_main_log_window(f"ERROR: Permission denied running hdfmonkey: {hdfmonkey_exe}")
                        if hdfmonkey_needs_exec_bit():
                            cmd = hdfmonkey_chmod_instruction(hdfmonkey_exe)
                            add_main_log_window(
                                "The hdfmonkey provided by the CSpect itch.io package is not "
                                "executable. Make it executable by running:")
                            add_main_log_window(f"    {cmd}")

            except (FileNotFoundError,subprocess.CalledProcessError) as ex:
                    # If hdfmonkey can't actually be located, offer to install it
                    # (once). The dialog is marshalled to the UI thread, so this is
                    # safe even when called from a background worker. A real
                    # hdfmonkey error (binary present) skips this and logs below.
                    if (not silent) and prompt_if_missing and (not self._hdfmonkey_prompt_shown) and (not _hdfmonkey_binary_found()):
                        self._hdfmonkey_prompt_shown = True
                        self._hdfmonkey_missing_signals.missing.emit()
                    stderr_text = ""
                    if not isinstance(ex, FileNotFoundError):
                        stderr_text = (ex.stderr or b"").decode(errors="replace").strip()
                        exec_process = subprocess.CompletedProcess(args=ex.cmd, returncode=ex.returncode,
                                                                   stdout=ex.stdout, stderr=ex.stderr)
                    if silent:
                        # FileNotFoundError has no returncode/stderr — log it as a
                        # plain "not found" rather than touching those attributes.
                        if isinstance(ex, FileNotFoundError):
                            logging.debug(f"hdfmonkey {command_to_execute} not found (silent): {execution_cmd} - {ex}")
                        else:
                            logging.debug(f"hdfmonkey {command_to_execute} returned {ex.returncode} (silent): {execution_cmd}"
                                          + (f" | stderr: {stderr_text}" if stderr_text else ""))
                    elif isinstance(ex, FileNotFoundError) or ex.returncode == 1:
                        logging.error(f"Failed executing hdfmonkey: {execution_cmd} - Once hdfmonkey is installed please close the application and restart it.")
                        add_main_log_window("ERROR: hdfmonkey could not be found. Use the 'Download and install HDF Monkey' button (bottom right of the SD Card tab) to install it automatically, or do a full CSpect install from the itch.io tab, which also bundles hdfmonkey. It can also be installed manually from https://github.com/gasman/hdfmonkey — restart the app once installed.")
                    elif ex.returncode == 255:
                        if execution_cmd is not None:
                            logging.error(f"ERROR: hdfmonkey failed - A file can't be opened: {execution_cmd} this is commonly caused by strange characters such as quotes and signs")
                            add_main_log_window(f"ERROR: hdfmonkey failed - A file can't be opened: {execution_cmd} this is commonly caused by strange characters such as quotes and signs")
                        else:
                            logging.error(f"ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs")
                            add_main_log_window(f"ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs")
                    else:
                        err_detail = f" | stderr: {stderr_text}" if stderr_text else ""
                        if HDFMONKEY_EXECUTABLE is not None and execution_cmd is not None:
                            logging.error(f"ERROR: hdfmonkey {HDFMONKEY_EXECUTABLE} execution failed with unknown error: {execution_cmd} - Exception: {ex}{err_detail}")
                            add_main_log_window(f"ERROR: hdfmonkey {HDFMONKEY_EXECUTABLE} execution failed with unknown error: {execution_cmd} - Exception: {ex}{err_detail}")
                        else:
                            logging.error(f"ERROR: hdfmonkey execution failed with unknown error: - Exception: {ex}{err_detail}")
                            add_main_log_window(f"ERROR: hdfmonkey  execution failed with unknown error: - Exception: {ex}{err_detail}")

            return exec_process

        def execute_shell_command(command_to_execute, additional_args = "", cwd = None):
            execution_cmd = command_to_execute + " " + additional_args
            return subprocess.run(execution_cmd, shell=True, check=True, stdout=subprocess.PIPE, cwd=cwd)

        def execute_shell_command_no_wait(command_to_execute, additional_args = ""):
            execution_cmd = command_to_execute + " " + additional_args
            return subprocess.run(execution_cmd, shell=False, stdin=None, stdout=None, stderr=None,close_fds=True, start_new_session=True, capture_output=False, timeout=None)

        def nextsync_update_root_drive():
            drive = self.nextsync_diskdrive.currentText() or self.nextsync_diskdrive.itemText(0)
            self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(drive)))
            self.nextsync_treeview.show()
            _nextsync_update_set_syncroot_button()
            # The drive switcher also drives the Remote Explorer's local pane so
            # the user can change drive from within it.
            re_widget = getattr(self, "_re_widget", None)
            if re_widget is not None:
                re_widget.set_local_dir(drive)

        # ---------------------------------------------------------------
        # Scan helpers: walk an image directory tree and return flat lists
        # of (image_path_in_image, local_disk_path) pairs or just names,
        # emitting live status/progress so the UI stays responsive.
        # ---------------------------------------------------------------

        def image_confirm_deletion_dialog():
            # Popup wizard asking the user to confirm deletion of the selected
            # image file or folder. Used when the "Do not prompt for confirmation
            # on deletion" setting is False.
            if not self.image_selected_path:
                return

            selected = self.image_selected_paths or [(self.image_selected_path, self.image_selected_is_dir)]

            dialog = QDialog(self)
            dialog.setWindowTitle("Confirm Deletion")
            dialog.setMinimumWidth(420)
            layout = QVBoxLayout(dialog)

            if len(selected) > 1:
                n_dirs = sum(1 for (_p, d) in selected if d)
                question = f"Delete the {len(selected)} selected items"
                if n_dirs:
                    question += " (folders are deleted with all of their contents)"
                question += "?"
                path_text = "\n".join(p.replace("//", "/") for (p, _d) in selected)
            else:
                sel_path, is_dir = selected[0]
                name = sel_path.rstrip("/").rsplit("/", 1)[-1]
                kind = "folder" if is_dir else "file"
                if is_dir:
                    question = f"Delete the {kind} “{name}” and all of its contents?"
                else:
                    question = f"Delete the {kind} “{name}”?"
                path_text = sel_path.replace("//", "/")

            msg = QLabel(question, dialog)
            msg.setWordWrap(True)
            layout.addWidget(msg)

            path_label = QLabel(path_text, dialog)
            path_label.setWordWrap(True)
            path_label.setStyleSheet("color: gray;")
            layout.addWidget(path_label)

            warn = QLabel("This action cannot be undone.", dialog)
            warn.setStyleSheet("color: #d33;")
            layout.addWidget(warn)

            button_box = QDialogButtonBox(dialog)
            delete_button = button_box.addButton("Delete", QDialogButtonBox.AcceptRole)
            cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
            layout.addWidget(button_box)

            delete_button.clicked.connect(dialog.accept)
            cancel_button.clicked.connect(dialog.reject)
            cancel_button.setDefault(True)   # safer default — Enter cancels

            if dialog.exec() == QDialog.Accepted:
                image_delete_files()

        def image_delete_files():
            if not right_disk_image_explorer_content:
                logging.info("Please select an image file or folder first to delete!")
                add_main_log_window("Please select an image file or folder first to delete!")
                return

            if not self.image_selected_path:
                logging.info("Please select an image file or folder first to delete!")
                add_main_log_window("Please select an image file or folder first to delete!")
                return

            img_err = _check_image_writable(self.right_disk_image_path, check_free_space=False)
            if img_err:
                logging.error(img_err)
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return

            # Delete every selected entry. Fall back to the primary selection if
            # the multi-selection list is somehow empty.
            paths_to_delete = [p for (p, _d) in self.image_selected_paths] or [self.image_selected_path]
            # Unique parent directories to refresh once the deletion finishes.
            parent_paths = []
            for p in paths_to_delete:
                parent = p.rstrip("/").rsplit("/", 1)[0] or "/"
                if parent not in parent_paths:
                    parent_paths.append(parent)
            image_path  = self.right_disk_image_path

            set_all_buttons_disabled()

            dlg    = HdfProgressDialog("Deleting files\u2026", self)
            worker = HdfTaskWorker(_run_delete_task, execute_hdf_monkey, image_path, paths_to_delete)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_delete_finished():
                dlg.close()
                for parent_path in parent_paths:
                    image_reload_dir(parent_path)
                set_all_buttons_enabled()

            worker.signals.finished.connect(_on_delete_finished)
            self.threadpool.start(worker)
            dlg.exec()

        def _image_entry_exists(image_path, path):
            """True if *path* (file or directory) already exists inside the image.

            The comparison is case-insensitive because the image is a FAT volume,
            where 'GAME.TAP' and 'game.tap' are the same entry — so a case-only
            rename must also be reported as already existing."""
            parent, name = get_parent_root_directory_splited(path.rstrip("/"))
            result = execute_hdf_monkey("ls", image_path, extra_argv=[parent or "/"])
            if result.returncode != 0:
                return False
            name_cf = name.casefold()
            for line in result.stdout.splitlines():
                decoded = line.decode(errors="replace") if isinstance(line, bytes) else line
                parts = decoded.split('\t', 1)
                if len(parts) == 2 and parts[1].casefold() == name_cf:
                    return True
            return False

        def _run_rename_task(signals, cancel_event, image_path, src_path, new_path,
                             base_name, is_windows):
            """Rename a file/folder inside the image. hdfmonkey has no 'mv', so we
            copy the entry out to a temp dir (get), write it back under the new
            name (put), verify the copy, then remove the original (rm). Folders are
            handled recursively by reusing the get/put/delete task bodies. The
            original is only removed once the new entry is confirmed present, so a
            failed copy never loses data."""

            class _StepProxy:
                """Wraps the real worker signals so every status line the reused
                get/put/delete bodies emit is relabelled with a fixed phase header
                (e.g. 'Uploading (Step 2/3)…'). The per-file detail line and the
                progress/error signals pass straight through."""
                def __init__(self, header):
                    self._header = header
                @property
                def progress(self):
                    return signals.progress
                @property
                def error(self):
                    return signals.error
                @property
                def status(self):
                    return self            # so '.status.emit(msg)' lands on emit()
                def emit(self, msg):
                    detail = msg.split("\n", 1)[1] if "\n" in msg else ""
                    signals.status.emit(f"{self._header}\n{detail}")

            tmp_root = tempfile.mkdtemp(prefix="zxnu_rename_")
            try:
                dir_nav = "\\" if is_windows else "/"

                # ---- Phase 1: copy the source OUT of the image to tmp_root/base_name
                _run_get_task(_StepProxy("Downloading (Step 1/3)…"), cancel_event,
                              execute_hdf_monkey, image_path, [(src_path, base_name)], tmp_root,
                              dir_nav, is_windows)
                if cancel_event.is_set():
                    return
                local_copy = os.path.join(tmp_root, base_name)
                if not os.path.exists(local_copy):
                    signals.error.emit(f"Rename failed: could not copy {src_path} out of the image.")
                    return

                # ---- Phase 2: write it back under the new name
                _run_put_task(_StepProxy("Uploading (Step 2/3)…"), cancel_event,
                              execute_hdf_monkey, _check_access_denied_is_full_disk,
                              image_path, local_copy, new_path)
                if cancel_event.is_set():
                    return
                if not _image_entry_exists(image_path, new_path):
                    signals.error.emit(f"Rename failed: {new_path} was not written — the original is kept.")
                    return

                # ---- Phase 3: remove the original
                _run_delete_task(_StepProxy("Deleting (Step 3/3)…"), cancel_event,
                                 execute_hdf_monkey, image_path, [src_path])
            finally:
                shutil.rmtree(tmp_root, ignore_errors=True)

        def image_rename_dialog():
            """Prompt for and perform a rename of the selected image entry. Acts on
            the primary selection (single entry) and runs on a worker thread."""
            if not right_disk_image_explorer_content or not self.image_selected_path:
                logging.info("Please select an image file or folder first to rename!")
                add_main_log_window("Please select an image file or folder first to rename!")
                return

            src_path = self.image_selected_path.rstrip("/")
            if not src_path or src_path == "/":
                return
            is_dir = self.image_selected_is_dir

            img_err = _check_image_writable(self.right_disk_image_path, check_free_space=False)
            if img_err:
                logging.error(img_err)
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return

            old_name = src_path.rsplit("/", 1)[-1]
            kind = "folder" if is_dir else "file"
            new_name, ok = QInputDialog.getText(
                self, "Rename", f"New name for the {kind}:", text=old_name)
            if not ok:
                return
            new_name = new_name.strip()
            if not new_name or new_name == old_name:
                return
            if "/" in new_name or "\\" in new_name:
                QMessageBox.warning(self, "Rename failed", "The name cannot contain '/' or '\\'.")
                return

            parent     = src_path.rsplit("/", 1)[0]   # "" for a root-level entry
            new_path   = (parent + "/" + new_name).replace("//", "/")
            image_path = self.right_disk_image_path

            if _image_entry_exists(image_path, new_path):
                QMessageBox.warning(self, "Rename failed",
                                    f'"{new_name}" already exists in this folder.')
                return

            parent_dir = parent or "/"
            is_windows = platform.system() == "Windows"

            set_all_buttons_disabled()
            dlg    = HdfProgressDialog("Renaming…", self)
            worker = HdfTaskWorker(_run_rename_task, image_path, src_path, new_path,
                                   old_name, is_windows)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_rename_finished():
                dlg.close()
                image_reload_dir(parent_dir)
                set_all_buttons_enabled()

            worker.signals.finished.connect(_on_rename_finished)
            self.threadpool.start(worker)
            dlg.exec()


        def _nextsync_run_prepare():
            nextsync_warnings()
            save_configuration_file()

        # Debounce timer: coalesces rapid prepare requests (e.g. clicking around
        # the explorer) so the recursive scan in nextsync_warnings() runs once,
        # shortly after the user settles, rather than once per selection change.
        self._nextsync_prepare_timer = QTimer(self)
        self._nextsync_prepare_timer.setSingleShot(True)
        self._nextsync_prepare_timer.setInterval(NEXTSYNC_PREPARE_DEBOUNCE_MS)
        self._nextsync_prepare_timer.timeout.connect(_nextsync_run_prepare)

        def nextsync_perform_checks_and_prepare_server_start():
            self._nextsync_prepare_timer.start()

        def nextsync_refresh_explorer():
            """Force the NextSync left explorer to re-stat the displayed folder.

            Files just written by the upload (.sync5 -send) thread otherwise keep
            showing their initial 0 KB size: QFileSystemModel caches the size from
            when the file was first created (empty) and doesn't reliably re-stat
            it. Toggling the model's root path makes it rescan. Runs on the UI
            thread (wired to sig.finished), so touching the widgets is safe.
            """
            try:
                root_proxy = self.nextsync_treeview.rootIndex()
                root_src = self.nextsync_model.mapToSource(root_proxy)
                view_path = self.nextsync_filesystem_model.filePath(root_src)
                # Bounce via "" so a repeated sync to the same folder still
                # rescans (setRootPath is a no-op when the path is unchanged).
                self.nextsync_filesystem_model.setRootPath("")
                self.nextsync_filesystem_model.setRootPath(view_path or "/")
                if view_path:
                    self.nextsync_treeview.setRootIndex(
                        self.nextsync_model.mapFromSource(
                            self.nextsync_filesystem_model.index(view_path)))
            except Exception as e:
                logging.error(f"NextSync explorer refresh failed: {e}", exc_info=True)

        def nextsync_start_server(serve_folder=None):
            # A gallery "Send via NextSync" (explicit serve_folder) is routed
            # through the Remote Explorer's '.sync5 -listen' session when that
            # server is running: the item is pushed with mkdir/put over the
            # live link instead of starting the classic one-shot sync server
            # (which couldn't bind anyway — the listen server holds port 2048).
            # _re_try_send_folder is defined later in __init__, which is fine:
            # this closure resolves it at call time, always after construction.
            if serve_folder and _re_try_send_folder(serve_folder):
                return
            # Starting the classic server while the Remote Explorer listen
            # server is live can only fail — both need port 2048 — so cancel
            # here with a clear advisory instead of a cryptic bind error.
            if getattr(self, "_re_running", False):
                self._show_toast(
                    "Classic NextSync server not started",
                    "You have already started a Remote Explorer nextsync "
                    "server, please stop it first.",
                    variant="yellow", duration_ms=10000)
                return
            # Guard: don't start a second sync while one is already running
            t = getattr(self, "_nextsync_thread", None)
            if t is not None and t.is_alive():
                add_nextsync_log_window("NextSync is already running — please wait for it to finish.")
                return
            try:
                # --- progress dialog ---
                dlg = HdfProgressDialog("NextSync — sending to ZX Spectrum Next", parent=self, cancel_label="Stop")
                dlg.set_status("Waiting for ZX Next to connect…\nRun .sync5 (or .sync5 -fast) on your Next")
                dlg.set_progress(-1)   # indeterminate spinner until first file

                sig = NextSyncSignals()
                cancel_flag = threading.Event()
                # Exposed for the app-exit path: _graceful_nextsync_shutdown
                # sets it so a sync in flight when the window closes / Ctrl-C
                # arrives ends at a safe file boundary (current file finished,
                # sync point persisted) instead of dying mid-transfer with the
                # process. Stale after the sync ends — always gated on
                # _nextsync_thread.is_alive().
                self._nextsync_cancel_flag = cancel_flag

                sig.progress.connect(dlg.set_progress)
                sig.status.connect(dlg.set_status)
                sig.finished.connect(lambda: QTimer.singleShot(800, dlg.accept))
                # Refresh the left explorer so files received via .sync5 -send show
                # their real size instead of a stale 0 KB.
                sig.finished.connect(nextsync_refresh_explorer)
                sig.cancelled.connect(dlg.mark_cancelled)
                # Port already taken (another instance?) -> yellow toast on the UI thread.
                sig.port_in_use.connect(_nextsync_on_port_in_use)
                dlg.cancel_requested.connect(lambda: cancel_flag.set())

                # "Send via NextSync" (an explicit serve_folder, e.g. from a
                # GalleryItemViewer) transfers a specific item exactly once.
                # Transiently switch the Sync mode to "Sync once" for the duration
                # of the send — without persisting it — then restore the user's
                # chosen mode (e.g. Always sync) when the transfer finishes.
                if serve_folder and not self.nextsync_synconce_checkbox.isChecked():
                    _prev_always = self.nextsync_alwayssync_checkbox.isChecked()
                    _prev_incr = self.nextsync_syncincremental_radio.isChecked()
                    self._nextsync_sync_mode_transient = True
                    self.nextsync_synconce_checkbox.setChecked(True)
                    self._nextsync_sync_mode_transient = False

                    def _restore_sync_mode(_pa=_prev_always, _pi=_prev_incr):
                        self._nextsync_sync_mode_transient = True
                        try:
                            if _pa:
                                self.nextsync_alwayssync_checkbox.setChecked(True)
                            elif _pi:
                                self.nextsync_syncincremental_radio.setChecked(True)
                        finally:
                            self._nextsync_sync_mode_transient = False
                    sig.finished.connect(_restore_sync_mode)

                def _run(_sf=serve_folder):
                    try:
                        nextsync_do_server_job(
                            progress_callback=sig.progress,
                            status_callback=sig.status,
                            cancel_flag=cancel_flag,
                            serve_folder=_sf,
                        )
                    except Exception as ex:
                        logging.error(f"NextSync thread error: {ex}", exc_info=True)
                        # A port clash is reported via its own signal (-> yellow
                        # toast on the UI thread); everything else logs as before.
                        if is_address_in_use(ex):
                            sig.port_in_use.emit(PORT)
                        else:
                            nextsync_server_exception_occured(ex)
                    finally:
                        # A session aborted by an exception would otherwise
                        # leave the transfer flag stuck on (see the sidebar
                        # sync-icon animation).
                        self._nextsync_transfer_active = False
                        if cancel_flag.is_set():
                            sig.cancelled.emit()
                        sig.finished.emit()

                t = threading.Thread(target=_run, daemon=True)
                self._nextsync_thread = t
                nextsync_hide_start_cancel_buttons()
                t.start()
                dlg.exec()   # blocks main thread showing the modal dialog
                # Tear the dialog (and its child spinner QTimer) down on the
                # event loop now, instead of leaving it in a reference cycle for
                # Python's GC to collect later — a QTimer firing / a QDialog
                # being destroyed mid-paint is what triggers the "QBackingStore
                # ::flush() ... does not have a handle" crash on cancel.
                dlg.deleteLater()
                # Ensure pane is in the correct state after dialog closes
                QTimer.singleShot(0, lambda: (
                    nextsync_hide_start_cancel_buttons(),
                    self.nextsync_prepare_server.setVisible(True),
                ))

            except Exception as e:
                logging.error(f"An unexpected error occurred while starting nextsync server. Exception: {e}", exc_info=True)

        # Store on self so it can be called from any scope (e.g. ZXDB/GetIt Send via NextSync)
        self._nextsync_start_server_fn = nextsync_start_server

        # Copies the selected file to image
        def on_treeview_context_menu(pos):
            index = self.treeview.indexAt(pos)
            menu = QMenu(self.treeview)
            source_index = self.proxy_model.mapToSource(index) if index.isValid() else None
            name = self.model.fileName(source_index) if source_index is not None else ""
            # Empty space or the ".." up-entry: only offer "Create new directory",
            # targeting the folder currently shown at the top of the tree.
            if source_index is None or name == "..":
                target_dir = local_current_view_dir()
                menu.addAction("Create new directory…",
                               lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                                   target_dir, local_explorer_refresh, add_main_log_window)))
                action_paste = menu.addAction("Paste")
                action_paste.setEnabled(_explorer_clipboard_has_items())
                action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_local(
                    target_dir, local_explorer_refresh, add_main_log_window)))
                menu.exec(self.treeview.viewport().mapToGlobal(pos))
                return
            file_path = self.model.filePath(source_index)
            is_dir = self.model.isDir(source_index)
            # A new folder lands inside a folder, or in a file's parent folder.
            new_dir_target = file_path if is_dir else os.path.dirname(file_path)
            action_copy_text = QAction("Copy text to clipboard", self.treeview)
            action_copy_path = QAction("Copy path to clipboard", self.treeview)
            action_copy = QAction("Copy", self.treeview)
            action_cut = QAction("Cut", self.treeview)
            action_paste = QAction("Paste", self.treeview)
            action_newdir = QAction("Create new directory…", self.treeview)
            action_rename = QAction("Rename", self.treeview)
            action_copy_text.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
            action_copy_path.triggered.connect(lambda: QGuiApplication.clipboard().setText(file_path))
            action_copy.triggered.connect(lambda: _local_explorer_copy_selection())
            action_cut.triggered.connect(lambda: _local_explorer_copy_selection(mode="cut"))
            action_paste.setEnabled(_explorer_clipboard_has_items())
            action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_local(
                new_dir_target, local_explorer_refresh, add_main_log_window)))
            # Defer the dialogs until the menu's modal loop has closed (showing a
            # QInputDialog from inside menu.exec() fights the menu's input grab).
            action_newdir.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                    new_dir_target, local_explorer_refresh, add_main_log_window)))
            action_rename.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: local_explorer_rename_item(file_path, name, is_dir)))
            # Delete acts on the selection (or the clicked item); the Del key
            # runs the same handler. Deferred like the other dialog-openers.
            action_delete = QAction("Delete", self.treeview)
            action_delete.triggered.connect(
                lambda: QTimer.singleShot(0, local_explorer_delete_selection))
            # Zip actions (mirroring the Remote Explorer's local pane): "Unzip
            # file" only on a .zip file, "Zip" archives the selection (or the
            # clicked item) into <first item>.zip next to it. Deferred like the
            # other dialog-openers so the menu's grab is released first.
            action_unzip = QAction("Unzip file", self.treeview)
            action_unzip.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: _local_unzip_file(
                    file_path, local_explorer_refresh, add_main_log_window)))
            action_zip = QAction("Zip", self.treeview)
            action_zip.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: _local_zip_selection(
                    _local_explorer_selected_paths_or(file_path),
                    local_explorer_refresh, add_main_log_window)))
            menu.addAction(action_copy_text)
            menu.addAction(action_copy_path)
            menu.addSeparator()
            if not is_dir and name.lower().endswith(".zip"):
                menu.addAction(action_unzip)
            menu.addAction(action_zip)
            menu.addSeparator()
            menu.addAction(action_copy)
            menu.addAction(action_cut)
            menu.addAction(action_paste)
            menu.addAction(action_newdir)
            menu.addAction(action_rename)
            menu.addAction(action_delete)
            menu.exec(self.treeview.viewport().mapToGlobal(pos))

        # ---- SD Card explorer pane delegation (strangler seam) --------------
        # The explorer pair's navigation/model layer lives in
        # zxnu_sdcard_explorer.SdCardExplorerPane (constructed further below).
        # These thin wrappers keep the historical closure names alive for the
        # operation layer; new code should call the pane directly.
        def local_explorer_refresh():
            self.sdcard_explorer.local_explorer_refresh()

        def local_current_view_dir():
            return self.sdcard_explorer.local_current_view_dir()

        def local_sync_path_box():
            self.sdcard_explorer.local_sync_path_box()

        def _suspend_local_fs_watchers(suspend):
            """Drop (True) / restore (False) the file watchers of every local
            QFileSystemModel (SD-card tab, NextSync classic tab, and the
            Remote Explorer's local pane — they all browse the same disk).
            On Windows, deleting a folder a model has listed (= watches)
            leaves the watcher's FindFirstChangeNotification handle pointing
            at a pending-delete directory: Qt's watcher thread then spams
            'FindNextChangeNotification failed ... (Access is denied.)' and,
            with enough watched subfolders, that storm freezes the whole UI
            (the QTBUG-65683 family). Suspending clears every watch handle up
            front; the post-delete refresh re-lists (and re-watches) whatever
            is on screen."""
            models = [self.model, self.nextsync_filesystem_model]
            re_widget = getattr(self, "_re_widget", None)
            if re_widget is not None:
                models.append(re_widget.local_model)
            for m in models:
                try:
                    m.setOption(QFileSystemModel.Option.DontWatchForChanges, suspend)
                except Exception:
                    pass

        def _deletes_go_to_recycle_bin():
            """True when local-explorer deletes should go to the Recycle Bin:
            the Settings toggle is on AND the optional Send2Trash package is
            available. Image-side deletes never consult this (a virtual FAT
            filesystem has no bin)."""
            return (send2trash_available()
                    and self.settings_delete_to_recycle_bin_checkbox.isChecked())

        def _local_delete_paths_async(items, log_fn):
            """Delete local *items* [(path, is_dir), …] on a worker thread with
            a progress dialog, with every local file watcher suspended for the
            duration (see _suspend_local_fs_watchers — deleting watched
            subfolders used to hang the UI). Shared by the SD-card and NextSync
            classic explorers' Delete actions. Refreshes both local explorers
            when done (same filesystem) and shows one summary box if anything
            could not be deleted.

            Honours the "Send deleted files to the Recycle Bin" setting: when
            on (and Send2Trash is installed) every item is trashed instead of
            removed; a failed trash is reported as a failure — it never falls
            back to a permanent delete the user did not ask for."""
            use_recycle = _deletes_go_to_recycle_bin()
            _suspend_local_fs_watchers(True)
            holder = {"deleted": [], "failed": []}

            def _task(signals, cancel_event, _items=items, _h=holder,
                      _recycle=use_recycle):
                signals.progress.emit(-1)   # marquee: deletion has no total %

                def _force_remove(func, path, exc_info):
                    # shutil.rmtree onerror: clear a Windows read-only
                    # attribute and retry, so the delete proceeds instead of
                    # erroring out.
                    try:
                        os.chmod(path, stat.S_IWRITE)
                        func(path)
                    except OSError:
                        pass

                verb = "Sending to Recycle Bin…" if _recycle else "Deleting…"
                for p, is_dir in _items:
                    if cancel_event.is_set():
                        return
                    signals.status.emit(f"{verb}\n{p}")
                    try:
                        if _recycle:
                            from send2trash import send2trash
                            # send2trash refuses forward slashes on Windows.
                            send2trash(os.path.normpath(p))
                            if os.path.exists(p):
                                raise OSError(
                                    "still present after sending to the Recycle Bin")
                        elif is_dir and not os.path.islink(p):
                            shutil.rmtree(p, onerror=_force_remove)
                            if os.path.exists(p):
                                # onerror swallowed a real failure and left
                                # part of the tree behind — surface it instead
                                # of silently claiming success (the old code's
                                # failure mode with watched subfolders).
                                raise OSError("some content could not be removed")
                        else:
                            os.remove(p)
                        _h["deleted"].append(p)
                    except OSError as e:
                        logging.error(f"Failed to delete {p}: {e}", exc_info=True)
                        _h["failed"].append((p, str(e)))

            dlg    = HdfProgressDialog("Deleting…", self)
            worker = HdfTaskWorker(_task)
            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(log_fn)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_delete_finished():
                dlg.close()
                _suspend_local_fs_watchers(False)
                for p in holder["deleted"]:
                    log_fn(f"Sent to Recycle Bin: {p}" if use_recycle
                           else f"Deleted: {p}")
                for p, err in holder["failed"]:
                    log_fn(f"Failed to delete {p}: {err}")
                # Both local explorers browse the same filesystem: refresh both.
                local_explorer_refresh()
                nextsync_refresh_explorer()
                if holder["failed"]:
                    listing = "\n".join(p for p, _e in holder["failed"][:10])
                    if len(holder["failed"]) > 10:
                        listing += f"\n… and {len(holder['failed']) - 10} more"
                    QMessageBox.critical(self, "Delete failed",
                                         f"Could not delete:\n{listing}")

            worker.signals.finished.connect(_on_delete_finished)
            self.threadpool.start(worker)
            dlg.exec()

        def local_explorer_delete_selection():
            """Delete the SD-card local explorer's selected files/folders
            (Del key / context menu). Honours the "Do not prompt for
            confirmation on deletion" setting like the NextSync explorer's
            delete; otherwise asks first — folders warn that all their
            contents go too, multi-selections are confirmed as one batch."""
            fallback = ""
            cur = self.treeview.currentIndex()
            if cur.isValid():
                src = self.proxy_model.mapToSource(cur)
                if self.model.fileName(src) != "..":
                    fallback = self.model.filePath(src)
            paths = [p for p in _local_explorer_selected_paths_or(fallback)
                     if p and os.path.exists(p)]
            if not paths:
                return
            items = [(p, os.path.isdir(p)) for p in paths]
            if not self.settings_no_prompt_on_deletion_checkbox.isChecked():
                if len(items) == 1:
                    p, is_dir = items[0]
                    name = os.path.basename(p.rstrip("/\\")) or p
                    msg = (f'Delete the folder "{name}" and all of its '
                           f'contents?\n\n{p}' if is_dir
                           else f'Delete the file "{name}"?\n\n{p}')
                else:
                    listing = "\n".join(p for p, _d in items[:15])
                    if len(items) > 15:
                        listing += f"\n… and {len(items) - 15} more"
                    msg = (f"Delete these {len(items)} items? Folders are "
                           f"deleted with all of their contents.\n\n{listing}")
                msg += ("\n\nDeleted files are sent to the Recycle Bin."
                        if _deletes_go_to_recycle_bin()
                        else "\n\nThis cannot be undone.")
                if QMessageBox.question(
                        self, "Confirm deletion", msg,
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No) != QMessageBox.Yes:
                    return

            _local_delete_paths_async(items, add_main_log_window)

        def _local_unzip_file(zip_path, refresh_fn, log_fn):
            """'Unzip file' on a local explorer's .zip: extract it into its
            own folder (cancellable per-file progress dialog; zip-slip entries
            skipped). Shared by the SD-card local explorer; a cancel keeps
            what was already extracted."""
            name = os.path.basename(zip_path)
            dest = os.path.dirname(zip_path) or "."
            res = zip_extract_with_dialog(self, zip_path, dest, log=log_fn)
            if res["cancelled"]:
                log_fn(f"Unzip of {name} cancelled — already-extracted files remain.")
            elif res["error"]:
                log_fn(f"ERROR: could not extract {name}: {res['error']}")
                QMessageBox.critical(self, "Unzip failed",
                                     f"Could not extract {name}:\n{res['error']}")
            else:
                skipped = res["skipped"]
                extra = (f" ({skipped} unsafe "
                         f"{'entry' if skipped == 1 else 'entries'} skipped)"
                         if skipped else "")
                log_fn(f"Extracted {res['files']} file(s) from {name} into {dest}.{extra}")
            refresh_fn()

        def _local_zip_selection(paths, refresh_fn, log_fn):
            """'Zip' on a local explorer selection: build <first item>.zip next
            to it (name uniquified against the folder), cancellable with
            per-file progress."""
            paths = [p for p in paths if p and os.path.exists(p)]
            if not paths:
                return
            first = os.path.basename(paths[0].rstrip("/\\")) or "archive"
            dest = os.path.dirname(os.path.abspath(paths[0].rstrip("/\\"))) or "."
            try:
                taken = {n.lower() for n in os.listdir(dest)}
            except OSError:
                taken = set()
            zip_name = zip_unique_name(first, taken)
            zip_local = os.path.join(dest, zip_name)
            res = zip_create_with_dialog(self, paths, zip_local, log=log_fn)
            if res["cancelled"]:
                log_fn(f"Zip cancelled — {zip_name} was not created.")
            elif res["error"]:
                log_fn(f"ERROR: could not create {zip_name}: {res['error']}")
                QMessageBox.critical(self, "Zip failed",
                                     f"Could not create {zip_name}:\n{res['error']}")
            else:
                log_fn(f"Created {zip_name} in {dest} ({res['files']} file(s)).")
            refresh_fn()

        def _local_explorer_selected_paths_or(fallback_path):
            """The SD-card local tree's multi-selection paths (minus '..'), or
            the given fallback when nothing is selected."""
            paths = []
            for ix in self.treeview.selectionModel().selectedRows(0):
                src = self.proxy_model.mapToSource(ix)
                if self.model.fileName(src) == "..":
                    continue
                paths.append(self.model.filePath(src))
            return paths or [fallback_path]

        def local_explorer_import_external_paths(paths, dest_dir, refresh_fn=None,
                                                 on_complete=None):
            """Copy files/folders dropped from the OS file manager into dest_dir
            (local filesystem) on a background thread with a progress dialog.
            Reuses the shared copy worker; never overwrites (name clashes get a
            '-(copy)' suffix) and refreshes the explorer when done. *refresh_fn*
            selects which explorer to re-stat (defaults to the SD-card local one);
            the NextSync paste passes its own refresher. *on_complete*, when given,
            is called with a single bool (True only if the transfer finished without
            error or cancellation) — used by cut+paste to remove the source after."""
            if not dest_dir or not os.path.isdir(dest_dir):
                add_main_log_window("Import failed: no valid destination folder.")
                return

            items = []
            for src in paths:
                if not src or not os.path.exists(src):
                    continue
                src_is_dir = os.path.isdir(src)
                if src_is_dir:
                    src_abs = os.path.abspath(src)
                    dest_abs = os.path.abspath(dest_dir)
                    # Guard against importing a folder into itself or a subfolder.
                    if dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep):
                        add_main_log_window(f"Skipped {src}: cannot import a folder into itself.")
                        continue
                base = os.path.basename(src.rstrip("/\\"))
                target = _nextsync_unique_path(os.path.join(dest_dir, base), src_is_dir)
                items.append((src, target, src_is_dir))

            if not items:
                if on_complete:
                    on_complete(False)
                return

            dlg    = HdfProgressDialog("Importing into folder…", self)
            worker = HdfTaskWorker(_run_nextsync_import_task, items)
            outcome = {"error": False, "cancelled": False}

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.error.connect(lambda *_: outcome.update(error=True))
            worker.signals.cancelled.connect(dlg.mark_cancelled)
            worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

            def _on_local_import_finished():
                dlg.close()
                (refresh_fn or local_explorer_refresh)()
                if on_complete:
                    on_complete(not outcome["error"] and not outcome["cancelled"])

            worker.signals.finished.connect(_on_local_import_finished)
            self.threadpool.start(worker)
            dlg.exec()

        def nextsync_current_view_dir():
            """Path of the folder currently shown at the top of the NextSync tree."""
            root_proxy = self.nextsync_treeview.rootIndex()
            root_src = self.nextsync_model.mapToSource(root_proxy)
            return self.nextsync_filesystem_model.filePath(root_src)

        def nextsync_on_treeview_context_menu(pos):
            index = self.nextsync_treeview.indexAt(pos)
            source_index = self.nextsync_model.mapToSource(index) if index.isValid() else None
            name = self.nextsync_filesystem_model.fileName(source_index) if source_index is not None else ""
            # Empty space or the ".." up-entry: only offer Paste, into the current folder.
            if source_index is None or name == "..":
                clipboard_has = _explorer_clipboard_has_items()
                paste_dir = nextsync_current_view_dir()
                menu = QMenu(self.nextsync_treeview)
                action_newdir = QAction("Create new directory…", self.nextsync_treeview)
                action_newdir.triggered.connect(lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                    paste_dir, nextsync_refresh_explorer, add_nextsync_log_window)))
                action_paste = QAction("Paste", self.nextsync_treeview)
                action_paste.setEnabled(clipboard_has and bool(paste_dir))
                action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_paste_explorer_item(paste_dir)))
                menu.addAction(action_newdir)
                menu.addAction(action_paste)
                menu.exec(self.nextsync_treeview.viewport().mapToGlobal(pos))
                return
            file_path = self.nextsync_filesystem_model.filePath(source_index)
            is_dir = self.nextsync_filesystem_model.isDir(source_index)
            # Paste / new-folder target: into the folder itself, or into a file's
            # parent folder.
            paste_dir = file_path if is_dir else os.path.dirname(file_path)
            menu = QMenu(self.nextsync_treeview)
            action_copy_text = QAction("Copy text to clipboard", self.nextsync_treeview)
            action_copy_path = QAction("Copy path to clipboard", self.nextsync_treeview)
            action_newdir = QAction("Create new directory…", self.nextsync_treeview)
            action_copy = QAction("Copy", self.nextsync_treeview)
            action_cut = QAction("Cut", self.nextsync_treeview)
            action_paste = QAction("Paste", self.nextsync_treeview)
            action_rename = QAction("Rename", self.nextsync_treeview)
            action_delete = QAction("Delete", self.nextsync_treeview)
            action_paste.setEnabled(_explorer_clipboard_has_items())
            action_copy_text.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
            action_copy_path.triggered.connect(lambda: QGuiApplication.clipboard().setText(file_path))
            action_copy.triggered.connect(lambda: nextsync_copy_explorer_item(file_path))
            action_cut.triggered.connect(lambda: nextsync_copy_explorer_item(file_path, mode="cut"))
            # Defer the dialog-showing actions until after the context menu's modal
            # event loop has closed: showing a QMessageBox/QInputDialog from inside
            # menu.exec() fights the menu's input grab and can hang the UI.
            action_newdir.triggered.connect(lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                paste_dir, nextsync_refresh_explorer, add_nextsync_log_window)))
            action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_paste_explorer_item(paste_dir)))
            action_rename.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_rename_explorer_item(file_path, name, is_dir)))
            action_delete.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_delete_explorer_item(file_path, name, is_dir)))
            menu.addAction(action_copy_text)
            menu.addAction(action_copy_path)
            menu.addSeparator()
            menu.addAction(action_newdir)
            menu.addAction(action_copy)
            menu.addAction(action_cut)
            menu.addAction(action_paste)
            menu.addAction(action_rename)
            menu.addAction(action_delete)
            menu.exec(self.nextsync_treeview.viewport().mapToGlobal(pos))

        def nextsync_rename_explorer_item(file_path, name, is_dir):
            """Rename a file/folder in the NextSync explorer (local filesystem).

            Prompts for a new name, refuses path separators and overwriting an
            existing entry, then renames in place and refreshes the tree.
            """
            kind = "folder" if is_dir else "file"
            new_name, ok = QInputDialog.getText(
                self, "Rename", f"New name for the {kind}:", text=name)
            if not ok:
                return
            new_name = new_name.strip()
            if not new_name or new_name == name:
                return
            if "/" in new_name or "\\" in new_name:
                QMessageBox.warning(self, "Rename failed", "The name cannot contain '/' or '\\'.")
                return
            new_path = os.path.join(os.path.dirname(file_path), new_name)
            if os.path.exists(new_path):
                QMessageBox.warning(self, "Rename failed", f'"{new_name}" already exists in this folder.')
                return
            try:
                os.rename(file_path, new_path)
                add_nextsync_log_window(f"{timestamp()} | Renamed: {file_path} -> {new_path}")
            except OSError as e:
                logging.error(f"Failed to rename {file_path} -> {new_path}: {e}", exc_info=True)
                add_nextsync_log_window(f"{timestamp()} | Failed to rename {file_path}: {e}")
                QMessageBox.critical(self, "Rename failed", f"Could not rename:\n{file_path}\n\n{e}")
            finally:
                nextsync_refresh_explorer()

        def nextsync_copy_explorer_item(file_path, mode="copy"):
            """Remember a file/folder for a later Paste (or Cut, when mode='cut').
            Routed through the shared cross-explorer clipboard so Copy/Cut/Paste work
            between the NextSync explorer, the SD-card local explorer and the image."""
            _explorer_clipboard_set("local", [(file_path, os.path.isdir(file_path))],
                                    add_nextsync_log_window, mode=mode)

        def _nextsync_unique_path(path, is_dir):
            """Return path, or a non-colliding '-(copy)' variant if it already exists."""
            if not os.path.exists(path):
                return path
            parent = os.path.dirname(path)
            name = os.path.basename(path)
            stem, ext = (name, "") if is_dir else os.path.splitext(name)
            i = 1
            while True:
                suffix = "-(copy)" if i == 1 else f"-(copy) ({i})"
                candidate = os.path.join(parent, f"{stem}{suffix}{ext}")
                if not os.path.exists(candidate):
                    return candidate
                i += 1

        def nextsync_paste_explorer_item(dest_dir):
            """Paste the shared clipboard into dest_dir (NextSync local explorer).
            Handles both local->local copies and image->local downloads, and
            refreshes the NextSync tree when done."""
            _explorer_paste_into_local(dest_dir, nextsync_refresh_explorer,
                                       add_nextsync_log_window)

        def _run_nextsync_import_task(signals, cancel_event, items):
            """Background worker body for drag-and-drop imports into the NextSync
            explorer. *items* is a list of (src_path, target_path, is_dir) where
            target_path has already been made unique on the UI thread.
            Phase 1 enumerates files/dirs (indeterminate progress), Phase 2 creates
            directories then copies each file with real percentage progress."""
            signals.progress.emit(-1)   # indeterminate while scanning
            all_files = []   # (src_file, dst_file)
            all_dirs  = []   # dst directories to create, parents before children
            for src, target, is_dir in items:
                if cancel_event.is_set():
                    break
                signals.status.emit(f"Scanning…\n{src}")
                if not is_dir:
                    all_files.append((src, target))
                    continue
                all_dirs.append(target)
                for dirpath, _dirnames, filenames in os.walk(src):
                    if cancel_event.is_set():
                        break
                    rel = os.path.relpath(dirpath, src)
                    dst_dir = target if rel == "." else os.path.join(target, rel)
                    if rel != ".":
                        all_dirs.append(dst_dir)
                    for fname in filenames:
                        all_files.append((os.path.join(dirpath, fname),
                                          os.path.join(dst_dir, fname)))

            if cancel_event.is_set():
                return

            # ---- Phase 2a: create directories (parents already precede children) ----
            for d in all_dirs:
                if cancel_event.is_set():
                    break
                try:
                    os.makedirs(d, exist_ok=True)
                except OSError as e:
                    logging.error(f"Failed creating folder {d}: {e}")
                    signals.error.emit(f"{timestamp()} | Failed creating folder {d}: {e}")

            if cancel_event.is_set():
                return

            # ---- Phase 2b: copy files ----
            total = max(len(all_files), 1)
            for idx, (src_file, dst_file) in enumerate(all_files):
                if cancel_event.is_set():
                    break
                signals.status.emit(f"Copying ({idx + 1}/{total})\n{src_file}")
                signals.progress.emit(int(idx / total * 100))
                try:
                    shutil.copy2(src_file, dst_file)
                except OSError as e:
                    logging.error(f"Failed copying {src_file} -> {dst_file}: {e}")
                    signals.error.emit(f"{timestamp()} | Failed copying {src_file}: {e}")
                signals.progress.emit(int((idx + 1) / total * 100))

        def nextsync_import_external_paths(paths, dest_dir):
            """Copy files/folders dropped from the OS file manager into dest_dir
            (local filesystem) on a background thread with a progress dialog.
            Mirrors the paste logic: never overwrites (name clashes get a
            '-(copy)' suffix) and refreshes the explorer when done."""
            if not dest_dir or not os.path.isdir(dest_dir):
                add_nextsync_log_window(f"{timestamp()} | Import failed: no valid destination folder.")
                return

            # Resolve sources to unique targets up front (quick, UI thread) so the
            # worker just copies. Self-import and existence are checked here too.
            items = []
            for src in paths:
                if not src or not os.path.exists(src):
                    continue
                src_is_dir = os.path.isdir(src)
                if src_is_dir:
                    src_abs = os.path.abspath(src)
                    dest_abs = os.path.abspath(dest_dir)
                    # Guard against importing a folder into itself or a subfolder.
                    if dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep):
                        add_nextsync_log_window(f"{timestamp()} | Skipped {src}: cannot import a folder into itself.")
                        continue
                base = os.path.basename(src.rstrip("/\\"))
                target = _nextsync_unique_path(os.path.join(dest_dir, base), src_is_dir)
                items.append((src, target, src_is_dir))

            if not items:
                return

            dlg    = HdfProgressDialog("Importing into folder…", self)
            worker = HdfTaskWorker(_run_nextsync_import_task, items)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_nextsync_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_import_finished():
                dlg.close()
                nextsync_refresh_explorer()

            worker.signals.finished.connect(_on_import_finished)
            self.threadpool.start(worker)
            dlg.exec()

        def nextsync_delete_explorer_item(file_path, name, is_dir):
            """Delete a file or folder from the NextSync explorer (local filesystem).

            Honours the "Do not prompt for confirmation on deletion" setting: when
            enabled, delete straight away; otherwise ask the user to confirm first
            (a folder warns it removes the folder and all its contents). Refreshes
            the explorer afterwards so the deleted entry disappears.
            """
            if self.settings_no_prompt_on_deletion_checkbox.isChecked():
                confirmed = True
            else:
                if is_dir:
                    msg = (f'Delete the folder "{name}" and all of its contents?\n\n'
                           f'{file_path}')
                else:
                    msg = f'Delete the file "{name}"?\n\n{file_path}'
                msg += ("\n\nDeleted files are sent to the Recycle Bin."
                        if _deletes_go_to_recycle_bin()
                        else "\n\nThis cannot be undone.")
                reply = QMessageBox.question(
                    self, "Confirm deletion", msg,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                confirmed = reply == QMessageBox.Yes
            if not confirmed:
                return

            _local_delete_paths_async(
                [(file_path, is_dir)],
                lambda m: add_nextsync_log_window(f"{timestamp()} | {m}"))

        def on_nextsync_file_explorer_path_edited():
            # Typing a folder path into the sync-root box commits it as the new
            # sync root (and navigates the explorer there). Files are rejected:
            # the sync root is always a directory.
            new_path = self.nextsync_file_explorer_path.text().strip()
            if os.path.isdir(new_path):
                norm = new_path.replace("\\", "/")
                if not norm.endswith("/"):
                    norm += "/"
                self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(norm, 0)))
                set_treeview_properties()
                self.nextsync_treeview.show()
                _nextsync_commit_sync_root(norm)
            else:
                # Restore the previous valid value
                self.nextsync_file_explorer_path.setText(self.left_file_nextsync_explorer_selection_full_filename_path)

        def nextsync_get_fileexplorer_root_selection():
              if self.left_file_nextsync_explorer_selection_full_filename_path != "":
                selected_explorer_item_directory_destination = ""
                if not os.path.isdir(self.left_file_nextsync_explorer_selection_full_filename_path):
                    # we are pointing to a file not a directory
                    splitted_filepath = self.left_file_nextsync_explorer_selection_full_filename_path.split('/')
                    for file_dest_token in range (0, len(splitted_filepath)-2):
                        selected_explorer_item_directory_destination += splitted_filepath[file_dest_token] + "/"
                else:
                    selected_explorer_item_directory_destination = self.left_file_nextsync_explorer_selection_full_filename_path
                    if not self.left_file_nextsync_explorer_selection_full_filename_path.endswith("/"):
                        selected_explorer_item_directory_destination = selected_explorer_item_directory_destination + "/"

                return selected_explorer_item_directory_destination
              else:
                return ""

        def nextsync_show_sync_buttons_based_on_fileexplorer_content_selection():

            if self.left_file_nextsync_explorer_selection_full_filename_path != "":
                selected_explorer_item_directory_destination = nextsync_get_fileexplorer_root_selection()
                if selected_explorer_item_directory_destination == "":
                    return

                # first hide all buttons
                self.nextsync_button_create_syncignore.setVisible(False)
                self.nextsync_button_delete_syncignore.setVisible(False)
                self.nextsync_button_delete_syncpointfile.setVisible(False)

                if os.path.exists(selected_explorer_item_directory_destination + IGNOREFILE) and os.path.isfile(selected_explorer_item_directory_destination + IGNOREFILE):
                    # ignore file exists offer to delete it
                    self.nextsync_button_delete_syncignore.setVisible(True)
                else:
                    # ignore file does not exist offer to create it
                    self.nextsync_button_create_syncignore.setVisible(True)

                if os.path.exists(selected_explorer_item_directory_destination + SYNCPOINT) and os.path.isfile(selected_explorer_item_directory_destination + SYNCPOINT):
                    # SYNCPOINT file exists offer to delete it
                    self.nextsync_button_delete_syncpointfile.setVisible(True)



        def nextsync_create_sample_ignorefile(file):
            try:
                config_array = []
                for cs in IGNOREFILE_DEFAULT_CONTENT:
                    config_array.append(cs + '\n')
                with open(file, "w") as config_file:
                    config_file.writelines(config_array)
            except Exception as e:
                logging.error(f"Failed creating: {file} Exception: {e}")
                add_nextsync_log_window(f"Failed creating: {file} Exception: {e}")

        def nextsync_create_syncingore_button():
            nextsync_create_sample_ignorefile(nextsync_get_fileexplorer_root_selection() + IGNOREFILE)
            nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
            save_configuration_file()

        def nextsync_delete_syncingore_button():
            try:
                os.remove(nextsync_get_fileexplorer_root_selection() + IGNOREFILE)
            except Exception as e:
                logging.error(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + IGNOREFILE} Exception: {e}")
                add_nextsync_log_window(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + IGNOREFILE} Exception: {e}")

            nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
            save_configuration_file()

        def nextsync_delete_syncpoint_button():
            try:
                os.remove(nextsync_get_fileexplorer_root_selection() + SYNCPOINT)
            except Exception as e:
                logging.error(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + SYNCPOINT} Exception: {e}")
                add_nextsync_log_window(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + SYNCPOINT} Exception: {e}")

            nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()


        def nextsync_sync_mode_changed():
            # Persist the current "Sync mode" radio selection into the two legacy
            # boolean settings. The radios are exclusive, so at most one of these
            # is "true"; "Sync changed files" (incremental) leaves both "false".
            configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] = (
                "true" if self.nextsync_synconce_checkbox.isChecked() else "false")
            configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] = (
                "true" if self.nextsync_alwayssync_checkbox.isChecked() else "false")
            save_configuration_file()

        def nextsync_slowtransfer_checkbox_statechanged():
            # The payload size itself is derived from this persisted setting at
            # server start (see nextsync_do_server_job) — the old in-place
            # MAX_PAYLOAD rebind here was a dead local and never took effect.
            if self.nextsync_slowtransfer_checkbox.isChecked():
                configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] = "true"
            else:
                configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] = "false"

            save_configuration_file()

        def nextsync_on_treeview_double_clicked(ix):
            # Pure navigation: double-clicking a folder (or "..") only changes
            # the folder being browsed. The sync root (the box below the
            # explorer) is only changed via the "Set current folder as new sync
            # root folder" button or by typing a folder path into the box.
            if not ix.isValid():
                return

            source_ix = self.nextsync_model.mapToSource(ix)
            file_name = self.nextsync_filesystem_model.fileName(source_ix)
            file_path = self.nextsync_filesystem_model.filePath(source_ix)

            if file_name == "..":
                current_root_source = self.nextsync_model.mapToSource(self.nextsync_treeview.rootIndex())
                current_root_path = self.nextsync_filesystem_model.filePath(current_root_source)
                parent_path = os.path.dirname(current_root_path.rstrip("/\\"))
                if not parent_path:
                    return
                selected_explorer_item_directory_destination = parent_path.replace("\\", "/") + "/"

            elif self.nextsync_filesystem_model.isDir(source_ix):
                selected_explorer_item_directory_destination = file_path
                if not selected_explorer_item_directory_destination.endswith("/"):
                    selected_explorer_item_directory_destination += "/"

            else:
                return

            self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(selected_explorer_item_directory_destination, 0)))
            set_treeview_properties()
            self.nextsync_treeview.show()

            _nextsync_update_set_syncroot_button()

        def _nextsync_current_browse_dir():
            """The folder the NextSync left explorer is currently showing."""
            root_src = self.nextsync_model.mapToSource(self.nextsync_treeview.rootIndex())
            path = self.nextsync_filesystem_model.filePath(root_src)
            if not path:
                return ""
            path = path.replace("\\", "/")
            if not path.endswith("/"):
                path += "/"
            return path

        def _nextsync_commit_sync_root(path):
            """Record `path` (a directory, forward slashes, trailing "/") as the
            Classic sync root: shown in the sync-root box, persisted, and picked
            up by the SyncIgnore/SyncPoint buttons and the prepare scan."""
            self.left_file_nextsync_explorer_selection_full_filename_path = path
            self.nextsync_file_explorer_path.setText(path)
            configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = path
            save_configuration_file()
            nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
            # Re-run the Prepare scan for the new root so the "Ready to sync N
            # files" log stays accurate and Start stays available without an
            # extra Prepare click.
            nextsync_perform_checks_and_prepare_server_start()
            _nextsync_update_set_syncroot_button()

        def _nextsync_update_set_syncroot_button():
            """Offer "Set current folder as new sync root folder" only while the
            browsed folder differs from the sync root shown in the box."""
            current = _nextsync_current_browse_dir()
            root = (self.left_file_nextsync_explorer_selection_full_filename_path
                    or "").replace("\\", "/")
            same = (current != "" and root != "" and
                    os.path.normcase(current.rstrip("/")) == os.path.normcase(root.rstrip("/")))
            visible = current != "" and not same
            self.nextsync_set_syncroot_button.setVisible(visible)
            # Pulse the offer green while it is on screen (defined by the
            # pane builder; guarded for construction order).
            pulse = getattr(self, "_nextsync_syncroot_pulse_set", None)
            if pulse is not None:
                pulse(visible)

        def _nextsync_on_set_syncroot_clicked():
            folder = _nextsync_current_browse_dir()
            if not folder:
                return
            if QMessageBox.question(
                    self, "Set sync root",
                    "Set this folder as the new sync root?\n\n" + folder,
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Yes) == QMessageBox.Yes:
                _nextsync_commit_sync_root(folder)

        def transfert_content_from_image_to_disk():

            global right_disk_image_explorer_content

            if not right_disk_image_explorer_content:
                logging.warning("Please load an image file first !")
                add_main_log_window("Please load an image file first !")
                return

            set_all_buttons_disabled()

            selected_explorer_item_directory_destination = ""
            if self.left_file_explorer_selection_full_filename_path:
                if not os.path.isdir(self.left_file_explorer_selection_full_filename_path):
                    parts = self.left_file_explorer_selection_full_filename_path.split('/')
                    selected_explorer_item_directory_destination = "/".join(parts[:-1]) + "/"
                else:
                    selected_explorer_item_directory_destination = self.left_file_explorer_selection_full_filename_path
            else:
                set_all_buttons_enabled()
                return

            is_windows = platform.system() == "Windows"
            if is_windows:
                selected_explorer_item_directory_destination = selected_explorer_item_directory_destination.replace("/", "\\")
                directory_navigation = "\\"
            else:
                directory_navigation = "/"

            if not self.image_selected_path:
                set_all_buttons_enabled()
                return

            base_name  = self.image_selected_path.rstrip("/").rsplit("/", 1)[-1]
            items      = [(self.image_selected_path, base_name)]
            image_path = self.right_disk_image_path

            dlg    = HdfProgressDialog("Downloading from image\u2026", self)
            worker = HdfTaskWorker(_run_get_task, execute_hdf_monkey, image_path, items,
                                   selected_explorer_item_directory_destination,
                                   directory_navigation, is_windows)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_get_finished():
                dlg.close()
                set_all_buttons_enabled()

            worker.signals.finished.connect(_on_get_finished)
            self.threadpool.start(worker)
            dlg.exec()

        def image_get_paths_to_local(image_items, dest_dir, refresh_fn=None,
                                     on_complete=None):
            """Download image entries dragged out of the SD-card image tree into a
            local folder. *image_items* is a list of (image_path, is_dir); *dest_dir*
            is the local directory the drop landed on. This is the drag-and-drop
            equivalent of transfert_content_from_image_to_disk (the ':<-' button),
            but driven by the drag source/target rather than the current selections,
            and able to handle several dragged entries at once. *refresh_fn* selects
            which local explorer to re-stat afterwards (defaults to the SD-card one;
            an image->NextSync paste passes the NextSync refresher)."""
            global right_disk_image_explorer_content

            if not right_disk_image_explorer_content:
                logging.warning("Please load an image file first !")
                add_main_log_window("Please load an image file first !")
                return

            if not dest_dir or not os.path.isdir(dest_dir):
                add_main_log_window("Download failed: no valid destination folder.")
                return

            items = []
            for path, _is_dir in image_items:
                if not path:
                    continue
                base_name = path.rstrip("/").rsplit("/", 1)[-1]
                items.append((path, base_name))
            if not items:
                return

            is_windows = platform.system() == "Windows"
            # _run_get_task joins dest + dir_nav + base, so strip any trailing
            # separator from the dropped folder to avoid a doubled separator.
            dest = dest_dir.rstrip("/\\")
            if is_windows:
                dest = dest.replace("/", "\\")
                directory_navigation = "\\"
            else:
                directory_navigation = "/"

            set_all_buttons_disabled()

            image_path = self.right_disk_image_path
            dlg    = HdfProgressDialog("Downloading from image…", self)
            worker = HdfTaskWorker(_run_get_task, execute_hdf_monkey, image_path, items, dest,
                                   directory_navigation, is_windows)
            outcome = {"error": False, "cancelled": False}

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.error.connect(lambda *_: outcome.update(error=True))
            worker.signals.cancelled.connect(dlg.mark_cancelled)
            worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

            def _on_get_finished():
                dlg.close()
                (refresh_fn or local_explorer_refresh)()
                set_all_buttons_enabled()
                if on_complete:
                    on_complete(not outcome["error"] and not outcome["cancelled"])

            worker.signals.finished.connect(_on_get_finished)
            self.threadpool.start(worker)
            dlg.exec()

        # ── Shared cross-explorer clipboard (Ctrl+C / Ctrl+V / Ctrl+X) ─────────
        # One buffer drives Copy/Paste across all three explorers: the SD-card
        # local tree, the NextSync local tree and the SD-card image tree. It
        # remembers the source kind ('local' real files vs 'image' in-image
        # paths) plus the selected (path, is_dir) entries; Paste then routes to
        # the right transfer helper based on source + destination:
        #   local -> local : local_explorer_import_external_paths
        #   local -> image : image_upload_external_paths
        #   image -> local : image_get_paths_to_local
        #   image -> image : image_copy_items_within (get to temp, then put)
        # Paste also accepts files copied in the OS file manager (Windows Explorer
        # etc.): if the system clipboard holds file URLs and was updated more
        # recently than our internal copy, those files are pasted instead.
        def _next_clip_serial():
            self._clip_serial_counter = getattr(self, "_clip_serial_counter", 0) + 1
            return self._clip_serial_counter

        def _on_os_clipboard_changed():
            # Any external clipboard change (e.g. Ctrl+C in Windows Explorer) makes
            # the system clipboard the most-recent copy source.
            self._os_clip_serial = _next_clip_serial()
        try:
            QGuiApplication.clipboard().dataChanged.connect(_on_os_clipboard_changed)
        except Exception:
            pass

        def _os_clipboard_files():
            """Local file paths currently on the system clipboard (or [])."""
            try:
                md = QGuiApplication.clipboard().mimeData()
            except Exception:
                return []
            if md is None or not md.hasUrls():
                return []
            return [u.toLocalFile() for u in md.urls()
                    if u.isLocalFile() and u.toLocalFile()]

        def _os_clipboard_is_cut():
            """True when the files on the system clipboard were *cut* (move) rather
            than copied. Windows records this in the 'Preferred DropEffect' clipboard
            format — a little-endian DWORD where bit 1 (value 2) is DROPEFFECT_MOVE
            and bit 2 (value 5/copy) is a plain copy. Other OS file managers have no
            such convention, so a cut there is indistinguishable from a copy and we
            conservatively treat it as a copy."""
            try:
                md = QGuiApplication.clipboard().mimeData()
                if md is None:
                    return False
                # Qt exposes the raw Windows clipboard format under a mangled name;
                # accept both the friendly and the qt-windows-mime spellings.
                for fmt in ("Preferred DropEffect",
                            'application/x-qt-windows-mime;value="Preferred DropEffect"'):
                    if md.hasFormat(fmt):
                        raw = bytes(md.data(fmt))
                        if len(raw) >= 4:
                            effect = int.from_bytes(raw[:4], "little")
                            # DROPEFFECT_COPY == 1, DROPEFFECT_MOVE == 2: a cut sets
                            # the move bit (copy sets only bit 0, so 1 & 2 == 0).
                            return bool(effect & 2)
            except Exception:
                pass
            return False

        def _explorer_clipboard_set(source, items, log_fn=None, mode="copy"):
            items = [(p, bool(d)) for (p, d) in items if p]
            if not items:
                return
            self._explorer_clipboard = {"source": source, "items": items, "mode": mode}
            self._explorer_clip_serial = _next_clip_serial()
            if log_fn:
                names = ", ".join(os.path.basename(p.rstrip("/\\")) or p
                                  for p, _ in items[:3])
                more = "" if len(items) <= 3 else f" (+{len(items) - 3} more)"
                verb = "Cut to" if mode == "cut" else "Copied to"
                log_fn(f"{verb} clipboard: {names}{more}")

        def _explorer_clipboard_clear():
            """Forget the internal clipboard buffer (used after a cut+paste move so
            a second paste cannot duplicate or re-move the now-relocated source)."""
            self._explorer_clipboard = None
            self._explorer_clip_serial = _next_clip_serial()

        def _explorer_effective_clip():
            """Resolve which copy source a Paste should use. Returns
            ('os', [paths], mode) for system-clipboard files, ('clip', clipdict, mode)
            for the internal buffer, or (None, None, None). *mode* is 'copy' or 'cut'.
            The most recently updated source wins; ties (e.g. files already on the
            clipboard at startup) favour the OS."""
            os_paths = _os_clipboard_files()
            clip = getattr(self, "_explorer_clipboard", None)
            clip_has = bool(clip and clip.get("items"))
            os_serial = getattr(self, "_os_clip_serial", 0)
            clip_serial = getattr(self, "_explorer_clip_serial", 0)
            if os_paths and (not clip_has or os_serial >= clip_serial):
                return ("os", os_paths, "cut" if _os_clipboard_is_cut() else "copy")
            if clip_has:
                return ("clip", clip, clip.get("mode", "copy"))
            if os_paths:
                return ("os", os_paths, "cut" if _os_clipboard_is_cut() else "copy")
            return (None, None, None)

        def _explorer_clipboard_has_items():
            kind, _data, _mode = _explorer_effective_clip()
            return kind is not None

        def _same_dir(path, dir_path):
            """True if *path* lives directly inside *dir_path* (case-insensitive on
            Windows). Used to make a cut+paste into the source's own folder a no-op."""
            try:
                parent = os.path.dirname(os.path.abspath(path.rstrip("/\\")))
                return os.path.normcase(parent) == os.path.normcase(os.path.abspath(dir_path))
            except Exception:
                return False

        def _image_name_in_dir(image_path, parent, name):
            """True if *name* already exists in the image directory *parent*."""
            res = execute_hdf_monkey("ls", image_path, extra_argv=[parent or "/"], silent=True)
            if res.returncode != 0:
                return False
            for n, _isdir, _sz in image_parse_ls(res.stdout):
                if n == name:
                    return True
            return False

        def _image_unique_name(image_path, parent, base):
            """Return *base*, or a non-colliding '-(copy)' variant in *parent*."""
            if not _image_name_in_dir(image_path, parent, base):
                return base
            stem, ext = os.path.splitext(base)
            i = 1
            while True:
                suffix = "-(copy)" if i == 1 else f"-(copy) ({i})"
                candidate = f"{stem}{suffix}{ext}"
                if not _image_name_in_dir(image_path, parent, candidate):
                    return candidate
                i += 1

        def _run_image_copy_task(signals, cancel_event, image_path, items, target_dir):
            """Worker: copy image entries to another place in the same image.
            hdfmonkey has no in-image copy, so each source is downloaded to a
            temp dir then re-uploaded under *target_dir* with a unique name."""
            tmp = tempfile.mkdtemp(prefix="zxnu_imgcopy_")
            try:
                is_windows = platform.system() == "Windows"
                dir_nav = "\\" if is_windows else "/"
                dest_tmp = tmp.rstrip("/\\")
                parent = target_dir.rstrip("/") or "/"
                for src, _isdir in items:
                    if cancel_event.is_set():
                        break
                    base = src.rstrip("/").rsplit("/", 1)[-1]
                    # Download this source into the temp dir …
                    _run_get_task(signals, cancel_event, execute_hdf_monkey, image_path, [(src, base)],
                                  dest_tmp, dir_nav, is_windows)
                    if cancel_event.is_set():
                        break
                    # … then upload it back under a unique name in target_dir.
                    local_path = os.path.join(dest_tmp, base)
                    unique = _image_unique_name(image_path, parent, base)
                    dest_image = (parent.rstrip("/") + "/" + unique).replace("//", "/")
                    _run_put_task(signals, cancel_event, execute_hdf_monkey,
                                  _check_access_denied_is_full_disk,
                                  image_path, local_path, dest_image)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        def image_copy_items_within(image_items, target_dir, on_complete=None):
            """Paste image clipboard entries into another image folder (image->image).
            *on_complete*, when given, is called with a single bool (True only if the
            copy finished without error or cancellation) so cut+paste can remove the
            source entries afterward."""
            global right_disk_image_explorer_content
            if not right_disk_image_explorer_content:
                add_main_log_window("Please load an image file first !")
                return
            img_err = _check_image_writable(self.right_disk_image_path)
            if img_err:
                logging.error(img_err)
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return
            items = [(p, d) for (p, d) in image_items if p]
            if not items:
                return
            target = (target_dir or "/").rstrip("/") or "/"
            set_all_buttons_disabled()
            image_path = self.right_disk_image_path
            dlg    = HdfProgressDialog("Copying within image…", self)
            worker = HdfTaskWorker(_run_image_copy_task, image_path, items, target)
            outcome = {"error": False, "cancelled": False}

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.error.connect(lambda *_: outcome.update(error=True))
            worker.signals.cancelled.connect(dlg.mark_cancelled)
            worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

            def _on_done():
                dlg.close()
                image_reload_dir(target or "/")
                set_all_buttons_enabled()
                if on_complete:
                    on_complete(not outcome["error"] and not outcome["cancelled"])

            worker.signals.finished.connect(_on_done)
            self.threadpool.start(worker)
            dlg.exec()

        def _delete_local_paths_after_move(paths, log_fn):
            """Silently remove real-filesystem source files/folders once a cut+paste
            copy has succeeded (this is what turns a copy into a move). Clears the
            Windows read-only attribute on the way down so rmtree cannot stall."""
            def _force_remove(func, path, exc_info):
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except OSError:
                    pass
            for p in paths:
                try:
                    if not p or not os.path.exists(p):
                        continue
                    if os.path.isdir(p):
                        shutil.rmtree(p, onerror=_force_remove)
                    else:
                        os.remove(p)
                    if log_fn:
                        log_fn(f"Moved (removed source): {p}")
                except OSError as e:
                    logging.error(f"Cut: failed to remove source {p}: {e}", exc_info=True)
                    if log_fn:
                        log_fn(f"Cut: failed to remove source {p}: {e}")

        def _delete_image_paths_after_move(paths, log_fn):
            """Remove in-image source entries once a cut+paste copy has succeeded.
            hdfmonkey deletes run on the thread pool with a progress dialog, then the
            affected source folders are re-listed so the moved entries disappear."""
            paths = [p for p in paths if p and p != UP_DIRECTORY]
            if not paths:
                return
            image_path = self.right_disk_image_path
            parents = []
            for p in paths:
                parent = p.rstrip("/").rsplit("/", 1)[0] or "/"
                if parent not in parents:
                    parents.append(parent)
            set_all_buttons_disabled()
            dlg    = HdfProgressDialog("Removing moved source…", self)
            worker = HdfTaskWorker(_run_delete_task, execute_hdf_monkey, image_path, paths)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _done():
                dlg.close()
                for parent in parents:
                    image_reload_dir(parent)
                set_all_buttons_enabled()
                if log_fn:
                    log_fn("Moved: removed source from image.")

            worker.signals.finished.connect(_done)
            self.threadpool.start(worker)
            dlg.exec()

        def _explorer_paste_into_local(dest_dir, refresh_fn, log_fn):
            """Paste into a real-filesystem directory (either local explorer):
            OS-clipboard files, a local->local copy, or an image->local download.
            When the clipboard is in 'cut' mode the source is removed after a
            successful copy, turning the paste into a move."""
            kind, data, mode = _explorer_effective_clip()
            if kind is None:
                return
            if not dest_dir or not os.path.isdir(dest_dir):
                log_fn("Paste failed: no valid destination folder.")
                return
            is_cut = (mode == "cut")

            if kind == "os":
                src_paths = list(data)
                def _after(success, _p=src_paths):
                    if success and is_cut:
                        _delete_local_paths_after_move(_p, log_fn)
                        local_explorer_refresh()
                        nextsync_refresh_explorer()
                        try:
                            QGuiApplication.clipboard().clear()
                        except Exception:
                            pass
                local_explorer_import_external_paths(data, dest_dir, refresh_fn=refresh_fn,
                                                     on_complete=_after if is_cut else None)
            elif data["source"] == "local":
                paths = [p for (p, _d) in data["items"]]
                # Cutting into the source's own folder is a no-op move.
                if is_cut and paths and all(_same_dir(p, dest_dir) for p in paths):
                    log_fn("Nothing to move: items are already in this folder.")
                    _explorer_clipboard_clear()
                    return
                def _after(success, _p=paths):
                    if success and is_cut:
                        _delete_local_paths_after_move(_p, log_fn)
                        # Refresh both local explorers so the source view also
                        # reflects the removal, wherever the cut originated.
                        local_explorer_refresh()
                        nextsync_refresh_explorer()
                        _explorer_clipboard_clear()
                local_explorer_import_external_paths(paths, dest_dir, refresh_fn=refresh_fn,
                                                     on_complete=_after if is_cut else None)
            else:
                img_items = list(data["items"])
                img_paths = [p for (p, _d) in img_items]
                def _after(success, _p=img_paths):
                    if success and is_cut:
                        # Defer so the download dialog's event loop fully unwinds
                        # before the delete dialog opens its own.
                        QTimer.singleShot(0, lambda: _delete_image_paths_after_move(_p, log_fn))
                        _explorer_clipboard_clear()
                image_get_paths_to_local(img_items, dest_dir, refresh_fn=refresh_fn,
                                         on_complete=_after if is_cut else None)

        def _explorer_paste_into_image(target_dir):
            """Paste into the SD-card image at *target_dir*: OS-clipboard files,
            a local->image upload, or an image->image copy. In 'cut' mode the source
            is removed after a successful copy, turning the paste into a move."""
            kind, data, mode = _explorer_effective_clip()
            if kind is None:
                return
            is_cut = (mode == "cut")

            if kind == "os":
                src_paths = list(data)
                def _after(success, _p=src_paths):
                    if success and is_cut:
                        _delete_local_paths_after_move(_p, add_main_log_window)
                        local_explorer_refresh()
                        nextsync_refresh_explorer()
                        try:
                            QGuiApplication.clipboard().clear()
                        except Exception:
                            pass
                image_upload_external_paths(data, target_dir,
                                            on_complete=_after if is_cut else None)
            elif data["source"] == "local":
                paths = [p for (p, _d) in data["items"]]
                def _after(success, _p=paths):
                    if success and is_cut:
                        _delete_local_paths_after_move(_p, add_main_log_window)
                        local_explorer_refresh()
                        nextsync_refresh_explorer()
                image_upload_external_paths(paths, target_dir,
                                            on_complete=_after if is_cut else None)
            else:
                img_items = list(data["items"])
                img_paths = [p for (p, _d) in img_items]
                target = (target_dir or "/").rstrip("/") or "/"
                # Cutting into the source's own image folder is a no-op move.
                if is_cut and img_paths and all(
                        (p.rstrip("/").rsplit("/", 1)[0] or "/") == target for p in img_paths):
                    add_main_log_window("Nothing to move: items are already in this folder.")
                    _explorer_clipboard_clear()
                    return
                def _after(success, _p=img_paths):
                    if success and is_cut:
                        # Defer so the copy dialog's event loop fully unwinds
                        # before the delete dialog opens its own.
                        QTimer.singleShot(0, lambda: _delete_image_paths_after_move(_p, add_main_log_window))
                        _explorer_clipboard_clear()
                image_copy_items_within(img_items, target_dir,
                                        on_complete=_after if is_cut else None)

        def _local_explorer_copy_selection(mode="copy"):
            """Copy (or, when mode='cut', cut) the SD-card local tree's selection to
            the shared clipboard."""
            items = []
            for ix in self.treeview.selectionModel().selectedRows(0):
                src = self.proxy_model.mapToSource(ix)
                if self.model.fileName(src) == "..":
                    continue
                items.append((self.model.filePath(src), self.model.isDir(src)))
            if not items:
                cur = self.treeview.currentIndex()
                if cur.isValid():
                    src = self.proxy_model.mapToSource(cur)
                    if self.model.fileName(src) != "..":
                        items.append((self.model.filePath(src), self.model.isDir(src)))
            _explorer_clipboard_set("local", items, add_main_log_window, mode=mode)

        def _local_explorer_paste_target_dir():
            cur = self.treeview.currentIndex()
            if cur.isValid():
                src = self.proxy_model.mapToSource(cur)
                if self.model.fileName(src) != "..":
                    path = self.model.filePath(src)
                    return path if self.model.isDir(src) else os.path.dirname(path)
            return local_current_view_dir()

        def _nextsync_explorer_copy_selection(mode="copy"):
            """Copy (or, when mode='cut', cut) the NextSync local tree's selection to
            the shared clipboard."""
            items = []
            for ix in self.nextsync_treeview.selectionModel().selectedRows(0):
                src = self.nextsync_model.mapToSource(ix)
                if self.nextsync_filesystem_model.fileName(src) == "..":
                    continue
                items.append((self.nextsync_filesystem_model.filePath(src),
                              self.nextsync_filesystem_model.isDir(src)))
            if not items:
                cur = self.nextsync_treeview.currentIndex()
                if cur.isValid():
                    src = self.nextsync_model.mapToSource(cur)
                    if self.nextsync_filesystem_model.fileName(src) != "..":
                        items.append((self.nextsync_filesystem_model.filePath(src),
                                      self.nextsync_filesystem_model.isDir(src)))
            _explorer_clipboard_set("local", items, add_nextsync_log_window, mode=mode)

        def _nextsync_explorer_paste_target_dir():
            cur = self.nextsync_treeview.currentIndex()
            if cur.isValid():
                src = self.nextsync_model.mapToSource(cur)
                if self.nextsync_filesystem_model.fileName(src) != "..":
                    path = self.nextsync_filesystem_model.filePath(src)
                    return path if self.nextsync_filesystem_model.isDir(src) else os.path.dirname(path)
            return nextsync_current_view_dir()

        def _image_explorer_copy_selection(mode="copy"):
            """Copy (or, when mode='cut', cut) the SD-card image tree's selection to
            the shared clipboard."""
            items = list(self.image_selected_paths)
            if not items and self.image_selected_path:
                items = [(self.image_selected_path, self.image_selected_is_dir)]
            _explorer_clipboard_set("image", items, add_main_log_window, mode=mode)


        def _check_access_denied_is_full_disk(image_path):
            """If hdfmonkey returns Access denied, check whether it is a full volume.
            Returns an error string if full, None otherwise."""
            err = _check_image_writable(image_path, check_free_space=True)
            if err and "volume is full" in err:
                return (
                    "The image volume is full — no space left to write.\n"
                    "Delete files from the image to free space, or switch to a larger image file.\n"
                    "Larger SD card images (.img) can be downloaded from https://zxnext.uk/hosted/"
                )
            return None

        def image_upload_external_paths(paths, target_dir, on_complete=None):
            """Copy local files/folders (e.g. dropped from Windows Explorer) into
            the loaded disk image under *target_dir*. *on_complete*, when given, is
            called with a single bool (True only if the upload finished without error
            or cancellation) so cut+paste can remove the local source after."""
            global right_disk_image_explorer_content

            if not right_disk_image_explorer_content:
                logging.warning("Please load an image file first !")
                add_main_log_window("Please load an image first!")
                return

            img_err = _check_image_writable(self.right_disk_image_path)
            if img_err:
                logging.error(img_err)
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return

            if self.settings_warn_image_nearly_full_checkbox.isChecked():
                _warn_if_image_nearly_full(self.right_disk_image_path)

            dest_dir = (target_dir or "/").rstrip("/")
            items = []
            for p in paths:
                base = os.path.basename(p.rstrip("/\\"))
                if not base:
                    continue
                dest = (dest_dir + "/" + base).replace("//", "/")
                items.append((p, dest))
            if not items:
                return

            set_all_buttons_disabled()

            image_path = self.right_disk_image_path
            reload_dir = dest_dir or "/"

            dlg    = HdfProgressDialog("Uploading to image…", self)
            worker = HdfTaskWorker(_run_put_external_task, execute_hdf_monkey,
                                   _check_access_denied_is_full_disk, image_path, items)
            outcome = {"error": False, "cancelled": False}

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.error.connect(lambda *_: outcome.update(error=True))
            worker.signals.cancelled.connect(dlg.mark_cancelled)
            worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

            def _on_external_put_finished():
                dlg.close()
                image_reload_dir(reload_dir)
                set_all_buttons_enabled()
                if on_complete:
                    on_complete(not outcome["error"] and not outcome["cancelled"])

            worker.signals.finished.connect(_on_external_put_finished)
            self.threadpool.start(worker)
            dlg.exec()

        def transfert_content_from_disk_to_image():

            global right_disk_image_explorer_content

            if not right_disk_image_explorer_content:
                logging.warning("Please load an image file first !")
                add_main_log_window("Please load an image first!")
                return

            img_err = _check_image_writable(self.right_disk_image_path)
            if img_err:
                logging.error(img_err)
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return

            if self.settings_warn_image_nearly_full_checkbox.isChecked():
                _warn_if_image_nearly_full(self.right_disk_image_path)

            set_all_buttons_disabled()

            dest_file_path = (generate_disk_file_path() + "/" + self.left_file_explorer_selection_file_name).replace('//', '/')

            upload_path = self.left_file_explorer_selection_full_filename_path
            if platform.system() == "Windows":
                upload_path = upload_path.replace("/", "\\")

            image_path      = self.right_disk_image_path
            sel_path        = self.left_file_explorer_selection_full_filename_path
            disk_path_fn    = generate_disk_file_path

            dlg    = HdfProgressDialog("Uploading to image\u2026", self)
            worker = HdfTaskWorker(_run_put_task, execute_hdf_monkey,
                                   _check_access_denied_is_full_disk,
                                   image_path, upload_path, dest_file_path)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_put_finished():
                dlg.close()
                display_path = sel_path
                if not os.path.isdir(display_path):
                    display_path = os.path.dirname(display_path.rstrip("/\\")).replace("\\", "/") + "/"
                self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(display_path, 0)))
                set_treeview_properties()
                self.treeview.show()
                local_sync_path_box()
                # Refresh the image tree asynchronously (the listing runs on a
                # worker thread, so finishing an upload never blocks the UI).
                update_disk_manager_widget_table()
                set_all_buttons_enabled()

            worker.signals.finished.connect(_on_put_finished)
            self.threadpool.start(worker)
            dlg.exec()


        # ---- SD Card image explorer delegation (strangler seam) -------------
        # The image tree's model/population/navigation layer lives in
        # zxnu_sdcard_explorer.SdCardExplorerPane; these wrappers keep the
        # historical names alive for the operation layer around them.
        def image_dest_dir():
            return self.sdcard_explorer.image_dest_dir()

        def generate_disk_file_path():
            # Kept under the original name so every existing call site works:
            # the directory that uploads / new folders / gallery sends target.
            return self.sdcard_explorer.image_dest_dir()

        def image_update_path_label():
            self.sdcard_explorer.image_update_path_label()

        def image_clear_model():
            self.sdcard_explorer.image_clear_model()

        def image_parse_ls(ls_stdout):
            return SdCardExplorerPane.image_parse_ls(ls_stdout)

        def image_make_row(name, is_dir, size_value, full_path):
            return self.sdcard_explorer.image_make_row(name, is_dir, size_value, full_path)

        def image_populate_item(parent_name_item, dir_path, on_done=None):
            self.sdcard_explorer.image_populate_item(parent_name_item, dir_path, on_done)

        def image_load_root(on_done=None):
            self.sdcard_explorer.image_load_root(on_done)

        def image_find_item(path):
            return self.sdcard_explorer.image_find_item(path)

        def image_reload_dir(path):
            self.sdcard_explorer.image_reload_dir(path)

        def image_navigate_to_path(path):
            self.sdcard_explorer.image_navigate_to_path(path)

        def apply_image_filter():
            self.sdcard_explorer.apply_image_filter()

        def set_table_image_properties():
            self.sdcard_explorer.set_table_image_properties()

        def update_disk_manager_widget_table(command_execution_content=None):
            # Refresh entry point kept under its original name. Callers invoke
            # this right after operating on the current target directory, so it
            # simply reloads that directory's node in the tree. The raw ls output
            # argument is no longer needed (the tree re-lists itself).
            image_reload_dir(image_dest_dir())

        # ── Remote Zip / Unzip on the SD-card image (hdfmonkey-staged) ──────
        # hdfmonkey cannot zip, so both actions stage through the PC exactly
        # like the Remote Explorer's Next-side versions: download (progress +
        # Cancel) -> zip/unzip locally (per-file progress + Cancel) -> upload
        # back into the image. Chained stages are deferred with a 0-timer so
        # each modal transfer dialog fully unwinds before the next opens.
        def _image_remote_unzip(zip_path):
            """'Remote Unzip file' on a .zip inside the image: get it to a
            temp dir, extract on the PC, upload the tree back into the zip's
            image folder. The image is only touched by the final upload."""
            if not right_disk_image_explorer_content:
                return
            img_err = _check_image_writable(self.right_disk_image_path)
            if img_err:
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return
            name = zip_path.rstrip("/").rsplit("/", 1)[-1]
            dest_dir = zip_path.rstrip("/").rsplit("/", 1)[0] or "/"
            tmp = tempfile.mkdtemp(prefix="zxnu_imgunzip_")

            def _go(success):
                local_zip = os.path.join(tmp, name)
                if not success or not os.path.isfile(local_zip):
                    shutil.rmtree(tmp, ignore_errors=True)
                    add_main_log_window("Remote unzip: download from the image "
                                        "failed or was cancelled — the image "
                                        "is unchanged.")
                    return
                extract_dir = os.path.join(tmp, "_extracted")
                os.makedirs(extract_dir, exist_ok=True)
                res = zip_extract_with_dialog(self, local_zip, extract_dir,
                                              log=add_main_log_window)
                if not res["ok"] or res["files"] == 0:
                    shutil.rmtree(tmp, ignore_errors=True)
                    if res["cancelled"]:
                        add_main_log_window("Remote unzip cancelled — the "
                                            "image is unchanged.")
                    elif res["error"]:
                        add_main_log_window(f"ERROR: could not extract {name}: "
                                            f"{res['error']}")
                        QMessageBox.critical(
                            self, "Remote unzip failed",
                            f"Could not extract {name}:\n{res['error']}")
                    else:
                        add_main_log_window(f"{name} contains no extractable "
                                            "files.")
                    return
                tops = [os.path.join(extract_dir, e)
                        for e in sorted(os.listdir(extract_dir))]

                def _done(up_ok):
                    shutil.rmtree(tmp, ignore_errors=True)
                    if up_ok:
                        skipped = res["skipped"]
                        extra = (f" ({skipped} unsafe "
                                 f"{'entry' if skipped == 1 else 'entries'} "
                                 "skipped)" if skipped else "")
                        add_main_log_window(
                            f"Extracted {res['files']} file(s) from {name} "
                            f"into {dest_dir} on the image.{extra}")
                    else:
                        add_main_log_window("Remote unzip: upload into the "
                                            "image failed or was cancelled.")
                image_upload_external_paths(tops, dest_dir, on_complete=_done)

            add_main_log_window(f"Remote unzip: fetching {zip_path} from "
                                "the image …")
            image_get_paths_to_local(
                [(zip_path, False)], tmp, refresh_fn=lambda: None,
                on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

        def _image_remote_zip(items):
            """'Remote Zip' on the image selection: get the items to a temp
            dir, zip them on the PC, upload <first item>.zip back into the
            first item's image folder (name uniquified via hdfmonkey ls)."""
            if not right_disk_image_explorer_content or not items:
                return
            img_err = _check_image_writable(self.right_disk_image_path)
            if img_err:
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return
            first = items[0][0].rstrip("/").rsplit("/", 1)[-1] or "archive"
            dest_dir = items[0][0].rstrip("/").rsplit("/", 1)[0] or "/"
            taken = set()
            res_ls = execute_hdf_monkey("ls", self.right_disk_image_path,
                                        extra_argv=[dest_dir], silent=True)
            if res_ls.returncode == 0:
                taken = {n.lower() for n, _d, _s in image_parse_ls(res_ls.stdout)}
            zip_name = zip_unique_name(first, taken)
            tmp = tempfile.mkdtemp(prefix="zxnu_imgzip_")
            dl = os.path.join(tmp, "dl")
            os.makedirs(dl, exist_ok=True)

            def _go(success):
                if not success:
                    shutil.rmtree(tmp, ignore_errors=True)
                    add_main_log_window("Remote zip: download from the image "
                                        "failed or was cancelled — no zip was "
                                        "created.")
                    return
                src_paths = [os.path.join(dl, e) for e in sorted(os.listdir(dl))]
                zip_local = os.path.join(tmp, zip_name)
                res = zip_create_with_dialog(self, src_paths, zip_local,
                                             log=add_main_log_window)
                if not res["ok"]:
                    shutil.rmtree(tmp, ignore_errors=True)
                    if res["cancelled"]:
                        add_main_log_window("Remote zip cancelled — no zip "
                                            "was created.")
                    else:
                        add_main_log_window(f"ERROR: could not build "
                                            f"{zip_name}: {res['error']}")
                    return
                size = os.path.getsize(zip_local)

                def _done(up_ok):
                    shutil.rmtree(tmp, ignore_errors=True)
                    if up_ok:
                        add_main_log_window(
                            f"Created {zip_name} in {dest_dir} on the image "
                            f"({res['files']} file(s), {size:,} bytes).")
                    else:
                        add_main_log_window("Remote zip: upload into the "
                                            "image failed or was cancelled.")
                image_upload_external_paths([zip_local], dest_dir,
                                            on_complete=_done)

            add_main_log_window(f"Remote zip: fetching {len(items)} item(s) "
                                "from the image …")
            image_get_paths_to_local(
                items, dl, refresh_fn=lambda: None,
                on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

        def image_tree_context_menu(pos):
            # Right-click menu on the image explorer tree, mirroring the
            # "New Folder" and "Delete Files or Folder" buttons below it.
            if not right_disk_image_explorer_content:
                return

            index = self.image_treeview.indexAt(pos)
            menu = QMenu(self.image_treeview)

            if index.isValid():
                # Select the right-clicked row so the selection-driven New
                # Folder / Delete handlers act on it — but only when it isn't
                # already part of an existing multi-selection, so right-clicking
                # one of several selected rows keeps them all selected for delete.
                if not self.image_treeview.selectionModel().isSelected(index):
                    self.image_treeview.setCurrentIndex(index)
                name_item = self.image_model.itemFromIndex(index.siblingAtColumn(0))
                is_dir = bool(name_item.data(IMG_ISDIR_ROLE)) if name_item is not None else False

                selected_count = len(self.image_selected_paths)
                new_folder_label = "New Folder Here…" if is_dir else "New Folder…"
                menu.addAction(new_folder_label, image_newfolder_dialog)
                menu.addSeparator()
                copy_label = f"Copy {selected_count} items" if selected_count > 1 else "Copy"
                menu.addAction(copy_label, lambda: _image_explorer_copy_selection())
                cut_label = f"Cut {selected_count} items" if selected_count > 1 else "Cut"
                menu.addAction(cut_label, lambda: _image_explorer_copy_selection(mode="cut"))
                action_paste = menu.addAction("Paste")
                action_paste.setEnabled(_explorer_clipboard_has_items())
                action_paste.triggered.connect(
                    lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_image(image_dest_dir())))
                menu.addSeparator()
                # Rename acts on a single entry; only offer it for a lone selection.
                if selected_count <= 1:
                    menu.addAction("Rename…",
                                   lambda: QTimer.singleShot(0, image_rename_dialog))
                delete_label = f"Delete {selected_count} items" if selected_count > 1 else "Delete"
                menu.addAction(delete_label, delete_files_button_show_confirmation_buttons)
                # Remote Zip/Unzip (PC-staged via hdfmonkey): "Remote Unzip
                # file" only on a lone .zip file, "Remote Zip" on any selection.
                item_path = (name_item.data(IMG_PATH_ROLE) or "") \
                    if name_item is not None else ""
                menu.addSeparator()
                if (selected_count <= 1 and not is_dir
                        and item_path.lower().endswith(".zip")):
                    menu.addAction(
                        "Remote Unzip file",
                        lambda p=item_path: QTimer.singleShot(
                            0, lambda: _image_remote_unzip(p)))
                sel_items = (list(self.image_selected_paths)
                             or ([(item_path, is_dir)] if item_path else []))
                if sel_items:
                    zip_label = (f"Remote Zip {selected_count} items"
                                 if selected_count > 1 else "Remote Zip")
                    menu.addAction(
                        zip_label,
                        lambda it=sel_items: QTimer.singleShot(
                            0, lambda: _image_remote_zip(it)))
            else:
                # Empty area: clear the selection so a new folder lands at the root.
                self.image_treeview.clearSelection()
                menu.addAction("New Folder…", image_newfolder_dialog)
                action_paste = menu.addAction("Paste")
                action_paste.setEnabled(_explorer_clipboard_has_items())
                action_paste.triggered.connect(
                    lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_image(image_dest_dir())))

            menu.exec(self.image_treeview.viewport().mapToGlobal(pos))


        def nextsync_warnings():
            add_nextsync_log_window ("")

            selected_nextsync_explorer_sync_root_directory = ""

            if self.left_file_nextsync_explorer_selection_full_filename_path:
                splitted_filepath = self.left_file_nextsync_explorer_selection_full_filename_path.split('/')
                if not os.path.isdir(self.left_file_nextsync_explorer_selection_full_filename_path):
                # if '.' in dest_file_content:
                    for file_dest_token in range (0, len(splitted_filepath)-1):
                        selected_nextsync_explorer_sync_root_directory += splitted_filepath[file_dest_token] + "/"
                else:
                    selected_nextsync_explorer_sync_root_directory = self.left_file_nextsync_explorer_selection_full_filename_path + "/"

            add_nextsync_log_window ("Using " + selected_nextsync_explorer_sync_root_directory + " as sync root")

            if not os.path.isfile(selected_nextsync_explorer_sync_root_directory + IGNOREFILE):
                add_nextsync_log_window ("Warning! Ignore file " + IGNOREFILE + " not found in directory. All files will be synced, possibly including this file.")
            if not os.path.isfile(selected_nextsync_explorer_sync_root_directory + SYNCPOINT):
                add_nextsync_log_window ("Sync point file " + SYNCPOINT + " not found, syncing all files regardless of timestamp.")

            # Show the Start button straight away; the (potentially expensive)
            # recursive file scan runs on a worker thread and logs the
            # "Ready to sync N files" line when it finishes, so the UI never
            # blocks even on very large folders.
            nextsync_show_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(False)

            if not (selected_nextsync_explorer_sync_root_directory and os.path.isdir(selected_nextsync_explorer_sync_root_directory)):
                add_nextsync_log_window ("")
                add_nextsync_log_window ("Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.")
                add_nextsync_log_window ("")
                return

            # Bump the generation token so a scan whose sync root was changed
            # before it finished is discarded instead of logging a stale count.
            self._nextsync_scan_generation += 1
            scan_gen = self._nextsync_scan_generation
            root_dir = selected_nextsync_explorer_sync_root_directory
            scan_result = {}

            _always_sync = self.nextsync_alwayssync_checkbox.isChecked()

            def _scan_fn(signals, cancel_event, _root=root_dir, _holder=scan_result,
                         _always=_always_sync):
                files = getFileList(_root, _always)
                _holder["count"] = len(files)
                _holder["total"] = sum(x[1] for x in files)

            scan_worker = HdfTaskWorker(_scan_fn)

            def _on_scan_done(_gen=scan_gen, _holder=scan_result):
                # Release our keep-alive reference now that the slot is running.
                self._nextsync_scan_workers.discard(scan_worker)
                # Discard results from a superseded scan (sync root changed since).
                if _gen != self._nextsync_scan_generation:
                    return
                if "count" not in _holder:
                    return   # scan raised — nothing reliable to report
                count = _holder["count"]
                total = _holder["total"]
                if count < 10 and total < 100000:
                    severity = "Note"
                elif count < 100 and total < 1000000:
                    severity = "Warning"
                else:
                    severity = "WARNING"
                add_nextsync_log_window (f"{severity}: Ready to sync {count} files, {total/1024:.2f} kilobytes.")
                add_nextsync_log_window ("")

            # Keep the worker (and its signals) alive until _on_scan_done runs,
            # so the queued cross-thread `finished` slot can't be dropped by GC.
            self._nextsync_scan_workers.add(scan_worker)
            scan_worker.signals.finished.connect(_on_scan_done)
            self.threadpool.start(scan_worker)

        def nextsync_show_ip_info():
            add_nextsync_log_window ("------------------------------------------", False)
            add_nextsync_log_window ("NextSync server, protocol version: " + VERSION, False)
            add_nextsync_log_window ("", False)
            hostinfo = socket.gethostbyname_ex(socket.gethostname())
            add_nextsync_log_window ("Running on host:\n    " + str(hostinfo[0]) , False)
            if hostinfo[1] != []:
                add_nextsync_log_window ("Aliases:", False)
                for x in hostinfo[1]:
                    add_nextsync_log_window ("    " + str(x), False)
            if hostinfo[2] != []:
                add_nextsync_log_window ("IP addresses:", False)
                for x in hostinfo[2]:
                    add_nextsync_log_window ("    " + str(x), False)

            # If we're unsure of the ip, try getting it via internet connection
            if len(hostinfo[2]) > 1 or "127" in hostinfo[2][0]:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80)) # ping google dns
                    add_nextsync_log_window ("Primary IP:\n    " + str(s.getsockname()[0]), False)

        def nextsync_cancel_server_job():
            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)
            save_configuration_file()

        def _on_nextsync_conflict_prompt(name, path, holder, ev):
            """UI-thread slot: ask the user how to handle a received file/dir that
            already exists locally. Records one of overwrite/overwrite_all/
            ignore/ignore_all in *holder* and unblocks the worker via *ev*."""
            try:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Question)
                box.setWindowTitle("File or directory exists")
                box.setText("File or directory already exists locally.")
                box.setInformativeText(
                    f"{path}\n\n"
                    "Tip: set a default for this in Settings → "
                    "\"NextSync — when a sent file or directory exists locally\".")
                b_ow_one = box.addButton("Overwrite local file (one time)", QMessageBox.AcceptRole)
                b_ow_all = box.addButton("Overwrite local file (always in this sync)", QMessageBox.AcceptRole)
                b_ig_one = box.addButton("Ignore (one time)", QMessageBox.RejectRole)
                b_ig_all = box.addButton("Ignore (always in this sync)", QMessageBox.RejectRole)
                box.exec()
                clicked = box.clickedButton()
                if clicked is b_ow_one:
                    holder["result"] = "overwrite"
                elif clicked is b_ow_all:
                    holder["result"] = "overwrite_all"
                elif clicked is b_ig_all:
                    holder["result"] = "ignore_all"
                else:
                    holder["result"] = "ignore"   # Ignore (one time) or dialog closed
            finally:
                ev.set()

        # Created on the UI thread so its queued signal delivers the prompt there.
        self._nextsync_conflict_signals = NextSyncConflictSignals()
        self._nextsync_conflict_signals.prompt.connect(_on_nextsync_conflict_prompt)

        def _nextsync_ask_conflict(name, path):
            """Called from the receive worker thread: block until the user picks
            how to handle an existing local file/dir. Returns one of
            'overwrite', 'overwrite_all', 'ignore', 'ignore_all'."""
            holder = {}
            ev = threading.Event()
            self._nextsync_conflict_signals.prompt.emit(name, path, holder, ev)
            ev.wait()
            return holder.get("result", "ignore")

        def nextsync_do_server_job(progress_callback, status_callback=None, cancel_flag=None, serve_folder=None):
            """Adapter for the classic sync server, which now lives in
            zxnu_workers.run_classic_sync_server: computes the sync root
            (incl. the "Send via NextSync" serve_folder one-shots), drives the
            pane-side UI state around the run, and injects the Settings reads
            and prompts the protocol loop needs. Kept under the historical
            name/signature for the Worker(...) launch sites."""
            selected_root = ""
            force_sync_once = False
            # Only touch the pane progress bar when running from the pane (no
            # cancel_flag = pane invocation), exactly as before.
            if cancel_flag is None:
                self.nextsync_progressbar.setValue(0)
                self.nextsync_progressbar.setVisible(True)
                self.nextsync_button_create_syncignore.setVisible(False)
                self.nextsync_button_delete_syncignore.setVisible(False)
                self.nextsync_button_delete_syncpointfile.setVisible(False)
            nextsync_show_ip_info()
            if serve_folder and os.path.isdir(serve_folder):
                # Caller specified an exact folder (e.g. downloads/comix): the
                # "Send via NextSync" buttons mean exactly one transfer.
                selected_root = serve_folder.rstrip("/\\") + "/"
                force_sync_once = True
            elif self.left_file_nextsync_explorer_selection_full_filename_path:
                splitted_filepath = self.left_file_nextsync_explorer_selection_full_filename_path.split('/')
                if not os.path.isdir(self.left_file_nextsync_explorer_selection_full_filename_path):
                    for file_dest_token in range(0, len(splitted_filepath) - 1):
                        selected_root += splitted_filepath[file_dest_token] + "/"
                else:
                    selected_root = self.left_file_nextsync_explorer_selection_full_filename_path + "/"

            def _conflict_policy():
                pol = configuration_dictionary.get(
                    SETTING_NEXTSYNC_SEND_CONFLICT, DEFAULT_NEXTSYNC_SEND_CONFLICT)
                return pol if pol in ("prompt", "overwrite", "ignore") else DEFAULT_NEXTSYNC_SEND_CONFLICT

            # Payload size honours the persisted "slow transfer" setting. (The
            # old in-place MAX_PAYLOAD rebind in the checkbox handler never
            # left that function, so the toggle had no runtime effect — fixed
            # by deriving the size here, per run.)
            _slow = configuration_dictionary.get(
                SETTING_NEXTSYNC_SLOWTRANSFER, "").strip().lower() in ("true", "1")

            run_classic_sync_server(
                selected_root,
                add_nextsync_log_window,
                progress_callback=progress_callback,
                status_callback=status_callback,
                cancel_flag=cancel_flag,
                force_sync_once=force_sync_once,
                sync_once=lambda: self.nextsync_synconce_checkbox.isChecked(),
                always_sync=lambda: self.nextsync_alwayssync_checkbox.isChecked(),
                get_conflict_policy=_conflict_policy,
                ask_conflict=_nextsync_ask_conflict,
                max_payload=256 if _slow else 1024,
                verbose=ZX_NEXT_UNITE_VERBOSE_LOG_MODE,
                set_session_active=lambda on: setattr(self, "_nextsync_transfer_active", on),
                pane_progress=(None if cancel_flag is not None
                               else self.nextsync_progressbar.setValue),
            )

            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)
            if cancel_flag is None:
                self.nextsync_progressbar.setVisible(False)

        def list_windows_drives():
            """Return a list of drive letters on Windows."""
            drives = []
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(f"{letter}:\\")
                bitmask >>= 1
            return drives

        # ------------------------------------------
        # main program starts here
        # ------------------------------------------

        # NextSync specific variables
        # If you want to be really safe (but transfer slower), use this:
        #MAX_PAYLOAD = 256

        # The next uart has a buffer of 512 bytes; sending packets of 256 bytes will always
        # fit and there won't be any buffer overruns. However, it's much slower.

        #  Build Main UI

        self.setWindowTitle("zx-next-unite " + ZX_NEXT_UNITE_VERSION)
        self.setMinimumSize(QSize(ZX_NEXT_UNITE_UI_WIDTH, ZX_NEXT_UNITE_UI_HEIGTH))

        # Initialize configuration dictonnary
        for c in CONFIG_FILE_SETTINGS:
            configuration_dictionary[c] = ""

        # CSpect ESC-key disable defaults to On ("-esc"): seed index 1 after the
        # reset above so a cfg that predates this option (no "esc=" line) still
        # restores as On. A cfg that carries an explicit "esc=" value overrides
        # this when read in load_configuration_file.
        configuration_dictionary[SETTING_ESC] = "1"
        # MAME ESC-key disable likewise defaults to On ("-confirm_quit"); seed
        # index 1 so a cfg without a "mame_esc" line restores as On, while an
        # explicit saved value still wins in load_configuration_file.
        configuration_dictionary[SETTING_MAME_ESC] = "1"

        def _persist_retro(key, checked):
            """Persist a pane's Classic/Retro item-viewer choice to the config
            file. Skips the file write while restoring saved settings (the value
            is already loaded) and is a no-op during __init__ anyway via
            save_configuration_file's _initialising guard."""
            try:
                configuration_dictionary[key] = "true" if checked else "false"
                if not getattr(self, "_retro_restoring", False):
                    save_configuration_file()
            except Exception:
                pass

        # Pre-populate color defaults so save works correctly before first load
        configuration_dictionary[SETTING_COLOR_UP_DIRECTORY] = DEFAULT_COLOR_UP_DIRECTORY
        configuration_dictionary[SETTING_COLOR_DIR_NAME]     = DEFAULT_COLOR_DIR_NAME
        configuration_dictionary[SETTING_COLOR_DIR_TYPE]     = DEFAULT_COLOR_DIR_TYPE
        configuration_dictionary[SETTING_COLOR_FILE_NAME]    = DEFAULT_COLOR_FILE_NAME
        configuration_dictionary[SETTING_COLOR_FILE_EXT]     = DEFAULT_COLOR_FILE_EXT
        configuration_dictionary[SETTING_COLOR_FILE_SIZE]    = DEFAULT_COLOR_FILE_SIZE
        configuration_dictionary[SETTING_COLOR_GENERAL_TEXT] = DEFAULT_COLOR_GENERAL_TEXT
        configuration_dictionary[SETTING_COLOR_RETRO_LOG]    = DEFAULT_COLOR_RETRO_LOG
        configuration_dictionary[SETTING_DESKTOP_THEME]      = DEFAULT_DESKTOP_THEME
        # UI language: seeded EMPTY, the repo's "never saved" convention (see
        # _apply_first_run_pygame_defaults) — a blank value at the end of
        # __init__ triggers the one-time OS-language adoption. Must be seeded
        # regardless: save_configuration_file writes every
        # CONFIG_FILE_SETTINGS key and would KeyError otherwise.
        configuration_dictionary[SETTING_UI_LANGUAGE]        = ""

        # Init UI forms

        self.setWindowIcon(QIcon(ZX_NEXT_UNITE_ICON_IMAGE_FILE))


        self.zx_next_unite_form = QFormLayout()
        self.nextsync_form = QFormLayout()

        # zx_next_unite horizontals
        self.horizontal1 = QHBoxLayout()
        self.horizontal2 = QHBoxLayout()
        # horizontal3 (explorers) and horizontal4 (Path) are now the
        # sdcard_explorer_grid built further below; horizontal5 (the log row)
        # became the bottom pane of the explorers ⇄ log splitter.
        # (horizontal6 replaced by the MAME / CSpect QGroupBox rows built below.)

        # nextsync horizontals

        self.horizontal10 = QHBoxLayout()
        self.horizontal11 = QHBoxLayout()
        self.horizontal12 = QHBoxLayout()
        self.horizontal13 = QHBoxLayout()
        self.horizontal14 = QHBoxLayout()
        self.horizontal15 = QHBoxLayout()
        self.horizontal16 = QHBoxLayout()


        self.imageinput = QComboBox()
        self.imageinput.setEditable(True)
        self.imageinput.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.imageinput.setToolTip(
            "Path to the SD card image (.img / .hdf).\n"
            "Type a path directly, click the arrow to pick from recently loaded images,\n"
            "or use the 'Select NextZXOS disk Image' button to browse."
        )
        self.imageinput.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.imageinput.lineEdit().setPlaceholderText("SD card image path...")
        # Pressing Enter in the editable field triggers a load attempt
        self.imageinput.lineEdit().returnPressed.connect(lambda: load_image())
        # Selecting an item from the history dropdown loads it immediately
        self.imageinput.activated.connect(lambda _index: load_image())
        self.selectimage = QPushButton("ToDisk", self)
        self.selectimage.setText("Select NextZXOS disk Image")
        # (was `self.selectimage.toolTip = "..."` — a plain attribute that
        # SHADOWED the Qt method with a str, so the tooltip never showed and
        # every later toolTip() call on this button raised TypeError.)
        self.selectimage.setToolTip("Select a disk image to be loaded.")
        self.selectimage.clicked.connect(select_image)

        self.downloadimage = QPushButton("Download NextZXOS Image", self)
        self.downloadimage.setToolTip(
            "Download a ready-to-use NextZXOS SD card image from zxnext.uk,\n"
            "save it to disk, and load it automatically."
        )
        self.downloadimage.clicked.connect(download_nextzxos_image)

        self.horizontal1.addWidget(self.imageinput)
        self.horizontal1.addWidget(self.selectimage)
        self.horizontal1.addWidget(self.downloadimage)

        self.zx_next_unite_form.addRow(self.horizontal1)

        self.zx_next_unite_diskdrive = QComboBox()

        available_drives = []

        if platform.system() == "Windows":

            available_drives = list_windows_drives()

            for letter in available_drives:
                 self.zx_next_unite_diskdrive.addItem(letter)

            self.zx_next_unite_diskdrive.show()

            self.horizontal2.addWidget(self.zx_next_unite_diskdrive)
            # (activated is connected by SdCardExplorerPane, which owns the tree)
        else:
            available_drives.append('/')
            self.zx_next_unite_diskdrive.setVisible(False)

        self.filterlabel = QLabel()
        self.filterlabel.setText("Search: ")


        self.horizontal2.addWidget(self.filterlabel)

        self.filtertext = QLineEdit()
        self.filtertext.setPlaceholderText("Filter by name...")
        # (textChanged is connected by SdCardExplorerPane, which owns the tree)
        self.filtertext.setMinimumWidth(FILTER_TEXT_WIDTH)
        self.filtertext.setMaximumWidth(FILTER_TEXT_WIDTH)

        self.horizontal2.addWidget(self.filtertext)

        # The "Disk Image Explorer:" label and its path moved into the path
        # row that now sits directly above the explorers (see the
        # sdcard_explorer_grid below); a stretch keeps the image-explorer
        # Filter box on the right-hand side, roughly over that explorer.
        self.horizontal2.addStretch(1)

        self.image_filterlabel = QLabel()
        self.image_filterlabel.setText("  Filter: ")
        self.horizontal2.addWidget(self.image_filterlabel)

        self.image_filtertext = QLineEdit()
        self.image_filtertext.setPlaceholderText("Filter by name, type or size...")
        self.image_filtertext.setToolTip(
            "Filter the disk image explorer rows in real-time.\n"
            "Type any text to show only rows whose Name, Type or Size columns contain that text.\n"
            "Clear the field to show all entries."
        )
        # (textChanged is connected by SdCardExplorerPane, which owns the tree)
        self.image_filtertext.setMinimumWidth(FILTER_TEXT_WIDTH)
        self.image_filtertext.setMaximumWidth(FILTER_TEXT_WIDTH)
        self.horizontal2.addWidget(self.image_filtertext)

        self.zx_next_unite_form.addRow(self.horizontal2)

        # ---- SD Card explorer pane (zxnu_sdcard_explorer) -------------------
        # The explorer pair's widgets and navigation/model layer live in
        # SdCardExplorerPane; the operation layer below (context menus, key
        # handlers, drag & drop, transfers, deletes, the load pipeline) stays
        # here and reaches the pane through these hooks and the historical
        # attribute aliases.
        def _sd_set_image_loaded(entries):
            # image_load_root bookkeeping: the module-global "an image is
            # loaded" guard plus the legacy flat name list (name lookups).
            global right_disk_image_explorer_content
            if entries is None:
                right_disk_image_explorer_content = []
                return
            right_disk_image_explorer_content =                 [(n, "DIR" if d else "") for (n, d, s) in entries] or ["loaded"]
            self.image_explorer_item_list.clear()
            for n, _d, _s in entries:
                self.image_explorer_item_list.addItem(n)

        def _sd_set_selected_names(names):
            global right_disk_image_selected_files
            right_disk_image_selected_files = list(names)

        def _sd_local_nav_side_effects():
            # Legacy behavior of the local double-click: reset the NextSync
            # prepare/start buttons.
            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)

        _sd_hooks = SimpleNamespace(
            get_setting=lambda key: configuration_dictionary.get(key, ""),
            set_setting=lambda key, value: configuration_dictionary.__setitem__(key, value),
            save_config=lambda: save_configuration_file(),
            log=lambda msg: add_main_log_window(msg),
            set_treeview_properties=lambda: set_treeview_properties(),
            execute_hdf_monkey=lambda *a, **kw: execute_hdf_monkey(*a, **kw),
            is_image_loaded=lambda: bool(right_disk_image_explorer_content),
            set_image_loaded=_sd_set_image_loaded,
            update_usage_gauge=lambda pth: _update_image_usage_gauge(pth),
            on_local_navigate_side_effects=_sd_local_nav_side_effects,
            set_selected_names=_sd_set_selected_names,
        )
        self.sdcard_explorer = SdCardExplorerPane(
            self, _sd_hooks,
            self.zx_next_unite_diskdrive if platform.system() == "Windows" else None,
            available_drives[0], self.filtertext, self.image_filtertext)

        # Historical attribute aliases: the operation layer and the offscreen
        # test suite keep addressing the pane's widgets through MainWindow.
        _pane = self.sdcard_explorer
        self.model = _pane.model
        self.proxy_model = _pane.proxy_model
        self.treeview = _pane.treeview
        self.image_model = _pane.image_model
        self.image_treeview = _pane.image_treeview
        self.image_usage_gauge = _pane.image_usage_gauge
        self.image_explorer_container = _pane.image_explorer_container
        self.local_file_explorer_path = _pane.local_file_explorer_path
        self.localexplorerlabel = _pane.localexplorerlabel
        self.local_explorer_up_button = _pane.local_explorer_up_button
        self.local_explorer_refresh_button = _pane.local_explorer_refresh_button
        self.local_path_row_container = _pane.local_path_row_container
        self.image_explorer_up_button = _pane.image_explorer_up_button
        self.image_explorer_refresh_button = _pane.image_explorer_refresh_button
        self.diskimageexplorerlabel = _pane.diskimageexplorerlabel
        self.diskimageexplorerpathinput = _pane.diskimageexplorerpathinput
        self.image_path_row_container = _pane.image_path_row_container
        self.sdcard_explorer_grid = _pane.sdcard_explorer_grid
        self.sdcard_explorer_container = _pane
        self._image_recolor_all = _pane.image_recolor_all

        # Operation-layer wiring onto the pane's local tree: the context menu
        # dispatches into the transfer/delete/rename/zip flows kept here.
        self.treeview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.treeview.customContextMenuRequested.connect(on_treeview_context_menu)

        def _local_tree_key_press(event):
            # Ctrl+C / Ctrl+X / Ctrl+V copy, cut & paste via the shared clipboard.
            if event.matches(QKeySequence.StandardKey.Copy):
                _local_explorer_copy_selection()
                return
            if event.matches(QKeySequence.StandardKey.Cut):
                _local_explorer_copy_selection(mode="cut")
                return
            if event.matches(QKeySequence.StandardKey.Paste):
                _explorer_paste_into_local(_local_explorer_paste_target_dir(),
                                           local_explorer_refresh, add_main_log_window)
                return
            # Delete mirrors the context-menu "Delete": prompt (honouring the
            # "no prompt on deletion" setting), then remove the selection.
            if event.key() == Qt.Key.Key_Delete:
                local_explorer_delete_selection()
                return
            # F2 mirrors the context-menu "Rename" action on the selected entry.
            if event.key() == Qt.Key.Key_F2:
                ix = self.treeview.currentIndex()
                if ix.isValid():
                    source_ix = self.proxy_model.mapToSource(ix)
                    nm = self.model.fileName(source_ix)
                    if nm != "..":
                        local_explorer_rename_item(
                            self.model.filePath(source_ix), nm, self.model.isDir(source_ix))
                        return
            QTreeView.keyPressEvent(self.treeview, event)

        self.treeview.keyPressEvent = _local_tree_key_press

        # --- Drag & drop into / out of the local explorer ---------------------
        # Drops onto the left explorer come from two sources:
        #   * the OS file manager (Windows Explorer, etc.) -> import (copy) the
        #     files/folders into the folder the drop lands on;
        #   * the SD-card image explorer on the right (custom IMAGE_DRAG_MIME) ->
        #     download those image entries into that folder (same as ':<-').
        # In both cases the target folder is the item the drop lands on (its
        # parent if the item is a file), or the current root for empty space.
        # Dragging out of this explorer carries the selected local paths as
        # text/uri-list URLs, which the image explorer accepts as an upload.
        def _local_drop_target_dir(pos):
            index = self.treeview.indexAt(pos)
            if index.isValid():
                source_ix = self.proxy_model.mapToSource(index)
                if self.model.fileName(source_ix) != "..":
                    path = self.model.filePath(source_ix)
                    if self.model.isDir(source_ix):
                        return path
                    return os.path.dirname(path)
            # Fall back to the directory currently shown at the tree root.
            root_src = self.proxy_model.mapToSource(self.treeview.rootIndex())
            return self.model.filePath(root_src)

        def _local_drag_acceptable(event):
            # Reject drags originating from this same explorer (nothing to do),
            # accept image-explorer drags and OS file-manager URL drags.
            if event.source() is self.treeview:
                return False
            md = event.mimeData()
            return md.hasFormat(IMAGE_DRAG_MIME) or md.hasUrls()

        def _local_drag_enter(event):
            if _local_drag_acceptable(event):
                event.acceptProposedAction()
            else:
                event.ignore()

        def _local_drag_move(event):
            if _local_drag_acceptable(event):
                event.acceptProposedAction()
            else:
                event.ignore()

        def _local_drop(event):
            if not _local_drag_acceptable(event):
                event.ignore()
                return
            md = event.mimeData()
            dest_dir = _local_drop_target_dir(event.position().toPoint())

            # Drag out of the image explorer: download the entries into dest_dir.
            if md.hasFormat(IMAGE_DRAG_MIME):
                raw = bytes(md.data(IMAGE_DRAG_MIME)).decode("utf-8", errors="replace")
                image_items = []
                for line in raw.splitlines():
                    if not line:
                        continue
                    tag, _, p = line.partition("\t")
                    if p:
                        image_items.append((p, tag == "D"))
                if not image_items:
                    event.ignore()
                    return
                event.acceptProposedAction()
                image_get_paths_to_local(image_items, dest_dir)
                return

            # OS file-manager drop: import the local files/folders.
            paths = [u.toLocalFile() for u in md.urls()
                     if u.isLocalFile() and u.toLocalFile()]
            if not paths:
                event.ignore()
                return
            event.acceptProposedAction()
            local_explorer_import_external_paths(paths, dest_dir)

        def _local_start_drag(supported_actions):
            # Carry the selected local file/folder paths as text/uri-list URLs so
            # they can be dropped onto the image explorer (equivalent to '->:').
            paths = []
            for ix in self.treeview.selectionModel().selectedRows(0):
                source_ix = self.proxy_model.mapToSource(ix)
                if self.model.fileName(source_ix) == "..":
                    continue
                paths.append(self.model.filePath(source_ix))
            if not paths:
                return
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
            drag = QDrag(self.treeview)
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction)

        self.treeview.setAcceptDrops(True)
        self.treeview.setDragEnabled(True)
        self.treeview.setDragDropMode(QAbstractItemView.DragDrop)
        self.treeview.setDefaultDropAction(Qt.CopyAction)
        self.treeview.setDropIndicatorShown(True)
        self.treeview.dragEnterEvent = _local_drag_enter
        self.treeview.dragMoveEvent = _local_drag_move
        self.treeview.dropEvent = _local_drop
        self.treeview.startDrag = _local_start_drag

        self.centralbuttonscontainer = QWidget()
        self.centralbuttons = QVBoxLayout()

        self.button_to_disk = QPushButton("ToDisk", self)
        self.button_to_disk.setText(":<-")
        self.button_to_disk.setMaximumWidth(DISK_ARROWS_BUTTONS_SIZE)
        self.button_to_disk.clicked.connect(transfert_content_from_image_to_disk)

        self.button_to_image = QPushButton("ToImage", self)
        self.button_to_image.setText("->:")
        self.button_to_image.setMaximumWidth(DISK_ARROWS_BUTTONS_SIZE)
        self.button_to_image.clicked.connect(transfert_content_from_disk_to_image)

        # The image tree is built by SdCardExplorerPane; only the operation-
        # layer context menu is wired here (it dispatches transfers/deletes).
        self.image_treeview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_treeview.customContextMenuRequested.connect(image_tree_context_menu)

        def _image_tree_key_press(event):
            # Ctrl+C / Ctrl+X / Ctrl+V copy, cut & paste via the shared clipboard.
            if event.matches(QKeySequence.StandardKey.Copy):
                _image_explorer_copy_selection()
            elif event.matches(QKeySequence.StandardKey.Cut):
                _image_explorer_copy_selection(mode="cut")
            elif event.matches(QKeySequence.StandardKey.Paste):
                _explorer_paste_into_image(image_dest_dir())
            elif event.key() == Qt.Key.Key_Delete and self.image_selected_path:
                delete_files_button_show_confirmation_buttons()
            elif event.key() == Qt.Key.Key_F2 and self.image_selected_path:
                image_rename_dialog()
            else:
                QTreeView.keyPressEvent(self.image_treeview, event)

        self.image_treeview.keyPressEvent = _image_tree_key_press

        # --- Drag & drop into / out of the disk image --------------------------
        # Dropping files/folders onto the image explorer uploads them into the
        # image (same as '->:'); the source can be the OS file manager or the
        # local explorer on the left (both carry text/uri-list URLs). The target
        # folder is the item the drop lands on (its parent if the item is a file),
        # or the image root when dropped on empty space.
        # Dragging out of the image explorer carries the selected entries as a
        # custom IMAGE_DRAG_MIME payload, which the local explorer accepts as a
        # download (same as ':<-').
        def _image_drop_target_dir(pos):
            index = self.image_treeview.indexAt(pos)
            if not index.isValid():
                return "/"
            name_item = self.image_model.itemFromIndex(index.siblingAtColumn(0))
            if name_item is None:
                return "/"
            path = name_item.data(IMG_PATH_ROLE) or "/"
            if bool(name_item.data(IMG_ISDIR_ROLE)):
                return path or "/"
            parent = path.rstrip("/").rsplit("/", 1)[0]
            return parent or "/"

        def _image_drag_acceptable(event):
            # Ignore drags from the image explorer itself (would be image->image);
            # accept local-explorer / OS URL drags when an image is loaded.
            if event.source() is self.image_treeview:
                return False
            return bool(right_disk_image_explorer_content) and event.mimeData().hasUrls()

        def _image_drag_enter(event):
            if _image_drag_acceptable(event):
                event.acceptProposedAction()
            else:
                event.ignore()

        def _image_drag_move(event):
            if _image_drag_acceptable(event):
                event.acceptProposedAction()
            else:
                event.ignore()

        def _image_drop(event):
            if not _image_drag_acceptable(event):
                event.ignore()
                return
            paths = [u.toLocalFile() for u in event.mimeData().urls()
                     if u.isLocalFile() and u.toLocalFile()]
            if not paths:
                event.ignore()
                return
            event.acceptProposedAction()
            target_dir = _image_drop_target_dir(event.position().toPoint())
            image_upload_external_paths(paths, target_dir)

        def _image_start_drag(supported_actions):
            # Gather the selected image entries into a custom-MIME payload so the
            # local explorer can download them on drop (equivalent to ':<-').
            if not right_disk_image_explorer_content:
                return
            items = []
            for col0 in self.image_treeview.selectionModel().selectedRows(0):
                name_item = self.image_model.itemFromIndex(col0)
                if name_item is None:
                    continue
                path = name_item.data(IMG_PATH_ROLE) or ""
                if not path:
                    continue
                is_dir = bool(name_item.data(IMG_ISDIR_ROLE))
                items.append((path, is_dir))
            if not items:
                return
            payload = "\n".join(f"{'D' if d else 'F'}\t{p}" for p, d in items)
            mime = QMimeData()
            mime.setData(IMAGE_DRAG_MIME, payload.encode("utf-8"))
            drag = QDrag(self.image_treeview)
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction)

        self.image_treeview.setAcceptDrops(True)
        self.image_treeview.setDragEnabled(True)
        self.image_treeview.setDragDropMode(QAbstractItemView.DragDrop)
        self.image_treeview.setDefaultDropAction(Qt.CopyAction)
        self.image_treeview.setDropIndicatorShown(True)
        self.image_treeview.dragEnterEvent = _image_drag_enter
        self.image_treeview.dragMoveEvent = _image_drag_move
        self.image_treeview.dropEvent = _image_drop
        self.image_treeview.startDrag = _image_start_drag

        # The usage gauge, path rows and the explorer grid are owned by the
        # pane. The centre transfer-buttons column is populated here (the
        # buttons are operation-wired) and slotted into the pane's grid.
        self.centralbuttons.addWidget(self.button_to_image)
        self.centralbuttons.addWidget(self.button_to_disk)
        self.centralbuttons.setAlignment(Qt.AlignCenter)
        self.centralbuttonscontainer.setLayout(self.centralbuttons)
        self.sdcard_explorer_grid.addWidget(self.centralbuttonscontainer, 1, 1)

        self.listWidgetLog = QListWidget(self)

        for l in INIT_LOG:
            add_main_log_window(l)

        self.listWidgetHelp = QListWidget(self)

        for l in INIT_HELP:
            add_help_content(l, False)


        # Height is governed by the explorers ⇄ log splitter (built below);
        # only keep a small floor so the log can never be dragged away entirely.
        self.listWidgetLog.setMinimumHeight(60)
        # self.listWidgetLog.setMinimumWidth(410)
        # self.listWidgetLog.setMaximumWidth(410)

        self.imageexplorerbuttonscontainer = QWidget()
        self.imageexplorerbuttons = QHBoxLayout()

        self.hiddenspacelabel1 = QLabel()
        self.hiddenspacelabel1.setText("      ")
        self.imageexplorerbuttons.addWidget(self.hiddenspacelabel1)

        self.button_new_folder = QPushButton("NewFolder", self)
        self.button_new_folder.setText("New Folder")
        self.button_new_folder.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.button_new_folder.clicked.connect(image_newfolder)

        self.button_rename = QPushButton("Rename", self)
        self.button_rename.setText("Rename")
        self.button_rename.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.button_rename.clicked.connect(image_rename_dialog)

        self.download_and_install_hdfmonkey_button = QPushButton("Download & install HDF Monkey", self)
        self.download_and_install_hdfmonkey_button.setText("Download and install HDF Monkey")
        self.download_and_install_hdfmonkey_button.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.download_and_install_hdfmonkey_button.clicked.connect(_on_hdfmonkey_button_clicked)
        self.download_and_install_hdfmonkey_button.setVisible(False)

        self.hiddenspacelabel2 = QLabel()
        self.hiddenspacelabel2.setText("       ")
        self.imageexplorerbuttons.addWidget(self.hiddenspacelabel2)

        self.button_delete_files = QPushButton("DeleteFiles", self)
        self.button_delete_files.setText("Delete")
        self.button_delete_files.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.button_delete_files.clicked.connect(delete_files_button_show_confirmation_buttons)

        self.imageexplorerbuttons.addWidget(self.button_new_folder)
        self.imageexplorerbuttons.addWidget(self.button_rename)
        self.imageexplorerbuttons.addWidget(self.button_delete_files)

        self.imageexplorerbuttons.addWidget(self.download_and_install_hdfmonkey_button)

        self.new_folder_input = QLineEdit()

        self.new_folder_input.setPlaceholderText("New directory name ...")
        tooltip_text = "Enter new directory name ("
        for not_allowed_chars in DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS:
            tooltip_text += not_allowed_chars
        tooltip_text += " are not allowed): "

        self.new_folder_input.setToolTip(tooltip_text)
        self.new_folder_input.setMinimumWidth(150)
        self.new_folder_input.setMaximumWidth(150)
        self.new_folder_input.returnPressed.connect(image_newfolder_create)

        self.button_create_directory = QPushButton("Create Directory", self)
        self.button_create_directory.setText("Create Directory")
        self.button_create_directory.setMinimumWidth(IMAGE_BUTTONS_SIZE/2)
        self.button_create_directory.clicked.connect(image_newfolder_create)

        self.button_create_directory_cancel = QPushButton("Cancel Directory", self)
        self.button_create_directory_cancel.setText("Cancel")
        self.button_create_directory_cancel.setMinimumWidth(IMAGE_BUTTONS_SIZE/2)
        self.button_create_directory_cancel.clicked.connect(image_newfolder_cancel)

        self.imageexplorerbuttons.addWidget(self.new_folder_input)
        self.imageexplorerbuttons.addWidget(self.button_create_directory)
        self.imageexplorerbuttons.addWidget(self.button_create_directory_cancel)

        self.new_folder_input.setVisible(False)
        self.button_create_directory.setVisible(False)
        self.button_create_directory_cancel.setVisible(False)

        self.imageexplorerbuttons.setAlignment(Qt.AlignTop)

        self.imageexplorerbuttonscontainer.setLayout(self.imageexplorerbuttons)

        # Place the New Folder / Delete Files buttons directly beneath the disk
        # image explorer (grid row 2, right column).
        self.sdcard_explorer_grid.addWidget(self.imageexplorerbuttonscontainer, 2, 2)

        # Add Log Window
        # Optional retro 8-bit pygame log for the SD Card tab, mirroring the one on
        # the NextSync tab. Pygame is optional: the toggle disables itself with an
        # install hint when pygame-ce is missing. Page 0 = the classic list log,
        # page 1 = the retro display (built lazily the first time it is switched on,
        # and sharing the NextSync starfield-animation preference).
        self._main_retro_log = None
        self._main_pygame_on = False

        self.main_pygame_button = QPushButton("🎮 Retro")
        self.main_pygame_button.setCheckable(True)
        self.main_pygame_button.setToolTip(
            "Switch the SD Card log window to a retro 8-bit pygame display:\n"
            "an animated starfield with green Consolas text.\n"
            "Requires the optional 'pygame-ce' package.")

        self.main_log_stack = QStackedWidget(self)
        self.main_log_stack.setMinimumHeight(60)
        self.main_log_stack.addWidget(self.listWidgetLog)

        self.main_log_container = QWidget(self)
        _main_log_v = QVBoxLayout(self.main_log_container)
        _main_log_v.setContentsMargins(0, 0, 0, 0)
        _main_log_v.setSpacing(2)
        _main_log_v.addWidget(self.main_pygame_button)
        _main_log_v.addWidget(self.main_log_stack)

        def _main_build_retro_log():
            if self._main_retro_log is not None:
                return self._main_retro_log
            from zxnu_pygame import RetroLogWidget
            # scrollable live log: auto-follows the newest line, but the user can
            # scroll up (scrollbar / wheel) to read the history.
            widget = RetroLogWidget(
                scrollable=True, follow_tail=True, context_copy=True,
                font_px=getattr(self, "_retro_log_font_size",
                                DEFAULT_RETRO_LOG_FONT_SIZE))
            # Same floor as the classic log so the splitter can shrink either
            # log mode equally.
            widget.setMinimumHeight(60)
            try:
                widget.enable_background(getattr(self, "_nextsync_pygame_anim", True))
            except Exception:
                pass
            # Seed the user's retro-log text color (Settings color picker).
            try:
                widget.set_text_color(qcolor_to_hex(self.img_color_retro_log))
            except Exception:
                pass
            # Seed it with the existing classic-log contents. The list shows
            # newest-first, so iterate bottom-up for chronological order.
            for i in range(self.listWidgetLog.count() - 1, -1, -1):
                widget.append(self.listWidgetLog.item(i).text())
            self._main_retro_log = widget
            self.main_log_stack.addWidget(widget)
            return widget

        def _main_pygame_disable(reason=""):
            btn = self.main_pygame_button
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.setText("🎮 Retro")
            btn.blockSignals(False)
            btn.setEnabled(False)
            if reason:
                btn.setToolTip(reason)

        def _main_pygame_persist(enabled):
            # Skip writing while restoring the saved choice at startup so a
            # transient "pygame unavailable" never clobbers the user's pref.
            if getattr(self, "_main_pygame_restoring", False):
                return
            try:
                configuration_dictionary[SETTING_SDCARD_PYGAME_LOG] = (
                    "true" if enabled else "false")
                save_configuration_file()
            except Exception:
                pass

        def _main_on_pygame_toggled(checked):
            if checked:
                try:
                    from zxnu_pygame import pygame_available
                    ok, why = pygame_available()
                except Exception as exc:
                    ok, why = False, str(exc)
                if not ok:
                    _main_pygame_disable(
                        f"{why}\nInstall with: pip install pygame-ce")
                    add_main_log_window(
                        "Pygame mode unavailable — run: pip install pygame-ce")
                    return
                try:
                    widget = _main_build_retro_log()
                except Exception as exc:
                    _main_pygame_disable(f"Pygame init failed: {exc}")
                    return
                self._main_pygame_on = True
                self.main_pygame_button.setText("🖼 Switch to 'Classic' view mode")
                self.main_log_stack.setCurrentWidget(widget)
                widget.start()
                _main_pygame_persist(True)
            else:
                self._main_pygame_on = False
                self.main_pygame_button.setText("🎮 Retro")
                if self._main_retro_log is not None:
                    self._main_retro_log.stop()
                self.main_log_stack.setCurrentWidget(self.listWidgetLog)
                _main_pygame_persist(False)

        self.main_pygame_button.toggled.connect(_main_on_pygame_toggled)

        # Explorers ⇄ log splitter: a draggable horizontal grabber between the
        # explorer area (ending with its Path box) and the Retro/Classic log
        # window, so the explorers can be made taller at the log's expense and
        # vice versa. Extra space from resizing the window still goes to the
        # explorers (stretch factor 1 vs 0); neither pane can be collapsed to
        # nothing, and the log opens at its usual ~160 px height.
        self.sdcard_splitter = QSplitter(Qt.Vertical)
        self.sdcard_splitter.addWidget(self.sdcard_explorer_container)
        self.sdcard_splitter.addWidget(self.main_log_container)
        self.sdcard_splitter.setChildrenCollapsible(False)
        self.sdcard_splitter.setStretchFactor(0, 1)
        self.sdcard_splitter.setStretchFactor(1, 0)
        self.sdcard_splitter.setHandleWidth(8)
        self.sdcard_splitter.setSizes([500, 160])
        self.sdcard_splitter.handle(1).setToolTip(
            "Drag to resize the file explorers / log window split.")

        def _splitter_persist_on_move(splitter, setting_key):
            """Persist *splitter*'s pane sizes under *setting_key* on every
            user drag. splitterMoved only fires for real drags (never for
            programmatic setSizes, including the restore in
            load_configuration_file), so a restore can't echo back into the
            file; save_configuration_file is a no-op while _initialising."""
            def _on_moved(_pos, _index):
                configuration_dictionary[setting_key] = ",".join(
                    str(_s) for _s in splitter.sizes())
                save_configuration_file()
            splitter.splitterMoved.connect(_on_moved)

        _splitter_persist_on_move(self.sdcard_splitter, SETTING_SDCARD_SPLITTER)

        self.zx_next_unite_form.addRow(self.sdcard_splitter)

        # Add action buttons at the bottom, split into two titled groups so the
        # MAME and CSpect controls read as separate emulators rather than one
        # long undifferentiated button row. The MAME group sits on its own line
        # with the CSpect group (launch + display options) stacked below it.
        self.mame_group = QGroupBox("MAME")
        self.mame_group_layout = QHBoxLayout(self.mame_group)

        # "Launch Mame" button — placed before "Launch CSpect". Only shown when
        # a MAME executable was found (PATH or a prior downloads/mame install) or
        # the Flatpak launch option is enabled (Linux). The label gains a
        # "(flatpak)" suffix in that mode; both are kept current by
        # _refresh_mame_launch_ui() when the Flatpak toggle changes.
        _mame_available = self._mame_usable()
        self.button_start_mame = QPushButton(self._mame_launch_label(), self)
        self.button_start_mame.clicked.connect(launch_mame)
        self.button_start_mame.setVisible(_mame_available)
        self.mame_group_layout.addWidget(self.button_start_mame)

        # "Install MAME" button — shown in place of "Launch Mame" when MAME is
        # missing, on platforms where the automatic install is supported (64-bit
        # Windows). It detects the latest official release, downloads it and
        # extracts it into downloads/mame; on success the Launch button (revealed
        # by _on_mame_install_result) replaces it.
        self.button_install_mame = QPushButton("⬇  Install MAME", self)
        self.button_install_mame.setToolTip(
            "Detect the latest MAME release for this PC, then download and\n"
            "install it into the downloads/mame folder. Requires an internet\n"
            "connection (~90 MB download, ~500 MB installed).")
        self.button_install_mame.clicked.connect(install_mame)
        _mame_install_offered = (not _mame_available) and (mame_windows_asset_arch() is not None)
        self.button_install_mame.setVisible(_mame_install_offered)
        self.mame_group_layout.addWidget(self.button_install_mame)

        # MAME per-launch option combos (aspect / mouse / joystick), mirroring the
        # CSpect options. Their values are appended to the MAME command line at
        # launch (see launch_mame) and persisted to hdfg.cfg as combo indices.
        # They are only meaningful once MAME is installed, so they are hidden
        # while only "Install MAME" is offered and revealed after a successful
        # install (see _on_mame_install_result).
        self.mame_aspect = QComboBox()
        for _ma in MAME_ASPECT:
            self.mame_aspect.addItem(_ma[0])
        self.mame_aspect.setToolTip("MAME display aspect ratio (-aspect).")
        self.mame_aspect.currentIndexChanged.connect(set_mame_aspect)
        self.mame_group_layout.addWidget(self.mame_aspect)

        self.mame_sound = QComboBox()
        for _ms in MAME_SOUND:
            self.mame_sound.addItem(_ms[0])
        self.mame_sound.setToolTip(
            "MAME audio output method (-sound). 'Sound On' (default) keeps MAME's\n"
            "own backend; WASAPI / XAudio2 / PortAudio force a specific method\n"
            "(WASAPI and XAudio2 are Windows-only); 'Sound Off' mutes (-sound none).")
        self.mame_sound.currentIndexChanged.connect(set_mame_sound)
        self.mame_group_layout.addWidget(self.mame_sound)

        self.mame_mouse = QComboBox()
        for _mm in MAME_MOUSE:
            self.mame_mouse.addItem(_mm[0])
        self.mame_mouse.setToolTip(
            "Enable host mouse capture (Kempston mouse) or disable it\n"
            "(-mouse / -mouse_device none).")
        self.mame_mouse.currentIndexChanged.connect(set_mame_mouse)
        self.mame_group_layout.addWidget(self.mame_mouse)

        self.mame_joystick = QComboBox()
        for _mj in MAME_JOYSTICK:
            self.mame_joystick.addItem(_mj[0])
        self.mame_joystick.setToolTip(
            "Enable or disable joystick input\n"
            "(-joystick / -joystickprovider none).")
        self.mame_joystick.currentIndexChanged.connect(set_mame_joystick)
        self.mame_group_layout.addWidget(self.mame_joystick)

        self.mame_esc = QComboBox()
        for _me in MAME_ESC:
            self.mame_esc.addItem(_me[0])
        # Default to "Disable ESC Key On" (index 1) so a fresh install (no cfg
        # file, where load_configuration_file never reaches the restore above)
        # ships with quit-confirmation enabled. Set before the currentIndexChanged
        # connection below so no save is triggered; a saved cfg value still wins.
        self.mame_esc.setCurrentIndex(1)
        self.mame_esc.setToolTip(
            "Stop the ESC key from instantly quitting MAME by requiring a quit\n"
            "confirmation (-confirm_quit). 'Disable ESC Key On' (default) passes\n"
            "-confirm_quit; 'Disable ESC Key Off' lets ESC quit immediately.")
        self.mame_esc.currentIndexChanged.connect(set_mame_esc)
        self.mame_group_layout.addWidget(self.mame_esc)

        # Hide the option combos until MAME is actually installed (the group can
        # still be visible to host the "Install MAME" button).
        for _mame_combo in (self.mame_aspect, self.mame_sound, self.mame_mouse, self.mame_joystick, self.mame_esc):
            _mame_combo.setVisible(_mame_available)

        self.mame_group_layout.addStretch(1)

        # --- CSpect group: launch button plus its display / input options -----
        self.cspect_group = QGroupBox("CSpect")
        self.cspect_group_layout = QHBoxLayout(self.cspect_group)

        self.button_start_cspect = QPushButton("🕹  LaunchCSpect", self)
        self.button_start_cspect.setText("🕹  Launch CSpect")
        self.button_start_cspect.clicked.connect(launch_cspect)
        self.cspect_group_layout.addWidget(self.button_start_cspect)

        # Populate Screen Size Combo
        self.cspect_screensize = QComboBox()

        for sc in CSPECT_SCREEN_SIZES:
             self.cspect_screensize.addItem(sc[0])

        # First-run default (no cfg present): "Screen Size X3" rather than the
        # combo's natural index-0 ("Screen Size X1"). A saved configuration
        # overrides this later in load_configuration_file(). Set before the
        # signal is connected so it doesn't trigger a spurious config save.
        _screensize_default_idx = next(
            (i for i, sc in enumerate(CSPECT_SCREEN_SIZES) if sc[0] == "Screen Size X3"), 0)
        self.cspect_screensize.setCurrentIndex(_screensize_default_idx)

        self.cspect_screensize.show()
        self.cspect_screensize.currentIndexChanged.connect(set_cspect_screen_size)

        self.cspect_group_layout.addWidget(self.cspect_screensize)

        # Populate Sound Combo
        self.cspect_sound = QComboBox()

        for ssound in CSPECT_SOUND:
             self.cspect_sound.addItem(ssound[0])

        self.cspect_sound.show()
        self.cspect_sound.currentIndexChanged.connect(set_cspect_sound_on_off)

        self.cspect_group_layout.addWidget(self.cspect_sound)

        # Populate vsync Combo
        self.cspect_vsync = QComboBox()

        for vs in CSPECT_SCREEN_SYNC:
             self.cspect_vsync.addItem(vs[0])

        self.cspect_vsync.show()
        self.cspect_vsync.currentIndexChanged.connect(set_cspect_vsync_on_off)

        self.cspect_group_layout.addWidget(self.cspect_vsync)

        # Populate Joystick Combo
        self.cspect_joystick = QComboBox()

        for jsc in CSPECT_JOYSTICK:
             self.cspect_joystick.addItem(jsc[0])

        self.cspect_joystick.show()
        self.cspect_joystick.currentIndexChanged.connect(set_cspect_joystick_on_off)

        self.cspect_group_layout.addWidget(self.cspect_joystick)

        # Populate Mouse Combo (mouse capture on/off; "Mouse Off" passes -mouse)
        self.cspect_mouse = QComboBox()

        for msc in CSPECT_MOUSE:
             self.cspect_mouse.addItem(msc[0])

        self.cspect_mouse.show()
        self.cspect_mouse.currentIndexChanged.connect(set_cspect_mouse_on_off)

        self.cspect_group_layout.addWidget(self.cspect_mouse)

        # Populate frequency Combo
        self.cspect_frequency = QComboBox()

        for cf in CSPECT_FREQUENCY:
             self.cspect_frequency.addItem(cf[0])

        self.cspect_frequency.show()
        self.cspect_frequency.currentIndexChanged.connect(set_cspect_display_frequency)

        self.cspect_group_layout.addWidget(self.cspect_frequency)

        # Populate ESC-key disable combo ("Disable ESC Key On" passes -esc)
        self.cspect_esc = QComboBox()

        for ec in CSPECT_ESC:
             self.cspect_esc.addItem(ec[0])

        # Default to "Disable ESC Key On" (index 1) so a fresh install (no cfg
        # file, where load_configuration_file never reaches the restore below)
        # ships with ESC-to-quit disabled. Set before the currentIndexChanged
        # connection below so no save is triggered; a saved cfg value still wins.
        self.cspect_esc.setCurrentIndex(1)

        self.cspect_esc.setToolTip(
            "Disable the ESC key from quitting CSpect (-esc). 'Disable ESC Key On'\n"
            "(default) passes -esc so ESC won't quit; 'Disable ESC Key Off' leaves\n"
            "ESC working.")
        self.cspect_esc.show()
        self.cspect_esc.currentIndexChanged.connect(set_cspect_esc)

        self.cspect_group_layout.addWidget(self.cspect_esc)

        self.cspect_group_layout.addStretch(1)

        # Hide the MAME group when neither of its buttons applies — i.e. MAME is
        # not installed and the in-app installer isn't offered on this platform
        # (only 64-bit Windows) — so we never show an empty titled box. On
        # Linux/macOS the group shows only when a MAME was detected.
        if not _mame_available and not _mame_install_offered:
            self.mame_group.setVisible(False)

        # Hide the whole CSpect group when the CSpect emulator was not found at
        # startup (application directory or PATH). The individual controls keep
        # their own visible flags so the async downloads/cspect scan can reveal
        # the group later just by showing it. The MAME group is unaffected.
        if self._cspect_executable_path is None:
            self.cspect_group.setVisible(False)

        # MAME group on its own line, CSpect group stacked directly beneath it.
        self.zx_next_unite_form.addRow(self.mame_group)
        self.zx_next_unite_form.addRow(self.cspect_group)

        set_all_buttons_disabled()
        enable_image_selection()


        wid = QWidget()
        grid = QGridLayout(wid)
        wid.setLayout(grid)

        # setting the inner widget and layout
        grid_inner = QGridLayout()
        wid_inner = BackgroundWidget(wid)
        wid_inner.setLayout(grid_inner)
        self._bg_widget = wid_inner

        # add the inner widget to the outer layout
        grid.addWidget(wid_inner)

        # add tab frame to widget
        wid_inner.tab = QTabWidget(wid_inner)
        wid_inner.tab.setAttribute(Qt.WA_TranslucentBackground)
        wid_inner.tab.setAutoFillBackground(False)
        self._tab_widget = wid_inner.tab
        # Animated 8-bit sprite sidebar (left) mirroring the tabs, so the user
        # can jump straight to any tab on narrow windows where the top tab bar
        # overflows behind the < / > scroll arrows.
        self._tab_sidebar = TabSpriteSidebar(wid_inner.tab, wid_inner)
        grid_inner.addWidget(self._tab_sidebar, 0, 0)
        grid_inner.addWidget(wid_inner.tab, 0, 1)
        grid_inner.setColumnStretch(1, 1)

        # Feed the sidebar's NextSync icon its activity state: (running,
        # transferring). Running — either server, classic or Remote Explorer —
        # makes the icon's arrows carry travelling packet pixels so a live
        # server stays visible from any tab; an ongoing transfer (a classic
        # client session, or a Remote Explorer batch operation) accelerates
        # them. Polled from the sidebar's animation timer, so it needs no
        # start/stop wiring here.
        def _sidebar_sync_activity():
            try:
                t = getattr(self, "_nextsync_thread", None)
                classic = t is not None and t.is_alive()
            except Exception:
                classic = False
            running = classic or bool(getattr(self, "_re_running", False))
            if not running:
                return (False, False)
            transferring = bool(getattr(self, "_nextsync_transfer_active", False))
            _rew = getattr(self, "_re_widget", None)
            if _rew is not None and getattr(_rew, "_op_active", False):
                transferring = True
            return (True, transferring)
        self._tab_sidebar.set_sync_activity_getter(_sidebar_sync_activity)

        # ---- Initialize AllInOne tab color cycling timer early ----
        _ALLINONE_COLORS = [QColor('red'), QColor('#FFD700'),
                            QColor('green'), QColor('blue')]  # Red, Yellow, Green, Blue
        self._allinone_color_frame = 0
        self._allinone_color_timer = QTimer(self)
        self._allinone_color_timer.setInterval(500)  # Change color every 500ms

        def _allinone_color_tick():
            # Cycle the tab text color of the AllInOne tab. Using
            # setTabTextColor keeps the existing setTabText-based spinner
            # (rotating earth) and result-count badge fully intact.
            try:
                tab_bar = self._tab_widget.tabBar()
            except Exception:
                return
            color = _ALLINONE_COLORS[self._allinone_color_frame % len(_ALLINONE_COLORS)]
            self._allinone_color_frame += 1
            for i in range(self._tab_widget.count()):
                if "Unite!" in self._tab_widget.tabText(i):
                    tab_bar.setTabTextColor(i, color)
                    break

        self._allinone_color_timer.timeout.connect(_allinone_color_tick)

        # ---- Remote Explorer sub-tab text colour animation --------------------
        # Mirrors the Unite! main-tab colour cycling, but on the NextSync tab's
        # "Remote Explorer" sub-tab (nextsync_mode_tabs index 0). To save CPU it
        # runs ONLY while the NextSync tab is the visible main tab: on_tab_changed
        # (and the deferred startup activation) call _re_tab_anim_set_active to
        # start/stop it. Reuses the Unite! colour list and 500 ms cadence.
        self._re_tab_color_frame = 0
        self._re_tab_color_timer = QTimer(self)
        self._re_tab_color_timer.setInterval(500)

        def _re_tab_color_tick():
            tabs = getattr(self, "nextsync_mode_tabs", None)
            if tabs is None:
                return
            color = _ALLINONE_COLORS[self._re_tab_color_frame % len(_ALLINONE_COLORS)]
            self._re_tab_color_frame += 1
            try:
                tabs.setTabTextColor(0, color)   # index 0 == "Remote Explorer"
            except RuntimeError:
                pass  # tab bar gone (shutdown) — harmless
        self._re_tab_color_timer.timeout.connect(_re_tab_color_tick)

        def _re_tab_anim_set_active(active):
            """Start/stop the Remote Explorer sub-tab colour cycling. Called with
            True when the NextSync tab becomes visible and False when leaving it,
            so the timer never runs while the user is on another tab."""
            if getattr(self, "nextsync_mode_tabs", None) is None:
                return
            if active:
                if not self._re_tab_color_timer.isActive():
                    self._re_tab_color_timer.start()
            elif self._re_tab_color_timer.isActive():
                self._re_tab_color_timer.stop()
                # Repaint the tab in the normal readable colour so it doesn't
                # freeze on whatever cycle colour it stopped on.
                _restore = getattr(self, "_apply_tab_text_colors", None)
                if _restore is not None:
                    try:
                        _restore()
                    except Exception:
                        pass
        self._re_tab_anim_set_active = _re_tab_anim_set_active

        # ── Favorites helpers (extracted to zxnu_favorites_pane.py) ──
        # Cross-pane favorite record/toggle/navigate helpers; the three
        # run_search closures are injected as forwarding lambdas (the pane
        # builders that bind them run below).
        build_favorites_helpers(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            getit_run_search=lambda *a, **k: getit_run_search(*a, **k),
            zxdb_run_search=lambda *a, **k: zxdb_run_search(*a, **k),
            zxart_run_search=lambda *a, **k: zxart_run_search(*a, **k),
        )

        zx_next_unite_container = QWidget()
        zx_next_unite_container.setLayout(self.zx_next_unite_form)

        # --- NextSync tab: widgets + wiring (extracted to zxnu_nextsync_pane.py).
        # The operation-layer closures defined above are injected as params; the
        # builder hands back nextsync_container (added to the tab grid below) and
        # _re_try_send_folder (called from nextsync_start_server).
        build_nextsync_pane(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            available_drives=available_drives,
            list_windows_drives=list_windows_drives,
            set_treeview_properties=set_treeview_properties,
            add_nextsync_log_window=add_nextsync_log_window,
            apply_file_extension_filter_nextsync=apply_file_extension_filter_nextsync,
            nextsync_update_root_drive=nextsync_update_root_drive,
            on_nextsync_file_explorer_path_edited=on_nextsync_file_explorer_path_edited,
            nextsync_on_treeview_context_menu=nextsync_on_treeview_context_menu,
            nextsync_on_treeview_double_clicked=nextsync_on_treeview_double_clicked,
            nextsync_rename_explorer_item=nextsync_rename_explorer_item,
            nextsync_delete_explorer_item=nextsync_delete_explorer_item,
            nextsync_import_external_paths=nextsync_import_external_paths,
            nextsync_refresh_explorer=nextsync_refresh_explorer,
            nextsync_start_server=nextsync_start_server,
            nextsync_cancel_server_job=nextsync_cancel_server_job,
            nextsync_perform_checks_and_prepare_server_start=nextsync_perform_checks_and_prepare_server_start,
            nextsync_hide_start_cancel_buttons=nextsync_hide_start_cancel_buttons,
            nextsync_sync_mode_changed=nextsync_sync_mode_changed,
            nextsync_slowtransfer_checkbox_statechanged=nextsync_slowtransfer_checkbox_statechanged,
            nextsync_create_syncingore_button=nextsync_create_syncingore_button,
            nextsync_delete_syncingore_button=nextsync_delete_syncingore_button,
            nextsync_delete_syncpoint_button=nextsync_delete_syncpoint_button,
            _nextsync_on_set_syncroot_clicked=_nextsync_on_set_syncroot_clicked,
            _nextsync_explorer_copy_selection=_nextsync_explorer_copy_selection,
            _nextsync_explorer_paste_target_dir=_nextsync_explorer_paste_target_dir,
            _explorer_paste_into_local=_explorer_paste_into_local,
        )
        nextsync_container = self.nextsync_container
        _re_try_send_folder = self._re_try_send_folder

        build_getit_pane(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            execute_hdf_monkey=execute_hdf_monkey,
            generate_disk_file_path=generate_disk_file_path,
            update_disk_manager_widget_table=update_disk_manager_widget_table,
            _persist_retro=_persist_retro,
            _search_autocomplete_on=_search_autocomplete_on,
            _splitter_persist_on_move=_splitter_persist_on_move,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _qimage_from_data=_qimage_from_data,
            _gallery_add_text_pages=_gallery_add_text_pages,
            _gallery_add_description_page=_gallery_add_description_page,
            _make_retro_toggle_button=_make_retro_toggle_button,
            _popup_height_for=_popup_height_for,
            _wrap_flow_row=_wrap_flow_row,
            getit_run_in_thread=getit_run_in_thread,
            _CompleterPopupHider=_CompleterPopupHider,
            _GALLERY_TEXT_EXTS=_GALLERY_TEXT_EXTS,
            _start_tab_spinner=lambda *a, **k: _start_tab_spinner(*a, **k),
            _stop_tab_spinner=lambda *a, **k: _stop_tab_spinner(*a, **k),
            _set_tab_badge=lambda *a, **k: _set_tab_badge(*a, **k),
            _clear_tab_badge=lambda *a, **k: _clear_tab_badge(*a, **k),
            _multi_search_enabled=lambda *a, **k: _multi_search_enabled(*a, **k),
            _cross_search_zxdb=lambda *a, **k: _cross_search_zxdb(*a, **k),
            _cross_search_zxart=lambda *a, **k: _cross_search_zxart(*a, **k),
            _right_disk_content=lambda: right_disk_image_explorer_content,
        )
        getit_run_search = self.getit_run_search
        getit_on_latest = self.getit_on_latest
        getit_on_random = self.getit_on_random
        _getit_open_gallery_viewer = self._getit_open_gallery_viewer

        build_zxdb_pane(
            self,
            configuration_dictionary=configuration_dictionary,
            _DblClickFilter=_DblClickFilter,
            save_configuration_file=save_configuration_file,
            execute_hdf_monkey=execute_hdf_monkey,
            generate_disk_file_path=generate_disk_file_path,
            update_disk_manager_widget_table=update_disk_manager_widget_table,
            _persist_retro=_persist_retro,
            _search_autocomplete_on=_search_autocomplete_on,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _qimage_from_data=_qimage_from_data,
            _gallery_add_text_pages=_gallery_add_text_pages,
            _gallery_add_description_page=_gallery_add_description_page,
            _make_disclaimer_ticker=_make_disclaimer_ticker,
            _make_retro_toggle_button=_make_retro_toggle_button,
            _popup_height_for=_popup_height_for,
            _wrap_flow_row=_wrap_flow_row,
            getit_run_in_thread=getit_run_in_thread,
            _CompleterPopupHider=_CompleterPopupHider,
            _start_tab_spinner=lambda *a, **k: _start_tab_spinner(*a, **k),
            _stop_tab_spinner=lambda *a, **k: _stop_tab_spinner(*a, **k),
            _set_tab_badge=lambda *a, **k: _set_tab_badge(*a, **k),
            _clear_tab_badge=lambda *a, **k: _clear_tab_badge(*a, **k),
            _multi_search_enabled=lambda *a, **k: _multi_search_enabled(*a, **k),
            _cross_search_getit=lambda *a, **k: _cross_search_getit(*a, **k),
            _cross_search_zxart=lambda *a, **k: _cross_search_zxart(*a, **k),
            _right_disk_content=lambda: right_disk_image_explorer_content,
        )
        zxdb_run_search = self.zxdb_run_search
        zxdb_on_latest = self.zxdb_on_latest
        zxdb_on_random = self.zxdb_on_random
        _zxdb_open_gallery_viewer = self._zxdb_open_gallery_viewer

        build_zxart_pane(
            self,
            configuration_dictionary=configuration_dictionary,
            _DblClickFilter=_DblClickFilter,
            save_configuration_file=save_configuration_file,
            execute_hdf_monkey=execute_hdf_monkey,
            generate_disk_file_path=generate_disk_file_path,
            update_disk_manager_widget_table=update_disk_manager_widget_table,
            _persist_retro=_persist_retro,
            _search_autocomplete_on=_search_autocomplete_on,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _qimage_from_data=_qimage_from_data,
            _gallery_add_text_pages=_gallery_add_text_pages,
            _gallery_add_description_page=_gallery_add_description_page,
            _make_disclaimer_ticker=_make_disclaimer_ticker,
            _make_retro_toggle_button=_make_retro_toggle_button,
            _popup_height_for=_popup_height_for,
            _wrap_flow_row=_wrap_flow_row,
            getit_run_in_thread=getit_run_in_thread,
            _CompleterPopupHider=_CompleterPopupHider,
            _start_tab_spinner=lambda *a, **k: _start_tab_spinner(*a, **k),
            _stop_tab_spinner=lambda *a, **k: _stop_tab_spinner(*a, **k),
            _set_tab_badge=lambda *a, **k: _set_tab_badge(*a, **k),
            _clear_tab_badge=lambda *a, **k: _clear_tab_badge(*a, **k),
            _multi_search_enabled=lambda *a, **k: _multi_search_enabled(*a, **k),
            _cross_search_getit=lambda *a, **k: _cross_search_getit(*a, **k),
            _cross_search_zxdb=lambda *a, **k: _cross_search_zxdb(*a, **k),
            _right_disk_content=lambda: right_disk_image_explorer_content,
        )
        zxart_run_search = self.zxart_run_search
        zxart_on_latest = self.zxart_on_latest
        zxart_on_random = self.zxart_on_random
        _zxart_open_gallery_viewer = self._zxart_open_gallery_viewer

        self.setCentralWidget(wid_inner)


        # Create zx-next-unite Tab
        zx_next_unite_tab = QWidget(wid_inner.tab)
        zx_next_unite_tab.setAttribute(Qt.WA_TranslucentBackground)
        zx_next_unite_tab.setAutoFillBackground(False)
        grid_tab = QGridLayout(zx_next_unite_tab)
        grid_tab.addWidget(zx_next_unite_container) # here use the form container
        zx_next_unite_tab.setLayout(grid_tab)
        zx_next_unite_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_GOOEY
        wid_inner.tab.addTab(zx_next_unite_tab, ZX_NEXT_UNITE_TAB_TITLE_GOOEY)

        # Create NextSync Tab
        zxnextunite_NextSync_tab = QWidget(wid_inner.tab)
        zxnextunite_NextSync_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_NextSync_tab.setAutoFillBackground(False)
        grid_tab_nextsync = QGridLayout(zxnextunite_NextSync_tab)
        # Row 0: the Remote Explorer / Classic experience selector, right below
        # the main tab strip and spanning the full width. Row 1: the form content.
        grid_tab_nextsync.addWidget(self.nextsync_mode_tabs, 0, 0)
        grid_tab_nextsync.addWidget(nextsync_container, 1, 0) # here use the form container
        grid_tab_nextsync.setRowStretch(1, 1)
        zxnextunite_NextSync_tab.setLayout(grid_tab_nextsync)
        zxnextunite_NextSync_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC
        wid_inner.tab.addTab(zxnextunite_NextSync_tab, ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC)

        # Create GetIt Tab
        zxnextunite_GetIt_tab = QWidget(wid_inner.tab)
        zxnextunite_GetIt_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_GetIt_tab.setAutoFillBackground(False)
        grid_tab_getit = QGridLayout(zxnextunite_GetIt_tab)
        grid_tab_getit.setContentsMargins(0, 0, 0, 0)
        grid_tab_getit.addWidget(self._getit_stack)
        zxnextunite_GetIt_tab.setLayout(grid_tab_getit)
        zxnextunite_GetIt_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_GETIT
        wid_inner.tab.addTab(zxnextunite_GetIt_tab, ZX_NEXT_UNITE_TAB_TITLE_GETIT)

        # Create zxART Tab (right of GetIt)
        zxnextunite_ZXART_tab = QWidget(wid_inner.tab)
        zxnextunite_ZXART_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_ZXART_tab.setAutoFillBackground(False)
        grid_tab_zxart = QGridLayout(zxnextunite_ZXART_tab)
        grid_tab_zxart.setContentsMargins(0, 0, 0, 0)
        grid_tab_zxart.addWidget(self._zxart_stack)
        zxnextunite_ZXART_tab.setLayout(grid_tab_zxart)
        zxnextunite_ZXART_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_ZXART
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            wid_inner.tab.addTab(zxnextunite_ZXART_tab, ZX_NEXT_UNITE_TAB_TITLE_ZXART)
        else:
            zxnextunite_ZXART_tab.setParent(None)

        # Create ZXDB Tab (right of zxART)
        zxnextunite_ZXDB_tab = QWidget(wid_inner.tab)
        zxnextunite_ZXDB_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_ZXDB_tab.setAutoFillBackground(False)
        grid_tab_zxdb = QGridLayout(zxnextunite_ZXDB_tab)
        grid_tab_zxdb.setContentsMargins(0, 0, 0, 0)
        grid_tab_zxdb.addWidget(self._zxdb_stack)
        zxnextunite_ZXDB_tab.setLayout(grid_tab_zxdb)
        zxnextunite_ZXDB_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_ZXDB
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            wid_inner.tab.addTab(zxnextunite_ZXDB_tab, ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
        else:
            zxnextunite_ZXDB_tab.setParent(None)

        # Create ONLINE Favorites Tab (extracted to zxnu_favorites_pane.py;
        # right of zxArt, before Settings). The builder also exposes the
        # _fav_* getters consumed by build_unite_pane/build_unite_ops below —
        # re-bound to the bare locals those call sites pass.
        build_favorites_pane(
            self,
            wid_inner=wid_inner,
            _persist_retro=_persist_retro,
            _make_retro_toggle_button=_make_retro_toggle_button,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _getit_open_gallery_viewer=_getit_open_gallery_viewer,
            _zxdb_open_gallery_viewer=_zxdb_open_gallery_viewer,
            _zxart_open_gallery_viewer=_zxart_open_gallery_viewer,
        )
        _fav_title_getter = self._fav_title_getter
        _fav_info_getter = self._fav_info_getter
        _fav_thumb_fetch = self._fav_thumb_fetch
        _fav_extra_fetch = self._fav_extra_fetch
        _fav_open_fullscreen = self._fav_open_fullscreen

        # ─── ONLINE: AllInOne (Unite!) Tab (extracted to zxnu_unite_pane.py) ──
        # Widget layer only; the operation layer follows in build_unite_ops
        # below, right after the itch.io tab block (its historical position).
        build_unite_pane(
            self,
            _fav_title_getter=_fav_title_getter,
            _fav_info_getter=_fav_info_getter,
            _fav_thumb_fetch=_fav_thumb_fetch,
            _fav_extra_fetch=_fav_extra_fetch,
            _fav_open_fullscreen=_fav_open_fullscreen,
            wid_inner=wid_inner,
            zxnextunite_GetIt_tab=zxnextunite_GetIt_tab,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _wrap_flow_row=_wrap_flow_row,
        )

        # ─── ONLINE: itch.io Tab (extracted to zxnu_itchio_pane.py) ─────────
        # Optional, requires the 'itch-dl' package; the whole tab (widgets,
        # closures, install/uninstall actions, conditional insertion) lives in
        # build_itchio_pane. Op-layer helpers defined later in __init__ are
        # injected as forwarding lambdas.
        build_itchio_pane(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            image_upload_external_paths=image_upload_external_paths,
            generate_disk_file_path=generate_disk_file_path,
            _persist_retro=_persist_retro,
            wid_inner=wid_inner,
            _right_disk_content=lambda: right_disk_image_explorer_content,
            _CompleterPopupHider=_CompleterPopupHider,
            _gallery_add_description_page=_gallery_add_description_page,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _make_retro_toggle_button=_make_retro_toggle_button,
            _qimage_from_data=_qimage_from_data,
            _wrap_flow_row=_wrap_flow_row,
            getit_run_in_thread=getit_run_in_thread,
            _multi_search_enabled=lambda *a, **k: _multi_search_enabled(*a, **k),
            _cross_search_getit=lambda *a, **k: _cross_search_getit(*a, **k),
            _cross_search_zxdb=lambda *a, **k: _cross_search_zxdb(*a, **k),
            _cross_search_zxart=lambda *a, **k: _cross_search_zxart(*a, **k),
            _set_tab_badge=lambda *a, **k: _set_tab_badge(*a, **k),
            _clear_tab_badge=lambda *a, **k: _clear_tab_badge(*a, **k),
            _start_tab_spinner=lambda *a, **k: _start_tab_spinner(*a, **k),
            _stop_tab_spinner=lambda *a, **k: _stop_tab_spinner(*a, **k),
        )

        # --- Unite! operation layer (extracted to zxnu_unite_pane.py) ---
        # Aggregation + tab badge, fan-out Search/Latest/Random, merged
        # autocomplete, the view-mode apply helper and the optional pygame
        # ("Retro") visualization mode.
        build_unite_ops(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            _ALLINONE_SOURCE_LABELS=self._ALLINONE_SOURCE_LABELS,
            _allinone_source_label=self._allinone_source_label,
            _fav_title_getter=_fav_title_getter,
            _fav_info_getter=_fav_info_getter,
            _fav_thumb_fetch=_fav_thumb_fetch,
            _search_autocomplete_on=_search_autocomplete_on,
            getit_on_latest=getit_on_latest,
            getit_on_random=getit_on_random,
            zxdb_on_latest=zxdb_on_latest,
            zxdb_on_random=zxdb_on_random,
            zxart_on_latest=zxart_on_latest,
            zxart_on_random=zxart_on_random,
            _getit_open_gallery_viewer=_getit_open_gallery_viewer,
            _zxdb_open_gallery_viewer=_zxdb_open_gallery_viewer,
            _zxart_open_gallery_viewer=_zxart_open_gallery_viewer,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _popup_height_for=_popup_height_for,
            getit_run_in_thread=getit_run_in_thread,
            _CompleterPopupHider=_CompleterPopupHider,
            _start_tab_spinner=lambda *a, **k: _start_tab_spinner(*a, **k),
            _stop_tab_spinner=lambda *a, **k: _stop_tab_spinner(*a, **k),
            _clear_tab_badge=lambda *a, **k: _clear_tab_badge(*a, **k),
            _cross_search_getit=lambda *a, **k: _cross_search_getit(*a, **k),
            _cross_search_zxdb=lambda *a, **k: _cross_search_zxdb(*a, **k),
            _cross_search_zxart=lambda *a, **k: _cross_search_zxart(*a, **k),
        )

        # ── Favorites ops + per-pane Classic ↔ Retro item-viewer routing
        # (extracted to zxnu_favorites_pane.py): _fav_repopulate, the
        # view-mode apply helper (applied here, persist=False) and the
        # _pane_* routing layer.
        build_favorites_ops(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            _gif_fetch_bytes=_gif_fetch_bytes,
            _getit_open_gallery_viewer=_getit_open_gallery_viewer,
            _zxdb_open_gallery_viewer=_zxdb_open_gallery_viewer,
            _zxart_open_gallery_viewer=_zxart_open_gallery_viewer,
            _fav_title_getter=_fav_title_getter,
            _fav_info_getter=_fav_info_getter,
            _fav_thumb_fetch=_fav_thumb_fetch,
        )

        # Create Settings Tab (extracted to zxnu_settings_pane.py; the builder
        # writes every historical self.settings_* attribute and hands back
        # settings_scroll for the addTab below)
        # UI-language re-translation hook: the Settings pane's language combo
        # calls this with a code from zxnu_i18n.UI_LANGUAGES; the walk swaps
        # every catalogued text in place (English restores the originals).
        def _i18n_apply(code):
            translate_widget_tree(self, normalize_ui_language(code))
        self._i18n_apply = _i18n_apply

        build_settings_pane(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            open_cspect_configuration_file=open_cspect_configuration_file,
            wid_inner=wid_inner,
            _zxnu_set_crash_log_enabled=_zxnu_set_crash_log_enabled,
            _apply_autocomplete_setting=lambda *a, **k: _apply_autocomplete_setting(*a, **k),
        )
        settings_scroll = self.settings_scroll
        wid_inner.tab.addTab(settings_scroll, "Settings 🔩")

          # Create Help Tab
        # Mirrors the SD Card Utility log window: a Classic/Retro toggle that
        # swaps the help list for a retro 8-bit pygame console (the animated
        # $/£/€ starfield with green Consolas text). Pygame is optional — the
        # toggle disables itself with an install hint when pygame-ce is missing.
        # When pygame is present, retro mode is turned on automatically for this
        # tab on first run (see _apply_first_run_pygame_defaults). Page 0 = the
        # classic help list, page 1 = the retro console (built lazily the first
        # time it is switched on, and sharing the NextSync starfield-animation
        # preference).
        zxnextunite_Help_tab = QWidget(wid_inner.tab)
        zxnextunite_Help_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_Help_tab.setAutoFillBackground(False)

        self._help_retro_log = None
        self._help_pygame_on = False

        self.help_pygame_button = QPushButton("🎮 Retro")
        self.help_pygame_button.setCheckable(True)
        self.help_pygame_button.setToolTip(
            "Switch the help window to a retro 8-bit pygame console:\n"
            "an animated starfield with green Consolas text.\n"
            "Requires the optional 'pygame-ce' package.")

        self.help_log_stack = QStackedWidget(self)
        self.help_log_stack.addWidget(self.listWidgetHelp)   # page 0 = classic list

        grid_tab_Help = QVBoxLayout(zxnextunite_Help_tab)
        grid_tab_Help.setContentsMargins(0, 0, 0, 0)
        grid_tab_Help.setSpacing(2)
        grid_tab_Help.addWidget(self.help_pygame_button)
        grid_tab_Help.addWidget(self.help_log_stack)

        def _help_build_retro_log():
            if self._help_retro_log is not None:
                return self._help_retro_log
            from zxnu_pygame import RetroLogWidget
            # scrollable=True adds a vertical scrollbar (+ mouse wheel) so the
            # long help text can be read in full, top-to-bottom.
            widget = RetroLogWidget(scrollable=True)
            try:
                widget.enable_background(getattr(self, "_nextsync_pygame_anim", True))
            except Exception:
                pass
            # Seed the user's retro-log text color (Settings color picker).
            try:
                widget.set_text_color(qcolor_to_hex(self.img_color_retro_log))
            except Exception:
                pass
            # Seed it with the existing help contents. The help list reads
            # top-down (first line first), so iterate top-to-bottom for the same
            # reading order.
            for i in range(self.listWidgetHelp.count()):
                widget.append(self.listWidgetHelp.item(i).text())
            self._help_retro_log = widget
            self.help_log_stack.addWidget(widget)
            return widget

        def _help_pygame_disable(reason=""):
            btn = self.help_pygame_button
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.setText("🎮 Retro")
            btn.blockSignals(False)
            btn.setEnabled(False)
            if reason:
                btn.setToolTip(reason)

        def _help_pygame_persist(enabled):
            # Skip writing while restoring the saved choice at startup so a
            # transient "pygame unavailable" never clobbers the user's pref.
            if getattr(self, "_help_pygame_restoring", False):
                return
            try:
                configuration_dictionary[SETTING_HELP_PYGAME_LOG] = (
                    "true" if enabled else "false")
                save_configuration_file()
            except Exception:
                pass

        def _help_on_pygame_toggled(checked):
            if checked:
                try:
                    from zxnu_pygame import pygame_available
                    ok, why = pygame_available()
                except Exception as exc:
                    ok, why = False, str(exc)
                if not ok:
                    _help_pygame_disable(
                        f"{why}\nInstall with: pip install pygame-ce")
                    return
                try:
                    widget = _help_build_retro_log()
                except Exception as exc:
                    _help_pygame_disable(f"Pygame init failed: {exc}")
                    return
                self._help_pygame_on = True
                self.help_pygame_button.setText("🖼 Switch to 'Classic' view mode")
                self.help_log_stack.setCurrentWidget(widget)
                widget.start()
                _help_pygame_persist(True)
            else:
                self._help_pygame_on = False
                self.help_pygame_button.setText("🎮 Retro")
                if self._help_retro_log is not None:
                    self._help_retro_log.stop()
                self.help_log_stack.setCurrentWidget(self.listWidgetHelp)
                _help_pygame_persist(False)

        self.help_pygame_button.toggled.connect(_help_on_pygame_toggled)

        zxnextunite_Help_tab.setLayout(grid_tab_Help)
        wid_inner.tab.addTab(zxnextunite_Help_tab, "?")

        #wid_inner.tab.tabBarClicked.connect(tab_changed)

        def _show_content_disclaimer():
            """Show the legal disclaimer splash for content panes.

            Returns True if the caller should proceed (user agreed previously,
            or just ticked the checkbox).  Returns False if the user dismissed
            with Close (no agreement) — caller should still open the pane but
            will be shown the dialog again next time.
            """
            if configuration_dictionary.get(SETTING_CONTENT_DISCLAIMER_AGREED, "") == "1":
                return True

            from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel

            dlg = QDialog(self)
            dlg.setWindowTitle("Content Sources — Legal Disclaimer")
            dlg.setMinimumWidth(620)
            dlg.setMinimumHeight(440)
            dlg.setModal(True)

            layout = QVBoxLayout(dlg)
            layout.setSpacing(10)
            layout.setContentsMargins(16, 16, 16, 12)

            title_lbl = QLabel("<b>Third-Party Content Sources — Legal Disclaimer</b>")
            title_lbl.setWordWrap(True)
            layout.addWidget(title_lbl)

            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setPlainText(_DISCLAIMER_TEXT)
            text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(text_edit, 1)

            agree_cb = QCheckBox("I agree and understand. Do not show this message again.")
            layout.addWidget(agree_cb)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            close_btn = QPushButton("Close")
            close_btn.setDefault(True)
            btn_row.addWidget(close_btn)
            layout.addLayout(btn_row)

            def _on_agree(checked):
                if checked:
                    configuration_dictionary[SETTING_CONTENT_DISCLAIMER_AGREED] = "1"
                    save_configuration_file()
                    dlg.accept()

            def _on_close():
                dlg.reject()

            agree_cb.stateChanged.connect(_on_agree)
            close_btn.clicked.connect(_on_close)

            dlg.exec()
            return configuration_dictionary.get(SETTING_CONTENT_DISCLAIMER_AGREED, "") == "1"

        # ---- Multi-API cross-search helpers ----

        def _autocomplete_enabled() -> bool:
            cb = getattr(self, "settings_search_autocomplete_checkbox", None)
            return cb is None or cb.isChecked()

        def _apply_autocomplete_setting(enabled: bool):
            """Attach or detach completers on every search input.

            itch.io is optional (built only when the tab is present), so it is
            looked up with getattr and skipped when absent — keeping it in line
            with the other panes' typing guard so the global autocomplete toggle
            governs its suggestion dropdown too."""
            for input_widget, completer in (
                (self.getit_search_input, getattr(self, "_getit_completer", None)),
                (self.zxdb_search_input,  getattr(self, "_zxdb_completer",  None)),
                (self.zxart_search_input, getattr(self, "_zxart_completer", None)),
                # Never (re)attach the Unite! completer while pygame mode is on:
                # its dropdown steals keyboard focus over the animating surface.
                (getattr(self, "allinone_search_input", None),
                 None if getattr(self, "_allinone_pygame_on", False)
                 else getattr(self, "_allinone_completer", None)),
                (getattr(self, "itchio_search_input", None),
                 getattr(self, "_itchio_completer", None)),
            ):
                if input_widget is None:
                    continue
                try:
                    input_widget.setCompleter(completer if enabled else None)
                except RuntimeError:
                    pass

        def _multi_search_enabled() -> bool:
            cb = getattr(self, "settings_multi_search_checkbox", None)
            return cb is not None and cb.isChecked()

        def _cross_search_getit(query: str, on_done=None):
            """Run a full GetIt search in the background, populate the table and badge the tab."""
            if not query:
                if on_done:
                    on_done()
                return
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            def _after_search():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                n = self.getit_results_table.rowCount()
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, n)
                if on_done:
                    on_done()
            getit_run_search(query, 1, _after_search)

        def _cross_search_zxdb(query: str, on_done=None):
            """Run a full ZXDB search in the background, populate the table and badge the tab."""
            if not ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                if on_done:
                    on_done()
                return
            if not query:
                if on_done:
                    on_done()
                return
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            def _after_search():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                n = self.zxdb_results_table.rowCount()
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, n)
                if on_done:
                    on_done()
            zxdb_run_search(query, 1, _after_search)

        def _cross_search_zxart(query: str, on_done=None):
            """Run a full zxART search in the background, populate the table and badge the tab."""
            if not ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                if on_done:
                    on_done()
                return
            if not query:
                if on_done:
                    on_done()
                return
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            def _after_search():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                n = self.zxart_results_table.rowCount()
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, n)
                if on_done:
                    on_done()
            zxart_run_search(query, 1, _after_search)

        # ---- Tab badge helpers (multi-search result counts) ----

        def _tab_index(base_title: str) -> int:
            """Return the tab index whose text starts with base_title (ignores badge suffix)."""
            tw = self._tab_widget
            for i in range(tw.count()):
                if tw.tabText(i).startswith(base_title):
                    return i
            return -1

        def _set_tab_badge(base_title: str, count: int):
            idx = _tab_index(base_title)
            if idx >= 0:
                self._tab_widget.setTabText(idx, f"{base_title} ({count})")

        def _clear_tab_badge(base_title: str):
            idx = _tab_index(base_title)
            if idx >= 0:
                self._tab_widget.setTabText(idx, base_title)

        # ---- Tab spinner (animated progress while cross-search is running) ----
        _SPINNER_FRAMES = ["🌍", "🌎", "🌏", "🌐"]
        self._spinner_tabs: dict = {}   # base_title -> frame index
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(200)

        def _spinner_tick():
            for base_title in list(self._spinner_tabs.keys()):
                frame_idx = self._spinner_tabs[base_title]
                frame = _SPINNER_FRAMES[frame_idx % len(_SPINNER_FRAMES)]
                self._spinner_tabs[base_title] = frame_idx + 1
                idx = _tab_index(base_title)
                if idx >= 0:
                    self._tab_widget.setTabText(idx, f"{base_title} ({frame})")

        self._spinner_timer.timeout.connect(_spinner_tick)

        def _start_tab_spinner(base_title: str):
            self._spinner_tabs[base_title] = 0
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()

        def _stop_tab_spinner(base_title: str):
            self._spinner_tabs.pop(base_title, None)
            if not self._spinner_tabs:
                self._spinner_timer.stop()
            # Reset the tab text so the last spinner frame doesn't linger.
            # Callers that want a result badge will re-apply it via _set_tab_badge.
            _clear_tab_badge(base_title)

        # ---- Search-input placeholder animator (dancing "..." while an
        # autocomplete cache fetch is running). Multiple concurrent fetches
        # on the same input share the animation via a reference count.
        _AC_ANIM_FRAMES = [
            "...        ",
            " ...       ",
            "  ...      ",
            "   ...     ",
            "    ...    ",
            "     ...   ",
            "      ...  ",
            "       ... ",
            "      ...  ",
            "     ...   ",
            "    ...    ",
            "   ...     ",
            "  ...      ",
            " ...       ",
        ]
        self._ac_anim_state: dict = {}     # id(widget) -> state dict
        self._ac_anim_timer = QTimer(self)
        self._ac_anim_timer.setInterval(120)

        def _ac_anim_tick():
            for state in list(self._ac_anim_state.values()):
                w = state.get("widget")
                if w is None:
                    continue
                try:
                    frame = _AC_ANIM_FRAMES[state["frame"] % len(_AC_ANIM_FRAMES)]
                    state["frame"] += 1
                    w.setPlaceholderText(frame)
                except RuntimeError:
                    # Underlying C++ widget was destroyed; drop this entry.
                    self._ac_anim_state.pop(id(w), None)
                except Exception:
                    pass
            if not self._ac_anim_state:
                self._ac_anim_timer.stop()

        self._ac_anim_timer.timeout.connect(_ac_anim_tick)

        def _ac_anim_start(widget):
            if widget is None:
                return
            key = id(widget)
            state = self._ac_anim_state.get(key)
            if state is None:
                try:
                    original = widget.placeholderText()
                except Exception:
                    original = ""
                state = {"widget": widget, "original": original,
                         "refs": 0, "frame": 0}
                self._ac_anim_state[key] = state
            state["refs"] += 1
            if not self._ac_anim_timer.isActive():
                self._ac_anim_timer.start()

        def _ac_anim_stop(widget):
            if widget is None:
                return
            key = id(widget)
            state = self._ac_anim_state.get(key)
            if state is None:
                return
            state["refs"] -= 1
            if state["refs"] <= 0:
                try:
                    widget.setPlaceholderText(state.get("original", ""))
                except Exception:
                    pass
                self._ac_anim_state.pop(key, None)
            if not self._ac_anim_state:
                self._ac_anim_timer.stop()

        self._ac_anim_start = _ac_anim_start
        self._ac_anim_stop  = _ac_anim_stop

        def on_tab_changed(index):
            if self._initialising:
                return
            # Close any open completer popup so it doesn't linger after the
            # user switches to a different pane.
            for _c in (
                getattr(self, "_getit_completer",    None),
                getattr(self, "_zxdb_completer",     None),
                getattr(self, "_zxart_completer",    None),
                getattr(self, "_allinone_completer", None),
            ):
                if _c is not None:
                    try:
                        _c.popup().hide()
                    except Exception:
                        pass
            # If any pane is currently in fullscreen mode (stack index 1),
            # dismiss it before activating the new tab so the user always
            # lands on the gallery view of the destination pane.
            try:
                if self._getit_stack.currentIndex() == 1:
                    self._hide_fullscreen_getit()
            except Exception:
                pass
            try:
                if self._zxdb_stack.currentIndex() == 1:
                    self._hide_fullscreen_zxdb()
            except Exception:
                pass
            try:
                if self._zxart_stack.currentIndex() == 1:
                    self._hide_fullscreen_zxart()
            except Exception:
                pass
            tab_title = wid_inner.tab.tabText(index)
            # Only run the idle "breathing" glow on the transfer buttons while the
            # SD-card tab is the active one; stop it on every other tab.
            _stop_transfer_idle_animation()
            # The Remote Explorer sub-tab colour animation only runs while the
            # NextSync tab is visible; stop it here and (re)start it in the
            # NextSync branch below.
            self._re_tab_anim_set_active(False)
            if tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GOOEY):
                _start_transfer_idle_animation()
                # Re-tint the existing rows with the current item colors (the
                # user may have changed them in Settings). This is synchronous
                # and instant, independent of the async re-listing below.
                self._image_recolor_all()
                if right_disk_image_explorer_content:
                    # Refresh the explorer when returning to the SD Card tab. The
                    # listing runs on a worker thread (no UI-thread hdfmonkey call
                    # on tab switch).
                    update_disk_manager_widget_table()
            elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GETIT):
                _show_content_disclaimer()
                self._getit_fetch_motd()
                # Only fall back to "Latest" when the pane is genuinely empty
                # and no query is pending.  A query mirrored in from an
                # AllInOne multi-search (e.g. "lunar") must be preserved — its
                # background search may have returned few/zero rows, and we
                # must not clear the box or override it with latest releases.
                if (self.getit_results_table.rowCount() == 0
                        and not self._getit_search_loading
                        and not self.getit_search_input.text().strip()):
                    self._getit_on_latest()
            elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ZXDB):
                _show_content_disclaimer()
                self._zxdb_on_tab_activated()
            elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ZXART):
                _show_content_disclaimer()
                self._zxart_on_tab_activated()
            elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
                _show_content_disclaimer()
            elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC):
                # Now visible: animate the "Remote Explorer" sub-tab text.
                self._re_tab_anim_set_active(True)
                # Auto-run the "Prepare" step on entering the tab so the
                # "Start Classic NextSync server" button is ready without an extra
                # click. Guard on the prepare button still being visible so we
                # don't re-scan/re-log on every revisit or after a sync is set up.
                if self.nextsync_prepare_server.isVisible():
                    nextsync_perform_checks_and_prepare_server_start()


        #  Start main logic

        load_configuration_file()
        # Re-tint the tab bar with the just-loaded general UI text colour. This
        # covers Custom mode (whose theme re-apply returns early without
        # refreshing) and the Settings / itch.io tabs that are added after the
        # initial colouring pass, so every tab honours the saved colour.
        if hasattr(self, "_refresh_tab_stylesheet"):
            try:
                self._refresh_tab_stylesheet()
            except Exception:
                pass
        self._initialising = False

        def _apply_first_run_pygame_defaults():
            """On the first run (every pygame option still unset) default them all
            to ON when pygame is installed. A setting that was never saved stays
            "" (see the CONFIG_FILE_SETTINGS pre-seed), so only untouched options
            are flipped on; once the user saves a choice — including OFF — it is
            respected on later runs. Runs unconditionally (even with no config
            file, where load_configuration_file's restore block is skipped)."""
            try:
                from zxnu_pygame import pygame_available
                if not bool(pygame_available()[0]):
                    return
            except Exception:
                return

            def _unset(key):
                return str(configuration_dictionary.get(key, "")).strip() == ""

            # Each control's own toggled/stateChanged handler sets the config
            # value, persists it and applies the visual effect, so flipping the
            # control on (post-_initialising) is all that's needed. Each flip is
            # isolated in its own try/except so a failure building ONE pygame
            # surface can't abort the rest — in particular the NextSync retro
            # default must still apply if another pane's pygame init hiccups.
            def _first_run_check_on(key, attr):
                if not _unset(key):
                    return
                btn = getattr(self, attr, None)
                if btn is not None and btn.isEnabled() and not btn.isChecked():
                    try:
                        btn.setChecked(True)
                    except Exception:
                        logging.exception(
                            "First-run pygame default failed for %s", attr)

            _first_run_check_on(SETTING_ALLINONE_PYGAME_MODE, "allinone_pygame_button")
            _first_run_check_on(SETTING_NEXTSYNC_PYGAME_MODE, "nextsync_pygame_button")
            _first_run_check_on(SETTING_SDCARD_PYGAME_LOG,    "main_pygame_button")
            _first_run_check_on(SETTING_HELP_PYGAME_LOG,      "help_pygame_button")
            # Per-pane Classic/Retro item-viewer toggles (GetIt, ZXDB, zxArt,
            # itch.io, Favorites). Default them to Retro too so every gallery
            # pane opens items in the pygame viewer on first run, matching the
            # Unite! tab. Each button's toggled handler persists the choice and
            # applies the retro gallery scene, so flipping it on is enough.
            for _retro_key, _retro_btn_attr in (
                (SETTING_GETIT_ITEM_RETRO,     "getit_retro_button"),
                (SETTING_ZXDB_ITEM_RETRO,      "zxdb_retro_button"),
                (SETTING_ZXART_ITEM_RETRO,     "zxart_retro_button"),
                (SETTING_ITCHIO_ITEM_RETRO,    "itchio_retro_button"),
                (SETTING_FAVORITES_ITEM_RETRO, "favorites_retro_button"),
            ):
                _first_run_check_on(_retro_key, _retro_btn_attr)
            if _unset(SETTING_ALIEN_FLOYD_BG):
                cb = getattr(self, "settings_alien_floyd_bg_checkbox", None)
                if cb is not None and not cb.isChecked():
                    cb.setChecked(True)
            # The dedicated "Alien Floyd's" tab stays OFF by default on first run;
            # the user can enable it from Settings. (Its checkbox already defaults
            # to unchecked, so no first-run flip is applied here.)

        _apply_first_run_pygame_defaults()

        # Re-apply view modes now that config has been loaded (the per-pane setup
        # runs before load_configuration_file, so the combos/stacks need updating).
        self._getit_apply_view_mode(self._getit_view_mode, persist=False)
        self._zxdb_apply_view_mode(self._zxdb_view_mode,   persist=False)
        self._zxart_apply_view_mode(self._zxart_view_mode, persist=False)
        self._favorites_apply_view_mode(self._favorites_view_mode, persist=False)
        self._allinone_apply_view_mode(self._allinone_view_mode, persist=False)
        # itch.io is optional (built only when itch-dl is installed) and follows
        # the same shared view mode as the other online panes.
        if hasattr(self, "_itchio_apply_view_mode"):
            self._itchio_apply_view_mode(self._getit_view_mode, persist=False)

        # Connect tab-changed AFTER load so setCurrentIndex during config restore
        # does not trigger on_tab_changed before state is ready.
        wid_inner.tab.currentChanged.connect(on_tab_changed)

        # If the GetIt tab is already active after restoring config, trigger its
        # initialisation manually (currentChanged was not connected during load).
        # NOTE: defer to the event loop so widgets (especially Gallery cells)
        # are fully realised before any background fetcher emits queued signals
        # back into them. Running this synchronously inside __init__ launches
        # network threads before window.show()/event loop is up, and the queued
        # results land in half-constructed Qt widgets, causing access violations
        # on Windows when starting in Gallery mode.
        def _deferred_startup_tab_activation():
            # Always kick off the AllInOne "Latest" multi-search at startup so
            # the Unite! pane is populated with the latest releases, regardless
            # of which tab was restored from the configuration.
            try:
                _aio_latest = getattr(self, "_allinone_on_latest", None)
                if _aio_latest is not None:
                    _aio_latest()
            except Exception:
                pass
            try:
                current_title = wid_inner.tab.tabText(wid_inner.tab.currentIndex())
            except Exception:
                return
            if current_title == ZX_NEXT_UNITE_TAB_TITLE_GOOEY:
                # App restored onto the SD-card tab: kick off the idle glow now,
                # since currentChanged wasn't connected during config restore.
                _start_transfer_idle_animation()
            elif current_title == ZX_NEXT_UNITE_TAB_TITLE_GETIT:
                _show_content_disclaimer()
                self._getit_fetch_motd()
                # Preserve any pending query (e.g. mirrored from an AllInOne
                # multi-search) instead of clearing it with a "Latest" fetch.
                if (self.getit_results_table.rowCount() == 0
                        and not self._getit_search_loading
                        and not self.getit_search_input.text().strip()):
                    self._getit_on_latest()
            elif current_title == ZX_NEXT_UNITE_TAB_TITLE_ZXDB:
                _show_content_disclaimer()
                self._zxdb_on_tab_activated()
            elif current_title == ZX_NEXT_UNITE_TAB_TITLE_ZXART:
                _show_content_disclaimer()
                self._zxart_on_tab_activated()
            elif current_title == ZX_NEXT_UNITE_TAB_TITLE_ALLINONE:
                _show_content_disclaimer()
            elif current_title == ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC:
                # App restored directly onto the NextSync tab: start the Remote
                # Explorer sub-tab animation (currentChanged wasn't connected yet)
                # and auto-prepare so the Start button is ready.
                self._re_tab_anim_set_active(True)
                if self.nextsync_prepare_server.isVisible():
                    nextsync_perform_checks_and_prepare_server_start()

        # Use a small delay (not 0) so the first paint/show events have a
        # chance to be processed before any thumbnail fetch threads spin up.
        QTimer.singleShot(150, _deferred_startup_tab_activation)

        # Once the window is up and the config (enable flag + last installed MAME
        # tag) has loaded, check GitHub for a newer MAME release in the background
        # and offer to update if one is found. The helper self-gates: it no-ops
        # when MAME isn't installed, the check is disabled in Settings, or
        # automatic install isn't supported on this OS/CPU. The delay lets the
        # emulator-detection toast appear first so the two don't collide.
        QTimer.singleShot(1800, self._check_mame_update_async)

        # Likewise check itch.io for a newer CSpect build. This self-gates (needs
        # the check enabled, an API key configured, and an existing itch.io CSpect
        # install) and runs once per session. The itch.io tab's post-login
        # callback triggers the same check the moment a connection succeeds; this
        # timer is the fallback that also covers the case where the tab isn't
        # present but a key is saved. The later delay keeps it clear of the MAME
        # check and the emulator-detection toast.
        _check_cspect = getattr(self, "_check_cspect_update_async", None)
        if _check_cspect is not None:
            QTimer.singleShot(2600, _check_cspect)

        # The ".sync5 dot was updated" advisory (compares the bundled dotN
        # version with the one this cfg last saw) runs early — it concerns the
        # update the user JUST installed — and ZX Next Unite's own release
        # check runs last, staggered clear of the MAME/CSpect checks so their
        # prompts and log lines don't collide.
        QTimer.singleShot(1200, self._check_dotn_version_advisory)
        QTimer.singleShot(3400, self._check_zxnu_update_async)

        # Expose the nested save function so closeEvent (a class method) can call it.
        self._save_configuration_file = save_configuration_file

        def _warn_after_startup_load(success):
            if success:
                # Restore the in-image folder the disk image explorer targeted
                # when the app last ran (persisted on every selection change by
                # image_update_path_label). A path that no longer exists in
                # this image logs an advisory and stays at the image root — and
                # the not-found fallback re-persists "/", so the stale path is
                # forgotten rather than retried on every startup.
                _saved_image_dir = configuration_dictionary[SETTING_IMAGE_EXPLORERPATH]
                if _saved_image_dir and _saved_image_dir != "/":
                    image_navigate_to_path(_saved_image_dir)
            if success and self.settings_warn_image_nearly_full_checkbox.isChecked():
                _warn_if_image_nearly_full(self.right_disk_image_path)

        # Log the MAME discovery like the CSpect/hdfmonkey finds below — the
        # detection itself ran during construction (resolve_mame_executable),
        # before the log window existed, so nothing was printed for it. The
        # full path is logged now; the self-reported version follows once the
        # (blocking) 'mame -version' probe returns on a worker thread.
        _mame_found_path = getattr(self, "_mame_executable_path", None)
        if _mame_found_path:
            add_main_log_window(f"Using MAME under: {_mame_found_path}")

            def _mame_version_probe(progress_callback=None, _p=_mame_found_path):
                # First non-empty line of 'mame -version' output, e.g.
                # "0.278 (mame0278)". Older builds that don't know the option
                # print their usage banner instead, which also carries the
                # version — either way the first line is the informative one.
                try:
                    proc = subprocess.run(
                        [_p, "-version"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(_p) or None,
                        text=True, timeout=20,
                        **subprocess_no_window_kwargs())
                    for _line in (proc.stdout or "").splitlines():
                        if _line.strip():
                            return _line.strip()
                except Exception as exc:
                    logging.info(f"MAME version probe failed: {exc}")
                return ""

            def _mame_version_log(line):
                if line:
                    add_main_log_window(f"MAME version: {line}")

            _mame_ver_worker = self._Worker(_mame_version_probe)
            _mame_ver_worker.signals.result.connect(_mame_version_log)
            _mame_ver_worker.signals.finished.connect(
                lambda: setattr(self, "_mame_version_probe_worker", None))
            # Keep a strong reference until the probe returns, else Qt drops
            # the queued result when the unparented signals object is collected
            # (same pattern as the emulator scan worker below).
            self._mame_version_probe_worker = _mame_ver_worker
            self.threadpool.start(_mame_ver_worker)

        # Quiet probe: a bundled itch.io CSpect hdfmonkey isn't discovered until
        # the downloads/cspect scan below, so don't log a misleading "hdfmonkey
        # not found" error here when that scan may still turn one up.
        _hdfmonkey_present = is_hdfmonkey_present(silent=True)
        _startup_load_started = False
        if _hdfmonkey_present:
            load_image(_warn_after_startup_load)
            _startup_load_started = True
        else:
            # hdfmonkey isn't on PATH / in the app dir, but the full detection
            # below (a prior standalone install, alongside CSpect, or the
            # downloads/cspect scan) may still turn up a usable copy. Don't
            # surface the download button yet — only hide the image-write controls
            # for now. _finalize_hdfmonkey_button() reveals the download button
            # after the scan, and only if hdfmonkey still can't be located
            # anywhere. The jjjs auto-download covers every platform, so this is
            # no longer Windows-only.
            self.button_new_folder.setVisible(False)
            self.button_delete_files.setVisible(False)
            # MAME doesn't need hdfmonkey: with hdfmonkey absent load_image()
            # isn't run at startup, so nothing would otherwise enable the MAME
            # group even when MAME is present. Refresh it here (the background
            # scan re-affirms via show_hdf_monkey_download_and_install once
            # detection completes).
            _update_mame_controls()

        # Background scan of downloads/cspect for an itch.io CSpect bundle. Run
        # only when CSpect or hdfmonkey (either, any platform) is still missing —
        # itch.io installs land under downloads/cspect, possibly in a per-version
        # sub-folder. Kept off the UI thread because the recursive walk can be
        # slow. The emulator-detection toast waits for the result; when nothing
        # needs scanning it fires on a short timer as before.
        _need_cspect = self._cspect_executable_path is None
        # hdfmonkey may be bundled with the itch.io CSpect package on every
        # platform (Windows/Linux/macOS), so scan for it whenever it is missing.
        _need_hdfmonkey = not _hdfmonkey_present

        # A previous "install hdfmonkey only" auto-download leaves a build under
        # downloads/hdfmonkey/<platform>/; re-adopt it on launch (cheap isfile
        # checks) before the slower scans so it persists across restarts on every
        # platform.
        if _need_hdfmonkey:
            _dl_hdfmonkey = find_hdfmonkey_in_downloads(
                ZXNU_DATA_ROOT)
            if _dl_hdfmonkey:
                self._hdfmonkey_executable_path = _dl_hdfmonkey
                _need_hdfmonkey = False
                _hdfmonkey_present = True
                add_main_log_window(
                    f"Found previously installed hdfmonkey: {_dl_hdfmonkey}")
                self.download_and_install_hdfmonkey_button.setVisible(False)
                self.button_new_folder.setVisible(True)
                self.button_delete_files.setVisible(True)
                if not _startup_load_started:
                    load_image(_warn_after_startup_load)
                    _startup_load_started = True

        # When CSpect was already found (manual install on PATH or the
        # application directory) but hdfmonkey is still missing, CSpect ships an
        # hdfmonkey build alongside itself under hdfmonkey/<platform>/ — pick
        # that up directly. This is a couple of os.path.isfile checks, so it runs
        # synchronously here and saves the slower itch.io downloads scan below.
        if _need_hdfmonkey and self._cspect_executable_path:
            _near_hdfmonkey = find_hdfmonkey_near_cspect(self._cspect_executable_path)
            if _near_hdfmonkey:
                self._hdfmonkey_executable_path = _near_hdfmonkey
                _need_hdfmonkey = False
                add_main_log_window(f"Found hdfmonkey alongside CSpect: {_near_hdfmonkey}")
                self.download_and_install_hdfmonkey_button.setVisible(False)
                self.button_new_folder.setVisible(True)
                self.button_delete_files.setVisible(True)
                if not _startup_load_started:
                    load_image(_warn_after_startup_load)
                    _startup_load_started = True

        def _on_emulator_scan_done(result):
            self._emulator_scan_pending = False
            try:
                cspect_path, hdfmonkey_path = result
            except (TypeError, ValueError):
                cspect_path = hdfmonkey_path = None

            # Adopt a downloads/itchio CSpect when none is set yet, OR switch to a
            # newer one when the build currently in use also came from downloads
            # (find_emulators_in_downloads always returns the highest version, so
            # a differing path means a newer build appeared — e.g. after a CSpect
            # update). A PATH/app-dir CSpect (_cspect_from_downloads False) is left
            # alone so the standalone setup keeps working.
            _adopt_cspect = False
            if cspect_path:
                if not self._cspect_executable_path:
                    _adopt_cspect = True
                elif self._cspect_from_downloads and \
                        os.path.abspath(cspect_path) != \
                        os.path.abspath(self._cspect_executable_path):
                    _adopt_cspect = True
            if _adopt_cspect:
                self._cspect_executable_path = cspect_path
                self._cspect_from_downloads = True
                # The CSpect group was hidden at construction because no emulator
                # was found; reveal it now that a bundled copy exists.
                self.cspect_group.setVisible(True)
                add_main_log_window(f"Using CSpect under downloads/cspect: {cspect_path}")
                # Bind hdfmonkey to the copy bundled with THIS (newest) CSpect
                # build. Each itch.io CSpect ships its own hdfmonkey under
                # <build>/hdfmonkey/<platform>/, so after an update the matching
                # new hdfmonkey must replace the older build's lingering copy
                # (the reported bug). Only override when this build actually ships
                # one; otherwise keep whatever hdfmonkey was already found.
                _bundled_hdf = find_hdfmonkey_near_cspect(cspect_path)
                if _bundled_hdf and _bundled_hdf != self._hdfmonkey_executable_path:
                    self._hdfmonkey_executable_path = None  # let the block below adopt it
                    hdfmonkey_path = _bundled_hdf

            if hdfmonkey_path and not self._hdfmonkey_executable_path:
                self._hdfmonkey_executable_path = hdfmonkey_path
                add_main_log_window(f"Using hdfmonkey bundled with CSpect: {hdfmonkey_path}")
                # A bundled hdfmonkey makes the download/install wizard
                # unnecessary; hide it and restore the image controls.
                self.download_and_install_hdfmonkey_button.setVisible(False)
                self.button_new_folder.setVisible(True)
                self.button_delete_files.setVisible(True)
                # Load the image now if startup couldn't (hdfmonkey was missing).
                if not _startup_load_started:
                    load_image(_warn_after_startup_load)

            # A CSpect adopted from the scan must unlock its option combos even
            # when no load_image() runs here (e.g. no bundled hdfmonkey, or the
            # startup load already happened) — load_image is what refreshes them
            # otherwise.
            _update_cspect_controls()
            # An emulator found only now (after the startup load already ran
            # with no image) still deserves the "select a disk image" advisory
            # and the yellow pulse on the image-picking buttons.
            _maybe_show_no_image_toast()
            _start_load_image_hint_animation()
            self._show_emulator_detection_toast()

        def _finalize_hdfmonkey_button():
            """Reveal the 'Download and install HDF Monkey' button only after the
            full detection has run (PATH, current/app dir, a prior standalone
            install, alongside CSpect, and the downloads/cspect scan) and
            hdfmonkey still can't be located. Offered on every platform now that
            the jjjs auto-download ships builds for Windows, Linux and macOS.
            No-op when a copy was found — the scan handlers have already restored
            the image controls in that case."""
            if _hdfmonkey_binary_found():
                return
            show_hdf_monkey_download_and_install_buttons()

        def _rescan_emulators_after_install():
            """Re-run CSpect / hdfmonkey detection on demand (used after an
            itch.io CSpect install) so a freshly downloaded emulator becomes
            usable without restarting the app. Reuses _on_emulator_scan_done to
            apply results and show the detection toast.

            When the CSpect in use already came from downloads/itchio we re-scan
            even though one is set, so installing/updating to a newer build
            switches to it (and to that build's bundled hdfmonkey) — a PATH/
            app-dir CSpect is left untouched so standalone setups keep working."""
            need_cspect = (self._cspect_executable_path is None
                           or self._cspect_from_downloads)
            need_hdfmonkey = (self._hdfmonkey_executable_path is None
                              and not is_hdfmonkey_present(silent=True))
            # CSpect ships an hdfmonkey build alongside itself; prefer that cheap
            # check before the slower downloads walk.
            if need_hdfmonkey and self._cspect_executable_path:
                near = find_hdfmonkey_near_cspect(self._cspect_executable_path)
                if near:
                    self._hdfmonkey_executable_path = near
                    need_hdfmonkey = False
                    add_main_log_window(f"Found hdfmonkey alongside CSpect: {near}")
                    try:
                        self.download_and_install_hdfmonkey_button.setVisible(False)
                        self.button_new_folder.setVisible(True)
                        self.button_delete_files.setVisible(True)
                    except RuntimeError:
                        pass
            if not (need_cspect or need_hdfmonkey):
                # Nothing left to find; just re-affirm what is available.
                self._show_emulator_detection_toast()
                return
            _dir = ZXNU_DATA_ROOT

            def _rescan(progress_callback=None):
                return find_emulators_in_downloads(
                    _dir, scan_for_cspect=need_cspect,
                    scan_for_hdfmonkey=need_hdfmonkey)

            def _rescan_finished_fallback():
                if self._emulator_scan_pending:
                    self._emulator_scan_pending = False
                    self._show_emulator_detection_toast()
                self._emulator_scan_worker = None

            self._emulator_scan_pending = True
            _worker = self._Worker(_rescan)
            _worker.signals.result.connect(_on_emulator_scan_done)
            _worker.signals.finished.connect(_rescan_finished_fallback)
            self._emulator_scan_worker = _worker
            self.threadpool.start(_worker)

        # Expose the rescan so the itch.io install flow can re-detect emulators
        # right after a CSpect download/extract.
        self._rescan_emulators_after_install = _rescan_emulators_after_install

        def _rescan_emulators_after_uninstall(removed_path=None):
            """Re-detect emulators after an itch.io emulator package (CSpect) is
            uninstalled. The install-time rescan only ever *adds* a found build;
            it never drops one. So here we first forget any CSpect/hdfmonkey path
            that lived inside the now-deleted *removed_path* (restoring the
            "not found" UI), then re-run detection so a different copy — a
            PATH-installed CSpect or another downloads build — is picked up if
            one exists."""
            def _under(p, root):
                if not p or not root:
                    return False
                try:
                    p = os.path.abspath(p)
                    root = os.path.abspath(root)
                    return os.path.commonpath([p, root]) == root
                except (ValueError, OSError):
                    return False

            cleared_cspect = False
            if _under(self._cspect_executable_path, removed_path):
                self._cspect_executable_path = None
                self._cspect_from_downloads = False
                cleared_cspect = True
                # The group was revealed when CSpect was found; hide it again now
                # that the build is gone.
                try: self.cspect_group.setVisible(False)
                except RuntimeError: pass
            if _under(self._hdfmonkey_executable_path, removed_path):
                self._hdfmonkey_executable_path = None

            # A PATH-installed CSpect is a cheap fallback the downloads-only
            # rescan below won't find; restore it (and its controls) if present.
            if cleared_cspect:
                try:
                    _path_cspect = find_cspect_executable()
                except Exception:
                    _path_cspect = None
                if _path_cspect:
                    self._cspect_executable_path = _path_cspect
                    self._cspect_from_downloads = False
                    try: self.cspect_group.setVisible(True)
                    except RuntimeError: pass

            # Re-run detection for whatever is still missing (walks downloads),
            # then re-show the hdfmonkey download button (any platform) if no copy
            # remains anywhere.
            _rescan_emulators_after_install()
            if not _hdfmonkey_binary_found():
                try:
                    show_hdf_monkey_download_and_install_buttons()
                except Exception:
                    pass

        self._rescan_emulators_after_uninstall = _rescan_emulators_after_uninstall

        if _need_cspect or _need_hdfmonkey:
            self._emulator_scan_pending = True
            _app_dir = ZXNU_DATA_ROOT

            def _scan_downloads(progress_callback=None):
                # Wrapper so the generic Worker's injected progress_callback kwarg
                # is absorbed; find_emulators_in_downloads is a pure helper.
                return find_emulators_in_downloads(
                    _app_dir, scan_for_cspect=_need_cspect,
                    scan_for_hdfmonkey=_need_hdfmonkey)

            def _scan_finished_fallback():
                # Ensure the toast still appears if the worker errored before
                # emitting a result (result fires first on success, so this is a
                # no-op then). Also drop the strong reference kept below so the
                # worker and its signals can be collected now that it is done.
                if self._emulator_scan_pending:
                    self._emulator_scan_pending = False
                    self._show_emulator_detection_toast()
                self._emulator_scan_worker = None
                # The full detection has now finished: show the hdfmonkey
                # download button only if no copy could be located anywhere.
                _finalize_hdfmonkey_button()

            _scan_worker = self._Worker(_scan_downloads)
            _scan_worker.signals.result.connect(_on_emulator_scan_done)
            _scan_worker.signals.finished.connect(_scan_finished_fallback)
            # Keep the worker (and therefore its WorkerSignals) alive until it
            # finishes. self._Worker creates an unparented WorkerSignals; without
            # a strong reference here the local _scan_worker is collected as soon
            # as __init__ returns, and Qt then discards the still-queued result/
            # finished events (sender destroyed) — so the scan results and the
            # detection toast would silently never arrive. Same pattern as the
            # NextSync scan worker retention above.
            self._emulator_scan_worker = _scan_worker
            self.threadpool.start(_scan_worker)
        else:
            # Nothing to scan — report detected emulators via a 5-second toast
            # (green when found, yellow advisory when none are available),
            # deferred so it appears after the window is shown.
            QTimer.singleShot(400, self._show_emulator_detection_toast)

        # The image listing above now runs asynchronously and manages the path
        # label itself ("Loading image…" while in flight, then the real path or an
        # error). Only override the label when no load was kicked off (or there is
        # no image to load), so we don't clobber the in-flight "Loading image…".
        _startup_image_path = self.imageinput.currentText()
        if not (_startup_load_started and _startup_image_path and _startup_image_path != '""'):
            self.diskimageexplorerpathinput.setText("Please load an image.")

        nextsync_show_ip_info()
        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()

        # Apply the UI language to the fully-built widget tree (English is a
        # no-op). Runs at the very end of __init__ so every tab and pane
        # already exists; texts generated later (logs, dialogs) stay English
        # until their call sites adopt zxnu_i18n.ui_tr.
        #
        # First run (ui_language never saved — blank, the same convention as
        # _apply_first_run_pygame_defaults): adopt the OS language when it is
        # one of the shipped six, else English, persist the choice once, and
        # tell the user with a 15 s bottom-left toast (in THAT language) that
        # points at the Settings tab. Later runs just apply the saved value.
        _ui_lang_saved_raw = str(
            configuration_dictionary.get(SETTING_UI_LANGUAGE, "") or "").strip()
        _ui_language = normalize_ui_language(_ui_lang_saved_raw)
        if not _ui_lang_saved_raw:
            _ui_language = system_ui_language()
            configuration_dictionary[SETTING_UI_LANGUAGE] = _ui_language
            save_configuration_file()   # one-time adoption (never re-runs)
            if _ui_language != "en":
                add_main_log_window(
                    f"UI language set to '{_ui_language}' to match the system "
                    "language — change it on the Settings tab.")
                _lang_toast_title = ui_tr(
                    "🌐  Language set to match your system", _ui_language)
                _lang_toast_body = ui_tr(
                    "The interface language was set to match your system "
                    "language.\nYou can change it anytime in the Settings tab "
                    "(\"Application language:\").", _ui_language)
                # Deferred like the no-image advisory so it positions against
                # the shown window.
                QTimer.singleShot(1200, lambda: self._show_toast(
                    _lang_toast_title, _lang_toast_body, variant="green",
                    duration_ms=15000, corner="bottom-left"))
        if _ui_language != "en":
            # Point the Settings combo at the language too (signals blocked:
            # this is a restore, not a user change to persist/re-apply).
            _ui_lang_combo = getattr(self, "settings_ui_language_combo", None)
            if _ui_lang_combo is not None:
                _ui_lang_ix = _ui_lang_combo.findData(_ui_language)
                if _ui_lang_ix >= 0 and _ui_lang_combo.currentIndex() != _ui_lang_ix:
                    _ui_lang_combo.blockSignals(True)
                    try:
                        _ui_lang_combo.setCurrentIndex(_ui_lang_ix)
                    finally:
                        _ui_lang_combo.blockSignals(False)
            self._i18n_apply(_ui_language)

"""
    Main application loop
"""

# closeEvent is defined here (outside __init__) so it is a real class method
def _mainwindow_close_event(self, event):
    """Save the active tab and all settings when the user closes the window."""
    if hasattr(self, '_save_configuration_file'):
        self._save_configuration_file()
    super(MainWindow, self).closeEvent(event)

MainWindow.closeEvent = _mainwindow_close_event


def _mainwindow_reposition_ac_popups(self):
    """Reposition any visible autocomplete popups beneath their search input.

    The QCompleter popups are top-level Qt.Tool windows positioned manually
    with popup.move(). When the main window is moved, Qt does not relocate
    them, so they stay stuck at their original screen coordinates. This
    re-anchors each visible popup to the bottom-left of its line edit.
    """
    pairs = (
        ("_getit_completer",    "getit_search_input"),
        ("_zxdb_completer",     "zxdb_search_input"),
        ("_zxart_completer",    "zxart_search_input"),
        ("_allinone_completer", "allinone_search_input"),
    )
    for completer_attr, input_attr in pairs:
        try:
            completer = getattr(self, completer_attr, None)
            line_edit = getattr(self, input_attr, None)
            if completer is None or line_edit is None:
                continue
            popup = completer.popup()
            if popup is None or not popup.isVisible():
                continue
            pos = line_edit.mapToGlobal(line_edit.rect().bottomLeft())
            popup.move(pos)
        except RuntimeError:
            pass
        except Exception:
            pass


# moveEvent is defined here (outside __init__) so it is a real class method
def _mainwindow_move_event(self, event):
    """Keep visible autocomplete popups anchored to their input, and live
    toasts anchored to the bottom-right corner, when the main window is
    dragged across the screen."""
    super(MainWindow, self).moveEvent(event)
    _mainwindow_reposition_ac_popups(self)
    self._reposition_toasts()

MainWindow.moveEvent = _mainwindow_move_event


# resizeEvent is defined here (outside __init__) so it is a real class method
def _mainwindow_resize_event(self, event):
    """Re-anchor visible autocomplete popups and live toasts when the window
    is resized, as that also shifts their anchor positions."""
    super(MainWindow, self).resizeEvent(event)
    _mainwindow_reposition_ac_popups(self)
    self._reposition_toasts()

MainWindow.resizeEvent = _mainwindow_resize_event

import signal

# On Linux, Qt's AT-SPI accessibility bridge prints a harmless but noisy
# warning at startup when the desktop's accessibility bus is incomplete:
#   AtSpiAdaptor::applicationInterface does not implement
#   "GetApplicationBusAddress" "/org/a11y/atspi/accessible/root"
# Disable the bridge so that message doesn't clutter the console. Only do so
# when the user hasn't set QT_ACCESSIBILITY themselves (e.g. someone relying on
# a screen reader can keep it on by exporting QT_ACCESSIBILITY=1). Must run
# before QApplication is constructed, as Qt reads this during startup.
if platform.system() == "Linux" and "QT_ACCESSIBILITY" not in os.environ:
    os.environ["QT_ACCESSIBILITY"] = "0"


# ── optional --anim test override ────────────────────────────────────────────
# `python zx-next-unite.py --anim <walk|c5|ufo|aliens>` forces the retro Sir
# Clive promenade (Alien Floyd mode) to play that animation first — and enables
# it without toggling the Settings preference — so the C5, flying-saucer and
# alien-dogfight animations can be eyeballed quickly.  Requires pygame (which is
# mandatory for any of the animations); the flag is a no-op / warning otherwise.
def _zxnu_parse_anim_arg():
    kind = None
    rest = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--anim" and i + 1 < len(sys.argv):
            kind = sys.argv[i + 1]
            i += 2
            continue
        if a.startswith("--anim="):
            kind = a.split("=", 1)[1]
            i += 1
            continue
        rest.append(a)
        i += 1
    if kind is None:
        return
    # Strip our flag so QApplication doesn't try to interpret it.
    sys.argv[:] = rest
    try:
        import zxnu_pygame as _zpg
        ok, why = _zpg.pygame_available()
        if not ok:
            print("--anim ignored: %s pygame is required for the animations."
                  % why, file=sys.stderr)
            return
        if kind not in _zpg._FORCE_ANIM_CHOICES:
            print("--anim: unknown animation %r; choose one of: %s"
                  % (kind, ", ".join(_zpg._FORCE_ANIM_CHOICES)),
                  file=sys.stderr)
            return
        _zpg.set_forced_first_anim(kind)
        print("--anim: forcing the '%s' promenade animation to play first."
              % kind)
    except Exception as exc:                       # pragma: no cover - defensive
        print("--anim setup failed: %s" % exc, file=sys.stderr)


_zxnu_parse_anim_arg()


# ── optional -start-remote-explorer-listener switch ──────────────────────────
# `python zx-next-unite.py -start-remote-explorer-listener` opens the NextSync
# tab's Remote Explorer view and starts its '.sync5 -listen' server right at
# startup, using the saved sync root — so a Next can run '.sync5 -listen' and
# connect without a single click (pairs nicely with the HTTP bridge Settings
# toggle for a fully remote setup). The view is forced open for this run only;
# the saved Settings are not modified. Without a saved sync root the server
# cannot start and the usual "pick a sync root folder first" advisory is
# logged instead.
_ZXNU_START_RE_LISTENER = False


def _zxnu_parse_start_re_listener_arg():
    global _ZXNU_START_RE_LISTENER
    rest = [a for a in sys.argv
            if a not in ("-start-remote-explorer-listener",
                         "--start-remote-explorer-listener")]
    if len(rest) != len(sys.argv):
        _ZXNU_START_RE_LISTENER = True
        # Strip our flag so QApplication doesn't try to interpret it.
        sys.argv[:] = rest
        print("-start-remote-explorer-listener: starting the Remote Explorer "
              "'.sync5 -listen' server at startup.")


_zxnu_parse_start_re_listener_arg()

app = QApplication(sys.argv)

# Remove the 256 MB image allocation cap so that large zxART images
# (which Qt rejects by default) are loaded without the
# "QImageIOHandler: Rejecting image" warning.
QImageReader.setAllocationLimit(0)

# Suppress a Qt-internal warning
# constructs a QFont from CSS that has no explicit point/pixel size (the
# font inherits a pixel-size-only font and Qt resolves it as -1pt).
# This is a known Qt bug; the label still renders correctly.
# Suppress known Qt and libpng warnings that are harmless and clutter the console.
# "Point size <= 0" is a Qt bug when a font inherits a pixel-only size.
# "libpng warning:" covers the family of harmless libpng decode warnings (e.g.
# "hIST: out of place", "bKGD: invalid", "iCCP: known incorrect sRGB profile")
# emitted by Qt's PNG handler when decoding images downloaded from GetIt / ZXDB
# / ZXArt that carry malformed ancillary chunks.  libpng recovers and the image
# still renders correctly, so the whole family is silently ignored.
# "AtSpiAdaptor" covers the harmless Linux AT-SPI accessibility-bridge warning
# ('AtSpiAdaptor::applicationInterface does not implement
# "GetApplicationBusAddress" …') printed at startup when the desktop's
# accessibility bus is incomplete.  Disabling the bridge via QT_ACCESSIBILITY
# does not reliably stop it on every distro, so it is filtered here too.
_QT_SUPPRESS_MSGS = ("Point size <= 0", "libpng warning:", "AtSpiAdaptor",
                     # "QBasicTimer::start: Timers cannot be started from another
                     # thread" (and the "QObject::startTimer:" variant) - noise
                     # from Qt objects whose timers are poked off the GUI thread.
                     "Timers cannot be started from another thread")
def _qt_message_handler(msg_type, context, message):
    if any(s in message for s in _QT_SUPPRESS_MSGS):
        return
    # Mirror Qt log messages into the crash log so windowed-mode builds can
    # surface plugin / image-format / font issues that would otherwise be
    # invisible (sys.stderr is None when packaged with --windowed).
    try:
        if _ZXNU_CRASH_FH is not None:
            _ZXNU_CRASH_FH.write("[Qt] %s\n" % message)
    except Exception:
        pass
    import sys as _sys
    if _sys.stderr is not None:
        print(message, file=_sys.stderr)
qInstallMessageHandler(_qt_message_handler)
_app_font = QFont("Consolas")
_app_font.setStyleHint(QFont.StyleHint.Monospace)
# Ensure the application font always has a valid positive point size so that
# widgets which inherit it and then call font().pointSize() never receive -1
# (which happens when only pixelSize or no size is set on the QFont).
_resolved_ps = QFontInfo(_app_font).pointSize()
if _resolved_ps > 0:
    _app_font.setPointSize(_resolved_ps)
else:
    _app_font.setPointSize(10)
app.setFont(_app_font)

window = MainWindow()
window.show()

# On exit — window "X", Ctrl-C, or any other app.quit() — wind both NextSync
# servers down softly so no half-written file is left on the Next:
#   * classic sync server: request the graceful cancel (the server finishes
#     the file currently in flight, tells the Next there is nothing more to
#     sync and persists the sync point) and give it a bounded wait to get
#     there — the daemon thread would otherwise die abruptly mid-transfer
#     with the process;
#   * Remote Explorer "-listen" session: drop queued commands, let the
#     in-flight transfer finish, then deliver "Q" so the Next leaves listen
#     mode and closes its own connection instead of sitting on the socket.
# aboutToQuit is the single choke point for every quit path; the shutdown is
# idempotent, so calling it more than once is harmless.
def _graceful_nextsync_shutdown():
    try:
        t = getattr(window, "_nextsync_thread", None)
        flag = getattr(window, "_nextsync_cancel_flag", None)
        if t is not None and t.is_alive() and flag is not None:
            flag.set()
            t.join(timeout=10.0)
    except Exception:
        # Shutdown is best-effort but must be diagnosable: a failure here can
        # leave a sync server without its clean goodbye to the Next.
        logging.exception("Graceful shutdown: classic sync server stop failed")
    try:
        fn = getattr(window, "_nextsync_stop_listen_server_fn", None)
        if callable(fn):
            fn()
    except Exception:
        logging.exception("Graceful shutdown: Remote Explorer listen-server stop failed")

app.aboutToQuit.connect(_graceful_nextsync_shutdown)

# Allow Ctrl-C (SIGINT) to terminate the application cleanly.
# Qt's event loop blocks Python signal delivery unless we periodically
# yield back to the Python interpreter via a no-op timer.
def _handle_sigint(*_args):
    print("\nInterrupted — exiting.", flush=True)
    # Wind the sync servers down now, before teardown, so the goodbye reaches
    # the Next even if the aboutToQuit slots don't get a chance to run during
    # interpreter shutdown.
    _graceful_nextsync_shutdown()
    # Close the window the way the "X" button would BEFORE quitting: the
    # animation timers keep queueing repaints (and the shutdown joins above
    # block the loop, letting them pile up), and flushing those against the
    # window during interpreter teardown — after its native handle is gone —
    # is what intermittently printed "QBackingStore::flush() called for
    # QWidgetWindow ... which does not have a handle" on Ctrl-C. Closing hides
    # the window (pending paints are discarded while the handle still exists)
    # and runs closeEvent, so a Ctrl-C exit also saves the configuration like
    # a normal close.
    try:
        window.close()
    except Exception:
        pass
    app.quit()

signal.signal(signal.SIGINT, _handle_sigint)

_sigint_timer = QTimer()
_sigint_timer.setInterval(200)   # check every 200 ms
_sigint_timer.timeout.connect(lambda: None)   # no-op; just wakes Python
_sigint_timer.start()

# Catalog prefetch disabled — zxart_client_search now uses a direct
# server-side title filter, so no upfront catalog download is needed.
# _zxart_prefetch_cache_if_stale()

sys.exit(app.exec())

