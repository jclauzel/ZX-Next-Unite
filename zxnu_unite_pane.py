"""zxnu_unite_pane.py — Unite! (AllInOne) aggregated pane builders.

Strangler extraction from MainWindow.__init__ (same builder-function seam as
zxnu_zxdb_pane.py / zxnu_zxart_pane.py / zxnu_getit_pane.py):

* build_unite_pane(host, ...)  — the Unite! tab's widget layer: search row,
  paging, table/gallery stack, preview panel, tab insertion and the tab-title
  colour-cycle timer start. Called from __init__ at the tab-construction spot
  (right after the Favorites tab).
* build_unite_ops(host, ...)   — the Unite! operation layer: aggregation of the
  last GetIt/ZXDB/zxArt (+ optional itch.io) results, the fan-out
  Search/Latest/Random handlers, merged autocomplete, the view-mode apply
  helper and the optional pygame ("Retro") visualization mode. Called from
  __init__ right after the itch.io tab block, at the ops blob's historical
  position; the view-mode + pygame sections were folded in from a few hundred
  lines further down (safe: they only define closures/connect signals, and
  their single construction-time call — _allinone_apply_view_mode(...,
  persist=False) — touches only widgets this module built).

Everything the blocks assigned to ``self`` is written to ``host`` (the
MainWindow), so every historical attribute keeps working; the __init__-locals
each block reads are injected as keyword-only params (forwarding lambdas at the
call site for names defined later in __init__). Inside the three pygame
closures the original local variable ``host`` (a PygameSurfaceWidget) was
renamed ``pg_host`` so it cannot shadow the builder's ``host`` param. See
CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

from PySide6.QtCore import (Qt, QTimer, QStringListModel)
from PySide6.QtGui import (QPixmap)
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QComboBox,
    QLineEdit, QHBoxLayout, QVBoxLayout, QSizePolicy, QStackedWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QCompleter)

from zxnu_config import *
from zxnu_i18n import ui_tr_now
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *


def build_unite_pane(
    host,
    *,
    _fav_title_getter,
    _fav_info_getter,
    _fav_thumb_fetch,
    _fav_extra_fetch,
    _fav_open_fullscreen,
    wid_inner,
    zxnextunite_GetIt_tab,
    _gif_fetch_bytes,
    _wrap_flow_row,
):
    """Build the Unite! tab's widgets (search row, views, preview, tab)."""
    # ─── ONLINE: AllInOne Tab ───────────────────────────────────────────
    # Aggregated gallery view of the last GetIt + ZXDB + zxArt search
    # results. A dedicated search box always runs across the 3 sources
    # (this pane has no source of its own). Each tile shows a source tag
    # (bottom-left) so the user can tell which pane produced it. Double-
    # click opens the proper source-specific full-screen viewer (same
    # routing as Favorites).
    zxnextunite_AllInOne_tab = QWidget(wid_inner.tab)
    zxnextunite_AllInOne_tab.setAttribute(Qt.WA_TranslucentBackground)
    zxnextunite_AllInOne_tab.setAutoFillBackground(False)
    allinone_v = QVBoxLayout(zxnextunite_AllInOne_tab)
    allinone_v.setContentsMargins(4, 4, 4, 4)

    # --- Search row (wraps onto extra rows when the window is narrow) ---
    allinone_search_row = FlowLayout(margin=2)
    host.allinone_search_input = QLineEdit()
    host.allinone_search_input.setPlaceholderText(
        "Search across GetIt + ZXDB + zxArt..."
    )
    host.allinone_search_input.setMinimumWidth(280)
    host.allinone_search_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    allinone_search_row.addWidget(host.allinone_search_input)

    host._allinone_search_valid_lbl = QLabel()
    host._allinone_search_valid_lbl.setVisible(False)
    allinone_search_row.addWidget(host._allinone_search_valid_lbl)

    host.allinone_search_button = QPushButton("Search")
    allinone_search_row.addWidget(host.allinone_search_button)

    host.allinone_latest_button = QPushButton("Latest")
    host.allinone_latest_button.setToolTip(
        "Fetch the latest releases from GetIt + ZXDB + zxArt and merge them here"
    )
    allinone_search_row.addWidget(host.allinone_latest_button)

    host.allinone_random_button = QPushButton("Random")
    host.allinone_random_button.setToolTip(
        "Fetch random entries from GetIt + ZXDB + zxArt and merge them here"
    )
    allinone_search_row.addWidget(host.allinone_random_button)

    allinone_search_row.addWidget(QLabel("Page:"))
    host.allinone_page_label = QLabel("1")
    host.allinone_page_label.setMinimumWidth(24)
    allinone_search_row.addWidget(host.allinone_page_label)

    host.allinone_prev_button = QPushButton("< Prev")
    host.allinone_prev_button.setEnabled(False)
    allinone_search_row.addWidget(host.allinone_prev_button)

    host.allinone_next_button = QPushButton("Next >")
    host.allinone_next_button.setEnabled(False)
    allinone_search_row.addWidget(host.allinone_next_button)

    allinone_search_row.addWidget(QLabel("View:"))
    host.allinone_view_combo = QComboBox()
    host.allinone_view_combo.addItem("Table",   "table")
    host.allinone_view_combo.addItem("Gallery", "gallery")
    host.allinone_view_combo.setToolTip(
        "Switch between the classic table view and the picture (gallery) view.\n"
        "Persisted across sessions in the config file."
    )
    allinone_search_row.addWidget(host.allinone_view_combo)

    host.allinone_pygame_button = QPushButton("🎮 Retro")
    host.allinone_pygame_button.setCheckable(True)
    host.allinone_pygame_button.setToolTip(
        "Switch the Unite! Table & Gallery views to a pygame-rendered\n"
        "visualization. Click again to return to the classic views.\n"
        "Requires the optional 'pygame-ce' package."
    )
    allinone_search_row.addWidget(host.allinone_pygame_button)

    host.allinone_status_label = QLabel("")
    allinone_search_row.addWidget(host.allinone_status_label)

    allinone_v.addWidget(_wrap_flow_row(allinone_search_row))

    # --- Preview panel (right column, shown only in Table view) ---
    host.allinone_screenshot_label = QLabel()
    host.allinone_screenshot_label.setFixedSize(256, 192)
    host.allinone_screenshot_label.setAlignment(Qt.AlignCenter)
    host.allinone_screenshot_label.setStyleSheet("background: #111; border: 1px solid #444;")
    host.allinone_screenshot_label.setText("No preview")
    host.allinone_screenshot_label.setToolTip("Double-click to open full view")

    allinone_right_col = QVBoxLayout()
    allinone_right_col.addWidget(host.allinone_screenshot_label)
    allinone_right_col.addStretch()
    allinone_right_widget = QWidget()
    allinone_right_widget.setLayout(allinone_right_col)
    host._allinone_right_widget = allinone_right_widget
    host._allinone_preview_label = host.allinone_screenshot_label
    # Initially hidden; _allinone_apply_view_mode will show it in Table mode.
    host.allinone_screenshot_label.setVisible(False)

    # --- Aggregated gallery view ---
    _ALLINONE_SOURCE_LABELS = {"getit": "GetIt", "zxdb": "ZXDB", "zxart": "ZXArt",
                               "itchio": "itch.io"}

    def _allinone_source_label(e):
        try:
            src = (e.get("_fav_source") or e.get("source") or "").lower()
        except Exception:
            src = ""
        return _ALLINONE_SOURCE_LABELS.get(src, "")

    host.allinone_gallery_view = GalleryView(
        rows_per_page_getter=lambda: host._gallery_rows_per_page,
        anim_mode_getter=lambda: host._gallery_anim_mode,
        cols_getter=lambda: host._gallery_cols,
        img_size_getter=lambda: host._gallery_img_size,
        thumb_fetch_cb=_fav_thumb_fetch,
        extra_fetch_cb=_fav_extra_fetch,
        title_getter=_fav_title_getter,
        info_getter=_fav_info_getter,
        is_favorite_cb=lambda e: host._fav_is(e),
        toggle_favorite_cb=lambda e: host._fav_toggle(e),
        source_label_getter=_allinone_source_label,
        source_overlay_anchor="bottomright",
    )
    # Animate .gif thumbnails (QMovie) just like the in-pane item viewer.
    host.allinone_gallery_view.set_gif_fetch_cb(_gif_fetch_bytes)

    def _allinone_on_cell_dbl_clicked(entry):
        try:
            _fav_open_fullscreen(entry)
        except Exception:
            pass
    host.allinone_gallery_view.cell_dbl_clicked.connect(_allinone_on_cell_dbl_clicked)

    # --- Aggregated table view (mirrors Favorites) ---
    host.allinone_results_table = QTableWidget(0, 5)
    host.allinone_results_table.setHorizontalHeaderLabels(
        ["Source", "Title", "Rating", "Info", "Year"]
    )
    host.allinone_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    host.allinone_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    host.allinone_results_table.setSelectionMode(QAbstractItemView.SingleSelection)
    host.allinone_results_table.verticalHeader().setVisible(False)
    try:
        hh = host.allinone_results_table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    except Exception:
        pass

    def _allinone_table_entry_for_row(row):
        try:
            if row < 0 or row >= host.allinone_results_table.rowCount():
                return None
            it = host.allinone_results_table.item(row, 0)
            if it is None:
                return None
            entry = it.data(Qt.UserRole)
            if isinstance(entry, dict):
                return entry
        except Exception:
            pass
        return None

    def _allinone_table_on_double_clicked(_idx):
        row = host.allinone_results_table.currentRow()
        entry = _allinone_table_entry_for_row(row)
        if entry is not None:
            _fav_open_fullscreen(entry)

    host.allinone_results_table.doubleClicked.connect(_allinone_table_on_double_clicked)

    # --- Row selection → load preview image ---
    def _allinone_on_row_selected():
        rows = host.allinone_results_table.selectedItems()
        if not rows:
            return
        row = host.allinone_results_table.currentRow()
        entry = _allinone_table_entry_for_row(row)
        if entry is None:
            return
        host.allinone_screenshot_label.setText("Loading…")
        host.allinone_screenshot_label.setPixmap(QPixmap())
        # Delegate thumbnail fetch to the shared _fav_thumb_fetch which
        # routes to the correct source-specific fetcher.
        def _set_pixmap(px, _url=None):
            try:
                if px is None or px.isNull():
                    host.allinone_screenshot_label.setText("No preview")
                else:
                    host.allinone_screenshot_label.setPixmap(
                        px.scaled(256, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
            except RuntimeError:
                pass
        def _set_screenshots(_urls):
            pass  # Not needed for the simple preview label
        try:
            _fav_thumb_fetch(entry, _set_pixmap, _set_screenshots)
        except Exception:
            host.allinone_screenshot_label.setText("No preview")

    host.allinone_results_table.itemSelectionChanged.connect(_allinone_on_row_selected)

    # Double-click on preview opens full view
    from PySide6.QtCore import QEvent as _QEvent

    def _allinone_preview_dbl_click(event):
        if event.type() == _QEvent.MouseButtonDblClick:
            row = host.allinone_results_table.currentRow()
            entry = _allinone_table_entry_for_row(row)
            if entry is not None:
                _fav_open_fullscreen(entry)
        # Let the label handle the event normally
        return QLabel.event(host.allinone_screenshot_label, event)

    host.allinone_screenshot_label.event = _allinone_preview_dbl_click
    host.allinone_screenshot_label.setCursor(Qt.PointingHandCursor)

    # --- Stack the two views (table = idx 0, gallery = idx 1) ---
    host.allinone_view_stack = QStackedWidget()
    host.allinone_view_stack.addWidget(host.allinone_results_table)   # idx 0 = table
    host.allinone_view_stack.addWidget(host.allinone_gallery_view)    # idx 1 = gallery

    # Paging state for the AllInOne aggregated view (client-side paging).
    host._allinone_all_entries = []
    host._allinone_current_page = 1
    host._allinone_total_pages = 1

    # Wrap view_stack + right preview widget in a horizontal row
    allinone_table_row = QHBoxLayout()
    allinone_table_row.addWidget(host.allinone_view_stack, 1)
    # Animated retro "SEARCHING..." banner over the
    # results area whenever a multi-source fan-out is in flight — including
    # re-searches over already-populated content, so it stays visible on top
    # of the pygame GalleryScene (not only on the first/empty load).
    # Works in both Classic (table/gallery) and Pygame modes: the overlay
    # floats above whichever page the view-stack is showing.
    host._allinone_loading_overlay = RetroLoadingOverlay(
        host.allinone_view_stack,
        lambda: getattr(host, "_allinone_bulk_active", False))
    allinone_table_row.addWidget(allinone_right_widget)
    allinone_table_container = QWidget()
    allinone_table_container.setLayout(allinone_table_row)
    allinone_v.addWidget(allinone_table_container)
    zxnextunite_AllInOne_tab.setLayout(allinone_v)
    zxnextunite_AllInOne_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_ALLINONE

    # Insert AllInOne *before* GetIt in the tab bar.
    _getit_tab_index = wid_inner.tab.indexOf(zxnextunite_GetIt_tab)
    if _getit_tab_index < 0:
        _getit_tab_index = wid_inner.tab.count()
    wid_inner.tab.insertTab(
        _getit_tab_index, zxnextunite_AllInOne_tab,
        f"{ZX_NEXT_UNITE_TAB_TITLE_ALLINONE} (0)"
    )

    # Start the AllInOne tab text color cycling animation. Give every
    # other tab an explicit readable text color first, since the
    # stylesheet no longer sets one (so setTabTextColor can take effect
    # on the AllInOne tab without being overridden). Seed with the current
    # general UI text colour so the tabs honour the theme / user pick;
    # _refresh_tab_stylesheet() re-applies it whenever it changes and once
    # more after the config file loads.
    try:
        _tab_bar = host._tab_widget.tabBar()
        _default_tab_color = getattr(
            host, "img_color_general_text", None
        ) or hex_to_qcolor(DEFAULT_COLOR_GENERAL_TEXT)
        for _i in range(host._tab_widget.count()):
            if "Unite!" not in host._tab_widget.tabText(_i):
                _tab_bar.setTabTextColor(_i, _default_tab_color)
    except Exception:
        pass
    host._allinone_color_timer.start()

    # Expose the source-label helpers: build_unite_ops (called later in
    # __init__, after the itch.io tab block) receives them as params read off
    # these host attributes.
    host._ALLINONE_SOURCE_LABELS = _ALLINONE_SOURCE_LABELS
    host._allinone_source_label = _allinone_source_label


def build_unite_ops(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    _ALLINONE_SOURCE_LABELS,
    _allinone_source_label,
    _fav_title_getter,
    _fav_info_getter,
    _fav_thumb_fetch,
    _search_autocomplete_on,
    getit_on_latest,
    getit_on_random,
    zxdb_on_latest,
    zxdb_on_random,
    zxart_on_latest,
    zxart_on_random,
    _getit_open_gallery_viewer,
    _zxdb_open_gallery_viewer,
    _zxart_open_gallery_viewer,
    _gif_fetch_bytes,
    _popup_height_for,
    getit_run_in_thread,
    _CompleterPopupHider,
    _start_tab_spinner,
    _stop_tab_spinner,
    _clear_tab_badge,
    _cross_search_getit,
    _cross_search_zxdb,
    _cross_search_zxart,
):
    """Wire the Unite! operation layer onto the widgets built by build_unite_pane."""
    # --- Aggregation + tab badge ---
    def _allinone_has_image(e):
        """Whether an aggregated entry is known to carry a real picture.
        zxArt / itch.io expose this in their list data; GetIt and ZXDB always
        attempt a real image, so they are treated optimistically (True) and
        the Qt gallery's runtime re-sort sinks any that turn out imageless."""
        src = (e.get("_fav_source") or e.get("source") or "").lower()
        pred = ((host._fav_fetchers or {}).get(src) or {}).get("has_image")
        if pred is None:
            return True
        try:
            return bool(pred(e))
        except Exception:
            return True

    def _allinone_order_image_first(entries):
        """Stable-partition so picture-bearing entries lead and known-
        imageless ones sink to the bottom. Applied to every sort mode so
        "Mixed" can no longer surface imageless items at the top — the same
        image-first guarantee GetIt-first and Classic already get from their
        base ordering. Order within each group is preserved, so each mode's
        arrangement of the picture-bearing items is untouched."""
        keep, sink = [], []
        for e in entries:
            (keep if _allinone_has_image(e) else sink).append(e)
        return keep + sink

    def _allinone_collect():
        # Gather each source's tagged entries into its own bucket so the
        # configured sort mode can arrange them. The gallery's image-first
        # re-sort still lifts picture-bearing items to the top within
        # whatever base order we return here — that rule is common to every
        # mode; only the per-source ordering below changes.
        src_attr = {"getit":  "_getit_last_entries",
                    "zxdb":   "_zxdb_last_entries",
                    "zxart":  "_zxart_last_entries",
                    "itchio": "_itchio_last_entries"}
        order = ["getit", "zxdb", "zxart"]
        # itch.io joins the aggregation only when it took part in the last
        # Unite! search; Latest/Random (and prior itch.io tab browsing) keep
        # it out so it doesn't push catalogue results onto later pages.
        if getattr(host, "_allinone_include_itchio", False):
            order.append("itchio")
        buckets = {}
        for src in order:
            lst = getattr(host, src_attr[src], None) or []
            buckets[src] = [{**e, "_fav_source": src}
                            for e in lst if isinstance(e, dict)]

        mode = getattr(host, "_search_sort_mode", DEFAULT_SEARCH_SORT_MODE)

        if mode == SEARCH_SORT_MIXED:
            # Round-robin interleave so GetIt is scattered among the other
            # sources rather than leading the list. GetIt is placed last in
            # the cycle so it never takes the very first slot.
            cycle = [s for s in ("zxdb", "zxart", "itchio", "getit")
                     if buckets.get(s)]
            merged = []
            i = 0
            while True:
                took_any = False
                for s in cycle:
                    b = buckets[s]
                    if i < len(b):
                        merged.append(b[i])
                        took_any = True
                if not took_any:
                    break
                i += 1
        elif mode == SEARCH_SORT_CLASSIC:
            # ZXDB / zxArt (and itch.io) first, GetIt content trailing last.
            seq = ("zxdb", "zxart", "itchio", "getit")
            merged = []
            for s in seq:
                merged.extend(buckets.get(s, []))
        else:
            # Default "GetIt first class": GetIt leads, then the rest.
            seq = ("getit", "zxdb", "zxart", "itchio")
            merged = []
            for s in seq:
                merged.extend(buckets.get(s, []))

        # Common to every mode: float picture-bearing entries to the top and
        # sink known-imageless ones, so no mode (notably "Mixed") starts the
        # gallery with imageless content.
        return _allinone_order_image_first(merged)

    def _allinone_update_tab_badge(n):
        try:
            # While the AllInOne search spinner is running, leave the tab
            # text to the spinner (rotating earth). The final count is
            # applied once the spinner stops.
            if getattr(host, "_spinner_tabs", None) and \
                    ZX_NEXT_UNITE_TAB_TITLE_ALLINONE in host._spinner_tabs:
                return
            for i in range(host._tab_widget.count()):
                if host._tab_widget.tabText(i).startswith(
                        ZX_NEXT_UNITE_TAB_TITLE_ALLINONE):
                    host._tab_widget.setTabText(
                        i, f"{ZX_NEXT_UNITE_TAB_TITLE_ALLINONE} ({n})")
                    break
        except Exception:
            pass

    def _allinone_fill_table(page_entries):
        try:
            import re as _re
            tbl = host.allinone_results_table
            tbl.setRowCount(0)
            for entry in page_entries:
                if not isinstance(entry, dict):
                    continue
                src_lbl = _ALLINONE_SOURCE_LABELS.get(
                    (entry.get("_fav_source") or entry.get("source") or "").lower(), ""
                )
                raw_title = _fav_title_getter(entry) or ""
                _span_match = _re.search(r'<span[^>]*>([^<]+)</span>', raw_title)
                rating = _span_match.group(1).strip() if _span_match else ""
                title = _re.sub(r'<[^>]+>', '', raw_title).strip()
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
                src_item.setData(Qt.UserRole, entry)
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

    # Latest / Random / multi-search fan out to several sources, each of
    # which calls _allinone_repopulate as its results land. Rebuilding the
    # aggregated gallery on every source completion re-spawned the whole
    # page's thumbnail threads several times over (and re-ran the image-first
    # re-sort), which hung the UI thread. While a bulk op is in progress we
    # coalesce those requests into a single rebuild at the end.
    host._allinone_bulk_active = False
    host._allinone_repopulate_pending = False

    def _allinone_repopulate_now():
        try:
            entries = _allinone_collect()
            # Cache the full merged list so prev/next can re-slice without
            # re-aggregating, and keep page state coherent across reloads.
            host._allinone_all_entries = entries
            total = len(entries)
            total_pages = max(1, (total + ALLINONE_PAGE_SIZE - 1) // ALLINONE_PAGE_SIZE)
            # Clamp current page if the merged set shrank (e.g. fewer
            # results after a new search).
            cur = getattr(host, "_allinone_current_page", 1) or 1
            if cur > total_pages:
                cur = total_pages
            if cur < 1:
                cur = 1
            host._allinone_current_page = cur
            host._allinone_total_pages = total_pages
            start = (cur - 1) * ALLINONE_PAGE_SIZE
            end = start + ALLINONE_PAGE_SIZE
            page_entries = entries[start:end]
            host.allinone_gallery_view.populate(page_entries)
            _allinone_fill_table(page_entries)
            if getattr(host, "_allinone_pygame_widget", None) is not None:
                try:
                    host._allinone_pygame_feed()
                except Exception:
                    pass
            _allinone_update_tab_badge(total)
            try:
                host.allinone_page_label.setText(str(cur))
                host.allinone_prev_button.setEnabled(cur > 1)
                host.allinone_next_button.setEnabled(cur < total_pages)
                host.allinone_status_label.setText(
                    f"{total} result(s)  |  page {cur}/{total_pages}"
                )
            except Exception:
                pass
        except Exception:
            pass

    def _allinone_repopulate():
        # Defer while a bulk fan-out is active; _allinone_end_bulk() flushes
        # one rebuild once every source has reported back.
        if getattr(host, "_allinone_bulk_active", False):
            host._allinone_repopulate_pending = True
            return
        _allinone_repopulate_now()

    def _allinone_begin_bulk():
        # Reset state at the start of each bulk op so a prior fan-out whose
        # completion was dropped can't leave the gallery stuck deferring.
        host._allinone_bulk_active = True
        host._allinone_repopulate_pending = False

    def _allinone_end_bulk(flush=False):
        was_active = getattr(host, "_allinone_bulk_active", False)
        host._allinone_bulk_active = False
        if (was_active and host._allinone_repopulate_pending) or flush:
            host._allinone_repopulate_pending = False
            _allinone_repopulate_now()

    host._allinone_repopulate = _allinone_repopulate
    host._allinone_begin_bulk = _allinone_begin_bulk
    host._allinone_end_bulk = _allinone_end_bulk

    # --- Paging handlers (client-side over the merged result list) ---
    def allinone_on_prev():
        cur = getattr(host, "_allinone_current_page", 1) or 1
        if cur <= 1:
            return
        host._allinone_current_page = cur - 1
        _allinone_repopulate()

    def allinone_on_next():
        cur = getattr(host, "_allinone_current_page", 1) or 1
        total_pages = getattr(host, "_allinone_total_pages", 1) or 1
        if cur >= total_pages:
            return
        host._allinone_current_page = cur + 1
        _allinone_repopulate()

    host.allinone_prev_button.clicked.connect(allinone_on_prev)
    host.allinone_next_button.clicked.connect(allinone_on_next)

    # --- Search handler: always fan out to GetIt + ZXDB + zxArt ---
    def allinone_on_search():
        q = host.allinone_search_input.text().strip()
        if q and len(q) < SEARCH_MIN_CHARS:
            return
        # Suppress the autocomplete suggestions popup once a search is
        # submitted; it stays hidden until the user types again.
        host._allinone_ac_block = True
        try:
            _allinone_ac_timer.stop()
        except Exception:
            pass
        try:
            host._allinone_completer.popup().hide()
        except Exception:
            pass
        # Reset paging on a new search so results start at page 1.
        host._allinone_current_page = 1
        # Mirror the query into each source pane's input box so the
        # user can see/edit it there too.
        try:
            host.getit_search_input.setText(q)
        except Exception:
            pass
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            try:
                host.zxdb_search_input.setText(q)
            except Exception:
                pass
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            try:
                host.zxart_search_input.setText(q)
            except Exception:
                pass
        if getattr(host, "_itchio_connected", False):
            try:
                host.itchio_search_input.setText(q)
            except Exception:
                pass
        # Clear stale badges before searching.
        try:
            _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
            if getattr(host, "_itchio_connected", False):
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
        except Exception:
            pass
        # Show the rotating-earth animation on the AllInOne tab while any
        # of the source searches are still running. We count how many
        # sources we kicked off and stop the spinner once they have all
        # reported back, then refresh the aggregated badge count.
        sources = [_cross_search_getit]
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            sources.append(_cross_search_zxdb)
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            sources.append(_cross_search_zxart)
        # itch.io joins the fan-out only when the optional tab is built and
        # the user is currently connected.
        _itch_search = getattr(host, "_itchio_cross_search", None)
        _itch_on = bool(_itch_search) and getattr(host, "_itchio_connected", False)
        if _itch_on:
            sources.append(_itch_search)
        # itch.io results belong in this aggregation only when it actually
        # joined the fan-out for this query.
        host._allinone_include_itchio = _itch_on

        pending = {"count": len(sources)}

        # Coalesce the per-source AllInOne rebuilds into one at the end.
        _allinone_begin_bulk()

        def _allinone_source_done():
            pending["count"] -= 1
            if pending["count"] <= 0:
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)
                try:
                    _allinone_end_bulk(flush=True)
                except Exception:
                    pass

        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)

        # Run the same searches the per-source panes use. Each will
        # call its populate_results, which in turn refreshes the
        # AllInOne gallery via _allinone_repopulate.
        try:
            _cross_search_getit(q, _allinone_source_done)
        except Exception:
            _allinone_source_done()
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            try:
                _cross_search_zxdb(q, _allinone_source_done)
            except Exception:
                _allinone_source_done()
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            try:
                _cross_search_zxart(q, _allinone_source_done)
            except Exception:
                _allinone_source_done()
        if _itch_on:
            try:
                _itch_search(q, _allinone_source_done)
            except Exception:
                _allinone_source_done()

    def _allinone_search_validate(text: str):
        t = text.strip()
        if not t:
            host._allinone_search_valid_lbl.setVisible(False)
        elif len(t) < SEARCH_MIN_CHARS:
            host._allinone_search_valid_lbl.setText(
                f"Min {SEARCH_MIN_CHARS} chars")
            host._allinone_search_valid_lbl.setStyleSheet("color: #c33;")
            host._allinone_search_valid_lbl.setVisible(True)
        else:
            host._allinone_search_valid_lbl.setVisible(False)

    host.allinone_search_input.textChanged.connect(_allinone_search_validate)
    host.allinone_search_button.clicked.connect(allinone_on_search)
    host.allinone_search_input.returnPressed.connect(allinone_on_search)

    # --- Shared fan-out driver for Random/Latest on the Unite! tab.
    # Kicks off every supplied source action (each taking an on_complete
    # callback), drives the rotating-earth spinner on the AllInOne tab,
    # and refreshes the aggregated gallery once every source has reported
    # back. Each per-source completion is counted at most once, and a
    # watchdog timer guarantees the spinner is always cleared even if a
    # source's callback is dropped by a supersede race — preventing the
    # "forever-spinning earth" symptom.
    def _allinone_fanout(actions):
        host._allinone_current_page = 1
        try:
            _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
            if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
            if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
        except Exception:
            pass

        state = {"pending": len(actions), "done": False}

        # Coalesce the per-source AllInOne rebuilds into a single rebuild
        # when the fan-out completes (the watchdog guarantees _finish runs).
        _allinone_begin_bulk()

        watchdog = QTimer(host)
        watchdog.setSingleShot(True)
        watchdog.setInterval(30000)

        def _finish():
            if state["done"]:
                return
            state["done"] = True
            try:
                watchdog.stop()
            except Exception:
                pass
            _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)
            try:
                _allinone_end_bulk(flush=True)
            except Exception:
                pass

        def _make_done():
            fired = {"v": False}

            def _done():
                # Guard against a source invoking its callback twice.
                if fired["v"]:
                    return
                fired["v"] = True
                state["pending"] -= 1
                if state["pending"] <= 0:
                    _finish()
            return _done

        watchdog.timeout.connect(_finish)

        _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ALLINONE)
        watchdog.start()

        if not actions:
            _finish()
            return

        for action in actions:
            done_cb = _make_done()
            try:
                action(done_cb)
            except Exception:
                done_cb()

    # --- Random handler: fan out to GetIt + ZXDB + zxArt Random buttons.
    # Each per-source random handler clears its own search box, drives its
    # tab spinner/badge, and ultimately refreshes the AllInOne gallery via
    # _allinone_repopulate once the shared fan-out driver reports done.
    def allinone_on_random():
        # Clear the AllInOne search box too, so the pane reflects the
        # "random" mode rather than a stale query.
        try:
            host.allinone_search_input.clear()
        except Exception:
            pass
        # Random browses GetIt/ZXDB/zxArt only — keep itch.io out.
        host._allinone_include_itchio = False

        actions = [lambda cb: getit_on_random(cb)]
        # ZXDB random — only meaningful in 'games' mode; the button there
        # is auto-disabled outside of it, so guard accordingly.
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            try:
                zxdb_games_mode = host.zxdb_random_button.isEnabled()
            except Exception:
                zxdb_games_mode = False
            if zxdb_games_mode:
                actions.append(lambda cb: zxdb_on_random(cb))
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            actions.append(lambda cb: zxart_on_random(cb))

        _allinone_fanout(actions)

    host.allinone_random_button.clicked.connect(allinone_on_random)

    # --- Latest handler: fan out to GetIt + ZXDB + zxArt "Latest" actions.
    # Each per-source latest handler clears its own search box, drives its
    # tab spinner/badge, and fetches the most recent releases. The shared
    # fan-out driver runs the rotating-earth spinner on the AllInOne tab
    # until every source has reported back, then refreshes the gallery.
    def allinone_on_latest():
        # Clear the AllInOne search box so the pane reflects "latest"
        # mode rather than a stale query.
        try:
            host.allinone_search_input.clear()
        except Exception:
            pass
        # Latest fetches GetIt/ZXDB/zxArt only — keep itch.io out so the
        # aggregated pages show catalogue content, not prior itch.io browsing.
        host._allinone_include_itchio = False
        # Suppress the autocomplete suggestions popup once latest is
        # requested; it stays hidden until the user types again.
        host._allinone_ac_block = True
        try:
            _allinone_ac_timer.stop()
        except Exception:
            pass
        try:
            host._allinone_completer.popup().hide()
        except Exception:
            pass

        actions = [lambda cb: getit_on_latest(cb)]
        # ZXDB latest — zxdb_on_latest forces 'games' mode itself.
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            actions.append(lambda cb: zxdb_on_latest(cb))
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            actions.append(lambda cb: zxart_on_latest(cb))

        _allinone_fanout(actions)

    host.allinone_latest_button.clicked.connect(allinone_on_latest)
    # Expose so deferred startup activation can trigger the same "Latest"
    # multi-search logic that the button press performs.
    host._allinone_on_latest = allinone_on_latest

    # --- Autocomplete (merge title suggestions from GetIt + ZXDB + zxArt
    #     caches, triggering source-pane fetches on demand). ---
    host._allinone_ac_model = QStringListModel(host)
    _allinone_completer = QCompleter(host._allinone_ac_model, host)
    _allinone_completer.setCompletionMode(QCompleter.PopupCompletion)
    _allinone_completer.setCaseSensitivity(Qt.CaseInsensitive)
    # Substring (contains) matching so typing e.g. "cspect" also surfaces
    # titles like "#CSpect" or "The CSpect Emulator", not just those that
    # begin with the typed text. The merge in _allinone_ac_update_model uses
    # the same "tl in key" rule so the model and the popup filter agree.
    _allinone_completer.setFilterMode(Qt.MatchContains)
    # Ensure the popup follows the main window on Windows
    popup = _allinone_completer.popup()
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
    host._allinone_completer = _allinone_completer
    host.allinone_search_input.setCompleter(_allinone_completer)
    host._allinone_popup_hider = _CompleterPopupHider(
        host.allinone_search_input, _allinone_completer, host)

    def _allinone_safe_show_popup(q: str):
        try:
            if not host._search_autocomplete_on():
                return
            if getattr(host, "_allinone_ac_block", False):
                return
            if not host.allinone_search_input.hasFocus():
                return
            if host.allinone_search_input.text().strip() != q:
                return
            if host._allinone_ac_model.rowCount() == 0:
                return
            _allinone_completer.setCompletionPrefix(q)
            popup = _allinone_completer.popup()
            if popup is None:
                return
            try:
                popup.setParent(host.allinone_search_input.window(),
                                Qt.Tool
                                | Qt.FramelessWindowHint
                                | Qt.WindowStaysOnTopHint
                                | Qt.WindowDoesNotAcceptFocus)
                popup.setFocusPolicy(Qt.NoFocus)
                popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
            except Exception:
                pass
            le = host.allinone_search_input
            rect = le.rect()
            pos = le.mapToGlobal(rect.bottomLeft())
            popup.setMinimumWidth(le.width())
            popup.move(pos)
            popup.resize(le.width(), _popup_height_for(popup, host._allinone_ac_model.rowCount()))
            popup.show()
        except RuntimeError:
            pass
        except Exception:
            pass

    def _allinone_ac_update_model(text: str):
        if not text:
            host._allinone_ac_model.setStringList([])
            return
        host._allinone_ac_filter_gen = (
            getattr(host, "_allinone_ac_filter_gen", 0) + 1
        )
        gen = host._allinone_ac_filter_gen
        tl = text.lower()

        # Snapshot all three caches up-front (cheap shallow copies) so
        # the worker thread doesn't touch shared state while the user
        # keeps typing.
        getit_snapshot = list(getattr(host, "_getit_ac_titles", None) or [])
        zxdb_cache = getattr(host, "_zxdb_ac_cache", None) or {}
        letter = tl[0]
        zxdb_snapshot = list(zxdb_cache.get(letter, []))
        zxart_cache = getattr(host, "_zxart_ac_cache", None) or {}
        zxart_best_pfx = None
        for cached_prefix in zxart_cache.keys():
            if tl.startswith(cached_prefix.lower()):
                if (zxart_best_pfx is None
                        or len(cached_prefix) > len(zxart_best_pfx)):
                    zxart_best_pfx = cached_prefix
        zxart_snapshot = (
            list(zxart_cache.get(zxart_best_pfx, []))
            if zxart_best_pfx is not None else []
        )
        # itch.io: the user's purchased + collection titles, taken from the
        # cached combined library. Only contributes while connected.
        if getattr(host, "_itchio_connected", False):
            itchio_snapshot = [
                (g.get("title") or "")
                for g in (getattr(host, "_itchio_library", None) or [])
                if isinstance(g, dict) and g.get("title")
            ]
        else:
            itchio_snapshot = []

        def _fn():
            merged: dict = {}  # lower-case title -> first-seen original
            for t in getit_snapshot:
                if not t:
                    continue
                key = t.lower()
                if tl in key and key not in merged:
                    merged[key] = t
            for t in zxdb_snapshot:
                if not t:
                    continue
                key = t.lower()
                if tl in key and key not in merged:
                    merged[key] = t
            for t in zxart_snapshot:
                if not t:
                    continue
                key = t.lower()
                if tl in key and key not in merged:
                    merged[key] = t
            for t in itchio_snapshot:
                if not t:
                    continue
                key = t.lower()
                if tl in key and key not in merged:
                    merged[key] = t
            matches = sorted(merged.values(), key=str.lower)
            return (gen, text, matches[:80])

        def _on_ok(result):
            rgen, rtext, matches = result
            if rgen != getattr(host, "_allinone_ac_filter_gen", -1):
                return
            try:
                if host.allinone_search_input.text().strip() != rtext:
                    return
            except RuntimeError:
                return
            host._allinone_ac_model.setStringList(matches)
            if matches:
                QTimer.singleShot(
                    0, lambda q=rtext: _allinone_safe_show_popup(q)
                )

        def _on_err(_err):
            pass

        getit_run_in_thread(_fn, _on_ok, _on_err)

    def _allinone_ac_notify(_source: str, _key: str):
        """Called by GetIt / ZXDB / zxArt autocomplete fetchers once their
        caches receive new data.  Refresh the AllInOne model so newly
        arrived titles appear in the suggestion list."""
        try:
            text = host.allinone_search_input.text().strip()
            if not text:
                return
            if not host.allinone_search_input.hasFocus():
                # Still refresh the model so it's ready when focus returns.
                _allinone_ac_update_model(text)
                return
            _allinone_ac_update_model(text)
        except RuntimeError:
            pass
        except Exception:
            pass
        # Stop the placeholder animation once any source has responded
        # and at least one cache is populated.
        try:
            if getattr(host, "_allinone_ac_waiting", False):
                any_data = (
                    bool(getattr(host, "_getit_ac_titles", None))
                    or bool(getattr(host, "_zxdb_ac_cache", None))
                    or bool(getattr(host, "_zxart_ac_cache", None))
                    or bool(getattr(host, "_itchio_library", None))
                )
                if any_data:
                    host._ac_anim_stop(host.allinone_search_input)
                    host._allinone_ac_waiting = False
        except Exception:
            pass

    host._allinone_ac_notify = _allinone_ac_notify

    # Debounce typing so we don't fire cache priming + filter on every
    # keystroke.
    _allinone_ac_timer = QTimer(host)
    _allinone_ac_timer.setSingleShot(True)
    _allinone_ac_timer.setInterval(200)
    host._allinone_ac_timer = _allinone_ac_timer

    def _allinone_ac_do_work(text: str):
        text = text.strip()
        if not text:
            host._allinone_ac_model.setStringList([])
            if getattr(host, "_allinone_ac_waiting", False):
                try:
                    host._ac_anim_stop(host.allinone_search_input)
                except Exception:
                    pass
                host._allinone_ac_waiting = False
            return
        tl = text.lower()
        need_fetch = False

        # GetIt: prime full title cache once.
        getit_titles = getattr(host, "_getit_ac_titles", None) or []
        if not getit_titles and not getattr(host, "_getit_ac_loading", False):
            starter = getattr(host, "_getit_ac_start_fetch", None)
            if callable(starter):
                try:
                    starter()
                    need_fetch = True
                except Exception:
                    pass
        elif getattr(host, "_getit_ac_loading", False):
            need_fetch = True

        # ZXDB: prime the relevant per-letter cache.
        if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
            letter = tl[0]
            zxdb_cache = getattr(host, "_zxdb_ac_cache", None) or {}
            zxdb_fetching = getattr(host, "_zxdb_ac_fetching", None) or set()
            if letter not in zxdb_cache and letter not in zxdb_fetching:
                fetcher = getattr(host, "_zxdb_ac_fetch_letter", None)
                if callable(fetcher):
                    try:
                        fetcher(letter)
                        need_fetch = True
                    except Exception:
                        pass
            elif letter in zxdb_fetching:
                need_fetch = True

        # zxArt: prime a prefix fetch if none of the cached prefixes
        # covers the current text.
        if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
            zxart_cache = getattr(host, "_zxart_ac_cache", None) or {}
            covered = any(
                tl.startswith(p.lower()) for p in zxart_cache.keys()
            )
            zxart_inflight = (
                getattr(host, "_zxart_ac_external_fetching", None) or set()
            )
            already_fetching = any(
                tl.startswith(p.lower()) for p in zxart_inflight
            )
            if not covered and not already_fetching:
                fetcher = getattr(host, "_zxart_ac_fetch_prefix", None)
                if callable(fetcher):
                    try:
                        fetcher(text)
                        need_fetch = True
                    except Exception:
                        pass
            elif already_fetching:
                need_fetch = True

        # itch.io: build the combined library once (purchases + collections)
        # so the user's owned titles can be suggested. Only when connected.
        if getattr(host, "_itchio_connected", False):
            if getattr(host, "_itchio_library", None) is None:
                if not getattr(host, "_itchio_library_building", False):
                    starter = getattr(host, "_itchio_prebuild_library", None)
                    if callable(starter):
                        try:
                            starter()
                            need_fetch = True
                        except Exception:
                            pass
                else:
                    need_fetch = True

        if need_fetch and not getattr(host, "_allinone_ac_waiting", False):
            try:
                host._ac_anim_start(host.allinone_search_input)
                host._allinone_ac_waiting = True
            except Exception:
                pass
        _allinone_ac_update_model(text)

    def _allinone_ac_trigger():
        if not _search_autocomplete_on():
            try:
                host._allinone_ac_model.setStringList([])
            except Exception:
                pass
            return
        try:
            text = host.allinone_search_input.text()
        except RuntimeError:
            return
        _allinone_ac_do_work(text)

    _allinone_ac_timer.timeout.connect(_allinone_ac_trigger)

    def _allinone_ac_on_text_changed(_text: str):
        # Clear the model immediately when the box is emptied so a stale
        # popup doesn't linger while the debounce timer is still pending.
        if not _text.strip():
            host._allinone_ac_model.setStringList([])
        if getattr(host, "_allinone_ac_suppress", False):
            host._allinone_ac_suppress = False
            return
        # The user is typing again: re-enable autocomplete suggestions
        # that were suppressed after the last search submission.
        host._allinone_ac_block = False
        _allinone_ac_timer.start()

    host.allinone_search_input.textChanged.connect(_allinone_ac_on_text_changed)

    def _allinone_ac_activated(selected: str):
        try:
            if selected:
                host._allinone_ac_suppress = True
                _allinone_ac_timer.stop()
                try:
                    _allinone_completer.popup().hide()
                except Exception:
                    pass
                host.allinone_search_input.setText(selected)
        except Exception:
            pass
        allinone_on_search()

    _allinone_completer.activated.connect(_allinone_ac_activated)

    # ── AllInOne (Unite!) view-mode apply helper (mirrors GetIt/ZXDB/zxArt) ──
    def _allinone_apply_view_mode(mode: str, *, persist: bool = True):
        mode = (mode or "gallery").lower()
        if mode not in ("table", "gallery"):
            mode = "gallery"
        host._allinone_view_mode = mode
        host.allinone_view_stack.setCurrentIndex(1 if mode == "gallery" else 0)
        # Show/hide the preview panel based on view mode (Table = visible)
        _table = (mode == "table")
        if hasattr(host, '_allinone_right_widget'):
            host._allinone_right_widget.setVisible(_table)
        if hasattr(host, '_allinone_preview_label'):
            host._allinone_preview_label.setVisible(_table)
        # In pygame mode the same Table/Gallery selection drives the pygame
        # scene instead of the classic Qt stack pages.
        if getattr(host, "_allinone_pygame_on", False) and \
                getattr(host, "_allinone_pygame_widget", None) is not None:
            host._allinone_pygame_set_scene()
            host.allinone_view_stack.setCurrentWidget(host._allinone_pygame_widget)
            if hasattr(host, '_allinone_right_widget'):
                host._allinone_right_widget.setVisible(False)
        cb = host.allinone_view_combo
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
            if hasattr(host, '_favorites_apply_view_mode'):
                host._favorites_apply_view_mode(mode, persist=False)
            configuration_dictionary[SETTING_GETIT_VIEW_MODE]     = mode
            configuration_dictionary[SETTING_ZXDB_VIEW_MODE]      = mode
            configuration_dictionary[SETTING_ZXART_VIEW_MODE]     = mode
            configuration_dictionary[SETTING_FAVORITES_VIEW_MODE] = mode
            configuration_dictionary[SETTING_ALLINONE_VIEW_MODE]  = mode
            if hasattr(host, '_itchio_apply_view_mode'):
                host._itchio_apply_view_mode(mode, persist=False)
            configuration_dictionary[SETTING_ITCHIO_VIEW_MODE]    = mode
            save_configuration_file()

    host._allinone_apply_view_mode = _allinone_apply_view_mode

    # ── Pygame visualization mode (optional, lazily built) ──────────────
    host._allinone_pygame_on = False
    host._allinone_pygame_widget = None
    host._allinone_pygame_table = None
    host._allinone_pygame_gallery = None
    # Space-Invaders background animation preference (on by default,
    # overridden from the config file by load_configuration_file()).
    host._allinone_pygame_anim = True

    def _allinone_pygame_open_viewer(entry):
        pg_host = host._allinone_pygame_widget
        if not isinstance(entry, dict) or pg_host is None:
            return
        src = host._fav_source_of(entry)
        opener = {
            "getit": _getit_open_gallery_viewer,
            "zxdb":  _zxdb_open_gallery_viewer,
            "zxart": _zxart_open_gallery_viewer,
            "itchio": getattr(host, "_itchio_open_gallery_viewer", None),
        }.get(src)
        if opener is None:
            return
        prev = pg_host.scene()
        try:
            from zxnu_pygame import PygameItemViewer
            viewer = opener(
                entry,
                make_viewer=lambda **kw: PygameItemViewer(
                    pg_host, anim_mode_getter=lambda: host._gallery_anim_mode, **kw),
                install=False,
            )
        except Exception:
            viewer = None
        if viewer is not None:
            # In Pygame mode a content item may be a plain-text file (.txt,
            # .nfo, …); wire the raw-bytes fetcher so the viewer can render
            # it as a retro log console instead of dropping it. The same
            # fetcher also streams animated .gif screenshots.
            if hasattr(viewer, "set_text_fetch_cb"):
                viewer.set_text_fetch_cb(_gif_fetch_bytes)
            if hasattr(viewer, "set_gif_fetch_cb"):
                viewer.set_gif_fetch_cb(_gif_fetch_bytes)
            viewer.install_into_stack(None, close_fn=lambda: pg_host.set_scene(prev))
    host._allinone_pygame_open_viewer = _allinone_pygame_open_viewer

    def _allinone_pygame_build():
        if host._allinone_pygame_widget is not None:
            return host._allinone_pygame_widget
        import zxnu_pygame as _zpg
        pg_host = _zpg.PygameSurfaceWidget()
        host._allinone_pygame_table = _zpg.TableScene(
            source_label_getter=_allinone_source_label,
            title_getter=_fav_title_getter,
            info_getter=_fav_info_getter,
            open_cb=_allinone_pygame_open_viewer,
        )
        host._allinone_pygame_gallery = _zpg.GalleryScene(
            title_getter=_fav_title_getter,
            source_label_getter=_allinone_source_label,
            thumb_fetch_cb=_fav_thumb_fetch,
            is_favorite_cb=lambda e: host._fav_is(e),
            toggle_favorite_cb=lambda e: host._fav_toggle(e),
            open_cb=_allinone_pygame_open_viewer,
            cols_getter=lambda: host._gallery_cols,
        )
        # Animate .gif thumbnails regardless of the "Gallery animation"
        # setting, mirroring the Qt gallery cells.
        try:
            host._allinone_pygame_gallery.set_gif_fetch_cb(_gif_fetch_bytes)
        except Exception:
            pass
        host._allinone_pygame_widget = pg_host
        try:
            pg_host.enable_background(getattr(host, "_allinone_pygame_anim", True))
        except Exception:
            pass
        host.allinone_view_stack.addWidget(pg_host)   # idx 2
        return pg_host

    def _allinone_pygame_feed():
        if host._allinone_pygame_widget is None:
            return
        entries = getattr(host, "_allinone_all_entries", []) or []
        cur = getattr(host, "_allinone_current_page", 1) or 1
        start = (cur - 1) * ALLINONE_PAGE_SIZE
        page = entries[start:start + ALLINONE_PAGE_SIZE]
        try:
            host._allinone_pygame_table.set_entries(page)
            host._allinone_pygame_gallery.set_entries(page)
        except Exception:
            pass
    host._allinone_pygame_feed = _allinone_pygame_feed

    def _allinone_pygame_set_scene():
        pg_host = host._allinone_pygame_widget
        if pg_host is None:
            return
        mode = getattr(host, "_allinone_view_mode", "gallery")
        scene = (host._allinone_pygame_gallery if mode == "gallery"
                 else host._allinone_pygame_table)
        pg_host.set_scene(scene)
    host._allinone_pygame_set_scene = _allinone_pygame_set_scene

    def _allinone_pygame_disable(reason=""):
        btn = host.allinone_pygame_button
        btn.blockSignals(True)
        btn.setChecked(False)
        btn.setText(ui_tr_now("🎮 Retro"))
        btn.blockSignals(False)
        btn.setEnabled(False)
        if reason:
            btn.setToolTip(reason)

    def _allinone_on_pygame_toggled(checked):
        if checked:
            try:
                from zxnu_pygame import pygame_available
                ok, why = pygame_available()
            except Exception as exc:
                ok, why = False, str(exc)
            if not ok:
                _allinone_pygame_disable(
                    f"{why}\nInstall with: pip install pygame-ce")
                try:
                    host.allinone_status_label.setText(ui_tr_now(
                        "Pygame mode unavailable — run: pip install pygame-ce"
                    ))
                except Exception:
                    pass
                return
            try:
                _allinone_pygame_build()
            except Exception as exc:
                _allinone_pygame_disable(f"Pygame init failed: {exc}")
                return
            host._allinone_pygame_on = True
            host.allinone_pygame_button.setText(ui_tr_now("🖼 Switch to 'Classic' view mode"))
            # The autocomplete dropdown is a top-level Qt.Tool window.  Shown
            # over the continuously-repainting pygame surface on Windows it
            # steals keyboard activation from the search box, so the user can
            # select the text but can no longer type ("the completer gets
            # stuck").  Dismiss any open popup and detach the completer while
            # pygame mode is active so the search box stays a plain, fully
            # typeable input; it is restored when switching back to Classic.
            try:
                host._allinone_completer.popup().hide()
                host.allinone_search_input.setCompleter(None)
            except Exception:
                pass
            _allinone_pygame_feed()
            _allinone_pygame_set_scene()
            host.allinone_view_stack.setCurrentWidget(host._allinone_pygame_widget)
            try:
                host._allinone_pygame_widget.enable_background(
                    getattr(host, "_allinone_pygame_anim", True))
            except Exception:
                pass
            if hasattr(host, "_allinone_right_widget"):
                host._allinone_right_widget.setVisible(False)
            _allinone_pygame_persist(True)
        else:
            host._allinone_pygame_on = False
            host.allinone_pygame_button.setText(ui_tr_now("🎮 Retro"))
            # Back to Classic: restore the autocomplete completer, honouring
            # the global "Enable search autocompletion" setting.
            try:
                host.allinone_search_input.setCompleter(
                    host._allinone_completer
                    if host._search_autocomplete_on() else None)
            except Exception:
                pass
            _allinone_apply_view_mode(
                getattr(host, "_allinone_view_mode", "gallery"), persist=False)
            _allinone_pygame_persist(False)

    def _allinone_pygame_persist(enabled):
        # Skip writing while restoring the saved choice at startup so a
        # transient "pygame unavailable" never clobbers the user's pref.
        if getattr(host, "_allinone_pygame_restoring", False):
            return
        try:
            configuration_dictionary[SETTING_ALLINONE_PYGAME_MODE] = (
                "true" if enabled else "false")
            save_configuration_file()
        except Exception:
            pass

    host.allinone_pygame_button.toggled.connect(_allinone_on_pygame_toggled)

    def _on_allinone_view_combo_changed(_idx):
        _allinone_apply_view_mode(
            host.allinone_view_combo.currentData() or "gallery"
        )

    host.allinone_view_combo.currentIndexChanged.connect(
        _on_allinone_view_combo_changed
    )
    _allinone_apply_view_mode(host._allinone_view_mode, persist=False)
