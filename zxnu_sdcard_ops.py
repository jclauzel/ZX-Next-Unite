"""zxnu_sdcard_ops.py — SD-card tab operation layer.

Strangler extraction from MainWindow.__init__ (builder-function seam; the
SD-card WIDGETS live in zxnu_sdcard_explorer.py). Four builders, each called
at its block's historical __init__ position because the original code is
interleaved with the NextSync op blocks (zxnu_nextsync_ops.py):

* build_sdcard_utils(host, ...)          — is_hdfmonkey_present, the image
  history, the transfer/hdfmonkey/load-image-hint animations, load_image,
  the three log windows, new-folder chain, select_image,
  download_nextzxos_image, the usage gauge + writability checks,
  execute_hdf_monkey and the shell-command helpers.
* build_image_edit_ops(host, ...)        — image deletion (confirm dialog +
  worker) and in-image rename (task + dialog).
* build_local_explorer_ops(host, ...)    — the SD tab's LOCAL pane ops:
  create-directory/rename helpers (shared with the NextSync classic
  explorer), context menu, watched-delete, zip/unzip, import.
* build_transfer_clipboard_ops(host, ...) — image ⇄ disk transfers, the
  shared cross-explorer clipboard (OS-clipboard synced), in-image
  copy/paste, remote zip/unzip, the image context menu and the
  SdCardExplorerPane delegate wrappers.

The module globals right_disk_image_explorer_content /
right_disk_image_selected_files stay in zxnu_main (other monolith closures
still write them): reads arrive through the _right_disk_content getter and
writes through _set_right_disk_content/_set_right_disk_selected setter
hooks, with the original ``global`` declarations dropped. Everything the
blocks assigned to ``self`` is written to ``host``; closures consumed by
bare name elsewhere in __init__ are exposed on host and re-bound at the
call sites. See CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import logging
import os
import pathlib
import platform
import shlex
import shutil
import stat
import struct
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile

from PySide6.QtCore import (Qt, QTimer)
from PySide6.QtGui import (QAction, QGuiApplication)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
    QDialog, QDialogButtonBox, QFileDialog, QFileSystemModel, QInputDialog,
    QLabel, QLineEdit, QListWidgetItem, QMenu, QMessageBox, QProgressBar,
    QVBoxLayout)

from zxnu_config import *
from zxnu_workers import *
from zxnu_i18n import ui_tr_now
from zxnu_sdcard_explorer import (SdCardExplorerPane, IMG_PATH_ROLE,
                                  IMG_ISDIR_ROLE)


def build_sdcard_utils(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    set_all_buttons_disabled,
    set_all_buttons_enabled,
    enable_image_selection,
    _hdfmonkey_binary_found,
    _right_disk_content,
    _set_right_disk_content,
    _set_right_disk_selected,
    image_confirm_deletion_dialog,
    image_delete_files,
    _nextsync_update_set_syncroot_button,
    generate_disk_file_path,
    image_clear_model,
    image_load_root,
    update_disk_manager_widget_table,
):
    """SD-card utility closures: load pipeline, logging, dialogs, hdfmonkey."""
    def delete_files_button_show_confirmation_buttons():
        if not host.image_selected_path:
            logging.info("Please select an image file or folder first to delete!")
            add_main_log_window(ui_tr_now(
                "Please select an image file or folder first to delete!"))
            return
        # When "Do not prompt for confirmation on deletion" is enabled, delete
        # straight away; otherwise ask for confirmation via a popup dialog.
        if host.settings_no_prompt_on_deletion_checkbox.isChecked():
            image_delete_files()
            return
        image_confirm_deletion_dialog()


    def is_hdfmonkey_present(silent=False):

        # Pure probe (used at startup and after install): never pop the
        # "install hdfmonkey?" dialog from here — startup already surfaces the
        # download button, and the dialog is reserved for real user actions.
        # *silent* keeps a failed probe out of the log/console: at startup the
        # bundled itch.io CSpect hdfmonkey hasn't been discovered yet, so a
        # bare-"hdfmonkey"-not-on-PATH failure here is misleading — the
        # subsequent downloads/cspect scan may still find a usable copy.
        hdfmonkeyexecresult = execute_hdf_monkey("", "", silent=silent, prompt_if_missing=False)

        try:
            # Key presence off hdfmonkey's usage banner in stdout, NOT the
            # return code: the empty/unknown probe command makes hdfmonkey
            # exit non-zero (the jjjs 0.5.7 build returns 1), so a returncode
            # check would misreport a perfectly working build as missing.
            command_execution = str(hdfmonkeyexecresult.stdout)
            if "hdfmonkey help" in command_execution:
                return True
            # It ran but produced no recognisable hdfmonkey banner. Only warn
            # when it exited cleanly — a genuine not-found / other failure is
            # already surfaced by execute_hdf_monkey.
            if hdfmonkeyexecresult.returncode == 0:
                add_main_log_window(ui_tr_now(
                    "Failed executing hdfmonkey, please make sure it is "
                    "installed in the same local directory as zx-next-unite."))
            return False
        except Exception as e:
            logging.error(f"Failed executing hdfmonkey, please make sure it is installed in the same local directory as zx-next-unite.... {e}")
            add_main_log_window(ui_tr_now(
                "Failed executing hdfmonkey, please make sure it is "
                "installed in the same local directory as zx-next-unite.") + f" {e}")
            return False

    def _add_to_image_history(path: str):
        """Add *path* to the top of the image history combo and persist it.
        Duplicates are removed so each path appears only once.
        The list is capped at MAX_IMAGE_HISTORY entries."""
        if not path or path == '""':
            return
        # Remove any existing occurrence so the new one goes to the top.
        # history_index, not findText: findText is case-SENSITIVE, so
        # loading C:\temp\x.img after C:\TEMP\x.img made two rows for one
        # file — and the '✕' / Delete removal (9.6.0) finds only the first
        # of the pair, leaving its twin in the dropdown for good.
        existing_index = host.imageinput.history_index(path)
        if existing_index != -1:
            host.imageinput.removeItem(existing_index)
        host.imageinput.insertItem(0, path)
        # Keep within the max size (skip index 0 which is the current text placeholder)
        while host.imageinput.count() > MAX_IMAGE_HISTORY:
            host.imageinput.removeItem(host.imageinput.count() - 1)
        host.imageinput.setCurrentText(path)
        save_configuration_file()

    def _start_transfer_idle_animation():
        """Start a soft, continuously-looping green 'breathing' background pulse
        on the two transfer-arrow buttons (Send '->:' / Get ':<-'). It runs the
        whole time the SD-card tab is active so the controls gently draw the eye
        even when nothing else is going on.

        The pulse is driven by a plain QTimer that rewrites each button's
        background colour — this is always visible, unlike a QGraphicsEffect,
        and fits the tiny (~30px) buttons without changing their text. Safe to
        call repeatedly; it restarts cleanly."""
        _stop_transfer_idle_animation()

        # Triangle wave over (2*steps) ticks → a smooth fade up and down. The
        # two buttons are offset by half a cycle so they breathe out of phase.
        steps = 22
        phase = {"n": 0}

        def _alpha_for(pos):
            pos %= (2 * steps)
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            return int(150 * tri)

        def _tick():
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            for btn, off in ((host.button_to_image, 0), (host.button_to_disk, steps)):
                a = _alpha_for(phase["n"] + off)
                try:
                    btn.setStyleSheet(
                        "QPushButton { "
                        f"background-color: rgba(46,204,113,{a}); "
                        f"border: 1px solid rgba(46,204,113,{min(a + 60, 255)}); "
                        "border-radius: 4px; }")
                except RuntimeError:
                    pass

        timer = QTimer(host)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        host._transfer_anim_timer = timer

    def _stop_transfer_idle_animation():
        """Stop the breathing pulse and restore the buttons' normal appearance."""
        timer = getattr(host, "_transfer_anim_timer", None)
        if timer is not None:
            timer.stop()
            host._transfer_anim_timer = None
        for btn in (host.button_to_image, host.button_to_disk):
            try:
                btn.setStyleSheet("")
            except RuntimeError:
                pass

    def _start_hdfmonkey_button_animation():
        """Pulse the 'Download and install HDF Monkey' button in a soft yellow
        'breathing' glow while hdfmonkey is missing, so it draws the eye — the
        amber counterpart to the green transfer-arrow pulse
        (_start_transfer_idle_animation).

        The timer polices itself: on each tick it checks whether the button is
        still on offer and stops (restoring the normal look) the moment the
        button is explicitly hidden — which happens as soon as hdfmonkey is
        installed or otherwise detected. So callers only ever need to *start*
        it (from show_hdf_monkey_download_and_install_buttons); every place
        that hides the button on a successful install/detection stops it for
        free. Safe to call repeatedly — a second call is a no-op while it runs.
        """
        btn = host.download_and_install_hdfmonkey_button
        if getattr(host, "_hdfmonkey_btn_anim_timer", None) is not None:
            return  # already pulsing

        steps = 22
        phase = {"n": 0}

        def _alpha_for(pos):
            pos %= (2 * steps)
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            return int(150 * tri)

        def _tick():
            # Auto-stop once the button is no longer offered (hdfmonkey found
            # / installed) so a stray timer can't keep restyling a hidden or
            # deleted widget. isHidden() (not isVisible()) is used so merely
            # switching away from the SD-card tab doesn't cancel the pulse.
            try:
                hidden = btn.isHidden()
            except RuntimeError:
                hidden = True
            if hidden:
                _stop_hdfmonkey_button_animation()
                return
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            a = _alpha_for(phase["n"])
            try:
                btn.setStyleSheet(
                    "QPushButton { "
                    f"background-color: rgba(241,196,15,{a}); "
                    f"border: 1px solid rgba(241,196,15,{min(a + 60, 255)}); "
                    "border-radius: 4px; }")
            except RuntimeError:
                _stop_hdfmonkey_button_animation()

        timer = QTimer(host)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        host._hdfmonkey_btn_anim_timer = timer

    def _stop_hdfmonkey_button_animation():
        """Stop the yellow pulse and restore the download button's normal look."""
        timer = getattr(host, "_hdfmonkey_btn_anim_timer", None)
        if timer is not None:
            timer.stop()
            host._hdfmonkey_btn_anim_timer = None
        try:
            host.download_and_install_hdfmonkey_button.setStyleSheet("")
        except RuntimeError:
            pass

    def _load_image_hint_wanted():
        """True while the 'load an image' hint applies: at least one
        emulator (CSpect or MAME) is usable but no disk image is loaded.
        Mirrors _maybe_show_no_image_toast's emulator gating — with no
        emulator installed the detection toast's "install one" advice is
        the right message, not this pulse."""
        if _right_disk_content():
            return False
        _cspect_found = getattr(host, "_cspect_executable_path", None) is not None
        return _cspect_found or host._mame_usable()

    def _start_load_image_hint_animation():
        """Pulse 'Select NextZXOS disk Image' and 'Download NextZXOS Image' in the
        same soft yellow 'breathing' glow as the hdfmonkey install button
        while an emulator is ready but no disk image is loaded — the hint
        that picking an image is the user's next step.

        The timer polices itself: on each tick it re-checks the condition
        and stops (restoring the normal look) the moment an image finishes
        loading. So callers only ever need to *start* it at the moments the
        hint may become due — no image at startup, a failed or cleared
        load, or an emulator detected/installed later. Safe to call
        repeatedly — a no-op while it already runs or while the hint
        doesn't apply."""
        if getattr(host, "_load_image_hint_anim_timer", None) is not None:
            return
        if not _load_image_hint_wanted():
            return

        steps = 22
        phase = {"n": 0}

        def _alpha_for(pos):
            pos %= (2 * steps)
            tri = pos / steps if pos <= steps else (2 * steps - pos) / steps
            return int(150 * tri)

        def _tick():
            if not _load_image_hint_wanted():
                _stop_load_image_hint_animation()
                return
            phase["n"] = (phase["n"] + 1) % (2 * steps)
            # Half a cycle apart so the two buttons breathe out of phase,
            # exactly like the transfer arrows.
            for btn, off in ((host.selectimage, 0), (host.downloadimage, steps)):
                a = _alpha_for(phase["n"] + off)
                try:
                    btn.setStyleSheet(
                        "QPushButton { "
                        f"background-color: rgba(241,196,15,{a}); "
                        f"border: 1px solid rgba(241,196,15,{min(a + 60, 255)}); "
                        "border-radius: 4px; }")
                except RuntimeError:
                    _stop_load_image_hint_animation()
                    return

        timer = QTimer(host)
        timer.setInterval(55)
        timer.timeout.connect(_tick)
        timer.start()
        host._load_image_hint_anim_timer = timer

    def _stop_load_image_hint_animation():
        """Stop the yellow hint pulse and restore both buttons' normal look."""
        timer = getattr(host, "_load_image_hint_anim_timer", None)
        if timer is not None:
            timer.stop()
            host._load_image_hint_anim_timer = None
        for btn in (host.selectimage, host.downloadimage):
            try:
                btn.setStyleSheet("")
            except RuntimeError:
                pass

    def _maybe_show_no_image_toast():
        """One-shot (per session) yellow advisory pointing at the image
        picker. Shown ONLY when at least one emulator (CSpect or MAME) is
        actually installed — with none installed the detection toast's
        "install one" advice is the right message instead — and only while
        no disk image is selected. Called from load_image's no-image branch
        and from the emulator scan (an emulator found *after* the startup
        load still deserves the hint). Deferred so it positions against the
        shown window at startup; 30 s so it survives a look around the UI,
        OK to dismiss."""
        if getattr(host, "_no_image_toast_shown", False):
            return
        if (host.imageinput.currentText() or "").strip().strip('"'):
            return
        _cspect_found = getattr(host, "_cspect_executable_path", None) is not None
        if not (_cspect_found or host._mame_usable()):
            return
        host._no_image_toast_shown = True
        QTimer.singleShot(800, lambda: host._show_toast(
            "⚠  No disk image selected/found for your emulator",
            "To start an emulator please select first a disk image "
            "at the top of the screen on the SD Card Utility tab.",
            variant="yellow", duration_ms=30000))

    def load_image(on_done=None):
        """Select and load the disk image named in the image-path combo.

        The actual directory listing runs on a worker thread (see
        image_load_root), so this returns immediately rather than blocking the
        UI thread while hdfmonkey reads the image. *on_done* (optional) is
        invoked on the UI thread with True/False once loading completes."""

        global right_disk_image_explorer_path

        # Tidy whatever is in the box (typed, pasted, picked or restored):
        # drop stray surrounding quotes and, on Windows, show native
        # backslash separators. Reflect the cleaned value back into the box
        # so the user sees e.g. C:\temp\next.img rather than "C:/temp\next.img".
        # blockSignals avoids re-entering load_image while we rewrite the text.
        _clean_image_path = normalize_sd_image_path(host.imageinput.currentText())
        if _clean_image_path != host.imageinput.currentText():
            host.imageinput.blockSignals(True)
            host.imageinput.setCurrentText(_clean_image_path)
            host.imageinput.blockSignals(False)

        # Populate right image path content
        host.right_disk_image_path = _clean_image_path

        right_disk_image_explorer_path = []
        _set_right_disk_content([])
        # image_clear_model() bumps the load generation, invalidating any
        # in-flight listing from a previous image so it can't repopulate the
        # tree we are about to rebuild.
        image_clear_model()

        if host.right_disk_image_path and host.right_disk_image_path != '""':
            # Lock the controls while the image is being read; the load
            # callback restores them to the right state for success/failure.
            set_all_buttons_disabled()
            host.diskimageexplorerpathinput.setText("Loading image…")

            def _after(success):
                if success:
                    host.diskimageexplorerpathinput.setText(generate_disk_file_path().replace('//', '/'))
                    # Probe BEFORE set_all_buttons_enabled: that call ends
                    # in _update_*_controls, which reads this verdict to
                    # decide whether the two Launch buttons come back.
                    _probe = getattr(host, "_probe_image_write_access", None)
                    if _probe is not None:
                        _probe(host.right_disk_image_path, announce=True)
                    set_all_buttons_enabled()
                    # set_all_buttons_enabled re-gates the two Launch
                    # BUTTONS; the emulator strips are built from the same
                    # answer but nothing on this path rebuilds them, so a
                    # tab that went blocked under "no image selected" would
                    # stay greyed and inert beside a live button.
                    _regate = getattr(host, "_refresh_emulator_launchability",
                                      None)
                    if _regate is not None:
                        _regate()
                    _add_to_image_history(host.right_disk_image_path)
                    # Kick the idle pulse so it's running right after a load,
                    # not only when the tab is (re)entered — and retire the
                    # yellow "load an image" hint, its job is done.
                    _stop_load_image_hint_animation()
                    _start_transfer_idle_animation()
                    # Wizzy's one-time starter-pack suggestion (deferred a
                    # beat so the load UI settles first; the wizard itself
                    # gates on enabled/visible/once flags).
                    _wiz = getattr(host, "_wizard", None)
                    if _wiz is not None:
                        QTimer.singleShot(
                            1200, lambda: _wiz.on_image_loaded())
                else:
                    logging.error(f"Failed loading image :{host.right_disk_image_path}.")
                    add_main_log_window(ui_tr_now(
                        "Failed loading image: {path}.").format(
                            path=host.right_disk_image_path))
                    set_all_buttons_disabled()
                    enable_image_selection()
                    _update_image_usage_gauge("")
                if on_done is not None:
                    on_done(success)

            image_load_root(_after)
            return

        set_all_buttons_disabled()
        enable_image_selection()
        _update_image_usage_gauge("")
        # The image is gone from the box, so its cached "in use" verdict is
        # meaningless - and keeping it would grey the Launch buttons for
        # whatever is picked next if it happens to be the same file.
        _forget = getattr(host, "_forget_image_write_state", None)
        if _forget is not None:
            _forget(_clean_image_path)
        # Unloading takes the image away from the strips too - they must go
        # back to "select an image first", not keep offering a launch.
        _regate = getattr(host, "_refresh_emulator_launchability", None)
        if _regate is not None:
            _regate()
        # Forget what was selected INSIDE the image that just went: the load
        # branch resets these, the unload branch never did. Harmless while
        # unloading was rare; forgetting a remembered path (9.6.0) makes it
        # an everyday move, and a stale selection is what the in-image
        # delete/rename would act on.
        host.image_selected_path = ""
        host.image_selected_is_dir = False
        _set_right_disk_selected([])
        # Make the launch-button gating discoverable: with no image selected
        # the Launch buttons stay greyed out (CSpect needs the mounted image
        # for -mmc=, MAME boots the image file directly), and the only other
        # hint is a hover tooltip on the disabled buttons.
        add_main_log_window(ui_tr_now(
            "No SD-card disk image selected — pick or create a .img/.hdf "
            "at the top of this tab to unlock the emulator Launch buttons."))
        # Same hint as a yellow advisory toast (see the helper for the
        # emulator-installed gating).
        _maybe_show_no_image_toast()

        if on_done is not None:
            on_done(False)

    def apply_file_extension_filter_nextsync():
        text = host.nextsync_filtertext.text().strip()
        host.nextsync_model.setFilterFixedString(text)
        set_treeview_properties()
        host.nextsync_treeview.show()

    def add_main_log_window(string_to_log:str):
        newItem = QListWidgetItem()
        newItem.setText(string_to_log)
        host.listWidgetLog.insertItem(0, newItem)

        # Mirror into the optional retro 8-bit pygame log (terminal-style,
        # newest at the bottom) whenever it has been built.
        retro = getattr(host, "_main_retro_log", None)
        if retro is not None:
            try:
                retro.append(string_to_log)
            except Exception:
                pass

    def add_nextsync_log_window(string_to_log:str, from_top:bool = True):

        newItem = QListWidgetItem()
        newItem.setText(string_to_log)
        if from_top:
            host.nextsync_log.insertItem(0, newItem)
        else:
            host.nextsync_log.insertItem(host.nextsync_log.count(), newItem)

        # Mirror into the optional retro 8-bit pygame log (terminal-style,
        # newest at the bottom) whenever it has been built.
        retro = getattr(host, "_nextsync_retro_log", None)
        if retro is not None:
            try:
                retro.append(string_to_log)
            except Exception:
                pass

        # And into the Remote Explorer view's mini log (both siblings),
        # so bridge/server activity is traceable without flipping to the
        # Classic tab. Bounded: a long session must not grow it forever.
        mini = getattr(host, "_re_mini_log", None)
        if mini is not None:
            try:
                if from_top:
                    mini.insertItem(0, string_to_log)
                else:
                    mini.addItem(string_to_log)
                while mini.count() > 500:
                    mini.takeItem(mini.count() - 1)
            except Exception:
                pass
        mini_r = getattr(host, "_re_mini_retro", None)
        if mini_r is not None:
            try:
                mini_r.append(string_to_log)
            except Exception:
                pass

    def add_help_content(string_to_log:str, from_top:bool = True):

        newItem = QListWidgetItem()
        newItem.setText(string_to_log)
        if from_top:
            host.listWidgetHelp.insertItem(0, newItem)
        else:
            host.listWidgetHelp.insertItem(host.listWidgetHelp.count(), newItem)

        # Mirror into the optional retro 8-bit pygame console (terminal-style,
        # newest at the bottom) whenever it has been built.
        retro = getattr(host, "_help_retro_log", None)
        if retro is not None:
            try:
                retro.append(string_to_log)
            except Exception:
                pass

    def set_treeview_properties():
        host.treeview.setSortingEnabled(True)
        host.treeview.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        # ExtendedSelection, NOT SingleSelection: SdCardExplorerPane builds
        # the local tree multi-select (multi-item drags, Ctrl-A) and this
        # refresh hook runs after every navigation — the old SingleSelection
        # here silently downgraded the tree moments after construction,
        # which is exactly why Ctrl-A "did nothing" on the SD Card tab.
        # The classic NextSync tree below never opted into multi-select,
        # so its line keeps the Qt default it always had.
        host.treeview.setSelectionMode(QAbstractItemView.ExtendedSelection)
        host.nextsync_treeview.setSortingEnabled(True)
        host.nextsync_treeview.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        host.nextsync_treeview.setSelectionMode(QAbstractItemView.SingleSelection)


    def image_newfolder():


        if _right_disk_content():  # check that we have an image content first
            # hide create folder and delete folder buttons
            host.button_new_folder.setVisible(False)
            host.button_rename.setVisible(False)
            host.button_delete_files.setVisible(False)
            host.new_folder_input.setVisible(True)
            host.button_create_directory.setVisible(True)
            host.button_create_directory_cancel.setVisible(True)
        else:
            logging.info("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image file first !"))

        save_configuration_file()

    def image_newfolder_cancel():


        if _right_disk_content():  # check that we have an image content first
            # hide create folder and delete folder buttons
            host.button_new_folder.setVisible(True)
            host.button_rename.setVisible(True)
            host.button_delete_files.setVisible(True)
            host.new_folder_input.setVisible(False)
            host.button_create_directory.setVisible(False)
            host.button_create_directory_cancel.setVisible(False)
        else:
            logging.info("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image file first !"))

        save_configuration_file()

    def image_invalid_folder_name(name):
        """Return an error message if *name* is not a usable folder name, else ""."""
        if name.strip() == "":
            return "Please enter a folder name."
        for not_allowed_chars in DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS:
            if not_allowed_chars in name:
                nachars = "".join(DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS)
                return f"These characters are not allowed: {nachars}"
        return ""

    def image_create_folder_named(name):
        """Create folder *name* inside the current target directory and
        refresh the tree. Assumes *name* has already been validated."""
        directory_to_create = (generate_disk_file_path() + "/" + name).replace("//", "/")
        hdfmonkeyexecresult = execute_hdf_monkey("mkdir", host.right_disk_image_path, extra_argv=[directory_to_create])
        if hdfmonkeyexecresult.returncode != 0:
            logging.error(f"Failed creating directory - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
            add_main_log_window(f"Failed creating directory - hdfmonkey result code: {hdfmonkeyexecresult.returncode}")
        update_disk_manager_widget_table()

    def image_newfolder_create():

        directory_to_create = host.new_folder_input.text().strip()

        error = image_invalid_folder_name(directory_to_create)
        if error:
            logging.warning(error)
            add_main_log_window(error)
            return

        host.button_new_folder.setVisible(True)
        host.button_rename.setVisible(True)
        host.button_delete_files.setVisible(True)
        host.new_folder_input.setVisible(False)
        host.button_create_directory.setVisible(False)
        host.button_create_directory_cancel.setVisible(False)

        image_create_folder_named(directory_to_create)

    def image_newfolder_dialog():
        # Popup dialog used by the tree's right-click "New Folder" action.

        if not _right_disk_content():
            logging.info("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image file first !"))
            return

        nachars = "".join(DIRECTORY_CREATION_NOT_ALLOWED_CHARACTERS)

        dialog = QDialog(host)
        dialog.setWindowTitle(ui_tr_now("Create New Folder"))
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)

        dest = generate_disk_file_path().replace("//", "/")
        info_label = QLabel(f"Create a new folder in:  {dest}", dialog)
        layout.addWidget(info_label)

        name_input = QLineEdit(dialog)
        name_input.setPlaceholderText("New folder name…")
        name_input.setToolTip(f"Enter a folder name ({nachars} are not allowed).")
        layout.addWidget(name_input)

        error_label = QLabel("", dialog)
        error_label.setStyleSheet("color: #d33;")
        error_label.setVisible(False)
        layout.addWidget(error_label)

        button_box = QDialogButtonBox(dialog)
        create_button = button_box.addButton(
            ui_tr_now("Create"), QDialogButtonBox.AcceptRole)
        button_box.addButton(ui_tr_now("Cancel"), QDialogButtonBox.RejectRole)
        layout.addWidget(button_box)

        def _on_create():
            name = name_input.text().strip()
            error = image_invalid_folder_name(name)
            if error:
                error_label.setText(error)
                error_label.setVisible(True)
                return
            image_create_folder_named(name)
            dialog.accept()

        create_button.clicked.connect(_on_create)
        button_box.rejected.connect(dialog.reject)
        name_input.returnPressed.connect(_on_create)

        name_input.setFocus()
        dialog.exec()

    def select_image():

        global right_disk_image_explorer_path
        global right_disk_image_path

        dialog = QFileDialog(host) # https://doc.qt.io/qtforpython-6.2/PySide6/QtWidgets/QFileDialog.html
        dialog.setFileMode(QFileDialog.AnyFile)
        dialog.setViewMode(QFileDialog.Detail)
        fileName = QFileDialog.getOpenFileName(host,"Open File","/home/", "Images (*.img *.hdf)" )
        host.imageinput.setCurrentText(normalize_sd_image_path(fileName[0]))
        configuration_dictionary[SETTING_HDDFILE] = host.imageinput.currentText()

        right_disk_image_explorer_path = []
        _set_right_disk_content([])
        right_disk_image_path = ""
        _set_right_disk_selected([])
        image_clear_model()

        # Now try to load it
        def _on_loaded(success):
            if success:
                save_configuration_file()
                if host.settings_warn_image_nearly_full_checkbox.isChecked():
                    _warn_if_image_nearly_full(host.right_disk_image_path)
        load_image(_on_loaded)

    def download_nextzxos_image():
        """Quick wizard to download a ready-to-use NextZXOS SD card image from
        zxnext.uk, save it to disk, extract the contained disk image, select it
        into self.imageinput and load it automatically."""

        NEXTZXOS_IMAGES = [
            ("Next distribution 2Gb SD Card Image",
             "https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-2gb.zip"),
            ("Next distribution 4Gb SD Card Image",
             "https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-4gb.zip"),
            ("Next distribution 8Gb SD Card Image",
             "https://zxnext.uk/hosted/index_files/hdfimages/cspect-next-8gb.zip"),
        ]

        dialog = QDialog(host)
        dialog.setWindowTitle(ui_tr_now("Download NextZXOS Image"))
        dialog.setMinimumWidth(480)

        dialog_layout = QVBoxLayout(dialog)

        info_label = QLabel(
            "Select a NextZXOS SD card image to download from zxnext.uk.\n"
            "The image will be saved to a location of your choice and then\n"
            "loaded automatically so you can start using it right away."
        )
        dialog_layout.addWidget(info_label)

        image_combo = QComboBox(dialog)
        for label, url in NEXTZXOS_IMAGES:
            image_combo.addItem(label, url)
        dialog_layout.addWidget(image_combo)

        download_progress = QProgressBar(dialog)
        download_progress.setRange(0, 100)
        download_progress.setValue(0)
        download_progress.setVisible(False)
        dialog_layout.addWidget(download_progress)

        button_box = QDialogButtonBox(dialog)
        download_button = button_box.addButton(
            ui_tr_now("Download"), QDialogButtonBox.AcceptRole)
        cancel_button = button_box.addButton(ui_tr_now("Cancel"), QDialogButtonBox.RejectRole)
        dialog_layout.addWidget(button_box)

        cancel_button.clicked.connect(dialog.reject)

        def do_download():
            selected_label = image_combo.currentText()
            selected_url = image_combo.currentData()

            suggested_name = os.path.basename(urllib.parse.urlparse(selected_url).path)

            save_path, _selected_filter = QFileDialog.getSaveFileName(
                dialog,
                "Save NextZXOS Image",
                suggested_name,
                "Zip Archives (*.zip);;All Files (*)"
            )

            if not save_path:
                return

            download_button.setEnabled(False)
            cancel_button.setEnabled(False)
            image_combo.setEnabled(False)
            download_progress.setVisible(True)
            download_progress.setValue(0)

            add_main_log_window(ui_tr_now("Downloading {name} from {url}").format(
                name=selected_label, url=selected_url))

            try:
                request = urllib.request.Request(
                    selected_url,
                    headers={"User-Agent": "ZX-Next-Unite"}
                )
                with urllib.request.urlopen(request) as response:
                    total_size = response.getheader("Content-Length")
                    total_size = int(total_size) if total_size else 0
                    downloaded = 0
                    chunk_size = 65536
                    with open(save_path, "wb") as out_file:
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            out_file.write(chunk)
                            downloaded += len(chunk)
                            if total_size:
                                percent = int(downloaded * 100 / total_size)
                                download_progress.setValue(min(percent, 100))
                            QApplication.processEvents()
                download_progress.setValue(100)
            except Exception as download_error:
                logging.error(f"Failed downloading NextZXOS image: {download_error}")
                add_main_log_window(ui_tr_now(
                    "Failed downloading NextZXOS image: {error}").format(
                        error=download_error))
                QMessageBox.critical(
                    dialog,
                    ui_tr_now("Download Failed"),
                    ui_tr_now("Failed to download the NextZXOS image:") + f"\n{download_error}"
                )
                download_button.setEnabled(True)
                cancel_button.setEnabled(True)
                image_combo.setEnabled(True)
                download_progress.setVisible(False)
                return

            logging.info(f"Downloaded NextZXOS image archive to {save_path}")

            # Extract the disk image from the downloaded archive so it can be
            # loaded. The archive's own folder layout is deliberately ignored:
            # the image lands NEXT TO the zip, NAMED AFTER IT
            # (cspect-next-2gb-fresh.zip -> cspect-next-2gb-fresh.img) — the
            # stock archives keep their image under a fixed internal folder
            # (2gb/cspect-next-2gb.img), so honouring it meant a renamed
            # download produced no artifact carrying the chosen name (the
            # reported "it didn't extract"), and every download of the same
            # size silently overwrote the previous one through that fixed
            # path. Extraction is chunked with the events pumped so a 2 GB
            # inflate shows progress instead of freezing the whole UI for its
            # duration — the freeze is the prime suspect for the reported
            # never-diagnosed post-extract crash, and every stage now also
            # writes the file log so a future failure leaves a trace.
            image_to_load = save_path
            try:
                if zipfile.is_zipfile(save_path):
                    with zipfile.ZipFile(save_path) as archive:
                        image_members = [
                            info for info in archive.infolist()
                            if info.filename.lower().endswith((".img", ".hdf"))
                        ]
                        if image_members:
                            member = image_members[0]
                            target = os.path.join(
                                os.path.dirname(save_path),
                                os.path.splitext(os.path.basename(save_path))[0]
                                + os.path.splitext(member.filename)[1].lower())
                            logging.info(
                                f"Extracting {member.filename} "
                                f"({member.file_size} bytes) to {target}")
                            download_progress.setValue(0)
                            download_progress.setFormat(
                                ui_tr_now("Extracting image... %p%"))
                            extracted = 0
                            with archive.open(member) as src, \
                                    open(target, "wb") as dst:
                                while True:
                                    chunk = src.read(4 * 1024 * 1024)
                                    if not chunk:
                                        break
                                    dst.write(chunk)
                                    extracted += len(chunk)
                                    if member.file_size:
                                        download_progress.setValue(min(
                                            int(extracted * 100 / member.file_size),
                                            100))
                                    QApplication.processEvents()
                            image_to_load = target
                            add_main_log_window(ui_tr_now(
                                "Extracted disk image: {path}").format(
                                    path=image_to_load))
                            logging.info(f"Extracted disk image: {image_to_load}")
                        else:
                            # No .img/.hdf inside: fall through with the zip
                            # itself (load_image will report it cannot read
                            # it) — but say so in the log rather than nothing.
                            logging.warning(
                                f"No .img/.hdf member found in {save_path}; "
                                "loading the archive path as-is")
            except Exception as extract_error:
                logging.error(f"Failed extracting NextZXOS image: {extract_error}")
                add_main_log_window(ui_tr_now(
                    "Failed extracting NextZXOS image: {error}").format(
                        error=extract_error))
                QMessageBox.critical(
                    dialog,
                    ui_tr_now("Extraction Failed"),
                    ui_tr_now("The image was downloaded but could not be extracted:") + f"\n{extract_error}"
                )
                download_button.setEnabled(True)
                cancel_button.setEnabled(True)
                image_combo.setEnabled(True)
                download_progress.setVisible(False)
                download_progress.setFormat("%p%")
                return

            dialog.accept()

            # Selecting + loading can only fail unexpectedly from here on —
            # and the dialog is already gone, so an uncaught exception used
            # to leave the user staring at an unchanged window with nothing
            # in any log (the reported symptom). Catch, log, and SAY it.
            try:
                global right_disk_image_explorer_path
                global right_disk_image_path

                # Select the downloaded image into the image input
                logging.info(f"Selecting downloaded image: {image_to_load}")
                host.imageinput.setCurrentText(normalize_sd_image_path(image_to_load))
                configuration_dictionary[SETTING_HDDFILE] = host.imageinput.currentText()

                right_disk_image_explorer_path = []
                _set_right_disk_content([])
                right_disk_image_path = ""
                _set_right_disk_selected([])
                image_clear_model()

                # Now try to load it
                def _on_loaded(success):
                    if success:
                        logging.info(f"Downloaded image loaded: "
                                     f"{host.right_disk_image_path}")
                        save_configuration_file()
                        if host.settings_warn_image_nearly_full_checkbox.isChecked():
                            _warn_if_image_nearly_full(host.right_disk_image_path)
                load_image(_on_loaded)
            except Exception as select_error:
                logging.error(
                    f"Failed selecting/loading the downloaded image: {select_error}")
                add_main_log_window(ui_tr_now(
                    "Failed loading image: {path}.").format(path=image_to_load))
                QMessageBox.critical(
                    host,
                    ui_tr_now("Load Failed"),
                    ui_tr_now("The image was extracted but could not be loaded:")
                    + f"\n{select_error}"
                )

        download_button.clicked.connect(do_download)

        dialog.exec()

    def _get_image_free_space_pct(image_path):
        """Parse the FAT layout of image_path and return (free_pct, free_mb, total_mb).
        Returns None if the image cannot be read or is not a recognised FAT volume."""
        try:
            clean = image_path.strip('"').strip("'")
            with open(clean, 'rb') as f:
                mbr = f.read(512)
                pte = mbr[446:462]
                lba_start = struct.unpack_from('<I', pte, 8)[0]
                f.seek(lba_start * 512)
                vbr = f.read(512)
                bps      = struct.unpack_from('<H', vbr, 11)[0]
                spc      = vbr[13]
                rsvd     = struct.unpack_from('<H', vbr, 14)[0]
                nfats    = vbr[16]
                root_ent = struct.unpack_from('<H', vbr, 17)[0]
                total16  = struct.unpack_from('<H', vbr, 19)[0]
                fat_sz16 = struct.unpack_from('<H', vbr, 22)[0]
                total32  = struct.unpack_from('<I', vbr, 32)[0]
                fat_sz32 = struct.unpack_from('<I', vbr, 36)[0]
                fat_sz   = fat_sz32 if fat_sz16 == 0 else fat_sz16
                total    = total32  if total16  == 0 else total16
                if not (bps and spc and fat_sz and total):
                    return None
                data_start     = rsvd + nfats * fat_sz + (root_ent * 32 + bps - 1) // bps
                total_clusters = (total - data_start) // spc
                is_fat32       = (total_clusters >= 65525)
                entry_size     = 4 if is_fat32 else 2
                fat_offset     = (lba_start + rsvd) * bps
                fat_size_bytes = fat_sz * bps
                f.seek(fat_offset)
                fat_data = f.read(fat_size_bytes)
                free_clusters = sum(
                    1 for c in range(2, min(total_clusters + 2, len(fat_data) // entry_size))
                    if (struct.unpack_from('<I', fat_data, c * entry_size)[0] & 0x0FFFFFFF
                        if is_fat32
                        else struct.unpack_from('<H', fat_data, c * entry_size)[0]) == 0
                )
                cluster_bytes = spc * bps
                total_mb = total_clusters * cluster_bytes // (1024 * 1024)
                free_mb  = free_clusters  * cluster_bytes // (1024 * 1024)
                free_pct = (free_clusters / total_clusters * 100) if total_clusters else 0
                return (free_pct, free_mb, total_mb)
        except Exception:
            return None

    def _update_image_usage_gauge(image_path=None):
        """Refresh the SD card usage gauge below the image explorer.
        Reads the FAT free-space data from the image and updates the bar colour and tooltip.
        Call with no argument (or empty string) to reset the gauge to an empty state."""
        if not image_path:
            image_path = host.right_disk_image_path if hasattr(host, 'right_disk_image_path') else ""
        result = _get_image_free_space_pct(image_path) if image_path else None
        gauge = host.image_usage_gauge
        if result is None:
            gauge.setValue(0)
            gauge.setFormat(ui_tr_now("No image loaded"))
            gauge.setToolTip(ui_tr_now(
                "No SD card image is currently loaded."))
            gauge.setStyleSheet("")
            return
        free_pct, free_mb, total_mb = result
        used_pct = 100.0 - free_pct
        used_mb  = total_mb - free_mb
        gauge.setValue(int(round(used_pct)))
        gauge.setFormat(f"{used_pct:.1f} % used")
        gauge.setToolTip(
            f"SD Card Image usage: {used_pct:.1f} % used\n"
            f"{used_mb} MB used / {total_mb} MB total\n"
            f"{free_mb} MB remaining ({free_pct:.1f} % free)"
        )
        if used_pct < 70:
            color = "#4caf50"   # green
        elif used_pct < 90:
            color = "#ff9800"   # orange/yellow
        else:
            color = "#f44336"   # red
        gauge.setStyleSheet(
            f"QProgressBar {{"
            f"  border: 1px solid #555; border-radius: 4px;"
            f"  background: #2b2b2b; text-align: center; color: #ffffff;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  background-color: {color}; border-radius: 3px;"
            f"}}"
        )

    def _warn_if_image_nearly_full(image_path):
        """Show a warning dialog if the SD image has less than 10 % free space."""
        result = _get_image_free_space_pct(image_path)
        if result is None:
            return
        free_pct, free_mb, total_mb = result
        used_pct = 100 - free_pct
        if free_pct < 10:
            if free_pct == 0:
                space_line = ui_tr_now(
                    "The image is completely full ({total} MB capacity, "
                    "0 MB free).").format(total=total_mb)
            else:
                space_line = ui_tr_now(
                    "Only {free} MB free out of {total} MB "
                    "({used} % used, {pct} % free).").format(
                        free=free_mb, total=total_mb,
                        used=f"{used_pct:.1f}", pct=f"{free_pct:.1f}")
            msg = QMessageBox(host)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle(ui_tr_now("SD Image Nearly Full"))
            msg.setText(
                "\u26a0\ufe0f  " + ui_tr_now(
                    "The SD card image is nearly full.") + "\n\n"
                + space_line + "\n\n"
                + ui_tr_now(
                    "Delete files from the image to free space, or switch to "
                    "a larger image.\nLarger SD card images can be downloaded "
                    "from:") + "\nhttps://zxnext.uk/hosted/"
            )
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec()

    def _check_image_writable(image_path, check_free_space=True):
        """Return None if image_path is writable, or an error string explaining why not.
        Also checks that the FAT volume has at least one free cluster."""
        if not image_path:
            return "No image file selected."
        try:
            clean = image_path.strip('"').strip("'")
            p = pathlib.Path(clean)
            if not p.exists():
                return f"Image file not found: {clean}"
            # Check for offline cloud file (OneDrive file not downloaded locally)
            if hasattr(p.stat(), 'st_file_attributes'):
                OFFLINE = 0x1000  # FILE_ATTRIBUTE_OFFLINE
                if p.stat().st_file_attributes & OFFLINE:
                    return (f"The image file is an offline cloud file (e.g. OneDrive).\n"
                            f"Please right-click the file in Explorer and choose\n"
                            f"'Always keep on this device' to pin it locally before writing.")
            # Definitive write test
            with open(clean, 'r+b') as f:
                # --- FAT free-cluster check (skipped for delete operations) ---
                if check_free_space:
                    try:
                        mbr = f.read(512)
                        pte = mbr[446:462]
                        lba_start = struct.unpack_from('<I', pte, 8)[0]
                        f.seek(lba_start * 512)
                        vbr = f.read(512)
                        bps      = struct.unpack_from('<H', vbr, 11)[0]
                        spc      = vbr[13]
                        rsvd     = struct.unpack_from('<H', vbr, 14)[0]
                        nfats    = vbr[16]
                        root_ent = struct.unpack_from('<H', vbr, 17)[0]
                        total16  = struct.unpack_from('<H', vbr, 19)[0]
                        fat_sz16 = struct.unpack_from('<H', vbr, 22)[0]
                        total32  = struct.unpack_from('<I', vbr, 32)[0]
                        fat_sz32 = struct.unpack_from('<I', vbr, 36)[0]
                        fat_sz   = fat_sz32 if fat_sz16 == 0 else fat_sz16
                        total    = total32  if total16  == 0 else total16
                        if bps and spc and fat_sz and total:
                            data_start = rsvd + nfats * fat_sz + (root_ent * 32 + bps - 1) // bps
                            total_clusters = (total - data_start) // spc
                            is_fat32 = (total_clusters >= 65525)
                            entry_size = 4 if is_fat32 else 2
                            fat_offset = (lba_start + rsvd) * bps
                            fat_size_bytes = fat_sz * bps
                            f.seek(fat_offset)
                            fat_data = f.read(fat_size_bytes)
                            free = sum(
                                1 for c in range(2, min(total_clusters + 2, len(fat_data) // entry_size))
                                if (struct.unpack_from('<I', fat_data, c * entry_size)[0] & 0x0FFFFFFF
                                    if is_fat32
                                    else struct.unpack_from('<H', fat_data, c * entry_size)[0]) == 0
                            )
                            if free == 0:
                                cap_mb = total_clusters * spc * bps // 1024 // 1024
                                return (f"The image volume is full (0 free clusters, {cap_mb} MB capacity).\n"
                                        f"Delete files from the image before adding new content.")
                    except Exception:
                        pass  # FAT parse failure is non-fatal for the write check
        except OSError as e:
            return (f"The image file cannot be opened for writing:\n{e}\n\n"
                    f"If the file is in OneDrive, right-click it and choose\n"
                    f"'Always keep on this device'.")
        except Exception as e:
            return f"Cannot check image file: {e}"
        return None

    def execute_hdf_monkey(command_to_execute, image_path, additional_args="", silent=False, extra_argv=None, prompt_if_missing=True):
        # Sentinel with a non-zero returncode in case we never reach subprocess.run
        exec_process = subprocess.CompletedProcess(args=[], returncode=-1)
        # Prefer a bundled hdfmonkey discovered under downloads/cspect
        # (Windows only); otherwise fall back to the PATH/"hdfmonkey" default.
        hdfmonkey_exe = getattr(host, "_hdfmonkey_executable_path", None) or HDFMONKEY_EXECUTABLE
        execution_cmd = f'{hdfmonkey_exe} {command_to_execute} {image_path} {additional_args}'
        # On Linux/macOS the itch.io CSpect bundle ships hdfmonkey without
        # its executable bit set, so the OS would refuse to launch it
        # (EACCES). Add the bit ourselves (the binary is in the user's own
        # downloads dir, no sudo needed) so it runs like a manually compiled
        # build. No-op on Windows / for the bare "hdfmonkey" PATH default.
        ensure_hdfmonkey_executable(hdfmonkey_exe)
        try:
            img = image_path.strip('"')
            argv = [hdfmonkey_exe, command_to_execute, img]
            if extra_argv is not None:
                # Caller passes a clean list of path strings – no quoting/parsing needed
                argv += extra_argv
            elif additional_args:
                argv += shlex.split(additional_args, posix=True)
            exec_process = subprocess.run(argv, shell=False, check=True,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          **subprocess_no_window_kwargs())

        except PermissionError as ex:
                # The proactive chmod above couldn't make the bundled
                # hdfmonkey executable (e.g. read-only filesystem or a file
                # the user doesn't own). Surface that in the SD-card log
                # window with the exact fix and full path, rather than
                # letting it bubble up as an opaque failure.
                exec_process = subprocess.CompletedProcess(args=[hdfmonkey_exe], returncode=-1)
                if silent:
                    logging.debug(f"hdfmonkey {command_to_execute} permission denied (silent): {execution_cmd} - {ex}")
                else:
                    logging.error(f"Permission denied running hdfmonkey: {execution_cmd} - {ex}")
                    add_main_log_window(f"ERROR: Permission denied running hdfmonkey: {hdfmonkey_exe}")
                    if hdfmonkey_needs_exec_bit():
                        cmd = hdfmonkey_chmod_instruction(hdfmonkey_exe)
                        add_main_log_window(ui_tr_now(
                            "The hdfmonkey provided by the CSpect itch.io package is not "
                            "executable. Make it executable by running:"))
                        add_main_log_window(f"    {cmd}")

        except (FileNotFoundError,subprocess.CalledProcessError) as ex:
                # If hdfmonkey can't actually be located, offer to install it
                # (once). The dialog is marshalled to the UI thread, so this is
                # safe even when called from a background worker. A real
                # hdfmonkey error (binary present) skips this and logs below.
                if (not silent) and prompt_if_missing and (not host._hdfmonkey_prompt_shown) and (not _hdfmonkey_binary_found()):
                    host._hdfmonkey_prompt_shown = True
                    host._hdfmonkey_missing_signals.missing.emit()
                stderr_text = ""
                if not isinstance(ex, FileNotFoundError):
                    stderr_text = (ex.stderr or b"").decode(errors="replace").strip()
                    exec_process = subprocess.CompletedProcess(args=ex.cmd, returncode=ex.returncode,
                                                               stdout=ex.stdout, stderr=ex.stderr)
                if silent:
                    # FileNotFoundError has no returncode/stderr — log it as a
                    # plain "not found" rather than touching those attributes.
                    if isinstance(ex, FileNotFoundError):
                        logging.debug(f"hdfmonkey {command_to_execute} not found (silent): {execution_cmd} - {ex}")
                    else:
                        logging.debug(f"hdfmonkey {command_to_execute} returned {ex.returncode} (silent): {execution_cmd}"
                                      + (f" | stderr: {stderr_text}" if stderr_text else ""))
                elif isinstance(ex, FileNotFoundError) or ex.returncode == 1:
                    logging.error(f"Failed executing hdfmonkey: {execution_cmd} - Once hdfmonkey is installed please close the application and restart it.")
                    add_main_log_window(ui_tr_now(
                        "ERROR: hdfmonkey could not be found. Use the "
                        "'Download and install HDF Monkey' button (bottom "
                        "right of the SD Card tab) to install it "
                        "automatically, or do a full CSpect install from the "
                        "itch.io tab, which also bundles hdfmonkey. It can "
                        "also be installed manually from "
                        "https://github.com/gasman/hdfmonkey — restart the "
                        "app once installed."))
                elif ex.returncode == 255:
                    if execution_cmd is not None:
                        logging.error(f"ERROR: hdfmonkey failed - A file can't be opened: {execution_cmd} this is commonly caused by strange characters such as quotes and signs")
                        add_main_log_window(ui_tr_now(
                            "ERROR: hdfmonkey failed - A file can't be opened: "
                            "{command} this is commonly caused by strange "
                            "characters such as quotes and signs").format(
                                command=execution_cmd))
                    else:
                        logging.error(f"ERROR: hdfmonkey failed - A file can't be opened this is commonly caused by strange characters such as quotes and signs")
                        add_main_log_window(ui_tr_now(
                            "ERROR: hdfmonkey failed - A file can't be opened "
                            "this is commonly caused by strange characters "
                            "such as quotes and signs"))
                else:
                    err_detail = f" | stderr: {stderr_text}" if stderr_text else ""
                    if HDFMONKEY_EXECUTABLE is not None and execution_cmd is not None:
                        logging.error(f"ERROR: hdfmonkey {HDFMONKEY_EXECUTABLE} execution failed with unknown error: {execution_cmd} - Exception: {ex}{err_detail}")
                        add_main_log_window(f"ERROR: hdfmonkey {HDFMONKEY_EXECUTABLE} execution failed with unknown error: {execution_cmd} - Exception: {ex}{err_detail}")
                    else:
                        logging.error(f"ERROR: hdfmonkey execution failed with unknown error: - Exception: {ex}{err_detail}")
                        add_main_log_window(f"ERROR: hdfmonkey  execution failed with unknown error: - Exception: {ex}{err_detail}")

        return exec_process

    def execute_shell_command(command_to_execute, additional_args = "", cwd = None):
        execution_cmd = command_to_execute + " " + additional_args
        return subprocess.run(execution_cmd, shell=True, check=True, stdout=subprocess.PIPE, cwd=cwd)

    def execute_shell_command_no_wait(command_to_execute, additional_args = ""):
        execution_cmd = command_to_execute + " " + additional_args
        return subprocess.run(execution_cmd, shell=False, stdin=None, stdout=None, stderr=None,close_fds=True, start_new_session=True, capture_output=False, timeout=None)

    def nextsync_update_root_drive():
        drive = host.nextsync_diskdrive.currentText() or host.nextsync_diskdrive.itemText(0)
        host.nextsync_treeview.setRootIndex(host.nextsync_model.mapFromSource(host.nextsync_filesystem_model.index(drive)))
        host.nextsync_treeview.show()
        _nextsync_update_set_syncroot_button()
        # The drive switcher also drives the Remote Explorer's local pane so
        # the user can change drive from within it.
        re_widget = getattr(host, "_re_widget", None)
        if re_widget is not None:
            re_widget.set_local_dir(drive)

    # ---------------------------------------------------------------
    # Scan helpers: walk an image directory tree and return flat lists
    # of (image_path_in_image, local_disk_path) pairs or just names,
    # emitting live status/progress so the UI stays responsive.
    # ---------------------------------------------------------------

    # Consumed by bare name elsewhere in __init__ (re-bound at the call site).
    host._maybe_show_no_image_toast = _maybe_show_no_image_toast
    host._start_hdfmonkey_button_animation = _start_hdfmonkey_button_animation
    host._start_load_image_hint_animation = _start_load_image_hint_animation
    host._start_transfer_idle_animation = _start_transfer_idle_animation
    host._stop_hdfmonkey_button_animation = _stop_hdfmonkey_button_animation
    host._stop_transfer_idle_animation = _stop_transfer_idle_animation
    host._update_image_usage_gauge = _update_image_usage_gauge
    host._warn_if_image_nearly_full = _warn_if_image_nearly_full
    host.add_help_content = add_help_content
    host.add_main_log_window = add_main_log_window
    host.add_nextsync_log_window = add_nextsync_log_window
    host.apply_file_extension_filter_nextsync = apply_file_extension_filter_nextsync
    host.delete_files_button_show_confirmation_buttons = delete_files_button_show_confirmation_buttons
    host.download_nextzxos_image = download_nextzxos_image
    host.execute_hdf_monkey = execute_hdf_monkey
    host.execute_shell_command = execute_shell_command
    host.image_newfolder = image_newfolder
    host.image_newfolder_cancel = image_newfolder_cancel
    host.image_newfolder_create = image_newfolder_create
    host.is_hdfmonkey_present = is_hdfmonkey_present
    host.load_image = load_image
    host.nextsync_update_root_drive = nextsync_update_root_drive
    host.select_image = select_image
    host.set_treeview_properties = set_treeview_properties
    host._check_image_writable = _check_image_writable
    host.image_newfolder_dialog = image_newfolder_dialog


def build_image_edit_ops(
    host,
    *,
    _right_disk_content,
    set_all_buttons_disabled,
    set_all_buttons_enabled,
    add_main_log_window,
    _check_image_writable,
    execute_hdf_monkey,
    _check_access_denied_is_full_disk,
    image_reload_dir,
):
    """Image deletion + in-image rename (dialogs and worker tasks)."""
    def image_confirm_deletion_dialog():
        # Popup wizard asking the user to confirm deletion of the selected
        # image file or folder. Used when the "Do not prompt for confirmation
        # on deletion" setting is False.
        if not host.image_selected_path:
            return

        selected = host.image_selected_paths or [(host.image_selected_path, host.image_selected_is_dir)]

        dialog = QDialog(host)
        dialog.setWindowTitle(ui_tr_now("Confirm Deletion"))
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)

        if len(selected) > 1:
            n_dirs = sum(1 for (_p, d) in selected if d)
            question = f"Delete the {len(selected)} selected items"
            if n_dirs:
                question += " (folders are deleted with all of their contents)"
            question += "?"
            path_text = "\n".join(p.replace("//", "/") for (p, _d) in selected)
        else:
            sel_path, is_dir = selected[0]
            name = sel_path.rstrip("/").rsplit("/", 1)[-1]
            kind = "folder" if is_dir else "file"
            if is_dir:
                question = f"Delete the {kind} “{name}” and all of its contents?"
            else:
                question = f"Delete the {kind} “{name}”?"
            path_text = sel_path.replace("//", "/")

        msg = QLabel(question, dialog)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        path_label = QLabel(path_text, dialog)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color: gray;")
        layout.addWidget(path_label)

        warn = QLabel("This action cannot be undone.", dialog)
        warn.setStyleSheet("color: #d33;")
        layout.addWidget(warn)

        button_box = QDialogButtonBox(dialog)
        delete_button = button_box.addButton(
            ui_tr_now("Delete"), QDialogButtonBox.AcceptRole)
        cancel_button = button_box.addButton(ui_tr_now("Cancel"), QDialogButtonBox.RejectRole)
        layout.addWidget(button_box)

        delete_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        cancel_button.setDefault(True)   # safer default — Enter cancels

        if dialog.exec() == QDialog.Accepted:
            image_delete_files()

    def image_delete_files():
        if not _right_disk_content():
            logging.info("Please select an image file or folder first to delete!")
            add_main_log_window(ui_tr_now(
                "Please select an image file or folder first to delete!"))
            return

        if not host.image_selected_path:
            logging.info("Please select an image file or folder first to delete!")
            add_main_log_window(ui_tr_now(
                "Please select an image file or folder first to delete!"))
            return

        img_err = _check_image_writable(host.right_disk_image_path, check_free_space=False)
        if img_err:
            logging.error(img_err)
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return

        # Delete every selected entry. Fall back to the primary selection if
        # the multi-selection list is somehow empty.
        paths_to_delete = [p for (p, _d) in host.image_selected_paths] or [host.image_selected_path]
        # Unique parent directories to refresh once the deletion finishes.
        parent_paths = []
        for p in paths_to_delete:
            parent = p.rstrip("/").rsplit("/", 1)[0] or "/"
            if parent not in parent_paths:
                parent_paths.append(parent)
        image_path  = host.right_disk_image_path

        set_all_buttons_disabled()

        dlg    = HdfProgressDialog("Deleting files\u2026", host)
        worker = HdfTaskWorker(_run_delete_task, execute_hdf_monkey, image_path, paths_to_delete)

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_delete_finished():
            dlg.close()
            for parent_path in parent_paths:
                image_reload_dir(parent_path)
            set_all_buttons_enabled()

        worker.signals.finished.connect(_on_delete_finished)
        host.threadpool.start(worker)
        dlg.exec()

    def _image_entry_exists(image_path, path):
        """True if *path* (file or directory) already exists inside the image.

        The comparison is case-insensitive because the image is a FAT volume,
        where 'GAME.TAP' and 'game.tap' are the same entry — so a case-only
        rename must also be reported as already existing."""
        parent, name = get_parent_root_directory_splited(path.rstrip("/"))
        result = execute_hdf_monkey("ls", image_path, extra_argv=[parent or "/"])
        if result.returncode != 0:
            return False
        name_cf = name.casefold()
        for line in result.stdout.splitlines():
            decoded = line.decode(errors="replace") if isinstance(line, bytes) else line
            parts = decoded.split('\t', 1)
            if len(parts) == 2 and parts[1].casefold() == name_cf:
                return True
        return False

    def _run_rename_task(signals, cancel_event, image_path, src_path, new_path,
                         base_name, is_windows):
        """Rename a file/folder inside the image. hdfmonkey has no 'mv', so we
        copy the entry out to a temp dir (get), write it back under the new
        name (put), verify the copy, then remove the original (rm). Folders are
        handled recursively by reusing the get/put/delete task bodies. The
        original is only removed once the new entry is confirmed present, so a
        failed copy never loses data."""

        class _StepProxy:
            """Wraps the real worker signals so every status line the reused
            get/put/delete bodies emit is relabelled with a fixed phase header
            (e.g. 'Uploading (Step 2/3)…'). The per-file detail line and the
            progress/error signals pass straight through."""
            def __init__(host, header):
                host._header = header
            @property
            def progress(host):
                return signals.progress
            @property
            def error(host):
                return signals.error
            @property
            def status(host):
                return host            # so '.status.emit(msg)' lands on emit()
            def emit(host, msg):
                detail = msg.split("\n", 1)[1] if "\n" in msg else ""
                signals.status.emit(f"{host._header}\n{detail}")

        tmp_root = tempfile.mkdtemp(prefix="zxnu_rename_")
        try:
            dir_nav = "\\" if is_windows else "/"

            # ---- Phase 1: copy the source OUT of the image to tmp_root/base_name
            _run_get_task(_StepProxy("Downloading (Step 1/3)…"), cancel_event,
                          execute_hdf_monkey, image_path, [(src_path, base_name)], tmp_root,
                          dir_nav, is_windows)
            if cancel_event.is_set():
                return
            local_copy = os.path.join(tmp_root, base_name)
            if not os.path.exists(local_copy):
                signals.error.emit(f"Rename failed: could not copy {src_path} out of the image.")
                return

            # ---- Phase 2: write it back under the new name
            _run_put_task(_StepProxy("Uploading (Step 2/3)…"), cancel_event,
                          execute_hdf_monkey, _check_access_denied_is_full_disk,
                          image_path, local_copy, new_path)
            if cancel_event.is_set():
                return
            if not _image_entry_exists(image_path, new_path):
                signals.error.emit(f"Rename failed: {new_path} was not written — the original is kept.")
                return

            # ---- Phase 3: remove the original
            _run_delete_task(_StepProxy("Deleting (Step 3/3)…"), cancel_event,
                             execute_hdf_monkey, image_path, [src_path])
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def image_rename_dialog():
        """Prompt for and perform a rename of the selected image entry. Acts on
        the primary selection (single entry) and runs on a worker thread."""
        if not _right_disk_content() or not host.image_selected_path:
            logging.info("Please select an image file or folder first to rename!")
            add_main_log_window(ui_tr_now(
                "Please select an image file or folder first to rename!"))
            return

        src_path = host.image_selected_path.rstrip("/")
        if not src_path or src_path == "/":
            return
        is_dir = host.image_selected_is_dir

        img_err = _check_image_writable(host.right_disk_image_path, check_free_space=False)
        if img_err:
            logging.error(img_err)
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return

        old_name = src_path.rsplit("/", 1)[-1]
        kind = "folder" if is_dir else "file"
        new_name, ok = QInputDialog.getText(
            host, ui_tr_now("Rename"), ui_tr_now("New name for the {kind}:").format(kind=ui_tr_now(kind)), text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.warning(host, ui_tr_now("Rename failed"), ui_tr_now("The name cannot contain '/' or '\\'."))
            return

        parent     = src_path.rsplit("/", 1)[0]   # "" for a root-level entry
        new_path   = (parent + "/" + new_name).replace("//", "/")
        image_path = host.right_disk_image_path

        if _image_entry_exists(image_path, new_path):
            QMessageBox.warning(host, ui_tr_now("Rename failed"),
                                ui_tr_now('"{name}" already exists in this folder.').format(name=new_name))
            return

        parent_dir = parent or "/"
        is_windows = platform.system() == "Windows"

        set_all_buttons_disabled()
        dlg    = HdfProgressDialog("Renaming…", host)
        worker = HdfTaskWorker(_run_rename_task, image_path, src_path, new_path,
                               old_name, is_windows)

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_rename_finished():
            dlg.close()
            image_reload_dir(parent_dir)
            set_all_buttons_enabled()

        worker.signals.finished.connect(_on_rename_finished)
        host.threadpool.start(worker)
        dlg.exec()

    # Consumed by bare name elsewhere in __init__ (re-bound at the call site).
    host.image_rename_dialog = image_rename_dialog
    host.image_confirm_deletion_dialog = image_confirm_deletion_dialog
    host.image_delete_files = image_delete_files


def build_local_explorer_ops(
    host,
    *,
    add_main_log_window,
    _right_disk_content,
    nextsync_refresh_explorer,
    _nextsync_unique_path,
    _run_nextsync_import_task,
    _explorer_clipboard_has_items,
    _explorer_paste_into_local,
    _local_explorer_copy_selection,
):
    """The SD tab's local-pane operation closures (menu, delete, zip, import)."""
    def _local_make_directory(dir_path, refresh_fn, log_fn):
        """'Create new directory…' on a local explorer: prompt for a name
        and create it under dir_path. Shared by the SD-card local pane and
        the NextSync classic explorer (both browse the local filesystem);
        refresh_fn/log_fn route feedback to the pane the action came from.
        """
        if not dir_path or not os.path.isdir(dir_path):
            return
        name, ok = QInputDialog.getText(
            host, ui_tr_now("Create new directory"),
            ui_tr_now("New directory name in") + f"\n{dir_path}:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if "/" in name or "\\" in name:
            QMessageBox.warning(host, ui_tr_now("Create directory failed"),
                                ui_tr_now("The name cannot contain '/' or '\\'."))
            return
        new_path = os.path.join(dir_path, name)
        if os.path.exists(new_path):
            QMessageBox.warning(
                host, ui_tr_now("Create directory failed"),
                ui_tr_now('"{name}" already exists in this folder.').format(name=name))
            return
        try:
            os.makedirs(new_path)
            log_fn(f"Created directory: {new_path}")
        except OSError as e:
            logging.error(f"Failed to create directory {new_path}: {e}",
                          exc_info=True)
            log_fn(f"Failed to create directory {new_path}: {e}")
            QMessageBox.critical(host, ui_tr_now("Create directory failed"),
                                 ui_tr_now("Could not create:") + f"\n{new_path}\n\n{e}")
        finally:
            refresh_fn()

    def local_explorer_rename_item(file_path, name, is_dir):
        """Rename a file/folder in the SD-card local explorer (context
        menu + F2). Mirrors nextsync_rename_explorer_item, logging to the
        main log window."""
        kind = "folder" if is_dir else "file"
        new_name, ok = QInputDialog.getText(
            host, ui_tr_now("Rename"), ui_tr_now("New name for the {kind}:").format(kind=ui_tr_now(kind)), text=name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == name:
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.warning(host, ui_tr_now("Rename failed"),
                                ui_tr_now("The name cannot contain '/' or '\\'."))
            return
        new_path = os.path.join(os.path.dirname(file_path), new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(
                host, ui_tr_now("Rename failed"),
                ui_tr_now('"{name}" already exists in this folder.').format(name=new_name))
            return
        try:
            os.rename(file_path, new_path)
            add_main_log_window(ui_tr_now("Renamed: {old} -> {new}").format(
                old=file_path, new=new_path))
        except OSError as e:
            logging.error(f"Failed to rename {file_path} -> {new_path}: {e}",
                          exc_info=True)
            add_main_log_window(ui_tr_now(
                "Failed to rename {path}: {error}").format(path=file_path, error=e))
            QMessageBox.critical(host, ui_tr_now("Rename failed"),
                                 ui_tr_now("Could not rename:") + f"\n{file_path}\n\n{e}")
        finally:
            local_explorer_refresh()

    def _local_send_via_nextsync(path, name, is_dir, cleanup=None):
        """'Send via NextSync <name>' on the SD tab's local pane: push the
        clicked file/folder to the connected Next through the live Remote
        Explorer '.sync5 -listen' session, into the Next directory it
        currently shows. Mirrors the gallery panes' _re_try_send_folder
        (same toasts/log lines) but also takes single files; folders are
        recreated top-down by the widget (mkdir before the puts into it).
        *cleanup* (optional) runs exactly once, after the queued batch
        ends OR on any early exit — the image explorer's send passes its
        temp-download remover here, and the puts read the files until the
        batch is done, so it must never fire earlier."""
        cleanup = cleanup or (lambda: None)
        widget = getattr(host, "_re_widget", None)
        if widget is None:
            cleanup()
            return
        target = widget.remote_cwd()
        base = target if target.endswith("/") else target + "/"
        # The remote paths the upload will create (kept in step with
        # RemoteExplorerWidget._enqueue_dir_upload), for the success toast.
        sent = []
        if is_dir:
            top = os.path.basename(os.path.normpath(path).rstrip("/\\")) or "dir"
            for _root, _dirs, _files in os.walk(path):
                _dirs.sort()
                rel = os.path.relpath(_root, path).replace(os.sep, "/")
                rdir = base + top if rel in (".", "") else base + top + "/" + rel
                for _fname in sorted(_files):
                    sent.append(rdir + "/" + _fname)
            if not sent:
                add_main_log_window(ui_tr_now(
                    "Send via NextSync: nothing to send in {folder}.").format(
                        folder=path))
                cleanup()
                return
        else:
            sent.append(base + name)

        def _done(ok, fails):
            cleanup()
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
            [path], title="Sending via Remote Explorer…", on_done=_done)
        if state == "busy":
            host._show_toast(
                "Remote Explorer is busy",
                "Another transfer is still running — wait for it to "
                "finish, then try again.",
                variant="yellow", duration_ms=8000)
            cleanup()
            return
        if state == "offline":
            # The Next dropped off between the right-click and the click.
            host._show_toast(
                "You have started a Remote Explorer nextsync server already",
                "Start '.sync5 -L' (-l or -listen) on your Next and retry "
                "again (canceling the upload / send process for now).",
                variant="yellow", duration_ms=30000)
            cleanup()
            return
        if state != "queued":
            cleanup()
            return
        add_main_log_window(ui_tr_now(
            "Sending {folder} via Remote Explorer (-listen) → {target} …"
        ).format(folder=path, target=target))

    def _local_start_re_server():
        """'Start NextSync Remote Explorer' on the SD tab's local pane:
        bring the NextSync tab's '.sync5 -listen' server up without leaving
        the SD tab. Mirrors the -start-remote-explorer-listener startup
        hook: the Remote Explorer view is forced open first, so the widget
        and the server closures the toggle talks to all exist (the
        _re_open_restoring guard keeps the transient view switch out of
        the saved settings), then the server starts through the shared
        toggle — inheriting its remaining guards (the classic-server-
        holds-port-2048 advisory)."""
        if getattr(host, "_re_running", False):
            return                      # started elsewhere meanwhile
        if not getattr(host, "_re_sync_root", ""):
            # The listen server refuses to start without a sync root; say
            # so with a toast HERE instead of a log line on another tab.
            host._show_toast(
                "Remote Explorer NextSync server not started",
                "Please select a sync root first on the NextSync Remote "
                "Explorer tab and retry.",
                variant="yellow", duration_ms=10000)
            return
        tabs = getattr(host, "nextsync_mode_tabs", None)
        if tabs is not None and tabs.currentIndex() != 0:
            host._re_open_restoring = True
            try:
                tabs.setCurrentIndex(0)     # tab 0 = Remote Explorer view
            finally:
                host._re_open_restoring = False
        toggle = getattr(host, "_nextsync_re_toggle_server", None)
        if toggle is not None:
            toggle()

    def _local_stop_re_server():
        """'Stop NextSync Remote Explorer': say goodbye to a connected
        Next, then shut the '.sync5 -listen' server down (the exposed stop
        closure is idempotent and handles the in-flight transfer)."""
        stop = getattr(host, "_nextsync_stop_listen_server_fn", None)
        if stop is not None and getattr(host, "_re_running", False):
            stop()

    # Copies the selected file to image
    def on_treeview_context_menu(pos):
        index = host.treeview.indexAt(pos)
        menu = QMenu(host.treeview)
        source_index = host.proxy_model.mapToSource(index) if index.isValid() else None
        name = host.model.fileName(source_index) if source_index is not None else ""
        # Empty space or the ".." up-entry: only offer "Create new directory",
        # targeting the folder currently shown at the top of the tree.
        if source_index is None or name == "..":
            target_dir = local_current_view_dir()
            menu.addAction(ui_tr_now("Create new directory…"),
                           lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                               target_dir, local_explorer_refresh, add_main_log_window)))
            action_paste = menu.addAction(ui_tr_now("Paste"))
            action_paste.setEnabled(_explorer_clipboard_has_items())
            action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_local(
                target_dir, local_explorer_refresh, add_main_log_window)))
            menu.exec(host.treeview.viewport().mapToGlobal(pos))
            return
        file_path = host.model.filePath(source_index)
        is_dir = host.model.isDir(source_index)
        # A new folder lands inside a folder, or in a file's parent folder.
        new_dir_target = file_path if is_dir else os.path.dirname(file_path)
        # "Open" hands the clicked item to the OS shell (associated app for
        # a file, file manager for a folder). Deferred like the launchers so
        # the menu's grab is released before anything appears on screen.
        action_open = QAction(ui_tr_now("Open"), host.treeview)

        def _open_with_shell(_p=file_path, _n=name):
            if not open_path_with_system_shell(_p):
                add_main_log_window(ui_tr_now(
                    "Open: the system could not open {name}.").format(name=_n))
        action_open.triggered.connect(
            lambda: QTimer.singleShot(0, _open_with_shell))
        action_copy_text = QAction(ui_tr_now("Copy text to clipboard"), host.treeview)
        action_copy_path = QAction(ui_tr_now("Copy path to clipboard"), host.treeview)
        action_copy = QAction(ui_tr_now("Copy"), host.treeview)
        action_cut = QAction(ui_tr_now("Cut"), host.treeview)
        action_paste = QAction(ui_tr_now("Paste"), host.treeview)
        action_newdir = QAction(ui_tr_now("Create new directory…"), host.treeview)
        action_rename = QAction(ui_tr_now("Rename"), host.treeview)
        action_copy_text.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
        action_copy_path.triggered.connect(lambda: QGuiApplication.clipboard().setText(file_path))
        action_copy.triggered.connect(lambda: _local_explorer_copy_selection())
        action_cut.triggered.connect(lambda: _local_explorer_copy_selection(mode="cut"))
        action_paste.setEnabled(_explorer_clipboard_has_items())
        action_paste.triggered.connect(lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_local(
            new_dir_target, local_explorer_refresh, add_main_log_window)))
        # Defer the dialogs until the menu's modal loop has closed (showing a
        # QInputDialog from inside menu.exec() fights the menu's input grab).
        action_newdir.triggered.connect(
            lambda: QTimer.singleShot(0, lambda: _local_make_directory(
                new_dir_target, local_explorer_refresh, add_main_log_window)))
        action_rename.triggered.connect(
            lambda: QTimer.singleShot(0, lambda: local_explorer_rename_item(file_path, name, is_dir)))
        # Delete acts on the selection (or the clicked item); the Del key
        # runs the same handler. Deferred like the other dialog-openers.
        action_delete = QAction(ui_tr_now("Delete"), host.treeview)
        action_delete.triggered.connect(
            lambda: QTimer.singleShot(0, local_explorer_delete_selection))
        # Zip actions (mirroring the Remote Explorer's local pane): "Unzip
        # file" only on a .zip file, "Zip" archives the selection (or the
        # clicked item) into <first item>.zip next to it. Deferred like the
        # other dialog-openers so the menu's grab is released first.
        action_unzip = QAction(ui_tr_now("Unzip file"), host.treeview)
        action_unzip.triggered.connect(
            lambda: QTimer.singleShot(0, lambda: _local_unzip_file(
                file_path, local_explorer_refresh, add_main_log_window)))
        action_zip = QAction(ui_tr_now("Zip"), host.treeview)
        action_zip.triggered.connect(
            lambda: QTimer.singleShot(0, lambda: _local_zip_selection(
                _local_explorer_selected_paths_or(file_path),
                local_explorer_refresh, add_main_log_window)))
        # "Start <emulator> with <file>" — very top of the menu, the same
        # entries the NextSync tab's local explorers offer. These boot the
        # local file as it is, with no transfer: the emulator reads it from
        # the PC. The "Send to SD Card and start …" pair below does the other
        # job — putting the file on the card first — so both are offered and
        # the plain one comes first, being the cheaper of the two.
        _emu_direct = emulator_autostart_entries(host, file_path, is_dir)
        for _entry in _emu_direct:
            menu.addAction(
                _entry.label,
                lambda _e=_entry: QTimer.singleShot(
                    0, lambda: _e.launch(file_path)))
        if _emu_direct:
            menu.addSeparator()

        # "Send to SD Card and start CSpect with <file>" — offered only with
        # CSpect installed, an image loaded, and a file CSpect can boot. It
        # uploads into the folder the image explorer is showing and, once that
        # succeeds, starts CSpect on the local copy (a host path — see
        # _cspect_start_from_image for why it cannot be the in-image one).
        if (not is_dir and emulator_offers_autostart(file_path)
                and _right_disk_content()
                and getattr(host, "_cspect_executable_path", None)
                and getattr(host, "_launch_cspect_fn", None)
                and getattr(host, "image_upload_external_paths", None)):
            def _send_and_start(_p=file_path):
                target = (host.diskimageexplorerpathinput.text() or "/").strip()
                target = ("/" + target.strip("/")) if target.strip("/") else "/"

                def _after(success):
                    if not success:
                        add_main_log_window(ui_tr_now(
                            "Send to SD Card and start CSpect: the transfer "
                            "failed, CSpect was not started."))
                        return
                    # CSpect is started on the LOCAL file, not on the copy just
                    # written into the image: its trailing argument is resolved
                    # against CSpect's working directory, so it must be a host
                    # path. The image copy is what the program will find on the
                    # SD card once it is running.
                    host._launch_cspect_fn(_p)
                add_main_log_window(ui_tr_now(
                    "Sending {name} to the SD card image, then starting "
                    "CSpect…").format(name=os.path.basename(_p)))
                host.image_upload_external_paths([_p], target, on_complete=_after)
            menu.addAction(
                ui_tr_now("Send to SD Card and start CSpect with file {name}"
                          ).format(name=name),
                lambda: QTimer.singleShot(0, _send_and_start))
            menu.addSeparator()

        # The MAME twin of the action above. Same shape, same reason for
        # starting on the LOCAL file rather than the copy just written into
        # the image: MAME cannot read a path inside the mounted image.
        if (not is_dir and emulator_offers_autostart(file_path)
                and _right_disk_content()
                and host._mame_usable()
                and getattr(host, "_launch_mame_fn", None)
                and getattr(host, "image_upload_external_paths", None)):
            def _send_and_start_mame(_p=file_path):
                target = (host.diskimageexplorerpathinput.text() or "/").strip()
                target = ("/" + target.strip("/")) if target.strip("/") else "/"

                def _after(success):
                    if not success:
                        add_main_log_window(ui_tr_now(
                            "Send to SD Card and start MAME: the transfer "
                            "failed, MAME was not started."))
                        return
                    host._launch_mame_fn(_p)
                add_main_log_window(ui_tr_now(
                    "Sending {name} to the SD card image, then starting "
                    "MAME…").format(name=os.path.basename(_p)))
                host.image_upload_external_paths([_p], target, on_complete=_after)
            menu.addAction(
                ui_tr_now("Send to SD Card and start MAME with file {name}"
                          ).format(name=name),
                lambda: QTimer.singleShot(0, _send_and_start_mame))
            menu.addSeparator()

        # --- NextSync Remote Explorer (driven from the SD tab) ---
        # "Send via NextSync <name>" — offered only while the NextSync tab's
        # Remote Explorer '.sync5 -listen' session has a Next connected: the
        # clicked file/folder goes over Wi-Fi into the Next directory the
        # Remote Explorer currently shows, the same route as the gallery
        # panes' "Send via NextSync" buttons. Below it the server itself is
        # started/stopped (by RUNNING state, not connection: a listener
        # waiting for its Next cannot be started twice — only stopped), so
        # the whole round-trip works without leaving the SD tab.
        _re_running = getattr(host, "_re_running", False)
        if (_re_running
                and getattr(getattr(host, "_re_widget", None),
                            "_connected", False)):
            menu.addAction(
                ui_tr_now("Send via NextSync {name}").format(name=name),
                lambda: QTimer.singleShot(0, lambda: _local_send_via_nextsync(
                    file_path, name, is_dir)))
        if _re_running:
            menu.addAction(
                ui_tr_now("Stop NextSync Remote Explorer"),
                lambda: QTimer.singleShot(0, _local_stop_re_server))
        else:
            menu.addAction(
                ui_tr_now("Start NextSync Remote Explorer"),
                lambda: QTimer.singleShot(0, _local_start_re_server))
        menu.addSeparator()

        menu.addAction(action_open)
        menu.addSeparator()
        menu.addAction(action_copy_text)
        menu.addAction(action_copy_path)
        menu.addSeparator()
        if not is_dir and name.lower().endswith(".zip"):
            menu.addAction(action_unzip)
        menu.addAction(action_zip)
        menu.addSeparator()
        menu.addAction(action_copy)
        menu.addAction(action_cut)
        menu.addAction(action_paste)
        menu.addAction(action_newdir)
        menu.addAction(action_rename)
        menu.addAction(action_delete)
        menu.exec(host.treeview.viewport().mapToGlobal(pos))

    # ---- SD Card explorer pane delegation (strangler seam) --------------
    # The explorer pair's navigation/model layer lives in
    # zxnu_sdcard_explorer.SdCardExplorerPane (constructed further below).
    # These thin wrappers keep the historical closure names alive for the
    # operation layer; new code should call the pane directly.
    def local_explorer_refresh():
        host.sdcard_explorer.local_explorer_refresh()

    def local_current_view_dir():
        return host.sdcard_explorer.local_current_view_dir()

    def local_sync_path_box():
        host.sdcard_explorer.local_sync_path_box()

    def _suspend_local_fs_watchers(suspend):
        """Drop (True) / restore (False) the file watchers of every local
        QFileSystemModel (SD-card tab, NextSync classic tab, and the
        Remote Explorer's local pane — they all browse the same disk).
        On Windows, deleting a folder a model has listed (= watches)
        leaves the watcher's FindFirstChangeNotification handle pointing
        at a pending-delete directory: Qt's watcher thread then spams
        'FindNextChangeNotification failed ... (Access is denied.)' and,
        with enough watched subfolders, that storm freezes the whole UI
        (the QTBUG-65683 family). Suspending clears every watch handle up
        front; the post-delete refresh re-lists (and re-watches) whatever
        is on screen."""
        models = [host.model, host.nextsync_filesystem_model]
        re_widget = getattr(host, "_re_widget", None)
        if re_widget is not None:
            models.append(re_widget.local_model)
        for m in models:
            try:
                m.setOption(QFileSystemModel.Option.DontWatchForChanges, suspend)
            except Exception:
                pass

    def _deletes_go_to_recycle_bin():
        """True when local-explorer deletes should go to the Recycle Bin:
        the Settings toggle is on AND the optional Send2Trash package is
        available. Image-side deletes never consult this (a virtual FAT
        filesystem has no bin)."""
        return (send2trash_available()
                and host.settings_delete_to_recycle_bin_checkbox.isChecked())

    def _local_delete_paths_async(items, log_fn):
        """Delete local *items* [(path, is_dir), …] on a worker thread with
        a progress dialog, with every local file watcher suspended for the
        duration (see _suspend_local_fs_watchers — deleting watched
        subfolders used to hang the UI). Shared by the SD-card and NextSync
        classic explorers' Delete actions. Refreshes both local explorers
        when done (same filesystem) and shows one summary box if anything
        could not be deleted.

        Honours the "Send deleted files to the Recycle Bin" setting: when
        on (and Send2Trash is installed) every item is trashed instead of
        removed; a failed trash is reported as a failure — it never falls
        back to a permanent delete the user did not ask for."""
        use_recycle = _deletes_go_to_recycle_bin()
        _suspend_local_fs_watchers(True)
        holder = {"deleted": [], "failed": []}

        def _task(signals, cancel_event, _items=items, _h=holder,
                  _recycle=use_recycle):
            signals.progress.emit(-1)   # marquee: deletion has no total %

            def _force_remove(func, path, exc_info):
                # shutil.rmtree onerror: clear a Windows read-only
                # attribute and retry, so the delete proceeds instead of
                # erroring out.
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except OSError:
                    pass

            verb = "Sending to Recycle Bin…" if _recycle else "Deleting…"
            for p, is_dir in _items:
                if cancel_event.is_set():
                    return
                signals.status.emit(f"{verb}\n{p}")
                try:
                    if _recycle:
                        from send2trash import send2trash
                        # send2trash refuses forward slashes on Windows.
                        send2trash(os.path.normpath(p))
                        if os.path.exists(p):
                            raise OSError(
                                "still present after sending to the Recycle Bin")
                    elif is_dir and not os.path.islink(p):
                        shutil.rmtree(p, onerror=_force_remove)
                        if os.path.exists(p):
                            # onerror swallowed a real failure and left
                            # part of the tree behind — surface it instead
                            # of silently claiming success (the old code's
                            # failure mode with watched subfolders).
                            raise OSError("some content could not be removed")
                    else:
                        os.remove(p)
                    _h["deleted"].append(p)
                except OSError as e:
                    logging.error(f"Failed to delete {p}: {e}", exc_info=True)
                    _h["failed"].append((p, str(e)))

        dlg    = HdfProgressDialog("Deleting…", host)
        worker = HdfTaskWorker(_task)
        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(log_fn)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_delete_finished():
            dlg.close()
            _suspend_local_fs_watchers(False)
            for p in holder["deleted"]:
                log_fn(f"Sent to Recycle Bin: {p}" if use_recycle
                       else f"Deleted: {p}")
            for p, err in holder["failed"]:
                log_fn(f"Failed to delete {p}: {err}")
            # Both local explorers browse the same filesystem: refresh both.
            local_explorer_refresh()
            nextsync_refresh_explorer()
            if holder["failed"]:
                listing = "\n".join(p for p, _e in holder["failed"][:10])
                if len(holder["failed"]) > 10:
                    listing += "\n" + ui_tr_now("… and {n} more").format(n=len(holder['failed']) - 10)
                QMessageBox.critical(host, ui_tr_now("Delete failed"),
                                     ui_tr_now("Could not delete:") + f"\n{listing}")

        worker.signals.finished.connect(_on_delete_finished)
        host.threadpool.start(worker)
        dlg.exec()

    def local_explorer_delete_selection():
        """Delete the SD-card local explorer's selected files/folders
        (Del key / context menu). Honours the "Do not prompt for
        confirmation on deletion" setting like the NextSync explorer's
        delete; otherwise asks first — folders warn that all their
        contents go too, multi-selections are confirmed as one batch."""
        fallback = ""
        cur = host.treeview.currentIndex()
        if cur.isValid():
            src = host.proxy_model.mapToSource(cur)
            if host.model.fileName(src) != "..":
                fallback = host.model.filePath(src)
        paths = [p for p in _local_explorer_selected_paths_or(fallback)
                 if p and os.path.exists(p)]
        if not paths:
            return
        items = [(p, os.path.isdir(p)) for p in paths]
        if not host.settings_no_prompt_on_deletion_checkbox.isChecked():
            if len(items) == 1:
                p, is_dir = items[0]
                name = os.path.basename(p.rstrip("/\\")) or p
                msg = (ui_tr_now('Delete the folder "{name}" and all of its '
                                 'contents?').format(name=name) if is_dir
                       else ui_tr_now('Delete the file "{name}"?')
                       .format(name=name)) + f"\n\n{p}"
            else:
                listing = "\n".join(p for p, _d in items[:15])
                if len(items) > 15:
                    listing += "\n" + ui_tr_now("… and {n} more").format(
                        n=len(items) - 15)
                msg = (ui_tr_now("Delete these {count} items? Folders are "
                                 "deleted with all of their contents.")
                       .format(count=len(items)) + f"\n\n{listing}")
            msg += "\n\n" + (
                ui_tr_now("Deleted files are sent to the Recycle Bin.")
                if _deletes_go_to_recycle_bin()
                else ui_tr_now("This cannot be undone."))
            if QMessageBox.question(
                    host, ui_tr_now("Confirm deletion"), msg,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return

        _local_delete_paths_async(items, add_main_log_window)

    def _local_unzip_file(zip_path, refresh_fn, log_fn):
        """'Unzip file' on a local explorer's .zip: extract it into its
        own folder (cancellable per-file progress dialog; zip-slip entries
        skipped). Shared by the SD-card local explorer; a cancel keeps
        what was already extracted."""
        name = os.path.basename(zip_path)
        dest = os.path.dirname(zip_path) or "."
        res = zip_extract_with_dialog(host, zip_path, dest, log=log_fn)
        if res["cancelled"]:
            log_fn(f"Unzip of {name} cancelled — already-extracted files remain.")
        elif res["error"]:
            log_fn(f"ERROR: could not extract {name}: {res['error']}")
            QMessageBox.critical(host, ui_tr_now("Unzip failed"),
                                 ui_tr_now("Could not extract {name}:").format(name=name) + f"\n{res['error']}")
        else:
            skipped = res["skipped"]
            extra = (f" ({skipped} unsafe "
                     f"{'entry' if skipped == 1 else 'entries'} skipped)"
                     if skipped else "")
            log_fn(f"Extracted {res['files']} file(s) from {name} into {dest}.{extra}")
        refresh_fn()

    def _local_zip_selection(paths, refresh_fn, log_fn):
        """'Zip' on a local explorer selection: build <first item>.zip next
        to it (name uniquified against the folder), cancellable with
        per-file progress."""
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
        res = zip_create_with_dialog(host, paths, zip_local, log=log_fn)
        if res["cancelled"]:
            log_fn(f"Zip cancelled — {zip_name} was not created.")
        elif res["error"]:
            log_fn(f"ERROR: could not create {zip_name}: {res['error']}")
            QMessageBox.critical(host, ui_tr_now("Zip failed"),
                                 ui_tr_now("Could not create {name}:").format(name=zip_name) + f"\n{res['error']}")
        else:
            log_fn(f"Created {zip_name} in {dest} ({res['files']} file(s)).")
        refresh_fn()

    def _local_explorer_selected_paths_or(fallback_path):
        """The SD-card local tree's multi-selection paths (minus '..'), or
        the given fallback when nothing is selected."""
        paths = []
        for ix in host.treeview.selectionModel().selectedRows(0):
            src = host.proxy_model.mapToSource(ix)
            if host.model.fileName(src) == "..":
                continue
            paths.append(host.model.filePath(src))
        return paths or [fallback_path]

    def local_explorer_import_external_paths(paths, dest_dir, refresh_fn=None,
                                             on_complete=None):
        """Copy files/folders dropped from the OS file manager into dest_dir
        (local filesystem) on a background thread with a progress dialog.
        Reuses the shared copy worker; never overwrites (name clashes get a
        '-(copy)' suffix) and refreshes the explorer when done. *refresh_fn*
        selects which explorer to re-stat (defaults to the SD-card local one);
        the NextSync paste passes its own refresher. *on_complete*, when given,
        is called with a single bool (True only if the transfer finished without
        error or cancellation) — used by cut+paste to remove the source after."""
        if not dest_dir or not os.path.isdir(dest_dir):
            add_main_log_window(ui_tr_now(
                "Import failed: no valid destination folder."))
            return

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
                    add_main_log_window(ui_tr_now(
                        "Skipped {path}: cannot import a folder into itself."
                    ).format(path=src))
                    continue
            base = os.path.basename(src.rstrip("/\\"))
            target = _nextsync_unique_path(os.path.join(dest_dir, base), src_is_dir)
            items.append((src, target, src_is_dir))

        if not items:
            if on_complete:
                on_complete(False)
            return

        dlg    = HdfProgressDialog("Importing into folder…", host)
        worker = HdfTaskWorker(_run_nextsync_import_task, items)
        outcome = {"error": False, "cancelled": False}

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.error.connect(lambda *_: outcome.update(error=True))
        worker.signals.cancelled.connect(dlg.mark_cancelled)
        worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

        def _on_local_import_finished():
            dlg.close()
            (refresh_fn or local_explorer_refresh)()
            if on_complete:
                on_complete(not outcome["error"] and not outcome["cancelled"])

        worker.signals.finished.connect(_on_local_import_finished)
        host.threadpool.start(worker)
        dlg.exec()

    # Consumed by bare name elsewhere in __init__ (re-bound at the call site).
    # The three Remote Explorer helpers are also consumed by the IMAGE
    # explorer's context menu (build_transfer_clipboard_ops), resolved via
    # getattr at click time.
    host._deletes_go_to_recycle_bin = _deletes_go_to_recycle_bin
    host._local_delete_paths_async = _local_delete_paths_async
    host._local_make_directory = _local_make_directory
    host._local_send_via_nextsync = _local_send_via_nextsync
    host._local_start_re_server = _local_start_re_server
    host._local_stop_re_server = _local_stop_re_server
    host.local_explorer_delete_selection = local_explorer_delete_selection
    host.local_explorer_import_external_paths = local_explorer_import_external_paths
    host.local_explorer_refresh = local_explorer_refresh
    host.local_explorer_rename_item = local_explorer_rename_item
    host.local_sync_path_box = local_sync_path_box
    host.on_treeview_context_menu = on_treeview_context_menu
    host.local_current_view_dir = local_current_view_dir


def build_transfer_clipboard_ops(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    _right_disk_content,
    set_all_buttons_disabled,
    set_all_buttons_enabled,
    nextsync_hide_start_cancel_buttons,
    nextsync_show_start_cancel_buttons,
    delete_files_button_show_confirmation_buttons,
    add_main_log_window,
    add_nextsync_log_window,
    set_treeview_properties,
    image_newfolder_dialog,
    _warn_if_image_nearly_full,
    _check_image_writable,
    execute_hdf_monkey,
    image_rename_dialog,
    nextsync_refresh_explorer,
    local_explorer_refresh,
    local_current_view_dir,
    local_sync_path_box,
    local_explorer_import_external_paths,
    nextsync_current_view_dir,
):
    """Transfers, the shared explorer clipboard, remote zip and context menu."""
    def transfert_content_from_image_to_disk():


        if not _right_disk_content():
            logging.warning("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image file first !"))
            return

        set_all_buttons_disabled()

        selected_explorer_item_directory_destination = ""
        if host.left_file_explorer_selection_full_filename_path:
            if not os.path.isdir(host.left_file_explorer_selection_full_filename_path):
                parts = host.left_file_explorer_selection_full_filename_path.split('/')
                selected_explorer_item_directory_destination = "/".join(parts[:-1]) + "/"
            else:
                selected_explorer_item_directory_destination = host.left_file_explorer_selection_full_filename_path
        else:
            set_all_buttons_enabled()
            return

        is_windows = platform.system() == "Windows"
        if is_windows:
            selected_explorer_item_directory_destination = selected_explorer_item_directory_destination.replace("/", "\\")
            directory_navigation = "\\"
        else:
            directory_navigation = "/"

        if not host.image_selected_path:
            set_all_buttons_enabled()
            return

        base_name  = host.image_selected_path.rstrip("/").rsplit("/", 1)[-1]
        items      = [(host.image_selected_path, base_name)]
        image_path = host.right_disk_image_path

        dlg    = HdfProgressDialog("Downloading from image\u2026", host)
        worker = HdfTaskWorker(_run_get_task, execute_hdf_monkey, image_path, items,
                               selected_explorer_item_directory_destination,
                               directory_navigation, is_windows)

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_get_finished():
            dlg.close()
            set_all_buttons_enabled()

        worker.signals.finished.connect(_on_get_finished)
        host.threadpool.start(worker)
        dlg.exec()

    def image_get_paths_to_local(image_items, dest_dir, refresh_fn=None,
                                 on_complete=None):
        """Download image entries dragged out of the SD-card image tree into a
        local folder. *image_items* is a list of (image_path, is_dir); *dest_dir*
        is the local directory the drop landed on. This is the drag-and-drop
        equivalent of transfert_content_from_image_to_disk (the ':<-' button),
        but driven by the drag source/target rather than the current selections,
        and able to handle several dragged entries at once. *refresh_fn* selects
        which local explorer to re-stat afterwards (defaults to the SD-card one;
        an image->NextSync paste passes the NextSync refresher)."""

        if not _right_disk_content():
            logging.warning("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image file first !"))
            return

        if not dest_dir or not os.path.isdir(dest_dir):
            add_main_log_window(ui_tr_now(
                "Download failed: no valid destination folder."))
            return

        items = []
        for path, _is_dir in image_items:
            if not path:
                continue
            base_name = path.rstrip("/").rsplit("/", 1)[-1]
            items.append((path, base_name))
        if not items:
            return

        is_windows = platform.system() == "Windows"
        # _run_get_task joins dest + dir_nav + base, so strip any trailing
        # separator from the dropped folder to avoid a doubled separator.
        dest = dest_dir.rstrip("/\\")
        if is_windows:
            dest = dest.replace("/", "\\")
            directory_navigation = "\\"
        else:
            directory_navigation = "/"

        set_all_buttons_disabled()

        image_path = host.right_disk_image_path
        dlg    = HdfProgressDialog("Downloading from image…", host)
        worker = HdfTaskWorker(_run_get_task, execute_hdf_monkey, image_path, items, dest,
                               directory_navigation, is_windows)
        outcome = {"error": False, "cancelled": False}

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.error.connect(lambda *_: outcome.update(error=True))
        worker.signals.cancelled.connect(dlg.mark_cancelled)
        worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

        def _on_get_finished():
            dlg.close()
            (refresh_fn or local_explorer_refresh)()
            set_all_buttons_enabled()
            if on_complete:
                on_complete(not outcome["error"] and not outcome["cancelled"])

        worker.signals.finished.connect(_on_get_finished)
        host.threadpool.start(worker)
        dlg.exec()

    # ── Shared cross-explorer clipboard (Ctrl+C / Ctrl+V / Ctrl+X) ─────────
    # One buffer drives Copy/Paste across all three explorers: the SD-card
    # local tree, the NextSync local tree and the SD-card image tree. It
    # remembers the source kind ('local' real files vs 'image' in-image
    # paths) plus the selected (path, is_dir) entries; Paste then routes to
    # the right transfer helper based on source + destination:
    #   local -> local : local_explorer_import_external_paths
    #   local -> image : image_upload_external_paths
    #   image -> local : image_get_paths_to_local
    #   image -> image : image_copy_items_within (get to temp, then put)
    # Paste also accepts files copied in the OS file manager (Windows Explorer
    # etc.): if the system clipboard holds file URLs and was updated more
    # recently than our internal copy, those files are pasted instead.
    def _next_clip_serial():
        host._clip_serial_counter = getattr(host, "_clip_serial_counter", 0) + 1
        return host._clip_serial_counter

    def _on_os_clipboard_changed():
        # Any external clipboard change (e.g. Ctrl+C in Windows Explorer) makes
        # the system clipboard the most-recent copy source.
        host._os_clip_serial = _next_clip_serial()
    try:
        QGuiApplication.clipboard().dataChanged.connect(_on_os_clipboard_changed)
    except Exception:
        pass

    def _os_clipboard_files():
        """Local file paths currently on the system clipboard (or [])."""
        try:
            md = QGuiApplication.clipboard().mimeData()
        except Exception:
            return []
        if md is None or not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls()
                if u.isLocalFile() and u.toLocalFile()]

    def _os_clipboard_is_cut():
        """True when the files on the system clipboard were *cut* (move) rather
        than copied. Windows records this in the 'Preferred DropEffect' clipboard
        format — a little-endian DWORD where bit 1 (value 2) is DROPEFFECT_MOVE
        and bit 2 (value 5/copy) is a plain copy. Other OS file managers have no
        such convention, so a cut there is indistinguishable from a copy and we
        conservatively treat it as a copy."""
        try:
            md = QGuiApplication.clipboard().mimeData()
            if md is None:
                return False
            # Qt exposes the raw Windows clipboard format under a mangled name;
            # accept both the friendly and the qt-windows-mime spellings.
            for fmt in ("Preferred DropEffect",
                        'application/x-qt-windows-mime;value="Preferred DropEffect"'):
                if md.hasFormat(fmt):
                    raw = bytes(md.data(fmt))
                    if len(raw) >= 4:
                        effect = int.from_bytes(raw[:4], "little")
                        # DROPEFFECT_COPY == 1, DROPEFFECT_MOVE == 2: a cut sets
                        # the move bit (copy sets only bit 0, so 1 & 2 == 0).
                        return bool(effect & 2)
        except Exception:
            pass
        return False

    def _explorer_clipboard_set(source, items, log_fn=None, mode="copy"):
        items = [(p, bool(d)) for (p, d) in items if p]
        if not items:
            return
        host._explorer_clipboard = {"source": source, "items": items, "mode": mode}
        host._explorer_clip_serial = _next_clip_serial()
        if log_fn:
            names = ", ".join(os.path.basename(p.rstrip("/\\")) or p
                              for p, _ in items[:3])
            more = "" if len(items) <= 3 else f" (+{len(items) - 3} more)"
            verb = "Cut to" if mode == "cut" else "Copied to"
            log_fn(f"{verb} clipboard: {names}{more}")

    def _explorer_clipboard_clear():
        """Forget the internal clipboard buffer (used after a cut+paste move so
        a second paste cannot duplicate or re-move the now-relocated source)."""
        host._explorer_clipboard = None
        host._explorer_clip_serial = _next_clip_serial()

    def _explorer_effective_clip():
        """Resolve which copy source a Paste should use. Returns
        ('os', [paths], mode) for system-clipboard files, ('clip', clipdict, mode)
        for the internal buffer, or (None, None, None). *mode* is 'copy' or 'cut'.
        The most recently updated source wins; ties (e.g. files already on the
        clipboard at startup) favour the OS."""
        os_paths = _os_clipboard_files()
        clip = getattr(host, "_explorer_clipboard", None)
        clip_has = bool(clip and clip.get("items"))
        os_serial = getattr(host, "_os_clip_serial", 0)
        clip_serial = getattr(host, "_explorer_clip_serial", 0)
        if os_paths and (not clip_has or os_serial >= clip_serial):
            return ("os", os_paths, "cut" if _os_clipboard_is_cut() else "copy")
        if clip_has:
            return ("clip", clip, clip.get("mode", "copy"))
        if os_paths:
            return ("os", os_paths, "cut" if _os_clipboard_is_cut() else "copy")
        return (None, None, None)

    def _explorer_clipboard_has_items():
        kind, _data, _mode = _explorer_effective_clip()
        return kind is not None

    def _same_dir(path, dir_path):
        """True if *path* lives directly inside *dir_path* (case-insensitive on
        Windows). Used to make a cut+paste into the source's own folder a no-op."""
        try:
            parent = os.path.dirname(os.path.abspath(path.rstrip("/\\")))
            return os.path.normcase(parent) == os.path.normcase(os.path.abspath(dir_path))
        except Exception:
            return False

    def _image_name_in_dir(image_path, parent, name):
        """True if *name* already exists in the image directory *parent*."""
        res = execute_hdf_monkey("ls", image_path, extra_argv=[parent or "/"], silent=True)
        if res.returncode != 0:
            return False
        for n, _isdir, _sz in image_parse_ls(res.stdout):
            if n == name:
                return True
        return False

    def _image_unique_name(image_path, parent, base):
        """Return *base*, or a non-colliding '-(copy)' variant in *parent*."""
        if not _image_name_in_dir(image_path, parent, base):
            return base
        stem, ext = os.path.splitext(base)
        i = 1
        while True:
            suffix = "-(copy)" if i == 1 else f"-(copy) ({i})"
            candidate = f"{stem}{suffix}{ext}"
            if not _image_name_in_dir(image_path, parent, candidate):
                return candidate
            i += 1

    def _run_image_copy_task(signals, cancel_event, image_path, items, target_dir):
        """Worker: copy image entries to another place in the same image.
        hdfmonkey has no in-image copy, so each source is downloaded to a
        temp dir then re-uploaded under *target_dir* with a unique name."""
        tmp = tempfile.mkdtemp(prefix="zxnu_imgcopy_")
        try:
            is_windows = platform.system() == "Windows"
            dir_nav = "\\" if is_windows else "/"
            dest_tmp = tmp.rstrip("/\\")
            parent = target_dir.rstrip("/") or "/"
            for src, _isdir in items:
                if cancel_event.is_set():
                    break
                base = src.rstrip("/").rsplit("/", 1)[-1]
                # Download this source into the temp dir …
                _run_get_task(signals, cancel_event, execute_hdf_monkey, image_path, [(src, base)],
                              dest_tmp, dir_nav, is_windows)
                if cancel_event.is_set():
                    break
                # … then upload it back under a unique name in target_dir.
                local_path = os.path.join(dest_tmp, base)
                unique = _image_unique_name(image_path, parent, base)
                dest_image = (parent.rstrip("/") + "/" + unique).replace("//", "/")
                _run_put_task(signals, cancel_event, execute_hdf_monkey,
                              _check_access_denied_is_full_disk,
                              image_path, local_path, dest_image)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def image_copy_items_within(image_items, target_dir, on_complete=None):
        """Paste image clipboard entries into another image folder (image->image).
        *on_complete*, when given, is called with a single bool (True only if the
        copy finished without error or cancellation) so cut+paste can remove the
        source entries afterward."""
        if not _right_disk_content():
            add_main_log_window(ui_tr_now("Please load an image file first !"))
            return
        img_err = _check_image_writable(host.right_disk_image_path)
        if img_err:
            logging.error(img_err)
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return
        items = [(p, d) for (p, d) in image_items if p]
        if not items:
            return
        target = (target_dir or "/").rstrip("/") or "/"
        set_all_buttons_disabled()
        image_path = host.right_disk_image_path
        dlg    = HdfProgressDialog("Copying within image…", host)
        worker = HdfTaskWorker(_run_image_copy_task, image_path, items, target)
        outcome = {"error": False, "cancelled": False}

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.error.connect(lambda *_: outcome.update(error=True))
        worker.signals.cancelled.connect(dlg.mark_cancelled)
        worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

        def _on_done():
            dlg.close()
            image_reload_dir(target or "/")
            set_all_buttons_enabled()
            if on_complete:
                on_complete(not outcome["error"] and not outcome["cancelled"])

        worker.signals.finished.connect(_on_done)
        host.threadpool.start(worker)
        dlg.exec()

    def _delete_local_paths_after_move(paths, log_fn):
        """Silently remove real-filesystem source files/folders once a cut+paste
        copy has succeeded (this is what turns a copy into a move). Clears the
        Windows read-only attribute on the way down so rmtree cannot stall."""
        def _force_remove(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except OSError:
                pass
        for p in paths:
            try:
                if not p or not os.path.exists(p):
                    continue
                if os.path.isdir(p):
                    shutil.rmtree(p, onerror=_force_remove)
                else:
                    os.remove(p)
                if log_fn:
                    log_fn(f"Moved (removed source): {p}")
            except OSError as e:
                logging.error(f"Cut: failed to remove source {p}: {e}", exc_info=True)
                if log_fn:
                    log_fn(f"Cut: failed to remove source {p}: {e}")

    def _delete_image_paths_after_move(paths, log_fn):
        """Remove in-image source entries once a cut+paste copy has succeeded.
        hdfmonkey deletes run on the thread pool with a progress dialog, then the
        affected source folders are re-listed so the moved entries disappear."""
        paths = [p for p in paths if p and p != UP_DIRECTORY]
        if not paths:
            return
        image_path = host.right_disk_image_path
        parents = []
        for p in paths:
            parent = p.rstrip("/").rsplit("/", 1)[0] or "/"
            if parent not in parents:
                parents.append(parent)
        set_all_buttons_disabled()
        dlg    = HdfProgressDialog("Removing moved source…", host)
        worker = HdfTaskWorker(_run_delete_task, execute_hdf_monkey, image_path, paths)

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _done():
            dlg.close()
            for parent in parents:
                image_reload_dir(parent)
            set_all_buttons_enabled()
            if log_fn:
                log_fn("Moved: removed source from image.")

        worker.signals.finished.connect(_done)
        host.threadpool.start(worker)
        dlg.exec()

    def _explorer_paste_into_local(dest_dir, refresh_fn, log_fn):
        """Paste into a real-filesystem directory (either local explorer):
        OS-clipboard files, a local->local copy, or an image->local download.
        When the clipboard is in 'cut' mode the source is removed after a
        successful copy, turning the paste into a move."""
        kind, data, mode = _explorer_effective_clip()
        if kind is None:
            return
        if not dest_dir or not os.path.isdir(dest_dir):
            log_fn("Paste failed: no valid destination folder.")
            return
        is_cut = (mode == "cut")

        if kind == "os":
            src_paths = list(data)
            def _after(success, _p=src_paths):
                if success and is_cut:
                    _delete_local_paths_after_move(_p, log_fn)
                    local_explorer_refresh()
                    nextsync_refresh_explorer()
                    try:
                        QGuiApplication.clipboard().clear()
                    except Exception:
                        pass
            local_explorer_import_external_paths(data, dest_dir, refresh_fn=refresh_fn,
                                                 on_complete=_after if is_cut else None)
        elif data["source"] == "local":
            paths = [p for (p, _d) in data["items"]]
            # Cutting into the source's own folder is a no-op move.
            if is_cut and paths and all(_same_dir(p, dest_dir) for p in paths):
                log_fn("Nothing to move: items are already in this folder.")
                _explorer_clipboard_clear()
                return
            def _after(success, _p=paths):
                if success and is_cut:
                    _delete_local_paths_after_move(_p, log_fn)
                    # Refresh both local explorers so the source view also
                    # reflects the removal, wherever the cut originated.
                    local_explorer_refresh()
                    nextsync_refresh_explorer()
                    _explorer_clipboard_clear()
            local_explorer_import_external_paths(paths, dest_dir, refresh_fn=refresh_fn,
                                                 on_complete=_after if is_cut else None)
        else:
            img_items = list(data["items"])
            img_paths = [p for (p, _d) in img_items]
            def _after(success, _p=img_paths):
                if success and is_cut:
                    # Defer so the download dialog's event loop fully unwinds
                    # before the delete dialog opens its own.
                    QTimer.singleShot(0, lambda: _delete_image_paths_after_move(_p, log_fn))
                    _explorer_clipboard_clear()
            image_get_paths_to_local(img_items, dest_dir, refresh_fn=refresh_fn,
                                     on_complete=_after if is_cut else None)

    def _explorer_paste_into_image(target_dir):
        """Paste into the SD-card image at *target_dir*: OS-clipboard files,
        a local->image upload, or an image->image copy. In 'cut' mode the source
        is removed after a successful copy, turning the paste into a move."""
        kind, data, mode = _explorer_effective_clip()
        if kind is None:
            return
        is_cut = (mode == "cut")

        if kind == "os":
            src_paths = list(data)
            def _after(success, _p=src_paths):
                if success and is_cut:
                    _delete_local_paths_after_move(_p, add_main_log_window)
                    local_explorer_refresh()
                    nextsync_refresh_explorer()
                    try:
                        QGuiApplication.clipboard().clear()
                    except Exception:
                        pass
            image_upload_external_paths(data, target_dir,
                                        on_complete=_after if is_cut else None)
        elif data["source"] == "local":
            paths = [p for (p, _d) in data["items"]]
            def _after(success, _p=paths):
                if success and is_cut:
                    _delete_local_paths_after_move(_p, add_main_log_window)
                    local_explorer_refresh()
                    nextsync_refresh_explorer()
            image_upload_external_paths(paths, target_dir,
                                        on_complete=_after if is_cut else None)
        else:
            img_items = list(data["items"])
            img_paths = [p for (p, _d) in img_items]
            target = (target_dir or "/").rstrip("/") or "/"
            # Cutting into the source's own image folder is a no-op move.
            if is_cut and img_paths and all(
                    (p.rstrip("/").rsplit("/", 1)[0] or "/") == target for p in img_paths):
                add_main_log_window(ui_tr_now(
                    "Nothing to move: items are already in this folder."))
                _explorer_clipboard_clear()
                return
            def _after(success, _p=img_paths):
                if success and is_cut:
                    # Defer so the copy dialog's event loop fully unwinds
                    # before the delete dialog opens its own.
                    QTimer.singleShot(0, lambda: _delete_image_paths_after_move(_p, add_main_log_window))
                    _explorer_clipboard_clear()
            image_copy_items_within(img_items, target_dir,
                                    on_complete=_after if is_cut else None)

    def _local_explorer_copy_selection(mode="copy"):
        """Copy (or, when mode='cut', cut) the SD-card local tree's selection to
        the shared clipboard."""
        items = []
        for ix in host.treeview.selectionModel().selectedRows(0):
            src = host.proxy_model.mapToSource(ix)
            if host.model.fileName(src) == "..":
                continue
            items.append((host.model.filePath(src), host.model.isDir(src)))
        if not items:
            cur = host.treeview.currentIndex()
            if cur.isValid():
                src = host.proxy_model.mapToSource(cur)
                if host.model.fileName(src) != "..":
                    items.append((host.model.filePath(src), host.model.isDir(src)))
        _explorer_clipboard_set("local", items, add_main_log_window, mode=mode)

    def _local_explorer_paste_target_dir():
        cur = host.treeview.currentIndex()
        if cur.isValid():
            src = host.proxy_model.mapToSource(cur)
            if host.model.fileName(src) != "..":
                path = host.model.filePath(src)
                return path if host.model.isDir(src) else os.path.dirname(path)
        return local_current_view_dir()

    def _nextsync_explorer_copy_selection(mode="copy"):
        """Copy (or, when mode='cut', cut) the NextSync local tree's selection to
        the shared clipboard."""
        items = []
        for ix in host.nextsync_treeview.selectionModel().selectedRows(0):
            src = host.nextsync_model.mapToSource(ix)
            if host.nextsync_filesystem_model.fileName(src) == "..":
                continue
            items.append((host.nextsync_filesystem_model.filePath(src),
                          host.nextsync_filesystem_model.isDir(src)))
        if not items:
            cur = host.nextsync_treeview.currentIndex()
            if cur.isValid():
                src = host.nextsync_model.mapToSource(cur)
                if host.nextsync_filesystem_model.fileName(src) != "..":
                    items.append((host.nextsync_filesystem_model.filePath(src),
                                  host.nextsync_filesystem_model.isDir(src)))
        _explorer_clipboard_set("local", items, add_nextsync_log_window, mode=mode)

    def _nextsync_explorer_paste_target_dir():
        cur = host.nextsync_treeview.currentIndex()
        if cur.isValid():
            src = host.nextsync_model.mapToSource(cur)
            if host.nextsync_filesystem_model.fileName(src) != "..":
                path = host.nextsync_filesystem_model.filePath(src)
                return path if host.nextsync_filesystem_model.isDir(src) else os.path.dirname(path)
        return nextsync_current_view_dir()

    def _image_explorer_copy_selection(mode="copy"):
        """Copy (or, when mode='cut', cut) the SD-card image tree's selection to
        the shared clipboard."""
        items = list(host.image_selected_paths)
        if not items and host.image_selected_path:
            items = [(host.image_selected_path, host.image_selected_is_dir)]
        _explorer_clipboard_set("image", items, add_main_log_window, mode=mode)


    def _check_access_denied_is_full_disk(image_path):
        """If hdfmonkey returns Access denied, check whether it is a full volume.
        Returns an error string if full, None otherwise."""
        err = _check_image_writable(image_path, check_free_space=True)
        if err and "volume is full" in err:
            return (
                "The image volume is full — no space left to write.\n"
                "Delete files from the image to free space, or switch to a larger image file.\n"
                "Larger SD card images (.img) can be downloaded from https://zxnext.uk/hosted/"
            )
        return None

    def image_upload_external_paths(paths, target_dir, on_complete=None):
        # (exposed on host below so the LOCAL explorer's menu — built by a
        # different builder — can upload a file before starting CSpect)
        """Copy local files/folders (e.g. dropped from Windows Explorer) into
        the loaded disk image under *target_dir*. *on_complete*, when given, is
        called with a single bool (True only if the upload finished without error
        or cancellation) so cut+paste can remove the local source after."""

        if not _right_disk_content():
            logging.warning("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image first!"))
            return

        img_err = _check_image_writable(host.right_disk_image_path)
        if img_err:
            logging.error(img_err)
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return

        if host.settings_warn_image_nearly_full_checkbox.isChecked():
            _warn_if_image_nearly_full(host.right_disk_image_path)

        dest_dir = (target_dir or "/").rstrip("/")
        items = []
        for p in paths:
            base = os.path.basename(p.rstrip("/\\"))
            if not base:
                continue
            dest = (dest_dir + "/" + base).replace("//", "/")
            items.append((p, dest))
        if not items:
            return

        set_all_buttons_disabled()

        image_path = host.right_disk_image_path
        reload_dir = dest_dir or "/"

        dlg    = HdfProgressDialog("Uploading to image…", host)
        worker = HdfTaskWorker(_run_put_external_task, execute_hdf_monkey,
                               _check_access_denied_is_full_disk, image_path, items)
        outcome = {"error": False, "cancelled": False}

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.error.connect(lambda *_: outcome.update(error=True))
        worker.signals.cancelled.connect(dlg.mark_cancelled)
        worker.signals.cancelled.connect(lambda: outcome.update(cancelled=True))

        def _on_external_put_finished():
            dlg.close()
            image_reload_dir(reload_dir)
            set_all_buttons_enabled()
            if on_complete:
                on_complete(not outcome["error"] and not outcome["cancelled"])

        worker.signals.finished.connect(_on_external_put_finished)
        host.threadpool.start(worker)
        dlg.exec()

    def transfert_content_from_disk_to_image():


        if not _right_disk_content():
            logging.warning("Please load an image file first !")
            add_main_log_window(ui_tr_now("Please load an image first!"))
            return

        img_err = _check_image_writable(host.right_disk_image_path)
        if img_err:
            logging.error(img_err)
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return

        if host.settings_warn_image_nearly_full_checkbox.isChecked():
            _warn_if_image_nearly_full(host.right_disk_image_path)

        set_all_buttons_disabled()

        dest_file_path = (generate_disk_file_path() + "/" + host.left_file_explorer_selection_file_name).replace('//', '/')

        upload_path = host.left_file_explorer_selection_full_filename_path
        if platform.system() == "Windows":
            upload_path = upload_path.replace("/", "\\")

        image_path      = host.right_disk_image_path
        sel_path        = host.left_file_explorer_selection_full_filename_path
        disk_path_fn    = generate_disk_file_path

        dlg    = HdfProgressDialog("Uploading to image\u2026", host)
        worker = HdfTaskWorker(_run_put_task, execute_hdf_monkey,
                               _check_access_denied_is_full_disk,
                               image_path, upload_path, dest_file_path)

        dlg.cancel_requested.connect(worker.cancel)
        worker.signals.progress.connect(dlg.set_progress)
        worker.signals.status.connect(dlg.set_status)
        worker.signals.error.connect(add_main_log_window)
        worker.signals.cancelled.connect(dlg.mark_cancelled)

        def _on_put_finished():
            dlg.close()
            display_path = sel_path
            if not os.path.isdir(display_path):
                display_path = os.path.dirname(display_path.rstrip("/\\")).replace("\\", "/") + "/"
            host.treeview.setRootIndex(host.proxy_model.mapFromSource(host.model.index(display_path, 0)))
            set_treeview_properties()
            host.treeview.show()
            local_sync_path_box()
            # Refresh the image tree asynchronously (the listing runs on a
            # worker thread, so finishing an upload never blocks the UI).
            update_disk_manager_widget_table()
            set_all_buttons_enabled()

        worker.signals.finished.connect(_on_put_finished)
        host.threadpool.start(worker)
        dlg.exec()


    # ---- SD Card image explorer delegation (strangler seam) -------------
    # The image tree's model/population/navigation layer lives in
    # zxnu_sdcard_explorer.SdCardExplorerPane; these wrappers keep the
    # historical names alive for the operation layer around them.
    def image_dest_dir():
        return host.sdcard_explorer.image_dest_dir()

    def generate_disk_file_path():
        # Kept under the original name so every existing call site works:
        # the directory that uploads / new folders / gallery sends target.
        return host.sdcard_explorer.image_dest_dir()

    def image_update_path_label():
        host.sdcard_explorer.image_update_path_label()

    def image_clear_model():
        host.sdcard_explorer.image_clear_model()

    def image_parse_ls(ls_stdout):
        return SdCardExplorerPane.image_parse_ls(ls_stdout)

    def image_make_row(name, is_dir, size_value, full_path):
        return host.sdcard_explorer.image_make_row(name, is_dir, size_value, full_path)

    def image_populate_item(parent_name_item, dir_path, on_done=None):
        host.sdcard_explorer.image_populate_item(parent_name_item, dir_path, on_done)

    def image_load_root(on_done=None):
        host.sdcard_explorer.image_load_root(on_done)

    def image_find_item(path):
        return host.sdcard_explorer.image_find_item(path)

    def image_reload_dir(path):
        host.sdcard_explorer.image_reload_dir(path)

    def image_navigate_to_path(path):
        host.sdcard_explorer.image_navigate_to_path(path)

    def apply_image_filter():
        host.sdcard_explorer.apply_image_filter()

    def set_table_image_properties():
        host.sdcard_explorer.set_table_image_properties()

    def update_disk_manager_widget_table(command_execution_content=None):
        # Refresh entry point kept under its original name. Callers invoke
        # this right after operating on the current target directory, so it
        # simply reloads that directory's node in the tree. The raw ls output
        # argument is no longer needed (the tree re-lists itself).
        image_reload_dir(image_dest_dir())

    # ── Remote Zip / Unzip on the SD-card image (hdfmonkey-staged) ──────
    # hdfmonkey cannot zip, so both actions stage through the PC exactly
    # like the Remote Explorer's Next-side versions: download (progress +
    # Cancel) -> zip/unzip locally (per-file progress + Cancel) -> upload
    # back into the image. Chained stages are deferred with a 0-timer so
    # each modal transfer dialog fully unwinds before the next opens.
    def _image_remote_unzip(zip_path):
        """'Remote Unzip file' on a .zip inside the image: get it to a
        temp dir, extract on the PC, upload the tree back into the zip's
        image folder. The image is only touched by the final upload."""
        if not _right_disk_content():
            return
        img_err = _check_image_writable(host.right_disk_image_path)
        if img_err:
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return
        name = zip_path.rstrip("/").rsplit("/", 1)[-1]
        dest_dir = zip_path.rstrip("/").rsplit("/", 1)[0] or "/"
        tmp = tempfile.mkdtemp(prefix="zxnu_imgunzip_")

        def _go(success):
            local_zip = os.path.join(tmp, name)
            if not success or not os.path.isfile(local_zip):
                shutil.rmtree(tmp, ignore_errors=True)
                add_main_log_window(ui_tr_now(
                    "Remote unzip: download from the image failed or was "
                    "cancelled — the image is unchanged."))
                return
            extract_dir = os.path.join(tmp, "_extracted")
            os.makedirs(extract_dir, exist_ok=True)
            res = zip_extract_with_dialog(host, local_zip, extract_dir,
                                          log=add_main_log_window)
            if not res["ok"] or res["files"] == 0:
                shutil.rmtree(tmp, ignore_errors=True)
                if res["cancelled"]:
                    add_main_log_window(ui_tr_now(
                        "Remote unzip cancelled — the image is unchanged."))
                elif res["error"]:
                    add_main_log_window(ui_tr_now(
                        "ERROR: could not extract {name}: {error}").format(
                            name=name, error=res["error"]))
                    QMessageBox.critical(
                        host, ui_tr_now("Remote unzip failed"),
                        ui_tr_now("Could not extract {name}:").format(name=name) + f"\n{res['error']}")
                else:
                    add_main_log_window(ui_tr_now(
                        "{name} contains no extractable files.").format(name=name))
                return
            tops = [os.path.join(extract_dir, e)
                    for e in sorted(os.listdir(extract_dir))]

            def _done(up_ok):
                shutil.rmtree(tmp, ignore_errors=True)
                if up_ok:
                    skipped = res["skipped"]
                    extra = (f" ({skipped} unsafe "
                             f"{'entry' if skipped == 1 else 'entries'} "
                             "skipped)" if skipped else "")
                    add_main_log_window(ui_tr_now(
                        "Extracted {count} file(s) from {name} into {folder} "
                        "on the image.").format(
                            count=res["files"], name=name, folder=dest_dir)
                        + extra)
                else:
                    add_main_log_window(ui_tr_now(
                        "Remote unzip: upload into the image failed or was "
                        "cancelled."))
            image_upload_external_paths(tops, dest_dir, on_complete=_done)

        add_main_log_window(ui_tr_now(
            "Remote unzip: fetching {path} from the image …").format(path=zip_path))
        image_get_paths_to_local(
            [(zip_path, False)], tmp, refresh_fn=lambda: None,
            on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

    def _cspect_start_from_image(image_path):
        """Boot a file that lives INSIDE the mounted image with CSpect.

        CSpect resolves its trailing file argument against its own working
        directory — a HOST path — not against the -mmc root, so passing the
        in-image path makes it look for <cspect dir>/<in-image path> and fail
        with "Could not find a part of the path". The file therefore has to
        exist on the host first: extract it (and nothing else) into a temp
        folder with hdfmonkey, then start CSpect on that copy. The image stays
        mounted as the SD card, so anything the program loads from the card
        still resolves.

        The temp copy is deliberately NOT deleted on completion: CSpect is
        launched detached and reads the file after we return."""
        if not _right_disk_content() or not image_path:
            return
        name = os.path.basename(image_path)
        tmp = tempfile.mkdtemp(prefix="zxnu-cspect-")

        def _go(ok):
            local = os.path.join(tmp, name)
            if not ok or not os.path.isfile(local):
                add_main_log_window(ui_tr_now(
                    "Start CSpect: {name} could not be read from the image, "
                    "CSpect was not started.").format(name=name))
                shutil.rmtree(tmp, ignore_errors=True)
                return
            host._launch_cspect_fn(local)

        add_main_log_window(ui_tr_now(
            "Extracting {name} from the image, then starting CSpect…").format(
                name=name))
        image_get_paths_to_local(
            [(image_path, False)], tmp, refresh_fn=lambda: None,
            on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

    def _mame_start_from_image(image_path):
        """Boot a file that lives INSIDE the mounted image with MAME.

        Same constraint as the CSpect twin above: MAME loads the file through
        its own file system, so an in-image path is meaningless to it and the
        file has to exist on the host first. Extract it (and nothing else) to
        a temp folder with hdfmonkey, then start MAME on that copy with the
        image still attached as -hard1, so anything the program loads from the
        SD card resolves as usual.

        The extracted copy is deliberately NOT deleted on success: MAME is
        launched detached and opens the file after we return."""
        if not _right_disk_content() or not image_path:
            return
        name = os.path.basename(image_path)
        if host._mame_flatpak_enabled():
            # Flatpak MAME cannot see this process's /tmp, so stage the copy
            # under the user's home instead — see mame_autostart_staging_dir().
            tmp = mame_autostart_staging_dir()
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                os.makedirs(tmp, exist_ok=True)
            except OSError as exc:
                logging.exception("MAME auto-start staging dir unusable")
                add_main_log_window(ui_tr_now(
                    "Start MAME: could not prepare the staging folder {path} "
                    "({error}).").format(path=tmp, error=exc))
                return
        else:
            tmp = tempfile.mkdtemp(prefix="zxnu-mame-")

        def _go(ok):
            local = os.path.join(tmp, name)
            if not ok or not os.path.isfile(local):
                add_main_log_window(ui_tr_now(
                    "Start MAME: {name} could not be read from the image, "
                    "MAME was not started.").format(name=name))
                shutil.rmtree(tmp, ignore_errors=True)
                return
            host._launch_mame_fn(local)

        add_main_log_window(ui_tr_now(
            "Extracting {name} from the image, then starting MAME…").format(
                name=name))
        image_get_paths_to_local(
            [(image_path, False)], tmp, refresh_fn=lambda: None,
            on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

    def _image_remote_zip(items):
        """'Remote Zip' on the image selection: get the items to a temp
        dir, zip them on the PC, upload <first item>.zip back into the
        first item's image folder (name uniquified via hdfmonkey ls)."""
        if not _right_disk_content() or not items:
            return
        img_err = _check_image_writable(host.right_disk_image_path)
        if img_err:
            add_main_log_window(f"ERROR: {img_err}")
            QMessageBox.critical(host, ui_tr_now("Image not writable"), img_err)
            return
        first = items[0][0].rstrip("/").rsplit("/", 1)[-1] or "archive"
        dest_dir = items[0][0].rstrip("/").rsplit("/", 1)[0] or "/"
        taken = set()
        res_ls = execute_hdf_monkey("ls", host.right_disk_image_path,
                                    extra_argv=[dest_dir], silent=True)
        if res_ls.returncode == 0:
            taken = {n.lower() for n, _d, _s in image_parse_ls(res_ls.stdout)}
        zip_name = zip_unique_name(first, taken)
        tmp = tempfile.mkdtemp(prefix="zxnu_imgzip_")
        dl = os.path.join(tmp, "dl")
        os.makedirs(dl, exist_ok=True)

        def _go(success):
            if not success:
                shutil.rmtree(tmp, ignore_errors=True)
                add_main_log_window(ui_tr_now(
                    "Remote zip: download from the image failed or was "
                    "cancelled — no zip was created."))
                return
            src_paths = [os.path.join(dl, e) for e in sorted(os.listdir(dl))]
            zip_local = os.path.join(tmp, zip_name)
            res = zip_create_with_dialog(host, src_paths, zip_local,
                                         log=add_main_log_window)
            if not res["ok"]:
                shutil.rmtree(tmp, ignore_errors=True)
                if res["cancelled"]:
                    add_main_log_window(ui_tr_now(
                        "Remote zip cancelled — no zip was created."))
                else:
                    add_main_log_window(ui_tr_now(
                        "ERROR: could not build {name}: {error}").format(
                            name=zip_name, error=res["error"]))
                return
            size = os.path.getsize(zip_local)

            def _done(up_ok):
                shutil.rmtree(tmp, ignore_errors=True)
                if up_ok:
                    add_main_log_window(ui_tr_now(
                        "Created {name} in {folder} on the image "
                        "({count} file(s), {bytes} bytes).").format(
                            name=zip_name, folder=dest_dir,
                            count=res["files"], bytes=f"{size:,}"))
                else:
                    add_main_log_window(ui_tr_now(
                        "Remote zip: upload into the image failed or was "
                        "cancelled."))
            image_upload_external_paths([zip_local], dest_dir,
                                        on_complete=_done)

        add_main_log_window(ui_tr_now(
            "Remote zip: fetching {count} item(s) from the image …").format(
                count=len(items)))
        image_get_paths_to_local(
            items, dl, refresh_fn=lambda: None,
            on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

    def _image_send_via_nextsync(image_path, is_dir):
        """'Send via NextSync <name>' on the IMAGE explorer: the Next
        cannot read from the mounted HDF, so the entry is first downloaded
        from the image into a temp folder (hdfmonkey get, progress
        dialog), then handed to the local pane's send handler — which
        pushes it through the live Remote Explorer '.sync5 -listen'
        session and removes the temp copy once the batch ends (the queued
        puts read the files until then, hence the cleanup callback rather
        than deleting here)."""
        if not _right_disk_content():
            return
        name = image_path.rstrip("/").rsplit("/", 1)[-1] or "item"
        tmp = tempfile.mkdtemp(prefix="zxnu_imgsend_")

        def _cleanup():
            shutil.rmtree(tmp, ignore_errors=True)

        def _go(ok):
            local = os.path.join(tmp, name)
            if not ok or not os.path.exists(local):
                _cleanup()
                add_main_log_window(ui_tr_now(
                    "Send via NextSync: {name} could not be read from the "
                    "image, nothing was sent.").format(name=name))
                return
            send = getattr(host, "_local_send_via_nextsync", None)
            if send is None:
                _cleanup()
                return
            send(local, name, is_dir, cleanup=_cleanup)

        add_main_log_window(ui_tr_now(
            "Extracting {name} from the image, then sending it via "
            "NextSync…").format(name=name))
        image_get_paths_to_local(
            [(image_path, is_dir)], tmp, refresh_fn=lambda: None,
            on_complete=lambda okd: QTimer.singleShot(0, lambda: _go(okd)))

    def image_tree_context_menu(pos):
        # Right-click menu on the image explorer tree, mirroring the
        # "New Folder" and "Delete Files or Folder" buttons below it.
        if not _right_disk_content():
            return

        index = host.image_treeview.indexAt(pos)
        menu = QMenu(host.image_treeview)

        if index.isValid():
            # Select the right-clicked row so the selection-driven New
            # Folder / Delete handlers act on it — but only when it isn't
            # already part of an existing multi-selection, so right-clicking
            # one of several selected rows keeps them all selected for delete.
            if not host.image_treeview.selectionModel().isSelected(index):
                host.image_treeview.setCurrentIndex(index)
            name_item = host.image_model.itemFromIndex(index.siblingAtColumn(0))
            is_dir = bool(name_item.data(IMG_ISDIR_ROLE)) if name_item is not None else False

            selected_count = len(host.image_selected_paths)

            # "Start <emulator> with <file>" — top of the menu, and only when
            # that emulator is actually installed and the row is a file it can
            # boot. Neither emulator can read the file from the image itself,
            # so both helpers extract it to a temp folder on the host first
            # and start the emulator on that copy.
            item_path = (name_item.data(IMG_PATH_ROLE) or "") \
                if name_item is not None else ""
            _emu_entry_added = False
            if (not is_dir and emulator_offers_autostart(item_path)
                    and getattr(host, "_cspect_executable_path", None)
                    and getattr(host, "_launch_cspect_fn", None)):
                menu.addAction(
                    ui_tr_now("Start CSpect with file {name}").format(
                        name=os.path.basename(item_path)),
                    lambda p=item_path: QTimer.singleShot(
                        0, lambda: _cspect_start_from_image(p)))
                _emu_entry_added = True
            if (not is_dir and emulator_offers_autostart(item_path)
                    and host._mame_usable()
                    and getattr(host, "_launch_mame_fn", None)):
                menu.addAction(
                    ui_tr_now("Start MAME with file {name}").format(
                        name=os.path.basename(item_path)),
                    lambda p=item_path: QTimer.singleShot(
                        0, lambda: _mame_start_from_image(p)))
                _emu_entry_added = True
            if _emu_entry_added:
                menu.addSeparator()

            # --- NextSync Remote Explorer (the local pane's section,
            # mirrored) --- "Send via NextSync <name>" needs a connected
            # Next; Start/Stop go by RUNNING state (a listener waiting for
            # its Next cannot be started twice — only stopped). The item is
            # staged from the image to a temp folder before the send. The
            # handlers live in build_local_explorer_ops, resolved via
            # getattr at click time.
            _re_running = getattr(host, "_re_running", False)
            if (item_path and _re_running
                    and getattr(getattr(host, "_re_widget", None),
                                "_connected", False)):
                menu.addAction(
                    ui_tr_now("Send via NextSync {name}").format(
                        name=os.path.basename(item_path.rstrip("/"))
                        or item_path),
                    lambda p=item_path, d=is_dir: QTimer.singleShot(
                        0, lambda: _image_send_via_nextsync(p, d)))
            if _re_running:
                menu.addAction(
                    ui_tr_now("Stop NextSync Remote Explorer"),
                    lambda: QTimer.singleShot(0, lambda: getattr(
                        host, "_local_stop_re_server", lambda: None)()))
            else:
                menu.addAction(
                    ui_tr_now("Start NextSync Remote Explorer"),
                    lambda: QTimer.singleShot(0, lambda: getattr(
                        host, "_local_start_re_server", lambda: None)()))
            menu.addSeparator()

            new_folder_label = "New Folder Here…" if is_dir else "New Folder…"
            menu.addAction(new_folder_label, image_newfolder_dialog)
            menu.addSeparator()
            copy_label = f"Copy {selected_count} items" if selected_count > 1 else "Copy"
            menu.addAction(copy_label, lambda: _image_explorer_copy_selection())
            cut_label = f"Cut {selected_count} items" if selected_count > 1 else "Cut"
            menu.addAction(cut_label, lambda: _image_explorer_copy_selection(mode="cut"))
            action_paste = menu.addAction(ui_tr_now("Paste"))
            action_paste.setEnabled(_explorer_clipboard_has_items())
            action_paste.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_image(image_dest_dir())))
            menu.addSeparator()
            # Rename acts on a single entry; only offer it for a lone selection.
            if selected_count <= 1:
                menu.addAction(ui_tr_now("Rename…"),
                               lambda: QTimer.singleShot(0, image_rename_dialog))
            delete_label = f"Delete {selected_count} items" if selected_count > 1 else "Delete"
            menu.addAction(delete_label, delete_files_button_show_confirmation_buttons)
            # Remote Zip/Unzip (PC-staged via hdfmonkey): "Remote Unzip
            # file" only on a lone .zip file, "Remote Zip" on any selection.
            item_path = (name_item.data(IMG_PATH_ROLE) or "") \
                if name_item is not None else ""
            menu.addSeparator()
            if (selected_count <= 1 and not is_dir
                    and item_path.lower().endswith(".zip")):
                menu.addAction(
                    ui_tr_now("Remote Unzip file"),
                    lambda p=item_path: QTimer.singleShot(
                        0, lambda: _image_remote_unzip(p)))
            sel_items = (list(host.image_selected_paths)
                         or ([(item_path, is_dir)] if item_path else []))
            if sel_items:
                zip_label = (f"Remote Zip {selected_count} items"
                             if selected_count > 1 else "Remote Zip")
                menu.addAction(
                    zip_label,
                    lambda it=sel_items: QTimer.singleShot(
                        0, lambda: _image_remote_zip(it)))
        else:
            # Empty area: clear the selection so a new folder lands at the root.
            host.image_treeview.clearSelection()
            menu.addAction(ui_tr_now("New Folder…"), image_newfolder_dialog)
            action_paste = menu.addAction(ui_tr_now("Paste"))
            action_paste.setEnabled(_explorer_clipboard_has_items())
            action_paste.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: _explorer_paste_into_image(image_dest_dir())))

        menu.exec(host.image_treeview.viewport().mapToGlobal(pos))

    # Consumed by bare name elsewhere in __init__ (re-bound at the call site).
    host._explorer_clipboard_has_items = _explorer_clipboard_has_items
    host._explorer_clipboard_set = _explorer_clipboard_set
    host._explorer_paste_into_image = _explorer_paste_into_image
    host._explorer_paste_into_local = _explorer_paste_into_local
    host._image_explorer_copy_selection = _image_explorer_copy_selection
    host._local_explorer_copy_selection = _local_explorer_copy_selection
    host._local_explorer_paste_target_dir = _local_explorer_paste_target_dir
    host._nextsync_explorer_copy_selection = _nextsync_explorer_copy_selection
    host._nextsync_explorer_paste_target_dir = _nextsync_explorer_paste_target_dir
    host.generate_disk_file_path = generate_disk_file_path
    host.image_dest_dir = image_dest_dir
    host.image_get_paths_to_local = image_get_paths_to_local
    host.image_navigate_to_path = image_navigate_to_path
    host.image_tree_context_menu = image_tree_context_menu
    host.image_upload_external_paths = image_upload_external_paths
    host.transfert_content_from_disk_to_image = transfert_content_from_disk_to_image
    host.transfert_content_from_image_to_disk = transfert_content_from_image_to_disk
    host.update_disk_manager_widget_table = update_disk_manager_widget_table
    host.image_clear_model = image_clear_model
    host.image_load_root = image_load_root
    host.image_reload_dir = image_reload_dir
    host._check_access_denied_is_full_disk = _check_access_denied_is_full_disk
