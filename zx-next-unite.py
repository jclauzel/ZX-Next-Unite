
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
7
from math import log
import sys, os, string, subprocess, platform, datetime, fnmatch, socket, struct, time, glob, threading, shlex, pathlib
from PySide6.QtCore import QSize, Qt, QSortFilterProxyModel, QModelIndex, QDir, QRunnable, Slot, Signal, QObject, QThreadPool, QRect, QTimer
from PySide6.QtGui import QIcon, QColor, QAction, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QLabel, QMainWindow, QPushButton, QTableWidget, QVBoxLayout, QWidget, QFileSystemModel, QTreeView, QFormLayout, QHBoxLayout, QLineEdit, QListWidgetItem, QListWidget, QFileDialog, QTableWidgetItem, QAbstractItemView, QDialog, QGridLayout, QTabWidget, QProgressBar, QCheckBox, QMenu
from PySide6 import QtCore
import urllib.request
import urllib.parse
import zipfile, traceback
import logging
import ctypes

ZX_NEXT_UNITE_VERSION = "4.1"
ZX_NEXT_UNITE_ICON_IMAGE_FILE = "zx-next-unite.png"
ZX_NEXT_UNITE_VERBOSE_LOG_MODE = False
ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER = 1
ZX_NEXT_UNITE_UI_WIDTH = 900 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER 
ZX_NEXT_UNITE_UI_HEIGTH = 650 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER 
ZX_NEXT_UNITE_CONFIG_FILE_NAME =  "hdfg.cfg"
ZX_NEXT_UNITE_TAB_TITLE_GOOEY =  "zx-next-unite - SD Card Utility"
ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC = "NextSync - Network Transfer Manager"
ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC_SYNCON = "NextSync - Sync ON"
ZX_NEXT_UNITE_TAB_TITLE_GETIT = "GetIt"

GETIT_BASE_URL = "https://zxnext.uk"
GETIT_PAGE_SIZE = 20


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

CONFIG_FILE_SETTINGS = (SETTING_HDDFILE, SETTING_EXPLORERPATH, SETTING_SCREENSIZE, SETTING_SOUND, SETTING_VSYNC, SETTING_HERTZ, SETTING_JOYSTICK, SETTING_CSPECT, SETTING_CUSTOM, SETTING_ESC, SETTING_NEXTSYNC_EXPLORERPATH, SETTING_NEXTSYNC_SYNCONCE, SETTING_NEXTSYNC_ALWAYSSYNC, SETTING_NEXTSYNC_SLOWTRANSFER, SETTING_DEFAULT_TAB_WHEN_OPENING, SETTING_WARN_IMAGE_NEARLY_FULL, SETTING_NO_PROMPT_ON_DELETION, SETTING_COLOR_UP_DIRECTORY, SETTING_COLOR_DIR_NAME, SETTING_COLOR_DIR_TYPE, SETTING_COLOR_FILE_NAME, SETTING_COLOR_FILE_EXT, SETTING_COLOR_FILE_SIZE, SETTING_IMAGE_HISTORY)
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
    def _on_cancel_clicked(self):
        self._cancelled = True
        self._cancel_btn.setEnabled(False)
        self._action_label.setText("Cancelling\u2026")
        self._file_label.setText("")
        self.cancel_requested.emit()

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

    total_pages = max(1, (total + len(entries) - 1) // max(1, len(entries))) if entries else 1

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


def getit_parse_music_list(text: str):
    """Parse a music listing response into (entries, total, page, total_pages).

    Each entry: {'filename', 'id', 'size', 'author'}.
    """
    text = text.strip()
    entries = []
    total = page = total_pages = 0

    lines = text.splitlines()
    if not lines:
        return entries, total, page, total_pages

    header = lines[0]
    if header.startswith("^R^"):
        parts = header.split("^")
        try:
            total       = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 0
            page        = int(parts[3]) if len(parts) > 3 and parts[3].strip().isdigit() else 1
            total_pages = int(parts[4]) if len(parts) > 4 and parts[4].strip().isdigit() else 1
        except (IndexError, ValueError):
            pass

    for line in lines[1:]:
        line = line.strip()
        if not line or line == "^END^":
            break
        if line.startswith("^"):
            parts = line.split("^")
            if len(parts) >= 5:
                entries.append({
                    "filename": parts[1].strip(),
                    "id":       parts[2].strip(),
                    "size":     parts[3].strip(),
                    "author":   parts[4].strip(),
                })

    return entries, total, page, total_pages


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
        super(MainWindow, self).__init__(*args, **kwargs)

        # Prevent any save_configuration_file() calls from firing while widgets
        # are being constructed and signals are being connected — the real config
        # has not been loaded yet at that point.
        self._initialising = True

        global right_disk_image_explorer_content

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
                except:
                    logging.error(f"An error occurred in Worker.run: {sys.exc_info()}")
                    traceback.print_exc()
                    exctype, value = sys.exc_info()[:2]
                    self.signals.error.emit((exctype, value, traceback.format_exc()))
                else:
                    self.signals.result.emit(result)  # Return the result of the processing
                finally:
                    self.signals.finished.emit()  # Done        
        
        def get_tuple_value(tuple_type, text_value):
            if not tuple_type:  # empty tuple
                return None

            try:
                index = next(i for i, v in enumerate(tuple_type) if v[0] == text_value)
                return tuple_type[index][1]
            except StopIteration:
                return None  # value not found
        
        def get_int_value(str_value: str):
            if str_value == "" or str_value == None:
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

                config_array = [];
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
            if len(right_disk_image_explorer_content) !=0: # check that we have an image content first
                
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

            if len(self.right_disk_image_path) != 0 and self.right_disk_image_path != '""':
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
            
            if len(right_disk_image_explorer_content) !=0: # check that we have an image content first
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
            
            if len(right_disk_image_explorer_content) !=0: # check that we have an image content first
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
            from PySide6.QtWidgets import QMessageBox
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

                if len(command_execution) > 0:

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
                
                    if len(command_execution) > 0:
                
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
                result_path += token_path[row-1]
                row +=1
                if row == len(token_path):
                    break
                if len(token_path) != row:
                    result_path += "/"                
            return result_path , token_path[row-1]         
        
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
                from PySide6.QtWidgets import QMessageBox
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
             # Pass the function to execute
            try:
                worker = Worker(nextsync_do_server_job) # Any other args, kwargs are passed to the run function
                #worker.signals.result.connect(print_output)
                worker.signals.finished.connect(thread_complete)
                worker.signals.progress.connect(progress_fn)
                worker.signals.error.connect(nextsync_server_exception_occured)
                # Execute
                self.threadpool.start(worker)
                nextsync_hide_start_cancel_buttons()

            except Exception as e:
                logging.error(f"An unexpected error occurred while starting nextsync server. Exception: {e}")          
       
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
                config_file = open(file, "w")
                config_array = [];   
                for cs in IGNOREFILE_DEFAULT_CONTENT:
                    config_array.append(cs + '\n') 

                config_file.writelines(config_array)
                config_file.close()            
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
            
            if len(right_disk_image_explorer_content) !=0: # check that we have an image content first
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
                from PySide6.QtWidgets import QMessageBox
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
                result_path += right_disk_image_explorer_path[row-1]
                if len(right_disk_image_explorer_path) != row:
                    result_path += "/"
                row +=1
            return result_path

        def disk_image_explorer_item_double_clicked():
            
            global right_disk_image_explorer_content
            
            if len(right_disk_image_explorer_content) !=0: # check that we have an image content first
                
                set_all_buttons_disabled()

                # Reset all buttons such as Create directory or Delete files if the user suddely tries to navigate instead
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
            if len(right_disk_image_explorer_path)!=0:

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
            
            if len(self.left_file_nextsync_explorer_selection_full_filename_path) !=0:
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
            
        def nextsync_do_server_job(progress_callback):

            selected_nextsync_explorer_sync_root_directory = ""
            self.nextsync_progressbar.setValue(0)
            self.nextsync_progressbar.setVisible(True)

            # hide all buttons
            self.nextsync_button_create_syncignore.setVisible(False)
            self.nextsync_button_delete_syncignore.setVisible(False)
            self.nextsync_button_delete_syncpointfile.setVisible(False)            
            
            
            
            nextsync_show_ip_info()

            if len(self.left_file_nextsync_explorer_selection_full_filename_path) !=0:
                splitted_filepath = self.left_file_nextsync_explorer_selection_full_filename_path.split('/')
                if not os.path.isdir(self.left_file_nextsync_explorer_selection_full_filename_path):
                # if '.' in dest_file_content:
                    for file_dest_token in range (0, len(splitted_filepath)-1):
                        selected_nextsync_explorer_sync_root_directory += splitted_filepath[file_dest_token] + "/"
                else:
                    selected_nextsync_explorer_sync_root_directory = self.left_file_nextsync_explorer_selection_full_filename_path + "/"           
    
            working = True
            while working:
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
                    conn, addr = s.accept()
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
                                    progress_callback.emit(fn*100/len(f)) # send progress update info to UI
                                    fn+=1
                            elif data == b"Get" or data == b"Gee": # Really common mistransmit. Probably uart-esp..
                                bytecount = MAX_PAYLOAD
                                if bytecount + fileofs > len(filedata):
                                    bytecount = len(filedata) - fileofs                        
                                packet = filedata[fileofs:fileofs+bytecount]
                                if ZX_NEXT_UNITE_VERBOSE_LOG_MODE:
                                    if len(filedata) != 0:
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
                if self.nextsync_synconce_checkbox.isChecked():
                    working = False
                    
            nextsync_hide_start_cancel_buttons()
            self.nextsync_prepare_server.setVisible(True)
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
            tooltip_text +=not_allowed_chars
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
        self.getit_form.addRow(self.getit_results_table)

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

        getit_detail_form.addRow("Title:",       self.getit_detail_title)
        getit_detail_form.addRow("Author:",      self.getit_detail_author)
        getit_detail_form.addRow("Size:",        self.getit_detail_size)
        getit_detail_form.addRow("Date:",        self.getit_detail_date)
        getit_detail_form.addRow("Hits:",        self.getit_detail_hits)
        getit_detail_form.addRow("URL:",         self.getit_detail_url)
        getit_detail_form.addRow("Description:", self.getit_detail_desc)

        getit_detail_widget = QWidget()
        getit_detail_widget.setLayout(getit_detail_form)
        getit_detail_outer.addWidget(getit_detail_widget, 1)

        getit_dl_buttons = QVBoxLayout()
        self.getit_download_button = QPushButton("Download File")
        self.getit_download_button.setEnabled(False)
        getit_dl_buttons.addWidget(self.getit_download_button)

        self.getit_screenshot_label = QLabel()
        self.getit_screenshot_label.setFixedSize(256, 192)
        self.getit_screenshot_label.setAlignment(Qt.AlignCenter)
        self.getit_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
        self.getit_screenshot_label.setText("No preview")
        getit_dl_buttons.addWidget(self.getit_screenshot_label)
        getit_dl_buttons.addStretch()

        getit_dl_widget = QWidget()
        getit_dl_widget.setLayout(getit_dl_buttons)
        getit_detail_outer.addWidget(getit_dl_widget)

        getit_detail_container = QWidget()
        getit_detail_container.setLayout(getit_detail_outer)
        self.getit_form.addRow(getit_detail_container)

        # --- Music section ---
        getit_music_section_label = QLabel("Music (PT3 / MOD from zxart / modarchive)")
        getit_music_section_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        self.getit_form.addRow(getit_music_section_label)

        getit_music_search_row = QHBoxLayout()
        self.getit_music_search_input = QLineEdit()
        self.getit_music_search_input.setPlaceholderText("Search music... (! = artist, # = list view, leave empty for latest)")
        self.getit_music_search_input.setMinimumWidth(280)
        getit_music_search_row.addWidget(self.getit_music_search_input)

        self.getit_music_search_button = QPushButton("Search Music")
        getit_music_search_row.addWidget(self.getit_music_search_button)

        self.getit_music_latest_button = QPushButton("Latest Music")
        getit_music_search_row.addWidget(self.getit_music_latest_button)

        getit_music_search_row.addWidget(QLabel("Page:"))
        self.getit_music_page_label = QLabel("1")
        self.getit_music_page_label.setMinimumWidth(24)
        getit_music_search_row.addWidget(self.getit_music_page_label)

        self.getit_music_prev_button = QPushButton("< Prev")
        self.getit_music_prev_button.setEnabled(False)
        getit_music_search_row.addWidget(self.getit_music_prev_button)

        self.getit_music_next_button = QPushButton("Next >")
        self.getit_music_next_button.setEnabled(False)
        getit_music_search_row.addWidget(self.getit_music_next_button)

        self.getit_music_status_label = QLabel("")
        getit_music_search_row.addWidget(self.getit_music_status_label, 1)

        getit_music_search_widget = QWidget()
        getit_music_search_widget.setLayout(getit_music_search_row)
        self.getit_form.addRow(getit_music_search_widget)

        getit_music_result_row = QHBoxLayout()
        self.getit_music_table = QTableWidget(0, 4)
        self.getit_music_table.setHorizontalHeaderLabels(["Filename", "ID", "Size", "Author"])
        self.getit_music_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.getit_music_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.getit_music_table.horizontalHeader().setStretchLastSection(True)
        self.getit_music_table.setMinimumHeight(130)
        self.getit_music_table.setColumnWidth(0, 220)
        self.getit_music_table.setColumnWidth(1, 80)
        self.getit_music_table.setColumnWidth(2, 70)
        getit_music_result_row.addWidget(self.getit_music_table, 1)

        getit_music_dl_layout = QVBoxLayout()
        self.getit_music_download_button = QPushButton("Download MOD")
        self.getit_music_download_button.setEnabled(False)
        getit_music_dl_layout.addWidget(self.getit_music_download_button)
        getit_music_dl_layout.addStretch()
        getit_music_dl_widget = QWidget()
        getit_music_dl_widget.setLayout(getit_music_dl_layout)
        getit_music_result_row.addWidget(getit_music_dl_widget)

        getit_music_result_widget = QWidget()
        getit_music_result_widget.setLayout(getit_music_result_row)
        self.getit_form.addRow(getit_music_result_widget)

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

        self._getit_music_current_page = 1
        self._getit_music_total_pages  = 1
        self._getit_music_last_query   = ""
        self._getit_music_selected_id  = ""

        self._getit_motd_loaded = False
        self._getit_motd_loading = False
        self._getit_search_loading = False

        # ---- Internal helpers ----

        def getit_set_status(msg: str):
            self.getit_status_label.setText(msg)

        def getit_set_music_status(msg: str):
            self.getit_music_status_label.setText(msg)

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

        def getit_populate_music(entries, page, total_pages):
            self._getit_music_current_page = page
            self._getit_music_total_pages  = total_pages
            self.getit_music_page_label.setText(str(page))
            self.getit_music_prev_button.setEnabled(page > 1)
            self.getit_music_next_button.setEnabled(page < total_pages)

            self.getit_music_table.setRowCount(0)
            for e in entries:
                row = self.getit_music_table.rowCount()
                self.getit_music_table.insertRow(row)
                self.getit_music_table.setItem(row, 0, QTableWidgetItem(e["filename"]))
                self.getit_music_table.setItem(row, 1, QTableWidgetItem(e["id"]))
                self.getit_music_table.setItem(row, 2, QTableWidgetItem(e["size"]))
                self.getit_music_table.setItem(row, 3, QTableWidgetItem(e["author"]))

        # ---- Background search task ----

        def getit_run_search(query: str, page: int):
            if self._getit_search_loading:
                return
            self._getit_search_loading = True
            getit_set_status("Searching…")
            self.getit_search_button.setEnabled(False)
            self.getit_latest_button.setEnabled(False)
            self._getit_last_query = query

            def _search_fn():
                if query:
                    path = f"/f?s={urllib.parse.quote(query)}"
                    if page > 1:
                        path += f"?p={page}"
                else:
                    path = "/f"
                    if page > 1:
                        path += f"?p={page}"
                text = getit_fetch(path)
                entries, total, pg, total_pages = getit_parse_file_list(text)
                return (entries, total, pg, total_pages)

            def _on_result(data):
                self._getit_search_loading = False
                getit_populate_results(data[0], data[2] or page, data[3] or 1)
                getit_set_status(f"{data[1]} result(s)  |  page {data[2]}/{data[3]}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)

            def _on_error(err):
                self._getit_search_loading = False
                getit_set_status(f"Error: {err[1]}")
                self.getit_search_button.setEnabled(True)
                self.getit_latest_button.setEnabled(True)

            self._getit_search_thread = getit_run_in_thread(_search_fn, _on_result, _on_error)

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
                import tempfile, os
                url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
                tmp = tempfile.NamedTemporaryFile(suffix=".bmp", delete=False)
                tmp.close()
                urllib.request.urlretrieve(url, tmp.name)
                return tmp.name

            def _on_scr_done(path):
                px = QPixmap(path)
                import os; os.unlink(path)
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

        def getit_on_download():
            if not self._getit_selected_id:
                return
            save_path, _ = QFileDialog.getSaveFileName(
                None, "Save file", self._getit_selected_link or f"{self._getit_selected_id}.zip"
            )
            if not save_path:
                return
            eid = self._getit_selected_id
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

        self.getit_download_button.clicked.connect(getit_on_download)

        # ---- Music search ----

        def getit_run_music_search(query: str, page: int):
            getit_set_music_status("Searching…")
            self.getit_music_search_button.setEnabled(False)
            self.getit_music_latest_button.setEnabled(False)
            self._getit_music_last_query = query

            def _music_fn():
                if query:
                    path = f"/scr?s={urllib.parse.quote(query)}&p={page}"
                else:
                    path = f"/pt3?p={page}"
                text = getit_fetch(path)
                entries, total, pg, total_pages = getit_parse_music_list(text)
                return (entries, total, pg, total_pages)

            def _on_music_result(data):
                getit_populate_music(data[0], data[2] or page, data[3] or 1)
                getit_set_music_status(f"{data[1]} result(s)  |  page {data[2]}/{data[3]}")
                self.getit_music_search_button.setEnabled(True)
                self.getit_music_latest_button.setEnabled(True)

            def _on_music_error(err):
                getit_set_music_status(f"Error: {err[1]}")
                self.getit_music_search_button.setEnabled(True)
                self.getit_music_latest_button.setEnabled(True)

            self._getit_music_thread = getit_run_in_thread(_music_fn, _on_music_result, _on_music_error)

        def getit_on_music_search():
            getit_run_music_search(self.getit_music_search_input.text().strip(), 1)

        def getit_on_music_latest():
            self.getit_music_search_input.clear()
            getit_run_music_search("", 1)

        def getit_on_music_prev():
            getit_run_music_search(self._getit_music_last_query, max(1, self._getit_music_current_page - 1))

        def getit_on_music_next():
            getit_run_music_search(self._getit_music_last_query, min(self._getit_music_total_pages, self._getit_music_current_page + 1))

        self.getit_music_search_button.clicked.connect(getit_on_music_search)
        self.getit_music_latest_button.clicked.connect(getit_on_music_latest)
        self.getit_music_search_input.returnPressed.connect(getit_on_music_search)
        self.getit_music_prev_button.clicked.connect(getit_on_music_prev)
        self.getit_music_next_button.clicked.connect(getit_on_music_next)

        # ---- Music row selected → enable download ----

        def getit_on_music_row_selected():
            rows = self.getit_music_table.selectedItems()
            if not rows:
                self.getit_music_download_button.setEnabled(False)
                self._getit_music_selected_id = ""
                return
            row = self.getit_music_table.currentRow()
            self._getit_music_selected_id = self.getit_music_table.item(row, 1).text()
            self.getit_music_download_button.setEnabled(bool(self._getit_music_selected_id))

        self.getit_music_table.itemSelectionChanged.connect(getit_on_music_row_selected)

        # ---- Download MOD ----

        def getit_on_music_download():
            mid = self._getit_music_selected_id
            if not mid:
                return
            filename = ""
            row = self.getit_music_table.currentRow()
            if row >= 0:
                fn_item = self.getit_music_table.item(row, 0)
                if fn_item:
                    filename = fn_item.text()
            save_path, _ = QFileDialog.getSaveFileName(
                None, "Save MOD", filename or f"{mid}.mod"
            )
            if not save_path:
                return
            getit_set_music_status(f"Downloading MOD {mid}…")
            self.getit_music_download_button.setEnabled(False)

            def _mod_fn():
                url = f"{GETIT_BASE_URL}/mod?id={mid}"
                urllib.request.urlretrieve(url, save_path)
                return save_path

            def _on_mod_done(p):
                getit_set_music_status(f"Saved to {p}")
                self.getit_music_download_button.setEnabled(True)

            def _on_mod_error(err):
                getit_set_music_status(f"Download error: {err[1]}")
                self.getit_music_download_button.setEnabled(True)

            self._getit_mod_thread = getit_run_in_thread(_mod_fn, _on_mod_done, _on_mod_error)

        self.getit_music_download_button.clicked.connect(getit_on_music_download)

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
        self._getit_fetch_motd      = getit_fetch_motd
        self._getit_on_latest       = getit_on_latest
        self._getit_on_music_latest = getit_on_music_latest

        getit_container = QWidget()
        getit_container.setLayout(self.getit_form)

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
        grid_tab_getit.addWidget(getit_container)
        zxnextunite_GetIt_tab.setLayout(grid_tab_getit)
        zxnextunite_GetIt_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_GETIT
        wid_inner.tab.addTab(zxnextunite_GetIt_tab, ZX_NEXT_UNITE_TAB_TITLE_GETIT)

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
                from PySide6.QtWidgets import QColorDialog
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
        grid_tab_Settings.addWidget(settings_section_lbl, 2, 0, 1, 2)

        self.settings_btn_color_up_directory = _make_color_button(
            SETTING_COLOR_UP_DIRECTORY, "img_color_up_directory",
            "  Up Directory item",
            "Color used for the '[Up Directory..]' navigation row in the image explorer.",
            3)
        self.settings_btn_color_dir_name = _make_color_button(
            SETTING_COLOR_DIR_NAME, "img_color_dir_name",
            "  Directory name",
            "Color used for directory name entries in the image explorer.",
            4)
        self.settings_btn_color_dir_type = _make_color_button(
            SETTING_COLOR_DIR_TYPE, "img_color_dir_type",
            "  Directory type label",
            "Color used for the 'DIR' type label column of directory entries.",
            5)
        self.settings_btn_color_file_name = _make_color_button(
            SETTING_COLOR_FILE_NAME, "img_color_file_name",
            "  File name",
            "Color used for file name entries in the image explorer.",
            6)
        self.settings_btn_color_file_ext = _make_color_button(
            SETTING_COLOR_FILE_EXT, "img_color_file_ext",
            "  File extension",
            "Color used for the file extension column in the image explorer.",
            7)
        self.settings_btn_color_file_size = _make_color_button(
            SETTING_COLOR_FILE_SIZE, "img_color_file_size",
            "  File size",
            "Color used for the file size column in the image explorer.",
            8)

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
                if len(right_disk_image_explorer_content) != 0:
                    hdfmonkeyexecresult = execute_hdf_monkey("ls", self.right_disk_image_path, extra_argv=[generate_disk_file_path()])
                    if hdfmonkeyexecresult.returncode == 0:
                        update_disk_manager_widget_table(hdfmonkeyexecresult.stdout)
            elif tab_title == ZX_NEXT_UNITE_TAB_TITLE_GETIT:
                self._getit_fetch_motd()
                if self.getit_results_table.rowCount() == 0 and not self._getit_search_loading:
                    self._getit_on_latest()
        

        #  Start main logic

        load_configuration_file()
        self._initialising = False

        # Connect tab-changed AFTER load so setCurrentIndex during config restore
        # does not trigger on_tab_changed before state is ready.
        wid_inner.tab.currentChanged.connect(on_tab_changed)
        # Expose the nested save function so closeEvent (a class method) can call it.
        self._save_configuration_file = save_configuration_file

        if is_hdfmonkey_present():
            if load_image():
                if self.settings_warn_image_nearly_full_checkbox.isChecked():
                    _warn_if_image_nearly_full(self.right_disk_image_path)
        else:
            if platform.system() == "Windows":
                if show_hdf_monkey_download_and_install_buttons():
                    if is_hdfmonkey_present():
                        if load_image():
                            if self.settings_warn_image_nearly_full_checkbox.isChecked():
                                _warn_if_image_nearly_full(self.right_disk_image_path)

        if len(right_disk_image_explorer_content) == 0:
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
