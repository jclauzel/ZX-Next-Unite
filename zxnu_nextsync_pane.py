"""zxnu_nextsync_pane.py — the NextSync tab's widget + wiring builder.

Strangler extraction from MainWindow.__init__ (builder-function seam, see
zxnu_zxdb_pane.py): the NextSync tab's construction blob — the classic local
explorer (drive combo / filter / tree + DnD), sync-root row, log window and
its Classic/Pygame toggle, the Remote Explorer / Classic experience selector
tabs, the RemoteExplorerWidget + `-listen` server control block, the NextSync
HTTP bridge state + start/stop plumbing, the sync-mode radio group and the
start/cancel/progress row — now lives here as build_nextsync_pane(host, ...).

The operation layer (nextsync_* transfer/server/context-menu closures defined
earlier in __init__) is injected via keyword-only params, exactly like the
gallery panes inject theirs. Two frame-locals the rest of __init__ still reads
are handed back as host attributes and re-bound to bare locals at the call
site: nextsync_container (the tab-page assembly adds it to the NextSync grid)
and _re_try_send_folder (called from nextsync_start_server when a Remote
Explorer session is live). ``available_drives`` must stay a param (not a
module import): on non-Windows the block appends to the __init__ list without
ever rebinding it first. See CLAUDE.md and the memory
``strangler-extraction-pattern``.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time

from zxnu_i18n import current_ui_language, translate_widget_tree, ui_tr_now

from PySide6 import QtCore
from PySide6.QtCore import (Qt, QTimer, QRect, QDir)
from PySide6.QtGui import (QKeySequence)
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QCheckBox,
    QComboBox, QLineEdit, QHBoxLayout, QVBoxLayout, QProgressBar, QTreeView,
    QFileSystemModel, QGroupBox, QRadioButton, QButtonGroup, QListWidget,
    QTabBar, QStackedWidget, QAbstractItemView, QMenu, QApplication)

from zxnu_http_bridge import NextSyncHttpBridge, QueueBridgeHost
from zxnu_network import detect_local_ipv4
from zxnu_remote_explorer import RemoteExplorerWidget
from zxnu_config import *
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *


def build_nextsync_pane(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    available_drives,
    list_windows_drives,
    set_treeview_properties,
    add_nextsync_log_window,
    apply_file_extension_filter_nextsync,
    nextsync_update_root_drive,
    on_nextsync_file_explorer_path_edited,
    nextsync_on_treeview_context_menu,
    nextsync_on_treeview_double_clicked,
    nextsync_rename_explorer_item,
    nextsync_delete_explorer_item,
    nextsync_import_external_paths,
    nextsync_refresh_explorer,
    nextsync_start_server,
    nextsync_cancel_server_job,
    nextsync_perform_checks_and_prepare_server_start,
    nextsync_hide_start_cancel_buttons,
    nextsync_sync_mode_changed,
    nextsync_slowtransfer_checkbox_statechanged,
    nextsync_create_syncingore_button,
    nextsync_delete_syncingore_button,
    nextsync_delete_syncpoint_button,
    _nextsync_on_set_syncroot_clicked,
    _nextsync_explorer_copy_selection,
    _nextsync_explorer_paste_target_dir,
    _explorer_paste_into_local,
):
    """Build the NextSync tab: classic explorer, log, Remote Explorer, bridge."""
    nextsync_container = QWidget()
    nextsync_container.setLayout(host.nextsync_form)

    host.nextsync_log_and_sync_buttons_container = QWidget()
    host.nextsync_container_log_and_sync_buttons = QVBoxLayout()

    host.nextsync_container_log_and_sync_buttons.setAlignment(Qt.AlignTop)
    # No top margin: the form above already sets the distance from the mode
    # tabs, and a second 9 px here was half of the dead band under them.
    _m = host.nextsync_container_log_and_sync_buttons.contentsMargins()
    host.nextsync_container_log_and_sync_buttons.setContentsMargins(
        _m.left(), 0, _m.right(), _m.bottom())
    host.nextsync_log_and_sync_buttons_container.setLayout(host.nextsync_container_log_and_sync_buttons)


    host.nextsync_fileexplorer_and_buttons_container = QWidget()
    host.nextsync_container_fileexplorer_and_buttons_buttons = QVBoxLayout()

    host.nextsync_container_fileexplorer_and_buttons_buttons.setAlignment(Qt.AlignTop)
    # No margins, the same as the log container it sits beside. Left the
    # style default (9 px on every side), this column indented the Classic
    # sync tree 9 px further in than the drive combo naming it, so the
    # explorer read as offset from its own switcher (reported) — and 9 px
    # lower than the log pane opposite. Pinned explicitly: a QLayout with no
    # parent widget yet reports (0,0,0,0), and the style's 9 px only arrives
    # on setLayout() below, so this has to be set rather than adjusted.
    host.nextsync_container_fileexplorer_and_buttons_buttons.setContentsMargins(
        0, 0, 0, 0)
    host.nextsync_fileexplorer_and_buttons_container.setLayout(host.nextsync_container_fileexplorer_and_buttons_buttons)

    # Add Disk drive selection
    host.nextsync_diskdrive = QComboBox()

    if platform.system() == "Windows":

        available_drives = list_windows_drives()

        for letter in available_drives:
             host.nextsync_diskdrive.addItem(letter)

        host.nextsync_diskdrive.show()

        host.horizontal10.addWidget(host.nextsync_diskdrive)
        host.nextsync_diskdrive.activated.connect(nextsync_update_root_drive)
    else:
        available_drives.append('/')
        host.nextsync_diskdrive.setVisible(False)


    # Add Filter
    host.nextsync_filterlabel = QLabel()
    host.nextsync_filterlabel.setText("Search: ")

    host.horizontal10.addWidget(host.nextsync_filterlabel)

    host.nextsync_filtertext = QLineEdit()
    host.nextsync_filtertext.setPlaceholderText("Filter by name...")
    host.nextsync_filtertext.textChanged.connect(apply_file_extension_filter_nextsync)
    host.nextsync_filtertext.setMinimumWidth(FILTER_TEXT_WIDTH + 400)
    host.nextsync_filtertext.setMaximumWidth(FILTER_TEXT_WIDTH + 400)

    host.horizontal10.addWidget(host.nextsync_filtertext)
    # The trailing stretch the SD Card tab's twin row has always had (its
    # horizontal2). Without it this QHBoxLayout had no stretchable item, so
    # Qt handed every spare pixel to the widgets themselves: the drive combo
    # ballooned across most of the window and the filter box was pushed to
    # the far right, a screen away from the "Search:" label naming it
    # (reported). One stretch pins the three together on the left at their
    # natural sizes and parks the slack on the right.
    host.horizontal10.addStretch(1)


    host.nextsync_form.addRow(host.horizontal10)
    # Row index of the classic drive/filter bar, so the Remote Explorer view
    # can take the whole row OUT of the layout (setRowVisible, Qt 6.4+)
    # rather than merely hiding its widgets: a row emptied by hiding is
    # zero-height but still collects the form's vertical spacing.
    host._nextsync_classic_bar_row = host.nextsync_form.rowCount() - 1

    # The band under the "Remote Explorer / Classic sync" tabs was 30 px of
    # nothing (reported): the tab page's grid spacing, then this form's top
    # margin, then the empty classic-bar row's spacing, then the inner
    # container's top margin — four paddings stacked where one is enough.
    # The mode tabs are the separator, so the content starts right under
    # them, the way the SD Card tab's image row starts under its tab bar.
    _m = host.nextsync_form.contentsMargins()
    host.nextsync_form.setContentsMargins(_m.left(), 0, _m.right(), _m.bottom())

    host.nextsync_treeview = QTreeView()

    host.nextsync_filesystem_model = QFileSystemModel()

    host.nextsync_filesystem_model.setRootPath('/')
    host.nextsync_filesystem_model.setFilter(~QDir.NoDotAndDotDot | QDir.NoDot)
    host.nextsync_filesystem_model.sort(0, Qt.AscendingOrder)


    host.nextsync_model = DotDotFirstProxyModel(recursiveFilteringEnabled = True, filterRole = QFileSystemModel.FileNameRole)
    host.nextsync_model.setSourceModel(host.nextsync_filesystem_model)
    host.nextsync_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)
    host.nextsync_model.setDynamicSortFilter(True)

    host.nextsync_treeview.setModel(host.nextsync_model)
    host.nextsync_treeview.setSortingEnabled(True)
    host.nextsync_treeview.setRootIndex(host.nextsync_model.mapFromSource(host.nextsync_filesystem_model.index(available_drives[0])))
    host.nextsync_model.sort(0, QtCore.Qt.AscendingOrder)

    host.nextsync_treeview.show()
    host.nextsync_treeview.setColumnWidth(0, 250)

    host.nextsync_treeview.doubleClicked.connect(nextsync_on_treeview_double_clicked)
    host.nextsync_treeview.setContextMenuPolicy(Qt.CustomContextMenu)
    host.nextsync_treeview.customContextMenuRequested.connect(nextsync_on_treeview_context_menu)

    def _nextsync_tree_key_press(event):
        # Ctrl+C / Ctrl+X / Ctrl+V copy, cut & paste via the shared clipboard.
        if event.matches(QKeySequence.StandardKey.Copy):
            _nextsync_explorer_copy_selection()
            return
        if event.matches(QKeySequence.StandardKey.Cut):
            _nextsync_explorer_copy_selection(mode="cut")
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            _explorer_paste_into_local(_nextsync_explorer_paste_target_dir(),
                                       nextsync_refresh_explorer, add_nextsync_log_window)
            return
        # Delete key / F2 mirror the context-menu "Delete" / "Rename" actions.
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_F2):
            ix = host.nextsync_treeview.currentIndex()
            if ix.isValid():
                source_ix = host.nextsync_model.mapToSource(ix)
                name = host.nextsync_filesystem_model.fileName(source_ix)
                if name != "..":
                    file_path = host.nextsync_filesystem_model.filePath(source_ix)
                    is_dir = host.nextsync_filesystem_model.isDir(source_ix)
                    if event.key() == Qt.Key.Key_Delete:
                        nextsync_delete_explorer_item(file_path, name, is_dir)
                    else:
                        nextsync_rename_explorer_item(file_path, name, is_dir)
                    return
        QTreeView.keyPressEvent(host.nextsync_treeview, event)

    host.nextsync_treeview.keyPressEvent = _nextsync_tree_key_press

    # --- Drag & drop into the NextSync (classic) local explorer ---------
    # Dropping files/folders from the OS file manager onto the explorer
    # imports (copies) them into the folder the drop lands on (its parent
    # if the item is a file), or the current root when dropped on empty
    # space. Dragging WITHIN the explorer works the same way, mirroring
    # the Remote Explorer's local pane: the dragged file/folder is COPIED
    # into the folder it is dropped on, and dropping it back into its own
    # folder is a no-op (a deliberate duplicate is Copy/Paste's job, never
    # a drag's side effect).
    def _nextsync_drop_target_dir(pos):
        index = host.nextsync_treeview.indexAt(pos)
        if index.isValid():
            source_ix = host.nextsync_model.mapToSource(index)
            if host.nextsync_filesystem_model.fileName(source_ix) != "..":
                path = host.nextsync_filesystem_model.filePath(source_ix)
                if host.nextsync_filesystem_model.isDir(source_ix):
                    return path
                return os.path.dirname(path)
        # Fall back to the directory currently shown at the tree root.
        root_src = host.nextsync_model.mapToSource(host.nextsync_treeview.rootIndex())
        return host.nextsync_filesystem_model.filePath(root_src)

    def _nextsync_drag_enter(event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _nextsync_drag_move(event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _nextsync_drop(event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile() and u.toLocalFile()]
        if not paths:
            event.ignore()
            return
        dest_dir = _nextsync_drop_target_dir(event.position().toPoint())
        if event.source() is host.nextsync_treeview:
            # Intra-explorer drag: dropping an item into the folder it is
            # already in is a no-op, not a "-(copy)" duplicate. (Importing
            # a folder into itself is guarded inside the import helper.)
            dest_abs = os.path.normcase(os.path.abspath(dest_dir))
            paths = [p for p in paths
                     if os.path.normcase(os.path.abspath(
                         os.path.dirname(p.rstrip("/\\")))) != dest_abs]
            if not paths:
                event.ignore()
                return
        event.acceptProposedAction()
        nextsync_import_external_paths(paths, dest_dir)

    host.nextsync_treeview.setAcceptDrops(True)
    host.nextsync_treeview.setDragEnabled(True)
    host.nextsync_treeview.setDragDropMode(QAbstractItemView.DragDrop)
    # A drag within the explorer proposes a COPY (the copy is performed by
    # _nextsync_drop); without this Qt would propose an internal move for
    # same-view drags. Same setup as the Remote Explorer's local pane.
    host.nextsync_treeview.setDefaultDropAction(Qt.CopyAction)
    host.nextsync_treeview.setDropIndicatorShown(True)
    host.nextsync_treeview.dragEnterEvent = _nextsync_drag_enter
    host.nextsync_treeview.dragMoveEvent = _nextsync_drag_move
    host.nextsync_treeview.dropEvent = _nextsync_drop

    set_treeview_properties()

    host.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(host.nextsync_treeview)

    # Sync-root box + "Set current folder as new sync root folder" button.
    # The box shows the committed Classic sync root; it no longer follows
    # clicks in the explorer above (navigating and choosing the sync root
    # are separate actions). The button appears only while browsing a
    # different folder than the sync root and asks for confirmation before
    # committing.

    host.nextsync_file_explorer_path = QLineEdit()
    host.nextsync_file_explorer_path.setText("-")
    host.nextsync_file_explorer_path.setPlaceholderText("Sync root folder...")
    host.nextsync_file_explorer_path.setToolTip(
        "Sync root: the folder the Classic NextSync server syncs from.\n"
        "Type a folder path here, or navigate the explorer above and press\n"
        "'Set current folder as new sync root folder'.")
    host.nextsync_file_explorer_path.editingFinished.connect(on_nextsync_file_explorer_path_edited)

    host.nextsync_set_syncroot_button = QPushButton("Set current folder as new sync root folder", host)
    host.nextsync_set_syncroot_button.setToolTip(
        "Make the folder currently shown in the explorer above the new sync root.")
    host.nextsync_set_syncroot_button.clicked.connect(_nextsync_on_set_syncroot_clicked)
    host.nextsync_set_syncroot_button.setVisible(False)

    host.nextsync_syncroot_row = QHBoxLayout()
    host.nextsync_syncroot_row.setContentsMargins(0, 0, 0, 0)
    host.nextsync_syncroot_row.addWidget(host.nextsync_file_explorer_path, 1)
    host.nextsync_syncroot_row.addWidget(host.nextsync_set_syncroot_button)

    # Green "pick me" pulse while the set-sync-root offer is on screen — the
    # same treatment as the Remote Explorer start-button pulses below, so the
    # suggested next step is always the one that glows. Driven by
    # _nextsync_update_set_syncroot_button in the monolith, which owns the
    # button's visibility.
    host._nextsync_syncroot_pulse_timer = None

    def _nextsync_syncroot_pulse_set(on):
        if not on:
            if host._nextsync_syncroot_pulse_timer is not None:
                host._nextsync_syncroot_pulse_timer.stop()
                host._nextsync_syncroot_pulse_timer = None
            try:
                host.nextsync_set_syncroot_button.setStyleSheet("")
            except RuntimeError:
                pass
            return
        if host._nextsync_syncroot_pulse_timer is not None:
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
                host.nextsync_set_syncroot_button.setStyleSheet(
                    "QPushButton { color: %s; font-weight: bold;"
                    " padding: 4px 10px; border-radius: 6px;"
                    " background-color: rgba(%d,%d,%d,%d);"
                    " border: 1px solid rgba(%d,%d,%d,%d); }" % (
                        fg, r, g, b, a, r, g, b, min(a + 60, 255)))
            except RuntimeError:
                pass
        timer = QTimer(host)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        host._nextsync_syncroot_pulse_timer = timer
    host._nextsync_syncroot_pulse_set = _nextsync_syncroot_pulse_set
    host.nextsync_container_fileexplorer_and_buttons_buttons.addLayout(host.nextsync_syncroot_row)


    host.horizontal12.addWidget(host.nextsync_fileexplorer_and_buttons_container)


    host.nextsync_button_create_syncignore = QPushButton("Create SyncIgnore File", host)
    host.nextsync_button_create_syncignore.setText("Create SyncIgnore File")
    host.nextsync_button_create_syncignore.clicked.connect(nextsync_create_syncingore_button)
    host.nextsync_button_create_syncignore.setVisible(False)

    host.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(host.nextsync_button_create_syncignore)

    host.nextsync_button_delete_syncignore = QPushButton("Delete SyncIgnore File", host)
    host.nextsync_button_delete_syncignore.setText("Delete SyncIgnore File")
    host.nextsync_button_delete_syncignore.clicked.connect(nextsync_delete_syncingore_button)
    host.nextsync_button_delete_syncignore.setVisible(False)

    host.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(host.nextsync_button_delete_syncignore)

    host.nextsync_button_delete_syncpointfile = QPushButton("Delete SyncPoint File", host)
    host.nextsync_button_delete_syncpointfile.setText("Delete SyncPoint File")
    host.nextsync_button_delete_syncpointfile.clicked.connect(nextsync_delete_syncpoint_button)
    host.nextsync_button_delete_syncpointfile.setVisible(False)

    host.nextsync_container_fileexplorer_and_buttons_buttons.addWidget(host.nextsync_button_delete_syncpointfile)

    host.nextsync_form.addRow(host.horizontal12)


    # Add NextSync Log Window

    host.nextsync_log = QListWidget(host)
    host.nextsync_log.setMinimumHeight(NEXTSYNC_UI_HEIGTH)
    #self.nextsync_log.setMaximumHeight(NEXTSYNC_UI_HEIGTH)

    # Same welcome banner the SD Card log opens with, as the first line.
    add_nextsync_log_window(WELCOME_BANNER)

    # Classic / Pygame toggle for the log window (mirrors the Unite! tab).
    # Pygame is optional: the button stays disabled with an install hint when
    # pygame-ce is missing. When on, the log becomes a retro 8-bit display —
    # the animated $/£/€ starfield with green Consolas text (see
    # zxnu_pygame.RetroLogWidget).
    host._nextsync_retro_log = None
    host._nextsync_pygame_on = False
    if not hasattr(host, "_nextsync_pygame_anim"):
        host._nextsync_pygame_anim = True

    host.nextsync_pygame_button = QPushButton("🎮 Retro")
    host.nextsync_pygame_button.setCheckable(True)
    host.nextsync_pygame_button.setToolTip(
        "Switch the NextSync log window to a retro 8-bit pygame display:\n"
        "an animated starfield with green Consolas text.\n"
        "Requires the optional 'pygame-ce' package.")
    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_pygame_button)

    # NextSync experience selector. Two tabs replace the old checkable
    # "Remote Explorer" toggle button:
    #   index 0 "Remote Explorer" — flips the log window into a dual-pane
    #       local <-> Next file explorer driven by ".sync5 -listen" (built
    #       lazily the first time it is shown).
    #   index 1 "Classic" — the traditional one-way NextSync push (PC -> Next).
    # The chosen tab is persisted (SETTING_NEXTSYNC_REMOTE_EXPLORER, "true"
    # for Remote Explorer) so the NextSync tab reopens in the same experience
    # next launch. currentChanged drives the same show/hide + persist logic
    # the toggle button used to (connected below, once the mode widgets exist).
    host.nextsync_mode_tabs = QTabBar(host)
    host.nextsync_mode_tabs.setExpanding(False)
    host.nextsync_mode_tabs.addTab("🗂 Remote Explorer")   # index 0
    host.nextsync_mode_tabs.addTab("🔄 Classic sync")       # index 1
    host.nextsync_mode_tabs.setToolTip(
        "Remote Explorer: a dual-pane file explorer (local <-> Next).\n"
        "Run '.sync5 -L' (-l or -listen) on your Next, then transfer files with ->: / :<-,\n"
        "drag & drop, or the right-click menu (New Folder / Rename / Delete).\n"
        "Classic: the traditional one-way NextSync push (PC -> Next).")
    # Default to the Classic tab (matches the historical default and the
    # controls built below); the saved preference restores Remote Explorer
    # at startup. Set before currentChanged is connected so it does not fire
    # the handler while the mode widgets are still being constructed.
    host.nextsync_mode_tabs.setCurrentIndex(1)
    # Placed at the very top of the NextSync tab page (added to
    # grid_tab_nextsync below), so it sits right under the main tab strip
    # and spans the full width, rather than being buried in the log column.

    # Stack: page 0 = the classic list log, page 1 = the retro pygame log
    # (built lazily the first time the user switches it on).
    host.nextsync_log_stack = QStackedWidget(host)
    host.nextsync_log_stack.setMinimumHeight(NEXTSYNC_UI_HEIGTH)
    host.nextsync_log_stack.addWidget(host.nextsync_log)
    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_log_stack)

    # --- remote file explorer (dual-pane) ------------------------------
    host._re_widget = None
    host._re_thread = None
    host._re_stop = None
    host._re_queue = None
    host._re_sig = None
    host._re_mini_log = None       # Remote Explorer mini log (list side)
    host._re_mini_retro = None     # ...and its lazy retro sibling
    host._re_mini_stack = None
    host._re_container = None      # RE widget + mini log, one stack entry
    host._re_running = False
    host._re_pulse_timer = None            # green "running" pulse (play label)
    host._re_start_btn_pulse_timer = None  # yellow "start me" pulse (start button)
    # The Remote Explorer's chosen local "sync root". "" until the user picks
    # a folder in its left file explorer; the 'Start Remote Explorer
    # NextSync server' button
    # stays disabled (with a prompt) until then. Mirrored from the widget via
    # _re_on_sync_root_changed.
    host._re_sync_root = ""

    def _re_enqueue(cmd):
        if host._re_queue is not None:
            host._re_queue.put(cmd)

    def _re_drain():
        # Remove every still-queued command (used by the explorer's Cancel to
        # stop after the in-flight transfer). Returns how many were dropped.
        n = 0
        q = host._re_queue
        if q is not None:
            while True:
                try:
                    q.get_nowait()
                    n += 1
                except Exception:   # queue.Empty (or anything) -> stop
                    break
        return n

    # ---- NextSync HTTP bridge (Settings → "Enable NextSync HTTP bridge").
    # A Flask web server (zxnu_http_bridge) that republishes the -listen
    # session as HTTP routes, so a Next running the .http dot command (or
    # curl / a browser) can drive the connected Next's file system. The
    # bridge's commands ride the same worker queue as the Remote Explorer,
    # carrying a BridgeReply the worker fills INSTEAD of emitting signals —
    # so bridge traffic never touches the Remote Explorer panes.
    host._re_bridge = None
    # Live session state for the bridge's /status route, maintained from
    # the worker's signals with DirectConnection (plain field writes from
    # the worker thread — no Qt event loop involvement needed).
    host._re_bridge_state = {"connected": False, "current": "", "drives": None}
    # The worker's control surface, ONE dict for the whole app run: the
    # worker seeds its sid counter from control['seq'] (so sids never
    # restart across the routine last-Next-leaves/relisten cycle — a stale
    # HTTP session id must mean "gone", never "another machine") and
    # installs 'roster'/'enqueue_to'/'max_peers' for the bridge's
    # session-targeted routes each time it starts.
    host._re_control = {"seq": 0}

    def _re_bridge_make_cmd(op, a1, a2, reply):
        # Canonical bridge op -> the worker's command-tuple dialect, with
        # the reply sink riding as the LAST element.
        if op == "ls":
            return ("ls", a1, reply)
        if op == "get":
            return ("get", a1, a2, reply)          # a2 = bridge temp dir
        if op == "put":
            return ("put", a2, a1, reply)          # worker: (local, remote)
        if op in ("mkdir", "rmdir", "rm", "rmtree"):
            return (op, a1, reply)
        if op == "ren":
            return ("rename", a1, a2, reply)
        if op == "rcpy":
            return ("rcpy", a1, a2, reply)
        if op == "rfsize":
            return ("fsize", a1, reply)
        if op == "free":
            return ("free", a1, reply)
        if op == "drives":
            return ("drives", reply)
        if op == "version":
            return ("version", reply)
        if op == "crc":
            return ("crc", a1, reply)
        if op == "forceexit":
            # The dot leaves -listen and exits to BASIC; ZX Next Remote
            # 0.9.47+ reads the marker and exits its application too
            # (older builds just leave the session, as they always did).
            return ("quit_app", reply)
        return None

    def _re_bridge_enqueue(cmd):
        q = host._re_queue
        if q is None or not host._re_running:
            return False
        q.put(cmd)
        return True

    def _re_bridge_session_state():
        st = host._re_bridge_state
        return {"listening": bool(host._re_running),
                "connected": bool(st["connected"]),
                "current": st["current"] or "",
                "drives": list(st["drives"]) if st["drives"] else None}

    def _re_bridge_sessions():
        # Roster snapshot for GET /sessions: worker sids + addresses,
        # decorated with the same address-keyed friendly names the Remote
        # Explorer combo shows. Reading the name map from an HTTP thread is
        # safe: it is a plain dict-backed config read, never written here.
        roster = host._re_control.get('roster')
        active, plist = roster() if roster is not None else (None, [])
        if not host._re_running:
            active, plist = None, []
        return (active,
                [(sid, addr, _re_machine_name_for(addr) or "")
                 for sid, addr in plist],
                int(host._re_control.get('max_peers', 4)))

    def _re_emulator_launchers():
        # The Remote Explorer's left-hand strip. The rule itself lives in
        # zxnu_workers.emulator_launch_entries, shared with the SD Card
        # tab's identical strip so the two can never disagree.
        return emulator_launch_entries(host)

    def _re_refresh_emulators():
        # Detection is not a signal in this app: it changes when the
        # startup scan lands, when an itch.io install/uninstall finishes,
        # when MAME is installed in-app and when the Flatpak toggle flips.
        # Each of those calls this; the widget also refreshes on show, so
        # a missed notification costs a stale strip only while it is
        # hidden. Safe before the (lazily built) widget exists.
        _w = host._re_widget
        if _w is None:
            return
        try:
            _w.refresh_emulator_strip()
        except RuntimeError:
            pass                      # widget torn down mid-shutdown
    host._re_refresh_emulators = _re_refresh_emulators

    def _re_bridge_enqueue_to(sid, cmd):
        # Targeted delivery for ?session=N — the session's own queue, the
        # baton untouched. The worker's closure validates sid under its
        # roster lock; False (gone / not running) maps to HTTP 410.
        fn = host._re_control.get('enqueue_to')
        if fn is None or not host._re_running:
            return False
        return bool(fn(sid, cmd))

    def _nextsync_http_bridge_start():
        if host._re_bridge is not None and host._re_bridge.running:
            return
        try:
            port = int(configuration_dictionary.get(
                SETTING_NEXTSYNC_HTTP_PORT) or 80)
        except (TypeError, ValueError):
            port = 80
        try:
            conn_limit = int(configuration_dictionary.get(
                SETTING_NEXTSYNC_HTTP_CONNECTION_LIMIT) or 1)
        except (TypeError, ValueError):
            conn_limit = 1
        # Optional bearer-token protection: only enforce when the toggle is
        # on AND a token is actually persisted.
        _token_on = configuration_dictionary.get(
            SETTING_NEXTSYNC_HTTP_TOKEN_ENABLED, "").strip().lower() in (
                "true", "1", "yes", "on")
        _token = (configuration_dictionary.get(
            SETTING_NEXTSYNC_HTTP_TOKEN) or "").strip()
        host._re_bridge = NextSyncHttpBridge(
            QueueBridgeHost(_re_bridge_enqueue, _re_bridge_make_cmd,
                            _re_bridge_session_state,
                            sessions=_re_bridge_sessions,
                            enqueue_to=_re_bridge_enqueue_to),
            port=port, connection_limit=conn_limit,
            auth_token=_token if (_token_on and _token) else None,
            verbose=(configuration_dictionary.get(
                SETTING_NEXTSYNC_HTTP_VERBOSE, "").strip().lower()
                in ("true", "1", "yes", "on")),
            log=lambda s: add_nextsync_log_window(str(s)))
        ok, err = host._re_bridge.start()
        if ok:
            add_nextsync_log_window(
                # The route list is an API contract, not prose: it stays
                # verbatim outside the translated sentence.
                ui_tr_now("NextSync HTTP bridge listening on port {port}"
                          ).format(port=port)
                + " (routes: /status /sessions /drives /free /ls /get "
                  "/put /mkdir /rmdir /rmtree /rm /ren /rcpy /rfsize /sum "
                  "/forceexit)")
            if _token_on and _token:
                add_nextsync_log_window(ui_tr_now(
                    "NextSync HTTP bridge: bearer-token protection is ON "
                    "(requests must carry the {header} header; others get "
                    "HTTP 401)").format(header=NEXTSYNC_BRIDGE_TOKEN_HEADER))
            host._show_toast(
                "NextSync HTTP bridge started",
                ui_tr_now(
                    "Serving on port {port}. A Next with the .http dot "
                    "command (or curl) can now drive the Next connected in "
                    "'.sync5 -L' (-l or -listen).").format(port=port),
                variant="green", duration_ms=6000)
        elif host._re_bridge.port_in_use:
            # Something (IIS? another server instance?) already owns the
            # port: a targeted red error, exactly what happened and that
            # nothing was started.
            host._re_bridge = None
            add_nextsync_log_window(ui_tr_now(
                "NextSync HTTP bridge NOT started: {error}").format(error=err))
            host._show_toast(
                "NextSync HTTP bridge not started",
                ui_tr_now(
                    "You have specified to start the flask integration "
                    "server but port {port} is already in use, the web "
                    "server has not been started.").format(port=port),
                variant="red", duration_ms=12000)
        else:
            host._re_bridge = None
            add_nextsync_log_window(ui_tr_now(
                "NextSync HTTP bridge NOT started: {error}").format(error=err))
            host._show_toast("NextSync HTTP bridge not started", err,
                             variant="yellow", duration_ms=12000)

    def _nextsync_http_bridge_stop():
        bridge, host._re_bridge = host._re_bridge, None
        if bridge is not None:
            bridge.stop()
            add_nextsync_log_window(ui_tr_now("NextSync HTTP bridge stopped."))
    host._nextsync_http_bridge_start = _nextsync_http_bridge_start
    host._nextsync_http_bridge_stop = _nextsync_http_bridge_stop

    def _re_apply_item_colors():
        # Push the SD Card Utility's live item colours into the Remote
        # Explorer so its two panes are tinted the same way as the image tree
        # (dir/file name, type, size, up-dir). Safe to call before the widget
        # exists (lazy build) — it's a no-op then, and the build applies the
        # current colours itself.
        widget = getattr(host, "_re_widget", None)
        if widget is None:
            return
        try:
            widget.set_item_colors({
                "up_directory": host.img_color_up_directory,
                "dir_name":     host.img_color_dir_name,
                "dir_type":     host.img_color_dir_type,
                "file_name":    host.img_color_file_name,
                "file_ext":     host.img_color_file_ext,
                "file_size":    host.img_color_file_size,
                "general_text": host.img_color_general_text,
            })
            # The idle host/IP panel follows the retro (Consolas) log
            # COLOUR setting only; its font size stays at the widget's
            # normal 10pt — inheriting the log font size blew the block up
            # until it overlapped the pane's header/footer (reverted).
            col = (configuration_dictionary.get(SETTING_COLOR_RETRO_LOG)
                   or "").strip() or "#33ff33"
            widget.set_idle_details_style(col)
        except Exception:
            pass
    host._re_apply_item_colors = _re_apply_item_colors

    # 'Start Remote Explorer NextSync server' button text shown once a sync
    # root is chosen.
    _RE_START_TEXT = "▶ Start Remote Explorer NextSync server"
    _RE_NO_ROOT_TEXT = "Please set a sync root folder on the left local file explorer"

    def _re_update_start_button():
        # Reflect the current state on the Remote Explorer's server button
        # (pulses only run while the Remote Explorer view is in front):
        #   running        -> "Stop", clickable, no pulse
        #   sync root set  -> "Start", clickable, yellow "start me" pulse
        #   no sync root   -> prompt text, disabled, green "pick one" pulse
        btn = host.nextsync_re_start_button
        if host._re_running:
            _re_stop_startbtn_pulse()
            btn.setEnabled(True)
            btn.setText("⏹ Stop Remote Explorer NextSync server")
            return
        in_view = (host.nextsync_mode_tabs.currentIndex() == 0)
        if getattr(host, "_re_sync_root", ""):
            btn.setEnabled(True)
            btn.setText(_RE_START_TEXT)
            _re_start_startbtn_pulse("yellow") if in_view else _re_stop_startbtn_pulse()
        else:
            btn.setEnabled(False)
            btn.setText(_RE_NO_ROOT_TEXT)
            _re_start_startbtn_pulse("green") if in_view else _re_stop_startbtn_pulse()
    def _re_update_start_button_and_status():
        _re_update_start_button_inner()
        # Mirror the same state onto the Remote Explorer's "Next:" label
        # (only shown while disconnected) so it never claims to be waiting
        # for .sync5 before the server is even running.
        if host._re_widget is not None:
            host._re_widget.refresh_idle_status()
    _re_update_start_button_inner = _re_update_start_button
    _re_update_start_button = _re_update_start_button_and_status
    host._re_update_start_button = _re_update_start_button

    def _re_idle_status():
        """The "Next:" pane label while DISCONNECTED, mirroring the start
        button's three states."""
        if host._re_running:
            return "Next: (waiting for .sync5 -L (-l or -listen) …)"
        if not getattr(host, "_re_sync_root", ""):
            return "Next: Select a sync root folder"
        return "Next: Start NextSync server"

    # Cache the host/IP block briefly: refresh_idle_status fires on every
    # start-button state churn and the (time-bounded) hostname resolution
    # is not free. Addresses can change (Wi-Fi roaming), hence the expiry.
    _re_ip_info_cache = {"t": 0.0, "text": ""}

    def _re_idle_details():
        """Multi-line host/IP block for the EMPTY Next pane while
        disconnected — the same information the Classic sync log prints,
        because the server address to type into '.sync5' on the Next is
        exactly what the user needs while setting the link up."""
        now = time.monotonic()
        if now - _re_ip_info_cache["t"] > 30 or not _re_ip_info_cache["text"]:
            try:
                hostname, _aliases, ips, primary = detect_local_ipv4()
            except Exception:
                return ""
            lines = ["The Next's files will appear here.",
                     "Run '.sync5 -L' (-l or -listen) on your Next "
                     "to connect.", ""]
            if hostname:
                lines += ["Running on host:", f"    {hostname}"]
            if ips:
                lines.append("IP addresses:")
                lines += [f"    {x}" for x in ips]
            if primary:
                lines += ["Primary IP:", f"    {primary}"]
            if not (ips or primary):
                lines += ["No network detected — connect to Wi-Fi/Ethernet",
                          "to see the address your Next should sync to."]
            else:
                lines += ["",
                          "The first '.sync5' run asks which server to talk "
                          "to — give it the",
                          "Primary IP (or whichever address is on the same "
                          "network as the Next)."]
                addr = primary or next(
                    (x for x in ips if not x.startswith("127")), None)
                if addr:
                    lines += ["",
                              "Example — on the Next, type this once to save "
                              "the address:",
                              f"    .sync5 {addr}",
                              "then start the remote session any time with:",
                              "    .sync5 -L   (-l or -listen)",
                              "or a ZX Next Remote listener."]
            _re_ip_info_cache["text"] = "\n".join(lines)
            _re_ip_info_cache["t"] = now
        return _re_ip_info_cache["text"]

    def _re_on_sync_root_changed(root):
        # The widget reports the user picked (or changed) the local sync root.
        host._re_sync_root = (root or "").strip()
        if host._re_sync_root:
            try:
                configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = host._re_sync_root
                save_configuration_file()
            except Exception:
                pass
        _re_update_start_button()

    def _re_on_remote_cwd_changed(path, addr=None):
        # The widget reports the Next-side folder it's now showing. Persist it
        # so the next (re)connect jumps straight back to it (fires only when
        # the folder — or the machine — actually changes, so this stays
        # cheap). Since 9.5.14 the report carries the active peer's ADDRESS:
        # each machine gets its own entry in a JSON map, so the Next that
        # lived in /A and the N-Go that lived in /SYS each come back to
        # their own folder (a vanished folder fails its first listing and
        # the widget drops to root by itself). The generic key stays as the
        # fallback for machines never seen before.
        try:
            configuration_dictionary[SETTING_NEXTSYNC_REMOTE_CWD] = (path or "/")
            if addr:
                try:
                    _map = json.loads(str(configuration_dictionary.get(
                        SETTING_RE_REMOTE_CWDS, "") or "{}"))
                    if not isinstance(_map, dict):
                        _map = {}
                except (ValueError, TypeError):
                    _map = {}
                _map.pop(str(addr), None)      # re-insert = newest
                _map[str(addr)] = (path or "/")
                while len(_map) > 24:          # keep the cfg tidy: oldest out
                    _map.pop(next(iter(_map)))
                configuration_dictionary[SETTING_RE_REMOTE_CWDS] = (
                    json.dumps(_map, separators=(",", ":")))
            save_configuration_file()
        except Exception:
            pass

    def _re_remote_cwd_for(addr):
        # The restore half: this machine's remembered folder, or None so the
        # widget falls back to the generic last-folder.
        try:
            _map = json.loads(str(configuration_dictionary.get(
                SETTING_RE_REMOTE_CWDS, "") or "{}"))
            _v = _map.get(str(addr), "") if isinstance(_map, dict) else ""
            return _v if isinstance(_v, str) and _v else None
        except (ValueError, TypeError):
            return None

    def _re_machine_name_for(addr):
        # The machine combo's friendly name for an address (9.5.18), or
        # None — same JSON-map shape as the per-machine folders above.
        try:
            _map = json.loads(str(configuration_dictionary.get(
                SETTING_RE_MACHINE_NAMES, "") or "{}"))
            _v = _map.get(str(addr), "") if isinstance(_map, dict) else ""
            return _v if isinstance(_v, str) and _v else None
        except (ValueError, TypeError):
            return None

    def _re_on_machine_name_changed(addr, name):
        # The ✎ button's report: remember (or forget, on empty) the name
        # for this address. Mirrors the per-machine folder map: re-insert
        # = newest, capped so the cfg stays tidy.
        if not addr:
            return
        try:
            try:
                _map = json.loads(str(configuration_dictionary.get(
                    SETTING_RE_MACHINE_NAMES, "") or "{}"))
                if not isinstance(_map, dict):
                    _map = {}
            except (ValueError, TypeError):
                _map = {}
            _map.pop(str(addr), None)
            if name:
                _map[str(addr)] = str(name)
                while len(_map) > 24:          # oldest out
                    _map.pop(next(iter(_map)))
            configuration_dictionary[SETTING_RE_MACHINE_NAMES] = (
                json.dumps(_map, separators=(",", ":")))
            save_configuration_file()
        except Exception:
            pass

    def _re_machine_color_for(addr):
        # The machine's picked tint for an address (9.5.27) as "#rrggbb",
        # or None - the exact twin of the name map above, deliberately a
        # SEPARATE cfg key: the name map's values are plain strings and
        # every cfg already in the field holds them that way.
        try:
            _map = json.loads(str(configuration_dictionary.get(
                SETTING_RE_MACHINE_COLORS, "") or "{}"))
            _v = _map.get(str(addr), "") if isinstance(_map, dict) else ""
            return _v if isinstance(_v, str) and _v else None
        except (ValueError, TypeError):
            return None

    def _re_on_machine_color_changed(addr, color):
        # The colour editor's report: remember (or forget, on empty) the
        # tint for this address. Same LRU cap as the names, so the two maps
        # age out together rather than one keeping ghosts the other dropped.
        if not addr:
            return
        try:
            try:
                _map = json.loads(str(configuration_dictionary.get(
                    SETTING_RE_MACHINE_COLORS, "") or "{}"))
                if not isinstance(_map, dict):
                    _map = {}
            except (ValueError, TypeError):
                _map = {}
            _map.pop(str(addr), None)
            if color:
                _map[str(addr)] = str(color)
                while len(_map) > 24:          # oldest out
                    _map.pop(next(iter(_map)))
            configuration_dictionary[SETTING_RE_MACHINE_COLORS] = (
                json.dumps(_map, separators=(",", ":")))
            save_configuration_file()
        except Exception:
            pass

    def _re_on_sort_changed(which, value):
        # The widget reports a new column sort ("<key>:<asc|desc>") for one of
        # its panes; persist it so both panes reopen sorted the same way.
        key = (SETTING_NEXTSYNC_RE_LOCAL_SORT if which == "local"
               else SETTING_NEXTSYNC_RE_NEXT_SORT)
        try:
            configuration_dictionary[key] = value
            save_configuration_file()
        except Exception:
            pass

    # splitterMoved fires on every mouse-move of a drag, and the cfg writer
    # rewrites the whole file each time - so the dictionary is updated at
    # once and the FILE once the drag pauses (review, 9.7.2). A quit in
    # that window loses nothing: every quit path closes the window, and
    # the closeEvent save writes the dictionary.
    _re_splitter_flush = QTimer(host)
    _re_splitter_flush.setSingleShot(True)
    _re_splitter_flush.setInterval(300)
    _re_splitter_flush.timeout.connect(save_configuration_file)

    def _re_on_splitter_moved(value):
        # The widget reports its local ⇄ Next pane widths ("left,right" px)
        # on every drag; persist them so the split reopens where it was.
        try:
            configuration_dictionary[SETTING_NEXTSYNC_RE_SPLITTER] = value
            _re_splitter_flush.start()
        except Exception:
            pass

    def _re_on_zxnr_update_path_changed(path):
        # The widget reports the full Next-side path a ZX Next Remote
        # self-update just swapped; persist it so the next update's
        # confirm dialog opens on what worked last time.
        try:
            configuration_dictionary[SETTING_ZXNR_UPDATE_PATH] = path or ""
            save_configuration_file()
        except Exception:
            pass

    def _re_on_extra_drives_changed(letters):
        # The widget reports the user-declared extra Next drives (e.g. "DE"
        # for additional SD readers); persist so they reappear next session.
        try:
            configuration_dictionary[SETTING_NEXTSYNC_EXTRA_DRIVES] = letters or ""
            save_configuration_file()
        except Exception:
            pass

    def _re_mini_sync_mode():
        # Keep the Remote Explorer's mini log on the same Classic/Retro
        # side as the big log window. The retro sibling is built lazily
        # (pygame only spins up if the user actually uses Retro mode) and
        # seeded from the mini list, newest-first source read bottom-up.
        stack = getattr(host, "_re_mini_stack", None)
        if stack is None:
            return
        if getattr(host, "_nextsync_pygame_on", False):
            if host._re_mini_retro is None:
                try:
                    from zxnu_pygame import RetroLogWidget
                    mini_r = RetroLogWidget(
                        scrollable=True, follow_tail=True, context_copy=True,
                        font_px=getattr(host, "_retro_log_font_size",
                                        DEFAULT_RETRO_LOG_FONT_SIZE))
                    mini_r.set_font_step_cb(
                        lambda d: host._step_retro_log_font(d))
                    try:
                        mini_r.enable_background(
                            getattr(host, "_nextsync_pygame_anim", True))
                        mini_r.set_text_color(
                            qcolor_to_hex(host.img_color_retro_log))
                    except Exception:
                        pass
                    for i in range(host._re_mini_log.count() - 1, -1, -1):
                        mini_r.append(host._re_mini_log.item(i).text())
                    host._re_mini_retro = mini_r
                    stack.addWidget(mini_r)
                except Exception:
                    logging.exception("Remote Explorer mini retro log failed")
                    return                    # pygame missing: stay classic
            stack.setCurrentWidget(host._re_mini_retro)
            host._re_mini_retro.start()
        else:
            if getattr(host, "_re_mini_retro", None) is not None:
                host._re_mini_retro.stop()
            stack.setCurrentWidget(host._re_mini_log)

    def _nextsync_on_dot_update(ok, message):
        # The ("update_dot", …) macro's one terminal outcome, success or
        # failure: into the NextSync log (the durable record — progress
        # lines already ride sig.log into the same window) AND a toast so
        # the verdict is seen from whichever tab is in front. The body is
        # already translated at its emit site in zxnu_workers; the static
        # title goes through the _show_toast chokepoint like every other.
        add_nextsync_log_window(str(message))
        host._show_toast("Remote .sync5 update", str(message),
                         variant=("green" if ok else "red"),
                         duration_ms=12000)

    def _nextsync_on_put_verify_failed(message):
        # The verify-after-put verdict (9.7.3): RED in the NextSync log (the
        # durable record - the Classic list and the RE mini list colour the
        # item, the retro mirrors print it plain) AND a red toast, whatever
        # tab is in front. The message arrives already translated (emit-site
        # ui_tr_now in zxnu_workers). The widget's own on_put_done(False)
        # follows in emission order and adds "Upload failed: <name>" to the
        # batch's end-of-op summary toast; this one outlives it (12 s vs
        # 9 s) since every toast shares the same corner.
        add_nextsync_log_window(str(message), color=FONT_RED)
        host._show_toast("❌  NextSync CRC-32 verification failed",
                         str(message), variant="red", duration_ms=12000)

    def _nextsync_build_remote_explorer():
        if host._re_widget is not None:
            return host._re_widget
        start_dir = configuration_dictionary.get(SETTING_NEXTSYNC_EXPLORERPATH) or None
        remote_cwd = configuration_dictionary.get(SETTING_NEXTSYNC_REMOTE_CWD) or None
        local_sort = configuration_dictionary.get(SETTING_NEXTSYNC_RE_LOCAL_SORT) or None
        next_sort = configuration_dictionary.get(SETTING_NEXTSYNC_RE_NEXT_SORT) or None
        extra_drives = configuration_dictionary.get(SETTING_NEXTSYNC_EXTRA_DRIVES) or ""
        widget = RemoteExplorerWidget(
            _re_enqueue, local_start_dir=start_dir,
            log=lambda s: add_nextsync_log_window(str(s)),
            drain=_re_drain, on_sync_root_changed=_re_on_sync_root_changed,
            remote_start_dir=remote_cwd,
            on_remote_cwd_changed=_re_on_remote_cwd_changed,
            remote_cwd_for=_re_remote_cwd_for,
            machine_name_for=_re_machine_name_for,
            on_machine_name_changed=_re_on_machine_name_changed,
            machine_color_for=_re_machine_color_for,
            on_machine_color_changed=_re_on_machine_color_changed,
            # The session strip's right-click Disconnect targets the machine
            # that was clicked, which may not be the one holding the baton -
            # so it needs the same per-session queue the HTTP bridge's
            # ?session=N routes use, not the shared one.
            enqueue_to=_re_bridge_enqueue_to,
            emulator_launchers=_re_emulator_launchers,
            # Right-click on a strip tab also offers the remembered disk
            # images that are writable right now (9.6.2) - the same list
            # and the same effect as on the SD Card tab.
            emulator_images=(lambda: host.writable_image_choices()),
            on_emulator_image_picked=(
                lambda path: host.select_emulator_image(path)),
            # Per-emulator colour (9.6.0): the host owns ONE map, so a
            # colour picked on this strip is the colour the SD Card tab's
            # strip and its Launch buttons wear too.
            emulator_color_for=lambda n: host.emulator_color_for(n),
            # A hover on a greyed strip tab re-probes that emulator's image
            # (9.7.2); looked up at call time, the closure is installed by
            # zxnu_emulator_ops after this pane is wired.
            on_emulator_recheck=lambda n: getattr(
                host, "_recheck_emulator_launchability",
                lambda *_a: False)(n),
            # The connect-time update offer (9.7.2): a 10-second yellow
            # toast with an "Update now" button; the Settings toggle is
            # read per connection, so a change needs no restart.
            update_prompt_enabled=lambda: (configuration_dictionary.get(
                SETTING_RE_UPDATE_PROMPT, "") or "").strip().lower()
                not in ("false", "0", "no"),
            on_update_prompt=lambda body, accept: host._show_toast(
                "⚠  Update available for this Next", body,
                variant="yellow", duration_ms=10000,
                action=("Update now", accept)),
            on_emulator_color_changed=lambda n, c: host.set_emulator_color(n, c),
            # The dotN build behind the session tabs' "Update .sync5"
            # action: resolved by zxnu_emulator_ops (source checkout, or
            # this release's own 'sync5' asset) on CLICK only, and on a
            # worker thread (the widget's _Sync5ResolveTask — the resolve
            # can be two sequential network requests) — the menu itself
            # must never touch the network.
            sync5_update_source=(lambda: host._resolve_sync5_update_binary()),
            # The ZX Next Remote twin behind the "Update ZX Next Remote"
            # session-tab entry and top-bar link: resolved by
            # zxnu_emulator_ops from the newest extracted itch.io folder.
            # LOCAL FILESYSTEM ONLY — no network — so unlike the dot's
            # resolver the widget may call it on the UI thread (it still
            # caches the answer per label refresh).
            zxnr_update_source=(
                lambda flavor: host._resolve_zxnr_update_binary(flavor)),
            zxnr_update_path=configuration_dictionary.get(
                SETTING_ZXNR_UPDATE_PATH) or "",
            on_zxnr_update_path_changed=_re_on_zxnr_update_path_changed,
            # The local ⇄ Next split (9.7.2): restored from the cfg here -
            # this widget is built lazily, after load_configuration_file's
            # splitter restore has run - and persisted by the move callback.
            splitter_sizes=configuration_dictionary.get(
                SETTING_NEXTSYNC_RE_SPLITTER) or None,
            on_splitter_moved=_re_on_splitter_moved,
            # The local drive switcher lives IN this widget's nav row now
            # (9.6.0). It used to be the classic tab's combo, left behind
            # on a full-width row above the whole view: it stretched across
            # the window, cost a row of height and pushed the local pane out
            # of line with the Next pane (reported).
            local_drives=(available_drives if platform.system() == "Windows"
                          else None),
            local_sort=local_sort, next_sort=next_sort,
            on_sort_changed=_re_on_sort_changed,
            extra_drives=extra_drives,
            on_extra_drives_changed=_re_on_extra_drives_changed,
            # Same "Start <emulator> with <file>" entries as the SD Card tab and
            # the classic explorer; the widget itself knows nothing about which
            # emulators are installed.
            emulator_entries=lambda path: emulator_autostart_entries(host, path),
            on_toast=lambda title, msg, variant="red": host._show_toast(
                title, msg, variant=variant, duration_ms=9000))
        host._re_widget = widget
        # The last-look UX restore (9.5.14) for these lazily-built panes:
        # config_io's startup restore cannot reach them (they may not be
        # created for hours, or ever), so the saved column widths apply
        # here, the moment the trees exist. Capture stays central — the
        # save hook reads the live trees whenever the widget exists.
        apply_tree_column_widths(
            widget.local_view,
            configuration_dictionary.get(SETTING_RE_LOCAL_COLS, ""))
        apply_tree_column_widths(
            widget.next_view,
            configuration_dictionary.get(SETTING_RE_NEXT_COLS, ""))
        # Ctrl+wheel font zoom on both panes: restored here like the column
        # widths above; every applied change persists immediately.
        def _re_tree_font_persist(_key):
            def _p(_pt):
                configuration_dictionary[_key] = str(_pt)
                save_configuration_file()
            return _p
        apply_tree_font_pt(
            widget.local_view,
            configuration_dictionary.get(SETTING_RE_LOCAL_FONT, ""))
        apply_tree_font_pt(
            widget.next_view,
            configuration_dictionary.get(SETTING_RE_NEXT_FONT, ""))
        bind_tree_font_zoom(widget.local_view,
                            _re_tree_font_persist(SETTING_RE_LOCAL_FONT))
        bind_tree_font_zoom(widget.next_view,
                            _re_tree_font_persist(SETTING_RE_NEXT_FONT))
        # ---- mini log under the explorer panes (2026-08-07): tracing the
        # bridge/server activity used to require flipping to the Classic
        # tab — which, before the same-day fix, even stopped the server.
        # This is the same stream (add_nextsync_log_window mirrors into
        # it), newest first like the classic list, right-click to copy,
        # with a retro sibling that follows the Classic/Retro toggle.
        container = QWidget()
        _lay = QVBoxLayout(container)
        _lay.setContentsMargins(0, 0, 0, 0)
        _lay.setSpacing(4)
        _lay.addWidget(widget, 1)
        mini = QListWidget(container)
        mini.setContextMenuPolicy(Qt.CustomContextMenu)

        def _re_mini_menu(pos):
            menu = QMenu(mini)
            item = mini.itemAt(pos)
            if item is not None:
                menu.addAction(
                    ui_tr_now("Copy"),
                    lambda: QApplication.clipboard().setText(item.text()))
            menu.addAction(
                ui_tr_now("Copy all text"),
                lambda: QApplication.clipboard().setText("\n".join(
                    mini.item(i).text() for i in range(mini.count()))))
            menu.exec(mini.mapToGlobal(pos))
        mini.customContextMenuRequested.connect(_re_mini_menu)
        # Seed with the classic log's story so far (it shows newest-first;
        # same order here).
        for i in range(min(host.nextsync_log.count(), 200)):
            mini.addItem(host.nextsync_log.item(i).text())
        mini_stack = QStackedWidget(container)
        mini_stack.setFixedHeight(110)
        mini_stack.addWidget(mini)
        _lay.addWidget(mini_stack)
        host._re_mini_log = mini
        host._re_mini_stack = mini_stack
        host._re_container = container
        host.nextsync_log_stack.addWidget(container)
        _re_mini_sync_mode()
        _re_apply_item_colors()          # tint to the user's configured colours
        # Sync the cached sync root with whatever the widget restored (a saved
        # path enables Start; first run leaves it disabled).
        host._re_sync_root = widget.sync_root() or ""
        widget.set_idle_status_provider(_re_idle_status)
        widget.set_idle_details_provider(_re_idle_details)
        _re_update_start_button()
        # Built after the UI language is known: walk it once so its
        # catalogued texts and tooltips are translated now AND their
        # English sources cached for the next language switch (the same
        # step the gallery takes for its lazily built pages).
        try:
            translate_widget_tree(widget, current_ui_language())
        except Exception:                       # noqa: BLE001
            logging.exception("Remote explorer: initial translation failed")
        return widget

    # Soft green "breathing" pulse on the running indicator (same idea as the
    # SD-card transfer-arrow pulse) so it's obvious the server is live.
    def _re_start_play_pulse():
        _re_stop_play_pulse()
        steps = 22
        phase = {"n": 0}

        def _tick():
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            pos = phase["n"]
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            a = int(70 + 150 * tri)
            try:
                host.nextsync_re_play_label.setStyleSheet(
                    "QLabel { color: #eafff0; font-weight: bold; padding: 4px 10px;"
                    " border-radius: 6px;"
                    f" background-color: rgba(46,204,113,{a});"
                    f" border: 1px solid rgba(46,204,113,{min(a + 60, 255)}); }}")
            except RuntimeError:
                pass
        timer = QTimer(host)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        host._re_pulse_timer = timer

    def _re_stop_play_pulse():
        if host._re_pulse_timer is not None:
            host._re_pulse_timer.stop()
            host._re_pulse_timer = None
        try:
            host.nextsync_re_play_label.setStyleSheet("")
        except RuntimeError:
            pass

    # Soft yellow "breathing" pulse on the Start button while the Remote
    # Explorer is open but the server has not been started yet — the amber
    # counterpart to the green "running" pulse above, so it's obvious the
    # next step is to start the server.
    def _re_start_startbtn_pulse(color="yellow"):
        _re_stop_startbtn_pulse()
        steps = 22
        phase = {"n": 0}
        # Pulse colour encodes what the button is asking for:
        #   green  -> no sync root yet, prompting the user to pick a folder
        #   yellow -> sync root chosen, server not started ("start me")
        (r, g, b), fg = ((46, 204, 113), "#eafff0") if color == "green" \
            else ((241, 196, 15), "#3a2e00")

        def _tick():
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            pos = phase["n"]
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            a = int(70 + 150 * tri)
            try:
                host.nextsync_re_start_button.setStyleSheet(
                    "QPushButton { color: %s; font-weight: bold;"
                    " padding: 4px 10px; border-radius: 6px;"
                    " background-color: rgba(%d,%d,%d,%d);"
                    " border: 1px solid rgba(%d,%d,%d,%d); }" % (
                        fg, r, g, b, a, r, g, b, min(a + 60, 255)))
            except RuntimeError:
                pass
        timer = QTimer(host)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        host._re_start_btn_pulse_timer = timer

    def _re_stop_startbtn_pulse():
        if host._re_start_btn_pulse_timer is not None:
            host._re_start_btn_pulse_timer.stop()
            host._re_start_btn_pulse_timer = None
        try:
            host.nextsync_re_start_button.setStyleSheet("")
        except RuntimeError:
            pass

    def _re_auto_relisten():
        """Deferred restart of the listen server after the Next hung up
        (see _nextsync_on_re_disconnected). Skipped if the user restarted
        it themselves or left Remote Explorer mode in the meantime."""
        if host._re_running:
            return
        try:
            if host.nextsync_mode_tabs.currentIndex() != 0:
                return
        except RuntimeError:
            return
        _nextsync_start_listen_server()

    def _nextsync_on_re_disconnected():
        # The -listen session ended. If _re_running is already False we
        # stopped it ourselves (the Stop button / closing the view already
        # reset the state) - nothing to do. Otherwise the Next hung up on its
        # own: it pressed BREAK / sent "Bye", or the link dropped. The worker
        # thread has now exited (it emits disconnected from its finally), so
        # nothing is listening on the socket any more; a restarted
        # '.sync5 -listen' would just get "connection refused".
        if not host._re_running:
            return
        host._re_thread = None
        host._re_stop = None
        host._re_queue = None
        # Leave self._re_sig alone: we're running inside its own
        # disconnected() slot, and the next Start reassigns it anyway.
        host._re_running = False
        _re_stop_play_pulse()          # green "running" pulse off
        try:
            host.nextsync_re_play_label.setVisible(False)
        except RuntimeError:
            pass
        if getattr(host, "_re_had_connection", False):
            # A Next was connected and hung up on its own: relisten
            # automatically so the next '.sync5 -listen' just works — no
            # button press needed. Deferred so this slot (owned by the old
            # session's signals) unwinds first; the old worker closed its
            # socket before emitting disconnected, so the port is free.
            # Gated on a real prior connection so a session that never got
            # one (e.g. a failing bind emitting disconnected) can't loop.
            add_nextsync_log_window(
                # '.sync5 -listen' is a literal the Next must receive verbatim,
                # so it is interpolated rather than left inside the translation.
                ui_tr_now("Remote explorer: the Next disconnected (BREAK / Bye) "
                          "— restarting the listen server; run {command} on "
                          "your Next to reconnect.").format(
                              command="'.sync5 -L' (-l or -listen)"))
            QTimer.singleShot(250, _re_auto_relisten)
            return
        add_nextsync_log_window(ui_tr_now(
            "Remote explorer: the Next disconnected (BREAK / Bye). "
            "Press 'Start Remote Explorer NextSync server' to accept a new "
            "connection."))
        # Restore the button to "Start" (and pulse it, if still in view and a
        # sync root is set) so the user can accept a fresh connection.
        _re_update_start_button()

    def _re_notify_session_toast(connected):
        """5 s toast whenever the Next connects to / disconnects from the
        Remote Explorer '.sync5 -listen' session. The session lives on the
        NextSync tab but the user may be anywhere in the app (the SD Card
        explorers' 'Send via NextSync' actions hinge on this state), so
        the state change is announced wherever they are. The worker also
        emits 'disconnected' from its finally when a listener that never
        saw a Next winds down (manual stop, failed bind) — gating on
        _re_had_connection filters those out, exactly as the
        auto-relisten does."""
        if connected:
            host._show_toast(
                "Next connected",
                "A Next is now connected to the NextSync Remote Explorer.",
                variant="green", duration_ms=5000)
        elif getattr(host, "_re_had_connection", False):
            host._show_toast(
                "Next disconnected",
                "The Next disconnected from the NextSync Remote Explorer.",
                variant="yellow", duration_ms=5000)

    def _nextsync_on_re_port_in_use(port):
        # The listen server could not bind: the port is already held, almost
        # always by another running ZX-Next-Unite (or a standalone NextSync
        # server). Reset to "not started" and warn with a yellow toast.
        # Clearing _re_running here also makes the disconnected() slot that
        # follows (emitted from the worker's finally) skip its misleading
        # "the Next disconnected" message.
        host._re_thread = None
        host._re_stop = None
        host._re_queue = None
        host._re_running = False
        _re_stop_play_pulse()
        try:
            host.nextsync_re_play_label.setVisible(False)
        except RuntimeError:
            pass
        add_nextsync_log_window(ui_tr_now(
            "Remote explorer: port {port} is already in use — is another "
            "ZX-Next-Unite (or NextSync server) already running?").format(
                port=port))
        host._show_toast(
            "NextSync server not started",
            ui_tr_now(
                "Port {port} is already in use.\nIs another ZX-Next-Unite "
                "instance (or a standalone NextSync server) already "
                "running?").format(port=port),
            variant="yellow", duration_ms=12000)
        _re_update_start_button()

    def _nextsync_start_listen_server():
        if host._re_running:
            return
        # A sync root must be chosen first (the button is normally disabled
        # without one; this guards direct/programmatic calls).
        if not getattr(host, "_re_sync_root", ""):
            add_nextsync_log_window(ui_tr_now(
                "Set a sync root folder first: navigate to the folder in the "
                "left local file explorer and press 'Set current folder as "
                "new sync root folder'."))
            return
        # Can't run the listen server while a normal sync is in progress —
        # both servers bind port 2048. Cancel with a clear advisory.
        t = getattr(host, "_nextsync_thread", None)
        if t is not None and t.is_alive():
            add_nextsync_log_window(ui_tr_now(
                "Stop the running sync before starting the remote server."))
            host._show_toast(
                "Remote Explorer NextSync server not started",
                "You have already started a Classic nextsync server, "
                "please stop it first.",
                variant="yellow", duration_ms=10000)
            return
        widget = host._re_widget or _nextsync_build_remote_explorer()
        import queue as _queue_mod
        host._re_queue = _queue_mod.Queue()
        host._re_stop = threading.Event()
        host._re_sig = RemoteExplorerSignals()
        # Whether this session ever saw a Next connect — the automatic
        # relisten on disconnect is gated on it (see
        # _nextsync_on_re_disconnected).
        host._re_had_connection = False
        host._re_sig.connected.connect(
            lambda: setattr(host, "_re_had_connection", True))
        host._re_sig.connected.connect(widget.on_connected)
        host._re_sig.disconnected.connect(widget.on_disconnected)
        # Reset the pane's server state when the Next hangs up on its own
        # (BREAK / Bye / dropped link), so the user must press Start again.
        host._re_sig.disconnected.connect(_nextsync_on_re_disconnected)
        # Session toasts (5 s, every tab). AFTER _nextsync_on_re_disconnected:
        # that handler only reads _re_had_connection, and the auto-relisten
        # that resets it runs deferred — so the flag is still valid here.
        host._re_sig.connected.connect(
            lambda: _re_notify_session_toast(True))
        host._re_sig.disconnected.connect(
            lambda: _re_notify_session_toast(False))
        host._re_sig.listing.connect(widget.on_listing)
        host._re_sig.ls_failed.connect(widget.on_ls_failed)
        host._re_sig.got.connect(widget.on_got)
        host._re_sig.put_done.connect(widget.on_put_done)
        host._re_sig.op_done.connect(widget.on_op_done)
        host._re_sig.os_protected.connect(widget.on_os_protected)
        host._re_sig.drives.connect(widget.on_drives)
        host._re_sig.free_space.connect(widget.on_free_space)
        host._re_sig.ident.connect(widget.on_ident)
        # Terminal verdict of the remote .sync5 self-update macro: the log
        # is the durable record, the toast reaches the user whatever tab
        # is in front. The message arrives already translated (emit-site
        # ui_tr_now in zxnu_workers), so it passes through untouched; the
        # static title translates inside the _show_toast chokepoint.
        host._re_sig.dot_update.connect(_nextsync_on_dot_update)
        # The verify-after-put verdict (9.7.3): red log line + red toast.
        host._re_sig.put_verify_failed.connect(_nextsync_on_put_verify_failed)
        host._re_sig.fsize.connect(widget.on_fsize)
        host._re_sig.op_progress.connect(widget.on_op_progress)
        # Keep the HTTP bridge's /status state fresh. DirectConnection:
        # these run on the worker thread and only write plain fields, so
        # the bridge (its own threads) sees them without any event loop.
        _bst = host._re_bridge_state
        host._re_sig.connected.connect(
            lambda: _bst.update(connected=True), Qt.DirectConnection)
        host._re_sig.disconnected.connect(
            lambda: _bst.update(connected=False, current="", drives=None),
            Qt.DirectConnection)
        host._re_sig.drives.connect(
            lambda cur, ls: _bst.update(current=cur or "",
                                        drives=list(ls) if ls else None),
            Qt.DirectConnection)
        host._re_sig.marked.connect(widget.on_marked)
        # Multi-Next roster (option B): the machine combo + switch refresh.
        host._re_sig.peers.connect(widget.on_peers)
        host._re_sig.log.connect(lambda s: add_nextsync_log_window(str(s)))
        host._re_sig.error.connect(widget.on_error)
        host._re_sig.error.connect(lambda s: add_nextsync_log_window("Remote explorer: " + str(s)))
        host._re_sig.port_in_use.connect(_nextsync_on_re_port_in_use)
        host._re_thread = threading.Thread(
            target=run_remote_listen_server,
            args=(host._re_sig, host._re_queue, host._re_stop),
            # verify_crc (9.7.3): the Settings toggle, read by the worker
            # PER PUT — a flip applies to the next file, no restart (the
            # update_prompt_enabled shape above; the lambda runs on the
            # worker thread: a GIL-atomic dict.get of a whole string).
            kwargs={"control": host._re_control,
                    "verify_crc": lambda: nextsync_verify_crc_enabled(configuration_dictionary)},
            daemon=True)
        host._re_thread.start()
        host._re_running = True
        _re_stop_startbtn_pulse()   # started now: drop the yellow "start me" pulse
        host.nextsync_re_start_button.setText("⏹ Stop Remote Explorer NextSync server")
        host.nextsync_re_play_label.setText("▶  Remote Explorer NextSync server running")
        host.nextsync_re_play_label.setVisible(True)
        _re_start_play_pulse()
        # 3 s confirmation (also the QMenu start path on the SD Card tab):
        # the {command} is a Next-side literal, so it is interpolated rather
        # than sitting inside the translatable template.
        host._show_toast("NextSync server started",
                         ui_tr_now("You can now start your Next {command} "
                                   "dot command.").format(
                             command="'.sync5 -L' (-l or -listen)"),
                         variant="green", duration_ms=3000)

    def _nextsync_stop_listen_server():
        # Ask the Next to leave -listen first: the worker delivers "Q" (quit)
        # on the Next's next poll, so the Next tears down its own connection
        # and returns to BASIC instead of sitting there waiting on the socket.
        # Give it a moment to be sent BEFORE forcing the socket shut with the
        # stop event (which would drop the link without saying goodbye).
        q = host._re_queue
        t = host._re_thread
        # Is a Next actually on the line? The 10 s goodbye grace below only
        # makes sense for a CONNECTED session: while merely listening the
        # worker sits in accept() and never polls the command queue, so the
        # "Q" cannot be delivered and there is no dot to say goodbye to —
        # waiting used to make Ctrl-C take ~11 s on an idle listener.
        connected = bool(getattr(getattr(host, "_re_widget", None),
                                 "_connected", False))
        if q is not None:
            try:
                # Drop everything still queued first: the transfer currently
                # in flight (if any) completes untouched, and "quit" becomes
                # the very next command the Next polls — so it leaves listen
                # mode at a clean file boundary instead of the stop event
                # below forcing the socket shut mid-batch (a half-written
                # file on the Next).
                _re_drain()
                q.put(("quit",))
            except Exception:
                # Load-bearing: if the goodbye can't be queued the Next is
                # left waiting (the dotN-hang class of bug), so make it
                # visible in the log rather than swallowing it silently.
                logging.exception("Remote Explorer: failed to queue the quit command")
        if connected and t is not None and t.is_alive():
            # Generous bound: the in-flight file must finish before the Next
            # polls again and receives the "Q" (slow Wi-Fi links move tens
            # of KB/s). Only after this do we force the socket shut.
            t.join(timeout=10.0)        # worker sends "Q", then exits cleanly
        # Fallback (and the whole path while unconnected): end the loop —
        # the accept() poll notices the stop event within its 1 s timeout.
        if host._re_stop is not None:
            host._re_stop.set()
        if t is not None and t.is_alive():
            t.join(timeout=1.0 if connected else 2.0)
        host._re_thread = None
        host._re_stop = None
        host._re_queue = None
        host._re_sig = None
        host._re_running = False
        _re_stop_play_pulse()
        try:
            host.nextsync_re_play_label.setVisible(False)
        except RuntimeError:
            pass
        # Back to "not started": restore the button (Start + pulse if a sync
        # root is set and we're still in view, else the disabled prompt).
        _re_update_start_button()
    # Exposed so app-exit paths (window close / Ctrl-C) can say goodbye to a
    # connected Next before the process dies. Safe/idempotent to call twice.
    host._nextsync_stop_listen_server_fn = _nextsync_stop_listen_server

    def _re_try_send_folder(folder):
        """Route a gallery-pane "Send via NextSync" through the live Remote
        Explorer '.sync5 -listen' session instead of the classic one-shot
        sync server.

        Handles the send only when the Remote Explorer server is running —
        the classic server could never even bind then, the listen session
        already holds port 2048. With a Next connected, *folder* (the
        downloaded per-item folder / itch.io install dir) is recreated
        under the Remote Explorer's current Next directory: each
        sub-directory is made with "mkdir" before its files are "put", so
        the item lands with the same relative layout a classic sync of that
        folder's parent would create. A green toast confirms success; Next-
        side failures are red-toasted (with descriptions) by the widget's
        operation tracker. Returns True when the send was handled here
        (including the advisory cases), False -> caller runs classic sync."""
        if not getattr(host, "_re_running", False):
            return False
        widget = getattr(host, "_re_widget", None)
        if widget is None:
            return False
        if not getattr(widget, "_connected", False):
            # Server up but no Next yet: cancel the send (a classic sync
            # would just die on the taken port) and tell the user how to
            # proceed. 30 s so it survives the walk to the Next.
            host._show_toast(
                "You have started a Remote Explorer nextsync server already",
                "Start '.sync5 -L' (-l or -listen) on your Next and retry "
                "again (canceling the upload / send process for now).",
                variant="yellow", duration_ms=30000)
            return True
        if not (folder and os.path.isdir(folder)):
            return False
        # The remote paths the widget's folder upload will create (kept in
        # step with RemoteExplorerWidget._enqueue_dir_upload), for the log
        # line and the success toast.
        cwd = widget.remote_cwd()
        base = cwd if cwd.endswith("/") else cwd + "/"
        top = os.path.basename(os.path.normpath(folder).rstrip("/\\")) or "dir"
        sent = []
        for _root, _dirs, _files in os.walk(folder):
            _dirs.sort()
            rel = os.path.relpath(_root, folder).replace(os.sep, "/")
            rdir = base + top if rel in (".", "") else base + top + "/" + rel
            for _name in sorted(_files):
                sent.append(rdir + "/" + _name)
        if not sent:
            add_nextsync_log_window(ui_tr_now(
                "Send via NextSync: nothing to send in {folder}.").format(
                    folder=folder))
            return True

        def _done(ok, fails):
            if not ok:
                # Failures were already red-toasted (with the per-file
                # descriptions) by the Remote Explorer; a user cancel or a
                # disconnect needs no success banner either way.
                return
            if len(sent) == 1:
                body = ui_tr_now("file {path}").format(path=sent[0])
            else:
                body = (ui_tr_now("{n} files:").format(n=len(sent))
                        + "\n" + "\n".join(sent[:5]))
                if len(sent) > 5:
                    body += "\n" + ui_tr_now("…and {n} more").format(
                        n=len(sent) - 5)
            host._show_toast("✅  Sent via Remote Explorer", body,
                             variant="green", duration_ms=8000)

        state = widget.send_local_paths(
            [folder], title="Sending via Remote Explorer…", on_done=_done)
        if state == "busy":
            host._show_toast(
                "Remote Explorer is busy",
                "Another transfer is still running — wait for it to "
                "finish, then try again.",
                variant="yellow", duration_ms=8000)
            return True
        if state != "queued":
            return False
        add_nextsync_log_window(ui_tr_now(
            "Sending {folder} via Remote Explorer (-listen) → {target} …"
        ).format(folder=folder, target=cwd))
        return True

    def _nextsync_re_toggle_server():
        if host._re_running:
            _nextsync_stop_listen_server()
        else:
            _nextsync_start_listen_server()
    host._nextsync_re_toggle_server = _nextsync_re_toggle_server

    def _nextsync_toggle_remote_explorer(checked):
        if checked:
            _nextsync_build_remote_explorer()
            # The stack entry is the container (explorer + mini log).
            host.nextsync_log_stack.setCurrentWidget(host._re_container)
            _re_mini_sync_mode()
            # Swap the normal sync controls for the dedicated server control.
            host.nextsync_prepare_server.setVisible(False)
            host.nextsync_start_server.setVisible(False)
            host.nextsync_cancel_server.setVisible(False)
            host.nextsync_sync_mode_group.setVisible(False)
            host.nextsync_slowtransfer_checkbox.setVisible(False)
            # The retro-log toggle ("🎮 Retro" / "🖼 Switch to 'Classic' view
            # mode") is meaningless
            # here — the log window is replaced by the file explorer — so hide
            # it in Remote Explorer mode (restored on the Classic tab).
            host.nextsync_pygame_button.setVisible(False)
            host.nextsync_re_start_button.setVisible(True)
            host.nextsync_re_play_label.setVisible(host._re_running)
            # Set the button state: disabled with a "pick a sync root" prompt
            # until the user selects one, then "Start" (pulsing yellow).
            _re_update_start_button()
            # Give the Remote Explorer the full width AND the full height:
            # hide the local file explorer column (with its SyncIgnore /
            # SyncPoint buttons), the name filter and — since 9.6.0 — the
            # classic drive switcher. That combo used to be left behind
            # here as the Remote Explorer's way to change drive, alone on
            # its row: it stretched across the entire window, cost a row of
            # height and pushed the local pane out of line with the Next
            # pane (reported). The Remote Explorer carries its own drive
            # combo in its nav row now, where the SD Card tab's local pane
            # has always had one, so this row can empty out completely.
            host.nextsync_fileexplorer_and_buttons_container.setVisible(False)
            # setRowVisible, not three setVisible calls: hiding the widgets
            # leaves a zero-height row that still takes the form's vertical
            # spacing, which is part of the dead band this removes.
            host.nextsync_form.setRowVisible(
                host._nextsync_classic_bar_row, False)
            # The full walkthrough is for the EMPTY state only: with a sync
            # root already set (or the server already running) it read like
            # the app had forgotten the root it was actively using.
            if (not getattr(host, "_re_sync_root", "")
                    and not getattr(host, "_re_running", False)):
                add_nextsync_log_window(
                    ui_tr_now(
                        "Remote explorer: navigate to a folder in the left file "
                        "explorer, press 'Set current folder as new sync root "
                        "folder', click 'Start Remote Explorer NextSync server', "
                        "then run {command} on your Next.").format(
                            command="'.sync5 -L' (-l or -listen)"))
        else:
            # The listen server SURVIVES leaving the Remote Explorer view
            # (2026-08-07). Stopping it here sent a protocol 'Q' — or, with
            # a transfer in flight, a force-close after the goodbye grace —
            # every time the user flipped to the Classic tab to watch the
            # console… which is exactly where the HTTP bridge lines print.
            # The bridge rides this same session, so a peek at the log
            # killed the peer's connection ("Server quit." / "put FAIL
            # link down r1" on the Next) and every later bridge call
            # answered 503, looking like a phantom server crash. Stopping
            # is now only ever explicit: the Start/Stop button, the SD
            # tab's context menu, or app exit.
            _re_stop_startbtn_pulse()   # leaving the RE view: drop the pulse
            if getattr(host, "_re_running", False):
                add_nextsync_log_window(ui_tr_now(
                    "Remote explorer: server keeps running in the "
                    "background — stop it from the Remote Explorer view."))
            # Restore the log view that matches the current retro/classic
            # choice — NOT always the classic list. Forcing the list here
            # while retro mode is on desyncs the retro toggle: the button
            # still reads "🖼 Switch to 'Classic' view mode" (checked) but the
            # plain list shows, so
            # the user's next press merely unchecks it with no visible change
            # ("Retro doesn't switch on the first press").
            if getattr(host, "_nextsync_pygame_on", False) and \
                    host._nextsync_retro_log is not None:
                host.nextsync_log_stack.setCurrentWidget(host._nextsync_retro_log)
                host._nextsync_retro_log.start()
            else:
                host.nextsync_log_stack.setCurrentWidget(host.nextsync_log)
            host.nextsync_re_start_button.setVisible(False)
            host.nextsync_re_play_label.setVisible(False)
            # Restore the normal sync controls.
            host.nextsync_prepare_server.setVisible(True)
            host.nextsync_sync_mode_group.setVisible(True)
            host.nextsync_slowtransfer_checkbox.setVisible(True)
            # Bring the retro-log toggle back (hidden in Remote Explorer mode).
            host.nextsync_pygame_button.setVisible(True)
            # Restore the local file explorer column, the name filter and
            # the classic drive switcher (Windows only — there is no drive
            # letter to switch on the other platforms, and it has been
            # hidden since construction there).
            host.nextsync_fileexplorer_and_buttons_container.setVisible(True)
            host.nextsync_form.setRowVisible(
                host._nextsync_classic_bar_row, True)
            # setRowVisible shows EVERY widget in the row, the drive combo
            # included — and there is no drive letter to switch outside
            # Windows, where it has been hidden since construction.
            host.nextsync_diskdrive.setVisible(platform.system() == "Windows")
            nextsync_hide_start_cancel_buttons()
        # Persist the open/closed choice so the NextSync tab reopens in the
        # same view next launch. Skipped while restoring the saved choice at
        # startup (and save_configuration_file is a no-op during _initialising).
        if not getattr(host, "_re_open_restoring", False):
            try:
                configuration_dictionary[SETTING_NEXTSYNC_REMOTE_EXPLORER] = (
                    "true" if checked else "false")
                save_configuration_file()
            except Exception:
                pass

    def _nextsync_on_mode_tab_changed(index):
        # Tab 0 = Remote Explorer, tab 1 = Classic. Reuse the toggle logic:
        # `checked` here means "Remote Explorer active".
        _nextsync_toggle_remote_explorer(index == 0)
    host.nextsync_mode_tabs.currentChanged.connect(_nextsync_on_mode_tab_changed)

    def _nextsync_build_retro_log():
        if host._nextsync_retro_log is not None:
            return host._nextsync_retro_log
        from zxnu_pygame import RetroLogWidget
        # scrollable live log: auto-follows the newest line, but the user can
        # scroll up (scrollbar / wheel) to read the history.
        widget = RetroLogWidget(
            scrollable=True, follow_tail=True, context_copy=True,
            font_px=getattr(host, "_retro_log_font_size",
                            DEFAULT_RETRO_LOG_FONT_SIZE))
        # Right-click font stepping (persisted via the Settings combo).
        widget.set_font_step_cb(lambda d: host._step_retro_log_font(d))
        widget.setMinimumHeight(NEXTSYNC_UI_HEIGTH)
        try:
            widget.enable_background(getattr(host, "_nextsync_pygame_anim", True))
        except Exception:
            pass
        # Seed the user's retro-log text color (Settings color picker).
        try:
            widget.set_text_color(qcolor_to_hex(host.img_color_retro_log))
        except Exception:
            pass
        # Seed it with the existing classic-log contents. The list shows
        # newest-first, so iterate bottom-up for chronological order.
        for i in range(host.nextsync_log.count() - 1, -1, -1):
            widget.append(host.nextsync_log.item(i).text())
        host._nextsync_retro_log = widget
        host.nextsync_log_stack.addWidget(widget)
        return widget

    def _nextsync_pygame_disable(reason=""):
        btn = host.nextsync_pygame_button
        btn.blockSignals(True)
        btn.setChecked(False)
        btn.setText(ui_tr_now("🎮 Retro"))
        btn.blockSignals(False)
        btn.setEnabled(False)
        if reason:
            btn.setToolTip(reason)

    def _nextsync_pygame_persist(enabled):
        # Skip writing while restoring the saved choice at startup so a
        # transient "pygame unavailable" never clobbers the user's pref.
        if getattr(host, "_nextsync_pygame_restoring", False):
            return
        try:
            configuration_dictionary[SETTING_NEXTSYNC_PYGAME_MODE] = (
                "true" if enabled else "false")
            save_configuration_file()
        except Exception:
            pass

    def _nextsync_on_pygame_toggled(checked):
        if checked:
            try:
                from zxnu_pygame import pygame_available
                ok, why = pygame_available()
            except Exception as exc:
                ok, why = False, str(exc)
            if not ok:
                _nextsync_pygame_disable(
                    f"{why}\nInstall with: pip install pygame-ce")
                add_nextsync_log_window(
                    ui_tr_now(
                        "Pygame mode unavailable — run: pip install pygame-ce"))
                return
            try:
                widget = _nextsync_build_retro_log()
            except Exception as exc:
                _nextsync_pygame_disable(f"Pygame init failed: {exc}")
                return
            host._nextsync_pygame_on = True
            host.nextsync_pygame_button.setText(ui_tr_now("🖼 Switch to 'Classic' view mode"))
            host.nextsync_log_stack.setCurrentWidget(widget)
            widget.start()
            _re_mini_sync_mode()       # the RE view's mini log follows
            _nextsync_pygame_persist(True)
        else:
            host._nextsync_pygame_on = False
            host.nextsync_pygame_button.setText(ui_tr_now("🎮 Retro"))
            if host._nextsync_retro_log is not None:
                host._nextsync_retro_log.stop()
            host.nextsync_log_stack.setCurrentWidget(host.nextsync_log)
            _re_mini_sync_mode()       # the RE view's mini log follows
            _nextsync_pygame_persist(False)

    host.nextsync_pygame_button.toggled.connect(_nextsync_on_pygame_toggled)


    # Sync mode — three mutually-exclusive radio buttons in a titled group.
    # The two original boolean settings are kept for backward compatibility:
    #   Incremental  -> SYNCONCE=false, ALWAYSSYNC=false  (continuous, skip known)
    #   Sync once    -> SYNCONCE=true,  ALWAYSSYNC=false  (one-shot)
    #   Always sync  -> SYNCONCE=false, ALWAYSSYNC=true   (continuous, send all)
    # The radios keep the historical attribute names so the server loop's
    # `.isChecked()` reads (sync-once / always-sync) work unchanged.
    host.nextsync_sync_mode_group = QGroupBox("Sync mode")
    _sync_mode_layout = QVBoxLayout(host.nextsync_sync_mode_group)
    _sync_mode_layout.setContentsMargins(8, 4, 8, 4)
    _sync_mode_layout.setSpacing(2)

    host.nextsync_syncincremental_radio = QRadioButton("Sync changed files (continuous)")
    host.nextsync_syncincremental_radio.setToolTip(
        "Keep listening and send only files that are new or changed since the\n"
        "last sync (skips files recorded in the sync point). The default mode.")
    host.nextsync_synconce_checkbox = QRadioButton("Sync once")
    host.nextsync_synconce_checkbox.setToolTip(
        "Perform a single sync and then stop the server.")
    host.nextsync_alwayssync_checkbox = QRadioButton("Always sync (send everything)")
    host.nextsync_alwayssync_checkbox.setToolTip(
        "Keep listening and send every file each time, ignoring the sync point.")

    host.nextsync_syncincremental_radio.setChecked(True)   # default mode

    _sync_mode_layout.addWidget(host.nextsync_syncincremental_radio)
    _sync_mode_layout.addWidget(host.nextsync_synconce_checkbox)
    _sync_mode_layout.addWidget(host.nextsync_alwayssync_checkbox)

    # An exclusive button group enforces "one or the other" automatically.
    host._nextsync_sync_mode_btngroup = QButtonGroup(host)
    host._nextsync_sync_mode_btngroup.setExclusive(True)
    host._nextsync_sync_mode_btngroup.addButton(host.nextsync_syncincremental_radio)
    host._nextsync_sync_mode_btngroup.addButton(host.nextsync_synconce_checkbox)
    host._nextsync_sync_mode_btngroup.addButton(host.nextsync_alwayssync_checkbox)

    def _on_sync_mode_toggled(_btn, checked):
        # Only the newly-selected radio drives a persist (avoids a double
        # save for the paired off/on toggles), and transient overrides from
        # the gallery "Send via NextSync" path are not written to config.
        if checked and not getattr(host, "_nextsync_sync_mode_transient", False):
            nextsync_sync_mode_changed()
    host._nextsync_sync_mode_btngroup.buttonToggled.connect(_on_sync_mode_toggled)

    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_sync_mode_group)


    host.nextsync_slowtransfer_checkbox = QCheckBox("Slow transfer")
    host.nextsync_slowtransfer_checkbox.setText("Slow transfer")
    #self.nextsync_alwayssync_checkbox.setChecked(True)
    host.nextsync_slowtransfer_checkbox.stateChanged.connect(nextsync_slowtransfer_checkbox_statechanged)
    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_slowtransfer_checkbox)


    host.nextsync_prepare_server = QPushButton("Prepare Server", host)
    host.nextsync_prepare_server.setText("Prepare Classic NextSync server")
    host.nextsync_prepare_server.clicked.connect(nextsync_perform_checks_and_prepare_server_start)

    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_prepare_server)



    host.nextsync_start_server = QPushButton("▶ Start Classic NextSync server", host)
    host.nextsync_start_server.setText("▶ Start Classic NextSync server")
    host.nextsync_start_server.clicked.connect(nextsync_start_server)

    # Cancel button is kept as a hidden widget (so the existing show/hide and
    # handler wiring stays valid) but is no longer added to the pane layout.
    # Cancelling an in-progress sync is done via the progress dialog's Stop.
    host.nextsync_cancel_server = QPushButton("Cancel NextSync Server", host)
    host.nextsync_cancel_server.setText("Cancel sync")
    host.nextsync_cancel_server.clicked.connect(nextsync_cancel_server_job)
    host.nextsync_cancel_server.setVisible(False)


    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_start_server)

    # Remote-explorer server control (shown only in Remote Explorer mode,
    # in place of the Prepare/Start buttons and the Sync mode group).
    host.nextsync_re_start_button = QPushButton("▶ Start Remote Explorer NextSync server", host)
    host.nextsync_re_start_button.setVisible(False)
    host.nextsync_re_start_button.clicked.connect(host._nextsync_re_toggle_server)
    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_re_start_button)

    host.nextsync_re_play_label = QLabel("▶  Remote Explorer NextSync server running", host)
    host.nextsync_re_play_label.setAlignment(Qt.AlignCenter)
    host.nextsync_re_play_label.setVisible(False)
    host.nextsync_container_log_and_sync_buttons.addWidget(host.nextsync_re_play_label)




    host.horizontal12.addWidget(host.nextsync_log_and_sync_buttons_container)


    host.nextsync_form.addRow(host.horizontal14)

    nextsync_hide_start_cancel_buttons()

    host.nextsync_progressbar = QProgressBar()
    host.nextsync_progressbar.setGeometry(QRect(20, 10, 361, 23))
    host.nextsync_progressbar.setProperty("value", 0)
    host.nextsync_progressbar.setObjectName("progressBar")
    host.nextsync_progressbar.setVisible(False)

    host.horizontal15.addWidget(host.nextsync_progressbar)


    host.nextsync_form.addRow(host.horizontal15)

    # Read back by MainWindow.__init__ (see module docstring).
    host.nextsync_container = nextsync_container
    host._re_try_send_folder = _re_try_send_folder
