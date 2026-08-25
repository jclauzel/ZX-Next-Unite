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
    QLoggingCategory,
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
from zxnu_emulator_ops import (build_emulator_ops, build_hdfmonkey_install_ops)
from zxnu_config_io import build_config_io
from zxnu_nextsync_ops import (build_nextsync_server_start,
    build_nextsync_explorer_ops, build_nextsync_server_job)
from zxnu_sdcard_ops import (build_sdcard_utils, build_image_edit_ops,
    build_local_explorer_ops, build_transfer_clipboard_ops)
from zxnu_tab_ops import build_tab_ops
from zxnu_retro_ui import (build_main_retro_log, build_sidebar_anim,
    build_help_retro_log, build_content_disclaimer)
from zxnu_wizard import build_wizard
from zxnu_network import build_network_watch
from zxnu_favorites_pane import (build_favorites_helpers,
    build_favorites_pane, build_favorites_ops)
from zxnu_i18n import (mark_combo_items_translatable, normalize_ui_language,
                       system_ui_language, translate_widget_tree, ui_tr,
                       ui_tr_now)
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
            btn.setText(ui_tr_now("🖼 Switch to 'Classic' view mode"))
        else:
            setattr(window, flag_attr, False)
            btn.setText(ui_tr_now("🎮 Retro"))
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


class _ImagePathCombo(QComboBox):
    """The SD-image path box. showPopup stamps WHEN the history dropdown
    opened, so the activation wiring can tell a real pick from the Windows
    "phantom activation" — the very click that opens the dropdown also
    "activating" the current entry when the (long-path-widened) popup lands
    under the cursor. Reported as: the history list appeared and instantly
    vanished while the already-loaded image reloaded. See the
    imageinput.activated wiring in MainWindow.setupUI."""

    def showPopup(self):
        self._popup_shown_at = time.monotonic()
        super().showPopup()


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
            # After a fresh itch.io CSpect install (one-shot flag), run the
            # Windows-only OpenAL 1.1 check \u2014 CSpect has no sound on Windows
            # without it (Linux/macOS ship OpenAL, so it's skipped there).
            # is_openal_installed() is privilege-free (system DLL + read-only
            # registry scan), so when the runtime IS present the user just
            # gets a quiet confirmation \u2014 the warning toast and the guided
            # install offer (_offer_openal_install, zxnu_emulator_ops) only
            # appear when it is genuinely missing.
            _openal = (self._cspect_openal_notice_pending
                       and "CSpect" in found
                       and platform.system() == "Windows")
            self._cspect_openal_notice_pending = False
            if _openal and not is_openal_installed():
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
                # The toast alone is easy to miss \u2014 actively offer the guided
                # install (download oalinst.zip + run the official installer).
                # Deferred a beat so the toast paints before the modal opens.
                QTimer.singleShot(400, self._offer_openal_install)
            elif _openal:
                self.add_main_log_window(
                    "OpenAL: runtime detected \u2014 CSpect sound is ready.")
                body += "\r\n\r\n" + ui_tr_now(
                    "OpenAL 1.1 detected \u2014 CSpect sound is ready.")
                self._show_toast(
                    "\u2705  CSpect installed",
                    body,
                    variant="green",
                    duration_ms=8000,
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
        # Onboarding Wizard assistant (zxnu_wizard.py): shown by default;
        # the first-run introduction plays once. Seeded so the config writer
        # never KeyErrors on these new keys.
        configuration_dictionary[SETTING_WIZARD_ENABLED] = ""
        configuration_dictionary[SETTING_WIZARD_INTRO_SHOWN] = ""
        configuration_dictionary[SETTING_WIZARD_FONT_SIZE] = ""
        configuration_dictionary[SETTING_WIZARD_SP_OFFERED] = ""

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
                    self.button_start_cspect.setToolTip(ui_tr_now(
                        "Load a ZX Spectrum Next disk image first — then CSpect "
                        "can boot it from the mounted SD card."))
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
                    self.button_start_mame.setToolTip(ui_tr_now(
                        "Select a ZX Spectrum Next disk image (.img/.hdf) first "
                        "— then MAME can boot it as the Next's hard disk."))
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
            # Same state, a second surface: the Remote Explorer's emulator
            # strip is built from _mame_usable() too.
            if hasattr(self, "_re_refresh_emulators"):
                self._re_refresh_emulators()
        self._refresh_mame_launch_ui = _refresh_mame_launch_ui

        def enable_image_selection():
            self.imageinput.setDisabled(False)
            self.selectimage.setDisabled(False)
            # The LOCAL explorer (left pane) needs no disk image: browsing the
            # PC, filtering, zip/unzip and the "Start <emulator> with <file>"
            # actions all work on their own. It is only greyed out here because
            # set_all_buttons_disabled() is the same blunt lock used during
            # transfers, so re-enable the local half in this resting state —
            # otherwise no image (or no hdfmonkey) leaves the whole left pane
            # dead and those actions unreachable.
            # Everything image-side (the image tree, both transfer buttons, the
            # in-image new-folder/rename/delete controls) deliberately stays
            # disabled: there is nothing yet for them to act on.
            for _local_widget in (self.zx_next_unite_diskdrive, self.filterlabel,
                                  self.filtertext, self.treeview,
                                  self.local_explorer_up_button,
                                  self.local_explorer_refresh_button):
                _local_widget.setDisabled(False)
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

        # ── hdfmonkey download/install chain (extracted to
        # zxnu_emulator_ops.py::build_hdfmonkey_install_ops): jjjs zip
        # download + per-platform extract, manual-download fallback, binary
        # discovery and the missing-hdfmonkey prompt signal wiring.
        build_hdfmonkey_install_ops(
            self,
            _update_mame_controls=_update_mame_controls,
            _update_cspect_controls=_update_cspect_controls,
            _start_hdfmonkey_button_animation=lambda *a, **k: _start_hdfmonkey_button_animation(*a, **k),
            _stop_hdfmonkey_button_animation=lambda *a, **k: _stop_hdfmonkey_button_animation(*a, **k),
            load_image=lambda *a, **k: load_image(*a, **k),
            add_main_log_window=lambda *a, **k: add_main_log_window(*a, **k),
        )
        _hdfmonkey_binary_found = self._hdfmonkey_binary_found
        show_hdf_monkey_download_and_install_buttons = (
            self.show_hdf_monkey_download_and_install_buttons)
        _on_hdfmonkey_button_clicked = self._on_hdfmonkey_button_clicked


        # def tab_changed():
        #     # Do nothing for now has this event happens before rendering the tab
        #     # get_pyhdfmgooey_currenttab_config()

        # ── Config-file I/O (extracted to zxnu_config_io.py): the whole
        # hdfg.cfg restore pipeline (load_configuration_file) and the guarded
        # writer (save_configuration_file). Helpers defined later in __init__
        # arrive as forwarding lambdas; wid_inner and the module-global
        # -start-remote-explorer-listener flag are read via getter hooks.
        build_config_io(
            self,
            configuration_dictionary=configuration_dictionary,
            get_int_value=get_int_value,
            _zxnu_set_crash_log_enabled=_zxnu_set_crash_log_enabled,
            _zxnu_start_re_listener=lambda: _ZXNU_START_RE_LISTENER,
            _wid_inner=lambda: wid_inner,
            get_pyhdfmgooey_currenttab_config=lambda *a, **k: get_pyhdfmgooey_currenttab_config(*a, **k),
            add_main_log_window=lambda *a, **k: add_main_log_window(*a, **k),
            add_nextsync_log_window=lambda *a, **k: add_nextsync_log_window(*a, **k),
            local_sync_path_box=lambda *a, **k: local_sync_path_box(*a, **k),
            _nextsync_update_set_syncroot_button=lambda *a, **k: _nextsync_update_set_syncroot_button(*a, **k),
        )
        load_configuration_file = self.load_configuration_file
        save_configuration_file = self.save_configuration_file

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

            # ---- the last-look UX capture (9.5.14): the monitor, window
            # size and explorer column widths as they stand RIGHT NOW, so
            # the closeEvent save carries the shutdown truth and the next
            # start restores the look the user left. The Remote Explorer
            # widget is built lazily — while it has never been opened this
            # run, its previously saved widths survive untouched (the keys
            # are only overwritten when a live tree can answer).
            try:
                _win = self.window()
                _handle = _win.windowHandle()
                _scr = _handle.screen() if _handle is not None else None
                if _scr is not None:
                    configuration_dictionary[SETTING_WINDOW_SCREEN] = _scr.name()
                configuration_dictionary[SETTING_WINDOW_SIZE] = (
                    f"{_win.width()}x{_win.height()}")
            except RuntimeError:
                pass                    # window mid-teardown: keep saved

            def _tree_cols(_tree):
                if _tree is None or _tree.model() is None:
                    return None
                _hdr = _tree.header()
                return ",".join(str(_hdr.sectionSize(_i))
                                for _i in range(_hdr.count()))

            _re_w = getattr(self, "_re_widget", None)
            for _cols_key, _cols_tree in (
                (SETTING_SDCARD_TREE_COLS, getattr(self, "treeview", None)),
                (SETTING_IMAGE_TREE_COLS,
                 getattr(self, "image_treeview", None)),
                (SETTING_RE_LOCAL_COLS,
                 getattr(_re_w, "local_view", None) if _re_w else None),
                (SETTING_RE_NEXT_COLS,
                 getattr(_re_w, "next_view", None) if _re_w else None),
            ):
                try:
                    _cols_val = _tree_cols(_cols_tree)
                except RuntimeError:
                    _cols_val = None    # widget mid-teardown: keep saved
                if _cols_val:
                    configuration_dictionary[_cols_key] = _cols_val
            #save_configuration_file()

        # ── Emulator ops (extracted to zxnu_emulator_ops.py): the CSpect/MAME
        # settings setters + launchers, the MAME install/update chain, the
        # zx-next-unite self-update chain, the .sync dotN version advisory,
        # the CSpect update chain and the item-viewer emulator wiring.
        build_emulator_ops(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
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


        # Setter hooks for the module globals the extracted SD-card ops
        # rewrite (reads go through the matching _right_disk_content getter).
        def _set_right_disk_content(value):
            global right_disk_image_explorer_content
            right_disk_image_explorer_content = value

        def _set_right_disk_selected(value):
            global right_disk_image_selected_files
            right_disk_image_selected_files = value

        # ── SD-card utils + image-load pipeline (extracted to
        # zxnu_sdcard_ops.py). Closures bound later in __init__ arrive as
        # forwarding lambdas.
        build_sdcard_utils(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            set_all_buttons_disabled=set_all_buttons_disabled,
            set_all_buttons_enabled=set_all_buttons_enabled,
            enable_image_selection=enable_image_selection,
            _hdfmonkey_binary_found=_hdfmonkey_binary_found,
            _right_disk_content=lambda: right_disk_image_explorer_content,
            _set_right_disk_content=_set_right_disk_content,
            _set_right_disk_selected=_set_right_disk_selected,
            image_confirm_deletion_dialog=lambda *a, **k: image_confirm_deletion_dialog(*a, **k),
            image_delete_files=lambda *a, **k: image_delete_files(*a, **k),
            _nextsync_update_set_syncroot_button=lambda *a, **k: _nextsync_update_set_syncroot_button(*a, **k),
            generate_disk_file_path=lambda *a, **k: generate_disk_file_path(*a, **k),
            image_clear_model=lambda *a, **k: image_clear_model(*a, **k),
            image_load_root=lambda *a, **k: image_load_root(*a, **k),
            update_disk_manager_widget_table=lambda *a, **k: update_disk_manager_widget_table(*a, **k),
        )
        _maybe_show_no_image_toast = self._maybe_show_no_image_toast
        _start_hdfmonkey_button_animation = self._start_hdfmonkey_button_animation
        _start_load_image_hint_animation = self._start_load_image_hint_animation
        _start_transfer_idle_animation = self._start_transfer_idle_animation
        _stop_hdfmonkey_button_animation = self._stop_hdfmonkey_button_animation
        _stop_transfer_idle_animation = self._stop_transfer_idle_animation
        _update_image_usage_gauge = self._update_image_usage_gauge
        _warn_if_image_nearly_full = self._warn_if_image_nearly_full
        add_help_content = self.add_help_content
        add_main_log_window = self.add_main_log_window
        add_nextsync_log_window = self.add_nextsync_log_window
        apply_file_extension_filter_nextsync = self.apply_file_extension_filter_nextsync
        delete_files_button_show_confirmation_buttons = self.delete_files_button_show_confirmation_buttons
        download_nextzxos_image = self.download_nextzxos_image
        execute_hdf_monkey = self.execute_hdf_monkey
        execute_shell_command = self.execute_shell_command
        image_newfolder = self.image_newfolder
        image_newfolder_cancel = self.image_newfolder_cancel
        image_newfolder_create = self.image_newfolder_create
        is_hdfmonkey_present = self.is_hdfmonkey_present
        load_image = self.load_image
        nextsync_update_root_drive = self.nextsync_update_root_drive
        select_image = self.select_image
        set_treeview_properties = self.set_treeview_properties
        _check_image_writable = self._check_image_writable
        image_newfolder_dialog = self.image_newfolder_dialog

        # ── Image deletion + in-image rename (extracted to zxnu_sdcard_ops.py).
        build_image_edit_ops(
            self,
            _right_disk_content=lambda: right_disk_image_explorer_content,
            set_all_buttons_disabled=set_all_buttons_disabled,
            set_all_buttons_enabled=set_all_buttons_enabled,
            add_main_log_window=add_main_log_window,
            _check_image_writable=_check_image_writable,
            execute_hdf_monkey=execute_hdf_monkey,
            _check_access_denied_is_full_disk=lambda *a, **k: _check_access_denied_is_full_disk(*a, **k),
            image_reload_dir=lambda *a, **k: image_reload_dir(*a, **k),
        )
        image_rename_dialog = self.image_rename_dialog
        image_confirm_deletion_dialog = self.image_confirm_deletion_dialog
        image_delete_files = self.image_delete_files


        # ── NextSync server prepare/start (extracted to zxnu_nextsync_ops.py;
        # nextsync_warnings/nextsync_do_server_job/_re_try_send_folder are
        # bound later in __init__ and arrive as forwarding lambdas).
        build_nextsync_server_start(
            self,
            save_configuration_file=save_configuration_file,
            add_nextsync_log_window=add_nextsync_log_window,
            nextsync_server_exception_occured=nextsync_server_exception_occured,
            _nextsync_on_port_in_use=_nextsync_on_port_in_use,
            nextsync_hide_start_cancel_buttons=nextsync_hide_start_cancel_buttons,
            nextsync_warnings=lambda *a, **k: nextsync_warnings(*a, **k),
            nextsync_do_server_job=lambda *a, **k: nextsync_do_server_job(*a, **k),
            _re_try_send_folder=lambda *a, **k: _re_try_send_folder(*a, **k),
        )
        nextsync_perform_checks_and_prepare_server_start = (
            self.nextsync_perform_checks_and_prepare_server_start)
        nextsync_refresh_explorer = self.nextsync_refresh_explorer
        nextsync_start_server = self._nextsync_start_server_fn

        # ── SD-tab local-pane ops (extracted to zxnu_sdcard_ops.py; the
        # clipboard closures are bound below and arrive as forwarding lambdas).
        build_local_explorer_ops(
            self,
            add_main_log_window=add_main_log_window,
            # Read via the getter hook: right_disk_image_explorer_content is a
            # module global in zxnu_main, so the builder must not capture it.
            _right_disk_content=lambda: right_disk_image_explorer_content,
            nextsync_refresh_explorer=nextsync_refresh_explorer,
            _nextsync_unique_path=lambda *a, **k: _nextsync_unique_path(*a, **k),
            _run_nextsync_import_task=lambda *a, **k: _run_nextsync_import_task(*a, **k),
            _explorer_clipboard_has_items=lambda *a, **k: _explorer_clipboard_has_items(*a, **k),
            _explorer_paste_into_local=lambda *a, **k: _explorer_paste_into_local(*a, **k),
            _local_explorer_copy_selection=lambda *a, **k: _local_explorer_copy_selection(*a, **k),
        )
        _deletes_go_to_recycle_bin = self._deletes_go_to_recycle_bin
        _local_delete_paths_async = self._local_delete_paths_async
        _local_make_directory = self._local_make_directory
        local_explorer_delete_selection = self.local_explorer_delete_selection
        local_explorer_import_external_paths = self.local_explorer_import_external_paths
        local_explorer_refresh = self.local_explorer_refresh
        local_explorer_rename_item = self.local_explorer_rename_item
        local_sync_path_box = self.local_sync_path_box
        on_treeview_context_menu = self.on_treeview_context_menu
        local_current_view_dir = self.local_current_view_dir

        # ── NextSync classic-explorer ops (extracted to zxnu_nextsync_ops.py).
        build_nextsync_explorer_ops(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            add_nextsync_log_window=add_nextsync_log_window,
            set_treeview_properties=set_treeview_properties,
            nextsync_perform_checks_and_prepare_server_start=nextsync_perform_checks_and_prepare_server_start,
            nextsync_refresh_explorer=nextsync_refresh_explorer,
            _deletes_go_to_recycle_bin=_deletes_go_to_recycle_bin,
            _local_delete_paths_async=_local_delete_paths_async,
            _local_make_directory=_local_make_directory,
            _explorer_clipboard_set=lambda *a, **k: _explorer_clipboard_set(*a, **k),
            _explorer_clipboard_has_items=lambda *a, **k: _explorer_clipboard_has_items(*a, **k),
            _explorer_paste_into_local=lambda *a, **k: _explorer_paste_into_local(*a, **k),
        )
        on_nextsync_file_explorer_path_edited = self.on_nextsync_file_explorer_path_edited
        nextsync_on_treeview_context_menu = self.nextsync_on_treeview_context_menu
        nextsync_on_treeview_double_clicked = self.nextsync_on_treeview_double_clicked
        nextsync_rename_explorer_item = self.nextsync_rename_explorer_item
        nextsync_delete_explorer_item = self.nextsync_delete_explorer_item
        nextsync_import_external_paths = self.nextsync_import_external_paths
        _nextsync_unique_path = self._nextsync_unique_path
        _run_nextsync_import_task = self._run_nextsync_import_task
        nextsync_sync_mode_changed = self.nextsync_sync_mode_changed
        nextsync_slowtransfer_checkbox_statechanged = (
            self.nextsync_slowtransfer_checkbox_statechanged)
        nextsync_create_syncingore_button = self.nextsync_create_syncingore_button
        nextsync_delete_syncingore_button = self.nextsync_delete_syncingore_button
        nextsync_delete_syncpoint_button = self.nextsync_delete_syncpoint_button
        _nextsync_on_set_syncroot_clicked = self._nextsync_on_set_syncroot_clicked
        _nextsync_update_set_syncroot_button = self._nextsync_update_set_syncroot_button
        nextsync_current_view_dir = self.nextsync_current_view_dir
        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection = (
            self.nextsync_show_sync_buttons_based_on_fileexplorer_content_selection)

        # ── Transfers + shared explorer clipboard + image context menu
        # (extracted to zxnu_sdcard_ops.py).
        build_transfer_clipboard_ops(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            _right_disk_content=lambda: right_disk_image_explorer_content,
            set_all_buttons_disabled=set_all_buttons_disabled,
            set_all_buttons_enabled=set_all_buttons_enabled,
            nextsync_hide_start_cancel_buttons=nextsync_hide_start_cancel_buttons,
            nextsync_show_start_cancel_buttons=nextsync_show_start_cancel_buttons,
            delete_files_button_show_confirmation_buttons=delete_files_button_show_confirmation_buttons,
            add_main_log_window=add_main_log_window,
            add_nextsync_log_window=add_nextsync_log_window,
            set_treeview_properties=set_treeview_properties,
            image_newfolder_dialog=image_newfolder_dialog,
            _warn_if_image_nearly_full=_warn_if_image_nearly_full,
            _check_image_writable=_check_image_writable,
            execute_hdf_monkey=execute_hdf_monkey,
            image_rename_dialog=image_rename_dialog,
            nextsync_refresh_explorer=nextsync_refresh_explorer,
            local_explorer_refresh=local_explorer_refresh,
            local_current_view_dir=local_current_view_dir,
            local_sync_path_box=local_sync_path_box,
            local_explorer_import_external_paths=local_explorer_import_external_paths,
            nextsync_current_view_dir=nextsync_current_view_dir,
        )
        _explorer_clipboard_has_items = self._explorer_clipboard_has_items
        _explorer_clipboard_set = self._explorer_clipboard_set
        _explorer_paste_into_image = self._explorer_paste_into_image
        _explorer_paste_into_local = self._explorer_paste_into_local
        _image_explorer_copy_selection = self._image_explorer_copy_selection
        _local_explorer_copy_selection = self._local_explorer_copy_selection
        _local_explorer_paste_target_dir = self._local_explorer_paste_target_dir
        _nextsync_explorer_copy_selection = self._nextsync_explorer_copy_selection
        _nextsync_explorer_paste_target_dir = self._nextsync_explorer_paste_target_dir
        generate_disk_file_path = self.generate_disk_file_path
        image_dest_dir = self.image_dest_dir
        image_get_paths_to_local = self.image_get_paths_to_local
        image_navigate_to_path = self.image_navigate_to_path
        image_tree_context_menu = self.image_tree_context_menu
        image_upload_external_paths = self.image_upload_external_paths
        transfert_content_from_disk_to_image = self.transfert_content_from_disk_to_image
        transfert_content_from_image_to_disk = self.transfert_content_from_image_to_disk
        update_disk_manager_widget_table = self.update_disk_manager_widget_table
        image_clear_model = self.image_clear_model
        image_load_root = self.image_load_root
        image_reload_dir = self.image_reload_dir
        _check_access_denied_is_full_disk = self._check_access_denied_is_full_disk


        # ── NextSync server job + warnings/IP/cancel/conflict prompt
        # (extracted to zxnu_nextsync_ops.py).
        build_nextsync_server_job(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            add_nextsync_log_window=add_nextsync_log_window,
            nextsync_hide_start_cancel_buttons=nextsync_hide_start_cancel_buttons,
            nextsync_show_start_cancel_buttons=nextsync_show_start_cancel_buttons,
        )
        nextsync_warnings = self.nextsync_warnings
        nextsync_do_server_job = self.nextsync_do_server_job
        nextsync_cancel_server_job = self.nextsync_cancel_server_job
        nextsync_show_ip_info = self.nextsync_show_ip_info

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


        self.imageinput = _ImagePathCombo()
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

        # Selecting an item from the history dropdown loads it immediately —
        # UNLESS the activation is the opening click's own echo. On Windows
        # the click that OPENS the dropdown can also "activate" the current
        # entry (the popup, widened by long history paths, lands under the
        # cursor): the list flashed shut while the already-loaded image
        # reloaded (reported). Two guards: activating the image that is
        # ALREADY loaded is a no-op (there is nothing to load), and when
        # that no-op arrives within the opening half-second — the phantom's
        # signature — the dropdown is put straight back up, so the user
        # gets the list the click asked for.
        def _image_history_activated(index):
            picked = normalize_sd_image_path(self.imageinput.itemText(index))
            loaded = normalize_sd_image_path(
                (getattr(self, "right_disk_image_path", "") or "").strip())
            if picked and picked == loaded:
                opened_at = getattr(self.imageinput, "_popup_shown_at", 0.0)
                if time.monotonic() - opened_at < 0.5:
                    QTimer.singleShot(0, self.imageinput.showPopup)
                return
            load_image()
        self.imageinput.activated.connect(_image_history_activated)
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
        self.local_nav_row_container = _pane.local_nav_row_container
        self.image_nav_row_container = _pane.image_nav_row_container
        self.image_explorer_up_button = _pane.image_explorer_up_button
        self.image_explorer_refresh_button = _pane.image_explorer_refresh_button
        self.diskimageexplorerlabel = _pane.diskimageexplorerlabel
        self.diskimageexplorerpathinput = _pane.diskimageexplorerpathinput
        self.image_path_row_container = _pane.image_path_row_container
        self.sdcard_explorer_grid = _pane.sdcard_explorer_grid
        self.sdcard_explorer_container = _pane
        self._image_recolor_all = _pane.image_recolor_all

        # Ctrl+wheel font zoom on both SD Card explorers (the restore half
        # runs in load_configuration_file, next to the column widths).
        def _sd_tree_font_persist(_key):
            def _p(_pt):
                configuration_dictionary[_key] = str(_pt)
                save_configuration_file()
            return _p
        bind_tree_font_zoom(self.treeview,
                            _sd_tree_font_persist(SETTING_SDCARD_TREE_FONT))
        bind_tree_font_zoom(self.image_treeview,
                            _sd_tree_font_persist(SETTING_IMAGE_TREE_FONT))

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

        # INIT_LOG is built at import time, before a language is known, so
        # each line is translated here as it is emitted. The banner carries the
        # version, so it goes through its own {version} template; the credit
        # lines translate the prose around the names, which stay as written.
        add_main_log_window(ui_tr_now("Welcome to ZX Next Unite {version}")
                            .format(version=ZX_NEXT_UNITE_VERSION))
        for l in INIT_LOG[1:]:
            add_main_log_window(ui_tr_now(l))

        self.listWidgetHelp = QListWidget(self)

        # Like INIT_LOG above, INIT_HELP is built at import time, before a
        # language is known, so every line is translated as it is inserted.
        # Unlike the log windows (append-only streams) the help is static
        # CONTENT the widget-tree walk cannot reach, so a language switch
        # rebuilds the whole list — clearing the retro console mirror first,
        # which add_help_content would otherwise fill with duplicates.
        def _repopulate_help():
            self.listWidgetHelp.clear()
            _help_retro = getattr(self, "_help_retro_log", None)
            if _help_retro is not None:
                try:
                    _help_retro.clear()
                except Exception:
                    pass
            add_help_content(ui_tr_now("Welcome to zx-next-unite {version} help")
                             .format(version=ZX_NEXT_UNITE_VERSION), False)
            for l in INIT_HELP[1:]:
                add_help_content(ui_tr_now(l), False)
        self._repopulate_help = _repopulate_help
        _repopulate_help()


        # Height is governed by the explorers ⇄ log splitter (built below);
        # only keep a small floor so the log can never be dragged away entirely.
        self.listWidgetLog.setMinimumHeight(60)
        # self.listWidgetLog.setMinimumWidth(410)
        # self.listWidgetLog.setMaximumWidth(410)

        self.imageexplorerbuttonscontainer = QWidget()
        self.imageexplorerbuttons = QHBoxLayout()
        # Flush against the path box since the one-row mirroring (9.5.19):
        # no layout margins, and the two historical spacer labels stay OUT
        # of the layout -- their literal runs of spaces were the visible
        # gap between the box and the buttons (field report #3).
        self.imageexplorerbuttons.setContentsMargins(0, 0, 0, 0)

        self.hiddenspacelabel1 = QLabel()
        self.hiddenspacelabel1.setText("      ")

        self.button_new_folder = QPushButton("NewFolder", self)
        self.button_new_folder.setText("New Folder")
        # Natural width since the 9.5.19 one-row mirroring: the trio sits
        # right of the image path box, and the box owns the slack -- the
        # 190px minimums starved it to a sliver (field report).
        self.button_new_folder.clicked.connect(image_newfolder)

        self.button_rename = QPushButton("Rename", self)
        self.button_rename.setText("Rename")
        self.button_rename.clicked.connect(image_rename_dialog)

        self.download_and_install_hdfmonkey_button = QPushButton("Download & install HDF Monkey", self)
        self.download_and_install_hdfmonkey_button.setText("Download and install HDF Monkey")
        self.download_and_install_hdfmonkey_button.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.download_and_install_hdfmonkey_button.clicked.connect(_on_hdfmonkey_button_clicked)
        self.download_and_install_hdfmonkey_button.setVisible(False)

        self.hiddenspacelabel2 = QLabel()
        self.hiddenspacelabel2.setText("       ")

        self.button_delete_files = QPushButton("DeleteFiles", self)
        self.button_delete_files.setText("Delete")
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

        # The New Folder / Rename / Delete cluster joins the image path row,
        # right of the path box -- ONE bottom row, the Remote Explorer's
        # exact arrangement (9.5.19 mirroring).
        self.image_path_row_container.layout().addWidget(self.imageexplorerbuttonscontainer)

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

        # ── SD-card retro pygame log (extracted to zxnu_retro_ui.py). ──
        build_main_retro_log(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            add_main_log_window=add_main_log_window,
        )

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
        self.sdcard_splitter.setHandleWidth(10)
        # Visible "grab pill" on the handle (invisible by default on dark
        # themes — users never found the splitter). Shared style constant.
        self.sdcard_splitter.setStyleSheet(SPLITTER_HANDLE_QSS)
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
        # Windows). It lists the recent official releases for this CPU, downloads
        # the chosen one and extracts it into downloads/mame; on success the
        # Launch button (revealed by _on_mame_install_result) replaces it.
        self.button_install_mame = QPushButton("⬇  Install MAME", self)
        self.button_install_mame.setToolTip(
            "List the recent MAME releases for this PC and install the one you\n"
            "choose (the newest by default) into the downloads/mame folder.\n"
            "Requires an internet connection (~90 MB download, ~500 MB installed).")
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

        # The CSpect/MAME option combos are the one place where the i18n walk
        # may translate ITEM texts: every selection above is read back by index
        # (emulator_option_argument) and persisted as an index, so no dispatch
        # depends on the displayed label. The MAME machine combo is deliberately
        # absent — its items are machine names passed straight to MAME.
        for _option_combo in (self.cspect_screensize, self.cspect_sound,
                              self.cspect_vsync, self.cspect_joystick,
                              self.cspect_mouse, self.cspect_frequency,
                              self.cspect_esc, self.mame_aspect,
                              self.mame_sound, self.mame_mouse,
                              self.mame_joystick, self.mame_esc):
            mark_combo_items_translatable(_option_combo)

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

        # ── Sidebar sync animation + tab-title colour cycles (extracted to
        # zxnu_retro_ui.py). ──
        build_sidebar_anim(self)

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
            # The Help tab is list CONTENT, not widget texts — the walk above
            # cannot reach it, so it is rebuilt in the new language instead.
            _repopulate_help()
            # A speaking wizard switches language mid-speech (zxnu_wizard.py).
            _wiz = getattr(self, "_wizard", None)
            if _wiz is not None:
                _wiz.on_language_changed()
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

        # ── Help-tab retro pygame log (extracted to zxnu_retro_ui.py). ──
        build_help_retro_log(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
        )

        zxnextunite_Help_tab.setLayout(grid_tab_Help)
        wid_inner.tab.addTab(zxnextunite_Help_tab, "?")

        #wid_inner.tab.tabBarClicked.connect(tab_changed)

        # ── One-time content disclaimer (extracted to zxnu_retro_ui.py). ──
        build_content_disclaimer(
            self,
            configuration_dictionary=configuration_dictionary,
            save_configuration_file=save_configuration_file,
            _DISCLAIMER_TEXT=_DISCLAIMER_TEXT,
        )
        _show_content_disclaimer = self._show_content_disclaimer

        # ---- Multi-API cross-search helpers ----

        # ── Cross-tab ops (extracted to zxnu_tab_ops.py): autocomplete
        # helpers, Unite! cross-search fan-out, tab badges/spinners (+their
        # timers), the autocomplete-arrow animation and on_tab_changed.
        build_tab_ops(
            self,
            _right_disk_content=lambda: right_disk_image_explorer_content,
            _start_transfer_idle_animation=_start_transfer_idle_animation,
            _stop_transfer_idle_animation=_stop_transfer_idle_animation,
            nextsync_perform_checks_and_prepare_server_start=nextsync_perform_checks_and_prepare_server_start,
            update_disk_manager_widget_table=update_disk_manager_widget_table,
            wid_inner=wid_inner,
            getit_run_search=getit_run_search,
            zxdb_run_search=zxdb_run_search,
            zxart_run_search=zxart_run_search,
            _show_content_disclaimer=_show_content_disclaimer,
        )
        _apply_autocomplete_setting = self._apply_autocomplete_setting
        _multi_search_enabled = self._multi_search_enabled
        _cross_search_getit = self._cross_search_getit
        _cross_search_zxdb = self._cross_search_zxdb
        _cross_search_zxart = self._cross_search_zxart
        _set_tab_badge = self._set_tab_badge
        _clear_tab_badge = self._clear_tab_badge
        _start_tab_spinner = self._start_tab_spinner
        _stop_tab_spinner = self._stop_tab_spinner
        on_tab_changed = self.on_tab_changed

        #  Start main logic
        load_configuration_file()
        # Re-tint the tab bar with the just-loaded general UI text colour.
        # This covers Custom mode (whose theme re-apply returns early without
        # refreshing) and the Settings / itch.io tabs that are added after the
        # initial colouring pass, so every tab honours the saved colour.
        if hasattr(self, "_refresh_tab_stylesheet"):
            try:
                self._refresh_tab_stylesheet()
            except Exception:
                pass
        self._initialising = False

        # ── Onboarding Wizard (zxnu_wizard.py): Wizzy, the animated
        # pixel-art assistant. Built now (hidden); its deferred startup —
        # after the deferred tab activation below has settled — syncs the
        # Settings checkbox and plays the first-run introduction.
        build_wizard(self, configuration_dictionary=configuration_dictionary)
        QTimer.singleShot(2200, self._wizard.startup)

        # ── Network watcher (zxnu_network.py): offline-tolerant startup.
        # Probes off the UI thread every 30 s; a confirmed outage shows a
        # yellow advisory (emulators still work) and gates the online tabs'
        # auto-fetches; a recovery shows a green toast and re-runs the
        # current tab's activation so the skipped fetches fire.
        build_network_watch(self, on_tab_changed=on_tab_changed)

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
            if current_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GOOEY):
                # App restored onto the SD-card tab: kick off the idle glow now,
                # since currentChanged wasn't connected during config restore.
                _start_transfer_idle_animation()
            elif current_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GETIT):
                _show_content_disclaimer()
                self._getit_fetch_motd()
                # Preserve any pending query (e.g. mirrored from an AllInOne
                # multi-search) instead of clearing it with a "Latest" fetch.
                if (self.getit_results_table.rowCount() == 0
                        and not self._getit_search_loading
                        and not self.getit_search_input.text().strip()):
                    self._getit_on_latest()
            elif current_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ZXDB):
                _show_content_disclaimer()
                # Restored straight onto ZXDB: the Unite! "Latest" fan-out
                # above drives this pane too, and ZXDB's newest rows have no
                # screenshots yet. Wait for that fetch, then load the pane's
                # own (picture-bearing) first page — see the docstring on
                # _zxdb_startup_initial_load.
                self._zxdb_startup_initial_load()
            elif current_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ZXART):
                _show_content_disclaimer()
                self._zxart_on_tab_activated()
            elif current_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
                _show_content_disclaimer()
            elif current_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC):
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
            add_main_log_window(ui_tr_now("Using MAME under: {path}")
                                .format(path=_mame_found_path))

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
                    add_main_log_window(ui_tr_now("MAME version: {version}")
                                        .format(version=line))

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
                add_main_log_window(ui_tr_now(
                    "Found hdfmonkey alongside CSpect: {path}").format(
                        path=_near_hdfmonkey))
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
                add_main_log_window(ui_tr_now(
                    "Using CSpect under downloads/cspect: {path}"
                ).format(path=cspect_path))
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
                add_main_log_window(ui_tr_now(
                    "Using hdfmonkey bundled with CSpect: {path}"
                ).format(path=hdfmonkey_path))
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
            # The NextSync tab's Remote Explorer carries an emulator
            # strip built from the same detection; tell it too.
            if hasattr(self, "_re_refresh_emulators"):
                self._re_refresh_emulators()

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
                    add_main_log_window(ui_tr_now(
                        "Found hdfmonkey alongside CSpect: {path}").format(
                            path=near))
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
                add_main_log_window(ui_tr_now(
                    "UI language set to '{lang}' to match the system "
                    "language — change it on the Settings tab.").format(
                        lang=_ui_language))
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
              "'.sync5 -L' (-l or -listen) server at startup.")


_zxnu_parse_start_re_listener_arg()

app = QApplication(sys.argv)

# The "Next" chrome: retro navy + magenta/cyan widget styling, applied
# app-wide (see NEXT_CHROME_QSS in zxnu_config for the boundary rules —
# the desktop-theme engine keeps owning colors, tabs and backgrounds).
app.setStyleSheet(NEXT_CHROME_QSS)

# Remove the 256 MB image allocation cap so that large zxART images
# (which Qt rejects by default) are loaded without the
# "QImageIOHandler: Rejecting image" warning.
QImageReader.setAllocationLimit(0)

# Silence Qt's SVG-render chatter ("qt.svg.draw: The requested buffer size
# is too big, ignoring", QTBUG-123010): on Linux the file explorers pull
# the system icon THEME's SVGs, and Qt 6.7+ logs this for icons whose
# filter buffer exceeds its cap. Purely cosmetic, not ours to fix (the
# icons are the distro's), and every app asset here is a PNG — so the
# category can go quiet without hiding anything actionable.
QLoggingCategory.setFilterRules("qt.svg.draw.warning=false")

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
    # In-flight catalogue/thumbnail/update fetches are QRunnables on the
    # GLOBAL QThreadPool doing network I/O — and interpreter finalization
    # waits for them, so a slow socket used to hold the whole exit hostage
    # for its full timeout (very visible since the wizard's tour started
    # warming the online tabs). Drop everything still queued and give the
    # running ones a short grace; _handle_sigint escalates if they overrun.
    try:
        _pool = QThreadPool.globalInstance()
        _pool.clear()
        _pool.waitForDone(3000)
    except Exception:
        logging.exception("Graceful shutdown: thread-pool drain failed")

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
    # The graceful shutdown above already cleared the global pool and
    # granted 3 s of grace. If a network runnable is STILL running, the
    # interpreter's exit would block until its socket times out — for a
    # console Ctrl-C that reads as a hang, so leave hard instead: config
    # and syncpoint are saved (window.close()) and the sync goodbyes were
    # delivered; only the doomed fetch results are lost.
    try:
        if not QThreadPool.globalInstance().waitForDone(0):
            print("Exiting with a network fetch still in flight.", flush=True)
            for _h in logging.getLogger().handlers:
                try:
                    _h.flush()
                except Exception:
                    pass
            os._exit(0)
    except Exception:
        pass

signal.signal(signal.SIGINT, _handle_sigint)

_sigint_timer = QTimer()
_sigint_timer.setInterval(200)   # check every 200 ms
_sigint_timer.timeout.connect(lambda: None)   # no-op; just wakes Python
_sigint_timer.start()

# Catalog prefetch disabled — zxart_client_search now uses a direct
# server-side title filter, so no upfront catalog download is needed.
# _zxart_prefetch_cache_if_stale()

sys.exit(app.exec())

