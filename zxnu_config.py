"""Shared constants, data tables and pure helpers for zx-next-unite.

Extracted from zx-next-unite.py to reduce the size of the main module.
Contains no GUI/window logic — only configuration constants, lookup
tables, small pure helpers and the zxArt language state."""

import os
import platform
import shutil
import subprocess
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


ZX_NEXT_UNITE_VERSION = "8.2"
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
SETTING_MAME_COMMAND_LINE_PARAMETERS = "mame_command_line_parameters"
SETTING_MAME_ROM_CHOICE              = "mame_rom_choice"            # MAME system/ROM, e.g. "tbblue" (default)
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
SETTING_ITCHIO_API_KEY         = "itchio_api_key"          # str: personal itch.io API key (https://itch.io/user/settings/api-keys)
SETTING_SHOW_ITCHIO_TAB        = "show_itchio_tab"         # "false" => hide the itch.io tab (default shown when itch-dl is installed)
SETTING_ITCHIO_VIEW_MODE       = "itchio_view_mode"        # "gallery" (default) or "table"
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

def subprocess_no_window_kwargs(extra_flags=0):
    """Return subprocess kwargs that prevent a console window from flashing.

    On Windows, command-line tools such as hdfmonkey would otherwise briefly
    pop up a console window every time they run (e.g. when deleting or sending
    a file). Passing CREATE_NO_WINDOW plus a STARTUPINFO with a hidden window
    keeps them invisible. On other platforms this is a no-op. ``extra_flags``
    lets callers OR in additional Windows creation flags (e.g.
    CREATE_NEW_PROCESS_GROUP).
    """
    if platform.system() != "Windows":
        return {}
    CREATE_NO_WINDOW = 0x08000000
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": CREATE_NO_WINDOW | extra_flags,
        "startupinfo": startupinfo,
    }
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
             ("Pyside6 is not bundled and needs to be installed separately (see installation instructions)."),
             (""),
             ("zx-next-unite also uses pygame-ce (the community edition of pygame) for its animated backgrounds and visualizations (e.g. the 'Alien Floyd's' effects). Many thanks to the pygame and pygame-ce communities - see https://pyga.me and https://www.pygame.org."),
             (""),
             ("pygame-ce is distributed under the GNU LGPL v2.1 license and, like Pyside6, is not bundled and needs to be installed separately (see installation instructions)."),
             (""),
             ("zx-next-unite optionally uses itch-dl by Dragoon Aethis to power the itch.io tab (browsing and installing your itch.io collections). Many thanks to its author - see https://github.com/DragoonAethis/itch-dl."),
             (""),
             ("itch-dl is distributed under the MIT license (Copyright (c) 2022 Dragoon Aethis) and, like Pyside6 and pygame-ce, is not bundled and needs to be installed separately (see installation instructions). The itch.io tab is only shown when itch-dl is installed."),
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
CONFIG_FILE_SETTINGS = (SETTING_HDDFILE, SETTING_EXPLORERPATH, SETTING_SCREENSIZE, SETTING_SOUND, SETTING_VSYNC, SETTING_HERTZ, SETTING_JOYSTICK, SETTING_CSPECT, SETTING_CUSTOM, SETTING_ESC, SETTING_NEXTSYNC_EXPLORERPATH, SETTING_NEXTSYNC_SYNCONCE,
SETTING_NEXTSYNC_ALWAYSSYNC, SETTING_NEXTSYNC_SLOWTRANSFER, SETTING_DEFAULT_TAB_WHEN_OPENING, SETTING_WARN_IMAGE_NEARLY_FULL, SETTING_NO_PROMPT_ON_DELETION, SETTING_COLOR_UP_DIRECTORY, SETTING_COLOR_DIR_NAME, SETTING_COLOR_DIR_TYPE, SETTING_COLOR_FILE_NAME,
SETTING_COLOR_FILE_EXT, SETTING_COLOR_FILE_SIZE, SETTING_DESKTOP_THEME, SETTING_IMAGE_HISTORY, SETTING_ZXDB_LAST_MODE, SETTING_ZXDB_LAST_QUERY, SETTING_CONTENT_DISCLAIMER_AGREED, SETTING_BG_OPACITY, SETTING_AVAIL_CHECK, SETTING_MULTI_SEARCH, SETTING_SEARCH_AUTOCOMPLETE, SETTING_SEARCH_SORT_MODE, SETTING_GALLERY_ANIM_MODE,
SETTING_GALLERY_ROWS_PER_PAGE, SETTING_GALLERY_COLS, SETTING_GALLERY_IMG_SIZE, SETTING_GALLERY_SLIDESHOW_SECS, SETTING_GETIT_VIEW_MODE, SETTING_ZXDB_VIEW_MODE,
SETTING_ZXART_VIEW_MODE, SETTING_ZXART_LANGUAGE, SETTING_FAVORITES, SETTING_FAVORITES_VIEW_MODE,
SETTING_ALLINONE_VIEW_MODE, SETTING_ALLINONE_PYGAME_MODE, SETTING_ALLINONE_PYGAME_ANIM, SETTING_BG_IMAGE, SETTING_CRASH_LOG_ENABLED, SETTING_MAME_COMMAND_LINE_PARAMETERS,
SETTING_DISABLE_NO_EMULATOR_TOAST, SETTING_MAME_ROM_CHOICE, SETTING_ALIEN_FLOYD_BG, SETTING_ALIEN_FLOYD_TAB, SETTING_ALIEN_FLOYD_HISCORE, SETTING_ALIEN_FLOYD_HISCORES,
SETTING_NEXTSYNC_SEND_CONFLICT, SETTING_NEXTSYNC_PYGAME_MODE, SETTING_NEXTSYNC_PYGAME_ANIM, SETTING_SDCARD_PYGAME_LOG,
SETTING_ITCHIO_API_KEY, SETTING_SHOW_ITCHIO_TAB, SETTING_ITCHIO_VIEW_MODE,
SETTING_GETIT_ITEM_RETRO, SETTING_ZXDB_ITEM_RETRO, SETTING_ZXART_ITEM_RETRO, SETTING_ITCHIO_ITEM_RETRO, SETTING_FAVORITES_ITEM_RETRO)

IMAGE_BUTTONS_SIZE = 190
DISK_ARROWS_BUTTONS_SIZE = 30

CSPECT_SCREEN_SIZES = (("Screen Size X1", "-w1"),("Screen Size X2", "-w2"),("Screen Size X3", "-w3"), ("Screen Size X4", "-w4"), ("Fullscreen", "-fullscreen"))
CSPECT_SOUND = (("Sound On", ""),("Sound Off", "-sound"))
CSPECT_SCREEN_SYNC = (("VSync On", "-vsync"),("VSync Off", ""))
CSPECT_JOYSTICK = (("Joystick On", "-joystick"),("Joystick Off", ""))
CSPECT_FREQUENCY = (("50Hz", ""),("60Hz", "-60"))
CSPECT_BASE_ARGUMENTS = "-basickeys -zxnext -nextrom"

FONT_GREEN = QColor(0, 255, 0)
FONT_BLUE = QColor(0, 0, 255)
FONT_RED = QColor(255, 0, 0)

MAME_ROM_CHOICE = (("tbblue"), ("specnext_ks1"), ("specnext_ks2"), ("specnext_ks3"))
MAME_EXECUTABLE_NAME = "mame"
# The ROM/system (e.g. "tbblue") and the "-hard1 <image>" arguments are NOT part
# of this default: the ROM is chosen by the user (SETTING_MAME_ROM_CHOICE) and
# "-hard1 <image>" is appended dynamically at launch so the image is always the
# last argument passed to MAME.
MAME_DEFAULT_COMMAND_LINE = "{MAME_EXECUTABLE_NAME} -ui_active -nounevenstretch -aspect 2:1 -video bgfx  -bgfx_screen_chains unfiltered -window -skip_gameinfo -confirm_quit"
# Appended right before the image path at launch time.
MAME_HARD_DISK_PARAMETER = "-hard1"

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

UP_DIRECTORY = "[Up Directory..]"
DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS = ('"', '<', '>', ':', '\\', '/', '|', '?', '*', '!', '(',')', '.', "'", '$', '@')
HDFMONKEY_EXECUTABLE = "hdfmonkey"

# Sub-directory (relative to the application directory) where itch.io CSpect
# installs are downloaded by the itch.io tab. Several CSpect versions may live
# here, each in its own sub-folder, and the Windows builds ship hdfmonkey.exe
# alongside CSpect.exe.
DOWNLOADS_CSPECT_DIRNAME = os.path.join("downloads", "itchio", "mdf200", "cspect")


def hdfmonkey_bundle_subpaths():
    """Return candidate ``(relative_subpath, exe_filename)`` pairs, in priority
    order, where the itch.io CSpect bundle ships an hdfmonkey build runnable on
    the *current* platform.

    The itch.io CSpect package carries hdfmonkey builds for every platform side
    by side under ``hdfmonkey/<platform>/``:

        hdfmonkey/windows-64/hdfmonkey.exe   (Windows)
        hdfmonkey/linux-musl/hdfmonkey       (Linux)
        hdfmonkey/macos-intel/hdfmonkey      (macOS, Intel)
        hdfmonkey/macos-mn/hdfmonkey         (macOS, Apple Silicon / "MN")

    Because the Linux and both macOS builds all share the bare file name
    ``hdfmonkey``, callers must match the full platform-specific *sub-path*
    rather than the file name — otherwise a binary built for another OS could be
    handed back. Returns an empty list on unrecognised platforms.
    """
    system = platform.system()
    if system == "Windows":
        exe = HDFMONKEY_EXECUTABLE + ".exe"
        return [(os.path.join("hdfmonkey", "windows-64", exe), exe)]
    exe = HDFMONKEY_EXECUTABLE
    if system == "Linux":
        return [(os.path.join("hdfmonkey", "linux-musl", exe), exe)]
    if system == "Darwin":
        machine = (platform.machine() or "").lower()
        # Prefer the build native to this Mac; fall back to the other. Apple
        # Silicon ("MN") can run the Intel binary through Rosetta 2, but an Intel
        # Mac can never run the arm64 build, so order accordingly.
        dirs = (["macos-mn", "macos-intel"]
                if machine in ("arm64", "aarch64")
                else ["macos-intel", "macos-mn"])
        return [(os.path.join("hdfmonkey", d, exe), exe) for d in dirs]
    return []


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


def find_emulators_in_downloads(base_dir, scan_for_cspect=True, scan_for_hdfmonkey=True):
    """Recursively search ``<base_dir>/downloads/cspect`` for a CSpect.exe and a
    bundled hdfmonkey executable built for the current platform.

    Returns ``(cspect_path, hdfmonkey_path)`` where each element is the full path
    to the first matching executable found, or ``None`` if not found / not
    searched for. This is the fallback used when neither tool is present in the
    application directory or on PATH — itch.io CSpect installs land under
    ``downloads/cspect`` (optionally in a per-version sub-folder).

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
    hdfmonkey_path = None
    hdf_best_rank = None  # priority rank of the best hdfmonkey match so far
    for dirpath, _dirnames, filenames in os.walk(search_root):
        for filename in filenames:
            low = filename.lower()
            if want_cspect and cspect_path is None and low == cspect_target:
                cspect_path = os.path.join(dirpath, filename)
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
        cspect_done = (not want_cspect) or cspect_path is not None
        hdf_done = (not want_hdfmonkey) or hdf_best_rank == 0
        if cspect_done and hdf_done:
            break  # everything we were asked to find has been located
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
