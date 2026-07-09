"""Shared constants, data tables and pure helpers for zx-next-unite.

Extracted from zx-next-unite.py to reduce the size of the main module.
Contains no GUI/window logic — only configuration constants, lookup
tables, small pure helpers and the zxArt language state."""

import io
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


ZX_NEXT_UNITE_VERSION = "8.7"
# Set to False to hide all Download / Send to SD Card / Send via NextSync
# buttons and context-menu actions for the respective pane.
ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS  = False
ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS = False
ZX_NEXT_UNITE_SHOW_ZXDB_PANE = True
ZX_NEXT_UNITE_SHOW_ZXART_PANE = True

ZX_NEXT_UNITE_ICON_IMAGE_FILE = "zx-next-unite.png"
ZX_NEXT_UNITE_VERBOSE_LOG_MODE = False
ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER = 1
ZX_NEXT_UNITE_UI_WIDTH = 900 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER
ZX_NEXT_UNITE_UI_HEIGTH = 650 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER
ZX_NEXT_UNITE_CONFIG_FILE_NAME = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "hdfg.cfg")
# Debounce window (ms) for the NextSync "prepare" file scan: rapid explorer
# selection changes are coalesced so the recursive scan runs once after settling.
NEXTSYNC_PREPARE_DEBOUNCE_MS = 300

ZX_NEXT_UNITE_TAB_TITLE_GOOEY = "TOOL: SD Card Utility"
ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC = "TOOL: NextSync"
ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC_SYNCON = "NextSync - Sync ON"
ZX_NEXT_UNITE_TAB_TITLE_GETIT = "🌍 GetIt"
ZX_NEXT_UNITE_TAB_TITLE_ZXDB  = "🌍 ZXDB/ZXinfo.dk"
ZX_NEXT_UNITE_TAB_TITLE_ZXART = "🌍 ZXArt.ee"
ZX_NEXT_UNITE_TAB_TITLE_FAVORITES = "🌍 ♥ Favorites"
ZX_NEXT_UNITE_TAB_TITLE_ALLINONE  = "🌍 Unite!"
ZX_NEXT_UNITE_TAB_TITLE_ITCHIO    = "🌍 itch.io"

GETIT_BASE_URL = "https://zxnext.uk"
GETIT_USER_AGENT = f"ZX-Next-Unite/{ZX_NEXT_UNITE_VERSION}"
GETIT_PAGE_SIZE = 18

# Minimum number of characters required before a keyword search is accepted.
SEARCH_MIN_CHARS = 3

ZXDB_BASE_URL = "https://api.zxinfo.dk/v3"
ZXDB_USER_AGENT = f"ZX-Next-Unite/{ZX_NEXT_UNITE_VERSION}"
ZXDB_PAGE_SIZE = 20

ZXART_BASE_URL = "https://zxart.ee/api"
ZXART_USER_AGENT = f"ZX-Next-Unite/{ZX_NEXT_UNITE_VERSION}"
ZXART_PAGE_SIZE = 20

# itch.io — optional tab driven by the 'itch-dl' package. Browsing uses the
# public itch.io API directly (api.itch.io) with the user's personal API key;
# the actual install/download of a collection item is delegated to itch-dl.
ITCH_API_BASE = "https://api.itch.io"
ITCH_USER_AGENT = f"ZX-Next-Unite/{ZX_NEXT_UNITE_VERSION}"
ITCH_PAGE_SIZE = 30
# Canonical itch.io page for CSpect (Mike Dailly's emulator). The startup
# update check builds a minimal game dict from this URL to look up the account's
# owned CSpect download key and the newest available build (see
# zxnu_itchio.latest_cspect_upload / install_cspect_update).
CSPECT_ITCH_URL = "https://mdf200.itch.io/cspect"
# Safety bound on how many API pages the owned-games / collection / library
# walks will follow. The walks stop naturally on the last (short) page; this is
# only a runaway guard. Set high so large libraries (a user with 1000s of
# purchases or a big collection) load in full rather than being truncated.
ITCH_MAX_PAGES = 1000

# AllInOne pane aggregates results from GetIt + ZXDB + zxArt (+ itch.io when a
# key is configured). Paging is applied client-side on the merged result list.
ALLINONE_PAGE_SIZE = GETIT_PAGE_SIZE + ZXDB_PAGE_SIZE + ZXART_PAGE_SIZE

# Caps how many gallery thumbnail/asset fetches do their HTTP + image-decode
# work at once (enforced by a semaphore). A full Unite! page can have ~58 cells,
# each kicking off an image (and sometimes a metadata) fetch; letting them all
# run simultaneously starves the UI thread (Python GIL) and floods the remote
# servers, hanging the UI on Latest/Random. High enough to download several in
# parallel, low enough to keep the UI responsive. Tune up for faster bulk loads,
# down for a smoother UI.
GALLERY_THUMB_FETCH_WORKERS = 16


# Legacy single-platform Windows build (kept only as a manual fallback URL in
# messages). The pre-compiled binary it ships has a known bug (issue #50), so the
# in-app auto-download now uses HDF_MONKEY_JJJS_URL below instead.
HDF_MONKEY_WINDOWS_URL = "https://uto.speccy.org/downloads/hdfmonkey_windows.zip"

# Current in-app auto-download source for hdfmonkey: a jjjs-packaged release that
# bundles fixed builds for every platform (windows-64 / linux-musl / macos-intel
# / macos-mn). The forum attachment is served by id; the session token (sid) in a
# copy-pasted browser link expires, so we deliberately omit it. The archive is a
# zip-inside-a-zip: the outer zip holds a ZipCrypto-encrypted inner zip plus a
# 'password.txt' whose contents (currently "jjjs") unlock it. See
# extract_hdfmonkey_from_jjjs_zip().
HDF_MONKEY_JJJS_URL = "https://www.specnext.com/forum/download/file.php?id=1159"
HDF_MONKEY_JJJS_ZIP_PASSWORD = b"jjjs"
# File name the in-app auto-download saves the jjjs archive under (inside the
# top-level downloads folder). Also the name the manual-fallback message suggests
# so find_hdfmonkey_jjjs_zip_in_downloads re-discovers a hand-placed copy.
HDF_MONKEY_JJJS_ZIP_FILENAME = "hdfmonkey_jjjs.zip"

SETTING_HDDFILE = "hddffile"
SETTING_EXPLORERPATH = "explorerpath"
SETTING_SCREENSIZE = "screensize"
SETTING_SOUND = "sound"
SETTING_VSYNC = "vsync"
SETTING_HERTZ = "hertz"
SETTING_JOYSTICK = "joy"
SETTING_MOUSE = "mouse"
SETTING_CSPECT = "cspect"
SETTING_CUSTOM = "custom"
SETTING_ESC = "esc"                                                # combo index into CSPECT_ESC (ESC-exit disable on/off; default 0 = off, no -esc)
SETTING_MAME_COMMAND_LINE_PARAMETERS = "mame_command_line_parameters"
SETTING_MAME_ROM_CHOICE              = "mame_rom_choice"            # MAME system/ROM, e.g. "tbblue" (default)
SETTING_MAME_UPDATE_CHECK            = "mame_update_check"          # "false" => skip the startup MAME update check (default on)
SETTING_MAME_INSTALLED_TAG           = "mame_installed_tag"         # GitHub release tag of the MAME build installed via the app (e.g. "mame0272")
SETTING_MAME_ASPECT                  = "mame_aspect"               # combo index into MAME_ASPECT (display aspect ratio, e.g. 2:1 / 1:1)
SETTING_MAME_SOUND                   = "mame_sound"                # combo index into MAME_SOUND (audio on/off)
SETTING_MAME_MOUSE                   = "mame_mouse"                # combo index into MAME_MOUSE (mouse capture on/off)
SETTING_MAME_JOYSTICK                = "mame_joystick"             # combo index into MAME_JOYSTICK (joystick input on/off)
SETTING_MAME_ESC                     = "mame_esc"                  # combo index into MAME_ESC (ESC-exit disable on/off; default 0 = off, no -esc)
SETTING_DISABLE_NO_EMULATOR_TOAST  = "disable_no_emulator_toast"   # bool (default False)
SETTING_NEXTSYNC_EXPLORERPATH = "nextsync_explorerpath"
SETTING_NEXTSYNC_SYNCONCE = "nextsync_synconce"
SETTING_NEXTSYNC_ALWAYSSYNC = "nextsync_alwayssync"
SETTING_NEXTSYNC_SLOWTRANSFER = "nextsync_slowtransfer"
SETTING_DEFAULT_TAB_WHEN_OPENING = "default_tab"
SETTING_WARN_IMAGE_NEARLY_FULL = "warn_image_nearly_full"
SETTING_NO_PROMPT_ON_DELETION  = "no_prompt_on_deletion"
SETTING_AVAIL_CHECK            = "avail_check"
SETTING_MULTI_SEARCH           = "multi_search"
SETTING_SEARCH_AUTOCOMPLETE    = "search_autocomplete"
SETTING_SEARCH_SORT_MODE       = "search_sort_mode"        # "getit_first" (default) | "mixed" | "classic"
SETTING_CRASH_LOG_ENABLED      = "crash_log_enabled"
SETTING_ZXDB_LAST_MODE         = "zxdb_last_mode"
SETTING_ZXDB_LAST_QUERY        = "zxdb_last_query"
SETTING_ZXART_LAST_MODE        = "zxart_last_mode"
SETTING_ZXART_LAST_QUERY       = "zxart_last_query"
SETTING_COLOR_UP_DIRECTORY = "color_up_directory"
SETTING_COLOR_DIR_NAME    = "color_dir_name"
SETTING_COLOR_DIR_TYPE    = "color_dir_type"
SETTING_COLOR_FILE_NAME   = "color_file_name"
SETTING_COLOR_FILE_EXT    = "color_file_ext"
SETTING_COLOR_FILE_SIZE   = "color_file_size"
SETTING_COLOR_GENERAL_TEXT = "color_general_text"   # app-wide Classic-mode text colour
SETTING_DESKTOP_THEME     = "desktop_theme"     # "automatic" (default) | "dark" | "custom"
SETTING_IMAGE_HISTORY     = "image_history"
SETTING_BG_OPACITY        = "bg_opacity"
SETTING_BG_IMAGE          = "bg_image"          # "" = Random, else filename (basename only)
SETTING_CONTENT_DISCLAIMER_AGREED = "content_disclaimer_agreed"
SETTING_NEXTSYNC_SEND_CONFLICT = "nextsync_send_conflict"   # "prompt" (default) | "overwrite" | "ignore"
SETTING_GALLERY_ANIM_MODE      = "gallery_anim_mode"        # "hover" (default), "timer" or "none"
SETTING_GALLERY_ROWS_PER_PAGE  = "gallery_rows_per_page"    # int 1..10, default 2
SETTING_GALLERY_COLS           = "gallery_cols"             # int: 2 | 4 (default) | 8
SETTING_GALLERY_IMG_SIZE       = "gallery_img_size"         # "small" | "medium" (default) | "large"
SETTING_GALLERY_SLIDESHOW_SECS = "gallery_slideshow_secs"   # int seconds: 5 (default) | 10 | 15 | 30 | 60
SETTING_GETIT_VIEW_MODE        = "getit_view_mode"          # "table" (default) or "gallery"
SETTING_ZXDB_VIEW_MODE         = "zxdb_view_mode"
SETTING_ZXART_VIEW_MODE        = "zxart_view_mode"
SETTING_ZXART_LANGUAGE         = "zxart_language"          # "eng" | "pol" | "spa"
SETTING_FAVORITES              = "favorites"               # JSON list of favorite entries
SETTING_FAVORITES_VIEW_MODE    = "favorites_view_mode"     # "gallery" (default) or "table"
SETTING_ALLINONE_VIEW_MODE     = "allinone_view_mode"      # "gallery" (default) or "table"
SETTING_ALLINONE_PYGAME_MODE   = "allinone_pygame_mode"    # "true" => pygame visualization, else classic
SETTING_NEXTSYNC_PYGAME_MODE   = "nextsync_pygame_mode"    # "true" => retro 8-bit pygame log window on the NextSync tab, else classic list
SETTING_SDCARD_PYGAME_LOG      = "sdcard_pygame_log"       # "true" => retro 8-bit pygame log window on the SD Card utility tab, else classic list
SETTING_HELP_PYGAME_LOG        = "help_pygame_log"         # "true" => retro 8-bit pygame console on the "?" Help tab, else classic list
SETTING_ITCHIO_API_KEY         = "itchio_api_key"          # str: personal itch.io API key (https://itch.io/user/settings/api-keys)
SETTING_SHOW_ITCHIO_TAB        = "show_itchio_tab"         # "false" => hide the itch.io tab (default shown when itch-dl is installed)
SETTING_ITCHIO_VIEW_MODE       = "itchio_view_mode"        # "gallery" (default) or "table"
SETTING_CSPECT_UPDATE_CHECK    = "cspect_update_check"     # "false" => skip the startup itch.io CSpect update check (default on)
# Per-pane item-viewer mode: "true" => open items in the Retro (pygame) viewer
# (renders .txt/instruction pages as a log console), else the Classic Qt viewer.
SETTING_GETIT_ITEM_RETRO       = "getit_item_retro"
SETTING_ZXDB_ITEM_RETRO        = "zxdb_item_retro"
SETTING_ZXART_ITEM_RETRO       = "zxart_item_retro"
SETTING_ITCHIO_ITEM_RETRO      = "itchio_item_retro"
SETTING_FAVORITES_ITEM_RETRO   = "favorites_item_retro"
SETTING_NEXTSYNC_PYGAME_ANIM   = "nextsync_pygame_anim"    # "false" => freeze the starfield in the NextSync retro log (default on)
SETTING_ALLINONE_PYGAME_ANIM   = "allinone_pygame_anim"    # "false" => disable the Space-Invaders background (default on)
SETTING_ALIEN_FLOYD_BG         = "alien_floyd_bg"          # "true" => pygame-ce "Alien Floyd's" animated background on all tabs (default off)
SETTING_ALIEN_FLOYD_TAB        = "alien_floyd_tab"         # "true" => show the dedicated "Alien Floyd's" tab (default off)
SETTING_ALIEN_FLOYD_HISCORE    = "alien_floyd_hiscore"     # int: legacy single best score (superseded by the table below)
SETTING_ALIEN_FLOYD_HISCORES   = "alien_floyd_hiscores"    # str: "Alien Floyd's" top-5 table, "NAME:SCORE;NAME:SCORE;…"
DEFAULT_ZXART_LANGUAGE         = "eng"
ZXART_LANGUAGE_CHOICES         = (
    ("English", "eng"),
    ("Polish",  "pol"),
    ("Spanish", "spa"),
)
ZXART_LEGAL_STATUS_LABELS = {
    "":            "",
    "unknown":     "Unknown",
    "original":    "Original",
    "rerelease":   "Re-release",
    "adaptation":  "Adaptation",
    "localization": "Localization",
    "mod":         "Modification",
    "crack":       "Cracked",
    "mia":         "Missing in action",
    "corrupted":   "Corrupted",
    "compilation": "Compilation",
    "incomplete":  "Incomplete",
    "demoversion": "Demo version",
    "forbidden":   "Forbidden by author",
    "unreleased":  "Unreleased",
    "recovered":   "Recovered",
    "illegal":     "Illegal",
    "allowed":     "Distribution allowed",
}
ZXART_LEGAL_STATUS_CACHE = dict(ZXART_LEGAL_STATUS_LABELS)
def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def subprocess_no_window_kwargs(extra_flags=0, gui_app=False):
    """Return subprocess kwargs that prevent a console window from flashing.

    On Windows, command-line tools such as hdfmonkey would otherwise briefly
    pop up a console window every time they run (e.g. when deleting or sending
    a file). Passing CREATE_NO_WINDOW plus a STARTUPINFO with a hidden window
    keeps them invisible. On other platforms this is a no-op. ``extra_flags``
    lets callers OR in additional Windows creation flags (e.g.
    CREATE_NEW_PROCESS_GROUP).

    Set ``gui_app=True`` when launching a *graphical* program (e.g. the MAME
    emulator) rather than a CLI tool. Such a program creates its own top-level
    window and, when it does so with SW_SHOWDEFAULT, inherits the initial
    show-state from ``STARTUPINFO.wShowWindow`` — so the SW_HIDE startupinfo used
    for CLI tools would make the program's own window open *invisible*. For GUI
    apps we therefore still suppress a stray console window (CREATE_NO_WINDOW)
    but leave the show-state alone so the app's window appears normally.
    """
    if platform.system() != "Windows":
        return {}
    CREATE_NO_WINDOW = 0x08000000
    kwargs = {"creationflags": CREATE_NO_WINDOW | extra_flags}
    if not gui_app:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return kwargs
def zxart_legal_status_label(code) -> str:
    """Return a human-readable label for a zxArt ``legalStatus`` code.

    Unknown codes are passed through (prettified) and memoised so they are
    only formatted once per session.
    """
    if code is None:
        return ""
    key = str(code).strip()
    if not key:
        return ""
    lk = key.lower()
    cached = ZXART_LEGAL_STATUS_CACHE.get(lk)
    if cached is not None:
        return cached
    # Unknown code: prettify (e.g. "some_value" -> "Some Value") and cache.
    label = key.replace("_", " ").replace("-", " ").strip()
    label = label[:1].upper() + label[1:] if label else key
    ZXART_LEGAL_STATUS_CACHE[lk] = label
    return label
ZXART_UI_TRANSLATIONS = {
    # Toolbar / controls
    "Search":           {"pol": "Szukaj",         "spa": "Buscar"},
    "Deep":             {"pol": "Głęboko",        "spa": "Profundo"},
    "Productions":      {"pol": "Produkcje",      "spa": "Producciones"},
    "By letter":        {"pol": "Wg litery",      "spa": "Por letra"},
    "Pictures":         {"pol": "Obrazy",         "spa": "Imágenes"},
    "Page:":            {"pol": "Strona:",        "spa": "Página:"},
    "< Prev":           {"pol": "< Poprzednia",   "spa": "< Anterior"},
    "Next >":           {"pol": "Następna >",     "spa": "Siguiente >"},
    "View:":            {"pol": "Widok:",         "spa": "Vista:"},
    "Table":            {"pol": "Tabela",         "spa": "Tabla"},
    "Gallery":          {"pol": "Galeria",        "spa": "Galería"},
    "Language:":        {"pol": "Język:",         "spa": "Idioma:"},
    # Detail / metadata field names
    "Title:":           {"pol": "Tytuł:",         "spa": "Título:"},
    "Year:":            {"pol": "Rok:",           "spa": "Año:"},
    "Authors:":         {"pol": "Autorzy:",       "spa": "Autores:"},
    "Groups:":          {"pol": "Grupy:",         "spa": "Grupos:"},
    "Produced by:":     {"pol": "Wyprodukowane przez:", "spa": "Producido por:"},
    "Compo:":           {"pol": "Konkurs:",       "spa": "Concurso:"},
    "Place:":           {"pol": "Miejsce:",       "spa": "Puesto:"},
    "Languages:":       {"pol": "Języki:",        "spa": "Idiomas:"},
    "Legal:":           {"pol": "Status prawny:", "spa": "Estado legal:"},
    "Description:":     {"pol": "Opis:",          "spa": "Descripción:"},
    "Type:":            {"pol": "Typ:",           "spa": "Tipo:"},
    "Rating:":          {"pol": "Ocena:",         "spa": "Valoración:"},
    "Views:":           {"pol": "Wyświetlenia:",  "spa": "Vistas:"},
    "Tags:":            {"pol": "Tagi:",          "spa": "Etiquetas:"},
    "Language:":        {"pol": "Język:",         "spa": "Idioma:"},
    # Table headers
    "ID":               {"pol": "ID",             "spa": "ID"},
    "Title":            {"pol": "Tytuł",          "spa": "Título"},
    "Year":             {"pol": "Rok",            "spa": "Año"},
    "Author / Group":   {"pol": "Autor / Grupa",  "spa": "Autor / Grupo"},
    "Author(s)":        {"pol": "Autor(zy)",      "spa": "Autor(es)"},
    "Genre / Compo":    {"pol": "Gatunek / Konkurs", "spa": "Género / Concurso"},
    "Tags":             {"pol": "Tagi",           "spa": "Etiquetas"},
    # Action buttons / context menu
    "Download":         {"pol": "Pobierz",        "spa": "Descargar"},
    "Download File":    {"pol": "Pobierz plik",   "spa": "Descargar archivo"},
    "Send to SD card":  {"pol": "Wyślij na kartę SD", "spa": "Enviar a tarjeta SD"},
    "Send via NextSync":{"pol": "Wyślij przez NextSync", "spa": "Enviar por NextSync"},
    # Status/messages
    "No preview":       {"pol": "Brak podglądu",  "spa": "Sin vista previa"},
}


def _zxart_tr(text: str) -> str:
    """Translate a zxArt UI string into the currently-selected language."""
    if not isinstance(text, str) or not text:
        return text
    lang = _zxart_lang()
    if lang == "eng":
        return text
    entry = ZXART_UI_TRANSLATIONS.get(text)
    if not entry:
        return text
    return entry.get(lang, text)
DEFAULT_NEXTSYNC_SEND_CONFLICT = "prompt"   # how to handle a received file/dir that already exists locally
DEFAULT_GALLERY_ANIM_MODE      = "hover"
DEFAULT_GALLERY_ROWS_PER_PAGE  = 2
DEFAULT_GALLERY_COLS           = 4
DEFAULT_GALLERY_IMG_SIZE       = "medium"
# Gallery slideshow pause time (seconds): how long each screenshot stays on
# screen before the auto-advancing slideshow moves on. Shared by every gallery
# item viewer (Qt + pygame) and the ZXDB / zxArt detail slideshows.
DEFAULT_GALLERY_SLIDESHOW_SECS = 5
GALLERY_SLIDESHOW_SECS_CHOICES = (5, 10, 15, 30, 60)
# The live slideshow pause time (seconds) is kept in a module-level global so
# every viewer shares one user-configurable cadence without threading a getter
# through each constructor. MainWindow calls set_gallery_slideshow_secs() on
# config load and whenever the Settings combo changes; the viewers read the
# interval back via gallery_slideshow_interval_ms() each time they (re)arm.
_gallery_slideshow_secs = DEFAULT_GALLERY_SLIDESHOW_SECS

def set_gallery_slideshow_secs(secs):
    """Set the shared slideshow pause time (seconds). Silently ignores anything
    that is not one of GALLERY_SLIDESHOW_SECS_CHOICES."""
    global _gallery_slideshow_secs
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return
    if s in GALLERY_SLIDESHOW_SECS_CHOICES:
        _gallery_slideshow_secs = s

def gallery_slideshow_secs():
    """Current slideshow pause time, in seconds."""
    return _gallery_slideshow_secs

def gallery_slideshow_interval_ms():
    """Current slideshow pause time in milliseconds (for QTimer.setInterval)."""
    return _gallery_slideshow_secs * 1000
# Unite! multi-search result sort/render modes. These decide the *base* order
# of the merged result list before the gallery's image-first re-sort lifts
# picture-bearing items to the top (that image-first rule is common to all
# modes — it is only the per-source ordering that differs).
#   • getit_first : GetIt catalogue leads, then ZXDB, zxArt, itch.io (default).
#   • mixed       : sources are round-robin interleaved so GetIt is scattered
#                   among the others rather than leading the list.
#   • classic     : ZXDB / zxArt (and itch.io) lead, GetIt content trails last.
SEARCH_SORT_GETIT_FIRST        = "getit_first"
SEARCH_SORT_MIXED              = "mixed"
SEARCH_SORT_CLASSIC            = "classic"
DEFAULT_SEARCH_SORT_MODE       = SEARCH_SORT_GETIT_FIRST
GALLERY_COLS                   = 4
GALLERY_MIN_ROWS               = 1
GALLERY_MAX_ROWS               = 10
MAX_ALT_TEXT_LINES             = 5
MAX_IMAGE_HISTORY         = 10

DEFAULT_COLOR_UP_DIRECTORY = "#ff0000"
DEFAULT_COLOR_DIR_NAME    = "#0000ff"
DEFAULT_COLOR_DIR_TYPE    = "#0000ff"
DEFAULT_COLOR_FILE_NAME   = "#00ff00"
DEFAULT_COLOR_FILE_EXT    = "#00ff00"
DEFAULT_COLOR_FILE_SIZE   = "#00ff00"
# General app-wide UI text (labels, checkboxes, section headers, …) in Classic
# (non-pygame) mode. The tab panes are always rendered dark regardless of the OS
# theme, so this defaults to a light colour that stays readable even when the OS
# desktop uses a light/White palette (which would otherwise leave the text black
# and invisible — see issue #118).
DEFAULT_COLOR_GENERAL_TEXT = "#e8e8e8"

# ── Desktop theme (SD Card explorer font colours) ───────────────────────────
# Mode meanings:
#   "automatic" - follow the OS: high-contrast/accessibility -> all-black
#                 palette, else dark desktop -> the dark tweaks, else -> the
#                 light (white) palette (the plain defaults above).
#   "white"     - force the light palette (Directory name/type stay blue).
#   "dark"      - force the dark tweaks (orange/yellow).
#   "black"     - high-contrast: every font colour is black (accessibility).
#   "custom"    - user hand-picked colours; leave them alone.
DESKTOP_THEME_AUTOMATIC = "automatic"
DESKTOP_THEME_WHITE     = "white"
DESKTOP_THEME_DARK      = "dark"
DESKTOP_THEME_BLACK     = "black"
DESKTOP_THEME_CUSTOM    = "custom"
DEFAULT_DESKTOP_THEME   = DESKTOP_THEME_AUTOMATIC
# In dark mode only the two hard-to-read blue entries change; every other colour
# keeps its default.
DARK_COLOR_DIR_NAME     = "#ffa500"   # Directory name : orange (was blue)
DARK_COLOR_DIR_TYPE     = "#ffff00"   # Directory type : yellow (was blue)
# High-contrast (accessibility): every explorer font colour becomes black.
HIGH_CONTRAST_COLOR     = "#000000"
# General UI text in high-contrast mode: the tab panes stay dark, so the most
# readable / highest-contrast choice for the app chrome is pure white.
HIGH_CONTRAST_TEXT_COLOR = "#ffffff"

def detect_system_dark_theme():
    """Best-effort detection of whether the OS desktop theme is dark.

    Returns True for dark, False for light/unknown. Cross-platform (Windows and
    Linux desktops — GNOME, KDE, Commodore OS, …), trying in order:
      1. Qt's own colour-scheme hint (Qt 6.5+, reads the OS setting directly),
      2. the Windows registry (AppsUseLightTheme),
      3. GNOME gsettings / KDE kdeglobals,
      4. the active QPalette's lightness (covers any other Qt-themed desktop).
    """
    import sys as _sys
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt as _Qt
        app = QApplication.instance()
    except Exception:
        app = None

    # 1) Qt colour-scheme hint (most reliable when present).
    if app is not None:
        try:
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if scheme == _Qt.ColorScheme.Dark:
                    return True
                if scheme == _Qt.ColorScheme.Light:
                    return False
        except Exception:
            pass

    # 2) Windows registry (authoritative when the Qt hint is unavailable).
    if _sys.platform.startswith("win"):
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            winreg.CloseKey(k)
            return int(val) == 0
        except Exception:
            pass

    # 3) Linux desktops: GNOME (gsettings) then KDE (kdeglobals).
    try:
        import subprocess
        for _key in ("color-scheme", "gtk-theme"):
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", _key],
                capture_output=True, text=True, timeout=1.5)
            if out.returncode == 0 and "dark" in out.stdout.lower():
                return True
    except Exception:
        pass
    try:
        import os as _os
        _kg = _os.path.expanduser("~/.config/kdeglobals")
        if _os.path.isfile(_kg):
            with open(_kg, "r", errors="ignore") as _f:
                for _line in _f:
                    _l = _line.strip().lower()
                    if _l.startswith("colorscheme") and "dark" in _l:
                        return True
    except Exception:
        pass

    # 4) Fallback: compare the active palette's window vs text lightness. Works
    #    on any desktop that themes Qt (incl. Commodore OS) once an app exists.
    if app is not None:
        try:
            from PySide6.QtGui import QPalette
            pal = app.palette()
            win = pal.color(QPalette.ColorRole.Window)
            txt = pal.color(QPalette.ColorRole.WindowText)
            return win.lightness() < txt.lightness()
        except Exception:
            pass
    return False

def detect_system_high_contrast():
    """Best-effort detection of an OS high-contrast / accessibility theme.

    Returns True when the user has a high-contrast accessibility mode enabled
    (Windows "High Contrast", GNOME a11y high-contrast, or a HighContrast GTK
    theme). Used so Automatic mode can switch the explorer fonts to the
    all-black high-contrast palette for readability.
    """
    import sys as _sys
    if _sys.platform.startswith("win"):
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Accessibility\HighContrast")
            flags, _ = winreg.QueryValueEx(k, "Flags")
            winreg.CloseKey(k)
            return (int(flags) & 1) == 1   # HCF_HIGHCONTRASTON
        except Exception:
            return False
    # Linux GNOME: explicit a11y high-contrast toggle, or a HighContrast theme.
    try:
        import subprocess
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.a11y.interface", "high-contrast"],
            capture_output=True, text=True, timeout=1.5)
        if out.returncode == 0 and "true" in out.stdout.lower():
            return True
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True, text=True, timeout=1.5)
        if out.returncode == 0 and "highcontrast" in out.stdout.lower().replace("-", "").replace(" ", ""):
            return True
    except Exception:
        pass
    return False

PORT = 2048    # Port to listen on (non-privileged ports are > 1023)
VERSION3 = "NextSync3"
# Sync4 adds the bidirectional ("-send") upload direction (Next -> PC). Old Sync3
# dots keep working unchanged (PC -> Next only); only Sync4 dots can push files
# to the app. VERSION below doubles as the banner text and the Sync4 reply.
VERSION4 = "NextSync4"
VERSION = "NextSync4"
IGNOREFILE = "syncignore.txt"
SYNCPOINT = "syncpoint.dat"
MAX_PAYLOAD = 1024
NEXTSYNC_UI_HEIGTH_MULTIPLIER = 1
NEXTSYNC_UI_HEIGTH = 300 * ZX_NEXT_UNITE_UI_SIZE_MULTIPLIER
IGNOREFILE_DEFAULT_CONTENT = (("syncignore.txt"), ("syncpoint.dat"), ("zx-next-unite.png"),("*.bak"), ("*.py"), ("*.pyproj"), ("*.pyproj"), ("hdfmonkey.exe"), ("hdfg.cfg"))

INIT_LOG = (("NextSync - by Jari Komppa and Julien Clauzel"), ("MAME - ZX Spectrum Next support by Holub https://wiki.specnext.dev/MAME:Installing"), ("HDF Monkey - by Matt Westcott"), ("CSpect - by Mike Dailly http://cspect.org"), ("Inspired by HDFM-GOOEY - by em00k"), ("zx-next-unite - by Julien Clauzel 2024"))
INIT_HELP = ((f"Welcome to zx-next-unite {ZX_NEXT_UNITE_VERSION} help"),
             (""),
             ("Introduction:"),
             ("--------"),
             ("HdfmGooey was initialy created by emOOk and NextSync by Jari Komppa."),
             ("A while back I rambled with the idea of an all in one bootstrapper transfer tool to"),
             ("avoid manipulating SD cards for the Spectrum Next and that was the initial idea of it."),
             ("Last but not the least some source code was lost from HDFM Gooey and the tool was stuck back in that time,"),
             ("with the agreement of emOOk I started a rewrite in Python and later with Jari"),
             ("The point of using Python that would also provide MacOS and Linux portability."),
             ("Later down the line I then extended the NextSync functionality from Sync3 to Sync4."),
             ("The new .snyc4 command for the Next can send Sync4 that therefore alow to send files and directories using -send command line option."),
             ("There is as well a new nextsync4.py command line located in nextsync\\sync\\server that support the new Sync4 protocol."),
             ("Here we are now you have it!"),
             (""),
             (""),
             ("Keyboard shortcuts"),
             ("------------------"),
             ("The three file explorers (SD Card local, SD Card disk image and NextSync local) share these shortcuts. Copy / Cut / Paste work across all three explorers and also exchange with the operating-system clipboard (e.g. copy in Windows Explorer, paste into the disk image, and vice-versa):"),
             ("    Ctrl+C  -  Copy the selected file(s)/folder(s) to the shared clipboard."),
             ("    Ctrl+X  -  Cut the selection (moved to the destination on the next paste)."),
             ("    Ctrl+V  -  Paste into the selected / currently shown folder."),
             ("    F2      -  Rename the selected file or folder."),
             ("    Delete  -  Delete the selected file or folder (disk-image & NextSync explorers)."),
             (""),
             ("In the picture (gallery) item viewer (double-click an item in the GetIt, ZXDB, zxArt or itch.io tabs):"),
             ("    Esc           -  Close the viewer and return to the gallery."),
             ("    Left / Right  -  Show the previous / next screenshot."),
             (""),
             (""),
             ("Third party license"),
             ("-------------------"),
             ("zx-next-unite is a Qt Application using pyside6 in Python on top of Qt6, which retains the GPLv2 Licensing."),
             ("Please refer to the LICENSE file on github: https://github.com/jclauzel/zx-next-unite/blob/master/LICENSE.txt."),
             (""),
             ("Pyside6 is not bundled when performing a manual python install and needs to be installed separately (see installation instructions)."),
             (""),
             ("zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org."),
             (""),
             ("pygame-ce is distributed under the GNU LGPL v2.1 license and, like Pyside6, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions)."),
             (""),
             ("zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl."),
             (""),
             ("itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like Pyside6 and pygame-ce, is not bundled when performing a manual python install and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed."),
             (""),
             ("Setup & How to:"),
             ("---------------"),
             ("Checkout main setup & demo video avaible at: https://youtu.be/-gUxV4fM1yo  (and the full python install is covered in the old py-hdfm-gooey since ZX-Next-Unite is an evolution of it : https://youtu.be/FJG-Z0DCIjQ )"),
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
             ("Third-Party Content Sources (GetIt / ZXDB / zxArt):"),
             ("----------------------------------------------------"),
             ("zx-next-unite integrates three external databases to let you browse and download"),
             ("Spectrum-related software and artwork directly from within the application."),
             ("The application consumes their public APIs — it does not host, mirror, or"),
             ("redistribute any of the files itself."),
             (""),
             ("GetIt:"),
             ("  GetIt is a community-maintained archive of ZX Spectrum Next software."),
             ("  API base URL : https://www.specnext.com/getit/"),
             ("  The application queries the GetIt API to list and search files, then"),
             ("  downloads them directly from the URLs returned by that API."),
             (""),
             ("ZXDB:"),
             ("  ZXDB is an open-source database of ZX Spectrum and related software,"),
             ("  maintained by the community at https://github.com/zxdb/ZXDB ."),
             (f"  API base URL : {ZXDB_BASE_URL}"),
             ("  The application queries the ZXDB REST API for titles, releases, screenshots"),
             ("  and inlays, then downloads files directly from the URLs returned by that API."),
             (""),
             ("zxArt:"),
             ("  zxArt (https://zxart.ee) is a gallery and archive dedicated to ZX Spectrum"),
             ("  visual art, music, and productions."),
             (f"  API base URL : {ZXART_BASE_URL}"),
             ("  The application sends requests to the zxArt API to search productions and"),
             ("  pictures, retrieve metadata and preview images, and download productions"),
             ("  directly from the URLs returned by that API."),
             (""),
             ("Mame:"),
             ("  Mame emulator brought to you by Hulob for the ZX Spectrum Next can be installed following this documentation: https://wiki.specnext.dev/MAME:Installing"),
             ("  Official Windows Binary Packages can be found here: https://www.mamedev.org/release.html"),
             ("  Put the file tbblue.zip that can be found here: https://github.com/Threetwosevensixseven/NexCreator/raw/master/bootroms/tbblue.zip into MAME's roms folder."),
             ("  Important note: Don't extract the tbblue.zip file; MAME will look for the zip file when the 'tbblue' machine is selected."),
             (""),
             ("CSpect:"),
             ("  Mike Dailly's CSpect is a downloadable emulator for Windows, macOS, and Linux"),
             ("  Sites and links:"),
             ("  https://mdf200.itch.io/cspect"),
             ("  https://dailly.blogspot.com/"),
             ("  https://www.patreon.com/mikedailly"),
             ("  https://lemmings.info"),
             ("  https://www.instagram.com/_mikedailly"),
             (""),
             ("Legal disclaimer:"),
             ("  The author of zx-next-unite does NOT distribute any files, ROMs, games,"),
             ("  demos, graphics, music, or any other content obtained through these APIs."),
             ("  All content is served exclusively by the respective third-party services."),
             ("  This application and author do not control third-party content."),
             ("  It is the sole responsibility of the end user to ensure that any content"),
             ("  they download or use through this application complies with the applicable"),
             ("  copyright, licensing, and legal requirements in their jurisdiction."),
             ("  If in doubt, consult the terms of service of the relevant platform and"),
             ("  seek appropriate legal advice before downloading or using any content."),
             ("  For inquiries you may reach out to me on my github page: https://github.com/jclauzel/ZX-Next-Unite"),
             (""),
             ("Enjoy!"),
             ("")
            )
CONFIG_FILE_SETTINGS = (SETTING_HDDFILE, SETTING_EXPLORERPATH, SETTING_SCREENSIZE, SETTING_SOUND, SETTING_VSYNC, SETTING_HERTZ, SETTING_JOYSTICK, SETTING_MOUSE, SETTING_CSPECT, SETTING_CUSTOM, SETTING_ESC, SETTING_NEXTSYNC_EXPLORERPATH, SETTING_NEXTSYNC_SYNCONCE,
SETTING_NEXTSYNC_ALWAYSSYNC, SETTING_NEXTSYNC_SLOWTRANSFER, SETTING_DEFAULT_TAB_WHEN_OPENING, SETTING_WARN_IMAGE_NEARLY_FULL, SETTING_NO_PROMPT_ON_DELETION, SETTING_COLOR_UP_DIRECTORY, SETTING_COLOR_DIR_NAME, SETTING_COLOR_DIR_TYPE, SETTING_COLOR_FILE_NAME,
SETTING_COLOR_FILE_EXT, SETTING_COLOR_FILE_SIZE, SETTING_COLOR_GENERAL_TEXT, SETTING_DESKTOP_THEME, SETTING_IMAGE_HISTORY, SETTING_ZXDB_LAST_MODE, SETTING_ZXDB_LAST_QUERY, SETTING_CONTENT_DISCLAIMER_AGREED, SETTING_BG_OPACITY, SETTING_AVAIL_CHECK, SETTING_MULTI_SEARCH, SETTING_SEARCH_AUTOCOMPLETE, SETTING_SEARCH_SORT_MODE, SETTING_GALLERY_ANIM_MODE,
SETTING_GALLERY_ROWS_PER_PAGE, SETTING_GALLERY_COLS, SETTING_GALLERY_IMG_SIZE, SETTING_GALLERY_SLIDESHOW_SECS, SETTING_GETIT_VIEW_MODE, SETTING_ZXDB_VIEW_MODE,
SETTING_ZXART_VIEW_MODE, SETTING_ZXART_LANGUAGE, SETTING_FAVORITES, SETTING_FAVORITES_VIEW_MODE,
SETTING_ALLINONE_VIEW_MODE, SETTING_ALLINONE_PYGAME_MODE, SETTING_ALLINONE_PYGAME_ANIM, SETTING_BG_IMAGE, SETTING_CRASH_LOG_ENABLED, SETTING_MAME_COMMAND_LINE_PARAMETERS,
SETTING_DISABLE_NO_EMULATOR_TOAST, SETTING_MAME_ROM_CHOICE, SETTING_MAME_UPDATE_CHECK, SETTING_MAME_INSTALLED_TAG, SETTING_MAME_ASPECT, SETTING_MAME_SOUND, SETTING_MAME_MOUSE, SETTING_MAME_JOYSTICK, SETTING_MAME_ESC, SETTING_ALIEN_FLOYD_BG, SETTING_ALIEN_FLOYD_TAB, SETTING_ALIEN_FLOYD_HISCORE, SETTING_ALIEN_FLOYD_HISCORES,
SETTING_NEXTSYNC_SEND_CONFLICT, SETTING_NEXTSYNC_PYGAME_MODE, SETTING_NEXTSYNC_PYGAME_ANIM, SETTING_SDCARD_PYGAME_LOG, SETTING_HELP_PYGAME_LOG,
SETTING_ITCHIO_API_KEY, SETTING_SHOW_ITCHIO_TAB, SETTING_ITCHIO_VIEW_MODE, SETTING_CSPECT_UPDATE_CHECK,
SETTING_GETIT_ITEM_RETRO, SETTING_ZXDB_ITEM_RETRO, SETTING_ZXART_ITEM_RETRO, SETTING_ITCHIO_ITEM_RETRO, SETTING_FAVORITES_ITEM_RETRO)

IMAGE_BUTTONS_SIZE = 190
DISK_ARROWS_BUTTONS_SIZE = 30

CSPECT_SCREEN_SIZES = (("Screen Size X1", "-w1"),("Screen Size X2", "-w2"),("Screen Size X3", "-w3"), ("Screen Size X4", "-w4"), ("Fullscreen", "-fullscreen"))
CSPECT_SOUND = (("Sound On", ""),("Sound Off", "-sound"))
CSPECT_SCREEN_SYNC = (("VSync On", "-vsync"),("VSync Off", ""))
CSPECT_JOYSTICK = (("Joystick On", "-joystick"),("Joystick Off", ""))
# CSpect captures the mouse by default; "-mouse" *disables* mouse capture. So
# "Mouse On" (default) passes nothing and "Mouse Off" passes "-mouse".
CSPECT_MOUSE = (("Mouse On", ""),("Mouse Off", "-mouse"))
CSPECT_FREQUENCY = (("50Hz", ""),("60Hz", "-60"))
# CSpect "-esc" *disables* the ESC key from quitting the emulator. By default it
# is not passed, so ESC still exits: "Disable ESC Key Off" (index 0, default)
# passes nothing; "Disable ESC Key On" passes "-esc".
CSPECT_ESC = (("Disable ESC Key Off", ""),("Disable ESC Key On", "-esc"))
# Default CSpect command-line parameters, editable in the Settings tab ("CSpect
# default launch parameters") and persisted to hdfg.cfg (SETTING_CUSTOM). Mirrors
# MAME_DEFAULT_COMMAND_LINE: the SD Card Utility group options (screen size,
# sound, VSync, joystick, mouse, frequency, ESC) are appended on top at launch.
CSPECT_DEFAULT_LAUNCH_PARAMETERS = "-basickeys -zxnext"

FONT_GREEN = QColor(0, 255, 0)
FONT_BLUE = QColor(0, 0, 255)
FONT_RED = QColor(255, 0, 0)

MAME_ROM_CHOICE = (("tbblue"), ("specnext_ks1"), ("specnext_ks2"), ("specnext_ks3"))
MAME_EXECUTABLE_NAME = "mame"

# MAME per-launch options exposed as combo boxes in the SD Card Utility's MAME
# group, mirroring the CSpect options. Each tuple is (label, argument); the
# first entry is the first-run default. These are appended dynamically at launch
# (see launch_mame) and are NOT baked into MAME_DEFAULT_COMMAND_LINE, so the
# user can flip them from the UI without editing the raw command line. Option
# names follow the specnext MAME guide (https://wiki.specnext.dev/MAME:Installing)
# and `mame -showusage`:
#   -aspect W:H            display aspect ratio (2:1 is the crisp-pixel default)
#   -sound <method>        audio output method: wasapi, xaudio2, portaudio or
#                          none ("Sound On" leaves MAME's own default backend)
#   -mouse                 enable host mouse capture (Kempston mouse)
#   -mouse_device none     disable the mouse control (wiki's recommended default)
#   -joystick              enable joystick input
#   -joystickprovider none disable joystick detection
MAME_ASPECT = (("Aspect 2:1", "-aspect 2:1"), ("Aspect 1:1", "-aspect 1:1"))
# MAME's "-sound" selects the audio output backend (per `mame -showusage`), not a
# simple on/off. "Sound On" (the first-run default) passes nothing so MAME keeps
# its own default backend; the middle entries force a specific method; "Sound
# Off" mutes with "-sound none". wasapi/xaudio2 are Windows-only backends.
MAME_SOUND = (
    ("Sound On", ""),
    ("Sound WASAPI", "-sound wasapi"),
    ("Sound XAudio2", "-sound xaudio2"),
    ("Sound PortAudio", "-sound portaudio"),
    ("Sound Off", "-sound none"),
)
MAME_MOUSE = (("Mouse Off", "-mouse_device none"), ("Mouse On", "-mouse"))
MAME_JOYSTICK = (("Joystick On", "-joystick"), ("Joystick Off", "-joystickprovider none"))
# "-esc" disables the ESC key from quitting the emulator. Not passed by default,
# so ESC still exits: "Disable ESC Key Off" (index 0, default) passes nothing;
# "Disable ESC Key On" passes "-esc".
MAME_ESC = (("Disable ESC Key Off", ""), ("Disable ESC Key On", "-esc"))

# Options now driven by the MAME group combos above. They are stripped from any
# user-edited command-line params at launch so the combo selections are the
# single source of truth (never duplicated or in conflict). Flag options take no
# value; value options consume the following token.
MAME_COMBO_FLAG_OPTIONS = frozenset({"-mouse", "-nomouse", "-joystick", "-nojoystick", "-esc"})
MAME_COMBO_VALUE_OPTIONS = frozenset({"-aspect", "-sound", "-mouse_device", "-joystickprovider"})

def strip_mame_combo_options(tokens):
    """Remove the aspect/mouse/joystick options (and their values) from a list of
    MAME command-line tokens so the values chosen in the MAME group combos are
    authoritative. Returns a new list; the input is left unchanged."""
    result = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in MAME_COMBO_VALUE_OPTIONS:
            skip_next = True
            continue
        if token in MAME_COMBO_FLAG_OPTIONS:
            continue
        result.append(token)
    return result

# The ROM/system (e.g. "tbblue"), the aspect/mouse/joystick options (chosen via
# the combos above) and the "-hard1 <image>" arguments are NOT part of this
# default: the ROM is chosen by the user (SETTING_MAME_ROM_CHOICE), the combo
# options are appended at launch, and "-hard1 <image>" is appended dynamically
# so the image is always the last argument passed to MAME.
MAME_DEFAULT_COMMAND_LINE = "{MAME_EXECUTABLE_NAME} -ui_active -nounevenstretch -video bgfx -bgfx_screen_chains unfiltered -window -skip_gameinfo -confirm_quit"
# Previous default that hard-coded "-aspect 2:1"; a saved cfg still carrying this
# exact string is migrated to MAME_DEFAULT_COMMAND_LINE on load so the Settings
# command-line box no longer duplicates the now combo-controlled aspect option.
MAME_DEFAULT_COMMAND_LINE_LEGACY = "{MAME_EXECUTABLE_NAME} -ui_active -nounevenstretch -aspect 2:1 -video bgfx  -bgfx_screen_chains unfiltered -window -skip_gameinfo -confirm_quit"
# Appended right before the image path at launch time.
MAME_HARD_DISK_PARAMETER = "-hard1"
# Setup guide for the ZX Spectrum Next MAME support. Notably it documents the
# one manual step the auto-install can't do for the user: obtaining the TBBLUE
# "boot ROM" (e.g. boot-30204.bin) without which MAME aborts with "Required
# files are missing". Surfaced after a successful install and when a launch
# fails for that reason.
MAME_INSTALL_WIKI_URL = "https://wiki.specnext.dev/MAME:Installing"

def find_mame_executable():
    """Return the full path to the MAME executable if it can be found on the
    system PATH, otherwise None.

    On Windows the executable is typically ``mame.exe``; on Linux/macOS it is
    ``MAME_EXECUTABLE_NAME`` (``mame``). ``shutil.which`` already appends the
    platform's executable extensions, but we check the explicit ``.exe`` name
    on Windows as a fallback for robustness.
    """
    candidate = shutil.which(MAME_EXECUTABLE_NAME)
    if candidate is None and platform.system() == "Windows":
        candidate = shutil.which(MAME_EXECUTABLE_NAME + ".exe")
    return candidate


# ── MAME auto-install (Windows) ───────────────────────────────────────────
# The official MAME Windows binaries are published on GitHub as self-extracting
# 7-Zip archives named ``mame<ver>b_x64.exe`` / ``mame<ver>b_arm64.exe`` (the
# ``b`` = binaries; ``s`` is the source build). These SFX .exe files extract
# silently from the command line with ``<sfx> -o<dir> -y``, so no external 7-Zip
# or extra Python dependency is needed. We query the "latest release" endpoint,
# pick the asset matching this CPU architecture, download it, and unpack it into
# ``downloads/mame/`` (see the UI's install_mame flow and find_mame_in_downloads).
MAME_GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/mamedev/mame/releases/latest"


def mame_windows_asset_arch():
    """Return the MAME Windows binary architecture tag for this machine — either
    ``"x64"`` or ``"arm64"`` — or ``None`` when the automatic install is not
    supported here (non-Windows, or an unrecognised CPU). MAME publishes only
    64-bit Windows binaries, so 32-bit hosts return ``None``."""
    if platform.system() != "Windows":
        return None
    machine = (platform.machine() or "").lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    return None


def parse_mame_version_number(text):
    """Extract MAME's integer minor version from *text*, or ``None``.

    MAME versions increase monotonically (0.271, 0.272, 0.273 …) so the integer
    after the ``0.`` is enough to compare releases. This accepts either the
    GitHub release-tag form (``"mame0272"`` → 272) or a version string emitted by
    the binary (``"0.272"`` / ``"MAME v0.272 (mame0272)"`` → 272), so the same
    helper works for both the latest-release tag and the installed build's
    self-reported version.
    """
    if not text:
        return None
    s = str(text).lower()
    m = re.search(r"mame0*(\d+)", s)      # release-tag form, e.g. mame0272
    if not m:
        m = re.search(r"0\.(\d+)", s)     # version-string form, e.g. 0.272
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def select_mame_release_asset(release, arch):
    """Pick the Windows binaries self-extractor for *arch* out of a parsed GitHub
    "latest release" object.

    *release* is the decoded JSON dict from ``MAME_GITHUB_LATEST_RELEASE_API``
    and *arch* is a tag from :func:`mame_windows_asset_arch` (``"x64"`` /
    ``"arm64"``). MAME names the binary self-extractors ``mame<ver>b_<arch>.exe``
    (the ``b`` distinguishes them from the ``s`` source build), so match on that
    suffix. Returns ``(tag_name, asset_name, download_url, size_bytes)`` for the
    first match, or ``None`` when the release carries no build for this arch.
    """
    if not isinstance(release, dict) or not arch:
        return None
    tag = release.get("tag_name") or ""
    suffix = f"b_{arch}.exe".lower()
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url") or ""
        if url and name.lower().endswith(suffix):
            try:
                size = int(asset.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            return (tag, name, url, size)
    return None


CSPECT_EXECUTABLE_NAME = "CSpect"

def find_cspect_executable():
    """Return the path to the CSpect executable if it can be found, otherwise
    None.

    CSpect is a .NET assembly (``CSpect.exe``) that is conventionally placed in
    the same directory as zx-next-unite (see the in-app help). We therefore
    check the application directory first and then fall back to the system PATH
    so users who put CSpect on their PATH are also covered. On Linux/macOS
    CSpect is launched through mono, but the assembly file is still named
    ``CSpect.exe``.
    """
    exe_name = CSPECT_EXECUTABLE_NAME + ".exe"
    local_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    local_candidate = os.path.join(local_dir, exe_name)
    if os.path.isfile(local_candidate):
        return local_candidate
    candidate = shutil.which(CSPECT_EXECUTABLE_NAME)
    if candidate is None:
        candidate = shutil.which(exe_name)
    return candidate


def qcolor_to_hex(color: QColor) -> str:
    """Return a lowercase #rrggbb hex string for the given QColor."""
    return color.name().lower()

def hex_to_qcolor(hex_str: str) -> QColor:
    """Return a QColor from a #rrggbb hex string, falling back to white on error."""
    color = QColor(hex_str)
    return color if color.isValid() else QColor(255, 255, 255)


def normalize_sd_image_path(raw) -> str:
    """Tidy an SD-card image path for display and storage.

    Strips any surrounding quotes and leading/trailing whitespace — older
    hdfg.cfg files and the file picker used to wrap the path in double quotes,
    e.g. ``"C:/temp/next.img"`` — and, on Windows, rewrites forward slashes to
    native backslashes so a mixed value like ``"C:/temp\\next.img"`` displays as
    ``C:\\temp\\next.img``.  On Linux/macOS separators are left untouched (a
    backslash is a legal filename character there), only the quotes/whitespace
    are removed, e.g. ``/home/user/next.img``.

    Paths containing spaces are preserved verbatim: hdfmonkey is run via an argv
    list and the emulators re-quote the path themselves at launch, so no
    surrounding quotes are needed here for ``C:\\temp\\ha ha\\next.img`` to load.
    Returns ``""`` for blank/empty input."""
    if not raw:
        return ""
    s = str(raw).strip()
    # Peel off one or more layers of matched surrounding quotes ("..." or '...').
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    if platform.system() == "Windows":
        s = s.replace("/", "\\")
    return s

UP_DIRECTORY = "[Up Directory..]"
DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS = ('"', '<', '>', ':', '\\', '/', '|', '?', '*', '!', '(',')', '.', "'", '$', '@')
HDFMONKEY_EXECUTABLE = "hdfmonkey"

# Top-level downloads folder (relative to the application directory). This is
# where the manual-fallback flow asks the user to drop a hand-downloaded
# hdfmonkey archive when the automatic download from specnext.com is blocked.
DOWNLOADS_ROOT_DIRNAME = "downloads"

# Root (relative to the application directory) scanned for an itch.io CSpect
# install. itch.io downloads land in per-author/game/version sub-folders here
# (e.g. itchio/mdf200/cspect/files/CSpect3_1_4_0/), and the exact author/slug
# depends on the item — plus a user may drop a manually-downloaded build here —
# so the whole itchio tree is walked rather than one hardcoded sub-path. The
# Windows builds ship hdfmonkey.exe alongside CSpect.exe.
DOWNLOADS_CSPECT_DIRNAME = os.path.join("downloads", "itchio")

# Sub-directory (relative to the application directory) where the standalone
# hdfmonkey auto-download (HDF_MONKEY_JJJS_URL) unpacks the build for the current
# platform, mirroring the itch.io CSpect layout: downloads/hdfmonkey/<platform>/.
DOWNLOADS_HDFMONKEY_DIRNAME = os.path.join("downloads", "hdfmonkey")

# Sub-directory (relative to the application directory) where the MAME
# auto-install (see MAME_GITHUB_LATEST_RELEASE_API) unpacks the latest official
# Windows binaries. The self-extractor drops mame.exe and its support tree here.
DOWNLOADS_MAME_DIRNAME = os.path.join("downloads", "mame")


def hdfmonkey_platform_dirs():
    """Return candidate ``(platform_dirname, exe_filename)`` pairs, in priority
    order, naming the per-platform sub-folder that carries an hdfmonkey build
    runnable on the *current* platform.

    Both the itch.io CSpect package and the standalone hdfmonkey release
    (``hdfmonkey-…-jjjs.zip``) lay their builds out under the same per-platform
    folder names::

        windows-64/hdfmonkey.exe   (Windows)
        linux-musl/hdfmonkey       (Linux)
        macos-intel/hdfmonkey      (macOS, Intel)
        macos-mn/hdfmonkey         (macOS, Apple Silicon / "MN")

    The bare file name is shared across the Linux and both macOS builds, so
    callers must key off the platform folder rather than the file name. Returns
    an empty list on unrecognised platforms.
    """
    system = platform.system()
    if system == "Windows":
        exe = HDFMONKEY_EXECUTABLE + ".exe"
        return [("windows-64", exe)]
    exe = HDFMONKEY_EXECUTABLE
    if system == "Linux":
        return [("linux-musl", exe)]
    if system == "Darwin":
        machine = (platform.machine() or "").lower()
        # Prefer the build native to this Mac; fall back to the other. Apple
        # Silicon ("MN") can run the Intel binary through Rosetta 2, but an Intel
        # Mac can never run the arm64 build, so order accordingly.
        dirs = (["macos-mn", "macos-intel"]
                if machine in ("arm64", "aarch64")
                else ["macos-intel", "macos-mn"])
        return [(d, exe) for d in dirs]
    return []


def hdfmonkey_bundle_subpaths():
    """Return candidate ``(relative_subpath, exe_filename)`` pairs, in priority
    order, where the itch.io CSpect bundle ships an hdfmonkey build runnable on
    the *current* platform.

    The itch.io CSpect package carries hdfmonkey builds for every platform side
    by side under ``hdfmonkey/<platform>/`` (see ``hdfmonkey_platform_dirs`` for
    the per-platform folder names):

        hdfmonkey/windows-64/hdfmonkey.exe   (Windows)
        hdfmonkey/linux-musl/hdfmonkey       (Linux)
        hdfmonkey/macos-intel/hdfmonkey      (macOS, Intel)
        hdfmonkey/macos-mn/hdfmonkey         (macOS, Apple Silicon / "MN")

    Because the Linux and both macOS builds all share the bare file name
    ``hdfmonkey``, callers must match the full platform-specific *sub-path*
    rather than the file name — otherwise a binary built for another OS could be
    handed back. Returns an empty list on unrecognised platforms.
    """
    return [(os.path.join("hdfmonkey", d, exe), exe)
            for d, exe in hdfmonkey_platform_dirs()]


def hdfmonkey_needs_exec_bit():
    """True on Linux/macOS, where a freshly extracted itch.io hdfmonkey binary
    has no executable permission bit and must be ``chmod +x`` before it runs."""
    return platform.system() in ("Linux", "Darwin")


def hdfmonkey_chmod_instruction(hdfmonkey_path):
    """The exact shell command the user must run to make a bundled itch.io
    hdfmonkey executable. The path is quoted so spaces are handled."""
    return f'sudo chmod +x "{hdfmonkey_path}"'


def ensure_hdfmonkey_executable(hdfmonkey_path):
    """On Linux/macOS, make a bundled hdfmonkey binary executable if it isn't
    already, so it launches like a manually compiled build instead of failing
    with EACCES ("Permission denied").

    The itch.io CSpect package ships the Linux/macOS hdfmonkey without its
    executable permission bit. The binary lives under the user's own downloads
    directory, so a plain ``os.chmod`` (no sudo) is enough to add the bits.

    Returns True if the file is (now) executable, False on any failure. No-op
    that returns True on Windows; returns False for the bare ``hdfmonkey`` PATH
    default (not a real file) so callers don't assume a fix was applied."""
    if not hdfmonkey_needs_exec_bit():
        return True
    if not hdfmonkey_path or not os.path.isfile(hdfmonkey_path):
        return False
    try:
        mode = os.stat(hdfmonkey_path).st_mode
        if mode & 0o111:
            return True  # already executable
        os.chmod(hdfmonkey_path, mode | 0o111)
        return True
    except OSError:
        return False


def cspect_version_key(name):
    """Natural-order sort key for a CSpect build name so versioned names compare
    numerically rather than lexically (``CSpect3_1_4_0`` > ``CSpect3_1_3_0`` >
    ``CSpect3_0_15_2``).

    Any file extension (e.g. the ``.zip`` on an itch.io upload filename) is
    stripped first, so the key computed for an installed build folder
    (``CSpect3_1_4_0``) equals the one for its source archive
    (``CSpect3_1_4_0.zip``) — otherwise the trailing ``.zip`` token would make an
    identical version compare as *newer* and trigger a spurious update. Digit
    runs compare as ints, other runs as lower-case text."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    return [int(tok) if tok.isdigit() else tok.lower()
            for tok in re.split(r"(\d+)", stem)]


def cspect_version_newer(candidate_name, installed_name):
    """True when the CSpect build *candidate_name* is a newer version than
    *installed_name* (both compared with :func:`cspect_version_key`).

    Heterogeneous names can leave an int token opposite a str token at the same
    position, which raises ``TypeError`` on comparison; that falls back to a
    plain case-insensitive string comparison of the stems so the check never
    crashes the startup flow."""
    ck = cspect_version_key(candidate_name)
    ik = cspect_version_key(installed_name)
    try:
        return ck > ik
    except TypeError:
        a = os.path.splitext(os.path.basename(candidate_name or ""))[0].lower()
        b = os.path.splitext(os.path.basename(installed_name or ""))[0].lower()
        return a > b


def find_installed_cspect_version(base_dir):
    """Return ``(version_name, cspect_exe_path)`` for the newest CSpect build
    installed under ``<base_dir>/downloads/itchio``, or ``(None, None)`` when no
    itch.io CSpect install is present.

    The version is taken from the executable's parent folder name (e.g.
    ``CSpect3_1_4_0``), which is exactly how the itch.io archive extracts (see
    ``zxnu_itchio.extract_zip``: ``files/CSpect3_1_4_0.zip`` →
    ``files/CSpect3_1_4_0/CSpect.exe``). Used by the startup update check to know
    which build to compare against the latest itch.io upload. The recursive walk
    can be slow, so callers run it on a background thread."""
    search_root = os.path.join(base_dir, DOWNLOADS_CSPECT_DIRNAME)
    if not os.path.isdir(search_root):
        return (None, None)
    target = (CSPECT_EXECUTABLE_NAME + ".exe").lower()
    best_name = None
    best_path = None
    for dirpath, _dirnames, filenames in os.walk(search_root):
        for filename in filenames:
            if filename.lower() != target:
                continue
            folder = os.path.basename(dirpath)
            if best_name is None or cspect_version_newer(folder, best_name):
                best_name = folder
                best_path = os.path.join(dirpath, filename)
    return (best_name, best_path)


def find_emulators_in_downloads(base_dir, scan_for_cspect=True, scan_for_hdfmonkey=True):
    """Recursively search ``<base_dir>/downloads/itchio`` for a CSpect.exe and a
    bundled hdfmonkey executable built for the current platform.

    Returns ``(cspect_path, hdfmonkey_path)`` where each element is the full path
    to the matching executable found, or ``None`` if not found / not searched
    for. This is the fallback used when neither tool is present in the
    application directory or on PATH — itch.io CSpect installs land under
    ``downloads/itchio`` in a per-author/game/version sub-folder (and a manually
    downloaded build dropped anywhere under that tree is picked up too).

    When several CSpect builds are installed side by side (e.g. an earlier
    ``CSpect3_1_3_0`` left in place next to a freshly updated ``CSpect3_1_4_0``),
    the *newest* one is returned — chosen by the version encoded in each
    executable's parent folder name (see ``cspect_version_key``) — so the app
    always launches the latest available build.

    The hdfmonkey search now covers Windows, Linux and macOS (Intel + Apple
    Silicon): the itch.io CSpect package ships an hdfmonkey build for each of
    these under ``hdfmonkey/<platform>/`` (see ``hdfmonkey_bundle_subpaths``).
    The walk is potentially slow (many files), so callers run it on a background
    thread.
    """
    search_root = os.path.join(base_dir, DOWNLOADS_CSPECT_DIRNAME)
    if not os.path.isdir(search_root):
        return (None, None)

    want_cspect = bool(scan_for_cspect)
    want_hdfmonkey = bool(scan_for_hdfmonkey)

    cspect_target = (CSPECT_EXECUTABLE_NAME + ".exe").lower()
    # Platform-specific bundle locations for hdfmonkey, in priority order. We
    # match the full sub-path (e.g. .../hdfmonkey/linux-musl/hdfmonkey) rather
    # than the bare file name: on Linux/macOS the bundle ships several same-named
    # "hdfmonkey" binaries (one per platform) and only the one under the matching
    # platform folder can actually run here.
    hdf_candidates = hdfmonkey_bundle_subpaths() if want_hdfmonkey else []
    want_hdfmonkey = want_hdfmonkey and bool(hdf_candidates)
    # (path-suffix, priority rank) pairs; a leading separator anchors the match
    # to whole path components.
    hdf_suffixes = [(os.sep + sp.lower(), rank)
                    for rank, (sp, _exe) in enumerate(hdf_candidates)]

    cspect_path = None
    cspect_best_name = None  # parent-folder name of the best CSpect match so far
    hdfmonkey_path = None
    hdf_best_rank = None  # priority rank of the best hdfmonkey match so far
    for dirpath, _dirnames, filenames in os.walk(search_root):
        for filename in filenames:
            low = filename.lower()
            if want_cspect and low == cspect_target:
                # Keep the newest build when multiple versions coexist: compare
                # the version encoded in each exe's parent folder name so a
                # freshly-updated CSpect3_1_4_0 wins over a lingering
                # CSpect3_1_3_0, regardless of os.walk() visit order.
                folder = os.path.basename(dirpath)
                if cspect_best_name is None or \
                   cspect_version_newer(folder, cspect_best_name):
                    cspect_path = os.path.join(dirpath, filename)
                    cspect_best_name = folder
                continue
            # Keep looking for hdfmonkey until the top-priority (rank 0) build is
            # found, so a native build wins over a lower-priority fallback.
            if want_hdfmonkey and (hdf_best_rank is None or hdf_best_rank > 0):
                full = os.path.join(dirpath, filename)
                full_low = full.lower()
                for suffix, rank in hdf_suffixes:
                    if full_low.endswith(suffix) and \
                       (hdf_best_rank is None or rank < hdf_best_rank):
                        hdfmonkey_path = full
                        hdf_best_rank = rank
                        break
        # When scanning for CSpect we can't stop at the first hit — a newer build
        # may sit in a later-visited folder — so only the hdfmonkey search can
        # short-circuit the walk (and only when CSpect isn't wanted).
        cspect_done = not want_cspect
        hdf_done = (not want_hdfmonkey) or hdf_best_rank == 0
        if cspect_done and hdf_done:
            break  # everything we were asked to find has been located

    # Tie hdfmonkey to the *newest* CSpect we selected. Each itch.io CSpect build
    # ships its own hdfmonkey under <build>/hdfmonkey/<platform>/, so the generic
    # walk above can hand back an older lingering build's copy. When we have a
    # winning CSpect, prefer the hdfmonkey bundled with it so updating CSpect also
    # switches to that build's hdfmonkey. Fall back to the walk result only when
    # the newest build ships none.
    if want_hdfmonkey and cspect_path:
        near = find_hdfmonkey_near_cspect(cspect_path)
        if near:
            hdfmonkey_path = near
    return (cspect_path, hdfmonkey_path)


def find_hdfmonkey_near_cspect(cspect_path):
    """Locate an hdfmonkey executable shipped alongside a manually installed
    CSpect, for the current platform.

    CSpect distributions bundle hdfmonkey under
    ``hdfmonkey/<platform>/hdfmonkey[.exe]`` next to the CSpect executable (and a
    copy is sometimes placed directly beside it). So when CSpect was found on
    PATH or in the application directory but hdfmonkey is otherwise missing, this
    picks up that bundled copy without needing the itch.io downloads scan. The
    platform-specific sub-folders (windows-64 / linux-musl / macos-intel /
    macos-mn) come from ``hdfmonkey_bundle_subpaths``.

    Returns the full path to the executable, or ``None`` when ``cspect_path`` is
    falsy or when no copy is found for this platform.
    """
    if not cspect_path:
        return None
    cspect_dir = os.path.dirname(os.path.abspath(cspect_path))
    # Platform-specific bundle locations first (in priority order)...
    candidates = [os.path.join(cspect_dir, subpath)
                  for subpath, _exe in hdfmonkey_bundle_subpaths()]
    # ...then the binary dropped directly beside CSpect (some manual installs).
    exe = HDFMONKEY_EXECUTABLE + (".exe" if platform.system() == "Windows" else "")
    candidates.append(os.path.join(cspect_dir, exe))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def find_hdfmonkey_in_downloads(base_dir):
    """Locate an hdfmonkey executable installed by the standalone auto-download
    (HDF_MONKEY_JJJS_URL) under ``<base_dir>/downloads/hdfmonkey/<platform>/``.

    This is the persistence hook for the "install hdfmonkey only" button: the
    extracted binary lands in a per-platform sub-folder there (see
    ``extract_hdfmonkey_from_jjjs_zip``), so a later launch re-discovers it the
    same cheap way ``find_hdfmonkey_near_cspect`` picks up a CSpect-bundled copy.

    Returns the full path to the first matching build for this platform, or
    ``None`` when none is present.
    """
    if not base_dir:
        return None
    root = os.path.join(base_dir, DOWNLOADS_HDFMONKEY_DIRNAME)
    for plat_dir, exe in hdfmonkey_platform_dirs():
        candidate = os.path.join(root, plat_dir, exe)
        if os.path.isfile(candidate):
            return candidate
    return None


def _looks_like_hdfmonkey_jjjs_zip(outer_zip):
    """Return True when an already-opened ``zipfile.ZipFile`` has the shape of the
    jjjs hdfmonkey archive: an outer zip carrying a nested ``.zip`` member, plus
    either the ``password.txt`` that unlocks it or an entry name hinting at
    hdfmonkey / jjjs. Keeps the manual-drop scan from mistaking an unrelated zip
    in the downloads folder (an itch.io payload, say) for the hdfmonkey build."""
    try:
        names = [n.lower() for n in outer_zip.namelist()]
    except Exception:
        return False
    if not any(n.endswith(".zip") for n in names):
        return False
    if any(os.path.basename(n) == "password.txt" for n in names):
        return True
    return any(("hdfm" in n or "jjjs" in n) for n in names)


def find_hdfmonkey_jjjs_zip_in_downloads(base_dir):
    """Locate a jjjs hdfmonkey archive the user manually downloaded and dropped
    into the ``downloads`` folder.

    When the in-app auto-download from ``HDF_MONKEY_JJJS_URL`` is blocked (e.g.
    specnext.com serves a login or anti-robot page instead of the attachment),
    the app invites the user to fetch the zip in a browser and drop it into
    ``<base_dir>/downloads/``. This scans that folder (and the per-platform
    ``downloads/hdfmonkey`` sub-folder) for a zip whose shape matches the jjjs
    archive — see :func:`_looks_like_hdfmonkey_jjjs_zip` — so
    :func:`extract_hdfmonkey_from_jjjs_zip` can then unpack it.

    Returns the full path to the first matching archive, or ``None`` when none is
    found.
    """
    if not base_dir:
        return None
    search_dirs = [
        os.path.join(base_dir, DOWNLOADS_ROOT_DIRNAME),
        os.path.join(base_dir, DOWNLOADS_HDFMONKEY_DIRNAME),
    ]
    seen = set()
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for name in entries:
            if not name.lower().endswith(".zip"):
                continue
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            try:
                if not zipfile.is_zipfile(path):
                    continue
                with zipfile.ZipFile(path) as outer:
                    if _looks_like_hdfmonkey_jjjs_zip(outer):
                        return path
            except (OSError, zipfile.BadZipFile):
                continue
    return None


def find_mame_in_downloads(base_dir):
    """Locate a MAME executable installed by the in-app auto-install under
    ``<base_dir>/downloads/mame/``.

    This is the persistence hook for the "Install MAME" button: the
    self-extractor drops ``mame.exe`` at the top of that folder (see the UI's
    install_mame flow), so a later launch re-discovers it cheaply — the same way
    :func:`find_hdfmonkey_in_downloads` re-adopts a standalone hdfmonkey build.

    The direct ``downloads/mame/mame.exe`` hit is checked first; a bounded walk
    is the fallback in case a future layout nests it in a sub-folder. Returns the
    full path to the executable, or ``None`` when none is present.
    """
    if not base_dir:
        return None
    root = os.path.join(base_dir, DOWNLOADS_MAME_DIRNAME)
    if not os.path.isdir(root):
        return None
    exe = MAME_EXECUTABLE_NAME + ".exe" if platform.system() == "Windows" else MAME_EXECUTABLE_NAME
    direct = os.path.join(root, exe)
    if os.path.isfile(direct):
        return direct
    for dirpath, _dirnames, filenames in os.walk(root):
        if exe in filenames:
            return os.path.join(dirpath, exe)
    return None


def extract_hdfmonkey_from_jjjs_zip(outer_zip_path, dest_root,
                                    password=HDF_MONKEY_JJJS_ZIP_PASSWORD):
    """Extract the hdfmonkey build for the *current* platform out of a downloaded
    jjjs-packaged archive and return the full path to the installed executable.

    The archive downloaded from ``HDF_MONKEY_JJJS_URL`` is a zip-inside-a-zip:

        hdfmonkey-…-jjjs.zip            (outer, unencrypted)
          ├── hdfm-…-jjjs.zip           (inner, ZipCrypto-encrypted)
          │     ├── windows-64/hdfmonkey.exe
          │     ├── linux-musl/hdfmonkey
          │     ├── macos-intel/hdfmonkey
          │     └── macos-mn/hdfmonkey
          └── password.txt              ("jjjs")

    Only the single binary matching this platform is written, to
    ``<dest_root>/<platform>/hdfmonkey[.exe]``. The password bundled in
    ``password.txt`` is preferred over *password* so a future re-pack that
    changes it keeps working; ZipCrypto (not AES) means Python's stdlib
    ``zipfile`` can decrypt it with no extra dependency. On Linux/macOS the
    freshly written binary is made executable.

    Raises ``RuntimeError`` when the platform is unsupported or the expected
    build is absent; propagates ``zipfile``/OS errors for the caller to report.
    """
    plat_dirs = hdfmonkey_platform_dirs()
    if not plat_dirs:
        raise RuntimeError(
            f"No hdfmonkey build is available for this platform "
            f"({platform.system()}).")

    with zipfile.ZipFile(outer_zip_path) as outer:
        outer_names = outer.namelist()
        # Prefer the password shipped alongside the inner zip; fall back to the
        # documented default if it is missing or empty.
        pwd = password
        if "password.txt" in outer_names:
            try:
                shipped = outer.read("password.txt").decode("utf-8", "replace").strip()
                if shipped:
                    pwd = shipped.encode("utf-8")
            except Exception:
                pass  # keep the default password
        # The inner archive is the (only) nested .zip entry.
        inner_name = next(
            (n for n in outer_names if n.lower().endswith(".zip")), None)
        if inner_name is None:
            raise RuntimeError(
                "Downloaded hdfmonkey archive does not contain the expected "
                "inner zip.")
        try:
            inner_bytes = outer.read(inner_name, pwd=pwd)
        except RuntimeError:
            # A future re-pack might drop the encryption; retry without a
            # password rather than failing outright.
            inner_bytes = outer.read(inner_name)

    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
        inner_names = set(inner.namelist())
        for plat_dir, exe in plat_dirs:
            # Zip entries always use forward slashes regardless of host OS.
            member = f"{plat_dir}/{exe}"
            if member not in inner_names:
                continue
            target_dir = os.path.join(dest_root, plat_dir)
            os.makedirs(target_dir, exist_ok=True)
            dest = os.path.join(target_dir, exe)
            with inner.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            # Linux/macOS builds ship without the executable bit; add it so the
            # binary launches straight away (it lives in the user's own
            # downloads dir, so no elevation is needed).
            ensure_hdfmonkey_executable(dest)
            return dest

    raise RuntimeError(
        f"Downloaded hdfmonkey archive has no build for this platform "
        f"({platform.system()} {platform.machine()}).")


FILTER_LABEL_TEXT = "Filter: "
FILTER_TEXT_WIDTH = 320
_zxart_current_language: str = DEFAULT_ZXART_LANGUAGE


def _zxart_lang() -> str:
    """Return the currently-selected zxART API language code."""
    lang = (_zxart_current_language or DEFAULT_ZXART_LANGUAGE).strip().lower()
    if lang not in ("eng", "pol", "spa"):
        lang = DEFAULT_ZXART_LANGUAGE
    return lang


def _zxart_set_language(code: str) -> None:
    """Update the global zxART API language."""
    global _zxart_current_language
    code = (code or DEFAULT_ZXART_LANGUAGE).strip().lower()
    if code not in ("eng", "pol", "spa"):
        code = DEFAULT_ZXART_LANGUAGE
    _zxart_current_language = code


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.
__all__ = [_n for _n in dir() if not _n.startswith('__')]
