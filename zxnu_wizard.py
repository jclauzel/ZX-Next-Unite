"""zxnu_wizard.py — Wizzy, the animated onboarding wizard.

A pure-Qt (no pygame required) pixel-art assistant that lives in the
bottom-left corner of the main window and teaches newcomers the tabs:

* The sprite follows the Alien Floyd / Sir Clive convention — chunky
  string-encoded pixel maps + palette (see zxnu_pygame._make_palette_sprite)
  — but is rendered with QImage/QPainter so it works for every user,
  pygame installed or not. Gestures: idle bob + blink, wave, point (at the
  tabs), cast (the magic wand APPEARS, sparkles and disappears again) and
  talk (mouth flaps while a message is shown).
* All dialogue lives in zxnu_wizard_content.py in the seven UI languages
  and is resolved per message via zxnu_i18n.current_ui_language(), so the
  wizard follows the Settings language live.
* The per-tab deep-dive content is the GitHub wiki user manual: each tour
  step async-fetches its wiki page for a one-line "From the manual" teaser
  (cached; silently skipped offline) and "Read the manual" opens the page
  in the browser.
* First run: the wizard introduces itself and offers the tour or its own
  off switch (SETTING_WIZARD_ENABLED; also togglable in Settings). State
  persists in hdfg.cfg via the injected configuration dictionary.

Built through build_wizard(host, ...) — the same builder seam as the pane
modules — so the monolith only grows by the builder call.
"""
from __future__ import annotations

import logging
import os
import random
import re
import sys
import threading
import urllib.request
import webbrowser

from PySide6.QtCore import (Qt, QObject, QRect, QTimer, Signal)
from PySide6.QtGui import (QColor, QImage, QPainter, QPixmap)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLayout, QPushButton,
    QVBoxLayout, QWidget)

import zxnu_config
from zxnu_config import (SETTING_NEXTSYNC_EXPLORERPATH,
                         SETTING_WIZARD_ENABLED, SETTING_WIZARD_FONT_SIZE,
                         SETTING_WIZARD_INTRO_SHOWN, ZXART_USER_AGENT,
                         mame_windows_asset_arch)
from zxnu_i18n import current_ui_language
from zxnu_wizard_content import (DISCLAIMER_STEPS, GITHUB_URL, GUIDES,
                                 JOKES, KUDOS_NAMES, STORIES, TEXTS,
                                 TOUR_STEPS, USER_MANUAL_PAGE,
                                 WIKI_PAGE_BASE, WIKI_RAW_BASE,
                                 wizard_lines, wizard_tr)


def _is_linux():
    """Platform gate for the Linux-only guide notes (patchable in tests)."""
    return sys.platform.startswith("linux")

# ── Sprite artwork ────────────────────────────────────────────────────────
# 26×28 cells, '.' transparent. The suit is deep blue with the Spectrum
# rainbow flash across the chest; hat and trim are bright magenta/yellow —
# proper 8-bit BRIGHT 1 colours.
_PAL = {
    "h": (255, 60, 255),    # hat
    "H": (170, 0, 170),     # hat brim / shadow
    "y": (255, 255, 60),    # stars + trim
    "s": (255, 208, 160),   # skin
    "e": (20, 20, 30),      # eyes / mouth
    "o": (200, 90, 80),     # open mouth
    "b": (245, 245, 245),   # beard
    "n": (50, 70, 220),     # robe (deep blue)
    "N": (30, 45, 160),     # robe shadow
    "R": (255, 50, 50),     # rainbow flash
    "Y": (255, 220, 40),
    "G": (60, 230, 70),
    "C": (60, 220, 230),
    "k": (55, 55, 75),      # boots
    "w": (150, 100, 50),    # wand
    "*": (255, 255, 120),   # sparkle bright
    "+": (255, 160, 255),   # sparkle alt
}

_BASE = [
    ".............y............",
    "............hh............",
    "...........hhhh...........",
    "...........hhhh...........",
    "..........hhhhhh..........",
    ".........hhhhhhh..........",
    ".........hhhhhhhh.........",
    "........hhhhhhhhh.........",
    ".......hhhhhhhhhhh........",
    ".....HHHHHHHHHHHHHH.......",
    "........ssssssss..........",
    ".......sseessees..........",
    ".......ssssssssss.........",
    "......sbbsseessbbs........",
    "......bbbbssssbbbb........",
    ".......bbbbbbbbbb.........",
    "......nnbbbbbbbbnn........",
    ".....nnnnbbbbbbnnnn.......",
    "....nnnnnnbbbbnnnnnn......",
    "....nnnnnnnbbnnnnnnn......",
    "....nnnRnnYnnGnnCnnn......",
    "....nnnnCnnRnnYnnnnn......",
    "....nnnGnnCnnRnnGnnn......",
    "....nnnnYnnGnnCnnnnn......",
    "....NnnnnnnnnnnnnnnN......",
    "....NnnnnnnnnnnnnnnN......",
    ".....kkkk......kkkk.......",
    ".....kkkk......kkkk.......",
]


def _patched(base, patches):
    """Return a copy of *base* with (col, row, art_rows) patches stamped on
    top — the Clive-walk stamp. '.' cells in a patch are transparent
    (leave the base pixel); ' ' cells ERASE the base pixel."""
    rows = [list(r) for r in base]
    for (x, y, art) in patches:
        for dy, line in enumerate(art):
            ry = y + dy
            if not (0 <= ry < len(rows)):
                continue
            for dx, ch in enumerate(line):
                rx = x + dx
                if not (0 <= rx < len(rows[ry])):
                    continue
                if ch == " ":
                    rows[ry][rx] = "."
                elif ch != ".":
                    rows[ry][rx] = ch
    return ["".join(r) for r in rows]


# Gesture patches. The right side of the sprite faces the tabs.
_EYES_SHUT = [(7, 11, ["sssssssss"])]   # blink: skin over the eye row
_MOUTH_OPEN = [(11, 13, ["oo"])]
# Arm raised high, hand open (wave frame 1) / mid (frame 2).
_ARM_UP = [(18, 12, ["...ss",
                     "..nns",
                     ".nnn.",
                     "nnn.."]),
           (16, 16, ["nn...",
                     ".nn.."])]
_ARM_MID = [(18, 14, ["....s",
                      "..nns",
                      "nnnn."]),
            (16, 17, ["nn..."])]
# Arm extended right, pointing at the tab strip.
_ARM_POINT = [(18, 16, ["......",
                        "nnnnss",
                        "nnnnss"])]
# Wand arm: appears only in the cast frames — sleeve out, wooden wand up,
# sparkle at the tip growing/bursting frame by frame.
_ARM_WAND = [(18, 15, ["....w.",
                       "....w.",
                       "nnnsw.",
                       "nnnss."])]
_SPARK_1 = [(22, 13, ["*"])]
_SPARK_2 = [(21, 12, [".*.",
                      "***",
                      ".*."])]
_SPARK_3 = [(20, 11, ["+.*.+",
                      ".***.",
                      "*****",
                      ".***.",
                      "+.*.+"])]

# Head turn: both pupils shift together (base eyes sit at cols 9-10/13-14
# of row 11); used as an idle "glance around" and while strolling.
_LOOK_LEFT = [(7, 11, ["seesseess"])]
_LOOK_RIGHT = [(7, 11, ["ssseessee"])]
# Profile view (facing right): one eye, a little nose, and the stiff
# bent-elbow arms of a proper walk-like-an-Egyptian promenade.
_PROFILE_HEAD = [(7, 11, ["ssssssees"]), (17, 12, ["ss"])]
_ARMS_EGYPTIAN = [(21, 16, ["s"]), (18, 17, ["nnns"]),     # front arm up
                  (2, 21, ["s"]), (2, 20, ["snnn"])]       # back arm down
_ERASE_BOOTS = [(5, 26, ["    "]), (15, 26, ["    "]),
                (5, 27, ["    "]), (15, 27, ["    "])]
_LEGS_STEP_1 = _ERASE_BOOTS + [(16, 26, ["kkkk"]), (16, 27, ["kkkk"]),
                               (4, 26, ["kkkk"])]           # back heel up
_LEGS_STEP_2 = _ERASE_BOOTS + [(14, 26, ["kkkk"]), (14, 27, ["kkkk"]),
                               (6, 26, ["kkkk"])]
_WALK_BASE = _PROFILE_HEAD + _ARMS_EGYPTIAN

_FRAME_SPECS = {
    "idle":  [[], _EYES_SHUT],                       # [0]=open, [1]=blink
    "look":  [_LOOK_LEFT, [], _LOOK_RIGHT, []],
    "wave":  [_ARM_UP, _ARM_MID],
    "point": [_ARM_POINT, _ARM_POINT + _MOUTH_OPEN],
    "cast":  [_ARM_WAND + _SPARK_1, _ARM_WAND + _SPARK_2,
              _ARM_WAND + _SPARK_3, _ARM_WAND + _SPARK_2],
    "talk":  [[], _MOUTH_OPEN],
    "walk":  [_WALK_BASE + _LEGS_STEP_1, _WALK_BASE + _LEGS_STEP_2],
}


def build_wizard_frames(px=3):
    """name -> [QPixmap] for every gesture (chunky *px* pixels per cell)."""
    frames = {}
    for name, specs in _FRAME_SPECS.items():
        pix_list = []
        for patches in specs:
            rows = _patched(_BASE, patches)
            h, w = len(rows), len(rows[0])
            img = QImage(w * px, h * px, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            painter = QPainter(img)
            for r, line in enumerate(rows):
                for c, ch in enumerate(line):
                    col = _PAL.get(ch)
                    if col is not None:
                        painter.fillRect(c * px, r * px, px, px,
                                         QColor(*col))
            painter.end()
            pix_list.append(QPixmap.fromImage(img))
        frames[name] = pix_list
    # The promenade needs a left-facing walk too: mirror the profile frames.
    frames["walk_left"] = [
        QPixmap.fromImage(pix.toImage().mirrored(True, False))
        for pix in frames["walk"]]
    return frames


class WizardSprite(QWidget):
    """The animated wizard sprite (clickable)."""

    clicked = Signal()

    _GESTURE_MS = {"idle": 200, "look": 420, "wave": 260, "point": 320,
                   "cast": 180, "talk": 190, "walk": 150, "walk_left": 150}

    def __init__(self, parent=None, px=3):
        super().__init__(parent)
        self._frames = build_wizard_frames(px)
        first = self._frames["idle"][0]
        # A little headroom so the idle bob never clips the hat star.
        self.setFixedSize(first.width(), first.height() + 6)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Wizzy — click me!")
        self._gesture = "idle"
        self._frame = 0
        self._cycles_left = -1          # -1 = loop forever
        self._tick_count = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._GESTURE_MS["idle"])

    def set_gesture(self, name, cycles=-1):
        """Play *name*; after *cycles* full loops fall back to idle."""
        if name not in self._frames:
            name = "idle"
        self._gesture = name
        self._frame = 0
        self._cycles_left = cycles
        self._timer.setInterval(self._GESTURE_MS.get(name, 200))
        self.update()

    def _tick(self):
        self._tick_count += 1
        frames = self._frames[self._gesture]
        nxt = self._frame + 1
        if self._gesture == "idle":
            # Mostly eyes-open; blink for one tick every couple seconds.
            self._frame = 1 if (self._tick_count % 14 == 0) else 0
        else:
            if nxt >= len(frames):
                nxt = 0
                if self._cycles_left > 0:
                    self._cycles_left -= 1
                    if self._cycles_left == 0:
                        self.set_gesture("idle")
                        return
            self._frame = nxt
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        frames = self._frames[self._gesture]
        pix = frames[min(self._frame, len(frames) - 1)]
        # Gentle two-pixel idle bob so he always feels alive.
        bob = 2 if (self._gesture == "idle"
                    and (self._tick_count // 4) % 2) else 0
        painter.drawPixmap(0, 6 - bob, pix)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class WizardBubble(QWidget):
    """The wizard's retro speech bubble (text + a row of action buttons)."""

    MAX_TEXT_W = 360

    # Bubble font size in px: adjustable via the wizard's own "Font size"
    # dialog (persisted in SETTING_WIZARD_FONT_SIZE), clamped to sane bounds.
    DEFAULT_FONT_PX = 12
    MIN_FONT_PX = 9
    MAX_FONT_PX = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._font_px = self.DEFAULT_FONT_PX
        self._apply_style()
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        # The layout owns the bubble's size: it can never shrink below its
        # content (deferred button deletions used to trigger a relayout
        # that squashed the bubble under its own text).
        v.setSizeConstraint(QLayout.SetMinimumSize)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        # Fixed width: a wrapped QLabel's sizeHint under a plain adjustSize
        # miscomputes its height (the classic word-wrap trap — text got
        # visibly clipped); pinning the width makes heightForWidth exact.
        self.label.setFixedWidth(self.MAX_TEXT_W)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.label)
        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(6)
        v.addLayout(self.button_row)
        # A second, smaller row for ever-present reference links (the
        # in-depth guides keep Manual/GitHub reachable on every screen).
        self.links_row = QHBoxLayout()
        self.links_row.setSpacing(6)
        v.addLayout(self.links_row)
        self._buttons = []
        self._link_buttons = []
        self._pages = [""]
        self._page = 0
        self._actions = []
        self._links = []
        # Set by the manager: called after every re-render so the bubble is
        # re-anchored (page flips change its height; growing from a fixed
        # top-left corner used to push it past the window's bottom edge).
        self.on_resize = None

    def _apply_style(self):
        px = self._font_px
        self.setStyleSheet(
            "WizardBubble { background-color: rgba(20, 20, 46, 235);"
            " border: 2px solid #ff3cff; border-radius: 10px; }"
            f"QLabel {{ color: #ffffff; background: transparent;"
            f" font-size: {px}px; }}"
            "QPushButton { background-color: #32327a; color: #ffff3c;"
            " border: 1px solid #ffff3c; border-radius: 4px;"
            " padding: 3px 10px; }"
            "QPushButton:hover { background-color: #4646aa; }")

    def apply_font_px(self, px):
        """Set the bubble font (clamped) and re-render the current page so
        the geometry is re-measured with the new metrics."""
        self._font_px = max(self.MIN_FONT_PX, min(self.MAX_FONT_PX, int(px)))
        self._apply_style()
        if self.isVisible():
            self._render()

    # Long speeches are split into pages the reader flips with ◀ / ▶ —
    # nothing ever ends in an unreadable "…" again.
    PAGE_CHARS = 250

    @staticmethod
    def _paginate(text, limit=PAGE_CHARS):
        pages = []
        for block in text.split("\n\n"):
            block = block.strip()
            while len(block) > limit:
                cut = block.rfind(" ", 0, limit)
                if cut <= 0:
                    cut = limit
                pages.append(block[:cut].rstrip())
                block = block[cut:].strip()
            if block:
                pages.append(block)
        return pages or [""]

    def show_message(self, text, buttons, links=None):
        """Paginate *text* and rebuild the button row from (label, cb);
        *links* fills the smaller always-visible reference row."""
        self._pages = self._paginate(text)
        self._page = 0
        self._actions = list(buttons)
        self._links = list(links or [])
        self._render()

    def append_text(self, extra):
        """Add late-arriving text (the wiki teaser) as extra pages."""
        self._pages += self._paginate(extra)
        self._render()

    def _flip(self, delta):
        self._page = max(0, min(len(self._pages) - 1, self._page + delta))
        self._render()

    def _render(self):
        # Empty the whole button row (widgets AND the stretch item — the
        # stretch used to accumulate once per render).
        while self.button_row.count():
            item = self.button_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)   # off-screen NOW (deleteLater ghosts)
                w.deleteLater()
        self._buttons = []
        many = len(self._pages) > 1
        last = self._page >= len(self._pages) - 1
        text = self._pages[self._page] + ("  …" if not last else "")
        self.label.setText(text)
        # Word-wrapped QLabels under-report their wrapped height to layouts
        # (the text was visibly clipped); measure it ourselves — with the
        # POLISHED (stylesheet) font — and pin both dimensions so the
        # layout maths below is exact.
        self.label.ensurePolished()
        fm = self.label.fontMetrics()
        rect = fm.boundingRect(QRect(0, 0, self.MAX_TEXT_W, 2000),
                               Qt.TextWordWrap, text)
        self.label.setFixedSize(self.MAX_TEXT_W,
                                rect.height() + fm.lineSpacing())
        nav = []
        if many:
            nav = [("◀", lambda: self._flip(-1)),
                   (f"{self._page + 1}/{len(self._pages)}", None),
                   ("▶", lambda: self._flip(+1))]
        for (label, cb) in nav + self._actions:
            btn = QPushButton(label)
            if cb is None:
                btn.setEnabled(False)              # the page counter
            elif label == "◀":
                btn.clicked.connect(cb)
                btn.setEnabled(self._page > 0)
            elif label == "▶":
                btn.clicked.connect(cb)
                btn.setEnabled(not last)
            else:
                btn.clicked.connect(cb)
            if label in ("◀", "▶"):
                btn.setFixedWidth(30)
            elif cb is None:
                btn.setFixedWidth(48)
            self.button_row.addWidget(btn)
            self._buttons.append(btn)
        self.button_row.addStretch(1)
        # Reference links row (smaller, persistent across pages).
        while self.links_row.count():
            item = self.links_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._link_buttons = []
        for (label, cb) in self._links:
            btn = QPushButton(label)
            btn.setStyleSheet(
                f"font-size: {max(8, self._font_px - 2)}px;"
                " padding: 1px 8px;")
            btn.clicked.connect(cb)
            self.links_row.addWidget(btn)
            self._link_buttons.append(btn)
        if self._links:
            self.links_row.addStretch(1)
        self.layout().activate()
        self.resize(self.layout().sizeHint())
        self.show()
        self.raise_()

    def resizeEvent(self, event):
        # The SetMinimumSize layout constraint can grow the bubble a beat
        # after _render (posted LayoutRequest); re-anchoring on the REAL
        # resize keeps the bubble inside the window whatever the timing.
        super().resizeEvent(event)
        if self.on_resize is not None:
            self.on_resize()


class _WikiFetchSignals(QObject):
    done = Signal(str, str)     # page, teaser ("" when unavailable)


def _teaser_from_markdown(md, limit=220):
    """First real paragraph of a wiki page, markdown stripped."""
    para = []
    for line in md.splitlines():
        stripped = line.strip()
        if not stripped:
            if para:
                break
            continue
        if stripped.startswith(("#", "|", "!", ">", "---", "***", "```")):
            if para:
                break
            continue
        para.append(stripped)
    text = " ".join(para)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # [text](url)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


class WizardManager(QObject):
    """Owns the sprite + bubble, the tour, jokes/stories and persistence."""

    _health_ip_ready = Signal(str)     # "" when undetectable

    def __init__(self, host, *, configuration_dictionary):
        super().__init__(host)
        self._host = host
        self._cfg = configuration_dictionary
        self.sprite = WizardSprite(host)
        self.bubble = WizardBubble(host)
        self.sprite.hide()
        self.bubble.hide()
        self.sprite.clicked.connect(self.show_menu)
        self.bubble.on_resize = self._reposition
        self._tour_index = -1
        self._tour_active_page = None
        # How to re-compose whatever the bubble currently shows — called on
        # a live language switch so the wizard changes language mid-speech.
        self._respeak = None
        # True while the bubble shows a tab OFFER (guide.offer/help.offer):
        # offers may be retargeted on a tab switch, real content never is.
        self._offer_shown = False
        self._teaser_cache = {}
        self._fetch_signals = _WikiFetchSignals()
        self._fetch_signals.done.connect(self._on_teaser)
        self._joke_bag = []
        self._story_bag = []
        # Idle liveliness: every few seconds Wizzy glances around, waves or
        # takes a little walk-like-an-Egyptian stroll along the bottom edge.
        self._stroll_target = None
        self._stroll_timer = QTimer(self)
        self._stroll_timer.setInterval(30)
        self._stroll_timer.timeout.connect(self._stroll_tick)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(9000)
        self._idle_timer.timeout.connect(self._idle_act)
        self._idle_timer.start()
        # Health check: the local-IP probe result, filled by a daemon
        # thread (the probe can stall on broken DNS — never on UI).
        self._health_ip = None
        self._health_ip_pending = False
        self._health_ip_ready.connect(self._on_health_ip)
        # Quick Start: poll for the async steps (download/install) landing.
        self._qs_pred = None
        self._qs_then = None
        self._qs_timer = QTimer(self)
        self._qs_timer.setInterval(1000)
        self._qs_timer.timeout.connect(self._qs_tick)
        # In-depth guide offers: fire once per guided tab per session.
        self._offered_tabs = set()
        tabw = getattr(host, "_tab_widget", None)
        if tabw is not None:
            tabw.currentChanged.connect(self._on_tab_switched)
        host.installEventFilter(self)

    # ── plumbing ─────────────────────────────────────────────────────────
    def _lang(self):
        return current_ui_language() or "en"

    def _tr(self, key):
        return wizard_tr(key, self._lang())

    def _save_cfg(self):
        saver = getattr(self._host, "_save_configuration_file", None) or \
            getattr(self._host, "save_configuration_file", None)
        if saver is not None:
            try:
                saver()
            except Exception:
                logging.exception("wizard: could not save configuration")

    def enabled(self):
        return str(self._cfg.get(SETTING_WIZARD_ENABLED, "")).lower() != "false"

    def _font_px(self):
        try:
            v = int(str(self._cfg.get(SETTING_WIZARD_FONT_SIZE, "")).strip()
                    or 0)
        except ValueError:
            v = 0
        if WizardBubble.MIN_FONT_PX <= v <= WizardBubble.MAX_FONT_PX:
            return v
        return WizardBubble.DEFAULT_FONT_PX

    def adjust_font(self, delta=0):
        """The wizard's own font dialog: A− / A+ resize his letters live
        and persist the choice."""
        px = self.bubble._font_px + delta
        px = max(WizardBubble.MIN_FONT_PX,
                 min(WizardBubble.MAX_FONT_PX, px))
        if delta:
            self._cfg[SETTING_WIZARD_FONT_SIZE] = str(px)
            self._save_cfg()
            self.bubble.apply_font_px(px)
        self._respeak = lambda: self.adjust_font(0)
        self._say(f"{self._tr('wizard.font')} ({px}px)",
                  [("A−", lambda: self.adjust_font(-1)),
                   ("A+", lambda: self.adjust_font(+1)),
                   (self._tr("btn.close"), self._dismiss)],
                  gesture="talk", cycles=6)

    def set_enabled(self, on, persist=True):
        self._cfg[SETTING_WIZARD_ENABLED] = "" if on else "false"
        if persist:
            self._save_cfg()
        if on:
            self.sprite.show()
            self.sprite.raise_()
            self.sprite.set_gesture("wave", cycles=3)
            self._reposition()
        else:
            self.bubble.hide()
            self.sprite.hide()
        # Keep the Settings checkbox in sync when it exists.
        cb = getattr(self._host, "settings_wizard_checkbox", None)
        if cb is not None and cb.isChecked() != bool(on):
            cb.blockSignals(True)
            cb.setChecked(bool(on))
            cb.blockSignals(False)

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() in (event.Type.Resize,
                                                  event.Type.Move,
                                                  event.Type.Show):
            self._reposition()
        return False

    def _reposition(self):
        host = self._host
        margin = 10
        sy = host.height() - self.sprite.height() - margin
        self.sprite.move(margin, max(0, sy))
        bx = margin + self.sprite.width() + 6
        by = host.height() - self.bubble.height() - margin
        self.bubble.move(bx, max(0, by))
        self.sprite.raise_()
        self.bubble.raise_()

    # ── idle liveliness ──────────────────────────────────────────────────
    def _idle_act(self):
        if (not self.enabled() or not self.sprite.isVisible()
                or self.bubble.isVisible() or self._stroll_timer.isActive()):
            return
        roll = random.random()
        if roll < 0.40:
            self.sprite.set_gesture("look", cycles=2)      # glance around
        elif roll < 0.55:
            self.sprite.set_gesture("wave", cycles=2)
        elif roll < 0.70:
            self.sprite.set_gesture("cast", cycles=2)      # wand flourish
        else:
            self._start_stroll()

    def _start_stroll(self):
        """Stroll to a random spot along the bottom edge, Egyptian style."""
        span = max(80, int(self._host.width() * 0.35))
        cur = self.sprite.x()
        target = 10 + random.randint(0, span)
        if abs(target - cur) < 40:
            target = 10 if cur > 10 + span // 2 else 10 + span
        self._stroll_target = target
        self.sprite.set_gesture("walk" if target > cur else "walk_left")
        self._stroll_timer.start()

    def _stroll_tick(self):
        target = self._stroll_target
        x = self.sprite.x()
        if target is None or abs(target - x) <= 2:
            self._stop_stroll()
            return
        self.sprite.move(x + (2 if target > x else -2), self.sprite.y())

    def _stop_stroll(self):
        self._stroll_timer.stop()
        self._stroll_target = None
        if self.sprite._gesture in ("walk", "walk_left"):
            self.sprite.set_gesture("idle")

    def _say(self, text, buttons, gesture="talk", cycles=8, links=None):
        self._offer_shown = False      # offer_guide/offer_help re-set it
        self._stop_stroll()
        self.sprite.show()
        self.sprite.set_gesture(gesture, cycles=cycles)
        self.bubble.show_message(text, buttons, links=links)
        self._reposition()

    def _dismiss(self):
        self.bubble.hide()
        self._tour_index = -1
        self._tour_active_page = None
        self._respeak = None
        self._offer_shown = False
        self._qs_pred = self._qs_then = None
        self._qs_timer.stop()
        self.sprite.set_gesture("idle")

    def _on_network_changed(self, online):
        if online and self._tour_active_page and self.bubble.isVisible():
            self._request_teaser(self._tour_active_page)

    def on_language_changed(self):
        """Live language switch (Settings combo): re-compose the current
        speech in the new language while it is on screen."""
        if self.bubble.isVisible() and self._respeak is not None:
            self._respeak()

    # ── startup / first run ──────────────────────────────────────────────
    def startup(self):
        """Deferred post-load entry point (config already restored)."""
        cb = getattr(self._host, "settings_wizard_checkbox", None)
        if cb is not None:
            cb.blockSignals(True)
            cb.setChecked(self.enabled())
            cb.blockSignals(False)
        self.bubble.apply_font_px(self._font_px())
        # When the network comes back mid-speech, fetch the teaser the
        # open bubble skipped while offline (the watcher is built right
        # after the wizard, so it exists by the time startup() runs).
        net = getattr(self._host, "_network", None)
        if net is not None:
            net.online_changed.connect(self._on_network_changed)
        if not self.enabled():
            return
        self.sprite.show()
        self._reposition()
        intro_shown = str(
            self._cfg.get(SETTING_WIZARD_INTRO_SHOWN, "")).lower() == "true"
        if not intro_shown:
            self._cfg[SETTING_WIZARD_INTRO_SHOWN] = "true"
            self._save_cfg()
            self.intro()
        else:
            self.sprite.set_gesture("wave", cycles=2)
            # The saved default tab may itself be a guided one: offer its
            # in-depth guide right away (returning users only — the intro
            # takes precedence on a first run).
            self._maybe_offer_current_tab()

    def intro(self):
        self._respeak = self.intro
        self._say(self._tr("intro.hello") + "\n\n" + self._tr("intro.offer"),
                  [(self._tr("btn.quickstart"), self.start_quickstart),
                   (self._tr("btn.tour"), self.start_tour),
                   (self._tr("btn.later"), self._dismiss),
                   (self._tr("btn.off"), self.turn_off)],
                  gesture="wave", cycles=4)

    def turn_off(self):
        self._respeak = None
        self._say(self._tr("wizard.off"), [], gesture="cast", cycles=2)
        QTimer.singleShot(2600, lambda: self.set_enabled(False))

    # ── the tour ─────────────────────────────────────────────────────────
    def _resolve_step(self, step):
        """(tab_index, text_key, wiki_page) or None when the tab is hidden."""
        const_name, text_key, page = step
        prefix = getattr(zxnu_config, const_name, const_name)
        tabw = getattr(self._host, "_tab_widget", None)
        if tabw is None:
            return None
        for i in range(tabw.count()):
            if tabw.tabText(i).startswith(prefix):
                return (i, text_key, page)
        return None

    def start_tour(self):
        self._tour_index = -1
        self.next_tour_step()

    def next_tour_step(self):
        while True:
            self._tour_index += 1
            if self._tour_index >= len(TOUR_STEPS):
                self.finish_tour()
                return
            if self._resolve_step(TOUR_STEPS[self._tour_index]) is not None:
                break
        self._show_tour_step()

    def _show_tour_step(self):
        """Display (or, on a language switch, re-display) the current step."""
        resolved = self._resolve_step(TOUR_STEPS[self._tour_index])
        if resolved is None:
            return
        tab_index, text_key, page = resolved
        try:
            self._host._tab_widget.setCurrentIndex(tab_index)
        except Exception:
            pass
        self._tour_active_page = page
        text = self._tr(text_key)
        if text_key in DISCLAIMER_STEPS:
            # Browsing third-party catalogues: softly recall that the app
            # distributes nothing itself and rights are the user's to check.
            text += "\n\n" + self._tr("tour.disclaimer")
        self._respeak = self._show_tour_step
        self._say(text,
                  [(self._tr("btn.next"), self.next_tour_step),
                   (self._tr("btn.more"), lambda _=False, p=page:
                       self.open_manual(p)),
                   (self._tr("btn.stop"), self._dismiss)],
                  gesture="point", cycles=6)
        self._request_teaser(page)

    def finish_tour(self):
        self._tour_active_page = None
        self._respeak = self.finish_tour
        # The finale: kudos to the people who make the Next awesome, then
        # the goodbye — the bubble paginates the long thank-you list.
        self._say(self._tr("tour.kudos").format(names=KUDOS_NAMES)
                  + "\n\n" + self._tr("tour.done"),
                  [(self._tr("btn.close"), self._dismiss)],
                  gesture="cast", cycles=6)

    # ── Quick Start: three clicks to a booting Next ──────────────────────
    def _qs_image_loaded(self):
        return bool(getattr(self._host, "right_disk_image_path", None))

    def _qs_emulator_ready(self):
        return bool(getattr(self._host, "_cspect_executable_path", None)
                    or getattr(self._host, "_mame_executable_path", None))

    def start_quickstart(self):
        """Resume wherever the setup actually stands."""
        if not self._qs_image_loaded():
            self._qs_step_image()
        elif not self._qs_emulator_ready():
            self._qs_step_emulator()
        else:
            self._qs_step_launch()

    def _qs_step_image(self):
        self._respeak = self._qs_step_image
        self._say(self._tr("qs.image"),
                  [(self._tr("btn.doit"), self._qs_do_image),
                   (self._tr("btn.skipstep"), self._qs_after_image),
                   (self._tr("btn.later"), self._dismiss)],
                  gesture="point", cycles=6)

    def _qs_do_image(self):
        fn = getattr(self._host, "download_nextzxos_image", None)
        if fn is None:
            return
        self._goto_tab(getattr(zxnu_config, "ZX_NEXT_UNITE_TAB_TITLE_GOOEY",
                               "TOOL: SD Card Utility"))
        try:
            fn()
        except Exception:
            logging.exception("Quick Start: NextZXOS download failed")
            return
        self._qs_wait(self._qs_image_loaded, "qs.image.wait",
                      self._qs_after_image)

    def _qs_after_image(self):
        if self._qs_emulator_ready():
            self._qs_step_launch()
        else:
            self._qs_step_emulator()

    def _qs_step_emulator(self):
        buttons = []
        if (mame_windows_asset_arch() is not None
                and getattr(self._host, "install_mame", None) is not None):
            buttons.append((self._tr("btn.doit"), self._qs_do_mame))
        buttons.append((self._tr("btn.takeme"),
                        lambda: self._goto_tab(getattr(
                            zxnu_config, "ZX_NEXT_UNITE_TAB_TITLE_ITCHIO",
                            "itch.io"))))
        buttons.append((self._tr("btn.skipstep"), self._qs_step_launch))
        self._respeak = self._qs_step_emulator
        self._say(self._tr("qs.emulator"), buttons,
                  gesture="point", cycles=6)

    def _qs_do_mame(self):
        host = self._host
        try:
            found = getattr(host, "_hdfmonkey_binary_found", None)
            if found is not None and not found():
                host._on_hdfmonkey_button_clicked()
        except Exception:
            logging.exception("Quick Start: hdfmonkey install failed")
        try:
            host.install_mame()
        except Exception:
            logging.exception("Quick Start: MAME install failed")
            return
        self._qs_wait(self._qs_emulator_ready, "qs.emulator.wait",
                      self._qs_step_launch)

    def _qs_step_launch(self):
        self._respeak = self._qs_step_launch
        self._say(self._tr("qs.launch"),
                  [(self._tr("btn.doit"), self._qs_do_launch),
                   (self._tr("btn.later"), self._dismiss)],
                  gesture="cast", cycles=4)

    def _qs_do_launch(self):
        host = self._host
        fn = None
        if getattr(host, "_cspect_executable_path", None):
            fn = getattr(host, "_launch_cspect_fn", None)
        fn = fn or getattr(host, "_launch_mame_fn", None)
        try:
            if fn is not None:
                fn()
        except Exception:
            logging.exception("Quick Start: emulator launch failed")
        self._respeak = None
        self._say(self._tr("qs.done"),
                  [(self._tr("btn.close"), self._dismiss)],
                  gesture="cast", cycles=8)

    def _qs_wait(self, predicate, wait_key, then):
        """Show a waiting bubble and poll until *predicate* holds."""
        self._qs_pred, self._qs_then = predicate, then
        self._respeak = lambda: self.bubble.show_message(
            self._tr(wait_key), [(self._tr("btn.later"), self._dismiss)])
        self._say(self._tr(wait_key),
                  [(self._tr("btn.later"), self._dismiss)],
                  gesture="talk", cycles=30)
        self._qs_timer.start()

    def _qs_tick(self):
        if self._qs_pred is None:
            self._qs_timer.stop()
            return
        if self._qs_pred():
            self._qs_timer.stop()
            then, self._qs_pred, self._qs_then = self._qs_then, None, None
            if then is not None:
                then()

    # ── health check ─────────────────────────────────────────────────────
    def _health_checks(self):
        """Live (text_key, ok, detail) rows from state the app already
        tracks — nothing here probes the network or spawns processes."""
        host = self._host
        rows = []
        net = getattr(host, "_network_online", None)
        rows.append(("health.network",
                     bool(net()) if callable(net) else True, ""))
        found = getattr(host, "_hdfmonkey_binary_found", None)
        rows.append(("health.hdfmonkey",
                     bool(found()) if callable(found) else False, ""))
        emus = [name for name, attr in (("CSpect", "_cspect_executable_path"),
                                        ("MAME", "_mame_executable_path"))
                if getattr(host, attr, None)]
        rows.append(("health.emulators", bool(emus), ", ".join(emus)))
        rows.append(("health.image", self._qs_image_loaded(), ""))
        root = str(self._cfg.get(SETTING_NEXTSYNC_EXPLORERPATH, "")).strip()
        rows.append(("health.syncroot",
                     bool(root) and os.path.isdir(root), ""))
        ip = self._health_ip
        rows.append(("health.localip", bool(ip), ip or ""))
        return rows

    def show_health(self):
        """Wizzy's stethoscope: one ✅/⚠️ line per readiness item. The
        health button doubles as the refresh. The local-IP probe can
        stall ~2 s on broken DNS, so it runs in a daemon thread and the
        line self-updates when the result lands (cached for instant
        display meanwhile)."""
        self._respeak = self.show_health
        lines = [self._tr("health.title")]
        for key, ok, detail in self._health_checks():
            line = ("✅  " if ok else "⚠️  ") + self._tr(key)
            if ok and detail:
                line += f": {detail}"
            lines.append(line)
        self._say("\n".join(lines),
                  [(self._tr("btn.health"), self.show_health),
                   (self._tr("btn.close"), self._dismiss)],
                  gesture="look", cycles=4)
        if not self._health_ip_pending:
            self._health_ip_pending = True

            def _probe(sig=self._health_ip_ready):
                try:
                    from zxnu_network import detect_local_ipv4
                    ip = detect_local_ipv4()[3]
                except Exception:
                    ip = None
                sig.emit(ip or "")

            threading.Thread(target=_probe, daemon=True).start()

    def _on_health_ip(self, ip):
        self._health_ip_pending = False
        changed = (ip or None) != self._health_ip
        self._health_ip = ip or None
        if (changed and self.bubble.isVisible()
                and self._respeak == self.show_health):
            self.show_health()

    # ── in-depth guides ──────────────────────────────────────────────────
    def _guide_links(self, page):
        """The always-visible reference row: user manual + GitHub repo."""
        return [(self._tr("btn.more"),
                 lambda _=False, p=page: self.open_manual(p)),
                ("GitHub", lambda: self._open_url(GITHUB_URL))]

    def _open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception:
            logging.exception("wizard: could not open %s", url)

    def offer_guide(self, guide_id):
        """First visit of a guided tab: discovery tour or in-depth info?
        The "Tell me more" button resolves the tab AT CLICK TIME
        (about_current_tab), so an offer left open across a tab switch
        can never open the previous tab's content."""
        guide = GUIDES[guide_id]
        self._respeak = lambda: self.offer_guide(guide_id)
        self._say(self._tr("guide.offer"),
                  [(self._tr("btn.indepth"), self.about_current_tab),
                   (self._tr("btn.tour"), self.start_tour),
                   (self._tr("btn.later"), self._dismiss)],
                  gesture="wave", cycles=3,
                  links=self._guide_links(guide["page"]))
        self._offer_shown = True

    def start_guide(self, guide_id):
        self._show_guide_node(guide_id, GUIDES[guide_id]["start"])

    def _show_guide_node(self, guide_id, node_id):
        guide = GUIDES[guide_id]
        node = guide["nodes"][node_id]
        text = self._tr(node_id)
        extra = node.get("linux_extra")
        if extra and _is_linux():
            text += "\n\n" + self._tr(extra)
        buttons = []
        goto = node.get("goto")
        if goto:
            title = getattr(zxnu_config, goto, goto)
            buttons.append((self._tr("btn.takeme"),
                            lambda _=False, t=title: self._goto_tab(t)))
        for btn_key, target in node["buttons"]:
            if target == "close":
                buttons.append((self._tr(btn_key), self._dismiss))
            else:
                buttons.append((self._tr(btn_key),
                                lambda _=False, g=guide_id, n=target:
                                    self._show_guide_node(g, n)))
        self._respeak = lambda: self._show_guide_node(guide_id, node_id)
        self._say(text, buttons,
                  gesture=node.get("gesture", "talk"), cycles=10,
                  links=self._guide_links(guide["page"]))

    def _goto_tab(self, title_prefix):
        """Switch the main tab widget to the tab whose title starts with
        *title_prefix* (the guide bubble stays where it is)."""
        tabw = getattr(self._host, "_tab_widget", None)
        if tabw is None:
            return
        for i in range(tabw.count()):
            if tabw.tabText(i).startswith(title_prefix):
                tabw.setCurrentIndex(i)
                return

    def _maybe_offer_current_tab(self):
        tabw = getattr(self._host, "_tab_widget", None)
        if tabw is not None:
            self._on_tab_switched(tabw.currentIndex())

    def _on_tab_switched(self, index):
        """First time a guided tab is visited this session (wizard idle):
        offer the in-depth guide for it. A visible OFFER left over from
        the previous tab is dismissed first (and the new tab's own offer
        may replace it); real content is never hijacked."""
        if (not self.enabled() or not self.sprite.isVisible()
                or 0 <= self._tour_index < len(TOUR_STEPS)):
            return
        if self.bubble.isVisible():
            if not self._offer_shown:
                return
            self._dismiss()             # stale offer for the previous tab
        tabw = getattr(self._host, "_tab_widget", None)
        if tabw is None or index < 0:
            return
        topic = self._tab_topic(tabw.tabText(index))
        if topic is None or topic[1] in self._offered_tabs:
            return
        kind, key, page = topic
        self._offered_tabs.add(key)
        if kind == "guide":
            self.offer_guide(key)
        else:
            self.offer_help(key, page)

    def _tab_topic(self, title):
        """What the wizard knows about the tab titled *title*:
        ("guide", guide_id, page) for the in-depth tabs, ("help",
        tour_key, page) for tour tabs (the Settings tab's help is
        tour.settings, not the tour's language opener), else None."""
        for guide_id, guide in GUIDES.items():
            prefix = getattr(zxnu_config, guide["tab"], guide["tab"])
            if title.startswith(prefix):
                return ("guide", guide_id, guide["page"])
        for const_name, text_key, page in TOUR_STEPS:
            if text_key == "tour.language":
                continue
            prefix = getattr(zxnu_config, const_name, const_name)
            if title.startswith(prefix):
                return ("help", text_key, page)
        return None

    def about_current_tab(self):
        """Menu entry: always-available help for the CURRENT tab (the
        automatic offer only fires once per session — this is the manual
        way back to it)."""
        tabw = getattr(self._host, "_tab_widget", None)
        if tabw is None:
            return
        topic = self._tab_topic(tabw.tabText(tabw.currentIndex()))
        if topic is None:
            self.open_manual(None)
            return
        kind, key, page = topic
        if kind == "guide":
            self.start_guide(key)
        else:
            self.show_tab_help(key, page)

    def offer_help(self, text_key, page):
        # "Yes" resolves the CURRENT tab at click time — see offer_guide.
        self._respeak = lambda: self.offer_help(text_key, page)
        self._say(self._tr("help.offer"),
                  [(self._tr("btn.yes"), self.about_current_tab),
                   (self._tr("btn.later"), self._dismiss)],
                  gesture="look", cycles=4,
                  links=self._guide_links(page))
        self._offer_shown = True

    def show_tab_help(self, text_key, page):
        text = self._tr(text_key)
        if text_key in DISCLAIMER_STEPS:
            text += "\n\n" + self._tr("tour.disclaimer")
        self._respeak = lambda: self.show_tab_help(text_key, page)
        self._tour_active_page = page       # lets the wiki teaser append
        self._say(text,
                  [(self._tr("btn.close"), self._dismiss)],
                  gesture="point", cycles=6,
                  links=self._guide_links(page))
        self._request_teaser(page)

    # ── wiki content ─────────────────────────────────────────────────────
    def open_manual(self, page=None):
        try:
            webbrowser.open(WIKI_PAGE_BASE.format(page=page or
                                                  USER_MANUAL_PAGE))
        except Exception:
            logging.exception("wizard: could not open the wiki page")

    def _request_teaser(self, page):
        cached = self._teaser_cache.get(page)
        if cached is not None:
            self._on_teaser(page, cached)
            return
        # Confirmed offline (zxnu_network watcher): don't burn a thread on
        # a fetch that can only time out — and don't cache the failure, so
        # the teaser is retried once the network is back.
        gate = getattr(self._host, "_network_online", None)
        if gate is not None and not gate():
            return

        def _fetch(sig=self._fetch_signals, p=page):
            teaser = ""
            try:
                req = urllib.request.Request(
                    WIKI_RAW_BASE.format(page=p),
                    headers={"User-Agent": ZXART_USER_AGENT})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    md = resp.read().decode("utf-8", errors="replace")
                teaser = _teaser_from_markdown(md)
            except Exception:
                teaser = ""     # offline / page missing: silently skip
            sig.done.emit(p, teaser)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_teaser(self, page, teaser):
        if teaser:
            # Only successes are cached: a transient failure (or an
            # offline spell) must not blank the teaser for the session.
            self._teaser_cache[page] = teaser
        if teaser and page == self._tour_active_page and \
                self.bubble.isVisible():
            self.bubble.append_text(
                f"{self._tr('manual.teaser')} {teaser}")
            self._reposition()

    # ── fun ──────────────────────────────────────────────────────────────
    # Bags hold INDICES into the canonical lists (same length in every
    # language, tripwired), so a live language switch re-tells the SAME
    # joke/story in the new language.
    def _draw_index(self, table, bag):
        if not bag:
            n = len(wizard_lines(table, "en"))
            bag[:] = random.sample(range(n), n)
        return bag.pop()

    def _show_fun(self, table, idx, again_cb, gesture, cycles):
        lines = wizard_lines(table, self._lang())
        self._respeak = lambda: self._show_fun(table, idx, again_cb,
                                               gesture, cycles)
        self._say(lines[idx % len(lines)],
                  [(self._tr("btn.another"), again_cb),
                   (self._tr("btn.close"), self._dismiss)],
                  gesture=gesture, cycles=cycles)

    def tell_joke(self):
        self._show_fun(JOKES, self._draw_index(JOKES, self._joke_bag),
                       self.tell_joke, "cast", 4)

    def tell_story(self):
        self._show_fun(STORIES, self._draw_index(STORIES, self._story_bag),
                       self.tell_story, "talk", 14)

    # ── the click menu ───────────────────────────────────────────────────
    def show_menu(self):
        self._respeak = self.show_menu
        self._say(self._tr("menu.title"),
                  [(self._tr("btn.abouttab"), self.about_current_tab),
                   (self._tr("btn.quickstart"), self.start_quickstart),
                   (self._tr("btn.tour"), self.start_tour),
                   (self._tr("btn.joke"), self.tell_joke),
                   (self._tr("btn.story"), self.tell_story),
                   (self._tr("btn.off"), self.turn_off)],
                  gesture="wave", cycles=3,
                  links=self._guide_links(None)
                  + [(self._tr("btn.health"), self.show_health),
                     (self._tr("btn.font"), lambda: self.adjust_font(0))])


def build_wizard(host, *, configuration_dictionary):
    """Create the wizard (hidden until startup() decides) on *host*."""
    host._wizard = WizardManager(
        host, configuration_dictionary=configuration_dictionary)
    return host._wizard
