"""SD Card Utility tab: the dual local ⇄ disk-image explorer pane.

``SdCardExplorerPane`` is the first extraction of the strangler refactor that
shrinks ``MainWindow.__init__``: it owns the explorer pair's WIDGETS and the
navigation / model layer, while the heavy operation layer (hdfmonkey
transfers, deletes, context menus, drag & drop glue, the image load pipeline)
stays in ``zx-next-unite.py`` for now and is reached through a small `hooks`
protocol. MainWindow keeps aliases to every widget under its historical
attribute names (``self.treeview``, ``self.image_treeview``, …), so the
remaining monolith code — and the offscreen test suite — keep working
unchanged while later sittings pull more logic across.

Owned by the pane (3-column grid, the pane itself is the grid container):

    row 0: [ Up|Refresh|"Local path:"|box ]   [ Up|Refresh|"Disk Image Explorer:"|box ]
    row 1: [ local file explorer ][ ⇄ buttons ][ disk image explorer + usage gauge ]
    row 2:                                     [ New Folder / Delete… buttons row ]

plus: the local QFileSystemModel/proxy/tree with all navigation (click
persistence, double-click, Up, Refresh, path-box commits, drive switching,
name filter) and the image QStandardItemModel/tree with its lazy "hdfmonkey
ls" population, find/reload/navigate, selection tracking, path box, filter,
recoloring and the usage-gauge widget.

Host protocol (all late-bound, so construction order in MainWindow barely
matters):

* ``host`` — the MainWindow. The pane READS host attributes
  (``threadpool``, ``right_disk_image_path``, ``img_color_*``) and WRITES the
  selection state the operation layer consumes:
  ``left_file_explorer_selection_file_name`` /
  ``left_file_explorer_selection_full_filename_path`` and
  ``image_selected_path`` / ``image_selected_paths`` /
  ``image_selected_is_dir``.
* ``hooks`` — a SimpleNamespace of callables provided by MainWindow:
  ``get_setting(key)->str`` / ``set_setting(key, value)`` / ``save_config()``,
  ``log(msg)``, ``set_treeview_properties()``,
  ``execute_hdf_monkey(cmd, image_path, extra_argv=...)``,
  ``is_image_loaded()->bool``, ``set_image_loaded(entries_or_none)``,
  ``update_usage_gauge(image_path)`` and
  ``on_local_navigate_side_effects()`` (the legacy NextSync prepare-button
  reset a local double-click has always performed).

The custom item-data roles of the image tree live here (single source; the
operation layer imports them from this module).
"""

import os
import platform

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from zxnu_config import SETTING_EXPLORERPATH, SETTING_IMAGE_EXPLORERPATH
from zxnu_workers import DotDotFirstProxyModel, HdfTaskWorker

# ---------------------------------------------------------------------------
# The disk image explorer is a lazily-populated tree: the image can be listed
# only through hdfmonkey, so every tree item carries its full in-image path
# plus the bookkeeping needed for lazy expansion.
# ---------------------------------------------------------------------------
IMG_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1  # full path inside the image, e.g. "/games/manic.tap"
IMG_ISDIR_ROLE = int(Qt.ItemDataRole.UserRole) + 2  # bool: is this item a directory
IMG_LOADED_ROLE = int(Qt.ItemDataRole.UserRole) + 3  # bool: have this folder's children been loaded
IMG_LOADING_ROLE = int(Qt.ItemDataRole.UserRole) + 4  # bool: a background "ls" for this folder is in flight


def is_filetype_a_directory(file_type: str):
    """True for the type column hdfmonkey's ls prints for directories (the
    same helper also exists as a MainWindow closure for the operation layer)."""
    ft = file_type.strip()
    return ft == "[DIR]" or ft == "b'[DIR]" or ft == 'b"[DIR]'


class SdCardExplorerPane(QWidget):
    """The SD Card tab's explorer pair (see the module docstring)."""

    def __init__(self, host, hooks, drive_combo, initial_root, local_filter_edit, image_filter_edit, transfer_buttons_container=None, image_buttons_container=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._hooks = hooks
        self._drive_combo = drive_combo
        self._local_filter_edit = local_filter_edit
        self._image_filter_edit = image_filter_edit

        # In-flight "hdfmonkey ls" workers are kept alive here until their
        # finished slot runs (Qt drops queued cross-thread signals when the
        # unparented signals object is GC'd), and the load generation
        # invalidates listings that complete after the tree was wiped.
        self._image_ls_workers = set()
        self._image_load_generation = 0

        # ---- local explorer (left pane) -----------------------------------
        self.model = None  # created below; kept as attributes for host aliases
        self._build_local_pane(initial_root)
        # ---- image explorer (right pane) -----------------------------------
        self._build_image_pane()
        # ---- path rows + grid ----------------------------------------------
        self._build_grid(transfer_buttons_container, image_buttons_container)

        # Seed the local path box with the folder the explorer starts on.
        self.local_sync_path_box()

    # ------------------------------------------------------------------ UI --
    def _build_local_pane(self, initial_root):
        from PySide6.QtWidgets import QFileSystemModel

        self.model = QFileSystemModel()
        self.model.setRootPath("/")
        from PySide6.QtCore import QDir

        self.model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)

        self.treeview = QTreeView()
        self.treeview.setSortingEnabled(True)
        # Allow selecting several entries at once so a multi-item drag onto the
        # image explorer uploads them all. Single-target actions (click handler,
        # rename, '->:') still use the current/primary selection.
        self.treeview.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.proxy_model = DotDotFirstProxyModel(recursiveFilteringEnabled=True, filterRole=QFileSystemModel.FileNameRole)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.proxy_model.setDynamicSortFilter(True)

        self.treeview.setModel(self.proxy_model)
        self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(initial_root)))
        self.treeview.setColumnWidth(0, 250)
        self.treeview.doubleClicked.connect(self._on_local_double_clicked)
        self.treeview.clicked.connect(self._on_local_clicked)
        # Context menu / key handling / drag & drop are operation-layer glue
        # and stay wired by MainWindow onto these widgets (via its aliases).

        if self._drive_combo is not None:
            self._drive_combo.activated.connect(self.update_root_drive)

        if self._local_filter_edit is not None:
            self._local_filter_edit.textChanged.connect(self.apply_local_filter)

    def _build_image_pane(self):
        self._img_folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._img_file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self.image_model = QStandardItemModel(self)
        self.image_treeview = QTreeView(self)
        self.image_treeview.setModel(self.image_model)
        self.image_treeview.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_treeview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.image_treeview.setUniformRowHeights(True)
        self.image_treeview.setSortingEnabled(True)
        self.image_treeview.expanded.connect(self._on_image_expanded)
        self.image_treeview.selectionModel().selectionChanged.connect(self._on_image_selection_changed)
        self.set_table_image_properties()

        if self._image_filter_edit is not None:
            self._image_filter_edit.textChanged.connect(self.apply_image_filter)

        # Usage gauge — sits directly below the image explorer table.
        self.image_usage_gauge = QProgressBar()
        self.image_usage_gauge.setRange(0, 100)
        self.image_usage_gauge.setValue(0)
        self.image_usage_gauge.setFormat("No image loaded")
        self.image_usage_gauge.setFixedHeight(18)
        self.image_usage_gauge.setToolTip("No SD card image is currently loaded.")
        self.image_usage_gauge.setTextVisible(True)

        self.image_explorer_container = QWidget()
        image_explorer_vbox = QVBoxLayout(self.image_explorer_container)
        image_explorer_vbox.setContentsMargins(0, 0, 0, 0)
        image_explorer_vbox.setSpacing(2)
        image_explorer_vbox.addWidget(self.image_treeview)
        image_explorer_vbox.addWidget(self.image_usage_gauge)

    def _build_grid(self, transfer_buttons_container, image_buttons_container):
        # Path row above the explorers: editable boxes showing the local
        # folder / in-image target folder, each with Up / Refresh buttons
        # mirroring the Remote Explorer's top bars.
        self.local_file_explorer_path = QLineEdit()
        self.local_file_explorer_path.setPlaceholderText("Local folder path...")
        self.local_file_explorer_path.setClearButtonEnabled(True)
        self.local_file_explorer_path.setToolTip(
            "Folder currently shown in the local file explorer below.\nType or paste a folder (or file) path and press Enter to navigate there;\nthe drive selector follows automatically."
        )
        self.local_file_explorer_path.editingFinished.connect(self._on_local_path_edited)

        self.local_explorer_up_button = QPushButton("Up", self)
        self.local_explorer_up_button.setMaximumWidth(48)
        self.local_explorer_up_button.setToolTip("Go up one folder in the local file explorer\n(same as double-clicking its '..' entry).")
        self.local_explorer_up_button.clicked.connect(self.local_explorer_up)

        self.local_explorer_refresh_button = QPushButton("Refresh", self)
        self.local_explorer_refresh_button.setMaximumWidth(72)
        self.local_explorer_refresh_button.setToolTip("Re-read the current local folder from disk.")
        self.local_explorer_refresh_button.clicked.connect(self.local_explorer_refresh)

        self.localexplorerlabel = QLabel()
        self.localexplorerlabel.setText("Local path: ")

        self.local_path_row_container = QWidget()
        local_path_row = QHBoxLayout(self.local_path_row_container)
        local_path_row.setContentsMargins(0, 0, 0, 0)
        local_path_row.addWidget(self.local_explorer_up_button)
        local_path_row.addWidget(self.local_explorer_refresh_button)
        local_path_row.addWidget(self.localexplorerlabel)
        local_path_row.addWidget(self.local_file_explorer_path, 1)

        self.image_explorer_up_button = QPushButton("Up", self)
        self.image_explorer_up_button.setMaximumWidth(48)
        self.image_explorer_up_button.setToolTip("Select the parent folder inside the SD card image\n(at the top level this returns the target to the image root).")
        self.image_explorer_up_button.clicked.connect(self.image_explorer_up)

        self.image_explorer_refresh_button = QPushButton("Refresh", self)
        self.image_explorer_refresh_button.setMaximumWidth(72)
        self.image_explorer_refresh_button.setToolTip("Re-list the current image folder from the SD card image\n(runs 'hdfmonkey ls' again).")
        self.image_explorer_refresh_button.clicked.connect(self.image_explorer_refresh)

        self.diskimageexplorerlabel = QLabel()
        self.diskimageexplorerlabel.setText("Disk Image Explorer: ")

        self.diskimageexplorerpathinput = QLineEdit()
        self.diskimageexplorerpathinput.setPlaceholderText("Path inside the SD card image...")
        self.diskimageexplorerpathinput.setClearButtonEnabled(True)
        self.diskimageexplorerpathinput.setToolTip(
            "Current target folder inside the SD card image (uploads, New Folder\n"
            "and gallery sends land here). Type or paste an in-image path such as\n"
            "/games and press Enter to navigate the disk image explorer there;\n"
            "an empty path or / selects the image root."
        )
        self.diskimageexplorerpathinput.editingFinished.connect(self._on_image_path_edited)

        self.image_path_row_container = QWidget()
        image_path_row = QHBoxLayout(self.image_path_row_container)
        image_path_row.setContentsMargins(0, 0, 0, 0)
        image_path_row.addWidget(self.image_explorer_up_button)
        image_path_row.addWidget(self.image_explorer_refresh_button)
        image_path_row.addWidget(self.diskimageexplorerlabel)
        image_path_row.addWidget(self.diskimageexplorerpathinput, 1)

        # 3-column grid; the two explorer columns share the stretch equally so
        # each path row matches its explorer's width, and the New Folder /
        # Delete buttons sit directly under the disk image explorer.
        self.sdcard_explorer_grid = QGridLayout(self)
        self.sdcard_explorer_grid.setContentsMargins(0, 0, 0, 0)
        self.sdcard_explorer_grid.addWidget(self.local_path_row_container, 0, 0)
        self.sdcard_explorer_grid.addWidget(self.image_path_row_container, 0, 2)
        self.sdcard_explorer_grid.addWidget(self.treeview, 1, 0)
        self.sdcard_explorer_grid.addWidget(self.image_explorer_container, 1, 2)
        self.sdcard_explorer_grid.setColumnStretch(0, 1)
        self.sdcard_explorer_grid.setColumnStretch(2, 1)
        self.sdcard_explorer_grid.setRowStretch(1, 1)
        # The centre transfer-buttons column (1,1) and the New Folder /
        # Delete row (2,2) are operation-wired widgets: when not handed in
        # here, MainWindow slots them into this grid once it has built them.
        if transfer_buttons_container is not None:
            self.sdcard_explorer_grid.addWidget(transfer_buttons_container, 1, 1)
        if image_buttons_container is not None:
            self.sdcard_explorer_grid.addWidget(image_buttons_container, 2, 2)

    # ------------------------------------------------- local explorer: nav --
    def update_root_drive(self, _index=None):
        drive = (self._drive_combo.currentText() or self._drive_combo.itemText(0)) if self._drive_combo else "/"
        self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(drive)))
        self._hooks.set_treeview_properties()
        self.treeview.show()
        self.local_sync_path_box()

    def apply_local_filter(self, _text=None):
        """Real-time name filter for the local tree (the Search box lives in
        MainWindow's top row; its textChanged is connected here)."""
        if self._local_filter_edit is None:
            return
        self.proxy_model.setFilterWildcard(self._local_filter_edit.text())

    def local_current_view_dir(self):
        """Path of the folder currently shown at the top of the local tree."""
        root_src = self.proxy_model.mapToSource(self.treeview.rootIndex())
        return self.model.filePath(root_src)

    def local_sync_path_box(self):
        """Reflect the folder currently shown in the local explorer into its
        path box, and keep the Windows drive selector on that folder's drive.
        Called after every navigation (double-click, drive change, path-box
        commit, startup restore, post-upload re-root)."""
        path = self.local_current_view_dir() or ""
        if platform.system() == "Windows" and self._drive_combo is not None and len(path) >= 2 and path[1] == ":":
            want = path[0].upper()
            for i in range(self._drive_combo.count()):
                if self._drive_combo.itemText(i)[:1].upper() == want:
                    # setCurrentIndex does not emit activated, so this cannot
                    # re-enter update_root_drive.
                    if self._drive_combo.currentIndex() != i:
                        self._drive_combo.setCurrentIndex(i)
                    break
        self.local_file_explorer_path.setText(path)

    def local_navigate_to_dir(self, dest):
        """Root the local tree at *dest* (forward slashes, trailing '/'),
        persist it like a double-click navigation, and sync the path box."""
        host = self._host
        host.left_file_explorer_selection_file_name = ""
        host.left_file_explorer_selection_full_filename_path = dest
        self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(dest, 0)))
        self._hooks.set_treeview_properties()
        self.treeview.show()
        self._hooks.set_setting(SETTING_EXPLORERPATH, dest)
        self._hooks.save_config()
        self.local_sync_path_box()

    def _on_local_clicked(self):
        host = self._host
        for ix in self.treeview.selectedIndexes():
            source_ix = self.proxy_model.mapToSource(ix)
            if self.model.fileName(source_ix) == "..":
                # Don't navigate on single-click; navigation happens on
                # double-click. Just clear the current selection so no stale
                # file path is carried.
                host.left_file_explorer_selection_file_name = ""
                host.left_file_explorer_selection_full_filename_path = ""
                break
            host.left_file_explorer_selection_file_name = self.model.fileName(source_ix)
            host.left_file_explorer_selection_full_filename_path = self.model.filePath(source_ix)
            if platform.system() != "Windows":
                host.left_file_explorer_selection_full_filename_path.replace("\\", "/")
            self._hooks.set_setting(SETTING_EXPLORERPATH, host.left_file_explorer_selection_full_filename_path)
            self._hooks.save_config()
            break

    def _on_local_double_clicked(self, ix):
        # ix is the proxy index passed directly by the doubleClicked signal
        if not ix.isValid():
            return
        # Legacy side effect kept from the original handler: reset the
        # NextSync prepare/start buttons (see the hook in MainWindow).
        self._hooks.on_local_navigate_side_effects()

        source_ix = self.proxy_model.mapToSource(ix)
        file_name = self.model.fileName(source_ix)
        file_path = self.model.filePath(source_ix)

        if file_name == "..":
            # Navigate one level up using the current root path as reference.
            parent_path = os.path.dirname(self.local_current_view_dir().rstrip("/\\"))
            if not parent_path:
                return
            dest = parent_path.replace("\\", "/") + "/"
        elif self.model.isDir(source_ix):
            dest = file_path
            if not dest.endswith("/"):
                dest += "/"
        else:
            return
        self.local_navigate_to_dir(dest)

    def _on_local_path_edited(self):
        # Typing or pasting a path into the local path box navigates the left
        # explorer there (a file path lands on its parent folder) and persists
        # it like a double-click navigation would. Anything that isn't an
        # existing path restores the box to the current folder.
        new_path = self.local_file_explorer_path.text().strip().strip('"')
        if os.path.isfile(new_path):
            new_path = os.path.dirname(new_path)
        if new_path and os.path.isdir(new_path):
            norm = new_path.replace("\\", "/")
            if not norm.endswith("/"):
                norm += "/"
            self.local_navigate_to_dir(norm)
        else:
            self.local_sync_path_box()

    def local_explorer_up(self):
        """Navigate one folder up — the 'Up' button twin of double-clicking
        the '..' entry (mirrors the Remote Explorer's Up button)."""
        current_root = self.local_current_view_dir()
        parent_path = os.path.dirname((current_root or "").rstrip("/\\"))
        if not parent_path or not os.path.isdir(parent_path):
            return
        dest = parent_path.replace("\\", "/")
        if not dest.endswith("/"):
            dest += "/"
        self.local_navigate_to_dir(dest)

    def local_explorer_refresh(self):
        """Force the local explorer to re-stat the folder currently shown, so
        files just dropped in appear immediately rather than waiting on the
        filesystem watcher."""
        try:
            view_path = self.local_current_view_dir()
            self.model.setRootPath("")
            self.model.setRootPath(view_path or "/")
            if view_path:
                self.treeview.setRootIndex(self.proxy_model.mapFromSource(self.model.index(view_path)))
        except Exception as exc:
            self._hooks.log(f"Local explorer refresh failed: {exc}")

    # ---------------------------------------------- image explorer: model --
    def set_table_image_properties(self):
        # Header + column sizing for the image explorer tree. clear() drops
        # the headers, so every reset path re-applies them here.
        self.image_model.setHorizontalHeaderLabels(["Name", "Type", "Size"])
        self.image_treeview.setColumnWidth(0, 250)

    def apply_image_filter(self, _text=None):
        # Filter the image explorer tree in real-time. A row is shown when its
        # own Name/Type/Size text matches OR any of its (already-loaded)
        # descendants match, so matches stay reachable inside folders.
        text = self._image_filter_edit.text().strip().lower() if self._image_filter_edit is not None else ""

        def _filter(parent_item):
            any_visible = False
            for r in range(parent_item.rowCount()):
                name_item = parent_item.child(r, 0)
                if name_item is None:
                    continue
                child_match = _filter(name_item)
                row_text = " ".join((parent_item.child(r, c).text() if parent_item.child(r, c) else "") for c in range(self.image_model.columnCount())).lower()
                self_match = (text in row_text) if text else True
                visible = self_match or child_match
                self.image_treeview.setRowHidden(r, name_item.index().parent(), not visible)
                any_visible = any_visible or visible
            return any_visible

        _filter(self.image_model.invisibleRootItem())

    def image_dest_dir(self):
        """Image directory targeted by uploads / new folders, based on the
        current tree selection: a selected folder -> that folder; a selected
        file -> the folder containing it; nothing selected -> the image root."""
        host = self._host
        if host.image_selected_path:
            if host.image_selected_is_dir:
                return host.image_selected_path or "/"
            parent = host.image_selected_path.rstrip("/").rsplit("/", 1)[0]
            return parent if parent else "/"
        return "/"

    def image_update_path_label(self):
        if self._hooks.is_image_loaded():
            target = self.image_dest_dir().replace("//", "/")
            self.diskimageexplorerpathinput.setText(target)
            # Persist the in-image target folder so the next startup can
            # restore the disk image explorer to it.
            if self._hooks.get_setting(SETTING_IMAGE_EXPLORERPATH) != target:
                self._hooks.set_setting(SETTING_IMAGE_EXPLORERPATH, target)
                self._hooks.save_config()
        else:
            self.diskimageexplorerpathinput.setText("Please load an image.")

    def image_clear_model(self):
        # Wipe the tree and re-apply the column headers (clear() drops them).
        # Bumping the load generation invalidates any in-flight background
        # "hdfmonkey ls" worker: clear() frees every QStandardItem, so a worker
        # that captured one must not repopulate it (see image_populate_item).
        self._image_load_generation += 1
        self.image_model.clear()
        self.set_table_image_properties()

    @staticmethod
    def image_parse_ls(ls_stdout):
        """Parse 'hdfmonkey ls' output into a list of (name, is_dir, size)."""
        entries = []
        for line in ls_stdout.splitlines():
            decoded = line.decode(errors="replace") if isinstance(line, bytes) else line
            parts = decoded.split("\t", 1)
            if len(parts) < 2:
                continue
            file_type, file_name = parts[0], parts[1]
            if is_filetype_a_directory(file_type):
                entries.append((file_name, True, ""))
            else:
                try:
                    # file_type is e.g. "[1234 bytes]" – extract the number
                    file_size = file_type.strip("[]").split()[0]
                except Exception:
                    file_size = "0"
                entries.append((file_name, False, file_size))
        return entries

    def image_make_row(self, name, is_dir, size_value, full_path):
        """Build the [name, type, size] QStandardItem row for one entry."""
        host = self._host
        name_item = QStandardItem(self._img_folder_icon if is_dir else self._img_file_icon, str(name))
        name_item.setEditable(False)
        name_item.setData(full_path, IMG_PATH_ROLE)
        name_item.setData(is_dir, IMG_ISDIR_ROLE)
        name_item.setData(False, IMG_LOADED_ROLE)

        if is_dir:
            type_item = QStandardItem("DIR")
            size_item = QStandardItem("")
            name_item.setForeground(host.img_color_dir_name)
            type_item.setForeground(host.img_color_dir_type)
        else:
            file_ext = str.split(name, ".")[1] if "." in name else ""
            type_item = QStandardItem(file_ext)
            size_item = QStandardItem()
            # Store the size as an int so the Size column sorts numerically.
            size_item.setData(int(size_value) if str(size_value).isdigit() else str(size_value), Qt.ItemDataRole.DisplayRole)
            name_item.setForeground(host.img_color_file_name)
            type_item.setForeground(host.img_color_file_ext)
            size_item.setForeground(host.img_color_file_size)

        type_item.setEditable(False)
        size_item.setEditable(False)
        return [name_item, type_item, size_item]

    def image_recolor_all(self):
        """Re-apply the configured item colors to every row already shown in
        the image explorer tree, in place (synchronous — no re-listing).
        Used when the user changes the colors in Settings and when returning
        to the SD Card Utility tab."""
        host = self._host

        def _recolor(parent_item):
            for r in range(parent_item.rowCount()):
                name_item = parent_item.child(r, 0)
                if name_item is None:
                    continue
                type_item = parent_item.child(r, 1)
                size_item = parent_item.child(r, 2)
                if bool(name_item.data(IMG_ISDIR_ROLE)):
                    name_item.setForeground(host.img_color_dir_name)
                    if type_item is not None:
                        type_item.setForeground(host.img_color_dir_type)
                else:
                    name_item.setForeground(host.img_color_file_name)
                    if type_item is not None:
                        type_item.setForeground(host.img_color_file_ext)
                    if size_item is not None:
                        size_item.setForeground(host.img_color_file_size)
                if name_item.hasChildren():
                    _recolor(name_item)

        try:
            _recolor(self.image_model.invisibleRootItem())
        except Exception:
            pass

    # ------------------------------------------- image explorer: population --
    def image_populate_item(self, parent_name_item, dir_path, on_done=None):
        """(Re)load the children of *dir_path* under *parent_name_item*
        (None = the invisible root) without blocking the UI thread.

        The slow part — the "hdfmonkey ls" subprocess — runs on the thread
        pool; the parsed entries are then turned into tree rows back on the UI
        thread (QStandardItem objects must only be created/attached there).
        Folders get a placeholder child so the expand arrow appears; the
        placeholder is replaced on first expand.

        *on_done* (optional) is invoked on the UI thread once population
        finishes, with the parsed entries list, or None on hdfmonkey failure.
        """
        parent = parent_name_item if parent_name_item is not None else self.image_model.invisibleRootItem()
        image_path = self._host.right_disk_image_path
        # Capture the load generation so a listing that completes after the
        # tree was wiped/reloaded is discarded instead of mutating a stale
        # (or freed) model.
        gen = self._image_load_generation
        holder = {}

        def _ls_fn(signals, cancel_event, _img=image_path, _dir=dir_path, _h=holder):
            result = self._hooks.execute_hdf_monkey("ls", _img, extra_argv=[_dir])
            if result.returncode != 0:
                _h["rc"] = result.returncode
            else:
                _h["entries"] = self.image_parse_ls(result.stdout)

        def _finish():
            # Release our keep-alive reference now that the slot is running.
            self._image_ls_workers.discard(worker)
            # A newer load wiped/replaced the tree while we were listing —
            # the captured QStandardItems may already be deleted, so do not
            # touch the model. The newer load owns the final UI state.
            if gen != self._image_load_generation:
                return
            entries = holder.get("entries")
            if entries is None:
                rc = holder.get("rc", "?")
                self._hooks.log(f"Failed listing image directory: {dir_path} - hdfmonkey result code: {rc}")
                if on_done is not None:
                    on_done(None)
                return

            parent.removeRows(0, parent.rowCount())
            for name, is_dir, size in entries:
                full_path = (dir_path + "/" + name).replace("//", "/")
                row = self.image_make_row(name, is_dir, size, full_path)
                parent.appendRow(row)
                if is_dir:
                    # Placeholder so the expand arrow shows before the folder
                    # is actually listed; replaced on first expand.
                    row[0].appendRow([QStandardItem("")])

            if parent_name_item is not None:
                parent_name_item.setData(True, IMG_LOADED_ROLE)
            if on_done is not None:
                on_done(entries)

        worker = HdfTaskWorker(_ls_fn)
        # Keep the worker (and its signals) alive until _finish runs, so the
        # queued cross-thread `finished` slot can't be dropped by GC.
        self._image_ls_workers.add(worker)
        worker.signals.finished.connect(_finish)
        self._host.threadpool.start(worker)

    def _on_image_expanded(self, index):
        # Lazy-load a folder's contents the first time it is expanded. The
        # listing runs on a worker thread; the folder shows its placeholder
        # row until the real children arrive, so expanding never blocks the UI.
        if not index.isValid():
            return
        name_item = self.image_model.itemFromIndex(index.siblingAtColumn(0))
        if name_item is None:
            return
        if name_item.data(IMG_ISDIR_ROLE) and not name_item.data(IMG_LOADED_ROLE) and not name_item.data(IMG_LOADING_ROLE):
            # Guard against a second worker being launched if the user
            # collapses and re-expands before the first listing returns.
            name_item.setData(True, IMG_LOADING_ROLE)

            def _after(_entries, _item=name_item):
                _item.setData(False, IMG_LOADING_ROLE)
                self.apply_image_filter()

            self.image_populate_item(name_item, name_item.data(IMG_PATH_ROLE), _after)

    def image_load_root(self, on_done=None):
        """Build the tree from the image root without blocking the UI thread.

        *on_done* (optional) is invoked on the UI thread with True on success
        or False on hdfmonkey failure once the root listing completes."""
        host = self._host
        self.image_clear_model()
        host.image_selected_path = ""
        host.image_selected_is_dir = False

        def _after(entries):
            if entries is None:
                self._hooks.set_image_loaded(None)
                self._hooks.update_usage_gauge("")
                if on_done is not None:
                    on_done(False)
                return
            # The host keeps the "an image is loaded" flag + the legacy flat
            # item list in sync (they gate the whole operation layer).
            self._hooks.set_image_loaded(entries)
            self.apply_image_filter()
            self._hooks.update_usage_gauge(host.right_disk_image_path)
            if on_done is not None:
                on_done(True)

        self.image_populate_item(None, "/", _after)

    def image_find_item(self, path):
        """Return the column-0 QStandardItem for *path* among already-loaded
        tree nodes, or None (None also means "the root")."""
        if not path or path.rstrip("/") == "":
            return None
        target = path.rstrip("/")
        root = self.image_model.invisibleRootItem()
        stack = [root.child(r, 0) for r in range(root.rowCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if (item.data(IMG_PATH_ROLE) or "").rstrip("/") == target:
                return item
            for r in range(item.rowCount()):
                stack.append(item.child(r, 0))
        return None

    def image_reload_dir(self, path):
        """Reload the children of the image directory at *path*, preserving
        the rest of the tree's expansion state. Falls back to a full root
        reload when the directory isn't currently materialised in the tree.
        The listing runs on a worker thread, so this returns immediately."""
        item = self.image_find_item(path)
        if item is None:
            self.image_load_root()
            return
        was_expanded = self.image_treeview.isExpanded(item.index())

        def _after(_entries, _item=item, _expanded=was_expanded):
            if _expanded:
                self.image_treeview.expand(_item.index())
            self.apply_image_filter()
            self._hooks.update_usage_gauge(self._host.right_disk_image_path)

        self.image_populate_item(item, item.data(IMG_PATH_ROLE), _after)

    # ------------------------------------------- image explorer: navigation --
    def image_navigate_to_path(self, path):
        """Navigate the disk image explorer to *path* (an in-image path):
        walk the tree down from the root, listing not-yet-loaded folders on
        demand (async, one background "hdfmonkey ls" per level), expanding
        each level and finally selecting the target entry — so uploads /
        New Folder target it, exactly as if the user had clicked it.
        "/" (or an empty path) clears the selection back to the image root;
        a segment that doesn't exist logs an advisory and the path box
        falls back to the current target directory."""
        segments = [s for s in path.replace("\\", "/").split("/") if s]
        if not segments:
            # Root: clear current + selection so actions target "/" again
            # (the selection-changed handler refreshes the path box).
            self.image_treeview.setCurrentIndex(QModelIndex())
            self.image_treeview.selectionModel().clearSelection()
            self.image_update_path_label()
            return
        # A listing finishing after the image was reloaded/cleared must not
        # keep descending into freed items (same guard as image_populate_item).
        gen = self._image_load_generation

        def _find_child(parent_item, name):
            # FAT is case-insensitive, so match the segment likewise.
            parent = parent_item if parent_item is not None else self.image_model.invisibleRootItem()
            for r in range(parent.rowCount()):
                child = parent.child(r, 0)
                if child is not None and child.text().lower() == name.lower():
                    return child
            return None

        def _descend(parent_item, remaining):
            if gen != self._image_load_generation:
                return
            child = _find_child(parent_item, remaining[0])
            if child is None:
                self._hooks.log(f"Image path not found: {path}")
                self.image_update_path_label()
                return
            rest = remaining[1:]
            if not rest:
                # Target reached: select it (folder or file). setCurrentIndex
                # fires the selection-changed handler, which updates the path
                # box to the resulting target directory.
                idx = child.index()
                self.image_treeview.setCurrentIndex(idx)
                self.image_treeview.scrollTo(idx)
                if child.data(IMG_ISDIR_ROLE):
                    # Expanding also lazy-loads the folder via _on_image_expanded.
                    self.image_treeview.expand(idx)
                return
            if not child.data(IMG_ISDIR_ROLE):
                self._hooks.log(f"Not a directory in image: {child.data(IMG_PATH_ROLE)}")
                self.image_update_path_label()
                return
            if child.data(IMG_LOADED_ROLE):
                self.image_treeview.expand(child.index())
                _descend(child, rest)
                return
            if child.data(IMG_LOADING_ROLE):
                # A listing for this folder is already in flight (e.g. from
                # the expand of a just-finished navigation). Launching a
                # second one would race it: whichever "ls" finishes last
                # does removeRows() and wipes the winner's rows — and with
                # them the selection the walk just made. Wait for the
                # in-flight listing instead, then retry this level.
                QTimer.singleShot(50, lambda: _descend(parent_item, remaining))
                return
            # Children not listed yet — list them, then continue the walk.
            # The IMG_LOADING_ROLE guard keeps _on_image_expanded from
            # launching a second listing for the same folder meanwhile.
            child.setData(True, IMG_LOADING_ROLE)

            def _after(entries, _child=child, _rest=rest):
                _child.setData(False, IMG_LOADING_ROLE)
                self.apply_image_filter()
                if entries is None or gen != self._image_load_generation:
                    self.image_update_path_label()
                    return
                self.image_treeview.expand(_child.index())
                _descend(_child, _rest)

            self.image_populate_item(child, child.data(IMG_PATH_ROLE), _after)

        _descend(None, segments)

    def _on_image_path_edited(self):
        # Typing or pasting a path into the disk-image path box navigates the
        # image explorer there (expand + select), mirroring the local path box
        # on the left. Without a loaded image the box just falls back to its
        # "Please load an image." text.
        if not self._hooks.is_image_loaded():
            self.image_update_path_label()
            return
        self.image_navigate_to_path(self.diskimageexplorerpathinput.text().strip().strip('"'))

    def image_explorer_up(self):
        """'Up' button of the disk image explorer: select the parent of the
        current target directory (at the top level this clears the selection,
        so actions target the image root again)."""
        if not self._hooks.is_image_loaded():
            return
        cur = self.image_dest_dir().replace("//", "/")
        if not cur or cur == "/":
            return
        parent = cur.rstrip("/").rsplit("/", 1)[0] or "/"
        self.image_navigate_to_path(parent)

    def image_explorer_refresh(self):
        """'Refresh' button of the disk image explorer: re-list the current
        target directory via hdfmonkey (full root reload when it isn't
        materialised in the tree)."""
        if not self._hooks.is_image_loaded():
            return
        self.image_reload_dir(self.image_dest_dir())

    def _on_image_selection_changed(self, *_):
        """Track the selection for the operation layer: every selected row in
        host.image_selected_paths, the primary item in host.image_selected_path
        / image_selected_is_dir, and the legacy name list the host maintains.
        """
        host = self._host
        host.image_selected_path = ""
        host.image_selected_is_dir = False
        host.image_selected_paths = []
        selected_names = []

        sel_model = self.image_treeview.selectionModel()
        for col0 in sel_model.selectedRows(0):
            name_item = self.image_model.itemFromIndex(col0)
            if name_item is None:
                continue
            path = name_item.data(IMG_PATH_ROLE) or ""
            if not path:
                continue
            is_dir = bool(name_item.data(IMG_ISDIR_ROLE))
            host.image_selected_paths.append((path, is_dir))
            selected_names.append(name_item.text())

        current = self.image_treeview.currentIndex()
        primary_item = None
        if current.isValid():
            primary_item = self.image_model.itemFromIndex(current.siblingAtColumn(0))
        if primary_item is None or not (primary_item.data(IMG_PATH_ROLE) or ""):
            # Fall back to the first selected row.
            if host.image_selected_paths:
                (host.image_selected_path, host.image_selected_is_dir) = host.image_selected_paths[0]
        else:
            host.image_selected_path = primary_item.data(IMG_PATH_ROLE) or ""
            host.image_selected_is_dir = bool(primary_item.data(IMG_ISDIR_ROLE))

        self._hooks.set_selected_names(selected_names)
        self.image_update_path_label()
