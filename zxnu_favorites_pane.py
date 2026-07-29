"""zxnu_favorites_pane.py — Favorites tab + per-pane Classic/Retro routing.

Strangler extraction from MainWindow.__init__ (same builder-function seam as
the other pane modules), in THREE builders because the original block is
interleaved with the pane-builder calls and tab assembly that stay in the
monolith — each is called at its chunk's historical __init__ position:

* build_favorites_helpers(host, ...) — the cross-pane favorite helpers
  (_fav_source_of/_fav_is/_fav_toggle/_fav_save/_fav_navigate_to_source, tab
  badge + gallery refresh). Runs BEFORE the NextSync/GetIt/ZXDB/zxArt pane
  builders, which consume host._fav_is/_fav_toggle at runtime; the three
  run_search closures those builders bind later are injected as forwarding
  lambdas.
* build_favorites_pane(host, ...)    — the Favorites tab widget layer (getters,
  GalleryView, table, view-mode selector row, stack, scroll, addTab). Also
  exposes host._fav_title_getter/_fav_info_getter/_fav_thumb_fetch/
  _fav_extra_fetch for the Unite! builders and build_favorites_ops (re-bound to
  bare __init__ locals at the call site).
* build_favorites_ops(host, ...)     — _fav_repopulate + the view-mode apply
  helper (construction-time apply included, persist=False) and the _pane_*
  per-pane Classic ↔ Retro item-viewer routing layer. Inside its closures the
  original local variable ``host`` (a PygameSurfaceWidget) was renamed
  ``pg_host`` so it cannot shadow the builder's ``host`` param.

Everything the blocks assigned to ``self`` is written to ``host`` (the
MainWindow), so every historical attribute keeps working. See CLAUDE.md and
the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QLabel, QComboBox, QHBoxLayout,
    QVBoxLayout, QGridLayout, QScrollArea, QStackedWidget, QFrame,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QMenu)

from zxnu_config import *
from zxnu_gallery import *
from zxnu_i18n import ui_tr_now


def build_favorites_helpers(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    getit_run_search,
    zxdb_run_search,
    zxart_run_search,
):
    """Cross-pane favorite record/toggle/navigate helpers (no widgets)."""
    # ── Favorites helpers (cross-pane, captured by closures below) ──
    _FAV_SOURCE_LABELS = {"getit": "GetIt", "zxdb": "ZXDB", "zxart": "zxArt",
                          "itchio": "itch.io"}

    def _fav_source_of(entry):
        """Best-effort detection of which pane an entry came from."""
        if not isinstance(entry, dict):
            return ""
        s = (entry.get("_fav_source") or entry.get("source") or "").lower()
        if s in _FAV_SOURCE_LABELS:
            return s
        kind = (entry.get("_kind") or "").lower()
        if kind.startswith("zxart"):
            return "zxart"
        if kind in ("game", "magazine", "suggest"):
            return "zxdb"
        if entry.get("category") is not None or "size" in entry:
            return "getit"
        return ""

    def _fav_key(source, entry_id):
        return (str(source or ""), str(entry_id or ""))

    def _fav_is(entry):
        if not isinstance(entry, dict):
            return False
        src = _fav_source_of(entry)
        eid = entry.get("id") or ""
        if not src or not eid:
            return False
        return _fav_key(src, eid) in host._favorites_index

    def _fav_make_record(entry, source):
        rec = {}
        try:
            # Deep copy via JSON so we keep a serializable, decoupled
            # snapshot of the upstream entry (incl. _source, _kind, etc).
            rec = json.loads(json.dumps(entry, ensure_ascii=False, default=str))
        except Exception:
            rec = {}
        rec["source"]      = source
        rec["_fav_source"] = source
        rec["id"]          = str(entry.get("id") or "")
        return rec

    def _fav_save():
        try:
            configuration_dictionary[SETTING_FAVORITES] = json.dumps(
                host._favorites, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            configuration_dictionary[SETTING_FAVORITES] = "[]"
        save_configuration_file()

    def _fav_update_tab_badge():
        try:
            idx = -1
            for i in range(host._tab_widget.count()):
                if host._tab_widget.tabText(i).startswith(
                        ZX_NEXT_UNITE_TAB_TITLE_FAVORITES):
                    idx = i
                    break
            if idx < 0:
                return
            n = len(host._favorites)
            host._tab_widget.setTabText(
                idx, f"{ZX_NEXT_UNITE_TAB_TITLE_FAVORITES} ({n})")
        except Exception:
            pass

    def _fav_refresh_all_galleries():
        for attr in ("getit_gallery_view", "zxdb_gallery_view",
                     "zxart_gallery_view", "favorites_gallery_view",
                     "itchio_gallery_view", "allinone_gallery_view"):
            gv = getattr(host, attr, None)
            if gv is not None:
                try:
                    gv.refresh_favorites()
                except Exception:
                    pass
        # Re-populate the favorites grid so removals disappear and adds
        # show up.
        try:
            _fav_repopulate = getattr(host, "_fav_repopulate_fn", None)
            if _fav_repopulate is not None and not host._favorites_refreshing:
                host._favorites_refreshing = True
                try:
                    _fav_repopulate()
                finally:
                    host._favorites_refreshing = False
        except Exception:
            pass

    def _fav_toggle(entry):
        if not isinstance(entry, dict):
            return
        src = _fav_source_of(entry)
        eid = entry.get("id") or ""
        if not src or not eid:
            return
        key = _fav_key(src, eid)
        if key in host._favorites_index:
            host._favorites = [
                f for f in host._favorites
                if _fav_key(f.get("source"), f.get("id")) != key
            ]
            host._favorites_index.discard(key)
        else:
            rec = _fav_make_record(entry, src)
            host._favorites.append(rec)
            host._favorites_index.add(key)
        _fav_save()
        _fav_update_tab_badge()
        _fav_refresh_all_galleries()

    host._fav_is               = _fav_is
    host._fav_toggle           = _fav_toggle
    host._fav_source_of        = _fav_source_of
    host._fav_update_tab_badge = _fav_update_tab_badge
    host._fav_refresh_all      = _fav_refresh_all_galleries
    host._fav_source_label_for = lambda e: _FAV_SOURCE_LABELS.get(
        _fav_source_of(e), "")

    def _fav_navigate_to_source(entry):
        if not isinstance(entry, dict):
            return
        src   = host._fav_source_of(entry)
        eid   = entry.get("id") or ""
        title = entry.get("title") or ""
        query = str(title or eid).strip()
        if not src:
            return
        try:
            if src == "getit":
                target_title = ZX_NEXT_UNITE_TAB_TITLE_GETIT
            elif src == "zxdb":
                target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXDB
            elif src == "zxart":
                target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXART
            else:
                return
            # Switch to the proper pane first.
            for i in range(host._tab_widget.count()):
                if host._tab_widget.tabText(i).startswith(target_title):
                    host._tab_widget.setCurrentIndex(i)
                    break

            def _select_in(view_attr):
                gv = getattr(host, view_attr, None)
                if gv is None:
                    return
                try:
                    gv.select_entry(lambda e, _eid=str(eid):
                                    str(e.get("id") or "") == _eid)
                except Exception:
                    pass

            if src == "getit":
                host.getit_search_input.setText(query)
                def _gi_done(_va="getit_gallery_view"):
                    _select_in(_va)
                getit_run_search(query, 1, _gi_done)
            elif src == "zxdb":
                host.zxdb_search_input.setText(query)
                def _zd_done(_va="zxdb_gallery_view"):
                    _select_in(_va)
                zxdb_run_search(query, 1, _zd_done)
            elif src == "zxart":
                host.zxart_search_input.setText(query)
                def _za_done(_va="zxart_gallery_view"):
                    _select_in(_va)
                zxart_run_search(query, 1, _za_done)
        except Exception:
            pass

    host._fav_navigate_to_source = _fav_navigate_to_source


def build_favorites_pane(
    host,
    *,
    wid_inner,
    _persist_retro,
    _make_retro_toggle_button,
    _gif_fetch_bytes,
    _getit_open_gallery_viewer,
    _zxdb_open_gallery_viewer,
    _zxart_open_gallery_viewer,
):
    """Build the Favorites tab widgets (getters, views, selector, addTab)."""
    # Create ONLINE Favorites Tab (right of zxArt, before Settings)
    zxnextunite_Favorites_tab = QWidget(wid_inner.tab)
    zxnextunite_Favorites_tab.setAttribute(Qt.WA_TranslucentBackground)
    zxnextunite_Favorites_tab.setAutoFillBackground(False)
    grid_tab_favorites = QGridLayout(zxnextunite_Favorites_tab)
    grid_tab_favorites.setContentsMargins(0, 0, 0, 0)

    def _fav_title_getter(e):
        src = (e.get("_fav_source") or e.get("source") or "").lower()
        fetch = (host._fav_fetchers or {}).get(src) or {}
        tg = fetch.get("title")
        if tg:
            try:
                return tg(e)
            except Exception:
                pass
        return str(e.get("title") or e.get("id") or "")

    def _fav_info_getter(e):
        src = (e.get("_fav_source") or e.get("source") or "").lower()
        fetch = (host._fav_fetchers or {}).get(src) or {}
        ig = fetch.get("info")
        if ig:
            try:
                return ig(e)
            except Exception:
                pass
        parts = []
        if e.get("author"): parts.append(str(e["author"]))
        if e.get("year"):   parts.append(str(e["year"]))
        return " · ".join(parts)

    def _fav_thumb_fetch(entry, set_pixmap, set_screenshots,
                         set_tags=None, set_info_text=None):
        src = (entry.get("_fav_source") or entry.get("source") or "").lower()
        fetch = (host._fav_fetchers or {}).get(src) or {}
        tf = fetch.get("thumb")
        if tf is None:
            return
        try:
            # Each pane's thumb fetcher has a slightly different signature.
            if src == "getit":
                tf(entry, set_pixmap, set_screenshots,
                   set_tags=set_tags, set_info_text=set_info_text)
            elif src == "zxart":
                tf(entry, set_pixmap, set_screenshots, set_tags=set_tags)
            else:
                tf(entry, set_pixmap, set_screenshots)
        except Exception:
            pass

    def _fav_extra_fetch(url, on_pixmap):
        for src in ("getit", "zxdb", "zxart", "itchio"):
            fetch = (host._fav_fetchers or {}).get(src) or {}
            ef = fetch.get("extra")
            if ef is not None:
                try:
                    ef(url, on_pixmap)
                except Exception:
                    pass

    def _fav_context_menu(entry, global_pos):
        menu = QMenu()
        src_lbl = host._fav_source_label_for(entry) or "source"
        act_go = menu.addAction(ui_tr_now("Open in {source}").format(source=src_lbl))
        act_rm = menu.addAction(ui_tr_now("Remove from Favorites"))
        chosen = menu.exec(global_pos)
        if chosen is act_go:
            host._fav_navigate_to_source(entry)
        elif chosen is act_rm:
            host._fav_toggle(entry)

    host.favorites_gallery_view = GalleryView(
        rows_per_page_getter=lambda: host._gallery_rows_per_page,
        anim_mode_getter=lambda: host._gallery_anim_mode,
        cols_getter=lambda: host._gallery_cols,
        img_size_getter=lambda: host._gallery_img_size,
        thumb_fetch_cb=_fav_thumb_fetch,
        extra_fetch_cb=_fav_extra_fetch,
        title_getter=_fav_title_getter,
        info_getter=_fav_info_getter,
        context_menu_cb=_fav_context_menu,
        is_favorite_cb=lambda e: True,
        toggle_favorite_cb=lambda e: host._fav_toggle(e),
        source_label_getter=host._fav_source_label_for,
    )
    # Animate .gif thumbnails (QMovie) just like the in-pane item viewer.
    host.favorites_gallery_view.set_gif_fetch_cb(_gif_fetch_bytes)

    def _fav_open_fullscreen(entry):
        if not isinstance(entry, dict):
            return
        src = host._fav_source_of(entry)
        if src == "getit":
            target_title = ZX_NEXT_UNITE_TAB_TITLE_GETIT
            opener = _getit_open_gallery_viewer
        elif src == "zxdb":
            target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXDB
            opener = _zxdb_open_gallery_viewer
        elif src == "zxart":
            target_title = ZX_NEXT_UNITE_TAB_TITLE_ZXART
            opener = _zxart_open_gallery_viewer
        elif src == "itchio" and getattr(host, "_itchio_open_gallery_viewer", None):
            target_title = ZX_NEXT_UNITE_TAB_TITLE_ITCHIO
            opener = host._itchio_open_gallery_viewer
        else:
            host._fav_navigate_to_source(entry)
            return
        # Switch to the source tab so the viewer stack is visible.
        for i in range(host._tab_widget.count()):
            if host._tab_widget.tabText(i).startswith(target_title):
                host._tab_widget.setCurrentIndex(i)
                break
        try:
            # Honour the Favorites pane's Classic/Retro toggle, opening the
            # item in the source pane's chosen viewer mode.
            host._pane_open_item(
                src, entry, getattr(host, "_favorites_item_retro", False))
        except Exception:
            pass

    host._fav_open_fullscreen = _fav_open_fullscreen

    def _fav_on_cell_dbl_clicked(entry):
        _fav_open_fullscreen(entry)
    host.favorites_gallery_view.cell_dbl_clicked.connect(_fav_on_cell_dbl_clicked)

    # ── View: Table / Gallery selector row ──────────────────────────────
    fav_top_row = QHBoxLayout()
    fav_top_row.setContentsMargins(0, 0, 0, 0)
    host.favorites_view_text_label = QLabel("View:")
    fav_top_row.addWidget(host.favorites_view_text_label)
    host.favorites_view_combo = QComboBox()
    host.favorites_view_combo.addItem("Table",   "table")
    host.favorites_view_combo.addItem("Gallery", "gallery")
    host.favorites_view_combo.setToolTip(
        "Switch between the classic table view and the picture (gallery) view.\n"
        "Persisted across sessions in the config file."
    )
    fav_top_row.addWidget(host.favorites_view_combo)
    host.favorites_retro_button = _make_retro_toggle_button(
        host, "_favorites_item_retro",
        on_change=lambda c, k=SETTING_FAVORITES_ITEM_RETRO: (
            _persist_retro(k, c), host._pane_retro_gallery_set("favorites", c)))
    fav_top_row.addWidget(host.favorites_retro_button)
    fav_top_row.addStretch(1)

    # ── Table view of favorites ────────────────────────────────────────
    host.favorites_results_table = QTableWidget(0, 5)
    host.favorites_results_table.setHorizontalHeaderLabels(
        ["Source", "Title", "Rating", "Info", "Year"]
    )
    host.favorites_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    host.favorites_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    host.favorites_results_table.setSelectionMode(QAbstractItemView.SingleSelection)
    host.favorites_results_table.verticalHeader().setVisible(False)
    try:
        hh = host.favorites_results_table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    except Exception:
        pass

    def _fav_table_entry_for_row(row):
        try:
            if row < 0 or row >= host.favorites_results_table.rowCount():
                return None
            it = host.favorites_results_table.item(row, 0)
            if it is None:
                return None
            idx = it.data(Qt.UserRole)
            if isinstance(idx, int) and 0 <= idx < len(host._favorites):
                return host._favorites[idx]
        except Exception:
            pass
        return None

    def _fav_table_on_double_clicked(_idx):
        row = host.favorites_results_table.currentRow()
        entry = _fav_table_entry_for_row(row)
        if entry is not None:
            _fav_open_fullscreen(entry)

    def _fav_table_on_context_menu(pos):
        row = host.favorites_results_table.rowAt(pos.y())
        entry = _fav_table_entry_for_row(row)
        if entry is None:
            return
        host.favorites_results_table.selectRow(row)
        _fav_context_menu(
            entry,
            host.favorites_results_table.viewport().mapToGlobal(pos),
        )

    host.favorites_results_table.doubleClicked.connect(_fav_table_on_double_clicked)
    host.favorites_results_table.setContextMenuPolicy(Qt.CustomContextMenu)
    host.favorites_results_table.customContextMenuRequested.connect(
        _fav_table_on_context_menu
    )

    # ── Stack the two views ────────────────────────────────────────────
    host.favorites_view_stack = QStackedWidget()
    host.favorites_view_stack.addWidget(host.favorites_results_table)   # idx 0 = table
    host.favorites_view_stack.addWidget(host.favorites_gallery_view)    # idx 1 = gallery

    fav_container = QWidget()
    fav_container.setAutoFillBackground(False)
    fav_container.setAttribute(Qt.WA_TranslucentBackground)
    fav_v = QVBoxLayout(fav_container)
    fav_v.setContentsMargins(0, 0, 0, 0)
    fav_v.addLayout(fav_top_row)
    fav_v.addWidget(host.favorites_view_stack)
    fav_container.setLayout(fav_v)

    # Wrap in a scroll area so a vertical scrollbar appears when the tab is
    # too short for its content — matching the GetIt / ZXDB / zxArt tabs.
    fav_scroll = QScrollArea()
    fav_scroll.setWidget(fav_container)
    fav_scroll.setWidgetResizable(True)
    fav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    fav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    fav_scroll.setFrameShape(QFrame.NoFrame)
    fav_scroll.setAutoFillBackground(False)
    fav_scroll.setAttribute(Qt.WA_TranslucentBackground)
    fav_scroll.viewport().setAutoFillBackground(False)
    fav_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

    grid_tab_favorites.addWidget(fav_scroll)
    zxnextunite_Favorites_tab.setLayout(grid_tab_favorites)
    zxnextunite_Favorites_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_FAVORITES
    wid_inner.tab.addTab(zxnextunite_Favorites_tab,
                         f"{ZX_NEXT_UNITE_TAB_TITLE_FAVORITES} (0)")

    # Expose the getters the Unite! builders and build_favorites_ops consume
    # (re-bound to bare __init__ locals at the call site).
    host._fav_title_getter = _fav_title_getter
    host._fav_info_getter = _fav_info_getter
    host._fav_thumb_fetch = _fav_thumb_fetch
    host._fav_extra_fetch = _fav_extra_fetch


def build_favorites_ops(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    _gif_fetch_bytes,
    _getit_open_gallery_viewer,
    _zxdb_open_gallery_viewer,
    _zxart_open_gallery_viewer,
    _fav_title_getter,
    _fav_info_getter,
    _fav_thumb_fetch,
):
    """Favorites repopulate/view-mode ops + the _pane_* routing layer."""
    def _fav_repopulate():
        try:
            host.favorites_gallery_view.populate(list(host._favorites))
            host._pane_retro_gallery_refresh("favorites")
        except Exception:
            pass
        try:
            tbl = host.favorites_results_table
            tbl.setRowCount(0)
            for i, entry in enumerate(host._favorites):
                import re as _re
                src_lbl = host._fav_source_label_for(entry) or ""
                raw_title = _fav_title_getter(entry) or ""
                # Extract star rating from an HTML <span> if present
                _span_match = _re.search(r'<span[^>]*>([^<]+)</span>', raw_title)
                rating = _span_match.group(1).strip() if _span_match else ""
                # Strip all HTML tags to get a clean title
                title = _re.sub(r'<[^>]+>', '', raw_title).strip()
                # Also strip any inline star rating remaining in plain text,
                # e.g. "Some title★★★★☆  (4.2)" — capture it as rating if not already set
                _plain_match = _re.search(r'\s*[★☆]+\s*[\d.,]*\s*(?:\([^)]*\))?\s*$', title)
                if _plain_match:
                    if not rating:
                        rating = _plain_match.group(0).strip()
                    title = title[:_plain_match.start()].strip()
                info  = _fav_info_getter(entry) or ""
                year  = str(entry.get("year") or "")
                row = tbl.rowCount()
                tbl.insertRow(row)
                src_item    = QTableWidgetItem(src_lbl)
                src_item.setData(Qt.UserRole, i)
                title_item  = QTableWidgetItem(title)
                rating_item = QTableWidgetItem(rating)
                rating_item.setTextAlignment(Qt.AlignCenter)
                info_item   = QTableWidgetItem(info)
                year_item   = QTableWidgetItem(year)
                tbl.setItem(row, 0, src_item)
                tbl.setItem(row, 1, title_item)
                tbl.setItem(row, 2, rating_item)
                tbl.setItem(row, 3, info_item)
                tbl.setItem(row, 4, year_item)
        except Exception:
            pass
    host._fav_repopulate_fn = _fav_repopulate
    _fav_repopulate()
    host._fav_update_tab_badge()

    # ── View-mode apply helper (mirrors GetIt/ZXDB/zxArt) ──────────────
    def _favorites_apply_view_mode(mode: str, *, persist: bool = True):
        mode = (mode or "gallery").lower()
        if mode not in ("table", "gallery"):
            mode = "gallery"
        host._favorites_view_mode = mode
        host.favorites_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
        if getattr(host, "_pane_retro_gallery_refresh", None):
            host._pane_retro_gallery_refresh("favorites")
        cb = host.favorites_view_combo
        target_idx = 1 if mode == "gallery" else 0
        if cb.currentIndex() != target_idx:
            cb.blockSignals(True)
            cb.setCurrentIndex(target_idx)
            cb.blockSignals(False)
        if persist:
            if hasattr(host, '_getit_apply_view_mode'):
                host._getit_apply_view_mode(mode, persist=False)
            if hasattr(host, '_zxdb_apply_view_mode'):
                host._zxdb_apply_view_mode(mode, persist=False)
            if hasattr(host, '_zxart_apply_view_mode'):
                host._zxart_apply_view_mode(mode, persist=False)
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

    host._favorites_apply_view_mode = _favorites_apply_view_mode

    def _on_favorites_view_combo_changed(_idx):
        _favorites_apply_view_mode(
            host.favorites_view_combo.currentData() or "gallery"
        )

    host.favorites_view_combo.currentIndexChanged.connect(
        _on_favorites_view_combo_changed
    )
    _favorites_apply_view_mode(host._favorites_view_mode, persist=False)

    # ── Per-pane Classic ↔ Retro item-viewer routing ────────────────────
    # Each source pane (GetIt/ZXDB/zxArt/itch.io) can open an item either in
    # the Classic Qt GalleryItemViewer or, when its Retro toggle is on, in
    # the pygame PygameItemViewer (which renders .txt/instruction pages as a
    # log console). The pygame viewer needs a PygameSurfaceWidget host added
    # to the pane's own stack; created lazily and reused across opens.
    host._pane_pygame_hosts = {}

    def _pane_info(src):
        return {
            "getit": (_getit_open_gallery_viewer, getattr(host, "_getit_stack", None)),
            "zxdb":  (_zxdb_open_gallery_viewer,  getattr(host, "_zxdb_stack", None)),
            "zxart": (_zxart_open_gallery_viewer, getattr(host, "_zxart_stack", None)),
            "itchio": (getattr(host, "_itchio_open_gallery_viewer", None),
                       getattr(host, "_itchio_stack", None)),
        }.get(src)

    def _ensure_pane_host(src, stack):
        pg_host = host._pane_pygame_hosts.get(src)
        if pg_host is None:
            import zxnu_pygame as _zpg
            pg_host = _zpg.PygameSurfaceWidget()
            try:
                pg_host.enable_background(getattr(host, "_allinone_pygame_anim", True))
            except Exception:
                pass
            stack.addWidget(pg_host)
            host._pane_pygame_hosts[src] = pg_host
        return pg_host

    def _pane_open_item(src, entry, retro=False):
        info = _pane_info(src)
        if not info or info[0] is None:
            return None
        opener, stack = info
        if retro and stack is not None:
            try:
                from zxnu_pygame import pygame_available, PygameItemViewer
                ok, _why = pygame_available()
            except Exception:
                ok = False
            if ok:
                pg_host = _ensure_pane_host(src, stack)
                viewer = opener(
                    entry,
                    make_viewer=lambda **kw: PygameItemViewer(
                        pg_host, anim_mode_getter=lambda: host._gallery_anim_mode, **kw),
                    install=False,
                )
                if viewer is not None:
                    if hasattr(viewer, "set_text_fetch_cb"):
                        viewer.set_text_fetch_cb(_gif_fetch_bytes)
                    if hasattr(viewer, "set_gif_fetch_cb"):
                        viewer.set_gif_fetch_cb(_gif_fetch_bytes)
                    viewer.install_into_stack(
                        None, close_fn=lambda _s=stack: _s.setCurrentIndex(0))
                    stack.setCurrentWidget(pg_host)
                return viewer
        # Classic (Qt) path — the opener installs into the pane stack itself.
        return opener(entry)
    host._pane_open_item = _pane_open_item

    # ── Per-pane Retro (pygame-rendered) gallery grid ───────────────────
    # When a pane's Retro toggle is on, its results grid is rendered by the
    # pygame TableScene/GalleryScene (like the Unite! tab) instead of the Qt
    # table/gallery. Built lazily and added as index 2 of the pane's
    # <src>_view_stack; reuses the getters the pane already registered in
    # self._fav_fetchers.
    host._pane_retro_galleries = {}   # src -> (host, table_scene, gallery_scene)

    def _pane_gallery_runtime(src):
        # (view_stack, entries_attr, view_mode_attr, label)
        return {
            "getit":  (getattr(host, "getit_view_stack", None),  "_getit_last_entries",  "_getit_view_mode",  "GetIt"),
            "zxdb":   (getattr(host, "zxdb_view_stack", None),   "_zxdb_last_entries",   "_zxdb_view_mode",   "ZXDB"),
            "zxart":  (getattr(host, "zxart_view_stack", None),  "_zxart_last_entries",  "_zxart_view_mode",  "zxArt"),
            "itchio": (getattr(host, "itchio_view_stack", None), "_itchio_last_entries", "_itchio_view_mode", "itch.io"),
            "favorites": (getattr(host, "favorites_view_stack", None), "_favorites", "_favorites_view_mode", "Favorites"),
        }.get(src)

    def _pane_retro_getters(src):
        # (title, info, thumb, open_cb, source_label, is_fav, toggle_fav)
        if src == "favorites":
            return (_fav_title_getter, _fav_info_getter, _fav_thumb_fetch,
                    lambda e: host._fav_open_fullscreen(e),
                    host._fav_source_label_for,
                    lambda e: host._fav_is(e), lambda e: host._fav_toggle(e))
        f = (getattr(host, "_fav_fetchers", None) or {}).get(src) or {}
        title = f.get("title") or (lambda e: str(e.get("title") or e.get("id") or ""))
        info = f.get("info") or (lambda e: "")
        thumb = f.get("thumb")
        label = {"getit": "GetIt", "zxdb": "ZXDB", "zxart": "zxArt",
                 "itchio": "itch.io"}.get(src, src)
        return (title, info, thumb,
                lambda e, _s=src: host._pane_open_item(_s, e, True),
                lambda _e, _l=label: _l,
                lambda e, _s=src: host._fav_is({**e, "_fav_source": _s}),
                lambda e, _s=src: host._fav_toggle({**e, "_fav_source": _s}))

    def _pane_retro_gallery_build(src):
        built = host._pane_retro_galleries.get(src)
        if built is not None:
            return built
        cfg = _pane_gallery_runtime(src)
        if not cfg or cfg[0] is None:
            return None
        import zxnu_pygame as _zpg
        title, info, thumb, open_cb, src_label, is_fav, tog_fav = _pane_retro_getters(src)
        pg_host = _zpg.PygameSurfaceWidget()
        table = _zpg.TableScene(source_label_getter=src_label, title_getter=title,
                                info_getter=info, open_cb=open_cb)
        gallery = _zpg.GalleryScene(title_getter=title, source_label_getter=src_label,
                                    thumb_fetch_cb=thumb, is_favorite_cb=is_fav,
                                    toggle_favorite_cb=tog_fav, open_cb=open_cb,
                                    cols_getter=lambda: host._gallery_cols)
        # Animate .gif thumbnails regardless of the "Gallery animation"
        # setting, mirroring the Qt gallery cells.
        try:
            gallery.set_gif_fetch_cb(_gif_fetch_bytes)
        except Exception:
            pass
        try:
            pg_host.enable_background(getattr(host, "_allinone_pygame_anim", True))
        except Exception:
            pass
        cfg[0].addWidget(pg_host)   # index 2 of the pane's view stack
        host._pane_retro_galleries[src] = (pg_host, table, gallery)
        return host._pane_retro_galleries[src]

    def _pane_retro_gallery_feed(src):
        built = host._pane_retro_galleries.get(src)
        if built is None:
            return
        cfg = _pane_gallery_runtime(src)
        entries = list(getattr(host, cfg[1], []) or []) if cfg else []
        try:
            built[1].set_entries(entries)
            built[2].set_entries(entries)
        except Exception:
            pass
    host._pane_retro_gallery_feed = _pane_retro_gallery_feed

    def _pane_retro_gallery_set_scene(src):
        built = host._pane_retro_galleries.get(src)
        cfg = _pane_gallery_runtime(src)
        if built is None or cfg is None:
            return
        mode = (getattr(host, cfg[2], "gallery") or "gallery")
        built[0].set_scene(built[2] if mode == "gallery" else built[1])

    def _pane_retro_gallery_set(src, on):
        cfg = _pane_gallery_runtime(src)
        if not cfg or cfg[0] is None:
            return
        view_stack = cfg[0]
        if on:
            try:
                from zxnu_pygame import pygame_available
                ok, _why = pygame_available()
            except Exception:
                ok = False
            if not ok:
                return
            built = _pane_retro_gallery_build(src)
            if built is None:
                return
            _pane_retro_gallery_feed(src)
            _pane_retro_gallery_set_scene(src)
            try:
                built[0].enable_background(getattr(host, "_allinone_pygame_anim", True))
            except Exception:
                pass
            view_stack.setCurrentWidget(built[0])
        else:
            mode = (getattr(host, cfg[2], "gallery") or "gallery")
            view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
    host._pane_retro_gallery_set = _pane_retro_gallery_set

    def _pane_retro_gallery_refresh(src):
        """Re-feed + re-assert the Retro grid when a pane's data or view mode
        changes while Retro is on (no-op when Classic)."""
        if not getattr(host, "_" + src + "_item_retro", False):
            return
        if host._pane_retro_galleries.get(src) is None:
            _pane_retro_gallery_set(src, True)
            return
        _pane_retro_gallery_feed(src)
        _pane_retro_gallery_set_scene(src)
        cfg = _pane_gallery_runtime(src)
        if cfg and cfg[0] is not None:
            cfg[0].setCurrentWidget(host._pane_retro_galleries[src][0])
    host._pane_retro_gallery_refresh = _pane_retro_gallery_refresh
