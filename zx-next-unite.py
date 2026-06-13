#!/usr/bin/env python3

"""
    zx-next-unite by Julien Clauzel based on:

        HDFM-GOOEY, Getit by em00k
    &
        NextSync by Jari Komppa
    & 
        ZXDB by https://api.zxinfo.dk/
    &
        ZXArt by https://zxart.ee/

    * Requirements:
        - Python 3.13+
        - pyside6
        - CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
            feel free to support his development efforts & patreon https://www.patreon.com/mikedailly
            - Make sure Spectrum Next roms installed are installed in local directory (they should be provided in the CSpect zip package by default).
                These two files namely: enNextZX.rom and enNxtMMC.rom -MUST- be placed in the root folder of your #CSpect.
        - You will need Spectrum Next images files that you can download from https://zxspectrumnext.online/cspect/  such as https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-2gb.zip
        - Download & install hdfmonkey by Matt Westcott https://github.com/gasman/hdfmonkey , on Windows either compile the source manually or download a pre-compiled version at:
            https://uto.speccy.org/downloads/hdfmonkey_windows.zip
        - On Mac/Linux you will need to install mono-complete

    * Additional help pages:
        - https://wiki.specnext.dev/Development_Tools:Linux_setup

    * First install pyside6 this is required for the UI to render the different controls being used:
        python -m pip install pyside6

    * Copy Cspect (with the Spectrum Next roms) and hdfmonkey in the same directory (see above).

            - hdfmonkey -

        If you are running the app on Windows and hdfmonkey in not present in the same directory, you will see an error message in the main log Windows as it is missing.
           if that is the case you will see a 'Download and Install button' bottom right, once clicked it will try to fetch https://uto.speccy.org/downloads/hdfmonkey_windows.zip
           and unzip hdfmonkey executable in the same directory.
               If the above automated install is successful, you should then be able to select an image and navigate it.

        On Mac/Linux you will need to install hdfmonkey manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey

    * On Windows: OpenAL sound library is required for CSpect you may download it from here: https://openal.org/

    * On Mac/Linux: you will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete

    * Start zx-next-unite.py
        python zx-next-unite.py

    * Windows executables can be created using pyinstaller and upx https://upx.github.io/ & https://github.com/upx/upx/: 
    To update embedded images use: pyside6-rcc rc_backgrounds.qrc -o rc_backgrounds.py

    pip install pyinstaller
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
def _zxnu_crash_log_path():
    try:
        if getattr(_sys_early, "frozen", False):
            base = _os_early.path.dirname(_sys_early.executable)
        else:
            base = _os_early.path.dirname(_os_early.path.abspath(__file__))
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
        cfg_path = _os_early.path.join(
            _os_early.path.dirname(_os_early.path.abspath(_sys_early.argv[0])),
            "hdfg.cfg")
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
import logging
import os
import pathlib
import platform
import re
import shlex
import socket
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
    QModelIndex,
    QObject,
    QRect,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    QStringListModel,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
    qInstallMessageHandler,
)
from PySide6.QtCore import Q_ARG
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QImage, QFontInfo, QPainter, QPixmap, QFont
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QStackedWidget,
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
from zxnu_media import *
from zxnu_gallery import *
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
from PySide6.QtWidgets import QCompleter, QWidget
from PySide6.QtCore import Qt, QEvent, QObject, QTimer, QMargins
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



assert sys.version_info >= (3, 6) # We need 3.6 for f"" strings.

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



# ---------------------------------------------------------------------------
# Shared HTTP retry helper
# ---------------------------------------------------------------------------

_HTTP_RETRYABLE = (429, 502, 503, 504)


def _is_retryable_connection_error(exc: OSError) -> bool:
    """Return True for transient connection-level errors that are worth retrying.

    Covers:
    - ``ConnectionResetError`` / WinError 10054 ("An existing connection was
      forcibly closed by the remote host") — raised directly or wrapped inside
      ``urllib.error.URLError`` whose ``__str__`` reads "urlopen error [WinError
      10054] …".
    - ``ConnectionRefusedError``, ``BrokenPipeError``, ``TimeoutError``.
    - Any ``urllib.error.URLError`` whose *reason* is one of the above.
    """
    import urllib.error as _ue
    # URLError wraps the underlying socket/OS error in .reason
    inner = exc.reason if isinstance(exc, _ue.URLError) else exc
    return isinstance(inner, (
        ConnectionResetError,
        ConnectionRefusedError,
        BrokenPipeError,
        TimeoutError,
        OSError,  # catches generic errno-based errors (ECONNRESET etc.)
    ))


def _http_fetch_bytes_with_retry(
    url: str,
    *,
    headers: dict = None,
    method: str = "GET",
    timeout: int = 20,
    _retries: int = 3,
    _backoff: float = 1.5,
) -> bytes:
    """Fetch *url* as bytes with retry/back-off on transient errors.

    Retries on HTTP 429/502/503/504 and on connection-level OS errors,
    including ``ConnectionResetError`` / WinError 10054 ("An existing
    connection was forcibly closed by the remote host") whether raised
    directly or wrapped inside ``urllib.error.URLError``.
    Closes the HTTPError response before sleeping on retryable HTTP codes.
    """
    import urllib.error as _ue
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    delay = _backoff
    last_exc = None
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except _ue.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                last_exc = exc
                exc.close()
                # logging.warning(
                #     "_http_fetch_bytes_with_retry: HTTP %d on attempt %d/%d for %s",
                #     exc.code, attempt, _retries, url,
                # )
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                raise
        except OSError as exc:
            # Catches urllib.error.URLError (subclass of OSError) and direct
            # socket errors such as ConnectionResetError (WinError 10054).
            last_exc = exc
            # logging.warning(
            #     "_http_fetch_bytes_with_retry: connection error on attempt %d/%d for %s: %s",
            #     attempt, _retries, url, exc,
            # )
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
    raise last_exc


def _http_fetch_with_cd_retry(
    url: str,
    *,
    headers: dict = None,
    timeout: int = 60,
    _retries: int = 3,
    _backoff: float = 1.5,
):
    """Fetch *url*, returning ``(content_disposition_header, bytes)`` with retry.

    Retries on HTTP 429/502/503/504 and on connection-level OS errors,
    including ``ConnectionResetError`` / WinError 10054.
    """
    import urllib.error as _ue
    req = urllib.request.Request(url, headers=headers or {})
    delay = _backoff
    last_exc = None
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cd   = resp.headers.get("Content-Disposition", "")
                data = resp.read()
            return cd, data
        except _ue.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                last_exc = exc
                exc.close()
                logging.warning(
                    "_http_fetch_with_cd_retry: HTTP %d on attempt %d/%d for %s",
                    exc.code, attempt, _retries, url,
                )
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                raise
        except OSError as exc:
            # Catches urllib.error.URLError and direct socket errors
            # including ConnectionResetError (WinError 10054).
            last_exc = exc
            logging.warning(
                "_http_fetch_with_cd_retry: connection error on attempt %d/%d for %s: %s",
                attempt, _retries, url, exc,
            )
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
    raise last_exc


def _http_head_ok_with_retry(
    url: str,
    *,
    headers: dict = None,
    timeout: int = 10,
    _retries: int = 3,
    _backoff: float = 1.5,
) -> bool:
    """Send a HEAD request; return True when the server responds with status < 400.

    Retries on *_HTTP_RETRYABLE* HTTP codes and on connection-level OS errors,
    including ``ConnectionResetError`` / WinError 10054 ("An existing
    connection was forcibly closed by the remote host").
    Returns False after exhausting retries so callers treat the URL as
    unavailable rather than propagating an exception.
    """
    import urllib.error as _ue
    req = urllib.request.Request(url, headers=headers or {}, method="HEAD")
    delay = _backoff
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status < 400
        except _ue.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                exc.close()
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                return exc.code < 400
        except OSError:
            # Catches urllib.error.URLError and direct socket errors
            # including ConnectionResetError (WinError 10054).
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
    return False


# ---------------------------------------------------------------------------
# GetIt API helpers
# ---------------------------------------------------------------------------

def getit_fetch(path: str, timeout: int = 10) -> str:
    """Fetch a plain-text response from the GetIt server and return the body string."""
    url = GETIT_BASE_URL + path
    data = _http_fetch_bytes_with_retry(
        url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=timeout
    )
    return data.decode("utf-8", errors="replace")


def getit_parse_file_list(text: str):
    """Parse a ^R^…^END^ file listing into a list of dicts.

    The entire response is a single caret-delimited line:
      ^R^[total]^[id]^[title]^[author]^[size]^[category]^…^END^

    Returns (entries, total, page, total_pages) where entries is a list of
    {'id', 'title', 'author', 'size', 'category'} dicts.
    """
    text = text.strip()
    entries = []
    total = 0
    page = 1
    total_pages = 1

    # Split on ^ and drop empty leading/trailing tokens
    parts = [p for p in text.split("^") if p != ""]
    # parts[0] == 'R', parts[1] == total (int), then groups of 5: id,title,author,size,category
    if not parts or parts[0] != "R":
        return entries, total, page, total_pages

    try:
        total = int(parts[1])
    except (IndexError, ValueError):
        pass

    i = 2
    while i + 4 < len(parts):
        chunk = parts[i:i + 5]
        if chunk[0] == "END":
            break
        entries.append({
            "id":       chunk[0].strip(),
            "title":    chunk[1].strip(),
            "author":   chunk[2].strip(),
            "size":     chunk[3].strip(),
            "category": chunk[4].strip(),
        })
        i += 5

    total_pages = max(1, (total + GETIT_PAGE_SIZE - 1) // GETIT_PAGE_SIZE) if total else 1

    return entries, total, page, total_pages


def getit_parse_detail(text: str) -> dict:
    """Parse an entry-detail response into a dict of TAG->value pairs."""
    text = text.strip()
    detail = {}
    TAGS = ["IDID", "TITL", "LINK", "FSIZ", "AUTH", "HITS", "MD5", "VER", "DESC", "DATE", "URL"]
    for tag in TAGS:
        marker = f"^{tag}^"
        idx = text.find(marker)
        if idx == -1:
            continue
        value_start = idx + len(marker)
        # Find the next known tag or end of string
        end = len(text)
        for other_tag in TAGS:
            if other_tag == tag:
                continue
            other_idx = text.find(f"^{other_tag}^", value_start)
            if other_idx != -1 and other_idx < end:
                end = other_idx
        raw_value = text[value_start:end].strip(" \r\n^")
        # Strip embedded newlines from description per spec
        raw_value = raw_value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
        detail[tag] = raw_value
    return detail


# ---------------------------------------------------------------------------
# ZXDB (zxinfo.dk) helpers
# ---------------------------------------------------------------------------

def zxdb_entry_website_url(eid) -> str:
    """Return the public zxinfo.dk landing-page URL for a ZXDB entry id.

    ZXInfo ids are commonly zero-padded to 7 digits but the website happily
    accepts the unpadded form too. We pass the id through unchanged when it
    is non-numeric (e.g. magazine ids that may include letters)."""
    s = str(eid or "").strip()
    if not s:
        return ""
    try:
        n = int(s)
        s = f"{n:07d}"
    except (TypeError, ValueError):
        pass
    return f"https://zxinfo.dk/details/{urllib.parse.quote(s)}"


def zxart_entry_website_url(entry) -> str:
    """Return the public zxart.ee landing-page URL for a zxArt gallery entry.

    The url is provided by the zxArt API on each prod / picture record, so
    we read it from *entry* (which carries the API record under ``_source``).
    Falls back to a generic search URL if the API record did not include
    a direct url."""
    if not isinstance(entry, dict):
        return ""
    src = entry.get("_source") if isinstance(entry.get("_source"), dict) else {}
    for key in ("url", "Url", "URL", "pageUrl", "page_url"):
        u = src.get(key) if isinstance(src, dict) else None
        if isinstance(u, str) and u.strip():
            return u.strip()
    title = entry.get("title") or ""
    if title:
        return "https://zxart.ee/eng/search/?searchString=" + urllib.parse.quote(title)
    return ""


def zxdb_fetch_json(path: str, timeout: int = 15, _retries: int = 3, _backoff: float = 1.5):
    """GET JSON from the ZXInfo API. *path* must include any query string.
    Identifies the client per API policy via a custom User-Agent.
    Retries up to *_retries* times on transient server errors (5xx / network)
    with exponential backoff starting at *_backoff* seconds."""
    import time as _time
    url = ZXDB_BASE_URL + path
    last_exc = None
    for attempt in range(_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": ZXDB_USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            try:
                exc.close()
            except Exception:
                pass
            if exc.code in (502, 503, 504, 429) and attempt < _retries - 1:
                _time.sleep(_backoff * (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, OSError) as exc:
            # urllib.error.URLError wraps socket errors such as
            # ConnectionResetError (WinError 10054) — all are retried here.
            last_exc = exc
            if attempt < _retries - 1:
                _time.sleep(_backoff * (2 ** attempt))
                continue
            raise
    raise last_exc


def zxdb_fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch raw bytes (e.g. a screenshot or game file) using ZXDB UA."""
    return _http_fetch_bytes_with_retry(
        url, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=timeout
    )


def zxdb_pick(d: dict, *keys, default=""):
    """Return the first non-empty value from *d* among *keys*."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def zxdb_parse_search(payload) -> tuple:
    """Normalize a /search JSON response into (entries, total, page, total_pages, page_size).

    Each entry is a dict: id, title, year, author(s), machine, genre, score.
    Handles a couple of envelope shapes seen on the ZXInfo API (Elastic-style
    `hits.hits[]._source` and a flatter `{ items: [...] }`).
    """
    entries = []
    total = 0
    page = 0
    total_pages = 1
    page_size = ZXDB_PAGE_SIZE

    if not isinstance(payload, dict):
        return entries, total, page, total_pages, page_size

    # Pagination metadata (may appear under different keys)
    # ZXInfo v3 uses ES envelope: hits.total.value (or hits.total as int)
    _hits_meta = payload.get("hits")
    if isinstance(_hits_meta, dict):
        _hits_total = _hits_meta.get("total")
        if isinstance(_hits_total, dict):
            total = int(_hits_total.get("value", 0) or 0)
        elif isinstance(_hits_total, (int, float)):
            total = int(_hits_total)
        else:
            total = int(zxdb_pick(payload, "hits_count", "total", "totalHits", default=0) or 0)
    else:
        total = int(zxdb_pick(payload, "hits_count", "total", "totalHits", default=0) or 0)
    page  = int(zxdb_pick(payload, "current_page", "currentPage", "page", default=0) or 0)
    total_pages = int(zxdb_pick(payload, "total_pages", "totalPages", "pages", default=0) or 0)
    page_size = int(zxdb_pick(payload, "size", "pageSize", default=ZXDB_PAGE_SIZE) or ZXDB_PAGE_SIZE)

    # Pull the array of hits
    hits = []
    if isinstance(payload.get("hits"), dict) and isinstance(payload["hits"].get("hits"), list):
        hits = payload["hits"]["hits"]
    elif isinstance(payload.get("hits"), list):
        hits = payload["hits"]
    elif isinstance(payload.get("items"), list):
        hits = payload["items"]
    elif isinstance(payload.get("results"), list):
        hits = payload["results"]

    for h in hits:
        if isinstance(h, dict) and "_source" in h and isinstance(h["_source"], dict):
            src = h["_source"]
            eid = h.get("_id") or src.get("id") or src.get("entry_id") or ""
            score = h.get("_score", "")
        else:
            src = h if isinstance(h, dict) else {}
            eid = src.get("id") or src.get("entry_id") or ""
            score = src.get("_score", src.get("score", ""))

        # Authors / publishers may be a list of dicts
        author = ""
        for key in ("authors", "publishers"):
            v = src.get(key)
            if isinstance(v, list) and v:
                names = []
                for a in v:
                    if isinstance(a, dict):
                        nm = a.get("name") or a.get("groupName") or ""
                    else:
                        nm = str(a)
                    if nm:
                        names.append(nm)
                if names:
                    author = ", ".join(names)
                    break
            elif isinstance(v, str) and v:
                author = v
                break

        machine = zxdb_pick(src, "machineType", "machine_type", "machine")
        genre   = zxdb_pick(src, "genreType", "genre", "genretype")
        year    = src.get("originalYearOfRelease") or src.get("yearOfRelease") or src.get("year") or ""
        title   = zxdb_pick(src, "title", "fullTitle", "name")

        entries.append({
            "id":      str(eid),
            "title":   str(title),
            "year":    str(year),
            "author":  str(author),
            "machine": str(machine),
            "genre":   str(genre),
            "score":   "" if score == "" else f"{score:.1f}" if isinstance(score, (int, float)) else str(score),
        })

    if not total_pages and page_size > 0 and total:
        total_pages = max(1, (total + page_size - 1) // page_size)

    return entries, total, page or 0, max(1, total_pages or 1), page_size


def zxdb_parse_game_detail(payload) -> dict:
    """Extract a flat detail dict from a /games/{id} response.

    Returns: title, year, authors, publishers, machine, genre, language,
    description, remarks, screenshot_url, downloads (list of {format, url, type}).
    """
    if not isinstance(payload, dict):
        return {}

    src = payload
    if "_source" in payload and isinstance(payload["_source"], dict):
        src = payload["_source"]

    def _join_names(v):
        if isinstance(v, list):
            out = []
            for a in v:
                if isinstance(a, dict):
                    nm = a.get("name") or a.get("groupName") or ""
                    if nm:
                        out.append(nm)
                elif isinstance(a, str) and a:
                    out.append(a)
            return ", ".join(out)
        if isinstance(v, str):
            return v
        return ""

    detail = {
        "id":          str(payload.get("_id") or src.get("id") or ""),
        "title":       str(zxdb_pick(src, "title", "fullTitle", "name")),
        "year":        str(src.get("originalYearOfRelease") or src.get("yearOfRelease") or src.get("year") or ""),
        "authors":     _join_names(src.get("authors")),
        "publishers":  _join_names(src.get("publishers")),
        "machine":     str(zxdb_pick(src, "machineType", "machine_type", "machine")),
        "genre":       str(zxdb_pick(src, "genreType", "genre", "genretype")),
        "language":    str(zxdb_pick(src, "language")),
        "description": "",
        "remarks":     str(zxdb_pick(src, "remarks", "originalPublication")),
        "screenshot_url": "",
        "screenshots":   [],   # list of {url, type}
        "downloads":   [],
    }

    # ZXInfo nests publishers inside releases[].publishers in /games/{id}.
    # Fall back to release-level publishers when top-level is empty.
    if not detail["publishers"]:
        rel_pub_names: list = []
        seen_pub: set = set()
        rels0 = src.get("releases") or []
        if isinstance(rels0, list):
            for rel in rels0:
                if not isinstance(rel, dict):
                    continue
                rp = rel.get("publishers")
                names_str = _join_names(rp)
                if not names_str:
                    continue
                for nm in [n.strip() for n in names_str.split(",")]:
                    if nm and nm not in seen_pub:
                        seen_pub.add(nm)
                        rel_pub_names.append(nm)
        if rel_pub_names:
            detail["publishers"] = ", ".join(rel_pub_names)

    # Description: usually under additionals or comments – best-effort.
    desc_candidates = [
        src.get("description"),
        src.get("comments"),
        src.get("manual"),
    ]
    for d in desc_candidates:
        if isinstance(d, str) and d.strip():
            detail["description"] = d.strip()
            break
        if isinstance(d, list) and d:
            joined = " ".join(str(x) for x in d if x)
            if joined.strip():
                detail["description"] = joined.strip()
                break

    # Screenshots / additionals: collect ALL image-like entries for slideshow.
    image_exts = (".png", ".gif", ".jpg", ".jpeg", ".bmp", ".scr")
    # Web-renderable raster formats; preferred over raw .scr screen dumps when
    # the same screen is offered in more than one format.
    web_image_exts = (".png", ".gif", ".jpg", ".jpeg", ".bmp")
    seen_urls = set()
    # Maps a screen's base filename (without extension) to its index in
    # detail["screenshots"], so the same picture offered both as a PNG and as
    # a raw .scr screen dump (e.g. ZXInfo "additionalDownloads") is counted
    # only once instead of inflating the slideshow page count.
    seen_stems = {}

    def _abs_url(u):
        if not u:
            return ""
        if u.startswith("/"):
            # ZXInfo serves screen/asset paths under https://zxinfo.dk/media.
            # The older spectrumcomputing.co.uk host returns 404 for these
            # relative paths (e.g. /zxscreens/0037705/0037705-load-1.png).
            return "https://zxinfo.dk/media" + u
        return u

    # Gather candidate "asset" lists from top-level AND from each release.
    asset_lists = []
    for key in ("screens", "additionals", "additionalDownloads"):
        v = src.get(key)
        if isinstance(v, list):
            asset_lists.append(v)
    rels = src.get("releases") or []
    if isinstance(rels, list):
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            for key in ("screens", "additionals", "additionalDownloads"):
                v = rel.get(key)
                if isinstance(v, list):
                    asset_lists.append(v)

    for arr in asset_lists:
        for a in arr:
            if not isinstance(a, dict):
                continue
            url = _abs_url(a.get("url") or a.get("path"))
            if not url:
                continue
            t = (a.get("type") or "").lower()
            fmt = (a.get("format") or "").lower()
            ulow = url.lower()
            is_image = (
                any(ulow.endswith(ext) for ext in image_exts)
                or "picture" in fmt
                or any(s in t for s in (
                    "running", "loading", "screenshot", "screen",
                    "inlay", "cover", "advert", "map", "picture",
                    "media scan", "tape", "cassette", "disk", "box",
                ))
            )
            if not is_image:
                continue
            if url in seen_urls:
                continue
            # Deduplicate the same screen offered in multiple formats (e.g. a
            # PNG screenshot plus its raw .scr screen dump). Key on the base
            # filename without extension; keep the web-renderable raster
            # version (.png/.gif/.jpg…) over a .scr that the viewer cannot
            # display.
            base = os.path.basename(ulow)
            stem, ext = os.path.splitext(base)
            prev_idx = seen_stems.get(stem)
            if prev_idx is not None:
                prev_url = detail["screenshots"][prev_idx]["url"]
                prev_is_web = any(prev_url.lower().endswith(e) for e in web_image_exts)
                cur_is_web = ext in web_image_exts
                if cur_is_web and not prev_is_web:
                    # Replace the non-web entry with this web-renderable one.
                    seen_urls.discard(prev_url)
                    seen_urls.add(url)
                    detail["screenshots"][prev_idx] = {
                        "url":  url,
                        "type": str(a.get("type") or ""),
                    }
                continue
            seen_urls.add(url)
            seen_stems[stem] = len(detail["screenshots"])
            detail["screenshots"].append({
                "url":  url,
                "type": str(a.get("type") or ""),
            })

    # Prefer a "running" or "loading" screen as the very first frame.
    def _shot_priority(s):
        t = (s.get("type") or "").lower()
        if "running" in t:   return 0
        if "loading" in t:   return 1
        if "screen"  in t:   return 2
        return 3
    detail["screenshots"].sort(key=_shot_priority)
    if detail["screenshots"]:
        detail["screenshot_url"] = detail["screenshots"][0]["url"]

    # Releases / downloads – collect every file we can find:
    #   * releases[].files                 (game tape/disk images)
    #   * releases[].additionals           (per-release manuals, poke files, scans)
    #   * top-level src["additionals"]     (general manuals, poke files, scans)
    seen_dl_urls = set()

    def _add_download(entry: dict, release_year: str = ""):
        if not isinstance(entry, dict):
            return
        url = _abs_url(entry.get("path") or entry.get("url") or entry.get("downloadPath"))
        if not url or url in seen_dl_urls:
            return
        seen_dl_urls.add(url)
        try:
            host = urllib.parse.urlparse(url).netloc or ""
        except Exception:
            host = ""
        fname = os.path.basename(urllib.parse.urlparse(url).path) or ""
        detail["downloads"].append({
            "url":      url,
            "format":   str(entry.get("format") or ""),
            "type":     str(entry.get("type") or entry.get("format") or ""),
            "size":     str(entry.get("size") or ""),
            "filename": fname,
            "source":   host or "zxinfo",
            "year":     str(release_year or entry.get("yearOfRelease") or ""),
        })

    releases = src.get("releases") or []
    if isinstance(releases, list):
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            release_year = rel.get("yearOfRelease") or rel.get("year") or ""
            for key in ("files", "dl", "additionals", "additionalDownloads"):
                items = rel.get(key)
                if isinstance(items, list):
                    for f in items:
                        _add_download(f, release_year)

    for key in ("additionals", "additionalDownloads"):
        top = src.get(key)
        if isinstance(top, list):
            for f in top:
                _add_download(f)

    return detail


# ---------------------------------------------------------------------------
# zxART (zxart.ee) helpers
# ---------------------------------------------------------------------------

_RE_ID_ONLY_URL = re.compile(r"/id:\d+/?$", re.IGNORECASE)

def _filter_download_urls(downloads: list) -> list:
    """Remove entries whose URL looks like a bare id-only path with no file
    extension (e.g. ``…/id:12345/``).  These are API browse URLs, not
    downloadable files, and will not work as direct downloads."""
    if not downloads:
        return downloads
    filtered = []
    for d in downloads:
        url = (d.get("url") or "").strip()
        if not url:
            continue
        path = urllib.parse.urlparse(url).path
        if _RE_ID_ONLY_URL.search(path):
            continue
        filtered.append(d)
    return filtered


def zxart_safe_url(url: str) -> str:
    """Percent-encode any non-ASCII characters in *url* so the request can be
    sent over HTTP. Some zxArt asset URLs (e.g. Clive prod images) include
    Cyrillic characters in their filenames which would otherwise cause
    ``UnicodeEncodeError`` inside ``http.client``."""
    try:
        if not url:
            return url
        # Already pure ASCII -> nothing to do.
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass
    try:
        parts = urllib.parse.urlsplit(url)
        # Preserve reserved characters that are legal in their respective
        # components; only percent-encode the bytes that are not valid ASCII.
        path     = urllib.parse.quote(parts.path,     safe="/:@!$&'()*+,;=-._~%")
        query    = urllib.parse.quote(parts.query,    safe="=&%:/@!$'()*+,;-._~")
        fragment = urllib.parse.quote(parts.fragment, safe="=&%:/@!$'()*+,;-._~")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
    except Exception:
        return url


def zxart_fetch_json(path: str, timeout: int = 15, _retries: int = 3, _backoff: float = 1.5):
    """GET JSON from the zxART API. *path* is appended to ZXART_BASE_URL.
    Sends the mandatory User-Agent header on every request.
    Retries up to *_retries* times on transient HTTP errors with exponential back-off."""
    import urllib.error
    url = zxart_safe_url(ZXART_BASE_URL + path)
    delay = _backoff
    last_exc = None
    for attempt in range(1, _retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ZXART_USER_AGENT,
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in _HTTP_RETRYABLE:
                last_exc = exc
                exc.close()
                logging.warning(
                    "zxart_fetch_json: HTTP %d on attempt %d/%d for %s",
                    exc.code, attempt, _retries, path,
                )
                if attempt < _retries:
                    time.sleep(delay)
                    delay *= 2
            else:
                raise
        except OSError as exc:
            # urllib.error.URLError (subclass of OSError) wraps socket errors
            # such as ConnectionResetError (WinError 10054) — all retried here.
            last_exc = exc
            if attempt < _retries:
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc


def zxart_fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch raw bytes from any URL, identifying as zxART user agent."""
    return _http_fetch_bytes_with_retry(
        zxart_safe_url(url), headers={"User-Agent": ZXART_USER_AGENT}, timeout=timeout
    )


# Process-level caches for zxArt author / group name lookups.
# The API answers one entity per call, so we memoize to avoid re-querying.
# These dicts are read from the UI thread (table population) and written from
# background worker threads (prefetch / progressive resolve), so every access
# is guarded by _ZXART_NAME_CACHE_LOCK to make check-then-set atomic and avoid
# data races. All caches are kept entirely in memory for the process lifetime.
_ZXART_NAME_CACHE_LOCK = threading.RLock()
_ZXART_AUTHOR_NAME_CACHE: dict = {}
_ZXART_GROUP_NAME_CACHE:  dict = {}
_ZXART_PUBLISHER_NAME_CACHE: dict = {}


def _zxart_resolve_author_name(author_id) -> str:
    """Resolve a numeric zxArt authorId to its display title via the API.

    Uses the documented /export:author/filter:authorId=<id>/ endpoint.
    Returns the title string, or "" if the lookup fails / yields nothing.
    Results are cached for the lifetime of the process.
    """
    if author_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(author_id)
    except (TypeError, ValueError):
        return str(author_id)
    with _ZXART_NAME_CACHE_LOCK:
        if key in _ZXART_AUTHOR_NAME_CACHE:
            return _ZXART_AUTHOR_NAME_CACHE[key]
    name = ""
    try:
        resp = zxart_fetch_json(f"/export:author/filter:authorId={key}/")
        rows = (resp.get("responseData") or {}).get("author") or []
        if rows:
            name = str(rows[0].get("title") or "")
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_AUTHOR_NAME_CACHE[key] = name
    return name


def _zxart_resolve_group_name(group_id) -> str:
    """Resolve a numeric zxArt groupId to its display title via the API.

    Uses the documented /export:group/filter:groupId=<id>/ endpoint.
    Returns the title string, or "" if the lookup fails / yields nothing.
    Results are cached for the lifetime of the process.
    """
    if group_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(group_id)
    except (TypeError, ValueError):
        return str(group_id)
    with _ZXART_NAME_CACHE_LOCK:
        if key in _ZXART_GROUP_NAME_CACHE:
            return _ZXART_GROUP_NAME_CACHE[key]
    name = ""
    try:
        resp = zxart_fetch_json(f"/export:group/filter:groupId={key}/")
        rows = (resp.get("responseData") or {}).get("group") or []
        if rows:
            name = str(rows[0].get("title") or "")
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_GROUP_NAME_CACHE[key] = name
    return name


def _zxart_resolve_author_names(author_ids) -> str:
    """Resolve a list of authorIds to a comma-separated display string.

    Unknown IDs fall back to the raw numeric value so we never silently
    drop information.
    """
    out = []
    for aid in author_ids or []:
        name = _zxart_resolve_author_name(aid)
        out.append(name if name else str(aid))
    return ", ".join(s for s in out if s)


def _zxart_resolve_group_names(group_ids) -> str:
    """Resolve a list of groupIds to a comma-separated display string."""
    out = []
    for gid in group_ids or []:
        name = _zxart_resolve_group_name(gid)
        out.append(name if name else str(gid))
    return ", ".join(s for s in out if s)


def _zxart_resolve_publisher_name(publisher_id) -> str:
    """Resolve a numeric zxArt publisherId to a display name.

    The public zxArt API has no working ``export:publisher`` entity, but in
    practice publisher ids are reused from the ``group`` namespace
    (e.g. publisherId ``366520`` is the same as groupId ``366520`` →
    ``ZX Online``). We therefore look the id up via ``export:group`` first.
    Falls back to the raw numeric id so the caller never gets an empty value.
    """
    if publisher_id in (None, "", 0, "0"):
        return ""
    name = _zxart_resolve_group_name(publisher_id)
    if name:
        return name
    return str(publisher_id)


def _zxart_resolve_publisher_names(publisher_ids) -> str:
    """Resolve a list of zxArt publisher ids to a comma-separated string.

    Uses :func:`_zxart_resolve_publisher_name` which treats publisher ids as
    group ids — that matches the actual zxArt data model where the same
    numeric id is reused across the two namespaces.
    """
    out = []
    for pid in publisher_ids or []:
        name = _zxart_resolve_publisher_name(pid)
        out.append(name if name else str(pid))
    return ", ".join(s for s in out if s)


def _zxart_scrape_publishers_from_prod_url(prod_url: str) -> str:
    """Fetch the English zxArt landing page for *prod_url* and return the
    publisher name(s) parsed from its ``<meta name="description">`` tag,
    which is rendered as ``... published by <Publisher> in <Year>``.

    Returns an empty string if the URL is missing or the pattern is not
    found. Result is cached per-URL for the process lifetime.
    """
    if not prod_url:
        return ""
    url = str(prod_url)
    # Force the English landing page so the meta description is in English
    # regardless of which localized URL the API returned.
    if "/rus/soft/" in url:
        url = url.replace("/rus/soft/", "/eng/software/")
    elif "/rus/" in url:
        url = url.replace("/rus/", "/eng/")
    cache_key = ("prod_url", url)
    with _ZXART_NAME_CACHE_LOCK:
        if cache_key in _ZXART_PUBLISHER_NAME_CACHE:
            return _ZXART_PUBLISHER_NAME_CACHE[cache_key]
    name = ""
    try:
        html = _http_fetch_bytes_with_retry(
            url,
            headers={
                "User-Agent":      "Mozilla/5.0 ZX-Next-Unite",
                "Accept-Language": "en",
            },
            timeout=15,
        ).decode("utf-8", errors="replace")
        m = re.search(r"published by ([^\"<]+?) in \d{4}", html, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_PUBLISHER_NAME_CACHE[cache_key] = name
    return name


def _zxart_resolve_publishers_via_zxdb(title: str, year: str = "") -> str:
    """Cross-reference a zxArt production *title* against the ZXDB (ZXInfo)
    API to recover a human-readable publisher name when zxArt only exposes
    an opaque numeric publisher id.

    Strategy: search ZXDB by title (mode=tit) and return the publishers of
    the best hit. If *year* is provided, prefer hits whose
    ``originalYearOfRelease`` / ``yearOfRelease`` matches. Falls back to
    release-level publishers when the top-level record has none. Results
    are cached per (title, year) for the process lifetime.
    """
    if not title:
        return ""
    t = str(title).strip()
    if not t:
        return ""
    y = str(year or "").strip()
    cache_key = ("zxdb_title", t.lower(), y)
    with _ZXART_NAME_CACHE_LOCK:
        if cache_key in _ZXART_PUBLISHER_NAME_CACHE:
            return _ZXART_PUBLISHER_NAME_CACHE[cache_key]
    name = ""
    try:
        q = urllib.parse.quote(t)
        payload = zxdb_fetch_json(f"/search?query={q}&mode=tit&size=10")
        hits = ((payload or {}).get("hits") or {}).get("hits") or []
        # Prefer year-matched hits when a year is provided.
        def _pubs_from_hit(hit):
            d = zxdb_parse_game_detail(hit)
            return str(d.get("publishers") or "")
        def _year_of(hit):
            s = hit.get("_source") or {}
            return str(s.get("originalYearOfRelease")
                       or s.get("yearOfRelease")
                       or s.get("year") or "")
        if y:
            for h in hits:
                if _year_of(h) == y:
                    p = _pubs_from_hit(h)
                    if p:
                        name = p
                        break
        if not name:
            for h in hits:
                p = _pubs_from_hit(h)
                if p:
                    name = p
                    break
    except Exception:
        name = ""
    with _ZXART_NAME_CACHE_LOCK:
        _ZXART_PUBLISHER_NAME_CACHE[cache_key] = name
    return name


def _zxart_prefetch_names_for_entries(entries):
    """Pre-warm the group / publisher name caches for *entries*.

    Intended to be called from a background thread immediately after fetching
    a page of results.  That way :func:`_zxart_table_author_col` only hits
    the in-memory cache when it runs on the UI thread, keeping the UI smooth.

    This warms the *entire* resolution chain used by the author/group table
    column — group ids, publisher ids, and the HTML scrape fallback — so the
    UI thread never performs a network request while populating the table.

    Picture entries are skipped because their author field is already a plain
    string and they don't use group / publisher IDs.
    """
    for e in entries:
        src  = e.get("_source") or {}
        kind = (e.get("_kind") or "").lower()
        if kind == "zxart_picture":
            continue
        # 1. Groups (drives "Produced by").
        groups = [str(g) for g in (src.get("groups") or []) if g]
        if not groups:
            groups = [n for n in [_zxart_resolve_group_name(gid)
                                  for gid in (src.get("groupsIds") or [])] if n]
        # 2. Publishers (drives "Published by"); resolve ids then, if still
        #    empty, warm the HTML scrape fallback so the UI thread won't block.
        pub_ids = src.get("publishersIds") or []
        published_by = _zxart_resolve_publisher_names(pub_ids)
        if not published_by:
            _zxart_scrape_publishers_from_prod_url(str(src.get("url") or ""))


# ---------------------------------------------------------------------------
# Cache-only (non-blocking) zxArt name resolution.
#
# The functions above may perform a network request on a cold cache.  The UI
# thread must never do that, so the helpers below ONLY consult the in-memory
# caches and return None on a miss.  Callers running on the GUI thread use
# these to render immediately and schedule a background warm-up if needed.
# ---------------------------------------------------------------------------

def _zxart_cached_group_name(group_id):
    """Return the cached group title, or None if not yet resolved."""
    if group_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(group_id)
    except (TypeError, ValueError):
        return str(group_id)
    with _ZXART_NAME_CACHE_LOCK:
        return _ZXART_GROUP_NAME_CACHE.get(key)


def _zxart_cached_publisher_name(publisher_id):
    """Return the cached publisher title (group namespace), or None on miss."""
    if publisher_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(publisher_id)
    except (TypeError, ValueError):
        return str(publisher_id)
    with _ZXART_NAME_CACHE_LOCK:
        return _ZXART_GROUP_NAME_CACHE.get(key)


def _zxart_cached_scraped_publisher(prod_url: str):
    """Return the cached scraped publisher for *prod_url*, or None on miss."""
    if not prod_url:
        return ""
    url = str(prod_url)
    if "/rus/soft/" in url:
        url = url.replace("/rus/soft/", "/eng/software/")
    elif "/rus/" in url:
        url = url.replace("/rus/", "/eng/")
    with _ZXART_NAME_CACHE_LOCK:
        return _ZXART_PUBLISHER_NAME_CACHE.get(("prod_url", url))


def _zxart_author_col_cached(e):
    """Cache-only version of the table's author/group column.

    Returns a tuple ``(text, complete)``:
      * ``text``     — the best string we can build from in-memory caches.
      * ``complete`` — True if every required name was already cached (so no
        background warm-up is needed); False if a network lookup is still
        pending (the caller should warm the cache off the UI thread and then
        refresh the cell).

    This function performs NO network I/O and is safe to call on the GUI
    thread while populating a results table.
    """
    src  = e.get("_source") or {}
    kind = (e.get("_kind") or "").lower()
    if kind == "zxart_picture":
        return (e.get("author", ""), True)

    complete = True

    # 1. Groups -> "Produced by".
    groups = [str(g) for g in (src.get("groups") or []) if g]
    if not groups:
        for gid in (src.get("groupsIds") or []):
            name = _zxart_cached_group_name(gid)
            if name is None:
                complete = False
            elif name:
                groups.append(name)
    produced_by = ", ".join(groups)

    # 2. Authors (direct strings) when there are no groups.
    if not produced_by:
        authors = [str(a) for a in (src.get("authors") or []) if a]
        if authors:
            return (", ".join(authors), True)

    # 3. Publishers -> "Published by".
    published_by_parts = []
    for pid in (src.get("publishersIds") or []):
        name = _zxart_cached_publisher_name(pid)
        if name is None:
            complete = False
        elif name:
            published_by_parts.append(name)
    published_by = ", ".join(published_by_parts)
    if not published_by:
        scraped = _zxart_cached_scraped_publisher(str(src.get("url") or ""))
        if scraped is None:
            complete = False
        elif scraped:
            published_by = scraped

    parts = []
    if produced_by:  parts.append(f"Produced by: {produced_by}")
    if published_by: parts.append(f"Published by: {published_by}")
    text = " · ".join(parts) if parts else e.get("author", "")
    return (text, complete)


def zxart_parse_prod_list(response: dict) -> tuple:
    """Parse a zxART API response for zxProd entities.

    Returns (entries, total) where each entry has keys:
    id, title, year, author, machine, genre, _kind, _source.
    """
    entries = []
    if not isinstance(response, dict):
        return entries, 0

    total = 0
    try:
        total = int(response.get("totalAmount") or 0)
    except (TypeError, ValueError):
        pass

    prods = (response.get("responseData") or {}).get("zxProd", [])
    if not isinstance(prods, list):
        prods = []

    for prod in prods:
        if not isinstance(prod, dict):
            continue
        pid   = str(prod.get("id") or "")
        title = str(prod.get("title") or "")
        year  = str(prod.get("year") or "")
        # groupsIds -> we resolve names separately when detail is loaded;
        # use description as author placeholder
        authors_info = prod.get("authorsInfo") or []
        group_ids    = prod.get("groupsIds") or []
        author_hint  = ""
        if group_ids:
            author_hint = f"{len(group_ids)} group(s)"
        elif authors_info:
            author_hint = f"{len(authors_info)} author(s)"

        compo = str(prod.get("compo") or "")
        party_place = prod.get("partyPlace")
        genre = compo or ""
        if party_place:
            genre = f"{genre} (#{party_place})" if genre else f"#{party_place}"

        entries.append({
            "id":      pid,
            "title":   title,
            "year":    year,
            "author":  author_hint,
            "machine": ", ".join(str(h) for h in (prod.get("hardwareRequired") or [])),
            "genre":   genre,
            "_kind":   "zxart_prod",
            "_source": prod,
        })

    return entries, total


# Letter-to-approximate-offset table for the zxART Games category (zxProdCategory=92177,
# ~23 000 entries ordered by title,asc).  Values are conservative lower-bound offsets so
# that a ±500-item window centred on the estimate reliably contains all titles starting
# with the requested letter.
# Category ID in zxART that covers all software productions (games + demos)
# Selected zxART API language ("eng" | "pol" | "spa").  Mutated by the
# language combo in the zxArt pane and persisted to the cfg file.  All
# zxART HTTP request builders use _zxart_lang() to honour this value.



def _zxart_title_at(offset: int) -> str:
    """Return the lowercase title at ``offset`` within the title-asc ordering.

    Uses a ``limit:1`` probe which is reliable at any depth in the catalog
    (unlike larger windows which can hit HTTP 500 on some offsets).
    Returns an empty string on error.
    """
    try:
        resp = zxart_fetch_json(
            f"/export:zxProd/language:{_zxart_lang()}/start:{offset}/limit:1"
            f"/order:title,asc",
            timeout=15,
        )
        prods = (resp.get("responseData") or {}).get("zxProd", [])
        if isinstance(prods, list) and prods:
            return str(prods[0].get("title") or "").lower()
    except Exception as exc:
        logging.warning("_zxart_title_at(%d) failed: %s", offset, exc)
    return ""


def zxart_prefix_search(query: str, progress_cb=None,
                        window: int = 200, max_results: int = 200) -> tuple:
    """Find zxART productions whose title *starts with* ``query``.

    Strategy:
      1. Probe the total catalog size via the sentinel.
      2. Binary-search the title-asc ordering using single-item probes
         to locate the first offset whose title >= query.
      3. Fetch a small window starting at that offset and keep entries
         whose lowercase title starts with the query.

    Issues roughly log2(N) + 1 small requests (~16 total for 23k entries).
    Each request is a few hundred bytes to a few KB.  Returns
    ``(entries, total_matched)``.
    """
    if not query:
        return [], 0

    q_lower = query.lower()

    def _notify(msg: str):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    _notify(f"Searching zxART for titles starting with '{query}'…")

    # 1. catalog size — cheap single-item probe
    try:
        resp0 = zxart_fetch_json(
            f"/export:zxProd/language:{_zxart_lang()}/start:0/limit:1/order:date,desc",
            timeout=10,
        )
        total = int(resp0.get("totalAmount") or 0)
    except Exception:
        total = 0
    if total <= 0:
        total = 1000

    # 2. binary search for the first offset whose title >= query
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi) // 2
        title = _zxart_title_at(mid)
        if not title:
            # treat unknown as "past the query" so we shrink to the lower half
            hi = mid
            continue
        if title < q_lower:
            lo = mid + 1
        else:
            hi = mid

    start = max(0, lo - 5)  # small back-step in case of rounding

    # 3. fetch a window and filter client-side
    path = (
        f"/export:zxProd/language:{_zxart_lang()}/start:{start}/limit:{window}"
        f"/order:title,asc"
    )
    try:
        resp = zxart_fetch_json(path, timeout=30)
        entries, _ = zxart_parse_prod_list(resp)
    except Exception as exc:
        # If the window straddles a known bad offset, shrink it.
        logging.warning("zxart_prefix_search window fetch failed: %s — retrying smaller", exc)
        entries = []
        for sub_off in range(start, start + window, 25):
            try:
                resp = zxart_fetch_json(
                    f"/export:zxProd/language:{_zxart_lang()}/start:{sub_off}/limit:25"
                    f"/order:title,asc",
                    timeout=20,
                )
                sub, _ = zxart_parse_prod_list(resp)
                entries.extend(sub)
            except Exception as sub_exc:
                logging.warning("zxart_prefix_search sub-window %d failed: %s", sub_off, sub_exc)

    # The window may begin slightly before the prefix and extend slightly past
    # it.  Trim to the contiguous run of entries that start with the query.
    matched = []
    seen_match = False
    for e in entries:
        title = (e.get("title") or "").lower()
        if title.startswith(q_lower):
            matched.append(e)
            seen_match = True
        elif seen_match:
            # past the prefix range — done
            break

    for e in matched:
        e["_kind"] = "zxart_prod"
    _notify("")
    return matched[:max_results], len(matched)


def zxart_client_search(query: str, progress_cb=None) -> tuple:
    """Search zxART productions by title using fast prefix search.

    Returns (matched_entries, total_matched).
    """
    if not query:
        return [], 0
    return zxart_prefix_search(query, progress_cb=progress_cb)


def zxart_parse_picture_list(response: dict) -> tuple:
    """Parse a zxART API response for zxPicture entities."""
    entries = []
    if not isinstance(response, dict):
        return entries, 0

    total = 0
    try:
        total = int(response.get("totalAmount") or 0)
    except (TypeError, ValueError):
        pass

    pics = (response.get("responseData") or {}).get("zxPicture", [])
    if not isinstance(pics, list):
        pics = []

    for pic in pics:
        if not isinstance(pic, dict):
            continue
        pid   = str(pic.get("id") or "")
        title = str(pic.get("title") or "")
        year  = str(pic.get("year") or "")
        rating = str(pic.get("rating") or "")
        tags   = pic.get("tags") or []
        genre  = ", ".join(str(t) for t in tags[:3]) if tags else ""

        entries.append({
            "id":      pid,
            "title":   title,
            "year":    year,
            "author":  "",
            "machine": str(pic.get("type") or ""),
            "genre":   genre,
            "_kind":   "zxart_picture",
            "_source": pic,
        })

    return entries, total


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


def getit_run_in_thread(fn, on_result, on_error):
    """Run *fn* in a daemon thread. Results are marshalled to the main thread
    via Qt queued signal connections, which are thread-safe.

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

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t







def _apply_completer_fix_to_children(widget: QWidget):
    for child in widget.findChildren(QWidget):
        # If the child itself uses a completer, replace it
        if isinstance(child, QLineEdit):
            comp = child.completer()
            if comp is not None:
                _ensure_completer_is_movable(comp)
        # Recursively patch deeper levels
        _apply_completer_fix_to_children(child)

class MainWindow(QMainWindow):

    def _show_toast(self, title: str, message: str = "", *, variant: str = "green",
                    duration_ms: int = 10000):
        """Show a small, auto-dismissing toast in the bottom-right corner.

        ``variant`` selects the colour scheme:
          - "green"  : success / informational (default)
          - "yellow" : warning / advisory

        The toast disappears automatically after ``duration_ms`` milliseconds,
        or immediately when the user clicks the OK button.
        """
        # Colour schemes per variant: (bg, border, title_fg, btn_bg, btn_border,
        # btn_hover).
        if variant == "yellow":
            scheme = ("#2e2a14", "#f0c000", "#f7eec5", "#7d6a2e", "#f0c000", "#8f7c38")
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

            # Position in the bottom-right corner of the main window.
            try:
                geo = self.frameGeometry()
                x = geo.right() - toast.width() - 24
                y = geo.bottom() - toast.height() - 24
                toast.move(max(geo.left() + 8, x), max(geo.top() + 8, y))
            except Exception:
                pass

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

    def _show_emulator_detection_toast(self):
        """Show a startup toast reporting which emulators (CSpect / MAME) were
        detected on the system. A green toast lists the emulators found; if none
        are found a yellow advisory toast is shown instead. Auto-dismisses after
        5 seconds.
        """
        found = []
        if getattr(self, "_cspect_executable_path", None):
            found.append("CSpect")
        if getattr(self, "_mame_executable_path", None):
            found.append("Mame")

        if found:
            self._show_toast(
                "\u2705  Emulator(s) detected",
                "Found: " + " and ".join(found) + ".",
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
        # Optional pygame-ce "Alien Floyd's" features (both default off).
        configuration_dictionary[SETTING_ALIEN_FLOYD_BG] = ""
        configuration_dictionary[SETTING_ALIEN_FLOYD_TAB] = ""
        configuration_dictionary[SETTING_ALIEN_FLOYD_HISCORE] = "0"

        # Detect whether the MAME emulator is available on the system PATH
        # (mame.exe on Windows, mame elsewhere). When present, a "Launch Mame"
        # button is shown next to "Launch CSpect".
        self._mame_executable_path = find_mame_executable()

        # Detect whether the CSpect emulator is available (application directory
        # or PATH). When absent, all CSpect controls are hidden.
        self._cspect_executable_path = find_cspect_executable()

        # Live QColor instances for the image explorer — updated by Settings pickers
        self.img_color_up_directory = hex_to_qcolor(DEFAULT_COLOR_UP_DIRECTORY)
        self.img_color_dir_name     = hex_to_qcolor(DEFAULT_COLOR_DIR_NAME)
        self.img_color_dir_type     = hex_to_qcolor(DEFAULT_COLOR_DIR_TYPE)
        self.img_color_file_name    = hex_to_qcolor(DEFAULT_COLOR_FILE_NAME)
        self.img_color_file_ext     = hex_to_qcolor(DEFAULT_COLOR_FILE_EXT)
        self.img_color_file_size    = hex_to_qcolor(DEFAULT_COLOR_FILE_SIZE)

        self.left_file_explorer_selection_file_name = ""
        self.left_file_explorer_selection_full_filename_path = ""
        self.left_file_nextsync_explorer_selection_file_name = ""
        self.left_file_nextsync_explorer_selection_full_filename_path = ""

        # Gallery (picture view) defaults — may be overridden when the cfg file loads.
        self._gallery_anim_mode      = DEFAULT_GALLERY_ANIM_MODE
        self._gallery_rows_per_page  = DEFAULT_GALLERY_ROWS_PER_PAGE
        self._gallery_cols           = DEFAULT_GALLERY_COLS
        self._gallery_img_size       = DEFAULT_GALLERY_IMG_SIZE
        self._getit_view_mode        = "gallery"
        self._zxdb_view_mode         = "gallery"
        self._zxart_view_mode        = "gallery"
        self._favorites_view_mode    = "gallery"
        self._allinone_view_mode     = "gallery"

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

        def nextsync_hide_start_cancel_buttons():
            self.nextsync_start_server.setVisible(False)
            self.nextsync_cancel_server.setVisible(False)

        def nextsync_show_start_cancel_buttons():
            self.nextsync_start_server.setVisible(True)
            self.nextsync_cancel_server.setVisible(True)


        def set_all_buttons_disabled():

            self.imageinput.setDisabled(True)
            self.selectimage.setDisabled(True)
            self.zx_next_unite_diskdrive.setDisabled(True)
            self.filterlabel.setDisabled(True)
            self.filtertext.setDisabled(True)
            self.treeview.setDisabled(True)
            self.button_to_disk.setDisabled(True)
            self.button_to_image.setDisabled(True)
            self.TableWidgetImage.setDisabled(True)
            self.button_new_folder.setDisabled(True)
            self.button_delete_files.setDisabled(True)
            self.button_cancel.setDisabled(True)
            self.button_confirm_deletion.setDisabled(True)
            self.new_folder_input.setDisabled(True)
            self.button_create_directory.setDisabled(True)
            self.button_start_cspect.setDisabled(True)
            self.button_start_mame.setDisabled(True)
            self.cspect_screensize.setDisabled(True)
            self.cspect_sound.setDisabled(True)
            self.cspect_vsync.setDisabled(True)
            self.cspect_joystick.setDisabled(True)
            self.cspect_frequency.setDisabled(True)
            self.button_open_config_file.setDisabled(True)

        def set_all_buttons_enabled():
            self.imageinput.setDisabled(False)
            self.selectimage.setDisabled(False)
            self.zx_next_unite_diskdrive.setDisabled(False)
            self.filterlabel.setDisabled(False)
            self.filtertext.setDisabled(False)
            self.treeview.setDisabled(False)
            self.button_to_disk.setDisabled(False)
            self.button_to_image.setDisabled(False)
            self.TableWidgetImage.setDisabled(False)
            self.button_new_folder.setDisabled(False)
            self.button_delete_files.setDisabled(False)
            self.button_cancel.setDisabled(False)
            self.button_confirm_deletion.setDisabled(False)
            self.new_folder_input.setDisabled(False)
            self.button_create_directory.setDisabled(False)
            self.button_start_cspect.setDisabled(False)
            self.button_start_mame.setDisabled(False)
            self.cspect_screensize.setDisabled(False)
            self.cspect_sound.setDisabled(False)
            self.cspect_vsync.setDisabled(False)
            self.cspect_joystick.setDisabled(False)
            self.cspect_frequency.setDisabled(False)
            self.button_open_config_file.setDisabled(False)

        def enable_image_selection():
            self.imageinput.setDisabled(False)
            self.selectimage.setDisabled(False)

        def disable_image_selection():
            self.imageinput.setDisabled(True)
            self.selectimage.setDisabled(True)

        def download_and_install_hdflonkey():
            try:
                # Set a proper User-Agent header to avoid connection rejection
                opener = urllib.request.build_opener()
                opener.addheaders = [('User-Agent', ZXART_USER_AGENT)]
                urllib.request.install_opener(opener)

                zip_path, _ = urllib.request.urlretrieve(HDF_MONKEY_WINDOWS_URL)
                with zipfile.ZipFile(zip_path, "r") as f:
                    f.extractall()
                self.button_new_folder.setVisible(True)
                self.button_delete_files.setVisible(True)
                self.download_and_install_hdfmonkey_button.setVisible(False)
                logging.info("Successfully installed hdfmonkey.")
                add_main_log_window("Successfully installed hdfmonkey.")

                if is_hdfmonkey_present():
                    load_image()
                    set_all_buttons_enabled()

                return True
            except Exception as e:
                logging.error(f"Failed downloading & installing hdfmonkey: {e}, please download and install manually in current folder the executable from: {HDF_MONKEY_WINDOWS_URL} ")
                add_main_log_window(f"Failed downloading & installing hdfmonkey: {e}, please download and install manually in current folder the executable from: {HDF_MONKEY_WINDOWS_URL} ")
                #set_all_buttons_enabled()
                return False

        def show_hdf_monkey_download_and_install_buttons():
            self.download_and_install_hdfmonkey_button.setVisible(True)
            self.button_new_folder.setVisible(False)
            self.button_delete_files.setVisible(False)


        # def tab_changed():
        #     # Do nothing for now has this event happens before rendering the tab
        #     # get_pyhdfmgooey_currenttab_config()

        def load_configuration_file():

            config_loaded_with_success = False

            try:

                # Load configuration dictionary
                pass

                with open(ZX_NEXT_UNITE_CONFIG_FILE_NAME, "r") as config_file:
                    for line in config_file:
                        config_setting_name, config_setting_value = line.strip().split('=', 1)
                        configuration_dictionary[config_setting_name] = config_setting_value


                #  Now set the settings back to the application SETTING_SCREENSIZE and others

                # Restore image history into the combo (most-recent-first list stored as '|'-delimited)
                history_raw = configuration_dictionary.get(SETTING_IMAGE_HISTORY, "")
                if history_raw:
                    history_entries = [p for p in history_raw.split("|") if p.strip()]
                    self.imageinput.blockSignals(True)
                    self.imageinput.clear()
                    for entry in history_entries[:MAX_IMAGE_HISTORY]:
                        self.imageinput.addItem(entry)
                    self.imageinput.blockSignals(False)

                # Set the active image path (most recently used)
                current_hddfile = configuration_dictionary[SETTING_HDDFILE]
                self.imageinput.setCurrentText(current_hddfile)
                self.cspect_sound.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_SOUND]))
                self.cspect_screensize.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_SCREENSIZE]))
                self.cspect_vsync.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_VSYNC]))
                self.cspect_joystick.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_JOYSTICK]))
                self.cspect_frequency.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_HERTZ]))

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
                    self.file_explorer_path.setText(self.left_file_explorer_selection_full_filename_path)

                if configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] != "":
                    if not os.path.isdir(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH]):
                        configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = os.path.dirname(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH].rstrip("/\\")) + "/"


                    self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH])))
                    self.left_file_nextsync_explorer_selection_full_filename_path = configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH]
                    self.nextsync_file_explorer_path.setText(self.left_file_nextsync_explorer_selection_full_filename_path)

                if configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] != "":
                    if configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] == "1" or configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE].lower() == "true":
                        self.nextsync_synconce_checkbox.setChecked(True)
                    else:
                        self.nextsync_synconce_checkbox.setChecked(False)

                if configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] != "":
                    if configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] == "1" or configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC].lower() == "true":
                        self.nextsync_alwayssync_checkbox.setChecked(True)
                    else:
                        self.nextsync_alwayssync_checkbox.setChecked(False)

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
                    self.settings_mame_params_edit.blockSignals(True)
                    self.settings_mame_params_edit.setText(_params)
                    self.settings_mame_params_edit.blockSignals(False)
                    configuration_dictionary[SETTING_MAME_COMMAND_LINE_PARAMETERS] = _params
                # Ensure runtime state matches the persisted setting (the
                # early-bootstrap read already honoured this, but reapply here
                # so any cfg edits made between launches take immediate effect).
                try:
                    _zxnu_set_crash_log_enabled(_crash_checked)
                except Exception:
                    pass

                # Gallery animation mode: "hover" (default) or "timer"
                if SETTING_GALLERY_ANIM_MODE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_ANIM_MODE] != "":
                    val = configuration_dictionary[SETTING_GALLERY_ANIM_MODE].strip().lower()
                    if val in ("hover", "timer"):
                        self._gallery_anim_mode = val
                        cb = getattr(self, "settings_gallery_anim_combo", None)
                        if cb is not None:
                            for _i in range(cb.count()):
                                if cb.itemData(_i) == val:
                                    cb.setCurrentIndex(_i)
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

                # Per-pane view mode: "table" (default) or "gallery"
                for _pane_key, _attr in (
                    (SETTING_GETIT_VIEW_MODE, "_getit_view_mode"),
                    (SETTING_ZXDB_VIEW_MODE,  "_zxdb_view_mode"),
                    (SETTING_ZXART_VIEW_MODE, "_zxart_view_mode"),
                    (SETTING_FAVORITES_VIEW_MODE, "_favorites_view_mode"),
                    (SETTING_ALLINONE_VIEW_MODE, "_allinone_view_mode"),
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

                # Alien Floyd's (pygame-ce) optional background + dedicated tab
                # (both default off). Disable the controls when pygame-ce is not
                # installed, but leave the saved preferences untouched.
                try:
                    # Seed the persisted arcade high score and wire the saver.
                    try:
                        import zxnu_pygame as _zpg_hs
                        _hs_raw = configuration_dictionary.get(
                            SETTING_ALIEN_FLOYD_HISCORE, "").strip()
                        _zpg_hs.init_alien_hiscore(int(_hs_raw) if _hs_raw else 0)

                        def _save_alien_hiscore(v):
                            configuration_dictionary[SETTING_ALIEN_FLOYD_HISCORE] = str(int(v))
                            try:
                                save_configuration_file()
                            except Exception:
                                pass
                        _zpg_hs.set_alien_hiscore_save_cb(_save_alien_hiscore)
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
                                    "Install with: pip install pygame-ce")
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
                        _bg_dir_load = os.path.dirname(os.path.abspath(sys.argv[0]))
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
                with open(ZX_NEXT_UNITE_CONFIG_FILE_NAME, "w") as config_file:
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

        def is_filetype_a_directory(file_type:str):
            ft = file_type.strip()
            return ft == "[DIR]" or ft == "b'[DIR]" or ft == 'b"[DIR]'

        def get_pyhdfmgooey_currenttab_config():
            configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING] = wid_inner.tab.currentIndex()
            configuration_dictionary[SETTING_HDDFILE] = self.imageinput.currentText()
            configuration_dictionary[SETTING_SCREENSIZE] = self.cspect_screensize.currentIndex()
            configuration_dictionary[SETTING_SOUND] = self.cspect_sound.currentIndex()
            configuration_dictionary[SETTING_VSYNC] = self.cspect_vsync.currentIndex()
            configuration_dictionary[SETTING_JOYSTICK] = self.cspect_joystick.currentIndex()
            configuration_dictionary[SETTING_HERTZ] = self.cspect_frequency.currentIndex()
            # Persist the full history as a '|'-delimited string
            history_items = [self.imageinput.itemText(i) for i in range(self.imageinput.count()) if self.imageinput.itemText(i)]
            configuration_dictionary[SETTING_IMAGE_HISTORY] = "|".join(history_items)
            #save_configuration_file()

        def set_cspect_screen_size():
            configuration_dictionary[SETTING_SCREENSIZE] = self.cspect_screensize.currentIndex()
            save_configuration_file()

        def set_cspect_sound_on_off():
            configuration_dictionary[SETTING_SOUND] = self.cspect_sound.currentIndex()
            save_configuration_file()

        def set_cspect_vsync_on_off():
            configuration_dictionary[SETTING_VSYNC] = self.cspect_vsync.currentIndex()
            save_configuration_file()

        def set_cspect_joystick_on_off():
            configuration_dictionary[SETTING_JOYSTICK] = self.cspect_joystick.currentIndex()
            save_configuration_file()

        def set_cspect_display_frequency():
            configuration_dictionary[SETTING_HERTZ] = self.cspect_frequency.currentIndex()
            save_configuration_file()

        def open_cspect_configuration_file():
            if platform.system() == "Windows":
                execute_shell_command("notepad", ZX_NEXT_UNITE_CONFIG_FILE_NAME)
            else:
                execute_shell_command("vim", "./" + ZX_NEXT_UNITE_CONFIG_FILE_NAME)
            return

        def launch_cspect():
            if right_disk_image_explorer_content:  # check that we have an image content first
                set_all_buttons_disabled()

                cspect_arguments = " " + CSPECT_BASE_ARGUMENTS + " "
                cspect_screensize_text = self.cspect_screensize.currentText()
                cspect_sound_text = self.cspect_sound.currentText()
                cspect_vsync_text = self.cspect_vsync.currentText()
                cspect_joystick_text = self.cspect_joystick.currentText()
                cspect_frequency_text = self.cspect_frequency.currentText()

                cspect_arguments += get_tuple_value(CSPECT_SCREEN_SIZES, cspect_screensize_text) + " "
                cspect_arguments += get_tuple_value(CSPECT_SOUND, cspect_sound_text) + " "
                cspect_arguments += get_tuple_value(CSPECT_SCREEN_SYNC, cspect_vsync_text) + " "
                cspect_arguments += get_tuple_value(CSPECT_JOYSTICK, cspect_joystick_text) + " "
                cspect_arguments += get_tuple_value(CSPECT_FREQUENCY, cspect_frequency_text) + " "

                if configuration_dictionary[SETTING_ESC] != "":
                    cspect_arguments += " -esc "

                if configuration_dictionary[SETTING_CUSTOM] != "":
                    cspect_arguments += " " + configuration_dictionary[SETTING_CUSTOM] + " "

                cspect_arguments += " -mmc=" + self.right_disk_image_path + " "

                logging.info(f"Cspect start with arguments: {cspect_arguments}")
                add_main_log_window(f"Cspect start with arguments: {cspect_arguments}")

                try:
                    if platform.system() == "Windows":
                        execute_shell_command ("CSpect.exe", cspect_arguments)
                        #execute_shell_command_no_wait ("CSpect.exe", cspect_arguments)
                    else:
                        execute_shell_command ("mono CSpect.exe", cspect_arguments)
                except subprocess.CalledProcessError as ex:
                    if ex.returncode == 1:
                        logging.error("CSpect.exe is not present in the same local directory as zx-next-unite.Please install it from http://cspect.org")
                        add_main_log_window("ERROR: CSpect.exe is not present in the same local directory as zx-next-unite.Please install it from http://cspect.org")
                    else:
                        logging.error(f"ERROR: Unknown shell execute error: {ex.returncode} - :{ex}")
                        add_main_log_window(f"ERROR: Unknown shell execute error: {ex.returncode} - :{ex}")

                    if platform.system() != "Windows":
                        logging.error("On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.")
                        add_main_log_window("On MacOS and Linux mono is required as it runs under it. Please make sure mono is installed.")

                set_all_buttons_enabled()


        def launch_mame():
            if not right_disk_image_explorer_content:  # check that we have an image content first
                return

            mame_path = getattr(self, "_mame_executable_path", None)
            if not mame_path:
                logging.error("MAME executable not found on PATH. Cannot launch MAME.")
                add_main_log_window("ERROR: MAME executable not found on PATH. Cannot launch MAME.")
                return

            # Pull the (possibly user-customised) command line from the cfg file,
            # falling back to the built-in default. The literal placeholder
            # {MAME_EXECUTABLE_NAME} is resolved to the detected executable.
            mame_parameters = configuration_dictionary.get(
                SETTING_MAME_COMMAND_LINE_PARAMETERS, MAME_DEFAULT_COMMAND_LINE
            )
            if not mame_parameters:
                mame_parameters = MAME_DEFAULT_COMMAND_LINE
            mame_parameters = mame_parameters.replace("{MAME_EXECUTABLE_NAME}", "").strip()

            # The ROM/system (e.g. "tbblue") is picked by the user in the Settings
            # tab and stored separately; it is inserted right after the executable.
            mame_rom = configuration_dictionary.get(
                SETTING_MAME_ROM_CHOICE, MAME_ROM_CHOICE[0]
            ).strip()
            if not mame_rom:
                mame_rom = MAME_ROM_CHOICE[0]

            # Build: mame + <rom> + MAME_COMMAND_LINE_PARAMETERS + "-hard1" + image
            # The image path is wrapped in double quotes in the combo box; strip
            # them so it is a valid command-line argument. MAME runs from its own
            # install directory (see below), so resolve the image to an absolute
            # path to keep relative paths working. The "-hard1 <image>" pair is
            # appended last so the image is always the final argument.
            mame_image = self.imageinput.currentText().strip().strip('"')
            if mame_image:
                mame_image = os.path.abspath(mame_image)
            mame_argv = [mame_path, mame_rom] + shlex.split(mame_parameters)
            if mame_image:
                mame_argv += [MAME_HARD_DISK_PARAMETER, mame_image]

            logging.info(f"MAME start with arguments: {mame_argv}")
            add_main_log_window(f"MAME start with arguments: {' '.join(mame_argv)}")

            # Launch MAME with its stdout/stderr captured so we can surface any
            # startup error (bad ROM path, missing media, invalid option, etc.)
            # in the log window. The process itself runs in its own session/group
            # so it is detached from the app, and a daemon reader thread drains
            # the pipe without blocking the UI.
            #
            # MAME loads its support files (bgfx shaders, hash/, roms/) relative
            # to its own install directory, so run it from there; otherwise it
            # exits immediately when those files cannot be found.
            mame_cwd = os.path.dirname(mame_path) or None
            try:
                if platform.system() == "Windows":
                    creationflags = 0x00000200  # CREATE_NEW_PROCESS_GROUP
                    mame_proc = subprocess.Popen(
                        mame_argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        text=True,
                        bufsize=1,
                        cwd=mame_cwd,
                        creationflags=creationflags,
                    )
                else:
                    mame_proc = subprocess.Popen(
                        mame_argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        text=True,
                        bufsize=1,
                        cwd=mame_cwd,
                        start_new_session=True,
                    )
            except Exception as ex:
                logging.error(f"ERROR: Failed to launch MAME: {ex}")
                add_main_log_window(f"ERROR: Failed to launch MAME: {ex}")
                return

            # Marshal captured output back to the UI thread via queued signals
            # (Qt widgets must only be touched from the main thread).
            mame_signals = MameProcessSignals()
            mame_signals.output.connect(
                lambda line: add_main_log_window(f"MAME: {line}"),
                Qt.QueuedConnection,
            )

            def _on_mame_finished(return_code):
                add_main_log_window(f"MAME exited with code {return_code}.")
                logging.info(f"MAME exited with code {return_code}.")

            mame_signals.finished.connect(_on_mame_finished, Qt.QueuedConnection)
            # Keep a reference so the signals object is not garbage-collected
            # while the reader thread is still running.
            self._mame_signals = mame_signals

            def _read_mame_output(proc, signals):
                try:
                    if proc.stdout is not None:
                        for raw_line in proc.stdout:
                            line = raw_line.rstrip("\r\n")
                            if line:
                                signals.output.emit(line)
                    proc.wait()
                except Exception as exc:
                    signals.output.emit(f"ERROR reading MAME output: {exc}")
                finally:
                    signals.finished.emit(proc.returncode if proc.returncode is not None else -1)

            threading.Thread(
                target=_read_mame_output,
                args=(mame_proc, mame_signals),
                daemon=True,
            ).start()


        # Expose the emulator launch helpers so other UI surfaces (e.g. the
        # GalleryItemViewer action bars on GetIt / ZXDB / ZxArt) can trigger
        # the same launch logic as the main window buttons.
        self._launch_cspect_fn = launch_cspect
        self._launch_mame_fn   = launch_mame

        def _wire_viewer_emulators(viewer, allow=True):
            """Add "Launch CSpect" / "Launch Mame" buttons to a
            GalleryItemViewer action bar (under "Send to SD card").

            A button is only wired/shown when the matching emulator was
            detected at startup *and* ``allow`` is True.  ``allow`` lets the
            ZXDB/ZxArt panes honour their ENABLE_DOWNLOAD_BUTTONS settings;
            GetIt passes the default (always allowed)."""
            cspect_ok = bool(allow) and getattr(self, "_cspect_executable_path", None) is not None
            mame_ok   = bool(allow) and getattr(self, "_mame_executable_path", None) is not None
            viewer.set_emulator_actions(
                cspect_cb=(self._launch_cspect_fn if cspect_ok else None),
                mame_cb=(self._launch_mame_fn if mame_ok else None),
                cspect_enabled=cspect_ok,
                mame_enabled=mame_ok,
                cspect_tooltip="🕹  Launch CSpect with the loaded SD card image",
                mame_tooltip="🕹  Launch MAME with the loaded image",
            )
        self._wire_viewer_emulators = _wire_viewer_emulators


        def delete_files_button_show_confirmation_buttons():
            if self.settings_no_prompt_on_deletion_checkbox.isChecked():
                button_confirm_directory_deletion()
                return
            self.button_confirm_deletion.setVisible(True)
            self.button_cancel.setVisible(True)
            self.button_new_folder.setVisible(False)
            self.button_delete_files.setVisible(False)


        def button_confirm_directory_deletion():
            image_delete_files()
            self.button_confirm_deletion.setVisible(False)
            self.button_cancel.setVisible(False)
            self.button_new_folder.setVisible(True)
            self.button_delete_files.setVisible(True)

        def button_cancel_deletion():
            self.button_confirm_deletion.setVisible(False)
            self.button_cancel.setVisible(False)
            self.button_new_folder.setVisible(True)
            self.button_delete_files.setVisible(True)

        def is_hdfmonkey_present():

            hdfmonkeyexecresult = execute_hdf_monkey("", "")

            try:
                if hdfmonkeyexecresult.returncode == 0:
                    command_execution = hdfmonkeyexecresult.stdout
                    if "hdfmonkey help" not in str(command_execution):
                        add_main_log_window("Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.")
                        return False
                    else:
                        return True
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

        def load_image():

            global right_disk_image_explorer_content

            # Populate right impage path content
            self.right_disk_image_path = self.imageinput.currentText()

            right_disk_image_explorer_content = []
            self.TableWidgetImage.clear()
            self.TableWidgetImage.setRowCount(0)
            set_table_image_properties()

            if self.right_disk_image_path and self.right_disk_image_path != '""':
                hdfmonkeyexecresult = execute_hdf_monkey("ls", self.right_disk_image_path)

                if hdfmonkeyexecresult.returncode == 0:
                    command_execution = hdfmonkeyexecresult.stdout
                    update_disk_manager_widget_table(command_execution)
                    self.diskimageexplorerlabelpath.setText(generate_disk_file_path().replace('//', '/'))
                    set_all_buttons_enabled()
                    _add_to_image_history(self.right_disk_image_path)
                    return True
                else:
                    if hdfmonkeyexecresult is not None:
                        logging.error(f"Failed loading image :{self.right_disk_image_path} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                        add_main_log_window(f"Failed loading image :{self.right_disk_image_path} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                    else:
                        logging.error(f"Failed loading image :{self.right_disk_image_path}.")
                        add_main_log_window(f"Failed loading image :{self.right_disk_image_path}.")

            set_all_buttons_disabled()
            enable_image_selection()
            _update_image_usage_gauge("")

            return False

        def apply_file_extension_filter():
            text = self.filtertext.text().strip()
            self.proxy_model.setFilterFixedString(text)
            set_treeview_properties()
            self.treeview.show()

        def apply_file_extension_filter_nextsync():
            text = self.nextsync_filtertext.text().strip()
            self.nextsync_model.setFilterFixedString(text)
            set_treeview_properties()
            self.nextsync_treeview.show()

        def apply_image_filter():
            text = self.image_filtertext.text().strip().lower()
            for row in range(self.TableWidgetImage.rowCount()):
                match = False
                for col in range(self.TableWidgetImage.columnCount()):
                    item = self.TableWidgetImage.item(row, col)
                    if item and text in item.text().lower():
                        match = True
                        break
                self.TableWidgetImage.setRowHidden(row, not match if text else False)

        def add_main_log_window(string_to_log:str):
            newItem = QListWidgetItem()
            newItem.setText(string_to_log)
            self.listWidgetLog.insertItem(0, newItem)

        def add_nextsync_log_window(string_to_log:str, from_top:bool = True):

            newItem = QListWidgetItem()
            newItem.setText(string_to_log)
            if from_top:
                self.nextsync_log.insertItem(0, newItem)
            else:
                self.nextsync_log.insertItem(self.nextsync_log.count(), newItem)

        def add_help_content(string_to_log:str, from_top:bool = True):

            newItem = QListWidgetItem()
            newItem.setText(string_to_log)
            if from_top:
                self.listWidgetHelp.insertItem(0, newItem)
            else:
                self.listWidgetHelp.insertItem(self.listWidgetHelp.count(), newItem)

        def set_table_image_properties():
            self.TableWidgetImage.setHorizontalHeaderLabels(["Name", "Type", "Size"])
            self.TableWidgetImage.setSortingEnabled(True)
            self.TableWidgetImage.horizontalHeader().setSortIndicatorShown(True)
            self.TableWidgetImage.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

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
                self.button_delete_files.setVisible(True)
                self.new_folder_input.setVisible(False)
                self.button_create_directory.setVisible(False)
                self.button_create_directory_cancel.setVisible(False)
            else:
                logging.info("Please load an image file first !")
                add_main_log_window("Please load an image file first !")

            save_configuration_file()

        def image_newfolder_create():

            directory_to_create = self.new_folder_input.text()

            if directory_to_create.strip() == "":
                    logging.warning(f"Please enter a directory name!")
                    add_main_log_window(f"Please enter a directory name!")
                    return

            for not_allowed_chars in DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS:
                if not_allowed_chars in directory_to_create:
                    nachars = ""
                    for n in DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS:
                        nachars += n

                    logging.warning(f"Do not use any of the forbiden characters :{nachars} when creating directories!")
                    add_main_log_window(f"Do not use any of the forbiden characters :{nachars} when creating directories!")
                    return

            directory_to_create = generate_disk_file_path() + "/" + directory_to_create
            directory_to_create = directory_to_create.replace("//", "/")

            self.button_new_folder.setVisible(True)
            self.button_delete_files.setVisible(True)
            self.new_folder_input.setVisible(False)
            self.button_create_directory.setVisible(False)
            self.button_create_directory_cancel.setVisible(False)

            hdfmonkeyexecresult = execute_hdf_monkey("mkdir", self.right_disk_image_path, extra_argv=[directory_to_create])

            if hdfmonkeyexecresult.returncode != 0:
                logging.error(f"Failed creating directory - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                add_main_log_window(f"Failed creating directory - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")

            hdfmonkeyexecresult = execute_hdf_monkey("ls", self.right_disk_image_path, extra_argv=[generate_disk_file_path()])

            if hdfmonkeyexecresult.returncode != 0:
                logging.error(f"Failed browsing directory after creating it - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                add_main_log_window(f"Failed browsing directory after creating it - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")

            command_execution = hdfmonkeyexecresult.stdout
            update_disk_manager_widget_table(command_execution)

        def select_image():

            global right_disk_image_explorer_path
            global right_disk_image_explorer_content
            global right_disk_image_path
            global right_disk_image_selected_files

            dialog = QFileDialog(self) # https://doc.qt.io/qtforpython-6.2/PySide6/QtWidgets/QFileDialog.html
            dialog.setFileMode(QFileDialog.AnyFile)
            dialog.setViewMode(QFileDialog.Detail)
            fileName = QFileDialog.getOpenFileName(self,"Open File","/home/", "Images (*.img *.hdf)" )
            self.imageinput.setCurrentText('"' + str(fileName[0]) + '"')
            configuration_dictionary[SETTING_HDDFILE] = self.imageinput.currentText()

            right_disk_image_explorer_path = []
            right_disk_image_explorer_content = []
            right_disk_image_path = ""
            right_disk_image_selected_files = []
            self.TableWidgetImage.clear()
            self.TableWidgetImage.setRowCount(0)

            set_table_image_properties()

            # Now try to load it
            if load_image():
                save_configuration_file()
                if self.settings_warn_image_nearly_full_checkbox.isChecked():
                    _warn_if_image_nearly_full(self.right_disk_image_path)

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
                self.imageinput.setCurrentText('"' + str(image_to_load) + '"')
                configuration_dictionary[SETTING_HDDFILE] = self.imageinput.currentText()

                right_disk_image_explorer_path = []
                right_disk_image_explorer_content = []
                right_disk_image_path = ""
                right_disk_image_selected_files = []
                self.TableWidgetImage.clear()
                self.TableWidgetImage.setRowCount(0)

                set_table_image_properties()

                # Now try to load it
                if load_image():
                    save_configuration_file()
                    if self.settings_warn_image_nearly_full_checkbox.isChecked():
                        _warn_if_image_nearly_full(self.right_disk_image_path)

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

        def execute_hdf_monkey(command_to_execute, image_path, additional_args="", silent=False, extra_argv=None):
            # Sentinel with a non-zero returncode in case we never reach subprocess.run
            exec_process = subprocess.CompletedProcess(args=[], returncode=-1)
            execution_cmd = f'{HDFMONKEY_EXECUTABLE} {command_to_execute} {image_path} {additional_args}'
            try:
                img = image_path.strip('"')
                argv = [HDFMONKEY_EXECUTABLE, command_to_execute, img]
                if extra_argv is not None:
                    # Caller passes a clean list of path strings – no quoting/parsing needed
                    argv += extra_argv
                elif additional_args:
                    argv += shlex.split(additional_args, posix=True)
                exec_process = subprocess.run(argv, shell=False, check=True,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            except (FileNotFoundError,subprocess.CalledProcessError) as ex:
                    if not isinstance(ex, FileNotFoundError):
                        stderr_text = (ex.stderr or b"").decode(errors="replace").strip()
                        exec_process = subprocess.CompletedProcess(args=ex.cmd, returncode=ex.returncode,
                                                                   stdout=ex.stdout, stderr=ex.stderr)
                    if silent:
                        logging.debug(f"hdfmonkey {command_to_execute} returned {ex.returncode} (silent): {execution_cmd}"
                                      + (f" | stderr: {stderr_text}" if stderr_text else ""))
                    elif isinstance(ex, FileNotFoundError) or ex.returncode == 1:
                        logging.error(f"Failed executing hdfmonkey: {execution_cmd} - Once hdfmonkey is installed in the same directory please close the application and restart it.")
                        add_main_log_window("ERROR: Once hdfmonkey is installed in the same directory please close the application and restart it.")
                        if platform.system() == "Windows":
                            logging.error(f"ERROR: hdfmonkey is required and likely not present in local directory, please install a pre-compiled version from https://uto.speccy.org/downloads/hdfmonkey_windows.zip or compile it from https://github.com/gasman/hdfmonkey.")
                            add_main_log_window("ERROR: hdfmonkey is required and likely not present in local directory, please install a pre-compiled version from https://uto.speccy.org/downloads/hdfmonkey_windows.zip or compile it from https://github.com/gasman/hdfmonkey.")
                        else:
                            logging.error(f"ERROR: hdfmonkey execution failed: {ex}, please make sure it is installed from https://github.com/gasman/hdfmonkey and working properly.")
                            add_main_log_window(f"ERROR: hdfmonkey execution failed: {ex}, please make sure it is installed from https://github.com/gasman/hdfmonkey and working properly.")
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

        def execute_shell_command(command_to_execute, additional_args = ""):
            execution_cmd = command_to_execute + " " + additional_args
            return subprocess.run(execution_cmd, shell=True, check=True, stdout=subprocess.PIPE)

        def execute_shell_command_no_wait(command_to_execute, additional_args = ""):
            execution_cmd = command_to_execute + " " + additional_args
            return subprocess.run(execution_cmd, shell=False, stdin=None, stdout=None, stderr=None,close_fds=True, start_new_session=True, capture_output=False, timeout=None)

        def update_root_drive():
            self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(self.zx_next_unite_diskdrive.itemText(0))))
            set_treeview_properties()
            self.treeview.show()

        def nextsync_update_root_drive():
            self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(self.nextsync_diskdrive.itemText(0))))
            self.nextsync_treeview.show()

        # ---------------------------------------------------------------
        # Scan helpers: walk an image directory tree and return flat lists
        # of (image_path_in_image, local_disk_path) pairs or just names,
        # emitting live status/progress so the UI stays responsive.
        # ---------------------------------------------------------------

        def _scan_image_tree_for_get(image_path, image_source, disk_dest, cancel_event,
                                     signals, out_files, out_dirs):
            """Recursively enumerate all files and dirs under image_source.
            Appends (img_src, disk_dst) tuples to out_files and out_dirs.
            Emits status with each discovered name so the user sees live names."""
            hdfr = execute_hdf_monkey("ls", image_path, extra_argv=[image_source])
            if hdfr.returncode != 0:
                return
            for line in hdfr.stdout.splitlines():
                if cancel_event.is_set():
                    return
                decoded = line.decode(errors="replace") if isinstance(line, bytes) else line
                parts = decoded.split('\t', 1)
                if len(parts) < 2:
                    continue
                ftype = parts[0]
                fname = parts[1]
                img_path = (image_source + "/" + fname).replace("//", "/")
                if platform.system() == "Windows":
                    disk_path = disk_dest + "\\" + fname
                else:
                    disk_path = disk_dest + "/" + fname
                signals.status.emit(f"Scanning\u2026\n{img_path}")
                if is_filetype_a_directory(ftype):
                    out_dirs.append((img_path, disk_path))
                    _scan_image_tree_for_get(image_path, img_path, disk_path, cancel_event,
                                             signals, out_files, out_dirs)
                else:
                    out_files.append((img_path, disk_path))

        def _scan_image_tree_for_delete(image_path, destination, cancel_event,
                                        signals, out_files, out_dirs):
            """Recursively enumerate all files and dirs under destination.
            Appends item path strings to out_files (deepest first) and out_dirs."""
            hdfr = execute_hdf_monkey("ls", image_path, extra_argv=[destination])
            if hdfr.returncode != 0:
                return
            for line in hdfr.stdout.splitlines():
                if cancel_event.is_set():
                    return
                decoded = line.decode(errors="replace") if isinstance(line, bytes) else line
                parts = decoded.split('\t', 1)
                if len(parts) < 2:
                    continue
                ftype = parts[0]
                fname = parts[1]
                full  = (destination + "/" + fname).replace("//", "/")
                signals.status.emit(f"Scanning\u2026\n{full}")
                if is_filetype_a_directory(ftype):
                    _scan_image_tree_for_delete(image_path, full, cancel_event,
                                                signals, out_files, out_dirs)
                    out_dirs.append(full)   # directory itself deleted after its contents
                else:
                    out_files.append(full)

        # recursively delete all files in sub directories
        def delete_sub_directory_content(image_path, destination):

            # list and delete all files in that directory
            hdfmonkeyexecresult = execute_hdf_monkey("ls", image_path, extra_argv=[destination])
            if hdfmonkeyexecresult.returncode == 0:
                command_execution = hdfmonkeyexecresult.stdout

                results_lines = command_execution.splitlines()

                if command_execution:

                    for files in results_lines:
                        decoded_files = files.decode(errors="replace") if isinstance(files, bytes) else files
                        directory_result_table = decoded_files.split('\t', 1)
                        if len(directory_result_table) < 2:
                            continue
                        file_type = directory_result_table[0]
                        file_name = directory_result_table[1]

                        if not is_filetype_a_directory(file_type):
                            hdfmonkeyexecresult = execute_hdf_monkey("rm", self.right_disk_image_path,
                                                                     extra_argv=[destination + "/" + file_name])
                            if hdfmonkeyexecresult.returncode != 0:
                                logging.error(f"Failed deleting file: {self.right_disk_image_path}{destination}/{file_name} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                                add_main_log_window(f"Failed deleting file: {self.right_disk_image_path}{destination}/{file_name} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")

                        else:
                            delete_sub_directory_content(image_path, destination + "/" + file_name)
                            # delete the directory in then end
                            hdfmonkeyexecresult = execute_hdf_monkey("rm", self.right_disk_image_path,
                                                                         extra_argv=[destination + "/" + file_name])
                            if hdfmonkeyexecresult.returncode != 0:
                                logging.error(f"Failed deleting file: {self.right_disk_image_path}{destination}/{file_name} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                                add_main_log_window(f"Failed deleting file: {self.right_disk_image_path}{destination}/{file_name} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")

        # recursively get all files in sub directories from image and copy to disj
        def get_directory_content(image_path, image_source, disk_source, folder_name):

            image_source += "/" + folder_name

            image_source = image_source.replace("//", "/") # on root drive remove double slashes

            if platform.system() == "Windows":
                disk_source += "\\" + folder_name
            else:
                disk_source += "/" + folder_name
            image_source = image_source.replace('"', '')

            if is_directory(image_path, image_source):

                # list and get all files in that directory
                hdfmonkeyexecresult = execute_hdf_monkey("ls", image_path, extra_argv=[image_source])
                if hdfmonkeyexecresult.returncode == 0:
                    command_execution = hdfmonkeyexecresult.stdout

                    results_lines = command_execution.splitlines()

                    if command_execution:

                        for files in results_lines:

                            decoded_files = files.decode(errors="replace") if isinstance(files, bytes) else files
                            directory_result_table = decoded_files.split('\t', 1)
                            if len(directory_result_table) < 2:
                                continue
                            file_type = directory_result_table[0]
                            file_name = directory_result_table[1]

                            if platform.system() == "Windows":
                                disk_destination = disk_source.replace('\\', '/') + "/" + file_name
                            else:
                                disk_destination = disk_source + "/" + file_name

                            if not is_filetype_a_directory(file_type):

                                hdfmonkeyexecresult = execute_hdf_monkey("get", self.right_disk_image_path,
                                                                         extra_argv=[image_source + "/" + file_name,
                                                                                     disk_destination.replace('\\', '/')])
                                if hdfmonkeyexecresult.returncode != 0:
                                    logging.error(f"Failed getting file: {self.right_disk_image_path}{image_source}/{file_name} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
                                    add_main_log_window(f"Failed getting file: {self.right_disk_image_path}{image_source}/{file_name} - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")

                            else:

                                disk_destination = disk_destination.replace('"', '')
                                # create the directory

                                try:
                                    os.makedirs(disk_destination)
                                except FileExistsError:
                                    pass
                                except Exception as e:
                                    logging.error(f"Failed creating directory: {disk_destination} - Exception: {e}")
                                    add_main_log_window(f"Failed creating directory: {disk_destination} - Exception: {e}")

                                get_directory_content (image_path, image_source, disk_source,  file_name)

        #First returned value is the root parent directory full path second variable is the last path or filename
        def get_parent_root_directory_splited(file_name:str):

            token_path = file_name.split("/")

            result_path = ""
            row = 1
            for i in token_path:
                result_path += token_path[row - 1]
                row += 1
                if row == len(token_path):
                    break
                if len(token_path) != row:
                    result_path += "/"
            return result_path, token_path[row - 1]

        def is_directory(image_path, source):

            root_folder , file_name_from_source = get_parent_root_directory_splited (source)

            hdfmonkeyexecresult = execute_hdf_monkey("ls", image_path, extra_argv=[root_folder])

            if hdfmonkeyexecresult.returncode == 0:
                command_execution = hdfmonkeyexecresult.stdout

                results_lines = command_execution.splitlines()

                for line in results_lines:
                    decoded_line = line.decode(errors="replace") if isinstance(line, bytes) else line
                    directory_result_table = decoded_line.split('\t', 1)
                    if len(directory_result_table) < 2:
                        continue
                    file_type = directory_result_table[0]
                    file_name = directory_result_table[1]

                    if file_name == file_name_from_source:
                        if is_filetype_a_directory(file_type):
                            return True
                        else:
                            return False

            return False

        def _run_delete_task(signals, cancel_event, image_path, disk_path_fn, files_to_delete):
            """Background worker body for image_delete_files.
            Phase 1: scan/count all items recursively (indeterminate progress).
            Phase 2: delete each item with real percentage progress."""
            actual = [f for f in files_to_delete if f != UP_DIRECTORY]

            # ---- Phase 1: enumerate everything ----
            signals.progress.emit(-1)   # indeterminate
            all_files = []   # flat list of image paths to rm
            all_dirs  = []   # directories to rm after their content
            for f in actual:
                if cancel_event.is_set():
                    break
                full = (disk_path_fn() + "/" + f).replace("//", "/")
                signals.status.emit(f"Scanning\u2026\n{full}")
                if is_directory(image_path, full):
                    _scan_image_tree_for_delete(image_path, full, cancel_event,
                                                signals, all_files, all_dirs)
                    all_dirs.append(full)  # delete the top-level dir itself last
                else:
                    all_files.append(full)

            if cancel_event.is_set():
                return

            # ---- Phase 2: delete ----
            all_items = all_files + all_dirs   # files first, then dirs (deepest already ordered)
            total     = max(len(all_items), 1)
            for idx, item_path in enumerate(all_items):
                if cancel_event.is_set():
                    break
                signals.status.emit(f"Deleting ({idx + 1}/{total})\n{item_path}")
                signals.progress.emit(int(idx / total * 100))
                try:
                    execute_hdf_monkey("rm", image_path, extra_argv=[item_path])
                except Exception as e:
                    logging.error(f"Failed deleting: {item_path} - {e}")
                    signals.error.emit(f"Failed deleting: {item_path}\n{e}")
                signals.progress.emit(int((idx + 1) / total * 100))

        def image_delete_files():
            if not right_disk_image_explorer_content:
                logging.info("Please select an image file or folder first to delete!")
                add_main_log_window("Please select an image file or folder first to delete!")
                return

            img_err = _check_image_writable(self.right_disk_image_path, check_free_space=False)
            if img_err:
                logging.error(img_err)
                add_main_log_window(f"ERROR: {img_err}")
                QMessageBox.critical(self, "Image not writable", img_err)
                return

            files_snapshot = list(right_disk_image_selected_files)
            image_path     = self.right_disk_image_path
            disk_path_fn   = generate_disk_file_path

            set_all_buttons_disabled()

            dlg    = HdfProgressDialog("Deleting files\u2026", self)
            worker = HdfTaskWorker(_run_delete_task, image_path, disk_path_fn, files_snapshot)

            dlg.cancel_requested.connect(worker.cancel)
            worker.signals.progress.connect(dlg.set_progress)
            worker.signals.status.connect(dlg.set_status)
            worker.signals.error.connect(add_main_log_window)
            worker.signals.cancelled.connect(dlg.mark_cancelled)

            def _on_delete_finished():
                dlg.close()
                result = execute_hdf_monkey("ls", image_path, extra_argv=[generate_disk_file_path()])
                if result.returncode == 0:
                    update_disk_manager_widget_table(result.stdout)
                else:
                    logging.error(f"Failed browsing directory after deleting files - hdfmonkey result code: {result.returncode}")
                    add_main_log_window(f"Failed browsing directory after deleting files - hdfmonkey result code: {result.returncode}")
                set_all_buttons_enabled()

            worker.signals.finished.connect(_on_delete_finished)
            self.threadpool.start(worker)
            dlg.exec()


        def nextsync_perform_checks_and_prepare_server_start():
            nextsync_warnings()
            save_configuration_file()


        def nextsync_start_server(serve_folder=None):
            # Guard: don't start a second sync while one is already running
            t = getattr(self, "_nextsync_thread", None)
            if t is not None and t.is_alive():
                add_nextsync_log_window("NextSync is already running — please wait for it to finish.")
                return
            try:
                # --- progress dialog ---
                dlg = HdfProgressDialog("NextSync — sending to ZX Spectrum Next", parent=self)
                dlg.set_status("Waiting for ZX Next to connect…\nRun .sync (or .syncfast) on your Next")
                dlg.set_progress(-1)   # indeterminate spinner until first file

                sig = NextSyncSignals()
                cancel_flag = threading.Event()

                sig.progress.connect(dlg.set_progress)
                sig.status.connect(dlg.set_status)
                sig.finished.connect(lambda: QTimer.singleShot(800, dlg.accept))
                sig.cancelled.connect(dlg.mark_cancelled)
                dlg.cancel_requested.connect(lambda: cancel_flag.set())

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
                        nextsync_server_exception_occured(ex)
                    finally:
                        if cancel_flag.is_set():
                            sig.cancelled.emit()
                        sig.finished.emit()

                t = threading.Thread(target=_run, daemon=True)
                self._nextsync_thread = t
                nextsync_hide_start_cancel_buttons()
                t.start()
                dlg.exec()   # blocks main thread showing the modal dialog
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
        def on_treeview_clicked():

            for ix in self.treeview.selectedIndexes():

                source_ix = self.proxy_model.mapToSource(ix)

                if self.model.fileName(source_ix) == "..":
                    # Don't navigate on single-click; navigation happens on double-click.
                    # Just clear the current selection so no stale file path is carried.
                    self.left_file_explorer_selection_file_name = ""
                    self.left_file_explorer_selection_full_filename_path = ""
                    break

                else:

                    self.left_file_explorer_selection_file_name = self.model.fileName(source_ix)
                    self.left_file_explorer_selection_full_filename_path = self.model.filePath(source_ix)
                    if platform.system() != "Windows":
                        self.left_file_explorer_selection_full_filename_path.replace("\\", '/')

                    self.file_explorer_path.setText(self.left_file_explorer_selection_full_filename_path)
                    configuration_dictionary[SETTING_EXPLORERPATH] = self.left_file_explorer_selection_full_filename_path
                    save_configuration_file()

                    break

        def on_treeview_double_clicked(ix):
            # ix is the proxy index passed directly by the doubleClicked signal
            if not ix.isValid():
                return

            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)

            source_ix = self.proxy_model.mapToSource(ix)
            file_name = self.model.fileName(source_ix)
            file_path = self.model.filePath(source_ix)

            if file_name == "..":
                # Navigate one level up using the current root path as the reference
                current_root_source = self.proxy_model.mapToSource(self.treeview.rootIndex())
                current_root_path = self.model.filePath(current_root_source)
                parent_path = os.path.dirname(current_root_path.rstrip("/\\"))
                if not parent_path:
                    return
                selected_explorer_item_directory_destination = parent_path.replace("\\", "/") + "/"

            elif self.model.isDir(source_ix):
                # Navigate into the selected directory
                selected_explorer_item_directory_destination = file_path
                if not selected_explorer_item_directory_destination.endswith("/"):
                    selected_explorer_item_directory_destination += "/"

            else:
                return

            self.left_file_explorer_selection_file_name = ""
            self.left_file_explorer_selection_full_filename_path = selected_explorer_item_directory_destination

            self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(selected_explorer_item_directory_destination, 0)))
            set_treeview_properties()
            self.treeview.show()

            self.file_explorer_path.setText(selected_explorer_item_directory_destination)

            configuration_dictionary[SETTING_EXPLORERPATH] = selected_explorer_item_directory_destination
            save_configuration_file()

        def on_treeview_context_menu(pos):
            index = self.treeview.indexAt(pos)
            if not index.isValid():
                return
            source_index = self.proxy_model.mapToSource(index)
            name = self.model.fileName(source_index)
            if name == "..":
                return
            file_path = self.model.filePath(source_index)
            menu = QMenu(self.treeview)
            action_copy_text = QAction("Copy text to clipboard", self.treeview)
            action_copy_path = QAction("Copy path to clipboard", self.treeview)
            action_copy_text.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
            action_copy_path.triggered.connect(lambda: QGuiApplication.clipboard().setText(file_path))
            menu.addAction(action_copy_text)
            menu.addAction(action_copy_path)
            menu.exec(self.treeview.viewport().mapToGlobal(pos))

        def nextsync_on_treeview_context_menu(pos):
            index = self.nextsync_treeview.indexAt(pos)
            if not index.isValid():
                return
            source_index = self.nextsync_model.mapToSource(index)
            name = self.nextsync_filesystem_model.fileName(source_index)
            if name == "..":
                return
            file_path = self.nextsync_filesystem_model.filePath(source_index)
            menu = QMenu(self.nextsync_treeview)
            action_copy_text = QAction("Copy text to clipboard", self.nextsync_treeview)
            action_copy_path = QAction("Copy path to clipboard", self.nextsync_treeview)
            action_copy_text.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
            action_copy_path.triggered.connect(lambda: QGuiApplication.clipboard().setText(file_path))
            menu.addAction(action_copy_text)
            menu.addAction(action_copy_path)
            menu.exec(self.nextsync_treeview.viewport().mapToGlobal(pos))

        def on_file_explorer_path_edited():
            new_path = self.file_explorer_path.text().strip()
            if os.path.exists(new_path):
                norm = new_path.replace("\\", "/")
                if not norm.endswith("/"):
                    norm += "/"
                self.left_file_explorer_selection_full_filename_path = norm
                self.left_file_explorer_selection_file_name = ""
                self.file_explorer_path.setText(norm)
                self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(norm, 0)))
                set_treeview_properties()
                self.treeview.show()
                configuration_dictionary[SETTING_EXPLORERPATH] = norm
                save_configuration_file()
            else:
                # Restore the previous valid value
                self.file_explorer_path.setText(self.left_file_explorer_selection_full_filename_path)

        def on_nextsync_file_explorer_path_edited():
            new_path = self.nextsync_file_explorer_path.text().strip()
            if os.path.exists(new_path):
                norm = new_path.replace("\\", "/")
                if not norm.endswith("/"):
                    norm += "/"
                self.left_file_nextsync_explorer_selection_full_filename_path = norm
                self.left_file_nextsync_explorer_selection_file_name = ""
                self.nextsync_file_explorer_path.setText(norm)
                self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(norm, 0)))
                set_treeview_properties()
                self.nextsync_treeview.show()
                configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = norm
                save_configuration_file()
                nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
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


        def nextsync_synconce_checkbox_statechanged():
            if self.nextsync_synconce_checkbox.isChecked():
                configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] = "true"
            else:
                configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] = "false"

            save_configuration_file()

        def nextsync_alwayssync_checkbox_statechanged():
            if self.nextsync_alwayssync_checkbox.isChecked():
                configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] = "true"
            else:
                configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] = "false"

            save_configuration_file()

        def nextsync_slowtransfer_checkbox_statechanged():
            if self.nextsync_slowtransfer_checkbox.isChecked():
                configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] = "true"
                MAX_PAYLOAD = 256
            else:
                configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] = "false"
                MAX_PAYLOAD = 1024

            save_configuration_file()

        def nextsync_on_treeview_clicked():

            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)

            for ix in self.nextsync_treeview.selectedIndexes():
                source_ix = self.nextsync_model.mapToSource(ix)

                if self.nextsync_filesystem_model.fileName(source_ix) == "..":
                    # Don't navigate on single-click; navigation happens on double-click.
                    self.left_file_nextsync_explorer_selection_file_name = ""
                    self.left_file_nextsync_explorer_selection_full_filename_path = ""
                    break

                else:

                    self.left_file_nextsync_explorer_selection_file_name = self.nextsync_filesystem_model.fileName(source_ix)
                    self.left_file_nextsync_explorer_selection_full_filename_path = self.nextsync_filesystem_model.filePath(source_ix)
                    if platform.system() != "Windows":
                        self.left_file_nextsync_explorer_selection_full_filename_path = self.left_file_nextsync_explorer_selection_full_filename_path.replace("\\", '/')

                    self.nextsync_file_explorer_path.setText(self.left_file_nextsync_explorer_selection_full_filename_path)
                    configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = self.left_file_nextsync_explorer_selection_full_filename_path
                    save_configuration_file()

                    nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
                    break

        def nextsync_on_treeview_double_clicked(ix):
            if not ix.isValid():
                return

            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)

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

            self.left_file_nextsync_explorer_selection_file_name = ""
            self.left_file_nextsync_explorer_selection_full_filename_path = selected_explorer_item_directory_destination

            self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(selected_explorer_item_directory_destination, 0)))
            set_treeview_properties()
            self.nextsync_treeview.show()

            self.nextsync_file_explorer_path.setText(selected_explorer_item_directory_destination)

            configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = selected_explorer_item_directory_destination
            save_configuration_file()

            nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()

        def image_explorer_selection_changed():

            global right_disk_image_explorer_content

            if right_disk_image_explorer_content:  # check that we have an image content first
                right_disk_image_selected_files.clear()
                for idx in self.TableWidgetImage.selectionModel().selectedIndexes():
                    row_number = idx.row()
                    column_number = idx.column()
                    name_item = self.TableWidgetImage.item(row_number, 0)
                    if name_item:
                        right_disk_image_selected_files.append(name_item.text())

        def _run_get_task(signals, cancel_event, image_path, disk_path_fn, files_to_get,
                          dest_dir, dir_nav, is_windows):
            """Background worker body for transfert_content_from_image_to_disk.
            Phase 1: scan/count all items recursively (indeterminate progress).
            Phase 2: copy each file with real percentage progress."""

            # ---- Phase 1: enumerate everything ----
            signals.progress.emit(-1)   # indeterminate marquee
            all_files = []   # list of (img_src_path, local_disk_path)
            all_dirs  = []   # list of (img_src_path, local_disk_path)  – dirs to create

            for f in files_to_get:
                if cancel_event.is_set():
                    break
                source = (disk_path_fn() + "/" + f).replace("//", "/")
                signals.status.emit(f"Scanning\u2026\n{source}")
                if not is_directory(image_path, source):
                    local = dest_dir + dir_nav + f
                    all_files.append((source, local))
                else:
                    local_dir = os.path.join(dest_dir, f) if is_windows else dest_dir + "/" + f
                    all_dirs.append((source, local_dir))
                    _scan_image_tree_for_get(image_path, source, local_dir, cancel_event,
                                             signals, all_files, all_dirs)

            if cancel_event.is_set():
                return

            # ---- Phase 2: create directories then copy files ----
            # Create all discovered directories first
            for _, local_dir in all_dirs:
                try:
                    os.makedirs(local_dir, exist_ok=True)
                except Exception as e:
                    logging.error(f"Failed creating directory: {local_dir} - {e}")
                    signals.error.emit(f"Failed creating directory: {local_dir}\n{e}")

            total = max(len(all_files), 1)
            for idx, (img_src, local_dst) in enumerate(all_files):
                if cancel_event.is_set():
                    break
                signals.status.emit(f"Downloading ({idx + 1}/{total})\n{img_src}")
                signals.progress.emit(int(idx / total * 100))
                try:
                    execute_hdf_monkey("get", image_path,
                                       extra_argv=[img_src, local_dst.replace('\\', '/')])
                except Exception as e:
                    logging.error(f"Failed downloading: {img_src} - {e}")
                    signals.error.emit(f"Failed downloading: {img_src}\n{e}")
                signals.progress.emit(int((idx + 1) / total * 100))

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

            if not right_disk_image_selected_files:
                set_all_buttons_enabled()
                return

            files_snapshot = list(right_disk_image_selected_files)
            image_path     = self.right_disk_image_path
            disk_path_fn   = generate_disk_file_path

            dlg    = HdfProgressDialog("Downloading from image\u2026", self)
            worker = HdfTaskWorker(_run_get_task, image_path, disk_path_fn,
                                   files_snapshot,
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

        def _run_put_task(signals, cancel_event, image_path, upload_path, dest_file_path):
            """Background worker body for transfert_content_from_disk_to_image.
            For a single file: simple upload with status.
            For a directory: Phase 1 scans the local tree, Phase 2 uploads each file."""

            if not os.path.isdir(upload_path):
                # ---- Single file ----
                signals.status.emit(f"Uploading to image\n{os.path.basename(upload_path)}")
                signals.progress.emit(0)
                if not cancel_event.is_set():
                    result = execute_hdf_monkey("put", image_path, extra_argv=[upload_path.replace('\\', '/'), dest_file_path])
                    if result.returncode != 0:
                        stdout_text = (result.stdout or b"").decode(errors="replace").strip()
                        if "Access denied" in stdout_text:
                            full_err = _check_access_denied_is_full_disk(image_path)
                            if full_err:
                                logging.error(full_err)
                                signals.error.emit(full_err)
                                cancel_event.set()
                                return
                        logging.error(f"Failed uploading to image: {image_path} file: {upload_path} {dest_file_path}")
                        signals.error.emit(f"Failed uploading: {os.path.basename(upload_path)}")
                signals.progress.emit(100)
                return

            # ---- Directory: Phase 1 enumerate local tree ----
            signals.progress.emit(-1)   # indeterminate
            all_files = []   # list of (local_path, image_dest_path)
            all_img_dirs = []  # image-side directories to create, parents before children

            def _scan_local_dir(local_dir, img_dir):
                try:
                    entries = os.listdir(local_dir)
                except Exception as e:
                    logging.error(f"Cannot list directory {local_dir}: {e}")
                    return
                for name in entries:
                    if cancel_event.is_set():
                        return
                    local_path = os.path.join(local_dir, name)
                    img_path   = (img_dir + "/" + name).replace("//", "/")
                    signals.status.emit(f"Scanning\u2026\n{local_path}")
                    if os.path.isdir(local_path):
                        all_img_dirs.append(img_path)   # must mkdir before uploading into it
                        _scan_local_dir(local_path, img_path)
                    else:
                        all_files.append((local_path, img_path))

            # The top-level dest_file_path directory must also exist in the image
            all_img_dirs.insert(0, dest_file_path)
            _scan_local_dir(upload_path, dest_file_path)

            if cancel_event.is_set():
                return

            # ---- Phase 1b: create all image-side directories (mkdir -p style) ----
            # hdfmonkey mkdir cannot create intermediate paths, so we must ensure
            # every ancestor segment exists before creating a child directory.
            _img_dirs_created = set()

            def _image_makedirs(img_dir_path):
                """Create img_dir_path and all its ancestors inside the image.
                Returns False and sets cancel_event if a full-disk condition is detected."""
                parts = img_dir_path.strip("/").split("/")
                for i in range(1, len(parts) + 1):
                    if cancel_event.is_set():
                        return False
                    seg = "/" + "/".join(parts[:i])
                    if seg in _img_dirs_created:
                        continue
                    signals.status.emit(f"Creating directory\n{seg}")
                    result = execute_hdf_monkey("mkdir", image_path, extra_argv=[seg], silent=True)
                    mkdir_stdout = (result.stdout or b"").decode(errors="replace").strip()
                    if result.returncode == 0:
                        _img_dirs_created.add(seg)
                    else:
                        if "Access denied" in mkdir_stdout:
                            full_err = _check_access_denied_is_full_disk(image_path)
                            if full_err:
                                logging.error(full_err)
                                signals.error.emit(full_err)
                                cancel_event.set()
                                return False
                        # Non-zero may mean already exists — verify with ls
                        ls_result = execute_hdf_monkey("ls", image_path, extra_argv=[seg], silent=True)
                        ls_stdout = (ls_result.stdout or b"").decode(errors="replace").strip()
                        if ls_result.returncode == 0:
                            _img_dirs_created.add(seg)   # exists already — fine
                        else:
                            logging.warning(f"mkdir failed and directory not found: {seg} (rc={result.returncode})"
                                            + (f" | mkdir stdout: {mkdir_stdout}" if mkdir_stdout else "")
                                            + (f" | ls stdout: {ls_stdout}" if ls_stdout else ""))
                return True

            for img_dir in all_img_dirs:
                if cancel_event.is_set():
                    break
                if not _image_makedirs(img_dir):
                    break

            if cancel_event.is_set():
                return

            # ---- Phase 2: upload each file ----
            total = max(len(all_files), 1)
            for idx, (local_path, img_dst) in enumerate(all_files):
                if cancel_event.is_set():
                    break
                signals.status.emit(f"Uploading ({idx + 1}/{total})\n{local_path}")
                signals.progress.emit(int(idx / total * 100))
                result = execute_hdf_monkey("put", image_path, extra_argv=[local_path.replace('\\', '/'), img_dst])
                if result.returncode != 0:
                    stdout_text = (result.stdout or b"").decode(errors="replace").strip()
                    if "Access denied" in stdout_text:
                        full_err = _check_access_denied_is_full_disk(image_path)
                        if full_err:
                            logging.error(full_err)
                            signals.error.emit(full_err)
                            cancel_event.set()
                            break
                    logging.error(f"Failed uploading: {local_path} -> {img_dst} | stdout: {stdout_text}")
                    signals.error.emit(f"Failed uploading: {os.path.basename(local_path)}")
                signals.progress.emit(int((idx + 1) / total * 100))

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
            worker = HdfTaskWorker(_run_put_task, image_path, upload_path, dest_file_path)

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
                self.file_explorer_path.setText(display_path)
                result = execute_hdf_monkey("ls", image_path, extra_argv=[disk_path_fn()])
                if result.returncode == 0:
                    update_disk_manager_widget_table(result.stdout)
                else:
                    logging.error(f"Failed browsing directory after uploading file - hdfmonkey result code: {result.returncode}")
                    add_main_log_window(f"Failed browsing directory after uploading file - hdfmonkey result code: {result.returncode}")
                set_all_buttons_enabled()

            worker.signals.finished.connect(_on_put_finished)
            self.threadpool.start(worker)
            dlg.exec()


        def generate_disk_file_path():
            result_path = "/"
            row = 1
            for i in right_disk_image_explorer_path:
                result_path += right_disk_image_explorer_path[row - 1]
                if len(right_disk_image_explorer_path) != row:
                    result_path += "/"
                row += 1
            return result_path

        def disk_image_explorer_item_double_clicked():

            global right_disk_image_explorer_content

            if right_disk_image_explorer_content:  # check that we have an image content first
                set_all_buttons_disabled()

                # Reset all buttons such as Create directory or Delete files if the user suddenly tries to navigate instead
                if self.button_confirm_deletion.isVisible() or self.button_create_directory.isVisible():
                    button_cancel_deletion()
                    image_newfolder_cancel()


                row_number = 0
                column_number = 0
                for idx in self.TableWidgetImage.selectionModel().selectedIndexes():
                    row_number = idx.row()
                    column_number = idx.column()

                # If user picked to go one directory level up
                name_item = self.TableWidgetImage.item(row_number, 0)
                type_item = self.TableWidgetImage.item(row_number, 1)
                row_name = name_item.text() if name_item else ""
                row_type = type_item.text() if type_item else ""

                if row_number == 0 and row_name == UP_DIRECTORY and row_type == "":
                    self.image_filtertext.clear()
                    self.TableWidgetImage.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                    right_disk_image_explorer_path.pop()
                    hdfmonkeyexecresult = execute_hdf_monkey("ls", self.right_disk_image_path, extra_argv=[generate_disk_file_path()])

                    if hdfmonkeyexecresult.returncode == 0:
                        command_execution = hdfmonkeyexecresult.stdout
                        self.diskimageexplorerlabelpath.setText(generate_disk_file_path().replace('//', '/'))
                        update_disk_manager_widget_table(command_execution)
                        set_all_buttons_enabled()
                        return

                if row_type == 'DIR':
                    self.image_filtertext.clear()
                    self.TableWidgetImage.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                    right_disk_image_explorer_path.append(row_name)
                    hdfmonkeyexecresult = execute_hdf_monkey("ls", self.right_disk_image_path, extra_argv=[generate_disk_file_path()])

                    if hdfmonkeyexecresult.returncode == 0:
                        command_execution = hdfmonkeyexecresult.stdout
                        update_disk_manager_widget_table(command_execution)
                        self.diskimageexplorerlabelpath.setText(generate_disk_file_path().replace('//', '/'))

                set_all_buttons_enabled()

            else:
                logging.warning("Please load an image file first !")
                add_main_log_window("Please load an image file first !")

        def update_disk_manager_widget_table(command_execution_content):

            global right_disk_image_explorer_content

            results_lines = command_execution_content.splitlines()

            self.TableWidgetImage.clear()
            set_table_image_properties()

            self.TableWidgetImage.setRowCount(0)
            self.TableWidgetImage.setRowCount(len(results_lines)+1)
            self.TableWidgetImage.verticalHeader().setVisible(False)

            row = 0

            right_disk_image_explorer_content.clear()

            # If we are not at the root add "[Up Directory..]" in order that the user can go back up
            if right_disk_image_explorer_path:

                newItemUpDirectory = QTableWidgetItem(UP_DIRECTORY)
                newItemUpDirectory.setForeground(self.img_color_up_directory)
                newItemEmpty1 = QTableWidgetItem("")
                newItemEmpty2 = QTableWidgetItem("")
                newItemUpDirectory.setFlags(newItemUpDirectory.flags() & ~Qt.ItemIsEditable) # make non editable
                newItemEmpty1.setFlags(newItemEmpty1.flags() & ~Qt.ItemIsEditable) # make non editable
                newItemEmpty1.setFlags(~Qt.ItemIsEnabled) # make non editable
                newItemEmpty2.setFlags(newItemEmpty2.flags() & ~Qt.ItemIsEditable) # make non editable
                newItemEmpty2.setFlags(~Qt.ItemIsEnabled)
                self.TableWidgetImage.setItem(row, 0, newItemUpDirectory)
                self.TableWidgetImage.setItem(row, 1, newItemEmpty1)
                self.TableWidgetImage.setItem(row, 2, newItemEmpty2)


                right_disk_image_explorer_content.append((UP_DIRECTORY, ""))
                row += 1


            self.image_explorer_item_list.clear()

            for dirvalues in results_lines:
                decoded_line = dirvalues.decode(errors="replace") if isinstance(dirvalues, bytes) else dirvalues
                directory_result_table = decoded_line.split('\t', 1)
                if len(directory_result_table) < 2:
                    continue
                file_type = directory_result_table[0]
                file_name = directory_result_table[1]

                newItemName = QTableWidgetItem(str(file_name))

                if is_filetype_a_directory(file_type):
                    file_type = "DIR"
                    newItemFSName = QTableWidgetItem(str(file_type))
                    newItemEmptyDir = QTableWidgetItem("")

                    newItemFSName.setFlags(newItemFSName.flags() & ~Qt.ItemIsEditable) # make non editable
                    newItemName.setForeground(self.img_color_dir_name)
                    newItemName.setFlags(newItemName.flags() & ~Qt.ItemIsEditable) # make non editable
                    newItemFSName.setForeground(self.img_color_dir_type)
                    newItemEmptyDir.setFlags(newItemEmptyDir.flags() & ~Qt.ItemIsEditable) # make non editable

                    newItemFSName.setFlags(~Qt.ItemIsEnabled)
                    newItemEmptyDir.setFlags(~Qt.ItemIsEnabled)

                    self.TableWidgetImage.setItem(row, 0, newItemName)
                    self.TableWidgetImage.setItem(row, 1, newItemFSName)
                    self.TableWidgetImage.setItem(row, 2, newItemEmptyDir)

                    right_disk_image_explorer_content.append((file_name, "DIR"))


                else:
                    try:
                        # file_type is e.g. "[1234 bytes]" – extract the number
                        file_size = file_type.strip("[]").split()[0]
                    except Exception:
                        logging.info(f"update_disk_manager_widget_table file split failed for: {file_type}")
                        file_size = "0"

                    newItemFS = QTableWidgetItem(file_size)

                    file_ext = str.split(file_name, '.')[1] if '.' in file_name else ""
                    newItemExt = QTableWidgetItem(file_ext)

                    newItemFS.setForeground(self.img_color_file_size)
                    newItemName.setForeground(self.img_color_file_name)
                    newItemExt.setForeground(self.img_color_file_ext)

                    newItemFS.setFlags(~Qt.ItemIsEnabled)
                    newItemExt.setFlags(~Qt.ItemIsEnabled)


                    newItemFS.setFlags(newItemFS.flags() & ~Qt.ItemIsEditable) # make non editable
                    newItemExt.setFlags(newItemExt.flags() & ~Qt.ItemIsEditable) # make non editable
                    newItemName.setFlags(newItemName.flags() & ~Qt.ItemIsEditable) # make non editable

                    self.TableWidgetImage.setItem(row, 0, newItemName)
                    self.TableWidgetImage.setItem(row, 1, newItemExt)
                    self.TableWidgetImage.setItem(row, 2, newItemFS)



                    if '.' in file_name:
                        right_disk_image_explorer_content.append((file_name, file_ext))
                    else:
                        right_disk_image_explorer_content.append((file_name, ""))


                self.image_explorer_item_list.addItem(file_name)

                row += 1

            apply_image_filter()
            _update_image_usage_gauge(self.right_disk_image_path)


        def update_syncpoint(path_to_content, knownfiles):
            with open(path_to_content + SYNCPOINT, 'w') as f:
                for x in knownfiles:
                    f.write(f"{x}\n")

        def agecheck(path_to_content, f):
            if not os.path.isfile(path_to_content + SYNCPOINT):
                return False
            ptime = os.path.getmtime(path_to_content + SYNCPOINT)
            mtime = os.path.getmtime(f)
            if mtime > ptime:
                return False
            return True

        def getFileList(path_to_content):
            knownfiles = []
            if os.path.isfile(path_to_content + SYNCPOINT):
                with open(path_to_content + SYNCPOINT) as f:
                    knownfiles = f.read().splitlines()
            ignorelist = []
            if os.path.isfile(path_to_content + IGNOREFILE):
                with open(path_to_content + IGNOREFILE) as f:
                    ignorelist = f.read().splitlines()
            r = []
            gf = glob.glob(path_to_content + "**", recursive=True)
            for g in gf:
                if os.path.isfile(g):
                    basename = os.path.basename(g)
                    # Never send internal control files to the device
                    if basename in (SYNCPOINT, IGNOREFILE):
                        continue
                    ignored = False
                    for i in ignorelist:
                        # Match against full path OR basename so patterns like
                        # "syncpoint.dat" work alongside glob patterns like "*.py"
                        if fnmatch.fnmatch(g, i) or fnmatch.fnmatch(basename, i):
                            ignored = True
                            break
                    if not self.nextsync_alwayssync_checkbox.isChecked():
                        if g in knownfiles:
                            if agecheck(path_to_content, g):
                                ignored = True
                    if not ignored:
                        stats = os.stat(g)
                        r.append([g, stats.st_size])
            return r

        def timestamp():
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def sendpacket(conn, payload, packetno):
            checksum0 = 0 # random.choice([0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1]) # 5%
            checksum1 = 0
            # packetno -= random.choice([0]*99+[1]) # 1%
            for x in payload:
                checksum0 = (checksum0 ^ x) & 0xff
                checksum1 = (checksum1 + checksum0) & 0xff
            packet = ((len(payload)+5).to_bytes(2, byteorder="big")
                + payload
                + (checksum0 & 0xff).to_bytes(1, byteorder="big")
                + (checksum1 & 0xff).to_bytes(1, byteorder="big")
                + (packetno & 0xff).to_bytes(1, byteorder="big"))
            conn.sendall(packet)

            if ZX_NEXT_UNITE_VERBOSE_LOG_MODE:
                add_nextsync_log_window (str(timestamp()) + " | Packet sent: " + str(len(packet)) + " bytes, payload: " + str(len(payload)) + " bytes, checksums: " + str(checksum0) + ", " + str(checksum1) + ", packetno: " + str(packetno & 0xff) )

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
            initial = getFileList(selected_nextsync_explorer_sync_root_directory)
            total = 0
            for x in initial:
                total += x[1]
            severity = ""
            if len(initial) < 10 and total < 100000:
                severity ="Note"
            elif len(initial) < 100 and total < 1000000:
                severity = "Warning"
            else:
                severity = "WARNING"
            #add_nextsync_log_window (severity + ": Ready to sync " + str(len(initial)) +" files, " + str(total/1024) +" kilobytes.")
            add_nextsync_log_window (f"{severity}: Ready to sync {len(initial)} files, {total/1024:.2f} kilobytes.")
            add_nextsync_log_window ("")


            nextsync_show_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(False)

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

        def nextsync_do_server_job(progress_callback, status_callback=None, cancel_flag=None, serve_folder=None):
            """Run the NextSync server loop.

            progress_callback – Signal(int) or None; emitted with 0-100 per-file progress.
            status_callback   – Signal(str) or None; emitted with a human-readable status line.
            cancel_flag       – threading.Event or None; checked between socket accept retries.
            serve_folder      – str or None; when provided, serve exactly this folder instead of
                                the folder selected in the NextSync pane.
            """

            selected_nextsync_explorer_sync_root_directory = ""

            # Only touch the pane progress bar when running from the pane (no cancel_flag = pane invocation)
            if cancel_flag is None:
                self.nextsync_progressbar.setValue(0)
                self.nextsync_progressbar.setVisible(True)
                # hide all buttons
                self.nextsync_button_create_syncignore.setVisible(False)
                self.nextsync_button_delete_syncignore.setVisible(False)
                self.nextsync_button_delete_syncpointfile.setVisible(False)

            nextsync_show_ip_info()

            if serve_folder and os.path.isdir(serve_folder):
                # Caller specified an exact folder (e.g. downloads/comix) — use it directly.
                selected_nextsync_explorer_sync_root_directory = serve_folder.rstrip("/\\") + "/"
            elif self.left_file_nextsync_explorer_selection_full_filename_path:
                splitted_filepath = self.left_file_nextsync_explorer_selection_full_filename_path.split('/')
                if not os.path.isdir(self.left_file_nextsync_explorer_selection_full_filename_path):
                # if '.' in dest_file_content:
                    for file_dest_token in range (0, len(splitted_filepath)-1):
                        selected_nextsync_explorer_sync_root_directory += splitted_filepath[file_dest_token] + "/"
                else:
                    selected_nextsync_explorer_sync_root_directory = self.left_file_nextsync_explorer_selection_full_filename_path + "/"

            working = True
            while working:
                if cancel_flag is not None and cancel_flag.is_set():
                    working = False
                    break
                add_nextsync_log_window (f"{timestamp()} | NextSync listening to port {PORT}")
                add_nextsync_log_window (f"{timestamp()} | Now start run .sync (or .syncfast) command on your Next!" )
                totalbytes = 0
                payloadbytes = 0
                starttime = 0
                retries = 0
                packets = 0
                restarts = 0
                gee = 0
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", PORT))
                    s.listen()
                    # Poll for cancel every second while waiting for connection
                    s.settimeout(1.0)
                    conn = None
                    while conn is None:
                        if cancel_flag is not None and cancel_flag.is_set():
                            working = False
                            break
                        try:
                            conn, addr = s.accept()
                        except socket.timeout:
                            continue
                    if conn is None:
                        break  # cancelled during accept
                    # Make sure *nixes close the socket when we ask it to.
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                    f = getFileList(selected_nextsync_explorer_sync_root_directory)
                    add_nextsync_log_window (f'{timestamp()} | Sync file list has {len(f)} files.')
                    knownfiles = []
                    if os.path.isfile(selected_nextsync_explorer_sync_root_directory + SYNCPOINT):
                        with open(selected_nextsync_explorer_sync_root_directory + SYNCPOINT) as kf:
                            knownfiles = kf.read().splitlines()
                    fn = 0
                    filedata = b''
                    packet = b''
                    fileofs = 0
                    totalbytes = 0
                    packetno = 0
                    starttime = time.time()
                    endtime = starttime
                    with conn:
                        add_nextsync_log_window (f'{timestamp()} | Connected by {addr[0]} port {addr[1]}')
                        talking = True
                        while talking:
                            data = conn.recv(1024)
                            if not data:
                                break
                            decoded = data.decode()
                            if ZX_NEXT_UNITE_VERBOSE_LOG_MODE:
                                add_nextsync_log_window (f'{timestamp()} | Data received: "{decoded}", {len(decoded)} bytes')
                            if data == b"Sync3":
                                add_nextsync_log_window (f'{timestamp()} | Using protocol version: {VERSION3}')
                                packet = str.encode(VERSION3)
                                sendpacket(conn, packet, 0)
                                packets += 1
                                totalbytes += len(packet)
                            elif data == b"Next" or data == b"Neex": # Really common mistransmit. Probably uart-esp..
                                if data == b"Neex":
                                    gee += 1
                                # If the user pressed Cancel, finish gracefully at the next
                                # file boundary: the previously requested file has already
                                # been fully transferred at this point, so we just tell the
                                # client there is nothing more to sync.
                                _cancel_now = cancel_flag is not None and cancel_flag.is_set()
                                if fn >= len(f) or _cancel_now:
                                    if _cancel_now:
                                        add_nextsync_log_window (f"{timestamp()} | Cancel requested — stopping after current file")
                                        if status_callback is not None:
                                            status_callback.emit("Cancelled — finishing current file…")
                                    else:
                                        add_nextsync_log_window (f"{timestamp()} | Nothing (more) to sync")
                                    packet = b'\x00\x00\x00\x00\x00' # end of.
                                    packets += 1
                                    sendpacket(conn, packet, 0)
                                    totalbytes += len(packet)
                                    # Persist sync point even on cancel so already-sent
                                    # files aren't re-sent next time.
                                    update_syncpoint(selected_nextsync_explorer_sync_root_directory, knownfiles)
                                else:
                                    specfn = f[fn][0].replace('\\','/')
                                    add_nextsync_log_window (f"{timestamp()} | File: {f[fn][0]} (as {specfn}) length: {f[fn][1]} bytes")
                                    packet = (f[fn][1]).to_bytes(4, byteorder="big") + (len(specfn)).to_bytes(1, byteorder="big") + (specfn).encode()
                                    packets += 1
                                    sendpacket(conn, packet, 0)
                                    totalbytes += len(packet)
                                    with open(f[fn][0], 'rb') as srcfile:
                                        filedata = srcfile.read()
                                    payloadbytes += len(filedata)
                                    if f[fn][0] not in knownfiles:
                                        knownfiles.append(f[fn][0])
                                    fileofs = 0
                                    packetno = 0
                                    pct = int(fn * 100 / len(f)) if f else 0
                                    if progress_callback is not None:
                                        progress_callback.emit(pct)
                                    if status_callback is not None:
                                        status_callback.emit(f"Sending file {fn}/{len(f)}\n{specfn}")
                                    # also update pane progress bar when running from the pane
                                    if cancel_flag is None:
                                        self.nextsync_progressbar.setValue(pct)
                                    fn += 1
                            elif data == b"Get" or data == b"Gee": # Really common mistransmit. Probably uart-esp..
                                bytecount = MAX_PAYLOAD
                                if bytecount + fileofs > len(filedata):
                                    bytecount = len(filedata) - fileofs
                                packet = filedata[fileofs:fileofs+bytecount]
                                if ZX_NEXT_UNITE_VERBOSE_LOG_MODE:
                                    if filedata:
                                        add_nextsync_log_window (f"{timestamp()} | Sending {bytecount} bytes, offset {fileofs}/{len(filedata)}")
                                    else:
                                        add_nextsync_log_window (f"{timestamp()} | Sending {bytecount} bytes 0 bytes")

                                packets += 1
                                sendpacket(conn, packet, packetno)
                                totalbytes += len(packet)
                                fileofs += bytecount
                                packetno += 1
                                if data == b"Gee":
                                    gee += 1
                            elif data == b"Retry":
                                retries += 1
                                add_nextsync_log_window (f"{timestamp()} | Resending")
                                sendpacket(conn, packet, packetno - 1)
                            elif data == b"Restart":
                                restarts += 1
                                add_nextsync_log_window (f"{timestamp()} | Restarting")
                                fileofs = 0
                                packetno = 0
                                sendpacket(conn, str.encode("Back"), 0)
                            elif data == b"Bye":
                                sendpacket(conn, str.encode("Later"), 0)
                                add_nextsync_log_window (f"{timestamp()} | Closing connection")
                                talking = False
                            elif data == b"Sync2" or data == b"Sync1" or data == b"Sync":
                                packet = str.encode("Nextsync 0.8 or later needed")
                                add_nextsync_log_window (f'{timestamp()} | Old protocol version requested')
                                sendpacket(conn, packet, 0)
                                packets += 1
                                totalbytes += len(packet)
                            else:
                                add_nextsync_log_window (f"{timestamp()} | Unknown command")
                                sendpacket(conn, str.encode("Error"), 0)
                        endtime = time.time()
                deltatime = endtime - starttime
                add_nextsync_log_window (f"{timestamp()} | {totalbytes/1024:.2f} kilobytes transferred in {deltatime:.2f} seconds, {(totalbytes/deltatime)/1024:.2f} kBps")
                add_nextsync_log_window (f"{timestamp()} | {payloadbytes/1024:.2f} kilobytes payload, {(payloadbytes/deltatime)/1024:.2f} kBps effective speed")
                add_nextsync_log_window (f"{timestamp()} | packets: {packets}, retries: {retries}, restarts: {restarts}, gee: {gee}")

                add_nextsync_log_window (f"{timestamp()} | Disconnected")
                add_nextsync_log_window ("")
                if self.nextsync_synconce_checkbox.isChecked() or (cancel_flag is not None and cancel_flag.is_set()):
                    working = False

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

        # Pre-populate color defaults so save works correctly before first load
        configuration_dictionary[SETTING_COLOR_UP_DIRECTORY] = DEFAULT_COLOR_UP_DIRECTORY
        configuration_dictionary[SETTING_COLOR_DIR_NAME]     = DEFAULT_COLOR_DIR_NAME
        configuration_dictionary[SETTING_COLOR_DIR_TYPE]     = DEFAULT_COLOR_DIR_TYPE
        configuration_dictionary[SETTING_COLOR_FILE_NAME]    = DEFAULT_COLOR_FILE_NAME
        configuration_dictionary[SETTING_COLOR_FILE_EXT]     = DEFAULT_COLOR_FILE_EXT
        configuration_dictionary[SETTING_COLOR_FILE_SIZE]    = DEFAULT_COLOR_FILE_SIZE

        # Init UI forms

        self.setWindowIcon(QIcon(ZX_NEXT_UNITE_ICON_IMAGE_FILE))


        self.zx_next_unite_form = QFormLayout()
        self.nextsync_form = QFormLayout()

        # zx_next_unite horizontals
        self.horizontal1 = QHBoxLayout()
        self.horizontal2 = QHBoxLayout()
        self.horizontal3 = QHBoxLayout()
        self.horizontal4 = QHBoxLayout()
        self.horizontal5 = QHBoxLayout()
        self.horizontal6 = QHBoxLayout()

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
            "or use the 'Select Disk Image' button to browse."
        )
        self.imageinput.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.imageinput.lineEdit().setPlaceholderText("SD card image path...")
        # Pressing Enter in the editable field triggers a load attempt
        self.imageinput.lineEdit().returnPressed.connect(lambda: load_image())
        # Selecting an item from the history dropdown loads it immediately
        self.imageinput.activated.connect(lambda _index: load_image())
        self.selectimage = QPushButton("ToDisk", self)
        self.selectimage.setText("Select Disk Image")
        self.selectimage.toolTip = "Select a disk image to be loaded."
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
            self.zx_next_unite_diskdrive.activated.connect(update_root_drive)
        else:
            available_drives.append('/')
            self.zx_next_unite_diskdrive.setVisible(False)

        self.filterlabel = QLabel()
        self.filterlabel.setText("Search: ")


        self.horizontal2.addWidget(self.filterlabel)

        self.filtertext = QLineEdit()
        self.filtertext.setPlaceholderText("Filter by name...")
        self.filtertext.textChanged.connect(apply_file_extension_filter)
        self.filtertext.setMinimumWidth(FILTER_TEXT_WIDTH)
        self.filtertext.setMaximumWidth(FILTER_TEXT_WIDTH)

        self.horizontal2.addWidget(self.filtertext)

        self.diskimageexplorerlabel = QLabel()
        self.diskimageexplorerlabel.setText("                Disk Image Explorer: ")

        self.horizontal2.addWidget(self.diskimageexplorerlabel)

        self.diskimageexplorerlabelpath = QLabel()
        self.diskimageexplorerlabelpath.setText("")

        self.diskimageexplorerlabelpath.setMinimumWidth(400)
        #self.diskimageexplorerlabelpath.setMaximumWidth(400)

        self.horizontal2.addWidget(self.diskimageexplorerlabelpath)

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
        self.image_filtertext.textChanged.connect(apply_image_filter)
        self.image_filtertext.setMinimumWidth(FILTER_TEXT_WIDTH)
        self.image_filtertext.setMaximumWidth(FILTER_TEXT_WIDTH)
        self.horizontal2.addWidget(self.image_filtertext)

        self.zx_next_unite_form.addRow(self.horizontal2)

        self.model = QFileSystemModel()

        self.model.setRootPath('/')
        self.model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)

        self.treeview = QTreeView()
        self.treeview.setSortingEnabled(True)

        self.proxy_model = DotDotFirstProxyModel(recursiveFilteringEnabled = True, filterRole = QFileSystemModel.FileNameRole)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.proxy_model.setDynamicSortFilter(True)

        self.treeview.setModel(self.proxy_model)
        self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(available_drives[0])))

        self.treeview.show()
        self.treeview.setColumnWidth(0, 250)
        self.treeview.doubleClicked.connect(on_treeview_double_clicked)
        self.treeview.clicked.connect(on_treeview_clicked)
        self.treeview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.treeview.customContextMenuRequested.connect(on_treeview_context_menu)

        self.centralbuttonscontainer = QWidget()
        self.centralbuttons = QVBoxLayout()

        self.button_to_disk = QPushButton("ToDisk", self)
        self.button_to_disk.setText("<<<")
        self.button_to_disk.setMaximumWidth(DISK_ARROWS_BUTTONS_SIZE)
        self.button_to_disk.clicked.connect(transfert_content_from_image_to_disk)

        self.button_to_image = QPushButton("ToImage", self)
        self.button_to_image.setText(">>>")
        self.button_to_image.setMaximumWidth(DISK_ARROWS_BUTTONS_SIZE)
        self.button_to_image.clicked.connect(transfert_content_from_disk_to_image)

        self.TableWidgetImage = QTableWidget(0, 3, self) # https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QTableWidget.html https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QListWidget.html
        set_table_image_properties()

        self.TableWidgetImage.doubleClicked.connect(disk_image_explorer_item_double_clicked)
        self.TableWidgetImage.itemSelectionChanged.connect(image_explorer_selection_changed)

        def _table_image_key_press(event):
            if event.key() == Qt.Key.Key_Delete and right_disk_image_selected_files:
                delete_files_button_show_confirmation_buttons()
            else:
                QTableWidget.keyPressEvent(self.TableWidgetImage, event)

        self.TableWidgetImage.keyPressEvent = _table_image_key_press

        # Usage gauge — sits directly below the image explorer table
        self.image_usage_gauge = QProgressBar()
        self.image_usage_gauge.setRange(0, 100)
        self.image_usage_gauge.setValue(0)
        self.image_usage_gauge.setFormat("No image loaded")
        self.image_usage_gauge.setFixedHeight(18)
        self.image_usage_gauge.setToolTip("No SD card image is currently loaded.")
        self.image_usage_gauge.setTextVisible(True)

        self.image_explorer_container = QWidget()
        image_explorer_vbox = QVBoxLayout(self.image_explorer_container)
        image_explorer_vbox.setContentsMargins(0, 0, 0, 0)
        image_explorer_vbox.setSpacing(2)
        image_explorer_vbox.addWidget(self.TableWidgetImage)
        image_explorer_vbox.addWidget(self.image_usage_gauge)

        self.horizontal3.addWidget(self.treeview)

        self.centralbuttons.addWidget(self.button_to_image)
        self.centralbuttons.addWidget(self.button_to_disk)

        self.centralbuttons.setAlignment(Qt.AlignCenter)
        self.centralbuttonscontainer.setLayout(self.centralbuttons)
        self.horizontal3.addWidget(self.centralbuttonscontainer)
        self.horizontal3.addWidget(self.image_explorer_container)

        self.zx_next_unite_form.addRow(self.horizontal3)

        self.listWidgetLog = QListWidget(self)

        for l in INIT_LOG:
            add_main_log_window(l)

        self.listWidgetHelp = QListWidget(self)

        for l in INIT_HELP:
            add_help_content(l, False)


        self.listWidgetLog.setMinimumHeight(120)
        self.listWidgetLog.setMaximumHeight(160)
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

        self.download_and_install_hdfmonkey_button = QPushButton("Download & install HDF Monkey", self)
        self.download_and_install_hdfmonkey_button.setText("Download and install HDF Monkey from speccy.org")
        self.download_and_install_hdfmonkey_button.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.download_and_install_hdfmonkey_button.clicked.connect(download_and_install_hdflonkey)
        self.download_and_install_hdfmonkey_button.setVisible(False)

        self.hiddenspacelabel2 = QLabel()
        self.hiddenspacelabel2.setText("       ")
        self.imageexplorerbuttons.addWidget(self.hiddenspacelabel2)

        self.button_delete_files = QPushButton("DeleteFiles", self)
        self.button_delete_files.setText("Delete Files or Folder")
        self.button_delete_files.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.button_delete_files.clicked.connect(delete_files_button_show_confirmation_buttons)

        self.button_cancel = QPushButton("Cancel", self)
        self.button_cancel.setText("Cancel")
        self.button_cancel.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.button_cancel.setVisible(False)
        self.button_cancel.clicked.connect(button_cancel_deletion)

        self.button_confirm_deletion = QPushButton("Yes, confirm deletion", self)
        self.button_confirm_deletion.setText("Yes, confirm deletion")
        self.button_confirm_deletion.setMinimumWidth(IMAGE_BUTTONS_SIZE)
        self.button_confirm_deletion.setVisible(False)

        self.button_confirm_deletion.clicked.connect(button_confirm_directory_deletion)

        self.imageexplorerbuttons.addWidget(self.button_new_folder)
        self.imageexplorerbuttons.addWidget(self.button_delete_files)

        self.imageexplorerbuttons.addWidget(self.button_confirm_deletion)
        self.imageexplorerbuttons.addWidget(self.button_cancel)

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

        # Show Explorer selected Path

        self.file_explorer_path = QLineEdit()
        self.file_explorer_path.setText("-")
        self.file_explorer_path.setPlaceholderText("Path...")
        self.file_explorer_path.editingFinished.connect(on_file_explorer_path_edited)

        self.horizontal4.addWidget(self.file_explorer_path)

        self.zx_next_unite_form.addRow(self.horizontal4)

        # Add Log Window
        self.horizontal5.addWidget(self.listWidgetLog)

        self.horizontal5.addWidget(self.imageexplorerbuttonscontainer)

        self.zx_next_unite_form.addRow(self.horizontal5)

        # Add action buttons at the bottom

        # "Launch Mame" button — placed before "Launch CSpect". Only shown when
        # the MAME executable was found on the system PATH at startup.
        self.button_start_mame = QPushButton("🕹  Launch Mame", self)
        self.button_start_mame.setText("🕹  Launch Mame")
        self.button_start_mame.clicked.connect(launch_mame)
        self.button_start_mame.setVisible(self._mame_executable_path is not None)
        self.horizontal6.addWidget(self.button_start_mame)

        self.button_start_cspect = QPushButton("🕹  LaunchCSpect", self)
        self.button_start_cspect.setText("🕹  Launch CSpect")
        self.button_start_cspect.clicked.connect(launch_cspect)
        self.horizontal6.addWidget(self.button_start_cspect)

        # Populate Screen Size Combo
        self.cspect_screensize = QComboBox()

        for sc in CSPECT_SCREEN_SIZES:
             self.cspect_screensize.addItem(sc[0])

        self.cspect_screensize.show()
        self.cspect_screensize.currentIndexChanged.connect(set_cspect_screen_size)

        self.horizontal6.addWidget(self.cspect_screensize)

        # Populate Sound Combo
        self.cspect_sound = QComboBox()

        for ssound in CSPECT_SOUND:
             self.cspect_sound.addItem(ssound[0])

        self.cspect_sound.show()
        self.cspect_sound.currentIndexChanged.connect(set_cspect_sound_on_off)

        self.horizontal6.addWidget(self.cspect_sound)

        # Populate vsync Combo
        self.cspect_vsync = QComboBox()

        for vs in CSPECT_SCREEN_SYNC:
             self.cspect_vsync.addItem(vs[0])

        self.cspect_vsync.show()
        self.cspect_vsync.currentIndexChanged.connect(set_cspect_vsync_on_off)

        self.horizontal6.addWidget(self.cspect_vsync)

        # Populate Joystick Combo
        self.cspect_joystick = QComboBox()

        for jsc in CSPECT_JOYSTICK:
             self.cspect_joystick.addItem(jsc[0])

        self.cspect_joystick.show()
        self.cspect_joystick.currentIndexChanged.connect(set_cspect_joystick_on_off)

        self.horizontal6.addWidget(self.cspect_joystick)

        # Populate frequency Combo
        self.cspect_frequency = QComboBox()

        for cf in CSPECT_FREQUENCY:
             self.cspect_frequency.addItem(cf[0])

        self.cspect_frequency.show()
        self.cspect_frequency.currentIndexChanged.connect(set_cspect_display_frequency)

        self.horizontal6.addWidget(self.cspect_frequency)

        self.button_open_config_file = QPushButton("Open config file", self)
        self.button_open_config_file.setText("Open config file")
        self.button_open_config_file.clicked.connect(open_cspect_configuration_file)
        self.horizontal6.addWidget(self.button_open_config_file)

        # Hide all CSpect controls when the CSpect emulator was not found at
        # startup (application directory or PATH). The MAME button and the
        # general "Open config file" button are unaffected.
        if self._cspect_executable_path is None:
            for _cspect_widget in (
                self.button_start_cspect,
                self.cspect_screensize,
                self.cspect_sound,
                self.cspect_vsync,
                self.cspect_joystick,
                self.cspect_frequency,
            ):
                _cspect_widget.setVisible(False)

        self.zx_next_unite_form.addRow(self.horizontal6)

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
        grid_inner.addWidget(wid_inner.tab)

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

        # ── Favorites helpers (cross-pane, captured by closures below) ──
        _FAV_SOURCE_LABELS = {"getit": "GetIt", "zxdb": "ZXDB", "zxart": "zxArt"}

        def _fav_source_of(entry):
            """Best-effort detection of which pane an entry came from."""
            if not isinstance(entry, dict):
                return ""
            s = (entry.get("_fav_source") or entry.get("source") or "").lower()
            if s in _FAV_SOURCE_LABELS:
                return s
            kind = (entry.get("_kind") or "").lower()
            if kind.startswith("zxart"):
                return "zxart"
            if kind in ("game", "magazine", "suggest"):
                return "zxdb"
            if entry.get("category") is not None or "size" in entry:
                return "getit"
            return ""

        def _fav_key(source, entry_id):
            return (str(source or ""), str(entry_id or ""))

        def _fav_is(entry):
            if not isinstance(entry, dict):
                return False
            src = _fav_source_of(entry)
            eid = entry.get("id") or ""
            if not src or not eid:
                return False
            return _fav_key(src, eid) in self._favorites_index

        def _fav_make_record(entry, source):
            rec = {}
            try:
                # Deep copy via JSON so we keep a serializable, decoupled
                # snapshot of the upstream entry (incl. _source, _kind, etc).
                rec = json.loads(json.dumps(entry, ensure_ascii=False, default=str))
            except Exception:
                rec = {}
            rec["source"]      = source
            rec["_fav_source"] = source
            rec["id"]          = str(entry.get("id") or "")
            return rec

        def _fav_save():
            try:
                configuration_dictionary[SETTING_FAVORITES] = json.dumps(
                    self._favorites, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                configuration_dictionary[SETTING_FAVORITES] = "[]"
            save_configuration_file()

        def _fav_update_tab_badge():
            try:
                idx = -1
                for i in range(self._tab_widget.count()):
                    if self._tab_widget.tabText(i).startswith(
                            ZX_NEXT_UNITE_TAB_TITLE_FAVORITES):
                        idx = i
                        break
                if idx < 0:
                    return
                n = len(self._favorites)
                self._tab_widget.setTabText(
                    idx, f"{ZX_NEXT_UNITE_TAB_TITLE_FAVORITES} ({n})")
            except Exception:
                pass

        def _fav_refresh_all_galleries():
            for attr in ("getit_gallery_view", "zxdb_gallery_view",
                         "zxart_gallery_view", "favorites_gallery_view"):
                gv = getattr(self, attr, None)
                if gv is not None:
                    try:
                        gv.refresh_favorites()
                    except Exception:
                        pass
            # Re-populate the favorites grid so removals disappear and adds
            # show up.
            try:
                _fav_repopulate = getattr(self, "_fav_repopulate_fn", None)
                if _fav_repopulate is not None and not self._favorites_refreshing:
                    self._favorites_refreshing = True
                    try:
                        _fav_repopulate()
                    finally:
                        self._favorites_refreshing = False
            except Exception:
                pass

        def _fav_toggle(entry):
            if not isinstance(entry, dict):
                return
            src = _fav_source_of(entry)
            eid = entry.get("id") or ""
            if not src or not eid:
                return
            key = _fav_key(src, eid)
            if key in self._favorites_index:
                self._favorites = [
                    f for f in self._favorites
                    if _fav_key(f.get("source"), f.get("id")) != key
                ]
                self._favorites_index.discard(key)
            else:
                rec = _fav_make_record(entry, src)
                self._favorites.append(rec)
                self._favorites_index.add(key)
            _fav_save()
            _fav_update_tab_badge()
            _fav_refresh_all_galleries()

        self._fav_is               = _fav_is
        self._fav_toggle           = _fav_toggle
        self._fav_source_of        = _fav_source_of
        self._fav_update_tab_badge = _fav_update_tab_badge
        self._fav_refresh_all      = _fav_refresh_all_galleries
        self._fav_source_label_for = lambda e: _FAV_SOURCE_LABELS.get(
            _fav_source_of(e), "")

        def _fav_navigate_to_source(entry):
            if not isinstance(entry, dict):
                return
            src   = self._fav_source_of(entry)
            eid   = entry.get("id") or ""
            title = entry.get("title") or ""
            query = str(title or eid).strip()
            if not src:
                return
            try:
                if src == "getit":
                    target_title = ZX_NEXT_UNITE_TAB_TITLE_GETIT
                elif src == "zxdb":
                    target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXDB
                elif src == "zxart":
                    target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXART
                else:
                    return
                # Switch to the proper pane first.
                for i in range(self._tab_widget.count()):
                    if self._tab_widget.tabText(i).startswith(target_title):
                        self._tab_widget.setCurrentIndex(i)
                        break

                def _select_in(view_attr):
                    gv = getattr(self, view_attr, None)
                    if gv is None:
                        return
                    try:
                        gv.select_entry(lambda e, _eid=str(eid):
                                        str(e.get("id") or "") == _eid)
                    except Exception:
                        pass

                if src == "getit":
                    self.getit_search_input.setText(query)
                    def _gi_done(_va="getit_gallery_view"):
                        _select_in(_va)
                    getit_run_search(query, 1, _gi_done)
                elif src == "zxdb":
                    self.zxdb_search_input.setText(query)
                    def _zd_done(_va="zxdb_gallery_view"):
                        _select_in(_va)
                    zxdb_run_search(query, 1, _zd_done)
                elif src == "zxart":
                    self.zxart_search_input.setText(query)
                    def _za_done(_va="zxart_gallery_view"):
                        _select_in(_va)
                    zxart_run_search(query, 1, _za_done)
            except Exception:
                pass

        self._fav_navigate_to_source = _fav_navigate_to_source

        zx_next_unite_container = QWidget()
        zx_next_unite_container.setLayout(self.zx_next_unite_form)

        nextsync_container = QWidget()
        nextsync_container.setLayout(self.nextsync_form)

        self.nextsync_log_and_sync_buttons_container = QWidget()
        self.nextsync_container_log_and_sync_buttons = QVBoxLayout()

        self.nextsync_container_log_and_sync_buttons.setAlignment(Qt.AlignTop)
        self.nextsync_log_and_sync_buttons_container.setLayout(self.nextsync_container_log_and_sync_buttons)


        self.nextsync_fileexplorer_and_buttons_container = QWidget()
        self.nextsync_container_fileexplorer_and_buttons_buttons = QVBoxLayout()

        self.nextsync_container_fileexplorer_and_buttons_buttons.setAlignment(Qt.AlignTop)
        self.nextsync_fileexplorer_and_buttons_container.setLayout(self.nextsync_container_fileexplorer_and_buttons_buttons)

        # Add Disk drive selection
        self.nextsync_diskdrive = QComboBox()

        if platform.system() == "Windows":

            available_drives = list_windows_drives()

            for letter in available_drives:
                 self.nextsync_diskdrive.addItem(letter)

            self.nextsync_diskdrive.show()

            self.horizontal10.addWidget(self.nextsync_diskdrive)
            self.nextsync_diskdrive.activated.connect(nextsync_update_root_drive)
        else:
            available_drives.append('/')
            self.nextsync_diskdrive.setVisible(False)


        # Add Filter
        self.nextsync_filterlabel = QLabel()
        self.nextsync_filterlabel.setText("Search: ")

        self.horizontal10.addWidget(self.nextsync_filterlabel)

        self.nextsync_filtertext = QLineEdit()
        self.nextsync_filtertext.setPlaceholderText("Filter by name...")
        self.nextsync_filtertext.textChanged.connect(apply_file_extension_filter_nextsync)
        self.nextsync_filtertext.setMinimumWidth(FILTER_TEXT_WIDTH + 400)
        self.nextsync_filtertext.setMaximumWidth(FILTER_TEXT_WIDTH + 400)

        self.horizontal10.addWidget(self.nextsync_filtertext)


        self.nextsync_form.addRow(self.horizontal10)

        self.nextsync_treeview = QTreeView()

        self.nextsync_filesystem_model = QFileSystemModel()

        self.nextsync_filesystem_model.setRootPath('/')
        self.nextsync_filesystem_model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
        self.nextsync_filesystem_model.sort(0, Qt.AscendingOrder)


        self.nextsync_model = DotDotFirstProxyModel(recursiveFilteringEnabled = True, filterRole = QFileSystemModel.FileNameRole)
        self.nextsync_model.setSourceModel(self.nextsync_filesystem_model)
        self.nextsync_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.nextsync_model.setDynamicSortFilter(True)

        self.nextsync_treeview.setModel(self.nextsync_model)
        self.nextsync_treeview.setSortingEnabled(True)
        self.nextsync_treeview.setRootIndex(self.nextsync_model.mapFromSource(self.nextsync_filesystem_model.index(available_drives[0])))
        self.nextsync_model.sort(0, QtCore.Qt.AscendingOrder)

        self.nextsync_treeview.show()
        self.nextsync_treeview.setColumnWidth(0, 250)

        self.nextsync_treeview.clicked.connect(nextsync_on_treeview_clicked)
        self.nextsync_treeview.doubleClicked.connect(nextsync_on_treeview_double_clicked)
        self.nextsync_treeview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nextsync_treeview.customContextMenuRequested.connect(nextsync_on_treeview_context_menu)

        set_treeview_properties()

        self.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(self.nextsync_treeview)

        # Show Explorer selected Path

        self.nextsync_file_explorer_path = QLineEdit()
        self.nextsync_file_explorer_path.setText("-")
        self.nextsync_file_explorer_path.setPlaceholderText("Path...")
        self.nextsync_file_explorer_path.editingFinished.connect(on_nextsync_file_explorer_path_edited)

        self.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(self.nextsync_file_explorer_path)


        self.horizontal12.addWidget(self.nextsync_fileexplorer_and_buttons_container)


        self.nextsync_button_create_syncignore = QPushButton("Create SyncIgnore File", self)
        self.nextsync_button_create_syncignore.setText("Create SyncIgnore File")
        self.nextsync_button_create_syncignore.clicked.connect(nextsync_create_syncingore_button)
        self.nextsync_button_create_syncignore.setVisible(False)

        self.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(self.nextsync_button_create_syncignore)

        self.nextsync_button_delete_syncignore = QPushButton("Delete SyncIgnore File", self)
        self.nextsync_button_delete_syncignore.setText("Delete SyncIgnore File")
        self.nextsync_button_delete_syncignore.clicked.connect(nextsync_delete_syncingore_button)
        self.nextsync_button_delete_syncignore.setVisible(False)

        self.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(self.nextsync_button_delete_syncignore)

        self.nextsync_button_delete_syncpointfile = QPushButton("Delete SyncPoint File", self)
        self.nextsync_button_delete_syncpointfile.setText("Delete SyncPoint File")
        self.nextsync_button_delete_syncpointfile.clicked.connect(nextsync_delete_syncpoint_button)
        self.nextsync_button_delete_syncpointfile.setVisible(False)

        self.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(self.nextsync_button_delete_syncpointfile)

        self.nextsync_form.addRow(self.horizontal12)


        # Add NextSync Log Window

        self.nextsync_log = QListWidget(self)
        self.nextsync_log.setMinimumHeight(NEXTSYNC_UI_HEIGTH)
        #self.nextsync_log.setMaximumHeight(NEXTSYNC_UI_HEIGTH)

        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_log)


        self.nextsync_synconce_checkbox = QCheckBox("Sync once")
        self.nextsync_synconce_checkbox.setText("Sync once")
        #self.nextsync_synconce_checkbox.setChecked(True)
        self.nextsync_synconce_checkbox.stateChanged.connect(nextsync_synconce_checkbox_statechanged)
        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_synconce_checkbox)

        self.nextsync_alwayssync_checkbox = QCheckBox("Always Sync")
        self.nextsync_alwayssync_checkbox.setText("Always Sync")
        #self.nextsync_alwayssync_checkbox.setChecked(True)
        self.nextsync_alwayssync_checkbox.stateChanged.connect(nextsync_alwayssync_checkbox_statechanged)
        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_alwayssync_checkbox)


        self.nextsync_slowtransfer_checkbox = QCheckBox("Slow transfer")
        self.nextsync_slowtransfer_checkbox.setText("Slow transfer")
        #self.nextsync_alwayssync_checkbox.setChecked(True)
        self.nextsync_slowtransfer_checkbox.stateChanged.connect(nextsync_slowtransfer_checkbox_statechanged)
        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_slowtransfer_checkbox)


        self.nextsync_prepare_server = QPushButton("Prepare Server", self)
        self.nextsync_prepare_server.setText("Prepare NextSync network server")
        self.nextsync_prepare_server.clicked.connect(nextsync_perform_checks_and_prepare_server_start)

        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_prepare_server)



        self.nextsync_start_server = QPushButton("Yes, start NextSync Server", self)
        self.nextsync_start_server.setText("Yes, start NextSync Server")
        self.nextsync_start_server.clicked.connect(nextsync_start_server)

        self.nextsync_cancel_server = QPushButton("Cancel NextSync Server", self)
        self.nextsync_cancel_server.setText("Cancel sync")
        self.nextsync_cancel_server.clicked.connect(nextsync_cancel_server_job)


        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_start_server)
        self.nextsync_container_log_and_sync_buttons.addWidget(self.nextsync_cancel_server)




        self.horizontal12.addWidget(self.nextsync_log_and_sync_buttons_container)


        self.nextsync_form.addRow(self.horizontal14)

        nextsync_hide_start_cancel_buttons()

        self.nextsync_progressbar = QProgressBar()
        self.nextsync_progressbar.setGeometry(QRect(20, 10, 361, 23))
        self.nextsync_progressbar.setProperty("value", 0)
        self.nextsync_progressbar.setObjectName("progressBar")
        self.nextsync_progressbar.setVisible(False)

        self.horizontal15.addWidget(self.nextsync_progressbar)


        self.nextsync_form.addRow(self.horizontal15)

        # -----------------------------------------------------------------------
        # GetIt UI construction
        # -----------------------------------------------------------------------

        self.getit_form = QFormLayout()
        self.getit_form.setContentsMargins(4, 4, 4, 4)

        # --- Search row ---
        getit_search_row = QHBoxLayout()
        self.getit_search_input = QLineEdit()
        self.getit_search_input.setPlaceholderText("Search files... (leave empty for latest 20)")
        self.getit_search_input.setMinimumWidth(280)
        getit_search_row.addWidget(self.getit_search_input)

        self._getit_search_valid_lbl = QLabel()
        self._getit_search_valid_lbl.setVisible(False)
        getit_search_row.addWidget(self._getit_search_valid_lbl)

        self.getit_search_button = QPushButton("Search")
        getit_search_row.addWidget(self.getit_search_button)

        self.getit_latest_button = QPushButton("Latest")
        getit_search_row.addWidget(self.getit_latest_button)

        self.getit_random_button = QPushButton("Random")
        self.getit_random_button.setToolTip(
            "Pick a random page from the full GetIt catalogue and show its entries."
        )
        getit_search_row.addWidget(self.getit_random_button)

        getit_search_row.addWidget(QLabel("Page:"))
        self.getit_page_label = QLabel("1")
        self.getit_page_label.setMinimumWidth(24)
        getit_search_row.addWidget(self.getit_page_label)

        self.getit_prev_button = QPushButton("< Prev")
        self.getit_prev_button.setEnabled(False)
        getit_search_row.addWidget(self.getit_prev_button)

        self.getit_next_button = QPushButton("Next >")
        self.getit_next_button.setEnabled(False)
        getit_search_row.addWidget(self.getit_next_button)

        getit_search_row.addWidget(QLabel("View:"))
        self.getit_view_combo = QComboBox()
        self.getit_view_combo.addItem("Table",   "table")
        self.getit_view_combo.addItem("Gallery", "gallery")
        self.getit_view_combo.setToolTip(
            "Switch between the classic table view and the picture (gallery) view.\n"
            "Persisted across sessions in the config file."
        )
        getit_search_row.addWidget(self.getit_view_combo)

        self.getit_status_label = QLabel("")
        getit_search_row.addWidget(self.getit_status_label, 1)

        getit_search_widget = QWidget()
        getit_search_widget.setLayout(getit_search_row)
        # NOTE: the search/button bar is intentionally NOT added to the
        # scrolled form here.  It is placed in a fixed header above the
        # scroll area (see _getit_stack assembly) so the vertical scroller
        # only spans the results/details area, matching the Unite! tab.
        self._getit_search_widget = getit_search_widget

        # --- Results table ---
        self.getit_results_table = QTableWidget(0, 4)
        self.getit_results_table.setHorizontalHeaderLabels(["ID", "Title", "Author", "Size"])
        self.getit_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.getit_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.getit_results_table.horizontalHeader().setStretchLastSection(True)
        self.getit_results_table.setMinimumHeight(200)
        self.getit_results_table.setColumnWidth(0, 70)
        self.getit_results_table.setColumnWidth(1, 350)
        self.getit_results_table.setColumnWidth(2, 150)
        self.getit_results_table.setColumnWidth(3, 80)

        self.getit_screenshot_label = QLabel()
        self.getit_screenshot_label.setFixedSize(256, 192)
        self.getit_screenshot_label.setAlignment(Qt.AlignCenter)
        self.getit_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
        self.getit_screenshot_label.setText("No preview")
        self.getit_screenshot_label.setToolTip("Double-click to enlarge")

        _GETIT_BTN_STYLE = (
            "QPushButton { color: #eee; background: #2a2a2a; border: 1px solid #444;"
            " border-radius: 4px; padding: 6px 12px; text-align: left; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #666; }"
            "QPushButton:disabled { color: #555; background: #1a1a1a; border-color: #333; }"
        )
        self.getit_download_button = QPushButton("⬇  Download")
        self.getit_download_button.setStyleSheet(_GETIT_BTN_STYLE)
        self.getit_download_button.setEnabled(False)

        self.getit_send_sd_button = QPushButton("💾  Send to SD card")
        self.getit_send_sd_button.setStyleSheet(_GETIT_BTN_STYLE)
        self.getit_send_sd_button.setEnabled(False)

        self.getit_send_ns_button = QPushButton("🔁  Send via NextSync")
        self.getit_send_ns_button.setStyleSheet(_GETIT_BTN_STYLE)
        self.getit_send_ns_button.setEnabled(False)

        getit_right_col = QVBoxLayout()
        _getit_link_label = QLabel('<a href="http://zxnext.uk">http://zxnext.uk</a>')
        _getit_link_label.setOpenExternalLinks(True)
        _getit_link_label.setTextFormat(Qt.RichText)
        _getit_link_label.setAlignment(Qt.AlignCenter)
        getit_right_col.addWidget(_getit_link_label)
        # Visibility is controlled by _getit_apply_view_mode (shown in Table, hidden in Gallery)
        self.getit_screenshot_label.setVisible(False)
        self.getit_download_button.setVisible(False)
        self.getit_send_sd_button.setVisible(False)
        self.getit_send_ns_button.setVisible(False)
        getit_right_col.addWidget(self.getit_screenshot_label)
        getit_right_col.addWidget(self.getit_download_button)
        getit_right_col.addWidget(self.getit_send_sd_button)
        getit_right_col.addWidget(self.getit_send_ns_button)
        self._getit_preview_label        = self.getit_screenshot_label
        self._getit_preview_download_btn = self.getit_download_button
        self._getit_preview_send_sd_btn  = self.getit_send_sd_button
        self._getit_preview_send_ns_btn  = self.getit_send_ns_button
        getit_right_col.addStretch()
        getit_right_widget = QWidget()
        getit_right_widget.setLayout(getit_right_col)
        self._getit_right_widget = getit_right_widget

        getit_table_row = QHBoxLayout()

        self.getit_view_stack = QStackedWidget()
        self.getit_view_stack.addWidget(self.getit_results_table)  # index 0: Table

        def _getit_gallery_title(e):
            return (e.get("title") or e.get("id") or "")[:80]
        def _getit_gallery_info(e):
            parts = []
            if e.get("author"):   parts.append(e["author"])
            if e.get("date"):     parts.append(str(e["date"]))
            if e.get("category"): parts.append(e["category"])
            return " · ".join(parts)

        def _getit_thumb_fetch(entry, set_pixmap, set_screenshots,
                               set_tags=None, set_info_text=None):
            eid = entry.get("id") or ""
            url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
            set_screenshots([url])
            def _make_placeholder():
                # GetIt entries always describe a downloadable artefact; use
                # the entry filename / category as the typed label so a
                # missing image still shows the format (e.g. TAP, POK).
                link = self._getit_selected_link or ""
                title = entry.get("title") or eid
                ref = link or title
                label = zxfmt_label_for_name(ref) if ref else "FILE"
                if label == "FILE":
                    cat = (entry.get("category") or "").upper()
                    if cat:
                        label = cat[:6]
                placeholder_url = f"placeholder://{label}/{title}"
                set_screenshots([placeholder_url])
                pm = zxfmt_make_placeholder_pixmap(label, title)
                if not pm.isNull():
                    set_pixmap(pm, placeholder_url)
            def _fn(_u=url):
                tmp = tempfile.NamedTemporaryFile(suffix=".bmp", delete=False)
                tmp.close()
                with open(tmp.name, "wb") as _fh:
                    _fh.write(_http_fetch_bytes_with_retry(_u, timeout=20))
                return (tmp.name, _u)
            def _on_done(res, _set=set_pixmap):
                path, u = res
                px = QPixmap(path)
                try: os.unlink(path)
                except Exception: pass
                # Suppress libpng warnings for malformed PNGs
                if px.isNull():
                    _make_placeholder()
                    return
                _set(px, u)
            def _on_err(_err):
                _make_placeholder()
            getit_run_in_thread(_fn, _on_done, _on_err)

            # Lazily enrich the hover-info line with the entry date, which is
            # not part of the list endpoint. Author and category already come
            # from the list payload, so the cell shows useful info immediately.
            if set_info_text is not None and eid:
                def _det_fn(_eid=eid):
                    text = getit_fetch(f"/nx/{_eid}/f/")
                    return getit_parse_detail(text)
                def _det_ok(d, _e=entry, _set=set_info_text):
                    parts = []
                    if _e.get("author"):  parts.append(_e["author"])
                    date = (d.get("DATE") or "").strip() if isinstance(d, dict) else ""
                    if date:              parts.append(date)
                    if _e.get("category"):parts.append(_e["category"])
                    _set(" · ".join(parts))
                def _det_err(_e): pass
                getit_run_in_thread(_det_fn, _det_ok, _det_err)

        def _getit_extra_fetch(url, on_pixmap):
            # GetIt only exposes a single screenshot per entry; nothing to do.
            pass

        def _getit_extra_fetch_url(url, on_pixmap):
            """Generic URL → QPixmap fetcher used by GalleryItemViewer."""
            if isinstance(url, str) and url.startswith("placeholder://"):
                rest = url[len("placeholder://"):]
                label, _, sub = rest.partition("/")
                pm = zxfmt_make_placeholder_pixmap(label or "FILE", sub)
                if not pm.isNull():
                    on_pixmap(pm)
                return
            if zxscr_url_is_scr(url):
                base = _zxscr_basename_for_url(url)
                cached = _ZXSCR_PIXMAP_CACHE.get(base)
                if cached is not None and not cached.isNull():
                    on_pixmap(cached)
                    return
                def _scr_fn(_u=url, _b=base):
                    return (_http_fetch_bytes_with_retry(_u, timeout=20), _b)
                def _scr_ok(res):
                    data, b = res
                    pm = zxscr_convert_bytes_to_pixmap(data, b)
                    if pm is not None and not pm.isNull():
                        on_pixmap(pm)
                getit_run_in_thread(_scr_fn, _scr_ok, lambda _e: None)
                return
            def _fn(_u=url):
                tmp = tempfile.NamedTemporaryFile(suffix=".bmp", delete=False)
                tmp.close()
                with open(tmp.name, "wb") as _fh:
                    _fh.write(_http_fetch_bytes_with_retry(_u, timeout=20))
                return tmp.name
            def _on_done(path):
                px = QPixmap(path)
                try: os.unlink(path)
                except Exception: pass
                if not px.isNull():
                    on_pixmap(px)
                else:
                    on_pixmap(None)
            def _on_err(_e): on_pixmap(None)
            getit_run_in_thread(_fn, _on_done, _on_err)

        def _getit_gallery_context_menu(entry, global_pos):
            eid   = entry.get("id") or ""
            title = entry.get("title") or eid
            default_name = self._getit_selected_link or f"{eid}.bin"
            _safe_title  = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid
            _img_path    = self.right_disk_image_path or ""
            _img_label   = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                            ) if _img_path else "(no image loaded)"
            _sd_dest     = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base     = _getit_resolve_ns_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest     = os.path.join(_ns_base, _safe_title)
            menu = QMenu()
            act_dl      = menu.addAction(f'Download \u201c{title}\u201d')
            menu.addSeparator()
            act_send_sd = menu.addAction(f"Send to SD card (image)  \u2192  {_sd_dest}")
            act_send_sd.setEnabled(bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content))
            act_send_ns = menu.addAction(f"Send using NextSync  \u2192  {_ns_dest}")
            chosen = menu.exec(global_pos)
            if chosen is None:
                return
            if chosen is act_dl:
                getit_do_download(eid, default_name)
            elif chosen is act_send_sd:
                _getit_send_to_image(eid, default_name, title)
            elif chosen is act_send_ns:
                def _after_ns_dl_gi(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after_ns_dl_gi)

        self.getit_gallery_view = GalleryView(
            rows_per_page_getter=lambda: self._gallery_rows_per_page,
            anim_mode_getter=lambda: self._gallery_anim_mode,
            cols_getter=lambda: self._gallery_cols,
            img_size_getter=lambda: self._gallery_img_size,
            thumb_fetch_cb=_getit_thumb_fetch,
            extra_fetch_cb=_getit_extra_fetch,
            title_getter=_getit_gallery_title,
            info_getter=_getit_gallery_info,
            context_menu_cb=_getit_gallery_context_menu,
            is_favorite_cb=lambda e: self._fav_is({**e, "_fav_source": "getit"}),
            toggle_favorite_cb=lambda e: self._fav_toggle({**e, "_fav_source": "getit"}),
        )
        self._fav_fetchers = getattr(self, "_fav_fetchers", {})
        self._fav_fetchers["getit"] = {
            "thumb": _getit_thumb_fetch,
            "extra": _getit_extra_fetch,
            "title": _getit_gallery_title,
            "info":  _getit_gallery_info,
        }
        self.getit_view_stack.addWidget(self.getit_gallery_view)  # index 1: Gallery

        getit_table_row.addWidget(self.getit_view_stack, 1)
        getit_table_row.addWidget(getit_right_widget)
        getit_table_container = QWidget()
        getit_table_container.setLayout(getit_table_row)
        self.getit_form.addRow(getit_table_container)

        # --- Detail panel ---
        getit_detail_outer = QHBoxLayout()
        getit_detail_form = QFormLayout()
        getit_detail_form.setContentsMargins(0, 0, 0, 0)

        self.getit_detail_title  = QLabel("")
        self.getit_detail_author = QLabel("")
        self.getit_detail_size   = QLabel("")
        self.getit_detail_date   = QLabel("")
        self.getit_detail_hits   = QLabel("")
        self.getit_detail_url    = QLabel("")
        self.getit_detail_url.setOpenExternalLinks(True)
        self.getit_detail_desc   = QLabel("")
        self.getit_detail_desc.setWordWrap(True)

        # getit_detail_form.addRow("Title:",       self.getit_detail_title)
        # getit_detail_form.addRow("Author:",      self.getit_detail_author)
        # getit_detail_form.addRow("Size:",        self.getit_detail_size)
        # getit_detail_form.addRow("Date:",        self.getit_detail_date)
        # getit_detail_form.addRow("Hits:",        self.getit_detail_hits)
        # getit_detail_form.addRow("URL:",         self.getit_detail_url)
        # getit_detail_form.addRow("Description:", self.getit_detail_desc)

        getit_detail_widget = QWidget()
        getit_detail_widget.setLayout(getit_detail_form)
        getit_detail_outer.addWidget(getit_detail_widget, 1)

        getit_detail_container = QWidget()
        getit_detail_container.setLayout(getit_detail_outer)
        self.getit_form.addRow(getit_detail_container)



        # --- MOTD ---

        self.getit_motd_text = QLabel("")
        self.getit_motd_text.setWordWrap(True)
        self.getit_motd_text.setStyleSheet("color: #888; font-style: italic;")
        self.getit_form.addRow(self.getit_motd_text)

        # Internal state
        self._getit_current_page = 1
        self._getit_total_pages  = 1
        self._getit_last_query   = ""
        self._getit_selected_id  = ""
        self._getit_selected_link = ""

        self._getit_motd_loaded = False
        self._getit_motd_loading = False
        self._getit_search_loading = False
        # Generation token: bumped on every new search/latest/random so an
        # in-flight request can be superseded (its stale result discarded)
        # instead of blocking the new request until it finishes.
        self._getit_search_gen = 0
        self._getit_last_entries = []  # cached page entries for gallery refresh
        self._getit_ac_titles: list = []   # autocomplete title cache (loaded once)
        self._getit_ac_loading = False     # guard against duplicate fetch

        # ---- Internal helpers ----

        def getit_set_status(msg: str):
            self.getit_status_label.setText(msg)

        def getit_populate_results(entries, page, total_pages):
            self._getit_current_page = page
            self._getit_total_pages  = total_pages
            self.getit_page_label.setText(str(page))
            self.getit_prev_button.setEnabled(page > 1)
            self.getit_next_button.setEnabled(page < total_pages)

            self.getit_results_table.setRowCount(0)
            for e in entries:
                row = self.getit_results_table.rowCount()
                self.getit_results_table.insertRow(row)
                self.getit_results_table.setItem(row, 0, QTableWidgetItem(e["id"]))
                self.getit_results_table.setItem(row, 1, QTableWidgetItem(e["title"]))
                self.getit_results_table.setItem(row, 2, QTableWidgetItem(e["author"]))
                self.getit_results_table.setItem(row, 3, QTableWidgetItem(e["size"]))
            self._getit_last_entries = list(entries)
            self.getit_gallery_view.populate(entries)
            self.getit_gallery_view.select_entry(
                lambda _e, _sel=self._getit_selected_id: bool(_sel) and _e.get("id") == _sel
            )
            try:
                _aio = getattr(self, "_allinone_repopulate", None)
                if _aio is not None:
                    _aio()
            except Exception:
                pass

        def getit_clear_detail():
            self.getit_detail_title.setText("")
            self.getit_detail_author.setText("")
            self.getit_detail_size.setText("")
            self.getit_detail_date.setText("")
            self.getit_detail_hits.setText("")
            self.getit_detail_url.setText("")
            self.getit_detail_desc.setText("")
            self.getit_download_button.setEnabled(False)
            self._getit_selected_id   = ""
            self._getit_selected_link = ""

        def getit_populate_detail(detail: dict):
            self.getit_detail_title.setText(detail.get("TITL", ""))
            self.getit_detail_author.setText(detail.get("AUTH", ""))
            self.getit_detail_size.setText(detail.get("FSIZ", ""))
            self.getit_detail_date.setText(detail.get("DATE", ""))
            self.getit_detail_hits.setText(detail.get("HITS", ""))
            url_val = detail.get("URL", "")
            self.getit_detail_url.setText(f'<a href="{url_val}">{url_val}</a>' if url_val else "")
            self.getit_detail_desc.setText(detail.get("DESC", ""))
            link = detail.get("LINK", "")
            self._getit_selected_link = link
            _has_id = bool(self._getit_selected_id)
            _sd_ok  = bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content)
            self.getit_download_button.setEnabled(_has_id)
            self.getit_send_sd_button.setEnabled(_has_id and _sd_ok)
            self.getit_send_ns_button.setEnabled(_has_id)

        # ---- Background search task ----

        def getit_run_search(query: str, page: int, on_complete=None):
            # Supersede any in-flight GetIt request: bump the generation token
            # so the previous request's result/error is discarded when it
            # finally arrives, and start this one immediately.
            self._getit_search_gen += 1
            _gen = self._getit_search_gen
            self._getit_last_query = query
            self._getit_search_loading = True
            getit_set_status("Searching…")
            self.getit_search_button.setEnabled(False)
            self.getit_latest_button.setEnabled(False)

            def _search_fn():
                offset = (page - 1) * GETIT_PAGE_SIZE
                if query:
                    path = f"/f?s={urllib.parse.quote(query)}"
                else:
                    # Empty-search path (/f?s=) is the only endpoint that
                    # supports offset-based pagination; bare /f ignores ?o=.
                    path = "/f?s="
                if offset > 0:
                    path += f"&o={offset}"
                text = getit_fetch(path)
                entries, total, pg, total_pages = getit_parse_file_list(text)
                return (entries, total, total_pages)

            def _on_result(data):
                if _gen != self._getit_search_gen:
                    return  # superseded by a newer search
                self._getit_search_loading = False
                entries, total, total_pages = data[0], data[1], data[2] or 1
                # The GetIt /f endpoint reports only the item count on the
                # current page as "total", never the full catalogue size, so
                # total_pages always computes to 1.
                # - For no-search browsing: always allow Next as long as we
                #   got any results (the endpoint doesn't expose a catalogue
                #   total, so we optimistically enable Next and let the next
                #   fetch return empty to signal the real end).
                # - For search queries: only allow Next when a full page was
                #   returned (the search endpoint does return a reliable total
                #   when results span multiple pages).
                if total_pages <= page and len(entries) > 0:
                    if not query or len(entries) >= GETIT_PAGE_SIZE:
                        total_pages = page + 1
                self._getit_total_pages = total_pages
                getit_populate_results(entries, page, total_pages)
                getit_set_status(f"{total} result(s)  |  page {page}/{total_pages}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)
                if on_complete:
                    on_complete()

            def _on_error(err):
                if _gen != self._getit_search_gen:
                    return  # superseded by a newer search
                self._getit_search_loading = False
                getit_set_status(f"Error: {err[1]}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)
                if on_complete:
                    on_complete()

            self._getit_search_thread = getit_run_in_thread(_search_fn, _on_result, _on_error)

        def _show_page(page: int):
            """Navigate to a page by re-running the search with the new page number."""
            getit_run_search(self._getit_last_query, page)

        def getit_on_search():
            getit_clear_detail()
            q = self.getit_search_input.text().strip()
            if q and len(q) < SEARCH_MIN_CHARS:
                return
            # Suppress the autocomplete suggestions popup once a search is
            # submitted; it stays hidden until the user types again.
            self._getit_ac_block = True
            try:
                self._getit_ac_timer.stop()
            except Exception:
                pass
            try:
                self._getit_completer.popup().hide()
            except Exception:
                pass
            if q:
                _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                def _getit_done():
                    _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                    _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, self.getit_results_table.rowCount())
                getit_run_search(q, 1, _getit_done)
            else:
                getit_run_search(q, 1)
            if _multi_search_enabled() and q:
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    self.zxdb_search_input.setText(q)
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    self.zxart_search_input.setText(q)
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _cross_search_zxdb(q)
                _cross_search_zxart(q)

        def getit_on_latest(on_complete=None):
            getit_clear_detail()
            self.getit_search_input.clear()
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            def _getit_latest_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, self.getit_results_table.rowCount())
                if on_complete:
                    on_complete()
            getit_run_search("", 1, _getit_latest_done)

        def getit_on_random(on_complete=None):
            import random as _random
            getit_clear_detail()
            self.getit_search_input.clear()
            self._getit_last_query = ""
            # Supersede any in-flight GetIt request.
            self._getit_search_gen += 1
            _gen = self._getit_search_gen
            self._getit_search_loading = True
            getit_set_status("Picking random GetIt entries…")
            self.getit_search_button.setEnabled(False)
            self.getit_latest_button.setEnabled(False)
            self.getit_random_button.setEnabled(False)
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)

            def _fn():
                # Probe the catalogue to find the number of pages, then load
                # a random one.  GetIt has no random endpoint, so we sample
                # client-side by page.  Use /f?s= (empty search) because bare
                # /f ignores offset params and doesn't return a catalogue total.
                text = getit_fetch("/f?s=")
                _entries, _total, _pg, total_pages = getit_parse_file_list(text)
                tp = max(1, (_total + GETIT_PAGE_SIZE - 1) // GETIT_PAGE_SIZE) if _total else 1
                page = _random.randint(1, tp)
                path = f"/f?s=&o={(page - 1) * GETIT_PAGE_SIZE}" if page > 1 else "/f?s="
                text2 = getit_fetch(path)
                entries, total, _pg2, tp2 = getit_parse_file_list(text2)
                tp2 = max(1, (_total + GETIT_PAGE_SIZE - 1) // GETIT_PAGE_SIZE) if _total else tp
                # Shuffle the page entries so consecutive random clicks differ
                # even when the random page repeats.
                _random.shuffle(entries)
                return (entries, total, page, tp2)

            def _on_ok(data):
                if _gen != self._getit_search_gen:
                    return  # superseded by a newer search
                entries, total, page, total_pages = data
                self._getit_search_loading = False
                getit_populate_results(entries, page, total_pages)
                getit_set_status(
                    f"{len(entries)} random entry(ies)  |  page {page}/{total_pages}"
                )
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)
                self.getit_random_button.setEnabled(True)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, self.getit_results_table.rowCount())
                if on_complete:
                    on_complete()

            def _on_err(err):
                if _gen != self._getit_search_gen:
                    return  # superseded by a newer search
                self._getit_search_loading = False
                getit_set_status(f"Error: {err[1]}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)
                self.getit_random_button.setEnabled(True)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, self.getit_results_table.rowCount())
                if on_complete:
                    on_complete()

            self._getit_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def getit_on_prev():
            getit_run_search(self._getit_last_query, max(1, self._getit_current_page - 1))

        def getit_on_next():
            getit_run_search(self._getit_last_query, min(self._getit_total_pages, self._getit_current_page + 1))

        self.getit_search_button.clicked.connect(getit_on_search)
        self.getit_latest_button.clicked.connect(getit_on_latest)
        self.getit_random_button.clicked.connect(getit_on_random)
        self.getit_search_input.returnPressed.connect(getit_on_search)
        self.getit_prev_button.clicked.connect(getit_on_prev)
        self.getit_next_button.clicked.connect(getit_on_next)

        # ---- GetIt autocomplete ----

        self._getit_ac_model = QStringListModel(self)
        _getit_completer = QCompleter(self._getit_ac_model, self)
        _getit_completer.setCompletionMode(QCompleter.PopupCompletion)
        _getit_completer.setCaseSensitivity(Qt.CaseInsensitive)
        _getit_completer.setFilterMode(Qt.MatchStartsWith)
        #_ensure_completer_is_movable(_getit_completer)
        # Ensure the popup follows the main window on Windows
        popup = _getit_completer.popup()
        if popup is not None:
            popup.setParent(self)
            popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.Window)
            popup.setAttribute(Qt.WA_ShowWithoutActivating)
            
        self._getit_completer = _getit_completer
        self.getit_search_input.setCompleter(_getit_completer)

        def _getit_safe_show_popup(q: str):
            """Show the GetIt completer popup without calling QCompleter.complete()."""
            try:
                if not self._search_autocomplete_on():
                    return
                if getattr(self, "_getit_ac_block", False):
                    return
                if not self.getit_search_input.hasFocus():
                    return
                if self.getit_search_input.text().strip() != q:
                    return
                if self._getit_ac_model.rowCount() == 0:
                    return
                _getit_completer.setCompletionPrefix(q)
                popup = _getit_completer.popup()
                if popup is None:
                    return
                # QCompleter's popup is a Qt::Popup window which on Windows
                # performs an implicit keyboard+mouse grab, stealing focus
                # from the line edit no matter what attributes we set.  Re-
                # parent it as a Qt::Tool window with WindowDoesNotAcceptFocus
                # so the OS never routes key events to it: the user can keep
                # typing while the suggestion list stays visible.
                try:
                    popup.setParent(self.getit_search_input.window(),
                                    Qt.Tool
                                    | Qt.FramelessWindowHint
                                    | Qt.WindowStaysOnTopHint
                                    | Qt.WindowDoesNotAcceptFocus)
                    popup.setFocusPolicy(Qt.NoFocus)
                    popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
                except Exception:
                    pass
                le = self.getit_search_input
                rect = le.rect()
                pos = le.mapToGlobal(rect.bottomLeft())
                popup.setMinimumWidth(le.width())
                popup.move(pos)
                popup.resize(le.width(), _popup_height_for(popup, self._getit_ac_model.rowCount()))
                popup.show()
            except RuntimeError:
                pass
            except Exception:
                pass

        def _getit_ac_update_model(text: str):
            """Filter the cached title list (off the UI thread) to those
            starting with *text* and update the completer model.  The actual
            filter+sort runs on a worker thread so typing remains responsive
            even when the cached catalog grows to thousands of entries.  A
            generation token is used to discard stale results that arrive
            after the user has typed more characters."""
            if not text:
                self._getit_ac_model.setStringList([])
                return
            self._getit_ac_filter_gen = getattr(self, "_getit_ac_filter_gen", 0) + 1
            gen = self._getit_ac_filter_gen
            # Snapshot the cache so the worker doesn't touch shared state.
            titles_snapshot = list(self._getit_ac_titles or [])
            tl = text.lower()

            def _fn():
                matches = sorted(
                    (t for t in titles_snapshot if t.lower().startswith(tl)),
                    key=str.lower,
                )
                return (gen, text, matches[:80])

            def _on_ok(result):
                rgen, rtext, matches = result
                if rgen != getattr(self, "_getit_ac_filter_gen", -1):
                    return
                try:
                    if self.getit_search_input.text().strip() != rtext:
                        return
                except RuntimeError:
                    return
                self._getit_ac_model.setStringList(matches)
                if matches:
                    QTimer.singleShot(0, lambda q=rtext: _getit_safe_show_popup(q))

            def _on_err(_err):
                pass

            getit_run_in_thread(_fn, _on_ok, _on_err)

        def _getit_ac_populate_cache(titles: list):
            """Called on the main thread once the full-catalog fetch completes."""
            self._getit_ac_titles = titles
            # Update the model for whatever text is already in the box.
            _getit_ac_update_model(self.getit_search_input.text().strip())

        def _getit_ac_fetch():
            """Background worker: fetch all titles from the GetIt catalog once."""
            results = []
            # Walk pages until we run out of entries.
            offset = 0
            while True:
                path = "/f?s=" if offset == 0 else f"/f?s=&o={offset}"
                try:
                    raw = getit_fetch(path)
                    entries, _total, _pg, _tp = getit_parse_file_list(raw)
                except Exception:
                    break
                if not entries:
                    break
                results.extend(e["title"] for e in entries if e.get("title"))
                if len(entries) < GETIT_PAGE_SIZE:
                    break
                offset += GETIT_PAGE_SIZE
            return results

        def _getit_ac_start_fetch():
            """Kick off a one-time background fetch of all GetIt titles."""
            if self._getit_ac_loading or self._getit_ac_titles:
                return
            self._getit_ac_loading = True
            self._ac_anim_start(self.getit_search_input)

            def _on_ok(titles):
                self._getit_ac_loading = False
                self._ac_anim_stop(self.getit_search_input)
                _getit_ac_populate_cache(titles)
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("getit", "")
                    except Exception:
                        pass

            def _on_err(_err):
                self._getit_ac_loading = False
                self._ac_anim_stop(self.getit_search_input)
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("getit", "")
                    except Exception:
                        pass

            self._getit_ac_thread = getit_run_in_thread(
                _getit_ac_fetch, _on_ok, _on_err
            )

        # Expose the starter so the AllInOne pane can piggy-back on the
        # shared GetIt title cache and animate its own placeholder.
        self._getit_ac_start_fetch = _getit_ac_start_fetch

        # Debounce typing so we don't dispatch a worker thread on every
        # keystroke — pressing two letters in quick succession would
        # otherwise queue two filter jobs.
        _getit_ac_timer = QTimer(self)
        _getit_ac_timer.setSingleShot(True)
        _getit_ac_timer.setInterval(150)
        self._getit_ac_timer = _getit_ac_timer

        def _getit_ac_trigger():
            if not _search_autocomplete_on():
                self._getit_ac_model.setStringList([])
                return
            text = self.getit_search_input.text().strip()
            if not text:
                self._getit_ac_model.setStringList([])
                return
            if not self._getit_ac_titles:
                _getit_ac_start_fetch()
                return
            _getit_ac_update_model(text)

        _getit_ac_timer.timeout.connect(_getit_ac_trigger)

        def _getit_ac_on_text_changed(_text: str):
            # If the change was caused by selecting an item from the popup,
            # don't re-open the popup — that would re-steal focus and trap
            # the user (they couldn't even press Backspace afterwards).
            if getattr(self, "_getit_ac_suppress", False):
                self._getit_ac_suppress = False
                return
            # The user is typing again: re-enable autocomplete suggestions
            # that were suppressed after the last search submission.
            self._getit_ac_block = False
            _getit_ac_timer.start()

        self.getit_search_input.textChanged.connect(_getit_ac_on_text_changed)

        def _getit_ac_activated(selected: str):
            try:
                if selected:
                    # Suppress the textChanged-driven popup re-open caused by
                    # setText below.  Also hide any currently-visible popup.
                    self._getit_ac_suppress = True
                    self._getit_ac_timer.stop()
                    try:
                        _getit_completer.popup().hide()
                    except Exception:
                        pass
                    self.getit_search_input.setText(selected)
            except Exception:
                pass
            getit_on_search()

        _getit_completer.activated.connect(_getit_ac_activated)

        def _getit_search_validate(text: str):
            t = text.strip()
            if not t:
                self._getit_search_valid_lbl.setVisible(False)
            elif len(t) < SEARCH_MIN_CHARS:
                self._getit_search_valid_lbl.setText('<font color="red">❌</font>')
                self._getit_search_valid_lbl.setToolTip(f"Searches must be at least {SEARCH_MIN_CHARS} characters long")
                self._getit_search_valid_lbl.setVisible(True)
            else:
                self._getit_search_valid_lbl.setText('<font color="green">✔</font>')
                self._getit_search_valid_lbl.setVisible(True)
        self.getit_search_input.textChanged.connect(_getit_search_validate)

        # ---- Row selection → fetch detail ----

        def getit_on_row_selected():
            rows = self.getit_results_table.selectedItems()
            if not rows:
                return
            row = self.getit_results_table.currentRow()
            entry_id = self.getit_results_table.item(row, 0).text()
            if not entry_id:
                return
            self._getit_selected_id = entry_id
            getit_set_status(f"Loading details for {entry_id}…")
            self.getit_download_button.setEnabled(False)
            self.getit_send_sd_button.setEnabled(False)
            self.getit_send_ns_button.setEnabled(False)
            self.getit_screenshot_label.setText("Loading…")
            self.getit_screenshot_label.setPixmap(QPixmap())

            def _scr_fn(eid=entry_id):
                url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
                tmp = tempfile.NamedTemporaryFile(suffix=".bmp", delete=False)
                tmp.close()
                urllib.request.urlretrieve(url, tmp.name)
                return tmp.name

            def _on_scr_done(path):
                px = QPixmap(path)
                os.unlink(path)
                if px.isNull():
                    self.getit_screenshot_label.setText("No preview")
                else:
                    self.getit_screenshot_label.setPixmap(
                        px.scaled(256, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )

            def _on_scr_error(err):
                self.getit_screenshot_label.setText("No preview")

            self._getit_scr_thread = getit_run_in_thread(_scr_fn, _on_scr_done, _on_scr_error)

            def _detail_fn():
                text   = getit_fetch(f"/nx/{entry_id}/f/")
                return getit_parse_detail(text)

            def _on_detail(d):
                getit_populate_detail(d)
                getit_set_status(f"Details loaded for {entry_id}")

            self._getit_detail_thread = getit_run_in_thread(
                _detail_fn, _on_detail,
                lambda err: getit_set_status(f"Detail error: {err[1]}")
            )

        self.getit_results_table.itemSelectionChanged.connect(getit_on_row_selected)

        def getit_on_gallery_cell(entry):
            eid = entry.get("id") or ""
            if not eid:
                return
            # Try to mirror selection in the table so existing detail logic runs once.
            for r in range(self.getit_results_table.rowCount()):
                item = self.getit_results_table.item(r, 0)
                if item is not None and item.text() == eid:
                    self.getit_results_table.selectRow(r)
                    break
            self.getit_gallery_view.select_entry(lambda _e, _e0=entry: _e is _e0)

        self.getit_gallery_view.cell_clicked.connect(getit_on_gallery_cell)

        def _getit_open_gallery_viewer(entry, make_viewer=None, install=True):
            eid   = entry.get("id") or ""
            title = entry.get("title") or eid
            if not eid:
                return None
            info_rows = [
                ("Title:",    title),
                ("Author:",   entry.get("author", "")),
                ("Category:", entry.get("category", "")),
                ("Size:",     entry.get("size", "")),
            ]
            scr_url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
            # Compute a typed placeholder (same logic as gallery thumbnails)
            # so when the entry has no preview image the full-screen viewer
            # shows e.g. "TAP" / "TZX2TAP" in yellow on dark instead of a
            # blank pane.
            _ph_link  = self._getit_selected_link or ""
            _ph_ref   = _ph_link or title
            _ph_label = zxfmt_label_for_name(_ph_ref) if _ph_ref else "FILE"
            if _ph_label == "FILE":
                _ph_cat = (entry.get("category") or "").upper()
                if _ph_cat:
                    _ph_label = _ph_cat[:6]
            _mk = make_viewer or (lambda **kw: GalleryItemViewer(parent=self, **kw))
            viewer = _mk(
                title=title,
                info_rows=info_rows,
                screenshots=[scr_url],
                extra_fetch_cb=_getit_extra_fetch_url,
                tags=_gallery_extract_tags(entry),
            )
            viewer.set_placeholder(_ph_label, title)
            _fav_entry_getit = {**entry, "_fav_source": "getit"}
            viewer.set_favorite_hooks(_fav_entry_getit, self._fav_is, self._fav_toggle)

            # ── action buttons ──────────────────────────────────────────
            default_name = self._getit_selected_link or f"{eid}.bin"
            _safe_title  = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid
            _img_path    = self.right_disk_image_path or ""
            _img_label   = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                            ) if _img_path else ""
            _sd_dest     = f"{_img_path}  →  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base     = _getit_resolve_ns_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest     = os.path.join(_ns_base, _safe_title)
            _sd_ok       = bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content)

            def _dl():
                getit_do_download(eid, default_name)
            def _sd():
                _getit_send_to_image(eid, default_name, title)
            def _ns():
                def _after(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after)

            viewer.set_actions(
                download_cb=_dl, send_sd_cb=_sd, send_ns_cb=_ns,
                sd_enabled=_sd_ok,  sd_tooltip=_sd_dest,
                ns_enabled=True,    ns_tooltip=_ns_dest,
            )
            self._wire_viewer_emulators(viewer)

            # ── push into pane stack ────────────────────────────────────
            if install:
                viewer.install_into_stack(
                    self._getit_stack,
                    close_fn=lambda: self._getit_stack.setCurrentIndex(0),
                )
            return viewer

        self.getit_gallery_view.cell_dbl_clicked.connect(_getit_open_gallery_viewer)

        def _getit_table_on_double_clicked(item):
            row = self.getit_results_table.currentRow()
            id_item = self.getit_results_table.item(row, 0)
            if id_item is None:
                return
            eid = id_item.text()
            if not eid:
                return
            # Prefer the fully populated entry from the cached list
            entry = next((e for e in self._getit_last_entries if e.get("id") == eid), None)
            if entry is None:
                entry = {
                    "id":     eid,
                    "title":  (self.getit_results_table.item(row, 1).text()
                               if self.getit_results_table.item(row, 1) else ""),
                    "author": (self.getit_results_table.item(row, 2).text()
                               if self.getit_results_table.item(row, 2) else ""),
                    "size":   (self.getit_results_table.item(row, 3).text()
                               if self.getit_results_table.item(row, 3) else ""),
                }
            _getit_open_gallery_viewer(entry)

        self.getit_results_table.itemDoubleClicked.connect(_getit_table_on_double_clicked)

        def _getit_apply_view_mode(mode: str, *, persist: bool = True):
            mode = (mode or "table").lower()
            if mode not in ("table", "gallery"):
                mode = "table"
            self._getit_view_mode = mode
            self.getit_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
            _table = (mode == "table")
            if hasattr(self, '_getit_right_widget'):
                self._getit_right_widget.setVisible(_table)
            if hasattr(self, '_getit_preview_label'):
                self._getit_preview_label.setVisible(_table)
            if hasattr(self, '_getit_preview_download_btn'):
                self._getit_preview_download_btn.setVisible(_table)
            if hasattr(self, '_getit_preview_send_sd_btn'):
                self._getit_preview_send_sd_btn.setVisible(_table)
            if hasattr(self, '_getit_preview_send_ns_btn'):
                self._getit_preview_send_ns_btn.setVisible(_table)
            # keep combo in sync without re-triggering
            cb = self.getit_view_combo
            target_idx = 1 if mode == "gallery" else 0
            if cb.currentIndex() != target_idx:
                cb.blockSignals(True)
                cb.setCurrentIndex(target_idx)
                cb.blockSignals(False)
            if persist:
                # sync other panes to the same view mode
                if hasattr(self, '_zxdb_apply_view_mode'):
                    self._zxdb_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxart_apply_view_mode'):
                    self._zxart_apply_view_mode(mode, persist=False)
                if hasattr(self, '_favorites_apply_view_mode'):
                    self._favorites_apply_view_mode(mode, persist=False)
                if hasattr(self, '_allinone_apply_view_mode'):
                    self._allinone_apply_view_mode(mode, persist=False)
                configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
                configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
                configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
                save_configuration_file()

        self._getit_apply_view_mode = _getit_apply_view_mode

        def _on_getit_view_combo_changed(_idx):
            _getit_apply_view_mode(self.getit_view_combo.currentData() or "table")

        self.getit_view_combo.currentIndexChanged.connect(_on_getit_view_combo_changed)
        _getit_apply_view_mode(self._getit_view_mode, persist=False)

        # ---- Download file ----

        def getit_do_download(eid, default_name):
            getit_set_status(f"Preparing download for {eid}…")
            self.getit_download_button.setEnabled(False)

            def _probe_fn():
                """HEAD request to resolve the server-side filename before we ask
                the user where to save, so the dialog shows the correct extension."""
                url = f"{GETIT_BASE_URL}/nx/{eid}/"
                cd, _ = _http_fetch_with_cd_retry(
                    url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=15
                )
                # Parse: attachment; filename=HeadOverHeels.tap
                real_name = ""
                for part in cd.split(";"):
                    part = part.strip()
                    if part.lower().startswith("filename="):
                        real_name = part[len("filename="):].strip().strip('"').strip("'")
                        break
                return real_name or os.path.basename(default_name) or f"{eid}.bin"

            def _on_probe_done(server_filename):
                self.getit_download_button.setEnabled(True)
                getit_set_status("")
                # Show save dialog with the server-provided filename as the default.
                save_path, _ = QFileDialog.getSaveFileName(
                    None, "Save file", server_filename
                )
                if not save_path:
                    return

                # Ensure the save path keeps the correct extension even if the
                # user typed a different name without an extension.
                server_ext = os.path.splitext(server_filename)[1]
                user_ext   = os.path.splitext(save_path)[1]
                if server_ext and not user_ext:
                    save_path = save_path + server_ext

                getit_set_status(f"Downloading {eid}…")
                self.getit_download_button.setEnabled(False)

                def _dl_fn():
                    url = f"{GETIT_BASE_URL}/nx/{eid}/"
                    data = _http_fetch_bytes_with_retry(
                        url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
                    )
                    with open(save_path, "wb") as fh:
                        fh.write(data)
                    return save_path

                def _on_dl_done(p):
                    getit_set_status(f"Saved to {p}")
                    self.getit_download_button.setEnabled(True)

                def _on_dl_error(err):
                    getit_set_status(f"Download error: {err[1]}")
                    self.getit_download_button.setEnabled(True)

                self._getit_dl_thread = getit_run_in_thread(_dl_fn, _on_dl_done, _on_dl_error)

            def _on_probe_error(err):
                # Fall back to the old behaviour if the probe fails.
                self.getit_download_button.setEnabled(True)
                getit_set_status("")
                fallback = os.path.basename(default_name) or f"{eid}.bin"
                save_path, _ = QFileDialog.getSaveFileName(None, "Save file", fallback)
                if not save_path:
                    return
                getit_set_status(f"Downloading {eid}…")
                self.getit_download_button.setEnabled(False)

                def _dl_fn2():
                    url = f"{GETIT_BASE_URL}/nx/{eid}/"
                    data = _http_fetch_bytes_with_retry(
                        url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
                    )
                    with open(save_path, "wb") as fh:
                        fh.write(data)
                    return save_path

                def _on_done2(p):
                    getit_set_status(f"Saved to {p}")
                    self.getit_download_button.setEnabled(True)

                def _on_err2(e2):
                    getit_set_status(f"Download error: {e2[1]}")
                    self.getit_download_button.setEnabled(True)

                self._getit_dl_thread = getit_run_in_thread(_dl_fn2, _on_done2, _on_err2)

            self._getit_probe_thread = getit_run_in_thread(_probe_fn, _on_probe_done, _on_probe_error)

        def getit_on_download():
            if not self._getit_selected_id:
                return
            getit_do_download(
                self._getit_selected_id,
                self._getit_selected_link or f"{self._getit_selected_id}.bin"
            )

        self.getit_download_button.clicked.connect(getit_on_download)

        def _getit_on_send_sd():
            if not self._getit_selected_id:
                return
            eid   = self._getit_selected_id
            title = self.getit_detail_title.text() or eid
            _getit_send_to_image(
                eid,
                self._getit_selected_link or f"{eid}.bin",
                title,
            )

        def _getit_on_send_ns():
            if not self._getit_selected_id:
                return
            eid          = self._getit_selected_id
            title        = self.getit_detail_title.text() or eid
            default_name = self._getit_selected_link or f"{eid}.bin"
            _ns_base     = _getit_resolve_ns_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)
            def _after(_folder):
                QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
            _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after)

        self.getit_send_sd_button.clicked.connect(_getit_on_send_sd)
        self.getit_send_ns_button.clicked.connect(_getit_on_send_ns)

        def _getit_resolve_ns_base_path(configured_path: str) -> str:
            """Return the NextSync root directory for local-copy sends."""
            p = (configured_path or "").strip().rstrip("/\\")
            if p:
                if os.path.isdir(p):
                    return p
                parent = os.path.dirname(p)
                if parent and os.path.isdir(parent):
                    return parent
            return os.path.abspath("downloads")

        def _getit_send_to_image(eid: str, default_name: str, title: str):
            """Download the GetIt entry to a temp file then hdfmonkey-put it into the
            currently loaded disk image at the current browse path."""
            if not right_disk_image_explorer_content:
                getit_set_status("Please load a disk image first (SD Card tab).")
                return
            if not self.right_disk_image_path:
                getit_set_status("No disk image loaded.")
                return

            safe_name = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid
            fname     = os.path.basename(default_name) if default_name else f"{eid}.bin"
            img_dir   = (generate_disk_file_path().rstrip("/") + "/" + safe_name).replace("//", "/")
            img_dest  = (img_dir + "/" + fname).replace("//", "/")
            url       = f"{GETIT_BASE_URL}/nx/{eid}/"
            image_path = self.right_disk_image_path

            getit_set_status(f"Sending {eid} → image:{img_dest}…")

            def _dl_and_put():
                # Resolve the server filename so we use the correct extension.
                _cd, _data = _http_fetch_with_cd_retry(
                    url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
                )
                _real = ""
                for _part in _cd.split(";"):
                    _part = _part.strip()
                    if _part.lower().startswith("filename="):
                        _real = _part[len("filename="):].strip().strip('"').strip("'")
                        break
                _use_fname = _real or fname
                tmp = tempfile.NamedTemporaryFile(suffix="_" + _use_fname, delete=False)
                tmp.close()
                try:
                    with open(tmp.name, "wb") as _fh:
                        _fh.write(_data)
                    # Update dest path with real filename
                    nonlocal img_dest
                    img_dest = (img_dir + "/" + _use_fname).replace("//", "/")
                     # Create the sub-directory in the image (ignore errors — may already exist)
                    execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir], silent=True)
                    # Upload the file into the image
                    result = execute_hdf_monkey("put", image_path,
                                               extra_argv=[tmp.name.replace("\\", "/"), img_dest])
                    if result.returncode != 0:
                        raise RuntimeError(f"hdfmonkey put failed (rc={result.returncode})")
                finally:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
                return img_dest

            def _on_done(dest):
                getit_set_status(f"Sent to image: {dest}")
                self._show_sd_notification(f"Sent to SD card image:\n{dest}")
                # Refresh the disk image table so the new folder appears
                res = execute_hdf_monkey("ls", image_path,
                                         extra_argv=[generate_disk_file_path()])
                if res.returncode == 0:
                    update_disk_manager_widget_table(res.stdout)

            def _on_err(err):
                getit_set_status(f"Send to image failed: {err[1]}")

            getit_run_in_thread(_dl_and_put, _on_done, _on_err)

        def _getit_send_to_ns_folder(eid: str, default_name: str, dest_root: str,
                                     title: str, post_action=None):
            """Download the GetIt entry into dest_root/{sanitized_title}/ on the local
            filesystem (used for NextSync sends)."""
            safe_folder = re.sub(r'[<>:"/\\|?*]', "", title or default_name or eid).strip() or eid
            folder      = os.path.join(dest_root, safe_folder)
            os.makedirs(folder, exist_ok=True)
            fname       = os.path.basename(default_name) if default_name else f"{eid}.bin"
            save_path   = os.path.join(folder, fname or f"{eid}.bin")
            url         = f"{GETIT_BASE_URL}/nx/{eid}/"
            getit_set_status(f"Sending {eid} → {folder}…")

            def _dl_fn():
                cd, data = _http_fetch_with_cd_retry(
                    url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
                )
                real = ""
                for part in cd.split(";"):
                    part = part.strip()
                    if part.lower().startswith("filename="):
                        real = part[len("filename="):].strip().strip('"').strip("'")
                        break
                use_fname = real or fname
                dest = os.path.join(folder, use_fname)
                with open(dest, "wb") as fh:
                    fh.write(data)
                return dest

            def _on_done(p):
                getit_set_status(f"Sent → {p}")
                if post_action:
                    post_action(folder)

            def _on_err(err):
                getit_set_status(f"Send error: {err[1]}")

            getit_run_in_thread(_dl_fn, _on_done, _on_err)

        # ---- Context menu on results table ----

        def getit_on_table_context_menu(pos):
            item = self.getit_results_table.itemAt(pos)
            if item is None:
                return
            row = self.getit_results_table.row(item)
            eid_item   = self.getit_results_table.item(row, 0)
            title_item = self.getit_results_table.item(row, 1)
            if not eid_item:
                return
            eid   = eid_item.text()
            title = title_item.text() if title_item else eid
            default_name = self._getit_selected_link or f"{eid}.bin"

            _safe_title = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid

            # SD card: destination is inside the currently loaded disk image
            _img_path  = self.right_disk_image_path or ""
            _img_label = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                         ) if _img_path else "(no image loaded)"
            _sd_dest   = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"

            # NextSync: destination is a sub-folder inside the NextSync root on the local filesystem
            _ns_base = _getit_resolve_ns_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest = os.path.join(_ns_base, _safe_title)

            menu = QMenu(self.getit_results_table)
            act_dl      = menu.addAction(f'Download \u201c{title}\u201d')
            menu.addSeparator()
            act_send_sd = menu.addAction(f"Send to SD card (image)  →  {_sd_dest}")
            act_send_sd.setEnabled(bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content))
            act_send_ns = menu.addAction(f"Send using NextSync  →  {_ns_dest}")
            chosen = menu.exec(self.getit_results_table.viewport().mapToGlobal(pos))
            if chosen is None:
                return
            self.getit_results_table.selectRow(row)
            if chosen is act_dl:
                getit_do_download(eid, default_name)
            elif chosen is act_send_sd:
                _getit_send_to_image(eid, default_name, title)
            elif chosen is act_send_ns:
                def _after_ns_dl_gi(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after_ns_dl_gi)

        self.getit_results_table.setContextMenuPolicy
        self.getit_results_table.customContextMenuRequested.connect(getit_on_table_context_menu)

        # ---- MOTD fetch ----

        def getit_fetch_motd():
            if self._getit_motd_loaded or self._getit_motd_loading:
                return
            self._getit_motd_loading = True

            def _motd_fn():
                return getit_fetch("/motd2.txt").strip()

            def _on_motd(t):
                self._getit_motd_loading = False
                self._getit_motd_loaded = True
                self.getit_motd_text.setText(t)

            def _on_motd_error(err):
                self._getit_motd_loading = False
                self.getit_motd_text.setText(f"(MOTD unavailable: {err[1]})")

            self._getit_motd_thread = getit_run_in_thread(_motd_fn, _on_motd, _on_motd_error)

        # Store for on_tab_changed wiring below
        self._getit_fetch_motd = getit_fetch_motd
        self._getit_on_latest  = getit_on_latest

        getit_container = QWidget()
        getit_container.setLayout(self.getit_form)
        getit_container.setAutoFillBackground(False)
        getit_container.setAttribute(Qt.WA_TranslucentBackground)

        # Wrap in scroll area here so the stack owns the scroll area, not the bare container
        getit_scroll = QScrollArea()
        getit_scroll.setWidget(getit_container)
        getit_scroll.setWidgetResizable(True)
        getit_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        getit_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        getit_scroll.setAutoFillBackground(False)
        getit_scroll.setAttribute(Qt.WA_TranslucentBackground)
        getit_scroll.viewport().setAutoFillBackground(False)
        getit_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

        # Compose a fixed search/button header above the scrollable results so
        # the vertical scroller only covers the content area (like the Unite!
        # tab), instead of spanning the whole tab including the button bar.
        getit_normal_widget = QWidget()
        getit_normal_widget.setAutoFillBackground(False)
        getit_normal_widget.setAttribute(Qt.WA_TranslucentBackground)
        getit_normal_layout = QVBoxLayout(getit_normal_widget)
        getit_normal_layout.setContentsMargins(0, 0, 0, 0)
        getit_normal_layout.setSpacing(0)
        getit_normal_layout.addWidget(self._getit_search_widget, 0)
        getit_normal_layout.addWidget(getit_scroll, 1)

        # ---- Fullscreen preview overlay ----
        self._getit_fullscreen_pixmap = None

        getit_overlay = QWidget()
        getit_overlay.setStyleSheet("background: #000;")
        getit_overlay_layout = QVBoxLayout(getit_overlay)
        getit_overlay_layout.setContentsMargins(0, 0, 0, 0)
        getit_overlay_layout.setSpacing(0)

        getit_close_btn = QToolButton()
        getit_close_btn.setText("✕")
        getit_close_btn.setStyleSheet(
            "QToolButton { color: white; background: #333; border: none; font-size: 18px; padding: 4px 8px; }"
            "QToolButton:hover { background: #c00; }"
        )
        getit_close_bar = QHBoxLayout()
        getit_close_bar.setContentsMargins(4, 4, 4, 0)
        getit_close_bar.addWidget(getit_close_btn, 0)
        getit_close_bar.addStretch()
        getit_close_bar_widget = QWidget()
        getit_close_bar_widget.setLayout(getit_close_bar)
        getit_overlay_layout.addWidget(getit_close_bar_widget, 0)

        self.getit_fullscreen_label = QLabel()
        self.getit_fullscreen_label.setAlignment(Qt.AlignCenter)
        self.getit_fullscreen_label.setStyleSheet("background: #000;")
        self.getit_fullscreen_label.setCursor(Qt.PointingHandCursor)
        getit_overlay_layout.addWidget(self.getit_fullscreen_label, 1)

        self._getit_stack = QStackedWidget()
        self._getit_stack.setAutoFillBackground(False)
        self._getit_stack.setAttribute(Qt.WA_TranslucentBackground)
        self._getit_stack.addWidget(getit_normal_widget)   # index 0 – normal view
        self._getit_stack.addWidget(getit_overlay)  # index 1 – fullscreen preview
        self._getit_stack.setCurrentIndex(0)

        def _getit_show_fullscreen():
            px = self.getit_screenshot_label.pixmap()
            if px is None or px.isNull():
                return
            self._getit_fullscreen_pixmap = px
            self._getit_stack.setCurrentIndex(1)
            _getit_resize_fullscreen()

        def _getit_hide_fullscreen():
            self._getit_stack.setCurrentIndex(0)
        self._hide_fullscreen_getit = _getit_hide_fullscreen

        def _getit_resize_fullscreen():
            px = self._getit_fullscreen_pixmap
            if px and not px.isNull():
                sz = self.getit_fullscreen_label.size()
                self.getit_fullscreen_label.setPixmap(
                    px.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )

        getit_close_btn.clicked.connect(_getit_hide_fullscreen)
        self.getit_fullscreen_label.mousePressEvent = lambda e: _getit_hide_fullscreen()

        # Intercept double-click on the thumbnail via an event filter
        class _DblClickFilter(QtCore.QObject):
            def __init__(self, callback):
                super().__init__()
                self._cb = callback
            def eventFilter(self, obj, event):
                if event.type() == QtCore.QEvent.MouseButtonDblClick:
                    self._cb()
                    return True
                return False

        self._getit_dbl_filter = _DblClickFilter(_getit_show_fullscreen)
        self.getit_screenshot_label.installEventFilter(self._getit_dbl_filter)
        self.getit_screenshot_label.setCursor(Qt.PointingHandCursor)

        # -----------------------------------------------------------------------
        # ZXDB UI construction (ZXInfo API v3)
        # -----------------------------------------------------------------------

        self.zxdb_form = QFormLayout()
        self.zxdb_form.setContentsMargins(4, 4, 4, 4)

        # --- Search row ---
        zxdb_search_row = QHBoxLayout()
        self.zxdb_search_input = QLineEdit()
        self.zxdb_search_input.setPlaceholderText("Search ZXDB games... (leave empty for random selection)")
        self.zxdb_search_input.setMinimumWidth(280)
        zxdb_search_row.addWidget(self.zxdb_search_input)

        self._zxdb_search_valid_lbl = QLabel()
        self._zxdb_search_valid_lbl.setVisible(False)
        zxdb_search_row.addWidget(self._zxdb_search_valid_lbl)

        self.zxdb_search_button = QPushButton("Search")
        zxdb_search_row.addWidget(self.zxdb_search_button)

        self.zxdb_mode_combo = QComboBox()
        # (display label, internal mode key)
        for label, key in (
            ("Games",       "games"),
            ("By letter",   "byletter"),
            ("Magazines",   "magazines"),
            ("By author",   "author"),
            ("Suggestions", "suggest"),
        ):
            self.zxdb_mode_combo.addItem(label, key)
        self.zxdb_mode_combo.setCurrentIndex(0)
        self.zxdb_mode_combo.setToolTip("Search mode")
        zxdb_search_row.addWidget(self.zxdb_mode_combo)

        self.zxdb_letter_combo = QComboBox()
        for _lbl in ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
            self.zxdb_letter_combo.addItem(_lbl, _lbl.lower())
        self.zxdb_letter_combo.setToolTip("Pick a letter")
        self.zxdb_letter_combo.setVisible(False)
        zxdb_search_row.addWidget(self.zxdb_letter_combo)

        self.zxdb_latest_button = QPushButton("Latest")
        self.zxdb_latest_button.setToolTip("Show the most recently added/updated ZXDB games.")
        zxdb_search_row.addWidget(self.zxdb_latest_button)

        self.zxdb_random_button = QPushButton("Random")
        zxdb_search_row.addWidget(self.zxdb_random_button)

        zxdb_search_row.addWidget(QLabel("Page:"))
        self.zxdb_page_label = QLabel("1")
        self.zxdb_page_label.setMinimumWidth(24)
        zxdb_search_row.addWidget(self.zxdb_page_label)

        self.zxdb_prev_button = QPushButton("< Prev")
        self.zxdb_prev_button.setEnabled(False)
        zxdb_search_row.addWidget(self.zxdb_prev_button)

        self.zxdb_next_button = QPushButton("Next >")
        self.zxdb_next_button.setEnabled(False)
        zxdb_search_row.addWidget(self.zxdb_next_button)

        zxdb_search_row.addWidget(QLabel("View:"))
        self.zxdb_view_combo = QComboBox()
        self.zxdb_view_combo.addItem("Table",   "table")
        self.zxdb_view_combo.addItem("Gallery", "gallery")
        self.zxdb_view_combo.setToolTip(
            "Switch between the classic table view and the picture (gallery) view.\n"
            "Persisted across sessions in the config file."
        )
        zxdb_search_row.addWidget(self.zxdb_view_combo)

        self.zxdb_status_label = QLabel("")
        self.zxdb_status_label.setCursor(Qt.ArrowCursor)
        self._zxdb_status_open_path = None
        def _zxdb_status_mouse_press(ev):
            if ev.button() == Qt.LeftButton and self._zxdb_status_open_path:
                p = self._zxdb_status_open_path
                if os.path.isfile(p):
                    p = os.path.dirname(p)
                # Ensure the folder exists before trying to open it
                try:
                    os.makedirs(p, exist_ok=True)
                except OSError:
                    pass
                if not os.path.isdir(p):
                    return
                if sys.platform == "win32":
                    os.startfile(p)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", p])
                else:
                    subprocess.Popen(["xdg-open", p])
        self.zxdb_status_label.mousePressEvent = _zxdb_status_mouse_press
        zxdb_search_row.addWidget(self.zxdb_status_label, 1)

        zxdb_search_widget = QWidget()
        zxdb_search_widget.setLayout(zxdb_search_row)
        # Keep the search/button bar fixed above the scroll area (see the
        # _zxdb_stack assembly) so the vertical scroller only covers the
        # results/details area, matching the Unite! tab.
        self._zxdb_search_widget = zxdb_search_widget

        # --- Results table + screenshot/download column ---
        self.zxdb_results_table = QTableWidget(0, 6)
        self.zxdb_results_table.setHorizontalHeaderLabels(
            ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"]
        )
        self.zxdb_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.zxdb_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.zxdb_results_table.horizontalHeader().setStretchLastSection(True)
        self.zxdb_results_table.setMinimumHeight(220)
        self.zxdb_results_table.setColumnWidth(0, 80)
        self.zxdb_results_table.setColumnWidth(1, 280)
        self.zxdb_results_table.setColumnWidth(2, 60)
        self.zxdb_results_table.setColumnWidth(3, 180)
        self.zxdb_results_table.setColumnWidth(4, 120)

        self.zxdb_screenshot_label = QLabel()
        self.zxdb_screenshot_label.setFixedSize(256, 192)
        self.zxdb_screenshot_label.setAlignment(Qt.AlignCenter)
        self.zxdb_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
        self.zxdb_screenshot_label.setText("No preview")
        self.zxdb_screenshot_label.setToolTip("Double-click to enlarge")

        # Wrap label in a container so overlay QToolButtons receive clicks
        # (QLabel consumes mouse events and blocks children from receiving them)
        zxdb_preview_container = QWidget()
        zxdb_preview_container.setFixedSize(256, 192)
        self.zxdb_screenshot_label.setParent(zxdb_preview_container)
        self.zxdb_screenshot_label.move(0, 0)

        _nav_btn_style = (
            "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
            " font-size: 20px; font-weight: bold; padding: 2px 6px; }"
            "QToolButton:hover { background: rgba(0,0,0,210); }"
        )
        self.zxdb_prev_shot_btn = QToolButton(zxdb_preview_container)
        self.zxdb_prev_shot_btn.setText("<")
        self.zxdb_prev_shot_btn.setStyleSheet(_nav_btn_style)
        self.zxdb_prev_shot_btn.setVisible(False)
        self.zxdb_prev_shot_btn.raise_()

        self.zxdb_next_shot_btn = QToolButton(zxdb_preview_container)
        self.zxdb_next_shot_btn.setText(">")
        self.zxdb_next_shot_btn.setStyleSheet(_nav_btn_style)
        self.zxdb_next_shot_btn.setVisible(False)
        self.zxdb_next_shot_btn.raise_()

        def _zxdb_reposition_shot_btns():
            h = zxdb_preview_container.height()
            bh = self.zxdb_prev_shot_btn.sizeHint().height()
            by = (h - bh) // 2
            self.zxdb_prev_shot_btn.move(2, by)
            bw = self.zxdb_next_shot_btn.sizeHint().width()
            self.zxdb_next_shot_btn.move(zxdb_preview_container.width() - bw - 2, by)

        _zxdb_reposition_shot_btns()

        self.zxdb_download_button = QPushButton("Download File")
        self.zxdb_download_button.setEnabled(False)

        zxdb_right_col = QVBoxLayout()
        _zxdb_link_label = QLabel('<a href="https://zxinfo.dk/">https://zxinfo.dk/</a>')
        _zxdb_link_label.setOpenExternalLinks(True)
        _zxdb_link_label.setTextFormat(Qt.RichText)
        _zxdb_link_label.setAlignment(Qt.AlignCenter)
        zxdb_right_col.addWidget(_zxdb_link_label)
        # Visibility is controlled by _zxdb_apply_view_mode (shown in Table, hidden in Gallery)
        zxdb_preview_container.setVisible(False)
        self.zxdb_download_button.setVisible(False)
        zxdb_right_col.addWidget(zxdb_preview_container)
        zxdb_right_col.addWidget(self.zxdb_download_button)
        self._zxdb_preview_container = zxdb_preview_container
        self._zxdb_preview_download_btn = self.zxdb_download_button
        zxdb_right_col.addStretch()
        zxdb_right_widget = QWidget()
        zxdb_right_widget.setLayout(zxdb_right_col)

        zxdb_table_row = QHBoxLayout()

        self.zxdb_view_stack = QStackedWidget()
        self.zxdb_view_stack.addWidget(self.zxdb_results_table)  # index 0

        def _zxdb_gallery_title(e):
            return (e.get("title") or e.get("id") or "")[:80]
        def _zxdb_gallery_info(e):
            parts = []
            if e.get("author"):  parts.append(e["author"])
            if e.get("year"):    parts.append(str(e["year"]))
            if e.get("machine"): parts.append(e["machine"])
            if e.get("genre"):   parts.append(e["genre"])
            return " · ".join(parts)

        def _zxdb_tooltip_getter(e):
            lines = []
            if e.get("title"):   lines.append(f"Title: {e['title']}")
            if e.get("year"):    lines.append(f"Year: {e['year']}")
            if e.get("author"):  lines.append(f"Author: {e['author']}")
            if e.get("machine"): lines.append(f"Machine: {e['machine']}")
            if e.get("genre"):   lines.append(f"Genre: {e['genre']}")
            return _build_tooltip_text(lines)

        def _zxdb_thumb_fetch(entry, set_pixmap, set_screenshots):
            eid = entry.get("id") or ""
            if not eid:
                return
            def _fn():
                payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                detail  = zxdb_parse_game_detail(payload)
                shots = detail.get("screenshots") or []
                if not shots and detail.get("screenshot_url"):
                    shots = [{"url": detail["screenshot_url"], "type": ""}]
                # ZXDB's parser already restricts screenshots to image
                # assets, so we trust the upstream classification here
                # (extensions on the CDN URLs are not always one of the
                # canonical image suffixes we know about).
                urls = []
                for s in shots:
                    if not isinstance(s, dict):
                        continue
                    u = s.get("url") or ""
                    if u:
                        urls.append(u)
                # Also collect downloads so we can render a typed
                # placeholder if no real image is available.
                return (urls, detail.get("downloads") or [])
            def _on_ok(res):
                urls, downloads = res
                if urls:
                    set_screenshots(urls)
                    def _img_fn(_u=urls[0]):
                        return (_u, _http_fetch_bytes_with_retry(
                            _u, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=20))
                    def _img_ok(r):
                        u, data = r
                        if zxscr_url_is_scr(u):
                            pm = zxscr_convert_bytes_to_pixmap(
                                data, _zxscr_basename_for_url(u))
                            if pm is not None and not pm.isNull():
                                set_pixmap(pm, u)
                                return
                        px = QPixmap()
                        px.loadFromData(data)
                        if not px.isNull():
                            set_pixmap(px, u)
                    getit_run_in_thread(_img_fn, _img_ok, lambda _e: None)
                    return
                # No real image: render a typed placeholder showing the
                # primary download format (e.g. TAP, POK, PDF) so the cell
                # is still informative instead of a black square.
                label, fname = zxfmt_pick_best_download(downloads)
                title = entry.get("title") or eid
                sub = fname or title
                placeholder_url = f"placeholder://{label}/{sub}"
                set_screenshots([placeholder_url])
                pm = zxfmt_make_placeholder_pixmap(label, sub)
                if not pm.isNull():
                    set_pixmap(pm, placeholder_url)
            getit_run_in_thread(_fn, _on_ok, lambda _e: None)

        def _zxdb_extra_fetch(url, on_pixmap):
            if isinstance(url, str) and url.startswith("placeholder://"):
                rest = url[len("placeholder://"):]
                label, _, sub = rest.partition("/")
                pm = zxfmt_make_placeholder_pixmap(label or "FILE", sub)
                if not pm.isNull():
                    on_pixmap(pm)
                return
            scr_url = zxscr_url_is_scr(url)
            if scr_url:
                base = _zxscr_basename_for_url(url)
                cached = _ZXSCR_PIXMAP_CACHE.get(base)
                if cached is not None and not cached.isNull():
                    on_pixmap(cached)
                    return
            def _fn(_u=url):
                return _http_fetch_bytes_with_retry(
                    _u, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=20)
            def _on_ok(data, _u=url, _scr=scr_url):
                if _scr:
                    pm = zxscr_convert_bytes_to_pixmap(
                        data, _zxscr_basename_for_url(_u))
                    if pm is not None and not pm.isNull():
                        on_pixmap(pm)
                        return
                px = QPixmap()
                px.loadFromData(data)
                if not px.isNull():
                    on_pixmap(px)
            getit_run_in_thread(_fn, _on_ok, lambda _e: None)

        def _zxdb_gallery_context_menu(entry, global_pos):
            eid   = entry.get("id") or ""
            title = entry.get("title") or eid
            kind  = (entry.get("_kind") or "game").lower()
            _safe_title = zxdb_sanitize_folder(title)
            _img_path   = self.right_disk_image_path or ""
            _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                           ) if _img_path else "(no image loaded)"
            _sd_dest    = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base    = _zxdb_resolve_base_path(self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest    = os.path.join(_ns_base, _safe_title)
            menu = QMenu()
            act_download = menu.addAction("Download content")
            act_mlt      = menu.addAction("More like this")
            menu.addSeparator()
            act_send_sd  = menu.addAction(f"Send to SD card (image)  \u2192  {_sd_dest}")
            act_send_sd.setEnabled(bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content))
            act_send_ns  = menu.addAction(f"Send using NextSync  \u2192  {_ns_dest}")
            if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                act_download.setVisible(False)
                act_send_sd.setVisible(False)
                act_send_ns.setVisible(False)
            menu.addSeparator()
            _web_url = zxdb_entry_website_url(eid)
            act_open_web = menu.addAction("Open on website (zxinfo.dk)")
            act_open_web.setEnabled(bool(_web_url))
            action = menu.exec(global_pos)
            if action is None:
                return
            if action is act_open_web:
                if _web_url:
                    try:
                        webbrowser.open(_web_url, new=2)
                    except Exception:
                        pass
                return
            if kind == "magazine":
                # For magazine cells just show the download overlay if detail is loaded
                if action is act_download:
                    if self._zxdb_selected_downloads:
                        zxdb_show_downloads_overlay(self._zxdb_selected_title or title,
                                                    self._zxdb_selected_downloads)
                return
            def _fetch_and_send(dest_root, post_action=None):
                if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                    _zxdb_send_to_path(self._zxdb_selected_title or title,
                                       self._zxdb_selected_downloads, dest_root, post_action)
                    return
                zxdb_set_status(f"Loading {eid}\u2026")
                def _fn():
                    payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok(detail, _dr=dest_root, _pa=post_action):
                    zxdb_populate_detail(detail)
                    dls = detail.get("downloads", []) or []
                    if not dls:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    _zxdb_send_to_path(detail.get("title") or title, dls, _dr, _pa)
                def _on_err(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
            if action is act_download:
                if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                    zxdb_show_downloads_overlay(self._zxdb_selected_title or title,
                                                self._zxdb_selected_downloads)
                    return
                zxdb_set_status(f"Loading {eid}\u2026")
                def _fn_dl():
                    payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok_dl(detail):
                    zxdb_populate_detail(detail)
                    downloads = detail.get("downloads", []) or []
                    if not downloads:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    zxdb_show_downloads_overlay(detail.get("title") or title, downloads)
                def _on_err_dl(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn_dl, _on_ok_dl, _on_err_dl)
            elif action is act_send_sd:
                if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                    _zxdb_send_to_image(self._zxdb_selected_title or title,
                                        self._zxdb_selected_downloads)
                    return
                zxdb_set_status(f"Loading {eid}\u2026")
                def _fn_sd():
                    payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok_sd(detail):
                    zxdb_populate_detail(detail)
                    dls = detail.get("downloads", []) or []
                    if not dls:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    _zxdb_send_to_image(detail.get("title") or title, dls)
                def _on_err_sd(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn_sd, _on_ok_sd, _on_err_sd)
            elif action is act_send_ns:
                def _after_ns_dl(_folder):
                    QTimer.singleShot(0, self._nextsync_start_server_fn)
                _fetch_and_send(_ns_base, _after_ns_dl)
            elif action is act_mlt:
                zxdb_set_status(f"Finding titles similar to '{title}'\u2026")
                def _fn_mlt():
                    payload = zxdb_fetch_json(
                        f"/games/morelikethis/{urllib.parse.quote(eid)}"
                        f"?mode=compact&size={ZXDB_PAGE_SIZE}"
                    )
                    entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                    for e in entries:
                        e["_kind"] = "game"
                    return ("games", entries, total, 1, total_pages)
                def _on_ok_mlt(data):
                    kind2, entries, total, pg, total_pages = data
                    zxdb_populate_results(entries, pg, total_pages, kind2)
                    zxdb_set_status(f"{len(entries)} title(s) similar to '{title}'")
                def _on_err_mlt(err):
                    zxdb_set_status(f"More like this error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn_mlt, _on_ok_mlt, _on_err_mlt)

        self.zxdb_gallery_view = GalleryView(
            rows_per_page_getter=lambda: self._gallery_rows_per_page,
            anim_mode_getter=lambda: self._gallery_anim_mode,
            cols_getter=lambda: self._gallery_cols,
            img_size_getter=lambda: self._gallery_img_size,
            thumb_fetch_cb=_zxdb_thumb_fetch,
            extra_fetch_cb=_zxdb_extra_fetch,
            title_getter=_zxdb_gallery_title,
            info_getter=_zxdb_gallery_info,
            context_menu_cb=_zxdb_gallery_context_menu,
            is_favorite_cb=lambda e: self._fav_is({**e, "_fav_source": "zxdb"}),
            toggle_favorite_cb=lambda e: self._fav_toggle({**e, "_fav_source": "zxdb"}),
            tooltip_getter=_zxdb_tooltip_getter,
        )
        self._fav_fetchers = getattr(self, "_fav_fetchers", {})
        self._fav_fetchers["zxdb"] = {
            "thumb": _zxdb_thumb_fetch,
            "extra": _zxdb_extra_fetch,
            "title": _zxdb_gallery_title,
            "info":  _zxdb_gallery_info,
        }
        self.zxdb_view_stack.addWidget(self.zxdb_gallery_view)  # index 1

        zxdb_table_row.addWidget(self.zxdb_view_stack, 1)
        zxdb_table_row.addWidget(zxdb_right_widget)
        zxdb_table_container = QWidget()
        zxdb_table_container.setLayout(zxdb_table_row)
        self.zxdb_form.addRow(zxdb_table_container)

        # --- Detail panel (rebuilt per kind: game / magazine / suggest) ---
        self._zxdb_detail_layout = QFormLayout()
        self._zxdb_detail_layout.setContentsMargins(0, 0, 0, 0)
        self._zxdb_detail_rows = []   # list of (label_widget, value_widget) pairs

        self._zxdb_detail_widget = QWidget()
        self._zxdb_detail_widget.setLayout(self._zxdb_detail_layout)
        # Detail widget intentionally not added to form; info shown via cell tooltips instead.

        # --- Internal state ---
        self._zxdb_current_page  = 1
        self._zxdb_total_pages   = 1
        self._zxdb_last_query    = ""
        self._zxdb_selected_id   = ""
        self._zxdb_selected_title = ""
        self._zxdb_selected_downloads = []
        self._zxdb_search_loading = False
        # Generation token: see _getit_search_gen for rationale.
        self._zxdb_search_gen = 0
        self._zxdb_loaded_once   = False
        self._zxdb_results_mode  = "games"
        self._zxdb_magazine_issues = []   # issues list of the currently-loaded magazine
        self._zxdb_last_entries = []
        self._zxdb_ac_cache: dict = {}    # letter -> sorted list of titles
        self._zxdb_ac_fetching: set = set()  # letters currently being fetched

        # Slideshow state
        self._zxdb_screenshots = []        # list of dicts {url, type}
        self._zxdb_shot_cache  = {}        # url -> QPixmap
        self._zxdb_shot_index  = 0
        self._zxdb_shot_token  = 0         # invalidates outstanding fetches when row changes
        self._zxdb_slideshow_timer = QTimer(self)
        self._zxdb_slideshow_timer.setInterval(5000)

        # ---- Helpers ----

        def zxdb_set_status(msg: str, open_path: str = None):
            self.zxdb_status_label.setText(msg)
            self._zxdb_status_open_path = open_path
            if open_path:
                self.zxdb_status_label.setStyleSheet("color: #4fc3f7; text-decoration: underline;")
                self.zxdb_status_label.setCursor(Qt.PointingHandCursor)
            else:
                self.zxdb_status_label.setStyleSheet("")
                self.zxdb_status_label.setCursor(Qt.ArrowCursor)

        def _zxdb_clear_detail_rows():
            while self._zxdb_detail_layout.rowCount() > 0:
                self._zxdb_detail_layout.removeRow(0)
            self._zxdb_detail_rows = []

        def _zxdb_add_row(label: str, value: str, *, dim: bool = False, wrap: bool = True, is_html: bool = False):
            lab = QLabel(label)
            val = QLabel()
            import html as _html
            if is_html:
                inner = str(value or "")
            else:
                inner = _html.escape(str(value or "")).replace("\n", "<br>")
            val.setText(
                f'<div style="word-wrap:break-word; word-break:break-all;">{inner}</div>'
            )
            val.setTextFormat(Qt.RichText)
            if wrap:
                val.setWordWrap(True)
                val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)
                val.setMinimumWidth(0)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if dim:
                val.setStyleSheet("color: #888;")
            self._zxdb_detail_layout.addRow(lab, val)
            self._zxdb_detail_rows.append((lab, val))

        def zxdb_clear_detail():
            _zxdb_clear_detail_rows()
            self.zxdb_screenshot_label.setText("No preview")
            self.zxdb_screenshot_label.setPixmap(QPixmap())
            self.zxdb_download_button.setEnabled(False)
            self._zxdb_selected_id = ""
            self._zxdb_selected_title = ""
            self._zxdb_selected_downloads = []
            self._zxdb_slideshow_timer.stop()
            self._zxdb_shot_token += 1
            self._zxdb_screenshots = []
            self._zxdb_shot_cache  = {}
            self._zxdb_shot_index  = 0

        def zxdb_populate_results(entries, page, total_pages, mode="games"):
            self._zxdb_current_page = page or 1
            self._zxdb_total_pages  = total_pages or 1
            self._zxdb_results_mode = mode
            self.zxdb_page_label.setText(str(self._zxdb_current_page))
            self.zxdb_prev_button.setEnabled(self._zxdb_current_page > 1)
            self.zxdb_next_button.setEnabled(self._zxdb_current_page < self._zxdb_total_pages)

            headers = _ZXDB_HEADERS.get(mode, _ZXDB_HEADERS["games"])
            self.zxdb_results_table.setHorizontalHeaderLabels(headers)

            self.zxdb_results_table.setRowCount(0)
            for e in entries:
                row = self.zxdb_results_table.rowCount()
                self.zxdb_results_table.insertRow(row)
                id_item = QTableWidgetItem(e.get("id", ""))
                # Stash the full entry dict on column 0 so row-selection can dispatch
                # detail loading per kind without re-querying the source list.
                id_item.setData(Qt.UserRole, e)
                self.zxdb_results_table.setItem(row, 0, id_item)
                self.zxdb_results_table.setItem(row, 1, QTableWidgetItem(e.get("title", "")))
                self.zxdb_results_table.setItem(row, 2, QTableWidgetItem(e.get("year", "")))
                self.zxdb_results_table.setItem(row, 3, QTableWidgetItem(e.get("author", "")))
                self.zxdb_results_table.setItem(row, 4, QTableWidgetItem(e.get("machine", "")))
                self.zxdb_results_table.setItem(row, 5, QTableWidgetItem(e.get("genre", "")))
            self._zxdb_last_entries = list(entries)
            self.zxdb_gallery_view.populate(entries)
            self.zxdb_gallery_view.select_entry(
                lambda _e, _sel=self._zxdb_selected_id: bool(_sel) and _e.get("id") == _sel
            )
            try:
                _aio = getattr(self, "_allinone_repopulate", None)
                if _aio is not None:
                    _aio()
            except Exception:
                pass

        def zxdb_populate_detail(detail: dict):
            """Game detail (used for games and by-author results)."""
            _zxdb_clear_detail_rows()
            _zxdb_add_row("Title:",       detail.get("title", ""))
            _zxdb_add_row("Year:",        detail.get("year", ""))
            _zxdb_add_row("Authors:",     detail.get("authors", ""))
            _zxdb_add_row("Published by:", detail.get("publishers", ""))
            _zxdb_add_row("Machine:",     detail.get("machine", ""))
            _zxdb_add_row("Genre:",       detail.get("genre", ""))
            _zxdb_add_row(
                "Description:",
                detail.get("description", "") or detail.get("remarks", ""),
                dim=True,
            )

            self._zxdb_selected_downloads = _filter_download_urls(
                detail.get("downloads", []) or []
            )
            self.zxdb_download_button.setEnabled(bool(self._zxdb_selected_downloads))

        def zxdb_populate_magazine_detail(name: str, summary: dict, issues_payload):
            """Render /magazines/{name}/issues result."""
            _zxdb_clear_detail_rows()
            issues = []
            country = summary.get("country") or ""
            language = summary.get("language") or ""
            mtype = summary.get("type") or ""
            publisher = summary.get("publisher") or ""
            if isinstance(issues_payload, dict):
                country   = issues_payload.get("country")   or country
                language  = issues_payload.get("language")  or language
                mtype     = issues_payload.get("type")      or mtype
                publisher = issues_payload.get("publisher") or publisher
                issues    = issues_payload.get("issues") or []
            elif isinstance(issues_payload, list):
                issues = issues_payload

            years = sorted({
                str(i.get("date_year"))
                for i in issues
                if isinstance(i, dict) and i.get("date_year")
            })
            year_range = ""
            if years:
                year_range = years[0] if len(years) == 1 else f"{years[0]} – {years[-1]}"

            _zxdb_add_row("Magazine:",   name)
            _zxdb_add_row("Publisher:",  str(publisher) if publisher else "")
            _zxdb_add_row("Type:",       str(mtype) if mtype else "")
            _zxdb_add_row("Language:",   str(language) if language else "")
            _zxdb_add_row("Country:",    str(country) if country else "")
            _zxdb_add_row("Issues:",     str(len(issues)) if issues else "0")
            _zxdb_add_row("Years:",      year_range, dim=True)

            if issues:
                preview = []
                for i in issues[:6]:
                    if not isinstance(i, dict):
                        continue
                    vol = i.get("volume")
                    num = i.get("number")
                    yr  = i.get("date_year")
                    mo  = i.get("date_month")
                    label = []
                    if vol is not None: label.append(f"V{vol}")
                    if num is not None: label.append(f"#{num}")
                    if yr:              label.append(f"{yr}" + (f"/{mo:02d}" if isinstance(mo, int) else ""))
                    preview.append(" ".join(label) if label else str(i.get("id", "")))
                _zxdb_add_row(
                    "Preview:",
                    ", ".join(preview) + (f" … (+{len(issues) - len(preview)})" if len(issues) > len(preview) else ""),
                    dim=True,
                )

            # No file downloads for magazines (issues carry per-issue files we don't drill into here).
            self._zxdb_selected_downloads = []
            self.zxdb_download_button.setEnabled(False)

        def zxdb_populate_suggest_detail(entry: dict):
            """Render a /suggest/{term} row in the detail pane."""
            _zxdb_clear_detail_rows()
            _zxdb_add_row("Suggestion:", entry.get("title", ""))
            _zxdb_add_row("Type:",       entry.get("_suggest_type", "") or entry.get("machine", ""))
            label = entry.get("author", "")  # we stuffed labeltype here
            if label:
                _zxdb_add_row("Label:", label)
            eid = entry.get("_entry_id", "")
            if eid:
                _zxdb_add_row("Entry ID:", eid, dim=True)
            _zxdb_add_row(
                "Tip:",
                "Switch to Games and search for this title, or pick another suggestion.",
                dim=True,
            )
            self._zxdb_selected_downloads = []
            self.zxdb_download_button.setEnabled(False)

        # ---- Screenshot slideshow ----

        def zxdb_set_pixmap(pm: QPixmap):
            if pm is None or pm.isNull():
                self.zxdb_screenshot_label.setText("No preview")
                self.zxdb_screenshot_label.setPixmap(QPixmap())
                return
            self.zxdb_screenshot_label.setPixmap(
                pm.scaled(
                    self.zxdb_screenshot_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            # If the fullscreen view is showing this pane's preview, refresh it too.
            if self._zxdb_stack.currentIndex() == 1:
                self._zxdb_fullscreen_pixmap = pm
                fs = self.zxdb_fullscreen_label.size()
                self.zxdb_fullscreen_label.setPixmap(
                    pm.scaled(fs, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )

        def zxdb_update_nav_buttons():
            multi = len(self._zxdb_screenshots) > 1
            self.zxdb_prev_shot_btn.setVisible(multi)
            self.zxdb_next_shot_btn.setVisible(multi)
            self.zxdb_fs_prev_btn.setVisible(multi and self._zxdb_stack.currentIndex() == 1)
            self.zxdb_fs_next_btn.setVisible(multi and self._zxdb_stack.currentIndex() == 1)

        def zxdb_show_shot_at(idx: int):
            if not self._zxdb_screenshots:
                return
            idx = idx % len(self._zxdb_screenshots)
            self._zxdb_shot_index = idx
            zxdb_update_nav_buttons()
            url = self._zxdb_screenshots[idx]["url"]
            cached = self._zxdb_shot_cache.get(url)
            if cached is not None:
                zxdb_set_pixmap(cached)
                return

            token = self._zxdb_shot_token

            def _fn():
                return zxdb_fetch_bytes(url)

            def _on_ok(data):
                if token != self._zxdb_shot_token:
                    return  # selection changed; drop result
                pm = QPixmap()
                if pm.loadFromData(data) and not pm.isNull():
                    self._zxdb_shot_cache[url] = pm
                    if self._zxdb_screenshots and self._zxdb_screenshots[self._zxdb_shot_index]["url"] == url:
                        zxdb_set_pixmap(pm)

            def _on_err(_err):
                pass

            getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_slideshow_tick():
            if len(self._zxdb_screenshots) <= 1:
                return
            zxdb_show_shot_at(self._zxdb_shot_index + 1)

        self._zxdb_slideshow_timer.timeout.connect(zxdb_slideshow_tick)

        def _zxdb_nav_prev():
            if len(self._zxdb_screenshots) > 1:
                self._zxdb_slideshow_timer.stop()
                zxdb_show_shot_at(self._zxdb_shot_index - 1)
                self._zxdb_slideshow_timer.start()

        def _zxdb_nav_next():
            if len(self._zxdb_screenshots) > 1:
                self._zxdb_slideshow_timer.stop()
                zxdb_show_shot_at(self._zxdb_shot_index + 1)
                self._zxdb_slideshow_timer.start()

        self.zxdb_prev_shot_btn.clicked.connect(_zxdb_nav_prev)
        self.zxdb_next_shot_btn.clicked.connect(_zxdb_nav_next)

        def zxdb_start_slideshow(screenshots):
            self._zxdb_slideshow_timer.stop()
            self._zxdb_shot_token += 1
            self._zxdb_screenshots = list(screenshots or [])
            self._zxdb_shot_cache  = {}
            self._zxdb_shot_index  = 0
            if not self._zxdb_screenshots:
                self.zxdb_screenshot_label.setText("No preview")
                self.zxdb_screenshot_label.setPixmap(QPixmap())
                zxdb_update_nav_buttons()
                return
            zxdb_show_shot_at(0)
            if len(self._zxdb_screenshots) > 1:
                self._zxdb_slideshow_timer.start()

        # ---- Search task ----

        def zxdb_current_mode():
            return self.zxdb_mode_combo.currentData() or "games"

        def zxdb_set_busy(busy: bool):
            self._zxdb_search_loading = busy
            self.zxdb_search_button.setEnabled(not busy)
            self.zxdb_random_button.setEnabled(not busy and zxdb_current_mode() == "games")
            self.zxdb_latest_button.setEnabled(not busy and zxdb_current_mode() == "games")
            self.zxdb_mode_combo.setEnabled(not busy)
            self.zxdb_letter_combo.setEnabled(not busy)

        def _zxdb_extract_es_hits(payload):
            """Return the array of hits from an Elasticsearch-style or flat payload."""
            if isinstance(payload, list):
                return payload
            if not isinstance(payload, dict):
                return []
            h = payload.get("hits")
            if isinstance(h, dict) and isinstance(h.get("hits"), list):
                return h["hits"]
            if isinstance(h, list):
                return h
            for k in ("items", "results"):
                v = payload.get(k)
                if isinstance(v, list):
                    return v
            return []

        def _zxdb_extract_es_total(payload):
            if isinstance(payload, dict):
                h = payload.get("hits")
                if isinstance(h, dict):
                    t = h.get("total")
                    if isinstance(t, dict):
                        return int(t.get("value") or 0)
                    if isinstance(t, (int, float)):
                        return int(t)
                for k in ("hits_count", "total", "totalHits"):
                    v = payload.get(k)
                    if isinstance(v, (int, float)):
                        return int(v)
            return 0

        def _zxdb_parse_magazine_list(payload):
            """Normalize /magazines/ response into the table's 6-column shape."""
            entries = []
            for it in _zxdb_extract_es_hits(payload):
                if not isinstance(it, dict):
                    continue
                src = it.get("_source", it)
                name = src.get("name") or src.get("magazine") or src.get("title") or ""
                publisher = src.get("publisher") or ""
                if isinstance(publisher, list):
                    publisher = ", ".join(
                        p.get("name", "") if isinstance(p, dict) else str(p)
                        for p in publisher
                    )
                entries.append({
                    "id":      str(it.get("_id") or src.get("id") or name),
                    "title":   str(name),
                    "year":    str(src.get("yearStart") or src.get("year") or ""),
                    "author":  str(publisher),
                    "machine": str(src.get("type") or "Magazine"),
                    "genre":   str(src.get("language") or src.get("country") or ""),
                    "_kind":   "magazine",
                    "_source": src,
                    "_name":   str(name),
                })
            return entries

        def _zxdb_parse_suggest_list(payload):
            """Normalize /suggest/{term} response into the table's 6-column shape."""
            entries = []
            if not isinstance(payload, list):
                return entries
            for it in payload:
                if not isinstance(it, dict):
                    continue
                text  = it.get("text") or it.get("name") or ""
                stype = it.get("type") or it.get("_type") or ""
                eid   = it.get("entry_id") or ""
                src   = it.get("_source") if isinstance(it.get("_source"), dict) else {}
                if not eid and isinstance(src, dict):
                    eid = src.get("id") or src.get("entry_id") or ""
                entries.append({
                    "id":      str(eid or text),
                    "title":   str(text),
                    "year":    "",
                    "author":  str(it.get("labeltype") or ""),
                    "machine": str(stype),
                    "genre":   "",
                    "_kind":   "suggest",
                    "_suggest_type": str(stype),
                    "_entry_id":     str(eid),
                    "_source": it,
                })
            return entries

        # Column header presets per result mode.
        _ZXDB_HEADERS = {
            "games":     ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"],
            "byletter":  ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"],
            "magazines": ["ID", "Magazine", "Year",  "Publisher",          "Type",    "Language / Country"],
            "author":    ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"],
            "suggest":   ["ID", "Suggestion", "—", "Label", "Type", "—"],
        }

        def zxdb_run_search(query: str, page: int, on_complete=None):
            mode = zxdb_current_mode()

            if mode == "suggest" and not query:
                zxdb_set_status("Type a term to get suggestions.")
                return
            if mode == "author" and not query:
                zxdb_set_status("Type an author / publisher name to search.")
                return

            # Supersede any in-flight ZXDB request.
            self._zxdb_search_gen += 1
            _gen = self._zxdb_search_gen
            zxdb_set_busy(True)
            zxdb_set_status("Searching…")
            self._zxdb_last_query = query

            offset = max(0, (page - 1) * ZXDB_PAGE_SIZE)

            if mode == "games":
                params = {
                    "size":   str(ZXDB_PAGE_SIZE),
                    "offset": str(offset),
                    "mode":   "compact",
                    "sort":   "rel_desc",
                    "contenttype": "SOFTWARE",
                }
                if query:
                    params["query"] = query
                path = f"/search?{urllib.parse.urlencode(params)}"

                def _fn():
                    payload = zxdb_fetch_json(path)
                    entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                    for e in entries:
                        e["_kind"] = "game"
                    return ("games", entries, total, page, total_pages)

            elif mode == "byletter":
                letter = self.zxdb_letter_combo.currentData() or "a"
                params = {
                    "size":   str(ZXDB_PAGE_SIZE),
                    "offset": str(offset),
                    "mode":   "compact",
                    "contenttype": "SOFTWARE",
                }
                path = f"/games/byletter/{urllib.parse.quote(letter)}?{urllib.parse.urlencode(params)}"

                def _fn():
                    payload = zxdb_fetch_json(path)
                    entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                    for e in entries:
                        e["_kind"] = "game"
                    return ("byletter", entries, total, page, total_pages)

            elif mode == "magazines":
                if query:
                    # Fetch a specific magazine by name
                    mag_path = f"/magazines/{urllib.parse.quote(query)}"

                    def _fn():
                        payload = zxdb_fetch_json(mag_path)
                        # /magazines/{name} returns a single ES hit: {_id, _source, …}
                        # Wrap it so _zxdb_parse_magazine_list can handle it uniformly.
                        if isinstance(payload, dict) and "_source" in payload:
                            wrapped = {"hits": {"hits": [payload], "total": {"value": 1}}}
                        elif isinstance(payload, list):
                            wrapped = {"hits": {"hits": payload, "total": {"value": len(payload)}}}
                        else:
                            wrapped = payload
                        entries = _zxdb_parse_magazine_list(wrapped)
                        total = len(entries)
                        return ("magazines", entries, total, 1, 1)
                else:
                    # List all magazines
                    params = {
                        "size":   str(ZXDB_PAGE_SIZE),
                        "offset": str(offset),
                        "sort":   "name_asc",
                    }
                    list_path = f"/magazines/?{urllib.parse.urlencode(params)}"

                    def _fn():
                        payload = zxdb_fetch_json(list_path)
                        entries = _zxdb_parse_magazine_list(payload)
                        total = _zxdb_extract_es_total(payload) or len(entries)
                        total_pages = max(1, (total + ZXDB_PAGE_SIZE - 1) // ZXDB_PAGE_SIZE) if total else 1
                        return ("magazines", entries, total, page, total_pages)

            elif mode == "author":
                # ZXInfo exposes both /authors/{name}/games and /publishers/{name}/games.
                # Many UI users type a publisher/label name (e.g. 'Ultimate'), so we
                # try authors first and fall back to publishers when authors yields
                # no hits — this matches the working URL the user supplied:
                #   /publishers/{name}/games?mode=compact&...
                params = {
                    "size":   str(ZXDB_PAGE_SIZE),
                    "offset": str(offset),
                    "mode":   "compact",
                    "sort":   "rel_desc",
                }
                qs = urllib.parse.urlencode(params)
                qname = urllib.parse.quote(query)

                def _fn():
                    used = "authors"
                    payload = zxdb_fetch_json(f"/authors/{qname}/games?{qs}")
                    entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                    if not entries:
                        used = "publishers"
                        payload = zxdb_fetch_json(f"/publishers/{qname}/games?{qs}")
                        entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                    for e in entries:
                        e["_kind"] = "game"
                        e["_source_endpoint"] = used
                    return ("author", entries, total, page, total_pages)

            else:  # suggest
                path = f"/suggest/{urllib.parse.quote(query)}"

                def _fn():
                    payload = zxdb_fetch_json(path)
                    entries = _zxdb_parse_suggest_list(payload)
                    return ("suggest", entries, len(entries), 1, 1)

            def _on_ok(data):
                if _gen != self._zxdb_search_gen:
                    return  # superseded by a newer search
                kind, entries, total, pg, total_pages = data
                zxdb_populate_results(entries, pg, total_pages, kind)
                if kind == "magazines":
                    zxdb_set_status(f"{len(entries)} magazine(s) shown  |  page {pg}/{total_pages}  |  {total} total")
                elif kind == "suggest":
                    zxdb_set_status(f"{len(entries)} suggestion(s)")
                elif kind == "author":
                    zxdb_set_status(f"{total} result(s) for '{query}'  |  page {pg}/{total_pages}")
                elif kind == "byletter":
                    letter_lbl = self.zxdb_letter_combo.currentText()
                    zxdb_set_status(f"{total} result(s) for '{letter_lbl}'  |  page {pg}/{total_pages}")
                else:
                    zxdb_set_status(f"{total} result(s)  |  page {pg}/{total_pages}")
                zxdb_set_busy(False)
                if on_complete:
                    on_complete()

            def _on_err(err):
                if _gen != self._zxdb_search_gen:
                    return  # superseded by a newer search
                exc = err[1]
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (502, 503, 504):
                    zxdb_set_status(f"Server temporarily unavailable (HTTP {exc.code}) — please try again.")
                else:
                    zxdb_set_status(f"Error: {exc}")
                zxdb_set_busy(False)
                if on_complete:
                    on_complete()

            self._zxdb_search_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_run_random(on_complete=None):
            # Supersede any in-flight ZXDB request.
            self._zxdb_search_gen += 1
            _gen = self._zxdb_search_gen
            self._zxdb_search_loading = True
            zxdb_set_status("Fetching random games…")
            self.zxdb_search_button.setEnabled(False)
            self.zxdb_random_button.setEnabled(False)
            self._zxdb_last_query = ""
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)

            def _fn():
                payload = zxdb_fetch_json(f"/games/random/{ZXDB_PAGE_SIZE}")
                # /games/random returns an ES envelope: { hits: { hits: [...] } }
                entries = []
                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    hits_outer = payload.get("hits", {})
                    if isinstance(hits_outer, dict):
                        items = hits_outer.get("hits", [])
                    elif isinstance(hits_outer, list):
                        items = hits_outer
                    else:
                        items = payload.get("items", [])
                else:
                    items = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    src = it.get("_source", it)
                    eid = it.get("_id") or src.get("id") or src.get("entry_id") or ""
                    author = ""
                    for key in ("authors", "publishers"):
                        v = src.get(key)
                        if isinstance(v, list) and v:
                            names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in v]
                            author = ", ".join(n for n in names if n)
                            if author:
                                break
                    entries.append({
                        "id":      str(eid),
                        "title":   str(zxdb_pick(src, "title", "fullTitle", "name")),
                        "year":    str(src.get("originalYearOfRelease") or src.get("yearOfRelease") or ""),
                        "author":  author,
                        "machine": str(zxdb_pick(src, "machineType", "machine_type", "machine")),
                        "genre":   str(zxdb_pick(src, "genreType", "genre", "genretype")),
                        "score":   "",
                        "_kind":   "game",
                    })
                return entries

            def _on_ok(entries):
                if _gen != self._zxdb_search_gen:
                    return  # superseded by a newer search
                self._zxdb_search_loading = False
                zxdb_populate_results(entries, 1, 1, "games")
                zxdb_set_status(f"{len(entries)} random game(s)")
                self.zxdb_search_button.setEnabled(True)
                self.zxdb_random_button.setEnabled(True)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, self.zxdb_results_table.rowCount())
                if on_complete:
                    on_complete()

            def _on_err(err):
                if _gen != self._zxdb_search_gen:
                    return  # superseded by a newer search
                self._zxdb_search_loading = False
                exc = err[1]
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (502, 503, 504):
                    zxdb_set_status(f"Server temporarily unavailable (HTTP {exc.code}) — please try again.")
                else:
                    zxdb_set_status(f"Error: {exc}")
                self.zxdb_search_button.setEnabled(True)
                self.zxdb_random_button.setEnabled(True)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, self.zxdb_results_table.rowCount())
                if on_complete:
                    on_complete()

            self._zxdb_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_run_latest(on_complete=None):
            # Supersede any in-flight ZXDB request.
            self._zxdb_search_gen += 1
            _gen = self._zxdb_search_gen
            self._zxdb_search_loading = True
            zxdb_set_status("Fetching latest games…")
            self.zxdb_search_button.setEnabled(False)
            self.zxdb_random_button.setEnabled(False)
            self.zxdb_latest_button.setEnabled(False)
            self._zxdb_last_query = ""
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)

            params = {
                "size":   str(ZXDB_PAGE_SIZE),
                "offset": "0",
                "mode":   "compact",
                "sort":   "date_desc",
                "contenttype": "SOFTWARE",
            }
            path = f"/search?{urllib.parse.urlencode(params)}"

            def _fn():
                payload = zxdb_fetch_json(path)
                entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                for e in entries:
                    e["_kind"] = "game"
                return (entries, total, total_pages)

            def _on_ok(data):
                if _gen != self._zxdb_search_gen:
                    return  # superseded by a newer search
                entries, total, total_pages = data
                self._zxdb_search_loading = False
                zxdb_populate_results(entries, 1, total_pages or 1, "games")
                zxdb_set_status(f"{len(entries)} latest game(s)")
                self.zxdb_search_button.setEnabled(True)
                self.zxdb_random_button.setEnabled(zxdb_current_mode() == "games")
                self.zxdb_latest_button.setEnabled(zxdb_current_mode() == "games")
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, self.zxdb_results_table.rowCount())
                if on_complete:
                    on_complete()

            def _on_err(err):
                if _gen != self._zxdb_search_gen:
                    return  # superseded by a newer search
                self._zxdb_search_loading = False
                zxdb_set_status(f"Error: {err[1]}")
                self.zxdb_search_button.setEnabled(True)
                self.zxdb_random_button.setEnabled(zxdb_current_mode() == "games")
                self.zxdb_latest_button.setEnabled(zxdb_current_mode() == "games")
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, self.zxdb_results_table.rowCount())
                if on_complete:
                    on_complete()

            self._zxdb_latest_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_on_search():
            zxdb_clear_detail()
            q = self.zxdb_search_input.text().strip()
            save_configuration_file()
            if q and len(q) < SEARCH_MIN_CHARS:
                return
            # Suppress the autocomplete suggestions popup once a search is
            # submitted; it stays hidden until the user types again.
            self._zxdb_ac_block = True
            try:
                _zxdb_ac_timer.stop()
            except Exception:
                pass
            try:
                self._zxdb_completer.popup().hide()
            except Exception:
                pass
            if q:
                _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                def _zxdb_done():
                    _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                    _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, self.zxdb_results_table.rowCount())
                zxdb_run_search(q, 1, _zxdb_done)
            else:
                zxdb_run_search(q, 1)
            if _multi_search_enabled() and q:
                self.getit_search_input.setText(q)
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    self.zxart_search_input.setText(q)
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _cross_search_getit(q)
                _cross_search_zxart(q)

        def zxdb_on_random(on_complete=None):
            zxdb_clear_detail()
            self.zxdb_search_input.clear()
            zxdb_run_random(on_complete)

        def zxdb_on_latest(on_complete=None):
            zxdb_clear_detail()
            self.zxdb_search_input.clear()
            # Force the mode to 'games' so the latest list is meaningful.
            for i in range(self.zxdb_mode_combo.count()):
                if self.zxdb_mode_combo.itemData(i) == "games":
                    if self.zxdb_mode_combo.currentIndex() != i:
                        self.zxdb_mode_combo.setCurrentIndex(i)
                    break
            zxdb_run_latest(on_complete)

        def zxdb_on_prev():
            zxdb_run_search(self._zxdb_last_query, max(1, self._zxdb_current_page - 1))

        def zxdb_on_next():
            zxdb_run_search(self._zxdb_last_query, min(self._zxdb_total_pages, self._zxdb_current_page + 1))

        self.zxdb_search_button.clicked.connect(zxdb_on_search)
        self.zxdb_random_button.clicked.connect(zxdb_on_random)
        self.zxdb_latest_button.clicked.connect(zxdb_on_latest)
        self.zxdb_search_input.returnPressed.connect(zxdb_on_search)
        self.zxdb_prev_button.clicked.connect(zxdb_on_prev)
        self.zxdb_next_button.clicked.connect(zxdb_on_next)

        def _zxdb_search_validate(text: str):
            t = text.strip()
            if not t:
                self._zxdb_search_valid_lbl.setVisible(False)
            elif len(t) < SEARCH_MIN_CHARS:
                self._zxdb_search_valid_lbl.setText('<font color="red">❌</font>')
                self._zxdb_search_valid_lbl.setToolTip(f"Searches must be {SEARCH_MIN_CHARS} characters long")
                self._zxdb_search_valid_lbl.setVisible(True)
            else:
                self._zxdb_search_valid_lbl.setText('<font color="green">✔</font>')
                self._zxdb_search_valid_lbl.setVisible(True)
        self.zxdb_search_input.textChanged.connect(_zxdb_search_validate)

        # ---- ZXDB autocomplete ----

        self._zxdb_ac_ready = False  # suppressed until after startup
        self._zxdb_ac_model = QStringListModel(self)
        _zxdb_completer = QCompleter(self._zxdb_ac_model, self)
        _zxdb_completer.setCompletionMode(QCompleter.PopupCompletion)
        _zxdb_completer.setCaseSensitivity(Qt.CaseInsensitive)
        _zxdb_completer.setFilterMode(Qt.MatchStartsWith)
        # Ensure the popup follows the main window on Windows
        popup = _zxdb_completer.popup()
        if popup is not None:
            popup.setParent(self)
            popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
            popup.setAttribute(Qt.WA_ShowWithoutActivating)
        self._zxdb_completer = _zxdb_completer
        self.zxdb_search_input.setCompleter(_zxdb_completer)

        _zxdb_ac_timer = QTimer(self)
        _zxdb_ac_timer.setSingleShot(True)
        _zxdb_ac_timer.setInterval(300)

        def _zxdb_safe_show_popup(q: str):
            """Show the ZXDB completer popup without calling QCompleter.complete(),
            which has crashed Qt with a native access violation on Windows."""
            try:
                if not self._search_autocomplete_on():
                    return
                if getattr(self, "_zxdb_ac_block", False):
                    return
                if not self.zxdb_search_input.hasFocus():
                    return
                if self.zxdb_search_input.text().strip() != q:
                    return
                if self._zxdb_ac_model.rowCount() == 0:
                    return
                _zxdb_completer.setCompletionPrefix(q)
                popup = _zxdb_completer.popup()
                if popup is None:
                    return
                try:
                    popup.setParent(self.zxdb_search_input.window(),
                                    Qt.Tool
                                    | Qt.FramelessWindowHint
                                    | Qt.WindowStaysOnTopHint
                                    | Qt.WindowDoesNotAcceptFocus)
                    popup.setFocusPolicy(Qt.NoFocus)
                    popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
                except Exception:
                    pass
                le = self.zxdb_search_input
                rect = le.rect()
                pos = le.mapToGlobal(rect.bottomLeft())
                popup.setMinimumWidth(le.width())
                popup.move(pos)
                popup.resize(le.width(), _popup_height_for(popup, self._zxdb_ac_model.rowCount()))
                popup.show()
            except RuntimeError:
                pass
            except Exception:
                pass

        def _zxdb_ac_update_model(text: str):
            """Filter the cached per-letter titles to those starting with
            *text* off the UI thread, then update the completer model."""
            if not text:
                self._zxdb_ac_model.setStringList([])
                return
            letter = text[0].lower()
            cached_snapshot = list(self._zxdb_ac_cache.get(letter, []))
            self._zxdb_ac_filter_gen = getattr(self, "_zxdb_ac_filter_gen", 0) + 1
            gen = self._zxdb_ac_filter_gen
            tl = text.lower()

            def _fn():
                matches = [t for t in cached_snapshot if t.lower().startswith(tl)]
                return (gen, text, matches[:80])

            def _on_ok(result):
                rgen, rtext, matches = result
                if rgen != getattr(self, "_zxdb_ac_filter_gen", -1):
                    return
                try:
                    if self.zxdb_search_input.text().strip() != rtext:
                        return
                except RuntimeError:
                    return
                self._zxdb_ac_model.setStringList(matches)
                if matches:
                    QTimer.singleShot(0, lambda q=rtext: _zxdb_safe_show_popup(q))

            def _on_err(_err):
                pass

            getit_run_in_thread(_fn, _on_ok, _on_err)

        def _zxdb_ac_fetch_letter(letter: str):
            """Fetch all titles for *letter* via /games/byletter, cache, then refresh model."""
            if letter in self._zxdb_ac_fetching:
                return
            self._zxdb_ac_fetching.add(letter)
            self._ac_anim_start(self.zxdb_search_input)

            def _fn():
                titles = []
                offset = 0
                fetch_size = 200
                total = None
                while True:
                    params = {
                        "size":        str(fetch_size),
                        "offset":      str(offset),
                        "mode":        "compact",
                        "contenttype": "SOFTWARE",
                    }
                    path = f"/games/byletter/{urllib.parse.quote(letter)}?{urllib.parse.urlencode(params)}"
                    try:
                        payload = zxdb_fetch_json(path)
                        entries, page_total, _pg, _tp, _ps = zxdb_parse_search(payload)
                    except Exception:
                        break
                    if not entries:
                        break
                    titles.extend(e["title"] for e in entries if e.get("title"))
                    # ZXInfo may cap the effective page size below the value
                    # we asked for, so do not exit just because we received
                    # fewer rows than requested.  Drive pagination from the
                    # server-reported total instead and stop only once we
                    # have walked the whole letter (or the API stops
                    # returning new rows).
                    if total is None and page_total:
                        total = page_total
                    offset += len(entries)
                    if total is not None and offset >= total:
                        break
                    # Safety net: if the API keeps returning the same rows
                    # without advancing, bail out.
                    if total is None and len(entries) < 10:
                        break
                return (letter, sorted(set(titles), key=str.lower))

            def _on_ok(result):
                ltr, sorted_titles = result
                self._zxdb_ac_fetching.discard(ltr)
                self._zxdb_ac_cache[ltr] = sorted_titles
                self._ac_anim_stop(self.zxdb_search_input)
                # Refresh model if the user is still on this prefix.
                _zxdb_ac_update_model(self.zxdb_search_input.text().strip())
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("zxdb", ltr)
                    except Exception:
                        pass

            def _on_err(_err):
                self._zxdb_ac_fetching.discard(letter)
                self._ac_anim_stop(self.zxdb_search_input)
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("zxdb", letter)
                    except Exception:
                        pass

            self._zxdb_ac_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        # Expose the per-letter fetcher so the AllInOne pane can prime the
        # ZXDB cache for cross-source autocomplete suggestions.
        self._zxdb_ac_fetch_letter = _zxdb_ac_fetch_letter

        def _zxdb_ac_trigger():
            if not _search_autocomplete_on():
                self._zxdb_ac_model.setStringList([])
                return
            mode = zxdb_current_mode()
            if mode not in ("games", "byletter", "author"):
                self._zxdb_ac_model.setStringList([])
                return
            text = self.zxdb_search_input.text().strip()
            if not text:
                self._zxdb_ac_model.setStringList([])
                return
            letter = text[0].lower()
            if letter in self._zxdb_ac_cache:
                _zxdb_ac_update_model(text)
            else:
                _zxdb_ac_fetch_letter(letter)

        def _zxdb_ac_on_text_changed(_text: str):
            if getattr(self, "_zxdb_ac_suppress", False):
                self._zxdb_ac_suppress = False
                return
            # The user is typing again: re-enable autocomplete suggestions
            # that were suppressed after the last search submission.
            self._zxdb_ac_block = False
            _zxdb_ac_timer.start()

        _zxdb_ac_timer.timeout.connect(_zxdb_ac_trigger)
        self.zxdb_search_input.textChanged.connect(_zxdb_ac_on_text_changed)

        def _zxdb_ac_activated(selected: str):
            try:
                if selected:
                    self._zxdb_ac_suppress = True
                    _zxdb_ac_timer.stop()
                    try:
                        _zxdb_completer.popup().hide()
                    except Exception:
                        pass
                    self.zxdb_search_input.setText(selected)
            except Exception:
                pass
            zxdb_on_search()

        _zxdb_completer.activated.connect(_zxdb_ac_activated)

        def zxdb_on_mode_changed(_idx):
            mode = zxdb_current_mode()
            placeholders = {
                "games":     "Search ZXDB games... (leave empty for random selection)",
                "byletter":  "(pick a letter from the list →)",
                "magazines": "Filter magazines... (leave empty to list all)",
                "author":    "Type an author name (e.g. 'Matthew Smith')",
                "suggest":   "Type a term to get suggestions",
            }
            self.zxdb_search_input.setPlaceholderText(placeholders.get(mode, ""))
            self.zxdb_search_input.setVisible(mode != "byletter")
            self.zxdb_letter_combo.setVisible(mode == "byletter")
            self.zxdb_random_button.setEnabled(mode == "games")
            self.zxdb_latest_button.setEnabled(mode == "games")
            # Reset paging/results when switching modes.
            self._zxdb_last_query = ""
            self._zxdb_current_page = 1
            self._zxdb_total_pages = 1
            self.zxdb_page_label.setText("1")
            self.zxdb_prev_button.setEnabled(False)
            self.zxdb_next_button.setEnabled(False)
            self.zxdb_results_table.setRowCount(0)
            zxdb_clear_detail()
            zxdb_set_status("")
            configuration_dictionary[SETTING_ZXDB_LAST_MODE] = mode
            save_configuration_file()

        self.zxdb_mode_combo.currentIndexChanged.connect(zxdb_on_mode_changed)

        def zxdb_on_letter_changed(_idx):
            if zxdb_current_mode() == "byletter":
                zxdb_clear_detail()
                zxdb_run_search("", 1)

        self.zxdb_letter_combo.currentIndexChanged.connect(zxdb_on_letter_changed)

        # ---- Row selection -> fetch detail + screenshot ----

        def _zxdb_reset_preview():
            self._zxdb_slideshow_timer.stop()
            self._zxdb_shot_token += 1
            self._zxdb_screenshots = []
            self._zxdb_shot_cache  = {}
            self._zxdb_shot_index  = 0
            self.zxdb_screenshot_label.setPixmap(QPixmap())

        def _zxdb_load_game(eid: str, title_hint: str):
            self._zxdb_selected_id    = eid
            self._zxdb_selected_title = title_hint or eid
            zxdb_set_status(f"Loading {eid}…")
            self.zxdb_screenshot_label.setText("Loading…")
            _zxdb_reset_preview()

            def _fn():
                payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)

            def _on_ok(detail):
                if self._zxdb_selected_id != eid:
                    return
                zxdb_populate_detail(detail)
                shots = detail.get("screenshots") or []
                if not shots and detail.get("screenshot_url"):
                    shots = [{"url": detail["screenshot_url"], "type": ""}]
                zxdb_start_slideshow(shots)
                n = len(shots)
                title = detail.get("title", eid)
                if n > 1:
                    zxdb_set_status(f"Loaded {title}  |  {n} screenshots (cycling every 5s)")
                else:
                    zxdb_set_status(f"Loaded {title}")

            def _on_err(err):
                if self._zxdb_selected_id != eid:
                    return
                zxdb_set_status(f"Detail error: {err[1]}")
                self.zxdb_screenshot_label.setText("No preview")

            self._zxdb_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _zxdb_load_magazine(entry: dict):
            name = entry.get("_name") or entry.get("title") or ""
            self._zxdb_selected_id    = entry.get("id") or name
            self._zxdb_selected_title = name
            self._zxdb_magazine_issues = []
            zxdb_set_status(f"Loading magazine '{name}'…")
            self.zxdb_screenshot_label.setText("Loading…")
            _zxdb_reset_preview()

            def _fn():
                # /magazines/{name} returns a single ES hit whose _source contains
                # the full issues array (with id, files, cover_image per issue).
                return zxdb_fetch_json(f"/magazines/{urllib.parse.quote(name)}")

            def _on_ok(payload):
                if self._zxdb_selected_title != name:
                    return
                src = {}
                if isinstance(payload, dict) and "_source" in payload:
                    src = payload["_source"]
                elif isinstance(payload, dict):
                    src = payload
                issues = src.get("issues") or []
                self._zxdb_magazine_issues = issues
                # Build summary dict for the detail panel
                summary = {
                    "publisher": src.get("publisher") or "",
                    "type":      src.get("type") or "",
                    "language":  src.get("language") or "",
                    "country":   src.get("country") or "",
                }
                # Wrap issues as a payload that zxdb_populate_magazine_detail understands
                issues_payload = {"issues": issues}
                zxdb_populate_magazine_detail(name, summary, issues_payload)
                # Build a slideshow from issue cover_images
                shots = []
                seen = set()
                for i in issues:
                    if not isinstance(i, dict):
                        continue
                    cov = i.get("cover_image") or ""
                    if not cov:
                        continue
                    url = cov if cov.startswith("http") else "https://spectrumcomputing.co.uk" + cov
                    if url in seen:
                        continue
                    seen.add(url)
                    label = []
                    if i.get("volume")     is not None: label.append(f"V{i['volume']}")
                    if i.get("number")     is not None: label.append(f"#{i['number']}")
                    if i.get("date_year"): label.append(str(i["date_year"]))
                    shots.append({"url": url, "type": " ".join(label) or "Cover"})
                zxdb_start_slideshow(shots)
                n_shots  = len(shots)
                n_issues = len(issues)
                hint = "  (double-click or right-click → Retrieve all issues)" if n_issues > 1 else ""
                if n_shots > 1:
                    zxdb_set_status(f"Loaded {name}  |  {n_issues} issue(s), {n_shots} cover(s) cycling every 5s{hint}")
                else:
                    zxdb_set_status(f"Loaded {name}  |  {n_issues} issue(s){hint}")

            def _on_err(err):
                if self._zxdb_selected_title != name:
                    return
                zxdb_set_status(f"Magazine error: {err[1]}")
                self.zxdb_screenshot_label.setText("No preview")

            self._zxdb_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _zxdb_open_issues_dialog(mag_name: str, issues: list):
            """Show a dialog listing all issues for a magazine.
            Selecting a row loads its files/preview; right-click offers Download content."""
            if not issues:
                zxdb_set_status(f"No issues available for '{mag_name}'.")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(f"All issues — {mag_name}  ({len(issues)} issues)")
            dlg.resize(860, 500)
            v = QVBoxLayout(dlg)

            tbl = QTableWidget(len(issues), 5, dlg)
            tbl.setHorizontalHeaderLabels(["Issue #", "Volume", "Year", "Month", "Files"])
            tbl.verticalHeader().setVisible(False)
            tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setAlternatingRowColors(True)
            tbl.horizontalHeader().setStretchLastSection(True)
            tbl.setColumnWidth(0, 80)
            tbl.setColumnWidth(1, 80)
            tbl.setColumnWidth(2, 80)
            tbl.setColumnWidth(3, 80)

            for row, iss in enumerate(issues):
                if not isinstance(iss, dict):
                    continue
                tbl.setItem(row, 0, QTableWidgetItem(str(iss.get("number") or "")))
                tbl.setItem(row, 1, QTableWidgetItem(str(iss.get("volume") or "")))
                tbl.setItem(row, 2, QTableWidgetItem(str(iss.get("date_year") or "")))
                tbl.setItem(row, 3, QTableWidgetItem(str(iss.get("date_month") or "")))
                files = iss.get("files") or []
                tbl.setItem(row, 4, QTableWidgetItem(str(len(files))))
                # Stash the full issue dict
                tbl.item(row, 0).setData(Qt.UserRole, iss)

            def _load_issue_from_row(row: int):
                """Load files/preview from the already-fetched issue data."""
                id_cell = tbl.item(row, 0)
                if not id_cell:
                    return
                iss = id_cell.data(Qt.UserRole)
                if not isinstance(iss, dict):
                    return
                issue_num = iss.get("number") or iss.get("id") or ""
                issue_id_api = str(iss.get("id") or issue_num)
                downloads = []
                shots = []
                for f in (iss.get("files") or []):
                    if not isinstance(f, dict):
                        continue
                    link = f.get("file_link") or ""
                    if not link:
                        continue
                    url = link if link.startswith("http") else "https://spectrumcomputing.co.uk" + link
                    ftype = f.get("filetype") or ""
                    fname = f.get("filename") or os.path.basename(urllib.parse.urlparse(url).path) or ""
                    downloads.append({
                        "url":      url,
                        "filename": fname,
                        "type":     ftype,
                        "format":   ftype,
                        "size":     f.get("file_size"),
                        "source":   f.get("comments") or "",
                    })
                    if url.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                        shots.append({"url": url, "type": ftype})
                # Update main panel
                _zxdb_clear_detail_rows()
                _zxdb_add_row("Magazine:", mag_name)
                _zxdb_add_row("Issue #:",  str(issue_num))
                for key, lbl in (
                    ("date_year",  "Year"),
                    ("date_month", "Month"),
                    ("volume",     "Volume"),
                ):
                    v2 = iss.get(key)
                    if v2 is not None:
                        _zxdb_add_row(f"{lbl}:", str(v2))
                self._zxdb_selected_downloads = downloads
                self._zxdb_selected_title = f"{mag_name} #{issue_num}"
                self._zxdb_selected_id = f"{mag_name}:{issue_id_api}"
                self.zxdb_download_button.setEnabled(bool(downloads))
                if shots:
                    zxdb_start_slideshow(shots)
                else:
                    self.zxdb_screenshot_label.setText("No image files")
                n_files = len(downloads)
                zxdb_set_status(
                    f"Issue #{issue_num} of '{mag_name}'"
                    + (f"  |  {n_files} file(s)" if n_files else "  |  no files")
                )

            def _on_issue_selected():
                sel = tbl.selectionModel().selectedRows()
                if sel:
                    _load_issue_from_row(sel[0].row())

            def _on_issue_double_clicked(item):
                _load_issue_from_row(tbl.row(item))

            def _on_issue_context_menu(pos):
                item = tbl.itemAt(pos)
                if item is None:
                    return
                row = tbl.row(item)
                tbl.selectRow(row)
                _load_issue_from_row(row)
                menu2 = QMenu(tbl)
                act_dl = menu2.addAction("Download content")
                act_dl.setEnabled(bool(self._zxdb_selected_downloads))
                action = menu2.exec(tbl.viewport().mapToGlobal(pos))
                if action is act_dl:
                    zxdb_show_downloads_overlay(
                        self._zxdb_selected_title,
                        self._zxdb_selected_downloads,
                    )

            tbl.itemSelectionChanged.connect(_on_issue_selected)
            tbl.itemDoubleClicked.connect(_on_issue_double_clicked)
            tbl.setContextMenuPolicy(Qt.CustomContextMenu)
            tbl.customContextMenuRequested.connect(_on_issue_context_menu)

            v.addWidget(tbl, 1)

            btn_close = QPushButton("Close")
            btn_close.clicked.connect(dlg.accept)
            brow = QHBoxLayout()
            brow.addStretch()
            brow.addWidget(btn_close)
            v.addLayout(brow)

            dlg.exec()

        def _zxdb_load_suggest(entry: dict):
            stype = (entry.get("_suggest_type") or "").upper()
            eid   = entry.get("_entry_id") or ""
            # If the suggestion points at a SOFTWARE entry, drill straight into it.
            if stype == "SOFTWARE" and eid:
                _zxdb_load_game(eid, entry.get("title", ""))
                return
            # Otherwise just show the suggestion details.
            _zxdb_reset_preview()
            self._zxdb_selected_id    = entry.get("id") or ""
            self._zxdb_selected_title = entry.get("title", "")
            self.zxdb_screenshot_label.setText("No preview")
            zxdb_populate_suggest_detail(entry)
            zxdb_set_status(f"Suggestion: {entry.get('title', '')}  ({stype or 'unknown'})")

        def zxdb_on_row_selected():
            sel = self.zxdb_results_table.selectionModel().selectedRows()
            if not sel:
                return
            row = sel[0].row()
            id_item    = self.zxdb_results_table.item(row, 0)
            title_item = self.zxdb_results_table.item(row, 1)
            if not id_item:
                return
            entry = id_item.data(Qt.UserRole) or {}
            kind = (entry.get("_kind") or "game").lower()
            title_hint = title_item.text() if title_item else id_item.text()

            self.zxdb_download_button.setEnabled(False)

            if kind == "magazine":
                _zxdb_load_magazine(entry)
            elif kind == "suggest":
                _zxdb_load_suggest(entry)
            else:
                _zxdb_load_game(id_item.text(), title_hint)

        self.zxdb_results_table.itemSelectionChanged.connect(zxdb_on_row_selected)

        def zxdb_on_gallery_cell(entry):
            eid = entry.get("id") or ""
            if not eid:
                return
            for r in range(self.zxdb_results_table.rowCount()):
                item = self.zxdb_results_table.item(r, 0)
                if item is not None and item.text() == eid:
                    self.zxdb_results_table.selectRow(r)
                    break
            self.zxdb_gallery_view.select_entry(lambda _e, _e0=entry: _e is _e0)

        self.zxdb_gallery_view.cell_clicked.connect(zxdb_on_gallery_cell)

        def _zxdb_open_gallery_viewer(entry, make_viewer=None, install=True):
            eid   = entry.get("id") or ""
            title = entry.get("title") or eid
            if not eid:
                return None
            kind = (entry.get("_kind") or "game").lower()

            info_rows_base = [
                ("Title:",   title),
                ("Author:",  entry.get("author", "")),
                ("Year:",    str(entry.get("year", "") or "")),
                ("Machine:", entry.get("machine", "")),
                ("Genre:",   entry.get("genre", "")),
            ]
            _mk = make_viewer or (lambda **kw: GalleryItemViewer(parent=self, **kw))
            viewer = _mk(
                title=title,
                info_rows=info_rows_base,
                screenshots=[],
                extra_fetch_cb=_zxdb_extra_fetch,
                tags=_gallery_extract_tags(entry),
            )
            _fav_entry_zxdb = {**entry, "_fav_source": "zxdb"}
            viewer.set_favorite_hooks(_fav_entry_zxdb, self._fav_is, self._fav_toggle)

            # ── action buttons ──────────────────────────────────────────
            _safe_title = zxdb_sanitize_folder(title)
            _img_path   = self.right_disk_image_path or ""
            _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                           ) if _img_path else ""
            _sd_dest    = f"{_img_path}  →  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base    = _zxdb_resolve_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest    = os.path.join(_ns_base, _safe_title)
            _sd_ok      = bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content)

            def _dl_btn():
                if kind == "magazine":
                    if self._zxdb_selected_downloads:
                        zxdb_show_downloads_overlay(self._zxdb_selected_title or title,
                                                    self._zxdb_selected_downloads)
                    return
                if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                    zxdb_show_downloads_overlay(self._zxdb_selected_title or title,
                                                self._zxdb_selected_downloads)
                    return
                zxdb_set_status(f"Loading {eid}\u2026")
                def _fn():
                    payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok(detail):
                    zxdb_populate_detail(detail)
                    dls = _filter_download_urls(detail.get("downloads", []) or [])
                    viewer.set_download_available(bool(dls))
                    if not dls:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    zxdb_show_downloads_overlay(detail.get("title") or title, dls)
                def _on_err(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            def _sd_btn():
                if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                    _zxdb_send_to_image(self._zxdb_selected_title or title,
                                        self._zxdb_selected_downloads)
                    return
                zxdb_set_status(f"Loading {eid}\u2026")
                def _fn():
                    payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok(detail):
                    zxdb_populate_detail(detail)
                    dls = _filter_download_urls(detail.get("downloads", []) or [])
                    viewer.set_download_available(bool(dls))
                    if not dls:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    _zxdb_send_to_image(detail.get("title") or title, dls)
                def _on_err(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            def _ns_btn():
                if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                    def _after(_f):
                        QTimer.singleShot(0, lambda _folder=_f: self._nextsync_start_server_fn(_folder))
                    _zxdb_send_to_path(self._zxdb_selected_title or title,
                                       self._zxdb_selected_downloads, _ns_base, _after)
                    return
                zxdb_set_status(f"Loading {eid}\u2026")
                def _fn():
                    payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok(detail):
                    zxdb_populate_detail(detail)
                    dls = _filter_download_urls(detail.get("downloads", []) or [])
                    viewer.set_download_available(bool(dls))
                    if not dls:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    def _after(_f):
                        QTimer.singleShot(0, lambda _folder=_f: self._nextsync_start_server_fn(_folder))
                    _zxdb_send_to_path(detail.get("title") or title, dls, _ns_base, _after)
                def _on_err(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                self._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            viewer.set_actions(
                download_cb=_dl_btn, send_sd_cb=_sd_btn, send_ns_cb=_ns_btn,
                sd_enabled=_sd_ok, sd_tooltip=_sd_dest,
                ns_enabled=True,   ns_tooltip=_ns_dest,
            )
            self._wire_viewer_emulators(
                viewer, allow=ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS)
            viewer.set_open_web_url(zxdb_entry_website_url(eid), "zxinfo.dk")
            # If downloads are disabled globally, hide all action buttons immediately.
            if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                viewer.set_download_available(False)
            # If we already have cached (filtered) downloads, use them to set
            # initial button visibility; otherwise keep buttons visible until
            # the async enrich resolves.
            elif self._zxdb_selected_id == eid:
                viewer.set_download_available(
                    bool(_filter_download_urls(self._zxdb_selected_downloads or []))
                )

            # ── async enrich (screenshots + full metadata) ──────────────
            def _fn():
                if kind == "magazine":
                    return ("magazine", {}, [])
                payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                detail  = zxdb_parse_game_detail(payload)
                shots   = detail.get("screenshots") or []
                if not shots and detail.get("screenshot_url"):
                    shots = [{"url": detail["screenshot_url"], "type": ""}]
                return ("game", detail, shots)
            def _on_ok(res):
                kind2, detail, shots = res
                if kind2 == "magazine":
                    return
                urls = [s.get("url") for s in shots if isinstance(s, dict) and s.get("url")]
                if urls:
                    viewer.set_screenshots(urls)
                else:
                    _dls_tmp = _filter_download_urls(detail.get("downloads", []) or [])
                    _ph_label, _ph_fname = zxfmt_pick_best_download(_dls_tmp)
                    _ph_sub = _ph_fname or detail.get("title") or title
                    viewer.set_placeholder(_ph_label, _ph_sub)
                rows = [
                    ("Title:",       detail.get("title", title)),
                    ("Year:",        str(detail.get("year", "") or "")),
                    ("Authors:",     detail.get("authors", "")),
                    ("Published by:", detail.get("publishers", "")),
                    ("Machine:",     detail.get("machine", "")),
                    ("Genre:",       detail.get("genre", "")),
                    ("Language:",    detail.get("language", "")),
                    ("Description:", detail.get("description") or detail.get("remarks", "")),
                ]
                _gallery_viewer_refresh_meta(viewer, detail.get("title") or title, rows)
                dls = _filter_download_urls(detail.get("downloads", []) or [])
                if ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                    viewer.set_download_available(bool(dls))
            def _on_err(_e): viewer.set_placeholder("FILE", title)
            self._zxdb_gallery_viewer_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            # ── push into pane stack ────────────────────────────────────
            if install:
                viewer.install_into_stack(
                    self._zxdb_stack,
                    close_fn=lambda: self._zxdb_stack.setCurrentIndex(0),
                )
            return viewer

        self.zxdb_gallery_view.cell_dbl_clicked.connect(_zxdb_open_gallery_viewer)

        def _zxdb_apply_view_mode(mode: str, *, persist: bool = True):
            mode = (mode or "table").lower()
            if mode not in ("table", "gallery"):
                mode = "table"
            self._zxdb_view_mode = mode
            self.zxdb_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
            _table = (mode == "table")
            if hasattr(self, '_zxdb_preview_container'):
                self._zxdb_preview_container.setVisible(_table)
            if hasattr(self, '_zxdb_preview_download_btn'):
                self._zxdb_preview_download_btn.setVisible(
                    _table and ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS
                )
            cb = self.zxdb_view_combo
            target_idx = 1 if mode == "gallery" else 0
            if cb.currentIndex() != target_idx:
                cb.blockSignals(True)
                cb.setCurrentIndex(target_idx)
                cb.blockSignals(False)
            if persist:
                # sync other panes to the same view mode
                if hasattr(self, '_getit_apply_view_mode'):
                    self._getit_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxart_apply_view_mode'):
                    self._zxart_apply_view_mode(mode, persist=False)
                if hasattr(self, '_favorites_apply_view_mode'):
                    self._favorites_apply_view_mode(mode, persist=False)
                if hasattr(self, '_allinone_apply_view_mode'):
                    self._allinone_apply_view_mode(mode, persist=False)
                configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
                configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
                configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
                save_configuration_file()

        self._zxdb_apply_view_mode = _zxdb_apply_view_mode

        def _on_zxdb_view_combo_changed(_idx):
            _zxdb_apply_view_mode(self.zxdb_view_combo.currentData() or "table")

        self.zxdb_view_combo.currentIndexChanged.connect(_on_zxdb_view_combo_changed)
        _zxdb_apply_view_mode(self._zxdb_view_mode, persist=False)

        def zxdb_on_row_double_clicked(item):
            row = self.zxdb_results_table.row(item)
            id_item = self.zxdb_results_table.item(row, 0)
            if not id_item:
                return
            entry = id_item.data(Qt.UserRole) or {}
            if (entry.get("_kind") or "").lower() == "magazine":
                mag_name = entry.get("_name") or entry.get("title") or ""
                if self._zxdb_magazine_issues and self._zxdb_selected_title == mag_name:
                    _zxdb_open_issues_dialog(mag_name, self._zxdb_magazine_issues)
                else:
                    # Issues not loaded yet — load then open dialog
                    zxdb_set_status(f"Loading issues for '{mag_name}'…")
                    def _fn_dbl():
                        payload = zxdb_fetch_json(f"/magazines/{urllib.parse.quote(mag_name)}")
                        src = payload.get("_source", payload) if isinstance(payload, dict) else {}
                        return src.get("issues") or []
                    def _on_ok_dbl(issues):
                        self._zxdb_magazine_issues = issues
                        _zxdb_open_issues_dialog(mag_name, issues)
                    def _on_err_dbl(err):
                        zxdb_set_status(f"Error loading issues: {err[1]}")
                    self._zxdb_detail_thread = getit_run_in_thread(_fn_dbl, _on_ok_dbl, _on_err_dbl)
            else:
                _zxdb_open_gallery_viewer(entry)

        self.zxdb_results_table.itemDoubleClicked.connect(zxdb_on_row_double_clicked)

        # ---- Download ----

        def zxdb_pick_default_download():
            """Choose a sensible default file from the current detail's downloads."""
            if not self._zxdb_selected_downloads:
                return None
            preferred_ext = (".tap", ".tzx", ".z80", ".sna", ".trd", ".dsk", ".scl")
            for d in self._zxdb_selected_downloads:
                u = (d.get("url") or "").lower()
                if u.endswith(preferred_ext):
                    return d
            return self._zxdb_selected_downloads[0]

        def zxdb_do_download(d: dict):
            url = d.get("url", "")
            if not url:
                return
            base = os.path.basename(urllib.parse.urlparse(url).path) or f"{self._zxdb_selected_id}.bin"
            save_path, _ = QFileDialog.getSaveFileName(None, "Save file", base)
            if not save_path:
                return
            zxdb_set_status(f"Downloading {base}…")
            self.zxdb_download_button.setEnabled(False)

            def _fn():
                data = zxdb_fetch_bytes(url, timeout=60)
                with open(save_path, "wb") as f:
                    f.write(data)
                return save_path

            def _on_ok(p):
                zxdb_set_status(f"Saved to {p}  ↗ open folder", open_path=os.path.abspath(p))
                self.zxdb_download_button.setEnabled(bool(self._zxdb_selected_downloads))

            def _on_err(err):
                zxdb_set_status(f"Download error: {err[1]}")
                self.zxdb_download_button.setEnabled(bool(self._zxdb_selected_downloads))

            self._zxdb_dl_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_on_download_clicked():
            d = zxdb_pick_default_download()
            if d:
                zxdb_do_download(d)

        self.zxdb_download_button.clicked.connect(zxdb_on_download_clicked)

        # ---- Context menu on results table ----

        def zxdb_sanitize_folder(name: str) -> str:
            n = (name or "").strip().lower()
            # Strip illegal Windows path chars
            for ch in '<>:"/\\|?*':
                n = n.replace(ch, "")
            # Collapse whitespace to single space, then dashes/spaces collapsed
            n = " ".join(n.split())
            return n or "untitled"

        def zxdb_human_size(n) -> str:
            try:
                n = int(n)
            except (TypeError, ValueError):
                return str(n) if n else ""
            if n <= 0:
                return ""
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} TB"

        def zxdb_download_to_path(url: str, save_path: str, on_done=None, on_err=None):
            def _fn():
                data = zxdb_fetch_bytes(url, timeout=60)
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(data)
                return save_path
            def _ok(p):
                if on_done: on_done(p)
            def _err(e):
                if on_err: on_err(e)
            return getit_run_in_thread(_fn, _ok, _err)

        def _zxdb_resolve_base_path(configured_path: str) -> str:
            """Return the configured path if it's a valid directory, else fall back to app-local 'downloads'."""
            p = (configured_path or "").strip().rstrip("/\\")
            if p and os.path.isdir(p):
                return p
            return os.path.abspath("downloads")

        def _zxdb_send_to_path(title: str, downloads: list, dest_root: str, post_action=None):
            """Download all files in *downloads* into dest_root/{sanitized_title}/, then call post_action(folder)."""
            if not downloads:
                zxdb_set_status("No downloadable files for this entry.")
                return
            folder = os.path.join(dest_root, zxdb_sanitize_folder(title))
            os.makedirs(folder, exist_ok=True)
            pending = {"n": len(downloads), "ok": 0, "ko": 0}

            def _maybe_finish():
                if pending["ok"] + pending["ko"] >= pending["n"]:
                    if pending["ok"] > 0:
                        zxdb_set_status(
                            f"Sent {pending['ok']}/{pending['n']} file(s) → {folder}  ↗ open folder",
                            open_path=folder,
                        )
                    else:
                        zxdb_set_status(f"All {pending['n']} download(s) failed — check the URLs")
                    if post_action:
                        post_action(folder)

            for d in downloads:
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                save_path = os.path.join(folder, fname)

                def _ok(p, _f=fname):
                    pending["ok"] += 1
                    zxdb_set_status(f"Downloaded {_f}")
                    _maybe_finish()

                def _err(e, _f=fname):
                    pending["ko"] += 1
                    zxdb_set_status(f"Failed {_f}: {e[1]}")
                    _maybe_finish()

                zxdb_download_to_path(d.get("url", ""), save_path, _ok, _err)

        def _zxdb_send_to_image(title: str, downloads: list):
            """Download all ZXDB files to temp then hdfmonkey-put them into the loaded disk image."""
            if not right_disk_image_explorer_content:
                zxdb_set_status("Please load a disk image first (SD Card tab).")
                return
            if not self.right_disk_image_path:
                zxdb_set_status("No disk image loaded.")
                return
            if not downloads:
                zxdb_set_status("No downloadable files for this entry.")
                return

            safe_name  = zxdb_sanitize_folder(title)
            img_dir    = (generate_disk_file_path().rstrip("/") + "/" + safe_name).replace("//", "/")
            image_path = self.right_disk_image_path
            pending    = {"n": len(downloads), "ok": 0, "ko": 0}

            def _maybe_finish():
                if pending["ok"] + pending["ko"] >= pending["n"]:
                    if pending["ok"] > 0:
                        zxdb_set_status(f"Sent {pending['ok']}/{pending['n']} file(s) → image:{img_dir}")
                        self._show_sd_notification(
                            f"Sent {pending['ok']}/{pending['n']} file(s) to SD card image:\n{img_dir}"
                        )
                        res = execute_hdf_monkey("ls", image_path, extra_argv=[generate_disk_file_path()])
                        if res.returncode == 0:
                            update_disk_manager_widget_table(res.stdout)
                    else:
                        zxdb_set_status(f"All {pending['n']} download(s) failed — check the URLs")

            for d in downloads:
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                url      = d.get("url", "")
                img_dest = (img_dir + "/" + fname).replace("//", "/")

                def _dl_and_put(_url=url, _fname=fname, _img_dest=img_dest):
                    tmp = tempfile.NamedTemporaryFile(suffix="_" + _fname, delete=False)
                    tmp.close()
                    try:
                        urllib.request.urlretrieve(_url, tmp.name)
                        execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir], silent=True)
                        result = execute_hdf_monkey("put", image_path,
                                                   extra_argv=[tmp.name.replace("\\", "/"), _img_dest])
                        if result.returncode != 0:
                            raise RuntimeError(f"hdfmonkey put failed (rc={result.returncode})")
                    finally:
                        try:
                            os.unlink(tmp.name)
                        except OSError:
                            pass
                    return _img_dest

                def _ok(dest, _f=fname):
                    pending["ok"] += 1
                    zxdb_set_status(f"Sent {_f} → image:{dest}")
                    _maybe_finish()

                def _err(e, _f=fname):
                    pending["ko"] += 1
                    zxdb_set_status(f"Failed {_f}: {e[1]}")
                    _maybe_finish()

                getit_run_in_thread(_dl_and_put, _ok, _err)

        def zxdb_show_downloads_overlay(title: str, downloads: list):
            if not downloads:
                zxdb_set_status("No downloadable files for this entry.")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Downloads — {title}")
            dlg.resize(1180, 460)
            v = QVBoxLayout(dlg)

            info = QLabel(
                f"<b>{len(downloads)}</b> file(s) for <b>{title}</b>. "
                f"“Download all” saves into <code>downloads\\{zxdb_sanitize_folder(title)}\\</code>."
            )
            info.setWordWrap(True)
            v.addWidget(info)

            # cols: 0-Type 1-Filename 2-Size 3-Source 4-URL 5-Avail. 6-Download 7-SD 8-NextSync
            COL_AVAIL = 5
            COL_DL    = 6
            COL_SD    = 7
            COL_NS    = 8
            tbl = QTableWidget(len(downloads), 9, dlg)
            tbl.setHorizontalHeaderLabels(["Type", "Filename", "Size", "Source", "URL", "Avail.", "", "", ""])
            tbl.verticalHeader().setVisible(False)
            tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setTextElideMode(Qt.ElideMiddle)
            tbl.horizontalHeader().setStretchLastSection(False)
            tbl.setColumnWidth(0, 160)
            tbl.setColumnWidth(2, 90)
            tbl.setColumnWidth(3, 180)
            tbl.setColumnWidth(COL_AVAIL, 52)
            tbl.setColumnWidth(COL_DL, 100)
            tbl.setColumnWidth(COL_SD, 140)
            tbl.setColumnWidth(COL_NS, 160)
            if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                for _c in (COL_SD, COL_NS):
                    tbl.setColumnWidth(_c, 0)
                    tbl.setColumnHidden(_c, True)
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
            tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

            folder_root = os.path.abspath(os.path.join("downloads", zxdb_sanitize_folder(title)))
            _ns_base_dlg = _zxdb_resolve_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)

            # Per-row availability: None=pending, True=ok, False=404/error
            _avail: list = [None] * len(downloads)

            def _set_avail_cell(row: int, ok: bool):
                item = QTableWidgetItem("✅" if ok else "❌")
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(Qt.darkGreen if ok else Qt.red)
                item.setToolTip("File is available" if ok else "File returned 404 / unreachable")
                _avail[row] = ok
                tbl.setItem(row, COL_AVAIL, item)
                _active_cols = [COL_DL] if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS else [COL_DL, COL_SD, COL_NS]
                for _col in _active_cols:
                    btn_w = tbl.cellWidget(row, _col)
                    if btn_w is not None:
                        btn_w.setEnabled(ok)

            def _check_url(row: int, url: str):
                def _fn():
                    return _http_head_ok_with_retry(
                        url, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=10
                    )
                def _on_ok(result):
                    _set_avail_cell(row, bool(result))
                def _on_err(_):
                    _set_avail_cell(row, False)
                getit_run_in_thread(_fn, _on_ok, _on_err)

            def _make_dl_handler(d):
                def _go():
                    fname = d.get("filename") or os.path.basename(
                        urllib.parse.urlparse(d.get("url", "")).path
                    ) or "file.bin"
                    save_path = os.path.join(folder_root, fname)
                    zxdb_set_status(f"Downloading {fname}…")
                    def _ok(p):
                        zxdb_set_status(f"Saved {fname}  ↗ open folder", open_path=os.path.dirname(os.path.abspath(p)))
                    def _err(e):
                        zxdb_set_status(f"Download error: {e[1]}")
                    zxdb_download_to_path(d.get("url", ""), save_path, _ok, _err)
                return _go

            def _make_sd_handler(d):
                def _go():
                    if not right_disk_image_explorer_content or not self.right_disk_image_path:
                        zxdb_set_status("Please load a disk image first (SD Card tab).")
                        return
                    _zxdb_send_to_image(title, [d])
                return _go

            def _make_ns_handler(d):
                def _go():
                    def _after(_folder):
                        QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                    _zxdb_send_to_path(title, [d], _ns_base_dlg, _after)
                return _go

            for row, d in enumerate(downloads):
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or ""
                tbl.setItem(row, 0, QTableWidgetItem(d.get("type") or d.get("format") or ""))
                tbl.setItem(row, 1, QTableWidgetItem(fname))
                tbl.setItem(row, 2, QTableWidgetItem(zxdb_human_size(d.get("size"))))
                tbl.setItem(row, 3, QTableWidgetItem(d.get("source") or ""))
                url_text = d.get("url", "") or ""
                url_item = QTableWidgetItem(url_text)
                url_item.setToolTip(url_text)
                tbl.setItem(row, 4, url_item)
                # Availability placeholder until HEAD check completes
                avail_item = QTableWidgetItem("⏳")
                avail_item.setTextAlignment(Qt.AlignCenter)
                avail_item.setToolTip("Checking availability…")
                tbl.setItem(row, COL_AVAIL, avail_item)
                # Action buttons disabled until availability is confirmed
                btn = QPushButton("Download")
                btn.setEnabled(False)
                btn.clicked.connect(_make_dl_handler(d))
                tbl.setCellWidget(row, COL_DL, btn)

                if ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                    sd_btn = QPushButton("Send to SD Card")
                    sd_btn.setEnabled(False)
                    sd_btn.clicked.connect(_make_sd_handler(d))
                    tbl.setCellWidget(row, COL_SD, sd_btn)

                    ns_btn = QPushButton("Send via NextSync")
                    ns_btn.setEnabled(False)
                    ns_btn.clicked.connect(_make_ns_handler(d))
                    tbl.setCellWidget(row, COL_NS, ns_btn)

            v.addWidget(tbl, 1)

            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            dl_all_btn = QPushButton(f"Download all → downloads\\{zxdb_sanitize_folder(title)}")
            sd_all_btn = QPushButton("Send all to SD Card")
            ns_all_btn = QPushButton("Send all via NextSync")
            close_btn  = QPushButton("Close")
            btn_row.addWidget(dl_all_btn)
            if ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                btn_row.addWidget(sd_all_btn)
                btn_row.addWidget(ns_all_btn)
            btn_row.addWidget(close_btn)
            v.addLayout(btn_row)

            close_btn.clicked.connect(dlg.accept)

            def _eligible():
                return [d for i, d in enumerate(downloads) if _avail[i] is not False]

            def _send_all_sd():
                if not right_disk_image_explorer_content or not self.right_disk_image_path:
                    zxdb_set_status("Please load a disk image first (SD Card tab).")
                    return
                items = _eligible()
                if not items:
                    zxdb_set_status("All files are unavailable (404).")
                    return
                _zxdb_send_to_image(title, items)

            def _send_all_ns():
                items = _eligible()
                if not items:
                    zxdb_set_status("All files are unavailable (404).")
                    return
                def _after(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                _zxdb_send_to_path(title, items, _ns_base_dlg, _after)

            sd_all_btn.clicked.connect(_send_all_sd)
            ns_all_btn.clicked.connect(_send_all_ns)

            def _download_all():
                dl_all_btn.setEnabled(False)
                dl_all_btn.setText("Downloading…")
                # Skip files confirmed unavailable (404); include pending/ok ones
                eligible = [d for i, d in enumerate(downloads) if _avail[i] is not False]
                if not eligible:
                    dl_all_btn.setText("Nothing to download")
                    zxdb_set_status("All files are unavailable (404).")
                    return
                pending = {"n": len(eligible), "ok": 0, "ko": 0}

                def _maybe_finish():
                    if pending["ok"] + pending["ko"] >= pending["n"]:
                        dl_all_btn.setText(
                            f"Done — {pending['ok']} ok, {pending['ko']} failed"
                        )
                        if pending["ok"] > 0:
                            zxdb_set_status(
                                f"Downloaded {pending['ok']}/{pending['n']} file(s) into {folder_root}  ↗ open folder",
                                open_path=folder_root
                            )
                        else:
                            zxdb_set_status(
                                f"All {pending['n']} download(s) failed — check the URLs"
                            )

                for d in eligible:
                    fname = d.get("filename") or os.path.basename(
                        urllib.parse.urlparse(d.get("url", "")).path
                    ) or "file.bin"
                    save_path = os.path.join(folder_root, fname)
                    def _ok(p, _f=fname):
                        pending["ok"] += 1
                        zxdb_set_status(f"Saved {_f}")
                        _maybe_finish()
                    def _err(e, _f=fname):
                        pending["ko"] += 1
                        zxdb_set_status(f"Failed {_f}: {e[1]}")
                        _maybe_finish()
                    zxdb_download_to_path(d.get("url", ""), save_path, _ok, _err)

            dl_all_btn.clicked.connect(_download_all)

            # Fire HEAD checks for every URL now that the table and callbacks are ready
            avail_check_enabled = getattr(self, "settings_avail_check_checkbox", None)
            avail_check_enabled = avail_check_enabled is not None and avail_check_enabled.isChecked()
            if avail_check_enabled:
                for row, d in enumerate(downloads):
                    url_to_check = d.get("url", "")
                    if url_to_check:
                        _check_url(row, url_to_check)
                    else:
                        _set_avail_cell(row, False)
            else:
                # Setting is off — enable all Download buttons immediately, hide placeholders
                for row in range(len(downloads)):
                    avail_item = tbl.item(row, COL_AVAIL)
                    if avail_item:
                        avail_item.setText("")
                        avail_item.setToolTip("Availability check disabled in Settings")
                    for _col in (COL_DL, COL_SD, COL_NS):
                        btn_w = tbl.cellWidget(row, _col)
                        if btn_w is not None:
                            btn_w.setEnabled(True)

            _ticker_lbl, _ticker_timer = _make_disclaimer_ticker(dlg)
            v.addWidget(_ticker_lbl)

            dlg.exec()

        def zxdb_on_table_context_menu(pos):
            item = self.zxdb_results_table.itemAt(pos)
            if item is None:
                return
            row = self.zxdb_results_table.row(item)
            id_item    = self.zxdb_results_table.item(row, 0)
            title_item = self.zxdb_results_table.item(row, 1)
            if not id_item:
                return
            eid   = id_item.text()
            title = title_item.text() if title_item else eid
            entry = id_item.data(Qt.UserRole) or {}
            kind  = (entry.get("_kind") or "game").lower()

            # Make sure the row is selected so the detail is loaded for it.
            self.zxdb_results_table.selectRow(row)

            menu = QMenu(self.zxdb_results_table)

            if kind == "magazine":
                mag_name = entry.get("_name") or title
                act_fetch_mag   = menu.addAction("Fetch single magazine by name")
                act_all_issues  = menu.addAction("Retrieve all issues")
                act_fetch_issue = menu.addAction("Fetch issue info for this magazine")
                act_dl_issue    = menu.addAction("Download content")
                # Only enable download if we already have files loaded for this row
                has_downloads = (
                    self._zxdb_selected_id == (entry.get("id") or mag_name)
                    or self._zxdb_selected_title.startswith(mag_name + " #")
                ) and bool(self._zxdb_selected_downloads)
                act_dl_issue.setEnabled(has_downloads)
                action = menu.exec(self.zxdb_results_table.viewport().mapToGlobal(pos))
                if action is None:
                    return

                if action is act_dl_issue:
                    zxdb_show_downloads_overlay(
                        self._zxdb_selected_title or mag_name,
                        self._zxdb_selected_downloads,
                    )
                    return

                if action is act_all_issues:
                    if self._zxdb_magazine_issues and self._zxdb_selected_title == mag_name:
                        _zxdb_open_issues_dialog(mag_name, self._zxdb_magazine_issues)
                    else:
                        zxdb_set_status(f"Loading issues for '{mag_name}'…")
                        def _fn_all():
                            payload = zxdb_fetch_json(f"/magazines/{urllib.parse.quote(mag_name)}")
                            src = payload.get("_source", payload) if isinstance(payload, dict) else {}
                            return src.get("issues") or []
                        def _on_ok_all(issues):
                            self._zxdb_magazine_issues = issues
                            _zxdb_open_issues_dialog(mag_name, issues)
                        def _on_err_all(err):
                            zxdb_set_status(f"Error loading issues: {err[1]}")
                        self._zxdb_ctx_thread = getit_run_in_thread(_fn_all, _on_ok_all, _on_err_all)
                    return

                if action is act_fetch_mag:
                    zxdb_set_status(f"Fetching magazine '{mag_name}'…")

                    def _fn_mag():
                        payload = zxdb_fetch_json(f"/magazines/{urllib.parse.quote(mag_name)}")
                        if isinstance(payload, dict) and "_source" in payload:
                            wrapped = {"hits": {"hits": [payload], "total": {"value": 1}}}
                        elif isinstance(payload, list):
                            wrapped = {"hits": {"hits": payload, "total": {"value": len(payload)}}}
                        else:
                            wrapped = payload
                        entries = _zxdb_parse_magazine_list(wrapped)
                        return ("magazines", entries, len(entries), 1, 1)

                    def _on_ok_mag(data):
                        kind2, entries, total, pg, total_pages = data
                        zxdb_populate_results(entries, pg, total_pages, kind2)
                        zxdb_set_status(f"Loaded magazine '{mag_name}'")

                    def _on_err_mag(err):
                        zxdb_set_status(f"Magazine error: {err[1]}")

                    self._zxdb_ctx_thread = getit_run_in_thread(_fn_mag, _on_ok_mag, _on_err_mag)

                elif action is act_fetch_issue:
                    issue_id, ok = QInputDialog.getText(
                        self, "Fetch Issue", f"Issue number for '{mag_name}':"
                    )
                    if not ok or not issue_id.strip():
                        return
                    issue_id = issue_id.strip()
                    zxdb_set_status(f"Fetching issue {issue_id} of '{mag_name}'…")

                    def _fn_issue():
                        return zxdb_fetch_json(
                            f"/magazines/{urllib.parse.quote(mag_name)}"
                            f"/issues/{urllib.parse.quote(issue_id)}"
                        )

                    def _on_ok_issue(payload):
                        _zxdb_clear_detail_rows()
                        src = payload if isinstance(payload, dict) else {}
                        _zxdb_add_row("Magazine:", mag_name)
                        _zxdb_add_row("Issue:",    issue_id)
                        for key, lbl in (
                            ("date_year",  "Year"),
                            ("date_month", "Month"),
                            ("volume",     "Volume"),
                            ("number",     "Number"),
                        ):
                            v = src.get(key)
                            if v is not None:
                                _zxdb_add_row(f"{lbl}:", str(v))
                        # Build downloads list from files
                        downloads = []
                        shots = []
                        for f in (src.get("files") or []):
                            if not isinstance(f, dict):
                                continue
                            link = f.get("file_link") or ""
                            if not link:
                                continue
                            url = link if link.startswith("http") else "https://spectrumcomputing.co.uk" + link
                            ftype = f.get("filetype") or ""
                            downloads.append({
                                "url":    url,
                                "type":   ftype,
                                "format": ftype,
                                "size":   f.get("file_size"),
                                "source": f.get("comments") or "",
                            })
                            if "cover" in ftype.lower() or "magazine" in ftype.lower():
                                shots.append({"url": url, "type": ftype})
                        # Store downloads so the Download action and button can use them
                        self._zxdb_selected_downloads = downloads
                        self._zxdb_selected_title = f"{mag_name} #{issue_id}"
                        self._zxdb_selected_id = f"{mag_name}:{issue_id}"
                        self.zxdb_download_button.setEnabled(bool(downloads))
                        if shots:
                            zxdb_start_slideshow(shots)
                        else:
                            self.zxdb_screenshot_label.setText("No cover image")
                        contents = src.get("contents") or src.get("articles") or []
                        n_files = len(downloads)
                        zxdb_set_status(
                            f"Issue {issue_id} of '{mag_name}'"
                            + (f"  |  {len(contents)} item(s)" if contents else "")
                            + (f"  |  {n_files} downloadable file(s)" if n_files else "")
                        )

                    def _on_err_issue(err):
                        zxdb_set_status(f"Issue error: {err[1]}")

                    self._zxdb_ctx_thread = getit_run_in_thread(_fn_issue, _on_ok_issue, _on_err_issue)

            else:
                # ---- Resolve "Send to" destinations ----
                _img_path     = self.right_disk_image_path or ""
                _img_label    = (generate_disk_file_path().rstrip("/") + "/" + zxdb_sanitize_folder(title)
                                 ) if _img_path else "(no image loaded)"
                _sd_dest      = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
                _ns_base      = _zxdb_resolve_base_path(self.left_file_nextsync_explorer_selection_full_filename_path)
                _safe_title   = zxdb_sanitize_folder(title)
                _ns_dest      = os.path.join(_ns_base, _safe_title)

                act_download  = menu.addAction("Download content")
                act_mlt       = menu.addAction("More like this")
                menu.addSeparator()
                act_send_sd   = menu.addAction(f"Send to SD card (image)  →  {_sd_dest}")
                act_send_sd.setEnabled(bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content))
                act_send_ns   = menu.addAction(f"Send using NextSync  →  {_ns_dest}")
                if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                    act_download.setVisible(False)
                    act_send_sd.setVisible(False)
                    act_send_ns.setVisible(False)
                menu.addSeparator()
                _web_url = zxdb_entry_website_url(eid)
                act_open_web = menu.addAction("Open on website (zxinfo.dk)")
                act_open_web.setEnabled(bool(_web_url))
                action = menu.exec(self.zxdb_results_table.viewport().mapToGlobal(pos))
                if action is None:
                    return
                if action is act_open_web:
                    if _web_url:
                        try:
                            webbrowser.open(_web_url, new=2)
                        except Exception:
                            pass
                    return

                # ---- helper: fetch downloads then send to a path ----
                def _fetch_and_send(dest_root, post_action=None):
                    if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                        _zxdb_send_to_path(self._zxdb_selected_title or title,
                                           self._zxdb_selected_downloads,
                                           dest_root, post_action)
                        return
                    zxdb_set_status(f"Loading {eid}…")
                    def _fn():
                        payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                        return zxdb_parse_game_detail(payload)
                    def _on_ok(detail, _dr=dest_root, _pa=post_action):
                        zxdb_populate_detail(detail)
                        shots = detail.get("screenshots") or []
                        if not shots and detail.get("screenshot_url"):
                            shots = [{"url": detail["screenshot_url"], "type": ""}]
                        zxdb_start_slideshow(shots)
                        dls = detail.get("downloads", []) or []
                        if not dls:
                            zxdb_set_status("No downloadable files for this entry.")
                            return
                        _zxdb_send_to_path(detail.get("title") or title, dls, _dr, _pa)
                    def _on_err(err):
                        zxdb_set_status(f"Detail error: {err[1]}")
                    self._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

                if action is act_download:
                    # If detail for this row is already loaded, show the overlay immediately.
                    if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                        zxdb_show_downloads_overlay(self._zxdb_selected_title or title,
                                                    self._zxdb_selected_downloads)
                        return

                    # Otherwise load the detail first, then show the overlay.
                    zxdb_set_status(f"Loading {eid}…")

                    def _fn():
                        payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                        return zxdb_parse_game_detail(payload)

                    def _on_ok(detail):
                        zxdb_populate_detail(detail)
                        shots = detail.get("screenshots") or []
                        if not shots and detail.get("screenshot_url"):
                            shots = [{"url": detail["screenshot_url"], "type": ""}]
                        zxdb_start_slideshow(shots)
                        downloads = detail.get("downloads", []) or []
                        if not downloads:
                            zxdb_set_status("No downloadable files for this entry.")
                            return
                        zxdb_show_downloads_overlay(detail.get("title") or title, downloads)

                    def _on_err(err):
                        zxdb_set_status(f"Detail error: {err[1]}")

                    self._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

                elif action is act_send_sd:
                    def _fetch_and_send_to_image():
                        if self._zxdb_selected_id == eid and self._zxdb_selected_downloads:
                            _zxdb_send_to_image(self._zxdb_selected_title or title,
                                                self._zxdb_selected_downloads)
                            return
                        zxdb_set_status(f"Loading {eid}…")
                        def _fn_sd():
                            payload = zxdb_fetch_json(f"/games/{urllib.parse.quote(eid)}")
                            return zxdb_parse_game_detail(payload)
                        def _on_ok_sd(detail):
                            zxdb_populate_detail(detail)
                            dls = detail.get("downloads", []) or []
                            if not dls:
                                zxdb_set_status("No downloadable files for this entry.")
                                return
                            _zxdb_send_to_image(detail.get("title") or title, dls)
                        def _on_err_sd(err):
                            zxdb_set_status(f"Detail error: {err[1]}")
                        self._zxdb_ctx_thread = getit_run_in_thread(_fn_sd, _on_ok_sd, _on_err_sd)
                    _fetch_and_send_to_image()

                elif action is act_send_ns:
                    def _after_ns_dl(_folder):
                        QTimer.singleShot(0, self._nextsync_start_server_fn)
                    _fetch_and_send(_ns_base, _after_ns_dl)

                elif action is act_mlt:
                    zxdb_set_status(f"Finding titles similar to '{title}'…")

                    def _fn_mlt():
                        payload = zxdb_fetch_json(
                            f"/games/morelikethis/{urllib.parse.quote(eid)}"
                            f"?mode=compact&size={ZXDB_PAGE_SIZE}"
                        )
                        entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                        for e in entries:
                            e["_kind"] = "game"
                        return ("games", entries, total, 1, total_pages)

                    def _on_ok_mlt(data):
                        kind2, entries, total, pg, total_pages = data
                        zxdb_populate_results(entries, pg, total_pages, kind2)
                        zxdb_set_status(
                            f"{len(entries)} title(s) similar to '{title}'"
                        )

                    def _on_err_mlt(err):
                        zxdb_set_status(f"More like this error: {err[1]}")

                    self._zxdb_ctx_thread = getit_run_in_thread(_fn_mlt, _on_ok_mlt, _on_err_mlt)

        self.zxdb_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.zxdb_results_table.customContextMenuRequested.connect(zxdb_on_table_context_menu)

        # ---- Fullscreen preview overlay (mirrors GetIt) ----

        zxdb_container = QWidget()
        zxdb_container.setLayout(self.zxdb_form)
        zxdb_container.setAutoFillBackground(False)
        zxdb_container.setAttribute(Qt.WA_TranslucentBackground)

        zxdb_scroll = QScrollArea()
        zxdb_scroll.setWidget(zxdb_container)
        zxdb_scroll.setWidgetResizable(True)
        zxdb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        zxdb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        zxdb_scroll.setAutoFillBackground(False)
        zxdb_scroll.setAttribute(Qt.WA_TranslucentBackground)
        zxdb_scroll.viewport().setAutoFillBackground(False)
        zxdb_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

        # Fixed search/button header above the scrollable results so the
        # vertical scroller only covers the content area (like the Unite! tab).
        zxdb_normal_widget = QWidget()
        zxdb_normal_widget.setAutoFillBackground(False)
        zxdb_normal_widget.setAttribute(Qt.WA_TranslucentBackground)
        zxdb_normal_layout = QVBoxLayout(zxdb_normal_widget)
        zxdb_normal_layout.setContentsMargins(0, 0, 0, 0)
        zxdb_normal_layout.setSpacing(0)
        zxdb_normal_layout.addWidget(self._zxdb_search_widget, 0)
        zxdb_normal_layout.addWidget(zxdb_scroll, 1)

        self._zxdb_fullscreen_pixmap = None

        zxdb_overlay = QWidget()
        zxdb_overlay.setStyleSheet("background: #000;")
        zxdb_overlay_layout = QVBoxLayout(zxdb_overlay)
        zxdb_overlay_layout.setContentsMargins(0, 0, 0, 0)
        zxdb_overlay_layout.setSpacing(0)

        zxdb_close_btn = QToolButton()
        zxdb_close_btn.setText("✕")
        zxdb_close_btn.setStyleSheet(
            "QToolButton { color: white; background: #333; border: none; font-size: 18px; padding: 4px 8px; }"
            "QToolButton:hover { background: #c00; }"
        )
        zxdb_close_bar = QHBoxLayout()
        zxdb_close_bar.setContentsMargins(4, 4, 4, 0)
        zxdb_close_bar.addWidget(zxdb_close_btn, 0)
        zxdb_close_bar.addStretch()
        zxdb_close_bar_widget = QWidget()
        zxdb_close_bar_widget.setLayout(zxdb_close_bar)
        zxdb_overlay_layout.addWidget(zxdb_close_bar_widget, 0)

        self.zxdb_fullscreen_label = QLabel()
        self.zxdb_fullscreen_label.setAlignment(Qt.AlignCenter)
        self.zxdb_fullscreen_label.setStyleSheet("background: #000;")
        self.zxdb_fullscreen_label.setCursor(Qt.PointingHandCursor)
        zxdb_overlay_layout.addWidget(self.zxdb_fullscreen_label, 1)

        _fs_nav_style = (
            "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
            " font-size: 32px; font-weight: bold; padding: 4px 10px; }"
            "QToolButton:hover { background: rgba(0,0,0,220); }"
        )
        self.zxdb_fs_prev_btn = QToolButton(zxdb_overlay)
        self.zxdb_fs_prev_btn.setText("<")
        self.zxdb_fs_prev_btn.setStyleSheet(_fs_nav_style)
        self.zxdb_fs_prev_btn.setVisible(False)
        self.zxdb_fs_prev_btn.raise_()

        self.zxdb_fs_next_btn = QToolButton(zxdb_overlay)
        self.zxdb_fs_next_btn.setText(">")
        self.zxdb_fs_next_btn.setStyleSheet(_fs_nav_style)
        self.zxdb_fs_next_btn.setVisible(False)
        self.zxdb_fs_next_btn.raise_()

        def _zxdb_reposition_fs_btns():
            ow = zxdb_overlay.width()
            oh = zxdb_overlay.height()
            bh = self.zxdb_fs_prev_btn.sizeHint().height()
            by = (oh - bh) // 2
            self.zxdb_fs_prev_btn.move(8, by)
            bw = self.zxdb_fs_next_btn.sizeHint().width()
            self.zxdb_fs_next_btn.move(ow - bw - 8, by)

        self._zxdb_reposition_fs_btns = _zxdb_reposition_fs_btns
        self.zxdb_fs_prev_btn.clicked.connect(_zxdb_nav_prev)
        self.zxdb_fs_next_btn.clicked.connect(_zxdb_nav_next)

        self._zxdb_stack = QStackedWidget()
        self._zxdb_stack.setAutoFillBackground(False)
        self._zxdb_stack.setAttribute(Qt.WA_TranslucentBackground)
        self._zxdb_stack.addWidget(zxdb_normal_widget)
        self._zxdb_stack.addWidget(zxdb_overlay)
        self._zxdb_stack.setCurrentIndex(0)

        def _zxdb_show_fullscreen():
            px = self.zxdb_screenshot_label.pixmap()
            if px is None or px.isNull():
                return
            self._zxdb_fullscreen_pixmap = px
            self._zxdb_stack.setCurrentIndex(1)
            _zxdb_resize_fullscreen()
            self._zxdb_reposition_fs_btns()
            zxdb_update_nav_buttons()

        def _zxdb_hide_fullscreen():
            self._zxdb_stack.setCurrentIndex(0)
            zxdb_update_nav_buttons()
        self._hide_fullscreen_zxdb = _zxdb_hide_fullscreen

        def _zxdb_resize_fullscreen():
            px = self._zxdb_fullscreen_pixmap
            if px and not px.isNull():
                sz = self.zxdb_fullscreen_label.size()
                self.zxdb_fullscreen_label.setPixmap(
                    px.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            self._zxdb_reposition_fs_btns()

        zxdb_close_btn.clicked.connect(_zxdb_hide_fullscreen)
        self.zxdb_fullscreen_label.mousePressEvent = lambda e: _zxdb_hide_fullscreen()

        self._zxdb_dbl_filter = _DblClickFilter(_zxdb_show_fullscreen)
        self.zxdb_screenshot_label.installEventFilter(self._zxdb_dbl_filter)
        self.zxdb_screenshot_label.setCursor(Qt.PointingHandCursor)

        # Expose handler for tab activation
        def zxdb_on_tab_activated():
            if self._zxdb_loaded_once or self._zxdb_search_loading:
                return
            self._zxdb_loaded_once = True
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            def _zxdb_random_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, self.zxdb_results_table.rowCount())
            zxdb_run_random(_zxdb_random_done)
            # Allow autocomplete only after the initial load has been kicked off
            # so that config-restored search text doesn't trigger background
            # by-letter fetches before the first render completes.
            self._zxdb_ac_ready = True

        self._zxdb_on_tab_activated = zxdb_on_tab_activated

        # -----------------------------------------------------------------------
        # zxART UI construction (zxart.ee API)
        # -----------------------------------------------------------------------

        self.zxart_form = QFormLayout()
        self.zxart_form.setContentsMargins(4, 4, 4, 4)

        # --- Search row ---
        zxart_search_row = QHBoxLayout()
        self.zxart_search_input = QLineEdit()
        self.zxart_search_input.setPlaceholderText("Search zxART productions... (leave empty to browse latest)")
        self.zxart_search_input.setMinimumWidth(280)
        zxart_search_row.addWidget(self.zxart_search_input)

        self._zxart_search_valid_lbl = QLabel()
        self._zxart_search_valid_lbl.setVisible(False)
        zxart_search_row.addWidget(self._zxart_search_valid_lbl)

        self.zxart_search_button = QPushButton(_zxart_tr("Search"))
        zxart_search_row.addWidget(self.zxart_search_button)

        self.zxart_latest_button = QPushButton(_zxart_tr("Latest"))
        self.zxart_latest_button.setToolTip(
            "Show the most recent zxART productions/pictures (sorted by date)."
        )
        zxart_search_row.addWidget(self.zxart_latest_button)

        self.zxart_random_button = QPushButton(_zxart_tr("Random"))
        self.zxart_random_button.setToolTip(
            "Pick a random page of zxART productions and show its entries."
        )
        zxart_search_row.addWidget(self.zxart_random_button)

        self.zxart_mode_combo = QComboBox()
        for _lbl, _key in (
            ("Productions",  "prods"),
            ("By letter",    "byletter"),
            ("Pictures",     "pictures"),
        ):
            self.zxart_mode_combo.addItem(_zxart_tr(_lbl), _key)
        self.zxart_mode_combo.setCurrentIndex(0)
        self.zxart_mode_combo.setToolTip("Browse mode")
        zxart_search_row.addWidget(self.zxart_mode_combo)

        self.zxart_letter_combo = QComboBox()
        for _lbl in ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
            self.zxart_letter_combo.addItem(_lbl, _lbl.lower())
        self.zxart_letter_combo.setToolTip("Pick a letter")
        self.zxart_letter_combo.setVisible(False)
        zxart_search_row.addWidget(self.zxart_letter_combo)

        self.zxart_page_text_label = QLabel(_zxart_tr("Page:"))
        zxart_search_row.addWidget(self.zxart_page_text_label)
        self.zxart_page_label = QLabel("1")
        self.zxart_page_label.setMinimumWidth(24)
        zxart_search_row.addWidget(self.zxart_page_label)

        self.zxart_prev_button = QPushButton(_zxart_tr("< Prev"))
        self.zxart_prev_button.setEnabled(False)
        zxart_search_row.addWidget(self.zxart_prev_button)

        self.zxart_next_button = QPushButton(_zxart_tr("Next >"))
        self.zxart_next_button.setEnabled(False)
        zxart_search_row.addWidget(self.zxart_next_button)

        self.zxart_view_text_label = QLabel(_zxart_tr("View:"))
        zxart_search_row.addWidget(self.zxart_view_text_label)
        self.zxart_view_combo = QComboBox()
        self.zxart_view_combo.addItem(_zxart_tr("Table"),   "table")
        self.zxart_view_combo.addItem(_zxart_tr("Gallery"), "gallery")
        self.zxart_view_combo.setToolTip(
            "Switch between the classic table view and the picture (gallery) view.\n"
            "Persisted across sessions in the config file."
        )
        zxart_search_row.addWidget(self.zxart_view_combo)

        self.zxart_language_text_label = QLabel(_zxart_tr("Language:"))
        zxart_search_row.addWidget(self.zxart_language_text_label)
        self.zxart_language_combo = QComboBox()
        for _lbl, _code in ZXART_LANGUAGE_CHOICES:
            self.zxart_language_combo.addItem(_lbl, _code)
        self.zxart_language_combo.setToolTip(
            "zxART catalog display language.\n"
            "Persisted across sessions in the config file."
        )
        zxart_search_row.addWidget(self.zxart_language_combo)

        self.zxart_status_label = QLabel("")
        self.zxart_status_label.setCursor(Qt.ArrowCursor)
        self._zxart_status_open_path = None

        def _zxart_status_mouse_press(ev):
            if ev.button() == Qt.LeftButton and self._zxart_status_open_path:
                p = self._zxart_status_open_path
                if os.path.isfile(p):
                    p = os.path.dirname(p)
                try:
                    os.makedirs(p, exist_ok=True)
                except OSError:
                    pass
                if not os.path.isdir(p):
                    return
                if sys.platform == "win32":
                    os.startfile(p)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", p])
                else:
                    subprocess.Popen(["xdg-open", p])

        self.zxart_status_label.mousePressEvent = _zxart_status_mouse_press
        zxart_search_row.addWidget(self.zxart_status_label, 1)

        zxart_search_widget = QWidget()
        zxart_search_widget.setLayout(zxart_search_row)
        # Keep the search/button bar fixed above the scroll area (see the
        # _zxart_stack assembly) so the vertical scroller only covers the
        # results/details area, matching the Unite! tab.
        self._zxart_search_widget = zxart_search_widget

        # --- Results table + screenshot/download column ---
        self.zxart_results_table = QTableWidget(0, 6)
        self.zxart_results_table.setHorizontalHeaderLabels(
            [_zxart_tr(h) for h in ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"]]
        )
        self.zxart_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.zxart_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.zxart_results_table.horizontalHeader().setStretchLastSection(True)
        self.zxart_results_table.setMinimumHeight(220)
        self.zxart_results_table.setMaximumWidth(1000)
        self.zxart_results_table.setColumnWidth(0, 80)
        self.zxart_results_table.setColumnWidth(1, 280)
        self.zxart_results_table.setColumnWidth(2, 60)
        self.zxart_results_table.setColumnWidth(3, 180)
        self.zxart_results_table.setColumnWidth(4, 120)

        self.zxart_screenshot_label = QLabel()
        self.zxart_screenshot_label.setFixedSize(256, 192)
        self.zxart_screenshot_label.setAlignment(Qt.AlignCenter)
        self.zxart_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
        self.zxart_screenshot_label.setText(_zxart_tr("No preview"))
        self.zxart_screenshot_label.setToolTip("Double-click to enlarge")

        zxart_preview_container = QWidget()
        zxart_preview_container.setFixedSize(256, 192)
        self.zxart_screenshot_label.setParent(zxart_preview_container)
        self.zxart_screenshot_label.move(0, 0)

        _zxart_nav_btn_style = (
            "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
            " font-size: 20px; font-weight: bold; padding: 2px 6px; }"
            "QToolButton:hover { background: rgba(0,0,0,210); }"
        )
        self.zxart_prev_shot_btn = QToolButton(zxart_preview_container)
        self.zxart_prev_shot_btn.setText("<")
        self.zxart_prev_shot_btn.setStyleSheet(_zxart_nav_btn_style)
        self.zxart_prev_shot_btn.setVisible(False)
        self.zxart_prev_shot_btn.raise_()

        self.zxart_next_shot_btn = QToolButton(zxart_preview_container)
        self.zxart_next_shot_btn.setText(">")
        self.zxart_next_shot_btn.setStyleSheet(_zxart_nav_btn_style)
        self.zxart_next_shot_btn.setVisible(False)
        self.zxart_next_shot_btn.raise_()

        def _zxart_reposition_shot_btns():
            h = zxart_preview_container.height()
            bh = self.zxart_prev_shot_btn.sizeHint().height()
            by = (h - bh) // 2
            self.zxart_prev_shot_btn.move(2, by)
            bw = self.zxart_next_shot_btn.sizeHint().width()
            self.zxart_next_shot_btn.move(zxart_preview_container.width() - bw - 2, by)

        _zxart_reposition_shot_btns()

        self.zxart_download_button = QPushButton(_zxart_tr("Download File"))
        self.zxart_download_button.setEnabled(False)

        zxart_right_col = QVBoxLayout()
        _zxart_link_label = QLabel('<a href="https://zxart.ee/">https://zxart.ee/</a>')
        _zxart_link_label.setOpenExternalLinks(True)
        _zxart_link_label.setTextFormat(Qt.RichText)
        _zxart_link_label.setAlignment(Qt.AlignCenter)
        zxart_right_col.addWidget(_zxart_link_label)
        # Visibility is controlled by _zxart_apply_view_mode (shown in Table, hidden in Gallery)
        zxart_preview_container.setVisible(False)
        zxart_right_col.addWidget(zxart_preview_container)
        self._zxart_preview_container = zxart_preview_container

        self.zxart_download_button.setVisible(False)
        zxart_right_col.addWidget(self.zxart_download_button)
        self._zxart_preview_download_btn = self.zxart_download_button
        zxart_right_col.addStretch()
        zxart_right_widget = QWidget()
        zxart_right_widget.setLayout(zxart_right_col)

        zxart_table_row = QHBoxLayout()

        self.zxart_view_stack = QStackedWidget()
        self.zxart_view_stack.addWidget(self.zxart_results_table)  # index 0

        def _zxart_gallery_title(e):
            title = (e.get("title") or e.get("id") or "")[:80]
            src = e.get("_source") or {}
            # Prods expose "votes" (avg 0–5); pictures expose "rating" (0–10).
            rating_val = src.get("votes")
            if rating_val in (None, "", 0, "0"):
                rating_val = src.get("rating")
            stars = _gallery_stars(rating_val) if rating_val not in (None, "") else ""
            if stars:
                # Title gets rich text so we can show stars on a second line.
                safe = (title.replace("&", "&amp;")
                              .replace("<", "&lt;")
                              .replace(">", "&gt;"))
                return f"{safe}<br><span style='color:#ffcc44;'>{stars}</span>"
            return title
        def _zxart_gallery_info(e):
            parts = []
            if e.get("author"):  parts.append(e["author"])
            if e.get("year"):    parts.append(str(e["year"]))
            if e.get("machine"): parts.append(e["machine"])
            if e.get("genre"):   parts.append(e["genre"])
            return " · ".join(parts)

        def _zxart_fav_table_info(e):
            """Richer info string for the Favorites table / gallery: resolves
            Produced by / Published by.

            Runs on the UI thread, so it must NOT perform any network I/O. It
            reads only the in-memory caches; on a cold cache it returns a
            best-effort string and schedules a background warm-up so the value
            improves on the next repopulate."""
            src  = e.get("_source") or {}
            kind = (e.get("_kind") or "").lower()
            if kind == "zxart_picture" or not src:
                # Pictures and entries without cached source: fall back to author · year
                parts = []
                if e.get("author"): parts.append(str(e["author"]))
                if e.get("year"):   parts.append(str(e["year"]))
                return " · ".join(parts)
            text, complete = _zxart_author_col_cached(e)
            if not complete:
                # Warm the caches off the UI thread; no cell handle to refresh
                # here, so the resolved value surfaces on the next repopulate.
                getit_run_in_thread(
                    lambda _e=e: _zxart_prefetch_names_for_entries([_e]),
                    lambda _r: None, lambda _err: None)
            if text:
                return text
            parts = []
            if e.get("author"): parts.append(str(e["author"]))
            if e.get("year"):   parts.append(str(e["year"]))
            return " · ".join(parts)

        def _zxart_table_author_col(e):
            """Resolve 'Produced by / Published by' for the table column.
            Mirrors the logic of _zxart_fav_table_info: resolves group and
            publisher IDs via the API (with process-level caching) so the
            column shows real names instead of raw counts like '3 author(s)'."""
            src  = e.get("_source") or {}
            kind = (e.get("_kind") or "").lower()
            if kind == "zxart_picture":
                return e.get("author", "")
            # 1. Groups: prefer direct name strings from the API response,
            #    then resolve IDs via the API (cached after first lookup).
            groups = [str(g) for g in (src.get("groups") or []) if g]
            if not groups:
                groups = [n for n in [_zxart_resolve_group_name(gid)
                                      for gid in (src.get("groupsIds") or [])] if n]
            produced_by = ", ".join(groups)
            # 2. Authors: direct name strings when no groups
            if not produced_by:
                authors = [str(a) for a in (src.get("authors") or []) if a]
                if authors:
                    return ", ".join(authors)
            # 3. Publishers: resolve via the API (publishers reuse group namespace)
            pub_ids = src.get("publishersIds") or []
            published_by = _zxart_resolve_publisher_names(pub_ids)
            if not published_by:
                published_by = _zxart_scrape_publishers_from_prod_url(
                    str(src.get("url") or "")
                )
            parts = []
            if produced_by:  parts.append(f"Produced by: {produced_by}")
            if published_by: parts.append(f"Published by: {published_by}")
            return " · ".join(parts) if parts else e.get("author", "")

        def _zxart_tooltip_getter(e):
            src = e.get("_source") or {}
            lines = []
            if e.get("title"):   lines.append(f"Title: {e['title']}")
            if e.get("year"):    lines.append(f"Year: {e['year']}")
            if e.get("author"):  lines.append(f"Author: {e['author']}")
            if e.get("machine"): lines.append(f"Machine: {e['machine']}")
            if e.get("genre"):   lines.append(f"Genre: {e['genre']}")
            party = src.get("partyName") or src.get("party")
            if party:            lines.append(f"Party: {party}")
            return _build_tooltip_text(lines)

        def _zxart_thumb_fetch(entry, set_pixmap, set_screenshots, set_tags=None):
            src = entry.get("_source") or {}
            kind = (entry.get("_kind") or "").lower()
            # Pictures expose imageUrl directly; prods carry imagesUrls list.
            # zxArt's API guarantees these are image URLs (even when the
            # path has no extension like /file/id:123/), so we trust the
            # upstream field rather than re-filtering by extension.
            urls = []
            if kind == "zxart_picture":
                u = src.get("imageUrl") or src.get("originalUrl") or ""
                if u:
                    urls.append(u)
            else:
                for u in (src.get("imagesUrls") or []):
                    if u:
                        urls.append(u)

            # Apply tags we can derive immediately (pictures + any cached
            # release info), then start an async release lookup for prods
            # so we can show hardware/format badges like on zxart.ee.
            if set_tags is not None:
                try:
                    set_tags(_gallery_extract_tags(entry))
                except Exception:
                    pass
                if kind != "zxart_picture" and not src.get("releases"):
                    pid = str(entry.get("id") or "")
                    if pid:
                        def _rel_fn(_pid=pid):
                            resp = zxart_fetch_json(
                                f"/action:filter/export:zxRelease"
                                f"/filter:zxProdId={urllib.parse.quote(_pid)}",
                                timeout=20,
                            )
                            return (resp.get("responseData") or {}).get("zxRelease") or []
                        def _rel_ok(rels, _e=entry, _st=set_tags):
                            try:
                                src2 = _e.get("_source") or {}
                                src2["releases"] = rels
                                _e["_source"] = src2
                                _st(_gallery_extract_tags(_e))
                            except Exception:
                                pass
                        getit_run_in_thread(_rel_fn, _rel_ok, lambda _e: None)

            if not urls:
                # No real picture for this entry: render a typed placeholder
                # using release formats / known download names so the cell
                # shows e.g. "TAP" or "POK" instead of a black square.
                label = "FILE"
                formats = []
                rf = src.get("releaseFormats") or []
                if isinstance(rf, list):
                    formats.extend([str(x) for x in rf if x])
                for rel in (src.get("releases") or []):
                    if not isinstance(rel, dict):
                        continue
                    v = rel.get("releaseFormat")
                    if isinstance(v, list):
                        formats.extend([str(x) for x in v if x])
                    elif v:
                        formats.append(str(v))
                    fn = rel.get("fileName") or ""
                    if fn:
                        label = zxfmt_label_for_name(fn)
                        break
                if label == "FILE" and formats:
                    label = zxfmt_label_for_name("x." + formats[0].lower())
                title = entry.get("title") or str(entry.get("id") or "")
                placeholder_url = f"placeholder://{label}/{title}"
                set_screenshots([placeholder_url])
                pm = zxfmt_make_placeholder_pixmap(label, title)
                if not pm.isNull():
                    set_pixmap(pm, placeholder_url)
                return
            set_screenshots(urls)
            def _img_fn(_u=urls[0]):
                return (_u, _http_fetch_bytes_with_retry(
                    zxart_safe_url(_u), headers={"User-Agent": ZXART_USER_AGENT}, timeout=20))
            def _img_ok(res):
                u, data = res
                if zxscr_url_is_scr(u):
                    pm = zxscr_convert_bytes_to_pixmap(
                        data, _zxscr_basename_for_url(u))
                    if pm is not None and not pm.isNull():
                        set_pixmap(pm, u)
                        return
                px = QPixmap()
                px.loadFromData(data)
                if not px.isNull():
                    set_pixmap(px, u)
            getit_run_in_thread(_img_fn, _img_ok, lambda _e: None)

        def _zxart_extra_fetch(url, on_pixmap):
            if isinstance(url, str) and url.startswith("placeholder://"):
                rest = url[len("placeholder://"):]
                label, _, sub = rest.partition("/")
                pm = zxfmt_make_placeholder_pixmap(label or "FILE", sub)
                if not pm.isNull():
                    on_pixmap(pm)
                return
            scr_url = zxscr_url_is_scr(url)
            if scr_url:
                base = _zxscr_basename_for_url(url)
                cached = _ZXSCR_PIXMAP_CACHE.get(base)
                if cached is not None and not cached.isNull():
                    on_pixmap(cached)
                    return
            def _fn(_u=url):
                return _http_fetch_bytes_with_retry(
                    zxart_safe_url(_u), headers={"User-Agent": ZXART_USER_AGENT}, timeout=20)
            def _on_ok(data, _u=url, _scr=scr_url):
                if _scr:
                    pm = zxscr_convert_bytes_to_pixmap(
                        data, _zxscr_basename_for_url(_u))
                    if pm is not None and not pm.isNull():
                        on_pixmap(pm)
                        return
                px = QPixmap()
                px.loadFromData(data)
                if not px.isNull():
                    on_pixmap(px)
            getit_run_in_thread(_fn, _on_ok, lambda _e: None)

        def _zxart_gallery_context_menu(entry, global_pos):
            pid   = entry.get("id") or ""
            title = entry.get("title") or pid
            kind  = entry.get("_kind", "zxart_prod")
            _safe_title = zxart_sanitize_folder(title)
            _img_path   = self.right_disk_image_path or ""
            _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                           ) if _img_path else "(no image loaded)"
            _sd_dest    = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base    = _zxart_resolve_base_path(self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest    = os.path.join(_ns_base, _safe_title)
            menu = QMenu()
            act_download = menu.addAction("Download content")
            menu.addSeparator()
            act_send_sd  = menu.addAction(f"Send to SD card (image)  \u2192  {_sd_dest}")
            act_send_sd.setEnabled(bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content))
            act_send_ns  = menu.addAction(f"Send using NextSync  \u2192  {_ns_dest}")
            if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                act_download.setVisible(False)
                act_send_sd.setVisible(False)
                act_send_ns.setVisible(False)
            menu.addSeparator()
            _web_url = zxart_entry_website_url(entry)
            act_open_web = menu.addAction("Open on website (zxart.ee)")
            act_open_web.setEnabled(bool(_web_url))
            action = menu.exec(global_pos)
            if action is None:
                return
            if action is act_open_web:
                if _web_url:
                    try:
                        webbrowser.open(_web_url, new=2)
                    except Exception:
                        pass
                return

            def _ensure_detail_then(callback):
                if self._zxart_selected_id == pid and self._zxart_selected_downloads:
                    callback(self._zxart_selected_title or title, self._zxart_selected_downloads)
                    return
                zxart_set_status(f"Loading {pid}\u2026")
                if kind == "zxart_picture":
                    def _fn():
                        pic_resp = zxart_fetch_json(
                            f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(pid)}"
                        )
                        pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                        pic  = pics[0] if pics else (entry.get("_source") or {})
                        image_url    = pic.get("imageUrl") or ""
                        original_url = pic.get("originalUrl") or ""
                        downloads = []
                        if original_url:
                            fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{pid}.bin"
                            downloads.append({"url": original_url, "filename": fname,
                                              "type": "original", "format": "", "size": "", "source": "zxart"})
                        if image_url and image_url != original_url:
                            fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{pid}.png"
                            downloads.append({"url": image_url, "filename": fname_img,
                                              "type": "preview (PC)", "format": "", "size": "", "source": "zxart"})
                        return (str(pic.get("title") or title), downloads)
                    def _on_ok(res, _cb=callback):
                        t2, dls = res
                        self._zxart_selected_title = t2
                        self._zxart_selected_downloads = dls
                        self.zxart_download_button.setEnabled(bool(dls))
                        _cb(t2, dls)
                    def _on_err(err):
                        zxart_set_status(f"Detail error: {err[1]}")
                    self._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
                else:
                    def _fn():
                        rel_resp = zxart_fetch_json(
                            f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(pid)}"
                        )
                        releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                        prod_resp = zxart_fetch_json(
                            f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(pid)}"
                        )
                        prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                        prod  = prods[0] if prods else {}
                        downloads = []
                        for rel in releases:
                            if not isinstance(rel, dict): continue
                            file_url  = rel.get("file") or ""
                            file_name = rel.get("fileName") or (
                                os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else "")
                            if not file_url: continue
                            downloads.append({
                                "url": file_url, "filename": file_name,
                                "type": f"{rel.get('releaseType') or ''} / {rel.get('releaseFormat') or ''}".strip(" /") or "release",
                                "format": rel.get("releaseFormat") or "",
                                "size": "", "source": rel.get("title") or "zxart",
                            })
                        return (str(prod.get("title") or title), downloads)
                    def _on_ok(res, _cb=callback):
                        t2, dls = res
                        self._zxart_selected_title = t2
                        self._zxart_selected_downloads = dls
                        self.zxart_download_button.setEnabled(bool(dls))
                        _cb(t2, dls)
                    def _on_err(err):
                        zxart_set_status(f"Detail error: {err[1]}")
                    self._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            if action is act_download:
                def _show(t, dls):
                    zxart_show_downloads_overlay(t, dls)
                _ensure_detail_then(_show)
            elif action is act_send_sd:
                def _send_sd(t, dls):
                    _zxart_send_to_image(t, dls)
                _ensure_detail_then(_send_sd)
            elif action is act_send_ns:
                def _send_ns(t, dls, _nb=_ns_base):
                    def _after(_folder):
                        QTimer.singleShot(0, self._nextsync_start_server_fn)
                    _zxart_send_to_path(t, dls, _nb, _after)
                _ensure_detail_then(_send_ns)

        def _zxart_has_image(e):
            src = e.get("_source") or {}
            kind = (e.get("_kind") or "").lower()
            if kind == "zxart_picture":
                return bool(src.get("imageUrl") or src.get("originalUrl"))
            for u in (src.get("imagesUrls") or []):
                if u:
                    return True
            return False

        self.zxart_gallery_view = GalleryView(
            rows_per_page_getter=lambda: self._gallery_rows_per_page,
            anim_mode_getter=lambda: self._gallery_anim_mode,
            cols_getter=lambda: self._gallery_cols,
            img_size_getter=lambda: self._gallery_img_size,
            thumb_fetch_cb=_zxart_thumb_fetch,
            extra_fetch_cb=_zxart_extra_fetch,
            title_getter=_zxart_gallery_title,
            info_getter=_zxart_gallery_info,
            context_menu_cb=_zxart_gallery_context_menu,
            image_predicate=_zxart_has_image,
            is_favorite_cb=lambda e: self._fav_is({**e, "_fav_source": "zxart"}),
            toggle_favorite_cb=lambda e: self._fav_toggle({**e, "_fav_source": "zxart"}),
            tooltip_getter=_zxart_tooltip_getter,
        )
        self._fav_fetchers = getattr(self, "_fav_fetchers", {})
        self._fav_fetchers["zxart"] = {
            "thumb": _zxart_thumb_fetch,
            "extra": _zxart_extra_fetch,
            "title": _zxart_gallery_title,
            "info":  _zxart_fav_table_info,
        }
        self.zxart_view_stack.addWidget(self.zxart_gallery_view)  # index 1

        zxart_table_row.addWidget(self.zxart_view_stack, 1)
        zxart_table_row.addWidget(zxart_right_widget)
        zxart_table_container = QWidget()
        zxart_table_container.setLayout(zxart_table_row)
        self.zxart_form.addRow(zxart_table_container)

        # --- Detail panel ---
        self._zxart_detail_layout = QFormLayout()
        self._zxart_detail_layout.setContentsMargins(0, 0, 0, 0)
        self._zxart_detail_rows = []

        self._zxart_detail_widget = QWidget()
        self._zxart_detail_widget.setLayout(self._zxart_detail_layout)
        # Detail widget intentionally not added to form; info shown via cell tooltips instead.

        # --- Internal state ---
        self._zxart_current_page   = 1
        self._zxart_total_pages    = 1
        self._zxart_last_query     = ""
        self._zxart_selected_id    = ""
        self._zxart_selected_title = ""
        self._zxart_selected_downloads = []
        self._zxart_search_loading = False
        # Generation token: see _getit_search_gen for rationale.
        self._zxart_search_gen = 0
        self._zxart_loaded_once    = False
        self._zxart_results_mode   = "prods"
        self._zxart_last_entries   = []
        self._zxart_ac_cache: dict = {}   # prefix -> sorted title list (short-lived cache)

        # Slideshow state
        self._zxart_screenshots    = []
        self._zxart_shot_cache     = {}
        self._zxart_shot_index     = 0
        self._zxart_shot_token     = 0
        self._zxart_slideshow_timer = QTimer(self)
        self._zxart_slideshow_timer.setInterval(5000)

        # ---- Helpers ----

        def zxart_set_status(msg: str, open_path: str = None):
            self.zxart_status_label.setText(msg)
            self._zxart_status_open_path = open_path
            if open_path:
                self.zxart_status_label.setStyleSheet("color: #4fc3f7; text-decoration: underline;")
                self.zxart_status_label.setCursor(Qt.PointingHandCursor)
            else:
                self.zxart_status_label.setStyleSheet("")
                self.zxart_status_label.setCursor(Qt.ArrowCursor)

        def _zxart_clear_detail_rows():
            while self._zxart_detail_layout.rowCount() > 0:
                self._zxart_detail_layout.removeRow(0)
            self._zxart_detail_rows = []

        def _zxart_add_row(label: str, value: str, *, dim: bool = False, wrap: bool = True, is_html: bool = False):
            lab = QLabel(_zxart_tr(label))
            val = QLabel()
            import html as _html
            if is_html:
                inner = str(value or "")
            else:
                inner = _html.escape(str(value or "")).replace("\n", "<br>")
            val.setText(
                f'<div style="word-wrap:break-word; word-break:break-all;">{inner}</div>'
            )
            val.setTextFormat(Qt.RichText)
            if wrap:
                val.setWordWrap(True)
                val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)
                val.setMinimumWidth(0)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if dim:
                val.setStyleSheet("color: #888;")
            self._zxart_detail_layout.addRow(lab, val)
            self._zxart_detail_rows.append((lab, val))

        def zxart_clear_detail():
            _zxart_clear_detail_rows()
            self.zxart_screenshot_label.setText("No preview")
            self.zxart_screenshot_label.setPixmap(QPixmap())
            self.zxart_download_button.setEnabled(False)
            self._zxart_selected_id = ""
            self._zxart_selected_title = ""
            self._zxart_selected_downloads = []
            self._zxart_slideshow_timer.stop()
            self._zxart_shot_token += 1
            self._zxart_screenshots = []
            self._zxart_shot_cache  = {}
            self._zxart_shot_index  = 0

        def zxart_sanitize_folder(name: str) -> str:
            n = (name or "").strip().lower()
            for ch in '<>:"/\\|?*':
                n = n.replace(ch, "")
            n = " ".join(n.split())
            return n or "untitled"

        def zxart_human_size(n) -> str:
            try:
                n = int(n)
            except (TypeError, ValueError):
                return str(n) if n else ""
            if n <= 0:
                return ""
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} TB"

        def zxart_download_to_path(url: str, save_path: str, on_done=None, on_err=None):
            def _fn():
                data = zxart_fetch_bytes(url, timeout=60)
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(data)
                return save_path
            def _ok(p):
                if on_done: on_done(p)
            def _err(e):
                if on_err: on_err(e)
            return getit_run_in_thread(_fn, _ok, _err)

        def _zxart_resolve_base_path(configured_path: str) -> str:
            p = (configured_path or "").strip().rstrip("/\\")
            if p and os.path.isdir(p):
                return p
            return os.path.abspath("downloads")

        def _zxart_send_to_path(title: str, downloads: list, dest_root: str, post_action=None):
            if not downloads:
                zxart_set_status("No downloadable files for this entry.")
                return
            folder = os.path.join(dest_root, zxart_sanitize_folder(title))
            os.makedirs(folder, exist_ok=True)
            pending = {"n": len(downloads), "ok": 0, "ko": 0}

            def _maybe_finish():
                if pending["ok"] + pending["ko"] >= pending["n"]:
                    if pending["ok"] > 0:
                        zxart_set_status(
                            f"Sent {pending['ok']}/{pending['n']} file(s) → {folder}  ↗ open folder",
                            open_path=folder,
                        )
                    else:
                        zxart_set_status(f"All {pending['n']} download(s) failed — check the URLs")
                    if post_action:
                        post_action(folder)

            for d in downloads:
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                save_path = os.path.join(folder, fname)

                def _ok(p, _f=fname):
                    pending["ok"] += 1
                    zxart_set_status(f"Downloaded {_f}")
                    _maybe_finish()

                def _err(e, _f=fname):
                    pending["ko"] += 1
                    zxart_set_status(f"Failed {_f}: {e[1]}")
                    _maybe_finish()

                zxart_download_to_path(d.get("url", ""), save_path, _ok, _err)

        def _zxart_send_to_image(title: str, downloads: list):
            if not right_disk_image_explorer_content:
                zxart_set_status("Please load a disk image first (SD Card tab).")
                return
            if not self.right_disk_image_path:
                zxart_set_status("No disk image loaded.")
                return
            if not downloads:
                zxart_set_status("No downloadable files for this entry.")
                return

            safe_name  = zxart_sanitize_folder(title)
            img_dir    = (generate_disk_file_path().rstrip("/") + "/" + safe_name).replace("//", "/")
            image_path = self.right_disk_image_path
            pending    = {"n": len(downloads), "ok": 0, "ko": 0}

            def _maybe_finish():
                if pending["ok"] + pending["ko"] >= pending["n"]:
                    if pending["ok"] > 0:
                        zxart_set_status(f"Sent {pending['ok']}/{pending['n']} file(s) → image:{img_dir}")
                        self._show_sd_notification(
                            f"Sent {pending['ok']}/{pending['n']} file(s) to SD card image:\n{img_dir}"
                        )
                        res = execute_hdf_monkey("ls", image_path, extra_argv=[generate_disk_file_path()])
                        if res.returncode == 0:
                            update_disk_manager_widget_table(res.stdout)
                    else:
                        zxart_set_status(f"All {pending['n']} download(s) failed — check the URLs")

            for d in downloads:
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                url      = d.get("url", "")
                img_dest = (img_dir + "/" + fname).replace("//", "/")

                def _dl_and_put(_url=url, _fname=fname, _img_dest=img_dest):
                    tmp = tempfile.NamedTemporaryFile(suffix="_" + _fname, delete=False)
                    tmp.close()
                    try:
                        data = _http_fetch_bytes_with_retry(
                            zxart_safe_url(_url),
                            headers={"User-Agent": ZXART_USER_AGENT},
                            timeout=60,
                        )
                        with open(tmp.name, "wb") as fh:
                            fh.write(data)
                        execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir], silent=True)
                        result = execute_hdf_monkey("put", image_path,
                                                   extra_argv=[tmp.name.replace("\\", "/"), _img_dest])
                        if result.returncode != 0:
                            raise RuntimeError(f"hdfmonkey put failed (rc={result.returncode})")
                    finally:
                        try:
                            os.unlink(tmp.name)
                        except OSError:
                            pass
                    return _img_dest

                def _ok(dest, _f=fname):
                    pending["ok"] += 1
                    zxart_set_status(f"Sent {_f} → image:{dest}")
                    _maybe_finish()

                def _err(e, _f=fname):
                    pending["ko"] += 1
                    zxart_set_status(f"Failed {_f}: {e[1]}")
                    _maybe_finish()

                getit_run_in_thread(_dl_and_put, _ok, _err)

        # Generation token: bumped on every table (re)population so that a
        # late-arriving background author-column resolve for a previous page
        # is ignored instead of writing into a now-stale table.
        self._zxart_populate_gen = 0

        def _zxart_resolve_author_cols_async(pending_rows):
            """Warm the zxArt name caches for *pending_rows* off the UI thread,
            then refresh the matching table cells on the UI thread.

            *pending_rows* is a list of ``(id_item, entry)`` tuples. The
            id_item carries the entry on Qt.UserRole and is only used to read
            the row id; the refresh re-locates rows by id so it stays correct
            even if Qt reordered or partially rebuilt the table.
            """
            gen = self._zxart_populate_gen
            entries = [e for (_it, e) in pending_rows]

            def _fn():
                # Network-backed warm-up of the in-memory caches. Safe here:
                # this runs on a daemon worker thread, never the GUI thread.
                _zxart_prefetch_names_for_entries(entries)
                # Recompute the final (now cache-complete) strings.
                return [(str(e.get("id", "")), _zxart_author_col_cached(e)[0])
                        for e in entries]

            def _ok(results):
                # Back on the GUI thread (queued connection). Drop the update
                # if a newer population happened in the meantime.
                if gen != self._zxart_populate_gen:
                    return
                try:
                    tbl = self.zxart_results_table
                except RuntimeError:
                    return
                by_id = {rid: txt for (rid, txt) in results}
                try:
                    row_count = tbl.rowCount()
                except RuntimeError:
                    return
                for row in range(row_count):
                    id_item = tbl.item(row, 0)
                    if id_item is None:
                        continue
                    rid = id_item.text()
                    txt = by_id.get(rid)
                    if not txt:
                        continue
                    cell = tbl.item(row, 3)
                    if cell is not None:
                        cell.setText(txt)
                    else:
                        tbl.setItem(row, 3, QTableWidgetItem(txt))

            getit_run_in_thread(_fn, _ok, lambda _e: None)

        def zxart_populate_results(entries, page, total_pages, mode="prods"):
            self._zxart_populate_gen += 1
            self._zxart_current_page = page or 1
            self._zxart_total_pages  = total_pages or 1
            self._zxart_results_mode = mode
            self.zxart_page_label.setText(str(self._zxart_current_page))
            self.zxart_prev_button.setEnabled(self._zxart_current_page > 1)
            self.zxart_next_button.setEnabled(self._zxart_current_page < self._zxart_total_pages)

            headers_map = {
                "prods":    ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
                "byletter": ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
                "pictures": ["ID", "Title", "Year", "Author(s)", "Type", "Tags"],
            }
            self.zxart_results_table.setHorizontalHeaderLabels(
                [_zxart_tr(h) for h in headers_map.get(mode, headers_map["prods"])]
            )

            self.zxart_results_table.setRowCount(0)
            _pending_author_rows = []
            for e in entries:
                row = self.zxart_results_table.rowCount()
                self.zxart_results_table.insertRow(row)
                id_item = QTableWidgetItem(e.get("id", ""))
                id_item.setData(Qt.UserRole, e)
                self.zxart_results_table.setItem(row, 0, id_item)
                self.zxart_results_table.setItem(row, 1, QTableWidgetItem(e.get("title", "")))
                self.zxart_results_table.setItem(row, 2, QTableWidgetItem(e.get("year", "")))
                # Author / group column: resolve from the in-memory caches only
                # (never block the UI thread on a network call). If the cache
                # is cold, show the best-effort text now and warm the cache in
                # a background thread, then refresh the cell when it lands.
                author_text, complete = _zxart_author_col_cached(e)
                author_item = QTableWidgetItem(author_text)
                self.zxart_results_table.setItem(row, 3, author_item)
                if not complete:
                    _pending_author_rows.append((id_item, e))
                self.zxart_results_table.setItem(row, 4, QTableWidgetItem(e.get("machine", "")))
                self.zxart_results_table.setItem(row, 5, QTableWidgetItem(e.get("genre", "")))
            self._zxart_last_entries = list(entries)
            if _pending_author_rows:
                _zxart_resolve_author_cols_async(_pending_author_rows)
            self.zxart_gallery_view.populate(entries)
            self.zxart_gallery_view.select_entry(
                lambda _e, _sel=self._zxart_selected_id: bool(_sel) and _e.get("id") == _sel
            )
            try:
                _aio = getattr(self, "_allinone_repopulate", None)
                if _aio is not None:
                    _aio()
            except Exception:
                pass

        # ---- Slideshow ----

        def zxart_set_pixmap(pm: QPixmap):
            if pm is None or pm.isNull():
                self.zxart_screenshot_label.setText("No preview")
                self.zxart_screenshot_label.setPixmap(QPixmap())
                return
            self.zxart_screenshot_label.setPixmap(
                pm.scaled(
                    self.zxart_screenshot_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            if self._zxart_stack.currentIndex() == 1:
                self._zxart_fullscreen_pixmap = pm
                fs = self.zxart_fullscreen_label.size()
                self.zxart_fullscreen_label.setPixmap(
                    pm.scaled(fs, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )

        def zxart_update_nav_buttons():
            multi = len(self._zxart_screenshots) > 1
            self.zxart_prev_shot_btn.setVisible(multi)
            self.zxart_next_shot_btn.setVisible(multi)
            self.zxart_fs_prev_btn.setVisible(multi and self._zxart_stack.currentIndex() == 1)
            self.zxart_fs_next_btn.setVisible(multi and self._zxart_stack.currentIndex() == 1)

        def zxart_show_shot_at(idx: int):
            if not self._zxart_screenshots:
                return
            idx = idx % len(self._zxart_screenshots)
            self._zxart_shot_index = idx
            zxart_update_nav_buttons()
            url = self._zxart_screenshots[idx]["url"]
            cached = self._zxart_shot_cache.get(url)
            if cached is not None:
                zxart_set_pixmap(cached)
                return

            token = self._zxart_shot_token

            def _fn():
                return zxart_fetch_bytes(url)

            def _on_ok(data):
                if token != self._zxart_shot_token:
                    return
                pm = QPixmap()
                if pm.loadFromData(data) and not pm.isNull():
                    self._zxart_shot_cache[url] = pm
                    if self._zxart_screenshots and self._zxart_screenshots[self._zxart_shot_index]["url"] == url:
                        zxart_set_pixmap(pm)

            def _on_err(_err):
                pass

            getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxart_slideshow_tick():
            if len(self._zxart_screenshots) <= 1:
                return
            zxart_show_shot_at(self._zxart_shot_index + 1)

        self._zxart_slideshow_timer.timeout.connect(zxart_slideshow_tick)

        def _zxart_nav_prev():
            if len(self._zxart_screenshots) > 1:
                self._zxart_slideshow_timer.stop()
                zxart_show_shot_at(self._zxart_shot_index - 1)
                self._zxart_slideshow_timer.start()

        def _zxart_nav_next():
            if len(self._zxart_screenshots) > 1:
                self._zxart_slideshow_timer.stop()
                zxart_show_shot_at(self._zxart_shot_index + 1)
                self._zxart_slideshow_timer.start()

        self.zxart_prev_shot_btn.clicked.connect(_zxart_nav_prev)
        self.zxart_next_shot_btn.clicked.connect(_zxart_nav_next)

        def zxart_start_slideshow(screenshots):
            self._zxart_slideshow_timer.stop()
            self._zxart_shot_token += 1
            self._zxart_screenshots = list(screenshots or [])
            self._zxart_shot_cache  = {}
            self._zxart_shot_index  = 0
            if not self._zxart_screenshots:
                self.zxart_screenshot_label.setText("No preview")
                self.zxart_screenshot_label.setPixmap(QPixmap())
                zxart_update_nav_buttons()
                return
            zxart_show_shot_at(0)
            if len(self._zxart_screenshots) > 1:
                self._zxart_slideshow_timer.start()

        # ---- Detail population ----

        def zxart_populate_prod_detail(detail: dict):
            _zxart_clear_detail_rows()
            _zxart_add_row("Title:",       detail.get("title", ""))
            _zxart_add_row("Year:",        detail.get("year", ""))
            _zxart_add_row("Authors:",     detail.get("authors", ""))
            _zxart_add_row("Groups:",      detail.get("groups", ""))
            _zxart_add_row("Produced by:", detail.get("produced_by", ""))
            _zxart_add_row("Published by:", detail.get("publishers", ""))
            _zxart_add_row("Compo:",       detail.get("compo", ""))
            party_place = detail.get("partyPlace", "")
            if party_place:
                _zxart_add_row("Place:", str(party_place))
            _zxart_add_row("Languages:",   detail.get("language", ""))
            _zxart_add_row("Legal:",       zxart_legal_status_label(detail.get("legalStatus", "")))
            _zxart_add_row("Description:", detail.get("description", ""), dim=True, is_html=True)
            self._zxart_selected_downloads = detail.get("downloads", []) or []
            self.zxart_download_button.setEnabled(bool(self._zxart_selected_downloads))

        def zxart_populate_picture_detail(detail: dict):
            _zxart_clear_detail_rows()
            _zxart_add_row("Title:",    detail.get("title", ""))
            _zxart_add_row("Year:",     detail.get("year", ""))
            _zxart_add_row("Authors:",  detail.get("authors", ""))
            _zxart_add_row("Type:",     detail.get("pic_type", ""))
            _zxart_add_row("Rating:",   detail.get("rating", ""))
            _zxart_add_row("Views:",    detail.get("views", ""))
            tags = detail.get("tags", "")
            if tags:
                _zxart_add_row("Tags:", tags, dim=True)
            _zxart_add_row("Description:", detail.get("description", ""), dim=True, is_html=True)
            self._zxart_selected_downloads = detail.get("downloads", []) or []
            self.zxart_download_button.setEnabled(bool(self._zxart_selected_downloads))

        # ---- Search tasks ----

        def zxart_current_mode():
            return self.zxart_mode_combo.currentData() or "prods"

        def zxart_set_busy(busy: bool):
            self._zxart_search_loading = busy
            self.zxart_search_button.setEnabled(not busy)
            self.zxart_mode_combo.setEnabled(not busy)
            self.zxart_letter_combo.setEnabled(not busy)
            try:
                self.zxart_random_button.setEnabled(not busy)
            except AttributeError:
                pass
            try:
                self.zxart_latest_button.setEnabled(not busy)
            except AttributeError:
                pass

        def zxart_run_search(query: str, page: int, on_complete=None):
            # Supersede any in-flight zxART request.
            self._zxart_search_gen += 1
            _gen = self._zxart_search_gen
            mode = zxart_current_mode()
            zxart_set_busy(True)
            zxart_set_status("Searching…")
            self._zxart_last_query = query
            offset = max(0, (page - 1) * ZXART_PAGE_SIZE)

            if mode == "pictures":
                if query:
                    path = (
                        f"/export:zxPicture/language:{_zxart_lang()}/start:{offset}"
                        f"/limit:{ZXART_PAGE_SIZE}/filter:title~{urllib.parse.quote(query)}"
                    )
                else:
                    path = (
                        f"/export:zxPicture/language:{_zxart_lang()}/start:{offset}"
                        f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
                    )

                def _fn_pic():
                    resp = zxart_fetch_json(path)
                    entries, total = zxart_parse_picture_list(resp)
                    total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE) if total else 1
                    _zxart_prefetch_names_for_entries(entries)
                    return ("pictures", entries, total, page, total_pages)

                _fn = _fn_pic

            elif mode == "byletter":
                letter = self.zxart_letter_combo.currentData() or "a"
                if letter == "#":
                    filt = "title~0,1,2,3,4,5,6,7,8,9"
                else:
                    filt = f"title~{urllib.parse.quote(letter)}"
                path = (
                    f"/export:zxProd/language:{_zxart_lang()}/start:{offset}"
                    f"/limit:{ZXART_PAGE_SIZE}/filter:{filt}/order:title,asc"
                )

                def _fn_letter():
                    resp = zxart_fetch_json(path)
                    entries, total = zxart_parse_prod_list(resp)
                    total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE) if total else 1
                    for e in entries:
                        e["_kind"] = "zxart_prod"
                    _zxart_prefetch_names_for_entries(entries)
                    return ("byletter", entries, total, page, total_pages)

                _fn = _fn_letter

            else:  # prods
                if query:
                    def _fn_prods():
                        def _progress(msg: str):
                            # Called from background thread — post to Qt main thread.
                            QMetaObject.invokeMethod(
                                self.zxart_status_label,
                                "setText",
                                Qt.QueuedConnection,
                                Q_ARG(str, msg),
                            )
                        entries, total = zxart_client_search(
                            query, progress_cb=_progress
                        )
                        for e in entries:
                            e["_kind"] = "zxart_prod"
                        _zxart_prefetch_names_for_entries(entries)
                        return ("prods", entries, total, 1, 1)
                else:
                    path = (
                        f"/export:zxProd/language:{_zxart_lang()}/start:{offset}"
                        f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
                    )

                    def _fn_prods():
                        resp = zxart_fetch_json(path)
                        entries, total = zxart_parse_prod_list(resp)
                        total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE) if total else 1
                        for e in entries:
                            e["_kind"] = "zxart_prod"
                        _zxart_prefetch_names_for_entries(entries)
                        return ("prods", entries, total, page, total_pages)

                _fn = _fn_prods

            def _on_ok(data):
                if _gen != self._zxart_search_gen:
                    return  # superseded by a newer search
                kind, entries, total, pg, total_pages = data
                zxart_populate_results(entries, pg, total_pages, kind)
                if kind == "pictures":
                    zxart_set_status(f"{total} picture(s)  |  page {pg}/{total_pages}")
                elif kind == "byletter":
                    lbl = self.zxart_letter_combo.currentText()
                    zxart_set_status(f"{total} production(s) for '{lbl}'  |  page {pg}/{total_pages}")
                elif kind == "prods" and total_pages == 1 and self._zxart_last_query:
                    zxart_set_status(f"{total} result(s) for '{self._zxart_last_query}'")
                else:
                    zxart_set_status(f"{total} production(s)  |  page {pg}/{total_pages}")
                zxart_set_busy(False)
                if on_complete:
                    on_complete()

            def _on_err(err):
                if _gen != self._zxart_search_gen:
                    return  # superseded by a newer search
                zxart_set_status(f"Error: {err[1]}")
                zxart_set_busy(False)
                if on_complete:
                    on_complete()

            self._zxart_search_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxart_on_search():
            zxart_clear_detail()
            q = self.zxart_search_input.text().strip()
            save_configuration_file()
            if q and len(q) < SEARCH_MIN_CHARS:
                return
            # Invalidate any in-flight autocomplete request and cancel any
            # pending debounce timer — its async result must not pop the
            # completer popup while the real search is running, which has
            # produced a native access violation inside QCompleter.
            try:
                self._zxart_ac_gen += 1
                self._zxart_ac_block = True
                t = getattr(self, "_zxart_ac_timer", None)
                if t is not None:
                    t.stop()
                if getattr(self, "_zxart_ac_model", None) is not None:
                    self._zxart_ac_model.setStringList([])
                comp = getattr(self, "_zxart_completer", None)
                if comp is not None:
                    try:
                        popup = comp.popup()
                        if popup is not None and popup.isVisible():
                            popup.hide()
                    except RuntimeError:
                        pass
            except Exception:
                pass
            if q:
                _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                def _zxart_done():
                    _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                    _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, self.zxart_results_table.rowCount())
                zxart_run_search(q, 1, _zxart_done)
            else:
                zxart_run_search(q, 1)
            if _multi_search_enabled() and q:
                self.getit_search_input.setText(q)
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    self.zxdb_search_input.setText(q)
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _cross_search_getit(q)
                _cross_search_zxdb(q)

        def zxart_on_prev():
            zxart_run_search(self._zxart_last_query, max(1, self._zxart_current_page - 1))

        def zxart_on_next():
            zxart_run_search(self._zxart_last_query, min(self._zxart_total_pages, self._zxart_current_page + 1))

        def zxart_on_latest(on_complete=None):
            zxart_clear_detail()
            self.zxart_search_input.clear()
            self._zxart_last_query = ""
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            def _zxart_latest_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, self.zxart_results_table.rowCount())
                if on_complete:
                    on_complete()
            # zxart_run_search with empty query already uses order:date,desc for
            # both 'prods' and 'pictures' modes, returning the most recent items.
            zxart_run_search("", 1, _zxart_latest_done)

        def zxart_on_random(on_complete=None):
            import random as _random
            zxart_clear_detail()
            self.zxart_search_input.clear()
            self._zxart_last_query = ""
            mode = zxart_current_mode()
            # Supersede any in-flight zxART request.
            self._zxart_search_gen += 1
            _gen = self._zxart_search_gen
            zxart_set_busy(True)
            zxart_set_status("Picking random zxART entries…")
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)

            def _fn():
                if mode == "pictures":
                    export = "zxPicture"
                    kind = "pictures"
                else:
                    export = "zxProd"
                    kind = "prods"
                # Probe first page to learn the total number of entries.
                probe_path = (
                    f"/export:{export}/language:{_zxart_lang()}/start:0"
                    f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
                )
                probe_resp = zxart_fetch_json(probe_path)
                if kind == "pictures":
                    _e, total = zxart_parse_picture_list(probe_resp)
                else:
                    _e, total = zxart_parse_prod_list(probe_resp)
                total = max(1, int(total or 1))
                total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE)
                page = _random.randint(1, total_pages)
                offset = (page - 1) * ZXART_PAGE_SIZE
                path = (
                    f"/export:{export}/language:{_zxart_lang()}/start:{offset}"
                    f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
                )
                resp = zxart_fetch_json(path)
                if kind == "pictures":
                    entries, _tot = zxart_parse_picture_list(resp)
                    for e in entries:
                        e["_kind"] = "zxart_pic"
                else:
                    entries, _tot = zxart_parse_prod_list(resp)
                    for e in entries:
                        e["_kind"] = "zxart_prod"
                _random.shuffle(entries)
                return (kind, entries, total, page, total_pages)

            def _on_ok(data):
                if _gen != self._zxart_search_gen:
                    return  # superseded by a newer search
                kind, entries, total, page, total_pages = data
                zxart_populate_results(entries, page, total_pages, kind)
                noun = "picture(s)" if kind == "pictures" else "production(s)"
                zxart_set_status(
                    f"{len(entries)} random {noun}  |  page {page}/{total_pages}"
                )
                zxart_set_busy(False)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, self.zxart_results_table.rowCount())
                if on_complete:
                    on_complete()

            def _on_err(err):
                if _gen != self._zxart_search_gen:
                    return  # superseded by a newer search
                zxart_set_status(f"Error: {err[1]}")
                zxart_set_busy(False)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, self.zxart_results_table.rowCount())
                if on_complete:
                    on_complete()

            self._zxart_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        self.zxart_search_button.clicked.connect(zxart_on_search)
        self.zxart_search_input.returnPressed.connect(zxart_on_search)
        self.zxart_prev_button.clicked.connect(zxart_on_prev)
        self.zxart_next_button.clicked.connect(zxart_on_next)
        self.zxart_random_button.clicked.connect(zxart_on_random)
        self.zxart_latest_button.clicked.connect(zxart_on_latest)

        def _zxart_search_validate(text: str):
            t = text.strip()
            if not t:
                self._zxart_search_valid_lbl.setVisible(False)
            elif len(t) < SEARCH_MIN_CHARS:
                self._zxart_search_valid_lbl.setText('<font color="red">❌</font>')
                self._zxart_search_valid_lbl.setToolTip(f"Searches must be {SEARCH_MIN_CHARS} characters long")
                self._zxart_search_valid_lbl.setVisible(True)
            else:
                self._zxart_search_valid_lbl.setText('<font color="green">✔</font>')
                self._zxart_search_valid_lbl.setVisible(True)
        self.zxart_search_input.textChanged.connect(_zxart_search_validate)

        # ---- ZxArt autocomplete ----

        self._zxart_ac_model = QStringListModel(self)
        _zxart_completer = QCompleter(self._zxart_ac_model, self)
        _zxart_completer.setCompletionMode(QCompleter.PopupCompletion)
        _zxart_completer.setCaseSensitivity(Qt.CaseInsensitive)
        _zxart_completer.setFilterMode(Qt.MatchStartsWith)
        # Ensure the popup follows the main window on Windows
        popup = _zxart_completer.popup()
        if popup is not None:
            popup.setParent(self)
            popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
            popup.setAttribute(Qt.WA_ShowWithoutActivating)
        self._zxart_completer = _zxart_completer
        self.zxart_search_input.setCompleter(_zxart_completer)

        _zxart_ac_timer = QTimer(self)
        _zxart_ac_timer.setSingleShot(True)
        _zxart_ac_timer.setInterval(400)
        self._zxart_ac_timer = _zxart_ac_timer
        self._zxart_ac_pending: str = ""
        # Generation token: bumped whenever a real search starts or the
        # input is cleared.  Async autocomplete results carrying an older
        # token are discarded so they cannot repopulate / re-pop the
        # completer while a full search (or teardown) is already in flight.
        self._zxart_ac_gen: int = 0

        def _zxart_ac_trigger():
            if not _search_autocomplete_on():
                self._zxart_ac_model.setStringList([])
                return
            mode = zxart_current_mode()
            if mode not in ("prods", "byletter"):
                self._zxart_ac_model.setStringList([])
                return
            text = self.zxart_search_input.text().strip()
            if not text:
                self._zxart_ac_model.setStringList([])
                return

            # Avoid firing a heavy zxART network search (binary-search probes +
            # 200-item window fetch) on an empty input.  Other panes (GetIt,
            # ZXDB) already offer suggestions starting at the first typed
            # character, so allow the autocomplete to trigger as soon as the
            # user has typed at least one character.  The full search button
            # itself still enforces SEARCH_MIN_CHARS.
            if len(text) < 1:
                self._zxart_ac_model.setStringList([])
                return

            # Safe popup helper.  QCompleter.complete() has crashed Qt with
            # a native access violation on this Windows build, even when
            # deferred via QTimer.singleShot(0, ...).  We therefore drive
            # the popup view directly: set the completion prefix on the
            # completer (which filters the model) and show the popup view
            # at an explicit geometry, skipping complete()'s internal
            # event-loop pumping.
            def _safe_show_popup(_q=text):
                try:
                    if not self._search_autocomplete_on():
                        return
                    if getattr(self, "_zxart_ac_block", False):
                        return
                    if not self.zxart_search_input.hasFocus():
                        return
                    if self.zxart_search_input.text().strip() != _q:
                        return
                    if self._zxart_ac_model.rowCount() == 0:
                        return
                    _zxart_completer.setCompletionPrefix(_q)
                    popup = _zxart_completer.popup()
                    if popup is None:
                        return
                    try:
                        popup.setParent(self.zxart_search_input.window(),
                                        Qt.Tool
                                        | Qt.FramelessWindowHint
                                        | Qt.WindowStaysOnTopHint
                                        | Qt.WindowDoesNotAcceptFocus)
                        popup.setFocusPolicy(Qt.NoFocus)
                        popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
                    except Exception:
                        pass
                    le = self.zxart_search_input
                    rect = le.rect()
                    pos = le.mapToGlobal(rect.bottomLeft())
                    popup.setMinimumWidth(le.width())
                    popup.move(pos)
                    popup.resize(le.width(), _popup_height_for(popup, self._zxart_ac_model.rowCount()))
                    popup.show()
                except RuntimeError:
                    pass
                except Exception:
                    pass

            # Serve from short-lived prefix cache if available.
            if text in self._zxart_ac_cache:
                titles = self._zxart_ac_cache[text][:80]
                self._zxart_ac_model.setStringList(titles)
                if titles:
                    QTimer.singleShot(0, _safe_show_popup)
                return

            # Also try to derive results from a cached longer prefix.
            tl = text.lower()
            for cached_prefix, cached_list in self._zxart_ac_cache.items():
                if tl.startswith(cached_prefix.lower()):
                    matches = sorted(
                        (t for t in cached_list if t.lower().startswith(tl)),
                        key=str.lower,
                    )[:80]
                    self._zxart_ac_model.setStringList(matches)
                    if matches:
                        QTimer.singleShot(0, _safe_show_popup)
                    return

            self._zxart_ac_pending = text
            gen_at_dispatch = self._zxart_ac_gen
            self._ac_anim_start(self.zxart_search_input)

            def _fn():
                entries, _total = zxart_client_search(text)
                titles = sorted(
                    {e["title"] for e in entries if e.get("title")},
                    key=str.lower,
                )
                return (text, titles)

            def _on_ok(result):
                queried, titles = result
                self._ac_anim_stop(self.zxart_search_input)
                try:
                    # Evict oldest cache entries to cap memory (keep last 10 prefixes).
                    if len(self._zxart_ac_cache) >= 10:
                        oldest = next(iter(self._zxart_ac_cache))
                        del self._zxart_ac_cache[oldest]
                    self._zxart_ac_cache[queried] = titles
                    # Discard the result if a real search has been launched (or
                    # the input was reset) since we dispatched this fetch — in
                    # that case popping the completer would re-enter Qt while
                    # QCompleter/QLineEdit are mid-transition, which has caused
                    # an access violation on Windows.
                    if gen_at_dispatch != self._zxart_ac_gen:
                        return
                    # Only update the model if user hasn't moved on to a different prefix.
                    if self.zxart_search_input.text().strip() != queried:
                        return
                    if not self.zxart_search_input.hasFocus():
                        return
                    self._zxart_ac_model.setStringList(titles[:80])
                    if titles:
                        QTimer.singleShot(0, _safe_show_popup)
                except RuntimeError:
                    # Underlying C++ widget/model was deleted while the queued
                    # result was in flight — safe to drop.
                    pass

            def _on_err(_err):
                self._ac_anim_stop(self.zxart_search_input)

            self._zxart_ac_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _zxart_ac_on_text_changed(_text: str):
            if getattr(self, "_zxart_ac_suppress", False):
                self._zxart_ac_suppress = False
                return
            # The user is typing again: re-enable autocomplete suggestions
            # that were suppressed after the last search submission.
            self._zxart_ac_block = False
            _zxart_ac_timer.start()

        _zxart_ac_timer.timeout.connect(_zxart_ac_trigger)
        self.zxart_search_input.textChanged.connect(_zxart_ac_on_text_changed)

        # Tracks zxArt prefix fetches initiated externally (e.g. by the
        # AllInOne pane) so we don't fire duplicate requests for the same
        # prefix while one is already in flight.
        self._zxart_ac_external_fetching: set = set()

        def _zxart_ac_fetch_prefix(prefix: str):
            """Fetch zxArt titles starting with *prefix* into the shared
            _zxart_ac_cache.  Used by the AllInOne pane to prime suggestions
            without touching the zxArt search input/completer."""
            if not prefix:
                return
            if prefix in self._zxart_ac_cache:
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("zxart", prefix)
                    except Exception:
                        pass
                return
            if prefix in self._zxart_ac_external_fetching:
                return
            self._zxart_ac_external_fetching.add(prefix)

            def _fn():
                entries, _total = zxart_client_search(prefix)
                titles = sorted(
                    {e["title"] for e in entries if e.get("title")},
                    key=str.lower,
                )
                return (prefix, titles)

            def _on_ok(result):
                pfx, titles = result
                self._zxart_ac_external_fetching.discard(pfx)
                try:
                    if len(self._zxart_ac_cache) >= 10:
                        oldest = next(iter(self._zxart_ac_cache))
                        del self._zxart_ac_cache[oldest]
                    self._zxart_ac_cache[pfx] = titles
                except Exception:
                    pass
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("zxart", pfx)
                    except Exception:
                        pass

            def _on_err(_err):
                self._zxart_ac_external_fetching.discard(prefix)
                cb = getattr(self, "_allinone_ac_notify", None)
                if cb:
                    try:
                        cb("zxart", prefix)
                    except Exception:
                        pass

            getit_run_in_thread(_fn, _on_ok, _on_err)

        self._zxart_ac_fetch_prefix = _zxart_ac_fetch_prefix

        def _zxart_ac_activated(selected: str):
            try:
                if selected:
                    self._zxart_ac_suppress = True
                    _zxart_ac_timer.stop()
                    try:
                        _zxart_completer.popup().hide()
                    except Exception:
                        pass
                    self.zxart_search_input.setText(selected)
            except Exception:
                pass
            zxart_on_search()

        _zxart_completer.activated.connect(_zxart_ac_activated)

        def zxart_on_mode_changed(_idx):
            mode = zxart_current_mode()
            placeholders = {
                "prods":    "Search zxART productions... (leave empty to browse latest)",
                "byletter": "(pick a letter from the list →)",
                "pictures": "Search zxART pictures... (leave empty to browse latest)",
            }
            self.zxart_search_input.setPlaceholderText(placeholders.get(mode, ""))
            self.zxart_search_input.setVisible(mode != "byletter")
            self.zxart_letter_combo.setVisible(mode == "byletter")
            self._zxart_last_query = ""
            self._zxart_current_page = 1
            self._zxart_total_pages  = 1
            self.zxart_page_label.setText("1")
            self.zxart_prev_button.setEnabled(False)
            self.zxart_next_button.setEnabled(False)
            self.zxart_results_table.setRowCount(0)
            zxart_clear_detail()
            zxart_set_status("")
            configuration_dictionary[SETTING_ZXART_LAST_MODE] = mode
            save_configuration_file()

        self.zxart_mode_combo.currentIndexChanged.connect(zxart_on_mode_changed)

        def zxart_on_letter_changed(_idx):
            if zxart_current_mode() == "byletter":
                zxart_clear_detail()
                zxart_run_search("", 1)

        self.zxart_letter_combo.currentIndexChanged.connect(zxart_on_letter_changed)

        # ---- Row selection -> fetch detail ----

        def _zxart_reset_preview():
            self._zxart_slideshow_timer.stop()
            self._zxart_shot_token += 1
            self._zxart_screenshots = []
            self._zxart_shot_cache  = {}
            self._zxart_shot_index  = 0
            self.zxart_screenshot_label.setPixmap(QPixmap())

        def _zxart_load_prod(pid: str, title_hint: str):
            """Load full production detail including releases."""
            self._zxart_selected_id    = pid
            self._zxart_selected_title = title_hint or pid
            zxart_set_status(f"Loading production {pid}…")
            self.zxart_screenshot_label.setText("Loading…")
            _zxart_reset_preview()

            def _fn():
                # Fetch the production record
                prod_resp = zxart_fetch_json(
                    f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(pid)}"
                )
                prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                prod = prods[0] if prods else {}

                # Fetch all releases for this production
                rel_resp = zxart_fetch_json(
                    f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(pid)}"
                )
                releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []

                # Build detail dict
                def _join(lst):
                    if isinstance(lst, list):
                        return ", ".join(str(x) for x in lst if x)
                    return str(lst) if lst else ""

                authors_info = prod.get("authorsInfo") or []
                author_ids = [a.get("authorId") for a in authors_info if isinstance(a, dict) and a.get("authorId")]

                group_ids = prod.get("groupsIds") or []
                pub_ids   = prod.get("publishersIds") or []

                # Resolve human-readable names via the zxArt API.
                # Bulk endpoint resolves most authors in a single call; per-id
                # lookups (cached) cover any IDs the bulk filter omits.
                bulk_authors = {}
                try:
                    a_resp = zxart_fetch_json(
                        f"/export:author/filter:zxProdId={urllib.parse.quote(pid)}/limit:200/"
                    )
                    for a in (a_resp.get("responseData") or {}).get("author", []) or []:
                        if isinstance(a, dict) and a.get("id"):
                            bulk_authors[int(a["id"])] = str(a.get("title") or "")
                except Exception:
                    pass

                author_display_parts = []
                for aid in author_ids:
                    try:
                        key = int(aid)
                    except (TypeError, ValueError):
                        author_display_parts.append(str(aid))
                        continue
                    name = bulk_authors.get(key) or _zxart_resolve_author_name(key)
                    author_display_parts.append(name if name else str(aid))
                authors_display = ", ".join(s for s in author_display_parts if s)

                groups_display = _zxart_resolve_group_names(group_ids)
                publishers_display = _zxart_resolve_publisher_names(pub_ids)
                if not publishers_display:
                    publishers_display = _zxart_scrape_publishers_from_prod_url(
                        str(prod.get("url") or "")
                    )

                downloads = []
                for rel in releases:
                    if not isinstance(rel, dict):
                        continue
                    file_url  = rel.get("file") or ""
                    file_name = rel.get("fileName") or (
                        os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else ""
                    )
                    if not file_url:
                        continue
                    rel_type   = rel.get("releaseType") or ""
                    rel_format = rel.get("releaseFormat") or ""
                    rel_title  = rel.get("title") or ""
                    downloads.append({
                        "url":      file_url,
                        "filename": file_name,
                        "type":     f"{rel_type} / {rel_format}".strip(" /") or "release",
                        "format":   rel_format,
                        "size":     "",
                        "source":   rel_title or "zxart",
                        "year":     str(rel.get("year") or ""),
                    })

                # imagesUrls on the prod record are the primary previews (screenshots, inlays)
                screenshots = []
                seen_urls = set()
                for img_url in (prod.get("imagesUrls") or []):
                    if img_url and img_url not in seen_urls:
                        seen_urls.add(img_url)
                        screenshots.append({"url": img_url, "type": "screenshot"})

                # Additional inlays / ads / instructions from releases
                for rel in releases:
                    if not isinstance(rel, dict):
                        continue
                    for key in ("inlays", "ads", "instructions"):
                        for img_url in (rel.get(key) or []):
                            if img_url and img_url not in seen_urls:
                                seen_urls.add(img_url)
                                screenshots.append({"url": img_url, "type": key.rstrip("s")})

                detail = {
                    "id":          pid,
                    "title":       str(prod.get("title") or ""),
                    "year":        str(prod.get("year") or ""),
                    "authors":     authors_display,
                    "groups":      groups_display,
                    "publishers":  publishers_display,
                    "produced_by": groups_display,
                    "compo":       str(prod.get("compo") or ""),
                    "partyPlace":  prod.get("partyPlace") or "",
                    "language":    _join(prod.get("language")),
                    "legalStatus": zxart_legal_status_label(prod.get("legalStatus") or ""),
                    "description": str(prod.get("description") or ""),
                    "screenshots": screenshots,
                    "downloads":   downloads,
                }
                return detail

            def _on_ok(detail):
                if self._zxart_selected_id != pid:
                    return
                zxart_populate_prod_detail(detail)
                shots = detail.get("screenshots") or []
                zxart_start_slideshow(shots)
                title = detail.get("title", pid)
                n = len(shots)
                n_dl = len(detail.get("downloads") or [])
                msg = f"Loaded {title}"
                if n_dl:
                    msg += f"  |  {n_dl} file(s)"
                if n > 1:
                    msg += f"  |  {n} image(s) cycling"
                zxart_set_status(msg)

            def _on_err(err):
                if self._zxart_selected_id != pid:
                    return
                zxart_set_status(f"Detail error: {err[1]}")
                self.zxart_screenshot_label.setText("No preview")

            self._zxart_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _zxart_load_picture(pid: str, title_hint: str, source: dict):
            """Load picture detail – preview from imageUrl, download from originalUrl."""
            self._zxart_selected_id    = pid
            self._zxart_selected_title = title_hint or pid
            zxart_set_status(f"Loading picture {pid}…")
            self.zxart_screenshot_label.setText("Loading…")
            _zxart_reset_preview()

            def _fn():
                pic_resp = zxart_fetch_json(
                    f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(pid)}"
                )
                pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                pic  = pics[0] if pics else source or {}

                image_url    = pic.get("imageUrl") or ""
                original_url = pic.get("originalUrl") or ""
                author_ids   = pic.get("authorIds") or []
                tags         = pic.get("tags") or []

                # Resolve human-readable author names via the zxArt API.
                # /export:author/filter:zxPictureId=<id>/ returns one row per
                # contributor with id + title, in one call.
                authors_display = ""
                try:
                    a_resp = zxart_fetch_json(
                        f"/export:author/filter:zxPictureId={urllib.parse.quote(pid)}/limit:200/"
                    )
                    names_by_id = {
                        int(a["id"]): str(a.get("title") or "")
                        for a in (a_resp.get("responseData") or {}).get("author", []) or []
                        if isinstance(a, dict) and a.get("id")
                    }
                    parts = []
                    for aid in author_ids:
                        try:
                            key = int(aid)
                        except (TypeError, ValueError):
                            parts.append(str(aid))
                            continue
                        name = names_by_id.get(key) or _zxart_resolve_author_name(key)
                        parts.append(name if name else str(aid))
                    if not author_ids and names_by_id:
                        parts = list(names_by_id.values())
                    authors_display = ", ".join(s for s in parts if s)
                except Exception:
                    authors_display = _zxart_resolve_author_names(author_ids)

                screenshots = []
                if image_url:
                    screenshots.append({"url": image_url, "type": "picture"})

                downloads = []
                if original_url:
                    fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{pid}.bin"
                    downloads.append({
                        "url":      original_url,
                        "filename": fname,
                        "type":     "original",
                        "format":   "",
                        "size":     "",
                        "source":   "zxart",
                    })
                if image_url and image_url != original_url:
                    fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{pid}.png"
                    downloads.append({
                        "url":      image_url,
                        "filename": fname_img,
                        "type":     "preview (PC)",
                        "format":   "",
                        "size":     "",
                        "source":   "zxart",
                    })

                detail = {
                    "id":          pid,
                    "title":       str(pic.get("title") or ""),
                    "year":        str(pic.get("year") or ""),
                    "authors":     authors_display,
                    "pic_type":    str(pic.get("type") or ""),
                    "rating":      str(pic.get("rating") or ""),
                    "views":       str(pic.get("views") or ""),
                    "tags":        ", ".join(str(t) for t in tags),
                    "description": str(pic.get("description") or ""),
                    "screenshots": screenshots,
                    "downloads":   downloads,
                }
                return detail

            def _on_ok(detail):
                if self._zxart_selected_id != pid:
                    return
                zxart_populate_picture_detail(detail)
                shots = detail.get("screenshots") or []
                zxart_start_slideshow(shots)
                title = detail.get("title", pid)
                zxart_set_status(f"Loaded picture: {title}")

            def _on_err(err):
                if self._zxart_selected_id != pid:
                    return
                zxart_set_status(f"Detail error: {err[1]}")
                self.zxart_screenshot_label.setText("No preview")

            self._zxart_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxart_on_row_selected():
            sel = self.zxart_results_table.selectionModel().selectedRows()
            if not sel:
                return
            row = sel[0].row()
            id_item    = self.zxart_results_table.item(row, 0)
            title_item = self.zxart_results_table.item(row, 1)
            if not id_item:
                return
            entry = id_item.data(Qt.UserRole) or {}
            kind  = entry.get("_kind", "zxart_prod")
            pid   = id_item.text()
            title_hint = title_item.text() if title_item else pid
            self.zxart_download_button.setEnabled(False)
            if kind == "zxart_picture":
                _zxart_load_picture(pid, title_hint, entry.get("_source") or {})
            else:
                _zxart_load_prod(pid, title_hint)

        self.zxart_results_table.itemSelectionChanged.connect(zxart_on_row_selected)

        def zxart_on_row_double_clicked(item):
            row = self.zxart_results_table.row(item)
            id_item = self.zxart_results_table.item(row, 0)
            if not id_item:
                return
            entry = id_item.data(Qt.UserRole) or {}
            if entry:
                _zxart_open_gallery_viewer(entry)

        self.zxart_results_table.itemDoubleClicked.connect(zxart_on_row_double_clicked)

        def zxart_on_gallery_cell(entry):
            eid = entry.get("id") or ""
            if not eid:
                return
            for r in range(self.zxart_results_table.rowCount()):
                item = self.zxart_results_table.item(r, 0)
                if item is not None and item.text() == eid:
                    self.zxart_results_table.selectRow(r)
                    break
            self.zxart_gallery_view.select_entry(lambda _e, _e0=entry: _e is _e0)

        self.zxart_gallery_view.cell_clicked.connect(zxart_on_gallery_cell)

        def _zxart_open_gallery_viewer(entry, make_viewer=None, install=True):
            eid   = entry.get("id") or ""
            title = entry.get("title") or eid
            if not eid:
                return None
            kind = entry.get("_kind", "zxart_prod")

            info_rows_base = [
                ("Title:",  title),
                ("Author:", entry.get("author", "")),
                ("Year:",   str(entry.get("year", "") or "")),
                ("Type:",   entry.get("prodType", "") or entry.get("pic_type", "")),
            ]
            _mk = make_viewer or (lambda **kw: GalleryItemViewer(parent=self, **kw))
            viewer = _mk(
                title=title,
                info_rows=info_rows_base,
                screenshots=[],
                extra_fetch_cb=_zxart_extra_fetch,
                tags=_gallery_extract_tags(entry),
            )
            _fav_entry_zxart = {**entry, "_fav_source": "zxart"}
            viewer.set_favorite_hooks(_fav_entry_zxart, self._fav_is, self._fav_toggle)

            # ── action buttons ──────────────────────────────────────────
            _safe_title = zxart_sanitize_folder(title)
            _img_path   = self.right_disk_image_path or ""
            _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                           ) if _img_path else ""
            _sd_dest    = f"{_img_path}  →  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base    = _zxart_resolve_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)
            _ns_dest    = os.path.join(_ns_base, _safe_title)
            _sd_ok      = bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content)

            def _ensure_detail_then(callback, _pid=eid, _kind=kind, _title=title):
                if self._zxart_selected_id == _pid and self._zxart_selected_downloads:
                    callback(self._zxart_selected_title or _title,
                             self._zxart_selected_downloads)
                    return
                zxart_set_status(f"Loading {_pid}\u2026")
                if _kind == "zxart_picture":
                    def _fn():
                        pic_resp = zxart_fetch_json(
                            f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(_pid)}"
                        )
                        pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                        pic  = pics[0] if pics else (entry.get("_source") or {})
                        image_url    = pic.get("imageUrl") or ""
                        original_url = pic.get("originalUrl") or ""
                        downloads = []
                        if original_url:
                            fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{_pid}.bin"
                            downloads.append({"url": original_url, "filename": fname,
                                              "type": "original", "format": "", "size": "", "source": "zxart"})
                        if image_url and image_url != original_url:
                            fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{_pid}.png"
                            downloads.append({"url": image_url, "filename": fname_img,
                                              "type": "preview (PC)", "format": "", "size": "", "source": "zxart"})
                        return (str(pic.get("title") or _title), downloads)
                    def _on_ok(res, _cb=callback):
                        t2, dls = res
                        dls = _filter_download_urls(dls)
                        self._zxart_selected_title     = t2
                        self._zxart_selected_downloads = dls
                        self.zxart_download_button.setEnabled(bool(dls))
                        viewer.set_download_available(bool(dls))
                        _cb(t2, dls)
                    def _on_err(err):
                        zxart_set_status(f"Detail error: {err[1]}")
                    self._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
                else:
                    def _fn():
                        rel_resp = zxart_fetch_json(
                            f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(_pid)}"
                        )
                        releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                        prod_resp = zxart_fetch_json(
                            f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(_pid)}"
                        )
                        prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                        prod  = prods[0] if prods else {}
                        downloads = []
                        for rel in releases:
                            if not isinstance(rel, dict): continue
                            file_url  = rel.get("file") or ""
                            file_name = rel.get("fileName") or (
                                os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else "")
                            if not file_url: continue
                            downloads.append({
                                "url": file_url, "filename": file_name,
                                "type": f"{rel.get('releaseType') or ''} / {rel.get('releaseFormat') or ''}".strip(" /") or "release",
                                "format": rel.get("releaseFormat") or "",
                                "size": "", "source": rel.get("title") or "zxart",
                            })
                        return (str(prod.get("title") or _title), downloads)
                    def _on_ok(res, _cb=callback):
                        t2, dls = res
                        dls = _filter_download_urls(dls)
                        self._zxart_selected_title     = t2
                        self._zxart_selected_downloads = dls
                        self.zxart_download_button.setEnabled(bool(dls))
                        viewer.set_download_available(bool(dls))
                        _cb(t2, dls)
                    def _on_err(err):
                        zxart_set_status(f"Detail error: {err[1]}")
                    self._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            def _dl_btn():
                _ensure_detail_then(lambda t, dls: zxart_show_downloads_overlay(t, dls))
            def _sd_btn():
                _ensure_detail_then(lambda t, dls: _zxart_send_to_image(t, dls))
            _captured_ns_base = _ns_base
            def _ns_btn():
                def _do(t, dls):
                    def _after(_folder):
                        QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                    _zxart_send_to_path(t, dls, _captured_ns_base, _after)
                _ensure_detail_then(_do)

            viewer.set_actions(
                download_cb=_dl_btn, send_sd_cb=_sd_btn, send_ns_cb=_ns_btn,
                sd_enabled=_sd_ok, sd_tooltip=_sd_dest,
                ns_enabled=True,   ns_tooltip=_ns_dest,
            )
            self._wire_viewer_emulators(
                viewer, allow=ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS)
            viewer.set_open_web_url(zxart_entry_website_url(entry), "zxart.ee")
            # If downloads are disabled globally, hide all action buttons immediately.
            if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                viewer.set_download_available(False)
            elif self._zxart_selected_id == eid:
                viewer.set_download_available(
                    bool(_filter_download_urls(self._zxart_selected_downloads or []))
                )

            # ── async enrich (screenshots + full metadata) ──────────────
            def _fn():
                if kind == "zxart_picture":
                    pic_resp = zxart_fetch_json(
                        f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(eid)}"
                    )
                    pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                    pic  = pics[0] if pics else {}
                    image_url = pic.get("imageUrl") or ""
                    screenshots = [image_url] if image_url else []
                    _raw_rating = pic.get("rating")
                    rating = str(_raw_rating) if _raw_rating is not None else ""
                    rows = [
                        (_zxart_tr("Title:"),       str(pic.get("title") or title)),
                        (_zxart_tr("Year:"),        str(pic.get("year") or "")),
                        (_zxart_tr("Authors:"),     ", ".join(str(a) for a in (pic.get("authors") or []))),
                        (_zxart_tr("Type:"),        str(pic.get("type") or "")),
                        (_zxart_tr("Rating:"),      _gallery_stars(rating) if rating else ""),
                        (_zxart_tr("Views:"),       str(pic.get("views") or "")),
                        (_zxart_tr("Description:"), str(pic.get("description") or "")),
                    ]
                    return (screenshots, rows, str(pic.get("title") or title))
                else:
                    prod_resp = zxart_fetch_json(
                        f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(eid)}"
                    )
                    prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                    prod  = prods[0] if prods else {}
                    # Also pull releases so we can derive hardware/format tags.
                    try:
                        rel_resp = zxart_fetch_json(
                            f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(eid)}",
                            timeout=20,
                        )
                        releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                    except Exception:
                        releases = []
                    screenshots = [u for u in (prod.get("imagesUrls") or []) if u]
                    votes        = prod.get("votes")
                    votes_amount = prod.get("votesAmount")
                    try:
                        # "votes" is already the average score (e.g. 4.14 out of 5);
                        # "votesAmount" is the number of voters — do NOT divide.
                        rating = f"{float(votes):.2f}" if votes is not None else ""
                    except (TypeError, ValueError):
                        rating = ""
                    pub_ids_fs = prod.get("publishersIds") or []
                    publishers_fs = _zxart_resolve_publisher_names(pub_ids_fs)
                    if not publishers_fs:
                        publishers_fs = _zxart_scrape_publishers_from_prod_url(
                            str(prod.get("url") or "")
                        )
                    grp_ids_fs = prod.get("groupsIds") or []
                    produced_by_fs = _zxart_resolve_group_names(grp_ids_fs)
                    rows = [
                        (_zxart_tr("Title:"),       str(prod.get("title") or title)),
                        (_zxart_tr("Year:"),        str(prod.get("year") or "")),
                        (_zxart_tr("Authors:"),     ", ".join(str(a) for a in (prod.get("authors") or []))),
                        (_zxart_tr("Groups:"),      ", ".join(str(g) for g in (prod.get("groups")  or []))),
                        (_zxart_tr("Produced by:"), produced_by_fs),
                        (_zxart_tr("Published by:"), publishers_fs),
                        (_zxart_tr("Compo:"),       str(prod.get("compo") or "")),
                        (_zxart_tr("Place:"),       str(prod.get("partyPlace") or "")),
                        (_zxart_tr("Rating:"),      _gallery_stars(rating) if rating else ""),
                        (_zxart_tr("Language:"),    str(prod.get("language") or "")),
                        (_zxart_tr("Legal:"),       zxart_legal_status_label(prod.get("legalStatus") or "")),
                        (_zxart_tr("Description:"), str(prod.get("description") or ""), True),
                    ]
                    return (screenshots, rows, str(prod.get("title") or title), releases)

            def _on_ok(res):
                if len(res) == 4:
                    screenshots, rows, fetched_title, releases = res
                else:
                    screenshots, rows, fetched_title = res
                    releases = None
                if screenshots:
                    viewer.set_screenshots(screenshots)
                else:
                    _ph_label = "FILE"
                    if releases:
                        for _rel in releases:
                            if not isinstance(_rel, dict):
                                continue
                            _fn2 = _rel.get("fileName") or ""
                            if _fn2:
                                _ph_label = zxfmt_label_for_name(_fn2)
                                break
                        if _ph_label == "FILE":
                            _fmts = []
                            for _rel in releases:
                                if not isinstance(_rel, dict):
                                    continue
                                _v = _rel.get("releaseFormat")
                                if isinstance(_v, list):
                                    _fmts.extend([str(x) for x in _v if x])
                                elif _v:
                                    _fmts.append(str(_v))
                            if _fmts:
                                _ph_label = zxfmt_label_for_name("x." + _fmts[0].lower())
                    viewer.set_placeholder(_ph_label, fetched_title)
                _gallery_viewer_refresh_meta(viewer, fetched_title, rows)
                # Mirror the ZXDB viewer: once metadata resolves, (re)assert the
                # action-button visibility so "Download" / "Send to SD card" /
                # "Send via NextSync" are shown when downloads are enabled for
                # this pane. Without this the buttons stay hidden if the entry
                # was selected (single-clicked) before opening, because no
                # downloads were cached yet at setup time.
                if ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                    if releases is not None:
                        # Build the candidate download URLs the same way as the
                        # button handlers (_ensure_detail_then) and filter them,
                        # so visibility matches what a download would actually
                        # produce.
                        _dls = []
                        for _rel in releases:
                            if not isinstance(_rel, dict):
                                continue
                            _file_url = _rel.get("file") or ""
                            if _file_url:
                                _dls.append({"url": _file_url})
                        _has_dl = bool(_filter_download_urls(_dls))
                    else:
                        # Pictures always expose at least the image itself.
                        _has_dl = bool(screenshots)
                    viewer.set_download_available(_has_dl)
                if releases:
                    try:
                        src2 = entry.get("_source") or {}
                        src2["releases"] = releases
                        entry["_source"] = src2
                        viewer.set_tags(_gallery_extract_tags(entry))
                    except Exception:
                        pass

            def _on_err(_e): viewer.set_placeholder("FILE", title)
            self._zxart_gallery_viewer_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            # ── push into pane stack ────────────────────────────────────
            if install:
                viewer.install_into_stack(
                    self._zxart_stack,
                    close_fn=lambda: self._zxart_stack.setCurrentIndex(0),
                )
            return viewer

        self.zxart_gallery_view.cell_dbl_clicked.connect(_zxart_open_gallery_viewer)

        def _zxart_apply_view_mode(mode: str, *, persist: bool = True):
            mode = (mode or "table").lower()
            if mode not in ("table", "gallery"):
                mode = "table"
            self._zxart_view_mode = mode
            self.zxart_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
            _table = (mode == "table")
            if hasattr(self, '_zxart_preview_container'):
                self._zxart_preview_container.setVisible(_table)
            if hasattr(self, '_zxart_preview_download_btn'):
                self._zxart_preview_download_btn.setVisible(
                    _table and ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS
                )
            cb = self.zxart_view_combo
            target_idx = 1 if mode == "gallery" else 0
            if cb.currentIndex() != target_idx:
                cb.blockSignals(True)
                cb.setCurrentIndex(target_idx)
                cb.blockSignals(False)
            if persist:
                # sync other panes to the same view mode
                if hasattr(self, '_getit_apply_view_mode'):
                    self._getit_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxdb_apply_view_mode'):
                    self._zxdb_apply_view_mode(mode, persist=False)
                if hasattr(self, '_favorites_apply_view_mode'):
                    self._favorites_apply_view_mode(mode, persist=False)
                if hasattr(self, '_allinone_apply_view_mode'):
                    self._allinone_apply_view_mode(mode, persist=False)
                configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
                configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
                configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
                save_configuration_file()

        self._zxart_apply_view_mode = _zxart_apply_view_mode

        def _on_zxart_view_combo_changed(_idx):
            _zxart_apply_view_mode(self.zxart_view_combo.currentData() or "table")

        self.zxart_view_combo.currentIndexChanged.connect(_on_zxart_view_combo_changed)
        _zxart_apply_view_mode(self._zxart_view_mode, persist=False)

        # ---- Language selector ----

        # Initialise combo from the global zxART language (already populated
        # from cfg in load_configuration_file when present).
        def _zxart_sync_language_combo():
            code = _zxart_lang()
            cb = self.zxart_language_combo
            for i in range(cb.count()):
                if cb.itemData(i) == code:
                    if cb.currentIndex() != i:
                        cb.blockSignals(True)
                        cb.setCurrentIndex(i)
                        cb.blockSignals(False)
                    break

        _zxart_sync_language_combo()

        def _zxart_retranslate_ui():
            try:
                self.zxart_search_button.setText(_zxart_tr("Search"))
                self.zxart_random_button.setText(_zxart_tr("Random"))
                self.zxart_latest_button.setText(_zxart_tr("Latest"))
                for i, (_lbl, _key) in enumerate(
                    (("Productions", "prods"),
                     ("By letter",  "byletter"),
                     ("Pictures",   "pictures"))
                ):
                    self.zxart_mode_combo.setItemText(i, _zxart_tr(_lbl))
                self.zxart_page_text_label.setText(_zxart_tr("Page:"))
                self.zxart_prev_button.setText(_zxart_tr("< Prev"))
                self.zxart_next_button.setText(_zxart_tr("Next >"))
                self.zxart_view_text_label.setText(_zxart_tr("View:"))
                self.zxart_view_combo.setItemText(0, _zxart_tr("Table"))
                self.zxart_view_combo.setItemText(1, _zxart_tr("Gallery"))
                self.zxart_language_text_label.setText(_zxart_tr("Language:"))
                self.zxart_download_button.setText(_zxart_tr("Download File"))
                # Re-apply table headers for the current mode
                headers_map = {
                    "prods":    ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
                    "byletter": ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
                    "pictures": ["ID", "Title", "Year", "Author(s)",      "Type", "Tags"],
                }
                mode = zxart_current_mode()
                self.zxart_results_table.setHorizontalHeaderLabels(
                    [_zxart_tr(h) for h in headers_map.get(mode, headers_map["prods"])]
                )
                # Translate the "No preview" placeholder when no pixmap is shown
                if self.zxart_screenshot_label.pixmap() is None or self.zxart_screenshot_label.pixmap().isNull():
                    cur = self.zxart_screenshot_label.text()
                    if cur in ("No preview", "Brak podglądu", "Sin vista previa", ""):
                        self.zxart_screenshot_label.setText(_zxart_tr("No preview"))
            except Exception as _exc:
                logging.warning("zxart: retranslate UI failed: %s", _exc)

        self._zxart_retranslate_ui = _zxart_retranslate_ui

        def _on_zxart_language_changed(_idx):
            code = self.zxart_language_combo.currentData() or DEFAULT_ZXART_LANGUAGE
            _zxart_set_language(code)
            configuration_dictionary[SETTING_ZXART_LANGUAGE] = _zxart_lang()
            save_configuration_file()
            # Update all static UI labels to the new language.
            _zxart_retranslate_ui()
            # Re-run the current view so titles/metadata reload in the new language.
            try:
                zxart_clear_detail()
            except Exception:
                pass
            try:
                zxart_run_search(self._zxart_last_query or "",
                                 max(1, self._zxart_current_page))
            except Exception as _exc:
                logging.warning("zxart: language reload failed: %s", _exc)

        self.zxart_language_combo.currentIndexChanged.connect(_on_zxart_language_changed)

        # ---- Download ----

        def zxart_pick_default_download():
            if not self._zxart_selected_downloads:
                return None
            preferred_ext = (".tap", ".tzx", ".z80", ".sna", ".trd", ".dsk", ".scl", ".bin")
            for d in self._zxart_selected_downloads:
                u = (d.get("url") or "").lower()
                if any(u.endswith(ext) for ext in preferred_ext):
                    return d
            return self._zxart_selected_downloads[0]

        def zxart_do_download(d: dict):
            url = d.get("url", "")
            if not url:
                return
            base = os.path.basename(urllib.parse.urlparse(url).path) or f"{self._zxart_selected_id}.bin"
            save_path, _ = QFileDialog.getSaveFileName(None, "Save file", base)
            if not save_path:
                return
            zxart_set_status(f"Downloading {base}…")
            self.zxart_download_button.setEnabled(False)

            def _fn():
                data = zxart_fetch_bytes(url, timeout=60)
                with open(save_path, "wb") as f:
                    f.write(data)
                return save_path

            def _on_ok(p):
                zxart_set_status(f"Saved to {p}  ↗ open folder", open_path=os.path.abspath(p))
                self.zxart_download_button.setEnabled(bool(self._zxart_selected_downloads))

            def _on_err(err):
                zxart_set_status(f"Download error: {err[1]}")
                self.zxart_download_button.setEnabled(bool(self._zxart_selected_downloads))

            self._zxart_dl_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxart_on_download_clicked():
            d = zxart_pick_default_download()
            if d:
                zxart_do_download(d)

        self.zxart_download_button.clicked.connect(zxart_on_download_clicked)

        # ---- Downloads overlay dialog ----

        def zxart_show_downloads_overlay(title: str, downloads: list):
            if not downloads:
                zxart_set_status("No downloadable files for this entry.")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Downloads — {title}")
            dlg.resize(1180, 460)
            v = QVBoxLayout(dlg)

            info = QLabel(
                f"<b>{len(downloads)}</b> file(s) for <b>{title}</b>. "
                f"'Download all' saves into downloads\\{zxart_sanitize_folder(title)}\\"
            )
            info.setWordWrap(True)
            v.addWidget(info)

            # cols: 0-Type 1-Filename 2-Size 3-Source 4-URL 5-Avail. 6-Download 7-SD 8-NextSync
            COL_AVAIL = 5
            COL_DL    = 6
            COL_SD    = 7
            COL_NS    = 8
            tbl = QTableWidget(len(downloads), 9, dlg)
            tbl.setHorizontalHeaderLabels(["Type", "Filename", "Size", "Source", "URL", "Avail.", "", "", ""])
            tbl.verticalHeader().setVisible(False)
            tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setTextElideMode(Qt.ElideMiddle)
            tbl.horizontalHeader().setStretchLastSection(False)
            tbl.setColumnWidth(0, 160)
            tbl.setColumnWidth(2, 90)
            tbl.setColumnWidth(3, 180)
            tbl.setColumnWidth(COL_AVAIL, 52)
            tbl.setColumnWidth(COL_DL, 100)
            tbl.setColumnWidth(COL_SD, 140)
            tbl.setColumnWidth(COL_NS, 160)
            if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                for _c in (COL_SD, COL_NS):
                    tbl.setColumnWidth(_c, 0)
                    tbl.setColumnHidden(_c, True)
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
            tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

            folder_root = os.path.abspath(os.path.join("downloads", zxart_sanitize_folder(title)))
            _ns_base_dlg = _zxart_resolve_base_path(
                self.left_file_nextsync_explorer_selection_full_filename_path)

            # Per-row availability: None=pending, True=ok, False=404/error
            _avail: list = [None] * len(downloads)

            def _set_avail_cell(row: int, ok: bool):
                item = QTableWidgetItem("✅" if ok else "❌")
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(Qt.darkGreen if ok else Qt.red)
                item.setToolTip("File is available" if ok else "File returned 404 / unreachable")
                _avail[row] = ok
                tbl.setItem(row, COL_AVAIL, item)
                _active_cols = [COL_DL] if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS else [COL_DL, COL_SD, COL_NS]
                for _col in _active_cols:
                    btn_w = tbl.cellWidget(row, _col)
                    if btn_w is not None:
                        btn_w.setEnabled(ok)

            def _check_url(row: int, url: str):
                def _fn():
                    return _http_head_ok_with_retry(
                        zxart_safe_url(url), headers={"User-Agent": ZXART_USER_AGENT}, timeout=10
                    )
                def _on_ok(result):
                    _set_avail_cell(row, bool(result))
                def _on_err(_):
                    _set_avail_cell(row, False)
                getit_run_in_thread(_fn, _on_ok, _on_err)

            def _make_dl_handler(d):
                def _go():
                    fname = d.get("filename") or os.path.basename(
                        urllib.parse.urlparse(d.get("url", "")).path
                    ) or "file.bin"
                    save_path = os.path.join(folder_root, fname)
                    zxart_set_status(f"Downloading {fname}…")
                    def _ok(p):
                        zxart_set_status(f"Saved {fname}  ↗ open folder", open_path=os.path.dirname(os.path.abspath(p)))
                    def _err(e):
                        zxart_set_status(f"Download error: {e[1]}")
                    zxart_download_to_path(d.get("url", ""), save_path, _ok, _err)
                return _go

            def _make_sd_handler(d):
                def _go():
                    if not right_disk_image_explorer_content or not self.right_disk_image_path:
                        zxart_set_status("Please load a disk image first (SD Card tab).")
                        return
                    _zxart_send_to_image(title, [d])
                return _go

            def _make_ns_handler(d):
                def _go():
                    def _after(_folder):
                        QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                    _zxart_send_to_path(title, [d], _ns_base_dlg, _after)
                return _go

            for row, d in enumerate(downloads):
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or ""
                tbl.setItem(row, 0, QTableWidgetItem(d.get("type") or d.get("format") or ""))
                tbl.setItem(row, 1, QTableWidgetItem(fname))
                tbl.setItem(row, 2, QTableWidgetItem(zxart_human_size(d.get("size"))))
                tbl.setItem(row, 3, QTableWidgetItem(d.get("source") or ""))
                url_text = d.get("url", "") or ""
                url_item = QTableWidgetItem(url_text)
                url_item.setToolTip(url_text)
                tbl.setItem(row, 4, url_item)
                # Availability placeholder until HEAD check completes
                avail_item = QTableWidgetItem("⏳")
                avail_item.setTextAlignment(Qt.AlignCenter)
                avail_item.setToolTip("Checking availability…")
                tbl.setItem(row, COL_AVAIL, avail_item)
                # Action buttons disabled until availability is confirmed
                btn = QPushButton("Download")
                btn.setEnabled(False)
                btn.clicked.connect(_make_dl_handler(d))
                tbl.setCellWidget(row, COL_DL, btn)

                if ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                    sd_btn = QPushButton("Send to SD Card")
                    sd_btn.setEnabled(False)
                    sd_btn.clicked.connect(_make_sd_handler(d))
                    tbl.setCellWidget(row, COL_SD, sd_btn)

                    ns_btn = QPushButton("Send via NextSync")
                    ns_btn.setEnabled(False)
                    ns_btn.clicked.connect(_make_ns_handler(d))
                    tbl.setCellWidget(row, COL_NS, ns_btn)

            v.addWidget(tbl, 1)

            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            dl_all_btn = QPushButton(f"Download all → downloads\\{zxart_sanitize_folder(title)}")
            sd_all_btn = QPushButton("Send all to SD Card")
            ns_all_btn = QPushButton("Send all via NextSync")
            close_btn  = QPushButton("Close")
            btn_row.addWidget(dl_all_btn)
            if ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                btn_row.addWidget(sd_all_btn)
                btn_row.addWidget(ns_all_btn)
            btn_row.addWidget(close_btn)
            v.addLayout(btn_row)

            close_btn.clicked.connect(dlg.accept)

            def _eligible():
                return [d for i, d in enumerate(downloads) if _avail[i] is not False]

            def _send_all_sd():
                if not right_disk_image_explorer_content or not self.right_disk_image_path:
                    zxart_set_status("Please load a disk image first (SD Card tab).")
                    return
                items = _eligible()
                if not items:
                    zxart_set_status("All files are unavailable (404).")
                    return
                _zxart_send_to_image(title, items)

            def _send_all_ns():
                items = _eligible()
                if not items:
                    zxart_set_status("All files are unavailable (404).")
                    return
                def _after(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: self._nextsync_start_server_fn(_f))
                _zxart_send_to_path(title, items, _ns_base_dlg, _after)

            sd_all_btn.clicked.connect(_send_all_sd)
            ns_all_btn.clicked.connect(_send_all_ns)

            def _download_all():
                dl_all_btn.setEnabled(False)
                dl_all_btn.setText("Downloading…")
                # Skip files confirmed unavailable (404); include pending/ok ones
                eligible = [d for i, d in enumerate(downloads) if _avail[i] is not False]
                if not eligible:
                    dl_all_btn.setText("Nothing to download")
                    zxart_set_status("All files are unavailable (404).")
                    return
                pending = {"n": len(eligible), "ok": 0, "ko": 0}

                def _maybe_finish():
                    if pending["ok"] + pending["ko"] >= pending["n"]:
                        dl_all_btn.setText(f"Done — {pending['ok']} ok, {pending['ko']} failed")
                        if pending["ok"] > 0:
                            zxart_set_status(
                                f"Downloaded {pending['ok']}/{pending['n']} file(s) into {folder_root}  ↗ open folder",
                                open_path=folder_root
                            )
                        else:
                            zxart_set_status(f"All {pending['n']} download(s) failed — check the URLs")

                for d in eligible:
                    fname = d.get("filename") or os.path.basename(
                        urllib.parse.urlparse(d.get("url", "")).path
                    ) or "file.bin"
                    save_path = os.path.join(folder_root, fname)
                    def _ok(p, _f=fname):
                        pending["ok"] += 1
                        zxart_set_status(f"Saved {_f}")
                        _maybe_finish()
                    def _err(e, _f=fname):
                        pending["ko"] += 1
                        zxart_set_status(f"Failed {_f}: {e[1]}")
                        _maybe_finish()
                    zxart_download_to_path(d.get("url", ""), save_path, _ok, _err)

            dl_all_btn.clicked.connect(_download_all)

            # Fire HEAD checks for every URL now that the table and callbacks are ready
            avail_check_enabled = getattr(self, "settings_avail_check_checkbox", None)
            avail_check_enabled = avail_check_enabled is not None and avail_check_enabled.isChecked()
            if avail_check_enabled:
                for row, d in enumerate(downloads):
                    url_to_check = d.get("url", "")
                    if url_to_check:
                        _check_url(row, url_to_check)
                    else:
                        _set_avail_cell(row, False)
            else:
                # Setting is off — enable all Download buttons immediately, hide placeholders
                for row in range(len(downloads)):
                    avail_item = tbl.item(row, COL_AVAIL)
                    if avail_item:
                        avail_item.setText("")
                        avail_item.setToolTip("Availability check disabled in Settings")
                    for _col in (COL_DL, COL_SD, COL_NS):
                        btn_w = tbl.cellWidget(row, _col)
                        if btn_w is not None:
                            btn_w.setEnabled(True)

            _ticker_lbl, _ticker_timer = _make_disclaimer_ticker(dlg)
            v.addWidget(_ticker_lbl)

            dlg.exec()

        # ---- Context menu ----

        def zxart_on_table_context_menu(pos):
            item = self.zxart_results_table.itemAt(pos)
            if item is None:
                return
            row = self.zxart_results_table.row(item)
            id_item    = self.zxart_results_table.item(row, 0)
            title_item = self.zxart_results_table.item(row, 1)
            if not id_item:
                return
            pid   = id_item.text()
            title = title_item.text() if title_item else pid
            entry = id_item.data(Qt.UserRole) or {}
            kind  = entry.get("_kind", "zxart_prod")

            self.zxart_results_table.selectRow(row)

            _img_path   = self.right_disk_image_path or ""
            _img_label  = (generate_disk_file_path().rstrip("/") + "/" + zxart_sanitize_folder(title)
                           ) if _img_path else "(no image loaded)"
            _sd_dest    = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base    = _zxart_resolve_base_path(self.left_file_nextsync_explorer_selection_full_filename_path)
            _safe_title = zxart_sanitize_folder(title)
            _ns_dest    = os.path.join(_ns_base, _safe_title)

            menu = QMenu(self.zxart_results_table)
            act_download = menu.addAction("Download content")
            menu.addSeparator()
            act_send_sd  = menu.addAction(f"Send to SD card (image)  →  {_sd_dest}")
            act_send_sd.setEnabled(bool(self.right_disk_image_path) and bool(right_disk_image_explorer_content))
            act_send_ns  = menu.addAction(f"Send using NextSync  →  {_ns_dest}")
            if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                act_download.setVisible(False)
                act_send_sd.setVisible(False)
                act_send_ns.setVisible(False)
            menu.addSeparator()
            _web_url = zxart_entry_website_url(entry)
            act_open_web = menu.addAction("Open on website (zxart.ee)")
            act_open_web.setEnabled(bool(_web_url))
            action = menu.exec(self.zxart_results_table.viewport().mapToGlobal(pos))
            if action is None:
                return
            if action is act_open_web:
                if _web_url:
                    try:
                        webbrowser.open(_web_url, new=2)
                    except Exception:
                        pass
                return

            def _ensure_detail_then(callback):
                """If detail for this row is already loaded, call callback immediately."""
                if self._zxart_selected_id == pid and self._zxart_selected_downloads:
                    callback(self._zxart_selected_title or title, self._zxart_selected_downloads)
                    return
                zxart_set_status(f"Loading {pid}…")
                if kind == "zxart_picture":
                    def _fn():
                        pic_resp = zxart_fetch_json(
                            f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(pid)}"
                        )
                        pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                        pic  = pics[0] if pics else (entry.get("_source") or {})
                        image_url    = pic.get("imageUrl") or ""
                        original_url = pic.get("originalUrl") or ""
                        downloads = []
                        if original_url:
                            fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{pid}.bin"
                            downloads.append({"url": original_url, "filename": fname, "type": "original",
                                              "format": "", "size": "", "source": "zxart"})
                        if image_url and image_url != original_url:
                            fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{pid}.png"
                            downloads.append({"url": image_url, "filename": fname_img, "type": "preview (PC)",
                                              "format": "", "size": "", "source": "zxart"})
                        return (str(pic.get("title") or title), downloads)
                    def _on_ok(res, _cb=callback):
                        t2, dls = res
                        self._zxart_selected_title = t2
                        self._zxart_selected_downloads = dls
                        self.zxart_download_button.setEnabled(bool(dls))
                        _cb(t2, dls)
                    def _on_err(err):
                        zxart_set_status(f"Detail error: {err[1]}")
                    self._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
                else:
                    def _fn():
                        rel_resp = zxart_fetch_json(
                            f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(pid)}"
                        )
                        releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                        prod_resp = zxart_fetch_json(
                            f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(pid)}"
                        )
                        prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                        prod  = prods[0] if prods else {}
                        downloads = []
                        for rel in releases:
                            if not isinstance(rel, dict):
                                continue
                            file_url  = rel.get("file") or ""
                            file_name = rel.get("fileName") or (
                                os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else ""
                            )
                            if not file_url:
                                continue
                            downloads.append({
                                "url":      file_url,
                                "filename": file_name,
                                "type":     f"{rel.get('releaseType') or ''} / {rel.get('releaseFormat') or ''}".strip(" /") or "release",
                                "format":   rel.get("releaseFormat") or "",
                                "size":     "",
                                "source":   rel.get("title") or "zxart",
                            })
                        return (str(prod.get("title") or title), downloads)
                    def _on_ok(res, _cb=callback):
                        t2, dls = res
                        self._zxart_selected_title = t2
                        self._zxart_selected_downloads = dls
                        self.zxart_download_button.setEnabled(bool(dls))
                        _cb(t2, dls)
                    def _on_err(err):
                        zxart_set_status(f"Detail error: {err[1]}")
                    self._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            if action is act_download:
                def _show(t, dls):
                    zxart_show_downloads_overlay(t, dls)
                _ensure_detail_then(_show)

            elif action is act_send_sd:
                def _send_sd(t, dls):
                    _zxart_send_to_image(t, dls)
                _ensure_detail_then(_send_sd)

            elif action is act_send_ns:
                def _send_ns(t, dls, _nb=_ns_base):
                    def _after(_folder):
                        QTimer.singleShot(0, self._nextsync_start_server_fn)
                    _zxart_send_to_path(t, dls, _nb, _after)
                _ensure_detail_then(_send_ns)

        self.zxart_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.zxart_results_table.customContextMenuRequested.connect(zxart_on_table_context_menu)

        # ---- Fullscreen preview overlay ----

        zxart_container = QWidget()
        zxart_container.setLayout(self.zxart_form)
        zxart_container.setAutoFillBackground(False)
        zxart_container.setAttribute(Qt.WA_TranslucentBackground)

        zxart_scroll = QScrollArea()
        zxart_scroll.setWidget(zxart_container)
        zxart_scroll.setWidgetResizable(True)
        zxart_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        zxart_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        zxart_scroll.setAutoFillBackground(False)
        zxart_scroll.setAttribute(Qt.WA_TranslucentBackground)
        zxart_scroll.viewport().setAutoFillBackground(False)
        zxart_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

        # Fixed search/button header above the scrollable results so the
        # vertical scroller only covers the content area (like the Unite! tab).
        zxart_normal_widget = QWidget()
        zxart_normal_widget.setAutoFillBackground(False)
        zxart_normal_widget.setAttribute(Qt.WA_TranslucentBackground)
        zxart_normal_layout = QVBoxLayout(zxart_normal_widget)
        zxart_normal_layout.setContentsMargins(0, 0, 0, 0)
        zxart_normal_layout.setSpacing(0)
        zxart_normal_layout.addWidget(self._zxart_search_widget, 0)
        zxart_normal_layout.addWidget(zxart_scroll, 1)

        self._zxart_fullscreen_pixmap = None

        zxart_overlay = QWidget()
        zxart_overlay.setStyleSheet("background: #000;")
        zxart_overlay_layout = QVBoxLayout(zxart_overlay)
        zxart_overlay_layout.setContentsMargins(0, 0, 0, 0)
        zxart_overlay_layout.setSpacing(0)

        zxart_close_btn = QToolButton()
        zxart_close_btn.setText("✕")
        zxart_close_btn.setStyleSheet(
            "QToolButton { color: white; background: #333; border: none; font-size: 18px; padding: 4px 8px; }"
            "QToolButton:hover { background: #c00; }"
        )
        zxart_close_bar = QHBoxLayout()
        zxart_close_bar.setContentsMargins(4, 4, 4, 0)
        zxart_close_bar.addWidget(zxart_close_btn, 0)
        zxart_close_bar.addStretch()
        zxart_close_bar_widget = QWidget()
        zxart_close_bar_widget.setLayout(zxart_close_bar)
        zxart_overlay_layout.addWidget(zxart_close_bar_widget, 0)

        self.zxart_fullscreen_label = QLabel()
        self.zxart_fullscreen_label.setAlignment(Qt.AlignCenter)
        self.zxart_fullscreen_label.setStyleSheet("background: #000;")
        self.zxart_fullscreen_label.setCursor(Qt.PointingHandCursor)
        zxart_overlay_layout.addWidget(self.zxart_fullscreen_label, 1)

        _zxart_fs_nav_style = (
            "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
            " font-size: 32px; font-weight: bold; padding: 4px 10px; }"
            "QToolButton:hover { background: rgba(0,0,0,220); }"
        )
        self.zxart_fs_prev_btn = QToolButton(zxart_overlay)
        self.zxart_fs_prev_btn.setText("<")
        self.zxart_fs_prev_btn.setStyleSheet(_zxart_fs_nav_style)
        self.zxart_fs_prev_btn.setVisible(False)
        self.zxart_fs_prev_btn.raise_()

        self.zxart_fs_next_btn = QToolButton(zxart_overlay)
        self.zxart_fs_next_btn.setText(">")
        self.zxart_fs_next_btn.setStyleSheet(_zxart_fs_nav_style)
        self.zxart_fs_next_btn.setVisible(False)
        self.zxart_fs_next_btn.raise_()

        def _zxart_reposition_fs_btns():
            ow = zxart_overlay.width()
            oh = zxart_overlay.height()
            bh = self.zxart_fs_prev_btn.sizeHint().height()
            by = (oh - bh) // 2
            self.zxart_fs_prev_btn.move(8, by)
            bw = self.zxart_fs_next_btn.sizeHint().width()
            self.zxart_fs_next_btn.move(ow - bw - 8, by)

        self._zxart_reposition_fs_btns = _zxart_reposition_fs_btns
        self.zxart_fs_prev_btn.clicked.connect(_zxart_nav_prev)
        self.zxart_fs_next_btn.clicked.connect(_zxart_nav_next)

        self._zxart_stack = QStackedWidget()
        self._zxart_stack.setAutoFillBackground(False)
        self._zxart_stack.setAttribute(Qt.WA_TranslucentBackground)
        self._zxart_stack.addWidget(zxart_normal_widget)
        self._zxart_stack.addWidget(zxart_overlay)
        self._zxart_stack.setCurrentIndex(0)

        def _zxart_show_fullscreen():
            px = self.zxart_screenshot_label.pixmap()
            if px is None or px.isNull():
                return
            self._zxart_fullscreen_pixmap = px
            self._zxart_stack.setCurrentIndex(1)
            _zxart_resize_fullscreen()
            self._zxart_reposition_fs_btns()
            zxart_update_nav_buttons()

        def _zxart_hide_fullscreen():
            self._zxart_stack.setCurrentIndex(0)
            zxart_update_nav_buttons()
        self._hide_fullscreen_zxart = _zxart_hide_fullscreen

        def _zxart_resize_fullscreen():
            px = self._zxart_fullscreen_pixmap
            if px and not px.isNull():
                sz = self.zxart_fullscreen_label.size()
                self.zxart_fullscreen_label.setPixmap(
                    px.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            self._zxart_reposition_fs_btns()

        zxart_close_btn.clicked.connect(_zxart_hide_fullscreen)
        self.zxart_fullscreen_label.mousePressEvent = lambda e: _zxart_hide_fullscreen()

        self._zxart_dbl_filter = _DblClickFilter(_zxart_show_fullscreen)
        self.zxart_screenshot_label.installEventFilter(self._zxart_dbl_filter)
        self.zxart_screenshot_label.setCursor(Qt.PointingHandCursor)

        def zxart_on_tab_activated():
            if self._zxart_loaded_once or self._zxart_search_loading:
                return
            self._zxart_loaded_once = True
            # Skip default load if a cross-search already populated results
            if self._zxart_last_query:
                return
            # Load latest productions on first activation
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            def _zxart_initial_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, self.zxart_results_table.rowCount())
            zxart_run_search("", 1, _zxart_initial_done)

        self._zxart_on_tab_activated = zxart_on_tab_activated

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
        grid_tab_nextsync.addWidget(nextsync_container) # here use the form container
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
            # Stop the poll timer before unparenting so it never fires against
            # the destroyed child widgets (zxart_cache_progress_bar etc.).
            self._zxart_cache_poll_timer.stop()
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

        # Create ONLINE Favorites Tab (right of zxArt, before Settings)
        zxnextunite_Favorites_tab = QWidget(wid_inner.tab)
        zxnextunite_Favorites_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_Favorites_tab.setAutoFillBackground(False)
        grid_tab_favorites = QGridLayout(zxnextunite_Favorites_tab)
        grid_tab_favorites.setContentsMargins(0, 0, 0, 0)

        def _fav_title_getter(e):
            src = (e.get("_fav_source") or e.get("source") or "").lower()
            fetch = (self._fav_fetchers or {}).get(src) or {}
            tg = fetch.get("title")
            if tg:
                try:
                    return tg(e)
                except Exception:
                    pass
            return str(e.get("title") or e.get("id") or "")

        def _fav_info_getter(e):
            src = (e.get("_fav_source") or e.get("source") or "").lower()
            fetch = (self._fav_fetchers or {}).get(src) or {}
            ig = fetch.get("info")
            if ig:
                try:
                    return ig(e)
                except Exception:
                    pass
            parts = []
            if e.get("author"): parts.append(str(e["author"]))
            if e.get("year"):   parts.append(str(e["year"]))
            return " · ".join(parts)

        def _fav_thumb_fetch(entry, set_pixmap, set_screenshots,
                             set_tags=None, set_info_text=None):
            src = (entry.get("_fav_source") or entry.get("source") or "").lower()
            fetch = (self._fav_fetchers or {}).get(src) or {}
            tf = fetch.get("thumb")
            if tf is None:
                return
            try:
                # Each pane's thumb fetcher has a slightly different signature.
                if src == "getit":
                    tf(entry, set_pixmap, set_screenshots,
                       set_tags=set_tags, set_info_text=set_info_text)
                elif src == "zxart":
                    tf(entry, set_pixmap, set_screenshots, set_tags=set_tags)
                else:
                    tf(entry, set_pixmap, set_screenshots)
            except Exception:
                pass

        def _fav_extra_fetch(url, on_pixmap):
            for src in ("getit", "zxdb", "zxart"):
                fetch = (self._fav_fetchers or {}).get(src) or {}
                ef = fetch.get("extra")
                if ef is not None:
                    try:
                        ef(url, on_pixmap)
                    except Exception:
                        pass

        def _fav_context_menu(entry, global_pos):
            menu = QMenu()
            src_lbl = self._fav_source_label_for(entry) or "source"
            act_go = menu.addAction(f"Open in {src_lbl}")
            act_rm = menu.addAction("Remove from Favorites")
            chosen = menu.exec(global_pos)
            if chosen is act_go:
                self._fav_navigate_to_source(entry)
            elif chosen is act_rm:
                self._fav_toggle(entry)

        self.favorites_gallery_view = GalleryView(
            rows_per_page_getter=lambda: self._gallery_rows_per_page,
            anim_mode_getter=lambda: self._gallery_anim_mode,
            cols_getter=lambda: self._gallery_cols,
            img_size_getter=lambda: self._gallery_img_size,
            thumb_fetch_cb=_fav_thumb_fetch,
            extra_fetch_cb=_fav_extra_fetch,
            title_getter=_fav_title_getter,
            info_getter=_fav_info_getter,
            context_menu_cb=_fav_context_menu,
            is_favorite_cb=lambda e: True,
            toggle_favorite_cb=lambda e: self._fav_toggle(e),
            source_label_getter=self._fav_source_label_for,
        )

        def _fav_open_fullscreen(entry):
            if not isinstance(entry, dict):
                return
            src = self._fav_source_of(entry)
            if src == "getit":
                target_title = ZX_NEXT_UNITE_TAB_TITLE_GETIT
                opener = _getit_open_gallery_viewer
            elif src == "zxdb":
                target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXDB
                opener = _zxdb_open_gallery_viewer
            elif src == "zxart":
                target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXART
                opener = _zxart_open_gallery_viewer
            else:
                self._fav_navigate_to_source(entry)
                return
            # Switch to the source tab so the viewer stack is visible.
            for i in range(self._tab_widget.count()):
                if self._tab_widget.tabText(i).startswith(target_title):
                    self._tab_widget.setCurrentIndex(i)
                    break
            try:
                opener(entry)
            except Exception:
                pass

        self._fav_open_fullscreen = _fav_open_fullscreen

        def _fav_on_cell_dbl_clicked(entry):
            _fav_open_fullscreen(entry)
        self.favorites_gallery_view.cell_dbl_clicked.connect(_fav_on_cell_dbl_clicked)

        # ── View: Table / Gallery selector row ──────────────────────────────
        fav_top_row = QHBoxLayout()
        fav_top_row.setContentsMargins(0, 0, 0, 0)
        self.favorites_view_text_label = QLabel("View:")
        fav_top_row.addWidget(self.favorites_view_text_label)
        self.favorites_view_combo = QComboBox()
        self.favorites_view_combo.addItem("Table",   "table")
        self.favorites_view_combo.addItem("Gallery", "gallery")
        self.favorites_view_combo.setToolTip(
            "Switch between the classic table view and the picture (gallery) view.\n"
            "Persisted across sessions in the config file."
        )
        fav_top_row.addWidget(self.favorites_view_combo)
        fav_top_row.addStretch(1)

        # ── Table view of favorites ────────────────────────────────────────
        self.favorites_results_table = QTableWidget(0, 5)
        self.favorites_results_table.setHorizontalHeaderLabels(
            ["Source", "Title", "Rating", "Info", "Year"]
        )
        self.favorites_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.favorites_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.favorites_results_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.favorites_results_table.verticalHeader().setVisible(False)
        try:
            hh = self.favorites_results_table.horizontalHeader()
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(1, QHeaderView.Stretch)
            hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(3, QHeaderView.Stretch)
            hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        except Exception:
            pass

        def _fav_table_entry_for_row(row):
            try:
                if row < 0 or row >= self.favorites_results_table.rowCount():
                    return None
                it = self.favorites_results_table.item(row, 0)
                if it is None:
                    return None
                idx = it.data(Qt.UserRole)
                if isinstance(idx, int) and 0 <= idx < len(self._favorites):
                    return self._favorites[idx]
            except Exception:
                pass
            return None

        def _fav_table_on_double_clicked(_idx):
            row = self.favorites_results_table.currentRow()
            entry = _fav_table_entry_for_row(row)
            if entry is not None:
                _fav_open_fullscreen(entry)

        def _fav_table_on_context_menu(pos):
            row = self.favorites_results_table.rowAt(pos.y())
            entry = _fav_table_entry_for_row(row)
            if entry is None:
                return
            self.favorites_results_table.selectRow(row)
            _fav_context_menu(
                entry,
                self.favorites_results_table.viewport().mapToGlobal(pos),
            )

        self.favorites_results_table.doubleClicked.connect(_fav_table_on_double_clicked)
        self.favorites_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.favorites_results_table.customContextMenuRequested.connect(
            _fav_table_on_context_menu
        )

        # ── Stack the two views ────────────────────────────────────────────
        self.favorites_view_stack = QStackedWidget()
        self.favorites_view_stack.addWidget(self.favorites_results_table)   # idx 0 = table
        self.favorites_view_stack.addWidget(self.favorites_gallery_view)    # idx 1 = gallery

        fav_container = QWidget()
        fav_v = QVBoxLayout(fav_container)
        fav_v.setContentsMargins(0, 0, 0, 0)
        fav_v.addLayout(fav_top_row)
        fav_v.addWidget(self.favorites_view_stack)
        fav_container.setLayout(fav_v)

        grid_tab_favorites.addWidget(fav_container)
        zxnextunite_Favorites_tab.setLayout(grid_tab_favorites)
        zxnextunite_Favorites_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_FAVORITES
        wid_inner.tab.addTab(zxnextunite_Favorites_tab,
                             f"{ZX_NEXT_UNITE_TAB_TITLE_FAVORITES} (0)")

        # ─── ONLINE: AllInOne Tab ───────────────────────────────────────────
        # Aggregated gallery view of the last GetIt + ZXDB + zxArt search
        # results. A dedicated search box always runs across the 3 sources
        # (this pane has no source of its own). Each tile shows a source tag
        # (bottom-left) so the user can tell which pane produced it. Double-
        # click opens the proper source-specific full-screen viewer (same
        # routing as Favorites).
        zxnextunite_AllInOne_tab = QWidget(wid_inner.tab)
        zxnextunite_AllInOne_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_AllInOne_tab.setAutoFillBackground(False)
        allinone_v = QVBoxLayout(zxnextunite_AllInOne_tab)
        allinone_v.setContentsMargins(4, 4, 4, 4)

        # --- Search row ---
        allinone_search_row = QHBoxLayout()
        self.allinone_search_input = QLineEdit()
        self.allinone_search_input.setPlaceholderText(
            "Search across GetIt + ZXDB + zxArt..."
        )
        self.allinone_search_input.setMinimumWidth(280)
        allinone_search_row.addWidget(self.allinone_search_input)

        self._allinone_search_valid_lbl = QLabel()
        self._allinone_search_valid_lbl.setVisible(False)
        allinone_search_row.addWidget(self._allinone_search_valid_lbl)

        self.allinone_search_button = QPushButton("Search")
        allinone_search_row.addWidget(self.allinone_search_button)

        self.allinone_latest_button = QPushButton("Latest")
        self.allinone_latest_button.setToolTip(
            "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here"
        )
        allinone_search_row.addWidget(self.allinone_latest_button)

        self.allinone_random_button = QPushButton("Random")
        self.allinone_random_button.setToolTip(
            "Fetch random entries from GetIt + ZXDB + zxArt and merge them here"
        )
        allinone_search_row.addWidget(self.allinone_random_button)

        allinone_search_row.addWidget(QLabel("Page:"))
        self.allinone_page_label = QLabel("1")
        self.allinone_page_label.setMinimumWidth(24)
        allinone_search_row.addWidget(self.allinone_page_label)

        self.allinone_prev_button = QPushButton("< Prev")
        self.allinone_prev_button.setEnabled(False)
        allinone_search_row.addWidget(self.allinone_prev_button)

        self.allinone_next_button = QPushButton("Next >")
        self.allinone_next_button.setEnabled(False)
        allinone_search_row.addWidget(self.allinone_next_button)

        allinone_search_row.addWidget(QLabel("View:"))
        self.allinone_view_combo = QComboBox()
        self.allinone_view_combo.addItem("Table",   "table")
        self.allinone_view_combo.addItem("Gallery", "gallery")
        self.allinone_view_combo.setToolTip(
            "Switch between the classic table view and the picture (gallery) view.\n"
            "Persisted across sessions in the config file."
        )
        allinone_search_row.addWidget(self.allinone_view_combo)

        self.allinone_pygame_button = QPushButton("🎮 Pygame")
        self.allinone_pygame_button.setCheckable(True)
        self.allinone_pygame_button.setToolTip(
            "Switch the Unite! Table & Gallery views to a pygame-rendered\n"
            "visualization. Click again to return to the classic views.\n"
            "Requires the optional 'pygame-ce' package."
        )
        allinone_search_row.addWidget(self.allinone_pygame_button)

        self.allinone_status_label = QLabel("")
        allinone_search_row.addWidget(self.allinone_status_label, 1)

        allinone_v.addLayout(allinone_search_row)

        # --- Preview panel (right column, shown only in Table view) ---
        self.allinone_screenshot_label = QLabel()
        self.allinone_screenshot_label.setFixedSize(256, 192)
        self.allinone_screenshot_label.setAlignment(Qt.AlignCenter)
        self.allinone_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
        self.allinone_screenshot_label.setText("No preview")
        self.allinone_screenshot_label.setToolTip("Double-click to open full view")

        allinone_right_col = QVBoxLayout()
        allinone_right_col.addWidget(self.allinone_screenshot_label)
        allinone_right_col.addStretch()
        allinone_right_widget = QWidget()
        allinone_right_widget.setLayout(allinone_right_col)
        self._allinone_right_widget = allinone_right_widget
        self._allinone_preview_label = self.allinone_screenshot_label
        # Initially hidden; _allinone_apply_view_mode will show it in Table mode.
        self.allinone_screenshot_label.setVisible(False)

        # --- Aggregated gallery view ---
        _ALLINONE_SOURCE_LABELS = {"getit": "GetIt", "zxdb": "ZXDB", "zxart": "ZXArt"}

        def _allinone_source_label(e):
            try:
                src = (e.get("_fav_source") or e.get("source") or "").lower()
            except Exception:
                src = ""
            return _ALLINONE_SOURCE_LABELS.get(src, "")

        self.allinone_gallery_view = GalleryView(
            rows_per_page_getter=lambda: self._gallery_rows_per_page,
            anim_mode_getter=lambda: self._gallery_anim_mode,
            cols_getter=lambda: self._gallery_cols,
            img_size_getter=lambda: self._gallery_img_size,
            thumb_fetch_cb=_fav_thumb_fetch,
            extra_fetch_cb=_fav_extra_fetch,
            title_getter=_fav_title_getter,
            info_getter=_fav_info_getter,
            is_favorite_cb=lambda e: self._fav_is(e),
            toggle_favorite_cb=lambda e: self._fav_toggle(e),
            source_label_getter=_allinone_source_label,
            source_overlay_anchor="bottomright",
        )

        def _allinone_on_cell_dbl_clicked(entry):
            try:
                _fav_open_fullscreen(entry)
            except Exception:
                pass
        self.allinone_gallery_view.cell_dbl_clicked.connect(_allinone_on_cell_dbl_clicked)

        # --- Aggregated table view (mirrors Favorites) ---
        self.allinone_results_table = QTableWidget(0, 5)
        self.allinone_results_table.setHorizontalHeaderLabels(
            ["Source", "Title", "Rating", "Info", "Year"]
        )
        self.allinone_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.allinone_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.allinone_results_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.allinone_results_table.verticalHeader().setVisible(False)
        try:
            hh = self.allinone_results_table.horizontalHeader()
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(1, QHeaderView.Stretch)
            hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(3, QHeaderView.Stretch)
            hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        except Exception:
            pass

        def _allinone_table_entry_for_row(row):
            try:
                if row < 0 or row >= self.allinone_results_table.rowCount():
                    return None
                it = self.allinone_results_table.item(row, 0)
                if it is None:
                    return None
                entry = it.data(Qt.UserRole)
                if isinstance(entry, dict):
                    return entry
            except Exception:
                pass
            return None

        def _allinone_table_on_double_clicked(_idx):
            row = self.allinone_results_table.currentRow()
            entry = _allinone_table_entry_for_row(row)
            if entry is not None:
                _fav_open_fullscreen(entry)

        self.allinone_results_table.doubleClicked.connect(_allinone_table_on_double_clicked)

        # --- Row selection → load preview image ---
        def _allinone_on_row_selected():
            rows = self.allinone_results_table.selectedItems()
            if not rows:
                return
            row = self.allinone_results_table.currentRow()
            entry = _allinone_table_entry_for_row(row)
            if entry is None:
                return
            self.allinone_screenshot_label.setText("Loading…")
            self.allinone_screenshot_label.setPixmap(QPixmap())
            # Delegate thumbnail fetch to the shared _fav_thumb_fetch which
            # routes to the correct source-specific fetcher.
            def _set_pixmap(px, _url=None):
                try:
                    if px is None or px.isNull():
                        self.allinone_screenshot_label.setText("No preview")
                    else:
                        self.allinone_screenshot_label.setPixmap(
                            px.scaled(256, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                except RuntimeError:
                    pass
            def _set_screenshots(_urls):
                pass  # Not needed for the simple preview label
            try:
                _fav_thumb_fetch(entry, _set_pixmap, _set_screenshots)
            except Exception:
                self.allinone_screenshot_label.setText("No preview")

        self.allinone_results_table.itemSelectionChanged.connect(_allinone_on_row_selected)

        # Double-click on preview opens full view
        from PySide6.QtCore import QEvent as _QEvent

        def _allinone_preview_dbl_click(event):
            if event.type() == _QEvent.MouseButtonDblClick:
                row = self.allinone_results_table.currentRow()
                entry = _allinone_table_entry_for_row(row)
                if entry is not None:
                    _fav_open_fullscreen(entry)
            # Let the label handle the event normally
            return QLabel.event(self.allinone_screenshot_label, event)

        self.allinone_screenshot_label.event = _allinone_preview_dbl_click
        self.allinone_screenshot_label.setCursor(Qt.PointingHandCursor)

        # --- Stack the two views (table = idx 0, gallery = idx 1) ---
        self.allinone_view_stack = QStackedWidget()
        self.allinone_view_stack.addWidget(self.allinone_results_table)   # idx 0 = table
        self.allinone_view_stack.addWidget(self.allinone_gallery_view)    # idx 1 = gallery

        # Paging state for the AllInOne aggregated view (client-side paging).
        self._allinone_all_entries = []
        self._allinone_current_page = 1
        self._allinone_total_pages = 1

        # Wrap view_stack + right preview widget in a horizontal row
        allinone_table_row = QHBoxLayout()
        allinone_table_row.addWidget(self.allinone_view_stack, 1)
        allinone_table_row.addWidget(allinone_right_widget)
        allinone_table_container = QWidget()
        allinone_table_container.setLayout(allinone_table_row)
        allinone_v.addWidget(allinone_table_container)
        zxnextunite_AllInOne_tab.setLayout(allinone_v)
        zxnextunite_AllInOne_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_ALLINONE

        # Insert AllInOne *before* GetIt in the tab bar.
        _getit_tab_index = wid_inner.tab.indexOf(zxnextunite_GetIt_tab)
        if _getit_tab_index < 0:
            _getit_tab_index = wid_inner.tab.count()
        wid_inner.tab.insertTab(
            _getit_tab_index, zxnextunite_AllInOne_tab,
            f"{ZX_NEXT_UNITE_TAB_TITLE_ALLINONE} (0)"
        )

        # Start the AllInOne tab text color cycling animation. Give every
        # other tab an explicit readable text color first, since the
        # stylesheet no longer sets one (so setTabTextColor can take effect
        # on the AllInOne tab without being overridden).
        try:
            _tab_bar = self._tab_widget.tabBar()
            _default_tab_color = QColor("#dddddd")
            for _i in range(self._tab_widget.count()):
                if "Unite!" not in self._tab_widget.tabText(_i):
                    _tab_bar.setTabTextColor(_i, _default_tab_color)
        except Exception:
            pass
        self._allinone_color_timer.start()

        # --- Aggregation + tab badge ---
        def _allinone_collect():
            merged = []
            for src, attr in (("getit", "_getit_last_entries"),
                              ("zxdb",  "_zxdb_last_entries"),
                              ("zxart", "_zxart_last_entries")):
                lst = getattr(self, attr, None) or []
                for e in lst:
                    if not isinstance(e, dict):
                        continue
                    tagged = {**e, "_fav_source": src}
                    merged.append(tagged)
            return merged

        def _allinone_update_tab_badge(n):
            try:
                # While the AllInOne search spinner is running, leave the tab
                # text to the spinner (rotating earth). The final count is
                # applied once the spinner stops.
                if getattr(self, "_spinner_tabs", None) and \
                        ZX_NEXT_UNITE_TAB_TITLE_ALLINONE in self._spinner_tabs:
                    return
                for i in range(self._tab_widget.count()):
                    if self._tab_widget.tabText(i).startswith(
                            ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
                        self._tab_widget.setTabText(
                            i, f"{ZX_NEXT_UNITE_TAB_TITLE_ALLINONE} ({n})")
                        break
            except Exception:
                pass

        def _allinone_fill_table(page_entries):
            try:
                import re as _re
                tbl = self.allinone_results_table
                tbl.setRowCount(0)
                for entry in page_entries:
                    if not isinstance(entry, dict):
                        continue
                    src_lbl = _ALLINONE_SOURCE_LABELS.get(
                        (entry.get("_fav_source") or entry.get("source") or "").lower(), ""
                    )
                    raw_title = _fav_title_getter(entry) or ""
                    _span_match = _re.search(r'<span[^>]*>([^<]+)</span>', raw_title)
                    rating = _span_match.group(1).strip() if _span_match else ""
                    title = _re.sub(r'<[^>]+>', '', raw_title).strip()
                    _plain_match = _re.search(r'\s*[★☆]+\s*[\d.,]*\s*(?:\([^)]*\))?\s*$', title)
                    if _plain_match:
                        if not rating:
                            rating = _plain_match.group(0).strip()
                        title = title[:_plain_match.start()].strip()
                    info  = _fav_info_getter(entry) or ""
                    year  = str(entry.get("year") or "")
                    row = tbl.rowCount()
                    tbl.insertRow(row)
                    src_item    = QTableWidgetItem(src_lbl)
                    src_item.setData(Qt.UserRole, entry)
                    title_item  = QTableWidgetItem(title)
                    rating_item = QTableWidgetItem(rating)
                    rating_item.setTextAlignment(Qt.AlignCenter)
                    info_item   = QTableWidgetItem(info)
                    year_item   = QTableWidgetItem(year)
                    tbl.setItem(row, 0, src_item)
                    tbl.setItem(row, 1, title_item)
                    tbl.setItem(row, 2, rating_item)
                    tbl.setItem(row, 3, info_item)
                    tbl.setItem(row, 4, year_item)
            except Exception:
                pass

        def _allinone_repopulate():
            try:
                entries = _allinone_collect()
                # Cache the full merged list so prev/next can re-slice without
                # re-aggregating, and keep page state coherent across reloads.
                self._allinone_all_entries = entries
                total = len(entries)
                total_pages = max(1, (total + ALLINONE_PAGE_SIZE - 1) // ALLINONE_PAGE_SIZE)
                # Clamp current page if the merged set shrank (e.g. fewer
                # results after a new search).
                cur = getattr(self, "_allinone_current_page", 1) or 1
                if cur > total_pages:
                    cur = total_pages
                if cur < 1:
                    cur = 1
                self._allinone_current_page = cur
                self._allinone_total_pages = total_pages
                start = (cur - 1) * ALLINONE_PAGE_SIZE
                end = start + ALLINONE_PAGE_SIZE
                page_entries = entries[start:end]
                self.allinone_gallery_view.populate(page_entries)
                _allinone_fill_table(page_entries)
                if getattr(self, "_allinone_pygame_widget", None) is not None:
                    try:
                        self._allinone_pygame_feed()
                    except Exception:
                        pass
                _allinone_update_tab_badge(total)
                try:
                    self.allinone_page_label.setText(str(cur))
                    self.allinone_prev_button.setEnabled(cur > 1)
                    self.allinone_next_button.setEnabled(cur < total_pages)
                    self.allinone_status_label.setText(
                        f"{total} result(s)  |  page {cur}/{total_pages}"
                    )
                except Exception:
                    pass
            except Exception:
                pass

        self._allinone_repopulate = _allinone_repopulate

        # --- Paging handlers (client-side over the merged result list) ---
        def allinone_on_prev():
            cur = getattr(self, "_allinone_current_page", 1) or 1
            if cur <= 1:
                return
            self._allinone_current_page = cur - 1
            _allinone_repopulate()

        def allinone_on_next():
            cur = getattr(self, "_allinone_current_page", 1) or 1
            total_pages = getattr(self, "_allinone_total_pages", 1) or 1
            if cur >= total_pages:
                return
            self._allinone_current_page = cur + 1
            _allinone_repopulate()

        self.allinone_prev_button.clicked.connect(allinone_on_prev)
        self.allinone_next_button.clicked.connect(allinone_on_next)

        # --- Search handler: always fan out to GetIt + ZXDB + zxArt ---
        def allinone_on_search():
            q = self.allinone_search_input.text().strip()
            if q and len(q) < SEARCH_MIN_CHARS:
                return
            # Suppress the autocomplete suggestions popup once a search is
            # submitted; it stays hidden until the user types again.
            self._allinone_ac_block = True
            try:
                _allinone_ac_timer.stop()
            except Exception:
                pass
            try:
                self._allinone_completer.popup().hide()
            except Exception:
                pass
            # Reset paging on a new search so results start at page 1.
            self._allinone_current_page = 1
            # Mirror the query into each source pane's input box so the
            # user can see/edit it there too.
            try:
                self.getit_search_input.setText(q)
            except Exception:
                pass
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                try:
                    self.zxdb_search_input.setText(q)
                except Exception:
                    pass
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                try:
                    self.zxart_search_input.setText(q)
                except Exception:
                    pass
            # Clear stale badges before searching.
            try:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            except Exception:
                pass
            # Show the rotating-earth animation on the AllInOne tab while any
            # of the source searches are still running. We count how many
            # sources we kicked off and stop the spinner once they have all
            # reported back, then refresh the aggregated badge count.
            sources = [_cross_search_getit]
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                sources.append(_cross_search_zxdb)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                sources.append(_cross_search_zxart)

            pending = {"count": len(sources)}

            def _allinone_source_done():
                pending["count"] -= 1
                if pending["count"] <= 0:
                    _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)
                    try:
                        _allinone_repopulate()
                    except Exception:
                        pass

            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)

            # Run the same searches the per-source panes use. Each will
            # call its populate_results, which in turn refreshes the
            # AllInOne gallery via _allinone_repopulate.
            try:
                _cross_search_getit(q, _allinone_source_done)
            except Exception:
                _allinone_source_done()
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                try:
                    _cross_search_zxdb(q, _allinone_source_done)
                except Exception:
                    _allinone_source_done()
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                try:
                    _cross_search_zxart(q, _allinone_source_done)
                except Exception:
                    _allinone_source_done()

        def _allinone_search_validate(text: str):
            t = text.strip()
            if not t:
                self._allinone_search_valid_lbl.setVisible(False)
            elif len(t) < SEARCH_MIN_CHARS:
                self._allinone_search_valid_lbl.setText(
                    f"Min {SEARCH_MIN_CHARS} chars")
                self._allinone_search_valid_lbl.setStyleSheet("color: #c33;")
                self._allinone_search_valid_lbl.setVisible(True)
            else:
                self._allinone_search_valid_lbl.setVisible(False)

        self.allinone_search_input.textChanged.connect(_allinone_search_validate)
        self.allinone_search_button.clicked.connect(allinone_on_search)
        self.allinone_search_input.returnPressed.connect(allinone_on_search)

        # --- Shared fan-out driver for Random/Latest on the Unite! tab.
        # Kicks off every supplied source action (each taking an on_complete
        # callback), drives the rotating-earth spinner on the AllInOne tab,
        # and refreshes the aggregated gallery once every source has reported
        # back. Each per-source completion is counted at most once, and a
        # watchdog timer guarantees the spinner is always cleared even if a
        # source's callback is dropped by a supersede race — preventing the
        # "forever-spinning earth" symptom.
        def _allinone_fanout(actions):
            self._allinone_current_page = 1
            try:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            except Exception:
                pass

            state = {"pending": len(actions), "done": False}

            watchdog = QTimer(self)
            watchdog.setSingleShot(True)
            watchdog.setInterval(30000)

            def _finish():
                if state["done"]:
                    return
                state["done"] = True
                try:
                    watchdog.stop()
                except Exception:
                    pass
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)
                try:
                    _allinone_repopulate()
                except Exception:
                    pass

            def _make_done():
                fired = {"v": False}

                def _done():
                    # Guard against a source invoking its callback twice.
                    if fired["v"]:
                        return
                    fired["v"] = True
                    state["pending"] -= 1
                    if state["pending"] <= 0:
                        _finish()
                return _done

            watchdog.timeout.connect(_finish)

            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)
            watchdog.start()

            if not actions:
                _finish()
                return

            for action in actions:
                done_cb = _make_done()
                try:
                    action(done_cb)
                except Exception:
                    done_cb()

        # --- Random handler: fan out to GetIt + ZXDB + zxArt Random buttons.
        # Each per-source random handler clears its own search box, drives its
        # tab spinner/badge, and ultimately refreshes the AllInOne gallery via
        # _allinone_repopulate once the shared fan-out driver reports done.
        def allinone_on_random():
            # Clear the AllInOne search box too, so the pane reflects the
            # "random" mode rather than a stale query.
            try:
                self.allinone_search_input.clear()
            except Exception:
                pass

            actions = [lambda cb: getit_on_random(cb)]
            # ZXDB random — only meaningful in 'games' mode; the button there
            # is auto-disabled outside of it, so guard accordingly.
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                try:
                    zxdb_games_mode = self.zxdb_random_button.isEnabled()
                except Exception:
                    zxdb_games_mode = False
                if zxdb_games_mode:
                    actions.append(lambda cb: zxdb_on_random(cb))
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                actions.append(lambda cb: zxart_on_random(cb))

            _allinone_fanout(actions)

        self.allinone_random_button.clicked.connect(allinone_on_random)

        # --- Latest handler: fan out to GetIt + ZXDB + zxArt "Latest" actions.
        # Each per-source latest handler clears its own search box, drives its
        # tab spinner/badge, and fetches the most recent releases. The shared
        # fan-out driver runs the rotating-earth spinner on the AllInOne tab
        # until every source has reported back, then refreshes the gallery.
        def allinone_on_latest():
            # Clear the AllInOne search box so the pane reflects "latest"
            # mode rather than a stale query.
            try:
                self.allinone_search_input.clear()
            except Exception:
                pass
            # Suppress the autocomplete suggestions popup once latest is
            # requested; it stays hidden until the user types again.
            self._allinone_ac_block = True
            try:
                _allinone_ac_timer.stop()
            except Exception:
                pass
            try:
                self._allinone_completer.popup().hide()
            except Exception:
                pass

            actions = [lambda cb: getit_on_latest(cb)]
            # ZXDB latest — zxdb_on_latest forces 'games' mode itself.
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                actions.append(lambda cb: zxdb_on_latest(cb))
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                actions.append(lambda cb: zxart_on_latest(cb))

            _allinone_fanout(actions)

        self.allinone_latest_button.clicked.connect(allinone_on_latest)
        # Expose so deferred startup activation can trigger the same "Latest"
        # multi-search logic that the button press performs.
        self._allinone_on_latest = allinone_on_latest

        # --- Autocomplete (merge title suggestions from GetIt + ZXDB + zxArt
        #     caches, triggering source-pane fetches on demand). ---
        self._allinone_ac_model = QStringListModel(self)
        _allinone_completer = QCompleter(self._allinone_ac_model, self)
        _allinone_completer.setCompletionMode(QCompleter.PopupCompletion)
        _allinone_completer.setCaseSensitivity(Qt.CaseInsensitive)
        _allinone_completer.setFilterMode(Qt.MatchStartsWith)
        # Ensure the popup follows the main window on Windows
        popup = _allinone_completer.popup()
        if popup is not None:
            popup.setParent(self)
            popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
            popup.setAttribute(Qt.WA_ShowWithoutActivating)
        self._allinone_completer = _allinone_completer
        self.allinone_search_input.setCompleter(_allinone_completer)

        def _allinone_safe_show_popup(q: str):
            try:
                if not self._search_autocomplete_on():
                    return
                if getattr(self, "_allinone_ac_block", False):
                    return
                if not self.allinone_search_input.hasFocus():
                    return
                if self.allinone_search_input.text().strip() != q:
                    return
                if self._allinone_ac_model.rowCount() == 0:
                    return
                _allinone_completer.setCompletionPrefix(q)
                popup = _allinone_completer.popup()
                if popup is None:
                    return
                try:
                    popup.setParent(self.allinone_search_input.window(),
                                    Qt.Tool
                                    | Qt.FramelessWindowHint
                                    | Qt.WindowStaysOnTopHint
                                    | Qt.WindowDoesNotAcceptFocus)
                    popup.setFocusPolicy(Qt.NoFocus)
                    popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
                except Exception:
                    pass
                le = self.allinone_search_input
                rect = le.rect()
                pos = le.mapToGlobal(rect.bottomLeft())
                popup.setMinimumWidth(le.width())
                popup.move(pos)
                popup.resize(le.width(), _popup_height_for(popup, self._allinone_ac_model.rowCount()))
                popup.show()
            except RuntimeError:
                pass
            except Exception:
                pass

        def _allinone_ac_update_model(text: str):
            if not text:
                self._allinone_ac_model.setStringList([])
                return
            self._allinone_ac_filter_gen = (
                getattr(self, "_allinone_ac_filter_gen", 0) + 1
            )
            gen = self._allinone_ac_filter_gen
            tl = text.lower()

            # Snapshot all three caches up-front (cheap shallow copies) so
            # the worker thread doesn't touch shared state while the user
            # keeps typing.
            getit_snapshot = list(getattr(self, "_getit_ac_titles", None) or [])
            zxdb_cache = getattr(self, "_zxdb_ac_cache", None) or {}
            letter = tl[0]
            zxdb_snapshot = list(zxdb_cache.get(letter, []))
            zxart_cache = getattr(self, "_zxart_ac_cache", None) or {}
            zxart_best_pfx = None
            for cached_prefix in zxart_cache.keys():
                if tl.startswith(cached_prefix.lower()):
                    if (zxart_best_pfx is None
                            or len(cached_prefix) > len(zxart_best_pfx)):
                        zxart_best_pfx = cached_prefix
            zxart_snapshot = (
                list(zxart_cache.get(zxart_best_pfx, []))
                if zxart_best_pfx is not None else []
            )

            def _fn():
                merged: dict = {}  # lower-case title -> first-seen original
                for t in getit_snapshot:
                    if not t:
                        continue
                    key = t.lower()
                    if key.startswith(tl) and key not in merged:
                        merged[key] = t
                for t in zxdb_snapshot:
                    if not t:
                        continue
                    key = t.lower()
                    if key.startswith(tl) and key not in merged:
                        merged[key] = t
                for t in zxart_snapshot:
                    if not t:
                        continue
                    key = t.lower()
                    if key.startswith(tl) and key not in merged:
                        merged[key] = t
                matches = sorted(merged.values(), key=str.lower)
                return (gen, text, matches[:80])

            def _on_ok(result):
                rgen, rtext, matches = result
                if rgen != getattr(self, "_allinone_ac_filter_gen", -1):
                    return
                try:
                    if self.allinone_search_input.text().strip() != rtext:
                        return
                except RuntimeError:
                    return
                self._allinone_ac_model.setStringList(matches)
                if matches:
                    QTimer.singleShot(
                        0, lambda q=rtext: _allinone_safe_show_popup(q)
                    )

            def _on_err(_err):
                pass

            getit_run_in_thread(_fn, _on_ok, _on_err)

        def _allinone_ac_notify(_source: str, _key: str):
            """Called by GetIt / ZXDB / zxArt autocomplete fetchers once their
            caches receive new data.  Refresh the AllInOne model so newly
            arrived titles appear in the suggestion list."""
            try:
                text = self.allinone_search_input.text().strip()
                if not text:
                    return
                if not self.allinone_search_input.hasFocus():
                    # Still refresh the model so it's ready when focus returns.
                    _allinone_ac_update_model(text)
                    return
                _allinone_ac_update_model(text)
            except RuntimeError:
                pass
            except Exception:
                pass
            # Stop the placeholder animation once any source has responded
            # and at least one cache is populated.
            try:
                if getattr(self, "_allinone_ac_waiting", False):
                    any_data = (
                        bool(getattr(self, "_getit_ac_titles", None))
                        or bool(getattr(self, "_zxdb_ac_cache", None))
                        or bool(getattr(self, "_zxart_ac_cache", None))
                    )
                    if any_data:
                        self._ac_anim_stop(self.allinone_search_input)
                        self._allinone_ac_waiting = False
            except Exception:
                pass

        self._allinone_ac_notify = _allinone_ac_notify

        # Debounce typing so we don't fire cache priming + filter on every
        # keystroke.
        _allinone_ac_timer = QTimer(self)
        _allinone_ac_timer.setSingleShot(True)
        _allinone_ac_timer.setInterval(200)
        self._allinone_ac_timer = _allinone_ac_timer

        def _allinone_ac_do_work(text: str):
            text = text.strip()
            if not text:
                self._allinone_ac_model.setStringList([])
                if getattr(self, "_allinone_ac_waiting", False):
                    try:
                        self._ac_anim_stop(self.allinone_search_input)
                    except Exception:
                        pass
                    self._allinone_ac_waiting = False
                return
            tl = text.lower()
            need_fetch = False

            # GetIt: prime full title cache once.
            getit_titles = getattr(self, "_getit_ac_titles", None) or []
            if not getit_titles and not getattr(self, "_getit_ac_loading", False):
                starter = getattr(self, "_getit_ac_start_fetch", None)
                if callable(starter):
                    try:
                        starter()
                        need_fetch = True
                    except Exception:
                        pass
            elif getattr(self, "_getit_ac_loading", False):
                need_fetch = True

            # ZXDB: prime the relevant per-letter cache.
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                letter = tl[0]
                zxdb_cache = getattr(self, "_zxdb_ac_cache", None) or {}
                zxdb_fetching = getattr(self, "_zxdb_ac_fetching", None) or set()
                if letter not in zxdb_cache and letter not in zxdb_fetching:
                    fetcher = getattr(self, "_zxdb_ac_fetch_letter", None)
                    if callable(fetcher):
                        try:
                            fetcher(letter)
                            need_fetch = True
                        except Exception:
                            pass
                elif letter in zxdb_fetching:
                    need_fetch = True

            # zxArt: prime a prefix fetch if none of the cached prefixes
            # covers the current text.
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                zxart_cache = getattr(self, "_zxart_ac_cache", None) or {}
                covered = any(
                    tl.startswith(p.lower()) for p in zxart_cache.keys()
                )
                zxart_inflight = (
                    getattr(self, "_zxart_ac_external_fetching", None) or set()
                )
                already_fetching = any(
                    tl.startswith(p.lower()) for p in zxart_inflight
                )
                if not covered and not already_fetching:
                    fetcher = getattr(self, "_zxart_ac_fetch_prefix", None)
                    if callable(fetcher):
                        try:
                            fetcher(text)
                            need_fetch = True
                        except Exception:
                            pass
                elif already_fetching:
                    need_fetch = True

            if need_fetch and not getattr(self, "_allinone_ac_waiting", False):
                try:
                    self._ac_anim_start(self.allinone_search_input)
                    self._allinone_ac_waiting = True
                except Exception:
                    pass
            _allinone_ac_update_model(text)

        def _allinone_ac_trigger():
            if not _search_autocomplete_on():
                try:
                    self._allinone_ac_model.setStringList([])
                except Exception:
                    pass
                return
            try:
                text = self.allinone_search_input.text()
            except RuntimeError:
                return
            _allinone_ac_do_work(text)

        _allinone_ac_timer.timeout.connect(_allinone_ac_trigger)

        def _allinone_ac_on_text_changed(_text: str):
            # Clear the model immediately when the box is emptied so a stale
            # popup doesn't linger while the debounce timer is still pending.
            if not _text.strip():
                self._allinone_ac_model.setStringList([])
            if getattr(self, "_allinone_ac_suppress", False):
                self._allinone_ac_suppress = False
                return
            # The user is typing again: re-enable autocomplete suggestions
            # that were suppressed after the last search submission.
            self._allinone_ac_block = False
            _allinone_ac_timer.start()

        self.allinone_search_input.textChanged.connect(_allinone_ac_on_text_changed)

        def _allinone_ac_activated(selected: str):
            try:
                if selected:
                    self._allinone_ac_suppress = True
                    _allinone_ac_timer.stop()
                    try:
                        _allinone_completer.popup().hide()
                    except Exception:
                        pass
                    self.allinone_search_input.setText(selected)
            except Exception:
                pass
            allinone_on_search()

        _allinone_completer.activated.connect(_allinone_ac_activated)

        def _fav_repopulate():
            try:
                self.favorites_gallery_view.populate(list(self._favorites))
            except Exception:
                pass
            try:
                tbl = self.favorites_results_table
                tbl.setRowCount(0)
                for i, entry in enumerate(self._favorites):
                    import re as _re
                    src_lbl = self._fav_source_label_for(entry) or ""
                    raw_title = _fav_title_getter(entry) or ""
                    # Extract star rating from an HTML <span> if present
                    _span_match = _re.search(r'<span[^>]*>([^<]+)</span>', raw_title)
                    rating = _span_match.group(1).strip() if _span_match else ""
                    # Strip all HTML tags to get a clean title
                    title = _re.sub(r'<[^>]+>', '', raw_title).strip()
                    # Also strip any inline star rating remaining in plain text,
                    # e.g. "Some title★★★★☆  (4.2)" — capture it as rating if not already set
                    _plain_match = _re.search(r'\s*[★☆]+\s*[\d.,]*\s*(?:\([^)]*\))?\s*$', title)
                    if _plain_match:
                        if not rating:
                            rating = _plain_match.group(0).strip()
                        title = title[:_plain_match.start()].strip()
                    info  = _fav_info_getter(entry) or ""
                    year  = str(entry.get("year") or "")
                    row = tbl.rowCount()
                    tbl.insertRow(row)
                    src_item    = QTableWidgetItem(src_lbl)
                    src_item.setData(Qt.UserRole, i)
                    title_item  = QTableWidgetItem(title)
                    rating_item = QTableWidgetItem(rating)
                    rating_item.setTextAlignment(Qt.AlignCenter)
                    info_item   = QTableWidgetItem(info)
                    year_item   = QTableWidgetItem(year)
                    tbl.setItem(row, 0, src_item)
                    tbl.setItem(row, 1, title_item)
                    tbl.setItem(row, 2, rating_item)
                    tbl.setItem(row, 3, info_item)
                    tbl.setItem(row, 4, year_item)
            except Exception:
                pass
        self._fav_repopulate_fn = _fav_repopulate
        _fav_repopulate()
        self._fav_update_tab_badge()

        # ── View-mode apply helper (mirrors GetIt/ZXDB/zxArt) ──────────────
        def _favorites_apply_view_mode(mode: str, *, persist: bool = True):
            mode = (mode or "gallery").lower()
            if mode not in ("table", "gallery"):
                mode = "gallery"
            self._favorites_view_mode = mode
            self.favorites_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
            cb = self.favorites_view_combo
            target_idx = 1 if mode == "gallery" else 0
            if cb.currentIndex() != target_idx:
                cb.blockSignals(True)
                cb.setCurrentIndex(target_idx)
                cb.blockSignals(False)
            if persist:
                if hasattr(self, '_getit_apply_view_mode'):
                    self._getit_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxdb_apply_view_mode'):
                    self._zxdb_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxart_apply_view_mode'):
                    self._zxart_apply_view_mode(mode, persist=False)
                if hasattr(self, '_allinone_apply_view_mode'):
                    self._allinone_apply_view_mode(mode, persist=False)
                configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
                configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
                configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
                save_configuration_file()

        self._favorites_apply_view_mode = _favorites_apply_view_mode

        def _on_favorites_view_combo_changed(_idx):
            _favorites_apply_view_mode(
                self.favorites_view_combo.currentData() or "gallery"
            )

        self.favorites_view_combo.currentIndexChanged.connect(
            _on_favorites_view_combo_changed
        )
        _favorites_apply_view_mode(self._favorites_view_mode, persist=False)

        # ── AllInOne (Unite!) view-mode apply helper (mirrors GetIt/ZXDB/zxArt) ──
        def _allinone_apply_view_mode(mode: str, *, persist: bool = True):
            mode = (mode or "gallery").lower()
            if mode not in ("table", "gallery"):
                mode = "gallery"
            self._allinone_view_mode = mode
            self.allinone_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
            # Show/hide the preview panel based on view mode (Table = visible)
            _table = (mode == "table")
            if hasattr(self, '_allinone_right_widget'):
                self._allinone_right_widget.setVisible(_table)
            if hasattr(self, '_allinone_preview_label'):
                self._allinone_preview_label.setVisible(_table)
            # In pygame mode the same Table/Gallery selection drives the pygame
            # scene instead of the classic Qt stack pages.
            if getattr(self, "_allinone_pygame_on", False) and \
                    getattr(self, "_allinone_pygame_widget", None) is not None:
                self._allinone_pygame_set_scene()
                self.allinone_view_stack.setCurrentWidget(self._allinone_pygame_widget)
                if hasattr(self, '_allinone_right_widget'):
                    self._allinone_right_widget.setVisible(False)
            cb = self.allinone_view_combo
            target_idx = 1 if mode == "gallery" else 0
            if cb.currentIndex() != target_idx:
                cb.blockSignals(True)
                cb.setCurrentIndex(target_idx)
                cb.blockSignals(False)
            if persist:
                if hasattr(self, '_getit_apply_view_mode'):
                    self._getit_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxdb_apply_view_mode'):
                    self._zxdb_apply_view_mode(mode, persist=False)
                if hasattr(self, '_zxart_apply_view_mode'):
                    self._zxart_apply_view_mode(mode, persist=False)
                if hasattr(self, '_favorites_apply_view_mode'):
                    self._favorites_apply_view_mode(mode, persist=False)
                configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
                configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
                configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
                configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
                save_configuration_file()

        self._allinone_apply_view_mode = _allinone_apply_view_mode

        # ── Pygame visualization mode (optional, lazily built) ──────────────
        self._allinone_pygame_on = False
        self._allinone_pygame_widget = None
        self._allinone_pygame_table = None
        self._allinone_pygame_gallery = None
        # Space-Invaders background animation preference (on by default,
        # overridden from the config file by load_configuration_file()).
        self._allinone_pygame_anim = True

        def _allinone_pygame_open_viewer(entry):
            host = self._allinone_pygame_widget
            if not isinstance(entry, dict) or host is None:
                return
            src = self._fav_source_of(entry)
            opener = {
                "getit": _getit_open_gallery_viewer,
                "zxdb":  _zxdb_open_gallery_viewer,
                "zxart": _zxart_open_gallery_viewer,
            }.get(src)
            if opener is None:
                return
            prev = host.scene()
            try:
                from zxnu_pygame import PygameItemViewer
                viewer = opener(
                    entry,
                    make_viewer=lambda **kw: PygameItemViewer(host, **kw),
                    install=False,
                )
            except Exception:
                viewer = None
            if viewer is not None:
                viewer.install_into_stack(None, close_fn=lambda: host.set_scene(prev))
        self._allinone_pygame_open_viewer = _allinone_pygame_open_viewer

        def _allinone_pygame_build():
            if self._allinone_pygame_widget is not None:
                return self._allinone_pygame_widget
            import zxnu_pygame as _zpg
            host = _zpg.PygameSurfaceWidget()
            self._allinone_pygame_table = _zpg.TableScene(
                source_label_getter=_allinone_source_label,
                title_getter=_fav_title_getter,
                info_getter=_fav_info_getter,
                open_cb=_allinone_pygame_open_viewer,
            )
            self._allinone_pygame_gallery = _zpg.GalleryScene(
                title_getter=_fav_title_getter,
                source_label_getter=_allinone_source_label,
                thumb_fetch_cb=_fav_thumb_fetch,
                is_favorite_cb=lambda e: self._fav_is(e),
                toggle_favorite_cb=lambda e: self._fav_toggle(e),
                open_cb=_allinone_pygame_open_viewer,
                cols_getter=lambda: self._gallery_cols,
            )
            self._allinone_pygame_widget = host
            try:
                host.enable_background(getattr(self, "_allinone_pygame_anim", True))
            except Exception:
                pass
            self.allinone_view_stack.addWidget(host)   # idx 2
            return host

        def _allinone_pygame_feed():
            if self._allinone_pygame_widget is None:
                return
            entries = getattr(self, "_allinone_all_entries", []) or []
            cur = getattr(self, "_allinone_current_page", 1) or 1
            start = (cur - 1) * ALLINONE_PAGE_SIZE
            page = entries[start:start + ALLINONE_PAGE_SIZE]
            try:
                self._allinone_pygame_table.set_entries(page)
                self._allinone_pygame_gallery.set_entries(page)
            except Exception:
                pass
        self._allinone_pygame_feed = _allinone_pygame_feed

        def _allinone_pygame_set_scene():
            host = self._allinone_pygame_widget
            if host is None:
                return
            mode = getattr(self, "_allinone_view_mode", "gallery")
            scene = (self._allinone_pygame_gallery if mode == "gallery"
                     else self._allinone_pygame_table)
            host.set_scene(scene)
        self._allinone_pygame_set_scene = _allinone_pygame_set_scene

        def _allinone_pygame_disable(reason=""):
            btn = self.allinone_pygame_button
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.setText("🎮 Pygame")
            btn.blockSignals(False)
            btn.setEnabled(False)
            if reason:
                btn.setToolTip(reason)

        def _allinone_on_pygame_toggled(checked):
            if checked:
                try:
                    from zxnu_pygame import pygame_available
                    ok, why = pygame_available()
                except Exception as exc:
                    ok, why = False, str(exc)
                if not ok:
                    _allinone_pygame_disable(
                        f"{why}\nInstall with: pip install pygame-ce")
                    try:
                        self.allinone_status_label.setText(
                            "Pygame mode unavailable — run: pip install pygame-ce"
                        )
                    except Exception:
                        pass
                    return
                try:
                    _allinone_pygame_build()
                except Exception as exc:
                    _allinone_pygame_disable(f"Pygame init failed: {exc}")
                    return
                self._allinone_pygame_on = True
                self.allinone_pygame_button.setText("🖼 Classic")
                _allinone_pygame_feed()
                _allinone_pygame_set_scene()
                self.allinone_view_stack.setCurrentWidget(self._allinone_pygame_widget)
                try:
                    self._allinone_pygame_widget.enable_background(
                        getattr(self, "_allinone_pygame_anim", True))
                except Exception:
                    pass
                if hasattr(self, "_allinone_right_widget"):
                    self._allinone_right_widget.setVisible(False)
                _allinone_pygame_persist(True)
            else:
                self._allinone_pygame_on = False
                self.allinone_pygame_button.setText("🎮 Pygame")
                _allinone_apply_view_mode(
                    getattr(self, "_allinone_view_mode", "gallery"), persist=False)
                _allinone_pygame_persist(False)

        def _allinone_pygame_persist(enabled):
            # Skip writing while restoring the saved choice at startup so a
            # transient "pygame unavailable" never clobbers the user's pref.
            if getattr(self, "_allinone_pygame_restoring", False):
                return
            try:
                configuration_dictionary[SETTING_ALLINONE_PYGAME_MODE] = (
                    "true" if enabled else "false")
                save_configuration_file()
            except Exception:
                pass

        self.allinone_pygame_button.toggled.connect(_allinone_on_pygame_toggled)

        def _on_allinone_view_combo_changed(_idx):
            _allinone_apply_view_mode(
                self.allinone_view_combo.currentData() or "gallery"
            )

        self.allinone_view_combo.currentIndexChanged.connect(
            _on_allinone_view_combo_changed
        )
        _allinone_apply_view_mode(self._allinone_view_mode, persist=False)

        # Create Settings Tab
        zxnextunite_Settings_tab = QWidget(wid_inner.tab)
        zxnextunite_Settings_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_Settings_tab.setAutoFillBackground(False)
        grid_tab_Settings = QGridLayout(zxnextunite_Settings_tab)

        def settings_warn_image_nearly_full_statechanged():
            configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL] = "true" if self.settings_warn_image_nearly_full_checkbox.isChecked() else "false"
            save_configuration_file()

        self.settings_warn_image_nearly_full_checkbox = QCheckBox("SD Card - Warn when an image is nearly full.")
        self.settings_warn_image_nearly_full_checkbox.setChecked(True)
        self.settings_warn_image_nearly_full_checkbox.setToolTip(
            "When enabled, a warning dialog is shown after loading or writing to an SD image\n"
            "if it has less than 10% free space remaining.\n"
            "Uncheck this option to suppress that warning."
        )
        self.settings_warn_image_nearly_full_checkbox.stateChanged.connect(settings_warn_image_nearly_full_statechanged)
        grid_tab_Settings.addWidget(self.settings_warn_image_nearly_full_checkbox, 0, 0, 1, 2)

        def settings_no_prompt_on_deletion_statechanged():
            configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION] = "true" if self.settings_no_prompt_on_deletion_checkbox.isChecked() else "false"
            save_configuration_file()

        self.settings_no_prompt_on_deletion_checkbox = QCheckBox("SD Card - Do not prompt for confirmation on deletion.")
        self.settings_no_prompt_on_deletion_checkbox.setChecked(False)
        self.settings_no_prompt_on_deletion_checkbox.setToolTip(
            "When enabled, deleting a file or folder in the SD card image explorer\n"
            "will proceed immediately without asking for confirmation.\n"
            "Leave unchecked to keep the confirmation prompt (recommended)."
        )
        self.settings_no_prompt_on_deletion_checkbox.stateChanged.connect(settings_no_prompt_on_deletion_statechanged)
        grid_tab_Settings.addWidget(self.settings_no_prompt_on_deletion_checkbox, 1, 0, 1, 2)

        def settings_avail_check_statechanged():
            configuration_dictionary[SETTING_AVAIL_CHECK] = "true" if self.settings_avail_check_checkbox.isChecked() else "false"
            save_configuration_file()

        self.settings_avail_check_checkbox = QCheckBox("Perform pre-availability check on Downloads (ZXDB & zxArt).")
        self.settings_avail_check_checkbox.setChecked(True)
        self.settings_avail_check_checkbox.setToolTip(
            "When enabled, the Downloads dialog sends a HEAD request for each file\n"
            "to check whether it is reachable before allowing the download.\n"
            "Files that return HTTP 404 are marked with \u274c and their Download button\n"
            "is disabled. Leave unchecked to skip the check (faster dialog open)."
        )
        self.settings_avail_check_checkbox.stateChanged.connect(settings_avail_check_statechanged)
        _avail_check_visible = ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS or ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS
        self.settings_avail_check_checkbox.setVisible(_avail_check_visible)
        grid_tab_Settings.addWidget(self.settings_avail_check_checkbox, 2, 0, 1, 2)

        def settings_multi_search_statechanged():
            configuration_dictionary[SETTING_MULTI_SEARCH] = "true" if self.settings_multi_search_checkbox.isChecked() else "false"
            save_configuration_file()

        self.settings_multi_search_checkbox = QCheckBox("Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).")
        self.settings_multi_search_checkbox.setChecked(True)
        self.settings_multi_search_checkbox.setToolTip(
            "When enabled, a search on any of GetIt, ZXDB or zxArt also runs the\n"
            "same query silently on the other two panes. The tab label is updated\n"
            "with the number of results found, e.g. ZXDB (5)."
        )
        self.settings_multi_search_checkbox.stateChanged.connect(settings_multi_search_statechanged)
        grid_tab_Settings.addWidget(self.settings_multi_search_checkbox, 3, 0, 1, 2)

        def settings_search_autocomplete_statechanged():
            enabled = self.settings_search_autocomplete_checkbox.isChecked()
            configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE] = "true" if enabled else "false"
            save_configuration_file()
            _apply_autocomplete_setting(enabled)

        self.settings_search_autocomplete_checkbox = QCheckBox("Enable search autocompletion.")
        self.settings_search_autocomplete_checkbox.setChecked(True)
        self.settings_search_autocomplete_checkbox.setToolTip(
            "When enabled, typing in any search box shows an autocomplete dropdown\n"
            "with matching titles fetched from the respective API.\n"
            "Uncheck to disable autocomplete suggestions on all search inputs."
        )
        self.settings_search_autocomplete_checkbox.stateChanged.connect(settings_search_autocomplete_statechanged)
        grid_tab_Settings.addWidget(self.settings_search_autocomplete_checkbox, 4, 0, 1, 2)

        # ---- Gallery (picture view) settings ----
        def _settings_gallery_anim_changed():
            data = self.settings_gallery_anim_combo.currentData() or DEFAULT_GALLERY_ANIM_MODE
            self._gallery_anim_mode = data
            configuration_dictionary[SETTING_GALLERY_ANIM_MODE] = data
            save_configuration_file()

        gallery_anim_lbl = QLabel("Gallery animation:")
        gallery_anim_lbl.setToolTip(
            "Controls when multi-screenshot tiles cycle through their images\n"
            "in the GetIt / ZXDB / zxArt 'Gallery' (picture) view.\n"
            "  • On hover (default): cycles only while the mouse is over the tile.\n"
            "  • Timed: cycles continuously while the gallery is visible."
        )
        grid_tab_Settings.addWidget(gallery_anim_lbl, 5, 0)

        self.settings_gallery_anim_combo = QComboBox()
        self.settings_gallery_anim_combo.addItem("On hover (default)", "hover")
        self.settings_gallery_anim_combo.addItem("Timed",              "timer")
        self.settings_gallery_anim_combo.setToolTip(gallery_anim_lbl.toolTip())
        self.settings_gallery_anim_combo.currentIndexChanged.connect(
            lambda _i: _settings_gallery_anim_changed()
        )
        grid_tab_Settings.addWidget(self.settings_gallery_anim_combo, 5, 1)

        def _settings_gallery_rows_changed(val: int):
            val = max(GALLERY_MIN_ROWS, min(GALLERY_MAX_ROWS, int(val)))
            self._gallery_rows_per_page = val
            configuration_dictionary[SETTING_GALLERY_ROWS_PER_PAGE] = str(val)
            save_configuration_file()

        gallery_rows_lbl = QLabel("Gallery rows per page (min):")
        gallery_rows_lbl.setToolTip(
            "Number of thumbnail rows shown per gallery page.\n"
            f"Range {GALLERY_MIN_ROWS}–{GALLERY_MAX_ROWS}. Default {DEFAULT_GALLERY_ROWS_PER_PAGE}."
        )
        grid_tab_Settings.addWidget(gallery_rows_lbl, 6, 0)

        self.settings_gallery_rows_spin = QSpinBox()
        self.settings_gallery_rows_spin.setRange(GALLERY_MIN_ROWS, GALLERY_MAX_ROWS)
        self.settings_gallery_rows_spin.setValue(DEFAULT_GALLERY_ROWS_PER_PAGE)
        self.settings_gallery_rows_spin.setToolTip(gallery_rows_lbl.toolTip())
        self.settings_gallery_rows_spin.valueChanged.connect(_settings_gallery_rows_changed)
        grid_tab_Settings.addWidget(self.settings_gallery_rows_spin, 6, 1)

        def _make_color_button(setting_key, color_attr, label_text, tooltip_text, grid_row):
            """Create a label + color-swatch button at the given grid row."""
            lbl = QLabel(label_text)
            lbl.setToolTip(tooltip_text)
            grid_tab_Settings.addWidget(lbl, grid_row, 0)

            btn = QPushButton()
            btn.setFixedSize(80, 22)
            btn.setToolTip(tooltip_text)

            def _update_swatch(color: QColor):
                setattr(self, color_attr, color)
                configuration_dictionary[setting_key] = qcolor_to_hex(color)
                btn.setStyleSheet(f"background-color: {qcolor_to_hex(color)}; border: 1px solid #888;")

            def _apply_color(color: QColor):
                _update_swatch(color)
                save_configuration_file()

            def _on_click():
                current = getattr(self, color_attr)
                chosen = QColorDialog.getColor(current, zxnextunite_Settings_tab, f"Choose color — {label_text}")
                if chosen.isValid():
                    _apply_color(chosen)

            btn.clicked.connect(_on_click)
            # initialise swatch to the current live color (no save — config not loaded yet)
            _update_swatch(getattr(self, color_attr))
            grid_tab_Settings.addWidget(btn, grid_row, 1)
            return btn

        def _settings_gallery_cols_changed():
            val = self.settings_gallery_cols_combo.currentData() or DEFAULT_GALLERY_COLS
            self._gallery_cols = int(val)
            configuration_dictionary[SETTING_GALLERY_COLS] = str(val)
            save_configuration_file()

        gallery_cols_lbl = QLabel("Gallery items per row:")
        gallery_cols_lbl.setToolTip(
            "Number of thumbnail columns shown in the gallery grid.\n"
            "Default is 4. Choose 2 for larger tiles or 8 for more items per row."
        )
        grid_tab_Settings.addWidget(gallery_cols_lbl, 7, 0)

        self.settings_gallery_cols_combo = QComboBox()
        self.settings_gallery_cols_combo.addItem("2", 2)
        self.settings_gallery_cols_combo.addItem("4 (default)", 4)
        self.settings_gallery_cols_combo.addItem("8", 8)
        self.settings_gallery_cols_combo.setCurrentIndex(1)  # default: 4
        self.settings_gallery_cols_combo.setToolTip(gallery_cols_lbl.toolTip())
        self.settings_gallery_cols_combo.currentIndexChanged.connect(
            lambda _i: _settings_gallery_cols_changed()
        )
        grid_tab_Settings.addWidget(self.settings_gallery_cols_combo, 7, 1)

        def _settings_gallery_img_size_changed():
            val = self.settings_gallery_img_size_combo.currentData() or DEFAULT_GALLERY_IMG_SIZE
            self._gallery_img_size = val
            configuration_dictionary[SETTING_GALLERY_IMG_SIZE] = val
            save_configuration_file()

        gallery_img_size_lbl = QLabel("Gallery image size:")
        gallery_img_size_lbl.setToolTip(
            "Controls the height of gallery thumbnails.\n"
            "  • Small: half the medium height\n"
            "  • Medium (default): standard size\n"
            "  • Large: double the medium height"
        )
        grid_tab_Settings.addWidget(gallery_img_size_lbl, 8, 0)

        self.settings_gallery_img_size_combo = QComboBox()
        self.settings_gallery_img_size_combo.addItem("Small",          "small")
        self.settings_gallery_img_size_combo.addItem("Medium (default)", "medium")
        self.settings_gallery_img_size_combo.addItem("Large",           "large")
        self.settings_gallery_img_size_combo.setCurrentIndex(1)  # default: medium
        self.settings_gallery_img_size_combo.setToolTip(gallery_img_size_lbl.toolTip())
        self.settings_gallery_img_size_combo.currentIndexChanged.connect(
            lambda _i: _settings_gallery_img_size_changed()
        )
        grid_tab_Settings.addWidget(self.settings_gallery_img_size_combo, 8, 1)

        settings_section_lbl = QLabel("SD Card Image Explorer — Item Colors:")
        settings_section_lbl.setToolTip("Customize the foreground color for each item type displayed in the SD card image explorer.")
        grid_tab_Settings.addWidget(settings_section_lbl, 9, 0, 1, 2)

        self.settings_btn_color_up_directory = _make_color_button(
            SETTING_COLOR_UP_DIRECTORY, "img_color_up_directory",
            "  Up Directory item",
            "Color used for the '[Up Directory..]' navigation row in the image explorer.",
            10)
        self.settings_btn_color_dir_name = _make_color_button(
            SETTING_COLOR_DIR_NAME, "img_color_dir_name",
            "  Directory name",
            "Color used for directory name entries in the image explorer.",
            11)
        self.settings_btn_color_dir_type = _make_color_button(
            SETTING_COLOR_DIR_TYPE, "img_color_dir_type",
            "  Directory type label",
            "Color used for the 'DIR' type label column of directory entries.",
            12)
        self.settings_btn_color_file_name = _make_color_button(
            SETTING_COLOR_FILE_NAME, "img_color_file_name",
            "  File name",
            "Color used for file name entries in the image explorer.",
            13)
        self.settings_btn_color_file_ext = _make_color_button(
            SETTING_COLOR_FILE_EXT, "img_color_file_ext",
            "  File extension",
            "Color used for the file extension column in the image explorer.",
            14)
        self.settings_btn_color_file_size = _make_color_button(
            SETTING_COLOR_FILE_SIZE, "img_color_file_size",
            "  File size",
            "Color used for the file size column in the image explorer.",
            15)

        # ---- Background image opacity ----
        bg_opacity_lbl = QLabel("Background image opacity (%):")
        bg_opacity_lbl.setToolTip(
            "Controls how visible the background image is behind the UI.\n"
            "0 = fully hidden, 100 = fully visible. Default is 5%."
        )
        grid_tab_Settings.addWidget(bg_opacity_lbl, 16, 0)

        bg_opacity_row = QWidget()
        bg_opacity_row_layout = QHBoxLayout(bg_opacity_row)
        bg_opacity_row_layout.setContentsMargins(0, 0, 0, 0)
        bg_opacity_row_layout.setSpacing(6)

        self.settings_bg_opacity_slider = QSlider(Qt.Horizontal)
        self.settings_bg_opacity_slider.setRange(0, 100)
        self.settings_bg_opacity_slider.setValue(BackgroundWidget.DEFAULT_OPACITY)
        self.settings_bg_opacity_slider.setTickInterval(10)
        self.settings_bg_opacity_slider.setTickPosition(QSlider.TicksBelow)
        self.settings_bg_opacity_slider.setToolTip(
            "Drag to set the background image opacity (0–100 %)."
        )

        self.settings_bg_opacity_spinbox = QSpinBox()
        self.settings_bg_opacity_spinbox.setRange(0, 100)
        self.settings_bg_opacity_spinbox.setValue(BackgroundWidget.DEFAULT_OPACITY)
        self.settings_bg_opacity_spinbox.setSuffix(" %")
        self.settings_bg_opacity_spinbox.setFixedWidth(60)
        self.settings_bg_opacity_spinbox.setToolTip(
            "Type a value 0–100 to set the background image opacity."
        )

        def _build_tab_stylesheet(alpha: int) -> str:
            """Return a QTabWidget stylesheet whose pane background uses the
            given alpha (0–255), allowing the BackgroundWidget behind it to
            show through at the configured opacity level."""
            return (
                f"QTabWidget::pane {{"
                f"  background: rgba(43,43,43,{alpha});"
                f"  border: 1px solid #555;"
                f"}}"
                f"QTabBar::tab {{"
                f"  background: rgba(43,43,43,200);"
                f"  padding: 4px 10px;"
                f"  border: 1px solid #555;"
                f"  border-bottom: none;"
                f"}}"
                f"QTabBar::tab:selected {{"
                f"  background: rgba(60,60,60,220);"
                f"  font-weight: bold;"
                f"}}"
                f"QTabBar::tab:hover {{"
                f"  background: rgba(70,70,70,220);"
                f"}}"
                f"QStackedWidget {{"
                f"  background: transparent;"
                f"}}"
                f"QScrollArea {{"
                f"  background: transparent;"
                f"  border: none;"
                f"}}"
                f"QScrollArea > QWidget > QWidget {{"
                f"  background: transparent;"
                f"}}"
            )
        self._build_tab_stylesheet = _build_tab_stylesheet
        # Apply default opacity stylesheet immediately (before config loads)
        _default_pane_alpha = max(0, min(255, int(255 - (BackgroundWidget.DEFAULT_OPACITY / 100.0) * 255)))
        self._tab_widget.setStyleSheet(_build_tab_stylesheet(_default_pane_alpha))

        def _apply_bg_opacity(value: int):
            self.settings_bg_opacity_slider.blockSignals(True)
            self.settings_bg_opacity_spinbox.blockSignals(True)
            self.settings_bg_opacity_slider.setValue(value)
            self.settings_bg_opacity_spinbox.setValue(value)
            self.settings_bg_opacity_slider.blockSignals(False)
            self.settings_bg_opacity_spinbox.blockSignals(False)
            self._bg_widget.set_bg_opacity(value)
            # Map 0-100 % opacity to 255-0 pane alpha (more opacity = more
            # background visible = less opaque pane)
            pane_alpha = max(0, min(255, int(255 - (value / 100.0) * 255)))
            self._tab_widget.setStyleSheet(_build_tab_stylesheet(pane_alpha))
            configuration_dictionary[SETTING_BG_OPACITY] = str(value)
            save_configuration_file()

        self.settings_bg_opacity_slider.valueChanged.connect(_apply_bg_opacity)
        self.settings_bg_opacity_spinbox.valueChanged.connect(_apply_bg_opacity)

        bg_opacity_row_layout.addWidget(self.settings_bg_opacity_slider, 1)
        bg_opacity_row_layout.addWidget(self.settings_bg_opacity_spinbox, 0)
        grid_tab_Settings.addWidget(bg_opacity_row, 16, 1)

        # ---- Background image selector ----
        bg_image_lbl = QLabel("Background image:")
        bg_image_lbl.setToolTip(
            "Choose a specific background image or 'Random' to cycle through\n"
            "all images in the script folder every 5 seconds."
        )
        grid_tab_Settings.addWidget(bg_image_lbl, 17, 0)

        bg_image_row = QWidget()
        bg_image_row_layout = QHBoxLayout(bg_image_row)
        bg_image_row_layout.setContentsMargins(0, 0, 0, 0)
        bg_image_row_layout.setSpacing(8)

        self.settings_bg_image_combo = QComboBox()
        self.settings_bg_image_combo.setToolTip(
            "Select 'Random' to cycle through all available backgrounds,\n"
            "or pick a specific image to lock it."
        )
        # Populate: first entry = Random (empty data = random mode)
        self.settings_bg_image_combo.addItem("Random", "")
        _bg_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        _bg_image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        _bg_candidates = sorted(
            f for f in os.listdir(_bg_dir)
            if os.path.splitext(f)[1].lower() in _bg_image_extensions
        ) if os.path.isdir(_bg_dir) else []
        for _bg_fname in _bg_candidates:
            _bg_full = os.path.join(_bg_dir, _bg_fname)
            self.settings_bg_image_combo.addItem(os.path.splitext(_bg_fname)[0], _bg_full)
        # Add bundled Qt resource images (embedded via rc_backgrounds)
        from PySide6.QtCore import QDir as _QDir_bg
        for _rc_name in _QDir_bg(":/").entryList():
            if os.path.splitext(_rc_name)[1].lower() in _bg_image_extensions:
                _rc_path = ":/" + _rc_name
                _rc_label = os.path.splitext(_rc_name)[0] + " (built-in)"
                self.settings_bg_image_combo.addItem(_rc_label, _rc_path)

        bg_image_row_layout.addWidget(self.settings_bg_image_combo, 1)

        # Small QLabel used as a thumbnail preview of the selected image
        self.settings_bg_image_preview = QLabel()
        self.settings_bg_image_preview.setFixedSize(160, 90)
        self.settings_bg_image_preview.setAlignment(Qt.AlignCenter)
        self.settings_bg_image_preview.setStyleSheet(
            "border: 1px solid #666; background: #222;"
        )
        self.settings_bg_image_preview.setToolTip("Preview of the selected background image.")
        bg_image_row_layout.addWidget(self.settings_bg_image_preview, 0)

        grid_tab_Settings.addWidget(bg_image_row, 17, 1)

        def _update_bg_image_preview(path: str):
            """Refresh the thumbnail label for the given absolute image path."""
            if path:
                px = QPixmap(path)
                if not px.isNull():
                    self.settings_bg_image_preview.setPixmap(
                        px.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    return
            self.settings_bg_image_preview.clear()
            self.settings_bg_image_preview.setText("(cycling)")

        def _on_bg_image_combo_changed(index: int):
            path = self.settings_bg_image_combo.itemData(index) or ""
            self._bg_widget.set_bg_image(path)
            _update_bg_image_preview(path)
            configuration_dictionary[SETTING_BG_IMAGE] = path if path.startswith(":/") else (os.path.basename(path) if path else "")
            save_configuration_file()

        self.settings_bg_image_combo.currentIndexChanged.connect(_on_bg_image_combo_changed)

        # Initialise preview to match the current (Random) state — show first
        # available image as a hint, or "(cycling)" if none found.
        if _bg_candidates:
            _hint_path = os.path.join(_bg_dir, _bg_candidates[0])
            _update_bg_image_preview(_hint_path)
        else:
            _update_bg_image_preview("")

        # Also keep the preview in sync when the background cycles (random mode)
        def _on_bg_cycle_update():
            if not self._bg_widget._bg_fixed:
                _p = (self._bg_widget._bg_paths[self._bg_widget._bg_index]
                      if self._bg_widget._bg_paths else "")
                _update_bg_image_preview(_p)

        self._bg_widget._cycle_timer.timeout.connect(_on_bg_cycle_update)

        # ── Crash log toggle (bottom of Settings list) ──────────────────
        def settings_crash_log_enabled_statechanged():
            enabled = self.settings_crash_log_enabled_checkbox.isChecked()
            configuration_dictionary[SETTING_CRASH_LOG_ENABLED] = "true" if enabled else "false"
            save_configuration_file()
            try:
                _zxnu_set_crash_log_enabled(enabled)
            except Exception:
                pass

        self.settings_crash_log_enabled_checkbox = QCheckBox("Enable crash log file generation")
        self.settings_crash_log_enabled_checkbox.setChecked(False)
        self.settings_crash_log_enabled_checkbox.setToolTip(
            "When enabled, unhandled Python exceptions and native crashes are written\n"
            "to 'zx-next-unite-crash.log' next to the executable (or in %TEMP% if that\n"
            "folder is read-only). This is useful for diagnosing issues in the windowed\n"
            "(.exe) build where stderr is not visible. Leave unchecked to suppress the\n"
            "log file entirely (default)."
        )
        self.settings_crash_log_enabled_checkbox.stateChanged.connect(
            settings_crash_log_enabled_statechanged)
        grid_tab_Settings.addWidget(self.settings_crash_log_enabled_checkbox, 18, 0, 1, 2)

        def settings_disable_no_emulator_toast_statechanged():
            configuration_dictionary[SETTING_DISABLE_NO_EMULATOR_TOAST] = "true" if self.settings_disable_no_emulator_toast_checkbox.isChecked() else "false"
            save_configuration_file()

        self.settings_disable_no_emulator_toast_checkbox = QCheckBox("Disable 'No emulators detected' message at startup")
        self.settings_disable_no_emulator_toast_checkbox.setChecked(False)
        self.settings_disable_no_emulator_toast_checkbox.setToolTip(
            "When enabled, the yellow advisory toast shown at startup when\n"
            "neither CSpect nor Mame are found on PATH is suppressed.\n"
            "Check this if you do not use any emulator and do not want the reminder."
        )
        self.settings_disable_no_emulator_toast_checkbox.stateChanged.connect(settings_disable_no_emulator_toast_statechanged)
        grid_tab_Settings.addWidget(self.settings_disable_no_emulator_toast_checkbox, 19, 0, 1, 2)

        # ── MAME options (only shown when the MAME emulator was detected) ──
        if getattr(self, "_mame_executable_path", None):
            def settings_mame_rom_changed():
                configuration_dictionary[SETTING_MAME_ROM_CHOICE] = self.settings_mame_rom_combo.currentText().strip()
                save_configuration_file()

            mame_rom_lbl = QLabel("MAME ROM / system:")
            mame_rom_lbl.setToolTip(
                "The MAME system (ROM set) to launch, e.g. 'tbblue' or 'specnext_ks2'.\n"
                "This is inserted right after the MAME executable and is no longer part\n"
                "of the command-line parameters below."
            )
            grid_tab_Settings.addWidget(mame_rom_lbl, 20, 0)

            self.settings_mame_rom_combo = QComboBox()
            for _rom_name in MAME_ROM_CHOICE:
                self.settings_mame_rom_combo.addItem(_rom_name)
            self.settings_mame_rom_combo.setToolTip(mame_rom_lbl.toolTip())
            self.settings_mame_rom_combo.currentIndexChanged.connect(
                lambda _i: settings_mame_rom_changed())
            grid_tab_Settings.addWidget(self.settings_mame_rom_combo, 20, 1)

            def settings_mame_params_changed():
                configuration_dictionary[SETTING_MAME_COMMAND_LINE_PARAMETERS] = self.settings_mame_params_edit.text()
                save_configuration_file()

            mame_params_lbl = QLabel("MAME launch parameters:")
            mame_params_lbl.setToolTip(
                "Command-line parameters passed to MAME. The '{MAME_EXECUTABLE_NAME}'\n"
                "placeholder resolves to the detected executable. The ROM/system above\n"
                "and the '-hard1 <image>' arguments are added automatically at launch,\n"
                "so the loaded image is always the last argument."
            )
            grid_tab_Settings.addWidget(mame_params_lbl, 21, 0)

            self.settings_mame_params_edit = QLineEdit()
            self.settings_mame_params_edit.setText(
                configuration_dictionary.get(
                    SETTING_MAME_COMMAND_LINE_PARAMETERS, MAME_DEFAULT_COMMAND_LINE))
            self.settings_mame_params_edit.setToolTip(mame_params_lbl.toolTip())
            self.settings_mame_params_edit.editingFinished.connect(settings_mame_params_changed)
            grid_tab_Settings.addWidget(self.settings_mame_params_edit, 21, 1)

        # ── Unite! pygame background animation toggle ──────────────────────
        def _settings_pygame_anim_changed():
            on = self.settings_pygame_anim_checkbox.isChecked()
            self._allinone_pygame_anim = on
            try:
                configuration_dictionary[SETTING_ALLINONE_PYGAME_ANIM] = (
                    "true" if on else "false")
                save_configuration_file()
            except Exception:
                pass
            w = getattr(self, "_allinone_pygame_widget", None)
            if w is not None:
                try:
                    w.enable_background(on)
                except Exception:
                    pass

        self.settings_pygame_anim_checkbox = QCheckBox(
            "Unite! — Space Invaders background animation (pygame mode)")
        self.settings_pygame_anim_checkbox.setChecked(True)
        self.settings_pygame_anim_checkbox.setToolTip(
            "When the Unite! tab is in pygame visualization mode, play an animated\n"
            "Space Invaders scene (twinkling stars, aliens and a ship) behind the\n"
            "Table / Gallery views. On by default. Saved to the configuration file."
        )
        self.settings_pygame_anim_checkbox.stateChanged.connect(
            lambda _s: _settings_pygame_anim_changed())
        grid_tab_Settings.addWidget(self.settings_pygame_anim_checkbox, 22, 0, 1, 2)

        # ── Alien Floyd's: optional pygame-ce animated background everywhere ──
        # A Pink Floyd homage. When on, a pygame-ce "Alien Floyd's" animation
        # (pigs, moons, prisms, guitars, dogs … that morph into one another and
        # bob down soft Bézier curves, a defending ship, glowing stars that turn
        # into $/£/€ signs) replaces the cycling background images on every tab,
        # and floats above the image of every gallery item viewer.
        def _apply_alien_floyd_bg(on):
            try:
                import zxnu_pygame as _zpg
                _zpg.set_alien_floyd_enabled(on)
            except Exception:
                pass
            bg = getattr(self, "_bg_widget", None)
            if bg is not None:
                try:
                    bg.set_alien_mode(on)
                except Exception:
                    pass
        self._apply_alien_floyd_bg = _apply_alien_floyd_bg

        def _settings_alien_bg_changed():
            on = self.settings_alien_floyd_bg_checkbox.isChecked()
            configuration_dictionary[SETTING_ALIEN_FLOYD_BG] = "true" if on else "false"
            if not self._initialising:
                save_configuration_file()
            _apply_alien_floyd_bg(on)

        self.settings_alien_floyd_bg_checkbox = QCheckBox(
            "Alien Floyd's — animated background on all tabs (pygame-ce)")
        self.settings_alien_floyd_bg_checkbox.setChecked(False)
        self.settings_alien_floyd_bg_checkbox.setToolTip(
            "Pink Floyd homage. Replaces the cycling background images on every\n"
            "tab with an animated 'Alien Floyd's' scene (morphing pigs, moons,\n"
            "prisms, guitars, dogs …, a defending ship and glowing stars that\n"
            "flicker into $/£/€ signs), and floats it above every gallery item\n"
            "viewer image. Optional. Off by default. Saved to the configuration\n"
            "file. Requires the optional 'pygame-ce' package.")
        self.settings_alien_floyd_bg_checkbox.stateChanged.connect(
            lambda _s: _settings_alien_bg_changed())
        grid_tab_Settings.addWidget(self.settings_alien_floyd_bg_checkbox, 23, 0, 1, 2)

        # ── Alien Floyd's: optional dedicated full-window tab ────────────────
        self._alien_floyd_tab_widget = None

        def _alien_floyd_tab_set_visible(on):
            tabw = wid_inner.tab
            if on:
                if self._alien_floyd_tab_widget is not None and \
                        tabw.indexOf(self._alien_floyd_tab_widget) != -1:
                    return
                try:
                    from zxnu_pygame import AlienFloydWidget, pygame_available
                    ok, _why = pygame_available()
                    if not ok:
                        return
                except Exception:
                    return
                page = QWidget()
                page_layout = QVBoxLayout(page)
                page_layout.setContentsMargins(0, 0, 0, 0)
                anim = AlienFloydWidget(page)
                page_layout.addWidget(anim)
                page.tab_name_private = "AlienFloyds"
                page._alien_anim = anim
                self._alien_floyd_tab_widget = page
                # Insert just before the Settings tab.
                idx = tabw.count()
                for _i in range(tabw.count()):
                    if tabw.tabText(_i).startswith("Settings"):
                        idx = _i
                        break
                tabw.insertTab(idx, page, "🌈 Alien Floyd's")
            else:
                page = self._alien_floyd_tab_widget
                if page is not None:
                    _i = tabw.indexOf(page)
                    if _i != -1:
                        tabw.removeTab(_i)
                    try:
                        anim = getattr(page, "_alien_anim", None)
                        if anim is not None:
                            anim.teardown()
                        page.deleteLater()
                    except Exception:
                        pass
                    self._alien_floyd_tab_widget = None
        self._alien_floyd_tab_set_visible = _alien_floyd_tab_set_visible

        def _settings_alien_tab_changed():
            on = self.settings_alien_floyd_tab_checkbox.isChecked()
            configuration_dictionary[SETTING_ALIEN_FLOYD_TAB] = "true" if on else "false"
            if not self._initialising:
                save_configuration_file()
            _alien_floyd_tab_set_visible(on)

        self.settings_alien_floyd_tab_checkbox = QCheckBox(
            "Alien Floyd's — show the full-window 'Alien Floyd's' tab (pygame-ce)")
        self.settings_alien_floyd_tab_checkbox.setChecked(False)
        self.settings_alien_floyd_tab_checkbox.setToolTip(
            "Add a dedicated 'Alien Floyd's' tab (before Settings) that shows the\n"
            "full-window pygame-ce animation. Off by default. Saved to the\n"
            "configuration file. Requires the optional 'pygame-ce' package.")
        self.settings_alien_floyd_tab_checkbox.stateChanged.connect(
            lambda _s: _settings_alien_tab_changed())
        grid_tab_Settings.addWidget(self.settings_alien_floyd_tab_checkbox, 24, 0, 1, 2)

        grid_tab_Settings.setColumnStretch(2, 1)
        zxnextunite_Settings_tab.setLayout(grid_tab_Settings)
        zxnextunite_Settings_tab.tab_name_private = "Settings"
        wid_inner.tab.addTab(zxnextunite_Settings_tab, "Settings 🔩")

          # Create Help Tab
        zxnextunite_Help_tab = QWidget(wid_inner.tab)
        zxnextunite_Help_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_Help_tab.setAutoFillBackground(False)
        grid_tab_Help = QGridLayout(zxnextunite_Help_tab)
        grid_tab_Help.addWidget(self.listWidgetHelp) # TODO as above use the form container of Help use the form container
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

            from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QCheckBox, QPushButton, QLabel, QSizePolicy

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
            """Attach or detach completers on all three search inputs."""
            for input_widget, completer in (
                (self.getit_search_input, getattr(self, "_getit_completer", None)),
                (self.zxdb_search_input,  getattr(self, "_zxdb_completer",  None)),
                (self.zxart_search_input, getattr(self, "_zxart_completer", None)),
                (getattr(self, "allinone_search_input", None),
                 getattr(self, "_allinone_completer", None)),
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
            if tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GOOEY):
                if right_disk_image_explorer_content:
                    hdfmonkeyexecresult = execute_hdf_monkey("ls", self.right_disk_image_path)
                    if hdfmonkeyexecresult.returncode == 0:
                        update_disk_manager_widget_table(hdfmonkeyexecresult.stdout)
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


        #  Start main logic

        load_configuration_file()
        self._initialising = False

        # Re-apply view modes now that config has been loaded (the per-pane setup
        # runs before load_configuration_file, so the combos/stacks need updating).
        self._getit_apply_view_mode(self._getit_view_mode, persist=False)
        self._zxdb_apply_view_mode(self._zxdb_view_mode,   persist=False)
        self._zxart_apply_view_mode(self._zxart_view_mode, persist=False)
        self._favorites_apply_view_mode(self._favorites_view_mode, persist=False)
        self._allinone_apply_view_mode(self._allinone_view_mode, persist=False)

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
            if current_title == ZX_NEXT_UNITE_TAB_TITLE_GETIT:
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

        # Use a small delay (not 0) so the first paint/show events have a
        # chance to be processed before any thumbnail fetch threads spin up.
        QTimer.singleShot(150, _deferred_startup_tab_activation)
        # Report which emulators were detected at startup via a 5-second toast
        # (green when found, yellow advisory when none are available). Deferred
        # so it appears after the window is shown.
        QTimer.singleShot(400, self._show_emulator_detection_toast)
        # Expose the nested save function so closeEvent (a class method) can call it.
        self._save_configuration_file = save_configuration_file

        if is_hdfmonkey_present():
            if load_image():
                if self.settings_warn_image_nearly_full_checkbox.isChecked():
                    _warn_if_image_nearly_full(self.right_disk_image_path)
        else:
            if platform.system() == "Windows":
                show_hdf_monkey_download_and_install_buttons()
                if is_hdfmonkey_present():
                    if load_image():
                        if self.settings_warn_image_nearly_full_checkbox.isChecked():
                            _warn_if_image_nearly_full(self.right_disk_image_path)

        if not right_disk_image_explorer_content:
            self.diskimageexplorerlabelpath.setText("Please load an image.")
        else:
            self.diskimageexplorerlabelpath.setText(generate_disk_file_path().replace('//', '/'))

        nextsync_show_ip_info()
        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()

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
    """Keep visible autocomplete popups anchored to their input when the
    main window is dragged across the screen."""
    super(MainWindow, self).moveEvent(event)
    _mainwindow_reposition_ac_popups(self)

MainWindow.moveEvent = _mainwindow_move_event


# resizeEvent is defined here (outside __init__) so it is a real class method
def _mainwindow_resize_event(self, event):
    """Re-anchor visible autocomplete popups when the window is resized, as
    that also shifts the search input's screen position."""
    super(MainWindow, self).resizeEvent(event)
    _mainwindow_reposition_ac_popups(self)

MainWindow.resizeEvent = _mainwindow_resize_event

import signal

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
# "libpng warning: hIST: out of place" is a libpng warning that occurs when
# loading PNG images with an out-of-order hIST chunk.  It does not affect
# functionality, so we ignore it.
_QT_SUPPRESS_MSGS = ("Point size <= 0", "libpng warning: hIST: out of place")
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

# Allow Ctrl-C (SIGINT) to terminate the application cleanly.
# Qt's event loop blocks Python signal delivery unless we periodically
# yield back to the Python interpreter via a no-op timer.
def _handle_sigint(*_args):
    print("\nInterrupted — exiting.", flush=True)
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

