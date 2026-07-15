"""Remote file-explorer widget for the NextSync tab.

A dual-pane file manager modelled on the SD Card Utility tab, but the right
("Next") pane is driven by the NextSync ``.sync4 -listen`` protocol
(zxnu_workers.run_remote_listen_server) instead of hdfmonkey:

    [ local file explorer ] [ ->:  :<- ] [ Next file explorer ]

Commands are pushed onto a queue.Queue the listen worker drains; results arrive
through RemoteExplorerSignals and are applied here on the UI thread.
"""

import os
import posixpath

from PySide6.QtCore import Qt, QDir, QModelIndex, QMimeData, QUrl, QSize
from PySide6.QtGui import QDrag, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QFileSystemModel, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QMessageBox, QPushButton, QStyle, QTreeView, QVBoxLayout,
    QWidget,
)

# Roles carrying the remote entry's full posix path and its directory flag.
RE_PATH_ROLE = Qt.UserRole + 1
RE_ISDIR_ROLE = Qt.UserRole + 2

ARROW_BTN_W = 40


def _human_size(n):
    if n is None:
        return ""
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n}{unit}" if unit == "B" else f"{n/1024:.0f}{unit}"
        n /= 1024
    return str(n)


def _posix_join(base, name):
    base = base or "/"
    if not base.endswith("/"):
        base += "/"
    return posixpath.normpath(base + name)


class RemoteExplorerWidget(QWidget):
    """Dual-pane local <-> Next file manager.

    enqueue(cmd_tuple) is the single channel to the listen worker; the host wires
    the worker's signals to on_connected/on_disconnected/on_listing/on_got/
    on_put_done/on_op_done.  `log` is an optional callable(str) for status lines.
    """

    def __init__(self, enqueue, local_start_dir=None, log=None, parent=None):
        super().__init__(parent)
        self._enqueue = enqueue
        self._log = log or (lambda s: None)
        self._cwd = "/"                      # current Next directory
        self._connected = False

        # ---- left: local file explorer ------------------------------------
        self.local_model = QFileSystemModel(self)
        self.local_model.setRootPath("")
        self.local_view = QTreeView(self)
        self.local_view.setModel(self.local_model)
        self.local_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.local_view.setUniformRowHeights(True)
        self.local_view.setSortingEnabled(True)
        for col in (1, 2, 3):
            self.local_view.hideColumn(col)      # keep just the name column
        self.local_view.setDragEnabled(True)
        self.local_view.setAcceptDrops(True)
        self.local_view.setDropIndicatorShown(True)
        self.local_view.doubleClicked.connect(self._local_double_clicked)
        self.local_view.dragEnterEvent = self._local_drag_enter
        self.local_view.dragMoveEvent = self._local_drag_enter
        self.local_view.dropEvent = self._local_drop

        self.local_path_label = QLabel(self)
        self.local_path_label.setToolTip("Local folder (double-click a folder to enter, Up to go back)")

        start = local_start_dir if (local_start_dir and os.path.isdir(local_start_dir)) \
            else QDir.homePath()
        self._set_local_dir(start)

        local_up = QPushButton("Up", self)
        local_up.setMaximumWidth(48)
        local_up.clicked.connect(self._local_up)
        local_bar = QHBoxLayout()
        local_bar.setContentsMargins(0, 0, 0, 0)
        local_bar.addWidget(local_up)
        local_bar.addWidget(self.local_path_label, 1)

        local_box = QVBoxLayout()
        local_box.setContentsMargins(0, 0, 0, 0)
        local_box.setSpacing(2)
        local_box.addLayout(local_bar)
        local_box.addWidget(self.local_view)
        local_container = QWidget(self)
        local_container.setLayout(local_box)

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
        self.next_model.setHorizontalHeaderLabels(["Name", "Size"])
        self.next_view = QTreeView(self)
        self.next_view.setModel(self.next_model)
        self.next_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.next_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.next_view.setUniformRowHeights(True)
        self.next_view.setRootIsDecorated(False)
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
    #  connection state
    # ==================================================================
    def _set_connected(self, on):
        self._connected = on
        for w in (self.btn_to_next, self.btn_to_local, self.btn_new_folder,
                  self.btn_rename, self.btn_delete, self.next_view):
            w.setEnabled(on)
        if not on:
            self.next_model.removeRows(0, self.next_model.rowCount())
            self.next_path_label.setText("Next: (waiting for .sync4 -listen …)")

    # ---- worker signal slots (UI thread) ------------------------------
    def on_connected(self):
        self._set_connected(True)
        self._cwd = "/"
        self.refresh()

    def on_disconnected(self):
        self._set_connected(False)

    def on_listing(self, path, entries):
        self._cwd = path if path.startswith("/") else "/" + path
        self.next_path_label.setText(f"Next: {self._cwd}")
        self.next_model.removeRows(0, self.next_model.rowCount())
        if self._cwd not in ("/", ""):
            self._add_next_row("..", True, None, is_updir=True)
        for is_dir, size, name in entries:
            if name in (".", ".."):
                continue
            self._add_next_row(name, is_dir, size)
        self.next_view.resizeColumnToContents(0)

    def on_got(self, remote, local_path):
        self._log(f"Downloaded {remote} -> {local_path}")
        self.local_model.setRootPath(self.local_model.rootPath())  # nudge a refresh

    def on_put_done(self, ok, remote):
        self._log(f"Uploaded -> {remote}" if ok else f"Upload failed: {remote}")
        self.refresh()

    def on_op_done(self, ok, op, path):
        self._log(f"{op} {path}: {'ok' if ok else 'FAILED'}")
        self.refresh()

    # ==================================================================
    #  Next pane
    # ==================================================================
    def _add_next_row(self, name, is_dir, size, is_updir=False):
        name_item = QStandardItem(self._dir_icon if is_dir else self._file_icon, name)
        name_item.setData(".." if is_updir else _posix_join(self._cwd, name), RE_PATH_ROLE)
        name_item.setData(bool(is_dir), RE_ISDIR_ROLE)
        size_item = QStandardItem("" if is_dir else _human_size(size))
        self.next_model.appendRow([name_item, size_item])

    def refresh(self):
        if self._connected:
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
            self._enqueue(("mkdir", _posix_join(self._cwd, name.strip())))

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
        self._enqueue(("rename", path, _posix_join(parent, new_name)))

    def _delete_selected(self):
        entries = self._selected_next_entries()
        if not entries:
            return
        names = "\n".join(p for p, _ in entries)
        if QMessageBox.question(self, "Delete", f"Delete on the Next?\n\n{names}") != QMessageBox.Yes:
            return
        for path, is_dir in entries:
            self._enqueue(("rmdir" if is_dir else "rm", path))

    # ==================================================================
    #  transfers
    # ==================================================================
    def _get_selected(self):
        entries = self._selected_next_entries()
        if not entries:
            return
        dest = self._local_dir()
        for path, _is_dir in entries:
            self._enqueue(("get", path, dest))
        self._log(f"Downloading {len(entries)} item(s) to {dest} …")

    def _put_selected(self):
        files = [p for p in self._selected_local_paths() if os.path.isfile(p)]
        if not files:
            self._log("Select local file(s) to upload (folders: drag or one at a time).")
            return
        self._put_files(files)

    def _put_files(self, files):
        base = self._cwd if self._cwd.endswith("/") else self._cwd + "/"
        for f in files:
            self._enqueue(("put", f, base))
        self._log(f"Uploading {len(files)} file(s) to {self._cwd} …")

    # ==================================================================
    #  local pane
    # ==================================================================
    def _set_local_dir(self, path):
        self.local_view.setRootIndex(self.local_model.index(path))
        self.local_path_label.setText(path)

    def _local_dir(self):
        return self.local_model.filePath(self.local_view.rootIndex()) or QDir.homePath()

    def _local_up(self):
        cur = self._local_dir()
        parent = os.path.dirname(cur.rstrip("/\\"))
        if parent and os.path.isdir(parent):
            self._set_local_dir(parent)

    def _local_double_clicked(self, index):
        path = self.local_model.filePath(index)
        if os.path.isdir(path):
            self._set_local_dir(path)

    def _selected_local_paths(self):
        out = []
        for ix in self.local_view.selectionModel().selectedRows(0):
            p = self.local_model.filePath(ix)
            if p:
                out.append(p)
        return out

    # ==================================================================
    #  drag & drop
    # ==================================================================
    def _next_drag_enter(self, event):
        if self._connected and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _next_drop(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self._put_files(paths)

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
        for line in bytes(data).decode(errors="replace").splitlines():
            if "\t" in line:
                _flag, path = line.split("\t", 1)
                self._enqueue(("get", path, dest))
