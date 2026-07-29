"""zxnu_retro_ui.py — retro-log toggles, sidebar animations, disclaimer.

Strangler extraction from MainWindow.__init__ (builder-function seam), four
small builders each called at its block's historical position:

* build_main_retro_log(host, ...)   — the SD-card tab's "🎮 Retro" pygame log
  (build/disable/persist/toggle closures + the toggled connect).
* build_sidebar_anim(host)          — the sidebar sync-activity getter and the
  Unite!/Remote-Explorer tab-title colour-cycle timers (built here, at the
  historical position) + _re_tab_anim_set_active.
* build_help_retro_log(host, ...)   — the Help tab's "🎮 Retro" pygame log
  twin of the SD-card one.
* build_content_disclaimer(host, ...) — the one-time content-disclaimer
  dialog (_show_content_disclaimer, re-bound at the call site for the
  build_tab_ops kwarg and the startup calls).

See CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QSizePolicy)

from zxnu_config import *


def build_main_retro_log(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    add_main_log_window,
):
    """The SD-card tab's retro pygame log toggle closures + connect."""
    def _main_build_retro_log():
        if host._main_retro_log is not None:
            return host._main_retro_log
        from zxnu_pygame import RetroLogWidget
        # scrollable live log: auto-follows the newest line, but the user can
        # scroll up (scrollbar / wheel) to read the history.
        widget = RetroLogWidget(
            scrollable=True, follow_tail=True, context_copy=True,
            font_px=getattr(host, "_retro_log_font_size",
                            DEFAULT_RETRO_LOG_FONT_SIZE))
        # Same floor as the classic log so the splitter can shrink either
        # log mode equally.
        widget.setMinimumHeight(60)
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
        for i in range(host.listWidgetLog.count() - 1, -1, -1):
            widget.append(host.listWidgetLog.item(i).text())
        host._main_retro_log = widget
        host.main_log_stack.addWidget(widget)
        return widget

    def _main_pygame_disable(reason=""):
        btn = host.main_pygame_button
        btn.blockSignals(True)
        btn.setChecked(False)
        btn.setText("🎮 Retro")
        btn.blockSignals(False)
        btn.setEnabled(False)
        if reason:
            btn.setToolTip(reason)

    def _main_pygame_persist(enabled):
        # Skip writing while restoring the saved choice at startup so a
        # transient "pygame unavailable" never clobbers the user's pref.
        if getattr(host, "_main_pygame_restoring", False):
            return
        try:
            configuration_dictionary[SETTING_SDCARD_PYGAME_LOG] = (
                "true" if enabled else "false")
            save_configuration_file()
        except Exception:
            pass

    def _main_on_pygame_toggled(checked):
        if checked:
            try:
                from zxnu_pygame import pygame_available
                ok, why = pygame_available()
            except Exception as exc:
                ok, why = False, str(exc)
            if not ok:
                _main_pygame_disable(
                    f"{why}\nInstall with: pip install pygame-ce")
                add_main_log_window(
                    "Pygame mode unavailable — run: pip install pygame-ce")
                return
            try:
                widget = _main_build_retro_log()
            except Exception as exc:
                _main_pygame_disable(f"Pygame init failed: {exc}")
                return
            host._main_pygame_on = True
            host.main_pygame_button.setText("🖼 Switch to 'Classic' view mode")
            host.main_log_stack.setCurrentWidget(widget)
            widget.start()
            _main_pygame_persist(True)
        else:
            host._main_pygame_on = False
            host.main_pygame_button.setText("🎮 Retro")
            if host._main_retro_log is not None:
                host._main_retro_log.stop()
            host.main_log_stack.setCurrentWidget(host.listWidgetLog)
            _main_pygame_persist(False)

    host.main_pygame_button.toggled.connect(_main_on_pygame_toggled)


def build_sidebar_anim(host):
    """Sidebar sync-activity getter + tab-title colour-cycle timers."""
    # Feed the sidebar's NextSync icon its activity state: (running,
    # transferring). Running — either server, classic or Remote Explorer —
    # makes the icon's arrows carry travelling packet pixels so a live
    # server stays visible from any tab; an ongoing transfer (a classic
    # client session, or a Remote Explorer batch operation) accelerates
    # them. Polled from the sidebar's animation timer, so it needs no
    # start/stop wiring here.
    def _sidebar_sync_activity():
        try:
            t = getattr(host, "_nextsync_thread", None)
            classic = t is not None and t.is_alive()
        except Exception:
            classic = False
        running = classic or bool(getattr(host, "_re_running", False))
        if not running:
            return (False, False)
        transferring = bool(getattr(host, "_nextsync_transfer_active", False))
        _rew = getattr(host, "_re_widget", None)
        if _rew is not None and getattr(_rew, "_op_active", False):
            transferring = True
        return (True, transferring)
    host._tab_sidebar.set_sync_activity_getter(_sidebar_sync_activity)

    # ---- Initialize AllInOne tab color cycling timer early ----
    _ALLINONE_COLORS = [QColor('red'), QColor('#FFD700'),
                        QColor('green'), QColor('blue')]  # Red, Yellow, Green, Blue
    host._allinone_color_frame = 0
    host._allinone_color_timer = QTimer(host)
    host._allinone_color_timer.setInterval(500)  # Change color every 500ms

    def _allinone_color_tick():
        # Cycle the tab text color of the AllInOne tab. Using
        # setTabTextColor keeps the existing setTabText-based spinner
        # (rotating earth) and result-count badge fully intact.
        try:
            tab_bar = host._tab_widget.tabBar()
        except Exception:
            return
        color = _ALLINONE_COLORS[host._allinone_color_frame % len(_ALLINONE_COLORS)]
        host._allinone_color_frame += 1
        for i in range(host._tab_widget.count()):
            if "Unite!" in host._tab_widget.tabText(i):
                tab_bar.setTabTextColor(i, color)
                break

    host._allinone_color_timer.timeout.connect(_allinone_color_tick)

    # ---- Remote Explorer sub-tab text colour animation --------------------
    # Mirrors the Unite! main-tab colour cycling, but on the NextSync tab's
    # "Remote Explorer" sub-tab (nextsync_mode_tabs index 0). To save CPU it
    # runs ONLY while the NextSync tab is the visible main tab: on_tab_changed
    # (and the deferred startup activation) call _re_tab_anim_set_active to
    # start/stop it. Reuses the Unite! colour list and 500 ms cadence.
    host._re_tab_color_frame = 0
    host._re_tab_color_timer = QTimer(host)
    host._re_tab_color_timer.setInterval(500)

    def _re_tab_color_tick():
        tabs = getattr(host, "nextsync_mode_tabs", None)
        if tabs is None:
            return
        color = _ALLINONE_COLORS[host._re_tab_color_frame % len(_ALLINONE_COLORS)]
        host._re_tab_color_frame += 1
        try:
            tabs.setTabTextColor(0, color)   # index 0 == "Remote Explorer"
        except RuntimeError:
            pass  # tab bar gone (shutdown) — harmless
    host._re_tab_color_timer.timeout.connect(_re_tab_color_tick)

    def _re_tab_anim_set_active(active):
        """Start/stop the Remote Explorer sub-tab colour cycling. Called with
        True when the NextSync tab becomes visible and False when leaving it,
        so the timer never runs while the user is on another tab."""
        if getattr(host, "nextsync_mode_tabs", None) is None:
            return
        if active:
            if not host._re_tab_color_timer.isActive():
                host._re_tab_color_timer.start()
        elif host._re_tab_color_timer.isActive():
            host._re_tab_color_timer.stop()
            # Repaint the tab in the normal readable colour so it doesn't
            # freeze on whatever cycle colour it stopped on.
            _restore = getattr(host, "_apply_tab_text_colors", None)
            if _restore is not None:
                try:
                    _restore()
                except Exception:
                    pass
    host._re_tab_anim_set_active = _re_tab_anim_set_active


def build_help_retro_log(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
):
    """The Help tab's retro pygame log toggle closures + connect."""
    def _help_build_retro_log():
        if host._help_retro_log is not None:
            return host._help_retro_log
        from zxnu_pygame import RetroLogWidget
        # scrollable=True adds a vertical scrollbar (+ mouse wheel) so the
        # long help text can be read in full, top-to-bottom.
        widget = RetroLogWidget(scrollable=True)
        try:
            widget.enable_background(getattr(host, "_nextsync_pygame_anim", True))
        except Exception:
            pass
        # Seed the user's retro-log text color (Settings color picker).
        try:
            widget.set_text_color(qcolor_to_hex(host.img_color_retro_log))
        except Exception:
            pass
        # Seed it with the existing help contents. The help list reads
        # top-down (first line first), so iterate top-to-bottom for the same
        # reading order.
        for i in range(host.listWidgetHelp.count()):
            widget.append(host.listWidgetHelp.item(i).text())
        host._help_retro_log = widget
        host.help_log_stack.addWidget(widget)
        return widget

    def _help_pygame_disable(reason=""):
        btn = host.help_pygame_button
        btn.blockSignals(True)
        btn.setChecked(False)
        btn.setText("🎮 Retro")
        btn.blockSignals(False)
        btn.setEnabled(False)
        if reason:
            btn.setToolTip(reason)

    def _help_pygame_persist(enabled):
        # Skip writing while restoring the saved choice at startup so a
        # transient "pygame unavailable" never clobbers the user's pref.
        if getattr(host, "_help_pygame_restoring", False):
            return
        try:
            configuration_dictionary[SETTING_HELP_PYGAME_LOG] = (
                "true" if enabled else "false")
            save_configuration_file()
        except Exception:
            pass

    def _help_on_pygame_toggled(checked):
        if checked:
            try:
                from zxnu_pygame import pygame_available
                ok, why = pygame_available()
            except Exception as exc:
                ok, why = False, str(exc)
            if not ok:
                _help_pygame_disable(
                    f"{why}\nInstall with: pip install pygame-ce")
                return
            try:
                widget = _help_build_retro_log()
            except Exception as exc:
                _help_pygame_disable(f"Pygame init failed: {exc}")
                return
            host._help_pygame_on = True
            host.help_pygame_button.setText("🖼 Switch to 'Classic' view mode")
            host.help_log_stack.setCurrentWidget(widget)
            widget.start()
            _help_pygame_persist(True)
        else:
            host._help_pygame_on = False
            host.help_pygame_button.setText("🎮 Retro")
            if host._help_retro_log is not None:
                host._help_retro_log.stop()
            host.help_log_stack.setCurrentWidget(host.listWidgetHelp)
            _help_pygame_persist(False)

    host.help_pygame_button.toggled.connect(_help_on_pygame_toggled)


def build_content_disclaimer(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    _DISCLAIMER_TEXT,
):
    """The one-time content-disclaimer dialog."""
    def _show_content_disclaimer():
        """Show the legal disclaimer splash for content panes.

        Returns True if the caller should proceed (user agreed previously,
        or just ticked the checkbox).  Returns False if the user dismissed
        with Close (no agreement) — caller should still open the pane but
        will be shown the dialog again next time.
        """
        if configuration_dictionary.get(SETTING_CONTENT_DISCLAIMER_AGREED, "") == "1":
            return True

        from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel

        dlg = QDialog(host)
        dlg.setWindowTitle("Content Sources — Legal Disclaimer")
        dlg.setMinimumWidth(620)
        dlg.setMinimumHeight(440)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 12)

        title_lbl = QLabel("<b>Third-Party Content Sources — Legal Disclaimer</b>")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(_DISCLAIMER_TEXT)
        text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(text_edit, 1)

        agree_cb = QCheckBox("I agree and understand. Do not show this message again.")
        layout.addWidget(agree_cb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        def _on_agree(checked):
            if checked:
                configuration_dictionary[SETTING_CONTENT_DISCLAIMER_AGREED] = "1"
                save_configuration_file()
                dlg.accept()

        def _on_close():
            dlg.reject()

        agree_cb.stateChanged.connect(_on_agree)
        close_btn.clicked.connect(_on_close)

        dlg.exec()
        return configuration_dictionary.get(SETTING_CONTENT_DISCLAIMER_AGREED, "") == "1"

    # Consumed by bare name elsewhere in __init__ (re-bound at the call site).
    host._show_content_disclaimer = _show_content_disclaimer
