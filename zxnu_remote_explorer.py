"""Remote file-explorer widget for the NextSync tab.

A dual-pane file manager modelled on the SD Card Utility tab, but the right
("Next") pane is driven by the NextSync ``.sync5 -listen`` protocol
(zxnu_workers.run_remote_listen_server) instead of hdfmonkey:

    [ local file explorer ] [ ->:  :<- ] [ Next file explorer ]

Commands are pushed onto a queue.Queue the listen worker drains; results arrive
through RemoteExplorerSignals and are applied here on the UI thread.
"""

import html
import os
import posixpath
import shutil
import tempfile

from PySide6.QtCore import (
    Qt, QDir, QEvent, QModelIndex, QMimeData, QUrl, QSize, QTimer,
)
from PySide6.QtGui import (
    QColor, QDrag, QKeySequence, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileSystemModel, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QStyle,
    QTreeView, QVBoxLayout, QWidget,
)

from zxnu_config import (
    DEFAULT_COLOR_UP_DIRECTORY, DEFAULT_COLOR_DIR_NAME, DEFAULT_COLOR_DIR_TYPE,
    DEFAULT_COLOR_FILE_NAME, DEFAULT_COLOR_FILE_EXT, DEFAULT_COLOR_FILE_SIZE,
    DEFAULT_COLOR_GENERAL_TEXT, hex_to_qcolor,
)
from zxnu_workers import (
    DotDotFirstProxyModel, HdfProgressDialog, zip_create_with_dialog,
    zip_extract_with_dialog, zip_unique_name,
)

# Roles carrying the remote entry's full posix path and its directory flag.
RE_PATH_ROLE = Qt.UserRole + 1
RE_ISDIR_ROLE = Qt.UserRole + 2

ARROW_BTN_W = 40

# Sort persistence. Each pane's sort is stored as "<key>:<asc|desc>" where key is
# name/size/type. The two panes place those columns differently, so the key<->
# visible-column mappings differ: the local (QFileSystemModel) columns are
# Name(0)/Size(1)/Type(2); the Next (QStandardItemModel) columns are
# Name(0)/Type(1)/Size(2).
RE_SORT_KEYS = ("name", "size", "type")
RE_LOCAL_SORT_COL = {"name": 0, "size": 1, "type": 2}
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
                 on_extra_drives_changed=None):
        super().__init__(parent)
        self._enqueue_raw = enqueue          # host closure: put one command
        self._drain_raw = drain              # host closure: empty the queue, -> count
        self._log = log or (lambda s: None)
        self._on_sync_root_changed = on_sync_root_changed or (lambda p: None)
        # Surface Next-side failures ('F' replies / abandoned transfers) to the
        # user: on_toast(title, message, variant) pops a host toast.
        self._on_toast = on_toast or (lambda title, msg, variant="red": None)
        # Persist/restore the Next-side folder across (re)connections: on connect
        # we jump back to the last folder browsed, and every listing reports the
        # new folder to the host so it can save it (see on_connected/on_listing).
        self._on_remote_cwd_changed = on_remote_cwd_changed or (lambda p: None)
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
        self._next_overlay = None     # the "copy in progress" overlay QLabel
        # Free-space precheck state for a Next->Next paste (rfsize each source
        # + a fresh free-space read of the destination drive BEFORE the rcpy
        # is allowed to start). None when no precheck is pending.
        self._precheck = None

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
        # Show Name / Type / Size (hide Date Modified) and, like the SD-card image
        # tree, order them Name, Type, Size — QFileSystemModel's native order is
        # Name(0), Size(1), Type(2), so swap the last two visually.
        self.local_view.hideColumn(3)
        self.local_view.header().swapSections(1, 2)
        self.local_view.setColumnWidth(0, 250)
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
        self.local_view.clicked.connect(self._local_clicked)
        self.local_view.doubleClicked.connect(self._local_double_clicked)
        self.local_view.dragEnterEvent = self._local_drag_enter
        self.local_view.dragMoveEvent = self._local_drag_enter
        self.local_view.dropEvent = self._local_drop
        self.local_view.keyPressEvent = self._local_key_press
        self.local_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_view.customContextMenuRequested.connect(self._local_context_menu)

        # Top bar: Up / Refresh + the name filter ("Search: … Filter by name…"),
        # mirroring the classic sync local explorer.
        local_up = QPushButton("Up", self)
        local_up.setMaximumWidth(48)
        local_up.clicked.connect(self._local_up)
        local_refresh = QPushButton("Refresh", self)
        local_refresh.setMaximumWidth(72)
        local_refresh.setToolTip("Re-read the current local folder from disk")
        local_refresh.clicked.connect(self._local_refresh)
        self.local_filter_label = QLabel("Search: ", self)
        self.local_filter_edit = QLineEdit(self)
        self.local_filter_edit.setPlaceholderText("Filter by name...")
        self.local_filter_edit.setClearButtonEnabled(True)
        self.local_filter_edit.textChanged.connect(self._local_filter_changed)
        local_bar = QHBoxLayout()
        local_bar.setContentsMargins(0, 0, 0, 0)
        local_bar.addWidget(local_up)
        local_bar.addWidget(local_refresh)
        local_bar.addWidget(self.local_filter_label)
        local_bar.addWidget(self.local_filter_edit, 1)

        # Under the tree: the sync-root path box (same idea as classic sync's
        # "Path…" field). Shows the chosen sync root; typing a folder path and
        # pressing Enter jumps there and selects it.
        self.local_path_edit = QLineEdit(self)
        self.local_path_edit.setPlaceholderText("Select a sync root folder above...")
        self.local_path_edit.setToolTip(
            "Sync root: the local folder the Remote Explorer works in. Click a "
            "folder above to choose it, or type a path here and press Enter.")
        self.local_path_edit.editingFinished.connect(self._on_path_edit)

        local_box = QVBoxLayout()
        local_box.setContentsMargins(0, 0, 0, 0)
        local_box.setSpacing(2)
        local_box.addLayout(local_bar)
        local_box.addWidget(self.local_view)
        local_box.addWidget(self.local_path_edit)
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
        self.next_view.dropEvent = self._next_drop
        self.next_view.startDrag = self._next_start_drag
        self.next_view.keyPressEvent = self._next_key_press

        self.next_path_label = QLabel("Next: (not connected)", self)
        next_up = QPushButton("Up", self)
        next_up.setMaximumWidth(48)
        next_up.clicked.connect(self._next_up)
        refresh = QPushButton("Refresh", self)
        refresh.setMaximumWidth(72)
        refresh.clicked.connect(self.refresh)
        # Drive switcher: populated from the dot's getdrives reply on connect
        # (e.g. C and M). Disabled until the drives are known; stays a single
        # "C" with an explanatory tooltip when the dot predates getdrives.
        self.next_drive_combo = QComboBox(self)
        self.next_drive_combo.setToolTip(
            "Next drive to browse (from '.sync5 -listen'). Switching drives "
            "jumps to that drive's root; all transfers and file operations "
            "then target it. The Next reports C, M and its current drive; "
            "use + to add drives from extra SD readers/partitions.")
        self.next_drive_combo.setEnabled(False)
        self.next_drive_combo.currentTextChanged.connect(self._on_drive_changed)
        # "+": declare an extra drive letter the dot cannot discover on its
        # own (additional SD card readers / partitions). Only ever probed by
        # the USER switching to it - see _add_drive_clicked.
        self.next_drive_add = QPushButton("+ Drive", self)
        self.next_drive_add.setMaximumWidth(64)
        self.next_drive_add.setToolTip(
            "Add a Next drive letter (D..P) for an additional SD card "
            "reader/partition the Next cannot report by itself. The drive is "
            "remembered and offered automatically next time.")
        self.next_drive_add.setEnabled(False)
        self.next_drive_add.clicked.connect(self._add_drive_clicked)
        next_bar = QHBoxLayout()
        next_bar.setContentsMargins(0, 0, 0, 0)
        next_bar.addWidget(next_up)
        next_bar.addWidget(refresh)
        next_bar.addWidget(self.next_drive_combo)
        next_bar.addWidget(self.next_drive_add)
        next_bar.addWidget(self.next_path_label, 1)

        next_box = QVBoxLayout()
        next_box.setContentsMargins(0, 0, 0, 0)
        next_box.setSpacing(2)
        next_box.addLayout(next_bar)
        next_box.addWidget(self.next_view)

        # Next-side toolbar: New Folder / Rename / Delete
        self.btn_new_folder = QPushButton("New Folder", self)
        self.btn_new_folder.clicked.connect(self._new_folder)
        self.btn_rename = QPushButton("Rename", self)
        self.btn_rename.clicked.connect(self._rename_selected)
        self.btn_delete = QPushButton("Delete", self)
        self.btn_delete.clicked.connect(self._delete_selected)
        next_tools = QHBoxLayout()
        next_tools.setContentsMargins(0, 0, 0, 0)
        next_tools.addWidget(self.btn_new_folder)
        next_tools.addWidget(self.btn_rename)
        next_tools.addWidget(self.btn_delete)
        next_tools.addStretch(1)
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
                bytes_total=0, files_total=0):
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
        self._op_bytes_total = int(bytes_total or 0)
        self._op_files_total = int(files_total or 0)
        self._op_bytes_est = 0
        self._op_files_seen = 0
        self._op_last_name = ""
        self.setEnabled(False)           # make the whole explorer unclickable
        # Delay the dialog so instant operations (a quick mkdir/rename) don't
        # flash a modal box on screen.
        QTimer.singleShot(250, self._show_op_dialog_if_running)
        enqueue_fn()
        if self._op_total == 0:          # nothing actually queued
            self._end_operation()

    def _show_op_dialog_if_running(self):
        if not self._op_active or self._op_dialog is not None or self._op_background:
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
        # Keep the background-copy overlay covering the Next pane through
        # resizes / splitter drags.
        if (obj is getattr(self, "next_container", None)
                and self._next_overlay is not None
                and event.type() == QEvent.Type.Resize):
            self._next_overlay.setGeometry(self.next_container.rect())
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
                               f"Copied {n} item(s) on the Next.", "green")
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
            body += f"\n…and {len(fails) - len(shown)} more"
        title = ("A Next operation failed" if len(fails) == 1
                 else f"{len(fails)} Next operations failed")
        self._on_toast(title, body, "red")

    # ==================================================================
    #  connection state
    # ==================================================================
    def _set_connected(self, on):
        self._connected = on
        for w in (self.btn_to_next, self.btn_to_local, self.btn_new_folder,
                  self.btn_rename, self.btn_delete, self.next_view):
            w.setEnabled(on)
        if not on:
            self.next_model.removeRows(0, self.next_model.rowCount())
            self.next_path_label.setText("Next: (waiting for .sync5 -listen …)")
            self.next_path_label.setToolTip("")
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

    # ---- worker signal slots (UI thread) ------------------------------
    def on_connected(self):
        self._set_connected(True)
        # Jump straight back to the folder we were last browsing. If it's gone,
        # the listing fails and on_ls_failed() drops us back to the root.
        self._cwd = self._remote_start_dir or "/"
        # Ask which drives are mounted (dot v5.1+) before the first listing so
        # the drive switcher fills in as the pane appears.
        self._enqueue(("drives",))
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
        """Path label = cwd + the cached free space of its drive (if known).
        The free-space figure is bold and traffic-light coloured (see
        _free_color) so a filling-up card is visible at a glance."""
        free = self._free_space.get(self._cwd_drive())
        if free is not None:
            # Rich text so only the free-space part is coloured; the label
            # auto-detects HTML. &nbsp; keeps the double-space look that the
            # plain-text form used (rich text collapses runs of spaces).
            self.next_path_label.setText(
                f"Next: {html.escape(self._cwd)}&nbsp;&nbsp;—&nbsp;&nbsp;"
                f"<b><span style=\"color: {self._free_color(free)};\">"
                f"{html.escape(self._fmt_free(free))} free</span></b>")
            self.next_path_label.setToolTip(
                f"Free space on drive {self._cwd_drive()}: {free} bytes "
                "(reported by the Next via F_GETFREE; NextZXOS exposes no "
                "safe way for a dot command to read the total partition "
                "size).\nGreen: more than 200 MB free · yellow: 100–200 MB "
                "· red: below 100 MB.\nRe-read after every transfer, "
                "delete, rename or copy, so the figure tracks the card.")
        else:
            self.next_path_label.setText(f"Next: {self._cwd}")
            self.next_path_label.setToolTip("")

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
            self, "Add Next drive",
            "Drive letter of the additional SD reader/partition (D..P):")
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
                self, "Add Next drive",
                f"Add drive {letter}: to the list?\n\n"
                "Only add a drive that really exists on your Next (an extra "
                "SD card reader or partition). Selecting a drive that is not "
                "mounted CRASHES the Next.",
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
                       f"{path} no longer exists on the Next.\nReturned to {root}.",
                       "yellow")
        self._cwd = root
        self.refresh()

    def _remember_remote_cwd(self, path):
        """Record the current Next folder for restore-on-reconnect, notifying the
        host (which persists it) only when it actually changes."""
        norm = _norm_remote_dir(path)
        if norm != self._remote_start_dir:
            self._remote_start_dir = norm
            self._on_remote_cwd_changed(norm)

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
        act_new = menu.addAction("New Folder…")
        act_get = menu.addAction("Download (:<-)")
        act_size = menu.addAction("Get size")
        act_unzip = menu.addAction("Remote Unzip file")
        act_rzip = menu.addAction("Remote Zip")
        act_copy = menu.addAction("Copy")
        act_cut = menu.addAction("Cut")
        act_paste = menu.addAction("Paste")
        act_ren = menu.addAction("Rename…")
        act_del = menu.addAction("Delete")
        act_ref = menu.addAction("Refresh")
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
        if not self._connected:
            return
        name, ok = QInputDialog.getText(self, "New Folder", f"New folder in {self._cwd}:")
        if ok and name.strip():
            target = _posix_join(self._cwd, name.strip())
            self._run_op("Creating folder on the Next…",
                         lambda: self._enqueue(("mkdir", target)),
                         toast_mkdir_fail=True)

    def _rename_selected(self):
        if not self._connected:
            return
        entries = self._selected_next_entries()
        if len(entries) != 1:
            self._log("Select exactly one Next item to rename.")
            return
        path, _is_dir = entries[0]
        old_name = posixpath.basename(path.rstrip("/")) or path
        new_name, ok = QInputDialog.getText(
            self, "Rename", f"Rename '{old_name}' to:", text=old_name)
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
        if not self._connected:
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
            self, "Size on the Next",
            f"{path}\n\n"
            f"Files:  {int(data.get('files', 0)):,}\n"
            f"Folders:  {int(data.get('dirs', 0)):,}\n"
            f"Total size:  {n:,} bytes  ({self._fmt_free(n)})")

    def _delete_selected(self):
        entries = self._selected_next_entries()
        if not entries:
            return
        names = "\n".join(p for p, _ in entries)
        if QMessageBox.question(
                self, "Delete",
                "Delete on the Next? Folders are deleted with everything "
                f"inside them.\n\n{names}") != QMessageBox.Yes:
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
                self, "Not enough space on the Next",
                f"This copy needs {total:,} bytes ({self._fmt_free(total)}), "
                f"but drive {drive}: only has {free:,} bytes "
                f"({self._fmt_free(free)}) free.\n\n"
                f"It exceeds the available remote space by {over:,} bytes "
                f"({self._fmt_free(over)}).\n\nThe copy was not started.",
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
        if not entries:
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
    def _remote_unzip(self, zip_path):
        """Context menu 'Remote Unzip file' (a single selected remote .zip):
        stage 1 downloads the zip to a temp dir (normal cancellable op),
        stage 2 extracts it locally (own progress dialog + Cancel), stage 3
        uploads the extracted tree back into the zip's folder. Every abort
        path removes the temp dir and leaves the Next untouched."""
        if not self._connected or self._op_active:
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
                               f"Could not extract {name}: {res['error']}",
                               "red")
            else:
                self._on_toast("Remote unzip", f"{name} contains no "
                               "extractable files.", "yellow")
            return
        # Will-it-fit guard against the freshest cached free-space figure
        # (re-read at the end of the download op just before this).
        free = self._free_space.get(self._cwd_drive())
        if free is not None and total_bytes > free:
            shutil.rmtree(tmp, ignore_errors=True)
            self._on_toast(
                "Remote unzip refused",
                f"Unzipping needs {self._fmt_free(total_bytes)}, but drive "
                f"{self._cwd_drive()}: only has {self._fmt_free(free)} free.",
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
                extra = (f" ({skipped} unsafe "
                         f"{'entry' if skipped == 1 else 'entries'} skipped)"
                         if skipped else "")
                self._on_toast("✅  Remote unzip complete",
                               f"Extracted {files} file(s) from {name} "
                               f"into {self._cwd}.{extra}", "green")

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
        if not self._connected or self._op_active or not entries:
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
                               f"Could not build {zip_name}: {res['error']}",
                               "red")
            return
        files = res["files"]
        size = os.path.getsize(zip_local)
        free = self._free_space.get(self._cwd_drive())
        if free is not None and size > free:
            shutil.rmtree(tmp, ignore_errors=True)
            self._on_toast(
                "Remote zip refused",
                f"{zip_name} is {self._fmt_free(size)}, but drive "
                f"{self._cwd_drive()}: only has {self._fmt_free(free)} free.",
                "red")
            return
        base = self._cwd_base()

        def done(ok2, _fails2):
            shutil.rmtree(tmp, ignore_errors=True)
            if ok2:
                self._on_toast("✅  Remote zip complete",
                               f"Created {zip_name} in {self._cwd} "
                               f"({files} file(s), {self._fmt_free(size)}).",
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

    def _set_local_dir(self, path, commit=True):
        """Point the browse root at `path`. When `commit`, also make it the sync
        root (navigating into a folder means you want to work there)."""
        path = path.replace("\\", "/") if path else path
        self.local_view.setRootIndex(self._view_ix(path))
        self._browse_root = path
        if commit:
            self._commit_sync_root(path)

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
            self._set_local_dir(parent, commit=True)

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

    def _local_clicked(self, index):
        """Single-click picks a sync root, like the classic sync explorer: a
        folder selects itself, a file selects its parent folder. Changing the
        tree root (browsing) still happens on double-click / Up."""
        if self._is_local_updir(index):
            return                       # ".." only navigates on double-click / Up
        path = self._path_of(index)
        if not path:
            return
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if folder and os.path.isdir(folder):
            self._commit_sync_root(folder)

    def _local_double_clicked(self, index):
        if self._is_local_updir(index):
            self._local_up()             # ".." goes one level up
            return
        path = self._path_of(index)
        if os.path.isdir(path):
            self._set_local_dir(path, commit=True)

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
        act_new = menu.addAction("New Folder…")
        act_unzip = menu.addAction("Unzip file")
        act_zip = menu.addAction("Zip")
        menu.addSeparator()
        act_copy = menu.addAction("Copy")
        act_cut = menu.addAction("Cut")
        act_paste = menu.addAction("Paste")
        menu.addSeparator()
        act_ren = menu.addAction("Rename…")
        act_del = menu.addAction("Delete")
        menu.addSeparator()
        act_ref = menu.addAction("Refresh")
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
        if chosen == act_new:
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
                           f"Could not extract {name}: {res['error']}", "red")
        else:
            skipped = res["skipped"]
            extra = (f" ({skipped} unsafe "
                     f"{'entry' if skipped == 1 else 'entries'} skipped)"
                     if skipped else "")
            self._on_toast("✅  Unzip complete",
                           f"Extracted {res['files']} file(s) from {name} "
                           f"into {dest}.{extra}", "green")
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
                           f"Could not create {zip_name}: {res['error']}",
                           "red")
        else:
            self._on_toast("✅  Zip complete",
                           f"Created {zip_name} in {dest} "
                           f"({res['files']} file(s)).", "green")
        self._local_refresh()

    def _local_new_folder(self):
        base = self._browse_dir()
        if not base or not os.path.isdir(base):
            return
        name, ok = QInputDialog.getText(self, "New Folder", f"New folder in {base}:")
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
        if QMessageBox.question(self, "Delete",
                                f"Delete from the local disk?\n\n{names}") != QMessageBox.Yes:
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
            self, "Rename", f"Rename '{old_name}' to:", text=old_name)
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
        if self._connected and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _next_drop(self, event):
        # Accept both files and folders dragged from the local pane or the OS
        # file manager; folders are uploaded recursively (see _put_paths).
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile() and os.path.exists(u.toLocalFile())]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self._run_op("Uploading to the Next…", lambda: self._put_paths(paths))

    def _next_start_drag(self, supported):
        # Drag Next entries to the local pane / OS to download them.
        entries = self._selected_next_entries()
        if not entries:
            return
        # Represented as text; the local pane treats a drop as "download here".
        mime = QMimeData()
        mime.setData("application/x-zxnu-next-entries",
                     "\n".join(f"{'D' if d else 'F'}\t{p}" for p, d in entries).encode())
        drag = QDrag(self.next_view)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def _local_drag_enter(self, event):
        # Next-pane entries (download here) or file URLs -- from the local pane
        # itself or the OS file manager (copy into the folder dropped on).
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

        def go():
            for path, is_dir in entries:
                self._prepare_local_download_dir(dest, path, is_dir)
                self._enqueue(("get", path, dest))
            self._log(f"Downloading {len(entries)} item(s) to {dest} …")
        self._run_op("Downloading from the Next…", go)
