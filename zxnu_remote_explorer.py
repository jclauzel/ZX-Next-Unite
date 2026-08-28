"""Remote file-explorer widget for the NextSync tab.

A dual-pane file manager modelled on the SD Card Utility tab, but the right
("Next") pane is driven by the NextSync ``.sync5 -listen`` protocol
(zxnu_workers.run_remote_listen_server) instead of hdfmonkey:

    [ local file explorer ] [ ->:  :<- ] [ Next file explorer ]

Commands are pushed onto a queue.Queue the listen worker drains; results arrive
through RemoteExplorerSignals and are applied here on the UI thread.
"""

import html
import logging
import math
import os
import posixpath
import random
import shutil
import sys
import tempfile
import time

from zxnu_http_bridge import session_label   # stdlib-only at import
from zxnu_i18n import ui_tr_now

from PySide6.QtCore import (
    Qt, QCoreApplication, QDir, QEvent, QEventLoop, QModelIndex, QMimeData,
    QRect, QUrl, QSize, QTimer,
)
from PySide6.QtGui import (
    QBrush, QColor, QDrag, QFontMetrics, QKeySequence, QPainter, QPalette,
    QPen, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFileSystemModel, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QStyle, QTreeView,
    QVBoxLayout, QWidget,
)

from zxnu_config import (
    DEFAULT_COLOR_UP_DIRECTORY, DEFAULT_COLOR_DIR_NAME, DEFAULT_COLOR_DIR_TYPE,
    DEFAULT_COLOR_FILE_NAME, DEFAULT_COLOR_FILE_EXT, DEFAULT_COLOR_FILE_SIZE,
    DEFAULT_COLOR_GENERAL_TEXT, hex_to_qcolor, open_path_with_system_shell,
    qcolor_to_hex, readable_text_color,
)
from zxnu_workers import (
    CompactButton, DotDotFirstProxyModel, HdfProgressDialog,
    as_emulator_launch,
    bind_select_all_except_updir, zip_create_with_dialog,
    zip_extract_with_dialog, zip_unique_name,
)

# Roles carrying the remote entry's full posix path and its directory flag.
RE_PATH_ROLE = Qt.UserRole + 1
RE_ISDIR_ROLE = Qt.UserRole + 2
# Resolved ONCE at import: PySide6 materialises enum members lazily, and
# a first-touch inside a hot repaint path has been seen crashing when it
# lands mid garbage-collection (the machine-colour suite reproduced it).
RE_BG_ROLE = Qt.ItemDataRole.BackgroundRole
RE_FG_ROLE = Qt.ItemDataRole.ForegroundRole


# CompactButton (imported above from zxnu_workers) backs the Up / Refresh /
# + Drive buttons in both navigation bars — it is shared with the SD Card
# tab's explorer pair, whose rows mirror these.

ARROW_BTN_W = 40

# Sort persistence. Each pane's sort is stored as "<key>:<asc|desc>" where key is
# name/size/type (+ modified, local pane only). The two panes place those columns
# differently, so the key<->visible-column mappings differ: the local
# (QFileSystemModel) columns are Name(0)/Size(1)/Type(2)/Modified(3); the Next
# (QStandardItemModel) columns are Name(0)/Type(1)/Size(2) — the wire listing
# carries no timestamp, so the Next pane has no Modified column and a stray
# "modified" in its saved sort falls back to name via the .get() lookups.
RE_SORT_KEYS = ("name", "size", "type", "modified")
RE_LOCAL_SORT_COL = {"name": 0, "size": 1, "type": 2, "modified": 3}
RE_LOCAL_SORT_KEY = {v: k for k, v in RE_LOCAL_SORT_COL.items()}
RE_NEXT_SORT_COL = {"name": 0, "type": 1, "size": 2}
RE_NEXT_SORT_KEY = {v: k for k, v in RE_NEXT_SORT_COL.items()}


def _parse_re_sort(s):
    """Parse a saved "<key>:<asc|desc>" sort string to (key, Qt.SortOrder).

    Anything unrecognised falls back to the default: Name, ascending (A first).
    """
    key, _, order = (s or "").partition(":")
    key = key.strip().lower()
    if key not in RE_SORT_KEYS:
        key = "name"
    order = (Qt.DescendingOrder if order.strip().lower() == "desc"
             else Qt.AscendingOrder)
    return (key, order)


def _re_sort_to_str(key, order):
    return f"{key}:{'desc' if order == Qt.DescendingOrder else 'asc'}"


def _default_item_colors():
    """The SD Card Utility's image-tree item colours as a fresh dict of QColor.

    Keys mirror the SETTING_COLOR_* families used by the SD-card explorer so the
    host can push its live ``img_color_*`` values straight in (see
    RemoteExplorerWidget.set_item_colors). Used until the host supplies the
    user's configured colours.
    """
    return {
        "up_directory": hex_to_qcolor(DEFAULT_COLOR_UP_DIRECTORY),
        "dir_name":     hex_to_qcolor(DEFAULT_COLOR_DIR_NAME),
        "dir_type":     hex_to_qcolor(DEFAULT_COLOR_DIR_TYPE),
        "file_name":    hex_to_qcolor(DEFAULT_COLOR_FILE_NAME),
        "file_ext":     hex_to_qcolor(DEFAULT_COLOR_FILE_EXT),
        "file_size":    hex_to_qcolor(DEFAULT_COLOR_FILE_SIZE),
        "general_text": hex_to_qcolor(DEFAULT_COLOR_GENERAL_TEXT),
    }


class ColoredFileSystemModel(QFileSystemModel):
    """QFileSystemModel that tints each column with the SD-card explorer's
    configurable item colours, so the local pane matches the look of the SD Card
    Utility's image tree.

    ``colours`` is a live dict (see _default_item_colors) shared with the owning
    widget: it is mutated in place when the user changes the colours in Settings,
    and a repaint re-queries these values — no re-listing of the folder needed.
    QFileSystemModel's native column order is 0=Name, 1=Size, 2=Type, so the
    colour mapping is keyed off that (the view re-orders them visually to
    Name/Type/Size to mirror the image tree).
    """

    def __init__(self, colours, parent=None):
        super().__init__(parent)
        self._colours = colours

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (role == Qt.ItemDataRole.DisplayRole and index.isValid()
                and index.column() == 1):
            # Size column: one unified human-readable unit (blank for folders and
            # the ".." row), so it matches the Next pane instead of the OS-localised
            # "octets/Kio". The real byte count is still used for sorting (see
            # DotDotFirstProxyModel.lessThan).
            if self.isDir(index) or self.fileName(index) == "..":
                return ""
            return _human_size(self.size(index))
        if (role == Qt.ItemDataRole.DisplayRole and index.isValid()
                and index.column() == 2):
            # Type column: mirror the Next pane / SD-card image tree ("DIR" for
            # folders and the ".." row, the file's extension otherwise) instead
            # of the OS-localised description ("File folder", "Compressed
            # archived file", "text/plain", ...).
            if self.isDir(index) or self.fileName(index) == "..":
                return "DIR"
            return _ext_type_text(self.fileName(index))
        if (role == Qt.ItemDataRole.DisplayRole and index.isValid()
                and index.column() == 3):
            # Modified column: a fixed ISO-style stamp instead of the OS-locale
            # short form, so a column of dates lines up and reads at a glance
            # (the whole point is spotting which files a sync just touched).
            # Blank for the ".." row; folders keep their real mtime. Sorting
            # compares the actual QDateTime (DotDotFirstProxyModel.lessThan),
            # never this string.
            #
            # toLocalTime() is load-bearing: QDateTime.toString() prints the
            # time in whatever spec the QDateTime CARRIES, and Qt has shipped
            # file-time stamps in both LocalTime and UTC specs across
            # versions/platforms (Windows file times are UTC FILETIMEs
            # underneath). Displaying a UTC-spec'd stamp raw shifts every
            # date by the timezone offset — hours off, silently. Converting
            # explicitly is a no-op when the stamp is already local and the
            # correct wall-clock everywhere else.
            if self.fileName(index) == "..":
                return ""
            return (self.lastModified(index)
                    .toLocalTime().toString("yyyy-MM-dd HH:mm"))
        if role == Qt.ItemDataRole.ForegroundRole and index.isValid():
            c = self._colours
            if self.fileName(index) == "..":  # the parent ".." up-entry
                return c["up_directory"]
            is_dir = self.isDir(index)
            col = index.column()
            if col == 0:                       # Name
                return c["dir_name"] if is_dir else c["file_name"]
            if col == 2:                       # Type
                return c["dir_type"] if is_dir else c["file_ext"]
            if col == 1 and not is_dir:        # Size (blank for folders)
                return c["file_size"]
            if col == 3:                       # Modified (same tint as Size)
                return c["file_size"]
            return None                        # let the view use its default
        return super().data(index, role)


def _human_size(n):
    """One unified, human-readable size string used by BOTH Remote Explorer panes:
    exact bytes under 1 KiB, then one decimal in K/M/G (e.g. "512 B", "6.8 K",
    "4.0 M"). Replaces the OS-localised "octets/Kio" text on the local side and the
    terse "1B/10K" on the Next side. Sorting always uses the real byte count, never
    this text, so mixed magnitudes still order correctly.

    n is scaled down by 1024 each loop iteration, so once we stop the value is
    already in the current unit - format it as-is.
    """
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} G"


def _ext_type_text(name):
    """Type text for a file entry, used by BOTH Remote Explorer panes: the first
    extension segment, exactly as the SD-card image tree shows it (guarded by
    the '.' test, so [1] is always present)."""
    return name.split(".")[1] if "." in name else ""


def _posix_join(base, name):
    base = base or "/"
    if not base.endswith("/"):
        base += "/"
    return posixpath.normpath(base + name)


def _re_drive_of(path):
    """Drive letter of a remote path ("M:/games" -> "M"), "" when unprefixed.

    An unprefixed path ("/games") lands on the dot's current drive, exactly as
    it always did; a prefixed one targets that drive explicitly (esxDOS
    resolves "m:/..." natively, so the dot needs no translation)."""
    p = path or ""
    return p[0].upper() if len(p) >= 2 and p[1] == ":" and p[0].isalpha() else ""


def _re_norm_dir(p):
    """Normalise a remote directory path: a bare drive ("M:", as
    posixpath.dirname yields for "M:/x") becomes that drive's root ("M:/"),
    and empty becomes "/"."""
    if not p:
        return "/"
    if _re_drive_of(p) and len(p) == 2:
        return p + "/"
    return p


def _norm_remote_dir(p):
    """Normalise a saved Next-side folder path to an absolute posix dir.

    Blank/invalid restores to "/". Keeps an optional drive prefix ("M:/games")
    so a session on a secondary drive restores to that drive. Used when
    restoring the last-browsed remote folder from the config file (see
    RemoteExplorerWidget.on_connected).
    """
    p = (p or "").replace("\\", "/").strip()
    if not p:
        return "/"
    drive = _re_drive_of(p)
    if drive:
        rest = p[2:]
        if not rest.startswith("/"):
            rest = "/" + rest
        return drive + ":" + posixpath.normpath(rest)
    if not p.startswith("/"):
        p = "/" + p
    return posixpath.normpath(p)


def _drag_button_down():
    """True while a physical mouse button is still held — i.e. the drag is
    still HOVERING, no drop has happened yet. Windows drop targets are
    documented to call IDataObject::GetData mid-hover (CFSTR_INDRAGLOOP
    exists precisely because of that), and blocking there would freeze the
    drag cursor for the whole download; the real drop's data extraction
    only ever runs after the button was released, so the button state IS
    the hover/drop discriminator. GetAsyncKeyState is used because Qt's
    own mouseButtons() goes stale once the OLE drag loop owns the mouse.
    Non-Windows platforms request drag data at drop time, so they never
    need the distinction (False). Both buttons are checked: VK_LBUTTON /
    VK_RBUTTON are PHYSICAL buttons for GetAsyncKeyState, so a swapped-
    buttons primary drag holds VK_RBUTTON."""
    if sys.platform == "win32":
        try:
            import ctypes
            state = ctypes.windll.user32.GetAsyncKeyState
            return bool((state(0x01) | state(0x02)) & 0x8000)
        except Exception:
            return False
    return False


class _StagedDragMime(QMimeData):
    """Drag payload for Next-pane entries whose local copies are still
    DOWNLOADING when the drag starts.

    Two hard requirements pull in opposite directions, and both are
    field-proven:

    1. The drag must start the INSTANT the gesture does. The first cut
       downloaded first and called QDrag.exec() after: over a real UART the
       fetch takes seconds, the user releases over Explorer long before it
       ends, and a drag exec'd after the release drops nothing.
    2. The file URLs must be advertised — non-empty — from that same
       instant. Windows decides the drop-allowed cursor DURING hover, by
       asking the data object whether it can produce CF_HDROP, which Qt
       answers by calling urls() on this object. The second cut withheld
       the URLs until the download finished, and Explorer showed the
       red "no drop" cursor (the field report: "it becomes a red circle
       that clearly says I won't be able to paste here").

    Both are satisfied by naming the staged paths UP FRONT: the staging
    folder and the file names inside it are known as soon as the drag
    starts (``stage['expect']``), long before the bytes arrive, and the
    URLs are stored on this object immediately. Only the CONTENT has to
    wait — so retrieveData blocks just once, at the real drop, until the
    background download has actually written those files. That is the
    delayed-rendering shape WinSCP/7-Zip/Outlook use; Explorer imposes no
    timeout on a GetData that keeps pumping.

    A request is treated as the real drop only when no mouse button is
    held and the cursor is not over our own panes — Explorer and our own
    drag handlers both ask this question repeatedly mid-hover, and
    blocking there would stall the gesture the user is still making."""

    def __init__(self, widget, stage):
        super().__init__()
        self._widget = widget
        self._stage = stage
        self._waited = False

    def retrieveData(self, fmt, preferred):
        if (fmt == "text/uri-list" and not self._waited
                and self._stage is not None
                and not self._stage.get('done')
                and not self._widget._drag_over_own_panes()
                and not _drag_button_down()):
            # The real drop, with the download still in flight: hold the
            # target here until the files this payload NAMES exist.
            self._waited = True
            staged = self._widget._await_drag_stage(self._stage)
            if staged:
                # Serve exactly what landed (a folder download can differ
                # from the predicted top-level names).
                self.setUrls([QUrl.fromLocalFile(p) for p in staged])
        return super().retrieveData(fmt, preferred)


class SessionTab(QWidget):
    """One connected Next as a vertical tab down the side of the Next pane.

    The machine combo tells you which machine you drive, but switching
    through it costs a click, a read and a second click. With two or more
    Nexts on the line the roster is small and fixed, so it can simply BE
    on screen: one tab per machine, piled top-down, the driven one lit.
    Clicking a tab is the combo pick it stands for — same request, same
    guards (RemoteExplorerWidget._request_machine).

    Text runs bottom-to-top like every side tab (IDE tabs, browser
    sidebars): a horizontal label wide enough for "10.0.0.7 #1 - N-GO"
    would eat ~140px of the listing, and the listing is the point of the
    pane. Colours come from the PALETTE rather than the app's hardcoded
    chrome so the tab follows whatever theme is live.

    Two opt-in channels ride on top of that (9.5.27):

    * ``tint`` - the user's per-machine colour (the same one the machine
      combo wears), painted INSTEAD of the palette background so a Next
      is recognisable by colour alone. The active/inactive distinction
      survives it: the driven machine gets the tint at full strength
      plus a bright rim, a benched one a darkened wash. The label flips
      between black and white to stay readable on whatever was picked
      (``readable_text_color``) - a fixed foreground is invisible on
      half the colour wheel.
    * ``on_menu`` - a right-click handler, called with the GLOBAL
      position, so the strip can offer the same per-machine actions the
      top bar offers for the driven one (switch / name+colour /
      disconnect). Tabs are rebuilt wholesale on every roster change, so
      the handler is re-bound with the tab and can never outlive its sid.
    """

    W = 26                       # strip width; the text is what is tall
    _PAD = 10                    # end padding, both ends of the label

    def __init__(self, text, active, on_click, parent=None, *,
                 tint=None, on_menu=None):
        super().__init__(parent)
        self._text = text or ""
        self._active = bool(active)
        self._on_click = on_click
        # QColor or None. Kept as a plain attribute rather than widget
        # state to restyle later: _rebuild_session_strip throws every tab
        # away on each roster/name change, so the tint has to arrive with
        # the constructor or it would be lost on the next repaint.
        self._tint = tint if (tint is not None and tint.isValid()) else None
        self._on_menu = on_menu
        # "Shown, but cannot be used right now" (9.6.2). A plain attribute
        # rather than setEnabled(False): paintEvent below never consults
        # isEnabled(), so a disabled tab with a user-picked tint would look
        # exactly like a working one - dead to clicks and silent about it.
        # Staying enabled also keeps tooltip delivery entirely in our hands,
        # which is where the reason for the block is shown.
        self._blocked = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self._text)
        fm = QFontMetrics(self.font())
        want = fm.horizontalAdvance(self._text) + self._PAD * 2
        # Capped so one long name cannot push the others off a short pane;
        # the full text is always in the tooltip.
        self.setFixedSize(self.W, max(56, min(200, want)))

    def set_active(self, active):
        active = bool(active)
        if active != self._active:
            self._active = active
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._blocked:
            event.accept()             # greyed out: the tooltip says why
        elif event.button() == Qt.LeftButton and self._on_click is not None:
            self._on_click()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Swallow doubles: QWidget's default re-dispatches a double click
        into mousePressEvent, so without this a fast double click fired
        _on_click TWICE - harmless on a session tab (switching to the
        machine already driven is a no-op) but an EmulatorTab's click
        LAUNCHES, and two launches mount one disk image twice. The first
        press of the pair has already acted; the double adds nothing."""
        if event.button() == Qt.LeftButton:
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """Right-click: hand the GLOBAL position to the host's builder.

        contextMenuEvent rather than the CustomContextMenu policy the two
        views use: this is a hand-painted QWidget with no viewport to map
        through, and the event already carries the screen position a
        QMenu wants.
        """
        if self._on_menu is None:
            super().contextMenuEvent(event)
            return
        event.accept()
        self._on_menu(event.globalPos())

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self.palette()
        if self._tint is not None:
            # The machine's own colour. Active = as picked; benched = the
            # same hue, dimmed - so "which one is lit" stays a brightness
            # difference (the palette's own signal) and never depends on
            # telling two hues apart.
            bg = self._tint if self._active else self._tint.darker(190)
            fg = readable_text_color(bg)
        elif self._active:
            bg = pal.color(QPalette.ColorRole.Highlight)
            fg = pal.color(QPalette.ColorRole.HighlightedText)
        else:
            bg = pal.color(QPalette.ColorRole.Button).darker(115)
            fg = pal.color(QPalette.ColorRole.ButtonText)
        rimmed = self._tint is not None and self._active
        dashed = False
        if self._blocked:
            # Three cues, because one is not enough at 26px. Draining the
            # SATURATION carries the tinted case on its own (a green tab
            # becomes a grey one), but an untinted tab is already grey, so
            # darkening alone moved it only ~19% and read as a rendering
            # artefact. The dashed outline and the translucent label are
            # what make the untinted case unmistakable.
            grey = QColor(bg).toHsv()
            grey.setHsv(grey.hue(), 0, max(70, grey.value() - 40))
            bg = grey
            fg = readable_text_color(bg)
            fg.setAlpha(130)
            rimmed, dashed = False, True
        if rimmed:
            # A rim in the readable foreground: on a dark tint the dimmed
            # and the full shade can sit close together, and the driven
            # machine must never be ambiguous.
            p.setPen(QPen(fg, 2))
        elif dashed:
            _dash = QPen(fg, 1, Qt.PenStyle.DashLine)
            p.setPen(_dash)
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(bg)
        # Rounded on the OUTER edge only would need a path; a plain
        # rounded rect reads as a tab well enough and costs one call.
        # The rim needs a pixel of room for the pen (drawn centred on the
        # path); everything else keeps the original geometry exactly.
        box = (self.rect().adjusted(1, 1, -2, -2) if (rimmed or dashed)
               else self.rect().adjusted(0, 0, -1, -1))
        p.drawRoundedRect(box, 6, 6)
        # Bottom-to-top: put the origin at the bottom-left and turn left,
        # which makes the drawing rect (height x width).
        p.translate(0, self.height())
        p.rotate(-90)
        p.setPen(fg)
        box = QRect(self._PAD, 0, self.height() - self._PAD * 2, self.W)
        text = QFontMetrics(self.font()).elidedText(
            self._text, Qt.TextElideMode.ElideRight, box.width())
        p.drawText(box, int(Qt.AlignmentFlag.AlignVCenter
                            | Qt.AlignmentFlag.AlignLeft), text)
        p.end()


class EmulatorTab(SessionTab):
    """One INSTALLED emulator as a vertical tab down the LEFT edge of the
    local pane - the mirror of the session strip on the Next side.

    Same 26px strip, same bottom-to-top label, same the-whole-widget-is-
    the-button. What differs is semantic: an emulator is never "the one
    this pane drives", so there is no active state to light, and a click
    LAUNCHES instead of switching. The tab exists only while its emulator
    is detected, which is the same rule the SD Card tab's Launch buttons
    follow (they are hidden, not disabled, when nothing was found) - so
    the strip answers "what can I boot from here?" just by existing.

    A right-click carries the same ``on_menu`` channel a SessionTab has,
    and the ``tint`` the same meaning (9.6.0): the user's picked colour
    for THAT emulator, painted instead of the palette background. There
    is no active/inactive here, so the tint is simply the colour as
    picked. One colour per emulator, shared by both strips and by the SD
    Card tab's Launch buttons — see emulator_color_menu below.
    """

    def __init__(self, text, on_click, parent=None, *, tooltip="",
                 tint=None, on_menu=None, blocked=False):
        super().__init__(text, False, on_click, parent,
                         tint=tint, on_menu=on_menu)
        # *blocked*: the emulator is installed but cannot start right now
        # (no disk image, or another emulator still holds it). The tab stays
        # in the strip - dropping it would read as "MAME is gone", which is
        # a different and wrong answer - but is greyed and inert, with the
        # reason as its tooltip. Tabs are rebuilt wholesale on every
        # refresh, so this arrives with the constructor exactly like *tint*.
        self._blocked = bool(blocked)
        if self._blocked:
            self.setCursor(Qt.ForbiddenCursor)
        if tooltip:
            self.setToolTip(tooltip)


def emulator_color_menu(parent, label, current, on_picked, global_pos):
    """THE right-click colour editor for an emulator surface.

    One function so the three surfaces that wear an emulator's colour —
    the SD Card tab's strip, the Remote Explorer's strip and the SD Card
    tab's Launch buttons — offer the same two actions and write the same
    value. *current* is a QColor or None; *on_picked* takes "#rrggbb" (or
    "" to reset) and is responsible for persisting and repainting.

    menu.exec() RETURNS the chosen action rather than firing a triggered
    handler, so the colour dialog opens after the menu's input grab has
    already been released — the rule the NextSync explorer menu learned
    the hard way (a QMessageBox opened from inside menu.exec() fights it
    and can hang the UI).
    """
    menu = QMenu(parent)
    act_set = menu.addAction(
        ui_tr_now("Set the {emulator} color…").format(emulator=label))
    act_reset = menu.addAction(
        ui_tr_now("Reset the {emulator} color").format(emulator=label))
    act_reset.setEnabled(current is not None)
    chosen = menu.exec(global_pos)
    if chosen is act_set:
        # Same 3-arg house form as the Settings colour rows and the
        # machine-colour picker: the platform's native dialog, English
        # title (the app deliberately leaves the colour dialog alone).
        picked = QColorDialog.getColor(
            current if current is not None else QColor("#3cd8e8"),
            parent, f"Choose color - {label}")
        if picked.isValid():
            on_picked(qcolor_to_hex(picked))
    elif chosen is act_reset:
        on_picked("")


class MachineIdentityDialog(QDialog):
    """The "Name this Next" editor: friendly name AND machine colour.

    Was a bare QInputDialog.getText until 9.5.27. A name tells two Nexts
    apart once you READ the combo; a colour tells them apart at a glance,
    on the combo and on the session tabs at the same time - which is the
    whole job of the strip. Both are keyed by ADDRESS and persisted by
    the host, so they survive whatever session id the machine is dealt
    next.

    The colour is optional and stays optional: no colour means the tab
    and the combo keep the palette/chrome look they always had, so
    nobody who never opens this dialog sees a change.
    """

    def __init__(self, addr, name="", color=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(ui_tr_now("Name this Next"))
        self._color = color if (color is not None and color.isValid()) else None

        box = QVBoxLayout(self)
        box.addWidget(QLabel(
            ui_tr_now("Friendly name for {addr} (empty removes it):").format(
                addr=addr), self))
        self.name_edit = QLineEdit(str(name or ""), self)
        self.name_edit.selectAll()
        box.addWidget(self.name_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel(ui_tr_now("Color:"), self))
        self.color_btn = QPushButton(self)
        self.color_btn.setFixedSize(80, 22)
        self.color_btn.setToolTip(ui_tr_now(
            "Pick a color for this Next. It tints the machine list and "
            "this machine's tab in the session strip."))
        self.color_btn.clicked.connect(self._pick_color)
        row.addWidget(self.color_btn)
        self.color_clear = QPushButton("✕", self)
        self.color_clear.setFixedSize(22, 22)
        self.color_clear.setToolTip(ui_tr_now("Clear the color"))
        self.color_clear.clicked.connect(self._clear_color)
        row.addWidget(self.color_clear)
        row.addStretch(1)
        box.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._paint_swatch()

    def _paint_swatch(self):
        """The swatch shows the colour, or a dashed outline for "none" -
        an empty solid button would read as black on a dark theme."""
        if self._color is None:
            self.color_btn.setStyleSheet(
                "QPushButton { background: transparent;"
                " border: 1px dashed #888; border-radius: 3px; }")
        else:
            self.color_btn.setStyleSheet(
                f"QPushButton {{ background-color: {qcolor_to_hex(self._color)};"
                " border: 1px solid #888; border-radius: 3px; }")
        self.color_clear.setEnabled(self._color is not None)

    def _pick_color(self):
        # Same 3-arg house form as the Settings colour rows: the platform's
        # native picker where there is one, and an English title (the app
        # deliberately leaves the colour dialog untranslated).
        chosen = QColorDialog.getColor(
            self._color if self._color is not None else QColor("#3cd8e8"),
            self, f"Choose color - {self.windowTitle()}")
        if chosen.isValid():
            self._color = chosen
            self._paint_swatch()

    def _clear_color(self):
        self._color = None
        self._paint_swatch()

    def result_values(self):
        """(name, colour-or-None) as edited. The caller decides what an
        empty name or a missing colour means - this dialog only reports."""
        return self.name_edit.text(), self._color


class IdleStarfieldOverlay(QWidget):
    """The idle-details panel shown over the empty Next pane while
    disconnected: a soft 8-bit starfield (chunky parallax pixel stars plus
    the occasional comet) painted behind the host/IP guidance text.

    Replaces the transparent QLabel the pane used before, whose text sat
    directly over the disabled Next tree and collided with its header row
    and footer buttons — this widget paints its own deep-space backdrop, so
    nothing bleeds through. It keeps the tiny API the pane drives
    (text()/setText(), set_label_stylesheet) and stays mouse-transparent
    like the label it replaces. Stars are plain bright dots ON PURPOSE —
    never the Sinclair rainbow stripes (trade-dress rule; see the sprite
    conventions in zxnu_wizard.py).
    """

    _TICK_MS = 40                     # 25 fps — a soft drift, cheap to paint
    _BACKDROP = (6, 9, 26, 238)       # deep-space blue-black, near opaque
    # Bright 8-bit dot palette, weighted towards white so the field reads as
    # stars with just a sprinkle of colour.
    _STAR_COLORS = (
        (245, 245, 245), (245, 245, 245), (245, 245, 245),
        (140, 224, 255), (255, 224, 140), (170, 170, 255), (255, 140, 224),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        # Purely informational, like the label it replaces: never intercept
        # the pane's mouse events.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(self._label)
        self._stars = []          # dicts: x/y px, size, speed, colour, twinkle
        self._comets = []         # dicts: x/y, vx/vy, trail [(x, y), ...]
        self._frame = 0
        self._next_comet_at = self._comet_delay()
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ---- the tiny API the pane drives (QLabel-compatible) ----------------

    def text(self):
        return self._label.text()

    def setText(self, text):
        self._label.setText(text)

    def set_label_stylesheet(self, css):
        self._label.setStyleSheet(css)

    # ---- animation lifecycle: only tick while actually on screen ---------

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        # The NextSync tab going to the back hides this child too — stop
        # burning frames until it comes forward again.
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_star_count()

    # ---- the field --------------------------------------------------------

    def _comet_delay(self):
        return random.randint(160, 400)           # ~6-16 s between comets

    def _make_star(self, anywhere):
        w = max(1, self.width())
        size = random.choice((1, 1, 1, 2, 2, 3))  # mostly tiny: depth illusion
        return {
            "x": float(random.randrange(w) if anywhere
                       else w + random.randint(0, 24)),
            "y": float(random.randrange(max(1, self.height()))),
            "size": size,
            # Bigger (nearer) stars drift faster: cheap parallax.
            "speed": (0.10 + 0.16 * size) * random.uniform(0.75, 1.3),
            "color": random.choice(self._STAR_COLORS),
            "phase": random.uniform(0.0, 2 * math.pi),
            "rate": random.uniform(0.03, 0.10),   # twinkle speed, rad/frame
        }

    def _sync_star_count(self):
        """Keep the star density proportional to the pane area (resize adds
        or culls stars instead of reseeding, so nothing visibly jumps)."""
        if self.width() <= 0 or self.height() <= 0:
            return
        want = max(24, min(140, (self.width() * self.height()) // 4200))
        while len(self._stars) < want:
            self._stars.append(self._make_star(anywhere=True))
        del self._stars[want:]

    def _spawn_comet(self):
        h = max(2, self.height())
        return {
            "x": float(self.width() + 8),
            "y": float(random.randint(h // 12, h // 2)),
            "vx": -random.uniform(3.0, 5.0),
            "vy": random.uniform(0.6, 1.6),
            "trail": [],
        }

    def _tick(self):
        self._frame += 1
        w, h = self.width(), self.height()
        for s in self._stars:
            s["x"] -= s["speed"]
            if s["x"] < -3:           # wrapped: re-enter on the right edge
                s["x"] = w + random.randint(0, 24)
                s["y"] = float(random.randrange(max(1, h)))
        if self._frame >= self._next_comet_at and len(self._comets) < 2:
            self._comets.append(self._spawn_comet())
            self._next_comet_at = self._frame + self._comet_delay()
        for c in self._comets:
            c["trail"].insert(0, (c["x"], c["y"]))
            del c["trail"][14:]
            c["x"] += c["vx"]
            c["y"] += c["vy"]
        self._comets = [c for c in self._comets
                        if c["x"] > -40 and c["y"] < h + 40]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        # Chunky pixels on purpose: no antialiasing anywhere.
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*self._BACKDROP))
        p.drawRoundedRect(self.rect(), 8, 8)
        for s in self._stars:
            r, g, b = s["color"]
            # Soft twinkle: each star breathes on its own phase and rate.
            base = 120 + 36 * s["size"]
            a = int(base * (0.6 + 0.4 * math.sin(s["phase"]
                                                 + s["rate"] * self._frame)))
            p.fillRect(int(s["x"]), int(s["y"]), s["size"], s["size"],
                       QColor(r, g, b, max(0, min(255, a))))
        for c in self._comets:
            # Tail first (newest chunk brightest), then the head over it.
            n = len(c["trail"])
            for i, (tx, ty) in enumerate(c["trail"]):
                fade = 1.0 - (i + 1) / (n + 1)
                sz = 2 if i < 5 else 1
                p.fillRect(int(tx), int(ty), sz, sz,
                           QColor(150, 220, 255, int(200 * fade)))
            p.fillRect(int(c["x"]) - 1, int(c["y"]) - 1, 3, 3,
                       QColor(255, 255, 255, 235))
        p.end()


class RemoteExplorerWidget(QWidget):
    """Dual-pane local <-> Next file manager.

    enqueue(cmd_tuple) is the single channel to the listen worker; the host wires
    the worker's signals to on_connected/on_disconnected/on_listing/on_got/
    on_put_done/on_op_done.  `log` is an optional callable(str) for status lines.
    `on_sync_root_changed` is an optional callable(str) fired whenever the user
    picks (or clears) the local "sync root" folder, so the host can enable/disable
    the 'Start NextSync server' button.
    """

    def __init__(self, enqueue, local_start_dir=None, log=None, parent=None,
                 drain=None, on_sync_root_changed=None, remote_start_dir=None,
                 on_remote_cwd_changed=None, local_sort=None, next_sort=None,
                 on_sort_changed=None, on_toast=None, extra_drives=None,
                 on_extra_drives_changed=None, emulator_entries=None,
                 remote_cwd_for=None, machine_name_for=None,
                 on_machine_name_changed=None, machine_color_for=None,
                 on_machine_color_changed=None, enqueue_to=None,
                 emulator_launchers=None, emulator_color_for=None,
                 on_emulator_color_changed=None, local_drives=None):
        super().__init__(parent)
        self._enqueue_raw = enqueue          # host closure: put one command
        # host closure: enqueue_to(sid, cmd) -> bool, delivering ONE command
        # to a NAMED session's own queue instead of the shared one the baton
        # holder drains. The HTTP bridge has had this since ?session=N; the
        # session strip's right-click menu needs it for the same reason -
        # "disconnect THAT Next" must not mean "disconnect whichever Next
        # this pane happens to drive". Without the hook the menu simply
        # offers nothing for a benched machine.
        self._enqueue_to_raw = enqueue_to
        self._drain_raw = drain              # host closure: empty the queue, -> count
        self._log = log or (lambda s: None)
        # host closure: path -> [EmulatorAutostart] for the emulators that are
        # installed and can boot that file, so both panes can offer the same
        # "Start <emulator> with <file>" entries as the SD Card tab. The widget
        # deliberately does not know which emulators exist; without the hook it
        # simply offers nothing.
        self._emulator_entries = emulator_entries or (lambda path: [])
        # host closure: () -> [(name, launch_callable)] for the emulators
        # that are INSTALLED right now, newest detection wins. Drives the
        # left-hand emulator strip. The widget deliberately knows nothing
        # about which emulators exist or how one is started - same seam as
        # _emulator_entries above; without the hook the strip never shows.
        self._emulator_launchers = emulator_launchers or (lambda: [])
        self._on_sync_root_changed = on_sync_root_changed or (lambda p: None)
        # Surface Next-side failures ('F' replies / abandoned transfers) to the
        # user: on_toast(title, message, variant) pops a host toast.
        self._on_toast = on_toast or (lambda title, msg, variant="red": None)
        # Persist/restore the Next-side folder across (re)connections: on connect
        # we jump back to the last folder browsed, and every listing reports the
        # new folder to the host so it can save it (see on_connected/on_listing).
        # Since 9.5.14 both directions are PER-MACHINE: the report carries the
        # active peer's address, and remote_cwd_for(addr) answers with that
        # machine's remembered folder (the generic remote_start_dir stays as
        # the fallback for a machine never seen before).
        self._on_remote_cwd_changed = (on_remote_cwd_changed or
                                       (lambda p, a=None: None))
        self._remote_cwd_for = remote_cwd_for
        # Friendly machine names (9.5.18): machine_name_for(addr) answers
        # the saved name for an ADDRESS (None/"" = none) and the ✎ button
        # reports edits through on_machine_name_changed(addr, name) — the
        # host persists the map, so a machine that reconnects (whatever
        # session id the new worker deals it) greets by name in the combo:
        # "10.0.0.185 #1 - N-Go". Keyed by address on purpose: session ids
        # restart at 1 with every worker and would forget the name.
        self._machine_name_for = machine_name_for or (lambda a: None)
        self._on_machine_name_changed = (on_machine_name_changed or
                                         (lambda a, n: None))
        # Per-machine COLOUR (9.5.27), the exact twin of the name pair above
        # and keyed by the same ADDRESS: machine_color_for(addr) answers a
        # "#rrggbb" string (None/"" = untinted) and edits are reported through
        # on_machine_color_changed(addr, hex) for the host to persist. Kept as
        # a separate map from the names on purpose - the existing cfg entries
        # are plain strings, and widening them would break every cfg already
        # in the field.
        self._machine_color_for = machine_color_for or (lambda a: None)
        self._on_machine_color_changed = (on_machine_color_changed or
                                          (lambda a, c: None))
        # Per-EMULATOR colour (9.6.0): the same shape again, keyed by the
        # emulator instead of the machine. The colour a user picks here is
        # deliberately the SAME one the SD Card tab's strip and its Launch
        # buttons wear - the host owns one map for all three, so "CSpect
        # is green" is true everywhere in the app or nowhere.
        self._emulator_color_for = emulator_color_for or (lambda n: None)
        self._on_emulator_color_changed = (on_emulator_color_changed or
                                           (lambda n, c: None))
        self._remote_cwd_addr = None         # last addr a folder was reported for
        self._remote_start_dir = _norm_remote_dir(remote_start_dir)
        # Per-pane sort (column + direction), restored from the config and saved
        # via on_sort_changed(which, "<key>:<asc|desc>") whenever the user clicks
        # a column header. Defaults to Name ascending in both panes.
        self._on_sort_changed = on_sort_changed or (lambda which, value: None)
        self._local_sort = _parse_re_sort(local_sort)
        self._next_sort = _parse_re_sort(next_sort)
        self._restoring_sort = False         # guard: don't re-save while restoring
        self._next_entries = []              # last Next listing, for re-sorting
        self._cwd = "/"                      # current Next directory
        self._connected = False
        # Drives on the Next, from the dot's getdrives ('W') reply: the dot
        # reports {C, M, current} only - it can never PROBE other letters
        # (any file call on an unmounted drive crashes a dotN, learned on real
        # hardware). Empty until known (or when the dot predates v5.1, in
        # which case everything stays on the dot's current drive as before).
        self._drives = []
        self._default_drive = ""             # the dot's current drive letter
        self._drive_combo_guard = False      # ignore programmatic combo changes
        # Free space per drive letter, from the dot's 'Z' reply (v5.2+):
        # letter -> free bytes (int), or None when the query failed / the dot
        # predates 'Z'. Free space is the ONLY storage metric a dotN can
        # obtain safely (total partition size needs +3DOS/IDEDOS calls that
        # crash a dotN), so the pane shows "free" alone, never a percentage.
        self._free_space = {}
        # Extra drive letters the USER declared (additional SD readers /
        # partitions the dot cannot discover), persisted by the host via
        # on_extra_drives_changed (SETTING_NEXTSYNC_EXTRA_DRIVES, e.g. "DE").
        self._on_extra_drives_changed = on_extra_drives_changed or (lambda s: None)
        self._extra_drives = sorted({c.upper() for c in (extra_drives or "")
                                     if c.upper() in "CDEFGHIJKLMNOP"})
        # The local folder the Remote Explorer works in. "" until the user picks
        # one (first run); a folder must be chosen before the server can start.
        self._sync_root = ""
        self._browse_root = ""               # folder the local tree is rooted at
        # Internal copy/paste buffer shared between the two panes, as
        # (kind, items, mode) where kind is "local"/"next", mode is "copy"/"cut"
        # and items is [local_path, …] or [(remote_path, is_dir), …].
        self._clip = None
        # In-flight cut/move jobs, oldest first. Each is a dict:
        #   {token, src_kind, src_path, is_dir, local_copy, ok}
        # The source is deleted only once its marker confirms a clean transfer.
        self._cut_jobs = []
        self._cut_seq = 0

        # A remote "operation" is a batch of commands the user shouldn't
        # interrupt (except via Cancel): transfers, mkdir/rename/delete, moves.
        # While one runs the whole widget is disabled and a modal progress
        # dialog is shown. Plain directory listings (ls) are NOT operations.
        self._op_active = False
        self._op_total = 0            # commands queued so far for this op
        self._op_completed = 0        # commands finished so far
        self._op_cancelled = False
        self._op_determinate = True   # False -> marquee bar (totals may grow)
        self._op_title = ""
        self._op_dialog = None
        # Failures ('F' replies / abandoned transfers) seen during the running
        # operation, toasted as one summary when it ends (so a batch that fails
        # many items shows a single toast, not a storm). ``_op_toast_mkdir`` lets
        # a deliberate New Folder report a failed mkdir, while the many mkdirs of a
        # recursive upload stay quiet (a failed one there just means "exists").
        self._op_failures = []
        self._op_toast_mkdir = False
        # Heartbeat-driven progress (rcpy): the Next pushes a named 'D' block
        # as each file copy starts and an empty one per 64 KB inside big
        # files. Against the totals measured by the paste's rfsize precheck
        # these drive a real percentage (max of the byte estimate, the file
        # count and the command count - whichever profile fits the tree).
        self._op_bytes_total = 0      # precheck total bytes (0 = untracked)
        self._op_files_total = 0      # precheck total files (0 = untracked)
        self._op_bytes_est = 0        # 64 KB per empty keepalive seen
        self._op_files_seen = 0       # named 'D' blocks seen
        self._op_last_name = ""       # last item name the Next reported
        # "Close this window and continue in the background": the label the
        # op's dialog button carries instead of "Cancel" (rcpy can't stop an
        # in-flight Next-side copy), and whether the user pressed it. While
        # backgrounded only the NEXT pane is blocked (with an overlay); the
        # local pane stays usable and the outcome arrives as a toast.
        self._op_background_label = None
        self._op_background = False
        self._op_quiet_failures = False
        self._op_quiet_ui = False
        self._next_overlay = None     # the "copy in progress" overlay QLabel
        # Free-space precheck state for a Next->Next paste (rfsize each source
        # + a fresh free-space read of the destination drive BEFORE the rcpy
        # is allowed to start). None when no precheck is pending.
        self._precheck = None
        # Drag-out staging state (see _begin_drag_stage): the current stage
        # dict, the retired-but-not-yet-deletable stage folders, whether this
        # session already swept old runs' leftovers, and how long a drop may
        # wait for a still-running staging download inside the drag mime's
        # retrieveData (test-tunable).
        self._drag_stage = None
        self._drag_stage_hist = []
        self._drag_swept = False
        self._drag_stage_wait_ms = 60000
        # >0 while a drag hovers one of our own panes (see
        # _drag_over_own_panes): counted up by their drag handlers, cleared
        # on leave/drop.
        self._drag_hover = 0

        # Per-item font colours, mirroring the SD Card Utility's image tree. The
        # host pushes the user's configured colours in via set_item_colors(); the
        # dict is mutated in place so ColoredFileSystemModel (which holds the same
        # reference) always sees the current values.
        self._colors = _default_item_colors()

        # ---- left: local file explorer ------------------------------------
        self.local_model = ColoredFileSystemModel(self._colors, self)
        self.local_model.setRootPath("")
        # Emit a ".." parent-directory row (like the SD Card tab's local tree and
        # the Next pane): clear NoDotAndDotDot to show it, keep NoDot to hide ".".
        # DotDotFirstProxyModel pins that ".." entry to the top of the list.
        self.local_model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
        # Name-filter proxy, mirroring the classic sync local explorer: it filters
        # by file name and keeps any ".." entry on top. The per-item foreground
        # colours pass straight through to the source model.
        self.local_proxy = DotDotFirstProxyModel(
            recursiveFilteringEnabled=True,
            filterRole=QFileSystemModel.FileNameRole)
        self.local_proxy.setSourceModel(self.local_model)
        self.local_proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.local_proxy.setDynamicSortFilter(True)

        self.local_view = QTreeView(self)
        self.local_view.setModel(self.local_proxy)
        self.local_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.local_view.setUniformRowHeights(True)
        self.local_view.setSortingEnabled(True)
        # Ctrl-A selects the folder's contents, never the ".." parent row.
        bind_select_all_except_updir(self.local_view, self._is_local_updir)
        # Show Name / Type / Size / Modified, ordering the first three like the
        # SD-card image tree — QFileSystemModel's native order is Name(0),
        # Size(1), Type(2), Date Modified(3), so swap Size/Type visually and let
        # Modified sit last. The Modified column is the point of the local pane
        # during a sync session: it shows at a glance which files just changed
        # on this side (the Next pane cannot mirror it — the wire listing
        # carries no timestamp).
        self.local_view.header().swapSections(1, 2)
        self.local_view.setColumnWidth(0, 250)
        self.local_view.setColumnWidth(3, 130)
        # Persist the chosen sort: react to header clicks, and apply the saved one
        # now (guarded so applying it doesn't count as a user change).
        self.local_view.header().sortIndicatorChanged.connect(self._on_local_sort_changed)
        self._apply_local_sort()
        self.local_view.setDragEnabled(True)
        self.local_view.setAcceptDrops(True)
        self.local_view.setDropIndicatorShown(True)
        # A drag within the local pane proposes a COPY (we perform the copy
        # ourselves in _local_drop); without this Qt would propose an internal
        # move for same-view drags.
        self.local_view.setDefaultDropAction(Qt.CopyAction)
        self.local_view.doubleClicked.connect(self._local_double_clicked)
        self.local_view.dragEnterEvent = self._local_drag_enter
        self.local_view.dragMoveEvent = self._local_drag_enter
        self.local_view.dragLeaveEvent = self._pane_drag_leave
        self.local_view.dropEvent = self._local_drop
        self.local_view.startDrag = self._local_start_drag
        self.local_view.keyPressEvent = self._local_key_press
        self.local_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_view.customContextMenuRequested.connect(self._local_context_menu)

        # Top bar: Up / Refresh / the drive switcher + the name filter
        # ("Search: … Filter by name…"), mirroring the classic sync local
        # explorer AND the SD Card tab's local pane, whose nav rows carry
        # their drive combo in exactly this slot.
        #
        # The drive combo used to be the NextSync tab's classic one, left
        # alone on a full-width row above the whole widget when the Remote
        # Explorer view took over: it stretched across the entire window,
        # cost a row of height, and pushed this pane out of line with the
        # Next pane beside it (reported). Owning one here puts it back in
        # the row it belongs to and lets that row go away entirely.
        local_up = CompactButton("Up", self)
        local_up.clicked.connect(self._local_up)
        local_refresh = CompactButton("Refresh", self, floor=72)
        local_refresh.setToolTip("Re-read the current local folder from disk")
        local_refresh.clicked.connect(self._local_refresh)
        self.local_drive_combo = QComboBox(self)
        self.local_drive_combo.setToolTip(
            "The local drive this pane is browsing.")
        for _letter in (local_drives or []):
            self.local_drive_combo.addItem(str(_letter))
        # One drive (or a POSIX host, where there is only "/") is nothing to
        # switch between, so the combo stays out of the way entirely.
        self.local_drive_combo.setVisible(
            self.local_drive_combo.count() > 1)
        self.local_drive_combo.activated.connect(self._on_local_drive_picked)
        self.local_filter_label = QLabel("Search: ", self)
        self.local_filter_edit = QLineEdit(self)
        self.local_filter_edit.setPlaceholderText("Filter by name...")
        self.local_filter_edit.setClearButtonEnabled(True)
        self.local_filter_edit.textChanged.connect(self._local_filter_changed)
        local_bar = QHBoxLayout()
        local_bar.setContentsMargins(0, 0, 0, 0)
        local_bar.addWidget(local_up)
        local_bar.addWidget(local_refresh)
        local_bar.addWidget(self.local_drive_combo)
        local_bar.addWidget(self.local_filter_label)
        local_bar.addWidget(self.local_filter_edit, 1)

        # Under the tree: the sync-root box + "Set current folder as new sync
        # root folder" button (same idea as classic sync's row). The box shows
        # the committed sync root; it no longer follows clicks or navigation in
        # the tree above. The button appears only while browsing a different
        # folder than the sync root and asks for confirmation before
        # committing. Typing a folder path and pressing Enter also commits.
        self.local_path_edit = QLineEdit(self)
        self.local_path_edit.setPlaceholderText("Sync root folder...")
        self.local_path_edit.setToolTip(
            "Sync root: the local folder the Remote Explorer works in.\n"
            "Type a folder path here, or navigate the explorer above and press\n"
            "'Set current folder as new sync root folder'.")
        self.local_path_edit.editingFinished.connect(self._on_path_edit)

        self.local_set_syncroot_button = QPushButton(
            "Set current folder as new sync root folder", self)
        self.local_set_syncroot_button.setToolTip(
            "Make the folder currently shown in the explorer above the new sync root.")
        self.local_set_syncroot_button.clicked.connect(self._on_set_syncroot_clicked)
        self.local_set_syncroot_button.setVisible(False)
        # Green attention pulse while the offer is on screen (started/stopped
        # by _update_set_syncroot_button, mirroring the classic sync view's
        # button — see zxnu_nextsync_pane).
        self._syncroot_pulse_timer = None

        local_path_row = QHBoxLayout()
        local_path_row.setContentsMargins(0, 0, 0, 0)
        local_path_row.addWidget(self.local_path_edit, 1)
        local_path_row.addWidget(self.local_set_syncroot_button)

        # Emulator strip (9.5.27): the session strip's mirror image, down
        # the OUTER left edge. Margin on the right only, so the tabs sit
        # flush with the pane's edge exactly as the session tabs do on
        # theirs. Hidden whenever no emulator is installed, so a machine
        # without one loses no width at all.
        self.local_emulator_strip = QWidget(self)
        self._emulator_strip_box = QVBoxLayout(self.local_emulator_strip)
        self._emulator_strip_box.setContentsMargins(0, 0, 3, 0)
        self._emulator_strip_box.setSpacing(4)
        self._emulator_strip_box.addStretch(1)   # tabs pile from the TOP
        self.local_emulator_strip.setVisible(False)
        self._emulator_tabs = []

        local_tree_row = QHBoxLayout()
        local_tree_row.setContentsMargins(0, 0, 0, 0)
        local_tree_row.setSpacing(0)
        local_tree_row.addWidget(self.local_emulator_strip, 0)
        local_tree_row.addWidget(self.local_view, 1)

        local_box = QVBoxLayout()
        local_box.setContentsMargins(0, 0, 0, 0)
        local_box.setSpacing(2)
        local_box.addLayout(local_bar)
        local_box.addLayout(local_tree_row)
        local_box.addLayout(local_path_row)
        local_container = QWidget(self)
        local_container.setLayout(local_box)

        # First run has no sync root: browse from home but leave the sync root
        # unset, so the host keeps the Start button disabled until the user picks
        # a folder. A saved path (SETTING_NEXTSYNC_EXPLORERPATH) is restored as
        # the sync root and enables Start straight away.
        if local_start_dir and os.path.isdir(local_start_dir):
            self._set_local_dir(local_start_dir, commit=True)
        else:
            self._set_local_dir(QDir.homePath(), commit=False)

        # ---- centre: transfer buttons -------------------------------------
        self.btn_to_next = QPushButton("->:", self)
        self.btn_to_next.setMaximumWidth(ARROW_BTN_W)
        self.btn_to_next.setToolTip("Upload the selected local file(s) to the Next folder (put)")
        self.btn_to_next.clicked.connect(self._put_selected)

        self.btn_to_local = QPushButton(":<-", self)
        self.btn_to_local.setMaximumWidth(ARROW_BTN_W)
        self.btn_to_local.setToolTip("Download the selected Next item(s) to the local folder (get)")
        self.btn_to_local.clicked.connect(self._get_selected)

        # QTimer driving the soft green "breathing" glow on the two transfer
        # arrow buttons while the Remote Explorer view is visible, mirroring the
        # SD Card tab's transfer-arrow pulse (_start_transfer_idle_animation).
        # Started/stopped from showEvent/hideEvent; None while stopped.
        self._arrow_pulse_timer = None

        centre_box = QVBoxLayout()
        centre_box.setAlignment(Qt.AlignCenter)
        centre_box.addWidget(self.btn_to_next)
        centre_box.addWidget(self.btn_to_local)
        centre_container = QWidget(self)
        centre_container.setLayout(centre_box)

        # ---- right: Next file explorer ------------------------------------
        self._dir_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self.next_model = QStandardItemModel(self)
        self.next_model.setHorizontalHeaderLabels(["Name", "Type", "Size"])
        self.next_view = QTreeView(self)
        self.next_view.setModel(self.next_model)
        self.next_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.next_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.next_view.setUniformRowHeights(True)
        self.next_view.setRootIsDecorated(False)
        self.next_view.setColumnWidth(0, 250)
        # Ctrl-A selects the listing's contents, never the ".." parent row.
        bind_select_all_except_updir(
            self.next_view, lambda ix: ix.data(RE_PATH_ROLE) == "..")
        # The Next model is rebuilt on every listing, so instead of Qt's view sort
        # (which would sort the Size column as text and unpin "..") we sort the
        # entries ourselves in _rebuild_next_rows and just drive the header: make
        # the sections clickable and show the indicator. sectionClicked toggles /
        # switches the sort; the saved one is shown via the indicator.
        next_header = self.next_view.header()
        next_header.setSectionsClickable(True)
        next_header.setSortIndicatorShown(True)
        next_header.sectionClicked.connect(self._on_next_header_clicked)
        self._apply_next_sort_indicator()
        self.next_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.next_view.customContextMenuRequested.connect(self._next_context_menu)
        self.next_view.doubleClicked.connect(self._next_double_clicked)
        self.next_view.setAcceptDrops(True)
        self.next_view.setDragEnabled(True)
        self.next_view.setDropIndicatorShown(True)
        self.next_view.dragEnterEvent = self._next_drag_enter
        self.next_view.dragMoveEvent = self._next_drag_enter
        self.next_view.dragLeaveEvent = self._pane_drag_leave
        self.next_view.dropEvent = self._next_drop
        self.next_view.startDrag = self._next_start_drag
        self.next_view.keyPressEvent = self._next_key_press

        # Idle-status provider: the host app can plug in a callable that
        # returns the "Next: …" text to show while DISCONNECTED, so the
        # label reflects the real setup stage ("Select a sync root folder",
        # "Start NextSync server", "waiting for .sync5 -listen…") instead
        # of always claiming to be waiting for the dot.
        self._idle_status_provider = None
        # Idle-DETAILS provider: a second host callable whose multi-line text
        # (the host/IP block the Classic log prints) is shown INSIDE the empty
        # Next pane while disconnected — the address the user must give
        # '.sync5' is the one thing they need while setting the link up.
        self._idle_details_provider = None
        self._idle_info_overlay = None
        # Style for the idle-details overlay's text; the host pushes the
        # user's retro-log (Consolas) COLOUR through set_idle_details_style
        # so it follows the setting live. The size stays at the pane's
        # normal 10pt — inheriting the log font size blew the block up until
        # it overlapped the pane's header and footer (reverted).
        self._idle_info_color = "#a6f0a6"
        self._idle_info_pt = 10
        # Top-bar label: the idle status while disconnected, the drive's free
        # space while connected. The PATH itself lives in next_path_edit at
        # the bottom of the pane since the UX pass that aligned it with the
        # local pane's sync-root box (both panes: bar / tree / path row).
        self.next_path_label = QLabel("Next: (not connected)", self)
        next_up = CompactButton("Up", self)
        next_up.clicked.connect(self._next_up)
        refresh = CompactButton("Refresh", self, floor=72)
        refresh.clicked.connect(self.refresh)
        # Drive switcher: populated from the dot's getdrives reply on connect
        # (e.g. C and M). Disabled until the drives are known; stays a single
        # "C" with an explanatory tooltip when the dot predates getdrives.
        self.next_drive_combo = QComboBox(self)
        self.next_drive_combo.setToolTip(
            "Next drive to browse (from '.sync5 -L'). Switching drives "
            "jumps to that drive's root; all transfers and file operations "
            "then target it. The Next reports C, M and its current drive; "
            "use + to add drives from extra SD readers/partitions.")
        self.next_drive_combo.setEnabled(False)
        self.next_drive_combo.currentTextChanged.connect(self._on_drive_changed)
        # "+": declare an extra drive letter the dot cannot discover on its
        # own (additional SD card readers / partitions). Only ever probed by
        # the USER switching to it - see _add_drive_clicked.
        self.next_drive_add = CompactButton("+ Drive", self, floor=64)
        self.next_drive_add.setToolTip(
            "Add a Next drive letter (D..P) for an additional SD card "
            "reader/partition the Next cannot report by itself. The drive is "
            "remembered and offered automatically next time.")
        self.next_drive_add.setEnabled(False)
        self.next_drive_add.clicked.connect(self._add_drive_clicked)
        # Machine switcher (multi-Next, option B): WHICH connected Next this
        # pane drives. Hidden until a second Next joins; fed by the worker's
        # peers signal (on_peers). Every other connected Next stays on the
        # line, idling, and keeps its place.
        self._peer_active = None
        self._peer_map = []            # [(sid, addr)] — the last roster; MUST
                                       # exist from birth: on_connected reads
                                       # it via _active_addr, and only the
                                       # live app guarantees a roster arrived
                                       # first (the widget test calls
                                       # on_connected directly — the missing
                                       # init was CI failure #31717960490)
        self._peer_guard = False
        self.next_machine_combo = QComboBox(self)
        self.next_machine_combo.setToolTip(
            "Which connected Next this pane drives. Other connected Nexts "
            "stay on the line (idling) and keep their place; switching "
            "re-reads the chosen machine's drives and listing.")
        self.next_machine_combo.setVisible(False)
        self.next_machine_combo.activated.connect(self._on_machine_pick)
        # Re-measure on EVERY content change, not just the first show (the
        # Qt default): a freshly assigned " - <name>" must widen the combo
        # right away, not sit clipped until the next restart (9.5.18 field
        # report).
        self.next_machine_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        # The small round ✎ next to the machine combo (9.5.18): name the
        # machine the combo shows. Hidden/shown together with the combo.
        self.next_machine_name_btn = QPushButton("✎", self)
        self.next_machine_name_btn.setFixedSize(18, 18)
        self.next_machine_name_btn.setStyleSheet(
            "QPushButton { border-radius: 9px; border: 1px solid #888; }")
        self.next_machine_name_btn.setToolTip(
            "Give this Next a friendly name (shown in the machine list, "
            "remembered for its address across sessions). Empty removes "
            "the name.")
        self.next_machine_name_btn.setVisible(False)
        self.next_machine_name_btn.clicked.connect(self._on_machine_name_edit)
        # Disconnect (9.5.24): the HTTP bridge's /forceexit as a button —
        # tell the Next this pane drives to leave listen mode AND end its
        # application. Sits between the machine's name and its drive
        # because that is the row that identifies the machine: everything
        # left of it says WHICH Next, everything right of it says what is
        # on it. Enabled only while connected (see _set_connected), and
        # never a broadcast — the marked quit reaches the driven seat
        # alone, so other connected Nexts keep their place.
        self.btn_disconnect = CompactButton("Disconnect", self, floor=72)
        self.btn_disconnect.setToolTip(
            "Tell the Next shown here to leave listen mode and exit its "
            "application (the HTTP bridge's /forceexit). The server keeps "
            "listening, so the same machine — or another — can connect "
            "again straight away.")
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self._disconnect_peer)
        next_bar = QHBoxLayout()
        next_bar.setContentsMargins(0, 0, 0, 0)
        next_bar.addWidget(next_up)
        next_bar.addWidget(refresh)
        next_bar.addWidget(self.next_machine_combo)
        next_bar.addWidget(self.next_machine_name_btn)
        next_bar.addWidget(self.btn_disconnect)
        next_bar.addWidget(self.next_drive_combo)
        next_bar.addWidget(self.next_drive_add)
        next_bar.addWidget(self.next_path_label, 1)

        # The session strip (9.5.25): one SessionTab per connected Next,
        # down the pane's outer edge, shown only when there are at least
        # TWO machines — with one there is nothing to switch between and
        # the combo already names it. Rebuilt from the roster, so it is
        # always the same truth the combo shows.
        self.next_session_strip = QWidget(self)
        self._session_strip_box = QVBoxLayout(self.next_session_strip)
        self._session_strip_box.setContentsMargins(3, 0, 0, 0)
        self._session_strip_box.setSpacing(4)
        self._session_strip_box.addStretch(1)   # tabs pile from the TOP
        self.next_session_strip.setVisible(False)
        self._session_tabs = []

        next_tree_row = QHBoxLayout()
        next_tree_row.setContentsMargins(0, 0, 0, 0)
        next_tree_row.setSpacing(0)
        next_tree_row.addWidget(self.next_view, 1)
        next_tree_row.addWidget(self.next_session_strip, 0)

        next_box = QVBoxLayout()
        next_box.setContentsMargins(0, 0, 0, 0)
        next_box.setSpacing(2)
        next_box.addLayout(next_bar)
        next_box.addLayout(next_tree_row)

        # Under the tree, mirroring the local pane's sync-root row: the Next
        # path box (type a folder, ENTER lists it) with the New Folder /
        # Rename / Delete toolbar to its right.
        self.next_path_edit = QLineEdit(self)
        self.next_path_edit.setPlaceholderText("Next folder...")
        self.next_path_edit.setToolTip(
            "The Next folder shown above. Type a path (e.g. /games or "
            "m:/data) and press Enter to jump to it.")
        self.next_path_edit.setEnabled(False)
        self.next_path_edit.returnPressed.connect(self._on_next_path_edit)
        self.btn_new_folder = QPushButton("New Folder", self)
        self.btn_new_folder.clicked.connect(self._new_folder)
        self.btn_rename = QPushButton("Rename", self)
        self.btn_rename.clicked.connect(self._rename_selected)
        self.btn_delete = QPushButton("Delete", self)
        self.btn_delete.clicked.connect(self._delete_selected)
        next_tools = QHBoxLayout()
        next_tools.setContentsMargins(0, 0, 0, 0)
        next_tools.addWidget(self.next_path_edit, 1)
        next_tools.addWidget(self.btn_new_folder)
        next_tools.addWidget(self.btn_rename)
        next_tools.addWidget(self.btn_delete)
        next_box.addLayout(next_tools)
        next_container = QWidget(self)
        next_container.setLayout(next_box)
        # Kept for the background-copy overlay: while an rcpy continues in the
        # background the whole right pane is disabled and covered by a label.
        self.next_container = next_container
        next_container.installEventFilter(self)   # keep the overlay sized

        # ---- assemble the 3-column grid -----------------------------------
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(local_container, 0, 0)
        grid.addWidget(centre_container, 0, 1)
        grid.addWidget(next_container, 0, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(0, 1)

        self._set_connected(False)

    # ==================================================================
    #  transfer-arrow "breathing" pulse (mirrors the SD Card tab)
    # ==================================================================
    def showEvent(self, event):
        # The view became visible (Remote Explorer sub-view selected and the
        # NextSync tab in front): start the green pulse, exactly like the SD
        # Card tab kicks its transfer-arrow glow when it becomes the active tab.
        super().showEvent(event)
        self._start_arrow_pulse()
        # Emulator detection may have changed while this pane was on
        # another tab (an itch.io install finishing, the startup scan
        # landing). The host refreshes the strip at each of those points
        # too; this is the safety net, and rebuilding a list of at most
        # two tabs is cheaper than tracking who missed a notification.
        self.refresh_emulator_strip()

    def hideEvent(self, event):
        # Hidden (returned to classic sync, or switched to another tab): stop
        # the pulse and restore the buttons' normal look.
        super().hideEvent(event)
        self._stop_arrow_pulse()

    def _start_arrow_pulse(self):
        """Start a soft, continuously-looping green 'breathing' background pulse
        on the two transfer-arrow buttons (Send '->:' / Get ':<-'), matching the
        SD Card tab's _start_transfer_idle_animation. The pulse rewrites each
        button's background colour on a QTimer (always visible, unlike a
        QGraphicsEffect, and it leaves the tiny arrow text untouched). Safe to
        call repeatedly; it restarts cleanly."""
        self._stop_arrow_pulse()

        # Triangle wave over (2*steps) ticks -> a smooth fade up and down. The
        # two buttons are offset by half a cycle so they breathe out of phase.
        steps = 22
        phase = {"n": 0}

        def _alpha_for(pos):
            pos %= (2 * steps)
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            return int(150 * tri)

        def _tick():
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            for btn, off in ((self.btn_to_next, 0), (self.btn_to_local, steps)):
                a = _alpha_for(phase["n"] + off)
                try:
                    btn.setStyleSheet(
                        "QPushButton { "
                        f"background-color: rgba(46,204,113,{a}); "
                        f"border: 1px solid rgba(46,204,113,{min(a + 60, 255)}); "
                        "border-radius: 4px; }")
                except RuntimeError:
                    pass

        timer = QTimer(self)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        self._arrow_pulse_timer = timer

    def _stop_arrow_pulse(self):
        """Stop the breathing pulse and restore the buttons' normal appearance."""
        timer = getattr(self, "_arrow_pulse_timer", None)
        if timer is not None:
            timer.stop()
            self._arrow_pulse_timer = None
        for btn in (self.btn_to_next, self.btn_to_local):
            try:
                btn.setStyleSheet("")
            except RuntimeError:
                pass

    # ==================================================================
    #  command queue  (counts toward the running operation, if any)
    # ==================================================================
    def _enqueue(self, cmd):
        # Every remote command except a plain refresh (ls) is enqueued here.
        # While an operation runs, refresh() is suppressed, so anything queued
        # during one is genuine operation work and counts toward its progress.
        if self._op_active:
            self._op_total += 1
        self._enqueue_raw(cmd)

    # ==================================================================
    #  operation progress / blocking / cancel
    # ==================================================================
    def _run_op(self, title, enqueue_fn, determinate=True, toast_mkdir_fail=False,
                on_done=None, background_label=None, quiet_failures=False,
                bytes_total=0, files_total=0, quiet_ui=False):
        """Run a batch of remote commands as a cancellable, blocking operation.

        ``enqueue_fn`` queues the commands (via _enqueue). The widget is
        disabled and, after a short delay, a modal progress dialog appears; both
        are lifted once every queued command has reported back. ``toast_mkdir_fail``
        opts a single deliberate mkdir (New Folder) into failure toasts.
        ``on_done`` (optional) is called on the UI thread when the operation
        ends, as ``on_done(ok, failures)`` — see _end_operation.

        ``background_label`` replaces the dialog's Cancel button: pressing it
        does NOT cancel — it closes the dialog and lets the operation finish in
        the background with only the Next pane blocked (used by rcpy, whose
        in-flight Next-side copy cannot be stopped). ``quiet_failures`` keeps
        failures out of the end-of-op toast (the paste precheck treats a failed
        rfsize as "size unknown", not something to alarm about).
        ``bytes_total``/``files_total`` (from that precheck) arm the
        heartbeat-driven progress percentage — see on_op_progress.
        ``quiet_ui`` runs the operation WITHOUT disabling the widget and
        without the progress dialog (the drag staging download: the drag
        gesture and both panes must stay alive while it runs). The queue
        accounting is unchanged, and it stays safe with the panes enabled
        because every user entry point either goes through this method's
        no-nesting guard or through refresh(), which no-ops during an op.
        """
        if self._op_active or not self._connected or self._precheck is not None:
            # Never nest, never start without a live server (with no queue the
            # commands would silently vanish and the op could never end), and
            # never start while a paste precheck is still waiting for its
            # free-space reply — its evaluation launches the rcpy op itself.
            # (The precheck's own stage-1 op sets _precheck inside enqueue_fn,
            # after this guard has passed.)
            return
        self._op_active = True
        self._op_total = 0
        self._op_completed = 0
        self._op_cancelled = False
        self._op_determinate = determinate
        self._op_title = title
        self._op_dialog = None
        self._op_failures = []
        self._op_toast_mkdir = toast_mkdir_fail
        self._op_on_done = on_done
        self._op_background_label = background_label
        self._op_background = False
        self._op_quiet_failures = quiet_failures
        self._op_quiet_ui = quiet_ui
        self._op_bytes_total = int(bytes_total or 0)
        self._op_files_total = int(files_total or 0)
        self._op_bytes_est = 0
        self._op_files_seen = 0
        self._op_last_name = ""
        if not quiet_ui:
            # Any real operation may WRITE what a cached drag stage holds
            # pre-write copies of, so the reuse cache dies with the op start.
            self._invalidate_drag_stage()
            self.setEnabled(False)       # make the whole explorer unclickable
            # Delay the dialog so instant operations (a quick mkdir/rename)
            # don't flash a modal box on screen.
            QTimer.singleShot(250, self._show_op_dialog_if_running)
        enqueue_fn()
        if self._op_total == 0:          # nothing actually queued
            self._end_operation()

    def _busy_refused(self):
        """True when an operation (or the paste precheck) is still running —
        the caller must then do NOTHING and return. Quiet staging ops (a
        drag's background download) leave the panes ENABLED, so user actions
        can now arrive mid-op where the disabled widget used to make them
        impossible; _run_op would refuse those silently, so this guard says
        why up front — BEFORE a confirm dialog is shown, a cut clipboard is
        consumed, or a drop is accepted."""
        if self._op_active or self._precheck is not None:
            self._log("Busy - another operation is still running; "
                      "try again when it finishes.")
            return True
        return False

    def _show_op_dialog_if_running(self):
        # _op_quiet_ui also guards a stale timer here: a fast NON-quiet op can
        # end and a quiet one start inside the same 250 ms window.
        if (not self._op_active or self._op_dialog is not None
                or self._op_background or self._op_quiet_ui):
            return
        dlg = HdfProgressDialog(self._op_title, self.window(),
                                cancel_label=(self._op_background_label or "Cancel"))
        dlg.cancel_requested.connect(self._on_op_cancel_clicked)
        dlg.set_progress(0 if self._op_determinate else -1)
        dlg.set_status("Transfer/Operation in progress…")
        self._op_dialog = dlg
        dlg.show()
        self._update_op_progress()

    def _op_step_done(self, label=None):
        """One queued command reported back (success or failure)."""
        if not self._op_active:
            return
        self._op_completed += 1
        if label and self._op_dialog is not None:
            self._op_dialog.set_status(f"Transfer/Operation in progress…\n{label}")
        self._update_op_progress()
        if self._op_completed >= self._op_total:
            self._end_operation()

    def _op_hb_percent(self):
        """Heartbeat-driven percentage, or None when the op isn't armed with
        precheck totals. The max of three estimators — 64 KB per in-file
        keepalive vs total bytes (right for a few big files), files started vs
        total files (right for many small files), commands completed vs queued
        (a coarse floor) — capped at 99 until the op really ends."""
        if not (self._op_bytes_total or self._op_files_total):
            return None
        bp = (100 * self._op_bytes_est // self._op_bytes_total) \
            if self._op_bytes_total else 0
        fp = (100 * self._op_files_seen // self._op_files_total) \
            if self._op_files_total else 0
        cp = (100 * self._op_completed // self._op_total) if self._op_total else 0
        return min(99, max(bp, fp, cp))

    def on_op_progress(self, op, name):
        """A 'D' heartbeat arrived while a long command runs (rcpy: named =
        one file copy starting, empty = 64 KB copied inside the current file;
        rfsize: named = the directory now being scanned). Drives the progress
        dialog — and the background overlay — instead of leaving the bar at 0%
        for the whole copy."""
        if not self._op_active:
            return
        if name:
            self._op_files_seen += 1
            self._op_last_name = name
            if self._op_dialog is not None:
                self._op_dialog.set_status(f"{self._op_title}\n{name}")
        else:
            self._op_bytes_est += 65536
        self._update_op_progress()

    def _update_op_progress(self):
        if self._op_background:
            self._update_next_overlay_text()
            return
        if self._op_dialog is None:
            return
        pct = self._op_hb_percent()
        if pct is not None:
            self._op_dialog.set_progress(pct)
            return
        if not self._op_determinate:
            return
        if self._op_total > 0:
            self._op_dialog.set_progress(
                int(100 * self._op_completed / self._op_total))

    def _on_op_cancel_clicked(self):
        # The dialog's single button: a real Cancel for most operations, or
        # "Close this window and continue in the background" for rcpy.
        if self._op_background_label:
            self._op_background_now()
        else:
            self._on_op_cancel()

    def _on_op_cancel(self):
        # Stop after the current file: drop everything still queued (the in-flight
        # transfer finishes on its own so nothing is left half-written), and don't
        # delete any further move sources.
        if not self._op_active or self._op_cancelled:
            return
        self._op_cancelled = True
        drained = self._drain_raw() if self._drain_raw else 0
        # Drained commands will never report back, so count them as done.
        self._op_completed += int(drained or 0)
        self._cut_jobs.clear()
        self._log("Cancelling remote operation after the current transfer…")
        # The dialog froze its status at a bare "Cancelling…" when the button
        # was pressed (set_status is muted once cancelled), so spell out WHAT
        # the wait is for: the in-flight file must land whole on the Next.
        if self._op_dialog is not None:
            self._op_dialog.set_cancel_note(ui_tr_now(
                "Cancelling — will stop once the current file has finished "
                "transferring, to avoid file corruption…"))
        # If nothing was in flight, we're already finished.
        if self._op_completed >= self._op_total:
            self._end_operation()

    # ---- background mode: dialog closed, only the Next pane blocked ----
    def _op_background_now(self):
        """Close the progress dialog and let the operation finish in the
        background: the local pane becomes usable again, the Next pane is
        covered by a "copy in progress" overlay until the op ends (a Next-side
        rcpy cannot actually be interrupted, so this is the honest offer)."""
        if not self._op_active or self._op_background:
            return
        self._op_background = True
        if self._op_dialog is not None:
            self._op_dialog.close()
            self._op_dialog = None
        self.setEnabled(True)
        self._update_next_overlay_text()
        self._log("Remote copy continues in the background; the Next pane "
                  "unlocks when it completes.")

    def _update_next_overlay_text(self):
        pct = self._op_hb_percent()
        text = "Remote copy in progress…"
        if pct is not None:
            text += f"  {pct}%"
        text += "\nplease wait"
        if self._op_last_name:
            text += "\n\n" + self._op_last_name
        self._set_next_overlay(text)

    def _set_next_overlay(self, text):
        """Cover (text) or free (None) the whole Next pane. The overlay both
        says what is going on and swallows interaction; the pane's widgets are
        disabled underneath it for good measure."""
        if text is None:
            if self._next_overlay is not None:
                self._next_overlay.deleteLater()
                self._next_overlay = None
                self.next_container.setEnabled(True)
            return
        self.next_container.setEnabled(False)
        if self._next_overlay is None:
            lbl = QLabel(self.next_container)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "QLabel { background-color: rgba(15, 15, 15, 175);"
                " color: #ffd54a; font-weight: bold; font-size: 13pt;"
                " border-radius: 8px; }")
            lbl.setGeometry(self.next_container.rect())
            lbl.show()
            self._next_overlay = lbl
        self._next_overlay.setText(text)

    def eventFilter(self, obj, event):
        # Keep the background-copy and idle-details overlays covering the
        # Next pane through resizes / splitter drags.
        if (obj is getattr(self, "next_container", None)
                and event.type() == QEvent.Type.Resize):
            if self._next_overlay is not None:
                self._next_overlay.setGeometry(self.next_container.rect())
            if self._idle_info_overlay is not None:
                self._idle_info_overlay.setGeometry(self.next_container.rect())
        return super().eventFilter(obj, event)

    def _end_operation(self):
        was_bg = self._op_background
        self._op_background = False
        self._op_background_label = None
        self._op_active = False
        if self._op_dialog is not None:
            self._op_dialog.close()
            self._op_dialog = None
        self._set_next_overlay(None)
        self.setEnabled(True)
        # Tell the user about any Next-side failures this operation hit (one toast
        # for the whole batch). A user cancel is expected, so don't cry failure.
        fails, self._op_failures = self._op_failures, []
        if fails and not self._op_cancelled and not self._op_quiet_failures:
            self._toast_failures(fails)
        # A backgrounded operation has no dialog left to announce its end, so
        # the outcome arrives as a toast (failures already toasted red above).
        if was_bg and not self._op_cancelled:
            if not self._connected:
                self._on_toast("⚠  Remote copy interrupted",
                               "The connection to the Next ended before the "
                               "copy finished; its state is unknown.", "red")
            elif not fails:
                n = self._op_completed
                self._on_toast("✅  Remote copy complete",
                               ui_tr_now("Copied {n} item(s) on the Next.")
                               .format(n=n), "green")
        # Report the batch outcome to an interested caller (see send_local_paths).
        # ok requires every queued command to have reported back with no failure
        # and no cancel; a mid-batch disconnect ends the op early with
        # _connected already False, so it can never masquerade as success.
        cb, self._op_on_done = getattr(self, "_op_on_done", None), None
        if cb is not None:
            ok = (self._connected and not self._op_cancelled and not fails
                  and self._op_completed >= self._op_total)
            try:
                cb(ok, fails)
            except Exception:
                pass
        # One listing now that the batch is done (suppressed during the op).
        self.refresh()
        # Transfers/deletes changed the drive's fill level: re-read it so the
        # free-space figure in the path label stays honest.
        self._query_free()

    # ==================================================================
    #  failure reporting  (toast the user, with context)
    # ==================================================================
    def _record_op_failure(self, desc):
        """Note one failed command during the running operation (deduped, capped),
        to be toasted as a summary when the operation ends."""
        if desc and desc not in self._op_failures and len(self._op_failures) < 100:
            self._op_failures.append(desc)

    def _toast_failures(self, fails):
        """Toast a summary of the failures collected during an operation."""
        shown = fails[:5]
        body = "\n".join(shown)
        if len(fails) > len(shown):
            body += "\n" + ui_tr_now("…and {n} more").format(
                n=len(fails) - len(shown))
        title = ("A Next operation failed" if len(fails) == 1
                 else ui_tr_now("{n} Next operations failed").format(
                     n=len(fails)))
        self._on_toast(title, body, "red")

    # ==================================================================
    #  connection state
    # ==================================================================
    def _idle_status_text(self):
        if self._idle_status_provider is not None:
            try:
                text = self._idle_status_provider()
                if text:
                    return text
            except Exception:
                pass
        return "Next: (waiting for .sync5 -L (-l or -listen) …)"

    def set_idle_status_provider(self, provider):
        """Install the host's disconnected-state status callable (0-arg,
        returns the full "Next: …" text) and apply it right away."""
        self._idle_status_provider = provider
        self.refresh_idle_status()

    def set_idle_details_provider(self, provider):
        """Install the host's disconnected-state DETAILS callable (0-arg,
        returns the multi-line host/IP block for the empty Next pane, "" for
        none) and apply it right away."""
        self._idle_details_provider = provider
        self._update_idle_info_overlay()

    def _update_idle_info_overlay(self):
        """Show the idle-details text over the (empty, disabled) Next pane
        while disconnected — on the animated 8-bit starfield
        (IdleStarfieldOverlay); remove it when connected or without text."""
        text = ""
        if not self._connected and self._idle_details_provider is not None:
            try:
                text = self._idle_details_provider() or ""
            except Exception:
                text = ""
        if not text:
            if self._idle_info_overlay is not None:
                self._idle_info_overlay.deleteLater()
                self._idle_info_overlay = None
            return
        if self._idle_info_overlay is None:
            ov = IdleStarfieldOverlay(self.next_container)
            ov.set_label_stylesheet(self._idle_info_stylesheet())
            ov.setGeometry(self.next_container.rect())
            ov.show()
            ov.raise_()
            self._idle_info_overlay = ov
        self._idle_info_overlay.setText(text)

    def _idle_info_stylesheet(self):
        return ("QLabel { background: transparent;"
                f" color: {self._idle_info_color};"
                " font-family: Consolas, 'Courier New', monospace;"
                f" font-size: {self._idle_info_pt}pt; }}")

    def set_idle_details_style(self, color=None, point_size=None):
        """Restyle the idle-details overlay (the host pushes the retro-log
        Consolas colour and font-size settings here, live)."""
        if color:
            self._idle_info_color = color
        if point_size:
            self._idle_info_pt = int(point_size)
        if self._idle_info_overlay is not None:
            self._idle_info_overlay.set_label_stylesheet(
                self._idle_info_stylesheet())

    def refresh_idle_status(self):
        """Re-evaluate the idle status (host state changed: sync root set,
        server started/stopped). No-op while connected."""
        if not self._connected:
            self.next_path_label.setText(self._idle_status_text())
        self._update_idle_info_overlay()

    def _set_connected(self, on):
        self._connected = on
        # Any connection transition invalidates the drag-stage reuse cache:
        # after a disconnect/reconnect (or the multi-Next baton move, which
        # is _set_connected(False) + on_connected) the same remote path can
        # name DIFFERENT content — even another machine's SD card.
        self._invalidate_drag_stage()
        for w in (self.btn_to_next, self.btn_to_local, self.btn_new_folder,
                  self.btn_rename, self.btn_delete, self.next_view,
                  self.next_path_edit, self.btn_disconnect):
            w.setEnabled(on)
        if not on:
            self.next_model.removeRows(0, self.next_model.rowCount())
            self.next_path_label.setText(self._idle_status_text())
            self.next_path_label.setToolTip("")
            self.next_path_edit.setText("")
            self.next_path_edit.setModified(False)
            self._free_space.clear()   # a reconnect re-reads it
            self._drives = []
            self._default_drive = ""
            self.next_drive_add.setEnabled(False)
            self._drive_combo_guard = True
            try:
                self.next_drive_combo.clear()
                self.next_drive_combo.setEnabled(False)
            finally:
                self._drive_combo_guard = False
        self._update_idle_info_overlay()

    # ---- worker signal slots (UI thread) ------------------------------
    def _active_addr(self):
        """The ACTIVE peer's address, from the worker's roster (multi-Next).
        None while no roster arrived — the worker emits the roster before
        connected, so on_connected always has it."""
        for _sid, _addr in self._peer_map:
            if _sid == self._peer_active:
                return _addr
        return None

    def on_connected(self):
        self._set_connected(True)
        # Jump straight back to the folder we were last browsing. Per-machine
        # first (9.5.14): THIS Next's remembered folder — keyed by its
        # address, fed by the host — wins; the generic last-folder covers a
        # machine never seen before. If the folder is gone, the listing
        # fails and on_ls_failed() drops us back to the root.
        _addr = self._active_addr()
        _saved = (self._remote_cwd_for(_addr)
                  if (_addr and self._remote_cwd_for) else None)
        self._cwd = _saved or self._remote_start_dir or "/"
        # Ask which drives are mounted (dot v5.1+) before the first listing so
        # the drive switcher fills in as the pane appears. RAW on purpose (the
        # _query_free rule): its reply emits no op_done, so counting it while
        # an operation runs — reachable since quiet staging ops stopped
        # disabling the panes, e.g. the multi-Next baton moving here mid-drag
        # — would leave the operation one step short FOREVER.
        self._enqueue_raw(("drives",))
        self.refresh()

    def on_disconnected(self):
        self._set_connected(False)
        # A pending paste precheck can never complete now.
        self._precheck = None
        # Abandon any in-flight moves: their transfers can't complete, so their
        # sources must stay put.
        if self._cut_jobs:
            self._log("Connection ended; unfinished moves kept their sources.")
            self._cut_jobs.clear()
        # A running operation can never report back now -- release the UI so the
        # window doesn't stay blocked.
        if self._op_active:
            self._log("Connection ended; stopped the running operation.")
            self._end_operation()
        # The roster died with the worker: forget it, so a fresh worker's
        # sids can never collide with stale ones (the auto-relisten spins
        # a new worker whose sequence restarts at 1).
        self._peer_map = []
        self._peer_active = None

    def on_peers(self, payload):
        """The worker's connected-Nexts roster (multi-Next, option B):
        ``(active_sid, [(sid, address), ...])``. Rebuilds the machine combo
        (hidden below two Nexts) and, when the ACTIVE session changed — a
        user pick answered, or the active Next left and the worker handed
        the baton on — drops the pane content and re-reads the new
        machine's drives and listing: what was on screen belonged to a
        different SD card."""
        try:
            active, plist = payload
        except (TypeError, ValueError):
            return
        prev = self._peer_active
        # The baton is leaving `prev` (a user pick answered, or that Next
        # just disconnected): persist the folder ON SCREEN under the OLD
        # machine's address RIGHT NOW, from the roster we still hold —
        # the listing-time save covers confirmed navigations, this covers
        # the departure itself, so a machine that leaves mid-browse still
        # comes back to where it was.
        if prev is not None and active != prev and self._connected:
            _old_addr = None
            for _sid, _addr in self._peer_map:
                if _sid == prev:
                    _old_addr = _addr
                    break
            if _old_addr:
                self._on_remote_cwd_changed(_norm_remote_dir(self._cwd),
                                            _old_addr)
        # KEEP the roster: _active_addr() answers from it, which is what
        # keys every per-machine folder save and restore. The assignment
        # must sit exactly here — after the departure-save (which resolves
        # the OLD active sid against the roster we held until now) and
        # before the baton-move reconnect below (whose on_connected looks
        # up the NEW active machine's folder).
        self._peer_map = [(sid, addr) for sid, addr in plist]
        self._peer_active = active
        self._peer_guard = True
        try:
            self.next_machine_combo.clear()
            for sid, addr in plist:
                self.next_machine_combo.addItem(
                    self._machine_label(sid, addr), sid)
                if sid == active:
                    self.next_machine_combo.setCurrentIndex(
                        self.next_machine_combo.count() - 1)
            # Visible whenever ANY Next is on the line (field request: a
            # lone machine still shows WHO the pane drives); hidden only
            # while the roster is empty. The ✎ name button rides along.
            self.next_machine_combo.setVisible(len(plist) >= 1)
            self.next_machine_name_btn.setVisible(len(plist) >= 1)
            self._apply_machine_colors()
        finally:
            self._peer_guard = False
        self._rebuild_session_strip()
        if active is not None and prev is not None and active != prev:
            # The baton moved: this pane now drives a DIFFERENT machine.
            self._set_connected(False)     # drop the old card's listing
            self.on_connected()            # drives + listing of the new one

    def refresh_emulator_strip(self):
        """Rebuild the left-hand emulator tabs from the host's detection.

        Public and idempotent: emulator detection is not a Qt signal in
        this app - it changes when the startup scan finishes, when an
        itch.io install or uninstall lands, when MAME is installed
        in-app, and when the Linux Flatpak toggle flips - so the host
        calls this at each of those points, and showEvent calls it as
        the safety net for anything that changed while the tab was away.

        Tabs are rebuilt rather than patched, exactly as the session
        strip does: the list is at most two entries and MAME's label can
        change under it (the "(flatpak)" suffix), so an in-place update
        would be the harder thing to get right.
        """
        for tab in self._emulator_tabs:
            self._emulator_strip_box.removeWidget(tab)
            tab.setParent(None)
            tab.deleteLater()
        self._emulator_tabs = []
        try:
            entries = list(self._emulator_launchers() or [])
        except Exception:                       # noqa: BLE001
            logging.exception("Remote explorer: emulator lookup failed")
            entries = []
        self.local_emulator_strip.setVisible(bool(entries))
        for i, item in enumerate(entries):
            entry = as_emulator_launch(item)
            name, launch, why = entry.name, entry.launch, entry.blocked
            tab = EmulatorTab(
                str(name), (lambda fn=launch: self._launch_emulator(fn)),
                self.local_emulator_strip,
                tooltip=(why or ui_tr_now("Start {emulator}").format(
                    emulator=name)),
                blocked=bool(why),
                tint=self._emulator_color(name),
                on_menu=(lambda pos, n=name: self._emulator_tab_menu(n, pos)))
            self._emulator_strip_box.insertWidget(i, tab)
            self._emulator_tabs.append(tab)

    def _emulator_color(self, name):
        """This emulator's picked colour as a QColor, or None.

        Forgiving in the same way _machine_color is: the host stores
        "#rrggbb" and anything unparseable is read as "no colour", so a
        hand-edited cfg can never make the strip unpaintable.
        """
        raw = self._emulator_color_for(name)
        if not raw:
            return None
        col = QColor(str(raw))
        return col if col.isValid() else None

    def _emulator_tab_menu(self, name, global_pos):
        """Right-click on an emulator tab: pick or reset its colour."""
        emulator_color_menu(
            self, str(name), self._emulator_color(name),
            (lambda hexval, n=name: self._on_emulator_color_changed(n, hexval)),
            global_pos)

    def _launch_emulator(self, fn):
        """Run one emulator launcher, called with NO arguments - the bare
        launch the SD Card tab's button makes. Deferred by a zero-timer so
        the click that started it has finished delivering first: the
        launcher disables and re-enables widgets around a blocking call,
        and doing that from inside a mouse handler is how a repaint gets
        skipped."""
        QTimer.singleShot(0, lambda: self._run_emulator_launcher(fn))

    def _run_emulator_launcher(self, fn):
        try:
            fn()
        except Exception:                       # noqa: BLE001
            logging.exception("Remote explorer: emulator launch failed")

    def _machine_color(self, addr):
        """This address's picked colour as a QColor, or None.

        The host stores "#rrggbb"; anything it cannot parse is treated as
        "no colour" rather than as an error - a hand-edited cfg must not
        be able to make the pane unpaintable.
        """
        raw = self._machine_color_for(addr)
        if not raw:
            return None
        col = QColor(str(raw))
        return col if col.isValid() else None

    def _apply_machine_colors(self):
        """Push the per-machine tints onto the machine combo.

        TWO surfaces, because the combo has two: the popup ENTRIES take
        Background/Foreground roles, and the CLOSED field - the part that
        is on screen the other 99% of the time, and the one the request
        was actually about - takes a widget stylesheet for the machine
        currently driven.

        The stylesheet restates the whole app-chrome combo rule
        (NEXT_CHROME_QSS in zxnu_config) rather than just the background:
        a partial widget rule leaves the app's border/padding in force and
        the two disagree about the frame. ``::drop-down`` is deliberately
        NOT restated - the chrome never styles it either, so the arrow
        keeps the platform's own rendering.
        """
        for i in range(self.next_machine_combo.count()):
            sid = self.next_machine_combo.itemData(i)
            addr = None
            for _sid, _addr in self._peer_map:
                if _sid == sid:
                    addr = _addr
                    break
            col = self._machine_color(addr) if addr else None
            if col is None:
                self.next_machine_combo.setItemData(i, None, RE_BG_ROLE)
                self.next_machine_combo.setItemData(i, None, RE_FG_ROLE)
            else:
                self.next_machine_combo.setItemData(
                    i, QBrush(col), RE_BG_ROLE)
                self.next_machine_combo.setItemData(
                    i, QBrush(readable_text_color(col)), RE_FG_ROLE)
        active_col = None
        _addr = self._active_addr()
        if _addr:
            active_col = self._machine_color(_addr)
        if active_col is None:
            self.next_machine_combo.setStyleSheet("")
            return
        self.next_machine_combo.setStyleSheet(
            "QComboBox {"
            f" background-color: {qcolor_to_hex(active_col)};"
            f" color: {qcolor_to_hex(readable_text_color(active_col))};"
            " border: 1px solid #4a4a8a; border-radius: 3px;"
            " padding: 2px 8px; }"
            "QComboBox:hover { border-color: #ff3cff; }")

    def _rebuild_session_strip(self):
        """Repaint the side tabs from the roster we hold.

        Called wherever the roster or a NAME changes, so the strip can
        never disagree with the combo — they are two renderings of one
        list. Tabs are rebuilt rather than patched: the roster is at most
        four entries (SESS_MAX), and a machine leaving mid-list would make
        an in-place update the harder thing to get right.
        """
        for tab in self._session_tabs:
            self._session_strip_box.removeWidget(tab)
            tab.setParent(None)
            tab.deleteLater()
        self._session_tabs = []
        show = len(self._peer_map) >= 2
        self.next_session_strip.setVisible(show)
        if not show:
            return
        for i, (sid, addr) in enumerate(self._peer_map):
            # The NAME if the user gave this address one, else the plain
            # address — the tab is for recognising a machine at a glance,
            # so the friendly name earns the space when it exists — with
            # the session id on the tail, the way the combo composes it
            # (session_label). The id is not decoration: one machine can
            # hold several '.sync5 -L' sessions at once, and now that a
            # MAME per disk image is a normal setup they arrive under the
            # SAME address and the SAME name — two identical "Mame" tabs
            # with nothing to tell them apart (reported). The tooltip
            # carries the full combo label so the address is one hover
            # away when the name took its place.
            label = (self._machine_name_for(addr) or "").strip() or str(addr)
            tab = SessionTab(f"{label} #{sid}", sid == self._peer_active,
                             (lambda s=sid: self._request_machine(s)),
                             self.next_session_strip,
                             tint=self._machine_color(addr),
                             on_menu=(lambda pos, s=sid:
                                      self._session_tab_menu(s, pos)))
            tab.setToolTip(self._machine_label(sid, addr))
            self._session_strip_box.insertWidget(i, tab)
            self._session_tabs.append(tab)

    def _machine_label(self, sid, addr):
        """One combo entry: "addr #sid", plus " - Name" when the host
        remembers a friendly name for that address. Delegates to the ONE
        composer (zxnu_http_bridge.session_label, stdlib-only) so the
        combo, GET /sessions and ZXNextRemote's title line can never
        drift apart."""
        return session_label(sid, addr, self._machine_name_for(addr) or "")

    def _disconnect_peer(self):
        """The Disconnect button: the DRIVEN Next (see _disconnect_session)."""
        if not self._connected:
            return
        self._disconnect_session(None)

    def _disconnect_session(self, sid=None):
        """Ask a Next to leave listen mode and end its application - the
        bridge's /forceexit, on a button (``sid=None``, the driven one) or
        from a session tab's right-click menu (``sid=<that machine>``).

        Sends the MARKED quit ('Q' + the exit marker, zxnu_workers): a bare
        quit is what a server SHUTTING DOWN sends, and the far side must be
        able to tell the two apart or stopping our own server would kill the
        operator's app. Fire-and-forget rather than a tracked operation -
        the command's whole point is that the peer stops answering, so there
        is no completion to wait for; the worker's disconnect signal repaints
        the pane when the link drops.

        A NAMED session is delivered on that session's OWN queue rather than
        the shared one, because the shared queue is drained by whoever holds
        the baton: routing a targeted quit through it would send the wrong
        machine away. That also keeps it off _enqueue, whose job is to count
        commands toward the RUNNING operation - an operation that belongs to
        a different Next entirely.
        """
        machine = self._machine_text_for(sid)
        if QMessageBox.question(
                self, ui_tr_now("Disconnect"),
                ui_tr_now("Tell this Next to leave listen mode and exit? "
                          "ZX Next Remote closes its application; a "
                          "'.sync5' dot returns to BASIC. The server keeps "
                          "listening, so it can connect again.")
                + (f"\n\n{machine}" if machine else ""),
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel) != QMessageBox.Yes:
            return
        if sid is None or sid == self._peer_active:
            # RAW, as the docstring above promises: fire-and-forget with no
            # completion reply, so it must never count toward a running
            # operation (a quiet staging op leaves this button clickable).
            self._enqueue_raw(("quit_app",))
        elif not self._enqueue_to(sid, ("quit_app",)):
            # That Next left between the right-click and the answer, or the
            # host wired no targeted channel. Either way nothing was sent,
            # and saying so beats a silent no-op.
            self._log(ui_tr_now("That Next is no longer on the line."))
            return
        self._log(ui_tr_now("Asked the Next to leave listen mode and exit."))

    def _enqueue_to(self, sid, cmd):
        """One command to a NAMED session's queue. False when the host wired
        no targeted channel, or the worker no longer knows that sid."""
        if self._enqueue_to_raw is None:
            return False
        try:
            return bool(self._enqueue_to_raw(sid, cmd))
        except Exception:                       # noqa: BLE001
            logging.exception("Remote explorer: targeted enqueue failed")
            return False

    def _machine_text_for(self, sid=None):
        """How to NAME a machine in a dialog: the combo's own label for it,
        falling back to the bare address. ``sid=None`` means the driven one,
        which is what the top Disconnect button has always shown."""
        if sid is None:
            machine = (self.next_machine_combo.currentText().strip()
                       if self.next_machine_combo.isVisible() else "")
            if machine:
                return machine
            _addr = self._active_addr()
            return str(_addr) if _addr else ""
        for _sid, _addr in self._peer_map:
            if _sid == sid:
                return self._machine_label(_sid, _addr)
        return ""

    def _session_tab_menu(self, sid, global_pos):
        """Right-click on a session tab: the per-machine actions.

        The top bar has always acted on the DRIVEN Next only - to name or
        disconnect a benched one you had to switch to it first, which is a
        listing round-trip for something you only wanted to name or send
        away. The strip already knows which machine was clicked, so every
        entry here acts on THAT one.

        Dialogs are raised after menu.exec() returns, so the menu's modal
        grab is already released (both view menus do the same).
        """
        addr = None
        for _sid, _addr in self._peer_map:
            if _sid == sid:
                addr = _addr
                break
        if addr is None:
            return                    # the machine left mid right-click
        menu = QMenu(self)
        act_switch = menu.addAction(ui_tr_now("Switch to this Next"))
        act_switch.setEnabled(sid != self._peer_active)
        act_name = menu.addAction(ui_tr_now("Name and color…"))
        menu.addSeparator()
        act_disc = menu.addAction(ui_tr_now("Disconnect"))
        chosen = menu.exec(global_pos)
        if chosen == act_switch:
            self._request_machine(sid)
        elif chosen == act_name:
            self._edit_machine_identity(addr)
        elif chosen == act_disc:
            self._disconnect_session(sid)

    def _on_machine_name_edit(self):
        """The ✎ button: name/colour the machine the combo currently shows."""
        ix = self.next_machine_combo.currentIndex()
        sid = self.next_machine_combo.itemData(ix)
        addr = None
        for _sid, _addr in self._peer_map:
            if _sid == sid:
                addr = _addr
                break
        if not addr:
            return
        self._edit_machine_identity(addr)

    def _edit_machine_identity(self, addr):
        """Name (or rename) and colour ONE machine.

        Both are keyed by the machine's ADDRESS and persisted by the host,
        so they survive reconnects and restarts whatever session id the
        machine is dealt next. An empty name removes it; a cleared colour
        removes the tint. Reached from the ✎ button (the driven machine)
        and from a session tab's right-click menu (any machine on the
        line) - one editor, so the two entry points cannot drift.
        """
        dlg = MachineIdentityDialog(
            addr, self._machine_name_for(addr) or "",
            self._machine_color(addr), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, color = dlg.result_values()
        # Collapse whitespace and cap the length: the combo shares its row
        # with the drive switcher, and " - <name>" must leave it room.
        name = " ".join(str(name).split())[:24].strip()
        self._on_machine_name_changed(addr, name)
        self._on_machine_color_changed(
            addr, qcolor_to_hex(color) if color is not None else "")
        # Relabel in place - every session of that address adopts the name
        # (two '.sync5 -L' sessions from one machine share one entry name).
        for i in range(self.next_machine_combo.count()):
            _s = self.next_machine_combo.itemData(i)
            for _sid, _addr in self._peer_map:
                if _sid == _s and _addr == addr:
                    self.next_machine_combo.setItemText(
                        i, self._machine_label(_s, _addr))
                    break
        self._apply_machine_colors()
        # The side tabs show the NAME when there is one and now the COLOUR
        # too, so an edit has to reach them - otherwise the tab would still
        # read the bare address the user just replaced.
        self._rebuild_session_strip()

    def _on_machine_pick(self, index):
        """User picked a Next in the machine combo: hand the baton over.
        The switch is applied by the worker and confirmed back through the
        peers signal, which is where the refresh happens — this only sends
        the request (and refuses it mid-operation: the commands still
        queued belong to the machine they were built for)."""
        if self._peer_guard:
            return
        self._request_machine(self.next_machine_combo.itemData(index))

    def _request_machine(self, sid):
        """Ask the worker to hand the baton to `sid` — the one path both
        switchers take, so the combo and the session strip can never grow
        different rules about when a switch is allowed."""
        if sid is None or sid == self._peer_active:
            return
        if self._op_active or self._precheck is not None:
            self._log("Finish the running operation before switching Next.")
            self._peer_guard = True
            try:
                for i in range(self.next_machine_combo.count()):
                    if (self.next_machine_combo.itemData(i)
                            == self._peer_active):
                        self.next_machine_combo.setCurrentIndex(i)
                        break
            finally:
                self._peer_guard = False
            return
        self._enqueue_raw(("select_next", sid))

    def on_drives(self, current, letters):
        """getdrives result: ``current`` is the dot's default drive letter and
        ``letters`` the drives it vouches for — always {C, M, current}; the
        dot can never PROBE other letters (a file call on an unmounted drive
        crashes a dotN). Both empty when the dot predates the command (pre
        v5.1) — then the switcher shows a lone entry for the drive in use and
        stays disabled, and every path keeps riding the dot's current drive
        exactly as before. User-declared extra drives (additional SD readers)
        are merged in from the saved config."""
        self._default_drive = (current or "").strip().upper()[:1]
        self._drives = sorted({(l or "").strip().upper()[:1]
                               for l in (letters or []) if (l or "").strip()})
        if self._drives and self._default_drive not in self._drives:
            # Distrust a current-drive letter that isn't in the reported list
            # (a mis-decoded M_GETDRV byte must not become a bogus combo entry).
            self._default_drive = "C" if "C" in self._drives else self._drives[0]
        if self._drives:
            self._log("Next drives: " + " ".join(self._known_drives())
                      + (f" (current: {self._default_drive})"
                         if self._default_drive else "")
                      + " — use '+ Drive' to add extra SD reader/partition "
                        "letters (they are remembered).")
        self._rebuild_drive_combo()
        # Ask for the current drive's free space (dot v5.2+; older dots
        # degrade with a log line, exactly like getdrives itself).
        self._query_free()

    # ---- free space (psize/pfull, dot v5.2+) --------------------------
    @staticmethod
    def _fmt_free(nbytes):
        """Human-readable free space (the pfull view): 512 -> '512 bytes',
        1572864 -> '1.5 MB'."""
        if nbytes < 1024:
            return f"{nbytes} bytes"
        v = float(nbytes)
        for unit in ("KB", "MB", "GB", "TB"):
            v /= 1024.0
            if v < 1024.0 or unit == "TB":
                return f"{v:.1f} {unit}"

    def _query_free(self, drive=""):
        """Ask the Next for a drive's free space ('Z', dot v5.2+). Read-only
        and tiny, so it rides _enqueue_raw: it must never count toward a
        running operation's progress (its reply emits no op_done)."""
        if self._connected:
            self._enqueue_raw(("free", drive or self._cwd_drive()))

    def on_free_space(self, drive, nbytes):
        """'Z' result: cache it and refresh the path label. ``nbytes`` is None
        when the query failed on the Next ('F') or the dot predates v5.2 (the
        worker's log line says which); then any stale figure is dropped so the
        label never shows a wrong number. Also feeds a pending paste precheck
        waiting on the destination drive's fresh figure."""
        drive = (drive or "").strip().upper()[:1] or self._cwd_drive()
        self._free_space[drive] = nbytes
        if nbytes is not None:
            self._log(f"Drive {drive}: {self._fmt_free(nbytes)} free "
                      f"({nbytes} bytes)")
        self._update_next_path_label()
        pc = self._precheck
        if pc is not None and not pc["free_seen"] and drive == pc["drive"]:
            pc["free_seen"] = True
            pc["free"] = nbytes
            if pc["sizes_done"]:
                self._precheck_evaluate()

    @staticmethod
    def _free_color(nbytes):
        """Traffic-light colour for the free-space figure: green above
        200 MB, yellow between 100 and 200 MB, red below 100 MB. Shades
        picked to stay readable on both light and dark backgrounds."""
        mb = nbytes / (1024.0 * 1024.0)
        if mb > 200:
            return "#2fb344"       # green: comfortable
        if mb >= 100:
            return "#dd9c07"       # yellow/amber: getting tight
        return "#e03131"           # red: nearly full

    def _update_next_path_label(self):
        """Top label = the cached free space of the cwd's drive (if known);
        bottom path box = the cwd itself (mirroring the local pane's row).
        The free-space figure is bold and traffic-light coloured (see
        _free_color) so a filling-up card is visible at a glance."""
        free = self._free_space.get(self._cwd_drive())
        if free is not None:
            # Rich text so only the free-space part is coloured; the label
            # auto-detects HTML.
            self.next_path_label.setText(
                f"Next: <b><span style=\"color: {self._free_color(free)};\">"
                f"{html.escape(self._fmt_free(free))} free</span></b>")
            self.next_path_label.setToolTip(
                f"Free space on drive {self._cwd_drive()}: {free} bytes "
                "(reported by the Next via F_GETFREE; NextZXOS exposes no "
                "safe way for a dot command to read the total partition "
                "size).\nGreen: more than 200 MB free · yellow: 100–200 MB "
                "· red: below 100 MB.\nRe-read after every transfer, "
                "delete, rename or copy, so the figure tracks the card.")
        else:
            self.next_path_label.setText("Next: connected")
            self.next_path_label.setToolTip("")
        # Never stomp a path the user is midway through typing: setText only
        # while the box carries no uncommitted edit (isModified is cleared by
        # setText and by _on_next_path_edit committing).
        if not self.next_path_edit.isModified():
            self.next_path_edit.setText(self._cwd)

    def _known_drives(self):
        """Every drive the combo offers: the dot-reported set plus the
        user-declared extras (only once the dot reported anything — an old
        dot gives us no safe way to know extras will even parse)."""
        if not self._drives:
            return []
        return sorted(set(self._drives) | set(self._extra_drives))

    def _rebuild_drive_combo(self):
        """(Re)fill the drive switcher from _known_drives(), keeping the
        current selection pointed at the cwd's drive."""
        known = self._known_drives()
        self._drive_combo_guard = True
        try:
            self.next_drive_combo.clear()
            if known:
                self.next_drive_combo.addItems(known)
                self.next_drive_combo.setEnabled(len(known) > 1)
            else:
                # Old dot: show the one drive we're implicitly on.
                self.next_drive_combo.addItem(self._cwd_drive())
                self.next_drive_combo.setEnabled(False)
        finally:
            self._drive_combo_guard = False
        self.next_drive_add.setEnabled(self._connected and bool(self._drives))
        self._sync_drive_combo()

    def _add_drive_clicked(self):
        """Declare an extra drive letter (additional SD reader/partition).

        The Next cannot discover these itself — and it must never guess:
        merely opening a path on an unmounted drive crashes the dot, which is
        why adding one is an explicit, warned, user decision."""
        letter, ok = QInputDialog.getText(
            self, ui_tr_now("Add Next drive"),
            ui_tr_now("Drive letter of the additional SD reader/partition (D..P):"))
        letter = (letter or "").strip().rstrip(":").upper()
        if not ok or not letter:
            return
        if len(letter) != 1 or letter not in "CDEFGHIJKLMNOP":
            self._log("Add drive: enter a single letter C..P (A/B are the "
                      "floppy drives and cannot be used).")
            return
        if letter in self._known_drives():
            self._select_drive(letter)
            return
        if QMessageBox.warning(
                self, ui_tr_now("Add Next drive"),
                ui_tr_now("Add drive {letter}: to the list?").format(
                    letter=letter) + "\n\n"
                + ui_tr_now("Only add a drive that really exists on your "
                            "Next (an extra SD card reader or partition). "
                            "Selecting a drive that is not mounted CRASHES "
                            "the Next."),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        self._add_drive_letter(letter)

    def _add_drive_letter(self, letter):
        """Add a confirmed extra drive, persist it, and switch to it."""
        if letter not in self._extra_drives:
            self._extra_drives = sorted(set(self._extra_drives) | {letter})
            self._on_extra_drives_changed("".join(self._extra_drives))
        self._rebuild_drive_combo()
        self._select_drive(letter)

    def _select_drive(self, letter):
        """Point the combo at ``letter`` as a user action (switches drive)."""
        ix = self.next_drive_combo.findText(letter)
        if ix >= 0:
            self.next_drive_combo.setCurrentIndex(ix)

    def _cwd_drive(self):
        """The drive the pane is effectively on: the cwd's prefix if it has
        one, else the dot's reported current drive, else "C"."""
        return _re_drive_of(self._cwd) or self._default_drive or "C"

    def _sync_drive_combo(self):
        """Point the drive switcher at the drive the cwd is on (guarded, so it
        never fires _on_drive_changed)."""
        want = self._cwd_drive()
        ix = self.next_drive_combo.findText(want)
        if ix >= 0 and self.next_drive_combo.currentIndex() != ix:
            self._drive_combo_guard = True
            try:
                self.next_drive_combo.setCurrentIndex(ix)
            finally:
                self._drive_combo_guard = False

    def _on_drive_changed(self, text):
        """User picked a drive: jump to that drive's root and list it. Every
        later command (ls/get/put/mkdir/rm/rmdir/rename/rmtree, drag-drops
        included) builds its paths from the cwd, so the drive prefix rides
        along automatically."""
        if self._drive_combo_guard or not self._connected:
            return
        drive = (text or "").strip().upper()[:1]
        if not drive or drive == _re_drive_of(self._cwd):
            return
        self._cwd = f"{drive}:/"
        self.refresh()
        self._query_free(drive)   # free space of the newly selected drive

    def on_listing(self, path, entries):
        self._cwd = (path if (path.startswith("/") or _re_drive_of(path))
                     else "/" + path)
        self._update_next_path_label()
        self._sync_drive_combo()
        # Remember this (confirmed-good) folder so a later reconnect returns here.
        self._remember_remote_cwd(self._cwd)
        # Cache the entries so a later header click can re-sort without a re-listing.
        self._next_entries = [(bool(is_dir), size, name)
                              for is_dir, size, name in entries
                              if name not in (".", "..")]
        self._rebuild_next_rows()

    def _rebuild_next_rows(self):
        """(Re)populate the Next pane from the cached listing in the current sort
        order, always keeping ".." pinned at the top."""
        self.next_model.removeRows(0, self.next_model.rowCount())
        if not self._at_drive_root():
            self._add_next_row("..", True, None, is_updir=True)
        for is_dir, size, name in self._sorted_next_entries():
            self._add_next_row(name, is_dir, size)
        self.next_view.resizeColumnToContents(0)

    def _sorted_next_entries(self):
        """The cached Next entries ordered by the current sort key/direction.

        Size sorts numerically (not as the "12K"/"1M" display text); Type and
        Size group folders together (they carry no extension or size)."""
        key, order = self._next_sort
        reverse = (order == Qt.DescendingOrder)

        def sort_key(e):
            is_dir, size, name = e
            if key == "size":
                return (0 if is_dir else 1, size or 0, name.lower())
            if key == "type":
                return (0 if is_dir else 1, self._next_type_text(name).lower(),
                        name.lower())
            return (name.lower(),)      # name: plain alphabetical, A first
        return sorted(self._next_entries, key=sort_key, reverse=reverse)

    def on_ls_failed(self, path):
        """A listing could not be opened on the Next: the folder is gone.

        Happens when the folder we tried to restore on reconnect (or navigated
        into) no longer exists. Drop back to the root of the same drive so the
        pane recovers (and a missing drive root falls back to the default "/").
        """
        drive = _re_drive_of(path)
        root = f"{drive}:/" if drive else "/"
        if (path or "/") in ("/", root):
            if (path or "/") == "/" or not drive:
                return               # root itself failed: nothing better to do
            root = "/"               # a drive root failed: back to the default
        self._log(f"{path}: no such folder on the Next — returning to {root}.")
        self._on_toast("Folder unavailable",
                       ui_tr_now("{path} no longer exists on the Next.\n"
                                 "Returned to {root}.").format(
                                     path=path, root=root),
                       "yellow")
        self._cwd = root
        self.refresh()

    def _remember_remote_cwd(self, path):
        """Record the current Next folder for restore-on-reconnect, notifying the
        host (which persists it, per machine since 9.5.14) only when the folder
        — or the MACHINE it belongs to — actually changes: two Nexts sitting on
        the same path must still each get their own entry."""
        norm = _norm_remote_dir(path)
        addr = self._active_addr()
        if norm != self._remote_start_dir or addr != self._remote_cwd_addr:
            self._remote_start_dir = norm
            self._remote_cwd_addr = addr
            self._on_remote_cwd_changed(norm, addr)

    # ==================================================================
    #  column sort (persisted per pane; default Name ascending)
    # ==================================================================
    def _save_sort(self, which, key, order):
        self._on_sort_changed(which, _re_sort_to_str(key, order))

    def _apply_local_sort(self):
        """Apply the restored local-pane sort without it counting as a user edit."""
        key, order = self._local_sort
        self._restoring_sort = True
        try:
            self.local_view.sortByColumn(RE_LOCAL_SORT_COL.get(key, 0), order)
        finally:
            self._restoring_sort = False

    def _on_local_sort_changed(self, column, order):
        # Fired by the header on every sort change; ignore the programmatic one we
        # trigger while restoring, persist the rest.
        if self._restoring_sort:
            return
        key = RE_LOCAL_SORT_KEY.get(column, "name")
        self._local_sort = (key, order)
        self._save_sort("local", key, order)

    def _apply_next_sort_indicator(self):
        key, order = self._next_sort
        self.next_view.header().setSortIndicator(RE_NEXT_SORT_COL.get(key, 0), order)

    def _on_next_header_clicked(self, column):
        # Clicking the current column flips direction; a different column starts
        # ascending. We sort the cached listing ourselves and repaint.
        key = RE_NEXT_SORT_KEY.get(column, "name")
        cur_key, cur_order = self._next_sort
        if key == cur_key:
            order = (Qt.AscendingOrder if cur_order == Qt.DescendingOrder
                     else Qt.DescendingOrder)
        else:
            order = Qt.AscendingOrder
        self._next_sort = (key, order)
        self._apply_next_sort_indicator()
        self._save_sort("next", key, order)
        self._rebuild_next_rows()

    def on_got(self, remote, local_path):
        self._log(f"Downloaded {remote} -> {local_path}")
        self.local_model.setRootPath(self.local_model.rootPath())  # nudge a refresh
        self._op_step_done(f"Downloaded {posixpath.basename(remote.rstrip('/')) or remote}")

    def on_put_done(self, ok, remote):
        self._log(f"Uploaded -> {remote}" if ok else f"Upload failed: {remote}")
        if not ok:
            self._cut_fail_head()
            # The Next abandoned the pull (e.g. locked/read-only destination).
            self._record_op_failure(
                f"Upload failed: {posixpath.basename(remote.rstrip('/')) or remote}")
        # Only refresh when the file landed in the folder we're looking at, so a
        # recursive folder upload doesn't fire one listing per file.
        if self._in_cwd(remote):
            self.refresh()
        self._op_step_done(f"Uploaded {posixpath.basename(remote.rstrip('/')) or remote}")

    # The exact guidance the user sees when a remote write is refused by the
    # far side's OS protection. A constant so the toast, the log and the tests
    # speak with one voice.
    OSP_TITLE = "🛡  Remote OS protection"
    OSP_MESSAGE = (
        "Write access appears to be blocked in the remote operating system. "
        "If you are using ZX Next Remote as the listener, please check its "
        "\"OS protection\" setting and customise the restricted directory "
        "list if appropriate."
    )

    def on_os_protected(self, op, path):
        """A remote WRITE (mkdir/rmdir/rm/rename/copy — or an upload: a
        put refused with the marked 'F'+OSP block) was refused by the far
        side's OS protection. Retrying — or grinding through the rest of a
        batch — would only repeat the refusal, so stop the operation and say
        exactly what to check: the block is on the OTHER machine's settings,
        not here."""
        self._log(f"{op} {path}: BLOCKED by remote OS protection")
        self._on_toast(self.OSP_TITLE, self.OSP_MESSAGE, "red")
        self._record_op_failure(
            f"{op} blocked by remote OS protection: "
            f"{posixpath.basename(path.rstrip('/')) or path}")
        # Stop the batch like a user cancel: drop what is still queued (an
        # in-flight transfer finishes on its own) and delete no further move
        # sources. Force _op_quiet_failures off so the end-of-op toast lists
        # this even inside the paste precheck.
        if self._op_active and not self._op_cancelled:
            self._op_cancelled = True
            self._op_quiet_failures = False
            drained = self._drain_raw() if self._drain_raw else 0
            self._op_completed += int(drained or 0)
            self._cut_jobs.clear()
        self._op_step_done()

    def on_op_done(self, ok, op, path):
        self._log(f"{op} {path}: {'ok' if ok else 'FAILED'}")
        # A failed mkdir usually just means the folder already exists, so the many
        # mkdirs of a recursive upload stay quiet; a deliberate New Folder opts in
        # via _op_toast_mkdir. rmdir/rm/rename failures are always real.
        if not ok and (op != "mkdir" or self._op_toast_mkdir):
            self._record_op_failure(
                f"{op} failed: {posixpath.basename(path.rstrip('/')) or path}")
        if self._in_cwd(path):
            self.refresh()
        self._op_step_done(f"{op} {posixpath.basename(path.rstrip('/')) or path}")

    def on_error(self, msg=None):
        # Any error while a move's transfer is draining means we must not delete
        # its source. It also counts as that command reporting back, and (during an
        # operation) is worth surfacing to the user - e.g. a failed/dropped get.
        self._cut_fail_head()
        if msg and self._op_active:
            self._record_op_failure(str(msg))
        self._op_step_done()

    def on_marked(self, token):
        """A queued move barrier was reached: the head job's transfer is done.

        Any follow-on deletes are queued *before* this marker is counted, so the
        operation stays active until they too complete.
        """
        job = None
        if self._cut_jobs:
            head = self._cut_jobs[0]
            if str(head.get("token")) == str(token):
                job = self._cut_jobs.pop(0)
        if job is None:
            # Cancelled, out of step, or not a move marker: just count it.
            self._op_step_done()
            return
        if not job.get("ok", False):
            self._log(f"Move: transfer failed, kept {job['src_path']}")
        elif job["src_kind"] == "local":
            self._delete_local_after_move(job["src_path"])
        else:
            self._delete_remote_after_move(job)
        self._op_step_done()

    def _in_cwd(self, path):
        """True if ``path``'s parent is the directory currently shown.

        Drive-aware on both sides: dirname("M:/x") yields the bare "M:", which
        _re_norm_dir turns back into the "M:/" root form the cwd uses."""
        parent = _re_norm_dir(posixpath.dirname(path.rstrip("/")) or "/")
        cwd = self._cwd
        if not (cwd.startswith("/") or _re_drive_of(cwd)):
            cwd = "/" + cwd
        return parent == _re_norm_dir(cwd.rstrip("/") or "/")

    # ==================================================================
    #  Next pane
    # ==================================================================
    @staticmethod
    def _next_type_text(name):
        # Mirror the SD-card image tree: the "type" is the first extension
        # segment (shared with the local pane's Type column).
        return _ext_type_text(name)

    def _add_next_row(self, name, is_dir, size, is_updir=False):
        name_item = QStandardItem(self._dir_icon if is_dir else self._file_icon, name)
        name_item.setData(".." if is_updir else _posix_join(self._cwd, name), RE_PATH_ROLE)
        name_item.setData(bool(is_dir), RE_ISDIR_ROLE)
        name_item.setEditable(False)
        if is_updir:
            type_item = QStandardItem("")
            size_item = QStandardItem("")
        elif is_dir:
            type_item = QStandardItem("DIR")
            size_item = QStandardItem("")
        else:
            type_item = QStandardItem(self._next_type_text(name))
            size_item = QStandardItem(_human_size(size))
        type_item.setEditable(False)
        size_item.setEditable(False)
        self.next_model.appendRow([name_item, type_item, size_item])
        self._color_next_row(name_item, type_item, size_item)

    def _color_next_row(self, name_item, type_item, size_item):
        """Tint one Next row's items with the configured SD-card item colours."""
        c = self._colors
        if name_item.data(RE_PATH_ROLE) == "..":
            name_item.setForeground(c["up_directory"])
            return
        if bool(name_item.data(RE_ISDIR_ROLE)):
            name_item.setForeground(c["dir_name"])
            type_item.setForeground(c["dir_type"])
        else:
            name_item.setForeground(c["file_name"])
            type_item.setForeground(c["file_ext"])
            size_item.setForeground(c["file_size"])

    def _recolor_next(self):
        """Re-tint every row already shown in the Next pane, in place (used when
        the user changes the item colours in Settings — no re-listing needed)."""
        model = self.next_model
        for r in range(model.rowCount()):
            name_item = model.item(r, 0)
            if name_item is None:
                continue
            self._color_next_row(name_item, model.item(r, 1), model.item(r, 2))

    def set_item_colors(self, colors):
        """Push the SD Card Utility's live item colours into both panes.

        ``colors`` maps the keys up_directory/dir_name/dir_type/file_name/
        file_ext/file_size/general_text to QColor (the host's ``img_color_*``
        values). Missing keys keep their current value. The shared ``_colors``
        dict is mutated in place so the local model repaints in the new colours
        and the Next rows are re-tinted immediately.
        """
        if not colors:
            return
        for key, value in colors.items():
            if key in self._colors and value is not None:
                self._colors[key] = QColor(value)
        self._recolor_next()
        # The local model reads _colors live in data(); a repaint re-queries it.
        self.local_view.viewport().update()

    def refresh(self):
        # Suppressed while an operation runs (a single listing happens when it
        # ends), so mid-batch completions don't flood the queue with listings.
        if self._connected and not self._op_active:
            self._enqueue(("ls", self._cwd))

    def _at_drive_root(self):
        """True when the cwd is a root ("/" or "X:/") — nowhere further up."""
        rest = self._cwd[2:] if _re_drive_of(self._cwd) else self._cwd
        return rest in ("/", "")

    def _next_up(self):
        if not self._at_drive_root():
            self._cwd = _re_norm_dir(
                posixpath.dirname(self._cwd.rstrip("/")) or "/")
            self.refresh()

    def _on_next_path_edit(self):
        """ENTER in the Next path box: jump the pane to the typed folder.

        A typed drive letter is checked against the known set first —
        merely opening a path on an unmounted drive CRASHES the dot (the
        hardware rule '+ Drive' warns about), so an unknown letter is
        refused with a pointer at '+ Drive' instead of being probed. A
        folder that turns out not to exist is handled like any dead
        listing (on_listing_failed backs off to the drive root)."""
        if not self._connected:
            return
        raw = (self.next_path_edit.text() or "").strip().strip('"')
        self.next_path_edit.setModified(False)
        if not raw:
            self.next_path_edit.setText(self._cwd)
            return
        path = _norm_remote_dir(raw)
        drive = _re_drive_of(path)
        known = self._known_drives()
        if drive and known and drive not in known:
            self._log(f"Unknown Next drive {drive}: — use '+ Drive' to add "
                      "an extra SD reader/partition letter first.")
            self.next_path_edit.setText(self._cwd)
            return
        self._cwd = path
        self.refresh()

    def _next_double_clicked(self, index):
        item = self.next_model.itemFromIndex(index.siblingAtColumn(0))
        if item is None:
            return
        if item.data(RE_PATH_ROLE) == "..":
            self._next_up()
            return
        if bool(item.data(RE_ISDIR_ROLE)):
            self._cwd = item.data(RE_PATH_ROLE)
            self.refresh()

    def _selected_next_entries(self):
        out = []
        for ix in self.next_view.selectionModel().selectedRows(0):
            item = self.next_model.itemFromIndex(ix)
            if item is None:
                continue
            path = item.data(RE_PATH_ROLE)
            if not path or path == "..":
                continue
            out.append((path, bool(item.data(RE_ISDIR_ROLE))))
        return out

    def _next_context_menu(self, pos):
        if not self._connected:
            return
        menu = QMenu(self)
        # "Start <emulator> with <file>" at the top, mirroring the SD Card tab's
        # image pane: the file lives on the Next, so it is downloaded to the PC
        # first and the emulator started on that copy (see
        # _emulator_start_from_next). Offered for a single selected file only.
        emu_next = []
        _sel_now = self._selected_next_entries()
        if len(_sel_now) == 1 and not _sel_now[0][1]:
            for entry in self._emulator_entries(_sel_now[0][0]):
                emu_next.append((menu.addAction(entry.label), entry))
            if emu_next:
                menu.addSeparator()
        act_new = menu.addAction(ui_tr_now("New Folder…"))
        act_get = menu.addAction(ui_tr_now("Download (:<-)"))
        act_size = menu.addAction(ui_tr_now("Get size"))
        act_unzip = menu.addAction(ui_tr_now("Remote Unzip file"))
        act_rzip = menu.addAction(ui_tr_now("Remote Zip"))
        act_copy = menu.addAction(ui_tr_now("Copy"))
        act_cut = menu.addAction(ui_tr_now("Cut"))
        act_paste = menu.addAction(ui_tr_now("Paste"))
        act_ren = menu.addAction(ui_tr_now("Rename…"))
        act_del = menu.addAction(ui_tr_now("Delete"))
        act_ref = menu.addAction(ui_tr_now("Refresh"))
        sel = self._selected_next_entries()
        # Rename and Get size act on exactly one item.
        act_ren.setEnabled(len(sel) == 1)
        act_size.setEnabled(len(sel) == 1)
        # "Remote Unzip file" only appears for a single selected .zip FILE;
        # "Remote Zip" whenever something is selected. Both work PC-side
        # (download -> unzip/zip -> upload): the dot cannot run .unzip
        # itself while .sync occupies the dot page.
        act_unzip.setVisible(len(sel) == 1 and not sel[0][1]
                             and sel[0][0].lower().endswith(".zip"))
        act_rzip.setVisible(bool(sel))
        act_copy.setEnabled(bool(sel))
        act_cut.setEnabled(bool(sel))
        # Paste: a local clipboard uploads here; a copied Next clipboard is
        # duplicated ON the Next itself via the dot's rcpy (v5.2+).
        act_paste.setEnabled(bool(self._clip))
        chosen = menu.exec(self.next_view.viewport().mapToGlobal(pos))
        for act, entry in emu_next:
            if chosen == act:
                self._emulator_start_from_next(_sel_now[0][0], entry)
                return
        if chosen == act_new:
            self._new_folder()
        elif chosen == act_get:
            self._get_selected()
        elif chosen == act_size:
            self._get_size_selected()
        elif chosen == act_unzip:
            self._remote_unzip(sel[0][0])
        elif chosen == act_rzip:
            self._remote_zip(sel)
        elif chosen == act_copy:
            self._copy_next("copy")
        elif chosen == act_cut:
            self._copy_next("cut")
        elif chosen == act_paste:
            self._paste_into_next()
        elif chosen == act_ren:
            self._rename_selected()
        elif chosen == act_del:
            self._delete_selected()
        elif chosen == act_ref:
            self.refresh()

    def _new_folder(self):
        if not self._connected or self._busy_refused():
            return
        name, ok = QInputDialog.getText(self, ui_tr_now("New Folder"), ui_tr_now("New folder in {path}:").format(path=self._cwd))
        if ok and name.strip():
            target = _posix_join(self._cwd, name.strip())
            self._run_op("Creating folder on the Next…",
                         lambda: self._enqueue(("mkdir", target)),
                         toast_mkdir_fail=True)

    def _rename_selected(self):
        if not self._connected or self._busy_refused():
            return
        entries = self._selected_next_entries()
        if len(entries) != 1:
            self._log("Select exactly one Next item to rename.")
            return
        path, _is_dir = entries[0]
        old_name = posixpath.basename(path.rstrip("/")) or path
        new_name, ok = QInputDialog.getText(
            self, ui_tr_now("Rename"), ui_tr_now("Rename '{name}' to:").format(name=old_name), text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if "/" in new_name or "\\" in new_name:
            self._log("Rename: enter a name only, not a path.")
            return
        parent = _re_norm_dir(posixpath.dirname(path.rstrip("/")) or "/")
        target = _posix_join(parent, new_name)
        self._run_op("Renaming on the Next…",
                     lambda: self._enqueue(("rename", path, target)))

    def _get_size_selected(self):
        """rfsize: measure the selected file or whole folder ON the Next
        ('S', dot v5.2+) - rcpy's "will the copy fit" companion. Runs as an
        indeterminate operation so the busy animation shows while the Next
        walks the tree (that can take a while on big folders); the worker's
        op_done closes the op, then on_fsize pops the result dialog."""
        if not self._connected or self._busy_refused():
            return
        entries = self._selected_next_entries()
        if len(entries) != 1:
            self._log("Select exactly one Next item to measure.")
            return
        path, _is_dir = entries[0]
        self._run_op("Measuring size on the Next…",
                     lambda: self._enqueue(("fsize", path)),
                     determinate=False)

    def on_fsize(self, path, data):
        """rfsize result. A pending paste precheck consumes its own paths
        silently (a failed measure there just means "size unknown"). Otherwise
        it is the user's Get Size: pop the result dialog (data = None needs no
        second report — the op_done(False, "size", path) that preceded it
        already raised the standard failure toast)."""
        pc = self._precheck
        if pc is not None and path in pc["paths"]:
            if data is None:
                pc["unknown"] = True
            else:
                pc["bytes"] += int(data.get("bytes", 0))
                pc["files"] += int(data.get("files", 0))
            return
        if data is None:
            return
        n = int(data.get("bytes", 0))
        QMessageBox.information(
            self, ui_tr_now("Size on the Next"),
            f"{path}\n\n"
            + ui_tr_now("Files:  {files}\nFolders:  {folders}\n"
                        "Total size:  {size} bytes  ({pretty})").format(
                files=f"{int(data.get('files', 0)):,}",
                folders=f"{int(data.get('dirs', 0)):,}",
                size=f"{n:,}", pretty=self._fmt_free(n)))

    def _delete_selected(self):
        entries = self._selected_next_entries()
        if not entries or self._busy_refused():
            return
        names = "\n".join(p for p, _ in entries)
        if QMessageBox.question(
                self, ui_tr_now("Delete"),
                ui_tr_now("Delete on the Next? Folders are deleted "
                          "with everything inside them.")
                + f"\n\n{names}") != QMessageBox.Yes:
            return

        # Folders go through the worker's recursive rmtree walk: esxDOS rmdir
        # only removes *empty* folders, so a bare rmdir on a folder with content
        # fails and deletes nothing.
        def go():
            for path, is_dir in entries:
                self._enqueue(("rmtree" if is_dir else "rm", path))
        self._run_op("Deleting on the Next…", go)

    def _next_key_press(self, event):
        # Ctrl+C / Ctrl+X copy or cut the Next selection; Ctrl+V pastes local
        # clipboard items here (upload). Delete / F2 mirror the context-menu
        # Delete / Rename.
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_next("copy")
            return
        if event.matches(QKeySequence.StandardKey.Cut):
            self._copy_next("cut")
            return
        if self._connected:
            if event.matches(QKeySequence.StandardKey.Paste):
                self._paste_into_next()
                return
            if event.key() == Qt.Key.Key_Delete:
                self._delete_selected()
                return
            if event.key() == Qt.Key.Key_F2:
                self._rename_selected()
                return
        QTreeView.keyPressEvent(self.next_view, event)

    # ==================================================================
    #  copy / cut / paste (internal buffer shared between the two panes)
    # ==================================================================
    def _copy_next(self, mode="copy"):
        entries = self._selected_next_entries()
        if entries:
            self._clip = ("next", entries, mode)
            verb = "Cut" if mode == "cut" else "Copied"
            if mode == "cut":
                self._log(f"{verb} {len(entries)} Next item(s). Paste in the "
                          "local pane to move them to the PC.")
            else:
                self._log(f"{verb} {len(entries)} Next item(s). Paste in the "
                          "local pane to download, or in a Next folder to "
                          "duplicate them ON the Next (dot v5.2+, works "
                          "across partitions).")

    def _copy_local(self, mode="copy"):
        paths = [p for p in self._selected_local_paths() if os.path.exists(p)]
        if paths:
            self._clip = ("local", paths, mode)
            verb = "Cut" if mode == "cut" else "Copied"
            self._log(f"{verb} {len(paths)} local item(s). Paste in the Next "
                      "pane to " + ("move" if mode == "cut" else "upload")
                      + ", or in a local folder to "
                      + ("move" if mode == "cut" else "copy") + " it there.")

    def _paste_into_next(self):
        # Paste into the current Next directory. Local clipboard: copy =
        # upload, cut = upload then delete the local source once confirmed.
        # NEXT clipboard (copy): duplicate the items ON the Next itself via
        # the dot's rcpy command (v5.2+) - no data crosses the wire, and it
        # works across partitions (paste under a different drive's cwd).
        if not self._connected or not self._clip:
            return
        if self._busy_refused():   # BEFORE any branch can consume a cut clip
            return
        if self._clip[0] == "next":
            _kind, entries, mode = self._clip
            if mode == "cut":
                # A within-Next move isn't offered: cut Next items paste into
                # the LOCAL pane (move to the PC); same-drive moves are what
                # Rename is for.
                self._log("Cut Next items paste into the local pane (move to "
                          "the PC). To duplicate on the Next use Copy; to "
                          "move/rename use Rename.")
                return
            base = self._cwd
            jobs = []
            for path, _is_dir in entries:
                name = posixpath.basename(path.rstrip("/")) or path
                dst = _posix_join(base, name)
                # Guard rcpy's infinite trap (folder into itself: the Next-side
                # walk would re-read its own growing output forever) and the
                # pointless self-overwrite. FAT is case-insensitive -> lower().
                s = path.rstrip("/").lower()
                d = dst.rstrip("/").lower()
                if d == s or d.startswith(s + "/"):
                    self._log(f"copy: skipped {path} (destination equals or "
                              "is inside the source)")
                    continue
                jobs.append((path, dst))
            if not jobs:
                return
            # Will it fit? Measure every source (rfsize) and re-read the
            # destination drive's free space BEFORE any rcpy is allowed to
            # start; _precheck_evaluate then blocks the copy with a clear
            # message when it cannot fit, or launches it (with the measured
            # totals driving the progress bar).
            self._start_rcpy_precheck(jobs)
            return
        _kind, paths, mode = self._clip
        paths = list(paths)
        if mode == "cut":
            self._clip = None            # a cut is consumed by its paste
            self._run_op("Moving to the Next…",
                         lambda: self._move_local_paths_to_next(paths),
                         determinate=False)
        else:
            self._run_op("Uploading to the Next…",
                         lambda: self._put_paths(paths))

    # ==================================================================
    #  Next -> Next paste: free-space precheck, then the rcpy itself
    # ==================================================================
    def _start_rcpy_precheck(self, jobs):
        """Stage 1 of a Next->Next paste: rfsize every source, then re-read
        the DESTINATION drive's free space (queued after the sizes, so it is
        the freshest figure the Next can give). on_fsize/on_free_space feed
        the results in; _precheck_evaluate decides."""
        pc = {
            "jobs": jobs,
            "paths": {p for p, _ in jobs},
            "bytes": 0, "files": 0,
            "unknown": False,       # any rfsize failed -> sizes unreliable
            "free": None, "free_seen": False,
            "sizes_done": False,
            "drive": self._cwd_drive(),
        }

        def go():
            # Set inside the op (i.e. after _run_op's guards passed): a
            # pending precheck blocks _run_op, so it must never be armed for
            # an op that was refused.
            self._precheck = pc
            for path, _ in jobs:
                self._enqueue(("fsize", path))
            # The free-space query rides _enqueue_raw (its reply emits no
            # op_done, so it must not count toward the op) and is queued
            # AFTER the sizes: the worker answers in order, so the figure
            # arrives last — a true last-moment reading.
            self._enqueue_raw(("free", pc["drive"]))
        self._run_op("Checking space on the Next…", go, determinate=False,
                     quiet_failures=True, on_done=self._precheck_sizes_done)

    def _precheck_sizes_done(self, _ok, _fails):
        """Stage 1's operation ended (every rfsize replied). The free-space
        figure normally arrives just after (queued behind the sizes); a guard
        timer makes sure a lost reply can't strand the paste silently."""
        pc = self._precheck
        if pc is None:
            return
        if not self._connected:
            self._precheck = None
            return
        pc["sizes_done"] = True
        if pc["free_seen"]:
            self._precheck_evaluate()
        else:
            QTimer.singleShot(20000, lambda pc=pc: self._precheck_free_timeout(pc))

    def _precheck_free_timeout(self, pc):
        if self._precheck is pc and not pc["free_seen"]:
            self._log("Free-space reply never arrived; copying without the "
                      "space check.")
            pc["free_seen"] = True          # proceed with free unknown (None)
            self._precheck_evaluate()

    def _precheck_evaluate(self):
        """Stage 2: sizes and free space are in. Refuse the paste outright when
        the copy cannot fit; otherwise launch the rcpy operation, armed with
        the measured totals so its progress bar tracks the heartbeats."""
        pc, self._precheck = self._precheck, None
        if pc is None or not self._connected:
            return
        total, files, free = pc["bytes"], pc["files"], pc["free"]
        drive = pc["drive"]
        if free is not None and not pc["unknown"] and total > free:
            over = total - free
            self._log(f"rcpy refused: needs {total:,} bytes but drive {drive} "
                      f"has only {free:,} free ({over:,} bytes short).")
            QMessageBox.critical(
                self, ui_tr_now("Not enough space on the Next"),
                ui_tr_now("This copy needs {need} bytes ({need_h}), "
                          "but drive {drive}: only has {free} bytes "
                          "({free_h}) free.\n\nIt exceeds the available "
                          "remote space by {over} bytes ({over_h}).\n\n"
                          "The copy was not started.").format(
                    need=f"{total:,}", need_h=self._fmt_free(total),
                    drive=drive, free=f"{free:,}",
                    free_h=self._fmt_free(free), over=f"{over:,}",
                    over_h=self._fmt_free(over)),
                QMessageBox.StandardButton.Close)
            return
        if pc["unknown"] or free is None:
            self._log("Could not verify the copy's size against the free "
                      "space; copying anyway.")
            total = files = 0               # no totals -> marquee progress
        self._start_rcpy(pc["jobs"], total, files)

    def _start_rcpy(self, jobs, bytes_total, files_total):
        """The rcpy operation proper. With totals from the precheck the
        progress bar is driven by the Next's 'D' heartbeats (see
        on_op_progress); without them it shows the busy marquee. Its dialog
        button doesn't Cancel — a Next-side copy can't be interrupted — it
        closes the window and lets the copy finish in the background."""
        def go():
            for path, dst in jobs:
                self._enqueue(("rcpy", path, dst))
            self._log(f"Copying {len(jobs)} item(s) on the Next …")
        self._run_op(
            "Copying on the Next…", go,
            determinate=bool(bytes_total or files_total),
            background_label="Close this window and continue in the background",
            bytes_total=bytes_total, files_total=files_total)

    def _paste_into_local(self):
        # Paste the clipboard into the current local directory. Next items:
        # copy = download, cut = download then delete the Next source once
        # confirmed. Local items: copy = duplicate here, cut = move here.
        if not self._clip:
            return
        if self._clip[0] == "local":
            _kind, paths, mode = self._clip
            paths = [p for p in paths if os.path.exists(p)]
            if mode == "cut":
                self._clip = None            # a cut is consumed by its paste
            self._copy_paths_into_local(paths, self._local_dir(),
                                        move=(mode == "cut"), dup_in_place=True)
            return
        if self._busy_refused():   # BEFORE the Next branch can consume a cut
            return
        _kind, entries, mode = self._clip
        entries = list(entries)
        if mode == "cut":
            self._clip = None
            self._run_op("Moving from the Next…",
                         lambda: self._move_next_entries_to_local(entries),
                         determinate=False)
        else:
            dest = self._local_dir()

            def go():
                for path, is_dir in entries:
                    self._prepare_local_download_dir(dest, path, is_dir)
                    self._enqueue(("get", path, dest))
                self._log(f"Downloading {len(entries)} item(s) to {dest} …")
            self._run_op("Downloading from the Next…", go)

    # ==================================================================
    #  cut / move (transfer, then delete the source only once confirmed)
    # ==================================================================
    def _new_cut_token(self):
        self._cut_seq += 1
        return f"cut{self._cut_seq}"

    def _cut_head(self):
        return self._cut_jobs[0] if self._cut_jobs else None

    def _cut_fail_head(self):
        head = self._cut_head()
        if head is not None:
            head["ok"] = False

    def _move_local_paths_to_next(self, paths):
        """Upload each local file/folder, then delete it locally once its
        marker confirms the whole item transferred cleanly."""
        base = self._cwd if self._cwd.endswith("/") else self._cwd + "/"
        n = 0
        for p in paths:
            if os.path.isdir(p):
                self._enqueue_dir_upload(p, base)
            elif os.path.isfile(p):
                self._enqueue(("put", p, base))
            else:
                continue
            token = self._new_cut_token()
            self._cut_jobs.append({"token": token, "src_kind": "local",
                                   "src_path": p, "is_dir": os.path.isdir(p),
                                   "local_copy": None, "ok": True})
            self._enqueue(("mark", token))
            n += 1
        if n:
            self._log(f"Moving {n} item(s) to {self._cwd} …")

    def _move_next_entries_to_local(self, entries):
        """Download each Next file/folder, then delete it on the Next once its
        marker confirms the download completed."""
        dest = self._local_dir()
        n = 0
        for path, is_dir in entries:
            self._prepare_local_download_dir(dest, path, is_dir)
            self._enqueue(("get", path, dest))
            local_copy = (os.path.join(dest, os.path.basename(path.rstrip("/")))
                          if is_dir else None)
            token = self._new_cut_token()
            self._cut_jobs.append({"token": token, "src_kind": "next",
                                   "src_path": path, "is_dir": bool(is_dir),
                                   "local_copy": local_copy, "ok": True})
            self._enqueue(("mark", token))
            n += 1
        if n:
            self._log(f"Moving {n} item(s) to {dest} …")

    def _delete_local_after_move(self, path):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            self._log(f"Moved (removed local {path})")
        except OSError as ex:
            self._log(f"Move: uploaded but could not remove local {path}: {ex}")

    def _delete_remote_after_move(self, job):
        path = job["src_path"]
        if not job.get("is_dir"):
            self._enqueue(("rm", path))
            self._log(f"Moved (removing {path} on the Next)")
            return
        # esxDOS rmdir only removes empty folders, so delete the tree from the
        # bottom up. We just downloaded an exact copy, so mirror its layout
        # instead of re-listing the Next.
        local_copy = job.get("local_copy")
        if not local_copy or not os.path.isdir(local_copy):
            # Nothing to mirror from; try a plain rmdir (works if it was empty).
            self._enqueue(("rmdir", path))
            self._log(f"Moved (removing {path} on the Next)")
            return
        for root, _dirs, files in os.walk(local_copy, topdown=False):
            rel = os.path.relpath(root, local_copy)
            rdir = path if rel in (".", "") else _posix_join(
                path, rel.replace(os.sep, "/"))
            for name in sorted(files):
                self._enqueue(("rm", _posix_join(rdir, name)))
            self._enqueue(("rmdir", rdir))
        self._log(f"Moved (removing {path} tree on the Next)")

    # ==================================================================
    #  transfers
    # ==================================================================
    def _prepare_local_download_dir(self, dest, path, is_dir):
        """For a folder download, create the destination folder up front.

        The Next only streams the files inside a folder, so without this an
        *empty* folder would leave no local trace at all ("nothing happens"),
        and a folder's own entry would only ever appear once a file landed in
        it. Creating it here makes the folder show immediately.
        """
        if not is_dir:
            return
        try:
            os.makedirs(os.path.join(dest, os.path.basename(path.rstrip("/"))),
                        exist_ok=True)
        except OSError as ex:
            self._log(f"Could not create local folder for {path}: {ex}")

    def _get_selected(self):
        entries = self._selected_next_entries()
        if not entries or self._busy_refused():
            return
        dest = self._local_dir()

        def go():
            for path, is_dir in entries:
                self._prepare_local_download_dir(dest, path, is_dir)
                self._enqueue(("get", path, dest))
            self._log(f"Downloading {len(entries)} item(s) to {dest} …")
        self._run_op("Downloading from the Next…", go)

    def _put_selected(self):
        paths = [p for p in self._selected_local_paths() if os.path.exists(p)]
        if not paths:
            self._log("Select local file(s) or folder(s) to upload.")
            return
        if self._busy_refused():
            return
        self._run_op("Uploading to the Next…", lambda: self._put_paths(paths))

    def _put_paths(self, paths):
        """Upload files and/or whole folders to the current Next directory.

        Folders are recreated on the Next: each sub-directory is made with
        ``mkdir`` and every file is ``put`` into it, in top-down order so a
        directory always exists before files land in it (the listen worker
        processes the queue strictly in order).
        """
        base = self._cwd if self._cwd.endswith("/") else self._cwd + "/"
        n_files = 0
        n_dirs = 0
        for p in paths:
            if os.path.isdir(p):
                n_files += self._enqueue_dir_upload(p, base)
                n_dirs += 1
            elif os.path.isfile(p):
                self._enqueue(("put", p, base))
                n_files += 1
        if n_files or n_dirs:
            what = f"{n_files} file(s)"
            if n_dirs:
                what += f" in {n_dirs} folder(s)"
            self._log(f"Uploading {what} to {self._cwd} …")

    def _enqueue_dir_upload(self, local_dir, remote_base):
        """Queue mkdir/put commands to copy ``local_dir`` under ``remote_base``.

        Returns the number of files queued.
        """
        local_dir = os.path.normpath(local_dir)
        top = os.path.basename(local_dir.rstrip("/\\")) or "dir"
        remote_top = _posix_join(remote_base, top)
        self._enqueue(("mkdir", remote_top))
        n = 0
        for root, dirs, files in os.walk(local_dir):
            dirs.sort()
            rel = os.path.relpath(root, local_dir)
            if rel in (".", ""):
                remote_dir = remote_top
            else:
                remote_dir = _posix_join(remote_top, rel.replace(os.sep, "/"))
                self._enqueue(("mkdir", remote_dir))
            for name in sorted(files):
                self._enqueue(("put", os.path.join(root, name), remote_dir + "/"))
                n += 1
        return n

    # Back-compat alias: earlier code/tests referenced _put_files.
    def _put_files(self, files):
        self._put_paths(files)

    def send_local_paths(self, paths, title="Sending to the Next…", on_done=None):
        """Public: upload local files/folders into the current Next directory as
        one tracked, cancellable operation — the host's gallery panes use this
        to route 'Send via NextSync' through a live '.sync5 -listen' session.

        Folders are recreated top-down (mkdir before the puts into it), same as
        a drag-and-drop upload. Returns "queued" once the batch is enqueued,
        "busy" while another operation is still running, "offline" when no Next
        is connected, or "empty" when nothing in *paths* exists. *on_done*
        (optional) fires on the UI thread when the batch ends, as
        ``on_done(ok, failures)``; failures have already been red-toasted by
        the widget, so callers typically only act on ok."""
        if not self._connected:
            return "offline"
        if self._op_active:
            return "busy"
        paths = [p for p in (paths or []) if p and os.path.exists(p)]
        if not paths:
            return "empty"
        self._run_op(title, lambda: self._put_paths(paths), on_done=on_done)
        return "queued"

    def remote_cwd(self):
        """The Next directory currently shown ("/" until a listing arrived).
        Gallery sends land here, so the host reports it in logs/toasts."""
        return self._cwd or "/"

    # ==================================================================
    #  remote zip / unzip  (PC-side: the Next only moves the bytes)
    # ==================================================================
    # A dot command cannot run another dot (.unzip would be loaded over the
    # running .sync's own $2000 page, and the launch call itself is the
    # M_P3DOS crash trap), so both actions do the zip work ON THE PC with
    # the existing protocol verbs: download -> zipfile -> upload. The wire
    # carries the data twice; the dotN needs zero new bytes.

    def _next_listing_names(self):
        """Lower-cased entry names currently listed in the Next pane (to pick
        a zip name that doesn't collide with an existing one)."""
        names = set()
        for row in range(self.next_model.rowCount()):
            item = self.next_model.item(row, 0)
            p = item.data(RE_PATH_ROLE) if item is not None else None
            if p and p != "..":
                names.add((posixpath.basename(p.rstrip("/")) or "").lower())
        return names

    def _cwd_base(self):
        return self._cwd if self._cwd.endswith("/") else self._cwd + "/"

    # ---- Remote Unzip file -------------------------------------------
    def _emulator_start_from_next(self, remote_path, entry):
        """Boot a file that lives ON THE NEXT in an emulator.

        The emulator runs on the PC and knows nothing about the Next's storage,
        so the file is downloaded first (same 'get' op as the Download action)
        and the emulator started on that copy — the Next pane's counterpart to
        the SD Card tab extracting from the image with hdfmonkey.

        It lands in the LOCAL pane's current folder — exactly where the
        Download action puts things — so it appears in the left explorer and
        the user keeps it, instead of disappearing into a temp directory. The
        file is deliberately NOT deleted afterwards: it is the user's now, and
        the emulator is launched detached and opens it after we return.

        NO disk image is involved and none is required. MAME loads a snapshot
        with no -hard1, and CSpect takes a plain folder as its -mmc root (its
        own sample launchers use `-mmc=./`), so the downloaded file's folder
        serves as the card root.
        """
        if not self._connected or self._busy_refused():
            return
        name = posixpath.basename(remote_path.rstrip("/")) or remote_path
        # Only asks about things that genuinely stop a launch — booting a file
        # needs no mounted image, so this does not fire for a missing SD card.
        blocked = entry.blocked()
        if blocked:
            self._log(blocked)
            self._on_toast(
                ui_tr_now("Could not start {emulator}").format(emulator=entry.name),
                blocked, "red")
            return

        # The local pane's folder, same as Download. Only if it cannot be
        # written to (read-only media, permissions) does this fall back to the
        # emulator's own staging directory.
        dest = self._local_dir()
        scratch = False
        if not (dest and os.path.isdir(dest) and os.access(dest, os.W_OK)):
            try:
                dest = entry.staging_dir()
                scratch = True
            except OSError as exc:
                logging.exception("emulator staging dir unusable")
                self._on_toast(
                    ui_tr_now("Could not start {emulator}").format(emulator=entry.name),
                    ui_tr_now("Could not prepare a folder for {name}: {error}")
                    .format(name=name, error=exc), "red")
                return

        def _started(ok, _fails):
            local = os.path.join(dest, name)
            if ok and not os.path.isfile(local):
                # The worker names the arriving file from what the NEXT
                # reports, not from the path we asked for (_re_relname_under),
                # so a single-file get can land under a sub-path. Look for it
                # by name — never by "the only file here", which was safe in a
                # private staging dir but not in the user's own folder.
                found = [os.path.join(root, f)
                         for root, _dirs, files in os.walk(dest)
                         for f in files if f == name]
                if found:
                    local = max(found, key=os.path.getmtime)
            if not ok or not os.path.isfile(local):
                # Only ever remove a directory WE made. dest is normally the
                # user's own browsing folder, and deleting that would be
                # catastrophic.
                if scratch:
                    shutil.rmtree(dest, ignore_errors=True)
                self._log(ui_tr_now(
                    "Start {emulator}: {name} could not be downloaded from "
                    "the Next, {emulator} was not started.").format(
                        emulator=entry.name, name=name))
                self._on_toast(
                    ui_tr_now("Could not start {emulator}").format(
                        emulator=entry.name),
                    ui_tr_now("Start {emulator}: {name} could not be "
                              "downloaded from the Next, {emulator} was not "
                              "started.").format(emulator=entry.name, name=name),
                    "red")
                return
            self._local_refresh()      # show what just arrived
            # Deferred so _end_operation fully unwinds before the launch.
            QTimer.singleShot(0, lambda: entry.launch(local))

        self._log(ui_tr_now(
            "Downloading {name} from the Next, then starting {emulator}…")
            .format(name=name, emulator=entry.name))
        self._run_op(ui_tr_now("Downloading {name}…").format(name=name),
                     lambda: self._enqueue(("get", remote_path, dest)),
                     on_done=_started)

    def _remote_unzip(self, zip_path):
        """Context menu 'Remote Unzip file' (a single selected remote .zip):
        stage 1 downloads the zip to a temp dir (normal cancellable op),
        stage 2 extracts it locally (own progress dialog + Cancel), stage 3
        uploads the extracted tree back into the zip's folder. Every abort
        path removes the temp dir and leaves the Next untouched."""
        if not self._connected or self._busy_refused():
            return
        tmp = tempfile.mkdtemp(prefix="zxnu_runzip_")
        name = posixpath.basename(zip_path.rstrip("/"))
        self._log(f"Remote unzip: fetching {zip_path} …")

        def stage2(ok, _fails):
            # Deferred so _end_operation fully unwinds before the next stage.
            QTimer.singleShot(0, lambda: self._remote_unzip_extract(
                ok, tmp, os.path.join(tmp, name), name))

        self._run_op("Remote Unzip: downloading the zip…",
                     lambda: self._enqueue(("get", zip_path, tmp)),
                     on_done=stage2)

    def _remote_unzip_extract(self, ok, tmp, local_zip, name):
        if not ok or not os.path.isfile(local_zip):
            shutil.rmtree(tmp, ignore_errors=True)
            self._log("Remote unzip: download failed or was cancelled — "
                      "nothing changed on the Next.")
            return
        extract_dir = os.path.join(tmp, "_extracted")
        os.makedirs(extract_dir, exist_ok=True)
        res = zip_extract_with_dialog(self.window(), local_zip, extract_dir,
                                      log=self._log)
        files, skipped = res["files"], res["skipped"]
        total_bytes = res["bytes"]
        if not res["ok"] or files == 0:
            shutil.rmtree(tmp, ignore_errors=True)
            if res["cancelled"]:
                self._log("Remote unzip: cancelled — nothing changed on "
                          "the Next.")
            elif res["error"]:
                self._on_toast("Remote unzip failed",
                               ui_tr_now("Could not extract {name}: {error}")
                               .format(name=name, error=res["error"]),
                               "red")
            else:
                self._on_toast("Remote unzip",
                               ui_tr_now("{name} contains no extractable "
                                         "files.").format(name=name),
                               "yellow")
            return
        # Will-it-fit guard against the freshest cached free-space figure
        # (re-read at the end of the download op just before this).
        free = self._free_space.get(self._cwd_drive())
        if free is not None and total_bytes > free:
            shutil.rmtree(tmp, ignore_errors=True)
            self._on_toast(
                "Remote unzip refused",
                ui_tr_now("Unzipping needs {need}, but drive {drive}: only "
                          "has {free} free.").format(
                              need=self._fmt_free(total_bytes),
                              drive=self._cwd_drive(),
                              free=self._fmt_free(free)),
                "red")
            return
        base = self._cwd_base()

        def go():
            for entry in sorted(os.listdir(extract_dir)):
                full = os.path.join(extract_dir, entry)
                if os.path.isdir(full):
                    self._enqueue_dir_upload(full, base)
                else:
                    self._enqueue(("put", full, base))

        def done(ok2, _fails2):
            shutil.rmtree(tmp, ignore_errors=True)
            if ok2:
                extra = (" (" + ui_tr_now("{skipped} unsafe entries skipped")
                         .format(skipped=skipped) + ")" if skipped else "")
                self._on_toast("✅  Remote unzip complete",
                               ui_tr_now("Extracted {files} file(s) from "
                                         "{name} into {cwd}.").format(
                                             files=files, name=name,
                                             cwd=self._cwd) + extra,
                               "green")

        self._log(f"Remote unzip: uploading {files} file(s) to {self._cwd} …")
        if not self._connected or self._op_active:
            shutil.rmtree(tmp, ignore_errors=True)
            self._log("Remote unzip: connection lost before the upload — "
                      "nothing changed on the Next.")
            return
        self._run_op("Remote Unzip: uploading to the Next…", go, on_done=done)

    # ---- Remote Zip ---------------------------------------------------
    def _remote_zip(self, entries):
        """Context menu 'Remote Zip': download the selected remote files /
        folders, zip them on the PC, and upload the zip back into the current
        Next folder. The zip is named after the FIRST selected item + '.zip'
        (single or multiple selection alike), uniquified against the current
        listing so nothing is overwritten."""
        if not self._connected or not entries or self._busy_refused():
            return
        first = posixpath.basename(entries[0][0].rstrip("/")) or "archive"
        zip_name = zip_unique_name(first, self._next_listing_names())
        tmp = tempfile.mkdtemp(prefix="zxnu_rzip_")
        dl = os.path.join(tmp, "dl")
        os.makedirs(dl, exist_ok=True)

        def go():
            for path, is_dir in entries:
                self._prepare_local_download_dir(dl, path, is_dir)
                self._enqueue(("get", path, dl))

        def stage2(ok, _fails):
            QTimer.singleShot(0, lambda: self._remote_zip_pack(
                ok, tmp, dl, zip_name))

        self._log(f"Remote zip: fetching {len(entries)} item(s) for "
                  f"{zip_name} …")
        self._run_op("Remote Zip: downloading from the Next…", go,
                     on_done=stage2)

    def _remote_zip_pack(self, ok, tmp, dl, zip_name):
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)
            self._log("Remote zip: download failed or was cancelled — "
                      "no zip was created.")
            return
        # Everything under dl/ mirrors the selection; zip its top-level
        # entries so the archive holds the items by name (folders recursed).
        src_paths = [os.path.join(dl, e) for e in sorted(os.listdir(dl))]
        zip_local = os.path.join(tmp, zip_name)
        res = zip_create_with_dialog(self.window(), src_paths, zip_local,
                                     log=self._log)
        if not res["ok"]:
            shutil.rmtree(tmp, ignore_errors=True)
            if res["cancelled"]:
                self._log("Remote zip: cancelled — no zip was uploaded.")
            elif res["error"] == "nothing to zip":
                self._on_toast("Remote zip", "Nothing was downloaded — no "
                               "zip was created.", "yellow")
            else:
                self._on_toast("Remote zip failed",
                               ui_tr_now("Could not build {zip_name}: {error}")
                               .format(zip_name=zip_name, error=res["error"]),
                               "red")
            return
        files = res["files"]
        size = os.path.getsize(zip_local)
        free = self._free_space.get(self._cwd_drive())
        if free is not None and size > free:
            shutil.rmtree(tmp, ignore_errors=True)
            self._on_toast(
                "Remote zip refused",
                ui_tr_now("{zip_name} is {size}, but drive {drive}: only "
                          "has {free} free.").format(
                              zip_name=zip_name, size=self._fmt_free(size),
                              drive=self._cwd_drive(),
                              free=self._fmt_free(free)),
                "red")
            return
        base = self._cwd_base()

        def done(ok2, _fails2):
            shutil.rmtree(tmp, ignore_errors=True)
            if ok2:
                self._on_toast("✅  Remote zip complete",
                               ui_tr_now("Created {zip_name} in {dest} "
                                         "({files} file(s), {size}).").format(
                                             zip_name=zip_name, dest=self._cwd,
                                             files=files,
                                             size=self._fmt_free(size)),
                               "green")

        self._log(f"Remote zip: uploading {zip_name} "
                  f"({self._fmt_free(size)}) to {self._cwd} …")
        if not self._connected or self._op_active:
            shutil.rmtree(tmp, ignore_errors=True)
            self._log("Remote zip: connection lost before the upload — "
                      "no zip was created on the Next.")
            return
        self._run_op("Remote Zip: uploading the zip…",
                     lambda: self._enqueue(("put", zip_local, base)),
                     on_done=done)

    # ==================================================================
    #  local pane
    # ==================================================================
    def sync_root(self):
        """The chosen sync-root folder ("" until the user picks one). The host
        gates the 'Start NextSync server' button on this."""
        return self._sync_root

    def set_local_dir(self, path):
        """Public: point the local pane's browse root at `path` (e.g. a drive
        root from the host's drive switcher). Leaves the sync root unchanged.
        Ignored if `path` isn't an existing directory.
        """
        if path and os.path.isdir(path):
            self._set_local_dir(path, commit=False)

    # -- index mapping between the filter proxy (the view) and the file model --
    def _view_ix(self, path):
        return self.local_proxy.mapFromSource(self.local_model.index(path))

    def _path_of(self, view_ix):
        return self.local_model.filePath(self.local_proxy.mapToSource(view_ix))

    def _is_local_updir(self, view_ix):
        """True if the view index is the ".." parent-directory row."""
        return self.local_model.fileName(
            self.local_proxy.mapToSource(view_ix)) == ".."

    def _browse_dir(self):
        """The folder the tree is rooted at (used by Up / Refresh / New Folder)."""
        return self._browse_root or QDir.homePath()

    def _local_dir(self):
        """Where downloads land: the sync root once chosen, else the browse root."""
        return self._sync_root or self._browse_dir()

    def _on_local_drive_picked(self, _index=None):
        """The local drive switcher: browse that drive's root. Navigation
        only — the sync root is left alone, exactly as the classic tab's
        switcher behaved."""
        letter = self.local_drive_combo.currentText()
        if letter:
            self.set_local_dir(letter)

    def _sync_local_drive_combo(self, path):
        """Keep the drive combo showing the drive actually being browsed,
        however the pane got there (Up, a double-click, a restored path).
        Silent: setCurrentIndex does not emit `activated`, so this cannot
        loop back into _on_local_drive_picked."""
        if self.local_drive_combo.count() < 1 or not path:
            return
        drive = os.path.splitdrive(path.replace("/", os.sep))[0]
        if not drive:
            return
        want = os.path.normcase(drive.rstrip("\\/"))
        for i in range(self.local_drive_combo.count()):
            item = self.local_drive_combo.itemText(i)
            if os.path.normcase(item.rstrip("\\/")) == want:
                if self.local_drive_combo.currentIndex() != i:
                    self.local_drive_combo.setCurrentIndex(i)
                return

    def _set_local_dir(self, path, commit=True):
        """Point the browse root at `path`. When `commit`, also make it the sync
        root (a typed path or the restored saved path — plain navigation passes
        commit=False and leaves the sync root alone)."""
        path = path.replace("\\", "/") if path else path
        self.local_view.setRootIndex(self._view_ix(path))
        self._browse_root = path
        self._sync_local_drive_combo(path)
        if commit:
            self._commit_sync_root(path)
        self._update_set_syncroot_button()

    def _commit_sync_root(self, path):
        """Record `path` as the sync root, show it in the path box, and notify the
        host (which enables the Start button)."""
        norm = (path or "").replace("\\", "/").rstrip("/")
        if not norm or not os.path.isdir(norm):
            return
        self._sync_root = norm
        if self.local_path_edit.text() != norm:
            self.local_path_edit.setText(norm)
        self._on_sync_root_changed(norm)
        self._update_set_syncroot_button()

    def _update_set_syncroot_button(self):
        """Offer "Set current folder as new sync root folder" only while the
        browsed folder differs from the committed sync root — pulsing green
        while it is on screen, like the classic sync view's button."""
        cur = (self._browse_root or "").replace("\\", "/").rstrip("/")
        root = (self._sync_root or "").rstrip("/")
        same = (cur != "" and root != "" and
                os.path.normcase(cur) == os.path.normcase(root))
        visible = cur != "" and not same
        self.local_set_syncroot_button.setVisible(visible)
        self._set_syncroot_pulse(visible)

    def _set_syncroot_pulse(self, on):
        """Start/stop the green pulse on the set-sync-root offer (same look
        as the host's server-start pulses: stylesheet alpha driven by a
        triangle wave on a 55 ms QTimer)."""
        if not on:
            if self._syncroot_pulse_timer is not None:
                self._syncroot_pulse_timer.stop()
                self._syncroot_pulse_timer = None
            try:
                self.local_set_syncroot_button.setStyleSheet("")
            except RuntimeError:
                pass
            return
        if self._syncroot_pulse_timer is not None:
            return                       # already pulsing
        steps = 22
        phase = {"n": 0}
        r, g, b, fg = 46, 204, 113, "#eafff0"

        def _tick():
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            pos = phase["n"]
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            a = int(70 + 150 * tri)
            try:
                self.local_set_syncroot_button.setStyleSheet(
                    "QPushButton { color: %s; font-weight: bold;"
                    " padding: 4px 10px; border-radius: 6px;"
                    " background-color: rgba(%d,%d,%d,%d);"
                    " border: 1px solid rgba(%d,%d,%d,%d); }" % (
                        fg, r, g, b, a, r, g, b, min(a + 60, 255)))
            except RuntimeError:
                pass
        timer = QTimer(self)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        self._syncroot_pulse_timer = timer

    def _on_set_syncroot_clicked(self):
        folder = self._browse_dir()
        if not (folder and os.path.isdir(folder)):
            return
        if QMessageBox.question(
                self, ui_tr_now("Set sync root"),
                ui_tr_now("Set this folder as the new sync root?") + "\n\n" + folder,
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes) == QMessageBox.Yes:
            self._commit_sync_root(folder)

    def _local_filter_changed(self, text):
        self.local_proxy.setFilterFixedString((text or "").strip())

    def _on_path_edit(self):
        new = self.local_path_edit.text().strip()
        if new and os.path.isdir(new):
            self._set_local_dir(new, commit=True)
        else:
            # Restore the last valid sync root (empty falls back to placeholder).
            self.local_path_edit.setText(self._sync_root)

    def _local_up(self):
        cur = self._browse_dir()
        parent = os.path.dirname(cur.rstrip("/\\"))
        if parent and os.path.isdir(parent):
            self._set_local_dir(parent, commit=False)

    def _local_refresh(self):
        """Force the local pane to re-read the current folder from disk.

        Mirrors the Next pane's Refresh. QFileSystemModel usually auto-updates
        via its file-system watcher, but bouncing the root path guarantees an
        immediate rescan (e.g. right after files land from a download).
        """
        cur = self._browse_dir()
        self.local_model.setRootPath("")          # bounce so an unchanged path rescans
        self.local_model.setRootPath(cur or "")
        if cur and os.path.isdir(cur):
            self.local_view.setRootIndex(self._view_ix(cur))

    def _local_double_clicked(self, index):
        # Pure navigation: double-clicking a folder (or "..") only changes the
        # folder being browsed. The sync root is only changed via the "Set
        # current folder as new sync root folder" button or by typing a folder
        # path into the box below.
        if self._is_local_updir(index):
            self._local_up()             # ".." goes one level up
            return
        path = self._path_of(index)
        if os.path.isdir(path):
            self._set_local_dir(path, commit=False)

    def _selected_local_paths(self):
        out = []
        for ix in self.local_view.selectionModel().selectedRows(0):
            if self._is_local_updir(ix):     # never act on the ".." up-entry
                continue
            p = self._path_of(ix)
            if p:
                out.append(p)
        return out

    def _local_key_press(self, event):
        # Ctrl+C / Ctrl+X copy or cut the local selection; Ctrl+V pastes the
        # clipboard here (Next items download, local items copy/move). Delete /
        # F2 act on the local pane, mirroring the Next pane.
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_local("copy")
            return
        if event.matches(QKeySequence.StandardKey.Cut):
            self._copy_local("cut")
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste_into_local()
            return
        if event.key() == Qt.Key.Key_Delete:
            self._local_delete_selected()
            return
        if event.key() == Qt.Key.Key_F2:
            self._local_rename_selected()
            return
        QTreeView.keyPressEvent(self.local_view, event)

    def _local_context_menu(self, pos):
        # Right-click menu for the local pane, mirroring the Next pane and the
        # SD Card tab's local explorer: New Folder / Copy / Cut / Paste / Rename
        # / Delete / Refresh. Dialogs are shown after menu.exec() returns, so the
        # menu's modal grab is already released.
        sel = [p for p in self._selected_local_paths() if p]
        has_sel = len(sel) > 0
        menu = QMenu(self)
        # "Start <emulator> with <file>" at the top, for a single selected file
        # the emulator can boot. These paths are already local, so they go
        # straight to the launcher.
        emu_local = []
        if len(sel) == 1 and os.path.isfile(sel[0]):
            for entry in self._emulator_entries(sel[0]):
                emu_local.append((menu.addAction(entry.label), entry))
            if emu_local:
                menu.addSeparator()
        # "Open" hands the item to the OS shell: a .html opens in the
        # browser, a folder in the file manager. The emulator entries above
        # keep the top spot — they are the pane's own, more specific
        # openers; this is the generic one.
        act_open = menu.addAction(ui_tr_now("Open"))
        act_open.setEnabled(len(sel) == 1)
        menu.addSeparator()
        act_new = menu.addAction(ui_tr_now("New Folder…"))
        act_unzip = menu.addAction(ui_tr_now("Unzip file"))
        act_zip = menu.addAction(ui_tr_now("Zip"))
        menu.addSeparator()
        act_copy = menu.addAction(ui_tr_now("Copy"))
        act_cut = menu.addAction(ui_tr_now("Cut"))
        act_paste = menu.addAction(ui_tr_now("Paste"))
        menu.addSeparator()
        act_ren = menu.addAction(ui_tr_now("Rename…"))
        act_del = menu.addAction(ui_tr_now("Delete"))
        menu.addSeparator()
        act_ref = menu.addAction(ui_tr_now("Refresh"))
        act_copy.setEnabled(has_sel)
        act_cut.setEnabled(has_sel)
        act_ren.setEnabled(len(sel) == 1)
        act_del.setEnabled(has_sel)
        # Local zip actions mirror the Next pane's Remote Zip/Unzip: "Unzip
        # file" only for a single selected local .zip, "Zip" for any selection.
        act_unzip.setVisible(len(sel) == 1 and os.path.isfile(sel[0])
                             and sel[0].lower().endswith(".zip"))
        act_zip.setVisible(has_sel)
        # Paste here downloads copied/cut Next items into this local folder, or
        # copies/moves copied/cut LOCAL items into it.
        act_paste.setEnabled(bool(self._clip))
        chosen = menu.exec(self.local_view.viewport().mapToGlobal(pos))
        for act, entry in emu_local:
            if chosen == act:
                QTimer.singleShot(0, lambda e=entry, p=sel[0]: e.launch(p))
                return
        if chosen == act_open:
            self._local_open_selected()
        elif chosen == act_new:
            self._local_new_folder()
        elif chosen == act_unzip:
            self._local_unzip(sel[0])
        elif chosen == act_zip:
            self._local_zip(sel)
        elif chosen == act_copy:
            self._copy_local("copy")
        elif chosen == act_cut:
            self._copy_local("cut")
        elif chosen == act_paste:
            self._paste_into_local()
        elif chosen == act_ren:
            self._local_rename_selected()
        elif chosen == act_del:
            self._local_delete_selected()
        elif chosen == act_ref:
            self._local_refresh()

    def _local_open_selected(self):
        """Context-menu 'Open': the selected local item goes to the OS shell
        (its associated application, or the file manager for a folder)."""
        sel = [p for p in self._selected_local_paths() if p]
        if len(sel) != 1:
            return
        if not open_path_with_system_shell(sel[0]):
            self._log(f"Open: the system could not open {sel[0]}.")

    def _local_unzip(self, zip_path):
        """Local pane 'Unzip file': extract a local .zip into its own folder
        (cancellable, per-file progress; unsafe entries skipped). A cancel
        keeps what was already extracted."""
        name = os.path.basename(zip_path)
        dest = os.path.dirname(zip_path) or "."
        res = zip_extract_with_dialog(self.window(), zip_path, dest,
                                      log=self._log)
        if res["cancelled"]:
            self._log(f"Unzip of {name} cancelled — already-extracted "
                      "files remain.")
        elif res["error"]:
            self._on_toast("Unzip failed",
                           ui_tr_now("Could not extract {name}: {error}")
                           .format(name=name, error=res["error"]), "red")
        else:
            skipped = res["skipped"]
            extra = (" (" + ui_tr_now("{skipped} unsafe entries skipped")
                     .format(skipped=skipped) + ")" if skipped else "")
            self._on_toast("✅  Unzip complete",
                           ui_tr_now("Extracted {files} file(s) from {name} "
                                     "into {cwd}.").format(
                                         files=res["files"], name=name,
                                         cwd=dest) + extra, "green")
        self._local_refresh()

    def _local_zip(self, paths):
        """Local pane 'Zip': zip the selection into <first item's name>.zip
        next to it (uniquified against the folder), cancellable with per-file
        progress."""
        paths = [p for p in paths if p and os.path.exists(p)]
        if not paths:
            return
        first = os.path.basename(paths[0].rstrip("/\\")) or "archive"
        dest = os.path.dirname(os.path.abspath(paths[0].rstrip("/\\"))) or "."
        try:
            taken = {n.lower() for n in os.listdir(dest)}
        except OSError:
            taken = set()
        zip_name = zip_unique_name(first, taken)
        zip_local = os.path.join(dest, zip_name)
        res = zip_create_with_dialog(self.window(), paths, zip_local,
                                     log=self._log)
        if res["cancelled"]:
            self._log(f"Zip cancelled — {zip_name} was not created.")
        elif res["error"]:
            self._on_toast("Zip failed",
                           ui_tr_now("Could not create {zip_name}: {error}")
                           .format(zip_name=zip_name, error=res["error"]),
                           "red")
        else:
            self._on_toast("✅  Zip complete",
                           ui_tr_now("Created {zip_name} in {dest} "
                                     "({files} file(s)).").format(
                                         zip_name=zip_name, dest=dest,
                                         files=res["files"]), "green")
        self._local_refresh()

    def _local_new_folder(self):
        base = self._browse_dir()
        if not base or not os.path.isdir(base):
            return
        name, ok = QInputDialog.getText(self, ui_tr_now("New Folder"), ui_tr_now("New folder in {path}:").format(path=base))
        name = (name or "").strip()
        if not ok or not name:
            return
        if "/" in name or "\\" in name:
            self._log("New folder: enter a name only, not a path.")
            return
        target = os.path.join(base, name)
        if os.path.exists(target):
            self._log(f"New folder: '{name}' already exists.")
            return
        try:
            os.makedirs(target)
            self._log(f"Created folder {target}")
            self._local_refresh()
        except OSError as ex:
            self._log(f"New folder failed: {ex}")

    def _local_delete_selected(self):
        paths = [p for p in self._selected_local_paths()
                 if p and os.path.exists(p)]
        if not paths:
            return
        names = "\n".join(paths)
        if QMessageBox.question(self, ui_tr_now("Delete"),
                                ui_tr_now("Delete from the local disk?") + f"\n\n{names}") != QMessageBox.Yes:
            return
        for p in paths:
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                self._log(f"Deleted {p}")
            except OSError as ex:
                self._log(f"Delete failed: {p}: {ex}")

    @staticmethod
    def _unique_copy_target(dest, name):
        """A free path for ``name`` inside ``dest``: the name itself if unused,
        else "name - Copy", "name - Copy (2)", … (Explorer-style), so a paste
        into the source's own folder duplicates instead of overwriting."""
        target = os.path.join(dest, name)
        if not os.path.exists(target):
            return target
        stem, ext = os.path.splitext(name)
        n = 1
        while True:
            suffix = " - Copy" if n == 1 else f" - Copy ({n})"
            target = os.path.join(dest, stem + suffix + ext)
            if not os.path.exists(target):
                return target
            n += 1

    def _copy_paths_into_local(self, paths, dest, move=False, dup_in_place=False):
        """Copy (or move) local files/folders into the local folder ``dest``.

        Backs both a drag-drop onto a folder and a local Copy/Cut -> Paste.
        Copies colliding with an existing name get an Explorer-style
        " - Copy" name; a same-folder copy only does that when
        ``dup_in_place`` (deliberate paste), a drag there is a no-op. Moves
        never overwrite and a same-folder move is always a no-op. Failures are
        logged and summarised in one toast.
        """
        fails = []
        done = 0
        dest_abs = os.path.normcase(os.path.abspath(dest))
        for src in paths:
            name = os.path.basename(src.rstrip("/\\")) or src
            src_abs = os.path.normcase(os.path.abspath(src))
            src_is_dir = os.path.isdir(src) and not os.path.islink(src)
            if src_is_dir and (dest_abs == src_abs
                               or dest_abs.startswith(src_abs + os.sep)):
                self._log(f"Skipped {name}: cannot copy a folder into itself.")
                fails.append(f"{name}: cannot copy a folder into itself")
                continue
            same_folder = os.path.normcase(
                os.path.dirname(src_abs.rstrip("\\/"))) == dest_abs
            if same_folder and (move or not dup_in_place):
                continue                     # already here: nothing to do
            target = self._unique_copy_target(dest, name)
            if move and os.path.basename(target) != name:
                self._log(f"Skipped {name}: already exists in {dest}.")
                fails.append(f"{name}: already exists")
                continue
            try:
                if move:
                    shutil.move(src, target)
                elif src_is_dir:
                    shutil.copytree(src, target, symlinks=True)
                else:
                    shutil.copy2(src, target)
                done += 1
                self._log(f"{'Moved' if move else 'Copied'} {src} -> {target}")
            except (OSError, shutil.Error) as ex:
                self._log(f"{'Move' if move else 'Copy'} failed: {src}: {ex}")
                fails.append(f"{name}: {ex}")
        if done:
            self._local_refresh()
        if fails:
            self._toast_failures(fails)

    def _local_rename_selected(self):
        paths = self._selected_local_paths()
        if len(paths) != 1:
            self._log("Select exactly one local item to rename.")
            return
        old = paths[0]
        old_name = os.path.basename(old.rstrip("/\\")) or old
        new_name, ok = QInputDialog.getText(
            self, ui_tr_now("Rename"), ui_tr_now("Rename '{name}' to:").format(name=old_name), text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if "/" in new_name or "\\" in new_name:
            self._log("Rename: enter a name only, not a path.")
            return
        new_path = os.path.join(os.path.dirname(old), new_name)
        if os.path.exists(new_path):
            self._log(f"Rename: '{new_name}' already exists.")
            return
        try:
            os.rename(old, new_path)
            self._log(f"Renamed {old_name} -> {new_name}")
        except OSError as ex:
            self._log(f"Rename failed: {ex}")

    # ==================================================================
    #  drag & drop
    # ==================================================================
    def _next_drag_enter(self, event):
        # Refuse the Next pane's own drags: they now ALSO carry staged file
        # URLs (for OS drops - see _next_start_drag), and accepting those here
        # would round-trip the temp copies right back onto the Next.
        #
        # The custom format is tested FIRST and hasUrls() only after: hasUrls
        # is a DATA PULL (it goes through the mime's retrieveData), and this
        # runs on every drag-move event, so asking it about our own staged
        # drag would interrogate a download still in flight from inside the
        # live drag loop. Cheap question first, and the expensive one never
        # asked for our own payload.
        md = event.mimeData()
        self._drag_hover += 1        # our pane owns the cursor right now
        if (self._connected
                and not md.hasFormat("application/x-zxnu-next-entries")
                and md.hasUrls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _pane_drag_leave(self, event):
        """The cursor left one of our panes (or a drag ended on it): the
        payload may be asked for its files again, and that question is no
        longer a mid-drag one. See _drag_over_own_panes."""
        self._drag_hover = 0
        event.accept()

    def _drag_over_own_panes(self):
        """True while a drag is hovering one of THIS widget's panes.

        Set from the panes' own drag handlers, so it needs no OS state and
        no guessing: any payload question asked while this holds comes from
        our own hover handling (Qt asks on every move), never from a drop
        being performed — so the lazy mime must answer instantly instead of
        waiting for a staging download."""
        return self._drag_hover > 0

    def _next_drop(self, event):
        # Accept both files and folders dragged from the local pane or the OS
        # file manager; folders are uploaded recursively (see _put_paths).
        # Never the Next pane's own drags (see _next_drag_enter).
        self._drag_hover = 0     # a DROP: payload questions may wait again
        if event.mimeData().hasFormat("application/x-zxnu-next-entries"):
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile() and os.path.exists(u.toLocalFile())]
        if not paths:
            event.ignore()
            return
        if self._busy_refused():
            # Refuse BEFORE accepting: an accepted-then-discarded drop shows
            # the OS a successful copy while nothing was uploaded.
            event.ignore()
            return
        event.acceptProposedAction()
        self._run_op("Uploading to the Next…", lambda: self._put_paths(paths))

    # How long a finished stage stays reusable for a re-drag of the same
    # selection (seconds). Covers "the drop landed before the download
    # finished - drag again" without re-fetching, while keeping the window
    # short enough that a selection changed ON THE NEXT between drags is
    # unlikely to be served stale.
    DRAG_STAGE_REUSE_S = 180
    # How long a RETIRED stage folder is kept before deletion. The OS file
    # manager copies out of the staged folder itself and that copy can still
    # be running - or queued behind other transfers, or sitting in a paused
    # copy dialog - long after the drop, so folders are aged out, never
    # deleted the moment the next drag replaces them.
    DRAG_STAGE_KEEP_S = 1800

    def _begin_drag_stage(self, entries):
        """Start downloading ``entries`` into a fresh temp folder for a drag
        WITHOUT blocking, and return the stage-state dict the drag's mime
        consults ({'done','ok','staged',...}), or None when staging cannot
        run right now (operation active / disconnected / precheck pending).

        The download runs as a quiet operation (no disabled widget, no
        progress dialog - the drag gesture and both panes must stay alive);
        the drag itself starts immediately and _StagedDragMime waits for
        this stage AT DROP TIME if it has to. A just-finished stage of the
        SAME selection is reused instead of re-downloading - that is what
        makes "the drop came too early, drag again" instant - and replaced
        stage folders are retired and AGED OUT (_retire_drag_stages), never
        deleted on the spot, because the OS file manager may still be
        copying out of them long after a drop."""
        key = tuple(entries)
        st = self._drag_stage
        if (st is not None and st.get('done') and st.get('ok')
                and st.get('key') == key
                and time.monotonic() - st.get('ts', 0) < self.DRAG_STAGE_REUSE_S
                and all(os.path.exists(p) for p in st.get('staged', ()))):
            return st
        if self._op_active or not self._connected or self._precheck is not None:
            return None
        self._retire_drag_stages(st)
        tmp = tempfile.mkdtemp(prefix="zxnu_drag_")
        # Where each item WILL land, known before a single byte arrives: the
        # download writes basenames into the stage folder. The drag needs
        # these immediately - a drag advertising no files reads to Windows
        # as "nothing droppable here" and Explorer shows the no-drop cursor
        # long before the user releases (see _StagedDragMime).
        expect = [os.path.join(tmp, posixpath.basename(p.rstrip("/")) or "item")
                  for p, _d in entries]
        st = {'key': key, 'dir': tmp, 'expect': expect, 'staged': [],
              'done': False, 'ok': False, 'ts': 0.0}
        self._drag_stage = st

        def go():
            for path, is_dir in entries:
                self._prepare_local_download_dir(tmp, path, is_dir)
                self._enqueue(("get", path, tmp))
            self._log(f"Drag: fetching {len(entries)} item(s) for the drop …")

        def done(ok, _fails):
            st['staged'] = ([os.path.join(tmp, e) for e in sorted(os.listdir(tmp))]
                            if ok and os.path.isdir(tmp) else [])
            st['ok'] = bool(ok and st['staged'])
            st['ts'] = time.monotonic()
            st['done'] = True
            if not st['ok']:
                self._log("Drag: nothing staged - a drop will carry no files.")

        self._run_op("Downloading from the Next…", go, on_done=done,
                     quiet_ui=True)
        if not self._op_active and not st['done']:
            # _run_op declined to start (raced by another operation): fail the
            # stage so a drop never waits for a download that never began.
            st['done'] = True
        return st

    def _invalidate_drag_stage(self):
        """Drop the drag-stage reuse cache. The staged FOLDER is only retired,
        never deleted here: an in-flight drag's mime resolves from its own
        stage dict, and the OS may still be copying out of a just-dropped
        one. Called whenever staged content could go stale — every
        connection transition, and every non-staging operation (it may have
        written the very files a cached stage holds pre-write copies of)."""
        if self._drag_stage is not None:
            self._retire_drag_stages(self._drag_stage)
            self._drag_stage = None

    def _retire_drag_stages(self, st):
        """Move stage folder ``st`` (may be None) onto the retirement list,
        delete retired folders old enough that no OS-side copy can still be
        reading them, and - once per session - sweep day-old zxnu_drag_
        leftovers of PREVIOUS runs (nothing can clean a stage folder at app
        exit: the shell may still own it)."""
        now = time.monotonic()
        if st is not None and st.get('dir'):
            self._drag_stage_hist.append({'dir': st['dir'], 'ts': now})
        keep = []
        for h in self._drag_stage_hist:
            if now - h['ts'] < self.DRAG_STAGE_KEEP_S:
                keep.append(h)
            else:
                shutil.rmtree(h['dir'], ignore_errors=True)
        self._drag_stage_hist = keep
        if not self._drag_swept:
            self._drag_swept = True
            cutoff = time.time() - 86400
            try:
                for name in os.listdir(tempfile.gettempdir()):
                    if not name.startswith("zxnu_drag_"):
                        continue
                    p = os.path.join(tempfile.gettempdir(), name)
                    try:
                        if os.path.getmtime(p) < cutoff:
                            shutil.rmtree(p, ignore_errors=True)
                    except OSError:
                        logging.exception("drag-stage sweep: %s", p)
            except OSError:
                logging.exception("drag-stage sweep failed")

    def _await_drag_stage(self, st):
        """The staged local paths for ``st``, waiting (bounded) for a staging
        download still in flight. Called from the drag mime's retrieveData -
        i.e. from inside the target's drop - so it pumps queued events (the
        worker's signals, the staging operation's end) but never user input."""
        deadline = time.monotonic() + self._drag_stage_wait_ms / 1000.0
        while not st['done'] and time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            time.sleep(0.02)
        if not st['done']:
            self._log("Drag: the drop timed out waiting for the download - "
                      "it keeps running; drag again once it has finished.")
            return []
        return list(st['staged']) if st.get('ok') else []

    def _next_start_drag(self, supported):
        # Drag Next entries to the local pane / OS to download them.
        entries = self._selected_next_entries()
        if not entries:
            return
        # The gesture must never wait for the download (the user releases
        # over the target within a second or two; a UART fetch takes longer),
        # so the staging download starts in the background and the drag
        # starts NOW, with _StagedDragMime handing the URLs over lazily at
        # drop time. The custom payload lets the local pane treat a drop as
        # "download here" even when staging could not run.
        st = self._begin_drag_stage(entries)
        mime = _StagedDragMime(self, st) if st is not None else QMimeData()
        mime.setData("application/x-zxnu-next-entries",
                     "\n".join(f"{'D' if d else 'F'}\t{p}" for p, d in entries).encode())
        if st is not None:
            # Name the staged files RIGHT NOW (they may still be downloading
            # - see _StagedDragMime): Windows asks whether we can produce
            # files while the cursor merely hovers a folder, and answers
            # that question with the drop cursor. Serving the finished
            # stage's real paths when we have them keeps a cache re-drag
            # exact.
            paths = st['staged'] if st.get('ok') else st.get('expect', [])
            mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self.next_view)
        drag.setMimeData(mime)
        self._drag_hover = 0     # never inherit a previous drag's count
        try:
            drag.exec(Qt.CopyAction)
        finally:
            # The gesture is over however it ended (dropped elsewhere,
            # cancelled, or left the window without a leave event).
            self._drag_hover = 0

    def _local_start_drag(self, supported):
        # Explicit drag-out with real text/uri-list URLs (never the ".." row),
        # so the selection can be dropped on the Next pane, the SD Card tab's
        # explorers or the OS file manager - mirroring the SD Card tab's local
        # explorer (_local_start_drag in zxnu_main).
        paths = [p for p in self._selected_local_paths() if os.path.exists(p)]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self.local_view)
        drag.setMimeData(mime)
        self._drag_hover = 0
        try:
            drag.exec(Qt.CopyAction)
        finally:
            self._drag_hover = 0

    def _local_drag_enter(self, event):
        # Next-pane entries (download here) or file URLs -- from the local pane
        # itself or the OS file manager (copy into the folder dropped on).
        # The custom format is tested first on purpose: hasUrls() is a data
        # pull, and short-circuiting keeps our own staged drag from being
        # interrogated mid-hover (see _next_drag_enter).
        self._drag_hover += 1        # our pane owns the cursor right now
        if (event.mimeData().hasFormat("application/x-zxnu-next-entries")
                or event.mimeData().hasUrls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _local_drop_dir(self, event):
        """The local folder a drop lands in: the folder row under the cursor,
        else the pane's current folder (the ".." row counts as current too)."""
        ix = self.local_view.indexAt(event.position().toPoint())
        if ix.isValid() and not self._is_local_updir(ix):
            p = self._path_of(ix)
            if p and os.path.isdir(p):
                return p
        return self._local_dir()

    def _local_drop(self, event):
        # A DROP, not a hover: the staged-URL read below is allowed to wait
        # for a staging download again (see _drag_over_own_panes).
        self._drag_hover = 0
        data = event.mimeData().data("application/x-zxnu-next-entries")
        if not data:
            # Local/OS file drag: copy the dropped items into the folder they
            # were dropped on (a drop back into their own folder is a no-op).
            paths = [u.toLocalFile() for u in event.mimeData().urls()
                     if u.isLocalFile() and os.path.exists(u.toLocalFile())]
            if not paths:
                event.ignore()
                return
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self._copy_paths_into_local(paths, self._local_drop_dir(event))
            return
        event.acceptProposedAction()
        # A Next-pane drag also carries file URLs - the selection, staged to
        # a temp folder by a background download (that is what makes OS drops
        # work - see _next_start_drag / _StagedDragMime). Reading urls() here
        # WAITS for that download if it is still running (the lazy mime's
        # retrieveData), then the staged copies are used instead of
        # downloading a second time; the entry payload below stays the
        # fallback for a drag whose staging failed or never started.
        staged = [u.toLocalFile() for u in event.mimeData().urls()
                  if u.isLocalFile() and os.path.exists(u.toLocalFile())]
        if staged:
            self._copy_paths_into_local(staged, self._local_drop_dir(event))
            return
        dest = self._local_dir()
        # Each line is "<D|F>\t<path>"; keep the dir flag so folders (empty ones
        # in particular) are recreated locally, not silently dropped.
        entries = []
        for line in bytes(data).decode(errors="replace").splitlines():
            if "\t" in line:
                flag, path = line.split("\t", 1)
                entries.append((path, flag == "D"))
        if not entries:
            return
        if self._busy_refused():
            # Reachable since quiet staging ops (a previous drag's download
            # still running) stopped disabling the panes.
            return

        def go():
            for path, is_dir in entries:
                self._prepare_local_download_dir(dest, path, is_dir)
                self._enqueue(("get", path, dest))
            self._log(f"Downloading {len(entries)} item(s) to {dest} …")
        self._run_op("Downloading from the Next…", go)
