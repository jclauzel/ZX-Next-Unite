"""zxnu_getit_pane.py — GetIt (zxnext.uk) gallery pane builder.

Strangler extraction from MainWindow.__init__: the ~3k-line GetIt (zxnext.uk) UI
construction blob (widgets + navigation + search/detail/download closures) now
lives here as build_getit_pane(host, ...). The operation-layer wiring that still
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
    QScrollArea, QStackedWidget, QSplitter, QFrame, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QToolButton, QMenu, QCompleter,
    QFileDialog)

from zxnu_config import *
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *
# Star imports skip underscore-prefixed names; import the private
# helpers the block uses explicitly (tests/test_pane_imports.py
# tripwires that these lists stay complete).
from zxnu_api import (_http_fetch_bytes_with_retry,
    _http_fetch_with_cd_retry)
from zxnu_gallery import (_DblClickFilter)
from zxnu_media import (_ZXSCR_PIXMAP_CACHE, _gallery_extract_tags,
    _zxscr_basename_for_url)


def build_getit_pane(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    execute_hdf_monkey,
    generate_disk_file_path,
    update_disk_manager_widget_table,
    _persist_retro,
    _search_autocomplete_on,
    _splitter_persist_on_move,
    _gif_fetch_bytes,
    _qimage_from_data,
    _gallery_add_text_pages,
    _gallery_add_description_page,
    _make_retro_toggle_button,
    _popup_height_for,
    _wrap_flow_row,
    getit_run_in_thread,
    _CompleterPopupHider,
    _GALLERY_TEXT_EXTS,
    _start_tab_spinner,
    _stop_tab_spinner,
    _set_tab_badge,
    _clear_tab_badge,
    _multi_search_enabled,
    _cross_search_zxdb,
    _cross_search_zxart,
    _right_disk_content,
):
    # -----------------------------------------------------------------------
    # GetIt UI construction
    # -----------------------------------------------------------------------

    host.getit_form = QFormLayout()
    host.getit_form.setContentsMargins(4, 4, 4, 4)

    # --- Search row (wraps onto extra rows when the window is narrow) ---
    getit_search_row = FlowLayout(margin=2)
    host.getit_search_input = QLineEdit()
    host.getit_search_input.setPlaceholderText("Search files... (leave empty for latest 20)")
    host.getit_search_input.setMinimumWidth(280)
    host.getit_search_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    getit_search_row.addWidget(host.getit_search_input)

    host._getit_search_valid_lbl = QLabel()
    host._getit_search_valid_lbl.setVisible(False)
    getit_search_row.addWidget(host._getit_search_valid_lbl)

    host.getit_search_button = QPushButton("Search")
    getit_search_row.addWidget(host.getit_search_button)

    host.getit_latest_button = QPushButton("Latest")
    getit_search_row.addWidget(host.getit_latest_button)

    host.getit_random_button = QPushButton("Random")
    host.getit_random_button.setToolTip(
        "Pick a random page from the full GetIt catalogue and show its entries."
    )
    getit_search_row.addWidget(host.getit_random_button)

    getit_search_row.addWidget(QLabel("Page:"))
    host.getit_page_label = QLabel("1")
    host.getit_page_label.setMinimumWidth(24)
    getit_search_row.addWidget(host.getit_page_label)

    host.getit_prev_button = QPushButton("< Prev")
    host.getit_prev_button.setEnabled(False)
    getit_search_row.addWidget(host.getit_prev_button)

    host.getit_next_button = QPushButton("Next >")
    host.getit_next_button.setEnabled(False)
    getit_search_row.addWidget(host.getit_next_button)

    getit_search_row.addWidget(QLabel("View:"))
    host.getit_view_combo = QComboBox()
    host.getit_view_combo.addItem("Table",   "table")
    host.getit_view_combo.addItem("Gallery", "gallery")
    host.getit_view_combo.setToolTip(
        "Switch between the classic table view and the picture (gallery) view.\n"
        "Persisted across sessions in the config file."
    )
    getit_search_row.addWidget(host.getit_view_combo)
    host.getit_retro_button = _make_retro_toggle_button(
        host, "_getit_item_retro",
        on_change=lambda c, k=SETTING_GETIT_ITEM_RETRO: (
            _persist_retro(k, c), host._pane_retro_gallery_set("getit", c)))
    getit_search_row.addWidget(host.getit_retro_button)

    host.getit_status_label = QLabel("")
    getit_search_row.addWidget(host.getit_status_label)

    getit_search_widget = _wrap_flow_row(getit_search_row)
    # NOTE: the search/button bar is intentionally NOT added to the
    # scrolled form here.  It is placed in a fixed header above the
    # scroll area (see _getit_stack assembly) so the vertical scroller
    # only spans the results/details area, matching the Unite! tab.
    host._getit_search_widget = getit_search_widget

    # --- Results table ---
    host.getit_results_table = QTableWidget(0, 4)
    host.getit_results_table.setHorizontalHeaderLabels(["ID", "Title", "Author", "Size"])
    host.getit_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    host.getit_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    host.getit_results_table.horizontalHeader().setStretchLastSection(True)
    host.getit_results_table.setMinimumHeight(200)
    host.getit_results_table.setColumnWidth(0, 70)
    host.getit_results_table.setColumnWidth(1, 350)
    host.getit_results_table.setColumnWidth(2, 150)
    host.getit_results_table.setColumnWidth(3, 80)

    host.getit_screenshot_label = QLabel()
    host.getit_screenshot_label.setFixedSize(256, 192)
    host.getit_screenshot_label.setAlignment(Qt.AlignCenter)
    host.getit_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
    host.getit_screenshot_label.setText("No preview")
    host.getit_screenshot_label.setToolTip("Double-click to enlarge")

    _GETIT_BTN_STYLE = (
        "QPushButton { color: #eee; background: #2a2a2a; border: 1px solid #444;"
        " border-radius: 4px; padding: 6px 12px; text-align: left; }"
        "QPushButton:hover { background: #3a3a3a; border-color: #666; }"
        "QPushButton:disabled { color: #555; background: #1a1a1a; border-color: #333; }"
    )
    host.getit_download_button = QPushButton("⬇  Download")
    host.getit_download_button.setStyleSheet(_GETIT_BTN_STYLE)
    host.getit_download_button.setEnabled(False)

    host.getit_send_sd_button = QPushButton("💾  Send to SD card")
    host.getit_send_sd_button.setStyleSheet(_GETIT_BTN_STYLE)
    host.getit_send_sd_button.setEnabled(False)

    host.getit_send_ns_button = QPushButton("🔁  Send via NextSync")
    host.getit_send_ns_button.setStyleSheet(_GETIT_BTN_STYLE)
    host.getit_send_ns_button.setEnabled(False)

    getit_right_col = QVBoxLayout()
    _getit_link_label = QLabel('<a href="https://zxnext.uk/">https://zxnext.uk/</a>')
    _getit_link_label.setOpenExternalLinks(True)
    _getit_link_label.setTextFormat(Qt.RichText)
    _getit_link_label.setAlignment(Qt.AlignCenter)
    getit_right_col.addWidget(_getit_link_label)
    # Visibility is controlled by _getit_apply_view_mode (shown in Table, hidden in Gallery)
    host.getit_screenshot_label.setVisible(False)
    host.getit_download_button.setVisible(False)
    host.getit_send_sd_button.setVisible(False)
    host.getit_send_ns_button.setVisible(False)
    getit_right_col.addWidget(host.getit_screenshot_label)
    getit_right_col.addWidget(host.getit_download_button)
    getit_right_col.addWidget(host.getit_send_sd_button)
    getit_right_col.addWidget(host.getit_send_ns_button)
    host._getit_preview_label        = host.getit_screenshot_label
    host._getit_preview_download_btn = host.getit_download_button
    host._getit_preview_send_sd_btn  = host.getit_send_sd_button
    host._getit_preview_send_ns_btn  = host.getit_send_ns_button
    getit_right_col.addStretch()
    getit_right_widget = QWidget()
    getit_right_widget.setLayout(getit_right_col)
    host._getit_right_widget = getit_right_widget

    getit_table_row = QHBoxLayout()

    host.getit_view_stack = QStackedWidget()
    host.getit_view_stack.addWidget(host.getit_results_table)  # index 0: Table

    def _getit_gallery_title(e):
        return (e.get("title") or e.get("id") or "")[:80]
    def _getit_gallery_info(e):
        parts = []
        if e.get("author"):   parts.append(e["author"])
        if e.get("date"):     parts.append(str(e["date"]))
        if e.get("category"): parts.append(e["category"])
        return " · ".join(parts)

    def _getit_thumb_fetch(entry, set_pixmap, set_screenshots,
                           set_tags=None, set_info_text=None):
        eid = entry.get("id") or ""
        url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
        set_screenshots([url])
        def _make_placeholder():
            # GetIt entries always describe a downloadable artefact; use
            # the entry filename / category as the typed label so a
            # missing image still shows the format (e.g. TAP, POK).
            link = host._getit_selected_link or ""
            title = entry.get("title") or eid
            ref = link or title
            label = zxfmt_label_for_name(ref) if ref else "FILE"
            if label == "FILE":
                cat = (entry.get("category") or "").upper()
                if cat:
                    label = cat[:6]
            placeholder_url = f"placeholder://{label}/{title}"
            set_screenshots([placeholder_url])
            pm = zxfmt_make_placeholder_pixmap(label, title)
            if not pm.isNull():
                set_pixmap(pm, placeholder_url)
        def _fn(_u=url):
            # Fetch *and* decode off the UI thread (QImage is thread-safe);
            # the UI callback only does the cheap QPixmap.fromImage().
            data = _http_fetch_bytes_with_retry(_u, timeout=20)
            return (_qimage_from_data(data), _u)
        def _on_done(res, _set=set_pixmap):
            img, u = res
            px = QPixmap.fromImage(img) if (img is not None and not img.isNull()) else QPixmap()
            # Suppress libpng warnings for malformed PNGs
            if px.isNull():
                _make_placeholder()
                return
            _set(px, u)
        def _on_err(_err):
            _make_placeholder()
        getit_run_in_thread(_fn, _on_done, _on_err, gated=True)

        # Lazily enrich the hover-info line with the entry date, which is
        # not part of the list endpoint. Author and category already come
        # from the list payload, so the cell shows useful info immediately.
        if set_info_text is not None and eid:
            def _det_fn(_eid=eid):
                text = getit_fetch(f"/nx/{_eid}/f/")
                return getit_parse_detail(text)
            def _det_ok(d, _e=entry, _set=set_info_text):
                parts = []
                if _e.get("author"):  parts.append(_e["author"])
                date = (d.get("DATE") or "").strip() if isinstance(d, dict) else ""
                if date:              parts.append(date)
                if _e.get("category"):parts.append(_e["category"])
                _set(" · ".join(parts))
            def _det_err(_e): pass
            getit_run_in_thread(_det_fn, _det_ok, _det_err, gated=True)

    def _getit_extra_fetch(url, on_pixmap):
        # GetIt only exposes a single screenshot per entry; nothing to do.
        pass

    def _getit_extra_fetch_url(url, on_pixmap):
        """Generic URL → QPixmap fetcher used by GalleryItemViewer."""
        if isinstance(url, str) and url.startswith("placeholder://"):
            rest = url[len("placeholder://"):]
            label, _, sub = rest.partition("/")
            pm = zxfmt_make_placeholder_pixmap(label or "FILE", sub)
            if not pm.isNull():
                on_pixmap(pm)
            return
        if zxscr_url_is_scr(url):
            base = _zxscr_basename_for_url(url)
            cached = _ZXSCR_PIXMAP_CACHE.get(base)
            if cached is not None and not cached.isNull():
                on_pixmap(cached)
                return
            def _scr_fn(_u=url, _b=base):
                return (_http_fetch_bytes_with_retry(_u, timeout=20), _b)
            def _scr_ok(res):
                data, b = res
                pm = zxscr_convert_bytes_to_pixmap(data, b)
                if pm is not None and not pm.isNull():
                    on_pixmap(pm)
            getit_run_in_thread(_scr_fn, _scr_ok, lambda _e: None)
            return
        def _fn(_u=url):
            tmp = tempfile.NamedTemporaryFile(suffix=".bmp", delete=False)
            tmp.close()
            with open(tmp.name, "wb") as _fh:
                _fh.write(_http_fetch_bytes_with_retry(_u, timeout=20))
            return tmp.name
        def _on_done(path):
            px = QPixmap(path)
            try: os.unlink(path)
            except Exception: pass
            if not px.isNull():
                on_pixmap(px)
            else:
                on_pixmap(None)
        def _on_err(_e): on_pixmap(None)
        getit_run_in_thread(_fn, _on_done, _on_err)

    def _getit_gallery_context_menu(entry, global_pos):
        eid   = entry.get("id") or ""
        title = entry.get("title") or eid
        default_name = host._getit_selected_link or f"{eid}.bin"
        _safe_title  = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid
        _img_path    = host.right_disk_image_path or ""
        _img_label   = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                        ) if _img_path else "(no image loaded)"
        _sd_dest     = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base     = _getit_resolve_ns_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest     = os.path.join(_ns_base, _safe_title)
        menu = QMenu()
        act_dl      = menu.addAction(f'Download \u201c{title}\u201d')
        menu.addSeparator()
        act_send_sd = menu.addAction(f"Send to SD card (image)  \u2192  {_sd_dest}")
        act_send_sd.setEnabled(bool(host.right_disk_image_path) and bool(_right_disk_content()))
        act_send_ns = menu.addAction(f"Send using NextSync  \u2192  {_ns_dest}")
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is act_dl:
            getit_do_download(eid, default_name)
        elif chosen is act_send_sd:
            _getit_send_to_image(eid, default_name, title)
        elif chosen is act_send_ns:
            def _after_ns_dl_gi(_folder):
                QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
            _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after_ns_dl_gi)

    host.getit_gallery_view = GalleryView(
        rows_per_page_getter=lambda: host._gallery_rows_per_page,
        anim_mode_getter=lambda: host._gallery_anim_mode,
        cols_getter=lambda: host._gallery_cols,
        img_size_getter=lambda: host._gallery_img_size,
        thumb_fetch_cb=_getit_thumb_fetch,
        extra_fetch_cb=_getit_extra_fetch,
        title_getter=_getit_gallery_title,
        info_getter=_getit_gallery_info,
        context_menu_cb=_getit_gallery_context_menu,
        is_favorite_cb=lambda e: host._fav_is({**e, "_fav_source": "getit"}),
        toggle_favorite_cb=lambda e: host._fav_toggle({**e, "_fav_source": "getit"}),
    )
    # Animate .gif thumbnails (QMovie) just like the in-pane item viewer.
    host.getit_gallery_view.set_gif_fetch_cb(_gif_fetch_bytes)
    host._fav_fetchers = getattr(host, "_fav_fetchers", {})
    host._fav_fetchers["getit"] = {
        "thumb": _getit_thumb_fetch,
        "extra": _getit_extra_fetch,
        "title": _getit_gallery_title,
        "info":  _getit_gallery_info,
    }
    host.getit_view_stack.addWidget(host.getit_gallery_view)  # index 1: Gallery

    getit_table_row.addWidget(host.getit_view_stack, 1)
    # Animated retro "SEARCHING..." banner shown over the results area
    # whenever a fetch is in flight — including re-searches
    # over already-populated content, so it stays visible on top of the
    # pygame GalleryScene (not only on the first/empty load).
    host._getit_loading_overlay = RetroLoadingOverlay(
        host.getit_view_stack,
        lambda: getattr(host, "_getit_search_loading", False))
    getit_table_row.addWidget(getit_right_widget)
    getit_table_container = QWidget()
    getit_table_container.setLayout(getit_table_row)
    # Not added to the form directly: this container becomes the top pane
    # of the results ⇄ MOTD splitter assembled below the detail panel.

    # --- Detail panel ---
    getit_detail_outer = QHBoxLayout()
    getit_detail_form = QFormLayout()
    getit_detail_form.setContentsMargins(0, 0, 0, 0)

    host.getit_detail_title  = QLabel("")
    host.getit_detail_author = QLabel("")
    host.getit_detail_size   = QLabel("")
    host.getit_detail_date   = QLabel("")
    host.getit_detail_hits   = QLabel("")
    host.getit_detail_url    = QLabel("")
    host.getit_detail_url.setOpenExternalLinks(True)
    host.getit_detail_desc   = QLabel("")
    host.getit_detail_desc.setWordWrap(True)

    # getit_detail_form.addRow("Title:",       host.getit_detail_title)
    # getit_detail_form.addRow("Author:",      host.getit_detail_author)
    # getit_detail_form.addRow("Size:",        host.getit_detail_size)
    # getit_detail_form.addRow("Date:",        host.getit_detail_date)
    # getit_detail_form.addRow("Hits:",        host.getit_detail_hits)
    # getit_detail_form.addRow("URL:",         host.getit_detail_url)
    # getit_detail_form.addRow("Description:", host.getit_detail_desc)

    getit_detail_widget = QWidget()
    getit_detail_widget.setLayout(getit_detail_form)
    getit_detail_outer.addWidget(getit_detail_widget, 1)

    getit_detail_container = QWidget()
    getit_detail_container.setLayout(getit_detail_outer)

    # Top pane of the results ⇄ MOTD splitter: the results area plus the
    # (currently empty) detail panel, so the grab handle sits directly
    # above the MOTD text.
    getit_top_pane = QWidget()
    _getit_top_v = QVBoxLayout(getit_top_pane)
    _getit_top_v.setContentsMargins(0, 0, 0, 0)
    _getit_top_v.setSpacing(0)
    _getit_top_v.addWidget(getit_table_container, 1)
    _getit_top_v.addWidget(getit_detail_container, 0)

    # --- MOTD ---

    host.getit_motd_text = QLabel("")
    host.getit_motd_text.setWordWrap(True)
    host.getit_motd_text.setStyleSheet("color: #888; font-style: italic;")
    host.getit_motd_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)

    # The label lives in a frameless transparent scroller so a long MOTD
    # stays readable (scrolls) when its splitter pane is dragged small.
    getit_motd_scroll = QScrollArea()
    getit_motd_scroll.setWidget(host.getit_motd_text)
    getit_motd_scroll.setWidgetResizable(True)
    getit_motd_scroll.setFrameShape(QFrame.NoFrame)
    getit_motd_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    getit_motd_scroll.setAutoFillBackground(False)
    getit_motd_scroll.setAttribute(Qt.WA_TranslucentBackground)
    getit_motd_scroll.viewport().setAutoFillBackground(False)
    getit_motd_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)
    getit_motd_scroll.setMinimumHeight(24)

    # Results ⇄ MOTD splitter, mirroring the SD Card tab's explorers ⇄ log
    # one: drag the horizontal grabber to grow the results/gallery area at
    # the MOTD's expense or vice versa. The position is persisted to the
    # cfg on every drag and restored in load_configuration_file.
    host.getit_splitter = QSplitter(Qt.Vertical)
    host.getit_splitter.addWidget(getit_top_pane)
    host.getit_splitter.addWidget(getit_motd_scroll)
    host.getit_splitter.setChildrenCollapsible(False)
    host.getit_splitter.setStretchFactor(0, 1)
    host.getit_splitter.setStretchFactor(1, 0)
    host.getit_splitter.setHandleWidth(8)
    host.getit_splitter.setSizes([500, 60])
    host.getit_splitter.handle(1).setToolTip(
        "Drag to resize the results / MOTD split.")
    _splitter_persist_on_move(host.getit_splitter, SETTING_GETIT_SPLITTER)

    host.getit_form.addRow(host.getit_splitter)

    # Internal state
    host._getit_current_page = 1
    host._getit_total_pages  = 1
    host._getit_last_query   = ""
    host._getit_selected_id  = ""
    host._getit_selected_link = ""

    host._getit_motd_loaded = False
    host._getit_motd_loading = False
    host._getit_search_loading = False
    # Generation token: bumped on every new search/latest/random so an
    # in-flight request can be superseded (its stale result discarded)
    # instead of blocking the new request until it finishes.
    host._getit_search_gen = 0
    host._getit_last_entries = []  # cached page entries for gallery refresh
    host._getit_ac_titles: list = []   # autocomplete title cache (loaded once)
    host._getit_ac_loading = False     # guard against duplicate fetch

    # ---- Internal helpers ----

    def getit_set_status(msg: str):
        host.getit_status_label.setText(msg)

    def getit_populate_results(entries, page, total_pages):
        host._getit_current_page = page
        host._getit_total_pages  = total_pages
        host.getit_page_label.setText(str(page))
        host.getit_prev_button.setEnabled(page > 1)
        host.getit_next_button.setEnabled(page < total_pages)

        host.getit_results_table.setRowCount(0)
        for e in entries:
            row = host.getit_results_table.rowCount()
            host.getit_results_table.insertRow(row)
            host.getit_results_table.setItem(row, 0, QTableWidgetItem(e["id"]))
            host.getit_results_table.setItem(row, 1, QTableWidgetItem(e["title"]))
            host.getit_results_table.setItem(row, 2, QTableWidgetItem(e["author"]))
            host.getit_results_table.setItem(row, 3, QTableWidgetItem(e["size"]))
        host._getit_last_entries = list(entries)
        host.getit_gallery_view.populate(entries)
        host._pane_retro_gallery_refresh("getit")
        host.getit_gallery_view.select_entry(
            lambda _e, _sel=host._getit_selected_id: bool(_sel) and _e.get("id") == _sel
        )
        try:
            _aio = getattr(host, "_allinone_repopulate", None)
            if _aio is not None:
                _aio()
        except Exception:
            pass

    def getit_clear_detail():
        host.getit_detail_title.setText("")
        host.getit_detail_author.setText("")
        host.getit_detail_size.setText("")
        host.getit_detail_date.setText("")
        host.getit_detail_hits.setText("")
        host.getit_detail_url.setText("")
        host.getit_detail_desc.setText("")
        host.getit_download_button.setEnabled(False)
        host._getit_selected_id   = ""
        host._getit_selected_link = ""

    def getit_populate_detail(detail: dict):
        host.getit_detail_title.setText(detail.get("TITL", ""))
        host.getit_detail_author.setText(detail.get("AUTH", ""))
        host.getit_detail_size.setText(detail.get("FSIZ", ""))
        host.getit_detail_date.setText(detail.get("DATE", ""))
        host.getit_detail_hits.setText(detail.get("HITS", ""))
        url_val = detail.get("URL", "")
        host.getit_detail_url.setText(f'<a href="{url_val}">{url_val}</a>' if url_val else "")
        host.getit_detail_desc.setText(detail.get("DESC", ""))
        link = detail.get("LINK", "")
        host._getit_selected_link = link
        _has_id = bool(host._getit_selected_id)
        _sd_ok  = bool(host.right_disk_image_path) and bool(_right_disk_content())
        host.getit_download_button.setEnabled(_has_id)
        host.getit_send_sd_button.setEnabled(_has_id and _sd_ok)
        host.getit_send_ns_button.setEnabled(_has_id)

    # ---- Background search task ----

    def getit_run_search(query: str, page: int, on_complete=None):
        # Supersede any in-flight GetIt request: bump the generation token
        # so the previous request's result/error is discarded when it
        # finally arrives, and start this one immediately.
        host._getit_search_gen += 1
        _gen = host._getit_search_gen
        host._getit_last_query = query
        host._getit_search_loading = True
        getit_set_status("Searching…")
        host.getit_search_button.setEnabled(False)
        host.getit_latest_button.setEnabled(False)

        def _search_fn():
            offset = (page - 1) * GETIT_PAGE_SIZE
            if query:
                # GetIt's /f search matches the (lowercased) title against
                # the query as sent, so a mixed-case query like "CSpect"
                # would miss. Send it lowercased to make it case-insensitive
                # (the displayed query keeps its original case).
                path = f"/f?s={urllib.parse.quote(query.lower())}"
            else:
                # Empty-search path (/f?s=) is the only endpoint that
                # supports offset-based pagination; bare /f ignores ?o=.
                path = "/f?s="
            if offset > 0:
                path += f"&o={offset}"
            text = getit_fetch(path)
            entries, total, pg, total_pages = getit_parse_file_list(text)
            return (entries, total, total_pages)

        def _on_result(data):
            if _gen != host._getit_search_gen:
                return  # superseded by a newer search
            host._getit_search_loading = False
            entries, total, total_pages = data[0], data[1], data[2] or 1
            # The GetIt /f endpoint reports only the item count on the
            # current page as "total", never the full catalogue size, so
            # total_pages always computes to 1.
            # - For no-search browsing: always allow Next as long as we
            #   got any results (the endpoint doesn't expose a catalogue
            #   total, so we optimistically enable Next and let the next
            #   fetch return empty to signal the real end).
            # - For search queries: only allow Next when a full page was
            #   returned (the search endpoint does return a reliable total
            #   when results span multiple pages).
            if total_pages <= page and len(entries) > 0:
                if not query or len(entries) >= GETIT_PAGE_SIZE:
                    total_pages = page + 1
            host._getit_total_pages = total_pages
            getit_populate_results(entries, page, total_pages)
            getit_set_status(f"{total} result(s)  |  page {page}/{total_pages}")
            host.getit_search_button.setEnabled(True)
            host.getit_latest_button.setEnabled(True)
            if on_complete:
                on_complete()

        def _on_error(err):
            if _gen != host._getit_search_gen:
                return  # superseded by a newer search
            host._getit_search_loading = False
            getit_set_status(f"Error: {err[1]}")
            host.getit_search_button.setEnabled(True)
            host.getit_latest_button.setEnabled(True)
            if on_complete:
                on_complete()

        host._getit_search_thread = getit_run_in_thread(_search_fn, _on_result, _on_error)

    def _show_page(page: int):
        """Navigate to a page by re-running the search with the new page number."""
        getit_run_search(host._getit_last_query, page)

    def getit_on_search():
        getit_clear_detail()
        q = host.getit_search_input.text().strip()
        if q and len(q) < SEARCH_MIN_CHARS:
            return
        # Suppress the autocomplete suggestions popup once a search is
        # submitted; it stays hidden until the user types again.
        host._getit_ac_block = True
        try:
            host._getit_ac_timer.stop()
        except Exception:
            pass
        try:
            host._getit_completer.popup().hide()
        except Exception:
            pass
        if q:
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            def _getit_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, host.getit_results_table.rowCount())
            getit_run_search(q, 1, _getit_done)
        else:
            getit_run_search(q, 1)
        if _multi_search_enabled() and q:
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                host.zxdb_search_input.setText(q)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                host.zxart_search_input.setText(q)
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            _cross_search_zxdb(q)
            _cross_search_zxart(q)

    def getit_on_latest(on_complete=None):
        getit_clear_detail()
        host.getit_search_input.clear()
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
        def _getit_latest_done():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, host.getit_results_table.rowCount())
            if on_complete:
                on_complete()
        getit_run_search("", 1, _getit_latest_done)

    def getit_on_random(on_complete=None):
        import random as _random
        getit_clear_detail()
        host.getit_search_input.clear()
        host._getit_last_query = ""
        # Supersede any in-flight GetIt request.
        host._getit_search_gen += 1
        _gen = host._getit_search_gen
        host._getit_search_loading = True
        getit_set_status("Picking random GetIt entries…")
        host.getit_search_button.setEnabled(False)
        host.getit_latest_button.setEnabled(False)
        host.getit_random_button.setEnabled(False)
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)

        def _fn():
            # Probe the catalogue to find the number of pages, then load
            # a random one.  GetIt has no random endpoint, so we sample
            # client-side by page.  Use /f?s= (empty search) because bare
            # /f ignores offset params and doesn't return a catalogue total.
            text = getit_fetch("/f?s=")
            _entries, _total, _pg, total_pages = getit_parse_file_list(text)
            tp = max(1, (_total + GETIT_PAGE_SIZE - 1) // GETIT_PAGE_SIZE) if _total else 1
            page = _random.randint(1, tp)
            path = f"/f?s=&o={(page - 1) * GETIT_PAGE_SIZE}" if page > 1 else "/f?s="
            text2 = getit_fetch(path)
            entries, total, _pg2, tp2 = getit_parse_file_list(text2)
            tp2 = max(1, (_total + GETIT_PAGE_SIZE - 1) // GETIT_PAGE_SIZE) if _total else tp
            # Shuffle the page entries so consecutive random clicks differ
            # even when the random page repeats.
            _random.shuffle(entries)
            return (entries, total, page, tp2)

        def _on_ok(data):
            if _gen != host._getit_search_gen:
                return  # superseded by a newer search
            entries, total, page, total_pages = data
            host._getit_search_loading = False
            getit_populate_results(entries, page, total_pages)
            getit_set_status(
                f"{len(entries)} random entry(ies)  |  page {page}/{total_pages}"
            )
            host.getit_search_button.setEnabled(True)
            host.getit_latest_button.setEnabled(True)
            host.getit_random_button.setEnabled(True)
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, host.getit_results_table.rowCount())
            if on_complete:
                on_complete()

        def _on_err(err):
            if _gen != host._getit_search_gen:
                return  # superseded by a newer search
            host._getit_search_loading = False
            getit_set_status(f"Error: {err[1]}")
            host.getit_search_button.setEnabled(True)
            host.getit_latest_button.setEnabled(True)
            host.getit_random_button.setEnabled(True)
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT, host.getit_results_table.rowCount())
            if on_complete:
                on_complete()

        host._getit_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def getit_on_prev():
        getit_run_search(host._getit_last_query, max(1, host._getit_current_page - 1))

    def getit_on_next():
        getit_run_search(host._getit_last_query, min(host._getit_total_pages, host._getit_current_page + 1))

    host.getit_search_button.clicked.connect(getit_on_search)
    host.getit_latest_button.clicked.connect(getit_on_latest)
    host.getit_random_button.clicked.connect(getit_on_random)
    host.getit_search_input.returnPressed.connect(getit_on_search)
    host.getit_prev_button.clicked.connect(getit_on_prev)
    host.getit_next_button.clicked.connect(getit_on_next)

    # ---- GetIt autocomplete ----

    host._getit_ac_model = QStringListModel(host)
    _getit_completer = QCompleter(host._getit_ac_model, host)
    _getit_completer.setCompletionMode(QCompleter.PopupCompletion)
    _getit_completer.setCaseSensitivity(Qt.CaseInsensitive)
    _getit_completer.setFilterMode(Qt.MatchStartsWith)
    #_ensure_completer_is_movable(_getit_completer)
    # Ensure the popup follows the main window on Windows
    popup = _getit_completer.popup()
    if popup is not None:
        popup.setParent(host)
        # Use a non-grabbing tool window (NOT Qt.Popup) so the completer
        # popup that QLineEdit shows automatically on each keystroke never
        # performs the implicit Windows mouse/keyboard grab — that grab can
        # get stuck and leave the search box unclickable.  Mirrors the flags
        # used by _getit_safe_show_popup.
        popup.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                             | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus)
        popup.setFocusPolicy(Qt.NoFocus)
        popup.setAttribute(Qt.WA_ShowWithoutActivating)

    host._getit_completer = _getit_completer
    host.getit_search_input.setCompleter(_getit_completer)
    host._getit_popup_hider = _CompleterPopupHider(
        host.getit_search_input, _getit_completer, host)

    def _getit_safe_show_popup(q: str):
        """Show the GetIt completer popup without calling QCompleter.complete()."""
        try:
            if not host._search_autocomplete_on():
                return
            if getattr(host, "_getit_ac_block", False):
                return
            if not host.getit_search_input.hasFocus():
                return
            if host.getit_search_input.text().strip() != q:
                return
            if host._getit_ac_model.rowCount() == 0:
                return
            _getit_completer.setCompletionPrefix(q)
            popup = _getit_completer.popup()
            if popup is None:
                return
            # QCompleter's popup is a Qt::Popup window which on Windows
            # performs an implicit keyboard+mouse grab, stealing focus
            # from the line edit no matter what attributes we set.  Re-
            # parent it as a Qt::Tool window with WindowDoesNotAcceptFocus
            # so the OS never routes key events to it: the user can keep
            # typing while the suggestion list stays visible.
            try:
                popup.setParent(host.getit_search_input.window(),
                                Qt.Tool
                                | Qt.FramelessWindowHint
                                | Qt.WindowStaysOnTopHint
                                | Qt.WindowDoesNotAcceptFocus)
                popup.setFocusPolicy(Qt.NoFocus)
                popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
            except Exception:
                pass
            le = host.getit_search_input
            rect = le.rect()
            pos = le.mapToGlobal(rect.bottomLeft())
            popup.setMinimumWidth(le.width())
            popup.move(pos)
            popup.resize(le.width(), _popup_height_for(popup, host._getit_ac_model.rowCount()))
            popup.show()
        except RuntimeError:
            pass
        except Exception:
            pass

    def _getit_ac_update_model(text: str):
        """Filter the cached title list (off the UI thread) to those
        starting with *text* and update the completer model.  The actual
        filter+sort runs on a worker thread so typing remains responsive
        even when the cached catalog grows to thousands of entries.  A
        generation token is used to discard stale results that arrive
        after the user has typed more characters."""
        if not text:
            host._getit_ac_model.setStringList([])
            return
        host._getit_ac_filter_gen = getattr(host, "_getit_ac_filter_gen", 0) + 1
        gen = host._getit_ac_filter_gen
        # Snapshot the cache so the worker doesn't touch shared state.
        titles_snapshot = list(host._getit_ac_titles or [])
        tl = text.lower()

        def _fn():
            matches = sorted(
                (t for t in titles_snapshot if t.lower().startswith(tl)),
                key=str.lower,
            )
            return (gen, text, matches[:80])

        def _on_ok(result):
            rgen, rtext, matches = result
            if rgen != getattr(host, "_getit_ac_filter_gen", -1):
                return
            try:
                if host.getit_search_input.text().strip() != rtext:
                    return
            except RuntimeError:
                return
            host._getit_ac_model.setStringList(matches)
            if matches:
                QTimer.singleShot(0, lambda q=rtext: _getit_safe_show_popup(q))

        def _on_err(_err):
            pass

        getit_run_in_thread(_fn, _on_ok, _on_err)

    def _getit_ac_populate_cache(titles: list):
        """Called on the main thread once the full-catalog fetch completes."""
        host._getit_ac_titles = titles
        # Update the model for whatever text is already in the box.
        _getit_ac_update_model(host.getit_search_input.text().strip())

    def _getit_ac_fetch():
        """Background worker: fetch all titles from the GetIt catalog once."""
        results = []
        # Walk pages until we run out of entries.
        offset = 0
        while True:
            path = "/f?s=" if offset == 0 else f"/f?s=&o={offset}"
            try:
                raw = getit_fetch(path)
                entries, _total, _pg, _tp = getit_parse_file_list(raw)
            except Exception:
                break
            if not entries:
                break
            results.extend(e["title"] for e in entries if e.get("title"))
            if len(entries) < GETIT_PAGE_SIZE:
                break
            offset += GETIT_PAGE_SIZE
        return results

    def _getit_ac_start_fetch():
        """Kick off a one-time background fetch of all GetIt titles."""
        if host._getit_ac_loading or host._getit_ac_titles:
            return
        host._getit_ac_loading = True
        host._ac_anim_start(host.getit_search_input)

        def _on_ok(titles):
            host._getit_ac_loading = False
            host._ac_anim_stop(host.getit_search_input)
            _getit_ac_populate_cache(titles)
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("getit", "")
                except Exception:
                    pass

        def _on_err(_err):
            host._getit_ac_loading = False
            host._ac_anim_stop(host.getit_search_input)
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("getit", "")
                except Exception:
                    pass

        host._getit_ac_thread = getit_run_in_thread(
            _getit_ac_fetch, _on_ok, _on_err
        )

    # Expose the starter so the AllInOne pane can piggy-back on the
    # shared GetIt title cache and animate its own placeholder.
    host._getit_ac_start_fetch = _getit_ac_start_fetch

    # Debounce typing so we don't dispatch a worker thread on every
    # keystroke — pressing two letters in quick succession would
    # otherwise queue two filter jobs.
    _getit_ac_timer = QTimer(host)
    _getit_ac_timer.setSingleShot(True)
    _getit_ac_timer.setInterval(150)
    host._getit_ac_timer = _getit_ac_timer

    def _getit_ac_trigger():
        if not _search_autocomplete_on():
            host._getit_ac_model.setStringList([])
            return
        text = host.getit_search_input.text().strip()
        if not text:
            host._getit_ac_model.setStringList([])
            return
        if not host._getit_ac_titles:
            _getit_ac_start_fetch()
            return
        _getit_ac_update_model(text)

    _getit_ac_timer.timeout.connect(_getit_ac_trigger)

    def _getit_ac_on_text_changed(_text: str):
        # If the change was caused by selecting an item from the popup,
        # don't re-open the popup — that would re-steal focus and trap
        # the user (they couldn't even press Backspace afterwards).
        if getattr(host, "_getit_ac_suppress", False):
            host._getit_ac_suppress = False
            return
        # The user is typing again: re-enable autocomplete suggestions
        # that were suppressed after the last search submission.
        host._getit_ac_block = False
        _getit_ac_timer.start()

    host.getit_search_input.textChanged.connect(_getit_ac_on_text_changed)

    def _getit_ac_activated(selected: str):
        try:
            if selected:
                # Suppress the textChanged-driven popup re-open caused by
                # setText below.  Also hide any currently-visible popup.
                host._getit_ac_suppress = True
                host._getit_ac_timer.stop()
                try:
                    _getit_completer.popup().hide()
                except Exception:
                    pass
                host.getit_search_input.setText(selected)
        except Exception:
            pass
        getit_on_search()

    _getit_completer.activated.connect(_getit_ac_activated)

    def _getit_search_validate(text: str):
        t = text.strip()
        if not t:
            host._getit_search_valid_lbl.setVisible(False)
        elif len(t) < SEARCH_MIN_CHARS:
            host._getit_search_valid_lbl.setText('<font color="red">❌</font>')
            host._getit_search_valid_lbl.setToolTip(f"Searches must be at least {SEARCH_MIN_CHARS} characters long")
            host._getit_search_valid_lbl.setVisible(True)
        else:
            host._getit_search_valid_lbl.setText('<font color="green">✔</font>')
            host._getit_search_valid_lbl.setVisible(True)
    host.getit_search_input.textChanged.connect(_getit_search_validate)

    # ---- Row selection → fetch detail ----

    def getit_on_row_selected():
        rows = host.getit_results_table.selectedItems()
        if not rows:
            return
        row = host.getit_results_table.currentRow()
        entry_id = host.getit_results_table.item(row, 0).text()
        if not entry_id:
            return
        host._getit_selected_id = entry_id
        getit_set_status(f"Loading details for {entry_id}…")
        host.getit_download_button.setEnabled(False)
        host.getit_send_sd_button.setEnabled(False)
        host.getit_send_ns_button.setEnabled(False)
        host.getit_screenshot_label.setText("Loading…")
        host.getit_screenshot_label.setPixmap(QPixmap())

        def _scr_fn(eid=entry_id):
            url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
            tmp = tempfile.NamedTemporaryFile(suffix=".bmp", delete=False)
            tmp.close()
            urllib.request.urlretrieve(url, tmp.name)
            return tmp.name

        def _on_scr_done(path):
            px = QPixmap(path)
            os.unlink(path)
            if px.isNull():
                host.getit_screenshot_label.setText("No preview")
            else:
                host.getit_screenshot_label.setPixmap(
                    px.scaled(256, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )

        def _on_scr_error(err):
            host.getit_screenshot_label.setText("No preview")

        host._getit_scr_thread = getit_run_in_thread(_scr_fn, _on_scr_done, _on_scr_error)

        def _detail_fn():
            text   = getit_fetch(f"/nx/{entry_id}/f/")
            return getit_parse_detail(text)

        def _on_detail(d):
            getit_populate_detail(d)
            getit_set_status(f"Details loaded for {entry_id}")

        host._getit_detail_thread = getit_run_in_thread(
            _detail_fn, _on_detail,
            lambda err: getit_set_status(f"Detail error: {err[1]}")
        )

    host.getit_results_table.itemSelectionChanged.connect(getit_on_row_selected)

    def getit_on_gallery_cell(entry):
        eid = entry.get("id") or ""
        if not eid:
            return
        # Try to mirror selection in the table so existing detail logic runs once.
        for r in range(host.getit_results_table.rowCount()):
            item = host.getit_results_table.item(r, 0)
            if item is not None and item.text() == eid:
                host.getit_results_table.selectRow(r)
                break
        host.getit_gallery_view.select_entry(lambda _e, _e0=entry: _e is _e0)

    host.getit_gallery_view.cell_clicked.connect(getit_on_gallery_cell)

    def _getit_open_gallery_viewer(entry, make_viewer=None, install=True):
        eid   = entry.get("id") or ""
        title = entry.get("title") or eid
        if not eid:
            return None
        info_rows = [
            ("Title:",    title),
            ("Author:",   entry.get("author", "")),
            ("Category:", entry.get("category", "")),
            ("Size:",     entry.get("size", "")),
        ]
        scr_url = f"{GETIT_BASE_URL}/nx/{eid}/i/"
        # Compute a typed placeholder (same logic as gallery thumbnails)
        # so when the entry has no preview image the full-screen viewer
        # shows e.g. "TAP" / "TZX2TAP" in yellow on dark instead of a
        # blank pane.
        _ph_link  = host._getit_selected_link or ""
        _ph_ref   = _ph_link or title
        _ph_label = zxfmt_label_for_name(_ph_ref) if _ph_ref else "FILE"
        if _ph_label == "FILE":
            _ph_cat = (entry.get("category") or "").upper()
            if _ph_cat:
                _ph_label = _ph_cat[:6]
        _mk = make_viewer or (lambda **kw: GalleryItemViewer(
            parent=host, anim_mode_getter=lambda: host._gallery_anim_mode, **kw))
        viewer = _mk(
            title=title,
            info_rows=info_rows,
            screenshots=[scr_url],
            extra_fetch_cb=_getit_extra_fetch_url,
            tags=_gallery_extract_tags(entry),
        )
        if hasattr(viewer, "set_gif_fetch_cb"):
            viewer.set_gif_fetch_cb(_gif_fetch_bytes)
        viewer.set_placeholder(_ph_label, title)
        _fav_entry_getit = {**entry, "_fav_source": "getit"}
        viewer.set_favorite_hooks(_fav_entry_getit, host._fav_is, host._fav_toggle)
        # Fetch the GetIt detail (DESC + real LINK filename) so the Pygame
        # viewer can show readable text: if the entry's file is itself a
        # text file, surface the file; otherwise show its description.
        if getattr(viewer, "add_text_document", None) is not None:
            def _getit_text_fn(_eid=eid):
                return getit_parse_detail(getit_fetch(f"/nx/{_eid}/f/"))

            def _getit_text_ok(d, _eid=eid):
                link = (d.get("LINK") or "").strip()
                added = []
                if link.lower().endswith(_GALLERY_TEXT_EXTS):
                    added = _gallery_add_text_pages(viewer, [{
                        "url": f"{GETIT_BASE_URL}/nx/{_eid}/", "filename": link,
                    }])
                if not added:
                    _gallery_add_description_page(viewer, d.get("DESC"))

            host._getit_viewer_text_thread = getit_run_in_thread(
                _getit_text_fn, _getit_text_ok, lambda _e: None)

        # ── action buttons ──────────────────────────────────────────
        default_name = host._getit_selected_link or f"{eid}.bin"
        _safe_title  = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid
        _img_path    = host.right_disk_image_path or ""
        _img_label   = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                        ) if _img_path else ""
        _sd_dest     = f"{_img_path}  →  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base     = _getit_resolve_ns_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest     = os.path.join(_ns_base, _safe_title)
        _sd_ok       = bool(host.right_disk_image_path) and bool(_right_disk_content())

        def _dl():
            getit_do_download(eid, default_name)
        def _sd():
            _getit_send_to_image(eid, default_name, title)
        def _ns():
            def _after(_folder):
                QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
            _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after)

        viewer.set_actions(
            download_cb=_dl, send_sd_cb=_sd, send_ns_cb=_ns,
            sd_enabled=_sd_ok,  sd_tooltip=_sd_dest,
            ns_enabled=True,    ns_tooltip=_ns_dest,
        )
        host._wire_viewer_emulators(viewer)

        # ── push into pane stack ────────────────────────────────────
        if install:
            viewer.install_into_stack(
                host._getit_stack,
                close_fn=lambda: host._getit_stack.setCurrentIndex(0),
            )
        return viewer

    host.getit_gallery_view.cell_dbl_clicked.connect(
        lambda e: host._pane_open_item("getit", e, getattr(host, "_getit_item_retro", False)))

    def _getit_table_on_double_clicked(item):
        row = host.getit_results_table.currentRow()
        id_item = host.getit_results_table.item(row, 0)
        if id_item is None:
            return
        eid = id_item.text()
        if not eid:
            return
        # Prefer the fully populated entry from the cached list
        entry = next((e for e in host._getit_last_entries if e.get("id") == eid), None)
        if entry is None:
            entry = {
                "id":     eid,
                "title":  (host.getit_results_table.item(row, 1).text()
                           if host.getit_results_table.item(row, 1) else ""),
                "author": (host.getit_results_table.item(row, 2).text()
                           if host.getit_results_table.item(row, 2) else ""),
                "size":   (host.getit_results_table.item(row, 3).text()
                           if host.getit_results_table.item(row, 3) else ""),
            }
        host._pane_open_item("getit", entry, getattr(host, "_getit_item_retro", False))

    host.getit_results_table.itemDoubleClicked.connect(_getit_table_on_double_clicked)

    def _getit_apply_view_mode(mode: str, *, persist: bool = True):
        mode = (mode or "table").lower()
        if mode not in ("table", "gallery"):
            mode = "table"
        host._getit_view_mode = mode
        host.getit_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
        if getattr(host, "_pane_retro_gallery_refresh", None):
            host._pane_retro_gallery_refresh("getit")
        _table = (mode == "table")
        # Keep the right column visible in both modes so the zxnext.uk site
        # link stays shown (mirrors ZXDB/zxArt); only the preview screenshot
        # and its buttons are hidden in Gallery mode.
        if hasattr(host, '_getit_right_widget'):
            host._getit_right_widget.setVisible(True)
        if hasattr(host, '_getit_preview_label'):
            host._getit_preview_label.setVisible(_table)
        if hasattr(host, '_getit_preview_download_btn'):
            host._getit_preview_download_btn.setVisible(_table)
        if hasattr(host, '_getit_preview_send_sd_btn'):
            host._getit_preview_send_sd_btn.setVisible(_table)
        if hasattr(host, '_getit_preview_send_ns_btn'):
            host._getit_preview_send_ns_btn.setVisible(_table)
        # keep combo in sync without re-triggering
        cb = host.getit_view_combo
        target_idx = 1 if mode == "gallery" else 0
        if cb.currentIndex() != target_idx:
            cb.blockSignals(True)
            cb.setCurrentIndex(target_idx)
            cb.blockSignals(False)
        if persist:
            # sync other panes to the same view mode
            if hasattr(host, '_zxdb_apply_view_mode'):
                host._zxdb_apply_view_mode(mode, persist=False)
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

    host._getit_apply_view_mode = _getit_apply_view_mode

    def _on_getit_view_combo_changed(_idx):
        _getit_apply_view_mode(host.getit_view_combo.currentData() or "table")

    host.getit_view_combo.currentIndexChanged.connect(_on_getit_view_combo_changed)
    _getit_apply_view_mode(host._getit_view_mode, persist=False)

    # ---- Download file ----

    def getit_do_download(eid, default_name):
        getit_set_status(f"Preparing download for {eid}…")
        host.getit_download_button.setEnabled(False)

        def _probe_fn():
            """HEAD request to resolve the server-side filename before we ask
            the user where to save, so the dialog shows the correct extension."""
            url = f"{GETIT_BASE_URL}/nx/{eid}/"
            cd, _ = _http_fetch_with_cd_retry(
                url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=15
            )
            # Parse: attachment; filename=HeadOverHeels.tap
            real_name = ""
            for part in cd.split(";"):
                part = part.strip()
                if part.lower().startswith("filename="):
                    real_name = part[len("filename="):].strip().strip('"').strip("'")
                    break
            return real_name or os.path.basename(default_name) or f"{eid}.bin"

        def _on_probe_done(server_filename):
            host.getit_download_button.setEnabled(True)
            getit_set_status("")
            # Show save dialog with the server-provided filename as the default.
            save_path, _ = QFileDialog.getSaveFileName(
                None, "Save file", server_filename
            )
            if not save_path:
                return

            # Ensure the save path keeps the correct extension even if the
            # user typed a different name without an extension.
            server_ext = os.path.splitext(server_filename)[1]
            user_ext   = os.path.splitext(save_path)[1]
            if server_ext and not user_ext:
                save_path = save_path + server_ext

            getit_set_status(f"Downloading {eid}…")
            host.getit_download_button.setEnabled(False)

            def _dl_fn():
                url = f"{GETIT_BASE_URL}/nx/{eid}/"
                data = _http_fetch_bytes_with_retry(
                    url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
                )
                with open(save_path, "wb") as fh:
                    fh.write(data)
                return save_path

            def _on_dl_done(p):
                getit_set_status(f"Saved to {p}")
                host.getit_download_button.setEnabled(True)

            def _on_dl_error(err):
                getit_set_status(f"Download error: {err[1]}")
                host.getit_download_button.setEnabled(True)

            host._getit_dl_thread = getit_run_in_thread(_dl_fn, _on_dl_done, _on_dl_error)

        def _on_probe_error(err):
            # Fall back to the old behaviour if the probe fails.
            host.getit_download_button.setEnabled(True)
            getit_set_status("")
            fallback = os.path.basename(default_name) or f"{eid}.bin"
            save_path, _ = QFileDialog.getSaveFileName(None, "Save file", fallback)
            if not save_path:
                return
            getit_set_status(f"Downloading {eid}…")
            host.getit_download_button.setEnabled(False)

            def _dl_fn2():
                url = f"{GETIT_BASE_URL}/nx/{eid}/"
                data = _http_fetch_bytes_with_retry(
                    url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
                )
                with open(save_path, "wb") as fh:
                    fh.write(data)
                return save_path

            def _on_done2(p):
                getit_set_status(f"Saved to {p}")
                host.getit_download_button.setEnabled(True)

            def _on_err2(e2):
                getit_set_status(f"Download error: {e2[1]}")
                host.getit_download_button.setEnabled(True)

            host._getit_dl_thread = getit_run_in_thread(_dl_fn2, _on_done2, _on_err2)

        host._getit_probe_thread = getit_run_in_thread(_probe_fn, _on_probe_done, _on_probe_error)

    def getit_on_download():
        if not host._getit_selected_id:
            return
        getit_do_download(
            host._getit_selected_id,
            host._getit_selected_link or f"{host._getit_selected_id}.bin"
        )

    host.getit_download_button.clicked.connect(getit_on_download)

    def _getit_on_send_sd():
        if not host._getit_selected_id:
            return
        eid   = host._getit_selected_id
        title = host.getit_detail_title.text() or eid
        _getit_send_to_image(
            eid,
            host._getit_selected_link or f"{eid}.bin",
            title,
        )

    def _getit_on_send_ns():
        if not host._getit_selected_id:
            return
        eid          = host._getit_selected_id
        title        = host.getit_detail_title.text() or eid
        default_name = host._getit_selected_link or f"{eid}.bin"
        _ns_base     = _getit_resolve_ns_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)
        def _after(_folder):
            QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
        _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after)

    host.getit_send_sd_button.clicked.connect(_getit_on_send_sd)
    host.getit_send_ns_button.clicked.connect(_getit_on_send_ns)

    def _getit_resolve_ns_base_path(configured_path: str) -> str:
        """Return the NextSync root directory for local-copy sends."""
        p = (configured_path or "").strip().rstrip("/\\")
        if p:
            if os.path.isdir(p):
                return p
            parent = os.path.dirname(p)
            if parent and os.path.isdir(parent):
                return parent
        return os.path.abspath("downloads")

    def _getit_send_to_image(eid: str, default_name: str, title: str):
        """Download the GetIt entry to a temp file then hdfmonkey-put it into the
        currently loaded disk image at the current browse path."""
        if not _right_disk_content():
            getit_set_status("Please load a disk image first (SD Card tab).")
            return
        if not host.right_disk_image_path:
            getit_set_status("No disk image loaded.")
            return

        safe_name = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid
        fname     = os.path.basename(default_name) if default_name else f"{eid}.bin"
        img_dir   = (generate_disk_file_path().rstrip("/") + "/" + safe_name).replace("//", "/")
        img_dest  = (img_dir + "/" + fname).replace("//", "/")
        url       = f"{GETIT_BASE_URL}/nx/{eid}/"
        image_path = host.right_disk_image_path

        getit_set_status(f"Sending {eid} → image:{img_dest}…")

        def _dl_and_put():
            # Resolve the server filename so we use the correct extension.
            _cd, _data = _http_fetch_with_cd_retry(
                url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
            )
            _real = ""
            for _part in _cd.split(";"):
                _part = _part.strip()
                if _part.lower().startswith("filename="):
                    _real = _part[len("filename="):].strip().strip('"').strip("'")
                    break
            _use_fname = _real or fname
            tmp = tempfile.NamedTemporaryFile(suffix="_" + _use_fname, delete=False)
            tmp.close()
            try:
                with open(tmp.name, "wb") as _fh:
                    _fh.write(_data)
                # Update dest path with real filename
                nonlocal img_dest
                img_dest = (img_dir + "/" + _use_fname).replace("//", "/")
                 # Create the sub-directory in the image (ignore errors — may already exist)
                execute_hdf_monkey("mkdir", image_path, extra_argv=[img_dir], silent=True)
                # Upload the file into the image
                result = execute_hdf_monkey("put", image_path,
                                           extra_argv=[tmp.name.replace("\\", "/"), img_dest])
                if result.returncode != 0:
                    raise RuntimeError(f"hdfmonkey put failed (rc={result.returncode})")
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            return img_dest

        def _on_done(dest):
            getit_set_status(f"Sent to image: {dest}")
            host._show_sd_notification(f"Sent to SD card image:\n{dest}")
            # Refresh the disk image table so the new folder appears (the
            # listing runs on a worker thread, so it never blocks the UI).
            update_disk_manager_widget_table()

        def _on_err(err):
            getit_set_status(f"Send to image failed: {err[1]}")

        getit_run_in_thread(_dl_and_put, _on_done, _on_err)

    def _getit_send_to_ns_folder(eid: str, default_name: str, dest_root: str,
                                 title: str, post_action=None):
        """Download the GetIt entry into dest_root/{sanitized_title}/ on the local
        filesystem (used for NextSync sends)."""
        safe_folder = re.sub(r'[<>:"/\\|?*]', "", title or default_name or eid).strip() or eid
        folder      = os.path.join(dest_root, safe_folder)
        os.makedirs(folder, exist_ok=True)
        fname       = os.path.basename(default_name) if default_name else f"{eid}.bin"
        save_path   = os.path.join(folder, fname or f"{eid}.bin")
        url         = f"{GETIT_BASE_URL}/nx/{eid}/"
        getit_set_status(f"Sending {eid} → {folder}…")

        def _dl_fn():
            cd, data = _http_fetch_with_cd_retry(
                url, headers={"User-Agent": GETIT_USER_AGENT}, timeout=60
            )
            real = ""
            for part in cd.split(";"):
                part = part.strip()
                if part.lower().startswith("filename="):
                    real = part[len("filename="):].strip().strip('"').strip("'")
                    break
            use_fname = real or fname
            dest = os.path.join(folder, use_fname)
            with open(dest, "wb") as fh:
                fh.write(data)
            return dest

        def _on_done(p):
            getit_set_status(f"Sent → {p}")
            if post_action:
                post_action(folder)

        def _on_err(err):
            getit_set_status(f"Send error: {err[1]}")

        getit_run_in_thread(_dl_fn, _on_done, _on_err)

    # ---- Context menu on results table ----

    def getit_on_table_context_menu(pos):
        item = host.getit_results_table.itemAt(pos)
        if item is None:
            return
        row = host.getit_results_table.row(item)
        eid_item   = host.getit_results_table.item(row, 0)
        title_item = host.getit_results_table.item(row, 1)
        if not eid_item:
            return
        eid   = eid_item.text()
        title = title_item.text() if title_item else eid
        default_name = host._getit_selected_link or f"{eid}.bin"

        _safe_title = re.sub(r'[<>:"/\\|?*]', "", title).strip() or eid

        # SD card: destination is inside the currently loaded disk image
        _img_path  = host.right_disk_image_path or ""
        _img_label = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                     ) if _img_path else "(no image loaded)"
        _sd_dest   = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"

        # NextSync: destination is a sub-folder inside the NextSync root on the local filesystem
        _ns_base = _getit_resolve_ns_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest = os.path.join(_ns_base, _safe_title)

        menu = QMenu(host.getit_results_table)
        act_dl      = menu.addAction(f'Download \u201c{title}\u201d')
        menu.addSeparator()
        act_send_sd = menu.addAction(f"Send to SD card (image)  →  {_sd_dest}")
        act_send_sd.setEnabled(bool(host.right_disk_image_path) and bool(_right_disk_content()))
        act_send_ns = menu.addAction(f"Send using NextSync  →  {_ns_dest}")
        chosen = menu.exec(host.getit_results_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        host.getit_results_table.selectRow(row)
        if chosen is act_dl:
            getit_do_download(eid, default_name)
        elif chosen is act_send_sd:
            _getit_send_to_image(eid, default_name, title)
        elif chosen is act_send_ns:
            def _after_ns_dl_gi(_folder):
                QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
            _getit_send_to_ns_folder(eid, default_name, _ns_base, title, _after_ns_dl_gi)

    # (found by ruff B018: the call parens were missing, so the policy was
    # never applied and the connected context menu could not fire)
    host.getit_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
    host.getit_results_table.customContextMenuRequested.connect(getit_on_table_context_menu)

    # ---- MOTD fetch ----

    def getit_fetch_motd():
        if host._getit_motd_loaded or host._getit_motd_loading:
            return
        host._getit_motd_loading = True

        def _motd_fn():
            return getit_fetch("/motd2.txt").strip()

        def _on_motd(t):
            host._getit_motd_loading = False
            host._getit_motd_loaded = True
            host.getit_motd_text.setText(t)

        def _on_motd_error(err):
            host._getit_motd_loading = False
            host.getit_motd_text.setText(f"(MOTD unavailable: {err[1]})")

        host._getit_motd_thread = getit_run_in_thread(_motd_fn, _on_motd, _on_motd_error)

    # Store for on_tab_changed wiring below
    host._getit_fetch_motd = getit_fetch_motd
    host._getit_on_latest  = getit_on_latest

    getit_container = QWidget()
    getit_container.setLayout(host.getit_form)
    getit_container.setAutoFillBackground(False)
    getit_container.setAttribute(Qt.WA_TranslucentBackground)

    # Wrap in scroll area here so the stack owns the scroll area, not the bare container
    getit_scroll = QScrollArea()
    getit_scroll.setWidget(getit_container)
    getit_scroll.setWidgetResizable(True)
    getit_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    getit_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    getit_scroll.setAutoFillBackground(False)
    getit_scroll.setAttribute(Qt.WA_TranslucentBackground)
    getit_scroll.viewport().setAutoFillBackground(False)
    getit_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

    # Compose a fixed search/button header above the scrollable results so
    # the vertical scroller only covers the content area (like the Unite!
    # tab), instead of spanning the whole tab including the button bar.
    getit_normal_widget = QWidget()
    getit_normal_widget.setAutoFillBackground(False)
    getit_normal_widget.setAttribute(Qt.WA_TranslucentBackground)
    getit_normal_layout = QVBoxLayout(getit_normal_widget)
    getit_normal_layout.setContentsMargins(0, 0, 0, 0)
    getit_normal_layout.setSpacing(0)
    getit_normal_layout.addWidget(host._getit_search_widget, 0)
    getit_normal_layout.addWidget(getit_scroll, 1)

    # ---- Fullscreen preview overlay ----
    host._getit_fullscreen_pixmap = None

    getit_overlay = QWidget()
    getit_overlay.setStyleSheet("background: #000;")
    getit_overlay_layout = QVBoxLayout(getit_overlay)
    getit_overlay_layout.setContentsMargins(0, 0, 0, 0)
    getit_overlay_layout.setSpacing(0)

    getit_close_btn = QToolButton()
    getit_close_btn.setText("✕")
    getit_close_btn.setStyleSheet(
        "QToolButton { color: white; background: #333; border: none; font-size: 18px; padding: 4px 8px; }"
        "QToolButton:hover { background: #c00; }"
    )
    getit_close_bar = QHBoxLayout()
    getit_close_bar.setContentsMargins(4, 4, 4, 0)
    getit_close_bar.addWidget(getit_close_btn, 0)
    getit_close_bar.addStretch()
    getit_close_bar_widget = QWidget()
    getit_close_bar_widget.setLayout(getit_close_bar)
    getit_overlay_layout.addWidget(getit_close_bar_widget, 0)

    host.getit_fullscreen_label = QLabel()
    host.getit_fullscreen_label.setAlignment(Qt.AlignCenter)
    host.getit_fullscreen_label.setStyleSheet("background: #000;")
    host.getit_fullscreen_label.setCursor(Qt.PointingHandCursor)
    getit_overlay_layout.addWidget(host.getit_fullscreen_label, 1)

    host._getit_stack = QStackedWidget()
    host._getit_stack.setAutoFillBackground(False)
    host._getit_stack.setAttribute(Qt.WA_TranslucentBackground)
    host._getit_stack.addWidget(getit_normal_widget)   # index 0 – normal view
    host._getit_stack.addWidget(getit_overlay)  # index 1 – fullscreen preview
    host._getit_stack.setCurrentIndex(0)

    def _getit_show_fullscreen():
        px = host.getit_screenshot_label.pixmap()
        if px is None or px.isNull():
            return
        host._getit_fullscreen_pixmap = px
        host._getit_stack.setCurrentIndex(1)
        _getit_resize_fullscreen()

    def _getit_hide_fullscreen():
        host._getit_stack.setCurrentIndex(0)
    host._hide_fullscreen_getit = _getit_hide_fullscreen

    def _getit_resize_fullscreen():
        px = host._getit_fullscreen_pixmap
        if px and not px.isNull():
            sz = host.getit_fullscreen_label.size()
            host.getit_fullscreen_label.setPixmap(
                px.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    getit_close_btn.clicked.connect(_getit_hide_fullscreen)
    host.getit_fullscreen_label.mousePressEvent = lambda e: _getit_hide_fullscreen()

    # Intercept double-click on the thumbnail via an event filter
    # (_DblClickFilter now lives in zxnu_gallery, shared by all gallery panes)
    host._getit_dbl_filter = _DblClickFilter(_getit_show_fullscreen)
    host.getit_screenshot_label.installEventFilter(host._getit_dbl_filter)
    host.getit_screenshot_label.setCursor(Qt.PointingHandCursor)
    host.getit_run_search = getit_run_search
    host.getit_on_latest = getit_on_latest
    host.getit_on_random = getit_on_random
    host._getit_open_gallery_viewer = _getit_open_gallery_viewer
