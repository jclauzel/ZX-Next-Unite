"""zxnu_config_io.py — hdfg.cfg restore/save pipeline.

Strangler extraction from MainWindow.__init__ (builder-function seam):
build_config_io(host, ...) defines

* load_configuration_file — the whole config restore pipeline: reads
  hdfg.cfg into configuration_dictionary and pushes every setting back
  into the constructed widget tree (theme/colors/gallery/emulator combos,
  NextSync + HTTP-bridge state, favorites, itch.io auto-connect, the
  startup -start-remote-explorer-listener hook, ...). Runs at startup
  after the widgets exist, so the __init__-locals it calls are injected
  as forwarding lambdas and ``wid_inner`` / the module-global
  ``_ZXNU_START_RE_LISTENER`` flag are read via getter hooks at call time.
* save_configuration_file — the guarded writer (no-op while
  host._initialising; UTF-8, one key=value line per CONFIG_FILE_SETTINGS
  entry — see the memory ``adding-new-setting-key``).

Both are written to ``host`` and re-bound to bare __init__ locals at the
call site (dozens of closures and builder calls capture them). See
CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import json
import logging
import os

from PySide6.QtCore import (Qt, QTimer)
from PySide6.QtGui import (QPixmap)

from zxnu_config import *
from zxnu_i18n import ui_tr_now
from zxnu_gallery import *
from zxnu_http_bridge import flask_available


def build_config_io(
    host,
    *,
    configuration_dictionary,
    get_int_value,
    _zxnu_set_crash_log_enabled,
    _zxnu_start_re_listener,
    _wid_inner,
    get_pyhdfmgooey_currenttab_config,
    add_main_log_window,
    add_nextsync_log_window,
    local_sync_path_box,
    _nextsync_update_set_syncroot_button,
):
    """Define load/save_configuration_file and expose them on the host."""
    def load_configuration_file():

        config_loaded_with_success = False

        try:

            # Load configuration dictionary
            pass

            # The cfg is written as UTF-8 (see save_configuration_file);
            # decode it explicitly instead of trusting the Windows locale
            # code page. Files written by older versions used that locale
            # encoding, so fall back to cp1252 when strict UTF-8 fails —
            # otherwise one accented character would abort the whole load.
            with open(ZX_NEXT_UNITE_CONFIG_FILE_NAME, "rb") as config_file:
                _cfg_bytes = config_file.read()
            try:
                _cfg_text = _cfg_bytes.decode("utf-8")
            except UnicodeDecodeError:
                _cfg_text = _cfg_bytes.decode("cp1252", errors="replace")
            for line in _cfg_text.splitlines():
                if not line.strip():
                    continue
                config_setting_name, config_setting_value = line.strip().split('=', 1)
                configuration_dictionary[config_setting_name] = config_setting_value


            #  Now set the settings back to the application SETTING_SCREENSIZE and others

            # Restore image history into the combo (most-recent-first list
            # stored as '|'-delimited). Entries saved by older versions may
            # carry surrounding quotes / forward slashes, so tidy each one
            # (dropping any that become empty or duplicate after cleanup).
            history_raw = configuration_dictionary.get(SETTING_IMAGE_HISTORY, "")
            if history_raw:
                history_entries = []
                for p in history_raw.split("|"):
                    clean = normalize_sd_image_path(p)
                    if clean and clean not in history_entries:
                        history_entries.append(clean)
                host.imageinput.blockSignals(True)
                host.imageinput.clear()
                for entry in history_entries[:MAX_IMAGE_HISTORY]:
                    host.imageinput.addItem(entry)
                host.imageinput.blockSignals(False)

            # Set the active image path (most recently used), tidied the same way.
            current_hddfile = normalize_sd_image_path(configuration_dictionary[SETTING_HDDFILE])
            host.imageinput.setCurrentText(current_hddfile)
            host.cspect_sound.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_SOUND]))
            host.cspect_screensize.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_SCREENSIZE]))
            host.cspect_vsync.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_VSYNC]))
            host.cspect_joystick.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_JOYSTICK]))
            host.cspect_mouse.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MOUSE]))
            host.cspect_frequency.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_HERTZ]))
            host.cspect_esc.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_ESC]))
            # MAME option combos (aspect / sound / mouse / joystick). Stored
            # as combo indices; an absent value ("") maps to index 0 (the
            # default, i.e. "Sound On" for audio).
            if hasattr(host, "mame_aspect"):
                host.mame_aspect.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_ASPECT]))
                host.mame_sound.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_SOUND]))
                host.mame_mouse.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_MOUSE]))
                host.mame_joystick.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_JOYSTICK]))
                host.mame_esc.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_MAME_ESC]))

            # Flatpak launch toggle + rom path (Linux-only widgets). The
            # Settings tab was built from the seeded defaults *before* this
            # load ran, so re-apply the saved values here — otherwise the
            # checkbox would always come up unchecked after a restart even
            # though the cfg has it on. An empty saved rom path falls back to
            # the per-user default. Finally re-affirm the SD Card MAME group
            # so a saved "on" reveals the Launch button with no restart.
            if not (configuration_dictionary.get(SETTING_MAME_FLATPAK_ROMPATH, "") or "").strip():
                configuration_dictionary[SETTING_MAME_FLATPAK_ROMPATH] = default_mame_flatpak_rompath()
            if hasattr(host, "settings_mame_flatpak_checkbox"):
                _fp_on = str(configuration_dictionary.get(
                    SETTING_MAME_FLATPAK, "")).strip().lower() in ("true", "1", "yes", "on")
                host.settings_mame_flatpak_checkbox.blockSignals(True)
                host.settings_mame_flatpak_checkbox.setChecked(_fp_on)
                host.settings_mame_flatpak_checkbox.blockSignals(False)
                host.settings_mame_flatpak_rompath_edit.setText(
                    configuration_dictionary[SETTING_MAME_FLATPAK_ROMPATH])
                host.settings_mame_flatpak_rompath_row.setVisible(_fp_on)
                if hasattr(host, "_refresh_mame_launch_ui"):
                    host._refresh_mame_launch_ui()

            if configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING]== "":
                # First run (no previously saved tab): default to the
                # AllInOne ("Unite!") tab so the user lands on the
                # aggregated view showing the latest releases.
                _aio_default_idx = 0
                for _ti in range(_wid_inner().tab.count()):
                    if _wid_inner().tab.tabText(_ti).startswith(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
                        _aio_default_idx = _ti
                        break
                configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING] = _aio_default_idx

            _wid_inner().tab.setCurrentIndex(get_int_value(configuration_dictionary[SETTING_DEFAULT_TAB_WHEN_OPENING]))

            if configuration_dictionary[SETTING_EXPLORERPATH] != "":
                if not os.path.isdir(configuration_dictionary[SETTING_EXPLORERPATH]):
                    configuration_dictionary[SETTING_EXPLORERPATH] = os.path.dirname(configuration_dictionary[SETTING_EXPLORERPATH].rstrip("/\\")) + "/"


                host.treeview.setRootIndex(host.proxy_model.mapFromSource(host.model.index(configuration_dictionary[SETTING_EXPLORERPATH])))
                host.left_file_explorer_selection_full_filename_path = configuration_dictionary[SETTING_EXPLORERPATH]
                local_sync_path_box()

            if configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] != "":
                if not os.path.isdir(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH]):
                    configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH] = os.path.dirname(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH].rstrip("/\\")) + "/"


                host.nextsync_treeview.setRootIndex(host.nextsync_model.mapFromSource(host.nextsync_filesystem_model.index(configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH])))
                host.left_file_nextsync_explorer_selection_full_filename_path = configuration_dictionary[SETTING_NEXTSYNC_EXPLORERPATH]
                host.nextsync_file_explorer_path.setText(host.left_file_nextsync_explorer_selection_full_filename_path)

            # Restored (or empty) sync root: sync the "Set current folder as
            # new sync root folder" button visibility with it.
            _nextsync_update_set_syncroot_button()

            # Select the "Sync mode" radio from the two legacy booleans.
            # "Sync once" wins if a legacy config somehow had both set; with
            # neither set we fall back to the incremental (default) mode.
            _sync_once_pref = configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE] in ("1",) or \
                configuration_dictionary[SETTING_NEXTSYNC_SYNCONCE].lower() == "true"
            _always_pref = configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC] in ("1",) or \
                configuration_dictionary[SETTING_NEXTSYNC_ALWAYSSYNC].lower() == "true"
            if _sync_once_pref:
                host.nextsync_synconce_checkbox.setChecked(True)
            elif _always_pref:
                host.nextsync_alwayssync_checkbox.setChecked(True)
            else:
                host.nextsync_syncincremental_radio.setChecked(True)

            if configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] != "":
                if configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER] == "1" or configuration_dictionary[SETTING_NEXTSYNC_SLOWTRANSFER].lower() == "true":
                    host.nextsync_slowtransfer_checkbox.setChecked(True)
                else:
                    host.nextsync_slowtransfer_checkbox.setChecked(False)

            if SETTING_WARN_IMAGE_NEARLY_FULL in configuration_dictionary and configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL] != "":
                checked = configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL] != "0" and configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL].lower() != "false"
                host.settings_warn_image_nearly_full_checkbox.setChecked(checked)

            if SETTING_NO_PROMPT_ON_DELETION in configuration_dictionary and configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION] != "":
                checked = configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION] != "0" and configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION].lower() != "false"
                host.settings_no_prompt_on_deletion_checkbox.setChecked(checked)

            # Recycle Bin deletes toggle (default on). Only restored when
            # Send2Trash is available — without it the checkbox stays
            # disabled+unchecked and local deletes remain permanent.
            if send2trash_available():
                _recycle = configuration_dictionary.get(
                    SETTING_DELETE_TO_RECYCLE_BIN, "").strip().lower()
                _recycle_on = _recycle not in ("false", "0", "no")  # default on
                host.settings_delete_to_recycle_bin_checkbox.blockSignals(True)
                host.settings_delete_to_recycle_bin_checkbox.setChecked(_recycle_on)
                host.settings_delete_to_recycle_bin_checkbox.blockSignals(False)

            # NextSync receive-conflict policy (combo): restore the saved value,
            # falling back to the default for empty/unknown entries.
            _send_conflict = configuration_dictionary.get(SETTING_NEXTSYNC_SEND_CONFLICT, "").strip().lower()
            if _send_conflict not in ("prompt", "overwrite", "ignore"):
                _send_conflict = DEFAULT_NEXTSYNC_SEND_CONFLICT
            configuration_dictionary[SETTING_NEXTSYNC_SEND_CONFLICT] = _send_conflict
            _sc_idx = host.settings_nextsync_send_conflict_combo.findData(_send_conflict)
            host.settings_nextsync_send_conflict_combo.blockSignals(True)
            host.settings_nextsync_send_conflict_combo.setCurrentIndex(max(0, _sc_idx))
            host.settings_nextsync_send_conflict_combo.blockSignals(False)

            if SETTING_AVAIL_CHECK in configuration_dictionary and configuration_dictionary[SETTING_AVAIL_CHECK] != "":
                checked = configuration_dictionary[SETTING_AVAIL_CHECK] != "0" and configuration_dictionary[SETTING_AVAIL_CHECK].lower() != "false"
            else:
                checked = True
            host.settings_avail_check_checkbox.setChecked(checked)

            # Multi-search defaults to True; only turn off when explicitly saved as false/0
            if SETTING_MULTI_SEARCH in configuration_dictionary and configuration_dictionary[SETTING_MULTI_SEARCH] != "":
                checked = configuration_dictionary[SETTING_MULTI_SEARCH] != "0" and configuration_dictionary[SETTING_MULTI_SEARCH].lower() != "false"
                host.settings_multi_search_checkbox.setChecked(checked)

            # Search autocomplete defaults to True; only turn off when explicitly saved as false/0
            if SETTING_SEARCH_AUTOCOMPLETE in configuration_dictionary and configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE] != "":
                checked = configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE] != "0" and configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE].lower() != "false"
                host.settings_search_autocomplete_checkbox.setChecked(checked)

            # Crash-log generation defaults to False; only turn on when explicitly saved as true/1.
            if SETTING_CRASH_LOG_ENABLED in configuration_dictionary and configuration_dictionary[SETTING_CRASH_LOG_ENABLED] != "":
                _crash_checked = configuration_dictionary[SETTING_CRASH_LOG_ENABLED] in ("1", "true", "True", "yes", "on")
            else:
                _crash_checked = False
            host.settings_crash_log_enabled_checkbox.blockSignals(True)
            host.settings_crash_log_enabled_checkbox.setChecked(_crash_checked)
            host.settings_crash_log_enabled_checkbox.blockSignals(False)

            # Disable no-emulator toast defaults to False.
            if SETTING_DISABLE_NO_EMULATOR_TOAST in configuration_dictionary and configuration_dictionary[SETTING_DISABLE_NO_EMULATOR_TOAST] != "":
                _no_toast = configuration_dictionary[SETTING_DISABLE_NO_EMULATOR_TOAST].lower() in ("true", "1", "yes", "on")
            else:
                _no_toast = False
            host.settings_disable_no_emulator_toast_checkbox.setChecked(_no_toast)

            # NextSync HTTP bridge defaults to False; when enabled it also
            # auto-starts the web server with the app. The start is
            # deferred to the event loop so the whole window (log pane,
            # toasts, the bridge closures) exists by then.
            if SETTING_NEXTSYNC_HTTP_BRIDGE in configuration_dictionary and configuration_dictionary[SETTING_NEXTSYNC_HTTP_BRIDGE] != "":
                _http_bridge_on = configuration_dictionary[SETTING_NEXTSYNC_HTTP_BRIDGE].lower() in ("true", "1", "yes", "on")
            else:
                _http_bridge_on = False
            host.settings_http_bridge_checkbox.blockSignals(True)
            host.settings_http_bridge_checkbox.setChecked(_http_bridge_on)
            host.settings_http_bridge_checkbox.blockSignals(False)
            if _http_bridge_on and flask_available():
                QTimer.singleShot(0, host._nextsync_http_bridge_start)
            elif _http_bridge_on:
                # Enabled in the config but Flask is gone (uninstalled /
                # different environment): keep the choice, skip the start,
                # say why — never an error at startup.
                add_nextsync_log_window(
                    "NextSync HTTP bridge is enabled in the settings but "
                    "the 'flask' package is not installed - install it "
                    "with: python -m pip install flask")

            # HTTP bridge port (defaults to 80). The deferred bridge start
            # above reads the port straight from the configuration
            # dictionary, so only the widget needs syncing here.
            try:
                _http_port = int(configuration_dictionary.get(
                    SETTING_NEXTSYNC_HTTP_PORT) or 80)
            except (TypeError, ValueError):
                _http_port = 80
            if not (1 <= _http_port <= 65535):
                _http_port = 80
            host.settings_http_port_spinbox.blockSignals(True)
            host.settings_http_port_spinbox.setValue(_http_port)
            host.settings_http_port_spinbox.blockSignals(False)

            # HTTP bridge concurrent-connection limit (defaults to 1 —
            # the recommended value, matching the serial -listen session).
            try:
                _http_conn = int(configuration_dictionary.get(
                    SETTING_NEXTSYNC_HTTP_CONNECTION_LIMIT) or 1)
            except (TypeError, ValueError):
                _http_conn = 1
            if _http_conn < 1:
                _http_conn = 1
            host.settings_http_conn_spinbox.blockSignals(True)
            host.settings_http_conn_spinbox.setValue(_http_conn)
            host.settings_http_conn_spinbox.blockSignals(False)

            # HTTP bridge request tracing (-v equivalent).
            _http_verbose = configuration_dictionary.get(
                SETTING_NEXTSYNC_HTTP_VERBOSE, "").strip().lower() in (
                    "true", "1", "yes", "on")
            host.settings_http_verbose_checkbox.blockSignals(True)
            host.settings_http_verbose_checkbox.setChecked(_http_verbose)
            host.settings_http_verbose_checkbox.blockSignals(False)

            # HTTP bridge bearer-token protection (checkbox + persisted
            # token). The deferred bridge start above reads both straight
            # from the config dict, so only the widgets need syncing.
            _http_token_on = configuration_dictionary.get(
                SETTING_NEXTSYNC_HTTP_TOKEN_ENABLED, "").strip().lower() in (
                    "true", "1", "yes", "on")
            host.settings_http_token_checkbox.blockSignals(True)
            host.settings_http_token_checkbox.setChecked(_http_token_on)
            host.settings_http_token_checkbox.blockSignals(False)
            host.settings_http_token_edit.blockSignals(True)
            host.settings_http_token_edit.setText(
                (configuration_dictionary.get(
                    SETTING_NEXTSYNC_HTTP_TOKEN) or "").strip())
            host.settings_http_token_edit.blockSignals(False)

            # This single call now also refreshes the token widgets' enabled
            # state from the checkbox value just restored above.
            host._http_port_widgets_set_enabled(_http_bridge_on)

            # MAME ROM/system choice (combo) and command-line parameters
            # (editable text). Both only exist as widgets when MAME was
            # detected at startup, so guard with hasattr.
            if hasattr(host, "settings_mame_rom_combo"):
                _rom = configuration_dictionary.get(SETTING_MAME_ROM_CHOICE, "").strip()
                if not _rom:
                    _rom = MAME_ROM_CHOICE[0]
                host.settings_mame_rom_combo.blockSignals(True)
                _idx = host.settings_mame_rom_combo.findText(_rom)
                if _idx < 0:
                    # Persisted ROM not in the predefined list: add it so the
                    # user's saved choice is preserved and selectable.
                    host.settings_mame_rom_combo.addItem(_rom)
                    _idx = host.settings_mame_rom_combo.findText(_rom)
                host.settings_mame_rom_combo.setCurrentIndex(max(0, _idx))
                host.settings_mame_rom_combo.blockSignals(False)
                configuration_dictionary[SETTING_MAME_ROM_CHOICE] = _rom

            if hasattr(host, "settings_mame_params_edit"):
                _params = configuration_dictionary.get(
                    SETTING_MAME_COMMAND_LINE_PARAMETERS, "")
                if not _params:
                    _params = MAME_DEFAULT_COMMAND_LINE
                # Migrate a legacy default (the one that hard-coded
                # "-aspect 2:1", or the one that hard-coded "-confirm_quit")
                # to the new default now that both are combo boxes, so the
                # editable command-line box no longer shows a stale, now
                # combo-controlled option. (Launch-time stripping still handles
                # any other custom occurrences — see launch_mame.)
                elif _params.strip() in MAME_DEFAULT_COMMAND_LINE_LEGACY_ALL:
                    _params = MAME_DEFAULT_COMMAND_LINE
                host.settings_mame_params_edit.blockSignals(True)
                host.settings_mame_params_edit.setText(_params)
                host.settings_mame_params_edit.blockSignals(False)
                configuration_dictionary[SETTING_MAME_COMMAND_LINE_PARAMETERS] = _params

            # MAME "check for a newer version at startup" toggle (default on).
            # Only exists as a widget when MAME was detected at startup.
            if hasattr(host, "settings_mame_update_check_checkbox"):
                _mame_upd = configuration_dictionary.get(
                    SETTING_MAME_UPDATE_CHECK, "").strip().lower()
                _mame_upd_on = _mame_upd not in ("false", "0", "no")  # default on
                host.settings_mame_update_check_checkbox.blockSignals(True)
                host.settings_mame_update_check_checkbox.setChecked(_mame_upd_on)
                host.settings_mame_update_check_checkbox.blockSignals(False)

            # ZX Next Unite "check for updates at startup on Github" toggle
            # (default on). Always present as a widget.
            if hasattr(host, "settings_zxnu_update_check_checkbox"):
                _zxnu_upd = configuration_dictionary.get(
                    SETTING_ZXNU_UPDATE_CHECK, "").strip().lower()
                _zxnu_upd_on = _zxnu_upd not in ("false", "0", "no")  # default on
                host.settings_zxnu_update_check_checkbox.blockSignals(True)
                host.settings_zxnu_update_check_checkbox.setChecked(_zxnu_upd_on)
                host.settings_zxnu_update_check_checkbox.blockSignals(False)

            # CSpect "check for a newer version on itch.io at startup" toggle
            # (default on). Always present as a widget (unlike MAME's, which
            # is gated on detection).
            if hasattr(host, "settings_cspect_update_check_checkbox"):
                _cspect_upd = configuration_dictionary.get(
                    SETTING_CSPECT_UPDATE_CHECK, "").strip().lower()
                _cspect_upd_on = _cspect_upd not in ("false", "0", "no")  # default on
                host.settings_cspect_update_check_checkbox.blockSignals(True)
                host.settings_cspect_update_check_checkbox.setChecked(_cspect_upd_on)
                host.settings_cspect_update_check_checkbox.blockSignals(False)

            # CSpect default launch parameters (editable text). Empty falls
            # back to the built-in default, mirroring the MAME params handling.
            if hasattr(host, "settings_cspect_params_edit"):
                _cspect_params = configuration_dictionary.get(SETTING_CUSTOM, "")
                if not _cspect_params:
                    _cspect_params = CSPECT_DEFAULT_LAUNCH_PARAMETERS
                # Migrate an older cfg that stored only *additional* params here
                # (the base "-basickeys -zxnext" used to be applied separately):
                # prepend the base so those users keep it now that this field
                # holds the full default command line.
                elif "-zxnext" not in _cspect_params:
                    _cspect_params = (CSPECT_DEFAULT_LAUNCH_PARAMETERS
                                      + " " + _cspect_params)
                host.settings_cspect_params_edit.blockSignals(True)
                host.settings_cspect_params_edit.setText(_cspect_params)
                host.settings_cspect_params_edit.blockSignals(False)
                configuration_dictionary[SETTING_CUSTOM] = _cspect_params
            # Ensure runtime state matches the persisted setting (the
            # early-bootstrap read already honoured this, but reapply here
            # so any cfg edits made between launches take immediate effect).
            try:
                _zxnu_set_crash_log_enabled(_crash_checked)
            except Exception:
                pass

            # Gallery animation mode: "hover" (default), "timer" or "none"
            if SETTING_GALLERY_ANIM_MODE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_ANIM_MODE] != "":
                val = configuration_dictionary[SETTING_GALLERY_ANIM_MODE].strip().lower()
                if val in ("hover", "timer", "none"):
                    host._gallery_anim_mode = val
                    cb = getattr(host, "settings_gallery_anim_combo", None)
                    if cb is not None:
                        for _i in range(cb.count()):
                            if cb.itemData(_i) == val:
                                cb.setCurrentIndex(_i)
                                break

            # Search sort rendering preference: "getit_first" (default),
            # "mixed" or "classic"
            if SETTING_SEARCH_SORT_MODE in configuration_dictionary and configuration_dictionary[SETTING_SEARCH_SORT_MODE] != "":
                _ssm = configuration_dictionary[SETTING_SEARCH_SORT_MODE].strip().lower()
                if _ssm in (SEARCH_SORT_GETIT_FIRST, SEARCH_SORT_MIXED, SEARCH_SORT_CLASSIC):
                    host._search_sort_mode = _ssm
                    _sscb = getattr(host, "settings_search_sort_combo", None)
                    if _sscb is not None:
                        for _i in range(_sscb.count()):
                            if _sscb.itemData(_i) == _ssm:
                                _sscb.setCurrentIndex(_i)
                                break

            # Gallery rows per page: int 1..10
            if SETTING_GALLERY_ROWS_PER_PAGE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_ROWS_PER_PAGE] != "":
                try:
                    n = int(configuration_dictionary[SETTING_GALLERY_ROWS_PER_PAGE])
                except (TypeError, ValueError):
                    n = DEFAULT_GALLERY_ROWS_PER_PAGE
                n = max(GALLERY_MIN_ROWS, min(GALLERY_MAX_ROWS, n))
                host._gallery_rows_per_page = n
                sp = getattr(host, "settings_gallery_rows_spin", None)
                if sp is not None:
                    sp.setValue(n)

            # Gallery items per row: 2 | 4 | 8
            if SETTING_GALLERY_COLS in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_COLS] != "":
                try:
                    _gcols = int(configuration_dictionary[SETTING_GALLERY_COLS])
                except (TypeError, ValueError):
                    _gcols = DEFAULT_GALLERY_COLS
                if _gcols in (2, 4, 8):
                    host._gallery_cols = _gcols
                    _gcb = getattr(host, "settings_gallery_cols_combo", None)
                    if _gcb is not None:
                        for _i in range(_gcb.count()):
                            if _gcb.itemData(_i) == _gcols:
                                _gcb.setCurrentIndex(_i)
                                break

            # Gallery image size: "small" | "medium" | "large"
            if SETTING_GALLERY_IMG_SIZE in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_IMG_SIZE] != "":
                _gsz = configuration_dictionary[SETTING_GALLERY_IMG_SIZE].strip().lower()
                if _gsz in ("small", "medium", "large"):
                    host._gallery_img_size = _gsz
                    _gscb = getattr(host, "settings_gallery_img_size_combo", None)
                    if _gscb is not None:
                        for _i in range(_gscb.count()):
                            if _gscb.itemData(_i) == _gsz:
                                _gscb.setCurrentIndex(_i)
                                break

            # Gallery slideshow pause time (seconds): 5 (default)|10|15|30|60
            if SETTING_GALLERY_SLIDESHOW_SECS in configuration_dictionary and configuration_dictionary[SETTING_GALLERY_SLIDESHOW_SECS] != "":
                try:
                    _gss = int(configuration_dictionary[SETTING_GALLERY_SLIDESHOW_SECS])
                except (TypeError, ValueError):
                    _gss = DEFAULT_GALLERY_SLIDESHOW_SECS
                if _gss in GALLERY_SLIDESHOW_SECS_CHOICES:
                    host._gallery_slideshow_secs = _gss
                    set_gallery_slideshow_secs(_gss)
                    # Refresh the persistent detail-slideshow timers so a
                    # loaded value applies without reopening an item.
                    for _tn in ("_zxdb_slideshow_timer", "_zxart_slideshow_timer"):
                        _t = getattr(host, _tn, None)
                        if _t is not None:
                            _t.setInterval(gallery_slideshow_interval_ms())
                    _sscb = getattr(host, "settings_gallery_slideshow_combo", None)
                    if _sscb is not None:
                        for _i in range(_sscb.count()):
                            if _sscb.itemData(_i) == _gss:
                                _sscb.setCurrentIndex(_i)
                                break

            # Retro log (SD Card + NextSync) font size, in points.
            if SETTING_RETRO_LOG_FONT_SIZE in configuration_dictionary and configuration_dictionary[SETTING_RETRO_LOG_FONT_SIZE] != "":
                try:
                    _rlf = int(configuration_dictionary[SETTING_RETRO_LOG_FONT_SIZE])
                except (TypeError, ValueError):
                    _rlf = DEFAULT_RETRO_LOG_FONT_SIZE
                if _rlf in RETRO_LOG_FONT_SIZE_CHOICES:
                    host._retro_log_font_size = _rlf
                    _rlcb = getattr(host, "settings_retro_log_font_combo", None)
                    if _rlcb is not None:
                        _rlcb.blockSignals(True)
                        for _i in range(_rlcb.count()):
                            if _rlcb.itemData(_i) == _rlf:
                                _rlcb.setCurrentIndex(_i)
                                break
                        _rlcb.blockSignals(False)
                    # Apply to any retro log widgets already built.
                    if hasattr(host, "_apply_retro_log_font_size"):
                        host._apply_retro_log_font_size(_rlf)

            # Per-pane view mode: "table" (default) or "gallery"
            for _pane_key, _attr in (
                (SETTING_GETIT_VIEW_MODE, "_getit_view_mode"),
                (SETTING_ZXDB_VIEW_MODE,  "_zxdb_view_mode"),
                (SETTING_ZXART_VIEW_MODE, "_zxart_view_mode"),
                (SETTING_FAVORITES_VIEW_MODE, "_favorites_view_mode"),
                (SETTING_ALLINONE_VIEW_MODE, "_allinone_view_mode"),
                (SETTING_ITCHIO_VIEW_MODE, "_itchio_view_mode"),
            ):
                if _pane_key in configuration_dictionary and configuration_dictionary[_pane_key] != "":
                    val = configuration_dictionary[_pane_key].strip().lower()
                    if val in ("table", "gallery"):
                        setattr(host, _attr, val)

            # Space-Invaders background animation preference (default on).
            # Applied before the pygame-mode restore below so the widget is
            # built with the right setting.
            _allinone_anim_pref = configuration_dictionary.get(
                SETTING_ALLINONE_PYGAME_ANIM, "").strip().lower()
            _allinone_anim_on = _allinone_anim_pref not in ("false", "0", "no")
            host._allinone_pygame_anim = _allinone_anim_on
            _anim_cb = getattr(host, "settings_pygame_anim_checkbox", None)
            if _anim_cb is not None:
                _anim_cb.blockSignals(True)
                _anim_cb.setChecked(_allinone_anim_on)
                _anim_cb.blockSignals(False)

            # Restore the Unite! pygame visualization mode if it was on last
            # session. Routed through the toggle button so the lazy import /
            # graceful-fallback path is reused; guarded so a transient
            # "pygame unavailable" doesn't overwrite the saved preference.
            _allinone_pg_pref = configuration_dictionary.get(
                SETTING_ALLINONE_PYGAME_MODE, "").strip().lower()
            if _allinone_pg_pref in ("true", "1", "yes") and \
                    hasattr(host, "allinone_pygame_button") and \
                    not host.allinone_pygame_button.isChecked():
                host._allinone_pygame_restoring = True
                try:
                    host.allinone_pygame_button.setChecked(True)
                finally:
                    host._allinone_pygame_restoring = False

            # NextSync retro-log starfield animation preference (default on);
            # applied before the mode restore below so the widget is built
            # with the right setting.
            _nextsync_anim_pref = configuration_dictionary.get(
                SETTING_NEXTSYNC_PYGAME_ANIM, "").strip().lower()
            _nextsync_anim_on = _nextsync_anim_pref not in ("false", "0", "no")
            host._nextsync_pygame_anim = _nextsync_anim_on
            _ns_anim_cb = getattr(host, "settings_nextsync_pygame_anim_checkbox", None)
            if _ns_anim_cb is not None:
                _ns_anim_cb.blockSignals(True)
                _ns_anim_cb.setChecked(_nextsync_anim_on)
                _ns_anim_cb.blockSignals(False)

            # Restore the NextSync retro 8-bit log mode the same way (routed
            # through the toggle so the lazy-import / fallback path is reused).
            _nextsync_pg_pref = configuration_dictionary.get(
                SETTING_NEXTSYNC_PYGAME_MODE, "").strip().lower()
            if _nextsync_pg_pref in ("true", "1", "yes") and \
                    hasattr(host, "nextsync_pygame_button") and \
                    not host.nextsync_pygame_button.isChecked():
                host._nextsync_pygame_restoring = True
                try:
                    host.nextsync_pygame_button.setChecked(True)
                finally:
                    host._nextsync_pygame_restoring = False

            # Restore the Remote Explorer view if it was open last session by
            # selecting its tab (index 0), which drives all the show/hide +
            # widget-build side effects (the listen server itself is NOT
            # auto-started).
            _re_open_pref = configuration_dictionary.get(
                SETTING_NEXTSYNC_REMOTE_EXPLORER, "").strip().lower()
            if _re_open_pref in ("true", "1", "yes") and \
                    hasattr(host, "nextsync_mode_tabs") and \
                    host.nextsync_mode_tabs.currentIndex() != 0:
                host._re_open_restoring = True
                try:
                    host.nextsync_mode_tabs.setCurrentIndex(0)
                finally:
                    host._re_open_restoring = False

            # -start-remote-explorer-listener: the command-line switch asks
            # for the '.sync5 -listen' server to be running from startup.
            # Force the Remote Explorer view open (without persisting it —
            # this run only, hence the _re_open_restoring guard) and start
            # the server once the event loop is up, so the toasts, log
            # pane and server closures it talks to all exist by then.
            if _zxnu_start_re_listener() and hasattr(host, "nextsync_mode_tabs"):
                if host.nextsync_mode_tabs.currentIndex() != 0:
                    host._re_open_restoring = True
                    try:
                        host.nextsync_mode_tabs.setCurrentIndex(0)
                    finally:
                        host._re_open_restoring = False

                def _autostart_re_listener():
                    if not getattr(host, "_re_running", False):
                        host._nextsync_re_toggle_server()
                QTimer.singleShot(0, _autostart_re_listener)

            # Restore the saved splitter positions (SD Card explorers ⇄
            # log, GetIt results ⇄ MOTD). The window is not shown yet, so
            # QSplitter re-applies the sizes on first layout; the stretch
            # factors (top pane 1, bottom pane 0) absorb any difference
            # between the saved and actual window height, keeping the
            # bottom pane at its saved height.
            for _split_key, _split_attr in (
                (SETTING_SDCARD_SPLITTER, "sdcard_splitter"),
                (SETTING_GETIT_SPLITTER,  "getit_splitter"),
            ):
                _split_pref = str(configuration_dictionary.get(
                    _split_key, "")).strip()
                _split_widget = getattr(host, _split_attr, None)
                if _split_pref and _split_widget is not None:
                    try:
                        _top, _bottom = (int(_v) for _v in _split_pref.split(",")[:2])
                        if _top > 0 and _bottom > 0:
                            _split_widget.setSizes([_top, _bottom])
                    except (TypeError, ValueError):
                        pass

            # Restore the SD Card retro 8-bit log mode the same way.
            _sdcard_pg_pref = configuration_dictionary.get(
                SETTING_SDCARD_PYGAME_LOG, "").strip().lower()
            if _sdcard_pg_pref in ("true", "1", "yes") and \
                    hasattr(host, "main_pygame_button") and \
                    not host.main_pygame_button.isChecked():
                host._main_pygame_restoring = True
                try:
                    host.main_pygame_button.setChecked(True)
                finally:
                    host._main_pygame_restoring = False

            # Restore the Help ("?") retro 8-bit console mode the same way.
            _help_pg_pref = configuration_dictionary.get(
                SETTING_HELP_PYGAME_LOG, "").strip().lower()
            if _help_pg_pref in ("true", "1", "yes") and \
                    hasattr(host, "help_pygame_button") and \
                    not host.help_pygame_button.isChecked():
                host._help_pygame_restoring = True
                try:
                    host.help_pygame_button.setChecked(True)
                finally:
                    host._help_pygame_restoring = False

            # Restore each pane's Classic/Retro item-viewer choice. Routed
            # through the toggle button so the pygame-availability check and
            # label update are reused; persisting during restore is a no-op
            # (save_configuration_file is guarded while _initialising).
            host._retro_restoring = True
            try:
                for _retro_key, _retro_btn_attr in (
                    (SETTING_GETIT_ITEM_RETRO,     "getit_retro_button"),
                    (SETTING_ZXDB_ITEM_RETRO,      "zxdb_retro_button"),
                    (SETTING_ZXART_ITEM_RETRO,     "zxart_retro_button"),
                    (SETTING_ITCHIO_ITEM_RETRO,    "itchio_retro_button"),
                    (SETTING_FAVORITES_ITEM_RETRO, "favorites_retro_button"),
                ):
                    _retro_pref = configuration_dictionary.get(
                        _retro_key, "").strip().lower()
                    _retro_btn = getattr(host, _retro_btn_attr, None)
                    if (_retro_pref in ("true", "1", "yes") and _retro_btn is not None
                            and not _retro_btn.isChecked()):
                        _retro_btn.setChecked(True)
            finally:
                host._retro_restoring = False

            # itch.io tab: prefill the saved API key and apply the saved
            # show/hide preference (the tab is built visible by default).
            try:
                _key_field = getattr(host, "itchio_key_input", None)
                if _key_field is not None:
                    _key_field.setText(
                        configuration_dictionary.get(SETTING_ITCHIO_API_KEY, "") or "")
                _itch_show_pref = configuration_dictionary.get(
                    SETTING_SHOW_ITCHIO_TAB, "").strip().lower()
                _itch_show = _itch_show_pref not in ("false", "0", "no")  # default on
                _itch_cb = getattr(host, "settings_show_itchio_tab_checkbox", None)
                if _itch_cb is not None and _itch_cb.isEnabled():
                    _itch_cb.blockSignals(True)
                    _itch_cb.setChecked(_itch_show)
                    _itch_cb.blockSignals(False)
                _itch_fn = getattr(host, "_itchio_tab_set_visible", None)
                if _itch_fn is not None:
                    _itch_fn(_itch_show)
                # Auto-connect once at startup when the tab is enabled and a
                # key was saved, so collections are ready without the user
                # having to click Connect.
                _itch_load = getattr(host, "_itchio_load_collections", None)
                _itch_key = (configuration_dictionary.get(
                    SETTING_ITCHIO_API_KEY, "") or "").strip()
                if (_itch_show and _itch_key and _itch_load is not None
                        and not getattr(host, "_itchio_autoconnected", False)):
                    host._itchio_autoconnected = True
                    QTimer.singleShot(0, _itch_load)
            except Exception:
                pass

            # Alien Floyd's (pygame-ce) optional background + dedicated tab
            # (both default off). Disable the controls when pygame-ce is not
            # installed, but leave the saved preferences untouched.
            try:
                # Seed the persisted arcade high-score table and wire the
                # saver.  The table (top 5, NAME:SCORE pairs) is the single
                # source of truth; the player adds an entry by spelling
                # their name (shooting letters) when a run makes the list.
                try:
                    import zxnu_pygame as _zpg_hs

                    def _save_alien_table(serialized):
                        configuration_dictionary[SETTING_ALIEN_FLOYD_HISCORES] = str(serialized)
                        try:
                            save_configuration_file()
                        except Exception:
                            pass
                    _zpg_hs.set_alien_table_save_cb(_save_alien_table)
                    _zpg_hs.init_alien_table(
                        configuration_dictionary.get(
                            SETTING_ALIEN_FLOYD_HISCORES, ""))
                except Exception:
                    pass
                _af_bg_on = configuration_dictionary.get(
                    SETTING_ALIEN_FLOYD_BG, "").strip().lower() in ("true", "1", "yes")
                _af_tab_on = configuration_dictionary.get(
                    SETTING_ALIEN_FLOYD_TAB, "").strip().lower() in ("true", "1", "yes")
                _af_ok = False
                try:
                    from zxnu_pygame import pygame_available as _pg_avail
                    _af_ok = bool(_pg_avail()[0])
                except Exception:
                    _af_ok = False
                _af_bg_cb = getattr(host, "settings_alien_floyd_bg_checkbox", None)
                _af_tab_cb = getattr(host, "settings_alien_floyd_tab_checkbox", None)
                if not _af_ok:
                    for _cb in (_af_bg_cb, _af_tab_cb):
                        if _cb is not None:
                            _cb.setEnabled(False)
                            _cb.setToolTip(
                                "Requires the optional 'pygame-ce' package.\n"
                                + zxnu_optional_install_hint("pygame-ce"))
                else:
                    if _af_bg_cb is not None:
                        _af_bg_cb.blockSignals(True)
                        _af_bg_cb.setChecked(_af_bg_on)
                        _af_bg_cb.blockSignals(False)
                    if _af_tab_cb is not None:
                        _af_tab_cb.blockSignals(True)
                        _af_tab_cb.setChecked(_af_tab_on)
                        _af_tab_cb.blockSignals(False)
                    if _af_bg_on and hasattr(host, "_apply_alien_floyd_bg"):
                        host._apply_alien_floyd_bg(True)
                    if _af_tab_on and hasattr(host, "_alien_floyd_tab_set_visible"):
                        host._alien_floyd_tab_set_visible(True)
            except Exception:
                pass

            # zxART API language (eng/pol/spa)
            _zxart_lang_cfg = configuration_dictionary.get(SETTING_ZXART_LANGUAGE, "").strip().lower()
            if _zxart_lang_cfg in ("eng", "pol", "spa"):
                _zxart_set_language(_zxart_lang_cfg)
            if hasattr(host, "zxart_language_combo"):
                cb = host.zxart_language_combo
                code = _zxart_lang()
                for _i in range(cb.count()):
                    if cb.itemData(_i) == code:
                        cb.blockSignals(True)
                        cb.setCurrentIndex(_i)
                        cb.blockSignals(False)
                        break

            saved_mode = configuration_dictionary.get(SETTING_ZXDB_LAST_MODE, "").strip()
            if saved_mode:
                for _i in range(host.zxdb_mode_combo.count()):
                    if host.zxdb_mode_combo.itemData(_i) == saved_mode:
                        host.zxdb_mode_combo.setCurrentIndex(_i)
                        break

            def _load_color_setting(setting_key, default_hex, color_attr, btn_attr):
                hex_val = configuration_dictionary.get(setting_key, "").strip()
                color = hex_to_qcolor(hex_val) if hex_val else hex_to_qcolor(default_hex)
                setattr(host, color_attr, color)
                btn = getattr(host, btn_attr)
                btn.setStyleSheet(f"background-color: {qcolor_to_hex(color)}; border: 1px solid #888;")

            _load_color_setting(SETTING_COLOR_UP_DIRECTORY, DEFAULT_COLOR_UP_DIRECTORY, "img_color_up_directory", "settings_btn_color_up_directory")
            _load_color_setting(SETTING_COLOR_DIR_NAME,     DEFAULT_COLOR_DIR_NAME,     "img_color_dir_name",     "settings_btn_color_dir_name")
            _load_color_setting(SETTING_COLOR_DIR_TYPE,     DEFAULT_COLOR_DIR_TYPE,     "img_color_dir_type",     "settings_btn_color_dir_type")
            _load_color_setting(SETTING_COLOR_FILE_NAME,    DEFAULT_COLOR_FILE_NAME,    "img_color_file_name",    "settings_btn_color_file_name")
            _load_color_setting(SETTING_COLOR_FILE_EXT,     DEFAULT_COLOR_FILE_EXT,     "img_color_file_ext",     "settings_btn_color_file_ext")
            _load_color_setting(SETTING_COLOR_FILE_SIZE,    DEFAULT_COLOR_FILE_SIZE,    "img_color_file_size",    "settings_btn_color_file_size")
            _load_color_setting(SETTING_COLOR_GENERAL_TEXT, DEFAULT_COLOR_GENERAL_TEXT, "img_color_general_text", "settings_btn_color_general_text")
            _load_color_setting(SETTING_COLOR_RETRO_LOG,    DEFAULT_COLOR_RETRO_LOG,    "img_color_retro_log",    "settings_btn_color_retro_log")
            # Push the restored retro-log color to any already-built retro
            # consoles (normally they are built later, lazily, and seed
            # themselves from img_color_retro_log at construction).
            if hasattr(host, "_apply_retro_log_color"):
                host._apply_retro_log_color()

            # Desktop theme mode. Automatic re-detects the OS light/dark
            # theme at every startup and Dark re-applies the dark tweaks
            # (both overriding the per-colour values just loaded); Custom
            # keeps the hand-picked colours above.
            _theme_cfg = configuration_dictionary.get(SETTING_DESKTOP_THEME, "").strip().lower()
            if _theme_cfg in (DESKTOP_THEME_AUTOMATIC, DESKTOP_THEME_WHITE,
                              DESKTOP_THEME_DARK, DESKTOP_THEME_BLACK,
                              DESKTOP_THEME_CUSTOM):
                host._desktop_theme_mode = _theme_cfg
            if hasattr(host, "_select_desktop_theme_in_combo"):
                host._select_desktop_theme_in_combo(host._desktop_theme_mode)
            if hasattr(host, "_apply_desktop_theme_colors"):
                host._apply_desktop_theme_colors(persist=False)

            # Background opacity
            _bg_opacity_raw = configuration_dictionary.get(SETTING_BG_OPACITY, "").strip()
            _bg_opacity_val = BackgroundWidget.DEFAULT_OPACITY
            if _bg_opacity_raw:
                try:
                    _bg_opacity_val = max(0, min(100, int(_bg_opacity_raw)))
                except (TypeError, ValueError):
                    pass
            host.settings_bg_opacity_slider.blockSignals(True)
            host.settings_bg_opacity_spinbox.blockSignals(True)
            host.settings_bg_opacity_slider.setValue(_bg_opacity_val)
            host.settings_bg_opacity_spinbox.setValue(_bg_opacity_val)
            host.settings_bg_opacity_slider.blockSignals(False)
            host.settings_bg_opacity_spinbox.blockSignals(False)
            host._bg_widget.set_bg_opacity(_bg_opacity_val)
            _pane_alpha = max(0, min(255, int(255 - (_bg_opacity_val / 100.0) * 255)))
            host._tab_widget.setStyleSheet(host._build_tab_stylesheet(_pane_alpha))

            # Background image selection
            _bg_image_raw = configuration_dictionary.get(SETTING_BG_IMAGE, "").strip()
            if _bg_image_raw:
                # Resource paths are stored with a :/ prefix; filesystem paths
                # are stored as basenames relative to the script directory.
                if _bg_image_raw.startswith(":/"):
                    _bg_full_load = _bg_image_raw
                    _path_valid = not QPixmap(_bg_full_load).isNull()
                else:
                    _bg_dir_load = ZXNU_DATA_ROOT
                    _bg_full_load = os.path.join(_bg_dir_load, _bg_image_raw)
                    _path_valid = os.path.isfile(_bg_full_load)
                if _path_valid:
                    # Find matching combo entry
                    _cb = getattr(host, "settings_bg_image_combo", None)
                    if _cb is not None:
                        for _ci in range(_cb.count()):
                            if _cb.itemData(_ci) == _bg_full_load:
                                _cb.blockSignals(True)
                                _cb.setCurrentIndex(_ci)
                                _cb.blockSignals(False)
                                break
                    host._bg_widget.set_bg_image(_bg_full_load)
                    _prev = getattr(host, "settings_bg_image_preview", None)
                    if _prev is not None:
                        _px = QPixmap(_bg_full_load)
                        if not _px.isNull():
                            _prev.setPixmap(
                                _px.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            )
            # If empty / not found, BackgroundWidget is already in random-cycling mode

            # Favorites
            try:
                _fav_raw = configuration_dictionary.get(SETTING_FAVORITES, "").strip()
                if _fav_raw:
                    _fav_list = json.loads(_fav_raw)
                    if isinstance(_fav_list, list):
                        host._favorites = []
                        host._favorites_index = set()
                        for _it in _fav_list:
                            if not isinstance(_it, dict):
                                continue
                            _src = str(_it.get("source") or "")
                            _id  = str(_it.get("id") or "")
                            if not _src or not _id:
                                continue
                            host._favorites.append(_it)
                            host._favorites_index.add((_src, _id))
                if hasattr(host, "_fav_update_tab_badge"):
                    host._fav_update_tab_badge()
                if hasattr(host, "_fav_refresh_all"):
                    host._fav_refresh_all()
            except Exception:
                pass

            config_loaded_with_success = True
            add_main_log_window(ui_tr_now("Loaded configuration file."))
            logging.info("Configuration file loaded successfully.")

        except ValueError as e:
            logging.error(f"Error parsing the configuration file. Value error: {e}")
        except IOError as e:
            logging.error(f"Failed to load configuration file. IOError: {e}")
        except FileNotFoundError:
            logging.error(f"Configuration file not found!")
        except Exception as e:
            logging.error(f"Failed to load configuration file. Exception: {e}")

        return config_loaded_with_success


    def save_configuration_file():

        # Skip saves that are triggered by signal emissions during __init__
        # while widgets are being set up (before load_configuration_file runs).
        if host._initialising:
            return

        get_pyhdfmgooey_currenttab_config()

        try:

            config_array = []
            # Explicit UTF-8: settings values include free-form JSON (the
            # favorites list keeps raw API metadata — Cyrillic titles etc.)
            # which the Windows locale code page cannot encode; without
            # this the write raised UnicodeEncodeError and truncated the
            # cfg, losing the favorites and every setting after them.
            with open(ZX_NEXT_UNITE_CONFIG_FILE_NAME, "w", encoding="utf-8") as config_file:
                for cs in CONFIG_FILE_SETTINGS:
                    config_array.append(cs + "=" + str(configuration_dictionary[cs]) + '\n')

                config_file.writelines(config_array)

            if ZX_NEXT_UNITE_VERBOSE_LOG_MODE:
                logging.info("Configuration file saved successfully.")
                add_main_log_window(ui_tr_now("Saved configuration file."))


        except IOError as e:
            logging.error(f"Failed to save configuration file with IOError: {e}")
            add_main_log_window(ui_tr_now(
                "Failed to save configuration file with IOError: {error}"
            ).format(error=e))
        except Exception as e:
            logging.error(f"An unexpected error occurred while saving the configuration file. Exception: {e}")
            add_main_log_window(f"An unexpected error occurred while saving the configuration file. Exception: {e}")

    # Re-bound to bare __init__ locals at the call site.
    host.load_configuration_file = load_configuration_file
    host.save_configuration_file = save_configuration_file
