"""zxnu_tab_ops.py — cross-tab operation helpers.

Strangler extraction from MainWindow.__init__ (builder-function seam):
build_tab_ops(host, ...) defines the search-autocomplete enable/apply
helpers, the Unite! multi-search gate + the three cross-search fan-out
helpers, the tab-title badge and spinner machinery (incl. their QTimers,
built here at the block's historical position), the autocomplete-arrow
animation, and on_tab_changed (the QTabWidget currentChanged handler that
drives per-tab side effects: SD gauge refresh, NextSync prepare, tab-title
colour animations, the content disclaimer, lazy Latest fetches).

All injected params are bound before the block (identity); the module
global ``right_disk_image_explorer_content`` is read via the
``_right_disk_content`` getter hook. The closures later builder-call
kwargs and the currentChanged connect consume by bare name are exposed on
``host`` and re-bound at the call site. See CLAUDE.md and the memory
``strangler-extraction-pattern``.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer

from zxnu_config import *


def build_tab_ops(
    host,
    *,
    _right_disk_content,
    _start_transfer_idle_animation,
    _stop_transfer_idle_animation,
    nextsync_perform_checks_and_prepare_server_start,
    update_disk_manager_widget_table,
    wid_inner,
    getit_run_search,
    zxdb_run_search,
    zxart_run_search,
    _show_content_disclaimer,
):
    """Autocomplete/cross-search/badge/spinner helpers + on_tab_changed."""
    def _autocomplete_enabled() -> bool:
        cb = getattr(host, "settings_search_autocomplete_checkbox", None)
        return cb is None or cb.isChecked()

    def _apply_autocomplete_setting(enabled: bool):
        """Attach or detach completers on every search input.

        itch.io is optional (built only when the tab is present), so it is
        looked up with getattr and skipped when absent — keeping it in line
        with the other panes' typing guard so the global autocomplete toggle
        governs its suggestion dropdown too."""
        for input_widget, completer in (
            (host.getit_search_input, getattr(host, "_getit_completer", None)),
            (host.zxdb_search_input,  getattr(host, "_zxdb_completer",  None)),
            (host.zxart_search_input, getattr(host, "_zxart_completer", None)),
            # Never (re)attach the Unite! completer while pygame mode is on:
            # its dropdown steals keyboard focus over the animating surface.
            (getattr(host, "allinone_search_input", None),
             None if getattr(host, "_allinone_pygame_on", False)
             else getattr(host, "_allinone_completer", None)),
            (getattr(host, "itchio_search_input", None),
             getattr(host, "_itchio_completer", None)),
        ):
            if input_widget is None:
                continue
            try:
                input_widget.setCompleter(completer if enabled else None)
            except RuntimeError:
                pass

    def _multi_search_enabled() -> bool:
        cb = getattr(host, "settings_multi_search_checkbox", None)
        return cb is not None and cb.isChecked()

    def _cross_search_getit(query: str, on_done=None):
        """Run a full GetIt search in the background, populate the table and badge the tab."""
        if not query:
            if on_done:
                on_done()
            return
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
        def _after_search():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            n = host.getit_results_table.rowCount()
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, n)
            if on_done:
                on_done()
        getit_run_search(query, 1, _after_search)

    def _cross_search_zxdb(query: str, on_done=None):
        """Run a full ZXDB search in the background, populate the table and badge the tab."""
        if not ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            if on_done:
                on_done()
            return
        if not query:
            if on_done:
                on_done()
            return
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
        def _after_search():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            n = host.zxdb_results_table.rowCount()
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, n)
            if on_done:
                on_done()
        zxdb_run_search(query, 1, _after_search)

    def _cross_search_zxart(query: str, on_done=None):
        """Run a full zxART search in the background, populate the table and badge the tab."""
        if not ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            if on_done:
                on_done()
            return
        if not query:
            if on_done:
                on_done()
            return
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
        def _after_search():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            n = host.zxart_results_table.rowCount()
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, n)
            if on_done:
                on_done()
        zxart_run_search(query, 1, _after_search)

    # ---- Tab badge helpers (multi-search result counts) ----

    def _tab_index(base_title: str) -> int:
        """Return the tab index whose text starts with base_title (ignores badge suffix)."""
        tw = host._tab_widget
        for i in range(tw.count()):
            if tw.tabText(i).startswith(base_title):
                return i
        return -1

    def _set_tab_badge(base_title: str, count: int):
        idx = _tab_index(base_title)
        if idx >= 0:
            host._tab_widget.setTabText(idx, f"{base_title} ({count})")

    def _clear_tab_badge(base_title: str):
        idx = _tab_index(base_title)
        if idx >= 0:
            host._tab_widget.setTabText(idx, base_title)

    # ---- Tab spinner (animated progress while cross-search is running) ----
    _SPINNER_FRAMES = ["🌍", "🌎", "🌏", "🌐"]
    host._spinner_tabs: dict = {}   # base_title -> frame index
    host._spinner_timer = QTimer(host)
    host._spinner_timer.setInterval(200)

    def _spinner_tick():
        for base_title in list(host._spinner_tabs.keys()):
            frame_idx = host._spinner_tabs[base_title]
            frame = _SPINNER_FRAMES[frame_idx % len(_SPINNER_FRAMES)]
            host._spinner_tabs[base_title] = frame_idx + 1
            idx = _tab_index(base_title)
            if idx >= 0:
                host._tab_widget.setTabText(idx, f"{base_title} ({frame})")

    host._spinner_timer.timeout.connect(_spinner_tick)

    def _start_tab_spinner(base_title: str):
        host._spinner_tabs[base_title] = 0
        if not host._spinner_timer.isActive():
            host._spinner_timer.start()

    def _stop_tab_spinner(base_title: str):
        host._spinner_tabs.pop(base_title, None)
        if not host._spinner_tabs:
            host._spinner_timer.stop()
        # Reset the tab text so the last spinner frame doesn't linger.
        # Callers that want a result badge will re-apply it via _set_tab_badge.
        _clear_tab_badge(base_title)

    # ---- Search-input placeholder animator (dancing "..." while an
    # autocomplete cache fetch is running). Multiple concurrent fetches
    # on the same input share the animation via a reference count.
    _AC_ANIM_FRAMES = [
        "...        ",
        " ...       ",
        "  ...      ",
        "   ...     ",
        "    ...    ",
        "     ...   ",
        "      ...  ",
        "       ... ",
        "      ...  ",
        "     ...   ",
        "    ...    ",
        "   ...     ",
        "  ...      ",
        " ...       ",
    ]
    host._ac_anim_state: dict = {}     # id(widget) -> state dict
    host._ac_anim_timer = QTimer(host)
    host._ac_anim_timer.setInterval(120)

    def _ac_anim_tick():
        for state in list(host._ac_anim_state.values()):
            w = state.get("widget")
            if w is None:
                continue
            try:
                frame = _AC_ANIM_FRAMES[state["frame"] % len(_AC_ANIM_FRAMES)]
                state["frame"] += 1
                w.setPlaceholderText(frame)
            except RuntimeError:
                # Underlying C++ widget was destroyed; drop this entry.
                host._ac_anim_state.pop(id(w), None)
            except Exception:
                pass
        if not host._ac_anim_state:
            host._ac_anim_timer.stop()

    host._ac_anim_timer.timeout.connect(_ac_anim_tick)

    def _ac_anim_start(widget):
        if widget is None:
            return
        key = id(widget)
        state = host._ac_anim_state.get(key)
        if state is None:
            try:
                original = widget.placeholderText()
            except Exception:
                original = ""
            state = {"widget": widget, "original": original,
                     "refs": 0, "frame": 0}
            host._ac_anim_state[key] = state
        state["refs"] += 1
        if not host._ac_anim_timer.isActive():
            host._ac_anim_timer.start()

    def _ac_anim_stop(widget):
        if widget is None:
            return
        key = id(widget)
        state = host._ac_anim_state.get(key)
        if state is None:
            return
        state["refs"] -= 1
        if state["refs"] <= 0:
            try:
                widget.setPlaceholderText(state.get("original", ""))
            except Exception:
                pass
            host._ac_anim_state.pop(key, None)
        if not host._ac_anim_state:
            host._ac_anim_timer.stop()

    host._ac_anim_start = _ac_anim_start
    host._ac_anim_stop  = _ac_anim_stop

    def _network_online():
        """The zxnu_network watcher's verdict; optimistic before it is
        built (host attr appears once build_network_watch has run)."""
        gate = getattr(host, "_network_online", None)
        return True if gate is None else gate()

    def on_tab_changed(index):
        if host._initialising:
            return
        # Close any open completer popup so it doesn't linger after the
        # user switches to a different pane.
        for _c in (
            getattr(host, "_getit_completer",    None),
            getattr(host, "_zxdb_completer",     None),
            getattr(host, "_zxart_completer",    None),
            getattr(host, "_allinone_completer", None),
        ):
            if _c is not None:
                try:
                    _c.popup().hide()
                except Exception:
                    pass
        # If any pane is currently in fullscreen mode (stack index 1),
        # dismiss it before activating the new tab so the user always
        # lands on the gallery view of the destination pane.
        try:
            if host._getit_stack.currentIndex() == 1:
                host._hide_fullscreen_getit()
        except Exception:
            pass
        try:
            if host._zxdb_stack.currentIndex() == 1:
                host._hide_fullscreen_zxdb()
        except Exception:
            pass
        try:
            if host._zxart_stack.currentIndex() == 1:
                host._hide_fullscreen_zxart()
        except Exception:
            pass
        tab_title = wid_inner.tab.tabText(index)
        # Only run the idle "breathing" glow on the transfer buttons while the
        # SD-card tab is the active one; stop it on every other tab.
        _stop_transfer_idle_animation()
        # The Remote Explorer sub-tab colour animation only runs while the
        # NextSync tab is visible; stop it here and (re)start it in the
        # NextSync branch below.
        host._re_tab_anim_set_active(False)
        if tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GOOEY):
            _start_transfer_idle_animation()
            # Re-tint the existing rows with the current item colors (the
            # user may have changed them in Settings). This is synchronous
            # and instant, independent of the async re-listing below.
            host._image_recolor_all()
            # Escape hatch for an emulator this app did NOT launch: coming
            # back to the tab is the natural "is it free yet?" moment, and
            # without it a grey-out from an externally started emulator
            # would only clear by re-picking the image by hand. Conditional
            # on the last verdict being "busy", so the common case does no
            # I/O at all on a tab switch.
            _busy = getattr(host, "_image_busy_reason", None)
            _reprobe = getattr(host, "_reprobe_and_regate", None)
            if _busy is not None and _reprobe is not None:
                for _emu in ("MAME", "CSpect"):
                    if _busy(_emu):
                        _reprobe(host.imageinput.currentText()
                                 if _emu == "MAME"
                                 else getattr(host, "right_disk_image_path", ""))
                        break
            if _right_disk_content():
                # Refresh the explorer when returning to the SD Card tab. The
                # listing runs on a worker thread (no UI-thread hdfmonkey call
                # on tab switch).
                update_disk_manager_widget_table()
        elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_GETIT):
            _show_content_disclaimer()
            # Confirmed offline (zxnu_network watcher): skip the automatic
            # fetches — nothing was marked "already fetched", so when the
            # network returns build_network_watch re-runs this handler and
            # they fire then.
            if not _network_online():
                return
            host._getit_fetch_motd()
            # Only fall back to "Latest" when the pane is genuinely empty
            # and no query is pending.  A query mirrored in from an
            # AllInOne multi-search (e.g. "lunar") must be preserved — its
            # background search may have returned few/zero rows, and we
            # must not clear the box or override it with latest releases.
            if (host.getit_results_table.rowCount() == 0
                    and not host._getit_search_loading
                    and not host.getit_search_input.text().strip()):
                host._getit_on_latest()
        elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ZXDB):
            _show_content_disclaimer()
            if not _network_online():
                return
            host._zxdb_on_tab_activated()
        elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ZXART):
            _show_content_disclaimer()
            if not _network_online():
                return
            host._zxart_on_tab_activated()
        elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
            _show_content_disclaimer()
        elif tab_title.startswith(ZX_NEXT_UNITE_TAB_TITLE_NEXTSYNC):
            # Now visible: animate the "Remote Explorer" sub-tab text.
            host._re_tab_anim_set_active(True)
            # Auto-run the "Prepare" step on entering the tab so the
            # "Start Classic NextSync server" button is ready without an extra
            # click. Guard on the prepare button still being visible so we
            # don't re-scan/re-log on every revisit or after a sync is set up.
            if host.nextsync_prepare_server.isVisible():
                nextsync_perform_checks_and_prepare_server_start()


    # Consumed by bare name elsewhere in __init__ (re-bound at the call site).
    host._apply_autocomplete_setting = _apply_autocomplete_setting
    host._multi_search_enabled = _multi_search_enabled
    host._cross_search_getit = _cross_search_getit
    host._cross_search_zxdb = _cross_search_zxdb
    host._cross_search_zxart = _cross_search_zxart
    host._set_tab_badge = _set_tab_badge
    host._clear_tab_badge = _clear_tab_badge
    host._start_tab_spinner = _start_tab_spinner
    host._stop_tab_spinner = _stop_tab_spinner
    host.on_tab_changed = on_tab_changed
