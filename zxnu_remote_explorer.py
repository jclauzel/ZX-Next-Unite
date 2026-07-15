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
import shutil

from PySide6.QtCore import Qt, QDir, QModelIndex, QMimeData, QUrl, QSize, QTimer
from PySide6.QtGui import QDrag, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QFileSystemModel, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QMessageBox, QPushButton, QStyle, QTreeView, QVBoxLayout,
    QWidget,
)

from zxnu_workers import HdfProgressDialog

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

    def __init__(self, enqueue, local_start_dir=None, log=None, parent=None,
                 drain=None):
        super().__init__(parent)
        self._enqueue_raw = enqueue          # host closure: put one command
        self._drain_raw = drain              # host closure: empty the queue, -> count
        self._log = log or (lambda s: None)
        self._cwd = "/"                      # current Next directory
        self._connected = False
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
        self.local_view.keyPressEvent = self._local_key_press

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
    def _run_op(self, title, enqueue_fn, determinate=True):
        """Run a batch of remote commands as a cancellable, blocking operation.

        ``enqueue_fn`` queues the commands (via _enqueue). The widget is
        disabled and, after a short delay, a modal progress dialog appears; both
        are lifted once every queued command has reported back.
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
        # One listing now that the batch is done (suppressed during the op).
        self.refresh()

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
        self._op_step_done(f"Downloaded {posixpath.basename(remote.rstrip('/')) or remote}")

    def on_put_done(self, ok, remote):
        self._log(f"Uploaded -> {remote}" if ok else f"Upload failed: {remote}")
        if not ok:
            self._cut_fail_head()
        # Only refresh when the file landed in the folder we're looking at, so a
        # recursive folder upload doesn't fire one listing per file.
        if self._in_cwd(remote):
            self.refresh()
        self._op_step_done(f"Uploaded {posixpath.basename(remote.rstrip('/')) or remote}")

    def on_op_done(self, ok, op, path):
        self._log(f"{op} {path}: {'ok' if ok else 'FAILED'}")
        # A failed mkdir usually just means the folder already exists, which does
        # not doom a move; only real transfer failures (put/get) count, and those
        # arrive via put_done(ok=False) / error.
        if self._in_cwd(path):
            self.refresh()
        self._op_step_done(f"{op} {posixpath.basename(path.rstrip('/')) or path}")

    def on_error(self, _msg=None):
        # Any error while a move's transfer is draining means we must not delete
        # its source. It also counts as that command reporting back.
        self._cut_fail_head()
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
    def _add_next_row(self, name, is_dir, size, is_updir=False):
        name_item = QStandardItem(self._dir_icon if is_dir else self._file_icon, name)
        name_item.setData(".." if is_updir else _posix_join(self._cwd, name), RE_PATH_ROLE)
        name_item.setData(bool(is_dir), RE_ISDIR_ROLE)
        size_item = QStandardItem("" if is_dir else _human_size(size))
        self.next_model.appendRow([name_item, size_item])

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
                         lambda: self._enqueue(("mkdir", target)))

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
                for path, _is_dir in entries:
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
    def _get_selected(self):
        entries = self._selected_next_entries()
        if not entries:
            return
        dest = self._local_dir()

        def go():
            for path, _is_dir in entries:
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

    # ==================================================================
    #  local pane
    # ==================================================================
    def set_local_dir(self, path):
        """Public: point the local pane at `path` (e.g. a drive root).

        Used by the host's drive switcher so the Remote Explorer can change
        drive too. Ignored if `path` isn't an existing directory.
        """
        if path and os.path.isdir(path):
            self._set_local_dir(path)

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
        paths = [line.split("\t", 1)[1]
                 for line in bytes(data).decode(errors="replace").splitlines()
                 if "\t" in line]
        if not paths:
            return

        def go():
            for path in paths:
                self._enqueue(("get", path, dest))
            self._log(f"Downloading {len(paths)} item(s) to {dest} …")
        self._run_op("Downloading from the Next…", go)
