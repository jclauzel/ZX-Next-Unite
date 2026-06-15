"""Pygame-rendered visualization layer for the "Unite!" tab.

This module is an OPTIONAL alternative presentation layer.  It renders the same
Table / Gallery / item-viewer experience as the classic PySide6 widgets, but
draws everything onto an offscreen ``pygame.Surface`` that is blitted into a
plain ``QWidget`` each frame.  No SDL window is ever created (we never call
``pygame.display.set_mode``), so there is no second event loop fighting Qt and
no extra OS window — Qt input events are translated and dispatched to a small
immediate-mode "scene".

Key constraints (see plan):
- pygame is imported lazily; ``pygame_available()`` can be called without it.
- We never call ``Surface.convert()/convert_alpha()`` (those require a display
  mode).  All surfaces are created with the ``SRCALPHA`` flag.
- Scenes work in *device* pixel coordinates so the result is crisp on HiDPI
  displays; Qt event coordinates are scaled by the device-pixel ratio.

The three scenes mirror the classic UI:
- ``TableScene``        – Source / Title / Rating / Info / Year rows.
- ``GalleryScene``      – thumbnail grid with source badge + favorite heart.
- ``PygameItemViewer``  – item viewer that *duck-types* the public API of
  ``zxnu_gallery.GalleryItemViewer`` so the existing per-source openers can
  populate it unchanged (full action parity).
"""

from __future__ import annotations

import html as _html
import importlib.util
import math as _math
import random as _random
import re as _re
import threading as _threading
import time as _time

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget


# ── lazy pygame handle ──────────────────────────────────────────────────────
_pg = None  # set by _ensure_pg()


def pygame_available():
    """Return ``(ok, reason)``.  Does NOT import pygame (no side effects)."""
    try:
        spec = importlib.util.find_spec("pygame")
    except Exception as exc:  # pragma: no cover - defensive
        return (False, f"pygame import probe failed: {exc}")
    if spec is None:
        return (False, "The 'pygame' package is not installed.")
    return (True, "")


def _ensure_pg():
    """Import pygame on first use and initialise only the font subsystem."""
    global _pg
    if _pg is None:
        import pygame  # noqa: PLC0415 - intentional lazy import
        try:
            pygame.font.init()
        except Exception:
            pass
        _pg = pygame
    return _pg


# ── one-time warm-up (off the UI thread) ────────────────────────────────────
_PREWARM_STARTED = False


def prewarm_async():
    """Warm pygame and the font caches on a background daemon thread.

    The first time an :class:`AlienFloydWidget` paints, it builds the HUD fonts;
    pygame's very first ``match_font`` call runs ``initsysfonts()``, which
    enumerates *every* installed system font and can block the calling thread
    for seconds.  Doing it here, ahead of time and off the UI thread, means the
    first time the user opens the "Alien Floyd's" tab the caches are already
    populated and the paint is instant.

    Safe to call repeatedly: only the first call spawns the thread, and it is a
    no-op when pygame isn't installed."""
    global _PREWARM_STARTED
    if _PREWARM_STARTED:
        return
    ok, _why = pygame_available()
    if not ok:
        return
    _PREWARM_STARTED = True

    def _warm():
        try:
            _ensure_pg()
            # Touch the system-font matcher (the expensive one-time step) and
            # pre-build the fonts the render path requests.  Cover both raw and
            # HiDPI-doubled sizes so the actual Font objects are cached too; the
            # dominant initsysfonts() cost is paid regardless of size.
            sizes = (9, 10, 11, 13, 14, 16, 18, 20, 24, 26, 30)
            for px in sizes:
                _font(px, bold=True)
                _font(px, bold=False)
                _fallback_fonts(px)
            for px in sizes:
                _font(px * 2, bold=True)
        except Exception:
            pass

    _threading.Thread(target=_warm, name="alien-floyd-prewarm",
                      daemon=True).start()


# ── palette (mirrors GalleryItemViewer's dark theme) ────────────────────────
C_BG        = (13, 13, 13)
C_PANEL     = (17, 17, 17)
C_IMG_BG    = (10, 10, 10)
C_BTN       = (42, 42, 42)
C_BTN_HOVER = (58, 58, 58)
C_BTN_DIS   = (26, 26, 26)
C_BORDER    = (68, 68, 68)
C_TEXT      = (221, 221, 221)
C_TEXT_DIM  = (136, 136, 136)
C_TEXT_OFF  = (85, 85, 85)
C_TITLE     = (255, 255, 255)
C_HEART     = (255, 85, 119)
C_SEL       = (38, 70, 110)
C_HOVER     = (32, 32, 36)
C_HEADER    = (28, 28, 28)
C_BADGE_BG  = (28, 58, 82)
C_BADGE_TX  = (191, 230, 255)
C_GRID_LINE = (40, 40, 40)


# ── conversions ─────────────────────────────────────────────────────────────
def qimage_to_surface(qimg):
    """Convert a QImage to an RGBA pygame Surface (or None)."""
    if qimg is None or qimg.isNull():
        return None
    pg = _ensure_pg()
    img = qimg.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return None
    try:
        buf = bytes(img.constBits())[: w * h * 4]
    except Exception:
        return None
    try:
        return pg.image.frombytes(buf, (w, h), "RGBA")
    except Exception:
        return None


def qpixmap_to_surface(qpix):
    if qpix is None or qpix.isNull():
        return None
    return qimage_to_surface(qpix.toImage())


def _surface_to_qimage(surf):
    pg = _pg
    w, h = surf.get_size()
    buf = pg.image.tobytes(surf, "RGBA")
    # Copy so the QImage owns its data after *buf* is collected.
    return QImage(buf, w, h, QImage.Format_RGBA8888).copy()


def _scale_keep_aspect(surf, max_w, max_h):
    pg = _pg
    w, h = surf.get_size()
    if w <= 0 or h <= 0 or max_w <= 0 or max_h <= 0:
        return surf
    scale = min(max_w / w, max_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    try:
        return pg.transform.smoothscale(surf, (nw, nh))
    except Exception:
        return pg.transform.scale(surf, (nw, nh))


# ── text helpers ────────────────────────────────────────────────────────────
_FONT_CACHE = {}
_FONT_PX = {}              # id(font) -> point size (for fallback-font sizing)
_FALLBACK_CACHE = {}       # px -> tuple(Font, ...) of available fallback fonts
# Guards the font-cache *build* paths only (the cache-hit fast path stays
# lockless).  Lets prewarm() populate the caches on a background thread without
# racing the UI thread's first render through pygame's one-time, non-reentrant
# initsysfonts() (which enumerates every installed system font).
_FONT_BUILD_LOCK = _threading.Lock()
_NOTDEF_CACHE = {}         # id(font) -> bytes of the font's ".notdef" (tofu) glyph
_GLYPH_FONT_CACHE = {}     # (id(base_font), ch) -> Font that can draw ch (or None)

# Preferred UI font for the pygame scenes.  Consolas is used explicitly; Segoe
# UI is kept as a graceful fallback, then pygame's bundled default.
_UI_FONT_NAMES = ("Consolas", "Segoe UI")

# Fonts that supply glyphs the monospace UI font lacks: assorted symbols/arrows
# (◀ ▶ ✕ ⬇ ♡) and emoji (🕹 🌐 💾 …).  Tried per-glyph, in order, until one
# actually provides the glyph.  "Segoe UI Symbol" is preferred first because it
# renders these as *monochrome* glyphs that honour the requested text colour
# (so arrows/close icons stay visible on dark buttons); colour-emoji fonts are
# kept as a secondary fallback for platforms without it.
_FALLBACK_FONT_NAMES = (
    "Segoe UI Symbol",     # Windows monochrome symbols/arrows/emoji (colour-aware)
    "Segoe UI Emoji",      # Windows colour emoji
    "Apple Color Emoji",   # macOS colour emoji
    "Noto Color Emoji",    # Linux colour emoji
    "Noto Emoji",          # Linux monochrome emoji
    "Symbola",
)


def _font(px, bold=False):
    px = max(8, int(px))
    key = (px, bold)
    f = _FONT_CACHE.get(key)
    if f is not None:
        return f
    with _FONT_BUILD_LOCK:
        f = _FONT_CACHE.get(key)   # re-check: prewarm/another caller may have won
        if f is not None:
            return f
        pg = _ensure_pg()
        f = None
        for name in _UI_FONT_NAMES:
            try:
                path = pg.font.match_font(name, bold=bold)
            except Exception:
                path = None
            if not path:
                continue
            try:
                f = pg.font.Font(path, px)
                break
            except Exception:
                f = None
        if f is None:
            try:
                f = pg.font.SysFont("Consolas, Segoe UI", px, bold=bold)
            except Exception:
                f = pg.font.Font(None, px)
        _FONT_CACHE[key] = f
        _FONT_PX[id(f)] = px
    return f


def _fallback_fonts(px):
    """Return cached tuple of available fallback fonts sized at *px*."""
    px = max(8, int(px))
    fonts = _FALLBACK_CACHE.get(px)
    if fonts is not None:
        return fonts
    with _FONT_BUILD_LOCK:
        fonts = _FALLBACK_CACHE.get(px)   # re-check under lock
        if fonts is not None:
            return fonts
        pg = _ensure_pg()
        out = []
        for name in _FALLBACK_FONT_NAMES:
            try:
                path = pg.font.match_font(name)
            except Exception:
                path = None
            if not path:
                continue
            try:
                out.append(pg.font.Font(path, px))
            except Exception:
                pass
        fonts = tuple(out)
        _FALLBACK_CACHE[px] = fonts
    return fonts


def _notdef_bytes(font):
    """Cached RGBA bytes of *font*'s ".notdef" box (rendered via a noncharacter
    that no font maps), used to detect tofu/missing glyphs reliably."""
    nid = id(font)
    b = _NOTDEF_CACHE.get(nid)
    if b is None:
        try:
            img = font.render("\ufdd0", True, (255, 255, 255))
            b = _pg.image.tobytes(img, "RGBA")
        except Exception:
            b = b""
        _NOTDEF_CACHE[nid] = b
    return b


def _font_has_glyph(font, ch):
    """True if *font* draws a real glyph for *ch* (not its .notdef/tofu box).

    pygame's ``Font.metrics`` returns the .notdef box metrics for missing
    glyphs (not ``None``), so it cannot be trusted.  Instead we compare the
    rendered bitmap against the font's known tofu box.
    """
    try:
        b = _pg.image.tobytes(font.render(ch, True, (255, 255, 255)), "RGBA")
    except Exception:
        return False
    if b == _notdef_bytes(font):
        return False
    if not any(b[3::4]):            # fully transparent → nothing was drawn
        return False
    return True


def _glyph_font(base_font, ch):
    """Return the font to draw *ch* with: the base font when it has the glyph,
    otherwise the first fallback font that provides it, or None."""
    key = (id(base_font), ch)
    if key in _GLYPH_FONT_CACHE:
        return _GLYPH_FONT_CACHE[key]
    font = None
    if _font_has_glyph(base_font, ch):
        font = base_font
    else:
        px = _FONT_PX.get(id(base_font))
        if px is None:
            px = max(8, int(round(base_font.get_height() / 1.3)))
        for fb in _fallback_fonts(px):
            if _font_has_glyph(fb, ch):
                font = fb
                break
    _GLYPH_FONT_CACHE[key] = font
    return font


def _draw_text(surface, text, x, y, font, color):
    if not text:
        return 0
    s = str(text)
    # Fast path: plain ASCII/Latin strings are fully covered by the monospace
    # UI font, so render them in a single blit.  Strings with higher-plane
    # characters (arrows, dingbats, emoji, …) get per-glyph fallback handling.
    if all(ord(c) < 0x2000 for c in s):
        try:
            img = font.render(s, True, color)
        except Exception:
            return 0
        surface.blit(img, (int(x), int(y)))
        return img.get_height()
    return _draw_text_mixed(surface, s, x, y, font, color)


def _draw_text_mixed(surface, s, x, y, base_font, color):
    """Render *s* one run at a time, substituting a fallback font for glyphs the
    monospace UI font lacks (emoji, arrows, dingbats, …)."""
    # Resolve a drawing font per character, then coalesce consecutive characters
    # sharing the same font into runs.
    runs = []
    cur_font = None
    cur_chars = []
    for ch in s:
        f = base_font if ch.isspace() else (_glyph_font(base_font, ch) or base_font)
        if cur_chars and f is not cur_font:
            runs.append((cur_font, "".join(cur_chars)))
            cur_chars = []
        cur_chars.append(ch)
        cur_font = f
    if cur_chars:
        runs.append((cur_font, "".join(cur_chars)))

    cur_x = int(x)
    base_h = base_font.get_height()
    max_h = 0
    for f, seg in runs:
        img = None
        try:
            img = f.render(seg, True, color)
        except Exception:
            if f is not base_font:
                try:               # keep layout even if a fallback render fails
                    img = base_font.render(seg, True, color)
                except Exception:
                    img = None
        if img is None:
            continue
        h = img.get_height()
        oy = (base_h - h) // 2 if f is not base_font else 0
        surface.blit(img, (cur_x, int(y) + oy))
        cur_x += img.get_width()
        max_h = max(max_h, h)
    return max_h or base_h


def _elide(text, font, max_w):
    text = str(text or "")
    if font.size(text)[0] <= max_w:
        return text
    ell = "…"
    while text and font.size(text + ell)[0] > max_w:
        text = text[:-1]
    return text + ell if text else ell


def _wrap_lines(text, font, max_w):
    """Wrap *text* (honouring explicit newlines) into a list of lines."""
    out = []
    for para in str(text or "").split("\n"):
        if not para:
            out.append("")
            continue
        words = para.split(" ")
        line = ""
        for w in words:
            cand = (line + " " + w) if line else w
            if font.size(cand)[0] <= max_w or not line:
                line = cand
            else:
                out.append(line)
                line = w
        out.append(line)
    return out


_STAR_RE = _re.compile(r"\s*[★☆]+\s*[\d.,]*\s*(?:\([^)]*\))?\s*$")
_SPAN_RE = _re.compile(r"<span[^>]*>([^<]+)</span>")


def _strip_html(value):
    """Mirror GalleryItemViewer._rebuild_meta's sanitisation to plain text."""
    raw = str(value or "")
    raw = _re.sub(r"<br\s*/?>", "\n", raw, flags=_re.IGNORECASE)
    raw = _re.sub(r"</p\s*>", "\n\n", raw, flags=_re.IGNORECASE)
    raw = _re.sub(r"<li\s*/?>", "\n• ", raw, flags=_re.IGNORECASE)
    raw = _re.sub(r"<[^>]+>", "", raw)
    raw = _html.unescape(raw)
    return _re.sub(r"\n{3,}", "\n\n", raw).strip()


def _split_title_rating(raw_title):
    """Split an aggregated title that may carry a trailing star-rating span,
    mirroring the logic in zx-next-unite.py:_allinone_fill_table."""
    raw_title = str(raw_title or "")
    m = _SPAN_RE.search(raw_title)
    rating = m.group(1).strip() if m else ""
    title = _re.sub(r"<[^>]+>", "", raw_title).strip()
    pm = _STAR_RE.search(title)
    if pm:
        if not rating:
            rating = pm.group(0).strip()
        title = title[: pm.start()].strip()
    return title, rating


# ── animated Space-Invaders background ──────────────────────────────────────
_VEIL_RGB = (8, 9, 16)
_VEIL_CACHE = {}


def _blit_veil(surface, rgb, alpha):
    """Blit a cached translucent veil so the animated background glows through
    the scene content drawn on top of it."""
    pg = _pg
    size = surface.get_size()
    key = (size, rgb, alpha)
    v = _VEIL_CACHE.get(key)
    if v is None:
        if len(_VEIL_CACHE) > 6:
            _VEIL_CACHE.clear()
        v = pg.Surface(size, pg.SRCALPHA)
        v.fill((rgb[0], rgb[1], rgb[2], alpha))
        _VEIL_CACHE[key] = v
    surface.blit(v, (0, 0))


# Classic 11×8 "crab" invader, two animation frames.
_INV_A = [
    "..X.....X..",
    "...X...X...",
    "..XXXXXXX..",
    ".XX.XXX.XX.",
    "XXXXXXXXXXX",
    "X.XXXXXXX.X",
    "X.X.....X.X",
    "...XX.XX...",
]
_INV_B = [
    "..X.....X..",
    "X..X...X..X",
    "X.XXXXXXX.X",
    "XXX.XXX.XXX",
    "XXXXXXXXXXX",
    ".XXXXXXXXX.",
    "..X.....X..",
    ".X.......X.",
]
_CANNON = [
    "......X......",
    ".....XXX.....",
    ".....XXX.....",
    ".XXXXXXXXXXX.",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
]
_UFO = [
    "....XXXXXXXX....",
    "..XXXXXXXXXXXX..",
    ".XXXXXXXXXXXXXX.",
    "XXXXXXXXXXXXXXXX",
    ".XX.XX.XX.XX.XX.",
]

_STAR_COLORS = [(255, 255, 255), (170, 200, 255), (255, 225, 180), (190, 255, 220)]
_ALIEN_ROW_COLORS = [(150, 255, 150), (120, 220, 255), (120, 220, 255),
                     (255, 175, 95), (255, 175, 95)]
_C_SHIP = (90, 235, 255)
_C_BULLET = (255, 245, 130)
_C_BOMB = (255, 120, 170)
_C_UFO = (255, 90, 120)
_C_PIG = (255, 130, 185)        # pink pig explosion burst
_C_SKY = (7, 8, 16)

# "Dark Side of the Moon" prism rainbow, used on the game-over score screen.
_SPECTRUM = [
    (255, 45, 45), (255, 140, 30), (255, 225, 45), (70, 220, 90),
    (60, 165, 255), (80, 80, 235), (170, 60, 220),
]

# Animated alien-fire palette: bright, saturated hues cycled per frame so the
# falling bombs stay vivid against the black sky (a flat red barely showed up).
_C_BOMB_CYCLE = [
    (255, 60, 60), (255, 150, 40), (255, 240, 70), (90, 255, 110),
    (60, 200, 255), (150, 110, 255), (255, 80, 200),
]

# Animated palette for the C5's own bullets (a livelier take on the old flat
# yellow), plus warm/cool spark colours for the muzzle "shooting stars".
_C_BULLET_CYCLE = [
    (255, 245, 130), (150, 255, 170), (130, 220, 255), (255, 170, 225),
    (205, 165, 255), (255, 215, 120),
]
_C_SPARK_COLORS = [
    (255, 255, 200), (255, 220, 120), (160, 255, 205), (150, 220, 255),
    (255, 175, 225), (205, 175, 255),
]


def _cycle_color(palette, phase):
    """Smoothly interpolate around *palette* by float *phase* (in entries)."""
    n = len(palette)
    f = phase % n
    i = int(f)
    j = (i + 1) % n
    u = f - i
    a, b = palette[i], palette[j]
    return (int(a[0] + (b[0] - a[0]) * u),
            int(a[1] + (b[1] - a[1]) * u),
            int(a[2] + (b[2] - a[2]) * u))


_GLOW_CACHE = {}


def _make_sprite(pattern, color, px):
    """Render a bitmap *pattern* (list of strings) at *px* pixels per cell."""
    pg = _pg
    rows = len(pattern)
    cols = len(pattern[0]) if rows else 0
    surf = pg.Surface((max(1, cols * px), max(1, rows * px)), pg.SRCALPHA)
    for r, line in enumerate(pattern):
        for c, ch in enumerate(line):
            if ch == "X":
                surf.fill((color[0], color[1], color[2], 255),
                          pg.Rect(c * px, r * px, px, px))
    return surf


def _make_glow(color, r, level):
    """A cached radial glow sprite (additive) for a star."""
    key = (color, r, level)
    g = _GLOW_CACHE.get(key)
    if g is not None:
        return g
    pg = _pg
    size = r * 2 + 1
    g = pg.Surface((size, size), pg.SRCALPHA)
    for i in range(r, 0, -1):
        f = (1.0 - i / r)
        f = f * f * level
        col = (int(color[0] * f), int(color[1] * f), int(color[2] * f))
        pg.draw.circle(g, col, (r, r), i)
    _GLOW_CACHE[key] = g
    return g


# ── Pink Floyd "Alien Floyd" sprites ────────────────────────────────────────
# Each alien randomly becomes one of these 8-bit, multi-colour homages to the
# band: pigs, (dark) moons, the prism / triangle spectre, holograms, guitars,
# bass guitars, microphones, dogs, beds, clouds and drums.  Patterns are fixed
# 12×10 grids; every non-'.' character maps to an RGB colour in the sprite's
# own palette.
_FLOYD_W = 12
_FLOYD_H = 10

_FLOYD_PATTERNS = {
    "pig": ([
        "............",
        ".....kk.....",
        "...kkPPkk...",
        "..kPPPPPPk..",
        ".kPPPPPPPPk.",
        ".kPPkPPPPok.",
        ".kPPPPPPPok.",
        "..kPPPPPPk..",
        "...k.kk.k...",
        "............",
    ], {"P": (255, 150, 190), "o": (220, 90, 140), "k": (60, 30, 50)}),
    "moon": ([
        "....kkkk....",
        "..kkMMMMkk..",
        ".kMMMMMMMMk.",
        ".kMMMMMMMMk.",
        "kMMMMMMMMMMk",
        "kMMMMMMMMMMk",
        ".kMMMMMMMMk.",
        ".kMMMMMMMMk.",
        "..kkMMMMkk..",
        "....kkkk....",
    ], {"M": (245, 240, 180), "k": (120, 115, 70)}),
    "dark_moon": ([
        "....hhhh....",
        "..hhDDDDhh..",
        ".hDDDDDDDDh.",
        ".hDDDDDDDDh.",
        "hDDDDDDDDDDh",
        "hDDDDDDDDDDh",
        ".hDDDDDDDDh.",
        ".hDDDDDDDDh.",
        "..hhDDDDhh..",
        "....hhhh....",
    ], {"D": (55, 55, 75), "h": (120, 120, 150)}),
    "prism": ([
        ".....W......",
        "....WWW.....",
        "....WWW.....",
        "...WWWWW....",
        "...WWWWWrrrr",
        "..WWWWWWgggg",
        "..WWWWWWbbbb",
        ".WWWWWWW....",
        ".WWWWWWW....",
        "WWWWWWWWW...",
    ], {"W": (235, 235, 245), "r": (230, 60, 60), "g": (60, 210, 90), "b": (70, 120, 235)}),
    "hologram": ([
        ".....cc.....",
        "....cCCc....",
        "...cCCCCc...",
        "..cCCCCCCc..",
        ".cCCCCCCCCc.",
        ".cCCCCCCCCc.",
        "..cCCCCCCc..",
        "...cCCCCc...",
        "....cCCc....",
        ".....cc.....",
    ], {"c": (120, 255, 245), "C": (40, 160, 180)}),
    "guitar": ([
        ".........nn.",
        "........nn..",
        ".......nn...",
        "..ggg.nn....",
        ".gggggn.....",
        "ggggggg.....",
        "gggGgggg....",
        ".ggggggg....",
        "..gggggg....",
        "...ggg......",
    ], {"g": (190, 110, 50), "G": (40, 25, 15), "n": (140, 90, 40)}),
    "bass": ([
        ".........nn.",
        "........nn..",
        ".......nn...",
        "..BBB.nn....",
        ".BBBBBn.....",
        "BBBBBBB.....",
        "BBBGBBBB....",
        ".BBBBBBB....",
        "..BBBBBB....",
        "...BBB......",
    ], {"B": (70, 120, 210), "G": (20, 25, 45), "n": (120, 120, 140)}),
    "microphone": ([
        "....ssss....",
        "...sSSSSs...",
        "..sSSSSSSs..",
        "..sSSSSSSs..",
        "..sSSSSSSs..",
        "...sSSSSs...",
        "....shhs....",
        ".....hh.....",
        ".....hh.....",
        "....hhhh....",
    ], {"s": (190, 195, 205), "S": (110, 115, 130), "h": (70, 70, 80)}),
    "dog": ([
        "........dd..",
        ".......dddd.",
        "d......ddddk",
        "ddd...ddddd.",
        "ddddddddddd.",
        "ddddddddddd.",
        ".dd.ddd.dd..",
        ".dd.ddd.dd..",
        ".d...d...d..",
        "............",
    ], {"d": (150, 95, 55), "k": (30, 20, 15)}),
    "bed": ([
        "............",
        "..PP........",
        ".bMMMMMMMMb.",
        ".bMMMMMMMMb.",
        ".bMMMMMMMMb.",
        ".f........f.",
        ".f........f.",
        "............",
        "............",
        "............",
    ], {"P": (230, 230, 240), "M": (90, 140, 200), "b": (120, 80, 50), "f": (120, 80, 50)}),
    "cloud": ([
        "............",
        "....CCCC....",
        "..CCCCCCCC..",
        ".CCCCCCCCCC.",
        "CCCCCCCCCCCC",
        "CCCCCCCCCCCC",
        ".CCCCCCCCCC.",
        "............",
        "............",
        "............",
    ], {"C": (235, 240, 250)}),
    "drum": ([
        "............",
        ".rRrRrRrRr..",
        "RRRRRRRRRR..",
        "RwwwwwwwwR..",
        "RwwwwwwwwR..",
        "RwwwwwwwwR..",
        "RRRRRRRRRR..",
        ".r.r.r.r.r..",
        "............",
        "............",
    ], {"R": (200, 50, 60), "r": (230, 200, 90), "w": (240, 235, 225)}),
}

_FLOYD_NAMES = tuple(_FLOYD_PATTERNS.keys())
_FLOYD_SPRITE_CACHE = {}    # (name, px) -> Surface

# Per-alien size multipliers: most Floyds are normal-sized, some are noticeably
# bigger.  Picked per cell in _new_wave so the swarm has varied silhouettes.
# Spread so they round to distinct pixel sizes even at the smallest base scale.
_FLOYD_SIZES = (1.0, 1.0, 1.0, 1.5, 2.0)

# Currency glyphs a star can briefly flicker into, and their glow colour.
_CURRENCY = ("$", "£", "€")
_C_CURRENCY = (255, 220, 120)

# Words used by the playable "Alien Floyd's" game (Pink Floyd themed).  A random
# one is picked at the start of each game; the player shoots Floyds to make them
# drop the next needed letter, then shoots the falling letter to collect it.
_FLOYD_GAME_WORDS = ("echos", "pink", "floyd", "wish", "moon", "axe")
_C_LETTER = (255, 245, 180)        # falling collectable letter
_C_LETTER_GLOW = (255, 225, 120)
_C_WORD_DONE = (120, 255, 140)     # collected letter (bold) in the top word
_C_WORD_TODO = (120, 120, 145)     # not-yet-collected letter
_C_GAME_SCORE = (255, 220, 120)


def _make_palette_sprite(rows, palette, px):
    """Render a multi-colour bitmap *rows* at *px* pixels per cell."""
    pg = _pg
    h = len(rows)
    w = len(rows[0]) if h else 0
    surf = pg.Surface((max(1, w * px), max(1, h * px)), pg.SRCALPHA)
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            col = palette.get(ch)
            if col is not None:
                surf.fill((col[0], col[1], col[2], 255),
                          pg.Rect(c * px, r * px, px, px))
    return surf


def _make_floyd_sprite(name, px):
    """A cached Floyd sprite surface for *name* at *px* pixels per cell."""
    key = (name, px)
    spr = _FLOYD_SPRITE_CACHE.get(key)
    if spr is None:
        rows, palette = _FLOYD_PATTERNS[name]
        spr = _make_palette_sprite(rows, palette, px)
        if len(_FLOYD_SPRITE_CACHE) > 240:
            _FLOYD_SPRITE_CACHE.clear()
        _FLOYD_SPRITE_CACHE[key] = spr
    return spr


def _smoothstep(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _bezier_wave(x):
    """A soft, periodic ping-pong curve in [-1, 1] eased like a Bézier so the
    Floyds drift up and down along gentle curves rather than straight lines."""
    p = x - _math.floor(x)
    if p < 0.5:
        v = _smoothstep(p * 2.0)
    else:
        v = 1.0 - _smoothstep((p - 0.5) * 2.0)
    return v * 2.0 - 1.0


# ── "Sir Clive on his C5" player sprite ──────────────────────────────────────
# A pixel homage to the famous photo of Sir Clive Sinclair driving his Sinclair
# C5 (the white aerodynamic recumbent trike, facing left).  Drawn procedurally
# so the wheels can roll, Clive's arms can fire to the top, and the whole sprite
# flips to face right when the player moves right.  Rendered onto a 26×16 cell
# grid at *px* pixels per cell.
_CLIVE_W = 26
_CLIVE_H = 16

_CLIVE_WHITE  = (244, 244, 246)
_CLIVE_WSHADE = (206, 206, 214)
_CLIVE_YELLOW = (240, 208, 60)
_CLIVE_TYRE   = (26, 26, 30)
_CLIVE_TYRE_HL = (70, 70, 80)
_CLIVE_HUB    = (120, 120, 130)
_CLIVE_HUBDK  = (60, 60, 70)
_CLIVE_SKIN   = (232, 196, 166)
_CLIVE_SKIN_SH = (198, 158, 130)
_CLIVE_BEARD  = (208, 202, 196)
_CLIVE_SUIT   = (34, 34, 46)
_CLIVE_SCARF  = (150, 150, 164)
_CLIVE_GLASS  = (150, 220, 240)
_CLIVE_COCK   = (40, 40, 50)
_CLIVE_FLASH  = (255, 240, 150)

_CLIVE_CACHE = {}        # (px, facing, roll_q, fire_q) -> Surface
_CLIVE_ROLL_STEPS = 12   # quantised wheel angles (for caching)
_CLIVE_FIRE_STEPS = 4    # quantised arm-raise amounts


def _make_clive_c5(px, facing="left", roll=0.0, fire=0.0):
    """A cached Clive-on-C5 sprite.  *facing* is 'left' (default) or 'right';
    *roll* is the wheel angle in radians; *fire* is 0..1 (arms raised to fire)."""
    px = max(2, int(px))
    rq = int(round(roll / (_math.pi * 2.0) * _CLIVE_ROLL_STEPS)) % _CLIVE_ROLL_STEPS
    fq = max(0, min(_CLIVE_FIRE_STEPS, int(round(fire * _CLIVE_FIRE_STEPS))))
    key = (px, facing, rq, fq)
    spr = _CLIVE_CACHE.get(key)
    if spr is not None:
        return spr
    if len(_CLIVE_CACHE) > 200:
        _CLIVE_CACHE.clear()
    roll = rq / _CLIVE_ROLL_STEPS * (_math.pi * 2.0)
    fire = fq / _CLIVE_FIRE_STEPS

    pg = _pg
    surf = pg.Surface((_CLIVE_W * px, _CLIVE_H * px), pg.SRCALPHA)

    def P(cx, cy):
        return (int(round(cx * px)), int(round(cy * px)))

    def circle(cx, cy, r, col, width=0):
        pg.draw.circle(surf, col, P(cx, cy), max(1, int(round(r * px))), width)

    def line(a, b, col, w):
        pg.draw.line(surf, col, P(*a), P(*b), max(1, int(round(w * px))))

    # wheels (rear big, front small) with spinning spokes + yellow hub marker
    for (wx, wy, wr) in ((19.0, 11.0, 4.3), (4.6, 12.4, 2.3)):
        circle(wx, wy, wr, _CLIVE_TYRE)
        circle(wx, wy, wr, _CLIVE_TYRE_HL, max(1, int(round(0.35 * px))))
        circle(wx, wy, wr * 0.5, _CLIVE_HUB)
        for k in range(4):
            a = roll + k * (_math.pi / 2)
            line((wx, wy), (wx + wr * 0.46 * _math.cos(a),
                            wy + wr * 0.46 * _math.sin(a)), _CLIVE_HUBDK, 0.3)
        circle(wx + wr * 0.6 * _math.cos(roll),
               wy + wr * 0.6 * _math.sin(roll), 0.55, _CLIVE_YELLOW)

    # C5 body (white aerodynamic wedge), shade band + yellow trim
    body = [P(0.5, 11.2), P(3, 8.6), P(9.5, 5.2), P(15, 3.8), P(18.6, 4.0),
            P(20.6, 5.6), P(21.6, 9), P(21.2, 11.6), P(16, 12.6),
            P(7, 12.6), P(3.2, 12.2)]
    pg.draw.polygon(surf, _CLIVE_WHITE, body)
    line((3.4, 11.6), (20.6, 11.6), _CLIVE_WSHADE, 0.7)
    line((4, 11.0), (20, 11.0), _CLIVE_YELLOW, 0.5)
    pg.draw.polygon(surf, _CLIVE_COCK,
                    [P(9.8, 5.4), P(15, 4.4), P(17.6, 5.2), P(16.6, 9), P(10.8, 9.2)])

    # Sir Clive: suit, scarf, bald head, beard, glasses, nose
    pg.draw.polygon(surf, _CLIVE_SUIT,
                    [P(12.8, 5.2), P(16.4, 4.6), P(17.8, 6.2), P(17, 9.2), P(13.0, 9.2)])
    line((12.8, 4.8), (16.4, 4.4), _CLIVE_SCARF, 0.7)
    circle(15.4, 2.9, 1.9, _CLIVE_SKIN)
    pg.draw.polygon(surf, _CLIVE_BEARD,
                    [P(13.6, 2.8), P(15.2, 2.6), P(15.4, 4.6), P(13.9, 4.3)])
    line((14.2, 2.9), (15.7, 2.8), _CLIVE_GLASS, 0.3)
    circle(13.9, 3.1, 0.4, _CLIVE_SKIN_SH)

    # arms: idle hold the steering low/front, firing raises them to the top
    shoulder = (14.2, 6.2)
    hx = 12.0 + (13.4 - 12.0) * fire
    hy = 8.8 + (0.8 - 8.8) * fire
    line(shoulder, (hx, hy), _CLIVE_SUIT, 0.8)
    circle(hx, hy, 0.6, _CLIVE_SKIN)
    if fire > 0.5:
        circle(hx, hy - 0.9, 0.8, _CLIVE_FLASH)

    if facing == "right":
        surf = pg.transform.flip(surf, True, False)
    _CLIVE_CACHE[key] = surf
    return surf


# ── floating inflatable pink pig "mother ship" ───────────────────────────────
# A pixel homage to the Pink Floyd "Animals" balloon pig: a fat pink body with a
# snout, an eye and four dangling legs, side-on.  It floats across the top of
# the screen, bobs softly along Bézier curves, sometimes accelerates, and slowly
# inflates/deflates (its on-screen size oscillates).
_PIG_PATTERN = [
    "..........kkkk..........",
    "........kkPPPPkk........",
    ".....kkkPPPPPPPPkkk.....",
    "...kkPPPPPPPPPPPPPPkk...",
    "..kPPPPPPPPPPPPPPPPPPk..",
    ".kPPPPPPPPPPPPPPPPPPPPk.",
    ".kPPPPPPPPPPPPPPPPPPPPk.",
    "skPPPePPPPPPPPPPPPPPPPk.",
    "ssPPPPPPPPPPPPPPPPPPPPk.",
    "skPPPPPPPPPPPPPPPPPPPPk.",
    ".kPPPPPPPPPPPPPPPPPPPok.",
    "..kPPPPPPPPPPPPPPPPPok..",
    "...kkoPPPPPPPPPPPPokk...",
    "....k.kk...kk...kk.k....",
    "....k.kk...kk...kk.k....",
    ".....k.k...kk...k.k.....",
]
_PIG_PALETTE = {
    "P": (242, 150, 190),
    "o": (212, 108, 158),
    "k": (70, 35, 55),
    "s": (250, 186, 210),
    "e": (250, 250, 250),
}
_PIG_W = len(_PIG_PATTERN[0])
_PIG_H = len(_PIG_PATTERN)
_PIG_SPRITE_CACHE = {}      # px -> base Surface
_PIG_SCALED_CACHE = {}      # (px, wq, hq) -> scaled Surface


def _make_pig_sprite(px):
    px = max(1, int(px))
    spr = _PIG_SPRITE_CACHE.get(px)
    if spr is None:
        spr = _make_palette_sprite(_PIG_PATTERN, _PIG_PALETTE, px)
        _PIG_SPRITE_CACHE[px] = spr
    return spr


# ── ZX Spectrum pickup ───────────────────────────────────────────────────────
# A pixel rubber-key home computer: black slab, grey key grid and space bar.
# (The corner rainbow flash is intentionally omitted to steer clear of any
# trademark concerns.)  The pig occasionally drops one; catching it with the C5
# grants an extra life.
_ZX_PATTERN = [
    ".bbbbbbbbbbbbbbbbbbbbbb.",
    "bKKKKKKKKKKKKKKKKKKKKKKb",
    "bKwwwKKKKKKKKKKKKKKKbbKb",
    "bKKKKKKKKKKKKKKKKKKKKKKb",
    "bKdgdgdgdgdgdgdgdgdgKKKb",
    "bKdgdgdgdgdgdgdgdgdgKKKb",
    "bKdgdgdgdgdgdgdgdgdgKKKb",
    "bKdgdgdgdgdgdgdgdgdgKKKb",
    "bKKKKKKKKKKKKKKKKKKKKKKb",
    "bKKKddddddddddddKKKKKKKb",
    "bKKKddddddddddddKKKKKKKb",
    "bKKKKKKKKKKKKKKKKKKKKKKb",
    ".bbbbbbbbbbbbbbbbbbbbbb.",
]
_ZX_PALETTE = {
    "K": (24, 24, 28),
    "b": (52, 52, 60),
    "d": (74, 74, 86),
    "g": (104, 104, 120),
    "w": (205, 205, 215),
}
_ZX_SPRITE_CACHE = {}


def _make_zx_sprite(px):
    px = max(1, int(px))
    spr = _ZX_SPRITE_CACHE.get(px)
    if spr is None:
        spr = _make_palette_sprite(_ZX_PATTERN, _ZX_PALETTE, px)
        _ZX_SPRITE_CACHE[px] = spr
    return spr


# ── 8-bit Clive portrait (for the centre of the game-over prism) ─────────────
# Bald head with ginger side-hair, glasses, ginger beard, grey suit and a navy
# tie — a pixel homage to the famous Sir Clive Sinclair publicity portrait.
_CLIVE_FACE_PATTERN = [
    "....SSSSSSSS....",
    "...SSSSSSSSSS...",
    "..hSSSSSSSSSSh..",
    ".hhSSSSSSSSSShh.",
    ".hSSSSSSSSSSSSh.",
    ".hSkSSSSSSSSkSh.",
    ".hgllgSSgllgSh..",
    ".hSeSSSSSSeSSh..",
    ".hSSSSkkSSSSSh..",
    ".hbSSSSSSSSbSh..",
    ".bbbSSSSSSbbb...",
    "..bbbbbSSbbbbb..",
    "...JWWWWWWWWJ...",
    "..JJWWWTTWWWJJ..",
    ".JJjWWWTTWWWjJJ.",
    ".JJJjWWTTWWjJJJ.",
    "JJJJjWWTTWWjJJJJ",
    "JJJJJjWTTWjJJJJJ",
]
_CLIVE_FACE_PALETTE = {
    "S": (232, 196, 166), "s": (198, 158, 130),
    "h": (170, 105, 65), "b": (185, 120, 75),
    "g": (215, 215, 225), "l": (170, 205, 225),
    "e": (55, 45, 40), "k": (90, 60, 45),
    "J": (145, 145, 150), "j": (115, 115, 122),
    "W": (212, 216, 226), "T": (45, 50, 95),
}
_CLIVE_FACE_W = len(_CLIVE_FACE_PATTERN[0])
_CLIVE_FACE_H = len(_CLIVE_FACE_PATTERN)
_CLIVE_FACE_CACHE = {}


def _make_clive_face(px):
    px = max(1, int(px))
    spr = _CLIVE_FACE_CACHE.get(px)
    if spr is None:
        spr = _make_palette_sprite(_CLIVE_FACE_PATTERN, _CLIVE_FACE_PALETTE, px)
        _CLIVE_FACE_CACHE[px] = spr
    return spr


# ── pixel asteroids ──────────────────────────────────────────────────────────
# Two cratered rock shapes; rendered at varied pixel sizes and rotated per frame
# so they appear to roll/tumble as they fall down the screen.  Shootable for +1.
_AST_PATTERNS = [
    [
        "....RRRRRR....",
        "..RRRRRRRRLL..",
        ".RRRkkRRRRRLL.",
        ".RRRkkRRRRRRL.",
        "RRRRRRRRRRRRRR",
        "RRRRRRRRkkRRRR",
        "LRRRRRRRkkRRRR",
        "LLRRRRRRRRRRRr",
        ".LLRRRRRRRRRr.",
        ".RRRRRkkRRRRr.",
        "..RRRRkkRRRr..",
        "....RRRRRR....",
    ],
    [
        "...RRRRRR...",
        ".RRRRRRRRLL.",
        ".RRRkkRRRLLR",
        "RRRkkRRRRRRR",
        "RRRRRRRRRRRR",
        "LRRRRRRkkRRR",
        "LLRRRRRkkRRr",
        ".LRRRRRRRRr.",
        ".RRRkkRRRRr.",
        "..RRkkRRRr..",
        "...RRRRRr...",
    ],
]
_AST_PALETTE = {
    "R": (112, 106, 99),
    "r": (74, 70, 65),
    "L": (150, 144, 135),
    "k": (54, 50, 48),
}
_AST_SPRITE_CACHE = {}      # (shape, px) -> base Surface
_AST_ROT_CACHE = {}         # (shape, px, angq) -> rotated Surface


def _make_asteroid_base(shape, px):
    key = (shape, max(1, int(px)))
    s = _AST_SPRITE_CACHE.get(key)
    if s is None:
        s = _make_palette_sprite(_AST_PATTERNS[shape], _AST_PALETTE, key[1])
        _AST_SPRITE_CACHE[key] = s
    return s


def _make_asteroid_sprite(shape, px, ang):
    angq = int(round(ang / 15.0)) * 15 % 360
    key = (shape, max(1, int(px)), angq)
    s = _AST_ROT_CACHE.get(key)
    if s is None:
        if len(_AST_ROT_CACHE) > 400:
            _AST_ROT_CACHE.clear()
        s = _pg.transform.rotate(_make_asteroid_base(shape, px), angq)
        _AST_ROT_CACHE[key] = s
    return s


class AlienFloydBackground:
    """An autoplaying "Alien Floyd" scene — a Pink Floyd homage built on the
    classic Space-Invaders skeleton: a drifting/twinkling starfield whose stars
    sometimes flicker into glowing $/£/€ glyphs; a descending formation of alien
    "Floyds" (pigs, moons, prisms, holograms, guitars, dogs, beds, clouds, …)
    that bob down along soft Bézier curves and randomly turn into one another;
    an AI cannon that tracks and fires often; bullets, bombs, the occasional
    mystery UFO and explosion bursts.

    Rendered opaque by default; pass ``transparent=True`` to :meth:`render` to
    draw only the sprites/stars over a fully transparent surface (used as an
    overlay above gallery item images)."""

    ROWS = 5
    COLS = 9

    def __init__(self, size, dpr=1.0, game=False):
        _ensure_pg()
        self.dpr = max(1.0, float(dpr or 1.0))
        self.w, self.h = size
        self._t = 0
        self._init_sprites()
        self._init_stars()
        self._bullets = []
        self._sparks = []          # colourful muzzle "shooting stars"
        self._bombs = []
        self._explosions = []
        self._divers = []
        self._dive_cd = _random.randint(20, 60)
        self._score = 0
        self._ship_x = self.w * 0.5
        self._ship_v = 0.0
        self._fire_cd = 0
        # Clive/C5 visual state: facing, rolling wheel angle, firing animation.
        self._ship_prev_x = self._ship_x
        self._ship_facing = "left"
        self._ship_roll = 0.0
        self._ship_fire_anim = 0
        self._pig = None                       # floating inflatable pig (dict)
        self._pig_cd = _random.randint(120, 360)
        # Playable-game state (only the dedicated "Alien Floyd's" tab sets this).
        self._game = bool(game)
        # The playable game opens on a title/attract screen and only begins once
        # the player presses Space; the auto-playing scenes are always "started".
        self._started = not self._game
        self._k_left = self._k_right = self._k_fire = False
        self._word = ""
        self._got = []                # per-position: which letters are collected
        self._letters = []            # falling collectable letters
        self._game_speed_bonus = 0.0  # accumulates each time a word is completed
        self._win_flash = 0           # frames left to flash the "complete" banner
        self._lives = 3               # lives left (a bomb hit costs one)
        self._ship_invuln = 0         # blinking grace frames after a hit
        self._words_done = 0          # words completed this game
        self._drops = []              # falling ZX Spectrum pickups (extra life)
        self._life_flash = 0          # frames left to flash "EXTRA LIFE"
        self._asteroids = []          # rolling pixel asteroids (shoot for +1)
        self._ast_cd = _random.randint(30, 90)
        # Game-over score screen state.
        self._gameover = False
        self._gameover_t = 0
        self._prev_fire = False
        self._final_score = 0
        self._final_wave = 1
        self._go_clives = []          # Clives flying Bézier arcs on the screen
        # New-high-score name entry: shoot the live letter to cycle A→B→…, the
        # <NEXT> target to lock it in, and <END> to finish.  Only offered when a
        # run earns a place on the high-score table (see alien_score_qualifies).
        self._name_entry = False
        self._name = ""               # committed letters so far
        self._name_letter = 0         # current live letter (0 == 'A')
        self._name_max = 8
        self._final_name = ""         # name entered for this game-over screen
        self._name_flash = 0          # brief feedback flash (e.g. name full)
        self._new_wave(first=True)
        if self._game:
            self._start_game()

    # -- setup -------------------------------------------------------------
    def s(self, px):
        return int(round(px * self.dpr))

    def _init_sprites(self):
        px = max(2, self.s(2))
        self._px = px
        self._floyd = {name: _make_floyd_sprite(name, px)
                       for name in _FLOYD_NAMES}
        self._alien_w = _FLOYD_W * px
        self._alien_h = _FLOYD_H * px
        self._cannon = _make_sprite(_CANNON, _C_SHIP, px)   # used for LIVES icons
        # Inflatable pig "mother ship": a base sprite that is smooth-scaled per
        # frame for the inflate/deflate effect.
        self._pig_base = _make_pig_sprite(max(2, self.s(2)))
        self._pig_bw = self._pig_base.get_width()
        self._pig_bh = self._pig_base.get_height()
        # The defending ship is "Sir Clive on his C5".
        self._ship_px = px
        self._ship_w = _CLIVE_W * px
        self._ship_h = _CLIVE_H * px
        self._ship_top = self.h - self._ship_h - self.s(8)
        # Extra spacing so the larger Floyds don't overlap their neighbours.
        self._step_x = self._alien_w + self.s(20)
        self._step_y = self._alien_h + self.s(14)
        self._bob_amp = self.s(11)

    def _init_stars(self):
        self._stars = []
        area = max(1, self.w * self.h)
        n = max(60, min(200, area // self.s(8000)))
        radii = [self.s(2), self.s(3), self.s(4)]
        for _ in range(n):
            self._stars.append({
                "x": _random.uniform(0, self.w),
                "y": _random.uniform(0, self.h),
                "spd": _random.uniform(0.15, 1.1) * self.dpr,
                "r": _random.choice(radii),
                "col": _random.choice(_STAR_COLORS),
                "ph": _random.uniform(0, 6.28),
                "dph": _random.uniform(0.03, 0.10),
                "glyph": None,
                "gttl": 0,
            })

    def _new_wave(self, first=False):
        # Lay the columns out within a clear left/right margin so the swarm has
        # breathing room at the edges instead of spilling across the whole width.
        margin = self.s(44)
        col_w = max(self._alien_w + self.s(6), (self.w - 2 * margin) / self.COLS)
        self._step_x = col_w
        self._start_x = margin + 0.5 * col_w - self._alien_w / 2
        self._top_y = self.s(20)
        # Aliens are no longer all present from the start: every cell begins
        # off-screen and is scheduled to appear at the top in small batches
        # (see _schedule_spawn_batches), so a wave builds up gradually.
        self._aliens = [[False] * self.COLS for _ in range(self.ROWS)]
        # Each alien is one of the Floyd kinds, remembered per cell so it can
        # randomly "turn into" another over time.  Per-cell phases stagger the
        # soft Bézier bob so the swarm undulates rather than moving as a block.
        nkinds = len(_FLOYD_NAMES)
        self._kind = [[_random.randrange(nkinds) for _ in range(self.COLS)]
                      for _ in range(self.ROWS)]
        # Per-cell size multiplier (some Floyds are bigger than others).
        self._scale = [[_random.choice(_FLOYD_SIZES) for _ in range(self.COLS)]
                       for _ in range(self.ROWS)]
        # Tighter scatter: a small horizontal jitter (keeps the side margins
        # clear) plus a modest vertical spread so cells reach the bottom — and
        # wrap back to the top — at staggered times rather than all together.
        self._scatter = [[(_random.uniform(-self._step_x * 0.14, self._step_x * 0.14),
                           _random.uniform(-self.s(8), self.s(34)))
                          for _ in range(self.COLS)]
                         for _ in range(self.ROWS)]
        self._bob_ph = [[_random.uniform(0.0, 1.0) for _ in range(self.COLS)]
                        for _ in range(self.ROWS)]
        # Per-cell vertical offset (replaces the old shared _fy): each Floyd
        # drifts down and wraps to the top independently.
        self._cell_dy = [[0.0 for _ in range(self.COLS)] for _ in range(self.ROWS)]
        # Per-cell respawn schedule: the frame at which a dead cell should
        # (re)appear at the top, or None to stay gone (e.g. after being shot).
        self._respawn = [[None for _ in range(self.COLS)] for _ in range(self.ROWS)]
        self._schedule_spawn_batches(first=first)
        self._fx = 0.0
        self._fy = 0.0
        self._dir = 1
        base = 0.4 if first else 0.55
        self._speed = ((base + 0.1 * getattr(self, "_wave", 0))
                       + getattr(self, "_game_speed_bonus", 0.0)) * self.dpr
        self._wave = getattr(self, "_wave", 0) + 1

    def _schedule_spawn_batches(self, first=False):
        """Schedule every cell of the new wave to appear at the top in small,
        staggered batches so the swarm builds up a few Floyds at a time."""
        cells = [(r, c) for r in range(self.ROWS) for c in range(self.COLS)]
        _random.shuffle(cells)
        batch = 4                       # how many appear together
        interval = 12                   # frames between successive batches
        start = self._t + (12 if first else 6)
        for i, (r, c) in enumerate(cells):
            self._respawn[r][c] = start + (i // batch) * interval

    def _process_spawns(self):
        """Bring any cells whose scheduled time has arrived onto the screen at
        the top (initial batch spawn-in and bottom-wrap respawns alike)."""
        for r in range(self.ROWS):
            for c in range(self.COLS):
                t0 = self._respawn[r][c]
                if t0 is not None and not self._aliens[r][c] and self._t >= t0:
                    self._aliens[r][c] = True
                    self._cell_dy[r][c] = 0.0
                    self._respawn[r][c] = None

    def _descend_and_wrap(self):
        """Drift the alive Floyds gently downward; any that reach the bottom
        disappear and are scheduled to re-enter at the top (no mass reset)."""
        descend = (1.5 + 0.12 * min(getattr(self, "_wave", 1), 8)) * self.dpr
        # In the playable game the swarm presses all the way down onto the C5
        # (so reaching it is dangerous); the auto-playing scene keeps a clear
        # band above the cannon for a tidier look.
        bottom = self.h - self.s(8) if self._game else self.h - self.s(40)
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                self._cell_dy[r][c] += descend
                _x, y = self._alien_pos(r, c)
                _w, h = self._alien_wh(r, c)
                if y + h >= bottom:
                    self._aliens[r][c] = False
                    self._cell_dy[r][c] = 0.0
                    self._respawn[r][c] = self._t + _random.randint(12, 40)

    def resize(self, size, dpr=None):
        self.w, self.h = size
        if dpr:
            self.dpr = max(1.0, float(dpr))
        self._init_sprites()
        self._init_stars()
        self._new_wave(first=True)
        self._wave = 1
        self._divers = []
        # Re-centre the C5 (the first surface is tiny before the widget is laid
        # out, so a preserved x would otherwise leave it stuck bottom-left).
        self._ship_x = self.w * 0.5
        self._ship_prev_x = self._ship_x

    # -- geometry ----------------------------------------------------------
    def _px_for_scale(self, scale):
        return max(2, int(round(self._px * scale)))

    def _alien_wh(self, r, c):
        """Pixel size of the (possibly enlarged) Floyd in cell (r, c)."""
        px = self._px_for_scale(self._scale[r][c])
        return _FLOYD_W * px, _FLOYD_H * px

    def _alien_pos(self, r, c):
        sdx, sdy = self._scatter[r][c]
        bx = self._start_x + c * self._step_x + self._fx + sdx
        by = self._top_y + r * self._step_y + self._cell_dy[r][c] + sdy
        # Soft Bézier bob: a gentle, eased up/down drift unique to each cell.
        bob = self._bob_amp * _bezier_wave(self._t * 0.012
                                           + self._bob_ph[r][c]
                                           + (r + c) * 0.13)
        # Bigger Floyds are centred on their base cell so the grid stays even.
        w, h = self._alien_wh(r, c)
        cx = bx + self._alien_w / 2
        cy = by + bob + self._alien_h / 2
        return (cx - w / 2, cy - h / 2)

    def _alive_bounds(self):
        minx, maxx, maxy = None, None, None
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                x, y = self._alien_pos(r, c)
                w, h = self._alien_wh(r, c)
                minx = x if minx is None else min(minx, x)
                maxx = x + w if maxx is None else max(maxx, x + w)
                maxy = y + h if maxy is None else max(maxy, y + h)
        return minx, maxx, maxy

    def _any_alive(self):
        """True while any Floyd is on screen *or* still scheduled to (re)appear,
        so a wave isn't declared cleared while batches are still spawning in."""
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if self._aliens[r][c] or self._respawn[r][c] is not None:
                    return True
        return False

    # -- simulation --------------------------------------------------------
    def update(self):
        self._t += 1
        if self._game and not self._started:
            self._update_title()
            return
        if self._game and self._gameover:
            if self._name_entry:
                self._update_name_entry()
            else:
                self._update_gameover()
            return
        self._update_stars()
        self._update_formation()
        self._update_divers()
        if self._game:
            self._update_ship_player()
            self._update_letters()
            self._update_drops()
            self._update_asteroids()
            # A Floyd (in formation or diving) touching the C5 destroys it.
            self._check_ship_collisions()
            if self._win_flash > 0:
                self._win_flash -= 1
            if self._life_flash > 0:
                self._life_flash -= 1
            if self._ship_invuln > 0:
                self._ship_invuln -= 1
        else:
            self._update_ship()
        self._update_ship_visual()
        self._update_bullets()
        self._update_sparks()
        self._update_bombs()
        self._update_pig()
        self._update_explosions()

    def _update_stars(self):
        for st in self._stars:
            st["y"] += st["spd"]
            st["ph"] += st["dph"]
            if st["y"] > self.h + st["r"]:
                st["y"] = -st["r"]
                st["x"] = _random.uniform(0, self.w)
            # A star occasionally flickers into a glowing $/£/€ sign.
            if st["glyph"] is not None:
                st["gttl"] -= 1
                if st["gttl"] <= 0:
                    st["glyph"] = None
            elif _random.random() < 0.0006:
                st["glyph"] = _random.choice(_CURRENCY)
                st["gttl"] = _random.randint(80, 180)

    def _update_formation(self):
        # Spawn-in scheduled cells (initial batches + bottom-wrap respawns).
        self._process_spawns()
        # The wave is only cleared once nothing is alive, nothing is pending to
        # (re)appear, and no Floyds are still diving.
        if not self._any_alive() and not self._divers:
            self._new_wave()
            return
        minx, maxx, _maxy = self._alive_bounds()
        margin = self.s(28)            # keep a clear margin off the side edges
        self._fx += self._dir * self._speed
        if maxx is not None and (maxx >= self.w - margin or minx <= margin):
            self._dir *= -1
        # Each Floyd drifts down on its own and wraps individually at the bottom
        # (disappears, then re-enters at the top) — no synchronised mass reset.
        self._descend_and_wrap()
        # Occasionally an alien drops a bomb.
        if self._t % 40 == 0:
            alive = [(r, c) for r in range(self.ROWS) for c in range(self.COLS)
                     if self._aliens[r][c]]
            if alive:
                r, c = _random.choice(alive)
                x, y = self._alien_pos(r, c)
                w, h = self._alien_wh(r, c)
                self._bombs.append([x + w / 2, y + h])
        # Pink Floyd homage: aliens randomly "turn into" other Floyds.
        if self._t % 16 == 0:
            alive = [(r, c) for r in range(self.ROWS) for c in range(self.COLS)
                     if self._aliens[r][c]]
            if alive:
                nkinds = len(_FLOYD_NAMES)
                for _ in range(max(1, len(alive) // 10)):
                    r, c = _random.choice(alive)
                    self._kind[r][c] = _random.randrange(nkinds)

    def _lowest_alien_x(self):
        """Target the column of the lowest-then-leftmost alive alien."""
        best = None
        for c in range(self.COLS):
            for r in range(self.ROWS - 1, -1, -1):
                if self._aliens[r][c]:
                    x, _y = self._alien_pos(r, c)
                    w, _h = self._alien_wh(r, c)
                    cx = x + w / 2
                    if best is None:
                        best = cx
                    break
        return best

    def _update_ship(self):
        target = self._lowest_alien_x()
        if target is None:
            target = self.w * 0.5
        # Also consider intercepting the floating pig when one is on screen.
        aim = target
        if self._pig is not None:
            px, py, pw, ph = self._pig_rect()
            ux = px + pw / 2
            if abs(ux - self._ship_x) < abs(target - self._ship_x):
                aim = ux
        # A diving Floyd takes priority — the ship rushes under the nearest one
        # to shoot it down before it escapes off the bottom.
        if self._divers:
            aim = min((self._diver_pos(d)[0] + self._alien_w / 2
                       for d in self._divers),
                      key=lambda dx: abs(dx - self._ship_x))
        # Velocity + acceleration: a spring pulls the ship toward the aim while
        # a capped max speed and light damping give it weighty, accelerating
        # left/right sweeps that overshoot and settle rather than gliding
        # linearly — the ship darts back and forth across the bottom.
        accel = (aim - self._ship_x) * 0.05 * self.dpr
        max_a = max(1.0, self.s(3))
        accel = max(-max_a, min(max_a, accel))
        self._ship_v += accel
        self._ship_v *= 0.90                          # damping
        max_v = max(2.0, self.s(16))                  # fast top speed
        self._ship_v = max(-max_v, min(max_v, self._ship_v))
        self._ship_x += self._ship_v
        # Bounce off the edges so it keeps sweeping side to side.
        lo, hi = self.s(8), self.w - self.s(8)
        if self._ship_x < lo:
            self._ship_x = lo
            self._ship_v = abs(self._ship_v) * 0.5
        elif self._ship_x > hi:
            self._ship_x = hi
            self._ship_v = -abs(self._ship_v) * 0.5
        # The defending ship fires often: short cooldown and a generous aim
        # tolerance so the sky stays busy with bullets.
        if self._fire_cd > 0:
            self._fire_cd -= 1
        elif abs(self._ship_x - aim) < self.s(46) or self._pig is not None \
                or self._divers:
            self._fire_bullet()
            self._fire_cd = _random.randint(4, 10)

    # -- playable game -----------------------------------------------------
    def set_key(self, name, down):
        """Set a player input flag (``name`` is 'left', 'right' or 'fire')."""
        if name == "left":
            self._k_left = bool(down)
        elif name == "right":
            self._k_right = bool(down)
        elif name == "fire":
            self._k_fire = bool(down)

    def _start_game(self, accelerate=False):
        """Begin a fresh game: pick a new random word, clear collected letters.
        When *accelerate* the Floyds get a permanent speed boost for this and all
        following words."""
        if accelerate:
            self._game_speed_bonus += 0.35
        self._word = _random.choice(_FLOYD_GAME_WORDS).upper()
        self._got = [False] * len(self._word)
        self._letters = []

    def _update_ship_player(self):
        """Player-controlled ship with momentum: cursor keys *accelerate* the C5
        rather than moving it a fixed step, so the longer a key is held the
        faster it goes (up to a cap), and releasing lets it coast and brake to a
        stop instead of stopping dead.  This gives weighty, non-linear steering."""
        thrust = 1.3 * self.dpr            # acceleration per frame while held
        max_v = 11.0 * self.dpr            # top speed
        left = self._k_left and not self._k_right
        right = self._k_right and not self._k_left
        if left:
            self._ship_v -= thrust
        elif right:
            self._ship_v += thrust
        # Friction: gentle while thrusting so speed builds up the longer you
        # hold; firmer when nothing is pressed so the C5 brakes smoothly.
        self._ship_v *= 0.90 if (left or right) else 0.80
        if abs(self._ship_v) < 0.15:
            self._ship_v = 0.0
        self._ship_v = max(-max_v, min(max_v, self._ship_v))
        self._ship_x += self._ship_v
        lo, hi = self.s(8), self.w - self.s(8)
        if self._ship_x < lo:
            self._ship_x = lo
            self._ship_v = abs(self._ship_v) * 0.3      # soft bounce off the wall
        elif self._ship_x > hi:
            self._ship_x = hi
            self._ship_v = -abs(self._ship_v) * 0.3
        if self._fire_cd > 0:
            self._fire_cd -= 1
        if self._k_fire and self._fire_cd <= 0:
            self._fire_bullet()
            self._fire_cd = 9

    def _fire_bullet(self):
        """Fire from Clive's hands (top of the ship) and kick off the arm-raise
        firing animation, with a little burst of colourful star sparks."""
        muzzle_y = self._ship_top + self.s(2)
        self._bullets.append([self._ship_x, muzzle_y])
        self._spawn_fire_sparks(self._ship_x, muzzle_y)
        self._ship_fire_anim = 10

    def _spawn_fire_sparks(self, x, y):
        """Emit a small spray of twinkling, colourful star sparks at the muzzle,
        fanning mostly upward (in the direction of fire) with a touch of drift."""
        for _ in range(_random.randint(3, 5)):
            ang = -_math.pi / 2 + _random.uniform(-1.0, 1.0)   # mostly upward
            spd = _random.uniform(1.2, 3.6) * self.dpr
            self._sparks.append({
                "x": x, "y": y,
                "vx": _math.cos(ang) * spd,
                "vy": _math.sin(ang) * spd,
                "age": 0,
                "life": _random.randint(10, 20),
                "col": _random.choice(_C_SPARK_COLORS),
                "rot": _random.uniform(0, 6.28),
                "drot": _random.uniform(-0.45, 0.45),
                "r": _random.uniform(1.8, 3.4) * self.dpr,
            })

    def _update_sparks(self):
        kept = []
        for sp in self._sparks:
            sp["x"] += sp["vx"]
            sp["y"] += sp["vy"]
            sp["vy"] += 0.06 * self.dpr        # gentle gravity so they arc
            sp["vx"] *= 0.96
            sp["rot"] += sp["drot"]
            sp["age"] += 1
            if sp["age"] < sp["life"]:
                kept.append(sp)
        self._sparks = kept

    def _render_sparks(self, surface):
        pg = _pg
        for sp in self._sparks:
            fade = max(0.0, 1.0 - sp["age"] / max(1, sp["life"]))
            col = (int(sp["col"][0] * fade), int(sp["col"][1] * fade),
                   int(sp["col"][2] * fade))
            x, y = sp["x"], sp["y"]
            r = sp["r"] * (0.6 + 0.6 * fade)
            a = sp["rot"]
            # 4-pointed sparkle: alternating long/short points around the centre.
            pts = []
            for k in range(8):
                ang = a + k * (_math.pi / 4)
                rad = r if k % 2 == 0 else r * 0.4
                pts.append((int(x + rad * _math.cos(ang)),
                            int(y + rad * _math.sin(ang))))
            pg.draw.polygon(surface, col, pts)

    def _update_ship_visual(self):
        """Update the C5's facing (flip on direction) and rolling-wheel angle."""
        dx = self._ship_x - self._ship_prev_x
        self._ship_prev_x = self._ship_x
        if dx > 0.3:
            self._ship_facing = "right"
        elif dx < -0.3:
            self._ship_facing = "left"
        self._ship_roll += dx / max(1.0, self._ship_px * 5.0)
        if self._ship_fire_anim > 0:
            self._ship_fire_anim -= 1

    def _drop_letter(self, x, y):
        """A hit Floyd drops a random still-needed letter of the word.  A letter
        that was missed (never collected) simply stays needed, so it can be
        dropped again the next time a Floyd or UFO is shot."""
        if not self._game or len(self._letters) >= 4:
            return
        # Candidate positions: not yet collected and not already falling.
        falling = {lt["idx"] for lt in self._letters}
        rem = [i for i, g in enumerate(self._got) if not g and i not in falling]
        if not rem:
            return
        idx = _random.choice(rem)
        self._letters.append({"x": x, "y": y, "ch": self._word[idx], "idx": idx})

    def _update_letters(self):
        spd = self.s(2)
        kept = []
        for lt in self._letters:
            lt["y"] += spd
            if lt["y"] < self.h - self.s(28):
                kept.append(lt)
        self._letters = kept

    def _collect_letter(self, lt):
        """The player shot a falling letter: collect its word position, scoring
        +10 (and +15 on completing the whole word)."""
        try:
            self._letters.remove(lt)
        except ValueError:
            pass
        self._spawn_explosion(lt["x"], lt["y"], _C_LETTER_GLOW)
        idx = lt["idx"]
        if 0 <= idx < len(self._got) and not self._got[idx]:
            self._got[idx] = True
            self._score += 10
            if all(self._got):
                self._score += 15            # word-completion bonus
                self._words_done += 1
                self._start_game(accelerate=True)
                self._new_wave()             # fresh, faster swarm
                self._win_flash = 90

    def _update_bullets(self):
        spd = self.s(7)
        alive_bullets = []
        for b in self._bullets:
            b[1] -= spd
            if b[1] < -self.s(4):
                continue
            hit = False
            # In game mode, falling letters are shootable to collect them.
            if self._game:
                for lt in list(self._letters):
                    if abs(b[0] - lt["x"]) <= self.s(11) \
                            and abs(b[1] - lt["y"]) <= self.s(12):
                        self._collect_letter(lt)
                        hit = True
                        break
            # Asteroids are shootable for +1 point.
            if self._game and not hit:
                for a in list(self._asteroids):
                    if abs(b[0] - a["x"]) <= a["r"] and abs(b[1] - a["y"]) <= a["r"]:
                        self._spawn_explosion(a["x"], a["y"], (160, 154, 145))
                        try:
                            self._asteroids.remove(a)
                        except ValueError:
                            pass
                        self._score += 1
                        hit = True
                        break
            for r in range(self.ROWS):
                if hit:
                    break
                for c in range(self.COLS):
                    if not self._aliens[r][c]:
                        continue
                    x, y = self._alien_pos(r, c)
                    w, h = self._alien_wh(r, c)
                    if x <= b[0] <= x + w and y <= b[1] <= y + h:
                        self._aliens[r][c] = False
                        self._spawn_explosion(x + w / 2, y + h / 2,
                                              _ALIEN_ROW_COLORS[r])
                        if self._game:
                            self._drop_letter(x + w / 2, y + h)
                        else:
                            self._score += 10
                        hit = True
                        break
                if hit:
                    break
            # diving Floyds are destroyable too
            if not hit:
                for d in list(self._divers):
                    dx, dy = self._diver_pos(d)
                    dw, dh = self._diver_wh(d)
                    if dx <= b[0] <= dx + dw and dy <= b[1] <= dy + dh:
                        self._spawn_explosion(dx + dw / 2, dy + dh / 2, _C_UFO)
                        try:
                            self._divers.remove(d)
                        except ValueError:
                            pass
                        if self._game:
                            self._drop_letter(dx + dw / 2, dy + dh)
                        else:
                            self._score += 50
                        hit = True
                        break
            if not hit and self._pig is not None:
                # Hit-box tracks the pig's *current* inflated size/position, with
                # a small inset so the rounded body (not transparent corners)
                # counts.  Shooting the pig is a +20 bonus.
                px, py, pw, ph = self._pig_rect()
                ix = pw * 0.12
                iy = ph * 0.12
                if px + ix <= b[0] <= px + pw - ix \
                        and py + iy <= b[1] <= py + ph - iy:
                    self._spawn_explosion(px + pw / 2, py + ph / 2, _C_PIG)
                    self._pig = None
                    self._pig_cd = _random.randint(260, 560)
                    self._score += 20
                    hit = True
            if not hit:
                alive_bullets.append(b)
        self._bullets = alive_bullets

    def _update_bombs(self):
        spd = self.s(3)
        kept = []
        # Ship hit-box (game mode): a bomb whose tip enters the C5's body costs
        # a life.  The bomb is a vertical streak from bm[1] (tip) to bm[1]+s(7).
        sl, st_, sr, sb = self._ship_rect()
        for bm in self._bombs:
            bm[1] += spd
            if self._game and self._ship_invuln <= 0 \
                    and sl <= bm[0] <= sr \
                    and bm[1] + self.s(7) >= st_ and bm[1] <= sb:
                self._ship_hit()
                continue
            if bm[1] >= self.h - self.s(20):
                self._spawn_explosion(bm[0], self.h - self.s(22), _C_BOMB)
                continue
            kept.append(bm)
        self._bombs = kept

    def _ship_rect(self):
        """The C5's collision rectangle (inset from the sprite so only solid
        bodywork, not the transparent corners, counts)."""
        inset = self.s(3)
        return (self._ship_x - self._ship_w / 2 + inset,
                self._ship_top + inset,
                self._ship_x + self._ship_w / 2 - inset,
                self._ship_top + self._ship_h - inset)

    def _check_ship_collisions(self):
        """Lose a life if a Floyd — in formation or diving — overlaps the C5.
        The colliding Floyd is destroyed along with the ship."""
        if self._ship_invuln > 0:
            return
        sl, st_, sr, sb = self._ship_rect()

        def hits(x, y, w, h):
            ix, iy = w * 0.16, h * 0.16     # ignore the sprite's clear margins
            return (x + ix < sr and x + w - ix > sl
                    and y + iy < sb and y + h - iy > st_)

        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                x, y = self._alien_pos(r, c)
                w, h = self._alien_wh(r, c)
                if hits(x, y, w, h):
                    self._aliens[r][c] = False
                    self._respawn[r][c] = self._t + _random.randint(12, 40)
                    self._spawn_explosion(x + w / 2, y + h / 2,
                                          _ALIEN_ROW_COLORS[r])
                    self._ship_hit()
                    return
        for d in list(self._divers):
            dx, dy = self._diver_pos(d)
            dw, dh = self._diver_wh(d)
            if hits(dx, dy, dw, dh):
                try:
                    self._divers.remove(d)
                except ValueError:
                    pass
                self._spawn_explosion(dx + dw / 2, dy + dh / 2, _C_UFO)
                self._ship_hit()
                return

    def _ship_hit(self):
        """The C5 is destroyed (alien bomb or a Floyd collision): it explodes in
        a colourful burst and the player loses a life (game over at zero)."""
        self._explode_ship()
        self._lives -= 1
        self._ship_invuln = 90        # ~3s of blinking grace before it returns
        if self._lives <= 0:
            self._enter_gameover()

    def _explode_ship(self):
        """A dramatic multi-burst explosion centred on the C5."""
        cx = self._ship_x
        cy = self._ship_top + self._ship_h / 2
        for col in (_C_SHIP, (255, 240, 150), (255, 255, 255), (255, 140, 60),
                    (255, 90, 70)):
            ox = _random.uniform(-self._ship_w * 0.35, self._ship_w * 0.35)
            oy = _random.uniform(-self._ship_h * 0.35, self._ship_h * 0.35)
            self._spawn_explosion(cx + ox, cy + oy, col)
        # a radial spray of twinkling sparks for extra drama
        for _ in range(_random.randint(12, 18)):
            ang = _random.uniform(0, 2 * _math.pi)
            spd = _random.uniform(1.8, 5.0) * self.dpr
            self._sparks.append({
                "x": cx, "y": cy,
                "vx": _math.cos(ang) * spd,
                "vy": _math.sin(ang) * spd,
                "age": 0,
                "life": _random.randint(14, 26),
                "col": _random.choice(_C_SPARK_COLORS),
                "rot": _random.uniform(0, 6.28),
                "drot": _random.uniform(-0.5, 0.5),
                "r": _random.uniform(2.0, 4.0) * self.dpr,
            })

    def _enter_gameover(self):
        """All lives lost — freeze the game and show the score screen."""
        self._gameover = True
        self._gameover_t = 0
        self._prev_fire = self._k_fire     # require a fresh press to continue
        self._final_score = self._score
        self._final_wave = getattr(self, "_wave", 1)
        self._bombs = []
        self._bullets = []
        self._sparks = []
        self._letters = []
        self._drops = []
        self._asteroids = []
        # Earning a place on the high-score table grants a name-entry round
        # (spell your name by shooting); otherwise go straight to the table.
        self._name_entry = alien_score_qualifies(self._final_score)
        if self._name_entry:
            self._begin_name_entry()
        else:
            self._final_name = ""
            self._spawn_go_squadron()

    def _spawn_go_squadron(self):
        """A squadron of Clives flying their C5s across the score screen."""
        self._go_clives = []
        for _ in range(5):
            cl = self._spawn_flying_clive()
            cl["t"] = _random.uniform(0.0, 0.9)   # stagger so they spread out
            cl["pos"] = self._bezier_point(cl, cl["t"])
            cl["prev"] = cl["pos"]
            self._go_clives.append(cl)

    # -- new-high-score name entry -----------------------------------------
    def _begin_name_entry(self):
        """Set up the interactive name-entry round: the C5 stays controllable
        and fires up at three floating targets (the live letter, <NEXT>, <END>)."""
        self._name = ""
        self._name_letter = 0
        self._final_name = ""
        self._name_flash = 0
        self._go_clives = []
        self._ship_x = self.w * 0.5
        self._ship_prev_x = self._ship_x
        self._ship_v = 0.0
        self._ship_invuln = 0
        # Raise the C5 up near the targets (just below the instructions) so
        # spelling your name doesn't mean shooting all the way up the screen.
        self._ship_top = int(self.h * 0.66)
        self._bullets = []
        self._sparks = []
        self._explosions = []
        self._prev_fire = self._k_fire     # require a fresh press before firing

    def _name_targets(self):
        """Current rects of the three shootable targets, laid out centred."""
        pg = _pg
        f = _font(self.s(18), bold=True)
        cur = chr(ord("A") + self._name_letter)
        specs = [("letter", cur), ("next", "NEXT"), ("end", "END")]
        pad = self.s(14)
        # Size the letter box to the widest glyph so it doesn't jump as it cycles.
        widths = [(f.size("W")[0] if tid == "letter" else f.size(label)[0]) + pad * 2
                  for tid, label in specs]
        gap = self.s(22)
        total = sum(widths) + gap * (len(specs) - 1)
        x = (self.w - total) // 2
        bh = self.s(36)
        y = int(self.h * 0.46)
        boxes = []
        for (tid, label), bw in zip(specs, widths):
            boxes.append({"id": tid, "label": label,
                          "rect": pg.Rect(int(x), y, int(bw), bh)})
            x += bw + gap
        return boxes

    def _update_name_entry(self):
        self._gameover_t += 1
        self._update_stars()
        self._update_ship_player()         # move + fire (fills self._bullets)
        self._update_ship_visual()
        self._update_name_bullets()
        self._update_sparks()
        self._update_explosions()
        if self._name_flash > 0:
            self._name_flash -= 1

    def _update_name_bullets(self):
        """Move the C5's bullets up; a bullet that reaches a target triggers it."""
        spd = self.s(7)
        boxes = self._name_targets()
        kept = []
        for b in self._bullets:
            b[1] -= spd
            if b[1] < -self.s(4):
                continue
            hit = False
            for box in boxes:
                if box["rect"].collidepoint(b[0], b[1]):
                    self._hit_name_target(box["id"], box["rect"])
                    hit = True
                    break
            if not hit:
                kept.append(b)
        self._bullets = kept

    def _hit_name_target(self, tid, rect):
        cx, cy = rect.center
        if tid == "letter":
            self._name_letter = (self._name_letter + 1) % 26
            self._spawn_explosion(cx, cy, _C_LETTER_GLOW)
            self._spawn_fire_sparks(cx, cy)
        elif tid == "next":
            if len(self._name) < self._name_max:
                self._name += chr(ord("A") + self._name_letter)
                self._name_letter = 0
                self._spawn_explosion(cx, cy, _C_WORD_DONE)
            else:
                self._name_flash = 30          # name is full — flash a reminder
        elif tid == "end":
            self._spawn_explosion(cx, cy, (255, 120, 150))
            self._finish_name_entry()

    def _finish_name_entry(self):
        """Record the entered name + score onto the high-score table and hand
        over to the passive game-over screen (which shows the table)."""
        name = self._name.strip()
        if not name:
            name = chr(ord("A") + self._name_letter)   # at least the live letter
        self._final_name = _sanitize_hiname(name) or "---"
        record_alien_score(self._final_name, self._final_score)
        self._name_entry = False
        self._gameover_t = 0
        self._bullets = []
        self._sparks = []
        self._spawn_go_squadron()

    @staticmethod
    def _bezier_point(cl, t):
        u = 1.0 - t
        a, b, c, e = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        p0, p1, p2, p3 = cl["p0"], cl["p1"], cl["p2"], cl["p3"]
        return (a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1])

    def _spawn_flying_clive(self):
        """A Clive-on-C5 that flies across the screen along a random cubic
        Bézier curve, entering and leaving off opposite edges."""
        W, H = self.w, self.h
        m = self.s(70)
        if _random.random() < 0.5:
            x0, x3 = -m, W + m            # left → right
        else:
            x0, x3 = W + m, -m            # right → left
        return {
            "p0": (x0, _random.uniform(H * 0.08, H * 0.88)),
            "p1": (_random.uniform(W * 0.15, W * 0.85), _random.uniform(-H * 0.1, H * 1.1)),
            "p2": (_random.uniform(W * 0.15, W * 0.85), _random.uniform(-H * 0.1, H * 1.1)),
            "p3": (x3, _random.uniform(H * 0.08, H * 0.88)),
            "t": 0.0,
            "dt": _random.uniform(0.0035, 0.0085),
            "px": max(2, self.s(_random.choice((2, 2, 3)))),
            "roll": 0.0,
            "facing": "left",
            "pos": (x0, 0.0),
            "prev": (x0, 0.0),
        }

    def _update_flying_clives(self):
        for cl in self._go_clives:
            cl["t"] += cl["dt"]
            if cl["t"] >= 1.0:
                new = self._spawn_flying_clive()
                cl.clear()
                cl.update(new)
                cl["pos"] = self._bezier_point(cl, 0.0)
                cl["prev"] = cl["pos"]
                continue
            pos = self._bezier_point(cl, cl["t"])
            dx = pos[0] - cl["prev"][0]
            cl["prev"] = cl["pos"] = pos
            if dx > 0.3:
                cl["facing"] = "right"
            elif dx < -0.3:
                cl["facing"] = "left"
            cl["roll"] += dx / max(1.0, cl["px"] * 5.0)

    def _update_gameover(self):
        """Animate the score screen; a fresh fire press restarts the game."""
        self._gameover_t += 1
        self._update_stars()               # keep the starfield drifting
        self._update_flying_clives()
        if self._gameover_t > 70 and self._k_fire and not self._prev_fire:
            self._restart_after_gameover()
        self._prev_fire = self._k_fire

    def _restart_after_gameover(self):
        self._begin_play()

    # -- title / attract screen --------------------------------------------
    def _update_title(self):
        """Animate the attract backdrop and wait for a fresh Space press."""
        self._update_stars()
        self._update_formation()
        self._update_divers()
        if self._k_fire and not self._prev_fire:
            self._begin_play()
        self._prev_fire = self._k_fire

    def _begin_play(self):
        """Start a fresh playthrough (from the title screen or after game over)."""
        self._started = True
        self._gameover = False
        self._name_entry = False
        self._name = ""
        self._name_letter = 0
        self._final_name = ""
        self._lives = 3
        self._score = 0
        self._words_done = 0
        self._game_speed_bonus = 0.0
        self._ship_x = self.w * 0.5
        self._ship_prev_x = self._ship_x
        self._ship_invuln = 60
        self._ship_v = 0.0
        # Restore the normal bottom-of-screen flying height (name entry raises it).
        self._ship_top = self.h - self._ship_h - self.s(8)
        self._bombs = []
        self._bullets = []
        self._sparks = []
        self._letters = []
        self._drops = []
        self._asteroids = []
        self._win_flash = 0
        self._life_flash = 0
        self._wave = 0
        self._pig = None
        self._start_game()
        self._new_wave(first=True)

    # -- floating inflatable pig "mother ship" ------------------------------
    def _pig_scale(self, p):
        """Smoothly oscillating inflate/deflate factor for the pig."""
        return 2.0 + 0.85 * _math.sin(p["t"] * 0.03 + p["infl"])

    def _pig_rect(self):
        """Current (x, y, w, h) of the pig at its live inflated size."""
        p = self._pig
        f = self._pig_scale(p)
        w = self._pig_bw * f
        h = self._pig_bh * f
        # Bob softly up and down along a Bézier-eased curve near the top.
        bob = self.s(16) * _bezier_wave(p["t"] * 0.01 + p["bob"])
        y = p["basey"] + bob
        return p["x"], y, w, h

    def _pig_scaled(self, w, h):
        """Nearest-neighbour scaled pig sprite (keeps the pixel look), cached on
        a quantised size to limit per-frame rescaling as the pig inflates."""
        w = max(2, (w // 2) * 2)
        h = max(2, (h // 2) * 2)
        key = (self._pig_bw, w, h)
        spr = _PIG_SCALED_CACHE.get(key)
        if spr is None:
            if len(_PIG_SCALED_CACHE) > 120:
                _PIG_SCALED_CACHE.clear()
            spr = _pg.transform.scale(self._pig_base, (w, h))
            _PIG_SCALED_CACHE[key] = spr
        return spr

    def _update_pig(self):
        if self._pig is None:
            self._pig_cd -= 1
            if self._pig_cd <= 0:
                dirx = _random.choice((-1, 1))
                est_w = self._pig_bw * 2.9
                self._pig = {
                    "dir": dirx,
                    "x": (-est_w if dirx > 0 else self.w),
                    "basey": _random.uniform(self.s(8), self.s(40)),
                    "t": 0.0,
                    "bob": _random.uniform(0.0, 1.0),
                    "infl": _random.uniform(0.0, 6.28),
                    "accel": 0.0,
                    "accel_cd": _random.randint(40, 120),
                }
                self._pig_cd = _random.randint(220, 520)
            return
        p = self._pig
        p["t"] += 1
        # Occasional acceleration bursts that then decay back to a gentle drift.
        p["accel_cd"] -= 1
        if p["accel_cd"] <= 0:
            p["accel"] = _random.uniform(self.s(1), self.s(3))
            p["accel_cd"] = _random.randint(60, 160)
        p["accel"] *= 0.96
        speed = self.s(1) + p["accel"]
        p["x"] += p["dir"] * speed
        px, py, pw, ph = self._pig_rect()
        # While drifting over the play area the pig sometimes drops a ZX
        # Spectrum (game mode only — catching it is an extra life).
        if self._game and len(self._drops) < 2 and _random.random() < 0.006 \
                and -pw * 0.2 < px < self.w - pw * 0.6:
            self._spawn_zx_drop(px + pw / 2, py + ph * 0.7)
        w = self._pig_bw * self._pig_scale(p)
        if p["x"] < -w - self.s(6) or p["x"] > self.w + self.s(6):
            self._pig = None

    # -- falling ZX Spectrum pickups ---------------------------------------
    def _spawn_zx_drop(self, x, y):
        self._drops.append({
            "x": x, "y": y,
            "vy": 1.6 * self.dpr,
            "t": _random.uniform(0.0, 6.28),
            "px": max(2, self.s(2)),
        })

    def _update_drops(self):
        if not self._drops:
            return
        # Ship catch box (same footprint as the bomb hit-box).
        ship_top = self._ship_top + self.s(2)
        ship_l = self._ship_x - self._ship_w / 2 + self.s(3)
        ship_r = self._ship_x + self._ship_w / 2 - self.s(3)
        kept = []
        for d in self._drops:
            d["vy"] = min(3.4 * self.dpr, d["vy"] + 0.06 * self.dpr)  # gentle accel
            d["y"] += d["vy"]
            d["t"] += 0.12
            spr = _make_zx_sprite(d["px"])
            dw, dh = spr.get_width(), spr.get_height()
            # caught by the C5?
            if d["y"] + dh / 2 >= ship_top and ship_l <= d["x"] <= ship_r:
                self._catch_zx()
                continue
            if d["y"] - dh / 2 > self.h:                          # missed it
                continue
            kept.append(d)
        self._drops = kept

    def _catch_zx(self):
        """Caught a dropped ZX Spectrum: award an extra life."""
        self._lives = min(9, self._lives + 1)
        self._life_flash = 70
        self._spawn_explosion(self._ship_x, self._ship_top, (90, 235, 255))

    # -- rolling pixel asteroids -------------------------------------------
    def _spawn_asteroid(self):
        px = _random.choice((2, 2, 3, 3, 4, 5))     # varied sizes
        shape = _random.randrange(len(_AST_PATTERNS))
        base = _make_asteroid_base(shape, px)
        w = base.get_width()
        # Some rocks "roll" down (spin coupled to how far they travel, like a
        # tumbling boulder) while the rest free-spin in place.  Rollers get a
        # stronger sideways drift so the rolling reads clearly.
        rolling = _random.random() < 0.55
        self._asteroids.append({
            "x": _random.uniform(w * 0.5, max(w, self.w - w * 0.5)),
            "y": -w,
            "vy": _random.uniform(1.2, 3.4) * self.dpr,
            "vx": (_random.uniform(-1.7, 1.7) if rolling
                   else _random.uniform(-0.7, 0.7)) * self.dpr,
            "shape": shape, "px": px,
            "ang": _random.uniform(0.0, 360.0),
            "dang": _random.uniform(-5.0, 5.0),     # free-spin speed (deg/frame)
            "roll": rolling,
            "r": w * 0.42,
        })

    def _update_asteroids(self):
        self._ast_cd -= 1
        if self._ast_cd <= 0 and len(self._asteroids) < 7:
            self._ast_cd = _random.randint(22, 70)
            self._spawn_asteroid()
        kept = []
        for a in self._asteroids:
            a["y"] += a["vy"]
            a["x"] += a["vx"]
            if a.get("roll"):
                # Roll without slipping: the spin angle tracks the distance the
                # rock travels (arc = r·θ), so it looks like a boulder rolling
                # down rather than spinning on the spot.  Direction follows its
                # sideways drift (a forward tumble when falling straight down).
                travel = a["vy"] + abs(a["vx"])
                sign = -1.0 if a["vx"] < 0 else 1.0
                a["ang"] += sign * _math.degrees(travel / max(1.0, a["r"]))
            else:
                a["ang"] += a["dang"]
            if a["y"] - a["r"] > self.h:
                continue
            kept.append(a)
        self._asteroids = kept

    def _render_asteroids(self, surface):
        for a in self._asteroids:
            spr = _make_asteroid_sprite(a["shape"], a["px"], a["ang"])
            surface.blit(spr, (int(a["x"] - spr.get_width() / 2),
                               int(a["y"] - spr.get_height() / 2)))

    def _spawn_explosion(self, x, y, color):
        parts = []
        for _ in range(10):
            ang = _random.uniform(0, 6.28)
            spd = _random.uniform(0.5, 2.5) * self.dpr
            parts.append([x, y, _math.cos(ang) * spd, _math.sin(ang) * spd])
        self._explosions.append({"parts": parts, "age": 0, "col": color})

    def _update_explosions(self):
        kept = []
        for ex in self._explosions:
            ex["age"] += 1
            for p in ex["parts"]:
                p[0] += p[2]
                p[1] += p[3]
            if ex["age"] < 18:
                kept.append(ex)
        self._explosions = kept

    # -- diving Floyds -----------------------------------------------------
    def _update_divers(self):
        """Now and then a Floyd peels out of the formation and swoops down a
        random cubic-Bézier path with a zig-zag wiggle, exiting the bottom."""
        self._dive_cd -= 1
        # Frequent, individual dives (staggered) so Floyds fall one-by-one
        # rather than the whole formation dropping at once.
        if self._dive_cd <= 0 and len(self._divers) < 7:
            self._dive_cd = _random.randint(8, 30)
            self._launch_diver()
        kept = []
        for d in self._divers:
            d["t"] += d["dt"]
            if d["t"] < 1.0:
                kept.append(d)
                # diving Floyds strafe the ship with bombs as they swoop
                if _random.random() < 0.03:
                    dx, dy = self._diver_pos(d)
                    dw, dh = self._diver_wh(d)
                    self._bombs.append([dx + dw / 2, dy + dh])
            elif d["mode"] == "return":
                # completed its loop — slot back into the formation
                self._aliens[d["r"]][d["c"]] = True
        self._divers = kept

    def _launch_diver(self):
        alive = [(r, c) for r in range(self.ROWS) for c in range(self.COLS)
                 if self._aliens[r][c]]
        if not alive:
            return
        r, c = _random.choice(alive)
        self._aliens[r][c] = False        # it has left the formation
        x, y = self._alien_pos(r, c)
        W, H = self.w, self.h
        side = _random.choice((-1, 1))
        # Half the divers loop back up into their formation slot; the rest
        # swoop all the way out through the bottom of the screen.
        mode = "return" if _random.random() < 0.5 else "exit"
        d = {
            "kind": self._kind[r][c], "r": r, "c": c, "mode": mode,
            "scale": self._scale[r][c],
            "p0": (x, y),
            "t": 0.0,
            "dt": _random.uniform(0.006, 0.012),
            "wig_a": _random.uniform(self.s(6), self.s(22)),   # zig-zag amplitude
            "wig_f": _random.uniform(2.0, 5.0),
            "wig_p": _random.uniform(0.0, 6.283),
        }
        if mode == "exit":
            end_x = _random.uniform(W * 0.08, W * 0.92)
            d["p1"] = (x + side * _random.uniform(W * 0.15, W * 0.45), y + H * 0.30)
            d["p2"] = (end_x - side * _random.uniform(W * 0.15, W * 0.45), y + H * 0.65)
            d["p3"] = (end_x, H + self._alien_h)
        else:
            # Loop down and curve back up; the end point tracks the live home
            # slot (computed each frame in _diver_pos) so it rejoins cleanly.
            d["p1"] = (x + side * _random.uniform(W * 0.20, W * 0.42), y + H * 0.45)
            d["p2"] = (x - side * _random.uniform(W * 0.10, W * 0.30), y + H * 0.55)
        self._divers.append(d)

    def _diver_wh(self, d):
        px = self._px_for_scale(d.get("scale", 1.0))
        return _FLOYD_W * px, _FLOYD_H * px

    def _diver_pos(self, d):
        t = d["t"]
        u = 1.0 - t
        p0, p1, p2 = d["p0"], d["p1"], d["p2"]
        p3 = d.get("p3")
        if p3 is None:                       # returning diver: live home slot
            p3 = self._alien_pos(d["r"], d["c"])
        a, b, cc, e = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        x = a * p0[0] + b * p1[0] + cc * p2[0] + e * p3[0]
        y = a * p0[1] + b * p1[1] + cc * p2[1] + e * p3[1]
        # Add a zig-zag that is strongest mid-flight (eased in and out).
        x += d["wig_a"] * _math.sin(t * d["wig_f"] * 6.283 + d["wig_p"]) \
            * (4.0 * t * (1.0 - t))
        return x, y

    # -- rendering ---------------------------------------------------------
    def render(self, surface, transparent=False):
        if transparent:
            surface.fill((0, 0, 0, 0))
        else:
            surface.fill(_C_SKY)
        pg = _pg
        # stars (additive glow + bright core); some flicker into $/£/€ glyphs
        for st in self._stars:
            level = 0.5 + 0.5 * _math.sin(st["ph"])
            x, y, r = st["x"], st["y"], st["r"]
            if st["glyph"] is not None:
                glow = _make_glow(_C_CURRENCY, max(2, r * 2), 1.0)
                surface.blit(glow, (int(x - r * 2), int(y - r * 2)),
                             special_flags=pg.BLEND_RGB_ADD)
                lf = 0.6 + 0.4 * level
                col = (int(_C_CURRENCY[0] * lf), int(_C_CURRENCY[1] * lf),
                       int(_C_CURRENCY[2] * lf))
                f = _font(max(10, int(r * 5)), bold=True)
                _draw_text(surface, st["glyph"], int(x - r * 2), int(y - r * 2), f, col)
                continue
            lvl = 0.5 if level < 0.4 else (0.75 if level < 0.75 else 1.0)
            g = _make_glow(st["col"], r, lvl)
            surface.blit(g, (int(x - r), int(y - r)), special_flags=pg.BLEND_RGB_ADD)
            if level > 0.7:
                surface.fill(st["col"], pg.Rect(int(x), int(y),
                                                max(1, self.s(1)), max(1, self.s(1))))
        # Title/attract screen takes over until the player presses Space.
        if self._game and not self._started:
            self._render_title(surface)
            return
        # Game-over score screen takes over the foreground (starfield kept).
        if self._game and self._gameover:
            if self._name_entry:
                self._render_name_entry(surface)
            else:
                self._render_gameover(surface)
            return
        # alien Floyds (each rendered at its own size)
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                px = self._px_for_scale(self._scale[r][c])
                spr = _make_floyd_sprite(_FLOYD_NAMES[self._kind[r][c]], px)
                x, y = self._alien_pos(r, c)
                surface.blit(spr, (int(x), int(y)))
        # diving Floyds (swooping down their Bézier paths)
        cue_blink = (self._t // 8) % 2 == 0
        for d in self._divers:
            px = self._px_for_scale(d.get("scale", 1.0))
            spr = _make_floyd_sprite(_FLOYD_NAMES[d["kind"]], px)
            x, y = self._diver_pos(d)
            dw, _dh = self._diver_wh(d)
            surface.blit(spr, (int(x), int(y)))
            # small blinking "▲ rejoining" cue while a diver loops back up
            if d["mode"] == "return" and d["t"] > 0.5 and cue_blink:
                cf = _font(self.s(9), bold=True)
                cue = "▲ rejoining"
                cw = cf.size(cue)[0]
                _draw_text(surface, cue,
                           int(x + dw / 2 - cw / 2),
                           int(y - self.s(12)), cf, (150, 255, 190))
        # bombs — alien fire: an animated rainbow streak with a white-hot core,
        # so the projectiles stay clearly visible against the black sky.
        bw = max(2, self.s(2))
        for bm in self._bombs:
            x, y = int(bm[0]), int(bm[1])
            col = _cycle_color(_C_BOMB_CYCLE, self._t * 0.30 + bm[1] * 0.06)
            pg.draw.line(surface, col, (x, y), (x, y + self.s(7)), bw)
            pg.draw.line(surface, (255, 255, 255), (x, y),
                         (x, y + self.s(3)), max(1, self.s(1)))
        # ship: Sir Clive on his C5 (blinks while briefly invulnerable).
        ship_blink = self._game and self._ship_invuln > 0 and (self._t // 4) % 2 == 0
        if not ship_blink:
            fire = self._ship_fire_anim / 10.0
            clive = _make_clive_c5(self._ship_px, self._ship_facing,
                                   self._ship_roll, fire)
            surface.blit(clive, (int(self._ship_x - self._ship_w / 2),
                                 int(self._ship_top)))
        # bullets — the C5's fire: an animated colour streak with a white-hot
        # core, plus the colourful muzzle star sparks drawn on top.
        bcw = max(2, self.s(2))
        for b in self._bullets:
            x, y = int(b[0]), int(b[1])
            col = _cycle_color(_C_BULLET_CYCLE, self._t * 0.40 + b[1] * 0.05)
            pg.draw.line(surface, col, (x, y), (x, y + self.s(8)), bcw)
            pg.draw.line(surface, (255, 255, 255), (x, y),
                         (x, y + self.s(3)), max(1, self.s(1)))
        self._render_sparks(surface)
        # floating inflatable pig "mother ship"
        if self._pig is not None:
            px, py, pw, ph = self._pig_rect()
            surface.blit(self._pig_scaled(int(pw), int(ph)),
                         (int(px), int(py)))
        # explosions
        for ex in self._explosions:
            fade = max(0.0, 1.0 - ex["age"] / 18.0)
            col = (int(ex["col"][0] * fade), int(ex["col"][1] * fade),
                   int(ex["col"][2] * fade))
            for p in ex["parts"]:
                surface.fill(col, pg.Rect(int(p[0]), int(p[1]),
                                          max(1, self.s(2)), max(1, self.s(2))))
        # falling collectable letters + ZX Spectrum pickups + asteroids (game)
        if self._game:
            self._render_asteroids(surface)
            self._render_letters(surface)
            self._render_drops(surface)
        # HUD: the playable game shows the word + score; the autoplaying scene
        # shows score/hi/wave.  Both are skipped on the transparent overlay.
        if self._game:
            self._render_game_hud(surface)
        elif not transparent:
            self._render_hud(surface)

    def _render_letters(self, surface):
        pg = _pg
        f = _font(self.s(16), bold=True)
        for lt in self._letters:
            ch = lt["ch"]
            lw = f.size(ch)[0]
            gr = self.s(8)
            glow = _make_glow(_C_LETTER_GLOW, gr, 1.0)
            surface.blit(glow, (int(lt["x"] - gr), int(lt["y"] - gr)),
                         special_flags=pg.BLEND_RGB_ADD)
            x = int(lt["x"] - lw / 2)
            y = int(lt["y"] - self.s(9))
            _draw_text(surface, ch, x + self.s(1), y + self.s(1), f, (0, 0, 0))
            _draw_text(surface, ch, x, y, f, _C_LETTER)

    def _render_drops(self, surface):
        """Falling ZX Spectrums: a soft glow plus a gentle tumbling wobble."""
        pg = _pg
        for d in self._drops:
            base = _make_zx_sprite(d["px"])
            ang = 14.0 * _math.sin(d["t"])      # gentle wobble, not a full tumble
            spr = pg.transform.rotate(base, ang)
            gr = max(4, self.s(6))
            glow = _make_glow((120, 200, 255), gr, 0.8)
            surface.blit(glow, (int(d["x"] - gr), int(d["y"] - gr)),
                         special_flags=pg.BLEND_RGB_ADD)
            surface.blit(spr, (int(d["x"] - spr.get_width() / 2),
                               int(d["y"] - spr.get_height() / 2)))

    def _render_game_hud(self, surface):
        """Top of screen: the word to complete (collected letters in bold/green)
        rendered in big retro letters, and the score top-right."""
        # The word to complete, centred, in big retro letters.
        big_done = _font(self.s(26), bold=True)
        big_todo = _font(self.s(26), bold=False)
        word = self._word
        got = self._got
        sp = self.s(7)
        widths = [(big_done if (i < len(got) and got[i]) else big_todo).size(c)[0]
                  for i, c in enumerate(word)]
        total = sum(widths) + sp * max(0, len(word) - 1)
        x = max(self.s(8), (self.w - total) // 2)
        y = self.s(8)
        for i, c in enumerate(word):
            if i < len(got) and got[i]:
                f, col = big_done, _C_WORD_DONE
            else:
                f, col = big_todo, _C_WORD_TODO
            _draw_text(surface, c, x + self.s(2), y + self.s(2), f, (0, 0, 0))
            _draw_text(surface, c, x, y, f, col)
            x += widths[i] + sp
        # Score, top-right, in big retro letters with a small label above.
        lab = _font(self.s(11), bold=True)
        sf = _font(self.s(24), bold=True)
        slabel = "%06d" % self._score
        sw = sf.size(slabel)[0]
        sx = self.w - sw - self.s(12)
        _draw_text(surface, "SCORE", sx, y, lab, _C_GAME_SCORE)
        _draw_text(surface, slabel, sx + self.s(1), y + self.s(15) + self.s(1),
                   sf, (0, 0, 0))
        _draw_text(surface, slabel, sx, y + self.s(15), sf, _C_GAME_SCORE)
        # Lives, top-left, as a row of little cannon icons.
        lab = _font(self.s(11), bold=True)
        _draw_text(surface, "LIVES", self.s(10), y, lab, _C_SHIP)
        life_spr = _make_sprite(_CANNON, _C_SHIP, max(1, self.s(1)))
        lw = life_spr.get_width()
        for i in range(max(0, self._lives)):
            surface.blit(life_spr,
                         (int(self.s(10) + i * (lw + self.s(4))),
                          int(y + self.s(15))))
        # Brief "word complete" banner.
        if self._win_flash > 0 and (self._win_flash // 6) % 2 == 0:
            bf = _font(self.s(20), bold=True)
            msg = "WORD COMPLETE!  +15"
            mw = bf.size(msg)[0]
            _draw_text(surface, msg, (self.w - mw) // 2, self.h // 2,
                       bf, _C_WORD_DONE)
        # Brief "extra life" banner (caught a ZX Spectrum).
        if self._life_flash > 0 and (self._life_flash // 6) % 2 == 0:
            bf = _font(self.s(18), bold=True)
            msg = "ZX SPECTRUM!  +1 LIFE"
            mw = bf.size(msg)[0]
            _draw_text(surface, msg, (self.w - mw) // 2,
                       self.h // 2 + self.s(26), bf, (120, 235, 255))

    # -- game-over score screen --------------------------------------------
    def _render_prism(self, surface, cx, cy, size, t):
        """A 'Dark Side of the Moon' prism rendered as a spinning pseudo-3D
        triangular prism (rainbow-glass side faces, white edges) with an 8-bit
        Clive portrait embedded on its front face, plus a dim spectrum fan and
        white beam behind it for the album look."""
        pg = _pg

        def lerp(p, q, f):
            return (p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)

        # Light widths scale with the prism so the beam/fan stay in proportion
        # as the prism grows.
        beam_w = max(1, int(round(size * 0.02)))
        fan_w = max(2, int(round(size * 0.05)))

        # --- dim DSOTM beam + spectrum fan behind the spinning prism ---------
        topf = (cx, cy - size * 0.66)
        blf = (cx - size * 0.62, cy + size * 0.5)
        brf = (cx + size * 0.62, cy + size * 0.5)
        entry = lerp(topf, blf, 0.52)
        pg.draw.line(surface, (120, 120, 130),
                     (cx - size * 2.4, entry[1] - size * 0.16), entry, beam_w)
        n = len(_SPECTRUM)
        for i, col in enumerate(_SPECTRUM):
            sp = lerp(topf, brf, 0.5 + (i / (n - 1)) * 0.26)
            sh = 0.30 + 0.22 * (0.5 + 0.5 * _math.sin(t * 0.12 + i * 0.7))
            c = (int(col[0] * sh), int(col[1] * sh), int(col[2] * sh))
            pg.draw.line(surface, c, sp,
                         (sp[0] + size * 2.7 * _math.cos(0.26),
                          sp[1] + size * 2.7 * _math.sin(0.26)), fan_w)

        # --- spinning pseudo-3D triangular prism ----------------------------
        ang = t * 0.05
        ca, sa = _math.cos(ang), _math.sin(ang)
        tri = [(0.0, -size * 0.66), (-size * 0.62, size * 0.5), (size * 0.62, size * 0.5)]
        depth = size * 0.30

        def proj(x, y, z):
            return (cx + x * ca + z * sa, cy + y, -x * sa + z * ca)

        front = [proj(x, y, depth) for (x, y) in tri]
        back = [proj(x, y, -depth) for (x, y) in tri]
        faces = []
        for i in range(3):
            j = (i + 1) % 3
            faces.append(("side", i, [front[i], front[j], back[j], back[i]]))
        faces.append(("tri", "back", back))
        faces.append(("tri", "front", front))
        faces.sort(key=lambda fc: sum(p[2] for p in fc[2]) / len(fc[2]))

        def signed_area(pts):
            a = 0.0
            for i in range(len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % len(pts)]
                a += x1 * y2 - x2 * y1
            return a * 0.5

        edge = max(1, int(round(size * 0.035)))
        side_edge = max(1, int(round(size * 0.018)))
        front_visible = sum(p[2] for p in front) > sum(p[2] for p in back)
        for kind, idx, verts in faces:
            pts = [(int(p[0]), int(p[1])) for p in verts]
            if abs(signed_area(pts)) < 2:
                continue
            if kind == "side":
                base = _SPECTRUM[(idx + int(t // 14)) % len(_SPECTRUM)]
                sh = 0.85 if signed_area(pts) > 0 else 0.45
                # glassy: blend the spectrum colour toward dark
                col = (int(base[0] * sh * 0.7 + 12), int(base[1] * sh * 0.7 + 12),
                       int(base[2] * sh * 0.7 + 14))
                pg.draw.polygon(surface, col, pts)
                pg.draw.polygon(surface, (235, 235, 245), pts, side_edge)
            else:
                pg.draw.polygon(surface, (24, 24, 38) if idx == "front" else (13, 13, 20),
                                pts)
                pg.draw.polygon(surface, (235, 235, 245), pts, edge)

        # --- Clive on the front face (foreshortened with the spin) ----------
        if front_visible:
            fpx = max(1, int(round(size * 0.05)))
            face = _make_clive_face(fpx)
            fw = max(2, int(face.get_width() * abs(ca)))
            fh = face.get_height()
            scaled = pg.transform.scale(face, (fw, fh))
            fxc = sum(p[0] for p in front) / 3.0
            fyc = sum(p[1] for p in front) / 3.0 + size * 0.06
            surface.blit(scaled, (int(fxc - fw / 2), int(fyc - fh / 2)))

    def _render_run_summary(self, surface, retro, y):
        """A compact one-line summary of the just-finished run."""
        W = self.w
        sf = _font(self.s(15), bold=True)
        parts = [("SCORE %06d" % self._final_score, _C_GAME_SCORE),
                 ("WAVE %d" % self._final_wave, (150, 200, 255)),
                 ("WORDS %d" % self._words_done, (255, 150, 200))]
        gap = self.s(18)
        widths = [sf.size(txt)[0] for txt, _ in parts]
        total = sum(widths) + gap * (len(parts) - 1)
        x = (W - total) // 2
        for (txt, col), w in zip(parts, widths):
            retro(txt, x, y, sf, col, 1)
            x += w + gap

    def _render_hiscore_table(self, surface, retro, top_y):
        """The top-N leaderboard, highlighting this run's freshly-entered row."""
        pg = _pg
        W = self.w
        table = get_alien_table()
        # Which row is the player's new entry (first exact match)?
        me_idx = -1
        if self._final_name:
            for i, e in enumerate(table):
                if e["name"] == self._final_name and e["score"] == self._final_score:
                    me_idx = i
                    break
        hf = _font(self.s(15), bold=True)
        rf = _font(self.s(15), bold=True)
        title = "HIGH SCORES"
        tw = hf.size(title)[0]
        retro(title, (W - tw) // 2, top_y, hf, (120, 255, 140), 1)
        row_h = self.s(20)
        pw = self.s(240)
        ph = row_h * ALIEN_TABLE_MAX + self.s(12)
        px = (W - pw) // 2
        py = top_y + self.s(26)
        panel = pg.Surface((pw, ph), pg.SRCALPHA)
        panel.fill((10, 10, 18, 215))
        surface.blit(panel, (px, py))
        pg.draw.rect(surface, (120, 120, 150), pg.Rect(px, py, pw, ph),
                     max(1, self.s(1)))
        for i in range(ALIEN_TABLE_MAX):
            ry = py + self.s(6) + i * row_h
            if i < len(table):
                name = table[i]["name"] or "---"
                score = table[i]["score"]
            else:
                name, score = "---", 0
            # Highlight (and blink) the player's new entry.
            if i == me_idx and (self._gameover_t // 12) % 2 == 0:
                col = (255, 220, 120)
            elif i == me_idx:
                col = (255, 170, 80)
            else:
                col = (210, 210, 225)
            retro("%d" % (i + 1), px + self.s(12), ry, rf, (150, 200, 255), 1)
            retro(name, px + self.s(40), ry, rf, col, 1)
            sval = "%06d" % score
            sw = rf.size(sval)[0]
            retro(sval, px + pw - self.s(12) - sw, ry, rf, col, 1)

    def _render_title(self, surface):
        """Attract screen: drifting Floyds behind a big flashing 'PRESS SPACE TO
        START GAME' prompt; the game waits here until the player presses Space."""
        W, H = self.w, self.h
        # drifting Floyds as a backdrop (formation + any divers)
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                px = self._px_for_scale(self._scale[r][c])
                spr = _make_floyd_sprite(_FLOYD_NAMES[self._kind[r][c]], px)
                x, y = self._alien_pos(r, c)
                surface.blit(spr, (int(x), int(y)))
        for d in self._divers:
            px = self._px_for_scale(d.get("scale", 1.0))
            spr = _make_floyd_sprite(_FLOYD_NAMES[d["kind"]], px)
            x, y = self._diver_pos(d)
            surface.blit(spr, (int(x), int(y)))
        _blit_veil(surface, (6, 6, 12), 140)

        def retro(text, x, y, font, col, sh=2):
            _draw_text(surface, text, int(x) + self.s(sh), int(y) + self.s(sh),
                       font, (0, 0, 0))
            _draw_text(surface, text, int(x), int(y), font, col)

        # game title
        tf = _font(self.s(40), bold=True)
        title = "ALIEN FLOYD'S"
        tw = tf.size(title)[0]
        retro(title, (W - tw) // 2, int(H * 0.28), tf, (255, 120, 150))

        # big flashing "press space" prompt, centred
        if (self._t // 16) % 2 == 0:
            pf = _font(self.s(28), bold=True)
            msg = "PRESS SPACE TO START GAME"
            mw = pf.size(msg)[0]
            retro(msg, (W - mw) // 2, int(H * 0.50), pf, (255, 220, 120))

        # controls hint
        hf = _font(self.s(13), bold=True)
        hint = "← →  MOVE       SPACE  FIRE"
        hw = hf.size(hint)[0]
        retro(hint, (W - hw) // 2, int(H * 0.60), hf, (200, 200, 215), 1)

    def _render_name_entry(self, surface):
        """Interactive 'NEW HIGH SCORE' name-entry round: spell your name by
        shooting the live letter (cycles A→B→…), <NEXT> (lock it in) and
        <END> (finish).  The C5 stays under the player's control below."""
        pg = _pg
        t = self._gameover_t
        W, H = self.w, self.h
        _blit_veil(surface, (6, 6, 12), 150)

        def retro(text, x, y, font, col, sh=2):
            _draw_text(surface, text, int(x) + self.s(sh), int(y) + self.s(sh),
                       font, (0, 0, 0))
            _draw_text(surface, text, int(x), int(y), font, col)

        # title (flashes) + final score
        tf = _font(self.s(28), bold=True)
        title = "NEW HIGH SCORE!"
        tw = tf.size(title)[0]
        flash = (t // 16) % 2 == 0
        retro(title, (W - tw) // 2, self.s(18), tf,
              (255, 220, 120) if flash else (255, 150, 80))
        sf = _font(self.s(16), bold=True)
        smsg = "SCORE  %06d" % self._final_score
        sw = sf.size(smsg)[0]
        retro(smsg, (W - sw) // 2, self.s(52), sf, _C_GAME_SCORE, 1)

        # the name being spelled: committed letters + the blinking live letter
        nf = _font(self.s(26), bold=True)
        cur = chr(ord("A") + self._name_letter)
        chars = [(c, _C_WORD_DONE) for c in self._name]
        chars.append((cur if (t // 14) % 2 == 0 else "_", _C_LETTER))
        sp = self.s(6)
        widths = [nf.size(c)[0] for c, _ in chars]
        total = sum(widths) + sp * (len(chars) - 1)
        nx = (W - total) // 2
        ny = int(H * 0.30)
        for (c, col), cw in zip(chars, widths):
            retro(c, nx, ny, nf, col, 1)
            nx += cw + sp

        # the three shootable targets
        bf = _font(self.s(18), bold=True)
        tcols = {"letter": _C_LETTER, "next": _C_WORD_DONE, "end": (255, 120, 150)}
        for box in self._name_targets():
            r = box["rect"]
            col = tcols[box["id"]]
            panel = pg.Surface((r.w, r.h), pg.SRCALPHA)
            panel.fill((12, 12, 22, 210))
            surface.blit(panel, (r.x, r.y))
            pg.draw.rect(surface, col, r, max(2, self.s(2)))
            lw = bf.size(box["label"])[0]
            retro(box["label"], r.centerx - lw / 2, r.centery - self.s(11),
                  bf, col, 1)

        # instructions (and a 'name full' reminder when applicable)
        inf = _font(self.s(12), bold=True)
        msg = "FIRE  [A] CYCLE   [NEXT] ADD   [END] FINISH"
        mw = inf.size(msg)[0]
        retro(msg, (W - mw) // 2, int(H * 0.58), inf, (210, 210, 225), 1)
        if self._name_flash > 0 and (self._name_flash // 5) % 2 == 0:
            fmsg = "NAME FULL — SHOOT [END]"
            fw = inf.size(fmsg)[0]
            retro(fmsg, (W - fw) // 2, int(H * 0.62), inf, (255, 150, 150), 1)

        # the player's C5, its bullets and the muzzle sparks / explosions
        self._render_player_and_shots(surface)

    def _render_player_and_shots(self, surface):
        """Draw the C5 plus its bullets, sparks and explosions (shared by the
        name-entry round, which keeps the ship playable)."""
        pg = _pg
        bcw = max(2, self.s(2))
        for b in self._bullets:
            x, y = int(b[0]), int(b[1])
            col = _cycle_color(_C_BULLET_CYCLE, self._t * 0.40 + b[1] * 0.05)
            pg.draw.line(surface, col, (x, y), (x, y + self.s(8)), bcw)
            pg.draw.line(surface, (255, 255, 255), (x, y),
                         (x, y + self.s(3)), max(1, self.s(1)))
        self._render_sparks(surface)
        for ex in self._explosions:
            fade = max(0.0, 1.0 - ex["age"] / 18.0)
            col = (int(ex["col"][0] * fade), int(ex["col"][1] * fade),
                   int(ex["col"][2] * fade))
            for p in ex["parts"]:
                surface.fill(col, pg.Rect(int(p[0]), int(p[1]),
                                          max(1, self.s(2)), max(1, self.s(2))))
        fire = self._ship_fire_anim / 10.0
        clive = _make_clive_c5(self._ship_px, self._ship_facing,
                               self._ship_roll, fire)
        surface.blit(clive, (int(self._ship_x - self._ship_w / 2),
                             int(self._ship_top)))

    def _render_gameover(self, surface):
        pg = _pg
        t = self._gameover_t
        W, H = self.w, self.h
        _blit_veil(surface, (6, 6, 12), 150)

        def retro(text, x, y, font, col, sh=2):
            _draw_text(surface, text, int(x) + self.s(sh), int(y) + self.s(sh),
                       font, (0, 0, 0))
            _draw_text(surface, text, int(x), int(y), font, col)

        # decorative pigs drifting across the top
        pig = _make_pig_sprite(max(2, self.s(2)))
        pw = pig.get_width()
        for k in range(2):
            x = int((t * (0.7 + 0.5 * k) + k * W * 0.55) % (W + pw)) - pw
            y = int(self.s(12) + k * self.s(40) + self.s(6) * _math.sin(t * 0.04 + k * 2))
            surface.blit(pig, (x, y))

        # squadron of Clives flying their C5s along Bézier arcs
        for cl in self._go_clives:
            spr = _make_clive_c5(cl["px"], cl["facing"], cl["roll"], 0.0)
            x, y = cl["pos"]
            surface.blit(spr, (int(x - spr.get_width() / 2),
                               int(y - spr.get_height() / 2)))

        # title
        tf = _font(self.s(30), bold=True)
        title = "GAME OVER"
        tw = tf.size(title)[0]
        retro(title, (W - tw) // 2, self.s(16), tf, (255, 90, 120))

        # prism centrepiece (compact, up top to leave room for the table; the
        # embedded Sir Clive portrait and the DSOTM light below scale with it)
        self._render_prism(surface, W * 0.5, H * 0.26, self.s(96), t)

        # this run's summary, then the high-score table (leaderboard)
        self._render_run_summary(surface, retro, int(H * 0.44))
        self._render_hiscore_table(surface, retro, int(H * 0.50))

        # blinking prompt
        if t > 50 and (t // 18) % 2 == 0:
            pf = _font(self.s(13), bold=True)
            msg = "PRESS  SPACE  TO  PLAY  AGAIN"
            mw = pf.size(msg)[0]
            retro(msg, (W - mw) // 2, H - self.s(32), pf, (235, 235, 245), 1)

    def _render_hud(self, surface):
        f = _font(self.s(14), bold=True)
        _draw_text(surface, "SCORE %06d" % self._score,
                   self.s(10), self.s(8), f, (170, 255, 170))
        hi = max(self._score, get_alien_hiscore())
        hlabel = "HI %06d" % hi
        hw = f.size(hlabel)[0]
        _draw_text(surface, hlabel, (self.w - hw) // 2, self.s(8),
                   f, (255, 220, 120))
        wlabel = "WAVE %d" % getattr(self, "_wave", 1)
        ww = f.size(wlabel)[0]
        _draw_text(surface, wlabel, self.w - ww - self.s(10), self.s(8),
                   f, (190, 220, 255))


# Backwards-compatible alias: the Unite! pygame visualization still imports the
# old name; both now refer to the Pink Floyd "Alien Floyd" homage scene.
SpaceInvadersBackground = AlienFloydBackground


# ── module-level "Alien Floyd background" enable flag ───────────────────────
# Set by the main window when the optional pygame-ce "Alien Floyd's" background
# mode is turned on in Settings.  Consulted by widgets that self-activate the
# overlay (e.g. the in-pane GalleryItemViewer) so call sites don't have to be
# threaded through with the preference.
_ALIEN_FLOYD_ENABLED = False


def set_alien_floyd_enabled(flag):
    """Enable/disable the global Alien Floyd's background preference."""
    global _ALIEN_FLOYD_ENABLED
    _ALIEN_FLOYD_ENABLED = bool(flag)


def alien_floyd_enabled():
    """True when the Alien Floyd's background mode is currently enabled."""
    return _ALIEN_FLOYD_ENABLED


# ── persistent arcade high-score table (leaderboard) ────────────────────────
# A top-N table of {name, score} entries, shared across every
# AlienFloydBackground instance (global background, the dedicated tab,
# item-viewer overlays, the Unite! pygame view).  It is the single source of
# truth for the high score: only a real *played* game-over with a qualifying
# score writes to it (via the shoot-your-name round), so the auto-playing
# background can never inflate it.  Persistence to hdfg.cfg is delegated to a
# save callback wired by the main window; the table is small, so it is saved
# immediately whenever it changes (no throttling needed).
ALIEN_TABLE_MAX = 5
_ALIEN_TABLE = []                 # list of {"name": str, "score": int}, desc
_ALIEN_TABLE_SAVE_CB = None


def _sanitize_hiname(value):
    """Coerce to the A–Z (max 8) alphabet the name-entry round can produce."""
    try:
        s = str(value).upper()
    except Exception:
        return ""
    s = "".join(ch for ch in s if "A" <= ch <= "Z")
    return s[:8]


def _serialize_table(table):
    """``NAME:SCORE`` pairs joined by ``;`` (INI/cfg-friendly: no newlines)."""
    return ";".join("%s:%d" % (e["name"] or "---", int(e["score"])) for e in table)


def _parse_table(serialized):
    out = []
    for part in str(serialized).split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        nm, _, sc = part.rpartition(":")
        try:
            score = int(sc)
        except ValueError:
            continue
        out.append({"name": _sanitize_hiname(nm) or "---", "score": max(0, score)})
    out.sort(key=lambda e: e["score"], reverse=True)
    return out[:ALIEN_TABLE_MAX]


def init_alien_table(serialized):
    """Seed the high-score table from persisted configuration at startup."""
    global _ALIEN_TABLE
    _ALIEN_TABLE = _parse_table(serialized)


def get_alien_table():
    """A copy of the current top-N table (descending by score)."""
    return list(_ALIEN_TABLE)


def get_alien_hiscore():
    """Top score on the table (0 when empty) — used by the live HUD."""
    return _ALIEN_TABLE[0]["score"] if _ALIEN_TABLE else 0


def set_alien_table_save_cb(cb):
    """Register ``cb(str)`` used to persist the serialized table."""
    global _ALIEN_TABLE_SAVE_CB
    _ALIEN_TABLE_SAVE_CB = cb


def alien_score_qualifies(score):
    """True when *score* would earn a place on the table (so the player should
    get a name-entry round)."""
    try:
        score = int(score)
    except (TypeError, ValueError):
        return False
    if score <= 0:
        return False
    if len(_ALIEN_TABLE) < ALIEN_TABLE_MAX:
        return True
    return score > _ALIEN_TABLE[-1]["score"]


def record_alien_score(name, score):
    """Insert ``(name, score)`` into the table, keep the top N, and persist.
    On a tie the existing holders keep the higher rank (stable sort)."""
    global _ALIEN_TABLE
    try:
        score = max(0, int(score))
    except (TypeError, ValueError):
        score = 0
    _ALIEN_TABLE.append({"name": _sanitize_hiname(name) or "---", "score": score})
    _ALIEN_TABLE.sort(key=lambda e: e["score"], reverse=True)
    del _ALIEN_TABLE[ALIEN_TABLE_MAX:]
    cb = _ALIEN_TABLE_SAVE_CB
    if cb is not None:
        try:
            cb(_serialize_table(_ALIEN_TABLE))
        except Exception:
            pass


class AlienFloydWidget(QWidget):
    """A self-contained QWidget that renders the :class:`AlienFloydBackground`
    animation via pygame, blitting each frame with QPainter (no SDL window).

    Used both as the full-window "Alien Floyd's" tab (opaque) and as a
    transparent overlay above gallery item images (``transparent=True``), where
    only the stars / Floyds / defending ship are drawn over a see-through
    surface so the screenshot underneath stays visible."""

    def __init__(self, parent=None, transparent=False, fps=30, game=False):
        super().__init__(parent)
        # NOTE: pygame is imported lazily off the UI thread (prewarm_async),
        # NOT here.  ``import pygame`` loads the SDL libraries and can block for
        # a second or more (longer on a cold disk cache); doing it in the
        # constructor froze the UI the first time the tab was opened.  The
        # widget paints a lightweight "Loading…" placeholder with QPainter until
        # the import finishes, then builds its pygame surface on the UI thread
        # (that part is cheap, ~a millisecond) and starts animating.
        prewarm_async()
        self._transparent = bool(transparent)
        self._game = bool(game)
        self._surface = None
        self._bg = None
        self._alive = True
        self._dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        if self._transparent:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        else:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        if self._game:
            # The dedicated tab is playable: take keyboard focus for the
            # cursor-key / space controls.
            self.setFocusPolicy(Qt.StrongFocus)
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / max(1, fps)))
        self._timer.timeout.connect(self._tick)
        # Builds the surface as soon as pygame is ready (it may already be, if
        # warm-up finished before construction); otherwise a no-op for now.
        self._ensure_surface()

    def _dev_size(self):
        w = max(1, int(round(self.width() * self._dpr)))
        h = max(1, int(round(self.height() * self._dpr)))
        return w, h

    def _ensure_surface(self):
        pg = _pg
        if pg is None:
            # pygame still importing on the warm-up thread; paint the
            # placeholder for now and retry on the next tick.
            return False
        w, h = self._dev_size()
        if self._surface is None or self._surface.get_size() != (w, h):
            self._surface = pg.Surface((w, h), pg.SRCALPHA)
            if self._bg is None:
                self._bg = AlienFloydBackground((w, h), self._dpr,
                                                game=self._game)
            else:
                try:
                    self._bg.resize((w, h), self._dpr)
                except Exception:
                    pass
        return True

    def start(self):
        if self._alive and not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()

    def teardown(self):
        self._alive = False
        try:
            self._timer.stop()
        except Exception:
            pass
        self._surface = None
        self._bg = None

    def _tick(self):
        if not self._alive:
            return
        if self._bg is None:
            # Still waiting on the off-thread pygame import; try to build the
            # surface now and repaint (placeholder or, once ready, first frame).
            self._ensure_surface()
            self.update()
            return
        try:
            self._bg.update()
        except Exception:
            pass
        self.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.start()
        if self._game:
            self.setFocus()

    def hideEvent(self, ev):
        self.stop()
        if self._game and self._bg is not None:
            # Release any held keys so the ship doesn't keep moving when the tab
            # is hidden and shown again.
            for k in ("left", "right", "fire"):
                try:
                    self._bg.set_key(k, False)
                except Exception:
                    pass
        super().hideEvent(ev)

    # -- keyboard (game mode only) -----------------------------------------
    def _dispatch_key(self, ev, down):
        if not self._game or self._bg is None:
            return False
        # Ignore the synthetic key-release from OS auto-repeat so a held key
        # stays "down" while the player keeps the cursor key pressed.
        if not down and ev.isAutoRepeat():
            return True
        k = ev.key()
        if k in (Qt.Key_Left, Qt.Key_A):
            self._bg.set_key("left", down)
            return True
        if k in (Qt.Key_Right, Qt.Key_D):
            self._bg.set_key("right", down)
            return True
        if k in (Qt.Key_Space, Qt.Key_Up):
            self._bg.set_key("fire", down)
            return True
        return False

    def keyPressEvent(self, ev):
        if self._dispatch_key(ev, True):
            ev.accept()
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if self._dispatch_key(ev, False):
            ev.accept()
            return
        super().keyReleaseEvent(ev)

    def resizeEvent(self, ev):
        self._dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        self._ensure_surface()
        super().resizeEvent(ev)
        self.update()

    def paintEvent(self, _ev):
        painter = QPainter(self)
        if not self._alive or self._surface is None or self._bg is None:
            if not self._transparent:
                painter.fillRect(self.rect(), Qt.black)
                # Friendly placeholder while pygame finishes importing off the
                # UI thread (opaque tab only; transparent overlays show nothing).
                if _pg is None:
                    painter.setPen(QColor(150, 150, 150))
                    f = painter.font()
                    f.setPointSize(max(11, f.pointSize() + 3))
                    painter.setFont(f)
                    painter.drawText(self.rect(), Qt.AlignCenter,
                                     "Loading Alien Floyd's…")
            painter.end()
            return
        self._ensure_surface()
        try:
            self._bg.render(self._surface, transparent=self._transparent)
        except Exception:
            if not self._transparent:
                self._surface.fill(C_BG)
        img = _surface_to_qimage(self._surface)
        img.setDevicePixelRatio(self._dpr)
        painter.drawImage(QPoint(0, 0), img)
        painter.end()


# ── scene base ──────────────────────────────────────────────────────────────
class _Scene:
    def __init__(self):
        self.host = None
        self.size = (0, 0)
        self.dpr = 1.0

    def attach(self, host):
        self.host = host

    def on_attach(self):
        pass

    def on_detach(self):
        pass

    def layout(self, size, dpr):
        self.size = size
        self.dpr = dpr

    def s(self, px):
        """Scale a logical pixel measurement to device pixels."""
        return int(round(px * self.dpr))

    def render(self, surface):
        self._paint_backdrop(surface)

    def _veil_alpha(self):
        return 200

    def _paint_backdrop(self, surface):
        """Fill the surface for this frame.  When the host has an active
        animated background it has already been drawn underneath, so we only
        lay down a translucent veil to keep the foreground readable."""
        host = self.host
        if host is not None and host.background_active():
            _blit_veil(surface, _VEIL_RGB, self._veil_alpha())
        else:
            surface.fill(C_BG)

    def on_event(self, ev):
        pass

    def redraw(self):
        if self.host is not None:
            self.host.request_redraw()


# ── the Qt host widget ──────────────────────────────────────────────────────
class PygameSurfaceWidget(QWidget):
    """A QWidget that hosts one active ``_Scene`` rendered via pygame."""

    def __init__(self, parent=None):
        super().__init__(parent)
        _ensure_pg()
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._surface = None
        self._scene = None
        self._dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        self._alive = True
        # Animated Space-Invaders background + its frame clock (~30fps).
        self._bg_enabled = True
        self._bg = None
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._on_anim_tick)
        self._ensure_surface()

    # -- animated background ----------------------------------------------
    def background_active(self):
        return self._bg_enabled and self._bg is not None

    def enable_background(self, flag):
        self._bg_enabled = bool(flag)
        if self._bg_enabled and self.isVisible():
            self._anim.start()
        elif not self._bg_enabled:
            self._anim.stop()
        self.update()

    def _on_anim_tick(self):
        if not self._alive or not self.background_active():
            return
        try:
            self._bg.update()
        except Exception:
            pass
        self.update()

    # -- scene management --------------------------------------------------
    def set_scene(self, scene):
        old = self._scene
        if old is not None and old is not scene:
            try:
                old.on_detach()
            except Exception:
                pass
        self._scene = scene
        if scene is not None:
            scene.attach(self)
            self._ensure_surface()
            try:
                scene.layout(self._surface.get_size(), self._dpr)
                scene.on_attach()
            except Exception:
                pass
        self.update()

    def scene(self):
        return self._scene

    def request_redraw(self):
        if self._alive:
            self.update()

    def teardown(self):
        self._alive = False
        try:
            self._anim.stop()
        except Exception:
            pass
        if self._scene is not None:
            try:
                self._scene.on_detach()
            except Exception:
                pass
        self._scene = None
        self._surface = None
        self._bg = None

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._alive and self._bg_enabled:
            self._anim.start()

    def hideEvent(self, ev):
        self._anim.stop()
        super().hideEvent(ev)

    # -- surface sizing ----------------------------------------------------
    def _dev_size(self):
        w = max(1, int(round(self.width() * self._dpr)))
        h = max(1, int(round(self.height() * self._dpr)))
        return w, h

    def _ensure_surface(self):
        pg = _pg
        w, h = self._dev_size()
        if self._surface is None or self._surface.get_size() != (w, h):
            self._surface = pg.Surface((w, h), pg.SRCALPHA)
            if self._bg is None:
                self._bg = SpaceInvadersBackground((w, h), self._dpr)
            else:
                try:
                    self._bg.resize((w, h), self._dpr)
                except Exception:
                    pass
            if self._scene is not None:
                try:
                    self._scene.layout((w, h), self._dpr)
                except Exception:
                    pass

    # -- Qt events ---------------------------------------------------------
    def resizeEvent(self, ev):
        self._dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        _VEIL_CACHE.clear()
        self._ensure_surface()
        super().resizeEvent(ev)
        self.update()

    def paintEvent(self, _ev):
        painter = QPainter(self)
        if not self._alive or self._surface is None or self._scene is None:
            painter.fillRect(self.rect(), Qt.black)
            painter.end()
            return
        self._ensure_surface()
        try:
            if self.background_active():
                self._bg.render(self._surface)
            self._scene.render(self._surface)
        except Exception:
            self._surface.fill(C_BG)
        img = _surface_to_qimage(self._surface)
        img.setDevicePixelRatio(self._dpr)
        painter.drawImage(QPoint(0, 0), img)
        painter.end()

    # -- input bridge ------------------------------------------------------
    def _pos(self, ev):
        try:
            p = ev.position()
            return (p.x() * self._dpr, p.y() * self._dpr)
        except Exception:
            p = ev.pos()
            return (p.x() * self._dpr, p.y() * self._dpr)

    @staticmethod
    def _btn(ev):
        b = ev.button()
        if b == Qt.LeftButton:
            return "left"
        if b == Qt.RightButton:
            return "right"
        return "other"

    def _send(self, ev_dict):
        if self._scene is None:
            return
        try:
            self._scene.on_event(ev_dict)
        except Exception:
            pass

    def mousePressEvent(self, ev):
        x, y = self._pos(ev)
        self._send({"type": "press", "x": x, "y": y, "button": self._btn(ev)})

    def mouseReleaseEvent(self, ev):
        x, y = self._pos(ev)
        self._send({"type": "release", "x": x, "y": y, "button": self._btn(ev)})

    def mouseDoubleClickEvent(self, ev):
        x, y = self._pos(ev)
        self._send({"type": "dblclick", "x": x, "y": y, "button": self._btn(ev)})

    def mouseMoveEvent(self, ev):
        x, y = self._pos(ev)
        self._send({"type": "move", "x": x, "y": y})

    def wheelEvent(self, ev):
        try:
            dy = ev.angleDelta().y()
        except Exception:
            dy = 0
        x, y = self._pos(ev)
        self._send({"type": "wheel", "x": x, "y": y, "dy": dy})

    def keyPressEvent(self, ev):
        self._send({"type": "key", "key": ev.key()})

    def leaveEvent(self, _ev):
        self._send({"type": "move", "x": -1, "y": -1})


# ── shared button model ─────────────────────────────────────────────────────
class _Button:
    __slots__ = ("key", "label", "cb", "enabled", "visible", "tooltip", "rect")

    def __init__(self, key, label):
        self.key = key
        self.label = label
        self.cb = None
        self.enabled = False
        self.visible = False
        self.tooltip = ""
        self.rect = None


# ── Table scene ─────────────────────────────────────────────────────────────
class TableScene(_Scene):
    """Source / Title / Rating / Info / Year table over the current page."""

    def __init__(self, source_label_getter, title_getter, info_getter,
                 open_cb):
        super().__init__()
        self._src_lbl = source_label_getter
        self._title_getter = title_getter
        self._info_getter = info_getter
        self._open_cb = open_cb
        self._entries = []
        self._rows = []          # cached (src, title, rating, info, year)
        self._scroll = 0
        self._sel = -1
        self._hover = -1

    def set_entries(self, entries):
        self._entries = list(entries or [])
        self._rows = []
        for e in self._entries:
            if not isinstance(e, dict):
                self._rows.append(("", "", "", "", ""))
                continue
            src = self._src_lbl(e) if self._src_lbl else ""
            title, rating = _split_title_rating(
                self._title_getter(e) if self._title_getter else "")
            info = _strip_html(self._info_getter(e) if self._info_getter else "")
            year = str(e.get("year") or "")
            self._rows.append((src, title, rating, info, year))
        self._scroll = 0
        self._sel = -1
        self.redraw()

    # geometry
    def _row_h(self):
        return self.s(30)

    def _cols(self, total_w):
        m = self.s(10)
        src_w = self.s(70)
        rating_w = self.s(80)
        year_w = self.s(60)
        rest = max(self.s(120), total_w - src_w - rating_w - year_w - 2 * m)
        title_w = int(rest * 0.45)
        info_w = rest - title_w
        x = m
        cols = []
        for w in (src_w, title_w, rating_w, info_w, year_w):
            cols.append((x, w))
            x += w
        return cols

    def _row_at(self, y):
        rh = self._row_h()
        header = rh
        if y < header:
            return -1
        idx = int((y - header + self._scroll) // rh)
        if 0 <= idx < len(self._rows):
            return idx
        return -1

    def render(self, surface):
        self._paint_backdrop(surface)
        w, h = self.size
        rh = self._row_h()
        cols = self._cols(w)
        headers = ("Source", "Title", "Rating", "Info", "Year")
        hf = _font(self.s(11), bold=True)
        rf = _font(self.s(11))
        # header
        surface.fill(C_HEADER, _pg.Rect(0, 0, w, rh))
        for (x, cw), name in zip(cols, headers):
            _draw_text(surface, name, x, (rh - hf.get_height()) // 2, hf, C_TEXT_DIM)
        # rows
        y0 = rh - self._scroll
        for i, row in enumerate(self._rows):
            ry = y0 + i * rh
            if ry + rh < rh or ry > h:
                continue
            if i == self._sel:
                surface.fill(C_SEL, _pg.Rect(0, ry, w, rh))
            elif i == self._hover:
                surface.fill(C_HOVER, _pg.Rect(0, ry, w, rh))
            ty = ry + (rh - rf.get_height()) // 2
            for (x, cw), val in zip(cols, row):
                _draw_text(surface, _elide(val, rf, cw - self.s(8)), x, ty, rf, C_TEXT)
            _pg.draw.line(surface, C_GRID_LINE, (0, ry + rh - 1), (w, ry + rh - 1))

    def _max_scroll(self):
        rh = self._row_h()
        content = len(self._rows) * rh
        view = max(0, self.size[1] - rh)
        return max(0, content - view)

    def on_event(self, ev):
        t = ev.get("type")
        if t == "move":
            self._hover = self._row_at(ev["y"])
            self.redraw()
        elif t == "wheel":
            self._scroll = max(0, min(self._max_scroll(),
                                      self._scroll - int(ev["dy"] * 0.5)))
            self.redraw()
        elif t == "press" and ev.get("button") == "left":
            idx = self._row_at(ev["y"])
            if idx >= 0:
                self._sel = idx
                self.redraw()
        elif t == "dblclick" and ev.get("button") == "left":
            idx = self._row_at(ev["y"])
            if idx >= 0 and self._open_cb:
                self._open_cb(self._entries[idx])


# ── Gallery scene ───────────────────────────────────────────────────────────
class GalleryScene(_Scene):
    """Thumbnail grid with source badge + favorite heart over the page."""

    def __init__(self, title_getter, source_label_getter, thumb_fetch_cb,
                 is_favorite_cb, toggle_favorite_cb, open_cb, cols_getter=None):
        super().__init__()
        self._title_getter = title_getter
        self._src_lbl = source_label_getter
        self._thumb_fetch = thumb_fetch_cb
        self._is_fav = is_favorite_cb
        self._toggle_fav = toggle_favorite_cb
        self._open_cb = open_cb
        self._cols_getter = cols_getter
        self._entries = []
        self._surfs = {}        # index -> scaled Surface
        self._requested = set()
        self._scroll = 0
        self._hover = -1

    def set_entries(self, entries):
        self._entries = list(entries or [])
        self._surfs = {}
        self._requested = set()
        self._scroll = 0
        self._hover = -1
        self._fetch_visible()
        self.redraw()

    def on_attach(self):
        self._fetch_visible()

    def _cols(self):
        if self._cols_getter:
            try:
                return max(1, int(self._cols_getter()))
            except Exception:
                pass
        return 4

    def _cell_size(self):
        cols = self._cols()
        gap = self.s(8)
        w = self.size[0]
        cw = max(self.s(80), (w - gap * (cols + 1)) // cols)
        ch = int(cw * 0.82) + self.s(22)   # image (~4:3) + title strip
        return cw, ch, gap

    def _cell_rect(self, idx):
        cols = self._cols()
        cw, ch, gap = self._cell_size()
        r, c = divmod(idx, cols)
        x = gap + c * (cw + gap)
        y = gap + r * (ch + gap) - self._scroll
        return _pg.Rect(x, y, cw, ch)

    def _fetch_visible(self):
        if not self._thumb_fetch:
            return
        for i, e in enumerate(self._entries):
            if i in self._requested or not isinstance(e, dict):
                continue
            self._requested.add(i)

            def _on_px(px, _url=None, _i=i):
                surf = qpixmap_to_surface(px)
                if surf is not None:
                    self._surfs[_i] = surf
                    self.redraw()

            try:
                self._thumb_fetch(e, _on_px, lambda *_: None)
            except Exception:
                pass

    def _max_scroll(self):
        cols = self._cols()
        _cw, ch, gap = self._cell_size()
        rows = (len(self._entries) + cols - 1) // cols
        content = gap + rows * (ch + gap)
        return max(0, content - self.size[1])

    def _idx_at(self, x, y):
        for i in range(len(self._entries)):
            if self._cell_rect(i).collidepoint(x, y):
                return i
        return -1

    def _heart_rect(self, cell):
        hs = self.s(20)
        return _pg.Rect(cell.right - hs - self.s(4), cell.top + self.s(4), hs, hs)

    def render(self, surface):
        self._paint_backdrop(surface)
        cw, ch, _gap = self._cell_size()
        img_h = int(cw * 0.82)
        tf = _font(self.s(10))
        bf = _font(self.s(9), bold=True)
        for i, e in enumerate(self._entries):
            cell = self._cell_rect(i)
            if cell.bottom < 0 or cell.top > self.size[1]:
                continue
            surface.fill(C_PANEL, cell)
            if i == self._hover:
                _pg.draw.rect(surface, C_BORDER, cell, self.s(2))
            img_rect = _pg.Rect(cell.x, cell.y, cw, img_h)
            surface.fill(C_IMG_BG, img_rect)
            surf = self._surfs.get(i)
            if surf is not None:
                scaled = _scale_keep_aspect(surf, cw, img_h)
                sw, sh = scaled.get_size()
                surface.blit(scaled, (cell.x + (cw - sw) // 2, cell.y + (img_h - sh) // 2))
            else:
                lbl = "…"
                _draw_text(surface, lbl, cell.centerx - self.s(4),
                           cell.y + img_h // 2 - self.s(6), tf, C_TEXT_OFF)
            # title strip
            title, _r = _split_title_rating(
                self._title_getter(e) if self._title_getter else "")
            _draw_text(surface, _elide(title, tf, cw - self.s(6)),
                       cell.x + self.s(3), cell.y + img_h + self.s(3), tf, C_TEXT)
            # source badge (bottom-right of image)
            src = self._src_lbl(e) if self._src_lbl else ""
            if src:
                pad = self.s(4)
                tw = bf.size(src)[0]
                bw, bh = tw + 2 * pad, bf.get_height() + pad
                br = _pg.Rect(img_rect.right - bw - self.s(3),
                              img_rect.bottom - bh - self.s(3), bw, bh)
                surface.fill(C_BADGE_BG, br)
                _draw_text(surface, src, br.x + pad, br.y + pad // 2, bf, C_BADGE_TX)
            # favorite heart (top-right of image)
            if self._toggle_fav is not None:
                is_fav = False
                if self._is_fav is not None:
                    try:
                        is_fav = bool(self._is_fav(e))
                    except Exception:
                        is_fav = False
                hr = self._heart_rect(cell)
                _draw_text(surface, "♥" if is_fav else "♡", hr.x, hr.y,
                           _font(self.s(15)), C_HEART)

    def on_event(self, ev):
        t = ev.get("type")
        if t == "move":
            self._hover = self._idx_at(ev["x"], ev["y"])
            self.redraw()
        elif t == "wheel":
            self._scroll = max(0, min(self._max_scroll(),
                                      self._scroll - int(ev["dy"] * 0.6)))
            self.redraw()
        elif t == "press" and ev.get("button") == "left":
            idx = self._idx_at(ev["x"], ev["y"])
            if idx >= 0 and self._toggle_fav is not None:
                if self._heart_rect(self._cell_rect(idx)).collidepoint(ev["x"], ev["y"]):
                    try:
                        self._toggle_fav(self._entries[idx])
                    except Exception:
                        pass
                    self.redraw()
        elif t == "dblclick" and ev.get("button") == "left":
            idx = self._idx_at(ev["x"], ev["y"])
            if idx >= 0 and self._open_cb:
                self._open_cb(self._entries[idx])


# ── Item viewer scene (duck-types GalleryItemViewer's public API) ───────────
class PygameItemViewer(_Scene):
    """Mirror of zxnu_gallery.GalleryItemViewer, rendered in pygame.

    Constructed by the same factory call the source openers use, so it accepts
    the identical keyword arguments and exposes the same public methods.
    """

    _ACTION_ORDER = ("open_web", "download", "send_sd",
                     "launch_cspect", "launch_mame", "send_ns")

    def __init__(self, host, title="", info_rows=None, screenshots=None,
                 extra_fetch_cb=None, tags=None):
        super().__init__()
        self.attach(host)
        self._title = title or ""
        self._rows = list(info_rows or [])
        self._screens = [u for u in (screenshots or []) if u]
        self._extra_fetch = extra_fetch_cb
        self._tags = [str(t) for t in (tags or []) if t]
        self._shot_idx = 0
        self._shot_cache = {}          # url -> Surface
        self._close_fn = None
        self._placeholder = ("", "")
        self._meta_scroll = 0

        # favorites
        self._fav_entry = None
        self._is_fav = None
        self._toggle_fav = None

        # actions
        self._buttons = {k: _Button(k, "") for k in self._ACTION_ORDER}
        self._buttons["open_web"].label = "🌐  Open on website"
        self._buttons["download"].label = "⬇  Download"
        self._buttons["send_sd"].label = "💾  Send to SD card"
        self._buttons["launch_cspect"].label = "🕹  Launch CSpect"
        self._buttons["launch_mame"].label = "🕹  Launch Mame"
        self._buttons["send_ns"].label = "🔁  Send via NextSync"

        # hit-test rects (device px, recomputed each render)
        self._close_rect = None
        self._heart_rect = None
        self._prev_rect = None
        self._next_rect = None
        self._hover_btn = None

        # slideshow timer (own QTimer, like the Qt viewer)
        self._timer = QTimer()
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._go_next)

        if self._screens:
            self._prefetch_all()

    # -- public API (matches GalleryItemViewer) ---------------------------
    def install_into_stack(self, _stack, close_fn=None):
        self._close_fn = close_fn
        if self.host is not None:
            self.host.set_scene(self)

    def on_attach(self):
        if len(self._screens) > 1:
            self._timer.start()

    def on_detach(self):
        try:
            self._timer.stop()
        except Exception:
            pass

    def set_favorite_hooks(self, entry, is_favorite_cb, toggle_favorite_cb):
        self._fav_entry = entry
        self._is_fav = is_favorite_cb
        self._toggle_fav = toggle_favorite_cb
        self.redraw()

    def set_actions(self, download_cb=None, send_sd_cb=None, send_ns_cb=None,
                    sd_enabled=False, ns_enabled=False,
                    sd_tooltip="", ns_tooltip=""):
        self._wire("download", download_cb, True)
        self._wire("send_sd", send_sd_cb, sd_enabled, sd_tooltip)
        self._wire("send_ns", send_ns_cb, ns_enabled, ns_tooltip)
        self.redraw()

    def set_emulator_actions(self, cspect_cb=None, mame_cb=None,
                             cspect_enabled=False, mame_enabled=False,
                             cspect_tooltip="", mame_tooltip=""):
        self._wire("launch_cspect", cspect_cb,
                   bool(cspect_enabled) and cspect_cb is not None, cspect_tooltip)
        self._wire("launch_mame", mame_cb,
                   bool(mame_enabled) and mame_cb is not None, mame_tooltip)
        self._buttons["launch_cspect"].visible = cspect_cb is not None
        self._buttons["launch_mame"].visible = mame_cb is not None
        self.redraw()

    def set_open_web_url(self, url, site_label=""):
        url = (url or "").strip()
        b = self._buttons["open_web"]
        if not url:
            b.visible = False
            b.enabled = False
            b.cb = None
            self.redraw()
            return
        import webbrowser
        b.label = f"🌐  Open on {site_label}" if site_label else "🌐  Open on website"
        b.tooltip = url
        b.visible = True
        b.enabled = True
        b.cb = lambda _u=url: webbrowser.open(_u, new=2)
        self.redraw()

    def set_download_available(self, has_dl):
        for k in ("download", "send_sd", "send_ns"):
            self._buttons[k].visible = bool(has_dl)
        self.redraw()

    def set_placeholder(self, label, subtitle=""):
        self._placeholder = (str(label or ""), str(subtitle or "") or self._title)
        self.redraw()

    def set_screenshots(self, urls):
        self._timer.stop()
        self._screens = [u for u in (urls or []) if u]
        self._shot_idx = 0
        self._shot_cache = {}
        if self._screens:
            self._prefetch_all()
            if len(self._screens) > 1 and self.host is not None and self.host.scene() is self:
                self._timer.start()
        self.redraw()

    def refresh_meta(self, title, rows):
        self._title = title or self._title
        self._rows = list(rows or [])
        self._meta_scroll = 0
        self.redraw()

    def set_tags(self, tags):
        self._tags = [str(t) for t in (tags or []) if t]
        self.redraw()

    # -- internals ---------------------------------------------------------
    def _wire(self, key, cb, enabled, tooltip=""):
        b = self._buttons[key]
        b.cb = cb
        b.enabled = bool(enabled) and cb is not None
        if tooltip:
            b.tooltip = tooltip
        if key in ("download", "send_sd", "send_ns") and cb is not None:
            b.visible = True

    def _prefetch_all(self):
        if not self._extra_fetch:
            return
        for url in list(self._screens):
            if url in self._shot_cache:
                continue

            def _on_px(px, _u=url):
                surf = qpixmap_to_surface(px)
                if surf is None:
                    self._drop_url(_u)
                    return
                self._shot_cache[_u] = surf
                self.redraw()

            try:
                self._extra_fetch(url, _on_px)
            except Exception:
                self._drop_url(url)

    def _drop_url(self, url):
        if url not in self._screens:
            return
        i = self._screens.index(url)
        del self._screens[i]
        self._shot_cache.pop(url, None)
        if not self._screens:
            self._timer.stop()
            self._shot_idx = 0
        elif self._shot_idx >= len(self._screens):
            self._shot_idx = 0
        self.redraw()

    def _go_prev(self):
        if not self._screens:
            return
        self._shot_idx = (self._shot_idx - 1) % len(self._screens)
        self.redraw()

    def _go_next(self):
        if not self._screens:
            return
        self._shot_idx = (self._shot_idx + 1) % len(self._screens)
        self.redraw()

    def _close(self):
        self._timer.stop()
        if self._close_fn:
            try:
                self._close_fn()
            except Exception:
                pass

    # -- rendering ---------------------------------------------------------
    def _panel_w(self):
        w = self.size[0]
        return int(min(self.s(400), max(self.s(300), w * 0.34)))

    def render(self, surface):
        self._paint_backdrop(surface)
        w, h = self.size
        pw = self._panel_w()
        img_w = w - pw
        # left image area
        img_area = _pg.Rect(self.s(8), self.s(8), img_w - self.s(16), h - self.s(16))
        surface.fill(C_IMG_BG, _pg.Rect(0, 0, img_w, h))
        self._render_image(surface, img_area)
        # right panel
        panel = _pg.Rect(img_w, 0, pw, h)
        surface.fill(C_PANEL, panel)
        self._render_panel(surface, panel)

    def _render_image(self, surface, area):
        surf = None
        if self._screens:
            url = self._screens[self._shot_idx % len(self._screens)]
            surf = self._shot_cache.get(url)
        if surf is not None:
            nav_h = self.s(26)
            scaled = _scale_keep_aspect(surf, area.w, area.h - nav_h)
            sw, sh = scaled.get_size()
            surface.blit(scaled, (area.x + (area.w - sw) // 2,
                                  area.y + (area.h - nav_h - sh) // 2))
            self._render_nav(surface, area)
        else:
            label, subtitle = self._placeholder
            f1 = _font(self.s(28), bold=True)
            msg = label or ("Loading…" if self._screens else "No preview available")
            tw = f1.size(msg)[0]
            _draw_text(surface, msg, area.centerx - tw // 2,
                       area.centery - self.s(20), f1,
                       (240, 210, 80) if label else C_TEXT_OFF)
            if subtitle:
                f2 = _font(self.s(12))
                sw = f2.size(_elide(subtitle, f2, area.w))[0]
                _draw_text(surface, _elide(subtitle, f2, area.w),
                           area.centerx - sw // 2, area.centery + self.s(18),
                           f2, C_TEXT_DIM)
            self._prev_rect = self._next_rect = None
        # tag overlay (top-right of image)
        if self._tags:
            self._render_tags(surface, area)

    def _render_nav(self, surface, area):
        if len(self._screens) <= 1:
            self._prev_rect = self._next_rect = None
            return
        f = _font(self.s(16))
        y = area.bottom - self.s(24)
        cx = area.centerx
        cnt = f"{self._shot_idx + 1} / {len(self._screens)}"
        cw = f.size(cnt)[0]
        self._prev_rect = _pg.Rect(cx - cw // 2 - self.s(44), y, self.s(34), self.s(22))
        self._next_rect = _pg.Rect(cx + cw // 2 + self.s(10), y, self.s(34), self.s(22))
        surface.fill(C_BTN, self._prev_rect)
        surface.fill(C_BTN, self._next_rect)
        _draw_text(surface, "◀", self._prev_rect.x + self.s(9), y + self.s(1), f, C_TITLE)
        _draw_text(surface, "▶", self._next_rect.x + self.s(9), y + self.s(1), f, C_TITLE)
        _draw_text(surface, cnt, cx - cw // 2, y + self.s(3), _font(self.s(11)), C_TEXT_DIM)

    def _render_tags(self, surface, area):
        f = _font(self.s(10), bold=True)
        x = area.right
        y = area.y + self.s(4)
        pad = self.s(5)
        for t in self._tags[:6]:
            tw = f.size(t)[0]
            bw = tw + 2 * pad
            x -= bw + self.s(4)
            r = _pg.Rect(x, y, bw, f.get_height() + pad)
            surface.fill(C_BADGE_BG, r)
            _draw_text(surface, t, r.x + pad, r.y + pad // 2, f, C_BADGE_TX)

    def _render_panel(self, surface, panel):
        pad = self.s(12)
        # close bar (heart + ✕)
        bar_h = self.s(30)
        cs = self.s(24)
        self._close_rect = _pg.Rect(panel.right - cs - self.s(8),
                                    self.s(6), cs, cs)
        surface.fill(C_BTN, self._close_rect)
        _draw_text(surface, "✕", self._close_rect.x + self.s(6),
                   self._close_rect.y + self.s(3), _font(self.s(13)), C_TITLE)
        if self._toggle_fav is not None and self._fav_entry is not None:
            is_fav = False
            if self._is_fav is not None:
                try:
                    is_fav = bool(self._is_fav(self._fav_entry))
                except Exception:
                    is_fav = False
            self._heart_rect = _pg.Rect(self._close_rect.x - cs - self.s(6),
                                        self.s(6), cs, cs)
            surface.fill(C_BTN, self._heart_rect)
            _draw_text(surface, "♥" if is_fav else "♡", self._heart_rect.x + self.s(5),
                       self._heart_rect.y + self.s(2), _font(self.s(15)), C_HEART)
        else:
            self._heart_rect = None

        # action bar height (drawn at bottom)
        vis_btns = [self._buttons[k] for k in self._ACTION_ORDER
                    if self._buttons[k].visible]
        btn_h = self.s(30)
        gap = self.s(6)
        ab_h = (btn_h + gap) * len(vis_btns) + gap if vis_btns else 0

        # metadata region (between close bar and action bar)
        meta_top = bar_h + self.s(6)
        meta_bottom = panel.bottom - ab_h
        self._render_meta(surface, panel, meta_top, meta_bottom, pad)

        # action buttons
        y = panel.bottom - ab_h + gap
        f = _font(self.s(11), bold=True)
        for b in vis_btns:
            r = _pg.Rect(panel.x + pad, y, panel.w - 2 * pad, btn_h)
            b.rect = r
            col = C_BTN_HOVER if (self._hover_btn is b and b.enabled) else (
                C_BTN if b.enabled else C_BTN_DIS)
            surface.fill(col, r)
            _pg.draw.rect(surface, C_BORDER, r, 1)
            tcol = C_TEXT if b.enabled else C_TEXT_OFF
            _draw_text(surface, _elide(b.label, f, r.w - self.s(12)),
                       r.x + self.s(8), r.y + (btn_h - f.get_height()) // 2, f, tcol)
            y += btn_h + gap

    def _render_meta(self, surface, panel, top, bottom, pad):
        clip = _pg.Rect(panel.x, top, panel.w, max(0, bottom - top))
        prev_clip = surface.get_clip()
        surface.set_clip(clip)
        x = panel.x + pad
        maxw = panel.w - 2 * pad
        y = top - self._meta_scroll
        # title
        tf = _font(self.s(15), bold=True)
        for line in _wrap_lines(self._title, tf, maxw):
            _draw_text(surface, line, x, y, tf, C_TITLE)
            y += tf.get_height() + self.s(2)
        y += self.s(4)
        _pg.draw.line(surface, C_GRID_LINE, (x, y), (panel.right - pad, y))
        y += self.s(6)
        lf = _font(self.s(10), bold=True)
        vf = _font(self.s(11))
        for row in (self._rows or []):
            label = row[0] if len(row) > 0 else ""
            value = _strip_html(row[1] if len(row) > 1 else "")
            if not value:
                continue
            _draw_text(surface, str(label), x, y, lf, C_TEXT_DIM)
            y += lf.get_height() + self.s(1)
            for line in _wrap_lines(value, vf, maxw):
                _draw_text(surface, line, x, y, vf, C_TEXT)
                y += vf.get_height() + self.s(1)
            y += self.s(6)
        self._meta_content_h = (y + self._meta_scroll) - top
        surface.set_clip(prev_clip)

    def _meta_max_scroll(self, top, bottom):
        return max(0, getattr(self, "_meta_content_h", 0) - (bottom - top))

    # -- events ------------------------------------------------------------
    def on_event(self, ev):
        t = ev.get("type")
        if t == "key":
            k = ev.get("key")
            if k == Qt.Key_Escape:
                self._close()
            elif k == Qt.Key_Left:
                self._go_prev()
            elif k == Qt.Key_Right:
                self._go_next()
            return
        if t == "wheel":
            pw = self._panel_w()
            if ev["x"] >= self.size[0] - pw:
                bar_h = self.s(30) + self.s(6)
                vis = sum(1 for k in self._ACTION_ORDER if self._buttons[k].visible)
                ab_h = (self.s(30) + self.s(6)) * vis + self.s(6) if vis else 0
                top = bar_h
                bottom = self.size[1] - ab_h
                self._meta_scroll = max(0, min(self._meta_max_scroll(top, bottom),
                                               self._meta_scroll - int(ev["dy"] * 0.5)))
                self.redraw()
            return
        x = ev.get("x", -1)
        y = ev.get("y", -1)
        if t == "move":
            self._hover_btn = None
            for b in self._buttons.values():
                if b.visible and b.rect is not None and b.rect.collidepoint(x, y):
                    self._hover_btn = b
                    break
            self.redraw()
            return
        if t in ("press", "dblclick") and ev.get("button") == "left":
            if self._close_rect and self._close_rect.collidepoint(x, y):
                self._close()
                return
            if self._heart_rect and self._heart_rect.collidepoint(x, y):
                if self._toggle_fav and self._fav_entry is not None:
                    try:
                        self._toggle_fav(self._fav_entry)
                    except Exception:
                        pass
                    self.redraw()
                return
            if self._prev_rect and self._prev_rect.collidepoint(x, y):
                self._go_prev()
                return
            if self._next_rect and self._next_rect.collidepoint(x, y):
                self._go_next()
                return
            # clicking the image closes the viewer (mirrors Qt eventFilter)
            for b in self._buttons.values():
                if b.visible and b.enabled and b.rect is not None \
                        and b.rect.collidepoint(x, y) and b.cb is not None:
                    try:
                        b.cb()
                    except Exception:
                        pass
                    return
            if x < self.size[0] - self._panel_w() and t == "press":
                self._close()
