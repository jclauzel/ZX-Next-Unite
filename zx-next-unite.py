#!/usr/bin/env python3

"""
    zx-next-unite by Julien Clauzel based on:

        HDFM-GOOEY by em00k
    &
        NextSync by Jari Komppa

    * Requirements:
        - Python 3.7+
        - pyside6
        - CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
            feel free to support his development efforts & patreon https://www.patreon.com/mikedailly
            - Make sure Spectrum Next roms installed are installed in local directory (they should be provided in the CSpect zip package by default).
                These two files namely: enNextZX.rom and enNxtMMC.rom -MUST- be placed in the root folder of your #CSpect.
        - You will need Spectrum Next images files that you can download from https://zxspectrumnext.online/cspect/  such as http://www.zxspectrumnext.online/cspect/cspect-next-2gb.zip
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

"""

# Standard library imports
import ctypes
import datetime
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
import traceback
import urllib.parse
import urllib.request
import zipfile

# Third-party imports
from PySide6 import QtCore
from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QObject,
    QRect,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QGridLayout,
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

ZX_NEXT_UNITE_VERSION = "4.4"
ZX_NEXT_UNITE_ICON_IMAGE_FILE = "zx-next-unite.png"
ZX_NEXT_UNITE_VERBOSE_LOG_MODE = False
ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER = 1
ZX_NEXT_UNITE_UI_WIDTH = 900 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER
ZX_NEXT_UNITE_UI_HEIGTH = 650 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER
ZX_NEXT_UNITE_CONFIG_FILE_NAME = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "hdfg.cfg")
ZX_NEXT_UNITE_TAB_TITLE_GOOEY = "zx-next-unite - SD Card Utility"
ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC = "NextSync - Network Transfer Manager"
ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC_SYNCON = "NextSync - Sync ON"
ZX_NEXT_UNITE_TAB_TITLE_GETIT = "GetIt"
ZX_NEXT_UNITE_TAB_TITLE_ZXDB  = "ZXDB"

GETIT_BASE_URL = "https://zxnext.uk"
GETIT_PAGE_SIZE = 18

ZXDB_BASE_URL = "https://api.zxinfo.dk/v3"
ZXDB_USER_AGENT = "ZX-Next-Unite"
ZXDB_PAGE_SIZE = 20


HDF_MONKEY_WINDOWS_URL = "https://uto.speccy.org/downloads/hdfmonkey_windows.zip"

SETTING_HDDFILE = "hddffile"
SETTING_EXPLORERPATH = "explorerpath"
SETTING_SCREENSIZE = "screensize"
SETTING_SOUND = "sound"
SETTING_VSYNC = "vsync"
SETTING_HERTZ = "hertz"
SETTING_JOYSTICK = "joy"
SETTING_CSPECT = "cspect"
SETTING_CUSTOM = "custom"
SETTING_ESC = "esc"
SETTING_NEXTSYNC_EXPLORERPATH = "nextsync_explorerpath"
SETTING_NEXTSYNC_SYNCONCE = "nextsync_synconce"
SETTING_NEXTSYNC_ALWAYSSYNC = "nextsync_alwayssync"
SETTING_NEXTSYNC_SLOWTRANSFER = "nextsync_slowtransfer"
SETTING_DEFAULT_TAB_WHEN_OPENING = "default_tab"
SETTING_WARN_IMAGE_NEARLY_FULL = "warn_image_nearly_full"
SETTING_NO_PROMPT_ON_DELETION  = "no_prompt_on_deletion"
SETTING_ZXDB_AVAIL_CHECK       = "zxdb_avail_check"
SETTING_ZXDB_LAST_MODE         = "zxdb_last_mode"
SETTING_ZXDB_LAST_QUERY        = "zxdb_last_query"
SETTING_COLOR_UP_DIRECTORY = "color_up_directory"
SETTING_COLOR_DIR_NAME    = "color_dir_name"
SETTING_COLOR_DIR_TYPE    = "color_dir_type"
SETTING_COLOR_FILE_NAME   = "color_file_name"
SETTING_COLOR_FILE_EXT    = "color_file_ext"
SETTING_COLOR_FILE_SIZE   = "color_file_size"
SETTING_IMAGE_HISTORY     = "image_history"
MAX_IMAGE_HISTORY         = 10

DEFAULT_COLOR_UP_DIRECTORY = "#ff0000"
DEFAULT_COLOR_DIR_NAME    = "#0000ff"
DEFAULT_COLOR_DIR_TYPE    = "#0000ff"
DEFAULT_COLOR_FILE_NAME   = "#00ff00"
DEFAULT_COLOR_FILE_EXT    = "#00ff00"
DEFAULT_COLOR_FILE_SIZE   = "#00ff00"

PORT = 2048    # Port to listen on (non-privileged ports are > 1023)
VERSION3 = "NextSync3"
VERSION = "NextSync4"
IGNOREFILE = "syncignore.txt"
SYNCPOINT = "syncpoint.dat"
MAX_PAYLOAD = 1024
NEXTSYNC_UI_HEIGTH_MULTIPLIER = 1
NEXTSYNC_UI_HEIGTH = 300 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER
IGNOREFILE_DEFAULT_CONTENT = (("syncignore.txt"), ("syncpoint.dat"), ("zx-next-unite.png"),("*.bak"), ("*.py"), ("*.pyproj"), ("*.pyproj"), ("hdfmonkey.exe"), ("hdfg.cfg"))

INIT_LOG = (("NextSync - by Jari Komppa"), ("HDF Monkey - by Matt Westcott"), ("CSpect - by Mike Dailly http://cspect.org"), ("Inspired by HDFM-GOOEY - by em00k"), ("zx-next-unite - by Julien Clauzel 2024"))
INIT_HELP = ((f"Welcome to zx-next-unite {ZX_NEXT_UNITE_VERSION} help"),
             (""),
             ("Introduction:"),
             ("--------"),
             ("zx-next-unite was initialy created by emOOk and NextSync by Jari Komppa."),
             ("A while back I rambled with the idea of an all in one bootstrapper transfer tool to"),
             ("avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it."),
             ("Last but not the least some source code was lost from HDFM Gooey and the tool was stuck back in that time,"),
             ("with the agreement of emOOk I started a rewrite in Python and later with Jari"),
             ("I started a rewrite in Python that would also provide MacOS and Linux portability."),
             ("Here we are now you have it!"),
             (""),
             (""),
             ("Third party license"),
             ("-------------------"),
             ("zx-next-unite is a Qt Application using pyside6 in Python on top of Qt6, which retains the GPLv2 Licensing."),
             ("Please refer to the LICENSE file on github: https://github.com/jclauzel/zx-next-unite/blob/master/LICENSE.txt."),
             (""),
             ("Pyside6 is not bundled and needs to be installed separately (see installation instructions)."),
             (""),
             ("Setup & How to:"),
             ("---------------"),
             ("Checkout main setup & demo video avaible at: https://youtu.be/FJG-Z0DCIjQ"),
             ("NextSync Head Over Heels demo: https://www.youtube.com/watch?v=D3_WqTPvjOE"),
             ("NextSync Night Knight demo: https://www.youtube.com/watch?v=eN1eMIqMCm4"),
             (""),
             ("hdfmonkey:"),
             ("----------"),
             ("Is a required external component developped by Matt Westcott  that allows to browse the image."),
             ("You will need to install it to get this application up and fully running."),
             (""),
             ("If you are running the app on Windows and hdfmonkey in not present in the same directory,"),
             ("you will see an error message in the main log Windows as it is missing."),
             (""),
             ("If that is the case you will see a 'Download and Install button' bottom right,"),
             ("once clicked it will try to fetch https://uto.speccy.org/downloads/hdfmonkey_windows.zip "),
             ("and unzip hdfmonkey executable in the same directory."),
             ("If the above automated install is successful, you should then be able to select an image and navigate it."),
             (""),
             ("On Mac/Linux you will need to install hdfmonkey manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey"),
             (""),
             ("NextSync:"),
             ("---------"),
             ("zx-next-unite implements the <Server> side code and protocol of NextSync by Jari Komppa."),
             ("It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py."),
             (""),
             ("Initial realease on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8"),
             ("As a result you will need to run the dot same .sync command on your Next as with the console version and the same network protocol."),
             (""),
             ("The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 ."),
             (""),
             ("You may follow the same instructions as the provided in the readme.txt of that release."),
             ("On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your next dot folder."),
             ("Navigate to NextSync tab, select the root folder to sync on the left."),
             ("Once you have selected the folder hit the 'prepare sync' button, check the Next Sync log Window on the right."),
             ("First time you will run .sync on your will be prompter to select the <server> IP address, this machine running NextSync."),
             ("From the log window pick the IP address from this machine you want to use and type it on your next."),
             ("Then start the sync server on this maching using the Yes, start sync button and then run the .sync command on your Next."),
             ("At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your next."),
             ("As it is your Next that will connect to this machine check your firewall alows inbound calls to this machine on port: 2048 by default." ),
             (""),
             ("The same syncignore.txt and syncpoint.dat file logic applies and alows you to control the sync (please check Jari documentation)."),
             (""),
             ("NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync"),
             (""),
             ("If you run in any type of issue using the NextSync integration please run first the Jari command line version to see if it works as expected."),
             (""),
             ("OpenAL sound engine (on Windows)"),
             ("--------------------------------"),
             ("OpenAL library is required on Windows for CSpect to play sound, you may download it here: https://openal.org/"),
             (""),
             ("Mono (on Linux & MacOS Only)"),
             ("-------"),
             ("You will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete"),
             (""),
             ("Enjoy!"),
             ("")
            )

CONFIG_FILE_SETTINGS = (SETTING_HDDFILE, SETTING_EXPLORERPATH, SETTING_SCREENSIZE, SETTING_SOUND, SETTING_VSYNC, SETTING_HERTZ, SETTING_JOYSTICK, SETTING_CSPECT, SETTING_CUSTOM, SETTING_ESC, SETTING_NEXTSYNC_EXPLORERPATH, SETTING_NEXTSYNC_SYNCONCE, SETTING_NEXTSYNC_ALWAYSSYNC, SETTING_NEXTSYNC_SLOWTRANSFER, SETTING_DEFAULT_TAB_WHEN_OPENING, SETTING_WARN_IMAGE_NEARLY_FULL, SETTING_NO_PROMPT_ON_DELETION, SETTING_COLOR_UP_DIRECTORY, SETTING_COLOR_DIR_NAME, SETTING_COLOR_DIR_TYPE, SETTING_COLOR_FILE_NAME, SETTING_COLOR_FILE_EXT, SETTING_COLOR_FILE_SIZE, SETTING_IMAGE_HISTORY, SETTING_ZXDB_LAST_MODE, SETTING_ZXDB_LAST_QUERY)
IMAGE_BUTTONS_SIZE = 190
DISK_ARROWS_BUTTONS_SIZE = 30

CSPECT_SCREEN_SIZES = (("Screen Size X1", "-w1"),("Screen Size X2", "-w2"),("Screen Size X3", "-w3"), ("Screen Size X4", "-w4"), ("Fullscreen", "-fullscreen"))
CSPECT_SOUND = (("Sound On", ""),("Sound Off", "-sound"))
CSPECT_SCREEN_SYNC = (("VSync On", "-vsync"),("VSync Off", ""))
CSPECT_JOYSTICK = (("Joystick On", "-vsync"),("Joystick Off", ""))
CSPECT_FREQUENCY = (("50Hz", ""),("60Hz", "-60"))
CSPECT_BASE_ARGUMENTS = "-basickeys -zxnext -nextrom"

FONT_GREEN = QColor(0, 255, 0)
FONT_BLUE = QColor(0, 0, 255)
FONT_RED = QColor(255, 0, 0)

def qcolor_to_hex(color: QColor) -> str:
    """Return a lowercase #rrggbb hex string for the given QColor."""
    return color.name().lower()

def hex_to_qcolor(hex_str: str) -> QColor:
    """Return a QColor from a #rrggbb hex string, falling back to white on error."""
    color = QColor(hex_str)
    return color if color.isValid() else QColor(255, 255, 255)

UP_DIRECTORY = "[Up Directory..]"
DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS = ('"', '<', '>', ':', '\\', '/', '|', '?', '*', '!', '(',')', '.', "'", '$', '@')
HDFMONKEY_EXECUTABLE = "hdfmonkey"
FILTER_LABEL_TEXT = "Filter: "
FILTER_TEXT_WIDTH = 320


assert sys.version_info >= (3, 6) # We need 3.6 for f"" strings.

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DotDotFirstProxyModel(QSortFilterProxyModel):
    """Proxy model that always keeps the '..' parent directory entry at the top."""
    def lessThan(self, left, right):
        left_name = self.sourceModel().fileName(left)
        right_name = self.sourceModel().fileName(right)
        if left_name == "..":
            return True
        if right_name == "..":
            return False
        return super().lessThan(left, right)

    def filterAcceptsRow(self, source_row, source_parent):
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)
        # Always show the parent-directory entry
        if source_model.fileName(index) == "..":
            return True
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True
        name = source_model.fileName(index)
        return pattern.lower() in name.lower()

class WorkerSignals(QObject):

    finished = Signal()
    error = Signal(tuple)
    result = Signal(object)
    progress = Signal(int)


class NextSyncSignals(QObject):
    """Signals used to marshal nextsync progress back to the main thread."""
    progress = Signal(int)   # 0-100 per-file progress
    status   = Signal(str)   # single-line status message
    finished = Signal()      # emitted when the job thread exits
    cancelled = Signal()     # emitted when job stopped due to cancel request


class HdfTaskSignals(QObject):
    """Signals for background hdfmonkey task workers."""
    progress  = Signal(int)   # 0-100
    status    = Signal(str)   # "action line\nfilename line"
    finished  = Signal()
    error     = Signal(str)   # human-readable error message
    cancelled = Signal()      # emitted when the worker stopped early due to cancel


class HdfTaskWorker(QRunnable):
    """Generic QRunnable that runs a callable on the thread pool.
    The callable receives (signals, cancel_event, *args, **kwargs).
    Call worker.cancel() from the UI thread to request early termination."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn           = fn
        self.args         = args
        self.kwargs       = kwargs
        self.signals      = HdfTaskSignals()
        self.cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            self.fn(self.signals, self.cancel_event, *self.args, **self.kwargs)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            if self.cancel_event.is_set():
                self.signals.cancelled.emit()
            self.signals.finished.emit()


class HdfProgressDialog(QDialog):
    """Modal progress dialog with live status, progress bar, spinner, and Cancel button."""

    cancel_requested = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Spinner + action label on one row
        action_row = QHBoxLayout()
        self._spinner_label = QLabel("")
        self._spinner_label.setFixedWidth(22)
        action_row.addWidget(self._spinner_label)
        self._action_label = QLabel("Starting\u2026")
        self._action_label.setWordWrap(True)
        action_row.addWidget(self._action_label, 1)
        layout.addLayout(action_row)

        # Current filename (smaller, muted)
        self._file_label = QLabel("")
        self._file_label.setWordWrap(True)
        _font = self._file_label.font()
        _font.setPointSize(max(_font.pointSize() - 1, 8))
        self._file_label.setFont(_font)
        layout.addWidget(self._file_label)

        # Progress bar
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        layout.addWidget(self._bar)

        # Cancel button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedWidth(90)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        self._cancelled = False
        self._spinner_frames = ["\u25f4", "\u25f7", "\u25f6", "\u25f5"]
        self._spinner_idx    = 0

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(120)
        self._anim_timer.timeout.connect(self._tick_spinner)
        self._anim_timer.start()

    # ------------------------------------------------------------------
    @Slot()
    def _on_cancel_clicked(self):
        self._cancelled = True
        self._cancel_btn.setEnabled(False)
        self._action_label.setText("Cancelling\u2026")
        self._file_label.setText("")
        self.cancel_requested.emit()

    @Slot()
    def _tick_spinner(self):
        self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_frames)
        self._spinner_label.setText(self._spinner_frames[self._spinner_idx])

    @Slot(int)
    def set_progress(self, value: int):
        """value == -1 activates the indeterminate (busy) marquee animation."""
        if value < 0:
            self._bar.setRange(0, 0)   # Qt marquee mode
        else:
            if self._bar.maximum() == 0:
                self._bar.setRange(0, 100)
            self._bar.setValue(value)

    @Slot(str)
    def set_status(self, text: str):
        """Expects 'Action description\nFilename or detail'."""
        if self._cancelled:
            return
        lines = text.split("\n", 1)
        self._action_label.setText(lines[0])
        self._file_label.setText(lines[1] if len(lines) > 1 else "")

    @Slot()
    def mark_cancelled(self):
        """Called when the worker confirms it stopped early."""
        self._action_label.setText("Cancelled.")
        self._file_label.setText("")

    def closeEvent(self, event):
        self._anim_timer.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# GetIt API helpers
# ---------------------------------------------------------------------------

def getit_fetch(path: str, timeout: int = 10) -> str:
    """Fetch a plain-text response from the GetIt server and return the body string."""
    url = GETIT_BASE_URL + path
    req = urllib.request.Request(url, headers={"User-Agent": "zx-next-unite-getit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


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

def zxdb_fetch_json(path: str, timeout: int = 15):
    """GET JSON from the ZXInfo API. *path* must include any query string.
    Identifies the client per API policy via a custom User-Agent."""
    url = ZXDB_BASE_URL + path
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


def zxdb_fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch raw bytes (e.g. a screenshot or game file) using ZXDB UA."""
    req = urllib.request.Request(url, headers={"User-Agent": ZXDB_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    seen_urls = set()

    def _abs_url(u):
        if not u:
            return ""
        if u.startswith("/"):
            return "https://spectrumcomputing.co.uk" + u
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
            seen_urls.add(url)
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
# GetIt QRunnable workers (must be module-level for stable C++ type identity)
# ---------------------------------------------------------------------------

def getit_run_in_thread(fn, on_result, on_error):
    """Run *fn* in a daemon thread. Results are marshalled to the main thread
    via Qt queued signal connections, which are thread-safe."""
    signals = WorkerSignals()
    signals.result.connect(on_result)
    signals.error.connect(on_error)

    def _run():
        try:
            result = fn()
            signals.result.emit(result)
        except Exception as exc:
            signals.error.emit((type(exc), exc, ""))

    t = threading.Thread(target=_run, daemon=True)
    t._getit_signals = signals  # keep signals alive until thread finishes
    t.start()
    return t



class MainWindow(QMainWindow):

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
        right_disk_image_selected_files = []
        configuration_dictionary = {}

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
                    self.signals.error.emit((exctype, value, traceback.format_exc()))
                else:
                    self.signals.result.emit(result)  # Return the result of the processing
                finally:
                    self.signals.finished.emit()  # Done

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
                logging.error(f"Failed downloading & installing hdfmonkey: {e}")
                add_main_log_window(f"Failed downloading & installing hdfmonkey: {e}")
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
                    configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING] = 0

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

                if SETTING_ZXDB_AVAIL_CHECK in configuration_dictionary and configuration_dictionary[SETTING_ZXDB_AVAIL_CHECK] != "":
                    checked = configuration_dictionary[SETTING_ZXDB_AVAIL_CHECK] != "0" and configuration_dictionary[SETTING_ZXDB_AVAIL_CHECK].lower() != "false"
                    self.settings_zxdb_avail_check_checkbox.setChecked(checked)

                saved_mode = configuration_dictionary.get(SETTING_ZXDB_LAST_MODE, "").strip()
                if saved_mode:
                    for _i in range(self.zxdb_mode_combo.count()):
                        if self.zxdb_mode_combo.itemData(_i) == saved_mode:
                            self.zxdb_mode_combo.setCurrentIndex(_i)
                            break

                saved_query = configuration_dictionary.get(SETTING_ZXDB_LAST_QUERY, "")
                if saved_query:
                    self.zxdb_search_input.setText(saved_query)

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
            except subprocess.CalledProcessError as ex:
                    stderr_text = (ex.stderr or b"").decode(errors="replace").strip()
                    exec_process = subprocess.CompletedProcess(args=ex.cmd, returncode=ex.returncode,
                                                               stdout=ex.stdout, stderr=ex.stderr)
                    if silent:
                        logging.debug(f"hdfmonkey {command_to_execute} returned {ex.returncode} (silent): {execution_cmd}"
                                      + (f" | stderr: {stderr_text}" if stderr_text else ""))
                    elif ex.returncode == 1:
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


        def nextsync_start_server():
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

                def _run():
                    try:
                        nextsync_do_server_job(
                            progress_callback=sig.progress,
                            status_callback=sig.status,
                            cancel_flag=cancel_flag,
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
                    ignored = False
                    for i in ignorelist:
                        if fnmatch.fnmatch(g, i):
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

        def nextsync_do_server_job(progress_callback, status_callback=None, cancel_flag=None):
            """Run the NextSync server loop.

            progress_callback – Signal(int) or None; emitted with 0-100 per-file progress.
            status_callback   – Signal(str) or None; emitted with a human-readable status line.
            cancel_flag       – threading.Event or None; checked between socket accept retries.
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

            if self.left_file_nextsync_explorer_selection_full_filename_path:
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
                                if fn >= len(f):
                                    add_nextsync_log_window (f"{timestamp()} | Nothing (more) to sync")
                                    packet = b'\x00\x00\x00\x00\x00' # end of.
                                    packets += 1
                                    sendpacket(conn, packet, 0)
                                    totalbytes += len(packet)
                                    # Sync complete, set sync point
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

        self.horizontal1.addWidget(self.imageinput)
        self.horizontal1.addWidget(self.selectimage)

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

        self.button_start_cspect = QPushButton("LaunchCSpect", self)
        self.button_start_cspect.setText("Launch CSpect")
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

        self.zx_next_unite_form.addRow(self.horizontal6)

        set_all_buttons_disabled()
        enable_image_selection()


        wid = QWidget()
        grid = QGridLayout(wid)
        wid.setLayout(grid)

        # setting the inner widget and layout
        grid_inner = QGridLayout()
        wid_inner = QWidget(wid)
        wid_inner.setLayout(grid_inner)

        # add the inner widget to the outer layout
        grid.addWidget(wid_inner)

        # add tab frame to widget
        wid_inner.tab = QTabWidget(wid_inner)
        grid_inner.addWidget(wid_inner.tab)

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

        self.getit_search_button = QPushButton("Search")
        getit_search_row.addWidget(self.getit_search_button)

        self.getit_latest_button = QPushButton("Latest")
        getit_search_row.addWidget(self.getit_latest_button)

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

        self.getit_status_label = QLabel("")
        getit_search_row.addWidget(self.getit_status_label, 1)

        getit_search_widget = QWidget()
        getit_search_widget.setLayout(getit_search_row)
        self.getit_form.addRow(getit_search_widget)

        # --- Results table ---
        self.getit_results_table = QTableWidget(0, 5)
        self.getit_results_table.setHorizontalHeaderLabels(["ID", "Title", "Author", "Size", "Category"])
        self.getit_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.getit_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.getit_results_table.horizontalHeader().setStretchLastSection(True)
        self.getit_results_table.setMinimumHeight(200)
        self.getit_results_table.setColumnWidth(0, 70)
        self.getit_results_table.setColumnWidth(1, 300)
        self.getit_results_table.setColumnWidth(2, 130)
        self.getit_results_table.setColumnWidth(3, 70)

        self.getit_screenshot_label = QLabel()
        self.getit_screenshot_label.setFixedSize(256, 192)
        self.getit_screenshot_label.setAlignment(Qt.AlignCenter)
        self.getit_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
        self.getit_screenshot_label.setText("No preview")
        self.getit_screenshot_label.setToolTip("Double-click to enlarge")

        self.getit_download_button = QPushButton("Download File")
        self.getit_download_button.setEnabled(False)

        getit_right_col = QVBoxLayout()
        getit_right_col.addWidget(self.getit_screenshot_label)
        getit_right_col.addWidget(self.getit_download_button)
        getit_right_col.addStretch()
        getit_right_widget = QWidget()
        getit_right_widget.setLayout(getit_right_col)

        getit_table_row = QHBoxLayout()
        getit_table_row.addWidget(self.getit_results_table, 1)
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
        getit_motd_label = QLabel("MOTD:")
        getit_motd_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        self.getit_form.addRow(getit_motd_label)

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
                self.getit_results_table.setItem(row, 4, QTableWidgetItem(e["category"]))

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
            self.getit_download_button.setEnabled(bool(self._getit_selected_id))

        # ---- Background search task ----

        def getit_run_search(query: str, page: int):
            if self._getit_search_loading:
                return
            self._getit_last_query = query
            self._getit_search_loading = True
            getit_set_status("Searching…")
            self.getit_search_button.setEnabled(False)
            self.getit_latest_button.setEnabled(False)

            def _search_fn():
                offset = (page - 1) * GETIT_PAGE_SIZE
                if query:
                    path = f"/f?s={urllib.parse.quote(query)}"
                    if offset > 0:
                        path += f"&o={offset}"
                else:
                    path = "/f"
                    if offset > 0:
                        path += f"?o={offset}"
                text = getit_fetch(path)
                entries, total, pg, total_pages = getit_parse_file_list(text)
                return (entries, total, total_pages)

            def _on_result(data):
                self._getit_search_loading = False
                total_pages = data[2] or 1
                self._getit_total_pages = total_pages
                getit_populate_results(data[0], page, total_pages)
                getit_set_status(f"{data[1]} result(s)  |  page {page}/{total_pages}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)

            def _on_error(err):
                self._getit_search_loading = False
                getit_set_status(f"Error: {err[1]}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)

            self._getit_search_thread = getit_run_in_thread(_search_fn, _on_result, _on_error)

        def _show_page(page: int):
            """Navigate to a page by re-running the search with the new page number."""
            getit_run_search(self._getit_last_query, page)

        def getit_on_search():
            getit_clear_detail()
            getit_run_search(self.getit_search_input.text().strip(), 1)

        def getit_on_latest():
            getit_clear_detail()
            self.getit_search_input.clear()
            getit_run_search("", 1)

        def getit_on_prev():
            getit_run_search(self._getit_last_query, max(1, self._getit_current_page - 1))

        def getit_on_next():
            getit_run_search(self._getit_last_query, min(self._getit_total_pages, self._getit_current_page + 1))

        self.getit_search_button.clicked.connect(getit_on_search)
        self.getit_latest_button.clicked.connect(getit_on_latest)
        self.getit_search_input.returnPressed.connect(getit_on_search)
        self.getit_prev_button.clicked.connect(getit_on_prev)
        self.getit_next_button.clicked.connect(getit_on_next)

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

        # ---- Download file ----

        def getit_do_download(eid, default_name):
            save_path, _ = QFileDialog.getSaveFileName(
                None, "Save file", default_name
            )
            if not save_path:
                return
            getit_set_status(f"Downloading {eid}…")
            self.getit_download_button.setEnabled(False)

            def _dl_fn():
                url = f"{GETIT_BASE_URL}/nx/{eid}/"
                urllib.request.urlretrieve(url, save_path)
                return save_path

            def _on_dl_done(p):
                getit_set_status(f"Saved to {p}")
                self.getit_download_button.setEnabled(True)

            def _on_dl_error(err):
                getit_set_status(f"Download error: {err[1]}")
                self.getit_download_button.setEnabled(True)

            self._getit_dl_thread = getit_run_in_thread(_dl_fn, _on_dl_done, _on_dl_error)

        def getit_on_download():
            if not self._getit_selected_id:
                return
            getit_do_download(
                self._getit_selected_id,
                self._getit_selected_link or f"{self._getit_selected_id}.zip"
            )

        self.getit_download_button.clicked.connect(getit_on_download)

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
                tmp = tempfile.NamedTemporaryFile(suffix="_" + fname, delete=False)
                tmp.close()
                try:
                    urllib.request.urlretrieve(url, tmp.name)
                    # Create the sub-directory in the image (ignore errors — may already exist)
                    execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir])
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
                urllib.request.urlretrieve(url, save_path)
                return save_path

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
            default_name = self._getit_selected_link or f"{eid}.zip"

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
                    QTimer.singleShot(0, self._nextsync_start_server_fn)
                _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after_ns_dl_gi)

        self.getit_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
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

        # Wrap in scroll area here so the stack owns the scroll area, not the bare container
        getit_scroll = QScrollArea()
        getit_scroll.setWidget(getit_container)
        getit_scroll.setWidgetResizable(True)
        getit_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        getit_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

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
        self._getit_stack.addWidget(getit_scroll)   # index 0 – normal view
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
        self.zxdb_form.addRow(zxdb_search_widget)

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
        zxdb_right_col.addWidget(zxdb_preview_container)
        zxdb_right_col.addWidget(self.zxdb_download_button)
        zxdb_right_col.addStretch()
        zxdb_right_widget = QWidget()
        zxdb_right_widget.setLayout(zxdb_right_col)

        zxdb_table_row = QHBoxLayout()
        zxdb_table_row.addWidget(self.zxdb_results_table, 1)
        zxdb_table_row.addWidget(zxdb_right_widget)
        zxdb_table_container = QWidget()
        zxdb_table_container.setLayout(zxdb_table_row)
        self.zxdb_form.addRow(zxdb_table_container)

        # --- Detail panel (rebuilt per kind: game / magazine / suggest) ---
        self._zxdb_detail_layout = QFormLayout()
        self._zxdb_detail_layout.setContentsMargins(0, 0, 0, 0)
        self._zxdb_detail_rows = []   # list of (label_widget, value_widget) pairs

        zxdb_detail_widget = QWidget()
        zxdb_detail_widget.setLayout(self._zxdb_detail_layout)
        self.zxdb_form.addRow(zxdb_detail_widget)

        # --- Internal state ---
        self._zxdb_current_page  = 1
        self._zxdb_total_pages   = 1
        self._zxdb_last_query    = ""
        self._zxdb_selected_id   = ""
        self._zxdb_selected_title = ""
        self._zxdb_selected_downloads = []
        self._zxdb_search_loading = False
        self._zxdb_loaded_once   = False
        self._zxdb_results_mode  = "games"
        self._zxdb_magazine_issues = []   # issues list of the currently-loaded magazine

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

        def _zxdb_add_row(label: str, value: str, *, dim: bool = False, wrap: bool = True):
            lab = QLabel(label)
            val = QLabel(value or "")
            if wrap:
                val.setWordWrap(True)
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

        def zxdb_populate_detail(detail: dict):
            """Game detail (used for games and by-author results)."""
            _zxdb_clear_detail_rows()
            _zxdb_add_row("Title:",       detail.get("title", ""))
            _zxdb_add_row("Year:",        detail.get("year", ""))
            _zxdb_add_row("Authors:",     detail.get("authors", ""))
            _zxdb_add_row("Publishers:",  detail.get("publishers", ""))
            _zxdb_add_row("Machine:",     detail.get("machine", ""))
            _zxdb_add_row("Genre:",       detail.get("genre", ""))
            _zxdb_add_row(
                "Description:",
                detail.get("description", "") or detail.get("remarks", ""),
                dim=True,
            )

            self._zxdb_selected_downloads = detail.get("downloads", []) or []
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

        def zxdb_run_search(query: str, page: int):
            if self._zxdb_search_loading:
                return
            mode = zxdb_current_mode()

            if mode == "suggest" and not query:
                zxdb_set_status("Type a term to get suggestions.")
                return
            if mode == "author" and not query:
                zxdb_set_status("Type an author / publisher name to search.")
                return

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

            def _on_err(err):
                zxdb_set_status(f"Error: {err[1]}")
                zxdb_set_busy(False)

            self._zxdb_search_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_run_random():
            if self._zxdb_search_loading:
                return
            self._zxdb_search_loading = True
            zxdb_set_status("Fetching random games…")
            self.zxdb_search_button.setEnabled(False)
            self.zxdb_random_button.setEnabled(False)
            self._zxdb_last_query = ""

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
                self._zxdb_search_loading = False
                zxdb_populate_results(entries, 1, 1, "games")
                zxdb_set_status(f"{len(entries)} random game(s)")
                self.zxdb_search_button.setEnabled(True)
                self.zxdb_random_button.setEnabled(True)

            def _on_err(err):
                self._zxdb_search_loading = False
                zxdb_set_status(f"Error: {err[1]}")
                self.zxdb_search_button.setEnabled(True)
                self.zxdb_random_button.setEnabled(True)

            self._zxdb_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def zxdb_on_search():
            zxdb_clear_detail()
            configuration_dictionary[SETTING_ZXDB_LAST_QUERY] = self.zxdb_search_input.text().strip()
            save_configuration_file()
            zxdb_run_search(self.zxdb_search_input.text().strip(), 1)

        def zxdb_on_random():
            zxdb_clear_detail()
            self.zxdb_search_input.clear()
            zxdb_run_random()

        def zxdb_on_prev():
            zxdb_run_search(self._zxdb_last_query, max(1, self._zxdb_current_page - 1))

        def zxdb_on_next():
            zxdb_run_search(self._zxdb_last_query, min(self._zxdb_total_pages, self._zxdb_current_page + 1))

        self.zxdb_search_button.clicked.connect(zxdb_on_search)
        self.zxdb_random_button.clicked.connect(zxdb_on_random)
        self.zxdb_search_input.returnPressed.connect(zxdb_on_search)
        self.zxdb_prev_button.clicked.connect(zxdb_on_prev)
        self.zxdb_next_button.clicked.connect(zxdb_on_next)

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
                        res = execute_hdf_monkey("ls", image_path, extra_argv=[generate_disk_file_path()])
                        if res.returncode == 0:
                            update_disk_manager_widget_table(res.stdout)
                    else:
                        zxdb_set_status(f"All {pending['n']} download(s) failed — check the URLs")

            # Create the sub-directory in the image once (ignore errors — may already exist)
            execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir])

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
            dlg.resize(820, 420)
            v = QVBoxLayout(dlg)

            info = QLabel(
                f"<b>{len(downloads)}</b> file(s) for <b>{title}</b>. "
                f"“Download all” saves into <code>downloads\\{zxdb_sanitize_folder(title)}\\</code>."
            )
            info.setWordWrap(True)
            v.addWidget(info)

            # cols: 0-Type 1-Filename 2-Size 3-Source 4-URL 5-Avail. 6-Download
            COL_AVAIL = 5
            COL_DL    = 6
            tbl = QTableWidget(len(downloads), 7, dlg)
            tbl.setHorizontalHeaderLabels(["Type", "Filename", "Size", "Source", "URL", "Avail.", ""])
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
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
            tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

            folder_root = os.path.abspath(os.path.join("downloads", zxdb_sanitize_folder(title)))

            # Per-row availability: None=pending, True=ok, False=404/error
            _avail: list = [None] * len(downloads)

            def _set_avail_cell(row: int, ok: bool):
                item = QTableWidgetItem("✅" if ok else "❌")
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(Qt.darkGreen if ok else Qt.red)
                item.setToolTip("File is available" if ok else "File returned 404 / unreachable")
                _avail[row] = ok
                tbl.setItem(row, COL_AVAIL, item)
                btn_w = tbl.cellWidget(row, COL_DL)
                if btn_w is not None:
                    btn_w.setEnabled(ok)

            def _check_url(row: int, url: str):
                def _fn():
                    try:
                        req = urllib.request.Request(
                            url,
                            method="HEAD",
                            headers={"User-Agent": ZXDB_USER_AGENT},
                        )
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            return resp.status < 400
                    except Exception:
                        return False
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
                # Download button disabled until availability is confirmed
                btn = QPushButton("Download")
                btn.setEnabled(False)
                btn.clicked.connect(_make_dl_handler(d))
                tbl.setCellWidget(row, COL_DL, btn)

            v.addWidget(tbl, 1)

            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            dl_all_btn = QPushButton(f"Download all → downloads\\{zxdb_sanitize_folder(title)}")
            close_btn  = QPushButton("Close")
            btn_row.addWidget(dl_all_btn)
            btn_row.addWidget(close_btn)
            v.addLayout(btn_row)

            close_btn.clicked.connect(dlg.accept)

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
            avail_check_enabled = getattr(self, "settings_zxdb_avail_check_checkbox", None)
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
                    btn_w = tbl.cellWidget(row, COL_DL)
                    if btn_w is not None:
                        btn_w.setEnabled(True)

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
                action = menu.exec(self.zxdb_results_table.viewport().mapToGlobal(pos))
                if action is None:
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

        zxdb_scroll = QScrollArea()
        zxdb_scroll.setWidget(zxdb_container)
        zxdb_scroll.setWidgetResizable(True)
        zxdb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        zxdb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

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
        self._zxdb_stack.addWidget(zxdb_scroll)
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
            zxdb_run_random()

        self._zxdb_on_tab_activated = zxdb_on_tab_activated

        self.setCentralWidget(wid_inner)


        # Create zx-next-unite Tab
        zx_next_unite_tab = QWidget(wid_inner.tab)
        grid_tab = QGridLayout(zx_next_unite_tab)
        grid_tab.addWidget(zx_next_unite_container) # here use the form container
        zx_next_unite_tab.setLayout(grid_tab)
        zx_next_unite_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_GOOEY
        wid_inner.tab.addTab(zx_next_unite_tab, ZX_NEXT_UNITE_TAB_TITLE_GOOEY)

        # Create NextSync Tab
        zxnextunite_NextSync_tab = QWidget(wid_inner.tab)
        grid_tab_nextsync = QGridLayout(zxnextunite_NextSync_tab)
        grid_tab_nextsync.addWidget(nextsync_container) # here use the form container
        zxnextunite_NextSync_tab.setLayout(grid_tab_nextsync)
        zxnextunite_NextSync_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC
        wid_inner.tab.addTab(zxnextunite_NextSync_tab, ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC)

        # Create GetIt Tab
        zxnextunite_GetIt_tab = QWidget(wid_inner.tab)
        grid_tab_getit = QGridLayout(zxnextunite_GetIt_tab)
        grid_tab_getit.setContentsMargins(0, 0, 0, 0)
        grid_tab_getit.addWidget(self._getit_stack)
        zxnextunite_GetIt_tab.setLayout(grid_tab_getit)
        zxnextunite_GetIt_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_GETIT
        wid_inner.tab.addTab(zxnextunite_GetIt_tab, ZX_NEXT_UNITE_TAB_TITLE_GETIT)

        # Create ZXDB Tab (right of GetIt)
        zxnextunite_ZXDB_tab = QWidget(wid_inner.tab)
        grid_tab_zxdb = QGridLayout(zxnextunite_ZXDB_tab)
        grid_tab_zxdb.setContentsMargins(0, 0, 0, 0)
        grid_tab_zxdb.addWidget(self._zxdb_stack)
        zxnextunite_ZXDB_tab.setLayout(grid_tab_zxdb)
        zxnextunite_ZXDB_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_ZXDB
        wid_inner.tab.addTab(zxnextunite_ZXDB_tab, ZX_NEXT_UNITE_TAB_TITLE_ZXDB)

        # Create Settings Tab
        zxnextunite_Settings_tab = QWidget(wid_inner.tab)
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

        def settings_zxdb_avail_check_statechanged():
            configuration_dictionary[SETTING_ZXDB_AVAIL_CHECK] = "true" if self.settings_zxdb_avail_check_checkbox.isChecked() else "false"
            save_configuration_file()

        self.settings_zxdb_avail_check_checkbox = QCheckBox("ZXDB - Perform pre-availability check on Downloads.")
        self.settings_zxdb_avail_check_checkbox.setChecked(False)
        self.settings_zxdb_avail_check_checkbox.setToolTip(
            "When enabled, the Downloads dialog sends a HEAD request for each file\n"
            "to check whether it is reachable before allowing the download.\n"
            "Files that return HTTP 404 are marked with \u274c and their Download button\n"
            "is disabled. Leave unchecked to skip the check (faster dialog open)."
        )
        self.settings_zxdb_avail_check_checkbox.stateChanged.connect(settings_zxdb_avail_check_statechanged)
        grid_tab_Settings.addWidget(self.settings_zxdb_avail_check_checkbox, 2, 0, 1, 2)

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

        settings_section_lbl = QLabel("SD Card Image Explorer — Item Colors:")
        settings_section_lbl.setToolTip("Customize the foreground color for each item type displayed in the SD card image explorer.")
        grid_tab_Settings.addWidget(settings_section_lbl, 3, 0, 1, 2)

        self.settings_btn_color_up_directory = _make_color_button(
            SETTING_COLOR_UP_DIRECTORY, "img_color_up_directory",
            "  Up Directory item",
            "Color used for the '[Up Directory..]' navigation row in the image explorer.",
            4)
        self.settings_btn_color_dir_name = _make_color_button(
            SETTING_COLOR_DIR_NAME, "img_color_dir_name",
            "  Directory name",
            "Color used for directory name entries in the image explorer.",
            5)
        self.settings_btn_color_dir_type = _make_color_button(
            SETTING_COLOR_DIR_TYPE, "img_color_dir_type",
            "  Directory type label",
            "Color used for the 'DIR' type label column of directory entries.",
            6)
        self.settings_btn_color_file_name = _make_color_button(
            SETTING_COLOR_FILE_NAME, "img_color_file_name",
            "  File name",
            "Color used for file name entries in the image explorer.",
            7)
        self.settings_btn_color_file_ext = _make_color_button(
            SETTING_COLOR_FILE_EXT, "img_color_file_ext",
            "  File extension",
            "Color used for the file extension column in the image explorer.",
            8)
        self.settings_btn_color_file_size = _make_color_button(
            SETTING_COLOR_FILE_SIZE, "img_color_file_size",
            "  File size",
            "Color used for the file size column in the image explorer.",
            9)

        grid_tab_Settings.setColumnStretch(2, 1)
        zxnextunite_Settings_tab.setLayout(grid_tab_Settings)
        zxnextunite_Settings_tab.tab_name_private = "Settings"
        wid_inner.tab.addTab(zxnextunite_Settings_tab, "Settings")

          # Create Help Tab
        zxnextunite_Help_tab = QWidget(wid_inner.tab)
        grid_tab_Help = QGridLayout(zxnextunite_Help_tab)
        grid_tab_Help.addWidget(self.listWidgetHelp) # TODO as above use the form container of Help use the form container
        zxnextunite_Help_tab.setLayout(grid_tab_Help)
        wid_inner.tab.addTab(zxnextunite_Help_tab, "?")

        #wid_inner.tab.tabBarClicked.connect(tab_changed)

        def on_tab_changed(index):
            if self._initialising:
                return
            tab_title = wid_inner.tab.tabText(index)
            if tab_title == ZX_NEXT_UNITE_TAB_TITLE_GOOEY:
                if right_disk_image_explorer_content:
                    hdfmonkeyexecresult = execute_hdf_monkey
                    if hdfmonkeyexecresult.returncode == 0:
                        update_disk_manager_widget_table(hdfmonkeyexecresult.stdout)
            elif tab_title == ZX_NEXT_UNITE_TAB_TITLE_GETIT:
                self._getit_fetch_motd()
                if self.getit_results_table.rowCount() == 0 and not self._getit_search_loading:
                    self._getit_on_latest()
            elif tab_title == ZX_NEXT_UNITE_TAB_TITLE_ZXDB:
                self._zxdb_on_tab_activated()


        #  Start main logic

        load_configuration_file()
        self._initialising = False

        # Connect tab-changed AFTER load so setCurrentIndex during config restore
        # does not trigger on_tab_changed before state is ready.
        wid_inner.tab.currentChanged.connect(on_tab_changed)

        # If the GetIt tab is already active after restoring config, trigger its
        # initialisation manually (currentChanged was not connected during load).
        if wid_inner.tab.tabText(wid_inner.tab.currentIndex()) == ZX_NEXT_UNITE_TAB_TITLE_GETIT:
            self._getit_fetch_motd()
            if self.getit_results_table.rowCount() == 0 and not self._getit_search_loading:
                self._getit_on_latest()
        elif wid_inner.tab.tabText(wid_inner.tab.currentIndex()) == ZX_NEXT_UNITE_TAB_TITLE_ZXDB:
            self._zxdb_on_tab_activated()
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

app = QApplication(sys.argv)

window = MainWindow()
window.show()

app.exec()
