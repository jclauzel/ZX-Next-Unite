"""Remote file-explorer widget for the NextSync tab.

A dual-pane file manager modelled on the SD Card Utility tab, but the right
("Next") pane is driven by the NextSync ``.sync5 -listen`` protocol
(zxnu_workers.run_remote_listen_server) instead of hdfmonkey:

    [ local file explorer ] [ ->:  :<- ] [ Next file explorer ]

Commands are pushed onto a queue.Queue the listen worker drains; results arrive
through RemoteExplorerSignals and are applied here on the UI thread.
"""

import os
import posixpath
import shutil

from PySide6.QtCore import Qt, QDir, QModelIndex, QMimeData, QUrl, QSize, QTimer
from PySide6.QtGui import (
    QColor, QDrag, QKeySequence, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QFileSystemModel, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QStyle, QTreeView,
    QVBoxLayout, QWidget,
)

from zxnu_config import (
    DEFAULT_COLOR_UP_DIRECTORY, DEFAULT_COLOR_DIR_NAME, DEFAULT_COLOR_DIR_TYPE,
    DEFAULT_COLOR_FILE_NAME, DEFAULT_COLOR_FILE_EXT, DEFAULT_COLOR_FILE_SIZE,
    DEFAULT_COLOR_GENERAL_TEXT, hex_to_qcolor,
)
from zxnu_workers import DotDotFirstProxyModel, HdfProgressDialog

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


def _norm_remote_dir(p):
    """Normalise a saved Next-side folder path to an absolute posix dir.

    Blank/invalid restores to "/". Used when restoring the last-browsed remote
    folder from the config file (see RemoteExplorerWidget.on_connected).
    """
    p = (p or "").replace("\\", "/").strip()
    if not p:
        return "/"
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
                 on_sort_changed=None, on_toast=None):
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
        next_bar = QHBoxLayout()
        next_bar.setContentsMargins(0, 0, 0, 0)
        next_bar.addWidget(next_up)
        next_bar.addWidget(refresh)
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
                on_done=None):
        """Run a batch of remote commands as a cancellable, blocking operation.

        ``enqueue_fn`` queues the commands (via _enqueue). The widget is
        disabled and, after a short delay, a modal progress dialog appears; both
        are lifted once every queued command has reported back. ``toast_mkdir_fail``
        opts a single deliberate mkdir (New Folder) into failure toasts.
        ``on_done`` (optional) is called on the UI thread when the operation
        ends, as ``on_done(ok, failures)`` — see _end_operation.
        """
        if self._op_active or not self._connected:
            # Never nest, and never start without a live server: with no queue
            # the commands would silently vanish and the op could never end.
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
        self.setEnabled(False)           # make the whole explorer unclickable
        # Delay the dialog so instant operations (a quick mkdir/rename) don't
        # flash a modal box on screen.
        QTimer.singleShot(250, self._show_op_dialog_if_running)
        enqueue_fn()
        if self._op_total == 0:          # nothing actually queued
            self._end_operation()

    def _show_op_dialog_if_running(self):
        if not self._op_active or self._op_dialog is not None:
            return
        dlg = HdfProgressDialog(self._op_title, self.window(), cancel_label="Cancel")
        dlg.cancel_requested.connect(self._on_op_cancel)
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

    def _update_op_progress(self):
        if self._op_dialog is None or not self._op_determinate:
            return
        if self._op_total > 0:
            self._op_dialog.set_progress(
                int(100 * self._op_completed / self._op_total))

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

    def _end_operation(self):
        self._op_active = False
        if self._op_dialog is not None:
            self._op_dialog.close()
            self._op_dialog = None
        self.setEnabled(True)
        # Tell the user about any Next-side failures this operation hit (one toast
        # for the whole batch). A user cancel is expected, so don't cry failure.
        fails, self._op_failures = self._op_failures, []
        if fails and not self._op_cancelled:
            self._toast_failures(fails)
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

    # ---- worker signal slots (UI thread) ------------------------------
    def on_connected(self):
        self._set_connected(True)
        # Jump straight back to the folder we were last browsing. If it's gone,
        # the listing fails and on_ls_failed() drops us back to the root.
        self._cwd = self._remote_start_dir or "/"
        self.refresh()

    def on_disconnected(self):
        self._set_connected(False)
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

    def on_listing(self, path, entries):
        self._cwd = path if path.startswith("/") else "/" + path
        self.next_path_label.setText(f"Next: {self._cwd}")
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
        if self._cwd not in ("/", ""):
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
        into) no longer exists. Drop back to the root so the pane recovers.
        """
        if (path or "/") == "/":
            return                       # root itself failed: nothing better to do
        self._log(f"{path}: no such folder on the Next — returning to the root.")
        self._on_toast("Folder unavailable",
                       f"{path} no longer exists on the Next.\nReturned to the root.",
                       "yellow")
        self._cwd = "/"
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
        """True if ``path``'s parent is the directory currently shown."""
        parent = posixpath.dirname(path.rstrip("/")) or "/"
        cwd = (self._cwd if self._cwd.startswith("/") else "/" + self._cwd)
        return parent == (cwd.rstrip("/") or "/")

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

    def _next_up(self):
        if self._cwd not in ("/", ""):
            self._cwd = posixpath.dirname(self._cwd.rstrip("/")) or "/"
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
        act_ren = menu.addAction("Rename…")
        act_del = menu.addAction("Delete")
        act_ref = menu.addAction("Refresh")
        # Rename acts on exactly one item.
        act_ren.setEnabled(len(self._selected_next_entries()) == 1)
        chosen = menu.exec(self.next_view.viewport().mapToGlobal(pos))
        if chosen == act_new:
            self._new_folder()
        elif chosen == act_get:
            self._get_selected()
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
        parent = posixpath.dirname(path.rstrip("/")) or "/"
        target = _posix_join(parent, new_name)
        self._run_op("Renaming on the Next…",
                     lambda: self._enqueue(("rename", path, target)))

    def _delete_selected(self):
        entries = self._selected_next_entries()
        if not entries:
            return
        names = "\n".join(p for p, _ in entries)
        if QMessageBox.question(self, "Delete", f"Delete on the Next?\n\n{names}") != QMessageBox.Yes:
            return

        def go():
            for path, is_dir in entries:
                self._enqueue(("rmdir" if is_dir else "rm", path))
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
            self._log(f"{verb} {len(entries)} Next item(s). Paste in the local "
                      "pane to " + ("move" if mode == "cut" else "download") + ".")

    def _copy_local(self, mode="copy"):
        paths = [p for p in self._selected_local_paths() if os.path.exists(p)]
        if paths:
            self._clip = ("local", paths, mode)
            verb = "Cut" if mode == "cut" else "Copied"
            self._log(f"{verb} {len(paths)} local item(s). Paste in the Next "
                      "pane to " + ("move" if mode == "cut" else "upload") + ".")

    def _paste_into_next(self):
        # Paste local clipboard items into the current Next directory: copy =
        # upload, cut = upload then delete the local source once confirmed.
        if not self._connected or not self._clip or self._clip[0] != "local":
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

    def _paste_into_local(self):
        # Paste Next clipboard items into the current local directory: copy =
        # download, cut = download then delete the Next source once confirmed.
        if not self._clip or self._clip[0] != "next":
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
        # Ctrl+C / Ctrl+X copy or cut the local selection; Ctrl+V pastes Next
        # clipboard items here (download). Delete / F2 act on the local pane,
        # mirroring the Next pane. (QFileSystemModel refreshes on change.)
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
        # Paste here downloads the copied/cut Next items into this local folder.
        act_paste.setEnabled(bool(self._clip) and self._clip[0] == "next")
        chosen = menu.exec(self.local_view.viewport().mapToGlobal(pos))
        if chosen == act_new:
            self._local_new_folder()
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
        if event.mimeData().hasFormat("application/x-zxnu-next-entries"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _local_drop(self, event):
        data = event.mimeData().data("application/x-zxnu-next-entries")
        if not data:
            event.ignore()
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
