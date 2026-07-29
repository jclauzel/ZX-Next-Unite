"""zxnu_nextsync_ops.py — NextSync tab operation layer.

Strangler extraction from MainWindow.__init__ (builder-function seam; pure
ops — the NextSync WIDGETS live in zxnu_nextsync_pane.py). Three builders,
each called at its block's historical __init__ position because the original
code is interleaved with SD-card op blocks that stay in the monolith:

* build_nextsync_server_start(host, ...) — the prepare-debounce timer,
  nextsync_perform_checks_and_prepare_server_start / nextsync_refresh_explorer
  and nextsync_start_server (Sync3/Sync4 + Remote Explorer -listen routing).
  nextsync_warnings / nextsync_do_server_job / _re_try_send_folder are defined
  later in __init__ (the last by build_nextsync_pane) and arrive as forwarding
  lambdas resolved at server-start time.
* build_nextsync_explorer_ops(host, ...) — the classic NextSync explorer's
  operation closures: context menu, rename/copy/paste/import/delete, the
  sync-root row, syncignore/syncpoint buttons, sync-mode + slow-transfer
  persistence, double-click navigation.
* build_nextsync_server_job(host, ...) — nextsync_warnings, IP info, cancel,
  the Sync4 send-conflict prompt marshalling (NextSyncConflictSignals) and
  nextsync_do_server_job (drives run_classic_sync_server from the worker).

Everything the blocks assigned to ``self`` is written to ``host``; the
closures the rest of __init__ consumes by bare name are exposed as host
attributes and re-bound to bare locals at the call sites (the start-server
closure via the pre-existing host._nextsync_start_server_fn — the bare
``host.nextsync_start_server`` attribute is the Start button widget). See
CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import threading

from PySide6.QtCore import QTimer
from PySide6.QtGui import (QAction, QGuiApplication)
from PySide6.QtWidgets import (QInputDialog, QMenu, QMessageBox)

from zxnu_config import *
from zxnu_workers import *
from zxnu_i18n import ui_tr_now
from zxnu_network import detect_local_ipv4


def build_nextsync_server_start(
    host,
    *,
    save_configuration_file,
    add_nextsync_log_window,
    nextsync_server_exception_occured,
    _nextsync_on_port_in_use,
    nextsync_hide_start_cancel_buttons,
    nextsync_warnings,
    nextsync_do_server_job,
    _re_try_send_folder,
):
    """Prepare/refresh/start-server closures + the prepare-debounce timer."""
    def _nextsync_run_prepare():
        nextsync_warnings()
        save_configuration_file()

    # Debounce timer: coalesces rapid prepare requests (e.g. clicking around
    # the explorer) so the recursive scan in nextsync_warnings() runs once,
    # shortly after the user settles, rather than once per selection change.
    host._nextsync_prepare_timer = QTimer(host)
    host._nextsync_prepare_timer.setSingleShot(True)
    host._nextsync_prepare_timer.setInterval(NEXTSYNC_PREPARE_DEBOUNCE_MS)
    host._nextsync_prepare_timer.timeout.connect(_nextsync_run_prepare)

    def nextsync_perform_checks_and_prepare_server_start():
        host._nextsync_prepare_timer.start()

    def nextsync_refresh_explorer():
        """Force the NextSync left explorer to re-stat the displayed folder.

        Files just written by the upload (.sync5 -send) thread otherwise keep
        showing their initial 0 KB size: QFileSystemModel caches the size from
        when the file was first created (empty) and doesn't reliably re-stat
        it. Toggling the model's root path makes it rescan. Runs on the UI
        thread (wired to sig.finished), so touching the widgets is safe.
        """
        try:
            root_proxy = host.nextsync_treeview.rootIndex()
            root_src = host.nextsync_model.mapToSource(root_proxy)
            view_path = host.nextsync_filesystem_model.filePath(root_src)
            # Bounce via "" so a repeated sync to the same folder still
            # rescans (setRootPath is a no-op when the path is unchanged).
            host.nextsync_filesystem_model.setRootPath("")
            host.nextsync_filesystem_model.setRootPath(view_path or "/")
            if view_path:
                host.nextsync_treeview.setRootIndex(
                    host.nextsync_model.mapFromSource(
                        host.nextsync_filesystem_model.index(view_path)))
        except Exception as e:
            logging.error(f"NextSync explorer refresh failed: {e}", exc_info=True)

    def nextsync_start_server(serve_folder=None):
        # A gallery "Send via NextSync" (explicit serve_folder) is routed
        # through the Remote Explorer's '.sync5 -listen' session when that
        # server is running: the item is pushed with mkdir/put over the
        # live link instead of starting the classic one-shot sync server
        # (which couldn't bind anyway — the listen server holds port 2048).
        # _re_try_send_folder is defined later in __init__, which is fine:
        # this closure resolves it at call time, always after construction.
        if serve_folder and _re_try_send_folder(serve_folder):
            return
        # Starting the classic server while the Remote Explorer listen
        # server is live can only fail — both need port 2048 — so cancel
        # here with a clear advisory instead of a cryptic bind error.
        if getattr(host, "_re_running", False):
            host._show_toast(
                "Classic NextSync server not started",
                "You have already started a Remote Explorer nextsync "
                "server, please stop it first.",
                variant="yellow", duration_ms=10000)
            return
        # Guard: don't start a second sync while one is already running
        t = getattr(host, "_nextsync_thread", None)
        if t is not None and t.is_alive():
            add_nextsync_log_window("NextSync is already running — please wait for it to finish.")
            return
        try:
            # --- progress dialog ---
            dlg = HdfProgressDialog("NextSync — sending to ZX Spectrum Next", parent=host, cancel_label="Stop")
            dlg.set_status("Waiting for ZX Next to connect…\nRun .sync5 (or .sync5 -fast) on your Next")
            dlg.set_progress(-1)   # indeterminate spinner until first file

            sig = NextSyncSignals()
            cancel_flag = threading.Event()
            # Exposed for the app-exit path: _graceful_nextsync_shutdown
            # sets it so a sync in flight when the window closes / Ctrl-C
            # arrives ends at a safe file boundary (current file finished,
            # sync point persisted) instead of dying mid-transfer with the
            # process. Stale after the sync ends — always gated on
            # _nextsync_thread.is_alive().
            host._nextsync_cancel_flag = cancel_flag

            sig.progress.connect(dlg.set_progress)
            sig.status.connect(dlg.set_status)
            sig.finished.connect(lambda: QTimer.singleShot(800, dlg.accept))
            # Refresh the left explorer so files received via .sync5 -send show
            # their real size instead of a stale 0 KB.
            sig.finished.connect(nextsync_refresh_explorer)
            sig.cancelled.connect(dlg.mark_cancelled)
            # Port already taken (another instance?) -> yellow toast on the UI thread.
            sig.port_in_use.connect(_nextsync_on_port_in_use)
            dlg.cancel_requested.connect(lambda: cancel_flag.set())

            # "Send via NextSync" (an explicit serve_folder, e.g. from a
            # GalleryItemViewer) transfers a specific item exactly once.
            # Transiently switch the Sync mode to "Sync once" for the duration
            # of the send — without persisting it — then restore the user's
            # chosen mode (e.g. Always sync) when the transfer finishes.
            if serve_folder and not host.nextsync_synconce_checkbox.isChecked():
                _prev_always = host.nextsync_alwayssync_checkbox.isChecked()
                _prev_incr = host.nextsync_syncincremental_radio.isChecked()
                host._nextsync_sync_mode_transient = True
                host.nextsync_synconce_checkbox.setChecked(True)
                host._nextsync_sync_mode_transient = False

                def _restore_sync_mode(_pa=_prev_always, _pi=_prev_incr):
                    host._nextsync_sync_mode_transient = True
                    try:
                        if _pa:
                            host.nextsync_alwayssync_checkbox.setChecked(True)
                        elif _pi:
                            host.nextsync_syncincremental_radio.setChecked(True)
                    finally:
                        host._nextsync_sync_mode_transient = False
                sig.finished.connect(_restore_sync_mode)

            def _run(_sf=serve_folder):
                try:
                    nextsync_do_server_job(
                        progress_callback=sig.progress,
                        status_callback=sig.status,
                        cancel_flag=cancel_flag,
                        serve_folder=_sf,
                    )
                except Exception as ex:
                    logging.error(f"NextSync thread error: {ex}", exc_info=True)
                    # A port clash is reported via its own signal (-> yellow
                    # toast on the UI thread); everything else logs as before.
                    if is_address_in_use(ex):
                        sig.port_in_use.emit(PORT)
                    else:
                        nextsync_server_exception_occured(ex)
                finally:
                    # A session aborted by an exception would otherwise
                    # leave the transfer flag stuck on (see the sidebar
                    # sync-icon animation).
                    host._nextsync_transfer_active = False
                    if cancel_flag.is_set():
                        sig.cancelled.emit()
                    sig.finished.emit()

            t = threading.Thread(target=_run, daemon=True)
            host._nextsync_thread = t
            nextsync_hide_start_cancel_buttons()
            t.start()
            dlg.exec()   # blocks main thread showing the modal dialog
            # Tear the dialog (and its child spinner QTimer) down on the
            # event loop now, instead of leaving it in a reference cycle for
            # Python's GC to collect later — a QTimer firing / a QDialog
            # being destroyed mid-paint is what triggers the "QBackingStore
            # ::flush() ... does not have a handle" crash on cancel.
            dlg.deleteLater()
            # Ensure pane is in the correct state after dialog closes
            QTimer.singleShot(0, lambda: (
                nextsync_hide_start_cancel_buttons(),
                host.nextsync_prepare_server.setVisible(True),
            ))

        except Exception as e:
            logging.error(f"An unexpected error occurred while starting nextsync server. Exception: {e}", exc_info=True)

    # Store on self so it can be called from any scope (e.g. ZXDB/GetIt Send via NextSync)
    host._nextsync_start_server_fn = nextsync_start_server

    # Consumed by bare name later in __init__ (re-bound at the call site).
    host.nextsync_perform_checks_and_prepare_server_start = (
        nextsync_perform_checks_and_prepare_server_start)
    host.nextsync_refresh_explorer = nextsync_refresh_explorer


def build_nextsync_explorer_ops(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    add_nextsync_log_window,
    set_treeview_properties,
    nextsync_perform_checks_and_prepare_server_start,
    nextsync_refresh_explorer,
    _deletes_go_to_recycle_bin,
    _local_delete_paths_async,
    _local_make_directory,
    _explorer_clipboard_set,
    _explorer_clipboard_has_items,
    _explorer_paste_into_local,
):
    """The classic NextSync explorer's operation closures."""
    def nextsync_current_view_dir():
        """Path of the folder currently shown at the top of the NextSync tree."""
        root_proxy = host.nextsync_treeview.rootIndex()
        root_src = host.nextsync_model.mapToSource(root_proxy)
        return host.nextsync_filesystem_model.filePath(root_src)

    def nextsync_on_treeview_context_menu(pos):
        index = host.nextsync_treeview.indexAt(pos)
        source_index = host.nextsync_model.mapToSource(index) if index.isValid() else None
        name = host.nextsync_filesystem_model.fileName(source_index) if source_index is not None else ""
        # Empty space or the ".." up-entry: only offer Paste, into the current folder.
        if source_index is None or name == "..":
            clipboard_has = _explorer_clipboard_has_items()
            paste_dir = nextsync_current_view_dir()
            menu = QMenu(host.nextsync_treeview)
            action_newdir = QAction(ui_tr_now("Create new directory…"), host.nextsync_treeview)
            action_newdir.triggered.connect(lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                paste_dir, nextsync_refresh_explorer, add_nextsync_log_window)))
            action_paste = QAction(ui_tr_now("Paste"), host.nextsync_treeview)
            action_paste.setEnabled(clipboard_has and bool(paste_dir))
            action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_paste_explorer_item(paste_dir)))
            menu.addAction(action_newdir)
            menu.addAction(action_paste)
            menu.exec(host.nextsync_treeview.viewport().mapToGlobal(pos))
            return
        file_path = host.nextsync_filesystem_model.filePath(source_index)
        is_dir = host.nextsync_filesystem_model.isDir(source_index)
        # Paste / new-folder target: into the folder itself, or into a file's
        # parent folder.
        paste_dir = file_path if is_dir else os.path.dirname(file_path)
        menu = QMenu(host.nextsync_treeview)
        action_copy_text = QAction(ui_tr_now("Copy text to clipboard"), host.nextsync_treeview)
        action_copy_path = QAction(ui_tr_now("Copy path to clipboard"), host.nextsync_treeview)
        action_newdir = QAction(ui_tr_now("Create new directory…"), host.nextsync_treeview)
        action_copy = QAction(ui_tr_now("Copy"), host.nextsync_treeview)
        action_cut = QAction(ui_tr_now("Cut"), host.nextsync_treeview)
        action_paste = QAction(ui_tr_now("Paste"), host.nextsync_treeview)
        action_rename = QAction(ui_tr_now("Rename"), host.nextsync_treeview)
        action_delete = QAction(ui_tr_now("Delete"), host.nextsync_treeview)
        action_paste.setEnabled(_explorer_clipboard_has_items())
        action_copy_text.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
        action_copy_path.triggered.connect(lambda: QGuiApplication.clipboard().setText(file_path))
        action_copy.triggered.connect(lambda: nextsync_copy_explorer_item(file_path))
        action_cut.triggered.connect(lambda: nextsync_copy_explorer_item(file_path, mode="cut"))
        # Defer the dialog-showing actions until after the context menu's modal
        # event loop has closed: showing a QMessageBox/QInputDialog from inside
        # menu.exec() fights the menu's input grab and can hang the UI.
        action_newdir.triggered.connect(lambda: QTimer.singleShot(0, lambda: _local_make_directory(
            paste_dir, nextsync_refresh_explorer, add_nextsync_log_window)))
        action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_paste_explorer_item(paste_dir)))
        action_rename.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_rename_explorer_item(file_path, name, is_dir)))
        action_delete.triggered.connect(lambda: QTimer.singleShot(0, lambda: nextsync_delete_explorer_item(file_path, name, is_dir)))
        menu.addAction(action_copy_text)
        menu.addAction(action_copy_path)
        menu.addSeparator()
        menu.addAction(action_newdir)
        menu.addAction(action_copy)
        menu.addAction(action_cut)
        menu.addAction(action_paste)
        menu.addAction(action_rename)
        menu.addAction(action_delete)
        menu.exec(host.nextsync_treeview.viewport().mapToGlobal(pos))

    def nextsync_rename_explorer_item(file_path, name, is_dir):
        """Rename a file/folder in the NextSync explorer (local filesystem).

        Prompts for a new name, refuses path separators and overwriting an
        existing entry, then renames in place and refreshes the tree.
        """
        kind = "folder" if is_dir else "file"
        new_name, ok = QInputDialog.getText(
            host, ui_tr_now("Rename"), ui_tr_now("New name for the {kind}:").format(kind=ui_tr_now(kind)), text=name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == name:
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.warning(host, ui_tr_now("Rename failed"), ui_tr_now("The name cannot contain '/' or '\\'."))
            return
        new_path = os.path.join(os.path.dirname(file_path), new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(host, ui_tr_now("Rename failed"), ui_tr_now('"{name}" already exists in this folder.').format(name=new_name))
            return
        try:
            os.rename(file_path, new_path)
            add_nextsync_log_window(f"{timestamp()} | Renamed: {file_path} -> {new_path}")
        except OSError as e:
            logging.error(f"Failed to rename {file_path} -> {new_path}: {e}", exc_info=True)
            add_nextsync_log_window(f"{timestamp()} | Failed to rename {file_path}: {e}")
            QMessageBox.critical(host, ui_tr_now("Rename failed"), ui_tr_now("Could not rename:") + f"\n{file_path}\n\n{e}")
        finally:
            nextsync_refresh_explorer()

    def nextsync_copy_explorer_item(file_path, mode="copy"):
        """Remember a file/folder for a later Paste (or Cut, when mode='cut').
        Routed through the shared cross-explorer clipboard so Copy/Cut/Paste work
        between the NextSync explorer, the SD-card local explorer and the image."""
        _explorer_clipboard_set("local", [(file_path, os.path.isdir(file_path))],
                                add_nextsync_log_window, mode=mode)

    def _nextsync_unique_path(path, is_dir):
        """Return path, or a non-colliding '-(copy)' variant if it already exists."""
        if not os.path.exists(path):
            return path
        parent = os.path.dirname(path)
        name = os.path.basename(path)
        stem, ext = (name, "") if is_dir else os.path.splitext(name)
        i = 1
        while True:
            suffix = "-(copy)" if i == 1 else f"-(copy) ({i})"
            candidate = os.path.join(parent, f"{stem}{suffix}{ext}")
            if not os.path.exists(candidate):
                return candidate
            i += 1

    def nextsync_paste_explorer_item(dest_dir):
        """Paste the shared clipboard into dest_dir (NextSync local explorer).
        Handles both local->local copies and image->local downloads, and
        refreshes the NextSync tree when done."""
        _explorer_paste_into_local(dest_dir, nextsync_refresh_explorer,
                                   add_nextsync_log_window)

    def _run_nextsync_import_task(signals, cancel_event, items):
        """Background worker body for drag-and-drop imports into the NextSync
        explorer. *items* is a list of (src_path, target_path, is_dir) where
        target_path has already been made unique on the UI thread.
        Phase 1 enumerates files/dirs (indeterminate progress), Phase 2 creates
        directories then copies each file with real percentage progress."""
        signals.progress.emit(-1)   # indeterminate while scanning
        all_files = []   # (src_file, dst_file)
        all_dirs  = []   # dst directories to create, parents before children
        for src, target, is_dir in items:
            if cancel_event.is_set():
                break
            signals.status.emit(f"Scanning…\n{src}")
            if not is_dir:
                all_files.append((src, target))
                continue
            all_dirs.append(target)
            for dirpath, _dirnames, filenames in os.walk(src):
                if cancel_event.is_set():
                    break
                rel = os.path.relpath(dirpath, src)
                dst_dir = target if rel == "." else os.path.join(target, rel)
                if rel != ".":
                    all_dirs.append(dst_dir)
                for fname in filenames:
                    all_files.append((os.path.join(dirpath, fname),
                                      os.path.join(dst_dir, fname)))

        if cancel_event.is_set():
            return

        # ---- Phase 2a: create directories (parents already precede children) ----
        for d in all_dirs:
            if cancel_event.is_set():
                break
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                logging.error(f"Failed creating folder {d}: {e}")
                signals.error.emit(f"{timestamp()} | Failed creating folder {d}: {e}")

        if cancel_event.is_set():
            return

        # ---- Phase 2b: copy files ----
        total = max(len(all_files), 1)
        for idx, (src_file, dst_file) in enumerate(all_files):
            if cancel_event.is_set():
                break
            signals.status.emit(f"Copying ({idx + 1}/{total})\n{src_file}")
            signals.progress.emit(int(idx / total * 100))
            try:
                shutil.copy2(src_file, dst_file)
            except OSError as e:
                logging.error(f"Failed copying {src_file} -> {dst_file}: {e}")
                signals.error.emit(f"{timestamp()} | Failed copying {src_file}: {e}")
            signals.progress.emit(int((idx + 1) / total * 100))

    def nextsync_import_external_paths(paths, dest_dir):
        """Copy files/folders dropped from the OS file manager into dest_dir
        (local filesystem) on a background thread with a progress dialog.
        Mirrors the paste logic: never overwrites (name clashes get a
        '-(copy)' suffix) and refreshes the explorer when done."""
        if not dest_dir or not os.path.isdir(dest_dir):
            add_nextsync_log_window(f"{timestamp()} | Import failed: no valid destination folder.")
            return

        # Resolve sources to unique targets up front (quick, UI thread) so the
        # worker just copies. Self-import and existence are checked here too.
        items = []
        for src in paths:
            if not src or not os.path.exists(src):
                continue
            src_is_dir = os.path.isdir(src)
            if src_is_dir:
                src_abs = os.path.abspath(src)
                dest_abs = os.path.abspath(dest_dir)
                # Guard against importing a folder into itself or a subfolder.
                if dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep):
                    add_nextsync_log_window(f"{timestamp()} | Skipped {src}: cannot import a folder into itself.")
                    continue
            base = os.path.basename(src.rstrip("/\\"))
            target = _nextsync_unique_path(os.path.join(dest_dir, base), src_is_dir)
            items.append((src, target, src_is_dir))

        if not items:
            return

        dlg    = HdfProgressDialog("Importing into folder…", host)
        worker = HdfTaskWorker(_run_nextsync_import_task, items)

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_nextsync_log_window)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_import_finished():
            dlg.close()
            nextsync_refresh_explorer()

        worker.signals.finished.connect(_on_import_finished)
        host.threadpool.start(worker)
        dlg.exec()

    def nextsync_delete_explorer_item(file_path, name, is_dir):
        """Delete a file or folder from the NextSync explorer (local filesystem).

        Honours the "Do not prompt for confirmation on deletion" setting: when
        enabled, delete straight away; otherwise ask the user to confirm first
        (a folder warns it removes the folder and all its contents). Refreshes
        the explorer afterwards so the deleted entry disappears.
        """
        if host.settings_no_prompt_on_deletion_checkbox.isChecked():
            confirmed = True
        else:
            if is_dir:
                msg = (ui_tr_now('Delete the folder "{name}" and all of '
                                 'its contents?').format(name=name)
                       + f'\n\n{file_path}')
            else:
                msg = (ui_tr_now('Delete the file "{name}"?')
                       .format(name=name) + f'\n\n{file_path}')
            msg += "\n\n" + (
                ui_tr_now("Deleted files are sent to the Recycle Bin.")
                if _deletes_go_to_recycle_bin()
                else ui_tr_now("This cannot be undone."))
            reply = QMessageBox.question(
                host, ui_tr_now("Confirm deletion"), msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            confirmed = reply == QMessageBox.Yes
        if not confirmed:
            return

        _local_delete_paths_async(
            [(file_path, is_dir)],
            lambda m: add_nextsync_log_window(f"{timestamp()} | {m}"))

    def on_nextsync_file_explorer_path_edited():
        # Typing a folder path into the sync-root box commits it as the new
        # sync root (and navigates the explorer there). Files are rejected:
        # the sync root is always a directory.
        new_path = host.nextsync_file_explorer_path.text().strip()
        if os.path.isdir(new_path):
            norm = new_path.replace("\\", "/")
            if not norm.endswith("/"):
                norm += "/"
            host.nextsync_treeview.setRootIndex(host.nextsync_model.mapFromSource(host.nextsync_filesystem_model.index(norm, 0)))
            set_treeview_properties()
            host.nextsync_treeview.show()
            _nextsync_commit_sync_root(norm)
        else:
            # Restore the previous valid value
            host.nextsync_file_explorer_path.setText(host.left_file_nextsync_explorer_selection_full_filename_path)

    def nextsync_get_fileexplorer_root_selection():
          if host.left_file_nextsync_explorer_selection_full_filename_path != "":
            selected_explorer_item_directory_destination = ""
            if not os.path.isdir(host.left_file_nextsync_explorer_selection_full_filename_path):
                # we are pointing to a file not a directory
                splitted_filepath = host.left_file_nextsync_explorer_selection_full_filename_path.split('/')
                for file_dest_token in range (0, len(splitted_filepath)-2):
                    selected_explorer_item_directory_destination += splitted_filepath[file_dest_token] + "/"
            else:
                selected_explorer_item_directory_destination = host.left_file_nextsync_explorer_selection_full_filename_path
                if not host.left_file_nextsync_explorer_selection_full_filename_path.endswith("/"):
                    selected_explorer_item_directory_destination = selected_explorer_item_directory_destination + "/"

            return selected_explorer_item_directory_destination
          else:
            return ""

    def nextsync_show_sync_buttons_based_on_fileexplorer_content_selection():

        if host.left_file_nextsync_explorer_selection_full_filename_path != "":
            selected_explorer_item_directory_destination = nextsync_get_fileexplorer_root_selection()
            if selected_explorer_item_directory_destination == "":
                return

            # first hide all buttons
            host.nextsync_button_create_syncignore.setVisible(False)
            host.nextsync_button_delete_syncignore.setVisible(False)
            host.nextsync_button_delete_syncpointfile.setVisible(False)

            if os.path.exists(selected_explorer_item_directory_destination + IGNOREFILE) and os.path.isfile(selected_explorer_item_directory_destination + IGNOREFILE):
                # ignore file exists offer to delete it
                host.nextsync_button_delete_syncignore.setVisible(True)
            else:
                # ignore file does not exist offer to create it
                host.nextsync_button_create_syncignore.setVisible(True)

            if os.path.exists(selected_explorer_item_directory_destination + SYNCPOINT) and os.path.isfile(selected_explorer_item_directory_destination + SYNCPOINT):
                # SYNCPOINT file exists offer to delete it
                host.nextsync_button_delete_syncpointfile.setVisible(True)



    def nextsync_create_sample_ignorefile(file):
        try:
            config_array = []
            for cs in IGNOREFILE_DEFAULT_CONTENT:
                config_array.append(cs + '\n')
            with open(file, "w") as config_file:
                config_file.writelines(config_array)
        except Exception as e:
            logging.error(f"Failed creating: {file} Exception: {e}")
            add_nextsync_log_window(f"Failed creating: {file} Exception: {e}")

    def nextsync_create_syncingore_button():
        nextsync_create_sample_ignorefile(nextsync_get_fileexplorer_root_selection() + IGNOREFILE)
        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
        save_configuration_file()

    def nextsync_delete_syncingore_button():
        try:
            os.remove(nextsync_get_fileexplorer_root_selection() + IGNOREFILE)
        except Exception as e:
            logging.error(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + IGNOREFILE} Exception: {e}")
            add_nextsync_log_window(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + IGNOREFILE} Exception: {e}")

        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
        save_configuration_file()

    def nextsync_delete_syncpoint_button():
        try:
            os.remove(nextsync_get_fileexplorer_root_selection() + SYNCPOINT)
        except Exception as e:
            logging.error(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + SYNCPOINT} Exception: {e}")
            add_nextsync_log_window(f"Failed deleting: {nextsync_get_fileexplorer_root_selection() + SYNCPOINT} Exception: {e}")

        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()


    def nextsync_sync_mode_changed():
        # Persist the current "Sync mode" radio selection into the two legacy
        # boolean settings. The radios are exclusive, so at most one of these
        # is "true"; "Sync changed files" (incremental) leaves both "false".
        configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] = (
            "true" if host.nextsync_synconce_checkbox.isChecked() else "false")
        configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] = (
            "true" if host.nextsync_alwayssync_checkbox.isChecked() else "false")
        save_configuration_file()

    def nextsync_slowtransfer_checkbox_statechanged():
        # The payload size itself is derived from this persisted setting at
        # server start (see nextsync_do_server_job) — the old in-place
        # MAX_PAYLOAD rebind here was a dead local and never took effect.
        if host.nextsync_slowtransfer_checkbox.isChecked():
            configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] = "true"
        else:
            configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] = "false"

        save_configuration_file()

    def nextsync_on_treeview_double_clicked(ix):
        # Pure navigation: double-clicking a folder (or "..") only changes
        # the folder being browsed. The sync root (the box below the
        # explorer) is only changed via the "Set current folder as new sync
        # root folder" button or by typing a folder path into the box.
        if not ix.isValid():
            return

        source_ix = host.nextsync_model.mapToSource(ix)
        file_name = host.nextsync_filesystem_model.fileName(source_ix)
        file_path = host.nextsync_filesystem_model.filePath(source_ix)

        if file_name == "..":
            current_root_source = host.nextsync_model.mapToSource(host.nextsync_treeview.rootIndex())
            current_root_path = host.nextsync_filesystem_model.filePath(current_root_source)
            parent_path = os.path.dirname(current_root_path.rstrip("/\\"))
            if not parent_path:
                return
            selected_explorer_item_directory_destination = parent_path.replace("\\", "/") + "/"

        elif host.nextsync_filesystem_model.isDir(source_ix):
            selected_explorer_item_directory_destination = file_path
            if not selected_explorer_item_directory_destination.endswith("/"):
                selected_explorer_item_directory_destination += "/"

        else:
            return

        host.nextsync_treeview.setRootIndex(host.nextsync_model.mapFromSource(host.nextsync_filesystem_model.index(selected_explorer_item_directory_destination, 0)))
        set_treeview_properties()
        host.nextsync_treeview.show()

        _nextsync_update_set_syncroot_button()

    def _nextsync_current_browse_dir():
        """The folder the NextSync left explorer is currently showing."""
        root_src = host.nextsync_model.mapToSource(host.nextsync_treeview.rootIndex())
        path = host.nextsync_filesystem_model.filePath(root_src)
        if not path:
            return ""
        path = path.replace("\\", "/")
        if not path.endswith("/"):
            path += "/"
        return path

    def _nextsync_commit_sync_root(path):
        """Record `path` (a directory, forward slashes, trailing "/") as the
        Classic sync root: shown in the sync-root box, persisted, and picked
        up by the SyncIgnore/SyncPoint buttons and the prepare scan."""
        host.left_file_nextsync_explorer_selection_full_filename_path = path
        host.nextsync_file_explorer_path.setText(path)
        configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = path
        save_configuration_file()
        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection()
        # Re-run the Prepare scan for the new root so the "Ready to sync N
        # files" log stays accurate and Start stays available without an
        # extra Prepare click.
        nextsync_perform_checks_and_prepare_server_start()
        _nextsync_update_set_syncroot_button()

    def _nextsync_update_set_syncroot_button():
        """Offer "Set current folder as new sync root folder" only while the
        browsed folder differs from the sync root shown in the box."""
        current = _nextsync_current_browse_dir()
        root = (host.left_file_nextsync_explorer_selection_full_filename_path
                or "").replace("\\", "/")
        same = (current != "" and root != "" and
                os.path.normcase(current.rstrip("/")) == os.path.normcase(root.rstrip("/")))
        visible = current != "" and not same
        host.nextsync_set_syncroot_button.setVisible(visible)
        # Pulse the offer green while it is on screen (defined by the
        # pane builder; guarded for construction order).
        pulse = getattr(host, "_nextsync_syncroot_pulse_set", None)
        if pulse is not None:
            pulse(visible)

    def _nextsync_on_set_syncroot_clicked():
        folder = _nextsync_current_browse_dir()
        if not folder:
            return
        if QMessageBox.question(
                host, ui_tr_now("Set sync root"),
                ui_tr_now("Set this folder as the new sync root?") + "\n\n" + folder,
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes) == QMessageBox.Yes:
            _nextsync_commit_sync_root(folder)

    # Consumed by bare name later in __init__ (re-bound at the call site).
    host.on_nextsync_file_explorer_path_edited = on_nextsync_file_explorer_path_edited
    host.nextsync_on_treeview_context_menu = nextsync_on_treeview_context_menu
    host.nextsync_on_treeview_double_clicked = nextsync_on_treeview_double_clicked
    host.nextsync_rename_explorer_item = nextsync_rename_explorer_item
    host.nextsync_delete_explorer_item = nextsync_delete_explorer_item
    host.nextsync_import_external_paths = nextsync_import_external_paths
    host._nextsync_unique_path = _nextsync_unique_path
    host._run_nextsync_import_task = _run_nextsync_import_task
    host.nextsync_sync_mode_changed = nextsync_sync_mode_changed
    host.nextsync_slowtransfer_checkbox_statechanged = (
        nextsync_slowtransfer_checkbox_statechanged)
    host.nextsync_create_syncingore_button = nextsync_create_syncingore_button
    host.nextsync_delete_syncingore_button = nextsync_delete_syncingore_button
    host.nextsync_delete_syncpoint_button = nextsync_delete_syncpoint_button
    host._nextsync_on_set_syncroot_clicked = _nextsync_on_set_syncroot_clicked
    host._nextsync_update_set_syncroot_button = _nextsync_update_set_syncroot_button
    host.nextsync_current_view_dir = nextsync_current_view_dir
    host.nextsync_show_sync_buttons_based_on_fileexplorer_content_selection = (
        nextsync_show_sync_buttons_based_on_fileexplorer_content_selection)


def build_nextsync_server_job(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    add_nextsync_log_window,
    nextsync_hide_start_cancel_buttons,
    nextsync_show_start_cancel_buttons,
):
    """Warnings/IP/cancel/conflict-prompt + the classic server job body."""
    def nextsync_warnings():
        add_nextsync_log_window ("")

        selected_nextsync_explorer_sync_root_directory = ""

        if host.left_file_nextsync_explorer_selection_full_filename_path:
            splitted_filepath = host.left_file_nextsync_explorer_selection_full_filename_path.split('/')
            if not os.path.isdir(host.left_file_nextsync_explorer_selection_full_filename_path):
            # if '.' in dest_file_content:
                for file_dest_token in range (0, len(splitted_filepath)-1):
                    selected_nextsync_explorer_sync_root_directory += splitted_filepath[file_dest_token] + "/"
            else:
                selected_nextsync_explorer_sync_root_directory = host.left_file_nextsync_explorer_selection_full_filename_path + "/"

        add_nextsync_log_window ("Using " + selected_nextsync_explorer_sync_root_directory + " as sync root")

        if not os.path.isfile(selected_nextsync_explorer_sync_root_directory + IGNOREFILE):
            add_nextsync_log_window ("Warning! Ignore file " + IGNOREFILE + " not found in directory. All files will be synced, possibly including this file.")
        if not os.path.isfile(selected_nextsync_explorer_sync_root_directory + SYNCPOINT):
            add_nextsync_log_window ("Sync point file " + SYNCPOINT + " not found, syncing all files regardless of timestamp.")

        # Show the Start button straight away; the (potentially expensive)
        # recursive file scan runs on a worker thread and logs the
        # "Ready to sync N files" line when it finishes, so the UI never
        # blocks even on very large folders.
        nextsync_show_start_cancel_buttons()
        host.nextsync_prepare_server.setVisible(False)

        if not (selected_nextsync_explorer_sync_root_directory and os.path.isdir(selected_nextsync_explorer_sync_root_directory)):
            add_nextsync_log_window ("")
            add_nextsync_log_window ("Navigate to a folder in the left local file explorer, press 'Set current folder as new sync root folder' to choose a sync root and then press the 'Start Classic NextSync server' button.")
            add_nextsync_log_window ("")
            return

        # Bump the generation token so a scan whose sync root was changed
        # before it finished is discarded instead of logging a stale count.
        host._nextsync_scan_generation += 1
        scan_gen = host._nextsync_scan_generation
        root_dir = selected_nextsync_explorer_sync_root_directory
        scan_result = {}

        _always_sync = host.nextsync_alwayssync_checkbox.isChecked()

        def _scan_fn(signals, cancel_event, _root=root_dir, _holder=scan_result,
                     _always=_always_sync):
            files = getFileList(_root, _always)
            _holder["count"] = len(files)
            _holder["total"] = sum(x[1] for x in files)

        scan_worker = HdfTaskWorker(_scan_fn)

        def _on_scan_done(_gen=scan_gen, _holder=scan_result):
            # Release our keep-alive reference now that the slot is running.
            host._nextsync_scan_workers.discard(scan_worker)
            # Discard results from a superseded scan (sync root changed since).
            if _gen != host._nextsync_scan_generation:
                return
            if "count" not in _holder:
                return   # scan raised — nothing reliable to report
            count = _holder["count"]
            total = _holder["total"]
            if count < 10 and total < 100000:
                severity = "Note"
            elif count < 100 and total < 1000000:
                severity = "Warning"
            else:
                severity = "WARNING"
            add_nextsync_log_window (f"{severity}: Ready to sync {count} files, {total/1024:.2f} kilobytes.")
            add_nextsync_log_window ("")

        # Keep the worker (and its signals) alive until _on_scan_done runs,
        # so the queued cross-thread `finished` slot can't be dropped by GC.
        host._nextsync_scan_workers.add(scan_worker)
        scan_worker.signals.finished.connect(_on_scan_done)
        host.threadpool.start(scan_worker)

    def nextsync_show_ip_info():
        add_nextsync_log_window ("------------------------------------------", False)
        add_nextsync_log_window ("NextSync server, protocol version: " + VERSION, False)
        add_nextsync_log_window ("", False)
        # detect_local_ipv4 never raises: a Linux box with no network route
        # used to crash the whole startup here on the raw 8.8.8.8 connect.
        hostname, aliases, ips, primary = detect_local_ipv4()
        if hostname:
            add_nextsync_log_window ("Running on host:\n    " + str(hostname), False)
        if aliases:
            add_nextsync_log_window ("Aliases:", False)
            for x in aliases:
                add_nextsync_log_window ("    " + str(x), False)
        if ips:
            add_nextsync_log_window ("IP addresses:", False)
            for x in ips:
                add_nextsync_log_window ("    " + str(x), False)
        if primary is not None:
            add_nextsync_log_window ("Primary IP:\n    " + str(primary), False)
        elif not ips or ips[0].startswith("127"):
            add_nextsync_log_window (
                "No network detected - connect to Wi-Fi/Ethernet to see "
                "the address your Next should sync to.", False)

    def nextsync_cancel_server_job():
        nextsync_hide_start_cancel_buttons()
        host.nextsync_prepare_server.setVisible(True)
        save_configuration_file()

    def _on_nextsync_conflict_prompt(name, path, holder, ev):
        """UI-thread slot: ask the user how to handle a received file/dir that
        already exists locally. Records one of overwrite/overwrite_all/
        ignore/ignore_all in *holder* and unblocks the worker via *ev*."""
        try:
            box = QMessageBox(host)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("File or directory exists")
            box.setText("File or directory already exists locally.")
            box.setInformativeText(
                f"{path}\n\n"
                "Tip: set a default for this in Settings → "
                "\"NextSync — when a sent file or directory exists locally\".")
            b_ow_one = box.addButton("Overwrite local file (one time)", QMessageBox.AcceptRole)
            b_ow_all = box.addButton("Overwrite local file (always in this sync)", QMessageBox.AcceptRole)
            b_ig_one = box.addButton("Ignore (one time)", QMessageBox.RejectRole)
            b_ig_all = box.addButton("Ignore (always in this sync)", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_ow_one:
                holder["result"] = "overwrite"
            elif clicked is b_ow_all:
                holder["result"] = "overwrite_all"
            elif clicked is b_ig_all:
                holder["result"] = "ignore_all"
            else:
                holder["result"] = "ignore"   # Ignore (one time) or dialog closed
        finally:
            ev.set()

    # Created on the UI thread so its queued signal delivers the prompt there.
    host._nextsync_conflict_signals = NextSyncConflictSignals()
    host._nextsync_conflict_signals.prompt.connect(_on_nextsync_conflict_prompt)

    def _nextsync_ask_conflict(name, path):
        """Called from the receive worker thread: block until the user picks
        how to handle an existing local file/dir. Returns one of
        'overwrite', 'overwrite_all', 'ignore', 'ignore_all'."""
        holder = {}
        ev = threading.Event()
        host._nextsync_conflict_signals.prompt.emit(name, path, holder, ev)
        ev.wait()
        return holder.get("result", "ignore")

    def nextsync_do_server_job(progress_callback, status_callback=None, cancel_flag=None, serve_folder=None):
        """Adapter for the classic sync server, which now lives in
        zxnu_workers.run_classic_sync_server: computes the sync root
        (incl. the "Send via NextSync" serve_folder one-shots), drives the
        pane-side UI state around the run, and injects the Settings reads
        and prompts the protocol loop needs. Kept under the historical
        name/signature for the Worker(...) launch sites."""
        selected_root = ""
        force_sync_once = False
        # Only touch the pane progress bar when running from the pane (no
        # cancel_flag = pane invocation), exactly as before.
        if cancel_flag is None:
            host.nextsync_progressbar.setValue(0)
            host.nextsync_progressbar.setVisible(True)
            host.nextsync_button_create_syncignore.setVisible(False)
            host.nextsync_button_delete_syncignore.setVisible(False)
            host.nextsync_button_delete_syncpointfile.setVisible(False)
        nextsync_show_ip_info()
        if serve_folder and os.path.isdir(serve_folder):
            # Caller specified an exact folder (e.g. downloads/comix): the
            # "Send via NextSync" buttons mean exactly one transfer.
            selected_root = serve_folder.rstrip("/\\") + "/"
            force_sync_once = True
        elif host.left_file_nextsync_explorer_selection_full_filename_path:
            splitted_filepath = host.left_file_nextsync_explorer_selection_full_filename_path.split('/')
            if not os.path.isdir(host.left_file_nextsync_explorer_selection_full_filename_path):
                for file_dest_token in range(0, len(splitted_filepath) - 1):
                    selected_root += splitted_filepath[file_dest_token] + "/"
            else:
                selected_root = host.left_file_nextsync_explorer_selection_full_filename_path + "/"

        def _conflict_policy():
            pol = configuration_dictionary.get(
                SETTING_NEXTSYNC_SEND_CONFLICT, DEFAULT_NEXTSYNC_SEND_CONFLICT)
            return pol if pol in ("prompt", "overwrite", "ignore") else DEFAULT_NEXTSYNC_SEND_CONFLICT

        # Payload size honours the persisted "slow transfer" setting. (The
        # old in-place MAX_PAYLOAD rebind in the checkbox handler never
        # left that function, so the toggle had no runtime effect — fixed
        # by deriving the size here, per run.)
        _slow = configuration_dictionary.get(
            SETTING_NEXTSYNC_SLOWTRANSFER, "").strip().lower() in ("true", "1")

        run_classic_sync_server(
            selected_root,
            add_nextsync_log_window,
            progress_callback=progress_callback,
            status_callback=status_callback,
            cancel_flag=cancel_flag,
            force_sync_once=force_sync_once,
            sync_once=lambda: host.nextsync_synconce_checkbox.isChecked(),
            always_sync=lambda: host.nextsync_alwayssync_checkbox.isChecked(),
            get_conflict_policy=_conflict_policy,
            ask_conflict=_nextsync_ask_conflict,
            max_payload=256 if _slow else 1024,
            verbose=ZX_NEXT_UNITE_VERBOSE_LOG_MODE,
            set_session_active=lambda on: setattr(host, "_nextsync_transfer_active", on),
            pane_progress=(None if cancel_flag is not None
                           else host.nextsync_progressbar.setValue),
        )

        nextsync_hide_start_cancel_buttons()
        host.nextsync_prepare_server.setVisible(True)
        if cancel_flag is None:
            host.nextsync_progressbar.setVisible(False)

    # Consumed by bare name later in __init__ (re-bound at the call site).
    host.nextsync_warnings = nextsync_warnings
    host.nextsync_do_server_job = nextsync_do_server_job
    host.nextsync_cancel_server_job = nextsync_cancel_server_job
    host.nextsync_show_ip_info = nextsync_show_ip_info
