"""zxnu_zxdb_pane.py — ZXDB (ZXInfo API v5) gallery pane builder.

Strangler extraction from MainWindow.__init__: the ~3k-line ZXDB (ZXInfo API v5) UI
construction blob (widgets + navigation + search/detail/download closures) now
lives here as build_zxdb_pane(host, ...). The operation-layer wiring that still
lives in MainWindow (tab spinners, cross-search dispatch, hdfmonkey transfers,
config persistence, shared retro/gallery UI helpers) is injected as keyword-only
parameters; everything the block assigned to ``self`` is written to ``host`` so
MainWindow keeps every historical attribute. See CLAUDE.md and the memory
``strangler-extraction-pattern``.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import webbrowser
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import (Qt, QTimer, QStringListModel)
from PySide6.QtGui import (QPixmap)
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QComboBox,
    QLineEdit, QFormLayout, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QToolButton, QMenu, QCompleter,
    QFileDialog, QInputDialog, QDialog)

from zxnu_config import *
from zxnu_i18n import ui_tr_now
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *
# Star imports skip underscore-prefixed names; import the private
# helpers the block uses explicitly (tests/test_pane_imports.py
# tripwires that these lists stay complete).
from zxnu_api import (_filter_download_urls, _http_fetch_bytes_with_retry,
    _http_head_ok_with_retry)
from zxnu_gallery import (_ScalingImageLabel, _gallery_viewer_refresh_meta)
from zxnu_media import (_ZXSCR_PIXMAP_CACHE, _build_tooltip_text,
    _gallery_extract_tags, _zxscr_basename_for_url)


def build_zxdb_pane(
    host,
    *,
    configuration_dictionary,
    _DblClickFilter,
    save_configuration_file,
    execute_hdf_monkey,
    generate_disk_file_path,
    update_disk_manager_widget_table,
    _persist_retro,
    _search_autocomplete_on,
    _gif_fetch_bytes,
    _qimage_from_data,
    _gallery_add_text_pages,
    _gallery_add_description_page,
    _make_disclaimer_ticker,
    _make_retro_toggle_button,
    _popup_height_for,
    _wrap_flow_row,
    getit_run_in_thread,
    _CompleterPopupHider,
    _start_tab_spinner,
    _stop_tab_spinner,
    _set_tab_badge,
    _clear_tab_badge,
    _multi_search_enabled,
    _cross_search_getit,
    _cross_search_zxart,
    _right_disk_content,
):
    # -----------------------------------------------------------------------
    # ZXDB UI construction (ZXInfo API v5)
    # -----------------------------------------------------------------------

    host.zxdb_form = QFormLayout()
    host.zxdb_form.setContentsMargins(4, 4, 4, 4)

    # --- Search row (wraps onto extra rows when the window is narrow) ---
    zxdb_search_row = FlowLayout(margin=2)
    host.zxdb_search_input = QLineEdit()
    host.zxdb_search_input.setPlaceholderText("Search ZXDB games... (leave empty for random selection)")
    host.zxdb_search_input.setMinimumWidth(280)
    host.zxdb_search_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    zxdb_search_row.addWidget(host.zxdb_search_input)

    host._zxdb_search_valid_lbl = QLabel()
    host._zxdb_search_valid_lbl.setVisible(False)
    zxdb_search_row.addWidget(host._zxdb_search_valid_lbl)

    host.zxdb_search_button = QPushButton("Search")
    zxdb_search_row.addWidget(host.zxdb_search_button)

    host.zxdb_mode_combo = QComboBox()
    # (display label, internal mode key)
    for label, key in (
        ("Games",       "games"),
        ("By letter",   "byletter"),
        ("Magazines",   "magazines"),
        ("By author",   "author"),
        ("Suggestions", "suggest"),
    ):
        host.zxdb_mode_combo.addItem(label, key)
    host.zxdb_mode_combo.setCurrentIndex(0)
    host.zxdb_mode_combo.setToolTip("Search mode")
    zxdb_search_row.addWidget(host.zxdb_mode_combo)

    host.zxdb_letter_combo = QComboBox()
    for _lbl in ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
        host.zxdb_letter_combo.addItem(_lbl, _lbl.lower())
    host.zxdb_letter_combo.setToolTip("Pick a letter")
    host.zxdb_letter_combo.setVisible(False)
    zxdb_search_row.addWidget(host.zxdb_letter_combo)

    host.zxdb_latest_button = QPushButton("Latest")
    host.zxdb_latest_button.setToolTip("Show the most recently added/updated ZXDB games.")
    zxdb_search_row.addWidget(host.zxdb_latest_button)

    host.zxdb_random_button = QPushButton("Random")
    zxdb_search_row.addWidget(host.zxdb_random_button)

    zxdb_search_row.addWidget(QLabel("Page:"))
    host.zxdb_page_label = QLabel("1")
    host.zxdb_page_label.setMinimumWidth(24)
    zxdb_search_row.addWidget(host.zxdb_page_label)

    host.zxdb_prev_button = QPushButton("< Prev")
    host.zxdb_prev_button.setEnabled(False)
    zxdb_search_row.addWidget(host.zxdb_prev_button)

    host.zxdb_next_button = QPushButton("Next >")
    host.zxdb_next_button.setEnabled(False)
    zxdb_search_row.addWidget(host.zxdb_next_button)

    zxdb_search_row.addWidget(QLabel("View:"))
    host.zxdb_view_combo = QComboBox()
    host.zxdb_view_combo.addItem("Table",   "table")
    host.zxdb_view_combo.addItem("Gallery", "gallery")
    host.zxdb_view_combo.setToolTip(
        "Switch between the classic table view and the picture (gallery) view.\n"
        "Persisted across sessions in the config file."
    )
    zxdb_search_row.addWidget(host.zxdb_view_combo)
    host.zxdb_retro_button = _make_retro_toggle_button(
        host, "_zxdb_item_retro",
        on_change=lambda c, k=SETTING_ZXDB_ITEM_RETRO: (
            _persist_retro(k, c), host._pane_retro_gallery_set("zxdb", c)))
    zxdb_search_row.addWidget(host.zxdb_retro_button)

    host.zxdb_status_label = QLabel("")
    host.zxdb_status_label.setCursor(Qt.ArrowCursor)
    host._zxdb_status_open_path = None
    def _zxdb_status_mouse_press(ev):
        if ev.button() == Qt.LeftButton and host._zxdb_status_open_path:
            p = host._zxdb_status_open_path
            if os.path.isfile(p):
                p = os.path.dirname(p)
            # Ensure the folder exists before trying to open it
            try:
                os.makedirs(p, exist_ok=True)
            except OSError:
                pass
            if not os.path.isdir(p):
                return
            if sys.platform == "win32":
                os.startfile(p)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
    host.zxdb_status_label.mousePressEvent = _zxdb_status_mouse_press
    zxdb_search_row.addWidget(host.zxdb_status_label)

    zxdb_search_widget = _wrap_flow_row(zxdb_search_row)
    # Keep the search/button bar fixed above the scroll area (see the
    # _zxdb_stack assembly) so the vertical scroller only covers the
    # results/details area, matching the Unite! tab.
    host._zxdb_search_widget = zxdb_search_widget

    # --- Results table + screenshot/download column ---
    host.zxdb_results_table = QTableWidget(0, 6)
    host.zxdb_results_table.setHorizontalHeaderLabels(
        ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"]
    )
    host.zxdb_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    host.zxdb_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    host.zxdb_results_table.horizontalHeader().setStretchLastSection(True)
    host.zxdb_results_table.setMinimumHeight(220)
    host.zxdb_results_table.setColumnWidth(0, 80)
    host.zxdb_results_table.setColumnWidth(1, 280)
    host.zxdb_results_table.setColumnWidth(2, 60)
    host.zxdb_results_table.setColumnWidth(3, 180)
    host.zxdb_results_table.setColumnWidth(4, 120)

    host.zxdb_screenshot_label = _ScalingImageLabel()
    host.zxdb_screenshot_label.setFixedSize(256, 192)
    host.zxdb_screenshot_label.setAlignment(Qt.AlignCenter)
    host.zxdb_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
    host.zxdb_screenshot_label.setText("No preview")
    host.zxdb_screenshot_label.setToolTip("Double-click to enlarge")

    # Wrap label in a container so overlay QToolButtons receive clicks
    # (QLabel consumes mouse events and blocks children from receiving them)
    zxdb_preview_container = QWidget()
    zxdb_preview_container.setFixedSize(256, 192)
    host.zxdb_screenshot_label.setParent(zxdb_preview_container)
    host.zxdb_screenshot_label.move(0, 0)

    _nav_btn_style = (
        "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
        " font-size: 20px; font-weight: bold; padding: 2px 6px; }"
        "QToolButton:hover { background: rgba(0,0,0,210); }"
    )
    host.zxdb_prev_shot_btn = QToolButton(zxdb_preview_container)
    host.zxdb_prev_shot_btn.setText("<")
    host.zxdb_prev_shot_btn.setStyleSheet(_nav_btn_style)
    host.zxdb_prev_shot_btn.setVisible(False)
    host.zxdb_prev_shot_btn.raise_()

    host.zxdb_next_shot_btn = QToolButton(zxdb_preview_container)
    host.zxdb_next_shot_btn.setText(">")
    host.zxdb_next_shot_btn.setStyleSheet(_nav_btn_style)
    host.zxdb_next_shot_btn.setVisible(False)
    host.zxdb_next_shot_btn.raise_()

    def _zxdb_reposition_shot_btns():
        h = zxdb_preview_container.height()
        bh = host.zxdb_prev_shot_btn.sizeHint().height()
        by = (h - bh) // 2
        host.zxdb_prev_shot_btn.move(2, by)
        bw = host.zxdb_next_shot_btn.sizeHint().width()
        host.zxdb_next_shot_btn.move(zxdb_preview_container.width() - bw - 2, by)

    _zxdb_reposition_shot_btns()

    host.zxdb_download_button = QPushButton("Download File")
    host.zxdb_download_button.setEnabled(False)

    zxdb_right_col = QVBoxLayout()
    _zxdb_link_label = QLabel('<a href="https://zxinfo.dk/">https://zxinfo.dk/</a>')
    _zxdb_link_label.setOpenExternalLinks(True)
    _zxdb_link_label.setTextFormat(Qt.RichText)
    _zxdb_link_label.setAlignment(Qt.AlignCenter)
    zxdb_right_col.addWidget(_zxdb_link_label)
    # Visibility is controlled by _zxdb_apply_view_mode (shown in Table, hidden in Gallery)
    zxdb_preview_container.setVisible(False)
    host.zxdb_download_button.setVisible(False)
    zxdb_right_col.addWidget(zxdb_preview_container)
    zxdb_right_col.addWidget(host.zxdb_download_button)
    host._zxdb_preview_container = zxdb_preview_container
    host._zxdb_preview_download_btn = host.zxdb_download_button
    zxdb_right_col.addStretch()
    zxdb_right_widget = QWidget()
    zxdb_right_widget.setLayout(zxdb_right_col)

    zxdb_table_row = QHBoxLayout()

    host.zxdb_view_stack = QStackedWidget()
    host.zxdb_view_stack.addWidget(host.zxdb_results_table)  # index 0

    def _zxdb_gallery_title(e):
        return (e.get("title") or e.get("id") or "")[:80]
    def _zxdb_gallery_info(e):
        parts = []
        if e.get("author"):  parts.append(e["author"])
        if e.get("year"):    parts.append(str(e["year"]))
        if e.get("machine"): parts.append(e["machine"])
        if e.get("genre"):   parts.append(e["genre"])
        return " · ".join(parts)

    def _zxdb_tooltip_getter(e):
        lines = []
        if e.get("title"):   lines.append(f"Title: {e['title']}")
        if e.get("year"):    lines.append(f"Year: {e['year']}")
        if e.get("author"):  lines.append(f"Author: {e['author']}")
        if e.get("machine"): lines.append(f"Machine: {e['machine']}")
        if e.get("genre"):   lines.append(f"Genre: {e['genre']}")
        return _build_tooltip_text(lines)

    def _zxdb_thumb_fetch(entry, set_pixmap, set_screenshots):
        eid = entry.get("id") or ""
        if not eid:
            return
        def _fn():
            payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
            detail  = zxdb_parse_game_detail(payload)
            shots = detail.get("screenshots") or []
            if not shots and detail.get("screenshot_url"):
                shots = [{"url": detail["screenshot_url"], "type": ""}]
            # ZXDB's parser already restricts screenshots to image
            # assets, so we trust the upstream classification here
            # (extensions on the CDN URLs are not always one of the
            # canonical image suffixes we know about).
            urls = []
            for s in shots:
                if not isinstance(s, dict):
                    continue
                u = s.get("url") or ""
                if u:
                    urls.append(u)
            # Also collect downloads so we can render a typed
            # placeholder if no real image is available.
            return (urls, detail.get("downloads") or [])
        def _on_ok(res):
            urls, downloads = res
            if urls:
                set_screenshots(urls)
                def _img_fn(_u=urls[0]):
                    data = _http_fetch_bytes_with_retry(
                        _u, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=20)
                    # Decode off the UI thread for every format — including
                    # SCR, whose (now buffer-based) decode produces a QImage
                    # that is safe to build on a worker thread. Only the
                    # cheap QPixmap.fromImage() runs back on the UI thread.
                    if zxscr_url_is_scr(_u):
                        img = zxscr_qimage_from_bytes(data, _zxscr_basename_for_url(_u))
                    else:
                        img = _qimage_from_data(data)
                    return (_u, img)
                def _img_ok(r):
                    u, img = r
                    px = QPixmap.fromImage(img) if (img is not None and not img.isNull()) else QPixmap()
                    if not px.isNull():
                        set_pixmap(px, u)
                getit_run_in_thread(_img_fn, _img_ok, lambda _e: None, gated=True)
                return
            # No real image: render a typed placeholder showing the
            # primary download format (e.g. TAP, POK, PDF) so the cell
            # is still informative instead of a black square.
            label, fname = zxfmt_pick_best_download(downloads)
            title = entry.get("title") or eid
            sub = fname or title
            placeholder_url = f"placeholder://{label}/{sub}"
            set_screenshots([placeholder_url])
            pm = zxfmt_make_placeholder_pixmap(label, sub)
            if not pm.isNull():
                set_pixmap(pm, placeholder_url)
        getit_run_in_thread(_fn, _on_ok, lambda _e: None, gated=True)

    def _zxdb_extra_fetch(url, on_pixmap):
        if isinstance(url, str) and url.startswith("placeholder://"):
            rest = url[len("placeholder://"):]
            label, _, sub = rest.partition("/")
            pm = zxfmt_make_placeholder_pixmap(label or "FILE", sub)
            if not pm.isNull():
                on_pixmap(pm)
            return
        scr_url = zxscr_url_is_scr(url)
        if scr_url:
            base = _zxscr_basename_for_url(url)
            cached = _ZXSCR_PIXMAP_CACHE.get(base)
            if cached is not None and not cached.isNull():
                on_pixmap(cached)
                return
        def _fn(_u=url):
            return _http_fetch_bytes_with_retry(
                _u, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=20)
        def _on_ok(data, _u=url, _scr=scr_url):
            if _scr:
                pm = zxscr_convert_bytes_to_pixmap(
                    data, _zxscr_basename_for_url(_u))
                if pm is not None and not pm.isNull():
                    on_pixmap(pm)
                    return
            px = QPixmap()
            px.loadFromData(data)
            if not px.isNull():
                on_pixmap(px)
        getit_run_in_thread(_fn, _on_ok, lambda _e: None)

    def _zxdb_gallery_context_menu(entry, global_pos):
        eid   = entry.get("id") or ""
        title = entry.get("title") or eid
        kind  = (entry.get("_kind") or "game").lower()
        _safe_title = zxdb_sanitize_folder(title)
        _img_path   = host.right_disk_image_path or ""
        _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                       ) if _img_path else "(no image loaded)"
        _sd_dest    = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base    = _zxdb_resolve_base_path(host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest    = os.path.join(_ns_base, _safe_title)
        menu = QMenu()
        act_download = menu.addAction(ui_tr_now("Download content"))
        act_mlt      = menu.addAction(ui_tr_now("More like this"))
        menu.addSeparator()
        act_send_sd  = menu.addAction(ui_tr_now("Send to SD card (image)  →  {dest}").format(dest=_sd_dest))
        act_send_sd.setEnabled(bool(host.right_disk_image_path) and bool(_right_disk_content()))
        act_send_ns  = menu.addAction(ui_tr_now("Send using NextSync  →  {dest}").format(dest=_ns_dest))
        if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
            act_download.setVisible(False)
            act_send_sd.setVisible(False)
            act_send_ns.setVisible(False)
        menu.addSeparator()
        _web_url = zxdb_entry_website_url(eid)
        act_open_web = menu.addAction(ui_tr_now("Open on website (zxinfo.dk)"))
        act_open_web.setEnabled(bool(_web_url))
        action = menu.exec(global_pos)
        if action is None:
            return
        if action is act_open_web:
            if _web_url:
                try:
                    webbrowser.open(_web_url, new=2)
                except Exception:
                    pass
            return
        if kind == "magazine":
            # For magazine cells just show the download overlay if detail is loaded
            if action is act_download:
                if host._zxdb_selected_downloads:
                    zxdb_show_downloads_overlay(host._zxdb_selected_title or title,
                                                host._zxdb_selected_downloads)
            return
        def _fetch_and_send(dest_root, post_action=None):
            if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                _zxdb_send_to_path(host._zxdb_selected_title or title,
                                   host._zxdb_selected_downloads, dest_root, post_action)
                return
            zxdb_set_status(f"Loading {eid}\u2026")
            def _fn():
                payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)
            def _on_ok(detail, _dr=dest_root, _pa=post_action):
                zxdb_populate_detail(detail)
                dls = detail.get("downloads", []) or []
                if not dls:
                    zxdb_set_status("No downloadable files for this entry.")
                    return
                _zxdb_send_to_path(detail.get("title") or title, dls, _dr, _pa)
            def _on_err(err):
                zxdb_set_status(f"Detail error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
        if action is act_download:
            if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                zxdb_show_downloads_overlay(host._zxdb_selected_title or title,
                                            host._zxdb_selected_downloads)
                return
            zxdb_set_status(f"Loading {eid}\u2026")
            def _fn_dl():
                payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)
            def _on_ok_dl(detail):
                zxdb_populate_detail(detail)
                downloads = detail.get("downloads", []) or []
                if not downloads:
                    zxdb_set_status("No downloadable files for this entry.")
                    return
                zxdb_show_downloads_overlay(detail.get("title") or title, downloads)
            def _on_err_dl(err):
                zxdb_set_status(f"Detail error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn_dl, _on_ok_dl, _on_err_dl)
        elif action is act_send_sd:
            if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                _zxdb_send_to_image(host._zxdb_selected_title or title,
                                    host._zxdb_selected_downloads)
                return
            zxdb_set_status(f"Loading {eid}\u2026")
            def _fn_sd():
                payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)
            def _on_ok_sd(detail):
                zxdb_populate_detail(detail)
                dls = detail.get("downloads", []) or []
                if not dls:
                    zxdb_set_status("No downloadable files for this entry.")
                    return
                _zxdb_send_to_image(detail.get("title") or title, dls)
            def _on_err_sd(err):
                zxdb_set_status(f"Detail error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn_sd, _on_ok_sd, _on_err_sd)
        elif action is act_send_ns:
            def _after_ns_dl(_folder):
                QTimer.singleShot(0, host._nextsync_start_server_fn)
            _fetch_and_send(_ns_base, _after_ns_dl)
        elif action is act_mlt:
            zxdb_set_status(f"Finding titles similar to '{title}'\u2026")
            def _fn_mlt():
                payload = zxdb_fetch_json(
                    f"/entries/morelikethis/{urllib.parse.quote(eid)}"
                    f"?mode=compact&size={ZXDB_PAGE_SIZE}"
                )
                entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                for e in entries:
                    e["_kind"] = "game"
                return ("games", entries, total, 1, total_pages)
            def _on_ok_mlt(data):
                kind2, entries, total, pg, total_pages = data
                zxdb_populate_results(entries, pg, total_pages, kind2)
                zxdb_set_status(f"{len(entries)} title(s) similar to '{title}'")
            def _on_err_mlt(err):
                zxdb_set_status(f"More like this error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn_mlt, _on_ok_mlt, _on_err_mlt)

    host.zxdb_gallery_view = GalleryView(
        rows_per_page_getter=lambda: host._gallery_rows_per_page,
        anim_mode_getter=lambda: host._gallery_anim_mode,
        cols_getter=lambda: host._gallery_cols,
        img_size_getter=lambda: host._gallery_img_size,
        thumb_fetch_cb=_zxdb_thumb_fetch,
        extra_fetch_cb=_zxdb_extra_fetch,
        title_getter=_zxdb_gallery_title,
        info_getter=_zxdb_gallery_info,
        context_menu_cb=_zxdb_gallery_context_menu,
        is_favorite_cb=lambda e: host._fav_is({**e, "_fav_source": "zxdb"}),
        toggle_favorite_cb=lambda e: host._fav_toggle({**e, "_fav_source": "zxdb"}),
        tooltip_getter=_zxdb_tooltip_getter,
    )
    # Animate .gif thumbnails (QMovie) just like the in-pane item viewer.
    host.zxdb_gallery_view.set_gif_fetch_cb(_gif_fetch_bytes)
    host._fav_fetchers = getattr(host, "_fav_fetchers", {})
    host._fav_fetchers["zxdb"] = {
        "thumb": _zxdb_thumb_fetch,
        "extra": _zxdb_extra_fetch,
        "title": _zxdb_gallery_title,
        "info":  _zxdb_gallery_info,
    }
    host.zxdb_view_stack.addWidget(host.zxdb_gallery_view)  # index 1

    zxdb_table_row.addWidget(host.zxdb_view_stack, 1)
    # Animated retro "SEARCHING..." banner over the
    # results area whenever a fetch is in flight — including re-searches over
    # already-populated content, so it stays visible on top of the pygame
    # GalleryScene (not only on the first/empty load).
    host._zxdb_loading_overlay = RetroLoadingOverlay(
        host.zxdb_view_stack,
        lambda: getattr(host, "_zxdb_search_loading", False))
    zxdb_table_row.addWidget(zxdb_right_widget)
    zxdb_table_container = QWidget()
    zxdb_table_container.setLayout(zxdb_table_row)
    host.zxdb_form.addRow(zxdb_table_container)

    # --- Detail panel (rebuilt per kind: game / magazine / suggest) ---
    host._zxdb_detail_layout = QFormLayout()
    host._zxdb_detail_layout.setContentsMargins(0, 0, 0, 0)
    host._zxdb_detail_rows = []   # list of (label_widget, value_widget) pairs

    host._zxdb_detail_widget = QWidget()
    host._zxdb_detail_widget.setLayout(host._zxdb_detail_layout)
    # Detail widget intentionally not added to form; info shown via cell tooltips instead.

    # --- Internal state ---
    host._zxdb_current_page  = 1
    host._zxdb_total_pages   = 1
    host._zxdb_last_query    = ""
    host._zxdb_selected_id   = ""
    host._zxdb_selected_title = ""
    host._zxdb_selected_downloads = []
    host._zxdb_search_loading = False
    # Generation token: see _getit_search_gen for rationale.
    host._zxdb_search_gen = 0
    host._zxdb_loaded_once   = False
    host._zxdb_results_mode  = "games"
    host._zxdb_magazine_issues = []   # issues list of the currently-loaded magazine
    host._zxdb_last_entries = []
    host._zxdb_ac_cache: dict = {}    # letter -> sorted list of titles
    host._zxdb_ac_fetching: set = set()  # letters currently being fetched

    # Slideshow state
    host._zxdb_screenshots = []        # list of dicts {url, type}
    host._zxdb_shot_cache  = {}        # url -> QPixmap
    host._zxdb_shot_index  = 0
    host._zxdb_shot_token  = 0         # invalidates outstanding fetches when row changes
    host._zxdb_slideshow_timer = QTimer(host)
    host._zxdb_slideshow_timer.setInterval(gallery_slideshow_interval_ms())
    # Stepping back with ◀/< holds on that image for a long beat (60s) so the
    # user can study it before the normal 5s cadence resumes. Guarded by the
    # shot token so a later row selection can't let a stale dwell advance
    # freshly-loaded screenshots.
    host._zxdb_shot_dwell_timer = QTimer(host)
    host._zxdb_shot_dwell_timer.setSingleShot(True)
    host._zxdb_dwell_token = -1

    # ---- Helpers ----

    def zxdb_set_status(msg: str, open_path: str = None):
        host.zxdb_status_label.setText(msg)
        host._zxdb_status_open_path = open_path
        if open_path:
            host.zxdb_status_label.setStyleSheet("color: #4fc3f7; text-decoration: underline;")
            host.zxdb_status_label.setCursor(Qt.PointingHandCursor)
        else:
            host.zxdb_status_label.setStyleSheet("")
            host.zxdb_status_label.setCursor(Qt.ArrowCursor)

    def _zxdb_clear_detail_rows():
        while host._zxdb_detail_layout.rowCount() > 0:
            host._zxdb_detail_layout.removeRow(0)
        host._zxdb_detail_rows = []

    def _zxdb_add_row(label: str, value: str, *, dim: bool = False, wrap: bool = True, is_html: bool = False):
        lab = QLabel(label)
        val = QLabel()
        import html as _html
        if is_html:
            inner = str(value or "")
        else:
            inner = _html.escape(str(value or "")).replace("\n", "<br>")
        val.setText(
            f'<div style="word-wrap:break-word; word-break:break-all;">{inner}</div>'
        )
        val.setTextFormat(Qt.RichText)
        if wrap:
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)
            val.setMinimumWidth(0)
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if dim:
            val.setStyleSheet("color: #888;")
        host._zxdb_detail_layout.addRow(lab, val)
        host._zxdb_detail_rows.append((lab, val))

    def zxdb_clear_detail():
        _zxdb_clear_detail_rows()
        host.zxdb_screenshot_label.setText("No preview")
        host.zxdb_screenshot_label.clear_image()
        host.zxdb_download_button.setEnabled(False)
        host._zxdb_selected_id = ""
        host._zxdb_selected_title = ""
        host._zxdb_selected_downloads = []
        host._zxdb_slideshow_timer.stop()
        host._zxdb_shot_token += 1
        host._zxdb_screenshots = []
        host._zxdb_shot_cache  = {}
        host._zxdb_shot_index  = 0

    def zxdb_populate_results(entries, page, total_pages, mode="games"):
        host._zxdb_current_page = page or 1
        host._zxdb_total_pages  = total_pages or 1
        host._zxdb_results_mode = mode
        host.zxdb_page_label.setText(str(host._zxdb_current_page))
        host.zxdb_prev_button.setEnabled(host._zxdb_current_page > 1)
        host.zxdb_next_button.setEnabled(host._zxdb_current_page < host._zxdb_total_pages)

        headers = _ZXDB_HEADERS.get(mode, _ZXDB_HEADERS["games"])
        host.zxdb_results_table.setHorizontalHeaderLabels(headers)

        host.zxdb_results_table.setRowCount(0)
        for e in entries:
            row = host.zxdb_results_table.rowCount()
            host.zxdb_results_table.insertRow(row)
            id_item = QTableWidgetItem(e.get("id", ""))
            # Stash the full entry dict on column 0 so row-selection can dispatch
            # detail loading per kind without re-querying the source list.
            id_item.setData(Qt.UserRole, e)
            host.zxdb_results_table.setItem(row, 0, id_item)
            host.zxdb_results_table.setItem(row, 1, QTableWidgetItem(e.get("title", "")))
            host.zxdb_results_table.setItem(row, 2, QTableWidgetItem(e.get("year", "")))
            host.zxdb_results_table.setItem(row, 3, QTableWidgetItem(e.get("author", "")))
            host.zxdb_results_table.setItem(row, 4, QTableWidgetItem(e.get("machine", "")))
            host.zxdb_results_table.setItem(row, 5, QTableWidgetItem(e.get("genre", "")))
        host._zxdb_last_entries = list(entries)
        host.zxdb_gallery_view.populate(entries)
        host._pane_retro_gallery_refresh("zxdb")
        host.zxdb_gallery_view.select_entry(
            lambda _e, _sel=host._zxdb_selected_id: bool(_sel) and _e.get("id") == _sel
        )
        try:
            _aio = getattr(host, "_allinone_repopulate", None)
            if _aio is not None:
                _aio()
        except Exception:
            pass

    def zxdb_populate_detail(detail: dict):
        """Game detail (used for games and by-author results)."""
        _zxdb_clear_detail_rows()
        _zxdb_add_row("Title:",       detail.get("title", ""))
        _zxdb_add_row("Year:",        detail.get("year", ""))
        _zxdb_add_row("Authors:",     detail.get("authors", ""))
        _zxdb_add_row("Published by:", detail.get("publishers", ""))
        _zxdb_add_row("Machine:",     detail.get("machine", ""))
        _zxdb_add_row("Genre:",       detail.get("genre", ""))
        _zxdb_add_row(
            "Description:",
            detail.get("description", "") or detail.get("remarks", ""),
            dim=True,
        )

        host._zxdb_selected_downloads = _filter_download_urls(
            detail.get("downloads", []) or []
        )
        host.zxdb_download_button.setEnabled(bool(host._zxdb_selected_downloads))

    def zxdb_populate_magazine_detail(name: str, summary: dict, issues_payload):
        """Render /magazines/{name}/issues result."""
        _zxdb_clear_detail_rows()
        issues = []
        country = summary.get("country") or ""
        language = summary.get("language") or ""
        mtype = summary.get("type") or ""
        publisher = summary.get("publisher") or ""
        if isinstance(issues_payload, dict):
            country   = issues_payload.get("country")   or country
            language  = issues_payload.get("language")  or language
            mtype     = issues_payload.get("type")      or mtype
            publisher = issues_payload.get("publisher") or publisher
            issues    = issues_payload.get("issues") or []
        elif isinstance(issues_payload, list):
            issues = issues_payload

        years = sorted({
            str(i.get("date_year"))
            for i in issues
            if isinstance(i, dict) and i.get("date_year")
        })
        year_range = ""
        if years:
            year_range = years[0] if len(years) == 1 else f"{years[0]} – {years[-1]}"

        _zxdb_add_row("Magazine:",   name)
        _zxdb_add_row("Publisher:",  str(publisher) if publisher else "")
        _zxdb_add_row("Type:",       str(mtype) if mtype else "")
        _zxdb_add_row("Language:",   str(language) if language else "")
        _zxdb_add_row("Country:",    str(country) if country else "")
        _zxdb_add_row("Issues:",     str(len(issues)) if issues else "0")
        _zxdb_add_row("Years:",      year_range, dim=True)

        if issues:
            preview = []
            for i in issues[:6]:
                if not isinstance(i, dict):
                    continue
                vol = i.get("volume")
                num = i.get("number")
                yr  = i.get("date_year")
                mo  = i.get("date_month")
                label = []
                if vol is not None: label.append(f"V{vol}")
                if num is not None: label.append(f"#{num}")
                if yr:              label.append(f"{yr}" + (f"/{mo:02d}" if isinstance(mo, int) else ""))
                preview.append(" ".join(label) if label else str(i.get("id", "")))
            _zxdb_add_row(
                "Preview:",
                ", ".join(preview) + (f" … (+{len(issues) - len(preview)})" if len(issues) > len(preview) else ""),
                dim=True,
            )

        # No file downloads for magazines (issues carry per-issue files we don't drill into here).
        host._zxdb_selected_downloads = []
        host.zxdb_download_button.setEnabled(False)

    def zxdb_populate_suggest_detail(entry: dict):
        """Render a /suggest/{term} row in the detail pane."""
        _zxdb_clear_detail_rows()
        _zxdb_add_row("Suggestion:", entry.get("title", ""))
        _zxdb_add_row("Type:",       entry.get("_suggest_type", "") or entry.get("machine", ""))
        label = entry.get("author", "")  # we stuffed labeltype here
        if label:
            _zxdb_add_row("Label:", label)
        eid = entry.get("_entry_id", "")
        if eid:
            _zxdb_add_row("Entry ID:", eid, dim=True)
        _zxdb_add_row(
            "Tip:",
            "Switch to Games and search for this title, or pick another suggestion.",
            dim=True,
        )
        host._zxdb_selected_downloads = []
        host.zxdb_download_button.setEnabled(False)

    # ---- Screenshot slideshow ----

    def zxdb_set_pixmap(pm: QPixmap):
        if pm is None or pm.isNull():
            host.zxdb_screenshot_label.setText("No preview")
            host.zxdb_screenshot_label.clear_image()
            return
        # The label keeps the original and re-fits it to its own size on
        # every resize, so the picture never stays stuck at the size it had
        # when first shown (the "first .scr doesn't get rescaled" symptom).
        host.zxdb_screenshot_label.set_image(pm)
        # If the fullscreen view is showing this pane's preview, refresh it too.
        if host._zxdb_stack.currentIndex() == 1:
            host._zxdb_fullscreen_pixmap = pm
            host.zxdb_fullscreen_label.set_image(pm)

    def zxdb_update_nav_buttons():
        multi = len(host._zxdb_screenshots) > 1
        host.zxdb_prev_shot_btn.setVisible(multi)
        host.zxdb_next_shot_btn.setVisible(multi)
        host.zxdb_fs_prev_btn.setVisible(multi and host._zxdb_stack.currentIndex() == 1)
        host.zxdb_fs_next_btn.setVisible(multi and host._zxdb_stack.currentIndex() == 1)

    def zxdb_show_shot_at(idx: int):
        if not host._zxdb_screenshots:
            return
        idx = idx % len(host._zxdb_screenshots)
        host._zxdb_shot_index = idx
        zxdb_update_nav_buttons()
        url = host._zxdb_screenshots[idx]["url"]
        cached = host._zxdb_shot_cache.get(url)
        if cached is not None:
            zxdb_set_pixmap(cached)
            return

        token = host._zxdb_shot_token

        def _fn():
            return zxdb_fetch_bytes(url)

        def _on_ok(data):
            if token != host._zxdb_shot_token:
                return  # selection changed; drop result
            pm = QPixmap()
            if pm.loadFromData(data) and not pm.isNull():
                host._zxdb_shot_cache[url] = pm
                if host._zxdb_screenshots and host._zxdb_screenshots[host._zxdb_shot_index]["url"] == url:
                    zxdb_set_pixmap(pm)

        def _on_err(_err):
            pass

        getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxdb_slideshow_tick():
        if len(host._zxdb_screenshots) <= 1:
            return
        zxdb_show_shot_at(host._zxdb_shot_index + 1)

    host._zxdb_slideshow_timer.timeout.connect(zxdb_slideshow_tick)

    def _zxdb_shot_dwell_elapsed():
        # The 60s pause after a ◀/< press is over: if still on the same
        # screenshot set, advance and resume the normal 5s cadence.
        if (host._zxdb_dwell_token == host._zxdb_shot_token
                and len(host._zxdb_screenshots) > 1):
            zxdb_show_shot_at(host._zxdb_shot_index + 1)
            host._zxdb_slideshow_timer.start()

    host._zxdb_shot_dwell_timer.timeout.connect(_zxdb_shot_dwell_elapsed)

    def _zxdb_nav_prev():
        if len(host._zxdb_screenshots) > 1:
            host._zxdb_slideshow_timer.stop()
            host._zxdb_shot_dwell_timer.stop()
            zxdb_show_shot_at(host._zxdb_shot_index - 1)
            # Dwell 60s on the image the user stepped back to, then resume.
            host._zxdb_dwell_token = host._zxdb_shot_token
            host._zxdb_shot_dwell_timer.start(60000)

    def _zxdb_nav_next():
        if len(host._zxdb_screenshots) > 1:
            host._zxdb_slideshow_timer.stop()
            host._zxdb_shot_dwell_timer.stop()
            zxdb_show_shot_at(host._zxdb_shot_index + 1)
            host._zxdb_slideshow_timer.start()

    host.zxdb_prev_shot_btn.clicked.connect(_zxdb_nav_prev)
    host.zxdb_next_shot_btn.clicked.connect(_zxdb_nav_next)

    def zxdb_start_slideshow(screenshots):
        host._zxdb_slideshow_timer.stop()
        host._zxdb_shot_token += 1
        host._zxdb_screenshots = list(screenshots or [])
        host._zxdb_shot_cache  = {}
        host._zxdb_shot_index  = 0
        if not host._zxdb_screenshots:
            host.zxdb_screenshot_label.setText("No preview")
            host.zxdb_screenshot_label.clear_image()
            zxdb_update_nav_buttons()
            return
        zxdb_show_shot_at(0)
        if len(host._zxdb_screenshots) > 1:
            host._zxdb_slideshow_timer.start()

    # ---- Search task ----

    def zxdb_current_mode():
        return host.zxdb_mode_combo.currentData() or "games"

    def zxdb_set_busy(busy: bool):
        host._zxdb_search_loading = busy
        host.zxdb_search_button.setEnabled(not busy)
        host.zxdb_random_button.setEnabled(not busy and zxdb_current_mode() == "games")
        host.zxdb_latest_button.setEnabled(not busy and zxdb_current_mode() == "games")
        host.zxdb_mode_combo.setEnabled(not busy)
        host.zxdb_letter_combo.setEnabled(not busy)

    def _zxdb_extract_es_hits(payload):
        """Return the array of hits from an Elasticsearch-style or flat payload."""
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        h = payload.get("hits")
        if isinstance(h, dict) and isinstance(h.get("hits"), list):
            return h["hits"]
        if isinstance(h, list):
            return h
        for k in ("items", "results"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
        return []

    def _zxdb_extract_es_total(payload):
        if isinstance(payload, dict):
            h = payload.get("hits")
            if isinstance(h, dict):
                t = h.get("total")
                if isinstance(t, dict):
                    return int(t.get("value") or 0)
                if isinstance(t, (int, float)):
                    return int(t)
            for k in ("hits_count", "total", "totalHits"):
                v = payload.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
        return 0

    def _zxdb_parse_magazine_list(payload):
        """Normalize /magazines/ response into the table's 6-column shape."""
        entries = []
        for it in _zxdb_extract_es_hits(payload):
            if not isinstance(it, dict):
                continue
            src = it.get("_source", it)
            name = src.get("name") or src.get("magazine") or src.get("title") or ""
            publisher = src.get("publisher") or ""
            if isinstance(publisher, list):
                publisher = ", ".join(
                    p.get("name", "") if isinstance(p, dict) else str(p)
                    for p in publisher
                )
            entries.append({
                "id":      str(it.get("_id") or src.get("id") or name),
                "title":   str(name),
                "year":    str(src.get("yearStart") or src.get("year") or ""),
                "author":  str(publisher),
                "machine": str(src.get("type") or "Magazine"),
                "genre":   str(src.get("language") or src.get("country") or ""),
                "_kind":   "magazine",
                "_source": src,
                "_name":   str(name),
            })
        return entries

    def _zxdb_parse_suggest_list(payload):
        """Normalize /suggest/{term} response into the table's 6-column shape."""
        entries = []
        if not isinstance(payload, list):
            return entries
        for it in payload:
            if not isinstance(it, dict):
                continue
            text  = it.get("text") or it.get("name") or ""
            stype = it.get("type") or it.get("_type") or ""
            eid   = it.get("entry_id") or ""
            src   = it.get("_source") if isinstance(it.get("_source"), dict) else {}
            if not eid and isinstance(src, dict):
                eid = src.get("id") or src.get("entry_id") or ""
            entries.append({
                "id":      str(eid or text),
                "title":   str(text),
                "year":    "",
                "author":  str(it.get("labeltype") or ""),
                "machine": str(stype),
                "genre":   "",
                "_kind":   "suggest",
                "_suggest_type": str(stype),
                "_entry_id":     str(eid),
                "_source": it,
            })
        return entries

    # Column header presets per result mode.
    _ZXDB_HEADERS = {
        "games":     ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"],
        "byletter":  ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"],
        "magazines": ["ID", "Magazine", "Year",  "Publisher",          "Type",    "Language / Country"],
        "author":    ["ID", "Title", "Year", "Author / Publisher", "Machine", "Genre"],
        "suggest":   ["ID", "Suggestion", "—", "Label", "Type", "—"],
    }

    def zxdb_run_search(query: str, page: int, on_complete=None):
        mode = zxdb_current_mode()

        if mode == "suggest" and not query:
            zxdb_set_status("Type a term to get suggestions.")
            return
        if mode == "author" and not query:
            zxdb_set_status("Type an author / publisher name to search.")
            return

        # Supersede any in-flight ZXDB request.
        host._zxdb_search_gen += 1
        _gen = host._zxdb_search_gen
        zxdb_set_busy(True)
        zxdb_set_status("Searching…")
        host._zxdb_last_query = query

        offset = max(0, (page - 1) * ZXDB_PAGE_SIZE)

        if mode == "games":
            params = {
                "size":   str(ZXDB_PAGE_SIZE),
                "offset": str(offset),
                "mode":   "compact",
                "sort":   "rel_desc",
                "contenttype": "SOFTWARE",
            }
            # v5 SPLITS these. A search TERM is a path segment -- as a
            # query parameter it is silently dropped and the unfiltered
            # index comes back with a 200. A bare browse (no term) still
            # uses the query string, where sort/contenttype/size/offset
            # are all honoured. /search/titles/ is the closest match to
            # v3's mode=tit; /search/ alone searches wider.
            if query:
                path = (f"/search/titles/{urllib.parse.quote(query)}"
                        f"?{urllib.parse.urlencode(params)}")
            else:
                path = f"/search?{urllib.parse.urlencode(params)}"

            def _fn():
                payload = zxdb_fetch_json(path)
                entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                for e in entries:
                    e["_kind"] = "game"
                return ("games", entries, total, page, total_pages)

        elif mode == "byletter":
            letter = host.zxdb_letter_combo.currentData() or "a"
            params = {
                "size":   str(ZXDB_PAGE_SIZE),
                "offset": str(offset),
                "mode":   "compact",
                "contenttype": "SOFTWARE",
            }
            path = f"/entries/byletter/{urllib.parse.quote(letter)}?{urllib.parse.urlencode(params)}"

            def _fn():
                payload = zxdb_fetch_json(path)
                entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                for e in entries:
                    e["_kind"] = "game"
                return ("byletter", entries, total, page, total_pages)

        elif mode == "magazines":
            if query:
                # Fetch a specific magazine by name
                mag_path = f"/magazines/{urllib.parse.quote(query)}"

                def _fn():
                    payload = zxdb_fetch_json(mag_path,
                                              base=ZXDB_MAGAZINES_BASE_URL)
                    # /magazines/{name} returns a single ES hit: {_id, _source, …}
                    # Wrap it so _zxdb_parse_magazine_list can handle it uniformly.
                    if isinstance(payload, dict) and "_source" in payload:
                        wrapped = {"hits": {"hits": [payload], "total": {"value": 1}}}
                    elif isinstance(payload, list):
                        wrapped = {"hits": {"hits": payload, "total": {"value": len(payload)}}}
                    else:
                        wrapped = payload
                    entries = _zxdb_parse_magazine_list(wrapped)
                    total = len(entries)
                    return ("magazines", entries, total, 1, 1)
            else:
                # List all magazines
                params = {
                    "size":   str(ZXDB_PAGE_SIZE),
                    "offset": str(offset),
                    "sort":   "name_asc",
                }
                list_path = f"/magazines/?{urllib.parse.urlencode(params)}"

                def _fn():
                    payload = zxdb_fetch_json(list_path,
                                              base=ZXDB_MAGAZINES_BASE_URL)
                    entries = _zxdb_parse_magazine_list(payload)
                    total = _zxdb_extract_es_total(payload) or len(entries)
                    total_pages = max(1, (total + ZXDB_PAGE_SIZE - 1) // ZXDB_PAGE_SIZE) if total else 1
                    return ("magazines", entries, total, page, total_pages)

        elif mode == "author":
            # ZXInfo v5 exposes both /entries/byauthor/{name} and
            # /entries/bypublisher/{name} (v3 spelled these
            # /authors/{name}/games and /publishers/{name}/games).
            # Many UI users type a publisher/label name (e.g. 'Ultimate'), so we
            # try authors first and fall back to publishers when authors yields
            # no hits — this matches the working URL the user supplied:
            #   /entries/bypublisher/{name}?mode=compact&...
            params = {
                "size":   str(ZXDB_PAGE_SIZE),
                "offset": str(offset),
                "mode":   "compact",
                "sort":   "rel_desc",
            }
            qs = urllib.parse.urlencode(params)
            qname = urllib.parse.quote(query)

            def _fn():
                used = "authors"
                payload = zxdb_fetch_json(f"/entries/byauthor/{qname}?{qs}")
                entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                if not entries:
                    used = "publishers"
                    payload = zxdb_fetch_json(f"/entries/bypublisher/{qname}?{qs}")
                    entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                for e in entries:
                    e["_kind"] = "game"
                    e["_source_endpoint"] = used
                return ("author", entries, total, page, total_pages)

        else:  # suggest
            path = f"/suggest/{urllib.parse.quote(query)}"

            def _fn():
                payload = zxdb_fetch_json(path)
                entries = _zxdb_parse_suggest_list(payload)
                return ("suggest", entries, len(entries), 1, 1)

        def _on_ok(data):
            if _gen != host._zxdb_search_gen:
                return  # superseded by a newer search
            kind, entries, total, pg, total_pages = data
            zxdb_populate_results(entries, pg, total_pages, kind)
            if kind == "magazines":
                zxdb_set_status(f"{len(entries)} magazine(s) shown  |  page {pg}/{total_pages}  |  {total} total")
            elif kind == "suggest":
                zxdb_set_status(f"{len(entries)} suggestion(s)")
            elif kind == "author":
                zxdb_set_status(f"{total} result(s) for '{query}'  |  page {pg}/{total_pages}")
            elif kind == "byletter":
                letter_lbl = host.zxdb_letter_combo.currentText()
                zxdb_set_status(f"{total} result(s) for '{letter_lbl}'  |  page {pg}/{total_pages}")
            else:
                zxdb_set_status(f"{total} result(s)  |  page {pg}/{total_pages}")
            zxdb_set_busy(False)
            if on_complete:
                on_complete()

        def _on_err(err):
            if _gen != host._zxdb_search_gen:
                return  # superseded by a newer search
            exc = err[1]
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (502, 503, 504):
                zxdb_set_status(f"Server temporarily unavailable (HTTP {exc.code}) — please try again.")
            else:
                zxdb_set_status(f"Error: {exc}")
            zxdb_set_busy(False)
            if on_complete:
                on_complete()

        host._zxdb_search_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxdb_run_random(on_complete=None):
        # Supersede any in-flight ZXDB request.
        host._zxdb_search_gen += 1
        _gen = host._zxdb_search_gen
        host._zxdb_search_loading = True
        zxdb_set_status("Fetching random games…")
        host.zxdb_search_button.setEnabled(False)
        host.zxdb_random_button.setEnabled(False)
        host._zxdb_last_query = ""
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)

        def _fn():
            payload = zxdb_fetch_json(f"/entries/random/{ZXDB_PAGE_SIZE}")
            # /games/random returns an ES envelope: { hits: { hits: [...] } }
            entries = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                hits_outer = payload.get("hits", {})
                if isinstance(hits_outer, dict):
                    items = hits_outer.get("hits", [])
                elif isinstance(hits_outer, list):
                    items = hits_outer
                else:
                    items = payload.get("items", [])
            else:
                items = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                src = it.get("_source", it)
                eid = it.get("_id") or src.get("id") or src.get("entry_id") or ""
                author = ""
                for key in ("authors", "publishers"):
                    v = src.get(key)
                    if isinstance(v, list) and v:
                        names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in v]
                        author = ", ".join(n for n in names if n)
                        if author:
                            break
                entries.append({
                    "id":      str(eid),
                    "title":   str(zxdb_pick(src, "title", "fullTitle", "name")),
                    "year":    str(src.get("originalYearOfRelease") or src.get("yearOfRelease") or ""),
                    "author":  author,
                    "machine": str(zxdb_pick(src, "machineType", "machine_type", "machine")),
                    "genre":   str(zxdb_pick(src, "genreType", "genre", "genretype")),
                    "score":   "",
                    "_kind":   "game",
                })
            return entries

        def _on_ok(entries):
            if _gen != host._zxdb_search_gen:
                return  # superseded by a newer search
            host._zxdb_search_loading = False
            zxdb_populate_results(entries, 1, 1, "games")
            zxdb_set_status(f"{len(entries)} random game(s)")
            host.zxdb_search_button.setEnabled(True)
            host.zxdb_random_button.setEnabled(True)
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, host.zxdb_results_table.rowCount())
            if on_complete:
                on_complete()

        def _on_err(err):
            if _gen != host._zxdb_search_gen:
                return  # superseded by a newer search
            host._zxdb_search_loading = False
            exc = err[1]
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (502, 503, 504):
                zxdb_set_status(f"Server temporarily unavailable (HTTP {exc.code}) — please try again.")
            else:
                zxdb_set_status(f"Error: {exc}")
            host.zxdb_search_button.setEnabled(True)
            host.zxdb_random_button.setEnabled(True)
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, host.zxdb_results_table.rowCount())
            if on_complete:
                on_complete()

        host._zxdb_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxdb_run_latest(on_complete=None):
        # Supersede any in-flight ZXDB request.
        host._zxdb_search_gen += 1
        _gen = host._zxdb_search_gen
        host._zxdb_search_loading = True
        zxdb_set_status("Fetching latest games…")
        host.zxdb_search_button.setEnabled(False)
        host.zxdb_random_button.setEnabled(False)
        host.zxdb_latest_button.setEnabled(False)
        host._zxdb_last_query = ""
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)

        params = {
            "size":   str(ZXDB_PAGE_SIZE),
            "offset": "0",
            "mode":   "compact",
            "sort":   "date_desc",
            "contenttype": "SOFTWARE",
        }
        path = f"/search?{urllib.parse.urlencode(params)}"

        def _fn():
            payload = zxdb_fetch_json(path)
            entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
            for e in entries:
                e["_kind"] = "game"
            return (entries, total, total_pages)

        def _on_ok(data):
            if _gen != host._zxdb_search_gen:
                return  # superseded by a newer search
            entries, total, total_pages = data
            host._zxdb_search_loading = False
            zxdb_populate_results(entries, 1, total_pages or 1, "games")
            zxdb_set_status(f"{len(entries)} latest game(s)")
            host.zxdb_search_button.setEnabled(True)
            host.zxdb_random_button.setEnabled(zxdb_current_mode() == "games")
            host.zxdb_latest_button.setEnabled(zxdb_current_mode() == "games")
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, host.zxdb_results_table.rowCount())
            if on_complete:
                on_complete()

        def _on_err(err):
            if _gen != host._zxdb_search_gen:
                return  # superseded by a newer search
            host._zxdb_search_loading = False
            zxdb_set_status(f"Error: {err[1]}")
            host.zxdb_search_button.setEnabled(True)
            host.zxdb_random_button.setEnabled(zxdb_current_mode() == "games")
            host.zxdb_latest_button.setEnabled(zxdb_current_mode() == "games")
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, host.zxdb_results_table.rowCount())
            if on_complete:
                on_complete()

        host._zxdb_latest_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxdb_on_search():
        zxdb_clear_detail()
        q = host.zxdb_search_input.text().strip()
        save_configuration_file()
        if q and len(q) < SEARCH_MIN_CHARS:
            return
        # Suppress the autocomplete suggestions popup once a search is
        # submitted; it stays hidden until the user types again.
        host._zxdb_ac_block = True
        try:
            _zxdb_ac_timer.stop()
        except Exception:
            pass
        try:
            host._zxdb_completer.popup().hide()
        except Exception:
            pass
        if q:
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            def _zxdb_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, host.zxdb_results_table.rowCount())
            zxdb_run_search(q, 1, _zxdb_done)
        else:
            zxdb_run_search(q, 1)
        if _multi_search_enabled() and q:
            host.getit_search_input.setText(q)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                host.zxart_search_input.setText(q)
            _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            _cross_search_getit(q)
            _cross_search_zxart(q)

    def zxdb_on_random(on_complete=None):
        zxdb_clear_detail()
        host.zxdb_search_input.clear()
        zxdb_run_random(on_complete)

    def zxdb_on_latest(on_complete=None):
        zxdb_clear_detail()
        host.zxdb_search_input.clear()
        # Force the mode to 'games' so the latest list is meaningful.
        for i in range(host.zxdb_mode_combo.count()):
            if host.zxdb_mode_combo.itemData(i) == "games":
                if host.zxdb_mode_combo.currentIndex() != i:
                    host.zxdb_mode_combo.setCurrentIndex(i)
                break
        zxdb_run_latest(on_complete)

    def zxdb_on_prev():
        zxdb_run_search(host._zxdb_last_query, max(1, host._zxdb_current_page - 1))

    def zxdb_on_next():
        zxdb_run_search(host._zxdb_last_query, min(host._zxdb_total_pages, host._zxdb_current_page + 1))

    host.zxdb_search_button.clicked.connect(zxdb_on_search)
    host.zxdb_random_button.clicked.connect(zxdb_on_random)
    host.zxdb_latest_button.clicked.connect(zxdb_on_latest)
    host.zxdb_search_input.returnPressed.connect(zxdb_on_search)
    host.zxdb_prev_button.clicked.connect(zxdb_on_prev)
    host.zxdb_next_button.clicked.connect(zxdb_on_next)

    def _zxdb_search_validate(text: str):
        t = text.strip()
        if not t:
            host._zxdb_search_valid_lbl.setVisible(False)
        elif len(t) < SEARCH_MIN_CHARS:
            host._zxdb_search_valid_lbl.setText('<font color="red">❌</font>')
            host._zxdb_search_valid_lbl.setToolTip(f"Searches must be {SEARCH_MIN_CHARS} characters long")
            host._zxdb_search_valid_lbl.setVisible(True)
        else:
            host._zxdb_search_valid_lbl.setText('<font color="green">✔</font>')
            host._zxdb_search_valid_lbl.setVisible(True)
    host.zxdb_search_input.textChanged.connect(_zxdb_search_validate)

    # ---- ZXDB autocomplete ----

    host._zxdb_ac_ready = False  # suppressed until after startup
    host._zxdb_ac_model = QStringListModel(host)
    _zxdb_completer = QCompleter(host._zxdb_ac_model, host)
    _zxdb_completer.setCompletionMode(QCompleter.PopupCompletion)
    _zxdb_completer.setCaseSensitivity(Qt.CaseInsensitive)
    _zxdb_completer.setFilterMode(Qt.MatchStartsWith)
    # Ensure the popup follows the main window on Windows
    popup = _zxdb_completer.popup()
    if popup is not None:
        popup.setParent(host)
        # Non-grabbing tool window (NOT Qt.Popup) so the auto-shown completer
        # popup never performs the implicit Windows mouse/keyboard grab that
        # can get stuck and leave the search box unclickable.
        popup.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                             | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus)
        popup.setFocusPolicy(Qt.NoFocus)
        popup.setAttribute(Qt.WA_ShowWithoutActivating)
    host._zxdb_completer = _zxdb_completer
    host.zxdb_search_input.setCompleter(_zxdb_completer)
    host._zxdb_popup_hider = _CompleterPopupHider(
        host.zxdb_search_input, _zxdb_completer, host)

    _zxdb_ac_timer = QTimer(host)
    _zxdb_ac_timer.setSingleShot(True)
    _zxdb_ac_timer.setInterval(300)

    def _zxdb_safe_show_popup(q: str):
        """Show the ZXDB completer popup without calling QCompleter.complete(),
        which has crashed Qt with a native access violation on Windows."""
        try:
            if not host._search_autocomplete_on():
                return
            if getattr(host, "_zxdb_ac_block", False):
                return
            if not host.zxdb_search_input.hasFocus():
                return
            if host.zxdb_search_input.text().strip() != q:
                return
            if host._zxdb_ac_model.rowCount() == 0:
                return
            _zxdb_completer.setCompletionPrefix(q)
            popup = _zxdb_completer.popup()
            if popup is None:
                return
            try:
                popup.setParent(host.zxdb_search_input.window(),
                                Qt.Tool
                                | Qt.FramelessWindowHint
                                | Qt.WindowStaysOnTopHint
                                | Qt.WindowDoesNotAcceptFocus)
                popup.setFocusPolicy(Qt.NoFocus)
                popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
            except Exception:
                pass
            le = host.zxdb_search_input
            rect = le.rect()
            pos = le.mapToGlobal(rect.bottomLeft())
            popup.setMinimumWidth(le.width())
            popup.move(pos)
            popup.resize(le.width(), _popup_height_for(popup, host._zxdb_ac_model.rowCount()))
            popup.show()
        except RuntimeError:
            pass
        except Exception:
            pass

    def _zxdb_ac_update_model(text: str):
        """Filter the cached per-letter titles to those starting with
        *text* off the UI thread, then update the completer model."""
        if not text:
            host._zxdb_ac_model.setStringList([])
            return
        letter = text[0].lower()
        cached_snapshot = list(host._zxdb_ac_cache.get(letter, []))
        host._zxdb_ac_filter_gen = getattr(host, "_zxdb_ac_filter_gen", 0) + 1
        gen = host._zxdb_ac_filter_gen
        tl = text.lower()

        def _fn():
            matches = [t for t in cached_snapshot if t.lower().startswith(tl)]
            return (gen, text, matches[:80])

        def _on_ok(result):
            rgen, rtext, matches = result
            if rgen != getattr(host, "_zxdb_ac_filter_gen", -1):
                return
            try:
                if host.zxdb_search_input.text().strip() != rtext:
                    return
            except RuntimeError:
                return
            host._zxdb_ac_model.setStringList(matches)
            if matches:
                QTimer.singleShot(0, lambda q=rtext: _zxdb_safe_show_popup(q))

        def _on_err(_err):
            pass

        getit_run_in_thread(_fn, _on_ok, _on_err)

    def _zxdb_ac_fetch_letter(letter: str):
        """Fetch all titles for *letter* via /games/byletter, cache, then refresh model."""
        if letter in host._zxdb_ac_fetching:
            return
        host._zxdb_ac_fetching.add(letter)
        host._ac_anim_start(host.zxdb_search_input)

        def _fn():
            titles = []
            offset = 0
            fetch_size = 200
            total = None
            while True:
                params = {
                    "size":        str(fetch_size),
                    "offset":      str(offset),
                    "mode":        "compact",
                    "contenttype": "SOFTWARE",
                }
                path = f"/entries/byletter/{urllib.parse.quote(letter)}?{urllib.parse.urlencode(params)}"
                try:
                    payload = zxdb_fetch_json(path)
                    entries, page_total, _pg, _tp, _ps = zxdb_parse_search(payload)
                except Exception:
                    break
                if not entries:
                    break
                titles.extend(e["title"] for e in entries if e.get("title"))
                # ZXInfo may cap the effective page size below the value
                # we asked for, so do not exit just because we received
                # fewer rows than requested.  Drive pagination from the
                # server-reported total instead and stop only once we
                # have walked the whole letter (or the API stops
                # returning new rows).
                if total is None and page_total:
                    total = page_total
                offset += len(entries)
                if total is not None and offset >= total:
                    break
                # Safety net: if the API keeps returning the same rows
                # without advancing, bail out.
                if total is None and len(entries) < 10:
                    break
            return (letter, sorted(set(titles), key=str.lower))

        def _on_ok(result):
            ltr, sorted_titles = result
            host._zxdb_ac_fetching.discard(ltr)
            host._zxdb_ac_cache[ltr] = sorted_titles
            host._ac_anim_stop(host.zxdb_search_input)
            # Refresh model if the user is still on this prefix.
            _zxdb_ac_update_model(host.zxdb_search_input.text().strip())
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("zxdb", ltr)
                except Exception:
                    pass

        def _on_err(_err):
            host._zxdb_ac_fetching.discard(letter)
            host._ac_anim_stop(host.zxdb_search_input)
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("zxdb", letter)
                except Exception:
                    pass

        host._zxdb_ac_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    # Expose the per-letter fetcher so the AllInOne pane can prime the
    # ZXDB cache for cross-source autocomplete suggestions.
    host._zxdb_ac_fetch_letter = _zxdb_ac_fetch_letter

    def _zxdb_ac_trigger():
        if not _search_autocomplete_on():
            host._zxdb_ac_model.setStringList([])
            return
        mode = zxdb_current_mode()
        if mode not in ("games", "byletter", "author"):
            host._zxdb_ac_model.setStringList([])
            return
        text = host.zxdb_search_input.text().strip()
        if not text:
            host._zxdb_ac_model.setStringList([])
            return
        letter = text[0].lower()
        if letter in host._zxdb_ac_cache:
            _zxdb_ac_update_model(text)
        else:
            _zxdb_ac_fetch_letter(letter)

    def _zxdb_ac_on_text_changed(_text: str):
        if getattr(host, "_zxdb_ac_suppress", False):
            host._zxdb_ac_suppress = False
            return
        # The user is typing again: re-enable autocomplete suggestions
        # that were suppressed after the last search submission.
        host._zxdb_ac_block = False
        _zxdb_ac_timer.start()

    _zxdb_ac_timer.timeout.connect(_zxdb_ac_trigger)
    host.zxdb_search_input.textChanged.connect(_zxdb_ac_on_text_changed)

    def _zxdb_ac_activated(selected: str):
        try:
            if selected:
                host._zxdb_ac_suppress = True
                _zxdb_ac_timer.stop()
                try:
                    _zxdb_completer.popup().hide()
                except Exception:
                    pass
                host.zxdb_search_input.setText(selected)
        except Exception:
            pass
        zxdb_on_search()

    _zxdb_completer.activated.connect(_zxdb_ac_activated)

    def zxdb_on_mode_changed(_idx):
        mode = zxdb_current_mode()
        placeholders = {
            "games":     "Search ZXDB games... (leave empty for random selection)",
            "byletter":  "(pick a letter from the list →)",
            "magazines": "Filter magazines... (leave empty to list all)",
            "author":    "Type an author name (e.g. 'Matthew Smith')",
            "suggest":   "Type a term to get suggestions",
        }
        host.zxdb_search_input.setPlaceholderText(placeholders.get(mode, ""))
        host.zxdb_search_input.setVisible(mode != "byletter")
        host.zxdb_letter_combo.setVisible(mode == "byletter")
        host.zxdb_random_button.setEnabled(mode == "games")
        host.zxdb_latest_button.setEnabled(mode == "games")
        # Reset paging/results when switching modes.
        host._zxdb_last_query = ""
        host._zxdb_current_page = 1
        host._zxdb_total_pages = 1
        host.zxdb_page_label.setText("1")
        host.zxdb_prev_button.setEnabled(False)
        host.zxdb_next_button.setEnabled(False)
        host.zxdb_results_table.setRowCount(0)
        zxdb_clear_detail()
        zxdb_set_status("")
        configuration_dictionary[SETTING_ZXDB_LAST_MODE] = mode
        save_configuration_file()

    host.zxdb_mode_combo.currentIndexChanged.connect(zxdb_on_mode_changed)

    def zxdb_on_letter_changed(_idx):
        if zxdb_current_mode() == "byletter":
            zxdb_clear_detail()
            zxdb_run_search("", 1)

    host.zxdb_letter_combo.currentIndexChanged.connect(zxdb_on_letter_changed)

    # ---- Row selection -> fetch detail + screenshot ----

    def _zxdb_reset_preview():
        host._zxdb_slideshow_timer.stop()
        host._zxdb_shot_token += 1
        host._zxdb_screenshots = []
        host._zxdb_shot_cache  = {}
        host._zxdb_shot_index  = 0
        host.zxdb_screenshot_label.clear_image()

    def _zxdb_load_game(eid: str, title_hint: str):
        host._zxdb_selected_id    = eid
        host._zxdb_selected_title = title_hint or eid
        zxdb_set_status(f"Loading {eid}…")
        host.zxdb_screenshot_label.setText("Loading…")
        _zxdb_reset_preview()

        def _fn():
            payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
            return zxdb_parse_game_detail(payload)

        def _on_ok(detail):
            if host._zxdb_selected_id != eid:
                return
            zxdb_populate_detail(detail)
            shots = detail.get("screenshots") or []
            if not shots and detail.get("screenshot_url"):
                shots = [{"url": detail["screenshot_url"], "type": ""}]
            zxdb_start_slideshow(shots)
            n = len(shots)
            title = detail.get("title", eid)
            if n > 1:
                zxdb_set_status(f"Loaded {title}  |  {n} screenshots (cycling every 5s)")
            else:
                zxdb_set_status(f"Loaded {title}")

        def _on_err(err):
            if host._zxdb_selected_id != eid:
                return
            zxdb_set_status(f"Detail error: {err[1]}")
            host.zxdb_screenshot_label.setText("No preview")

        host._zxdb_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def _zxdb_load_magazine(entry: dict):
        name = entry.get("_name") or entry.get("title") or ""
        host._zxdb_selected_id    = entry.get("id") or name
        host._zxdb_selected_title = name
        host._zxdb_magazine_issues = []
        zxdb_set_status(f"Loading magazine '{name}'…")
        host.zxdb_screenshot_label.setText("Loading…")
        _zxdb_reset_preview()

        def _fn():
            # /magazines/{name} returns a single ES hit whose _source contains
            # the full issues array (with id, files, cover_image per issue).
            return zxdb_fetch_json(f"/magazines/{urllib.parse.quote(name)}",
                                   base=ZXDB_MAGAZINES_BASE_URL)

        def _on_ok(payload):
            if host._zxdb_selected_title != name:
                return
            src = {}
            if isinstance(payload, dict) and "_source" in payload:
                src = payload["_source"]
            elif isinstance(payload, dict):
                src = payload
            issues = src.get("issues") or []
            host._zxdb_magazine_issues = issues
            # Build summary dict for the detail panel
            summary = {
                "publisher": src.get("publisher") or "",
                "type":      src.get("type") or "",
                "language":  src.get("language") or "",
                "country":   src.get("country") or "",
            }
            # Wrap issues as a payload that zxdb_populate_magazine_detail understands
            issues_payload = {"issues": issues}
            zxdb_populate_magazine_detail(name, summary, issues_payload)
            # Build a slideshow from issue cover_images
            shots = []
            seen = set()
            for i in issues:
                if not isinstance(i, dict):
                    continue
                cov = i.get("cover_image") or ""
                if not cov:
                    continue
                url = cov if cov.startswith("http") else "https://spectrumcomputing.co.uk" + cov
                if url in seen:
                    continue
                seen.add(url)
                label = []
                if i.get("volume")     is not None: label.append(f"V{i['volume']}")
                if i.get("number")     is not None: label.append(f"#{i['number']}")
                if i.get("date_year"): label.append(str(i["date_year"]))
                shots.append({"url": url, "type": " ".join(label) or "Cover"})
            zxdb_start_slideshow(shots)
            n_shots  = len(shots)
            n_issues = len(issues)
            hint = "  (double-click or right-click → Retrieve all issues)" if n_issues > 1 else ""
            if n_shots > 1:
                zxdb_set_status(f"Loaded {name}  |  {n_issues} issue(s), {n_shots} cover(s) cycling every 5s{hint}")
            else:
                zxdb_set_status(f"Loaded {name}  |  {n_issues} issue(s){hint}")

        def _on_err(err):
            if host._zxdb_selected_title != name:
                return
            zxdb_set_status(f"Magazine error: {err[1]}")
            host.zxdb_screenshot_label.setText("No preview")

        host._zxdb_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def _zxdb_open_issues_dialog(mag_name: str, issues: list):
        """Show a dialog listing all issues for a magazine.
        Selecting a row loads its files/preview; right-click offers Download content."""
        if not issues:
            zxdb_set_status(f"No issues available for '{mag_name}'.")
            return

        dlg = QDialog(host)
        dlg.setWindowTitle(f"All issues — {mag_name}  ({len(issues)} issues)")
        dlg.resize(860, 500)
        v = QVBoxLayout(dlg)

        tbl = QTableWidget(len(issues), 5, dlg)
        tbl.setHorizontalHeaderLabels(["Issue #", "Volume", "Year", "Month", "Files"])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setColumnWidth(0, 80)
        tbl.setColumnWidth(1, 80)
        tbl.setColumnWidth(2, 80)
        tbl.setColumnWidth(3, 80)

        for row, iss in enumerate(issues):
            if not isinstance(iss, dict):
                continue
            tbl.setItem(row, 0, QTableWidgetItem(str(iss.get("number") or "")))
            tbl.setItem(row, 1, QTableWidgetItem(str(iss.get("volume") or "")))
            tbl.setItem(row, 2, QTableWidgetItem(str(iss.get("date_year") or "")))
            tbl.setItem(row, 3, QTableWidgetItem(str(iss.get("date_month") or "")))
            files = iss.get("files") or []
            tbl.setItem(row, 4, QTableWidgetItem(str(len(files))))
            # Stash the full issue dict
            tbl.item(row, 0).setData(Qt.UserRole, iss)

        def _load_issue_from_row(row: int):
            """Load files/preview from the already-fetched issue data."""
            id_cell = tbl.item(row, 0)
            if not id_cell:
                return
            iss = id_cell.data(Qt.UserRole)
            if not isinstance(iss, dict):
                return
            issue_num = iss.get("number") or iss.get("id") or ""
            issue_id_api = str(iss.get("id") or issue_num)
            downloads = []
            shots = []
            for f in (iss.get("files") or []):
                if not isinstance(f, dict):
                    continue
                link = f.get("file_link") or ""
                if not link:
                    continue
                url = link if link.startswith("http") else "https://spectrumcomputing.co.uk" + link
                ftype = f.get("filetype") or ""
                fname = f.get("filename") or os.path.basename(urllib.parse.urlparse(url).path) or ""
                downloads.append({
                    "url":      url,
                    "filename": fname,
                    "type":     ftype,
                    "format":   ftype,
                    "size":     f.get("file_size"),
                    "source":   f.get("comments") or "",
                })
                if url.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                    shots.append({"url": url, "type": ftype})
            # Update main panel
            _zxdb_clear_detail_rows()
            _zxdb_add_row("Magazine:", mag_name)
            _zxdb_add_row("Issue #:",  str(issue_num))
            for key, lbl in (
                ("date_year",  "Year"),
                ("date_month", "Month"),
                ("volume",     "Volume"),
            ):
                v2 = iss.get(key)
                if v2 is not None:
                    _zxdb_add_row(f"{lbl}:", str(v2))
            host._zxdb_selected_downloads = downloads
            host._zxdb_selected_title = f"{mag_name} #{issue_num}"
            host._zxdb_selected_id = f"{mag_name}:{issue_id_api}"
            host.zxdb_download_button.setEnabled(bool(downloads))
            if shots:
                zxdb_start_slideshow(shots)
            else:
                host.zxdb_screenshot_label.setText("No image files")
            n_files = len(downloads)
            zxdb_set_status(
                f"Issue #{issue_num} of '{mag_name}'"
                + (f"  |  {n_files} file(s)" if n_files else "  |  no files")
            )

        def _on_issue_selected():
            sel = tbl.selectionModel().selectedRows()
            if sel:
                _load_issue_from_row(sel[0].row())

        def _on_issue_double_clicked(item):
            _load_issue_from_row(tbl.row(item))

        def _on_issue_context_menu(pos):
            item = tbl.itemAt(pos)
            if item is None:
                return
            row = tbl.row(item)
            tbl.selectRow(row)
            _load_issue_from_row(row)
            menu2 = QMenu(tbl)
            act_dl = menu2.addAction(ui_tr_now("Download content"))
            act_dl.setEnabled(bool(host._zxdb_selected_downloads))
            action = menu2.exec(tbl.viewport().mapToGlobal(pos))
            if action is act_dl:
                zxdb_show_downloads_overlay(
                    host._zxdb_selected_title,
                    host._zxdb_selected_downloads,
                )

        tbl.itemSelectionChanged.connect(_on_issue_selected)
        tbl.itemDoubleClicked.connect(_on_issue_double_clicked)
        tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        tbl.customContextMenuRequested.connect(_on_issue_context_menu)

        v.addWidget(tbl, 1)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        brow = QHBoxLayout()
        brow.addStretch()
        brow.addWidget(btn_close)
        v.addLayout(brow)

        dlg.exec()

    def _zxdb_load_suggest(entry: dict):
        stype = (entry.get("_suggest_type") or "").upper()
        eid   = entry.get("_entry_id") or ""
        # If the suggestion points at a SOFTWARE entry, drill straight into it.
        if stype == "SOFTWARE" and eid:
            _zxdb_load_game(eid, entry.get("title", ""))
            return
        # Otherwise just show the suggestion details.
        _zxdb_reset_preview()
        host._zxdb_selected_id    = entry.get("id") or ""
        host._zxdb_selected_title = entry.get("title", "")
        host.zxdb_screenshot_label.setText("No preview")
        zxdb_populate_suggest_detail(entry)
        zxdb_set_status(f"Suggestion: {entry.get('title', '')}  ({stype or 'unknown'})")

    def zxdb_on_row_selected():
        sel = host.zxdb_results_table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        id_item    = host.zxdb_results_table.item(row, 0)
        title_item = host.zxdb_results_table.item(row, 1)
        if not id_item:
            return
        entry = id_item.data(Qt.UserRole) or {}
        kind = (entry.get("_kind") or "game").lower()
        title_hint = title_item.text() if title_item else id_item.text()

        host.zxdb_download_button.setEnabled(False)

        if kind == "magazine":
            _zxdb_load_magazine(entry)
        elif kind == "suggest":
            _zxdb_load_suggest(entry)
        else:
            _zxdb_load_game(id_item.text(), title_hint)

    host.zxdb_results_table.itemSelectionChanged.connect(zxdb_on_row_selected)

    def zxdb_on_gallery_cell(entry):
        eid = entry.get("id") or ""
        if not eid:
            return
        for r in range(host.zxdb_results_table.rowCount()):
            item = host.zxdb_results_table.item(r, 0)
            if item is not None and item.text() == eid:
                host.zxdb_results_table.selectRow(r)
                break
        host.zxdb_gallery_view.select_entry(lambda _e, _e0=entry: _e is _e0)

    host.zxdb_gallery_view.cell_clicked.connect(zxdb_on_gallery_cell)

    def _zxdb_open_gallery_viewer(entry, make_viewer=None, install=True):
        eid   = entry.get("id") or ""
        title = entry.get("title") or eid
        if not eid:
            return None
        kind = (entry.get("_kind") or "game").lower()

        info_rows_base = [
            ("Title:",   title),
            ("Author:",  entry.get("author", "")),
            ("Year:",    str(entry.get("year", "") or "")),
            ("Machine:", entry.get("machine", "")),
            ("Genre:",   entry.get("genre", "")),
        ]
        _mk = make_viewer or (lambda **kw: GalleryItemViewer(
            parent=host, anim_mode_getter=lambda: host._gallery_anim_mode, **kw))
        viewer = _mk(
            title=title,
            info_rows=info_rows_base,
            screenshots=[],
            extra_fetch_cb=_zxdb_extra_fetch,
            tags=_gallery_extract_tags(entry),
        )
        if hasattr(viewer, "set_gif_fetch_cb"):
            viewer.set_gif_fetch_cb(_gif_fetch_bytes)
        _fav_entry_zxdb = {**entry, "_fav_source": "zxdb"}
        viewer.set_favorite_hooks(_fav_entry_zxdb, host._fav_is, host._fav_toggle)

        # ── action buttons ──────────────────────────────────────────
        _safe_title = zxdb_sanitize_folder(title)
        _img_path   = host.right_disk_image_path or ""
        _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                       ) if _img_path else ""
        _sd_dest    = f"{_img_path}  →  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base    = _zxdb_resolve_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest    = os.path.join(_ns_base, _safe_title)
        _sd_ok      = bool(host.right_disk_image_path) and bool(_right_disk_content())

        def _dl_btn():
            if kind == "magazine":
                if host._zxdb_selected_downloads:
                    zxdb_show_downloads_overlay(host._zxdb_selected_title or title,
                                                host._zxdb_selected_downloads)
                return
            if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                zxdb_show_downloads_overlay(host._zxdb_selected_title or title,
                                            host._zxdb_selected_downloads)
                return
            zxdb_set_status(f"Loading {eid}\u2026")
            def _fn():
                payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)
            def _on_ok(detail):
                zxdb_populate_detail(detail)
                dls = _filter_download_urls(detail.get("downloads", []) or [])
                viewer.set_download_available(bool(dls))
                if not dls:
                    zxdb_set_status("No downloadable files for this entry.")
                    return
                zxdb_show_downloads_overlay(detail.get("title") or title, dls)
            def _on_err(err):
                zxdb_set_status(f"Detail error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _sd_btn():
            if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                _zxdb_send_to_image(host._zxdb_selected_title or title,
                                    host._zxdb_selected_downloads)
                return
            zxdb_set_status(f"Loading {eid}\u2026")
            def _fn():
                payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)
            def _on_ok(detail):
                zxdb_populate_detail(detail)
                dls = _filter_download_urls(detail.get("downloads", []) or [])
                viewer.set_download_available(bool(dls))
                if not dls:
                    zxdb_set_status("No downloadable files for this entry.")
                    return
                _zxdb_send_to_image(detail.get("title") or title, dls)
            def _on_err(err):
                zxdb_set_status(f"Detail error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _ns_btn():
            if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                def _after(_f):
                    QTimer.singleShot(0, lambda _folder=_f: host._nextsync_start_server_fn(_folder))
                _zxdb_send_to_path(host._zxdb_selected_title or title,
                                   host._zxdb_selected_downloads, _ns_base, _after)
                return
            zxdb_set_status(f"Loading {eid}\u2026")
            def _fn():
                payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                return zxdb_parse_game_detail(payload)
            def _on_ok(detail):
                zxdb_populate_detail(detail)
                dls = _filter_download_urls(detail.get("downloads", []) or [])
                viewer.set_download_available(bool(dls))
                if not dls:
                    zxdb_set_status("No downloadable files for this entry.")
                    return
                def _after(_f):
                    QTimer.singleShot(0, lambda _folder=_f: host._nextsync_start_server_fn(_folder))
                _zxdb_send_to_path(detail.get("title") or title, dls, _ns_base, _after)
            def _on_err(err):
                zxdb_set_status(f"Detail error: {err[1]}")
            host._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        viewer.set_actions(
            download_cb=_dl_btn, send_sd_cb=_sd_btn, send_ns_cb=_ns_btn,
            sd_enabled=_sd_ok, sd_tooltip=_sd_dest,
            ns_enabled=True,   ns_tooltip=_ns_dest,
        )
        host._wire_viewer_emulators(
            viewer, allow=ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS)
        viewer.set_open_web_url(zxdb_entry_website_url(eid), "zxinfo.dk")
        # If downloads are disabled globally, hide all action buttons immediately.
        if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
            viewer.set_download_available(False)
        # If we already have cached (filtered) downloads, use them to set
        # initial button visibility; otherwise keep buttons visible until
        # the async enrich resolves.
        elif host._zxdb_selected_id == eid:
            viewer.set_download_available(
                bool(_filter_download_urls(host._zxdb_selected_downloads or []))
            )

        # ── async enrich (screenshots + full metadata) ──────────────
        def _fn():
            if kind == "magazine":
                return ("magazine", {}, [])
            payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
            detail  = zxdb_parse_game_detail(payload)
            shots   = detail.get("screenshots") or []
            if not shots and detail.get("screenshot_url"):
                shots = [{"url": detail["screenshot_url"], "type": ""}]
            return ("game", detail, shots)
        def _on_ok(res):
            kind2, detail, shots = res
            if kind2 == "magazine":
                return
            img_urls = [s.get("url") for s in shots
                        if isinstance(s, dict) and s.get("url")]
            if img_urls:
                viewer.set_screenshots(img_urls)
            else:
                _dls_tmp = _filter_download_urls(detail.get("downloads", []) or [])
                _ph_label, _ph_fname = zxfmt_pick_best_download(_dls_tmp)
                _ph_sub = _ph_fname or detail.get("title") or title
                viewer.set_placeholder(_ph_label, _ph_sub)
            # Surface readable text files (e.g. instructions) as Pygame
            # log-console pages after the pictures; the Qt viewer ignores
            # them. When there is none, fall back to the description.
            if not _gallery_add_text_pages(viewer, detail.get("text_files")):
                _gallery_add_description_page(
                    viewer, detail.get("description") or detail.get("remarks"))
            rows = [
                ("Title:",       detail.get("title", title)),
                ("Year:",        str(detail.get("year", "") or "")),
                ("Authors:",     detail.get("authors", "")),
                ("Published by:", detail.get("publishers", "")),
                ("Machine:",     detail.get("machine", "")),
                ("Genre:",       detail.get("genre", "")),
                ("Language:",    detail.get("language", "")),
                ("Description:", detail.get("description") or detail.get("remarks", "")),
            ]
            _gallery_viewer_refresh_meta(viewer, detail.get("title") or title, rows)
            dls = _filter_download_urls(detail.get("downloads", []) or [])
            if ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                viewer.set_download_available(bool(dls))
        def _on_err(_e): viewer.set_placeholder("FILE", title)
        host._zxdb_gallery_viewer_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        # ── push into pane stack ────────────────────────────────────
        if install:
            viewer.install_into_stack(
                host._zxdb_stack,
                close_fn=lambda: host._zxdb_stack.setCurrentIndex(0),
            )
        return viewer

    host.zxdb_gallery_view.cell_dbl_clicked.connect(
        lambda e: host._pane_open_item("zxdb", e, getattr(host, "_zxdb_item_retro", False)))

    def _zxdb_apply_view_mode(mode: str, *, persist: bool = True):
        mode = (mode or "table").lower()
        if mode not in ("table", "gallery"):
            mode = "table"
        host._zxdb_view_mode = mode
        host.zxdb_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
        if getattr(host, "_pane_retro_gallery_refresh", None):
            host._pane_retro_gallery_refresh("zxdb")
        _table = (mode == "table")
        if hasattr(host, '_zxdb_preview_container'):
            host._zxdb_preview_container.setVisible(_table)
        if hasattr(host, '_zxdb_preview_download_btn'):
            host._zxdb_preview_download_btn.setVisible(
                _table and ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS
            )
        cb = host.zxdb_view_combo
        target_idx = 1 if mode == "gallery" else 0
        if cb.currentIndex() != target_idx:
            cb.blockSignals(True)
            cb.setCurrentIndex(target_idx)
            cb.blockSignals(False)
        if persist:
            # sync other panes to the same view mode
            if hasattr(host, '_getit_apply_view_mode'):
                host._getit_apply_view_mode(mode, persist=False)
            if hasattr(host, '_zxart_apply_view_mode'):
                host._zxart_apply_view_mode(mode, persist=False)
            if hasattr(host, '_favorites_apply_view_mode'):
                host._favorites_apply_view_mode(mode, persist=False)
            if hasattr(host, '_allinone_apply_view_mode'):
                host._allinone_apply_view_mode(mode, persist=False)
            configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
            configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
            configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
            configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
            configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
            if hasattr(host, '_itchio_apply_view_mode'):
                host._itchio_apply_view_mode(mode, persist=False)
            configuration_dictionary[SETTING_ITCHIO_VIEW_MODE]    = mode
            save_configuration_file()

    host._zxdb_apply_view_mode = _zxdb_apply_view_mode

    def _on_zxdb_view_combo_changed(_idx):
        _zxdb_apply_view_mode(host.zxdb_view_combo.currentData() or "table")

    host.zxdb_view_combo.currentIndexChanged.connect(_on_zxdb_view_combo_changed)
    _zxdb_apply_view_mode(host._zxdb_view_mode, persist=False)

    def zxdb_on_row_double_clicked(item):
        row = host.zxdb_results_table.row(item)
        id_item = host.zxdb_results_table.item(row, 0)
        if not id_item:
            return
        entry = id_item.data(Qt.UserRole) or {}
        if (entry.get("_kind") or "").lower() == "magazine":
            mag_name = entry.get("_name") or entry.get("title") or ""
            if host._zxdb_magazine_issues and host._zxdb_selected_title == mag_name:
                _zxdb_open_issues_dialog(mag_name, host._zxdb_magazine_issues)
            else:
                # Issues not loaded yet — load then open dialog
                zxdb_set_status(f"Loading issues for '{mag_name}'…")
                def _fn_dbl():
                    payload = zxdb_fetch_json(f"/magazines/{urllib.parse.quote(mag_name)}",
                                              base=ZXDB_MAGAZINES_BASE_URL)
                    src = payload.get("_source", payload) if isinstance(payload, dict) else {}
                    return src.get("issues") or []
                def _on_ok_dbl(issues):
                    host._zxdb_magazine_issues = issues
                    _zxdb_open_issues_dialog(mag_name, issues)
                def _on_err_dbl(err):
                    zxdb_set_status(f"Error loading issues: {err[1]}")
                host._zxdb_detail_thread = getit_run_in_thread(_fn_dbl, _on_ok_dbl, _on_err_dbl)
        else:
            host._pane_open_item("zxdb", entry, getattr(host, "_zxdb_item_retro", False))

    host.zxdb_results_table.itemDoubleClicked.connect(zxdb_on_row_double_clicked)

    # ---- Download ----

    def zxdb_pick_default_download():
        """Choose a sensible default file from the current detail's downloads."""
        if not host._zxdb_selected_downloads:
            return None
        preferred_ext = (".tap", ".tzx", ".z80", ".sna", ".trd", ".dsk", ".scl")
        for d in host._zxdb_selected_downloads:
            u = (d.get("url") or "").lower()
            if u.endswith(preferred_ext):
                return d
        return host._zxdb_selected_downloads[0]

    def zxdb_do_download(d: dict):
        url = d.get("url", "")
        if not url:
            return
        base = os.path.basename(urllib.parse.urlparse(url).path) or f"{host._zxdb_selected_id}.bin"
        save_path, _ = QFileDialog.getSaveFileName(None, "Save file", base)
        if not save_path:
            return
        zxdb_set_status(f"Downloading {base}…")
        host.zxdb_download_button.setEnabled(False)

        def _fn():
            data = zxdb_fetch_bytes(url, timeout=60)
            with open(save_path, "wb") as f:
                f.write(data)
            return save_path

        def _on_ok(p):
            zxdb_set_status(f"Saved to {p}  ↗ open folder", open_path=os.path.abspath(p))
            host.zxdb_download_button.setEnabled(bool(host._zxdb_selected_downloads))

        def _on_err(err):
            zxdb_set_status(f"Download error: {err[1]}")
            host.zxdb_download_button.setEnabled(bool(host._zxdb_selected_downloads))

        host._zxdb_dl_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxdb_on_download_clicked():
        d = zxdb_pick_default_download()
        if d:
            zxdb_do_download(d)

    host.zxdb_download_button.clicked.connect(zxdb_on_download_clicked)

    # ---- Context menu on results table ----

    def zxdb_sanitize_folder(name: str) -> str:
        n = (name or "").strip().lower()
        # Strip illegal Windows path chars
        for ch in '<>:"/\\|?*':
            n = n.replace(ch, "")
        # Collapse whitespace to single space, then dashes/spaces collapsed
        n = " ".join(n.split())
        return n or "untitled"

    def zxdb_human_size(n) -> str:
        try:
            n = int(n)
        except (TypeError, ValueError):
            return str(n) if n else ""
        if n <= 0:
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def zxdb_download_to_path(url: str, save_path: str, on_done=None, on_err=None):
        def _fn():
            data = zxdb_fetch_bytes(url, timeout=60)
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
            return save_path
        def _ok(p):
            if on_done: on_done(p)
        def _err(e):
            if on_err: on_err(e)
        return getit_run_in_thread(_fn, _ok, _err)

    def _zxdb_resolve_base_path(configured_path: str) -> str:
        """Return the configured path if it's a valid directory, else fall back to app-local 'downloads'."""
        p = (configured_path or "").strip().rstrip("/\\")
        if p and os.path.isdir(p):
            return p
        return os.path.join(ZXNU_DATA_ROOT, DOWNLOADS_ROOT_DIRNAME)

    def _zxdb_send_to_path(title: str, downloads: list, dest_root: str, post_action=None):
        """Download all files in *downloads* into dest_root/{sanitized_title}/, then call post_action(folder)."""
        if not downloads:
            zxdb_set_status("No downloadable files for this entry.")
            return
        folder = os.path.join(dest_root, zxdb_sanitize_folder(title))
        os.makedirs(folder, exist_ok=True)
        pending = {"n": len(downloads), "ok": 0, "ko": 0}

        def _maybe_finish():
            if pending["ok"] + pending["ko"] >= pending["n"]:
                if pending["ok"] > 0:
                    zxdb_set_status(
                        f"Sent {pending['ok']}/{pending['n']} file(s) → {folder}  ↗ open folder",
                        open_path=folder,
                    )
                else:
                    zxdb_set_status(f"All {pending['n']} download(s) failed — check the URLs")
                if post_action:
                    post_action(folder)

        for d in downloads:
            fname = d.get("filename") or os.path.basename(
                urllib.parse.urlparse(d.get("url", "")).path
            ) or "file.bin"
            save_path = os.path.join(folder, fname)

            def _ok(p, _f=fname):
                pending["ok"] += 1
                zxdb_set_status(f"Downloaded {_f}")
                _maybe_finish()

            def _err(e, _f=fname):
                pending["ko"] += 1
                zxdb_set_status(f"Failed {_f}: {e[1]}")
                _maybe_finish()

            zxdb_download_to_path(d.get("url", ""), save_path, _ok, _err)

    def _zxdb_send_to_image(title: str, downloads: list):
        """Download all ZXDB files to temp then hdfmonkey-put them into the loaded disk image."""
        if not _right_disk_content():
            zxdb_set_status("Please load a disk image first (SD Card tab).")
            return
        if not host.right_disk_image_path:
            zxdb_set_status("No disk image loaded.")
            return
        if not downloads:
            zxdb_set_status("No downloadable files for this entry.")
            return

        safe_name  = zxdb_sanitize_folder(title)
        img_dir    = (generate_disk_file_path().rstrip("/") + "/" + safe_name).replace("//", "/")
        image_path = host.right_disk_image_path
        pending    = {"n": len(downloads), "ok": 0, "ko": 0}

        def _maybe_finish():
            if pending["ok"] + pending["ko"] >= pending["n"]:
                if pending["ok"] > 0:
                    zxdb_set_status(f"Sent {pending['ok']}/{pending['n']} file(s) → image:{img_dir}")
                    host._show_sd_notification(
                        ui_tr_now("Sent {ok}/{n} file(s) to SD card image:\n{dir}").format(
                            ok=pending["ok"], n=pending["n"], dir=img_dir)
                    )
                    # Async refresh (listing runs on a worker thread).
                    update_disk_manager_widget_table()
                else:
                    zxdb_set_status(f"All {pending['n']} download(s) failed — check the URLs")

        for d in downloads:
            fname = d.get("filename") or os.path.basename(
                urllib.parse.urlparse(d.get("url", "")).path
            ) or "file.bin"
            url      = d.get("url", "")
            img_dest = (img_dir + "/" + fname).replace("//", "/")

            def _dl_and_put(_url=url, _fname=fname, _img_dest=img_dest):
                tmp = tempfile.NamedTemporaryFile(suffix="_" + _fname, delete=False)
                tmp.close()
                try:
                    urllib.request.urlretrieve(_url, tmp.name)
                    execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir], silent=True)
                    result = execute_hdf_monkey("put", image_path,
                                               extra_argv=[tmp.name.replace("\\", "/"), _img_dest])
                    if result.returncode != 0:
                        raise RuntimeError(f"hdfmonkey put failed (rc={result.returncode})")
                finally:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
                return _img_dest

            def _ok(dest, _f=fname):
                pending["ok"] += 1
                zxdb_set_status(f"Sent {_f} → image:{dest}")
                _maybe_finish()

            def _err(e, _f=fname):
                pending["ko"] += 1
                zxdb_set_status(f"Failed {_f}: {e[1]}")
                _maybe_finish()

            getit_run_in_thread(_dl_and_put, _ok, _err)

    def zxdb_show_downloads_overlay(title: str, downloads: list):
        if not downloads:
            zxdb_set_status("No downloadable files for this entry.")
            return

        dlg = QDialog(host)
        dlg.setWindowTitle(f"Downloads — {title}")
        dlg.resize(1180, 460)
        v = QVBoxLayout(dlg)

        info = QLabel(
            f"<b>{len(downloads)}</b> file(s) for <b>{title}</b>. "
            f"“Download all” saves into <code>downloads\\{zxdb_sanitize_folder(title)}\\</code>."
        )
        info.setWordWrap(True)
        v.addWidget(info)

        # cols: 0-Type 1-Filename 2-Size 3-Source 4-URL 5-Avail. 6-Download 7-SD 8-NextSync
        COL_AVAIL = 5
        COL_DL    = 6
        COL_SD    = 7
        COL_NS    = 8
        tbl = QTableWidget(len(downloads), 9, dlg)
        tbl.setHorizontalHeaderLabels(["Type", "Filename", "Size", "Source", "URL", "Avail.", "", "", ""])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setTextElideMode(Qt.ElideMiddle)
        tbl.horizontalHeader().setStretchLastSection(False)
        tbl.setColumnWidth(0, 160)
        tbl.setColumnWidth(2, 90)
        tbl.setColumnWidth(3, 180)
        tbl.setColumnWidth(COL_AVAIL, 52)
        tbl.setColumnWidth(COL_DL, 100)
        tbl.setColumnWidth(COL_SD, 140)
        tbl.setColumnWidth(COL_NS, 160)
        if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
            for _c in (COL_SD, COL_NS):
                tbl.setColumnWidth(_c, 0)
                tbl.setColumnHidden(_c, True)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        folder_root = os.path.join(ZXNU_DATA_ROOT, DOWNLOADS_ROOT_DIRNAME, zxdb_sanitize_folder(title))
        _ns_base_dlg = _zxdb_resolve_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)

        # Per-row availability: None=pending, True=ok, False=404/error
        _avail: list = [None] * len(downloads)

        def _set_avail_cell(row: int, ok: bool):
            item = QTableWidgetItem("✅" if ok else "❌")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.darkGreen if ok else Qt.red)
            item.setToolTip("File is available" if ok else "File returned 404 / unreachable")
            _avail[row] = ok
            tbl.setItem(row, COL_AVAIL, item)
            _active_cols = [COL_DL] if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS else [COL_DL, COL_SD, COL_NS]
            for _col in _active_cols:
                btn_w = tbl.cellWidget(row, _col)
                if btn_w is not None:
                    btn_w.setEnabled(ok)

        def _check_url(row: int, url: str):
            def _fn():
                return _http_head_ok_with_retry(
                    url, headers={"User-Agent": ZXDB_USER_AGENT}, timeout=10
                )
            def _on_ok(result):
                _set_avail_cell(row, bool(result))
            def _on_err(_):
                _set_avail_cell(row, False)
            getit_run_in_thread(_fn, _on_ok, _on_err)

        def _make_dl_handler(d):
            def _go():
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                save_path = os.path.join(folder_root, fname)
                zxdb_set_status(f"Downloading {fname}…")
                def _ok(p):
                    zxdb_set_status(f"Saved {fname}  ↗ open folder", open_path=os.path.dirname(os.path.abspath(p)))
                def _err(e):
                    zxdb_set_status(f"Download error: {e[1]}")
                zxdb_download_to_path(d.get("url", ""), save_path, _ok, _err)
            return _go

        def _make_sd_handler(d):
            def _go():
                if not _right_disk_content() or not host.right_disk_image_path:
                    zxdb_set_status("Please load a disk image first (SD Card tab).")
                    return
                _zxdb_send_to_image(title, [d])
            return _go

        def _make_ns_handler(d):
            def _go():
                def _after(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
                _zxdb_send_to_path(title, [d], _ns_base_dlg, _after)
            return _go

        for row, d in enumerate(downloads):
            fname = d.get("filename") or os.path.basename(
                urllib.parse.urlparse(d.get("url", "")).path
            ) or ""
            tbl.setItem(row, 0, QTableWidgetItem(d.get("type") or d.get("format") or ""))
            tbl.setItem(row, 1, QTableWidgetItem(fname))
            tbl.setItem(row, 2, QTableWidgetItem(zxdb_human_size(d.get("size"))))
            tbl.setItem(row, 3, QTableWidgetItem(d.get("source") or ""))
            url_text = d.get("url", "") or ""
            url_item = QTableWidgetItem(url_text)
            url_item.setToolTip(url_text)
            tbl.setItem(row, 4, url_item)
            # Availability placeholder until HEAD check completes
            avail_item = QTableWidgetItem("⏳")
            avail_item.setTextAlignment(Qt.AlignCenter)
            avail_item.setToolTip("Checking availability…")
            tbl.setItem(row, COL_AVAIL, avail_item)
            # Action buttons disabled until availability is confirmed
            btn = QPushButton("Download")
            btn.setEnabled(False)
            btn.clicked.connect(_make_dl_handler(d))
            tbl.setCellWidget(row, COL_DL, btn)

            if ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                sd_btn = QPushButton("Send to SD Card")
                sd_btn.setEnabled(False)
                sd_btn.clicked.connect(_make_sd_handler(d))
                tbl.setCellWidget(row, COL_SD, sd_btn)

                ns_btn = QPushButton("Send via NextSync")
                ns_btn.setEnabled(False)
                ns_btn.clicked.connect(_make_ns_handler(d))
                tbl.setCellWidget(row, COL_NS, ns_btn)

        v.addWidget(tbl, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        dl_all_btn = QPushButton(f"Download all → downloads\\{zxdb_sanitize_folder(title)}")
        sd_all_btn = QPushButton("Send all to SD Card")
        ns_all_btn = QPushButton("Send all via NextSync")
        close_btn  = QPushButton("Close")
        btn_row.addWidget(dl_all_btn)
        if ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
            btn_row.addWidget(sd_all_btn)
            btn_row.addWidget(ns_all_btn)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        close_btn.clicked.connect(dlg.accept)

        def _eligible():
            return [d for i, d in enumerate(downloads) if _avail[i] is not False]

        def _send_all_sd():
            if not _right_disk_content() or not host.right_disk_image_path:
                zxdb_set_status("Please load a disk image first (SD Card tab).")
                return
            items = _eligible()
            if not items:
                zxdb_set_status("All files are unavailable (404).")
                return
            _zxdb_send_to_image(title, items)

        def _send_all_ns():
            items = _eligible()
            if not items:
                zxdb_set_status("All files are unavailable (404).")
                return
            def _after(_folder):
                QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
            _zxdb_send_to_path(title, items, _ns_base_dlg, _after)

        sd_all_btn.clicked.connect(_send_all_sd)
        ns_all_btn.clicked.connect(_send_all_ns)

        def _download_all():
            dl_all_btn.setEnabled(False)
            dl_all_btn.setText("Downloading…")
            # Skip files confirmed unavailable (404); include pending/ok ones
            eligible = [d for i, d in enumerate(downloads) if _avail[i] is not False]
            if not eligible:
                dl_all_btn.setText("Nothing to download")
                zxdb_set_status("All files are unavailable (404).")
                return
            pending = {"n": len(eligible), "ok": 0, "ko": 0}

            def _maybe_finish():
                if pending["ok"] + pending["ko"] >= pending["n"]:
                    dl_all_btn.setText(
                        f"Done — {pending['ok']} ok, {pending['ko']} failed"
                    )
                    if pending["ok"] > 0:
                        zxdb_set_status(
                            f"Downloaded {pending['ok']}/{pending['n']} file(s) into {folder_root}  ↗ open folder",
                            open_path=folder_root
                        )
                    else:
                        zxdb_set_status(
                            f"All {pending['n']} download(s) failed — check the URLs"
                        )

            for d in eligible:
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                save_path = os.path.join(folder_root, fname)
                def _ok(p, _f=fname):
                    pending["ok"] += 1
                    zxdb_set_status(f"Saved {_f}")
                    _maybe_finish()
                def _err(e, _f=fname):
                    pending["ko"] += 1
                    zxdb_set_status(f"Failed {_f}: {e[1]}")
                    _maybe_finish()
                zxdb_download_to_path(d.get("url", ""), save_path, _ok, _err)

        dl_all_btn.clicked.connect(_download_all)

        # Fire HEAD checks for every URL now that the table and callbacks are ready
        avail_check_enabled = getattr(host, "settings_avail_check_checkbox", None)
        avail_check_enabled = avail_check_enabled is not None and avail_check_enabled.isChecked()
        if avail_check_enabled:
            for row, d in enumerate(downloads):
                url_to_check = d.get("url", "")
                if url_to_check:
                    _check_url(row, url_to_check)
                else:
                    _set_avail_cell(row, False)
        else:
            # Setting is off — enable all Download buttons immediately, hide placeholders
            for row in range(len(downloads)):
                avail_item = tbl.item(row, COL_AVAIL)
                if avail_item:
                    avail_item.setText("")
                    avail_item.setToolTip("Availability check disabled in Settings")
                for _col in (COL_DL, COL_SD, COL_NS):
                    btn_w = tbl.cellWidget(row, _col)
                    if btn_w is not None:
                        btn_w.setEnabled(True)

        _ticker_lbl, _ticker_timer = _make_disclaimer_ticker(dlg)
        v.addWidget(_ticker_lbl)

        dlg.exec()

    def zxdb_on_table_context_menu(pos):
        item = host.zxdb_results_table.itemAt(pos)
        if item is None:
            return
        row = host.zxdb_results_table.row(item)
        id_item    = host.zxdb_results_table.item(row, 0)
        title_item = host.zxdb_results_table.item(row, 1)
        if not id_item:
            return
        eid   = id_item.text()
        title = title_item.text() if title_item else eid
        entry = id_item.data(Qt.UserRole) or {}
        kind  = (entry.get("_kind") or "game").lower()

        # Make sure the row is selected so the detail is loaded for it.
        host.zxdb_results_table.selectRow(row)

        menu = QMenu(host.zxdb_results_table)

        if kind == "magazine":
            mag_name = entry.get("_name") or title
            act_fetch_mag   = menu.addAction(ui_tr_now("Fetch single magazine by name"))
            act_all_issues  = menu.addAction(ui_tr_now("Retrieve all issues"))
            act_fetch_issue = menu.addAction(ui_tr_now("Fetch issue info for this magazine"))
            act_dl_issue    = menu.addAction(ui_tr_now("Download content"))
            # Only enable download if we already have files loaded for this row
            has_downloads = (
                host._zxdb_selected_id == (entry.get("id") or mag_name)
                or host._zxdb_selected_title.startswith(mag_name + " #")
            ) and bool(host._zxdb_selected_downloads)
            act_dl_issue.setEnabled(has_downloads)
            action = menu.exec(host.zxdb_results_table.viewport().mapToGlobal(pos))
            if action is None:
                return

            if action is act_dl_issue:
                zxdb_show_downloads_overlay(
                    host._zxdb_selected_title or mag_name,
                    host._zxdb_selected_downloads,
                )
                return

            if action is act_all_issues:
                if host._zxdb_magazine_issues and host._zxdb_selected_title == mag_name:
                    _zxdb_open_issues_dialog(mag_name, host._zxdb_magazine_issues)
                else:
                    zxdb_set_status(f"Loading issues for '{mag_name}'…")
                    def _fn_all():
                        payload = zxdb_fetch_json(f"/magazines/{urllib.parse.quote(mag_name)}",
                                              base=ZXDB_MAGAZINES_BASE_URL)
                        src = payload.get("_source", payload) if isinstance(payload, dict) else {}
                        return src.get("issues") or []
                    def _on_ok_all(issues):
                        host._zxdb_magazine_issues = issues
                        _zxdb_open_issues_dialog(mag_name, issues)
                    def _on_err_all(err):
                        zxdb_set_status(f"Error loading issues: {err[1]}")
                    host._zxdb_ctx_thread = getit_run_in_thread(_fn_all, _on_ok_all, _on_err_all)
                return

            if action is act_fetch_mag:
                zxdb_set_status(f"Fetching magazine '{mag_name}'…")

                def _fn_mag():
                    payload = zxdb_fetch_json(f"/magazines/{urllib.parse.quote(mag_name)}",
                                              base=ZXDB_MAGAZINES_BASE_URL)
                    if isinstance(payload, dict) and "_source" in payload:
                        wrapped = {"hits": {"hits": [payload], "total": {"value": 1}}}
                    elif isinstance(payload, list):
                        wrapped = {"hits": {"hits": payload, "total": {"value": len(payload)}}}
                    else:
                        wrapped = payload
                    entries = _zxdb_parse_magazine_list(wrapped)
                    return ("magazines", entries, len(entries), 1, 1)

                def _on_ok_mag(data):
                    kind2, entries, total, pg, total_pages = data
                    zxdb_populate_results(entries, pg, total_pages, kind2)
                    zxdb_set_status(f"Loaded magazine '{mag_name}'")

                def _on_err_mag(err):
                    zxdb_set_status(f"Magazine error: {err[1]}")

                host._zxdb_ctx_thread = getit_run_in_thread(_fn_mag, _on_ok_mag, _on_err_mag)

            elif action is act_fetch_issue:
                issue_id, ok = QInputDialog.getText(
                    host, "Fetch Issue", f"Issue number for '{mag_name}':"
                )
                if not ok or not issue_id.strip():
                    return
                issue_id = issue_id.strip()
                zxdb_set_status(f"Fetching issue {issue_id} of '{mag_name}'…")

                def _fn_issue():
                    return zxdb_fetch_json(
                        f"/magazines/{urllib.parse.quote(mag_name)}"
                        f"/issues/{urllib.parse.quote(issue_id)}",
                        base=ZXDB_MAGAZINES_BASE_URL,
                    )

                def _on_ok_issue(payload):
                    _zxdb_clear_detail_rows()
                    src = payload if isinstance(payload, dict) else {}
                    _zxdb_add_row("Magazine:", mag_name)
                    _zxdb_add_row("Issue:",    issue_id)
                    for key, lbl in (
                        ("date_year",  "Year"),
                        ("date_month", "Month"),
                        ("volume",     "Volume"),
                        ("number",     "Number"),
                    ):
                        v = src.get(key)
                        if v is not None:
                            _zxdb_add_row(f"{lbl}:", str(v))
                    # Build downloads list from files
                    downloads = []
                    shots = []
                    for f in (src.get("files") or []):
                        if not isinstance(f, dict):
                            continue
                        link = f.get("file_link") or ""
                        if not link:
                            continue
                        url = link if link.startswith("http") else "https://spectrumcomputing.co.uk" + link
                        ftype = f.get("filetype") or ""
                        downloads.append({
                            "url":    url,
                            "type":   ftype,
                            "format": ftype,
                            "size":   f.get("file_size"),
                            "source": f.get("comments") or "",
                        })
                        if "cover" in ftype.lower() or "magazine" in ftype.lower():
                            shots.append({"url": url, "type": ftype})
                    # Store downloads so the Download action and button can use them
                    host._zxdb_selected_downloads = downloads
                    host._zxdb_selected_title = f"{mag_name} #{issue_id}"
                    host._zxdb_selected_id = f"{mag_name}:{issue_id}"
                    host.zxdb_download_button.setEnabled(bool(downloads))
                    if shots:
                        zxdb_start_slideshow(shots)
                    else:
                        host.zxdb_screenshot_label.setText("No cover image")
                    contents = src.get("contents") or src.get("articles") or []
                    n_files = len(downloads)
                    zxdb_set_status(
                        f"Issue {issue_id} of '{mag_name}'"
                        + (f"  |  {len(contents)} item(s)" if contents else "")
                        + (f"  |  {n_files} downloadable file(s)" if n_files else "")
                    )

                def _on_err_issue(err):
                    zxdb_set_status(f"Issue error: {err[1]}")

                host._zxdb_ctx_thread = getit_run_in_thread(_fn_issue, _on_ok_issue, _on_err_issue)

        else:
            # ---- Resolve "Send to" destinations ----
            _img_path     = host.right_disk_image_path or ""
            _img_label    = (generate_disk_file_path().rstrip("/") + "/" + zxdb_sanitize_folder(title)
                             ) if _img_path else "(no image loaded)"
            _sd_dest      = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
            _ns_base      = _zxdb_resolve_base_path(host.left_file_nextsync_explorer_selection_full_filename_path)
            _safe_title   = zxdb_sanitize_folder(title)
            _ns_dest      = os.path.join(_ns_base, _safe_title)

            act_download  = menu.addAction(ui_tr_now("Download content"))
            act_mlt       = menu.addAction(ui_tr_now("More like this"))
            menu.addSeparator()
            act_send_sd   = menu.addAction(ui_tr_now("Send to SD card (image)  →  {dest}").format(dest=_sd_dest))
            act_send_sd.setEnabled(bool(host.right_disk_image_path) and bool(_right_disk_content()))
            act_send_ns   = menu.addAction(ui_tr_now("Send using NextSync  →  {dest}").format(dest=_ns_dest))
            if not ZX_NEXT_UNITE_ZXDB_ENABLE_DOWNLOAD_BUTTONS:
                act_download.setVisible(False)
                act_send_sd.setVisible(False)
                act_send_ns.setVisible(False)
            menu.addSeparator()
            _web_url = zxdb_entry_website_url(eid)
            act_open_web = menu.addAction(ui_tr_now("Open on website (zxinfo.dk)"))
            act_open_web.setEnabled(bool(_web_url))
            action = menu.exec(host.zxdb_results_table.viewport().mapToGlobal(pos))
            if action is None:
                return
            if action is act_open_web:
                if _web_url:
                    try:
                        webbrowser.open(_web_url, new=2)
                    except Exception:
                        pass
                return

            # ---- helper: fetch downloads then send to a path ----
            def _fetch_and_send(dest_root, post_action=None):
                if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                    _zxdb_send_to_path(host._zxdb_selected_title or title,
                                       host._zxdb_selected_downloads,
                                       dest_root, post_action)
                    return
                zxdb_set_status(f"Loading {eid}…")
                def _fn():
                    payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)
                def _on_ok(detail, _dr=dest_root, _pa=post_action):
                    zxdb_populate_detail(detail)
                    shots = detail.get("screenshots") or []
                    if not shots and detail.get("screenshot_url"):
                        shots = [{"url": detail["screenshot_url"], "type": ""}]
                    zxdb_start_slideshow(shots)
                    dls = detail.get("downloads", []) or []
                    if not dls:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    _zxdb_send_to_path(detail.get("title") or title, dls, _dr, _pa)
                def _on_err(err):
                    zxdb_set_status(f"Detail error: {err[1]}")
                host._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            if action is act_download:
                # If detail for this row is already loaded, show the overlay immediately.
                if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                    zxdb_show_downloads_overlay(host._zxdb_selected_title or title,
                                                host._zxdb_selected_downloads)
                    return

                # Otherwise load the detail first, then show the overlay.
                zxdb_set_status(f"Loading {eid}…")

                def _fn():
                    payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                    return zxdb_parse_game_detail(payload)

                def _on_ok(detail):
                    zxdb_populate_detail(detail)
                    shots = detail.get("screenshots") or []
                    if not shots and detail.get("screenshot_url"):
                        shots = [{"url": detail["screenshot_url"], "type": ""}]
                    zxdb_start_slideshow(shots)
                    downloads = detail.get("downloads", []) or []
                    if not downloads:
                        zxdb_set_status("No downloadable files for this entry.")
                        return
                    zxdb_show_downloads_overlay(detail.get("title") or title, downloads)

                def _on_err(err):
                    zxdb_set_status(f"Detail error: {err[1]}")

                host._zxdb_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

            elif action is act_send_sd:
                def _fetch_and_send_to_image():
                    if host._zxdb_selected_id == eid and host._zxdb_selected_downloads:
                        _zxdb_send_to_image(host._zxdb_selected_title or title,
                                            host._zxdb_selected_downloads)
                        return
                    zxdb_set_status(f"Loading {eid}…")
                    def _fn_sd():
                        payload = zxdb_fetch_json(f"/entries/{urllib.parse.quote(eid)}")
                        return zxdb_parse_game_detail(payload)
                    def _on_ok_sd(detail):
                        zxdb_populate_detail(detail)
                        dls = detail.get("downloads", []) or []
                        if not dls:
                            zxdb_set_status("No downloadable files for this entry.")
                            return
                        _zxdb_send_to_image(detail.get("title") or title, dls)
                    def _on_err_sd(err):
                        zxdb_set_status(f"Detail error: {err[1]}")
                    host._zxdb_ctx_thread = getit_run_in_thread(_fn_sd, _on_ok_sd, _on_err_sd)
                _fetch_and_send_to_image()

            elif action is act_send_ns:
                def _after_ns_dl(_folder):
                    QTimer.singleShot(0, host._nextsync_start_server_fn)
                _fetch_and_send(_ns_base, _after_ns_dl)

            elif action is act_mlt:
                zxdb_set_status(f"Finding titles similar to '{title}'…")

                def _fn_mlt():
                    payload = zxdb_fetch_json(
                        f"/entries/morelikethis/{urllib.parse.quote(eid)}"
                        f"?mode=compact&size={ZXDB_PAGE_SIZE}"
                    )
                    entries, total, _pg, total_pages, _ps = zxdb_parse_search(payload)
                    for e in entries:
                        e["_kind"] = "game"
                    return ("games", entries, total, 1, total_pages)

                def _on_ok_mlt(data):
                    kind2, entries, total, pg, total_pages = data
                    zxdb_populate_results(entries, pg, total_pages, kind2)
                    zxdb_set_status(
                        f"{len(entries)} title(s) similar to '{title}'"
                    )

                def _on_err_mlt(err):
                    zxdb_set_status(f"More like this error: {err[1]}")

                host._zxdb_ctx_thread = getit_run_in_thread(_fn_mlt, _on_ok_mlt, _on_err_mlt)

    host.zxdb_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
    host.zxdb_results_table.customContextMenuRequested.connect(zxdb_on_table_context_menu)

    # ---- Fullscreen preview overlay (mirrors GetIt) ----

    zxdb_container = QWidget()
    zxdb_container.setLayout(host.zxdb_form)
    zxdb_container.setAutoFillBackground(False)
    zxdb_container.setAttribute(Qt.WA_TranslucentBackground)

    zxdb_scroll = QScrollArea()
    zxdb_scroll.setWidget(zxdb_container)
    zxdb_scroll.setWidgetResizable(True)
    zxdb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    zxdb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    zxdb_scroll.setAutoFillBackground(False)
    zxdb_scroll.setAttribute(Qt.WA_TranslucentBackground)
    zxdb_scroll.viewport().setAutoFillBackground(False)
    zxdb_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

    # Fixed search/button header above the scrollable results so the
    # vertical scroller only covers the content area (like the Unite! tab).
    zxdb_normal_widget = QWidget()
    zxdb_normal_widget.setAutoFillBackground(False)
    zxdb_normal_widget.setAttribute(Qt.WA_TranslucentBackground)
    zxdb_normal_layout = QVBoxLayout(zxdb_normal_widget)
    zxdb_normal_layout.setContentsMargins(0, 0, 0, 0)
    zxdb_normal_layout.setSpacing(0)
    zxdb_normal_layout.addWidget(host._zxdb_search_widget, 0)
    zxdb_normal_layout.addWidget(zxdb_scroll, 1)

    host._zxdb_fullscreen_pixmap = None

    zxdb_overlay = QWidget()
    zxdb_overlay.setStyleSheet("background: #000;")
    zxdb_overlay_layout = QVBoxLayout(zxdb_overlay)
    zxdb_overlay_layout.setContentsMargins(0, 0, 0, 0)
    zxdb_overlay_layout.setSpacing(0)

    zxdb_close_btn = QToolButton()
    zxdb_close_btn.setText("✕")
    zxdb_close_btn.setStyleSheet(
        "QToolButton { color: white; background: #333; border: none; font-size: 18px; padding: 4px 8px; }"
        "QToolButton:hover { background: #c00; }"
    )
    zxdb_close_bar = QHBoxLayout()
    zxdb_close_bar.setContentsMargins(4, 4, 4, 0)
    zxdb_close_bar.addWidget(zxdb_close_btn, 0)
    zxdb_close_bar.addStretch()
    zxdb_close_bar_widget = QWidget()
    zxdb_close_bar_widget.setLayout(zxdb_close_bar)
    zxdb_overlay_layout.addWidget(zxdb_close_bar_widget, 0)

    host.zxdb_fullscreen_label = _ScalingImageLabel()
    host.zxdb_fullscreen_label.setAlignment(Qt.AlignCenter)
    host.zxdb_fullscreen_label.setStyleSheet("background: #000;")
    host.zxdb_fullscreen_label.setCursor(Qt.PointingHandCursor)
    zxdb_overlay_layout.addWidget(host.zxdb_fullscreen_label, 1)

    _fs_nav_style = (
        "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
        " font-size: 32px; font-weight: bold; padding: 4px 10px; }"
        "QToolButton:hover { background: rgba(0,0,0,220); }"
    )
    host.zxdb_fs_prev_btn = QToolButton(zxdb_overlay)
    host.zxdb_fs_prev_btn.setText("<")
    host.zxdb_fs_prev_btn.setStyleSheet(_fs_nav_style)
    host.zxdb_fs_prev_btn.setVisible(False)
    host.zxdb_fs_prev_btn.raise_()

    host.zxdb_fs_next_btn = QToolButton(zxdb_overlay)
    host.zxdb_fs_next_btn.setText(">")
    host.zxdb_fs_next_btn.setStyleSheet(_fs_nav_style)
    host.zxdb_fs_next_btn.setVisible(False)
    host.zxdb_fs_next_btn.raise_()

    def _zxdb_reposition_fs_btns():
        ow = zxdb_overlay.width()
        oh = zxdb_overlay.height()
        bh = host.zxdb_fs_prev_btn.sizeHint().height()
        by = (oh - bh) // 2
        host.zxdb_fs_prev_btn.move(8, by)
        bw = host.zxdb_fs_next_btn.sizeHint().width()
        host.zxdb_fs_next_btn.move(ow - bw - 8, by)

    host._zxdb_reposition_fs_btns = _zxdb_reposition_fs_btns
    host.zxdb_fs_prev_btn.clicked.connect(_zxdb_nav_prev)
    host.zxdb_fs_next_btn.clicked.connect(_zxdb_nav_next)

    host._zxdb_stack = QStackedWidget()
    host._zxdb_stack.setAutoFillBackground(False)
    host._zxdb_stack.setAttribute(Qt.WA_TranslucentBackground)
    host._zxdb_stack.addWidget(zxdb_normal_widget)
    host._zxdb_stack.addWidget(zxdb_overlay)
    host._zxdb_stack.setCurrentIndex(0)

    def _zxdb_show_fullscreen():
        px = host.zxdb_screenshot_label.pixmap()
        if px is None or px.isNull():
            return
        host._zxdb_fullscreen_pixmap = px
        host._zxdb_stack.setCurrentIndex(1)
        _zxdb_resize_fullscreen()
        host._zxdb_reposition_fs_btns()
        zxdb_update_nav_buttons()

    def _zxdb_hide_fullscreen():
        host._zxdb_stack.setCurrentIndex(0)
        zxdb_update_nav_buttons()
    host._hide_fullscreen_zxdb = _zxdb_hide_fullscreen

    def _zxdb_resize_fullscreen():
        px = host._zxdb_fullscreen_pixmap
        if px and not px.isNull():
            host.zxdb_fullscreen_label.set_image(px)
        host._zxdb_reposition_fs_btns()

    zxdb_close_btn.clicked.connect(_zxdb_hide_fullscreen)
    host.zxdb_fullscreen_label.mousePressEvent = lambda e: _zxdb_hide_fullscreen()

    host._zxdb_dbl_filter = _DblClickFilter(_zxdb_show_fullscreen)
    host.zxdb_screenshot_label.installEventFilter(host._zxdb_dbl_filter)
    host.zxdb_screenshot_label.setCursor(Qt.PointingHandCursor)

    # Expose handler for tab activation
    def zxdb_on_tab_activated():
        if host._zxdb_loaded_once or host._zxdb_search_loading:
            return
        host._zxdb_loaded_once = True
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
        def _zxdb_random_done():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB, host.zxdb_results_table.rowCount())
        zxdb_run_random(_zxdb_random_done)
        # Allow autocomplete only after the initial load has been kicked off
        # so that config-restored search text doesn't trigger background
        # by-letter fetches before the first render completes.
        host._zxdb_ac_ready = True

    def zxdb_startup_initial_load(_ticks=20, _saw_fanout=False, _waits=400):
        """Give the ZXDB tab a picture-rich first page when the app is
        restored straight onto it.

        Every launch kicks off the Unite! "Latest" multi-search before the
        restored tab is activated, and that fan-out drives THIS pane through
        zxdb_on_latest. ZXDB's newest rows are database entries created long
        before anyone uploads media to them, so that page is a grid of blank
        cells — the "no pictures on restart" report. The pane's own first-visit
        content (a random page) is drawn from established titles and does have
        screenshots, but a plain activation loses the race: it runs before the
        fan-out's ZXDB fetch starts, and the fan-out then overwrites it.

        Taking over early is not an option either — superseding the fan-out's
        fetch means its on_complete never fires, and Unite! waits on that
        callback. So this waits for the fan-out's ZXDB load to start AND
        finish, then runs the normal first-visit load. If no fan-out ever
        materialises (feature flag off, offline, Unite! hidden) the tick budget
        expires and the load runs anyway, so the tab is never left empty.

        A user-driven query owns the pane outright and is never replaced.
        """
        if host.zxdb_search_input.text().strip():
            return                          # a real query owns the pane
        if host._zxdb_search_loading:
            # A fetch is in flight — the fan-out's. Waiting for it does NOT
            # spend the budget: on a slow link it can easily outlast a fixed
            # window, and giving up mid-flight would leave the screenshot-less
            # page on screen, i.e. the very bug this exists to fix. The
            # separate _waits cap keeps a wedged request from polling forever.
            if _waits > 0:
                QTimer.singleShot(
                    150,
                    lambda: zxdb_startup_initial_load(_ticks, True, _waits - 1))
            return
        if not _saw_fanout and _ticks > 0:
            # No fan-out yet. It is kicked off just before this runs, so give
            # it a moment to start before concluding there won't be one.
            QTimer.singleShot(
                150, lambda: zxdb_startup_initial_load(_ticks - 1, False, _waits))
            return
        # The fan-out (if any) has finished; replace its page with ours.
        host._zxdb_loaded_once = False
        host._zxdb_last_query = ""
        zxdb_on_tab_activated()

    host._zxdb_startup_initial_load = zxdb_startup_initial_load
    host._zxdb_on_tab_activated = zxdb_on_tab_activated
    host.zxdb_run_search = zxdb_run_search
    host.zxdb_on_latest = zxdb_on_latest
    host.zxdb_on_random = zxdb_on_random
    host._zxdb_open_gallery_viewer = _zxdb_open_gallery_viewer
