"""Background worker, signal and progress-dialog classes for zx-next-unite.

Extracted from zx-next-unite.py."""

import errno
import os
import queue
import datetime
import fnmatch
import glob
import logging
import platform
import shutil
import socket

# The ident reply separator (the version query): type NUL number.
NULB = bytes([0])
import struct
import tempfile
import threading
import time
import weakref
import zlib
from collections import deque, namedtuple
from zxnu_config import (IGNOREFILE, MAX_PAYLOAD, PORT, SYNCPOINT,
                         TREE_FONT_MAX_PT, TREE_FONT_MIN_PT,
                         UP_DIRECTORY, VERSION3, VERSION4,
                         cspect_can_autostart, emulator_offers_autostart,
                         log_size,
                         is_filetype_a_directory, mame_autostart_staging_dir,
                         mame_can_autostart)
from PySide6.QtCore import (
    QEvent, QItemSelection, QItemSelectionModel, QObject, QPoint, QRect,
    QRunnable, QSize, QSortFilterProxyModel, QTimer, Qt, Signal, Slot,
    QRegularExpression,
)
from PySide6.QtGui import QFontInfo
# ui_tr_now translates the USER-FACING server log lines at their call sites
# (session status, transfer progress, guidance). Protocol diagnostics —
# packet/checksum/sequence/version lines — stay English on purpose so a log
# pasted into a bug report still matches the docs and the .dot source.
from zxnu_i18n import ui_tr_now
from PySide6.QtWidgets import (
    QDialog, QFileSystemModel, QHBoxLayout, QLabel, QLayout, QProgressBar,
    QPushButton, QVBoxLayout,
)


class _TreeFontZoomFilter(QObject):
    """Event filter behind bind_tree_font_zoom: Ctrl + mouse-wheel over an
    explorer tree (or its header) grows/shrinks the view's item font one
    point per notch. Trackpad deltas are accumulated until a full 120-unit
    notch, so a soft two-finger scroll can't race through sizes."""

    def __init__(self, tree, persist):
        super().__init__(tree)
        self._tree = tree
        self._persist = persist
        self._acc = 0

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Type.Wheel or not (
                ev.modifiers() & Qt.ControlModifier):
            return False
        self._acc += ev.angleDelta().y()
        steps = int(self._acc / 120)
        if steps:
            self._acc -= steps * 120
            f = self._tree.font()
            cur = f.pointSize()
            if cur <= 0:                      # pixel-sized font: resolve it
                cur = QFontInfo(f).pointSize()
            new = max(TREE_FONT_MIN_PT, min(TREE_FONT_MAX_PT, cur + steps))
            if new != cur:
                f.setPointSize(new)
                self._tree.setFont(f)
                if self._persist is not None:
                    try:
                        self._persist(new)
                    except Exception:
                        logging.exception("tree font zoom: persist failed")
        return True          # consume: a Ctrl+wheel never also scrolls


def bind_tree_font_zoom(tree, persist=None):
    """Ctrl + mouse-wheel zooms an explorer QTreeView's item font live.

    Wheel-up grows, wheel-down shrinks, clamped to
    TREE_FONT_MIN_PT..TREE_FONT_MAX_PT (zxnu_config); row heights and the
    header (which inherits the view font) follow by themselves. A plain
    wheel keeps scrolling exactly as before — only Ctrl+wheel is consumed.
    ``persist(pt)`` fires after each applied change so the host can store
    the size; the restore half is zxnu_config.apply_tree_font_pt. Returns
    the installed filter (parented to the tree; keep-alive is automatic).

    Bound on all five explorer panes: the SD Card tab's local/image trees
    (wired at the SdCardExplorerPane construction seam in zxnu_main), the
    Remote Explorer's local/Next trees and the NextSync Classic sync tree
    (9.7.39; both wired in zxnu_nextsync_pane).
    """
    filt = _TreeFontZoomFilter(tree, persist)
    tree.viewport().installEventFilter(filt)
    hdr = tree.header()
    if hdr is not None:
        hdr.viewport().installEventFilter(filt)
    return filt


class CompactButton(QPushButton):
    """A toolbar button that stays only as wide as its own label.

    Qt's style hands every QPushButton the same ~80 px minimum sizeHint
    whatever it says, so the narrow buttons in the file explorers' navigation
    rows (Up / Refresh / + Drive) have to be capped by hand to leave the filter
    box and the path field their room. A *hard* cap is what truncated them the
    moment zxnu_i18n swapped in a longer translation — "Up" becomes "W górę" /
    "Вверх", "Refresh" becomes "Обновить" — so the cap is derived from the text
    instead, and re-derived on every change that can alter its width: setText
    (how translate_widget_tree re-labels the UI) and a font or style change
    (the retro font-size setting, the app-wide stylesheet).

    Shared by the Remote Explorer (zxnu_remote_explorer) and the SD Card tab's
    explorer pair (zxnu_sdcard_explorer), whose navigation rows mirror it.
    """

    def __init__(self, text, parent=None, floor=48, padding=22):
        self._fit_floor = floor
        self._fit_padding = padding
        super().__init__(text, parent)
        self._fit_to_text()

    def setText(self, text):
        super().setText(text)
        self._fit_to_text()

    def changeEvent(self, event):
        super().changeEvent(event)
        # FontChange covers setFont; StyleChange catches the app-wide retro
        # stylesheet being (re)applied with a different font size.
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_to_text()

    def _fit_to_text(self):
        needed = self.fontMetrics().horizontalAdvance(self.text()) + self._fit_padding
        self.setMaximumWidth(max(self._fit_floor, needed))


# PathHistoryCombo / FolderHistoryCombo - the shared editable path boxes with a
# remembered dropdown - moved to zxnu_pathhistorycombo.py in 9.7.22, so this
# module stays about background work. They import CompactButton from here; do
# not import them back, or the two modules become a cycle.


# One emulator's "start this file" context-menu entry.
#   name        "CSpect" / "MAME" — for composing messages about it
#   label       the ready-to-use, already-translated menu label
#   launch      call with a HOST path to boot the file
#   staging_dir call to create and return a directory to put a host copy in,
#               when the file is not already on the PC
#   blocked     call for the reason this emulator cannot start right now, or ""
#               when it can. Callers that must fetch the file first (the Next
#               pane downloads it) check this BEFORE the transfer, so a launch
#               that cannot succeed is reported instead of silently doing
#               nothing after a download. Defaults to "never blocked" so a
#               caller can still build an entry by hand.
EmulatorAutostart = namedtuple("EmulatorAutostart",
                               "name label launch staging_dir blocked",
                               defaults=(lambda: "",))


# One emulator's bare "boot the mounted image" launch, for the two strips.
#   name     the strip label ("Mame" / "CSpect")
#   launch   call with NO argument to boot the mounted image
#   blocked  the reason it cannot start right now, or "" when it can - the
#            same string _emulator_launch_blocker hands every other surface,
#            so a strip tab and the SD Card tab's Launch button can never
#            disagree about whether a launch is possible or about why.
EmulatorLaunch = namedtuple("EmulatorLaunch", "name launch blocked",
                            defaults=("",))


def as_emulator_launch(item):
    """Coerce one entry to an EmulatorLaunch.

    The strips used to unpack a bare ``(name, launch)`` 2-tuple, and the
    widget tests still build one by hand, so accept both shapes rather than
    breaking every caller that never cared about the blocked reason.
    """
    if isinstance(item, EmulatorLaunch):
        return item
    seq = tuple(item)
    return EmulatorLaunch(*seq[:3]) if len(seq) >= 3 else EmulatorLaunch(*seq[:2])


def emulator_launch_entries(host):
    """Which emulators can be launched RIGHT NOW, as EmulatorLaunch entries.

    The bare "boot the mounted image" launch, with no file attached - the
    twin of :func:`emulator_autostart_entries` above, which answers the
    per-file question. Both explorer panes that carry the vertical
    emulator strip down their left edge (the NextSync tab's Remote
    Explorer and the SD Card tab's local pane) build it from THIS list, so
    the two strips can never disagree with each other or with the SD Card
    tab's Launch buttons.

    Both halves come from the SD Card tab's own rules: MAME through
    ``host._mame_usable()`` (which counts a Linux Flatpak setup with no
    local binary), CSpect through the detected executable path every other
    call site tests. The callables are ``host._launch_*_fn`` - the very
    functions those buttons are connected to - called with NO argument.

    An entry is listed while the emulator is INSTALLED, and carries the
    reason it cannot start right now in ``blocked`` (9.6.2). Presence and
    readiness are deliberately separate: a strip that dropped its tab when
    the disk image went busy would read as "MAME is gone", which is a
    different and wrong answer to a different question.
    """
    # The blocker spells MAME in capitals while the strip label is "Mame",
    # so the two names are passed separately rather than case-folded inside
    # the blocker (where the spelling is part of its public contract).
    _blocker = getattr(host, "_emulator_launch_blocker", None)

    def _why(emulator):
        if _blocker is None:
            return ""
        try:
            return _blocker(emulator) or ""
        except Exception:                   # noqa: BLE001
            logging.exception("emulator launch blocker failed for %s", emulator)
            return ""

    out = []
    _mame_ok = getattr(host, "_mame_usable", None)
    if (_mame_ok is not None and _mame_ok()
            and getattr(host, "_launch_mame_fn", None) is not None):
        out.append(EmulatorLaunch("Mame", host._launch_mame_fn, _why("MAME")))
    if (getattr(host, "_cspect_executable_path", None) is not None
            and getattr(host, "_launch_cspect_fn", None) is not None):
        out.append(EmulatorLaunch("CSpect", host._launch_cspect_fn,
                                  _why("CSpect")))
    return out


def emulator_autostart_entries(host, path, is_dir=False):
    """The "start <file> in an emulator" entries to offer for *path*.

    One list, used by all five explorers that can hold a bootable file (the SD
    Card tab's local and image panes, the NextSync tab's classic explorer, and
    the Remote Explorer's local and Next panes) so they offer the same entries,
    in the same order, under the same conditions. An emulator appears only when
    it is actually available AND can boot that file type.

    ``staging_dir`` is per-emulator rather than a plain ``mkdtemp()`` because
    Flatpak MAME cannot see this process's /tmp — see the long explanation on
    ``mame_autostart_staging_dir``. Callers that already hold a host path (a
    local explorer) never need it; callers that must first fetch the file (from
    the SD image, or from the Next) do.
    """
    entries = []
    # .nex only. Both emulators accept more than that in principle, but CSpect
    # crashes outright on a .tap trailing argument and MAME merely inserts a
    # tape without starting it — see EMULATOR_AUTOSTART_OFFER_EXTENSIONS.
    if is_dir or not emulator_offers_autostart(path):
        return entries
    # Split on BOTH separators regardless of platform. os.path.basename does
    # not treat "\" as one on POSIX, so a Windows-style path was labelled in
    # full there ("Start CSpect with file C:\games\beast.nex") — and the paths
    # reaching here come from the Next as well as from the local disk, so the
    # separator is not always the host's. Caught by CI on Linux; every dev
    # machine here is Windows, where it looked fine.
    _p = str(path).replace("\\", "/").rstrip("/")
    name = _p.rsplit("/", 1)[-1] or str(path)

    def _tmp(prefix):
        return lambda: tempfile.mkdtemp(prefix=prefix)

    def _blocked(emulator):
        # autostart=True: every entry here runs a FILE, which is never gated
        # on a mounted SD image.
        fn = getattr(host, "_emulator_launch_blocker", None)
        return (lambda: fn(emulator, autostart=True)) if fn else (lambda: "")

    if (cspect_can_autostart(path)
            and getattr(host, "_cspect_executable_path", None)
            and getattr(host, "_launch_cspect_fn", None)):
        entries.append(EmulatorAutostart(
            "CSpect",
            ui_tr_now("Start CSpect with file {name}").format(name=name),
            host._launch_cspect_fn, _tmp("zxnu-cspect-"), _blocked("CSpect")))

    _mame_usable = getattr(host, "_mame_usable", None)
    if (mame_can_autostart(path) and _mame_usable and _mame_usable()
            and getattr(host, "_launch_mame_fn", None)):
        def _mame_staging_dir():
            _flatpak = getattr(host, "_mame_flatpak_enabled", None)
            if not (_flatpak and _flatpak()):
                return tempfile.mkdtemp(prefix="zxnu-mame-")
            # Fixed directory under ~, cleared each time: nothing there is
            # reaped by the OS, so a fresh one per launch would leak disk.
            staged = mame_autostart_staging_dir()
            shutil.rmtree(staged, ignore_errors=True)
            os.makedirs(staged, exist_ok=True)
            return staged
        entries.append(EmulatorAutostart(
            "MAME",
            ui_tr_now("Start MAME with file {name}").format(name=name),
            host._launch_mame_fn, _mame_staging_dir, _blocked("MAME")))
    return entries


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new row when the available width
    runs out, instead of squeezing items past their minimum size.

    A plain ``QHBoxLayout`` toolbar overlaps its widgets when the window is made
    narrower than the row's combined minimum width: the box layout shrinks each
    item's allocated slot below its minimum, but ``QWidget.setGeometry`` clamps
    the widget back up to its minimum, so neighbours get drawn on top of each
    other (e.g. the Search button overlapping the search box).  Wrapping avoids
    that entirely -- items that no longer fit move to the next row.

    Adapted from the Qt "Flow Layout" example, with two additions used by the
    search/toolbar rows:

    * hidden widgets (``item.isEmpty()``) reserve no space, and
    * any item whose horizontal size policy is Expanding/MinimumExpanding grows
      to share the leftover width on its row -- the flow-layout equivalent of a
      ``QBoxLayout`` stretch factor, so a search input can still fill the bar.
    """

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=4):
        super().__init__(parent)
        self._items = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    # --- QLayout plumbing -------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if item.isEmpty():
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # --- layout core ------------------------------------------------------
    @staticmethod
    def _expanding(item):
        return bool(item.expandingDirections() & Qt.Horizontal)

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        y = area.y()
        line = []          # [(item, width, height), ...] for the current row
        line_w = 0         # widths + interior spacing accumulated so far

        def flush(line, y):
            if not line:
                return 0
            used = sum(w for _, w, _h in line) + self._hspace * (len(line) - 1)
            extra = max(0, area.width() - used)
            growers = [t for t in line if self._expanding(t[0])]
            per = extra // len(growers) if growers else 0
            x = area.x()
            line_h = 0
            for it, w, h in line:
                ww = w + (per if self._expanding(it) else 0)
                if not test_only:
                    it.setGeometry(QRect(QPoint(x, y), QSize(ww, h)))
                x += ww + self._hspace
                line_h = max(line_h, h)
            return line_h

        for item in self._items:
            if item.isEmpty():            # hidden widget -> no space reserved
                continue
            hint = item.sizeHint()
            w, h = hint.width(), hint.height()
            projected = line_w + (self._hspace if line else 0) + w
            if line and projected > area.width():
                y += flush(line, y) + self._vspace
                line, line_w = [], 0
            line.append((item, w, h))
            line_w += (self._hspace if len(line) > 1 else 0) + w
        y += flush(line, y)
        return y - rect.y() + m.bottom()


def is_address_in_use(ex):
    """True when an OSError from ``bind()`` means the TCP port is already taken.

    Covers Windows (WSAEADDRINUSE 10048, and WSAEACCES 10013 which is what an
    exclusive-use bind raises against a port another socket already holds) and
    POSIX (EADDRINUSE / EACCES). Used to turn a NextSync-server port clash into a
    friendly "another instance is probably running" warning instead of a crash.
    """
    if not isinstance(ex, OSError):
        return False
    if getattr(ex, "winerror", None) in (10048, 10013):
        return True
    return ex.errno in (errno.EADDRINUSE, errno.EACCES)


def bind_listen_socket(port):
    """Create a listening TCP socket on ``port`` (all interfaces).

    Uses SO_EXCLUSIVEADDRUSE on Windows so a second bind fails cleanly instead of
    silently "stealing" the port from another instance (SO_REUSEADDR has that
    surprising behaviour on Windows); on POSIX it uses SO_REUSEADDR so the server
    can be restarted without waiting out TIME_WAIT. Raises OSError on failure
    (``is_address_in_use`` classifies a port clash).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):        # Windows
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:                                             # POSIX
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen()
    except OSError:
        srv.close()
        raise
    return srv


# Resolved ONCE at import, like zxnu_remote_explorer's RE_*_ROLE: PySide6
# materialises enum members lazily, and lessThan/filterAcceptsRow are about
# as hot a path as this app has.
_FS_NAME_ROLE = QFileSystemModel.Roles.FileNameRole
_DISPLAY_ROLE = Qt.ItemDataRole.DisplayRole
# Measured: `x == Qt.CaseInsensitive` costs 1.2-1.9 us - the lookup through
# the Qt namespace, not the compare - which was 40% of a whole fast-path
# comparison. Resolved here, the compare is free.
_CASE_INSENSITIVE = Qt.CaseSensitivity.CaseInsensitive
_CASE_SENSITIVE = Qt.CaseSensitivity.CaseSensitive


class DotDotFirstProxyModel(QSortFilterProxyModel):
    """Proxy model that always keeps the '..' parent directory entry at the top."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The name filter has always matched case-insensitively (the old
        # substring test lower()ed both sides); saying so here makes the
        # regular expression Qt builds from setFilterWildcard carry the
        # option, so filterAcceptsRow can use it as it is.
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        # The folder the VIEW is rooted at (see set_keep_path).
        self._keep_path = ""
        self._keep_cmp = ""
        self._keep_names = set()

    # ---- the displayed folder is never filtered away --------------------
    def set_keep_path(self, path):
        """Never filter out *path* or any of its ancestors.

        *path* is the folder the view using this proxy is ROOTED at. A view
        roots itself with ``setRootIndex(proxy.mapFromSource(...))``, so that
        index has to exist; if the filter rejects the folder, the index is
        invalid and the view falls back to the proxy root - i.e. somewhere
        else entirely, usually blank.

        That was reachable by simply TYPING: with the tree on a folder whose
        own name does not match what you type (and nothing inside it matching
        either), the pane lost its place the moment the filter applied, and
        CLEARING the filter did not bring it back - a root index that has
        ceased to exist cannot be restored, so the folder was gone until the
        user navigated by hand. Measured on the SD Card pane, and the same
        hole existed in the NextSync classic explorer and the Remote
        Explorer's local pane, which share this proxy.

        Recursive filtering used to hide this by accident: a folder is
        accepted when a DESCENDANT matches, and ``filterAcceptsRow`` accepts
        the ".." row unconditionally, so a folder whose ".." child had
        already been fetched was rescued. QFileSystemModel fetches lazily and
        asynchronously, so whether that happened depended on timing - which
        is why the failure looked intermittent and why the fix cannot rely on
        it.

        The filter still applies to everything INSIDE the displayed folder;
        only the folder itself and the ancestors leading to it are exempt,
        because they are the structure the view needs rather than content the
        user is filtering.
        """
        self._keep_path = path or ""
        self._keep_cmp = self._cmp_path(self._keep_path)
        # Every row that can pass the keep test is an ancestor of the kept
        # folder, so its NAME is one of that path's components. Checking the
        # name first (the regex test needs it anyway) keeps the expensive
        # filePath() call off every other row - see filterAcceptsRow.
        #
        # A ROOT row does not report a bare component: QFileSystemModel calls
        # a Windows drive "C:/" and the POSIX root "/". Those rows are
        # ancestors of every kept folder, so leaving their spellings out of
        # the gate skipped the exemption for precisely the rows the mapping
        # down to the kept folder depends on.
        _parts = self._keep_cmp.split("/")
        self._keep_names = {c for c in _parts if c}
        if self._keep_cmp:
            self._keep_names.add((_parts[0] + "/") if _parts[0] else "/")
        self.invalidateFilter()

    def keep_path(self):
        """The folder currently exempt from the filter ("" when none)."""
        return self._keep_path

    @staticmethod
    def _cmp_path(path):
        """A path in the one spelling the ancestor test compares under.

        A bare root SURVIVES the trailing-slash strip: "/" is the POSIX
        filesystem root, and reducing it to "" turned the keep off entirely
        there - the whole fix was inert on Linux and macOS, and "/" was never
        recognised as the ancestor it is of every absolute path.
        """
        if not path:
            return ""
        p = str(path).replace("\\", "/")
        rooted = p.startswith("/")
        p = p.rstrip("/")
        if not p:
            return "/" if rooted else ""
        # Windows paths are case-insensitive; POSIX ones are not, and folding
        # them would let an unrelated sibling masquerade as the root.
        return p.lower() if os.name == "nt" else p

    def _is_kept(self, path):
        """True when *path* IS the kept folder or an ancestor of it."""
        keep = self._keep_cmp
        if not keep:
            return False
        p = self._cmp_path(path)
        if not p:
            return False
        if p == keep:
            return True
        # A prefix ending ON a separator - never a bare startswith, which
        # would make "C:/foo" keep "C:/foobar". A root already ends in one,
        # and appending a second would compare against "//".
        prefix = p if p.endswith("/") else p + "/"
        return keep.startswith(prefix)

    @staticmethod
    def _source_name(source_model, index):
        """The row's file name WITHOUT re-entering a Python data() override.

        QFileSystemModel.fileName() is an inline index.data(FileNameRole),
        so on a subclass that overrides data() in Python (all three local
        trees' ColoredFileSystemModel) every name costs a C++->Python
        crossing - and lessThan asks for two per comparison. The base-class
        call answers the same string (FileNameRole is never overridden)
        straight from the file node (9.7.39)."""
        if isinstance(source_model, QFileSystemModel):
            return QFileSystemModel.data(source_model, index,
                                         _FS_NAME_ROLE) or ""
        return source_model.fileName(index)

    def lessThan(self, left, right):
        source_model = self.sourceModel()
        left_name = self._source_name(source_model, left)
        right_name = self._source_name(source_model, right)
        if left_name == "..":
            return True
        if right_name == "..":
            return False
        # The Size column's display text is human-readable ("512 B", "2.0 K"), so
        # the default DisplayRole comparison would sort it as a string ("2.0 K"
        # before "512 B"). Compare the real byte count instead. QFileSystemModel's
        # Size is logical column 1.
        if isinstance(source_model, QFileSystemModel) and left.column() == 1:
            return source_model.size(left) < source_model.size(right)
        # Date Modified (logical column 3): compare the real QDateTime, not the
        # "yyyy-MM-dd HH:mm" display string — the string happens to sort right
        # up to the minute, but the datetime keeps seconds and never depends on
        # what the display format is changed to later.
        if isinstance(source_model, QFileSystemModel) and left.column() == 3:
            return source_model.lastModified(left) < source_model.lastModified(right)
        # Name and Type compare DISPLAY TEXT (9.7.39 fast path). Qt's own
        # comparison below reads that text through the model's virtual data()
        # - on a painted tree, two C++->Python crossings per comparison, and
        # Type's text is computed there every time (measured: a Type sort of
        # 5,223 entries 0.58 s, against 0.09 s on a plain model). A model that
        # offers sort_text() (ColoredFileSystemModel) hands the same text over
        # without re-entering data(), and the comparison is done here -
        # EXACTLY as Qt would do it, which is only knowable for ASCII:
        #  * QString::compare(Qt::CaseInsensitive) folds each UTF-16 unit with
        #    SIMPLE case folding; Python's lower() and casefold() are full
        #    mappings that differ ("İ", "ß", final sigma), and Python orders
        #    by code point where Qt orders by UTF-16 unit (they disagree
        #    between U+E000-U+FFFF and the supplementary planes). For ASCII
        #    all of these coincide: folding is A-Z -> a-z, units are code
        #    points, and a shorter prefix sorts first in both.
        #  * so any non-ASCII text - and any configuration these trees never
        #    use (another sort role, locale-aware sorting) - falls through to
        #    Qt's comparison, unchanged. tests/test_proxy_sort_fastpath.py
        #    pins the resulting order against the old one, row for row.
        # Ties return False both ways, as Qt's does, so the stable sort keeps
        # equal rows in the order the PREVIOUS sort left them (Qt stable-sorts
        # the mapping's current rows), exactly as before.
        sort_text = getattr(source_model, "sort_text", None)
        if (sort_text is not None and self.sortRole() == _DISPLAY_ROLE
                and not self.isSortLocaleAware()):
            # The names read above are handed over, so the Type column need
            # not read them a second time.
            lt = sort_text(left, left_name)
            rt = sort_text(right, right_name)
            if (isinstance(lt, str) and isinstance(rt, str)
                    and lt.isascii() and rt.isascii()):
                # Both members named: an unforeseen value from the getter
                # (another PySide6 enum representation, say) must cost speed,
                # never order - so it falls through to Qt like the other
                # guards do, rather than landing in a case-SENSITIVE compare.
                cs = self.sortCaseSensitivity()
                if cs == _CASE_INSENSITIVE:
                    return lt.lower() < rt.lower()
                if cs == _CASE_SENSITIVE:
                    return lt < rt
        return super().lessThan(left, right)

    def filterAcceptsRow(self, source_row, source_parent):
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)
        name = self._source_name(source_model, index)
        # Always show the parent-directory entry
        if name == "..":
            return True
        # Match with the regular expression the proxy holds, NOT with its
        # pattern text as a substring (9.7.2). setFilterWildcard("abc")
        # stores "(?s:abc)" on Qt 6.10 (older Qt stored the bare text), so
        # the substring test could never match a real name - and an EMPTY
        # filter, "(?s:)", rejected every row too, leaving only what
        # recursive filtering rescued through a populated ".." child. That
        # rescue depends on which folders happen to have been fetched,
        # which is how the local Refresh could land on an invalid root.
        # Unanchored, so "abc" still means "contains abc"; "*" and "?"
        # now work as well.
        rx = self.filterRegularExpression()
        if not rx.pattern() or not rx.isValid():
            return True
        # The folder the view is rooted at, and the ancestors leading down to
        # it, are structure rather than content: filtering them away destroys
        # the view's root index (see set_keep_path). The name check is a cheap
        # gate on the filePath() call, which walks the tree to build a string.
        if self._keep_names and (
                (name.lower() if os.name == "nt" else name) in self._keep_names):
            if self._is_kept(source_model.filePath(index)):
                return True
        if not (rx.patternOptions() & QRegularExpression.CaseInsensitiveOption):
            rx = QRegularExpression(rx.pattern(), rx.patternOptions()
                                    | QRegularExpression.CaseInsensitiveOption)
        return rx.match(name).hasMatch()

def root_tree_at(view, proxy, source_model, path, column=0):
    """Root *view* at *path* through *proxy*, and return whether it worked.

    Two things every caller needs and none of them used to do:

    * the proxy is told to KEEP *path* first. The root index is obtained with
      ``mapFromSource``, so the folder has to survive the name filter before
      an index for it can exist at all - navigating while a filter is typed
      would otherwise land on an invalid index. See
      :meth:`DotDotFirstProxyModel.set_keep_path`.
    * an INVALID index is not handed to ``setRootIndex``. Qt reads that as
      "root at the model root", so a folder that has been deleted or
      unplugged since used to blank the pane out instead of leaving it where
      it was.
    """
    if proxy is None or source_model is None:
        return False
    # Resolve in the SOURCE model first. Nothing may touch the keep until the
    # destination is known to exist: moving it and then failing would strip
    # the exemption from the folder still on screen and blank it - the very
    # bug this helper exists to prevent, through its own error path.
    src = source_model.index(path, column)
    if not src.isValid():
        return False
    setter = getattr(proxy, "set_keep_path", None)
    previous = proxy.keep_path() if hasattr(proxy, "keep_path") else ""
    if setter is not None:
        # Keep the MODEL's spelling, not the caller's. They differ often
        # enough to matter - an 8.3 short name, a drive letter's case, a
        # trailing slash - and a keep that does not compare equal to what
        # filePath() reports protects nothing at all.
        setter(source_model.filePath(src) or path)
    ix = proxy.mapFromSource(src)
    if not ix.isValid():
        if setter is not None:
            setter(previous)
        return False
    view.setRootIndex(ix)
    return True


def bind_select_all_except_updir(view, is_updir):
    """Make the view's Select All (Ctrl-A, or any programmatic selectAll)
    leave the ".." parent-directory row OUT of the selection: selecting
    "everything here" never means "and also the folder above" — a
    selection fed into delete/copy/drag must not smuggle the parent in.

    ``is_updir`` receives a column-0 view index (proxy-level where the
    view has a proxy) and answers whether it is the ".." row. The
    override is assigned as an instance attribute — the same duck-punch
    pattern the drag-and-drop hooks already use; Shiboken resolves
    virtual calls through the instance, so Qt's own Ctrl-A handling
    lands here too. Only top-level rows under the current root are
    checked: DotDotFirstProxyModel pins ".." there, and an expanded
    subfolder never shows one.

    The closure holds the view only WEAKLY: ``view.selectAll = closure``
    capturing ``view`` strongly would make every bound view a reference
    cycle through its own __dict__, leaving the C++/Python teardown
    order to the cycle collector — exactly the class of
    platform-dependent shutdown crash Shiboken widgets must not be
    exposed to."""
    base_select_all = type(view).selectAll
    view_ref = weakref.ref(view)

    def _select_all_except_updir():
        view = view_ref()
        if view is None:
            return
        base_select_all(view)
        model = view.model()
        root = view.rootIndex()
        last_col = max(0, model.columnCount(root) - 1)
        for row in range(model.rowCount(root)):
            ix = model.index(row, 0, root)
            if is_updir(ix):
                view.selectionModel().select(
                    QItemSelection(ix, model.index(row, last_col, root)),
                    QItemSelectionModel.Deselect)

    view.selectAll = _select_all_except_updir


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
    port_in_use = Signal(int)  # bind failed: the port is already taken


# The HTTP bridge's result sink: a command tuple whose LAST element is a
# BridgeReply is a "bridge command" — the worker fills the reply with a result
# dict INSTEAD of emitting its usual signals, so bridge traffic can never
# hijack the Remote Explorer pane (its on_listing adopts any path it is
# handed). zxnu_http_bridge imports only the stdlib at module level.
from zxnu_http_bridge import BridgeReply


class RemoteExplorerSignals(QObject):
    """Signals marshalling results of the NextSync ".sync5 -listen" remote file
    server back to the UI thread. The session runs in a worker thread; the UI
    feeds it commands via a queue and receives results through these."""
    connected    = Signal()               # a Next connected in -listen mode
    disconnected = Signal()               # the listen session ended
    # (active_sid, [(sid, address), ...]): the connected-Nexts roster,
    # emitted on every join, leave and baton switch (multi-Next, option
    # B). active_sid is the session the shared command queue currently
    # feeds; None only while no Next is connected. With Settings →
    # "NextSync — Sessions" Off (9.7.20) a Next dialing in again keeps the
    # seat's sid, so the roster it carries may not change at all — that is
    # the point: the widget keeps the pane it was driving.
    peers        = Signal(object)
    # (path, entries) where entries is a list of (is_dir: bool, size: int, name: str)
    listing      = Signal(str, object)    # result of an "ls"
    ls_failed    = Signal(str)            # an "ls" path could not be opened on the Next (gone)
    got          = Signal(str, str)       # (remote, local_path) a "get" finished
    put_done     = Signal(bool, str)      # (ok, remote) a "put" finished
    # (ok, message): terminal outcome of an ("update_dot", ...) macro — the
    # remote .sync5 self-update (stage + verify + release + swap). Fires
    # exactly once per job; progress rides the log signal. The message is
    # emit-site translated (ui_tr_now), with the step's diagnostic reason
    # left English like every other protocol diagnostic.
    # The third value (9.7.10) is the job's BRAND — "NextSync" for the
    # .sync5 dot, "ZXNextRemote" for a ZX Next Remote .nex — so the pane can
    # title the toast after the product the body names (every verdict used
    # to toast as "Remote .sync5 update", the ZXNR ones included).
    dot_update   = Signal(bool, str, str)
    # (message): the verify-after-put (Settings → Verify CRC, 9.7.3) found
    # the copy on the Next DIFFERENT from the bytes sent. Emit-site
    # translated (ui_tr_now, dot_update's shape); says whether the corrupted
    # copy was deleted. The pane prints it RED and toasts red. ALWAYS
    # followed by the put's own put_done(False, remote), so the widget's
    # one-report-per-command accounting is untouched. Unverified outcomes
    # (old listener, 'F', silence) never come here: they log and report
    # put_done(True).
    put_verify_failed = Signal(str)
    op_done      = Signal(bool, str, str) # (ok, op, path) mkdir/rmdir/rm result
    # (op, path): a WRITE was refused by the far side's OS protection
    # (a ZXNextRemote listener, 0.9.0). Distinct from op_done so the UI can
    # stop the whole batch and explain WHERE the setting lives, instead of
    # counting one generic failure and pressing on.
    os_protected = Signal(str, str)
    # (current, letters): the Next's default drive + every mounted drive letter,
    # e.g. ("C", ["C", "M"]). ("", []) when the dot predates the 'W' command.
    drives       = Signal(str, object)
    # (type, number): the far responder's identity from the 'Y' version
    # query - ("httpbridge"/"n2n", "1.0.2") for a ZX Next Remote listener,
    # ("sync", "5.8.0") for a dot. ("", "") when the far build predates the
    # command (ZXNR < 1.0.2 / dot < 5.8) - the pane then shows nothing,
    # exactly the pre-ident behaviour.
    ident        = Signal(str, str)
    # (drive, free_bytes): result of a ("free", drive) query ('Z', dot v5.2+).
    # free_bytes is an int, or None when the query failed on the Next ('F') or
    # the dot predates 'Z' -- the log line says which. Free space is the ONLY
    # storage metric a dotN can obtain safely (total partition size needs
    # +3DOS/IDEDOS calls that crash a dotN), so psize/pfull both present it.
    free_space   = Signal(str, object)
    # (path, data): result of a ("fsize", path) query ('S', rfsize, dot
    # v5.2+). data is {'files': int, 'dirs': int, 'bytes': int}, or None on
    # failure / pre-v5.2 dots. Emitted AFTER the matching op_done(ok, "size",
    # path), so a modal progress op has closed by the time the UI shows the
    # result dialog.
    fsize        = Signal(str, object)
    # (op, name): one 'D' progress block arrived while a long command runs.
    # op is "copy" (rcpy) or "size" (rfsize). name is the item the Next just
    # reported - the destination path of the file now being copied / the
    # directory now being scanned - or "" for an empty keepalive (rcpy sends
    # one per 64 KB inside a big file, so these pulse a byte estimate). The
    # UI uses them to drive the progress dialog instead of leaving the bar
    # parked at 0% for the whole copy.
    op_progress  = Signal(str, str)
    marked       = Signal(str)            # a queued ("mark", token) was reached
    log          = Signal(str)            # a human-readable log line
    error        = Signal(str)            # a human-readable error
    port_in_use  = Signal(int)            # bind failed: the port is already taken


def _re_checksums(payload):
    c0 = c1 = 0
    for x in payload:
        c0 = (c0 ^ x) & 0xff
        c1 = (c1 + c0) & 0xff
    return c0, c1


# ZXNextRemote's "OS protection" refusal marker (0.9.0): a listener that
# guards its OS folders answers a blocked WRITE with an 'F' status block
# carrying these three bytes after the 'F'. We surface it as a distinct,
# actionable message instead of a generic failure; the dotN never sends
# it, so an ordinary Next just fails the way it always did. The message
# is deliberately explicit about WHERE the setting lives — the block is
# on the far machine, not here.
# A quit that asks the far side to END THE APPLICATION, not merely leave
# the session: 'Q' plus this marker byte. Only the deliberate "make the Next
# exit" command (the bridge's /forceexit) sends it; stopping our own -listen
# server keeps sending the bare 'Q' it always did, because a server shutting
# down must never take the operator's app down with it. Backward compatible:
# the .sync5 dotN and ZXNextRemote before 0.9.47 read payload[0] and ignore
# the rest, so a marked quit still reads as the plain quit it always was.
RE_QUIT_EXIT_MARK = b"X"

RE_OSP_MARK = b"OSP"
RE_OSP_ERROR = (
    "os-protected: write access is blocked in the remote operating "
    "system. If the far side is ZX Next Remote, check its \"OS protection\" "
    "setting and customise the restricted directory list if appropriate."
)


def _re_is_osp(payload):
    """True if a reply PAYLOAD (block minus framing) is ZXNextRemote's
    OS-protection write refusal: 'F' followed by the OSP marker."""
    return (payload[0:1] == b"F" and
            payload[1:1 + len(RE_OSP_MARK)] == RE_OSP_MARK)


def _re_fail_block_payload(data):
    """The verified payload of a framed 'F' status block, else None.

    A dotN pushes the classic 1-byte b"F" when a put fails (couldn't create
    the file, or the transfer gave up); ZXNextRemote can mark WHY with a
    suffix — b"FOSP" when its OS protection refused the write — so the
    controller can say more than "upload failed". Framing is
    [len_hi len_lo][payload][cs0][cs1][pktno] with len = payload + 5; like
    the byte-exact matcher this replaces, trailing bytes after the frame are
    tolerated (TCP coalescing). Older dots send nothing at all - they just
    stop pulling - so callers keep the "abandoned upload" fallback as well.
    """
    if len(data) < 6 or data[0] != 0x00:
        return None
    total = (data[0] << 8) | data[1]
    if total < 6 or len(data) < total or data[2:3] != b'F':
        return None
    payload = bytes(data[2:total - 3])
    c0, c1 = _re_checksums(payload)
    return payload if (data[total - 3] == c0 and data[total - 2] == c1) else None


def _re_is_fail_block(data):
    """True if ``data`` is a framed 'F' status block (classic or marked)."""
    return _re_fail_block_payload(data) is not None


def _re_sendpacket(conn, payload, pktno):
    c0, c1 = _re_checksums(payload)
    conn.sendall((len(payload) + 5).to_bytes(2, "big") + bytes(payload) +
                 bytes([c0, c1, pktno & 0xff]))


def _re_goodbye_linger(conn):
    """Give a just-sent goodbye ('Q' / the marked 'Q'+exit) time to be READ
    before our FIN can race it off the peer's wire.

    The far side of a -listen session reads over an ESP UART, where the
    frame and the close can arrive in one breath - and ZXNextRemote before
    0.9.52 examined the close FIRST, so quitting a session threw the very
    goodbye away unread (the /forceexit-does-nothing field report; the
    dot's loop reads first and never showed it). Draining until the peer
    closes - bounded at 2 s - means the FIN only ever follows the goodbye,
    which fixes every field build without asking anyone to update.

    One drained thing IS answered: the dot (<= 5.7.4) replies to our 'Q'
    with the classic raw "Bye" and then waits for "Later" before it closes
    - leaving it unanswered made it stare at silence for its full cipxfer
    timeout (~20 s on -s: the long "Closing.." hang on quit/forceexit).
    Answering here frees every field dot instantly; 5.7.5+ skips the Bye
    and just closes, which lands in the EOF arm below either way."""
    tail = b''
    try:
        conn.settimeout(2.0)
        while True:
            data = conn.recv(256)
            if not data:
                break                 # peer closed: the goodbye was read
            tail = (tail + data)[-8:]     # recv may split the 3-byte verb
            if b"Bye" in tail:
                _re_sendpacket(conn, b"Later", 0)
                tail = b''
            # anything else (stray polls): consumed, not answered
    except OSError:
        pass                          # timeout or reset: we tried, close


def _re_recv_exact(conn, n):
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


class _ReLinkDead(OSError):
    """The link died under a UI command that WILL BE RETRIED (9.7.20).

    Raised by the session's reply wrapper on EOF when a link-loss retry is
    eligible, so the arm's own failure report is skipped and the
    session-level arm holds the command for the seat that comes back. An
    OSError subclass on purpose: the arms' existing handlers stay valid."""


def _re_retry_give_up(sig, r, why):
    """The ONE failure report a held command gets when its retries are over
    (9.7.20): put_done(False) for a put, error for the rest - the widget
    counts either as that command's step, exactly as the arm would have."""
    cmd = r['cmd']
    line = "retry%d of %s abandoned: %s" % (r['attempt'], r['label'], why)
    sig.log.emit(line)
    logging.warning("Remote explorer: %s", line)
    if cmd[0] == "put":
        remote = str(cmd[2])
        if remote.endswith('/') or remote.endswith('\\'):
            remote = remote + os.path.basename(str(cmd[1]))
        sig.put_done.emit(False, remote)
    else:
        sig.error.emit("%s: %s" % (r['label'], why))


class _ReLinkGone(Exception):
    """The link failed under a reply that owed no report (9.7.20 review).

    Raised by _re_session's ``_idle`` for the idle 'I', an ack, 'Later',
    'Back': whatever that loop turn reported has already gone out, and the
    command the Next will ask for next is still in the queue for whoever
    serves it. Caught ahead of OSError so it never reaches the arm that
    reports a COMMAND lost - that report is one widget step, and a step for
    a command that was never taken ended a multi-file paste one file early
    (measured: the old seat's 'I' hitting the socket the accept thread had
    just shut down, with Sessions Off)."""


def _re_drop_link(conn):
    """Shut an evicted seat's socket down from the accept thread (9.7.20,
    the single-seat mode of run_remote_listen_server).

    shutdown() is what wakes a recv blocked on ANOTHER thread on every
    platform — on POSIX a bare close() leaves that recv sleeping until it
    returns on its own; the close that follows is for Windows, where a
    shutdown alone leaves it sleeping until its 1 s timeout. The session's
    own ``with conn:`` closes once more on its way out — the C-level close
    is a no-op on an already-invalid descriptor, so that is harmless."""
    if conn is None:
        return
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _re_recv_block(conn):
    """One framed block from the seat, as (payload, pktno). 'BADCS' on a
    checksum miss, None on a header that is not a frame (logged), 'EOF'
    when the link died - or 'POLL' (9.7.35) when the two header bytes are
    the seat's own raw "Poll", in which case its LAST TWO BYTES ARE LEFT
    UNREAD for the caller to consume or to hand to the session loop."""
    hdr = _re_recv_exact(conn, 2)
    if hdr is None:
        return 'EOF'          # the link died (9.7.20: told apart from garbage)
    if hdr == b"Po":
        # THE SEAT'S OWN RAW "Poll", NOT A FRAME (9.7.35). The seat polls
        # with four bare bytes and answers a command with FRAMED blocks;
        # this reader only ever expected the latter, so a Poll arriving
        # while a reply was owed was read as a length of 0x506F = 20591
        # and refused as garbage. A frame can never start with these two
        # bytes (20591 is over the 4096 cap), so hand the Poll up as its
        # own thing - WITH ITS TAIL STILL IN THE SOCKET. What it means is
        # the caller's to decide (_re_recv_reply): a seat that missed the
        # command and re-polled, or a seat that does not know the opcode
        # at all and answered with silence. Only the caller knows which
        # opcode it sent.
        return 'POLL'
    total = (hdr[0] << 8) | hdr[1]
    if total < 5 or total > 4096:
        # SAY SO. This branch was silent, which is why the 2026-09-21
        # failure left no line in the file at all (9.7.35).
        logging.warning("Remote explorer: bad block header %02x %02x - not"
                        " a frame, the reply is dropped", hdr[0], hdr[1])
        return None
    rest = _re_recv_exact(conn, total - 2)
    if rest is None:
        return 'EOF'
    payload, (cs0, cs1) = rest[:-3], (rest[-3], rest[-2])
    c0, c1 = _re_checksums(payload)
    if c0 != cs0 or c1 != cs1:
        return 'BADCS'
    return (payload, rest[-1])


#: Seconds a single command's reply may go quiet before we give up on it.
#: Generous on purpose — this is the gap BETWEEN blocks, and the Next can
#: legitimately pause for seconds (SD seeks, the directory re-open/skip
#: walk, its own retry backoff), not the time a whole transfer takes.
RE_REPLY_TIMEOUT = 60.0

#: Seconds of TOTAL silence from a connected Next before we conclude it is
#: gone. The peer drives the session and polls continuously when idle (every
#: second or two), so silence is unambiguous. Only counted between commands;
#: a command's reply has its own timeout.
#:
#: 400 s since 9.7.18 (it was 45 s), PAIRED with ZX Next Remote 1.2.0's own
#: dead-link guard and to be moved only in step with it. The field case: a
#: cross-machine folder paste driven over the HTTP bridge leaves the SOURCE
#: seat idle for the whole upload leg to the other seat — minutes for one
#: big file — and on a shared Wi-Fi the controller's sustained upload can
#: starve that idle seat's tiny Poll/'I' round-trips. This server keeps a
#: benched seat answered throughout (one thread per seat; the ``warm`` case
#: in tests/test_remote_listen.py measures a worst gap of ~0.2 s across a
#: long targeted put), so the polls that stopped ARRIVING were lost on the
#: air, not unanswered here. The Next's guard went from ~35 s to a 56-poll
#: count AND a 300 s clock, whichever first, so its verdict lands within
#: ~330 s in every regime (one bad poll can cost ~29 s on a slow CIPSEND
#: prompt). This limit must sit ABOVE that: a dead link is then still
#: called by the Next's own honest "Connection lost" first and reaped here
#: second — the ordering the old 45 s gave the old 6-poll guard (ZX Next
#: Remote 0.9.42). The price: a Next that vanishes without a FIN (power, a
#: Wi-Fi fade its module never reports) holds its seat, and the pane shows
#: it connected, for up to ~10 min instead of 45 s. RE_CANCEL_GRACE_MS
#: below now fires BEFORE this — see its note. tests/test_bridge_stall.py
#: pins the ordering.
#:
#: 400 -> 620 AT 9.7.24, and NOT for a reason of its own: this limit must
#: outlast one whole relayed bridge op, and that op's budget
#: (zxnu_http_bridge.LONG_TIMEOUT) went 270 -> 570 to follow ZXNextRemote
#: 1.3.4's raised client patience. The ~330 s floor above is untouched and
#: still satisfied — the Next's own verdict lands first, which is the
#: ordering that matters. Three more minutes of a zombie seat is the cost
#: of letting a multi-megabyte file cross the bridge.
PEER_SILENCE_LIMIT = 620.0

#: How many Nexts may sit on the listen server at once (option B). One
#: past the cap gets the framed "Busy" turn-away -- the option-A reply,
#: kept as the over-capacity answer.
RE_MAX_PEERS = 4

#: Link-loss retries (9.7.20, Sessions Off only). A UI command the link died
#: under - the seat's own drop, or the eviction that seats the Next dialing
#: back in - is not reported failed at once: it is held in control['retry']
#: and re-run by the seat that comes back after a RE_LINK_RETRY_PAUSE_S
#: settle, up to RE_LINK_RETRIES times ("retry1: get /x" in the NextSync
#: log - ZX Next Remote's console wording, so the two logs read alike).
#: While one is held and no seat is up, the worker keeps LISTENING for the
#: Next instead of returning (which would end the widget's operation), for
#: up to RE_LINK_RETRY_WAIT_S from the failure - ZX Next Remote 1.2.5's
#: Listener dials again at once and then ten seconds apart, so three of
#: its dials fit. Field case: an N-Go's folder copy to the PC died with its
#: ESP link, the Listener re-dialed within a second, and the copy simply
#: reported failed. Only ops whose re-run is idempotent are retried: a put
#: truncates on open, a get overwrites, the rest answer 'F' if already
#: done - never rmtree (a half-walked tree would count its own deletions as
#: failures), never a bridge command (its caller retries), never a macro
#: step, never the raw drives/version/free queries (they degrade on their
#: own and count no widget step).
RE_LINK_RETRIES = 3
RE_LINK_RETRY_PAUSE_S = 3.0
RE_LINK_RETRY_WAIT_S = 30.0
RE_LINK_RETRY_OPS = frozenset(("ls", "get", "put", "mkdir", "rmdir", "rm",
                               "rename", "rcpy", "fsize"))

#: Listener builds that answer the 'K' (crc) op, by 'Y' ident type:
#: the .sync5 dot from v5.9.2, ZX Next Remote (httpbridge/n2n) from 1.0.8.
RE_CRC_FLOORS = {"sync": (5, 9, 2), "httpbridge": (1, 0, 8), "n2n": (1, 0, 8)}
#: Verify-after-put reply wait = floor + size/rate (the HTTP bridge's /crc
#: formula, 15 KB/s worst case plus a minute), capped at the crc op's hour.
#: Read at CALL time so the test suite can shorten the floor.
RE_VERIFY_WAIT_FLOOR = 60.0
RE_VERIFY_BYTES_PER_S = 15000.0
#: deploypak.txt extras of a remote update (9.7.6): how many times ONE extra
#: file is sent again after its 'K' crc check disagrees with the bytes served
#: (or its put fails outright) before the update gives up on it.
RE_UPD_EXTRA_RETRIES = 3
#: The longest path a -listen command may carry: the dot copies it into a
#: 254-byte buffer and silently TRUNCATES a longer one (nextsync.c, the
#: command parse) — a put would then land under a different name. The update
#: macro refuses every composed name over this before a byte moves.
# How long a user cancel waits for an in-flight transfer before letting go
# (9.7.12). Cancel deliberately waits for the current file so nothing is
# left half-written, but a Next that has stopped answering never reports
# that file done, which used to wedge the operation for good. It sat past
# PEER_SILENCE_LIMIT (then 45 s) so the worker's dead-peer detector got
# first refusal; since 9.7.18 that limit is 400 s and THIS fires first.
# That is fine because of what 9.7.14 made it: it only stops WAITING and
# releases the UI, claims nothing about the Next, and the put keeps running
# in the worker — the dead-peer verdict still arrives, later, and ends the
# session and the operation with it.
RE_CANCEL_GRACE_MS = 60000

RE_MAX_REMOTE_PATH = 254


def re_peer_answers_crc(rtype, number):
    """True when a 'Y' ident (type, number) names a listener that answers
    'K': a .sync5 dot >= 5.9.2 or ZX Next Remote >= 1.0.8. Unknown type,
    empty or unparseable version => False - never probe blind: an older
    listener meets 'K' with silence, and that would cost a wait per file."""
    floor = RE_CRC_FLOORS.get((rtype or "").strip().lower())
    if floor is None:
        return False
    try:
        v = tuple(int(p) for p in str(number or "").strip().split("."))
    except ValueError:
        return False
    return bool(v) and v >= floor


def re_verify_wait(size_bytes):
    """Seconds to wait for ONE 'K' answer over a file of size_bytes."""
    return min(3600.0, RE_VERIFY_WAIT_FLOOR
               + max(0, int(size_bytes)) / RE_VERIFY_BYTES_PER_S)


def _re_reply_call(conn, handler, timeout=None, late_ok=False):
    """:func:`_re_recv_reply` under a per-command socket timeout.

    The session loop parks the socket at a 1 s timeout so an idle poll
    cycle stays responsive (see ``conn.settimeout(1.0)`` in the loop head).
    That is far too short to read a command's REPLY, and a ``socket.timeout``
    raised inside ``_re_recv_reply`` used to escape the entire session loop
    — ``socket.timeout`` is an ``OSError`` subclass, so it landed in the
    session-level handler, killed the ``-listen`` session AND skipped the
    ``reply.put(...)`` that the HTTP caller was blocked on. The caller then
    waited out the whole bridge timeout for bytes that were never coming:
    the "tree copy wedges on the Nth file, the dot says done, the app sees
    nothing" bug. rcpy/rfsize had carried this fix inline for a while; now
    every command shares it, so a stall fails ONE command and the session
    lives on.

    ``timeout`` defaults to :data:`RE_REPLY_TIMEOUT` read at CALL time, so
    the test suite can shorten it without waiting out a real minute."""
    try:
        conn.settimeout(RE_REPLY_TIMEOUT if timeout is None else timeout)
        return _re_recv_reply(conn, handler, late_ok)
    except socket.timeout:
        return False
    finally:
        try:
            conn.settimeout(1.0)     # back to the responsive idle cadence
        except OSError:
            pass


def _re_recv_reply(conn, handler, late_ok=False):
    """Read the framed blocks the Next pushes in reply to a command, acking each
    with "Ok". handler(payload) returns True to stop. Returns True on clean
    completion, False on drop - or None when the link DIED under the reply
    (EOF; 9.7.20), falsy like False for every caller but told apart by the
    session's own reply wrapper, which turns it into a link-loss retry.

    Call it through :func:`_re_reply_call`, never directly: on its own it
    inherits whatever socket timeout the session loop last set (1 s).

    ``late_ok`` (9.7.35): a raw Poll from the seat while the reply is owed
    is "no reply" - False, its tail left in the socket for the session
    loop's catch-all - unless ``late_ok``, when it is swallowed ONCE and the
    wait continues. Only for opcodes every seat knows; see the branch."""
    expected = 0
    polled = False
    while True:
        blk = _re_recv_block(conn)
        if blk == 'EOF':
            return None
        if blk == 'POLL':
            # A RAW POLL WHILE A REPLY WAS OWED (9.7.35) means one of two
            # things, and the bytes cannot tell them apart:
            #  - the seat does NOT KNOW THE OPCODE (an old dot asked 'Y',
            #    'K' or 'U') and answered with silence, then polled. That
            #    IS its answer, and it now waits for ours. This has always
            #    worked by accident: the two header bytes were refused as
            #    garbage, the arm reported no reply, and the leftover "ll"
            #    fell to the session loop's catch-all, which answered the
            #    seat's Poll with the idle 'I' it was waiting for.
            #  - the seat MISSED THE COMMAND inside its 5 s window (an idle
            #    seat's first command after a multi-minute leg, the
            #    2026-09-21 cross-seat paste) and re-polled; the command
            #    then lands, it executes it, and the framed reply FOLLOWS.
            #    Reporting 'connection dropped' here turned that into a
            #    502 for a mkdir the seat had done and said ok to.
            # Only the CALLER knows which is possible: an arm whose opcode
            # every seat has known since v1 (M, R, X) passes late_ok and
            # gets the second reading; every other arm keeps the first,
            # byte for byte - return False with the tail still in the
            # socket, and the catch-all answers it exactly as before.
            # In the late case: swallow the tail, never ANSWER the Poll
            # (an 'I' would queue behind the command and the seat would
            # read it as the answer to its NEXT Poll - one-behind for
            # good), never RESEND the command (rm, rmdir and put are not
            # idempotent; nothing new goes on the wire, so the
            # one-command-per-Poll rule of the 9.7.27/28 comment holds),
            # and swallow ONCE: the Next's control GET is bounded at 10 s
            # and a re-poll costs the seat ~5.4 s, so a second Poll means
            # the command was lost (it landed inside the seat's 0.2 s
            # drain) and the old verdict, with the tail left for the
            # catch-all, is the honest one.
            if not late_ok or polled:
                if late_ok:
                    logging.warning("Remote explorer: the Next re-polled"
                                    " twice while a reply was owed -"
                                    " reply dropped")
                return False
            if _re_recv_exact(conn, 2) is None:     # the Poll's "ll"
                return None
            polled = True
            logging.warning("Remote explorer: the Next re-polled while a"
                            " reply was owed - swallowed, still waiting")
            continue
        if blk is None:
            return False
        if blk == 'BADCS':
            _re_sendpacket(conn, b"Resend", expected)
            continue
        payload, pktno = blk
        if pktno == ((expected - 1) & 0xff):
            _re_sendpacket(conn, b"Ok", pktno)
            continue
        if pktno != expected:
            _re_sendpacket(conn, b"Err seq", pktno)
            return False
        stop = handler(payload)
        _re_sendpacket(conn, b"Ok", pktno)
        expected = (expected + 1) & 0xff
        if stop:
            return True


def _re_sanitize_incoming_path(root, name):
    """Map a filename reported by the Next to a safe path under ``root``.

    A "get" of a directory streams every file back with its path relative to
    the fetched folder (e.g. ``GAMES/level1/boot.tap``); this preserves that
    sub-structure locally instead of flattening it to the basename. Strips any
    drive letter and leading slashes, drops '.'/'..' segments, and guarantees
    the result stays inside ``root``. Mirrors nextsync5.sanitize_incoming_path.
    """
    name = name.replace('\\', '/')
    if len(name) >= 2 and name[1] == ':':
        name = name[2:]
    name = name.lstrip('/')
    parts = [p for p in name.split('/') if p not in ('', '.', '..')]
    rel = os.path.join(*parts) if parts else 'received.bin'
    dest = os.path.normpath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if not (os.path.abspath(dest) == root_abs or
            os.path.abspath(dest).startswith(root_abs + os.sep)):
        dest = os.path.join(root, os.path.basename(rel) or 'received.bin')
    return dest


def _re_relname_under(remote, name):
    """Return the Next-reported *name* relative to the fetched *remote* item.

    A directory 'get' streams every file with its full Next path (e.g. fetching
    '/games/lev' yields '/games/lev/boot.tap'). Stripping the *parent* of the
    fetched item ('/games') leaves 'lev/boot.tap', so the download recreates the
    fetched file/folder on its own rather than nested under a copy of its whole
    Next path. A single-file get ('/games/boot.tap') likewise reduces to
    'boot.tap'. Names with no common parent are returned unchanged.
    """
    def _strip(s):
        s = s.replace('\\', '/')
        if len(s) >= 2 and s[1] == ':':
            s = s[2:]
        return s
    name = _strip(name).lstrip('/')
    r = _strip(remote).rstrip('/')
    slash = r.rfind('/')
    parent = r[:slash].lstrip('/') if slash > 0 else ''
    if parent and (name == parent or name.startswith(parent + '/')):
        return name[len(parent):].lstrip('/')
    return name


def _re_session(sid, conn, addr, my_q, sig, cmd_queue, stop_event, shared,
                max_payload, seat=None):
    """One connected Next's ``-listen`` session (multi-Next, option B).

    The body is run_remote_listen_server's original single session, moved
    verbatim and now run one thread per connected Next. Only the ACTIVE
    session (shared['state']['active']) pops the shared host/bridge
    command queue; every other session merely answers its Next's polls
    with the idle reply, so the link stays warm while the user works a
    different machine -- the Next itself never knows it is benched.
    ``my_q`` carries session-directed commands: the broadcast "quit" and
    the HTTP bridge's session-TARGETED ops (?session=N), which run here
    even while this session is benched -- targeted traffic never moves
    the baton, so it cannot yank the Remote Explorer pane. A
    ("select_next", sid) popped from the shared queue moves the baton
    and is answered with an idle. Each session owns its owed bridge
    sinks, its put/rmtree state and its silence timer, exactly as the
    single session always did.

    ``seat`` is this session's roster entry (9.7.20). Its ``evicted`` flag
    is raised by the accept loop when, with Settings → Sessions Off, a
    newcomer takes the seat over as the same Next coming back: from that
    moment this session must not pop another shared command — the
    newcomer holds the SAME sid, so the active-sid check alone would not
    stop it — and its next recv ends it (the accept loop shuts the socket
    down, so that is at once). None = a bare call (the tests').
    """
    peers = shared['peers']
    plock = shared['lock']
    state = shared['state']
    _emit_peers = shared['emit_peers']
    seat = seat if seat is not None else {}

    def _evicted():
        return bool(seat.get('evicted'))

    def _bye_evicted():
        # The accept loop already told the console who replaced whom; the
        # file log alone records that THIS is how the session ended, so a
        # link dropped on purpose is never read as a silent peer or a
        # connection error.
        logging.info("Remote explorer: seat #%s's old link from %s ended "
                     "(replaced by the Next dialing in again, Sessions Off)",
                     sid, addr[0])

    def _idle(payload=b"I"):
        # THE way to answer a Poll that took no command, ack a block, or say
        # 'Later'/'Back' (9.7.20 review): a reply that owes no report. If the
        # link is gone under it, the session simply ends - _ReLinkGone is
        # caught ahead of OSError below - because whatever this turn reported
        # already went out, and a raw OSError here would reach the arm that
        # reports a COMMAND lost: one phantom widget step, a paste ending a
        # file early, a queued move's source kept (measured with the old
        # seat's 'I' landing on a socket the accept thread had just shut).
        try:
            _re_sendpacket(conn, payload, 0)
        except OSError as ex:
            raise _ReLinkGone(ex) from ex

    def log(msg):
        sig.log.emit(msg)

    def _re_trace(msg):
        """A protocol-health line for the NextSync console (9.7.13).

        DELIBERATELY NOT gated behind a switch: these events are rare in a
        healthy session (a clean transfer emits none at all), and the failure
        they diagnose is one nobody can predict - the 5.9.2 put hang appeared
        once, after hours of idle, and an opt-in trace would have been off.
        The cost of being always-on is a handful of lines in the one case
        where they are the whole story.

        DELIBERATELY NOT TRANSLATED, like every other packet/checksum/sequence
        line here: a pasted log has to match the .dot source and the protocol
        docs word for word (CLAUDE.md's zxnu_i18n row)."""
        log(msg)
        logging.info("nextsync protocol: %s", msg)

    # Bridge sinks THIS session still owes an answer to (BridgeReply.put
    # is idempotent; the list is pruned as it goes).
    owed = []

    def _owe(r):
        if r is not None:
            owed[:] = [x for x in owed if not x.resolved]
            owed.append(r)

    def _fail_owed(err):
        for r in owed:
            r.put({'ok': False, 'error': err})
        owed[:] = []

    def _pop_shared():
        # Atomic "am I active? then take one": two sessions polling at
        # once must never race the roster check against the pop. An
        # evicted seat (Sessions Off, its Next dialed in again under the
        # same sid) is never active again whatever the sid says. A held
        # link-loss retry (9.7.20) comes FIRST and holds the queue behind
        # it: idle answers until its pause is over, then the command
        # itself, its attempt count riding retry_ctx to the Poll branch.
        with plock:
            if _evicted() or state['active'] != sid:
                return None
            r = control.get('retry')
            if r is not None and r.get('gen') != shared.get('gen'):
                # Another worker run stashed it (a session that outlived its
                # worker's finally). Its operation died with that worker -
                # the widget got `disconnected` - so drop it silently: no
                # report is owed here, and RUNNING it could write to a
                # different machine.
                control.pop('retry', None)
                logging.info("Remote explorer: dropped a link-loss retry "
                             "left by an earlier server run (%s)",
                             r.get('label'))
                r = None
            if r is not None:
                if not _single_seat():
                    # Flipped to On under a held retry: no seat may run it
                    # (the machine that comes back might be another one).
                    control.pop('retry', None)
                    _re_retry_give_up(sig, r, "Sessions was switched On")
                elif time.monotonic() < r['due']:
                    return None
                else:
                    control.pop('retry', None)
                    retry_ctx['attempt'] = r['attempt']
                    line = "retry%d: %s" % (r['attempt'], r['label'])
                    log(line)
                    logging.info("Remote explorer: %s", line)
                    return r['cmd']
            retry_ctx['attempt'] = 0
            try:
                return cmd_queue.get_nowait()
            except queue.Empty:
                return None

    # Remote .sync5 self-update jobs (the "update_dot" macro): id ->
    # {'data': staged bytes, 'dir': remote dot dir, 'ver': version, plus the
    # 'released'/'swap_started' step markers}. Declared OUTSIDE the try so
    # the finally below can honour dot_update's exactly-once contract when
    # the session dies mid-macro (Wi-Fi drop, a Bye during staging, stop).
    # Steps ride local_cmds like rmtree's walk, so nothing interleaves.
    upd_jobs = {}

    def _upd_extra_total(job):
        # Puts in a job's deploypak.txt plan (0 without one). Pre-try, like
        # upd_jobs: the finally block composes verdicts with it.
        return sum(1 for s in (job or {}).get('extras', ()) if s[0] == "put")

    def _upd_extras_note(job):
        # The sentence every verdict AFTER the companions landed must carry:
        # they overwrite in place, so "nothing was swapped" is only true of
        # the build itself — a refused staging put, a bad staged copy, a
        # refused release or rename, a session lost mid-swap all leave the
        # previous build running against the NEW data files (and, 9.7.7,
        # beside the other flavor's NEW build). Empty when nothing landed.
        job = job or {}
        note = ""
        sent = len(job.get('ex_sent', ()))
        if sent:
            note += " " + ui_tr_now(
                "{landed} of the {total} deploypak.txt file(s) had already "
                "been replaced on the card — the previous build now runs "
                "against the new data files; run the update again to put "
                "them back in step.").format(
                    landed=sent, total=_upd_extra_total(job))
        for rel in job.get('sib_sent', ()):
            note += " " + ui_tr_now(
                "The other flavor's build {path} had already been replaced "
                "on the card too.").format(path=job.get('dir', '') + "/" + rel)
        return note
    # Verify-after-put jobs (9.7.3): id -> {'remote', 'crc' (8 hex of the
    # bytes SENT), 'size', 'state', 'got'}. Pre-try like upd_jobs: the
    # finally settles the ONE put_done each still owes. vstate carries the
    # id counter and the once-per-session "skipped" advisory flag.
    vjobs = {}
    vstate = {'seq': 0, 'skip_said': False}
    # This session's 'Y' answer (type, number) once anyone asked - the widget
    # on connect / every baton move, or the verify step's own probe; ("", "")
    # = asked, unsupported. The verify gate reads it so an old listener is
    # never sent a 'K'.
    sess_ident = None
    # The put the Next is pulling right now — ("put", remote, bridge_reply
    # | None[, jid[, "extra"]]) — or None. Declared OUTSIDE the try (9.7.20)
    # because the finally settles a UI put the session died under: the pull
    # is served from the idle recv, so a link that goes mid-file ends the
    # session without ever reaching _put_finish, and the widget's operation
    # was left one step short for ever unless the whole worker ended too.
    pending = None

    def _ui_put_pending():
        # A plain UI put still being pulled: no bridge sink (those are
        # answered by _fail_owed) and no update_dot job id (the macro has
        # its own exactly-once verdict in the finally).
        return (pending is not None and pending[0] == "put"
                and len(pending) <= 3 and pending[2] is None)

    # Bytes moved by the transfer in flight (9.7.21), so a failure can say
    # how far it got instead of just "failed" - the question a folder copy
    # that died mid-way always raises. The put pull and the get handler keep
    # it current; _put_finish's failure arm, the get arm's failure tail, the
    # finally's session-death settle and the link-loss retry's stash line all
    # read it. Pre-try like `pending` for the finally's sake. 'total' is the
    # command's own total when it HAS one (a put's file size, a single-file
    # get's declared size) and 0 when it does not (a directory get).
    # 'path' is the command's own remote path, so a site that reports the
    # stop does not have to guess it; 'said' marks that one of them already
    # has - the get arm's failure tail and the finally would otherwise BOTH
    # speak for the same dead get (the tail runs when the reply call returns
    # False, the finally when a raw OSError skipped it); and 'live' says a
    # transfer is IN FLIGHT and owns these numbers.
    #
    # 'live' is what makes the record COMMAND-scoped rather than
    # session-scoped, and both bugs it fixes were real. A get that SUCCEEDED
    # left the record armed, so the finally's get branch fired at every
    # clean session end after a download and called a whole file "stopped".
    # And the update macro's staging and extras puts set put_data/pending
    # directly without coming through the put arm, so a failure there
    # reported the PREVIOUS put's byte counts against the macro's file name
    # - they arm nothing, so with 'live' they now say nothing, which is
    # right: the macro has its own progress and verdict lines.
    xfer = {'kind': '', 'done': 0, 'total': 0, 'files': 0, 'path': '',
            'said': False, 'live': False}

    def _xfer_note():
        """The progress phrase for the untranslated protocol-style lines
        (the retry stash): " after 1536 KB (1.5 MB) of 4096 KB (4.0 MB)",
        or "" when nothing has moved yet."""
        if not xfer['live'] or not xfer['done']:
            return ""
        if xfer['total']:
            return " after %s of %s" % (log_size(xfer['done']),
                                        log_size(xfer['total']))
        return " after %s" % log_size(xfer['done'])

    def _log_xfer_stopped(kind, path):
        """Say how much of a FAILED transfer moved (9.7.21). Translated: the
        NextSync console's transfer-progress half is user-facing (the
        zxnu_i18n row), unlike the packet/checksum diagnostics."""
        if (not xfer['live'] or kind != xfer['kind'] or not xfer['done']
                or xfer['said']):
            # No transfer owns these numbers any more (a finished one, or
            # a macro put that armed nothing), not this command's kind, not
            # a byte moved (the plain failure the caller emits beside this
            # already says that, and "0.0 KB" on every refused put would be
            # noise), or another site already said it for this transfer.
            return
        xfer['said'] = True
        path = path or xfer['path']
        if kind == 'put':
            log(ui_tr_now(
                "Upload stopped: {sent} of {total} sent to {path}").format(
                    sent=log_size(xfer['done']),
                    total=log_size(xfer['total']), path=path))
        elif xfer['files'] > 1:
            log(ui_tr_now(
                "Download stopped: {received} received from {path} across "
                "{files} files").format(
                    received=log_size(xfer['done']), path=path,
                    files=xfer['files']))
        else:
            log(ui_tr_now(
                "Download stopped: {received} received from {path}").format(
                    received=log_size(xfer['done']), path=path))

    # rmtree walks still open: job id -> {'root', 'fails', 'reply', ...}.
    # Pre-try like pending/vjobs/upd_jobs (9.7.20 review): the walk's steps
    # ride local_cmds one per Poll, so a link that dies BETWEEN two steps
    # ends the session from the idle recv with the job's ONE op_done never
    # sent - and with a survivor seated (or the Off mode's re-seat) no
    # disconnected ever came to end the widget's delete operation.
    rmtree_jobs = {}
    rmtree_seq = 0

    def _report_owed_elsewhere():
        # True when this death is reported by someone other than the error
        # signal - the finally (a UI put being pulled, a verify or update
        # job open, an rmtree walk open) or a bridge sink _fail_owed answers
        # (bridge traffic is silent to the Remote Explorer UI). An error
        # signal on top would be a SECOND widget step for one command, so
        # the except arms log their line instead in every such case.
        return (_ui_put_pending() or bool(vjobs) or bool(upd_jobs)
                or bool(rmtree_jobs)
                or any(not r.resolved for r in owed))

    # Link-loss retry state (9.7.20). `inflight` is the shared-queue UI
    # command this session is serving whose report is still owed and whose
    # op is in RE_LINK_RETRY_OPS (a put keeps it until its pull ends);
    # `inflight_attempt` its retry count so far (0 for a fresh command);
    # `stashed` set once it has been handed to control['retry'] so no arm
    # and not the finally report it. control survives worker restarts, so
    # a held retry outlives this session AND this worker.
    control = shared.get('control') or {}
    inflight = None
    inflight_attempt = 0
    stashed = False
    retry_ctx = {'attempt': 0}

    def _single_seat():
        fn = shared.get('sessions_on')
        if fn is None:
            return False
        try:
            return not fn()
        except Exception:                                    # noqa: BLE001
            return False

    def _inflight_label():
        c = inflight
        if c is None:
            return "?"
        if c[0] == "put":
            return "put " + str(c[2])
        return "%s %s" % (c[0], c[1] if len(c) > 1 else "")

    def _retry_eligible():
        return (inflight is not None and not stashed
                and inflight_attempt < RE_LINK_RETRIES and _single_seat())

    def _stash_retry(why):
        # Hold the command for the seat that comes back: no report now, the
        # retry's own outcome is the report. The pause gives the returning
        # link a moment to settle; the deadline bounds how long the worker
        # keeps listening with no seat at all.
        nonlocal stashed
        k = inflight_attempt + 1
        now = time.monotonic()
        control['retry'] = {'cmd': inflight, 'attempt': k,
                            'due': now + RE_LINK_RETRY_PAUSE_S,
                            'deadline': now + RE_LINK_RETRY_WAIT_S,
                            'label': _inflight_label(),
                            'gen': shared.get('gen')}
        stashed = True
        line = "retry%d in %ds: %s (%s%s)" % (
            k, int(RE_LINK_RETRY_PAUSE_S), _inflight_label(), why,
            _xfer_note())
        log(line)
        logging.info("Remote explorer: %s", line)

    def _retry_spent(why):
        # A retried command the link cut AGAIN with no retry left: say so
        # beside the failure report the normal arm now emits.
        if inflight is not None and inflight_attempt > 0 and not stashed:
            line = "retry%d failed: %s — giving up (%s)" % (
                inflight_attempt, _inflight_label(), why)
            log(line)
            logging.warning("Remote explorer: %s", line)

    def _re_reply_call(conn_, handler, timeout=None, late_ok=False):
        # Shadows the module function for every arm of THIS session
        # (9.7.20): the same contract, plus - when the reply ends in EOF and
        # a link-loss retry is eligible - _ReLinkDead instead of False, so
        # the arm's own failure report is skipped and the session-level arm
        # holds the command for the seat that comes back. Windows raises
        # its own OSError out of the recv instead; both land in the same
        # arm below.
        try:
            conn_.settimeout(RE_REPLY_TIMEOUT if timeout is None else timeout)
            r = _re_recv_reply(conn_, handler, late_ok)
        except socket.timeout:
            return False
        finally:
            try:
                conn_.settimeout(1.0)
            except OSError:
                pass
        if r is None:
            if _retry_eligible():
                raise _ReLinkDead("the link died under " + _inflight_label())
            _retry_spent("the link went down again")
            return False
        return bool(r)

    try:
        with conn:
            put_data = b''
            put_ofs = 0
            put_pkt = 0
            last_packet = b''
            # Protocol-health counters for the CURRENT put (9.7.13). The Next
            # asks for a "Retry" when a packet fails its checksum and a
            # "Restart" when the packet NUMBER did not match - the mismatch
            # storm that used to end in a hang is a burst of Restarts, and it
            # is entirely visible from this side. See _re_trace.
            put_retry = 0
            put_restart = 0
            pending = None   # ("put", remote, bridge_reply|None) awaiting completion

            def _query_ident():
                # The 'Y' exchange, shared by the version arm and the verify
                # step's self-probe: (type, number), or ("", "") when the
                # listener predates it - its raw "Poll" fails the block parse
                # at once (no wait paid; the stray bytes hit the loop's
                # catch-all idle reply).
                res = {'type': "", 'number': ""}

                def _hv(payload, _r=res):
                    if payload[0:1] == b'O' and len(payload) >= 2:
                        body = payload[1:].split(NULB, 1)
                        _r['type'] = body[0].decode(errors='replace')
                        if len(body) > 1:
                            _r['number'] = body[1].decode(errors='replace')
                    return True
                _re_sendpacket(conn, b"Y", 0)
                got_v = _re_reply_call(conn, _hv)
                return ((res['type'], res['number'])
                        if got_v and res['type'] else ("", ""))

            def _verify_wanted():
                # The Settings toggle via the injected 0-arg hook, read per
                # put (no server restart). Absent or broken => off.
                fn = shared.get('verify_crc')
                if fn is None:
                    return False
                try:
                    return bool(fn())
                except Exception:                                  # noqa: BLE001
                    logging.exception("Remote explorer: verify_crc hook failed")
                    return False

            def _verify_skip_advisory():
                if not vstate['skip_said']:
                    vstate['skip_said'] = True
                    log("crc32: verification skipped for this Next — its "
                        "listener predates .sync v5.9.2 / ZX Next Remote 1.0.8 "
                        "(or does not answer the version query); files are "
                        "sent unverified")

            def _plan_verify(remote):
                # put_data is STILL the file just served (kept for a late
                # Retry/Restart, see the Get arm): the digest is of the bytes
                # actually sent. Call only from _put_finish(True).
                if sess_ident is not None and not re_peer_answers_crc(*sess_ident):
                    _verify_skip_advisory()
                    sig.put_done.emit(True, remote)
                    return
                vstate['seq'] += 1
                vid = vstate['seq']
                vjobs[vid] = {'remote': remote,
                              'crc': "%08X" % (zlib.crc32(put_data) & 0xffffffff),
                              'size': len(put_data), 'state': 'queued', 'got': ""}
                local_cmds.appendleft(("put_verify", vid))

            def _put_finish(ok, osp=False):
                # A put that did NOT land says how much of it had been served
                # when it stopped (9.7.21) - "Upload stopped: 1.5 M of 4.0 M
                # sent to /games/x.tap". Before the arm below, so the staging
                # put of an update_dot job and a bridge put are covered too.
                if not ok and pending and len(pending) > 1:
                    _log_xfer_stopped('put', pending[1])
                # Resolved either way: the record stops owning this put (see
                # the 'live' note where xfer is declared).
                xfer['live'] = False
                # Resolve the pending put: to its bridge reply when it has
                # one, to the UI signal otherwise (reads `pending` live).
                # ``osp`` marks ZXNextRemote's OS-protection refusal (the
                # marked 'F'+OSP status block): the bridge caller gets the
                # same 401 + explanation every other blocked write gets, the
                # UI the os_protected toast instead of a generic failure.
                # A 4th element marks the staging put of an "update_dot" job:
                # chain the verify step instead of toasting put_done (the
                # update macro has its own progress and outcome lines).
                jid = pending[3] if len(pending) > 3 else None
                if jid is not None and jid in upd_jobs:
                    if len(pending) > 4 and pending[4] == "extra":
                        # A deploypak.txt extra (9.7.6): every byte served
                        # -> the 'K' check decides; a put the Next refused
                        # is sent again (a transient 'F' / an abandoned
                        # pull), the marked OS-protection refusal never.
                        _job = upd_jobs[jid]
                        _rel = _job['extras'][_job['ex_i']][-1]
                        if ok and not osp:
                            local_cmds.appendleft(("upd_extra_verify", jid))
                        elif osp:
                            local_cmds.appendleft(
                                ("upd_extra_fail", jid, _rel,
                                 "the far side's OS protection refused "
                                 "writing it (Settings on the Next)"))
                        elif _job['ex_try'] < RE_UPD_EXTRA_RETRIES:
                            _job['ex_try'] += 1
                            _upd_extra_retry_log(_job, _rel, refused=True)
                            local_cmds.appendleft(("upd_extra", jid))
                        else:
                            # The dot opens (create + truncate) the target
                            # BEFORE pulling, so a transfer that gave up
                            # can leave a cut-short file: delete whatever
                            # is there, like a corrupted copy.
                            local_cmds.appendleft(
                                ("upd_extra_rm", jid,
                                 "the Next refused it "
                                 + str(RE_UPD_EXTRA_RETRIES + 1) + " times"))
                        return
                    if ok and not osp:
                        local_cmds.appendleft(("upd_verify", jid))
                    else:
                        # Name the actual staged file, and the refuser: for
                        # a ZXNR target dir under the Next's default-on OS
                        # protection the FIRST refusal is this staging put,
                        # and the confirm dialog promised the failure would
                        # say so.
                        _b = upd_jobs[jid].get('base', 'sync5')
                        local_cmds.appendleft(
                            ("upd_fail", jid,
                             ("the far side's OS protection refused staging "
                              + _b + ".new (Settings on the Next)") if osp
                             else ("staging " + _b + ".new failed on the "
                                   "Next")))
                    return
                if pending[2] is not None:
                    if osp:
                        pending[2].put({'ok': False, 'error': RE_OSP_ERROR,
                                        'http': 401})
                    else:
                        pending[2].put({'ok': bool(ok)} if ok else
                                       {'ok': False,
                                        'error': 'put failed on the Next'})
                elif osp:
                    sig.os_protected.emit("put", pending[1])
                elif ok and _verify_wanted():
                    # Verify-after-put (9.7.3): hold this put's ONE put_done
                    # until the 'K' verdict (or the finally backstop). Bridge
                    # puts took the reply branch above; the update_dot
                    # staging put returned from the jid block.
                    _plan_verify(pending[1])
                else:
                    sig.put_done.emit(bool(ok), pending[1])

            def _upd_read_back(jid, job):
                # The update macro's read-back verify: pull the staged
                # <base>.new back with 'G' and byte-compare it with what was
                # served, then chain the release or the cleanup failure. One
                # wire command, so it answers the Poll it is called from —
                # by upd_verify directly (a listener that predates the crc
                # op) or via the upd_getback step (the fallback after a 'K'
                # that gave no verdict).
                got_back = bytearray()

                def _h(payload, _g=got_back):
                    o = payload[0:1]
                    if o == b'D':
                        _g.extend(payload[1:])
                    return o == b'B'
                _re_sendpacket(
                    conn,
                    b"G" + (job['dir'] + "/" + job['base'] + ".new").encode(),
                    0)
                if (_re_reply_call(conn, _h) and
                        bytes(got_back) == job['data']):
                    sig.log.emit(ui_tr_now(
                        "Remote {name} update: staged copy verified "
                        "({size} bytes) — swapping it in…").format(
                            name=job['name'], size=len(got_back)))
                    local_cmds.appendleft(("upd_release", jid))
                else:
                    local_cmds.appendleft(
                        ("upd_fail_rm", jid,
                         "the staged " + job['base'] + ".new read "
                         "back different from what was sent"))

            # deploypak.txt extras (9.7.6). The files an itch.io package lists
            # alongside its build (cmd[7] of "update_dot": the read_deploypak
            # plan — ("mkdir", rel) / ("put", local, rel), rel under the
            # remote dir) go FIRST, before the staging put: the swap ends the
            # session (the ZXNR flavor's marked quit soft-resets the Next),
            # so nothing could follow it — and a failure among them then
            # leaves the running build untouched with nothing staged to
            # clean. A mkdir answered with a plain 'F' is checked with a
            # listing (esx_f_mkdir answers 0xFF for "exists" too; a folder
            # that is neither creatable nor present fails the update); the
            # marked OS-protection refusal is fatal at once. A put is
            # followed by the 'K' crc check against the bytes served, the
            # staged build's own rules: a DEFINITE digest decides, no
            # verdict (silence / 'F' / malformed) drops to the 'G' read-back
            # byte-compare, which a listener that predates the crc op takes
            # directly — an extra is never left unverified, since a late
            # 'F' after the last byte is dropped by the put arm and only a
            # verify tells a cut-short data file apart. A DIFFERENT digest
            # or read-back, a refused put or an abandoned pull sends the
            # file again, up to RE_UPD_EXTRA_RETRIES times, then the
            # corrupted copy is deleted and the update fails naming the
            # file (upd_extra_fail: its verdict also says how many of the
            # manifest's files were already replaced — they overwrite in
            # place, no .bak). Every helper here is a Poll answer or chains
            # the step that will be.
            def _upd_extra_index(job):
                # 1-based ordinal of the put at ex_i among the plan's puts.
                return 1 + sum(1 for s in job['extras'][:job['ex_i']]
                               if s[0] == "put")

            def _upd_extra_mismatch(jid, job, rel, diag, giveup_why):
                # A DIFFERENT digest / read-back: the file goes again, up to
                # RE_UPD_EXTRA_RETRIES times, then the corrupted copy is
                # deleted and the update fails naming the file.
                if job['ex_try'] < RE_UPD_EXTRA_RETRIES:
                    log(diag + " — MISMATCH")
                    job['ex_try'] += 1
                    _upd_extra_retry_log(job, rel)
                    local_cmds.appendleft(("upd_extra", jid))
                else:
                    log(diag + " — MISMATCH, giving up")
                    local_cmds.appendleft(("upd_extra_rm", jid, giveup_why))

            def _upd_extra_readback(jid, job):
                # The extra's read-back verify: pull it back with 'G' and
                # byte-compare with what was served — for a listener that
                # predates the crc op, and the fallback after a 'K' with no
                # verdict (upd_verify's own rule: an extra is never shipped
                # unverified — a late 'F' after the last byte is dropped,
                # so a truncated data file would otherwise pass). One wire
                # command, so it answers the Poll it is called from.
                rel = job['extras'][job['ex_i']][-1]
                remote = job['dir'] + "/" + rel
                got_back = bytearray()

                def _h(payload, _g=got_back):
                    o = payload[0:1]
                    if o == b'D':
                        _g.extend(payload[1:])
                    return o == b'B'
                _re_sendpacket(conn, b"G" + remote.encode(), 0)
                if not _re_reply_call(conn, _h):
                    local_cmds.appendleft(
                        ("upd_extra_fail", jid, rel,
                         "connection dropped while reading " + rel + " back"))
                elif bytes(got_back) == job['ex_data']:
                    log(f"readback {remote}: {len(got_back)} bytes — verified")
                    _upd_extra_landed(jid, job)
                else:
                    _upd_extra_mismatch(
                        jid, job, rel,
                        f"readback {remote}: {len(got_back)} bytes back, "
                        f"{job['ex_size']} sent",
                        rel + " read back different from what was sent "
                        + str(RE_UPD_EXTRA_RETRIES + 1) + " times")

            def _upd_extra_retry_log(job, rel, refused=False):
                # Two causes, two sentences: a digest / read-back that
                # differs (the file arrived, corrupt) vs a put the Next
                # refused or abandoned (no byte, or not all of them, moved).
                sig.log.emit(ui_tr_now(
                    "Remote {name} update: the Next refused {path} — "
                    "sending it again (retry {retry} of {retries})…"
                    if refused else
                    "Remote {name} update: {path} did not arrive intact — "
                    "sending it again (retry {retry} of {retries})…").format(
                        name=job['name'], path=job['dir'] + "/" + rel,
                        retry=job['ex_try'], retries=RE_UPD_EXTRA_RETRIES))

            def _upd_extra_landed(jid, job):
                # One companion done (verified): on to the next on the
                # following Poll. Manifest files and the other flavor's
                # build are counted apart — the verdicts name them apart.
                step = job['extras'][job['ex_i']]
                job['sib_sent' if step[0] == "sibling" else 'ex_sent'].append(
                    step[-1])
                job['ex_i'] += 1
                job['ex_try'] = 0
                local_cmds.appendleft(("upd_extra", jid))

            def _upd_stage(jid, job):
                # The staging put of the build itself — this Poll's answer.
                # Step 0's tail before 9.7.6; now reached straight from step
                # 0 (no extras) or once the manifest is done.
                nonlocal put_data, put_ofs, put_pkt, pending
                _newp = job['dir'] + "/" + job['base'] + ".new"
                job['staged'] = True
                if _upd_extra_total(job):
                    sig.log.emit(ui_tr_now(
                        "Remote {name} update: all {count} deploypak.txt "
                        "file(s) are on the card — staging the build "
                        "itself…").format(
                            name=job['name'], count=_upd_extra_total(job)))
                sig.log.emit(ui_tr_now(
                    "Remote {name} update: staging {path} "
                    "({size} bytes)…").format(
                        name=job['name'], path=_newp, size=len(job['data'])))
                put_data = job['data']
                put_ofs = 0
                put_pkt = 0
                put_retry = put_restart = 0
                pending = ("put", _newp, None, jid)
                _re_sendpacket(conn, b"P" + _newp.encode(), 0)
                # the Next pulls the bytes via "Get" (served below);
                # _put_finish chains upd_verify when the last byte goes.

            def _upd_extra_step(jid, job):
                # Serve the extra at job['ex_i'] as THIS Poll's answer: the
                # mkdir exchange, a put's 'P', or — the plan exhausted — the
                # staging put of the build.
                nonlocal put_data, put_ofs, put_pkt, pending
                i = job['ex_i']
                if i >= len(job['extras']):
                    _upd_stage(jid, job)
                    return
                step = job['extras'][i]
                remote = job['dir'] + "/" + step[-1]
                if step[0] == "mkdir":
                    res = {'ok': None, 'osp': False}

                    def _h(payload, _r=res):
                        _r['ok'] = (payload[0:1] == b'O')
                        _r['osp'] = _re_is_osp(payload)
                        return True
                    _re_sendpacket(conn, b"M" + remote.encode(), 0)
                    if not _re_reply_call(conn, _h):
                        local_cmds.appendleft(
                            ("upd_extra_fail", jid, step[-1],
                             "connection dropped while creating " + remote))
                    elif res['osp']:
                        local_cmds.appendleft(
                            ("upd_extra_fail", jid, step[-1],
                             "the far side's OS protection refused creating "
                             + remote + " (Settings on the Next)"))
                    elif res['ok']:
                        job['ex_i'] += 1
                        job['ex_try'] = 0
                        local_cmds.appendleft(("upd_extra", jid))
                    else:
                        # A plain 'F' cannot be told from "exists"
                        # (esx_f_mkdir answers 0xFF for both): ask the
                        # folder itself on the next Poll — a swallowed real
                        # failure under an entry with nothing to put would
                        # otherwise report success with the folder missing.
                        log(f"mkdir {remote}: the Next answered F — checking "
                            "whether the folder exists")
                        local_cmds.appendleft(("upd_extra_lscheck", jid))
                    return
                local = step[1]
                try:
                    with open(local, 'rb') as fh:
                        blob = fh.read()
                except OSError as ex:
                    local_cmds.appendleft(
                        ("upd_extra_fail", jid, step[-1],
                         "reading " + local + " failed: " + str(ex)))
                    _idle()
                    return
                job['ex_data'] = blob
                job['ex_crc'] = "%08X" % (zlib.crc32(blob) & 0xffffffff)
                job['ex_size'] = len(blob)
                if job['ex_try'] == 0 and step[0] == "sibling":
                    sig.log.emit(ui_tr_now(
                        "Remote {name} update: sending the other flavor's "
                        "build {path} ({size} bytes) alongside…").format(
                            name=job['name'], path=remote, size=len(blob)))
                elif job['ex_try'] == 0:
                    sig.log.emit(ui_tr_now(
                        "Remote {name} update: sending deploypak.txt file "
                        "{index} of {count}: {path} ({size} bytes)…").format(
                            name=job['name'], index=_upd_extra_index(job),
                            count=_upd_extra_total(job), path=remote,
                            size=len(blob)))
                put_data = blob
                put_ofs = 0
                put_pkt = 0
                put_retry = put_restart = 0
                pending = ("put", remote, None, jid, "extra")
                _re_sendpacket(conn, b"P" + remote.encode(), 0)
                # the Next pulls the bytes via "Get"; _put_finish chains
                # upd_extra_verify (or the retry) when the last byte goes.
            # rmtree walk state: sub-commands the worker generates for itself
            # (rmtree_ls/rmtree_rm/rmtree_rmdir) are served before the host
            # queue, so a recursive delete runs as one contiguous batch.
            local_cmds = deque()
            # rmtree_jobs / rmtree_seq: declared pre-try (the finally settles
            # a walk the session died under).
            upd_seq = 0        # update_dot job ids (the dict lives pre-try)

            # Dead-peer detection. A Next that is switched off (or unplugged,
            # or whose Wi-Fi drops) never sends a FIN: the socket simply goes
            # quiet, and recv() times out forever while the UI keeps claiming
            # a Next is connected — reported after a machine sat "connected"
            # to a Next that had been off for hours.
            #
            # No probe is needed to notice, because THE NEXT DRIVES: an idle
            # peer polls us every second or two (the dot's idle throttle,
            # ZXNextRemote's is faster), so silence is itself the signal. The
            # timer only runs HERE, between commands — a command's own reply
            # is read inside _re_reply_call, which has its own timeout — so a
            # slow operation can never be mistaken for a dead peer.
            last_rx = time.monotonic()
            while not stop_event.is_set():
                if pending is None:
                    # The previous turn's command has reported (a put reports
                    # when its pull ends and keeps its inflight until then).
                    inflight = None
                    inflight_attempt = 0
                try:
                    conn.settimeout(1.0)
                    data = conn.recv(1024)
                except socket.timeout:
                    if _evicted():
                        _bye_evicted()
                        break
                    if time.monotonic() - last_rx >= PEER_SILENCE_LIMIT:
                        log(ui_tr_now(
                            "Remote explorer: no word from the Next for "
                            "{seconds}s — assuming it is gone (powered off? "
                            "Wi-Fi dropped?)").format(
                                seconds=int(PEER_SILENCE_LIMIT)))
                        logging.warning(
                            "Remote explorer: peer silent for %ss — "
                            "assuming it is gone", int(PEER_SILENCE_LIMIT))
                        break
                    continue
                # The two breaks below used to be SILENT: a session that
                # died here (the Next's ESP resetting the TCP link, a
                # Wi-Fi drop surfacing as ConnectionReset, …) left no
                # trace anywhere — the UI just started blinking for a
                # reconnect and the user was left guessing WHO hung up.
                # Name the reason, in the console AND the file log.
                except OSError as ex:
                    if _evicted():
                        _bye_evicted()
                        break
                    log(ui_tr_now(
                        "Remote explorer: connection error from the Next "
                        "({error}) — session over.").format(error=ex))
                    logging.warning(
                        "Remote explorer: connection error from the Next: %s",
                        ex)
                    break
                if not data:
                    if _evicted():
                        _bye_evicted()
                        break
                    log(ui_tr_now(
                        "Remote explorer: the Next closed the connection."))
                    # The FILE line names the seat (9.7.35): two of these
                    # bracketed the 2026-09-21 failure and could not be
                    # assigned to a machine. The console line is a
                    # translated catalog key and stays as it is.
                    logging.info(
                        "Remote explorer: the Next closed the connection"
                        " (seat #%s, %s).", sid,
                        addr[0] if addr else "?")
                    break
                last_rx = time.monotonic()

                # A put in flight is served by the Next pulling the bytes with
                # "Get"/"Gee" (or asking to resend with "Retry"/"Restart"). A newer
                # dotN instead pushes an explicit 'F' status block when the put
                # fails (couldn't create the file, or the transfer gave up) —
                # ZXNextRemote marks an OS-protection refusal as 'F'+OSP so the
                # failure toast can say WHY. Ack it so the dot's send_block_rt
                # doesn't burn its retries, and report the failure. Ack even with
                # no pending put (a rare late 'F' after the last byte already
                # counted) so the dot isn't left retrying.
                fail_payload = _re_fail_block_payload(data)
                if fail_payload is not None:
                    _idle(b"Ok")
                    if pending and pending[0] == "put":
                        _put_finish(False, osp=_re_is_osp(fail_payload))
                        pending = None
                        put_data = b''
                        put_ofs = 0
                        put_pkt = 0
                    continue
                # Older dots don't send 'F'; they just stop pulling and go back to
                # "Poll". Treat any other non-pull frame during a pending put as an
                # abandoned upload so the UI operation still completes and its
                # transfer dialog closes instead of waiting forever for a "Get".
                if (pending and pending[0] == "put" and
                        data not in (b"Get", b"Gee", b"Retry", b"Restart")):
                    _put_finish(False)
                    pending = None
                    put_data = b''
                    put_ofs = 0
                    put_pkt = 0

                if data == b"Poll":
                    from_shared = False
                    if local_cmds:
                        cmd = local_cmds.popleft()
                    else:
                        # Session-directed first (the broadcast "quit"),
                        # then -- only while THIS session holds the baton
                        # -- the shared host/bridge queue.
                        try:
                            cmd = my_q.get_nowait()
                        except queue.Empty:
                            cmd = _pop_shared()
                            from_shared = cmd is not None
                        if cmd is None:
                            _idle()   # idle
                            continue
                        if cmd[0] == "select_next":
                            # The baton moves; this Next idles on.
                            with plock:
                                if cmd[1] in peers:
                                    state['active'] = cmd[1]
                            _emit_peers()
                            _idle()
                            continue
                    op = cmd[0]
                    # A command from the HTTP bridge carries its result sink as
                    # the last element: fill that instead of emitting signals
                    # (bridge traffic must be silent to the Remote Explorer UI).
                    reply = cmd[-1] if isinstance(cmd[-1], BridgeReply) else None
                    _owe(reply)          # an HTTP thread is blocked on this
                    # Link-loss retry bookkeeping (9.7.20): only a shared-
                    # queue UI command in RE_LINK_RETRY_OPS is ever held for
                    # the seat that comes back; retry_ctx carries the attempt
                    # count _pop_shared read off a held command.
                    if from_shared and reply is None and op in RE_LINK_RETRY_OPS:
                        inflight = cmd
                        inflight_attempt = retry_ctx['attempt']
                    else:
                        inflight = None
                        inflight_attempt = 0
                    if op == "rmtree":
                        # Recursive folder delete: open a walk job and start with
                        # the root's listing (handled below, on this same poll).
                        rmtree_seq += 1
                        rmtree_jobs[rmtree_seq] = {'root': cmd[1], 'fails': 0,
                                                   'reply': reply}
                        cmd = ("rmtree_ls", rmtree_seq, cmd[1])
                        op = cmd[0]
                        reply = None
                    if op == "quit_app":
                        # /forceexit: this seat's Next is asked to leave
                        # listen mode AND exit its application. Deliberately
                        # NOT broadcast — unlike a server stop, which is for
                        # everyone, quitting somebody's app is aimed at the
                        # machine the caller targeted and nobody else.
                        _re_sendpacket(conn, b"Q" + RE_QUIT_EXIT_MARK, 0)
                        _re_goodbye_linger(conn)
                        if reply is not None:
                            reply.put({'ok': True})
                        break
                    if op == "quit":
                        _re_sendpacket(conn, b"Q", 0)
                        _re_goodbye_linger(conn)
                        # Stop is for EVERYONE: tell every OTHER
                        # session's Next to leave at its next poll too.
                        with plock:
                            _others = [p['q'] for s2, p in peers.items()
                                       if s2 != sid]
                        for _q2 in _others:
                            _q2.put(("quit",))
                        # A bridge-driven quit (/forceexit) must fill its reply
                        # BEFORE we break: the HTTP thread is blocked on it.
                        if reply is not None:
                            reply.put({'ok': True})
                        break
                    elif op == "mark":
                        # Client-side barrier: nothing goes to the Next, we just
                        # report that the queue reached this point, then idle so
                        # the Next keeps polling.
                        sig.marked.emit(str(cmd[1]))
                        _idle()
                    elif op == "ls":
                        path = cmd[1] or "."
                        entries = []
                        # The Next answers a listing with 'D' blocks then 'E', or a
                        # single 'F' status block if opendir failed (the folder is
                        # gone). Track which so a missing folder isn't mistaken for
                        # an empty one - and so the 'F' block is consumed instead of
                        # desyncing the stream. A ZX Next Remote listener whose
                        # Read+write OS protection refuses the listing answers the
                        # MARKED 'F'+OSP: tell that apart from a plain miss so the
                        # bridge relays it as 401 os-protected - not the "missing
                        # folder?" 502 that made a protected browse read as an
                        # unexplained failure - and the UI raises os_protected.
                        st = {'failed': False, 'osp': False}

                        def _h(payload, _e=entries, _st=st):
                            o = payload[0:1]
                            if o == b'E':
                                return True
                            if o == b'F':
                                _st['failed'] = True
                                _st['osp'] = _re_is_osp(payload)
                                return True
                            if o == b'D':
                                i = 1
                                while i + 6 <= len(payload):
                                    flags = payload[i]
                                    size = (payload[i+1] | (payload[i+2] << 8) |
                                            (payload[i+3] << 16) | (payload[i+4] << 24))
                                    nl = payload[i+5]
                                    name = payload[i+6:i+6+nl].decode(errors='replace')
                                    i += 6 + nl
                                    _e.append((bool(flags & 1), size, name))
                            return False
                        _re_sendpacket(conn, b"L" + path.encode(), 0)
                        if _re_reply_call(conn, _h):
                            if st['osp']:
                                # Read-protected on the far side: the same
                                # 401 + os-protected relay every blocked
                                # write gets, and the os_protected toast on
                                # the UI path - never "folder gone".
                                if reply:
                                    reply.put({'ok': False, 'http': 401,
                                               'error': RE_OSP_ERROR})
                                else:
                                    sig.os_protected.emit("ls", path)
                            elif st['failed']:
                                if reply:
                                    reply.put({'ok': False,
                                               'error': f"ls {path} failed "
                                                        "(missing folder?)"})
                                else:
                                    sig.ls_failed.emit(path)
                            else:
                                entries.sort(key=lambda e: (0 if e[0] else 1, e[2].lower()))
                                if reply:
                                    reply.put({'ok': True, 'entries': entries})
                                else:
                                    sig.listing.emit(path, entries)
                        elif reply:
                            reply.put({'ok': False, 'error': 'connection dropped'})
                        else:
                            sig.error.emit(f"ls {path}: connection dropped")
                    elif op == "get":
                        # Works for a single file or a whole directory: the Next
                        # streams every file back (N/D/E per file, B at the end)
                        # with a path relative to the fetched item, which we keep
                        # so sub-folders are recreated locally intact.
                        remote, dest_dir = cmd[1], cmd[2]
                        os.makedirs(dest_dir, exist_ok=True)
                        st = {'f': None, 'name': None, 'bytes': 0, 'last': None,
                              'count': 0, 'osp': False}
                        xfer.update(kind='get', done=0, total=0, files=0,
                                    path=remote, said=False, live=True)

                        def _h(payload, _st=st, _dd=dest_dir, _remote=remote):
                            o = payload[0:1]
                            if o == b'F' and _re_is_osp(payload):
                                # A read-protected SOURCE. A get has no
                                # failure frame, so a ZX Next Remote listener
                                # leads its empty walk with the marked 'F'+OSP
                                # purely so this is nameable; the 'B' still
                                # follows and ends the stream with nothing.
                                _st['osp'] = True
                                return False
                            if o == b'N':
                                # 'N' + [4B filelen][1B namelen][name] - and
                                # those four length bytes are ALWAYS ZERO on
                                # this wire: the dot writes them as "length
                                # unknown" (nextsync.c) and ZX Next Remote
                                # as "size unknown, like the dot" (fsrv.c).
                                # So a stopped download never claims a total
                                # (9.7.21); it counts what arrived and how
                                # many files it was spread over, which is
                                # everything the wire actually tells us.
                                namelen = payload[5] if len(payload) > 5 else 0
                                name = payload[6:6+namelen].decode(errors='replace')
                                xfer['files'] += 1
                                rel = (_re_relname_under(_remote, name) or
                                       os.path.basename(name.replace('\\', '/').rstrip('/')))
                                path = _re_sanitize_incoming_path(_dd, rel)
                                if _st['f']:
                                    _st['f'].close()
                                parent = os.path.dirname(path)
                                if parent:
                                    os.makedirs(parent, exist_ok=True)
                                _st['f'] = open(path, 'wb')
                                _st['name'] = name
                                _st['last'] = path
                                _st['bytes'] = 0
                                _st['count'] += 1
                            elif o == b'D':
                                if _st['f']:
                                    _st['f'].write(payload[1:])
                                    _st['bytes'] += len(payload) - 1
                                    xfer['done'] += len(payload) - 1
                            elif o == b'E':
                                if _st['f']:
                                    _st['f'].close()
                                    _st['f'] = None
                            elif o == b'B':
                                if _st['f']:
                                    _st['f'].close()
                                    _st['f'] = None
                                return True
                            return False
                        _re_sendpacket(conn, b"G" + remote.encode(), 0)
                        try:
                            ok = _re_reply_call(conn, _h)
                        finally:
                            # Closed on the retry path too (_ReLinkDead): a
                            # half-written file left open here would refuse
                            # the retried get's own open on Windows.
                            if st['f']:
                                st['f'].close()
                                st['f'] = None
                        if st['osp']:
                            # Read-protected source: the same 401 relay and
                            # os_protected toast a blocked write draws, not a
                            # phantom "0 files" success or a plain failure.
                            if reply:
                                reply.put({'ok': False, 'http': 401,
                                           'error': RE_OSP_ERROR})
                            else:
                                sig.os_protected.emit("get", remote)
                        elif reply:
                            reply.put({'ok': bool(ok), 'count': st['count'],
                                       'last': st['last']}
                                      if ok else {'ok': False, 'error': 'get failed'})
                        elif ok:
                            sig.got.emit(remote, st['last'] or dest_dir)
                        else:
                            _log_xfer_stopped('get', remote)
                            sig.error.emit(f"get {remote}: failed")
                        # This get is over, whatever the outcome: retire the
                        # record so the session's finally cannot report a
                        # COMPLETED download as stopped, and so a later
                        # command's retry note cannot borrow its bytes. The
                        # finally's branch survives for the one case it is
                        # for - a raw OSError out of recv, which never
                        # reaches this line.
                        xfer['live'] = False
                    elif op == "put":
                        local, remote = cmd[1], cmd[2]
                        try:
                            with open(local, 'rb') as fh:
                                put_data = fh.read()
                        except OSError as ex:
                            if reply:
                                reply.put({'ok': False, 'error': str(ex)})
                            else:
                                sig.error.emit(f"put {local}: {ex}")
                            continue
                        put_ofs = 0
                        put_pkt = 0
                        put_retry = put_restart = 0
                        xfer.update(kind='put', done=0, total=len(put_data),
                                    files=1, path=remote, said=False,
                                    live=True)
                        if remote.endswith('/') or remote.endswith('\\'):
                            remote = remote + os.path.basename(local)
                        pending = ("put", remote, reply)
                        _re_sendpacket(conn, b"P" + remote.encode(), 0)
                        # the Next now pulls the bytes via "Get" (served below)
                    elif op in ("mkdir", "rmdir", "rm"):
                        opc = {"mkdir": b"M", "rmdir": b"R", "rm": b"X"}[op]
                        path = cmd[1]
                        res = {'ok': None, 'osp': False}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            _r['osp'] = _re_is_osp(payload)
                            return True
                        _re_sendpacket(conn, opc + path.encode(), 0)
                        if _re_reply_call(conn, _h, late_ok=True):
                            # ONE COMMAND PER POLL CYCLE, and mkdir does not
                            # get a second one (9.7.28). 9.7.27 tried to make
                            # a refused mkdir idempotent from here by probing
                            # "L<path>" and accepting a clean listing as proof
                            # the directory already existed. The seat answered
                            # "ls FAIL not an ack" - its PB_NOTOK - because
                            # THE SEAT IS THE ONE THAT POLLS: every command in
                            # this worker rides the seat's own cadence, and
                            # that probe jammed a second one into a slot
                            # already spent. The next ordinary ls succeeded,
                            # so the link was never the problem.
                            #
                            # It was also the wrong repo. The disagreement was
                            # never protocol-vs-protocol; it was three mkdirs
                            # inside ZXNextRemote, of which the File server's
                            # 'M' op was the only one that refused an existing
                            # directory. Fixed there, in the main-bank
                            # fsx_mkdir wrapper (1.4.1). We relay the seat's
                            # verdict verbatim, which is this worker's job.
                            if res['osp'] and reply:
                                reply.put({'ok': False, 'error': RE_OSP_ERROR,
                                           'http': 401})
                            elif res['osp']:
                                sig.os_protected.emit(op, path)
                            elif reply:
                                reply.put({'ok': bool(res['ok'])})
                            else:
                                sig.op_done.emit(bool(res['ok']), op, path)
                        elif reply:
                            reply.put({'ok': False, 'error': 'connection dropped'})
                        else:
                            sig.error.emit(f"{op} {path}: connection dropped")
                    elif op == "rmtree_ls":
                        # rmtree step 1: list one folder of the walk, then queue
                        # deleting its files, walking its sub-folders and finally
                        # removing the (now empty) folder itself, ahead of
                        # anything else -- so the tree comes down bottom-up.
                        jid, path = cmd[1], cmd[2]
                        entries = []
                        st = {'failed': False}

                        def _h(payload, _e=entries, _st=st):
                            o = payload[0:1]
                            if o == b'E':
                                return True
                            if o == b'F':
                                _st['failed'] = True
                                return True
                            if o == b'D':
                                i = 1
                                while i + 6 <= len(payload):
                                    flags = payload[i]
                                    nl = payload[i+5]
                                    name = payload[i+6:i+6+nl].decode(errors='replace')
                                    i += 6 + nl
                                    _e.append((bool(flags & 1), name))
                            return False
                        _re_sendpacket(conn, b"L" + path.encode(), 0)
                        if _re_reply_call(conn, _h):
                            subs = []
                            if not st['failed']:
                                base = path.rstrip("/")
                                for is_dir, name in entries:
                                    if name in (".", ".."):
                                        continue
                                    child = base + "/" + name
                                    subs.append(("rmtree_ls", jid, child) if is_dir
                                                else ("rmtree_rm", jid, child))
                            # On a failed listing (gone, or not a folder) still try
                            # the rmdir: it reports the failure if the folder is
                            # really stuck, instead of stalling the job.
                            subs.append(("rmtree_rmdir", jid, path))
                            local_cmds.extendleft(reversed(subs))
                        else:
                            job = rmtree_jobs.pop(jid, None)
                            if job is not None and job.get('reply'):
                                job['reply'].put({'ok': False,
                                                  'error': 'connection dropped'})
                            else:
                                sig.error.emit(f"delete {path}: connection dropped")
                    elif op in ("rmtree_rm", "rmtree_rmdir"):
                        # rmtree steps 2/3: delete one file / one emptied folder.
                        # Only the root folder's rmdir reports back to the UI --
                        # one op_done for the whole job, matching the single
                        # command the UI enqueued.
                        jid, path = cmd[1], cmd[2]
                        opc = b"X" if op == "rmtree_rm" else b"R"
                        res = {'ok': None, 'osp': False}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            _r['osp'] = _re_is_osp(payload)
                            return True
                        _re_sendpacket(conn, opc + path.encode(), 0)
                        if _re_reply_call(conn, _h):
                            job = rmtree_jobs.get(jid)
                            if job is not None:
                                if not res['ok']:
                                    job['fails'] += 1
                                    job['osp'] = job.get('osp') or res['osp']
                                    log(f"delete: could not remove {path}"
                                        + (" (remote OS protection)"
                                           if res['osp'] else ""))
                                # A protected member ends the whole tree delete
                                # now: the rest would only repeat the refusal.
                                root_done = (op == "rmtree_rmdir" and
                                             path == job['root'])
                                if res['osp'] or root_done:
                                    if res['osp']:
                                        # abandon the queued remainder of THIS job
                                        local_cmds = deque(
                                            c for c in local_cmds
                                            if not (len(c) > 1 and c[1] == jid))
                                    rmtree_jobs.pop(jid, None)
                                    if job.get('osp') and job.get('reply'):
                                        job['reply'].put(
                                            {'ok': False, 'error': RE_OSP_ERROR,
                                             'http': 401})
                                    elif job.get('osp'):
                                        sig.os_protected.emit("delete", job['root'])
                                    elif job.get('reply'):
                                        job['reply'].put({'ok': job['fails'] == 0})
                                    else:
                                        sig.op_done.emit(job['fails'] == 0,
                                                         "delete", job['root'])
                        else:
                            job = rmtree_jobs.pop(jid, None)
                            if job is not None and job.get('reply'):
                                job['reply'].put({'ok': False,
                                                  'error': 'connection dropped'})
                            else:
                                sig.error.emit(f"delete {path}: connection dropped")
                    elif op == "version":
                        # version query (ZXNR 1.0.2+ / dot v5.8+): 'Y', no
                        # args. One status block back: 'O' + type + NUL +
                        # build number. An old listener ignores the unknown
                        # opcode and re-polls; its raw "Poll" fails the block
                        # parse - degrade to unsupported, never kill the
                        # session (drives' own rule). The bridge layer caches
                        # per seat, so this fires once per seated Next.
                        # 9.7.3: the answer is cached per session (sess_ident)
                        # - the verify-after-put gate reads it, so an old
                        # listener is never sent a 'K'.
                        sess_ident = _query_ident()
                        if sess_ident[0]:
                            if reply:
                                reply.put({'ok': True, 'type': sess_ident[0],
                                           'number': sess_ident[1]})
                            else:
                                sig.ident.emit(sess_ident[0], sess_ident[1])
                        elif reply:
                            reply.put({'ok': False,
                                       'error': 'version not supported '
                                                '(needs ZXNR 1.0.2+ / '
                                                '.sync v5.8+)'})
                        else:
                            log("This listener does not answer the version "
                                "query (pre ZXNR 1.0.2 / .sync v5.8).")
                            sig.ident.emit("", "")
                    elif op == "crc":
                        # CRC-32 of ONE file computed ON the Next ('K', dot
                        # v5.9.2+ / ZXNR 1.0.8+): 'O' + 8 upper-case hex
                        # digits (IEEE, zlib.crc32's value), 'F' when the
                        # file did not open. The Next streams the file and
                        # answers nothing meanwhile, so the wait is long: a
                        # 1 MB file takes the dot ~30 s. The bridge sizes
                        # its own wait from rfsize; this is the worker's
                        # ceiling - an hour, ~100 MB on the dot. An old
                        # listener ignores the unknown opcode - degrade
                        # like 'version', never kill the session.
                        path = cmd[1]
                        res = {'crc32': "", 'fail': False}

                        def _hc(payload, _r=res):
                            o = payload[0:1]
                            if o == b'O' and len(payload) >= 9:
                                _r['crc32'] = payload[1:9].decode(
                                    errors='replace').upper()
                            elif o == b'F':
                                _r['fail'] = True
                            return True
                        _re_sendpacket(conn, b"K" + path.encode(), 0)
                        try:
                            got_c = _re_reply_call(conn, _hc, timeout=3600.0)
                        except socket.timeout:
                            got_c = False
                        if got_c and res['crc32']:
                            log(f"crc32 {path}: {res['crc32']}")
                            if reply:
                                reply.put({'ok': True, 'path': path,
                                           'crc32': res['crc32']})
                        else:
                            why = ('the file did not open' if res['fail']
                                   else 'no answer: the file did not open, '
                                        'or the listener predates .sync '
                                        'v5.9.2 / ZXNR 1.0.8')
                            log(f"crc32 {path}: {why}")
                            if reply:
                                reply.put({'ok': False,
                                           'error': f'crc failed ({why})'})
                    elif op == "put_verify":
                        # Verify-after-put (Settings → Verify CRC, 9.7.3): a
                        # local_cmds continuation of the put that just landed
                        # (upd_verify's shape - nothing interleaves, and it
                        # stays on the session that did the put). Gated on
                        # this session's 'Y' ident - asked here if nobody did
                        # - so an old listener never waits out a 'K'. Then the
                        # crc op's exchange against the digest of the bytes
                        # served, the wait sized like the bridge's /crc.
                        # Silence, 'F', 'F'+OSP or a malformed digest is NOT
                        # corruption: keep the file, say it is unverified,
                        # report the put as done.
                        vid = cmd[1]
                        job = vjobs.get(vid)
                        if job is None:
                            _idle()
                            continue
                        if sess_ident is None:
                            sess_ident = _query_ident()    # answers this Poll
                            local_cmds.appendleft(cmd)     # back on the next one
                            continue
                        if not re_peer_answers_crc(*sess_ident):
                            vjobs.pop(vid, None)
                            _verify_skip_advisory()
                            sig.put_done.emit(True, job['remote'])
                            _idle()  # this Poll needs an answer
                            continue
                        wait = re_verify_wait(job['size'])
                        log(f"crc32 {job['remote']}: verifying {job['size']} "
                            "bytes on the Next…")
                        res = {'crc32': "", 'fail': False, 'osp': False}

                        def _hk(payload, _r=res):
                            o = payload[0:1]
                            if o == b'O' and len(payload) >= 9:
                                _r['crc32'] = payload[1:9].decode(
                                    errors='replace').upper()
                            elif o == b'F':
                                _r['fail'] = True
                                _r['osp'] = _re_is_osp(payload)
                            return True
                        job['state'] = 'asking'
                        _re_sendpacket(conn, b"K" + job['remote'].encode(), 0)
                        got_k = _re_reply_call(conn, _hk, timeout=wait)
                        digest = res['crc32']
                        if (got_k and len(digest) == 8
                                and all(c in "0123456789ABCDEF" for c in digest)):
                            if digest == job['crc']:
                                log(f"crc32 {job['remote']}: {digest} — verified")
                                vjobs.pop(vid, None)
                                sig.put_done.emit(True, job['remote'])
                            else:
                                log(f"crc32 {job['remote']}: {digest} on the Next, "
                                    f"{job['crc']} sent — MISMATCH, deleting the "
                                    "corrupted copy")
                                job['state'] = 'mismatch'
                                job['got'] = digest
                                local_cmds.appendleft(("put_verify_rm", vid))
                        else:
                            if res['osp']:
                                why = "the far side's OS protection refused the read"
                            elif res['fail']:
                                why = "the file did not open on the Next"
                            elif got_k:
                                why = "malformed digest from the Next"
                            else:
                                why = ("no answer: the listener predates .sync "
                                       "v5.9.2 / ZXNR 1.0.8, or the link dropped")
                            log(f"crc32 {job['remote']}: not verified ({why}) "
                                "— file kept")
                            vjobs.pop(vid, None)
                            sig.put_done.emit(True, job['remote'])
                    elif op == "put_verify_rm":
                        # The digest differed: delete the corrupted copy ('X',
                        # path-based; upd_fail_rm's shape - NO op_done, nothing
                        # enqueued this), then the red verdict, then the put's
                        # ONE put_done(False) - in that order, so the log reads
                        # verdict first, the widget's "Upload failed" after.
                        vid = cmd[1]
                        job = vjobs.get(vid)
                        if job is None:
                            _idle()
                            continue
                        resx = {'ok': None, 'osp': False}

                        def _hx(payload, _r=resx):
                            _r['ok'] = (payload[0:1] == b'O')
                            _r['osp'] = _re_is_osp(payload)
                            return True
                        job['state'] = 'deleting'
                        _re_sendpacket(conn, b"X" + job['remote'].encode(), 0)
                        got_x = _re_reply_call(conn, _hx)
                        vjobs.pop(vid, None)
                        if got_x and resx['ok']:
                            log(f"crc32 {job['remote']}: corrupted copy deleted")
                            msg = ui_tr_now(
                                "CRC-32 verification FAILED for {path}: {sent} was "
                                "sent but the Next holds {got}. The corrupted copy "
                                "has been deleted from the Next — send the file "
                                "again.").format(path=job['remote'], sent=job['crc'],
                                                 got=job['got'])
                        else:
                            detail = ("the far side's OS protection refused the delete"
                                      if resx['osp'] else
                                      "the Next refused the delete" if got_x else
                                      "no answer from the Next to the delete")
                            log(f"crc32 {job['remote']}: corrupted copy NOT deleted "
                                f"({detail})")
                            msg = ui_tr_now(
                                "CRC-32 verification FAILED for {path}: {sent} was "
                                "sent but the Next holds {got}. The corrupted copy "
                                "could NOT be deleted from the Next ({reason}) — "
                                "remove it by hand and send the file again.").format(
                                    path=job['remote'], sent=job['crc'],
                                    got=job['got'], reason=detail)
                        sig.put_verify_failed.emit(msg)
                        sig.put_done.emit(False, job['remote'])
                    elif op == "drives":
                        # getdrives: one pushed status block, 'O' + current
                        # drive letter + one letter per mounted drive. An old
                        # dot (pre v5.1) ignores 'W' and re-polls; its raw
                        # "Poll" fails the block parse below, which lands in
                        # the ("", []) fallback -- the widget then offers no
                        # drive switching, exactly the pre-drives behaviour.
                        res = {'cur': "", 'letters': []}

                        def _h(payload, _r=res):
                            if payload[0:1] == b'O' and len(payload) >= 2:
                                _r['cur'] = chr(payload[1])
                                _r['letters'] = [chr(b) for b in payload[2:]]
                            return True
                        _re_sendpacket(conn, b"W", 0)
                        # A timeout here (old dot slow to re-poll) must not
                        # kill the session like other commands' drops would:
                        # drives is an optional nicety, so degrade instead.
                        try:
                            got_reply = _re_reply_call(conn, _h)
                        except socket.timeout:
                            got_reply = False
                        if got_reply and res['cur']:
                            if reply:
                                reply.put({'ok': True, 'current': res['cur'],
                                           'letters': res['letters']})
                            else:
                                sig.drives.emit(res['cur'], res['letters'])
                        elif reply:
                            reply.put({'ok': False,
                                       'error': 'drives not supported '
                                                '(needs .sync v5.1+)'})
                        else:
                            log("This .sync dot does not report drives "
                                "(pre v5.1); staying on the default drive.")
                            sig.drives.emit("", [])
                    elif op == "free":
                        # free space ('Z', dot v5.2+): optional drive letter,
                        # answered with one status block 'O' + 4 bytes
                        # little-endian = free 512-byte blocks (F_GETFREE), or
                        # 'F' when the drive can't be measured. Free space is
                        # the only storage metric the dotN can obtain safely
                        # (total size needs +3DOS calls that crash a dotN).
                        # Optional nicety like drives: an old dot ignores 'Z'
                        # and re-polls, so degrade instead of dropping.
                        drv = ((cmd[1] or "").strip().rstrip(':').upper()
                               if len(cmd) > 1 else "")
                        res = {'blocks': None, 'fail': False}

                        def _h(payload, _r=res):
                            if payload[0:1] == b'F':
                                _r['fail'] = True
                            elif payload[0:1] == b'O' and len(payload) >= 5:
                                _r['blocks'] = int.from_bytes(payload[1:5], 'little')
                            return True
                        _re_sendpacket(conn, b"Z" + drv.encode(), 0)
                        try:
                            got_reply = _re_reply_call(conn, _h)
                        except socket.timeout:
                            got_reply = False
                        if got_reply and res['blocks'] is not None:
                            if reply:
                                reply.put({'ok': True, 'drive': drv,
                                           'free': res['blocks'] * 512})
                            else:
                                sig.free_space.emit(drv, res['blocks'] * 512)
                        else:
                            err = (f"free space {drv or '(current drive)'}: "
                                   "FAILED on the Next" if res['fail'] else
                                   "free space not supported (needs .sync v5.2+)")
                            if reply:
                                reply.put({'ok': False, 'error': err})
                            else:
                                log(err)
                                sig.free_space.emit(drv, None)
                    elif op == "rcpy":
                        # Local copy ON the Next ('C', dot v5.2+): src and dst
                        # travel NUL-separated like rename's paths. The Next
                        # answers with 'D' progress blocks then one 'O'/'F'.
                        # Widen the socket timeout for the reply: the copy is
                        # local SD I/O and the keepalives only bound the
                        # silent gaps (per file / per 64KB). Always emit an
                        # op_done - the UI counts one per queued command, so
                        # even the old-dot fallback must complete the op.
                        src, dst = cmd[1], cmd[2]
                        res = {'ok': None, 'files': 0, 'osp': False}

                        def _h(payload, _r=res):
                            if payload[0:1] == b'D':
                                # Progress: named = a file copy just started
                                # (the name is its destination path), empty =
                                # the per-64KB / per-256-entries keepalive.
                                if len(payload) > 1:
                                    _r['files'] += 1
                                sig.op_progress.emit(
                                    "copy", payload[1:].decode(errors='replace'))
                                return False
                            _r['ok'] = (payload[0:1] == b'O')
                            _r['osp'] = _re_is_osp(payload)
                            return True
                        _re_sendpacket(conn, b"C" + src.encode() + b"\x00" +
                                       dst.encode(), 0)
                        got_reply = _re_reply_call(conn, _h)
                        if got_reply and res['osp'] and reply:
                            reply.put({'ok': False, 'files': res['files'],
                                       'error': RE_OSP_ERROR, 'http': 401})
                        elif got_reply and res['osp']:
                            sig.os_protected.emit("copy", dst)
                        elif got_reply and res['ok'] is not None:
                            if reply:
                                reply.put({'ok': bool(res['ok']),
                                           'files': res['files']} if res['ok'] else
                                          {'ok': False, 'files': res['files'],
                                           'error': 'rcpy FAILED on the Next '
                                                    '(copied files stay)'})
                            else:
                                sig.op_done.emit(bool(res['ok']), "copy", src)
                        elif reply:
                            reply.put({'ok': False,
                                       'error': 'rcpy needs .sync v5.2+ '
                                                '(or the link dropped)'})
                        else:
                            log("rcpy needs .sync v5.2+ (or the link dropped).")
                            sig.op_done.emit(False, "copy", src)
                    elif op == "fsize":
                        # Tree/file size ON the Next ('S', rfsize, dot v5.2+).
                        # 'D' blocks are progress (named per directory) and
                        # keepalives; the terminal 'O' carries the totals.
                        # Emit op_done FIRST (closes the UI's progress op),
                        # THEN fsize with the data for the result dialog.
                        path = cmd[1]
                        res = {'data': None}

                        def _h(payload, _r=res):
                            o = payload[0:1]
                            if o == b'D':
                                # Named = the directory the walk just entered.
                                sig.op_progress.emit(
                                    "size", payload[1:].decode(errors='replace'))
                                return False
                            if o == b'O' and len(payload) >= 15:
                                _r['data'] = {
                                    'files': int.from_bytes(payload[1:5], 'little'),
                                    'dirs': int.from_bytes(payload[5:9], 'little'),
                                    'bytes': (int.from_bytes(payload[13:15], 'little') << 32)
                                             | int.from_bytes(payload[9:13], 'little'),
                                }
                            return True         # 'O' or 'F' both end the reply
                        _re_sendpacket(conn, b"S" + path.encode(), 0)
                        got_reply = _re_reply_call(conn, _h)
                        if reply:
                            if res['data'] is not None:
                                reply.put({'ok': True, **res['data']})
                            else:
                                reply.put({'ok': False,
                                           'error': 'rfsize failed (missing '
                                                    'path, or needs .sync '
                                                    'v5.2+)'})
                        else:
                            if not got_reply:
                                log("rfsize needs .sync v5.2+ (or the link dropped).")
                            sig.op_done.emit(res['data'] is not None, "size", path)
                            sig.fsize.emit(path, res['data'])
                    elif op == "rename":
                        old, new = cmd[1], cmd[2]
                        res = {'ok': None, 'osp': False}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            _r['osp'] = _re_is_osp(payload)
                            return True
                        # 'V' + old + NUL + new, in one length-framed block.
                        _re_sendpacket(conn, b"V" + old.encode() + b"\x00" + new.encode(), 0)
                        if _re_reply_call(conn, _h):
                            if res['osp'] and reply:
                                reply.put({'ok': False, 'error': RE_OSP_ERROR,
                                           'http': 401})
                            elif res['osp']:
                                sig.os_protected.emit("rename", old)
                            elif reply:
                                reply.put({'ok': bool(res['ok'])})
                            else:
                                sig.op_done.emit(bool(res['ok']), "rename", old)
                        elif reply:
                            reply.put({'ok': False, 'error': 'connection dropped'})
                        else:
                            sig.error.emit(f"rename {old}: connection dropped")
                    elif op == "update_dot":
                        # Remote .sync5 self-update, step 0 (stage). The whole
                        # macro rides local_cmds like rmtree's walk, so no
                        # baton move or bridge command can interleave:
                        #   put sync5.new -> verify (the Next's own CRC-32
                        #   via 'K' on a 5.9.2+/1.0.8+ listener, else the
                        #   get-back byte-compare) -> 'U' release ->
                        #   rm .bak -> ren sync5->bak -> ren new->sync5 ->
                        #   targeted quit. The release comes BEFORE the .bak
                        #   delete on purpose: a pre-5.9 dot refuses 'U', and
                        #   failing there means nothing of the user's was
                        #   deleted ('X' is path-based, so it is legal after
                        #   'U'). After 'U' ONLY path-based ops (V/X/Q) may
                        #   follow: anything that OPENS a file or directory
                        #   could be handed the freed handle number, which
                        #   NextZXOS's exit tidy-up still closes (nextsync.c,
                        #   the 'U' protocol comment) — so once released,
                        #   every failure path ends the session too.
                        # cmd = ("update_dot", local_path, remote_dir, version
                        #        [, base_file, brand, marked_exit, extras]) —
                        # the trailing four default to the .sync5 dot and no
                        # extras; the ZXNR flavor passes its .nex file name,
                        # "ZXNextRemote", marked_exit=True (the Q+'X' quit
                        # soft-resets the Next into NextZXOS so the swapped
                        # .nex can relaunch) and the companions plan: the
                        # OTHER flavor's build as a ("sibling", local, rel)
                        # step (9.7.7 — both .nex on the card stay in step)
                        # followed, when the package carries a deploypak.txt,
                        # by the read_deploypak mkdir/put steps — all sent
                        # BEFORE the staging put, each put crc-checked and
                        # re-sent up to RE_UPD_EXTRA_RETRIES times (see
                        # _upd_extra_step).
                        local, rdir, dver = cmd[1], cmd[2].rstrip("/"), cmd[3]
                        base = cmd[4] if len(cmd) > 4 and cmd[4] else "sync5"
                        brand = (cmd[5] if len(cmd) > 5 and cmd[5]
                                 else "NextSync")
                        marked = bool(cmd[6]) if len(cmd) > 6 else False
                        disp = ".sync5" if base == "sync5" else base
                        try:
                            with open(local, 'rb') as fh:
                                blob = fh.read()
                        except OSError as ex:
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update failed while reading "
                                "{path}: {error} — nothing was sent.").format(
                                    name=disp, path=local, error=ex),
                                brand)
                            _idle()
                            continue
                        # The brand + version are embedded verbatim in the
                        # binary (the dot's banner is contiguous; ZXNR's title
                        # and ZXNR_VERSION are separate literals): a blob
                        # without them is the wrong file or a stale build —
                        # refuse before a single byte moves.
                        bad = False
                        if brand == "NextSync":
                            bad = bool(dver) and (
                                b"NextSync " + dver.encode()) not in blob
                        else:
                            bad = (brand.encode() not in blob or
                                   (bool(dver) and
                                    dver.encode() not in blob))
                        if bad:
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update refused: {path} does "
                                "not look like a {brand} {version} build — "
                                "wrong or stale file.").format(
                                    name=disp, path=local, brand=brand,
                                    version=dver),
                                brand)
                            _idle()
                            continue
                        # cmd[7] (9.7.6): the deploypak.txt plan — the
                        # extras the package lists, sent BEFORE the staging
                        # put (see _upd_extra_step). Checked whole here so a
                        # broken plan refuses before a byte moves, like the
                        # blob above.
                        # A ("sibling", local, rel) step (9.7.7) is the
                        # OTHER flavor's build, sent alongside so both .nex
                        # on the card stay in step: a put like any manifest
                        # file, but its blob is held to the same brand +
                        # version check as the staged build — a stale
                        # sibling refuses before a byte moves.
                        extras = []
                        bad_extra = ""
                        bad_why = "deploypak.txt names a file that is not there"
                        raw_extras = cmd[7] if len(cmd) > 7 and cmd[7] else ()
                        for step in raw_extras:
                            step = tuple(step)
                            if (step[:1] == ("mkdir",) and len(step) == 2
                                    and step[1] and isinstance(step[1], str)):
                                extras.append(step)
                            elif (step[:1] in (("put",), ("sibling",))
                                    and len(step) == 3
                                    and step[1] and step[2]
                                    and isinstance(step[1], str)
                                    and isinstance(step[2], str)):
                                if not os.path.isfile(step[1]):
                                    bad_extra = step[1]
                                    if step[0] == "sibling":
                                        bad_why = ("the other flavor's build "
                                                   "is not there")
                                    break
                                if step[0] == "sibling":
                                    try:
                                        with open(step[1], 'rb') as fh:
                                            sblob = fh.read()
                                    except OSError as ex:
                                        bad_extra, bad_why = step[1], str(ex)
                                        break
                                    if (brand.encode() not in sblob
                                            or (bool(dver)
                                                and dver.encode() not in sblob)):
                                        bad_extra = ""
                                        sig.dot_update.emit(False, ui_tr_now(
                                            "Remote {name} update refused: "
                                            "{path} does not look like a "
                                            "{brand} {version} build — wrong "
                                            "or stale file.").format(
                                                name=disp, path=step[1],
                                                brand=brand, version=dver),
                                            brand)
                                        bad = True
                                        break
                                extras.append(step)
                            else:
                                bad_extra = repr(step)
                                break
                        if bad:
                            _idle()
                            continue
                        if bad_extra:
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update failed while reading "
                                "{path}: {error} — nothing was sent.").format(
                                    name=disp, path=bad_extra, error=bad_why),
                                brand)
                            _idle()
                            continue
                        # A companion may not be the build being swapped,
                        # nor its .new/.bak: the other flavor's canonical
                        # name typed as the target (the UI derives the
                        # sibling from the session's ident, the target from
                        # what was typed) would be put, then renamed aside
                        # and overwritten by itself — reported as success.
                        _taken = {base.lower(), (base + ".new").lower(),
                                  (base + ".bak").lower()}
                        clash = next((s[-1] for s in extras
                                      if s[-1].lower() in _taken), "")
                        if clash:
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update refused: {path} is "
                                "both a file sent alongside and the build "
                                "being swapped — check the path on the "
                                "Next; nothing was sent.").format(
                                    name=disp, path=rdir + "/" + clash),
                                brand)
                            _idle()
                            continue
                        # The listener copies a command's path into a
                        # 254-byte buffer and TRUNCATES a longer one (a
                        # misplaced file the crc check would then "verify"):
                        # refuse every composed name the macro will send
                        # that could not fit, before a byte moves.
                        too_long = ""
                        for _p in ([rdir + "/" + base + ".new",
                                    rdir + "/" + base + ".bak"]
                                   + [rdir + "/" + s[-1] for s in extras]):
                            if len(_p.encode("utf-8", "replace")) > RE_MAX_REMOTE_PATH:
                                too_long = _p
                                break
                        if too_long:
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update refused: {path} is "
                                "longer than the {limit} bytes a path on the "
                                "Next may have — nothing was sent.").format(
                                    name=disp, path=too_long,
                                    limit=RE_MAX_REMOTE_PATH),
                                brand)
                            _idle()
                            continue
                        # The swap's two renames each carry BOTH names in
                        # one command ("<cur>\0<cur>.bak", "<cur>.new\0<cur>"
                        # = 2·len + 5 bytes), truncated by the same buffer:
                        # a .nex path that fits alone can still be swapped
                        # onto a chopped name — after 'U', mid-swap. Bound
                        # the pair too, before a byte moves.
                        _cur = rdir + "/" + base
                        if (2 * len(_cur.encode("utf-8", "replace")) + 5
                                > RE_MAX_REMOTE_PATH):
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update refused: swapping "
                                "{path} would need a rename command longer "
                                "than the {limit} bytes a listener accepts — "
                                "choose a shorter folder; nothing was "
                                "sent.").format(
                                    name=disp, path=_cur,
                                    limit=RE_MAX_REMOTE_PATH),
                                brand)
                            _idle()
                            continue
                        upd_seq += 1
                        upd_jobs[upd_seq] = {'data': blob, 'dir': rdir,
                                             'ver': dver, 'base': base,
                                             'name': disp, 'marked': marked,
                                             'brand': brand,
                                             'extras': extras, 'ex_i': 0,
                                             'ex_try': 0, 'ex_sent': [],
                                             'sib_sent': []}
                        if extras:
                            n_files = sum(1 for s in extras if s[0] == "put")
                            n_dirs = sum(1 for s in extras if s[0] == "mkdir")
                            if n_files or n_dirs:
                                sig.log.emit(ui_tr_now(
                                    "Remote {name} update: deploypak.txt lists "
                                    "{files} file(s) and {folders} folder(s) to "
                                    "send to {dir} first…").format(
                                        name=disp, files=n_files,
                                        folders=n_dirs, dir=rdir))
                            _upd_extra_step(upd_seq, upd_jobs[upd_seq])
                        else:
                            _upd_stage(upd_seq, upd_jobs[upd_seq])
                    elif op == "upd_extra":
                        # deploypak.txt extras, one per Poll (see
                        # _upd_extra_step); the plan exhausted stages the
                        # build itself.
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        _upd_extra_step(jid, job)
                    elif op == "upd_extra_lscheck":
                        # A mkdir the Next answered with a plain 'F': list
                        # the folder — a listing (even empty) means it
                        # exists and the put can proceed; a plain 'F' means
                        # it could neither be created nor found; the marked
                        # 'F'+OSP names the protection.
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        _rel = job['extras'][job['ex_i']][-1]
                        remote = job['dir'] + "/" + _rel
                        st = {'failed': False, 'osp': False}

                        def _hl(payload, _st=st):
                            o = payload[0:1]
                            if o == b'F':
                                _st['failed'] = True
                                _st['osp'] = _re_is_osp(payload)
                                return True
                            return o == b'E'
                        _re_sendpacket(conn, b"L" + remote.encode(), 0)
                        if not _re_reply_call(conn, _hl):
                            local_cmds.appendleft(
                                ("upd_extra_fail", jid, _rel,
                                 "connection dropped while checking "
                                 + remote))
                        elif st['osp']:
                            local_cmds.appendleft(
                                ("upd_extra_fail", jid, _rel,
                                 "the far side's OS protection refused "
                                 "creating " + remote
                                 + " (Settings on the Next)"))
                        elif st['failed']:
                            local_cmds.appendleft(
                                ("upd_extra_fail", jid, _rel,
                                 "the Next could not create " + remote
                                 + " (the mkdir failed and no such folder "
                                 "exists)"))
                        else:
                            log(f"mkdir {remote}: the folder exists — "
                                "carrying on")
                            job['ex_i'] += 1
                            job['ex_try'] = 0
                            local_cmds.appendleft(("upd_extra", jid))
                    elif op == "upd_extra_verify":
                        # An extra's every byte was served: ask the Next for
                        # its CRC-32 ('K') and compare with the bytes sent —
                        # upd_verify's exchange and its rules: only a
                        # DEFINITE answer decides here (equal lands the file,
                        # different sends it AGAIN — up to
                        # RE_UPD_EXTRA_RETRIES times, then the corrupted
                        # copy is deleted and the update fails — and an
                        # OS-protected read fails naming the protection);
                        # silence, a plain 'F' or a malformed digest prove
                        # nothing and drop to the read-back byte-compare,
                        # which a listener that predates the op takes
                        # directly. Never "kept unverified": a late 'F'
                        # after the last byte is dropped by the put arm, so
                        # only a verify tells a truncated data file apart.
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        if sess_ident is None:
                            sess_ident = _query_ident()    # answers this Poll
                            local_cmds.appendleft(cmd)     # back on the next one
                            continue
                        _rel = job['extras'][job['ex_i']][-1]
                        remote = job['dir'] + "/" + _rel
                        if not re_peer_answers_crc(*sess_ident):
                            _upd_extra_readback(jid, job)  # answers this Poll
                            continue
                        want = job['ex_crc']
                        wait = re_verify_wait(job['ex_size'])
                        log(f"crc32 {remote}: verifying {job['ex_size']} "
                            "bytes on the Next…")
                        res = {'crc32': "", 'fail': False, 'osp': False}

                        def _hk(payload, _r=res):
                            o = payload[0:1]
                            if o == b'O' and len(payload) >= 9:
                                _r['crc32'] = payload[1:9].decode(
                                    errors='replace').upper()
                            elif o == b'F':
                                _r['fail'] = True
                                _r['osp'] = _re_is_osp(payload)
                            return True
                        _re_sendpacket(conn, b"K" + remote.encode(), 0)
                        got_k = _re_reply_call(conn, _hk, timeout=wait)
                        digest = res['crc32']
                        if (got_k and len(digest) == 8
                                and all(c in "0123456789ABCDEF" for c in digest)):
                            if digest == want:
                                log(f"crc32 {remote}: {digest} — verified")
                                _upd_extra_landed(jid, job)
                            else:
                                _upd_extra_mismatch(
                                    jid, job, _rel,
                                    f"crc32 {remote}: {digest} on the Next, "
                                    f"{want} sent",
                                    "the CRC-32 of " + _rel + " on the Next ("
                                    + digest + ") still differed from what "
                                    "was sent (" + want + ") after "
                                    + str(RE_UPD_EXTRA_RETRIES + 1)
                                    + " attempts")
                        elif res['osp']:
                            local_cmds.appendleft(
                                ("upd_extra_fail", jid, _rel,
                                 "the far side's OS protection refused "
                                 "reading it back (Settings on the Next)"))
                        else:
                            if res['fail']:
                                why = "the file did not open for the crc op"
                            elif got_k:
                                why = "malformed digest from the Next"
                            else:
                                why = "no answer to the crc op"
                            log(f"crc32 {remote}: {why} — reading the copy "
                                "back instead")
                            local_cmds.appendleft(("upd_extra_getback", jid))
                    elif op == "upd_extra_getback":
                        # The read-back way (see upd_extra_verify).
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        _upd_extra_readback(jid, job)
                    elif op == "upd_extra_rm":
                        # The retries ran out: delete the corrupted extra
                        # ('X', path-based) so a known-bad copy does not
                        # stay on the card, then report on the next Poll.
                        jid, why = cmd[1], cmd[2]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        _rel = job['extras'][job['ex_i']][-1]
                        remote = job['dir'] + "/" + _rel
                        resx = {'ok': None}

                        def _hx(payload, _r=resx):
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        _re_sendpacket(conn, b"X" + remote.encode(), 0)
                        got_x = _re_reply_call(conn, _hx)
                        why += (" — the copy left on the Next was deleted"
                                if got_x and resx['ok'] else
                                " — no copy could be deleted from the Next "
                                "(check " + remote + " by hand)")
                        local_cmds.appendleft(("upd_extra_fail", jid, _rel, why))
                    elif op == "upd_extra_fail":
                        # An extra failed for good: the verdict names the
                        # file AND what the manifest already replaced (the
                        # extras overwrite in place — no .bak for them), and
                        # says the build itself is untouched. Nothing is
                        # staged yet and the handle is not released (extras
                        # precede the staging put), so the session stays up.
                        jid, _rel, why = cmd[1], cmd[2], cmd[3]
                        job = upd_jobs.pop(jid, None) or {}
                        _ex = job.get('extras', ())
                        _kind = (_ex[job['ex_i']][0]
                                 if _ex and job.get('ex_i', 0) < len(_ex)
                                 else "put")
                        if _kind == "sibling":
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update failed while sending the "
                                "other flavor's build {path}: {reason}. Nothing "
                                "was swapped — the Next still runs its current "
                                "build.").format(
                                    name=job.get('name', ''),
                                    path=(job.get('dir', '') + "/" + _rel),
                                    reason=why)
                                + _upd_extras_note(job),
                                (job or {}).get('brand', 'NextSync'))
                        else:
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update failed while sending {path} "
                                "from deploypak.txt: {reason}. Nothing was swapped "
                                "— the Next still runs its current build, but "
                                "{landed} of the {total} file(s) the manifest lists "
                                "had already been replaced on the card (running "
                                "the update again sends them all).").format(
                                    name=job.get('name', ''),
                                    path=(job.get('dir', '') + "/" + _rel),
                                    reason=why,
                                    landed=len(job.get('ex_sent', ())),
                                    total=_upd_extra_total(job))
                                + "".join(" " + ui_tr_now(
                                    "The other flavor's build {path} had already "
                                    "been replaced on the card too.").format(
                                        path=job.get('dir', '') + "/" + r)
                                    for r in job.get('sib_sent', ())),
                                (job or {}).get('brand', 'NextSync'))
                        _idle()
                    elif op == "upd_verify":
                        # Step 1: prove what LANDED on the SD card. The wire
                        # checksums are per-block and in-flight only, and the
                        # next step makes this file the running build itself.
                        # 9.7.5: ask the Next for the staged file's CRC-32
                        # ('K', dot 5.9.2+ / ZXNR 1.0.8+) and compare it with
                        # zlib.crc32 of the bytes served — 8 hex digits come
                        # back instead of the whole file over Wi-Fi. Gated on
                        # this session's cached 'Y' ident (asked here if
                        # nobody did, put_verify's shape): an older listener
                        # meets 'K' with silence, so it keeps the pull-back
                        # byte-compare (_upd_read_back). Only a DEFINITE
                        # answer decides here — equal digests swap, different
                        # ones fail with cleanup, an OS-protected read fails
                        # naming the protection; silence, a plain 'F' or a
                        # malformed digest prove nothing either way and drop
                        # to the read-back, which stays authoritative.
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        if sess_ident is None:
                            sess_ident = _query_ident()    # answers this Poll
                            local_cmds.appendleft(cmd)     # back on the next one
                            continue
                        _newp = job['dir'] + "/" + job['base'] + ".new"
                        if not re_peer_answers_crc(*sess_ident):
                            _upd_read_back(jid, job)       # answers this Poll
                            continue
                        want = "%08X" % (zlib.crc32(job['data']) & 0xffffffff)
                        wait = re_verify_wait(len(job['data']))
                        res = {'crc32': "", 'fail': False, 'osp': False}

                        def _hk(payload, _r=res):
                            o = payload[0:1]
                            if o == b'O' and len(payload) >= 9:
                                _r['crc32'] = payload[1:9].decode(
                                    errors='replace').upper()
                            elif o == b'F':
                                _r['fail'] = True
                                _r['osp'] = _re_is_osp(payload)
                            return True
                        _re_sendpacket(conn, b"K" + _newp.encode(), 0)
                        got_k = _re_reply_call(conn, _hk, timeout=wait)
                        digest = res['crc32']
                        if (got_k and len(digest) == 8
                                and all(c in "0123456789ABCDEF" for c in digest)):
                            if digest == want:
                                sig.log.emit(ui_tr_now(
                                    "Remote {name} update: staged copy "
                                    "verified by CRC-32 {crc} ({size} bytes) "
                                    "— swapping it in…").format(
                                        name=job['name'], crc=digest,
                                        size=len(job['data'])))
                                local_cmds.appendleft(("upd_release", jid))
                            else:
                                local_cmds.appendleft(
                                    ("upd_fail_rm", jid,
                                     "the staged " + job['base'] + ".new's "
                                     "CRC-32 on the Next (" + digest + ") "
                                     "differs from what was sent (" + want
                                     + ")"))
                        elif res['osp']:
                            local_cmds.appendleft(
                                ("upd_fail_rm", jid,
                                 "the far side's OS protection refused "
                                 "reading back the staged " + job['base']
                                 + ".new (Settings on the Next)"))
                        else:
                            if res['fail']:
                                why = "the staged file did not open for the crc op"
                            elif got_k:
                                why = "malformed digest from the Next"
                            else:
                                why = "no answer to the crc op"
                            log(f"crc32 {_newp}: {why} — reading the staged "
                                "copy back instead")
                            local_cmds.appendleft(("upd_getback", jid))
                    elif op == "upd_getback":
                        # Step 1, the read-back way: pull the staged file back
                        # and byte-compare. The path for listeners that
                        # predate the crc op, and the fallback when 'K' gave
                        # no verdict (see upd_verify).
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        _upd_read_back(jid, job)
                    elif op == "upd_release":
                        # Step 2: 'U' — the dot closes the OS's own read
                        # handle on its file (hardware-measured: the renames
                        # FAIL while it is open). Runs BEFORE the .bak delete
                        # so a pre-5.9 dot fails here with nothing deleted.
                        # From here on, path-based ops only.
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        res = {'ok': None}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            return True
                        _re_sendpacket(conn, b"U", 0)
                        if _re_reply_call(conn, _h) and res['ok']:
                            job['released'] = True
                            local_cmds.appendleft(("upd_rmbak", jid))
                        else:
                            local_cmds.appendleft(
                                ("upd_fail_rm", jid,
                                 "the far side did not answer the release "
                                 "op (needs .sync v5.9+ / ZXNR 1.0.3+)"))
                    elif op == "upd_rmbak":
                        # Step 3: clear the first rename's destination —
                        # NextZXOS refuses rename-onto-existing, so this rm
                        # is load-bearing. A missing .bak answers 'F': fine,
                        # the name just has to be free. ('X' is path-based,
                        # legal after the release.)
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue

                        def _h(payload):
                            return True
                        _re_sendpacket(
                            conn,
                            b"X" + (job['dir'] + "/" + job['base']
                                    + ".bak").encode(), 0)
                        if _re_reply_call(conn, _h):
                            local_cmds.appendleft(("upd_ren1", jid))
                        else:
                            local_cmds.appendleft(
                                ("upd_fail", jid,
                                 "connection dropped while clearing "
                                 + job['base'] + ".bak"))
                    elif op in ("upd_ren1", "upd_ren2"):
                        # Steps 4/5: the swap itself — two back-to-back
                        # renames, nothing between them. From here the card's
                        # state is in motion: a LOST reply is reported as
                        # unknown/mid-swap, never as "nothing changed" (the
                        # rename may have landed with only its reply lost),
                        # and every failure ends the session (post-'U'
                        # contract — no session survives a failed swap for
                        # the user to keep browsing with).
                        jid = cmd[1]
                        job = upd_jobs.get(jid)
                        if job is None:
                            _idle()
                            continue
                        cur = job['dir'] + "/" + job['base']
                        job['swap_started'] = True
                        old, new = ((cur, cur + ".bak")
                                    if op == "upd_ren1" else
                                    (cur + ".new", cur))
                        res = {'ok': None, 'osp': False}

                        def _h(payload, _r=res):
                            _r['ok'] = (payload[0:1] == b'O')
                            _r['osp'] = _re_is_osp(payload)
                            return True
                        _re_sendpacket(conn, b"V" + old.encode() + b"\x00" +
                                       new.encode(), 0)
                        got_reply = _re_reply_call(conn, _h)
                        if got_reply and res['ok']:
                            local_cmds.appendleft(
                                ("upd_ren2", jid) if op == "upd_ren1"
                                else ("upd_done", jid))
                            continue
                        upd_jobs.pop(jid, None)
                        if got_reply and op == "upd_ren1":
                            # The far side REFUSED the first rename: nothing
                            # moved, the target is untouched — but the handle
                            # is released, so the session must still end.
                            # ZXNR's OS protection (default-on over apps/,
                            # dot/, sys/, …) is the expected refuser there:
                            # name it, or the user hunts a phantom SD error.
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update failed: {reason}. "
                                "Nothing was swapped — the Next still runs "
                                "its current build.").format(
                                    name=job['name'],
                                    reason=("the far side's OS protection "
                                            "refused renaming " + job['base']
                                            + " (Settings on the Next)")
                                           if res['osp'] else
                                           ("could not rename " + job['base']
                                            + " aside"))
                                + _upd_extras_note(job),
                                (job or {}).get('brand', 'NextSync'))
                        else:
                            # ren2 refused, or either rename's reply lost:
                            # the card is (or may be) mid-swap. Delete
                            # NOTHING — .bak is the recovery copy.
                            sig.dot_update.emit(False, ui_tr_now(
                                "Remote {name} update FAILED mid-swap: the "
                                "Next may be missing {target}. If it no "
                                "longer starts, rename {backup} back to "
                                "{file} in the NextZXOS Browser (the staged "
                                "{staged} can be deleted).").format(
                                    name=job['name'],
                                    target=cur,
                                    backup=cur + ".bak",
                                    file=job['base'],
                                    staged=cur + ".new")
                                + _upd_extras_note(job),
                                (job or {}).get('brand', 'NextSync'))
                        _re_sendpacket(conn, b"Q", 0)
                        _re_goodbye_linger(conn)
                        break
                    elif op == "upd_done":
                        # Step 6: success. Report, then a TARGETED quit — the
                        # broadcast "quit" arm would stop every other session.
                        # A marked job (the ZXNR flavor) sends Q+'X' instead:
                        # the .nex saves its settings and soft-resets the Next
                        # into NextZXOS, where the swapped build relaunches.
                        jid = cmd[1]
                        job = upd_jobs.pop(jid, None) or {}
                        if job.get('marked'):
                            sig.dot_update.emit(True, ui_tr_now(
                                "Remote {name} update complete: {version} is "
                                "on the card. The Next will now soft-reset "
                                "to NextZXOS — relaunch {file} to run the "
                                "new build.").format(
                                    name=job.get('name', ''),
                                    version=job.get('ver', ''),
                                    file=job.get('base', '')),
                                (job or {}).get('brand', 'NextSync'))
                            _re_sendpacket(conn, b"Q" + RE_QUIT_EXIT_MARK, 0)
                        else:
                            sig.dot_update.emit(True, ui_tr_now(
                                "Remote {name} update complete: {version} is "
                                "on the card. The session will now close — "
                                "run {command} on the Next to start the new "
                                "build.").format(
                                    name=job.get('name', ''),
                                    version=job.get('ver', ''),
                                    command=".sync5 -listen"),
                                (job or {}).get('brand', 'NextSync'))
                            _re_sendpacket(conn, b"Q", 0)
                        _re_goodbye_linger(conn)
                        break
                    elif op == "upd_fail_rm":
                        # Failure with cleanup: delete the staged sync5.new
                        # (path-based, so legal even after 'U'), then report
                        # on the following poll.
                        jid, why = cmd[1], cmd[2]
                        job = upd_jobs.get(jid)
                        if job is not None:
                            def _h(payload):
                                return True
                            _re_sendpacket(
                                conn,
                                b"X" + (job['dir'] + "/" + job['base']
                                        + ".new").encode(), 0)
                            _re_reply_call(conn, _h)
                            local_cmds.appendleft(("upd_fail", jid, why))
                        else:
                            _idle()
                    elif op == "upd_fail":
                        jid, why = cmd[1], cmd[2]
                        job = upd_jobs.pop(jid, None)
                        sig.dot_update.emit(False, ui_tr_now(
                            "Remote {name} update failed: {reason}. Nothing "
                            "was swapped — the Next still runs its current "
                            "build.").format(
                                name=(job or {}).get('name', ''),
                                reason=why)
                            + _upd_extras_note(job),
                            (job or {}).get('brand', 'NextSync'))
                        if job is not None and job.get('released'):
                            # The dot's own handle is already closed: the
                            # post-'U' contract forbids any op that opens a
                            # file or directory, so the session cannot stay
                            # up for the user to browse with.
                            _re_sendpacket(conn, b"Q", 0)
                            _re_goodbye_linger(conn)
                            break
                        _idle()

                elif data == b"Get" or data == b"Gee":
                    n = min(max_payload, len(put_data) - put_ofs)
                    last_packet = put_data[put_ofs:put_ofs + n]
                    _re_sendpacket(conn, last_packet, put_pkt)
                    put_ofs += n
                    put_pkt += 1
                    xfer['done'] = put_ofs
                    if put_ofs >= len(put_data) and pending and pending[0] == "put":
                        # Report the round trip only when it was NOT clean, so
                        # a healthy transfer stays silent and a troubled one
                        # says so in one line.
                        if put_retry or put_restart:
                            _re_trace(
                                "put finished with %d retry/retries and %d "
                                "restart(s) over %d packet(s) - the link is "
                                "corrupting data, not losing it"
                                % (put_retry, put_restart, put_pkt))
                        put_retry = 0
                        put_restart = 0
                        _put_finish(True)
                        pending = None
                elif data == b"Retry":
                    # Checksum failure on the Next: it wants the SAME packet
                    # again. Rare when the wire is healthy.
                    put_retry += 1
                    if put_retry <= 3 or put_retry % 10 == 0:
                        _re_trace("Next asked to retry packet %d (%d so far)"
                                  % ((put_pkt - 1) & 0xff, put_retry))
                    _re_sendpacket(conn, last_packet, (put_pkt - 1) & 0xff)
                elif data == b"Restart":
                    # PACKET-NUMBER mismatch on the Next: the two sides'
                    # counters have drifted and it is starting the file over.
                    # This is the storm that hung the 5.9.2 dot - it gives up
                    # after 6 in a row, so a burst approaching that is the
                    # whole diagnosis, and it is visible from here without a
                    # single byte spent in the dot.
                    put_restart += 1
                    _re_trace(
                        "Next restarted the transfer at packet %d (%d of the "
                        "6 it allows before giving up)" % (put_pkt, put_restart))
                    put_ofs = 0
                    put_pkt = 0
                    xfer['done'] = 0          # the Next is starting over
                    _idle(b"Back")
                elif data == b"Bye":
                    _idle(b"Later")
                    break
                else:
                    _idle()   # keep the Next polling
    except _ReLinkDead as ex:
        # EOF under a retryable UI command (the session's reply wrapper):
        # hold it for the seat that comes back - no report now, the retry's
        # own outcome is the report (9.7.20).
        _stash_retry("the Next dialed in again" if _evicted()
                     else "the link went down")
        _fail_owed(f"the -listen session ended: {ex}")
    except _ReLinkGone as ex:
        # The link went under a reply that owed no report (see _idle): no
        # command was lost, so no error signal - the widget would count it
        # as a step for a command that is still queued for the newcomer.
        if _evicted():
            _bye_evicted()
        else:
            log(ui_tr_now(
                "Remote explorer: connection error from the Next "
                "({error}) — session over.").format(error=ex))
            logging.warning(
                "Remote explorer: connection error from the Next: %s", ex)
        _fail_owed(f"the -listen session ended: {ex}")
    except OSError as ex:
        # A command WAS in flight (the no-report replies raise _ReLinkGone
        # above). The widget counts ONE step per command that reports back,
        # and an error signal is one such report - so it is emitted only
        # when nobody else reports this death: a UI put being pulled, a
        # verify or update job, an rmtree walk all get their one report
        # from the finally, and a bridge command's sink from _fail_owed
        # (bridge traffic is silent to the UI). Otherwise the operation
        # would end a step early (9.7.20).
        if _retry_eligible() and not _ui_put_pending():
            # Held for the seat that comes back (a put pending is held by
            # the finally instead, which owns its report).
            _stash_retry("the Next dialed in again" if _evicted()
                         else "the link went down (%s)" % ex)
        elif _evicted():
            _retry_spent("the Next dialed in again")
            # Sessions Off: the link was shut down under a command because
            # the Next dialed in again. Say so, in the words the widget's
            # failure toast will repeat.
            _msg = ui_tr_now(
                "the Next dialed in again while a command was in flight — "
                "Sessions is Off, so the old link was dropped and that "
                "command was lost")
            if _report_owed_elsewhere():
                log("Remote explorer: " + _msg)
            else:
                sig.error.emit(_msg)
        elif _report_owed_elsewhere():
            log(ui_tr_now(
                "Remote explorer: connection error from the Next "
                "({error}) — session over.").format(error=ex))
            logging.warning(
                "Remote explorer: connection error from the Next: %s", ex)
        else:
            _retry_spent("the link went down (%s)" % ex)
            sig.error.emit(f"Remote explorer server error: {ex}")
        _fail_owed(f"the -listen session ended: {ex}")
    except Exception as ex:                                   # noqa: BLE001
        # Never let an unforeseen error strand a blocked HTTP caller in
        # silence: report it, answer whoever is waiting. (No re-raise --
        # one session dying must not take the whole server down.)
        logging.exception("Remote explorer session: unhandled error")
        if _report_owed_elsewhere():
            log(f"Remote explorer server error: {ex}")   # the finally reports
        else:
            sig.error.emit(f"Remote explorer server error: {ex}")
        _fail_owed(f"the -listen session failed: {ex}")
    finally:
        # Clean exits owe answers too -- a "quit" mid-transfer, the Next
        # dropping the link, or the user stopping the server.
        _fail_owed("the -listen session ended before the command finished")
        # A UI put the Next was still pulling when the session ended
        # (9.7.20): settle its ONE put_done. The widget's operation counts
        # it, logs "Upload failed: <file>" and names it in the end-of-op
        # toast, instead of hanging one step short — a link that dies
        # mid-pull ends the session from the idle recv, never through
        # _put_finish. The except arms above keep their console line out
        # of the error signal in this case, so this is the step's only
        # report; with Sessions Off the rest of the queued copy carries on
        # over the link the Next dialed back in on.
        if _ui_put_pending():
            if _retry_eligible():
                _stash_retry("the Next dialed in again" if _evicted()
                             else "the link went down mid-file")
            else:
                _retry_spent("the link went down mid-file")
                _log_xfer_stopped('put', pending[1])
                sig.put_done.emit(False, pending[1])
        # A get the session died under (9.7.21): its arm never reached the
        # failure tail, so the "how far did it get" line is owed here. The
        # command's own report is the retry (held) or the except arm's
        # error, so this adds the number and nothing else.
        if xfer['kind'] == 'get' and xfer['live'] and not stashed:
            _log_xfer_stopped('get', '')      # '' -> the record's own path
        # An rmtree walk still open (9.7.20 review): the widget enqueued ONE
        # ('rmtree', root) and waits for its one op_done; the walk's steps
        # are served one per Poll, so a link that died between two of them
        # never reached a reporting arm. A bridge job's sink was _owe'd at
        # pop time and _fail_owed answered it above.
        for _job in rmtree_jobs.values():
            if not _job.get('reply'):
                sig.op_done.emit(False, "delete", _job['root'])
        rmtree_jobs.clear()
        # An update job still open here means the session died mid-macro and
        # the loop never reached a terminal arm: emit the exactly-once
        # dot_update verdict now. A job whose swap had started gets the
        # recovery wording -- the card's state is unknown and the .bak
        # instructions are the one message that must not be lost.
        for _job in upd_jobs.values():
            _cur = _job['dir'] + "/" + _job.get('base', 'sync5')
            if (_job.get('extras') and not _job.get('staged')
                    and _job['ex_i'] < len(_job['extras'])
                    and _job['extras'][_job['ex_i']][0] == "sibling"):
                # Died while the other flavor's build was in flight.
                _ex = _job['extras']
                sig.dot_update.emit(False, ui_tr_now(
                    "Remote {name} update failed: the session ended while "
                    "sending the other flavor's build {path} — it may be "
                    "missing or cut short on the card. Nothing was swapped "
                    "— run the update again.").format(
                        name=_job.get('name', ''),
                        path=_job['dir'] + "/" + _ex[_job['ex_i']][-1])
                    + _upd_extras_note(_job),
                    _job.get('brand', 'NextSync'))
            elif (_job.get('extras') and not _job.get('staged')
                    and _job['ex_i'] < len(_job['extras'])):
                # Died among the deploypak.txt extras: the one in flight
                # may be missing or cut short on the card (they overwrite
                # in place), the ones before it are already the new
                # build's — and the build itself was never staged.
                _ex = _job['extras']
                sig.dot_update.emit(False, ui_tr_now(
                    "Remote {name} update failed: the session ended while "
                    "sending {path} from deploypak.txt — it may be missing "
                    "or cut short on the card, and {landed} of the {total} "
                    "file(s) the manifest lists had already been replaced. "
                    "Nothing was swapped — run the update again to send "
                    "them all.").format(
                        name=_job.get('name', ''),
                        path=_job['dir'] + "/" + _ex[_job['ex_i']][-1],
                        landed=len(_job.get('ex_sent', ())),
                        total=_upd_extra_total(_job))
                    + "".join(" " + ui_tr_now(
                        "The other flavor's build {path} had already been "
                        "replaced on the card too.").format(
                            path=_job['dir'] + "/" + r)
                        for r in _job.get('sib_sent', ())),
                    _job.get('brand', 'NextSync'))
            elif (_job.get('extras') and not _job.get('staged')
                    and _upd_extra_total(_job)):
                # Died between the last companion landing and the Poll that
                # would have staged the build: every companion is on the
                # card, verified; nothing was in flight.
                sig.dot_update.emit(False, ui_tr_now(
                    "Remote {name} update failed: the session ended after "
                    "all {total} deploypak.txt file(s) had been replaced on "
                    "the card, before the build itself was staged. Nothing "
                    "was swapped — run the update again to send them "
                    "all.").format(
                        name=_job.get('name', ''),
                        total=_upd_extra_total(_job))
                    + "".join(" " + ui_tr_now(
                        "The other flavor's build {path} had already been "
                        "replaced on the card too.").format(
                            path=_job['dir'] + "/" + r)
                        for r in _job.get('sib_sent', ())),
                    _job.get('brand', 'NextSync'))
            elif _job.get('extras') and not _job.get('staged'):
                # The same gap, for a package with no manifest: only the
                # other flavor's build landed — say that, not "all 0 files".
                _last = (_job.get('sib_sent') or [_job['extras'][-1][-1]])[-1]
                sig.dot_update.emit(False, ui_tr_now(
                    "Remote {name} update failed: the session ended after "
                    "the other flavor's build {path} had been replaced on "
                    "the card, before the build itself was staged. Nothing "
                    "was swapped — run the update again.").format(
                        name=_job.get('name', ''),
                        path=_job['dir'] + "/" + _last),
                    _job.get('brand', 'NextSync'))
            elif _job.get('swap_started'):
                sig.dot_update.emit(False, ui_tr_now(
                    "Remote {name} update FAILED mid-swap: the Next may be "
                    "missing {target}. If it no longer starts, rename "
                    "{backup} back to {file} in the NextZXOS Browser (the "
                    "staged {staged} can be deleted).").format(
                        name=_job.get('name', ''),
                        target=_cur,
                        backup=_cur + ".bak",
                        file=_job.get('base', ''),
                        staged=_cur + ".new")
                    + _upd_extras_note(_job),
                    _job.get('brand', 'NextSync'))
            else:
                sig.dot_update.emit(False, ui_tr_now(
                    "Remote {name} update failed: {reason}. Nothing was "
                    "swapped — the Next still runs its current "
                    "build.").format(
                        name=_job.get('name', ''),
                        reason="the session ended before the update "
                               "finished")
                    + _upd_extras_note(_job),
                    _job.get('brand', 'NextSync'))
        upd_jobs.clear()
        # 9.7.3: a verify still owed here = the session died between the put
        # and its verdict. Settle its ONE put_done - never silently: a copy
        # already known corrupt goes red (undeleted), an unchecked one is
        # reported sent-but-unverified.
        for _vj in vjobs.values():
            if _vj['state'] in ('mismatch', 'deleting'):
                log(f"crc32 {_vj['remote']}: corrupted copy NOT deleted "
                    "(the session ended first)")
                sig.put_verify_failed.emit(ui_tr_now(
                    "CRC-32 verification FAILED for {path}: {sent} was sent "
                    "but the Next holds {got}. The corrupted copy could NOT be "
                    "deleted from the Next ({reason}) — remove it by hand and "
                    "send the file again.").format(
                        path=_vj['remote'], sent=_vj['crc'], got=_vj['got'],
                        reason="the session ended before the corrupted copy "
                               "could be deleted"))
                sig.put_done.emit(False, _vj['remote'])
            else:
                log(f"crc32 {_vj['remote']}: not verified (the session ended "
                    "before the check) — file kept")
                sig.put_done.emit(True, _vj['remote'])
        vjobs.clear()
        # Session-TARGETED commands still queued (never taken) would
        # otherwise strand their HTTP callers for the full bridge timeout:
        # fail them now, with the 410 the bridge maps to "session gone".
        while True:
            try:
                c = my_q.get_nowait()
            except queue.Empty:
                break
            r = c[-1] if c and isinstance(c[-1], BridgeReply) else None
            if r is not None:
                r.put({'ok': False, 'http': 410,
                       'error': f"session {sid} is gone - "
                                "GET /sessions for the live list"})
        try:
            conn.close()
        except OSError:
            pass


def run_remote_listen_server(sig, cmd_queue, stop_event, port=2048,
                             max_payload=512, control=None, verify_crc=None,
                             sessions=None):
    # max_payload is 512, not the protocol's 1024 cap: ZXNextRemote's bench
    # testing found Next CLONES (N-Go) corrupt >512-byte continuous UART
    # bursts at the Medium/Fast rates — deterministically, close checksums —
    # and its own server dropped to 512-byte data chunks for exactly that.
    # A put served from here with 1024-byte frames hit the same wall
    # (2026-08-07: "put FAIL ... r1" on the N-Go, dead session, bridge 502).
    # Real Nexts and the dot are indifferent; the cost is one extra 5-byte
    # frame header per KB.
    """Run the NextSync ``.sync5 -listen`` remote file server in a worker thread.

    Waits for a Next running ``.sync5 -listen`` to connect, then drives it from
    commands pulled off ``cmd_queue`` (a queue.Queue), emitting results through
    ``sig`` (a RemoteExplorerSignals). Commands are tuples:
        ("ls",    remote_path)
        ("get",   remote_path, local_dest_dir)
        ("put",   local_file,  remote_path)
        ("mkdir", remote_path)
        ("rmdir", remote_path)
        ("rm",    remote_path)
        ("rmtree", remote_path)   -> recursive folder delete (see below)
        ("drives",)               -> query mounted drives (see below)
        ("version",)              -> ask who serves the session + its build
        ("free",  drive_letter)   -> query a partition's free space (see below)
        ("rcpy",  src, dst)       -> copy locally ON the Next (see below)
        ("fsize", remote_path)    -> total size of a file/tree (see below)
        ("crc",   remote_path)    -> CRC-32 of one file, computed on the Next
                                  -> also asked automatically after each UI
                                     put when verify_crc() is on (9.7.3)
        ("rename", old_path, new_path)
        ("update_dot", local_file, remote_dir, version
                       [, base_file, brand, marked_exit, extras])
                                  -> remote self-update macro: send the
                                     ``extras`` first (9.7.6: the
                                     read_deploypak plan of a package's
                                     deploypak.txt — mkdir / put steps under
                                     remote_dir, each put crc-checked with
                                     'K' and re-sent up to
                                     RE_UPD_EXTRA_RETRIES times), then stage
                                     local_file as <remote_dir>/sync5.new
                                     ('P'), verify the staged copy — its
                                     CRC-32 computed on the Next ('K', dot
                                     5.9.2+ / ZXNR 1.0.8+ per the session's
                                     cached 'Y' ident) against zlib.crc32 of
                                     the bytes served, else pulled back and
                                     byte-compared ('G'; also the fallback
                                     when 'K' gives no verdict) — have the
                                     dot release its own file
                                     handle ('U', dot v5.9+ — BEFORE the .bak
                                     delete, so an old dot fails with nothing
                                     deleted), clear sync5.bak ('X'), swap
                                     with two renames ('V'), then a TARGETED
                                     quit of that session. Steps ride
                                     local_cmds so nothing can interleave;
                                     once released, every failure also ends
                                     the session (post-'U' contract); the
                                     outcome fires the dot_update signal
                                     exactly once, session death included.
                                     The trailing three parameters default
                                     to the dot ("sync5", "NextSync", plain
                                     quit); the ZXNR flavor passes its .nex
                                     file name, "ZXNextRemote", and
                                     marked_exit=True (Q+'X': the .nex saves
                                     settings and soft-resets into NextZXOS,
                                     ZXNR 1.0.3+ answers 'U').
        ("mark",  token)          -> echoes back via sig.marked once reached
        ("quit",)
    ``stop_event`` (threading.Event) ends the session/thread.

    ``mark`` is a client-side barrier: it touches nothing on the Next, it just
    emits ``marked(token)`` the moment the queue drains down to it. Because the
    queue is a single-consumer FIFO, everything enqueued before the marker has
    finished by then -- the UI uses this to know a cut/move's transfer completed
    before deleting the source.

    ``rmtree`` deletes a whole folder on the Next: esxDOS rmdir only removes
    *empty* directories, so the worker walks the tree itself over the ordinary
    protocol -- ls each directory, rm its files, recurse into its sub-folders,
    then rmdir it once empty (bottom-up). The walk runs as internally queued
    sub-commands served one per "Poll", exactly like user commands, and reports
    a single op_done(ok, "delete", root) when the root folder is gone (ok only
    if every file and folder inside deleted cleanly). A user cancel drains the
    host queue only, so an rmtree already underway finishes on its own -- same
    "stop after the current item" semantics as a cancelled transfer.

    ``drives`` sends the 'W' (getdrives) command; the Next replies with one
    status block 'O' + <current drive letter> + <mounted letters>, emitted as
    ``drives(current, [letters])``. Every remote path in the other commands may
    carry a drive prefix ("M:/games"); a path without one lands on the dot's
    current drive, so nothing changes for pre-drive-aware flows. A dot older
    than v5.1 ignores the unknown 'W' and just re-polls: its raw "Poll" fails
    the block parse, ``drives("", [])`` is emitted as the fallback, and the
    stray bytes are re-synced by the outer loop's catch-all idle reply.

    ``free`` sends the 'Z' command (dot v5.2+) with an optional drive letter
    ("" = the dot's current drive); the Next replies with one status block
    'O' + 4 bytes little-endian = free 512-byte blocks (F_GETFREE), or 'F'
    when the drive can't be measured, emitted as ``free_space(drive, bytes)``
    (bytes None on failure / pre-v5.2 dots, which degrade exactly like
    ``drives``).

    ``rcpy`` sends the 'C' command (dot v5.2+) with the source and destination
    paths NUL-separated (like ``rename``); the whole copy - a file or a
    recursive directory tree, across partitions too - runs ON the Next, no
    data through the PC. The Next pushes 'D' progress blocks (a named one per
    file, empty keepalives every 64KB inside big files) and ends with one
    'O'/'F', reported as ``op_done(ok, "copy", src)``. The reply socket
    timeout is temporarily widened: SD-card copies are slow and the
    keepalives only bound the gaps to ~64KB of local I/O.

    ``fsize`` sends the 'S' command (rfsize, dot v5.2+): the Next measures a
    file or a whole directory tree (rcpy's "will it fit" companion), pushing
    'D' progress blocks (one per directory + keepalives) and a terminal
    'O' + [4B files][4B dirs][4B size_lo][2B size_hi] or 'F'. Reported as
    ``op_done(ok, "size", path)`` followed by ``fsize(path, data)`` (data
    None on failure). Socket timeout widened like ``rcpy``.

    This is the app-side twin of nextsync5.py's listen_session: same wire
    protocol, but driven by the UI queue and reporting via Qt signals instead of
    a console CLI. It never touches the Sync3/Sync4 sync paths.

    ``verify_crc`` is an optional 0-arg callable read at each put's
    completion (the Settings toggle, no restart). When true, a UI put (no
    BridgeReply, not an update_dot stage) is followed on local_cmds by
    ``put_verify`` -- 'K' on the resolved path, gated on the session's cached
    'Y' ident (dot >= 5.9.2 / ZXNR >= 1.0.8; older peers get one advisory and
    stay unverified) -- and, on a DIFFERENT 8-hex digest, ``put_verify_rm``
    ('X', then put_verify_failed + put_done(False)). 'F', 'F'+OSP, silence or
    a malformed digest keep the file and report put_done(True). One put_done
    per put, always; bridge puts keep /put + /crc as the caller's own
    two-step.

    ``sessions`` (9.7.20) is an optional 0-arg callable read at each DIAL
    (Settings → "NextSync — Sessions"; None = on). On is the multi-Next
    roster above: up to RE_MAX_PEERS seats, a further dialer turned away
    Busy. Off is ONE seat: a Next dialing in while one is seated is taken
    for the SAME machine coming back over a dead link (its ESP module
    reset, its Wi-Fi blinked — the wire carries no machine identity, so
    this is the operator's assertion, the very one ZX Next Remote 1.2.5's
    n2n "Sessions" row makes, and the pairing for its Listener's re-dial
    ladder). The held link is shut down, its session ends without popping
    another command (the seat's ``evicted`` flag gates _pop_shared), and
    the newcomer takes the seat under the SAME sid with the baton at once,
    instead of sitting benched behind the zombie until PEER_SILENCE_LIMIT
    reaps it. The roster the widget sees may therefore not change at all,
    which is the point: the pane keeps its listing and its cached ident,
    and a bridge client's cached ``?session=N`` keeps driving the machine
    it named. A command in flight on the old link is lost and reported
    (its arm's own failure path, or put_done(False) for a put the Next was
    still pulling); the rest of the shared queue is served by the
    newcomer; the newcomer is asked its build again ("version" on its own
    queue, so the widget's and the bridge's sid-keyed ident caches refresh)
    and inherits every targeted command still parked on the old seat's
    queue. ``connected`` is not re-emitted (the roster never emptied).
    control['max_peers'] tracks the mode for GET /sessions. Off asserts
    that ONE Next targets this PC: two would evict each other for ever,
    and a different Next dialing in is treated as the same one.

    Off also RETRIES (9.7.20): a UI command the link died under (a get,
    put, ls, mkdir, rmdir, rm, rename, rcpy or fsize from the shared queue
    - RE_LINK_RETRY_OPS) is held in ``control['retry']`` instead of being
    reported, and the seat that comes back re-runs it after
    RE_LINK_RETRY_PAUSE_S, up to RE_LINK_RETRIES times ("retry1 in 3s: get
    /x (the link went down)" then "retry1: get /x" in the log); while it is
    held and no seat is up this loop keeps listening for up to
    RE_LINK_RETRY_WAIT_S instead of returning, so the widget's operation
    stays open across the Next's re-dial. The held command's ONE report is
    the retry's own outcome, or - retries spent, the deadline passed, the
    mode flipped to On - put_done(False) / error from _re_retry_give_up.
    """
    def log(msg):
        sig.log.emit(msg)

    # The caller's dict, defaulted HERE and not further down (9.7.20 review):
    # the bind-failure path below returns from inside the try, and the
    # finally's control.pop() then ran on the None default - AttributeError,
    # so `disconnected` was never emitted, the pane never relistened and a
    # widget operation never ended. A port already in use is a real path
    # (a second Unite, a leftover server), and the suite hits it too.
    control = control if control is not None else {}
    # This worker RUN's generation. control survives worker restarts, so a
    # link-loss retry must say which run stashed it: a session dying after
    # its worker's finally had popped could otherwise leave one behind for
    # the NEXT worker to run against whatever Next dials in - the
    # wrong-machine hazard this file refuses everywhere else.
    re_gen = int(control.get('gen', 0)) + 1
    control['gen'] = re_gen

    srv = None
    try:
        try:
            srv = bind_listen_socket(port)
        except OSError as ex:
            # Port already taken - almost always another ZX-Next-Unite (or a
            # standalone NextSync server) already listening on it. Signal the UI
            # to warn (yellow toast) instead of failing with a cryptic error, and
            # bail cleanly so the Next just sees "no server" rather than us
            # half-starting.
            if is_address_in_use(ex):
                sig.port_in_use.emit(port)
            else:
                sig.error.emit(f"Remote explorer server error: {ex}")
            return
        srv.settimeout(1.0)
        # '.sync5 -listen' is interpolated, never translated: it is a command
        # the user must type on the Next exactly as shown.
        log(ui_tr_now("Remote explorer: waiting for {command} on port {port}…")
            .format(command="'.sync5 -L' (-l or -listen)", port=port))

        # ---- the multi-Next roster (option B) --------------------------
        # One session thread per connected Next (_re_session), a shared
        # roster, and ONE active session that the host/bridge command
        # queue feeds. The accept loop below is also the reaper: it runs
        # on the listen socket's 1 s timeout, notices ended sessions,
        # hands the baton on when the active one dies, and -- preserving
        # the single-session lifecycle the pane's auto-relisten relies on
        # -- RETURNS once at least one Next connected and the last of
        # them has gone (the finally emits disconnected, the pane
        # relistens). Commands never wait on this loop: the active
        # session pops the shared queue directly on every poll.
        peers = {}                     # sid -> {'addr', 'q', 'thread'}
        plock = threading.Lock()
        # The sid counter is seeded from (and written back to) the caller's
        # ``control`` dict so it SURVIVES worker restarts: the worker returns
        # whenever the last Next leaves and the pane relistens with a fresh
        # one, and a restarting counter would let a remote client's cached
        # "session=1" silently drive a DIFFERENT machine. Sids are therefore
        # unique for the whole app run, and a stale sid can only mean "that
        # Next left" (the bridge answers 410), never "someone else".
        # (``control`` itself is defaulted at the top of the function, above
        # the bind, so every exit path finds a dict.)
        state = {'active': None, 'seq': int(control.get('seq', 0)),
                 'had_any': False}

        def _sessions_on():
            # Settings → "NextSync — Sessions" (9.7.20), read PER DIAL the
            # way verify_crc is read per put: a flip applies to the next
            # Next that connects, no restart. None (nextsync5-style bare
            # calls, the tests) = the multi-Next roster.
            if sessions is None:
                return True
            try:
                return bool(sessions())
            except Exception:                                 # noqa: BLE001
                logging.exception("Remote explorer: sessions hook failed")
                return True

        def _emit_peers():
            with plock:
                payload = (state['active'],
                           [(s, p['addr']) for s, p in sorted(peers.items())])
            sig.peers.emit(payload)

        shared = {'peers': peers, 'lock': plock, 'state': state,
                  'emit_peers': _emit_peers, 'verify_crc': verify_crc,
                  'control': control, 'sessions_on': _sessions_on,
                  'gen': re_gen}

        # ---- the control surface (HTTP bridge -> this worker) ----------
        # Both closures take plock themselves, so a bridge thread's check
        # and delivery cannot straddle a departure. After this worker
        # returns, ``peers`` is empty and they degrade to False/[] until a
        # relisten installs fresh ones over the same dict.
        def _roster():
            with plock:
                return (state['active'],
                        [(s, p['addr']) for s, p in sorted(peers.items())])

        def _enqueue_to(sid, cmd):
            with plock:
                p = peers.get(sid)
                if p is None:
                    return False
                p['q'].put(cmd)
                return True

        control['roster'] = _roster
        control['enqueue_to'] = _enqueue_to
        control['max_peers'] = RE_MAX_PEERS if _sessions_on() else 1

        while not stop_event.is_set():
            # Reap ended sessions; hand the baton on if the active died —
            # to the LAST-CONNECTED survivor (max sid), the machine the
            # user most recently brought to the party (field request:
            # min() handed it to the oldest, which read as arbitrary).
            with plock:
                dead = [d for d, p in peers.items()
                        if p['thread'] is not None
                        and not p['thread'].is_alive()]
                for d in dead:
                    del peers[d]
                if state['active'] not in peers:
                    state['active'] = max(peers) if peers else None
            if dead:
                _emit_peers()
            if state['had_any'] and not peers:
                # Last Next gone -> return (the pane relistens) - unless a
                # link-loss retry is held (9.7.20): then keep LISTENING for
                # the Next to dial back in, up to the retry's deadline, so
                # the widget's operation stays open and the command runs on
                # the returning seat's first Poll after the pause. Sessions
                # On (a flip under a held retry) means nobody may run it.
                r = control.get('retry')
                if r is not None and r.get('gen') != re_gen:
                    control.pop('retry', None)      # an earlier run's
                    r = None
                waiting = (r is not None and not _sessions_on()
                           and time.monotonic() < r['deadline'])
                if not waiting:
                    if r is not None:
                        control.pop('retry', None)
                        _re_retry_give_up(
                            sig, r,
                            "Sessions is On" if _sessions_on() else
                            "the Next did not come back within %ds"
                            % int(RE_LINK_RETRY_WAIT_S))
                    return             # last Next gone -> disconnected

            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue

            # The Next opens with the raw "Listen" handshake keyword.
            # READ IT PATIENTLY (2026-08-13 field report): the old code
            # did ONE recv on a socket that had inherited the listen
            # socket's 1 s timeout and demanded the whole keyword in one
            # piece -- over the ESP's Wi-Fi TCP the keyword can arrive
            # split, arrive late (the dot's CIPSEND round-trip alone can
            # eat the second), or a connect-retry probe can close without
            # a byte. Every one of those read as "did not request -listen
            # mode" AND KILLED THE WHOLE SERVER, which the pane then
            # relistened -- the "server stops by itself" console loop.
            # Now: 10 s window, accumulate to 6 bytes, and NO outcome
            # here brings the server down.
            try:
                conn.settimeout(10.0)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    # Belt and braces to the per-session silence timer:
                    # the OS eventually reaps half-open sockets itself.
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except OSError:
                    pass
                data = b""
                while len(data) < 6:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                # Timeout or reset mid-handshake: a probe, not a peer.
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            if not data:
                # Connected and closed without a word -- the ESP's
                # connect-retry does this. Not an error, not a log line:
                # the real attempt is right behind it.
                logging.info("Remote explorer: silent probe from %s",
                             addr[0])
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            if data[:6] != b"Listen":
                # A classic-sync (or unknown) client: refuse THIS
                # connection but keep serving -- Classic Sync has its own
                # server and the pane keeps the two off one port anyway.
                sig.error.emit("Connected client did not request -listen mode.")
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            single = not _sessions_on()
            with plock:
                control['max_peers'] = 1 if single else RE_MAX_PEERS
                # Sessions Off never turns a dialer away: the newcomer
                # EVICTS the held seat below (the same Next, coming back).
                full = (not single) and len(peers) >= RE_MAX_PEERS
            if full:
                # Over capacity: the option-A turn-away. Busy-aware dots
                # (5.7.2+) print "Server busy"; older ones fail fast.
                try:
                    _re_sendpacket(conn, b"Busy", 0)
                except OSError:
                    pass
                try:
                    conn.close()
                except OSError:
                    pass
                log(ui_tr_now(
                    "Remote explorer: turned away a second Next at "
                    "{address} — a session is already active (Busy).")
                    .format(address=addr[0]))
                logging.info(
                    "Remote explorer: turned away newcomer %s (busy)",
                    addr[0])
                continue

            try:
                _re_sendpacket(conn, b"Listening", 0)
            except OSError:
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            log(ui_tr_now("Remote explorer: connected to {address}").format(
                address=addr[0]))
            with plock:
                # `first` BEFORE any eviction: with Sessions Off the roster
                # never empties between the old seat and the new, and a
                # second `connected` would make the widget reset the very
                # pane this mode exists to keep.
                first = not peers
                evicted = []
                sid = None
                my_q = queue.Queue()
                if single and peers:
                    # Sessions Off (9.7.20): ONE seat. The newcomer is the
                    # same Next coming back over a dead link, so it takes
                    # the DRIVEN seat's id — the machine the pane drives;
                    # several seats can only be held here if the setting
                    # was flipped while they were up, and then every one
                    # of them goes. The evicted flag stops each old
                    # session from popping the shared queue from this
                    # moment (it holds the same sid, so the active check
                    # alone would not); the shutdown below wakes its recv
                    # so it ends now, not at PEER_SILENCE_LIMIT.
                    sid = (state['active'] if state['active'] in peers
                           else max(peers))
                    evicted = sorted(peers.items())
                    # Ask the returning Next its build again, at its first
                    # Poll (9.7.20 review): the widget's ident cache, the
                    # top bar's update offer and the bridge's /version-*
                    # are all keyed by the sid, which does not change here,
                    # so nothing else would re-ask - and a Next that came
                    # back running a DIFFERENT build (the hand-copied dot of
                    # a hang recovery, ZXNR instead of the dot) would be
                    # offered the wrong update. A targeted command a bridge
                    # client parked on the old seat's queue while the link
                    # was dead moves over too: the sid it named is THIS
                    # seat, and its caller was promised continuity, not a
                    # 410 for a session that is on the roster.
                    #
                    # The ('version',) FIRST is also load-bearing for
                    # the link-loss retry (9.7.20): it costs the
                    # newcomer a whole 'Y' round trip with the Next
                    # before it can reach _pop_shared, which is what
                    # guarantees the evicted session (woken by the
                    # _re_drop_link below, before this thread even
                    # starts) has stashed its retry by then. Serve the
                    # shared queue ahead of this and a newcomer could
                    # pop the NEXT command before the cut one is held,
                    # running the batch out of order.
                    my_q.put(("version",))
                    for _s, _p in evicted:
                        _p['evicted'] = True
                        while True:
                            try:
                                my_q.put(_p['q'].get_nowait())
                            except queue.Empty:
                                break
                    peers.clear()
                if sid is None:
                    state['seq'] += 1
                    sid = state['seq']
                    control['seq'] = sid   # persists across worker restarts
                seat = {'addr': addr[0], 'q': my_q, 'thread': None,
                        'conn': conn, 'evicted': False}
                peers[sid] = seat
                if state['active'] is None or single:
                    state['active'] = sid
                state['had_any'] = True
            for _s, _p in evicted:
                log(ui_tr_now(
                    "Remote explorer: {address} dialed in while {old} was "
                    "seated — Sessions is Off, so it is taken for the same "
                    "Next coming back: the old link is dropped and the "
                    "newcomer takes its place.").format(
                        address=addr[0], old=_p['addr']))
                logging.info(
                    "Remote explorer: single-seat mode — %s takes seat #%s "
                    "over from %s; the old link is shut down",
                    addr[0], _s, _p['addr'])
                _re_drop_link(_p.get('conn'))
            th = threading.Thread(
                target=_re_session,
                args=(sid, conn, addr, my_q, sig, cmd_queue, stop_event,
                      shared, max_payload),
                kwargs={'seat': seat},
                daemon=True)
            with plock:
                seat['thread'] = th
            th.start()
            # Roster BEFORE connected (9.5.14): the widget's on_connected
            # consults the active peer's ADDRESS for its per-machine
            # remembered folder, so the roster must already be in its
            # hands — cross-thread queued signals preserve this order.
            _emit_peers()
            if first:
                sig.connected.emit()   # 0 -> 1: "a Next is available"
    except Exception as ex:                                   # noqa: BLE001
        # Never die silently: report, then re-raise so the failure still
        # reaches the log/crash handler.
        logging.exception("Remote explorer server: unhandled error")
        sig.error.emit(f"Remote explorer server error: {ex}")
        raise
    finally:
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        # Sessions notice stop_event/socket death on their own 1 s
        # cadence; the port above is already free for a relisten. A retry
        # still held here (a stop, an error) dies with the worker: the
        # widget's on_disconnected ends the operation it belonged to. Only
        # THIS run's own (a later worker's must survive our late finally;
        # one stashed later still by our own dying session carries our dead
        # generation and the next worker drops it).
        if (control.get('retry') or {}).get('gen') == re_gen:
            control.pop('retry', None)
        sig.disconnected.emit()


class HdfTaskSignals(QObject):
    """Signals for background hdfmonkey task workers.

    The HdfTaskWorker that creates the instance is its only holder; the
    dialog and log slots a caller connects merely listen (nobody keeps the
    worker either once it is handed to the pool). So at application exit
    the object can already be destroyed while the pool thread is still
    finishing run(), and any emit then raises "Signal source has been
    deleted" - which HdfTaskWorker._emit swallows on purpose."""
    progress  = Signal(int)   # 0-100
    status    = Signal(str)   # "action line\nfilename line"
    finished  = Signal()
    error     = Signal(str)   # human-readable error message
    cancelled = Signal()      # emitted when the worker stopped early due to cancel


class HdfMonkeyMissingSignals(QObject):
    """Emitted (possibly from a worker thread) when hdfmonkey appears to be
    missing/unrunnable, so the UI thread can offer to download/install it."""
    missing = Signal()


class NextSyncConflictSignals(QObject):
    """Marshals a 'received file/dir already exists locally' prompt from the
    NextSync receive worker thread to the UI thread. The worker emits ``prompt``
    with a result holder + a threading.Event and blocks on the event until the
    UI slot records the user's choice and sets it."""
    prompt = Signal(str, str, object, object)   # (name, local_path, result_holder, done_event)


class EspEmuSignals(QObject):
    """Marshals espemu (RS232 ESP emulation) log lines from its socket
    thread onto the UI thread - same job as MameProcessSignals below for
    the MAME reader thread: add_main_log_window touches Qt widgets and
    must only ever run on the main thread."""
    line = Signal(str)


class MameProcessSignals(QObject):
    """Signals used to marshal output from a detached MAME process back to the
    main (UI) thread. A background reader thread emits ``output`` for every
    captured line and ``finished`` with the process exit code when MAME ends."""
    output   = Signal(str)   # one captured stdout/stderr line
    finished = Signal(int)   # process return code


class MameInstallSignals(QObject):
    """Marshals updates from the MAME auto-install worker thread to the UI
    thread, so each step of the download-then-extract job can be reported as it
    happens. Connect with ``Qt.QueuedConnection`` (the emits originate on a
    worker thread). The owner must keep a reference to the instance until the job
    finishes, otherwise pending queued emits are cancelled when it is GC'd."""
    status   = Signal(str)   # human-readable phase line for the log window
    progress = Signal(int)   # 0-100 download progress (button text)


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
            self._emit("error", str(exc))
        finally:
            if self.cancel_event.is_set():
                self._emit("cancelled")
            self._emit("finished")

    def _emit(self, name, *args):
        """Emit ``signals.<name>``, unless the owner has already gone.

        At application exit the owner drops its reference while the job
        is still finishing (see the HdfTaskSignals docstring for why that
        reference matters), the signals object is destroyed with it, and
        the emit raises "Signal source has been deleted" - printed by
        PySide as an unhandled error from QRunnable::run, seen at the end
        of the offscreen suite's phase 1 (9.7.2). There is nobody left to
        tell, so the RuntimeError is swallowed here; a live owner is
        notified exactly as before.
        """
        try:
            getattr(self.signals, name).emit(*args)
        except RuntimeError:
            pass


class HdfProgressDialog(QDialog):
    """Modal progress dialog with live status, progress bar, spinner, and Cancel button."""

    cancel_requested = Signal()

    def __init__(self, title, parent=None, cancel_label="Cancel"):
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
        _ps = _font.pointSize()
        if _ps <= 0:
            _ps = max(QFontInfo(_font).pointSize(), 9)
        _font.setPointSize(max(_ps - 1, 8))
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
        self._cancel_btn = QPushButton(cancel_label)
        # Minimum, not fixed: the Remote Explorer's background-copy dialog uses
        # a long label ("Close this window and continue in the background").
        self._cancel_btn.setMinimumWidth(90)
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
        # Never repaint once the dialog is hidden: a tick after accept()/hide()
        # would schedule an update on a top-level window whose native handle is
        # already gone, which Qt flushes with the fatal "QBackingStore::flush()
        # called for QWidgetWindow ... which does not have a handle" and crashes.
        if not self.isVisible():
            return
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

    @Slot(str)
    def set_cancel_note(self, text: str):
        """Post-cancel status line ('Explanation\nDetail'). set_status is
        deliberately muted once Cancel was pressed, so callers whose cancel
        is graceful use this to say WHAT 'Cancelling…' is waiting for —
        e.g. the Remote Explorer finishing the in-flight file so nothing
        lands half-written on the Next."""
        lines = text.split("\n", 1)
        self._action_label.setText(lines[0])
        self._file_label.setText(lines[1] if len(lines) > 1 else "")

    def done(self, result):
        # done() is the single funnel for accept()/reject()/close(), whereas
        # closeEvent() fires only on close() (not on accept()/reject()). This
        # dialog is normally dismissed with accept(), so stopping the spinner
        # timer here — not just in closeEvent — guarantees it can never tick
        # after the window is hidden and loses its native handle.
        self._anim_timer.stop()
        super().done(result)

    def closeEvent(self, event):
        self._anim_timer.stop()
        super().closeEvent(event)


# ----------------------------------------------------------------------
#  Local zip helpers — shared by the Remote Explorer (local pane AND the
#  Next-side Remote Zip/Unzip staging) and the SD Card tab (local explorer
#  and the image explorer's Remote Zip/Unzip). Both run ON THE UI THREAD
#  over local files only: they show a cancellable HdfProgressDialog naming
#  every file, pumping the event loop per entry (local zip work is fast;
#  the surrounding transfers have their own progress machinery).
# ----------------------------------------------------------------------

def _zip_dialog(parent, title):
    from PySide6.QtWidgets import QApplication
    dlg = HdfProgressDialog(title, parent)
    state = {"cancel": False}
    dlg.cancel_requested.connect(lambda: state.__setitem__("cancel", True))
    dlg.set_progress(0)
    dlg.show()
    QApplication.processEvents()
    return dlg, state


def zip_extract_with_dialog(parent, zip_path, dest_dir, log=None):
    """Extract *zip_path* into *dest_dir* with a cancellable progress dialog
    that names every file as it comes out. Zip-slip entries ('..' segments,
    absolute or drive-prefixed paths) are skipped and logged, not extracted.
    Returns {'ok', 'files', 'skipped', 'bytes', 'cancelled', 'error'};
    'ok' is True only when the archive extracted without cancel or error
    (skipped entries alone don't clear it), 'bytes' totals the uncompressed
    sizes. RuntimeError from zipfile = encrypted members."""
    import shutil
    import zipfile
    from PySide6.QtWidgets import QApplication
    log = log or (lambda s: None)
    res = {"ok": False, "files": 0, "skipped": 0, "bytes": 0,
           "cancelled": False, "error": None}
    dlg, state = _zip_dialog(parent, "Unzip: extracting…")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.infolist()
            for i, m in enumerate(members):
                if state["cancel"]:
                    res["cancelled"] = True
                    return res
                mname = m.filename.replace("\\", "/")
                parts = [p for p in mname.split("/") if p not in ("", ".")]
                if (not parts or any(p == ".." for p in parts)
                        or ":" in parts[0]):
                    res["skipped"] += 1
                    log(f"Unzip: skipped unsafe entry {m.filename!r}")
                    continue
                dlg.set_status(f"Extracting…\n{mname}")
                dlg.set_progress(int(100 * (i + 1) / max(len(members), 1)))
                QApplication.processEvents()
                target = os.path.join(dest_dir, *parts)
                if m.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                res["files"] += 1
                res["bytes"] += m.file_size
        res["ok"] = True
    except (zipfile.BadZipFile, RuntimeError, OSError) as ex:
        res["error"] = str(ex)
    finally:
        dlg.close()
    return res


def zip_create_with_dialog(parent, src_paths, zip_local, log=None):
    """Build *zip_local* (deflated) from the local files/folders *src_paths*,
    each archived under its base name (folders recursively, empty folders
    preserved), with a cancellable per-file progress dialog. Returns
    {'ok', 'files', 'cancelled', 'error'}; on cancel or error the
    half-written zip is removed."""
    import zipfile
    from PySide6.QtWidgets import QApplication
    log = log or (lambda s: None)
    res = {"ok": False, "files": 0, "cancelled": False, "error": None}
    # Flatten the work list first so the progress bar can be determinate.
    todo = []          # (full_local_path, arcname, is_dir_entry)
    for src in src_paths:
        base = os.path.basename(src.rstrip("/\\"))
        if not base:
            continue
        if os.path.isdir(src):
            for root, dirs, fnames in os.walk(src):
                dirs.sort()
                rel = os.path.relpath(root, src)
                arc_root = base if rel in (".", "") else \
                    base + "/" + rel.replace(os.sep, "/")
                if not dirs and not fnames:
                    todo.append((root, arc_root + "/", True))
                for fn in sorted(fnames):
                    todo.append((os.path.join(root, fn),
                                 arc_root + "/" + fn, False))
        elif os.path.isfile(src):
            todo.append((src, base, False))
    if not todo:
        res["error"] = "nothing to zip"
        return res
    dlg, state = _zip_dialog(parent, "Zip: compressing…")
    try:
        with zipfile.ZipFile(zip_local, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (full, arc, is_dir) in enumerate(todo):
                if state["cancel"]:
                    res["cancelled"] = True
                    break
                dlg.set_status(f"Compressing…\n{arc}")
                dlg.set_progress(int(100 * (i + 1) / len(todo)))
                QApplication.processEvents()
                if is_dir:
                    zf.writestr(zipfile.ZipInfo(arc), b"")   # empty folder
                else:
                    zf.write(full, arc)
                    res["files"] += 1
        res["ok"] = not res["cancelled"]
    except OSError as ex:
        res["error"] = str(ex)
    finally:
        dlg.close()
    if not res["ok"]:
        try:
            os.remove(zip_local)
        except OSError:
            pass
    return res


def zip_unique_name(first_name, taken_lower):
    """The zip name for a selection: the FIRST item's name + '.zip',
    uniquified Explorer-style against *taken_lower* (a lower-cased set of
    existing names): 'name.zip', 'name (2).zip', …"""
    name = first_name + ".zip"
    n = 1
    while name.lower() in taken_lower:
        n += 1
        name = f"{first_name} ({n}).zip"
    return name


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.


# ---------------------------------------------------------------------------
# SD-card image transfer worker bodies + the classic NextSync server
# (strangler extraction #3 from zx-next-unite.py). Pure worker-thread code:
# no widgets — results travel through HdfTaskSignals-style signals and the
# injected callables. *execute* is the host's execute_hdf_monkey runner;
# *check_full_disk* its access-denied-means-full-volume probe.
# ---------------------------------------------------------------------------

def _scan_image_tree_for_get(execute, image_path, image_source, disk_dest, cancel_event,
                             signals, out_files, out_dirs):
    """Recursively enumerate all files and dirs under image_source.
    Appends (img_src, disk_dst) tuples to out_files and out_dirs.
    Emits status with each discovered name so the user sees live names."""
    hdfr = execute("ls", image_path, extra_argv=[image_source])
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
            _scan_image_tree_for_get(execute, image_path, img_path, disk_path, cancel_event,
                                     signals, out_files, out_dirs)
        else:
            out_files.append((img_path, disk_path))

def _scan_image_tree_for_delete(execute, image_path, destination, cancel_event,
                                signals, out_files, out_dirs):
    """Recursively enumerate all files and dirs under destination.
    Appends item path strings to out_files (deepest first) and out_dirs."""
    hdfr = execute("ls", image_path, extra_argv=[destination])
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
            _scan_image_tree_for_delete(execute, image_path, full, cancel_event,
                                        signals, out_files, out_dirs)
            out_dirs.append(full)   # directory itself deleted after its contents
        else:
            out_files.append(full)

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

def is_directory(execute, image_path, source):

    root_folder , file_name_from_source = get_parent_root_directory_splited (source)

    hdfmonkeyexecresult = execute("ls", image_path, extra_argv=[root_folder])

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

def _run_delete_task(signals, cancel_event, execute, image_path, paths_to_delete):
    """Background worker body for image_delete_files.
    *paths_to_delete* is a list of full in-image paths.
    Phase 1: scan/count all items recursively (indeterminate progress).
    Phase 2: delete each item with real percentage progress."""
    actual = [p for p in paths_to_delete if p and p != UP_DIRECTORY]

    # ---- Phase 1: enumerate everything ----
    signals.progress.emit(-1)   # indeterminate
    all_files = []   # flat list of image paths to rm
    all_dirs  = []   # directories to rm after their content
    for full in actual:
        if cancel_event.is_set():
            break
        full = full.replace("//", "/")
        signals.status.emit(f"Scanning\u2026\n{full}")
        if is_directory(execute, image_path, full):
            _scan_image_tree_for_delete(execute, image_path, full, cancel_event,
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
            execute("rm", image_path, extra_argv=[item_path])
        except Exception as e:
            logging.error(f"Failed deleting: {item_path} - {e}")
            signals.error.emit(f"Failed deleting: {item_path}\n{e}")
        signals.progress.emit(int((idx + 1) / total * 100))

def _run_get_task(signals, cancel_event, execute, image_path, items,
                  dest_dir, dir_nav, is_windows):
    """Background worker body for transfert_content_from_image_to_disk.
    *items* is a list of (full_image_path, base_name) for the selected
    tree entries.
    Phase 1: scan/count all items recursively (indeterminate progress).
    Phase 2: copy each file with real percentage progress."""

    # ---- Phase 1: enumerate everything ----
    signals.progress.emit(-1)   # indeterminate marquee
    all_files = []   # list of (img_src_path, local_disk_path)
    all_dirs  = []   # list of (img_src_path, local_disk_path)  – dirs to create

    for source, base_name in items:
        if cancel_event.is_set():
            break
        source = source.replace("//", "/")
        signals.status.emit(f"Scanning\u2026\n{source}")
        if not is_directory(execute, image_path, source):
            local = dest_dir + dir_nav + base_name
            all_files.append((source, local))
        else:
            local_dir = os.path.join(dest_dir, base_name) if is_windows else dest_dir + "/" + base_name
            all_dirs.append((source, local_dir))
            _scan_image_tree_for_get(execute, image_path, source, local_dir, cancel_event,
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
            execute("get", image_path,
                               extra_argv=[img_src, local_dst.replace('\\', '/')])
        except Exception as e:
            logging.error(f"Failed downloading: {img_src} - {e}")
            signals.error.emit(f"Failed downloading: {img_src}\n{e}")
        signals.progress.emit(int((idx + 1) / total * 100))

def _run_put_task(signals, cancel_event, execute, check_full_disk,
                  image_path, upload_path, dest_file_path):
    """Background worker body for transfert_content_from_disk_to_image.
    For a single file: simple upload with status.
    For a directory: Phase 1 scans the local tree, Phase 2 uploads each file."""

    if not os.path.isdir(upload_path):
        # ---- Single file ----
        signals.status.emit(f"Uploading to image\n{os.path.basename(upload_path)}")
        signals.progress.emit(0)
        if not cancel_event.is_set():
            result = execute("put", image_path, extra_argv=[upload_path.replace('\\', '/'), dest_file_path])
            if result.returncode != 0:
                stdout_text = (result.stdout or b"").decode(errors="replace").strip()
                if "Access denied" in stdout_text:
                    full_err = check_full_disk(image_path)
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
            result = execute("mkdir", image_path, extra_argv=[seg], silent=True)
            mkdir_stdout = (result.stdout or b"").decode(errors="replace").strip()
            if result.returncode == 0:
                _img_dirs_created.add(seg)
            else:
                if "Access denied" in mkdir_stdout:
                    full_err = check_full_disk(image_path)
                    if full_err:
                        logging.error(full_err)
                        signals.error.emit(full_err)
                        cancel_event.set()
                        return False
                # Non-zero may mean already exists — verify with ls
                ls_result = execute("ls", image_path, extra_argv=[seg], silent=True)
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
        result = execute("put", image_path, extra_argv=[local_path.replace('\\', '/'), img_dst])
        if result.returncode != 0:
            stdout_text = (result.stdout or b"").decode(errors="replace").strip()
            if "Access denied" in stdout_text:
                full_err = check_full_disk(image_path)
                if full_err:
                    logging.error(full_err)
                    signals.error.emit(full_err)
                    cancel_event.set()
                    break
            logging.error(f"Failed uploading: {local_path} -> {img_dst} | stdout: {stdout_text}")
            signals.error.emit(f"Failed uploading: {os.path.basename(local_path)}")
        signals.progress.emit(int((idx + 1) / total * 100))

def _run_put_external_task(signals, cancel_event, execute, check_full_disk,
                           image_path, items):
    """Background worker body for drag-and-drop uploads. *items* is a list
    of (local_path, image_dest_path). Each entry is uploaded by reusing the
    single-path put logic (file or directory)."""
    for upload_path, dest_file_path in items:
        if cancel_event.is_set():
            break
        _run_put_task(signals, cancel_event, execute, check_full_disk,
                      image_path, upload_path, dest_file_path)



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

def getFileList(path_to_content, always_sync=False):
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
            if not always_sync:
                if g in knownfiles:
                    if agecheck(path_to_content, g):
                        ignored = True
            if not ignored:
                stats = os.stat(g)
                r.append([g, stats.st_size])
    return r

def timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sendpacket(conn, payload, packetno, log=None):
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

    if log is not None:
        log(str(timestamp()) + " | Packet sent: " + str(len(packet)) + " bytes, payload: " + str(len(payload)) + " bytes, checksums: " + str(checksum0) + ", " + str(checksum1) + ", packetno: " + str(packetno & 0xff) )

# ---- Sync4 upload (Next -> PC) helpers ------------------------------
# The Next frames each uploaded block exactly like sendpacket():
#   [2 bytes big-endian total][payload][checksum0][checksum1][packetno]
# where total = len(payload) + 5. recv_block() reverses that and
# verifies the checksums.

def recv_exact(conn, n):
    """Read exactly n bytes from conn, or None on disconnect."""
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def recv_block(conn):
    """Read one framed upload block.

    Returns (payload_bytes, packetno) on success, the string 'BADCS'
    when the frame's checksum is wrong (caller should ask for a resend),
    or None on disconnect / malformed length.
    """
    hdr = recv_exact(conn, 2)
    if hdr is None:
        return None
    total = (hdr[0] << 8) | hdr[1]
    if total < 5 or total > 4096:
        return None
    rest = recv_exact(conn, total - 2)
    if rest is None:
        return None
    payload = rest[:-3]
    cs0, cs1, pktno = rest[-3], rest[-2], rest[-1]
    c0 = 0
    c1 = 0
    for x in payload:
        c0 = (c0 ^ x) & 0xff
        c1 = (c1 + c0) & 0xff
    if c0 != cs0 or c1 != cs1:
        return 'BADCS'
    return (payload, pktno)

def sanitize_incoming_path(root, name):
    """Map a filename reported by the Next to a safe path under root.

    Strips any drive letter and leading slashes, drops '.'/'..'
    segments, and guarantees the result stays inside root.
    """
    name = name.replace('\\', '/')
    if len(name) >= 2 and name[1] == ':':
        name = name[2:]
    name = name.lstrip('/')
    parts = [p for p in name.split('/') if p not in ('', '.', '..')]
    rel = os.path.join(*parts) if parts else 'received.bin'
    dest = os.path.normpath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if not (os.path.abspath(dest) == root_abs or
            os.path.abspath(dest).startswith(root_abs + os.sep)):
        dest = os.path.join(root, os.path.basename(rel) or 'received.bin')
    return dest



def run_classic_sync_server(sync_root, log, *, progress_callback=None,
                            status_callback=None, cancel_flag=None,
                            force_sync_once=False, sync_once=None,
                            always_sync=None, get_conflict_policy=None,
                            ask_conflict=None, max_payload=None, port=None,
                            verbose=False, set_session_active=None,
                            pane_progress=None):
    """The classic (Sync3/Sync4) NextSync server loop, extracted verbatim from
    MainWindow's nextsync_do_server_job so it sits next to its listen-mode
    sibling run_remote_listen_server and is testable over localhost
    (tests/test_classic_sync.py).

    Everything UI-ish is injected: *log* receives each console line;
    *sync_once* / *always_sync* / *get_conflict_policy* are 0-arg callables
    read per use (they map to Settings the user can flip mid-session);
    *ask_conflict(name, path)* blocks the worker for the overwrite prompt;
    *set_session_active(bool)* drives the sidebar animation flag and
    *pane_progress(pct)* the NextSync pane's own progress bar (None when the
    caller is not the pane). progress_callback / status_callback are Qt
    signals (or None) exactly as before; *cancel_flag* is a threading.Event.
    The caller computes *sync_root* (trailing slash) and the pane-side UI
    state around the call. max_payload / port default to the zxnu_config
    values."""
    max_payload = max_payload or MAX_PAYLOAD
    port = port or PORT
    _vlog = log if verbose else None
    _ask = ask_conflict or (lambda _name, _path: "ignore")
    _sync_once = sync_once or (lambda: False)
    _always = always_sync or (lambda: False)
    _get_conflict_policy = get_conflict_policy or (lambda: "prompt")
    _set_active = set_session_active or (lambda _on: None)
    selected_nextsync_explorer_sync_root_directory = sync_root

    try:
        working = True
        while working:
            if cancel_flag is not None and cancel_flag.is_set():
                working = False
                break
            log(f"{timestamp()} | " + ui_tr_now(
                "NextSync listening to port {port}").format(port=port))
            log(f"{timestamp()} | " + ui_tr_now(
                "Now run one of these commands on your Next:"))
            # The two command lines are literals the Next must receive exactly:
            # only the direction labels around them could be translated, and
            # splitting them would break the aligned columns. Left verbatim.
            log(f"{timestamp()} |   PC  -> Next : .sync5   (or .sync5 -fast)")
            log(f"{timestamp()} |   Next -> PC  : .sync5 -send <file or directory>")
            if selected_nextsync_explorer_sync_root_directory:
                log(f"{timestamp()} |   " + ui_tr_now(
                    "(-send saves received files under: {folder})").format(
                        folder=selected_nextsync_explorer_sync_root_directory))
            totalbytes = 0
            payloadbytes = 0
            starttime = 0
            retries = 0
            packets = 0
            restarts = 0
            gee = 0
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # bind() raises OSError (WinError 10048) if the port is already
                # taken - typically another running instance. _run turns that
                # into the "another instance?" yellow toast via
                # nextsync_server_exception_occured.
                s.bind(("", port))
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
                f = getFileList(selected_nextsync_explorer_sync_root_directory, _always())
                log(f"{timestamp()} | " + ui_tr_now(
                    "Sync file list has {count} files.").format(count=len(f)))
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
                    log(f"{timestamp()} | " + ui_tr_now(
                        "Connected by {address} port {port}").format(
                            address=addr[0], port=addr[1]))
                    # A client session is live: the sidebar's NextSync icon
                    # accelerates its packet animation while this is set
                    # (cleared when the session ends, and defensively in
                    # the server thread's finally).
                    _set_active(True)
                    talking = True
                    while talking:
                        data = conn.recv(1024)
                        if not data:
                            break
                        decoded = data.decode()
                        if verbose:
                            log(f'{timestamp()} | Data received: "{decoded}", {len(decoded)} bytes')
                        if data == b"Sync3":
                            log(f'{timestamp()} | Using protocol version: {VERSION3}')
                            packet = str.encode(VERSION3)
                            sendpacket(conn, packet, 0, log=_vlog)
                            packets += 1
                            totalbytes += len(packet)
                        elif data == b"Sync4":
                            # Bidirectional protocol negotiation (Sync4). Only
                            # then will the Next be allowed to push files to us.
                            log(f'{timestamp()} | Using protocol version: {VERSION4}')
                            packet = str.encode(VERSION4)
                            sendpacket(conn, packet, 0, log=_vlog)
                            packets += 1
                            totalbytes += len(packet)
                        elif data == b"Send":
                            # Sync4 upload mode: the Next pushes files to us.
                            # We frame inbound blocks ourselves here (the main
                            # recv(1024) loop can't frame length-prefixed data).
                            log(f"{timestamp()} | " + ui_tr_now("Receiving files from the Next..."))
                            sendpacket(conn, b"Send", 0)  # ack -> Next starts sending
                            packets += 1
                            upload_root = selected_nextsync_explorer_sync_root_directory or "./"
                            log(f"{timestamp()} | " + ui_tr_now(
                                "Saving incoming files under: {folder}").format(
                                    folder=upload_root))
                            # How to treat incoming files/dirs that already exist
                            # locally. Read from the (persisted) setting; an
                            # "always in this sync" prompt choice overrides it
                            # for the rest of this transfer.
                            conflict_policy = _get_conflict_policy()
                            log(
                                f"{timestamp()} | " + ui_tr_now(
                                    "Existing-file policy: {policy} (change in "
                                    "Settings -> 'NextSync - when a sent file "
                                    "or directory exists locally')."
                                ).format(policy=conflict_policy))
                            expected_pkt = 0
                            cur_file = None
                            cur_name = None
                            cur_path = None
                            cur_bytes = 0
                            cur_skip = False
                            files_received = 0
                            # Paths of files fully received this session — added to the
                            # syncpoint afterwards so the next PC->Next sync won't push
                            # them straight back to the Next.
                            received_paths = []
                            while True:
                                blk = recv_block(conn)
                                if blk is None:
                                    log(f"{timestamp()} | " + ui_tr_now("Upload connection closed"))
                                    break
                                if blk == 'BADCS':
                                    if verbose:
                                        log(f'{timestamp()} | Bad checksum, requesting resend')
                                    sendpacket(conn, b"Resend", expected_pkt, log=_vlog)
                                    retries += 1
                                    continue
                                payload, pktno = blk
                                # Duplicate of last block (our ack was lost): re-ack only.
                                if pktno == ((expected_pkt - 1) & 0xff):
                                    sendpacket(conn, b"Ok", pktno, log=_vlog)
                                    continue
                                if pktno != expected_pkt:
                                    log(f'{timestamp()} | Packet sequence error (got {pktno}, expected {expected_pkt})')
                                    sendpacket(conn, b"Err seq", pktno, log=_vlog)
                                    break
                                op = payload[0:1]
                                if op == b'N':
                                    # 'N' + [4B filelen][1B namelen][name]
                                    namelen = payload[5] if len(payload) > 5 else 0
                                    cur_name = payload[6:6 + namelen].decode(errors='replace')
                                    cur_path = sanitize_incoming_path(upload_root, cur_name)
                                    # Close any still-open previous file first.
                                    if cur_file is not None:
                                        cur_file.close()
                                        cur_file = None
                                    cur_bytes = 0
                                    cur_skip = False
                                    # Conflict handling when the target already exists.
                                    if os.path.exists(cur_path):
                                        decision = conflict_policy
                                        if decision == 'prompt':
                                            choice = _ask(cur_name, cur_path)
                                            if choice == 'overwrite_all':
                                                conflict_policy = 'overwrite'   # apply to rest of this sync
                                                decision = 'overwrite'
                                            elif choice == 'ignore_all':
                                                conflict_policy = 'ignore'      # apply to rest of this sync
                                                decision = 'ignore'
                                            elif choice == 'overwrite':
                                                decision = 'overwrite'
                                            else:
                                                decision = 'ignore'
                                        if decision == 'ignore':
                                            cur_skip = True
                                    if cur_skip:
                                        # Don't create/truncate the local file: the incoming
                                        # data blocks are still acked but discarded (cur_file
                                        # is None), and this file is not counted/recorded.
                                        log(f"{timestamp()} | " + ui_tr_now(
                                            "Skipped (already exists): {path}"
                                        ).format(path=cur_path))
                                        if status_callback is not None:
                                            status_callback.emit(f"Skipping (exists)\n{cur_name}")
                                        sendpacket(conn, b"Ok", pktno, log=_vlog)
                                    else:
                                        try:
                                            parent = os.path.dirname(cur_path)
                                            if parent:
                                                os.makedirs(parent, exist_ok=True)
                                        except OSError:
                                            pass
                                        try:
                                            cur_file = open(cur_path, 'wb')
                                        except OSError as ex:
                                            log(f"{timestamp()} | " + ui_tr_now(
                                                "Cannot create {path}: {error}"
                                            ).format(path=cur_path, error=ex))
                                            cur_file = None
                                            sendpacket(conn, b"Err open", pktno, log=_vlog)
                                            break
                                        log(f"{timestamp()} | " + ui_tr_now(
                                            "Receiving: {name} -> {path}"
                                        ).format(name=cur_name, path=cur_path))
                                        if status_callback is not None:
                                            status_callback.emit(f"Receiving file\n{cur_name}")
                                        sendpacket(conn, b"Ok", pktno, log=_vlog)
                                elif op == b'D':
                                    if cur_file is not None:
                                        cur_file.write(payload[1:])
                                        cur_bytes += len(payload) - 1
                                    payloadbytes += len(payload) - 1
                                    totalbytes += len(payload)
                                    sendpacket(conn, b"Ok", pktno, log=_vlog)
                                elif op == b'E':
                                    if cur_file is not None:
                                        cur_file.close()
                                        cur_file = None
                                        files_received += 1
                                        if cur_path and cur_path not in received_paths:
                                            received_paths.append(cur_path)
                                        log(f"{timestamp()} | " + ui_tr_now(
                                            "Received {name} ({bytes} bytes)"
                                        ).format(name=cur_name, bytes=cur_bytes))
                                    sendpacket(conn, b"Ok", pktno, log=_vlog)
                                elif op == b'B':
                                    # Ack the bye with "Ok" (not "Later"): the Next's
                                    # generic send_block() only treats a reply as success
                                    # when it starts with 'O'. Replying "Later" here makes
                                    # the Next consider the bye failed and retry it ~12×;
                                    # since we close the connection right after, each retry
                                    # hits its full timeout — the long stall before the dot
                                    # prints "All done". "Ok" lets it finish on the first try.
                                    sendpacket(conn, b"Ok", pktno, log=_vlog)
                                    log(f"{timestamp()} | " + ui_tr_now(
                                        "Upload finished, {count} file(s) received"
                                    ).format(count=files_received))
                                    # If that single ack is lost/corrupted in transit (more
                                    # likely after a long directory send), the Next retransmits
                                    # the bye and would otherwise burn its full UART timeout
                                    # against a closed socket — the intermittent stall before
                                    # "All done". Linger briefly: re-ack any retransmitted bye
                                    # and stop as soon as the Next hangs up (clean case) or the
                                    # short grace period elapses.
                                    try:
                                        conn.settimeout(2.0)
                                        while True:
                                            extra = recv_block(conn)
                                            if extra is None:
                                                break  # Next closed its side — done
                                            if extra == 'BADCS':
                                                continue
                                            xpayload, xpktno = extra
                                            if xpayload[0:1] == b'B':
                                                sendpacket(conn, b"Ok", xpktno, log=_vlog)
                                            else:
                                                break
                                    except (socket.timeout, OSError):
                                        pass
                                    finally:
                                        try:
                                            conn.settimeout(None)
                                        except OSError:
                                            pass
                                    break
                                else:
                                    sendpacket(conn, b"Err op", pktno, log=_vlog)
                                    break
                                expected_pkt = (expected_pkt + 1) & 0xff
                            if cur_file is not None:
                                cur_file.close()
                                cur_file = None
                            # Record received files in the syncpoint so the next
                            # PC->Next sync treats them as already known and skips
                            # them (matching the glob path form getFileList uses).
                            if received_paths:
                                sp_known = []
                                if os.path.isfile(upload_root + SYNCPOINT):
                                    with open(upload_root + SYNCPOINT) as spf:
                                        sp_known = spf.read().splitlines()
                                for rp in received_paths:
                                    if rp not in sp_known:
                                        sp_known.append(rp)
                                update_syncpoint(upload_root, sp_known)
                                log(f"{timestamp()} | " + ui_tr_now(
                                    "Sync point updated with {count} received file(s)"
                                ).format(count=len(received_paths)))
                            talking = False
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
                                    log(f"{timestamp()} | " + ui_tr_now(
                                        "Cancel requested — stopping after current file"))
                                    if status_callback is not None:
                                        status_callback.emit("Cancelled — finishing current file…")
                                else:
                                    log(f"{timestamp()} | " + ui_tr_now("Nothing (more) to sync"))
                                packet = b'\x00\x00\x00\x00\x00' # end of.
                                packets += 1
                                sendpacket(conn, packet, 0, log=_vlog)
                                totalbytes += len(packet)
                                # Persist sync point even on cancel so already-sent
                                # files aren't re-sent next time.
                                update_syncpoint(selected_nextsync_explorer_sync_root_directory, knownfiles)
                            else:
                                specfn = f[fn][0].replace('\\','/')
                                log(f"{timestamp()} | File: {f[fn][0]} (as {specfn}) length: {f[fn][1]} bytes")
                                packet = (f[fn][1]).to_bytes(4, byteorder="big") + (len(specfn)).to_bytes(1, byteorder="big") + (specfn).encode()
                                packets += 1
                                sendpacket(conn, packet, 0, log=_vlog)
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
                                # also update the pane progress bar when running from the pane
                                if pane_progress is not None:
                                    pane_progress(pct)
                                fn += 1
                        elif data == b"Get" or data == b"Gee": # Really common mistransmit. Probably uart-esp..
                            bytecount = max_payload
                            if bytecount + fileofs > len(filedata):
                                bytecount = len(filedata) - fileofs
                            packet = filedata[fileofs:fileofs+bytecount]
                            if verbose:
                                if filedata:
                                    log(f"{timestamp()} | Sending {bytecount} bytes, offset {fileofs}/{len(filedata)}")
                                else:
                                    log(f"{timestamp()} | Sending {bytecount} bytes 0 bytes")

                            packets += 1
                            sendpacket(conn, packet, packetno, log=_vlog)
                            totalbytes += len(packet)
                            fileofs += bytecount
                            packetno += 1
                            if data == b"Gee":
                                gee += 1
                        elif data == b"Retry":
                            retries += 1
                            log(f"{timestamp()} | Resending")
                            sendpacket(conn, packet, packetno - 1, log=_vlog)
                        elif data == b"Restart":
                            restarts += 1
                            log(f"{timestamp()} | Restarting")
                            fileofs = 0
                            packetno = 0
                            sendpacket(conn, str.encode("Back"), 0, log=_vlog)
                        elif data == b"Bye":
                            sendpacket(conn, str.encode("Later"), 0, log=_vlog)
                            log(f"{timestamp()} | " + ui_tr_now("Closing connection"))
                            # Drain until the Next closes its side before we
                            # do: the hard (SO_LINGER-0) close below sends an
                            # RST that can otherwise clobber the just-queued
                            # "Later" (on Windows a reset also flushes data
                            # already in the peer's receive buffer), making
                            # the dot retry its bye against a dead socket —
                            # and tests/test_classic_sync.py flake with
                            # WinError 10054. The peer's EOF proves "Later"
                            # was consumed; the grace period bounds a client
                            # that never hangs up. Mirrors the post-B linger
                            # in the Send path above.
                            try:
                                conn.settimeout(2.0)
                                while conn.recv(1024):
                                    pass
                            except (socket.timeout, OSError):
                                pass
                            finally:
                                try:
                                    conn.settimeout(None)
                                except OSError:
                                    pass
                            talking = False
                        elif data == b"Sync2" or data == b"Sync1" or data == b"Sync":
                            packet = str.encode("Nextsync 0.8 or later needed")
                            log(f'{timestamp()} | Old protocol version requested')
                            sendpacket(conn, packet, 0, log=_vlog)
                            packets += 1
                            totalbytes += len(packet)
                        else:
                            log(f"{timestamp()} | Unknown command")
                            sendpacket(conn, str.encode("Error"), 0, log=_vlog)
                    endtime = time.time()
            _set_active(False)
            deltatime = endtime - starttime
            log(f"{timestamp()} | " + ui_tr_now(
                "{kb} kilobytes transferred in {seconds} seconds, {rate} kBps"
            ).format(kb=f"{totalbytes/1024:.2f}", seconds=f"{deltatime:.2f}",
                     rate=f"{(totalbytes/deltatime)/1024:.2f}"))
            log(f"{timestamp()} | " + ui_tr_now(
                "{kb} kilobytes payload, {rate} kBps effective speed"
            ).format(kb=f"{payloadbytes/1024:.2f}",
                     rate=f"{(payloadbytes/deltatime)/1024:.2f}"))
            log(f"{timestamp()} | packets: {packets}, retries: {retries}, restarts: {restarts}, gee: {gee}")

            log(f"{timestamp()} | " + ui_tr_now("Disconnected"))
            log("")
            if force_sync_once or _sync_once() or (cancel_flag is not None and cancel_flag.is_set()):
                working = False

    finally:
        # Defensive: never leave the sidebar animation flag stuck on.
        _set_active(False)


__all__ = [_n for _n in dir() if not _n.startswith('__')]
