"""zxnu_settings_pane.py — the Settings tab builder.

Strangler extraction from MainWindow.__init__ (builder-function seam, see
zxnu_zxdb_pane.py): the whole Settings-tab construction blob — every settings
row widget plus its inline change-handler closures (self-update / theme /
colors / gallery / NextSync HTTP bridge + bearer token / MAME / CSpect /
Alien Floyd's / itch.io / conflict policy / sort mode ...) — now lives here as
build_settings_pane(host, ...). Every ``self.settings_*`` (and friends)
attribute is written to ``host`` so MainWindow keeps its historical attribute
surface (load_configuration_file restores checkbox state through them). The
monolith keeps only the wid_inner.tab.addTab(settings_scroll, ...) line, fed
by the host.settings_scroll attribute written at the end. See CLAUDE.md and
the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import (Qt, QTimer)
from PySide6.QtGui import (QColor, QPixmap)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QCheckBox,
    QComboBox, QLineEdit, QGridLayout, QHBoxLayout, QVBoxLayout, QFrame,
    QScrollArea, QSlider, QSpinBox, QColorDialog)

import zxnu_itchio
from zxnu_http_bridge import flask_available
from zxnu_i18n import DEFAULT_UI_LANGUAGE, UI_LANGUAGES, normalize_ui_language
from zxnu_config import *
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *


# ---------------------------------------------------------------------------
# The Settings tab's visual order — ONE name per grid row, top to bottom.
# This tuple is the only place that decides where a settings row appears:
# every widget is placed with settings_grid_row("<name>"), so adding a
# setting means inserting its name here and placing its widgets — every row
# below moves down by itself. Never hardcode a grid row index again (the
# 9.5.17 checkbox insertion had to renumber 30+ call sites by hand).
# tests/test_ui_offscreen.py asserts widget positions through this same
# mapping, so a call site that bypasses it fails the suite.
# ---------------------------------------------------------------------------
SETTINGS_TAB_ROWS = (
    "zxnu_update_check",
    "ui_language",
    "wizard",
    "desktop_theme",
    "warn_image_nearly_full",
    "no_prompt_on_deletion",
    "delete_to_recycle_bin",
    "re_autostart",
    "nextsync_send_conflict",
    "avail_check",
    "multi_search",
    "search_autocomplete",
    "gallery_anim",
    "gallery_rows",
    "gallery_cols",
    "gallery_img_size",
    "gallery_slideshow",
    "search_sort",
    "colors_section",
    "color_up_directory",
    "color_dir_name",
    "color_dir_type",
    "color_file_name",
    "color_file_ext",
    "color_file_size",
    "color_general_text",
    "color_retro_log",
    "retro_log_font",
    "bg_opacity",
    "bg_image",
    "crash_log",
    "no_emulator_toast",
    "mame_rom",
    "mame_params",
    "mame_update_check",   # also the Flatpak options box — platform alternates
    "cspect_params",
    "cspect_update_check",
    "sdcard_pygame_anim",
    "alien_floyd_bg",
    "alien_floyd_tab",
    "nextsync_pygame_anim",
    "itchio_tab",
    "http_bridge",
    "open_config_file",
)
# A duplicate name would silently stack two rows on one grid row — the
# import must fail instead.
assert len(set(SETTINGS_TAB_ROWS)) == len(SETTINGS_TAB_ROWS), \
    "duplicate name in SETTINGS_TAB_ROWS"


def settings_grid_row(name):
    """The grid row of a named Settings entry. A typo raises ValueError at
    build time — a misnamed row must fail loudly, not land at row 0."""
    return SETTINGS_TAB_ROWS.index(name)


def build_settings_pane(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    open_cspect_configuration_file,
    wid_inner,
    _zxnu_set_crash_log_enabled,
    _apply_autocomplete_setting,
):
    """Build the Settings tab (widgets + inline persistence handlers)."""
    # Create Settings Tab
    zxnextunite_Settings_tab = QWidget(wid_inner.tab)
    zxnextunite_Settings_tab.setAttribute(Qt.WA_TranslucentBackground)
    zxnextunite_Settings_tab.setAutoFillBackground(False)
    grid_tab_Settings = QGridLayout(zxnextunite_Settings_tab)

    # ── ZX Next Unite self-update check (very top of the Settings pane) ──
    # Startup check of the app's own GitHub releases (mirrors the MAME /
    # CSpect update-check toggles below). Default on; persisted so a saved
    # "off" survives restarts. The check itself runs in
    # _check_zxnu_update_async (kicked from the startup sequence).
    def settings_zxnu_update_check_changed():
        on = host.settings_zxnu_update_check_checkbox.isChecked()
        configuration_dictionary[SETTING_ZXNU_UPDATE_CHECK] = (
            "true" if on else "false")
        save_configuration_file()

    host.settings_zxnu_update_check_checkbox = QCheckBox(
        "Check for ZX Next Unite updates at startup on Github")
    host.settings_zxnu_update_check_checkbox.setChecked(True)  # default on
    host.settings_zxnu_update_check_checkbox.setToolTip(
        "At startup, check the ZX Next Unite GitHub releases page for a\n"
        "newer version and offer to download it (Windows binary users) or\n"
        "advise a 'git pull' (running from source). The check and its\n"
        "outcome are logged in the SD Card Utility tab's log window, like\n"
        "the MAME and CSpect checks. On by default. Saved to the\n"
        "configuration file.")
    host.settings_zxnu_update_check_checkbox.stateChanged.connect(
        lambda _s: settings_zxnu_update_check_changed())
    grid_tab_Settings.addWidget(host.settings_zxnu_update_check_checkbox, settings_grid_row("zxnu_update_check"), 0, 1, 2)

    # ── Desktop theme (top of the Settings pane) ───────────────────────
    # Drives the SD Card explorer font colours. Automatic follows the OS
    # (high-contrast -> all black, dark -> orange/yellow, else -> light
    # blue); White/Dark/Black force a palette; Custom leaves the user's
    # picked colours alone. Picking any colour below switches to Custom.
    def _desktop_theme_variant():
        """Colour variant to apply: 'light', 'dark' or 'black' (or None for
        Custom, meaning 'leave the colours as they are')."""
        mode = getattr(host, "_desktop_theme_mode", DEFAULT_DESKTOP_THEME)
        if mode == DESKTOP_THEME_CUSTOM:
            return None
        if mode == DESKTOP_THEME_WHITE:
            return "light"
        if mode == DESKTOP_THEME_DARK:
            return "dark"
        if mode == DESKTOP_THEME_BLACK:
            return "black"
        # Automatic: accessibility high-contrast wins, then dark, else light.
        try:
            if detect_system_high_contrast():
                return "black"
        except Exception:
            pass
        try:
            if detect_system_dark_theme():
                return "dark"
        except Exception:
            pass
        return "light"
    host._desktop_theme_variant = _desktop_theme_variant

    # setting_key, colour attr, swatch-button attr, light hex, dark hex, black hex
    _theme_palette = (
        (SETTING_COLOR_UP_DIRECTORY, "img_color_up_directory", "settings_btn_color_up_directory", DEFAULT_COLOR_UP_DIRECTORY, DEFAULT_COLOR_UP_DIRECTORY, HIGH_CONTRAST_COLOR),
        (SETTING_COLOR_DIR_NAME,     "img_color_dir_name",     "settings_btn_color_dir_name",     DEFAULT_COLOR_DIR_NAME,     DARK_COLOR_DIR_NAME,        HIGH_CONTRAST_COLOR),
        (SETTING_COLOR_DIR_TYPE,     "img_color_dir_type",     "settings_btn_color_dir_type",     DEFAULT_COLOR_DIR_TYPE,     DARK_COLOR_DIR_TYPE,        HIGH_CONTRAST_COLOR),
        (SETTING_COLOR_FILE_NAME,    "img_color_file_name",    "settings_btn_color_file_name",    DEFAULT_COLOR_FILE_NAME,    DEFAULT_COLOR_FILE_NAME,    HIGH_CONTRAST_COLOR),
        (SETTING_COLOR_FILE_EXT,     "img_color_file_ext",     "settings_btn_color_file_ext",     DEFAULT_COLOR_FILE_EXT,     DEFAULT_COLOR_FILE_EXT,     HIGH_CONTRAST_COLOR),
        (SETTING_COLOR_FILE_SIZE,    "img_color_file_size",    "settings_btn_color_file_size",    DEFAULT_COLOR_FILE_SIZE,    DEFAULT_COLOR_FILE_SIZE,    HIGH_CONTRAST_COLOR),
        # General app-wide UI text. The panes are always dark, so light and
        # dark variants both use a light colour; high contrast uses white.
        (SETTING_COLOR_GENERAL_TEXT, "img_color_general_text", "settings_btn_color_general_text", DEFAULT_COLOR_GENERAL_TEXT, DEFAULT_COLOR_GENERAL_TEXT, HIGH_CONTRAST_TEXT_COLOR),
    )
    _theme_variant_col = {"light": 3, "dark": 4, "black": 5}

    def _apply_desktop_theme_colors(persist=False):
        """Recompute the SD Card explorer font colours from the current
        Desktop Theme mode. White/Dark/Black/Automatic derive the palette;
        Custom keeps the user's colours (snapshotting them to the cfg when
        persist=True)."""
        # Theme-aware view backgrounds: the explorers/tables/lists render
        # dark under every variant except explicit White (whose item
        # palette expects the stock light viewports). Appended to the
        # app-wide chrome so a theme switch re-applies both.
        _appinst = QApplication.instance()
        if _appinst is not None:
            _extra = ("" if _desktop_theme_variant() == "light"
                      else NEXT_DARK_VIEWS_QSS)
            _appinst.setStyleSheet(NEXT_CHROME_QSS + _extra)
        mode = getattr(host, "_desktop_theme_mode", DEFAULT_DESKTOP_THEME)
        if mode == DESKTOP_THEME_CUSTOM:
            if persist:
                for _entry in _theme_palette:
                    _c = getattr(host, _entry[1], None)
                    if _c is not None:
                        configuration_dictionary[_entry[0]] = qcolor_to_hex(_c)
            return
        variant = _desktop_theme_variant() or "light"
        _col = _theme_variant_col[variant]
        for _entry in _theme_palette:
            _k, _ca, _ba = _entry[0], _entry[1], _entry[2]
            _color = hex_to_qcolor(_entry[_col])
            setattr(host, _ca, _color)
            if persist:
                configuration_dictionary[_k] = qcolor_to_hex(_color)
            _btn = getattr(host, _ba, None)
            if _btn is not None:
                _btn.setStyleSheet(f"background-color: {qcolor_to_hex(_color)}; border: 1px solid #888;")
        if hasattr(host, "_image_recolor_all"):
            try:
                host._image_recolor_all()
            except Exception:
                pass
        # Mirror the theme's item colours into the Remote Explorer panes.
        if hasattr(host, "_re_apply_item_colors"):
            try:
                host._re_apply_item_colors()
            except Exception:
                pass
        # Re-apply the general UI text colour to the (always-dark) tab panes.
        if hasattr(host, "_refresh_tab_stylesheet"):
            try:
                host._refresh_tab_stylesheet()
            except Exception:
                pass
    host._apply_desktop_theme_colors = _apply_desktop_theme_colors

    def _select_desktop_theme_in_combo(mode):
        cb = getattr(host, "settings_desktop_theme_combo", None)
        if cb is None:
            return
        for _i in range(cb.count()):
            if cb.itemData(_i) == mode:
                cb.blockSignals(True)
                cb.setCurrentIndex(_i)
                cb.blockSignals(False)
                break
    host._select_desktop_theme_in_combo = _select_desktop_theme_in_combo

    def _settings_desktop_theme_changed():
        val = host.settings_desktop_theme_combo.currentData() or DEFAULT_DESKTOP_THEME
        host._desktop_theme_mode = val
        configuration_dictionary[SETTING_DESKTOP_THEME] = val
        _apply_desktop_theme_colors(persist=True)
        save_configuration_file()

    desktop_theme_lbl = QLabel("Desktop Theme:")
    desktop_theme_lbl.setToolTip(
        "Chooses the SD Card explorer font colours.\n"
        "  • Automatic (default): follow the operating system. If a\n"
        "    high-contrast/accessibility theme is on, all fonts turn black;\n"
        "    otherwise a dark desktop turns the Directory name orange and the\n"
        "    Directory type label yellow, and a light desktop keeps them blue.\n"
        "  • White mode: always use the light colours (Directory name and\n"
        "    Directory type label stay blue).\n"
        "  • Dark mode: Directory name orange, Directory type label yellow.\n"
        "  • High contrast (black): every font colour is black — for users\n"
        "    who are blind or have low vision.\n"
        "  • Custom: keep your hand-picked colours. Changing any colour\n"
        "    below switches to Custom automatically."
    )
    grid_tab_Settings.addWidget(desktop_theme_lbl, settings_grid_row("desktop_theme"), 0)

    host.settings_desktop_theme_combo = QComboBox()
    host.settings_desktop_theme_combo.addItem("Automatic (default)",   DESKTOP_THEME_AUTOMATIC)
    host.settings_desktop_theme_combo.addItem("White mode",            DESKTOP_THEME_WHITE)
    host.settings_desktop_theme_combo.addItem("Dark mode",             DESKTOP_THEME_DARK)
    host.settings_desktop_theme_combo.addItem("High contrast (black)", DESKTOP_THEME_BLACK)
    host.settings_desktop_theme_combo.addItem("Custom",                DESKTOP_THEME_CUSTOM)
    host.settings_desktop_theme_combo.setCurrentIndex(0)  # default: automatic
    host.settings_desktop_theme_combo.setToolTip(desktop_theme_lbl.toolTip())
    host.settings_desktop_theme_combo.currentIndexChanged.connect(
        lambda _i: _settings_desktop_theme_changed()
    )
    grid_tab_Settings.addWidget(host.settings_desktop_theme_combo, settings_grid_row("desktop_theme"), 1)

    # ── Application UI language (its own row, right under the self-update
    # toggle — columns 2+ sit outside the visible pane width, so the picker
    # must live in the 0/1 column band like every other setting) ──────────
    # Item text is the language's NATIVE name and the stored value rides
    # itemData — the i18n walk deliberately never touches combo items, so
    # this combo needs no translating and no text comparisons can break.
    def _settings_ui_language_changed():
        code = (host.settings_ui_language_combo.currentData()
                or DEFAULT_UI_LANGUAGE)
        configuration_dictionary[SETTING_UI_LANGUAGE] = code
        save_configuration_file()
        try:
            host._i18n_apply(code)      # live re-translate of the whole tree
        except Exception:
            logging.exception("UI language: retranslation failed")

    ui_language_lbl = QLabel("Application language:")
    ui_language_lbl.setToolTip(
        "Language of the application's buttons, labels and checkboxes.\n"
        "Applies immediately; texts written while the app runs (logs, dialogs)\n"
        "follow after a restart. Saved to the configuration file.")
    grid_tab_Settings.addWidget(ui_language_lbl, settings_grid_row("ui_language"), 0)

    host.settings_ui_language_combo = QComboBox()
    for _lang_code, _lang_name in UI_LANGUAGES:
        host.settings_ui_language_combo.addItem(_lang_name, _lang_code)
    _saved_ui_lang = normalize_ui_language(
        configuration_dictionary.get(SETTING_UI_LANGUAGE, ""))
    _saved_ui_ix = host.settings_ui_language_combo.findData(_saved_ui_lang)
    if _saved_ui_ix > 0:
        host.settings_ui_language_combo.setCurrentIndex(_saved_ui_ix)
    host.settings_ui_language_combo.setToolTip(ui_language_lbl.toolTip())
    host.settings_ui_language_combo.currentIndexChanged.connect(
        lambda _i: _settings_ui_language_changed())
    grid_tab_Settings.addWidget(host.settings_ui_language_combo, settings_grid_row("ui_language"), 1)

    def settings_warn_image_nearly_full_statechanged():
        configuration_dictionary[SETTING_WARN_IMAGE_NEARLY_FULL] = "true" if host.settings_warn_image_nearly_full_checkbox.isChecked() else "false"
        save_configuration_file()

    host.settings_warn_image_nearly_full_checkbox = QCheckBox("SD Card - Warn when an image is nearly full.")
    host.settings_warn_image_nearly_full_checkbox.setChecked(True)
    host.settings_warn_image_nearly_full_checkbox.setToolTip(
        "When enabled, a warning dialog is shown after loading or writing to an SD image\n"
        "if it has less than 10% free space remaining.\n"
        "Uncheck this option to suppress that warning."
    )
    host.settings_warn_image_nearly_full_checkbox.stateChanged.connect(settings_warn_image_nearly_full_statechanged)
    grid_tab_Settings.addWidget(host.settings_warn_image_nearly_full_checkbox, settings_grid_row("warn_image_nearly_full"), 0, 1, 2)

    def settings_no_prompt_on_deletion_statechanged():
        configuration_dictionary[SETTING_NO_PROMPT_ON_DELETION] = "true" if host.settings_no_prompt_on_deletion_checkbox.isChecked() else "false"
        save_configuration_file()

    host.settings_no_prompt_on_deletion_checkbox = QCheckBox("Do not prompt for confirmation on deletion.")
    host.settings_no_prompt_on_deletion_checkbox.setChecked(False)
    host.settings_no_prompt_on_deletion_checkbox.setToolTip(
        "When enabled, deleting a file or folder in the SD card image explorer\n"
        "will proceed immediately without asking for confirmation.\n"
        "Leave unchecked to keep the confirmation prompt (recommended)."
    )
    host.settings_no_prompt_on_deletion_checkbox.stateChanged.connect(settings_no_prompt_on_deletion_statechanged)
    grid_tab_Settings.addWidget(host.settings_no_prompt_on_deletion_checkbox, settings_grid_row("no_prompt_on_deletion"), 0, 1, 2)

    # ── Recycle Bin deletes (Send2Trash, optional) ─────────────────────
    # When on (the default), files/folders deleted in the LOCAL file
    # explorers (SD Card tab + NextSync classic tab) are sent to the
    # system Recycle Bin / Trash instead of being removed permanently.
    # Greyed out until the optional Send2Trash package is installed —
    # same gating pattern as the Flask HTTP-bridge toggle. Deletes
    # INSIDE the SD-card image are always permanent (a virtual FAT
    # filesystem has no bin), so this toggle does not affect them.
    def settings_delete_to_recycle_bin_statechanged():
        configuration_dictionary[SETTING_DELETE_TO_RECYCLE_BIN] = (
            "true" if host.settings_delete_to_recycle_bin_checkbox.isChecked()
            else "false")
        save_configuration_file()

    host.settings_delete_to_recycle_bin_checkbox = QCheckBox(
        "Send deleted files to the Recycle Bin (local file explorers).")
    host.settings_delete_to_recycle_bin_checkbox.setChecked(True)  # default on
    if send2trash_available():
        host.settings_delete_to_recycle_bin_checkbox.setToolTip(
            "When enabled, files and folders deleted in the LOCAL file\n"
            "explorers go to the system Recycle Bin / Trash and can be\n"
            "restored from there. When disabled they are deleted\n"
            "permanently, as before. Deletes inside the SD-card image are\n"
            "always permanent. Saved to the configuration file.")
    else:
        host.settings_delete_to_recycle_bin_checkbox.setChecked(False)
        host.settings_delete_to_recycle_bin_checkbox.setEnabled(False)
        host.settings_delete_to_recycle_bin_checkbox.setToolTip(
            "Requires the optional Send2Trash package.\n"
            + zxnu_optional_install_hint("Send2Trash")
            + "\nUntil it is installed, local deletes stay permanent.")
    host.settings_delete_to_recycle_bin_checkbox.stateChanged.connect(
        lambda _s: settings_delete_to_recycle_bin_statechanged())
    grid_tab_Settings.addWidget(host.settings_delete_to_recycle_bin_checkbox, settings_grid_row("delete_to_recycle_bin"), 0, 1, 2)

    def settings_avail_check_statechanged():
        configuration_dictionary[SETTING_AVAIL_CHECK] = "true" if host.settings_avail_check_checkbox.isChecked() else "false"
        save_configuration_file()

    host.settings_avail_check_checkbox = QCheckBox("Perform pre-availability check on Downloads (ZXDB & zxArt).")
    host.settings_avail_check_checkbox.setChecked(True)
    host.settings_avail_check_checkbox.setToolTip(
        "When enabled, the Downloads dialog sends a HEAD request for each file\n"
        "to check whether it is reachable before allowing the download.\n"
        "Files that return HTTP 404 are marked with \u274c and their Download button\n"
        "is disabled. Leave unchecked to skip the check (faster dialog open)."
    )
    host.settings_avail_check_checkbox.stateChanged.connect(settings_avail_check_statechanged)
    _avail_check_visible = ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS or ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS
    host.settings_avail_check_checkbox.setVisible(_avail_check_visible)
    grid_tab_Settings.addWidget(host.settings_avail_check_checkbox, settings_grid_row("avail_check"), 0, 1, 2)

    def settings_multi_search_statechanged():
        configuration_dictionary[SETTING_MULTI_SEARCH] = "true" if host.settings_multi_search_checkbox.isChecked() else "false"
        save_configuration_file()

    host.settings_multi_search_checkbox = QCheckBox("Enable multi API endpoints search (GetIt, ZXDB & zxArt search together).")
    host.settings_multi_search_checkbox.setChecked(True)
    host.settings_multi_search_checkbox.setToolTip(
        "When enabled, a search on any of GetIt, ZXDB or zxArt also runs the\n"
        "same query silently on the other two panes. The tab label is updated\n"
        "with the number of results found, e.g. ZXDB (5)."
    )
    host.settings_multi_search_checkbox.stateChanged.connect(settings_multi_search_statechanged)
    grid_tab_Settings.addWidget(host.settings_multi_search_checkbox, settings_grid_row("multi_search"), 0, 1, 2)

    def settings_search_autocomplete_statechanged():
        enabled = host.settings_search_autocomplete_checkbox.isChecked()
        configuration_dictionary[SETTING_SEARCH_AUTOCOMPLETE] = "true" if enabled else "false"
        save_configuration_file()
        _apply_autocomplete_setting(enabled)

    host.settings_search_autocomplete_checkbox = QCheckBox("Enable search autocompletion.")
    host.settings_search_autocomplete_checkbox.setChecked(True)
    host.settings_search_autocomplete_checkbox.setToolTip(
        "When enabled, typing in any search box shows an autocomplete dropdown\n"
        "with matching titles fetched from the respective API.\n"
        "Uncheck to disable autocomplete suggestions on all search inputs."
    )
    host.settings_search_autocomplete_checkbox.stateChanged.connect(settings_search_autocomplete_statechanged)
    grid_tab_Settings.addWidget(host.settings_search_autocomplete_checkbox, settings_grid_row("search_autocomplete"), 0, 1, 2)

    # ---- Gallery (picture view) settings ----
    def _settings_gallery_anim_changed():
        data = host.settings_gallery_anim_combo.currentData() or DEFAULT_GALLERY_ANIM_MODE
        host._gallery_anim_mode = data
        configuration_dictionary[SETTING_GALLERY_ANIM_MODE] = data
        save_configuration_file()

    gallery_anim_lbl = QLabel("Gallery animation:")
    gallery_anim_lbl.setToolTip(
        "Controls when multi-screenshot tiles cycle through their images\n"
        "in the GetIt / ZXDB / zxArt 'Gallery' (picture) view.\n"
        "  • On hover (default): cycles only while the mouse is over the tile.\n"
        "  • Timed: cycles continuously while the gallery is visible.\n"
        "  • None: never cycles between images (animated GIFs still play)."
    )
    grid_tab_Settings.addWidget(gallery_anim_lbl, settings_grid_row("gallery_anim"), 0)

    host.settings_gallery_anim_combo = QComboBox()
    host.settings_gallery_anim_combo.addItem("On hover (default)", "hover")
    host.settings_gallery_anim_combo.addItem("Timed",              "timer")
    host.settings_gallery_anim_combo.addItem("None",               "none")
    host.settings_gallery_anim_combo.setToolTip(gallery_anim_lbl.toolTip())
    host.settings_gallery_anim_combo.currentIndexChanged.connect(
        lambda _i: _settings_gallery_anim_changed()
    )
    grid_tab_Settings.addWidget(host.settings_gallery_anim_combo, settings_grid_row("gallery_anim"), 1)

    def _settings_gallery_rows_changed(val: int):
        val = max(GALLERY_MIN_ROWS, min(GALLERY_MAX_ROWS, int(val)))
        host._gallery_rows_per_page = val
        configuration_dictionary[SETTING_GALLERY_ROWS_PER_PAGE] = str(val)
        save_configuration_file()

    gallery_rows_lbl = QLabel("Gallery rows per page (min):")
    gallery_rows_lbl.setToolTip(
        "Number of thumbnail rows shown per gallery page.\n"
        f"Range {GALLERY_MIN_ROWS}–{GALLERY_MAX_ROWS}. Default {DEFAULT_GALLERY_ROWS_PER_PAGE}."
    )
    grid_tab_Settings.addWidget(gallery_rows_lbl, settings_grid_row("gallery_rows"), 0)

    host.settings_gallery_rows_spin = QSpinBox()
    host.settings_gallery_rows_spin.setRange(GALLERY_MIN_ROWS, GALLERY_MAX_ROWS)
    host.settings_gallery_rows_spin.setValue(DEFAULT_GALLERY_ROWS_PER_PAGE)
    host.settings_gallery_rows_spin.setToolTip(gallery_rows_lbl.toolTip())
    host.settings_gallery_rows_spin.valueChanged.connect(_settings_gallery_rows_changed)
    grid_tab_Settings.addWidget(host.settings_gallery_rows_spin, settings_grid_row("gallery_rows"), 1)

    def _make_color_button(setting_key, color_attr, label_text, tooltip_text, grid_row,
                           switch_theme=True, on_change=None):
        """Create a label + color-swatch button at the given grid row.

        switch_theme: hand-picking flips the Desktop Theme to Custom and
        re-tints the explorer/UI text mirrors — right for the theme-managed
        colors, wrong for theme-independent ones (retro log console).
        on_change: optional callback invoked with the picked QColor so the
        new color can be pushed to live widgets immediately."""
        lbl = QLabel(label_text)
        lbl.setToolTip(tooltip_text)
        grid_tab_Settings.addWidget(lbl, grid_row, 0)

        btn = QPushButton()
        btn.setFixedSize(80, 22)
        btn.setToolTip(tooltip_text)

        def _update_swatch(color: QColor):
            setattr(host, color_attr, color)
            configuration_dictionary[setting_key] = qcolor_to_hex(color)
            btn.setStyleSheet(f"background-color: {qcolor_to_hex(color)}; border: 1px solid #888;")

        def _apply_color(color: QColor):
            _update_swatch(color)
            if switch_theme:
                # Hand-picking a colour switches the Desktop Theme to Custom so
                # Automatic/Dark no longer overrides the user's choice.
                host._desktop_theme_mode = DESKTOP_THEME_CUSTOM
                configuration_dictionary[SETTING_DESKTOP_THEME] = DESKTOP_THEME_CUSTOM
                if hasattr(host, "_select_desktop_theme_in_combo"):
                    host._select_desktop_theme_in_combo(DESKTOP_THEME_CUSTOM)
            save_configuration_file()
            if switch_theme:
                # Re-tint the rows already shown in the image explorer so the
                # change is visible immediately (no async re-listing needed).
                if hasattr(host, "_image_recolor_all"):
                    host._image_recolor_all()
                # Mirror the change into the NextSync Remote Explorer's panes.
                if hasattr(host, "_re_apply_item_colors"):
                    host._re_apply_item_colors()
                # If the general UI text colour changed, re-apply it to the panes.
                if hasattr(host, "_refresh_tab_stylesheet"):
                    host._refresh_tab_stylesheet()
            if on_change is not None:
                on_change(color)

        def _on_click():
            current = getattr(host, color_attr)
            # Parent the picker to the main window (not the Settings tab) so the
            # tab widget's general-text stylesheet can't leak into a non-native
            # colour dialog on platforms that render it as a Qt widget.
            chosen = QColorDialog.getColor(current, host, f"Choose color — {label_text}")
            if chosen.isValid():
                _apply_color(chosen)

        btn.clicked.connect(_on_click)
        # initialise swatch to the current live color (no save — config not loaded yet)
        _update_swatch(getattr(host, color_attr))
        grid_tab_Settings.addWidget(btn, grid_row, 1)
        return btn

    def _settings_gallery_cols_changed():
        val = host.settings_gallery_cols_combo.currentData() or DEFAULT_GALLERY_COLS
        host._gallery_cols = int(val)
        configuration_dictionary[SETTING_GALLERY_COLS] = str(val)
        save_configuration_file()

    gallery_cols_lbl = QLabel("Gallery items per row:")
    gallery_cols_lbl.setToolTip(
        "Number of thumbnail columns shown in the gallery grid.\n"
        "Default is 4. Choose 2 for larger tiles or 8 for more items per row."
    )
    grid_tab_Settings.addWidget(gallery_cols_lbl, settings_grid_row("gallery_cols"), 0)

    host.settings_gallery_cols_combo = QComboBox()
    host.settings_gallery_cols_combo.addItem("2", 2)
    host.settings_gallery_cols_combo.addItem("4 (default)", 4)
    host.settings_gallery_cols_combo.addItem("8", 8)
    host.settings_gallery_cols_combo.setCurrentIndex(1)  # default: 4
    host.settings_gallery_cols_combo.setToolTip(gallery_cols_lbl.toolTip())
    host.settings_gallery_cols_combo.currentIndexChanged.connect(
        lambda _i: _settings_gallery_cols_changed()
    )
    grid_tab_Settings.addWidget(host.settings_gallery_cols_combo, settings_grid_row("gallery_cols"), 1)

    def _settings_gallery_img_size_changed():
        val = host.settings_gallery_img_size_combo.currentData() or DEFAULT_GALLERY_IMG_SIZE
        host._gallery_img_size = val
        configuration_dictionary[SETTING_GALLERY_IMG_SIZE] = val
        save_configuration_file()

    gallery_img_size_lbl = QLabel("Gallery image size:")
    gallery_img_size_lbl.setToolTip(
        "Controls the height of gallery thumbnails.\n"
        "  • Small: half the medium height\n"
        "  • Medium (default): standard size\n"
        "  • Large: double the medium height"
    )
    grid_tab_Settings.addWidget(gallery_img_size_lbl, settings_grid_row("gallery_img_size"), 0)

    host.settings_gallery_img_size_combo = QComboBox()
    host.settings_gallery_img_size_combo.addItem("Small",          "small")
    host.settings_gallery_img_size_combo.addItem("Medium (default)", "medium")
    host.settings_gallery_img_size_combo.addItem("Large",           "large")
    host.settings_gallery_img_size_combo.setCurrentIndex(1)  # default: medium
    host.settings_gallery_img_size_combo.setToolTip(gallery_img_size_lbl.toolTip())
    host.settings_gallery_img_size_combo.currentIndexChanged.connect(
        lambda _i: _settings_gallery_img_size_changed()
    )
    grid_tab_Settings.addWidget(host.settings_gallery_img_size_combo, settings_grid_row("gallery_img_size"), 1)

    def _settings_gallery_slideshow_changed():
        val = host.settings_gallery_slideshow_combo.currentData()
        try:
            secs = int(val)
        except (TypeError, ValueError):
            secs = DEFAULT_GALLERY_SLIDESHOW_SECS
        host._gallery_slideshow_secs = secs
        set_gallery_slideshow_secs(secs)
        configuration_dictionary[SETTING_GALLERY_SLIDESHOW_SECS] = str(secs)
        # Apply immediately to the persistent detail-slideshow timers so a
        # change takes effect without reopening an item.
        for _tn in ("_zxdb_slideshow_timer", "_zxart_slideshow_timer"):
            _t = getattr(host, _tn, None)
            if _t is not None:
                _t.setInterval(gallery_slideshow_interval_ms())
        save_configuration_file()

    gallery_slideshow_lbl = QLabel("Gallery slideshow pause time:")
    gallery_slideshow_lbl.setToolTip(
        "How long each screenshot is shown before the auto-advancing gallery\n"
        "slideshow moves to the next image. Default is 5 seconds."
    )
    grid_tab_Settings.addWidget(gallery_slideshow_lbl, settings_grid_row("gallery_slideshow"), 0)

    host.settings_gallery_slideshow_combo = QComboBox()
    for _secs in GALLERY_SLIDESHOW_SECS_CHOICES:
        _lbl = f"{_secs} seconds (default)" if _secs == DEFAULT_GALLERY_SLIDESHOW_SECS else f"{_secs} seconds"
        host.settings_gallery_slideshow_combo.addItem(_lbl, _secs)
    host.settings_gallery_slideshow_combo.setCurrentIndex(0)  # default: 5s
    host.settings_gallery_slideshow_combo.setToolTip(gallery_slideshow_lbl.toolTip())
    host.settings_gallery_slideshow_combo.currentIndexChanged.connect(
        lambda _i: _settings_gallery_slideshow_changed()
    )
    grid_tab_Settings.addWidget(host.settings_gallery_slideshow_combo, settings_grid_row("gallery_slideshow"), 1)

    settings_section_lbl = QLabel("Local file explorers & App Text Colors:")
    settings_section_lbl.setToolTip(
        "Customize the foreground color of each item type shown in the SD card\n"
        "image explorer, plus the general app text color (labels, checkboxes,\n"
        "section headers) used across the app in Classic (non-pygame) mode.")
    grid_tab_Settings.addWidget(settings_section_lbl, settings_grid_row("colors_section"), 0, 1, 2)

    host.settings_btn_color_up_directory = _make_color_button(
        SETTING_COLOR_UP_DIRECTORY, "img_color_up_directory",
        "  Up Directory item",
        "Color used for the '[Up Directory..]' navigation row in the image explorer.",
        settings_grid_row("color_up_directory"))
    host.settings_btn_color_dir_name = _make_color_button(
        SETTING_COLOR_DIR_NAME, "img_color_dir_name",
        "  Directory name",
        "Color used for directory name entries in the image explorer.",
        settings_grid_row("color_dir_name"))
    host.settings_btn_color_dir_type = _make_color_button(
        SETTING_COLOR_DIR_TYPE, "img_color_dir_type",
        "  Directory type label",
        "Color used for the 'DIR' type label column of directory entries.",
        settings_grid_row("color_dir_type"))
    host.settings_btn_color_file_name = _make_color_button(
        SETTING_COLOR_FILE_NAME, "img_color_file_name",
        "  File name",
        "Color used for file name entries in the image explorer.",
        settings_grid_row("color_file_name"))
    host.settings_btn_color_file_ext = _make_color_button(
        SETTING_COLOR_FILE_EXT, "img_color_file_ext",
        "  File extension",
        "Color used for the file extension column in the image explorer.",
        settings_grid_row("color_file_ext"))
    host.settings_btn_color_file_size = _make_color_button(
        SETTING_COLOR_FILE_SIZE, "img_color_file_size",
        "  File size",
        "Color used for the file size column in the image explorer.",
        settings_grid_row("color_file_size"))
    host.settings_btn_color_general_text = _make_color_button(
        SETTING_COLOR_GENERAL_TEXT, "img_color_general_text",
        "  General UI text",
        "Color for general app text (labels, checkboxes, section headers)\n"
        "in Classic (non-pygame) mode. The tab panes are always dark, so a\n"
        "light desktop/White theme would otherwise make this text black and\n"
        "unreadable. The White/Dark/Automatic/High-contrast themes set this\n"
        "automatically; picking a color here switches to the Custom theme.",
        settings_grid_row("color_general_text"))

    # ---- Retro log console text color (pygame log windows) ----
    def _apply_retro_log_color(color=None):
        """Push the retro-log text color to whichever retro 8-bit log
        widgets exist (SD Card / NextSync / Help consoles). Lazily-built
        ones pick it up from img_color_retro_log at construction."""
        c = color if color is not None else getattr(host, "img_color_retro_log", None)
        if c is None:
            return
        for _attr in ("_main_retro_log", "_nextsync_retro_log", "_help_retro_log"):
            _w = getattr(host, _attr, None)
            if _w is not None:
                try:
                    _w.set_text_color(qcolor_to_hex(c))
                except Exception:
                    pass
    host._apply_retro_log_color = _apply_retro_log_color

    host.settings_btn_color_retro_log = _make_color_button(
        SETTING_COLOR_RETRO_LOG, "img_color_retro_log",
        "  Retro logs console",
        "Font color for the retro 8-bit (pygame) log consoles on the\n"
        "SD Card, NextSync and Help tabs. Applies immediately. Default is\n"
        "the classic phosphor green. Independent of the Desktop Theme.",
        settings_grid_row("color_retro_log"), switch_theme=False, on_change=_apply_retro_log_color)

    # ---- Retro log font size (SD Card + NextSync pygame log windows) ----
    def _apply_retro_log_font_size(px):
        """Push the point size to whichever retro 8-bit log widgets exist."""
        for _attr in ("_main_retro_log", "_nextsync_retro_log",
                      "_help_retro_log"):
            _w = getattr(host, _attr, None)
            if _w is not None:
                try:
                    _w.set_font_size(px)
                except Exception:
                    pass
    host._apply_retro_log_font_size = _apply_retro_log_font_size

    def _settings_retro_log_font_changed():
        try:
            px = int(host.settings_retro_log_font_combo.currentData())
        except (TypeError, ValueError):
            px = DEFAULT_RETRO_LOG_FONT_SIZE
        host._retro_log_font_size = px
        _apply_retro_log_font_size(px)
        configuration_dictionary[SETTING_RETRO_LOG_FONT_SIZE] = str(px)
        save_configuration_file()

    retro_log_font_lbl = QLabel("Retro log font size:")
    retro_log_font_lbl.setToolTip(
        "Consolas text size for the retro 8-bit (pygame) log windows on the\n"
        "SD Card and NextSync tabs. Applies immediately. Default is 13."
    )
    grid_tab_Settings.addWidget(retro_log_font_lbl, settings_grid_row("retro_log_font"), 0)

    host.settings_retro_log_font_combo = QComboBox()
    for _px in RETRO_LOG_FONT_SIZE_CHOICES:
        _lbl = f"{_px} (default)" if _px == DEFAULT_RETRO_LOG_FONT_SIZE else str(_px)
        host.settings_retro_log_font_combo.addItem(_lbl, _px)
    host._retro_log_font_size = DEFAULT_RETRO_LOG_FONT_SIZE
    _rlf_def_idx = host.settings_retro_log_font_combo.findData(DEFAULT_RETRO_LOG_FONT_SIZE)
    if _rlf_def_idx >= 0:
        host.settings_retro_log_font_combo.setCurrentIndex(_rlf_def_idx)
    host.settings_retro_log_font_combo.setToolTip(retro_log_font_lbl.toolTip())
    host.settings_retro_log_font_combo.currentIndexChanged.connect(
        lambda _i: _settings_retro_log_font_changed()
    )
    grid_tab_Settings.addWidget(host.settings_retro_log_font_combo, settings_grid_row("retro_log_font"), 1)

    def _step_retro_log_font(delta):
        """Right-click "Increase/Decrease font size" on any retro console:
        step through the same choices as the combo above — whose change
        handler applies the size to every console AND persists it to the
        cfg, so the tweak is restored on the next startup."""
        combo = host.settings_retro_log_font_combo
        i = combo.currentIndex() + (1 if delta > 0 else -1)
        if 0 <= i < combo.count():
            combo.setCurrentIndex(i)
    host._step_retro_log_font = _step_retro_log_font

    # ---- Background image opacity ----
    bg_opacity_lbl = QLabel("Background image opacity (%):")
    bg_opacity_lbl.setToolTip(
        "Controls how visible the background image is behind the UI.\n"
        "0 = fully hidden, 100 = fully visible. Default is 5%."
    )
    grid_tab_Settings.addWidget(bg_opacity_lbl, settings_grid_row("bg_opacity"), 0)

    bg_opacity_row = QWidget()
    bg_opacity_row_layout = QHBoxLayout(bg_opacity_row)
    bg_opacity_row_layout.setContentsMargins(0, 0, 0, 0)
    bg_opacity_row_layout.setSpacing(6)

    host.settings_bg_opacity_slider = QSlider(Qt.Horizontal)
    host.settings_bg_opacity_slider.setRange(0, 100)
    host.settings_bg_opacity_slider.setValue(BackgroundWidget.DEFAULT_OPACITY)
    host.settings_bg_opacity_slider.setTickInterval(10)
    host.settings_bg_opacity_slider.setTickPosition(QSlider.TicksBelow)
    host.settings_bg_opacity_slider.setToolTip(
        "Drag to set the background image opacity (0–100 %)."
    )

    host.settings_bg_opacity_spinbox = QSpinBox()
    host.settings_bg_opacity_spinbox.setRange(0, 100)
    host.settings_bg_opacity_spinbox.setValue(BackgroundWidget.DEFAULT_OPACITY)
    host.settings_bg_opacity_spinbox.setSuffix(" %")
    host.settings_bg_opacity_spinbox.setFixedWidth(60)
    host.settings_bg_opacity_spinbox.setToolTip(
        "Type a value 0–100 to set the background image opacity."
    )

    def _build_tab_stylesheet(alpha: int) -> str:
        """Return a QTabWidget stylesheet whose pane background uses the
        given alpha (0–255), allowing the BackgroundWidget behind it to
        show through at the configured opacity level.

        It also forces the general UI text colour on the free-standing
        "chrome" widgets (labels, checkboxes, radio buttons, group boxes)
        that paint directly onto the always-dark pane. Without this the
        text colour is inherited from the OS palette, so a light/White
        desktop theme renders it black-on-dark and unreadable (issue #118).
        Widgets that carry their own colour (e.g. the ``color: #888`` hint
        labels) keep it — a widget's own style sheet wins over this one —
        and controls with their own opaque background (combos, spin boxes,
        line edits) are intentionally left on the platform palette."""
        _gt = getattr(host, "img_color_general_text", None)
        _text_hex = qcolor_to_hex(_gt) if _gt is not None else DEFAULT_COLOR_GENERAL_TEXT
        return (
            f"QTabWidget::pane {{"
            f"  background: rgba(43,43,43,{alpha});"
            f"  border: 1px solid #555;"
            f"}}"
            f"QTabBar::tab {{"
            f"  background: rgba(43,43,43,200);"
            f"  padding: 4px 10px;"
            f"  border: 1px solid #555;"
            f"  border-bottom: none;"
            f"}}"
            f"QTabBar::tab:selected {{"
            f"  background: rgba(60,60,60,220);"
            f"  font-weight: bold;"
            f"}}"
            f"QTabBar::tab:hover {{"
            f"  background: rgba(70,70,70,220);"
            f"}}"
            f"QStackedWidget {{"
            f"  background: transparent;"
            f"}}"
            f"QScrollArea {{"
            f"  background: transparent;"
            f"  border: none;"
            f"}}"
            f"QScrollArea > QWidget > QWidget {{"
            f"  background: transparent;"
            f"}}"
            f"QLabel, QCheckBox, QRadioButton, QGroupBox, QGroupBox::title {{"
            f"  color: {_text_hex};"
            f"}}"
        )
    host._build_tab_stylesheet = _build_tab_stylesheet

    def _apply_tab_text_colors():
        """Paint every tab label with the current general UI text colour so
        the tab bar honours the Desktop Theme / 'General UI text' pick. The
        colour-cycling 'Unite!' tab is skipped — its own animation timer
        owns its colour. Tab text colours are set explicitly because the
        tab stylesheet deliberately omits a QTabBar::tab colour (so the
        Unite! cycling can take effect); a stylesheet colour would override
        setTabTextColor and freeze the animation."""
        try:
            _tab_bar = host._tab_widget.tabBar()
        except Exception:
            return
        _gt = getattr(host, "img_color_general_text", None)
        _color = _gt if _gt is not None else hex_to_qcolor(DEFAULT_COLOR_GENERAL_TEXT)
        for _i in range(host._tab_widget.count()):
            if "Unite!" not in host._tab_widget.tabText(_i):
                _tab_bar.setTabTextColor(_i, _color)
        # Also paint the NextSync experience sub-tabs (nextsync_mode_tabs) so
        # they honour the theme. Skip the "Remote Explorer" tab (index 0)
        # while its colour animation is running — exactly as the Unite! main
        # tab is skipped above — so its cycling colour isn't overwritten.
        _mode_tabs = getattr(host, "nextsync_mode_tabs", None)
        if _mode_tabs is not None:
            _re_anim = getattr(host, "_re_tab_color_timer", None)
            _re_animating = bool(_re_anim is not None and _re_anim.isActive())
            for _i in range(_mode_tabs.count()):
                if _i == 0 and _re_animating:
                    continue
                _mode_tabs.setTabTextColor(_i, _color)
    host._apply_tab_text_colors = _apply_tab_text_colors

    def _refresh_tab_stylesheet():
        """Rebuild and re-apply the tab-widget stylesheet at the current
        background-opacity level, and re-tint the tab-bar labels. Used when
        the general UI text colour changes (Desktop Theme switch or the
        'General UI text' picker)."""
        try:
            _val = host.settings_bg_opacity_slider.value()
        except Exception:
            _val = BackgroundWidget.DEFAULT_OPACITY
        _pane_alpha = max(0, min(255, int(255 - (_val / 100.0) * 255)))
        host._tab_widget.setStyleSheet(_build_tab_stylesheet(_pane_alpha))
        _apply_tab_text_colors()
    host._refresh_tab_stylesheet = _refresh_tab_stylesheet
    # Apply default opacity stylesheet immediately (before config loads)
    _default_pane_alpha = max(0, min(255, int(255 - (BackgroundWidget.DEFAULT_OPACITY / 100.0) * 255)))
    host._tab_widget.setStyleSheet(_build_tab_stylesheet(_default_pane_alpha))

    def _apply_bg_opacity(value: int):
        host.settings_bg_opacity_slider.blockSignals(True)
        host.settings_bg_opacity_spinbox.blockSignals(True)
        host.settings_bg_opacity_slider.setValue(value)
        host.settings_bg_opacity_spinbox.setValue(value)
        host.settings_bg_opacity_slider.blockSignals(False)
        host.settings_bg_opacity_spinbox.blockSignals(False)
        host._bg_widget.set_bg_opacity(value)
        # Map 0-100 % opacity to 255-0 pane alpha (more opacity = more
        # background visible = less opaque pane)
        pane_alpha = max(0, min(255, int(255 - (value / 100.0) * 255)))
        host._tab_widget.setStyleSheet(_build_tab_stylesheet(pane_alpha))
        configuration_dictionary[SETTING_BG_OPACITY] = str(value)
        save_configuration_file()

    host.settings_bg_opacity_slider.valueChanged.connect(_apply_bg_opacity)
    host.settings_bg_opacity_spinbox.valueChanged.connect(_apply_bg_opacity)

    bg_opacity_row_layout.addWidget(host.settings_bg_opacity_slider, 1)
    bg_opacity_row_layout.addWidget(host.settings_bg_opacity_spinbox, 0)
    grid_tab_Settings.addWidget(bg_opacity_row, settings_grid_row("bg_opacity"), 1)

    # ---- Background image selector ----
    bg_image_lbl = QLabel("Background image:")
    bg_image_lbl.setToolTip(
        "Choose a specific background image or 'Random' to cycle through\n"
        "all images in the script folder every 5 seconds."
    )
    grid_tab_Settings.addWidget(bg_image_lbl, settings_grid_row("bg_image"), 0)

    bg_image_row = QWidget()
    bg_image_row_layout = QHBoxLayout(bg_image_row)
    bg_image_row_layout.setContentsMargins(0, 0, 0, 0)
    bg_image_row_layout.setSpacing(8)

    host.settings_bg_image_combo = QComboBox()
    host.settings_bg_image_combo.setToolTip(
        "Select 'Random' to cycle through all available backgrounds,\n"
        "or pick a specific image to lock it."
    )
    # Populate: first entry = Random (empty data = random mode)
    host.settings_bg_image_combo.addItem("Random", "")
    _bg_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    _bg_image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
    _bg_candidates = sorted(
        f for f in os.listdir(_bg_dir)
        if os.path.splitext(f)[1].lower() in _bg_image_extensions
    ) if os.path.isdir(_bg_dir) else []
    for _bg_fname in _bg_candidates:
        _bg_full = os.path.join(_bg_dir, _bg_fname)
        host.settings_bg_image_combo.addItem(os.path.splitext(_bg_fname)[0], _bg_full)
    # Add bundled Qt resource images (embedded via rc_backgrounds)
    from PySide6.QtCore import QDir as _QDir_bg
    for _rc_name in _QDir_bg(":/").entryList():
        if os.path.splitext(_rc_name)[1].lower() in _bg_image_extensions:
            _rc_path = ":/" + _rc_name
            _rc_label = os.path.splitext(_rc_name)[0] + " (built-in)"
            host.settings_bg_image_combo.addItem(_rc_label, _rc_path)

    bg_image_row_layout.addWidget(host.settings_bg_image_combo, 1)

    # Small QLabel used as a thumbnail preview of the selected image
    host.settings_bg_image_preview = QLabel()
    host.settings_bg_image_preview.setFixedSize(160, 90)
    host.settings_bg_image_preview.setAlignment(Qt.AlignCenter)
    host.settings_bg_image_preview.setStyleSheet(
        "border: 1px solid #666; background: #222;"
    )
    host.settings_bg_image_preview.setToolTip("Preview of the selected background image.")
    bg_image_row_layout.addWidget(host.settings_bg_image_preview, 0)

    grid_tab_Settings.addWidget(bg_image_row, settings_grid_row("bg_image"), 1)

    def _update_bg_image_preview(path: str):
        """Refresh the thumbnail label for the given absolute image path."""
        if path:
            px = QPixmap(path)
            if not px.isNull():
                host.settings_bg_image_preview.setPixmap(
                    px.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                return
        host.settings_bg_image_preview.clear()
        host.settings_bg_image_preview.setText("(cycling)")

    def _on_bg_image_combo_changed(index: int):
        path = host.settings_bg_image_combo.itemData(index) or ""
        host._bg_widget.set_bg_image(path)
        _update_bg_image_preview(path)
        configuration_dictionary[SETTING_BG_IMAGE] = path if path.startswith(":/") else (os.path.basename(path) if path else "")
        save_configuration_file()

    host.settings_bg_image_combo.currentIndexChanged.connect(_on_bg_image_combo_changed)

    # Initialise preview to match the current (Random) state — show first
    # available image as a hint, or "(cycling)" if none found.
    if _bg_candidates:
        _hint_path = os.path.join(_bg_dir, _bg_candidates[0])
        _update_bg_image_preview(_hint_path)
    else:
        _update_bg_image_preview("")

    # Also keep the preview in sync when the background cycles (random mode)
    def _on_bg_cycle_update():
        if not host._bg_widget._bg_fixed:
            _p = (host._bg_widget._bg_paths[host._bg_widget._bg_index]
                  if host._bg_widget._bg_paths else "")
            _update_bg_image_preview(_p)

    host._bg_widget._cycle_timer.timeout.connect(_on_bg_cycle_update)

    # ── Crash log toggle (bottom of Settings list) ──────────────────
    def settings_crash_log_enabled_statechanged():
        enabled = host.settings_crash_log_enabled_checkbox.isChecked()
        configuration_dictionary[SETTING_CRASH_LOG_ENABLED] = "true" if enabled else "false"
        save_configuration_file()
        try:
            _zxnu_set_crash_log_enabled(enabled)
        except Exception:
            pass

    host.settings_crash_log_enabled_checkbox = QCheckBox("Enable crash log file generation")
    host.settings_crash_log_enabled_checkbox.setChecked(False)
    host.settings_crash_log_enabled_checkbox.setToolTip(
        "The app ALWAYS writes a rotating diagnostic log to 'zx-next-unite.log'\n"
        "next to the executable (or in %TEMP% if that folder is read-only),\n"
        "capturing warnings, errors and unhandled exceptions — this is the first\n"
        "place to look when something 'silently doesn't work'.\n\n"
        "This checkbox additionally enables the low-level CRASH log\n"
        "('zx-next-unite-crash.log'): native (C-level) crashes via faulthandler,\n"
        "for diagnosing hard crashes of the windowed (.exe) build. Off by default;\n"
        "the always-on diagnostic log above is unaffected by this toggle."
    )
    host.settings_crash_log_enabled_checkbox.stateChanged.connect(
        settings_crash_log_enabled_statechanged)
    grid_tab_Settings.addWidget(host.settings_crash_log_enabled_checkbox, settings_grid_row("crash_log"), 0, 1, 2)

    def settings_disable_no_emulator_toast_statechanged():
        configuration_dictionary[SETTING_DISABLE_NO_EMULATOR_TOAST] = "true" if host.settings_disable_no_emulator_toast_checkbox.isChecked() else "false"
        save_configuration_file()

    host.settings_disable_no_emulator_toast_checkbox = QCheckBox("Disable 'No emulators detected' message at startup")
    host.settings_disable_no_emulator_toast_checkbox.setChecked(False)
    host.settings_disable_no_emulator_toast_checkbox.setToolTip(
        "When enabled, the yellow advisory toast shown at startup when\n"
        "neither CSpect nor Mame are found on PATH is suppressed.\n"
        "Check this if you do not use any emulator and do not want the reminder."
    )
    host.settings_disable_no_emulator_toast_checkbox.stateChanged.connect(settings_disable_no_emulator_toast_statechanged)
    grid_tab_Settings.addWidget(host.settings_disable_no_emulator_toast_checkbox, settings_grid_row("no_emulator_toast"), 0, 1, 2)

    # ── NextSync HTTP bridge toggle + port ──────────────────────────
    def _http_port_widgets_set_enabled(on):
        # The port and connection-limit boxes are only editable while
        # the bridge itself is enabled (and Flask is present at all).
        on = bool(on) and flask_available()
        host.settings_http_port_label.setEnabled(on)
        host.settings_http_port_spinbox.setEnabled(on)
        host.settings_http_conn_label.setEnabled(on)
        host.settings_http_conn_spinbox.setEnabled(on)
        # Bearer-token widgets: the "Require bearer token" checkbox follows
        # the bridge; the token field + Generate button also need the token
        # toggle itself to be on. (hasattr guards the first call, which can
        # precede the token widgets' construction below.)
        if hasattr(host, "settings_http_verbose_checkbox"):
            host.settings_http_verbose_checkbox.setEnabled(on)
        if hasattr(host, "settings_http_token_checkbox"):
            host.settings_http_token_checkbox.setEnabled(on)
            token_on = on and host.settings_http_token_checkbox.isChecked()
            host.settings_http_token_edit.setEnabled(token_on)
            host.settings_http_token_generate_btn.setEnabled(token_on)

    def settings_http_bridge_statechanged():
        enabled = host.settings_http_bridge_checkbox.isChecked()
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_BRIDGE] = "true" if enabled else "false"
        save_configuration_file()
        _http_port_widgets_set_enabled(enabled)
        if enabled:
            host._nextsync_http_bridge_start()
        else:
            host._nextsync_http_bridge_stop()

    host.settings_http_bridge_checkbox = QCheckBox(
        "Enable NextSync HTTP bridge (web server for the Next's .http command)")
    host.settings_http_bridge_checkbox.setChecked(False)
    if flask_available():
        host.settings_http_bridge_checkbox.setToolTip(
            "Starts a small self-hosted web server (Flask, port 80 by default —\n"
            "change it with the port box on the right) that republishes\n"
            "the Remote Explorer's '.sync5 -L' (-l or -listen) session as HTTP routes:\n"
            "/status /sessions /drives /free /ls /get /put /mkdir /rmdir /rmtree\n"
            "/rm /ren /rcpy /rfsize /sum /forceexit. A Next running the built-in .http\n"
            "command (HTTP only, no TLS) — or curl, or a browser — can then\n"
            "drive the file system of the Next connected in -listen mode.\n"
            "The server starts automatically with the app while this is enabled\n"
            "(off by default)."
        )
    else:
        # Flask is optional: without it the toggle is greyed out instead
        # of failing later, and the tooltip says how to enable it.
        host.settings_http_bridge_checkbox.setEnabled(False)
        host.settings_http_bridge_checkbox.setToolTip(
            "The NextSync HTTP bridge needs the optional 'flask' Python\n"
            "package, which is not installed.\n"
            + zxnu_optional_install_hint("flask")
        )
    host.settings_http_bridge_checkbox.stateChanged.connect(
        settings_http_bridge_statechanged)

    host.settings_http_port_label = QLabel("Port:")
    host.settings_http_port_spinbox = QSpinBox()
    host.settings_http_port_spinbox.setRange(1, 65535)
    host.settings_http_port_spinbox.setValue(80)
    # No fixed pixel width: the spinbox sizes itself to its widest value
    # ("65535"), which keeps the digits readable at any font size / DPI
    # scaling (a hard-coded width truncated them on scaled displays).
    _http_port_tip = (
        "TCP port the HTTP bridge listens on (default 80, what the Next's\n"
        ".http dot command talks to by default). Saved to hdfg.cfg\n"
        f"({SETTING_NEXTSYNC_HTTP_PORT}) and used every time the bridge\n"
        "starts; a bridge already running is restarted on the new port a\n"
        "moment after you stop typing.")
    host.settings_http_port_label.setToolTip(_http_port_tip)
    host.settings_http_port_spinbox.setToolTip(_http_port_tip)
    host.settings_http_port_label.setEnabled(False)
    host.settings_http_port_spinbox.setEnabled(False)

    # Debounce the restart of a running bridge: typing "8080" fires
    # valueChanged four times, but the server should bounce only once,
    # on the final value.
    host._http_port_restart_timer = QTimer(host)
    host._http_port_restart_timer.setSingleShot(True)
    host._http_port_restart_timer.setInterval(1500)

    def _http_bridge_apply_new_port():
        if host.settings_http_bridge_checkbox.isChecked() and \
                host._re_bridge is not None and host._re_bridge.running:
            host._nextsync_http_bridge_stop()
            host._nextsync_http_bridge_start()
    host._http_port_restart_timer.timeout.connect(_http_bridge_apply_new_port)

    def settings_http_port_changed(port):
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_PORT] = str(port)
        save_configuration_file()
        host._http_port_restart_timer.start()
    host.settings_http_port_spinbox.valueChanged.connect(
        settings_http_port_changed)

    host.settings_http_conn_label = QLabel("Max connections:")
    host.settings_http_conn_spinbox = QSpinBox()
    host.settings_http_conn_spinbox.setRange(1, 32)
    host.settings_http_conn_spinbox.setValue(1)
    # Sized to content for the same font-size/DPI reason as the port box.
    _http_conn_tip = (
        "Maximum number of HTTP requests the bridge serves concurrently\n"
        "(default 1). The recommended value is 1 to avoid concurrent\n"
        "access: the '.sync5 -L' (-l or -listen) session behind the bridge runs one\n"
        "command at a time anyway, so extra requests are held until a\n"
        "slot frees rather than rejected. Saved to hdfg.cfg\n"
        f"({SETTING_NEXTSYNC_HTTP_CONNECTION_LIMIT}); a bridge already\n"
        "running is restarted on the new value a moment after you stop\n"
        "typing.")
    host.settings_http_conn_label.setToolTip(_http_conn_tip)
    host.settings_http_conn_spinbox.setToolTip(_http_conn_tip)
    host.settings_http_conn_label.setEnabled(False)
    host.settings_http_conn_spinbox.setEnabled(False)

    def settings_http_conn_changed(limit):
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_CONNECTION_LIMIT] = str(limit)
        save_configuration_file()
        host._http_port_restart_timer.start()
    host.settings_http_conn_spinbox.valueChanged.connect(
        settings_http_conn_changed)

    _http_bridge_row = QHBoxLayout()
    _http_bridge_row.addWidget(host.settings_http_bridge_checkbox)
    _http_bridge_row.addSpacing(12)
    _http_bridge_row.addWidget(host.settings_http_port_label)
    _http_bridge_row.addWidget(host.settings_http_port_spinbox)
    _http_bridge_row.addSpacing(12)
    _http_bridge_row.addWidget(host.settings_http_conn_label)
    _http_bridge_row.addWidget(host.settings_http_conn_spinbox)
    _http_bridge_row.addStretch(1)

    # ── Bearer-token protection (second row, under the bridge toggle) ──
    def _http_bridge_restart_if_running():
        # Apply a token change immediately by bouncing a running bridge
        # (the token is read from the config dict at start()).
        if host.settings_http_bridge_checkbox.isChecked() and \
                host._re_bridge is not None and host._re_bridge.running:
            host._nextsync_http_bridge_stop()
            host._nextsync_http_bridge_start()

    def settings_http_token_statechanged():
        enabled = host.settings_http_token_checkbox.isChecked()
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_TOKEN_ENABLED] = \
            "true" if enabled else "false"
        # First time the protection is switched on with no token yet: mint
        # one, persist it and show it so the user can copy it.
        if enabled and not (configuration_dictionary.get(
                SETTING_NEXTSYNC_HTTP_TOKEN) or "").strip():
            tok = generate_bridge_token()
            configuration_dictionary[SETTING_NEXTSYNC_HTTP_TOKEN] = tok
            host.settings_http_token_edit.setText(tok)
        save_configuration_file()
        _http_port_widgets_set_enabled(
            host.settings_http_bridge_checkbox.isChecked())
        _http_bridge_restart_if_running()

    def settings_http_token_edited():
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_TOKEN] = \
            host.settings_http_token_edit.text().strip()
        save_configuration_file()
        _http_bridge_restart_if_running()

    def _http_token_generate():
        tok = generate_bridge_token()
        host.settings_http_token_edit.setText(tok)
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_TOKEN] = tok
        save_configuration_file()
        _http_bridge_restart_if_running()

    host.settings_http_token_checkbox = QCheckBox("Require bearer token")
    host.settings_http_token_checkbox.setChecked(False)
    host.settings_http_token_checkbox.setEnabled(False)
    _http_token_tip = (
        "When on, the web server answers a request only if it carries the\n"
        f"{NEXTSYNC_BRIDGE_TOKEN_HEADER} header equal to the token on the\n"
        "right; every other request gets HTTP 401. A 64-character random\n"
        "token is generated the first time you enable this; edit it or press\n"
        "Generate for a new one. Saved to hdfg.cfg\n"
        f"({SETTING_NEXTSYNC_HTTP_TOKEN_ENABLED} / "
        f"{SETTING_NEXTSYNC_HTTP_TOKEN}) and reapplied on the next request\n"
        "(a running bridge is bounced when you change it). Off by default.")
    host.settings_http_token_checkbox.setToolTip(_http_token_tip)
    host.settings_http_token_checkbox.stateChanged.connect(
        lambda _s: settings_http_token_statechanged())

    host.settings_http_token_edit = QLineEdit()
    host.settings_http_token_edit.setPlaceholderText(
        "bearer token (generated when you enable the checkbox)")
    host.settings_http_token_edit.setToolTip(
        "The shared secret callers must send in the "
        f"{NEXTSYNC_BRIDGE_TOKEN_HEADER} header.\n"
        "Copy it to your .http caller / script, or type your own value.")
    host.settings_http_token_edit.setEnabled(False)
    host.settings_http_token_edit.editingFinished.connect(
        settings_http_token_edited)

    host.settings_http_token_generate_btn = QPushButton("Generate")
    host.settings_http_token_generate_btn.setToolTip(
        "Generate a new random 64-character bearer token")
    host.settings_http_token_generate_btn.setEnabled(False)
    host.settings_http_token_generate_btn.clicked.connect(
        lambda: _http_token_generate())

    def settings_http_verbose_statechanged():
        configuration_dictionary[SETTING_NEXTSYNC_HTTP_VERBOSE] = (
            "true" if host.settings_http_verbose_checkbox.isChecked() else "false")
        save_configuration_file()
        _http_bridge_restart_if_running()

    host.settings_http_verbose_checkbox = QCheckBox("Trace bridge requests")
    host.settings_http_verbose_checkbox.setChecked(False)
    host.settings_http_verbose_checkbox.setEnabled(False)
    host.settings_http_verbose_checkbox.setToolTip(
        "Log every HTTP request and its answer to the NextSync console:\n"
        "  HTTP -> [1] GET /get?path=/games/a.tap\n"
        "  HTTP <- [1] 200 GET /get (12,345 bytes, 4.2s)\n"
        "The number in brackets is how many requests are in flight. This is\n"
        "the nextsync5.py -v view, and what to turn on when a transfer seems\n"
        "to hang: it shows whether the bridge answered or is still working.\n"
        "A request that outlives 15s is announced even with this OFF\n"
        "(\"HTTP .. still waiting\"), so a wedge is visible either way.\n"
        f"Saved to hdfg.cfg ({SETTING_NEXTSYNC_HTTP_VERBOSE}); a running\n"
        "bridge is bounced when you change it. Off by default (it is chatty).")
    host.settings_http_verbose_checkbox.stateChanged.connect(
        lambda _s: settings_http_verbose_statechanged())

    _http_token_row = QHBoxLayout()
    _http_token_row.addWidget(host.settings_http_token_checkbox)
    _http_token_row.addSpacing(8)
    _http_token_row.addWidget(host.settings_http_token_edit, 1)
    _http_token_row.addWidget(host.settings_http_token_generate_btn)

    _http_bridge_vbox = QVBoxLayout()
    _http_bridge_vbox.addLayout(_http_bridge_row)
    _http_bridge_vbox.addLayout(_http_token_row)
    _http_verbose_row = QHBoxLayout()
    _http_verbose_row.addWidget(host.settings_http_verbose_checkbox)
    _http_verbose_row.addStretch(1)
    _http_bridge_vbox.addLayout(_http_verbose_row)
    grid_tab_Settings.addLayout(_http_bridge_vbox, settings_grid_row("http_bridge"), 0, 1, 2)
    host._http_port_widgets_set_enabled = _http_port_widgets_set_enabled

    # ── MAME options (shown when MAME is launchable: a detected binary, or
    # on Linux the Flatpak launch option added below) ──
    if host._mame_usable():
        def settings_mame_rom_changed():
            configuration_dictionary[SETTING_MAME_ROM_CHOICE] = host.settings_mame_rom_combo.currentText().strip()
            save_configuration_file()

        mame_rom_lbl = QLabel("MAME ROM / system:")
        mame_rom_lbl.setToolTip(
            "The MAME system (ROM set) to launch, e.g. 'tbblue' or 'specnext_ks2'.\n"
            "This is inserted right after the MAME executable and is no longer part\n"
            "of the command-line parameters below."
        )
        grid_tab_Settings.addWidget(mame_rom_lbl, settings_grid_row("mame_rom"), 0)

        host.settings_mame_rom_combo = QComboBox()
        for _rom_name in MAME_ROM_CHOICE:
            host.settings_mame_rom_combo.addItem(_rom_name)
        host.settings_mame_rom_combo.setToolTip(mame_rom_lbl.toolTip())
        host.settings_mame_rom_combo.currentIndexChanged.connect(
            lambda _i: settings_mame_rom_changed())
        grid_tab_Settings.addWidget(host.settings_mame_rom_combo, settings_grid_row("mame_rom"), 1)

        def settings_mame_params_changed():
            configuration_dictionary[SETTING_MAME_COMMAND_LINE_PARAMETERS] = host.settings_mame_params_edit.text()
            save_configuration_file()

        mame_params_lbl = QLabel("MAME default launch parameters:")
        mame_params_lbl.setToolTip(
            "Command-line parameters passed to MAME. The '{MAME_EXECUTABLE_NAME}'\n"
            "placeholder resolves to the detected executable. The ROM/system above\n"
            "and the '-hard1 <image>' arguments are added automatically at launch,\n"
            "so the loaded image is always the last argument.\n"
            "The aspect ratio, mouse and joystick options are set with the combo\n"
            "boxes in the MAME group on the SD Card Utility tab; any -aspect,\n"
            "-mouse/-mouse_device or -joystick/-joystickprovider options typed here\n"
            "are ignored (the combos take precedence)."
        )
        grid_tab_Settings.addWidget(mame_params_lbl, settings_grid_row("mame_params"), 0)

        host.settings_mame_params_edit = QLineEdit()
        host.settings_mame_params_edit.setText(
            configuration_dictionary.get(
                SETTING_MAME_COMMAND_LINE_PARAMETERS, MAME_DEFAULT_COMMAND_LINE))
        host.settings_mame_params_edit.setToolTip(mame_params_lbl.toolTip())
        host.settings_mame_params_edit.editingFinished.connect(settings_mame_params_changed)
        grid_tab_Settings.addWidget(host.settings_mame_params_edit, settings_grid_row("mame_params"), 1)

        # The startup update check only exists where the app can actually
        # fetch a build (64-bit Windows / x86_64 Linux). On macOS MAME is
        # detected and launchable but there is no official binary to update
        # to, so the checkbox is omitted entirely rather than shown as a
        # no-op. _check_mame_update_async bails on the same condition.
        if mame_auto_install_supported():
            def settings_mame_update_check_changed():
                on = host.settings_mame_update_check_checkbox.isChecked()
                configuration_dictionary[SETTING_MAME_UPDATE_CHECK] = "true" if on else "false"
                save_configuration_file()

            host.settings_mame_update_check_checkbox = QCheckBox(
                "Check for a newer MAME version at startup")
            _mame_upd_pref = configuration_dictionary.get(
                SETTING_MAME_UPDATE_CHECK, "").strip().lower()
            host.settings_mame_update_check_checkbox.setChecked(
                _mame_upd_pref not in ("false", "0", "no"))  # default on
            host.settings_mame_update_check_checkbox.setToolTip(
                "When MAME is installed, check GitHub at startup for a newer official\n"
                "MAME release and, if one exists, offer to download and install it\n"
                "(overwriting the current downloads/mame files). On by default.\n"
                "Saved to the configuration file."
            )
            host.settings_mame_update_check_checkbox.stateChanged.connect(
                lambda _s: settings_mame_update_check_changed())
            grid_tab_Settings.addWidget(host.settings_mame_update_check_checkbox, settings_grid_row("mame_update_check"), 0, 1, 2)

    # ── Launch MAME with Flatpak (Linux) ──────────────────────────────
    # Shown on Linux regardless of whether a MAME binary was detected, so a
    # user whose only MAME is the Flathub build (org.mamedev.MAME) can enable
    # it here. Sits at grid row 27 — free on Linux, where the Windows-only
    # "check for update" checkbox above never appears, so the two never
    # collide. Enabling it makes MAME launchable app-wide (see _mame_usable),
    # reveals the rom-path box, and refreshes the SD Card launch controls
    # live; all values persist to the cfg file.
    if mame_flatpak_supported():
        def settings_mame_flatpak_rompath_changed():
            configuration_dictionary[SETTING_MAME_FLATPAK_ROMPATH] = (
                host.settings_mame_flatpak_rompath_edit.text().strip())
            save_configuration_file()

        def settings_mame_flatpak_changed():
            on = host.settings_mame_flatpak_checkbox.isChecked()
            configuration_dictionary[SETTING_MAME_FLATPAK] = "true" if on else "false"
            save_configuration_file()
            host.settings_mame_flatpak_rompath_row.setVisible(on)
            # Re-run MAME discovery so the change takes effect without a
            # restart: refresh the SD Card tab's Launch button / relabel /
            # group visibility, then re-report emulator availability (open
            # gallery viewers pick the new state up when next opened).
            host._refresh_mame_launch_ui()
            host._show_emulator_detection_toast()

        _flatpak_box = QWidget()
        _flatpak_layout = QVBoxLayout(_flatpak_box)
        _flatpak_layout.setContentsMargins(0, 0, 0, 0)
        _flatpak_layout.setSpacing(4)

        host.settings_mame_flatpak_checkbox = QCheckBox("Launch Mame with Flatpak")
        host.settings_mame_flatpak_checkbox.setToolTip(
            "Launch MAME via 'flatpak run org.mamedev.MAME' instead of a local\n"
            "binary — for Linux systems where MAME is installed from Flathub.\n"
            "When on, every 'Launch Mame' button becomes 'Launch Mame (flatpak)'\n"
            "and the rom folder below is passed to MAME as -rompath. Off by\n"
            "default. Saved to the configuration file."
        )
        _flatpak_on = str(configuration_dictionary.get(
            SETTING_MAME_FLATPAK, "")).strip().lower() in ("true", "1", "yes", "on")
        host.settings_mame_flatpak_checkbox.setChecked(_flatpak_on)
        host.settings_mame_flatpak_checkbox.stateChanged.connect(
            lambda _s: settings_mame_flatpak_changed())
        _flatpak_layout.addWidget(host.settings_mame_flatpak_checkbox)

        # Rom-path row (label + edit), revealed only while Flatpak is enabled.
        host.settings_mame_flatpak_rompath_row = QWidget()
        _rompath_row_layout = QHBoxLayout(host.settings_mame_flatpak_rompath_row)
        _rompath_row_layout.setContentsMargins(20, 0, 0, 0)
        _rompath_lbl = QLabel("Flatpak rom path:")
        _rompath_lbl.setToolTip(
            "Directory passed to Flatpak MAME as -rompath. Put the boot ROM\n"
            "(tbblue.zip) and any other ROMs here. Defaults to ~/roms.")
        host.settings_mame_flatpak_rompath_edit = QLineEdit()
        host.settings_mame_flatpak_rompath_edit.setText(
            (configuration_dictionary.get(SETTING_MAME_FLATPAK_ROMPATH, "")
             or "").strip() or default_mame_flatpak_rompath())
        host.settings_mame_flatpak_rompath_edit.setToolTip(_rompath_lbl.toolTip())
        host.settings_mame_flatpak_rompath_edit.editingFinished.connect(
            settings_mame_flatpak_rompath_changed)
        _rompath_row_layout.addWidget(_rompath_lbl)
        _rompath_row_layout.addWidget(host.settings_mame_flatpak_rompath_edit, 1)
        host.settings_mame_flatpak_rompath_row.setVisible(_flatpak_on)
        _flatpak_layout.addWidget(host.settings_mame_flatpak_rompath_row)

        grid_tab_Settings.addWidget(_flatpak_box, settings_grid_row("mame_update_check"), 0, 1, 2)

    # ── CSpect default launch parameters ───────────────────────────────
    # Shown unconditionally (unlike the MAME block above, which is gated on
    # MAME being detected) so the box is available even when CSpect is only
    # found later via the async downloads scan. Edits persist to SETTING_CUSTOM;
    # launch_cspect uses this as the base command line and appends the SD Card
    # group options (screen size, sound, VSync, joystick, mouse, frequency,
    # ESC) on top — mirroring the MAME default-launch-parameters handling.
    def settings_cspect_params_changed():
        configuration_dictionary[SETTING_CUSTOM] = host.settings_cspect_params_edit.text()
        save_configuration_file()

    cspect_params_lbl = QLabel("CSpect default launch parameters:")
    cspect_params_lbl.setToolTip(
        "The base command-line parameters CSpect is launched with (default:\n"
        f"'{CSPECT_DEFAULT_LAUNCH_PARAMETERS}'). Tweak them freely; the options\n"
        "chosen in the CSpect group on the SD Card Utility tab (screen size,\n"
        "sound, VSync, joystick, mouse, frequency, ESC) are appended on top at\n"
        "launch. Leave empty to restore the built-in default. Saved to the\n"
        "configuration file."
    )
    grid_tab_Settings.addWidget(cspect_params_lbl, settings_grid_row("cspect_params"), 0)

    host.settings_cspect_params_edit = QLineEdit()
    host.settings_cspect_params_edit.setText(
        configuration_dictionary.get(SETTING_CUSTOM, CSPECT_DEFAULT_LAUNCH_PARAMETERS))
    host.settings_cspect_params_edit.setToolTip(cspect_params_lbl.toolTip())
    host.settings_cspect_params_edit.editingFinished.connect(settings_cspect_params_changed)
    grid_tab_Settings.addWidget(host.settings_cspect_params_edit, settings_grid_row("cspect_params"), 1)

    # ── Unite! pygame background animation toggle ──────────────────────
    def _settings_pygame_anim_changed():
        on = host.settings_pygame_anim_checkbox.isChecked()
        host._allinone_pygame_anim = on
        try:
            configuration_dictionary[SETTING_ALLINONE_PYGAME_ANIM] = (
                "true" if on else "false")
            save_configuration_file()
        except Exception:
            pass
        w = getattr(host, "_allinone_pygame_widget", None)
        if w is not None:
            try:
                w.enable_background(on)
            except Exception:
                pass

    host.settings_pygame_anim_checkbox = QCheckBox(
        "Unite! — Invaders background animation (Retro/pygame mode)")
    host.settings_pygame_anim_checkbox.setChecked(True)
    host.settings_pygame_anim_checkbox.setToolTip(
        "When the Unite! tab is in Retro/pygame visualization mode, play an animated\n"
        "Space Invaders scene (twinkling stars, aliens and a ship) behind the\n"
        "Table / Gallery views. On by default. Saved to the configuration file."
    )
    host.settings_pygame_anim_checkbox.stateChanged.connect(
        lambda _s: _settings_pygame_anim_changed())
    grid_tab_Settings.addWidget(host.settings_pygame_anim_checkbox, settings_grid_row("sdcard_pygame_anim"), 0, 1, 2)

    # ── NextSync retro-log starfield animation toggle ──────────────────
    def _settings_nextsync_anim_changed():
        on = host.settings_nextsync_pygame_anim_checkbox.isChecked()
        host._nextsync_pygame_anim = on
        try:
            configuration_dictionary[SETTING_NEXTSYNC_PYGAME_ANIM] = (
                "true" if on else "false")
            save_configuration_file()
        except Exception:
            pass
        for _attr in ("_nextsync_retro_log", "_main_retro_log", "_help_retro_log"):
            w = getattr(host, _attr, None)
            if w is not None:
                try:
                    w.enable_background(on)
                except Exception:
                    pass

    host.settings_nextsync_pygame_anim_checkbox = QCheckBox(
        "NextSync — starfield log animation (Retro/pygame mode)")
    host.settings_nextsync_pygame_anim_checkbox.setChecked(
        getattr(host, "_nextsync_pygame_anim", True))
    host.settings_nextsync_pygame_anim_checkbox.setToolTip(
        "When the NextSync log window is in Retro/pygame mode, animate the retro\n"
        "starfield backdrop (twinkling stars that flicker into $/£/€ signs)\n"
        "behind the green Consolas log text. On by default. When off, a plain\n"
        "dark background is used. Saved to the configuration file."
    )
    host.settings_nextsync_pygame_anim_checkbox.stateChanged.connect(
        lambda _s: _settings_nextsync_anim_changed())
    grid_tab_Settings.addWidget(host.settings_nextsync_pygame_anim_checkbox, settings_grid_row("nextsync_pygame_anim"), 0, 1, 2)

    # ── Alien Floyd's: optional pygame-ce animated background everywhere ──
    # A Pink Floyd homage. When on, a pygame-ce "Alien Floyd's" animation
    # (pigs, moons, prisms, guitars, dogs … that morph into one another and
    # bob down soft Bézier curves, a defending ship, glowing stars that turn
    # into $/£/€ signs) replaces the cycling background images on every tab,
    # and floats above the image of every gallery item viewer.
    def _apply_alien_floyd_bg(on):
        try:
            import zxnu_pygame as _zpg
            _zpg.set_alien_floyd_enabled(on)
            if on:
                # Warm pygame + the font caches off the UI thread so the
                # first paint doesn't freeze while pygame enumerates fonts.
                _zpg.prewarm_async()
        except Exception:
            pass
        bg = getattr(host, "_bg_widget", None)
        if bg is not None:
            try:
                bg.set_alien_mode(on)
            except Exception:
                pass
        # The Unite! Table/Gallery pygame scene grows/loses its strolling
        # Clives with this setting; nudge its frame clock so the change
        # takes effect immediately (it runs the animation even when the
        # Space-Invaders backdrop toggle is off, just for the Clives).
        allinone = getattr(host, "_allinone_pygame_widget", None)
        if allinone is not None:
            try:
                allinone.sync_animation()
            except Exception:
                pass
    host._apply_alien_floyd_bg = _apply_alien_floyd_bg

    def _settings_alien_bg_changed():
        on = host.settings_alien_floyd_bg_checkbox.isChecked()
        configuration_dictionary[SETTING_ALIEN_FLOYD_BG] = "true" if on else "false"
        if not host._initialising:
            save_configuration_file()
        _apply_alien_floyd_bg(on)

    host.settings_alien_floyd_bg_checkbox = QCheckBox(
        "Alien Floyd's — animated background on all tabs (Retro/pygame)")
    host.settings_alien_floyd_bg_checkbox.setChecked(False)
    host.settings_alien_floyd_bg_checkbox.setToolTip(
        "Pink Floyd homage. Replaces the cycling background images on every\n"
        "tab with an animated 'Alien Floyd's' scene (morphing pigs, moons,\n"
        "prisms, guitars, dogs …, a defending ship and glowing stars that\n"
        "flicker into $/£/€ signs), and floats it above every gallery item\n"
        "viewer image. Optional. Off by default. Saved to the configuration\n"
        "file. Requires the optional 'pygame-ce' package.")
    host.settings_alien_floyd_bg_checkbox.stateChanged.connect(
        lambda _s: _settings_alien_bg_changed())
    grid_tab_Settings.addWidget(host.settings_alien_floyd_bg_checkbox, settings_grid_row("alien_floyd_bg"), 0, 1, 2)

    # ── Alien Floyd's: optional dedicated full-window tab ────────────────
    host._alien_floyd_tab_widget = None

    def _alien_floyd_tab_set_visible(on):
        tabw = wid_inner.tab
        if on:
            if host._alien_floyd_tab_widget is not None and \
                    tabw.indexOf(host._alien_floyd_tab_widget) != -1:
                return
            try:
                from zxnu_pygame import (AlienFloydWidget, pygame_available,
                                         prewarm_async)
                ok, _why = pygame_available()
                if not ok:
                    return
                # Warm pygame + the font caches off the UI thread so opening
                # the tab the first time doesn't freeze while pygame's first
                # match_font() enumerates every installed system font.
                prewarm_async()
            except Exception:
                return
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            # The dedicated tab is the playable game (cursor keys + space);
            # the global background and gallery overlays stay autoplaying.
            anim = AlienFloydWidget(page, game=True)
            page_layout.addWidget(anim)
            page.tab_name_private = "AlienFloyds"
            page._alien_anim = anim
            host._alien_floyd_tab_widget = page
            # Insert just before the Settings tab.
            idx = tabw.count()
            for _i in range(tabw.count()):
                if tabw.tabText(_i).startswith("Settings"):
                    idx = _i
                    break
            tabw.insertTab(idx, page, "🌈 Alien Floyd's")
        else:
            page = host._alien_floyd_tab_widget
            if page is not None:
                _i = tabw.indexOf(page)
                if _i != -1:
                    tabw.removeTab(_i)
                try:
                    anim = getattr(page, "_alien_anim", None)
                    if anim is not None:
                        anim.teardown()
                    page.deleteLater()
                except Exception:
                    pass
                host._alien_floyd_tab_widget = None
    host._alien_floyd_tab_set_visible = _alien_floyd_tab_set_visible

    def _settings_alien_tab_changed():
        on = host.settings_alien_floyd_tab_checkbox.isChecked()
        configuration_dictionary[SETTING_ALIEN_FLOYD_TAB] = "true" if on else "false"
        if not host._initialising:
            save_configuration_file()
        _alien_floyd_tab_set_visible(on)

    host.settings_alien_floyd_tab_checkbox = QCheckBox(
        "Alien Floyd's — show the full-window 'Alien Floyd's' tab (Retro/pygame)")
    host.settings_alien_floyd_tab_checkbox.setChecked(False)
    host.settings_alien_floyd_tab_checkbox.setToolTip(
        "Add a dedicated 'Alien Floyd's' tab (before Settings) that shows the\n"
        "full-window pygame-ce animation. Off by default. Saved to the\n"
        "configuration file. Requires the optional 'pygame-ce' package.")
    host.settings_alien_floyd_tab_checkbox.stateChanged.connect(
        lambda _s: _settings_alien_tab_changed())
    grid_tab_Settings.addWidget(host.settings_alien_floyd_tab_checkbox, settings_grid_row("alien_floyd_tab"), 0, 1, 2)

    # ── itch.io: optional online tab (driven by the 'itch-dl' package) ───
    def _itchio_tab_set_visible(on):
        page = getattr(host, "_itchio_tab_widget", None)
        if page is None:
            return  # itch-dl not installed → nothing to show/hide
        tabw = wid_inner.tab
        idx = tabw.indexOf(page)
        if on:
            if idx != -1:
                return
            # Re-insert at its default position (just after the ZXArt tab).
            _pos_fn = getattr(host, "_itchio_target_index", None)
            pos = _pos_fn(tabw) if _pos_fn is not None else tabw.count()
            tabw.insertTab(pos, page, ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
        else:
            if idx != -1:
                tabw.removeTab(idx)   # keeps the widget alive for re-adding
    host._itchio_tab_set_visible = _itchio_tab_set_visible

    def _settings_itchio_tab_changed():
        on = host.settings_show_itchio_tab_checkbox.isChecked()
        configuration_dictionary[SETTING_SHOW_ITCHIO_TAB] = "true" if on else "false"
        if not host._initialising:
            save_configuration_file()
        _itchio_tab_set_visible(on)

    host.settings_show_itchio_tab_checkbox = QCheckBox(
        "Show the itch.io tab (browse & install your itch.io collections)")
    host.settings_show_itchio_tab_checkbox.setChecked(True)
    _itchdl_ok, _itchdl_why = zxnu_itchio.itchdl_available()
    if not _itchdl_ok:
        host.settings_show_itchio_tab_checkbox.setEnabled(False)
        host.settings_show_itchio_tab_checkbox.setChecked(False)
        host.settings_show_itchio_tab_checkbox.setToolTip(_itchdl_why)
    else:
        host.settings_show_itchio_tab_checkbox.setToolTip(
            "Add an 'itch.io' tab that browses your itch.io collections and\n"
            "installs items via itch-dl. On by default. Saved to the\n"
            "configuration file. Requires the optional 'itch-dl' package.")
    host.settings_show_itchio_tab_checkbox.stateChanged.connect(
        lambda _s: _settings_itchio_tab_changed())
    grid_tab_Settings.addWidget(host.settings_show_itchio_tab_checkbox, settings_grid_row("itchio_tab"), 0, 1, 2)

    # ── Onboarding Wizard (Wizzy, zxnu_wizard.py) ────────────────────────
    def _settings_wizard_changed():
        on = host.settings_wizard_checkbox.isChecked()
        wiz = getattr(host, "_wizard", None)
        if wiz is not None:
            wiz.set_enabled(on, persist=not host._initialising)
        else:
            # During construction the wizard doesn't exist yet; just record
            # the preference (its deferred startup reads it).
            configuration_dictionary[SETTING_WIZARD_ENABLED] = (
                "" if on else "false")
            if not host._initialising:
                save_configuration_file()

    host.settings_wizard_checkbox = QCheckBox(
        "Show Wizzy, the onboarding wizard (bottom-left assistant)")
    host.settings_wizard_checkbox.setChecked(True)
    host.settings_wizard_checkbox.setToolTip(
        "An animated pixel-art wizard that lives in the bottom-left corner:\n"
        "tours the tabs for newcomers (deep-dive content comes from the\n"
        "GitHub wiki user manual), tells ZX Spectrum Next jokes and stories,\n"
        "and follows the application language. On by default. Saved to the\n"
        "configuration file.")
    host.settings_wizard_checkbox.stateChanged.connect(
        lambda _s: _settings_wizard_changed())
    # Right under the Application-language row (all rows below shifted +1).
    grid_tab_Settings.addWidget(host.settings_wizard_checkbox, settings_grid_row("wizard"), 0, 1, 2)

    # ── CSpect: check itch.io for a newer version at startup (default on) ──
    def settings_cspect_update_check_changed():
        on = host.settings_cspect_update_check_checkbox.isChecked()
        configuration_dictionary[SETTING_CSPECT_UPDATE_CHECK] = (
            "true" if on else "false")
        save_configuration_file()

    host.settings_cspect_update_check_checkbox = QCheckBox(
        "Check for CSpect update on itch.io on startup")
    _cspect_upd_pref = configuration_dictionary.get(
        SETTING_CSPECT_UPDATE_CHECK, "").strip().lower()
    host.settings_cspect_update_check_checkbox.setChecked(
        _cspect_upd_pref not in ("false", "0", "no"))  # default on
    host.settings_cspect_update_check_checkbox.setToolTip(
        "When an itch.io API key is configured and CSpect was installed from\n"
        "itch.io, check at startup whether a newer CSpect build is available\n"
        "and, if so, offer to download and install it (into the downloads\n"
        "folder). On by default. Saved to the configuration file.")
    host.settings_cspect_update_check_checkbox.stateChanged.connect(
        lambda _s: settings_cspect_update_check_changed())
    grid_tab_Settings.addWidget(
        host.settings_cspect_update_check_checkbox, settings_grid_row("cspect_update_check"), 0, 1, 2)

    # ── NextSync: start the Remote Explorer '.sync5 -listen' server at startup ──
    # The actual start rides the same deferred path as the
    # -start-remote-explorer-listener command-line switch (zxnu_config_io).
    def _settings_re_autostart_changed():
        on = host.settings_re_autostart_checkbox.isChecked()
        if on:
            # Without a sync root the server could never start (the listen
            # guard refuses) — so the tick itself is refused: revert the box
            # unsaved and say why, visibly. The live mirror is authoritative
            # once the Remote Explorer exists; the saved path covers the
            # pane not having been opened yet this session.
            _root = (getattr(host, "_re_sync_root", "") or str(
                configuration_dictionary.get(
                    SETTING_NEXTSYNC_EXPLORERPATH, "") or "").strip())
            if not (_root and os.path.isdir(_root)):
                host.settings_re_autostart_checkbox.blockSignals(True)
                host.settings_re_autostart_checkbox.setChecked(False)
                host.settings_re_autostart_checkbox.blockSignals(False)
                host._show_toast(
                    "Remote Explorer autostart not enabled",
                    "Define a sync root folder first, on the NextSync tab's "
                    "Remote Explorer view.",
                    variant="yellow", duration_ms=5000)
                return
        configuration_dictionary[SETTING_NEXTSYNC_RE_AUTOSTART] = (
            "true" if on else "false")
        if not host._initialising:
            save_configuration_file()

    host.settings_re_autostart_checkbox = QCheckBox(
        "NextSync — Automatically start Remote Explorer server on startup")
    host.settings_re_autostart_checkbox.setChecked(False)   # default off
    host.settings_re_autostart_checkbox.setToolTip(
        "Start the NextSync Remote Explorer '.sync5 -listen' server\n"
        "automatically when the application opens, so a Next can connect\n"
        "without you pressing Start first. Needs a sync root folder (set it\n"
        "on the NextSync tab's Remote Explorer view). Off by default.\n"
        "Saved to the configuration file.")
    host.settings_re_autostart_checkbox.stateChanged.connect(
        lambda _s: _settings_re_autostart_changed())
    grid_tab_Settings.addWidget(host.settings_re_autostart_checkbox, settings_grid_row("re_autostart"), 0, 1, 2)

    # ── NextSync: what to do when a received file/dir already exists locally ──
    def _settings_nextsync_send_conflict_changed():
        val = host.settings_nextsync_send_conflict_combo.currentData() or DEFAULT_NEXTSYNC_SEND_CONFLICT
        configuration_dictionary[SETTING_NEXTSYNC_SEND_CONFLICT] = val
        save_configuration_file()

    nextsync_send_conflict_lbl = QLabel("NextSync — when a sent file or directory exists locally:")
    nextsync_send_conflict_lbl.setToolTip(
        "Controls what happens when the Next pushes a file/folder via\n"
        "'.sync5 -send <file|dir>' and it already exists on the PC under the\n"
        "sync root.\n"
        "  • Prompt (default): ask each time, with one-time / always options.\n"
        "  • Overwrite: always replace the local file.\n"
        "  • Ignore: never touch existing local files."
    )
    grid_tab_Settings.addWidget(nextsync_send_conflict_lbl, settings_grid_row("nextsync_send_conflict"), 0)

    host.settings_nextsync_send_conflict_combo = QComboBox()
    host.settings_nextsync_send_conflict_combo.addItem("Prompt (default)", "prompt")
    host.settings_nextsync_send_conflict_combo.addItem("Overwrite",        "overwrite")
    host.settings_nextsync_send_conflict_combo.addItem("Ignore",           "ignore")
    host.settings_nextsync_send_conflict_combo.setToolTip(nextsync_send_conflict_lbl.toolTip())
    host.settings_nextsync_send_conflict_combo.currentIndexChanged.connect(
        lambda _i: _settings_nextsync_send_conflict_changed())
    grid_tab_Settings.addWidget(host.settings_nextsync_send_conflict_combo, settings_grid_row("nextsync_send_conflict"), 1)

    # ── Unite! search result sort / render preference ──────────────────
    def _settings_search_sort_changed():
        data = (host.settings_search_sort_combo.currentData()
                or DEFAULT_SEARCH_SORT_MODE)
        host._search_sort_mode = data
        configuration_dictionary[SETTING_SEARCH_SORT_MODE] = data
        save_configuration_file()
        # Re-render the Unite! aggregation so the new ordering takes effect
        # immediately on any results already on screen.
        _aio = getattr(host, "_allinone_repopulate", None)
        if callable(_aio):
            try:
                _aio()
            except Exception:
                pass

    search_sort_lbl = QLabel("Gallery search sort ordering preference:")
    search_sort_lbl.setToolTip(
        "How Unite! multi-search results are ordered when rendered.\n"
        "Every mode still floats items that have a picture/screenshot to the\n"
        "top; they differ only in how the sources are arranged underneath.\n"
        "  • GetIt first class (default): GetIt catalogue items lead, then\n"
        "    ZXDB, zxArt and itch.io.\n"
        "  • Mixed: GetIt is no longer first — sources are interleaved so\n"
        "    GetIt content is scattered among the others.\n"
        "  • Classic: ZXDB and zxArt items are preferred to the top, with\n"
        "    GetIt content placed at the end."
    )
    grid_tab_Settings.addWidget(search_sort_lbl, settings_grid_row("search_sort"), 0)

    host.settings_search_sort_combo = QComboBox()
    host.settings_search_sort_combo.addItem("GetIt first class (default)", SEARCH_SORT_GETIT_FIRST)
    host.settings_search_sort_combo.addItem("Mixed",   SEARCH_SORT_MIXED)
    host.settings_search_sort_combo.addItem("Classic", SEARCH_SORT_CLASSIC)
    host.settings_search_sort_combo.setToolTip(search_sort_lbl.toolTip())
    host.settings_search_sort_combo.currentIndexChanged.connect(
        lambda _i: _settings_search_sort_changed())
    grid_tab_Settings.addWidget(host.settings_search_sort_combo, settings_grid_row("search_sort"), 1)

    # "Open config file" — moved here from the SD-card tab. Opens hdfg.cfg in
    # the system text editor so advanced users can hand-edit settings. Placed
    # at the bottom of the Settings tab, left-aligned so it keeps its natural
    # width rather than stretching across both columns.
    host.button_open_config_file = QPushButton("Open config file", host)
    host.button_open_config_file.clicked.connect(open_cspect_configuration_file)
    grid_tab_Settings.addWidget(host.button_open_config_file, settings_grid_row("open_config_file"), 0, 1, 2, Qt.AlignLeft)

    grid_tab_Settings.setColumnStretch(2, 1)
    zxnextunite_Settings_tab.setLayout(grid_tab_Settings)
    zxnextunite_Settings_tab.tab_name_private = "Settings"

    # The Settings tab has grown a lot of options; on short screens they can
    # exceed the available height. Wrap the content in a vertical scroll area
    # so it scrolls when needed. Kept transparent (no frame, translucent
    # viewport) so the animated/background image shows through like the other
    # tabs. setWidgetResizable keeps the content at the viewport width, so
    # only a vertical scrollbar appears when required.
    settings_scroll = QScrollArea(wid_inner.tab)
    settings_scroll.setWidgetResizable(True)
    settings_scroll.setFrameShape(QFrame.NoFrame)
    settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    settings_scroll.setAttribute(Qt.WA_TranslucentBackground, True)
    settings_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    settings_scroll.setWidget(zxnextunite_Settings_tab)
    settings_scroll.viewport().setAutoFillBackground(False)
    settings_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
    settings_scroll.tab_name_private = "Settings"

    # Read back by MainWindow.__init__ right after this builder returns:
    # wid_inner.tab.addTab(settings_scroll, "Settings ...").
    host.settings_scroll = settings_scroll
