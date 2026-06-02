"""Image / screen / file-format helpers for zx-next-unite.

Includes the ZX Spectrum SCREEN$ decoder, placeholder-pixmap rendering,
gallery tag extraction and format-detection helpers. Extracted from
zx-next-unite.py."""

import os
import threading
import urllib.parse

from zxnu_config import *
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap


# ---------------------------------------------------------------------------
# Gallery tag extraction (zxArt / ZXDB / GetIt agnostic helper)
# ---------------------------------------------------------------------------
#
# zxART exposes a small set of short tag-like descriptors on each gallery
# thumbnail (visible on https://zxart.ee/eng/mainpage/) such as:
#   "GS"     – General Sound (sound hardware)
#   "AY/YM"  – AY-3-8910 / YM2149 sound chip
#   "Tape"   – tape-based release
#   "TR-DOS" – TR-DOS (5.25"/3.5" disc)
#   "48"     – Spectrum 48K
#   "128"    – Spectrum 128K / +2
#   "Pent"   – Pentagon / Pent128
#   "KS8"    – Kondratiev Sprinter / KS8
#   "TS"     – ZX-Evolution TS-Conf basic
#   "TS-Conf"– TS-Conf advanced
# The API does not return these as a single field; we derive them from
# release formats, hardware requirements, and prod-level flags.

# Base map: every extension from ZXFMT_PLACEHOLDER_LABELS gets its own label
# so the overlay always shows something for any known format.  The grouped
# overrides below replace generic labels with friendlier cluster names (e.g.
# several tape formats all collapse to "Tape").
_ZXART_RELEASE_FORMAT_TAGS: dict = {}

# Populated lazily once ZXFMT_PLACEHOLDER_LABELS is defined (see below).
def _build_release_format_tags() -> dict:
    d = {ext: label for ext, label in ZXFMT_PLACEHOLDER_LABELS}
    # Grouped / friendlier overrides
    for _ext in ("tap", "tzx", "pzx", "cdt", "csw", "voc", "wav", "mp3", "ogg", "flac"):
        if _ext in d:
            d[_ext] = "Tape"
    for _ext in ("trd", "scl", "fdi", "td0", "opd", "opu"):
        if _ext in d:
            d[_ext] = "TR-DOS"
    for _ext in ("dsk", "mgt"):
        if _ext in d:
            d[_ext] = "+3 Disk"
    for _ext in ("img", "hdf"):
        if _ext in d:
            d[_ext] = "HDD"
    for _ext in ("sna", "z80", "szx", "sp", "zx", "slt"):
        if _ext in d:
            d[_ext] = "Snapshot"
    for _ext in ("zip", "7z", "rar", "tar", "gz", "xz"):
        if _ext in d:
            d[_ext] = "Archive"
    for _ext in ("bas", "asm", "z80s", "c", "h", "pas"):
        if _ext in d:
            d[_ext] = "Source"
    for _ext in ("pdf", "txt", "doc", "docx", "rtf", "htm", "html", "nfo", "diz", "md"):
        if _ext in d:
            d[_ext] = "Docs"
    return d

_ZXART_HARDWARE_TAGS = {
    # Sound / extension hardware
    "generalsound":   "GS",
    "gs":             "GS",
    "ay":             "AY/YM",
    "ay-3-8912":      "AY/YM",
    "ym":             "AY/YM",
    "beeper":         "Beeper",
    "covox":          "Covox",
    "soundrive":      "Soundrive",
    # Storage / disc systems
    "trdos":          "TR-DOS",
    "tr-dos":         "TR-DOS",
    "dosbase":        "TR-DOS",
    # CPU / machine class (zxArt uses zx48/zx128/zx, etc.)
    "zx":             "48",
    "zx48":           "48",
    "zxspectrum48":   "48",
    "zx128":          "128",
    "zxspectrum128":  "128",
    "pentagon":       "Pent",
    "pent":           "Pent",
    "pent128":        "Pent128",
    "pentagon128":    "Pent128",
    "pent512":        "Pent512",
    "pentagon512":    "Pent512",
    "pent1024":       "Pent1024",
    "pentagon1024":   "Pent1024",
    "scorpion":       "Scorpion",
    "scorpion256":    "Scorpion256",
    "atm":            "ATM",
    "atm2":           "ATM2",
    "atm3":           "ATM3",
    "ts-conf":        "TS-Conf",
    "tsconf":         "TS-Conf",
    "evolution":      "TS",
    "zxevolution":    "TS",
    "ks8":            "KS8",
    "sprinter":       "KS8",
    "zxnext":         "Next",
    "next":           "Next",
}


def _zxart_tag_for_format(fmt: str) -> str:
    if not fmt:
        return ""
    f = str(fmt).strip().lower().lstrip(".")
    return _ZXART_RELEASE_FORMAT_TAGS.get(f, "")


def _zxart_tag_for_hardware(name) -> str:
    if not name:
        return ""
    n = str(name).strip().lower().replace(" ", "").replace("_", "")
    return _ZXART_HARDWARE_TAGS.get(n, "")


def _gallery_extract_tags(entry: dict) -> list:
    """Return a de-duplicated list of short overlay tags for a gallery entry.

    For zxArt entries we look at the production's release formats and
    hardware-required list. For pictures we surface the picture type (e.g.
    "standard", "border", "multicolor"). For non-zxArt entries we fall back
    to the entry's `machine` and `genre` keys when they look tag-like.
    """
    if not isinstance(entry, dict):
        return []

    tags = []
    seen = set()

    def _add(t):
        t = (t or "").strip()
        if not t:
            return
        k = t.lower()
        if k in seen:
            return
        seen.add(k)
        tags.append(t)

    src  = entry.get("_source") or {}
    kind = (entry.get("_kind") or "").lower()

    if kind == "zxart_picture":
        ptype = src.get("type") or ""
        if ptype:
            _add(str(ptype))
        for t in (src.get("tags") or [])[:3]:
            _add(str(t))
    else:
        # Prefer release-level info (most accurate); fall back to prod fields.
        for rel in (src.get("releases") or []):
            if not isinstance(rel, dict):
                continue
            rf = rel.get("releaseFormat")
            if isinstance(rf, list):
                for f in rf:
                    _add(_zxart_tag_for_format(f))
            else:
                _add(_zxart_tag_for_format(rf or ""))
            for hw in (rel.get("hardwareRequired") or []):
                _add(_zxart_tag_for_hardware(hw))
        for fmt in (src.get("releaseFormats") or []):
            _add(_zxart_tag_for_format(fmt))
        for hw in (src.get("hardwareRequired") or []):
            _add(_zxart_tag_for_hardware(hw))
        # Some prod payloads expose machine names in plain text.
        machine = entry.get("machine") or ""
        if machine:
            for piece in str(machine).replace(",", " ").split():
                _add(_zxart_tag_for_hardware(piece))

    # Cap the badge count so the overlay stays readable.
    return tags[:5]


# ---------------------------------------------------------------------------
# ZX Spectrum file-format helpers (used for gallery placeholder thumbnails
# when a content entry has no actual picture available).  Reference list:
#   https://worldofspectrum.org/faq/reference/formats.htm
# ---------------------------------------------------------------------------

# Recognised image-bearing extensions (real screenshots/cover scans).
ZXFMT_IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".scr",                 # Spectrum native screen$
    ".mlt",                 # MULTITECH ULAplus 8x1 attribute screen
    ".ifl", ".bsc", ".mc",  # 8x1, BiFROST, multicolour screens
    ".nxi",                 # ZX Spectrum Next layer-2 raw screen
    ".slr", ".sli",         # SamRam Spectrum / scanline images
    ".chr$",                # Char-mapped images
)

# Format -> short tag shown in big letters in the placeholder thumbnail.
# Keep ordering meaningful (specific before generic).  Bucketed by category.
ZXFMT_PLACEHOLDER_LABELS = (
    # Snapshots
    ("sna",  "SNA"),
    ("z80",  "Z80"),
    ("szx",  "SZX"),
    ("sp",   "SP"),
    ("zx",   "ZX"),
    ("slt",  "SLT"),
    # Tape images
    ("tap",  "TAP"),
    ("tzx",  "TZX"),
    ("pzx",  "PZX"),
    ("cdt",  "CDT"),
    ("csw",  "CSW"),
    ("voc",  "VOC"),
    ("wav",  "WAV"),
    ("mp3",  "MP3"),
    ("ogg",  "OGG"),
    ("flac", "FLAC"),
    # Disk images
    ("dsk",  "DSK"),
    ("trd",  "TRD"),
    ("scl",  "SCL"),
    ("fdi",  "FDI"),
    ("td0",  "TD0"),
    ("img",  "IMG"),
    ("mgt",  "MGT"),
    ("opd",  "OPD"),
    ("opu",  "OPU"),
    ("hdf",  "HDF"),
    ("$b",   "$B"),
    ("$c",   "$C"),
    # Cartridges / ROM
    ("rom",  "ROM"),
    ("dck",  "DCK"),
    # Pokes / cheats
    ("pok",  "POK"),
    # Source / code
    ("bas",  "BAS"),
    ("asm",  "ASM"),
    ("z80s", "ASM"),
    ("c",    "C"),
    ("h",    "H"),
    ("pas",  "PAS"),
    # Documents
    ("pdf",  "PDF"),
    ("txt",  "TXT"),
    ("doc",  "DOC"),
    ("docx", "DOCX"),
    ("rtf",  "RTF"),
    ("htm",  "HTM"),
    ("html", "HTML"),
    ("nfo",  "NFO"),
    ("diz",  "DIZ"),
    ("md",   "MD"),
    # Archives
    ("zip",  "ZIP"),
    ("7z",   "7Z"),
    ("rar",  "RAR"),
    ("tar",  "TAR"),
    ("gz",   "GZ"),
    ("xz",   "XZ"),
    # Misc / fallback
    ("xml",  "XML"),
    ("json", "JSON"),
)

# Now that ZXFMT_PLACEHOLDER_LABELS is defined, build the release-format tag
# map used by _zxart_tag_for_format() (defined earlier in the file).
_ZXART_RELEASE_FORMAT_TAGS.update(_build_release_format_tags())


def zxfmt_split_ext(name: str) -> str:
    """Return the lower-case extension (without the dot) of *name*, handling
    composite/uncommon Spectrum extensions like ``.tap.zip`` and ``$b``."""
    if not name:
        return ""
    n = name.lower().strip()
    # Strip query strings / fragments if a URL was passed in.
    for sep in ("?", "#"):
        if sep in n:
            n = n.split(sep, 1)[0]
    n = os.path.basename(n)
    # Composite ``foo.tap.zip`` -> use the inner format as the primary hint.
    parts = n.split(".")
    if len(parts) >= 3 and parts[-1] in ("zip", "gz", "xz", "7z", "rar"):
        inner = parts[-2]
        if inner:
            return inner
    if "." in n:
        return n.rsplit(".", 1)[-1]
    # Files like ``foo$b`` (BASIC) or ``foo$c`` (CODE) come from +D / DISCiPLE.
    if "$" in n:
        return "$" + n.rsplit("$", 1)[-1]
    return ""


def zxfmt_is_image_name(name: str) -> bool:
    """Return True if *name* points at an actual picture (not a generic
    document or binary)."""
    if not name:
        return False
    n = name.lower()
    for sep in ("?", "#"):
        if sep in n:
            n = n.split(sep, 1)[0]
    return any(n.endswith(ext) for ext in ZXFMT_IMAGE_EXTS)


def zxfmt_label_for_name(name: str) -> str:
    """Return a short upper-case label to render in a placeholder thumbnail
    for *name* (e.g. ``"TAP"``, ``"PDF"``, ``"POK"``).  Falls back to the
    extension itself, then to ``"FILE"``."""
    ext = zxfmt_split_ext(name)
    if not ext:
        return "FILE"
    for needle, label in ZXFMT_PLACEHOLDER_LABELS:
        if ext == needle:
            return label
    return ext.upper()[:6]


def zxfmt_make_placeholder_pixmap(label: str, subtitle: str = "",
                                  width: int = 320) -> QPixmap:
    """Render a 4:3 placeholder QPixmap with *label* in big letters and an
    optional *subtitle* (typically the file name) below.  Used by the
    gallery cells when no real screenshot is available so the user can still
    tell at a glance what kind of content the entry holds."""
    w = max(160, int(width))
    h = int(w * 3 / 4)
    pm = QPixmap(w, h)
    pm.fill(QColor("#101820"))
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # Frame
        p.setPen(QColor("#3a4a5a"))
        p.drawRect(2, 2, w - 5, h - 5)
        # Big label
        big = QFont()
        big.setBold(True)
        # Scale font size to roughly fit the label width.
        target_w = int(w * 0.7)
        size = max(18, int(h * 0.42))
        big.setPointSize(size)
        p.setFont(big)
        # Reduce until the label fits.
        for _ in range(12):
            fm = p.fontMetrics()
            if fm.horizontalAdvance(label) <= target_w:
                break
            size = max(10, size - 2)
            big.setPointSize(size)
            p.setFont(big)
        p.setPen(QColor("#ffd24a"))
        fm = p.fontMetrics()
        label_h = fm.height()
        label_y = int(h / 2) - 4
        p.drawText(QRect(0, label_y - label_h, w, label_h),
                   Qt.AlignCenter, label)
        # Subtitle (file name) underneath
        if subtitle:
            sub = QFont()
            sub.setPointSize(max(8, int(h * 0.09)))
            p.setFont(sub)
            p.setPen(QColor("#cfd8dc"))
            fm2 = p.fontMetrics()
            sub_text = subtitle
            avail = w - 16
            if fm2.horizontalAdvance(sub_text) > avail:
                sub_text = fm2.elidedText(sub_text, Qt.ElideMiddle, avail)
            sub_y = label_y + 6
            p.drawText(QRect(8, sub_y, w - 16, fm2.height() + 4),
                       Qt.AlignHCenter | Qt.AlignTop, sub_text)
    finally:
        p.end()
    return pm


# ---------------------------------------------------------------------------
# ZX Spectrum .scr screen decoder
# ---------------------------------------------------------------------------
# The standard Spectrum screen file is a raw memory dump of the display area
# at address 0x4000..0x5AFF, i.e. exactly 6912 bytes:
#   * Bytes 0..6143   : 6144 bytes of bitmap (256 x 192 px, 1 bit per pixel).
#   * Bytes 6144..6911:  768 bytes of attribute cells (32 x 24, 8x8 pixels each).
#
# Bitmap row addressing follows the well-known Spectrum interleave: for a
# pixel row y in 0..191, the byte offset of the leftmost byte of that row is
#       offset = ((y & 0b11000000) << 5)   # third = y / 64
#              | ((y & 0b00000111) << 8)   # pixel row within char row
#              | ((y & 0b00111000) << 2)   # char row within third
# (each "third" is 2048 bytes; within a third, eight 256-byte pixel-row
# planes are interleaved with the eight 32-byte text rows.)
#
# Attribute byte layout (per 8x8 cell):
#   bit  7  : FLASH
#   bit  6  : BRIGHT
#   bits 5-3: PAPER colour (0..7)
#   bits 2-0: INK   colour (0..7)
#
# The 8 standard Spectrum colours, as 24-bit RGB.  "Bright" uses 0xFF for the
# non-zero components, "normal" uses 0xCD (a widely used approximation of the
# real CRT brightness used by emulators and converters).
ZXSCR_PALETTE_NORMAL = (
    (0x00, 0x00, 0x00),  # 0 black
    (0x00, 0x00, 0xCD),  # 1 blue
    (0xCD, 0x00, 0x00),  # 2 red
    (0xCD, 0x00, 0xCD),  # 3 magenta
    (0x00, 0xCD, 0x00),  # 4 green
    (0x00, 0xCD, 0xCD),  # 5 cyan
    (0xCD, 0xCD, 0x00),  # 6 yellow
    (0xCD, 0xCD, 0xCD),  # 7 white
)
ZXSCR_PALETTE_BRIGHT = (
    (0x00, 0x00, 0x00),
    (0x00, 0x00, 0xFF),
    (0xFF, 0x00, 0x00),
    (0xFF, 0x00, 0xFF),
    (0x00, 0xFF, 0x00),
    (0x00, 0xFF, 0xFF),
    (0xFF, 0xFF, 0x00),
    (0xFF, 0xFF, 0xFF),
)
ZXSCR_BYTES = 6912


def zxscr_is_screen_bytes(data) -> bool:
    """Return True if *data* looks like a 6912-byte Spectrum screen dump."""
    return isinstance(data, (bytes, bytearray)) and len(data) == ZXSCR_BYTES


def _zxscr_pixel_row_offset(y: int) -> int:
    """Offset of the leftmost byte of pixel row *y* in the bitmap plane."""
    return ((y & 0xC0) << 5) | ((y & 0x07) << 8) | ((y & 0x38) << 2)


class ZxSpectrumScreen:
    """Decoder for a standard 6912-byte ZX Spectrum SCREEN$ file.

    This is an original Python implementation written from the public
    description of the Spectrum display format; it does not derive from
    any third-party converter.  ``flash_phase`` selects which half of the
    FLASH cycle to render (False = INK/PAPER as stored, True = swapped).
    """

    def __init__(self, data: bytes):
        if not zxscr_is_screen_bytes(data):
            raise ValueError("not a 6912-byte SCR screen")
        self._data = bytes(data)

    @property
    def width(self) -> int:
        return 256

    @property
    def height(self) -> int:
        return 192

    def to_qimage(self, flash_phase: bool = False) -> QImage:
        """Render the screen to a 256x192 ``QImage`` (RGB32)."""
        img = QImage(256, 192, QImage.Format_RGB32)
        data = self._data
        attrs = data  # alias for clarity; offset 6144 onwards
        for y in range(192):
            row_off = _zxscr_pixel_row_offset(y)
            attr_row = (y >> 3) * 32 + 6144
            char_y = y >> 3  # not used after row_off but kept for clarity
            for cx in range(32):
                byte = data[row_off + cx]
                attr = attrs[attr_row + cx]
                ink   = attr & 0x07
                paper = (attr >> 3) & 0x07
                bright = bool(attr & 0x40)
                flash  = bool(attr & 0x80)
                if flash and flash_phase:
                    ink, paper = paper, ink
                pal = ZXSCR_PALETTE_BRIGHT if bright else ZXSCR_PALETTE_NORMAL
                ink_r, ink_g, ink_b = pal[ink]
                pap_r, pap_g, pap_b = pal[paper]
                ink_px = (0xFF << 24) | (ink_r << 16) | (ink_g << 8) | ink_b
                pap_px = (0xFF << 24) | (pap_r << 16) | (pap_g << 8) | pap_b
                x = cx * 8
                # MSB is the leftmost pixel of the byte.
                for bit in range(8):
                    on = (byte >> (7 - bit)) & 1
                    img.setPixel(x + bit, y, ink_px if on else pap_px)
        return img


def zxscr_url_is_scr(url) -> bool:
    """Return True if *url* (string) points at a .scr screen file."""
    if not isinstance(url, str):
        return False
    n = url.lower()
    for sep in ("?", "#"):
        if sep in n:
            n = n.split(sep, 1)[0]
    return n.endswith(".scr")


# In-memory cache: url/base_name -> QPixmap (avoids repeated decoding)
# Guarded by a lock because it is populated from background worker threads
# (SCR byte conversion) and read from the GUI thread (extra-fetch callbacks).
_ZXSCR_PIXMAP_CACHE_LOCK = threading.RLock()
_ZXSCR_PIXMAP_CACHE: dict = {}


def _zxscr_basename_for_url(url: str) -> str:
    """Derive a cache key (no extension) from *url*."""
    try:
        from urllib.parse import urlsplit, unquote
        path = unquote(urlsplit(url).path)
    except Exception:
        path = url
    name = os.path.basename(path) or "screen.scr"
    stem, _ext = os.path.splitext(name)
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in stem)
    return safe or "screen"


def zxscr_convert_bytes_to_pixmap(data: bytes, base_name: str):
    """Decode *data* (6912-byte SCR) and cache the QPixmap in memory.
    Returns a QPixmap or None on failure."""
    if not zxscr_is_screen_bytes(data):
        return None
    with _ZXSCR_PIXMAP_CACHE_LOCK:
        cached = _ZXSCR_PIXMAP_CACHE.get(base_name)
    if cached is not None:
        return cached
    try:
        img = ZxSpectrumScreen(data).to_qimage()
        pm = QPixmap.fromImage(img)
        if not pm.isNull():
            with _ZXSCR_PIXMAP_CACHE_LOCK:
                _ZXSCR_PIXMAP_CACHE[base_name] = pm
        return pm
    except Exception:
        return None


def zxscr_try_pixmap_from_url_bytes(url, data):
    """If *url* / *data* describe a Spectrum .scr screen, return a QPixmap
    rendered from it (with caching).  Otherwise return ``None`` so the
    caller can fall back to its normal image-loading path."""
    if not zxscr_is_screen_bytes(data) and not zxscr_url_is_scr(url):
        return None
    if not zxscr_is_screen_bytes(data):
        return None
    base = _zxscr_basename_for_url(url) if isinstance(url, str) else "screen"
    return zxscr_convert_bytes_to_pixmap(data, base)


def zxfmt_pick_best_download(downloads):
    """From a list of download dicts (each may have ``url``/``filename``/
    ``format``/``type``), pick the entry most suitable for a placeholder
    thumbnail label.  Preference order: tape > disk > snapshot > pok > docs
    > anything else.  Returns ``(label, filename)`` or ``("FILE", "")`` if
    *downloads* is empty / unusable."""
    if not downloads:
        return ("FILE", "")
    priority = {
        # tapes
        "tap": 0, "tzx": 0, "pzx": 0, "cdt": 0, "csw": 1,
        # disks
        "trd": 2, "scl": 2, "dsk": 2, "fdi": 2, "td0": 2, "mgt": 2, "img": 2,
        # snapshots
        "sna": 3, "z80": 3, "szx": 3, "sp": 3, "slt": 3,
        # cartridges
        "rom": 4, "dck": 4,
        # pokes
        "pok": 5,
        # docs
        "pdf": 6, "txt": 6, "nfo": 6, "diz": 6,
        # archives
        "zip": 8, "7z": 8, "rar": 8, "tar": 8, "gz": 8, "xz": 8,
    }
    best = None
    best_rank = 999
    for d in downloads:
        if not isinstance(d, dict):
            continue
        name = str(d.get("filename") or "")
        url  = str(d.get("url") or "")
        ref  = name or url
        if not ref:
            continue
        ext  = zxfmt_split_ext(ref)
        rank = priority.get(ext, 7)
        if rank < best_rank:
            best_rank = rank
            best = (zxfmt_label_for_name(ref), name or os.path.basename(
                urllib.parse.urlparse(url).path) if url else name)
    if best is None:
        # Fall back to the first usable entry.
        for d in downloads:
            if not isinstance(d, dict):
                continue
            name = str(d.get("filename") or "")
            url  = str(d.get("url") or "")
            ref  = name or url
            if not ref:
                continue
            return (zxfmt_label_for_name(ref),
                    name or os.path.basename(urllib.parse.urlparse(url).path))
        return ("FILE", "")
    return best


def _build_tooltip_text(lines: list) -> str:
    """Join `lines` into a tooltip string, truncating to MAX_ALT_TEXT_LINES.

    If the total number of non-empty lines exceeds MAX_ALT_TEXT_LINES, the
    list is cut at that limit and a trailing "..." line is appended.
    """
    non_empty = [ln for ln in lines if ln and str(ln).strip()]
    if len(non_empty) > MAX_ALT_TEXT_LINES:
        non_empty = non_empty[:MAX_ALT_TEXT_LINES] + ["..."]
    return "\n".join(non_empty)


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.
__all__ = [_n for _n in dir() if not _n.startswith('__')]
