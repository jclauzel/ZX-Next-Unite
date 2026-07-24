"""zxnu_zxart_pane.py — zxART (zxart.ee API) gallery pane builder.

Strangler extraction from MainWindow.__init__: the ~3k-line zxART (zxart.ee API) UI
construction blob (widgets + navigation + search/detail/download closures) now
lives here as build_zxart_pane(host, ...). The operation-layer wiring that still
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

from PySide6.QtCore import (Qt, QTimer, QStringListModel, QMetaObject, Q_ARG)
from PySide6.QtGui import (QPixmap)
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QComboBox,
    QLineEdit, QFormLayout, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QToolButton, QMenu, QCompleter,
    QFileDialog, QInputDialog, QDialog)

from zxnu_config import *
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *
# Star imports skip underscore-prefixed names; import the private
# helpers the block uses explicitly (tests/test_pane_imports.py
# tripwires that these lists stay complete).
from zxnu_config import (_zxart_lang, _zxart_set_language, _zxart_tr)
from zxnu_api import (_filter_download_urls, _http_fetch_bytes_with_retry,
    _http_head_ok_with_retry, _zxart_author_col_cached,
    _zxart_prefetch_names_for_entries, _zxart_resolve_author_name,
    _zxart_resolve_author_names, _zxart_resolve_group_name,
    _zxart_resolve_group_names, _zxart_resolve_publisher_names,
    _zxart_scrape_publishers_from_prod_url)
from zxnu_gallery import (_ScalingImageLabel, _gallery_viewer_refresh_meta,
    _gallery_stars)
from zxnu_media import (_ZXSCR_PIXMAP_CACHE, _build_tooltip_text,
    _gallery_extract_tags, _zxscr_basename_for_url)


def build_zxart_pane(
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
    _cross_search_zxdb,
    _right_disk_content,
):
    # -----------------------------------------------------------------------
    # zxART UI construction (zxart.ee API)
    # -----------------------------------------------------------------------

    host.zxart_form = QFormLayout()
    host.zxart_form.setContentsMargins(4, 4, 4, 4)

    # --- Search row (wraps onto extra rows when the window is narrow) ---
    zxart_search_row = FlowLayout(margin=2)
    host.zxart_search_input = QLineEdit()
    host.zxart_search_input.setPlaceholderText("Search zxART productions... (leave empty to browse latest)")
    host.zxart_search_input.setMinimumWidth(280)
    host.zxart_search_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    zxart_search_row.addWidget(host.zxart_search_input)

    host._zxart_search_valid_lbl = QLabel()
    host._zxart_search_valid_lbl.setVisible(False)
    zxart_search_row.addWidget(host._zxart_search_valid_lbl)

    host.zxart_search_button = QPushButton(_zxart_tr("Search"))
    zxart_search_row.addWidget(host.zxart_search_button)

    host.zxart_latest_button = QPushButton(_zxart_tr("Latest"))
    host.zxart_latest_button.setToolTip(
        "Show the most recent zxART productions/pictures (sorted by date)."
    )
    zxart_search_row.addWidget(host.zxart_latest_button)

    host.zxart_random_button = QPushButton(_zxart_tr("Random"))
    host.zxart_random_button.setToolTip(
        "Pick a random page of zxART productions and show its entries."
    )
    zxart_search_row.addWidget(host.zxart_random_button)

    host.zxart_mode_combo = QComboBox()
    for _lbl, _key in (
        ("Productions",  "prods"),
        ("By letter",    "byletter"),
        ("Pictures",     "pictures"),
    ):
        host.zxart_mode_combo.addItem(_zxart_tr(_lbl), _key)
    host.zxart_mode_combo.setCurrentIndex(0)
    host.zxart_mode_combo.setToolTip("Browse mode")
    zxart_search_row.addWidget(host.zxart_mode_combo)

    host.zxart_letter_combo = QComboBox()
    for _lbl in ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
        host.zxart_letter_combo.addItem(_lbl, _lbl.lower())
    host.zxart_letter_combo.setToolTip("Pick a letter")
    host.zxart_letter_combo.setVisible(False)
    zxart_search_row.addWidget(host.zxart_letter_combo)

    host.zxart_page_text_label = QLabel(_zxart_tr("Page:"))
    zxart_search_row.addWidget(host.zxart_page_text_label)
    host.zxart_page_label = QLabel("1")
    host.zxart_page_label.setMinimumWidth(24)
    zxart_search_row.addWidget(host.zxart_page_label)

    host.zxart_prev_button = QPushButton(_zxart_tr("< Prev"))
    host.zxart_prev_button.setEnabled(False)
    zxart_search_row.addWidget(host.zxart_prev_button)

    host.zxart_next_button = QPushButton(_zxart_tr("Next >"))
    host.zxart_next_button.setEnabled(False)
    zxart_search_row.addWidget(host.zxart_next_button)

    host.zxart_view_text_label = QLabel(_zxart_tr("View:"))
    zxart_search_row.addWidget(host.zxart_view_text_label)
    host.zxart_view_combo = QComboBox()
    host.zxart_view_combo.addItem(_zxart_tr("Table"),   "table")
    host.zxart_view_combo.addItem(_zxart_tr("Gallery"), "gallery")
    host.zxart_view_combo.setToolTip(
        "Switch between the classic table view and the picture (gallery) view.\n"
        "Persisted across sessions in the config file."
    )
    zxart_search_row.addWidget(host.zxart_view_combo)
    host.zxart_retro_button = _make_retro_toggle_button(
        host, "_zxart_item_retro",
        on_change=lambda c, k=SETTING_ZXART_ITEM_RETRO: (
            _persist_retro(k, c), host._pane_retro_gallery_set("zxart", c)))
    zxart_search_row.addWidget(host.zxart_retro_button)

    host.zxart_language_text_label = QLabel(_zxart_tr("Language:"))
    zxart_search_row.addWidget(host.zxart_language_text_label)
    host.zxart_language_combo = QComboBox()
    for _lbl, _code in ZXART_LANGUAGE_CHOICES:
        host.zxart_language_combo.addItem(_lbl, _code)
    host.zxart_language_combo.setToolTip(
        "zxART catalog display language.\n"
        "Persisted across sessions in the config file."
    )
    zxart_search_row.addWidget(host.zxart_language_combo)

    host.zxart_status_label = QLabel("")
    host.zxart_status_label.setCursor(Qt.ArrowCursor)
    host._zxart_status_open_path = None

    def _zxart_status_mouse_press(ev):
        if ev.button() == Qt.LeftButton and host._zxart_status_open_path:
            p = host._zxart_status_open_path
            if os.path.isfile(p):
                p = os.path.dirname(p)
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

    host.zxart_status_label.mousePressEvent = _zxart_status_mouse_press
    zxart_search_row.addWidget(host.zxart_status_label)

    zxart_search_widget = _wrap_flow_row(zxart_search_row)
    # Keep the search/button bar fixed above the scroll area (see the
    # _zxart_stack assembly) so the vertical scroller only covers the
    # results/details area, matching the Unite! tab.
    host._zxart_search_widget = zxart_search_widget

    # --- Results table + screenshot/download column ---
    host.zxart_results_table = QTableWidget(0, 6)
    host.zxart_results_table.setHorizontalHeaderLabels(
        [_zxart_tr(h) for h in ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"]]
    )
    host.zxart_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    host.zxart_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    host.zxart_results_table.horizontalHeader().setStretchLastSection(True)
    host.zxart_results_table.setMinimumHeight(220)
    host.zxart_results_table.setMaximumWidth(1000)
    host.zxart_results_table.setColumnWidth(0, 80)
    host.zxart_results_table.setColumnWidth(1, 280)
    host.zxart_results_table.setColumnWidth(2, 60)
    host.zxart_results_table.setColumnWidth(3, 180)
    host.zxart_results_table.setColumnWidth(4, 120)

    host.zxart_screenshot_label = _ScalingImageLabel()
    host.zxart_screenshot_label.setFixedSize(256, 192)
    host.zxart_screenshot_label.setAlignment(Qt.AlignCenter)
    host.zxart_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
    host.zxart_screenshot_label.setText(_zxart_tr("No preview"))
    host.zxart_screenshot_label.setToolTip("Double-click to enlarge")

    zxart_preview_container = QWidget()
    zxart_preview_container.setFixedSize(256, 192)
    host.zxart_screenshot_label.setParent(zxart_preview_container)
    host.zxart_screenshot_label.move(0, 0)

    _zxart_nav_btn_style = (
        "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
        " font-size: 20px; font-weight: bold; padding: 2px 6px; }"
        "QToolButton:hover { background: rgba(0,0,0,210); }"
    )
    host.zxart_prev_shot_btn = QToolButton(zxart_preview_container)
    host.zxart_prev_shot_btn.setText("<")
    host.zxart_prev_shot_btn.setStyleSheet(_zxart_nav_btn_style)
    host.zxart_prev_shot_btn.setVisible(False)
    host.zxart_prev_shot_btn.raise_()

    host.zxart_next_shot_btn = QToolButton(zxart_preview_container)
    host.zxart_next_shot_btn.setText(">")
    host.zxart_next_shot_btn.setStyleSheet(_zxart_nav_btn_style)
    host.zxart_next_shot_btn.setVisible(False)
    host.zxart_next_shot_btn.raise_()

    def _zxart_reposition_shot_btns():
        h = zxart_preview_container.height()
        bh = host.zxart_prev_shot_btn.sizeHint().height()
        by = (h - bh) // 2
        host.zxart_prev_shot_btn.move(2, by)
        bw = host.zxart_next_shot_btn.sizeHint().width()
        host.zxart_next_shot_btn.move(zxart_preview_container.width() - bw - 2, by)

    _zxart_reposition_shot_btns()

    host.zxart_download_button = QPushButton(_zxart_tr("Download File"))
    host.zxart_download_button.setEnabled(False)

    zxart_right_col = QVBoxLayout()
    _zxart_link_label = QLabel('<a href="https://zxart.ee/">https://zxart.ee/</a>')
    _zxart_link_label.setOpenExternalLinks(True)
    _zxart_link_label.setTextFormat(Qt.RichText)
    _zxart_link_label.setAlignment(Qt.AlignCenter)
    zxart_right_col.addWidget(_zxart_link_label)
    # Visibility is controlled by _zxart_apply_view_mode (shown in Table, hidden in Gallery)
    zxart_preview_container.setVisible(False)
    zxart_right_col.addWidget(zxart_preview_container)
    host._zxart_preview_container = zxart_preview_container

    host.zxart_download_button.setVisible(False)
    zxart_right_col.addWidget(host.zxart_download_button)
    host._zxart_preview_download_btn = host.zxart_download_button
    zxart_right_col.addStretch()
    zxart_right_widget = QWidget()
    zxart_right_widget.setLayout(zxart_right_col)

    zxart_table_row = QHBoxLayout()

    host.zxart_view_stack = QStackedWidget()
    host.zxart_view_stack.addWidget(host.zxart_results_table)  # index 0

    def _zxart_gallery_title(e):
        title = (e.get("title") or e.get("id") or "")[:80]
        src = e.get("_source") or {}
        # Prods expose "votes" (avg 0–5); pictures expose "rating" (0–10).
        rating_val = src.get("votes")
        if rating_val in (None, "", 0, "0"):
            rating_val = src.get("rating")
        stars = _gallery_stars(rating_val) if rating_val not in (None, "") else ""
        if stars:
            # Title gets rich text so we can show stars on a second line.
            safe = (title.replace("&", "&amp;")
                          .replace("<", "&lt;")
                          .replace(">", "&gt;"))
            return f"{safe}<br><span style='color:#ffcc44;'>{stars}</span>"
        return title
    def _zxart_gallery_info(e):
        parts = []
        if e.get("author"):  parts.append(e["author"])
        if e.get("year"):    parts.append(str(e["year"]))
        if e.get("machine"): parts.append(e["machine"])
        if e.get("genre"):   parts.append(e["genre"])
        return " · ".join(parts)

    def _zxart_fav_table_info(e):
        """Richer info string for the Favorites table / gallery: resolves
        Produced by / Published by.

        Runs on the UI thread, so it must NOT perform any network I/O. It
        reads only the in-memory caches; on a cold cache it returns a
        best-effort string and schedules a background warm-up so the value
        improves on the next repopulate."""
        src  = e.get("_source") or {}
        kind = (e.get("_kind") or "").lower()
        if kind == "zxart_picture" or not src:
            # Pictures and entries without cached source: fall back to author · year
            parts = []
            if e.get("author"): parts.append(str(e["author"]))
            if e.get("year"):   parts.append(str(e["year"]))
            return " · ".join(parts)
        text, complete = _zxart_author_col_cached(e)
        if not complete:
            # Warm the caches off the UI thread; no cell handle to refresh
            # here, so the resolved value surfaces on the next repopulate.
            getit_run_in_thread(
                lambda _e=e: _zxart_prefetch_names_for_entries([_e]),
                lambda _r: None, lambda _err: None)
        if text:
            return text
        parts = []
        if e.get("author"): parts.append(str(e["author"]))
        if e.get("year"):   parts.append(str(e["year"]))
        return " · ".join(parts)

    def _zxart_table_author_col(e):
        """Resolve 'Produced by / Published by' for the table column.
        Mirrors the logic of _zxart_fav_table_info: resolves group and
        publisher IDs via the API (with process-level caching) so the
        column shows real names instead of raw counts like '3 author(s)'."""
        src  = e.get("_source") or {}
        kind = (e.get("_kind") or "").lower()
        if kind == "zxart_picture":
            return e.get("author", "")
        # 1. Groups: prefer direct name strings from the API response,
        #    then resolve IDs via the API (cached after first lookup).
        groups = [str(g) for g in (src.get("groups") or []) if g]
        if not groups:
            groups = [n for n in [_zxart_resolve_group_name(gid)
                                  for gid in (src.get("groupsIds") or [])] if n]
        produced_by = ", ".join(groups)
        # 2. Authors: direct name strings when no groups
        if not produced_by:
            authors = [str(a) for a in (src.get("authors") or []) if a]
            if authors:
                return ", ".join(authors)
        # 3. Publishers: resolve via the API (publishers reuse group namespace)
        pub_ids = src.get("publishersIds") or []
        published_by = _zxart_resolve_publisher_names(pub_ids)
        if not published_by:
            published_by = _zxart_scrape_publishers_from_prod_url(
                str(src.get("url") or "")
            )
        parts = []
        if produced_by:  parts.append(f"Produced by: {produced_by}")
        if published_by: parts.append(f"Published by: {published_by}")
        return " · ".join(parts) if parts else e.get("author", "")

    def _zxart_tooltip_getter(e):
        src = e.get("_source") or {}
        lines = []
        if e.get("title"):   lines.append(f"Title: {e['title']}")
        if e.get("year"):    lines.append(f"Year: {e['year']}")
        if e.get("author"):  lines.append(f"Author: {e['author']}")
        if e.get("machine"): lines.append(f"Machine: {e['machine']}")
        if e.get("genre"):   lines.append(f"Genre: {e['genre']}")
        party = src.get("partyName") or src.get("party")
        if party:            lines.append(f"Party: {party}")
        return _build_tooltip_text(lines)

    def _zxart_thumb_fetch(entry, set_pixmap, set_screenshots, set_tags=None):
        src = entry.get("_source") or {}
        kind = (entry.get("_kind") or "").lower()
        # Pictures expose imageUrl directly; prods carry imagesUrls list.
        # zxArt's API guarantees these are image URLs (even when the
        # path has no extension like /file/id:123/), so we trust the
        # upstream field rather than re-filtering by extension.
        urls = []
        if kind == "zxart_picture":
            u = src.get("imageUrl") or src.get("originalUrl") or ""
            if u:
                urls.append(u)
        else:
            for u in (src.get("imagesUrls") or []):
                if u:
                    urls.append(u)

        # Apply tags we can derive immediately (pictures + any cached
        # release info), then start an async release lookup for prods
        # so we can show hardware/format badges like on zxart.ee.
        if set_tags is not None:
            try:
                set_tags(_gallery_extract_tags(entry))
            except Exception:
                pass
            if kind != "zxart_picture" and not src.get("releases"):
                pid = str(entry.get("id") or "")
                if pid:
                    def _rel_fn(_pid=pid):
                        resp = zxart_fetch_json(
                            f"/action:filter/export:zxRelease"
                            f"/filter:zxProdId={urllib.parse.quote(_pid)}",
                            timeout=20,
                        )
                        return (resp.get("responseData") or {}).get("zxRelease") or []
                    def _rel_ok(rels, _e=entry, _st=set_tags):
                        try:
                            src2 = _e.get("_source") or {}
                            src2["releases"] = rels
                            _e["_source"] = src2
                            _st(_gallery_extract_tags(_e))
                        except Exception:
                            pass
                    getit_run_in_thread(_rel_fn, _rel_ok, lambda _e: None, gated=True)

        if not urls:
            # No real picture for this entry: render a typed placeholder
            # using release formats / known download names so the cell
            # shows e.g. "TAP" or "POK" instead of a black square.
            label = "FILE"
            formats = []
            rf = src.get("releaseFormats") or []
            if isinstance(rf, list):
                formats.extend([str(x) for x in rf if x])
            for rel in (src.get("releases") or []):
                if not isinstance(rel, dict):
                    continue
                v = rel.get("releaseFormat")
                if isinstance(v, list):
                    formats.extend([str(x) for x in v if x])
                elif v:
                    formats.append(str(v))
                fn = rel.get("fileName") or ""
                if fn:
                    label = zxfmt_label_for_name(fn)
                    break
            if label == "FILE" and formats:
                label = zxfmt_label_for_name("x." + formats[0].lower())
            title = entry.get("title") or str(entry.get("id") or "")
            placeholder_url = f"placeholder://{label}/{title}"
            set_screenshots([placeholder_url])
            pm = zxfmt_make_placeholder_pixmap(label, title)
            if not pm.isNull():
                set_pixmap(pm, placeholder_url)
            return
        set_screenshots(urls)
        def _img_fn(_u=urls[0]):
            data = _http_fetch_bytes_with_retry(
                zxart_safe_url(_u), headers={"User-Agent": ZXART_USER_AGENT}, timeout=20)
            # Decode off the UI thread for every format — including SCR,
            # whose (now buffer-based) decode produces a QImage that is safe
            # to build on a worker thread. Only the cheap QPixmap.fromImage()
            # runs back on the UI thread.
            if zxscr_url_is_scr(_u):
                img = zxscr_qimage_from_bytes(data, _zxscr_basename_for_url(_u))
            else:
                img = _qimage_from_data(data)
            return (_u, img)
        def _img_ok(res):
            u, img = res
            px = QPixmap.fromImage(img) if (img is not None and not img.isNull()) else QPixmap()
            if not px.isNull():
                set_pixmap(px, u)
        getit_run_in_thread(_img_fn, _img_ok, lambda _e: None, gated=True)

    def _zxart_extra_fetch(url, on_pixmap):
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
                zxart_safe_url(_u), headers={"User-Agent": ZXART_USER_AGENT}, timeout=20)
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

    def _zxart_gallery_context_menu(entry, global_pos):
        pid   = entry.get("id") or ""
        title = entry.get("title") or pid
        kind  = entry.get("_kind", "zxart_prod")
        _safe_title = zxart_sanitize_folder(title)
        _img_path   = host.right_disk_image_path or ""
        _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                       ) if _img_path else "(no image loaded)"
        _sd_dest    = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base    = _zxart_resolve_base_path(host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest    = os.path.join(_ns_base, _safe_title)
        menu = QMenu()
        act_download = menu.addAction("Download content")
        menu.addSeparator()
        act_send_sd  = menu.addAction(f"Send to SD card (image)  \u2192  {_sd_dest}")
        act_send_sd.setEnabled(bool(host.right_disk_image_path) and bool(_right_disk_content()))
        act_send_ns  = menu.addAction(f"Send using NextSync  \u2192  {_ns_dest}")
        if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
            act_download.setVisible(False)
            act_send_sd.setVisible(False)
            act_send_ns.setVisible(False)
        menu.addSeparator()
        _web_url = zxart_entry_website_url(entry)
        act_open_web = menu.addAction("Open on website (zxart.ee)")
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

        def _ensure_detail_then(callback):
            if host._zxart_selected_id == pid and host._zxart_selected_downloads:
                callback(host._zxart_selected_title or title, host._zxart_selected_downloads)
                return
            zxart_set_status(f"Loading {pid}\u2026")
            if kind == "zxart_picture":
                def _fn():
                    pic_resp = zxart_fetch_json(
                        f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(pid)}"
                    )
                    pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                    pic  = pics[0] if pics else (entry.get("_source") or {})
                    image_url    = pic.get("imageUrl") or ""
                    original_url = pic.get("originalUrl") or ""
                    downloads = []
                    if original_url:
                        fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{pid}.bin"
                        downloads.append({"url": original_url, "filename": fname,
                                          "type": "original", "format": "", "size": "", "source": "zxart"})
                    if image_url and image_url != original_url:
                        fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{pid}.png"
                        downloads.append({"url": image_url, "filename": fname_img,
                                          "type": "preview (PC)", "format": "", "size": "", "source": "zxart"})
                    return (str(pic.get("title") or title), downloads)
                def _on_ok(res, _cb=callback):
                    t2, dls = res
                    host._zxart_selected_title = t2
                    host._zxart_selected_downloads = dls
                    host.zxart_download_button.setEnabled(bool(dls))
                    _cb(t2, dls)
                def _on_err(err):
                    zxart_set_status(f"Detail error: {err[1]}")
                host._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
            else:
                def _fn():
                    rel_resp = zxart_fetch_json(
                        f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(pid)}"
                    )
                    releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                    prod_resp = zxart_fetch_json(
                        f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(pid)}"
                    )
                    prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                    prod  = prods[0] if prods else {}
                    downloads = []
                    for rel in releases:
                        if not isinstance(rel, dict): continue
                        file_url  = rel.get("file") or ""
                        file_name = rel.get("fileName") or (
                            os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else "")
                        if not file_url: continue
                        downloads.append({
                            "url": file_url, "filename": file_name,
                            "type": f"{rel.get('releaseType') or ''} / {rel.get('releaseFormat') or ''}".strip(" /") or "release",
                            "format": rel.get("releaseFormat") or "",
                            "size": "", "source": rel.get("title") or "zxart",
                        })
                    return (str(prod.get("title") or title), downloads)
                def _on_ok(res, _cb=callback):
                    t2, dls = res
                    host._zxart_selected_title = t2
                    host._zxart_selected_downloads = dls
                    host.zxart_download_button.setEnabled(bool(dls))
                    _cb(t2, dls)
                def _on_err(err):
                    zxart_set_status(f"Detail error: {err[1]}")
                host._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        if action is act_download:
            def _show(t, dls):
                zxart_show_downloads_overlay(t, dls)
            _ensure_detail_then(_show)
        elif action is act_send_sd:
            def _send_sd(t, dls):
                _zxart_send_to_image(t, dls)
            _ensure_detail_then(_send_sd)
        elif action is act_send_ns:
            def _send_ns(t, dls, _nb=_ns_base):
                def _after(_folder):
                    QTimer.singleShot(0, host._nextsync_start_server_fn)
                _zxart_send_to_path(t, dls, _nb, _after)
            _ensure_detail_then(_send_ns)

    def _zxart_has_image(e):
        src = e.get("_source") or {}
        kind = (e.get("_kind") or "").lower()
        if kind == "zxart_picture":
            return bool(src.get("imageUrl") or src.get("originalUrl"))
        for u in (src.get("imagesUrls") or []):
            if u:
                return True
        return False

    host.zxart_gallery_view = GalleryView(
        rows_per_page_getter=lambda: host._gallery_rows_per_page,
        anim_mode_getter=lambda: host._gallery_anim_mode,
        cols_getter=lambda: host._gallery_cols,
        img_size_getter=lambda: host._gallery_img_size,
        thumb_fetch_cb=_zxart_thumb_fetch,
        extra_fetch_cb=_zxart_extra_fetch,
        title_getter=_zxart_gallery_title,
        info_getter=_zxart_gallery_info,
        context_menu_cb=_zxart_gallery_context_menu,
        image_predicate=_zxart_has_image,
        is_favorite_cb=lambda e: host._fav_is({**e, "_fav_source": "zxart"}),
        toggle_favorite_cb=lambda e: host._fav_toggle({**e, "_fav_source": "zxart"}),
        tooltip_getter=_zxart_tooltip_getter,
    )
    # Animate .gif thumbnails (QMovie) just like the in-pane item viewer.
    host.zxart_gallery_view.set_gif_fetch_cb(_gif_fetch_bytes)
    host._fav_fetchers = getattr(host, "_fav_fetchers", {})
    host._fav_fetchers["zxart"] = {
        "thumb": _zxart_thumb_fetch,
        "extra": _zxart_extra_fetch,
        "title": _zxart_gallery_title,
        "info":  _zxart_fav_table_info,
        "has_image": _zxart_has_image,
    }
    host.zxart_view_stack.addWidget(host.zxart_gallery_view)  # index 1

    zxart_table_row.addWidget(host.zxart_view_stack, 1)
    # Animated retro "SEARCHING..." banner over the
    # results area whenever a fetch is in flight — including re-searches over
    # already-populated content, so it stays visible on top of the pygame
    # GalleryScene (not only on the first/empty load).
    host._zxart_loading_overlay = RetroLoadingOverlay(
        host.zxart_view_stack,
        lambda: getattr(host, "_zxart_search_loading", False))
    zxart_table_row.addWidget(zxart_right_widget)
    zxart_table_container = QWidget()
    zxart_table_container.setLayout(zxart_table_row)
    host.zxart_form.addRow(zxart_table_container)

    # --- Detail panel ---
    host._zxart_detail_layout = QFormLayout()
    host._zxart_detail_layout.setContentsMargins(0, 0, 0, 0)
    host._zxart_detail_rows = []

    host._zxart_detail_widget = QWidget()
    host._zxart_detail_widget.setLayout(host._zxart_detail_layout)
    # Detail widget intentionally not added to form; info shown via cell tooltips instead.

    # --- Internal state ---
    host._zxart_current_page   = 1
    host._zxart_total_pages    = 1
    host._zxart_last_query     = ""
    host._zxart_selected_id    = ""
    host._zxart_selected_title = ""
    host._zxart_selected_downloads = []
    host._zxart_search_loading = False
    # Generation token: see _getit_search_gen for rationale.
    host._zxart_search_gen = 0
    host._zxart_loaded_once    = False
    host._zxart_results_mode   = "prods"
    host._zxart_last_entries   = []
    host._zxart_ac_cache: dict = {}   # prefix -> sorted title list (short-lived cache)

    # Slideshow state
    host._zxart_screenshots    = []
    host._zxart_shot_cache     = {}
    host._zxart_shot_index     = 0
    host._zxart_shot_token     = 0
    host._zxart_slideshow_timer = QTimer(host)
    host._zxart_slideshow_timer.setInterval(gallery_slideshow_interval_ms())
    # Stepping back with ◀/< holds on that image for a long beat (60s) so the
    # user can study it before the normal 5s cadence resumes. Guarded by the
    # shot token so a later row selection can't let a stale dwell advance
    # freshly-loaded screenshots.
    host._zxart_shot_dwell_timer = QTimer(host)
    host._zxart_shot_dwell_timer.setSingleShot(True)
    host._zxart_dwell_token = -1

    # ---- Helpers ----

    def zxart_set_status(msg: str, open_path: str = None):
        host.zxart_status_label.setText(msg)
        host._zxart_status_open_path = open_path
        if open_path:
            host.zxart_status_label.setStyleSheet("color: #4fc3f7; text-decoration: underline;")
            host.zxart_status_label.setCursor(Qt.PointingHandCursor)
        else:
            host.zxart_status_label.setStyleSheet("")
            host.zxart_status_label.setCursor(Qt.ArrowCursor)

    def _zxart_clear_detail_rows():
        while host._zxart_detail_layout.rowCount() > 0:
            host._zxart_detail_layout.removeRow(0)
        host._zxart_detail_rows = []

    def _zxart_add_row(label: str, value: str, *, dim: bool = False, wrap: bool = True, is_html: bool = False):
        lab = QLabel(_zxart_tr(label))
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
        host._zxart_detail_layout.addRow(lab, val)
        host._zxart_detail_rows.append((lab, val))

    def zxart_clear_detail():
        _zxart_clear_detail_rows()
        host.zxart_screenshot_label.setText("No preview")
        host.zxart_screenshot_label.clear_image()
        host.zxart_download_button.setEnabled(False)
        host._zxart_selected_id = ""
        host._zxart_selected_title = ""
        host._zxart_selected_downloads = []
        host._zxart_slideshow_timer.stop()
        host._zxart_shot_token += 1
        host._zxart_screenshots = []
        host._zxart_shot_cache  = {}
        host._zxart_shot_index  = 0

    def zxart_sanitize_folder(name: str) -> str:
        n = (name or "").strip().lower()
        for ch in '<>:"/\\|?*':
            n = n.replace(ch, "")
        n = " ".join(n.split())
        return n or "untitled"

    def zxart_human_size(n) -> str:
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

    def zxart_download_to_path(url: str, save_path: str, on_done=None, on_err=None):
        def _fn():
            data = zxart_fetch_bytes(url, timeout=60)
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
            return save_path
        def _ok(p):
            if on_done: on_done(p)
        def _err(e):
            if on_err: on_err(e)
        return getit_run_in_thread(_fn, _ok, _err)

    def _zxart_resolve_base_path(configured_path: str) -> str:
        p = (configured_path or "").strip().rstrip("/\\")
        if p and os.path.isdir(p):
            return p
        return os.path.abspath("downloads")

    def _zxart_send_to_path(title: str, downloads: list, dest_root: str, post_action=None):
        if not downloads:
            zxart_set_status("No downloadable files for this entry.")
            return
        folder = os.path.join(dest_root, zxart_sanitize_folder(title))
        os.makedirs(folder, exist_ok=True)
        pending = {"n": len(downloads), "ok": 0, "ko": 0}

        def _maybe_finish():
            if pending["ok"] + pending["ko"] >= pending["n"]:
                if pending["ok"] > 0:
                    zxart_set_status(
                        f"Sent {pending['ok']}/{pending['n']} file(s) → {folder}  ↗ open folder",
                        open_path=folder,
                    )
                else:
                    zxart_set_status(f"All {pending['n']} download(s) failed — check the URLs")
                if post_action:
                    post_action(folder)

        for d in downloads:
            fname = d.get("filename") or os.path.basename(
                urllib.parse.urlparse(d.get("url", "")).path
            ) or "file.bin"
            save_path = os.path.join(folder, fname)

            def _ok(p, _f=fname):
                pending["ok"] += 1
                zxart_set_status(f"Downloaded {_f}")
                _maybe_finish()

            def _err(e, _f=fname):
                pending["ko"] += 1
                zxart_set_status(f"Failed {_f}: {e[1]}")
                _maybe_finish()

            zxart_download_to_path(d.get("url", ""), save_path, _ok, _err)

    def _zxart_send_to_image(title: str, downloads: list):
        if not _right_disk_content():
            zxart_set_status("Please load a disk image first (SD Card tab).")
            return
        if not host.right_disk_image_path:
            zxart_set_status("No disk image loaded.")
            return
        if not downloads:
            zxart_set_status("No downloadable files for this entry.")
            return

        safe_name  = zxart_sanitize_folder(title)
        img_dir    = (generate_disk_file_path().rstrip("/") + "/" + safe_name).replace("//", "/")
        image_path = host.right_disk_image_path
        pending    = {"n": len(downloads), "ok": 0, "ko": 0}

        def _maybe_finish():
            if pending["ok"] + pending["ko"] >= pending["n"]:
                if pending["ok"] > 0:
                    zxart_set_status(f"Sent {pending['ok']}/{pending['n']} file(s) → image:{img_dir}")
                    host._show_sd_notification(
                        f"Sent {pending['ok']}/{pending['n']} file(s) to SD card image:\n{img_dir}"
                    )
                    # Async refresh (listing runs on a worker thread).
                    update_disk_manager_widget_table()
                else:
                    zxart_set_status(f"All {pending['n']} download(s) failed — check the URLs")

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
                    data = _http_fetch_bytes_with_retry(
                        zxart_safe_url(_url),
                        headers={"User-Agent": ZXART_USER_AGENT},
                        timeout=60,
                    )
                    with open(tmp.name, "wb") as fh:
                        fh.write(data)
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
                zxart_set_status(f"Sent {_f} → image:{dest}")
                _maybe_finish()

            def _err(e, _f=fname):
                pending["ko"] += 1
                zxart_set_status(f"Failed {_f}: {e[1]}")
                _maybe_finish()

            getit_run_in_thread(_dl_and_put, _ok, _err)

    # Generation token: bumped on every table (re)population so that a
    # late-arriving background author-column resolve for a previous page
    # is ignored instead of writing into a now-stale table.
    host._zxart_populate_gen = 0

    def _zxart_resolve_author_cols_async(pending_rows):
        """Warm the zxArt name caches for *pending_rows* off the UI thread,
        then refresh the matching table cells on the UI thread.

        *pending_rows* is a list of ``(id_item, entry)`` tuples. The
        id_item carries the entry on Qt.UserRole and is only used to read
        the row id; the refresh re-locates rows by id so it stays correct
        even if Qt reordered or partially rebuilt the table.
        """
        gen = host._zxart_populate_gen
        entries = [e for (_it, e) in pending_rows]

        def _fn():
            # Network-backed warm-up of the in-memory caches. Safe here:
            # this runs on a daemon worker thread, never the GUI thread.
            _zxart_prefetch_names_for_entries(entries)
            # Recompute the final (now cache-complete) strings.
            return [(str(e.get("id", "")), _zxart_author_col_cached(e)[0])
                    for e in entries]

        def _ok(results):
            # Back on the GUI thread (queued connection). Drop the update
            # if a newer population happened in the meantime.
            if gen != host._zxart_populate_gen:
                return
            try:
                tbl = host.zxart_results_table
            except RuntimeError:
                return
            by_id = {rid: txt for (rid, txt) in results}
            try:
                row_count = tbl.rowCount()
            except RuntimeError:
                return
            for row in range(row_count):
                id_item = tbl.item(row, 0)
                if id_item is None:
                    continue
                rid = id_item.text()
                txt = by_id.get(rid)
                if not txt:
                    continue
                cell = tbl.item(row, 3)
                if cell is not None:
                    cell.setText(txt)
                else:
                    tbl.setItem(row, 3, QTableWidgetItem(txt))

        getit_run_in_thread(_fn, _ok, lambda _e: None)

    def zxart_populate_results(entries, page, total_pages, mode="prods"):
        host._zxart_populate_gen += 1
        host._zxart_current_page = page or 1
        host._zxart_total_pages  = total_pages or 1
        host._zxart_results_mode = mode
        host.zxart_page_label.setText(str(host._zxart_current_page))
        host.zxart_prev_button.setEnabled(host._zxart_current_page > 1)
        host.zxart_next_button.setEnabled(host._zxart_current_page < host._zxart_total_pages)

        headers_map = {
            "prods":    ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
            "byletter": ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
            "pictures": ["ID", "Title", "Year", "Author(s)", "Type", "Tags"],
        }
        host.zxart_results_table.setHorizontalHeaderLabels(
            [_zxart_tr(h) for h in headers_map.get(mode, headers_map["prods"])]
        )

        host.zxart_results_table.setRowCount(0)
        _pending_author_rows = []
        for e in entries:
            row = host.zxart_results_table.rowCount()
            host.zxart_results_table.insertRow(row)
            id_item = QTableWidgetItem(e.get("id", ""))
            id_item.setData(Qt.UserRole, e)
            host.zxart_results_table.setItem(row, 0, id_item)
            host.zxart_results_table.setItem(row, 1, QTableWidgetItem(e.get("title", "")))
            host.zxart_results_table.setItem(row, 2, QTableWidgetItem(e.get("year", "")))
            # Author / group column: resolve from the in-memory caches only
            # (never block the UI thread on a network call). If the cache
            # is cold, show the best-effort text now and warm the cache in
            # a background thread, then refresh the cell when it lands.
            author_text, complete = _zxart_author_col_cached(e)
            author_item = QTableWidgetItem(author_text)
            host.zxart_results_table.setItem(row, 3, author_item)
            if not complete:
                _pending_author_rows.append((id_item, e))
            host.zxart_results_table.setItem(row, 4, QTableWidgetItem(e.get("machine", "")))
            host.zxart_results_table.setItem(row, 5, QTableWidgetItem(e.get("genre", "")))
        host._zxart_last_entries = list(entries)
        if _pending_author_rows:
            _zxart_resolve_author_cols_async(_pending_author_rows)
        host.zxart_gallery_view.populate(entries)
        host._pane_retro_gallery_refresh("zxart")
        host.zxart_gallery_view.select_entry(
            lambda _e, _sel=host._zxart_selected_id: bool(_sel) and _e.get("id") == _sel
        )
        try:
            _aio = getattr(host, "_allinone_repopulate", None)
            if _aio is not None:
                _aio()
        except Exception:
            pass

    # ---- Slideshow ----

    def zxart_set_pixmap(pm: QPixmap):
        if pm is None or pm.isNull():
            host.zxart_screenshot_label.setText("No preview")
            host.zxart_screenshot_label.clear_image()
            return
        # The label keeps the original and re-fits it to its own size on
        # every resize, so the picture never stays stuck at the size it had
        # when first shown (the "first .scr doesn't get rescaled" symptom).
        host.zxart_screenshot_label.set_image(pm)
        if host._zxart_stack.currentIndex() == 1:
            host._zxart_fullscreen_pixmap = pm
            host.zxart_fullscreen_label.set_image(pm)

    def zxart_update_nav_buttons():
        multi = len(host._zxart_screenshots) > 1
        host.zxart_prev_shot_btn.setVisible(multi)
        host.zxart_next_shot_btn.setVisible(multi)
        host.zxart_fs_prev_btn.setVisible(multi and host._zxart_stack.currentIndex() == 1)
        host.zxart_fs_next_btn.setVisible(multi and host._zxart_stack.currentIndex() == 1)

    def zxart_show_shot_at(idx: int):
        if not host._zxart_screenshots:
            return
        idx = idx % len(host._zxart_screenshots)
        host._zxart_shot_index = idx
        zxart_update_nav_buttons()
        url = host._zxart_screenshots[idx]["url"]
        cached = host._zxart_shot_cache.get(url)
        if cached is not None:
            zxart_set_pixmap(cached)
            return

        token = host._zxart_shot_token

        def _fn():
            return zxart_fetch_bytes(url)

        def _on_ok(data):
            if token != host._zxart_shot_token:
                return
            pm = QPixmap()
            if pm.loadFromData(data) and not pm.isNull():
                host._zxart_shot_cache[url] = pm
                if host._zxart_screenshots and host._zxart_screenshots[host._zxart_shot_index]["url"] == url:
                    zxart_set_pixmap(pm)

        def _on_err(_err):
            pass

        getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxart_slideshow_tick():
        if len(host._zxart_screenshots) <= 1:
            return
        zxart_show_shot_at(host._zxart_shot_index + 1)

    host._zxart_slideshow_timer.timeout.connect(zxart_slideshow_tick)

    def _zxart_shot_dwell_elapsed():
        # The 60s pause after a ◀/< press is over: if still on the same
        # screenshot set, advance and resume the normal 5s cadence.
        if (host._zxart_dwell_token == host._zxart_shot_token
                and len(host._zxart_screenshots) > 1):
            zxart_show_shot_at(host._zxart_shot_index + 1)
            host._zxart_slideshow_timer.start()

    host._zxart_shot_dwell_timer.timeout.connect(_zxart_shot_dwell_elapsed)

    def _zxart_nav_prev():
        if len(host._zxart_screenshots) > 1:
            host._zxart_slideshow_timer.stop()
            host._zxart_shot_dwell_timer.stop()
            zxart_show_shot_at(host._zxart_shot_index - 1)
            # Dwell 60s on the image the user stepped back to, then resume.
            host._zxart_dwell_token = host._zxart_shot_token
            host._zxart_shot_dwell_timer.start(60000)

    def _zxart_nav_next():
        if len(host._zxart_screenshots) > 1:
            host._zxart_slideshow_timer.stop()
            host._zxart_shot_dwell_timer.stop()
            zxart_show_shot_at(host._zxart_shot_index + 1)
            host._zxart_slideshow_timer.start()

    host.zxart_prev_shot_btn.clicked.connect(_zxart_nav_prev)
    host.zxart_next_shot_btn.clicked.connect(_zxart_nav_next)

    def zxart_start_slideshow(screenshots):
        host._zxart_slideshow_timer.stop()
        host._zxart_shot_token += 1
        host._zxart_screenshots = list(screenshots or [])
        host._zxart_shot_cache  = {}
        host._zxart_shot_index  = 0
        if not host._zxart_screenshots:
            host.zxart_screenshot_label.setText("No preview")
            host.zxart_screenshot_label.clear_image()
            zxart_update_nav_buttons()
            return
        zxart_show_shot_at(0)
        if len(host._zxart_screenshots) > 1:
            host._zxart_slideshow_timer.start()

    # ---- Detail population ----

    def zxart_populate_prod_detail(detail: dict):
        _zxart_clear_detail_rows()
        _zxart_add_row("Title:",       detail.get("title", ""))
        _zxart_add_row("Year:",        detail.get("year", ""))
        _zxart_add_row("Authors:",     detail.get("authors", ""))
        _zxart_add_row("Groups:",      detail.get("groups", ""))
        _zxart_add_row("Produced by:", detail.get("produced_by", ""))
        _zxart_add_row("Published by:", detail.get("publishers", ""))
        _zxart_add_row("Compo:",       detail.get("compo", ""))
        party_place = detail.get("partyPlace", "")
        if party_place:
            _zxart_add_row("Place:", str(party_place))
        _zxart_add_row("Languages:",   detail.get("language", ""))
        _zxart_add_row("Legal:",       zxart_legal_status_label(detail.get("legalStatus", "")))
        _zxart_add_row("Description:", detail.get("description", ""), dim=True, is_html=True)
        host._zxart_selected_downloads = detail.get("downloads", []) or []
        host.zxart_download_button.setEnabled(bool(host._zxart_selected_downloads))

    def zxart_populate_picture_detail(detail: dict):
        _zxart_clear_detail_rows()
        _zxart_add_row("Title:",    detail.get("title", ""))
        _zxart_add_row("Year:",     detail.get("year", ""))
        _zxart_add_row("Authors:",  detail.get("authors", ""))
        _zxart_add_row("Type:",     detail.get("pic_type", ""))
        _zxart_add_row("Rating:",   detail.get("rating", ""))
        _zxart_add_row("Views:",    detail.get("views", ""))
        tags = detail.get("tags", "")
        if tags:
            _zxart_add_row("Tags:", tags, dim=True)
        _zxart_add_row("Description:", detail.get("description", ""), dim=True, is_html=True)
        host._zxart_selected_downloads = detail.get("downloads", []) or []
        host.zxart_download_button.setEnabled(bool(host._zxart_selected_downloads))

    # ---- Search tasks ----

    def zxart_current_mode():
        return host.zxart_mode_combo.currentData() or "prods"

    def zxart_set_busy(busy: bool):
        host._zxart_search_loading = busy
        host.zxart_search_button.setEnabled(not busy)
        host.zxart_mode_combo.setEnabled(not busy)
        host.zxart_letter_combo.setEnabled(not busy)
        try:
            host.zxart_random_button.setEnabled(not busy)
        except AttributeError:
            pass
        try:
            host.zxart_latest_button.setEnabled(not busy)
        except AttributeError:
            pass

    def zxart_run_search(query: str, page: int, on_complete=None):
        # Supersede any in-flight zxART request.
        host._zxart_search_gen += 1
        _gen = host._zxart_search_gen
        mode = zxart_current_mode()
        zxart_set_busy(True)
        zxart_set_status("Searching…")
        host._zxart_last_query = query
        offset = max(0, (page - 1) * ZXART_PAGE_SIZE)

        if mode == "pictures":
            if query:
                path = (
                    f"/export:zxPicture/language:{_zxart_lang()}/start:{offset}"
                    f"/limit:{ZXART_PAGE_SIZE}/filter:title~{urllib.parse.quote(query)}"
                )
            else:
                path = (
                    f"/export:zxPicture/language:{_zxart_lang()}/start:{offset}"
                    f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
                )

            def _fn_pic():
                resp = zxart_fetch_json(path)
                entries, total = zxart_parse_picture_list(resp)
                total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE) if total else 1
                _zxart_prefetch_names_for_entries(entries)
                return ("pictures", entries, total, page, total_pages)

            _fn = _fn_pic

        elif mode == "byletter":
            letter = host.zxart_letter_combo.currentData() or "a"
            if letter == "#":
                filt = "title~0,1,2,3,4,5,6,7,8,9"
            else:
                filt = f"title~{urllib.parse.quote(letter)}"
            path = (
                f"/export:zxProd/language:{_zxart_lang()}/start:{offset}"
                f"/limit:{ZXART_PAGE_SIZE}/filter:{filt}/order:title,asc"
            )

            def _fn_letter():
                resp = zxart_fetch_json(path)
                entries, total = zxart_parse_prod_list(resp)
                total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE) if total else 1
                for e in entries:
                    e["_kind"] = "zxart_prod"
                _zxart_prefetch_names_for_entries(entries)
                return ("byletter", entries, total, page, total_pages)

            _fn = _fn_letter

        else:  # prods
            if query:
                def _fn_prods():
                    def _progress(msg: str):
                        # Called from background thread — post to Qt main thread.
                        QMetaObject.invokeMethod(
                            host.zxart_status_label,
                            "setText",
                            Qt.QueuedConnection,
                            Q_ARG(str, msg),
                        )
                    entries, total = zxart_client_search(
                        query, progress_cb=_progress
                    )
                    for e in entries:
                        e["_kind"] = "zxart_prod"
                    _zxart_prefetch_names_for_entries(entries)
                    return ("prods", entries, total, 1, 1)
            else:
                path = (
                    f"/export:zxProd/language:{_zxart_lang()}/start:{offset}"
                    f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
                )

                def _fn_prods():
                    resp = zxart_fetch_json(path)
                    entries, total = zxart_parse_prod_list(resp)
                    total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE) if total else 1
                    for e in entries:
                        e["_kind"] = "zxart_prod"
                    _zxart_prefetch_names_for_entries(entries)
                    return ("prods", entries, total, page, total_pages)

            _fn = _fn_prods

        def _on_ok(data):
            if _gen != host._zxart_search_gen:
                return  # superseded by a newer search
            kind, entries, total, pg, total_pages = data
            zxart_populate_results(entries, pg, total_pages, kind)
            if kind == "pictures":
                zxart_set_status(f"{total} picture(s)  |  page {pg}/{total_pages}")
            elif kind == "byletter":
                lbl = host.zxart_letter_combo.currentText()
                zxart_set_status(f"{total} production(s) for '{lbl}'  |  page {pg}/{total_pages}")
            elif kind == "prods" and total_pages == 1 and host._zxart_last_query:
                zxart_set_status(f"{total} result(s) for '{host._zxart_last_query}'")
            else:
                zxart_set_status(f"{total} production(s)  |  page {pg}/{total_pages}")
            zxart_set_busy(False)
            if on_complete:
                on_complete()

        def _on_err(err):
            if _gen != host._zxart_search_gen:
                return  # superseded by a newer search
            zxart_set_status(f"Error: {err[1]}")
            zxart_set_busy(False)
            if on_complete:
                on_complete()

        host._zxart_search_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxart_on_search():
        zxart_clear_detail()
        q = host.zxart_search_input.text().strip()
        save_configuration_file()
        if q and len(q) < SEARCH_MIN_CHARS:
            return
        # Invalidate any in-flight autocomplete request and cancel any
        # pending debounce timer — its async result must not pop the
        # completer popup while the real search is running, which has
        # produced a native access violation inside QCompleter.
        try:
            host._zxart_ac_gen += 1
            host._zxart_ac_block = True
            t = getattr(host, "_zxart_ac_timer", None)
            if t is not None:
                t.stop()
            if getattr(host, "_zxart_ac_model", None) is not None:
                host._zxart_ac_model.setStringList([])
            comp = getattr(host, "_zxart_completer", None)
            if comp is not None:
                try:
                    popup = comp.popup()
                    if popup is not None and popup.isVisible():
                        popup.hide()
                except RuntimeError:
                    pass
        except Exception:
            pass
        if q:
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            def _zxart_done():
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, host.zxart_results_table.rowCount())
            zxart_run_search(q, 1, _zxart_done)
        else:
            zxart_run_search(q, 1)
        if _multi_search_enabled() and q:
            host.getit_search_input.setText(q)
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                host.zxdb_search_input.setText(q)
            _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            _cross_search_getit(q)
            _cross_search_zxdb(q)

    def zxart_on_prev():
        zxart_run_search(host._zxart_last_query, max(1, host._zxart_current_page - 1))

    def zxart_on_next():
        zxart_run_search(host._zxart_last_query, min(host._zxart_total_pages, host._zxart_current_page + 1))

    def zxart_on_latest(on_complete=None):
        zxart_clear_detail()
        host.zxart_search_input.clear()
        host._zxart_last_query = ""
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
        def _zxart_latest_done():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, host.zxart_results_table.rowCount())
            if on_complete:
                on_complete()
        # zxart_run_search with empty query already uses order:date,desc for
        # both 'prods' and 'pictures' modes, returning the most recent items.
        zxart_run_search("", 1, _zxart_latest_done)

    def zxart_on_random(on_complete=None):
        import random as _random
        zxart_clear_detail()
        host.zxart_search_input.clear()
        host._zxart_last_query = ""
        mode = zxart_current_mode()
        # Supersede any in-flight zxART request.
        host._zxart_search_gen += 1
        _gen = host._zxart_search_gen
        zxart_set_busy(True)
        zxart_set_status("Picking random zxART entries…")
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)

        def _fn():
            if mode == "pictures":
                export = "zxPicture"
                kind = "pictures"
            else:
                export = "zxProd"
                kind = "prods"
            # Probe first page to learn the total number of entries.
            probe_path = (
                f"/export:{export}/language:{_zxart_lang()}/start:0"
                f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
            )
            probe_resp = zxart_fetch_json(probe_path)
            if kind == "pictures":
                _e, total = zxart_parse_picture_list(probe_resp)
            else:
                _e, total = zxart_parse_prod_list(probe_resp)
            total = max(1, int(total or 1))
            total_pages = max(1, (total + ZXART_PAGE_SIZE - 1) // ZXART_PAGE_SIZE)
            page = _random.randint(1, total_pages)
            offset = (page - 1) * ZXART_PAGE_SIZE
            path = (
                f"/export:{export}/language:{_zxart_lang()}/start:{offset}"
                f"/limit:{ZXART_PAGE_SIZE}/order:date,desc"
            )
            resp = zxart_fetch_json(path)
            if kind == "pictures":
                entries, _tot = zxart_parse_picture_list(resp)
                for e in entries:
                    e["_kind"] = "zxart_pic"
            else:
                entries, _tot = zxart_parse_prod_list(resp)
                for e in entries:
                    e["_kind"] = "zxart_prod"
            _random.shuffle(entries)
            return (kind, entries, total, page, total_pages)

        def _on_ok(data):
            if _gen != host._zxart_search_gen:
                return  # superseded by a newer search
            kind, entries, total, page, total_pages = data
            zxart_populate_results(entries, page, total_pages, kind)
            noun = "picture(s)" if kind == "pictures" else "production(s)"
            zxart_set_status(
                f"{len(entries)} random {noun}  |  page {page}/{total_pages}"
            )
            zxart_set_busy(False)
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, host.zxart_results_table.rowCount())
            if on_complete:
                on_complete()

        def _on_err(err):
            if _gen != host._zxart_search_gen:
                return  # superseded by a newer search
            zxart_set_status(f"Error: {err[1]}")
            zxart_set_busy(False)
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, host.zxart_results_table.rowCount())
            if on_complete:
                on_complete()

        host._zxart_random_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    host.zxart_search_button.clicked.connect(zxart_on_search)
    host.zxart_search_input.returnPressed.connect(zxart_on_search)
    host.zxart_prev_button.clicked.connect(zxart_on_prev)
    host.zxart_next_button.clicked.connect(zxart_on_next)
    host.zxart_random_button.clicked.connect(zxart_on_random)
    host.zxart_latest_button.clicked.connect(zxart_on_latest)

    def _zxart_search_validate(text: str):
        t = text.strip()
        if not t:
            host._zxart_search_valid_lbl.setVisible(False)
        elif len(t) < SEARCH_MIN_CHARS:
            host._zxart_search_valid_lbl.setText('<font color="red">❌</font>')
            host._zxart_search_valid_lbl.setToolTip(f"Searches must be {SEARCH_MIN_CHARS} characters long")
            host._zxart_search_valid_lbl.setVisible(True)
        else:
            host._zxart_search_valid_lbl.setText('<font color="green">✔</font>')
            host._zxart_search_valid_lbl.setVisible(True)
    host.zxart_search_input.textChanged.connect(_zxart_search_validate)

    # ---- ZxArt autocomplete ----

    host._zxart_ac_model = QStringListModel(host)
    _zxart_completer = QCompleter(host._zxart_ac_model, host)
    _zxart_completer.setCompletionMode(QCompleter.PopupCompletion)
    _zxart_completer.setCaseSensitivity(Qt.CaseInsensitive)
    _zxart_completer.setFilterMode(Qt.MatchStartsWith)
    # Ensure the popup follows the main window on Windows
    popup = _zxart_completer.popup()
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
    host._zxart_completer = _zxart_completer
    host.zxart_search_input.setCompleter(_zxart_completer)
    host._zxart_popup_hider = _CompleterPopupHider(
        host.zxart_search_input, _zxart_completer, host)

    _zxart_ac_timer = QTimer(host)
    _zxart_ac_timer.setSingleShot(True)
    _zxart_ac_timer.setInterval(400)
    host._zxart_ac_timer = _zxart_ac_timer
    host._zxart_ac_pending: str = ""
    # Generation token: bumped whenever a real search starts or the
    # input is cleared.  Async autocomplete results carrying an older
    # token are discarded so they cannot repopulate / re-pop the
    # completer while a full search (or teardown) is already in flight.
    host._zxart_ac_gen: int = 0

    def _zxart_ac_trigger():
        if not _search_autocomplete_on():
            host._zxart_ac_model.setStringList([])
            return
        mode = zxart_current_mode()
        if mode not in ("prods", "byletter"):
            host._zxart_ac_model.setStringList([])
            return
        text = host.zxart_search_input.text().strip()
        if not text:
            host._zxart_ac_model.setStringList([])
            return

        # Avoid firing a heavy zxART network search (binary-search probes +
        # 200-item window fetch) on an empty input.  Other panes (GetIt,
        # ZXDB) already offer suggestions starting at the first typed
        # character, so allow the autocomplete to trigger as soon as the
        # user has typed at least one character.  The full search button
        # itself still enforces SEARCH_MIN_CHARS.
        if len(text) < 1:
            host._zxart_ac_model.setStringList([])
            return

        # Safe popup helper.  QCompleter.complete() has crashed Qt with
        # a native access violation on this Windows build, even when
        # deferred via QTimer.singleShot(0, ...).  We therefore drive
        # the popup view directly: set the completion prefix on the
        # completer (which filters the model) and show the popup view
        # at an explicit geometry, skipping complete()'s internal
        # event-loop pumping.
        def _safe_show_popup(_q=text):
            try:
                if not host._search_autocomplete_on():
                    return
                if getattr(host, "_zxart_ac_block", False):
                    return
                if not host.zxart_search_input.hasFocus():
                    return
                if host.zxart_search_input.text().strip() != _q:
                    return
                if host._zxart_ac_model.rowCount() == 0:
                    return
                _zxart_completer.setCompletionPrefix(_q)
                popup = _zxart_completer.popup()
                if popup is None:
                    return
                try:
                    popup.setParent(host.zxart_search_input.window(),
                                    Qt.Tool
                                    | Qt.FramelessWindowHint
                                    | Qt.WindowStaysOnTopHint
                                    | Qt.WindowDoesNotAcceptFocus)
                    popup.setFocusPolicy(Qt.NoFocus)
                    popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
                except Exception:
                    pass
                le = host.zxart_search_input
                rect = le.rect()
                pos = le.mapToGlobal(rect.bottomLeft())
                popup.setMinimumWidth(le.width())
                popup.move(pos)
                popup.resize(le.width(), _popup_height_for(popup, host._zxart_ac_model.rowCount()))
                popup.show()
            except RuntimeError:
                pass
            except Exception:
                pass

        # Serve from short-lived prefix cache if available.
        if text in host._zxart_ac_cache:
            titles = host._zxart_ac_cache[text][:80]
            host._zxart_ac_model.setStringList(titles)
            if titles:
                QTimer.singleShot(0, _safe_show_popup)
            return

        # Also try to derive results from a cached longer prefix.
        tl = text.lower()
        for cached_prefix, cached_list in host._zxart_ac_cache.items():
            if tl.startswith(cached_prefix.lower()):
                matches = sorted(
                    (t for t in cached_list if t.lower().startswith(tl)),
                    key=str.lower,
                )[:80]
                host._zxart_ac_model.setStringList(matches)
                if matches:
                    QTimer.singleShot(0, _safe_show_popup)
                return

        host._zxart_ac_pending = text
        gen_at_dispatch = host._zxart_ac_gen
        host._ac_anim_start(host.zxart_search_input)

        def _fn():
            entries, _total = zxart_client_search(text)
            titles = sorted(
                {e["title"] for e in entries if e.get("title")},
                key=str.lower,
            )
            return (text, titles)

        def _on_ok(result):
            queried, titles = result
            host._ac_anim_stop(host.zxart_search_input)
            try:
                # Evict oldest cache entries to cap memory (keep last 10 prefixes).
                if len(host._zxart_ac_cache) >= 10:
                    oldest = next(iter(host._zxart_ac_cache))
                    del host._zxart_ac_cache[oldest]
                host._zxart_ac_cache[queried] = titles
                # Discard the result if a real search has been launched (or
                # the input was reset) since we dispatched this fetch — in
                # that case popping the completer would re-enter Qt while
                # QCompleter/QLineEdit are mid-transition, which has caused
                # an access violation on Windows.
                if gen_at_dispatch != host._zxart_ac_gen:
                    return
                # Only update the model if user hasn't moved on to a different prefix.
                if host.zxart_search_input.text().strip() != queried:
                    return
                if not host.zxart_search_input.hasFocus():
                    return
                host._zxart_ac_model.setStringList(titles[:80])
                if titles:
                    QTimer.singleShot(0, _safe_show_popup)
            except RuntimeError:
                # Underlying C++ widget/model was deleted while the queued
                # result was in flight — safe to drop.
                pass

        def _on_err(_err):
            host._ac_anim_stop(host.zxart_search_input)

        host._zxart_ac_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def _zxart_ac_on_text_changed(_text: str):
        if getattr(host, "_zxart_ac_suppress", False):
            host._zxart_ac_suppress = False
            return
        # The user is typing again: re-enable autocomplete suggestions
        # that were suppressed after the last search submission.
        host._zxart_ac_block = False
        _zxart_ac_timer.start()

    _zxart_ac_timer.timeout.connect(_zxart_ac_trigger)
    host.zxart_search_input.textChanged.connect(_zxart_ac_on_text_changed)

    # Tracks zxArt prefix fetches initiated externally (e.g. by the
    # AllInOne pane) so we don't fire duplicate requests for the same
    # prefix while one is already in flight.
    host._zxart_ac_external_fetching: set = set()

    def _zxart_ac_fetch_prefix(prefix: str):
        """Fetch zxArt titles starting with *prefix* into the shared
        _zxart_ac_cache.  Used by the AllInOne pane to prime suggestions
        without touching the zxArt search input/completer."""
        if not prefix:
            return
        if prefix in host._zxart_ac_cache:
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("zxart", prefix)
                except Exception:
                    pass
            return
        if prefix in host._zxart_ac_external_fetching:
            return
        host._zxart_ac_external_fetching.add(prefix)

        def _fn():
            entries, _total = zxart_client_search(prefix)
            titles = sorted(
                {e["title"] for e in entries if e.get("title")},
                key=str.lower,
            )
            return (prefix, titles)

        def _on_ok(result):
            pfx, titles = result
            host._zxart_ac_external_fetching.discard(pfx)
            try:
                if len(host._zxart_ac_cache) >= 10:
                    oldest = next(iter(host._zxart_ac_cache))
                    del host._zxart_ac_cache[oldest]
                host._zxart_ac_cache[pfx] = titles
            except Exception:
                pass
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("zxart", pfx)
                except Exception:
                    pass

        def _on_err(_err):
            host._zxart_ac_external_fetching.discard(prefix)
            cb = getattr(host, "_allinone_ac_notify", None)
            if cb:
                try:
                    cb("zxart", prefix)
                except Exception:
                    pass

        getit_run_in_thread(_fn, _on_ok, _on_err)

    host._zxart_ac_fetch_prefix = _zxart_ac_fetch_prefix

    def _zxart_ac_activated(selected: str):
        try:
            if selected:
                host._zxart_ac_suppress = True
                _zxart_ac_timer.stop()
                try:
                    _zxart_completer.popup().hide()
                except Exception:
                    pass
                host.zxart_search_input.setText(selected)
        except Exception:
            pass
        zxart_on_search()

    _zxart_completer.activated.connect(_zxart_ac_activated)

    def zxart_on_mode_changed(_idx):
        mode = zxart_current_mode()
        placeholders = {
            "prods":    "Search zxART productions... (leave empty to browse latest)",
            "byletter": "(pick a letter from the list →)",
            "pictures": "Search zxART pictures... (leave empty to browse latest)",
        }
        host.zxart_search_input.setPlaceholderText(placeholders.get(mode, ""))
        host.zxart_search_input.setVisible(mode != "byletter")
        host.zxart_letter_combo.setVisible(mode == "byletter")
        host._zxart_last_query = ""
        host._zxart_current_page = 1
        host._zxart_total_pages  = 1
        host.zxart_page_label.setText("1")
        host.zxart_prev_button.setEnabled(False)
        host.zxart_next_button.setEnabled(False)
        host.zxart_results_table.setRowCount(0)
        zxart_clear_detail()
        zxart_set_status("")
        configuration_dictionary[SETTING_ZXART_LAST_MODE] = mode
        save_configuration_file()

    host.zxart_mode_combo.currentIndexChanged.connect(zxart_on_mode_changed)

    def zxart_on_letter_changed(_idx):
        if zxart_current_mode() == "byletter":
            zxart_clear_detail()
            zxart_run_search("", 1)

    host.zxart_letter_combo.currentIndexChanged.connect(zxart_on_letter_changed)

    # ---- Row selection -> fetch detail ----

    def _zxart_reset_preview():
        host._zxart_slideshow_timer.stop()
        host._zxart_shot_token += 1
        host._zxart_screenshots = []
        host._zxart_shot_cache  = {}
        host._zxart_shot_index  = 0
        host.zxart_screenshot_label.clear_image()

    def _zxart_load_prod(pid: str, title_hint: str):
        """Load full production detail including releases."""
        host._zxart_selected_id    = pid
        host._zxart_selected_title = title_hint or pid
        zxart_set_status(f"Loading production {pid}…")
        host.zxart_screenshot_label.setText("Loading…")
        _zxart_reset_preview()

        def _fn():
            # Fetch the production record
            prod_resp = zxart_fetch_json(
                f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(pid)}"
            )
            prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
            prod = prods[0] if prods else {}

            # Fetch all releases for this production
            rel_resp = zxart_fetch_json(
                f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(pid)}"
            )
            releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []

            # Build detail dict
            def _join(lst):
                if isinstance(lst, list):
                    return ", ".join(str(x) for x in lst if x)
                return str(lst) if lst else ""

            authors_info = prod.get("authorsInfo") or []
            author_ids = [a.get("authorId") for a in authors_info if isinstance(a, dict) and a.get("authorId")]

            group_ids = prod.get("groupsIds") or []
            pub_ids   = prod.get("publishersIds") or []

            # Resolve human-readable names via the zxArt API.
            # Bulk endpoint resolves most authors in a single call; per-id
            # lookups (cached) cover any IDs the bulk filter omits.
            bulk_authors = {}
            try:
                a_resp = zxart_fetch_json(
                    f"/export:author/filter:zxProdId={urllib.parse.quote(pid)}/limit:200/"
                )
                for a in (a_resp.get("responseData") or {}).get("author", []) or []:
                    if isinstance(a, dict) and a.get("id"):
                        bulk_authors[int(a["id"])] = str(a.get("title") or "")
            except Exception:
                pass

            author_display_parts = []
            for aid in author_ids:
                try:
                    key = int(aid)
                except (TypeError, ValueError):
                    author_display_parts.append(str(aid))
                    continue
                name = bulk_authors.get(key) or _zxart_resolve_author_name(key)
                author_display_parts.append(name if name else str(aid))
            authors_display = ", ".join(s for s in author_display_parts if s)

            groups_display = _zxart_resolve_group_names(group_ids)
            publishers_display = _zxart_resolve_publisher_names(pub_ids)
            if not publishers_display:
                publishers_display = _zxart_scrape_publishers_from_prod_url(
                    str(prod.get("url") or "")
                )

            downloads = []
            for rel in releases:
                if not isinstance(rel, dict):
                    continue
                file_url  = rel.get("file") or ""
                file_name = rel.get("fileName") or (
                    os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else ""
                )
                if not file_url:
                    continue
                rel_type   = rel.get("releaseType") or ""
                rel_format = rel.get("releaseFormat") or ""
                rel_title  = rel.get("title") or ""
                downloads.append({
                    "url":      file_url,
                    "filename": file_name,
                    "type":     f"{rel_type} / {rel_format}".strip(" /") or "release",
                    "format":   rel_format,
                    "size":     "",
                    "source":   rel_title or "zxart",
                    "year":     str(rel.get("year") or ""),
                })

            # imagesUrls on the prod record are the primary previews (screenshots, inlays)
            screenshots = []
            seen_urls = set()
            for img_url in (prod.get("imagesUrls") or []):
                if img_url and img_url not in seen_urls:
                    seen_urls.add(img_url)
                    screenshots.append({"url": img_url, "type": "screenshot"})

            # Additional inlays / ads / instructions from releases
            for rel in releases:
                if not isinstance(rel, dict):
                    continue
                for key in ("inlays", "ads", "instructions"):
                    for img_url in (rel.get(key) or []):
                        if img_url and img_url not in seen_urls:
                            seen_urls.add(img_url)
                            screenshots.append({"url": img_url, "type": key.rstrip("s")})

            detail = {
                "id":          pid,
                "title":       str(prod.get("title") or ""),
                "year":        str(prod.get("year") or ""),
                "authors":     authors_display,
                "groups":      groups_display,
                "publishers":  publishers_display,
                "produced_by": groups_display,
                "compo":       str(prod.get("compo") or ""),
                "partyPlace":  prod.get("partyPlace") or "",
                "language":    _join(prod.get("language")),
                "legalStatus": zxart_legal_status_label(prod.get("legalStatus") or ""),
                "description": str(prod.get("description") or ""),
                "screenshots": screenshots,
                "downloads":   downloads,
            }
            return detail

        def _on_ok(detail):
            if host._zxart_selected_id != pid:
                return
            zxart_populate_prod_detail(detail)
            shots = detail.get("screenshots") or []
            zxart_start_slideshow(shots)
            title = detail.get("title", pid)
            n = len(shots)
            n_dl = len(detail.get("downloads") or [])
            msg = f"Loaded {title}"
            if n_dl:
                msg += f"  |  {n_dl} file(s)"
            if n > 1:
                msg += f"  |  {n} image(s) cycling"
            zxart_set_status(msg)

        def _on_err(err):
            if host._zxart_selected_id != pid:
                return
            zxart_set_status(f"Detail error: {err[1]}")
            host.zxart_screenshot_label.setText("No preview")

        host._zxart_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def _zxart_load_picture(pid: str, title_hint: str, source: dict):
        """Load picture detail – preview from imageUrl, download from originalUrl."""
        host._zxart_selected_id    = pid
        host._zxart_selected_title = title_hint or pid
        zxart_set_status(f"Loading picture {pid}…")
        host.zxart_screenshot_label.setText("Loading…")
        _zxart_reset_preview()

        def _fn():
            pic_resp = zxart_fetch_json(
                f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(pid)}"
            )
            pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
            pic  = pics[0] if pics else source or {}

            image_url    = pic.get("imageUrl") or ""
            original_url = pic.get("originalUrl") or ""
            author_ids   = pic.get("authorIds") or []
            tags         = pic.get("tags") or []

            # Resolve human-readable author names via the zxArt API.
            # /export:author/filter:zxPictureId=<id>/ returns one row per
            # contributor with id + title, in one call.
            authors_display = ""
            try:
                a_resp = zxart_fetch_json(
                    f"/export:author/filter:zxPictureId={urllib.parse.quote(pid)}/limit:200/"
                )
                names_by_id = {
                    int(a["id"]): str(a.get("title") or "")
                    for a in (a_resp.get("responseData") or {}).get("author", []) or []
                    if isinstance(a, dict) and a.get("id")
                }
                parts = []
                for aid in author_ids:
                    try:
                        key = int(aid)
                    except (TypeError, ValueError):
                        parts.append(str(aid))
                        continue
                    name = names_by_id.get(key) or _zxart_resolve_author_name(key)
                    parts.append(name if name else str(aid))
                if not author_ids and names_by_id:
                    parts = list(names_by_id.values())
                authors_display = ", ".join(s for s in parts if s)
            except Exception:
                authors_display = _zxart_resolve_author_names(author_ids)

            screenshots = []
            if image_url:
                screenshots.append({"url": image_url, "type": "picture"})

            downloads = []
            if original_url:
                fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{pid}.bin"
                downloads.append({
                    "url":      original_url,
                    "filename": fname,
                    "type":     "original",
                    "format":   "",
                    "size":     "",
                    "source":   "zxart",
                })
            if image_url and image_url != original_url:
                fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{pid}.png"
                downloads.append({
                    "url":      image_url,
                    "filename": fname_img,
                    "type":     "preview (PC)",
                    "format":   "",
                    "size":     "",
                    "source":   "zxart",
                })

            detail = {
                "id":          pid,
                "title":       str(pic.get("title") or ""),
                "year":        str(pic.get("year") or ""),
                "authors":     authors_display,
                "pic_type":    str(pic.get("type") or ""),
                "rating":      str(pic.get("rating") or ""),
                "views":       str(pic.get("views") or ""),
                "tags":        ", ".join(str(t) for t in tags),
                "description": str(pic.get("description") or ""),
                "screenshots": screenshots,
                "downloads":   downloads,
            }
            return detail

        def _on_ok(detail):
            if host._zxart_selected_id != pid:
                return
            zxart_populate_picture_detail(detail)
            shots = detail.get("screenshots") or []
            zxart_start_slideshow(shots)
            title = detail.get("title", pid)
            zxart_set_status(f"Loaded picture: {title}")

        def _on_err(err):
            if host._zxart_selected_id != pid:
                return
            zxart_set_status(f"Detail error: {err[1]}")
            host.zxart_screenshot_label.setText("No preview")

        host._zxart_detail_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxart_on_row_selected():
        sel = host.zxart_results_table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        id_item    = host.zxart_results_table.item(row, 0)
        title_item = host.zxart_results_table.item(row, 1)
        if not id_item:
            return
        entry = id_item.data(Qt.UserRole) or {}
        kind  = entry.get("_kind", "zxart_prod")
        pid   = id_item.text()
        title_hint = title_item.text() if title_item else pid
        host.zxart_download_button.setEnabled(False)
        if kind == "zxart_picture":
            _zxart_load_picture(pid, title_hint, entry.get("_source") or {})
        else:
            _zxart_load_prod(pid, title_hint)

    host.zxart_results_table.itemSelectionChanged.connect(zxart_on_row_selected)

    def zxart_on_row_double_clicked(item):
        row = host.zxart_results_table.row(item)
        id_item = host.zxart_results_table.item(row, 0)
        if not id_item:
            return
        entry = id_item.data(Qt.UserRole) or {}
        if entry:
            host._pane_open_item("zxart", entry, getattr(host, "_zxart_item_retro", False))

    host.zxart_results_table.itemDoubleClicked.connect(zxart_on_row_double_clicked)

    def zxart_on_gallery_cell(entry):
        eid = entry.get("id") or ""
        if not eid:
            return
        for r in range(host.zxart_results_table.rowCount()):
            item = host.zxart_results_table.item(r, 0)
            if item is not None and item.text() == eid:
                host.zxart_results_table.selectRow(r)
                break
        host.zxart_gallery_view.select_entry(lambda _e, _e0=entry: _e is _e0)

    host.zxart_gallery_view.cell_clicked.connect(zxart_on_gallery_cell)

    def _zxart_open_gallery_viewer(entry, make_viewer=None, install=True):
        eid   = entry.get("id") or ""
        title = entry.get("title") or eid
        if not eid:
            return None
        kind = entry.get("_kind", "zxart_prod")

        info_rows_base = [
            ("Title:",  title),
            ("Author:", entry.get("author", "")),
            ("Year:",   str(entry.get("year", "") or "")),
            ("Type:",   entry.get("prodType", "") or entry.get("pic_type", "")),
        ]
        _mk = make_viewer or (lambda **kw: GalleryItemViewer(
            parent=host, anim_mode_getter=lambda: host._gallery_anim_mode, **kw))
        viewer = _mk(
            title=title,
            info_rows=info_rows_base,
            screenshots=[],
            extra_fetch_cb=_zxart_extra_fetch,
            tags=_gallery_extract_tags(entry),
        )
        if hasattr(viewer, "set_gif_fetch_cb"):
            viewer.set_gif_fetch_cb(_gif_fetch_bytes)
        _fav_entry_zxart = {**entry, "_fav_source": "zxart"}
        viewer.set_favorite_hooks(_fav_entry_zxart, host._fav_is, host._fav_toggle)

        # ── action buttons ──────────────────────────────────────────
        _safe_title = zxart_sanitize_folder(title)
        _img_path   = host.right_disk_image_path or ""
        _img_label  = (generate_disk_file_path().rstrip("/") + "/" + _safe_title
                       ) if _img_path else ""
        _sd_dest    = f"{_img_path}  →  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base    = _zxart_resolve_base_path(
            host.left_file_nextsync_explorer_selection_full_filename_path)
        _ns_dest    = os.path.join(_ns_base, _safe_title)
        _sd_ok      = bool(host.right_disk_image_path) and bool(_right_disk_content())

        def _ensure_detail_then(callback, _pid=eid, _kind=kind, _title=title):
            if host._zxart_selected_id == _pid and host._zxart_selected_downloads:
                callback(host._zxart_selected_title or _title,
                         host._zxart_selected_downloads)
                return
            zxart_set_status(f"Loading {_pid}\u2026")
            if _kind == "zxart_picture":
                def _fn():
                    pic_resp = zxart_fetch_json(
                        f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(_pid)}"
                    )
                    pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                    pic  = pics[0] if pics else (entry.get("_source") or {})
                    image_url    = pic.get("imageUrl") or ""
                    original_url = pic.get("originalUrl") or ""
                    downloads = []
                    if original_url:
                        fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{_pid}.bin"
                        downloads.append({"url": original_url, "filename": fname,
                                          "type": "original", "format": "", "size": "", "source": "zxart"})
                    if image_url and image_url != original_url:
                        fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{_pid}.png"
                        downloads.append({"url": image_url, "filename": fname_img,
                                          "type": "preview (PC)", "format": "", "size": "", "source": "zxart"})
                    return (str(pic.get("title") or _title), downloads)
                def _on_ok(res, _cb=callback):
                    t2, dls = res
                    dls = _filter_download_urls(dls)
                    host._zxart_selected_title     = t2
                    host._zxart_selected_downloads = dls
                    host.zxart_download_button.setEnabled(bool(dls))
                    viewer.set_download_available(bool(dls))
                    _cb(t2, dls)
                def _on_err(err):
                    zxart_set_status(f"Detail error: {err[1]}")
                host._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
            else:
                def _fn():
                    rel_resp = zxart_fetch_json(
                        f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(_pid)}"
                    )
                    releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                    prod_resp = zxart_fetch_json(
                        f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(_pid)}"
                    )
                    prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                    prod  = prods[0] if prods else {}
                    downloads = []
                    for rel in releases:
                        if not isinstance(rel, dict): continue
                        file_url  = rel.get("file") or ""
                        file_name = rel.get("fileName") or (
                            os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else "")
                        if not file_url: continue
                        downloads.append({
                            "url": file_url, "filename": file_name,
                            "type": f"{rel.get('releaseType') or ''} / {rel.get('releaseFormat') or ''}".strip(" /") or "release",
                            "format": rel.get("releaseFormat") or "",
                            "size": "", "source": rel.get("title") or "zxart",
                        })
                    return (str(prod.get("title") or _title), downloads)
                def _on_ok(res, _cb=callback):
                    t2, dls = res
                    dls = _filter_download_urls(dls)
                    host._zxart_selected_title     = t2
                    host._zxart_selected_downloads = dls
                    host.zxart_download_button.setEnabled(bool(dls))
                    viewer.set_download_available(bool(dls))
                    _cb(t2, dls)
                def _on_err(err):
                    zxart_set_status(f"Detail error: {err[1]}")
                host._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        def _dl_btn():
            _ensure_detail_then(lambda t, dls: zxart_show_downloads_overlay(t, dls))
        def _sd_btn():
            _ensure_detail_then(lambda t, dls: _zxart_send_to_image(t, dls))
        _captured_ns_base = _ns_base
        def _ns_btn():
            def _do(t, dls):
                def _after(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
                _zxart_send_to_path(t, dls, _captured_ns_base, _after)
            _ensure_detail_then(_do)

        viewer.set_actions(
            download_cb=_dl_btn, send_sd_cb=_sd_btn, send_ns_cb=_ns_btn,
            sd_enabled=_sd_ok, sd_tooltip=_sd_dest,
            ns_enabled=True,   ns_tooltip=_ns_dest,
        )
        host._wire_viewer_emulators(
            viewer, allow=ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS)
        viewer.set_open_web_url(zxart_entry_website_url(entry), "zxart.ee")
        # If downloads are disabled globally, hide all action buttons immediately.
        if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
            viewer.set_download_available(False)
        elif host._zxart_selected_id == eid:
            viewer.set_download_available(
                bool(_filter_download_urls(host._zxart_selected_downloads or []))
            )

        # ── async enrich (screenshots + full metadata) ──────────────
        def _fn():
            if kind == "zxart_picture":
                pic_resp = zxart_fetch_json(
                    f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(eid)}"
                )
                pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                pic  = pics[0] if pics else {}
                image_url = pic.get("imageUrl") or ""
                screenshots = [image_url] if image_url else []
                _raw_rating = pic.get("rating")
                rating = str(_raw_rating) if _raw_rating is not None else ""
                rows = [
                    (_zxart_tr("Title:"),       str(pic.get("title") or title)),
                    (_zxart_tr("Year:"),        str(pic.get("year") or "")),
                    (_zxart_tr("Authors:"),     ", ".join(str(a) for a in (pic.get("authors") or []))),
                    (_zxart_tr("Type:"),        str(pic.get("type") or "")),
                    (_zxart_tr("Rating:"),      _gallery_stars(rating) if rating else ""),
                    (_zxart_tr("Views:"),       str(pic.get("views") or "")),
                    (_zxart_tr("Description:"), str(pic.get("description") or "")),
                ]
                return (screenshots, rows, str(pic.get("title") or title))
            else:
                prod_resp = zxart_fetch_json(
                    f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(eid)}"
                )
                prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                prod  = prods[0] if prods else {}
                # Also pull releases so we can derive hardware/format tags.
                try:
                    rel_resp = zxart_fetch_json(
                        f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(eid)}",
                        timeout=20,
                    )
                    releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                except Exception:
                    releases = []
                screenshots = [u for u in (prod.get("imagesUrls") or []) if u]
                votes        = prod.get("votes")
                votes_amount = prod.get("votesAmount")
                try:
                    # "votes" is already the average score (e.g. 4.14 out of 5);
                    # "votesAmount" is the number of voters — do NOT divide.
                    rating = f"{float(votes):.2f}" if votes is not None else ""
                except (TypeError, ValueError):
                    rating = ""
                pub_ids_fs = prod.get("publishersIds") or []
                publishers_fs = _zxart_resolve_publisher_names(pub_ids_fs)
                if not publishers_fs:
                    publishers_fs = _zxart_scrape_publishers_from_prod_url(
                        str(prod.get("url") or "")
                    )
                grp_ids_fs = prod.get("groupsIds") or []
                produced_by_fs = _zxart_resolve_group_names(grp_ids_fs)
                rows = [
                    (_zxart_tr("Title:"),       str(prod.get("title") or title)),
                    (_zxart_tr("Year:"),        str(prod.get("year") or "")),
                    (_zxart_tr("Authors:"),     ", ".join(str(a) for a in (prod.get("authors") or []))),
                    (_zxart_tr("Groups:"),      ", ".join(str(g) for g in (prod.get("groups")  or []))),
                    (_zxart_tr("Produced by:"), produced_by_fs),
                    (_zxart_tr("Published by:"), publishers_fs),
                    (_zxart_tr("Compo:"),       str(prod.get("compo") or "")),
                    (_zxart_tr("Place:"),       str(prod.get("partyPlace") or "")),
                    (_zxart_tr("Rating:"),      _gallery_stars(rating) if rating else ""),
                    (_zxart_tr("Language:"),    str(prod.get("language") or "")),
                    (_zxart_tr("Legal:"),       zxart_legal_status_label(prod.get("legalStatus") or "")),
                    (_zxart_tr("Description:"), str(prod.get("description") or ""), True),
                ]
                return (screenshots, rows, str(prod.get("title") or title), releases)

        def _on_ok(res):
            if len(res) == 4:
                screenshots, rows, fetched_title, releases = res
            else:
                screenshots, rows, fetched_title = res
                releases = None
            if screenshots:
                viewer.set_screenshots(screenshots)
            else:
                _ph_label = "FILE"
                if releases:
                    for _rel in releases:
                        if not isinstance(_rel, dict):
                            continue
                        _fn2 = _rel.get("fileName") or ""
                        if _fn2:
                            _ph_label = zxfmt_label_for_name(_fn2)
                            break
                    if _ph_label == "FILE":
                        _fmts = []
                        for _rel in releases:
                            if not isinstance(_rel, dict):
                                continue
                            _v = _rel.get("releaseFormat")
                            if isinstance(_v, list):
                                _fmts.extend([str(x) for x in _v if x])
                            elif _v:
                                _fmts.append(str(_v))
                        if _fmts:
                            _ph_label = zxfmt_label_for_name("x." + _fmts[0].lower())
                viewer.set_placeholder(_ph_label, fetched_title)
            _gallery_viewer_refresh_meta(viewer, fetched_title, rows)
            # Surface readable text files among the releases (.txt/.nfo
            # notes) as Pygame log-console pages; the Qt viewer ignores them.
            # When there is none, fall back to the description (pulled from
            # the metadata rows by its translated label).
            _txt_added = []
            if releases:
                _txt_added = _gallery_add_text_pages(
                    viewer,
                    [{"url": r.get("file"), "fileName": r.get("fileName")}
                     for r in releases if isinstance(r, dict)])
            if not _txt_added:
                _desc_label = _zxart_tr("Description:")
                _desc = next((r[1] for r in (rows or [])
                              if r and r[0] == _desc_label and len(r) > 1 and r[1]), "")
                _gallery_add_description_page(viewer, _desc)
            # Mirror the ZXDB viewer: once metadata resolves, (re)assert the
            # action-button visibility so "Download" / "Send to SD card" /
            # "Send via NextSync" are shown when downloads are enabled for
            # this pane. Without this the buttons stay hidden if the entry
            # was selected (single-clicked) before opening, because no
            # downloads were cached yet at setup time.
            if ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
                if releases is not None:
                    # Build the candidate download URLs the same way as the
                    # button handlers (_ensure_detail_then) and filter them,
                    # so visibility matches what a download would actually
                    # produce.
                    _dls = []
                    for _rel in releases:
                        if not isinstance(_rel, dict):
                            continue
                        _file_url = _rel.get("file") or ""
                        if _file_url:
                            _dls.append({"url": _file_url})
                    _has_dl = bool(_filter_download_urls(_dls))
                else:
                    # Pictures always expose at least the image itself.
                    _has_dl = bool(screenshots)
                viewer.set_download_available(_has_dl)
            if releases:
                try:
                    src2 = entry.get("_source") or {}
                    src2["releases"] = releases
                    entry["_source"] = src2
                    viewer.set_tags(_gallery_extract_tags(entry))
                except Exception:
                    pass

        def _on_err(_e): viewer.set_placeholder("FILE", title)
        host._zxart_gallery_viewer_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        # ── push into pane stack ────────────────────────────────────
        if install:
            viewer.install_into_stack(
                host._zxart_stack,
                close_fn=lambda: host._zxart_stack.setCurrentIndex(0),
            )
        return viewer

    host.zxart_gallery_view.cell_dbl_clicked.connect(
        lambda e: host._pane_open_item("zxart", e, getattr(host, "_zxart_item_retro", False)))

    def _zxart_apply_view_mode(mode: str, *, persist: bool = True):
        mode = (mode or "table").lower()
        if mode not in ("table", "gallery"):
            mode = "table"
        host._zxart_view_mode = mode
        host.zxart_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
        if getattr(host, "_pane_retro_gallery_refresh", None):
            host._pane_retro_gallery_refresh("zxart")
        _table = (mode == "table")
        if hasattr(host, '_zxart_preview_container'):
            host._zxart_preview_container.setVisible(_table)
        if hasattr(host, '_zxart_preview_download_btn'):
            host._zxart_preview_download_btn.setVisible(
                _table and ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS
            )
        cb = host.zxart_view_combo
        target_idx = 1 if mode == "gallery" else 0
        if cb.currentIndex() != target_idx:
            cb.blockSignals(True)
            cb.setCurrentIndex(target_idx)
            cb.blockSignals(False)
        if persist:
            # sync other panes to the same view mode
            if hasattr(host, '_getit_apply_view_mode'):
                host._getit_apply_view_mode(mode, persist=False)
            if hasattr(host, '_zxdb_apply_view_mode'):
                host._zxdb_apply_view_mode(mode, persist=False)
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

    host._zxart_apply_view_mode = _zxart_apply_view_mode

    def _on_zxart_view_combo_changed(_idx):
        _zxart_apply_view_mode(host.zxart_view_combo.currentData() or "table")

    host.zxart_view_combo.currentIndexChanged.connect(_on_zxart_view_combo_changed)
    _zxart_apply_view_mode(host._zxart_view_mode, persist=False)

    # ---- Language selector ----

    # Initialise combo from the global zxART language (already populated
    # from cfg in load_configuration_file when present).
    def _zxart_sync_language_combo():
        code = _zxart_lang()
        cb = host.zxart_language_combo
        for i in range(cb.count()):
            if cb.itemData(i) == code:
                if cb.currentIndex() != i:
                    cb.blockSignals(True)
                    cb.setCurrentIndex(i)
                    cb.blockSignals(False)
                break

    _zxart_sync_language_combo()

    def _zxart_retranslate_ui():
        try:
            host.zxart_search_button.setText(_zxart_tr("Search"))
            host.zxart_random_button.setText(_zxart_tr("Random"))
            host.zxart_latest_button.setText(_zxart_tr("Latest"))
            for i, (_lbl, _key) in enumerate(
                (("Productions", "prods"),
                 ("By letter",  "byletter"),
                 ("Pictures",   "pictures"))
            ):
                host.zxart_mode_combo.setItemText(i, _zxart_tr(_lbl))
            host.zxart_page_text_label.setText(_zxart_tr("Page:"))
            host.zxart_prev_button.setText(_zxart_tr("< Prev"))
            host.zxart_next_button.setText(_zxart_tr("Next >"))
            host.zxart_view_text_label.setText(_zxart_tr("View:"))
            host.zxart_view_combo.setItemText(0, _zxart_tr("Table"))
            host.zxart_view_combo.setItemText(1, _zxart_tr("Gallery"))
            host.zxart_language_text_label.setText(_zxart_tr("Language:"))
            host.zxart_download_button.setText(_zxart_tr("Download File"))
            # Re-apply table headers for the current mode
            headers_map = {
                "prods":    ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
                "byletter": ["ID", "Title", "Year", "Author / Group", "Type", "Genre / Compo"],
                "pictures": ["ID", "Title", "Year", "Author(s)",      "Type", "Tags"],
            }
            mode = zxart_current_mode()
            host.zxart_results_table.setHorizontalHeaderLabels(
                [_zxart_tr(h) for h in headers_map.get(mode, headers_map["prods"])]
            )
            # Translate the "No preview" placeholder when no pixmap is shown
            if host.zxart_screenshot_label.pixmap() is None or host.zxart_screenshot_label.pixmap().isNull():
                cur = host.zxart_screenshot_label.text()
                if cur in ("No preview", "Brak podglądu", "Sin vista previa", ""):
                    host.zxart_screenshot_label.setText(_zxart_tr("No preview"))
        except Exception as _exc:
            logging.warning("zxart: retranslate UI failed: %s", _exc)

    host._zxart_retranslate_ui = _zxart_retranslate_ui

    def _on_zxart_language_changed(_idx):
        code = host.zxart_language_combo.currentData() or DEFAULT_ZXART_LANGUAGE
        _zxart_set_language(code)
        configuration_dictionary[SETTING_ZXART_LANGUAGE] = _zxart_lang()
        save_configuration_file()
        # Update all static UI labels to the new language.
        _zxart_retranslate_ui()
        # Re-run the current view so titles/metadata reload in the new language.
        try:
            zxart_clear_detail()
        except Exception:
            pass
        try:
            zxart_run_search(host._zxart_last_query or "",
                             max(1, host._zxart_current_page))
        except Exception as _exc:
            logging.warning("zxart: language reload failed: %s", _exc)

    host.zxart_language_combo.currentIndexChanged.connect(_on_zxart_language_changed)

    # ---- Download ----

    def zxart_pick_default_download():
        if not host._zxart_selected_downloads:
            return None
        preferred_ext = (".tap", ".tzx", ".z80", ".sna", ".trd", ".dsk", ".scl", ".bin")
        for d in host._zxart_selected_downloads:
            u = (d.get("url") or "").lower()
            if any(u.endswith(ext) for ext in preferred_ext):
                return d
        return host._zxart_selected_downloads[0]

    def zxart_do_download(d: dict):
        url = d.get("url", "")
        if not url:
            return
        base = os.path.basename(urllib.parse.urlparse(url).path) or f"{host._zxart_selected_id}.bin"
        save_path, _ = QFileDialog.getSaveFileName(None, "Save file", base)
        if not save_path:
            return
        zxart_set_status(f"Downloading {base}…")
        host.zxart_download_button.setEnabled(False)

        def _fn():
            data = zxart_fetch_bytes(url, timeout=60)
            with open(save_path, "wb") as f:
                f.write(data)
            return save_path

        def _on_ok(p):
            zxart_set_status(f"Saved to {p}  ↗ open folder", open_path=os.path.abspath(p))
            host.zxart_download_button.setEnabled(bool(host._zxart_selected_downloads))

        def _on_err(err):
            zxart_set_status(f"Download error: {err[1]}")
            host.zxart_download_button.setEnabled(bool(host._zxart_selected_downloads))

        host._zxart_dl_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

    def zxart_on_download_clicked():
        d = zxart_pick_default_download()
        if d:
            zxart_do_download(d)

    host.zxart_download_button.clicked.connect(zxart_on_download_clicked)

    # ---- Downloads overlay dialog ----

    def zxart_show_downloads_overlay(title: str, downloads: list):
        if not downloads:
            zxart_set_status("No downloadable files for this entry.")
            return

        dlg = QDialog(host)
        dlg.setWindowTitle(f"Downloads — {title}")
        dlg.resize(1180, 460)
        v = QVBoxLayout(dlg)

        info = QLabel(
            f"<b>{len(downloads)}</b> file(s) for <b>{title}</b>. "
            f"'Download all' saves into downloads\\{zxart_sanitize_folder(title)}\\"
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
        if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
            for _c in (COL_SD, COL_NS):
                tbl.setColumnWidth(_c, 0)
                tbl.setColumnHidden(_c, True)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        folder_root = os.path.abspath(os.path.join("downloads", zxart_sanitize_folder(title)))
        _ns_base_dlg = _zxart_resolve_base_path(
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
            _active_cols = [COL_DL] if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS else [COL_DL, COL_SD, COL_NS]
            for _col in _active_cols:
                btn_w = tbl.cellWidget(row, _col)
                if btn_w is not None:
                    btn_w.setEnabled(ok)

        def _check_url(row: int, url: str):
            def _fn():
                return _http_head_ok_with_retry(
                    zxart_safe_url(url), headers={"User-Agent": ZXART_USER_AGENT}, timeout=10
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
                zxart_set_status(f"Downloading {fname}…")
                def _ok(p):
                    zxart_set_status(f"Saved {fname}  ↗ open folder", open_path=os.path.dirname(os.path.abspath(p)))
                def _err(e):
                    zxart_set_status(f"Download error: {e[1]}")
                zxart_download_to_path(d.get("url", ""), save_path, _ok, _err)
            return _go

        def _make_sd_handler(d):
            def _go():
                if not _right_disk_content() or not host.right_disk_image_path:
                    zxart_set_status("Please load a disk image first (SD Card tab).")
                    return
                _zxart_send_to_image(title, [d])
            return _go

        def _make_ns_handler(d):
            def _go():
                def _after(_folder):
                    QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
                _zxart_send_to_path(title, [d], _ns_base_dlg, _after)
            return _go

        for row, d in enumerate(downloads):
            fname = d.get("filename") or os.path.basename(
                urllib.parse.urlparse(d.get("url", "")).path
            ) or ""
            tbl.setItem(row, 0, QTableWidgetItem(d.get("type") or d.get("format") or ""))
            tbl.setItem(row, 1, QTableWidgetItem(fname))
            tbl.setItem(row, 2, QTableWidgetItem(zxart_human_size(d.get("size"))))
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

            if ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
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
        dl_all_btn = QPushButton(f"Download all → downloads\\{zxart_sanitize_folder(title)}")
        sd_all_btn = QPushButton("Send all to SD Card")
        ns_all_btn = QPushButton("Send all via NextSync")
        close_btn  = QPushButton("Close")
        btn_row.addWidget(dl_all_btn)
        if ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
            btn_row.addWidget(sd_all_btn)
            btn_row.addWidget(ns_all_btn)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        close_btn.clicked.connect(dlg.accept)

        def _eligible():
            return [d for i, d in enumerate(downloads) if _avail[i] is not False]

        def _send_all_sd():
            if not _right_disk_content() or not host.right_disk_image_path:
                zxart_set_status("Please load a disk image first (SD Card tab).")
                return
            items = _eligible()
            if not items:
                zxart_set_status("All files are unavailable (404).")
                return
            _zxart_send_to_image(title, items)

        def _send_all_ns():
            items = _eligible()
            if not items:
                zxart_set_status("All files are unavailable (404).")
                return
            def _after(_folder):
                QTimer.singleShot(0, lambda _f=_folder: host._nextsync_start_server_fn(_f))
            _zxart_send_to_path(title, items, _ns_base_dlg, _after)

        sd_all_btn.clicked.connect(_send_all_sd)
        ns_all_btn.clicked.connect(_send_all_ns)

        def _download_all():
            dl_all_btn.setEnabled(False)
            dl_all_btn.setText("Downloading…")
            # Skip files confirmed unavailable (404); include pending/ok ones
            eligible = [d for i, d in enumerate(downloads) if _avail[i] is not False]
            if not eligible:
                dl_all_btn.setText("Nothing to download")
                zxart_set_status("All files are unavailable (404).")
                return
            pending = {"n": len(eligible), "ok": 0, "ko": 0}

            def _maybe_finish():
                if pending["ok"] + pending["ko"] >= pending["n"]:
                    dl_all_btn.setText(f"Done — {pending['ok']} ok, {pending['ko']} failed")
                    if pending["ok"] > 0:
                        zxart_set_status(
                            f"Downloaded {pending['ok']}/{pending['n']} file(s) into {folder_root}  ↗ open folder",
                            open_path=folder_root
                        )
                    else:
                        zxart_set_status(f"All {pending['n']} download(s) failed — check the URLs")

            for d in eligible:
                fname = d.get("filename") or os.path.basename(
                    urllib.parse.urlparse(d.get("url", "")).path
                ) or "file.bin"
                save_path = os.path.join(folder_root, fname)
                def _ok(p, _f=fname):
                    pending["ok"] += 1
                    zxart_set_status(f"Saved {_f}")
                    _maybe_finish()
                def _err(e, _f=fname):
                    pending["ko"] += 1
                    zxart_set_status(f"Failed {_f}: {e[1]}")
                    _maybe_finish()
                zxart_download_to_path(d.get("url", ""), save_path, _ok, _err)

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

    # ---- Context menu ----

    def zxart_on_table_context_menu(pos):
        item = host.zxart_results_table.itemAt(pos)
        if item is None:
            return
        row = host.zxart_results_table.row(item)
        id_item    = host.zxart_results_table.item(row, 0)
        title_item = host.zxart_results_table.item(row, 1)
        if not id_item:
            return
        pid   = id_item.text()
        title = title_item.text() if title_item else pid
        entry = id_item.data(Qt.UserRole) or {}
        kind  = entry.get("_kind", "zxart_prod")

        host.zxart_results_table.selectRow(row)

        _img_path   = host.right_disk_image_path or ""
        _img_label  = (generate_disk_file_path().rstrip("/") + "/" + zxart_sanitize_folder(title)
                       ) if _img_path else "(no image loaded)"
        _sd_dest    = f"{_img_path}  :  {_img_label}" if _img_path else "(no image loaded)"
        _ns_base    = _zxart_resolve_base_path(host.left_file_nextsync_explorer_selection_full_filename_path)
        _safe_title = zxart_sanitize_folder(title)
        _ns_dest    = os.path.join(_ns_base, _safe_title)

        menu = QMenu(host.zxart_results_table)
        act_download = menu.addAction("Download content")
        menu.addSeparator()
        act_send_sd  = menu.addAction(f"Send to SD card (image)  →  {_sd_dest}")
        act_send_sd.setEnabled(bool(host.right_disk_image_path) and bool(_right_disk_content()))
        act_send_ns  = menu.addAction(f"Send using NextSync  →  {_ns_dest}")
        if not ZX_NEXT_UNITE_ZXART_ENABLE_DOWNLOAD_BUTTONS:
            act_download.setVisible(False)
            act_send_sd.setVisible(False)
            act_send_ns.setVisible(False)
        menu.addSeparator()
        _web_url = zxart_entry_website_url(entry)
        act_open_web = menu.addAction("Open on website (zxart.ee)")
        act_open_web.setEnabled(bool(_web_url))
        action = menu.exec(host.zxart_results_table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is act_open_web:
            if _web_url:
                try:
                    webbrowser.open(_web_url, new=2)
                except Exception:
                    pass
            return

        def _ensure_detail_then(callback):
            """If detail for this row is already loaded, call callback immediately."""
            if host._zxart_selected_id == pid and host._zxart_selected_downloads:
                callback(host._zxart_selected_title or title, host._zxart_selected_downloads)
                return
            zxart_set_status(f"Loading {pid}…")
            if kind == "zxart_picture":
                def _fn():
                    pic_resp = zxart_fetch_json(
                        f"/export:zxPicture/language:{_zxart_lang()}/filter:zxPictureId={urllib.parse.quote(pid)}"
                    )
                    pics = (pic_resp.get("responseData") or {}).get("zxPicture") or []
                    pic  = pics[0] if pics else (entry.get("_source") or {})
                    image_url    = pic.get("imageUrl") or ""
                    original_url = pic.get("originalUrl") or ""
                    downloads = []
                    if original_url:
                        fname = os.path.basename(urllib.parse.urlparse(original_url).path) or f"{pid}.bin"
                        downloads.append({"url": original_url, "filename": fname, "type": "original",
                                          "format": "", "size": "", "source": "zxart"})
                    if image_url and image_url != original_url:
                        fname_img = os.path.basename(urllib.parse.urlparse(image_url).path) or f"{pid}.png"
                        downloads.append({"url": image_url, "filename": fname_img, "type": "preview (PC)",
                                          "format": "", "size": "", "source": "zxart"})
                    return (str(pic.get("title") or title), downloads)
                def _on_ok(res, _cb=callback):
                    t2, dls = res
                    host._zxart_selected_title = t2
                    host._zxart_selected_downloads = dls
                    host.zxart_download_button.setEnabled(bool(dls))
                    _cb(t2, dls)
                def _on_err(err):
                    zxart_set_status(f"Detail error: {err[1]}")
                host._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)
            else:
                def _fn():
                    rel_resp = zxart_fetch_json(
                        f"/action:filter/export:zxRelease/filter:zxProdId={urllib.parse.quote(pid)}"
                    )
                    releases = (rel_resp.get("responseData") or {}).get("zxRelease") or []
                    prod_resp = zxart_fetch_json(
                        f"/export:zxProd/language:{_zxart_lang()}/filter:zxProdId={urllib.parse.quote(pid)}"
                    )
                    prods = (prod_resp.get("responseData") or {}).get("zxProd") or []
                    prod  = prods[0] if prods else {}
                    downloads = []
                    for rel in releases:
                        if not isinstance(rel, dict):
                            continue
                        file_url  = rel.get("file") or ""
                        file_name = rel.get("fileName") or (
                            os.path.basename(urllib.parse.urlparse(file_url).path) if file_url else ""
                        )
                        if not file_url:
                            continue
                        downloads.append({
                            "url":      file_url,
                            "filename": file_name,
                            "type":     f"{rel.get('releaseType') or ''} / {rel.get('releaseFormat') or ''}".strip(" /") or "release",
                            "format":   rel.get("releaseFormat") or "",
                            "size":     "",
                            "source":   rel.get("title") or "zxart",
                        })
                    return (str(prod.get("title") or title), downloads)
                def _on_ok(res, _cb=callback):
                    t2, dls = res
                    host._zxart_selected_title = t2
                    host._zxart_selected_downloads = dls
                    host.zxart_download_button.setEnabled(bool(dls))
                    _cb(t2, dls)
                def _on_err(err):
                    zxart_set_status(f"Detail error: {err[1]}")
                host._zxart_ctx_thread = getit_run_in_thread(_fn, _on_ok, _on_err)

        if action is act_download:
            def _show(t, dls):
                zxart_show_downloads_overlay(t, dls)
            _ensure_detail_then(_show)

        elif action is act_send_sd:
            def _send_sd(t, dls):
                _zxart_send_to_image(t, dls)
            _ensure_detail_then(_send_sd)

        elif action is act_send_ns:
            def _send_ns(t, dls, _nb=_ns_base):
                def _after(_folder):
                    QTimer.singleShot(0, host._nextsync_start_server_fn)
                _zxart_send_to_path(t, dls, _nb, _after)
            _ensure_detail_then(_send_ns)

    host.zxart_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
    host.zxart_results_table.customContextMenuRequested.connect(zxart_on_table_context_menu)

    # ---- Fullscreen preview overlay ----

    zxart_container = QWidget()
    zxart_container.setLayout(host.zxart_form)
    zxart_container.setAutoFillBackground(False)
    zxart_container.setAttribute(Qt.WA_TranslucentBackground)

    zxart_scroll = QScrollArea()
    zxart_scroll.setWidget(zxart_container)
    zxart_scroll.setWidgetResizable(True)
    zxart_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    zxart_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    zxart_scroll.setAutoFillBackground(False)
    zxart_scroll.setAttribute(Qt.WA_TranslucentBackground)
    zxart_scroll.viewport().setAutoFillBackground(False)
    zxart_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

    # Fixed search/button header above the scrollable results so the
    # vertical scroller only covers the content area (like the Unite! tab).
    zxart_normal_widget = QWidget()
    zxart_normal_widget.setAutoFillBackground(False)
    zxart_normal_widget.setAttribute(Qt.WA_TranslucentBackground)
    zxart_normal_layout = QVBoxLayout(zxart_normal_widget)
    zxart_normal_layout.setContentsMargins(0, 0, 0, 0)
    zxart_normal_layout.setSpacing(0)
    zxart_normal_layout.addWidget(host._zxart_search_widget, 0)
    zxart_normal_layout.addWidget(zxart_scroll, 1)

    host._zxart_fullscreen_pixmap = None

    zxart_overlay = QWidget()
    zxart_overlay.setStyleSheet("background: #000;")
    zxart_overlay_layout = QVBoxLayout(zxart_overlay)
    zxart_overlay_layout.setContentsMargins(0, 0, 0, 0)
    zxart_overlay_layout.setSpacing(0)

    zxart_close_btn = QToolButton()
    zxart_close_btn.setText("✕")
    zxart_close_btn.setStyleSheet(
        "QToolButton { color: white; background: #333; border: none; font-size: 18px; padding: 4px 8px; }"
        "QToolButton:hover { background: #c00; }"
    )
    zxart_close_bar = QHBoxLayout()
    zxart_close_bar.setContentsMargins(4, 4, 4, 0)
    zxart_close_bar.addWidget(zxart_close_btn, 0)
    zxart_close_bar.addStretch()
    zxart_close_bar_widget = QWidget()
    zxart_close_bar_widget.setLayout(zxart_close_bar)
    zxart_overlay_layout.addWidget(zxart_close_bar_widget, 0)

    host.zxart_fullscreen_label = _ScalingImageLabel()
    host.zxart_fullscreen_label.setAlignment(Qt.AlignCenter)
    host.zxart_fullscreen_label.setStyleSheet("background: #000;")
    host.zxart_fullscreen_label.setCursor(Qt.PointingHandCursor)
    zxart_overlay_layout.addWidget(host.zxart_fullscreen_label, 1)

    _zxart_fs_nav_style = (
        "QToolButton { color: white; background: rgba(0,0,0,140); border: none;"
        " font-size: 32px; font-weight: bold; padding: 4px 10px; }"
        "QToolButton:hover { background: rgba(0,0,0,220); }"
    )
    host.zxart_fs_prev_btn = QToolButton(zxart_overlay)
    host.zxart_fs_prev_btn.setText("<")
    host.zxart_fs_prev_btn.setStyleSheet(_zxart_fs_nav_style)
    host.zxart_fs_prev_btn.setVisible(False)
    host.zxart_fs_prev_btn.raise_()

    host.zxart_fs_next_btn = QToolButton(zxart_overlay)
    host.zxart_fs_next_btn.setText(">")
    host.zxart_fs_next_btn.setStyleSheet(_zxart_fs_nav_style)
    host.zxart_fs_next_btn.setVisible(False)
    host.zxart_fs_next_btn.raise_()

    def _zxart_reposition_fs_btns():
        ow = zxart_overlay.width()
        oh = zxart_overlay.height()
        bh = host.zxart_fs_prev_btn.sizeHint().height()
        by = (oh - bh) // 2
        host.zxart_fs_prev_btn.move(8, by)
        bw = host.zxart_fs_next_btn.sizeHint().width()
        host.zxart_fs_next_btn.move(ow - bw - 8, by)

    host._zxart_reposition_fs_btns = _zxart_reposition_fs_btns
    host.zxart_fs_prev_btn.clicked.connect(_zxart_nav_prev)
    host.zxart_fs_next_btn.clicked.connect(_zxart_nav_next)

    host._zxart_stack = QStackedWidget()
    host._zxart_stack.setAutoFillBackground(False)
    host._zxart_stack.setAttribute(Qt.WA_TranslucentBackground)
    host._zxart_stack.addWidget(zxart_normal_widget)
    host._zxart_stack.addWidget(zxart_overlay)
    host._zxart_stack.setCurrentIndex(0)

    def _zxart_show_fullscreen():
        px = host.zxart_screenshot_label.pixmap()
        if px is None or px.isNull():
            return
        host._zxart_fullscreen_pixmap = px
        host._zxart_stack.setCurrentIndex(1)
        _zxart_resize_fullscreen()
        host._zxart_reposition_fs_btns()
        zxart_update_nav_buttons()

    def _zxart_hide_fullscreen():
        host._zxart_stack.setCurrentIndex(0)
        zxart_update_nav_buttons()
    host._hide_fullscreen_zxart = _zxart_hide_fullscreen

    def _zxart_resize_fullscreen():
        px = host._zxart_fullscreen_pixmap
        if px and not px.isNull():
            host.zxart_fullscreen_label.set_image(px)
        host._zxart_reposition_fs_btns()

    zxart_close_btn.clicked.connect(_zxart_hide_fullscreen)
    host.zxart_fullscreen_label.mousePressEvent = lambda e: _zxart_hide_fullscreen()

    host._zxart_dbl_filter = _DblClickFilter(_zxart_show_fullscreen)
    host.zxart_screenshot_label.installEventFilter(host._zxart_dbl_filter)
    host.zxart_screenshot_label.setCursor(Qt.PointingHandCursor)

    def zxart_on_tab_activated():
        if host._zxart_loaded_once or host._zxart_search_loading:
            return
        host._zxart_loaded_once = True
        # Skip default load if a cross-search already populated results
        if host._zxart_last_query:
            return
        # Load latest productions on first activation
        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
        def _zxart_initial_done():
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART, host.zxart_results_table.rowCount())
        zxart_run_search("", 1, _zxart_initial_done)

    host._zxart_on_tab_activated = zxart_on_tab_activated
    host.zxart_run_search = zxart_run_search
    host.zxart_on_latest = zxart_on_latest
    host.zxart_on_random = zxart_on_random
    host._zxart_open_gallery_viewer = _zxart_open_gallery_viewer
