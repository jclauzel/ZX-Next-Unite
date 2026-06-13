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
import time as _time

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QImage, QPainter
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
    if f is None:
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
    if fonts is None:
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
_C_SKY = (7, 8, 16)

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

# Currency glyphs a star can briefly flicker into, and their glow colour.
_CURRENCY = ("$", "£", "€")
_C_CURRENCY = (255, 220, 120)


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

    def __init__(self, size, dpr=1.0):
        _ensure_pg()
        self.dpr = max(1.0, float(dpr or 1.0))
        self.w, self.h = size
        self._t = 0
        self._init_sprites()
        self._init_stars()
        self._bullets = []
        self._bombs = []
        self._explosions = []
        self._divers = []
        self._dive_cd = _random.randint(20, 60)
        self._score = 0
        self._ship_x = self.w * 0.5
        self._ship_v = 0.0
        self._fire_cd = 0
        self._ufo = None
        self._ufo_cd = _random.randint(120, 360)
        self._new_wave(first=True)

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
        self._cannon = _make_sprite(_CANNON, _C_SHIP, px)
        self._ufo_spr = _make_sprite(_UFO, _C_UFO, px)
        self._step_x = self._alien_w + self.s(10)
        self._step_y = self._alien_h + self.s(6)
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
        total_w = self.COLS * self._step_x
        self._start_x = max(self.s(10), (self.w - total_w) / 2)
        self._top_y = self.s(24)
        self._aliens = [[True] * self.COLS for _ in range(self.ROWS)]
        # Each alien is one of the Floyd kinds, remembered per cell so it can
        # randomly "turn into" another over time.  Per-cell phases stagger the
        # soft Bézier bob so the swarm undulates rather than moving as a block.
        nkinds = len(_FLOYD_NAMES)
        self._kind = [[_random.randrange(nkinds) for _ in range(self.COLS)]
                      for _ in range(self.ROWS)]
        self._bob_ph = [[_random.uniform(0.0, 1.0) for _ in range(self.COLS)]
                        for _ in range(self.ROWS)]
        self._fx = 0.0
        self._fy = 0.0
        self._dir = 1
        base = 0.4 if first else 0.55
        self._speed = (base + 0.1 * getattr(self, "_wave", 0)) * self.dpr
        self._wave = getattr(self, "_wave", 0) + 1

    def resize(self, size, dpr=None):
        self.w, self.h = size
        if dpr:
            self.dpr = max(1.0, float(dpr))
        self._init_sprites()
        self._init_stars()
        self._new_wave(first=True)
        self._wave = 1
        self._divers = []
        self._ship_x = min(self._ship_x, self.w - self.s(10))

    # -- geometry ----------------------------------------------------------
    def _alien_pos(self, r, c):
        bx = self._start_x + c * self._step_x + self._fx
        by = self._top_y + r * self._step_y + self._fy
        # Soft Bézier bob: a gentle, eased up/down drift unique to each cell.
        bob = self._bob_amp * _bezier_wave(self._t * 0.012
                                           + self._bob_ph[r][c]
                                           + (r + c) * 0.13)
        return (bx, by + bob)

    def _alive_bounds(self):
        minx, maxx, maxy = None, None, None
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                x, y = self._alien_pos(r, c)
                minx = x if minx is None else min(minx, x)
                maxx = x if maxx is None else max(maxx, x + self._alien_w)
                maxy = y + self._alien_h if maxy is None else max(maxy, y + self._alien_h)
        return minx, maxx, maxy

    def _any_alive(self):
        return any(any(row) for row in self._aliens)

    # -- simulation --------------------------------------------------------
    def update(self):
        self._t += 1
        self._update_stars()
        self._update_formation()
        self._update_divers()
        self._update_ship()
        self._update_bullets()
        self._update_bombs()
        self._update_ufo()
        self._update_explosions()
        note_alien_score(self._score)

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
        if not self._any_alive():
            self._new_wave()
            return
        minx, maxx, maxy = self._alive_bounds()
        margin = self.s(10)
        self._fx += self._dir * self._speed
        if maxx is not None and (maxx >= self.w - margin or minx <= margin):
            self._dir *= -1
            self._fy += self.s(8)
        # If the swarm drifts past the bottom, regroup at the top.
        if maxy is not None and maxy >= self.h - self.s(46):
            self._fy = 0.0
            self._top_y = self.s(24)
            self._aliens = [[True] * self.COLS for _ in range(self.ROWS)]
        # Occasionally an alien drops a bomb.
        if self._t % 40 == 0:
            alive = [(r, c) for r in range(self.ROWS) for c in range(self.COLS)
                     if self._aliens[r][c]]
            if alive:
                r, c = _random.choice(alive)
                x, y = self._alien_pos(r, c)
                self._bombs.append([x + self._alien_w / 2, y + self._alien_h])
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
                    cx = x + self._alien_w / 2
                    if best is None:
                        best = cx
                    break
        return best

    def _update_ship(self):
        target = self._lowest_alien_x()
        if target is None:
            target = self.w * 0.5
        # Also consider intercepting the mystery UFO when one is on screen.
        aim = target
        if self._ufo is not None:
            ux = self._ufo[0] + self._ufo_spr.get_width() / 2
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
        elif abs(self._ship_x - aim) < self.s(46) or self._ufo is not None \
                or self._divers:
            self._bullets.append([self._ship_x, self.h - self.s(34)])
            self._fire_cd = _random.randint(4, 10)

    def _update_bullets(self):
        spd = self.s(7)
        alive_bullets = []
        for b in self._bullets:
            b[1] -= spd
            if b[1] < -self.s(4):
                continue
            hit = False
            for r in range(self.ROWS):
                for c in range(self.COLS):
                    if not self._aliens[r][c]:
                        continue
                    x, y = self._alien_pos(r, c)
                    if x <= b[0] <= x + self._alien_w and y <= b[1] <= y + self._alien_h:
                        self._aliens[r][c] = False
                        self._spawn_explosion(x + self._alien_w / 2,
                                              y + self._alien_h / 2,
                                              _ALIEN_ROW_COLORS[r])
                        self._score += 10
                        hit = True
                        break
                if hit:
                    break
            # diving Floyds are destroyable too
            if not hit:
                for d in list(self._divers):
                    dx, dy = self._diver_pos(d)
                    if dx <= b[0] <= dx + self._alien_w and dy <= b[1] <= dy + self._alien_h:
                        self._spawn_explosion(dx + self._alien_w / 2,
                                              dy + self._alien_h / 2, _C_UFO)
                        try:
                            self._divers.remove(d)
                        except ValueError:
                            pass
                        self._score += 50
                        hit = True
                        break
            if not hit:
                if self._ufo and self._ufo[0] <= b[0] <= self._ufo[0] + self._ufo_spr.get_width() \
                        and self._ufo[1] <= b[1] <= self._ufo[1] + self._ufo_spr.get_height():
                    self._spawn_explosion(self._ufo[0] + self._ufo_spr.get_width() / 2,
                                          self._ufo[1] + self._ufo_spr.get_height() / 2, _C_UFO)
                    self._ufo = None
                    self._score += 100
                else:
                    alive_bullets.append(b)
        self._bullets = alive_bullets

    def _update_bombs(self):
        spd = self.s(3)
        kept = []
        for bm in self._bombs:
            bm[1] += spd
            if bm[1] >= self.h - self.s(20):
                self._spawn_explosion(bm[0], self.h - self.s(22), _C_BOMB)
                continue
            kept.append(bm)
        self._bombs = kept

    def _update_ufo(self):
        if self._ufo is None:
            self._ufo_cd -= 1
            if self._ufo_cd <= 0:
                if _random.random() < 0.5:
                    self._ufo = [-self._ufo_spr.get_width(), self.s(12), 1]
                else:
                    self._ufo = [self.w, self.s(12), -1]
                self._ufo_cd = _random.randint(220, 520)
            return
        self._ufo[0] += self._ufo[2] * self.s(2)
        if self._ufo[0] < -self._ufo_spr.get_width() - self.s(4) or self._ufo[0] > self.w + self.s(4):
            self._ufo = None

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
        if self._dive_cd <= 0 and len(self._divers) < 5:
            self._dive_cd = _random.randint(18, 55)
            self._launch_diver()
        kept = []
        for d in self._divers:
            d["t"] += d["dt"]
            if d["t"] < 1.0:
                kept.append(d)
                # diving Floyds strafe the ship with bombs as they swoop
                if _random.random() < 0.03:
                    dx, dy = self._diver_pos(d)
                    self._bombs.append([dx + self._alien_w / 2,
                                        dy + self._alien_h])
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
        # alien Floyds
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if not self._aliens[r][c]:
                    continue
                spr = self._floyd[_FLOYD_NAMES[self._kind[r][c]]]
                x, y = self._alien_pos(r, c)
                surface.blit(spr, (int(x), int(y)))
        # diving Floyds (swooping down their Bézier paths)
        cue_blink = (self._t // 8) % 2 == 0
        for d in self._divers:
            spr = self._floyd[_FLOYD_NAMES[d["kind"]]]
            x, y = self._diver_pos(d)
            surface.blit(spr, (int(x), int(y)))
            # small blinking "▲ rejoining" cue while a diver loops back up
            if d["mode"] == "return" and d["t"] > 0.5 and cue_blink:
                cf = _font(self.s(9), bold=True)
                cue = "▲ rejoining"
                cw = cf.size(cue)[0]
                _draw_text(surface, cue,
                           int(x + self._alien_w / 2 - cw / 2),
                           int(y - self.s(12)), cf, (150, 255, 190))
        # bombs
        for bm in self._bombs:
            pg.draw.line(surface, _C_BOMB, (int(bm[0]), int(bm[1])),
                         (int(bm[0]), int(bm[1] + self.s(6))), max(1, self.s(1)))
        # ship
        cw = self._cannon.get_width()
        ch = self._cannon.get_height()
        surface.blit(self._cannon,
                     (int(self._ship_x - cw / 2), int(self.h - ch - self.s(18))))
        # bullets
        for b in self._bullets:
            pg.draw.line(surface, _C_BULLET, (int(b[0]), int(b[1])),
                         (int(b[0]), int(b[1] + self.s(7))), max(1, self.s(1)))
        # ufo
        if self._ufo:
            surface.blit(self._ufo_spr, (int(self._ufo[0]), int(self._ufo[1])))
        # explosions
        for ex in self._explosions:
            fade = max(0.0, 1.0 - ex["age"] / 18.0)
            col = (int(ex["col"][0] * fade), int(ex["col"][1] * fade),
                   int(ex["col"][2] * fade))
            for p in ex["parts"]:
                surface.fill(col, pg.Rect(int(p[0]), int(p[1]),
                                          max(1, self.s(2)), max(1, self.s(2))))
        # score / wave HUD (skipped on the transparent gallery overlay)
        if not transparent:
            self._render_hud(surface)

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


# ── persistent arcade high score ────────────────────────────────────────────
# Shared across every AlienFloydBackground instance (global background, the
# dedicated tab, item-viewer overlays, the Unite! pygame view).  The in-memory
# value updates instantly for the HUD; persistence to hdfg.cfg is delegated to
# a save callback wired by the main window and throttled to avoid disk churn.
_ALIEN_HISCORE = 0
_ALIEN_HISCORE_SAVE_CB = None
_ALIEN_HISCORE_LAST_SAVE = 0.0
_ALIEN_HISCORE_DIRTY = False


def init_alien_hiscore(value):
    """Seed the high score from persisted configuration at startup."""
    global _ALIEN_HISCORE
    try:
        _ALIEN_HISCORE = max(0, int(value))
    except (TypeError, ValueError):
        _ALIEN_HISCORE = 0


def get_alien_hiscore():
    return _ALIEN_HISCORE


def set_alien_hiscore_save_cb(cb):
    """Register ``cb(int)`` used to persist the high score to configuration."""
    global _ALIEN_HISCORE_SAVE_CB
    _ALIEN_HISCORE_SAVE_CB = cb


def note_alien_score(score):
    """Record a live score; bumps the high score and persists it (throttled to
    at most once every few seconds) when a new best is reached."""
    global _ALIEN_HISCORE, _ALIEN_HISCORE_LAST_SAVE, _ALIEN_HISCORE_DIRTY
    if score <= _ALIEN_HISCORE:
        return
    _ALIEN_HISCORE = score
    _ALIEN_HISCORE_DIRTY = True
    cb = _ALIEN_HISCORE_SAVE_CB
    if cb is not None:
        now = _time.monotonic()
        if now - _ALIEN_HISCORE_LAST_SAVE > 5.0:
            _ALIEN_HISCORE_LAST_SAVE = now
            _ALIEN_HISCORE_DIRTY = False
            try:
                cb(_ALIEN_HISCORE)
            except Exception:
                pass


def flush_alien_hiscore():
    """Force-persist a pending high score (e.g. when a widget is torn down)."""
    global _ALIEN_HISCORE_DIRTY
    cb = _ALIEN_HISCORE_SAVE_CB
    if cb is not None and _ALIEN_HISCORE_DIRTY:
        _ALIEN_HISCORE_DIRTY = False
        try:
            cb(_ALIEN_HISCORE)
        except Exception:
            pass


class AlienFloydWidget(QWidget):
    """A self-contained QWidget that renders the :class:`AlienFloydBackground`
    animation via pygame, blitting each frame with QPainter (no SDL window).

    Used both as the full-window "Alien Floyd's" tab (opaque) and as a
    transparent overlay above gallery item images (``transparent=True``), where
    only the stars / Floyds / defending ship are drawn over a see-through
    surface so the screenshot underneath stays visible."""

    def __init__(self, parent=None, transparent=False, fps=30):
        super().__init__(parent)
        _ensure_pg()
        self._transparent = bool(transparent)
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
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / max(1, fps)))
        self._timer.timeout.connect(self._tick)
        self._ensure_surface()

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
                self._bg = AlienFloydBackground((w, h), self._dpr)
            else:
                try:
                    self._bg.resize((w, h), self._dpr)
                except Exception:
                    pass

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
        flush_alien_hiscore()
        self._surface = None
        self._bg = None

    def _tick(self):
        if not self._alive or self._bg is None:
            return
        try:
            self._bg.update()
        except Exception:
            pass
        self.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.start()

    def hideEvent(self, ev):
        self.stop()
        super().hideEvent(ev)

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
